"""実行タブ — 単本・一括の非同期実行."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from yt_live_kit.services.batch import parse_urls, run_batch_job_target
from yt_live_kit.services.jobs import JobBusyError, get_active_job, is_busy, start_job, update_job
from yt_live_kit.services.pipeline import run
from yt_live_kit.ui.state import (
    clear_cut_result,
    clear_result,
    set_active_job_id,
)

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"


def _pipeline_progress_adapter(report) -> Callable[[str, str], None]:
    def on_progress(stage: str, message: str) -> None:
        report(stage=stage, message=message)

    return on_progress


def single_job_target(*, report, settings, url: str) -> None:
    """start_job 用: 単本 URL を pipeline.run で処理する."""
    result = run(url.strip(), settings, on_progress=_pipeline_progress_adapter(report))
    active = get_active_job(settings)
    if active is not None:
        update_job(
            active.job_id,
            settings=settings,
            video_id=result.video_id,
            title=result.title,
        )


def render_run_page() -> None:
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
            try:
                job_id = start_job(
                    "single",
                    single_job_target,
                    url=url.strip(),
                )
                set_active_job_id(job_id)
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
            try:
                job_id = start_job(
                    "batch",
                    run_batch_job_target,
                    total=len(urls),
                    urls=urls,
                    skip_existing=skip_existing,
                )
                set_active_job_id(job_id)
            except JobBusyError:
                st.error(_BUSY_MESSAGE)
