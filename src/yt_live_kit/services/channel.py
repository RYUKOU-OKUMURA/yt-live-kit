"""チャンネル配信アーカイブ一覧の取得・キャッシュ."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.channel import ChannelListDocument, ChannelVideo
from yt_live_kit.services import history

_HANDLE_BARE = re.compile(r"^[a-zA-Z0-9_]+$")
_HANDLE_AT = re.compile(r"^@([a-zA-Z0-9_.-]+)$")
_YT_AT = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_.-]+)(?:/(?:videos|streams))?(?:/)?$",
    re.IGNORECASE,
)
_YT_CHANNEL = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/channel/([a-zA-Z0-9_-]+)(?:/(?:videos|streams))?(?:/)?$",
    re.IGNORECASE,
)
_YT_C = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/c/([a-zA-Z0-9_.-]+)(?:/(?:videos|streams))?(?:/)?$",
    re.IGNORECASE,
)

_CHANNEL_NOT_FOUND = "チャンネルが見つかりませんでした。URL またはハンドル名を確認してください。"
_EMPTY_ARCHIVES = "このチャンネルには公開されたライブ配信アーカイブがありません。"
_FETCH_FAILED = "一覧の取得に失敗しました。yt-dlp を最新版に更新して再実行してください。"
_INVALID_INPUT = "有効なチャンネル URL またはハンドル名ではありません: {input}"


class ChannelError(Exception):
    """チャンネル一覧取得エラー."""


def _sanitize_handle(handle: str) -> str:
    """キャッシュファイル名に使える安全なハンドル文字列にする."""
    sanitized = re.sub(r"[^\w\-]", "", handle)
    if not sanitized:
        raise ChannelError(_INVALID_INPUT.format(input=handle))
    return sanitized


def normalize_channel_url(text: str) -> tuple[str, str]:
    """入力を配信アーカイブ一覧 URL とキャッシュ用ハンドルに正規化する.

    受け付ける形式:
    - @handle / handle（英数字とアンダースコア）
    - youtube.com/@handle（/videos /streams 付き可）
    - youtube.com/channel/UC... / youtube.com/c/name
    """
    raw = text.strip()
    if not raw:
        raise ChannelError(_INVALID_INPUT.format(input=text))

    match = _HANDLE_AT.match(raw)
    if match:
        name = match.group(1)
        handle = _sanitize_handle(name)
        return f"https://www.youtube.com/@{name}/streams", handle

    if _HANDLE_BARE.match(raw):
        handle = _sanitize_handle(raw)
        return f"https://www.youtube.com/@{raw}/streams", handle

    match = _YT_AT.match(raw)
    if match:
        name = match.group(1)
        handle = _sanitize_handle(name)
        return f"https://www.youtube.com/@{name}/streams", handle

    match = _YT_CHANNEL.match(raw)
    if match:
        channel_id = match.group(1)
        handle = _sanitize_handle(channel_id)
        return f"https://www.youtube.com/channel/{channel_id}/streams", handle

    match = _YT_C.match(raw)
    if match:
        name = match.group(1)
        handle = _sanitize_handle(name)
        return f"https://www.youtube.com/c/{name}/streams", handle

    raise ChannelError(_INVALID_INPUT.format(input=text))


def _run_ytdlp(args: list[str], settings: Settings) -> subprocess.CompletedProcess[str]:
    cmd = [settings.ytdlp_path, *args]
    timeout = settings.ytdlp_timeout
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ChannelError(
            f"yt-dlp の実行が {timeout} 秒でタイムアウトしました。"
            "ネットワーク状況を確認してください。"
        ) from exc


def _parse_json_lines(stdout: str) -> list[dict]:
    """JSON Lines 出力を行ごとにパースする。空行・壊れた行はスキップ."""
    entries: list[dict] = []
    had_line = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        had_line = True
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(data)
    if had_line and not entries:
        raise ChannelError(_FETCH_FAILED)
    return entries


def _entry_to_video(entry: dict) -> ChannelVideo | None:
    video_id = entry.get("id")
    if not video_id or not isinstance(video_id, str):
        return None
    title = entry.get("title") or video_id
    url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
    duration = entry.get("duration")
    if duration is not None and not isinstance(duration, int):
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = None
    upload_date = entry.get("upload_date")
    if upload_date is not None and not isinstance(upload_date, str):
        upload_date = str(upload_date)
    return ChannelVideo(
        video_id=video_id,
        title=title,
        url=url,
        duration=duration,
        upload_date=upload_date,
    )


def _channels_cache_dir(settings: Settings) -> Path:
    return settings.data_dir / "_channels"


def list_archives(
    channel_url: str,
    *,
    limit: int = 50,
    settings: Settings | None = None,
) -> ChannelListDocument:
    """チャンネルの配信アーカイブ一覧を yt-dlp で取得する."""
    settings = settings or get_settings()

    if shutil.which(settings.ytdlp_path) is None:
        raise ChannelError(
            f"yt-dlp が見つかりません（パス: {settings.ytdlp_path}）。"
            "インストール後 PATH に通すか、YTLK_YTDLP_PATH を設定してください。"
        )

    normalized_url, handle = normalize_channel_url(channel_url)

    result = _run_ytdlp(
        [
            "--flat-playlist",
            "--dump-json",
            "--extractor-args",
            "youtube:lang=ja",
            "--playlist-end",
            str(limit),
            normalized_url,
        ],
        settings,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip().lower()
        if any(
            token in stderr
            for token in ("unable to extract", "not found", "does not exist", "404", "invalid")
        ):
            raise ChannelError(_CHANNEL_NOT_FOUND)
        detail = result.stderr.strip()
        raise ChannelError(
            _FETCH_FAILED + (f"\n（詳細: {detail}）" if detail else "")
        )

    entries = _parse_json_lines(result.stdout)
    videos: list[ChannelVideo] = []
    for entry in entries:
        video = _entry_to_video(entry)
        if video is not None:
            videos.append(video)

    if not videos:
        raise ChannelError(_EMPTY_ARCHIVES)

    return ChannelListDocument(
        channel_url=normalized_url,
        handle=handle,
        fetched_at=datetime.now(timezone.utc),
        videos=videos,
    )


def save_cache(doc: ChannelListDocument, settings: Settings | None = None) -> Path:
    """一覧を data/_channels/{handle}.json に保存する.

    UI はレート制限対策（NFR-05）のため既定で load_cache を使い、
    ユーザーが明示的に再取得したときのみ list_archives を呼ぶ想定。
    """
    settings = settings or get_settings()
    cache_dir = _channels_cache_dir(settings)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{doc.handle}.json"
    path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_cache(handle: str, settings: Settings | None = None) -> ChannelListDocument | None:
    """data/_channels/{handle}.json からキャッシュを読み込む."""
    settings = settings or get_settings()
    path = _channels_cache_dir(settings) / f"{handle}.json"
    if not path.is_file():
        return None
    try:
        return ChannelListDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def mark_processed(
    doc: ChannelListDocument,
    settings: Settings | None = None,
) -> list[tuple[ChannelVideo, bool]]:
    """history.list_processed_videos と突き合わせ、各動画の処理済みフラグを返す."""
    settings = settings or get_settings()
    processed_ids = {video.video_id for video in history.list_processed_videos(settings)}
    return [(video, video.video_id in processed_ids) for video in doc.videos]
