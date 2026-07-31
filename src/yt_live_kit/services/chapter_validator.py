"""YouTube 概要欄用チャプターのバリデーション."""

from __future__ import annotations

import re
from dataclasses import dataclass

# M:SS または H:MM:SS + 半角スペース + タイトル
_CHAPTER_LINE_RE = re.compile(r"^(\d+:)?(\d{1,2}):(\d{2})\s+(.+)$")


@dataclass(frozen=True)
class ChapterEntry:
    """パース済みチャプター 1 行."""

    timestamp: str
    title: str
    seconds: int
    raw_line: str


@dataclass(frozen=True)
class ValidationResult:
    """バリデーション結果."""

    ok: bool
    errors: tuple[str, ...]
    chapters: tuple[ChapterEntry, ...]


def _validate_seconds_field(seconds_str: str, timestamp: str) -> str | None:
    """秒フィールドが 00–59 か検証し、違反時はエラーメッセージを返す."""
    try:
        seconds = int(seconds_str)
    except ValueError:
        return f"秒の形式が不正です: {timestamp}"
    if seconds < 0 or seconds > 59:
        return f"秒は 00〜59 の範囲である必要があります: {timestamp}"
    return None


def _parse_int_field(value_str: str, timestamp: str) -> int:
    """時・分フィールドを整数に変換する。非数値なら日本語エラーを送出する."""
    try:
        return int(value_str)
    except ValueError:
        raise ValueError(
            f"時刻は HH:MM:SS または M:SS の形式である必要があります: {timestamp}"
        ) from None


def parse_timestamp_to_seconds(timestamp: str) -> int:
    """M:SS または H:MM:SS を秒に変換する."""
    parts = timestamp.split(":")
    if len(parts) == 2:
        minutes_str, seconds_str = parts
        err = _validate_seconds_field(seconds_str, timestamp)
        if err:
            raise ValueError(err)
        minutes = _parse_int_field(minutes_str, timestamp)
        seconds = int(seconds_str)
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours_str, minutes_str, seconds_str = parts
        err = _validate_seconds_field(seconds_str, timestamp)
        if err:
            raise ValueError(err)
        hours = _parse_int_field(hours_str, timestamp)
        minutes = _parse_int_field(minutes_str, timestamp)
        seconds = int(seconds_str)
        if minutes < 0 or minutes > 59:
            raise ValueError(f"分は 00〜59 の範囲である必要があります: {timestamp}")
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"不正な時刻形式: {timestamp}")


def extract_chapter_lines(text: str) -> list[str]:
    """AI 出力からチャプター行を抽出する."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("```"):
            continue
        if _CHAPTER_LINE_RE.match(line):
            lines.append(line)
    return lines


def parse_chapters(text: str) -> list[ChapterEntry]:
    """テキストからチャプターエントリをパースする."""
    entries: list[ChapterEntry] = []
    for line in extract_chapter_lines(text):
        match = _CHAPTER_LINE_RE.match(line)
        if not match:
            continue
        h_part, minutes, seconds, title = match.groups()
        if h_part:
            timestamp = f"{h_part}{minutes}:{seconds}"
        else:
            timestamp = f"{minutes}:{seconds}"
        sec_err = _validate_seconds_field(seconds, timestamp)
        if sec_err:
            raise ValueError(sec_err)
        entries.append(
            ChapterEntry(
                timestamp=timestamp,
                title=title.strip(),
                seconds=parse_timestamp_to_seconds(timestamp),
                raw_line=line,
            )
        )
    return entries


def validate_chapters(text: str) -> ValidationResult:
    """チャプターテキストを YouTube 概要欄ルールで検証する."""
    errors: list[str] = []
    try:
        chapters = parse_chapters(text)
    except ValueError as exc:
        errors.append(str(exc))
        return ValidationResult(ok=False, errors=tuple(errors), chapters=())

    if not chapters:
        errors.append("チャプター行が 1 件も見つかりません。")
        return ValidationResult(ok=False, errors=tuple(errors), chapters=())

    if chapters[0].timestamp != "0:00" or chapters[0].seconds != 0:
        errors.append("先頭チャプターは 0:00 から始める必要があります。")

    if len(chapters) < 3:
        errors.append(f"チャプターは 3 件以上必要です（現在 {len(chapters)} 件）。")

    for i in range(1, len(chapters)):
        if chapters[i].seconds <= chapters[i - 1].seconds:
            errors.append(
                f"時刻が前後しています: "
                f"{chapters[i - 1].timestamp} → {chapters[i].timestamp}"
            )
            continue
        gap = chapters[i].seconds - chapters[i - 1].seconds
        if gap < 10:
            errors.append(
                f"チャプター間隔が 10 秒未満です: "
                f"{chapters[i - 1].timestamp} → {chapters[i].timestamp}（{gap} 秒）"
            )

    for chapter in chapters:
        if "<" in chapter.raw_line or ">" in chapter.raw_line:
            errors.append(f"半角の 〈 〉 は使用できません: {chapter.raw_line}")

    return ValidationResult(
        ok=len(errors) == 0,
        errors=tuple(errors),
        chapters=tuple(chapters),
    )


def format_chapters_markdown(chapters: tuple[ChapterEntry, ...] | list[ChapterEntry]) -> str:
    """検証済みチャプターを chapters.md 用テキストに整形する."""
    lines: list[str] = []
    for i, entry in enumerate(chapters):
        if i == 0:
            lines.append(f"0:00 {entry.title}")
        else:
            lines.append(entry.raw_line)
    return "\n".join(lines) + ("\n" if lines else "")
