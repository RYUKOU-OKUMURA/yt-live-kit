"""切り抜き候補モデル."""

from pydantic import BaseModel, Field


class ClipCandidate(BaseModel):
    """切り抜き候補 1 件."""

    id: str = Field(description="候補 ID（例: clip_001）")
    title: str = Field(description="切り抜きタイトル案")
    start: str = Field(description="開始時刻（HH:MM:SS または M:SS）")
    end: str = Field(description="終了時刻（HH:MM:SS または M:SS）")
    duration_sec: int = Field(description="区間長（秒）")
    reason: str = Field(description="選定理由")


class ClipCandidatesDocument(BaseModel):
    """candidates.json のルートオブジェクト."""

    candidates: list[ClipCandidate] = Field(description="切り抜き候補リスト")
