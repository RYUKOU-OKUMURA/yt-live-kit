from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmarks.s9_human_audit import (
    HumanAuditError,
    human_audit_fingerprint,
    load_human_audit,
    validate_human_audit,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "docs/benchmarks/s9-1-human-audit-v2.json"
BASE_FIXTURE = "6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718"
BOUNDARY_FINGERPRINT = "0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1"
BENCHMARK_ID = "s9-1-20260803"


def _artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_natural_language_audit_is_strictly_mapped_to_four_cases() -> None:
    artifact = load_human_audit(
        ARTIFACT_PATH,
        expected_base_fixture_fingerprint=BASE_FIXTURE,
        expected_boundary_audit_fingerprint=BOUNDARY_FINGERPRINT,
        expected_benchmark_id=BENCHMARK_ID,
    )

    assert artifact["source"]["exact_quote"] == "4本とも文字起こしは概ね問題なし"
    assert [(case["display_order"], case["case_id"]) for case in artifact["cases"]] == [
        (1, "lb4-clip002-short-proper-nouns"),
        (2, "hpe-audio-variation"),
        (3, "cgal-proper-nouns"),
        (4, "mkw-long-local-asr"),
    ]
    assert all(
        case["statement_scope"] == "all_four_cases_same_user_statement"
        for case in artifact["cases"]
    )


def test_audit_dimensions_are_not_promoted_to_exact_claims() -> None:
    artifact = load_human_audit(ARTIFACT_PATH)
    assert artifact["review_policy"]["mode"] == "operational_transcript_reference"
    assert artifact["review_policy"]["character_punctuation_exactness"]["status"] == "not_claimed"
    assert artifact["review_policy"]["cue_anchor_exact_ms"]["status"] == "unapproved"
    assert artifact["review_policy"]["glossary"]["status"] == "not_explicitly_audited"
    assert artifact["review_policy"]["boundary_auto_adoption"] == "prohibited"
    assert artifact["review_policy"]["human_boundary_review"] == "required"

    promoted = deepcopy(artifact)
    promoted["cases"][0]["character_punctuation_exactness"]["status"] = "exact_approved"
    with pytest.raises(HumanAuditError):
        validate_human_audit(promoted)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["source"].update(exact_quote="4本とも文字起こしは完全一致"),
        lambda value: value["cases"].__setitem__(0, {**value["cases"][0], "case_id": value["cases"][1]["case_id"]}),
        lambda value: value["cases"][0].pop("glossary"),
        lambda value: value.update(unexpected_field=True),
        lambda value: value["review_policy"].update(unexpected_field=True),
        lambda value: value["cases"][0]["cue_anchor_exact_ms"].update(unknown_field=True),
    ],
)
def test_audit_rejects_quote_missing_unknown_and_cross_case_drift(mutate) -> None:
    changed = deepcopy(_artifact())
    mutate(changed)
    with pytest.raises(HumanAuditError):
        validate_human_audit(changed)


def test_audit_fingerprint_changes_with_content_and_rejects_stale_value() -> None:
    artifact = _artifact()
    original = human_audit_fingerprint(artifact)
    changed = deepcopy(artifact)
    changed["cases"][2]["displayed_transcript_content"]["acceptance"] = "operational_benchmark_reference"
    changed["cases"][2]["statement_scope"] = "all_four_cases_same_user_statement"
    changed["source"]["review_context"] = "changed"
    assert human_audit_fingerprint(changed) != original

    changed = deepcopy(artifact)
    changed["audit_fingerprint"] = "0" * 64
    with pytest.raises(HumanAuditError, match="audit_fingerprint"):
        validate_human_audit(changed)


def test_audit_expected_identity_mismatch_is_fail_closed() -> None:
    with pytest.raises(HumanAuditError, match="base_fixture"):
        load_human_audit(ARTIFACT_PATH, expected_base_fixture_fingerprint="0" * 64)
    with pytest.raises(HumanAuditError, match="boundary_audit"):
        load_human_audit(ARTIFACT_PATH, expected_boundary_audit_fingerprint="0" * 64)
    with pytest.raises(HumanAuditError, match="benchmark_id"):
        load_human_audit(ARTIFACT_PATH, expected_benchmark_id="other")


def test_audit_artifact_has_no_halfwidth_angle_brackets() -> None:
    raw = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert "<" not in raw
    assert ">" not in raw
