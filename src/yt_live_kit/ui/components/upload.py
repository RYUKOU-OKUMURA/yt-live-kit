"""生成済みショートの予約投稿 preview・確認・状態表示 UI."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import UploadOperation
from yt_live_kit.services.description import (
    DescriptionError,
    build_shorts_description,
    get_shorts_template_path,
)
from yt_live_kit.services.schedule import (
    ScheduleError,
    UploadPreview,
    build_upload_preview,
    confirm_and_start_upload,
    latest_operation_for_source,
)
from yt_live_kit.services.shorts_queue import (
    ShortsQueueError,
    ShortsQueueItemResult,
    ShortsQueueResult,
    load_latest_shorts_queue_result,
)
from yt_live_kit.services.upload_queue import UploadQueueError, load_operation
from yt_live_kit.ui.state import set_active_job_id

_OPERATION_IDS_KEY = "upload_operation_ids"
_STATE_LABELS = {
    "reserved": "予約済み",
    "uploading": "アップロード中",
    "uploaded": "アップロード済み",
    "failed": "失敗",
    "needs_reconciliation": "手動照合が必要",
}
_ELIGIBILITY_LABELS = {
    "unknown": "未確認",
    "scheduled": "予約公開待ち",
    "suspected_private_lock": "非公開ロックの可能性",
    "confirmed_private_lock": "非公開ロック確認済み",
    "no_private_lock": "非公開ロックなし",
    "published": "公開済み",
}
_POLL_CLASSIFICATION_LABELS = {
    "processing": "処理中",
    "processing_succeeded": "処理完了",
    "processing_failed": "処理失敗",
    "processing_timeout": "処理確認がタイムアウト",
    "scheduled": "予約公開待ち",
    "suspected_private_lock": "非公開ロックの可能性",
    "published": "公開済み",
    "publication_timeout": "公開確認がタイムアウト",
}


def _safe_text(value: object) -> str:
    return str(value).replace("<", "〈").replace(">", "〉")


def _operation_ids() -> dict[str, str]:
    raw = st.session_state.get(_OPERATION_IDS_KEY)
    if not isinstance(raw, dict):
        raw = {}
        st.session_state[_OPERATION_IDS_KEY] = raw
    return raw


def _store_operation_id(video_id: str, operation_id: str) -> None:
    values = dict(_operation_ids())
    values[video_id] = operation_id
    st.session_state[_OPERATION_IDS_KEY] = values


def _resolve_operation(
    video_id: str,
    clip_id: str,
    settings: Settings,
) -> UploadOperation | None:
    operation_id = _operation_ids().get(video_id)
    if operation_id:
        operation = load_operation(operation_id, settings)
        if operation.source_video_id == video_id and operation.clip_id == clip_id:
            return operation
        return None
    return latest_operation_for_source(video_id, clip_id, settings)


def render_upload_operation(operation: UploadOperation) -> None:
    """upload operation だけを pipeline result と混同せず表示する."""
    with st.container(border=True):
        st.markdown(f"**投稿状態: {_STATE_LABELS[operation.state]}**")
        st.caption(
            f"operation {_safe_text(operation.operation_id)} / "
            f"job {_safe_text(operation.job_id)}"
        )
        st.write(f"予約日時: {operation.content.publish_at.isoformat()}")
        st.write(
            "公開判定: "
            + _ELIGIBILITY_LABELS.get(
                operation.publication_eligibility,
                _safe_text(operation.publication_eligibility),
            )
        )
        if operation.video_id:
            st.write(f"YouTube video ID: {_safe_text(operation.video_id)}")
        if operation.error:
            if operation.state == "needs_reconciliation":
                st.error(
                    "自動再送は行いません。YouTube Studio で動画 ID・チャンネル・"
                    "対象ファイルを手動照合してください。"
                )
            st.error(_safe_text(operation.error))
        if operation.poll_history:
            latest = operation.poll_history[-1]
            st.caption(
                f"最終確認 {latest.polled_at.isoformat()} / "
                f"{_POLL_CLASSIFICATION_LABELS[latest.classification]}"
            )


def _selection_from_label(value: str | None) -> bool | None:
    if value == "はい":
        return True
    if value == "いいえ":
        return False
    return None


@st.dialog("YouTube 予約投稿の確認", width="large")
def upload_preview_dialog(
    preview: UploadPreview,
    settings: Settings,
    dialog_nonce: str,
) -> None:
    """全 snapshot を表示し、明示選択と同意後だけ service confirm を呼ぶ."""
    st.warning(
        "YouTube へ非公開アップロードし、指定時刻に公開予約します。"
        "内容をすべて確認してから確定してください。"
    )
    with st.container(border=True):
        st.markdown("**投稿先チャンネル**")
        st.write(f"{_safe_text(preview.channel.title)} / {_safe_text(preview.channel.channel_id)}")
        st.markdown("**対象ファイル**")
        st.code(str(preview.video_path))
        st.write(f"サイズ: {preview.file_size} bytes / 尺: {preview.duration_sec:.3f} 秒")
        st.markdown("**タイトル**")
        st.write(_safe_text(preview.title))
        st.markdown("**説明文全文**")
        st.text_area(
            "説明文全文",
            value=_safe_text(preview.description),
            disabled=True,
            height=240,
            label_visibility="collapsed",
            key=f"upload_description_{dialog_nonce}",
        )
        st.markdown("**タグ**")
        st.write(_safe_text(",".join(preview.tags)))
        st.markdown("**予約日時**")
        st.write(
            f"{preview.publish_at.isoformat()}（{_safe_text(preview.policy.timezone)}） / "
            f"UTC {preview.publish_at_utc_z}"
        )
        st.write("公開設定: private / チャンネル登録者への通知: false")
        st.caption(
            f"America/Los_Angeles 当日試行: "
            f"{preview.attempt_count_la} / {preview.attempt_limit}"
        )

    made_for_kids_label = st.segmented_control(
        "子ども向けコンテンツですか",
        ["はい", "いいえ"],
        default=None,
        key=f"upload_audience_{dialog_nonce}",
    )
    synthetic_label = st.segmented_control(
        "現実と見分けにくい合成・改変コンテンツを含みますか",
        ["はい", "いいえ"],
        default=None,
        key=f"upload_synthetic_{dialog_nonce}",
    )
    guidelines = st.checkbox(
        "YouTube Community Guidelines に準拠する内容であることを確認しました",
        value=False,
        key=f"upload_guidelines_{dialog_nonce}",
    )
    made_for_kids = _selection_from_label(made_for_kids_label)
    synthetic = _selection_from_label(synthetic_label)
    ready = made_for_kids is not None and synthetic is not None and guidelines
    if st.button(
        "この内容で予約投稿を確定",
        type="primary",
        disabled=not ready,
        key=f"upload_confirm_{dialog_nonce}",
    ):
        try:
            operation = confirm_and_start_upload(
                preview,
                self_declared_made_for_kids=made_for_kids,
                contains_synthetic_media=synthetic,
                community_guidelines_confirmed=guidelines,
                settings=settings,
                now=datetime.now(timezone.utc),
            )
        except ScheduleError as exc:
            st.error(_safe_text(exc))
            return
        _store_operation_id(preview.source_video_id, operation.operation_id)
        set_active_job_id(operation.job_id)
        st.success("予約投稿ジョブを開始しました。")
        st.rerun()


def _clip_start_ms(result: ShortsQueueResult, target_id: str) -> int | None:
    """該当生成対象の先頭区間の開始ミリ秒を返す."""
    for spec in result.clip_specs:
        if spec.target_id == target_id:
            return spec.segments[0].start_ms
    return None


def _open_preview(
    video_id: str,
    item: ShortsQueueItemResult,
    settings: Settings,
    *,
    start_ms: int | None = None,
) -> None:
    if item.output_path is None:
        st.error("投稿できるショート動画ファイルがありません。")
        return
    try:
        description = build_shorts_description(
            item.description,
            video_id=video_id,
            start_ms=start_ms,
            settings=settings,
        )
    except DescriptionError as exc:
        st.error(_safe_text(exc))
        return
    try:
        preview = build_upload_preview(
            source_video_id=video_id,
            source_kind="shorts_queue",
            clip_id=item.target_id,
            video_path=item.output_path,
            title=item.title_candidates[0],
            description=description,
            tags=item.tags,
            settings=settings,
            now=datetime.now(timezone.utc),
        )
    except ScheduleError as exc:
        st.error(_safe_text(exc))
        return
    upload_preview_dialog(preview, settings, uuid.uuid4().hex)


def render_upload_section(video_id: str, settings: Settings) -> None:
    """最新の検証済み shorts queue から成功 item の投稿入口を描画する."""
    st.subheader("7. YouTube 予約投稿")
    st.caption(
        "生成済みショートを private 固定・通知なしでアップロードし、"
        "次の空き枠へ予約します。"
    )
    try:
        result = load_latest_shorts_queue_result(video_id, settings)
    except ShortsQueueError as exc:
        st.error(_safe_text(exc))
        return
    if result is None:
        st.info("まとめて生成したショートがありません。先にショートを生成してください。")
        return
    succeeded = tuple(item for item in result.items if item.status == "succeeded")
    if not succeeded:
        st.info("予約投稿できる生成済みショートがありません。")
        return
    template_path = get_shorts_template_path(settings)
    if not template_path.is_file():
        st.info(
            "ショート用の定型文が未設定です。"
            f"{template_path} に定型文を置くと、"
            "チャンネル URL や元配信リンクを概要欄へ差し込めます。"
            "使えるプレースホルダーは "
            "{{description}} / {{source_title}} / {{source_url}} です。"
        )
    for item in succeeded:
        with st.container(border=True):
            st.markdown(f"**{_safe_text(item.title_candidates[0])}**")
            st.caption(_safe_text(item.target_id))
            try:
                operation = _resolve_operation(video_id, item.target_id, settings)
            except UploadQueueError:
                st.error(
                    "投稿キューを安全に読み込めないため、予約投稿を停止しました。"
                    "投稿キューを手動修復してください。"
                )
                continue
            if operation is not None:
                render_upload_operation(operation)
            if st.button(
                "投稿内容を確認",
                key=f"upload_open_{result.job_id}_{item.target_id}",
                type="primary",
                disabled=operation is not None and operation.state != "failed",
            ):
                _open_preview(
                    video_id,
                    item,
                    settings,
                    start_ms=_clip_start_ms(result, item.target_id),
                )
