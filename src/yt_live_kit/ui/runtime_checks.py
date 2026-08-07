"""Streamlit rerun ごとの軽量な実行環境チェック."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.ytdlp import (
    YtdlpBinaryIdentity,
    check_ytdlp_version_warning,
    get_ytdlp_binary_identity,
)

YTDLP_WARNING_CACHE_TTL_SECONDS = 600
YTDLP_WARNING_CACHE_MAX_ENTRIES = 4


@st.cache_data(
    ttl=YTDLP_WARNING_CACHE_TTL_SECONDS,
    max_entries=YTDLP_WARNING_CACHE_MAX_ENTRIES,
    show_spinner=False,
)
def _cached_ytdlp_version_warning(
    configured_path: str,
    timeout: int,
    binary_identity: YtdlpBinaryIdentity,
) -> str | None:
    """yt-dlp の read-only warning だけを bounded cache する.

    ``binary_identity`` は cache key 専用であり、fetch や生成物の検証には使わない。
    service 側の warning 検査は ``--version`` のみを実行するため、この層から外部 API
    や字幕・動画取得を呼び出すことはない。
    """
    settings = Settings(ytdlp_path=configured_path, ytdlp_timeout=timeout)
    return check_ytdlp_version_warning(settings)


def check_ytdlp_version_warning_cached(
    settings: Settings | None = None,
) -> str | None:
    """設定と binary identity を key に yt-dlp warning を取得する."""
    settings = settings or get_settings()
    identity = get_ytdlp_binary_identity(settings.ytdlp_path)
    return _cached_ytdlp_version_warning(
        settings.ytdlp_path,
        settings.ytdlp_timeout,
        identity,
    )

def clear_ytdlp_version_warning_cache() -> None:
    """テストまたは設定変更時に warning cache を明示的に破棄する."""
    _cached_ytdlp_version_warning.clear()


def check_whisper_model_warning(settings: Settings | None = None) -> str | None:
    """whisper.cpp model file の存在だけを軽量に確認する.

    高精度字幕の preflight は SHA256 まで検証するが、モデルは 500MB 級のため
    Streamlit の rerun のたびに毎回ハッシュ計算すると致命的に遅くなる。
    ここでは ``Path.is_file()`` 相当の存在確認だけを行い、設定パスと
    設定すべき環境変数名を含む日本語警告を返す。
    """
    settings = settings or get_settings()
    model_path = Path(settings.whisper_model_path)
    if model_path.is_file():
        return None
    return (
        f"Whisper モデルファイルが見つかりません（設定: {settings.whisper_model_path}）。"
        "設定ページで YTLK_WHISPER_MODEL_PATH を確認するか、モデルファイルを配置してください。"
    )
