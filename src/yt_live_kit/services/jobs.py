"""バックグラウンドジョブの状態管理とワーカー起動."""

from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.ai_prompt import AiPromptError
from yt_live_kit.services.channel import ChannelError
from yt_live_kit.services.clips import ClipsError
from yt_live_kit.services.description import DescriptionError
from yt_live_kit.services.ffmpeg import FfmpegError
from yt_live_kit.services.pipeline import PipelineError
from yt_live_kit.services.storage import StorageError
from yt_live_kit.services.transcript import TranscriptError
from yt_live_kit.services.ytdlp import YtdlpError

JobKind = str  # "single" | "batch" | "regenerate" | "highlights" | "shorts"
JobStatus = str  # "running" | "done" | "failed" | "interrupted"

_UNEXPECTED_ERROR_MESSAGE = (
    "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
)
_BUSY_MESSAGE = "別の処理が実行中です。完了してから再度お試しください。"

_KNOWN_ERRORS = (
    AiPromptError,
    FfmpegError,
    PipelineError,
    YtdlpError,
    ClipsError,
    TranscriptError,
    DescriptionError,
    ChannelError,
    StorageError,
)


class JobBusyError(Exception):
    """同時実行制限によりジョブを開始できない."""

    def __init__(self, message: str = _BUSY_MESSAGE) -> None:
        super().__init__(message)


@dataclass
class JobState:
    """永続化されるジョブ状態."""

    job_id: str
    kind: str
    status: str
    video_id: str | None = None
    title: str | None = None
    stage: str | None = None
    message: str = ""
    current: int = 0
    total: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    error: str | None = None
    result_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "video_id": self.video_id,
            "title": self.title,
            "stage": self.stage,
            "message": self.message,
            "current": self.current,
            "total": self.total,
            "started_at": _dt_to_iso(self.started_at),
            "finished_at": _dt_to_iso(self.finished_at) if self.finished_at else None,
            "error": self.error,
            "result_ref": self.result_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobState:
        return cls(
            job_id=str(data["job_id"]),
            kind=str(data["kind"]),
            status=str(data["status"]),
            video_id=data.get("video_id"),
            title=data.get("title"),
            stage=data.get("stage"),
            message=str(data.get("message", "")),
            current=int(data.get("current", 0)),
            total=int(data.get("total", 0)),
            started_at=_dt_from_iso(str(data["started_at"])),
            finished_at=_dt_from_iso(str(data["finished_at"]))
            if data.get("finished_at")
            else None,
            error=data.get("error"),
            result_ref=data.get("result_ref"),
        )


def _dt_to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _dt_from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _jobs_dir(settings: Settings) -> Path:
    return settings.data_dir / "_jobs"


def _job_path(settings: Settings, job_id: str) -> Path:
    return _jobs_dir(settings) / f"{job_id}.json"


def _job_log_path(settings: Settings, job_id: str) -> Path:
    return _jobs_dir(settings) / f"{job_id}.log"


def _current_path(settings: Settings) -> Path:
    return _jobs_dir(settings) / "current.json"


def _write_job(state: JobState, settings: Settings) -> None:
    jobs_dir = _jobs_dir(settings)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = _job_path(settings, state.job_id)
    tmp_path = jobs_dir / f".{state.job_id}.tmp"
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)


def _write_current(job_id: str, settings: Settings) -> None:
    jobs_dir = _jobs_dir(settings)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = _current_path(settings)
    tmp_path = jobs_dir / ".current.tmp"
    payload = json.dumps({"job_id": job_id}, ensure_ascii=False, indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)


def create_job(
    kind: str,
    *,
    video_id: str | None = None,
    title: str | None = None,
    total: int = 0,
    settings: Settings | None = None,
) -> JobState:
    """新規ジョブを running で作成し、永続化する."""
    settings = settings or get_settings()
    settings.ensure_data_dir()
    state = JobState(
        job_id=uuid.uuid4().hex,
        kind=kind,
        status="running",
        video_id=video_id,
        title=title,
        total=total,
        message="開始しました",
    )
    _write_job(state, settings)
    _write_current(state.job_id, settings)
    return state


def update_job(job_id: str, *, settings: Settings | None = None, **fields: Any) -> JobState:
    """ジョブ状態を部分更新する."""
    settings = settings or get_settings()
    state = read_job(job_id, settings)
    if state is None:
        raise KeyError(f"ジョブが見つかりません: {job_id}")

    for key, value in fields.items():
        if hasattr(state, key):
            setattr(state, key, value)

    _write_job(state, settings)
    return state


def read_job(job_id: str, settings: Settings | None = None) -> JobState | None:
    """ジョブ状態を読み込む。存在しない・壊れた JSON の場合は None."""
    settings = settings or get_settings()
    path = _job_path(settings, job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return JobState.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def list_jobs(settings: Settings | None = None) -> list[JobState]:
    """全ジョブを started_at の新しい順で返す."""
    settings = settings or get_settings()
    jobs_dir = _jobs_dir(settings)
    if not jobs_dir.is_dir():
        return []

    jobs: list[JobState] = []
    for path in jobs_dir.glob("*.json"):
        state = read_job(path.stem, settings)
        if state is not None:
            jobs.append(state)

    jobs.sort(key=lambda job: job.started_at, reverse=True)
    return jobs


def read_current_job(settings: Settings | None = None) -> JobState | None:
    """current.json が指す最新ジョブを読み込む。

    current.json が無い・壊れている・指す先のジョブが無い場合は None を返す。
    """
    settings = settings or get_settings()
    path = _current_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    job_id = data.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return None
    return read_job(job_id, settings)


def get_active_job(settings: Settings | None = None) -> JobState | None:
    """status が running の最新ジョブを返す（current.json 経由、定数時間）."""
    job = read_current_job(settings)
    if job is not None and job.status == "running":
        return job
    return None


def is_busy(settings: Settings | None = None) -> bool:
    """実行中ジョブがあるか."""
    return get_active_job(settings) is not None


def cleanup_finished(older_than_hours: int = 24, settings: Settings | None = None) -> int:
    """完了済みジョブの JSON を削除し、削除件数を返す."""
    settings = settings or get_settings()
    jobs_dir = _jobs_dir(settings)
    if not jobs_dir.is_dir():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    current_job = read_current_job(settings)
    current_job_id = current_job.job_id if current_job is not None else None
    removed = 0
    for job in list_jobs(settings):
        if job.job_id == current_job_id:
            continue
        if job.status not in ("done", "failed", "interrupted"):
            continue
        if job.finished_at is None or job.finished_at >= cutoff:
            continue
        json_path = _job_path(settings, job.job_id)
        log_path = _job_log_path(settings, job.job_id)
        if json_path.is_file():
            json_path.unlink()
        if log_path.is_file():
            log_path.unlink()
        removed += 1
    return removed


def close_orphans(settings: Settings | None = None) -> list[str]:
    """起動時に running のジョブを interrupted にする.

    同時実行は 1 件に制限しているため、プロセス生存判定は行わず、
    起動時点で status が running のジョブはすべて前回の異常終了とみなす。
    """
    settings = settings or get_settings()
    interrupted: list[str] = []
    now = datetime.now(timezone.utc)
    for job in list_jobs(settings):
        if job.status != "running":
            continue
        update_job(
            job.job_id,
            settings=settings,
            status="interrupted",
            finished_at=now,
            message="前回の処理が中断されました",
            error="前回の処理が中断されました",
        )
        interrupted.append(job.job_id)
    return interrupted


def _error_message_for(exc: BaseException) -> tuple[str, bool]:
    """ユーザー向けメッセージと、詳細ログが必要かを返す."""
    if isinstance(exc, _KNOWN_ERRORS):
        return str(exc), False
    return _UNEXPECTED_ERROR_MESSAGE, True


def _write_error_log(settings: Settings, job_id: str, exc: BaseException) -> None:
    log_path = _job_log_path(settings, job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(traceback.format_exc(), encoding="utf-8")


def start_job(
    kind: str,
    target_fn: Callable[..., Any],
    *,
    video_id: str | None = None,
    title: str | None = None,
    total: int = 0,
    settings: Settings | None = None,
    **kwargs: Any,
) -> str:
    """バックグラウンドで target_fn を実行し、job_id を返す."""
    settings = settings or get_settings()
    if is_busy(settings):
        raise JobBusyError()

    state = create_job(
        kind,
        video_id=video_id,
        title=title,
        total=total,
        settings=settings,
    )
    job_id = state.job_id

    def report(
        *,
        stage: str | None = None,
        message: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if stage is not None:
            fields["stage"] = stage
        if message is not None:
            fields["message"] = message
        if current is not None:
            fields["current"] = current
        if total is not None:
            fields["total"] = total
        if fields:
            update_job(job_id, settings=settings, **fields)

    def worker() -> None:
        try:
            target_fn(report=report, settings=settings, **kwargs)
        except BaseException as exc:
            message, needs_log = _error_message_for(exc)
            if needs_log:
                _write_error_log(settings, job_id, exc)
            update_job(
                job_id,
                settings=settings,
                status="failed",
                finished_at=datetime.now(timezone.utc),
                error=message,
                message=message,
            )
            return

        result_ref = video_id
        current = read_job(job_id, settings)
        if current is not None and current.video_id:
            result_ref = current.video_id

        update_job(
            job_id,
            settings=settings,
            status="done",
            finished_at=datetime.now(timezone.utc),
            result_ref=result_ref,
            message="完了しました",
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return job_id
