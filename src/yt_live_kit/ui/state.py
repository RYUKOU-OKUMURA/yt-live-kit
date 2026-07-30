"""Streamlit session_state のキー定数と get/set ヘルパー."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.services.ffmpeg import CutResult
from yt_live_kit.services.pipeline import PipelineResult

SESSION_RESULT = "pipeline_result"
SESSION_CUT_RESULT = "cut_result"
SESSION_ACTIVE_JOB = "active_job_id"
SESSION_LAST_JOB = "last_job_id"
SESSION_HANDLED_JOBS = "handled_job_ids"
SESSION_INTERRUPTED_NOTICES = "interrupted_notices"
SESSION_INTERRUPTED_SHOWN = "interrupted_notices_shown"
SESSION_BATCH_SUMMARY = "batch_summary"
SESSION_JOB_ERROR = "job_error"

_orphans_initialized = False


def get_result() -> PipelineResult | None:
    return st.session_state.get(SESSION_RESULT)


def set_result(result: PipelineResult | None) -> None:
    st.session_state[SESSION_RESULT] = result


def clear_result() -> None:
    st.session_state[SESSION_RESULT] = None


def get_cut_result() -> CutResult | None:
    return st.session_state.get(SESSION_CUT_RESULT)


def set_cut_result(result: CutResult | None) -> None:
    st.session_state[SESSION_CUT_RESULT] = result


def clear_cut_result() -> None:
    st.session_state[SESSION_CUT_RESULT] = None


def get_active_job_id() -> str | None:
    return st.session_state.get(SESSION_ACTIVE_JOB)


def set_active_job_id(job_id: str | None) -> None:
    st.session_state[SESSION_ACTIVE_JOB] = job_id


def clear_active_job_id() -> None:
    st.session_state[SESSION_ACTIVE_JOB] = None


def get_last_job_id() -> str | None:
    return st.session_state.get(SESSION_LAST_JOB)


def set_last_job_id(job_id: str | None) -> None:
    st.session_state[SESSION_LAST_JOB] = job_id


def _handled_job_ids() -> set[str]:
    raw = st.session_state.get(SESSION_HANDLED_JOBS)
    if isinstance(raw, set):
        return raw
    return set()


def is_job_handled(job_id: str) -> bool:
    return job_id in _handled_job_ids()


def mark_job_handled(job_id: str) -> None:
    handled = _handled_job_ids()
    handled.add(job_id)
    st.session_state[SESSION_HANDLED_JOBS] = handled


def get_interrupted_notices() -> list[dict[str, str]]:
    raw = st.session_state.get(SESSION_INTERRUPTED_NOTICES)
    if isinstance(raw, list):
        return raw
    return []


def set_interrupted_notices(notices: list[dict[str, str]]) -> None:
    st.session_state[SESSION_INTERRUPTED_NOTICES] = notices


def interrupted_notices_shown() -> bool:
    return bool(st.session_state.get(SESSION_INTERRUPTED_SHOWN))


def mark_interrupted_notices_shown() -> None:
    st.session_state[SESSION_INTERRUPTED_SHOWN] = True


def get_batch_summary() -> dict[str, object] | None:
    raw = st.session_state.get(SESSION_BATCH_SUMMARY)
    if isinstance(raw, dict):
        return raw
    return None


def set_batch_summary(summary: dict[str, object] | None) -> None:
    st.session_state[SESSION_BATCH_SUMMARY] = summary


def clear_batch_summary() -> None:
    st.session_state[SESSION_BATCH_SUMMARY] = None


def get_job_error() -> str | None:
    return st.session_state.get(SESSION_JOB_ERROR)


def set_job_error(message: str | None) -> None:
    st.session_state[SESSION_JOB_ERROR] = message


def clear_job_error() -> None:
    st.session_state[SESSION_JOB_ERROR] = None


def init_orphans_once() -> list[str]:
    """プロセス起動時に 1 回だけ孤児ジョブをクローズする."""
    global _orphans_initialized
    if _orphans_initialized:
        return []
    _orphans_initialized = True

    from yt_live_kit.services.jobs import close_orphans, read_job
    from yt_live_kit.config import get_settings

    settings = get_settings()
    interrupted_ids = close_orphans(settings)
    if not interrupted_ids:
        return interrupted_ids

    notices: list[dict[str, str]] = []
    for job_id in interrupted_ids:
        job = read_job(job_id, settings)
        title = "不明"
        if job is not None:
            title = job.title or job.video_id or "不明"
        notices.append({"job_id": job_id, "title": title})
    set_interrupted_notices(notices)
    return interrupted_ids
