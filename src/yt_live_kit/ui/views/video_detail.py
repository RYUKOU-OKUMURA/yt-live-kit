"""選択した動画の成果物と次の作業をまとめて表示するページ."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.chapter_validator import validate_chapters
from yt_live_kit.services.clips import load_candidates_file
from yt_live_kit.services.highlights import load_segments_file
from yt_live_kit.services.history import ProcessedVideo, list_processed_videos
from yt_live_kit.services.jobs import (
    JobBusyError,
    JobState,
    get_active_job,
    is_busy,
    read_job_error_log,
    start_job,
)
from yt_live_kit.services.pipeline import (
    PipelineResult,
    load_result_from_disk,
    regenerate_job_target,
)
from yt_live_kit.services.storage import StorageError, format_bytes, purge_source
from yt_live_kit.services.shorts_queue import (
    ShortsQueueError,
    can_reserve_shorts_queue_item,
    load_latest_shorts_queue_result,
)
from yt_live_kit.services.youtube_api import (
    fetch_video_snippet,
    is_configured,
    merge_chapters_into_description,
    update_video_description,
)
from yt_live_kit.ui.components.clipboard import render_copy_button
from yt_live_kit.ui.components.short_cut import (
    S9_WHISPER_ERROR_PREFIX,
    S9_WHISPER_PROGRESS_PREFIX,
)
from yt_live_kit.ui.components.shorts_line import (
    make_line_upload_adapter,
    record_line_upload,
    render_main_line_summary,
    render_shorts_line,
    run_line_upload_transaction,
    validate_line_reservation,
)
from yt_live_kit.ui.components.status_bar import job_display_label
from yt_live_kit.ui.components.upload import (
    render_post_upload_followup,
    render_upload_section,
)
from yt_live_kit.ui.queries import count_generated_shorts
from yt_live_kit.ui.session_keys import (
    candidate_transfer_key,
    candidate_transfer_order_key,
    detail_workspace_key,
)
from yt_live_kit.ui.state import (
    JOB_ERROR_HISTORY_LIMIT,
    JobErrorNotification,
    format_job_error_summary_for_display,
    get_job_error_history,
    get_selected_video_id,
    sanitize_job_error_for_display,
    set_active_job_id,
)
from yt_live_kit.ui.views._local_settings import (
    load_description_applied_ids,
    mark_description_applied,
)
from yt_live_kit.ui.views.highlights import render_highlights_section
from yt_live_kit.ui.views.shorts import render_shorts_section
from yt_live_kit.ui.view_models.video_detail import (
    CandidateKey,
    CandidateTransfer,
    DetailSummary,
    Workspace,
    calculate_detail_summary,
    choose_initial_workspace,
    current_candidate_fingerprint,
    make_candidate_fingerprint,
    validate_candidate_transfer,
)

if TYPE_CHECKING:
    from streamlit.navigation.page import StreamlitPage

# Compatibility export for callers that previously imported this query from the
# detail view.  The implementation is shared with the library page.
count_shorts = count_generated_shorts

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_WORKSPACES: tuple[Workspace, ...] = ("materials", "shorts", "publish")
_WORKSPACE_LABELS: dict[Workspace, str] = {
    "materials": "素材候補",
    "shorts": "ショート作成",
    "publish": "公開・投稿",
}
_DESCRIPTION_UPDATED_IDS_KEY = "detail_description_updated_ids"
_DESCRIPTION_SUCCESS_KEY = "detail_description_success"
_MAX_DETAIL_JOB_ERROR_LOG_BYTES = 64 * 1024


def count_reservable_shorts(video_id: str, settings: Settings) -> int:
    """Count only items accepted by the same service gate used at reservation."""
    result = load_latest_shorts_queue_result(video_id, settings)
    if result is None or result.status != "done":
        return 0
    return sum(
        can_reserve_shorts_queue_item(result, item, settings)
        for item in result.items
    )


def _start_regenerate(
    video: ProcessedVideo,
    target: Literal["chapters", "clips"],
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "regenerate",
            regenerate_job_target,
            video_id=video.video_id,
            title=video.title,
            target=target,
            settings=settings,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


@st.dialog("成果物の再生成を確認")
def _confirm_regenerate_dialog(
    video: ProcessedVideo,
    target: Literal["chapters", "clips"],
    settings: Settings,
) -> None:
    target_label = "チャプター" if target == "chapters" else "切り抜き候補"
    st.warning(
        f"{target_label}を再生成します。既存の成果物は退避された後に上書きされます。"
    )
    if is_busy():
        st.info(_BUSY_MESSAGE)
    if st.button(
        "再生成を実行",
        key=f"detail_confirm_regen_{target}_{video.video_id}",
        type="primary",
        disabled=is_busy(),
    ):
        _start_regenerate(video, target, settings)


@st.dialog("元動画の削除を確認")
def _confirm_source_purge_dialog(
    video: ProcessedVideo,
    settings: Settings,
) -> None:
    st.warning(
        "元動画と中間ファイルを削除します。"
        "チャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
    )
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "削除を実行",
        key=f"detail_confirm_purge_{video.video_id}",
        type="primary",
        disabled=busy,
    ):
        try:
            deleted = purge_source(video.video_id, settings)
        except StorageError as exc:
            st.error(str(exc))
        else:
            st.success(
                f"元動画と中間ファイルを削除しました（{format_bytes(deleted)}）。"
            )
            st.rerun()


def _description_updated_ids() -> set[str]:
    value = st.session_state.get(_DESCRIPTION_UPDATED_IDS_KEY, set())
    return set(value) if isinstance(value, (set, list, tuple)) else set()


def _safe_user_text(value: object) -> str:
    """ユーザー表示用テキストの半角山カッコを全角へ置換する."""
    return str(value).replace("<", "〈").replace(">", "〉")


def _parse_s9_payload(value: object, prefix: str) -> dict[str, object] | None:
    text = str(value or "")
    marker = text.find(prefix)
    if marker < 0:
        return None
    try:
        payload = json.loads(text[marker + len(prefix) :].splitlines()[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _render_whisper_job_progress(job: JobState, *, video_id: str) -> None:
    """jobs の既存 message snapshot を UI thread で日本語の進捗へ変換する."""
    if job.kind not in {"short_cut", "short_cut_refine"} or job.video_id != video_id:
        return
    payload = _parse_s9_payload(job.message, S9_WHISPER_PROGRESS_PREFIX)
    with st.container(border=True, gap="small"):
        st.markdown("**高精度字幕の進捗**")
        st.write(f"job ID: {_safe_user_text(job.job_id)}")
        if payload is None:
            st.write(f"段階: {_safe_user_text(job.stage or '確認中')}")
            st.caption(_safe_user_text(job.message or "処理中です。"))
            return
        stage_labels = {
            "capability": "runtime capability",
            "audio": "音声準備",
            "span": "音声 span",
            "Whisper": "Whisper",
            "artifact": "artifact 保存",
            "resolver": "resolver",
        }
        stage = str(payload.get("stage", "確認中"))
        st.write(f"段階: {stage_labels.get(stage, stage)}")
        st.write(
            f"range: {payload.get('range_index', 0)} / "
            f"{payload.get('range_total', job.total)}"
        )
        current_range = payload.get("current_range")
        if isinstance(current_range, dict):
            st.write(
                "現在区間: "
                f"{_safe_user_text(current_range.get('id', '不明'))} "
                f"{_safe_user_text(current_range.get('start', '?'))} → "
                f"{_safe_user_text(current_range.get('end', '?'))}"
            )
        cache_hit = payload.get("cache_hit")
        st.write(
            "cache: "
            + ("hit" if cache_hit is True else "miss" if cache_hit is False else "検査中")
        )
        st.write(f"区間 status: {_safe_user_text(payload.get('status', '処理中'))}")
        diagnostic = payload.get("diagnostic")
        if diagnostic:
            st.caption(_safe_user_text(diagnostic))


def _render_structured_whisper_error(detail: str) -> None:
    payload = _parse_s9_payload(detail, S9_WHISPER_ERROR_PREFIX)
    if payload is None:
        return
    st.warning("高精度字幕は完了扱いにせず、既存成果物を維持しました。")
    st.write(f"job ID: {_safe_user_text(payload.get('job_id', '不明'))}")
    st.write(
        f"range: {_safe_user_text(payload.get('range_index', '不明'))} / "
        f"{_safe_user_text(payload.get('range_total', '不明'))}"
    )
    st.write(f"再試行: {'可' if payload.get('retryable') else '不可'}")
    st.write(f"既存成果物: {_safe_user_text(payload.get('existing_artifacts', '維持'))}")
    st.write(f"次操作: {_safe_user_text(payload.get('next_action', '詳細を確認してください。'))}")
    ranges = payload.get("ranges")
    if isinstance(ranges, list):
        for item in ranges:
            if not isinstance(item, dict):
                continue
            current = item.get("current_range")
            if isinstance(current, dict):
                range_label = (
                    f"{current.get('id', '不明')} "
                    f"{current.get('start', '?')} → {current.get('end', '?')}"
                )
            else:
                range_label = "区間不明"
            st.caption(
                f"区間 {item.get('range_index', '?')}/{item.get('range_total', '?')}: "
                f"{_safe_user_text(range_label)}・status "
                f"{_safe_user_text(item.get('status', 'failed'))}"
            )


def _mark_description_update_started(video_id: str) -> None:
    updated_ids = _description_updated_ids()
    updated_ids.add(video_id)
    st.session_state[_DESCRIPTION_UPDATED_IDS_KEY] = updated_ids


def _clear_description_update_started(video_id: str) -> None:
    updated_ids = _description_updated_ids()
    updated_ids.discard(video_id)
    if updated_ids:
        st.session_state[_DESCRIPTION_UPDATED_IDS_KEY] = updated_ids
    else:
        st.session_state.pop(_DESCRIPTION_UPDATED_IDS_KEY, None)


def _save_description_completion(
    video: ProcessedVideo,
    settings: Settings,
) -> bool:
    """ローカル完了記録だけを保存し、成功時に再描画する."""
    try:
        mark_description_applied(video.video_id, settings)
    # ファイル I/O 境界では認証ラッパー等の例外型も一定しないため、
    # BaseException を除く全例外を日本語メッセージへ変換する。
    except Exception as exc:
        _mark_description_update_started(video.video_id)
        st.warning(
            "YouTube 側は更新済みですが、完了状態を保存できませんでした。"
            "データ保存先の権限を確認し、完了状態の保存を再試行してください。"
            f"（詳細: {_safe_user_text(exc)}）"
        )
        return False

    _clear_description_update_started(video.video_id)
    st.session_state[_DESCRIPTION_SUCCESS_KEY] = (
        f"{_safe_user_text(video.title)} の YouTube 概要欄を更新しました。"
    )
    st.rerun()
    return True


@st.dialog("YouTube 概要欄への反映確認", width="large")
def _description_preview_dialog(
    video: ProcessedVideo,
    before: str,
    after: str,
    settings: Settings,
) -> None:
    """取得済みの更新前後を表示し、確定時だけ YouTube を更新する."""
    st.warning(
        "YouTube 上の公開データを書き換えます。"
        "更新前と更新後を確認してから確定してください。"
    )
    before_column, after_column = st.columns(2)
    with before_column:
        st.markdown("**更新前**")
        st.text_area(
            "現在の概要欄",
            value=_safe_user_text(before),
            height=360,
            disabled=True,
            key=f"detail_description_before_{video.video_id}",
        )
    with after_column:
        st.markdown("**更新後**")
        st.text_area(
            "反映後の概要欄",
            value=_safe_user_text(after),
            height=360,
            disabled=True,
            key=f"detail_description_after_{video.video_id}",
        )

    busy = is_busy()
    already_updated = video.video_id in _description_updated_ids()
    completion_only = already_updated or before == after
    if busy:
        st.info(_BUSY_MESSAGE)
    if already_updated:
        st.warning(
            "YouTube 側は更新済みですが、完了状態を保存できていません。"
            "YouTube は再更新せず、完了状態の保存だけを再試行できます。"
        )
    elif completion_only:
        st.info(
            "YouTube の概要欄には同じタイムラインが既に反映されています。"
            "YouTube は更新せず、完了状態だけを保存します。"
        )

    with st.container(horizontal=True):
        cancel_clicked = st.button(
            "キャンセル",
            key=f"detail_description_cancel_{video.video_id}",
        )
        confirm_clicked = st.button(
            (
                "完了状態の保存を再試行"
                if completion_only
                else "この内容を概要欄に反映"
            ),
            key=f"detail_description_confirm_{video.video_id}",
            type="primary",
            disabled=busy,
        )

    if cancel_clicked:
        st.rerun()
    if not confirm_clicked or busy:
        return

    if completion_only:
        _mark_description_update_started(video.video_id)
        _save_description_completion(video, settings)
        return

    try:
        update_video_description(video.video_id, after, settings)
    # YouTube クライアント境界では I/O・認証ライブラリ由来の例外型が
    # 一定しないため、BaseException を除く全例外を安全な表示へ変換する。
    except Exception as exc:
        st.error(
            "YouTube の概要欄を更新できませんでした。"
            f"時間をおいて再試行してください（詳細: {_safe_user_text(exc)}）。"
        )
        return

    # YouTube 更新直後に記録し、ローカル保存失敗時の二重更新を防ぐ。
    _mark_description_update_started(video.video_id)
    _save_description_completion(video, settings)


def _start_description_preview(
    video: ProcessedVideo,
    chapters_text: str,
    settings: Settings,
) -> None:
    """検証後に概要欄の更新前後を取得し、共通ダイアログを開く."""
    if is_busy():
        st.info(_BUSY_MESSAGE)
        return
    if video.video_id in _description_updated_ids():
        _description_preview_dialog(video, "", "", settings)
        return
    try:
        configured = is_configured(settings)
    # OAuth 設定確認もファイル I/O 境界のため、予期可能な外部例外を
    # スタックトレースではなく日本語の案内へ変換する。
    except Exception as exc:
        st.error(
            "YouTube OAuth 設定を確認できませんでした。"
            f"設定ファイルを確認してください（詳細: {_safe_user_text(exc)}）。"
        )
        return
    if not configured:
        st.error(
            "YouTube OAuth が設定されていません。"
            "設定ファイルを配置してから、もう一度お試しください。"
        )
        return
    if not chapters_text.strip():
        st.error(
            "反映できるチャプターがありません。"
            "先にチャプターを生成してください。"
        )
        return

    validation = validate_chapters(chapters_text)
    if not validation.ok:
        st.error(
            "チャプターの形式が不正なため、概要欄プレビューを開始できません。\n\n"
            + "\n".join(
                f"・{_safe_user_text(error)}" for error in validation.errors
            )
        )
        return

    try:
        snippet = fetch_video_snippet(video.video_id, settings)
        before = str(snippet.get("description") or "")
        after = merge_chapters_into_description(before, chapters_text)
    # YouTube API・認証・I/O の外部境界では例外型が一定しないため、
    # BaseException を除く全例外をサニタイズした日本語表示へ変換する。
    except Exception as exc:
        st.error(
            "概要欄プレビューを作成できませんでした。"
            f"時間をおいて再試行してください（詳細: {_safe_user_text(exc)}）。"
        )
        return
    _description_preview_dialog(video, before, after, settings)


def _render_description_control(
    video: ProcessedVideo,
    chapters_text: str,
    settings: Settings,
    *,
    busy: bool,
) -> None:
    st.warning(
        "概要欄への反映は YouTube 上の公開データを書き換えます。"
        "確定前に更新前後を必ず確認できます。"
    )
    clicked = st.button(
        "概要欄に反映",
        key=f"detail_description_open_{video.video_id}",
        type="primary",
        disabled=busy,
    )
    if clicked and not busy:
        _start_description_preview(video, chapters_text, settings)


def _render_regenerate_control(
    video: ProcessedVideo,
    *,
    target: Literal["chapters", "clips"],
    complete: bool,
    busy: bool,
    settings: Settings,
) -> None:
    target_label = "チャプター" if target == "chapters" else "切り抜き候補"
    button_label = f"{target_label}を再生成" if complete else f"{target_label}を生成"

    def render_button() -> None:
        if st.button(
            button_label,
            key=f"detail_regen_{target}_{video.video_id}",
            type="secondary" if complete else "primary",
            disabled=busy or not video.has_transcript,
            help=(
                None
                if video.has_transcript
                else "先に字幕の取得と整形を実行してください。"
            ),
        ):
            _confirm_regenerate_dialog(video, target, settings)

    if complete:
        st.caption("既存成果物を退避してから再生成します。")
    render_button()


def _render_transcript(result: PipelineResult) -> None:
    st.subheader("1. 字幕・文字起こし全文")
    st.text_area(
        "全文",
        value=result.full_transcript_text,
        height=400,
        disabled=True,
        label_visibility="collapsed",
    )
    render_copy_button(
        result.full_transcript_text,
        label="全文をコピー",
        key=f"detail_copy_transcript_{result.video_id}",
    )


def _render_job_error_notification(
    notification: JobErrorNotification,
    *,
    video_id: str,
    index: int,
    settings: Settings,
) -> None:
    """動画詳細内の構造化ジョブエラーを安全な native UI で表示する."""
    with st.container(border=True, gap="small"):
        st.text(
            "処理種別: "
            + sanitize_job_error_for_display(
                job_display_label(
                    notification.kind,
                    detail=notification.detail,
                )
            )
        )
        st.text(
            "発生日時: "
            + notification.occurred_at.isoformat(timespec="seconds")
        )
        st.text(
            "job ID: "
            + sanitize_job_error_for_display(notification.job_id or "（なし）")
        )
        st.text(
            "要約: "
            + format_job_error_summary_for_display(notification.summary)
        )

        detail = notification.detail
        if not detail and notification.job_id:
            detail = read_job_error_log(
                notification.job_id,
                settings,
                max_bytes=_MAX_DETAIL_JOB_ERROR_LOG_BYTES,
            )
        if detail:
            if notification.kind in {"short_cut", "short_cut_refine"}:
                _render_structured_whisper_error(detail)
            job_key = notification.job_id or f"missing-{index}"
            st.text_area(
                "技術ログ",
                value=sanitize_job_error_for_display(detail),
                height=220,
                disabled=True,
                key=f"detail_job_error_log_{video_id}_{job_key}",
            )


def _render_video_error_history(video_id: str, *, settings: Settings) -> None:
    """現在の動画に紐づく直近エラーだけを詳細領域へ表示する."""
    notifications = [
        notification
        for notification in get_job_error_history(video_id)
        if notification.video_id == video_id
    ]
    notifications = sorted(
        notifications,
        key=lambda notification: notification.occurred_at,
        reverse=True,
    )[:JOB_ERROR_HISTORY_LIMIT]
    if not notifications:
        return

    st.subheader("エラー履歴")
    for index, notification in enumerate(notifications):
        _render_job_error_notification(
            notification,
            video_id=video_id,
            index=index,
            settings=settings,
        )


def _render_chapters(
    video: ProcessedVideo,
    result: PipelineResult,
    *,
    busy: bool,
    settings: Settings,
) -> None:
    st.subheader("2. チャプター")
    has_chapters = video.has_chapters or bool(result.chapters_text.strip())
    if has_chapters:
        st.code(result.chapters_text, language="markdown")
        render_copy_button(
            result.chapters_text,
            label="タイムラインをコピー",
            key=f"detail_copy_chapters_{result.video_id}",
        )
    else:
        st.info("チャプターはまだ生成されていません。")
    _render_regenerate_control(
        video,
        target="chapters",
        complete=has_chapters,
        busy=busy,
        settings=settings,
    )


def _render_clips(
    video: ProcessedVideo,
    result: PipelineResult,
    *,
    busy: bool,
    settings: Settings,
    has_highlights: bool,
) -> None:
    st.subheader("3. 切り抜き候補")
    if result.clips_error:
        st.warning(
            "切り抜き候補の生成に失敗しましたが、他の成果物は利用できます。\n\n"
            f"{result.clips_error}"
        )
    if result.clips_candidates:
        for candidate in result.clips_candidates:
            with st.container(border=True):
                st.markdown(f"**{candidate.title}**")
                st.caption(
                    f"{candidate.start} → {candidate.end}（{candidate.duration_sec} 秒）"
                )
                st.write(candidate.reason)
    elif not result.clips_error:
        st.info("切り抜き候補はまだ生成されていません。")

    _render_regenerate_control(
        video,
        target="clips",
        complete=video.has_clips or bool(result.clips_candidates) or has_highlights,
        busy=busy,
        settings=settings,
    )


def _transfer_key(video_id: str, source: str) -> str:
    return candidate_transfer_key(video_id, source)


def _transfer_order_key(video_id: str) -> str:
    return candidate_transfer_order_key(video_id)


def _load_transfer_order(video_id: str) -> tuple[CandidateKey, ...]:
    raw = st.session_state.get(_transfer_order_key(video_id))
    if not isinstance(raw, list):
        return ()
    values: list[CandidateKey] = []
    for item in raw:
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and item[0] in {"clips", "highlights"}
            and isinstance(item[1], str)
        ):
            key: CandidateKey = (item[0], item[1])
            if key not in values:
                values.append(key)
    return tuple(values)


def _load_transfer(video_id: str, source: str) -> CandidateTransfer | None:
    raw = st.session_state.get(_transfer_key(video_id, source))
    if not isinstance(raw, dict):
        return None
    try:
        return CandidateTransfer(
            source=source,  # type: ignore[arg-type]
            selected_ids=tuple(str(value) for value in raw["selected_ids"]),
            fingerprint=str(raw["fingerprint"]),
        )
    except (KeyError, TypeError):
        return None


def _save_transfer(video_id: str, transfer: CandidateTransfer) -> None:
    st.session_state[_transfer_key(video_id, transfer.source)] = {
        "source": transfer.source,
        "selected_ids": transfer.selected_ids,
        "fingerprint": transfer.fingerprint,
    }
    order = list(_load_transfer_order(video_id))
    selected = {(transfer.source, candidate_id) for candidate_id in transfer.selected_ids}
    order = [
        key for key in order if key[0] != transfer.source or key in selected
    ]
    for candidate_id in transfer.selected_ids:
        key: CandidateKey = (transfer.source, candidate_id)
        if key not in order:
            order.append(key)
    st.session_state[_transfer_order_key(video_id)] = order


def _valid_transfer_candidates(
    video_id: str,
    *,
    clips: list[ClipCandidate],
    highlights: list[HighlightSegment],
    candidate_fingerprints: Mapping[str, str] | None = None,
) -> tuple[CandidateKey, ...]:
    """全 workspace 描画前に引き継ぎを再検証し、stale 状態を破棄する."""
    valid: list[CandidateKey] = []
    invalidated = False
    for source, candidates in (("clips", clips), ("highlights", highlights)):
        transfer = _load_transfer(video_id, source)
        if transfer is None:
            continue
        current = validate_candidate_transfer(
            transfer,
            current_fingerprint=(
                candidate_fingerprints[source]
                if candidate_fingerprints is not None
                and source in candidate_fingerprints
                else make_candidate_fingerprint(source, candidates)
            ),
            candidate_ids={candidate.id for candidate in candidates},
        )
        if current is None:
            st.session_state.pop(_transfer_key(video_id, source), None)
            invalidated = True
        else:
            valid.extend((source, candidate_id) for candidate_id in current.selected_ids)
    valid_set = set(valid)
    values = [key for key in _load_transfer_order(video_id) if key in valid_set]
    values.extend(key for key in valid if key not in values)
    st.session_state[_transfer_order_key(video_id)] = values
    if invalidated:
        st.warning("候補が更新されました。ショート作成対象を選び直してください。")
    return tuple(values)


def _set_workspace(video_id: str, workspace: Workspace) -> None:
    """widget callback 内で次 rerun の作業選択を更新する."""
    st.session_state[detail_workspace_key(video_id)] = workspace


def _render_state_summary(summary: DetailSummary) -> None:
    columns = st.columns(3)
    with columns[0].container(border=True, height="stretch"):
        st.markdown("**素材候補**")
        st.write(f"{summary.candidate_count} 件")
    with columns[1].container(border=True, height="stretch"):
        st.markdown("**ショート**")
        st.write(
            f"生成 {summary.generated_short_count} 本・"
            f"予約可能 {summary.reservable_short_count} 本"
        )
    with columns[2].container(border=True, height="stretch"):
        st.markdown("**概要欄**")
        st.write("反映済み" if summary.description_applied else "未反映")


def _existing_artifacts(video_id: str, settings: Settings) -> tuple[str, ...]:
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        return ()
    labels: list[str] = []
    for label, relative in (
        ("動画メタデータ", Path("meta.json")),
        ("字幕ファイル", Path("subtitles/ja.vtt")),
        ("チャプター", Path("chapters.md")),
        ("切り抜き候補", Path("clips/candidates.json")),
        ("ハイライト候補", Path("highlights/segments.json")),
    ):
        if (video_dir / relative).is_file():
            labels.append(label)
    return tuple(labels)


def _render_recovery_state(
    video_id: str,
    settings: Settings,
    *,
    run_page: StreamlitPage | None,
) -> None:
    st.warning("保存済みの字幕成果物を読み込めませんでした。")
    st.write(
        "字幕の保存結果が欠けているか壊れているため、安全のため通常の作業画面を"
        "停止しています。取り込みからこの動画を再処理してください。"
    )
    if st.button(
        "取り込みで再処理",
        key=f"detail_recover_{video_id}",
        type="primary",
        disabled=run_page is None,
    ) and run_page is not None:
        st.switch_page(run_page)

    details = st.expander(
        "読み込み状況の詳細",
        expanded=False,
        key=f"detail_recovery_details_{video_id}",
        on_change="rerun",
    )
    if details.open:
        with details:
            st.write(f"動画 ID: {_safe_user_text(video_id)}")
            artifacts = _existing_artifacts(video_id, settings)
            st.write(
                "存在する成果物: " + ("、".join(artifacts) if artifacts else "なし")
            )


def _load_material_candidates(
    result: PipelineResult,
    settings: Settings,
) -> tuple[list[ClipCandidate], list[HighlightSegment]]:
    clip_doc = load_candidates_file(result.video_id, settings)
    clips = (
        list(clip_doc.candidates)
        if clip_doc is not None
        else list(result.clips_candidates)
    )
    highlight_doc = load_segments_file(result.video_id, settings)
    highlights = list(highlight_doc.candidates) if highlight_doc is not None else []
    return clips, highlights


def _load_material_candidate_fingerprints(
    video_id: str,
    settings: Settings,
    *,
    clips: list[ClipCandidate],
    highlights: list[HighlightSegment],
) -> dict[str, str]:
    """Resolve saved clip lineage once, with legacy/highlight content fallback."""
    try:
        clip_document = load_candidates_file(video_id, settings)
    except (OSError, UnicodeError, TypeError, ValueError):
        clip_document = None
    lineage = getattr(clip_document, "lineage", None)
    return {
        "clips": current_candidate_fingerprint(
            "clips",
            clips,
            persisted_candidate_fingerprint=(
                lineage.candidate_fingerprint if lineage is not None else None
            ),
        ),
        "highlights": current_candidate_fingerprint("highlights", highlights),
    }


def _candidate_provenance_text(
    video_id: str,
    source: str,
    settings: Settings,
) -> str:
    """保存済み coarse candidate lineage を候補カード header 用に返す."""
    if source != "clips":
        return "candidate provenance: coarse VTT（候補 fingerprint は保存されていません）"
    try:
        document = load_candidates_file(video_id, settings)
    except (OSError, UnicodeError, TypeError, ValueError):
        document = None
    lineage = getattr(document, "lineage", None)
    if lineage is None:
        return "candidate provenance: coarse VTT（lineage 未確認）"
    return (
        "candidate provenance: coarse VTT・"
        f"candidate fingerprint {lineage.candidate_fingerprint}・"
        f"VTT fingerprint {lineage.coarse_vtt_artifact_fingerprint}"
    )


def _render_materials_workspace(
    result: PipelineResult,
    settings: Settings,
    *,
    clips: list[ClipCandidate],
    highlights: list[HighlightSegment],
    candidate_fingerprints: Mapping[str, str] | None = None,
) -> None:
    st.caption("候補を確認し、作成するショートへ同じ順序で引き継ぎます。")
    available: list[Literal["clips", "highlights"]] = []
    if clips:
        available.append("clips")
    if highlights:
        available.append("highlights")
    if not available:
        st.info("素材候補がありません。詳細・再生成から候補を生成してください。")
        return

    source: Literal["clips", "highlights"] = available[0]
    if len(available) == 2:
        selected = st.segmented_control(
            "候補ソース",
            available,
            default=available[0],
            required=True,
            format_func=lambda value: (
                "切り抜き候補" if value == "clips" else "ハイライト候補"
            ),
            key=f"materials_source_{result.video_id}",
            width="stretch",
        )
        if selected in available:
            source = selected

    candidates: list[ClipCandidate] | list[HighlightSegment] = (
        clips if source == "clips" else highlights
    )
    fingerprint = (
        candidate_fingerprints[source]
        if candidate_fingerprints is not None and source in candidate_fingerprints
        else make_candidate_fingerprint(source, candidates)
    )
    transfer = _load_transfer(result.video_id, source)
    valid_transfer = validate_candidate_transfer(
        transfer,
        current_fingerprint=fingerprint,
        candidate_ids={candidate.id for candidate in candidates},
    )
    if transfer is not None and valid_transfer is None:
        st.session_state.pop(_transfer_key(result.video_id, source), None)
        st.warning("候補が更新されました。ショート作成対象を選び直してください。")
    selected_ids = list(valid_transfer.selected_ids if valid_transfer else ())
    candidate_provenance = _candidate_provenance_text(
        result.video_id,
        source,
        settings,
    )

    for candidate in candidates:
        with st.container(border=True):
            st.markdown(f"**{_safe_user_text(candidate.title)}**")
            st.caption(candidate_provenance)
            st.caption(
                f"{_safe_user_text(candidate.start)} → {_safe_user_text(candidate.end)}"
                f"（{candidate.duration_sec} 秒）"
            )
            st.write(_safe_user_text(candidate.reason))
            already_added = candidate.id in selected_ids
            if st.button(
                "追加済み" if already_added else "ショート作成対象へ追加",
                key=f"materials_add_{result.video_id}_{source}_{candidate.id}",
                disabled=already_added,
            ):
                selected_ids.append(candidate.id)
                _save_transfer(
                    result.video_id,
                    CandidateTransfer(source, tuple(selected_ids), fingerprint),
                )
                st.rerun()

    if selected_ids:
        st.caption(f"ショート作成へ引き継ぐ候補: {len(selected_ids)} 件")
        st.button(
            "選択した候補でショート作成へ",
            key=f"materials_to_shorts_{result.video_id}_{source}",
            type="primary",
            on_click=_set_workspace,
            args=(result.video_id, "shorts"),
        )

    st.divider()
    st.markdown("#### 補助操作")
    render_highlights_section(result)


def _render_publish_workspace(
    video: ProcessedVideo,
    result: PipelineResult,
    settings: Settings,
    *,
    busy: bool,
    summary: DetailSummary,
) -> None:
    st.markdown("#### ショートの予約投稿")
    st.caption(
        "ショート制作ラインの工程 6 です。生成済みショートを private 固定・"
        "通知なしでアップロードし、次の空き枠へ予約します。"
    )
    # Tracking is independent from whether the latest manifest currently has
    # a new reservable item.  The adapter keeps both line safety callbacks as
    # one required value so a layout refactor cannot accidentally drop one.
    projection = render_upload_section(
        video.video_id,
        settings,
        line_adapter=make_line_upload_adapter(video.video_id, settings),
    )
    # 予約対象が無い理由は render_upload_section が manifest の状態から診断済み。
    # ここではその診断を繰り返さず、ラインへ戻る出口だけを足して行き止まりを防ぐ。
    # 「生成済みだが予約対象に入っていない」だけは manifest から導けないため残す。
    if summary.reservable_short_count == 0:
        if summary.generated_short_count:
            st.info(
                "生成済みですが予約対象に追加されていません。"
                "ショート生産ラインで最終確認まで進めてください。"
            )
        st.button(
            "ショート生産ラインへ",
            key=f"publish_to_shorts_{video.video_id}",
            on_click=_set_workspace,
            args=(video.video_id, "shorts"),
        )

    st.divider()
    st.markdown("#### 投稿後のフォローアップ")
    st.caption(
        "予約・投稿が済んだショートの追跡と、YouTube Studio での関連動画設定です。"
    )
    render_post_upload_followup(video.video_id, settings, projection=projection)

    st.divider()
    st.markdown("#### 元動画の概要欄")
    st.caption("ショート制作ラインとは別の作業です。元動画の説明にチャプターを反映します。")
    validation = validate_chapters(result.chapters_text)
    chapter_count = len(
        [line for line in result.chapters_text.splitlines() if line.strip()]
    )
    with st.container(border=True):
        if not result.chapters_text.strip():
            st.info("チャプターは未生成です。")
        elif validation.ok:
            st.success(f"チャプター生成済み・{chapter_count} 件・形式 OK")
        else:
            st.error("チャプターの形式エラーがあります。詳細・再生成で確認してください。")
        st.caption("反映済み" if summary.description_applied else "未反映")
        _render_description_control(
            video,
            result.chapters_text,
            settings,
            busy=busy or not validation.ok,
        )


def _render_details_and_regeneration(
    video: ProcessedVideo,
    result: PipelineResult,
    *,
    settings: Settings,
    busy: bool,
) -> None:
    details = st.expander(
        "詳細・再生成",
        expanded=False,
        key=f"detail_regeneration_{video.video_id}",
        on_change="rerun",
    )
    if not details.open:
        return
    with details:
        _render_video_error_history(video.video_id, settings=settings)
        _render_transcript(result)
        _render_chapters(video, result, busy=busy, settings=settings)
        st.markdown("**候補の再生成**")
        if result.clips_error:
            st.warning(_safe_user_text(result.clips_error))
        _render_regenerate_control(
            video,
            target="clips",
            complete=video.has_clips or bool(result.clips_candidates),
            busy=busy,
            settings=settings,
        )
        st.markdown("**元動画と中間ファイルの管理**")
        st.caption(
            "削除してもチャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
        )
        if st.button(
            "元動画を削除",
            key=f"detail_purge_{video.video_id}",
            disabled=busy,
        ):
            _confirm_source_purge_dialog(video, settings)


def render_video_detail_page(
    *,
    run_page: StreamlitPage | None = None,
) -> None:
    """選択中の動画について、保存済み成果物から詳細ページを再構築する."""
    video_id = get_selected_video_id()
    if video_id is None:
        st.header("動画詳細")
        st.info("ライブラリから動画を選択してください。")
        return

    settings = get_settings()
    video = next(
        (
            item
            for item in list_processed_videos(settings)
            if item.video_id == video_id
        ),
        None,
    )
    if video is None:
        st.warning(
            "選択した動画が見つかりません。"
            "ライブラリに戻り、動画を選び直してください。"
        )
        return

    st.header(_safe_user_text(video.title))
    st.caption(f"動画 ID: {_safe_user_text(video.video_id)}")
    description_success = st.session_state.pop(_DESCRIPTION_SUCCESS_KEY, None)
    if description_success:
        st.success(description_success)
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)
    active_job = get_active_job(settings)
    if active_job is not None:
        _render_whisper_job_progress(active_job, video_id=video_id)

    result = load_result_from_disk(video_id, settings)
    if result is None:
        _render_recovery_state(video_id, settings, run_page=run_page)
        return

    clips, highlights = _load_material_candidates(result, settings)
    candidate_fingerprints = _load_material_candidate_fingerprints(
        video.video_id,
        settings,
        clips=clips,
        highlights=highlights,
    )
    preferred_transfer_candidates = _valid_transfer_candidates(
        video.video_id,
        clips=clips,
        highlights=highlights,
        candidate_fingerprints=candidate_fingerprints,
    )
    try:
        reservable_count = count_reservable_shorts(video.video_id, settings)
    except ShortsQueueError as exc:
        reservable_count = 0
        st.warning(
            "予約可能なショートを安全に確認できませんでした。"
            f"詳細: {_safe_user_text(exc)}"
        )
    summary = calculate_detail_summary(
        clip_count=len(clips),
        highlight_count=len(highlights),
        generated_short_count=count_shorts(video.video_id, settings),
        reservable_short_count=reservable_count,
        description_applied=video.video_id in load_description_applied_ids(settings),
    )
    _render_state_summary(summary)
    render_main_line_summary(video.video_id, settings)

    default_workspace = choose_initial_workspace(
        video_id=video.video_id,
        candidate_count=summary.candidate_count,
        reservable_short_count=summary.reservable_short_count,
        active_job=active_job,
    )
    workspace = st.segmented_control(
        "作業を選択",
        _WORKSPACES,
        default=default_workspace,
        required=True,
        format_func=lambda value: _WORKSPACE_LABELS[value],
        key=detail_workspace_key(video.video_id),
        width="stretch",
    )
    selected_workspace: Workspace = (
        workspace if workspace in _WORKSPACES else default_workspace
    )

    if selected_workspace == "materials":
        _render_materials_workspace(
            result,
            settings,
            clips=clips,
            highlights=highlights,
            candidate_fingerprints=candidate_fingerprints,
        )
    elif selected_workspace == "shorts":
        render_shorts_line(
            video_id=video.video_id,
            title=result.title,
            clip_candidates=clips,
            highlight_candidates=highlights,
            settings=settings,
            preferred_candidate_keys=preferred_transfer_candidates,
        )
        render_shorts_section(result, expanded=False)
    else:
        _render_publish_workspace(
            video,
            result,
            settings,
            busy=busy,
            summary=summary,
        )

    _render_details_and_regeneration(
        video,
        result,
        settings=settings,
        busy=busy,
    )
