"""実行タブ — 単本・一括の非同期実行."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.services.batch import parse_urls, run_batch_job_target
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import run_single_job_target
from yt_live_kit.ui.state import (
    clear_batch_summary,
    clear_cut_result,
    clear_result,
    get_batch_summary,
    set_active_job_id,
)

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"


def batch_summary_severity(success: int, failed: int) -> str:
    """一括処理サマリーの表示レベルを決める.

    全件スキップ（成功 0・失敗 0）は「処理済みをスキップ」の正常動作であり、
    エラーではないため info とする。
    """
    if failed > 0 and success > 0:
        return "warning"
    if failed > 0:
        return "error"
    if success == 0:
        return "info"
    return "success"


def _render_batch_summary() -> None:
    summary = get_batch_summary()
    if not summary:
        return

    text = str(summary.get("summary", ""))
    lines = summary.get("lines", [])
    failed = int(summary.get("failed", 0) or 0)
    success = int(summary.get("success", 0) or 0)

    severity = batch_summary_severity(success, failed)
    if severity == "warning":
        st.warning(text)
    elif severity == "error":
        st.error(text)
    elif severity == "info":
        st.info(text)
    else:
        st.success(text)

    if isinstance(lines, list) and lines:
        with st.expander("URL 別ログ", expanded=False):
            st.code("\n".join(str(line) for line in lines), language=None)


def render_run_page() -> None:
    _render_batch_summary()

    st.markdown(
        "YouTube **公開アーカイブ** の URL を貼り付けて「実行」を押すと、"
        "概要欄用のタイムライン（チャプター）、文字起こし全文、切り抜き候補が生成されます。"
    )

    busy = is_busy()

    mode = st.radio(
        "実行モード",
        ["単本", "一括"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if busy:
        st.info(_BUSY_MESSAGE)

    if mode == "単本":
        url = st.text_input(
            "YouTube URL",
            placeholder="https://www.youtube.com/watch?v=...",
            help="公開アーカイブのみ対応しています。",
        )
        run_clicked = st.button(
            "実行",
            type="primary",
            disabled=busy or not url.strip(),
        )

        if run_clicked and url.strip() and not busy:
            clear_result()
            clear_cut_result()
            clear_batch_summary()
            try:
                job_id = start_job(
                    "single",
                    run_single_job_target,
                    url=url.strip(),
                )
                set_active_job_id(job_id)
                st.rerun()
            except JobBusyError:
                st.error(_BUSY_MESSAGE)
    else:
        batch_urls = st.text_area(
            "YouTube URL（1 行 1 本）",
            placeholder="https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/watch?v=...",
            height=150,
            help="複数 URL を改行区切りで貼り付けます。# で始まる行はコメントとして無視されます。",
        )
        skip_existing = st.checkbox(
            "処理済みをスキップ",
            value=True,
            help="チャプターが既にある動画 ID はスキップします。",
        )
        urls = parse_urls(batch_urls)
        batch_clicked = st.button(
            "一括実行",
            type="primary",
            disabled=busy or len(urls) == 0,
        )
        if batch_clicked and urls and not busy:
            clear_result()
            clear_cut_result()
            clear_batch_summary()
            try:
                job_id = start_job(
                    "batch",
                    run_batch_job_target,
                    total=len(urls),
                    urls=urls,
                    skip_existing=skip_existing,
                )
                set_active_job_id(job_id)
                st.rerun()
            except JobBusyError:
                st.error(_BUSY_MESSAGE)
