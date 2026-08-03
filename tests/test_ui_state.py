"""ui/state.py のセッション状態ヘルパーのテスト."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st

from yt_live_kit.ui.state import (
    JOB_ERROR_HISTORY_LIMIT,
    MAX_GLOBAL_JOB_ERROR_NOTIFICATIONS,
    MAX_UNREAD_JOB_ERROR_NOTIFICATIONS,
    MAX_VIDEO_JOB_ERROR_NOTIFICATIONS,
    SESSION_GLOBAL_JOB_ERRORS,
    SESSION_JOB_ERROR,
    SESSION_JOB_ERROR_HISTORY,
    SESSION_UNREAD_JOB_ERRORS,
    JobErrorNotification,
    clear_job_error,
    consume_unread_job_error_notifications,
    format_job_error_summary_for_display,
    get_global_job_error_notifications,
    get_job_error,
    get_job_error_history,
    get_job_error_notifications,
    get_unread_job_error_notifications,
    record_job_error,
    sanitize_job_error_for_display,
    set_job_error,
)


def _clear_structured_errors() -> None:
    for key in (
        SESSION_JOB_ERROR_HISTORY,
        SESSION_GLOBAL_JOB_ERRORS,
        SESSION_UNREAD_JOB_ERRORS,
    ):
        st.session_state.pop(key, None)


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


def test_structured_job_error_roundtrip_keeps_raw_detail_and_is_read_only() -> None:
    _clear_structured_errors()
    occurred_at = datetime(2026, 8, 3, 1, 2, tzinfo=timezone.utc)

    stored = record_job_error(
        "video-a",
        "job-a",
        "ffmpeg",
        "ffmpeg に失敗しました <summary>",
        "Traceback <raw>\ncommand failed",
        occurred_at,
    )

    assert isinstance(stored, JobErrorNotification)
    before = dict(st.session_state)
    history = get_job_error_history("video-a")
    assert history == [stored]
    assert history[0].video_id == "video-a"
    assert history[0].job_id == "job-a"
    assert history[0].kind == "ffmpeg"
    assert history[0].summary == "ffmpeg に失敗しました <summary>"
    assert history[0].detail == "Traceback <raw>\ncommand failed"
    assert history[0].occurred_at == occurred_at
    assert dict(st.session_state) == before
    assert sanitize_job_error_for_display(history[0].detail) == (
        "Traceback 〈raw〉\ncommand failed"
    )
    assert format_job_error_summary_for_display(history[0].summary) == (
        "ffmpeg に失敗しました 〈summary〉"
    )
    _clear_structured_errors()


def test_structured_job_errors_keep_latest_three_per_video() -> None:
    _clear_structured_errors()
    base = datetime(2026, 8, 3, tzinfo=timezone.utc)

    for index in range(MAX_VIDEO_JOB_ERROR_NOTIFICATIONS + 1):
        record_job_error(
            "video-a",
            f"job-{index}",
            "kind",
            f"summary-{index}",
            f"detail-{index}",
            base + timedelta(minutes=index),
        )

    history = get_job_error_history("video-a")
    assert JOB_ERROR_HISTORY_LIMIT == 3
    assert [notice.job_id for notice in history] == ["job-3", "job-2", "job-1"]
    assert len(history) == MAX_VIDEO_JOB_ERROR_NOTIFICATIONS
    _clear_structured_errors()


def test_structured_job_errors_separate_video_a_and_b() -> None:
    _clear_structured_errors()
    record_job_error("video-a", "job-a", "kind", "A", "detail A")
    record_job_error("video-b", "job-b", "kind", "B", "detail B")

    assert [notice.job_id for notice in get_job_error_notifications("video-a")] == [
        "job-a"
    ]
    assert [notice.job_id for notice in get_job_error_history("video-b")] == ["job-b"]
    assert get_job_error_history("video-c") == []
    _clear_structured_errors()


def test_global_job_errors_keep_only_summaries_and_latest_three() -> None:
    _clear_structured_errors()
    for index in range(MAX_GLOBAL_JOB_ERROR_NOTIFICATIONS + 1):
        record_job_error(
            None,
            f"global-job-{index}",
            "batch",
            f"global-{index}",
            f"technical detail {index}",
        )

    global_errors = get_global_job_error_notifications()
    assert [notice.job_id for notice in global_errors] == [
        "global-job-3",
        "global-job-2",
        "global-job-1",
    ]
    assert len(global_errors) == MAX_GLOBAL_JOB_ERROR_NOTIFICATIONS
    assert all(notice.video_id is None and notice.detail is None for notice in global_errors)
    assert get_job_error_history(None) == []
    _clear_structured_errors()


def test_structured_job_errors_deduplicate_same_job() -> None:
    _clear_structured_errors()
    first = record_job_error(
        "video-a", "same-job", "kind", "first summary", "first detail"
    )
    second = record_job_error(
        "video-a", "same-job", "kind", "second summary", "second detail"
    )

    assert second == first
    assert get_job_error_history("video-a") == [first]
    assert len(get_unread_job_error_notifications()) == 1
    _clear_structured_errors()


def test_unread_consume_is_separate_from_video_history() -> None:
    _clear_structured_errors()
    record_job_error("video-a", "video-job", "kind", "video summary", "detail")
    record_job_error(None, "global-job", "kind", "global summary", "global detail")

    unread = get_unread_job_error_notifications()
    assert len(unread) == MAX_UNREAD_JOB_ERROR_NOTIFICATIONS - 1
    assert all(notice.detail is None for notice in unread)
    consumed = consume_unread_job_error_notifications()
    assert consumed == unread
    assert get_unread_job_error_notifications() == []
    assert get_job_error_history("video-a")[0].detail == "detail"
    assert get_global_job_error_notifications()[0].detail is None
    _clear_structured_errors()
