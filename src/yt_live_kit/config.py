"""アプリケーション設定."""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class WhisperAdoptedContract:
    """S9-1 で採用した whisper.cpp の immutable production contract."""

    binary_version: str
    binary_sha256: str
    model_name: str
    model_sha256: str
    output_schema: str
    language: str
    initial_prompt: str
    timeout_sec: int
    threads: int
    processors: int
    beam_size: int
    best_of: int
    temperature: float
    no_fallback: bool
    vad: bool
    padding_ms: int

    @property
    def decode(self) -> dict[str, int | float | bool]:
        """固定 decode 設定の新しい snapshot を返す."""

        return {
            "beam_size": self.beam_size,
            "best_of": self.best_of,
            "no_fallback": self.no_fallback,
            "processors": self.processors,
            "temperature": self.temperature,
            "threads": self.threads,
            "vad": self.vad,
        }


WHISPER_ADOPTED_CONTRACT = WhisperAdoptedContract(
    binary_version=WHISPER_BINARY_VERSION,
    binary_sha256=WHISPER_BINARY_SHA256,
    model_name=WHISPER_MODEL_NAME,
    model_sha256=WHISPER_MODEL_SHA256,
    output_schema=WHISPER_OUTPUT_SCHEMA,
    language=WHISPER_LANGUAGE,
    initial_prompt=WHISPER_INITIAL_PROMPT,
    timeout_sec=180,
    threads=8,
    processors=1,
    beam_size=5,
    best_of=5,
    temperature=0.0,
    no_fallback=False,
    vad=False,
    padding_ms=0,
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

    # S9 runtime の env 上書きを許可するのは実体の location だけ。期待する
    # version / SHA / model / schema / prompt / decode / timeout は下の immutable
    # contract からのみ取得し、別実体を設定値だけで正当化できないようにする。
    whisper_binary_path: str = Field(
        default=WHISPER_BINARY_PATH,
        min_length=1,
        description="whisper.cpp whisper-cli の実行ファイルパス",
    )
    whisper_model_path: str = Field(
        default=WHISPER_MODEL_PATH,
        min_length=1,
        description="採用 whisper.cpp model file パス",
    )

    @field_validator("whisper_binary_path", "whisper_model_path", mode="after")
    @classmethod
    def _whisper_path_is_safe(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("whisper のパスが正しくありません。")
        return cleaned

    def ensure_data_dir(self) -> Path:
        """data ディレクトリを作成して返す."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def get_settings() -> Settings:
    """設定インスタンスを返す."""
    return Settings()
