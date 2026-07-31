"""区間字幕の ASS 生成と日本語フォント解決."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.vtt_parser import clean_vtt_text

_TIMESTAMP_RE = re.compile(
    r"^(?:(\d{2}):)?(\d{2}):(\d{2})(?:\.(\d{3}))?\s*-->\s*"
    r"(?:(\d{2}):)?(\d{2}):(\d{2})(?:\.(\d{3}))?"
)

_FONT_CANDIDATES = ("Hiragino Sans", "Noto Sans CJK JP")
_MAC_FONT_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
)
# 縦型ショート出力の解像度（1080x1920）。ASS の PlayResX/PlayResY と揃える.
_ASS_PLAY_RES_X = 1080
_ASS_PLAY_RES_Y = 1920
# macOS の日本語ヒラギノは実ファイル名が英語名と一致しないため、別名候補で補う.
_MAC_FONT_FILENAME_ALIASES: dict[str, tuple[str, ...]] = {
    "hiraginosans": ("ヒラギノ角ゴシック", "ヒラギノ角ゴ"),
}


class SubtitleBurnError(Exception):
    """字幕変換エラー."""


@dataclass(frozen=True)
class TimedCue:
    """開始・終了時刻付き字幕キュー."""

    start_seconds: float
    end_seconds: float
    text: str
    emphasis: bool = False


@dataclass(frozen=True)
class TelopPreset:
    """ASS のテロップスタイル定義."""

    font_size: float
    primary_colour: str
    secondary_colour: str
    outline_colour: str
    back_colour: str
    bold: bool
    italic: bool
    underline: bool
    strike_out: bool
    scale_x: float
    scale_y: float
    spacing: float
    angle: float
    border_style: int
    outline: float
    shadow: float
    alignment: int
    margin_l: int
    margin_r: int
    margin_v: int
    encoding: int
    emphasis_colour: str


TELOP_PRESETS: dict[str, TelopPreset] = {
    "default": TelopPreset(
        font_size=54,
        primary_colour="&H00FFFFFF",
        secondary_colour="&H000000FF",
        outline_colour="&H00000000",
        back_colour="&H80000000",
        bold=False,
        italic=False,
        underline=False,
        strike_out=False,
        scale_x=100,
        scale_y=100,
        spacing=0,
        angle=0,
        border_style=1,
        outline=3,
        shadow=0,
        alignment=2,
        margin_l=10,
        margin_r=10,
        margin_v=180,
        encoding=1,
        emphasis_colour="&H0000FFFF",
    ),
    "bold_outline": TelopPreset(
        font_size=60,
        primary_colour="&H00FFFFFF",
        secondary_colour="&H000000FF",
        outline_colour="&H00000000",
        back_colour="&H80000000",
        bold=True,
        italic=False,
        underline=False,
        strike_out=False,
        scale_x=100,
        scale_y=100,
        spacing=0,
        angle=0,
        border_style=1,
        outline=6,
        shadow=0,
        alignment=2,
        margin_l=40,
        margin_r=40,
        margin_v=180,
        encoding=1,
        emphasis_colour="&H0000FFFF",
    ),
    "boxed": TelopPreset(
        font_size=56,
        primary_colour="&H00FFFFFF",
        secondary_colour="&H000000FF",
        outline_colour="&H00000000",
        back_colour="&H60000000",
        bold=True,
        italic=False,
        underline=False,
        strike_out=False,
        scale_x=100,
        scale_y=100,
        spacing=0,
        angle=0,
        border_style=3,
        outline=3,
        shadow=0,
        alignment=2,
        margin_l=60,
        margin_r=60,
        margin_v=180,
        encoding=1,
        emphasis_colour="&H0000FFFF",
    ),
    "hook": TelopPreset(
        font_size=88,
        primary_colour="&H00FFFFFF",
        secondary_colour="&H000000FF",
        outline_colour="&H00000000",
        back_colour="&H60000000",
        bold=True,
        italic=False,
        underline=False,
        strike_out=False,
        scale_x=100,
        scale_y=100,
        spacing=0,
        angle=0,
        border_style=3,
        outline=4,
        shadow=0,
        alignment=8,
        margin_l=70,
        margin_r=70,
        margin_v=220,
        encoding=1,
        emphasis_colour="&H0000FFFF",
    ),
}

_ASS_STYLE_COLOUR_RE = re.compile(r"^&H[0-9A-Fa-f]{8}$")


def _parse_timestamp(h: str | None, m: str, s: str, ms: str | None) -> float:
    hours = int(h or 0)
    minutes = int(m)
    seconds = int(s)
    millis = int(ms or 0)
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_vtt_with_end(content: str) -> list[TimedCue]:
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
        text = clean_vtt_text(text)
        if text and end > start:
            cues.append(TimedCue(start_seconds=start, end_seconds=end, text=text))

    return _deduplicate_progressive_timed(cues)


# v2 までの内部呼び出しと外部利用者の互換性を維持する。
_parse_vtt_with_end = parse_vtt_with_end


def _deduplicate_progressive_timed(cues: list[TimedCue]) -> list[TimedCue]:
    """プログレッシブ表示による部分文字列重複を除去する（終了時刻を保持）.

    YouTube 自動字幕は「前の行 + 新しい語」が積み上がる形式で配信されるため、
    そのまま焼き込むと同じ文が重なって表示される。ロジックは
    vtt_parser.deduplicate_progressive() と等価（終了時刻対応版）で、
    両者が同じ入力に対して同じ text 列を返すことをテストで保証している。
    """
    result: list[TimedCue] = []
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
                    result.append(
                        TimedCue(
                            start_seconds=cue.start_seconds,
                            end_seconds=cue.end_seconds,
                            text=delta,
                            emphasis=cue.emphasis,
                        )
                    )
                prev_text = text
                continue
            if prev_text in text:
                idx = text.find(prev_text)
                delta = (text[:idx] + text[idx + len(prev_text) :]).strip()
                if delta:
                    result.append(
                        TimedCue(
                            start_seconds=cue.start_seconds,
                            end_seconds=cue.end_seconds,
                            text=delta,
                            emphasis=cue.emphasis,
                        )
                    )
                prev_text = text
                continue

        result.append(cue)
        prev_text = text

    return result


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
            TimedCue(
                start_seconds=new_start,
                end_seconds=new_end,
                text=cue.text,
                emphasis=cue.emphasis,
            )
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


def _sanitize_ass_text(text: str) -> str:
    """ユーザー文字列から ASS の制御列と物理改行を除く."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    normalized = "".join(
        " " if ord(character) < 32 and character != "\n" else character
        for character in normalized
    )
    return normalized.replace("\n", r"\N")


def _get_preset(name: str) -> TelopPreset:
    try:
        preset = TELOP_PRESETS[name]
    except KeyError as exc:
        available = "、".join(TELOP_PRESETS)
        raise SubtitleBurnError(
            f"テロッププリセット「{name}」は利用できません。利用可能: {available}"
        ) from exc
    _validate_preset_colours(name, preset)
    return preset


def _validate_preset_colours(name: str, preset: TelopPreset) -> None:
    colour_fields = (
        "primary_colour",
        "secondary_colour",
        "outline_colour",
        "back_colour",
        "emphasis_colour",
    )
    for field_name in colour_fields:
        colour = getattr(preset, field_name)
        if not _ASS_STYLE_COLOUR_RE.fullmatch(colour):
            raise SubtitleBurnError(
                f"テロッププリセット「{name}」の色 {field_name} が不正です。"
                "&H と8桁の16進数で指定してください。"
            )


def _inline_colour(style_colour: str) -> str:
    """アルファ付き ASS スタイル色をインライン色へ変換する."""
    return f"&H{style_colour[4:]}&"


def _ass_number(value: float) -> str:
    return f"{value:g}"


def _style_line(name: str, font_name: str, preset: TelopPreset) -> str:
    values = (
        name,
        font_name,
        _ass_number(preset.font_size),
        preset.primary_colour,
        preset.secondary_colour,
        preset.outline_colour,
        preset.back_colour,
        "-1" if preset.bold else "0",
        "-1" if preset.italic else "0",
        "-1" if preset.underline else "0",
        "-1" if preset.strike_out else "0",
        _ass_number(preset.scale_x),
        _ass_number(preset.scale_y),
        _ass_number(preset.spacing),
        _ass_number(preset.angle),
        str(preset.border_style),
        _ass_number(preset.outline),
        _ass_number(preset.shadow),
        str(preset.alignment),
        str(preset.margin_l),
        str(preset.margin_r),
        str(preset.margin_v),
        str(preset.encoding),
    )
    return "Style: " + ",".join(values)


def _serialize_ass(
    output_path: Path,
    *,
    font_name: str,
    styles: list[tuple[str, TelopPreset]],
    events: list[str],
    play_res_x: int,
    play_res_y: int,
) -> Path:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        *(_style_line(name, font_name, preset) for name, preset in styles),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_ass(
    cues: list[TimedCue],
    output_path: Path,
    *,
    font_name: str,
    preset: str = "default",
    hook_text: str | None = None,
    hook_preset: str = "hook",
    play_res_x: int = _ASS_PLAY_RES_X,
    play_res_y: int = _ASS_PLAY_RES_Y,
) -> Path:
    """ASS 字幕ファイルを書き出す（既定は縦型ショート 1080x1920 基準）."""
    selected_preset = _get_preset(preset)
    selected_hook_preset: TelopPreset | None = None
    clean_hook: str | None = None
    if hook_text is not None:
        if not hook_text.strip():
            raise SubtitleBurnError("フックタイトルを入力してください。")
        selected_hook_preset = _get_preset(hook_preset)
        clean_hook = _sanitize_ass_text(hook_text)

    events: list[str] = []
    for cue in cues:
        start = _seconds_to_ass_time(cue.start_seconds)
        end = _seconds_to_ass_time(cue.end_seconds)
        text = _sanitize_ass_text(cue.text)
        if cue.emphasis:
            emphasis = _inline_colour(selected_preset.emphasis_colour)
            primary = _inline_colour(selected_preset.primary_colour)
            text = f"{{\\c{emphasis}}}{text}{{\\c{primary}}}"
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    styles = [("Default", selected_preset)]
    if selected_hook_preset is not None and clean_hook is not None:
        styles.append(("Hook", selected_hook_preset))
        events.append(f"Dialogue: 1,0:00:00.00,0:00:02.00,Hook,,0,0,0,,{clean_hook}")

    return _serialize_ass(
        output_path,
        font_name=font_name,
        styles=styles,
        events=events,
        play_res_x=play_res_x,
        play_res_y=play_res_y,
    )


def write_hook_ass(
    hook_text: str,
    output_path: Path,
    *,
    font_name: str,
    preset: str = "hook",
) -> Path:
    """冒頭 2 秒に表示するフックタイトル ASS を書き出す."""
    if not hook_text.strip():
        raise SubtitleBurnError("フックタイトルを入力してください。")
    selected_preset = _get_preset(preset)
    clean_hook = _sanitize_ass_text(hook_text)
    return _serialize_ass(
        output_path,
        font_name=font_name,
        styles=[("Hook", selected_preset)],
        events=[f"Dialogue: 1,0:00:00.00,0:00:02.00,Hook,,0,0,0,,{clean_hook}"],
        play_res_x=_ASS_PLAY_RES_X,
        play_res_y=_ASS_PLAY_RES_Y,
    )


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
    cues = parse_vtt_with_end(content)
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
    # 日本語ファイル名（例: ヒラギノ角ゴシック W3.ttc）でも検出できるよう別名を併用する.
    needles = (needle, *_MAC_FONT_FILENAME_ALIASES.get(needle, ()))
    for directory in _MAC_FONT_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            stem = path.stem.casefold().replace(" ", "")
            if any(n in stem for n in needles):
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
