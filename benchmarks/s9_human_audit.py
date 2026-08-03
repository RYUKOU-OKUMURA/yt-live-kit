"""Strict validation for the S9-1 natural-language human audit artifact.

The audit records an operational review of the displayed transcript text.  It
does not turn a natural-language statement into character-exact transcript
approval or exact cue timestamps.  The schema is deliberately closed so that
an accidental field, case swap, or stronger status fails closed.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


HUMAN_AUDIT_SCHEMA = "s9-1-human-audit-v2"
EXPECTED_BENCHMARK_ID = "s9-1-20260803"
EXPECTED_AUDIT_DATE = "2026-08-03"
EXPECTED_AUDITOR = "user"
EXPECTED_EXACT_QUOTE = "4本とも文字起こしは概ね問題なし"
EXPECTED_REVIEW_CONTEXT = "2026-08-03に4本のProvisional gold transcriptを開いて確認した後のユーザー所見。"
EXPECTED_BASE_FIXTURE_FINGERPRINT = "6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718"
EXPECTED_BOUNDARY_AUDIT_FINGERPRINT = "0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1"

STATEMENT_SCOPE = "all_four_cases_same_user_statement"
REVIEW_POLICY_MODE = "operational_transcript_reference"
DISPLAYED_TRANSCRIPT_STATUS = "human_reviewed_no_material_issue_reported"
DISPLAYED_TRANSCRIPT_ACCEPTANCE = "operational_benchmark_reference"
GLOSSARY_STATUS = "not_explicitly_audited"
CHARACTER_PUNCTUATION_STATUS = "not_claimed"
CUE_ANCHOR_STATUS = "unapproved"
BOUNDARY_EDITORIAL_STATUS = "preserved_partial_boundary_audit"

FIXED_CASE_MAPPING = (
    (1, "lb4-clip002-short-proper-nouns"),
    (2, "hpe-audio-variation"),
    (3, "cgal-proper-nouns"),
    (4, "mkw-long-local-asr"),
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

_ROOT_FIELDS = {
    "schema",
    "benchmark_id",
    "audit_date",
    "auditor",
    "source",
    "base_fixture_fingerprint",
    "boundary_audit_fingerprint",
    "review_policy",
    "previous_display_order",
    "cases",
    "audit_fingerprint",
}
_SOURCE_FIELDS = {"kind", "exact_quote", "review_context"}
_REVIEW_POLICY_FIELDS = {
    "mode",
    "displayed_transcript_content",
    "glossary",
    "character_punctuation_exactness",
    "cue_anchor_exact_ms",
    "boundary_editorial",
    "boundary_auto_adoption",
    "human_boundary_review",
}
_DISPLAYED_TRANSCRIPT_FIELDS = {"status", "acceptance"}
_STATUS_FIELDS = {"status"}
_MAPPING_FIELDS = {"display_order", "case_id"}
_CASE_FIELDS = {
    "case_id",
    "display_order",
    "statement_scope",
    "displayed_transcript_content",
    "glossary",
    "character_punctuation_exactness",
    "cue_anchor_exact_ms",
    "boundary_editorial",
}


class HumanAuditError(ValueError):
    """Raised when a human-audit artifact violates the closed schema."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _error(message: str) -> HumanAuditError:
    # Error text is part of the user-facing audit surface.  Never reflect an
    # arbitrary value into it, because that could introduce forbidden markup.
    if "<" in message or ">" in message:
        raise RuntimeError("internal human-audit error text contains a forbidden character")
    return HumanAuditError(message)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(f"{label} は object である必要があります。")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise _error(f"{label} の field が schema と一致しません。")


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(f"{label} は空でない文字列が必要です。")
    if "<" in value or ">" in value:
        raise _error("監査 artifact に半角の山カッコを含めることはできません。")
    return value


def _require_fixed_text(value: Any, expected: str, label: str) -> str:
    text = _require_text(value, label)
    if text != expected:
        raise _error(f"{label} が固定値と一致しません。")
    return text


def _require_sha256(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if _SHA256_RE.fullmatch(text) is None:
        raise _error(f"{label} は SHA-256 hex 文字列である必要があります。")
    return text


def _require_fixed_status(value: Any, expected: str, label: str) -> str:
    return _require_fixed_text(value, expected, label)


def _validate_dimension(
    value: Any,
    *,
    label: str,
    expected_status: str,
    displayed: bool = False,
) -> dict[str, str]:
    obj = _require_object(value, label)
    _require_exact_keys(obj, _DISPLAYED_TRANSCRIPT_FIELDS if displayed else _STATUS_FIELDS, label)
    status = _require_fixed_status(obj["status"], expected_status, f"{label}.status")
    if displayed:
        acceptance = _require_fixed_status(
            obj["acceptance"], DISPLAYED_TRANSCRIPT_ACCEPTANCE, f"{label}.acceptance"
        )
        return {"status": status, "acceptance": acceptance}
    return {"status": status}


def _validate_review_policy(value: Any) -> dict[str, Any]:
    policy = _require_object(value, "review_policy")
    _require_exact_keys(policy, _REVIEW_POLICY_FIELDS, "review_policy")
    mode = _require_fixed_text(policy["mode"], REVIEW_POLICY_MODE, "review_policy.mode")
    displayed = _validate_dimension(
        policy["displayed_transcript_content"],
        label="review_policy.displayed_transcript_content",
        expected_status=DISPLAYED_TRANSCRIPT_STATUS,
        displayed=True,
    )
    glossary = _validate_dimension(
        policy["glossary"], label="review_policy.glossary", expected_status=GLOSSARY_STATUS
    )
    character = _validate_dimension(
        policy["character_punctuation_exactness"],
        label="review_policy.character_punctuation_exactness",
        expected_status=CHARACTER_PUNCTUATION_STATUS,
    )
    cue = _validate_dimension(
        policy["cue_anchor_exact_ms"],
        label="review_policy.cue_anchor_exact_ms",
        expected_status=CUE_ANCHOR_STATUS,
    )
    boundary = _validate_dimension(
        policy["boundary_editorial"],
        label="review_policy.boundary_editorial",
        expected_status=BOUNDARY_EDITORIAL_STATUS,
    )
    boundary_auto = _require_fixed_text(
        policy["boundary_auto_adoption"], "prohibited", "review_policy.boundary_auto_adoption"
    )
    human_boundary = _require_fixed_text(
        policy["human_boundary_review"], "required", "review_policy.human_boundary_review"
    )
    return {
        "mode": mode,
        "displayed_transcript_content": displayed,
        "glossary": glossary,
        "character_punctuation_exactness": character,
        "cue_anchor_exact_ms": cue,
        "boundary_editorial": boundary,
        "boundary_auto_adoption": boundary_auto,
        "human_boundary_review": human_boundary,
    }


def _validate_previous_display_order(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(FIXED_CASE_MAPPING):
        raise _error("previous_display_order は固定4件の配列である必要があります。")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        entry = _require_object(item, "previous_display_order item")
        _require_exact_keys(entry, _MAPPING_FIELDS, "previous_display_order item")
        display_order = entry["display_order"]
        if isinstance(display_order, bool) or not isinstance(display_order, int):
            raise _error("previous_display_order.display_order は整数が必要です。")
        case_id = _require_text(entry["case_id"], "previous_display_order.case_id")
        expected_order, expected_case_id = FIXED_CASE_MAPPING[index]
        if (display_order, case_id) != (expected_order, expected_case_id):
            raise _error("previous_display_order の固定対応が一致しません。")
        normalized.append({"display_order": display_order, "case_id": case_id})
    return normalized


def _validate_cases(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(FIXED_CASE_MAPPING):
        raise _error("cases は固定4件の配列である必要があります。")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        case = _require_object(item, "case")
        _require_exact_keys(case, _CASE_FIELDS, "case")
        expected_order, expected_case_id = FIXED_CASE_MAPPING[index]
        case_id = _require_fixed_text(case["case_id"], expected_case_id, "case.case_id")
        display_order = case["display_order"]
        if isinstance(display_order, bool) or not isinstance(display_order, int):
            raise _error("case.display_order は整数が必要です。")
        if display_order != expected_order:
            raise _error("case の固定表示順が一致しません。")
        statement_scope = _require_fixed_text(case["statement_scope"], STATEMENT_SCOPE, "case.statement_scope")
        displayed = _validate_dimension(
            case["displayed_transcript_content"],
            label=f"{case_id}.displayed_transcript_content",
            expected_status=DISPLAYED_TRANSCRIPT_STATUS,
            displayed=True,
        )
        glossary = _validate_dimension(
            case["glossary"], label=f"{case_id}.glossary", expected_status=GLOSSARY_STATUS
        )
        character = _validate_dimension(
            case["character_punctuation_exactness"],
            label=f"{case_id}.character_punctuation_exactness",
            expected_status=CHARACTER_PUNCTUATION_STATUS,
        )
        cue = _validate_dimension(
            case["cue_anchor_exact_ms"],
            label=f"{case_id}.cue_anchor_exact_ms",
            expected_status=CUE_ANCHOR_STATUS,
        )
        boundary = _validate_dimension(
            case["boundary_editorial"],
            label=f"{case_id}.boundary_editorial",
            expected_status=BOUNDARY_EDITORIAL_STATUS,
        )
        normalized.append(
            {
                "case_id": case_id,
                "display_order": display_order,
                "statement_scope": statement_scope,
                "displayed_transcript_content": displayed,
                "glossary": glossary,
                "character_punctuation_exactness": character,
                "cue_anchor_exact_ms": cue,
                "boundary_editorial": boundary,
            }
        )
    return normalized


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("監査 artifact の canonical JSON を生成できません。") from exc


def human_audit_fingerprint(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 of canonical audit data without its self-fingerprint."""

    if not isinstance(value, Mapping):
        raise _error("監査 artifact は object である必要があります。")
    payload = deepcopy(dict(value))
    payload.pop("audit_fingerprint", None)
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def validate_human_audit(
    value: Mapping[str, Any],
    expected_base_fixture_fingerprint: str | None = None,
    expected_boundary_audit_fingerprint: str | None = None,
    expected_benchmark_id: str | None = None,
) -> dict[str, Any]:
    """Validate and return a path-independent, normalized audit artifact."""

    root = _require_object(value, "監査 artifact")
    _require_exact_keys(root, _ROOT_FIELDS, "root")
    _require_fixed_text(root["schema"], HUMAN_AUDIT_SCHEMA, "schema")
    _require_fixed_text(root["benchmark_id"], EXPECTED_BENCHMARK_ID, "benchmark_id")
    if expected_benchmark_id is not None and root["benchmark_id"] != expected_benchmark_id:
        raise _error("benchmark_id が期待値と一致しません。")
    _require_fixed_text(root["audit_date"], EXPECTED_AUDIT_DATE, "audit_date")
    try:
        date.fromisoformat(root["audit_date"])
    except (TypeError, ValueError) as exc:
        raise _error("audit_date は実在する ISO 日付である必要があります。") from exc
    _require_fixed_text(root["auditor"], EXPECTED_AUDITOR, "auditor")

    source = _require_object(root["source"], "source")
    _require_exact_keys(source, _SOURCE_FIELDS, "source")
    _require_fixed_text(source["kind"], "user_natural_language", "source.kind")
    _require_fixed_text(source["exact_quote"], EXPECTED_EXACT_QUOTE, "source.exact_quote")
    _require_fixed_text(source["review_context"], EXPECTED_REVIEW_CONTEXT, "source.review_context")

    base_fingerprint = _require_sha256(root["base_fixture_fingerprint"], "base_fixture_fingerprint")
    if base_fingerprint != EXPECTED_BASE_FIXTURE_FINGERPRINT:
        raise _error("base_fixture_fingerprint が固定値と一致しません。")
    if (
        expected_base_fixture_fingerprint is not None
        and base_fingerprint != expected_base_fixture_fingerprint
    ):
        raise _error("base_fixture_fingerprint が期待値と一致しません。")

    boundary_fingerprint = _require_sha256(
        root["boundary_audit_fingerprint"], "boundary_audit_fingerprint"
    )
    if boundary_fingerprint != EXPECTED_BOUNDARY_AUDIT_FINGERPRINT:
        raise _error("boundary_audit_fingerprint が固定値と一致しません。")
    if (
        expected_boundary_audit_fingerprint is not None
        and boundary_fingerprint != expected_boundary_audit_fingerprint
    ):
        raise _error("boundary_audit_fingerprint が期待値と一致しません。")

    review_policy = _validate_review_policy(root["review_policy"])
    previous_display_order = _validate_previous_display_order(root["previous_display_order"])
    cases = _validate_cases(root["cases"])

    normalized_without_fingerprint: dict[str, Any] = {
        "schema": HUMAN_AUDIT_SCHEMA,
        "benchmark_id": EXPECTED_BENCHMARK_ID,
        "audit_date": EXPECTED_AUDIT_DATE,
        "auditor": EXPECTED_AUDITOR,
        "source": {
            "kind": "user_natural_language",
            "exact_quote": EXPECTED_EXACT_QUOTE,
            "review_context": EXPECTED_REVIEW_CONTEXT,
        },
        "base_fixture_fingerprint": base_fingerprint,
        "boundary_audit_fingerprint": boundary_fingerprint,
        "review_policy": review_policy,
        "previous_display_order": previous_display_order,
        "cases": cases,
    }
    computed_fingerprint = human_audit_fingerprint(normalized_without_fingerprint)
    supplied_fingerprint = _require_sha256(root["audit_fingerprint"], "audit_fingerprint")
    if supplied_fingerprint != computed_fingerprint:
        raise _error("audit_fingerprint が内容と一致しません。")

    normalized = dict(normalized_without_fingerprint)
    normalized["audit_fingerprint"] = computed_fingerprint
    return normalized


def load_human_audit(
    path: str | Path,
    *,
    expected_base_fixture_fingerprint: str | None = None,
    expected_boundary_audit_fingerprint: str | None = None,
    expected_benchmark_id: str | None = None,
) -> dict[str, Any]:
    """Load and validate an audit artifact without retaining its filesystem path."""

    artifact_path = Path(path)
    try:
        value = json.loads(artifact_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _error("human audit artifact を読めません。") from exc
    except json.JSONDecodeError as exc:
        raise _error("human audit artifact が JSON として不正です。") from exc
    return validate_human_audit(
        value,
        expected_base_fixture_fingerprint=expected_base_fixture_fingerprint,
        expected_boundary_audit_fingerprint=expected_boundary_audit_fingerprint,
        expected_benchmark_id=expected_benchmark_id,
    )
