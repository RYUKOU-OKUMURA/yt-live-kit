"""ショート生産ラインの安全状態、fingerprint、工程判定を扱う。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Literal
from zoneinfo import ZoneInfo

import fcntl

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from yt_live_kit.config import Settings
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.models.transcript import TranscriptArtifactRef
from yt_live_kit.models.upload import UploadOperation, UploadState
from yt_live_kit.services.schedule import SchedulePolicy
from yt_live_kit.services.transcript_artifact import TranscriptArtifactError, TranscriptArtifactStore
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_video_path,
    safe_identifier,
    validate_confined_candidate,
)

_SCHEMA_VERSION = 2
_WRITE_LOCK = threading.RLock()
_COMPLETED_UPLOAD_STATES = frozenset({"reserved", "uploading", "uploaded"})
_ATTENTION_UPLOAD_STATES = frozenset({"failed", "needs_reconciliation"})


class LineStateError(Exception):
    """ライン状態を安全に検証、保存、復元できないエラー。"""


class LineReservationStartedError(LineStateError):
    """外部投稿開始後にライン記録だけが失敗した状態。"""

    def __init__(self, message: str, operation: UploadOperation) -> None:
        super().__init__(message)
        self.operation = operation


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
    try:
        return safe_identifier(value, label)
    except PathConfinementError as exc:
        raise LineStateError(str(exc)) from exc


def _confined_output_path(output_path: Path, settings: Settings) -> Path:
    try:
        return validate_confined_candidate(
            settings.data_dir, output_path, label="ショート出力保存先"
        )
    except PathConfinementError as exc:
        raise LineStateError(str(exc)) from exc


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


def _validate_artifact_lineage(
    artifact_ref: TranscriptArtifactRef | None,
    artifact_fingerprint: str | None,
    used_range_cue_digests: Sequence[str],
) -> tuple[TranscriptArtifactRef | None, str | None, tuple[str, ...]]:
    """line state に保存する artifact provenance を一組として検証する."""
    if artifact_ref is None and (artifact_fingerprint is not None or used_range_cue_digests):
        raise LineStateError("artifact lineage が不完全です。")
    if artifact_ref is None:
        return None, None, ()
    if artifact_fingerprint is None:
        raise LineStateError("artifact lineage に fingerprint がありません。")
    if not used_range_cue_digests:
        raise LineStateError("artifact lineage には used_range_cue_digest が 1 件以上必要です。")
    fingerprint = _validate_digest(artifact_fingerprint, "artifact fingerprint")
    if fingerprint != artifact_ref.artifact_fingerprint:
        raise LineStateError("artifact fingerprint が artifact reference と一致しません。")
    digests: list[str] = []
    for digest in used_range_cue_digests:
        digests.append(_validate_digest(digest, "used_range_cue_digest") or "")
    return artifact_ref, fingerprint, tuple(digests)


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _material_context_digest(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    context: Mapping[str, object],
) -> str:
    return _canonical_digest(
        {
            "video_id": video_id,
            "clip_id": clip_id,
            "queue_fingerprint": queue_fingerprint,
            "material_context": context,
        }
    )


def _generation_spec_digest(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    review_fingerprint: str,
    spec: Mapping[str, object],
) -> str:
    return _canonical_digest(
        {
            "video_id": video_id,
            "clip_id": clip_id,
            "queue_fingerprint": queue_fingerprint,
            "review_fingerprint": review_fingerprint,
            "generation_spec": spec,
        }
    )


class LineState(_FrozenModel):
    """video と clip ごとの永続ライン状態。"""

    schema_version: Literal[1, 2] = _SCHEMA_VERSION
    video_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    queue_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    artifact_ref: TranscriptArtifactRef | None = None
    artifact_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    used_range_cue_digests: tuple[str, ...] = ()
    review_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    review_confirmed_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    review_confirmed_at: datetime | None = None
    material_context_json: str | None = None
    material_context_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    generation_spec_json: str | None = None
    generation_spec_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
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

    @field_validator("used_range_cue_digests", mode="before")
    @classmethod
    def _digests_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("used_range_cue_digests は配列で指定してください。")
        return tuple(value)

    @model_validator(mode="after")
    def _consistent_confirmations(self) -> LineState:
        if self.artifact_ref is None and (
            self.artifact_fingerprint is not None or self.used_range_cue_digests
        ):
            raise ValueError("artifact lineage が不完全です。")
        if self.artifact_ref is not None:
            if self.artifact_fingerprint != self.artifact_ref.artifact_fingerprint:
                raise ValueError("artifact fingerprint が artifact reference と一致しません。")
            if not self.used_range_cue_digests:
                raise ValueError("artifact lineage には used_range_cue_digest が 1 件以上必要です。")
            for digest in self.used_range_cue_digests:
                if not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError("used_range_cue_digest が正しくありません。")
                try:
                    int(digest, 16)
                except ValueError as exc:
                    raise ValueError("used_range_cue_digest が正しくありません。") from exc
        review_pair = (self.review_confirmed_fingerprint, self.review_confirmed_at)
        if (review_pair[0] is None) != (review_pair[1] is None):
            raise ValueError("台本確認 fingerprint と確認日時は両方必要です。")
        if (
            self.review_confirmed_fingerprint is not None
            and self.review_confirmed_fingerprint != self.review_fingerprint
        ):
            raise ValueError("台本確認が現在の review fingerprint と一致しません。")

        material_pair = (
            self.material_context_json,
            self.material_context_fingerprint,
        )
        if (material_pair[0] is None) != (material_pair[1] is None):
            raise ValueError("素材 context と fingerprint は両方必要です。")
        if material_pair[0] is not None:
            try:
                material = json.loads(material_pair[0])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("素材 context を読み込めません。") from exc
            if not isinstance(material, dict) or _canonical_json(material) != material_pair[0]:
                raise ValueError("素材 context が canonical JSON ではありません。")
            if material_pair[1] != _material_context_digest(
                self.video_id,
                self.clip_id,
                self.queue_fingerprint,
                material,
            ):
                raise ValueError("素材 context が queue fingerprint と一致しません。")

        generation_pair = (
            self.generation_spec_json,
            self.generation_spec_fingerprint,
        )
        if (generation_pair[0] is None) != (generation_pair[1] is None):
            raise ValueError("生成 spec と fingerprint は両方必要です。")
        if generation_pair[0] is not None:
            if self.review_fingerprint is None:
                raise ValueError("生成 spec には review fingerprint が必要です。")
            try:
                generation_spec = json.loads(generation_pair[0])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("生成 spec を読み込めません。") from exc
            if (
                not isinstance(generation_spec, dict)
                or _canonical_json(generation_spec) != generation_pair[0]
            ):
                raise ValueError("生成 spec が canonical JSON ではありません。")
            if generation_pair[1] != _generation_spec_digest(
                self.video_id,
                self.clip_id,
                self.queue_fingerprint,
                self.review_fingerprint,
                generation_spec,
            ):
                raise ValueError("生成 spec が現在のライン証跡と一致しません。")

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
        if self.output_fingerprint is not None and self.generation_spec_fingerprint is None:
            raise ValueError("output fingerprint には生成 spec の証跡が必要です。")
        review_is_current = (
            self.review_fingerprint is not None
            and self.review_confirmed_fingerprint == self.review_fingerprint
        )
        output_is_available = self.output_fingerprint is not None
        preview_is_current = (
            self.output_fingerprint is not None
            and self.preview_confirmed_fingerprint == self.output_fingerprint
        )
        if self.current_stage in {
            LineStage.GENERATION,
            LineStage.FINAL_REVIEW,
            LineStage.RESERVATION,
        } and not review_is_current:
            raise ValueError("現在工程に必要な台本確認がありません。")
        if self.current_stage in {
            LineStage.FINAL_REVIEW,
            LineStage.RESERVATION,
        } and not output_is_available:
            raise ValueError("現在工程に必要なショート出力がありません。")
        if self.current_stage == LineStage.RESERVATION and not preview_is_current:
            raise ValueError("予約工程に必要な最終確認がありません。")
        if self.current_stage == LineStage.RESERVED and self.upload_operation_id is None:
            raise ValueError("予約完了状態には投稿 operation ID が必要です。")
        if self.current_stage != LineStage.RESERVED and self.upload_operation_id is not None:
            raise ValueError("未完了のラインに投稿 operation ID は保存できません。")
        return self


class ActiveLinePointer(_FrozenModel):
    """動画内で明示選択された未完了ラインへの pointer。"""

    # pointer 自体の形式は変わらない。LineState の schema だけを上げる。
    schema_version: Literal[1] = 1
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


def make_review_fingerprint(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    document: TelopScriptDocument,
    *,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] | None = None,
) -> str:
    """queue snapshot と現在の台本を分離した canonical hash を返す。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    queue_fingerprint = _validate_digest(queue_fingerprint, "queue fingerprint")  # type: ignore[assignment]
    if not isinstance(document, TelopScriptDocument):
        raise LineStateError("テロップ台本の入力が正しくありません。")
    lineage_ref = artifact_ref or document.artifact_ref
    lineage_fingerprint = artifact_fingerprint or document.artifact_fingerprint
    lineage_digests = tuple(
        document.used_range_cue_digests
        if used_range_cue_digests is None
        else used_range_cue_digests
    )
    lineage_ref, lineage_fingerprint, lineage_digests = _validate_artifact_lineage(
        lineage_ref,
        lineage_fingerprint,
        lineage_digests,
    )
    if lineage_ref is not None and (
        document.artifact_ref != lineage_ref
        or document.artifact_fingerprint != lineage_fingerprint
        or tuple(document.used_range_cue_digests) != lineage_digests
    ):
        raise LineStateError("review fingerprint の artifact lineage が台本と一致しません。")
    document_payload = document.model_dump(mode="json")
    if lineage_ref is None:
        # 既存 VTT 台本の review fingerprint の意味を変えない。
        document_payload.pop("artifact_ref", None)
        document_payload.pop("artifact_fingerprint", None)
        document_payload.pop("used_range_cue_digests", None)
    payload: dict[str, object] = {
        "video_id": video_id,
        "clip_id": clip_id,
        "queue_fingerprint": queue_fingerprint,
        "telop_document": document_payload,
    }
    if lineage_ref is not None:
        payload["transcript_artifact"] = {
            "artifact_ref": lineage_ref.model_dump(mode="json"),
            "artifact_fingerprint": lineage_fingerprint,
            "used_range_cue_digests": list(lineage_digests),
        }
    return _canonical_digest(payload)


def make_material_context_fingerprint(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    context: Mapping[str, object],
) -> str:
    """素材・区間 evidence を exact queue snapshot へ結び付ける。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    queue = _validate_digest(queue_fingerprint, "queue fingerprint")
    if not isinstance(context, Mapping):
        raise LineStateError("素材 context が正しくありません。")
    return _material_context_digest(video_id, clip_id, queue, dict(context))  # type: ignore[arg-type]


def make_generation_spec_fingerprint(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    review_fingerprint: str,
    spec: Mapping[str, object],
) -> str:
    """full generation spec を queue・review lineage へ結び付ける。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    queue = _validate_digest(queue_fingerprint, "queue fingerprint")
    review = _validate_digest(review_fingerprint, "review fingerprint")
    if not isinstance(spec, Mapping):
        raise LineStateError("生成 spec が正しくありません。")
    return _generation_spec_digest(
        video_id,
        clip_id,
        queue,  # type: ignore[arg-type]
        review,  # type: ignore[arg-type]
        dict(spec),
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


def _invalidate_transcript_lineage(state: LineState) -> LineState:
    """artifact 欠損・内容不一致時に人確認と出力の再利用を止める."""
    updated_at = datetime.now(timezone.utc)
    if updated_at <= state.updated_at:
        updated_at = state.updated_at + timedelta(microseconds=1)
    return state.model_copy(
        update={
            "schema_version": _SCHEMA_VERSION,
            "review_fingerprint": None,
            "review_confirmed_fingerprint": None,
            "review_confirmed_at": None,
            "generation_spec_json": None,
            "generation_spec_fingerprint": None,
            "output_fingerprint": None,
            "preview_confirmed_fingerprint": None,
            "preview_confirmed_at": None,
            "upload_operation_id": None,
            "current_stage": LineStage.TELOP_REVIEW,
            "updated_at": updated_at,
        }
    )


def _state_timestamp(state: LineState, value: datetime | None, label: str) -> datetime:
    timestamp = _timestamp(value, label)
    if timestamp <= state.updated_at:
        raise LineStateError(f"{label}は現在の更新日時より後にしてください。")
    return timestamp


def create_line_state(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    *,
    review_fingerprint: str | None = None,
    material_context: Mapping[str, object] | None = None,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
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
        artifact_ref, artifact_fingerprint, used_range_cue_digests = _validate_artifact_lineage(
            artifact_ref,
            artifact_fingerprint,
            used_range_cue_digests,
        )
    except LineStateError:
        raise
    material_json: str | None = None
    material_fingerprint: str | None = None
    if material_context is not None:
        if not isinstance(material_context, Mapping):
            raise LineStateError("素材 context が正しくありません。")
        material = dict(material_context)
        material_json = _canonical_json(material)
        material_fingerprint = make_material_context_fingerprint(
            video_id,
            clip_id,
            queue_fingerprint,  # type: ignore[arg-type]
            material,
        )
    try:
        return LineState(
            video_id=video_id,
            clip_id=clip_id,
            queue_fingerprint=queue_fingerprint,
            artifact_ref=artifact_ref,
            artifact_fingerprint=artifact_fingerprint,
            used_range_cue_digests=used_range_cue_digests,
            review_fingerprint=review_fingerprint,
            material_context_json=material_json,
            material_context_fingerprint=material_fingerprint,
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
        generation_spec_json=None,
        generation_spec_fingerprint=None,
        output_fingerprint=None,
        preview_confirmed_fingerprint=None,
        preview_confirmed_at=None,
        current_stage=LineStage.TELOP_REVIEW,
        updated_at=_state_timestamp(state, now, "更新日時"),
    )


def set_generation_spec(
    state: LineState,
    current_review_fingerprint: str,
    spec: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> LineState:
    """人確認済み review に full generation spec の証跡を固定する。"""
    _ensure_not_reserved(state)
    review = _validate_digest(current_review_fingerprint, "review fingerprint")
    if (
        state.review_fingerprint != review
        or state.review_confirmed_fingerprint != review
    ):
        raise LineStateError("現在の台本が全文確認されていないため生成 spec を固定できません。")
    if not isinstance(spec, Mapping):
        raise LineStateError("生成 spec が正しくありません。")
    payload = dict(spec)
    spec_ref = payload.get("artifact_ref")
    spec_fingerprint = payload.get("artifact_fingerprint")
    spec_digests = payload.get("used_range_cue_digests")
    has_spec_lineage = any(
        item is not None for item in (spec_ref, spec_fingerprint, spec_digests)
    )
    if state.artifact_ref is None:
        if has_spec_lineage:
            raise LineStateError("生成 spec に state と異なる artifact lineage があります。")
    else:
        if not has_spec_lineage or not isinstance(spec_ref, Mapping):
            raise LineStateError("生成 spec に artifact lineage がありません。")
        if not isinstance(spec_digests, (list, tuple)):
            raise LineStateError("生成 spec の used_range_cue_digests が正しくありません。")
        if (
            dict(spec_ref) != state.artifact_ref.model_dump(mode="json")
            or spec_fingerprint != state.artifact_fingerprint
            or tuple(spec_digests) != tuple(state.used_range_cue_digests)
        ):
            raise LineStateError("生成 spec の artifact lineage が state と一致しません。")
    canonical = _canonical_json(payload)
    fingerprint = make_generation_spec_fingerprint(
        state.video_id,
        state.clip_id,
        state.queue_fingerprint,
        review,  # type: ignore[arg-type]
        payload,
    )
    if (
        state.generation_spec_json == canonical
        and state.generation_spec_fingerprint == fingerprint
    ):
        return state
    return _replace_state(
        state,
        generation_spec_json=canonical,
        generation_spec_fingerprint=fingerprint,
        output_fingerprint=None,
        preview_confirmed_fingerprint=None,
        preview_confirmed_at=None,
        current_stage=LineStage.GENERATION,
        updated_at=_state_timestamp(state, now, "生成 spec 更新日時"),
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
    timestamp = _state_timestamp(state, now, "台本確認日時")
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
    if state.generation_spec_fingerprint is None:
        raise LineStateError("生成 spec の証跡がないため出力を記録できません。")
    output = make_output_fingerprint(
        state.video_id,
        state.clip_id,
        state.review_fingerprint,
        output_path,
    )
    timestamp = _state_timestamp(state, now, "出力更新日時")
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
    timestamp = _state_timestamp(state, now, "出力確認日時")
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
    timestamp = _state_timestamp(state, now, "最終確認日時")
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
    output_path: Path | None = None,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> LineState:
    """工程 6 直前に再検証し、差分時は未確認状態を返して保存する。"""
    _ensure_not_reserved(state)
    if (
        state.output_fingerprint is None
        or state.preview_confirmed_fingerprint != state.output_fingerprint
    ):
        raise LineStateError("完成動画の最終確認が済んでいないため予約完了にできません。")
    operation_id = operation_id.strip() if isinstance(operation_id, str) else ""
    if not operation_id:
        raise LineStateError("投稿 operation ID が正しくありません。")
    if output_path is None or state.review_fingerprint is None:
        raise LineStateError("予約直前に完成動画を再確認できませんでした。")
    if settings is not None:
        output_path = _confined_output_path(output_path, settings)
    timestamp = _state_timestamp(state, now, "予約更新日時")
    try:
        checked = reconcile_output(state, output_path, now=timestamp)
    except LineStateError:
        checked = _replace_state(
            state,
            preview_confirmed_fingerprint=None,
            preview_confirmed_at=None,
            current_stage=LineStage.FINAL_REVIEW,
            updated_at=timestamp,
        )
    if (
        checked.output_fingerprint != state.output_fingerprint
        or checked.preview_confirmed_fingerprint != checked.output_fingerprint
    ):
        if settings is not None:
            save_line_state(checked, settings, expected_state=state)
        return checked
    completed = _replace_state(
        state,
        upload_operation_id=operation_id,
        current_stage=LineStage.RESERVED,
        updated_at=timestamp,
    )
    if settings is not None:
        save_line_state(completed, settings, expected_state=state)
    return completed


def line_state_path(video_id: str, clip_id: str, settings: Settings) -> Path:
    safe_video_id = _safe_identifier(video_id, "動画 ID")
    safe_clip_id = _safe_identifier(clip_id, "clip ID")
    try:
        return confined_video_path(
            settings.data_dir,
            safe_video_id,
            "shorts",
            "line",
            f"line_{safe_clip_id}.json",
            label="ショート生産ライン保存先",
        )
    except PathConfinementError as exc:
        raise LineStateError(str(exc)) from exc


def _active_line_path(video_id: str, settings: Settings) -> Path:
    safe_video_id = _safe_identifier(video_id, "動画 ID")
    try:
        return confined_video_path(
            settings.data_dir,
            safe_video_id,
            "shorts",
            "line",
            "active_line.json",
            label="ショート生産ライン保存先",
        )
    except PathConfinementError as exc:
        raise LineStateError(str(exc)) from exc


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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


def _file_snapshot(path: Path) -> bytes | None:
    """Capture one small JSON file for command-level rollback."""
    try:
        return path.read_bytes() if path.exists() else None
    except OSError as exc:
        raise LineStateError(
            "ショート生産ラインの以前の状態を確認できませんでした。"
        ) from exc


def _restore_file_snapshot(path: Path, snapshot: bytes | None) -> None:
    """Restore bytes without routing rollback through the failed writer."""
    if snapshot is None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise LineStateError(
                "ショート生産ラインの開始失敗を安全に巻き戻せませんでした。"
            ) from exc
        return

    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.rollback.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            handle.write(snapshot)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise LineStateError(
            "ショート生産ラインの開始失敗を安全に巻き戻せませんでした。"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _line_lock(video_id: str, settings: Settings) -> Iterator[None]:
    """同一動画の状態確認と atomic replace をプロセス間で直列化する。"""
    directory = _active_line_path(video_id, settings).parent
    lock_path = directory / ".line.lock"
    with _WRITE_LOCK:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except LineStateError:
            raise
        except OSError as exc:
            raise LineStateError(
                "ショート生産ラインの保存ロックを取得できませんでした。"
            ) from exc


def _read_line_state(path: Path, video_id: str, clip_id: str) -> LineState | None:
    """lock 内で既存 state を一度だけ parse する."""
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


def _artifact_lineage_is_current(
    state: LineState,
    settings: Settings,
) -> bool:
    """保存済み immutable artifact が高精度 lineage と一致するか確認する."""
    if state.artifact_ref is None or state.artifact_fingerprint is None:
        return state.artifact_ref is None
    try:
        store = TranscriptArtifactStore(state.video_id, settings)
        artifact = store.load_artifact(state.artifact_fingerprint)
        actual_ref = store.artifact_ref(artifact)
    except (TranscriptArtifactError, OSError, ValueError):
        return False
    return (
        actual_ref == state.artifact_ref
        and artifact.video_id == state.video_id
        and artifact.artifact_fingerprint == state.artifact_fingerprint
        and tuple(artifact.used_range_cue_digests)
        == tuple(state.used_range_cue_digests)
        and artifact.is_high_precision
    )


def _line_state_needs_invalidation(state: LineState, settings: Settings) -> bool:
    return state.schema_version == 1 or (
        state.artifact_ref is not None and not _artifact_lineage_is_current(state, settings)
    )


def _lineage_is_already_invalidated(state: LineState) -> bool:
    return (
        state.schema_version == _SCHEMA_VERSION
        and state.review_confirmed_fingerprint is None
        and state.review_confirmed_at is None
        and state.generation_spec_json is None
        and state.generation_spec_fingerprint is None
        and state.output_fingerprint is None
        and state.preview_confirmed_fingerprint is None
        and state.preview_confirmed_at is None
        and state.upload_operation_id is None
        and state.current_stage == LineStage.TELOP_REVIEW
    )


def _load_line_state_locked(
    video_id: str,
    clip_id: str,
    settings: Settings,
) -> LineState | None:
    """同一動画 lock 保持中の復元。失効は atomic に永続化する."""
    path = line_state_path(video_id, clip_id, settings)
    state = _read_line_state(path, video_id, clip_id)
    if state is None:
        return None
    if _line_state_needs_invalidation(state, settings):
        if _lineage_is_already_invalidated(state):
            return state
        invalidated = _invalidate_transcript_lineage(state)
        _atomic_write(path, invalidated.model_dump(mode="json"))
        return invalidated
    return state


def _same_line_projection(
    first: LineState,
    second: LineState,
) -> bool:
    """Compare a displayed projection while ignoring its synthetic timestamp."""
    first_payload = first.model_dump(mode="python")
    second_payload = second.model_dump(mode="python")
    first_payload.pop("updated_at", None)
    second_payload.pop("updated_at", None)
    return first_payload == second_payload


def materialize_line_state_projection(
    displayed_state: LineState,
    settings: Settings,
) -> LineState:
    """Canonicalize one read-only UI snapshot before an explicit command.

    A normal render may project legacy or stale artifact lineage in memory so it
    remains write-free.  An explicit command must first turn that exact
    projection into the durable CAS baseline.  Only the projection timestamp is
    synthetic; every other field must still match.  Concurrent durable changes
    therefore fail closed before callers perform file or external side effects.
    """
    if not isinstance(displayed_state, LineState):
        raise LineStateError("確認するライン状態が正しくありません。")
    path = line_state_path(
        displayed_state.video_id,
        displayed_state.clip_id,
        settings,
    )
    with _line_lock(displayed_state.video_id, settings):
        persisted = _read_line_state(
            path,
            displayed_state.video_id,
            displayed_state.clip_id,
        )
        if persisted is None:
            raise LineStateError(
                "ライン状態が見つかりません。最新状態を読み直してください。"
            )
        if not _line_state_needs_invalidation(persisted, settings):
            if persisted != displayed_state:
                raise LineStateError(
                    "ライン状態が別の操作で更新されました。最新状態を読み直してください。"
                )
            return persisted
        if _lineage_is_already_invalidated(persisted):
            if persisted != displayed_state:
                raise LineStateError(
                    "ライン状態が別の操作で更新されました。最新状態を読み直してください。"
                )
            return persisted

        canonical = _invalidate_transcript_lineage(persisted)
        if not _same_line_projection(canonical, displayed_state):
            raise LineStateError(
                "ライン状態が別の操作で更新されました。最新状態を読み直してください。"
            )
        _atomic_write(path, canonical.model_dump(mode="json"))
        return canonical


def save_line_state(
    state: LineState,
    settings: Settings,
    *,
    expected_state: LineState | None = None,
) -> Path:
    """古い snapshot を拒否してライン状態を atomic 保存する。"""
    if not isinstance(state, LineState):
        raise LineStateError("保存するライン状態が正しくありません。")
    path = line_state_path(state.video_id, state.clip_id, settings)
    with _line_lock(state.video_id, settings):
        persisted = _load_line_state_locked(state.video_id, state.clip_id, settings)
        if expected_state is not None and persisted != expected_state:
            raise LineStateError(
                "ライン状態が別の操作で更新されました。最新状態を読み直してください。"
            )
        if persisted is not None:
            if state.updated_at < persisted.updated_at:
                raise LineStateError(
                    "古いライン状態では新しい確認状態を上書きできません。"
                )
            if state.updated_at == persisted.updated_at:
                if state == persisted:
                    return path
                raise LineStateError(
                    "同じ更新日時に異なるライン状態があるため保存を停止しました。"
                )
        _atomic_write(path, state.model_dump(mode="json"))
    return path


def load_line_state(
    video_id: str,
    clip_id: str,
    settings: Settings,
) -> LineState | None:
    """欠落は None、破損・identity 不一致は fail closed error にする。"""
    path = line_state_path(video_id, clip_id, settings)
    state = _read_line_state(path, video_id, clip_id)
    if state is None or not _line_state_needs_invalidation(state, settings):
        return state
    with _line_lock(video_id, settings):
        return _load_line_state_locked(video_id, clip_id, settings)


def save_active_line(
    video_id: str,
    clip_id: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> Path:
    """保存済みの未完了ラインだけを明示 active pointer にする。"""
    pointer = ActiveLinePointer(clip_id=clip_id, updated_at=_timestamp(now, "選択更新日時"))
    path = _active_line_path(video_id, settings)
    with _line_lock(video_id, settings):
        state = _load_line_state_locked(video_id, clip_id, settings)
        if state is None:
            raise LineStateError("選択するショート生産ラインが保存されていません。")
        if state.current_stage == LineStage.RESERVED:
            raise LineStateError("予約完了したラインを作成中として選択できません。")
        _atomic_write(path, pointer.model_dump(mode="json"))
    return path


def persist_line_start(
    state: LineState,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Persist a new line and its active pointer as one rollback-safe command.

    The two JSON files cannot be replaced by one filesystem rename.  This
    command therefore snapshots both under the per-video lock and restores both
    if either write fails.  Session projections must only be installed after
    this function returns successfully.
    """
    if not isinstance(state, LineState):
        raise LineStateError("開始するライン状態が正しくありません。")
    if state.current_stage == LineStage.RESERVED:
        raise LineStateError("予約完了したラインを作成中として開始できません。")

    line_path = line_state_path(state.video_id, state.clip_id, settings)
    active_path = _active_line_path(state.video_id, settings)
    pointer = ActiveLinePointer(
        clip_id=state.clip_id,
        updated_at=_timestamp(now, "選択更新日時"),
    )

    with _line_lock(state.video_id, settings):
        previous_line = _file_snapshot(line_path)
        previous_active = _file_snapshot(active_path)
        try:
            persisted = _load_line_state_locked(
                state.video_id,
                state.clip_id,
                settings,
            )
            if persisted is not None:
                if state.updated_at < persisted.updated_at:
                    raise LineStateError(
                        "古いライン状態では新しい確認状態を上書きできません。"
                    )
                if state.updated_at == persisted.updated_at and state != persisted:
                    raise LineStateError(
                        "同じ更新日時に異なるライン状態があるため開始を停止しました。"
                    )
            _atomic_write(active_path, pointer.model_dump(mode="json"))
            # Pointer-first makes a process stop between replaces fail closed:
            # a dangling pointer is ignored by the resolver, while writing the
            # line first could make an uncommitted orphan win fallback selection.
            _atomic_write(line_path, state.model_dump(mode="json"))
        except LineStateError as exc:
            rollback_errors: list[LineStateError] = []
            for path, snapshot in (
                (line_path, previous_line),
                (active_path, previous_active),
            ):
                try:
                    _restore_file_snapshot(path, snapshot)
                except LineStateError as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                raise LineStateError(
                    "ライン開始の保存に失敗し、以前の状態も安全に復元できませんでした。"
                ) from rollback_errors[0]
            raise LineStateError(
                "ショート生産ラインの開始を安全に保存できませんでした。"
            ) from exc
    return line_path, active_path


def _load_active_pointer(video_id: str, settings: Settings) -> ActiveLinePointer | None:
    path = _active_line_path(video_id, settings)
    if not path.exists():
        return None
    try:
        return ActiveLinePointer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        return None


def abandon_line_state(state: LineState, settings: Settings) -> Path:
    """CAS で未完了ラインだけを退避し、生成成果物には触れず選定へ戻す。"""
    if not isinstance(state, LineState):
        raise LineStateError("破棄するライン状態が正しくありません。")
    _ensure_not_reserved(state)
    path = line_state_path(state.video_id, state.clip_id, settings)
    try:
        archive = confined_video_path(
            settings.data_dir,
            state.video_id,
            "shorts",
            "line",
            f"abandoned_{state.clip_id}_{uuid.uuid4().hex}.json",
            label="ショート生産ライン保存先",
        )
    except PathConfinementError as exc:
        raise LineStateError(str(exc)) from exc
    with _line_lock(state.video_id, settings):
        persisted = _load_line_state_locked(state.video_id, state.clip_id, settings)
        if persisted != state:
            raise LineStateError(
                "ライン状態が別の操作で更新されました。最新状態を読み直してください。"
            )
        try:
            os.replace(path, archive)
            pointer = _load_active_pointer(state.video_id, settings)
            if pointer is not None and pointer.clip_id == state.clip_id:
                _active_line_path(state.video_id, settings).unlink(missing_ok=True)
        except OSError as exc:
            raise LineStateError(
                "ショート生産ラインを安全に退避できませんでした。"
            ) from exc
    return archive


def run_line_reservation_transaction(
    video_id: str,
    clip_id: str,
    output_path: Path,
    settings: Settings,
    start_upload: Callable[[], UploadOperation],
) -> UploadOperation:
    """exact line の検証から外部投稿開始・operation 記録までを同一 lock で囲む。"""
    video_id = _safe_identifier(video_id, "動画 ID")
    clip_id = _safe_identifier(clip_id, "clip ID")
    output_path = _confined_output_path(output_path, settings)
    if not callable(start_upload):
        raise LineStateError("投稿開始 callback が正しくありません。")
    with _line_lock(video_id, settings):
        state = _load_line_state_locked(video_id, clip_id, settings)
        if state is None:
            return start_upload()
        if state.generation_spec_fingerprint is None:
            raise LineStateError("生成 spec の証跡がないため予約投稿できません。")
        checked = reconcile_output(state, output_path)
        if checked != state:
            _atomic_write(
                line_state_path(video_id, clip_id, settings),
                checked.model_dump(mode="json"),
            )
        if (
            checked.output_fingerprint is None
            or checked.preview_confirmed_fingerprint != checked.output_fingerprint
        ):
            raise LineStateError(
                "完成動画が最終確認時から変わりました。もう一度プレビューしてください。"
            )
        operation = start_upload()
        if not isinstance(operation, UploadOperation):
            raise LineStateError("開始済み投稿 operation を確認できませんでした。")
        try:
            if (
                operation.source_video_id != video_id
                or operation.clip_id != clip_id
                or operation.video_path.resolve() != Path(output_path).resolve()
            ):
                raise LineStateError("開始済み投稿 operation がライン対象と一致しません。")
            completed = record_upload_operation(
                checked,
                operation.operation_id,
                output_path,
            )
            _atomic_write(
                line_state_path(video_id, clip_id, settings),
                completed.model_dump(mode="json"),
            )
        except LineStateError as exc:
            raise LineReservationStartedError(str(exc), operation) from exc
        if (
            completed.current_stage != LineStage.RESERVED
            or completed.upload_operation_id != operation.operation_id
        ):
            raise LineReservationStartedError(
                "投稿開始後に完成動画が変わったため、ラインへ予約完了を記録できませんでした。",
                operation,
            )
        return operation


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


def _project_line_state_read_only(
    state: LineState,
    settings: Settings,
) -> LineState:
    """Apply fail-closed lineage invalidation in memory without saving it."""
    if not _line_state_needs_invalidation(state, settings):
        return state
    if _lineage_is_already_invalidated(state):
        return state
    return _invalidate_transcript_lineage(state)


def resolve_active_line_read_only(
    video_id: str,
    settings: Settings,
) -> LineState | None:
    """Resolve the sidebar line without modifying line or pointer files."""
    _safe_identifier(video_id, "動画 ID")
    pointer = _load_active_pointer(video_id, settings)
    if pointer is not None:
        try:
            pointed = _read_line_state(
                line_state_path(video_id, pointer.clip_id, settings),
                video_id,
                pointer.clip_id,
            )
        except LineStateError:
            pointed = None
        if pointed is not None and pointed.current_stage != LineStage.RESERVED:
            return _project_line_state_read_only(pointed, settings)

    directory = _active_line_path(video_id, settings).parent
    states: list[LineState] = []
    if directory.exists():
        for path in directory.glob("line_*.json"):
            clip_id = path.stem.removeprefix("line_")
            try:
                state = _read_line_state(path, video_id, clip_id)
            except LineStateError:
                continue
            if state is not None and state.current_stage != LineStage.RESERVED:
                states.append(state)
    if not states:
        return None
    states.sort(key=lambda state: state.clip_id)
    states.sort(key=lambda state: state.updated_at, reverse=True)
    return _project_line_state_read_only(states[0], settings)


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
    unfinished.sort(key=lambda state: state.clip_id)
    unfinished.sort(key=lambda state: state.updated_at, reverse=True)
    return unfinished[0]


def recover_line_state(
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
    *,
    review_fingerprint: str | None = None,
    material_context: Mapping[str, object] | None = None,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
    generation_spec: Mapping[str, object] | None = None,
    output_fingerprint: str | None = None,
    upload_operation: UploadOperation | None = None,
    now: datetime | None = None,
) -> LineState:
    """機械的に証明済みの evidence だけから、人確認を外して再構成する。"""
    state = create_line_state(
        video_id,
        clip_id,
        queue_fingerprint,
        review_fingerprint=review_fingerprint,
        material_context=material_context,
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        used_range_cue_digests=used_range_cue_digests,
        now=now,
    )
    output = _validate_digest(output_fingerprint, "output fingerprint", optional=True)
    generation_json: str | None = None
    generation_fingerprint: str | None = None
    if generation_spec is not None:
        if review_fingerprint is None or not isinstance(generation_spec, Mapping):
            raise LineStateError("生成 spec の復元 evidence が正しくありません。")
        generation_payload = dict(generation_spec)
        generation_json = _canonical_json(generation_payload)
        generation_fingerprint = make_generation_spec_fingerprint(
            video_id,
            clip_id,
            queue_fingerprint,
            review_fingerprint,
            generation_payload,
        )
    if output is not None and generation_fingerprint is None:
        raise LineStateError("出力の復元には生成 spec の証跡が必要です。")
    operation_id: str | None = None
    if upload_operation is not None:
        if not isinstance(upload_operation, UploadOperation):
            raise LineStateError("復元する投稿 operation が正しくありません。")
        if (
            upload_operation.source_video_id != video_id
            or upload_operation.clip_id != clip_id
        ):
            raise LineStateError("投稿 operation が復元対象のラインと一致しません。")
        if upload_operation.state not in _COMPLETED_UPLOAD_STATES:
            raise LineStateError("未完了の投稿 operation から予約完了を復元できません。")
        operation_id = upload_operation.operation_id
    stage = LineStage.RESERVED if operation_id is not None else LineStage.TELOP_REVIEW
    return _replace_state(
        state,
        generation_spec_json=generation_json,
        generation_spec_fingerprint=generation_fingerprint,
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
