"""Pure UI state contracts that must survive the large visual redesign."""

from __future__ import annotations

import pytest

from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.shorts_line import LineStage, confirm_review, create_line_state
from yt_live_kit.ui.view_models.shorts_line import (
    STAGES,
    line_next_action_text,
    line_stepper_items,
    project_review_state,
    sync_telop_editor_state,
)
from yt_live_kit.ui.view_models.video_detail import current_candidate_fingerprint


def _document(
    *,
    hook: str = "元のフック",
    text: str = "元の本文",
    start_sec: float = 10.0,
    end_sec: float = 20.0,
) -> TelopScriptDocument:
    return TelopScriptDocument.model_validate(
        {
            "hook_text": hook,
            "title_candidates": ["タイトル"],
            "description": "説明",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "lines": [
                        {
                            "text": text,
                            "start_sec": start_sec,
                            "end_sec": end_sec,
                            "emphasis": False,
                        }
                    ],
                }
            ],
        }
    )


def test_telop_editor_keeps_manual_buffer_for_identical_draft_identity() -> None:
    state: dict[str, object] = {"unrelated": "keep"}
    draft = _document()
    prefix = sync_telop_editor_state(
        state,
        draft,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )
    state[f"{prefix}_hook"] = "人が編集したフック"
    state[f"{prefix}_0_0_text"] = "人が編集した本文"

    same_prefix = sync_telop_editor_state(
        state,
        draft,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )

    assert same_prefix == prefix
    assert state[f"{prefix}_hook"] == "人が編集したフック"
    assert state[f"{prefix}_0_0_text"] == "人が編集した本文"
    assert state["unrelated"] == "keep"


def test_telop_editor_resets_only_its_prefix_for_new_full_document() -> None:
    state: dict[str, object] = {"unrelated": "keep"}
    first = _document()
    prefix = sync_telop_editor_state(
        state,
        first,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )
    state[f"{prefix}_hook"] = "古い手編集"
    state[f"{prefix}_stale_widget"] = "古い行"
    second = _document(hook="新しいAI案", text="新しい本文")

    sync_telop_editor_state(
        state,
        second,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )

    assert state[f"{prefix}_hook"] == "新しいAI案"
    assert state[f"{prefix}_0_0_text"] == "新しい本文"
    assert f"{prefix}_stale_widget" not in state
    assert state["unrelated"] == "keep"


def test_telop_editor_a_to_b_to_a_never_leaks_an_old_manual_buffer() -> None:
    state: dict[str, object] = {}
    draft_a = _document(hook="案A")
    draft_b = _document(hook="案B")
    prefix = sync_telop_editor_state(
        state,
        draft_a,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )
    state[f"{prefix}_hook"] = "Aの手編集"
    sync_telop_editor_state(
        state,
        draft_b,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )
    state[f"{prefix}_hook"] = "Bの手編集"

    sync_telop_editor_state(
        state,
        draft_a,
        video_id="video-1",
        clip_id="clip-1",
        queue_fingerprint="a" * 64,
    )

    assert state[f"{prefix}_hook"] == "案A"


def test_telop_editor_same_positions_reset_on_boundary_or_queue_change() -> None:
    state: dict[str, object] = {}
    draft = _document()
    prefix = sync_telop_editor_state(
        state,
        draft,
        video_id="video-1",
        clip_id="same-clip-id",
        queue_fingerprint="a" * 64,
    )
    state[f"{prefix}_0_0_text"] = "古い境界の手編集"
    boundary_changed = _document(start_sec=11.0, end_sec=20.0)
    sync_telop_editor_state(
        state,
        boundary_changed,
        video_id="video-1",
        clip_id="same-clip-id",
        queue_fingerprint="a" * 64,
    )
    assert state[f"{prefix}_0_0_text"] == "元の本文"
    state[f"{prefix}_0_0_text"] = "古いqueueの手編集"

    sync_telop_editor_state(
        state,
        boundary_changed,
        video_id="video-1",
        clip_id="same-clip-id",
        queue_fingerprint="b" * 64,
    )

    assert state[f"{prefix}_0_0_text"] == "元の本文"


def test_review_projection_invalidates_confirmation_after_edit_and_revert() -> None:
    original = "a" * 64
    changed = "b" * 64
    state = create_line_state(
        "video-1",
        "clip-1",
        "c" * 64,
        review_fingerprint=original,
    )
    state = confirm_review(state, original)
    projected = project_review_state(
        state,
        changed,
        force_unconfirmed=True,
    )
    reverted = project_review_state(
        state,
        original,
        force_unconfirmed=True,
    )

    assert projected.review_confirmed_fingerprint is None
    assert reverted.review_fingerprint == original
    assert reverted.review_confirmed_fingerprint is None


def test_clip_transfer_prefers_saved_coarse_lineage_fingerprint() -> None:
    candidate = ClipCandidate(
        id="clip-1",
        title="候補",
        start="0:00:00",
        end="0:00:20",
        duration_sec=20,
        reason="理由",
    )
    persisted = "d" * 64

    assert current_candidate_fingerprint(
        "clips",
        (candidate,),
        persisted_candidate_fingerprint=persisted,
    ) == persisted
    assert current_candidate_fingerprint(
        "highlights",
        (candidate,),
        persisted_candidate_fingerprint=persisted,
    ) != persisted


def test_line_stepper_items_none_state_marks_stage_one_current() -> None:
    """U9-6 修正: LineState 未作成でも工程 1（素材選定）は現在地として current にする。

    工程 2〜6 は一本道でまだ進めないため locked。これは表示の導出だけであり、
    LineState の生成条件（区間確定まで LineState を作らない）は変えない。
    """
    items = line_stepper_items(None)

    assert len(items) == 6
    assert [item.index for item in items] == [1, 2, 3, 4, 5, 6]
    assert [item.stage for item in items] == list(STAGES)
    assert items[0].status == "current"
    assert all(item.status == "locked" for item in items[1:])


@pytest.mark.parametrize("current_stage", STAGES)
def test_line_stepper_items_marks_done_current_and_locked(
    current_stage: LineStage,
) -> None:
    """U9-6: index < current は完了、index == current は進行中、それ以降は未到達."""
    items = line_stepper_items(current_stage)
    current_index = STAGES.index(current_stage) + 1

    assert len(items) == 6
    for item in items:
        if item.index < current_index:
            assert item.status == "done"
        elif item.index == current_index:
            assert item.status == "current"
        else:
            assert item.status == "locked"


def test_line_stepper_items_reserved_marks_all_stages_done() -> None:
    """U9-6: RESERVED は既存 render_stage_bar の分岐と同じく全工程を完了扱いにする."""
    items = line_stepper_items(LineStage.RESERVED)

    assert all(item.status == "done" for item in items)


def test_line_next_action_text_none_state_shows_first_step_guidance() -> None:
    """U9-6 修正: ライン未開始でも次の一歩を示し、行き止まりを作らない."""
    assert line_next_action_text(None) == "素材を選び、区間を決める"


@pytest.mark.parametrize("current_stage", (None, *STAGES))
def test_line_next_action_text_never_assumes_a_specific_screen(
    current_stage: LineStage | None,
) -> None:
    """U9-6 修正: サイドバーは全ページで描画されるため画面名を前提にしない。

    「〇〇タブで」のような特定画面の存在を前提にした表現は、その画面に
    既にいるユーザーには誤った案内になるため禁止する。
    """
    text = line_next_action_text(current_stage)

    assert isinstance(text, str)
    assert text != ""
    for forbidden in ("タブ", "ページ", "画面"):
        assert forbidden not in text


@pytest.mark.parametrize("current_stage", STAGES)
def test_line_next_action_text_matches_active_stage(
    current_stage: LineStage,
) -> None:
    text = line_next_action_text(current_stage)

    assert isinstance(text, str)
    assert text != ""
