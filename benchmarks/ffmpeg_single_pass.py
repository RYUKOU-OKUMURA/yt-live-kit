#!/usr/bin/env python3
"""Compare the current FFmpeg multi-pass path with seek and filter-graph variants.

This is intentionally standalone benchmark code.  It does not import or call the
production short-generation services, and it creates all media below a temporary
directory by default.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import math
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_RUNS = 3
DEFAULT_WARMUPS = 1
SOURCE_WIDTH = 1280
SOURCE_HEIGHT = 720
SOURCE_FPS = 30
SOURCE_SEGMENT_SECONDS = 60
SOURCE_SEGMENTS = 3
SOURCE_AUDIO_RATE = 48_000
SOURCE_DURATION = SOURCE_SEGMENT_SECONDS * SOURCE_SEGMENTS
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
VIDEO_PRESET = "medium"
INTERMEDIATE_CRF = 16
FINAL_CRF = 20
AUDIO_BITRATE = "192k"

BLUR_LAYOUT_FILTER = (
    "split[a][b];"
    "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
    "gblur=sigma=20[bg];"
    "[b]scale=1080:-1[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)


class BenchmarkError(RuntimeError):
    """A benchmark command failed."""


@dataclass(frozen=True)
class Cue:
    start_sec: float
    end_sec: float
    text: str
    kind: str = "subtitle"


@dataclass(frozen=True)
class Case:
    case_id: str
    description: str
    segments: tuple[tuple[float, float], ...]
    subtitle_variant: str
    cues: tuple[Cue, ...]
    internal_boundaries_sec: tuple[float, ...]

    @property
    def expected_duration_sec(self) -> float:
        return sum(end - start for start, end in self.segments)


@dataclass
class MediaMetrics:
    duration_sec: float | None
    format_start_pts_sec: float | None
    format_end_pts_sec: float | None
    video_first_frame_pts_sec: float | None
    video_last_frame_pts_sec: float | None
    video_frame_count: int
    audio_first_frame_pts_sec: float | None
    audio_last_frame_pts_sec: float | None
    audio_last_end_pts_sec: float | None
    width: int | None
    height: int | None
    pixel_format: str | None
    video_fps: float | None
    audio_stream_count: int
    subtitle_stream_count: int
    size_bytes: int
    boundary_checks: list[dict[str, Any]]


@dataclass
class TimedRun:
    run_index: int
    wall_time_sec: float
    output_path: str
    command: str
    metrics: MediaMetrics | None = None
    error: str | None = None


@dataclass
class ModeResult:
    mode: str
    warmup_wall_time_sec: float | None
    warmup_command: str | None
    runs: list[TimedRun]

    @property
    def successful_runs(self) -> list[TimedRun]:
        return [run for run in self.runs if run.metrics is not None]

    @property
    def wall_times(self) -> list[float]:
        return [run.wall_time_sec for run in self.successful_runs]


def fixed_cases() -> tuple[Case, ...]:
    """Return the fixed cases required by G1-1.

    The fixture has three 60-second colour-coded source sections.  The 60-second
    and 180-second cases therefore expose deterministic internal joins that can
    be checked without relying on a real programme or external service.
    """

    return (
        Case(
            case_id="15-single-no-subtitle",
            description="15 秒・単一区間・字幕なし",
            segments=((12.0, 27.0),),
            subtitle_variant="none",
            cues=(),
            internal_boundaries_sec=(),
        ),
        Case(
            case_id="60-three-normal-subtitle",
            description="60 秒・3 区間・通常字幕",
            segments=((0.0, 20.0), (60.0, 80.0), (120.0, 140.0)),
            subtitle_variant="normal",
            cues=(
                Cue(4.0, 8.0, "通常字幕 1"),
                Cue(24.0, 28.0, "通常字幕 2"),
                Cue(44.0, 48.0, "通常字幕 3"),
            ),
            internal_boundaries_sec=(20.0, 40.0),
        ),
        Case(
            case_id="180-three-hook-subtitle",
            description="180 秒・3 区間・通常字幕 + Hook",
            segments=((0.0, 60.0), (60.0, 120.0), (120.0, 180.0)),
            subtitle_variant="hook",
            cues=(
                Cue(0.0, 2.0, "冒頭フック", kind="hook"),
                Cue(8.0, 14.0, "通常字幕 1"),
                Cue(68.0, 74.0, "通常字幕 2"),
                Cue(128.0, 134.0, "通常字幕 3"),
            ),
            internal_boundaries_sec=(60.0, 120.0),
        ),
    )


def run_command(
    command: Sequence[str],
    *,
    capture_output: bool = True,
    text_output: bool = True,
    timeout_sec: int = 7_200,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = subprocess.run(
            list(command),
            capture_output=capture_output,
            text=text_output if capture_output else False,
            check=False,
            timeout=timeout_sec,
        )
    except OSError as exc:
        raise BenchmarkError(f"コマンドを起動できませんでした: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError(f"コマンドが {timeout_sec} 秒でタイムアウトしました") from exc

    if result.returncode != 0:
        stderr_value = result.stderr
        stdout_value = result.stdout
        stderr = (
            stderr_value
            if isinstance(stderr_value, str)
            else stderr_value.decode(errors="replace")
            if isinstance(stderr_value, bytes)
            else ""
        )
        stdout = (
            stdout_value
            if isinstance(stdout_value, str)
            else stdout_value.decode(errors="replace")
            if isinstance(stdout_value, bytes)
            else ""
        )
        detail = (stderr or stdout).strip()
        if len(detail) > 4_000:
            detail = detail[-4_000:]
        raise BenchmarkError(
            f"終了コード {result.returncode}: {shlex.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def command_text(command: Sequence[str]) -> str:
    return shlex.join([str(value) for value in command])


def resolve_ffprobe(ffmpeg_path: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    ffmpeg_file = Path(ffmpeg_path)
    if ffmpeg_file.parent != Path("."):
        sibling = ffmpeg_file.with_name("ffprobe")
        if sibling.is_file():
            return str(sibling)
    return "ffprobe"


def version_line(ffmpeg_path: str) -> tuple[str, str]:
    result = run_command([ffmpeg_path, "-version"], timeout_sec=60)
    output = result.stdout if isinstance(result.stdout, str) else ""
    first_line = output.splitlines()[0] if output.splitlines() else ""
    return first_line, output


def subtitles_filter_available(ffmpeg_path: str) -> bool:
    result = run_command([ffmpeg_path, "-hide_banner", "-filters"], timeout_sec=60)
    output = "\n".join(
        part
        for part in (
            result.stdout if isinstance(result.stdout, str) else "",
            result.stderr if isinstance(result.stderr, str) else "",
        )
    )
    return any(line.strip().endswith(" subtitles") or " subtitles " in line for line in output.splitlines())


def write_ass(path: Path, cues: Sequence[Cue]) -> Path:
    def ass_time(seconds: float) -> str:
        centiseconds = int(round(seconds * 100))
        hours, remainder = divmod(centiseconds, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        seconds_part, centiseconds_part = divmod(remainder, 100)
        return f"{hours}:{minutes:02d}:{seconds_part:02d}.{centiseconds_part:02d}"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Hiragino Sans,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,180,1",
        "Style: Hook,Hiragino Sans,88,&H00FFFFFF,&H000000FF,&H00000000,&H60000000,-1,0,0,0,100,100,0,0,3,4,0,8,70,70,220,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        style = "Hook" if cue.kind == "hook" else "Default"
        text = cue.text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        lines.append(
            f"Dialogue: 0,{ass_time(cue.start_sec)},{ass_time(cue.end_sec)},"
            f"{style},,0,0,0,,{text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def escape_filter_path(path: Path) -> str:
    escaped = path.resolve().as_posix()
    for character in ("\\", "'", ":", "[", "]", ";"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def subtitle_filter(ass_path: Path) -> str:
    return f"subtitles=filename={escape_filter_path(ass_path)}"


def build_fixture(ffmpeg_path: str, output_path: Path) -> list[str]:
    video_inputs = [
        "testsrc2=size=1280x720:rate=30:duration=60",
        "testsrc2=size=1280x720:rate=30:duration=60",
        "testsrc2=size=1280x720:rate=30:duration=60",
    ]
    audio_inputs = [
        "sine=frequency=440:sample_rate=48000:duration=60",
        "sine=frequency=660:sample_rate=48000:duration=60",
        "sine=frequency=880:sample_rate=48000:duration=60",
    ]
    command: list[str] = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
    for video_input, audio_input in zip(video_inputs, audio_inputs, strict=True):
        command.extend(["-f", "lavfi", "-i", video_input])
        command.extend(["-f", "lavfi", "-i", audio_input])
    filter_graph = (
        "[0:v]colorchannelmixer=rr=1.30:gg=0.65:bb=0.65[v0];"
        "[2:v]colorchannelmixer=rr=0.65:gg=1.30:bb=0.65[v1];"
        "[4:v]colorchannelmixer=rr=0.65:gg=0.65:bb=1.30[v2];"
        "[v0][1:a][v1][3:a][v2][5:a]concat=n=3:v=1:a=1[v][a]"
    )
    command.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            VIDEO_PRESET,
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def encode_segment_command(
    ffmpeg_path: str,
    source_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    *,
    seek_mode: str,
) -> list[str]:
    duration = end_sec - start_sec
    command: list[str] = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
    if seek_mode == "input":
        command.extend(["-ss", f"{start_sec:g}", "-i", str(source_path), "-t", f"{duration:g}"])
    elif seek_mode == "output":
        command.extend(["-i", str(source_path), "-ss", f"{start_sec:g}", "-t", f"{duration:g}"])
    else:
        raise ValueError(f"Unknown seek mode: {seek_mode}")
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            VIDEO_PRESET,
            "-crf",
            str(INTERMEDIATE_CRF),
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            str(output_path),
        ]
    )
    return command


def layout_filter_chain(ass_path: Path | None) -> str:
    return BLUR_LAYOUT_FILTER + (f",{subtitle_filter(ass_path)}" if ass_path else "")


def final_layout_command(
    ffmpeg_path: str,
    source_path: Path,
    output_path: Path,
    ass_path: Path | None,
) -> list[str]:
    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_path)]
    command.extend(["-filter_complex", f"[0:v]{layout_filter_chain(ass_path)}[vout]"])
    command.extend(
        [
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            VIDEO_PRESET,
            "-crf",
            str(FINAL_CRF),
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            str(output_path),
        ]
    )
    return command


def concat_command(ffmpeg_path: str, list_path: Path, output_path: Path) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output_path),
    ]


def write_concat_list(path: Path, segment_paths: Sequence[Path]) -> None:
    lines = []
    for segment_path in segment_paths:
        escaped = str(segment_path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def single_pass_command(
    ffmpeg_path: str,
    source_path: Path,
    output_path: Path,
    case: Case,
    ass_path: Path | None,
) -> list[str]:
    graph: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    for index, (start_sec, end_sec) in enumerate(case.segments):
        video_label = f"v{index}"
        audio_label = f"a{index}"
        graph.append(
            f"[0:v]trim=start={start_sec:g}:end={end_sec:g},setpts=PTS-STARTPTS[{video_label}]"
        )
        graph.append(
            f"[0:a]atrim=start={start_sec:g}:end={end_sec:g},asetpts=PTS-STARTPTS[{audio_label}]"
        )
        video_labels.append(f"[{video_label}]")
        audio_labels.append(f"[{audio_label}]")
    concat_inputs = "".join(
        video_label + audio_label
        for video_label, audio_label in zip(video_labels, audio_labels, strict=True)
    )
    graph.append(f"{concat_inputs}concat=n={len(case.segments)}:v=1:a=1[vcat][acat]")
    graph.append(f"[vcat]{layout_filter_chain(ass_path)}[vout]")
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-filter_complex",
        ";".join(graph),
        "-map",
        "[vout]",
        "-map",
        "[acat]",
        "-c:v",
        "libx264",
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(FINAL_CRF),
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        str(output_path),
    ]
    return command


def parse_float(value: Any) -> float | None:
    if value is None or value == "N/A":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def probe_stream(ffprobe_path: str, output_path: Path, stream_selector: str) -> dict[str, Any]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        stream_selector,
        "-show_entries",
        "stream=index,codec_type,start_time,duration,width,height,pix_fmt,r_frame_rate",
        "-of",
        "json",
        str(output_path),
    ]
    result = run_command(command, timeout_sec=300)
    raw = result.stdout if isinstance(result.stdout, str) else "{}"
    streams = json.loads(raw).get("streams", [])
    return streams[0] if streams else {}


def probe_frames(
    ffprobe_path: str,
    output_path: Path,
    stream_selector: str,
) -> list[dict[str, Any]]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        stream_selector,
        "-show_frames",
        "-show_entries",
        "frame=pts_time,best_effort_timestamp_time,duration_time",
        "-of",
        "json",
        str(output_path),
    ]
    result = run_command(command, timeout_sec=900)
    raw = result.stdout if isinstance(result.stdout, str) else "{}"
    return json.loads(raw).get("frames", [])


def sample_rgb_frames(ffmpeg_path: str, output_path: Path) -> tuple[list[tuple[int, int, int]], list[str]]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(output_path),
        "-map",
        "0:v:0",
        "-vf",
        "scale=1:1,format=rgb24",
        "-f",
        "rawvideo",
        "-",
    ]
    result = run_command(
        command, capture_output=True, text_output=False, timeout_sec=900
    )
    raw = result.stdout if isinstance(result.stdout, bytes) else b""
    if len(raw) % 3:
        raise BenchmarkError("1 px RGB サンプルのバイト数が不正です")
    pixels = [tuple(raw[index : index + 3]) for index in range(0, len(raw), 3)]
    return pixels, [command_text(command)]


def detect_boundaries(
    ffmpeg_path: str,
    output_path: Path,
    expected_boundaries: Sequence[float],
    fps: float | None,
) -> list[dict[str, Any]]:
    if not expected_boundaries or not fps or fps <= 0:
        return []
    pixels, _ = sample_rgb_frames(ffmpeg_path, output_path)
    if len(pixels) < 2:
        return [
            {
                "expected_sec": boundary,
                "detected_sec": None,
                "difference_frames": None,
                "detected": False,
            }
            for boundary in expected_boundaries
        ]

    distances = [
        math.sqrt(sum((left[channel] - right[channel]) ** 2 for channel in range(3)))
        for left, right in zip(pixels, pixels[1:])
    ]
    search_radius_frames = max(2, round(fps * 3))
    checks: list[dict[str, Any]] = []
    for expected in expected_boundaries:
        expected_index = round(expected * fps)
        start = max(0, expected_index - search_radius_frames)
        end = min(len(distances), expected_index + search_radius_frames)
        if start >= end:
            checks.append(
                {
                    "expected_sec": expected,
                    "detected_sec": None,
                    "difference_frames": None,
                    "detected": False,
                }
            )
            continue
        index = max(range(start, end), key=distances.__getitem__)
        detected_sec = (index + 1) / fps
        difference_frames = abs(detected_sec - expected) * fps
        checks.append(
            {
                "expected_sec": expected,
                "detected_sec": detected_sec,
                "difference_frames": difference_frames,
                "detected": distances[index] > 5.0,
                "colour_distance": distances[index],
            }
        )
    return checks


def probe_output(
    ffmpeg_path: str,
    ffprobe_path: str,
    output_path: Path,
    case: Case,
) -> MediaMetrics:
    video_stream = probe_stream(ffprobe_path, output_path, "v:0")
    audio_stream = probe_stream(ffprobe_path, output_path, "a:0")
    video_frames = probe_frames(ffprobe_path, output_path, "v:0")
    audio_frames = probe_frames(ffprobe_path, output_path, "a:0")
    format_command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration,start_time,size:stream=index,codec_type",
        "-of",
        "json",
        str(output_path),
    ]
    format_result = run_command(format_command, timeout_sec=300)
    format_data = json.loads(format_result.stdout if isinstance(format_result.stdout, str) else "{}")
    format_info = format_data.get("format", {})
    stream_info = format_data.get("streams", [])
    stream_types = [stream.get("codec_type") for stream in stream_info]

    def frame_pts(frame: dict[str, Any]) -> float | None:
        return parse_float(frame.get("best_effort_timestamp_time", frame.get("pts_time")))

    video_pts = [value for value in (frame_pts(frame) for frame in video_frames) if value is not None]
    audio_pts = [value for value in (frame_pts(frame) for frame in audio_frames) if value is not None]
    last_audio_end = None
    if audio_frames and audio_pts:
        last_frame = audio_frames[-1]
        duration = parse_float(last_frame.get("duration_time")) or 0.0
        last_audio_end = audio_pts[-1] + duration

    fps = None
    rate = video_stream.get("r_frame_rate")
    if isinstance(rate, str) and "/" in rate:
        numerator, denominator = rate.split("/", 1)
        try:
            fps = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            fps = None

    return MediaMetrics(
        duration_sec=parse_float(format_info.get("duration")),
        format_start_pts_sec=parse_float(format_info.get("start_time")),
        format_end_pts_sec=(
            parse_float(format_info.get("start_time")) or 0.0
        )
        + (parse_float(format_info.get("duration")) or 0.0),
        video_first_frame_pts_sec=video_pts[0] if video_pts else None,
        video_last_frame_pts_sec=video_pts[-1] if video_pts else None,
        video_frame_count=len(video_frames),
        audio_first_frame_pts_sec=audio_pts[0] if audio_pts else None,
        audio_last_frame_pts_sec=audio_pts[-1] if audio_pts else None,
        audio_last_end_pts_sec=last_audio_end,
        width=int(video_stream["width"]) if video_stream.get("width") else None,
        height=int(video_stream["height"]) if video_stream.get("height") else None,
        pixel_format=video_stream.get("pix_fmt"),
        video_fps=fps,
        audio_stream_count=stream_types.count("audio"),
        subtitle_stream_count=stream_types.count("subtitle"),
        size_bytes=int(format_info.get("size", output_path.stat().st_size)),
        boundary_checks=detect_boundaries(
            ffmpeg_path, output_path, case.internal_boundaries_sec, fps
        ),
    )


def prepare_case_assets(workdir: Path, case: Case) -> Path | None:
    if not case.cues:
        return None
    return write_ass(workdir / f"{case.case_id}.ass", case.cues)


def run_mode(
    mode: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    source_path: Path,
    case: Case,
    ass_path: Path | None,
    workdir: Path,
    *,
    runs: int,
    warmups: int,
    preserve_outputs: bool,
) -> ModeResult:
    mode_dir = workdir / case.case_id / mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    warmup_wall_time: float | None = None
    warmup_command_text: str | None = None

    def build_command(output_path: Path, run_index: int) -> list[list[list[str]]]:
        if mode in {"input-seek", "output-seek"}:
            seek_mode = "input" if mode == "input-seek" else "output"
            segment_paths = []
            for index, (start, end) in enumerate(case.segments, start=1):
                segment_paths.append(mode_dir / f"run-{run_index:02d}-seg-{index:02d}.mp4")
            segment_commands = [
                encode_segment_command(
                    ffmpeg_path,
                    source_path,
                    segment_path,
                    start,
                    end,
                    seek_mode=seek_mode,
                )
                for segment_path, (start, end) in zip(segment_paths, case.segments, strict=True)
            ]
            list_path = mode_dir / f"run-{run_index:02d}-concat.txt"
            concat_path = mode_dir / f"run-{run_index:02d}-concat.mp4"
            final_path = output_path
            # The returned command is assembled as a shell-safe command list joined by
            # a newline in the report; execution is handled separately below.
            return [
                segment_commands,
                [concat_command(ffmpeg_path, list_path, concat_path)],
                [final_layout_command(ffmpeg_path, concat_path, final_path, ass_path)],
            ]
        return [[single_pass_command(ffmpeg_path, source_path, output_path, case, ass_path)]]

    def command_lines(commands: list[list[list[str]]]) -> str:
        return "\n".join(command_text(command) for group in commands for command in group)

    def execute(commands: Any) -> None:
        if mode in {"input-seek", "output-seek"}:
            segment_paths = [
                mode_dir / f"run-{current_index:02d}-seg-{index:02d}.mp4"
                for index in range(1, len(case.segments) + 1)
            ]
            list_path = mode_dir / f"run-{current_index:02d}-concat.txt"
            concat_path = mode_dir / f"run-{current_index:02d}-concat.mp4"
            write_concat_list(list_path, segment_paths)
            for group in commands:
                for command in group:
                    run_command(command)
            # The concat list is an input, so it can be removed after the final pass.
            list_path.unlink(missing_ok=True)
            for segment_path in segment_paths:
                segment_path.unlink(missing_ok=True)
            concat_path.unlink(missing_ok=True)
        else:
            run_command(commands[0][0])

    for warmup_index in range(1, warmups + 1):
        current_index = -(warmup_index)
        output_path = mode_dir / f"warmup-{warmup_index:02d}.mp4"
        commands = build_command(output_path, current_index)
        started = time.perf_counter()
        execute(commands)
        elapsed = time.perf_counter() - started
        warmup_wall_time = elapsed
        warmup_command_text = command_lines(commands)
        if not preserve_outputs:
            output_path.unlink(missing_ok=True)

    timed_runs: list[TimedRun] = []
    for current_index in range(1, runs + 1):
        output_path = mode_dir / f"run-{current_index:02d}.mp4"
        commands = build_command(output_path, current_index)
        command_repr = command_lines(commands)
        started = time.perf_counter()
        try:
            execute(commands)
            elapsed = time.perf_counter() - started
            metrics = probe_output(ffmpeg_path, ffprobe_path, output_path, case)
            timed_runs.append(
                TimedRun(
                    run_index=current_index,
                    wall_time_sec=elapsed,
                    output_path=str(output_path),
                    command=command_repr,
                    metrics=metrics,
                )
            )
        except (BenchmarkError, OSError, ValueError, json.JSONDecodeError) as exc:
            elapsed = time.perf_counter() - started
            timed_runs.append(
                TimedRun(
                    run_index=current_index,
                    wall_time_sec=elapsed,
                    output_path=str(output_path),
                    command=command_repr,
                    error=str(exc),
                )
            )
        finally:
            if not preserve_outputs:
                output_path.unlink(missing_ok=True)

    return ModeResult(
        mode=mode,
        warmup_wall_time_sec=warmup_wall_time,
        warmup_command=warmup_command_text,
        runs=timed_runs,
    )


def median_min_max(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"median_sec": None, "min_sec": None, "max_sec": None}
    return {
        "median_sec": statistics.median(values),
        "min_sec": min(values),
        "max_sec": max(values),
    }


def boundary_checks_valid(
    checks: Sequence[dict[str, Any]],
    expected_boundaries: Sequence[float],
    fps: float | None,
) -> bool:
    """Require every expected colour transition to be detected near its target."""
    if len(checks) != len(expected_boundaries) or not expected_boundaries:
        return not expected_boundaries
    effective_fps = fps or SOURCE_FPS
    max_seconds = (1.0 + 1e-6) / effective_fps
    for check, expected in zip(checks, expected_boundaries, strict=True):
        detected = check.get("detected_sec")
        if not check.get("detected") or detected is None:
            return False
        if abs(float(detected) - expected) > max_seconds:
            return False
    return True


def summarize_gate(case: Case, modes: dict[str, ModeResult]) -> dict[str, Any]:
    required_modes = ("input-seek", "output-seek", "single-pass")
    failed_runs = {
        mode: [run.error for run in modes[mode].runs if run.error]
        for mode in required_modes
    }
    if any(failed_runs.values()):
        detail = "; ".join(
            f"{mode}: {errors[0]}" for mode, errors in failed_runs.items() if errors
        )
        return {
            "status": "blocked",
            "reason": f"計測が完了していません: {detail}",
        }
    input_runs = modes["input-seek"].successful_runs
    single_runs = modes["single-pass"].successful_runs
    if not input_runs or not single_runs:
        return {"status": "blocked", "reason": "計測 run がありません"}
    input_median = statistics.median(run.wall_time_sec for run in input_runs)
    single_median = statistics.median(run.wall_time_sec for run in single_runs)
    speedup = 1.0 - (single_median / input_median)

    boundary_deltas: list[float] = []
    audio_deltas: list[float] = []
    audio_start_errors: list[float] = []
    audio_end_errors: list[float] = []
    audio_streams_match = True
    boundary_observations_valid = True
    baseline = input_runs[0].metrics
    assert baseline is not None
    for run in single_runs:
        metrics = run.metrics
        assert metrics is not None
        base_checks = baseline.boundary_checks
        candidate_checks = metrics.boundary_checks
        boundary_observations_valid = boundary_observations_valid and boundary_checks_valid(
            base_checks, case.internal_boundaries_sec, baseline.video_fps
        ) and boundary_checks_valid(
            candidate_checks, case.internal_boundaries_sec, metrics.video_fps
        )
        for base_check, candidate_check in zip(base_checks, candidate_checks, strict=False):
            if base_check.get("detected_sec") is not None and candidate_check.get("detected_sec") is not None:
                boundary_deltas.append(
                    abs(float(base_check["detected_sec"]) - float(candidate_check["detected_sec"]))
                    * (metrics.video_fps or SOURCE_FPS)
                )
        if baseline.audio_last_end_pts_sec is not None and metrics.audio_last_end_pts_sec is not None:
            audio_deltas.append(abs(metrics.audio_last_end_pts_sec - baseline.audio_last_end_pts_sec))
        if metrics.audio_first_frame_pts_sec is not None:
            audio_start_errors.append(abs(metrics.audio_first_frame_pts_sec))
        if metrics.audio_last_end_pts_sec is not None:
            audio_end_errors.append(abs(metrics.audio_last_end_pts_sec - case.expected_duration_sec))
        audio_streams_match = audio_streams_match and (
            baseline.audio_stream_count > 0 and metrics.audio_stream_count == baseline.audio_stream_count
        )

    max_boundary_delta = max(boundary_deltas, default=None)
    max_audio_delta = max(audio_deltas, default=None)
    max_audio_start_error = max(audio_start_errors, default=None)
    max_audio_end_error = max(audio_end_errors, default=None)
    subtitle_streams_match = all(
        run.metrics is not None and run.metrics.subtitle_stream_count == baseline.subtitle_stream_count
        for run in single_runs
    )
    speed_gate = speedup >= 0.25
    boundary_gate = boundary_observations_valid and (
        max_boundary_delta is not None and max_boundary_delta <= 1.0 + 1e-6
        if case.internal_boundaries_sec
        else True
    )
    audio_gate = (
        audio_streams_match
        and max_audio_start_error is not None
        and max_audio_end_error is not None
        and max_audio_delta is not None
        and max_audio_delta <= 1.0 / SOURCE_FPS
        and max_audio_start_error <= 1.0 / SOURCE_FPS
        and max_audio_end_error <= 1.0 / SOURCE_FPS
    )
    automatic_gate = speed_gate and boundary_gate and audio_gate and subtitle_streams_match
    return {
        "status": "candidate" if automatic_gate else "reject",
        "speedup_fraction": speedup,
        "speedup_percent": speedup * 100,
        "speed_gate": speed_gate,
        "max_boundary_delta_frames": max_boundary_delta,
        "boundary_observations_valid": boundary_observations_valid,
        "boundary_gate": boundary_gate,
        "max_audio_end_delta_sec": max_audio_delta,
        "max_audio_start_error_sec": max_audio_start_error,
        "max_audio_error_vs_expected_sec": max_audio_end_error,
        "audio_streams_match": audio_streams_match,
        "audio_baseline_gate": max_audio_delta is not None and max_audio_delta <= 1.0 / SOURCE_FPS,
        "audio_gate": audio_gate,
        "subtitle_streams_match": subtitle_streams_match,
        "automatic_gate": automatic_gate,
        "subtitle_visual_check": "manual_required",
        "adoption_gate": False,
        "adoption_note": "字幕の視覚比較は自動判定せず、手動確認が必要",
    }


def json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    return value


MODE_NAMES = ("input-seek", "output-seek", "single-pass")


def mode_order_for_case(case_index: int) -> tuple[str, ...]:
    """Rotate the deterministic order so one mode is not always last."""
    offset = case_index % len(MODE_NAMES)
    return MODE_NAMES[offset:] + MODE_NAMES[:offset]


def build_report(
    ffmpeg_path: str,
    ffprobe_path: str,
    ffmpeg_version: str,
    cases: Sequence[Case],
    mode_results: dict[str, dict[str, ModeResult]],
    gates: dict[str, dict[str, Any]],
    mode_orders: dict[str, Sequence[str]],
    *,
    source_command: str,
    workdir: Path,
    runs: int,
    warmups: int,
    subtitle_available: bool,
    blocker: str | None,
    keep_workdir: bool,
) -> dict[str, Any]:
    case_reports = []
    for case in cases:
        modes = mode_results[case.case_id]
        case_reports.append(
            {
                "case": asdict(case),
                "expected_duration_sec": case.expected_duration_sec,
                "mode_execution_order": list(mode_orders.get(case.case_id, MODE_NAMES)),
                "modes": {
                    mode: {
                        "warmup_wall_time_sec": result.warmup_wall_time_sec,
                        "warmup_command": result.warmup_command,
                        "wall_time_summary": median_min_max(result.wall_times),
                        "runs": [asdict(run) for run in result.runs],
                    }
                    for mode, result in modes.items()
                },
                "adoption_gate": gates[case.case_id],
            }
        )
    return json_ready(
        {
            "schema_version": 1,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ffmpeg_path": ffmpeg_path,
            "ffprobe_path": ffprobe_path,
            "ffmpeg_version": ffmpeg_version,
            "fixture": {
                "duration_sec": SOURCE_DURATION,
                "size": f"{SOURCE_WIDTH}x{SOURCE_HEIGHT}",
                "fps": SOURCE_FPS,
                "audio_rate": SOURCE_AUDIO_RATE,
                "description": "3 区間の色付き testsrc2 と 440 / 660 / 880 Hz sine を concat したローカル fixture",
                "command": source_command,
            },
            "settings": {
                "runs_after_warmup": runs,
                "warmups_per_mode": warmups,
                "video_preset": VIDEO_PRESET,
                "intermediate_crf": INTERMEDIATE_CRF,
                "final_crf": FINAL_CRF,
                "audio_bitrate": AUDIO_BITRATE,
                "output_size": f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}",
            },
            "subtitle_filter_available": subtitle_available,
            "blocker": blocker,
            "workdir_for_debug": str(workdir)
            if keep_workdir
            else "temporary directory; removed after the run",
            "cases": case_reports,
        }
    )


def print_summary(report: dict[str, Any]) -> None:
    print(f"FFmpeg: {report['ffmpeg_version']}")
    print(f"字幕フィルタ: {'利用可能' if report['subtitle_filter_available'] else '利用不可'}")
    for case_report in report["cases"]:
        print(f"\n{case_report['case']['case_id']} ({case_report['case']['description']})")
        for mode, mode_report in case_report["modes"].items():
            summary = mode_report["wall_time_summary"]
            print(
                f"  {mode}: median={summary['median_sec']!s}s "
                f"min={summary['min_sec']!s}s max={summary['max_sec']!s}s"
            )
        gate = case_report["adoption_gate"]
        print(
            f"  gate: {gate.get('status')} "
            f"speedup={gate.get('speedup_percent', 'n/a')}% "
            f"boundary_delta_frames={gate.get('max_boundary_delta_frames', 'n/a')}"
        )
    if report.get("blocker"):
        print(f"\nブロッカー: {report['blocker']}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-path", default=DEFAULT_FFMPEG)
    parser.add_argument("--ffprobe-path")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument(
        "--case-id",
        action="append",
        help="実行対象を固定 case ID に絞る（複数指定可）。省略時は全ケース",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="JSON レポートの保存先。省略時は一時レポートのみ標準出力へ表示する",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="system temporary workdir と timed outputs を削除せず保持する",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.runs != DEFAULT_RUNS or args.warmups < DEFAULT_WARMUPS:
        print(
            f"G1 benchmark は --runs {DEFAULT_RUNS} と --warmups {DEFAULT_WARMUPS} 以上を要求します",
            file=sys.stderr,
        )
        return 2
    if args.report is not None and args.report.exists():
        print(
            f"report は既に存在します。別名を指定してください: {args.report}",
            file=sys.stderr,
        )
        return 2

    ffmpeg_path = args.ffmpeg_path
    ffprobe_path = resolve_ffprobe(ffmpeg_path, args.ffprobe_path)
    all_cases = fixed_cases()
    selected_case_ids = set(args.case_id or ())
    unknown_case_ids = selected_case_ids - {case.case_id for case in all_cases}
    if unknown_case_ids:
        print(
            "未知の --case-id: " + ", ".join(sorted(unknown_case_ids)),
            file=sys.stderr,
        )
        return 2
    cases = tuple(
        case
        for case in all_cases
        if not selected_case_ids or case.case_id in selected_case_ids
    )
    blocker: str | None = None
    try:
        ffmpeg_version, _ = version_line(ffmpeg_path)
        subtitle_available = subtitles_filter_available(ffmpeg_path)
    except BenchmarkError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.keep_workdir:
        workdir_context = nullcontext(
            Path(tempfile.mkdtemp(prefix="yt-live-kit-ffmpeg-benchmark-"))
        )
    else:
        workdir_context = tempfile.TemporaryDirectory(
            prefix="yt-live-kit-ffmpeg-benchmark-"
        )
    with workdir_context as temp_dir:
        workdir = Path(temp_dir)
        source_path = workdir / "fixture-source.mp4"
        source_command = build_fixture(ffmpeg_path, source_path)
        try:
            run_command(source_command, timeout_sec=7_200)
        except BenchmarkError as exc:
            blocker = f"fixture 生成に失敗: {exc}"
            report = build_report(
                ffmpeg_path,
                ffprobe_path,
                ffmpeg_version,
                cases,
                {case.case_id: {} for case in cases},
                {case.case_id: {"status": "blocked", "reason": blocker} for case in cases},
                {case.case_id: mode_order_for_case(index) for index, case in enumerate(cases)},
                source_command=command_text(source_command),
                workdir=workdir,
                runs=args.runs,
                warmups=args.warmups,
                subtitle_available=subtitle_available,
                blocker=blocker,
                keep_workdir=args.keep_workdir,
            )
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print_summary(report)
            return 2

        mode_results: dict[str, dict[str, ModeResult]] = {}
        gates: dict[str, dict[str, Any]] = {}
        mode_orders: dict[str, Sequence[str]] = {}
        for case_index, case in enumerate(cases):
            mode_order = mode_order_for_case(case_index)
            mode_orders[case.case_id] = mode_order
            if case.cues and not subtitle_available:
                blocker = "この FFmpeg に subtitles フィルタが無いため、字幕 / Hook ケースを実行できません"
                mode_results[case.case_id] = {
                    mode: ModeResult(mode, None, None, [])
                    for mode in MODE_NAMES
                }
                gates[case.case_id] = {"status": "blocked", "reason": blocker}
                continue
            ass_path = prepare_case_assets(workdir, case)
            mode_results[case.case_id] = {}
            for mode in mode_order:
                try:
                    mode_results[case.case_id][mode] = run_mode(
                        mode,
                        ffmpeg_path,
                        ffprobe_path,
                        source_path,
                        case,
                        ass_path,
                        workdir,
                        runs=args.runs,
                        warmups=args.warmups,
                        preserve_outputs=args.keep_workdir,
                    )
                except (BenchmarkError, OSError, ValueError) as exc:
                    mode_results[case.case_id][mode] = ModeResult(
                        mode=mode,
                        warmup_wall_time_sec=None,
                        warmup_command=None,
                        runs=[
                            TimedRun(
                                run_index=index,
                                wall_time_sec=0.0,
                                output_path=str(workdir / case.case_id / mode),
                                command="",
                                error=str(exc),
                            )
                            for index in range(1, args.runs + 1)
                        ],
                    )
            gates[case.case_id] = summarize_gate(case, mode_results[case.case_id])

            failed = gates[case.case_id].get("status") == "blocked"
            if failed:
                detail = gates[case.case_id].get("reason", "計測に失敗しました")
                blocker = f"{blocker}; {case.case_id}: {detail}" if blocker else f"{case.case_id}: {detail}"

        report = build_report(
            ffmpeg_path,
            ffprobe_path,
            ffmpeg_version,
            cases,
            mode_results,
            gates,
            mode_orders,
            source_command=command_text(source_command),
            workdir=workdir,
            runs=args.runs,
            warmups=args.warmups,
            subtitle_available=subtitle_available,
            blocker=blocker,
            keep_workdir=args.keep_workdir,
        )
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_summary(report)
        if args.keep_workdir:
            print(f"保持した一時 workdir: {workdir}")

    return 0 if all(case["adoption_gate"].get("status") != "blocked" for case in report["cases"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
