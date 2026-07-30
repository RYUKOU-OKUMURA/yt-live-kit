"""処理済み一覧ページのヘルパー関数テスト."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.services.storage import StorageSummary, VideoStorage
from yt_live_kit.ui.pages import history


def test_chapter_button_label_switches_on_has_chapters() -> None:
    assert history.chapter_button_label(True) == "チャプターを再生成"
    assert history.chapter_button_label(False) == "チャプターを生成"


def test_clips_button_label_switches_on_has_clips() -> None:
    assert history.clips_button_label(True) == "切り抜き候補を再生成"
    assert history.clips_button_label(False) == "切り抜き候補を生成"


def test_source_bytes_map_returns_video_id_to_bytes() -> None:
    summary = StorageSummary(
        total_bytes=3000,
        videos=[
            VideoStorage(
                video_id="abc123",
                title="A",
                source_bytes=1000,
                output_bytes=500,
                intermediate_bytes=200,
                other_bytes=100,
                total_bytes=1800,
            ),
            VideoStorage(
                video_id="def456",
                title="B",
                source_bytes=0,
                output_bytes=100,
                intermediate_bytes=0,
                other_bytes=0,
                total_bytes=100,
            ),
        ],
    )

    assert history.source_bytes_map(summary) == {
        "abc123": 1000,
        "def456": 0,
    }


def test_format_video_storage_row_includes_breakdown() -> None:
    video = VideoStorage(
        video_id="abc123",
        title="テスト動画",
        source_bytes=1024**3,
        output_bytes=1024**2,
        intermediate_bytes=1024,
        other_bytes=512,
        total_bytes=1024**3 + 1024**2 + 1024 + 512,
    )

    row = history.format_video_storage_row(video)

    assert "テスト動画" in row
    assert "元動画" in row
    assert "成果物" in row
    assert "中間" in row
    assert "その他" in row


def test_preview_purge_sources_older_than_counts_eligible_videos() -> None:
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    processed = [
        ProcessedVideo(
            video_id="old1",
            title="古い",
            fetched_at=now - timedelta(days=40),
            has_chapters=True,
            has_transcript=True,
            has_clips=False,
        ),
        ProcessedVideo(
            video_id="new1",
            title="新しい",
            fetched_at=now - timedelta(days=5),
            has_chapters=True,
            has_transcript=True,
            has_clips=False,
        ),
        ProcessedVideo(
            video_id="old2",
            title="古いがソースなし",
            fetched_at=now - timedelta(days=40),
            has_chapters=True,
            has_transcript=True,
            has_clips=False,
        ),
    ]
    source_sizes = {"old1": 1024, "new1": 2048, "old2": 0}

    count, total_bytes = history.preview_purge_sources_older_than(
        processed,
        30,
        now=now,
        source_bytes_for=lambda video_id: source_sizes.get(video_id, 0),
    )

    assert count == 1
    assert total_bytes == 1024


def test_preview_purge_sources_older_than_skips_missing_fetched_at() -> None:
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    processed = [
        ProcessedVideo(
            video_id="no_date",
            title="日時不明",
            fetched_at=None,
            has_chapters=True,
            has_transcript=False,
            has_clips=False,
        ),
    ]

    count, total_bytes = history.preview_purge_sources_older_than(
        processed,
        30,
        now=now,
        source_bytes_for=lambda _video_id: 999,
    )

    assert count == 0
    assert total_bytes == 0


def test_start_regenerate_calls_start_job_and_reruns() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=False,
        has_clips=False,
    )
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.pages.history.start_job", return_value="job-1") as start_job,
        patch("yt_live_kit.ui.pages.history.set_active_job_id") as set_active,
        patch("yt_live_kit.ui.pages.history.st.rerun") as rerun,
        patch("yt_live_kit.ui.pages.history.st.error") as show_error,
    ):
        history._start_regenerate(video, "chapters", settings)

    start_job.assert_called_once_with(
        "regenerate",
        history.regenerate_job_target,
        video_id="vid1234567",
        title="テスト",
        target="chapters",
        settings=settings,
    )
    set_active.assert_called_once_with("job-1")
    rerun.assert_called_once()
    show_error.assert_not_called()


def test_start_regenerate_shows_error_on_job_busy() -> None:
    from yt_live_kit.services.jobs import JobBusyError

    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=False,
        has_transcript=False,
        has_clips=False,
    )
    settings = MagicMock()

    with (
        patch(
            "yt_live_kit.ui.pages.history.start_job",
            side_effect=JobBusyError(),
        ),
        patch("yt_live_kit.ui.pages.history.set_active_job_id") as set_active,
        patch("yt_live_kit.ui.pages.history.st.rerun") as rerun,
        patch("yt_live_kit.ui.pages.history.st.error") as show_error,
    ):
        history._start_regenerate(video, "clips", settings)

    show_error.assert_called_once()
    set_active.assert_not_called()
    rerun.assert_not_called()
