"""subtitle_burn サービスのユニットテスト."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.services.subtitle_burn import (
    TimedCue,
    build_segment_subtitle,
    filter_cues_for_segment,
    is_japanese_font_available,
    resolve_font,
    write_ass,
)

SAMPLE_VTT = """WEBVTT

1
00:00:05.000 --> 00:00:08.000
区間前の字幕

2
00:00:10.000 --> 00:00:15.000
区間内の字幕

3
00:00:18.000 --> 00:00:22.000
またぎ字幕

4
00:00:25.000 --> 00:00:30.000
区間後の字幕
"""


def _parse_sample() -> list[TimedCue]:
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    return _parse_vtt_with_end(SAMPLE_VTT)


def test_filter_cues_keeps_only_overlapping():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 12.0, 20.0)

    texts = [cue.text for cue in filtered]
    assert "区間前の字幕" not in texts
    assert "区間内の字幕" in texts
    assert "またぎ字幕" in texts
    assert "区間後の字幕" not in texts


def test_filter_cues_before_segment():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 30.0, 45.0)
    assert filtered == []


def test_filter_cues_after_segment():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 0.0, 4.0)
    assert filtered == []


def test_filter_cues_spanning_start():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 20.0, 24.0)
    assert len(filtered) == 1
    assert filtered[0].text == "またぎ字幕"
    assert filtered[0].start_seconds == 0.0
    assert filtered[0].end_seconds == pytest.approx(2.0)


def test_filter_cues_time_offset_subtracted():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 10.0, 20.0)

    inner = next(cue for cue in filtered if cue.text == "区間内の字幕")
    assert inner.start_seconds == pytest.approx(0.0)
    assert inner.end_seconds == pytest.approx(5.0)


def test_filter_cues_never_negative_start():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 19.0, 24.0)

    for cue in filtered:
        assert cue.start_seconds >= 0.0
        assert cue.end_seconds >= cue.start_seconds


def test_write_ass_header_and_dialogue(tmp_path):
    cues = [
        TimedCue(start_seconds=1.5, end_seconds=4.25, text="テスト字幕"),
    ]
    output = tmp_path / "test.ass"
    write_ass(cues, output, font_name="Hiragino Sans")

    content = output.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Format: Layer, Start, End" in content
    assert "Dialogue: 0,0:00:01.50,0:00:04.25,Default,,0,0,0,,テスト字幕" in content


def test_build_segment_subtitle_writes_ass(tmp_path):
    video_id = "vid123"
    vtt_dir = tmp_path / video_id / "subtitles"
    vtt_dir.mkdir(parents=True)
    (vtt_dir / "ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")

    from yt_live_kit.config import Settings

    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.subtitle_burn.resolve_font",
        return_value="Hiragino Sans",
    ):
        ass_path = build_segment_subtitle(video_id, 10.0, 20.0, settings)

    assert ass_path.is_file()
    assert ass_path.name == "short_10_20.ass"
    content = ass_path.read_text(encoding="utf-8")
    assert "区間内の字幕" in content
    assert "区間前の字幕" not in content


@patch("yt_live_kit.services.subtitle_burn._font_exists")
def test_resolve_font_prefers_preferred(mock_exists):
    mock_exists.return_value = True
    assert resolve_font("My Font") == "My Font"
    mock_exists.assert_called_once_with("My Font")


@patch("yt_live_kit.services.subtitle_burn._font_exists")
def test_resolve_font_fallback_order(mock_exists):
    def side_effect(name: str) -> bool:
        return name == "Noto Sans CJK JP"

    mock_exists.side_effect = side_effect
    assert resolve_font(None) == "Noto Sans CJK JP"
    assert mock_exists.call_args_list[0][0][0] == "Hiragino Sans"
    assert mock_exists.call_args_list[1][0][0] == "Noto Sans CJK JP"


@patch("yt_live_kit.services.subtitle_burn._font_exists", return_value=False)
def test_resolve_font_sans_serif_last(mock_exists):
    assert resolve_font(None) == "sans-serif"


@patch("yt_live_kit.services.subtitle_burn._font_exists", return_value=False)
def test_is_japanese_font_available_false_when_only_sans(mock_exists):
    assert is_japanese_font_available() is False


@patch("yt_live_kit.services.subtitle_burn._font_exists", return_value=True)
def test_is_japanese_font_available_true(mock_exists):
    assert is_japanese_font_available() is True


@patch("yt_live_kit.services.subtitle_burn.shutil.which")
@patch("yt_live_kit.services.subtitle_burn.subprocess.run")
def test_font_detection_via_fc_list(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/fc-list"
    mock_run.return_value = MagicMock(returncode=0, stdout="Hiragino Sans\n")

    from yt_live_kit.services.subtitle_burn import _font_available_via_fc_list

    assert _font_available_via_fc_list("Hiragino Sans") is True
