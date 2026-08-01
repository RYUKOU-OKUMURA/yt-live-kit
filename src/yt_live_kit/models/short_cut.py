"""ショート用サブ区間（カットプラン）モデル."""

from pydantic import BaseModel, Field

from yt_live_kit.models.highlights import HighlightSegment


class ShortCutDocument(BaseModel):
    """cut_{parent_id}.json のルートオブジェクト."""

    parent_id: str = Field(description="親候補の ID（例: clip_002）")
    parent_start_ms: int = Field(description="親区間の開始（整数ミリ秒）")
    parent_end_ms: int = Field(description="親区間の終了（整数ミリ秒）")
    candidates: list[HighlightSegment] = Field(
        description="親区間内のサブ区間リスト（時系列順・非重複）"
    )
