"""yt-dlp ラッパー — メタデータ・字幕取得."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta

_SUBTITLE_FETCH_ERROR = (
    "字幕が取得できませんでした。公開アーカイブか確認し、yt-dlp を最新にして再実行してください。"
)

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


def extract_video_id(url_or_id: str) -> str:
    """YouTube URL または動画 ID から video_id を抽出する."""
    text = url_or_id.strip()
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    raise YtdlpError(f"有効な YouTube URL または動画 ID ではありません: {url_or_id}")


def _run_ytdlp(args: list[str], settings: Settings) -> subprocess.CompletedProcess[str]:
    cmd = [settings.ytdlp_path, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def get_ytdlp_version(settings: Settings | None = None) -> str:
    """yt-dlp のバージョン文字列を返す."""
    settings = settings or get_settings()
    result = _run_ytdlp(["--version"], settings)
    if result.returncode != 0:
        raise YtdlpError(f"yt-dlp のバージョン取得に失敗しました: {result.stderr.strip()}")
    return result.stdout.strip()


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


def _find_subtitle_file(subtitles_dir: Path, video_id: str) -> tuple[Path, str]:
    """優先順: ja-orig > ja."""
    candidates = [
        (subtitles_dir / f"{video_id}.ja-orig.vtt", "ja-orig"),
        (subtitles_dir / f"{video_id}.ja.vtt", "ja-orig"),
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
    settings.ensure_data_dir()

    if shutil.which(settings.ytdlp_path) is None:
        raise YtdlpError(
            f"yt-dlp が見つかりません（パス: {settings.ytdlp_path}）。"
            "インストール後 PATH に通すか、YTLK_YTDLP_PATH を設定してください。"
        )

    ytdlp_version = get_ytdlp_version(settings)
    info = _fetch_metadata(url, settings)

    video_id = info.get("id") or extract_video_id(url)
    video_dir = settings.data_dir / video_id
    subtitles_dir = video_dir / "subtitles"
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
    meta_path.write_text(
        meta.model_dump_json(indent=2),
        encoding="utf-8",
    )

    return meta
