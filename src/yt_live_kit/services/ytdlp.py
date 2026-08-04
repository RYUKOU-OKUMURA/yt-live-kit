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
import tempfile
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Iterator, Mapping, Sequence
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

# ``subprocess.Popen(preexec_fn=...)`` is unsafe when the caller has worker
# threads: the child can inherit a lock held by a vanished thread.  Keep the
# directory-FD based cwd protection by doing the fchdir in this tiny, freshly
# started Python process, immediately before it execs yt-dlp.  All values that
# affect the command are positional argv entries; no shell is involved.
_YTDLP_CWD_FD_WRAPPER = """
import os
import sys

directory_fd = int(sys.argv[1])
program = sys.argv[2]
os.fchdir(directory_fd)
os.execvp(program, [program, *sys.argv[3:]])
"""

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


class AudioSpanError(YtdlpError):
    """選択区間の音声 span を安全に準備できないエラー."""


_AUDIO_CACHE_SCHEMA = "s9-3-audio-cache-v1"
_AUDIO_CACHE_DIR = "audio_cache"
_AUDIO_FORMAT_SETTINGS: dict[str, Any] = {
    "container": "wav",
    "sample_rate": 16_000,
    "channel": 1,
    "codec": "pcm_s16le",
    "selector": "bestaudio",
    "seek": "yt-dlp-download-sections",
    "cue_inclusion_rule": "half_open_overlap",
}
_AUDIO_FFMPEG_CHECK_TIMEOUT = 30


@dataclass(frozen=True)
class AudioSpanRange:
    """音声取得に使う元動画基準の整数 millisecond 区間."""

    start_ms: int
    end_ms: int
    padding_before_ms: int = 0
    padding_after_ms: int = 0
    inclusion_rule: str = "half_open_overlap"

    def __post_init__(self) -> None:
        integer_fields = (
            self.start_ms,
            self.end_ms,
            self.padding_before_ms,
            self.padding_after_ms,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
            raise AudioSpanError("音声区間の時刻は整数ミリ秒で指定してください。")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise AudioSpanError("音声区間の開始・終了時刻が正しくありません。")
        if self.padding_before_ms < 0 or self.padding_after_ms < 0:
            raise AudioSpanError("音声区間の padding は 0 以上で指定してください。")
        if (
            not isinstance(self.inclusion_rule, str)
            or not self.inclusion_rule.strip()
            or "<" in self.inclusion_rule
            or ">" in self.inclusion_rule
        ):
            raise AudioSpanError("音声区間の cue inclusion rule が正しくありません。")
        if self.inclusion_rule != "half_open_overlap":
            raise AudioSpanError("未対応の cue inclusion rule です。")

    @property
    def requested_start_ms(self) -> int:
        return max(0, self.start_ms - self.padding_before_ms)

    @property
    def requested_end_ms(self) -> int:
        return self.end_ms + self.padding_after_ms

    @property
    def requested_duration_ms(self) -> int:
        return self.requested_end_ms - self.requested_start_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "padding_before_ms": self.padding_before_ms,
            "padding_after_ms": self.padding_after_ms,
            "requested_start_ms": self.requested_start_ms,
            "requested_end_ms": self.requested_end_ms,
            "inclusion_rule": self.inclusion_rule,
        }


@dataclass(frozen=True)
class AudioSpanResult:
    """音声 cache と実体 fingerprint を含む選択区間の結果."""

    video_id: str
    range: AudioSpanRange
    path: Path
    audio_bytes: bytes
    audio_input_fingerprint: str
    source_metadata: dict[str, Any]
    sample_rate: int
    channel: int
    codec: str
    ffmpeg_settings: dict[str, Any]
    cache_hit: bool
    request_fingerprint: str

    @property
    def source_ref(self) -> str:
        """artifact の source_ref に使う相対パスを返す."""

        return str(self.path)


def _strict_audio_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AudioSpanError(f"{label}は整数ミリ秒で指定してください。")
    return value


def normalize_audio_span_range(
    value: AudioSpanRange | Any,
    *,
    padding_ms: int = 0,
    inclusion_rule: str = "half_open_overlap",
) -> AudioSpanRange:
    """TranscriptRange / mapping / ``(start_ms, end_ms)`` を厳密に正規化する."""

    if isinstance(value, AudioSpanRange):
        return value
    if hasattr(value, "start_ms") and hasattr(value, "end_ms"):
        start_ms = getattr(value, "start_ms")
        end_ms = getattr(value, "end_ms")
        range_padding = getattr(value, "padding_ms", 0)
        padding_before = getattr(value, "padding_before_ms", 0)
        padding_after = getattr(value, "padding_after_ms", 0)
        rule = getattr(value, "inclusion_rule", inclusion_rule)
        if padding_before == 0 and padding_after == 0 and range_padding:
            padding_before = range_padding
            padding_after = range_padding
        elif padding_ms and padding_before == 0 and padding_after == 0 and not range_padding:
            padding_before = padding_ms
            padding_after = padding_ms
        return AudioSpanRange(
            _strict_audio_integer(start_ms, "開始時刻"),
            _strict_audio_integer(end_ms, "終了時刻"),
            _strict_audio_integer(padding_before, "padding 前"),
            _strict_audio_integer(padding_after, "padding 後"),
            str(rule),
        )
    if isinstance(value, Mapping):
        if "start_ms" not in value or "end_ms" not in value:
            raise AudioSpanError("音声区間には start_ms と end_ms が必要です。")
        range_padding = value.get("padding_ms", 0)
        padding_before = value.get("padding_before_ms", range_padding)
        padding_after = value.get("padding_after_ms", range_padding)
        if padding_ms and "padding_ms" not in value and "padding_before_ms" not in value and "padding_after_ms" not in value:
            padding_before = padding_ms
            padding_after = padding_ms
        return AudioSpanRange(
            _strict_audio_integer(value["start_ms"], "開始時刻"),
            _strict_audio_integer(value["end_ms"], "終了時刻"),
            _strict_audio_integer(padding_before, "padding 前"),
            _strict_audio_integer(padding_after, "padding 後"),
            str(value.get("inclusion_rule", inclusion_rule)),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) != 2:
            raise AudioSpanError("音声区間は start_ms / end_ms の2要素で指定してください。")
        return AudioSpanRange(
            _strict_audio_integer(value[0], "開始時刻"),
            _strict_audio_integer(value[1], "終了時刻"),
            _strict_audio_integer(padding_ms, "padding 前"),
            _strict_audio_integer(padding_ms, "padding 後"),
            inclusion_rule,
        )
    raise AudioSpanError("音声区間の形式が正しくありません。")


def _audio_canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AudioSpanError("音声 cache の fingerprint を計算できません。") from exc


def make_audio_span_manifest(
    video_id: str,
    ranges: Sequence[AudioSpanRange | Any],
    *,
    padding_ms: int = 0,
    inclusion_rule: str = "half_open_overlap",
) -> tuple[AudioSpanRange, ...]:
    """選択区間を入力順の immutable span manifest として正規化する."""

    safe_video = _safe_identifier(video_id, "動画 ID")
    if not ranges:
        raise AudioSpanError("音声区間は1件以上必要です。")
    normalized = tuple(
        normalize_audio_span_range(
            item,
            padding_ms=padding_ms,
            inclusion_rule=inclusion_rule,
        )
        for item in ranges
    )
    # 呼び出し側が入力順を意図しているため、重複除去・sort は行わない。
    _ = safe_video
    return normalized


def _audio_request_fingerprint(
    video_id: str,
    item: AudioSpanRange,
    *,
    source_metadata: Mapping[str, Any],
) -> str:
    payload = {
        "schema": _AUDIO_CACHE_SCHEMA,
        "video_id": video_id,
        "range": item.to_dict(),
        "source_metadata": dict(source_metadata),
        "format": _AUDIO_FORMAT_SETTINGS,
    }
    return hashlib.sha256(_audio_canonical_json(payload)).hexdigest()


def _format_audio_seek_ms(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _audio_cache_paths(
    video_id: str,
    request_fingerprint: str,
    settings: Settings,
) -> tuple[Path, Path, Path]:
    try:
        cache_dir = confined_video_path(
            settings.data_dir,
            video_id,
            "transcripts",
            _AUDIO_CACHE_DIR,
            label="音声 cache 保存先",
        )
        audio_path = cache_dir / f"{request_fingerprint}.wav"
        metadata_path = cache_dir / f"{request_fingerprint}.json"
        _validate_confined_path(cache_dir, settings, "音声 cache 保存先")
        _validate_confined_path(audio_path, settings, "音声 cache artifact")
        _validate_confined_path(metadata_path, settings, "音声 cache metadata")
        return cache_dir, audio_path, metadata_path
    except PathConfinementError as exc:
        raise AudioSpanError(str(exc)) from exc


def _ensure_audio_cache_dir(path: Path, settings: Settings) -> None:
    _ensure_directory(path, settings, "音声 cache 保存先")
    _validate_directory_entries(path, settings, "音声 cache 保存先")


def _audio_file_fingerprint(path: Path) -> tuple[bytes, str]:
    _lstat_without_symlink(path, "音声 cache artifact")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise AudioSpanError("音声 cache artifact を読み取れません。") from exc
    if not content:
        raise AudioSpanError("音声 cache artifact が空です。")
    return content, hashlib.sha256(content).hexdigest()


_EXPECTED_WAV_FORMAT = (16_000, 1, "pcm_s16le")


def _inspect_wav_format(content: bytes) -> tuple[int, int, str] | None:
    """RIFF/WAVE と PCM format を確認する。壊れた bytes は None にする."""

    if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        return None
    try:
        import io

        with wave.open(io.BytesIO(content), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            compression = wav.getcomptype()
    except (EOFError, OSError, wave.Error):
        return None
    if (
        channels <= 0
        or sample_rate <= 0
        or sample_width <= 0
        or compression != "NONE"
    ):
        return None
    return sample_rate, channels, f"pcm_s{sample_width * 8}le"


def _quarantine_audio_cache_file(path: Path, settings: Settings) -> None:
    """壊れた cache を同一ディレクトリへ隔離し、再生成を可能にする."""

    try:
        _validate_confined_path(path, settings, "破損した音声 cache")
        existing = _lstat_without_symlink(path, "破損した音声 cache")
        if existing is None or not stat.S_ISREG(existing.st_mode):
            return
        quarantine = path.with_name(f".{path.name}.corrupt-{uuid.uuid4().hex}")
        _validate_confined_path(quarantine, settings, "破損した音声 cache 隔離先")
        os.replace(path, quarantine)
    except (YtdlpError, OSError):
        # 隔離できない場合も、壊れた cache を成功扱いにはしない。次の atomic
        # write が可能なら同じ target を置き換え、symlink 等は fail closed にする。
        return


def _quarantine_audio_cache(
    audio_path: Path,
    metadata_path: Path,
    settings: Settings,
) -> None:
    _quarantine_audio_cache_file(audio_path, settings)
    _quarantine_audio_cache_file(metadata_path, settings)


def _atomic_write_audio_cache(
    path: Path,
    content: bytes,
    settings: Settings,
) -> None:
    _validate_confined_path(path, settings, "音声 cache artifact")
    parent = path.parent
    _ensure_audio_cache_dir(parent, settings)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        raise AudioSpanError("音声 cache artifact を原子的に保存できません。") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _audio_cache_metadata(
    *,
    video_id: str,
    item: AudioSpanRange,
    request_fingerprint: str,
    content: bytes,
    source_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": _AUDIO_CACHE_SCHEMA,
        "video_id": video_id,
        "range": item.to_dict(),
        "request_fingerprint": request_fingerprint,
        "audio": {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "sample_rate": _AUDIO_FORMAT_SETTINGS["sample_rate"],
            "channel": _AUDIO_FORMAT_SETTINGS["channel"],
            "codec": _AUDIO_FORMAT_SETTINGS["codec"],
        },
        "source_metadata": dict(source_metadata),
        "ffmpeg": dict(_AUDIO_FORMAT_SETTINGS),
    }


def _read_audio_cache(
    *,
    video_id: str,
    item: AudioSpanRange,
    request_fingerprint: str,
    audio_path: Path,
    metadata_path: Path,
    source_metadata: Mapping[str, Any],
    settings: Settings,
) -> AudioSpanResult | None:
    audio_stat = _lstat_without_symlink(audio_path, "音声 cache artifact")
    metadata_stat = _lstat_without_symlink(metadata_path, "音声 cache metadata")
    if audio_stat is None or metadata_stat is None:
        return None
    try:
        if not stat.S_ISREG(audio_stat.st_mode) or not stat.S_ISREG(metadata_stat.st_mode):
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_keys = {
            "schema",
            "video_id",
            "range",
            "request_fingerprint",
            "audio",
            "source_metadata",
            "ffmpeg",
        }
        if not isinstance(metadata, dict) or set(metadata) != expected_keys:
            return None
        if (
            metadata["schema"] != _AUDIO_CACHE_SCHEMA
            or metadata["video_id"] != video_id
            or metadata["range"] != item.to_dict()
            or metadata["request_fingerprint"] != request_fingerprint
            or metadata["source_metadata"] != dict(source_metadata)
            or metadata["ffmpeg"] != dict(_AUDIO_FORMAT_SETTINGS)
        ):
            return None
        audio_metadata = metadata["audio"]
        if not isinstance(audio_metadata, dict) or set(audio_metadata) != {
            "bytes",
            "sha256",
            "sample_rate",
            "channel",
            "codec",
        }:
            return None
        content, digest = _audio_file_fingerprint(audio_path)
        if (
            audio_metadata["bytes"] != len(content)
            or audio_metadata["sha256"] != digest
            or audio_metadata["sample_rate"] != _AUDIO_FORMAT_SETTINGS["sample_rate"]
            or audio_metadata["channel"] != _AUDIO_FORMAT_SETTINGS["channel"]
            or audio_metadata["codec"] != _AUDIO_FORMAT_SETTINGS["codec"]
        ):
            return None
        inspected = _inspect_wav_format(content)
        if inspected != _EXPECTED_WAV_FORMAT:
            return None
        return AudioSpanResult(
            video_id=video_id,
            range=item,
            path=audio_path,
            audio_bytes=content,
            audio_input_fingerprint=digest,
            source_metadata=dict(source_metadata),
            sample_rate=int(_AUDIO_FORMAT_SETTINGS["sample_rate"]),
            channel=int(_AUDIO_FORMAT_SETTINGS["channel"]),
            codec=str(_AUDIO_FORMAT_SETTINGS["codec"]),
            ffmpeg_settings=dict(_AUDIO_FORMAT_SETTINGS),
            cache_hit=True,
            request_fingerprint=request_fingerprint,
        )
    except (AudioSpanError, OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        # cache 破損は cache miss として安全に再取得する。壊れた bytes を
        # Whisper へ渡したり、既存 artifact を高精度として返したりしない。
        return None


def _resolve_audio_ffmpeg(ffmpeg_path: str) -> Path:
    """設定値から音声取得に使う FFmpeg 実体を解決する.

    S9 の音声 span は yt-dlp の ``--download-sections`` と後処理で FFmpeg
    を使う。PATH 上の別実体へ暗黙に切り替わると、字幕精査の入力が作れず
    fail closed にならないため、設定値をここで絶対パスへ固定する。
    """

    configured = ffmpeg_path.strip()
    if not configured:
        raise AudioSpanError(
            "FFmpeg のパスが空です。設定ページで YTLK_FFMPEG_PATH に"
            "実行可能な FFmpeg のパスを設定してください。"
        )

    try:
        configured_path = Path(configured).expanduser()
        has_path_component = configured_path.is_absolute() or os.sep in configured
        resolved_command = None if has_path_component else shutil.which(configured)
        if resolved_command is None and not has_path_component:
            raise AudioSpanError(
                f"FFmpeg が見つかりません（設定: {configured}）。"
                "設定ページで YTLK_FFMPEG_PATH を確認してください。"
            )
        candidate = configured_path if has_path_component else Path(resolved_command)
        resolved = candidate.resolve(strict=True)
        stat_result = resolved.stat()
    except AudioSpanError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise AudioSpanError(
            f"FFmpeg を確認できません（設定: {configured}）。"
            "設定ページで YTLK_FFMPEG_PATH に実行可能な FFmpeg のパスを設定してください。"
        ) from exc

    if not stat.S_ISREG(stat_result.st_mode):
        raise AudioSpanError(
            f"FFmpeg が通常のファイルではありません（設定: {configured}）。"
            "設定ページで実行ファイルのパスを指定してください。"
        )
    if not os.access(resolved, os.X_OK):
        raise AudioSpanError(
            f"FFmpeg に実行権限がありません（設定: {configured}）。"
            "設定ページで実行権限と YTLK_FFMPEG_PATH を確認してください。"
        )
    return resolved


def _check_audio_ffmpeg(ffmpeg_path: Path, settings: Settings) -> None:
    """FFmpeg 実体が起動できることをネットワーク前に確認する."""

    try:
        result = subprocess.run(
            [str(ffmpeg_path), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=min(settings.ffmpeg_timeout, _AUDIO_FFMPEG_CHECK_TIMEOUT),
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioSpanError(
            "FFmpeg の起動確認がタイムアウトしました。"
            "設定ページで YTLK_FFMPEG_PATH の実体を確認してください。"
        ) from exc
    except OSError as exc:
        raise AudioSpanError(
            "FFmpeg を実行できません。設定ページで YTLK_FFMPEG_PATH の"
            "実行権限と実体を確認してください。"
        ) from exc
    if result.returncode != 0:
        detail = _sanitize_diagnostic(result.stderr or result.stdout)
        raise AudioSpanError(
            f"FFmpeg の起動確認に失敗しました（終了コード: {result.returncode}）。"
            "設定ページで YTLK_FFMPEG_PATH に実行可能な FFmpeg のパスを設定してください。"
            + (f"（詳細: {detail}）" if detail else "")
        )


def _audio_download_argv(
    video_id: str,
    item: AudioSpanRange,
    *,
    ffmpeg_path: Path,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    section = (
        f"*{_format_audio_seek_ms(item.requested_start_ms)}-"
        f"{_format_audio_seek_ms(item.requested_end_ms)}"
    )
    return [
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--no-part",
        "--no-mtime",
        "--no-write-info-json",
        "--no-write-thumbnail",
        "--restrict-filenames",
        "--force-overwrites",
        "-f",
        str(_AUDIO_FORMAT_SETTINGS["selector"]),
        "--download-sections",
        section,
        "--extract-audio",
        "--ffmpeg-location",
        str(ffmpeg_path),
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "--postprocessor-args",
        "ExtractAudio+ffmpeg:-ar 16000 -ac 1 -c:a pcm_s16le",
        "-o",
        "span.%(ext)s",
        url,
    ]


def _find_audio_output(path: Path, settings: Settings) -> Path:
    _validate_confined_path(path, settings, "音声一時保存先")
    candidates: list[Path] = []
    try:
        for child in path.iterdir():
            _lstat_without_symlink(child, "音声一時保存先")
            if child.is_file() and child.suffix.lower() == ".wav":
                candidates.append(child)
    except OSError as exc:
        raise AudioSpanError("音声一時保存先を確認できません。") from exc
    if len(candidates) != 1:
        raise AudioSpanError("選択区間の音声ファイルを一意に確認できません。")
    return candidates[0]


def prepare_audio_span(
    video_id: str,
    selected_range: AudioSpanRange | Any,
    settings: Settings | None = None,
    *,
    source_metadata: Mapping[str, Any] | None = None,
    padding_ms: int = 0,
    inclusion_rule: str = "half_open_overlap",
) -> AudioSpanResult:
    """動画全体を取得せず、選択した絶対時刻 span だけを音声 cache 化する.

    yt-dlp の argv はこの関数内で固定構築され、URL・format・output template を
    呼び出し側から受け取らない。成功後は WAV bytes と変換条件を atomic に保存し、
    span / metadata の破損は cache miss として再取得する。
    """

    settings = settings or get_settings()
    video_id = _safe_identifier(video_id, "動画 ID")
    item = normalize_audio_span_range(
        selected_range,
        padding_ms=padding_ms,
        inclusion_rule=inclusion_rule,
    )
    merged_source_metadata: dict[str, Any] = {
        "video_id": video_id,
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "extractor": "yt-dlp",
        "format_selector": _AUDIO_FORMAT_SETTINGS["selector"],
        "seek": {
            "method": _AUDIO_FORMAT_SETTINGS["seek"],
            "start_ms": item.requested_start_ms,
            "end_ms": item.requested_end_ms,
        },
        "padding": {
            "before_ms": item.padding_before_ms,
            "after_ms": item.padding_after_ms,
        },
        "cue_inclusion_rule": item.inclusion_rule,
        "range_manifest": [item.to_dict()],
    }
    if source_metadata:
        # 呼び出し側の metadata は title / extractor 実績などを追加できるが、
        # 実際に取得した ID・range・変換契約は固定値を優先する。
        merged_source_metadata.update(dict(source_metadata))
        merged_source_metadata.update(
            {
                "video_id": video_id,
                "source_url": f"https://www.youtube.com/watch?v={video_id}",
                "format_selector": _AUDIO_FORMAT_SETTINGS["selector"],
                "seek": {
                    "method": _AUDIO_FORMAT_SETTINGS["seek"],
                    "start_ms": item.requested_start_ms,
                    "end_ms": item.requested_end_ms,
                },
                "padding": {
                    "before_ms": item.padding_before_ms,
                    "after_ms": item.padding_after_ms,
                },
                "cue_inclusion_rule": item.inclusion_rule,
                "range_manifest": [item.to_dict()],
            }
        )
    request_fingerprint = _audio_request_fingerprint(
        video_id,
        item,
        source_metadata=merged_source_metadata,
    )
    cache_dir, audio_path, metadata_path = _audio_cache_paths(
        video_id,
        request_fingerprint,
        settings,
    )
    _ensure_audio_cache_dir(cache_dir, settings)
    cached = _read_audio_cache(
        video_id=video_id,
        item=item,
        request_fingerprint=request_fingerprint,
        audio_path=audio_path,
        metadata_path=metadata_path,
        source_metadata=merged_source_metadata,
        settings=settings,
    )
    if cached is not None:
        return cached
    _quarantine_audio_cache(audio_path, metadata_path, settings)

    resolved_ffmpeg = _resolve_audio_ffmpeg(settings.ffmpeg_path)
    _check_audio_ffmpeg(resolved_ffmpeg, settings)

    if shutil.which(settings.ytdlp_path) is None:
        raise AudioSpanError(
            f"yt-dlp が見つかりません（パス: {settings.ytdlp_path}）。"
            "音声だけを取得するため、yt-dlp を導入して再試行してください。"
        )

    temporary_dir: Path | None = None
    temporary_identity: os.stat_result | None = None
    directory_descriptor = -1
    try:
        temporary_dir = Path(
            tempfile.mkdtemp(prefix=".s9-audio-span-", dir=cache_dir)
        )
        _validate_confined_path(temporary_dir, settings, "音声一時保存先")
        temporary_identity = _lstat_without_symlink(temporary_dir, "音声一時保存先")
        if temporary_identity is None:
            raise AudioSpanError("音声一時保存先を確認できません。")
        directory_descriptor = os.open(
            temporary_dir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        result = _run_ytdlp(
            _audio_download_argv(
                video_id,
                item,
                ffmpeg_path=resolved_ffmpeg,
            ),
            settings,
            timeout=settings.ytdlp_timeout,
            pass_fds=(directory_descriptor,),
            cwd_fd=directory_descriptor,
        )
        _assert_directory_identity(
            temporary_dir,
            temporary_identity,
            label="音声一時保存先",
        )
        if result.returncode != 0:
            detail = _sanitize_diagnostic(result.stderr or result.stdout)
            raise AudioSpanError(
                "選択区間の audio-only format（bestaudio）を取得できませんでした。"
                "動画全体や video format へ fallback せず、既存 YouTube VTT を明示的に使用してください。"
                + (f"（詳細: {detail}）" if detail else "")
            )
        output_path = _find_audio_output(temporary_dir, settings)
        content, digest = _audio_file_fingerprint(output_path)
        inspected = _inspect_wav_format(content)
        if inspected != _EXPECTED_WAV_FORMAT:
            raise AudioSpanError("変換後の音声形式が S9 の設定と一致しません。")
        metadata = _audio_cache_metadata(
            video_id=video_id,
            item=item,
            request_fingerprint=request_fingerprint,
            content=content,
            source_metadata=merged_source_metadata,
        )
        _atomic_write_audio_cache(audio_path, content, settings)
        write_text_atomically(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2),
        )
        return AudioSpanResult(
            video_id=video_id,
            range=item,
            path=audio_path,
            audio_bytes=content,
            audio_input_fingerprint=digest,
            source_metadata=merged_source_metadata,
            sample_rate=int(_AUDIO_FORMAT_SETTINGS["sample_rate"]),
            channel=int(_AUDIO_FORMAT_SETTINGS["channel"]),
            codec=str(_AUDIO_FORMAT_SETTINGS["codec"]),
            ffmpeg_settings=dict(_AUDIO_FORMAT_SETTINGS),
            cache_hit=False,
            request_fingerprint=request_fingerprint,
        )
    except AudioSpanError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise AudioSpanError("選択区間の音声準備に失敗しました。再試行してください。") from exc
    finally:
        if directory_descriptor != -1:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass
        if temporary_dir is not None:
            cleanup_ok = _cleanup_incoming_directory(
                temporary_dir,
                expected_identity=temporary_identity,
            )
            if not cleanup_ok:
                # 成果物 cache は既に atomic に確定していても、temporary span
                # の差し替えを黙って削除しない。呼び出し側は日本語診断を受ける。
                raise AudioSpanError("音声一時ファイルを安全に削除できません。")


def prepare_audio_spans(
    video_id: str,
    ranges: Sequence[AudioSpanRange | Any],
    settings: Settings | None = None,
    *,
    source_metadata: Mapping[str, Any] | None = None,
    padding_ms: int = 0,
    inclusion_rule: str = "half_open_overlap",
) -> tuple[AudioSpanResult, ...]:
    """複数音声 span を入力順に直列準備する."""

    manifest = make_audio_span_manifest(
        video_id,
        ranges,
        padding_ms=padding_ms,
        inclusion_rule=inclusion_rule,
    )
    return tuple(
        prepare_audio_span(
            video_id,
            item,
            settings,
            source_metadata=source_metadata,
        )
        for item in manifest
    )


# S9-3 の実装名を固定しつつ、呼び出し側の読みやすい別名も公開する。
download_audio_span = prepare_audio_span
prepare_audio_only_span = prepare_audio_span
download_audio_spans = prepare_audio_spans


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
    effective_timeout = settings.ytdlp_timeout if timeout is None else timeout
    if cwd_fd is not None and os.name != "posix":
        raise YtdlpError(
            "字幕を隔離保存する実行環境に対応していません。"
        )

    if cwd_fd is None:
        cmd = [settings.ytdlp_path, *args]
        inherited_fds = pass_fds
    else:
        cmd = [
            sys.executable,
            "-c",
            _YTDLP_CWD_FD_WRAPPER,
            str(cwd_fd),
            settings.ytdlp_path,
            *args,
        ]
        # The wrapper must receive cwd_fd even when the caller's additional
        # descriptors do not include it.  Preserve caller order and remove
        # duplicates so subprocess does not warn about repeated descriptors.
        inherited_fds = tuple(dict.fromkeys((*pass_fds, cwd_fd)))

    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=effective_timeout,
            pass_fds=inherited_fds,
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
