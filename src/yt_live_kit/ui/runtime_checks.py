"""Streamlit rerun ごとの軽量な実行環境チェック."""

from __future__ import annotations

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
