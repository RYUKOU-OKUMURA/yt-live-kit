"""U6 ショート生産ラインの状態・安全契約テスト。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.telop import (
    TelopLine,
    TelopScriptDocument,
    TelopSegmentScript,
)
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadOperation,
)
from yt_live_kit.services.schedule import SchedulePolicy
from yt_live_kit.services.shorts_line import (
    LineStage,
    LineState,
    LineStateError,
    calculate_line_stage,
    confirm_preview,
    confirm_review,
    create_line_state,
    evaluate_telop_gate,
    line_state_path,
    load_line_state,
    make_output_fingerprint,
    make_review_fingerprint,
    reconcile_output,
    record_output,
    record_upload_operation,
    recover_line_state,
    resolve_active_line,
    save_active_line,
    save_line_state,
    set_review_fingerprint,
    summarize_daily_lines,
)


NOW = datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc)
QUEUE_FP = "a" * 64


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def _document(*, text: str = "確認する本文", emphasis: bool = False) -> TelopScriptDocument:
    return TelopScriptDocument(
        hook_text="冒頭フック",
        title_candidates=["タイトル案"],
        description="説明文",
        tags=["タグ"],
        segments=[
            TelopSegmentScript(
                start_sec=10,
                end_sec=20,
                lines=[
                    TelopLine(
                        text=text,
                        start_sec=10,
                        end_sec=20,
                        emphasis=emphasis,
                    )
                ],
            )
        ],
    )


def _output(tmp_path: Path, content: bytes = b"mp4-v1") -> Path:
    path = (tmp_path / "short.mp4").resolve()
    path.write_bytes(content)
    return path


def _review_fingerprint(document: TelopScriptDocument | None = None) -> str:
    return make_review_fingerprint("video-1", "clip-1", QUEUE_FP, document or _document())


def _confirmed_state(tmp_path: Path) -> tuple[LineState, Path]:
    review = _review_fingerprint()
    state = create_line_state(
        "video-1", "clip-1", QUEUE_FP, review_fingerprint=review, now=NOW
    )
    state = confirm_review(state, review, now=NOW + timedelta(seconds=1))
    output = _output(tmp_path)
    state = record_output(state, output, now=NOW + timedelta(seconds=2))
    state = confirm_preview(state, output, now=NOW + timedelta(seconds=3))
    return state, output


def _operation(
    tmp_path: Path,
    *,
    operation_id: str,
    source_video_id: str = "source-1",
    source_kind: str = "shorts_queue",
    clip_id: str = "clip-1",
    state: str = "reserved",
    created_at: datetime = NOW,
) -> UploadOperation:
    video = (tmp_path / f"{operation_id}.mp4").resolve()
    video.write_bytes(b"video")
    content = UploadContentSnapshot(
        channel=UploadChannel(channel_id="UC1", title="確認チャンネル"),
        video_path=video,
        file_size=video.stat().st_size,
        file_mtime_ns=video.stat().st_mtime_ns,
        duration_sec=30,
        title="タイトル",
        description="説明",
        tags=("タグ",),
        publish_at=created_at + timedelta(days=1),
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=False,
        contains_synthetic_media=False,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=created_at,
    )
    started_at = created_at if state in {"uploading", "uploaded"} else None
    finished_at = created_at if state in {"uploaded", "failed", "needs_reconciliation"} else None
    return UploadOperation(
        operation_id=operation_id,
        source_video_id=source_video_id,
        source_kind=source_kind,
        clip_id=clip_id,
        video_path=video,
        content=content,
        state=state,
        job_id=f"job-{operation_id}",
        video_id=f"yt-{operation_id}" if state == "uploaded" else None,
        created_at=created_at,
        updated_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        error="手動確認が必要です。" if state in {"failed", "needs_reconciliation"} else None,
        poll_history=(),
        publication_eligibility="unknown",
    )


def test_review_fingerprint_is_canonical_and_covers_all_review_inputs() -> None:
    first = _review_fingerprint()
    assert len(first) == 64
    assert first == _review_fingerprint(_document())
    assert first != _review_fingerprint(_document(text="変更後"))
    assert first != _review_fingerprint(_document(emphasis=True))
    assert first != make_review_fingerprint("video-2", "clip-1", QUEUE_FP, _document())
    assert first != make_review_fingerprint("video-1", "clip-2", QUEUE_FP, _document())
    assert first != make_review_fingerprint("video-1", "clip-1", "b" * 64, _document())


def test_output_fingerprint_uses_strict_path_stat_and_content(tmp_path: Path) -> None:
    path = _output(tmp_path)
    review = _review_fingerprint()
    first = make_output_fingerprint("video-1", "clip-1", review, path)
    assert first == make_output_fingerprint("video-1", "clip-1", review, path.parent / "." / path.name)

    old = path.stat()
    path.write_bytes(b"mp4-v2")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    assert make_output_fingerprint("video-1", "clip-1", review, path) != first
    assert make_output_fingerprint("video-1", "clip-1", "b" * 64, path) != first
    with pytest.raises(LineStateError, match="読み込めない"):
        make_output_fingerprint("video-1", "clip-1", review, tmp_path / "missing.mp4")


def test_telop_gate_keeps_four_quality_concerns_separate() -> None:
    review = _review_fingerprint()
    warning_only = evaluate_telop_gate((), ("1 行が 16 文字を超えています。",), review, review)
    assert warning_only.hard_valid is True
    assert warning_only.warnings
    assert warning_only.human_confirmed is True
    assert warning_only.fingerprint_current is True
    assert warning_only.can_generate is True

    hard_failure = evaluate_telop_gate(("時刻が不正です。",), (), review, review)
    assert hard_failure.hard_valid is False
    assert hard_failure.can_generate is False
    unconfirmed = evaluate_telop_gate((), (), review, None)
    assert unconfirmed.human_confirmed is False
    assert unconfirmed.can_generate is False
    stale = evaluate_telop_gate((), (), review, "b" * 64)
    assert stale.human_confirmed is True
    assert stale.fingerprint_current is False
    assert stale.can_generate is False


def test_calculate_line_stage_covers_six_stages_and_terminal() -> None:
    review = _review_fingerprint()
    ready = evaluate_telop_gate((), (), review, review)
    blocked = evaluate_telop_gate((), (), review, None)
    base = {
        "material_selected": True,
        "segments_confirmed": True,
        "telop_gate": ready,
        "output_available": True,
        "output_fingerprint_current": True,
        "preview_confirmed": True,
    }
    assert calculate_line_stage(**{**base, "material_selected": False}) == LineStage.MATERIAL_SELECTION
    assert calculate_line_stage(**{**base, "segments_confirmed": False}) == LineStage.SEGMENT_DECISION
    assert calculate_line_stage(**{**base, "telop_gate": blocked}) == LineStage.TELOP_REVIEW
    assert calculate_line_stage(**{**base, "output_available": False}) == LineStage.GENERATION
    assert calculate_line_stage(**{**base, "preview_confirmed": False}) == LineStage.FINAL_REVIEW
    assert calculate_line_stage(**base) == LineStage.RESERVATION
    for state in ("reserved", "uploading", "uploaded"):
        assert calculate_line_stage(**base, upload_state=state) == LineStage.RESERVED
    for state in ("failed", "needs_reconciliation"):
        assert calculate_line_stage(**base, upload_state=state) == LineStage.RESERVATION


def test_review_edit_invalidation_does_not_auto_restore_after_revert(tmp_path: Path) -> None:
    original = _review_fingerprint()
    changed = _review_fingerprint(_document(text="変更後"))
    state = create_line_state(
        "video-1", "clip-1", QUEUE_FP, review_fingerprint=original, now=NOW
    )
    state = confirm_review(state, original, now=NOW + timedelta(seconds=1))
    state = record_output(state, _output(tmp_path), now=NOW + timedelta(seconds=2))
    state = set_review_fingerprint(state, changed, now=NOW + timedelta(seconds=3))
    assert state.review_confirmed_fingerprint is None
    assert state.output_fingerprint is None
    assert state.preview_confirmed_fingerprint is None
    assert state.current_stage == LineStage.TELOP_REVIEW

    reverted = set_review_fingerprint(state, original, now=NOW + timedelta(seconds=4))
    assert reverted.review_fingerprint == original
    assert reverted.review_confirmed_fingerprint is None
    with pytest.raises(LineStateError, match="ハード判定"):
        confirm_review(reverted, original, hard_errors=("形式エラー",))


def test_output_change_and_missing_only_invalidate_preview_confirmation(tmp_path: Path) -> None:
    state, output = _confirmed_state(tmp_path)
    review_confirmation = state.review_confirmed_fingerprint

    old = output.stat()
    output.write_bytes(b"externally-replaced")
    os.utime(output, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    changed = reconcile_output(state, output, now=NOW + timedelta(seconds=4))
    assert changed.review_confirmed_fingerprint == review_confirmation
    assert changed.output_fingerprint != state.output_fingerprint
    assert changed.preview_confirmed_fingerprint is None
    assert changed.current_stage == LineStage.FINAL_REVIEW

    output.unlink()
    missing = reconcile_output(changed, output, now=NOW + timedelta(seconds=5))
    assert missing.review_confirmed_fingerprint == review_confirmation
    assert missing.output_fingerprint is None
    assert missing.preview_confirmed_fingerprint is None
    assert missing.current_stage == LineStage.GENERATION


def test_preview_rechecks_output_and_upload_requires_preview(tmp_path: Path) -> None:
    review = _review_fingerprint()
    state = create_line_state(
        "video-1", "clip-1", QUEUE_FP, review_fingerprint=review, now=NOW
    )
    state = confirm_review(state, review, now=NOW + timedelta(seconds=1))
    output = _output(tmp_path)
    state = record_output(state, output, now=NOW + timedelta(seconds=2))
    with pytest.raises(LineStateError, match="最終確認"):
        record_upload_operation(state, "op-1")

    output.write_bytes(b"changed-after-render")
    with pytest.raises(LineStateError, match="変更"):
        confirm_preview(state, output)

    state = reconcile_output(state, output, now=NOW + timedelta(seconds=3))
    state = confirm_preview(state, output, now=NOW + timedelta(seconds=4))
    completed = record_upload_operation(state, "op-1", now=NOW + timedelta(seconds=5))
    assert completed.current_stage == LineStage.RESERVED
    assert completed.upload_operation_id == "op-1"
    with pytest.raises(LineStateError, match="編集できません"):
        set_review_fingerprint(completed, "b" * 64)


def test_line_state_atomic_round_trip_and_corrupt_fail_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state, _output_path = _confirmed_state(tmp_path)
    path = save_line_state(state, settings)
    assert path == line_state_path("video-1", "clip-1", settings)
    assert load_line_state("video-1", "clip-1", settings) == state
    assert not tuple(path.parent.glob("*.tmp"))
    assert load_line_state("video-1", "missing", settings) is None

    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(LineStateError, match="壊れている"):
        load_line_state("video-1", "clip-1", settings)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_atomic_replace_failure_preserves_previous_line(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = create_line_state("video-1", "clip-1", QUEUE_FP, now=NOW)
    path = save_line_state(state, settings)
    original = path.read_bytes()
    updated = set_review_fingerprint(state, _review_fingerprint(), now=NOW + timedelta(seconds=1))
    with patch("yt_live_kit.services.shorts_line.os.replace", side_effect=OSError("failure")):
        with pytest.raises(LineStateError, match="安全に保存"):
            save_line_state(updated, settings)
    assert path.read_bytes() == original
    assert not tuple(path.parent.glob("*.tmp"))


def test_active_pointer_and_deterministic_fallback(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    same_time = NOW + timedelta(minutes=1)
    older = create_line_state("video-1", "clip-z", QUEUE_FP, now=NOW)
    same_b = create_line_state("video-1", "clip-b", QUEUE_FP, now=same_time)
    same_a = create_line_state("video-1", "clip-a", QUEUE_FP, now=same_time)
    for state in (older, same_b, same_a):
        save_line_state(state, settings)

    save_active_line("video-1", "clip-z", settings, now=NOW + timedelta(minutes=2))
    assert resolve_active_line("video-1", settings) == older

    active_path = settings.data_dir / "video-1" / "shorts" / "line" / "active_line.json"
    active_path.write_text("{broken", encoding="utf-8")
    assert resolve_active_line("video-1", settings) == same_a

    line_state_path("video-1", "clip-a", settings).write_text("[]", encoding="utf-8")
    assert resolve_active_line("video-1", settings) == same_b


def test_completed_lines_are_not_reopened_by_pointer_or_fallback(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state, _output_path = _confirmed_state(tmp_path)
    state = record_upload_operation(state, "op-1", now=NOW + timedelta(seconds=4))
    save_line_state(state, settings)
    assert resolve_active_line("video-1", settings) is None
    with pytest.raises(LineStateError, match="予約完了"):
        save_active_line("video-1", "clip-1", settings)


def test_recovery_keeps_machine_evidence_but_clears_both_human_confirmations(tmp_path: Path) -> None:
    state, _output_path = _confirmed_state(tmp_path)
    recovered = recover_line_state(
        state.video_id,
        state.clip_id,
        state.queue_fingerprint,
        review_fingerprint=state.review_fingerprint,
        output_fingerprint=state.output_fingerprint,
        now=NOW + timedelta(minutes=1),
    )
    assert recovered.review_fingerprint == state.review_fingerprint
    assert recovered.output_fingerprint == state.output_fingerprint
    assert recovered.review_confirmed_fingerprint is None
    assert recovered.review_confirmed_at is None
    assert recovered.preview_confirmed_fingerprint is None
    assert recovered.preview_confirmed_at is None
    assert recovered.current_stage == LineStage.TELOP_REVIEW

    completed = recover_line_state(
        state.video_id,
        state.clip_id,
        state.queue_fingerprint,
        upload_operation_id="op-machine-proof",
        now=NOW + timedelta(minutes=2),
    )
    assert completed.current_stage == LineStage.RESERVED
    assert completed.upload_operation_id == "op-machine-proof"


def test_line_state_rejects_identity_traversal_and_inconsistent_confirmation() -> None:
    with pytest.raises(LineStateError, match="パス"):
        create_line_state("../video", "clip-1", QUEUE_FP)
    payload = create_line_state("video-1", "clip-1", QUEUE_FP, now=NOW).model_dump()
    payload.update(
        review_fingerprint="b" * 64,
        review_confirmed_fingerprint="c" * 64,
        review_confirmed_at=NOW,
    )
    with pytest.raises(Exception, match="一致しません"):
        LineState.model_validate(payload)


def test_daily_summary_uses_policy_timezone_latest_source_key_and_attention(tmp_path: Path) -> None:
    # Tokyo 8/1 の開始直後。UTC ではまだ 7/31。
    now = datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)
    policy = SchedulePolicy(timezone="Asia/Tokyo")
    operations = [
        _operation(
            tmp_path,
            operation_id="a-old",
            state="uploaded",
            created_at=datetime(2026, 7, 31, 15, 1, tzinfo=timezone.utc),
        ),
        _operation(
            tmp_path,
            operation_id="a-new",
            state="failed",
            created_at=datetime(2026, 7, 31, 15, 2, tzinfo=timezone.utc),
        ),
        _operation(
            tmp_path,
            operation_id="b",
            source_video_id="source-2",
            state="reserved",
            created_at=datetime(2026, 7, 31, 15, 3, tzinfo=timezone.utc),
        ),
        _operation(
            tmp_path,
            operation_id="c",
            source_kind="manual",
            state="uploading",
            created_at=datetime(2026, 7, 31, 15, 4, tzinfo=timezone.utc),
        ),
        _operation(
            tmp_path,
            operation_id="previous-day",
            source_video_id="source-3",
            state="needs_reconciliation",
            created_at=datetime(2026, 7, 31, 14, 59, tzinfo=timezone.utc),
        ),
    ]
    summary = summarize_daily_lines(operations, policy, now=now)
    assert summary.completed_count == 2
    assert summary.needs_attention_count == 1
    assert summary.target_count == 3

    utc_summary = summarize_daily_lines(
        operations,
        SchedulePolicy(timezone="UTC"),
        now=now,
    )
    assert utc_summary.completed_count == 2
    assert utc_summary.needs_attention_count == 2
    with pytest.raises(LineStateError, match="タイムゾーン"):
        summarize_daily_lines(operations, policy, now=now.replace(tzinfo=None))


def test_latest_operation_tie_breaks_by_operation_id(tmp_path: Path) -> None:
    first = _operation(tmp_path, operation_id="a", state="uploaded", created_at=NOW)
    second = _operation(tmp_path, operation_id="b", state="needs_reconciliation", created_at=NOW)
    summary = summarize_daily_lines(
        (first, second),
        SchedulePolicy(timezone="Asia/Tokyo"),
        now=NOW,
    )
    assert summary.completed_count == 0
    assert summary.needs_attention_count == 1
