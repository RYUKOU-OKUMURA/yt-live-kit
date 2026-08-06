"""ショート生産ライン UI の表示状態と安全接続のテスト."""

from __future__ import annotations

from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import stat
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings, WHISPER_ADOPTED_CONTRACT
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
    line_state_path,
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


class _ExpanderStub:
    """`st.expander` の戻り値スタブ（U10-1: 折り畳み open/closed の両経路を検証する）."""

    def __init__(self, is_open: bool) -> None:
        self.open = is_open

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


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


def test_sidebar_stepper_replaces_horizontal_badge_bar() -> None:
    """U9-6: 横並び st.badge 表示は廃止し、サイドバーの縦ステッパーへ一本化する."""
    state = create_line_state("video-1", "clip-1", "a" * 64).model_copy(
        update={"current_stage": LineStage.TELOP_REVIEW}
    )
    with (
        patch.object(shorts_line.st, "markdown") as markdown,
        patch.object(shorts_line.st, "caption") as caption,
        patch.object(shorts_line.st, "write"),
        patch.object(shorts_line.st, "badge") as badge,
    ):
        shorts_line.render_compact_line_status(state, None)

    badge.assert_not_called()
    markdowns = [call.args[0] for call in markdown.call_args_list]
    assert any("✅　1　素材選定" in value for value in markdowns)
    assert any("✅　2　区間決定" in value for value in markdowns)
    assert any("◉　3　テロップ確認" in value and "← 今" in value for value in markdowns)
    assert any("🔒　4　生成" in value for value in markdowns)
    assert any("🔒　5　最終確認" in value for value in markdowns)
    assert any("🔒　6　予約" in value for value in markdowns)
    captions = [call.args[0] for call in caption.call_args_list]
    # 接続線は記号側へ寄せるための空白を持つ（U9-6 追加修正）。
    assert captions.count(" │") == 5


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


def test_telop_editor_widgets_explicitly_persist_across_conditional_workspaces() -> None:
    _target, document, _spec = _lineage_fixture()
    session_state: dict[str, object] = {}
    first_column = MagicMock()
    second_column = MagicMock()

    @contextmanager
    def container(*_args: object, **_kwargs: object):
        yield

    with (
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "text_input") as text_input,
        patch.object(shorts_line.st, "text_area") as text_area,
        patch.object(shorts_line.st, "toggle") as toggle,
        patch.object(
            shorts_line.st,
            "columns",
            return_value=(first_column, second_column),
        ),
        patch.object(shorts_line.st, "container", side_effect=container),
        patch.object(shorts_line.st, "markdown"),
        patch.object(shorts_line.st, "caption"),
    ):
        shorts_line._editor_document(
            document,
            video_id="video-1",
            clip_id="clip-1",
            queue_fingerprint="a" * 64,
        )

    widget_calls = [
        *text_input.call_args_list,
        *text_area.call_args_list,
        *toggle.call_args_list,
        *first_column.number_input.call_args_list,
        *second_column.number_input.call_args_list,
    ]
    assert widget_calls
    assert all(call.kwargs["persist_state"] == "session" for call in widget_calls)


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
        patch.object(shorts_line, "resolve_active_line_read_only", return_value=None),
        patch.object(shorts_line, "render_short_cut_section") as cut,
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "subheader"),
        patch.object(shorts_line.st, "markdown"),
        patch.object(
            shorts_line.st, "expander", return_value=_ExpanderStub(True)
        ),
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


def test_confirmed_target_reaches_main_line_without_opening_reselect(
    tmp_path: Path,
) -> None:
    """U10-1: 引き継ぎ済みなら折り畳みを開かなくても主導線で確定表示・確定まで到達する."""
    clip = ClipCandidate(
        id="clip-1",
        title="短い切り抜き",
        start="0:00:00",
        end="0:00:30",
        duration_sec=30,
        reason="理由",
    )
    session_state: dict[str, object] = {}
    with (
        patch.object(shorts_line, "resolve_active_line_read_only", return_value=None),
        patch.object(shorts_line, "render_short_cut_section") as cut,
        patch.object(shorts_line, "_start_line") as start_line,
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "subheader"),
        patch.object(shorts_line.st, "markdown") as markdown,
        patch.object(
            shorts_line.st, "expander", return_value=_ExpanderStub(False)
        ) as expander,
        patch.object(shorts_line.st, "multiselect") as multiselect,
        patch.object(shorts_line.st, "radio") as radio,
        patch.object(shorts_line.st, "caption"),
        patch.object(shorts_line.st, "button", return_value=True),
    ):
        shorts_line.render_shorts_line(
            video_id="video-1",
            title="動画",
            clip_candidates=(clip,),
            highlight_candidates=(),
            settings=Settings(data_dir=tmp_path),
            preferred_candidate_keys=(("clips", "clip-1"),),
        )

    assert any(
        "今回作る候補" in call.args[0] for call in markdown.call_args_list
    )
    expander.assert_called_once()
    multiselect.assert_not_called()
    radio.assert_not_called()
    cut.assert_not_called()
    start_line.assert_called_once()


def test_empty_selection_still_shows_reselect_expander(tmp_path: Path) -> None:
    """U10-1: 全解除しても折り畳み（選び直し導線）が消えない（行き止まりゼロ）."""
    clip = ClipCandidate(
        id="clip-1",
        title="切り抜き",
        start="0:00:00",
        end="0:00:30",
        duration_sec=30,
        reason="理由",
    )
    selection_key = shorts_line.line_material_selection_key("video-1")
    transfer_marker_key = shorts_line.line_material_transfer_marker_key("video-1")
    session_state: dict[str, object] = {selection_key: [], transfer_marker_key: ()}
    with (
        patch.object(shorts_line, "resolve_active_line_read_only", return_value=None),
        patch.object(shorts_line, "render_short_cut_section") as cut,
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "subheader"),
        patch.object(shorts_line.st, "markdown") as markdown,
        patch.object(
            shorts_line.st, "expander", return_value=_ExpanderStub(False)
        ) as expander,
        patch.object(shorts_line.st, "multiselect"),
        patch.object(shorts_line.st, "radio"),
        patch.object(shorts_line.st, "caption"),
        patch.object(shorts_line.st, "info") as info,
        patch.object(shorts_line.st, "button", return_value=False),
    ):
        shorts_line.render_shorts_line(
            video_id="video-1",
            title="動画",
            clip_candidates=(clip,),
            highlight_candidates=(),
            settings=Settings(data_dir=tmp_path),
        )

    expander.assert_called_once()
    markdown.assert_not_called()
    info.assert_called_once()
    assert "選び直す" in info.call_args.args[0]
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


def test_scan_broken_line_entries_detects_corrupt_json(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    path = save_line_state(state, settings)
    path.write_text("{broken", encoding="utf-8")
    entries = shorts_line._scan_broken_line_entries("video-1", settings)
    assert len(entries) == 1
    assert entries[0].clip_id == "clip-1"
    assert "壊れている" in entries[0].message


def test_scan_broken_line_entries_does_not_mutate_legacy_state(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    legacy = state.model_copy(update={"schema_version": 1})
    path = save_line_state(legacy, settings)
    before = path.read_bytes()
    entries = shorts_line._scan_broken_line_entries("video-1", settings)
    assert entries == ()
    assert path.read_bytes() == before


def test_render_shorts_line_offers_recovery_for_corrupt_line_state(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    path = save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    path.write_text("{broken", encoding="utf-8")

    with patch.object(
        shorts_line,
        "_render_line_state_failure_recovery",
    ) as recovery:
        shorts_line.render_shorts_line(
            video_id="video-1",
            title="動画",
            clip_candidates=(),
            highlight_candidates=(),
            settings=settings,
        )

    recovery.assert_called_once()
    assert recovery.call_args.args[2] == "ショート生産ラインの状態が壊れているため安全に復元できません。"


def test_render_main_line_summary_offers_recovery_for_corrupt_line_state(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    path = save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    path.write_text("{broken", encoding="utf-8")

    with patch.object(
        shorts_line,
        "_render_line_state_failure_recovery",
    ) as recovery:
        shorts_line.render_main_line_summary("video-1", settings)

    recovery.assert_called_once()


def test_evacuate_broken_line_file_archives_and_clears_pointer(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    path = save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    path.write_text("{broken", encoding="utf-8")
    active_path = path.parent / "active_line.json"

    archive = shorts_line._evacuate_broken_line_file("video-1", "clip-1", settings)

    assert not path.exists()
    assert archive.is_file()
    assert not active_path.exists()


def test_execute_line_state_recovery_reparses_identity_mismatch_state(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
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
    mismatched = create_line_state(
        "video-1",
        "clip-wrong",
        queue_fingerprint,
        material_context=material,
    )
    wrong_path = line_state_path("video-1", "clip-1", settings)
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_path.write_text(mismatched.model_dump_json(indent=2), encoding="utf-8")
    evidence = shorts_line._gather_line_recovery_evidence(
        "video-1",
        "clip-1",
        settings,
        broken_path=wrong_path,
    )
    assert evidence is not None
    error = shorts_line._execute_line_state_recovery(
        "video-1",
        "clip-1",
        settings,
        evidence,
    )
    assert error is None
    restored = load_line_state("video-1", "clip-1", settings)
    assert restored is not None
    assert restored.clip_id == "clip-1"
    assert restored.queue_fingerprint == queue_fingerprint


def test_legacy_abandon_first_click_materializes_before_archive(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    legacy = state.model_copy(update={"schema_version": 1})
    line_path = save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    line_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    displayed = shorts_line.resolve_active_line_read_only("video-1", settings)
    assert displayed is not None
    session_state: dict[str, object] = {}

    with (
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line.st, "warning"),
        patch.object(shorts_line.st, "write"),
        patch.object(shorts_line.st, "checkbox", return_value=True),
        patch.object(shorts_line.st, "button", return_value=True),
        patch.object(shorts_line.st, "success"),
        patch.object(shorts_line.st, "rerun") as rerun,
        patch.object(shorts_line, "clear_line_confirmed_spec"),
        patch.object(shorts_line, "clear_line_snapshot"),
    ):
        shorts_line._confirm_abandon_line_dialog.__wrapped__(displayed, settings)

    assert not line_path.exists()
    assert tuple(line_path.parent.glob("abandoned_clip-1_*.json"))
    rerun.assert_called_once()


def test_abandon_stops_before_archive_when_projection_is_stale(tmp_path: Path) -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64)
    error = LineStateError("ライン状態が別の操作で更新されました。")
    with (
        patch.object(shorts_line.st, "warning"),
        patch.object(shorts_line.st, "write"),
        patch.object(shorts_line.st, "checkbox", return_value=True),
        patch.object(shorts_line.st, "button", return_value=True),
        patch.object(shorts_line.st, "error"),
        patch.object(
            shorts_line,
            "materialize_line_state_projection",
            side_effect=error,
        ),
        patch.object(shorts_line, "abandon_line_state") as abandon,
    ):
        shorts_line._confirm_abandon_line_dialog.__wrapped__(
            state,
            Settings(data_dir=tmp_path),
        )

    abandon.assert_not_called()


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
    session_state: dict[str, object] = {"unrelated": "keep"}
    with (
        patch(
            "yt_live_kit.ui.components.shorts_line.resolve_active_line_read_only",
            return_value=None,
        ),
        patch("yt_live_kit.ui.components.shorts_line.load_daily_line_summary", return_value=daily),
        patch.object(shorts_line.st, "session_state", session_state),
        patch("yt_live_kit.ui.components.shorts_line.st.divider"),
        patch("yt_live_kit.ui.components.shorts_line.st.markdown") as markdown,
        patch("yt_live_kit.ui.components.shorts_line.st.caption") as caption,
        patch("yt_live_kit.ui.components.shorts_line.st.write") as write,
        patch("yt_live_kit.ui.components.shorts_line.st.button") as button,
    ):
        shorts_line.render_sidebar_line_context("video-1", settings)

    markdowns = [call.args[0] for call in markdown.call_args_list]
    captions = [call.args[0] for call in caption.call_args_list]
    writes = [call.args[0] for call in write.call_args_list]
    assert markdowns[0] == "**作成中のショート**"
    # ライン未開始でも行き止まりにせず、最初の一歩を示す（U9-6）。
    assert "まだ開始していません" in captions
    # LineState は未作成でも、工程 1（素材選定）は現在地として current 表示する。
    assert any("◉　1　素材選定" in value and "← 今" in value for value in markdowns)
    assert any("🔒　2　区間決定" in value for value in markdowns)
    assert any("🔒　3　テロップ確認" in value for value in markdowns)
    assert any("🔒　4　生成" in value for value in markdowns)
    assert any("🔒　5　最終確認" in value for value in markdowns)
    assert any("🔒　6　予約" in value for value in markdowns)
    assert not any("✅" in value or "○" in value for value in markdowns)
    assert writes == [shorts_line.line_next_action_text(None)]
    # サイドバーは全ページで描画されるため、特定画面（タブ名）を前提にしない。
    assert writes == ["素材を選び、区間を決める"]
    assert "タブ" not in writes[0]
    assert "本日 1／3　・　要対応 2 件" in captions
    button.assert_not_called()
    assert session_state == {"unrelated": "keep"}


def test_main_line_double_render_keeps_legacy_line_pointer_and_session_unchanged(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", "clip-1", "a" * 64)
    line_path = save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    active_path = line_path.parent / "active_line.json"
    legacy = state.model_copy(update={"schema_version": 1})
    line_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    line_before = line_path.read_bytes()
    active_before = active_path.read_bytes()
    session_state: dict[str, object] = {"unrelated": "keep"}

    with (
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(shorts_line, "resolve_active_line") as mutating_resolver,
        patch.object(shorts_line, "_render_line_recovery_actions"),
        patch.object(shorts_line.st, "error"),
    ):
        for _ in range(2):
            shorts_line.render_shorts_line(
                video_id="video-1",
                title="動画",
                clip_candidates=(),
                highlight_candidates=(),
                settings=settings,
            )

    mutating_resolver.assert_not_called()
    assert session_state == {"unrelated": "keep"}
    assert line_path.read_bytes() == line_before
    assert active_path.read_bytes() == active_before


def test_sidebar_labels_target_stage_next_and_attention() -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64)
    daily = DailyLineSummary(completed_count=1, needs_attention_count=2)
    with (
        patch.object(shorts_line.st, "markdown") as markdown,
        patch.object(shorts_line.st, "caption") as caption,
        patch.object(shorts_line.st, "write") as write,
    ):
        shorts_line.render_compact_line_status(
            state,
            daily,
            title="重要ポイント",
        )

    markdowns = [call.args[0] for call in markdown.call_args_list]
    captions = [call.args[0] for call in caption.call_args_list]
    writes = [call.args[0] for call in write.call_args_list]
    assert "対象ショート: 重要ポイント" in captions
    assert any("← 今" in value for value in markdowns)
    assert "本日 1／3　・　要対応 2 件" in captions
    assert writes == [shorts_line.line_next_action_text(state.current_stage)]


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
        patch.object(shorts_line, "resolve_active_line_read_only", return_value=state),
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
        patch.object(shorts_line, "resolve_active_line_read_only", return_value=state),
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
        patch.object(shorts_line, "resolve_active_line_read_only", return_value=state),
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


def test_legacy_telop_generation_first_click_uses_canonical_state(
    tmp_path: Path,
) -> None:
    target, document, _spec = _lineage_fixture()
    settings = Settings(data_dir=tmp_path)
    state = create_line_state("video-1", target.target_id, "a" * 64)
    legacy = state.model_copy(update={"schema_version": 1})
    line_path = save_line_state(state, settings)
    save_active_line("video-1", target.target_id, settings)
    line_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    displayed = shorts_line.resolve_active_line_read_only("video-1", settings)
    assert displayed is not None
    context: dict[str, object] = {"target": target}

    with (
        patch.object(shorts_line.st, "session_state", {}),
        patch.object(
            shorts_line,
            "generate_telop_script",
            return_value=MagicMock(document=document),
        ) as generate,
    ):
        shorts_line._generate_line_telop(
            video_id="video-1",
            target=target,
            state=displayed,
            context=context,
            settings=settings,
        )

    generate.assert_called_once()
    persisted = load_line_state("video-1", target.target_id, settings)
    assert persisted is not None
    assert persisted.schema_version == 2
    assert persisted.review_fingerprint == make_review_fingerprint(
        "video-1", target.target_id, "a" * 64, document
    )
    assert context["draft"] == document


def test_stale_telop_generation_stops_before_codex_or_script_side_effect(
    tmp_path: Path,
) -> None:
    target, _document, _spec = _lineage_fixture()
    state = create_line_state("video-1", target.target_id, "a" * 64)
    context: dict[str, object] = {"target": target}
    with (
        patch.object(shorts_line.st, "session_state", {}),
        patch.object(
            shorts_line,
            "materialize_line_state_projection",
            side_effect=LineStateError("CAS conflict"),
        ),
        patch.object(shorts_line, "generate_telop_script") as generate,
    ):
        shorts_line._generate_line_telop(
            video_id="video-1",
            target=target,
            state=state,
            context=context,
            settings=Settings(data_dir=tmp_path),
        )

    generate.assert_not_called()
    assert context["telop_error"] == "CAS conflict"


def test_legacy_telop_review_first_commit_rebases_canonical_timestamp(
    tmp_path: Path,
) -> None:
    target, document, _spec = _lineage_fixture()
    settings = Settings(data_dir=tmp_path)
    queue_fingerprint = "a" * 64
    review = make_review_fingerprint(
        "video-1",
        target.target_id,
        queue_fingerprint,
        document,
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        queue_fingerprint,
        review_fingerprint=review,
    )
    state = confirm_review(state, review)
    legacy = state.model_copy(update={"schema_version": 1})
    line_path = save_line_state(state, settings)
    save_active_line("video-1", target.target_id, settings)
    line_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    displayed = shorts_line.resolve_active_line_read_only("video-1", settings)
    assert displayed is not None
    projected = shorts_line.project_review_state(
        displayed,
        review,
        force_unconfirmed=True,
    )

    completed, spec, saved_document = shorts_line._commit_telop_review(
        persisted_state=displayed,
        projected_state=projected,
        review_fingerprint=review,
        hard_errors=(),
        edited=document,
        target=target,
        defaults=ShortsLineDefaults(),
        settings=settings,
    )

    assert completed.schema_version == 2
    assert completed.review_confirmed_fingerprint == review
    assert completed.generation_spec_fingerprint is not None
    assert spec.target_id == target.target_id
    assert saved_document == document
    assert load_line_state("video-1", target.target_id, settings) == completed


def test_stale_telop_review_stops_before_confirmed_script_write(
    tmp_path: Path,
) -> None:
    target, document, _spec = _lineage_fixture()
    review = make_review_fingerprint(
        "video-1",
        target.target_id,
        "a" * 64,
        document,
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        "a" * 64,
        review_fingerprint=review,
    )
    with (
        patch.object(
            shorts_line,
            "materialize_line_state_projection",
            side_effect=LineStateError("CAS conflict"),
        ),
        patch.object(shorts_line, "save_confirmed_telop_script") as save_script,
    ):
        with pytest.raises(LineStateError, match="CAS conflict"):
            shorts_line._commit_telop_review(
                persisted_state=state,
                projected_state=state,
                review_fingerprint=review,
                hard_errors=(),
                edited=document,
                target=target,
                defaults=ShortsLineDefaults(),
                settings=Settings(data_dir=tmp_path),
            )

    save_script.assert_not_called()


def test_preview_confirmation_reobserves_output_from_canonical_state(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    output_path = tmp_path / "video-1" / "shorts" / "output" / "short.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"output")
    review = "b" * 64
    state = create_line_state(
        "video-1",
        "clip-1",
        "a" * 64,
        review_fingerprint=review,
    )
    state = confirm_review(state, review)
    state = set_generation_spec(
        state,
        review,
        {"target_id": "clip-1", "layout": "blur", "preset": "default"},
    )
    save_line_state(state, settings)

    confirmed = shorts_line._confirm_line_preview(state, output_path, settings)

    assert confirmed.current_stage == LineStage.RESERVATION
    assert confirmed.output_fingerprint is not None
    assert confirmed.preview_confirmed_fingerprint == confirmed.output_fingerprint
    assert load_line_state("video-1", "clip-1", settings) == confirmed


def test_stale_preview_confirmation_stops_before_output_observation(
    tmp_path: Path,
) -> None:
    state = create_line_state("video-1", "clip-1", "a" * 64)
    with (
        patch.object(
            shorts_line,
            "materialize_line_state_projection",
            side_effect=LineStateError("CAS conflict"),
        ),
        patch.object(shorts_line, "record_output") as record,
        patch.object(shorts_line, "reconcile_output") as reconcile,
        patch.object(shorts_line, "confirm_preview") as confirm,
        patch.object(shorts_line, "save_line_state") as save,
    ):
        with pytest.raises(LineStateError, match="CAS conflict"):
            shorts_line._confirm_line_preview(
                state,
                tmp_path / "short.mp4",
                Settings(data_dir=tmp_path),
            )

    record.assert_not_called()
    reconcile.assert_not_called()
    confirm.assert_not_called()
    save.assert_not_called()


def test_generation_command_revalidates_canonical_review_and_spec_before_job(
    tmp_path: Path,
) -> None:
    target, document, spec = _lineage_fixture()
    settings = Settings(data_dir=tmp_path)
    review = make_review_fingerprint(
        "video-1",
        target.target_id,
        "a" * 64,
        document,
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        "a" * 64,
        review_fingerprint=review,
    )
    state = confirm_review(state, review)
    state = set_generation_spec(state, review, spec.to_dict())
    save_line_state(state, settings)

    with patch.object(shorts_line, "start_or_confirm_line_generation") as start:
        shorts_line._start_line_generation_command(
            displayed_state=state,
            review_fingerprint=review,
            spec=spec,
            video_id="video-1",
            title="動画",
            settings=settings,
        )

    start.assert_called_once()
    assert start.call_args.kwargs["snapshot_fingerprint"] == "a" * 64


def test_stale_generation_command_stops_before_job_start(tmp_path: Path) -> None:
    target, document, spec = _lineage_fixture()
    review = make_review_fingerprint(
        "video-1",
        target.target_id,
        "a" * 64,
        document,
    )
    state = create_line_state(
        "video-1",
        target.target_id,
        "a" * 64,
        review_fingerprint=review,
    )
    with (
        patch.object(
            shorts_line,
            "materialize_line_state_projection",
            side_effect=LineStateError("CAS conflict"),
        ),
        patch.object(shorts_line, "start_or_confirm_line_generation") as start,
    ):
        with pytest.raises(LineStateError, match="CAS conflict"):
            shorts_line._start_line_generation_command(
                displayed_state=state,
                review_fingerprint=review,
                spec=spec,
                video_id="video-1",
                title="動画",
                settings=Settings(data_dir=tmp_path),
            )

    start.assert_not_called()


def test_rerun_replaces_stale_telop_error_when_persisted_script_matches(
    tmp_path: Path,
) -> None:
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
    settings = Settings(data_dir=tmp_path)
    save_line_state(state, settings)
    script_path = (
        tmp_path
        / "video-1"
        / "shorts"
        / "telop"
        / f"telop_{target.target_id}.json"
    )
    script_path.parent.mkdir(parents=True)
    script_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    stale_document = document.model_copy(update={"hook_text": "古い失敗前の台本"})
    context = {
        "telop_error": "前回の一時的なJSONエラー",
        "target": target,
        "draft": stale_document,
    }
    session_state: dict[str, object] = {}

    with patch.object(shorts_line.st, "session_state", session_state):
        restored = shorts_line._restore_stale_telop_context(
            "video-1",
            state,
            context,
            settings,
        )

    assert restored["draft"] == document
    assert "telop_error" not in restored


def test_telop_failure_keeps_fail_closed_retry_state(tmp_path: Path) -> None:
    target, _document, _spec = _lineage_fixture()
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
    state = create_line_state("video-1", target.target_id, queue_fingerprint)
    context: dict[str, object] = {"target": target}
    settings = Settings(data_dir=tmp_path)
    save_line_state(state, settings)
    with patch.object(
        shorts_line,
        "generate_telop_script",
        return_value=MagicMock(document=None),
    ):
        shorts_line._generate_line_telop(
            video_id="video-1",
            target=target,
            state=state,
            context=context,
            settings=settings,
        )

    assert "draft" not in context
    assert "テロップ台本を生成できませんでした" in context["telop_error"]


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


def test_start_line_installs_no_session_projection_when_durable_command_fails(
    tmp_path: Path,
) -> None:
    candidate = ClipCandidate(
        id="clip-source",
        title="短い候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    segment = HighlightSegment.model_validate(candidate.model_dump())
    option = shorts_line.ParentOption("切り抜き", candidate)
    session_state: dict[str, object] = {"unrelated": "keep"}
    settings = Settings(data_dir=tmp_path)

    with (
        patch.object(shorts_line.st, "session_state", session_state),
        patch.object(
            shorts_line,
            "persist_line_start",
            side_effect=LineStateError("pointer write fault"),
        ),
        patch.object(shorts_line, "install_prepared_line_snapshot") as install,
        patch.object(shorts_line, "_generate_line_telop") as generate_telop,
        patch.object(shorts_line.st, "error") as error,
        patch.object(shorts_line.st, "rerun") as rerun,
    ):
        shorts_line._start_line(
            video_id="video-1",
            title="動画",
            segments=(segment,),
            option=option,
            settings=settings,
        )

    assert session_state == {"unrelated": "keep"}
    install.assert_not_called()
    generate_telop.assert_not_called()
    rerun.assert_not_called()
    assert "pointer write fault" in error.call_args.args[0]


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


def test_s9_artifact_lineage_invalidates_when_whisper_contract_changes(
    tmp_path: Path,
) -> None:
    from yt_live_kit.models.transcript import TranscriptCue, TranscriptRange
    from yt_live_kit.services.transcript_artifact import (
        TranscriptArtifactStore,
        build_transcript_artifact,
    )

    settings = Settings(data_dir=tmp_path)
    contract = WHISPER_ADOPTED_CONTRACT
    artifact = build_transcript_artifact(
        video_id="video-1",
        source_kind="whisper_cpp",
        source_ref="transcripts/audio/range.wav",
        language="ja",
        ranges=[TranscriptRange(start_ms=10_000, end_ms=20_000)],
        cues=[TranscriptCue(start_ms=11_000, end_ms=12_000, text="artifact cue")],
        audio_bytes=b"ui-lineage-audio",
        model={
            "name": contract.model_name,
            "sha256": "0" * 64,
            "fingerprint": "0" * 64,
        },
        runtime={
            "version": contract.binary_version,
            "binary_sha256": contract.binary_sha256,
        },
        settings={
            "language": contract.language,
            "initial_prompt": contract.initial_prompt,
            "output_schema": contract.output_schema,
            "padding_ms": contract.padding_ms,
            "vad": contract.vad,
            "decode": contract.decode,
        },
        source_metadata={"audio_spans": [{"audio_route": "local_source_accurate_seek"}]},
    )
    store = TranscriptArtifactStore("video-1", settings)
    store.save(artifact)
    reference = store.artifact_ref(artifact)
    state = create_line_state(
        "video-1",
        "cut-1",
        "a" * 64,
        artifact_ref=reference,
        artifact_fingerprint=artifact.artifact_fingerprint,
        used_range_cue_digests=artifact.used_range_cue_digests,
    )

    current, reason = shorts_line._inspect_artifact_lineage(state, settings)

    assert current is False
    assert "証跡が一致しない" in reason
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
        patch.object(
            shorts_line.st, "expander", return_value=_ExpanderStub(False)
        ) as expander,
    ):
        shorts_line._render_telop_provenance_header(draft)

    assert "高精度字幕に合わせています" in caption.call_args.args[0]
    expander.assert_called_once()
    rendered = code.call_args.args[0]
    assert "transcripts/artifacts/ref.json" in rendered
    assert "a" * 64 in rendered
    assert "b" * 64 in rendered
    assert "c" * 64 in rendered


def test_s9_telop_header_skips_expander_when_no_artifact() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from yt_live_kit.ui.components import shorts_line

    draft = SimpleNamespace(
        artifact_ref=None,
        artifact_fingerprint=None,
        used_range_cue_digests=(),
    )
    with (
        patch.object(shorts_line.st, "caption") as caption,
        patch.object(shorts_line.st, "expander") as expander,
    ):
        shorts_line._render_telop_provenance_header(draft)

    assert "自動字幕（通常精度）に合わせています" in caption.call_args.args[0]
    expander.assert_not_called()
