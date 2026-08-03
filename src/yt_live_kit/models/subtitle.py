"""字幕 source artifact のメタデータモデル。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubtitleSourceMetadata(BaseModel):
    """取得済み YouTube VTT source の不変メタデータ。"""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    source_kind: Literal["youtube_vtt"] = "youtube_vtt"
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    language: Literal["ja-orig", "ja"]
    source_url: str = Field(min_length=1)
    ytdlp_version: str = Field(min_length=1)
    byte_size: int = Field(ge=1)
    source_path: str = Field(pattern=r"^subtitles/sources/[0-9a-f]{64}\.vtt$")
    canonical_path: Literal["subtitles/ja.vtt"] = "subtitles/ja.vtt"
    canonical_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_compatible: bool
    fetched_at: datetime

    @model_validator(mode="after")
    def validate_cross_field_consistency(self) -> "SubtitleSourceMetadata":
        expected_source_path = (
            f"subtitles/sources/{self.source_fingerprint}.vtt"
        )
        if self.source_path != expected_source_path:
            raise ValueError("source_path が source_fingerprint と一致しません。")
        if self.canonical_compatible != (
            self.content_sha256 == self.canonical_content_sha256
        ):
            raise ValueError("canonical compatibility と content hash が一致しません。")
        return self
