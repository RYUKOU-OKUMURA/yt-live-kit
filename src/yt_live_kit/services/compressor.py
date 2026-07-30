"""20 秒バケット圧縮."""

from __future__ import annotations

import re

_BUCKET_SECONDS = 20
_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$")


def _parse_line_seconds(line: str) -> tuple[int, str] | None:
    match = _LINE_RE.match(line.strip())
    if not match:
        return None
    h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
    text = match.group(4).strip()
    return h * 3600 + m * 60 + s, text


def _format_bucket(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def compress_lines(lines: list[str], bucket_seconds: int = _BUCKET_SECONDS) -> list[str]:
    """タイムスタンプ付き行を bucket_seconds 単位で圧縮する."""
    buckets: dict[int, list[str]] = {}

    for line in lines:
        parsed = _parse_line_seconds(line)
        if parsed is None:
            continue
        seconds, text = parsed
        if not text:
            continue
        bucket_start = (seconds // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_start, []).append(text)

    result: list[str] = []
    for bucket_start in sorted(buckets):
        combined = " ".join(buckets[bucket_start])
        result.append(f"{_format_bucket(bucket_start)} {combined}")

    return result
