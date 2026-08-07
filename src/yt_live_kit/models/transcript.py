"""S9 の不変な字幕成果物モデル。

このモジュールは既存の ``services.transcript`` が生成する full / compressed
テキストとは別の、字幕の provenance を保存するための型だけを定義する。
S9 で扱う時刻はすべて整数 millisecond であり、Pydantic の暗黙の数値変換を
許可しない。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION: Final = 1
# mypy は Literal[...] の引数に変数を書けないため、型は別名で明示する。
# 値と型の両方を一箇所で持ち、schema version を上げるときは 2 行を同時に直す。
TranscriptSchemaVersion = Literal[1]
FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"


class TranscriptSourceKind(str, Enum):
    """字幕の取得元。S9 初版ではこの 2 種類だけを扱う。"""

    YOUTUBE_VTT = "youtube_vtt"
    WHISPER_CPP = "whisper_cpp"


class TranscriptArtifactStatus(str, Enum):
    """artifact / 区間の処理状態。"""

    SUCCESS = "success"
    FALLBACK = "fallback"
    FAILED = "failed"
    PARTIAL = "partial"


class TranscriptResolverUse(str, Enum):
    """resolver の用途。候補探索と選択区間精査を混ぜない。"""

    COARSE_SEARCH = "coarse_search"
    SELECTED_RANGE = "selected_range"


# 短い別名は S9-3 / S9-4 側の import を読みやすくするために公開する。
SourceKind = TranscriptSourceKind
ArtifactStatus = TranscriptArtifactStatus
ResolverUse = TranscriptResolverUse


def _ensure_json_value(value: Any, label: str) -> Any:
    """設定・runtime metadata が canonical JSON にできることを検証する。"""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} に有限でない数値は指定できません。")
        # float の丸め・文字列化をここでは行わない。fingerprint はこの値を
        # json.dumps の元の数値表現で扱い、range 時刻には float を許可しない。
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} のキーは文字列で指定してください。")
            result[key] = _ensure_json_value(item, f"{label}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_ensure_json_value(item, label) for item in value]
    raise ValueError(f"{label} は JSON で表現できる値にしてください。")


class _StrictFrozenModel(BaseModel):
    """S9 artifact 全体で共有する strict / immutable 設定。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )


class TranscriptCue(_StrictFrozenModel):
    """元動画基準の絶対時刻を持つ字幕 cue。"""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _text_is_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cue の本文は空にできません。")
        if "\x00" in cleaned:
            raise ValueError("cue の本文に制御文字を指定できません。")
        return cleaned

    @model_validator(mode="after")
    def _range_is_valid(self) -> "TranscriptCue":
        if self.end_ms <= self.start_ms:
            raise ValueError("cue の終了時刻は開始時刻より後である必要があります。")
        return self


class TranscriptRange(_StrictFrozenModel):
    """artifact が入力・精査した絶対時刻 range。"""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    # padding_ms は S9-1 の設定をそのまま記録するための対称値。
    # before / after は将来の非対称 padding に備えた明示値で、通常は 0。
    padding_ms: int = Field(default=0, ge=0)
    padding_before_ms: int = Field(default=0, ge=0)
    padding_after_ms: int = Field(default=0, ge=0)
    inclusion_rule: str = Field(default="half_open_overlap", min_length=1)
    status: TranscriptArtifactStatus = TranscriptArtifactStatus.SUCCESS
    cue_digest: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    used_range_cue_digest: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )

    @field_validator("status", mode="before")
    @classmethod
    def _status_from_json(cls, value: TranscriptArtifactStatus | str) -> TranscriptArtifactStatus:
        if isinstance(value, TranscriptArtifactStatus):
            return value
        try:
            return TranscriptArtifactStatus(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("区間 status が正しくありません。") from exc

    @field_validator("inclusion_rule")
    @classmethod
    def _inclusion_rule_is_safe(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "<" in cleaned or ">" in cleaned:
            raise ValueError("cue inclusion rule が正しくありません。")
        return cleaned

    @model_validator(mode="after")
    def _range_is_valid(self) -> "TranscriptRange":
        if self.end_ms <= self.start_ms:
            raise ValueError("対象区間の終了時刻は開始時刻より後である必要があります。")
        return self

    @property
    def effective_padding_before_ms(self) -> int:
        return self.padding_before_ms or self.padding_ms

    @property
    def effective_padding_after_ms(self) -> int:
        return self.padding_after_ms or self.padding_ms


class TranscriptArtifact(_StrictFrozenModel):
    """取得元・設定・cue digest とともに固定した字幕成果物。

    ``model``, ``runtime``, ``settings`` は候補モデルや whisper-cli の capability
    が増えても top-level schema を変更せずに記録できる metadata namespace である。
    それ自体は JSON 値として検証されるため、Path / bytes / 任意 object は保存できない。
    """

    schema_version: TranscriptSchemaVersion = SCHEMA_VERSION
    source_kind: TranscriptSourceKind
    video_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    source_ref: str = Field(min_length=1)
    source_url: str | None = None
    source_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    source_content_sha256: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(min_length=1)
    ranges: tuple[TranscriptRange, ...] = Field(min_length=1)
    cues: tuple[TranscriptCue, ...] = ()
    status: TranscriptArtifactStatus = TranscriptArtifactStatus.SUCCESS
    model: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    audio_input_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    cache_identity: str = Field(pattern=FINGERPRINT_PATTERN)
    cue_digest: str = Field(pattern=FINGERPRINT_PATTERN)
    used_range_cue_digests: tuple[str, ...] = ()
    artifact_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    created_at: datetime

    @field_validator("source_kind", "status", mode="before")
    @classmethod
    def _enum_from_json(
        cls,
        value: TranscriptSourceKind | TranscriptArtifactStatus | str,
        info,
    ) -> TranscriptSourceKind | TranscriptArtifactStatus:
        enum_type = (
            TranscriptSourceKind if info.field_name == "source_kind" else TranscriptArtifactStatus
        )
        if isinstance(value, enum_type):
            return value
        try:
            return enum_type(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{info.field_name} が正しくありません。") from exc

    @field_validator("ranges", "cues", "used_range_cue_digests", mode="before")
    @classmethod
    def _ordered_sequence_from_json(cls, value: Any) -> tuple[Any, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at_from_json(cls, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("created_at の日時形式が正しくありません。") from exc
        return value

    @field_validator("source_ref")
    @classmethod
    def _source_ref_is_relative(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("字幕 source ref が正しくありません。")
        # URL は source_url と分けて保持するが、既存 fixture の参照互換のため
        # schema では許可し、filesystem path の検査は store 側で行う。
        if cleaned.startswith(("http://", "https://")):
            return cleaned
        path = Path(cleaned)
        if path.is_absolute() or "\\" in cleaned or any(
            part in {".", ".."} for part in path.parts
        ):
            raise ValueError("字幕 source ref はデータディレクトリ内の相対パスにしてください。")
        return cleaned

    @field_validator("source_url")
    @classmethod
    def _source_url_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("字幕 source URL が正しくありません。")
        return cleaned

    @field_validator("source_metadata", "model", "runtime", "settings")
    @classmethod
    def _metadata_is_json(cls, value: dict[str, Any], info) -> dict[str, Any]:
        return _ensure_json_value(value, info.field_name)

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at はタイムゾーン付き日時にしてください。")
        return value.astimezone(timezone.utc)

    @field_validator("used_range_cue_digests")
    @classmethod
    def _used_digests_are_hex(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            if len(digest) != 64:
                raise ValueError("used_range_cue_digest が正しくありません。")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise ValueError("used_range_cue_digest が正しくありません。") from exc
        return tuple(digest.lower() for digest in value)

    @model_validator(mode="after")
    def _artifact_is_consistent(self) -> "TranscriptArtifact":
        if len(self.used_range_cue_digests) not in {0, len(self.ranges)}:
            raise ValueError("used_range_cue_digest の個数が対象区間と一致しません。")

        # Whisper の success は、後続 resolver が「精査済み」と判断するための
        # provenance を必ず伴う。VTT / fallback は従来どおり metadata namespace
        # を要求しない。partial / failed は failure 診断を保存できるよう型として
        # 作成できるが、is_high_precision は常に false になる。
        if self.source_kind == TranscriptSourceKind.WHISPER_CPP and self.status == TranscriptArtifactStatus.SUCCESS:
            if not self.model:
                raise ValueError("Whisper success artifact には model provenance が必要です。")
            if not self.runtime:
                raise ValueError("Whisper success artifact には runtime provenance が必要です。")
            if not self.settings:
                raise ValueError("Whisper success artifact には settings provenance が必要です。")
            if not self.audio_input_fingerprint:
                raise ValueError("Whisper success artifact には audio input fingerprint が必要です。")

        # YouTube VTT の success は、永続 cache から再読込した時に元の
        # bytes と照合できる provenance を必ず持つ。URL だけの参照は
        # immutable source path として再検証できないため許可しない。
        if self.source_kind == TranscriptSourceKind.YOUTUBE_VTT and self.status == TranscriptArtifactStatus.SUCCESS:
            if self.source_ref.startswith(("http://", "https://")):
                raise ValueError("YouTube VTT success artifact には source path が必要です。")
            if not self.source_fingerprint:
                raise ValueError("YouTube VTT success artifact には source fingerprint が必要です。")
            if not self.source_content_sha256:
                raise ValueError("YouTube VTT success artifact には VTT bytes fingerprint が必要です。")

        if self.status == TranscriptArtifactStatus.SUCCESS and any(
            item.status != TranscriptArtifactStatus.SUCCESS for item in self.ranges
        ):
            raise ValueError("success artifact に失敗した区間を含めることはできません。")

        for cue in self.cues:
            if not any(
                cue.start_ms >= item.start_ms - item.effective_padding_before_ms
                and cue.end_ms <= item.end_ms + item.effective_padding_after_ms
                for item in self.ranges
            ):
                raise ValueError("cue が artifact の対象区間外にあります。")
        return self

    @property
    def used_range_cue_digest(self) -> str | None:
        """単一区間 artifact の利便性 alias。複数区間では None。"""

        if len(self.used_range_cue_digests) != 1:
            return None
        return self.used_range_cue_digests[0]

    @property
    def audio_spans_declare_route(self) -> bool:
        """全音声 span が S9-6 以降の取得経路を記録しているかを返す.

        S9-6 で修正した開始ずれは、経路を記録していなかった旧 span 取得で
        起きた。旧 artifact は benchmark の不変証跡として disk に残ってよい
        が、高精度字幕として再利用させない。
        """

        if self.source_kind != TranscriptSourceKind.WHISPER_CPP:
            return True
        spans = self.source_metadata.get("audio_spans")
        if not isinstance(spans, list) or not spans:
            return False
        for span in spans:
            if not isinstance(span, dict):
                return False
            route = span.get("audio_route")
            if not isinstance(route, str) or not route.strip():
                return False
        return True

    @property
    def is_high_precision(self) -> bool:
        return (
            self.source_kind == TranscriptSourceKind.WHISPER_CPP
            and self.status == TranscriptArtifactStatus.SUCCESS
            and all(item.status == TranscriptArtifactStatus.SUCCESS for item in self.ranges)
            and bool(self.model)
            and bool(self.runtime)
            and bool(self.settings)
            and bool(self.audio_input_fingerprint)
            and self.audio_spans_declare_route
        )

    def canonical_dict(self) -> dict[str, Any]:
        """fingerprint 検証に使う JSON-compatible snapshot を返す。"""

        return self.model_dump(mode="json")


class TranscriptArtifactRef(_StrictFrozenModel):
    """downstream が resolver を再実行せず保持する immutable reference。"""

    schema_version: TranscriptSchemaVersion = SCHEMA_VERSION
    video_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    artifact_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    source_kind: TranscriptSourceKind
    path: str = Field(pattern=r"^transcripts/artifacts/[0-9a-f]{64}\.json$")

    @field_validator("source_kind", mode="before")
    @classmethod
    def _source_kind_from_json(cls, value: TranscriptSourceKind | str) -> TranscriptSourceKind:
        if isinstance(value, TranscriptSourceKind):
            return value
        try:
            return TranscriptSourceKind(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("artifact reference の source_kind が正しくありません。") from exc

    @model_validator(mode="after")
    def _path_matches_fingerprint(self) -> "TranscriptArtifactRef":
        expected = f"transcripts/artifacts/{self.artifact_fingerprint}.json"
        if self.path != expected:
            raise ValueError("artifact reference の path が fingerprint と一致しません。")
        return self


# 後続タスクや外部 fixture で使われることがある名称を明示的に公開する。
Cue = TranscriptCue
TranscriptRangeSpec = TranscriptRange
TranscriptArtifactModel = TranscriptArtifact
