"""ショート生産ライン UI の表示状態と安全接続のテスト."""

from __future__ import annotations

from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import stat
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.ffmpeg import FfmpegError
from yt_live_kit.services.shorts_line import (
    DailyLineSummary,
    LineStage,
    LineStateError,
    confirm_preview,
    confirm_review,
    create_line_state,
    load_line_state,
    make_review_fingerprint,
    record_output,
    save_active_line,
    save_line_state,
    set_generation_spec,
    set_review_fingerprint,
)
from yt_live_kit.services.shorts_queue import (
    ShortsQueueItemResult,
    build_shorts_queue_targets,
    make_shorts_queue_clip_spec,
    make_shorts_queue_fingerprint,
    normalize_queue_candidates,
)
from yt_live_kit.ui.components import shorts_line, shorts_queue as shorts_queue_ui
from yt_live_kit.ui.views._local_settings import (
    ShortsLineDefaults,
    load_shorts_line_defaults,
    save_shorts_line_defaults,
)


def _reviewed_output_state(output_path: Path, settings: Settings):
    review = "b" * 64
    state = create_line_state("video-1", "clip-1", "a" * 64, review_fingerprint=review)
    state = confirm_review(state, review)
    state = set_generation_spec(
        state,
        review,
        {"target_id": "clip-1", "layout": "blur", "preset": "default"},
    )
    state = record_output(state, output_path)
    state = confirm_preview(state, output_path)
    save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    return state


def _lineage_fixture() -> tuple[object, TelopScriptDocument, object]:
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    target = build_shorts_queue_targets(
        normalize_queue_candidates((candidate,), source="clips"),
        mode="concat",
    )[0]
    segment = target.segments[0]
    document = TelopScriptDocument.model_validate(
        {
            "hook_text": "重要ポイント",
            "title_candidates": ["タイトル"],
            "description": "説明",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": segment.start_ms / 1000,
                    "end_sec": segment.end_ms / 1000,
                    "lines": [
                        {
                            "text": "本文",
                            "start_sec": segment.start_ms / 1000,
                            "end_sec": segment.end_ms / 1000,
                            "emphasis": False,
                        }
                    ],
                }
            ],
        }
    )
    spec = make_shorts_queue_clip_spec(
        target,
        document,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    return target, document, spec


@pytest.mark.parametrize(
    ("output", "running", "source", "expected"),
    [
        (False, False, True, "source"),
        (False, True, True, "generating"),
        (True, False, False, "output"),
        (True, True, False, "generating"),
        (False, False, False, "source_missing"),
    ],
)
def test_choose_preview_mode_has_four_explicit_states(
    output: bool,
    running: bool,
    source: bool,
    expected: str,
) -> None:
    assert shorts_line.choose_preview_mode(
        output_available=output,
        generation_running=running,
        source_available=source,
    ) == expected


def test_stage_bar_renders_six_stages_and_current_marker() -> None:
    @contextmanager
    def container(*_args: object, **_kwargs: object):
        yield

    with (
        patch("yt_live_kit.ui.components.shorts_line.st.container", side_effect=container),
        patch("yt_live_kit.ui.components.shorts_line.st.badge") as badge,
    ):
        shorts_line.render_stage_bar(LineStage.TELOP_REVIEW)

    assert badge.call_count == 6
    labels = [call.args[0] for call in badge.call_args_list]
    assert labels[0].endswith("完了")
    assert "3. テロップ確認・進行中" in labels
    assert labels[-1].endswith("待機中")


def test_human_confirmation_does_not_return_after_edit_and_revert() -> None:
    original = "b" * 64
    changed = "c" * 64
    state = create_line_state("video-1", "clip-1", "a" * 64, review_fingerprint=original)
    state = confirm_review(state, original)
    assert shorts_line.is_human_review_current(state, original) is True

    state = set_review_fingerprint(state, changed)
    assert shorts_line.is_human_review_current(state, changed) is False
    state = set_review_fingerprint(state, original)
    assert shorts_line.is_human_review_current(state, original) is False


def test_manifest_output_is_rejected_when_review_lineage_changed(tmp_path: Path) -> None:
    target, document_a, spec_a = _lineage_fixture()
    output = tmp_path / target.output_name
    output.write_bytes(b"old-output")
    queue_fingerprint = "a" * 64
    review_a = make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document_a
    )
    state = create_line_state(
        "video-1", target.target_id, queue_fingerprint, review_fingerprint=review_a
    )
    state = confirm_review(state, review_a)
    state = set_generation_spec(state, review_a, spec_a.to_dict())
    item = ShortsQueueItemResult(
        target_id=target.target_id,
        status="succeeded",
        output_path=output,
        log_path=None,
        font_warning=None,
        title_candidates=("タイトル",),
        description="説明",
        tags=("タグ",),
        error=None,
    )
    result = MagicMock(clip_specs=(spec_a,), items=(item,))

    with patch.object(shorts_line, "load_latest_shorts_queue_result", return_value=result):
        assert shorts_line._find_output(state, Settings(data_dir=tmp_path)) == (
            output,
            spec_a,
        )
        edited = document_a.model_copy(update={"hook_text": "変更後"})
        state_b = set_review_fingerprint(
            state,
            make_review_fingerprint(
                "video-1", target.target_id, queue_fingerprint, edited
            ),
        )
        assert shorts_line._find_output(state_b, Settings(data_dir=tmp_path)) == (
            None,
            None,
        )
        spec_b = make_shorts_queue_clip_spec(
            target,
            edited,
            layout="blur",
            preset="default",
            hook_preset="hook",
        )
        state_b = confirm_review(state_b, state_b.review_fingerprint or "")
        state_b = set_generation_spec(
            state_b,
            state_b.review_fingerprint or "",
            spec_b.to_dict(),
        )
        assert shorts_line._spec_matches_lineage(spec_a, state_b) is False
        assert shorts_line._spec_matches_lineage(spec_b, state_b) is True


def test_failed_manifest_without_output_never_advances_to_final_review(
    tmp_path: Path,
) -> None:
    target, document, spec = _lineage_fixture()
    queue_fingerprint = "a" * 64
    review = make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document
    )
    state = create_line_state(
        "video-1", target.target_id, queue_fingerprint, review_fingerprint=review
    )
    state = confirm_review(state, review)
    state = set_generation_spec(state, review, spec.to_dict())
    failed = ShortsQueueItemResult(
        target_id=target.target_id,
        status="failed",
        output_path=None,
        log_path=None,
        font_warning=None,
        title_candidates=("タイトル",),
        description="説明",
        tags=("タグ",),
        error="生成に失敗しました。",
    )
    result = MagicMock(clip_specs=(spec,), items=(failed,))

    with patch.object(shorts_line, "load_latest_shorts_queue_result", return_value=result):
        assert shorts_line._find_output(state, Settings(data_dir=tmp_path)) == (
            None,
            spec,
        )
    assert state.current_stage == LineStage.GENERATION
    assert state.output_fingerprint is None
    assert state.preview_confirmed_fingerprint is None


def test_manifest_output_is_rejected_after_layout_queue_change(tmp_path: Path) -> None:
    target, document, blur_spec = _lineage_fixture()
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    crop_queue = make_shorts_queue_fingerprint(
        video_id="video-1",
        source="clips",
        mode="concat",
        original_candidates=(candidate,),
        segments=target.segments,
        layout="crop",
        preset="default",
        hook_preset="hook",
    )
    crop_spec = make_shorts_queue_clip_spec(
        target,
        document,
        layout="crop",
        preset="default",
        hook_preset="hook",
    )
    review = make_review_fingerprint(
        "video-1", target.target_id, crop_queue, document
    )
    state = create_line_state(
        "video-1", target.target_id, crop_queue, review_fingerprint=review
    )
    state = confirm_review(state, review)
    state = set_generation_spec(state, review, crop_spec.to_dict())
    output = tmp_path / target.output_name
    output.write_bytes(b"old-blur-output")
    item = ShortsQueueItemResult(
        target_id=target.target_id,
        status="succeeded",
        output_path=output,
        log_path=None,
        font_warning=None,
        title_candidates=("タイトル",),
        description="説明",
        tags=("タグ",),
        error=None,
    )
    result = MagicMock(clip_specs=(blur_spec,), items=(item,))
    with patch.object(shorts_line, "load_latest_shorts_queue_result", return_value=result):
        assert shorts_line._find_output(state, Settings(data_dir=tmp_path)) == (
            None,
            None,
        )


def test_stale_context_spec_is_cleared_after_review_change() -> None:
    target, document, spec = _lineage_fixture()
    queue_fingerprint = "a" * 64
    review = make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document
    )
    state = create_line_state(
        "video-1", target.target_id, queue_fingerprint, review_fingerprint=review
    )
    changed = set_review_fingerprint(state, "b" * 64)
    context: dict[str, object] = {"confirmed_spec": spec}
    with (
        patch.object(shorts_line, "clear_line_confirmed_spec") as clear,
        patch.object(shorts_line, "_save_context") as save_context,
    ):
        assert shorts_line._current_context_spec("video-1", context, changed) is None
    assert "confirmed_spec" not in context
    clear.assert_called_once_with("video-1", target.target_id)
    save_context.assert_called_once_with("video-1", context)


def test_ordered_candidate_keys_preserve_source_order_and_colliding_ids() -> None:
    clip = ClipCandidate(
        id="same",
        title="切り抜き",
        start="0:00:00",
        end="0:04:00",
        duration_sec=240,
        reason="理由",
    )
    highlight = HighlightSegment(
        id="same",
        title="ハイライト",
        start="0:01:00",
        end="0:01:20",
        duration_sec=20,
        reason="理由",
    )
    assert shorts_line.ordered_candidate_keys(
        (clip,),
        (highlight,),
        (("highlights", "same"), ("clips", "same")),
    ) == (("highlights", "same"), ("clips", "same"))


def test_mixed_handoff_preselects_order_and_does_not_force_long_cut(
    tmp_path: Path,
) -> None:
    clip = ClipCandidate(
        id="same",
        title="長い切り抜き",
        start="0:01:00",
        end="0:05:00",
        duration_sec=240,
        reason="理由",
    )
    highlight = HighlightSegment(
        id="same",
        title="短いハイライト",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    session_state: dict[str, object] = {}
    with (
        patch.object(shorts_line, "resolve_active_line", return_value=None),
        patch.object(shorts_line, "render_stage_bar"),
        patch.object(shorts_line, "render_short_cut_section") as cut,
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "subheader"),
        patch.object(
            shorts_line.st,
            "multiselect",
            return_value=[("highlights", "same"), ("clips", "same")],
        ) as multiselect,
        patch.object(
            shorts_line.st, "radio", return_value=("highlights", "same")
        ),
        patch.object(shorts_line.st, "caption"),
        patch.object(shorts_line.st, "write"),
        patch.object(shorts_line.st, "button", return_value=False),
    ):
        shorts_line.render_shorts_line(
            video_id="video-1",
            title="動画",
            clip_candidates=(clip,),
            highlight_candidates=(highlight,),
            settings=Settings(data_dir=tmp_path),
            preferred_candidate_keys=(
                ("highlights", "same"),
                ("clips", "same"),
            ),
        )

    assert multiselect.call_args.args[1] == (
        ("highlights", "same"),
        ("clips", "same"),
    )
    assert multiselect.call_args.kwargs["default"] == [
        ("highlights", "same"),
        ("clips", "same"),
    ]
    cut.assert_not_called()


def test_recovery_actions_offer_retry_and_confirmed_abandon(tmp_path: Path) -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64)

    @contextmanager
    def horizontal(*_args: object, **_kwargs: object):
        yield

    with (
        patch.object(shorts_line.st, "container", side_effect=horizontal),
        patch.object(shorts_line.st, "button", side_effect=[False, False]) as button,
    ):
        shorts_line._render_line_recovery_actions(
            state,
            Settings(data_dir=tmp_path),
            retry=lambda: None,
            retry_label="保存状態を再読み込み",
        )

    assert [call.args[0] for call in button.call_args_list] == [
        "保存状態を再読み込み",
        "ラインを終了して素材選定へ戻る",
    ]


def test_validate_line_reservation_invalidates_changed_output(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    output_path = tmp_path / "video-1" / "shorts" / "output" / "short_clip-1.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"first")
    _reviewed_output_state(output_path, settings)

    output_path.write_bytes(b"changed")
    with pytest.raises(LineStateError, match="もう一度プレビュー"):
        shorts_line.validate_line_reservation(
            "video-1", "clip-1", output_path, settings
        )

    persisted = load_line_state("video-1", "clip-1", settings)
    assert persisted is not None
    assert persisted.current_stage == LineStage.FINAL_REVIEW
    assert persisted.preview_confirmed_fingerprint is None


def test_upload_callback_invalidates_if_output_changes_after_preview(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    output_path = tmp_path / "video-1" / "shorts" / "output" / "short_clip-1.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"first")
    _reviewed_output_state(output_path, settings)
    output_path.write_bytes(b"changed-after-preview")

    with pytest.raises(LineStateError, match="もう一度プレビュー"):
        shorts_line.record_line_upload(
            "video-1", "clip-1", "operation-1", output_path, settings
        )
    persisted = load_line_state("video-1", "clip-1", settings)
    assert persisted is not None
    assert persisted.current_stage == LineStage.FINAL_REVIEW
    assert persisted.upload_operation_id is None


def test_publish_workspace_callback_preserves_confirmed_line_without_operation(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    output_path = tmp_path / "video-1" / "shorts" / "output" / "short_clip-1.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"confirmed")
    before = _reviewed_output_state(output_path, settings)
    session_state: dict[str, object] = {}

    with patch.object(shorts_line.st, "session_state", session_state):
        shorts_line._switch_to_publish_workspace("video-1")

    assert session_state == {"detail_workspace_video-1": "publish"}
    assert load_line_state("video-1", "clip-1", settings) == before
    assert not (tmp_path / "_schedule" / "queue.json").exists()
    render_source = inspect.getsource(shorts_line.render_shorts_line)
    assert "on_click=_switch_to_publish_workspace" in render_source
    assert "detail_workspace_" not in render_source


def test_publish_callbacks_bypass_non_line_item_and_do_not_require_active_line(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    non_line = tmp_path / "non-line.mp4"
    non_line.write_bytes(b"normal-s4")
    shorts_line.validate_line_reservation("video-1", "normal-s4", non_line, settings)
    shorts_line.record_line_upload(
        "video-1", "normal-s4", "operation-normal", non_line, settings
    )

    line_output = tmp_path / "line.mp4"
    line_output.write_bytes(b"line")
    line_state = _reviewed_output_state(line_output, settings)
    other = create_line_state("video-1", "other", "c" * 64)
    save_line_state(other, settings)
    save_active_line("video-1", "other", settings)
    shorts_line.validate_line_reservation(
        "video-1", line_state.clip_id, line_output, settings
    )
    shorts_line.record_line_upload(
        "video-1", line_state.clip_id, "operation-line", line_output, settings
    )
    persisted = load_line_state("video-1", line_state.clip_id, settings)
    assert persisted is not None
    assert persisted.upload_operation_id == "operation-line"


def test_sidebar_is_display_only_and_shows_daily_progress(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    daily = DailyLineSummary(completed_count=1, needs_attention_count=2)
    with (
        patch("yt_live_kit.ui.components.shorts_line.resolve_active_line", return_value=None),
        patch("yt_live_kit.ui.components.shorts_line.load_daily_line_summary", return_value=daily),
        patch("yt_live_kit.ui.components.shorts_line.st.divider"),
        patch("yt_live_kit.ui.components.shorts_line.st.markdown") as markdown,
        patch("yt_live_kit.ui.components.shorts_line.st.caption") as caption,
        patch("yt_live_kit.ui.components.shorts_line.st.write") as write,
        patch("yt_live_kit.ui.components.shorts_line.st.button") as button,
    ):
        shorts_line.render_sidebar_line_context("video-1", settings)

    markdown.assert_called_once_with("**作成中のショート**")
    assert any("作成中のラインはありません" in call.args[0] for call in caption.call_args_list)
    assert any("本日のライン完了 1／3" in call.args[0] for call in write.call_args_list)
    button.assert_not_called()


def test_sidebar_labels_target_stage_next_and_attention() -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64)
    daily = DailyLineSummary(completed_count=1, needs_attention_count=2)
    with (
        patch.object(shorts_line.st, "markdown"),
        patch.object(shorts_line.st, "caption") as caption,
        patch.object(shorts_line.st, "write") as write,
    ):
        shorts_line.render_compact_line_status(
            state,
            daily,
            title="重要ポイント",
        )

    captions = [call.args[0] for call in caption.call_args_list]
    writes = [call.args[0] for call in write.call_args_list]
    assert "対象ショート: 重要ポイント" in captions
    assert any(value.startswith("次: ") for value in captions)
    assert "要対応 2 件" in captions
    assert any(
        value.startswith(
            f"工程 {shorts_line.stage_number(state.current_stage)}／6"
        )
        for value in writes
    )
    assert "本日のライン完了 1／3" in writes


def test_sidebar_generation_preview_has_no_duplicate_location_guidance(
    tmp_path: Path,
) -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64).model_copy(
        update={"current_stage": LineStage.GENERATION}
    )
    active_job = MagicMock(
        status="running",
        video_id="video-1",
        kind="shorts_queue",
    )
    with (
        patch.object(shorts_line, "resolve_active_line", return_value=state),
        patch.object(shorts_line, "_context", return_value={}),
        patch.object(shorts_line, "_find_output", return_value=(None, None)),
        patch.object(shorts_line, "get_active_job", return_value=active_job),
        patch.object(shorts_line, "render_compact_line_status"),
        patch.object(shorts_line, "load_daily_line_summary", return_value=None),
        patch.object(shorts_line.st, "divider"),
        patch.object(shorts_line.st, "info") as info,
    ):
        shorts_line.render_sidebar_line_context(
            "video-1",
            Settings(data_dir=tmp_path),
        )

    info.assert_called_once_with("ショートを生成中です。")
    assert "進捗は画面上部で確認できます" not in info.call_args.args[0]


def test_main_line_summary_is_only_collapsed_stage_status(tmp_path: Path) -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64).model_copy(
        update={"current_stage": LineStage.GENERATION}
    )
    active_job = MagicMock(
        status="running",
        video_id="video-1",
        kind="shorts_queue",
    )
    with (
        patch.object(shorts_line, "resolve_active_line", return_value=state),
        patch.object(shorts_line, "get_active_job", return_value=active_job),
        patch.object(shorts_line, "render_compact_line_status") as compact,
        patch.object(shorts_line, "load_daily_line_summary") as daily,
        patch.object(shorts_line.st, "caption") as caption,
    ):
        shorts_line.render_main_line_summary(
            "video-1",
            Settings(data_dir=tmp_path),
        )

    caption.assert_called_once_with("生成中・工程 4／6")
    compact.assert_not_called()
    daily.assert_not_called()


def test_sidebar_restores_persisted_segment_preview_after_restart(tmp_path: Path) -> None:
    target, document, spec = _lineage_fixture()
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    queue_fingerprint = make_shorts_queue_fingerprint(
        video_id="video-1",
        source="clips",
        mode="concat",
        original_candidates=(candidate,),
        segments=target.segments,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    review = make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document
    )
    material = shorts_line._material_context_payload(
        source="clips",
        original_candidate=candidate,
        target=target,
        defaults=ShortsLineDefaults(),
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        queue_fingerprint,
        review_fingerprint=review,
        material_context=material,
    )
    state = confirm_review(state, review)
    state = set_generation_spec(state, review, spec.to_dict())
    source = tmp_path / "video-1" / "clips" / "source" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    result = MagicMock(clip_specs=(spec,), items=())
    session_state: dict[str, object] = {}
    with (
        patch.object(shorts_line, "resolve_active_line", return_value=state),
        patch.object(shorts_line, "load_latest_shorts_queue_result", return_value=result),
        patch.object(shorts_line, "get_active_job", return_value=None),
        patch.object(shorts_line, "render_compact_line_status"),
        patch.object(shorts_line, "load_daily_line_summary", return_value=None),
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "divider"),
        patch.object(shorts_line.st, "video") as video,
    ):
        shorts_line.render_sidebar_line_context(
            "video-1", Settings(data_dir=tmp_path)
        )

    assert video.call_args.args[0] == source
    assert video.call_args.kwargs["start_time"] == 0
    assert video.call_args.kwargs["end_time"] == 20


def test_pre_script_restart_restores_target_and_telop_retry(tmp_path: Path) -> None:
    target, document, _spec = _lineage_fixture()
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    queue_fingerprint = make_shorts_queue_fingerprint(
        video_id="video-1",
        source="clips",
        mode="concat",
        original_candidates=(candidate,),
        segments=target.segments,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    material = shorts_line._material_context_payload(
        source="clips",
        original_candidate=candidate,
        target=target,
        defaults=ShortsLineDefaults(),
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        queue_fingerprint,
        material_context=material,
    )
    settings = Settings(data_dir=tmp_path)
    save_line_state(state, settings)
    session_state: dict[str, object] = {}
    with patch.object(shorts_line.st, "session_state", session_state):
        context = shorts_line._restore_context("video-1", state, settings)
        assert context is not None and context["target"] == target
        assert "draft" not in context
        with patch.object(
            shorts_line,
            "generate_telop_script",
            return_value=MagicMock(document=document),
        ):
            shorts_line._generate_line_telop(
                video_id="video-1",
                target=target,
                state=state,
                context=context,
                settings=settings,
            )

    persisted = load_line_state("video-1", target.target_id, settings)
    assert persisted is not None
    assert persisted.review_fingerprint == make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document
    )
    assert persisted.review_confirmed_fingerprint is None


def test_restart_restores_s4_snapshot_and_can_confirm_overwrite(tmp_path: Path) -> None:
    target, document_a, _spec_a = _lineage_fixture()
    document_b = document_a.model_copy(update={"hook_text": "再起動後の台本"})
    spec_b = make_shorts_queue_clip_spec(
        target,
        document_b,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    queue_fingerprint = make_shorts_queue_fingerprint(
        video_id="video-1",
        source="clips",
        mode="concat",
        original_candidates=(candidate,),
        segments=target.segments,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    review = make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document_b
    )
    material = shorts_line._material_context_payload(
        source="clips",
        original_candidate=candidate,
        target=target,
        defaults=ShortsLineDefaults(),
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        queue_fingerprint,
        review_fingerprint=review,
        material_context=material,
    )
    state = confirm_review(state, review)
    state = set_generation_spec(state, review, spec_b.to_dict())
    settings = Settings(data_dir=tmp_path)
    output = tmp_path / "video-1" / "shorts" / "output" / spec_b.output_name
    output.parent.mkdir(parents=True)
    output.write_bytes(b"old-same-target-output")
    session_state: dict[str, object] = {}
    with (
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_queue_ui.st, "session_state", session_state),
    ):
        context = shorts_line._restore_context("video-1", state, settings)
        assert context is not None and context["confirmed_spec"] == spec_b
        with (
            patch.object(
                shorts_queue_ui, "get_selected_video_id", return_value="video-1"
            ),
            patch.object(shorts_queue_ui, "is_busy", return_value=False),
            patch.object(shorts_queue_ui.st, "warning"),
            patch.object(shorts_queue_ui.st, "markdown"),
            patch.object(shorts_queue_ui.st, "button", return_value=True),
            patch.object(shorts_queue_ui.st, "error"),
            patch.object(shorts_queue_ui, "_start_queue_job") as start,
        ):
            shorts_queue_ui._confirm_queue_overwrite_dialog.__wrapped__(
                video_id="video-1",
                title="動画",
                specs=(spec_b,),
                snapshot_fingerprint=queue_fingerprint,
                existing_names=(spec_b.output_name,),
                settings=settings,
            )

    start.assert_called_once()


def test_generation_preflight_failure_preserves_generation_stage_and_human_review(
    tmp_path: Path,
) -> None:
    target, document, spec = _lineage_fixture()
    queue_fingerprint = "a" * 64
    review = make_review_fingerprint(
        "video-1", target.target_id, queue_fingerprint, document
    )
    state = create_line_state(
        "video-1", target.target_id, queue_fingerprint, review_fingerprint=review
    )
    state = confirm_review(state, review)
    state = set_generation_spec(state, review, spec.to_dict())
    settings = Settings(
        data_dir=tmp_path,
        ffmpeg_path="/opt/homebrew/bin/ffmpeg",
    )
    save_line_state(state, settings)

    with (
        patch.object(shorts_queue_ui, "is_busy", return_value=False),
        patch.object(
            shorts_queue_ui,
            "ensure_subtitles_filter",
            side_effect=FfmpegError(
                "指定された FFmpeg で subtitles フィルタを利用できません。"
            ),
        ),
        patch.object(shorts_queue_ui, "start_job") as start,
        patch.object(shorts_queue_ui.st, "error"),
    ):
        shorts_queue_ui.start_or_confirm_line_generation(
            video_id="video-1",
            title="動画",
            spec=spec,
            snapshot_fingerprint=queue_fingerprint,
            settings=settings,
        )

    start.assert_not_called()
    persisted = load_line_state("video-1", target.target_id, settings)
    assert persisted is not None
    assert persisted.current_stage == LineStage.GENERATION
    assert persisted.review_confirmed_fingerprint == review
    assert persisted.output_fingerprint is None
    assert not (tmp_path / "video-1" / "shorts" / "queue").exists()


def test_start_line_handles_external_defaults_symlink_without_starting_line(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    outside = tmp_path.parent / "outside-shorts-defaults.json"
    outside.write_text(
        json.dumps({"layout": "crop", "preset": "boxed", "hook_preset": "hook"}),
        encoding="utf-8",
    )
    config_dir = tmp_path / "_config"
    config_dir.mkdir()
    (config_dir / "shorts_defaults.json").symlink_to(outside)
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    option = shorts_line.ParentOption("切り抜き", candidate)

    with (
        patch.object(shorts_line.st, "error") as error,
        patch.object(shorts_line, "install_line_snapshot") as install_snapshot,
        patch.object(shorts_line, "save_line_state") as save_state,
        patch.object(shorts_line, "save_active_line") as save_active,
        patch.object(shorts_line, "_generate_line_telop") as generate_telop,
        patch.object(shorts_line.st, "rerun") as rerun,
    ):
        shorts_line._start_line(
            video_id="video-1",
            title="動画",
            segments=(),
            option=option,
            settings=settings,
        )

    error.assert_called_once()
    message = error.call_args.args[0]
    assert "設定ページ" in message
    assert "ラインは開始していません" in message
    assert "outside-shorts-defaults.json" not in message
    assert "<" not in message and ">" not in message
    install_snapshot.assert_not_called()
    save_state.assert_not_called()
    save_active.assert_not_called()
    generate_telop.assert_not_called()
    rerun.assert_not_called()


def _write_line_defaults(path: Path, defaults: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(defaults), encoding="utf-8")


def test_line_defaults_use_canonical_file_when_present(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    _write_line_defaults(
        tmp_path / "_config" / "shorts_defaults.json",
        {"layout": "crop", "preset": "boxed", "hook_preset": "hook"},
    )
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults(
        "crop", "boxed", "hook"
    )


def test_line_defaults_read_legacy_file_only_when_canonical_is_absent(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    _write_line_defaults(
        tmp_path / "_config" / "short_defaults.json",
        {"layout": "crop", "preset": "boxed", "hook_preset": "hook"},
    )
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults(
        "crop", "boxed", "hook"
    )


def test_line_defaults_prefer_canonical_when_both_files_exist(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    _write_line_defaults(
        tmp_path / "_config" / "shorts_defaults.json",
        {"layout": "crop", "preset": "boxed", "hook_preset": "hook"},
    )
    _write_line_defaults(
        tmp_path / "_config" / "short_defaults.json",
        {"layout": "blur", "preset": "default", "hook_preset": "hook"},
    )
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults(
        "crop", "boxed", "hook"
    )


def test_corrupt_canonical_does_not_restore_legacy_values(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    canonical_path = tmp_path / "_config" / "shorts_defaults.json"
    _write_line_defaults(
        canonical_path,
        {"layout": "not-a-layout", "preset": "default", "hook_preset": "hook"},
    )
    _write_line_defaults(
        tmp_path / "_config" / "short_defaults.json",
        {"layout": "crop", "preset": "boxed", "hook_preset": "hook"},
    )
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults()

    canonical_path.write_text("{not valid json", encoding="utf-8")
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("layout", []),
        ("preset", []),
        ("hook_preset", {}),
    ],
)
def test_line_defaults_invalid_unhashable_values_fail_safe(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    settings = Settings(data_dir=tmp_path)
    defaults: dict[str, object] = {
        "layout": "blur",
        "preset": "default",
        "hook_preset": "hook",
    }
    defaults[field] = value
    _write_line_defaults(tmp_path / "_config" / "shorts_defaults.json", defaults)
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults()


def test_save_line_defaults_uses_canonical_path_and_secure_atomic_contract(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    legacy_path = tmp_path / "_config" / "short_defaults.json"
    _write_line_defaults(
        legacy_path,
        {"layout": "blur", "preset": "default", "hook_preset": "hook"},
    )

    saved_path = save_shorts_line_defaults(
        ShortsLineDefaults("crop", "boxed", "hook"), settings
    )

    canonical_path = tmp_path / "_config" / "shorts_defaults.json"
    assert saved_path == canonical_path
    assert json.loads(canonical_path.read_text(encoding="utf-8")) == {
        "layout": "crop",
        "preset": "boxed",
        "hook_preset": "hook",
    }
    assert json.loads(legacy_path.read_text(encoding="utf-8")) == {
        "layout": "blur",
        "preset": "default",
        "hook_preset": "hook",
    }
    assert stat.S_IMODE(canonical_path.stat().st_mode) == 0o600
    lock_path = tmp_path / "_config" / ".shorts_defaults.json.lock"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("filename", ["shorts_defaults.json", "short_defaults.json"])
def test_external_symlink_path_is_rejected_as_confinement_error(
    tmp_path: Path,
    filename: str,
) -> None:
    settings = Settings(data_dir=tmp_path)
    outside = tmp_path.parent / f"outside-{filename}"
    outside.write_text(
        json.dumps({"layout": "crop", "preset": "boxed", "hook_preset": "hook"}),
        encoding="utf-8",
    )
    config_dir = tmp_path / "_config"
    config_dir.mkdir()
    (config_dir / filename).symlink_to(outside)

    with pytest.raises(ValueError, match="データディレクトリ外"):
        load_shorts_line_defaults(settings)
    if filename == "shorts_defaults.json":
        with pytest.raises(ValueError, match="データディレクトリ外"):
            save_shorts_line_defaults(ShortsLineDefaults(), settings)
def test_s9_artifact_lineage_mismatch_keeps_clip_scope_and_fails_closed() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from yt_live_kit.ui.components import shorts_line

    artifact_ref = MagicMock()
    state = SimpleNamespace(
        video_id="video-1",
        clip_id="cut-1",
        artifact_ref=artifact_ref,
        artifact_fingerprint="a" * 64,
        used_range_cue_digests=("b" * 64,),
    )
    artifact = SimpleNamespace(
        video_id="video-1",
        artifact_fingerprint="a" * 64,
        used_range_cue_digests=("c" * 64,),
        is_high_precision=True,
    )
    store = MagicMock()
    store.load_artifact.return_value = artifact
    store.artifact_ref.return_value = artifact_ref
    with patch.object(shorts_line, "TranscriptArtifactStore", return_value=store):
        current, reason = shorts_line._inspect_artifact_lineage(
            state,
            MagicMock(),
        )

    assert current is False
    assert "使用区間の証跡" in reason
    assert "対象 clip: cut-1" in reason
    assert "ライン全体は失効していません" in reason


def test_s9_telop_header_reuses_artifact_ref_and_digest_array() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from yt_live_kit.ui.components import shorts_line

    artifact_ref = SimpleNamespace(
        model_dump=lambda mode="json": {"path": "transcripts/artifacts/ref.json"}
    )
    draft = SimpleNamespace(
        artifact_ref=artifact_ref,
        artifact_fingerprint="a" * 64,
        used_range_cue_digests=("b" * 64, "c" * 64),
    )
    with (
        patch.object(shorts_line.st, "caption") as caption,
        patch.object(shorts_line.st, "code") as code,
    ):
        shorts_line._render_telop_provenance_header(draft)

    assert "同一 ref / digest 配列" in caption.call_args.args[0]
    rendered = code.call_args.args[0]
    assert "transcripts/artifacts/ref.json" in rendered
    assert "a" * 64 in rendered
    assert "b" * 64 in rendered
    assert "c" * 64 in rendered
