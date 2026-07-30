"""Streamlit Web UI — エントリポイント."""

from __future__ import annotations

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.services.ytdlp import check_ytdlp_version_warning
from yt_live_kit.ui.components.results import render_results
from yt_live_kit.ui.components.status_bar import render_status_bar
from yt_live_kit.ui.pages.history import render_history_page
from yt_live_kit.ui.pages.run import render_run_page
from yt_live_kit.ui.state import (
    get_interrupted_notices,
    get_result,
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

st.title("yt-live-kit")
st.caption(f"v{__version__} — YouTube ライブアーカイブのタイムライン生成")

ytdlp_warning = check_ytdlp_version_warning()
if ytdlp_warning:
    st.warning(ytdlp_warning)

tab_run, tab_history = st.tabs(["実行", "処理済み一覧"])

with tab_run:
    render_run_page()

with tab_history:
    render_history_page()

result = get_result()
if result is not None:
    st.divider()
    render_results(result)
