"""常駐ステータスバー（fragment）."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from yt_live_kit.config import get_settings
from yt_live_kit.services.batch import read_batch_summary
from yt_live_kit.services.jobs import JobState, get_active_job, list_jobs, read_job
from yt_live_kit.services.pipeline import load_result_from_disk
from yt_live_kit.ui.state import (
    clear_active_job_id,
    clear_cut_result,
    clear_job_error,
    get_active_job_id,
    get_last_job_id,
    is_job_handled,
    mark_job_handled,
    set_active_job_id,
    set_batch_summary,
    set_job_error,
    set_last_job_id,
    set_result,
)

_KIND_LABELS: dict[str, str] = {
    "single": "単本処理",
    "batch": "一括処理",
    "regenerate": "再生成",
    "highlights": "ハイライト動画",
    "shorts": "ショート動画",
}

_RESULT_LOAD_ERROR = (
    "成果物を読み込めませんでした。処理済み一覧から開き直してください。"
)


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def elapsed_seconds(job: JobState, *, now: datetime | None = None) -> int:
    reference = now or datetime.now(timezone.utc)
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((reference - started).total_seconds()))


def format_status_message(job: JobState, *, now: datetime | None = None) -> str:
    label = kind_label(job.kind)
    elapsed = elapsed_seconds(job, now=now)
    parts = [f"{label} — {job.message}（経過 {elapsed} 秒）"]
    if job.total > 0:
        parts.append(f"{job.current}/{job.total}")
    return " ".join(parts)


def should_show_running_bar(job: JobState | None) -> bool:
    return job is not None and job.status == "running"


def find_restorable_job(settings) -> JobState | None:
    """セッションまたはディスク上の未処理完了ジョブを探す."""
    last_id = get_last_job_id()
    if last_id:
        job = read_job(last_id, settings)
        if (
            job is not None
            and job.status in ("done", "failed", "interrupted")
            and not is_job_handled(job.job_id)
        ):
            return job

    for job in list_jobs(settings):
        if job.status in ("done", "failed", "interrupted") and not is_job_handled(job.job_id):
            return job
    return None


def _load_batch_summary_for_job(job: JobState, settings) -> None:
    if job.kind != "batch":
        return
    summary = read_batch_summary(settings, job.job_id)
    if summary is not None:
        set_batch_summary(summary)


def _render_running_job(job: JobState) -> None:
    message = format_status_message(job)
    if job.total > 0:
        ratio = min(max(job.current / job.total, 0.0), 1.0)
        st.progress(ratio, text=message)
    else:
        st.progress(0, text=message)


def _handle_finished_job(job: JobState) -> None:
    if is_job_handled(job.job_id):
        return

    mark_job_handled(job.job_id)
    settings = get_settings()

    if job.status == "done":
        if job.result_ref:
            result = load_result_from_disk(job.result_ref, settings)
            if result is not None:
                set_result(result)
                clear_cut_result()
            else:
                set_job_error(_RESULT_LOAD_ERROR)

        _load_batch_summary_for_job(job, settings)
        clear_active_job_id()
        st.rerun(scope="app")
        return

    if job.status in ("failed", "interrupted"):
        _load_batch_summary_for_job(job, settings)
        error_message = job.error or "処理に失敗しました。"
        set_job_error(error_message)
        clear_active_job_id()
        st.rerun(scope="app")


@st.fragment(run_every="1s")
def render_status_bar() -> None:
    settings = get_settings()
    active = get_active_job(settings)

    if active is not None:
        clear_job_error()
        set_active_job_id(active.job_id)
        set_last_job_id(active.job_id)
        _render_running_job(active)
        return

    tracked_id = get_active_job_id()
    if tracked_id:
        job = read_job(tracked_id, settings)
        if job is None:
            clear_active_job_id()
            return

        if job.status == "running":
            clear_job_error()
            set_last_job_id(job.job_id)
            _render_running_job(job)
            return

        _handle_finished_job(job)
        return

    restorable = find_restorable_job(settings)
    if restorable is not None:
        set_last_job_id(restorable.job_id)
        _handle_finished_job(restorable)
