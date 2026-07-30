"""動画メタデータモデル."""

from datetime import datetime

from pydantic import BaseModel, Field


class VideoMeta(BaseModel):
    """yt-dlp で取得した動画メタデータ."""

    id: str = Field(description="YouTube 動画 ID")
    title: str = Field(description="動画タイトル")
    url: str = Field(description="入力 URL")
    upload_date: str | None = Field(default=None, description="公開日 (YYYYMMDD)")
    duration: int | None = Field(default=None, description="動画長（秒）")
    ytdlp_version: str = Field(description="取得に使用した yt-dlp バージョン")
    fetched_at: datetime = Field(description="取得日時")
    subtitle_lang: str | None = Field(default=None, description="保存した字幕言語 (ja-orig / ja)")
