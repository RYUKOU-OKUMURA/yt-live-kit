"""再生窓 WAV の波形を標準ライブラリだけで間引きする pure helper。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_OVERVIEW_POINTS = 1500
DEFAULT_ZOOM_POINTS = 500
DEFAULT_ZOOM_WINDOW_MS = 2000


def read_mono_pcm16(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != 16000:
            raise ValueError("playback WAV は mono 16 kHz PCM16 である必要があります。")
        sample_rate = reader.getframerate()
        frame_count = reader.getnframes()
        raw = reader.readframes(frame_count)
    if len(raw) != frame_count * 2:
        raise ValueError("playback WAV の frame 数と bytes が一致しません。")
    samples = list(struct.unpack(f"<{frame_count}h", raw))
    return samples, sample_rate


def duration_ms_from_samples(sample_count: int, sample_rate: int) -> int:
    if sample_rate <= 0:
        raise ValueError("sample_rate が不正です。")
    return int(sample_count * 1000 / sample_rate)


def downsample_max_amplitude(
    samples: Sequence[int],
    sample_rate: int,
    *,
    max_points: int = DEFAULT_OVERVIEW_POINTS,
    start_ms: int = 0,
    end_ms: int | None = None,
) -> list[dict[str, int]]:
    """バケットごとの最大振幅を返す。ms は再生窓内相対の整数ミリ秒。"""

    if not samples:
        return []
    if max_points <= 0:
        raise ValueError("max_points は正の整数が必要です。")
    total_duration_ms = duration_ms_from_samples(len(samples), sample_rate)
    window_start_ms = max(0, int(start_ms))
    window_end_ms = total_duration_ms if end_ms is None else min(total_duration_ms, int(end_ms))
    if window_end_ms <= window_start_ms:
        return []

    start_frame = int(window_start_ms * sample_rate / 1000)
    end_frame = int(window_end_ms * sample_rate / 1000)
    window = samples[start_frame:end_frame]
    if not window:
        return []

    window_duration_ms = window_end_ms - window_start_ms
    point_count = min(max_points, len(window))
    frames_per_bucket = max(1, math.ceil(len(window) / point_count))
    ms_per_bucket = window_duration_ms / point_count

    buckets: list[dict[str, int]] = []
    for index in range(point_count):
        bucket_start = index * frames_per_bucket
        bucket_end = min(len(window), bucket_start + frames_per_bucket)
        if bucket_start >= bucket_end:
            continue
        peak = max(abs(sample) for sample in window[bucket_start:bucket_end])
        buckets.append(
            {
                "ms": window_start_ms + int(round(index * ms_per_bucket)),
                "amp": int(peak),
            }
        )
    return buckets


def chart_columns(buckets: Sequence[Mapping[str, int]]) -> dict[str, list[int]]:
    return {
        "ms": [int(bucket["ms"]) for bucket in buckets],
        "amp": [int(bucket["amp"]) for bucket in buckets],
    }


def zoom_bounds(
    duration_ms: int,
    center_ms: int,
    *,
    window_ms: int = DEFAULT_ZOOM_WINDOW_MS,
) -> tuple[int, int]:
    """拡大表示用の [start_ms, end_ms) を返す。"""

    if duration_ms <= 0:
        raise ValueError("duration_ms は正の整数が必要です。")
    half = max(1, int(window_ms) // 2)
    center = max(0, min(int(center_ms), duration_ms - 1))
    start = max(0, center - half)
    end = min(duration_ms, center + half)
    if end <= start:
        end = min(duration_ms, start + 1)
    return start, end


def row_onset_from_playback_position(receipt: Mapping[str, Any], playback_relative_ms: int) -> int:
    """再生窓内 ms を row 内相対 onset ms へ変換する。"""

    played_from = int(receipt["played_from_ms"])
    played_duration = int(receipt["played_duration_ms"])
    position = int(playback_relative_ms)
    if position < 0 or position >= played_duration:
        raise ValueError("波形位置は再生窓内である必要があります。")
    return played_from + position


def overview_and_zoom(
    samples: Sequence[int],
    sample_rate: int,
    *,
    cursor_ms: int,
    overview_points: int = DEFAULT_OVERVIEW_POINTS,
    zoom_points: int = DEFAULT_ZOOM_POINTS,
    zoom_window_ms: int = DEFAULT_ZOOM_WINDOW_MS,
) -> tuple[list[dict[str, int]], list[dict[str, int]], int]:
    duration_ms = duration_ms_from_samples(len(samples), sample_rate)
    overview = downsample_max_amplitude(samples, sample_rate, max_points=overview_points)
    zoom_start, zoom_end = zoom_bounds(duration_ms, cursor_ms, window_ms=zoom_window_ms)
    zoom = downsample_max_amplitude(
        samples,
        sample_rate,
        max_points=zoom_points,
        start_ms=zoom_start,
        end_ms=zoom_end,
    )
    return overview, zoom, duration_ms
