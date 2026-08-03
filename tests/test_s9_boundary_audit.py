from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks import s9_compare
from benchmarks.s9_audit_packet import AuditPacketError, load_boundary_audit
from benchmarks.s9_benchmark import (
    BoundaryAuditError,
    boundary_audit_fingerprint,
    evaluate_boundary_audit,
    manifest_fingerprint,
    validate_boundary_audit,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs/benchmarks/s9-1-cases.json"
BOUNDARY_PATH = ROOT / "docs/benchmarks/s9-1-boundary-audit.json"
REPORT_PATH = ROOT / "docs/benchmarks/s9-1-report.json"


def _load_fixture_and_audit() -> tuple[dict, dict]:
    return (
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8")),
        json.loads(BOUNDARY_PATH.read_text(encoding="utf-8")),
    )


def test_boundary_audit_matches_manifest_and_expected_editorial_contract() -> None:
    manifest, audit = _load_fixture_and_audit()

    result = evaluate_boundary_audit(
        audit,
        expected_base_fixture_fingerprint=manifest_fingerprint(manifest),
        expected_benchmark_id=manifest["benchmark_id"],
    )

    assert result["status"] == "pass"
    assert result["base_fixture_fingerprint"] == manifest_fingerprint(manifest)
    assert result["audit_status"] == "partial_boundary_only"
    assert result["expected_editorial_outcomes_verified"] is True
    assert [(case["display_order"], case["case_id"]) for case in result["cases"]] == [
        (1, "lb4-clip002-short-proper-nouns"),
        (2, "hpe-audio-variation"),
        (3, "cgal-proper-nouns"),
        (4, "mkw-long-local-asr"),
    ]
    assert [case["expected_editorial_outcome"] for case in result["cases"]] == [
        "pass",
        "opening_trim_or_review_required",
        "opening_trim_or_review_required",
        "internal_gap_removal_or_review_required",
    ]
    assert boundary_audit_fingerprint(audit) == "0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1"
    assert boundary_audit_fingerprint(audit) != manifest_fingerprint(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_order", 99),
        ("opening_signal", "meaningful_speech_present_at_opening"),
        ("internal_continuity", "long_internal_speech_gap"),
        ("expected_editorial_outcome", "opening_trim_or_review_required"),
    ],
)
def test_boundary_audit_rejects_case_contract_drift(field: str, value: object) -> None:
    _, audit = _load_fixture_and_audit()
    changed = deepcopy(audit)
    changed["cases"][0][field] = value

    with pytest.raises(BoundaryAuditError):
        validate_boundary_audit(changed)


@pytest.mark.parametrize("field", ["opening_signal", "internal_continuity", "expected_editorial_outcome"])
@pytest.mark.parametrize("bad_value", [[], {}, True, None, "unknown_enum_value"])
def test_boundary_audit_enum_fields_reject_non_string_and_unknown_values(
    field: str,
    bad_value: object,
) -> None:
    _, audit = _load_fixture_and_audit()
    changed = deepcopy(audit)
    changed["cases"][0][field] = bad_value

    with pytest.raises(BoundaryAuditError) as exc_info:
        validate_boundary_audit(changed)
    assert field in str(exc_info.value) or "固定 enum" in str(exc_info.value)


@pytest.mark.parametrize("field", ["source_feedback", "approximate_timing_note"])
@pytest.mark.parametrize("case_index", range(4))
def test_boundary_audit_rejects_canonical_text_changes(field: str, case_index: int) -> None:
    _, audit = _load_fixture_and_audit()
    changed = deepcopy(audit)
    changed["cases"][case_index][field] = f"{changed['cases'][case_index][field]} 改変"

    with pytest.raises(BoundaryAuditError, match="canonical"):
        validate_boundary_audit(changed)


@pytest.mark.parametrize("field", ["source_feedback", "approximate_timing_note"])
def test_boundary_audit_rejects_canonical_text_swaps_between_cases(field: str) -> None:
    _, audit = _load_fixture_and_audit()
    changed = deepcopy(audit)
    changed["cases"][0][field] = changed["cases"][1][field]

    with pytest.raises(BoundaryAuditError, match="canonical"):
        validate_boundary_audit(changed)


def test_boundary_audit_loader_rejects_canonical_text_change(tmp_path: Path) -> None:
    manifest, audit = _load_fixture_and_audit()
    changed = deepcopy(audit)
    changed["cases"][2]["approximate_timing_note"] = "6秒を production 閾値にする。"
    changed_path = tmp_path / "boundary-audit.json"
    changed_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AuditPacketError, match="canonical"):
        load_boundary_audit(changed_path, manifest)


def test_boundary_audit_rejects_case_id_date_and_markdown_table_corruption() -> None:
    _, audit = _load_fixture_and_audit()

    changed_case_id = deepcopy(audit)
    changed_case_id["cases"][0]["case_id"] = "mkw-long-local-asr"
    with pytest.raises(BoundaryAuditError):
        validate_boundary_audit(changed_case_id)

    invalid_date = deepcopy(audit)
    invalid_date["audit_date"] = "2026-02-30"
    with pytest.raises(BoundaryAuditError):
        validate_boundary_audit(invalid_date)

    for bad_feedback in ("feedback | corrupted", "feedback\ncorrupted"):
        invalid_feedback = deepcopy(audit)
        invalid_feedback["cases"][0]["source_feedback"] = bad_feedback
        with pytest.raises(BoundaryAuditError):
            validate_boundary_audit(invalid_feedback)


def test_boundary_audit_benchmark_id_is_checked_by_packet_and_compare_loaders() -> None:
    manifest, _ = _load_fixture_and_audit()
    changed_manifest = deepcopy(manifest)
    changed_manifest["benchmark_id"] = "s9-1-other"

    with pytest.raises(AuditPacketError, match="benchmark_id"):
        load_boundary_audit(BOUNDARY_PATH, changed_manifest)
    with pytest.raises(BoundaryAuditError, match="benchmark_id"):
        s9_compare._load_boundary_audit(BOUNDARY_PATH, changed_manifest)


def test_canonical_report_keeps_boundary_audit_separate_from_quality_gate() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert report["schema"] == "s9-1-comparison-report-v6"
    assert report["fixture_fingerprint"] == "6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718"
    assert report["boundary_audit"]["fingerprint"] == "0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1"
    assert report["human_audit"]["source"]["exact_quote"] == "4本とも文字起こしは概ね問題なし"
    assert report["human_audit"]["review_policy"]["character_punctuation_exactness"]["status"] == "not_claimed"
    assert report["human_audit"]["review_policy"]["cue_anchor_exact_ms"]["status"] == "unapproved"
    assert report["decision"]["go"] is True
    assert report["decision"]["s9_2_ready"] is True
    assert report["decision"]["s9_3_reference"] == "ggml-large-v3-turbo-q5_0"
    assert report["decision"]["boundary_automation"] == "not_adopted_human_review_required"
    assert report["gold_audit_status"] == "unverified_provisional"
    assert report["transcript_reference_status"] == "accepted_operational_benchmark_reference"
    assert report["decision"]["boundary_decision"]["status"] == "no_go"
    assert report["decision"]["operational_transcript_decision"]["status"] == "go"
    assert report["decision"]["boundary_automation"] == "not_adopted_human_review_required"
    assert report["comparison"]["model_selection_contract"]["rule"]["not_a_threshold_change"] is True


def test_canonical_builders_require_boundary_audit() -> None:
    with pytest.raises(ValueError, match="boundary audit artifact is required"):
        s9_compare.build_comparison(
            manifest_path=MANIFEST_PATH,
            run_paths={},
            production_before=Path("before.json"),
            production_after=Path("after.json"),
            parity_path=Path("parity.json"),
        )


def test_canonical_clis_require_boundary_audit_argument() -> None:
    compare_parser = s9_compare.build_parser()
    compare_args = [
        "--manifest",
        "manifest.json",
        "--q5-cold",
        "q5-cold.json",
        "--q5-warm",
        "q5-warm.json",
        "--turbo-cold",
        "turbo-cold.json",
        "--turbo-warm",
        "turbo-warm.json",
        "--production-before",
        "before.json",
        "--production-after",
        "after.json",
        "--parity",
        "parity.json",
        "--output-json",
        "report.json",
        "--output-md",
        "report.md",
    ]
    with pytest.raises(SystemExit):
        compare_parser.parse_args(compare_args)

    packet_script = ROOT / "benchmarks/s9_audit_packet.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(packet_script),
            "check",
            "--manifest",
            str(MANIFEST_PATH),
            "--document",
            str(ROOT / "docs/benchmarks/s9-1-human-audit.md"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "--boundary-audit" in completed.stderr
