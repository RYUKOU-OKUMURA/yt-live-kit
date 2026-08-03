"""S9-1 の cold / warm raw report を統合する比較 report generator。

実測そのものは ``s9_benchmark.py run`` が担当し、この module は既存の
raw report・固定 fixture・production hash 証跡だけを読み、JSON と Markdown
の比較 report を決定的に組み立てる。ネットワーク、YouTube、production data
への書き込みは行わない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from statistics import median
import sys
from typing import Any, Mapping

# ``python benchmarks/s9_compare.py`` でも repository root の harness を import
# できるようにする。実行時の外部 path は受け取らず、この file の親だけを使う。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.s9_benchmark import (
    CueAnchor,
    NormalizationConfig,
    TimeRange,
    WhisperSettings,
    _case_metrics,
    _exclude_marker_cues,
    build_whisper_argv,
    deduplicate_progressive_timed,
    evaluate_boundary_audit,
    file_fingerprint,
    manifest_fingerprint,
    parse_vtt_file,
    parse_whisper_json,
    _parse_peak_rss,
    sha256_file,
)
from benchmarks.s9_human_audit import HumanAuditError, validate_human_audit


CASE_IDS = (
    "lb4-clip002-short-proper-nouns",
    "mkw-long-local-asr",
    "cgal-proper-nouns",
    "hpe-audio-variation",
)
MODEL_NAMES = ("ggml-large-v3-turbo-q5_0", "ggml-large-v3-turbo")
OPERATIONAL_REFERENCE_MODE = "operational_transcript_reference"
COMPARISON_REPORT_SCHEMA = "s9-1-comparison-report-v7"
RAW_REPORT_SCHEMA = "s9-1-benchmark-report-v1"
PARITY_SCHEMA = "s9-1-vtt-progressive-parity-v2"
EVALUATION_CONTRACT_SCHEMA = "s9-1-evaluation-contract-v2"
EXPECTED_TIMEOUT_SEC = 180.0
REAL_TIME_TOLERANCE_MS = 25
PROTECTED_PRODUCTION_RELATIVE_PATHS = frozenset(
    {"LB4px1wRFnY/shorts/cutplan/cut_clip_003.json"}
)
CANONICAL_MODEL_RUN_DIRECTORIES = {
    "ggml-large-v3-turbo-q5_0": "q5",
    "ggml-large-v3-turbo": "turbo",
}
CANONICAL_RUN_KIND_DIRECTORIES = {
    "cold": "cold-audit-apply",
    "warm": "warm-audit-apply",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object が必要です: {path}")
    return value


def _flatten_source_files(fixture: Mapping[str, Any]) -> list[Path]:
    values: list[Path] = []
    for case in fixture.get("cases", []):
        source_files = case.get("source_files") if isinstance(case, Mapping) else None
        if not isinstance(source_files, Mapping):
            raise ValueError("fixture case の source_files がありません")
        for value in source_files.values():
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                if not isinstance(entry, str) or not entry:
                    raise ValueError("fixture source_files の path が不正です")
                candidate = Path(entry)
                if not candidate.is_absolute() or ".." in candidate.parts:
                    raise ValueError(f"fixture source_files は絶対canonical pathが必要です: {entry}")
                values.append(candidate)
    unique = list(dict.fromkeys(values))
    if not unique:
        raise ValueError("fixture source_files が空です")
    return unique


def _path_has_symlink(path: Path, *, root: Path) -> bool:
    if root.is_symlink():
        return True
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _derive_production_scope(fixture: Mapping[str, Any]) -> dict[str, Any]:
    source_paths = _flatten_source_files(fixture)
    try:
        root = Path(os.path.commonpath([str(path) for path in source_paths]))
    except ValueError as exc:
        raise ValueError("fixture source_files から production root を導出できません") from exc
    if not root.is_absolute() or root.is_symlink():
        raise ValueError(f"production root が不正です: {root}")
    expected_paths: dict[str, Path] = {}
    for source_path in source_paths:
        try:
            relative = source_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"fixture source file が production root 外です: {source_path}") from exc
        relative_text = relative.as_posix()
        if not relative_text or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"fixture source file の relative path が不正です: {relative_text}")
        expected_paths[relative_text] = source_path
    for relative_text in PROTECTED_PRODUCTION_RELATIVE_PATHS:
        protected_path = root / relative_text
        if not protected_path.is_file():
            raise ValueError(f"固定保護対象 production file がありません: {protected_path}")
        expected_paths[relative_text] = protected_path
    if len(source_paths) != 14 or len(expected_paths) != 15:
        raise ValueError(
            f"production scope の固定件数が不正です: source={len(source_paths)} expected={len(expected_paths)}"
        )
    return {
        "root": root,
        "source_paths": expected_paths,
        "expected_relative_paths": frozenset(expected_paths),
        "fixture_source_file_count": len(source_paths),
        "protected_file_count": len(PROTECTED_PRODUCTION_RELATIVE_PATHS),
    }


def _validate_relative_path(relative_path: str) -> None:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError(f"production hash artifact の relative path が不正です: {relative_path}")
    candidate = Path(relative_path)
    if relative_path != candidate.as_posix() or ".." in candidate.parts or "." in candidate.parts:
        raise ValueError(f"production hash artifact の relative path がcanonicalではありません: {relative_path}")


def _validate_production_hash_artifact(path: Path, *, expected_scope: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact fixture scope and re-read every production file."""

    report = _read_json(path)
    root = report.get("root")
    files = report.get("files")
    if not isinstance(root, str) or not isinstance(files, Mapping) or not files:
        raise ValueError(f"production hash artifact の schema が不正です: {path}")
    expected_root = Path(str(expected_scope["root"]))
    if Path(root) != expected_root:
        raise ValueError(f"production hash artifact の root が fixture scope と一致しません: {path}")
    expected_relative_paths = set(expected_scope["expected_relative_paths"])
    actual_relative_paths = set(files)
    if actual_relative_paths != expected_relative_paths:
        raise ValueError(f"production hash artifact の file set が fixture scope と一致しません: {path}")
    actual_files: dict[str, dict[str, Any]] = {}
    for relative_path, expected in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected, Mapping):
            raise ValueError(f"production hash artifact の file entry が不正です: {path}")
        _validate_relative_path(relative_path)
        if set(expected) != {"bytes", "sha256"}:
            raise ValueError(f"production hash artifact の file entry schema が不正です: {path} / {relative_path}")
        target = Path(root) / relative_path
        resolved_root = expected_root.resolve(strict=True)
        if _path_has_symlink(target, root=expected_root):
            raise ValueError(f"production hash artifact の symlink/root escape を拒否しました: {target}")
        try:
            target.resolve(strict=True).relative_to(resolved_root)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"production hash artifact の symlink/root escape を拒否しました: {target}") from exc
        if not target.is_file() or target.is_symlink():
            raise ValueError(f"production hash artifact の対象ファイルがありません: {target}")
        if isinstance(expected.get("bytes"), bool) or not isinstance(expected.get("bytes"), int) or expected["bytes"] < 0:
            raise ValueError(f"production hash artifact の bytes が不正です: {target}")
        if not isinstance(expected.get("sha256"), str) or len(expected["sha256"]) != 64:
            raise ValueError(f"production hash artifact の sha256 が不正です: {target}")
        actual = {"bytes": target.stat().st_size, "sha256": sha256_file(target)}
        if actual != {"bytes": expected.get("bytes"), "sha256": expected.get("sha256")}:
            raise ValueError(f"production file hash が artifact と一致しません: {target}")
        actual_files[relative_path] = actual
    result = dict(report)
    result["actual_recheck"] = True
    result["actual_files"] = actual_files
    result["scope_validation"] = {
        "root_matches_fixture": True,
        "exact_file_set": True,
        "path_traversal_rejected": True,
        "symlink_escape_rejected": True,
        "expected_file_count": len(expected_relative_paths),
        "fixture_source_file_count": expected_scope["fixture_source_file_count"],
        "protected_file_count": expected_scope["protected_file_count"],
    }
    return result


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _compact_metric(metric: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": metric.get("status"),
        "cer": metric.get("cer"),
        "levenshtein": metric.get("levenshtein"),
        "glossary": metric.get("glossary", {}),
        "cue": metric.get("cue", {}),
        "wall_time_ms": metric.get("wall_time_ms"),
        "peak_memory_bytes": metric.get("peak_memory_bytes"),
        "run_kind": metric.get("run_kind"),
        "declared_cache_hit": metric.get("cache_hit"),
    }


def _output_fingerprint(metric: Mapping[str, Any]) -> dict[str, Any] | None:
    execution = metric.get("execution")
    if not isinstance(execution, Mapping):
        return None
    paths = execution.get("output_paths")
    if not isinstance(paths, list) or not paths or not isinstance(paths[0], str):
        return None
    path = Path(paths[0])
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _case_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("raw report の cases が配列ではありません。")
    result: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
            raise ValueError("raw report の case が不正です。")
        result[str(case["case_id"])] = case
    if tuple(result) != CASE_IDS:
        raise ValueError(f"case 順または case 集合が固定値と異なります: {tuple(result)}")
    return result


def _expected_case_identity(fixture: Mapping[str, Any], case_id: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
    case = next((item for item in fixture["cases"] if item["id"] == case_id), None)
    if case is None:
        raise ValueError(f"fixture に固定 case がありません: {case_id}")
    start_ms, end_ms = case["range_ms"]
    anchors = [
        {"anchor_id": f"anchor-{index}", "start_ms": values[0], "end_ms": values[1]}
        for index, values in enumerate(case["gold"]["cue_anchors_ms"], 1)
    ]
    return {"start_ms": start_ms, "end_ms": end_ms}, anchors


def _expected_whisper_settings(fixture: Mapping[str, Any]) -> dict[str, Any]:
    whisper = fixture["whisper"]
    decode = dict(whisper["decode"])
    padding_ms = decode.pop("padding_ms", 0)
    decode.update({"threads": whisper["threads"], "processors": whisper["processors"]})
    return {
        "language": whisper["language"],
        "initial_prompt": whisper["initial_prompt"],
        "padding_ms": padding_ms,
        "decode": decode,
        "output_schema": whisper["output_schema"],
    }


def _expected_runtime_identity(fixture: Mapping[str, Any], binary_fingerprint: Mapping[str, Any]) -> dict[str, Any]:
    whisper = fixture["whisper"]
    return {
        "binary_path": whisper["binary"],
        "binary_fingerprint": dict(binary_fingerprint),
        "version": whisper["version"],
        "settings": _expected_whisper_settings(fixture),
        "timeout_sec": float(whisper.get("timeout_sec", EXPECTED_TIMEOUT_SEC)),
        "output_schema": whisper["output_schema"],
    }


def _canonical_run_root(fixture: Mapping[str, Any], model_name: str, run_kind: str) -> Path:
    model_directory = CANONICAL_MODEL_RUN_DIRECTORIES.get(model_name)
    run_directory = CANONICAL_RUN_KIND_DIRECTORIES.get(run_kind)
    if model_directory is None or run_directory is None:
        raise ValueError(f"canonical benchmark run identity が不正です: {model_name} / {run_kind}")
    cache_root = Path(str(fixture["audio_cache_root"]))
    if not cache_root.is_absolute() or cache_root.is_symlink():
        raise ValueError(f"canonical benchmark cache root が不正です: {cache_root}")
    return cache_root / "runs" / model_directory / run_directory


def _expected_candidate_output_path(run_root: Path, case_id: str, run_kind: str) -> Path:
    if case_id not in CASE_IDS or run_kind not in CANONICAL_RUN_KIND_DIRECTORIES:
        raise ValueError(f"canonical candidate output identity が不正です: {case_id} / {run_kind}")
    return run_root / "whisper" / case_id / f"{run_kind}.json"


def _validate_canonical_run_report_path(path: Path, *, expected_run_root: Path) -> None:
    expected_report = expected_run_root / "report.json"
    if str(path) != str(expected_report):
        raise ValueError(f"raw report path が canonical run root と一致しません: {path}")
    if _path_has_symlink(path, root=expected_run_root):
        raise ValueError(f"raw report path の symlink escape を拒否しました: {path}")
    try:
        path.resolve(strict=True).relative_to(expected_run_root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"raw report path が canonical run root 外です: {path}") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"canonical raw report がありません: {path}")


def _validate_canonical_output_path(
    output_path_text: str,
    *,
    expected_output_path: Path,
    expected_run_root: Path,
) -> Path:
    if output_path_text != str(expected_output_path):
        raise ValueError(
            f"candidate output path が canonical case/model/run-kind path と一致しません: {output_path_text}"
        )
    output_path = Path(output_path_text)
    if _path_has_symlink(output_path, root=expected_run_root):
        raise ValueError(f"candidate output path の symlink escape を拒否しました: {output_path}")
    try:
        output_path.resolve(strict=True).relative_to(expected_run_root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"candidate output path が canonical run root 外です: {output_path}") from exc
    if not output_path.is_file() or output_path.is_symlink():
        raise ValueError(f"canonical candidate output artifact がありません: {output_path}")
    return output_path


def _normalization_config(fixture: Mapping[str, Any]) -> NormalizationConfig:
    value = fixture.get("normalization", {})
    if not isinstance(value, Mapping):
        raise ValueError("fixture normalization が不正です")
    if "unicode_form" in value:
        return NormalizationConfig.from_mapping(value)
    return NormalizationConfig.from_mapping(
        {
            "unicode_form": value.get("unicode", "NFKC"),
            "remove_whitespace": value.get("strip_whitespace", True),
            "ignore_punctuation": value.get("ignore_punctuation", False),
        }
    )


def _expected_source_inputs(
    fixture: Mapping[str, Any],
    *,
    production_scope: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Path]]:
    inputs: list[dict[str, Any]] = []
    expected_vtt_inputs: dict[str, dict[str, Any]] = {}
    vtt_paths: dict[str, Path] = {}
    audio_root = Path(str(fixture["audio_cache_root"]))
    for case in fixture["cases"]:
        case_id = case["id"]
        source_path = Path(str(case["source_files"]["vtt"]))
        relative = source_path.relative_to(Path(str(production_scope["root"]))).as_posix()
        vtt_path = Path(str(production_scope["source_paths"][relative]))
        vtt_fingerprint = file_fingerprint(vtt_path)
        expected_vtt_inputs[case_id] = vtt_fingerprint
        vtt_paths[case_id] = vtt_path
        inputs.append({"kind": "baseline_vtt", "case_id": case_id, **vtt_fingerprint})
        audio_path = audio_root / str(case["audio_fixture"])
        audio_fingerprint = file_fingerprint(
            audio_path,
            expected_sha256=case["audio_sha256"],
            expected_bytes=case["audio_bytes"],
        )
        inputs.append({"kind": "audio", "case_id": case_id, **audio_fingerprint})
    return inputs, expected_vtt_inputs, vtt_paths


def _cue_sequence_fingerprint(cues: list[Mapping[str, Any]]) -> dict[str, Any]:
    payload = json.dumps(cues, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _text_sequence_sha256(texts: list[str]) -> str:
    return hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()


def _parse_real_time_ms(stderr: str) -> int | None:
    """Parse macOS ``/usr/bin/time -l``'s displayed real seconds."""

    matches = re.findall(r"(?m)^\s*([0-9]+(?:\.[0-9]+)?)\s+real\b", stderr)
    if len(matches) != 1:
        return None
    return round(float(matches[0]) * 1000)


def _validate_vtt_parity_artifact(
    path: Path,
    *,
    fixture: Mapping[str, Any],
    expected_vtt_inputs: Mapping[str, Mapping[str, Any]],
    expected_vtt_paths: Mapping[str, Path],
) -> dict[str, Any]:
    """Recompute production/benchmark VTT parity and reject fail-open artifacts."""

    from yt_live_kit.services.vtt_parser import deduplicate_progressive, parse_vtt

    artifact = _read_json(path)
    expected_top = {"schema", "benchmark_id", "fixture_fingerprint", "production_function", "benchmark_function", "cases"}
    if set(artifact) != expected_top:
        raise ValueError(f"VTT parity artifact のschemaが不正です: {path}")
    if artifact.get("schema") != PARITY_SCHEMA:
        raise ValueError(f"VTT parity artifact のschemaが固定値と異なります: {path}")
    if artifact.get("benchmark_id") != fixture["benchmark_id"] or artifact.get("fixture_fingerprint") != manifest_fingerprint(fixture):
        raise ValueError(f"VTT parity artifact のfixture identityが不正です: {path}")
    if artifact.get("production_function") != "yt_live_kit.services.vtt_parser.deduplicate_progressive":
        raise ValueError(f"VTT parity artifact のproduction functionが不正です: {path}")
    if artifact.get("benchmark_function") != "benchmarks.s9_benchmark.deduplicate_progressive_timed":
        raise ValueError(f"VTT parity artifact のbenchmark functionが不正です: {path}")
    cases = artifact.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CASE_IDS):
        raise ValueError(f"VTT parity artifact のcasesが固定4件ではありません: {path}")
    if [case.get("case_id") for case in cases if isinstance(case, Mapping)] != list(CASE_IDS):
        raise ValueError(f"VTT parity artifact のcase順が固定値と異なります: {path}")
    expected_case_fields = {
        "case_id",
        "source_vtt_bytes",
        "source_vtt_sha256",
        "production_raw_cues",
        "benchmark_raw_cues",
        "production_dedup_cues",
        "benchmark_dedup_cues",
        "production_text_sha256",
        "benchmark_text_sha256",
        "text_sequence_equal",
    }
    validated_cases: list[dict[str, Any]] = []
    for raw_case in cases:
        if not isinstance(raw_case, Mapping) or set(raw_case) != expected_case_fields:
            raise ValueError(f"VTT parity artifact のcase schemaが不正です: {path}")
        case_id = raw_case["case_id"]
        if case_id not in CASE_IDS:
            raise ValueError(f"VTT parity artifact にunknown caseがあります: {case_id}")
        source_path = expected_vtt_paths[case_id]
        actual_source = file_fingerprint(source_path)
        if raw_case["source_vtt_bytes"] != actual_source["bytes"] or raw_case["source_vtt_sha256"] != actual_source["sha256"]:
            raise ValueError(f"VTT parity artifact のsource VTT hashが実体と一致しません: {case_id}")
        expected_source = expected_vtt_inputs[case_id]
        if actual_source != expected_source:
            raise ValueError(f"VTT parity artifact のsource VTT identityがproduction hashと一致しません: {case_id}")
        content = source_path.read_text(encoding="utf-8")
        production_raw = parse_vtt(content)
        benchmark_raw = parse_vtt_file(source_path)
        production_dedup = deduplicate_progressive(production_raw)
        benchmark_dedup = deduplicate_progressive_timed(benchmark_raw)
        production_text = [cue.text for cue in production_dedup]
        benchmark_text = [cue.text for cue in benchmark_dedup]
        expected_values = {
            "production_raw_cues": len(production_raw),
            "benchmark_raw_cues": len(benchmark_raw),
            "production_dedup_cues": len(production_dedup),
            "benchmark_dedup_cues": len(benchmark_dedup),
            "production_text_sha256": _text_sequence_sha256(production_text),
            "benchmark_text_sha256": _text_sequence_sha256(benchmark_text),
            "text_sequence_equal": production_text == benchmark_text,
        }
        if expected_values["text_sequence_equal"] is not True or raw_case["text_sequence_equal"] is not True:
            raise ValueError(f"VTT parity artifact のtext_sequence_equalがtrueではありません: {case_id}")
        for key, expected_value in expected_values.items():
            if raw_case[key] != expected_value:
                raise ValueError(f"VTT parity artifact の再計算値が一致しません: {case_id} / {key}")
        for key in ("production_raw_cues", "benchmark_raw_cues", "production_dedup_cues", "benchmark_dedup_cues"):
            if isinstance(raw_case[key], bool) or not isinstance(raw_case[key], int) or raw_case[key] <= 0:
                raise ValueError(f"VTT parity artifact のcue countが不正です: {case_id} / {key}")
        validated_cases.append({"case_id": case_id, **expected_values, "source_vtt": actual_source})
    return {
        "passed": True,
        "schema_verified": True,
        "benchmark_identity_verified": True,
        "fixture_identity_verified": True,
        "source_hashes_verified": True,
        "text_sequence_equal_verified": True,
        "case_count": len(validated_cases),
        "cases": validated_cases,
    }


def _validate_raw_case_evidence(
    *,
    raw_case: Mapping[str, Any],
    fixture: Mapping[str, Any],
    case_id: str,
    expected_run_kind: str,
    expected_model_path: Path,
    expected_audio_path: Path,
    expected_vtt_path: Path,
    expected_settings: Mapping[str, Any],
    expected_run_root: Path,
) -> dict[str, Any]:
    """Reparse output artifacts and recompute all transcript-related metrics."""

    expected_range, expected_anchors = _expected_case_identity(fixture, case_id)
    target = TimeRange.from_value(expected_range)
    fixture_case = next(case for case in fixture["cases"] if case["id"] == case_id)
    normalization = _normalization_config(fixture)
    anchors = [CueAnchor.from_value(anchor, index) for index, anchor in enumerate(expected_anchors)]
    glossary = fixture_case["gold"]["glossary"]
    gold_text = fixture_case["gold"]["text"]
    candidate = raw_case.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError(f"raw report の candidate がありません: {case_id}")
    execution = candidate.get("execution")
    if not isinstance(execution, Mapping):
        raise ValueError(f"raw report の execution がありません: {case_id}")
    output_paths = execution.get("output_paths")
    if not isinstance(output_paths, list) or len(output_paths) != 1 or not isinstance(output_paths[0], str):
        raise ValueError(f"raw report の output_paths が不正です: {case_id}")
    expected_output_path = _expected_candidate_output_path(expected_run_root, case_id, expected_run_kind)
    output_path = _validate_canonical_output_path(
        output_paths[0],
        expected_output_path=expected_output_path,
        expected_run_root=expected_run_root,
    )
    if raw_case.get("candidate_output_path") != str(expected_output_path):
        raise ValueError(f"raw report の candidate_output_path が execution と一致しません: {case_id}")
    output_fingerprint = file_fingerprint(output_path)
    declared_output_fingerprint = raw_case.get("candidate_output_fingerprint")
    if declared_output_fingerprint is not None:
        if not isinstance(declared_output_fingerprint, Mapping) or dict(declared_output_fingerprint) != output_fingerprint:
            raise ValueError(f"raw report の candidate output fingerprint が実体と一致しません: {case_id}")
    output_payload = _read_json(output_path)
    params = output_payload.get("params")
    if not isinstance(params, Mapping) or params.get("model") != str(expected_model_path) or params.get("language") != fixture["whisper"]["language"]:
        raise ValueError(f"candidate output の model/language identity が不正です: {output_path}")
    actual_cues = parse_whisper_json(
        output_payload,
        absolute_start_ms=target.start_ms,
        expected_schema=fixture["whisper"]["output_schema"],
    )
    actual_cue_dicts = [cue.to_dict() for cue in actual_cues]
    if execution.get("cues") != actual_cue_dicts:
        raise ValueError(f"raw report の candidate text/timestamp が output artifact と一致しません: {case_id}")
    argv = execution.get("argv")
    expected_argv = build_whisper_argv(
        binary_path=fixture["whisper"]["binary"],
        model_path=expected_model_path,
        audio_path=expected_audio_path,
        output_json_path=output_path,
        settings=WhisperSettings.from_mapping(expected_settings),
        target_range=target,
    )
    if argv != expected_argv:
        raise ValueError(f"raw report の execution argv が fixture と一致しません: {case_id}")
    measured_argv = execution.get("measured_argv")
    allowed_measured = (expected_argv, ["/usr/bin/time", "-l", *expected_argv])
    if measured_argv not in allowed_measured:
        raise ValueError(f"raw report の measured_argv が固定argvと一致しません: {case_id}")
    if execution.get("status") != "ok" or execution.get("returncode") != 0 or execution.get("run_kind") != expected_run_kind:
        raise ValueError(f"raw report の execution status が不正です: {case_id}")
    if execution.get("output_paths") != [str(output_path)]:
        raise ValueError(f"raw report の output path identity が不正です: {case_id}")
    if execution.get("error") is not None:
        raise ValueError(f"raw report の成功 execution に error があります: {case_id}")
    measured_real_ms = _parse_real_time_ms(str(execution.get("stderr", "")))
    measured_peak_bytes = _parse_peak_rss(str(execution.get("stderr", "")))
    if measured_real_ms is None:
        raise ValueError(f"raw report の execution.stderr に real time がありません: {case_id}")
    if measured_peak_bytes is None:
        raise ValueError(f"raw report の execution.stderr に peak memory がありません: {case_id}")
    duration_ms = execution.get("duration_ms")
    peak_memory_bytes = execution.get("peak_memory_bytes")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or duration_ms < 0:
        raise ValueError(f"raw report の duration_ms が不正です: {case_id}")
    if abs(float(duration_ms) - measured_real_ms) > REAL_TIME_TOLERANCE_MS:
        raise ValueError(f"raw report の duration_ms がstderr real timeと一致しません: {case_id}")
    if peak_memory_bytes != measured_peak_bytes:
        raise ValueError(f"raw report の peak memory がstderr実測値と一致しません: {case_id}")
    if candidate.get("wall_time_ms") != execution.get("duration_ms"):
        raise ValueError(f"raw report の wall time が execution と一致しません: {case_id}")
    if candidate.get("peak_memory_bytes") != execution.get("peak_memory_bytes"):
        raise ValueError(f"raw report の peak memory が execution と一致しません: {case_id}")
    if candidate.get("run_kind") != execution.get("run_kind") or candidate.get("cache_hit") != execution.get("cache_hit"):
        raise ValueError(f"raw report の candidate execution metadata が一致しません: {case_id}")

    baseline_cues = deduplicate_progressive_timed(parse_vtt_file(expected_vtt_path))
    baseline_cues = _exclude_marker_cues(
        baseline_cues,
        fixture["normalization"].get("exclude_text_tokens", []),
        normalization,
    )
    expected_baseline = _case_metrics(
        gold_text=gold_text,
        cues=baseline_cues,
        gold_anchors=anchors,
        target=target,
        glossary=glossary,
        normalization=normalization,
        wall_time_ms=None,
        peak_memory_bytes=None,
        run_kind=None,
        cache_hit=None,
    )
    if raw_case.get("baseline") != expected_baseline:
        raise ValueError(f"raw report の baseline metrics が実VTTからの再計算値と一致しません: {case_id}")
    expected_candidate = _case_metrics(
        gold_text=gold_text,
        cues=actual_cues,
        gold_anchors=anchors,
        target=target,
        glossary=glossary,
        normalization=normalization,
        wall_time_ms=execution.get("duration_ms"),
        peak_memory_bytes=execution.get("peak_memory_bytes"),
        run_kind=expected_run_kind,
        cache_hit=execution.get("cache_hit"),
    )
    candidate_without_execution = {key: value for key, value in candidate.items() if key != "execution"}
    if candidate_without_execution != expected_candidate:
        raise ValueError(f"raw report の candidate metrics がoutput artifactからの再計算値と一致しません: {case_id}")
    return {
        "case_id": case_id,
        "expected_output_path": str(expected_output_path),
        "output_fingerprint": output_fingerprint,
        "output_path_identity_verified": True,
        "output_fingerprint_verified": True,
        "candidate_cue_fingerprint": _cue_sequence_fingerprint(actual_cue_dicts),
        "metrics_verified": True,
        "argv_verified": True,
        "output_schema_verified": True,
        "measurement_verified": True,
    }


def _validate_raw_report_identity(
    path: Path,
    *,
    fixture: Mapping[str, Any],
    expected_model_name: str,
    expected_run_kind: str,
    expected_manifest_fingerprint: str | None = None,
    expected_inputs: list[Mapping[str, Any]] | None = None,
    expected_vtt_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    expected_model_fingerprint: Mapping[str, Any] | None = None,
    expected_runtime_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed if one raw report is not the fixed benchmark invocation."""

    report = _read_json(path)
    if report.get("schema") != RAW_REPORT_SCHEMA:
        raise ValueError(f"raw report schema が固定値と異なります: {path}")
    if report.get("benchmark_id") != fixture["benchmark_id"]:
        raise ValueError(f"raw report benchmark_id が固定値と異なります: {path}")
    run_manifest_fingerprint = report.get("manifest_fingerprint")
    if not isinstance(run_manifest_fingerprint, str) or len(run_manifest_fingerprint) != 64:
        raise ValueError(f"raw report の manifest_fingerprint が不正です: {path}")
    if expected_manifest_fingerprint is not None and run_manifest_fingerprint != expected_manifest_fingerprint:
        raise ValueError(f"raw report 間の manifest_fingerprint が一致しません: {path}")

    raw_model = report.get("fingerprints", {}).get("model")
    if not isinstance(raw_model, Mapping):
        raise ValueError(f"raw report の model fingerprint がありません: {path}")
    expected_model = next((model for model in fixture["models"] if model["name"] == expected_model_name), None)
    if expected_model is None:
        raise ValueError(f"fixture に model がありません: {expected_model_name}")
    for field in ("path", "bytes", "sha256"):
        if raw_model.get(field) != expected_model[field]:
            raise ValueError(f"raw report の model {field} が fixture と一致しません: {path}")
    if expected_model_fingerprint is not None and dict(raw_model) != dict(expected_model_fingerprint):
        raise ValueError(f"raw report の model fingerprint が実model fileと一致しません: {path}")

    inputs = report.get("fingerprints", {}).get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError(f"raw report の input fingerprints がありません: {path}")
    if expected_inputs is not None and inputs != expected_inputs:
        raise ValueError(f"raw report 間の audio / VTT fingerprint が一致しません: {path}")
    inputs_by_key = {
        (item.get("kind"), item.get("case_id")): item
        for item in inputs
        if isinstance(item, Mapping)
    }
    for case in fixture["cases"]:
        key = ("audio", case["id"])
        audio = inputs_by_key.get(key)
        if not isinstance(audio, Mapping):
            raise ValueError(f"raw report の audio fingerprint がありません: {path} / {case['id']}")
        if audio.get("bytes") != case["audio_bytes"] or audio.get("sha256") != case["audio_sha256"]:
            raise ValueError(f"raw report の audio identity が fixture と一致しません: {path} / {case['id']}")
        vtt = inputs_by_key.get(("baseline_vtt", case["id"]))
        expected_vtt = expected_vtt_inputs.get(case["id"]) if expected_vtt_inputs is not None else None
        if not isinstance(vtt, Mapping) or not isinstance(expected_vtt, Mapping):
            raise ValueError(f"raw report の baseline VTT fingerprint がありません: {path} / {case['id']}")
        if vtt.get("bytes") != expected_vtt.get("bytes") or vtt.get("sha256") != expected_vtt.get("sha256"):
            raise ValueError(f"raw report の baseline VTT identity が production hash と一致しません: {path} / {case['id']}")

    runtime = report.get("whisper_runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError(f"raw report の whisper runtime がありません: {path}")
    runtime_identity = {
        "binary_path": runtime.get("binary_path"),
        "binary_fingerprint": runtime.get("binary_fingerprint"),
        "version": runtime.get("version"),
        "settings": runtime.get("settings"),
        "timeout_sec": runtime.get("timeout_sec"),
        "output_schema": runtime.get("output_schema"),
    }
    whisper = fixture["whisper"]
    expected_settings = _expected_whisper_settings(fixture)
    if runtime_identity["binary_path"] != whisper["binary"]:
        raise ValueError(f"raw report の whisper binary が fixture と一致しません: {path}")
    if runtime_identity["version"] != whisper["version"] or runtime_identity["settings"] != expected_settings:
        raise ValueError(f"raw report の whisper settings が fixture と一致しません: {path}")
    if runtime_identity["timeout_sec"] != float(whisper.get("timeout_sec", EXPECTED_TIMEOUT_SEC)):
        raise ValueError(f"raw report の whisper timeout がfixtureと一致しません: {path}")
    if runtime_identity["output_schema"] != whisper["output_schema"]:
        raise ValueError(f"raw report の output schema が fixture と一致しません: {path}")
    if expected_runtime_identity is not None and runtime_identity != expected_runtime_identity:
        raise ValueError(f"raw report 間の runtime identity が一致しません: {path}")

    if report.get("gold_audit_status") != "provisional" or report.get("metrics_status") != "provisional":
        raise ValueError(f"raw report の provisional audit status が変わっています: {path}")
    cases = _case_map(report)
    statuses: list[str | None] = []
    for case_id in CASE_IDS:
        raw_case = cases[case_id]
        expected_range, expected_anchors = _expected_case_identity(fixture, case_id)
        if raw_case.get("target_range") != expected_range or raw_case.get("gold_cue_anchors") != expected_anchors:
            raise ValueError(f"raw report の range / cue anchor identity が fixture と一致しません: {path} / {case_id}")
        candidate = raw_case.get("candidate")
        if not isinstance(candidate, Mapping):
            raise ValueError(f"raw report の candidate がありません: {path} / {case_id}")
        statuses.append(candidate.get("status"))
        if candidate.get("status") != "ok" or candidate.get("run_kind") != expected_run_kind:
            raise ValueError(f"raw report の candidate run status が不正です: {path} / {case_id}")
        execution = candidate.get("execution")
        if not isinstance(execution, Mapping) or execution.get("returncode") != 0 or execution.get("run_kind") != expected_run_kind:
            raise ValueError(f"raw report の execution identity が不正です: {path} / {case_id}")

    return {
        "path": str(path),
        "schema": report["schema"],
        "benchmark_id": report["benchmark_id"],
        "run_kind": expected_run_kind,
        "run_manifest_fingerprint": run_manifest_fingerprint,
        "inputs": inputs,
        "runtime_report": runtime,
        "runtime_identity": runtime_identity,
        "case_count": len(cases),
        "all_case_runs_ok": all(status == "ok" for status in statuses),
    }


def _relative_improvement(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else -1.0
    return (baseline - candidate) / baseline


def _aggregate_glossary(metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    keys = ("expected", "found", "missing", "incorrect", "exact_match_terms")
    result = {key: 0 for key in keys}
    for metric in metrics:
        glossary = metric.get("glossary", {})
        for key in keys:
            result[key] += int(glossary.get(key, 0))
    result["definition"] = "fixed glossary exact surface; found non-regression and missing/incorrect non-increase"
    return result


def _aggregate_cue(metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    missing = sum(int(metric.get("cue", {}).get("missing", 0)) for metric in metrics)
    duplicate = sum(int(metric.get("cue", {}).get("duplicate", 0)) for metric in metrics)
    anchors = sum(int(metric.get("cue", {}).get("gold_anchor_count", 0)) for metric in metrics)
    output = sum(int(metric.get("cue", {}).get("output_cue_count", 0)) for metric in metrics)
    return {
        "missing": missing,
        "duplicate": duplicate,
        "error_count": missing + duplicate,
        "gold_anchor_count": anchors,
        "output_cue_count": output,
        "error_rate": (missing + duplicate) / anchors if anchors else (0.0 if output == 0 else 1.0),
    }


def _run_case(
    case_id: str,
    cold_case: Mapping[str, Any],
    warm_case: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = _compact_metric(cold_case["baseline"])
    cold = _compact_metric(cold_case["candidate"])
    warm = _compact_metric(warm_case["candidate"])
    cold_output = _output_fingerprint(cold_case["candidate"])
    warm_output = _output_fingerprint(warm_case["candidate"])
    stable = bool(cold_output and warm_output and cold_output["sha256"] == warm_output["sha256"])
    return {
        "case_id": case_id,
        "baseline": baseline,
        "cold": cold,
        "warm": warm,
        "relative_cer_improvement": _relative_improvement(float(baseline["cer"]), float(cold["cer"])),
        "cold_output": cold_output,
        "warm_output": warm_output,
        "cold_warm_output_sha_equal": stable,
    }


def _model_report(
    *,
    name: str,
    fixture: Mapping[str, Any],
    cold_path: Path,
    warm_path: Path,
    transcript_audit: Mapping[str, Any],
    expected_inputs: list[Mapping[str, Any]],
    expected_vtt_inputs: Mapping[str, Mapping[str, Any]],
    expected_vtt_paths: Mapping[str, Path],
    expected_model_fingerprint: Mapping[str, Any],
    expected_runtime_identity: Mapping[str, Any],
    parity_validation: Mapping[str, Any],
    shared_raw_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cold_report = _read_json(cold_path)
    warm_report = _read_json(warm_path)
    expected_cold_run_root = _canonical_run_root(fixture, name, "cold")
    expected_warm_run_root = _canonical_run_root(fixture, name, "warm")
    _validate_canonical_run_report_path(cold_path, expected_run_root=expected_cold_run_root)
    _validate_canonical_run_report_path(warm_path, expected_run_root=expected_warm_run_root)
    if shared_raw_identity is not None:
        if shared_raw_identity.get("inputs") != expected_inputs:
            raise ValueError("model間のraw input identityが実fixtureと一致しません")
        if shared_raw_identity.get("runtime_identity") != expected_runtime_identity:
            raise ValueError("model間のraw runtime identityが実fixtureと一致しません")
    cold_identity = _validate_raw_report_identity(
        cold_path,
        fixture=fixture,
        expected_model_name=name,
        expected_run_kind="cold",
        expected_inputs=expected_inputs,
        expected_vtt_inputs=expected_vtt_inputs,
        expected_model_fingerprint=expected_model_fingerprint,
        expected_runtime_identity=expected_runtime_identity,
    )
    warm_identity = _validate_raw_report_identity(
        warm_path,
        fixture=fixture,
        expected_model_name=name,
        expected_run_kind="warm",
        expected_manifest_fingerprint=cold_identity["run_manifest_fingerprint"],
        expected_inputs=cold_identity["inputs"],
        expected_vtt_inputs=expected_vtt_inputs,
        expected_model_fingerprint=expected_model_fingerprint,
        expected_runtime_identity=cold_identity["runtime_identity"],
    )
    cold_cases = _case_map(cold_report)
    warm_cases = _case_map(warm_report)
    cold_evidence = [
        _validate_raw_case_evidence(
            raw_case=cold_cases[case_id],
            fixture=fixture,
            case_id=case_id,
            expected_run_kind="cold",
            expected_model_path=Path(str(expected_model_fingerprint["path"])),
            expected_audio_path=Path(str(next(item for item in expected_inputs if item["kind"] == "audio" and item["case_id"] == case_id)["path"])),
            expected_vtt_path=expected_vtt_paths[case_id],
            expected_settings=expected_runtime_identity["settings"],
            expected_run_root=expected_cold_run_root,
        )
        for case_id in CASE_IDS
    ]
    warm_evidence = [
        _validate_raw_case_evidence(
            raw_case=warm_cases[case_id],
            fixture=fixture,
            case_id=case_id,
            expected_run_kind="warm",
            expected_model_path=Path(str(expected_model_fingerprint["path"])),
            expected_audio_path=Path(str(next(item for item in expected_inputs if item["kind"] == "audio" and item["case_id"] == case_id)["path"])),
            expected_vtt_path=expected_vtt_paths[case_id],
            expected_settings=expected_runtime_identity["settings"],
            expected_run_root=expected_warm_run_root,
        )
        for case_id in CASE_IDS
    ]
    cases = [_run_case(case_id, cold_cases[case_id], warm_cases[case_id]) for case_id in CASE_IDS]
    relative_values = [case["relative_cer_improvement"] for case in cases]
    baseline_cases = [case["baseline"] for case in cases]
    cold_candidates = [case["cold"] for case in cases]
    warm_candidates = [case["warm"] for case in cases]
    all_runs = cold_candidates + warm_candidates
    gate = cold_report["gates"]
    evaluation = fixture["gates"]
    cold_budget_ms = round(float(evaluation["cold_wall_time_seconds"]) * 1000)
    warm_budget_ms = round(float(evaluation["warm_wall_time_seconds"]) * 1000)
    wall_checks = [
        {
            "case_id": case["case_id"],
            "run_kind": run_kind,
            "value_ms": run["wall_time_ms"],
            "budget_ms": cold_budget_ms if run_kind == "cold" else warm_budget_ms,
            "passed": run["wall_time_ms"] is not None
            and run["wall_time_ms"] <= (cold_budget_ms if run_kind == "cold" else warm_budget_ms),
        }
        for case in cases
        for run_kind, run in (("cold", case["cold"]), ("warm", case["warm"]))
    ]
    peak_values = [run["peak_memory_bytes"] for run in all_runs if run["peak_memory_bytes"] is not None]
    wall_passed = all(item["passed"] for item in wall_checks)
    peak_passed = bool(peak_values) and max(peak_values) <= int(evaluation["peak_memory_bytes"])
    output_stable = all(case["cold_warm_output_sha_equal"] for case in cases)
    paired_median = median(relative_values)
    baseline_glossary = _aggregate_glossary(baseline_cases)
    candidate_glossary = _aggregate_glossary(cold_candidates)
    baseline_cue = _aggregate_cue(baseline_cases)
    candidate_cue = _aggregate_cue(cold_candidates)
    glossary_passed = (
        candidate_glossary["found"] >= baseline_glossary["found"]
        and candidate_glossary["missing"] <= baseline_glossary["missing"]
        and candidate_glossary["incorrect"] <= baseline_glossary["incorrect"]
    )
    cue_allowed = baseline_cue["error_rate"] + float(evaluation["cue_error_rate_delta_points"]) / 100
    cue_passed = candidate_cue["error_rate"] <= cue_allowed
    cer_passed = paired_median >= float(evaluation["paired_median_relative_cer_improvement"])
    transcript_reference_passed = all(
        case.get("displayed_transcript_content", {}).get("acceptance") == "operational_benchmark_reference"
        for case in transcript_audit["cases"]
    )
    raw_runs_ok = cold_identity["all_case_runs_ok"] and warm_identity["all_case_runs_ok"]
    reproducibility_passed = raw_runs_ok and output_stable
    evidence_passed = all(
        item["metrics_verified"]
        and item["argv_verified"]
        and item["output_schema_verified"]
        and item["measurement_verified"]
        for item in cold_evidence + warm_evidence
    )
    parity_passed = parity_validation.get("passed") is True
    technical_passed = cer_passed and glossary_passed and cue_passed and wall_passed and peak_passed and reproducibility_passed and evidence_passed and parity_passed
    model_meta = next(model for model in fixture["models"] if model["name"] == name)
    return {
        "name": name,
        "distribution_url": model_meta["distribution_url"],
        "model_fingerprint": cold_report["fingerprints"]["model"],
        "run_manifest_fingerprint": cold_report["manifest_fingerprint"],
        "settings_contract": {
            "binary_path": cold_report["whisper_runtime"]["binary_path"],
            "binary_fingerprint": cold_report["whisper_runtime"].get("binary_fingerprint"),
            "version": cold_report["whisper_runtime"]["version"],
            "settings": cold_report["whisper_runtime"]["settings"],
            "timeout_sec": cold_report["whisper_runtime"]["timeout_sec"],
            "output_schema": cold_report["whisper_runtime"]["output_schema"],
        },
        "runs": {
            "cold": {"report_path": str(cold_path), "cases": [case["cold"] for case in cases]},
            "warm": {"report_path": str(warm_path), "cases": [case["warm"] for case in cases]},
        },
        "quality": {
            "cases": cases,
            "paired_median_relative_cer_improvement": paired_median,
            "glossary": {
                "baseline": baseline_glossary,
                "candidate": candidate_glossary,
                "passed": glossary_passed,
            },
            "cue": {
                "baseline": baseline_cue,
                "candidate": candidate_cue,
                "allowed_candidate_rate": cue_allowed,
                "passed": cue_passed,
            },
            "output_stable_cold_warm": output_stable,
        },
        "raw_identity": {
            "cold": cold_identity,
            "warm": warm_identity,
            "run_manifest_fingerprint_equal": cold_identity["run_manifest_fingerprint"] == warm_identity["run_manifest_fingerprint"],
            "input_fingerprints_equal": cold_identity["inputs"] == warm_identity["inputs"],
            "runtime_identity_equal": cold_identity["runtime_identity"] == warm_identity["runtime_identity"],
            "evidence": {"cold": cold_evidence, "warm": warm_evidence},
            "metrics_verified": evidence_passed,
            "output_fingerprints_verified": all(
                item["output_fingerprint_verified"] and item["output_path_identity_verified"]
                for item in cold_evidence + warm_evidence
            ),
            "all_case_runs_ok": raw_runs_ok,
        },
        "gates": {
            "quality": {
                "relative_cer": {"value": paired_median, "threshold": evaluation["paired_median_relative_cer_improvement"], "passed": cer_passed},
                "glossary_exact_match": {"passed": glossary_passed},
                "cue_missing_duplicate": {"passed": cue_passed},
            },
            "wall_time": {"checks": wall_checks, "passed": wall_passed},
            "peak_memory": {"budget_bytes": evaluation["peak_memory_bytes"], "values_bytes": peak_values, "passed": peak_passed},
            "reproducibility": {
                "raw_case_runs_ok": raw_runs_ok,
                "cold_warm_output_sha_equal": output_stable,
                "passed": reproducibility_passed,
                "numeric_threshold_changed": False,
                "definition": "raw case runs must succeed and cold/warm JSON output SHA-256 must match; this is an integrity check, not a numeric threshold",
            },
            "raw_evidence": {
                "passed": evidence_passed,
                "metrics_recomputed_from_artifacts": evidence_passed,
                "argv_and_output_schema_verified": evidence_passed,
                "definition": "raw baseline VTT and candidate full JSON are rehashed/reparsed and CER, glossary, cue, text, range, argv, run-kind and output schema are recomputed or matched",
            },
            "vtt_progressive_parity": {
                "passed": parity_passed,
                "case_count": parity_validation.get("case_count"),
                "text_sequence_equal_verified": parity_validation.get("text_sequence_equal_verified") is True,
            },
            "fixture_exact_gold": {
                "namespace": "fixture_benchmark_quality",
                "status": "unverified_provisional",
                "passed": False,
                "required_for_benchmark_quality": True,
                "required_for_operational_go": False,
                "definition": "fixture exact gold is required for benchmark-quality certification but is not required for operational transcript reference Go",
            },
            "transcript_operational_reference": {
                "status": "accepted_operational_benchmark_reference",
                "passed": transcript_reference_passed,
                "audit_fingerprint": transcript_audit["audit_fingerprint"],
                "definition": "all four displayed provisional transcripts were human reviewed with no material issue reported; this is operational evidence, not exact gold",
            },
            "technical_gates_passed": technical_passed,
            "effective_gates_passed": technical_passed and transcript_reference_passed,
            "go": technical_passed and transcript_reference_passed,
            "status": "go" if technical_passed and transcript_reference_passed else "no_go",
            "reasons": []
            if technical_passed and transcript_reference_passed
            else (
                [{"code": "transcript_operational_reference_failed", "message": "4 case の operational transcript reference がそろっていません。"}]
                if technical_passed
                else [{"code": "technical_gate_failed", "message": "技術 gate のいずれかが未達です。"}]
            ),
            "source_gate_report": {"status": gate.get("status"), "reasons": gate.get("reasons", [])},
        },
    }


def _representative_cases(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for case in fixture["cases"]:
        gold = case["gold"]
        result.append(
            {
                "case_id": case["id"],
                "video_id": case["video_id"],
                "source_url": case["source_url"],
                "candidate_id": case["candidate_id"],
                "range_ms": case["range_ms"],
                "selection_basis": case["selection_basis"],
                "audio_fixture": case["audio_fixture"],
                "gold_audit_status": gold["audit_status"],
                "gold_basis": gold["basis"],
                "glossary": gold["glossary"],
                "gold_cue_anchors_ms": gold["cue_anchors_ms"],
            }
        )
    return result


def _load_boundary_audit(path: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    value = _read_json(path)
    result = evaluate_boundary_audit(
        value,
        expected_base_fixture_fingerprint=manifest_fingerprint(fixture),
        expected_benchmark_id=fixture["benchmark_id"],
    )
    result["artifact"] = str(path)
    return result


def _load_transcript_audit(
    path: Path,
    fixture: Mapping[str, Any],
    boundary_audit: Mapping[str, Any],
) -> dict[str, Any]:
    value = _read_json(path)
    try:
        result = validate_human_audit(
            value,
            expected_base_fixture_fingerprint=manifest_fingerprint(fixture),
            expected_boundary_audit_fingerprint=str(boundary_audit["fingerprint"]),
            expected_benchmark_id=str(fixture["benchmark_id"]),
        )
    except HumanAuditError as exc:
        raise ValueError(f"transcript audit artifact の strict schema 検証に失敗しました: {exc.message}") from exc
    result["artifact"] = str(path)
    return result


def _all_runs(model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for run_kind in ("cold", "warm"):
        for case in model["quality"]["cases"]:
            result.append(case[run_kind])
    return result


def _model_selection_key(model: Mapping[str, Any]) -> tuple[Any, ...]:
    """事前宣言した、技術 gate 通過候補の deterministic tie-break。"""

    runs = _all_runs(model)
    wall_values = [float(run["wall_time_ms"]) for run in runs if run.get("wall_time_ms") is not None]
    case_median_wall_values = [
        median(
            float(run["wall_time_ms"])
            for run in (case["cold"], case["warm"])
            if run.get("wall_time_ms") is not None
        )
        for case in model["quality"]["cases"]
    ]
    memory_values = [int(run["peak_memory_bytes"]) for run in runs if run.get("peak_memory_bytes") is not None]
    case_quality = [float(case["relative_cer_improvement"]) for case in model["quality"]["cases"]]
    fingerprint = model["model_fingerprint"]
    return (
        0 if _is_local_candidate(model) else 1,
        max(case_median_wall_values),
        median(wall_values),
        max(wall_values),
        max(memory_values),
        int(fingerprint["bytes"]),
        -min(case_quality),
        -float(model["quality"]["paired_median_relative_cer_improvement"]),
        str(model["name"]),
    )


def _is_local_candidate(model: Mapping[str, Any]) -> bool:
    fingerprint = model["model_fingerprint"]
    return model["settings_contract"]["binary_path"].startswith("/") and fingerprint["path"].startswith("/")


def _select_adopted_model(models: list[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    eligible = [
        model
        for model in models
        if model["gates"]["effective_gates_passed"] and _is_local_candidate(model)
    ]
    ranked = sorted(
        (
            {
                "name": model["name"],
                "key": list(_model_selection_key(model)),
                "eligible": True,
            }
            for model in eligible
        ),
        key=lambda item: tuple(item["key"]),
    )
    selected_name = ranked[0]["name"] if ranked else None
    selected = next((dict(model) for model in models if model["name"] == selected_name), None)
    return selected, {
        "rule": {
            "declared_before_audit_apply_rerun": True,
            "prior_provisional_results_known": True,
            "policy_basis": "user_wait_time_and_local_constraints",
            "mode": "lexicographic",
            "eligibility": "all fixed numeric gates and operational transcript reference gate must pass; candidates must use local absolute model and whisper-cli paths",
            "ordered_keys": [
                "local_absolute_runtime_and_model",
                "max_case_median_wall_time_ms",
                "median_wall_time_ms_all_cold_warm_runs",
                "max_wall_time_ms_all_cold_warm_runs",
                "max_peak_memory_bytes_all_cold_warm_runs",
                "model_bytes",
                "worst_case_relative_cer_improvement_descending",
                "paired_median_relative_cer_improvement_descending",
                "model_name_ascending",
            ],
            "not_a_threshold_change": True,
        },
        "ranking": ranked,
        "selected_model": selected_name,
    }


def _build_evaluation_gate_contract(
    *,
    fixture_gold_status: str,
    benchmark_thresholds: Mapping[str, Any],
    models: list[Mapping[str, Any]],
    adopted_model: Mapping[str, Any] | None,
    operational_passed: bool,
) -> dict[str, Any]:
    technical_passed = all(model["gates"]["technical_gates_passed"] for model in models)
    transcript_reference_passed = all(
        model["gates"]["transcript_operational_reference"]["passed"] for model in models
    )
    benchmark_gate = {
        "namespace": "fixture_benchmark_quality",
        "validator": "validate_fixture_benchmark_quality_gate_v1",
        "source": "docs/benchmarks/s9-1-cases.json:gates",
        "thresholds": {
            key: value for key, value in benchmark_thresholds.items() if key != "require_gold_audit"
        },
        "fixture_exact_gold": {
            "namespace": "fixture_benchmark_quality",
            "status": fixture_gold_status,
            "required_for_benchmark_quality": True,
            "passed": fixture_gold_status == "audited",
        },
        "numeric_and_artifact_gate_passed": technical_passed,
        "passed": technical_passed and fixture_gold_status == "audited",
        "definition": "fixture quality certification requires exact human-audited gold plus fixed numeric and artifact gates",
    }
    operational_gate = {
        "namespace": OPERATIONAL_REFERENCE_MODE,
        "validator": "validate_effective_operational_gate_v1",
        "fixture_exact_gold": {
            "namespace": "fixture_benchmark_quality",
            "status": fixture_gold_status,
            "required_for_benchmark_quality": True,
            "required_for_operational_go": False,
            "passed": fixture_gold_status == "audited",
        },
        "technical_gate_passed": technical_passed,
        "transcript_reference_gate_passed": transcript_reference_passed,
        "boundary_automation_adopted": False,
        "human_review_required": True,
        "selected_model": adopted_model["name"] if adopted_model else None,
        "passed": operational_passed,
        "definition": "operational transcript reference Go requires fixed technical gates and the four-case human operational reference, but not fixture exact gold",
    }
    return {
        "schema": EVALUATION_CONTRACT_SCHEMA,
        "benchmark_quality_gate": benchmark_gate,
        "effective_operational_gate": operational_gate,
    }


def _validate_evaluation_gate_contract(
    contract: Mapping[str, Any],
    *,
    expected_decision_go: bool | None = None,
) -> dict[str, Any]:
    """Validate explicit benchmark/effective gate namespaces; reject legacy ambiguity."""

    if contract.get("schema") != EVALUATION_CONTRACT_SCHEMA:
        raise ValueError("evaluation contract schema が固定値と異なります")
    if "gates" in contract or "require_gold_audit" in json.dumps(contract, ensure_ascii=False):
        raise ValueError("legacy evaluation gate key は許可されません")
    benchmark = contract.get("benchmark_quality_gate")
    operational = contract.get("effective_operational_gate")
    if not isinstance(benchmark, Mapping) or not isinstance(operational, Mapping):
        raise ValueError("benchmark/effective gate namespace が不足しています")
    if benchmark.get("namespace") != "fixture_benchmark_quality":
        raise ValueError("benchmark quality gate namespace が不正です")
    if benchmark.get("validator") != "validate_fixture_benchmark_quality_gate_v1":
        raise ValueError("benchmark quality gate validator が不正です")
    if benchmark.get("source") != "docs/benchmarks/s9-1-cases.json:gates" or not isinstance(benchmark.get("thresholds"), Mapping):
        raise ValueError("benchmark quality gate の source/thresholds が不正です")
    benchmark_gold = benchmark.get("fixture_exact_gold")
    if not isinstance(benchmark_gold, Mapping):
        raise ValueError("benchmark quality gate の fixture_exact_gold がありません")
    if benchmark_gold.get("namespace") != "fixture_benchmark_quality" or benchmark_gold.get("required_for_benchmark_quality") is not True:
        raise ValueError("fixture exact gold の benchmark requirement が不正です")
    expected_benchmark = (
        benchmark.get("numeric_and_artifact_gate_passed") is True and benchmark_gold.get("passed") is True
    )
    if not isinstance(benchmark.get("passed"), bool) or benchmark.get("passed") != expected_benchmark:
        raise ValueError("benchmark quality gate の passed が構成要素と一致しません")
    if operational.get("namespace") != OPERATIONAL_REFERENCE_MODE:
        raise ValueError("effective operational gate namespace が不正です")
    if operational.get("validator") != "validate_effective_operational_gate_v1":
        raise ValueError("effective operational gate validator が不正です")
    operational_gold = operational.get("fixture_exact_gold")
    if not isinstance(operational_gold, Mapping):
        raise ValueError("effective operational gate の fixture_exact_gold がありません")
    if operational_gold.get("namespace") != "fixture_benchmark_quality":
        raise ValueError("effective operational gate の gold namespace が不正です")
    if operational_gold.get("required_for_benchmark_quality") is not True:
        raise ValueError("effective operational gate が benchmark gold requirement を失っています")
    if operational_gold.get("required_for_operational_go") is not False:
        raise ValueError("fixture exact gold を operational Go の必須条件へ昇格できません")
    expected_operational = (
        operational.get("technical_gate_passed") is True
        and operational.get("transcript_reference_gate_passed") is True
        and operational.get("boundary_automation_adopted") is False
        and operational.get("human_review_required") is True
    )
    if not isinstance(operational.get("passed"), bool) or operational.get("passed") != expected_operational:
        raise ValueError("effective operational gate の passed が構成要素と一致しません")
    if expected_decision_go is not None and operational.get("passed") != expected_decision_go:
        raise ValueError("effective operational gate と decision.go が一致しません")
    return {
        "benchmark_quality_passed": benchmark["passed"],
        "effective_operational_passed": operational["passed"],
    }


def build_comparison(
    *,
    manifest_path: Path,
    run_paths: Mapping[str, Mapping[str, Path]],
    production_before: Path,
    production_after: Path,
    parity_path: Path,
    boundary_audit_path: Path | None = None,
    transcript_audit_path: Path | None = None,
) -> dict[str, Any]:
    fixture = _read_json(manifest_path)
    if boundary_audit_path is None:
        raise ValueError("boundary audit artifact is required for the canonical S9-1 comparison report")
    boundary_audit = _load_boundary_audit(boundary_audit_path, fixture)
    if transcript_audit_path is None:
        raise ValueError("transcript audit artifact is required for the operational S9-1 comparison report")
    transcript_audit = _load_transcript_audit(transcript_audit_path, fixture, boundary_audit)
    production_scope = _derive_production_scope(fixture)
    before = _validate_production_hash_artifact(production_before, expected_scope=production_scope)
    after = _validate_production_hash_artifact(production_after, expected_scope=production_scope)
    expected_inputs, expected_vtt_inputs, expected_vtt_paths = _expected_source_inputs(
        fixture,
        production_scope=production_scope,
    )
    parity_validation = _validate_vtt_parity_artifact(
        parity_path,
        fixture=fixture,
        expected_vtt_inputs=expected_vtt_inputs,
        expected_vtt_paths=expected_vtt_paths,
    )
    binary_path = Path(str(fixture["whisper"]["binary"]))
    binary_fingerprint = file_fingerprint(binary_path)
    expected_runtime_identity = _expected_runtime_identity(fixture, binary_fingerprint)
    models: list[dict[str, Any]] = []
    shared_raw_identity: Mapping[str, Any] | None = None
    for name in MODEL_NAMES:
        model_fixture = next(model for model in fixture["models"] if model["name"] == name)
        model_path = Path(str(model_fixture["path"]))
        model_fingerprint = file_fingerprint(
            model_path,
            expected_sha256=model_fixture["sha256"],
            expected_bytes=model_fixture["bytes"],
        )
        model_report = _model_report(
            name=name,
            fixture=fixture,
            cold_path=run_paths[name]["cold"],
            warm_path=run_paths[name]["warm"],
            transcript_audit=transcript_audit,
            expected_inputs=expected_inputs,
            expected_vtt_inputs=expected_vtt_inputs,
            expected_vtt_paths=expected_vtt_paths,
            expected_model_fingerprint=model_fingerprint,
            expected_runtime_identity=expected_runtime_identity,
            parity_validation=parity_validation,
            shared_raw_identity=shared_raw_identity,
        )
        if shared_raw_identity is None:
            shared_raw_identity = model_report["raw_identity"]["cold"]
        models.append(model_report)
    if shared_raw_identity is None:
        raise ValueError("raw report identity がありません")
    source_inputs = expected_inputs
    medians = {model["name"]: model["quality"]["paired_median_relative_cer_improvement"] for model in models}
    runtime_report = shared_raw_identity["runtime_report"]
    shared_runtime_identity = shared_raw_identity["runtime_identity"]
    evaluation = fixture["gates"]
    adopted_model, model_selection = _select_adopted_model(models)
    all_effective_gates_passed = adopted_model is not None
    evaluation_gate_contract = _build_evaluation_gate_contract(
        fixture_gold_status="unverified_provisional",
        benchmark_thresholds=evaluation,
        models=models,
        adopted_model=adopted_model,
        operational_passed=all_effective_gates_passed,
    )
    decision_reason = (
        "ユーザーの自然文監査「4本とも文字起こしは概ね問題なし」を、4 case の displayed transcript content に限る operational benchmark reference として採用した。"
        if all_effective_gates_passed
        else "4 case の operational transcript reference または固定 numeric gate が不足しているため採用しない。"
    )
    no_go_reasons: list[str] = [] if all_effective_gates_passed else ["operational_reference_or_technical_gate_failed"]
    residual_risks = [
        "gold は既存 VTT / transcript / ASS / cutplan を文脈利用した仮作成で、音声の独立人手監査をしていない。",
        "cold は OS page cache を消去した完全 cold ではなく、各 model の cold wave と warm wave を分離した reuse 観測である。",
        "mKwn / CGal / hPe は公開 YouTube の audio-only span 取得で、client / network 条件差が残る。",
        "candidate cue は raw のまま評価し、rolling VTT dedupe を候補へ適用していない。",
        "モデルは Git 管理外の手動 cache にあり、production 自動 download は実装していない。",
    ]
    decision_reason += " 文字・句読点 exactness、glossary 個別 exact approval、cue anchor の正確なミリ秒は主張しない。境界・発話連続性は部分監査のまま維持し、自動境界採用はせず人確認を必須にする。"
    residual_risks.extend(
        [
            "自然文の「概ね問題なし」は transcript content の operational reference であり、character / punctuation exactness への昇格ではない。",
            "glossary は個別表記の明示監査ではなく、cue anchor exact milliseconds も未承認である。",
            "境界監査は transcript / glossary / cue anchor exact times の承認ではなく、4 case の部分的な人手所見である。",
            "case 1 の pass は今回確認した境界・発話連続性で追加処置なしという意味だけで、全文品質や最終 short の品質承認ではない。",
            "case 2・3 の約6秒、case 4 の約2〜26秒は今回の観察メモであり、production の普遍的な秒数閾値ではない。",
            "親候補の固定 span と最終 short cutplan の品質は分離し、S9-4 / S9-6 では audio activity・cue・padding・human preview を併用する必要がある。",
        ]
    )
    report = {
        "schema": COMPARISON_REPORT_SCHEMA,
        "benchmark_id": fixture["benchmark_id"],
        "measurement_date": fixture["measurement_date"],
        "fixture_fingerprint": manifest_fingerprint(fixture),
        "gold_audit_status": "unverified_provisional",
        "metrics_status": "fixed_reference_operational_not_exact_gold",
        "transcript_reference_status": "accepted_operational_benchmark_reference",
        "comparison": {
            "model_selection": adopted_model["name"] if adopted_model else "none; No-Go",
            "paired_median_relative_cer_improvement": medians,
            "reference_interpretation": "CER、glossary、cue は固定 fixture reference に対する指標。human verified exact transcript とは記録しない。",
            "model_selection_contract": model_selection,
        },
        "decision": {
            "adopted_model": {
                "name": adopted_model["name"],
                "model_fingerprint": adopted_model["model_fingerprint"],
                "settings_contract": adopted_model["settings_contract"],
            }
            if adopted_model
            else None,
            "go": all_effective_gates_passed,
            "status": "go_operational_transcript_reference" if all_effective_gates_passed else "no_go",
            "decision_scope": "operational_transcript_reference_only",
            "reference_mode": OPERATIONAL_REFERENCE_MODE,
            "reason": decision_reason,
            "no_go_reasons": no_go_reasons,
            "s9_2_ready": all_effective_gates_passed,
            "s9_2_start_allowed": all_effective_gates_passed,
            "s9_2_start_scope": "TranscriptArtifact / resolver work may start; boundary automation remains prohibited and human review remains required.",
            "s9_3_reference": adopted_model["name"] if adopted_model else None,
            "boundary_automation": "not_adopted_human_review_required",
            "boundary_decision": {
                "status": "no_go",
                "automation_adopted": False,
                "human_review_required": True,
                "partial_audit_fingerprint": boundary_audit["fingerprint"],
                "reason": "existing partial boundary / speech continuity audit is preserved; it does not authorize automatic boundaries",
            },
            "exact_transcript_decision": {
                "status": "not_approved",
                "character_punctuation_exactness": "not_claimed",
                "glossary": "not_explicitly_audited",
                "cue_anchor_exact_ms": "unapproved",
            },
            "operational_transcript_decision": {
                "status": "go" if all_effective_gates_passed else "no_go",
                "accepted_as": "operational_benchmark_reference",
                "audit_fingerprint": transcript_audit["audit_fingerprint"],
                "reference_model": adopted_model["name"] if adopted_model else None,
            },
            "fallback_only": "runtime、cache、artifact、または人確認が失敗した場合は既存 YouTube VTT を明示的に fallback とする。",
        },
        "evaluation_contract": {
            **evaluation_gate_contract,
            "candidate_output_identity": {
                "validator": "validate_canonical_candidate_output_path_v1",
                "cache_root": str(Path(str(fixture["audio_cache_root"]))),
                "model_directories": dict(CANONICAL_MODEL_RUN_DIRECTORIES),
                "run_kind_directories": dict(CANONICAL_RUN_KIND_DIRECTORIES),
                "template": "runs/{model_directory}/{run_kind_directory}/whisper/{case_id}/{run_kind}.json",
            },
            "candidate_cues": "raw; progressive VTT dedupe is baseline-only",
            "normalization": fixture["normalization"],
            "cue_rule": fixture["cue_rule"],
            "decision_mode": OPERATIONAL_REFERENCE_MODE,
            "human_audit_dimensions": {
                "displayed_transcript_content": "human_reviewed_no_material_issue_reported / accepted_operational_benchmark_reference",
                "glossary": "not_explicitly_audited",
                "character_punctuation_exactness": "not_claimed",
                "cue_anchor_exact_ms": "unapproved",
                "boundary_editorial_outcomes": "preserved_partial_boundary_audit",
            },
            "exact_gold_policy": {
                "fixture_gold_status": "unverified_provisional",
                "exact_gold_gate": "not_claimed_and_not_required_for_operational_reference_mode",
                "numeric_thresholds_changed": False,
                "cer_definition": "fixed-reference metric; not a claim of character-level human truth",
            },
            "raw_report_identity": {
                "source_fixture_fingerprint": manifest_fingerprint(fixture),
                "run_manifest_fingerprints": {
                    model["name"]: model["raw_identity"]["cold"]["run_manifest_fingerprint"]
                    for model in models
                },
                "all_models_share_input_fingerprints": all(
                    model["raw_identity"]["cold"]["inputs"] == source_inputs for model in models
                ),
                "all_models_share_runtime_identity": all(
                    model["raw_identity"]["cold"]["runtime_identity"] == shared_runtime_identity for model in models
                ),
                "all_ranges_match_fixture": True,
                "all_models_metrics_recomputed_from_artifacts": all(
                    model["raw_identity"]["metrics_verified"] for model in models
                ),
                "all_models_output_fingerprints_verified": all(
                    model["raw_identity"]["output_fingerprints_verified"] for model in models
                ),
                "case_run_count": sum(
                    len(model["runs"][run_kind]["cases"])
                    for model in models
                    for run_kind in ("cold", "warm")
                ),
            },
            "boundary_policy": boundary_audit["policy"],
            "vtt_progressive_parity_gate": parity_validation,
        },
        "human_audit": transcript_audit,
        "model_selection": model_selection,
        "representative_cases": _representative_cases(fixture),
        "models": models,
        "source_fingerprints": source_inputs,
        "production_integrity": {
            "before_report": str(production_before),
            "after_report": str(production_after),
            "checked_file_count": len(before["files"]),
            "actual_recheck": before["actual_recheck"] and after["actual_recheck"],
            "unchanged": before["files"] == after["files"] and before["actual_files"] == after["actual_files"],
            "files": before["files"],
            "scope": {
                "root": str(production_scope["root"]),
                "expected_relative_files": sorted(production_scope["expected_relative_paths"]),
                "fixture_source_file_count": production_scope["fixture_source_file_count"],
                "protected_file_count": production_scope["protected_file_count"],
                "expected_file_count": len(production_scope["expected_relative_paths"]),
                "exact_file_set": before["scope_validation"]["exact_file_set"] and after["scope_validation"]["exact_file_set"],
                "path_validation": {
                    "root_matches_fixture": before["scope_validation"]["root_matches_fixture"] and after["scope_validation"]["root_matches_fixture"],
                    "path_traversal_rejected": True,
                    "symlink_escape_rejected": True,
                },
            },
        },
        "vtt_progressive_parity": {
            "artifact": str(parity_path),
            "case_count": parity_validation["case_count"],
            "all_text_sequence_equal": parity_validation["text_sequence_equal_verified"],
            "validation": parity_validation,
            "cases": parity_validation["cases"],
        },
        "runtime": {
            "host": {"cpu": "Apple M4 Pro", "memory_bytes": 68719476736, "arch": "arm64"},
            "whisper_runtime": runtime_report,
            "ffmpeg": {"path": "/opt/homebrew/Cellar/ffmpeg-full/8.1.2_2/bin/ffmpeg", "version": "8.1.2"},
            "yt_dlp": {"version": "2026.07.04", "mode": "public audio-only span; no video archive"},
        },
        "raw_reports": {name: {kind: str(path) for kind, path in paths.items()} for name, paths in run_paths.items()},
        "raw_report_identity": {
            "source_fixture_fingerprint": manifest_fingerprint(fixture),
            "run_manifest_fingerprints": {
                model["name"]: model["raw_identity"]["cold"]["run_manifest_fingerprint"]
                for model in models
            },
            "input_fingerprints_verified": all(
                model["raw_identity"]["cold"]["inputs"] == source_inputs
                and model["raw_identity"]["warm"]["inputs"] == source_inputs
                for model in models
            ),
            "runtime_identity_verified": all(
                model["raw_identity"]["cold"]["runtime_identity"] == shared_runtime_identity
                and model["raw_identity"]["warm"]["runtime_identity"] == shared_runtime_identity
                for model in models
            ),
            "metrics_recomputed_from_artifacts": all(
                model["raw_identity"]["metrics_verified"] for model in models
            ),
            "output_fingerprints_verified": all(
                model["raw_identity"]["output_fingerprints_verified"] for model in models
            ),
            "argv_verified": all(
                all(item["argv_verified"] for kind in ("cold", "warm") for item in model["raw_identity"]["evidence"][kind])
                for model in models
            ),
            "all_case_runs_ok": all(model["raw_identity"]["all_case_runs_ok"] for model in models),
            "case_run_count": sum(
                len(model["runs"][run_kind]["cases"])
                for model in models
                for run_kind in ("cold", "warm")
            ),
        },
        "reproduction": {
            "shell": False,
            "cache_root": "/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark",
            "model_cache_root": "/Users/ryukouokumura/Library/Caches/whisper.cpp/models",
            "run_count": sum(
                len(model["runs"][run_kind]["cases"])
                for model in models
                for run_kind in ("cold", "warm")
            ),
            "successful_case_runs": sum(
                sum(1 for case in model["runs"][run_kind]["cases"] if case.get("status") == "ok")
                for model in models
                for run_kind in ("cold", "warm")
            ),
            "warm_interpretation": "second process invocation with the same audio/model/settings; persistent artifact cache hit is not measured or claimed",
            "commands": [
                f"uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name {name} --output-dir {run_paths[name]['cold'].parent} --report {run_paths[name]['cold']} --execute-whisper --run-kind cold"
                for name in MODEL_NAMES
            ]
            + [
                f"uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name {name} --output-dir {run_paths[name]['warm'].parent} --report {run_paths[name]['warm']} --execute-whisper --run-kind warm"
                for name in MODEL_NAMES
            ]
            + [
                "uv run python benchmarks/s9_compare.py --manifest docs/benchmarks/s9-1-cases.json --boundary-audit docs/benchmarks/s9-1-boundary-audit.json --transcript-audit docs/benchmarks/s9-1-human-audit-v2.json --q5-cold ... --q5-warm ... --turbo-cold ... --turbo-warm ... --output-json docs/benchmarks/s9-1-report.json --output-md docs/benchmarks/s9-1-report.md"
            ],
        },
        "residual_risks": residual_risks,
    }
    report["boundary_audit"] = boundary_audit
    _validate_evaluation_gate_contract(
        report["evaluation_contract"],
        expected_decision_go=report["decision"]["go"],
    )
    return report


def markdown_report(report: Mapping[str, Any]) -> str:
    decision = report["decision"]
    selected = decision.get("adopted_model")
    selected_name = selected["name"] if isinstance(selected, Mapping) else "なし"
    human_audit = report["human_audit"]
    lines = [
        "# S9-1 代表素材 benchmark report",
        "",
        f"測定日: {report['measurement_date']}",
        f"fixture fingerprint: `{report['fixture_fingerprint']}`",
        f"human audit fingerprint: `{human_audit['audit_fingerprint']}`",
        "",
        "## 判定",
        "",
        f"{('Go' if decision['go'] else 'No-Go')}。decision mode は operational transcript reference、採用モデルは `{selected_name}`。",
        "",
        "ユーザー原文「4本とも文字起こしは概ね問題なし」は、表示 transcript content の運用上の reference としてのみ採用した。human verified exact transcript とは記録しない。",
        "fixture gold の `gold_audit_status` は `unverified_provisional` のまま。glossary の個別 exact approval、文字・句読点 exactness、cue anchor の正確なミリ秒は未承認・未主張のまま。",
        "operational transcript reference は Go だが、boundary automation は No-Go / 不採用で、人の preview / 区間確認を必須とする。S9-2 start allowed は TranscriptArtifact / resolver の着手範囲だけを示す。",
        "",
        "## 人手監査の次元分離",
        "",
        f"- 原文: `{human_audit['source']['exact_quote']}`",
        "- displayed transcript content: human reviewed / no material issue reported / operational benchmark reference",
        "- glossary: not explicitly audited",
        "- character and punctuation exactness: not claimed",
        "- cue anchor exact milliseconds: unapproved",
        "- boundary/editorial outcomes: existing partial audit preserved",
        "",
    ]
    boundary_audit = report.get("boundary_audit")
    if isinstance(boundary_audit, Mapping):
        lines += [
            "## 境界・発話連続性の部分監査",
            "",
            f"監査者: {boundary_audit['auditor']} / 監査日: {boundary_audit['audit_date']}",
            f"boundary audit fingerprint: `{boundary_audit['fingerprint']}`",
            f"base fixture fingerprint: `{boundary_audit['base_fixture_fingerprint']}`（既存4音声の fixture fingerprint は変更していない）",
            "",
            "この証跡は開始境界と発話連続性だけの部分監査であり、transcript 全文、glossary、cue anchor の正確な時刻を audited にはしない。背景音は意味ある発話として数えず、単純な onset-only gate と Whisper timestamp 単独の境界確定は採用しない。",
            "case 1 の `pass` は、今回確認した境界・発話連続性で追加処置なしという意味だけであり、全文品質・glossary・cue anchor・最終 short の品質承認ではない。",
            "",
            "| 前回表示順 | case ID | 観察 | 期待 editorial outcome |",
            "|---:|---|---|---|",
        ]
        for case in boundary_audit["cases"]:
            lines.append(
                f"| {case['display_order']} | {case['case_id']} | {case['source_feedback']} | {case['expected_editorial_outcome']} |"
            )
        lines += [
            "",
            "S9-1 はこの部分監査を境界自動化の採用根拠にはしない。S9-4 / S9-6 は親候補の固定音声 span を機械的に確定せず、最終 short cutplan / preview で opening trim または内部 gap removal / review を人確認し、audio activity・cue・padding・human preview を併用する。今回の約時刻は観察メモであり、production の普遍的な秒数閾値ではない。",
            "",
        ]
    lines += [
        "## 代表素材",
        "",
        "| case | video / candidate | range | 選定理由 |",
        "|---|---|---:|---|",
    ]
    for case in report["representative_cases"]:
        lines.append(
            f"| {case['case_id']} | {case['video_id']} / {case['candidate_id']} | {case['range_ms'][0]}–{case['range_ms'][1]} ms | {case['selection_basis']} |"
        )
    lines += [
        "",
        "## 比較結果",
        "",
        "| model | case | baseline CER | candidate CER | relative improvement | glossary found | cue rate baseline → candidate | cold ms | warm ms | peak RSS max |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in report["models"]:
        for case in model["quality"]["cases"]:
            baseline = case["baseline"]
            cold = case["cold"]
            lines.append(
                f"| {model['name']} | {case['case_id']} | {baseline['cer']:.6f} | {cold['cer']:.6f} | {case['relative_cer_improvement']:.2%} | {cold['glossary']['found']} | {baseline['cue']['error_rate']:.2f} → {cold['cue']['error_rate']:.2f} | {cold['wall_time_ms']} | {case['warm']['wall_time_ms']} | {max(cold['peak_memory_bytes'], case['warm']['peak_memory_bytes'])} |"
            )
        quality = model["quality"]
        peak = max(item["peak_memory_bytes"] for case in quality["cases"] for item in (case["cold"], case["warm"]))
        lines.append(
            f"| **{model['name']} median** | 4 case | — | — | **{quality['paired_median_relative_cer_improvement']:.2%}** | {quality['glossary']['candidate']['found']} / {quality['glossary']['baseline']['found']} | gate pass | — | — | {peak} |"
        )
    lines += [
        "",
        "## 実行条件と証跡",
        "",
        "- host: Apple M4 Pro / 64 GB / arm64",
        "- whisper.cpp: 1.9.1、Metal、ja、threads 8、processors 1、temperature 0、beam size 5、best-of 5、padding 0、full JSON、timeout 180 秒",
        "- model cache: `/Users/ryukouokumura/Library/Caches/whisper.cpp/models/`",
        "- audio cache: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/`",
        "- baseline: production progressive dedupe parity 4/4。candidate: raw cue のまま評価",
        "- gate namespaces: `benchmark_quality_gate` は fixture exact gold を品質認定の必須条件として未達、`effective_operational_gate` は numeric / artifact gate と4 case operational transcript referenceでGo。fixture exact goldは operational Goの必須条件ではなく、boundary automationは不採用、人確認を必須とする。",
        "- candidate output identity: canonical cache root、model / run-kind directory、case、full JSON output path、path confinement、symlink拒否、実体 fingerprintを `validate_canonical_candidate_output_path_v1` で検証。",
        "- production hash scope: fixture source_files 14件 + protected cut_clip_003 1件 = exact 15件。root、relative path、完全な file set、path traversal、symlink escape、実ファイル bytes / SHA-256 を before / after とも fail-closed に再検証し、既存 `ja.vtt` と mp4 は非変更。",
        f"- raw evidence: model / audio / baseline VTT / whisper-cli の実体 bytes / SHA-256、full JSON の再parse、CER / glossary / cue 指標の再計算、argv / range / run-kind / output schema / candidate text / output fingerprint、stderr の real time / peak RSS を再検証。case runs は {report['reproduction']['successful_case_runs']} / {report['reproduction']['run_count']} 成功。",
        "- cold / warm output SHA equality は全 case で確認済み。warm は別 process invocation の再利用観測で、永続 artifact cache hit は計測・主張していない。",
        "- tie-break metadata: audit-apply 再計測前に固定。prior provisional results known。policy basis は user_wait_time_and_local_constraints。全結果を見る前に宣言したとは主張せず、pass 閾値の変更でもない。",
        f"- selected model: `{selected_name}`。tie-break は local-only、worst-case 待ち時間、全体待ち時間、peak memory、model bytes、per-case quality の lexicographic rule。",
        "- VTT progressive parity: strict v2 artifactを benchmark / fixture identity、固定4 case順、source VTT bytes / SHA-256、raw / dedup count、text sequence SHA-256から再計算し、4/4 case `text_sequence_equal=true` を effective Go gateへ含めた。",
        "",
        "## 再現 command",
        "",
    ]
    lines.extend(f"- `{command}`" for command in report["reproduction"]["commands"])
    lines += [
        "",
        "## 残余リスク",
        "",
    ]
    lines.extend(f"- {risk}" for risk in report["residual_risks"])
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S9-1 raw benchmark report comparator")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--q5-cold", required=True, type=Path)
    parser.add_argument("--q5-warm", required=True, type=Path)
    parser.add_argument("--turbo-cold", required=True, type=Path)
    parser.add_argument("--turbo-warm", required=True, type=Path)
    parser.add_argument("--production-before", required=True, type=Path)
    parser.add_argument("--production-after", required=True, type=Path)
    parser.add_argument("--parity", required=True, type=Path)
    parser.add_argument("--boundary-audit", required=True, type=Path)
    parser.add_argument("--transcript-audit", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_paths = {
        "ggml-large-v3-turbo-q5_0": {"cold": args.q5_cold, "warm": args.q5_warm},
        "ggml-large-v3-turbo": {"cold": args.turbo_cold, "warm": args.turbo_warm},
    }
    report = build_comparison(
        manifest_path=args.manifest,
        run_paths=run_paths,
        production_before=args.production_before,
        production_after=args.production_after,
        parity_path=args.parity,
        boundary_audit_path=args.boundary_audit,
        transcript_audit_path=args.transcript_audit,
    )
    report["reproduction"]["commands"][-1] = (
        "uv run python benchmarks/s9_compare.py"
        f" --manifest {args.manifest}"
        f" --q5-cold {args.q5_cold} --q5-warm {args.q5_warm}"
        f" --turbo-cold {args.turbo_cold} --turbo-warm {args.turbo_warm}"
        f" --production-before {args.production_before} --production-after {args.production_after}"
        f" --parity {args.parity}"
        + f" --boundary-audit {args.boundary_audit}"
        + f" --transcript-audit {args.transcript_audit}"
        + f" --output-json {args.output_json} --output-md {args.output_md}"
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "fixture_fingerprint": report["fixture_fingerprint"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
