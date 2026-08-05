"""生成済みショートの予約投稿 preview・確認・状態表示 UI."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import UploadOperation
from yt_live_kit.services.description import (
    DescriptionError,
    build_shorts_description,
    build_shorts_description_for_upload,
    get_shorts_template_path,
)
from yt_live_kit.services.schedule import (
    ScheduleError,
    SchedulePolicy,
    UploadPreview,
    build_upload_preview,
    confirm_and_start_upload,
    get_next_upload_slot,
    latest_operation_for_source,
    make_requested_publish_at,
    validate_upload_description,
)
from yt_live_kit.services.shorts_queue import (
    ShortsQueueError,
    ShortsQueueItemResult,
    ShortsQueueResult,
    can_reserve_shorts_queue_item,
    load_latest_shorts_queue_result,
)
from yt_live_kit.services.shorts_line import LineReservationStartedError, LineStateError
from yt_live_kit.services.upload_queue import (
    UploadQueueError,
    confirm_related_video,
    get_related_video_summary,
    list_operations,
    load_operation,
    start_publication_poll,
)
from yt_live_kit.ui.controllers.upload import LineUploadAdapter
from yt_live_kit.ui.state import set_active_job_id
from yt_live_kit.ui.view_models.upload import (
    SourceOperationProjection,
    project_source_operations,
)

_OPERATION_IDS_KEY = "upload_operation_ids"
_POST_START_ERRORS_KEY = "upload_post_start_errors"
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
_PUBLICATION_POLL_TERMINAL = frozenset(
    {
        "published",
        "suspected_private_lock",
        "processing_failed",
        "publication_timeout",
    }
)
_TITLE_DIRECTIONS = ("検索明快型", "仕事影響型", "好奇心型")
_RELATED_STATUS_LABELS = {
    "not_ready": "まだ確認できません",
    "pending": "Studio 設定の確認待ち",
    "confirmed": "Studio 設定確認済み",
}


def _safe_text(value: object) -> str:
    return str(value).replace("<", "〈").replace(">", "〉")


def _is_nonempty_file(path: Path) -> tuple[bool, str | None]:
    """予約入口で成果物の欠損・空ファイルを日本語へ変換する."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False, "生成済み動画ファイルが見つからないか、空になっています。"
    except OSError:
        return False, "生成済み動画ファイルを確認できませんでした。"
    return True, None


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


def _store_post_start_error(video_id: str, message: str) -> None:
    raw = st.session_state.get(_POST_START_ERRORS_KEY)
    values = dict(raw) if isinstance(raw, dict) else {}
    values[video_id] = message
    st.session_state[_POST_START_ERRORS_KEY] = values


def _pop_post_start_error(video_id: str) -> str | None:
    raw = st.session_state.get(_POST_START_ERRORS_KEY)
    if not isinstance(raw, dict):
        return None
    values = dict(raw)
    message = values.pop(video_id, None)
    st.session_state[_POST_START_ERRORS_KEY] = values
    return str(message) if message else None


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
        # session map は元動画単位の legacy / rerun state のため、同じ元動画の
        # 別 clip を指すことがある。対象 clip の永続 operation へ戻して、複数
        # Shorts の pending 関連動画導線を隠さない。
        return latest_operation_for_source(video_id, clip_id, settings)
    return latest_operation_for_source(video_id, clip_id, settings)


def _studio_video_edit_url(video_id: str) -> str:
    """関連動画設定を人が行う YouTube Studio の編集先."""
    return f"https://studio.youtube.com/video/{video_id}/edit"


@st.dialog("関連動画設定の確認", width="large")
def related_video_confirm_dialog(
    operation_id: str,
    source_video_id: str,
    video_id: str,
    settings: Settings,
    dialog_nonce: str,
) -> None:
    """Studio での人確認後だけ local queue の confirmed へ遷移する."""
    st.warning(
        "YouTube Studio の画面操作は自動化しません。"
        "Studio で関連動画を設定した後、2つの正本 ID を確認して確定してください。"
    )
    with st.container(border=True):
        st.write(f"投稿 operation: {_safe_text(operation_id)}")
        st.write(f"設定対象の元動画 ID: {_safe_text(source_video_id)}")
        st.write(f"対象 Shorts video ID: {_safe_text(video_id)}")
        st.markdown(
            f"[YouTube Studio の編集画面を開く]({_studio_video_edit_url(video_id)})"
        )
        st.write(
            "手順: Studio の編集画面で関連動画に元動画 ID を設定し、"
            "保存後にこの確認欄を明示的に確定してください。"
        )
    confirmed = st.checkbox(
        "Studioで関連動画を設定済みです",
        value=False,
        key=f"related_video_confirmed_{dialog_nonce}",
    )
    if st.button(
        "関連動画の設定済みを確定",
        type="primary",
        disabled=not confirmed,
        key=f"related_video_confirm_{dialog_nonce}",
    ):
        try:
            confirm_related_video(
                operation_id,
                source_video_id,
                video_id,
                settings,
                now=datetime.now(timezone.utc),
            )
        except UploadQueueError as exc:
            # queue service が race / ID 不一致 / 破損を fail closed で判定する。
            st.error(_safe_text(exc))
            return
        st.success("関連動画の設定確認を記録しました。YouTube API は呼び出していません。")
        st.rerun()


def _render_related_video_status(
    operation: UploadOperation,
    settings: Settings,
) -> None:
    """P6-3 service の結果だけで関連動画の追跡 UI を描画する."""
    with st.container(border=True):
        st.markdown("**関連動画の Studio 手動確認**")
        st.write(
            "状態: "
            + _RELATED_STATUS_LABELS.get(
                operation.related_video_status,
                _safe_text(operation.related_video_status),
            )
        )
        if operation.related_video_confirmed_at is not None:
            st.caption(
                "確認日時: "
                + operation.related_video_confirmed_at.isoformat()
            )
        if operation.state != "uploaded":
            st.caption("アップロード成功後に Studio の関連動画確認を行えます。")
            return
        if not operation.video_id:
            st.warning(
                "関連動画確認対象の Shorts ID が不足しているため、"
                "確認操作を停止しました。投稿キューを手動修復してください。"
            )
            return

        st.markdown(
            f"[YouTube Studio の編集画面を開く]"
            f"({_studio_video_edit_url(operation.video_id)})"
        )
        with st.expander(
            "Studio で設定する ID と手順",
            expanded=False,
            key=f"related_video_ids_{operation.operation_id}",
        ):
            st.write(f"設定対象の元動画 ID: {_safe_text(operation.source_video_id)}")
            st.write(f"対象 Shorts video ID: {_safe_text(operation.video_id)}")
            st.write(
                "手順: Studio の編集画面で関連動画を設定し、保存後に"
                "「Studioで関連動画を設定済み」と明示確定してください。"
            )
        if operation.related_video_status != "pending":
            return
        if st.button(
            "Studioで関連動画を設定済みとして確認",
            type="secondary",
            key=f"related_video_open_{operation.operation_id}",
        ):
            related_video_confirm_dialog(
                operation.operation_id,
                operation.source_video_id,
                operation.video_id,
                settings,
                uuid.uuid4().hex,
            )


def _render_related_video_summary_panel(
    settings: Settings,
    *,
    current_video_id: str | None = None,
) -> None:
    """現在の動画**以外**の確認待ちを、最新 manifest と独立に追跡し続ける入口."""
    try:
        summary = get_related_video_summary(settings)
    except UploadQueueError:
        st.error(
            "関連動画の確認待ち一覧を安全に読み込めないため、"
            "確認操作を停止しました。投稿キューを手動修復してください。"
        )
        return
    pending = tuple(
        item
        for item in summary.operations
        if item.source_video_id != current_video_id
    )
    if not pending:
        return
    with st.expander(f"他の動画の確認待ち（{len(pending)} 件）", expanded=False):
        st.caption(
            "いま開いている動画とは別の元動画から投稿したショートです。"
            "再起動後や、過去の生成結果が最新の一覧から消えた後も、ここから確認できます。"
        )
        for item in pending:
            with st.container(border=True):
                st.write(
                    f"元動画 {_safe_text(item.source_video_id)} のショート"
                    f"「{_safe_text(item.content.title)}」"
                )
                pending_video_id = item.video_id or ""
                st.write(
                    "状態: "
                    + _RELATED_STATUS_LABELS.get(
                        item.related_video_status,
                        _safe_text(item.related_video_status),
                    )
                )
                st.caption(
                    f"operation {_safe_text(item.operation_id)} / "
                    f"対象 Shorts video ID: {_safe_text(pending_video_id or '未取得')}"
                )
                if not pending_video_id:
                    st.warning(
                        "関連動画確認対象の Shorts ID が不足しているため、"
                        "確認操作を停止しました。投稿キューを手動修復してください。"
                    )
                    continue
                st.markdown(
                    f"[YouTube Studio の編集画面を開く]"
                    f"({_studio_video_edit_url(pending_video_id)})"
                )
                st.write(
                    "手順: Studio の編集画面で関連動画に元動画 ID を設定し、"
                    "保存後にこの確認欄を明示的に確定してください。"
                )
                if st.button(
                    "Studioで関連動画を設定済みとして確認",
                    type="secondary",
                    key=f"related_video_open_global_{item.operation_id}",
                ):
                    related_video_confirm_dialog(
                        item.operation_id,
                        item.source_video_id,
                        pending_video_id,
                        settings,
                        uuid.uuid4().hex,
                    )


def render_upload_operation(
    operation: UploadOperation, settings: Settings | None = None
) -> None:
    """upload operation だけを pipeline result と混同せず表示する."""
    with st.container(border=True):
        st.markdown(f"**投稿状態: {_STATE_LABELS[operation.state]}**")
        if operation.content.requirements is None:
            st.warning(
                "この投稿内容は legacy 形式で概要欄要件 snapshot がありません。"
                "新しい投稿には再生成した preview を使用してください。"
            )
        st.write(f"予約日時: {operation.content.publish_at.isoformat()}")
        st.write(
            "公開判定: "
            + _ELIGIBILITY_LABELS.get(
                operation.publication_eligibility,
                _safe_text(operation.publication_eligibility),
            )
        )
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
        with st.expander(
            "この投稿の内部 ID",
            expanded=False,
            key=f"upload_operation_ids_{operation.operation_id}",
        ):
            st.caption(
                f"operation {_safe_text(operation.operation_id)} / "
                f"job {_safe_text(operation.job_id)}"
            )
            if operation.video_id:
                st.write(f"YouTube video ID: {_safe_text(operation.video_id)}")
        if settings is not None and operation.state == "uploaded" and operation.video_id:
            latest_publication = next(
                (
                    observation.classification
                    for observation in reversed(operation.poll_history)
                    if observation.phase == "publication"
                ),
                None,
            )
            if latest_publication in _PUBLICATION_POLL_TERMINAL:
                st.caption("公開確認は terminal 判定済みです。")
            else:
                now = datetime.now(timezone.utc)
                if now < operation.content.publish_at:
                    st.caption(
                        "公開予定時刻までは公開状態を確認できません。"
                        "予約時刻後にこの画面から確認してください。"
                    )
                elif st.button(
                    "公開状態を確認",
                    key=f"upload_publication_poll_{operation.operation_id}",
                    type="secondary",
                ):
                    try:
                        poll_job_id = start_publication_poll(
                            operation.operation_id,
                            settings,
                            now=now,
                        )
                    except UploadQueueError as exc:
                        st.error(_safe_text(exc))
                    else:
                        set_active_job_id(poll_job_id)
                        st.success("公開状態の確認ジョブを開始しました。")
                        st.rerun()
        # publication poll が terminal / publishAt 前でも、upload 後の関連動画
        # 手動確認は追跡対象として常に残す。pending を hard gate にしない。
        if settings is not None:
            _render_related_video_status(operation, settings)


def _load_source_operations(
    video_id: str,
    settings: Settings,
) -> SourceOperationProjection | None:
    """予約 gate と追跡表示が同じ 1 回の queue 読み出しを共有する."""
    try:
        projection = project_source_operations(list_operations(settings), video_id)
    except UploadQueueError:
        st.error(
            "投稿キューを安全に読み込めないため、永続した投稿状態を表示できず、"
            "予約投稿を停止しました。"
            "投稿キューを手動修復してください。"
        )
        return None
    return projection


def _selection_from_label(value: str | None) -> bool | None:
    if value == "はい":
        return True
    if value == "いいえ":
        return False
    return None


def _candidate_values(item: ShortsQueueItemResult) -> tuple[str, ...]:
    """manifest の候補を文字列として安全に扱う."""
    raw = getattr(item, "title_candidates", ())
    if not isinstance(raw, (tuple, list)):
        return ()
    return tuple(value for value in raw if isinstance(value, str))


def _title_directions_are_complete(item: ShortsQueueItemResult) -> bool:
    candidates = _candidate_values(item)
    first_three = candidates[:3]
    return (
        len(first_three) == 3
        and all(value.strip() for value in first_three)
        and len(set(value.strip() for value in first_three)) == 3
    )


def _render_title_candidate_directions(item: ShortsQueueItemResult) -> None:
    """先頭3件を固定順で表示し、長さと legacy 不足を日本語で案内する."""
    candidates = _candidate_values(item)
    st.markdown("**タイトル候補（固定3方向）**")
    seen: set[str] = set()
    for index, direction in enumerate(_TITLE_DIRECTIONS):
        candidate = candidates[index] if index < len(candidates) else ""
        st.markdown(f"**{direction}**")
        if not candidate.strip():
            st.warning(
                f"{direction}のタイトル候補がありません。"
                "再生成または最終タイトルを手動補完してください。"
            )
            continue
        st.write(_safe_text(candidate))
        length = len(candidate)
        if not 18 <= length <= 32:
            st.warning(
                f"{direction}は18〜32文字推奨の範囲外です（現在 {length} 文字）。"
                "保存は可能です。"
            )
        normalized = candidate.strip()
        if normalized in seen:
            st.warning(
                f"{direction}のタイトル候補が他の方向と重複しています。"
                "再生成または手動補完を検討してください。"
            )
        seen.add(normalized)
    if not _title_directions_are_complete(item):
        st.info(
            "保存済みの legacy manifest ではタイトル候補が1〜2件の場合があります。"
            "新しい3方向を使うには再生成するか、最終タイトルを手動補完してください。"
        )


@st.dialog("YouTube 予約投稿の確認", width="large")
def upload_preview_dialog(
    preview: UploadPreview,
    settings: Settings,
    dialog_nonce: str,
    before_confirm: Callable[[str, Path], None] | None = None,
    on_operation_started: Callable[[str, str, Path], None] | None = None,
    reservation_transaction: (
        Callable[[str, Path, Callable[[], UploadOperation]], UploadOperation] | None
    ) = None,
    line_adapter: LineUploadAdapter | None = None,
) -> None:
    """全 snapshot を表示し、明示選択と同意後だけ service confirm を呼ぶ."""
    if line_adapter is not None and any(
        callback is not None
        for callback in (
            before_confirm,
            on_operation_started,
            reservation_transaction,
        )
    ):
        raise TypeError("ライン投稿 adapter と個別 callback は同時に指定できません。")
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
        preview_validator = (
            line_adapter.validate_preview
            if line_adapter is not None
            else before_confirm
        )
        if preview_validator is not None:
            try:
                preview_validator(preview.clip_id, preview.video_path)
            except LineStateError as exc:
                st.error(_safe_text(exc))
                return
        def start_upload() -> UploadOperation:
            return confirm_and_start_upload(
                preview,
                self_declared_made_for_kids=made_for_kids,
                contains_synthetic_media=synthetic,
                community_guidelines_confirmed=guidelines,
                settings=settings,
                now=datetime.now(timezone.utc),
            )

        try:
            transaction = (
                line_adapter.reservation_transaction
                if line_adapter is not None
                else reservation_transaction
            )
            operation = (
                transaction(
                    preview.clip_id,
                    preview.video_path,
                    start_upload,
                )
                if transaction is not None
                else start_upload()
            )
        except LineReservationStartedError as exc:
            operation = exc.operation
            _store_operation_id(preview.source_video_id, operation.operation_id)
            set_active_job_id(operation.job_id)
            message = (
                "予約投稿ジョブは開始済みですが、ライン状態へ記録できませんでした。"
                f" operation {_safe_text(operation.operation_id)} を確認してください。"
                f" 詳細: {_safe_text(exc)}"
            )
            _store_post_start_error(preview.source_video_id, message)
            st.error(message)
            st.rerun()
            return
        except (LineStateError, ScheduleError) as exc:
            st.error(_safe_text(exc))
            return
        _store_operation_id(preview.source_video_id, operation.operation_id)
        # operation 作成後は、ライン記録が失敗してもジョブ追跡を失わない。
        set_active_job_id(operation.job_id)
        if line_adapter is None and on_operation_started is not None:
            try:
                on_operation_started(
                    preview.clip_id,
                    operation.operation_id,
                    preview.video_path,
                )
            except LineStateError as exc:
                message = (
                    "予約投稿ジョブは開始済みですが、ライン状態へ記録できませんでした。"
                    f" operation {_safe_text(operation.operation_id)} を確認してください。"
                    f" 詳細: {_safe_text(exc)}"
                )
                _store_post_start_error(preview.source_video_id, message)
                st.error(message)
                # fragment 内の再確定による重複 operation を防ぎ、一覧で追跡させる。
                st.rerun()
                return
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
    title: str | None = None,
    description: str | None = None,
    tags: tuple[str, ...] | list[str] | None = None,
    requested_publish_at: datetime | None = None,
    before_preview: Callable[[str, Path], None] | None = None,
    before_confirm: Callable[[str, Path], None] | None = None,
    on_operation_started: Callable[[str, str, Path], None] | None = None,
    reservation_transaction: (
        Callable[[str, Path, Callable[[], UploadOperation]], UploadOperation] | None
    ) = None,
    p6_quality_gate: bool = False,
    line_adapter: LineUploadAdapter | None = None,
) -> None:
    """現在の編集値だけから、新しい不変 preview を作って確認を開く."""
    if line_adapter is not None and any(
        callback is not None
        for callback in (
            before_preview,
            before_confirm,
            on_operation_started,
            reservation_transaction,
        )
    ):
        raise TypeError("ライン投稿 adapter と個別 callback は同時に指定できません。")
    if item.output_path is None:
        st.error("投稿できるショート動画ファイルがありません。")
        return
    preview_validator = (
        line_adapter.validate_preview
        if line_adapter is not None
        else before_preview
    )
    if preview_validator is not None:
        try:
            preview_validator(item.target_id, item.output_path)
        except LineStateError as exc:
            st.error(_safe_text(exc))
            return
    candidates = _candidate_values(item)
    upload_title = (candidates[0] if candidates else "") if title is None else title
    upload_tags = tuple(item.tags) if tags is None else tuple(tags)
    upload_description = description
    requirements = None
    if p6_quality_gate:
        if not _title_directions_are_complete(item) and (
            not isinstance(upload_title, str)
            or not upload_title.strip()
            or upload_title.strip() in {value.strip() for value in candidates}
        ):
            st.error(
                "タイトル候補の3方向が揃っていないため、新しい投稿を開始できません。"
                "再生成または最終タイトルの手動補完を行ってください。"
            )
            return
        try:
            quality_build = build_shorts_description_for_upload(
                item.description,
                video_id=video_id,
                start_ms=start_ms,
                settings=settings,
            )
        except DescriptionError as exc:
            st.error(_safe_text(exc))
            return
        requirements = quality_build.requirements
        # editor を経由しない内部 caller だけは未指定時に canonical 本文を
        # 初期値にできる。UI editor から渡された文字列は、元本文と同じでも
        # 常にユーザーの最終編集値として immutable requirements で検証する。
        if upload_description is None:
            upload_description = quality_build.description
        try:
            validate_upload_description(upload_description, requirements)
        except ScheduleError as exc:
            st.error(_safe_text(exc))
            return
    elif upload_description is None:
        # P4 の既存 fallback は、P6 gate を明示しない legacy 呼び出しで維持する。
        try:
            upload_description = build_shorts_description(
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
            title=upload_title,
            description=upload_description,
            tags=upload_tags,
            settings=settings,
            now=datetime.now(timezone.utc),
            requested_publish_at=requested_publish_at,
            requirements=requirements,
        )
    except ScheduleError as exc:
        st.error(_safe_text(exc))
        return
    dialog_kwargs = (
        {"line_adapter": line_adapter}
        if line_adapter is not None
        else {}
    )
    upload_preview_dialog(
        preview,
        settings,
        uuid.uuid4().hex,
        before_confirm,
        on_operation_started,
        reservation_transaction,
        **dialog_kwargs,
    )


def _render_upload_metadata_editor(
    item: ShortsQueueItemResult,
    *,
    job_id: str,
    initial_description: str,
    disabled: bool = False,
) -> tuple[str, str, tuple[str, ...]]:
    """候補を起点に、実送信 metadata の編集値だけを返す."""
    _render_title_candidate_directions(item)
    direction_candidates = _candidate_values(item)
    direction_options = (0, 1, 2)

    def format_direction(index: int) -> str:
        value = direction_candidates[index] if index < len(direction_candidates) else ""
        label = value if value.strip() else "未設定"
        return _safe_text(f"{_TITLE_DIRECTIONS[index]}: {label}")

    selected_option = st.selectbox(
        "タイトル候補",
        direction_options,
        format_func=format_direction,
        key=f"upload_title_candidate_{job_id}_{item.target_id}",
        disabled=disabled,
        persist_state="session",
    )
    if isinstance(selected_option, int) and not isinstance(selected_option, bool):
        candidate_index = selected_option if selected_option in direction_options else 0
    elif selected_option in direction_candidates:
        # テスト double / legacy caller が候補文字列を返す場合の互換処理。
        candidate_index = direction_candidates.index(selected_option)
    else:
        candidate_index = 0
    selected_title = (
        direction_candidates[candidate_index]
        if candidate_index < len(direction_candidates)
        else ""
    )
    title = st.text_input(
        "タイトル",
        value=selected_title,
        max_chars=100,
        key=f"upload_title_{job_id}_{item.target_id}_{candidate_index}",
        help="候補を選んだ後、この欄で自由に編集できます。",
        disabled=disabled,
        persist_state="session",
    )
    description = st.text_area(
        "説明文",
        value=initial_description,
        height=240,
        key=f"upload_description_edit_{job_id}_{item.target_id}",
        help="ここに表示される全文が投稿内容の確認対象になります。",
        disabled=disabled,
        persist_state="session",
    )
    tags = st.multiselect(
        "タグ",
        options=list(item.tags),
        default=list(item.tags),
        accept_new_options=True,
        placeholder="タグを選択または追加",
        key=f"upload_tags_{job_id}_{item.target_id}",
        help="選択を外すと削除でき、新しいタグも入力できます。",
        disabled=disabled,
        persist_state="session",
    )
    st.caption("編集後は、予約日時と投稿内容をあらためて確認してください。")
    return title, description, tuple(tags)


def _render_upload_schedule_editor(
    *,
    job_id: str,
    item_id: str,
    policy: SchedulePolicy,
    initial_publish_at: datetime,
    disabled: bool,
) -> datetime | None:
    """native widget の日付・時刻を policy timezone の aware datetime にする."""
    local_initial = initial_publish_at.astimezone(initial_publish_at.tzinfo)
    st.caption(f"予約日時の timezone: {_safe_text(policy.timezone)}")
    publish_date = st.date_input(
        "予約日",
        value=local_initial.date(),
        key=f"upload_publish_date_{job_id}_{item_id}",
        format="YYYY/MM/DD",
        disabled=disabled,
        persist_state="session",
    )
    publish_time = st.time_input(
        "予約時刻",
        value=local_initial.timetz().replace(tzinfo=None),
        key=f"upload_publish_time_{job_id}_{item_id}",
        step=60,
        disabled=disabled,
        persist_state="session",
    )
    try:
        return make_requested_publish_at(policy, publish_date, publish_time)
    except ScheduleError as exc:
        st.error(_safe_text(exc))
        return None


def render_upload_section(
    video_id: str,
    settings: Settings,
    *,
    before_preview: Callable[[str, Path], None] | None = None,
    before_confirm: Callable[[str, Path], None] | None = None,
    on_operation_started: Callable[[str, str, Path], None] | None = None,
    reservation_transaction: (
        Callable[[str, Path, Callable[[], UploadOperation]], UploadOperation] | None
    ) = None,
    line_adapter: LineUploadAdapter | None = None,
) -> SourceOperationProjection | None:
    """最新の検証済み shorts queue から成功 item の投稿入口だけを描画する.

    投稿後の追跡は `render_post_upload_followup` が別の時間軸として描く。
    戻り値の projection を渡して queue の二重読み出しを避ける。
    """
    if line_adapter is not None and any(
        callback is not None
        for callback in (
            before_preview,
            before_confirm,
            on_operation_started,
            reservation_transaction,
        )
    ):
        raise TypeError("ライン投稿 adapter と個別 callback は同時に指定できません。")
    post_start_error = _pop_post_start_error(video_id)
    if post_start_error:
        st.error(post_start_error)
    source_operations = _load_source_operations(video_id, settings)
    if source_operations is None:
        return None
    try:
        result = load_latest_shorts_queue_result(video_id, settings)
    except ShortsQueueError as exc:
        st.error(_safe_text(exc))
        return source_operations
    if result is None:
        st.info("まとめて生成したショートがありません。先にショートを生成してください。")
        return source_operations
    if result.status == "interrupted":
        st.error(
            "ショート生成は前回の処理中に中断されました。"
            "途中の成果物は予約対象ではありません。"
            "ショート生成画面で内容を確認し、明示的に再実行してください。"
        )
        return source_operations
    if result.status != "done":
        st.info(
            "ショート生成が完了していないため、まだ予約投稿できません。"
            "生成の完了後にもう一度確認してください。"
        )
        return source_operations
    succeeded = tuple(item for item in result.items if item.status == "succeeded")
    if not succeeded:
        st.info("予約投稿できる生成済みショートがありません。")
        return source_operations
    try:
        policy, initial_publish_at = get_next_upload_slot(
            settings,
            now=datetime.now(timezone.utc),
        )
    except ScheduleError as exc:
        st.error(_safe_text(exc))
        return source_operations
    template_path = get_shorts_template_path(settings)
    if not template_path.is_file():
        st.info(
            "ショート用の定型文が未設定です。投稿フォームを開く前に、"
            f"必須構成を含む既定テンプレートを {template_path} へ安全に作成します。"
        )
    for item in succeeded:
        with st.container(border=True):
            candidates = _candidate_values(item)
            st.markdown(
                f"**{_safe_text(candidates[0] if candidates else 'タイトル未設定')}**"
            )
            # clip_id は人が読んで意味を取れないため主導線に出さない。
            # 同名タイトルの取り違えを追える程度に折り畳みへ残す。
            with st.expander(
                "このショートの内部 ID",
                expanded=False,
                key=f"upload_target_id_{item.target_id}",
            ):
                st.caption(_safe_text(item.target_id))
            # 既存 operation の復元・関連動画追跡は、ローカル成果物の欠損や
            # stale manifest に依存させない。ここから下は新規 preview 用の gate。
            if item.output_path is None:
                st.warning(
                    "生成済みショート動画ファイルが manifest にありません。"
                    "予約投稿せず、ショート生成を再実行してください。"
                )
                continue
            if isinstance(item.output_path, Path):
                output_ready, output_warning = _is_nonempty_file(item.output_path)
                if not output_ready:
                    st.warning(
                        f"{output_warning} 予約投稿せず、ショート生成を再実行してください。"
                    )
                    continue
                if not can_reserve_shorts_queue_item(result, item, settings):
                    st.warning(
                        "生成成果物の対象・安全性・fingerprintを確認できません。"
                        "予約投稿せず、ショート生成を再実行してください。"
                    )
                    continue
            start_ms = _clip_start_ms(result, item.target_id)
            try:
                initial_description = build_shorts_description_for_upload(
                    item.description,
                    video_id=video_id,
                    start_ms=start_ms,
                    settings=settings,
                ).description
            except DescriptionError as exc:
                st.error(_safe_text(exc))
                continue
            active_operation = item.target_id in source_operations.blocking_clip_ids
            if active_operation:
                st.warning(
                    "このショートは既存の投稿 operation を追跡中のため、"
                    "新しい予約投稿は開始できません。投稿履歴を確認してください。"
                )
            title, description, tags = _render_upload_metadata_editor(
                item,
                job_id=result.job_id,
                initial_description=initial_description,
                disabled=active_operation,
            )
            requested_publish_at = _render_upload_schedule_editor(
                job_id=result.job_id,
                item_id=item.target_id,
                policy=policy,
                initial_publish_at=initial_publish_at,
                disabled=active_operation,
            )
            # preview は session state に保持しない。編集 rerun 後にこのボタンを
            # 押した時点の値からだけ再構築し、過去の確認内容を再利用しない。
            preview_requested = st.button(
                "予約日時と投稿内容を確認",
                key=f"upload_open_{result.job_id}_{item.target_id}",
                type="primary",
                disabled=active_operation or requested_publish_at is None,
            )
            if preview_requested and requested_publish_at is not None and not active_operation:
                _open_preview(
                    video_id,
                    item,
                    settings,
                    start_ms=start_ms,
                    title=title,
                    description=description,
                    tags=tags,
                    requested_publish_at=requested_publish_at,
                    before_preview=before_preview,
                    before_confirm=before_confirm,
                    on_operation_started=on_operation_started,
                    reservation_transaction=reservation_transaction,
                    p6_quality_gate=True,
                    line_adapter=line_adapter,
                )
    return source_operations


def render_post_upload_followup(
    video_id: str,
    settings: Settings,
    *,
    projection: SourceOperationProjection | None,
) -> None:
    """投稿後の追跡だけを、最新 shorts manifest の状態と無関係に描画する.

    これは最新 manifest の存在・状態・成果物とは独立した P6-3 の永続追跡入口。
    再起動後や過去 clip が manifest から消えた場合も残す。`render_upload_section`
    が manifest 都合で早期 return しても、この関数は呼び出し元から必ず呼ばれる。
    """
    if projection is None:
        # queue 読み出しの失敗は render_upload_section が既にエラー表示済み。
        return
    if projection.operations:
        st.markdown("**この動画から開始した投稿の追跡**")
        for operation in projection.operations:
            render_upload_operation(operation, settings=settings)
    else:
        st.caption(
            "この動画から投稿したショートはまだありません。"
            "予約投稿すると、投稿状態と YouTube Studio の関連動画確認がここに並びます。"
        )
    _render_related_video_summary_panel(settings, current_video_id=video_id)
