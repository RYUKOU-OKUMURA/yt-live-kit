"""yt-dlp ラッパー — メタデータ・字幕取得."""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import fcntl

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.models.subtitle import SubtitleSourceMetadata
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_video_path,
    confined_path,
    safe_identifier,
    validate_confined_candidate,
)
from yt_live_kit.services.vtt_parser import parse_vtt

_SUBTITLE_FETCH_ERROR = (
    "字幕が取得できませんでした。公開アーカイブか確認し、yt-dlp を最新にして再実行してください。"
)

_SUBTITLE_SOURCES_DIR = "sources"
_SUBTITLE_LOCK_NAME = ".subtitle-sources.lock"
_INCOMING_PREFIX = ".incoming-"
_PARTIAL_SUFFIXES = (".part", ".tmp", ".ytdl")

_VTT_HEADER_RE = re.compile(r"^\ufeff?WEBVTT(?:[ \t].*)?$")
_VTT_TIMING_RE = re.compile(
    r"^\s*(?P<start>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})(?:\s+.*)?$"
)
_VTT_TIMING_START_RE = re.compile(r"^\s*(?:\d{2}:)?\d{2}:\d{2}")

# 2025.02.19 以前では字幕取得失敗の実績あり（tech-stack.md 参照）
YTDLP_MIN_RECOMMENDED_VERSION = "2025.02.19"

_VIDEO_ID_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"),
    re.compile(r"^([a-zA-Z0-9_-]{11})$"),
)


class YtdlpError(Exception):
    """yt-dlp 実行エラー."""


class SubtitleNotFoundError(YtdlpError):
    """字幕が取得できなかった."""

    def __init__(self, message: str = _SUBTITLE_FETCH_ERROR) -> None:
        super().__init__(message)


def _sanitize_diagnostic(value: str) -> str:
    """外部プロセス由来の診断をユーザー向け表示用に整える."""
    return value.replace("<", "〈").replace(">", "〉").strip()


def _retryable_subtitle_error(message: str) -> SubtitleNotFoundError:
    return SubtitleNotFoundError(
        f"{message}既存の字幕は保持しています。内容を確認して再試行してください。"
    )


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def make_subtitle_source_fingerprint(
    content: bytes,
    *,
    video_id: str,
    language: str,
    source_url: str,
    ytdlp_version: str,
) -> tuple[str, str]:
    """VTT の内容と取得 provenance から source fingerprint を作る.

    ``path`` や ``mtime`` は identity に含めない。戻り値は
    ``(source_fingerprint, content_sha256)`` で、S9-2 の resolver が source
    artifact と内容 digest を別々に参照できるようにする。
    """
    content_sha256 = hashlib.sha256(content).hexdigest()
    payload = {
        "content_sha256": content_sha256,
        "byte_size": len(content),
        "source_kind": "youtube_vtt",
        "video_id": video_id,
        "language": language,
        "source_url": source_url,
        "ytdlp_version": ytdlp_version,
    }
    fingerprint = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return fingerprint, content_sha256


def _parse_vtt_timestamp(value: str) -> int:
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours_text, minutes, seconds = parts
        hours = int(hours_text)
    else:  # pragma: no cover - _VTT_TIMING_RE で到達不能
        raise ValueError("invalid VTT timestamp")
    second_text, millisecond_text = seconds.split(".", 1)
    minutes_value = int(minutes)
    seconds_value = int(second_text)
    milliseconds_value = int(millisecond_text)
    if not 0 <= minutes_value <= 59 or not 0 <= seconds_value <= 59:
        raise ValueError("invalid VTT timestamp range")
    if not 0 <= milliseconds_value <= 999:
        raise ValueError("invalid VTT timestamp milliseconds")
    return (
        hours * 3_600_000
        + minutes_value * 60_000
        + seconds_value * 1_000
        + milliseconds_value
    )


def _validate_vtt_content(content: bytes, *, path: Path | None = None) -> None:
    """保存前に UTF-8 / WebVTT / cue を検証する."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _retryable_subtitle_error(
            "取得した字幕を UTF-8 として読み取れませんでした。"
        ) from exc

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not _VTT_HEADER_RE.fullmatch(lines[0].strip()):
        raise _retryable_subtitle_error(
            "取得した字幕が WebVTT 形式ではありません。"
        )

    timing_count = 0
    metadata_block: str | None = None
    in_cue = False
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped and metadata_block is None:
            in_cue = False
            continue
        if metadata_block is not None:
            if not stripped:
                metadata_block = None
            continue
        if stripped.startswith("NOTE"):
            metadata_block = "NOTE"
            continue
        if stripped in {"STYLE", "REGION"}:
            metadata_block = stripped
            continue
        if in_cue:
            continue
        if "-->" not in line:
            continue
        if not _VTT_TIMING_START_RE.match(line):
            # NOTE / cue text に含まれる矢印は timing line ではない。
            continue
        match = _VTT_TIMING_RE.fullmatch(line)
        if match is None:
            raise _retryable_subtitle_error(
                "取得した字幕の時刻形式を検証できませんでした。"
            )
        try:
            start_ms = _parse_vtt_timestamp(match.group("start"))
            end_ms = _parse_vtt_timestamp(match.group("end"))
        except (TypeError, ValueError) as exc:
            raise _retryable_subtitle_error(
                "取得した字幕の時刻を検証できませんでした。"
            ) from exc
        if end_ms <= start_ms:
            raise _retryable_subtitle_error(
                "取得した字幕の区間が正しくありません。"
            )
        timing_count += 1
        in_cue = True

    if timing_count == 0:
        raise _retryable_subtitle_error(
            "取得した字幕に有効なキューがありません。"
        )

    try:
        cues = parse_vtt(text)
    except (OSError, ValueError, UnicodeError) as exc:
        raise _retryable_subtitle_error(
            "取得した字幕を解析できませんでした。"
        ) from exc
    if not cues:
        raise _retryable_subtitle_error("取得した字幕に有効なテキストがありません。")


def _lstat_without_symlink(path: Path, label: str) -> os.stat_result | None:
    """存在する path が symlink でないことを確認する."""
    try:
        result = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise YtdlpError(f"{label}を安全に確認できませんでした。") from exc
    if stat.S_ISLNK(result.st_mode):
        raise YtdlpError(f"{label}にシンボリックリンクがあるため安全に扱えません。")
    return result


def _validate_confined_path(
    path: Path,
    settings: Settings,
    label: str,
) -> Path:
    try:
        return validate_confined_candidate(settings.data_dir, path, label=label)
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc


def _ensure_directory(path: Path, settings: Settings, label: str) -> None:
    _validate_confined_path(path, settings, label)
    current = _lstat_without_symlink(path, label)
    if current is not None and not stat.S_ISDIR(current.st_mode):
        raise YtdlpError(f"{label}がディレクトリではありません。")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise YtdlpError(f"{label}を作成できませんでした。再試行してください。") from exc
    current = _lstat_without_symlink(path, label)
    if current is None or not stat.S_ISDIR(current.st_mode):
        raise YtdlpError(f"{label}を安全に確認できませんでした。")


def _read_regular_file_bytes(path: Path, label: str) -> bytes:
    """symlink を追わず、読み込み中の path 交換も検知して bytes を読む."""
    before_path = _lstat_without_symlink(path, label)
    if before_path is None:
        raise _retryable_subtitle_error(f"{label}が見つかりません。")
    if not stat.S_ISREG(before_path.st_mode):
        raise _retryable_subtitle_error(f"{label}が通常のファイルではありません。")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise _retryable_subtitle_error(
            f"{label}を安全に読み取れませんでした。"
        ) from exc

    try:
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _retryable_subtitle_error(f"{label}が通常のファイルではありません。")
        with os.fdopen(file_descriptor, "rb") as file:
            file_descriptor = -1
            content = file.read()
            after = os.fstat(file.fileno())
        if after.st_size != len(content) or after.st_size != opened.st_size:
            raise _retryable_subtitle_error(
                f"{label}が取得中に変化しました。"
            )
        after_path = _lstat_without_symlink(path, label)
        if after_path is None or (
            after_path.st_dev != opened.st_dev
            or after_path.st_ino != opened.st_ino
            or after_path.st_size != opened.st_size
        ):
            raise _retryable_subtitle_error(
                f"{label}が取得中に置き換わりました。"
            )
        return content
    except YtdlpError:
        raise
    except OSError as exc:
        raise _retryable_subtitle_error(
            f"{label}を安全に読み取れませんでした。"
        ) from exc
    finally:
        if file_descriptor != -1:
            try:
                os.close(file_descriptor)
            except OSError:
                pass


def _unlink_if_identity_matches(
    path: Path,
    expected: os.stat_result | None,
    *,
    label: str,
) -> None:
    """今回作成した file だけを、競合時に誤って消さずに回収する."""
    if expected is None:
        return
    current = _lstat_without_symlink(path, label)
    if current is None or not stat.S_ISREG(current.st_mode):
        return
    if current.st_dev != expected.st_dev or current.st_ino != expected.st_ino:
        return
    try:
        path.unlink()
    except OSError:
        return

def _atomic_create_bytes(
    path: Path,
    content: bytes,
    settings: Settings | None = None,
    *,
    label: str = "字幕 artifact",
) -> bool:
    """既存 path を置き換えず、同一 directory へ bytes を atomic create する.

    一時ファイルを fsync してから ``link`` で destination を一度だけ作る。
    ``replace`` と違い、競合で別 writer が先に canonical を作っても既存 bytes を
    上書きしない。
    """
    if settings is not None:
        _validate_confined_path(path, settings, label)
    parent = path.parent
    if settings is not None:
        _validate_confined_path(parent, settings, f"{label}の保存先")
    parent_stat = _lstat_without_symlink(parent, f"{label}の保存先")
    if parent_stat is None or not stat.S_ISDIR(parent_stat.st_mode):
        raise YtdlpError(f"{label}の保存先を確認できませんでした。")

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = -1
    temporary_name: str | None = None
    file_descriptor = -1
    try:
        directory_descriptor = os.open(parent, directory_flags)
        opened_parent = os.fstat(directory_descriptor)
        if (
            opened_parent.st_dev != parent_stat.st_dev
            or opened_parent.st_ino != parent_stat.st_ino
        ):
            raise _retryable_subtitle_error(
                f"{label}の保存先が取得中に置き換わりました。"
            )

        try:
            existing = os.stat(
                path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode):
                raise YtdlpError(f"{label}にシンボリックリンクがあるため安全に扱えません。")
            if not stat.S_ISREG(existing.st_mode):
                raise YtdlpError(f"{label}が通常のファイルではありません。")
            return False

        for _ in range(8):
            candidate_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
            try:
                file_descriptor = os.open(
                    candidate_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate_name
            break
        if file_descriptor == -1 or temporary_name is None:
            raise _retryable_subtitle_error(
                f"{label}の一時ファイルを作成できませんでした。"
            )
        with os.fdopen(file_descriptor, "wb") as temporary:
            file_descriptor = -1
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())

        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            try:
                existing = os.stat(
                    path.name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError as exc:
                raise _retryable_subtitle_error(
                    f"{label}の競合を解決できませんでした。"
                ) from exc
            if stat.S_ISLNK(existing.st_mode):
                raise YtdlpError(f"{label}にシンボリックリンクがあるため安全に扱えません。")
            if not stat.S_ISREG(existing.st_mode):
                raise YtdlpError(f"{label}が通常のファイルではありません。")
            return False
        except OSError as exc:
            raise _retryable_subtitle_error(
                f"{label}を原子的に保存できませんでした。"
            ) from exc
        try:
            os.fsync(directory_descriptor)
        except OSError:
            # directory entry の durability は filesystem に依存するが、link
            # 自体は既に atomic に完了している。既存成果物を巻き戻さない。
            pass
        return True
    except YtdlpError:
        raise
    except OSError as exc:
        raise _retryable_subtitle_error(
            f"{label}を原子的に保存できませんでした。"
        ) from exc
    finally:
        if file_descriptor != -1:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if temporary_name is not None and directory_descriptor != -1:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                # 一時ファイルが残っても canonical/source を指すことはない。
                # 次回の incoming cleanup で対象を限定して回収する。
                pass
        if directory_descriptor != -1:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


@contextmanager
def _subtitle_lock(
    path: Path,
    settings: Settings,
    *,
    label: str,
) -> Iterator[None]:
    """symlink を追わず directory fd 経由で字幕用 flock を取得する."""
    _validate_confined_path(path, settings, label)
    parent = path.parent
    parent_stat = _lstat_without_symlink(parent, f"{label}の保存先")
    if parent_stat is None or not stat.S_ISDIR(parent_stat.st_mode):
        raise _retryable_subtitle_error(f"{label}の保存先を確認できませんでした。")

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = -1
    lock_descriptor = -1
    locked = False
    try:
        directory_descriptor = os.open(parent, directory_flags)
        opened_parent = os.fstat(directory_descriptor)
        if (
            opened_parent.st_dev != parent_stat.st_dev
            or opened_parent.st_ino != parent_stat.st_ino
        ):
            raise _retryable_subtitle_error(
                f"{label}の保存先が取得中に置き換わりました。"
            )
        lock_flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            lock_flags |= os.O_NOFOLLOW
        lock_descriptor = os.open(
            path.name,
            lock_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        lock_stat = os.fstat(lock_descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise YtdlpError(f"{label}が通常のファイルではありません。")
        os.fchmod(lock_descriptor, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    except YtdlpError:
        raise
    except OSError as exc:
        raise _retryable_subtitle_error(
            f"{label}を安全に取得できませんでした。"
        ) from exc
    finally:
        if locked:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        if lock_descriptor != -1:
            try:
                os.close(lock_descriptor)
            except OSError:
                pass
        if directory_descriptor != -1:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def _same_immutable_metadata(
    actual: SubtitleSourceMetadata,
    expected: SubtitleSourceMetadata,
) -> bool:
    actual_values = actual.model_dump(mode="json")
    expected_values = expected.model_dump(mode="json")
    actual_values.pop("fetched_at", None)
    expected_values.pop("fetched_at", None)
    return actual_values == expected_values


def _pending_source_paths(
    sources_dir: Path,
    source_fingerprint: str,
) -> list[Path]:
    paths = sorted(
        sources_dir.glob(f".{source_fingerprint}.*.vtt.pending")
    )
    for path in paths:
        _lstat_without_symlink(path, "字幕 pending source")
    return paths


def _promote_pending_source(
    pending_path: Path,
    source_path: Path,
    *,
    content: bytes,
    settings: Settings,
) -> None:
    pending_content = _read_regular_file_bytes(
        pending_path,
        "字幕 pending source",
    )
    if pending_content != content:
        raise _retryable_subtitle_error(
            "字幕 pending source の内容が一致しません。"
        )
    created = _atomic_create_bytes(
        source_path,
        pending_content,
        settings,
        label="字幕 source artifact",
    )
    if not created:
        existing_content = _read_regular_file_bytes(
            source_path,
            "字幕 source artifact",
        )
        if existing_content != content:
            raise _retryable_subtitle_error(
                "同じ source fingerprint の字幕内容が一致しません。"
            )
    pending_identity = _lstat_without_symlink(
        pending_path,
        "字幕 pending source",
    )
    _unlink_if_identity_matches(
        pending_path,
        pending_identity,
        label="字幕 pending source",
    )


def _persist_source_artifact(
    subtitles_dir: Path,
    content: bytes,
    *,
    settings: Settings,
    source_fingerprint: str,
    content_sha256: str,
    video_id: str,
    language: str,
    source_url: str,
    ytdlp_version: str,
    canonical_content_sha256: str,
    canonical_compatible: bool,
) -> Path:
    sources_dir = subtitles_dir / _SUBTITLE_SOURCES_DIR
    _ensure_directory(sources_dir, settings, "字幕 source 保存先")
    _validate_directory_entries(sources_dir, settings, "字幕 source 保存先")

    source_path = sources_dir / f"{source_fingerprint}.vtt"
    metadata_path = sources_dir / f"{source_fingerprint}.json"
    source_relative_path = f"subtitles/{_SUBTITLE_SOURCES_DIR}/{source_fingerprint}.vtt"
    metadata = SubtitleSourceMetadata(
        source_fingerprint=source_fingerprint,
        content_sha256=content_sha256,
        video_id=video_id,
        language=language,  # type: ignore[arg-type]
        source_url=source_url,
        ytdlp_version=ytdlp_version,
        byte_size=len(content),
        source_path=source_relative_path,
        canonical_content_sha256=canonical_content_sha256,
        canonical_compatible=canonical_compatible,
        fetched_at=datetime.now(timezone.utc),
    )

    pending_paths = _pending_source_paths(sources_dir, source_fingerprint)
    if len(pending_paths) > 1:
        raise _retryable_subtitle_error(
            "同じ source fingerprint の pending artifact が複数あります。"
        )

    source_stat = _lstat_without_symlink(source_path, "字幕 source artifact")
    metadata_stat = _lstat_without_symlink(metadata_path, "字幕 source metadata")
    pending_path = pending_paths[0] if pending_paths else None
    staged_by_this_call = False
    staged_identity: os.stat_result | None = None
    metadata_created_by_this_call = False
    metadata_created_identity: os.stat_result | None = None

    if source_stat is not None:
        if not stat.S_ISREG(source_stat.st_mode):
            raise YtdlpError("字幕 source artifact が通常のファイルではありません。")
        existing_content = _read_regular_file_bytes(
            source_path,
            "字幕 source artifact",
        )
        if existing_content != content:
            raise _retryable_subtitle_error(
                "同じ source fingerprint の字幕内容が一致しません。"
            )
    elif pending_path is None:
        if metadata_stat is not None:
            raise _retryable_subtitle_error(
                "字幕 source metadata はありますが source artifact がありません。"
            )
        pending_path = sources_dir / (
            f".{source_fingerprint}.{uuid.uuid4().hex}.vtt.pending"
        )
        _atomic_create_bytes(
            pending_path,
            content,
            settings,
            label="字幕 pending source",
        )
        staged_by_this_call = True
        staged_identity = _lstat_without_symlink(
            pending_path,
            "字幕 pending source",
        )

    try:
        metadata_bytes = metadata.model_dump_json(indent=2).encode("utf-8")
        metadata_created = _atomic_create_bytes(
            metadata_path,
            metadata_bytes,
            settings,
            label="字幕 source metadata",
        )
        if metadata_created:
            metadata_created_by_this_call = True
            metadata_created_identity = _lstat_without_symlink(
                metadata_path,
                "字幕 source metadata",
            )
        else:
            try:
                existing_metadata = SubtitleSourceMetadata.model_validate_json(
                    _read_regular_file_bytes(metadata_path, "字幕 source metadata")
                )
            except (ValueError, UnicodeError) as exc:
                raise _retryable_subtitle_error(
                    "既存の字幕 source metadata を検証できませんでした。"
                ) from exc
            if metadata_path.stem != existing_metadata.source_fingerprint:
                raise _retryable_subtitle_error(
                    "字幕 source metadata の fingerprint が一致しません。"
                )
            if not _same_immutable_metadata(existing_metadata, metadata):
                raise _retryable_subtitle_error(
                    "既存の字幕 source metadata が一致しません。"
                )

        if pending_path is not None:
            _promote_pending_source(
                pending_path,
                source_path,
                content=content,
                settings=settings,
            )
    except BaseException:
        if staged_by_this_call:
            _unlink_if_identity_matches(
                pending_path,
                staged_identity,
                label="字幕 pending source",
            )
        if metadata_created_by_this_call:
            _unlink_if_identity_matches(
                metadata_path,
                metadata_created_identity,
                label="字幕 source metadata",
            )
        raise
    return source_path


def _bootstrap_canonical_subtitle(
    subtitles_dir: Path,
    content: bytes,
    *,
    settings: Settings,
) -> Path:
    target = subtitles_dir / "ja.vtt"
    _validate_confined_path(target, settings, "字幕保存先")
    _atomic_create_bytes(target, content, settings, label="canonical 字幕")
    return target


def _commit_subtitle_artifacts(
    subtitles_dir: Path,
    content: bytes,
    *,
    settings: Settings,
    video_id: str,
    language: str,
    source_url: str,
    ytdlp_version: str,
) -> tuple[Path, str, str, Path]:
    source_fingerprint, content_sha256 = make_subtitle_source_fingerprint(
        content,
        video_id=video_id,
        language=language,
        source_url=source_url,
        ytdlp_version=ytdlp_version,
    )
    lock_path = subtitles_dir / _SUBTITLE_LOCK_NAME
    _validate_confined_path(lock_path, settings, "字幕保存ロック")
    _lstat_without_symlink(lock_path, "字幕保存ロック")

    try:
        with _subtitle_lock(lock_path, settings, label="字幕保存ロック"):
            _ensure_directory(subtitles_dir, settings, "字幕保存先")
            _validate_directory_entries(subtitles_dir, settings, "字幕保存先")
            canonical_path = subtitles_dir / "ja.vtt"
            canonical_stat = _lstat_without_symlink(canonical_path, "canonical 字幕")
            if canonical_stat is None:
                canonical_content_sha256 = hashlib.sha256(content).hexdigest()
                canonical_compatible = True
            else:
                if not stat.S_ISREG(canonical_stat.st_mode):
                    raise YtdlpError("canonical 字幕が通常のファイルではありません。")
                canonical_content = _read_regular_file_bytes(
                    canonical_path,
                    "canonical 字幕",
                )
                canonical_content_sha256 = hashlib.sha256(canonical_content).hexdigest()
                canonical_compatible = canonical_content == content
            source_path = _persist_source_artifact(
                subtitles_dir,
                content,
                settings=settings,
                source_fingerprint=source_fingerprint,
                content_sha256=content_sha256,
                video_id=video_id,
                language=language,
                source_url=source_url,
                ytdlp_version=ytdlp_version,
                canonical_content_sha256=canonical_content_sha256,
                canonical_compatible=canonical_compatible,
            )
            canonical_path = _bootstrap_canonical_subtitle(
                subtitles_dir,
                content,
                settings=settings,
            )
    except YtdlpError:
        raise
    except ValueError as exc:
        raise _retryable_subtitle_error(
            "字幕 source metadata を検証できませんでした。"
        ) from exc
    except OSError as exc:
        raise _retryable_subtitle_error(
            "字幕 artifact の保存中にファイルシステムエラーが発生しました。"
        ) from exc
    return canonical_path, language, source_fingerprint, source_path


@dataclass(frozen=True)
class YtdlpBinaryIdentity:
    """解決済み yt-dlp バイナリの内容を再検査するための軽量 identity.

    ``size`` / ``mtime_ns`` は高速な UI 診断 cache の無効化に使う値であり、
    生成物の内容検証を置き換えるものではない。ファイルが見つからない場合は
    ``MISSING_YTDLP_BINARY_IDENTITY`` を返す。
    """

    resolved_path: str
    device: int
    inode: int
    size: int
    mtime_ns: int

    @property
    def is_missing(self) -> bool:
        """バイナリが解決できなかった identity か."""
        return self == MISSING_YTDLP_BINARY_IDENTITY


MISSING_YTDLP_BINARY_IDENTITY = YtdlpBinaryIdentity(
    resolved_path="",
    device=-1,
    inode=-1,
    size=-1,
    mtime_ns=-1,
)


def get_ytdlp_binary_identity(
    configured_path: str | os.PathLike[str],
) -> YtdlpBinaryIdentity:
    """設定された yt-dlp の解決済みパスと stat identity を返す.

    この helper はバージョン取得やネットワークアクセスを行わず、PATH 解決と
    ``stat`` だけを行う。バイナリが無い、解決後に消えた、または stat に失敗した
    場合は常に同じ欠損 sentinel を返すため、UI の warning cache key に安全に使える。
    """
    try:
        resolved = shutil.which(os.fspath(configured_path))
    except (OSError, TypeError):
        return MISSING_YTDLP_BINARY_IDENTITY
    if resolved is None:
        return MISSING_YTDLP_BINARY_IDENTITY

    try:
        path = Path(resolved).resolve(strict=True)
        stat_result = path.stat()
    except (OSError, RuntimeError):
        return MISSING_YTDLP_BINARY_IDENTITY

    return YtdlpBinaryIdentity(
        resolved_path=str(path),
        device=int(stat_result.st_dev),
        inode=int(stat_result.st_ino),
        size=int(stat_result.st_size),
        mtime_ns=int(stat_result.st_mtime_ns),
    )

def extract_video_id(url_or_id: str) -> str:
    """YouTube URL または動画 ID から video_id を抽出する."""
    text = url_or_id.strip()
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return _safe_identifier(match.group(1), "動画 ID")
    raise YtdlpError("有効な YouTube URL または動画 ID ではありません。")


def _safe_identifier(value: object, label: str) -> str:
    try:
        return safe_identifier(value, label)
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc


def _run_ytdlp(
    args: list[str],
    settings: Settings,
    *,
    timeout: int | None = None,
    pass_fds: tuple[int, ...] = (),
    cwd_fd: int | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [settings.ytdlp_path, *args]
    effective_timeout = settings.ytdlp_timeout if timeout is None else timeout
    if cwd_fd is not None and os.name != "posix":
        raise YtdlpError(
            "字幕を隔離保存する実行環境に対応していません。"
        )
    preexec_fn = None if cwd_fd is None else lambda: os.fchdir(cwd_fd)
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=effective_timeout,
            pass_fds=pass_fds,
            preexec_fn=preexec_fn,
        )
    except subprocess.TimeoutExpired as exc:
        raise YtdlpError(
            f"yt-dlp の実行が {effective_timeout} 秒でタイムアウトしました。"
            "ネットワーク状況を確認してください。"
        ) from exc
    except OSError as exc:
        raise YtdlpError(
            "yt-dlp の実行に失敗しました。インストール状況を確認して再試行してください。"
        ) from exc


def get_ytdlp_version(settings: Settings | None = None) -> str:
    """yt-dlp のバージョン文字列を返す."""
    settings = settings or get_settings()
    result = _run_ytdlp(["--version"], settings)
    if result.returncode != 0:
        detail = _sanitize_diagnostic(result.stderr) or "不明なエラー"
        raise YtdlpError(f"yt-dlp のバージョン取得に失敗しました: {detail}")
    return result.stdout.strip()


def _parse_ytdlp_version(version: str) -> tuple[int, ...]:
    """yt-dlp バージョン文字列を比較可能なタプルに変換する."""
    # 例: "2026.07.04", "2026.7.4", "2025.02.19"
    parts = version.strip().split(".")
    nums: list[int] = []
    for part in parts[:3]:
        try:
            nums.append(int(part))
        except ValueError:
            break
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def is_ytdlp_version_outdated(
    version: str,
    min_version: str = YTDLP_MIN_RECOMMENDED_VERSION,
) -> bool:
    """yt-dlp が推奨最低バージョンより古いか."""
    return _parse_ytdlp_version(version) < _parse_ytdlp_version(min_version)


def check_ytdlp_version_warning(settings: Settings | None = None) -> str | None:
    """古い yt-dlp の場合に警告メッセージを返す。問題なければ None."""
    settings = settings or get_settings()
    if shutil.which(settings.ytdlp_path) is None:
        return (
            f"yt-dlp が見つかりません（パス: {settings.ytdlp_path}）。"
            "インストール後 PATH に通すか、YTLK_YTDLP_PATH を設定してください。"
        )
    try:
        version = get_ytdlp_version(settings)
    except YtdlpError as exc:
        return str(exc)

    if is_ytdlp_version_outdated(version):
        return (
            f"yt-dlp のバージョン ({version}) が古い可能性があります。"
            f" {YTDLP_MIN_RECOMMENDED_VERSION} 以降を推奨します。"
            "字幕取得に失敗する場合は `pip install -U yt-dlp` または"
            " `brew upgrade yt-dlp` で更新してください。"
        )
    return None


def _fetch_metadata(url: str, settings: Settings) -> dict:
    result = _run_ytdlp(["--dump-json", "--skip-download", url], settings)
    if result.returncode != 0:
        stderr = _sanitize_diagnostic(result.stderr)
        raise YtdlpError(f"メタデータの取得に失敗しました: {stderr or '不明なエラー'}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise YtdlpError("メタデータの解析に失敗しました") from exc


def _download_subtitles(url: str, output_dir: Path, settings: Settings) -> None:
    _ensure_directory(output_dir, settings, "字幕一時保存先")
    _validate_directory_entries(output_dir, settings, "字幕保存先")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = -1
    try:
        directory_descriptor = os.open(output_dir, directory_flags)
        opened_directory = os.fstat(directory_descriptor)
        current_directory = _lstat_without_symlink(
            output_dir,
            "字幕一時保存先",
        )
        if current_directory is None or (
            current_directory.st_dev != opened_directory.st_dev
            or current_directory.st_ino != opened_directory.st_ino
        ):
            raise _retryable_subtitle_error(
                "字幕一時保存先が取得中に置き換わりました。"
            )
        output_template = "%(id)s"
        result = _run_ytdlp(
            [
                "--write-auto-sub",
                "--sub-langs",
                "ja-orig,ja",
                "--sub-format",
                "vtt",
                "--skip-download",
                "-o",
                output_template,
                url,
            ],
            settings,
            pass_fds=(directory_descriptor,),
            cwd_fd=directory_descriptor,
        )
    except YtdlpError:
        raise
    except OSError as exc:
        raise _retryable_subtitle_error(
            "字幕の隔離保存に失敗しました。"
        ) from exc
    finally:
        if directory_descriptor != -1:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass
    if result.returncode != 0:
        stderr = _sanitize_diagnostic(result.stderr)
        raise SubtitleNotFoundError(
            _SUBTITLE_FETCH_ERROR if not stderr else f"{_SUBTITLE_FETCH_ERROR}\n（詳細: {stderr}）"
        )
    _validate_directory_entries(output_dir, settings, "字幕保存先")


def _validate_directory_entries(
    directory: Path, settings: Settings, label: str
) -> None:
    try:
        for entry in directory.iterdir():
            validate_confined_candidate(settings.data_dir, entry, label=label)
            _lstat_without_symlink(entry, label)
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise YtdlpError(f"{label}を安全に確認できませんでした。") from exc


def _find_subtitle_file(
    subtitles_dir: Path,
    video_id: str,
    *,
    strict_japanese: bool = False,
) -> tuple[Path, str]:
    """優先順: ja-orig > ja.

    ``strict_japanese`` は yt-dlp の隔離 incoming 専用で、未知言語や partial
    file を canonical 保存へ進めない。既存の読み込み経路は従来どおり fallback
    を許可する。
    """
    video_id = _safe_identifier(video_id, "動画 ID")
    candidates = [
        (subtitles_dir / f"{video_id}.ja-orig.vtt", "ja-orig"),
        (subtitles_dir / f"{video_id}.ja.vtt", "ja"),
        (subtitles_dir / "ja-orig.vtt", "ja-orig"),
        (subtitles_dir / "ja.vtt", "ja"),
    ]

    if strict_japanese:
        try:
            entries = list(subtitles_dir.iterdir())
        except OSError as exc:
            raise _retryable_subtitle_error(
                "取得した字幕の保存先を確認できませんでした。"
            ) from exc
        for entry in entries:
            _lstat_without_symlink(entry, "取得した字幕")
            if entry.name.endswith(_PARTIAL_SUFFIXES):
                raise _retryable_subtitle_error(
                    "取得した字幕が partial file のまま残っています。"
                )
            if entry.is_file() and entry.suffix.lower() == ".vtt":
                expected_names = {path.name for path, _ in candidates}
                if entry.name not in expected_names:
                    raise _retryable_subtitle_error(
                        "日本語字幕ではないファイルが取得されました。"
                    )

    for path, lang in candidates:
        if path.is_file():
            _lstat_without_symlink(path, "取得した字幕")
            return path, lang

    if strict_japanese:
        raise _retryable_subtitle_error(
            "対応する日本語字幕（ja-orig / ja）が見つかりません。"
        )

    vtt_files = sorted(subtitles_dir.glob("*.vtt"))
    if vtt_files:
        path = vtt_files[0]
        _lstat_without_symlink(path, "取得した字幕")
        lang = "ja-orig" if "ja-orig" in path.name else "ja"
        return path, lang

    raise SubtitleNotFoundError()


def _normalize_subtitle_path(subtitles_dir: Path, source: Path, lang: str) -> Path:
    """互換入口。canonical が無い場合だけ安全に初回保存する.

    S9-0 の fetch は source metadata と同じ commit 境界を使うためこの helper
    を直接呼ばない。既存の private helper 利用者に対しても、canonical の既存
    bytes を上書きしない契約を維持する。
    """
    target = subtitles_dir / "ja.vtt"
    _lstat_without_symlink(source, "取得した字幕")
    _lstat_without_symlink(target, "canonical 字幕")
    if source.resolve() == target.resolve():
        return target
    content = _read_regular_file_bytes(source, "取得した字幕")
    _validate_vtt_content(content, path=source)
    _atomic_create_bytes(target, content, label="canonical 字幕")
    return target


def _cleanup_incoming_directory(
    path: Path,
    *,
    expected_identity: os.stat_result | None = None,
) -> bool:
    """今回の fetch が作った一時 directory だけを回収する."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if expected_identity is not None and (
        path_stat.st_dev != expected_identity.st_dev
        or path_stat.st_ino != expected_identity.st_ino
    ):
        # 同名 path が別 directory へ差し替わった場合は、外部の directory を
        # rmtree しない。呼び出し側へ再試行可能な失敗として返す。
        return False
    try:
        if stat.S_ISLNK(path_stat.st_mode):
            path.unlink(missing_ok=True)
        elif stat.S_ISDIR(path_stat.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _assert_directory_identity(
    path: Path,
    expected_identity: os.stat_result,
    *,
    label: str,
) -> None:
    current = _lstat_without_symlink(path, label)
    if current is None or not stat.S_ISDIR(current.st_mode):
        raise _retryable_subtitle_error(f"{label}が見つかりません。")
    if (
        current.st_dev != expected_identity.st_dev
        or current.st_ino != expected_identity.st_ino
    ):
        raise _retryable_subtitle_error(
            f"{label}が取得中に置き換わりました。"
        )


def _create_incoming_directory(subtitles_dir: Path, settings: Settings) -> Path:
    """字幕 directory の file descriptor 経由で隔離 incoming を作る."""
    _validate_confined_path(subtitles_dir, settings, "字幕一時保存先")
    parent_stat = _lstat_without_symlink(subtitles_dir, "字幕一時保存先")
    if parent_stat is None or not stat.S_ISDIR(parent_stat.st_mode):
        raise _retryable_subtitle_error("字幕一時保存先を確認できませんでした。")

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = -1
    try:
        directory_descriptor = os.open(subtitles_dir, directory_flags)
        opened_parent = os.fstat(directory_descriptor)
        if (
            opened_parent.st_dev != parent_stat.st_dev
            or opened_parent.st_ino != parent_stat.st_ino
        ):
            raise _retryable_subtitle_error(
                "字幕一時保存先が取得中に置き換わりました。"
            )
        for _ in range(8):
            name = f"{_INCOMING_PREFIX}{uuid.uuid4().hex}"
            try:
                os.mkdir(name, 0o700, dir_fd=directory_descriptor)
            except FileExistsError:
                continue
            incoming = subtitles_dir / name
            _lstat_without_symlink(incoming, "字幕一時保存先")
            return incoming
        raise _retryable_subtitle_error(
            "字幕の一時保存先を作成できませんでした。"
        )
    except YtdlpError:
        raise
    except OSError as exc:
        raise _retryable_subtitle_error(
            "字幕の一時保存先を作成できませんでした。"
        ) from exc
    finally:
        if directory_descriptor != -1:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def _cleanup_stale_incoming_directories(
    subtitles_dir: Path,
    *,
    settings: Settings,
) -> None:
    """前回の hard crash で残った incoming だけを lock 下で回収する."""
    try:
        entries = list(subtitles_dir.iterdir())
    except OSError as exc:
        raise _retryable_subtitle_error(
            "字幕保存先を確認できませんでした。"
        ) from exc
    for entry in entries:
        if not entry.name.startswith(_INCOMING_PREFIX):
            continue
        identity = _lstat_without_symlink(entry, "字幕一時保存先")
        if identity is None:
            continue
        _validate_confined_path(entry, settings, "字幕一時保存先")
        if not _cleanup_incoming_directory(entry, expected_identity=identity):
            raise _retryable_subtitle_error(
                "前回の字幕一時ファイルを安全に削除できませんでした。"
            )


def _retrieve_and_validate_subtitle(
    url: str,
    video_id: str,
    subtitles_dir: Path,
    settings: Settings,
) -> tuple[bytes, str]:
    """incoming の作成から検証・回収までを video 単位で直列化する."""
    lock_path = subtitles_dir / ".subtitle-incoming.lock"
    _validate_confined_path(lock_path, settings, "字幕取得ロック")
    _lstat_without_symlink(lock_path, "字幕取得ロック")

    with _subtitle_lock(lock_path, settings, label="字幕取得ロック"):
        _cleanup_stale_incoming_directories(subtitles_dir, settings=settings)
        incoming_dir = _create_incoming_directory(subtitles_dir, settings)
        incoming_identity = _lstat_without_symlink(
            incoming_dir,
            "字幕一時保存先",
        )
        if incoming_identity is None:
            raise _retryable_subtitle_error(
                "字幕一時保存先を確認できませんでした。"
            )
        try:
            _ensure_directory(incoming_dir, settings, "字幕一時保存先")
            try:
                _download_subtitles(url, incoming_dir, settings)
            except YtdlpError:
                raise
            except Exception as exc:
                raise _retryable_subtitle_error(
                    "字幕取得処理が予期せず中断されました。"
                ) from exc

            _assert_directory_identity(
                incoming_dir,
                incoming_identity,
                label="字幕一時保存先",
            )
            _validate_directory_entries(incoming_dir, settings, "字幕一時保存先")
            subtitle_path, subtitle_lang = _find_subtitle_file(
                incoming_dir,
                video_id,
                strict_japanese=True,
            )
            try:
                subtitle_path.resolve().relative_to(incoming_dir.resolve())
            except (OSError, RuntimeError, ValueError) as exc:
                raise YtdlpError(
                    "取得した字幕が隔離された一時保存先の外にあるため安全に扱えません。"
                ) from exc
            subtitle_content = _read_regular_file_bytes(subtitle_path, "取得した字幕")
            _validate_vtt_content(subtitle_content, path=subtitle_path)
            return subtitle_content, subtitle_lang
        finally:
            cleanup_succeeded = _cleanup_incoming_directory(
                incoming_dir,
                expected_identity=incoming_identity,
            )
            if not cleanup_succeeded and sys.exc_info()[0] is None:
                raise _retryable_subtitle_error(
                    "字幕の一時ファイルを削除できませんでした。"
                )


def fetch(url: str, settings: Settings | None = None) -> VideoMeta:
    """URL からメタデータと字幕 VTT を取得し data/{video_id}/ に保存する."""
    settings = settings or get_settings()
    requested_video_id = extract_video_id(url)

    if shutil.which(settings.ytdlp_path) is None:
        raise YtdlpError(
            f"yt-dlp が見つかりません（パス: {settings.ytdlp_path}）。"
            "インストール後 PATH に通すか、YTLK_YTDLP_PATH を設定してください。"
        )

    ytdlp_version = get_ytdlp_version(settings)
    info = _fetch_metadata(url, settings)

    video_id = _safe_identifier(info.get("id") or requested_video_id, "動画 ID")
    try:
        video_dir = confined_video_path(
            settings.data_dir, video_id, label="動画保存先"
        )
        subtitles_dir = confined_path(
            settings.data_dir, video_id, "subtitles", label="字幕保存先"
        )
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc
    settings.ensure_data_dir()

    _ensure_directory(video_dir, settings, "動画保存先")
    _ensure_directory(subtitles_dir, settings, "字幕保存先")
    _validate_directory_entries(subtitles_dir, settings, "字幕保存先")
    meta_path = video_dir / "meta.json"
    _validate_confined_path(meta_path, settings, "メタデータ保存先")
    _lstat_without_symlink(meta_path, "メタデータ保存先")

    subtitle_content, subtitle_lang = _retrieve_and_validate_subtitle(
        url,
        video_id,
        subtitles_dir,
        settings,
    )

    source_url = str(info.get("webpage_url") or url)
    _canonical_path, _, _, _ = _commit_subtitle_artifacts(
        subtitles_dir,
        subtitle_content,
        settings=settings,
        video_id=video_id,
        language=subtitle_lang,
        source_url=source_url,
        ytdlp_version=ytdlp_version,
    )

    meta = VideoMeta(
        id=video_id,
        title=info.get("title") or video_id,
        url=url,
        upload_date=info.get("upload_date"),
        duration=info.get("duration"),
        ytdlp_version=ytdlp_version,
        fetched_at=datetime.now(timezone.utc),
        subtitle_lang=subtitle_lang,
    )

    try:
        write_text_atomically(meta_path, meta.model_dump_json(indent=2))
    except OSError as exc:
        raise YtdlpError(
            "動画メタデータの保存に失敗しました。字幕は保持しています。再試行してください。"
        ) from exc

    return meta


def download_video(url: str, output_dir: Path, settings: Settings | None = None) -> Path:
    """切り出し用に動画を 1 本ダウンロードする."""
    settings = settings or get_settings()
    extract_video_id(url)
    try:
        output_dir = validate_confined_candidate(
            settings.data_dir, output_dir, label="動画保存先"
        )
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc

    if shutil.which(settings.ytdlp_path) is None:
        raise YtdlpError(
            f"yt-dlp が見つかりません（パス: {settings.ytdlp_path}）。"
            "インストール後 PATH に通すか、YTLK_YTDLP_PATH を設定してください。"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _validate_directory_entries(output_dir, settings, "動画保存先")
    output_template = str(output_dir / "%(id)s.%(ext)s")

    result = _run_ytdlp(
        [
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            url,
        ],
        settings,
        timeout=settings.download_timeout,
    )

    if result.returncode != 0:
        stderr = _sanitize_diagnostic(result.stderr)
        raise YtdlpError(
            "動画のダウンロードに失敗しました。"
            + (f"\n（詳細: {stderr}）" if stderr else "")
        )

    _validate_directory_entries(output_dir, settings, "動画保存先")
    mp4_files = sorted(output_dir.glob("*.mp4"))
    if mp4_files:
        try:
            return validate_confined_candidate(
                settings.data_dir, mp4_files[0], label="動画保存先"
            )
        except PathConfinementError as exc:
            raise YtdlpError(str(exc)) from exc

    video_files = sorted(
        f for f in output_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".mp4", ".mkv", ".webm"}
    )
    if not video_files:
        raise YtdlpError(
            "ダウンロードした動画ファイルが見つかりません。"
        )
    try:
        return validate_confined_candidate(
            settings.data_dir, video_files[0], label="動画保存先"
        )
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc
