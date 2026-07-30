"""処理済み一覧タブ."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.history import ProcessedVideo, list_processed_videos
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import load_result_from_disk, regenerate_job_target
from yt_live_kit.services.storage import (
    StorageError,
    StorageSummary,
    VideoStorage,
    format_bytes,
    purge_source,
    purge_sources_older_than,
    summarize,
)
from yt_live_kit.ui.state import clear_cut_result, set_active_job_id, set_result

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_REGENERATE_NOTE = (
    "再生成時は既存の成果物を .bak に退避してから上書きします。"
)
_SESSION_STORAGE_SUMMARY = "history_storage_summary"
_SESSION_PURGE_CONFIRM = "history_purge_confirm_ids"
_SESSION_BULK_PREVIEW = "history_bulk_purge_preview"
_SESSION_PURGE_SUCCESS = "history_purge_success_message"


def chapter_button_label(has_chapters: bool) -> str:
    """チャプター再生成ボタンのラベルを返す."""
    if has_chapters:
        return "チャプターを再生成"
    return "チャプターを生成"


def clips_button_label(has_clips: bool) -> str:
    """切り抜き候補再生成ボタンのラベルを返す."""
    if has_clips:
        return "切り抜き候補を再生成"
    return "切り抜き候補を生成"


def source_bytes_map(summary: StorageSummary) -> dict[str, int]:
    """動画 ID から元動画バイト数へのマップを返す（行バッジ表示用）."""
    return {video.video_id: video.source_bytes for video in summary.videos}


def deletable_bytes_map(summary: StorageSummary) -> dict[str, int]:
    """動画 ID から削除対象バイト数（元動画＋中間ファイル）へのマップを返す."""
    return {
        video.video_id: video.source_bytes + video.intermediate_bytes
        for video in summary.videos
    }


def format_video_storage_row(video: VideoStorage) -> str:
    """ストレージ内訳 1 行分の表示文字列を返す."""
    title = video.title or video.video_id
    return (
        f"{title} — 合計 {format_bytes(video.total_bytes)} "
        f"（元動画 {format_bytes(video.source_bytes)} / "
        f"成果物 {format_bytes(video.output_bytes)} / "
        f"中間 {format_bytes(video.intermediate_bytes)} / "
        f"その他 {format_bytes(video.other_bytes)}）"
    )


def preview_purge_sources_older_than(
    processed: list[ProcessedVideo],
    days: int,
    *,
    now: datetime | None = None,
    deletable_bytes_for: Callable[[str], int] | None = None,
) -> tuple[int, int]:
    """N 日より古い削除対象（元動画＋中間ファイル）の件数と合計バイト数を返す."""
    if days < 0:
        return 0, 0

    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=days)
    count = 0
    total_bytes = 0

    for video in processed:
        fetched_at = video.fetched_at
        if fetched_at is None:
            continue
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        else:
            fetched_at = fetched_at.astimezone(timezone.utc)
        if fetched_at >= cutoff:
            continue

        deletable_bytes = (
            deletable_bytes_for(video.video_id) if deletable_bytes_for else 0
        )
        if deletable_bytes <= 0:
            continue
        count += 1
        total_bytes += deletable_bytes

    return count, total_bytes


def _get_storage_summary() -> StorageSummary | None:
    raw = st.session_state.get(_SESSION_STORAGE_SUMMARY)
    if isinstance(raw, StorageSummary):
        return raw
    return None


def _set_storage_summary(summary: StorageSummary) -> None:
    st.session_state[_SESSION_STORAGE_SUMMARY] = summary


def _purge_confirm_ids() -> set[str]:
    raw = st.session_state.get(_SESSION_PURGE_CONFIRM)
    if isinstance(raw, set):
        return raw
    return set()


def _request_purge_confirm(video_id: str) -> None:
    ids = _purge_confirm_ids()
    ids.add(video_id)
    st.session_state[_SESSION_PURGE_CONFIRM] = ids


def _clear_purge_confirm(video_id: str) -> None:
    ids = _purge_confirm_ids()
    ids.discard(video_id)
    st.session_state[_SESSION_PURGE_CONFIRM] = ids


def _get_bulk_preview() -> dict[str, object] | None:
    raw = st.session_state.get(_SESSION_BULK_PREVIEW)
    if isinstance(raw, dict):
        return raw
    return None


def _set_bulk_preview(days: int, count: int, total_bytes: int) -> None:
    st.session_state[_SESSION_BULK_PREVIEW] = {
        "days": days,
        "count": count,
        "total_bytes": total_bytes,
    }


def _clear_bulk_preview() -> None:
    st.session_state[_SESSION_BULK_PREVIEW] = None


def _get_purge_success_message() -> str | None:
    raw = st.session_state.get(_SESSION_PURGE_SUCCESS)
    if isinstance(raw, str):
        return raw
    return None


def _set_purge_success_message(message: str) -> None:
    st.session_state[_SESSION_PURGE_SUCCESS] = message


def _clear_purge_success_message() -> None:
    st.session_state[_SESSION_PURGE_SUCCESS] = None


def _start_regenerate(video: ProcessedVideo, target: str, settings: Settings) -> None:
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


def _render_row_actions(
    video: ProcessedVideo,
    *,
    busy: bool,
    settings: Settings,
) -> None:
    # has_transcript は full.txt の有無。regenerate が必要とする compressed.txt とは一致しない場合がある。
    regen_disabled = busy or not video.has_transcript
    regen_help = (
        None
        if video.has_transcript
        else "先に字幕の取得と整形を実行してください。"
    )
    action_cols = st.columns([2, 2, 2, 1])
    with action_cols[0]:
        if st.button(
            chapter_button_label(video.has_chapters),
            key=f"regen_chapters_{video.video_id}",
            disabled=regen_disabled,
            help=regen_help,
        ):
            _start_regenerate(video, "chapters", settings)
    with action_cols[1]:
        if st.button(
            clips_button_label(video.has_clips),
            key=f"regen_clips_{video.video_id}",
            disabled=regen_disabled,
            help=regen_help,
        ):
            _start_regenerate(video, "clips", settings)
    with action_cols[2]:
        confirming = video.video_id in _purge_confirm_ids()
        if confirming:
            st.warning(
                "元動画と中間ファイルを削除します。チャプター・全文・切り抜き候補・"
                "切り出し済み動画は残ります。"
            )
            confirm_cols = st.columns(2)
            with confirm_cols[0]:
                if st.button(
                    "削除を実行",
                    key=f"purge_exec_{video.video_id}",
                    type="primary",
                    disabled=busy,
                ):
                    try:
                        deleted = purge_source(video.video_id, settings)
                        _clear_purge_confirm(video.video_id)
                        summary = _get_storage_summary()
                        if summary is not None:
                            _set_storage_summary(summarize(settings))
                        _set_purge_success_message(
                            f"元動画と中間ファイルを削除しました（{format_bytes(deleted)}）。"
                        )
                        st.rerun()
                    except StorageError as exc:
                        st.error(str(exc))
            with confirm_cols[1]:
                if st.button(
                    "キャンセル",
                    key=f"purge_cancel_{video.video_id}",
                    disabled=busy,
                ):
                    _clear_purge_confirm(video.video_id)
                    st.rerun()
        elif st.button(
            "元動画を削除",
            key=f"purge_{video.video_id}",
            disabled=busy,
        ):
            _request_purge_confirm(video.video_id)
            st.rerun()
    with action_cols[3]:
        # 読み取り専用のため busy 中も有効のまま
        if st.button("開く", key=f"open_{video.video_id}"):
            loaded = load_result_from_disk(video.video_id, settings)
            if loaded is None:
                st.error("成果物を読み込めませんでした。")
            else:
                set_result(loaded)
                clear_cut_result()
                st.rerun()


def _render_storage_section(
    processed: list[ProcessedVideo],
    settings: Settings,
    *,
    busy: bool,
) -> None:
    with st.expander("ストレージ", expanded=False):
        summary = _get_storage_summary()
        if summary is None:
            st.caption(
                "容量の集計は data/ 全体を走査するため、必要なときだけ計算します。"
            )
            if st.button(
                "容量を計算",
                key="history_calc_storage",
                disabled=busy,
            ):
                _set_storage_summary(summarize(settings))
                st.rerun()
            return

        st.markdown(f"**合計容量:** {format_bytes(summary.total_bytes)}")
        st.caption("容量上位 10 件")
        for video in summary.videos[:10]:
            st.text(format_video_storage_row(video))

        if st.button(
            "容量を再計算",
            key="history_recalc_storage",
            disabled=busy,
        ):
            _set_storage_summary(summarize(settings))
            st.rerun()

        st.divider()
        st.markdown("**古い元動画・中間ファイルの一括削除**")
        days = st.number_input(
            "日数（この日数より前に取得した動画の元動画・中間ファイルを対象）",
            min_value=1,
            value=30,
            step=1,
            key="history_bulk_purge_days",
        )
        deletable_map = deletable_bytes_map(summary)
        preview = _get_bulk_preview()

        preview_cols = st.columns([1, 1])
        with preview_cols[0]:
            if st.button(
                "対象を確認",
                key="history_bulk_preview",
                disabled=busy,
            ):
                count, total_bytes = preview_purge_sources_older_than(
                    processed,
                    int(days),
                    deletable_bytes_for=lambda vid: deletable_map.get(vid, 0),
                )
                _set_bulk_preview(int(days), count, total_bytes)
                st.rerun()
        with preview_cols[1]:
            if st.button(
                "確認をクリア",
                key="history_bulk_clear_preview",
                disabled=busy,
            ):
                _clear_bulk_preview()
                st.rerun()

        if preview is not None and int(preview.get("days", -1)) == int(days):
            count = int(preview.get("count", 0))
            total_bytes = int(preview.get("total_bytes", 0))
            if count == 0:
                st.info(
                    f"{int(days)} 日より前の削除対象となる元動画・中間ファイルはありません。"
                )
            else:
                st.warning(
                    f"{int(days)} 日より前の元動画・中間ファイルが {count} 件、"
                    f"合計 {format_bytes(total_bytes)} あります。"
                    "チャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
                )
                if st.button(
                    f"{count} 件を削除する",
                    key="history_bulk_purge_exec",
                    type="primary",
                    disabled=busy,
                ):
                    try:
                        results = purge_sources_older_than(int(days), settings)
                    except StorageError as exc:
                        st.error(str(exc))
                    else:
                        deleted_count = len(results)
                        deleted_bytes = sum(size for _, size in results)
                        _set_storage_summary(summarize(settings))
                        _clear_bulk_preview()
                        st.success(
                            f"{deleted_count} 件、合計 {format_bytes(deleted_bytes)} "
                            "を削除しました。"
                        )


def render_history_page() -> None:
    settings = get_settings()
    processed = list_processed_videos(settings)

    if not processed:
        st.info("処理済みの動画がありません。実行タブから URL を処理してください。")
        return

    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)

    purge_success = _get_purge_success_message()
    if purge_success is not None:
        st.success(purge_success)
        _clear_purge_success_message()

    storage_summary = _get_storage_summary()
    bytes_map = (
        source_bytes_map(storage_summary) if storage_summary is not None else {}
    )

    st.caption(f"{len(processed)} 件の処理済み動画")
    st.caption(_REGENERATE_NOTE)

    for video in processed:
        badges = []
        if video.has_chapters:
            badges.append("チャプター")
        if video.has_transcript:
            badges.append("全文")
        if video.has_clips:
            badges.append("候補")
        source_bytes = bytes_map.get(video.video_id, 0)
        if source_bytes > 0:
            badges.append(f"元動画: {format_bytes(source_bytes)}")
        badge_text = " · ".join(badges) if badges else "メタのみ"
        fetched = (
            video.fetched_at.strftime("%Y-%m-%d %H:%M")
            if video.fetched_at
            else "日時不明"
        )

        st.markdown(f"**{video.title}**")
        st.caption(f"`{video.video_id}` — {fetched} — {badge_text}")
        _render_row_actions(
            video,
            busy=busy,
            settings=settings,
        )
        st.divider()

    _render_storage_section(processed, settings, busy=busy)
