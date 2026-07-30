"""WebVTT パーサー — プログレッシブ重複除去."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIMESTAMP_RE = re.compile(
    r"^(?:(\d{2}):)?(\d{2}):(\d{2})(?:\.(\d{3}))?\s*-->\s*"
    r"(?:(\d{2}):)?(\d{2}):(\d{2})(?:\.(\d{3}))?"
)
_VTT_TIMESTAMP_TAG_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
_VTT_C_TAG_RE = re.compile(r"<c>([^<]*)</c>")
_VTT_OTHER_TAG_RE = re.compile(r"<[^>]+>")


class EmptyVttError(Exception):
    """VTT に有効な字幕キューが含まれていない."""

    def __init__(self, path: Path | None = None) -> None:
        message = "字幕ファイルに有効なテキストがありません。"
        if path is not None:
            message = f"字幕ファイルに有効なテキストがありません: {path}"
        super().__init__(message)


def clean_vtt_text(text: str) -> str:
    """VTT インラインタグ（タイムスタンプ・c タグ等）を除去する."""
    cleaned = _VTT_TIMESTAMP_TAG_RE.sub("", text)
    cleaned = _VTT_C_TAG_RE.sub(r"\1", cleaned)
    cleaned = _VTT_OTHER_TAG_RE.sub("", cleaned)
    return " ".join(cleaned.split())


_clean_vtt_text = clean_vtt_text


@dataclass(frozen=True)
class Cue:
    """VTT キュー."""

    start_seconds: float
    text: str


def _parse_timestamp(h: str | None, m: str, s: str, ms: str | None) -> float:
    hours = int(h or 0)
    minutes = int(m)
    seconds = int(s)
    millis = int(ms or 0)
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_vtt(content: str) -> list[Cue]:
    """WebVTT 文字列をパースしてキューリストを返す."""
    cues: list[Cue] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("Kind:"):
            continue
        if line.isdigit() or "-->" not in line:
            if "-->" not in line:
                continue

        match = _TIMESTAMP_RE.match(line)
        if not match:
            continue

        start = _parse_timestamp(match.group(1), match.group(2), match.group(3), match.group(4))
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1

        text = " ".join(text_lines).strip()
        text = _clean_vtt_text(text)
        if text:
            cues.append(Cue(start_seconds=start, text=text))

    return cues


def deduplicate_progressive(cues: list[Cue]) -> list[Cue]:
    """プログレッシブ表示による部分文字列重複を除去する."""
    result: list[Cue] = []
    prev_text = ""

    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue

        if prev_text:
            if text == prev_text:
                continue
            if text.startswith(prev_text):
                delta = text[len(prev_text) :].strip()
                if delta:
                    result.append(Cue(start_seconds=cue.start_seconds, text=delta))
                prev_text = text
                continue
            if prev_text in text:
                idx = text.find(prev_text)
                delta = (text[:idx] + text[idx + len(prev_text) :]).strip()
                if delta:
                    result.append(Cue(start_seconds=cue.start_seconds, text=delta))
                prev_text = text
                continue

        result.append(cue)
        prev_text = text

    return result


def cues_to_lines(cues: list[Cue]) -> list[str]:
    """キューを [HH:MM:SS] テキスト 形式の行リストに変換する."""
    return [f"[{_format_timestamp(c.start_seconds)}] {c.text}" for c in cues]


def parse_vtt_file(path: Path) -> list[str]:
    """VTT ファイルを読み込み、重複除去済みタイムスタンプ行を返す."""
    content = path.read_text(encoding="utf-8")
    cues = parse_vtt(content)
    deduped = deduplicate_progressive(cues)
    if not deduped:
        raise EmptyVttError(path)
    return cues_to_lines(deduped)
