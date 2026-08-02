"""バックグラウンドジョブの状態管理とワーカー起動."""

from __future__ import annotations

import inspect
import fcntl
import json
import os
import threading
import traceback
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.ai_prompt import AiPromptError
from yt_live_kit.services.channel import ChannelError
from yt_live_kit.services.clips import ClipsError
from yt_live_kit.services.description import DescriptionError
from yt_live_kit.services.ffmpeg import FfmpegError
from yt_live_kit.services.highlights import HighlightsError
from yt_live_kit.services.pipeline import PipelineError
from yt_live_kit.services.shorts import ShortsError
from yt_live_kit.services.storage import StorageError
from yt_live_kit.services.subtitle_burn import SubtitleBurnError
from yt_live_kit.services.transcript import TranscriptError
from yt_live_kit.services.ytdlp import YtdlpError
from yt_live_kit.services.youtube_api import YouTubeAPIError
from yt_live_kit.services.upload_queue import UploadQueueError
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_path,
    safe_identifier,
    safe_video_identifier,
)

JobKind = str  # pipeline / batch / shorts_queue / upload
JobStatus = str  # "running" | "done" | "failed" | "interrupted"

_UNEXPECTED_ERROR_MESSAGE = (
    "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
)
_BUSY_MESSAGE = "別の処理が実行中です。完了してから再度お試しください。"

# is_busy() のチェックと create_job() の間の競合を防ぐための process 内ロック。
# 複数の Streamlit process 間は _job_store_lock() の advisory file lock で直列化する。
_START_LOCK = threading.Lock()
_JOB_PROCESS_LOCK = threading.RLock()
_JOB_LOCK_STATE = threading.local()
_ACTIVE_WORKERS_LOCK = threading.Lock()
_ACTIVE_WORKERS: set[tuple[str, str]] = set()

_CURRENT_MISSING = "missing"
_CURRENT_VALID = "valid"
_CURRENT_CORRUPT = "corrupt"
_CURRENT_TARGET_MISSING = "target_missing"
_CurrentPointerStatus = Literal["missing", "valid", "corrupt", "target_missing"]

_KNOWN_ERRORS = (
    AiPromptError,
    FfmpegError,
    PipelineError,
    YtdlpError,
    ClipsError,
    HighlightsError,
    TranscriptError,
    DescriptionError,
    ChannelError,
    StorageError,
    ShortsError,
    SubtitleBurnError,
    YouTubeAPIError,
    UploadQueueError,
)


class JobBusyError(Exception):
    """同時実行制限によりジョブを開始できない."""

    def __init__(self, message: str = _BUSY_MESSAGE) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class _CurrentPointer:
    """current.json の読み込み結果。missing と fail-closed 状態を区別する."""

    status: _CurrentPointerStatus
    job: JobState | None = None


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
    owner_pid: int | None = None
    owner_token: str | None = None

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
            "owner_pid": self.owner_pid,
            "owner_token": self.owner_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobState:
        job_id = data.get("job_id")
        _validate_job_id(job_id)
        video_id = data.get("video_id")
        if video_id is not None:
            _validate_video_id(video_id)
        owner_pid = data.get("owner_pid")
        if owner_pid is not None and (
            isinstance(owner_pid, bool)
            or not isinstance(owner_pid, int)
            or owner_pid <= 0
        ):
            raise ValueError("owner_pid の形式が正しくありません")
        owner_token = data.get("owner_token")
        if owner_token is not None and (
            not isinstance(owner_token, str) or not owner_token
        ):
            raise ValueError("owner_token の形式が正しくありません")
        return cls(
            job_id=job_id,
            kind=str(data["kind"]),
            status=str(data["status"]),
            video_id=video_id,
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
            owner_pid=owner_pid,
            owner_token=owner_token,
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
    return confined_path(settings.data_dir, "_jobs", label="ジョブ保存先")


def _job_path(settings: Settings, job_id: str) -> Path:
    _validate_job_id(job_id)
    return confined_path(
        settings.data_dir, "_jobs", f"{job_id}.json", label="ジョブ保存先"
    )


def _job_log_path(settings: Settings, job_id: str) -> Path:
    _validate_job_id(job_id)
    return confined_path(
        settings.data_dir, "_jobs", f"{job_id}.log", label="ジョブログ保存先"
    )


def _current_path(settings: Settings) -> Path:
    return confined_path(
        settings.data_dir, "_jobs", "current.json", label="ジョブ保存先"
    )


@contextmanager
def _job_store_lock(settings: Settings) -> Iterator[None]:
    """data root 単位の process / advisory file lock を取得する.

    lock の取得順を process lock → file lock に固定し、同一スレッド内の
    nested call は再取得しない。ファイル lock は process 終了時に OS が解放
    するため、stale lock file 自体を削除する必要がない。
    """
    _jobs_dir(settings)
    lock_path = confined_path(settings.data_dir, ".jobs.lock", label="ジョブ排他ロック")
    settings.ensure_data_dir()
    with _JOB_PROCESS_LOCK:
        if getattr(_JOB_LOCK_STATE, "depth", 0):
            _JOB_LOCK_STATE.depth += 1
            try:
                yield
            finally:
                _JOB_LOCK_STATE.depth -= 1
            return

        try:
            lock_file = lock_path.open("a+b")
        except OSError as exc:
            raise JobBusyError(
                "ジョブの排他ロックを取得できませんでした。"
            ) from exc
        try:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise JobBusyError(
                    "ジョブの排他ロックを取得できませんでした。"
                ) from exc
            _JOB_LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _JOB_LOCK_STATE.depth = 0
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            lock_file.close()


def _atomic_write_text(path: Path, payload: str) -> None:
    """同一ディレクトリの UUID 一時ファイルから安全にテキストを置換する."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        file_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_job(state: JobState, settings: Settings) -> None:
    jobs_dir = _jobs_dir(settings)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = _job_path(settings, state.job_id)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    _atomic_write_text(path, payload)


def _write_current(
    job_id: str,
    settings: Settings,
    *,
    owner_pid: int | None = None,
    owner_token: str | None = None,
) -> None:
    jobs_dir = _jobs_dir(settings)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = _current_path(settings)
    payload = json.dumps(
        {
            "job_id": job_id,
            "owner_pid": owner_pid,
            "owner_token": owner_token,
        },
        ensure_ascii=False,
        indent=2,
    )
    _atomic_write_text(path, payload)


def _validate_job_id(job_id: str) -> None:
    try:
        safe_identifier(job_id, "ジョブ ID")
    except PathConfinementError as exc:
        raise ValueError(str(exc)) from exc
    if job_id == "current":
        raise ValueError("予約されたジョブ ID は使用できません。")


def _validate_video_id(video_id: object) -> str:
    try:
        return safe_video_identifier(video_id)
    except PathConfinementError as exc:
        raise ValueError(str(exc)) from exc


def _create_job_unlocked(
    kind: str,
    *,
    video_id: str | None = None,
    title: str | None = None,
    total: int = 0,
    requested_job_id: str | None = None,
    owner_pid: int | None = None,
    owner_token: str | None = None,
    settings: Settings,
) -> JobState:
    job_id = requested_job_id or uuid.uuid4().hex
    _validate_job_id(job_id)
    if video_id is not None:
        _validate_video_id(video_id)
    if _job_path(settings, job_id).exists():
        raise ValueError("指定されたジョブ ID は既に存在します。")
    state = JobState(
        job_id=job_id,
        kind=kind,
        status="running",
        video_id=video_id,
        title=title,
        total=total,
        message="開始しました",
        owner_pid=owner_pid,
        owner_token=owner_token,
    )
    _write_job(state, settings)
    _write_current(
        state.job_id,
        settings,
        owner_pid=state.owner_pid,
        owner_token=state.owner_token,
    )
    return state


def create_job(
    kind: str,
    *,
    video_id: str | None = None,
    title: str | None = None,
    total: int = 0,
    requested_job_id: str | None = None,
    settings: Settings | None = None,
) -> JobState:
    """新規ジョブを running で作成し、永続化する."""
    settings = settings or get_settings()
    if requested_job_id is not None:
        _validate_job_id(requested_job_id)
    if video_id is not None:
        _validate_video_id(video_id)
    with _job_store_lock(settings):
        return _create_job_unlocked(
            kind,
            video_id=video_id,
            title=title,
            total=total,
            requested_job_id=requested_job_id,
            settings=settings,
        )


def _update_job_unlocked(
    job_id: str,
    *,
    settings: Settings,
    fields: dict[str, Any],
) -> JobState:
    if "video_id" in fields and fields["video_id"] is not None:
        _validate_video_id(fields["video_id"])
    state = read_job(job_id, settings)
    if state is None:
        raise KeyError(f"ジョブが見つかりません: {job_id}")

    for key, value in fields.items():
        if hasattr(state, key):
            setattr(state, key, value)

    _write_job(state, settings)
    return state


def update_job(job_id: str, *, settings: Settings | None = None, **fields: Any) -> JobState:
    """ジョブ状態を部分更新する."""
    settings = settings or get_settings()
    _validate_job_id(job_id)
    if "video_id" in fields and fields["video_id"] is not None:
        _validate_video_id(fields["video_id"])
    with _job_store_lock(settings):
        return _update_job_unlocked(job_id, settings=settings, fields=fields)


def read_job(job_id: str, settings: Settings | None = None) -> JobState | None:
    """ジョブ状態を読み込む。存在しない・壊れた JSON の場合は None."""
    settings = settings or get_settings()
    path = _job_path(settings, job_id)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return JobState.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError, TypeError, UnicodeError, ValueError):
        return None


def list_jobs(settings: Settings | None = None) -> list[JobState]:
    """全ジョブを started_at の新しい順で返す."""
    settings = settings or get_settings()
    with _job_store_lock(settings):
        return _list_jobs_unlocked(settings)


def _list_jobs_unlocked(settings: Settings) -> list[JobState]:
    jobs_dir = _jobs_dir(settings)
    try:
        if not jobs_dir.is_dir():
            return []
    except OSError:
        return []

    jobs: list[JobState] = []
    for path in jobs_dir.glob("*.json"):
        if path.name == "current.json":
            continue
        state = read_job(path.stem, settings)
        if state is not None:
            jobs.append(state)

    jobs.sort(key=lambda job: job.started_at, reverse=True)
    return jobs


def _scan_running_jobs(settings: Settings) -> list[JobState]:
    """current.json に依存せず running を走査する。

    pointer が無いときに壊れた job JSON を無視すると、未知の running job を
    見落として start を許すため、scan では個別 JSON の破損を fail closed にする。
    """
    jobs_dir = _jobs_dir(settings)
    try:
        if not jobs_dir.is_dir():
            return []
    except OSError as exc:
        raise JobBusyError(
            "ジョブ状態を確認できないため、新しい処理を開始できません。"
        ) from exc

    jobs: list[JobState] = []
    for path in jobs_dir.glob("*.json"):
        if path.name == "current.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("ジョブ JSON の形式が正しくありません")
            job = JobState.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError, TypeError, UnicodeError, ValueError) as exc:
            raise JobBusyError(
                "ジョブ状態を確認できないため、新しい処理を開始できません。"
            ) from exc
        if job.status == "running":
            jobs.append(job)
    jobs.sort(key=lambda job: (job.started_at, job.job_id), reverse=True)
    return jobs


def _read_current_pointer(settings: Settings) -> _CurrentPointer:
    path = _current_path(settings)
    try:
        if not path.is_file():
            return _CurrentPointer(_CURRENT_MISSING)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
        return _CurrentPointer(_CURRENT_CORRUPT)
    if not isinstance(data, dict):
        return _CurrentPointer(_CURRENT_CORRUPT)

    job_id = data.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return _CurrentPointer(_CURRENT_CORRUPT)
    try:
        target_path = _job_path(settings, job_id)
        if not target_path.is_file():
            return _CurrentPointer(_CURRENT_TARGET_MISSING)
    except OSError:
        return _CurrentPointer(_CURRENT_CORRUPT)
    except ValueError:
        return _CurrentPointer(_CURRENT_CORRUPT)
    job = read_job(job_id, settings)
    if job is None:
        return _CurrentPointer(_CURRENT_TARGET_MISSING)

    pointer_owner_pid = data.get("owner_pid")
    pointer_owner_token = data.get("owner_token")
    if (
        pointer_owner_pid != job.owner_pid
        or pointer_owner_token != job.owner_token
    ):
        return _CurrentPointer(_CURRENT_CORRUPT)
    return _CurrentPointer(_CURRENT_VALID, job)


def read_current_job(settings: Settings | None = None) -> JobState | None:
    """current.json が指す最新ジョブを読み込む。

    current.json が無い・壊れている・指す先のジョブが無い場合は None を返す。
    """
    settings = settings or get_settings()
    with _job_store_lock(settings):
        return _read_current_pointer(settings).job


def _get_active_job_unlocked(settings: Settings) -> JobState | None:
    pointer = _read_current_pointer(settings)
    if pointer.status == _CURRENT_VALID:
        if pointer.job is not None and pointer.job.status == "running":
            return pointer.job
    running = _scan_running_jobs(settings)
    return running[0] if running else None


def _is_busy_unlocked(settings: Settings) -> bool:
    pointer = _read_current_pointer(settings)
    if pointer.status == _CURRENT_VALID:
        if pointer.job is not None and pointer.job.status == "running":
            return True
        return bool(_scan_running_jobs(settings))
    if pointer.status == _CURRENT_MISSING:
        return bool(_scan_running_jobs(settings))
    # 破損 pointer / target missing は、running scan が空でも状態不明なので
    # 新しい job を開始しない。手動修復後に再開できる fail-closed 境界とする。
    return True


def get_active_job(settings: Settings | None = None) -> JobState | None:
    """status が running のジョブを返す。pointer が不正なら安全に走査する."""
    settings = settings or get_settings()
    with _job_store_lock(settings):
        return _get_active_job_unlocked(settings)


def is_busy(settings: Settings | None = None) -> bool:
    """実行中ジョブがあるか."""
    settings = settings or get_settings()
    try:
        with _job_store_lock(settings):
            return _is_busy_unlocked(settings)
    except JobBusyError:
        return True


def cleanup_finished(older_than_hours: int = 24, settings: Settings | None = None) -> int:
    """完了済みジョブの JSON を削除し、削除件数を返す."""
    settings = settings or get_settings()
    with _job_store_lock(settings):
        jobs_dir = _jobs_dir(settings)
        if not jobs_dir.is_dir():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        current_job = _read_current_pointer(settings).job
        current_job_id = current_job.job_id if current_job is not None else None
        removed = 0
        for job in _list_jobs_unlocked(settings):
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


def _worker_key(settings: Settings, owner_token: str) -> tuple[str, str]:
    return (str(settings.data_dir.resolve()), owner_token)


def _register_worker(settings: Settings, owner_token: str) -> None:
    with _ACTIVE_WORKERS_LOCK:
        _ACTIVE_WORKERS.add(_worker_key(settings, owner_token))


def _unregister_worker(settings: Settings, owner_token: str) -> None:
    with _ACTIVE_WORKERS_LOCK:
        _ACTIVE_WORKERS.discard(_worker_key(settings, owner_token))


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def _owner_is_live(job: JobState, settings: Settings) -> bool:
    if job.owner_pid is None or not job.owner_token:
        return False
    if job.owner_pid == os.getpid():
        with _ACTIVE_WORKERS_LOCK:
            return _worker_key(settings, job.owner_token) in _ACTIVE_WORKERS
    return _pid_is_alive(job.owner_pid)


def close_orphans(settings: Settings | None = None) -> list[str]:
    """起動時に running のジョブを interrupted にする.

    owner PID / token から worker が live と証明できる場合は変更しない。
    current.json が壊れていても全ジョブを安全に走査するが、pointer の不確実性
    自体を busy 扱いにする判断は is_busy() が担う。
    """
    settings = settings or get_settings()
    with _job_store_lock(settings):
        interrupted: list[str] = []
        now = datetime.now(timezone.utc)
        for job in _scan_running_jobs(settings):
            if _owner_is_live(job, settings):
                continue
            _update_job_unlocked(
                job.job_id,
                settings=settings,
                fields={
                    "status": "interrupted",
                    "finished_at": now,
                    "message": "前回の処理が中断されました",
                    "error": "前回の処理が中断されました",
                },
            )
            interrupted.append(job.job_id)
    # 外部効果の有無は jobs JSON ではなく attempt 台帳を正本として、
    # 必ず orphan close 後に upload operation を復元する。
    from yt_live_kit.services.upload_queue import recover_upload_operations

    recover_upload_operations(settings)
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


def _target_accepts_video_id(target_fn: Callable[..., Any]) -> bool:
    """target_fn が video_id キーワード引数を受け取れるか調べる.

    UI 側のジョブ関数（ハイライト・ショート等）は video_id を必須キーワード
    引数として宣言しているが、run_single_job_target 等は url を使うため
    video_id を持たない。無条件に渡すと後者が TypeError になるため、
    シグネチャを見て受け取れる場合のみ渡す。
    """
    try:
        signature = inspect.signature(target_fn)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == "video_id":
            return True
    return False


def start_job(
    kind: str,
    target_fn: Callable[..., Any],
    *,
    video_id: str | None = None,
    title: str | None = None,
    total: int = 0,
    requested_job_id: str | None = None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> str:
    """バックグラウンドで target_fn を実行し、job_id を返す.

    busy 判定・job JSON・current pointer の作成・worker thread 起動準備を
    data root の process / advisory lock 内で行う。これにより同じ data root
    を共有する複数 process でも勝者を 1 件にする。
    """
    settings = settings or get_settings()
    if requested_job_id is not None:
        _validate_job_id(requested_job_id)
    if video_id is not None:
        _validate_video_id(video_id)
    with _START_LOCK:
        with _job_store_lock(settings):
            if _is_busy_unlocked(settings):
                raise JobBusyError()

            owner_token = uuid.uuid4().hex
            state = _create_job_unlocked(
                kind,
                video_id=video_id,
                title=title,
                total=total,
                requested_job_id=requested_job_id,
                owner_pid=os.getpid(),
                owner_token=owner_token,
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
                    try:
                        call_kwargs: dict[str, Any] = dict(kwargs)
                        if _target_accepts_video_id(target_fn):
                            call_kwargs["video_id"] = video_id
                        target_fn(
                            report=report,
                            settings=settings,
                            job_id=job_id,
                            **call_kwargs,
                        )
                    except Exception as exc:
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
                    if current is not None:
                        # upload / shorts_queue 等の target が永続参照を明示した場合は
                        # pipeline 用 video_id で上書きしない。
                        if current.result_ref:
                            result_ref = current.result_ref
                        elif current.video_id:
                            result_ref = current.video_id

                    update_job(
                        job_id,
                        settings=settings,
                        status="done",
                        finished_at=datetime.now(timezone.utc),
                        result_ref=result_ref,
                        message="完了しました",
                    )
                finally:
                    _unregister_worker(settings, owner_token)

            thread = threading.Thread(target=worker, daemon=True)
            _register_worker(settings, owner_token)
            try:
                thread.start()
            except BaseException:
                _unregister_worker(settings, owner_token)
                raise
    return job_id
