"""ショート動画用テロップ台本モデル."""

from pydantic import BaseModel, Field


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
