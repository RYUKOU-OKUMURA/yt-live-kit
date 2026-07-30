"""VTT パーサーのユニットテスト."""

from pathlib import Path

from yt_live_kit.services.vtt_parser import (
    cues_to_lines,
    deduplicate_progressive,
    parse_vtt,
    parse_vtt_file,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_vtt_basic():
    content = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
テスト字幕
"""
    cues = parse_vtt(content)
    assert len(cues) == 1
    assert cues[0].text == "テスト字幕"
    assert cues[0].start_seconds == 1.0


def test_deduplicate_progressive_prefix():
    content = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
こんにちは

2
00:00:04.000 --> 00:00:07.000
こんにちは、今日は

3
00:00:07.000 --> 00:00:10.000
こんにちは、今日は AI について話します
"""
    cues = parse_vtt(content)
    deduped = deduplicate_progressive(cues)
    lines = cues_to_lines(deduped)

    assert len(deduped) == 3
    assert lines[0] == "[00:00:01] こんにちは"
    assert lines[1] == "[00:00:04] 、今日は"
    assert lines[2] == "[00:00:07] AI について話します"


def test_deduplicate_skips_identical():
    content = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
同じテキスト

2
00:00:04.000 --> 00:00:07.000
同じテキスト
"""
    cues = parse_vtt(content)
    deduped = deduplicate_progressive(cues)
    assert len(deduped) == 1


def test_sample_fixture():
    lines = parse_vtt_file(FIXTURES / "sample.vtt")
    assert len(lines) >= 4
    assert any("こんにちは" in line for line in lines)
    assert any("Cursor" in line for line in lines)
    assert all(line.startswith("[") for line in lines)
