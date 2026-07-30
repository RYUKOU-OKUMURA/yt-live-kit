"""yt-dlp ラッパーのユニットテスト."""

import pytest

from yt_live_kit.services.ytdlp import _find_subtitle_file, extract_video_id


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


def test_find_subtitle_file_ja_vtt_records_ja_lang(tmp_path):
    """{video_id}.ja.vtt は subtitle_lang='ja' として記録される."""
    subtitles_dir = tmp_path / "subtitles"
    subtitles_dir.mkdir()
    video_id = "IJvd6k6ZmUo"
    vtt_path = subtitles_dir / f"{video_id}.ja.vtt"
    vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")

    path, lang = _find_subtitle_file(subtitles_dir, video_id)

    assert path == vtt_path
    assert lang == "ja"


def test_find_subtitle_file_ja_orig_takes_priority(tmp_path):
    """ja-orig と ja が両方ある場合は ja-orig を優先する."""
    subtitles_dir = tmp_path / "subtitles"
    subtitles_dir.mkdir()
    video_id = "IJvd6k6ZmUo"
    orig_path = subtitles_dir / f"{video_id}.ja-orig.vtt"
    ja_path = subtitles_dir / f"{video_id}.ja.vtt"
    orig_path.write_text("WEBVTT\n\n", encoding="utf-8")
    ja_path.write_text("WEBVTT\n\n", encoding="utf-8")

    path, lang = _find_subtitle_file(subtitles_dir, video_id)

    assert path == orig_path
    assert lang == "ja-orig"
