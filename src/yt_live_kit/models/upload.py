"""予約投稿アップロードの永続データモデル."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

UploadState = Literal[
    "reserved", "uploading", "uploaded", "failed", "needs_reconciliation"
]
PollPhase = Literal["processing", "publication"]
PollClassification = Literal[
    "processing",
    "processing_succeeded",
    "processing_failed",
    "processing_timeout",
    "scheduled",
    "suspected_private_lock",
    "published",
    "publication_timeout",
]
PublicationEligibility = Literal[
    "unknown",
    "scheduled",
    "suspected_private_lock",
    "confirmed_private_lock",
    "no_private_lock",
    "published",
]
RelatedVideoStatus = Literal["not_ready", "pending", "confirmed"]


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} はタイムゾーン付き日時にしてください。")
    return value.astimezone(timezone.utc)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("YouTube の状態応答に未対応の値が含まれています。")


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


class UploadChannel(_FrozenModel):
    """実際に認証された YouTube チャンネル."""

    channel_id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class UploadDescriptionRequirementsSnapshot(_FrozenModel):
    """投稿用ショート概要欄の不変要件 snapshot.

    ``services.description.ShortsDescriptionRequirements`` は純粋 validator
    用の dataclass であり、永続層から service 層へ逆向き import しないため、
    queue / preview にはこの Pydantic model を保存する。legacy queue では
    field 自体が無いため、親 model 側で optional default を許容する。
    """

    generated_description: str
    source_title: str
    source_url: str
    fixed_cta: str
    template_bytes_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    meta_json_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_description_occurrences: int = Field(ge=0)
    source_title_occurrences: int = Field(ge=0)
    source_url_occurrences: int = Field(ge=0)
    fixed_cta_line_occurrences: int = Field(ge=0)


# 呼び出し側が P6-2 の名称をそのまま参照できる互換 alias。
ShortsDescriptionRequirementsSnapshot = UploadDescriptionRequirementsSnapshot


class UploadContentSnapshot(_FrozenModel):
    """確認画面と API body を結ぶ不変スナップショット."""

    channel: UploadChannel
    video_path: Path
    file_size: int = Field(gt=0)
    file_mtime_ns: int = Field(ge=0)
    duration_sec: float = Field(ge=10, le=180)
    title: str = Field(min_length=1, max_length=100)
    description: str
    tags: tuple[str, ...]
    publish_at: datetime
    privacy_status: Literal["private"]
    notify_subscribers: Literal[False]
    self_declared_made_for_kids: StrictBool
    contains_synthetic_media: StrictBool
    community_guidelines_confirmed: Literal[True]
    community_guidelines_confirmed_at: datetime
    # P4 / P2 の legacy operation はこの field が無くても読み込める。
    # P6 の新規 Shorts preview / confirm では schedule service が必須化する。
    requirements: UploadDescriptionRequirementsSnapshot | None = None

    @field_validator("video_path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("動画ファイルは絶対パスで指定してください。")
        return value

    @field_validator("publish_at")
    @classmethod
    def _publish_at_utc(cls, value: datetime) -> datetime:
        return _utc(value, "公開予定日時")

    @field_validator("community_guidelines_confirmed_at")
    @classmethod
    def _confirmed_at_utc(cls, value: datetime) -> datetime:
        return _utc(value, "ガイドライン確認日時")


class UploadStatusObservation(_FrozenModel):
    """YouTube status poll の追記専用観測値."""

    polled_at: datetime
    phase: PollPhase
    status: Mapping[str, Any]
    processing_details: Mapping[str, Any]
    classification: PollClassification
    error: str | None

    @field_validator("polled_at")
    @classmethod
    def _polled_at_utc(cls, value: datetime) -> datetime:
        return _utc(value, "取得日時")

    @field_validator("status", "processing_details", mode="after")
    @classmethod
    def _immutable_mapping(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _deep_freeze(value)

    @field_serializer("status", "processing_details")
    def _serialize_mapping(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _deep_thaw(value)

    @model_validator(mode="after")
    def _phase_and_classification_match(self) -> UploadStatusObservation:
        processing = {
            "processing",
            "processing_succeeded",
            "processing_failed",
            "processing_timeout",
        }
        publication = {
            "scheduled",
            "suspected_private_lock",
            "published",
            "publication_timeout",
            "processing_failed",
        }
        allowed = processing if self.phase == "processing" else publication
        if self.classification not in allowed:
            raise ValueError("poll phase と判定結果が一致しません。")
        failures = {
            "processing_failed",
            "processing_timeout",
            "suspected_private_lock",
            "publication_timeout",
        }
        if self.classification in failures and not self.error:
            raise ValueError("poll の失敗・timeout 判定にはエラーが必要です。")
        if self.classification not in failures and self.error is not None:
            raise ValueError("poll の正常判定にエラーは設定できません。")
        return self


class UploadOperation(_FrozenModel):
    """queue.json に保存する予約枠兼アップロード operation."""

    operation_id: str = Field(min_length=1)
    source_video_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    video_path: Path
    content: UploadContentSnapshot
    state: UploadState
    job_id: str = Field(min_length=1)
    video_id: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    poll_history: tuple[UploadStatusObservation, ...]
    publication_eligibility: PublicationEligibility
    related_video_status: RelatedVideoStatus = "not_ready"
    related_video_confirmed_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_related_video_fields(cls, value: Any) -> Any:
        """旧 queue の欠落 field を状態から安全側へ移行する."""
        if not isinstance(value, Mapping):
            return value
        migrated = dict(value)
        if "related_video_status" not in migrated:
            migrated["related_video_status"] = (
                "pending"
                if migrated.get("state") == "uploaded" and migrated.get("video_id")
                else "not_ready"
            )
        if "related_video_confirmed_at" not in migrated:
            migrated["related_video_confirmed_at"] = None
        return migrated

    @field_validator("video_path")
    @classmethod
    def _operation_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("動画ファイルは絶対パスで指定してください。")
        return value

    @field_validator(
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "related_video_confirmed_at",
    )
    @classmethod
    def _operation_utc(cls, value: datetime | None, info) -> datetime | None:
        return None if value is None else _utc(value, info.field_name)

    @model_validator(mode="after")
    def _consistent(self) -> UploadOperation:
        if self.video_path != self.content.video_path:
            raise ValueError("operation と確認内容の動画パスが一致しません。")
        if self.updated_at < self.created_at:
            raise ValueError("operation の更新日時が作成日時より前です。")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("operation の開始日時が作成日時より前です。")
        if self.finished_at is not None:
            lower = self.started_at or self.created_at
            if self.finished_at < lower:
                raise ValueError("operation の完了日時が開始日時より前です。")
            if self.updated_at < self.finished_at:
                raise ValueError("operation の更新日時が完了日時より前です。")
        if self.state == "reserved":
            if (
                self.started_at is not None
                or self.finished_at is not None
                or self.video_id is not None
                or self.error is not None
            ):
                raise ValueError("予約済み operation に実行結果は設定できません。")
        elif self.state == "uploading":
            if self.started_at is None:
                raise ValueError("アップロード中 operation には開始日時が必要です。")
            if (
                self.finished_at is not None
                or self.video_id is not None
                or self.error is not None
            ):
                raise ValueError("アップロード中 operation に完了結果は設定できません。")
        elif self.state == "uploaded":
            if self.started_at is None or self.finished_at is None or not self.video_id:
                raise ValueError("アップロード済み operation の時刻または動画 ID が不足しています。")
            if self.error is not None:
                raise ValueError("アップロード済み operation にエラーは設定できません。")
        else:
            if self.finished_at is None or not self.error:
                raise ValueError("失敗または照合待ち operation には完了日時とエラーが必要です。")
            if self.video_id is not None:
                raise ValueError("失敗または照合待ち operation に動画 ID は設定できません。")
        if self.state != "uploaded" and self.publication_eligibility != "unknown":
            raise ValueError("公開可否はアップロード済み operation にだけ設定できます。")
        if self.poll_history and self.state != "uploaded":
            raise ValueError("poll 履歴はアップロード済み operation にだけ設定できます。")
        if self.related_video_status == "not_ready":
            if self.state == "uploaded":
                raise ValueError("アップロード済み operation は関連動画確認待ちにしてください。")
            if self.related_video_confirmed_at is not None:
                raise ValueError("未準備の関連動画に確認日時は設定できません。")
        elif self.related_video_status == "pending":
            if self.state != "uploaded" or not self.video_id:
                raise ValueError("関連動画確認待ちはアップロード済み operation にだけ設定できます。")
            if self.related_video_confirmed_at is not None:
                raise ValueError("関連動画確認待ちに確認日時は設定できません。")
        elif self.related_video_status == "confirmed":
            if self.state != "uploaded" or not self.video_id:
                raise ValueError("関連動画確認済みはアップロード済み operation にだけ設定できます。")
            if self.related_video_confirmed_at is None:
                raise ValueError("関連動画確認済み operation には確認日時が必要です。")
            if (
                self.finished_at is not None
                and self.related_video_confirmed_at < self.finished_at
            ):
                raise ValueError("関連動画確認日時が upload 完了日時より前です。")
            if self.related_video_confirmed_at > self.updated_at:
                raise ValueError("関連動画確認日時が operation 更新日時より後です。")
        times = [item.polled_at for item in self.poll_history]
        if times != sorted(times):
            raise ValueError("poll 履歴は取得時刻順にしてください。")
        if self.finished_at is not None and times and times[0] < self.finished_at:
            raise ValueError("poll 履歴の取得日時が upload 完了日時より前です。")
        return self

    @property
    def latest_observation(self) -> UploadStatusObservation | None:
        return self.poll_history[-1] if self.poll_history else None


class UploadResult(_FrozenModel):
    """resumable upload の型付き結果."""

    state: Literal["uploaded", "failed", "needs_reconciliation"]
    video_id: str | None
    error: str | None

    @model_validator(mode="after")
    def _result_consistent(self) -> UploadResult:
        if self.state == "uploaded" and not self.video_id:
            raise ValueError("成功結果には YouTube 動画 ID が必要です。")
        if self.state == "uploaded" and self.error is not None:
            raise ValueError("成功結果にエラーは設定できません。")
        if self.state != "uploaded" and not self.error:
            raise ValueError("失敗結果には日本語エラーが必要です。")
        if self.state != "uploaded" and self.video_id is not None:
            raise ValueError("失敗結果に動画 ID は設定できません。")
        return self
