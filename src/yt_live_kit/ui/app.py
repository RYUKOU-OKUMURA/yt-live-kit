"""Streamlit Web UI — エントリポイント."""

from __future__ import annotations

from functools import partial

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.services.ytdlp import check_ytdlp_version_warning
from yt_live_kit.ui.components.results import render_results
from yt_live_kit.ui.components.status_bar import render_status_bar
from yt_live_kit.ui.views.channel import render_channel_page
from yt_live_kit.ui.views.history import render_history_page
from yt_live_kit.ui.views.library import render_library_page
from yt_live_kit.ui.views.run import render_run_page
from yt_live_kit.ui.state import (
    clear_job_error,
    get_interrupted_notices,
    get_job_error,
    get_result,
    get_selected_video_id,
    init_orphans_once,
    interrupted_notices_shown,
    mark_interrupted_notices_shown,
)

st.set_page_config(page_title="yt-live-kit", page_icon="📺", layout="wide")

init_orphans_once()

if not interrupted_notices_shown():
    notices = get_interrupted_notices()
    if notices:
        for notice in notices:
            st.warning(
                f"前回の処理が中断されています（{notice['title']}）。"
                "必要なら再実行してください。"
            )
        mark_interrupted_notices_shown()

render_status_bar()

job_error = get_job_error()
if job_error:
    st.error(job_error)
    clear_job_error()

st.title("yt-live-kit")
st.caption(f"v{__version__} — YouTube ライブアーカイブのタイムライン生成")

ytdlp_warning = check_ytdlp_version_warning()
if ytdlp_warning:
    st.warning(ytdlp_warning)


def _render_temporary_video_detail() -> None:
    """U2 実装まで選択内容を確認できる一時詳細ページ."""
    st.header("動画詳細")
    video_id = get_selected_video_id()
    if video_id is None:
        st.info("ライブラリから動画を選択してください。")
        return
    st.caption("選択中の動画 ID")
    st.code(video_id, language=None)
    st.info("詳細機能は次タスクで追加します。")


detail_page = st.Page(
    _render_temporary_video_detail,
    title="動画詳細",
    icon=":material/movie:",
    visibility="hidden",
)
library_page = st.Page(
    partial(render_library_page, detail_page=detail_page),
    title="ライブラリ",
    icon=":material/video_library:",
    default=True,
)
page = st.navigation(
    [
        library_page,
        st.Page(
            render_run_page,
            title="実行",
            icon=":material/play_arrow:",
        ),
        st.Page(
            render_channel_page,
            title="チャンネル",
            icon=":material/video_library:",
        ),
        st.Page(
            render_history_page,
            title="処理済み一覧",
            icon=":material/history:",
        ),
        detail_page,
    ]
)
page.run()

result = get_result()
if result is not None:
    st.divider()
    render_results(result)
