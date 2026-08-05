"""Streamlit Web UI — エントリポイント."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import partial

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.config import get_settings
from yt_live_kit.ui.components.shorts_line import render_sidebar_line_context
from yt_live_kit.ui.components.status_bar import (
    job_display_label,
    render_pipeline_completion_notices,
    render_status_bar,
    retry_hint_for_job,
)
from yt_live_kit.ui.runtime_checks import check_ytdlp_version_warning_cached
from yt_live_kit.services.jobs import read_job, read_job_error_log
from yt_live_kit.ui.state import (
    consume_unread_job_error_notifications,
    format_job_error_summary_for_display,
    get_selected_video_id,
    get_unread_job_error_notifications,
    init_orphans_once,
    record_job_error,
    set_selected_video_id,
)
from yt_live_kit.ui.views.intake import render_intake_page
from yt_live_kit.ui.views.library import render_library_page
from yt_live_kit.ui.views.settings import render_settings_page
from yt_live_kit.ui.views.video_detail import render_video_detail_page

st.set_page_config(page_title="yt-live-kit", page_icon="📺", layout="wide")

_TOP_SUMMARY_MAX_CHARS = 180


def _top_summary(value: object) -> str:
    summary = format_job_error_summary_for_display(value).strip()
    if not summary:
        summary = "処理に失敗しました。詳細画面で確認して再試行してください。"
    if len(summary) > _TOP_SUMMARY_MAX_CHARS:
        summary = summary[: _TOP_SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary


def _record_interrupted_jobs(job_ids: list[str], settings) -> None:
    """起動時に中断されたjobを旧warningではなく構造化通知へ移す。"""
    for job_id in job_ids:
        job = read_job(job_id, settings)
        if job is None:
            record_job_error(
                None,
                job_id,
                "interrupted",
                _top_summary(
                    f"中断された処理を確認できません。job ID {job_id}。"
                    "ジョブ履歴を確認してください。"
                ),
                None,
                datetime.now(timezone.utc),
            )
            continue

        video_id = job.video_id
        target = f"対象動画 {video_id}。" if video_id else ""
        log = read_job_error_log(job.job_id, settings)
        detail = log or job.error or "前回の処理が中断されました。"
        occurred_at = job.finished_at or datetime.now(timezone.utc)
        label = job_display_label(
            job.kind,
            stage=job.stage,
            message=job.message,
            error=job.error,
            detail=detail,
        )
        retry_hint = retry_hint_for_job(
            job.kind,
            stage=job.stage,
            message=job.message,
            error=job.error,
            detail=detail,
        )
        record_job_error(
            video_id,
            job.job_id,
            job.kind,
            _top_summary(
                f"{label}に失敗しました。{target}"
                "処理が中断されました。"
                f"{retry_hint}"
            ),
            (
                f"詳細元: {'ジョブログ' if log else 'ジョブ状態'}\n{detail}"
                if video_id
                else None
            ),
            occurred_at,
        )


def _render_unread_job_errors(detail_page) -> None:
    """上部はread-only描画し、クリック時だけunreadをconsumeする。"""
    for index, notice in enumerate(get_unread_job_error_notifications()):
        st.error(_top_summary(notice.summary))
        if notice.video_id:
            if st.button(
                "対象動画を開く",
                key=f"job-error-detail-{index}-{notice.job_id or 'unknown'}",
            ):
                set_selected_video_id(notice.video_id)
                consume_unread_job_error_notifications()
                st.switch_page(detail_page)
        if st.button(
            "通知を閉じる",
            key=f"job-error-dismiss-{index}-{notice.job_id or 'unknown'}",
        ):
            consume_unread_job_error_notifications()
            st.rerun(scope="app")


def _render_sidebar_brand() -> None:
    """Keep the product identity visible without repeating every page heading."""
    st.markdown("### yt-live-kit")
    st.caption(f"v{__version__}")


app_settings = get_settings()


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

interrupted_job_ids = init_orphans_once()
_record_interrupted_jobs(interrupted_job_ids, app_settings)

_render_unread_job_errors(detail_page)
render_pipeline_completion_notices(detail_page=detail_page)

ytdlp_warning = check_ytdlp_version_warning_cached(app_settings)
if ytdlp_warning:
    st.warning(ytdlp_warning)

with st.sidebar:
    _render_sidebar_brand()
    render_status_bar(detail_page=detail_page)
    render_sidebar_line_context(get_selected_video_id(), app_settings)
page.run()
