"""アプリケーション設定."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """yt-live-kit の設定."""

    model_config = SettingsConfigDict(
        env_prefix="YTLK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("./data"), description="成果物ルートディレクトリ")
    sleep: float = Field(default=1.0, ge=0, description="URL 間のスリープ秒数")
    ytdlp_path: str = Field(default="yt-dlp", description="yt-dlp バイナリパス")
    ffmpeg_path: str = Field(default="ffmpeg", description="ffmpeg バイナリパス")
    subtitle_font: str | None = Field(
        default=None,
        description="字幕焼き込み用フォント名（未指定時は自動検出）",
    )
    youtube_client_secret: Path = Field(
        default=Path("./data/_config/client_secret.json"),
        description="YouTube Data API の OAuth クライアントシークレット JSON のパス",
    )
    video_upload_daily_limit: int = Field(
        default=100,
        ge=1,
        le=100,
        description="America/Los_Angeles 暦日あたりの upload attempt 上限",
    )

    def ensure_data_dir(self) -> Path:
        """data ディレクトリを作成して返す."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def get_settings() -> Settings:
    """設定インスタンスを返す."""
    return Settings()
