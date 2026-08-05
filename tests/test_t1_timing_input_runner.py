from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from benchmarks.t1.annotation_packet import AnnotationError
from benchmarks.t1.timing_input_runner import (
    EVIDENCE_SCHEMA,
    EXPECTED_MANIFEST_FINGERPRINT,
    TimingInputRunnerError,
    build_whisper_argv,
    check_whisper_output_schema,
    parse_peak_memory_bytes,
    run_timing_inputs,
    wrap_argv_for_memory_measurement,
)


def _write_file(path: Path, content: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return len(content), sha256(content).hexdigest()


def _minimal_runtime_settings() -> dict[str, Any]:
    return {
        "language": "ja",
        "initial_prompt": "テスト用プロンプト",
        "padding_ms": 0,
        "decode": {
            "temperature": 0.0,
            "beam_size": 5,
            "best_of": 5,
            "threads": 8,
            "processors": 1,
            "no_fallback": False,
            "vad": False,
        },
        "output_schema": "whisper-cli-json-full-v1",
    }


def _make_manifest(
    tmp_path: Path,
    *,
    selected_source_ids: list[str] | None = None,
    max_whisper_invocations: int = 8,
    fingerprint: str = EXPECTED_MANIFEST_FINGERPRINT,
    bad_wav_hash: str | None = None,
) -> dict[str, Any]:
    binary_path = tmp_path / "whisper-cli"
    model_path = tmp_path / "model.bin"
    ffmpeg_path = tmp_path / "ffmpeg"
    binary_bytes, binary_sha = _write_file(binary_path, b"whisper-binary")
    model_bytes, model_sha = _write_file(model_path, b"model-data")
    ffmpeg_bytes, ffmpeg_sha = _write_file(ffmpeg_path, b"ffmpeg-binary")

    if selected_source_ids is None:
        selected_source_ids = [f"source-{index}-audio" for index in range(1, 9)]

    sources: dict[str, Any] = {}
    for source_id in selected_source_ids:
        wav_path = tmp_path / f"{source_id}.wav"
        wav_bytes, wav_sha = _write_file(wav_path, f"wav-{source_id}".encode())
        if bad_wav_hash == source_id:
            wav_sha = "0" * 64
        sources[source_id.replace("-audio", "")] = {
            "source_id": source_id,
            "source_content_kind": "wav_cache",
            "source_content_path": str(wav_path),
            "source_content_bytes": wav_bytes,
            "source_content_sha256": wav_sha,
        }

    return {
        "manifest_fingerprint": fingerprint,
        "limits": {
            "max_whisper_invocations": max_whisper_invocations,
        },
        "timing_inputs": {
            "selected_source_ids": selected_source_ids,
        },
        "runtime": {
            "binary": {
                "path": str(binary_path),
                "bytes": binary_bytes,
                "sha256": binary_sha,
            },
            "model": {
                "path": str(model_path),
                "bytes": model_bytes,
                "sha256": model_sha,
            },
            "ffmpeg": {
                "path": str(ffmpeg_path),
                "bytes": ffmpeg_bytes,
                "sha256": ffmpeg_sha,
            },
            "settings": _minimal_runtime_settings(),
        },
        "sources": sources,
    }


def _evidence_required_fields() -> set[str]:
    return {
        "schema",
        "mode",
        "status",
        "manifest_fingerprint",
        "manifest_fingerprint_match",
        "max_whisper_invocations",
        "planned_invocation_count",
        "invocation_count",
        "runtime",
        "sources",
        "error",
    }


def _source_required_fields() -> set[str]:
    return {
        "source_id",
        "source_wav_path",
        "source_wav_bytes",
        "source_wav_sha256",
        "manifest_bytes_match",
        "manifest_sha256_match",
        "verified",
        "command",
        "executed_at",
        "wall_time_ms",
        "exit_code",
        "output_json_bytes",
        "output_json_sha256",
        "output_schema_check",
    }


def test_hash_mismatch_fail_closed_without_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(argv)
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr("benchmarks.t1.timing_input_runner.subprocess.run", fake_run)
    manifest = _make_manifest(tmp_path, bad_wav_hash="source-1-audio")
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "timing-out"

    with pytest.raises(TimingInputRunnerError, match="hash 照合"):
        run_timing_inputs(
            manifest,
            output_dir=output_dir,
            evidence_path=evidence_path,
            execute=True,
        )

    assert calls == []
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["invocation_count"] == 0
    assert evidence["status"] == "failed"


def test_ninth_invocation_fail_closed(tmp_path: Path) -> None:
    manifest = _make_manifest(
        tmp_path,
        selected_source_ids=[f"source-{index}-audio" for index in range(1, 10)],
        max_whisper_invocations=8,
    )
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "timing-out"

    with pytest.raises(TimingInputRunnerError, match="exceeds"):
        run_timing_inputs(
            manifest,
            output_dir=output_dir,
            evidence_path=evidence_path,
            execute=True,
        )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["planned_invocation_count"] == 9
    assert evidence["invocation_count"] == 0


def test_preflight_does_not_call_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(argv)
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr("benchmarks.t1.timing_input_runner.subprocess.run", fake_run)
    manifest = _make_manifest(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "timing-out"

    evidence = run_timing_inputs(
        manifest,
        output_dir=output_dir,
        evidence_path=evidence_path,
        execute=False,
    )

    assert calls == []
    assert evidence["mode"] == "preflight"
    assert evidence["status"] == "preflight_ok"
    assert evidence["invocation_count"] == 0
    assert evidence["planned_invocation_count"] == 8
    assert all(source["command"] for source in evidence["sources"])
    assert all(source["executed_at"] is None for source in evidence["sources"])


def test_evidence_schema_fields(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "timing-out"

    evidence = run_timing_inputs(
        manifest,
        output_dir=output_dir,
        evidence_path=evidence_path,
        execute=False,
    )

    assert evidence["schema"] == EVIDENCE_SCHEMA
    assert _evidence_required_fields() <= set(evidence)
    assert evidence["manifest_fingerprint_match"] is True
    runtime = evidence["runtime"]
    assert {"binary", "model", "ffmpeg", "settings", "verified"} <= set(runtime)
    for component in ("binary", "model", "ffmpeg"):
        assert {"path", "bytes", "sha256", "verified"} <= set(runtime[component])
    assert len(evidence["sources"]) == 8
    for source in evidence["sources"]:
        assert _source_required_fields() <= set(source)


def test_production_output_dir_rejected(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    repo_root = Path(__file__).resolve().parents[1]
    forbidden_output = repo_root / "data" / "timing-out"

    with pytest.raises(AnnotationError, match="packet の出力先"):
        run_timing_inputs(
            manifest,
            output_dir=forbidden_output,
            evidence_path=evidence_path,
            execute=False,
        )


def test_execute_records_whisper_output_with_mock(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path, selected_source_ids=["source-1-audio"])
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "timing-out"

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        output_prefix = Path(argv[argv.index("--output-file") + 1])
        output_path = output_prefix.with_suffix(".json")
        payload = {
            "systeminfo": "test",
            "model": {},
            "params": {},
            "result": {},
            "transcription": [
                {
                    "text": "hello",
                    "tokens": [{"text": "hel", "timestamps": {"from": "0.0", "to": "0.1"}}],
                }
            ],
        }
        output_path.write_text(json.dumps(payload), encoding="utf-8")

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    evidence = run_timing_inputs(
        manifest,
        output_dir=output_dir,
        evidence_path=evidence_path,
        execute=True,
        subprocess_run=fake_run,
    )

    assert evidence["invocation_count"] == 1
    assert evidence["status"] == "ok"
    source = evidence["sources"][0]
    assert source["exit_code"] == 0
    assert source["output_json_bytes"] is not None
    assert source["output_json_sha256"] is not None
    assert source["output_schema_check"]["has_token_timing_arrays"] is True


def test_build_whisper_argv_uses_full_json_flags(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    audio = tmp_path / "audio.wav"
    binary.write_bytes(b"x")
    model.write_bytes(b"y")
    audio.write_bytes(b"z")
    output_json = tmp_path / "out.json"
    argv = build_whisper_argv(
        binary_path=binary,
        model_path=model,
        audio_path=audio,
        output_json_path=output_json,
        settings=_minimal_runtime_settings(),
    )
    assert "--output-json" in argv
    assert "--output-json-full" in argv
    assert argv.index("--output-json-full") == argv.index("--output-json") + 1


def test_check_whisper_output_schema_requires_token_timing() -> None:
    valid = check_whisper_output_schema(
        {
            "systeminfo": "x",
            "model": {},
            "params": {},
            "result": {},
            "transcription": [
                {"text": "a", "tokens": [{"text": "a", "offsets": {"from": 0, "to": 10}}]}
            ],
        }
    )
    invalid = check_whisper_output_schema(
        {
            "systeminfo": "x",
            "model": {},
            "params": {},
            "result": {},
            "transcription": [{"text": "a"}],
        }
    )
    assert valid["valid"] is True
    assert invalid["valid"] is False
    assert invalid["has_token_timing_arrays"] is False


def test_parse_peak_memory_bytes_from_time_l_stderr() -> None:
    stderr = (
        "        1.23 real         0.45 user         0.12 sys\n"
        "          987654321  maximum resident set size\n"
    )
    assert parse_peak_memory_bytes(stderr) == 987654321
    assert parse_peak_memory_bytes("no memory line here") is None


def test_wrap_argv_for_memory_measurement() -> None:
    argv = ["/bin/whisper-cli", "--file", "audio.wav"]
    assert wrap_argv_for_memory_measurement(argv) == ["/usr/bin/time", "-l", *argv]


def test_execute_with_measure_memory_records_peak_rss(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path, selected_source_ids=["source-1-audio"])
    evidence_path = tmp_path / "evidence.json"
    output_dir = tmp_path / "timing-out"

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        assert argv[0] == "/usr/bin/time"
        assert argv[1] == "-l"
        output_prefix = Path(argv[argv.index("--output-file") + 1])
        output_path = output_prefix.with_suffix(".json")
        payload = {
            "systeminfo": "test",
            "model": {},
            "params": {},
            "result": {},
            "transcription": [
                {
                    "text": "hello",
                    "tokens": [{"text": "hel", "offsets": {"from": 0, "to": 10}}],
                }
            ],
        }
        output_path.write_text(json.dumps(payload), encoding="utf-8")

        class Completed:
            returncode = 0
            stdout = ""
            stderr = "          123456789  maximum resident set size\n"

        return Completed()

    evidence = run_timing_inputs(
        manifest,
        output_dir=output_dir,
        evidence_path=evidence_path,
        execute=True,
        measure_memory=True,
        subprocess_run=fake_run,
    )

    source = evidence["sources"][0]
    assert evidence["measure_memory"] is True
    assert source["wrapped_command"].startswith("/usr/bin/time -l ")
    assert source["peak_memory_bytes"] == 123456789
