"""予約 upload operation と upload attempt の安全な永続化."""

from __future__ import annotations

import fcntl
import errno
import hashlib
import json
import os
import time
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    UploadContentSnapshot,
    UploadOperation,
    UploadResult,
    UploadStatusObservation,
)
from yt_live_kit.services.youtube_api import (
    YouTubeAPIError,
    build_upload_body,
    fetch_mine_channel,
    poll_processing_status,
    poll_publication_status,
    upload_video_resumable,
    validate_snapshot_identity,
)
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_path,
    safe_identifier,
    safe_video_identifier,
)

_QUEUE_VERSION = 1
_ATTEMPTS_VERSION = 1
_PROCESS_LOCK = threading.RLock()
_LOCK_STATE = threading.local()
_LA = ZoneInfo("America/Los_Angeles")
_PUBLICATION_POLL_KIND = "upload_publication"
_PUBLICATION_POLL_TERMINAL = frozenset(
    {
        "published",
        "suspected_private_lock",
        "processing_failed",
        "publication_timeout",
    }
)
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "reserved": frozenset({"uploading", "failed", "needs_reconciliation"}),
    "uploading": frozenset({"uploaded", "failed", "needs_reconciliation"}),
    "uploaded": frozenset(),
    "failed": frozenset(),
    "needs_reconciliation": frozenset(),
}


class UploadQueueError(Exception):
    """永続 upload queue を安全に扱えないエラー."""


class UploadAttempt(BaseModel):
    """resumable upload session 開始直前に残す追記専用台帳行."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_date_la: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    operation_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    attempted_at: datetime

    @field_validator("attempted_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("試行日時はタイムゾーン付き UTC 日時にしてください。")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _date_matches_timestamp(self) -> UploadAttempt:
        try:
            parsed_date = date.fromisoformat(self.attempt_date_la)
        except ValueError as exc:
            raise ValueError("試行日の形式が正しくありません。") from exc
        expected = self.attempted_at.astimezone(_LA).date()
        if parsed_date != expected:
            raise ValueError("試行日が America/Los_Angeles の実試行日と一致しません。")
        return self


def _utc_timestamp(value: datetime | None, label: str) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise UploadQueueError(f"{label}はタイムゾーン付き日時で指定してください。")
    return timestamp.astimezone(timezone.utc)


def _schedule_dir(settings: Settings) -> Path:
    try:
        return confined_path(settings.data_dir, "_schedule", label="投稿データ保存先")
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc


def _queue_path(settings: Settings) -> Path:
    try:
        return confined_path(
            settings.data_dir, "_schedule", "queue.json", label="投稿キュー保存先"
        )
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc


def _attempts_path(settings: Settings) -> Path:
    try:
        return confined_path(
            settings.data_dir,
            "_schedule",
            "upload_attempts.json",
            label="アップロード試行台帳保存先",
        )
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc


def _publication_lock_path(
    settings: Settings, operation_id: str, *, purpose: str
) -> Path:
    """operation ID を path にせず、公開確認用 lock の場所を決める."""
    _safe_identifier(operation_id, "投稿 operation ID")
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    try:
        return confined_path(
            settings.data_dir,
            "_schedule",
            f".publication_{purpose}_{digest}.lock",
            label="公開状態確認ロック保存先",
        )
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc


def _safe_identifier(value: object, label: str) -> str:
    try:
        return safe_identifier(value, label)
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc


def _safe_video_identifier(value: object, label: str = "動画 ID") -> str:
    try:
        return safe_video_identifier(value, label)
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc


def _validate_operation_paths(operation: UploadOperation, settings: Settings) -> None:
    for value, label in (
        (operation.operation_id, "投稿 operation ID"),
        (operation.job_id, "ジョブ ID"),
        (operation.clip_id, "clip ID"),
    ):
        _safe_identifier(value, label)
    _safe_video_identifier(operation.source_video_id)
    if operation.video_id is not None:
        _safe_video_identifier(operation.video_id)
    for path, label in (
        (operation.video_path, "投稿動画保存先"),
        (operation.content.video_path, "投稿 snapshot 保存先"),
    ):
        try:
            confined_path(settings.data_dir, path, label=label)
        except PathConfinementError as exc:
            raise UploadQueueError(str(exc)) from exc


@contextmanager
def _publication_file_lock(
    settings: Settings, operation_id: str, *, purpose: str
) -> Iterator[None]:
    """公開確認の claim / poll を operation 単位で重複させない."""
    path = _publication_lock_path(settings, operation_id, purpose=purpose)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise UploadQueueError(
                    "同じ投稿 operation の公開状態確認が既に実行中です。"
                ) from exc
            raise UploadQueueError(
                "公開状態確認の lock を取得できないため、投稿を停止しました。"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def schedule_lock(settings: Settings) -> Iterator[None]:
    """process lock の後に advisory file lock を取る固定順序."""
    directory = _schedule_dir(settings)
    lock_path = confined_path(settings.data_dir, "_schedule", ".schedule.lock", label="投稿キュー保存先")
    directory.mkdir(parents=True, exist_ok=True)
    with _PROCESS_LOCK:
        if getattr(_LOCK_STATE, "depth", 0):
            _LOCK_STATE.depth += 1
            try:
                yield
            finally:
                _LOCK_STATE.depth -= 1
            return
        with lock_path.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise UploadQueueError("投稿キューのロックを取得できませんでした。") from exc
            try:
                _LOCK_STATE.depth = 1
                yield
            finally:
                _LOCK_STATE.depth = 0
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise UploadQueueError("投稿データを安全に保存できませんでした。") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path, *, empty: dict[str, Any], label: str) -> dict[str, Any]:
    if not path.exists():
        return empty
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UploadQueueError(
            f"{label}が壊れているため安全のため投稿を停止しました。手動で修復してください。"
        ) from exc
    if not isinstance(value, dict):
        raise UploadQueueError(f"{label}の形式が正しくないため投稿を停止しました。")
    return value


def _read_operations_unlocked(settings: Settings) -> list[UploadOperation]:
    value = _read_json(
        _queue_path(settings),
        empty={"schema_version": _QUEUE_VERSION, "operations": []},
        label="投稿キュー",
    )
    if set(value) != {"schema_version", "operations"} or value.get("schema_version") != _QUEUE_VERSION:
        raise UploadQueueError("投稿キューの schema が正しくないため投稿を停止しました。")
    raw = value.get("operations")
    if not isinstance(raw, list):
        raise UploadQueueError("投稿キューの operation 一覧が正しくありません。")
    try:
        operations = [UploadOperation.model_validate(item) for item in raw]
        for operation in operations:
            _validate_operation_paths(operation, settings)
    except ValidationError as exc:
        raise UploadQueueError(f"投稿キューの内容が正しくありません: {exc}") from exc
    identifiers = [item.operation_id for item in operations]
    if len(identifiers) != len(set(identifiers)):
        raise UploadQueueError("投稿キューに重複した operation ID があります。")
    return operations


def _write_operations_unlocked(settings: Settings, operations: list[UploadOperation]) -> None:
    _atomic_write(
        _queue_path(settings),
        {
            "schema_version": _QUEUE_VERSION,
            "operations": [item.model_dump(mode="json") for item in operations],
        },
    )


def _read_attempts_unlocked(settings: Settings) -> list[UploadAttempt]:
    value = _read_json(
        _attempts_path(settings),
        empty={"schema_version": _ATTEMPTS_VERSION, "attempts": []},
        label="アップロード試行台帳",
    )
    if set(value) != {"schema_version", "attempts"} or value.get("schema_version") != _ATTEMPTS_VERSION:
        raise UploadQueueError("アップロード試行台帳の schema が正しくありません。")
    raw = value.get("attempts")
    if not isinstance(raw, list):
        raise UploadQueueError("アップロード試行台帳の一覧が正しくありません。")
    try:
        attempts = [UploadAttempt.model_validate(item) for item in raw]
        for attempt in attempts:
            _safe_identifier(attempt.operation_id, "投稿 operation ID")
            _safe_identifier(attempt.job_id, "ジョブ ID")
    except ValidationError as exc:
        raise UploadQueueError(f"アップロード試行台帳の内容が正しくありません: {exc}") from exc
    keys = [(item.operation_id, item.job_id) for item in attempts]
    if len(keys) != len(set(keys)):
        raise UploadQueueError("アップロード試行台帳に重複した operation / job があります。")
    return attempts


def _write_attempts_unlocked(settings: Settings, attempts: list[UploadAttempt]) -> None:
    _atomic_write(
        _attempts_path(settings),
        {
            "schema_version": _ATTEMPTS_VERSION,
            "attempts": [item.model_dump(mode="json") for item in attempts],
        },
    )


def list_operations(settings: Settings) -> tuple[UploadOperation, ...]:
    with schedule_lock(settings):
        return tuple(_read_operations_unlocked(settings))


def load_operation(operation_id: str, settings: Settings) -> UploadOperation:
    _safe_identifier(operation_id, "投稿 operation ID")
    with schedule_lock(settings):
        matches = [
            item for item in _read_operations_unlocked(settings)
            if item.operation_id == operation_id
        ]
    if len(matches) != 1:
        raise UploadQueueError("指定された投稿 operation が見つかりません。")
    return matches[0]


def save_reserved_operation(operation: UploadOperation, settings: Settings) -> None:
    """P2 confirm transaction 用。reserved record を重複なく保存する."""
    if operation.state != "reserved":
        raise UploadQueueError("新しい投稿 operation は予約済み状態で保存してください。")
    _validate_operation_paths(operation, settings)
    with schedule_lock(settings):
        operations = _read_operations_unlocked(settings)
        if any(item.operation_id == operation.operation_id for item in operations):
            raise UploadQueueError("同じ operation ID は再利用できません。")
        operations.append(operation)
        _write_operations_unlocked(settings, operations)


def create_reserved_operation(
    *, operation_id: str, job_id: str, source_video_id: str,
    source_kind: str, clip_id: str, content: UploadContentSnapshot,
    now: datetime | None = None, settings: Settings,
) -> UploadOperation:
    _safe_identifier(operation_id, "投稿 operation ID")
    _safe_identifier(job_id, "ジョブ ID")
    _safe_video_identifier(source_video_id)
    _safe_identifier(clip_id, "clip ID")
    try:
        confined_path(settings.data_dir, content.video_path, label="投稿動画保存先")
    except PathConfinementError as exc:
        raise UploadQueueError(str(exc)) from exc
    created = _utc_timestamp(now, "operation 作成日時")
    operation = UploadOperation(
        operation_id=operation_id, source_video_id=source_video_id,
        source_kind=source_kind, clip_id=clip_id, video_path=content.video_path,
        content=content, state="reserved", job_id=job_id, video_id=None,
        created_at=created, updated_at=created, started_at=None, finished_at=None,
        error=None, poll_history=(), publication_eligibility="unknown",
    )
    save_reserved_operation(operation, settings)
    return operation


def _replace_operation_unlocked(
    settings: Settings, replacement: UploadOperation
) -> UploadOperation:
    operations = _read_operations_unlocked(settings)
    indexes = [index for index, item in enumerate(operations) if item.operation_id == replacement.operation_id]
    if len(indexes) != 1:
        raise UploadQueueError("更新する投稿 operation が見つかりません。")
    operations[indexes[0]] = replacement
    _write_operations_unlocked(settings, operations)
    return replacement


def transition_operation(
    operation_id: str, new_state: Literal["uploading", "uploaded", "failed", "needs_reconciliation"],
    settings: Settings, *, video_id: str | None = None,
    error: str | None = None, now: datetime | None = None,
) -> UploadOperation:
    _safe_identifier(operation_id, "投稿 operation ID")
    if video_id is not None:
        _safe_video_identifier(video_id)
    timestamp = _utc_timestamp(now, "operation 更新日時")
    with schedule_lock(settings):
        operations = _read_operations_unlocked(settings)
        matches = [item for item in operations if item.operation_id == operation_id]
        if len(matches) != 1:
            raise UploadQueueError("更新する投稿 operation が見つかりません。")
        current = matches[0]
        if new_state not in _ALLOWED_TRANSITIONS[current.state]:
            raise UploadQueueError(
                f"投稿 operation を {current.state} から {new_state} へ変更できません。"
            )
        if timestamp < current.updated_at:
            raise UploadQueueError("operation 更新日時を過去へ戻すことはできません。")
        values = current.model_dump()
        values.update(
            state=new_state,
            updated_at=timestamp,
            started_at=timestamp if new_state == "uploading" else current.started_at,
            finished_at=timestamp if new_state in {"uploaded", "failed", "needs_reconciliation"} else None,
            video_id=video_id or current.video_id,
            error=error,
        )
        try:
            replacement = UploadOperation.model_validate(values)
        except ValidationError as exc:
            raise UploadQueueError(f"投稿 operation の状態変更が正しくありません: {exc}") from exc
        index = operations.index(current)
        operations[index] = replacement
        _write_operations_unlocked(settings, operations)
        return replacement


def append_poll_observation(
    operation_id: str, observation: UploadStatusObservation, settings: Settings
) -> UploadOperation:
    _safe_identifier(operation_id, "投稿 operation ID")
    with schedule_lock(settings):
        operations = _read_operations_unlocked(settings)
        matches = [item for item in operations if item.operation_id == operation_id]
        if len(matches) != 1:
            raise UploadQueueError("poll 結果を保存する投稿 operation が見つかりません。")
        current = matches[0]
        if current.state != "uploaded":
            raise UploadQueueError("poll 結果はアップロード済み operation にだけ保存できます。")
        if current.poll_history and observation.polled_at < current.poll_history[-1].polled_at:
            raise UploadQueueError("poll 結果は取得順に追加してください。")
        eligibility = current.publication_eligibility
        if observation.classification in {
            "scheduled", "suspected_private_lock", "published"
        }:
            eligibility = observation.classification
        values = current.model_dump()
        values.update(
            poll_history=current.poll_history + (observation,),
            updated_at=max(current.updated_at, observation.polled_at),
            publication_eligibility=eligibility,
        )
        try:
            replacement = UploadOperation.model_validate(values)
        except ValidationError as exc:
            raise UploadQueueError(f"poll 結果を保存できませんでした: {exc}") from exc
        index = operations.index(current)
        operations[index] = replacement
        _write_operations_unlocked(settings, operations)
        return replacement


def _publication_poll_marker(operation_id: str) -> str:
    """job JSON の title に使う非秘密の operation marker."""
    _safe_identifier(operation_id, "投稿 operation ID")
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return f"publication:{digest}"


def _latest_publication_classification(
    operation: UploadOperation,
) -> str | None:
    for observation in reversed(operation.poll_history):
        if observation.phase == "publication":
            return observation.classification
    return None


def _validate_publication_poll_start(
    operation: UploadOperation, *, now: datetime
) -> None:
    if operation.state != "uploaded" or not operation.video_id:
        raise UploadQueueError(
            "アップロード済みの動画 ID がないため、公開状態を確認できません。"
        )
    latest = _latest_publication_classification(operation)
    if latest in _PUBLICATION_POLL_TERMINAL:
        raise UploadQueueError(
            "この投稿 operation の公開状態確認は既に完了しています。"
        )
    if now < operation.content.publish_at:
        raise UploadQueueError(
            "公開予定時刻までは公開状態を確認できません。予約時刻後に実行してください。"
        )


def _has_running_publication_poll(
    operation_id: str, settings: Settings
) -> bool:
    """永続 job JSON から同一 operation の実行中 poll を探す."""
    from yt_live_kit.services.jobs import list_jobs

    marker = _publication_poll_marker(operation_id)
    return any(
        job.kind == _PUBLICATION_POLL_KIND
        and job.title == marker
        and job.status == "running"
        for job in list_jobs(settings)
    )


def start_publication_poll(
    operation_id: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> str:
    """予約時刻後だけ bounded publication poll job を起動する.

    upload job とは別の job を作るため、予約時刻まで upload worker を
    占有しない。job の存在は jobs の永続 JSON で復元し、operation の状態と
    poll history は queue.json を正本として扱う。
    """
    _safe_identifier(operation_id, "投稿 operation ID")
    reference = _utc_timestamp(now, "公開状態確認日時")
    operation = load_operation(operation_id, settings)
    _validate_publication_poll_start(operation, now=reference)

    # start_job() 自体の process 間排他は既存契約のままなので、CTA の
    # claim だけを専用 lock で直列化し、同じ operation の二重 job 作成を防ぐ。
    with _publication_file_lock(settings, operation_id, purpose="claim"):
        operation = load_operation(operation_id, settings)
        _validate_publication_poll_start(operation, now=reference)
        if _has_running_publication_poll(operation_id, settings):
            raise UploadQueueError(
                "同じ投稿 operation の公開状態確認が既に実行中です。"
            )
        from yt_live_kit.services.jobs import JobBusyError, start_job

        try:
            return start_job(
                _PUBLICATION_POLL_KIND,
                publication_poll_job_target,
                video_id=operation.video_id,
                title=_publication_poll_marker(operation_id),
                settings=settings,
                operation_id=operation_id,
            )
        except JobBusyError as exc:
            raise UploadQueueError(
                "別の処理が実行中のため、公開状態確認を開始できません。"
            ) from exc
        except Exception as exc:
            raise UploadQueueError(
                "公開状態確認ジョブを開始できませんでした。"
            ) from exc


def publication_poll_job_target(
    *,
    report,
    settings: Settings,
    job_id: str,
    operation_id: str,
    service: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """1 operation の公開状態を bounded poll し、同じ queue record に追記する."""
    _safe_identifier(job_id, "ジョブ ID")
    _safe_identifier(operation_id, "投稿 operation ID")
    with _publication_file_lock(settings, operation_id, purpose="poll"):
        operation = load_operation(operation_id, settings)
        reference = _utc_timestamp(clock(), "公開状態確認日時")
        _validate_publication_poll_start(operation, now=reference)

        # follow-up job 自身の永続 result_ref を先に設定する。再起動後も
        # status bar と jobs の復元経路から operation ID を失わない。
        from yt_live_kit.services.jobs import update_job

        update_job(job_id, settings=settings, result_ref=operation_id)
        report(stage="publication", message="公開状態を確認しています")
        processing_succeeded = any(
            observation.phase == "processing"
            and observation.classification == "processing_succeeded"
            for observation in operation.poll_history
        )

        try:
            poll_publication_status(
                operation.video_id or "",
                operation.content.publish_at,
                settings,
                processing_succeeded=processing_succeeded,
                service=service,
                sleep_fn=sleep_fn,
                clock=clock,
                on_observation=lambda item: append_poll_observation(
                    operation_id, item, settings
                ),
            )
        except YouTubeAPIError as exc:
            raise UploadQueueError(
                "アップロード済み動画の公開状態を確認できませんでした。"
                "YouTube Studio で operation の動画 ID を手動確認してください。"
            ) from exc
        report(stage="publication", message="公開状態の確認が完了しました")


def set_publication_eligibility(
    operation_id: str,
    eligibility: Literal["confirmed_private_lock", "no_private_lock"],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> UploadOperation:
    """P0 の YouTube Studio 目視結果を upload 成否とは別に記録する."""
    _safe_identifier(operation_id, "投稿 operation ID")
    timestamp = _utc_timestamp(now, "公開可否の確認日時")
    with schedule_lock(settings):
        operations = _read_operations_unlocked(settings)
        matches = [item for item in operations if item.operation_id == operation_id]
        if len(matches) != 1:
            raise UploadQueueError("公開可否を記録する投稿 operation が見つかりません。")
        current = matches[0]
        if current.state != "uploaded":
            raise UploadQueueError("YouTube Studio の確認結果はアップロード済み operation に記録してください。")
        if timestamp < current.updated_at:
            raise UploadQueueError("公開可否の確認日時を過去へ戻すことはできません。")
        values = current.model_dump()
        values.update(
            publication_eligibility=eligibility,
            updated_at=max(timestamp, current.updated_at),
        )
        replacement = UploadOperation.model_validate(values)
        index = operations.index(current)
        operations[index] = replacement
        _write_operations_unlocked(settings, operations)
        return replacement


def list_upload_attempts(settings: Settings) -> tuple[UploadAttempt, ...]:
    with schedule_lock(settings):
        return tuple(_read_attempts_unlocked(settings))


def has_upload_attempt(operation_id: str, job_id: str, settings: Settings) -> bool:
    _safe_identifier(operation_id, "投稿 operation ID")
    _safe_identifier(job_id, "ジョブ ID")
    with schedule_lock(settings):
        return any(
            item.operation_id == operation_id and item.job_id == job_id
            for item in _read_attempts_unlocked(settings)
        )


def count_upload_attempts(
    settings: Settings, *, now: datetime | None = None
) -> int:
    timestamp = _utc_timestamp(now, "試行数の基準日時")
    target_date = timestamp.astimezone(_LA).date().isoformat()
    with schedule_lock(settings):
        return sum(
            item.attempt_date_la == target_date
            for item in _read_attempts_unlocked(settings)
        )


def record_upload_attempt(
    operation_id: str, job_id: str, settings: Settings, *, now: datetime | None = None
) -> UploadAttempt:
    """API session 作成前に 1 回だけ attempt を原子的に記録する."""
    _safe_identifier(operation_id, "投稿 operation ID")
    _safe_identifier(job_id, "ジョブ ID")
    if not 1 <= settings.video_upload_daily_limit <= 100:
        raise UploadQueueError("アップロード試行上限は 1〜100 で設定してください。")
    attempted_at = _utc_timestamp(now, "upload 試行日時")
    date_la = attempted_at.astimezone(_LA).date().isoformat()
    with schedule_lock(settings):
        attempts = _read_attempts_unlocked(settings)
        existing = [
            item for item in attempts
            if item.operation_id == operation_id and item.job_id == job_id
        ]
        if existing:
            return existing[0]
        operations = _read_operations_unlocked(settings)
        operation = next((item for item in operations if item.operation_id == operation_id), None)
        if operation is None or operation.job_id != job_id:
            raise UploadQueueError("試行台帳と投稿 operation の job ID が一致しません。")
        used = sum(item.attempt_date_la == date_la for item in attempts)
        if used >= settings.video_upload_daily_limit:
            raise UploadQueueError("本日の YouTube upload 試行上限に達しました。")
        attempt = UploadAttempt(
            attempt_date_la=date_la, operation_id=operation_id,
            job_id=job_id, attempted_at=attempted_at,
        )
        attempts.append(attempt)
        _write_attempts_unlocked(settings, attempts)
        return attempt


def recover_upload_operations(settings: Settings) -> tuple[UploadOperation, ...]:
    """close_orphans 後、attempt を正本に active operation を安全側へ倒す."""
    with schedule_lock(settings):
        operations = _read_operations_unlocked(settings)
        try:
            attempts = _read_attempts_unlocked(settings)
        except UploadQueueError:
            changed: list[UploadOperation] = []
            timestamp = datetime.now(timezone.utc)
            for index, operation in enumerate(operations):
                if operation.state not in {"reserved", "uploading"}:
                    continue
                operation_timestamp = max(timestamp, operation.updated_at)
                values = operation.model_dump()
                values.update(
                    state="needs_reconciliation",
                    updated_at=operation_timestamp,
                    finished_at=operation_timestamp,
                    error="アップロード試行台帳が壊れているため、手動照合が必要です。",
                )
                replacement = UploadOperation.model_validate(values)
                operations[index] = replacement
                changed.append(replacement)
            if changed:
                _write_operations_unlocked(settings, operations)
            raise UploadQueueError(
                "アップロード試行台帳が壊れています。active operation を照合待ちにし、新規投稿を停止しました。"
            )

        attempt_by_operation: dict[str, list[UploadAttempt]] = {}
        operation_by_id = {item.operation_id: item for item in operations}
        for attempt in attempts:
            operation = operation_by_id.get(attempt.operation_id)
            if operation is None:
                raise UploadQueueError(
                    "試行台帳と投稿キューが不整合です。データを変更せず新規投稿を停止しました。"
                )
            attempt_by_operation.setdefault(attempt.operation_id, []).append(attempt)
        for operation in operations:
            operation_attempts = attempt_by_operation.get(operation.operation_id, [])
            has_attempt = bool(operation_attempts)
            wrong_job = any(item.job_id != operation.job_id for item in operation_attempts)
            if wrong_job and operation.state not in {"reserved", "uploading"}:
                raise UploadQueueError(
                    "terminal operation と試行台帳の job ID が不整合です。データを変更せず新規投稿を停止しました。"
                )
            if operation.state in {"uploaded", "needs_reconciliation"} and not has_attempt:
                raise UploadQueueError(
                    "terminal operation と試行台帳が不整合です。データを変更せず新規投稿を停止しました。"
                )

        changed = []
        timestamp = datetime.now(timezone.utc)
        for index, operation in enumerate(operations):
            if operation.state not in {"reserved", "uploading"}:
                continue
            operation_attempts = attempt_by_operation.get(operation.operation_id, [])
            wrong_job = any(item.job_id != operation.job_id for item in operation_attempts)
            if wrong_job:
                state = "needs_reconciliation"
                message = "投稿 operation と試行台帳の job ID が一致しないため、手動照合が必要です。"
            elif not operation_attempts:
                state = "failed"
                message = "アップロード session 開始前に処理が中断されました。新しく確認してください。"
            else:
                state = "needs_reconciliation"
                message = "アップロード開始後に処理が中断されました。自動再送せず手動照合してください。"
            operation_timestamp = max(timestamp, operation.updated_at)
            values = operation.model_dump()
            values.update(
                state=state,
                updated_at=operation_timestamp,
                finished_at=operation_timestamp,
                error=message,
            )
            replacement = UploadOperation.model_validate(values)
            operations[index] = replacement
            changed.append(replacement)
        if changed:
            _write_operations_unlocked(settings, operations)
        return tuple(changed)


def upload_job_target(
    *, report, settings: Settings, job_id: str, operation_id: str
) -> None:
    """reserved operation を一度だけ安全に upload する jobs target."""
    _safe_identifier(job_id, "ジョブ ID")
    _safe_identifier(operation_id, "投稿 operation ID")
    operation = load_operation(operation_id, settings)
    if operation.job_id != job_id:
        raise UploadQueueError("投稿 operation とジョブ ID が一致しません。")
    if operation.state != "reserved":
        if operation.state == "uploaded":
            from yt_live_kit.services.jobs import update_job
            update_job(job_id, settings=settings, result_ref=operation_id)
            return
        raise UploadQueueError(
            "この投稿 operation は既に開始済みです。自動再送せず状態を手動確認してください。"
        )

    report(stage="preflight", message="投稿内容を再確認しています")
    try:
        actual_channel = fetch_mine_channel(settings)
        if actual_channel != operation.content.channel:
            raise YouTubeAPIError(
                "確認後に投稿先チャンネルが変わりました。新しく確認してください。"
            )
        validate_snapshot_identity(operation.content, settings)
        build_upload_body(operation.content)
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, YouTubeAPIError)
            else "投稿内容の再確認に失敗しました。新しく確認してください。"
        )
        transition_operation(
            operation_id, "failed", settings,
            error=message,
        )
        raise UploadQueueError(message) from exc

    transition_operation(operation_id, "uploading", settings)
    try:
        record_upload_attempt(operation_id, job_id, settings)
    except UploadQueueError as exc:
        try:
            attempt_exists = has_upload_attempt(operation_id, job_id, settings)
        except UploadQueueError:
            attempt_exists = True  # 有無を確定できない場合は自動再送禁止。
        state = "needs_reconciliation" if attempt_exists else "failed"
        message = (
            "試行台帳を安全に確認できないため upload を開始していません。手動確認が必要です。"
            if attempt_exists
            else "upload session 開始前に試行を記録できなかったため投稿を中止しました。新しく確認してください。"
        )
        transition_operation(
            operation_id, state, settings,
            error=f"{message}詳細: {exc}",
        )
        raise
    report(stage="upload", message="YouTube へ非公開アップロードしています")
    try:
        result: UploadResult = upload_video_resumable(operation.content, settings)
    except Exception as exc:
        message = (
            "upload attempt 記録後にアップロード結果を確定できませんでした。"
            "自動再送せず YouTube Studio で手動照合してください。"
        )
        transition_operation(
            operation_id,
            "needs_reconciliation",
            settings,
            error=f"{message}詳細: {exc}",
        )
        raise UploadQueueError(message) from exc
    if result.state != "uploaded":
        transition_operation(
            operation_id,
            "needs_reconciliation",
            settings,
            error=result.error or "アップロード結果を手動照合してください。",
        )
        raise UploadQueueError(result.error or "アップロード結果を確認できません。")

    uploaded = transition_operation(
        operation_id, "uploaded", settings,
        video_id=result.video_id,
    )
    from yt_live_kit.services.jobs import update_job
    update_job(job_id, settings=settings, result_ref=operation_id)
    report(stage="processing", message="YouTube 側の動画処理を確認しています")
    try:
        poll_processing_status(
            uploaded.video_id or "",
            settings,
            on_observation=lambda item: append_poll_observation(
                operation_id, item, settings
            ),
        )
    except YouTubeAPIError as exc:
        raise UploadQueueError(
            "アップロードは完了しましたが YouTube 側の処理状態を確認できませんでした。"
            "operation の動画 ID を YouTube Studio で手動確認してください。"
        ) from exc
