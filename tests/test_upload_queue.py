"""安全な upload queue と attempt 台帳のテスト."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadOperation,
    UploadStatusObservation,
)
from yt_live_kit.services.upload_queue import (
    UploadQueueError,
    append_poll_observation,
    count_upload_attempts,
    create_reserved_operation,
    list_operations,
    list_upload_attempts,
    record_upload_attempt,
    recover_upload_operations,
    set_publication_eligibility,
    transition_operation,
    upload_job_target,
)
from yt_live_kit.models.upload import UploadResult
from yt_live_kit.services.youtube_api import YouTubeAPIError
from yt_live_kit.services.jobs import read_job, start_job


def _snapshot(tmp_path: Path, *, publish_at: datetime | None = None) -> UploadContentSnapshot:
    video = (tmp_path / "short.mp4").resolve()
    video.write_bytes(b"video")
    stat = video.stat()
    return UploadContentSnapshot(
        channel=UploadChannel(channel_id="UC123", title="テストチャンネル"),
        video_path=video,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
        duration_sec=30,
        title="タイトル",
        description="説明",
        tags=("タグ",),
        publish_at=publish_at or datetime(2026, 8, 2, tzinfo=timezone.utc),
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=False,
        contains_synthetic_media=True,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _reserved(
    tmp_path: Path,
    *,
    operation_id: str = "op1",
    job_id: str = "job1",
    publish_at: datetime | None = None,
):
    settings = Settings(data_dir=tmp_path)
    operation = create_reserved_operation(
        operation_id=operation_id,
        job_id=job_id,
        source_video_id="source1",
        source_kind="shorts_queue",
        clip_id="clip1",
        content=_snapshot(tmp_path, publish_at=publish_at),
        now=datetime(2026, 7, 31, tzinfo=timezone.utc),
        settings=settings,
    )
    return settings, operation


def test_operation_models_are_frozen_and_reject_unknown_fields(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    with pytest.raises(ValidationError):
        UploadOperation.model_validate({**operation.model_dump(), "unknown": 1})
    with pytest.raises(ValidationError):
        operation.state = "uploading"  # type: ignore[misc]
    assert list_operations(settings) == (operation,)


def test_operation_rejects_missing_publication_eligibility(tmp_path: Path) -> None:
    _settings, operation = _reserved(tmp_path)
    values = operation.model_dump()
    values.pop("publication_eligibility")
    with pytest.raises(ValidationError, match="publication_eligibility"):
        UploadOperation.model_validate(values)


def test_operation_rejects_missing_job_id(tmp_path: Path) -> None:
    _settings, operation = _reserved(tmp_path)
    values = operation.model_dump()
    values.pop("job_id")
    with pytest.raises(ValidationError, match="job_id"):
        UploadOperation.model_validate(values)


@pytest.mark.parametrize(
    "updates",
    [
        {"state": "reserved", "video_id": "yt1"},
        {"state": "reserved", "error": "エラー"},
        {"state": "uploading", "started_at": None},
        {
            "state": "uploading",
            "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        },
        {
            "state": "uploaded",
            "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "video_id": None,
        },
        {
            "state": "uploaded",
            "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "video_id": "yt1",
            "error": "エラー",
        },
        {
            "state": "failed",
            "finished_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "error": None,
        },
        {
            "state": "needs_reconciliation",
            "finished_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "error": "照合",
            "video_id": "yt1",
        },
    ],
)
def test_operation_rejects_inconsistent_state_fields(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    _settings, operation = _reserved(tmp_path)
    values = operation.model_dump()
    values.update(updates)
    with pytest.raises(ValidationError):
        UploadOperation.model_validate(values)


@pytest.mark.parametrize(
    "updates",
    [
        {"updated_at": datetime(2026, 7, 30, tzinfo=timezone.utc)},
        {
            "state": "uploading",
            "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "started_at": datetime(2026, 7, 31, tzinfo=timezone.utc),
        },
        {
            "state": "failed",
            "started_at": datetime(2026, 8, 1, 0, 1, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 1, 0, 1, tzinfo=timezone.utc),
            "error": "失敗",
        },
    ],
)
def test_operation_rejects_invalid_timestamp_order(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    _settings, operation = _reserved(tmp_path)
    values = operation.model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match="日時"):
        UploadOperation.model_validate(values)


def test_queue_is_single_full_operation_record_and_atomic_temp_is_removed(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    payload = json.loads((tmp_path / "_schedule" / "queue.json").read_text(encoding="utf-8"))
    assert payload["operations"][0]["operation_id"] == operation.operation_id
    assert payload["operations"][0]["content"]["channel"]["channel_id"] == "UC123"
    assert not list((tmp_path / "_schedule").glob("*.tmp"))


@pytest.mark.parametrize("content", ["{broken", "[]", '{"schema_version": 1, "operations": {}}'])
def test_broken_queue_fails_closed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "_schedule" / "queue.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(UploadQueueError, match="投稿キュー"):
        list_operations(Settings(data_dir=tmp_path))
    assert path.read_text(encoding="utf-8") == content


def test_invalid_utf8_queue_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "_schedule" / "queue.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(UploadQueueError, match="壊れている"):
        list_operations(Settings(data_dir=tmp_path))
    assert path.read_bytes() == b"\xff\xfe"


def test_state_transitions_and_terminal_reexecution_are_rejected(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    uploading = transition_operation(operation.operation_id, "uploading", settings)
    assert uploading.started_at is not None
    failed = transition_operation(
        operation.operation_id, "failed", settings, error="失敗しました"
    )
    assert failed.finished_at is not None
    with pytest.raises(UploadQueueError, match="変更できません"):
        transition_operation(operation.operation_id, "uploading", settings)


def test_attempt_is_idempotent_and_uses_los_angeles_date(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    instant = datetime(2026, 8, 1, 3, 30, tzinfo=timezone.utc)  # LA は前日
    first = record_upload_attempt(operation.operation_id, operation.job_id, settings, now=instant)
    second = record_upload_attempt(operation.operation_id, operation.job_id, settings, now=instant + timedelta(minutes=1))
    assert first == second
    assert first.attempt_date_la == "2026-07-31"
    assert len(list_upload_attempts(settings)) == 1


@pytest.mark.parametrize("attempt_date", ["2026-02-30", "2026-08-01"])
def test_attempt_rejects_invalid_or_mismatched_la_date(
    tmp_path: Path, attempt_date: str
) -> None:
    settings, operation = _reserved(tmp_path)
    path = tmp_path / "_schedule" / "upload_attempts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempts": [
                    {
                        "attempt_date_la": attempt_date,
                        "operation_id": operation.operation_id,
                        "job_id": operation.job_id,
                        "attempted_at": "2026-08-01T03:30:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(UploadQueueError, match="試行台帳"):
        list_upload_attempts(settings)
    with pytest.raises(UploadQueueError):
        count_upload_attempts(settings, now=datetime(2026, 8, 1, tzinfo=timezone.utc))


def test_upload_limit_environment_boundaries(monkeypatch) -> None:
    monkeypatch.setenv("YTLK_VIDEO_UPLOAD_DAILY_LIMIT", "1")
    assert Settings(_env_file=None).video_upload_daily_limit == 1
    monkeypatch.setenv("YTLK_VIDEO_UPLOAD_DAILY_LIMIT", "100")
    assert Settings(_env_file=None).video_upload_daily_limit == 100
    monkeypatch.setenv("YTLK_VIDEO_UPLOAD_DAILY_LIMIT", "101")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_attempt_date_is_stable_across_los_angeles_dst_fallback(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    first = create_reserved_operation(
        operation_id="dst1", job_id="job1", source_video_id="s",
        source_kind="short", clip_id="c1", content=_snapshot(tmp_path),
        settings=settings,
    )
    second = create_reserved_operation(
        operation_id="dst2", job_id="job2", source_video_id="s",
        source_kind="short", clip_id="c2", content=_snapshot(tmp_path),
        settings=settings,
    )
    # 2026-11-01 の 01:30 が PDT/PST で 2 回現れるが、暦日は同じ。
    one = record_upload_attempt(
        first.operation_id, first.job_id, settings,
        now=datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc),
    )
    two = record_upload_attempt(
        second.operation_id, second.job_id, settings,
        now=datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),
    )
    assert one.attempt_date_la == two.attempt_date_la == "2026-11-01"


def test_attempt_limit_counts_failed_or_unknown_attempts(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, video_upload_daily_limit=1)
    one = create_reserved_operation(
        operation_id="op1", job_id="job1", source_video_id="s", source_kind="short",
        clip_id="c1", content=_snapshot(tmp_path), settings=settings,
    )
    record_upload_attempt(one.operation_id, one.job_id, settings)
    two = create_reserved_operation(
        operation_id="op2", job_id="job2", source_video_id="s", source_kind="short",
        clip_id="c2", content=_snapshot(tmp_path), settings=settings,
    )
    with pytest.raises(UploadQueueError, match="上限"):
        record_upload_attempt(two.operation_id, two.job_id, settings)


def test_recovery_without_attempt_fails_and_releases_slot(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    recovered = recover_upload_operations(settings)
    assert recovered[0].operation_id == operation.operation_id
    assert recovered[0].state == "failed"
    assert "session 開始前" in (recovered[0].error or "")


def test_recovery_after_uploading_save_but_before_attempt_is_failed(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    transition_operation(operation.operation_id, "uploading", settings)
    recovered = recover_upload_operations(settings)
    assert recovered[0].state == "failed"
    assert list_upload_attempts(settings) == ()


def test_recovery_with_attempt_requires_reconciliation_and_never_resends(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    transition_operation(operation.operation_id, "uploading", settings)
    record_upload_attempt(operation.operation_id, operation.job_id, settings)
    recovered = recover_upload_operations(settings)
    assert recovered[0].state == "needs_reconciliation"
    assert "自動再送せず" in (recovered[0].error or "")
    assert recover_upload_operations(settings) == ()


def test_corrupt_attempt_ledger_marks_active_reconciliation_and_fails_closed(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    attempts = tmp_path / "_schedule" / "upload_attempts.json"
    attempts.write_text("{broken", encoding="utf-8")
    with pytest.raises(UploadQueueError, match="新規投稿を停止"):
        recover_upload_operations(settings)
    assert list_operations(settings)[0].state == "needs_reconciliation"


def test_terminal_ledger_mismatch_leaves_queue_unchanged(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    transition_operation(operation.operation_id, "uploading", settings)
    uploaded = transition_operation(
        operation.operation_id, "uploaded", settings, video_id="youtube1"
    )
    before = (tmp_path / "_schedule" / "queue.json").read_bytes()
    with pytest.raises(UploadQueueError, match="不整合"):
        recover_upload_operations(settings)
    assert (tmp_path / "_schedule" / "queue.json").read_bytes() == before
    assert uploaded.state == "uploaded"


def test_job_target_records_attempt_before_session_and_is_idempotent(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    upload = MagicMock()

    def assert_ledger_then_upload(*_args, **_kwargs):
        assert len(list_upload_attempts(settings)) == 1
        return UploadResult(state="uploaded", video_id="yt1", error=None)

    def read_only_channel_lookup(*_args, **_kwargs):
        assert list_upload_attempts(settings) == ()
        return operation.content.channel

    upload.side_effect = assert_ledger_then_upload
    with (
        patch(
            "yt_live_kit.services.upload_queue.fetch_mine_channel",
            side_effect=read_only_channel_lookup,
        ),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.build_upload_body"),
        patch("yt_live_kit.services.upload_queue.upload_video_resumable", upload),
        patch("yt_live_kit.services.upload_queue.poll_processing_status", return_value=()),
        patch("yt_live_kit.services.jobs.update_job"),
    ):
        upload_job_target(
            report=MagicMock(), settings=settings,
            job_id=operation.job_id, operation_id=operation.operation_id,
        )
        # terminal operation は既存結果を返し、新しい session / attempt を作らない。
        upload_job_target(
            report=MagicMock(), settings=settings,
            job_id=operation.job_id, operation_id=operation.operation_id,
        )
    assert upload.call_count == 1
    assert len(list_upload_attempts(settings)) == 1
    assert list_operations(settings)[0].state == "uploaded"


def test_job_target_preflight_failure_never_records_attempt_or_uploads(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    with (
        patch("yt_live_kit.services.upload_queue.fetch_mine_channel", return_value=operation.content.channel),
        patch(
            "yt_live_kit.services.upload_queue.validate_snapshot_identity",
            side_effect=YouTubeAPIError("確認後にファイルが変わりました。"),
        ),
        patch("yt_live_kit.services.upload_queue.upload_video_resumable") as upload,
    ):
        with pytest.raises(UploadQueueError, match="ファイル"):
            upload_job_target(
                report=MagicMock(), settings=settings,
                job_id=operation.job_id, operation_id=operation.operation_id,
            )
    upload.assert_not_called()
    assert list_upload_attempts(settings) == ()
    assert list_operations(settings)[0].state == "failed"


def test_persisted_metadata_tamper_is_rejected_before_attempt(tmp_path: Path) -> None:
    settings, operation = _reserved(
        tmp_path,
        publish_at=datetime(2099, 8, 2, tzinfo=timezone.utc),
    )
    queue_path = tmp_path / "_schedule" / "queue.json"
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    payload["operations"][0]["content"]["title"] = "  改変タイトル  "
    queue_path.write_text(json.dumps(payload), encoding="utf-8")
    with (
        patch(
            "yt_live_kit.services.upload_queue.fetch_mine_channel",
            return_value=operation.content.channel,
        ),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.upload_video_resumable") as upload,
    ):
        with pytest.raises(UploadQueueError, match="canonical"):
            upload_job_target(
                report=MagicMock(), settings=settings,
                job_id=operation.job_id, operation_id=operation.operation_id,
            )
    upload.assert_not_called()
    assert list_upload_attempts(settings) == ()
    assert list_operations(settings)[0].state == "failed"


def test_attempt_save_failure_stops_before_upload_session(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    real_replace = os.replace

    def fail_attempt_replace(source, destination):
        if Path(destination).name == "upload_attempts.json":
            raise OSError("attempt fault")
        return real_replace(source, destination)

    with (
        patch("yt_live_kit.services.upload_queue.fetch_mine_channel", return_value=operation.content.channel),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.build_upload_body"),
        patch("yt_live_kit.services.upload_queue.os.replace", side_effect=fail_attempt_replace),
        patch("yt_live_kit.services.upload_queue.upload_video_resumable") as upload,
    ):
        with pytest.raises(UploadQueueError, match="保存"):
            upload_job_target(
                report=MagicMock(), settings=settings,
                job_id=operation.job_id, operation_id=operation.operation_id,
            )
    upload.assert_not_called()
    assert list_operations(settings)[0].state == "failed"


def test_crash_after_attempt_save_recovers_without_resend(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    with (
        patch("yt_live_kit.services.upload_queue.fetch_mine_channel", return_value=operation.content.channel),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.build_upload_body"),
        patch(
            "yt_live_kit.services.upload_queue.upload_video_resumable",
            side_effect=RuntimeError("process crash"),
        ) as upload,
    ):
        with pytest.raises(UploadQueueError, match="attempt 記録後"):
            upload_job_target(
                report=MagicMock(), settings=settings,
                job_id=operation.job_id, operation_id=operation.operation_id,
            )
    assert len(list_upload_attempts(settings)) == 1
    assert list_operations(settings)[0].state == "needs_reconciliation"
    assert recover_upload_operations(settings) == ()
    assert upload.call_count == 1


def test_api_validation_exception_after_attempt_becomes_reconciliation(
    tmp_path: Path,
) -> None:
    settings, operation = _reserved(tmp_path)
    with (
        patch(
            "yt_live_kit.services.upload_queue.fetch_mine_channel",
            return_value=operation.content.channel,
        ),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.build_upload_body"),
        patch(
            "yt_live_kit.services.upload_queue.upload_video_resumable",
            side_effect=YouTubeAPIError("API 前の再検証に失敗しました。"),
        ),
    ):
        with pytest.raises(UploadQueueError, match="attempt 記録後"):
            upload_job_target(
                report=MagicMock(), settings=settings,
                job_id=operation.job_id, operation_id=operation.operation_id,
            )
    restored = list_operations(settings)[0]
    assert restored.state == "needs_reconciliation"
    assert len(list_upload_attempts(settings)) == 1


def test_poll_api_failure_keeps_uploaded_operation_and_result_ref(
    tmp_path: Path,
) -> None:
    settings, operation = _reserved(tmp_path)
    with (
        patch(
            "yt_live_kit.services.upload_queue.fetch_mine_channel",
            return_value=operation.content.channel,
        ),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.build_upload_body"),
        patch(
            "yt_live_kit.services.upload_queue.upload_video_resumable",
            return_value=UploadResult(state="uploaded", video_id="yt1", error=None),
        ),
        patch(
            "yt_live_kit.services.upload_queue.poll_processing_status",
            side_effect=YouTubeAPIError("poll API 失敗"),
        ),
        patch("yt_live_kit.services.jobs.update_job") as update_job,
    ):
        with pytest.raises(UploadQueueError, match="アップロードは完了"):
            upload_job_target(
                report=MagicMock(), settings=settings,
                job_id=operation.job_id, operation_id=operation.operation_id,
            )
    restored = list_operations(settings)[0]
    assert restored.state == "uploaded"
    assert restored.video_id == "yt1"
    assert restored.error is None
    update_job.assert_called_once_with(
        operation.job_id, settings=settings, result_ref=operation.operation_id
    )


def test_poll_network_failure_marks_job_failed_but_keeps_uploaded_result(
    tmp_path: Path,
) -> None:
    settings, operation = _reserved(tmp_path)
    service = MagicMock()
    service.videos().list().execute.side_effect = TimeoutError("network timeout")

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    with (
        patch(
            "yt_live_kit.services.upload_queue.fetch_mine_channel",
            return_value=operation.content.channel,
        ),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch("yt_live_kit.services.upload_queue.build_upload_body"),
        patch(
            "yt_live_kit.services.upload_queue.upload_video_resumable",
            return_value=UploadResult(state="uploaded", video_id="yt1", error=None),
        ),
        patch("yt_live_kit.services.youtube_api._build_service", return_value=service),
        patch("yt_live_kit.services.jobs.threading.Thread", ImmediateThread),
    ):
        job_id = start_job(
            "upload",
            upload_job_target,
            settings=settings,
            requested_job_id=operation.job_id,
            operation_id=operation.operation_id,
        )

    restored_operation = list_operations(settings)[0]
    restored_job = read_job(job_id, settings)
    assert restored_operation.state == "uploaded"
    assert restored_operation.video_id == "yt1"
    assert restored_operation.error is None
    assert restored_job is not None
    assert restored_job.status == "failed"
    assert restored_job.result_ref == operation.operation_id
    assert "アップロードは完了" in (restored_job.error or "")
    assert "手動" in (restored_job.error or "")


def test_concurrent_duplicate_operation_has_one_winner(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    content = _snapshot(tmp_path)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def reserve() -> None:
        barrier.wait()
        try:
            create_reserved_operation(
                operation_id="same-op", job_id="same-job",
                source_video_id="source", source_kind="short", clip_id="clip",
                content=content, settings=settings,
            )
            outcomes.append("saved")
        except UploadQueueError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["rejected", "saved"]
    assert len(list_operations(settings)) == 1


def test_atomic_replace_failure_preserves_previous_queue(tmp_path: Path) -> None:
    settings, _operation = _reserved(tmp_path)
    queue_path = tmp_path / "_schedule" / "queue.json"
    before = queue_path.read_bytes()
    with patch("yt_live_kit.services.upload_queue.os.replace", side_effect=OSError("fault")):
        with pytest.raises(UploadQueueError, match="保存"):
            create_reserved_operation(
                operation_id="op2", job_id="job2", source_video_id="source",
                source_kind="short", clip_id="clip2", content=_snapshot(tmp_path),
                settings=settings,
            )
    assert queue_path.read_bytes() == before


def test_advisory_file_lock_is_used(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with patch("yt_live_kit.services.upload_queue.fcntl.flock") as flock:
        assert list_operations(settings) == ()
    assert flock.call_count == 2


def test_recovery_job_mismatch_becomes_reconciliation(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    attempts_path = tmp_path / "_schedule" / "upload_attempts.json"
    attempts_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempts": [{
                    "attempt_date_la": "2026-07-31",
                    "operation_id": operation.operation_id,
                    "job_id": "different-job",
                    "attempted_at": "2026-08-01T00:00:00Z",
                }],
            }
        ),
        encoding="utf-8",
    )
    recovered = recover_upload_operations(settings)
    assert recovered[0].state == "needs_reconciliation"
    assert "job ID" in (recovered[0].error or "")


def test_studio_confirmation_is_separate_from_upload_state(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    transition_operation(operation.operation_id, "uploading", settings)
    record_upload_attempt(operation.operation_id, operation.job_id, settings)
    transition_operation(
        operation.operation_id, "uploaded", settings, video_id="yt1"
    )
    confirmed = set_publication_eligibility(
        operation.operation_id, "confirmed_private_lock", settings
    )
    assert confirmed.state == "uploaded"
    assert confirmed.publication_eligibility == "confirmed_private_lock"


def test_poll_history_round_trips_without_losing_old_observations(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    transition_operation(
        operation.operation_id,
        "uploading",
        settings,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    record_upload_attempt(
        operation.operation_id,
        operation.job_id,
        settings,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    transition_operation(
        operation.operation_id,
        "uploaded",
        settings,
        video_id="yt1",
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    first = UploadStatusObservation(
        polled_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        phase="processing",
        status={"privacyStatus": "private"},
        processing_details={"processingStatus": "processing"},
        classification="processing",
        error=None,
    )
    second = UploadStatusObservation(
        polled_at=datetime(2026, 8, 1, 0, 1, tzinfo=timezone.utc),
        phase="processing",
        status={"privacyStatus": "private"},
        processing_details={"processingStatus": "succeeded"},
        classification="processing_succeeded",
        error=None,
    )
    append_poll_observation(operation.operation_id, first, settings)
    append_poll_observation(operation.operation_id, second, settings)
    restored = list_operations(settings)[0]
    assert restored.poll_history == (first, second)
    assert restored.latest_observation == second


def test_observation_is_deeply_immutable_and_json_round_trips() -> None:
    raw_status = {"privacyStatus": "private", "nested": {"items": ["a"]}}
    observation = UploadStatusObservation(
        polled_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        phase="processing",
        status=raw_status,
        processing_details={"processingProgress": {"parts": [1, 2]}},
        classification="processing",
        error=None,
    )
    raw_status["privacyStatus"] = "public"
    raw_status["nested"]["items"].append("b")
    assert observation.status["privacyStatus"] == "private"
    assert observation.status["nested"]["items"] == ("a",)
    with pytest.raises(TypeError):
        observation.status["privacyStatus"] = "public"  # type: ignore[index]
    restored = UploadStatusObservation.model_validate_json(
        observation.model_dump_json()
    )
    assert restored == observation


def test_observation_rejects_missing_unknown_and_phase_mismatch() -> None:
    base = {
        "polled_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "phase": "processing",
        "status": {},
        "processing_details": {},
        "classification": "processing",
        "error": None,
    }
    missing = dict(base)
    missing.pop("status")
    with pytest.raises(ValidationError):
        UploadStatusObservation.model_validate(missing)
    with pytest.raises(ValidationError):
        UploadStatusObservation.model_validate({**base, "unknown": True})
    with pytest.raises(ValidationError, match="phase"):
        UploadStatusObservation.model_validate(
            {**base, "classification": "published"}
        )
    with pytest.raises(ValidationError, match="エラー"):
        UploadStatusObservation.model_validate(
            {**base, "classification": "processing_timeout"}
        )


def test_append_poll_requires_uploaded_and_never_moves_updated_at_backward(
    tmp_path: Path,
) -> None:
    settings, operation = _reserved(tmp_path)
    observation = UploadStatusObservation(
        polled_at=datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc),
        phase="processing",
        status={},
        processing_details={"processingStatus": "succeeded"},
        classification="processing_succeeded",
        error=None,
    )
    with pytest.raises(UploadQueueError, match="アップロード済み"):
        append_poll_observation(operation.operation_id, observation, settings)
    transition_operation(
        operation.operation_id,
        "uploading",
        settings,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    record_upload_attempt(
        operation.operation_id,
        operation.job_id,
        settings,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    transition_operation(
        operation.operation_id,
        "uploaded",
        settings,
        video_id="yt1",
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    set_publication_eligibility(
        operation.operation_id,
        "no_private_lock",
        settings,
        now=datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc),
    )
    updated = append_poll_observation(operation.operation_id, observation, settings)
    assert updated.updated_at == datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "action",
    ["create", "transition", "count_attempts", "record_attempt", "eligibility"],
)
def test_public_timestamp_boundaries_reject_naive_datetime(
    tmp_path: Path, action: str
) -> None:
    settings, operation = _reserved(tmp_path)
    naive = datetime(2026, 8, 1)
    with pytest.raises(UploadQueueError, match="タイムゾーン"):
        if action == "create":
            create_reserved_operation(
                operation_id="naive-op",
                job_id="naive-job",
                source_video_id="source",
                source_kind="short",
                clip_id="clip",
                content=_snapshot(tmp_path),
                settings=settings,
                now=naive,
            )
        elif action == "transition":
            transition_operation(operation.operation_id, "uploading", settings, now=naive)
        elif action == "count_attempts":
            count_upload_attempts(settings, now=naive)
        elif action == "record_attempt":
            record_upload_attempt(
                operation.operation_id, operation.job_id, settings, now=naive
            )
        else:
            transition_operation(
                operation.operation_id,
                "uploading",
                settings,
                now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            record_upload_attempt(
                operation.operation_id,
                operation.job_id,
                settings,
                now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            transition_operation(
                operation.operation_id,
                "uploaded",
                settings,
                video_id="yt1",
                now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            set_publication_eligibility(
                operation.operation_id,
                "no_private_lock",
                settings,
                now=naive,
            )


def test_transition_rejects_aware_timestamp_regression(tmp_path: Path) -> None:
    settings, operation = _reserved(tmp_path)
    with pytest.raises(UploadQueueError, match="過去"):
        transition_operation(
            operation.operation_id,
            "uploading",
            settings,
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        )
