"""Streamlit Web UI — エントリポイント."""

from __future__ import annotations

from functools import partial

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.config import get_settings
from yt_live_kit.services.ytdlp import check_ytdlp_version_warning
from yt_live_kit.ui.components.results import render_results
from yt_live_kit.ui.components.shorts_line import render_sidebar_line_context
from yt_live_kit.ui.components.status_bar import render_status_bar
from yt_live_kit.ui.views.intake import render_intake_page
from yt_live_kit.ui.views.library import render_library_page
from yt_live_kit.ui.views.settings import render_settings_page
from yt_live_kit.ui.views.video_detail import render_video_detail_page
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


intake_page = st.Page(
    render_intake_page,
    title="取り込み",
    icon=":material/input:",
    url_path="intake",
)
detail_page = st.Page(
    partial(render_video_detail_page, run_page=intake_page),
    title="動画詳細",
    icon=":material/movie:",
    url_path="video-detail",
    visibility="hidden",
)
library_page = st.Page(
    partial(render_library_page, detail_page=detail_page),
    title="ライブラリ",
    icon=":material/video_library:",
    url_path="library",
    default=True,
)
settings_page = st.Page(
    render_settings_page,
    title="設定",
    icon=":material/settings:",
    url_path="settings",
)
page = st.navigation(
    [
        library_page,
        intake_page,
        settings_page,
        detail_page,
    ]
)
with st.sidebar:
    render_sidebar_line_context(get_selected_video_id(), get_settings())
page.run()

result = get_result()
if result is not None:
    st.divider()
    render_results(result)
