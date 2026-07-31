"""処理済み一覧ページのヘルパー関数テスト."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.services.storage import StorageError, StorageSummary, VideoStorage
from yt_live_kit.services.youtube_api import YouTubeAPIError
from yt_live_kit.ui.views import history


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
                intermediate_bytes=500,
                other_bytes=0,
                total_bytes=600,
            ),
        ],
    )

    assert history.source_bytes_map(summary) == {
        "abc123": 1000,
        "def456": 0,
    }


def test_deletable_bytes_map_includes_source_and_intermediate() -> None:
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
                intermediate_bytes=500,
                other_bytes=0,
                total_bytes=600,
            ),
        ],
    )

    assert history.deletable_bytes_map(summary) == {
        "abc123": 1200,
        "def456": 500,
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
    deletable_sizes = {"old1": 1024, "new1": 2048, "old2": 0}

    count, total_bytes = history.preview_purge_sources_older_than(
        processed,
        30,
        now=now,
        deletable_bytes_for=lambda video_id: deletable_sizes.get(video_id, 0),
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
        deletable_bytes_for=lambda _video_id: 999,
    )

    assert count == 0
    assert total_bytes == 0


def test_preview_purge_counts_video_with_intermediate_only() -> None:
    """元動画 0 バイト + 中間ファイルありの動画がプレビュー件数に入る."""
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    processed = [
        ProcessedVideo(
            video_id="old_seg",
            title="中間のみ",
            fetched_at=now - timedelta(days=40),
            has_chapters=True,
            has_transcript=True,
            has_clips=False,
        ),
    ]
    deletable_sizes = {"old_seg": 512}

    count, total_bytes = history.preview_purge_sources_older_than(
        processed,
        30,
        now=now,
        deletable_bytes_for=lambda video_id: deletable_sizes.get(video_id, 0),
    )

    assert count == 1
    assert total_bytes == 512


def test_source_bytes_map_excludes_intermediate_for_row_badge() -> None:
    """行バッジ用の元動画バイト数は中間ファイルを含まない."""
    summary = StorageSummary(
        total_bytes=600,
        videos=[
            VideoStorage(
                video_id="seg_only",
                title="中間のみ",
                source_bytes=0,
                output_bytes=0,
                intermediate_bytes=512,
                other_bytes=88,
                total_bytes=600,
            ),
        ],
    )

    assert history.source_bytes_map(summary)["seg_only"] == 0
    assert history.deletable_bytes_map(summary)["seg_only"] == 512


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
        patch("yt_live_kit.ui.views.history.start_job", return_value="job-1") as start_job,
        patch("yt_live_kit.ui.views.history.set_active_job_id") as set_active,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
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
            "yt_live_kit.ui.views.history.start_job",
            side_effect=JobBusyError(),
        ),
        patch("yt_live_kit.ui.views.history.set_active_job_id") as set_active,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
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
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", mock_button),
        patch("yt_live_kit.ui.views.history.st.warning"),
        patch(
            "yt_live_kit.ui.views.history._purge_confirm_ids",
            return_value={"vid1234567"},
        ),
    ):
        history._render_row_actions(
            video,
            busy=True,
            settings=settings,
        )

    disabled_by_label = dict(calls)
    assert disabled_by_label["削除を実行"] is True
    assert disabled_by_label["キャンセル"] is True
    assert disabled_by_label.get("チャプターを表示") is None


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
        patch("yt_live_kit.ui.views.history.st.expander", side_effect=mock_expander),
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", mock_button),
        patch("yt_live_kit.ui.views.history.st.markdown"),
        patch("yt_live_kit.ui.views.history.st.caption"),
        patch("yt_live_kit.ui.views.history.st.text"),
        patch("yt_live_kit.ui.views.history.st.divider"),
        patch("yt_live_kit.ui.views.history.st.number_input", return_value=30),
        patch("yt_live_kit.ui.views.history._get_storage_summary", return_value=summary),
        patch(
            "yt_live_kit.ui.views.history._get_bulk_preview",
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
        patch("yt_live_kit.ui.views.history.st.expander", side_effect=mock_expander),
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history.st.markdown"),
        patch("yt_live_kit.ui.views.history.st.caption"),
        patch("yt_live_kit.ui.views.history.st.text"),
        patch("yt_live_kit.ui.views.history.st.divider"),
        patch("yt_live_kit.ui.views.history.st.warning"),
        patch("yt_live_kit.ui.views.history.st.number_input", return_value=30),
        patch("yt_live_kit.ui.views.history._get_storage_summary", return_value=summary),
        patch(
            "yt_live_kit.ui.views.history._get_bulk_preview",
            return_value={"days": 30, "count": 1, "total_bytes": 1024},
        ),
        patch(
            "yt_live_kit.ui.views.history.purge_sources_older_than",
            side_effect=StorageError("一括削除に失敗しました"),
        ),
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
        patch("yt_live_kit.ui.views.history.st.success") as show_success,
        patch("yt_live_kit.ui.views.history.summarize") as summarize,
    ):
        history._render_storage_section(processed, settings, busy=False)

    show_error.assert_called_once_with("一括削除に失敗しました")
    show_success.assert_not_called()
    summarize.assert_not_called()


def test_render_row_actions_reruns_and_stores_message_on_purge_success() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    session_state: dict[str, object] = {}

    def mock_button(label: str, **kwargs: object) -> bool:
        return label == "削除を実行"

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history.st.warning"),
        patch(
            "yt_live_kit.ui.views.history._purge_confirm_ids",
            return_value={"vid1234567"},
        ),
        patch(
            "yt_live_kit.ui.views.history.purge_source",
            return_value=1024,
        ),
        patch("yt_live_kit.ui.views.history._get_storage_summary", return_value=None),
        patch("yt_live_kit.ui.views.history.st.session_state", session_state),
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.history.st.success") as show_success,
    ):
        history._render_row_actions(
            video,
            busy=False,
            settings=settings,
        )

    rerun.assert_called_once()
    show_success.assert_not_called()
    assert session_state[history._SESSION_PURGE_SUCCESS] == (
        "元動画と中間ファイルを削除しました（1.0 KB）。"
    )


def test_render_row_actions_disables_regen_buttons_without_transcript() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=False,
        has_transcript=False,
        has_clips=False,
    )
    settings = MagicMock()
    calls: list[tuple[str, bool | None, str | None]] = []

    def mock_button(label: str, **kwargs: object) -> bool:
        calls.append(
            (
                label,
                kwargs.get("disabled"),  # type: ignore[arg-type]
                kwargs.get("help"),  # type: ignore[arg-type]
            )
        )
        return False

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
    ):
        history._render_row_actions(
            video,
            busy=False,
            settings=settings,
        )

    regen_calls = [
        (label, disabled, help_text)
        for label, disabled, help_text in calls
        if label in ("チャプターを生成", "切り抜き候補を生成")
    ]
    assert len(regen_calls) == 2
    for _label, disabled, help_text in regen_calls:
        assert disabled is True
        assert help_text == "先に字幕の取得と整形を実行してください。"


def test_toggle_open_chapter_adds_and_removes_video_id() -> None:
    session_state: dict[str, object] = {}

    with patch("yt_live_kit.ui.views.history.st.session_state", session_state):
        assert history._open_chapter_ids() == set()

        history._toggle_open_chapter("vid1234567")
        assert history._open_chapter_ids() == {"vid1234567"}

        history._toggle_open_chapter("vid1234567")
        assert history._open_chapter_ids() == set()


def test_render_row_actions_toggles_chapter_visibility_and_reruns() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    session_state: dict[str, object] = {}

    def mock_button(label: str, **kwargs: object) -> bool:
        return label == "チャプターを表示"

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history.st.session_state", session_state),
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.history.load_result_from_disk"),
        patch("yt_live_kit.ui.views.history.st.code"),
        patch("yt_live_kit.ui.views.history.st.info"),
    ):
        history._render_row_actions(video, busy=False, settings=settings)

    rerun.assert_called_once()
    assert session_state[history._SESSION_OPEN_CHAPTER_IDS] == {"vid1234567"}


def test_render_row_actions_shows_chapters_code_when_open() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    loaded = MagicMock()
    loaded.chapters_text = "00:00 イントロ"

    def mock_button(label: str, **kwargs: object) -> bool:
        return False

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch(
            "yt_live_kit.ui.views.history._open_chapter_ids",
            return_value={"vid1234567"},
        ),
        patch(
            "yt_live_kit.ui.views.history.load_result_from_disk",
            return_value=loaded,
        ),
        patch("yt_live_kit.ui.views.history.st.code") as show_code,
        patch("yt_live_kit.ui.views.history.st.info") as show_info,
    ):
        history._render_row_actions(video, busy=False, settings=settings)

    show_code.assert_called_once_with("00:00 イントロ", language="markdown")
    show_info.assert_not_called()


def test_render_row_actions_shows_info_when_chapters_missing() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=False,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    loaded = MagicMock()
    loaded.chapters_text = "   "

    def mock_button(label: str, **kwargs: object) -> bool:
        return False

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch(
            "yt_live_kit.ui.views.history._open_chapter_ids",
            return_value={"vid1234567"},
        ),
        patch(
            "yt_live_kit.ui.views.history.load_result_from_disk",
            return_value=loaded,
        ),
        patch("yt_live_kit.ui.views.history.st.code") as show_code,
        patch("yt_live_kit.ui.views.history.st.info") as show_info,
    ):
        history._render_row_actions(video, busy=False, settings=settings)

    show_code.assert_not_called()
    show_info.assert_called_once_with(
        "チャプターがまだ生成されていません。「チャプターを生成」を実行してください。"
    )


def test_render_row_actions_desc_button_disabled_without_chapters() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=False,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    calls: list[tuple[str, bool | None, str | None]] = []

    def mock_button(label: str, **kwargs: object) -> bool:
        calls.append((label, kwargs.get("disabled"), kwargs.get("help")))  # type: ignore[arg-type]
        return False

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history._get_desc_preview", return_value=None),
    ):
        history._render_row_actions(video, busy=False, settings=settings)

    disabled_by_label = {label: (disabled, help_text) for label, disabled, help_text in calls}
    assert disabled_by_label["概要欄に反映"] == (
        True,
        "先にチャプターを生成してください。",
    )


def test_render_row_actions_desc_button_enabled_with_chapters() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    calls: list[tuple[str, bool | None, str | None]] = []

    def mock_button(label: str, **kwargs: object) -> bool:
        calls.append((label, kwargs.get("disabled"), kwargs.get("help")))  # type: ignore[arg-type]
        return False

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history._get_desc_preview", return_value=None),
    ):
        history._render_row_actions(video, busy=False, settings=settings)

    disabled_by_label = {label: (disabled, help_text) for label, disabled, help_text in calls}
    assert disabled_by_label["概要欄に反映"] == (False, None)


def test_desc_preview_session_state_helpers_set_get_clear() -> None:
    session_state: dict[str, object] = {}

    with patch("yt_live_kit.ui.views.history.st.session_state", session_state):
        assert history._get_desc_preview("vid1") is None

        history._set_desc_preview("vid1", "現在の概要", "反映後の概要")
        assert history._get_desc_preview("vid1") == {
            "current": "現在の概要",
            "merged": "反映後の概要",
        }

        history._clear_desc_preview("vid1")
        assert history._get_desc_preview("vid1") is None


def test_start_description_preview_errors_when_not_configured() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    settings.youtube_client_secret = "data/_config/client_secret.json"

    with (
        patch("yt_live_kit.ui.views.history.youtube_api.is_configured", return_value=False),
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._start_description_preview(video, settings)

    show_error.assert_called_once()
    rerun.assert_not_called()


def test_start_description_preview_errors_when_no_chapters() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    loaded = MagicMock()
    loaded.chapters_text = "   "

    with (
        patch("yt_live_kit.ui.views.history.youtube_api.is_configured", return_value=True),
        patch("yt_live_kit.ui.views.history.load_result_from_disk", return_value=loaded),
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._start_description_preview(video, settings)

    show_error.assert_called_once_with("チャプターがありません。")
    rerun.assert_not_called()


def test_start_description_preview_sets_preview_and_reruns() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    loaded = MagicMock()
    loaded.chapters_text = "0:00 イントロ\n1:00 本編\n2:00 まとめ"
    session_state: dict[str, object] = {}

    with (
        patch("yt_live_kit.ui.views.history.youtube_api.is_configured", return_value=True),
        patch("yt_live_kit.ui.views.history.load_result_from_disk", return_value=loaded),
        patch(
            "yt_live_kit.ui.views.history.youtube_api.fetch_video_snippet",
            return_value={"title": "t", "description": "既存の概要"},
        ),
        patch("yt_live_kit.ui.views.history.st.session_state", session_state),
        patch("yt_live_kit.ui.views.history.st.warning") as show_warning,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._start_description_preview(video, settings)

    rerun.assert_called_once()
    preview = session_state[history._SESSION_DESC_PREVIEW]["vid1234567"]
    assert preview["current"] == "既存の概要"
    assert "0:00 イントロ" in preview["merged"]
    # チャプター形式が正しいため警告は出ない
    show_warning.assert_not_called()


def test_start_description_preview_shows_error_on_youtube_api_error() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    loaded = MagicMock()
    loaded.chapters_text = "0:00 イントロ\n1:00 本編\n2:00 まとめ"

    with (
        patch("yt_live_kit.ui.views.history.youtube_api.is_configured", return_value=True),
        patch("yt_live_kit.ui.views.history.load_result_from_disk", return_value=loaded),
        patch(
            "yt_live_kit.ui.views.history.youtube_api.fetch_video_snippet",
            side_effect=YouTubeAPIError("エラーが発生しました"),
        ),
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._start_description_preview(video, settings)

    show_error.assert_called_once_with("エラーが発生しました")
    rerun.assert_not_called()


def test_render_description_preview_writes_and_clears_on_success() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    session_state: dict[str, object] = {
        history._SESSION_DESC_PREVIEW: {
            "vid1234567": {"current": "元", "merged": "反映後の概要"}
        }
    }

    def mock_button(label: str, **kwargs: object) -> bool:
        return label == "YouTube に書き込む"

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history.st.warning"),
        patch("yt_live_kit.ui.views.history.st.code"),
        patch("yt_live_kit.ui.views.history.st.session_state", session_state),
        patch(
            "yt_live_kit.ui.views.history.youtube_api.update_video_description"
        ) as update_desc,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._render_description_preview(video, busy=False, settings=settings)

    update_desc.assert_called_once_with("vid1234567", "反映後の概要", settings)
    rerun.assert_called_once()
    assert session_state[history._SESSION_DESC_PREVIEW].get("vid1234567") is None
    assert session_state[history._SESSION_PURGE_SUCCESS] == "概要欄に反映しました。"


def test_render_description_preview_shows_error_on_update_failure() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    session_state: dict[str, object] = {
        history._SESSION_DESC_PREVIEW: {
            "vid1234567": {"current": "元", "merged": "反映後の概要"}
        }
    }

    def mock_button(label: str, **kwargs: object) -> bool:
        return label == "YouTube に書き込む"

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history.st.warning"),
        patch("yt_live_kit.ui.views.history.st.code"),
        patch("yt_live_kit.ui.views.history.st.session_state", session_state),
        patch(
            "yt_live_kit.ui.views.history.youtube_api.update_video_description",
            side_effect=YouTubeAPIError("反映に失敗しました"),
        ),
        patch("yt_live_kit.ui.views.history.st.error") as show_error,
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._render_description_preview(video, busy=False, settings=settings)

    show_error.assert_called_once_with("反映に失敗しました")
    rerun.assert_not_called()
    assert session_state[history._SESSION_DESC_PREVIEW]["vid1234567"] is not None


def test_render_description_preview_cancel_clears_and_reruns() -> None:
    video = ProcessedVideo(
        video_id="vid1234567",
        title="テスト",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=False,
    )
    settings = MagicMock()
    session_state: dict[str, object] = {
        history._SESSION_DESC_PREVIEW: {
            "vid1234567": {"current": "元", "merged": "反映後の概要"}
        }
    }

    def mock_button(label: str, **kwargs: object) -> bool:
        return label == "キャンセル"

    with (
        patch("yt_live_kit.ui.views.history.st.columns", side_effect=_mock_columns),
        patch("yt_live_kit.ui.views.history.st.button", side_effect=mock_button),
        patch("yt_live_kit.ui.views.history.st.warning"),
        patch("yt_live_kit.ui.views.history.st.code"),
        patch("yt_live_kit.ui.views.history.st.session_state", session_state),
        patch("yt_live_kit.ui.views.history.st.rerun") as rerun,
    ):
        history._render_description_preview(video, busy=False, settings=settings)

    rerun.assert_called_once()
    assert session_state[history._SESSION_DESC_PREVIEW].get("vid1234567") is None
