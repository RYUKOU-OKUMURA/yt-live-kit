#!/bin/bash
# yt-live-kit 起動スクリプト（macOS ダブルクリック用）
cd "$(dirname "$0")" || exit 1
uv run streamlit run src/yt_live_kit/ui/app.py
