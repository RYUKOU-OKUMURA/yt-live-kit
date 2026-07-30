"""チャンネルタブ — 配信アーカイブ一覧と一括投入."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from yt_live_kit.config import get_settings
from yt_live_kit.models.channel import ChannelListDocument, ChannelVideo
from yt_live_kit.services.batch import run_batch_job_target
from yt_live_kit.services.channel import (
    ChannelError,
    list_archives,
    load_cache,
    mark_processed,
    normalize_channel_url,
    save_cache,
)
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.ui.state import set_active_job_id

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_UNEXPECTED_ERROR = "予期しないエラーが発生しました。"
_LIMIT_OPTIONS = [20, 50, 100]
_CHECKBOX_KEY_PREFIX = "channel_cb_"


def format_duration(seconds: int | None) -> str:
    """秒数を H:MM:SS 形式に変換する."""
    if seconds is None:
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_upload_date(upload_date: str | None) -> str:
    """YYYYMMDD を YYYY-MM-DD に変換する."""
    if not upload_date or len(upload_date) != 8:
        return "—"
    return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"


def format_fetched_at(fetched_at: datetime) -> str:
    """取得日時を YYYY-MM-DD HH:MM 形式に変換する."""
    if fetched_at.tzinfo is not None:
        fetched_at = fetched_at.astimezone()
    return fetched_at.strftime("%Y-%m-%d %H:%M")


def filter_display_items(
    items: list[tuple[ChannelVideo, bool]],
    *,
    unprocessed_only: bool,
) -> list[tuple[ChannelVideo, bool]]:
    """未処理のみ表示トグルに応じて一覧行を絞り込む."""
    if not unprocessed_only:
        return items
    return [(video, processed) for video, processed in items if not processed]


def checkbox_key(video_id: str) -> str:
    """動画 ID から Streamlit チェックボックス key を生成する."""
    return f"{_CHECKBOX_KEY_PREFIX}{video_id}"


def count_selected(
    items: list[tuple[ChannelVideo, bool]],
    session_state: dict[str, object],
) -> int:
    """表示中の行のうち、チェックされている件数を返す."""
    count = 0
    for video, _ in items:
        if session_state.get(checkbox_key(video.video_id), False):
            count += 1
    return count


def collect_selected_urls(
    items: list[tuple[ChannelVideo, bool]],
    session_state: dict[str, object],
) -> list[str]:
    """選択された動画の URL 一覧を返す."""
    urls: list[str] = []
    for video, _ in items:
        if session_state.get(checkbox_key(video.video_id), False):
            urls.append(video.url)
    return urls


def _set_all_checkboxes(
    items: list[tuple[ChannelVideo, bool]],
    *,
    selected: bool,
) -> None:
    for video, _ in items:
        st.session_state[checkbox_key(video.video_id)] = selected


def _fetch_archives(
    channel_url: str,
    *,
    limit: int,
) -> ChannelListDocument:
    """yt-dlp で一覧を取得しキャッシュに保存する.

    NFR-05（レート制限対策）のため、list_archives はユーザーが
    「再取得」を押したときだけ呼ぶ。通常表示は load_cache の結果を使う。
    """
    settings = get_settings()
    with st.spinner("配信アーカイブ一覧を取得しています..."):
        doc = list_archives(channel_url, limit=limit, settings=settings)
        save_cache(doc, settings=settings)
    return doc


def render_channel_page() -> None:
    st.markdown(
        "YouTube チャンネルの **公開配信アーカイブ** 一覧を表示し、"
        "未処理の動画を選択して一括処理に投入できます。"
    )

    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)

    channel_input = st.text_input(
        "チャンネル URL / ハンドル",
        placeholder="@handle",
        help="@handle、ハンドル名、または youtube.com/@handle の URL を入力できます。",
    )

    limit = st.selectbox(
        "取得件数の上限",
        options=_LIMIT_OPTIONS,
        index=_LIMIT_OPTIONS.index(50),
        format_func=lambda value: f"{value} 件",
    )

    raw = channel_input.strip()
    if not raw:
        return

    try:
        channel_url, handle = normalize_channel_url(raw)
    except ChannelError as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error(_UNEXPECTED_ERROR)
        return

    settings = get_settings()
    doc = load_cache(handle, settings=settings)

    header_col, action_col = st.columns([3, 1])
    with header_col:
        if doc is not None:
            st.caption(f"前回取得: {format_fetched_at(doc.fetched_at)}")
    with action_col:
        refresh_clicked = st.button("再取得", use_container_width=True)

    if refresh_clicked:
        try:
            doc = _fetch_archives(channel_url, limit=limit)
        except ChannelError as exc:
            st.error(str(exc))
        except Exception:
            st.error(_UNEXPECTED_ERROR)

    if doc is None:
        st.info("キャッシュがありません。「再取得」で一覧を取得してください。")
        return

    items = mark_processed(doc, settings=settings)
    unprocessed_only = st.toggle("未処理のみ表示", value=True)

    display_items = filter_display_items(items, unprocessed_only=unprocessed_only)
    if not display_items:
        st.info("表示する動画がありません。")
        return

    select_col1, select_col2, count_col = st.columns([1, 1, 2])
    with select_col1:
        if st.button("すべて選択"):
            _set_all_checkboxes(display_items, selected=True)
            st.rerun()
    with select_col2:
        if st.button("選択を解除"):
            _set_all_checkboxes(display_items, selected=False)
            st.rerun()
    with count_col:
        selected_count = count_selected(display_items, st.session_state)
        st.caption(f"選択: {selected_count} 件")

    for video, processed in display_items:
        col_check, col_title, col_duration, col_date, col_badge = st.columns(
            [0.5, 4, 1, 1, 1]
        )
        with col_check:
            st.checkbox(
                "選択",
                key=checkbox_key(video.video_id),
                label_visibility="collapsed",
            )
        with col_title:
            st.markdown(f"**{video.title}**")
        with col_duration:
            st.caption(format_duration(video.duration))
        with col_date:
            st.caption(format_upload_date(video.upload_date))
        with col_badge:
            if processed:
                st.caption("処理済み")

    selected_urls = collect_selected_urls(display_items, st.session_state)
    batch_label = f"選択した {len(selected_urls)} 本を処理"
    if st.button(
        batch_label,
        type="primary",
        disabled=busy or len(selected_urls) == 0,
    ):
        if busy:
            st.error(_BUSY_MESSAGE)
            return
        try:
            job_id = start_job(
                "batch",
                run_batch_job_target,
                total=len(selected_urls),
                urls=selected_urls,
                skip_existing=True,
                settings=settings,
            )
            set_active_job_id(job_id)
            st.rerun()
        except JobBusyError:
            st.error(_BUSY_MESSAGE)
