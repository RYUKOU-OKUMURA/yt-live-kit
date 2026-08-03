"""ショート用サブ区間（カットプラン）モデル."""

from pydantic import BaseModel, Field, field_validator, model_validator

from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.transcript import TranscriptArtifactRef


class ShortCutDocument(BaseModel):
    """cut_{parent_id}.json のルートオブジェクト."""

    parent_id: str = Field(description="親候補の ID（例: clip_002）")
    parent_start_ms: int = Field(description="親区間の開始（整数ミリ秒）")
    parent_end_ms: int = Field(description="親区間の終了（整数ミリ秒）")
    candidates: list[HighlightSegment] = Field(
        description="親区間内のサブ区間リスト（時系列順・非重複）"
    )
    # 高精度化した区間だけを下流へ同一 artifact として結び付ける。
    # None/空 tuple は既存の VTT ベース cutplan との後方互換を保つ。
    artifact_ref: TranscriptArtifactRef | None = None
    artifact_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    used_range_cue_digests: tuple[str, ...] = ()

    @field_validator("used_range_cue_digests", mode="before")
    @classmethod
    def _tuple_digests(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("used_range_cue_digests は配列で指定してください。")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_artifact_lineage(self) -> "ShortCutDocument":
        has_any = (
            self.artifact_ref is not None
            or self.artifact_fingerprint is not None
            or bool(self.used_range_cue_digests)
        )
        if not has_any:
            return self
        if self.artifact_ref is None or self.artifact_fingerprint is None:
            raise ValueError("artifact lineage は ref/fingerprint/digest を一組で保存してください。")
        if self.artifact_fingerprint != self.artifact_ref.artifact_fingerprint:
            raise ValueError("artifact fingerprint が artifact ref と一致しません。")
        for digest in self.used_range_cue_digests:
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("used_range_cue_digest が正しくありません。")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise ValueError("used_range_cue_digest が正しくありません。") from exc
        return self
