"""ui/views/highlights.py のヘルパー関数テスト."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.ui.views.highlights import (
    _confirm_build_highlight_dialog,
    _confirm_regenerate_highlights_dialog,
    _start_or_confirm_build_highlight,
    _start_or_confirm_regenerate_highlights,
    build_highlight_job_target,
    can_build_highlight,
    clip_to_highlight_segment,
    collect_selected_segments,
    format_duration_jp,
    format_segment_label,
    format_selection_summary,
    segment_checkbox_key,
    sum_duration_sec,
)


def _segment(
    segment_id: str = "hl_001",
    *,
    duration_sec: int = 72,
) -> HighlightSegment:
    return HighlightSegment(
        id=segment_id,
        title=f"title-{segment_id}",
        start="0:01:00",
        end="0:02:12",
        duration_sec=duration_sec,
        reason="テスト理由",
    )


def test_format_duration_jp() -> None:
    assert format_duration_jp(45) == "45 秒"
    assert format_duration_jp(60) == "1 分"
    assert format_duration_jp(372) == "6 分 12 秒"


def test_format_selection_summary() -> None:
    assert format_selection_summary(5, 372) == "選択中: 5 区間 / 合計 6 分 12 秒"
    assert format_selection_summary(0, 0) == "選択中: 0 区間 / 合計 0 秒"


def test_can_build_highlight_requires_two_or_more() -> None:
    assert can_build_highlight(0) is False
    assert can_build_highlight(1) is False
    assert can_build_highlight(2) is True
    assert can_build_highlight(5) is True


def test_sum_duration_sec() -> None:
    segments = [_segment("a", duration_sec=60), _segment("b", duration_sec=90)]
    assert sum_duration_sec(segments) == 150


def test_segment_checkbox_key_includes_video_and_segment_id() -> None:
    assert segment_checkbox_key("vid123", "hl_001") == "hl_cb_vid123_hl_001"


def test_collect_selected_segments_reads_session_state() -> None:
    segments = [_segment("a"), _segment("b"), _segment("c")]
    session_state = {
        segment_checkbox_key("vid", "a"): True,
        segment_checkbox_key("vid", "b"): False,
        segment_checkbox_key("vid", "c"): True,
    }

    selected = collect_selected_segments(segments, "vid", session_state)

    assert [segment.id for segment in selected] == ["a", "c"]


def test_format_segment_label() -> None:
    segment = _segment()
    assert format_segment_label(segment) == "0:01:00 → 0:02:12 / 1 分 12 秒 / title-hl_001"


def test_clip_to_highlight_segment_copies_fields() -> None:
    clip = ClipCandidate(
        id="clip_001",
        title="clip title",
        start="0:10:00",
        end="0:25:00",
        duration_sec=900,
        reason="clip reason",
    )

    segment = clip_to_highlight_segment(clip)

    assert segment.id == clip.id
    assert segment.title == clip.title
    assert segment.start == clip.start
    assert segment.end == clip.end
    assert segment.duration_sec == clip.duration_sec
    assert segment.reason == clip.reason


@patch("yt_live_kit.services.ffmpeg.cut_and_concat")
def test_build_highlight_job_target_passes_settings_ffmpeg_path(mock_concat, tmp_path) -> None:
    """build_highlight_job_target が cut_and_concat に settings.ffmpeg_path を渡すこと（修正3）."""
    settings = Settings(data_dir=tmp_path, ffmpeg_path="/opt/homebrew/bin/ffmpeg")
    segment = _segment()
    reports: list[dict[str, object]] = []

    def report(**kwargs: object) -> None:
        reports.append(kwargs)

    build_highlight_job_target(
        report=report,
        settings=settings,
        video_id="vid123",
        segment_dicts=[segment.model_dump()],
    )

    mock_concat.assert_called_once()
    assert mock_concat.call_args.kwargs["ffmpeg_path"] == "/opt/homebrew/bin/ffmpeg"


def test_existing_highlight_segments_open_dialog_without_starting_job() -> None:
    result = MagicMock(video_id="vid123")
    settings = MagicMock()
    with (
        patch(
            "yt_live_kit.ui.views.highlights._confirm_regenerate_highlights_dialog"
        ) as dialog,
        patch(
            "yt_live_kit.ui.views.highlights._start_regenerate_highlights"
        ) as start,
    ):
        _start_or_confirm_regenerate_highlights(
            result, settings, output_exists=True
        )

    dialog.assert_called_once_with(result, settings)
    start.assert_not_called()


def test_missing_highlight_segments_start_without_dialog() -> None:
    result = MagicMock(video_id="vid123")
    settings = MagicMock()
    with (
        patch(
            "yt_live_kit.ui.views.highlights._confirm_regenerate_highlights_dialog"
        ) as dialog,
        patch(
            "yt_live_kit.ui.views.highlights._start_regenerate_highlights"
        ) as start,
    ):
        _start_or_confirm_regenerate_highlights(
            result, settings, output_exists=False
        )

    start.assert_called_once_with(result, settings)
    dialog.assert_not_called()


def test_regenerate_highlights_dialog_requires_confirmation() -> None:
    result = MagicMock(video_id="vid123")
    settings = MagicMock()
    with (
        patch("yt_live_kit.ui.views.highlights.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.highlights.st.warning"),
        patch(
            "yt_live_kit.ui.views.highlights.st.button",
            side_effect=[False, True],
        ),
        patch(
            "yt_live_kit.ui.views.highlights._start_regenerate_highlights"
        ) as start,
    ):
        _confirm_regenerate_highlights_dialog.__wrapped__(result, settings)
        start.assert_not_called()
        _confirm_regenerate_highlights_dialog.__wrapped__(result, settings)

    start.assert_called_once_with(result, settings)


def test_existing_highlight_video_opens_dialog_without_starting_job() -> None:
    result = MagicMock(video_id="vid123")
    selected = [_segment("a"), _segment("b")]
    settings = MagicMock()
    with (
        patch(
            "yt_live_kit.ui.views.highlights._confirm_build_highlight_dialog"
        ) as dialog,
        patch("yt_live_kit.ui.views.highlights._start_build_highlight") as start,
    ):
        _start_or_confirm_build_highlight(
            result,
            selected,
            settings,
            output_exists=True,
        )

    dialog.assert_called_once_with(result, selected, settings)
    start.assert_not_called()


def test_missing_highlight_video_starts_without_dialog() -> None:
    result = MagicMock(video_id="vid123")
    selected = [_segment("a"), _segment("b")]
    settings = MagicMock()
    with (
        patch(
            "yt_live_kit.ui.views.highlights._confirm_build_highlight_dialog"
        ) as dialog,
        patch("yt_live_kit.ui.views.highlights._start_build_highlight") as start,
    ):
        _start_or_confirm_build_highlight(
            result,
            selected,
            settings,
            output_exists=False,
        )

    start.assert_called_once_with(result, selected, settings)
    dialog.assert_not_called()


def test_build_highlight_dialog_requires_confirmation() -> None:
    result = MagicMock(video_id="vid123")
    selected = [_segment("a"), _segment("b")]
    settings = MagicMock()
    with (
        patch("yt_live_kit.ui.views.highlights.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.highlights.st.warning"),
        patch(
            "yt_live_kit.ui.views.highlights.st.button",
            side_effect=[False, True],
        ),
        patch("yt_live_kit.ui.views.highlights._start_build_highlight") as start,
    ):
        _confirm_build_highlight_dialog.__wrapped__(result, selected, settings)
        start.assert_not_called()
        _confirm_build_highlight_dialog.__wrapped__(result, selected, settings)

    start.assert_called_once_with(result, selected, settings)
