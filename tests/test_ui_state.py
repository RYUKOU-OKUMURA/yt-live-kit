"""ui/state.py のセッション状態ヘルパーのテスト."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.ui.state import (
    SESSION_JOB_ERROR,
    clear_job_error,
    get_job_error,
    set_job_error,
)


def test_job_error_set_get_clear_roundtrip() -> None:
    # 他テストからの汚染を避けるため、まずクリアしておく。
    clear_job_error()
    assert get_job_error() is None

    set_job_error("字幕が取得できませんでした。ネットワーク接続を確認してください。")
    assert (
        get_job_error()
        == "字幕が取得できませんでした。ネットワーク接続を確認してください。"
    )
    assert st.session_state[SESSION_JOB_ERROR] == (
        "字幕が取得できませんでした。ネットワーク接続を確認してください。"
    )

    clear_job_error()
    assert get_job_error() is None
