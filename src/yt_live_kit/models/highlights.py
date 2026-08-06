"""ハイライト区間モデル."""

from pydantic import BaseModel, ConfigDict, Field


class HighlightSegment(BaseModel):
    """ハイライト区間 1 件."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="区間 ID（例: hl_001）")
    title: str = Field(description="区間タイトル案")
    start: str = Field(description="開始時刻（HH:MM:SS または M:SS）")
    end: str = Field(description="終了時刻（HH:MM:SS または M:SS）")
    duration_sec: int = Field(description="区間長（秒）")
    reason: str = Field(description="選定理由")


class HighlightsDocument(BaseModel):
    """segments.json のルートオブジェクト（candidates.json と同形式）."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[HighlightSegment] = Field(description="ハイライト区間リスト")
