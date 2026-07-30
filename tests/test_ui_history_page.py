"""処理済み一覧ページのヘルパー関数テスト."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.services.storage import StorageError, StorageSummary, VideoStorage
from yt_live_kit.ui.pages import history


class _MockColumn:
    def __enter__(self) -> _MockColumn:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _mock_columns(spec: int | list[int]) -> list[_MockColumn]:
    count = len(spec) if isinstance(spec, list) else spec
    return [_MockColumn() for _ in range(count)]


def _collect_button_disabled_calls() -> tuple[list[tuple[str, bool | None]], MagicMock]:
    calls: list[tuple[str, bool | None]] = []

    def mock_button(label: str, **kwargs: object) -> bool:
        calls.append((label, kwargs.get("disabled")))  # type: ignore[arg-type]
        return False

    return calls, MagicMock(side_effect=mock_button)


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


def test_render_row_actions_disables_purge_confirm_buttons_when_busy() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=False,
        has_clips=False,
    )
    settings = MagicMock()
    calls, mock_button = _collect_button_disabled_calls()

    with (
        patch("yt_live_kit.ui.pages.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.pages.history.st.button", mock_button),
        patch("yt_live_kit.ui.pages.history.st.warning"),
        patch(
            "yt_live_kit.ui.pages.history._purge_confirm_ids",
            return_value={"vid1234567"},
        ),
    ):
        history._render_row_actions(
            video,
            busy=True,
            settings=settings,
            source_bytes=1024,
        )

    disabled_by_label = dict(calls)
    assert disabled_by_label["削除を実行"] is True
    assert disabled_by_label["キャンセル"] is True
    assert disabled_by_label.get("開く") is None


def test_render_storage_section_disables_storage_buttons_when_busy() -> None:
    processed = [
        ProcessedVideo(
            video_id="vid1234567",
            title="テスト",
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            has_chapters=True,
            has_transcript=False,
            has_clips=False,
        ),
    ]
    settings = MagicMock()
    summary = StorageSummary(
        total_bytes=1024,
        videos=[
            VideoStorage(
                video_id="vid1234567",
                title="テスト",
                source_bytes=1024,
                output_bytes=0,
                intermediate_bytes=0,
                other_bytes=0,
                total_bytes=1024,
            ),
        ],
    )
    calls, mock_button = _collect_button_disabled_calls()

    @contextmanager
    def mock_expander(_label: str, *, expanded: bool = False):
        yield

    with (
        patch("yt_live_kit.ui.pages.history.st.expander", side_effect=mock_expander),
        patch("yt_live_kit.ui.pages.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.pages.history.st.button", mock_button),
        patch("yt_live_kit.ui.pages.history.st.markdown"),
        patch("yt_live_kit.ui.pages.history.st.caption"),
        patch("yt_live_kit.ui.pages.history.st.text"),
        patch("yt_live_kit.ui.pages.history.st.divider"),
        patch("yt_live_kit.ui.pages.history.st.number_input", return_value=30),
        patch("yt_live_kit.ui.pages.history._get_storage_summary", return_value=summary),
        patch(
            "yt_live_kit.ui.pages.history._get_bulk_preview",
            return_value={"days": 30, "count": 1, "total_bytes": 1024},
        ),
    ):
        history._render_storage_section(processed, settings, busy=True)

    disabled_by_label = dict(calls)
    assert disabled_by_label["容量を再計算"] is True
    assert disabled_by_label["対象を確認"] is True
    assert disabled_by_label["確認をクリア"] is True
    assert disabled_by_label["1 件を削除する"] is True


def test_render_storage_section_shows_error_on_bulk_purge_storage_error() -> None:
    processed = [
        ProcessedVideo(
            video_id="vid1234567",
            title="テスト",
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            has_chapters=True,
            has_transcript=False,
            has_clips=False,
        ),
    ]
    settings = MagicMock()
    summary = StorageSummary(
        total_bytes=1024,
        videos=[
            VideoStorage(
                video_id="vid1234567",
                title="テスト",
                source_bytes=1024,
                output_bytes=0,
                intermediate_bytes=0,
                other_bytes=0,
                total_bytes=1024,
            ),
        ],
    )

    def mock_button(label: str, **kwargs: object) -> bool:
        return label == "1 件を削除する"

    @contextmanager
    def mock_expander(_label: str, *, expanded: bool = False):
        yield

    with (
        patch("yt_live_kit.ui.pages.history.st.expander", side_effect=mock_expander),
        patch("yt_live_kit.ui.pages.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.pages.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.pages.history.st.markdown"),
        patch("yt_live_kit.ui.pages.history.st.caption"),
        patch("yt_live_kit.ui.pages.history.st.text"),
        patch("yt_live_kit.ui.pages.history.st.divider"),
        patch("yt_live_kit.ui.pages.history.st.warning"),
        patch("yt_live_kit.ui.pages.history.st.number_input", return_value=30),
        patch("yt_live_kit.ui.pages.history._get_storage_summary", return_value=summary),
        patch(
            "yt_live_kit.ui.pages.history._get_bulk_preview",
            return_value={"days": 30, "count": 1, "total_bytes": 1024},
        ),
        patch(
            "yt_live_kit.ui.pages.history.purge_sources_older_than",
            side_effect=StorageError("一括削除に失敗しました"),
        ),
        patch("yt_live_kit.ui.pages.history.st.error") as show_error,
        patch("yt_live_kit.ui.pages.history.st.success") as show_success,
        patch("yt_live_kit.ui.pages.history.summarize") as summarize,
    ):
        history._render_storage_section(processed, settings, busy=False)

    show_error.assert_called_once_with("一括削除に失敗しました")
    show_success.assert_not_called()
    summarize.assert_not_called()
