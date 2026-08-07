"""確定済みテロップ台本からショート動画を順次生成するサービス."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

import fcntl

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.models.transcript import TranscriptArtifactRef
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services.ffmpeg import FfmpegError, probe_media_streams
from yt_live_kit.services.shorts import (
    MAX_DURATION_SEC,
    MIN_DURATION_SEC,
    ShortsError,
    build_short_from_segments,
)
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_video_path,
    safe_identifier,
    validate_confined_candidate,
)
from yt_live_kit.services.subtitle_burn import SubtitleBurnError, get_telop_preset
from yt_live_kit.services.telop import (
    TelopError,
    make_clip_id,
    normalize_segment_bounds,
    validate_telop_script,
)
from yt_live_kit.services.transcript_artifact import (
    TranscriptArtifactError,
    TranscriptArtifactStore,
    stored_artifact_lineage_is_current,
)

QueueSource = Literal["clips", "highlights"]
QueueMode = Literal["individual", "concat"]
QueueItemStatus = Literal["succeeded", "failed"]
QueueStatus = Literal["running", "done", "interrupted"]
ShortsQueueProgressCallback = Callable[[int, int, str], None] | None

LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
RECOVERY_TABLE: Mapping[str, str] = {
    "done": "完了 manifest は内容を保持し、検証済みの成功 item だけを表示・予約対象にする。",
    "running_live": "owner job が実行中なら manifest を変更せず、予約は許可しない。",
    "running_orphan": "owner job が見つからない、または終了済みなら interrupted へ atomic 遷移する。",
    "interrupted": "terminal state として保持し、途中結果を done に昇格させない。明示的な再実行だけを許可する。",
    "corrupted": "manifest を上書きせず、日本語エラーで fail closed にする。",
    "owner_mismatch": "保存先の job ID と owner_job_id が一致しなければ読み込み・復旧を拒否する。",
    "missing_output_or_fingerprint": "成功 item の自動再利用を拒否し、再生成を必要とする。",
}

_SPEC_KEYS = frozenset(
    {
        "target_id",
        "segments",
        "telop_document",
        "layout",
        "preset",
        "hook_preset",
        "output_name",
    }
)
_SPEC_KEYS_WITH_LINEAGE = frozenset(
    {
        *_SPEC_KEYS,
        "artifact_ref",
        "artifact_fingerprint",
        "used_range_cue_digests",
    }
)
_ITEM_KEYS = frozenset(
    {
        "target_id",
        "status",
        "output_path",
        "log_path",
        "font_warning",
        "title_candidates",
        "description",
        "tags",
        "error",
        "input_fingerprint",
        "output_fingerprint",
    }
)
_LEGACY_ITEM_KEYS = frozenset(
    {
        "target_id",
        "status",
        "output_path",
        "log_path",
        "font_warning",
        "title_candidates",
        "description",
        "tags",
        "error",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "video_id",
        "job_id",
        "owner_job_id",
        "status",
        "created_at",
        "updated_at",
        "clip_specs",
        "items",
        "success_count",
        "failure_count",
    }
)
_LEGACY_MANIFEST_KEYS = frozenset(_MANIFEST_KEYS - {"owner_job_id"})


class ShortsQueueError(ShortsError):
    """ショート量産キュー全体を開始・継続できないエラー."""


def _require_exact_keys(data: Mapping[str, object], expected: frozenset[str]) -> None:
    actual = frozenset(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail: list[str] = []
        if missing:
            detail.append(f"不足: {', '.join(missing)}")
        if unknown:
            detail.append(f"未対応: {', '.join(unknown)}")
        raise ShortsQueueError(
            "ショート量産データの項目が正しくありません。" + " / ".join(detail)
        )


def _require_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ShortsQueueError(f"{label} は文字列で指定してください。")
    if not allow_empty and not value.strip():
        raise ShortsQueueError(f"{label} を入力してください。")
    if "<" in value or ">" in value:
        raise ShortsQueueError(f"{label} に半角の山カッコは使えません。")
    return value


def _require_optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label)


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ShortsQueueError(f"{label} は整数で指定してください。")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_utc_datetime(value: object, label: str) -> datetime:
    text = _require_string(value, label)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ShortsQueueError(f"{label} の日時形式が正しくありません。") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise ShortsQueueError(f"{label} はタイムゾーン付き UTC 日時にしてください。")
    if offset.total_seconds() != 0:
        raise ShortsQueueError(f"{label} は UTC 日時にしてください。")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ShortsQueueSegmentSpec:
    """キューへ渡す deep immutable な区間."""

    id: str
    title: str
    start_ms: int
    end_ms: int
    reason: str

    @classmethod
    def from_highlight(cls, segment: HighlightSegment) -> ShortsQueueSegmentSpec:
        bounds = normalize_segment_bounds([segment])[0]
        return cls(
            id=_require_string(segment.id, "区間 ID"),
            title=_require_string(segment.title, "区間タイトル"),
            start_ms=bounds.start_ms,
            end_ms=bounds.end_ms,
            reason=_require_string(segment.reason, "選定理由"),
        )

    def to_highlight(self) -> HighlightSegment:
        return HighlightSegment(
            id=self.id,
            title=self.title,
            start=_format_timestamp(self.start_ms),
            end=_format_timestamp(self.end_ms),
            duration_sec=(self.end_ms - self.start_ms) // 1000,
            reason=self.reason,
        )

    def to_tuple(self) -> tuple[float, float]:
        return self.start_ms / 1000, self.end_ms / 1000

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> ShortsQueueSegmentSpec:
        if not isinstance(value, Mapping):
            raise ShortsQueueError("区間データはオブジェクトで指定してください。")
        expected = frozenset({"id", "title", "start_ms", "end_ms", "reason"})
        _require_exact_keys(value, expected)
        start_ms = _require_int(value["start_ms"], "開始時刻")
        end_ms = _require_int(value["end_ms"], "終了時刻")
        if start_ms < 0 or end_ms <= start_ms:
            raise ShortsQueueError("区間の開始・終了時刻が正しくありません。")
        return cls(
            id=_require_string(value["id"], "区間 ID"),
            title=_require_string(value["title"], "区間タイトル"),
            start_ms=start_ms,
            end_ms=end_ms,
            reason=_require_string(value["reason"], "選定理由"),
        )


@dataclass(frozen=True)
class ShortsQueueTarget:
    """台本生成前の決定的な生成対象."""

    target_id: str
    segments: tuple[ShortsQueueSegmentSpec, ...]
    output_name: str

    def highlight_segments(self) -> tuple[HighlightSegment, ...]:
        return tuple(segment.to_highlight() for segment in self.segments)


@dataclass(frozen=True)
class ShortsQueueClipSpec:
    """人が確認した台本を含む deep immutable な生成 snapshot."""

    target_id: str
    segments: tuple[ShortsQueueSegmentSpec, ...]
    telop_document_json: str
    layout: str
    preset: str
    hook_preset: str
    output_name: str
    artifact_ref: TranscriptArtifactRef | None = None
    artifact_fingerprint: str | None = None
    used_range_cue_digests: tuple[str, ...] = ()

    @property
    def telop_document(self) -> TelopScriptDocument:
        try:
            document = TelopScriptDocument.model_validate_json(self.telop_document_json)
        except Exception as exc:
            raise ShortsQueueError("確定済みテロップ台本を読み込めません。") from exc
        validation = validate_telop_script(
            document,
            segments=[segment.to_tuple() for segment in self.segments],
        )
        if not validation.ok or validation.document is None:
            raise ShortsQueueError(
                "確定済みテロップ台本が入力区間と一致しません: "
                + "、".join(validation.errors)
            )
        return validation.document

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "target_id": self.target_id,
            "segments": [segment.to_dict() for segment in self.segments],
            "telop_document": json.loads(self.telop_document_json),
            "layout": self.layout,
            "preset": self.preset,
            "hook_preset": self.hook_preset,
            "output_name": self.output_name,
        }
        if self.artifact_ref is not None:
            payload.update(
                {
                    "artifact_ref": self.artifact_ref.model_dump(mode="json"),
                    "artifact_fingerprint": self.artifact_fingerprint,
                    "used_range_cue_digests": list(self.used_range_cue_digests),
                }
            )
        return payload

    @classmethod
    def from_dict(cls, value: object) -> ShortsQueueClipSpec:
        if not isinstance(value, Mapping):
            raise ShortsQueueError("確定済み生成対象はオブジェクトで指定してください。")
        keys = frozenset(value)
        if keys not in {_SPEC_KEYS, _SPEC_KEYS_WITH_LINEAGE}:
            _require_exact_keys(value, _SPEC_KEYS_WITH_LINEAGE)
        raw_segments = value["segments"]
        if not isinstance(raw_segments, list) or not raw_segments:
            raise ShortsQueueError("確定済み生成対象に区間を 1 件以上指定してください。")
        segments = tuple(ShortsQueueSegmentSpec.from_dict(item) for item in raw_segments)
        _validate_target_duration(segments)
        target_id = _require_string(value["target_id"], "生成対象 ID")
        expected_id = make_clip_id([segment.to_tuple() for segment in segments])
        if target_id != expected_id:
            raise ShortsQueueError("生成対象 ID が入力区間と一致しません。")
        output_name = _require_string(value["output_name"], "出力ファイル名")
        if output_name != f"short_{target_id}.mp4":
            raise ShortsQueueError("出力ファイル名が生成対象 ID と一致しません。")
        layout = _require_string(value["layout"], "レイアウト")
        if layout not in {"blur", "crop"}:
            raise ShortsQueueError("レイアウトは blur または crop を指定してください。")
        preset = _require_string(value["preset"], "通常プリセット")
        hook_preset = _require_string(value["hook_preset"], "Hook プリセット")
        try:
            get_telop_preset(preset)
            get_telop_preset(hook_preset)
        except SubtitleBurnError as exc:
            raise ShortsQueueError(str(exc)) from exc
        raw_document = value["telop_document"]
        if not isinstance(raw_document, Mapping):
            raise ShortsQueueError("確定済みテロップ台本はオブジェクトで指定してください。")
        try:
            document = TelopScriptDocument.model_validate(dict(raw_document))
        except Exception as exc:
            raise ShortsQueueError("確定済みテロップ台本の形式が正しくありません。") from exc
        validation = validate_telop_script(
            document,
            segments=[segment.to_tuple() for segment in segments],
        )
        if not validation.ok or validation.document is None:
            raise ShortsQueueError(
                "確定済みテロップ台本が入力区間と一致しません: "
                + "、".join(validation.errors)
            )
        document = validation.document
        artifact_ref = document.artifact_ref
        artifact_fingerprint = document.artifact_fingerprint
        used_range_cue_digests = tuple(document.used_range_cue_digests)
        if keys == _SPEC_KEYS_WITH_LINEAGE:
            raw_ref = value["artifact_ref"]
            if not isinstance(raw_ref, Mapping):
                raise ShortsQueueError("artifact reference はオブジェクトで指定してください。")
            try:
                artifact_ref = TranscriptArtifactRef.model_validate(dict(raw_ref))
            except Exception as exc:
                raise ShortsQueueError("artifact reference の形式が正しくありません。") from exc
            artifact_fingerprint = _optional_fingerprint(
                value["artifact_fingerprint"], "artifact fingerprint"
            )
            raw_digests = value["used_range_cue_digests"]
            if not isinstance(raw_digests, list):
                raise ShortsQueueError("used_range_cue_digests は配列で指定してください。")
            used_range_cue_digests = tuple(
                _optional_fingerprint(item, "used_range_cue_digest") or ""
                for item in raw_digests
            )
            if any(not digest for digest in used_range_cue_digests):
                raise ShortsQueueError("used_range_cue_digest が空です。")
        has_lineage = (
            artifact_ref is not None
            or artifact_fingerprint is not None
            or bool(used_range_cue_digests)
        )
        if has_lineage:
            if artifact_ref is None or artifact_fingerprint is None:
                raise ShortsQueueError("artifact lineage が不完全です。")
            if artifact_fingerprint != artifact_ref.artifact_fingerprint:
                raise ShortsQueueError("artifact fingerprint が artifact reference と一致しません。")
            if (
                document.artifact_ref is None
                or document.artifact_fingerprint != artifact_fingerprint
                or document.artifact_ref != artifact_ref
                or tuple(document.used_range_cue_digests) != used_range_cue_digests
            ):
                raise ShortsQueueError("queue spec とテロップ台本の artifact lineage が一致しません。")
        canonical = _canonical_json(validation.document.model_dump(mode="json"))
        return cls(
            target_id=target_id,
            segments=segments,
            telop_document_json=canonical,
            layout=layout,
            preset=preset,
            hook_preset=hook_preset,
            output_name=output_name,
            artifact_ref=artifact_ref,
            artifact_fingerprint=artifact_fingerprint,
            used_range_cue_digests=used_range_cue_digests,
        )


@dataclass(frozen=True)
class ShortsQueueItemResult:
    """生成対象 1 件の成功または失敗."""

    target_id: str
    status: QueueItemStatus
    output_path: Path | None
    log_path: Path | None
    font_warning: str | None
    title_candidates: tuple[str, ...]
    description: str
    tags: tuple[str, ...]
    error: str | None
    input_fingerprint: str | None = None
    output_fingerprint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "status": self.status,
            "output_path": str(self.output_path) if self.output_path else None,
            "log_path": str(self.log_path) if self.log_path else None,
            "font_warning": self.font_warning,
            "title_candidates": list(self.title_candidates),
            "description": self.description,
            "tags": list(self.tags),
            "error": self.error,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        legacy: bool = False,
    ) -> ShortsQueueItemResult:
        if not isinstance(value, Mapping):
            raise ShortsQueueError("生成結果はオブジェクトで指定してください。")
        _require_exact_keys(value, _LEGACY_ITEM_KEYS if legacy else _ITEM_KEYS)
        status_text = _require_string(value["status"], "生成結果の状態")
        status: Literal["succeeded", "failed"]
        if status_text == "succeeded":
            status = "succeeded"
        elif status_text == "failed":
            status = "failed"
        else:
            raise ShortsQueueError("生成結果の状態が正しくありません。")
        title_candidates = _string_tuple(value["title_candidates"], "タイトル案")
        tags = _string_tuple(value["tags"], "タグ")
        output_path = _optional_path(value["output_path"], "出力パス")
        log_path = _optional_path(value["log_path"], "ログパス")
        error = _require_optional_string(value["error"], "エラー")
        input_fingerprint = _optional_fingerprint(
            value.get("input_fingerprint"), "入力 fingerprint"
        )
        output_fingerprint = _optional_fingerprint(
            value.get("output_fingerprint"), "出力 fingerprint"
        )
        if status == "succeeded" and (output_path is None or error is not None):
            raise ShortsQueueError("成功結果の出力パスまたはエラー状態が正しくありません。")
        if status == "failed" and (output_path is not None or error is None):
            raise ShortsQueueError("失敗結果の出力パスまたはエラー状態が正しくありません。")
        return cls(
            target_id=_require_string(value["target_id"], "生成対象 ID"),
            status=status,
            output_path=output_path,
            log_path=log_path,
            font_warning=_require_optional_string(value["font_warning"], "フォント警告"),
            title_candidates=title_candidates,
            description=_require_string(value["description"], "説明文"),
            tags=tags,
            error=error,
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
        )


@dataclass(frozen=True)
class ShortsQueueResult:
    """manifest と同じ内容を保持するキュー結果."""

    video_id: str
    job_id: str
    status: QueueStatus
    created_at: datetime
    updated_at: datetime
    clip_specs: tuple[ShortsQueueClipSpec, ...]
    items: tuple[ShortsQueueItemResult, ...]
    success_count: int
    failure_count: int
    manifest_path: Path
    owner_job_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "video_id": self.video_id,
            "job_id": self.job_id,
            "owner_job_id": self.owner_job_id or self.job_id,
            "status": self.status,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "updated_at": self.updated_at.astimezone(timezone.utc).isoformat(),
            "clip_specs": [spec.to_dict() for spec in self.clip_specs],
            "items": [item.to_dict() for item in self.items],
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        manifest_path: Path,
    ) -> ShortsQueueResult:
        if not isinstance(value, Mapping):
            raise ShortsQueueError("ショート量産 manifest はオブジェクトで指定してください。")
        schema_version = _require_int(value.get("schema_version"), "schema version")
        legacy = schema_version == LEGACY_SCHEMA_VERSION
        if schema_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
            raise ShortsQueueError("対応していないショート量産 manifest です。")
        _require_exact_keys(value, _LEGACY_MANIFEST_KEYS if legacy else _MANIFEST_KEYS)
        status_text = _require_string(value["status"], "キュー状態")
        # legacy manifest には interrupted が存在しない。
        status: Literal["running", "done", "interrupted"]
        if status_text == "running":
            status = "running"
        elif status_text == "done":
            status = "done"
        elif status_text == "interrupted" and not legacy:
            status = "interrupted"
        else:
            raise ShortsQueueError("キュー状態が正しくありません。")
        raw_specs = value["clip_specs"]
        raw_items = value["items"]
        if not isinstance(raw_specs, list) or not raw_specs:
            raise ShortsQueueError("manifest に生成対象がありません。")
        if not isinstance(raw_items, list):
            raise ShortsQueueError("manifest の生成結果が正しくありません。")
        specs = tuple(ShortsQueueClipSpec.from_dict(item) for item in raw_specs)
        items = tuple(
            ShortsQueueItemResult.from_dict(item, legacy=legacy) for item in raw_items
        )
        if len(items) > len(specs):
            raise ShortsQueueError("manifest の生成結果件数が対象件数を超えています。")
        expected_ids = tuple(spec.target_id for spec in specs[: len(items)])
        if tuple(item.target_id for item in items) != expected_ids:
            raise ShortsQueueError("manifest の生成結果順が対象の入力順と一致しません。")
        success_count = _require_int(value["success_count"], "成功件数")
        failure_count = _require_int(value["failure_count"], "失敗件数")
        if success_count != sum(item.status == "succeeded" for item in items):
            raise ShortsQueueError("manifest の成功件数が生成結果と一致しません。")
        if failure_count != sum(item.status == "failed" for item in items):
            raise ShortsQueueError("manifest の失敗件数が生成結果と一致しません。")
        if status == "done" and len(items) != len(specs):
            raise ShortsQueueError("完了 manifest に未処理の生成対象があります。")
        created_at = _parse_utc_datetime(value["created_at"], "作成日時")
        updated_at = _parse_utc_datetime(value["updated_at"], "更新日時")
        if updated_at < created_at:
            raise ShortsQueueError("manifest の更新日時が作成日時より前です。")
        job_id = _safe_path_identifier(value["job_id"], "ジョブ ID")
        owner_job_id = (
            job_id
            if legacy
            else _safe_path_identifier(value["owner_job_id"], "owner job ID")
        )
        if owner_job_id != job_id:
            raise ShortsQueueError("manifest の owner job ID がジョブ ID と一致しません。")
        return cls(
            video_id=_require_string(value["video_id"], "動画 ID"),
            job_id=job_id,
            owner_job_id=owner_job_id,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            clip_specs=specs,
            items=items,
            success_count=success_count,
            failure_count=failure_count,
            manifest_path=Path(manifest_path),
            schema_version=schema_version,
        )


def _format_timestamp(milliseconds: int) -> str:
    total_seconds, millis = divmod(milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if millis:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ShortsQueueError(f"{label}を 1 件以上指定してください。")
    return tuple(_require_string(item, f"{label} {index}") for index, item in enumerate(value, 1))


def _optional_fingerprint(value: object, label: str) -> str | None:
    if value is None:
        return None
    text = _require_string(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ShortsQueueError(f"{label} の形式が正しくありません。")
    return text


def _optional_path(value: object, label: str) -> Path | None:
    if value is None:
        return None
    return Path(_require_string(value, label))


def _normalized_lexical_path(path: Path) -> Path:
    """symlink を解決せず、比較可能な lexical absolute path に揃える."""
    return Path(os.path.abspath(os.fspath(path)))


def _has_symlink_component(path: Path, data_dir: Path) -> bool:
    """data root 自体を除き、candidate の lexical path に symlink があるか返す."""
    root = _normalized_lexical_path(data_dir)
    candidate = _normalized_lexical_path(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            return True
    return False


def _safe_path_identifier(value: object, label: str) -> str:
    try:
        return safe_identifier(value, label)
    except PathConfinementError as exc:
        raise ShortsQueueError(str(exc)) from exc


def normalize_queue_candidates(
    candidates: Sequence[ClipCandidate | HighlightSegment],
    *,
    source: QueueSource,
) -> tuple[ShortsQueueSegmentSpec, ...]:
    """選択した単一ソースの候補を表示順の frozen 区間へ正規化する."""
    if source not in {"clips", "highlights"}:
        raise ShortsQueueError("候補ソースは切り抜き候補またはハイライトを選んでください。")
    if not candidates:
        raise ShortsQueueError("候補を 1 件以上選択してください。")
    expected_type = ClipCandidate if source == "clips" else HighlightSegment
    if any(not isinstance(candidate, expected_type) for candidate in candidates):
        raise ShortsQueueError("異なる候補ソースを混在させることはできません。")
    highlights = [
        HighlightSegment.model_validate(candidate.model_dump(mode="json"))
        for candidate in candidates
    ]
    return tuple(ShortsQueueSegmentSpec.from_highlight(item) for item in highlights)


def select_queue_candidates_by_id(
    candidates: Sequence[ClipCandidate | HighlightSegment],
    selected_ids: Sequence[str],
) -> tuple[ClipCandidate | HighlightSegment, ...]:
    """現在のソース文書を表示順に走査し、snapshot の候補集合を取り直す."""
    if not selected_ids:
        raise ShortsQueueError("候補を 1 件以上選択してください。")
    selected_id_set = set(selected_ids)
    if len(selected_id_set) != len(selected_ids):
        raise ShortsQueueError("選択済み候補 ID が重複しています。")
    seen_ids: set[str] = set()
    selected: list[ClipCandidate | HighlightSegment] = []
    for candidate in candidates:
        if candidate.id in seen_ids:
            raise ShortsQueueError("候補ソース内の ID が重複しています。")
        seen_ids.add(candidate.id)
        if candidate.id in selected_id_set:
            selected.append(candidate)
    if {candidate.id for candidate in selected} != selected_id_set:
        raise ShortsQueueError(
            "確定後に候補内容が変更されました。選択し直してください。"
        )
    return tuple(selected)


def _validate_target_duration(segments: Sequence[ShortsQueueSegmentSpec]) -> int:
    """queue 公開境界の合計尺を整数 ms で再検証する."""
    total_ms = sum(segment.end_ms - segment.start_ms for segment in segments)
    if total_ms < int(MIN_DURATION_SEC * 1000):
        raise ShortsQueueError("ショート動画は 10 秒以上になる候補を選択してください。")
    if total_ms > int(MAX_DURATION_SEC * 1000):
        raise ShortsQueueError(
            "ショート動画が 180 秒を超えています。"
            "区間を分割するか減らす、または短くしてください。"
        )
    return total_ms


def build_shorts_queue_targets(
    segments: Sequence[ShortsQueueSegmentSpec],
    *,
    mode: QueueMode,
) -> tuple[ShortsQueueTarget, ...]:
    """個別または連結モードの決定的 target を入力順で作る."""
    if not segments:
        raise ShortsQueueError("候補を 1 件以上選択してください。")
    if mode not in {"individual", "concat"}:
        raise ShortsQueueError("生成モードは個別または連結を選んでください。")
    groups = tuple((segment,) for segment in segments) if mode == "individual" else (tuple(segments),)
    targets: list[ShortsQueueTarget] = []
    seen: set[str] = set()
    for group in groups:
        _validate_target_duration(group)
        target_id = make_clip_id([segment.to_tuple() for segment in group])
        output_name = f"short_{target_id}.mp4"
        if target_id in seen:
            raise ShortsQueueError(
                "同じ clip ID と出力先になる候補があります。選択内容を見直してください。"
            )
        seen.add(target_id)
        targets.append(
            ShortsQueueTarget(
                target_id=target_id,
                segments=group,
                output_name=output_name,
            )
        )
    return tuple(targets)


def make_shorts_queue_fingerprint(
    *,
    video_id: str,
    source: QueueSource,
    mode: QueueMode,
    original_candidates: Sequence[ClipCandidate | HighlightSegment],
    segments: Sequence[ShortsQueueSegmentSpec],
    layout: str,
    preset: str,
    hook_preset: str,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
) -> str:
    """選択 snapshot の material input だけから安定した SHA-256 を返す.

    artifact lineage は immutable input fingerprint の対象であり、既存 queue
    snapshot fingerprint の対象ではない。引数を受け取るのは lineage-aware な
    呼び出し側でも、同じ queue snapshot を再計算できることを明示するためで、
    この fingerprint payload には決して含めない。
    """
    _ = (artifact_ref, artifact_fingerprint, used_range_cue_digests)
    payload = {
        "video_id": _require_string(video_id, "動画 ID"),
        "source": source,
        "mode": mode,
        "original_candidates": [
            candidate.model_dump(mode="json") for candidate in original_candidates
        ],
        "segments": [segment.to_dict() for segment in segments],
        "layout": layout,
        "preset": preset,
        "hook_preset": hook_preset,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def is_shorts_queue_snapshot_current(
    expected_fingerprint: str | None,
    current_fingerprint: str,
) -> bool:
    """保存済み snapshot が現在の入力と一致するか返す."""
    return bool(expected_fingerprint) and expected_fingerprint == current_fingerprint


def can_start_shorts_queue(
    *,
    expected_fingerprint: str | None,
    current_fingerprint: str,
    target_ids: Sequence[str],
    confirmed_specs: Mapping[str, ShortsQueueClipSpec],
    busy: bool,
) -> tuple[bool, str | None]:
    """全確定済み snapshot を単一ジョブで開始できるか副作用なく判定する."""
    if busy:
        return False, "他の処理が実行中です。完了までお待ちください。"
    if not is_shorts_queue_snapshot_current(expected_fingerprint, current_fingerprint):
        return False, "選択内容が変更されました。台本を生成し直してください。"
    if not target_ids:
        return False, "生成対象を 1 件以上選択してください。"
    if len(set(target_ids)) != len(target_ids):
        return False, "生成対象 ID が重複しています。選択内容を見直してください。"
    if len(confirmed_specs) != len(target_ids) or set(confirmed_specs) != set(target_ids):
        return False, "すべての生成対象のテロップ台本を確定してください。"
    return True, None


def make_shorts_queue_clip_spec(
    target: ShortsQueueTarget,
    document: TelopScriptDocument,
    *,
    layout: str,
    preset: str,
    hook_preset: str,
) -> ShortsQueueClipSpec:
    """保存済み正規化台本から確定 spec を作り、公開境界で再検証する."""
    payload = {
        "target_id": target.target_id,
        "segments": [segment.to_dict() for segment in target.segments],
        "telop_document": document.model_dump(mode="json"),
        "layout": layout,
        "preset": preset,
        "hook_preset": hook_preset,
        "output_name": target.output_name,
    }
    return ShortsQueueClipSpec.from_dict(payload)


def _manifest_path(video_id: str, job_id: str, settings: Settings) -> Path:
    safe_video_id = _safe_path_identifier(video_id, "動画 ID")
    safe_job_id = _safe_path_identifier(job_id, "ジョブ ID")
    try:
        return confined_video_path(
            settings.data_dir,
            safe_video_id,
            "shorts",
            "queue",
            f"queue_{safe_job_id}.json",
            label="ショート量産保存先",
        )
    except PathConfinementError as exc:
        raise ShortsQueueError(str(exc)) from exc


def _queue_dir(video_id: str, settings: Settings) -> Path:
    safe_video_id = _safe_path_identifier(video_id, "動画 ID")
    try:
        return confined_video_path(
            settings.data_dir, safe_video_id, "shorts", "queue", label="ショート量産保存先"
        )
    except PathConfinementError as exc:
        raise ShortsQueueError(str(exc)) from exc


def _validate_result_paths(result: ShortsQueueResult, settings: Settings) -> None:
    paths = [result.manifest_path]
    for item in result.items:
        if item.output_path is not None:
            paths.append(item.output_path)
        if item.log_path is not None:
            paths.append(item.log_path)
    try:
        for path in paths:
            validate_confined_candidate(
                settings.data_dir, path, label="ショート量産成果物"
            )
    except PathConfinementError as exc:
        raise ShortsQueueError(str(exc)) from exc


def make_shorts_queue_input_fingerprint(
    video_id: str,
    spec: ShortsQueueClipSpec,
) -> str:
    """再実行時に同じ immutable input だったことを証明する fingerprint を返す."""
    spec_payload = spec.to_dict()
    if spec.artifact_ref is None:
        raw_document = spec_payload.get("telop_document")
        if isinstance(raw_document, Mapping):
            raw_document = dict(raw_document)
            raw_document.pop("artifact_ref", None)
            raw_document.pop("artifact_fingerprint", None)
            raw_document.pop("used_range_cue_digests", None)
            spec_payload["telop_document"] = raw_document
    payload = {
        "video_id": _require_string(video_id, "動画 ID"),
        "clip_spec": spec_payload,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def make_shorts_queue_output_fingerprint(
    path: Path,
    settings: Settings | None = None,
) -> str:
    """安全な絶対 path・stat・内容から immutable output fingerprint を作る."""
    settings = settings or get_settings()
    try:
        validate_confined_candidate(
            settings.data_dir, path, label="ショート量産成果物"
        )
        resolved_root = settings.data_dir.resolve(strict=False)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        before = resolved_path.stat()
        if not resolved_path.is_file() or before.st_size <= 0:
            raise OSError("empty output")
        digest = hashlib.sha256()
        with resolved_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = resolved_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError("output changed during fingerprinting")
    except (OSError, RuntimeError, ValueError) as exc:
        raise ShortsQueueError(
            "ショート量産成果物を安全に確認できないため、再利用できません。"
        ) from exc
    payload = {
        "path": str(resolved_path),
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


# 短い別名は、H1-3 の pure validation API を呼ぶ既存・将来の UI/CLI が
# output fingerprint の実装詳細に依存しないための互換入口である。
make_queue_input_fingerprint = make_shorts_queue_input_fingerprint
make_queue_output_fingerprint = make_shorts_queue_output_fingerprint


def can_reuse_shorts_queue_item(
    *,
    video_id: str,
    spec: ShortsQueueClipSpec,
    item: ShortsQueueItemResult,
    settings: Settings | None = None,
) -> bool:
    """成功 item を再利用できることを、入力・出力・安全な path で証明する.

    fingerprint の無い legacy/current done item は表示・予約の後方互換を保つが、
    interrupted queue の自動再利用には使わない。output が同名でも fingerprint が
    欠ける、内容が変わる、または data root 外なら必ず再生成へ倒す。
    """
    settings = settings or get_settings()
    if item.status != "succeeded" or item.output_path is None:
        return False
    try:
        _validate_spec_artifact_lineage(video_id, spec, settings)
    except ShortsQueueError:
        return False
    expected_input = make_shorts_queue_input_fingerprint(video_id, spec)
    if item.input_fingerprint != expected_input or not item.output_fingerprint:
        return False
    try:
        expected_path = confined_video_path(
            settings.data_dir,
            _safe_path_identifier(video_id, "動画 ID"),
            "shorts",
            "output",
            spec.output_name,
            label="ショート量産成果物",
        )
        validate_confined_candidate(
            settings.data_dir, item.output_path, label="ショート量産成果物"
        )
        actual_path = item.output_path.resolve(strict=True)
        if actual_path != expected_path.resolve(strict=True):
            return False
        streams = probe_media_streams(
            actual_path,
            ffmpeg_path=settings.ffmpeg_path,
            ffmpeg_timeout=settings.ffmpeg_timeout,
        )
        if not streams.has_audio_video:
            return False
        return make_shorts_queue_output_fingerprint(actual_path, settings) == item.output_fingerprint
    except (FfmpegError, OSError, RuntimeError, ShortsQueueError, ValueError):
        return False


def _validate_spec_artifact_lineage(
    video_id: str,
    spec: ShortsQueueClipSpec,
    settings: Settings,
) -> None:
    """queue 生成直前に、保存済み immutable artifact だけを検証する."""
    if spec.artifact_ref is None:
        return
    if spec.artifact_fingerprint is None:
        raise ShortsQueueError("queue spec の artifact lineage が不完全です。")
    try:
        store = TranscriptArtifactStore(video_id, settings)
        artifact = store.load_artifact(spec.artifact_fingerprint)
    except (TranscriptArtifactError, OSError, ValueError) as exc:
        raise ShortsQueueError(
            "queue spec の字幕 artifact を確認できません。古い結果は再利用せず、明示的に再確認してください。"
        ) from exc
    if (
        artifact.source_kind.value != "whisper_cpp"
        or artifact.status.value != "success"
        or not artifact.is_high_precision
        or any(item.status.value != "success" for item in artifact.ranges)
    ):
        raise ShortsQueueError(
            "queue spec の字幕 artifact は高精度成功結果ではありません。"
            "既存 VTT fallback を明示的に選ぶか、処理を停止してください。"
        )
    if not stored_artifact_lineage_is_current(
        video_id=video_id,
        artifact_ref=spec.artifact_ref,
        artifact_fingerprint=spec.artifact_fingerprint,
        used_range_cue_digests=spec.used_range_cue_digests,
        settings=settings,
    ):
        raise ShortsQueueError("queue spec の artifact lineage が保存済み artifact と一致しません。")


def reusable_shorts_queue_items(
    result: ShortsQueueResult,
    settings: Settings | None = None,
) -> dict[str, ShortsQueueItemResult]:
    """interrupted manifest から安全に再利用できる item だけを抽出する."""
    if result.status != "interrupted":
        return {}
    settings = settings or get_settings()
    specs = {spec.target_id: spec for spec in result.clip_specs}
    return {
        item.target_id: item
        for item in result.items
        if item.target_id in specs
        and can_reuse_shorts_queue_item(
            video_id=result.video_id,
            spec=specs[item.target_id],
            item=item,
            settings=settings,
        )
    }


def can_reserve_shorts_queue_item(
    result: ShortsQueueResult,
    item: ShortsQueueItemResult,
    settings: Settings | None = None,
) -> bool:
    """予約直前に成果物の存在と、現行 schema の証跡を再検証する.

    schema 1 の正常 done manifest は読み込み互換を保つため、spec から導く
    canonical output path と一致する非 symlink の非空ファイルだけを確認する。
    schema 2 以降は入力・出力 fingerprint まで一致しない成功 item を予約対象に
    しない。
    """
    settings = settings or get_settings()
    if item.status != "succeeded" or item.output_path is None:
        return False
    spec = next(
        (value for value in result.clip_specs if value.target_id == item.target_id),
        None,
    )
    if spec is None:
        return False
    if result.schema_version == LEGACY_SCHEMA_VERSION:
        try:
            expected_path = confined_video_path(
                settings.data_dir,
                _safe_path_identifier(result.video_id, "動画 ID"),
                "shorts",
                "output",
                spec.output_name,
                label="ショート量産成果物",
            )
            validate_confined_candidate(
                settings.data_dir, item.output_path, label="ショート量産成果物"
            )
            if _normalized_lexical_path(item.output_path) != _normalized_lexical_path(
                expected_path
            ):
                return False
            if _has_symlink_component(item.output_path, settings.data_dir):
                return False
            return item.output_path.is_file() and item.output_path.stat().st_size > 0
        except (OSError, PathConfinementError):
            return False
    return can_reuse_shorts_queue_item(
        video_id=result.video_id,
        spec=spec,
        item=item,
        settings=settings,
    )


def _queue_owner_is_live(result: ShortsQueueResult, settings: Settings) -> bool:
    """H1-1 の job owner が生きている間は queue を interrupted にしない."""
    try:
        from yt_live_kit.services.jobs import _owner_is_live, read_job

        job = read_job(result.owner_job_id or result.job_id, settings)
    except Exception:
        return False
    if job is None or job.job_id != result.owner_job_id or job.status != "running":
        return False
    return _owner_is_live(job, settings)


@contextmanager
def _manifest_lock(path: Path) -> Iterator[None]:
    """manifest の read-modify-write を process 境界で直列化する."""
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise ShortsQueueError("ショート量産 manifest の排他を取得できませんでした。") from exc


def _write_manifest_unlocked(result: ShortsQueueResult, settings: Settings) -> None:
    _validate_result_paths(result, settings)
    path = result.manifest_path
    text = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    try:
        write_text_atomically(path, text)
    except OSError as exc:
        raise ShortsQueueError("ショート量産結果を保存できませんでした。") from exc


def _write_manifest(result: ShortsQueueResult, settings: Settings) -> None:
    with _manifest_lock(result.manifest_path):
        _write_manifest_unlocked(result, settings)


def run_shorts_queue(
    video_id: str,
    clip_specs: Sequence[ShortsQueueClipSpec],
    settings: Settings | None = None,
    *,
    job_id: str,
    on_progress: ShortsQueueProgressCallback = None,
    reuse_items: Mapping[str, ShortsQueueItemResult] | None = None,
) -> ShortsQueueResult:
    """確定済み target を順次生成し、唯一の writer として manifest を更新する."""
    settings = settings or get_settings()
    path = _manifest_path(video_id, job_id, settings)
    if not clip_specs:
        raise ShortsQueueError("生成対象を 1 件以上指定してください。")
    validated_specs = tuple(
        ShortsQueueClipSpec.from_dict(spec.to_dict()) for spec in clip_specs
    )
    for spec in validated_specs:
        _validate_spec_artifact_lineage(video_id, spec, settings)
    if len({spec.output_name for spec in validated_specs}) != len(validated_specs):
        raise ShortsQueueError("同じ出力先になる生成対象があります。")
    now = datetime.now(timezone.utc)
    result = ShortsQueueResult(
        video_id=video_id,
        job_id=job_id,
        owner_job_id=job_id,
        status="running",
        created_at=now,
        updated_at=now,
        clip_specs=validated_specs,
        items=(),
        success_count=0,
        failure_count=0,
        manifest_path=path,
    )
    _write_manifest(result, settings)
    items: list[ShortsQueueItemResult] = []
    total = len(validated_specs)
    for index, spec in enumerate(validated_specs, start=1):
        reusable_item = reuse_items.get(spec.target_id) if reuse_items else None
        if reusable_item is not None and can_reuse_shorts_queue_item(
            video_id=video_id,
            spec=spec,
            item=reusable_item,
            settings=settings,
        ):
            items.append(reusable_item)
            if on_progress is not None:
                on_progress(index, total, f"{spec.target_id}: 安全に再利用")
            updated = datetime.now(timezone.utc)
            result = ShortsQueueResult(
                video_id=video_id,
                job_id=job_id,
                owner_job_id=job_id,
                status="done" if index == total else "running",
                created_at=now,
                updated_at=updated,
                clip_specs=validated_specs,
                items=tuple(items),
                success_count=sum(value.status == "succeeded" for value in items),
                failure_count=sum(value.status == "failed" for value in items),
                manifest_path=path,
            )
            _write_manifest(result, settings)
            continue
        document = spec.telop_document

        def report_inner(_current: int, _total: int, message: str) -> None:
            if on_progress is not None:
                on_progress(index - 1, total, f"{spec.target_id}: {message}")

        try:
            short = build_short_from_segments(
                video_id,
                [segment.to_tuple() for segment in spec.segments],
                settings,
                layout=spec.layout,
                telop_script=document,
                hook_text=document.hook_text,
                preset=spec.preset,
                hook_preset=spec.hook_preset,
                output_name=spec.output_name,
                ffmpeg_path=settings.ffmpeg_path,
                on_progress=report_inner,
            )
        except ShortsError as exc:
            item = ShortsQueueItemResult(
                target_id=spec.target_id,
                status="failed",
                output_path=None,
                log_path=None,
                font_warning=None,
                title_candidates=tuple(document.title_candidates),
                description=document.description,
                tags=tuple(document.tags),
                error=str(exc),
                input_fingerprint=make_shorts_queue_input_fingerprint(video_id, spec),
            )
        else:
            output_fingerprint: str | None = None
            try:
                output_fingerprint = make_shorts_queue_output_fingerprint(
                    short.output_path, settings
                )
            except ShortsQueueError:
                # fake / legacy builder が path だけ返す場合も結果自体は保持する。
                # fingerprint が無い item は recovery 時に自動再利用しない。
                output_fingerprint = None
            item = ShortsQueueItemResult(
                target_id=spec.target_id,
                status="succeeded",
                output_path=short.output_path,
                log_path=short.command_log_path,
                font_warning=short.font_warning,
                title_candidates=tuple(document.title_candidates),
                description=document.description,
                tags=tuple(document.tags),
                error=None,
                input_fingerprint=make_shorts_queue_input_fingerprint(video_id, spec),
                output_fingerprint=output_fingerprint,
            )
        items.append(item)
        if on_progress is not None:
            label = "完了" if item.status == "succeeded" else "失敗"
            on_progress(index, total, f"{spec.target_id}: {label}")
        updated = datetime.now(timezone.utc)
        result = ShortsQueueResult(
            video_id=video_id,
            job_id=job_id,
            owner_job_id=job_id,
            status="done" if index == total else "running",
            created_at=now,
            updated_at=updated,
            clip_specs=validated_specs,
            items=tuple(items),
            success_count=sum(value.status == "succeeded" for value in items),
            failure_count=sum(value.status == "failed" for value in items),
            manifest_path=path,
        )
        _write_manifest(result, settings)
    return result


def run_shorts_queue_job_target(
    *,
    report: Any,
    settings: Settings,
    job_id: str,
    video_id: str,
    clip_spec_dicts: list[dict[str, object]],
    resume_job_id: str | None = None,
) -> None:
    """start_job 用 target。spec 再構築と進捗 bridge だけを担当する."""
    specs = tuple(ShortsQueueClipSpec.from_dict(item) for item in clip_spec_dicts)
    reuse_items: dict[str, ShortsQueueItemResult] = {}
    if resume_job_id is not None:
        previous = load_shorts_queue_result(video_id, resume_job_id, settings)
        if previous is None or previous.status != "interrupted":
            raise ShortsQueueError("再実行元のショート量産結果が interrupted ではありません。")
        reuse_items = reusable_shorts_queue_items(previous, settings)

    def on_progress(current: int, total: int, message: str) -> None:
        report(current=current, total=total, message=message)

    run_shorts_queue(
        video_id,
        specs,
        settings,
        job_id=job_id,
        on_progress=on_progress,
        reuse_items=reuse_items,
    )


def _read_shorts_queue_result(
    video_id: str,
    job_id: str,
    settings: Settings,
) -> ShortsQueueResult | None:
    path = _manifest_path(video_id, job_id, settings)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = ShortsQueueResult.from_dict(raw, manifest_path=path)
        if result.video_id != video_id or result.job_id != job_id:
            raise ShortsQueueError(
                "manifest の動画 ID またはジョブ ID が保存先と一致しません。"
            )
        _validate_result_paths(result, settings)
        return result
    except ShortsQueueError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ShortsQueueError("ショート量産結果を読み込めませんでした。") from exc


def recover_shorts_queue_result(
    video_id: str,
    job_id: str,
    settings: Settings | None = None,
) -> ShortsQueueResult | None:
    """再起動後の孤児 queue を idempotent に recovery する.

    Recovery table は ``RECOVERY_TABLE`` が正本である。正常完了は不変、live
    owner の running は不変、owner 不在の running だけを interrupted へ atomic
    保存する。interrupted は terminal のため、途中 item を done に relabel
    しない。manifest が壊れている場合は元ファイルへ書き戻さず例外で fail
    closed にする。
    """
    settings = settings or get_settings()
    path = _manifest_path(video_id, job_id, settings)
    if not path.is_file():
        return None
    with _manifest_lock(path):
        result = _read_shorts_queue_result(video_id, job_id, settings)
        if result is None or result.status != "running":
            return result
        if _queue_owner_is_live(result, settings):
            return result
        recovered = ShortsQueueResult(
            video_id=result.video_id,
            job_id=result.job_id,
            owner_job_id=result.owner_job_id,
            status="interrupted",
            created_at=result.created_at,
            updated_at=datetime.now(timezone.utc),
            clip_specs=result.clip_specs,
            items=result.items,
            success_count=result.success_count,
            failure_count=result.failure_count,
            manifest_path=result.manifest_path,
            schema_version=result.schema_version,
        )
        _write_manifest_unlocked(recovered, settings)
        return recovered


def load_shorts_queue_result(
    video_id: str,
    job_id: str,
    settings: Settings | None = None,
) -> ShortsQueueResult | None:
    """指定 job ID の manifest を読み、読込元 path を結果へ注入する."""
    settings = settings or get_settings()
    result = recover_shorts_queue_result(video_id, job_id, settings)
    return result


def load_latest_shorts_queue_result(
    video_id: str,
    settings: Settings | None = None,
) -> ShortsQueueResult | None:
    """現在動画の検証済み manifest を作成日時・job ID の降順で返す."""
    settings = settings or get_settings()
    queue_dir = _queue_dir(video_id, settings)
    if not queue_dir.is_dir():
        return None
    results: list[ShortsQueueResult] = []
    for path in queue_dir.glob("queue_*.json"):
        job_id = path.stem.removeprefix("queue_")
        result = load_shorts_queue_result(video_id, job_id, settings)
        if result is not None:
            results.append(result)
    if not results:
        return None
    return max(results, key=lambda value: (value.created_at, value.job_id))
