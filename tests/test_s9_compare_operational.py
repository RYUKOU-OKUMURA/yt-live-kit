from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmarks.s9_compare import (
    _canonical_run_root,
    _derive_production_scope,
    _expected_candidate_output_path,
    _normalization_config,
    _parse_real_time_ms,
    _expected_runtime_identity,
    _expected_whisper_settings,
    _load_transcript_audit,
    _select_adopted_model,
    _validate_evaluation_gate_contract,
    _validate_raw_case_evidence,
    _validate_production_hash_artifact,
    _validate_raw_report_identity,
    _validate_vtt_parity_artifact,
)
from benchmarks.s9_benchmark import (
    CueAnchor,
    TimeRange,
    _case_metrics,
    _exclude_marker_cues,
    build_whisper_argv,
    deduplicate_progressive_timed,
    file_fingerprint,
    manifest_fingerprint,
    parse_vtt_file,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs/benchmarks/s9-1-cases.json"
BOUNDARY_PATH = ROOT / "docs/benchmarks/s9-1-boundary-audit.json"
AUDIT_PATH = ROOT / "docs/benchmarks/s9-1-human-audit-v2.json"
REPORT_PATH = ROOT / "docs/benchmarks/s9-1-report.json"
PACKET_PATH = ROOT / "docs/benchmarks/s9-1-human-audit.md"
PROTOCOL_PATH = ROOT / "docs/benchmarks/s9-1-protocol.md"
PARITY_FIXTURE_PATH = ROOT / "tests/fixtures/s9_vtt_parity.vtt"


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


def _local_raw_evidence_fixture(tmp_path: Path) -> tuple[dict, dict, dict]:
    """Build a complete four-case raw artifact without model/audio cache access."""

    manifest = deepcopy(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    model_path = tmp_path / "model.bin"
    binary_path = tmp_path / "whisper-cli"
    model_path.write_bytes(b"local model fixture")
    binary_path.write_bytes(b"local whisper binary fixture")
    manifest["whisper"]["binary"] = str(binary_path)
    manifest["whisper"]["timeout_sec"] = 180.0
    model_fixture = next(item for item in manifest["models"] if item["name"] == "ggml-large-v3-turbo-q5_0")
    model_fixture.update(file_fingerprint(model_path))
    manifest["audio_cache_root"] = str(tmp_path / "cache")
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    expected_vtt = file_fingerprint(PARITY_FIXTURE_PATH)
    expected_inputs: list[dict] = []
    cases: list[dict] = []
    runtime_identity = _expected_runtime_identity(manifest, file_fingerprint(binary_path))
    settings = _expected_whisper_settings(manifest)
    normalization = _normalization_config(manifest)
    for fixture_case in manifest["cases"]:
        case_id = fixture_case["id"]
        audio_path = audio_root / f"{case_id}.wav"
        audio_path.write_bytes(f"audio:{case_id}".encode())
        audio_fingerprint = file_fingerprint(audio_path)
        fixture_case["audio_cache_root"] = str(audio_root)
        fixture_case["audio_fixture"] = audio_path.name
        fixture_case["audio_bytes"] = audio_fingerprint["bytes"]
        fixture_case["audio_sha256"] = audio_fingerprint["sha256"]
        fixture_case["source_files"]["vtt"] = str(PARITY_FIXTURE_PATH)
        expected_inputs.extend(
            [
                {"kind": "baseline_vtt", "case_id": case_id, **expected_vtt},
                {"kind": "audio", "case_id": case_id, **audio_fingerprint},
            ]
        )
        start_ms, end_ms = fixture_case["range_ms"]
        anchors = [
            {"anchor_id": f"anchor-{index}", "start_ms": values[0], "end_ms": values[1]}
            for index, values in enumerate(fixture_case["gold"]["cue_anchors_ms"], 1)
        ]
        output_root = _canonical_run_root(manifest, "ggml-large-v3-turbo-q5_0", "cold")
        output_path = _expected_candidate_output_path(output_root, case_id, "cold")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "systeminfo": "fixture",
            "model": {},
            "params": {"model": str(model_path), "language": "ja"},
            "result": {},
            "transcription": [
                {
                    "timestamps": {"from": "00:00:00,000", "to": "00:00:01,000"},
                    "offsets": {"from": 0, "to": 1000},
                    "text": "fixture candidate",
                }
            ],
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cue = {"start_ms": start_ms, "end_ms": start_ms + 1000, "text": "fixture candidate"}
        argv = build_whisper_argv(
            binary_path=binary_path,
            model_path=model_path,
            audio_path=audio_path,
            output_json_path=output_path,
            settings=settings,
            target_range=TimeRange(start_ms, end_ms),
        )
        execution = {
            "status": "ok",
            "argv": argv,
            "measured_argv": argv,
            "stdout": "",
            "stderr": "        0.12 real         0.01 user         0.01 sys\n           456 maximum resident set size\n",
            "returncode": 0,
            "duration_ms": 123,
            "peak_memory_bytes": 456,
            "output_paths": [str(output_path)],
            "run_kind": "cold",
            "cache_hit": False,
            "cues": [cue],
            "error": None,
        }
        target = TimeRange(start_ms, end_ms)
        from benchmarks.s9_benchmark import VttCue

        actual_cues = [VttCue(**cue)]
        anchors_objects = [CueAnchor.from_value(anchor, index) for index, anchor in enumerate(anchors)]
        baseline_cues = _exclude_marker_cues(
            deduplicate_progressive_timed(parse_vtt_file(PARITY_FIXTURE_PATH)),
            manifest["normalization"].get("exclude_text_tokens", []),
            normalization,
        )
        baseline_metric = _case_metrics(
            gold_text=fixture_case["gold"]["text"],
            cues=baseline_cues,
            gold_anchors=anchors_objects,
            target=target,
            glossary=fixture_case["gold"]["glossary"],
            normalization=normalization,
            wall_time_ms=None,
            peak_memory_bytes=None,
            run_kind=None,
            cache_hit=None,
        )
        candidate_metric = _case_metrics(
            gold_text=fixture_case["gold"]["text"],
            cues=actual_cues,
            gold_anchors=anchors_objects,
            target=target,
            glossary=fixture_case["gold"]["glossary"],
            normalization=normalization,
            wall_time_ms=123,
            peak_memory_bytes=456,
            run_kind="cold",
            cache_hit=False,
        )
        candidate_metric["execution"] = execution
        cases.append(
            {
                "case_id": case_id,
                "target_range": {"start_ms": start_ms, "end_ms": end_ms},
                "gold_cue_anchors": anchors,
                "gold_cue_anchor_source": "fixture",
                "baseline": baseline_metric,
                "candidate": candidate_metric,
                "candidate_output_path": str(output_path),
            }
        )
    raw = {
        "schema": "s9-1-benchmark-report-v1",
        "benchmark_id": manifest["benchmark_id"],
        "manifest_fingerprint": manifest_fingerprint(manifest),
        "fingerprints": {"model": file_fingerprint(model_path), "inputs": expected_inputs},
        "whisper_runtime": runtime_identity,
        "gold_audit_status": "provisional",
        "metrics_status": "provisional",
        "cases": cases,
    }
    return manifest, raw, {
        "model_path": model_path,
        "binary_path": binary_path,
        "expected_inputs": expected_inputs,
        "expected_vtt": {case["id"]: expected_vtt for case in manifest["cases"]},
        "expected_runtime": runtime_identity,
        "expected_run_root": _canonical_run_root(manifest, "ggml-large-v3-turbo-q5_0", "cold"),
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
    assert report["schema"] == "s9-1-comparison-report-v7"
    assert report["decision"]["go"] is True
    assert report["decision"]["s9_3_reference"] == "ggml-large-v3-turbo-q5_0"
    assert report["decision"]["boundary_automation"] == "not_adopted_human_review_required"
    assert report["decision"]["boundary_decision"]["status"] == "no_go"
    assert report["decision"]["operational_transcript_decision"]["status"] == "go"
    assert report["gold_audit_status"] == "unverified_provisional"
    assert report["transcript_reference_status"] == "accepted_operational_benchmark_reference"
    assert report["human_audit"]["review_policy"]["displayed_transcript_content"]["acceptance"] == "operational_benchmark_reference"
    assert report["evaluation_contract"]["raw_report_identity"]["all_models_share_runtime_identity"] is True
    contract = report["evaluation_contract"]
    assert contract["schema"] == "s9-1-evaluation-contract-v2"
    assert "gates" not in contract
    assert contract["benchmark_quality_gate"]["namespace"] == "fixture_benchmark_quality"
    assert contract["benchmark_quality_gate"]["validator"] == "validate_fixture_benchmark_quality_gate_v1"
    assert contract["benchmark_quality_gate"]["source"] == "docs/benchmarks/s9-1-cases.json:gates"
    assert contract["benchmark_quality_gate"]["thresholds"]["paired_median_relative_cer_improvement"] == 0.1
    assert contract["benchmark_quality_gate"]["fixture_exact_gold"]["required_for_benchmark_quality"] is True
    assert contract["benchmark_quality_gate"]["passed"] is False
    assert contract["effective_operational_gate"]["namespace"] == "operational_transcript_reference"
    assert contract["effective_operational_gate"]["validator"] == "validate_effective_operational_gate_v1"
    assert contract["effective_operational_gate"]["fixture_exact_gold"]["required_for_operational_go"] is False
    assert contract["effective_operational_gate"]["passed"] is True
    assert "require_gold_audit" not in json.dumps(contract, ensure_ascii=False)
    assert contract["candidate_output_identity"]["validator"] == "validate_canonical_candidate_output_path_v1"
    assert contract["candidate_output_identity"]["template"].endswith("{case_id}/{run_kind}.json")
    assert _validate_evaluation_gate_contract(contract, expected_decision_go=True)["effective_operational_passed"] is True
    shared_runtime_identity = report["models"][0]["raw_identity"]["cold"]["runtime_identity"]
    assert all(
        model["raw_identity"]["cold"]["runtime_identity"] == shared_runtime_identity
        for model in report["models"]
    )
    assert report["raw_report_identity"]["runtime_identity_verified"] is True
    assert report["raw_report_identity"]["input_fingerprints_verified"] is True
    assert report["raw_report_identity"]["metrics_recomputed_from_artifacts"] is True
    assert report["raw_report_identity"]["output_fingerprints_verified"] is True
    assert report["evaluation_contract"]["vtt_progressive_parity_gate"]["passed"] is True
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
        assert model["gates"]["fixture_exact_gold"]["passed"] is False
        assert model["gates"]["fixture_exact_gold"]["status"] == "unverified_provisional"
        assert model["gates"]["fixture_exact_gold"]["required_for_benchmark_quality"] is True
        assert model["gates"]["fixture_exact_gold"]["required_for_operational_go"] is False
        assert model["gates"]["vtt_progressive_parity"]["passed"] is True
        run_count += len(model["runs"]["cold"]["cases"])
        run_count += len(model["runs"]["warm"]["cases"])
    assert run_count == 16
    assert report["production_integrity"]["unchanged"] is True
    assert report["production_integrity"]["scope"]["expected_file_count"] == 15
    assert report["production_integrity"]["scope"]["fixture_source_file_count"] == 14
    assert report["production_integrity"]["scope"]["protected_file_count"] == 1
    evidence = report["models"][0]["raw_identity"]["evidence"]["cold"][0]
    assert evidence["output_path_identity_verified"] is True
    assert evidence["output_fingerprint_verified"] is True


@pytest.mark.parametrize("mutation", [
    lambda contract: (contract.pop("benchmark_quality_gate"), contract.pop("effective_operational_gate"), contract.update(gates={"require_gold_audit": False})),
    lambda contract: contract["effective_operational_gate"]["fixture_exact_gold"].update(required_for_operational_go=True),
])
def test_gate_validator_rejects_legacy_or_ambiguous_gold_contract(mutation) -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    contract = deepcopy(report["evaluation_contract"])
    mutation(contract)

    with pytest.raises(ValueError):
        _validate_evaluation_gate_contract(contract, expected_decision_go=True)


def test_production_scope_is_fixture_14_plus_protected_1() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    scope = _derive_production_scope(manifest)

    assert scope["fixture_source_file_count"] == 14
    assert scope["protected_file_count"] == 1
    assert len(scope["expected_relative_paths"]) == 15
    assert "LB4px1wRFnY/shorts/cutplan/cut_clip_003.json" in scope["expected_relative_paths"]


def test_production_hash_artifact_rechecks_actual_files(tmp_path: Path) -> None:
    root = tmp_path / "production"
    (root / "nested").mkdir(parents=True)
    (root / "alpha.txt").write_text("alpha", encoding="utf-8")
    (root / "nested" / "beta.txt").write_text("beta", encoding="utf-8")
    scope = {
        "root": root,
        "expected_relative_paths": frozenset({"alpha.txt", "nested/beta.txt"}),
        "fixture_source_file_count": 2,
        "protected_file_count": 0,
    }
    artifact = {
        "root": str(root),
        "files": {
            relative: file_fingerprint(root / relative)
            for relative in ("alpha.txt", "nested/beta.txt")
        },
    }
    artifact["files"] = {
        relative: {"bytes": value["bytes"], "sha256": value["sha256"]}
        for relative, value in artifact["files"].items()
    }
    artifact_path = tmp_path / "hash.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    checked = _validate_production_hash_artifact(artifact_path, expected_scope=scope)
    assert checked["actual_recheck"] is True
    assert checked["scope_validation"]["exact_file_set"] is True

    (root / "alpha.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="production file hash"):
        _validate_production_hash_artifact(artifact_path, expected_scope=scope)


@pytest.mark.parametrize("mutation", [
    lambda artifact: artifact["files"].pop("alpha.txt"),
    lambda artifact: artifact["files"].update({"extra.txt": {"bytes": 0, "sha256": "0" * 64}}),
    lambda artifact: artifact.update(root="/wrong/root"),
    lambda artifact: artifact["files"].update({"../alpha.txt": artifact["files"].pop("alpha.txt")}),
    lambda artifact: artifact["files"].update({"/absolute.txt": artifact["files"].pop("alpha.txt")}),
])
def test_production_hash_scope_mutations_fail_closed(tmp_path: Path, mutation) -> None:
    root = tmp_path / "production"
    root.mkdir()
    (root / "alpha.txt").write_text("alpha", encoding="utf-8")
    scope = {
        "root": root,
        "expected_relative_paths": frozenset({"alpha.txt"}),
        "fixture_source_file_count": 1,
        "protected_file_count": 0,
    }
    artifact = {
        "root": str(root),
        "files": {"alpha.txt": {"bytes": 5, "sha256": file_fingerprint(root / "alpha.txt")["sha256"]}},
    }
    mutation(artifact)
    artifact_path = tmp_path / "hash.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        _validate_production_hash_artifact(artifact_path, expected_scope=scope)


def test_production_hash_scope_rejects_file_and_directory_symlink(tmp_path: Path) -> None:
    root = tmp_path / "production"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    scope = {
        "root": root,
        "expected_relative_paths": frozenset({"link.txt"}),
        "fixture_source_file_count": 1,
        "protected_file_count": 0,
    }
    artifact = {"root": str(root), "files": {"link.txt": {"bytes": 7, "sha256": file_fingerprint(outside)["sha256"]}}}
    artifact_path = tmp_path / "hash.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError):
        _validate_production_hash_artifact(artifact_path, expected_scope=scope)

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "escape.txt").write_text("escape", encoding="utf-8")
    (root / "linkdir").symlink_to(outside_dir, target_is_directory=True)
    directory_scope = {
        "root": root,
        "expected_relative_paths": frozenset({"linkdir/escape.txt"}),
        "fixture_source_file_count": 1,
        "protected_file_count": 0,
    }
    directory_artifact = {
        "root": str(root),
        "files": {
            "linkdir/escape.txt": {
                "bytes": 6,
                "sha256": file_fingerprint(outside_dir / "escape.txt")["sha256"],
            }
        },
    }
    directory_path = tmp_path / "directory-hash.json"
    directory_path.write_text(json.dumps(directory_artifact), encoding="utf-8")
    with pytest.raises(ValueError):
        _validate_production_hash_artifact(directory_path, expected_scope=directory_scope)


def _valid_parity_artifact(manifest: dict) -> tuple[dict, dict[str, dict[str, int | str]]]:
    import hashlib

    source = file_fingerprint(PARITY_FIXTURE_PATH)
    text_sha = hashlib.sha256("alpha\nbeta".encode()).hexdigest()
    cases = [
        {
            "case_id": case["id"],
            "source_vtt_bytes": source["bytes"],
            "source_vtt_sha256": source["sha256"],
            "production_raw_cues": 2,
            "benchmark_raw_cues": 2,
            "production_dedup_cues": 2,
            "benchmark_dedup_cues": 2,
            "production_text_sha256": text_sha,
            "benchmark_text_sha256": text_sha,
            "text_sequence_equal": True,
        }
        for case in manifest["cases"]
    ]
    expected_inputs = {case["id"]: source for case in manifest["cases"]}
    return {
        "schema": "s9-1-vtt-progressive-parity-v2",
        "benchmark_id": manifest["benchmark_id"],
        "fixture_fingerprint": manifest_fingerprint(manifest),
        "production_function": "yt_live_kit.services.vtt_parser.deduplicate_progressive",
        "benchmark_function": "benchmarks.s9_benchmark.deduplicate_progressive_timed",
        "cases": cases,
    }, expected_inputs


def test_vtt_parity_is_strict_and_is_a_canonical_gate(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    artifact, expected_inputs = _valid_parity_artifact(manifest)
    expected_paths = {case["id"]: PARITY_FIXTURE_PATH for case in manifest["cases"]}
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    result = _validate_vtt_parity_artifact(
        path,
        fixture=manifest,
        expected_vtt_inputs=expected_inputs,
        expected_vtt_paths=expected_paths,
    )
    assert result["passed"] is True
    assert result["case_count"] == 4


@pytest.mark.parametrize("mutation", [
    lambda value: value["cases"].pop(),
    lambda value: value["cases"].__setitem__(0, {**value["cases"][0], "case_id": "unknown-case"}),
    lambda value: value["cases"].reverse(),
    lambda value: value["cases"][0].pop("source_vtt_sha256"),
    lambda value: value["cases"][0].update(source_vtt_sha256="0" * 64),
    lambda value: value["cases"][0].update(text_sequence_equal=False),
    lambda value: value.update(cases=[]),
    lambda value: value.update(benchmark_id="other"),
    lambda value: value.update(unknown="field"),
])
def test_vtt_parity_mutations_fail_closed(tmp_path: Path, mutation) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    artifact, expected_inputs = _valid_parity_artifact(manifest)
    mutation(artifact)
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    expected_paths = {case["id"]: PARITY_FIXTURE_PATH for case in manifest["cases"]}
    with pytest.raises(ValueError):
        _validate_vtt_parity_artifact(
            path,
            fixture=manifest,
            expected_vtt_inputs=expected_inputs,
            expected_vtt_paths=expected_paths,
        )


def test_raw_evidence_rehashes_and_recomputes_metrics_without_external_cache(tmp_path: Path) -> None:
    manifest, raw, details = _local_raw_evidence_fixture(tmp_path)
    raw_path = details["expected_run_root"] / "report.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    identity = _validate_raw_report_identity(
        raw_path,
        fixture=manifest,
        expected_model_name="ggml-large-v3-turbo-q5_0",
        expected_run_kind="cold",
        expected_inputs=details["expected_inputs"],
        expected_vtt_inputs=details["expected_vtt"],
        expected_model_fingerprint=file_fingerprint(details["model_path"]),
        expected_runtime_identity=details["expected_runtime"],
    )
    assert identity["all_case_runs_ok"] is True
    for case in raw["cases"]:
        case_id = case["case_id"]
        audio_path = next(
            Path(item["path"])
            for item in details["expected_inputs"]
            if item["kind"] == "audio" and item["case_id"] == case_id
        )
        evidence = _validate_raw_case_evidence(
            raw_case=case,
            fixture=manifest,
            case_id=case_id,
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )
        assert evidence["metrics_verified"] is True
        assert evidence["output_schema_verified"] is True


@pytest.mark.parametrize("mutation", [
    lambda case: case["candidate"].update(cer=0.0),
    lambda case: case["baseline"].update(cer=0.0),
    lambda case: case["candidate"]["execution"]["cues"][0].update(text="tampered"),
    lambda case: case["candidate"]["execution"]["argv"].__setitem__(0, "/wrong/whisper-cli"),
    lambda case: case["candidate"].update(run_kind="warm"),
    lambda case: case["candidate"]["execution"].update(duration_ms=1),
    lambda case: case["candidate"]["execution"].update(peak_memory_bytes=1),
])
def test_raw_evidence_metric_text_and_argv_mutations_fail_closed(tmp_path: Path, mutation) -> None:
    manifest, raw, details = _local_raw_evidence_fixture(tmp_path)
    changed = deepcopy(raw["cases"][0])
    mutation(changed)
    case_id = changed["case_id"]
    audio_path = next(
        Path(item["path"])
        for item in details["expected_inputs"]
        if item["kind"] == "audio" and item["case_id"] == case_id
    )
    with pytest.raises(ValueError):
        _validate_raw_case_evidence(
            raw_case=changed,
            fixture=manifest,
            case_id=case_id,
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )


def test_raw_evidence_output_artifact_mutation_fails_closed(tmp_path: Path) -> None:
    manifest, raw, details = _local_raw_evidence_fixture(tmp_path)
    output_path = Path(raw["cases"][0]["candidate_output_path"])
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["transcription"][0]["text"] = "tampered output"
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    case = raw["cases"][0]
    case_id = case["case_id"]
    audio_path = next(
        Path(item["path"])
        for item in details["expected_inputs"]
        if item["kind"] == "audio" and item["case_id"] == case_id
    )
    with pytest.raises(ValueError):
        _validate_raw_case_evidence(
            raw_case=case,
            fixture=manifest,
            case_id=case_id,
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )


def test_candidate_output_case_run_and_symlink_swaps_fail_closed(tmp_path: Path) -> None:
    manifest, raw, details = _local_raw_evidence_fixture(tmp_path)
    changed = deepcopy(raw["cases"][0])
    other_case_path = raw["cases"][1]["candidate_output_path"]
    changed["candidate_output_path"] = other_case_path
    changed["candidate"]["execution"]["output_paths"] = [other_case_path]
    audio_path = next(
        Path(item["path"])
        for item in details["expected_inputs"]
        if item["kind"] == "audio" and item["case_id"] == changed["case_id"]
    )
    with pytest.raises(ValueError, match="canonical case/model/run-kind"):
        _validate_raw_case_evidence(
            raw_case=changed,
            fixture=manifest,
            case_id=changed["case_id"],
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )

    changed = deepcopy(raw["cases"][0])
    warm_root = _canonical_run_root(manifest, "ggml-large-v3-turbo-q5_0", "warm")
    warm_path = _expected_candidate_output_path(warm_root, changed["case_id"], "warm")
    changed["candidate_output_path"] = str(warm_path)
    changed["candidate"]["execution"]["output_paths"] = [str(warm_path)]
    with pytest.raises(ValueError, match="canonical case/model/run-kind"):
        _validate_raw_case_evidence(
            raw_case=changed,
            fixture=manifest,
            case_id=changed["case_id"],
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )

    expected_path = Path(raw["cases"][0]["candidate_output_path"])
    original = expected_path.read_bytes()
    outside = tmp_path / "outside-output.json"
    outside.write_bytes(original)
    expected_path.unlink()
    expected_path.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink escape"):
        _validate_raw_case_evidence(
            raw_case=raw["cases"][0],
            fixture=manifest,
            case_id=raw["cases"][0]["case_id"],
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )


def test_candidate_output_fingerprint_declaration_cannot_be_forged(tmp_path: Path) -> None:
    manifest, raw, details = _local_raw_evidence_fixture(tmp_path)
    changed = deepcopy(raw["cases"][0])
    changed["candidate_output_fingerprint"] = {
        "path": changed["candidate_output_path"],
        "bytes": 0,
        "sha256": "0" * 64,
    }
    audio_path = next(
        Path(item["path"])
        for item in details["expected_inputs"]
        if item["kind"] == "audio" and item["case_id"] == changed["case_id"]
    )
    with pytest.raises(ValueError, match="output fingerprint"):
        _validate_raw_case_evidence(
            raw_case=changed,
            fixture=manifest,
            case_id=changed["case_id"],
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )


def test_real_time_parser_and_q5_speedup_forgery_fail_closed(tmp_path: Path) -> None:
    assert _parse_real_time_ms("        2.25 real         0.74 user         0.21 sys\n") == 2250
    assert _parse_real_time_ms("no time evidence") is None
    manifest, raw, details = _local_raw_evidence_fixture(tmp_path)
    changed = deepcopy(raw["cases"][0])
    # A forged q5 wall value that could win the tie-break must not pass stderr evidence.
    changed["candidate"]["wall_time_ms"] = 1
    changed["candidate"]["execution"]["duration_ms"] = 1
    audio_path = next(
        Path(item["path"])
        for item in details["expected_inputs"]
        if item["kind"] == "audio" and item["case_id"] == changed["case_id"]
    )
    with pytest.raises(ValueError, match="real time"):
        _validate_raw_case_evidence(
            raw_case=changed,
            fixture=manifest,
            case_id=changed["case_id"],
            expected_run_kind="cold",
            expected_model_path=details["model_path"],
            expected_audio_path=audio_path,
            expected_vtt_path=PARITY_FIXTURE_PATH,
            expected_settings=details["expected_runtime"]["settings"],
            expected_run_root=details["expected_run_root"],
        )


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
