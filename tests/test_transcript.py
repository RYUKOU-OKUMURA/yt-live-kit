"""トランスクリプト生成のユニットテスト."""

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.transcript import TranscriptError, build_transcripts

SAMPLE_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
テスト字幕
"""


def test_build_transcripts_writes_atomically(tmp_path):
    video_id = "testvideo01"
    video_dir = tmp_path / video_id
    subtitles_dir = video_dir / "subtitles"
    subtitles_dir.mkdir(parents=True)
    (subtitles_dir / "ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")

    settings = Settings(data_dir=tmp_path)
    full_path, compressed_path = build_transcripts(video_id, settings)

    assert full_path.is_file()
    assert compressed_path.is_file()
    assert "テスト字幕" in full_path.read_text(encoding="utf-8")
    assert list((video_dir / "transcript").glob(".*.tmp")) == []


def test_build_transcripts_raises_on_empty_vtt(tmp_path):
    """有効キュー 0 件の VTT では空ファイルを保存せず TranscriptError を投げる."""
    video_id = "testvideo01"
    video_dir = tmp_path / video_id
    subtitles_dir = video_dir / "subtitles"
    subtitles_dir.mkdir(parents=True)
    (subtitles_dir / "ja.vtt").write_text("WEBVTT\n\n", encoding="utf-8")

    settings = Settings(data_dir=tmp_path)

    with pytest.raises(TranscriptError, match="有効なテキストがありません"):
        build_transcripts(video_id, settings)

    transcript_dir = video_dir / "transcript"
    assert not (transcript_dir / "full.txt").exists()
    assert not (transcript_dir / "compressed.txt").exists()
