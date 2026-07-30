"""チャンネル配信アーカイブ一覧モデル."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChannelVideo(BaseModel):
    """チャンネル内の配信アーカイブ 1 件."""

    video_id: str = Field(description="YouTube 動画 ID")
    title: str = Field(description="動画タイトル")
    url: str = Field(description="動画 URL")
    duration: int | None = Field(default=None, description="動画長（秒）")
    upload_date: str | None = Field(default=None, description="公開日 (YYYYMMDD)")


class ChannelListDocument(BaseModel):
    """チャンネル配信アーカイブ一覧のキャッシュドキュメント."""

    channel_url: str = Field(description="正規化済みチャンネル URL（/streams）")
    handle: str = Field(description="キャッシュファイル名用ハンドル")
    fetched_at: datetime = Field(description="一覧取得日時")
    videos: list[ChannelVideo] = Field(default_factory=list, description="配信アーカイブ一覧")
