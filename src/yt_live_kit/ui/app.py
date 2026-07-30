"""Streamlit Web UI（MVP 雛形）."""

import streamlit as st

from yt_live_kit import __version__

st.set_page_config(page_title="yt-live-kit", page_icon="📺", layout="wide")

st.title("yt-live-kit")
st.caption(f"v{__version__} — YouTube ライブアーカイブ処理ツール")

st.info(
    "YouTube アーカイブ URL を貼り付けて「実行」を押すと、"
    "タイムライン（チャプター）と切り抜き候補を生成します。"
)
