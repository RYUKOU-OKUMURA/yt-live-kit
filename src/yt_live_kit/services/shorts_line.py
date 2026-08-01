"""ショート生産ラインの安全状態、fingerprint、工程判定を扱う。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from yt_live_kit.config import Settings
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.models.upload import UploadOperation, UploadState
from yt_live_kit.services.schedule import SchedulePolicy

_SCHEMA_VERSION = 1
_WRITE_LOCK = threading.RLock()
_COMPLETED_UPLOAD_STATES = frozenset({"reserved", "uploading", "uploaded"})
_ATTENTION_UPLOAD_STATES = frozenset({"failed", "needs_reconciliation"})


class LineStateError(Exception):
    """ライン状態を安全に検証、保存、復元できないエラー。"""


class LineStage(str, Enum):
    """ショート 1 本の 6 工程と完了状態。"""

    MATERIAL_SELECTION = "material_selection"
    SEGMENT_DECISION = "segment_decision"
    TELOP_REVIEW = "telop_review"
    GENERATION = "generation"
    FINAL_REVIEW = "final_review"
    RESERVATION = "reservation"
    RESERVED = "reserved"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label}はタイムゾーン付き日時にしてください。")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | None, label: str) -> datetime:
    try:
        return _utc(value or datetime.now(timezone.utc), label)
    except ValueError as exc:
        raise LineStateError(str(exc)) from exc


def _safe_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise LineStateError(f"{label}が正しくありません。")
    if any(character in value for character in ("/", "\\", "\x00")):
        raise LineStateError(f"{label}にパスは指定できません。")
    return value


def _validate_digest(value: str | None, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise LineStateError(f"{label}が正しくありません。")
    try:
        int(value, 16)
    except ValueError as exc:
        raise LineStateError(f"{label}が正しくありません。") from exc
    return value.lower()


class LineState(_FrozenModel):
    """video と clip ごとの永続ライン状態。"""

    schema_version: Literal[1] = _SCHEMA_VERSION
    video_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    queue_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    review_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    review_confirmed_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    review_confirmed_at: datetime | None = None
    output_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    preview_confirmed_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    preview_confirmed_at: datetime | None = None
    current_stage: LineStage
    upload_operation_id: str | None = None
    updated_at: datetime

    @field_validator("video_id", "clip_id")
    @classmethod
    def _identifier(cls, value: str, info) -> str:
        try:
            return _safe_identifier(value, "動画 ID" if info.field_name == "video_id" else "clip ID")
        except LineStateError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("review_confirmed_at", "preview_confirmed_at", "updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None, info) -> datetime | None:
        return None if value is None else _utc(value, info.field_name)

    @field_validator("upload_operation_id")
    @classmethod
    def _operation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("投稿 operation ID が正しくありません。")
        return cleaned

    @model_validator(mode="after")
    def _consistent_confirmations(self) -> LineState:
        review_pair = (self.review_confirmed_fingerprint, self.review_confirmed_at)
        if (review_pair[0] is None) != (review_pair[1] is None):
            raise ValueError("台本確認 fingerprint と確認日時は両方必要です。")
        if (
            self.review_confirmed_fingerprint is not None
            and self.review_confirmed_fingerprint != self.review_fingerprint
        ):
            raise ValueError("台本確認が現在の review fingerprint と一致しません。")

        preview_pair = (self.preview_confirmed_fingerprint, self.preview_confirmed_at)
        if (preview_pair[0] is None) != (preview_pair[1] is None):
            raise ValueError("最終確認 fingerprint と確認日時は両方必要です。")
        if (
            self.preview_confirmed_fingerprint is not None
            and self.preview_confirmed_fingerprint != self.output_fingerprint
        ):
            raise ValueError("最終確認が現在の output fingerprint と一致しません。")
        if self.output_fingerprint is None and self.preview_confirmed_fingerprint is not None:
            raise ValueError("出力が無い状態で最終確認済みにはできません。")
        if self.output_fingerprint is not None and self.review_fingerprint is None:
            raise ValueError("output fingerprint には生成時の review fingerprint が必要です。")
        if self.current_stage == LineStage.RESERVED and self.upload_operation_id is None:
            raise ValueError("予約完了状態には投稿 operation ID が必要です。")
        return self


class ActiveLinePointer(_FrozenModel):
    """動画内で明示選択された未完了ラインへの pointer。"""

    schema_version: Literal[1] = _SCHEMA_VERSION
    clip_id: str = Field(min_length=1)
    updated_at: datetime

    @field_validator("clip_id")
    @classmethod
    def _clip_identifier(cls, value: str) -> str:
        try:
            return _safe_identifier(value, "clip ID")
        except LineStateError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("updated_at")
    @classmethod
    def _updated_at_utc(cls, value: datetime) -> datetime:
        return _utc(value, "更新日時")


class TelopGateStatus(_FrozenModel):
    """ゲート 2 の 4 分離を副作用なく表す。"""

    hard_valid: bool
    hard_errors: tuple[str, ...]
    warnings: tuple[str, ...]
    human_confirmed: bool
    fingerprint_current: bool
    can_generate: bool


class DailyLineSummary(_FrozenModel):
    """現在の schedule timezone における当日ライン集計。"""

    completed_count: int = Field(ge=0)
    needs_attention_count: int = Field(ge=0)
    target_count: int = 3


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_review_fingerprint(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    document: TelopScriptDocument,
) -> str:
    """queue snapshot と現在の台本を分離した canonical hash を返す。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    queue_fingerprint = _validate_digest(queue_fingerprint, "queue fingerprint")  # type: ignore[assignment]
    if not isinstance(document, TelopScriptDocument):
        raise LineStateError("テロップ台本の入力が正しくありません。")
    return _canonical_digest(
        {
            "video_id": video_id,
            "clip_id": clip_id,
            "queue_fingerprint": queue_fingerprint,
            "telop_document": document.model_dump(mode="json"),
        }
    )


def make_output_fingerprint(
    video_id: str,
    clip_id: str,
    review_fingerprint: str,
    output_path: Path,
) -> str:
    """解決済み path、stat、mp4 内容を結ぶ canonical hash を返す。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    review_fingerprint = _validate_digest(review_fingerprint, "review fingerprint")  # type: ignore[assignment]
    try:
        resolved = Path(output_path).resolve(strict=True)
        if not resolved.is_file():
            raise LineStateError("ショート出力が通常ファイルではありません。")
        before = resolved.stat()
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = resolved.stat()
    except LineStateError:
        raise
    except (OSError, RuntimeError) as exc:
        raise LineStateError(
            "ショート出力を読み込めないため fingerprint を計算できません。"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise LineStateError(
            "ショート出力が読み込み中に変更されました。もう一度確認してください。"
        )
    return _canonical_digest(
        {
            "video_id": video_id,
            "clip_id": clip_id,
            "review_fingerprint": review_fingerprint,
            "resolved_path": str(resolved),
            "st_size": after.st_size,
            "st_mtime_ns": after.st_mtime_ns,
            "content_sha256": digest.hexdigest(),
        }
    )


def evaluate_telop_gate(
    hard_errors: Sequence[str],
    warnings: Sequence[str],
    current_review_fingerprint: str | None,
    confirmed_review_fingerprint: str | None,
) -> TelopGateStatus:
    """ハード判定、警告、人確認、生成条件を独立して返す。"""
    current = _validate_digest(
        current_review_fingerprint, "review fingerprint", optional=True
    )
    confirmed = _validate_digest(
        confirmed_review_fingerprint, "確認済み review fingerprint", optional=True
    )
    errors = tuple(str(item) for item in hard_errors)
    warning_values = tuple(str(item) for item in warnings)
    hard_valid = not errors
    human_confirmed = confirmed is not None
    fingerprint_current = current is not None and confirmed == current
    return TelopGateStatus(
        hard_valid=hard_valid,
        hard_errors=errors,
        warnings=warning_values,
        human_confirmed=human_confirmed,
        fingerprint_current=fingerprint_current,
        can_generate=hard_valid and human_confirmed and fingerprint_current,
    )


def calculate_line_stage(
    *,
    material_selected: bool,
    segments_confirmed: bool,
    telop_gate: TelopGateStatus,
    output_available: bool,
    output_fingerprint_current: bool,
    preview_confirmed: bool,
    upload_state: UploadState | None = None,
) -> LineStage:
    """機械 evidence と人確認から 6 工程または完了状態を決定する。"""
    if upload_state in _COMPLETED_UPLOAD_STATES:
        return LineStage.RESERVED
    if upload_state in _ATTENTION_UPLOAD_STATES:
        return LineStage.RESERVATION
    if not material_selected:
        return LineStage.MATERIAL_SELECTION
    if not segments_confirmed:
        return LineStage.SEGMENT_DECISION
    if not telop_gate.can_generate:
        return LineStage.TELOP_REVIEW
    if not output_available or not output_fingerprint_current:
        return LineStage.GENERATION
    if not preview_confirmed:
        return LineStage.FINAL_REVIEW
    return LineStage.RESERVATION


def _replace_state(state: LineState, **updates: object) -> LineState:
    values = state.model_dump(mode="python")
    values.update(updates)
    try:
        return LineState.model_validate(values)
    except ValidationError as exc:
        raise LineStateError("ライン状態を安全に更新できませんでした。") from exc


def create_line_state(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    *,
    review_fingerprint: str | None = None,
    now: datetime | None = None,
) -> LineState:
    """区間確定後の未確認ライン状態を作る。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    queue_fingerprint = _validate_digest(queue_fingerprint, "queue fingerprint")  # type: ignore[assignment]
    review_fingerprint = _validate_digest(
        review_fingerprint, "review fingerprint", optional=True
    )
    try:
        return LineState(
            video_id=video_id,
            clip_id=clip_id,
            queue_fingerprint=queue_fingerprint,
            review_fingerprint=review_fingerprint,
            current_stage=LineStage.TELOP_REVIEW,
            updated_at=_timestamp(now, "更新日時"),
        )
    except ValidationError as exc:
        raise LineStateError("ライン状態の入力が正しくありません。") from exc


def _ensure_not_reserved(state: LineState) -> None:
    if state.current_stage == LineStage.RESERVED:
        raise LineStateError("予約完了したラインは編集できません。")


def set_review_fingerprint(
    state: LineState,
    review_fingerprint: str | None,
    *,
    now: datetime | None = None,
) -> LineState:
    """台本変更を記録し、変更時は確認と旧出力の結び付きを失効させる。"""
    _ensure_not_reserved(state)
    current = _validate_digest(review_fingerprint, "review fingerprint", optional=True)
    if current == state.review_fingerprint:
        return state
    return _replace_state(
        state,
        review_fingerprint=current,
        review_confirmed_fingerprint=None,
        review_confirmed_at=None,
        output_fingerprint=None,
        preview_confirmed_fingerprint=None,
        preview_confirmed_at=None,
        current_stage=LineStage.TELOP_REVIEW,
        updated_at=_timestamp(now, "更新日時"),
    )


def confirm_review(
    state: LineState,
    current_review_fingerprint: str,
    *,
    hard_errors: Sequence[str] = (),
    now: datetime | None = None,
) -> LineState:
    """現在値のハード判定通過後だけ、人の全文確認を記録する。"""
    _ensure_not_reserved(state)
    current = _validate_digest(current_review_fingerprint, "review fingerprint")
    gate = evaluate_telop_gate(hard_errors, (), current, current)
    if state.review_fingerprint != current:
        raise LineStateError("台本が変更されています。現在の内容をもう一度確認してください。")
    if not gate.hard_valid:
        raise LineStateError("台本の自動ハード判定を通過していないため確認できません。")
    timestamp = _timestamp(now, "台本確認日時")
    return _replace_state(
        state,
        review_confirmed_fingerprint=current,
        review_confirmed_at=timestamp,
        current_stage=LineStage.GENERATION,
        updated_at=timestamp,
    )


def record_output(
    state: LineState,
    output_path: Path,
    *,
    now: datetime | None = None,
) -> LineState:
    """確認済み review から生成された出力を記録する。"""
    _ensure_not_reserved(state)
    if (
        state.review_fingerprint is None
        or state.review_confirmed_fingerprint != state.review_fingerprint
    ):
        raise LineStateError("現在の台本が全文確認されていないため出力を記録できません。")
    output = make_output_fingerprint(
        state.video_id,
        state.clip_id,
        state.review_fingerprint,
        output_path,
    )
    timestamp = _timestamp(now, "出力更新日時")
    return _replace_state(
        state,
        output_fingerprint=output,
        preview_confirmed_fingerprint=None,
        preview_confirmed_at=None,
        current_stage=LineStage.FINAL_REVIEW,
        updated_at=timestamp,
    )


def reconcile_output(
    state: LineState,
    output_path: Path,
    *,
    now: datetime | None = None,
) -> LineState:
    """出力変更・欠損で最終確認だけを fail closed に失効させる。"""
    _ensure_not_reserved(state)
    if state.review_fingerprint is None:
        return state
    timestamp = _timestamp(now, "出力確認日時")
    try:
        current = make_output_fingerprint(
            state.video_id,
            state.clip_id,
            state.review_fingerprint,
            output_path,
        )
    except LineStateError:
        if Path(output_path).exists():
            raise
        next_stage = (
            LineStage.GENERATION
            if state.review_confirmed_fingerprint == state.review_fingerprint
            else LineStage.TELOP_REVIEW
        )
        return _replace_state(
            state,
            output_fingerprint=None,
            preview_confirmed_fingerprint=None,
            preview_confirmed_at=None,
            current_stage=next_stage,
            updated_at=timestamp,
        )
    if state.output_fingerprint is None:
        return state
    if current == state.output_fingerprint:
        return state
    return _replace_state(
        state,
        output_fingerprint=current,
        preview_confirmed_fingerprint=None,
        preview_confirmed_at=None,
        current_stage=LineStage.FINAL_REVIEW,
        updated_at=timestamp,
    )


def confirm_preview(
    state: LineState,
    output_path: Path,
    *,
    now: datetime | None = None,
) -> LineState:
    """表示後も同じ出力である場合だけ最終プレビュー確認を記録する。"""
    _ensure_not_reserved(state)
    if state.output_fingerprint is None or state.review_fingerprint is None:
        raise LineStateError("確認できるショート出力がありません。")
    current = make_output_fingerprint(
        state.video_id,
        state.clip_id,
        state.review_fingerprint,
        output_path,
    )
    if current != state.output_fingerprint:
        raise LineStateError("表示後にショート出力が変更されました。もう一度確認してください。")
    timestamp = _timestamp(now, "最終確認日時")
    return _replace_state(
        state,
        preview_confirmed_fingerprint=current,
        preview_confirmed_at=timestamp,
        current_stage=LineStage.RESERVATION,
        updated_at=timestamp,
    )


def record_upload_operation(
    state: LineState,
    operation_id: str,
    *,
    now: datetime | None = None,
) -> LineState:
    """最終確認済みラインへ予約 operation を結び付けて完了にする。"""
    _ensure_not_reserved(state)
    if (
        state.output_fingerprint is None
        or state.preview_confirmed_fingerprint != state.output_fingerprint
    ):
        raise LineStateError("完成動画の最終確認が済んでいないため予約完了にできません。")
    operation_id = operation_id.strip() if isinstance(operation_id, str) else ""
    if not operation_id:
        raise LineStateError("投稿 operation ID が正しくありません。")
    timestamp = _timestamp(now, "予約更新日時")
    return _replace_state(
        state,
        upload_operation_id=operation_id,
        current_stage=LineStage.RESERVED,
        updated_at=timestamp,
    )


def line_state_path(video_id: str, clip_id: str, settings: Settings) -> Path:
    return (
        settings.data_dir
        / _safe_identifier(video_id, "動画 ID")
        / "shorts"
        / "line"
        / f"line_{_safe_identifier(clip_id, 'clip ID')}.json"
    )


def _active_line_path(video_id: str, settings: Settings) -> Path:
    return (
        settings.data_dir
        / _safe_identifier(video_id, "動画 ID")
        / "shorts"
        / "line"
        / "active_line.json"
    )


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise LineStateError(
            "ショート生産ラインの状態を安全に保存できませんでした。"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def save_line_state(state: LineState, settings: Settings) -> Path:
    """ライン状態を同一ディレクトリ内の一時ファイルから atomic 保存する。"""
    if not isinstance(state, LineState):
        raise LineStateError("保存するライン状態が正しくありません。")
    path = line_state_path(state.video_id, state.clip_id, settings)
    with _WRITE_LOCK:
        _atomic_write(path, state.model_dump(mode="json"))
    return path


def load_line_state(
    video_id: str,
    clip_id: str,
    settings: Settings,
) -> LineState | None:
    """欠落は None、破損・identity 不一致は fail closed error にする。"""
    path = line_state_path(video_id, clip_id, settings)
    if not path.exists():
        return None
    try:
        state = LineState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise LineStateError(
            "ショート生産ラインの状態が壊れているため安全に復元できません。"
        ) from exc
    if state.video_id != video_id or state.clip_id != clip_id:
        raise LineStateError(
            "ショート生産ラインの対象が保存先と一致しないため復元できません。"
        )
    return state


def save_active_line(
    video_id: str,
    clip_id: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> Path:
    """保存済みの未完了ラインだけを明示 active pointer にする。"""
    state = load_line_state(video_id, clip_id, settings)
    if state is None:
        raise LineStateError("選択するショート生産ラインが保存されていません。")
    if state.current_stage == LineStage.RESERVED:
        raise LineStateError("予約完了したラインを作成中として選択できません。")
    pointer = ActiveLinePointer(clip_id=clip_id, updated_at=_timestamp(now, "選択更新日時"))
    path = _active_line_path(video_id, settings)
    with _WRITE_LOCK:
        _atomic_write(path, pointer.model_dump(mode="json"))
    return path


def _load_active_pointer(video_id: str, settings: Settings) -> ActiveLinePointer | None:
    path = _active_line_path(video_id, settings)
    if not path.exists():
        return None
    try:
        return ActiveLinePointer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        return None


def _valid_line_states(video_id: str, settings: Settings) -> tuple[LineState, ...]:
    directory = _active_line_path(video_id, settings).parent
    if not directory.exists():
        return ()
    states: list[LineState] = []
    for path in directory.glob("line_*.json"):
        clip_id = path.stem.removeprefix("line_")
        try:
            state = load_line_state(video_id, clip_id, settings)
        except LineStateError:
            continue
        if state is not None:
            states.append(state)
    return tuple(states)


def resolve_active_line(video_id: str, settings: Settings) -> LineState | None:
    """pointer 優先、無効時は非完了ラインから決定的に 1 件復元する。"""
    _safe_identifier(video_id, "動画 ID")
    pointer = _load_active_pointer(video_id, settings)
    if pointer is not None:
        try:
            pointed = load_line_state(video_id, pointer.clip_id, settings)
        except LineStateError:
            pointed = None
        if pointed is not None and pointed.current_stage != LineStage.RESERVED:
            return pointed
    unfinished = [
        state
        for state in _valid_line_states(video_id, settings)
        if state.current_stage != LineStage.RESERVED
    ]
    if not unfinished:
        return None
    return sorted(
        unfinished,
        key=lambda state: (-state.updated_at.timestamp(), state.clip_id),
    )[0]


def recover_line_state(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    *,
    review_fingerprint: str | None = None,
    output_fingerprint: str | None = None,
    upload_operation_id: str | None = None,
    now: datetime | None = None,
) -> LineState:
    """機械的に証明済みの evidence だけから、人確認を外して再構成する。"""
    state = create_line_state(
        video_id,
        clip_id,
        queue_fingerprint,
        review_fingerprint=review_fingerprint,
        now=now,
    )
    output = _validate_digest(output_fingerprint, "output fingerprint", optional=True)
    operation_id = (
        upload_operation_id.strip()
        if isinstance(upload_operation_id, str) and upload_operation_id.strip()
        else None
    )
    stage = LineStage.RESERVED if operation_id is not None else LineStage.TELOP_REVIEW
    return _replace_state(
        state,
        output_fingerprint=output,
        review_confirmed_fingerprint=None,
        review_confirmed_at=None,
        preview_confirmed_fingerprint=None,
        preview_confirmed_at=None,
        upload_operation_id=operation_id,
        current_stage=stage,
    )


def summarize_daily_lines(
    operations: Iterable[UploadOperation],
    policy: SchedulePolicy,
    *,
    now: datetime,
) -> DailyLineSummary:
    """policy timezone の当日最新 operation を source key ごとに集計する。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise LineStateError("日次集計の現在日時はタイムゾーン付きで指定してください。")
    if not isinstance(policy, SchedulePolicy):
        raise LineStateError("投稿スケジュール設定が正しくありません。")
    zone = ZoneInfo(policy.timezone)
    target_date = now.astimezone(zone).date()
    latest: dict[tuple[str, str, str], UploadOperation] = {}
    for operation in operations:
        if not isinstance(operation, UploadOperation):
            raise LineStateError("投稿 operation の入力が正しくありません。")
        if operation.created_at.astimezone(zone).date() != target_date:
            continue
        key = (operation.source_video_id, operation.source_kind, operation.clip_id)
        previous = latest.get(key)
        if previous is None or (operation.created_at, operation.operation_id) > (
            previous.created_at,
            previous.operation_id,
        ):
            latest[key] = operation
    return DailyLineSummary(
        completed_count=sum(
            item.state in _COMPLETED_UPLOAD_STATES for item in latest.values()
        ),
        needs_attention_count=sum(
            item.state in _ATTENTION_UPLOAD_STATES for item in latest.values()
        ),
    )
