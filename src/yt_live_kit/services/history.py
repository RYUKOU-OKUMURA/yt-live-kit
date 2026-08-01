"""処理済み動画の一覧・成果物参照."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta


@dataclass(frozen=True)
class ProcessedVideo:
    """data/ 内の処理済み動画サマリー."""

    video_id: str
    title: str
    fetched_at: datetime | None
    has_chapters: bool
    has_transcript: bool
    has_clips: bool


def list_processed_videos(settings: Settings | None = None) -> list[ProcessedVideo]:
    """data/ をスキャンし、処理済み動画の一覧を返す（新しい順）."""
    settings = settings or get_settings()
    data_dir = settings.data_dir
    if not data_dir.is_dir():
        return []

    results: list[ProcessedVideo] = []
    for entry in data_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith("_"):
            continue

        meta_path = entry / "meta.json"
        if not meta_path.is_file():
            continue

        try:
            meta = VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        results.append(
            ProcessedVideo(
                video_id=meta.id,
                title=meta.title,
                fetched_at=meta.fetched_at,
                has_chapters=(entry / "chapters" / "chapters.md").is_file(),
                has_transcript=(entry / "transcript" / "full.txt").is_file(),
                has_clips=(entry / "clips" / "candidates.json").is_file(),
            )
        )

    results.sort(
        key=lambda v: v.fetched_at or datetime.min.replace(tzinfo=None),
        reverse=True,
    )
    return results


def is_video_processed(video_id: str, settings: Settings | None = None) -> bool:
    """チャプターが保存済みなら処理済みとみなす."""
    settings = settings or get_settings()
    chapters_path = settings.data_dir / video_id / "chapters" / "chapters.md"
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
    video_dir = settings.data_dir / video_id

    if do_chapters:
        chapters_path = video_dir / "chapters" / "chapters.md"
        if not chapters_path.is_file():
            return False

    if do_clips:
        clips_path = video_dir / "clips" / "candidates.json"
        if not clips_path.is_file():
            return False

    return True
