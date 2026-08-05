from __future__ import annotations

from pathlib import Path
import wave

import pytest

from benchmarks.t1.waveform import (
    DEFAULT_OVERVIEW_POINTS,
    chart_columns,
    downsample_max_amplitude,
    overview_and_zoom,
    read_mono_pcm16,
    row_onset_from_playback_position,
    zoom_bounds,
)


def _write_pulse_wav(path: Path, *, duration_ms: int, pulse_at_ms: int, pulse_amp: int = 20000) -> None:
    sample_rate = 16000
    frame_count = duration_ms * 16
    pulse_frame = pulse_at_ms * 16
    frames = bytearray(frame_count * 2)
    for index in range(frame_count):
        value = pulse_amp if index == pulse_frame else 0
        frames[index * 2 : index * 2 + 2] = int(value).to_bytes(2, "little", signed=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(frames)


def test_downsample_caps_points_and_preserves_peak(tmp_path: Path) -> None:
    wav_path = tmp_path / "pulse.wav"
    _write_pulse_wav(wav_path, duration_ms=26000, pulse_at_ms=12345)
    samples, sample_rate = read_mono_pcm16(wav_path)
    buckets = downsample_max_amplitude(samples, sample_rate, max_points=1500)
    assert 1 <= len(buckets) <= 1500
    assert max(bucket["amp"] for bucket in buckets) == 20000
    peak_ms = max(buckets, key=lambda bucket: bucket["amp"])["ms"]
    assert abs(peak_ms - 12345) <= 50


def test_zoom_bounds_clamps_to_duration() -> None:
    assert zoom_bounds(26000, 100, window_ms=2000) == (0, 1100)
    assert zoom_bounds(26000, 25900, window_ms=2000) == (24900, 26000)


def test_row_onset_from_playback_position_converts_within_window() -> None:
    receipt = {"played_from_ms": 1500, "played_duration_ms": 5000}
    assert row_onset_from_playback_position(receipt, 250) == 1750
    with pytest.raises(ValueError):
        row_onset_from_playback_position(receipt, 5000)


def test_overview_and_zoom_returns_both_series(tmp_path: Path) -> None:
    wav_path = tmp_path / "short.wav"
    _write_pulse_wav(wav_path, duration_ms=5000, pulse_at_ms=2500)
    samples, sample_rate = read_mono_pcm16(wav_path)
    overview, zoom, duration_ms = overview_and_zoom(samples, sample_rate, cursor_ms=2500)
    assert duration_ms == 5000
    assert overview
    assert zoom
    assert len(overview) <= DEFAULT_OVERVIEW_POINTS
    assert chart_columns(overview)["ms"][0] == 0
