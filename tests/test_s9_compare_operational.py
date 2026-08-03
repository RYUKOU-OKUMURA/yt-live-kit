from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmarks.s9_compare import (
    _load_transcript_audit,
    _select_adopted_model,
    _validate_production_hash_artifact,
    _validate_raw_report_identity,
)
from benchmarks.s9_benchmark import manifest_fingerprint


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs/benchmarks/s9-1-cases.json"
BOUNDARY_PATH = ROOT / "docs/benchmarks/s9-1-boundary-audit.json"
AUDIT_PATH = ROOT / "docs/benchmarks/s9-1-human-audit-v2.json"
REPORT_PATH = ROOT / "docs/benchmarks/s9-1-report.json"
PACKET_PATH = ROOT / "docs/benchmarks/s9-1-human-audit.md"
PROTOCOL_PATH = ROOT / "docs/benchmarks/s9-1-protocol.md"


def _minimal_raw_report(*, model_name: str = "ggml-large-v3-turbo-q5_0", run_kind: str = "cold") -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    canonical = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    model = next(item for item in canonical["models"] if item["name"] == model_name)
    cases = []
    for fixture_case in manifest["cases"]:
        start_ms, end_ms = fixture_case["range_ms"]
        anchors = [
            {"anchor_id": f"anchor-{index}", "start_ms": values[0], "end_ms": values[1]}
            for index, values in enumerate(fixture_case["gold"]["cue_anchors_ms"], 1)
        ]
        cases.append(
            {
                "case_id": fixture_case["id"],
                "target_range": {"start_ms": start_ms, "end_ms": end_ms},
                "gold_cue_anchors": anchors,
                "candidate": {
                    "status": "ok",
                    "run_kind": run_kind,
                    "execution": {"returncode": 0, "run_kind": run_kind},
                },
            }
        )
    return {
        "schema": "s9-1-benchmark-report-v1",
        "benchmark_id": manifest["benchmark_id"],
        "manifest_fingerprint": canonical["raw_report_identity"]["run_manifest_fingerprints"][model_name],
        "fingerprints": {
            "model": model["model_fingerprint"],
            "inputs": canonical["source_fingerprints"],
        },
        "whisper_runtime": canonical["runtime"]["whisper_runtime"],
        "gold_audit_status": "provisional",
        "metrics_status": "provisional",
        "cases": cases,
    }


def _model(
    name: str,
    *,
    case_wall: list[tuple[int, int]],
    memory: int,
    model_bytes: int,
    quality: list[float],
    local: bool = True,
) -> dict:
    cases = []
    for index, ((cold_wall, warm_wall), improvement) in enumerate(zip(case_wall, quality)):
        cases.append(
            {
                "case_id": f"case-{index}",
                "relative_cer_improvement": improvement,
                "cold": {"wall_time_ms": cold_wall, "peak_memory_bytes": memory},
                "warm": {"wall_time_ms": warm_wall, "peak_memory_bytes": memory},
            }
        )
    return {
        "name": name,
        "settings_contract": {"binary_path": "/bin/whisper-cli" if local else "whisper-cli"},
        "model_fingerprint": {"path": "/models/model.bin" if local else "model.bin", "bytes": model_bytes},
        "quality": {"cases": cases, "paired_median_relative_cer_improvement": 0.8},
        "gates": {"effective_gates_passed": True},
    }


def test_tie_break_is_deterministic_and_uses_worst_case_wait_before_size() -> None:
    slower_worst_case_but_smaller = _model(
        "small",
        case_wall=[(10, 11), (10, 11), (10, 11), (50, 51)],
        memory=10,
        model_bytes=10,
        quality=[0.9, 0.9, 0.9, 0.2],
    )
    faster_worst_case_but_larger = _model(
        "large",
        case_wall=[(10, 11), (10, 11), (10, 11), (40, 41)],
        memory=100,
        model_bytes=100,
        quality=[0.8, 0.8, 0.8, 0.1],
    )

    selected, contract = _select_adopted_model([slower_worst_case_but_smaller, faster_worst_case_but_larger])
    assert selected is not None
    assert selected["name"] == "large"
    assert contract["rule"]["declared_before_audit_apply_rerun"] is True
    assert contract["rule"]["prior_provisional_results_known"] is True
    assert contract["rule"]["policy_basis"] == "user_wait_time_and_local_constraints"
    assert contract["rule"]["not_a_threshold_change"] is True
    assert contract["rule"]["ordered_keys"][1] == "max_case_median_wall_time_ms"

    same_key_a = _model("a", case_wall=[(10, 10)] * 4, memory=10, model_bytes=10, quality=[0.8] * 4)
    same_key_b = _model("b", case_wall=[(10, 10)] * 4, memory=10, model_bytes=10, quality=[0.8] * 4)
    selected, _ = _select_adopted_model([same_key_b, same_key_a])
    assert selected is not None and selected["name"] == "a"


def test_non_local_candidate_is_not_eligible() -> None:
    candidate = _model(
        "non-local",
        case_wall=[(10, 10)] * 4,
        memory=10,
        model_bytes=10,
        quality=[0.8] * 4,
        local=False,
    )
    selected, contract = _select_adopted_model([candidate])
    assert selected is None
    assert contract["selected_model"] is None


def test_transcript_audit_is_bound_to_base_and_boundary_fingerprints() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    boundary = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    boundary["fingerprint"] = "0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1"
    audit = _load_transcript_audit(AUDIT_PATH, manifest, boundary)
    assert audit["audit_fingerprint"] == "9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263"
    assert audit["base_fixture_fingerprint"] == manifest_fingerprint(manifest)


def test_canonical_report_contains_sixteen_runs_and_separate_statuses() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["schema"] == "s9-1-comparison-report-v5"
    assert report["decision"]["go"] is True
    assert report["decision"]["s9_3_reference"] == "ggml-large-v3-turbo-q5_0"
    assert report["decision"]["boundary_automation"] == "not_adopted_human_review_required"
    assert report["decision"]["boundary_decision"]["status"] == "no_go"
    assert report["decision"]["operational_transcript_decision"]["status"] == "go"
    assert report["gold_audit_status"] == "unverified_provisional"
    assert report["transcript_reference_status"] == "accepted_operational_benchmark_reference"
    assert report["human_audit"]["review_policy"]["displayed_transcript_content"]["acceptance"] == "operational_benchmark_reference"
    assert report["evaluation_contract"]["raw_report_identity"]["all_models_share_runtime_identity"] is True
    assert report["raw_report_identity"]["runtime_identity_verified"] is True
    assert report["raw_report_identity"]["input_fingerprints_verified"] is True
    assert report["reproduction"]["run_count"] == 16
    assert report["reproduction"]["successful_case_runs"] == 16
    assert "declared_before_results" not in report["comparison"]["model_selection_contract"]["rule"]
    packet = PACKET_PATH.read_text(encoding="utf-8")
    assert "operational transcript reference の canonical decision（q5採用）" in packet
    assert "現在の provisional 指標と No-Go" not in packet
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8")
    assert "cold-audit-apply" in protocol
    assert "cold-final3" not in protocol

    run_count = 0
    for model in report["models"]:
        assert model["gates"]["technical_gates_passed"] is True
        assert model["gates"]["effective_gates_passed"] is True
        assert model["gates"]["transcript_operational_reference"]["passed"] is True
        assert model["gates"]["gold_audit"]["passed"] is False
        assert model["gates"]["gold_audit"]["required_for_selected_mode"] is False
        run_count += len(model["runs"]["cold"]["cases"])
        run_count += len(model["runs"]["warm"]["cases"])
    assert run_count == 16
    assert report["production_integrity"]["unchanged"] is True


def test_production_hash_artifact_rechecks_actual_files(tmp_path: Path) -> None:
    target = tmp_path / "production.txt"
    target.write_text("unchanged", encoding="utf-8")
    import hashlib

    artifact = {
        "root": str(tmp_path),
        "files": {
            "production.txt": {
                "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        },
    }
    artifact_path = tmp_path / "hash.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    checked = _validate_production_hash_artifact(artifact_path)
    assert checked["actual_recheck"] is True

    target.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="production file hash"):
        _validate_production_hash_artifact(artifact_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["fingerprints"]["model"].update(sha256="0" * 64), "model sha256"),
        (
            lambda value: next(
                item for item in value["fingerprints"]["inputs"] if item["kind"] == "audio"
            ).update(sha256="0" * 64),
            "audio identity",
        ),
        (
            lambda value: next(
                item for item in value["fingerprints"]["inputs"] if item["kind"] == "baseline_vtt"
            ).update(sha256="0" * 64),
            "baseline VTT identity",
        ),
        (lambda value: value["cases"][0]["candidate"].update(run_kind="warm"), "candidate run status"),
        (
            lambda value: value["whisper_runtime"]["settings"]["decode"].update(threads=7),
            "whisper settings",
        ),
        (lambda value: value["cases"][0]["target_range"].update(start_ms=2853161), "range / cue anchor identity"),
    ],
)
def test_raw_report_identity_mutations_fail_closed(tmp_path: Path, mutation, message: str) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    changed = _minimal_raw_report()
    expected_vtt_inputs = {
        item["case_id"]: item
        for item in changed["fingerprints"]["inputs"]
        if item["kind"] == "baseline_vtt"
    }
    # Apply the mutation to the serialized copy without touching the cached raw run.
    changed = deepcopy(changed)
    mutation(changed)
    changed_path = tmp_path / "raw-report.json"
    changed_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _validate_raw_report_identity(
            changed_path,
            fixture=manifest,
            expected_model_name="ggml-large-v3-turbo-q5_0",
            expected_run_kind="cold",
            expected_vtt_inputs=expected_vtt_inputs,
        )
