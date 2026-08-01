"""動画詳細ページの状態計算・表示分岐・確認ダイアログのテスト."""

from __future__ import annotations

import ast
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.services.pipeline import PipelineResult
from yt_live_kit.services.youtube_api import YouTubeAPIError
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


def test_detail_summary_keeps_generated_and_reservable_counts_separate() -> None:
    summary = video_detail.calculate_detail_summary(
        clip_count=2,
        highlight_count=3,
        generated_short_count=4,
        reservable_short_count=1,
        description_applied=True,
    )
    assert summary == video_detail.DetailSummary(5, 4, 1, True)


def test_initial_workspace_prioritizes_running_job_for_current_video() -> None:
    from yt_live_kit.services.jobs import JobState

    active = JobState(
        job_id="job-1",
        kind="upload",
        status="running",
        video_id="vid1234567",
    )
    assert video_detail.choose_initial_workspace(
        video_id="vid1234567",
        candidate_count=0,
        reservable_short_count=0,
        active_job=active,
    ) == "publish"


def test_initial_workspace_ignores_job_for_other_video() -> None:
    from yt_live_kit.services.jobs import JobState

    active = JobState(
        job_id="job-1",
        kind="upload",
        status="running",
        video_id="other-video",
    )
    assert video_detail.choose_initial_workspace(
        video_id="vid1234567",
        candidate_count=0,
        reservable_short_count=0,
        active_job=active,
    ) == "materials"


def test_initial_workspace_follows_candidate_and_reservable_priority() -> None:
    assert video_detail.choose_initial_workspace(
        video_id="v", candidate_count=0, reservable_short_count=2
    ) == "materials"
    assert video_detail.choose_initial_workspace(
        video_id="v", candidate_count=2, reservable_short_count=0
    ) == "shorts"
    assert video_detail.choose_initial_workspace(
        video_id="v", candidate_count=2, reservable_short_count=1
    ) == "publish"


def test_candidate_transfer_preserves_order_and_invalidates_on_change() -> None:
    from yt_live_kit.models.clips import ClipCandidate

    first = ClipCandidate(
        id="clip-1",
        title="1",
        start="00:00:00",
        end="00:01:00",
        duration_sec=60,
        reason="理由",
    )
    second = first.model_copy(update={"id": "clip-2", "title": "2"})
    fingerprint = video_detail.make_candidate_fingerprint("clips", [first, second])
    transfer = video_detail.CandidateTransfer(
        "clips", ("clip-2", "clip-1"), fingerprint
    )
    assert video_detail.validate_candidate_transfer(
        transfer,
        current_fingerprint=fingerprint,
        candidate_ids={"clip-1", "clip-2"},
    ) == transfer
    changed = video_detail.make_candidate_fingerprint("clips", [second, first])
    assert video_detail.validate_candidate_transfer(
        transfer,
        current_fingerprint=changed,
        candidate_ids={"clip-1", "clip-2"},
    ) is None


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


def test_render_detail_guides_when_selection_is_missing() -> None:
    with (
        patch("yt_live_kit.ui.views.video_detail.get_selected_video_id", return_value=None),
        patch("yt_live_kit.ui.views.video_detail.st.header"),
        patch("yt_live_kit.ui.views.video_detail.st.info") as info,
    ):
        video_detail.render_video_detail_page()
    info.assert_called_once_with("ライブラリから動画を選択してください。")


def test_render_detail_without_saved_result_shows_only_recovery_state(
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
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail._render_recovery_state") as recovery,
        patch("yt_live_kit.ui.views.video_detail._render_materials_workspace") as materials,
        patch("yt_live_kit.ui.views.video_detail.st.header"),
        patch("yt_live_kit.ui.views.video_detail.st.caption"),
    ):
        video_detail.render_video_detail_page(run_page=run_page)

    recovery.assert_called_once_with(video.video_id, settings, run_page=run_page)
    materials.assert_not_called()


def test_render_detail_draws_only_selected_workspace(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    video = _video(transcript=True, chapters=True, clips=True)
    result = _result(tmp_path, chapters="0:00 はじめに")

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
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail.count_shorts", return_value=1))
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail.count_reservable_shorts", return_value=0))
        stack.enter_context(
            patch(
                "yt_live_kit.ui.views.video_detail.load_description_applied_ids",
                return_value=set(),
            )
        )
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False)
        )
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail.get_active_job", return_value=None))
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail._load_material_candidates", return_value=([], [])))
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail._render_state_summary"))
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail.render_main_line_summary"))
        line = stack.enter_context(patch("yt_live_kit.ui.views.video_detail.render_shorts_line"))
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail.st.segmented_control", return_value="shorts"))
        materials = stack.enter_context(patch("yt_live_kit.ui.views.video_detail._render_materials_workspace"))
        shorts = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.render_shorts_section")
        )
        publish = stack.enter_context(patch("yt_live_kit.ui.views.video_detail._render_publish_workspace"))
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail._render_details_and_regeneration"))
        for command in (
            "header",
            "caption",
            "subheader",
            "info",
        ):
            stack.enter_context(
                patch(f"yt_live_kit.ui.views.video_detail.st.{command}")
            )
        video_detail.render_video_detail_page()

    materials.assert_not_called()
    line.assert_called_once()
    shorts.assert_called_once_with(result, expanded=False)
    publish.assert_not_called()


_VALID_CHAPTERS = "0:00 はじめに\n0:10 本題\n0:20 まとめ"


def _dialog_contexts() -> tuple[object, object]:
    return nullcontext(), nullcontext()


def _dialog_button(label: str, **_kwargs: object) -> bool:
    return label == "この内容を概要欄に反映"


def test_description_outer_button_not_clicked_calls_no_api_or_mark() -> None:
    with (
        patch("yt_live_kit.ui.views.video_detail.st.warning") as warning,
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=False) as button,
        patch("yt_live_kit.ui.views.video_detail._start_description_preview") as start,
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied") as mark,
    ):
        video_detail._render_description_control(
            _video(chapters=True),
            _VALID_CHAPTERS,
            MagicMock(),
            busy=False,
        )

    assert "公開データを書き換え" in warning.call_args.args[0]
    assert button.call_args.args[0] == "概要欄に反映"
    assert button.call_args.kwargs["type"] == "primary"
    start.assert_not_called()
    fetch.assert_not_called()
    update.assert_not_called()
    mark.assert_not_called()


def test_preview_validates_then_fetches_and_merges_without_updating() -> None:
    video = _video(chapters=True)
    settings = MagicMock()
    snippet = {"description": "現在の説明"}

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True) as configured,
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet", return_value=snippet) as fetch,
        patch(
            "yt_live_kit.ui.views.video_detail.merge_chapters_into_description",
            return_value="反映後",
        ) as merge,
        patch("yt_live_kit.ui.views.video_detail._description_preview_dialog") as dialog,
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied") as mark,
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)

    configured.assert_called_once_with(settings)
    fetch.assert_called_once_with(video.video_id, settings)
    merge.assert_called_once_with("現在の説明", _VALID_CHAPTERS)
    dialog.assert_called_once_with(video, "現在の説明", "反映後", settings)
    update.assert_not_called()
    mark.assert_not_called()


def test_description_dialog_is_large_and_uses_read_only_before_after() -> None:
    source = Path(video_detail.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_description_preview_dialog"
    )
    decorator = next(
        item
        for item in function.decorator_list
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "dialog"
    )
    width = next(keyword for keyword in decorator.keywords if keyword.arg == "width")
    assert isinstance(width.value, ast.Constant)
    assert width.value.value == "large"

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown") as markdown,
        patch("yt_live_kit.ui.views.video_detail.st.text_area") as text_area,
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied") as mark,
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "更新前テキスト", "更新後テキスト", MagicMock()
        )

    assert call("**更新前**") in markdown.call_args_list
    assert call("**更新後**") in markdown.call_args_list
    assert text_area.call_args_list[0].kwargs["value"] == "更新前テキスト"
    assert text_area.call_args_list[1].kwargs["value"] == "更新後テキスト"
    assert all(item.kwargs["disabled"] is True for item in text_area.call_args_list)
    fetch.assert_not_called()
    update.assert_not_called()
    mark.assert_not_called()


def test_description_dialog_cancel_is_safe() -> None:
    def cancel_button(label: str, **_kwargs: object) -> bool:
        return label == "キャンセル"

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=cancel_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied") as mark,
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", MagicMock()
        )

    rerun.assert_called_once_with()
    update.assert_not_called()
    mark.assert_not_called()


def test_description_confirm_calls_update_then_mark_once_without_preview_refetch() -> None:
    events: list[str] = []
    session_state: dict[str, object] = {}
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=_dialog_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
        patch(
            "yt_live_kit.ui.views.video_detail.update_video_description",
            side_effect=lambda *_args: events.append("update"),
        ) as update,
        patch(
            "yt_live_kit.ui.views.video_detail.mark_description_applied",
            side_effect=lambda *_args: events.append("mark"),
        ) as mark,
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", settings
        )

    assert events == ["update", "mark"]
    update.assert_called_once_with("vid1234567", "後", settings)
    mark.assert_called_once_with("vid1234567", settings)
    fetch.assert_not_called()
    assert video_detail._DESCRIPTION_UPDATED_IDS_KEY not in session_state
    assert "更新しました" in session_state[video_detail._DESCRIPTION_SUCCESS_KEY]
    rerun.assert_called_once_with()


def test_description_update_failure_does_not_mark() -> None:
    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=_dialog_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch(
            "yt_live_kit.ui.views.video_detail.update_video_description",
            side_effect=YouTubeAPIError("API エラー"),
        ),
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied") as mark,
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", MagicMock()
        )

    assert "更新できませんでした" in error.call_args.args[0]
    mark.assert_not_called()


def test_description_mark_failure_offers_retry_without_automatic_second_update() -> None:
    session_state: dict[str, object] = {}

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning") as warning,
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=_dialog_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch(
            "yt_live_kit.ui.views.video_detail.mark_description_applied",
            side_effect=OSError("permission denied"),
        ),
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", MagicMock()
        )
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", MagicMock()
        )

    update.assert_called_once()
    assert "YouTube 側は更新済み" in warning.call_args.args[0]
    assert any(
        "完了状態を保存できませんでした" in item.args[0]
        for item in warning.call_args_list
    )
    assert "YouTube は再更新せず" in warning.call_args.args[0]
    assert session_state[video_detail._DESCRIPTION_UPDATED_IDS_KEY] == {"vid1234567"}


def test_mark_failure_retry_calls_mark_only_then_completes() -> None:
    session_state: dict[str, object] = {}
    settings = MagicMock()

    def confirm_button(label: str, **_kwargs: object) -> bool:
        return label in {
            "この内容を概要欄に反映",
            "完了状態の保存を再試行",
        }

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=confirm_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.st.rerun") as rerun,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch(
            "yt_live_kit.ui.views.video_detail.mark_description_applied",
            side_effect=[OSError("first failure"), None],
        ) as mark,
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", settings
        )
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", settings
        )

    update.assert_called_once_with("vid1234567", "後", settings)
    assert mark.call_count == 2
    assert video_detail._DESCRIPTION_UPDATED_IDS_KEY not in session_state
    assert "更新しました" in session_state[video_detail._DESCRIPTION_SUCCESS_KEY]
    rerun.assert_called_once_with()


def test_guard_retry_common_flow_does_not_block_another_video() -> None:
    guarded = _video(chapters=True)
    another = ProcessedVideo(
        video_id="another-video",
        title="別動画",
        fetched_at=None,
        has_chapters=True,
        has_transcript=True,
        has_clips=True,
    )
    settings = MagicMock()
    session_state: dict[str, object] = {
        video_detail._DESCRIPTION_UPDATED_IDS_KEY: {guarded.video_id},
    }

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch(
            "yt_live_kit.ui.views.video_detail.fetch_video_snippet",
            return_value={"description": "前"},
        ) as fetch,
        patch(
            "yt_live_kit.ui.views.video_detail.merge_chapters_into_description",
            return_value="後",
        ),
        patch("yt_live_kit.ui.views.video_detail._description_preview_dialog") as dialog,
    ):
        video_detail._start_description_preview(guarded, _VALID_CHAPTERS, settings)
        video_detail._start_description_preview(another, _VALID_CHAPTERS, settings)

    assert dialog.call_args_list == [
        call(guarded, "", "", settings),
        call(another, "前", "後", settings),
    ]
    fetch.assert_called_once_with(another.video_id, settings)


def test_restart_equal_preview_skips_update_and_saves_completion_only() -> None:
    video = _video(chapters=True)
    settings = MagicMock()
    session_state: dict[str, object] = {}

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch(
            "yt_live_kit.ui.views.video_detail.fetch_video_snippet",
            return_value={"description": "反映済み"},
        ),
        patch(
            "yt_live_kit.ui.views.video_detail.merge_chapters_into_description",
            return_value="反映済み",
        ),
        patch("yt_live_kit.ui.views.video_detail._description_preview_dialog") as dialog,
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)
    dialog.assert_called_once_with(video, "反映済み", "反映済み", settings)

    def retry_button(label: str, **_kwargs: object) -> bool:
        return label == "完了状態の保存を再試行"

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.info") as info,
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=retry_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.st.rerun"),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied") as mark,
    ):
        video_detail._description_preview_dialog.__wrapped__(
            video, "反映済み", "反映済み", settings
        )

    assert "YouTube は更新せず" in info.call_args.args[0]
    update.assert_not_called()
    mark.assert_called_once_with(video.video_id, settings)


def test_busy_description_preview_blocks_before_fetch() -> None:
    video = _video(chapters=True)
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=True),
        patch("yt_live_kit.ui.views.video_detail.st.info") as info,
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=True),
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
        patch("yt_live_kit.ui.views.video_detail._description_preview_dialog") as dialog,
    ):
        video_detail._render_description_control(
            video, _VALID_CHAPTERS, settings, busy=False
        )

    assert info.call_count == 1
    assert video_detail._BUSY_MESSAGE in info.call_args.args[0]
    fetch.assert_not_called()
    dialog.assert_not_called()


def test_user_facing_validator_and_external_errors_sanitize_angle_brackets() -> None:
    video = _video(chapters=True)
    settings = MagicMock()
    unsafe_chapters = "0:00 <危険>\n0:10 本題\n0:20 まとめ"

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
    ):
        video_detail._start_description_preview(video, unsafe_chapters, settings)
    validator_message = error.call_args.args[0]
    assert "〈危険〉" in validator_message
    assert "<" not in validator_message and ">" not in validator_message

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch(
            "yt_live_kit.ui.views.video_detail.fetch_video_snippet",
            side_effect=YouTubeAPIError("認証 <token> エラー"),
        ),
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)
    api_message = error.call_args.args[0]
    assert "〈token〉" in api_message
    assert "<" not in api_message and ">" not in api_message

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch(
            "yt_live_kit.ui.views.video_detail.is_configured",
            side_effect=OSError("設定 <secret> を読めません"),
        ),
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)
    auth_message = error.call_args.args[0]
    assert "〈secret〉" in auth_message
    assert "<" not in auth_message and ">" not in auth_message


def test_dialog_sanitizes_display_but_preserves_update_payload() -> None:
    settings = MagicMock()
    session_state: dict[str, object] = {}

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area") as text_area,
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=_dialog_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.st.rerun"),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description") as update,
        patch("yt_live_kit.ui.views.video_detail.mark_description_applied"),
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前 <old>", "後 <new>", settings
        )

    assert text_area.call_args_list[0].kwargs["value"] == "前 〈old〉"
    assert text_area.call_args_list[1].kwargs["value"] == "後 〈new〉"
    update.assert_called_once_with("vid1234567", "後 <new>", settings)


def test_update_and_mark_exception_details_are_sanitized() -> None:
    def confirm_button(label: str, **_kwargs: object) -> bool:
        return label in {
            "この内容を概要欄に反映",
            "完了状態の保存を再試行",
        }

    common_patches = (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=confirm_button),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
    )
    with ExitStack() as stack:
        for item in common_patches:
            stack.enter_context(item)
        stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.st.session_state", {})
        )
        stack.enter_context(patch("yt_live_kit.ui.views.video_detail.st.warning"))
        error = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.st.error")
        )
        stack.enter_context(
            patch(
                "yt_live_kit.ui.views.video_detail.update_video_description",
                side_effect=OSError("更新 <auth> 失敗"),
            )
        )
        mark = stack.enter_context(
            patch("yt_live_kit.ui.views.video_detail.mark_description_applied")
        )
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", MagicMock()
        )
    update_message = error.call_args.args[0]
    assert "〈auth〉" in update_message
    assert "<" not in update_message and ">" not in update_message
    mark.assert_not_called()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=confirm_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.warning") as warning,
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description"),
        patch(
            "yt_live_kit.ui.views.video_detail.mark_description_applied",
            side_effect=OSError("保存 <path> 失敗"),
        ),
    ):
        video_detail._description_preview_dialog.__wrapped__(
            _video(), "前", "後", MagicMock()
        )
    mark_message = warning.call_args.args[0]
    assert "〈path〉" in mark_message
    assert "<" not in mark_message and ">" not in mark_message


def test_preview_rejects_oauth_missing_chapters_and_invalid_format() -> None:
    video = _video()
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)
    assert "OAuth" in error.call_args.args[0]
    fetch.assert_not_called()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
    ):
        video_detail._start_description_preview(video, "", settings)
    assert "チャプターがありません" in error.call_args.args[0]
    fetch.assert_not_called()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch("yt_live_kit.ui.views.video_detail.fetch_video_snippet") as fetch,
    ):
        video_detail._start_description_preview(video, "0:05 不正", settings)
    assert "形式が不正" in error.call_args.args[0]
    assert "0:00" in error.call_args.args[0]
    fetch.assert_not_called()


def test_preview_shows_japanese_errors_for_limit_and_snippet_failure() -> None:
    video = _video(chapters=True)
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch(
            "yt_live_kit.ui.views.video_detail.fetch_video_snippet",
            return_value={"description": "あ" * 5001},
        ),
        patch("yt_live_kit.ui.views.video_detail._description_preview_dialog") as dialog,
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)
    assert "5000 文字" in error.call_args.args[0]
    dialog.assert_not_called()

    with (
        patch("yt_live_kit.ui.views.video_detail.st.session_state", {}),
        patch("yt_live_kit.ui.views.video_detail.st.error") as error,
        patch("yt_live_kit.ui.views.video_detail.is_configured", return_value=True),
        patch(
            "yt_live_kit.ui.views.video_detail.fetch_video_snippet",
            side_effect=YouTubeAPIError("動画が見つかりません"),
        ),
        patch("yt_live_kit.ui.views.video_detail.merge_chapters_into_description") as merge,
    ):
        video_detail._start_description_preview(video, _VALID_CHAPTERS, settings)
    assert "動画が見つかりません" in error.call_args.args[0]
    merge.assert_not_called()


def test_description_button_uses_common_preview_flow(tmp_path: Path) -> None:
    video = _video(chapters=True)
    settings = Settings(data_dir=tmp_path)

    with (
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.button", return_value=True),
        patch("yt_live_kit.ui.views.video_detail._start_description_preview") as start,
    ):
        video_detail._render_description_control(
            video, _VALID_CHAPTERS, settings, busy=False
        )

    start.assert_called_once_with(video, _VALID_CHAPTERS, settings)


def test_successful_description_mark_updates_summary_input(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    video = _video(transcript=True, chapters=True, clips=True)
    session_state: dict[str, object] = {}

    with (
        patch("yt_live_kit.ui.views.video_detail.st.columns", return_value=_dialog_contexts()),
        patch("yt_live_kit.ui.views.video_detail.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.views.video_detail.st.warning"),
        patch("yt_live_kit.ui.views.video_detail.st.markdown"),
        patch("yt_live_kit.ui.views.video_detail.st.text_area"),
        patch("yt_live_kit.ui.views.video_detail.st.button", side_effect=_dialog_button),
        patch("yt_live_kit.ui.views.video_detail.st.session_state", session_state),
        patch("yt_live_kit.ui.views.video_detail.st.rerun"),
        patch("yt_live_kit.ui.views.video_detail.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.video_detail.update_video_description"),
    ):
        video_detail._description_preview_dialog.__wrapped__(
            video, "前", "後", settings
        )

    applied_ids = load_description_applied_ids(settings)
    summary = video_detail.calculate_detail_summary(
        clip_count=1,
        highlight_count=0,
        generated_short_count=1,
        reservable_short_count=1,
        description_applied=video.video_id in applied_ids,
    )
    assert summary.description_applied is True
