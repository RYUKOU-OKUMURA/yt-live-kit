"""S9-3 whisper runtime の mock / fixture 中心テスト."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from yt_live_kit.config import WHISPER_ADOPTED_CONTRACT, Settings
from yt_live_kit.models.transcript import TranscriptArtifactStatus
from yt_live_kit.services.ytdlp import AudioSpanError, AudioSpanRange, AudioSpanResult
from yt_live_kit.services.whisper_runtime import (
    WhisperCapability,
    WhisperBusyError,
    WhisperOutputError,
    WhisperPreflightError,
    WhisperProcessResult,
    WhisperRangeStatus,
    WhisperSettingsContract,
    _preflight_whisper_runtime,
    _job_gate,
    build_whisper_argv,
    parse_whisper_full_json,
    preflight_whisper_runtime,
    run_selected_ranges,
)


VIDEO_ID = "IJvd6k6ZmUo"


def _full_payload(*, text: str = "こんにちは", start_ms: int = 0, end_ms: int = 500) -> dict:
    def timestamp(value: int) -> str:
        hours, remainder = divmod(value, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    return {
        "systeminfo": "whisper.cpp test fixture",
        "model": {
            "type": "large",
            "multilingual": True,
            "vocab": 51_866,
            "audio": {"ctx": 1_500, "state": 1_280, "head": 20, "layer": 32},
            "text": {"ctx": 448, "state": 1_280, "head": 20, "layer": 4},
            "mels": 128,
            "ftype": 8,
        },
        "params": {"model": "fixture-model.bin", "language": "ja", "translate": False},
        "result": {"language": "ja"},
        "transcription": [
            {
                "timestamps": {"from": timestamp(start_ms), "to": timestamp(end_ms)},
                "offsets": {"from": start_ms, "to": end_ms},
                "text": text,
                "tokens": [
                    {
                        "text": text,
                        "timestamps": {"from": timestamp(start_ms), "to": timestamp(end_ms)},
                        "offsets": {"from": start_ms, "to": end_ms},
                        "id": 1,
                        "p": 1.0,
                        "t_dtw": -1,
                    }
                ],
            }
        ],
    }


def _fake_settings(tmp_path: Path) -> Settings:
    binary = tmp_path / "whisper-cli"
    binary.write_bytes(b"whisper-binary-fixture")
    binary.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"whisper-model-fixture")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"ffmpeg-fixture")
    ffmpeg.chmod(0o755)
    return Settings(
        data_dir=tmp_path / "data",
        ytdlp_path="yt-dlp-fixture",
        whisper_binary_path=str(binary),
        whisper_model_path=str(model),
        ffmpeg_path=str(ffmpeg),
    )


def _fake_contract(settings: Settings):
    binary_sha = hashlib.sha256(Path(settings.whisper_binary_path).read_bytes()).hexdigest()
    model_sha = hashlib.sha256(Path(settings.whisper_model_path).read_bytes()).hexdigest()
    return replace(
        WHISPER_ADOPTED_CONTRACT,
        binary_sha256=binary_sha,
        model_sha256=model_sha,
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
        model_name=WHISPER_ADOPTED_CONTRACT.model_name,
        model_path=str(model),
        model_bytes=model.stat().st_size,
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        ffmpeg_path=settings.ffmpeg_path,
        ffmpeg_version="fixture",
        ffmpeg_capabilities=("audio_conversion", "aresample", "pcm_s16le"),
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
            "selector": "bestaudio",
        },
        cache_hit=cache_hit,
        request_fingerprint=hashlib.sha256(content + b"request").hexdigest(),
    )


def test_preflight_rejects_wrong_binary_hash(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    contract = replace(_fake_contract(settings), binary_sha256="0" * 64)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.shutil.which",
        lambda value: value,
    )

    with pytest.raises(WhisperPreflightError, match="SHA-256"):
        _preflight_whisper_runtime(settings, adopted_contract=contract)


def test_settings_env_cannot_redefine_adopted_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("YTLK_WHISPER_BINARY_PATH", str(tmp_path / "custom-whisper"))
    monkeypatch.setenv("YTLK_WHISPER_BINARY_SHA256", "0" * 64)
    monkeypatch.setenv("YTLK_WHISPER_BINARY_VERSION", "9.9.9")
    monkeypatch.setenv("YTLK_WHISPER_MODEL_NAME", "other-model")
    monkeypatch.setenv("YTLK_WHISPER_MODEL_SHA256", "0" * 64)
    monkeypatch.setenv("YTLK_WHISPER_OUTPUT_SCHEMA", "other-schema")
    monkeypatch.setenv("YTLK_WHISPER_LANGUAGE", "en")
    monkeypatch.setenv("YTLK_WHISPER_THREADS", "1")
    monkeypatch.setenv("YTLK_WHISPER_PROCESSORS", "2")
    monkeypatch.setenv("YTLK_WHISPER_BEAM_SIZE", "1")
    monkeypatch.setenv("YTLK_WHISPER_BEST_OF", "1")
    monkeypatch.setenv("YTLK_WHISPER_TEMPERATURE", "1")
    monkeypatch.setenv("YTLK_WHISPER_NO_FALLBACK", "true")
    monkeypatch.setenv("YTLK_WHISPER_VAD", "true")
    monkeypatch.setenv("YTLK_WHISPER_PADDING_MS", "500")
    monkeypatch.setenv("YTLK_WHISPER_INITIAL_PROMPT", "別の prompt")
    monkeypatch.setenv("YTLK_WHISPER_TIMEOUT", "1")
    settings = Settings(data_dir=tmp_path)
    contract = WhisperSettingsContract.from_settings(settings)

    assert settings.whisper_binary_path == str(tmp_path / "custom-whisper")
    assert not hasattr(settings, "whisper_binary_sha256")
    assert WHISPER_ADOPTED_CONTRACT.binary_version == "1.9.1"
    assert contract.output_schema == "whisper-cli-json-full-v1"
    assert contract.language == "ja"
    assert contract.threads == 8
    assert contract.processors == 1
    assert contract.beam_size == 5
    assert contract.best_of == 5
    assert contract.temperature == 0.0
    assert contract.no_fallback is False
    assert contract.vad is False
    assert contract.padding_ms == 0
    assert contract.initial_prompt == WHISPER_ADOPTED_CONTRACT.initial_prompt
    assert contract.timeout_sec == 180
    argv = build_whisper_argv(
        binary_path=settings.whisper_binary_path,
        model_path=settings.whisper_model_path,
        audio_path="/tmp/span.wav",
        output_json_path="/tmp/result.json",
        settings=settings,
        selected_range=AudioSpanRange(0, 1_000),
    )
    assert argv[argv.index("-t") + 1] == "8"
    assert argv[argv.index("--prompt") + 1] == WHISPER_ADOPTED_CONTRACT.initial_prompt


def test_public_preflight_always_uses_adopted_contract(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    seen = {}

    def fake_preflight(settings_arg, *, adopted_contract):
        seen["settings"] = settings_arg
        seen["contract"] = adopted_contract
        return _fake_capability(settings_arg)

    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime._preflight_whisper_runtime",
        fake_preflight,
    )
    capability = preflight_whisper_runtime(settings)
    assert capability.version == "1.9.1"
    assert seen["settings"] is settings
    assert seen["contract"] is WHISPER_ADOPTED_CONTRACT


@pytest.mark.parametrize(
    "field,value",
    (
        ("binary_version", "9.9.9"),
        ("model_name", "other-model"),
        ("initial_prompt", "別の prompt"),
        ("threads", 1),
        ("timeout_sec", 1),
    ),
)
def test_internal_contract_injection_rejects_immutable_contract_changes(
    monkeypatch,
    tmp_path,
    field,
    value,
):
    settings = _fake_settings(tmp_path)
    _patch_successful_preflight(monkeypatch)
    contract = replace(_fake_contract(settings), **{field: value})
    with pytest.raises(WhisperPreflightError, match="contract"):
        _preflight_whisper_runtime(settings, adopted_contract=contract)


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
    capability = _preflight_whisper_runtime(settings, adopted_contract=_fake_contract(settings))
    assert capability.version == "1.9.1"
    assert capability.model_sha256 == _fake_contract(settings).model_sha256
    assert capability.json_timestamp_capability is True
    assert "audio_conversion" in capability.ffmpeg_capabilities

    def wrong_version(argv, *, timeout, label):
        if "--version" in argv:
            return __import__("subprocess").CompletedProcess(argv, 0, "whisper.cpp version: 1.8.0", "")
        return __import__("subprocess").CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.whisper_runtime._run_inspection", wrong_version)
    with pytest.raises(WhisperPreflightError, match="version"):
        _preflight_whisper_runtime(settings, adopted_contract=_fake_contract(settings))


@pytest.mark.parametrize("wrong_path", (True, False))
def test_preflight_rejects_missing_or_wrong_model(monkeypatch, tmp_path, wrong_path):
    settings = _fake_settings(tmp_path)
    contract = _fake_contract(settings)
    if wrong_path:
        settings = settings.model_copy(update={"whisper_model_path": "missing-model.bin"})
    else:
        contract = replace(contract, model_sha256="0" * 64)
    _patch_successful_preflight(monkeypatch)
    with pytest.raises(WhisperPreflightError, match="model"):
        _preflight_whisper_runtime(settings, adopted_contract=contract)


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
        _preflight_whisper_runtime(settings, adopted_contract=_fake_contract(settings))

    _patch_successful_preflight(monkeypatch)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.shutil.which",
        lambda value: None if value == settings.ffmpeg_path else value,
    )
    with pytest.raises(WhisperPreflightError, match="FFmpeg"):
        _preflight_whisper_runtime(settings, adopted_contract=_fake_contract(settings))


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


@pytest.mark.parametrize(
    "token_text",
    (
        pytest.param(" ", id="single-space-measured-shape"),
        pytest.param("   ", id="multiple-spaces"),
        pytest.param("", id="empty"),
    ),
)
def test_strict_full_json_accepts_empty_or_whitespace_token_text(token_text):
    payload = _full_payload(text="こんにちは")
    token = payload["transcription"][0]["tokens"][0]
    token["text"] = token_text
    # This is the token shape observed in whisper.cpp 1.9.1 full JSON: id 220,
    # a single-space metadata token, and a zero-length 2990 ms timestamp.
    token["id"] = 220
    token["timestamps"] = {"from": "00:00:02,990", "to": "00:00:02,990"}
    token["offsets"] = {"from": 2990, "to": 2990}

    cues = parse_whisper_full_json(payload)

    assert len(cues) == 1
    assert cues[0].text == "こんにちは"
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 500)


@pytest.mark.parametrize("token_text", ("\x00", None, 1, True, b" "))
def test_strict_full_json_rejects_invalid_token_text_types_and_nul(token_text):
    payload = _full_payload()
    payload["transcription"][0]["tokens"][0]["text"] = token_text

    with pytest.raises(WhisperOutputError, match="token.text"):
        parse_whisper_full_json(payload)


def test_strict_full_json_rejects_whitespace_segment_text():
    payload = _full_payload(text=" \t\n")

    with pytest.raises(WhisperOutputError, match="segment"):
        parse_whisper_full_json(payload)


@pytest.mark.parametrize(
    "mutate,match",
    (
        (lambda payload: payload["model"]["audio"].update({"unknown": 1}), "schema"),
        (lambda payload: payload["params"].pop("translate"), "schema"),
        (lambda payload: payload["transcription"][0]["offsets"].update({"unknown": 1}), "schema"),
        (lambda payload: payload["transcription"][0]["tokens"][0].__setitem__("id", "1"), "整数"),
        (lambda payload: payload["transcription"][0].pop("tokens"), "schema"),
        (lambda payload: payload["result"].__setitem__("language", 1), "文字列"),
    ),
)
def test_strict_full_json_rejects_unknown_nested_fields_wrong_types_and_missing_fields(
    mutate,
    match,
):
    payload = _full_payload()
    mutate(payload)
    with pytest.raises(WhisperOutputError, match=match):
        parse_whisper_full_json(payload)


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


def test_audio_only_failure_returns_explicit_vtt_fallback(monkeypatch, tmp_path):
    settings = _fake_settings(tmp_path)
    capability = _fake_capability(settings)
    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.preflight_whisper_runtime",
        lambda _settings: capability,
    )
    def fail_audio(*_args, **_kwargs):
        raise AudioSpanError("audio-only format がありません。")

    monkeypatch.setattr(
        "yt_live_kit.services.whisper_runtime.prepare_audio_span",
        fail_audio,
    )

    result = run_selected_ranges(
        VIDEO_ID,
        [AudioSpanRange(1_000, 2_000)],
        settings,
        job_id="audio-only-fallback",
    )

    assert result.status == "failed"
    assert result.is_high_precision is False
    assert result.fallback_available is True
    assert result.fallback_kind == "youtube_vtt"
    assert "既存 YouTube VTT" in (result.diagnostic or "")
    assert "audio-only" in (result.range_results[0].diagnostic or "")


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
