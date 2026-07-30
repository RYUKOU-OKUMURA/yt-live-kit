"""yt-dlp ラッパーのユニットテスト."""

import pytest

from yt_live_kit.services.ytdlp import extract_video_id


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=IJvd6k6ZmUo", "IJvd6k6ZmUo"),
        ("https://youtu.be/IJvd6k6ZmUo", "IJvd6k6ZmUo"),
        ("IJvd6k6ZmUo", "IJvd6k6ZmUo"),
    ],
)
def test_extract_video_id(url: str, expected: str):
    assert extract_video_id(url) == expected
