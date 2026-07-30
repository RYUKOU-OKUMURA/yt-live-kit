"""yt-dlp バージョン警告のユニットテスト."""

from yt_live_kit.services.ytdlp import (
    YTDLP_MIN_RECOMMENDED_VERSION,
    _parse_ytdlp_version,
    is_ytdlp_version_outdated,
)


def test_parse_ytdlp_version():
    assert _parse_ytdlp_version("2026.07.04") == (2026, 7, 4)
    assert _parse_ytdlp_version("2026.7.4") == (2026, 7, 4)


def test_is_ytdlp_version_outdated():
    assert is_ytdlp_version_outdated("2025.01.01", YTDLP_MIN_RECOMMENDED_VERSION)
    assert not is_ytdlp_version_outdated("2026.07.04", YTDLP_MIN_RECOMMENDED_VERSION)
    assert not is_ytdlp_version_outdated(
        YTDLP_MIN_RECOMMENDED_VERSION, YTDLP_MIN_RECOMMENDED_VERSION
    )
