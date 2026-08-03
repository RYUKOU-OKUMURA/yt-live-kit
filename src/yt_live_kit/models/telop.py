"""ショート動画用テロップ台本モデル."""

from pydantic import BaseModel, Field, field_validator, model_validator

from yt_live_kit.models.transcript import TranscriptArtifactRef


class TelopLine(BaseModel):
    """テロップ 1 行と元動画基準の絶対時刻."""

    text: str = Field(description="テロップ本文")
    start_sec: float = Field(description="元動画基準の開始秒")
    end_sec: float = Field(description="元動画基準の終了秒")
    emphasis: bool = Field(default=False, description="強調表示するか")


class TelopSegmentScript(BaseModel):
    """選択区間 1 件分のテロップ台本."""

    start_sec: float = Field(description="元動画基準の区間開始秒")
    end_sec: float = Field(description="元動画基準の区間終了秒")
    lines: list[TelopLine] = Field(description="区間内のテロップ行")


class TelopScriptDocument(BaseModel):
    """テロップ台本とショート動画用メタデータ."""

    hook_text: str = Field(description="冒頭フック文言")
    title_candidates: list[str] = Field(description="タイトル案")
    description: str = Field(description="説明文")
    tags: list[str] = Field(description="タグ")
    segments: list[TelopSegmentScript] = Field(description="区間別テロップ台本")
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
    def _validate_artifact_lineage(self) -> "TelopScriptDocument":
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
