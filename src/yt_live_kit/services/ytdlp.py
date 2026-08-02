"""yt-dlp ラッパー — メタデータ・字幕取得."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_video_path,
    confined_path,
    safe_identifier,
)

_SUBTITLE_FETCH_ERROR = (
    "字幕が取得できませんでした。公開アーカイブか確認し、yt-dlp を最新にして再実行してください。"
)

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
) -> subprocess.CompletedProcess[str]:
    cmd = [settings.ytdlp_path, *args]
    effective_timeout = settings.ytdlp_timeout if timeout is None else timeout
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise YtdlpError(
            f"yt-dlp の実行が {effective_timeout} 秒でタイムアウトしました。"
            "ネットワーク状況を確認してください。"
        ) from exc


def get_ytdlp_version(settings: Settings | None = None) -> str:
    """yt-dlp のバージョン文字列を返す."""
    settings = settings or get_settings()
    result = _run_ytdlp(["--version"], settings)
    if result.returncode != 0:
        raise YtdlpError(f"yt-dlp のバージョン取得に失敗しました: {result.stderr.strip()}")
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
        stderr = result.stderr.strip()
        raise YtdlpError(f"メタデータの取得に失敗しました: {stderr or '不明なエラー'}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise YtdlpError("メタデータの解析に失敗しました") from exc


def _download_subtitles(url: str, output_dir: Path, settings: Settings) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _validate_directory_entries(output_dir, settings, "字幕保存先")
    output_template = str(output_dir / "%(id)s")
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
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise SubtitleNotFoundError(
            _SUBTITLE_FETCH_ERROR if not stderr else f"{_SUBTITLE_FETCH_ERROR}\n（詳細: {stderr}）"
        )
    _validate_directory_entries(output_dir, settings, "字幕保存先")


def _validate_directory_entries(
    directory: Path, settings: Settings, label: str
) -> None:
    try:
        for entry in directory.iterdir():
            confined_path(settings.data_dir, entry, label=label)
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise YtdlpError(f"{label}を安全に確認できませんでした。") from exc


def _find_subtitle_file(subtitles_dir: Path, video_id: str) -> tuple[Path, str]:
    """優先順: ja-orig > ja."""
    video_id = _safe_identifier(video_id, "動画 ID")
    candidates = [
        (subtitles_dir / f"{video_id}.ja-orig.vtt", "ja-orig"),
        (subtitles_dir / f"{video_id}.ja.vtt", "ja"),
        (subtitles_dir / "ja-orig.vtt", "ja-orig"),
        (subtitles_dir / "ja.vtt", "ja"),
    ]
    for path, lang in candidates:
        if path.is_file():
            return path, lang

    vtt_files = sorted(subtitles_dir.glob("*.vtt"))
    if vtt_files:
        path = vtt_files[0]
        lang = "ja-orig" if "ja-orig" in path.name else "ja"
        return path, lang

    raise SubtitleNotFoundError()


def _normalize_subtitle_path(subtitles_dir: Path, source: Path, lang: str) -> Path:
    """字幕を subtitles/ja.vtt に統一保存（ja-orig の場合も ja.vtt へコピー）."""
    target = subtitles_dir / "ja.vtt"
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


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
    subtitles_dir.mkdir(parents=True, exist_ok=True)

    _download_subtitles(url, subtitles_dir, settings)
    subtitle_path, subtitle_lang = _find_subtitle_file(subtitles_dir, video_id)
    _normalize_subtitle_path(subtitles_dir, subtitle_path, subtitle_lang)

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

    meta_path = video_dir / "meta.json"
    write_text_atomically(meta_path, meta.model_dump_json(indent=2))

    return meta


def download_video(url: str, output_dir: Path, settings: Settings | None = None) -> Path:
    """切り出し用に動画を 1 本ダウンロードする."""
    settings = settings or get_settings()
    extract_video_id(url)
    try:
        output_dir = confined_path(settings.data_dir, output_dir, label="動画保存先")
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
        stderr = result.stderr.strip()
        raise YtdlpError(
            "動画のダウンロードに失敗しました。"
            + (f"\n（詳細: {stderr}）" if stderr else "")
        )

    _validate_directory_entries(output_dir, settings, "動画保存先")
    mp4_files = sorted(output_dir.glob("*.mp4"))
    if mp4_files:
        try:
            return confined_path(settings.data_dir, mp4_files[0], label="動画保存先")
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
        return confined_path(settings.data_dir, video_files[0], label="動画保存先")
    except PathConfinementError as exc:
        raise YtdlpError(str(exc)) from exc
