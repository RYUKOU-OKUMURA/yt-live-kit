"""アプリケーション設定."""

import math
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# S9-1 の decision.adopted_model.settings_contract に固定した値。runtime 側でも
# 実体 fingerprint と capability を再検証するため、設定値だけで高精度結果を
# 信頼しない。
WHISPER_BINARY_PATH = "/opt/homebrew/bin/whisper-cli"
WHISPER_BINARY_VERSION = "1.9.1"
WHISPER_BINARY_SHA256 = (
    "1fbabb51a45906bd36684695de9025eab63618a6eedc26971c47fa5affc5fe49"
)
WHISPER_MODEL_NAME = "ggml-large-v3-turbo-q5_0"
WHISPER_MODEL_PATH = (
    "/Users/ryukouokumura/Library/Caches/whisper.cpp/models/"
    "ggml-large-v3-turbo-q5_0.bin"
)
WHISPER_MODEL_SHA256 = (
    "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
)
WHISPER_OUTPUT_SCHEMA = "whisper-cli-json-full-v1"
WHISPER_LANGUAGE = "ja"
WHISPER_INITIAL_PROMPT = (
    "日本語のライブ配信。固有名詞は Claude、Codex、Whisper、Ollama、Together AI、"
    "DirectX、Microsoft、Windows、Steam、DX12、iMac、Apple II、HHKB、macOS を含む。"
)


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
    ytdlp_timeout: int = Field(
        default=300,
        ge=1,
        description="yt-dlp の字幕・メタデータ取得タイムアウト秒数",
    )
    download_timeout: int = Field(
        default=3600,
        ge=1,
        description="yt-dlp の動画本体ダウンロードタイムアウト秒数",
    )
    ffmpeg_timeout: int = Field(
        default=3600,
        ge=1,
        description="ffmpeg / ffprobe のタイムアウト秒数",
    )
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

    # S9 runtime は固定した採用実体を preflight で照合する。パスや期待値を
    # env で明示的に差し替えることはできるが、実体との不一致は runtime が
    # fail closed で拒否する。自由な shell command は設定項目にしない。
    whisper_binary_path: str = Field(
        default=WHISPER_BINARY_PATH,
        min_length=1,
        description="whisper.cpp whisper-cli の実行ファイルパス",
    )
    whisper_binary_version: str = Field(
        default=WHISPER_BINARY_VERSION,
        min_length=1,
        description="期待する whisper.cpp の version",
    )
    whisper_binary_sha256: str = Field(
        default=WHISPER_BINARY_SHA256,
        min_length=64,
        max_length=64,
        description="期待する whisper-cli の SHA-256",
    )
    whisper_model_name: str = Field(
        default=WHISPER_MODEL_NAME,
        min_length=1,
        description="採用 whisper.cpp model 名",
    )
    whisper_model_path: str = Field(
        default=WHISPER_MODEL_PATH,
        min_length=1,
        description="採用 whisper.cpp model file パス",
    )
    whisper_model_sha256: str = Field(
        default=WHISPER_MODEL_SHA256,
        min_length=64,
        max_length=64,
        description="期待する whisper.cpp model の SHA-256",
    )
    whisper_output_schema: str = Field(
        default=WHISPER_OUTPUT_SCHEMA,
        min_length=1,
        description="保存を許可する whisper JSON schema",
    )
    whisper_language: str = Field(
        default=WHISPER_LANGUAGE,
        min_length=2,
        max_length=16,
        description="whisper の入力言語",
    )
    whisper_initial_prompt: str = Field(
        default=WHISPER_INITIAL_PROMPT,
        min_length=1,
        description="S9-1 settings contract の initial prompt",
    )
    whisper_timeout: int = Field(
        default=180,
        ge=1,
        le=86_400,
        description="whisper-cli 1 区間あたりの timeout 秒数",
    )
    whisper_threads: int = Field(
        default=8,
        ge=1,
        le=256,
        description="whisper-cli の threads",
    )
    whisper_processors: int = Field(
        default=1,
        ge=1,
        le=64,
        description="whisper-cli の processors",
    )
    whisper_beam_size: int = Field(
        default=5,
        ge=1,
        le=64,
        description="whisper-cli の beam size",
    )
    whisper_best_of: int = Field(
        default=5,
        ge=1,
        le=64,
        description="whisper-cli の best of",
    )
    whisper_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="whisper-cli の temperature",
    )
    whisper_no_fallback: bool = Field(
        default=False,
        description="whisper-cli の no_fallback",
    )
    whisper_vad: bool = Field(
        default=False,
        description="whisper-cli の VAD",
    )
    whisper_padding_ms: int = Field(
        default=0,
        ge=0,
        le=86_400_000,
        description="音声 span の padding millisecond",
    )

    @field_validator(
        "whisper_binary_sha256",
        "whisper_model_sha256",
        mode="after",
    )
    @classmethod
    def _whisper_sha256_is_lower_hex(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("whisper の SHA-256 は小文字64桁の16進数で指定してください。")
        return value

    @field_validator("whisper_binary_path", "whisper_model_path", mode="after")
    @classmethod
    def _whisper_path_is_safe(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("whisper のパスが正しくありません。")
        return cleaned

    @field_validator("whisper_language", "whisper_output_schema", mode="after")
    @classmethod
    def _whisper_text_setting_is_safe(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "<" in cleaned or ">" in cleaned:
            raise ValueError("whisper の設定値が正しくありません。")
        return cleaned

    @field_validator("whisper_initial_prompt", mode="after")
    @classmethod
    def _whisper_prompt_is_safe(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("whisper の initial prompt が正しくありません。")
        return cleaned

    @field_validator("whisper_temperature", mode="after")
    @classmethod
    def _whisper_temperature_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("whisper の temperature は有限値で指定してください。")
        return value

    def ensure_data_dir(self) -> Path:
        """data ディレクトリを作成して返す."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def get_settings() -> Settings:
    """設定インスタンスを返す."""
    return Settings()
