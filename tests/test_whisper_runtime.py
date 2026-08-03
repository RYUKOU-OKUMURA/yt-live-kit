"""S9-3 whisper runtime の mock / fixture 中心テスト."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.transcript import TranscriptArtifactStatus
from yt_live_kit.services.ytdlp import AudioSpanRange, AudioSpanResult
from yt_live_kit.services.whisper_runtime import (
    WhisperCapability,
    WhisperBusyError,
    WhisperPreflightError,
    WhisperProcessResult,
    WhisperRangeStatus,
    _job_gate,
    build_whisper_argv,
    parse_whisper_full_json,
    preflight_whisper_runtime,
    run_selected_ranges,
)


VIDEO_ID = "IJvd6k6ZmUo"


def _full_payload(*, text: str = "こんにちは", start_ms: int = 0, end_ms: int = 500) -> dict:
    return {
        "systeminfo": "whisper.cpp test fixture",
        "model": {"type": "test"},
        "params": {"language": "ja"},
        "result": {"language": "ja"},
        "transcription": [
            {
                "timestamps": {"from": start_ms, "to": end_ms},
                "text": text,
            }
        ],
    }


def _fake_settings(tmp_path: Path) -> Settings:
    binary = tmp_path / "whisper-cli"
    binary.write_bytes(b"whisper-binary-fixture")
    binary.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"whisper-model-fixture")
    model_sha = hashlib.sha256(model.read_bytes()).hexdigest()
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"ffmpeg-fixture")
    ffmpeg.chmod(0o755)
    return Settings(
        data_dir=tmp_path / "data",
        ytdlp_path="yt-dlp-fixture",
        whisper_binary_path=str(binary),
        whisper_binary_sha256=binary_sha,
        whisper_binary_version="1.9.1",
        whisper_model_path=str(model),
        whisper_model_sha256=model_sha,
        whisper_output_schema="whisper-cli-json-full-v1",
        ffmpeg_path=str(ffmpeg),
        whisper_timeout=3,
    )


def _fake_capability(settings: Settings) -> WhisperCapability:
    binary = Path(settings.whisper_binary_path).resolve()
    model = Path(settings.whisper_model_path).resolve()
    return WhisperCapability(
        binary_path=str(binary),
        binary_bytes=binary.stat().st_size,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        version="1.9.1",
        supported_flags=(
            "--beam-size",
            "--best-of",
            "--file",
            "--language",
            "--model",
            "--no-fallback",
            "--output-file",
            "--output-json",
            "--output-json-full",
            "--processors",
            "--prompt",
            "--temperature",
            "--threads",
        ),
        json_timestamp_capability=True,
        model_name=settings.whisper_model_name,
        model_path=str(model),
        model_bytes=model.stat().st_size,
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        ffmpeg_path=settings.ffmpeg_path,
        ffmpeg_version="fixture",
        ffmpeg_capabilities=("audio_conversion", "download_sections"),
    )


def _span(settings: Settings, item: AudioSpanRange, *, cache_hit: bool = False) -> AudioSpanResult:
    path = settings.data_dir / VIDEO_ID / "transcripts" / "audio_cache" / f"{item.start_ms}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"audio-{item.start_ms}".encode()
    path.write_bytes(content)
    return AudioSpanResult(
        video_id=VIDEO_ID,
        range=item,
        path=path,
        audio_bytes=content,
        audio_input_fingerprint=hashlib.sha256(content).hexdigest(),
        source_metadata={
            "video_id": VIDEO_ID,
            "source_url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
            "fixture": "s9-3",
        },
        sample_rate=16_000,
        channel=1,
        codec="pcm_s16le",
        ffmpeg_settings={
            "container": "wav",
            "sample_rate": 16_000,
            "channel": 1,
            "codec": "pcm_s16le",
            "selector": "bestaudio/best",
        },
        cache_hit=cache_hit,
        request_fingerprint=hashlib.sha256(content + b"request").hexdigest(),
    )


def test_preflight_rejects_wrong_binary_hash(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    settings = settings.model_copy(update={"whisper_binary_sha256": "0" * 64})
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.shutil.which",
        lambda value: value,
    )

    with pytest.raises(WhisperPreflightError, match="SHA-256"):
        preflight_whisper_runtime(settings)


def _patch_successful_preflight(monkeypatch):
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.shutil.which",
        lambda value: value,
    )
    help_text = " ".join(
        (
            "--model",
            "--file",
            "--language",
            "--prompt",
            "--output-json",
            "--output-json-full",
            "--output-file",
            "--threads",
            "--processors",
            "--beam-size",
            "--best-of",
            "--temperature",
            "--no-fallback",
        )
    )

    def fake_inspection(argv, *, timeout, label):
        if "--version" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "whisper.cpp version: 1.9.1", "")
        if "--help" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, help_text, "")
        if "-filters" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "aresample", "")
        if "-encoders" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "pcm_s16le", "")
        return __import__("subprocess").CompletedProcess(argv, 0, "ffmpeg version 8.1.2", "")

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._run_inspection", fake_inspection)


def test_preflight_checks_version_model_capability_and_ffmpeg(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    _patch_successful_preflight(monkeypatch)
    capability = preflight_whisper_runtime(settings)
    assert capability.version == "1.9.1"
    assert capability.model_sha256 == settings.whisper_model_sha256
    assert capability.json_timestamp_capability is True
    assert "audio_conversion" in capability.ffmpeg_capabilities

    def wrong_version(argv, *, timeout, label):
        if "--version" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "whisper.cpp version: 1.8.0", "")
        return __import__("subprocess").CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._run_inspection", wrong_version)
    with pytest.raises(WhisperPreflightError, match="version"):
        preflight_whisper_runtime(settings)


@pytest.mark.parametrize(
    "update",
    (
        {"whisper_model_path": "missing-model.bin"},
        {"whisper_model_sha256": "0" * 64},
    ),
)
def test_preflight_rejects_missing_or_wrong_model(monkeypatch, tmp_path, update):
    settings = _fake_settings(tmp_path).model_copy(update=update)
    _patch_successful_preflight(monkeypatch)
    with pytest.raises(WhisperPreflightError, match="model"):
        preflight_whisper_runtime(settings)


def test_preflight_rejects_missing_json_capability_and_ffmpeg(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    _patch_successful_preflight(monkeypatch)

    def no_json(argv, *, timeout, label):
        if "--version" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "whisper.cpp version: 1.9.1", "")
        if "--help" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "--model --file --language", "")
        return __import__("subprocess").CompletedProcess(argv, 0, "ffmpeg version 8.1.2", "")

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._run_inspection", no_json)
    with pytest.raises(WhisperPreflightError, match="capability"):
        preflight_whisper_runtime(settings)

    _patch_successful_preflight(monkeypatch)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.shutil.which",
        lambda value: None if value == settings.ffmpeg_path else value,
    )
    with pytest.raises(WhisperPreflightError, match="FFmpeg"):
        preflight_whisper_runtime(settings)


def test_strict_full_json_rejects_unknown_schema_and_out_of_range_cue():
    unknown = {**_full_payload(), "unknown": True}
    with pytest.raises(Exception, match="未知"):
        parse_whisper_full_json(unknown)

    mismatched_offsets = _full_payload()
    mismatched_offsets["transcription"][0]["offsets"] = {"from": 10, "to": 500}
    with pytest.raises(Exception, match="一致"):
        parse_whisper_full_json(mismatched_offsets)

    with pytest.raises(Exception, match="範囲外"):
        parse_whisper_full_json(
            _full_payload(start_ms=900, end_ms=1_200),
            absolute_start_ms=0,
            relative_duration_ms=1_000,
            allowed_absolute_range=AudioSpanRange(0, 1_000),
        )


def test_build_argv_is_fixed_full_json_and_contains_adopted_settings():
    settings = Settings()
    argv = build_whisper_argv(
        binary_path="/opt/homebrew/bin/whisper-cli",
        model_path="/tmp/model.bin",
        audio_path="/tmp/span.wav",
        output_json_path="/tmp/result.json",
        settings=settings,
        selected_range=AudioSpanRange(1_000, 2_000),
    )
    assert "--output-json-full" in argv
    assert "--language" in argv and argv[argv.index("--language") + 1] == "ja"
    assert "--no-fallback" not in argv
    assert "--vad" not in argv
    assert argv[argv.index("--duration") + 1] == "1000"


def test_selected_ranges_are_serial_and_absolute_cues_are_saved(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    capability = _fake_capability(settings)
    ranges = (AudioSpanRange(1_000, 2_000), AudioSpanRange(4_000, 5_000))
    spans = [_span(settings, item) for item in ranges]
    audio_calls: list[int] = []
    whisper_calls: list[str] = []

    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.preflight_whisper_runtime",
        lambda _settings: capability,
    )

    def fake_audio(_video_id, item, _settings, **kwargs):
        audio_calls.append(item.start_ms)
        return spans[len(audio_calls) - 1]

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime.prepare_audio_span", fake_audio)

    def fake_process(argv, *, timeout_sec):
        whisper_calls.append(argv[argv.index("--file") + 1])
        output_prefix = Path(argv[argv.index("--output-file") + 1])
        output_prefix.with_suffix(".json").write_text(
            json.dumps(_full_payload(start_ms=0, end_ms=500), ensure_ascii=False),
            encoding="utf-8",
        )
        return WhisperProcessResult(tuple(argv), "", "", 0, False, 5)

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._process_whisper", fake_process)

    result = run_selected_ranges(VIDEO_ID, ranges, settings, job_id="job-s9-3")

    assert audio_calls == [1_000, 4_000]
    assert len(whisper_calls) == 2
    assert result.status == "success"
    assert result.is_high_precision is True
    assert result.artifact is not None
    assert result.artifact.status is TranscriptArtifactStatus.SUCCESS
    assert result.artifact.source_ref == "transcripts/audio_cache/1000.wav"
    assert [cue.start_ms for cue in result.artifact.cues] == [1_000, 4_000]
    assert [item.range_index for item in result.range_results] == [1, 2]
    assert all(item.status is WhisperRangeStatus.SUCCESS for item in result.range_results)


def test_cache_hit_skips_whisper_subprocess(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    capability = _fake_capability(settings)
    item = AudioSpanRange(1_000, 2_000)
    first_span = _span(settings, item)

    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.preflight_whisper_runtime",
        lambda _settings: capability,
    )
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.prepare_audio_span",
        lambda *_args, **_kwargs: first_span,
    )

    def write_result(argv, *, timeout_sec):
        output_prefix = Path(argv[argv.index("--output-file") + 1])
        output_prefix.with_suffix(".json").write_text(json.dumps(_full_payload()), encoding="utf-8")
        return WhisperProcessResult(tuple(argv), "", "", 0, False, 1)

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._process_whisper", write_result)
    first = run_selected_ranges(VIDEO_ID, [item], settings, job_id="first")
    assert first.cache_hit is False

    cached_span = _span(settings, item, cache_hit=True)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.prepare_audio_span",
        lambda *_args, **_kwargs: cached_span,
    )

    def fail_process(*args, **kwargs):
        raise AssertionError("artifact cache hit では Whisper を再実行しない")

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._process_whisper", fail_process)
    second = run_selected_ranges(VIDEO_ID, [item], settings, job_id="second")
    assert second.cache_hit is True
    assert second.status == "success"
    assert second.artifact_fingerprint == first.artifact_fingerprint
    assert second.range_results[0].cache_hit is True


def test_partial_failure_is_not_high_precision_and_has_range_diagnostic(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    capability = _fake_capability(settings)
    ranges = (AudioSpanRange(1_000, 2_000), AudioSpanRange(4_000, 5_000))
    spans = [_span(settings, item) for item in ranges]
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.preflight_whisper_runtime",
        lambda _settings: capability,
    )
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.prepare_audio_span",
        lambda _video_id, item, _settings, **kwargs: spans[0] if item.start_ms == 1_000 else spans[1],
    )
    calls = 0

    def one_failure(argv, *, timeout_sec):
        nonlocal calls
        calls += 1
        if calls == 2:
            return WhisperProcessResult(tuple(argv), "", "失敗", 2, False, 2)
        output_prefix = Path(argv[argv.index("--output-file") + 1])
        output_prefix.with_suffix(".json").write_text(json.dumps(_full_payload()), encoding="utf-8")
        return WhisperProcessResult(tuple(argv), "", "", 0, False, 2)

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._process_whisper", one_failure)
    result = run_selected_ranges(VIDEO_ID, ranges, settings, job_id="partial")

    assert result.status == "partial"
    assert result.is_high_precision is False
    assert result.artifact is not None
    assert result.artifact.status is TranscriptArtifactStatus.PARTIAL
    assert result.range_results[1].status is WhisperRangeStatus.FAILED
    assert result.range_results[1].retryable is True
    assert "Whisper" in (result.range_results[1].diagnostic or "")


def test_malformed_output_keeps_typed_process_result(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    capability = _fake_capability(settings)
    item = AudioSpanRange(1_000, 2_000)
    span = _span(settings, item)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.preflight_whisper_runtime",
        lambda _settings: capability,
    )
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.prepare_audio_span",
        lambda *_args, **_kwargs: span,
    )

    def malformed(argv, *, timeout_sec):
        return WhisperProcessResult(tuple(argv), '{"transcription":', "", 0, False, 1)

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._process_whisper", malformed)
    result = run_selected_ranges(VIDEO_ID, [item], settings, job_id="malformed")

    assert result.status == "failed"
    assert result.artifact is not None
    assert result.artifact.status is TranscriptArtifactStatus.FAILED
    assert result.range_results[0].process is not None
    assert result.range_results[0].process.exit_code == 0
    assert "壊れている" in (result.range_results[0].diagnostic or "")


def test_timeout_is_retryable(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    capability = _fake_capability(settings)
    item = AudioSpanRange(1_000, 2_000)
    span = _span(settings, item)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.preflight_whisper_runtime",
        lambda _settings: capability,
    )
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.prepare_audio_span",
        lambda *_args, **_kwargs: span,
    )

    def timed_out(argv, *, timeout_sec):
        return WhisperProcessResult(tuple(argv), "", "タイムアウト", None, True, timeout_sec)

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._process_whisper", timed_out)
    result = run_selected_ranges(VIDEO_ID, [item], settings, job_id="timeout")

    assert result.status == "failed"
    assert result.is_high_precision is False
    assert result.artifact is not None
    assert result.artifact.status is TranscriptArtifactStatus.FAILED
    assert result.range_results[0].status is WhisperRangeStatus.FAILED
    assert result.range_results[0].retryable is True
    assert result.range_results[0].process is not None
    assert result.range_results[0].process.timed_out is True
    assert "タイムアウト" in (result.range_results[0].diagnostic or "")


def test_job_gate_rejects_concurrent_run(tmp_path):
    settings = _fake_settings(tmp_path)
    with _job_gate(settings):
        with pytest.raises(WhisperBusyError, match="同時"):
            with _job_gate(settings):
                pass
