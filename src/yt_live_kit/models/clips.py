"""切り抜き候補モデル。S9 の coarse VTT lineage は document root に保持する。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"


class ClipCandidate(BaseModel):
    """切り抜き候補 1 件."""

    id: str = Field(description="候補 ID（例: clip_001）")
    title: str = Field(description="切り抜きタイトル案")
    start: str = Field(description="開始時刻（HH:MM:SS または M:SS）")
    end: str = Field(description="終了時刻（HH:MM:SS または M:SS）")
    duration_sec: int = Field(description="区間長（秒）")
    reason: str = Field(description="選定理由")


class ClipCandidatesLineage(BaseModel):
    """候補探索に使った YouTube VTT の不変 provenance。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_kind: Literal["youtube_vtt"] = "youtube_vtt"
    coarse_vtt_artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    coarse_full_cue_digest: str = Field(pattern=_FINGERPRINT_PATTERN)
    candidate_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @property
    def coarse_artifact_fingerprint(self) -> str:
        """実行計画の短い名称との互換 alias。"""

        return self.coarse_vtt_artifact_fingerprint

    @property
    def coarse_cue_digest(self) -> str:
        return self.coarse_full_cue_digest


class ClipCandidatesDocument(BaseModel):
    """candidates.json のルートオブジェクト."""

    candidates: list[ClipCandidate] = Field(description="切り抜き候補リスト")
    # 旧 candidates.json には存在しない任意 field。候補自体の shape と表示順は
    # 変えず、S9-4 が後続へ伝播できる root snapshot だけを追加する。
    lineage: ClipCandidatesLineage | None = Field(default=None)
