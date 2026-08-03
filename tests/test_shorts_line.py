"""U6 ショート生産ラインの状態・安全契約テスト。"""

from __future__ import annotations

import json
import os
import threading
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
from yt_live_kit.models.transcript import TranscriptArtifactRef
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadOperation,
)
from yt_live_kit.services.schedule import SchedulePolicy
from yt_live_kit.services.shorts_line import (
    LineReservationStartedError,
    LineStage,
    LineState,
    LineStateError,
    abandon_line_state,
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
    run_line_reservation_transaction,
    save_active_line,
    save_line_state,
    set_review_fingerprint,
    set_generation_spec,
    summarize_daily_lines,
)


NOW = datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc)
QUEUE_FP = "a" * 64


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def test_abandon_line_archives_state_and_keeps_artifacts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = create_line_state("video-1", "clip-1", QUEUE_FP, now=NOW)
    save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings, now=NOW + timedelta(seconds=1))
    artifact = (
        settings.data_dir
        / "video-1"
        / "shorts"
        / "output"
        / "short_clip-1.mp4"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"keep")

    archive = abandon_line_state(state, settings)

    assert archive.is_file()
    assert load_line_state("video-1", "clip-1", settings) is None
    assert resolve_active_line("video-1", settings) is None
    assert artifact.read_bytes() == b"keep"


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
    path = (tmp_path / "data" / "short.mp4").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _review_fingerprint(document: TelopScriptDocument | None = None) -> str:
    return make_review_fingerprint("video-1", "clip-1", QUEUE_FP, document or _document())


def _lineage() -> tuple[TranscriptArtifactRef, str, tuple[str, ...]]:
    fingerprint = "c" * 64
    reference = TranscriptArtifactRef(
        video_id="video-1",
        artifact_fingerprint=fingerprint,
        source_kind="whisper_cpp",
        path=f"transcripts/artifacts/{fingerprint}.json",
    )
    return reference, fingerprint, ("d" * 64,)


def _generation_spec(*, layout: str = "blur", preset: str = "default") -> dict[str, object]:
    return {
        "target_id": "clip-1",
        "layout": layout,
        "preset": preset,
        "hook_preset": "hook",
        "telop_document": _document().model_dump(mode="json"),
    }


def test_line_state_propagates_artifact_lineage_into_review_and_generation_spec():
    reference, fingerprint, digests = _lineage()
    document = _document().model_copy(
        update={
            "artifact_ref": reference,
            "artifact_fingerprint": fingerprint,
            "used_range_cue_digests": digests,
        }
    )
    review = _review_fingerprint(document)
    state = create_line_state(
        "video-1",
        "clip-1",
        QUEUE_FP,
        review_fingerprint=review,
        artifact_ref=reference,
        artifact_fingerprint=fingerprint,
        used_range_cue_digests=digests,
        now=NOW,
    )
    state = confirm_review(state, review, now=NOW + timedelta(seconds=1))
    spec = {
        **_generation_spec(),
        "artifact_ref": reference.model_dump(mode="json"),
        "artifact_fingerprint": fingerprint,
        "used_range_cue_digests": list(digests),
    }
    state = set_generation_spec(state, review, spec, now=NOW + timedelta(seconds=2))
    assert state.artifact_ref == reference
    assert state.artifact_fingerprint == fingerprint
    assert state.used_range_cue_digests == digests
    assert state.generation_spec_json is not None
    assert "used_range_cue_digests" in state.generation_spec_json


def _confirmed_state(tmp_path: Path) -> tuple[LineState, Path]:
    review = _review_fingerprint()
    state = create_line_state(
        "video-1", "clip-1", QUEUE_FP, review_fingerprint=review, now=NOW
    )
    state = confirm_review(state, review, now=NOW + timedelta(seconds=1))
    state = set_generation_spec(
        state,
        review,
        _generation_spec(),
        now=NOW + timedelta(milliseconds=1500),
    )
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
    state = set_generation_spec(
        state,
        original,
        _generation_spec(),
        now=NOW + timedelta(milliseconds=1500),
    )
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


def test_generation_spec_proof_is_full_and_review_change_clears_it() -> None:
    review = _review_fingerprint()
    state = create_line_state(
        "video-1", "clip-1", QUEUE_FP, review_fingerprint=review, now=NOW
    )
    state = confirm_review(state, review, now=NOW + timedelta(seconds=1))
    state = set_generation_spec(
        state,
        review,
        _generation_spec(layout="blur", preset="default"),
        now=NOW + timedelta(seconds=2),
    )
    assert state.generation_spec_fingerprint is not None
    payload = state.model_dump(mode="json")
    payload["generation_spec_json"] = json.dumps(
        _generation_spec(layout="crop", preset="default"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(Exception, match="生成 spec"):
        LineState.model_validate(payload)

    changed = set_review_fingerprint(
        state,
        _review_fingerprint(_document(text="変更")),
        now=NOW + timedelta(seconds=3),
    )
    assert changed.generation_spec_json is None
    assert changed.generation_spec_fingerprint is None


def test_old_output_state_without_generation_proof_fails_closed(tmp_path: Path) -> None:
    state, _output_path = _confirmed_state(tmp_path)
    payload = state.model_dump(mode="json")
    payload["generation_spec_json"] = None
    payload["generation_spec_fingerprint"] = None
    with pytest.raises(Exception, match="生成 spec"):
        LineState.model_validate(payload)


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
    state = set_generation_spec(
        state,
        review,
        _generation_spec(),
        now=NOW + timedelta(milliseconds=1500),
    )
    output = _output(tmp_path)
    state = record_output(state, output, now=NOW + timedelta(seconds=2))
    with pytest.raises(LineStateError, match="最終確認"):
        record_upload_operation(state, "op-1", output)

    output.write_bytes(b"changed-after-render")
    with pytest.raises(LineStateError, match="変更"):
        confirm_preview(state, output)

    state = reconcile_output(state, output, now=NOW + timedelta(seconds=3))
    state = confirm_preview(state, output, now=NOW + timedelta(seconds=4))
    completed = record_upload_operation(
        state,
        "op-1",
        output,
        now=NOW + timedelta(seconds=5),
    )
    assert completed.current_stage == LineStage.RESERVED
    assert completed.upload_operation_id == "op-1"
    with pytest.raises(LineStateError, match="編集できません"):
        set_review_fingerprint(completed, "b" * 64)


def test_reservation_rechecks_output_after_preview_confirmation(tmp_path: Path) -> None:
    state, output = _confirmed_state(tmp_path)
    previous = output.stat()
    output.write_bytes(b"changed-before-reservation")
    os.utime(output, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
    invalidated = record_upload_operation(state, "op-1", output)
    assert invalidated.current_stage == LineStage.FINAL_REVIEW
    assert invalidated.review_confirmed_fingerprint == state.review_confirmed_fingerprint
    assert invalidated.preview_confirmed_fingerprint is None
    assert invalidated.upload_operation_id is None


def test_reservation_transaction_rejects_stale_state_and_serializes_tabs(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    state, output = _confirmed_state(tmp_path)
    save_line_state(state, settings)
    operation = _operation(
        tmp_path,
        operation_id="transaction-op",
        source_video_id="video-1",
        clip_id="clip-1",
    ).model_copy(update={"video_path": output})

    invalid = set_review_fingerprint(state, "c" * 64)
    save_line_state(invalid, settings, expected_state=state)
    called = False

    def must_not_start() -> UploadOperation:
        nonlocal called
        called = True
        return operation

    with pytest.raises(LineStateError, match="最終確認|生成 spec"):
        run_line_reservation_transaction(
            "video-1", "clip-1", output, settings, must_not_start
        )
    assert called is False

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second_settings = Settings(data_dir=second_dir / "data")
    state, output = _confirmed_state(second_dir)
    save_line_state(state, second_settings)
    operation = operation.model_copy(update={"video_path": output})
    callback_entered = threading.Event()
    release_callback = threading.Event()
    invalidator_started = threading.Event()
    invalidator_done = threading.Event()
    errors: list[Exception] = []

    def start_upload() -> UploadOperation:
        callback_entered.set()
        assert release_callback.wait(2)
        return operation

    def invalidate_from_second_tab() -> None:
        try:
            previous = load_line_state("video-1", "clip-1", second_settings)
            assert previous is not None
            changed = set_review_fingerprint(previous, "d" * 64)
            invalidator_started.set()
            save_line_state(changed, second_settings, expected_state=previous)
        except Exception as exc:
            errors.append(exc)
        finally:
            invalidator_done.set()

    result: list[UploadOperation] = []
    transaction = threading.Thread(
        target=lambda: result.append(
            run_line_reservation_transaction(
                "video-1", "clip-1", output, second_settings, start_upload
            )
        )
    )
    transaction.start()
    assert callback_entered.wait(2)
    invalidator = threading.Thread(target=invalidate_from_second_tab)
    invalidator.start()
    assert invalidator_started.wait(2)
    assert invalidator_done.wait(0.05) is False
    release_callback.set()
    transaction.join(2)
    invalidator.join(2)

    assert result == [operation]
    assert any(isinstance(error, LineStateError) for error in errors)
    persisted = load_line_state("video-1", "clip-1", second_settings)
    assert persisted is not None and persisted.current_stage == LineStage.RESERVED


def test_reservation_transaction_raises_started_error_if_output_changes_in_callback(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    state, output = _confirmed_state(tmp_path)
    save_line_state(state, settings)
    operation = _operation(
        tmp_path,
        operation_id="changed-during-start",
        source_video_id="video-1",
        clip_id="clip-1",
    ).model_copy(update={"video_path": output})

    def start_and_replace_output() -> UploadOperation:
        output.write_bytes(b"changed-during-upload-start")
        return operation

    with pytest.raises(LineReservationStartedError) as raised:
        run_line_reservation_transaction(
            "video-1",
            "clip-1",
            output,
            settings,
            start_and_replace_output,
        )

    assert raised.value.operation == operation
    persisted = load_line_state("video-1", "clip-1", settings)
    assert persisted is not None
    assert persisted.current_stage == LineStage.FINAL_REVIEW
    assert persisted.preview_confirmed_fingerprint is None
    assert persisted.upload_operation_id is None


def test_state_cannot_advance_beyond_persisted_gate_evidence() -> None:
    state = create_line_state("video-1", "clip-1", QUEUE_FP, now=NOW)
    for stage in (
        LineStage.GENERATION,
        LineStage.FINAL_REVIEW,
        LineStage.RESERVATION,
    ):
        with pytest.raises(Exception, match="必要な"):
            LineState.model_validate({**state.model_dump(), "current_stage": stage})
    with pytest.raises(LineStateError, match="後"):
        set_review_fingerprint(
            state,
            _review_fingerprint(),
            now=NOW - timedelta(seconds=1),
        )


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


def test_legacy_line_state_does_not_reuse_human_or_final_confirmation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    confirmed, _output_path = _confirmed_state(tmp_path)
    legacy = confirmed.model_copy(update={"schema_version": 1})
    save_line_state(legacy, settings)

    restored = load_line_state("video-1", "clip-1", settings)

    assert restored is not None
    assert restored.schema_version == 2
    assert restored.review_fingerprint is None
    assert restored.review_confirmed_fingerprint is None
    assert restored.preview_confirmed_fingerprint is None
    assert restored.output_fingerprint is None
    assert restored.current_stage == LineStage.TELOP_REVIEW


def test_missing_artifact_invalidates_line_confirmations_without_resolver(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reference, fingerprint, digests = _lineage()
    document = _document().model_copy(
        update={
            "artifact_ref": reference,
            "artifact_fingerprint": fingerprint,
            "used_range_cue_digests": digests,
        }
    )
    review = _review_fingerprint(document)
    state = create_line_state(
        "video-1",
        "clip-1",
        QUEUE_FP,
        review_fingerprint=review,
        artifact_ref=reference,
        artifact_fingerprint=fingerprint,
        used_range_cue_digests=digests,
        now=NOW,
    )
    state = confirm_review(state, review, now=NOW + timedelta(seconds=1))
    save_line_state(state, settings)

    restored = load_line_state("video-1", "clip-1", settings)

    assert restored is not None
    assert restored.artifact_ref == reference
    assert restored.review_confirmed_fingerprint is None
    assert restored.output_fingerprint is None
    assert restored.current_stage == LineStage.TELOP_REVIEW


def test_stale_confirmed_snapshot_cannot_restore_newer_invalidation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    confirmed, _output_path = _confirmed_state(tmp_path)
    save_line_state(confirmed, settings)
    stale_confirmed = confirmed

    invalidated = set_review_fingerprint(
        confirmed,
        _review_fingerprint(_document(text="変更後")),
        now=NOW + timedelta(seconds=4),
    )
    save_line_state(invalidated, settings, expected_state=confirmed)
    with pytest.raises(LineStateError, match="古い"):
        save_line_state(stale_confirmed, settings)

    restored = load_line_state("video-1", "clip-1", settings)
    assert restored == invalidated
    assert restored is not None
    assert restored.review_confirmed_fingerprint is None


def test_save_compare_and_swap_rejects_concurrent_replacement(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = create_line_state("video-1", "clip-1", QUEUE_FP, now=NOW)
    save_line_state(state, settings)
    first = set_review_fingerprint(
        state,
        _review_fingerprint(),
        now=NOW + timedelta(seconds=1),
    )
    save_line_state(first, settings, expected_state=state)

    competing = set_review_fingerprint(
        state,
        _review_fingerprint(_document(text="別の編集")),
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(LineStateError, match="別の操作"):
        save_line_state(competing, settings, expected_state=state)
    assert load_line_state("video-1", "clip-1", settings) == first


def test_record_upload_mismatch_returns_and_persists_invalidated_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state, output = _confirmed_state(tmp_path)
    save_line_state(state, settings)
    previous = output.stat()
    output.write_bytes(b"replaced-after-preview")
    os.utime(output, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))

    invalidated = record_upload_operation(
        state,
        "op-not-recorded",
        output,
        settings=settings,
        now=NOW + timedelta(seconds=4),
    )
    assert invalidated.current_stage == LineStage.FINAL_REVIEW
    assert invalidated.preview_confirmed_fingerprint is None
    assert invalidated.upload_operation_id is None
    assert load_line_state("video-1", "clip-1", settings) == invalidated


def test_atomic_parent_creation_error_is_normalized(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = create_line_state("video-1", "clip-1", QUEUE_FP, now=NOW)
    directory = line_state_path("video-1", "clip-1", settings).parent
    directory.mkdir(parents=True)
    real_mkdir = Path.mkdir
    calls = 0

    def fail_second_mkdir(path: Path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("mkdir failure")
        return real_mkdir(path, *args, **kwargs)

    with patch.object(Path, "mkdir", fail_second_mkdir):
        with pytest.raises(LineStateError, match="安全に保存"):
            save_line_state(state, settings)
    assert not line_state_path("video-1", "clip-1", settings).exists()


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
    state = record_upload_operation(
        state,
        "op-1",
        _output_path,
        now=NOW + timedelta(seconds=4),
    )
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
        generation_spec=_generation_spec(),
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

    operation = _operation(
        tmp_path,
        operation_id="op-machine-proof",
        source_video_id=state.video_id,
        clip_id=state.clip_id,
        state="reserved",
        created_at=NOW + timedelta(minutes=2),
    )
    completed = recover_line_state(
        state.video_id,
        state.clip_id,
        state.queue_fingerprint,
        upload_operation=operation,
        now=NOW + timedelta(minutes=2),
    )
    assert completed.current_stage == LineStage.RESERVED
    assert completed.upload_operation_id == "op-machine-proof"

    with pytest.raises(LineStateError, match="一致しません"):
        recover_line_state(
            state.video_id,
            state.clip_id,
            state.queue_fingerprint,
            upload_operation=_operation(
                tmp_path,
                operation_id="wrong-source",
                source_video_id="other-video",
                clip_id=state.clip_id,
                state="reserved",
                created_at=NOW + timedelta(minutes=3),
            ),
            now=NOW + timedelta(minutes=3),
        )
    with pytest.raises(LineStateError, match="未完了"):
        recover_line_state(
            state.video_id,
            state.clip_id,
            state.queue_fingerprint,
            upload_operation=_operation(
                tmp_path,
                operation_id="failed",
                source_video_id=state.video_id,
                clip_id=state.clip_id,
                state="failed",
                created_at=NOW + timedelta(minutes=4),
            ),
            now=NOW + timedelta(minutes=4),
        )


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
    with pytest.raises(Exception, match="未完了"):
        LineState.model_validate(
            {
                **create_line_state(
                    "video-1", "clip-1", QUEUE_FP, now=NOW
                ).model_dump(),
                "upload_operation_id": "op-without-reserved-stage",
            }
        )


def test_daily_summary_uses_policy_timezone_latest_source_key_and_attention(tmp_path: Path) -> None:
    # Tokyo 8/1 の開始直後。UTC ではまだ 7/31。
    now = datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)
    policy = SchedulePolicy(daily_times=["09:00"], timezone="Asia/Tokyo")
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
        SchedulePolicy(daily_times=["09:00"], timezone="UTC"),
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
        SchedulePolicy(daily_times=["09:00"], timezone="Asia/Tokyo"),
        now=NOW,
    )
    assert summary.completed_count == 0
    assert summary.needs_attention_count == 1
