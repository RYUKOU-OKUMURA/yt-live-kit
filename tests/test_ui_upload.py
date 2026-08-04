"""P2 予約投稿 component の表示・確認境界テスト."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadOperation,
    UploadStatusObservation,
)
from yt_live_kit.services.description import (
    SHORTS_DESCRIPTION_CTA,
    ShortsDescriptionBuild,
    ShortsDescriptionRequirements,
)
from yt_live_kit.services.schedule import (
    SchedulePolicy,
    UploadPreview,
    save_schedule_policy,
)
from yt_live_kit.services.upload_queue import (
    RelatedVideoSummary,
    UploadQueueError,
    save_reserved_operation,
)
from yt_live_kit.ui.components import upload
from yt_live_kit.ui.controllers.upload import LineUploadAdapter
from yt_live_kit.ui.view_models.upload import (
    latest_operations_for_source,
    project_source_operations,
)


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _quality_build(description: str = "説明文\n元動画\nhttps://example.com/source\n" + SHORTS_DESCRIPTION_CTA) -> ShortsDescriptionBuild:
    return ShortsDescriptionBuild(
        description=description,
        requirements=ShortsDescriptionRequirements(
            generated_description="説明文",
            source_title="元動画",
            source_url="https://example.com/source",
            fixed_cta=SHORTS_DESCRIPTION_CTA,
            template_bytes_fingerprint="a" * 64,
            meta_json_fingerprint="b" * 64,
            generated_description_occurrences=1,
            source_title_occurrences=1,
            source_url_occurrences=1,
            fixed_cta_line_occurrences=1,
        ),
    )


def _preview(tmp_path: Path) -> UploadPreview:
    path = (tmp_path / "short.mp4").resolve()
    path.write_bytes(b"video")
    return UploadPreview(
        source_video_id="source-1",
        source_kind="shorts_queue",
        clip_id="clip-1",
        channel=UploadChannel(channel_id="channel-1", title="確認チャンネル"),
        video_path=path,
        file_size=5,
        file_mtime_ns=path.stat().st_mtime_ns,
        duration_sec=30,
        title="予約タイトル",
        description="説明文全文",
        tags=("タグ1", "タグ2"),
        policy=SchedulePolicy(daily_time="09:00"),
        publish_at=NOW + timedelta(days=1),
        publish_at_utc_z="2026-08-02T00:00:00Z",
        privacy_status="private",
        notify_subscribers=False,
        attempt_count_la=2,
        attempt_limit=100,
        fingerprint="a" * 64,
    )


def _operation(tmp_path: Path, *, state: str = "reserved") -> UploadOperation:
    preview = _preview(tmp_path)
    content = UploadContentSnapshot(
        channel=preview.channel,
        video_path=preview.video_path,
        file_size=preview.file_size,
        file_mtime_ns=preview.file_mtime_ns,
        duration_sec=preview.duration_sec,
        title=preview.title,
        description=preview.description,
        tags=preview.tags,
        publish_at=preview.publish_at,
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=False,
        contains_synthetic_media=False,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=NOW,
    )
    terminal = state in {"uploaded", "failed", "needs_reconciliation"}
    return UploadOperation(
        operation_id="operation-1",
        source_video_id="source-1",
        source_kind="shorts_queue",
        clip_id="clip-1",
        video_path=preview.video_path,
        content=content,
        state=state,
        job_id="job-1",
        video_id="youtube-1" if state == "uploaded" else None,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW if state in {"uploading", "uploaded", "needs_reconciliation"} else None,
        finished_at=NOW if terminal else None,
        error=(
            "手動照合してください。"
            if state == "needs_reconciliation"
            else "投稿に失敗しました。" if state == "failed" else None
        ),
        poll_history=(),
        publication_eligibility="unknown",
    )


@pytest.mark.parametrize("active_state", ["reserved", "uploading"])
def test_tracking_projection_keeps_older_active_operation_for_same_clip(
    tmp_path: Path,
    active_state: str,
) -> None:
    older = _operation(tmp_path, state=active_state).model_copy(
        update={"operation_id": "operation-active"}
    )
    newer = _operation(tmp_path, state="failed").model_copy(
        update={
            "operation_id": "operation-newer",
            "created_at": NOW + timedelta(hours=1),
            "updated_at": NOW + timedelta(hours=1),
            "started_at": NOW + timedelta(hours=1),
            "finished_at": NOW + timedelta(hours=1),
        }
    )

    projection = project_source_operations((newer, older), "source-1")

    assert tuple(item.operation_id for item in projection.operations) == (
        "operation-newer",
        "operation-active",
    )
    assert projection.operation_ids == {
        "operation-newer",
        "operation-active",
    }
    assert projection.blocking_clip_ids == {"clip-1"}


def test_tracking_projection_keeps_older_reconciliation_and_unpolled_upload(
    tmp_path: Path,
) -> None:
    reconciliation = _operation(tmp_path, state="needs_reconciliation").model_copy(
        update={"operation_id": "operation-reconcile"}
    )
    uploaded = _operation(tmp_path, state="uploaded").model_copy(
        update={
            "operation_id": "operation-uploaded",
            "created_at": NOW + timedelta(minutes=1),
            "updated_at": NOW + timedelta(minutes=1),
            "started_at": NOW + timedelta(minutes=1),
            "finished_at": NOW + timedelta(minutes=1),
            "related_video_status": "confirmed",
            "related_video_confirmed_at": NOW + timedelta(minutes=1),
        }
    )
    scheduled_observation = UploadStatusObservation(
        polled_at=NOW + timedelta(minutes=2),
        phase="publication",
        status={"privacyStatus": "private"},
        processing_details={},
        classification="scheduled",
        error=None,
    )
    publication_pending = uploaded.model_copy(
        update={
            "operation_id": "operation-publication-pending",
            "created_at": NOW + timedelta(minutes=2),
            "updated_at": NOW + timedelta(minutes=2),
            "started_at": NOW + timedelta(minutes=2),
            "finished_at": NOW + timedelta(minutes=2),
            "poll_history": (scheduled_observation,),
        }
    )
    newer = _operation(tmp_path, state="failed").model_copy(
        update={
            "operation_id": "operation-newest",
            "created_at": NOW + timedelta(hours=1),
            "updated_at": NOW + timedelta(hours=1),
            "started_at": NOW + timedelta(hours=1),
            "finished_at": NOW + timedelta(hours=1),
        }
    )

    projection = project_source_operations(
        (newer, reconciliation, uploaded, publication_pending),
        "source-1",
    )

    assert {item.operation_id for item in projection.operations} == {
        "operation-newest",
        "operation-reconcile",
        "operation-uploaded",
        "operation-publication-pending",
    }
    assert projection.blocking_clip_ids == {"clip-1"}


def test_tracking_projection_drops_superseded_terminal_upload_unless_related_pending(
    tmp_path: Path,
) -> None:
    publication = UploadStatusObservation(
        polled_at=NOW + timedelta(minutes=1),
        phase="publication",
        status={"privacyStatus": "public"},
        processing_details={},
        classification="published",
        error=None,
    )
    terminal = _operation(tmp_path, state="uploaded").model_copy(
        update={
            "operation_id": "operation-terminal",
            "poll_history": (publication,),
            "related_video_status": "confirmed",
            "related_video_confirmed_at": NOW + timedelta(minutes=1),
        }
    )
    related_pending = terminal.model_copy(
        update={
            "operation_id": "operation-related-pending",
            "related_video_status": "pending",
            "related_video_confirmed_at": None,
        }
    )
    newer = _operation(tmp_path, state="failed").model_copy(
        update={
            "operation_id": "operation-newer",
            "created_at": NOW + timedelta(hours=1),
            "updated_at": NOW + timedelta(hours=1),
            "started_at": NOW + timedelta(hours=1),
            "finished_at": NOW + timedelta(hours=1),
        }
    )

    terminal_only = latest_operations_for_source((terminal, newer), "source-1")
    pending = latest_operations_for_source((related_pending, newer), "source-1")
    terminal_projection = project_source_operations((terminal, newer), "source-1")

    assert tuple(item.operation_id for item in terminal_only) == ("operation-newer",)
    assert {item.operation_id for item in pending} == {
        "operation-newer",
        "operation-related-pending",
    }
    assert terminal_projection.blocking_clip_ids == {"clip-1"}


def test_upload_section_blocks_new_reservation_for_older_upload_behind_newer_failure(
    tmp_path: Path,
) -> None:
    older = _operation(tmp_path, state="uploaded").model_copy(
        update={"operation_id": "operation-uploaded"}
    )
    newer = _operation(tmp_path, state="failed").model_copy(
        update={
            "operation_id": "operation-failed",
            "created_at": NOW + timedelta(hours=1),
            "updated_at": NOW + timedelta(hours=1),
            "started_at": NOW + timedelta(hours=1),
            "finished_at": NOW + timedelta(hours=1),
        }
    )
    item = MagicMock(
        status="succeeded",
        output_path=older.video_path,
        title_candidates=("検索タイトル", "仕事タイトル", "好奇心タイトル"),
        target_id="clip-1",
        description="説明文",
        tags=("タグ",),
    )
    result = MagicMock(
        status="done",
        items=(item,),
        job_id="shorts-job",
        clip_specs=(),
    )
    settings = Settings(data_dir=tmp_path)
    policy = SchedulePolicy(daily_times=["09:00"])
    requested = datetime(2026, 8, 5, 9, 0, tzinfo=ZoneInfo(policy.timezone))

    with (
        patch.object(upload, "list_operations", return_value=(newer, older)) as list_ops,
        patch.object(upload, "_render_related_video_summary_panel"),
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot", return_value=(policy, requested)),
        patch.object(upload, "can_reserve_shorts_queue_item", return_value=True),
        patch.object(
            upload,
            "build_shorts_description_for_upload",
            return_value=_quality_build(),
        ),
        patch.object(
            upload,
            "_render_upload_metadata_editor",
            return_value=("予約タイトル", "説明文", ("タグ",)),
        ) as metadata,
        patch.object(
            upload,
            "_render_upload_schedule_editor",
            return_value=requested,
        ) as schedule,
        patch.object(upload, "render_upload_operation") as render_operation,
        patch.object(upload, "_resolve_operation") as resolve,
        patch.object(upload, "_open_preview") as open_preview,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
        patch.object(upload.st, "warning") as warning,
        patch.object(upload.st, "button", return_value=True) as button,
    ):
        upload.render_upload_section("source-1", settings)

    list_ops.assert_called_once_with(settings)
    assert render_operation.call_count == 2
    resolve.assert_not_called()
    assert metadata.call_args.kwargs["disabled"] is True
    assert schedule.call_args.kwargs["disabled"] is True
    assert button.call_args.kwargs["disabled"] is True
    open_preview.assert_not_called()
    assert any(
        "既存の投稿 operation を追跡中" in call.args[0]
        for call in warning.call_args_list
    )


@contextmanager
def _container(*args, **kwargs):
    yield


def test_dialog_displays_complete_preview_and_defaults_are_unselected(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    segmented = MagicMock(side_effect=[None, None])
    button = MagicMock(return_value=False)
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write") as write,
        patch.object(upload.st, "code") as code,
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", segmented),
        patch.object(upload.st, "checkbox", return_value=False) as checkbox,
        patch.object(upload.st, "button", button),
        patch.object(upload, "confirm_and_start_upload") as confirm,
    ):
        upload.upload_preview_dialog.__wrapped__(preview, Settings(data_dir=tmp_path), "open-1")

    assert call(str(preview.video_path)) in code.call_args_list
    rendered = "\n".join(str(item.args[0]) for item in write.call_args_list)
    assert "channel-1" in rendered
    assert "予約タイトル" in rendered
    assert "2026-08-02T00:00:00Z" in rendered
    assert "private" in rendered and "false" in rendered
    assert all(item.kwargs["default"] is None for item in segmented.call_args_list)
    assert checkbox.call_args.kwargs["value"] is False
    assert button.call_args.kwargs["disabled"] is True
    confirm.assert_not_called()


def test_dialog_confirm_passes_explicit_choices_and_stores_operation(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    operation = _operation(tmp_path)
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", side_effect=["はい", "いいえ"]),
        patch.object(upload.st, "checkbox", return_value=True),
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "success"),
        patch.object(upload.st, "rerun"),
        patch.object(upload, "confirm_and_start_upload", return_value=operation) as confirm,
        patch.object(upload, "_store_operation_id") as store,
        patch.object(upload, "set_active_job_id") as set_job,
    ):
        upload.upload_preview_dialog.__wrapped__(preview, Settings(data_dir=tmp_path), "open-1")

    assert confirm.call_args.kwargs["self_declared_made_for_kids"] is True
    assert confirm.call_args.kwargs["contains_synthetic_media"] is False
    assert confirm.call_args.kwargs["community_guidelines_confirmed"] is True
    assert confirm.call_args.args[0].description == preview.description
    store.assert_called_once_with("source-1", "operation-1")
    set_job.assert_called_once_with("job-1")


def test_dialog_runs_confirm_inside_reservation_transaction(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    operation = _operation(tmp_path)
    transaction = MagicMock(
        side_effect=lambda _clip_id, _path, start_upload: start_upload()
    )
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", side_effect=["はい", "いいえ"]),
        patch.object(upload.st, "checkbox", return_value=True),
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "success"),
        patch.object(upload.st, "rerun"),
        patch.object(upload, "confirm_and_start_upload", return_value=operation) as confirm,
        patch.object(upload, "_store_operation_id"),
        patch.object(upload, "set_active_job_id"),
    ):
        upload.upload_preview_dialog.__wrapped__(
            preview,
            Settings(data_dir=tmp_path),
            "open-transaction",
            reservation_transaction=transaction,
        )

    transaction.assert_called_once()
    assert transaction.call_args.args[:2] == ("clip-1", preview.video_path)
    confirm.assert_called_once()


def test_line_adapter_revalidates_and_wraps_confirm_as_one_required_pair(
    tmp_path: Path,
) -> None:
    preview = _preview(tmp_path)
    operation = _operation(tmp_path)
    validate = MagicMock()
    transaction = MagicMock(
        side_effect=lambda _clip_id, _path, start_upload: start_upload()
    )
    adapter = LineUploadAdapter(validate, transaction)

    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", side_effect=["いいえ", "いいえ"]),
        patch.object(upload.st, "checkbox", return_value=True),
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "success"),
        patch.object(upload.st, "rerun"),
        patch.object(upload, "confirm_and_start_upload", return_value=operation) as confirm,
        patch.object(upload, "_store_operation_id"),
        patch.object(upload, "set_active_job_id"),
    ):
        upload.upload_preview_dialog.__wrapped__(
            preview,
            Settings(data_dir=tmp_path),
            "line-adapter",
            line_adapter=adapter,
        )

    validate.assert_called_once_with("clip-1", preview.video_path)
    transaction.assert_called_once()
    assert transaction.call_args.args[:2] == ("clip-1", preview.video_path)
    confirm.assert_called_once()


def test_dialog_revalidates_line_immediately_before_confirm(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    before_confirm = MagicMock(
        side_effect=upload.LineStateError("完成動画が確認後に変わりました。")
    )
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", side_effect=["はい", "いいえ"]),
        patch.object(upload.st, "checkbox", return_value=True),
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "error") as error,
        patch.object(upload, "confirm_and_start_upload") as confirm,
    ):
        upload.upload_preview_dialog.__wrapped__(
            preview,
            Settings(data_dir=tmp_path),
            "open-1",
            before_confirm=before_confirm,
        )

    before_confirm.assert_called_once_with("clip-1", preview.video_path)
    confirm.assert_not_called()
    assert "完成動画が確認後に変わりました" in error.call_args.args[0]


def test_started_upload_keeps_job_tracking_when_line_recording_fails(
    tmp_path: Path,
) -> None:
    preview = _preview(tmp_path)
    operation = _operation(tmp_path)
    on_started = MagicMock(side_effect=upload.LineStateError("CAS conflict"))
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", side_effect=["はい", "いいえ"]),
        patch.object(upload.st, "checkbox", return_value=True),
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "error") as error,
        patch.object(upload, "confirm_and_start_upload", return_value=operation),
        patch.object(upload, "_store_operation_id") as store,
        patch.object(upload, "_store_post_start_error") as store_error,
        patch.object(upload, "set_active_job_id") as set_job,
        patch.object(upload.st, "rerun") as rerun,
    ):
        upload.upload_preview_dialog.__wrapped__(
            preview,
            Settings(data_dir=tmp_path),
            "open-1",
            on_operation_started=on_started,
        )

    store.assert_called_once_with("source-1", "operation-1")
    set_job.assert_called_once_with("job-1")
    on_started.assert_called_once()
    assert "予約投稿ジョブは開始済み" in error.call_args.args[0]
    store_error.assert_called_once()
    assert "予約投稿ジョブは開始済み" in store_error.call_args.args[1]
    rerun.assert_called_once()


def test_dialog_does_not_confirm_when_button_is_not_clicked(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", side_effect=["はい", "いいえ"]),
        patch.object(upload.st, "checkbox", return_value=True),
        patch.object(upload.st, "button", return_value=False),
        patch.object(upload, "confirm_and_start_upload") as confirm,
    ):
        upload.upload_preview_dialog.__wrapped__(preview, Settings(data_dir=tmp_path), "open-1")
    confirm.assert_not_called()


def test_open_preview_uses_the_same_line_adapter_before_opening_dialog(
    tmp_path: Path,
) -> None:
    preview = _preview(tmp_path)
    item = MagicMock(
        output_path=preview.video_path,
        target_id="clip-1",
        title_candidates=(preview.title,),
        description=preview.description,
        tags=preview.tags,
    )
    validate = MagicMock()
    adapter = LineUploadAdapter(validate, MagicMock())
    with (
        patch.object(upload, "build_upload_preview", return_value=preview),
        patch.object(upload, "upload_preview_dialog") as dialog,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            line_adapter=adapter,
        )

    validate.assert_called_once_with("clip-1", preview.video_path)
    assert dialog.call_args.kwargs["line_adapter"] is adapter


def test_each_dialog_open_uses_new_widget_keys_and_unselected_defaults(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    item = MagicMock(
        output_path=preview.video_path,
        target_id="clip-1",
        title_candidates=(preview.title,),
        description=preview.description,
        tags=preview.tags,
    )
    with (
        patch.object(upload, "build_upload_preview", return_value=preview),
        patch.object(upload, "upload_preview_dialog") as dialog,
        patch.object(upload.uuid, "uuid4", side_effect=[MagicMock(hex="nonce-1"), MagicMock(hex="nonce-2")]),
    ):
        upload._open_preview("source-1", item, Settings(data_dir=tmp_path))
        upload._open_preview("source-1", item, Settings(data_dir=tmp_path))
    assert dialog.call_args_list[0].args[2] == "nonce-1"
    assert dialog.call_args_list[1].args[2] == "nonce-2"

    segmented = MagicMock(side_effect=[None, None])
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "code"),
        patch.object(upload.st, "text_area"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "segmented_control", segmented),
        patch.object(upload.st, "checkbox", return_value=False) as checkbox,
        patch.object(upload.st, "button", return_value=False),
    ):
        upload.upload_preview_dialog.__wrapped__(preview, Settings(data_dir=tmp_path), "nonce-2")
    assert all(item.kwargs["default"] is None for item in segmented.call_args_list)
    assert checkbox.call_args.kwargs["value"] is False
    assert "nonce-2" in checkbox.call_args.kwargs["key"]


def test_open_preview_sends_composed_description_to_preview(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    item = MagicMock(
        output_path=preview.video_path,
        target_id="clip-1",
        title_candidates=(preview.title,),
        description="台本の説明文",
        tags=preview.tags,
    )
    with (
        patch.object(upload, "build_shorts_description", return_value="合成後の本文") as compose,
        patch.object(upload, "build_upload_preview", return_value=preview) as build,
        patch.object(upload, "upload_preview_dialog"),
    ):
        upload._open_preview("source-1", item, Settings(data_dir=tmp_path), start_ms=90_000)

    assert compose.call_args.args[0] == "台本の説明文"
    assert compose.call_args.kwargs["video_id"] == "source-1"
    assert compose.call_args.kwargs["start_ms"] == 90_000
    assert build.call_args.kwargs["description"] == "合成後の本文"


def test_metadata_editor_supports_title_candidates_free_edit_and_native_tags() -> None:
    item = MagicMock(
        target_id="clip-1",
        title_candidates=("候補 1", "候補 2", "候補 2"),
        tags=("既存 1", "既存 2"),
    )
    with (
        patch.object(upload.st, "selectbox", return_value=1) as selectbox,
        patch.object(upload.st, "text_input", return_value="自由編集タイトル") as text_input,
        patch.object(upload.st, "text_area", return_value="編集後の説明文") as text_area,
        patch.object(upload.st, "multiselect", return_value=["既存 2", "追加タグ"]) as multiselect,
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "warning") as warning,
    ):
        edited = upload._render_upload_metadata_editor(
            item,
            job_id="job-1",
            initial_description="合成済みの説明文",
        )

    assert edited == ("自由編集タイトル", "編集後の説明文", ("既存 2", "追加タグ"))
    assert selectbox.call_args.args[1] == (0, 1, 2)
    assert selectbox.call_args.kwargs["format_func"](0) == "検索明快型: 候補 1"
    assert selectbox.call_args.kwargs["format_func"](1) == "仕事影響型: 候補 2"
    assert selectbox.call_args.kwargs["format_func"](2) == "好奇心型: 候補 2"
    assert text_input.call_args.kwargs["value"] == "候補 2"
    assert text_input.call_args.kwargs["max_chars"] == 100
    assert text_area.call_args.kwargs["value"] == "合成済みの説明文"
    assert multiselect.call_args.kwargs["default"] == ["既存 1", "既存 2"]
    assert multiselect.call_args.kwargs["accept_new_options"] is True
    assert any("重複" in item.args[0] for item in warning.call_args_list)


def test_title_direction_renderer_shows_fixed_labels_length_warning_and_legacy_guidance() -> None:
    item = MagicMock(title_candidates=("短", "二つ目の候補"))
    with (
        patch.object(upload.st, "markdown") as markdown,
        patch.object(upload.st, "write"),
        patch.object(upload.st, "warning") as warning,
        patch.object(upload.st, "info") as info,
    ):
        upload._render_title_candidate_directions(item)

    rendered = "\n".join(str(item.args[0]) for item in markdown.call_args_list)
    assert "検索明快型" in rendered
    assert "仕事影響型" in rendered
    assert "好奇心型" in rendered
    warning_text = "\n".join(str(item.args[0]) for item in warning.call_args_list)
    assert "18〜32" in warning_text
    assert "保存は可能" in warning_text
    assert "手動補完" in warning_text
    assert "再生成" in info.call_args.args[0]


def test_title_length_warning_does_not_block_p6_preview(tmp_path: Path) -> None:
    path = (tmp_path / "short.mp4").resolve()
    path.write_bytes(b"video")
    item = MagicMock(
        output_path=path,
        target_id="clip-1",
        title_candidates=("短", "二つ目の候補", "三つ目の候補"),
        description="説明文",
        tags=(),
    )
    preview = _preview(tmp_path)
    with (
        patch.object(upload, "build_shorts_description_for_upload", return_value=_quality_build()),
        patch.object(upload, "build_upload_preview", return_value=preview) as build,
        patch.object(upload, "upload_preview_dialog") as dialog,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="短",
            p6_quality_gate=True,
        )

    assert build.call_args.kwargs["title"] == "短"
    dialog.assert_called_once()


def test_legacy_title_candidates_can_open_preview_after_manual_completion(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "short.mp4").resolve()
    path.write_bytes(b"video")
    item = MagicMock(
        output_path=path,
        target_id="clip-1",
        title_candidates=("legacy候補1", "legacy候補2"),
        description="説明文",
        tags=(),
    )
    preview = _preview(tmp_path)
    with (
        patch.object(upload, "build_shorts_description_for_upload", return_value=_quality_build()),
        patch.object(upload, "build_upload_preview", return_value=preview) as build,
        patch.object(upload, "upload_preview_dialog") as dialog,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="手動補完した最終タイトル",
            p6_quality_gate=True,
        )

    assert build.call_args.kwargs["title"] == "手動補完した最終タイトル"
    assert build.call_args.kwargs["requirements"] is not None
    dialog.assert_called_once()


def test_p6_editor_text_equal_to_base_description_is_validated_not_replaced(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "short.mp4").resolve()
    path.write_bytes(b"video")
    item = MagicMock(
        output_path=path,
        target_id="clip-1",
        title_candidates=("検索明快なタイトル", "仕事影響のタイトル", "好奇心のタイトル"),
        description="説明文",
        tags=(),
    )
    with (
        patch.object(upload, "build_shorts_description_for_upload", return_value=_quality_build()),
        patch.object(upload, "build_upload_preview") as build,
        patch.object(upload, "upload_preview_dialog") as dialog,
        patch.object(upload.st, "error") as error,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="検索明快なタイトル",
            description=item.description,
            p6_quality_gate=True,
        )

    build.assert_not_called()
    dialog.assert_not_called()
    error.assert_called_once()
    error_message = error.call_args.args[0]
    assert "元動画タイトル" in error_message
    assert "開始秒付き元動画 URL" in error_message
    assert "チャンネル登録 CTA" in error_message


def test_schedule_editor_uses_native_date_time_and_job_item_unique_keys() -> None:
    policy = SchedulePolicy(daily_times=["09:00"], timezone="America/New_York")
    initial = datetime(2026, 8, 5, 14, 30, tzinfo=ZoneInfo(policy.timezone))
    date_input = MagicMock(return_value=date(2026, 8, 6))
    time_input = MagicMock(return_value=time(16, 45))
    with (
        patch.object(upload.st, "date_input", date_input),
        patch.object(upload.st, "time_input", time_input),
        patch.object(upload.st, "caption") as caption,
    ):
        first = upload._render_upload_schedule_editor(
            job_id="job-1",
            item_id="clip-1",
            policy=policy,
            initial_publish_at=initial,
            disabled=False,
        )
        upload._render_upload_schedule_editor(
            job_id="job-1",
            item_id="clip-2",
            policy=policy,
            initial_publish_at=initial,
            disabled=False,
        )

    assert first == datetime(2026, 8, 6, 16, 45, tzinfo=ZoneInfo(policy.timezone))
    assert "America/New_York" in caption.call_args_list[0].args[0]
    assert [item.kwargs["key"] for item in date_input.call_args_list] == [
        "upload_publish_date_job-1_clip-1",
        "upload_publish_date_job-1_clip-2",
    ]
    assert [item.kwargs["key"] for item in time_input.call_args_list] == [
        "upload_publish_time_job-1_clip-1",
        "upload_publish_time_job-1_clip-2",
    ]
    assert all(item.kwargs["step"] == 60 for item in time_input.call_args_list)


@pytest.mark.parametrize(
    ("publish_date", "publish_time"),
    [
        (date(2026, 3, 8), time(2, 30)),
        (date(2026, 11, 1), time(1, 30)),
    ],
)
def test_schedule_editor_rejects_dst_before_preview(
    publish_date: date,
    publish_time: time,
) -> None:
    policy = SchedulePolicy(daily_times=["09:00"], timezone="America/New_York")
    with (
        patch.object(upload.st, "date_input", return_value=publish_date),
        patch.object(upload.st, "time_input", return_value=publish_time),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "error") as error,
    ):
        requested = upload._render_upload_schedule_editor(
            job_id="job-1",
            item_id="clip-1",
            policy=policy,
            initial_publish_at=datetime(
                2026,
                3,
                9,
                9,
                tzinfo=ZoneInfo(policy.timezone),
            ),
            disabled=False,
        )

    assert requested is None
    assert "DST" in error.call_args.args[0]


def test_edited_metadata_builds_new_fixed_preview_snapshot(tmp_path: Path) -> None:
    base = _preview(tmp_path)
    item = MagicMock(
        output_path=base.video_path,
        target_id="clip-1",
        title_candidates=(base.title,),
        description=base.description,
        tags=base.tags,
    )
    mutable_tags = ["編集タグ", "追加タグ"]

    def build_from_edits(**kwargs) -> UploadPreview:
        return base.model_copy(
            update={
                "title": kwargs["title"],
                "description": kwargs["description"],
                "tags": tuple(kwargs["tags"]),
                "fingerprint": "b" * 64,
            }
        )

    with (
        patch.object(upload, "build_shorts_description") as compose,
        patch.object(upload, "build_upload_preview", side_effect=build_from_edits) as build,
        patch.object(upload, "upload_preview_dialog") as dialog,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="編集タイトル",
            description="編集後の説明文",
            tags=mutable_tags,
        )

    compose.assert_not_called()
    assert build.call_args.kwargs["title"] == "編集タイトル"
    assert build.call_args.kwargs["description"] == "編集後の説明文"
    assert build.call_args.kwargs["tags"] == ("編集タグ", "追加タグ")
    fixed_preview = dialog.call_args.args[0]
    mutable_tags.append("ダイアログ後の変更")
    assert fixed_preview.title == "編集タイトル"
    assert fixed_preview.description == "編集後の説明文"
    assert fixed_preview.tags == ("編集タグ", "追加タグ")


def test_open_preview_passes_edited_publish_at_into_new_preview(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    item = MagicMock(
        output_path=preview.video_path,
        target_id="clip-1",
        title_candidates=(preview.title,),
        description=preview.description,
        tags=preview.tags,
    )
    requested = datetime(2026, 8, 5, 15, 45, tzinfo=ZoneInfo("Asia/Tokyo"))
    with (
        patch.object(upload, "build_upload_preview", return_value=preview) as build,
        patch.object(upload, "upload_preview_dialog") as dialog,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="編集タイトル",
            description="編集後の説明文",
            tags=("編集タグ",),
            requested_publish_at=requested,
        )

    assert build.call_args.kwargs["requested_publish_at"] is requested
    dialog.assert_called_once_with(
        preview,
        build.call_args.kwargs["settings"],
        dialog.call_args.args[2],
        None,
        None,
        None,
    )


def test_edit_after_preview_requires_a_new_snapshot_without_mutating_old_one(
    tmp_path: Path,
) -> None:
    base = _preview(tmp_path)
    item = MagicMock(
        output_path=base.video_path,
        target_id="clip-1",
        title_candidates=(base.title,),
        description=base.description,
        tags=base.tags,
    )

    def build_from_edits(**kwargs) -> UploadPreview:
        fingerprint_char = "b" if kwargs["title"] == "最初のタイトル" else "c"
        return base.model_copy(
            update={
                "title": kwargs["title"],
                "description": kwargs["description"],
                "tags": tuple(kwargs["tags"]),
                "fingerprint": fingerprint_char * 64,
            }
        )

    with (
        patch.object(upload, "build_upload_preview", side_effect=build_from_edits),
        patch.object(upload, "upload_preview_dialog") as dialog,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="最初のタイトル",
            description="最初の説明文",
            tags=("最初",),
        )
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="編集後のタイトル",
            description="編集後の説明文",
            tags=("編集後",),
        )

    first_preview = dialog.call_args_list[0].args[0]
    latest_preview = dialog.call_args_list[1].args[0]
    assert first_preview.title == "最初のタイトル"
    assert first_preview.description == "最初の説明文"
    assert first_preview.tags == ("最初",)
    assert latest_preview.title == "編集後のタイトル"
    assert latest_preview.description == "編集後の説明文"
    assert latest_preview.tags == ("編集後",)
    assert first_preview.fingerprint != latest_preview.fingerprint


def test_invalid_edited_metadata_creates_no_dialog_operation_or_job(tmp_path: Path) -> None:
    preview = _preview(tmp_path)
    item = MagicMock(
        output_path=preview.video_path,
        target_id="clip-1",
        title_candidates=(preview.title,),
        description=preview.description,
        tags=preview.tags,
    )
    before_preview = MagicMock()
    reservation_transaction = MagicMock()
    with (
        patch.object(
            upload,
            "build_upload_preview",
            side_effect=upload.ScheduleError("タイトルを入力してください。"),
        ),
        patch.object(upload, "upload_preview_dialog") as dialog,
        patch.object(upload, "confirm_and_start_upload") as confirm,
        patch.object(upload.st, "error") as error,
    ):
        upload._open_preview(
            "source-1",
            item,
            Settings(data_dir=tmp_path),
            title="",
            description="編集後の説明文",
            tags=("タグ",),
            before_preview=before_preview,
            reservation_transaction=reservation_transaction,
        )

    before_preview.assert_called_once_with("clip-1", preview.video_path)
    assert "タイトルを入力してください" in error.call_args.args[0]
    dialog.assert_not_called()
    confirm.assert_not_called()
    reservation_transaction.assert_not_called()


def test_open_preview_shows_japanese_error_and_opens_no_dialog_on_template_error(
    tmp_path: Path,
) -> None:
    preview = _preview(tmp_path)
    item = MagicMock(
        output_path=preview.video_path,
        target_id="clip-1",
        title_candidates=(preview.title,),
        description="台本の説明文",
        tags=preview.tags,
    )
    with (
        patch.object(
            upload,
            "build_shorts_description",
            side_effect=upload.DescriptionError("定型文が長すぎます。"),
        ),
        patch.object(upload, "build_upload_preview") as build,
        patch.object(upload, "upload_preview_dialog") as dialog,
        patch.object(upload.st, "error") as error,
    ):
        upload._open_preview("source-1", item, Settings(data_dir=tmp_path))

    assert "定型文が長すぎます。" in error.call_args.args[0]
    build.assert_not_called()
    dialog.assert_not_called()


def test_clip_start_ms_uses_first_segment_of_matching_spec() -> None:
    result = MagicMock(
        clip_specs=(
            MagicMock(target_id="clip-0", segments=(MagicMock(start_ms=1_000),)),
            MagicMock(
                target_id="clip-1",
                segments=(MagicMock(start_ms=90_000), MagicMock(start_ms=150_000)),
            ),
        )
    )
    assert upload._clip_start_ms(result, "clip-1") == 90_000
    assert upload._clip_start_ms(result, "clip-unknown") is None


def test_upload_section_passes_first_segment_start_to_preview(tmp_path: Path) -> None:
    item = MagicMock(
        status="succeeded",
        title_candidates=("予約タイトル",),
        target_id="clip-1",
    )
    result = MagicMock(
        status="done",
        items=(item,),
        job_id="shorts-job",
        clip_specs=(
            MagicMock(
                target_id="clip-1",
                segments=(MagicMock(start_ms=90_000), MagicMock(start_ms=150_000)),
            ),
        ),
    )
    settings = Settings(data_dir=tmp_path)
    policy = SchedulePolicy(daily_times=["09:00"])
    requested = datetime(2026, 8, 5, 15, 45, tzinfo=ZoneInfo(policy.timezone))
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot", return_value=(policy, requested)),
        patch.object(upload, "_resolve_operation", return_value=None),
        patch.object(
            upload,
            "build_shorts_description_for_upload",
            return_value=_quality_build(),
        ) as quality_build,
        patch.object(
            upload,
            "_render_upload_metadata_editor",
            return_value=("自由編集タイトル", "編集後の説明文", ("編集タグ",)),
        ) as editor,
        patch.object(
            upload,
            "_render_upload_schedule_editor",
            return_value=requested,
        ),
        patch.object(upload, "_open_preview") as open_preview,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "subheader"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info") as info,
        patch.object(upload.st, "button", return_value=True),
    ):
        upload.render_upload_section("source-1", settings)

    assert open_preview.call_args.kwargs["start_ms"] == 90_000
    assert open_preview.call_args.kwargs["title"] == "自由編集タイトル"
    assert open_preview.call_args.kwargs["description"] == "編集後の説明文"
    assert open_preview.call_args.kwargs["tags"] == ("編集タグ",)
    assert open_preview.call_args.kwargs["requested_publish_at"] == requested
    quality_build.assert_called_once_with(
        item.description,
        video_id="source-1",
        start_ms=90_000,
        settings=settings,
    )
    assert editor.call_args.kwargs["initial_description"] == quality_build.return_value.description
    assert "shorts_description_template.txt" in info.call_args.args[0]


def test_related_video_ui_remains_visible_before_publish_at(tmp_path: Path) -> None:
    operation = _operation(tmp_path, state="uploaded")
    content = operation.content.model_copy(
        update={"publish_at": NOW + timedelta(days=2)}
    )
    operation = operation.model_copy(update={"content": content})
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload, "datetime") as datetime_cls,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "write") as write,
        patch.object(upload.st, "button", return_value=False),
        patch.object(upload, "start_publication_poll") as start_poll,
    ):
        datetime_cls.now.return_value = NOW
        upload.render_upload_operation(operation, settings=settings)

    start_poll.assert_not_called()
    rendered = "\n".join(str(call_item.args[0]) for call_item in write.call_args_list)
    assert "設定対象の元動画 ID: source-1" in rendered
    assert "対象 Shorts video ID: youtube-1" in rendered


def test_related_video_ui_remains_visible_after_terminal_publication_poll(
    tmp_path: Path,
) -> None:
    base = _operation(tmp_path, state="uploaded")
    observation = UploadStatusObservation(
        polled_at=NOW + timedelta(minutes=1),
        phase="publication",
        status={},
        processing_details={},
        classification="published",
        error=None,
    )
    operation = base.model_copy(
        update={
            "content": base.content.model_copy(
                update={"publish_at": NOW - timedelta(minutes=1)}
            ),
            "poll_history": (observation,),
        }
    )
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload, "datetime") as datetime_cls,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "caption") as caption,
        patch.object(upload.st, "write"),
        patch.object(upload.st, "button", return_value=False),
        patch.object(upload, "start_publication_poll") as start_poll,
    ):
        datetime_cls.now.return_value = NOW
        upload.render_upload_operation(operation, settings=settings)

    start_poll.assert_not_called()
    assert any("terminal 判定済み" in item.args[0] for item in caption.call_args_list)


def test_related_video_dialog_requires_explicit_confirmation_and_uses_both_canonical_ids(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload.st, "warning"),
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "checkbox", return_value=True) as checkbox,
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "success"),
        patch.object(upload.st, "rerun"),
        patch.object(upload, "confirm_related_video") as confirm,
    ):
        upload.related_video_confirm_dialog.__wrapped__(
            "operation-1",
            "source-1",
            "youtube-1",
            settings,
            "dialog-1",
        )

    assert checkbox.call_args.kwargs["value"] is False
    assert confirm.call_args.args[:4] == (
        "operation-1",
        "source-1",
        "youtube-1",
        settings,
    )


def test_upload_section_blocks_partial_running_manifest(tmp_path: Path) -> None:
    output = tmp_path / "partial.mp4"
    output.write_bytes(b"video")
    item = MagicMock(
        status="succeeded",
        output_path=output,
        title_candidates=("途中成功",),
        target_id="clip-1",
    )
    result = MagicMock(
        status="running",
        items=(item,),
        job_id="shorts-job",
        clip_specs=(),
    )

    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot") as next_slot,
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "info") as info,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", Settings(data_dir=tmp_path))

    assert "生成が完了していない" in info.call_args.args[0]
    next_slot.assert_not_called()
    button.assert_not_called()


def test_upload_section_renders_durable_operation_without_latest_manifest(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    operation = _operation(tmp_path, state="reserved")
    save_reserved_operation(operation, settings)

    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=None),
        patch.object(upload, "_render_related_video_summary_panel"),
        patch.object(upload, "render_upload_operation") as render_operation,
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
    ):
        upload.render_upload_section("source-1", settings)

    render_operation.assert_called_once_with(operation, settings=settings)


@pytest.mark.parametrize("manifest_kind", ["none", "interrupted", "done-empty"])
def test_upload_section_keeps_global_related_pending_reachable_without_latest_manifest(
    tmp_path: Path, manifest_kind: str
) -> None:
    settings = Settings(data_dir=tmp_path)
    operation = _operation(tmp_path, state="uploaded")
    summary = RelatedVideoSummary(pending_count=1, operations=(operation,))
    if manifest_kind == "none":
        result = None
    elif manifest_kind == "interrupted":
        result = MagicMock(
            status="interrupted",
            items=(),
            job_id="interrupted-job",
        )
    else:
        result = MagicMock(status="done", items=(), job_id="stale-job")
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_related_video_summary", return_value=summary) as get_summary,
        patch.object(upload, "related_video_confirm_dialog") as dialog,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "write") as write,
        patch.object(upload.st, "button", return_value=True),
        patch.object(upload.st, "info"),
        patch.object(upload.st, "error"),
        patch.object(upload, "_open_preview") as open_preview,
    ):
        upload.render_upload_section("source-1", settings)

    get_summary.assert_called_once_with(settings)
    dialog.assert_called_once_with(
        operation.operation_id,
        operation.source_video_id,
        operation.video_id,
        settings,
        dialog.call_args.args[4],
    )
    open_preview.assert_not_called()
    rendered = "\n".join(str(item.args[0]) for item in write.call_args_list)
    assert "設定対象の元動画 ID: source-1" in rendered
    assert "対象 Shorts video ID: youtube-1" in rendered


@pytest.mark.parametrize("output_kind", ["none", "missing"])
def test_upload_section_restores_uploaded_operation_before_output_gate(
    tmp_path: Path, output_kind: str
) -> None:
    operation = _operation(tmp_path, state="uploaded")
    output_path = None if output_kind == "none" else tmp_path / "moved.mp4"
    item = MagicMock(
        status="succeeded",
        output_path=output_path,
        title_candidates=("検索明快なタイトル", "仕事影響のタイトル", "好奇心のタイトル"),
        target_id="clip-1",
    )
    result = MagicMock(status="done", items=(item,), job_id="shorts-job", clip_specs=())
    settings = Settings(data_dir=tmp_path)
    policy = SchedulePolicy(daily_times=["09:00"])
    requested = datetime(2026, 8, 5, 15, 45, tzinfo=ZoneInfo(policy.timezone))
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot", return_value=(policy, requested)),
        patch.object(upload, "_render_related_video_summary_panel"),
        patch.object(upload, "list_operations", return_value=(operation,)) as list_ops,
        patch.object(upload, "render_upload_operation") as render_operation,
        patch.object(upload, "_open_preview") as open_preview,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "warning") as warning,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", settings)

    list_ops.assert_called_once_with(settings)
    render_operation.assert_called_once_with(operation, settings=settings)
    open_preview.assert_not_called()
    button.assert_not_called()
    assert any("予約投稿せず" in call.args[0] for call in warning.call_args_list)


def test_upload_section_blocks_interrupted_manifest_with_recovery_guidance(
    tmp_path: Path,
) -> None:
    result = MagicMock(
        status="interrupted",
        items=(MagicMock(status="succeeded"),),
        job_id="shorts-job",
    )
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot") as next_slot,
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "error") as error,
        patch.object(upload.st, "info") as info,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", Settings(data_dir=tmp_path))

    assert "中断" in error.call_args.args[0]
    assert "予約対象ではありません" in error.call_args.args[0]
    next_slot.assert_not_called()
    info.assert_not_called()
    button.assert_not_called()


def test_upload_section_blocks_missing_output_before_reservation_controls(
    tmp_path: Path,
) -> None:
    item = MagicMock(
        status="succeeded",
        output_path=tmp_path / "missing.mp4",
        title_candidates=("欠損動画",),
        target_id="clip-1",
    )
    result = MagicMock(status="done", items=(item,), job_id="shorts-job", clip_specs=())
    policy = SchedulePolicy(daily_times=["09:00"])
    requested = datetime(2026, 8, 5, 15, 45, tzinfo=ZoneInfo(policy.timezone))
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot", return_value=(policy, requested)),
        patch.object(upload, "list_operations", return_value=()) as list_ops,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
        patch.object(upload.st, "warning") as warning,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", Settings(data_dir=tmp_path))

    list_ops.assert_called_once_with(Settings(data_dir=tmp_path))
    button.assert_not_called()
    assert any("予約投稿せず" in call.args[0] for call in warning.call_args_list)


def test_upload_section_validates_legacy_manifest_before_reservation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "legacy.mp4"
    output.write_bytes(b"legacy video")
    item = MagicMock(
        status="succeeded",
        output_path=output,
        title_candidates=("legacy",),
        target_id="clip-1",
    )
    result = MagicMock(
        status="done",
        schema_version=1,
        items=(item,),
        job_id="legacy-job",
        clip_specs=(MagicMock(target_id="clip-1"),),
    )
    policy = SchedulePolicy(daily_times=["09:00"])
    requested = datetime(2026, 8, 5, 15, 45, tzinfo=ZoneInfo(policy.timezone))
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot", return_value=(policy, requested)),
        patch.object(
            upload, "can_reserve_shorts_queue_item", return_value=False
        ) as reserve,
        patch.object(upload, "list_operations", return_value=()) as list_ops,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
        patch.object(upload.st, "warning") as warning,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", settings)

    reserve.assert_called_once_with(result, item, settings)
    list_ops.assert_called_once_with(settings)
    button.assert_not_called()
    assert any("予約投稿せず" in call.args[0] for call in warning.call_args_list)


def test_invalid_schedule_or_unclicked_button_creates_no_preview_operation_or_job(
    tmp_path: Path,
) -> None:
    item = MagicMock(
        status="succeeded",
        title_candidates=("予約タイトル",),
        target_id="clip-1",
        description="説明文",
        tags=("タグ",),
    )
    result = MagicMock(status="done", items=(item,), job_id="shorts-job", clip_specs=())
    settings = Settings(data_dir=tmp_path)
    policy = SchedulePolicy(daily_times=["09:00"])
    initial = datetime(2026, 8, 5, 9, 0, tzinfo=ZoneInfo(policy.timezone))
    reservation_transaction = MagicMock()

    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "get_next_upload_slot", return_value=(policy, initial)),
        patch.object(upload, "_resolve_operation", return_value=None),
        patch.object(
            upload,
            "build_shorts_description_for_upload",
            return_value=_quality_build(),
        ),
        patch.object(
            upload,
            "_render_upload_metadata_editor",
            return_value=("予約タイトル", "説明文", ("タグ",)),
        ),
        patch.object(upload, "_render_upload_schedule_editor", return_value=None),
        patch.object(upload, "_open_preview") as open_preview,
        patch.object(upload, "confirm_and_start_upload") as confirm,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
        # disabled widget が誤って True を返す test double でも明示 guard する。
        patch.object(upload.st, "button", return_value=True) as button,
    ):
        upload.render_upload_section(
            "source-1",
            settings,
            reservation_transaction=reservation_transaction,
        )

    assert button.call_args.kwargs["disabled"] is True
    open_preview.assert_not_called()
    confirm.assert_not_called()
    reservation_transaction.assert_not_called()


def test_needs_reconciliation_shows_manual_guidance_without_retry_button(tmp_path: Path) -> None:
    operation = _operation(tmp_path, state="needs_reconciliation")
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "error") as error,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_operation(operation)
    assert any("自動再送は行いません" in item.args[0] for item in error.call_args_list)
    button.assert_not_called()


@pytest.mark.parametrize(
    ("state", "label"),
    [
        ("reserved", "予約済み"),
        ("uploading", "アップロード中"),
        ("uploaded", "アップロード済み"),
        ("failed", "失敗"),
        ("needs_reconciliation", "手動照合が必要"),
    ],
)
def test_operation_renderer_has_japanese_label_for_every_state(
    tmp_path: Path, state: str, label: str
) -> None:
    operation = _operation(tmp_path, state=state)
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "markdown") as markdown,
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "error"),
    ):
        upload.render_upload_operation(operation)
    assert label in markdown.call_args.args[0]


@pytest.mark.parametrize(
    ("classification", "label", "phase", "error"),
    [
        ("processing", "処理中", "processing", None),
        ("processing_succeeded", "処理完了", "processing", None),
        ("processing_failed", "処理失敗", "processing", "失敗"),
        ("processing_timeout", "処理確認がタイムアウト", "processing", "timeout"),
        ("scheduled", "予約公開待ち", "publication", None),
        ("suspected_private_lock", "非公開ロックの可能性", "publication", "lock"),
        ("published", "公開済み", "publication", None),
        ("publication_timeout", "公開確認がタイムアウト", "publication", "timeout"),
    ],
)
def test_poll_classification_is_always_rendered_in_japanese(
    tmp_path: Path,
    classification: str,
    label: str,
    phase: str,
    error: str | None,
) -> None:
    base = _operation(tmp_path, state="uploaded")
    observation = UploadStatusObservation(
        polled_at=NOW,
        phase=phase,
        status={},
        processing_details={},
        classification=classification,
        error=error,
    )
    operation = base.model_copy(update={"poll_history": (observation,)})
    with (
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "caption") as caption,
        patch.object(upload.st, "write"),
    ):
        upload.render_upload_operation(operation)
    assert label in caption.call_args_list[-1].args[0]
    assert classification not in caption.call_args_list[-1].args[0]


def test_uploaded_operation_starts_publication_poll_from_explicit_cta(
    tmp_path: Path,
) -> None:
    base = _operation(tmp_path, state="uploaded")
    content = base.content.model_copy(
        update={"publish_at": NOW - timedelta(minutes=1)}
    )
    operation = base.model_copy(update={"content": content})
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload, "datetime") as datetime_cls,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "write"),
        patch.object(upload.st, "button", return_value=True) as button,
        patch.object(upload.st, "success"),
        patch.object(upload.st, "rerun"),
        patch.object(upload, "start_publication_poll", return_value="poll-job") as start,
        patch.object(upload, "set_active_job_id") as set_job,
    ):
        datetime_cls.now.return_value = NOW
        upload.render_upload_operation(operation, settings=settings)

    start.assert_called_once_with(operation.operation_id, settings, now=NOW)
    set_job.assert_called_once_with("poll-job")
    button.assert_called_once()


def test_uploaded_operation_does_not_offer_api_cta_before_publish_time(
    tmp_path: Path,
) -> None:
    operation = _operation(tmp_path, state="uploaded")
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload, "datetime") as datetime_cls,
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "caption") as caption,
        patch.object(upload.st, "write"),
        patch.object(upload.st, "button") as button,
        patch.object(upload, "start_publication_poll") as start,
    ):
        datetime_cls.now.return_value = NOW
        upload.render_upload_operation(operation, settings=settings)

    button.assert_not_called()
    start.assert_not_called()
    assert any("公開予定時刻までは" in item.args[0] for item in caption.call_args_list)


def test_operation_session_mapping_is_scoped_to_video_and_fallbacks_by_clip(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    operation = _operation(tmp_path)
    clip_two_operation = operation.model_copy(
        update={"operation_id": "operation-2", "clip_id": "clip-2"}
    )
    save_reserved_operation(operation, settings)
    save_reserved_operation(clip_two_operation, settings)
    with (
        patch.object(upload, "_operation_ids", return_value={"source-1": "operation-1"}),
    ):
        assert upload._resolve_operation("source-1", "clip-1", settings) == operation
        assert (
            upload._resolve_operation("source-1", "clip-2", settings)
            == clip_two_operation
        )
        assert upload._resolve_operation("other-video", "clip-1", settings) is None

    with (
        patch.object(upload, "_operation_ids", return_value={}),
        patch.object(upload, "latest_operation_for_source", return_value=operation) as latest,
    ):
        assert upload._resolve_operation("source-1", "clip-1", settings) == operation
    latest.assert_called_once_with("source-1", "clip-1", settings)


def test_operation_lookup_propagates_queue_corruption_fail_closed(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(upload, "_operation_ids", return_value={"source-1": "operation-1"}),
        patch.object(upload, "load_operation", side_effect=UploadQueueError("broken")),
        pytest.raises(UploadQueueError),
    ):
        upload._resolve_operation("source-1", "clip-1", settings)


def test_queue_corruption_shows_japanese_error_and_hides_post_action(tmp_path: Path) -> None:
    item = MagicMock(
        status="succeeded",
        title_candidates=("予約タイトル",),
        target_id="clip-1",
    )
    result = MagicMock(status="done", items=(item,), job_id="shorts-job")
    settings = Settings(data_dir=tmp_path)
    save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings)
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "list_operations", side_effect=UploadQueueError("raw error")),
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "subheader"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
        patch.object(upload.st, "error") as error,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", settings)
    message = error.call_args.args[0]
    assert "予約投稿を停止" in message
    assert "raw error" not in message
    button.assert_not_called()
