"""subtitle_burn サービスのユニットテスト."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.services import subtitle_burn
from yt_live_kit.services.subtitle_burn import (
    TimedCue,
    build_segment_subtitle,
    filter_cues_for_segment,
    is_japanese_font_available,
    resolve_font,
    write_ass,
)
from yt_live_kit.services.vtt_parser import Cue, deduplicate_progressive

# YouTube 自動字幕によくある「前の行 + 新しい語」が積み上がるプログレッシブ形式。
PROGRESSIVE_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
こんにちは

2
00:00:04.000 --> 00:00:07.000
こんにちは 今日は

3
00:00:07.000 --> 00:00:10.000
こんにちは 今日は 配信です
"""

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


# --- 修正1: PlayResX / PlayResY ---------------------------------------------


def test_write_ass_includes_play_res(tmp_path):
    """PlayRes が無いと libass は 384x288 既定で約 6.7 倍にスケールされ字幕が巨大化する."""
    cues = [TimedCue(start_seconds=0.0, end_seconds=2.0, text="テスト")]
    output = tmp_path / "test.ass"
    write_ass(cues, output, font_name="Hiragino Sans")

    content = output.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content


def test_write_ass_play_res_overridable(tmp_path):
    """将来 1080x1920 以外に焼く場合に備え、引数で上書きできる."""
    cues = [TimedCue(start_seconds=0.0, end_seconds=2.0, text="テスト")]
    output = tmp_path / "test.ass"
    write_ass(cues, output, font_name="Hiragino Sans", play_res_x=720, play_res_y=1280)

    content = output.read_text(encoding="utf-8")
    assert "PlayResX: 720" in content
    assert "PlayResY: 1280" in content


# --- 修正2: プログレッシブ重複除去 ------------------------------------------


def test_parse_vtt_with_end_removes_progressive_duplicates():
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    cues = _parse_vtt_with_end(PROGRESSIVE_VTT)
    texts = [cue.text for cue in cues]

    assert texts == ["こんにちは", "今日は", "配信です"]
    # 同じ語が繰り返し現れない
    assert " ".join(texts).count("こんにちは") == 1


def test_parse_vtt_with_end_preserves_end_seconds_after_dedup():
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    cues = _parse_vtt_with_end(PROGRESSIVE_VTT)

    assert [cue.end_seconds for cue in cues] == pytest.approx([4.0, 7.0, 10.0])


def test_deduplicate_progressive_timed_matches_vtt_parser():
    """subtitle_burn 独自の除去関数が vtt_parser.deduplicate_progressive と同じ text 列を返す."""
    starts_texts = [
        (1.0, "こんにちは"),
        (4.0, "こんにちは 今日は"),
        (7.0, "こんにちは 今日は 配信です"),
        (10.0, "こんにちは 今日は 配信です"),  # 完全一致重複
        (13.0, "配信です 今日は 配信です"),  # 部分文字列一致（末尾以外）
    ]

    plain_cues = [Cue(start_seconds=s, text=t) for s, t in starts_texts]
    timed_cues = [
        TimedCue(start_seconds=s, end_seconds=s + 3.0, text=t) for s, t in starts_texts
    ]

    expected_texts = [cue.text for cue in deduplicate_progressive(plain_cues)]
    actual_texts = [
        cue.text for cue in subtitle_burn._deduplicate_progressive_timed(timed_cues)
    ]

    assert actual_texts == expected_texts


def test_deduplicate_progressive_timed_keeps_end_seconds():
    timed_cues = [
        TimedCue(start_seconds=1.0, end_seconds=4.0, text="こんにちは"),
        TimedCue(start_seconds=4.0, end_seconds=7.0, text="こんにちは 今日は"),
    ]

    result = subtitle_burn._deduplicate_progressive_timed(timed_cues)

    assert [cue.end_seconds for cue in result] == pytest.approx([4.0, 7.0])


def test_dedup_applied_before_segment_filter():
    """区間の直前のキューとの重複も取れるよう、絞り込み前に全体へ除去を適用する."""
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    vtt = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
こんにちは

2
00:00:04.000 --> 00:00:07.000
こんにちは 今日は
"""
    cues = _parse_vtt_with_end(vtt)
    segment_cues = filter_cues_for_segment(cues, 4.0, 7.0)

    assert [cue.text for cue in segment_cues] == ["今日は"]


# --- 修正4: フォント検出のマッチが緩い --------------------------------------


def test_font_dir_no_false_positive_for_short_filename(tmp_path):
    """"sans.ttf" のような短いファイル名が "Hiragino Sans" に誤検出しない."""
    (tmp_path / "sans.ttf").write_bytes(b"")

    with patch.object(subtitle_burn, "_MAC_FONT_DIRS", (tmp_path,)):
        assert subtitle_burn._font_available_in_mac_dirs("Hiragino Sans") is False


def test_font_dir_detects_japanese_hiragino_filename(tmp_path):
    """実ファイル名が日本語（ヒラギノ角ゴシック W3.ttc）でも検出できる."""
    (tmp_path / "ヒラギノ角ゴシック W3.ttc").write_bytes(b"")

    with patch.object(subtitle_burn, "_MAC_FONT_DIRS", (tmp_path,)):
        assert subtitle_burn._font_available_in_mac_dirs("Hiragino Sans") is True


def test_font_dir_returns_english_family_name_not_japanese(tmp_path):
    """検出できても返す値は英語ファミリ名のまま（force_style に渡すため）."""
    (tmp_path / "ヒラギノ角ゴシック W3.ttc").write_bytes(b"")

    with patch.object(subtitle_burn, "_MAC_FONT_DIRS", (tmp_path,)):
        with patch.object(subtitle_burn, "_font_available_via_fc_list", return_value=False):
            assert resolve_font(None) == "Hiragino Sans"
