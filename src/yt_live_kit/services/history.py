"""処理済み動画の一覧・成果物参照."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services._paths import (
    RESERVED_DATA_DIR_NAMES,
    PathConfinementError,
    confined_video_path,
)


class HistoryError(Exception):
    """履歴の保存先を安全に扱えないエラー."""


def _video_path(video_id: object, settings: Settings, *parts: str):
    try:
        return confined_video_path(
            settings.data_dir, video_id, *parts, label="動画保存先"
        )
    except PathConfinementError as exc:
        raise HistoryError(str(exc)) from exc


def _video_dir(video_id: object, settings: Settings):
    return _video_path(video_id, settings)


@dataclass(frozen=True)
class ProcessedVideo:
    """data/ 内の処理済み動画サマリー."""

    video_id: str
    title: str
    fetched_at: datetime | None
    has_chapters: bool
    has_transcript: bool
    has_clips: bool


def _as_utc(value: datetime | None) -> datetime | None:
    """旧 metadata の naive 日時を UTC とみなし、比較可能な aware 日時へ揃える."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def list_processed_videos(settings: Settings | None = None) -> list[ProcessedVideo]:
    """data/ をスキャンし、処理済み動画の一覧を返す（新しい順）."""
    settings = settings or get_settings()
    data_dir = settings.data_dir
    if not data_dir.is_dir():
        return []

    results: list[ProcessedVideo] = []
    for entry in data_dir.iterdir():
        if entry.name in RESERVED_DATA_DIR_NAMES:
            continue

        meta_path = _video_path(entry.name, settings, "meta.json")
        video_dir = _video_dir(entry.name, settings)
        if not video_dir.is_dir() or not meta_path.is_file():
            continue

        try:
            meta = VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        results.append(
            ProcessedVideo(
                video_id=meta.id,
                title=meta.title,
                fetched_at=_as_utc(meta.fetched_at),
                has_chapters=_video_path(
                    entry.name, settings, "chapters", "chapters.md"
                ).is_file(),
                has_transcript=_video_path(
                    entry.name, settings, "transcript", "full.txt"
                ).is_file(),
                has_clips=_video_path(
                    entry.name, settings, "clips", "candidates.json"
                ).is_file(),
            )
        )

    results.sort(
        key=lambda v: v.fetched_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return results


def is_video_processed(video_id: str, settings: Settings | None = None) -> bool:
    """チャプターが保存済みなら処理済みとみなす."""
    settings = settings or get_settings()
    chapters_path = _video_path(video_id, settings, "chapters", "chapters.md")
    return chapters_path.is_file()


def is_video_targets_complete(
    video_id: str,
    settings: Settings | None = None,
    *,
    do_chapters: bool = True,
    do_clips: bool = True,
) -> bool:
    """今回要求されている成果物がすべて揃っていれば True."""
    settings = settings or get_settings()
    video_dir = _video_dir(video_id, settings)

    if do_chapters:
        chapters_path = _video_path(video_id, settings, "chapters", "chapters.md")
        if not chapters_path.is_file():
            return False

    if do_clips:
        clips_path = _video_path(video_id, settings, "clips", "candidates.json")
        if not clips_path.is_file():
            return False

    return True
