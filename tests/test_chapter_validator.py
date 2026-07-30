"""チャプターバリデータのユニットテスト."""

import pytest

from yt_live_kit.services.chapter_validator import (
    extract_chapter_lines,
    parse_chapters,
    parse_timestamp_to_seconds,
    validate_chapters,
)

VALID_CHAPTERS = """\
0:00 配信開始・今日のテーマ紹介
5:30 Codex CLI の使い方デモ
12:00 Cursor との連携設定
25:00 質疑応答
"""

INVALID_TOO_FEW = """\
0:00 配信開始
5:00 終了
"""

INVALID_NOT_ZERO = """\
1:00 配信開始
5:00 本編
10:00 終了
"""

INVALID_GAP = """\
0:00 配信開始
0:05 すぐ次の章
10:00 本編
"""

INVALID_ANGLE_BRACKETS = """\
0:00 配信開始
5:00 API<T>の話
10:00 終了
"""


def test_parse_timestamp_mm_ss():
    assert parse_timestamp_to_seconds("0:00") == 0
    assert parse_timestamp_to_seconds("5:30") == 330
    assert parse_timestamp_to_seconds("59:59") == 3599


def test_parse_timestamp_hh_mm_ss():
    assert parse_timestamp_to_seconds("1:00:00") == 3600
    assert parse_timestamp_to_seconds("1:23:45") == 5025


def test_extract_chapter_lines_from_markdown():
    text = """\
# チャプター案

```
0:00 配信開始
5:30 本編
12:00 まとめ
```

余計な説明文
"""
    lines = extract_chapter_lines(text)
    assert lines == ["0:00 配信開始", "5:30 本編", "12:00 まとめ"]


def test_validate_valid_chapters():
    result = validate_chapters(VALID_CHAPTERS)
    assert result.ok is True
    assert len(result.errors) == 0
    assert len(result.chapters) == 4
    assert result.chapters[0].timestamp == "0:00"
    assert result.chapters[0].seconds == 0


def test_validate_requires_at_least_three():
    result = validate_chapters(INVALID_TOO_FEW)
    assert result.ok is False
    assert any("3 件以上" in err for err in result.errors)


def test_validate_first_must_be_zero():
    result = validate_chapters(INVALID_NOT_ZERO)
    assert result.ok is False
    assert any("0:00" in err for err in result.errors)


def test_validate_minimum_gap():
    result = validate_chapters(INVALID_GAP)
    assert result.ok is False
    assert any("10 秒未満" in err for err in result.errors)


def test_validate_forbids_angle_brackets():
    result = validate_chapters(INVALID_ANGLE_BRACKETS)
    assert result.ok is False
    assert any("<>" in err for err in result.errors)


def test_parse_chapters_preserves_raw_line():
    chapters = parse_chapters(VALID_CHAPTERS)
    assert chapters[1].raw_line == "5:30 Codex CLI の使い方デモ"
    assert chapters[1].title == "Codex CLI の使い方デモ"


def test_validate_empty_text():
    result = validate_chapters("")
    assert result.ok is False
    assert any("1 件も" in err for err in result.errors)


def test_fullwidth_brackets_allowed():
    text = """\
0:00 配信開始
5:00 〈Codex〉の話
10:00 まとめ
"""
    result = validate_chapters(text)
    assert result.ok is True
