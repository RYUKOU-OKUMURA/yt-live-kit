"""処理済み一覧タブ."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.config import get_settings
from yt_live_kit.services.history import list_processed_videos
from yt_live_kit.services.pipeline import load_result_from_disk
from yt_live_kit.ui.state import clear_cut_result, set_result


def render_history_page() -> None:
    settings = get_settings()
    processed = list_processed_videos(settings)

    if not processed:
        st.info("処理済みの動画がありません。実行タブから URL を処理してください。")
        return

    st.caption(f"{len(processed)} 件の処理済み動画")
    for video in processed:
        badges = []
        if video.has_chapters:
            badges.append("チャプター")
        if video.has_transcript:
            badges.append("全文")
        if video.has_clips:
            badges.append("候補")
        badge_text = " · ".join(badges) if badges else "メタのみ"
        fetched = (
            video.fetched_at.strftime("%Y-%m-%d %H:%M")
            if video.fetched_at
            else "日時不明"
        )

        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"**{video.title}**")
            st.caption(f"`{video.video_id}` — {fetched} — {badge_text}")
        with col2:
            if st.button("開く", key=f"open_{video.video_id}"):
                loaded = load_result_from_disk(video.video_id, settings)
                if loaded is None:
                    st.error("成果物を読み込めませんでした。")
                else:
                    set_result(loaded)
                    clear_cut_result()
                    st.rerun()
