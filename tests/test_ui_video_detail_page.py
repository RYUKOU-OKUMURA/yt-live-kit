"""動画詳細ページの状態計算・表示分岐・確認ダイアログのテスト."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.services.pipeline import PipelineResult
from yt_live_kit.ui.components.clipboard import build_clipboard_copy_html
from yt_live_kit.ui.views import video_detail
from yt_live_kit.ui.views._local_settings import (
    load_description_applied_ids,
    mark_description_applied,
    save_description_applied_ids,
)


def _video(
    *,
    transcript: bool = False,
    chapters: bool = False,
    clips: bool = False,
) -> ProcessedVideo:
    return ProcessedVideo(
        video_id="vid1234567",
        title="テスト動画",
        fetched_at=None,
        has_chapters=chapters,
        has_transcript=transcript,
        has_clips=clips,
    )


def _result(tmp_path: Path, *, chapters: str = "", candidates: tuple = ()) -> PipelineResult:
    meta = VideoMeta(
        id="vid1234567",
        title="テスト動画",
        url="https://example.com/watch?v=vid1234567",
        ytdlp_version="2026.01.01",
        fetched_at=datetime.now(timezone.utc),
    )
    return PipelineResult(
        video_id=meta.id,
        title=meta.title,
        meta=meta,
        chapters_text=chapters,
        chapters_path=tmp_path / "chapters.md",
        full_transcript_path=tmp_path / "full.txt",
        full_transcript_text="全文です",
        clips_candidates=candidates,
        clips_candidates_path=None,
    )


def _statuses(steps: tuple[video_detail.ProgressStep, ...]) -> list[str]:
    return [step.status for step in steps]


def test_stepper_marks_transcript_as_first_next() -> None:
    steps = video_detail.calculate_progress_steps(
        _video(), None, shorts_count=0, description_applied_ids=set()
    )
    assert _statuses(steps) == ["next", "pending", "pending", "pending", "pending"]


def test_stepper_marks_chapters_as_next_after_transcript(tmp_path: Path) -> None:
    steps = video_detail.calculate_progress_steps(
        _video(transcript=True),
        _result(tmp_path),
        shorts_count=0,
        description_applied_ids=set(),
    )
    assert _statuses(steps) == ["complete", "next", "pending", "pending", "pending"]


def test_stepper_accepts_result_chapters_and_moves_to_candidates(tmp_path: Path) -> None:
    steps = video_detail.calculate_progress_steps(
        _video(transcript=True),
        _result(tmp_path, chapters="0:00 はじめに"),
        shorts_count=0,
        description_applied_ids=set(),
    )
    assert _statuses(steps) == ["complete", "complete", "next", "pending", "pending"]


def test_stepper_accepts_highlight_artifact_as_candidate_completion(tmp_path: Path) -> None:
    steps = video_detail.calculate_progress_steps(
        _video(transcript=True, chapters=True),
        _result(tmp_path, chapters="0:00 はじめに"),
        shorts_count=0,
        description_applied_ids=set(),
        has_highlights=True,
    )
    assert _statuses(steps) == ["complete", "complete", "complete", "next", "pending"]


def test_stepper_moves_to_description_after_short(tmp_path: Path) -> None:
    steps = video_detail.calculate_progress_steps(
        _video(transcript=True, chapters=True, clips=True),
        _result(tmp_path, chapters="0:00 はじめに"),
        shorts_count=2,
        description_applied_ids=set(),
    )
    assert _statuses(steps) == ["complete", "complete", "complete", "complete", "next"]


def test_stepper_marks_everything_complete(tmp_path: Path) -> None:
    steps = video_detail.calculate_progress_steps(
        _video(transcript=True, chapters=True, clips=True),
        _result(tmp_path, chapters="0:00 はじめに"),
        shorts_count=1,
        description_applied_ids={"vid1234567"},
    )
    assert _statuses(steps) == ["complete"] * 5


def test_description_applied_ids_round_trip_and_mark(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    saved_path = save_description_applied_ids({"b", "a"}, settings)
    assert saved_path == tmp_path / "_config" / "description_applied_videos.json"
    assert load_description_applied_ids(settings) == {"a", "b"}

    mark_description_applied("vid1234567", settings)
    assert load_description_applied_ids(settings) == {"a", "b", "vid1234567"}


def test_clipboard_html_is_available_from_moved_component() -> None:
    html = build_clipboard_copy_html(
        text="0:00 はじめに",
        button_id="detail_copy",
        button_label="コピー",
    )
    assert "navigator.clipboard.writeText" in html
    assert "detail_copy" in html
    assert "コピーしました" in html


def test_purge_dialog_does_not_delete_before_confirmation() -> None:
    settings = MagicMock()
    with (
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.purge_source") as purge,
    ):
        video_detail._confirm_source_purge_dialog.__wrapped__(_video(), settings)
    purge.assert_not_called()


def test_purge_dialog_deletes_only_after_confirmation() -> None:
    settings = MagicMock()
    with (
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=True),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.success"),
        patch("yt_live_kit.ui.views.video_detail.st.rerun"),
        patch("yt_live_kit.ui.views.video_detail.purge_source", return_value=10) as purge,
    ):
        video_detail._confirm_source_purge_dialog.__wrapped__(_video(), settings)
    purge.assert_called_once_with("vid1234567", settings)


def test_regenerate_dialog_starts_job_only_after_confirmation() -> None:
    settings = MagicMock()
    with (
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=[False, True]),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail._start_regenerate") as start,
    ):
        video_detail._confirm_regenerate_dialog.__wrapped__(
            _video(), "chapters", settings
        )
        start.assert_not_called()
        video_detail._confirm_regenerate_dialog.__wrapped__(
            _video(), "chapters", settings
        )
    start.assert_called_once_with(_video(), "chapters", settings)


def test_regenerate_button_opens_dialog_without_starting_job() -> None:
    settings = MagicMock()
    video = _video(transcript=True)
    with (
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=True),
        patch(
            "yt_live_kit.ui.views.video_detail._confirm_regenerate_dialog"
        ) as dialog,
        patch("yt_live_kit.ui.views.video_detail._start_regenerate") as start,
    ):
        video_detail._render_regenerate_control(
            video,
            target="chapters",
            complete=False,
            busy=False,
            settings=settings,
        )

    dialog.assert_called_once_with(video, "chapters", settings)
    start.assert_not_called()


def test_next_step_cta_is_stretched_primary_and_returns_clicked_step() -> None:
    steps = (
        video_detail.ProgressStep(label="字幕", status="complete"),
        video_detail.ProgressStep(label="チャプター", status="next"),
        video_detail.ProgressStep(label="候補", status="pending"),
        video_detail.ProgressStep(label="ショート", status="pending"),
        video_detail.ProgressStep(label="概要欄", status="pending"),
    )

    @contextmanager
    def container(*_args: object, **_kwargs: object):
        yield

    with (
        patch("yt_live_kit.ui.views.video_detail.st.container", side_effect=container),
        patch("yt_live_kit.ui.views.video_detail.st.subheader"),
        patch("yt_live_kit.ui.views.video_detail.st.badge"),
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=True) as button,
    ):
        clicked = video_detail._render_stepper(steps)

    assert clicked == "チャプター"
    button.assert_called_once_with(
        "次にやる: チャプター",
        key="detail_next_step",
        type="primary",
        width="stretch",
    )


def test_render_detail_guides_when_selection_is_missing() -> None:
    with (
        patch("yt_live_kit.ui.views.video_detail.get_selected_video_id", return_value=None),
        patch("yt_live_kit.ui.views.video_detail.st.header"),
        patch("yt_live_kit.ui.views.video_detail.st.info") as info,
    ):
        video_detail.render_video_detail_page()
    info.assert_called_once_with("ライブラリから動画を選択してください。")


def test_render_detail_without_saved_result_shows_stepper_and_opens_run_page(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    video = _video(transcript=False)
    run_page = MagicMock()
    with (
        patch("yt_live_kit.ui.views.video_detail.get_selected_video_id", return_value=video.video_id),
        patch("yt_live_kit.ui.views.video_detail.get_settings", return_value=settings),
        patch("yt_live_kit.ui.views.video_detail.list_processed_videos", return_value=[video]),
        patch("yt_live_kit.ui.views.video_detail.load_result_from_disk", return_value=None),
        patch("yt_live_kit.ui.views.video_detail.count_shorts", return_value=0),
        patch("yt_live_kit.ui.views.video_detail.load_description_applied_ids", return_value=set()),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail._render_stepper", return_value="字幕") as stepper,
        patch("yt_live_kit.ui.views.video_detail.st.header"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown") as markdown,
        patch("yt_live_kit.ui.views.video_detail.st.caption"),
        patch("yt_live_kit.ui.views.video_detail.st.divider"),
        patch("yt_live_kit.ui.views.video_detail.st.warning") as warning,
        patch("yt_live_kit.ui.views.video_detail.st.switch_page") as switch_page,
    ):
        video_detail.render_video_detail_page(run_page=run_page)

    markdown.assert_called_once_with("**テスト動画**")
    rendered_steps = stepper.call_args.args[0]
    assert _statuses(rendered_steps) == [
        "next",
        "pending",
        "pending",
        "pending",
        "pending",
    ]
    switch_page.assert_called_once_with(run_page)
    assert "成果物" in warning.call_args.args[0]


def test_chapters_next_cta_opens_generation_dialog(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    video = _video(transcript=True)
    with (
        patch("yt_live_kit.ui.views.video_detail._confirm_regenerate_dialog") as dialog,
    ):
        expanded = video_detail._handle_next_action(
            "チャプター", video, settings, run_page=None
        )

    dialog.assert_called_once_with(video, "chapters", settings)
    assert expanded is False


def test_shorts_next_cta_requests_expanded_creation_ui(tmp_path: Path) -> None:
    assert video_detail._handle_next_action(
        "ショート",
        _video(transcript=True, chapters=True, clips=True),
        Settings(data_dir=tmp_path),
        run_page=None,
    ) is True


def test_render_detail_calls_pipeline_sections_from_saved_result(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    video = _video(transcript=True, chapters=True, clips=True)
    result = _result(tmp_path, chapters="0:00 はじめに")

    @contextmanager
    def expander(*_args: object, **_kwargs: object):
        yield

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "yt_live_kit.ui.views.video_detail.get_selected_video_id",
                return_value=video.video_id,
            )
        )
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.get_settings", return_value=settings)
        )
        stack.enter_context(
            patch(
                "yt_live_kit.ui.views.video_detail.list_processed_videos",
                return_value=[video],
            )
        )
        stack.enter_context(
            patch(
                "yt_live_kit.ui.views.video_detail.load_result_from_disk",
                return_value=result,
            )
        )
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.count_shorts", return_value=1)
        )
        stack.enter_context(
            patch(
                "yt_live_kit.ui.views.video_detail.load_description_applied_ids",
                return_value=set(),
            )
        )
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False)
        )
        stepper = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail._render_stepper")
        )
        transcript = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail._render_transcript")
        )
        chapters = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail._render_chapters")
        )
        clips = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail._render_clips")
        )
        highlights = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.render_highlights_section")
        )
        shorts = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.render_shorts_section")
        )
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.st.expander", side_effect=expander)
        )
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.st.button", return_value=False)
        )
        for command in (
            "header",
            "markdown",
            "caption",
            "divider",
            "subheader",
            "info",
        ):
            stack.enter_context(
                patch(f"yt_live_kit.ui.views.video_detail.st.{command}")
            )
        video_detail.render_video_detail_page()

    stepper.assert_called_once()
    transcript.assert_called_once_with(result)
    chapters.assert_called_once()
    clips.assert_called_once()
    highlights.assert_called_once_with(result)
    shorts.assert_called_once_with(result, expanded=False)
