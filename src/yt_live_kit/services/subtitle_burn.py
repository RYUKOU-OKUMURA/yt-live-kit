"""区間字幕の ASS 生成と日本語フォント解決."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.vtt_parser import _clean_vtt_text

_TIMESTAMP_RE = re.compile(
    r"^(?:(\d{2}):)?(\d{2}):(\d{2})(?:\.(\d{3}))?\s*-->\s*"
    r"(?:(\d{2}):)?(\d{2}):(\d{2})(?:\.(\d{3}))?"
)

_FONT_CANDIDATES = ("Hiragino Sans", "Noto Sans CJK JP")
_MAC_FONT_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
)


class SubtitleBurnError(Exception):
    """字幕変換エラー."""


@dataclass(frozen=True)
class TimedCue:
    """開始・終了時刻付き字幕キュー."""

    start_seconds: float
    end_seconds: float
    text: str


def _parse_timestamp(h: str | None, m: str, s: str, ms: str | None) -> float:
    hours = int(h or 0)
    minutes = int(m)
    seconds = int(s)
    millis = int(ms or 0)
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _parse_vtt_with_end(content: str) -> list[TimedCue]:
    """WebVTT をパースし、終了時刻付きキューを返す."""
    cues: list[TimedCue] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line or line.startswith(("WEBVTT", "NOTE", "Kind:")):
            continue
        if line.isdigit():
            if i < len(lines):
                line = lines[i].strip()
                i += 1
            else:
                continue

        match = _TIMESTAMP_RE.match(line)
        if not match:
            continue

        start = _parse_timestamp(match.group(1), match.group(2), match.group(3), match.group(4))
        end = _parse_timestamp(match.group(5), match.group(6), match.group(7), match.group(8))
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1

        text = " ".join(text_lines).strip()
        text = _clean_vtt_text(text)
        if text and end > start:
            cues.append(TimedCue(start_seconds=start, end_seconds=end, text=text))

    return cues


def filter_cues_for_segment(
    cues: list[TimedCue],
    start_sec: float,
    end_sec: float,
) -> list[TimedCue]:
    """区間と重なるキューを切り出し、0 秒基準にオフセットする."""
    segment_duration = end_sec - start_sec
    result: list[TimedCue] = []

    for cue in cues:
        if cue.start_seconds >= end_sec or cue.end_seconds <= start_sec:
            continue

        new_start = max(0.0, cue.start_seconds - start_sec)
        new_end = min(segment_duration, cue.end_seconds - start_sec)
        if new_end <= new_start:
            continue

        result.append(
            TimedCue(start_seconds=new_start, end_seconds=new_end, text=cue.text)
        )

    return result


def _seconds_to_ass_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def write_ass(cues: list[TimedCue], output_path: Path, *, font_name: str) -> Path:
    """ASS 字幕ファイルを書き出す."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            f"Style: Default,{font_name},54,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,180,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for cue in cues:
        start = _seconds_to_ass_time(cue.start_seconds)
        end = _seconds_to_ass_time(cue.end_seconds)
        text = _escape_ass_text(cue.text)
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _segment_ass_name(start_sec: float, end_sec: float) -> str:
    return f"short_{start_sec:g}_{end_sec:g}.ass"


def build_segment_subtitle(
    video_id: str,
    start_sec: float,
    end_sec: float,
    settings: Settings | None = None,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    """指定区間の ASS 字幕を生成する."""
    del ffmpeg_path  # VTT から直接生成するため未使用
    settings = settings or get_settings()
    vtt_path = settings.data_dir / video_id / "subtitles" / "ja.vtt"
    if not vtt_path.is_file():
        raise SubtitleBurnError(f"字幕ファイルが見つかりません: {vtt_path}")

    content = vtt_path.read_text(encoding="utf-8")
    cues = _parse_vtt_with_end(content)
    segment_cues = filter_cues_for_segment(cues, start_sec, end_sec)

    output_path = (
        settings.data_dir
        / video_id
        / "shorts"
        / "subtitles"
        / _segment_ass_name(start_sec, end_sec)
    )
    font_name = resolve_font(settings.subtitle_font)
    return write_ass(segment_cues, output_path, font_name=font_name)


def _font_available_via_fc_list(font_name: str) -> bool:
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return False
    result = subprocess.run(
        [fc_list, ":", "family"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    needle = font_name.casefold()
    return any(needle in line.casefold() for line in result.stdout.splitlines())


def _font_available_in_mac_dirs(font_name: str) -> bool:
    needle = font_name.casefold().replace(" ", "")
    for directory in _MAC_FONT_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            stem = path.stem.casefold().replace(" ", "")
            if needle in stem or stem in needle:
                return True
    return False


def _font_exists(font_name: str) -> bool:
    if _font_available_via_fc_list(font_name):
        return True
    return _font_available_in_mac_dirs(font_name)


def resolve_font(preferred: str | None = None) -> str:
    """利用可能な日本語フォント名を返す."""
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(_FONT_CANDIDATES)
    candidates.append("sans-serif")

    for name in candidates:
        if name == "sans-serif":
            return name
        if _font_exists(name):
            return name
    return "sans-serif"


def is_japanese_font_available(preferred: str | None = None) -> bool:
    """実フォントが解決できるか（sans-serif フォールバック以外）."""
    resolved = resolve_font(preferred)
    return resolved != "sans-serif"
