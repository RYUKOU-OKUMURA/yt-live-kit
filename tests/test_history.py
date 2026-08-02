"""history サービスのユニットテスト."""

import json
from datetime import datetime, timezone
from pathlib import Path

from yt_live_kit.config import Settings
from yt_live_kit.services.history import (
    is_video_processed,
    is_video_targets_complete,
    list_processed_videos,
)


def _write_meta(
    video_dir: Path,
    video_id: str,
    title: str,
    *,
    fetched_at: datetime | None = None,
) -> None:
    meta = {
        "id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": 3600,
        "ytdlp_version": "2026.7.4",
        "fetched_at": (
            fetched_at or datetime(2026, 7, 30, tzinfo=timezone.utc)
        ).isoformat(),
        "subtitle_lang": "ja",
    }
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_list_processed_videos(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)

    _write_meta(tmp_path / "videoAaaaaaa", "videoAaaaaaa", "動画A")
    (tmp_path / "videoAaaaaaa" / "chapters").mkdir()
    (tmp_path / "videoAaaaaaa" / "chapters" / "chapters.md").write_text(
        "0:00 A\n", encoding="utf-8"
    )
    (tmp_path / "videoAaaaaaa" / "transcript").mkdir()
    (tmp_path / "videoAaaaaaa" / "transcript" / "full.txt").write_text("full", encoding="utf-8")

    _write_meta(tmp_path / "videoBbbbbbb", "videoBbbbbbb", "動画B")

    (tmp_path / "_batch").mkdir()

    videos = list_processed_videos(settings)
    assert len(videos) == 2


def test_list_processed_videos_normalizes_legacy_naive_datetime_to_utc(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    _write_meta(
        tmp_path / "legacyVideo1",
        "legacyVideo1",
        "旧データ",
        fetched_at=datetime(2026, 7, 30, 12, 0),
    )
    _write_meta(
        tmp_path / "awareVideo01",
        "awareVideo01",
        "新データ",
        fetched_at=datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc),
    )

    videos = list_processed_videos(settings)

    assert [video.video_id for video in videos] == ["awareVideo01", "legacyVideo1"]
    assert all(video.fetched_at is not None for video in videos)
    assert all(video.fetched_at.utcoffset().total_seconds() == 0 for video in videos)


def test_is_video_processed(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "test1234567"
    video_dir.mkdir()
    assert not is_video_processed("test1234567", settings)

    (video_dir / "chapters").mkdir()
    (video_dir / "chapters" / "chapters.md").write_text("0:00 x\n", encoding="utf-8")
    assert is_video_processed("test1234567", settings)


def _setup_video_with_chapters(video_dir: Path) -> None:
    (video_dir / "chapters").mkdir(parents=True)
    (video_dir / "chapters" / "chapters.md").write_text("0:00 x\n", encoding="utf-8")


def _setup_video_with_clips(video_dir: Path) -> None:
    (video_dir / "clips").mkdir(parents=True)
    (video_dir / "clips" / "candidates.json").write_text("[]", encoding="utf-8")


def test_is_video_targets_complete_chapters_only(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "test1234567"
    video_dir.mkdir()
    _setup_video_with_chapters(video_dir)

    assert is_video_targets_complete(
        "test1234567", settings, do_chapters=True, do_clips=False
    )
    assert not is_video_targets_complete(
        "test1234567", settings, do_chapters=True, do_clips=True
    )


def test_is_video_targets_complete_clips_only(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "test1234567"
    video_dir.mkdir()
    _setup_video_with_chapters(video_dir)

    assert not is_video_targets_complete(
        "test1234567", settings, do_chapters=False, do_clips=True
    )

    _setup_video_with_clips(video_dir)
    assert is_video_targets_complete(
        "test1234567", settings, do_chapters=False, do_clips=True
    )


def test_is_video_targets_complete_both_required(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "test1234567"
    video_dir.mkdir()
    _setup_video_with_chapters(video_dir)

    assert not is_video_targets_complete("test1234567", settings)

    _setup_video_with_clips(video_dir)
    assert is_video_targets_complete("test1234567", settings)
