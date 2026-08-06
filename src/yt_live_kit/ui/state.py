"""Streamlit session_state のキー定数と get/set ヘルパー."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import streamlit as st

SESSION_RESULT = "pipeline_result"
SESSION_ACTIVE_JOB = "active_job_id"
SESSION_LAST_JOB = "last_job_id"
SESSION_HANDLED_JOBS = "handled_job_ids"
SESSION_BATCH_SUMMARY = "batch_summary"
SESSION_JOB_ERROR = "job_error"
SESSION_JOB_ERROR_HISTORY = "job_error_history"
SESSION_GLOBAL_JOB_ERRORS = "global_job_error_notifications"
SESSION_UNREAD_JOB_ERRORS = "unread_job_error_notifications"
SESSION_PIPELINE_COMPLETIONS = "pipeline_completion_notifications"
SESSION_SELECTED_VIDEO_ID = "selected_video_id"
SESSION_SHOW_ARCHIVED = "library_show_archived"

# U8-2 / U8-3 が表示先を分けて使えるよう、履歴・グローバル要約・上部 unread
# を別の session state key で管理する。古い SESSION_JOB_ERROR は互換用に残す。
SESSION_JOB_ERROR_NOTIFICATIONS = SESSION_JOB_ERROR_HISTORY
SESSION_JOB_ERROR_GLOBAL = SESSION_GLOBAL_JOB_ERRORS
SESSION_JOB_ERROR_UNREAD = SESSION_UNREAD_JOB_ERRORS

MAX_VIDEO_JOB_ERROR_NOTIFICATIONS = 3
MAX_GLOBAL_JOB_ERROR_NOTIFICATIONS = 3
MAX_UNREAD_JOB_ERROR_NOTIFICATIONS = 3
MAX_PIPELINE_COMPLETION_NOTIFICATIONS = 3
JOB_ERROR_HISTORY_LIMIT = MAX_VIDEO_JOB_ERROR_NOTIFICATIONS


@dataclass(frozen=True, slots=True)
class JobErrorNotification:
    """UI で扱う構造化ジョブエラー。

    ``detail`` は保存時には変更しない。半角山カッコを含む技術ログを表示する
    ときだけ ``sanitize_job_error_for_display`` を通し、raw の情報を保持する。
    ``occurred_at`` は比較しやすいよう UTC の aware datetime へ正規化する。
    """

    video_id: str | None
    job_id: str | None
    kind: str
    summary: str
    detail: str | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        occurred_at = self.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        else:
            occurred_at = occurred_at.astimezone(timezone.utc)
        object.__setattr__(self, "occurred_at", occurred_at)

    def to_dict(self) -> dict[str, object]:
        """session state 外へ渡す場合の raw payload を返す。"""
        return {
            "video_id": self.video_id,
            "job_id": self.job_id,
            "kind": self.kind,
            "summary": self.summary,
            "detail": self.detail,
            "occurred_at": self.occurred_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobErrorNotification":
        """辞書 payload を検証して復元する。壊れた state は呼び出し側で無視する。"""
        occurred_at = data.get("occurred_at")
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at)
        if not isinstance(occurred_at, datetime):
            raise ValueError("occurred_at の形式が正しくありません")

        video_id = data.get("video_id")
        job_id = data.get("job_id")
        kind = data.get("kind")
        summary = data.get("summary")
        detail = data.get("detail")
        if video_id is not None and not isinstance(video_id, str):
            raise ValueError("video_id の形式が正しくありません")
        if job_id is not None and not isinstance(job_id, str):
            raise ValueError("job_id の形式が正しくありません")
        if not isinstance(kind, str) or not isinstance(summary, str):
            raise ValueError("エラー通知の要約または種別が正しくありません")
        if detail is not None and not isinstance(detail, str):
            raise ValueError("エラー通知の詳細が正しくありません")
        return cls(video_id, job_id, kind, summary, detail, occurred_at)


@dataclass(frozen=True, slots=True)
class PipelineCompletionNotification:
    """本文を持たない、動画詳細への導線専用の完了通知。"""

    video_id: str
    job_id: str
    kind: str
    title: str
    completed_at: datetime

    def __post_init__(self) -> None:
        if not self.video_id or not self.job_id:
            raise ValueError("完了通知には video_id と job_id が必要です")
        completed_at = self.completed_at
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        else:
            completed_at = completed_at.astimezone(timezone.utc)
        object.__setattr__(self, "completed_at", completed_at)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineCompletionNotification":
        completed_at = data.get("completed_at")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)
        if not isinstance(completed_at, datetime):
            raise ValueError("completed_at の形式が正しくありません")

        video_id = data.get("video_id")
        job_id = data.get("job_id")
        kind = data.get("kind")
        title = data.get("title")
        if not all(isinstance(value, str) for value in (video_id, job_id, kind, title)):
            raise ValueError("完了通知の形式が正しくありません")
        return cls(video_id, job_id, kind, title, completed_at)


def sanitize_job_error_for_display(value: object) -> str:
    """ユーザー表示時だけ半角山カッコを全角へ置換する。"""
    return str(value).replace("<", "〈").replace(">", "〉")


def format_job_error_summary_for_display(value: object) -> str:
    """通知の要約を安全な 1 行へ整える。保存 payload は変更しない。"""
    return " ".join(sanitize_job_error_for_display(value).split())


def _coerce_job_error_notification(value: object) -> JobErrorNotification | None:
    if isinstance(value, JobErrorNotification):
        return value
    if isinstance(value, dict):
        try:
            return JobErrorNotification.from_dict(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _read_job_error_list(value: object) -> list[JobErrorNotification]:
    if not isinstance(value, (list, tuple)):
        return []
    notifications: list[JobErrorNotification] = []
    for item in value:
        notification = _coerce_job_error_notification(item)
        if notification is not None:
            notifications.append(notification)
    return notifications


def _read_job_error_history() -> dict[str, list[JobErrorNotification]]:
    raw = st.session_state.get(SESSION_JOB_ERROR_HISTORY)
    if not isinstance(raw, dict):
        return {}
    history: dict[str, list[JobErrorNotification]] = {}
    for video_id, values in raw.items():
        if not isinstance(video_id, str) or not video_id:
            continue
        notifications = _read_job_error_list(values)
        if notifications:
            history[video_id] = notifications
    return history


def _latest_job_errors(
    notifications: list[JobErrorNotification],
    limit: int,
) -> list[JobErrorNotification]:
    return sorted(
        notifications,
        key=lambda notification: notification.occurred_at,
        reverse=True,
    )[:limit]


def _notification_has_same_job(
    existing: JobErrorNotification,
    candidate: JobErrorNotification,
) -> bool:
    if candidate.job_id:
        return existing.job_id == candidate.job_id
    return existing == candidate


def _all_structured_job_errors() -> list[JobErrorNotification]:
    history = _read_job_error_history()
    notifications = [
        notification
        for values in history.values()
        for notification in values
    ]
    notifications.extend(
        _read_job_error_list(st.session_state.get(SESSION_GLOBAL_JOB_ERRORS))
    )
    notifications.extend(
        _read_job_error_list(st.session_state.get(SESSION_UNREAD_JOB_ERRORS))
    )
    return notifications


def _normalise_notification(notification: JobErrorNotification) -> JobErrorNotification:
    video_id = notification.video_id or None
    job_id = notification.job_id or None
    detail = notification.detail if video_id is not None else None
    return JobErrorNotification(
        video_id=video_id,
        job_id=job_id,
        kind=str(notification.kind),
        summary=str(notification.summary),
        detail=detail,
        occurred_at=notification.occurred_at,
    )


def add_job_error_notification(notification: JobErrorNotification) -> JobErrorNotification:
    """構造化通知を履歴とページ上部 unread へ追加する。

    動画通知の詳細履歴と、ページ上部で一度だけ消費する要約通知は別保存する。
    同じ job ID は一度だけ記録し、動画なし通知は detail を保存しない。
    """
    notification = _normalise_notification(notification)
    existing = next(
        (
            item
            for item in _all_structured_job_errors()
            if _notification_has_same_job(item, notification)
        ),
        None,
    )
    if existing is not None:
        return existing

    history = _read_job_error_history()
    if notification.video_id is not None:
        values = history.setdefault(notification.video_id, [])
        values.append(notification)
        history[notification.video_id] = _latest_job_errors(
            values,
            MAX_VIDEO_JOB_ERROR_NOTIFICATIONS,
        )
        st.session_state[SESSION_JOB_ERROR_HISTORY] = history
    else:
        global_errors = _read_job_error_list(
            st.session_state.get(SESSION_GLOBAL_JOB_ERRORS)
        )
        global_errors.append(replace(notification, detail=None))
        st.session_state[SESSION_GLOBAL_JOB_ERRORS] = _latest_job_errors(
            global_errors,
            MAX_GLOBAL_JOB_ERROR_NOTIFICATIONS,
        )

    unread = _read_job_error_list(st.session_state.get(SESSION_UNREAD_JOB_ERRORS))
    unread.append(replace(notification, detail=None))
    st.session_state[SESSION_UNREAD_JOB_ERRORS] = _latest_job_errors(
        unread,
        MAX_UNREAD_JOB_ERROR_NOTIFICATIONS,
    )
    return notification


def record_job_error(
    video_id: str | None,
    job_id: str | None,
    kind: str,
    summary: str,
    detail: str | None = None,
    occurred_at: datetime | None = None,
) -> JobErrorNotification:
    """構造化ジョブエラーを作成して session state へ記録する。"""
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)
    if not isinstance(occurred_at, datetime):
        raise TypeError("occurred_at は datetime で指定してください")
    notification = JobErrorNotification(
        video_id=video_id,
        job_id=job_id,
        kind=str(kind),
        summary=str(summary),
        detail=None if detail is None else str(detail),
        occurred_at=occurred_at,
    )
    return add_job_error_notification(notification)


def get_job_error_history(video_id: str | None) -> list[JobErrorNotification]:
    """指定動画の直近 3 件を返す。読み取り専用で consume / clear はしない。"""
    if not isinstance(video_id, str) or not video_id:
        return []
    history = _read_job_error_history()
    return _latest_job_errors(
        list(history.get(video_id, [])),
        MAX_VIDEO_JOB_ERROR_NOTIFICATIONS,
    )


def get_video_job_error_history(video_id: str | None) -> list[JobErrorNotification]:
    """``get_job_error_history`` の動画詳細用公開別名。"""
    return get_job_error_history(video_id)


def get_global_job_error_notifications() -> list[JobErrorNotification]:
    """動画に紐づかない直近 3 件の要約を返す。詳細は常に保持しない。"""
    return _latest_job_errors(
        _read_job_error_list(st.session_state.get(SESSION_GLOBAL_JOB_ERRORS)),
        MAX_GLOBAL_JOB_ERROR_NOTIFICATIONS,
    )


def get_job_error_notifications(
    video_id: str | None = None,
) -> list[JobErrorNotification]:
    """動画指定なら詳細履歴、動画なしならグローバル要約を返す read-only getter。"""
    if video_id is None:
        return get_global_job_error_notifications()
    return get_job_error_history(video_id)


def get_unread_job_error_notifications() -> list[JobErrorNotification]:
    """ページ上部でまだ consume していない要約だけを返す。"""
    return _latest_job_errors(
        _read_job_error_list(st.session_state.get(SESSION_UNREAD_JOB_ERRORS)),
        MAX_UNREAD_JOB_ERROR_NOTIFICATIONS,
    )


def consume_unread_job_error_notifications() -> list[JobErrorNotification]:
    """ページ上部用 unread を一度だけ返して消費する。詳細履歴は変更しない。"""
    notifications = get_unread_job_error_notifications()
    st.session_state[SESSION_UNREAD_JOB_ERRORS] = []
    return notifications


def _coerce_pipeline_completion_notification(
    value: object,
) -> PipelineCompletionNotification | None:
    if isinstance(value, PipelineCompletionNotification):
        return value
    if isinstance(value, dict):
        try:
            return PipelineCompletionNotification.from_dict(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def get_pipeline_completion_notifications() -> list[PipelineCompletionNotification]:
    """未消去の完了通知を新しい順で返す。読み取り時は state を変更しない。"""
    raw = st.session_state.get(SESSION_PIPELINE_COMPLETIONS)
    if not isinstance(raw, (list, tuple)):
        return []
    notifications = [
        notification
        for value in raw
        if (notification := _coerce_pipeline_completion_notification(value)) is not None
    ]
    return sorted(
        notifications,
        key=lambda notification: notification.completed_at,
        reverse=True,
    )[:MAX_PIPELINE_COMPLETION_NOTIFICATIONS]


def record_pipeline_completion(
    video_id: str,
    job_id: str,
    kind: str,
    title: str,
    completed_at: datetime | None = None,
) -> PipelineCompletionNotification:
    """pipeline 完了を job ID 単位で一度だけ通知へ追加する。"""
    notification = PipelineCompletionNotification(
        video_id=str(video_id),
        job_id=str(job_id),
        kind=str(kind),
        title=str(title),
        completed_at=completed_at or datetime.now(timezone.utc),
    )
    notifications = get_pipeline_completion_notifications()
    existing = next(
        (item for item in notifications if item.job_id == notification.job_id),
        None,
    )
    if existing is not None:
        return existing

    notifications.append(notification)
    st.session_state[SESSION_PIPELINE_COMPLETIONS] = sorted(
        notifications,
        key=lambda item: item.completed_at,
        reverse=True,
    )[:MAX_PIPELINE_COMPLETION_NOTIFICATIONS]
    return notification


def dismiss_pipeline_completion(job_id: str) -> bool:
    """指定 job の完了通知だけを消去する。"""
    notifications = get_pipeline_completion_notifications()
    remaining = [item for item in notifications if item.job_id != job_id]
    if len(remaining) == len(notifications):
        return False
    st.session_state[SESSION_PIPELINE_COMPLETIONS] = remaining
    return True

_orphans_initialized = False


def clear_result() -> None:
    st.session_state[SESSION_RESULT] = None


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


def get_selected_video_id() -> str | None:
    raw = st.session_state.get(SESSION_SELECTED_VIDEO_ID)
    if isinstance(raw, str) and raw:
        return raw
    return None


def set_selected_video_id(video_id: str | None) -> None:
    st.session_state[SESSION_SELECTED_VIDEO_ID] = video_id


def init_orphans_once() -> list[str]:
    """プロセス起動時に 1 回だけ孤児ジョブをクローズする."""
    global _orphans_initialized
    if _orphans_initialized:
        return []
    _orphans_initialized = True

    from yt_live_kit.services.jobs import close_orphans, cleanup_finished
    from yt_live_kit.config import get_settings

    settings = get_settings()
    interrupted_ids = close_orphans(settings)
    cleanup_finished(older_than_hours=24, settings=settings)
    if not interrupted_ids:
        return interrupted_ids

    for job_id in interrupted_ids:
        # app.py が st.warning で通知済みのため、find_restorable_job 側で
        # 二重に拾わないようここでハンドル済みにしておく。
        mark_job_handled(job_id)
    return interrupted_ids
