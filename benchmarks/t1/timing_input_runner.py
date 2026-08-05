"""T1-1 bounded whisper-cli timing input runner.

固定 manifest の 8 selected span に対し、hash 照合後に isolated temp へ
whisper-cli を最大 8 回だけ実行し、証跡 JSON を記録する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.t1.annotation_packet import (
    AnnotationError,
    _assert_isolated_packet_path,
    load_manifest,
    sha256_file,
)

EVIDENCE_SCHEMA = "t1-1-timing-inputs-evidence-v1"
EXPECTED_MANIFEST_FINGERPRINT = (
    "b4f33f33c6b7f23be0d28c13bdb0bdde946659a7a8f9909894e8b0d4807a11ec"
)
FULL_OUTPUT_SCHEMA = "whisper-cli-json-full-v1"
TIME_BINARY = "/usr/bin/time"
PEAK_MEMORY_PATTERN = "maximum resident set size"


class TimingInputRunnerError(ValueError):
    """timing input runner の契約違反。"""


def _write_evidence_atomic(path: Path, value: Mapping[str, Any]) -> None:
    """docs/benchmarks 向け証跡 JSON を atomic に保存する。"""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _format_number(value: int | float) -> str:
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _source_by_id(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    sources = manifest.get("sources")
    if not isinstance(sources, Mapping):
        raise TimingInputRunnerError("manifest.sources がありません。")
    result: dict[str, Mapping[str, Any]] = {}
    for value in sources.values():
        if not isinstance(value, Mapping) or not isinstance(value.get("source_id"), str):
            raise TimingInputRunnerError("manifest.sources の source_id が不正です。")
        source_id = value["source_id"]
        if source_id in result:
            raise TimingInputRunnerError(f"source_id が重複しています: {source_id}")
        result[source_id] = value
    return result


def _verify_file_identity(
    path_text: Any,
    expected_bytes: Any,
    expected_sha256: Any,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(path_text, str) or not path_text.startswith("/"):
        return {
            "label": label,
            "path": str(path_text),
            "exists": False,
            "bytes": None,
            "sha256": None,
            "expected_bytes": expected_bytes,
            "expected_sha256": expected_sha256,
            "bytes_match": False,
            "sha256_match": False,
            "verified": False,
            "error": f"{label} の path は絶対パスが必要です。",
        }
    path = Path(path_text)
    if not isinstance(expected_bytes, int) or not isinstance(expected_sha256, str):
        return {
            "label": label,
            "path": str(path),
            "exists": path.is_file(),
            "bytes": None,
            "sha256": None,
            "expected_bytes": expected_bytes,
            "expected_sha256": expected_sha256,
            "bytes_match": False,
            "sha256_match": False,
            "verified": False,
            "error": f"{label} の bytes / SHA-256 が不正です。",
        }
    if not path.is_file():
        return {
            "label": label,
            "path": str(path),
            "exists": False,
            "bytes": None,
            "sha256": None,
            "expected_bytes": expected_bytes,
            "expected_sha256": expected_sha256,
            "bytes_match": False,
            "sha256_match": False,
            "verified": False,
            "error": f"{label} が存在しません: {path}",
        }
    actual_bytes = path.stat().st_size
    try:
        actual_sha256 = sha256_file(path)
    except AnnotationError as exc:
        return {
            "label": label,
            "path": str(path),
            "exists": True,
            "bytes": actual_bytes,
            "sha256": None,
            "expected_bytes": expected_bytes,
            "expected_sha256": expected_sha256,
            "bytes_match": actual_bytes == expected_bytes,
            "sha256_match": False,
            "verified": False,
            "error": str(exc),
        }
    bytes_match = actual_bytes == expected_bytes
    sha256_match = actual_sha256 == expected_sha256
    return {
        "label": label,
        "path": str(path),
        "exists": True,
        "bytes": actual_bytes,
        "sha256": actual_sha256,
        "expected_bytes": expected_bytes,
        "expected_sha256": expected_sha256,
        "bytes_match": bytes_match,
        "sha256_match": sha256_match,
        "verified": bytes_match and sha256_match,
        "error": None
        if bytes_match and sha256_match
        else f"{label} の bytes / SHA-256 が manifest と一致しません: {path}",
    }


def build_whisper_argv(
    *,
    binary_path: str | Path,
    model_path: str | Path,
    audio_path: str | Path,
    output_json_path: str | Path,
    settings: Mapping[str, Any],
) -> list[str]:
    """manifest runtime.settings から whisper-cli 1.9.1 固定 argv を組み立てる。"""

    if settings.get("language") != "ja":
        raise TimingInputRunnerError("runtime.settings.language は ja 固定です。")
    initial_prompt = settings.get("initial_prompt", "")
    if not isinstance(initial_prompt, str):
        raise TimingInputRunnerError("runtime.settings.initial_prompt が不正です。")
    decode = settings.get("decode")
    if not isinstance(decode, Mapping):
        raise TimingInputRunnerError("runtime.settings.decode が不正です。")
    output_schema = settings.get("output_schema", FULL_OUTPUT_SCHEMA)
    output_path = Path(output_json_path)
    output_prefix = output_path.with_suffix("")
    argv = [
        str(binary_path),
        "--model",
        str(model_path),
        "--file",
        str(audio_path),
        "--language",
        "ja",
        "--prompt",
        initial_prompt,
        "--output-json",
        "--output-json-full",
        "--output-file",
        str(output_prefix),
        "--no-prints",
        "-p",
        "1",
    ]
    if output_schema != FULL_OUTPUT_SCHEMA:
        raise TimingInputRunnerError(
            f"runtime.settings.output_schema は {FULL_OUTPUT_SCHEMA} 固定です。"
        )
    decode_flags = {
        "temperature": "--temperature",
        "temperature_inc": "--temperature-inc",
        "beam_size": "--beam-size",
        "best_of": "--best-of",
        "threads": "--threads",
        "max_context": "--max-context",
        "max_len": "--max-len",
        "no_speech_threshold": "--no-speech-thold",
    }
    for key in sorted(decode):
        value = decode[key]
        if key == "processors":
            if value != 1:
                raise TimingInputRunnerError("runtime.settings.decode.processors は 1 固定です。")
            continue
        if key == "no_fallback":
            if value:
                argv.append("--no-fallback")
            continue
        if key == "vad":
            if value:
                raise TimingInputRunnerError("whisper-cli 1.9.1 では VAD 有効化を検証していません。")
            continue
        if key not in decode_flags:
            raise TimingInputRunnerError(f"未知の decode 設定です: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise TimingInputRunnerError(f"decode.{key} の値が不正です。")
        argv.extend([decode_flags[key], _format_number(value)])
    return argv


def parse_peak_memory_bytes(stderr: str) -> int | None:
    """macOS /usr/bin/time -l の stderr から peak RSS bytes を抽出する。"""

    for line in stderr.splitlines():
        stripped = line.strip()
        if PEAK_MEMORY_PATTERN not in stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if value >= 0:
            return value
    return None


def wrap_argv_for_memory_measurement(argv: Sequence[str]) -> list[str]:
    """whisper argv を /usr/bin/time -l でラップする。"""

    return [TIME_BINARY, "-l", *argv]


def check_whisper_output_schema(payload: Any) -> dict[str, Any]:
    """full JSON の root schema と token timing 配列の有無を確認する。"""

    result: dict[str, Any] = {
        "output_schema": FULL_OUTPUT_SCHEMA,
        "valid": False,
        "has_required_root_fields": False,
        "has_transcription": False,
        "has_token_timing_arrays": False,
        "segment_count": 0,
        "segments_with_tokens": 0,
        "errors": [],
    }
    if not isinstance(payload, Mapping):
        result["errors"].append("output は JSON object ではありません。")
        return result
    required_root = {"systeminfo", "model", "params", "result", "transcription"}
    missing = sorted(required_root - set(payload))
    result["has_required_root_fields"] = not missing
    if missing:
        result["errors"].append(f"必須 root field が不足: {missing}")
    transcription = payload.get("transcription")
    if not isinstance(transcription, list):
        result["errors"].append("transcription が list ではありません。")
        return result
    result["has_transcription"] = bool(transcription)
    result["segment_count"] = len(transcription)
    segments_with_tokens = 0
    for index, segment in enumerate(transcription):
        if not isinstance(segment, Mapping):
            result["errors"].append(f"transcription[{index}] が object ではありません。")
            continue
        tokens = segment.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            continue
        has_timing = False
        for token_index, token in enumerate(tokens):
            if not isinstance(token, Mapping):
                result["errors"].append(
                    f"transcription[{index}].tokens[{token_index}] が object ではありません。"
                )
                continue
            if "timestamps" in token or "offsets" in token:
                has_timing = True
        if has_timing:
            segments_with_tokens += 1
    result["segments_with_tokens"] = segments_with_tokens
    result["has_token_timing_arrays"] = segments_with_tokens > 0
    result["valid"] = (
        result["has_required_root_fields"]
        and result["has_transcription"]
        and result["has_token_timing_arrays"]
        and not result["errors"]
    )
    return result


def _assert_output_dir_isolated(output_dir: Path) -> Path:
    resolved = output_dir.expanduser().resolve()
    _assert_isolated_packet_path(resolved / "probe.json")
    return resolved


def _selected_timing_sources(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    timing_inputs = manifest.get("timing_inputs")
    if not isinstance(timing_inputs, Mapping):
        raise TimingInputRunnerError("timing_inputs がありません。")
    selected_ids = timing_inputs.get("selected_source_ids")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise TimingInputRunnerError("timing_inputs.selected_source_ids が不正です。")
    sources = _source_by_id(manifest)
    selected: list[Mapping[str, Any]] = []
    for source_id in selected_ids:
        if not isinstance(source_id, str):
            raise TimingInputRunnerError("selected_source_ids の要素が不正です。")
        source = sources.get(source_id)
        if source is None:
            raise TimingInputRunnerError(f"未知の source_id です: {source_id}")
        if source.get("source_content_kind") != "wav_cache":
            raise TimingInputRunnerError(f"{source_id} は wav_cache source ではありません。")
        selected.append(source)
    return selected


def _runtime_verification(manifest: Mapping[str, Any]) -> dict[str, Any]:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise TimingInputRunnerError("runtime がありません。")
    binary = runtime.get("binary")
    model = runtime.get("model")
    ffmpeg = runtime.get("ffmpeg")
    settings = runtime.get("settings")
    if not isinstance(binary, Mapping) or not isinstance(model, Mapping) or not isinstance(ffmpeg, Mapping):
        raise TimingInputRunnerError("runtime.binary / model / ffmpeg が不正です。")
    if not isinstance(settings, Mapping):
        raise TimingInputRunnerError("runtime.settings がありません。")
    binary_info = _verify_file_identity(
        binary.get("path"), binary.get("bytes"), binary.get("sha256"), label="runtime binary"
    )
    model_info = _verify_file_identity(
        model.get("path"), model.get("bytes"), model.get("sha256"), label="runtime model"
    )
    ffmpeg_info = _verify_file_identity(
        ffmpeg.get("path"), ffmpeg.get("bytes"), ffmpeg.get("sha256"), label="ffmpeg"
    )
    return {
        "binary": binary_info,
        "model": model_info,
        "ffmpeg": ffmpeg_info,
        "settings": json.loads(json.dumps(settings, ensure_ascii=False)),
        "verified": binary_info["verified"] and model_info["verified"] and ffmpeg_info["verified"],
    }


def _source_wav_verification(source: Mapping[str, Any]) -> dict[str, Any]:
    info = _verify_file_identity(
        source.get("source_content_path"),
        source.get("source_content_bytes"),
        source.get("source_content_sha256"),
        label=f"source {source.get('source_id')}",
    )
    return {
        "source_id": source.get("source_id"),
        "source_wav_path": info["path"],
        "source_wav_bytes": info["bytes"],
        "source_wav_sha256": info["sha256"],
        "manifest_bytes": source.get("source_content_bytes"),
        "manifest_sha256": source.get("source_content_sha256"),
        "manifest_bytes_match": info["bytes_match"],
        "manifest_sha256_match": info["sha256_match"],
        "verified": info["verified"],
        "error": info["error"],
    }


def run_timing_inputs(
    manifest: Mapping[str, Any],
    *,
    output_dir: Path,
    evidence_path: Path,
    execute: bool = False,
    measure_memory: bool = False,
    subprocess_run: Any = subprocess.run,
) -> dict[str, Any]:
    """preflight または bounded whisper-cli 実行を行い、証跡 JSON を保存する。"""

    isolated_output_dir = _assert_output_dir_isolated(output_dir)
    isolated_output_dir.mkdir(parents=True, exist_ok=True)

    manifest_fingerprint = manifest.get("manifest_fingerprint")
    fingerprint_match = manifest_fingerprint == EXPECTED_MANIFEST_FINGERPRINT
    limits = manifest.get("limits")
    if not isinstance(limits, Mapping):
        raise TimingInputRunnerError("limits がありません。")
    max_invocations = limits.get("max_whisper_invocations")
    if not isinstance(max_invocations, int) or max_invocations < 1:
        raise TimingInputRunnerError("limits.max_whisper_invocations が不正です。")

    selected_sources = _selected_timing_sources(manifest)
    planned_invocations = len(selected_sources)
    if planned_invocations > max_invocations:
        evidence = {
            "schema": EVIDENCE_SCHEMA,
            "mode": "execute" if execute else "preflight",
            "status": "failed",
            "manifest_fingerprint": manifest_fingerprint,
            "manifest_fingerprint_match": fingerprint_match,
            "max_whisper_invocations": max_invocations,
            "planned_invocation_count": planned_invocations,
            "invocation_count": 0,
            "error": (
                f"planned invocation count {planned_invocations} exceeds "
                f"max_whisper_invocations {max_invocations}"
            ),
            "runtime": _runtime_verification(manifest),
            "sources": [],
        }
        _write_evidence_atomic(evidence_path, evidence)
        raise TimingInputRunnerError(evidence["error"])

    runtime_info = _runtime_verification(manifest)
    source_entries: list[dict[str, Any]] = []
    preflight_ok = fingerprint_match and runtime_info["verified"]
    invocation_count = 0
    status = "preflight_ok" if not execute else "ok"
    global_error: str | None = None

    for source in selected_sources:
        source_id = str(source["source_id"])
        wav_info = _source_wav_verification(source)
        output_json_path = isolated_output_dir / f"{source_id}.json"
        _assert_isolated_packet_path(output_json_path)
        argv: list[str] = []
        command = ""
        wrapped_command = ""
        entry: dict[str, Any] = {
            "source_id": source_id,
            **wav_info,
            "output_json_path": str(output_json_path),
            "command": command,
            "wrapped_command": wrapped_command,
            "executed_at": None,
            "wall_time_ms": None,
            "peak_memory_bytes": None,
            "exit_code": None,
            "output_json_bytes": None,
            "output_json_sha256": None,
            "output_schema_check": None,
            "executed": False,
        }
        if preflight_ok and wav_info["verified"]:
            argv = build_whisper_argv(
                binary_path=runtime_info["binary"]["path"],
                model_path=runtime_info["model"]["path"],
                audio_path=wav_info["source_wav_path"],
                output_json_path=output_json_path,
                settings=runtime_info["settings"],
            )
            command = shlex.join(argv)
            entry["command"] = command
            if measure_memory:
                wrapped_argv = wrap_argv_for_memory_measurement(argv)
                wrapped_command = shlex.join(wrapped_argv)
                entry["wrapped_command"] = wrapped_command
        source_entries.append(entry)

        if not execute:
            continue
        if not preflight_ok or not wav_info["verified"]:
            status = "failed"
            global_error = "hash 照合または manifest fingerprint 不一致のため whisper-cli を実行しません。"
            break
        if invocation_count >= max_invocations:
            status = "failed"
            global_error = (
                f"invocation_count {invocation_count} が max_whisper_invocations "
                f"{max_invocations} に達したため fail closed しました。"
            )
            break

        invocation_count += 1
        if invocation_count > max_invocations:
            status = "failed"
            global_error = (
                f"invocation_count {invocation_count} が max_whisper_invocations "
                f"{max_invocations} を超えました。"
            )
            break

        started = time.monotonic()
        executed_at = datetime.now(timezone.utc).isoformat()
        run_argv = wrap_argv_for_memory_measurement(argv) if measure_memory else argv
        try:
            completed = subprocess_run(
                run_argv,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
            )
            exit_code = getattr(completed, "returncode", None)
        except OSError as exc:
            status = "failed"
            entry.update(
                {
                    "executed": True,
                    "executed_at": executed_at,
                    "wall_time_ms": max(0, round((time.monotonic() - started) * 1000)),
                    "exit_code": None,
                    "error": f"whisper-cli を起動できませんでした: {exc}",
                }
            )
            global_error = entry["error"]
            break

        wall_time_ms = max(0, round((time.monotonic() - started) * 1000))
        peak_memory_bytes: int | None = None
        if measure_memory:
            stderr_text = getattr(completed, "stderr", "") or ""
            peak_memory_bytes = parse_peak_memory_bytes(stderr_text)
            entry["peak_memory_bytes"] = peak_memory_bytes
        entry.update(
            {
                "executed": True,
                "executed_at": executed_at,
                "wall_time_ms": wall_time_ms,
                "exit_code": exit_code,
            }
        )
        if measure_memory and peak_memory_bytes is None:
            status = "failed"
            entry["error"] = "maximum resident set size を stderr から取得できませんでした。"
            global_error = entry["error"]
            break
        if exit_code != 0:
            status = "failed"
            entry["error"] = f"whisper-cli が非ゼロ終了しました: exit_code={exit_code}"
            global_error = entry["error"]
            break
        if not output_json_path.is_file():
            status = "failed"
            entry["error"] = f"output JSON がありません: {output_json_path}"
            global_error = entry["error"]
            break
        raw_bytes = output_json_path.read_bytes()
        entry["output_json_bytes"] = len(raw_bytes)
        entry["output_json_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            status = "failed"
            entry["error"] = f"output JSON を parse できません: {exc}"
            global_error = entry["error"]
            break
        schema_check = check_whisper_output_schema(payload)
        entry["output_schema_check"] = schema_check
        if not schema_check["valid"]:
            status = "failed"
            entry["error"] = "output JSON schema / token timing 検証に失敗しました。"
            global_error = entry["error"]
            break

    if not fingerprint_match:
        status = "failed"
        global_error = (
            f"manifest_fingerprint mismatch: expected={EXPECTED_MANIFEST_FINGERPRINT} "
            f"actual={manifest_fingerprint}"
        )
    elif not runtime_info["verified"]:
        status = "failed"
        global_error = "runtime binary / model / ffmpeg の hash 照合に失敗しました。"
    elif any(not entry["verified"] for entry in source_entries):
        status = "failed"
        if global_error is None:
            global_error = "source WAV の hash 照合に失敗しました。"

    if not execute and status != "failed":
        status = "preflight_ok"

    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "mode": "execute" if execute else "preflight",
        "status": status,
        "manifest_fingerprint": manifest_fingerprint,
        "manifest_fingerprint_match": fingerprint_match,
        "max_whisper_invocations": max_invocations,
        "planned_invocation_count": planned_invocations,
        "invocation_count": invocation_count,
        "measure_memory": measure_memory,
        "runtime": runtime_info,
        "sources": source_entries,
        "error": global_error,
    }
    _write_evidence_atomic(evidence_path, evidence)
    if status == "failed" and global_error:
        raise TimingInputRunnerError(global_error)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T1-1 bounded whisper-cli timing input runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="preflight または bounded whisper-cli 実行")
    run_parser.add_argument("--manifest", required=True, type=Path)
    run_parser.add_argument("--output-dir", required=True, type=Path)
    run_parser.add_argument("--evidence", required=True, type=Path)
    run_parser.add_argument(
        "--execute",
        action="store_true",
        help="hash 照合後に whisper-cli を実行する（省略時は preflight のみ）",
    )
    run_parser.add_argument(
        "--measure-memory",
        action="store_true",
        help="実行時に /usr/bin/time -l で peak RSS を計測する",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "run":
        return 2
    manifest = load_manifest(
        args.manifest,
        check_sources=False,
        check_runtime_sources=False,
    )
    try:
        evidence = run_timing_inputs(
            manifest,
            output_dir=args.output_dir,
            evidence_path=args.evidence,
            execute=args.execute,
            measure_memory=args.measure_memory,
        )
    except TimingInputRunnerError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "mode": evidence["mode"],
                "planned_invocation_count": evidence["planned_invocation_count"],
                "invocation_count": evidence["invocation_count"],
                "manifest_fingerprint_match": evidence["manifest_fingerprint_match"],
                "runtime_verified": evidence["runtime"]["verified"],
                "source_count": len(evidence["sources"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
