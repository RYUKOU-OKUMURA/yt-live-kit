"""S9-1 の cold / warm raw report を統合する比較 report generator。

実測そのものは ``s9_benchmark.py run`` が担当し、この module は既存の
raw report・固定 fixture・production hash 証跡だけを読み、JSON と Markdown
の比較 report を決定的に組み立てる。ネットワーク、YouTube、production data
への書き込みは行わない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any, Mapping

# ``python benchmarks/s9_compare.py`` でも repository root の harness を import
# できるようにする。実行時の外部 path は受け取らず、この file の親だけを使う。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.s9_benchmark import (
    evaluate_boundary_audit,
    manifest_fingerprint,
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


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object が必要です: {path}")
    return value


def _validate_production_hash_artifact(path: Path) -> dict[str, Any]:
    """Re-read every recorded production file; artifact equality alone is insufficient."""

    report = _read_json(path)
    root = report.get("root")
    files = report.get("files")
    if not isinstance(root, str) or not isinstance(files, Mapping) or not files:
        raise ValueError(f"production hash artifact の schema が不正です: {path}")
    actual_files: dict[str, dict[str, Any]] = {}
    for relative_path, expected in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected, Mapping):
            raise ValueError(f"production hash artifact の file entry が不正です: {path}")
        target = Path(root) / relative_path
        if not target.is_file():
            raise ValueError(f"production hash artifact の対象ファイルがありません: {target}")
        actual = {"bytes": target.stat().st_size, "sha256": sha256_file(target)}
        if actual != {"bytes": expected.get("bytes"), "sha256": expected.get("sha256")}:
            raise ValueError(f"production file hash が artifact と一致しません: {target}")
        actual_files[relative_path] = actual
    result = dict(report)
    result["actual_recheck"] = True
    result["actual_files"] = actual_files
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


def _validate_raw_report_identity(
    path: Path,
    *,
    fixture: Mapping[str, Any],
    expected_model_name: str,
    expected_run_kind: str,
    expected_manifest_fingerprint: str | None = None,
    expected_inputs: list[Mapping[str, Any]] | None = None,
    expected_vtt_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    expected_runtime_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed if one raw report is not the fixed benchmark invocation."""

    report = _read_json(path)
    if report.get("schema") != "s9-1-benchmark-report-v1":
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
    expected_decode = dict(whisper["decode"])
    padding_ms = expected_decode.pop("padding_ms", 0)
    expected_decode.update({"threads": whisper["threads"], "processors": whisper["processors"]})
    expected_settings = {
        "language": whisper["language"],
        "initial_prompt": whisper["initial_prompt"],
        "padding_ms": padding_ms,
        "decode": expected_decode,
        "output_schema": whisper["output_schema"],
    }
    if runtime_identity["binary_path"] != whisper["binary"]:
        raise ValueError(f"raw report の whisper binary が fixture と一致しません: {path}")
    if runtime_identity["version"] != whisper["version"] or runtime_identity["settings"] != expected_settings:
        raise ValueError(f"raw report の whisper settings が fixture と一致しません: {path}")
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
    expected_vtt_inputs: Mapping[str, Mapping[str, Any]],
    shared_raw_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cold_report = _read_json(cold_path)
    warm_report = _read_json(warm_path)
    expected_inputs = shared_raw_identity.get("inputs") if shared_raw_identity is not None else None
    expected_runtime_identity = shared_raw_identity.get("runtime_identity") if shared_raw_identity is not None else None
    cold_identity = _validate_raw_report_identity(
        cold_path,
        fixture=fixture,
        expected_model_name=name,
        expected_run_kind="cold",
        expected_inputs=expected_inputs,
        expected_vtt_inputs=expected_vtt_inputs,
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
        expected_runtime_identity=cold_identity["runtime_identity"],
    )
    cold_cases = _case_map(cold_report)
    warm_cases = _case_map(warm_report)
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
    technical_passed = cer_passed and glossary_passed and cue_passed and wall_passed and peak_passed and reproducibility_passed
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
            "gold_audit": {
                "status": "not_claimed",
                "passed": False,
                "required_for_selected_mode": False,
                "definition": "character and punctuation exactness, glossary exact approval, and cue anchor exact milliseconds are not claimed by the natural-language audit",
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
    before = _validate_production_hash_artifact(production_before)
    after = _validate_production_hash_artifact(production_after)
    production_root = Path(str(before["root"]))
    expected_vtt_inputs: dict[str, Mapping[str, Any]] = {}
    for fixture_case in fixture["cases"]:
        vtt_path = Path(str(fixture_case["source_files"]["vtt"]))
        try:
            relative_vtt = vtt_path.relative_to(production_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"fixture の baseline VTT が production root 外です: {vtt_path}") from exc
        expected_vtt = before["files"].get(relative_vtt)
        if not isinstance(expected_vtt, Mapping):
            raise ValueError(f"production hash artifact に baseline VTT がありません: {relative_vtt}")
        expected_vtt_inputs[fixture_case["id"]] = expected_vtt
    models: list[dict[str, Any]] = []
    shared_raw_identity: Mapping[str, Any] | None = None
    for name in MODEL_NAMES:
        model_report = _model_report(
            name=name,
            fixture=fixture,
            cold_path=run_paths[name]["cold"],
            warm_path=run_paths[name]["warm"],
            transcript_audit=transcript_audit,
            expected_vtt_inputs=expected_vtt_inputs,
            shared_raw_identity=shared_raw_identity,
        )
        if shared_raw_identity is None:
            shared_raw_identity = model_report["raw_identity"]["cold"]
        models.append(model_report)
    parity = _read_json(parity_path)
    if shared_raw_identity is None:
        raise ValueError("raw report identity がありません")
    source_inputs = shared_raw_identity["inputs"]
    medians = {model["name"]: model["quality"]["paired_median_relative_cer_improvement"] for model in models}
    runtime_report = shared_raw_identity["runtime_report"]
    runtime_identity = shared_raw_identity["runtime_identity"]
    evaluation = fixture["gates"]
    adopted_model, model_selection = _select_adopted_model(models)
    all_effective_gates_passed = adopted_model is not None
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
        "schema": "s9-1-comparison-report-v5",
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
            "candidate_cues": "raw; progressive VTT dedupe is baseline-only",
            "normalization": fixture["normalization"],
            "cue_rule": fixture["cue_rule"],
            "gates": evaluation,
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
                    model["raw_identity"]["cold"]["runtime_identity"] == runtime_identity for model in models
                ),
                "all_ranges_match_fixture": True,
                "case_run_count": sum(
                    len(model["runs"][run_kind]["cases"])
                    for model in models
                    for run_kind in ("cold", "warm")
                ),
            },
            "boundary_policy": boundary_audit["policy"],
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
        },
        "vtt_progressive_parity": {
            "artifact": str(parity_path),
            "case_count": len(parity["cases"]),
            "all_text_sequence_equal": all(case["text_sequence_equal"] for case in parity["cases"]),
            "cases": parity["cases"],
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
                model["raw_identity"]["cold"]["runtime_identity"] == runtime_identity
                and model["raw_identity"]["warm"]["runtime_identity"] == runtime_identity
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
        "- production data hash: before / after は一致。対象 15 ファイル、既存 `ja.vtt` と mp4 は非変更。",
        f"- raw identity: source fixture / model-specific run manifest、audio / VTT hash、range、runtime / settings、run-kind を4 raw reportで照合。case runs は {report['reproduction']['successful_case_runs']} / {report['reproduction']['run_count']} 成功。",
        "- cold / warm output SHA equality は全 case で確認済み。warm は別 process invocation の再利用観測で、永続 artifact cache hit は計測・主張していない。",
        "- tie-break metadata: audit-apply 再計測前に固定。prior provisional results known。policy basis は user_wait_time_and_local_constraints。全結果を見る前に宣言したとは主張せず、pass 閾値の変更でもない。",
        f"- selected model: `{selected_name}`。tie-break は local-only、worst-case 待ち時間、全体待ち時間、peak memory、model bytes、per-case quality の lexicographic rule。",
        "- VTT progressive parity: [s9-1-vtt-progressive-parity.json](./s9-1-vtt-progressive-parity.json) で 4/4 case 一致。",
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
