"""P2 予約投稿 component の表示・確認境界テスト."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadOperation,
    UploadStatusObservation,
)
from yt_live_kit.services.schedule import SchedulePolicy, UploadPreview
from yt_live_kit.services.upload_queue import UploadQueueError
from yt_live_kit.ui.components import upload


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


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
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "_resolve_operation", return_value=None),
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
    assert "shorts_description_template.txt" in info.call_args.args[0]


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


def test_operation_session_mapping_is_scoped_to_video_and_fallbacks_by_clip(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    operation = _operation(tmp_path)
    with (
        patch.object(upload, "_operation_ids", return_value={"source-1": "operation-1"}),
        patch.object(upload, "load_operation", return_value=operation) as load,
    ):
        assert upload._resolve_operation("source-1", "clip-1", settings) == operation
        assert upload._resolve_operation("source-1", "clip-2", settings) is None
        assert upload._resolve_operation("other-video", "clip-1", settings) is None
    assert load.call_count == 2

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
    result = MagicMock(items=(item,), job_id="shorts-job")
    with (
        patch.object(upload, "load_latest_shorts_queue_result", return_value=result),
        patch.object(upload, "_resolve_operation", side_effect=UploadQueueError("raw error")),
        patch.object(upload.st, "container", side_effect=_container),
        patch.object(upload.st, "subheader"),
        patch.object(upload.st, "caption"),
        patch.object(upload.st, "markdown"),
        patch.object(upload.st, "info"),
        patch.object(upload.st, "error") as error,
        patch.object(upload.st, "button") as button,
    ):
        upload.render_upload_section("source-1", Settings(data_dir=tmp_path))
    message = error.call_args.args[0]
    assert "予約投稿を停止" in message
    assert "raw error" not in message
    button.assert_not_called()
