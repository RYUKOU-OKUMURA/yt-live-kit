"""S9-1 専用の決定論的 benchmark harness。

このモジュールは production の字幕・動画・モデルを変更しない。manifest に
明示された入力を読み、既存の VTT / whisper-cli JSON を比較して JSON report を
作る。whisper-cli を実行する場合も、実行ファイル・モデル・音声入力は
manifest から受け取り、固定した argv を ``shell=False`` で 1 区間ずつ実行する。

## manifest schema: ``s9-1-benchmark-manifest-v1``

最小の manifest は次の形である。``path`` は manifest からの相対 path も許可
するが、fixture fingerprint には含めない。入力ファイルの内容は実行時に
SHA-256 と byte 数を記録する。fixture と model は手動で用意済みでなければ
ならず、自動ダウンロードは行わない。

.. code-block:: json

  {
    "schema": "s9-1-benchmark-manifest-v1",
    "benchmark_id": "representative-ja-2026-08",
    "gold_audit_status": "audited",
    "normalization": {
      "unicode_form": "NFKC",
      "remove_whitespace": true,
      "ignore_punctuation": false
    },
    "cue_inclusion_rule": {
      "kind": "overlap_half_open",
      "definition": "cue.start_ms < target.end_ms and cue.end_ms > target.start_ms"
    },
    "evaluation": {
      "relative_cer_improvement_min": 0.10,
      "glossary_non_regression": true,
      "cue_error_rate_delta_max": 0.05,
      "wall_time_budget_ms": 60000,
      "peak_memory_budget_bytes": 2147483648,
      "fail_closed_unaudited_gold": true
    },
    "model": {
      "name": "ggml-medium.bin",
      "path": "/models/ggml-medium.bin",
      "sha256": "<64 lowercase hexadecimal characters>",
      "bytes": 123,
      "distribution_url": "https://example.invalid/manual-source"
    },
    "whisper": {
      "binary_path": "/usr/local/bin/whisper-cli",
      "version": "1.9.1",
      "build": "metal,vulkan",
      "capabilities": ["json", "timestamps"],
      "settings": {
        "language": "ja",
        "initial_prompt": "固有名詞の候補",
        "padding_ms": 500,
        "decode": {"temperature": 0.0, "beam_size": 5},
        "output_schema": "whisper.cpp-json-v1"
      },
      "timeout_sec": 120
    },
    "cache_policy": {
      "mode": "declared",
      "run_kinds": ["cold", "warm"],
      "repeat_count": 2
    },
    "glossary": [
      {"term": "クロード", "expected_forms": ["クロード"],
       "incorrect_forms": ["フロード"]}
    ],
    "cases": [
      {
        "case_id": "case-01",
        "gold_transcript": {"path": "gold/case-01.txt"},
        "baseline_vtt": "baseline/case-01.vtt",
        "candidate_output_json": "outputs/case-01.json",
        "audio_path": "audio/case-01.wav",
        "target_range": {"start_ms": 10000, "end_ms": 30000},
        "gold_cue_anchors": [
          {"anchor_id": "a1", "start_ms": 10000, "end_ms": 15000}
        ],
        "run_kind": "warm",
        "cache_hit": true
      }
    ]
  }

``gold_transcript`` may also be an inline string or ``{"text": "..."}``.
``candidate_output_json`` may be a whisper.cpp JSON document with a
``transcription`` array, or a harness JSON document with a ``cues`` array. A case
may use ``candidate_vtt`` instead. When ``--execute-whisper`` is supplied, the
candidate output is written below the requested output directory and is parsed
with the same strict schema.

The report fixes these definitions in its own JSON:

* normalization is Unicode NFKC followed by removal of Unicode whitespace. The
  default keeps punctuation; ``ignore_punctuation`` removes only the explicit
  fixed punctuation set in :class:`NormalizationConfig`.
* cue inclusion is half-open overlap. A cue ending exactly at the target start,
  or starting exactly at the target end, is excluded.
* CER is Unicode codepoint Levenshtein distance divided by normalized gold length.
  If gold is empty, CER is 0 when hypothesis is also empty and - for a non-empty
  hypothesis - the deterministic value 1.0.
* paired CER improvement is calculated per case as
  ``(baseline_cer - candidate_cer) / baseline_cer`` and then the median of those
  paired values is taken. A zero baseline CER yields 0.0 when candidate is also
  zero, otherwise -1.0.
* cue error rate is ``(missing + duplicate) / gold_anchor_count``. An output
  cue that cannot be assigned to an unassigned gold anchor is a duplicate. With
  no anchors, the rate is 0 when there is no output cue and 1 otherwise.

The CLI writes ``<output-dir>/<benchmark_id>.report.json`` using stable JSON
serialization. It never writes to the manifest, source VTT, model path, audio
path, or any production data path.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


MANIFEST_SCHEMA = "s9-1-benchmark-manifest-v1"
REPORT_SCHEMA = "s9-1-benchmark-report-v1"
WHISPER_OUTPUT_SCHEMA = "whisper.cpp-json-v1"
WHISPER_OUTPUT_SCHEMAS = frozenset({WHISPER_OUTPUT_SCHEMA, "whisper-cli-json-full-v1"})
ALLOWED_RUN_KINDS = frozenset({"cold", "warm"})
BOUNDARY_AUDIT_SCHEMA = "s9-1-boundary-audit-v1"
BOUNDARY_AUDIT_CASE_IDS = (
    "lb4-clip002-short-proper-nouns",
    "hpe-audio-variation",
    "cgal-proper-nouns",
    "mkw-long-local-asr",
)
BOUNDARY_AUDIT_EXPECTED_OUTCOMES = {
    "lb4-clip002-short-proper-nouns": "pass",
    "mkw-long-local-asr": "internal_gap_removal_or_review_required",
    "cgal-proper-nouns": "opening_trim_or_review_required",
    "hpe-audio-variation": "opening_trim_or_review_required",
}
BOUNDARY_AUDIT_EXPECTED_CASES = {
    "lb4-clip002-short-proper-nouns": {
        "display_order": 1,
        "opening_signal": "no_material_issue_observed",
        "internal_continuity": "not_audited",
        "expected_editorial_outcome": "pass",
    },
    "hpe-audio-variation": {
        "display_order": 2,
        "opening_signal": "no_meaningful_speech_at_opening",
        "internal_continuity": "not_audited",
        "expected_editorial_outcome": "opening_trim_or_review_required",
    },
    "cgal-proper-nouns": {
        "display_order": 3,
        "opening_signal": "background_audio_without_meaningful_speech_at_opening",
        "internal_continuity": "not_audited",
        "expected_editorial_outcome": "opening_trim_or_review_required",
    },
    "mkw-long-local-asr": {
        "display_order": 4,
        "opening_signal": "meaningful_speech_present_at_opening",
        "internal_continuity": "long_internal_speech_gap",
        "expected_editorial_outcome": "internal_gap_removal_or_review_required",
    },
}


class BenchmarkError(Exception):
    """Harness の typed error。CLI と report の両方で code を保持する。"""

    code = "benchmark_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ManifestError(BenchmarkError):
    code = "invalid_manifest"


class SchemaError(BenchmarkError):
    code = "unknown_or_invalid_schema"


class FingerprintError(BenchmarkError):
    code = "fingerprint_error"


class ModelValidationError(BenchmarkError):
    code = "model_validation_failed"


class RunnerError(BenchmarkError):
    code = "runner_failed"


class BoundaryAuditError(BenchmarkError):
    code = "invalid_boundary_audit"


def _jsonable(value: Any) -> Any:
    """dataclass / Path / tuple を JSON primitive へ決定的に寄せる。"""

    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if hasattr(value, "value") and type(value).__module__ == "enum":
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("非有限の浮動小数点値は JSON に保存できません。")
    return value


def canonical_json_bytes(value: Any, *, strip_file_locations: bool = False) -> bytes:
    """sort_keys・compact・UTF-8 の canonical JSON bytes を返す。"""

    prepared = _jsonable(value)
    if strip_file_locations:
        prepared = _remove_file_locations(prepared)
    try:
        encoded = json.dumps(
            prepared,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FingerprintError("canonical JSON を生成できません。", details={"error": str(exc)}) from exc
    return encoded.encode("utf-8")


def canonical_json(value: Any, *, strip_file_locations: bool = False) -> str:
    return canonical_json_bytes(value, strip_file_locations=strip_file_locations).decode("utf-8")


_LOCATION_KEYS = frozenset(
    {
        "path",
        "audio_path",
        "baseline_vtt",
        "candidate_vtt",
        "candidate_output_json",
        "output_json",
        "output_dir",
        "binary_path",
        "manifest_path",
        "source_path",
        "mtime",
        "mtime_ns",
        "modified_at",
    }
)


def _remove_file_locations(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            child_key = str(raw_key)
            normalized_key = child_key.lower()
            if normalized_key in _LOCATION_KEYS or normalized_key.endswith("_path"):
                continue
            if normalized_key in {"mtime", "mtime_ns"} or normalized_key.endswith("_mtime"):
                continue
            result[child_key] = _remove_file_locations(raw_value, key=child_key)
        return result
    if isinstance(value, list):
        return [_remove_file_locations(item, key=key) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        return "__absolute_path__"
    return value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
    except OSError as exc:
        raise FingerprintError("ファイルの SHA-256 を計算できません。", details={"path": str(file_path), "error": str(exc)}) from exc
    return digest.hexdigest()


def file_fingerprint(path: str | Path, *, expected_sha256: str | None = None, expected_bytes: int | None = None) -> dict[str, Any]:
    """path とは別に report へ記録する入力 fingerprint。"""

    file_path = Path(path)
    if expected_bytes is not None and (
        isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0
    ):
        raise FingerprintError(
            "入力ファイルの expected_bytes が不正です。",
            details={"path": str(file_path), "expected_bytes": expected_bytes},
        )
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None
    ):
        raise FingerprintError(
            "入力ファイルの expected_sha256 が不正です。",
            details={"path": str(file_path), "expected_sha256": expected_sha256},
        )
    if not file_path.is_file():
        raise FingerprintError("入力ファイルが存在しません。", details={"path": str(file_path)})
    size = file_path.stat().st_size
    digest = sha256_file(file_path)
    if expected_bytes is not None and size != expected_bytes:
        raise FingerprintError(
            "入力ファイルの byte 数が manifest と一致しません。",
            details={"path": str(file_path), "expected_bytes": expected_bytes, "actual_bytes": size},
        )
    if expected_sha256 is not None and digest.lower() != expected_sha256.lower():
        raise FingerprintError(
            "入力ファイルの SHA-256 が manifest と一致しません。",
            details={"path": str(file_path), "expected_sha256": expected_sha256, "actual_sha256": digest},
        )
    return {"path": str(file_path), "bytes": size, "sha256": digest}


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """path / mtime を根拠にしない fixture / manifest fingerprint。"""

    return sha256_bytes(canonical_json_bytes(manifest, strip_file_locations=True))


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    actual = {str(key) for key in value}
    if actual != expected:
        raise BoundaryAuditError(
            f"{label} の field が schema と一致しません。",
            details={"expected": sorted(expected), "actual": sorted(actual)},
        )


def _require_string(value: Any, *, label: str, pattern: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BoundaryAuditError(f"{label} は空でない文字列が必要です。")
    if "<" in value or ">" in value:
        raise BoundaryAuditError(f"{label} に半角の山カッコを含められません。")
    if "\n" in value or "\r" in value or "|" in value:
        raise BoundaryAuditError(f"{label} に Markdown の行・列を壊す文字を含められません。")
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise BoundaryAuditError(f"{label} の形式が不正です。", details={"value": value})
    return value


def validate_boundary_audit(
    value: Mapping[str, Any],
    *,
    expected_case_ids: Sequence[str] | None = None,
    expected_base_fixture_fingerprint: str | None = None,
    expected_benchmark_id: str | None = None,
) -> dict[str, Any]:
    """S9-1 の部分的な境界監査 artifact を strict に検証する。

    この schema は transcript / glossary の gold を承認するものではない。
    約時刻は自然文の観察メモとして保持し、production の秒数閾値には変換しない。
    """

    if not isinstance(value, Mapping):
        raise BoundaryAuditError("boundary audit の root は object が必要です。")
    root = dict(value)
    _require_exact_keys(
        root,
        {
            "schema",
            "benchmark_id",
            "base_fixture_fingerprint",
            "audit_date",
            "auditor",
            "scope",
            "policy",
            "previous_display_order",
            "cases",
            "decision",
        },
        label="boundary audit root",
    )
    if root["schema"] != BOUNDARY_AUDIT_SCHEMA:
        raise BoundaryAuditError("boundary audit schema が不正です。", details={"schema": root["schema"]})
    benchmark_id = _require_string(root["benchmark_id"], label="benchmark_id")
    if expected_benchmark_id is not None and benchmark_id != expected_benchmark_id:
        raise BoundaryAuditError(
            "boundary audit の benchmark_id と base manifest が一致しません。",
            details={"expected": expected_benchmark_id, "actual": benchmark_id},
        )
    base_fingerprint = _require_string(
        root["base_fixture_fingerprint"],
        label="base_fixture_fingerprint",
        pattern=r"[0-9a-f]{64}",
    )
    if expected_base_fixture_fingerprint is not None and base_fingerprint != expected_base_fixture_fingerprint:
        raise BoundaryAuditError(
            "boundary audit が参照する base fixture fingerprint と manifest が一致しません。",
            details={"expected": expected_base_fixture_fingerprint, "actual": base_fingerprint},
        )
    audit_date = _require_string(root["audit_date"], label="audit_date", pattern=r"\d{4}-\d{2}-\d{2}")
    try:
        date.fromisoformat(audit_date)
    except ValueError as exc:
        raise BoundaryAuditError("audit_date は実在する ISO 日付である必要があります。", details={"audit_date": audit_date}) from exc
    if root["auditor"] != "user":
        raise BoundaryAuditError("auditor は user 固定です。")

    scope = root["scope"]
    if not isinstance(scope, Mapping):
        raise BoundaryAuditError("scope は object が必要です。")
    _require_exact_keys(
        scope,
        {"status", "source", "audited_dimensions", "not_audited_dimensions", "audio_fixture_identity"},
        label="scope",
    )
    if scope["status"] != "partial_boundary_and_continuity":
        raise BoundaryAuditError("scope.status は部分的な境界・連続性監査である必要があります。")
    if scope["source"] != "user_audio_listening_feedback":
        raise BoundaryAuditError("scope.source はユーザーの音声聴取所見である必要があります。")
    if scope["audited_dimensions"] != ["opening_boundary", "speech_continuity"]:
        raise BoundaryAuditError("scope.audited_dimensions が不正です。")
    if scope["not_audited_dimensions"] != ["transcript_full_text", "glossary", "cue_anchor_exact_times"]:
        raise BoundaryAuditError("scope.not_audited_dimensions が不正です。")
    if scope["audio_fixture_identity"] != "fixed_audio_spans_unchanged":
        raise BoundaryAuditError("固定音声 span の identity を変更する監査は許可されません。")

    policy = root["policy"]
    if not isinstance(policy, Mapping):
        raise BoundaryAuditError("policy は object が必要です。")
    _require_exact_keys(
        policy,
        {
            "kind",
            "meaningful_speech_required",
            "background_audio_is_not_meaningful_speech",
            "simple_onset_only_gate",
            "whisper_timestamp_sole_authority",
            "required_evidence",
            "production_default",
            "threshold_status",
        },
        label="policy",
    )
    expected_policy = {
        "kind": "temporary_benchmark_policy",
        "meaningful_speech_required": True,
        "background_audio_is_not_meaningful_speech": True,
        "simple_onset_only_gate": "forbidden",
        "whisper_timestamp_sole_authority": False,
        "required_evidence": ["audio_activity", "cue_context", "padding", "human_preview"],
        "production_default": "defer_to_s9_plan_or_s9_4",
        "threshold_status": "no_new_universal_seconds_threshold",
    }
    if dict(policy) != expected_policy:
        raise BoundaryAuditError("policy が今回固定した暫定 benchmark policy と一致しません。")

    expected_ids = tuple(expected_case_ids) if expected_case_ids is not None else None
    if expected_ids is not None and (
        len(expected_ids) != len(BOUNDARY_AUDIT_CASE_IDS)
        or set(expected_ids) != set(BOUNDARY_AUDIT_CASE_IDS)
    ):
        raise BoundaryAuditError(
            "S9-1 boundary audit の対象 case 集合が固定4 case と一致しません。",
            details={"expected": list(BOUNDARY_AUDIT_CASE_IDS), "actual": list(expected_ids)},
        )

    previous_order = root["previous_display_order"]
    if not isinstance(previous_order, list) or len(previous_order) != len(BOUNDARY_AUDIT_CASE_IDS):
        raise BoundaryAuditError("previous_display_order は固定4件が必要です。")
    expected_previous = [
        (1, "lb4-clip002-short-proper-nouns"),
        (2, "hpe-audio-variation"),
        (3, "cgal-proper-nouns"),
        (4, "mkw-long-local-asr"),
    ]
    previous_pairs: list[tuple[int, str]] = []
    for entry in previous_order:
        if not isinstance(entry, Mapping):
            raise BoundaryAuditError("previous_display_order の要素は object が必要です。")
        _require_exact_keys(entry, {"display_order", "case_id"}, label="previous_display_order item")
        order = entry["display_order"]
        case_id = _require_string(entry["case_id"], label="previous_display_order.case_id")
        if isinstance(order, bool) or not isinstance(order, int):
            raise BoundaryAuditError("display_order は整数が必要です。")
        previous_pairs.append((order, case_id))
    if previous_pairs != expected_previous:
        raise BoundaryAuditError(
            "previous_display_order と case ID の対応が固定所見と一致しません。",
            details={"expected": expected_previous, "actual": previous_pairs},
        )

    cases = root["cases"]
    if not isinstance(cases, list) or tuple(
        item.get("case_id") for item in cases if isinstance(item, Mapping)
    ) != BOUNDARY_AUDIT_CASE_IDS:
        raise BoundaryAuditError("boundary audit cases の順序または case 集合が固定値と異なります。")
    expected_opening = {
        "no_material_issue_observed",
        "no_meaningful_speech_at_opening",
        "background_audio_without_meaningful_speech_at_opening",
        "meaningful_speech_present_at_opening",
    }
    expected_continuity = {"not_audited", "long_internal_speech_gap"}
    expected_outcomes = set(BOUNDARY_AUDIT_EXPECTED_OUTCOMES.values())
    for case in cases:
        if not isinstance(case, Mapping):
            raise BoundaryAuditError("boundary audit case は object が必要です。")
        _require_exact_keys(
            case,
            {
                "case_id",
                "display_order",
                "source_feedback",
                "opening_signal",
                "internal_continuity",
                "approximate_timing_note",
                "expected_editorial_outcome",
            },
            label="boundary audit case",
        )
        case_id = _require_string(case["case_id"], label="case_id")
        order = case["display_order"]
        if isinstance(order, bool) or not isinstance(order, int) or order < 1:
            raise BoundaryAuditError("case.display_order は正の整数が必要です。", details={"case_id": case_id})
        expected_case = BOUNDARY_AUDIT_EXPECTED_CASES.get(case_id)
        if expected_case is None:
            raise BoundaryAuditError("boundary audit case ID が固定所見にありません。", details={"case_id": case_id})
        _require_string(case["source_feedback"], label=f"{case_id}.source_feedback")
        _require_string(case["approximate_timing_note"], label=f"{case_id}.approximate_timing_note")
        if case["opening_signal"] not in expected_opening:
            raise BoundaryAuditError("opening_signal が不正です。", details={"case_id": case_id})
        if case["internal_continuity"] not in expected_continuity:
            raise BoundaryAuditError("internal_continuity が不正です。", details={"case_id": case_id})
        outcome = case["expected_editorial_outcome"]
        if outcome not in expected_outcomes:
            raise BoundaryAuditError("expected_editorial_outcome が不正です。", details={"case_id": case_id})
        actual_contract = {
            "display_order": order,
            "opening_signal": case["opening_signal"],
            "internal_continuity": case["internal_continuity"],
            "expected_editorial_outcome": outcome,
        }
        if actual_contract != expected_case:
            raise BoundaryAuditError(
                "case の display_order / opening_signal / internal_continuity / expected_editorial_outcome が固定所見と一致しません。",
                details={"case_id": case_id, "expected": expected_case, "actual": actual_contract},
            )
    decision = root["decision"]
    if not isinstance(decision, Mapping):
        raise BoundaryAuditError("decision は object が必要です。")
    _require_exact_keys(
        decision,
        {
            "boundary_audit_status",
            "expected_editorial_outcomes_verified",
            "gold_transcript_status",
            "gold_glossary_status",
            "gold_cue_anchor_status",
            "s9_1_go",
            "s9_2_ready",
            "adopted_model",
            "no_go_reasons",
        },
        label="decision",
    )
    if decision["boundary_audit_status"] != "partial_boundary_only":
        raise BoundaryAuditError("boundary audit は transcript gold の完全監査に昇格できません。")
    if decision["expected_editorial_outcomes_verified"] is not True:
        raise BoundaryAuditError("expected editorial outcomes は machine verification 済みである必要があります。")
    for key in ("gold_transcript_status", "gold_glossary_status", "gold_cue_anchor_status"):
        if decision[key] != "unverified_provisional":
            raise BoundaryAuditError(f"{key} は unverified_provisional のままにする必要があります。")
    if decision["s9_1_go"] is not False or decision["s9_2_ready"] is not False:
        raise BoundaryAuditError("境界部分監査だけで S9-1 Go / S9-2 ready にはできません。")
    if decision["adopted_model"] is not None:
        raise BoundaryAuditError("boundary audit 後も adopted_model は未決定である必要があります。")
    if decision["no_go_reasons"] != ["gold_not_audited", "cue_proxy_blind_spot", "boundary_audit_is_partial"]:
        raise BoundaryAuditError("no_go_reasons が fail-closed 契約と一致しません。")
    return root


def boundary_audit_fingerprint(value: Mapping[str, Any]) -> str:
    """固定音声 fixture とは別に、境界監査証跡の fingerprint を返す。"""

    validated = validate_boundary_audit(value)
    return sha256_bytes(canonical_json_bytes(validated))


def evaluate_boundary_audit(
    value: Mapping[str, Any],
    *,
    expected_base_fixture_fingerprint: str | None = None,
    expected_benchmark_id: str | None = None,
) -> dict[str, Any]:
    """境界監査の機械検証結果を返す。品質 Go 判定とは分離する。"""

    validated = validate_boundary_audit(
        value,
        expected_base_fixture_fingerprint=expected_base_fixture_fingerprint,
        expected_benchmark_id=expected_benchmark_id,
    )
    return {
        "status": "pass",
        "schema": validated["schema"],
        "fingerprint": boundary_audit_fingerprint(validated),
        "base_fixture_fingerprint": validated["base_fixture_fingerprint"],
        "auditor": validated["auditor"],
        "audit_date": validated["audit_date"],
        "audit_status": validated["decision"]["boundary_audit_status"],
        "expected_editorial_outcomes_verified": validated["decision"]["expected_editorial_outcomes_verified"],
        "previous_display_order": validated["previous_display_order"],
        "cases": [
            {
                "case_id": case["case_id"],
                "display_order": case["display_order"],
                "opening_signal": case["opening_signal"],
                "internal_continuity": case["internal_continuity"],
                "source_feedback": case["source_feedback"],
                "approximate_timing_note": case["approximate_timing_note"],
                "expected_editorial_outcome": case["expected_editorial_outcome"],
            }
            for case in validated["cases"]
        ],
        "policy": validated["policy"],
        "scope": validated["scope"],
        "decision": validated["decision"],
    }


fixture_fingerprint = manifest_fingerprint


_DEFAULT_PUNCTUATION = tuple(
    "、。！？!?.,:;：；「」『』（）()［］[]【】〈〉《》・…‥〜～"
)


@dataclass(frozen=True)
class NormalizationConfig:
    """比較前に適用する、明示的で再現可能な日本語正規化。"""

    unicode_form: str = "NFKC"
    remove_whitespace: bool = True
    ignore_punctuation: bool = False
    punctuation: tuple[str, ...] = _DEFAULT_PUNCTUATION

    def __post_init__(self) -> None:
        if self.unicode_form != "NFKC":
            raise ManifestError("S9-1 の Unicode 正規化は NFKC 固定です。", details={"unicode_form": self.unicode_form})
        if not isinstance(self.remove_whitespace, bool) or not isinstance(self.ignore_punctuation, bool):
            raise ManifestError("normalization の boolean 設定が不正です。")
        if not self.punctuation or any(not isinstance(item, str) or len(item) != 1 for item in self.punctuation):
            raise ManifestError("ignore punctuation の文字集合が不正です。")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "NormalizationConfig":
        if value is None:
            return cls()
        allowed = {"unicode_form", "remove_whitespace", "ignore_punctuation", "punctuation"}
        unknown = set(value) - allowed
        if unknown:
            raise ManifestError("normalization に未知の field があります。", details={"fields": sorted(unknown)})
        punctuation = value.get("punctuation", _DEFAULT_PUNCTUATION)
        if isinstance(punctuation, str):
            punctuation = tuple(punctuation)
        elif isinstance(punctuation, Sequence):
            punctuation = tuple(punctuation)
        else:
            raise ManifestError("normalization.punctuation が不正です。")
        remove_whitespace = value.get("remove_whitespace", True)
        ignore_punctuation = value.get("ignore_punctuation", False)
        if not isinstance(remove_whitespace, bool) or not isinstance(ignore_punctuation, bool):
            raise ManifestError("normalization の remove_whitespace / ignore_punctuation は boolean が必要です。")
        return cls(
            unicode_form=str(value.get("unicode_form", "NFKC")),
            remove_whitespace=remove_whitespace,
            ignore_punctuation=ignore_punctuation,
            punctuation=punctuation,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "unicode_form": self.unicode_form,
            "remove_whitespace": self.remove_whitespace,
            "ignore_punctuation": self.ignore_punctuation,
            "punctuation": "".join(self.punctuation),
        }


def normalize_ja_text(
    text: str,
    config: NormalizationConfig | bool | None = None,
    *,
    ignore_punctuation: bool | None = None,
) -> str:
    """Unicode NFKC + 空白除去を行う。句読点は明示設定時だけ除去する。"""

    if not isinstance(text, str):
        raise TypeError("normalize_ja_text は文字列を受け取ります。")
    if isinstance(config, bool):
        settings = NormalizationConfig(ignore_punctuation=config)
    elif config is None:
        settings = NormalizationConfig()
    else:
        settings = config
    if ignore_punctuation is not None:
        settings = NormalizationConfig(
            unicode_form=settings.unicode_form,
            remove_whitespace=settings.remove_whitespace,
            ignore_punctuation=ignore_punctuation,
            punctuation=settings.punctuation,
        )
    import unicodedata

    normalized = unicodedata.normalize(settings.unicode_form, text)
    if settings.remove_whitespace:
        normalized = "".join(char for char in normalized if not char.isspace())
    if settings.ignore_punctuation:
        punctuation = set(settings.punctuation)
        normalized = "".join(char for char in normalized if char not in punctuation)
    return normalized


_TIMESTAMP_VALUE_RE = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})[\.,](?P<millis>\d{1,3})$"
)


def parse_timestamp_ms(value: str) -> int:
    """WebVTT timestamp を整数 millisecond へ変換する。"""

    if not isinstance(value, str):
        raise ValueError("timestamp は文字列で指定してください。")
    match = _TIMESTAMP_VALUE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"不正な WebVTT timestamp です: {value!r}")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    millis = int(match.group("millis").ljust(3, "0"))
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"不正な WebVTT timestamp です: {value!r}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


_VTT_TIMING_RE = re.compile(r"^\s*(?P<start>\S+)\s+-->\s+(?P<end>\S+)(?:\s+(?P<settings>.*))?\s*$")
_VTT_TAG_RE = re.compile(r"<[^>]*>")


def _clean_vtt_text(text: str) -> str:
    text = _VTT_TAG_RE.sub("", text)
    return " ".join(text.split()).strip()


@dataclass(frozen=True)
class VttCue:
    start_ms: int
    end_ms: int
    text: str
    identifier: str | None = None
    settings: str = ""

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("VTT cue の時刻範囲が不正です。")
        if not self.text:
            raise ValueError("VTT cue の本文が空です。")

    def to_dict(self) -> dict[str, Any]:
        result = {"start_ms": self.start_ms, "end_ms": self.end_ms, "text": self.text}
        if self.identifier is not None:
            result["identifier"] = self.identifier
        if self.settings:
            result["settings"] = self.settings
        return result


Cue = VttCue


def parse_vtt(content: str) -> list[VttCue]:
    """WebVTT cue を tag 除去済み・整数 ms で返す。重複 cue は保持する。"""

    if not isinstance(content, str):
        raise TypeError("parse_vtt は文字列を受け取ります。")
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[VttCue] = []
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            break
        block: list[str] = []
        while index < len(lines) and lines[index].strip():
            block.append(lines[index].strip())
            index += 1
        if not block:
            continue
        if block[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing_index = next((i for i, line in enumerate(block) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = _VTT_TIMING_RE.fullmatch(block[timing_index])
        if timing is None:
            continue
        try:
            start_ms = parse_timestamp_ms(timing.group("start"))
            end_ms = parse_timestamp_ms(timing.group("end"))
        except ValueError:
            continue
        if end_ms <= start_ms:
            continue
        text = _clean_vtt_text(" ".join(block[timing_index + 1 :]))
        if not text:
            continue
        identifier = block[0] if timing_index == 1 else None
        cues.append(
            VttCue(
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                identifier=identifier,
                settings=timing.group("settings") or "",
            )
        )
    return cues


def parse_vtt_file(path: str | Path) -> list[VttCue]:
    return parse_vtt(Path(path).read_text(encoding="utf-8"))


def levenshtein_distance(gold: str, hypothesis: str) -> int:
    """Unicode codepoint 列として Levenshtein distance を計算する。"""

    if not isinstance(gold, str) or not isinstance(hypothesis, str):
        raise TypeError("Levenshtein distance は文字列同士で計算します。")
    if len(gold) < len(hypothesis):
        gold, hypothesis = hypothesis, gold
    previous = list(range(len(hypothesis) + 1))
    for row_index, gold_char in enumerate(gold, start=1):
        current = [row_index]
        for column_index, hypothesis_char in enumerate(hypothesis, start=1):
            insertion = current[column_index - 1] + 1
            deletion = previous[column_index] + 1
            substitution = previous[column_index - 1] + (gold_char != hypothesis_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


unicode_levenshtein = levenshtein_distance


def character_error_rate(
    gold: str,
    hypothesis: str,
    *,
    normalization: NormalizationConfig | None = None,
) -> float:
    settings = normalization or NormalizationConfig()
    normalized_gold = normalize_ja_text(gold, settings)
    normalized_hypothesis = normalize_ja_text(hypothesis, settings)
    if not normalized_gold:
        return 0.0 if not normalized_hypothesis else 1.0
    return levenshtein_distance(normalized_gold, normalized_hypothesis) / len(normalized_gold)


cer = character_error_rate


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    expected_forms: tuple[str, ...] = ()
    incorrect_forms: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any]) -> "GlossaryEntry":
        if isinstance(value, str):
            return cls(term=value, expected_forms=(value,))
        if not isinstance(value, Mapping):
            raise ManifestError("glossary の各要素は文字列または object です。")
        allowed = {"term", "expected_forms", "variants", "incorrect_forms"}
        unknown = set(value) - allowed
        if unknown:
            raise ManifestError("glossary に未知の field があります。", details={"fields": sorted(unknown)})
        term = value.get("term")
        if not isinstance(term, str) or not term:
            raise ManifestError("glossary.term は空でない文字列が必要です。")
        forms_value = value.get("expected_forms", value.get("variants", [term]))
        incorrect_value = value.get("incorrect_forms", [])
        if isinstance(forms_value, str):
            forms_value = [forms_value]
        if isinstance(incorrect_value, str):
            incorrect_value = [incorrect_value]
        if not isinstance(forms_value, Sequence) or not isinstance(incorrect_value, Sequence):
            raise ManifestError("glossary の forms が不正です。", details={"term": term})
        return cls(term=term, expected_forms=tuple(forms_value), incorrect_forms=tuple(incorrect_value))


def _normalized_glossary(entries: Iterable[str | Mapping[str, Any] | GlossaryEntry], settings: NormalizationConfig) -> list[GlossaryEntry]:
    normalized: list[GlossaryEntry] = []
    for raw in entries:
        entry = raw if isinstance(raw, GlossaryEntry) else GlossaryEntry.from_value(raw)
        expected = tuple(dict.fromkeys(normalize_ja_text(value, settings) for value in (entry.expected_forms or (entry.term,))))
        incorrect = tuple(dict.fromkeys(normalize_ja_text(value, settings) for value in entry.incorrect_forms))
        if any(not value for value in expected):
            raise ManifestError("glossary の expected form が空になります。", details={"term": entry.term})
        normalized.append(
            GlossaryEntry(
                term=normalize_ja_text(entry.term, settings),
                expected_forms=expected,
                incorrect_forms=incorrect,
            )
        )
    return normalized


def glossary_exact_match(
    gold: str,
    hypothesis: str,
    glossary: Iterable[str | Mapping[str, Any] | GlossaryEntry],
    *,
    normalization: NormalizationConfig | None = None,
) -> dict[str, Any]:
    """固定 glossary を gold から独立に受け、found/missing/incorrect を数える。

    ``expected_forms`` のうち gold に実際に現れるものだけをその case の期待集合
    とする。glossary 自体は gold の本文から生成・推測しない。
    """

    settings = normalization or NormalizationConfig()
    normalized_gold = normalize_ja_text(gold, settings)
    normalized_hypothesis = normalize_ja_text(hypothesis, settings)
    per_term: list[dict[str, Any]] = []
    total_expected = total_found = total_missing = total_incorrect = 0
    exact_terms = 0
    for entry in _normalized_glossary(glossary, settings):
        expected = sorted(form for form in entry.expected_forms if form in normalized_gold)
        found = sorted(form for form in expected if form in normalized_hypothesis)
        missing = sorted(set(expected) - set(found))
        incorrect = sorted(form for form in entry.incorrect_forms if form in normalized_hypothesis and expected)
        exact = bool(expected) and not missing and not incorrect
        total_expected += len(expected)
        total_found += len(found)
        total_missing += len(missing)
        total_incorrect += len(incorrect)
        exact_terms += int(exact)
        per_term.append(
            {
                "term": entry.term,
                "expected": expected,
                "found": found,
                "missing": missing,
                "incorrect": incorrect,
                "exact_match": exact,
            }
        )
    return {
        "expected": total_expected,
        "found": total_found,
        "missing": total_missing,
        "incorrect": total_incorrect,
        "exact_match_terms": exact_terms,
        "term_count": len(per_term),
        "per_term": per_term,
    }


glossary_metrics = glossary_exact_match


@dataclass(frozen=True)
class TimeRange:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if isinstance(self.start_ms, bool) or isinstance(self.end_ms, bool):
            raise ValueError("range の時刻は整数で指定してください。")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("range の時刻範囲が不正です。")

    @classmethod
    def from_value(cls, value: "TimeRange | Mapping[str, Any] | Sequence[int]") -> "TimeRange":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            start = value.get("start_ms", value.get("start"))
            end = value.get("end_ms", value.get("end"))
        elif isinstance(value, Sequence) and len(value) == 2:
            start, end = value
        else:
            raise ManifestError("range は start_ms/end_ms object または 2 要素配列です。")
        if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
            raise ManifestError("range の時刻は整数 millisecond で指定してください。")
        return cls(start, end)

    def to_dict(self) -> dict[str, int]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms}


TargetRange = TimeRange


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and left_end > right_start


def select_cues_by_overlap(cues: Iterable[VttCue], target: TimeRange | Mapping[str, Any] | Sequence[int]) -> list[VttCue]:
    selected_range = TimeRange.from_value(target)
    return [
        cue
        for cue in cues
        if _overlaps(cue.start_ms, cue.end_ms, selected_range.start_ms, selected_range.end_ms)
    ]


select_cues_for_range = select_cues_by_overlap


@dataclass(frozen=True)
class CueAnchor:
    anchor_id: str
    start_ms: int
    end_ms: int

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | VttCue | "CueAnchor", index: int = 0) -> "CueAnchor":
        if isinstance(value, cls):
            return value
        if isinstance(value, VttCue):
            return cls(value.identifier or f"anchor-{index + 1}", value.start_ms, value.end_ms)
        if not isinstance(value, Mapping):
            raise ManifestError("gold_cue_anchors の各要素は object です。")
        identifier = value.get("anchor_id", value.get("id", f"anchor-{index + 1}"))
        start = value.get("start_ms", value.get("start"))
        end = value.get("end_ms", value.get("end"))
        if not isinstance(identifier, str) or not identifier:
            raise ManifestError("cue anchor の anchor_id が不正です。")
        if not isinstance(start, int) or not isinstance(end, int):
            raise ManifestError("cue anchor の時刻は整数 millisecond で指定してください。")
        try:
            return cls(identifier, start, end)
        except ValueError as exc:
            raise ManifestError("cue anchor の時刻範囲が不正です。", details={"anchor_id": identifier}) from exc


def cue_inclusion_metrics(
    output_cues: Iterable[VttCue],
    gold_anchors: Iterable[CueAnchor | VttCue | Mapping[str, Any]],
    *,
    target: TimeRange | Mapping[str, Any] | Sequence[int] | None = None,
) -> dict[str, Any]:
    """固定 gold anchor に対する missing / duplicate を決定的に数える。

    output cue は入力順に処理し、同じ identifier があれば identifier を優先し、
    それ以外は半開区間 overlap が最大で、同率なら開始時刻が早い未割当 anchor
    に greedy に割り当てる。割り当て不能な cue と二つ目以降の cue は duplicate。
    """

    anchors = [CueAnchor.from_value(value, index) for index, value in enumerate(gold_anchors)]
    output = list(output_cues)
    if target is not None:
        selected_range = TimeRange.from_value(target)
        anchors = [
            anchor
            for anchor in anchors
            if _overlaps(anchor.start_ms, anchor.end_ms, selected_range.start_ms, selected_range.end_ms)
        ]
        output = select_cues_by_overlap(output, selected_range)
    assigned: set[int] = set()
    duplicate = 0
    assigned_ids: list[str] = []
    for cue in output:
        candidates = [
            index
            for index, anchor in enumerate(anchors)
            if index not in assigned
            and ((cue.identifier and cue.identifier == anchor.anchor_id)
                 or _overlaps(cue.start_ms, cue.end_ms, anchor.start_ms, anchor.end_ms))
        ]
        if candidates:
            chosen = min(
                candidates,
                key=lambda index: (
                    -max(
                        0,
                        min(cue.end_ms, anchors[index].end_ms)
                        - max(cue.start_ms, anchors[index].start_ms),
                    ),
                    anchors[index].start_ms,
                    index,
                ),
            )
            assigned.add(chosen)
            assigned_ids.append(anchors[chosen].anchor_id)
        else:
            duplicate += 1
    missing_ids = [anchor.anchor_id for index, anchor in enumerate(anchors) if index not in assigned]
    missing = len(missing_ids)
    denominator = len(anchors)
    error_rate = (missing + duplicate) / denominator if denominator else (0.0 if not output else 1.0)
    return {
        "gold_anchor_count": denominator,
        "output_cue_count": len(output),
        "missing": missing,
        "missing_anchor_ids": missing_ids,
        "duplicate": duplicate,
        "assigned_anchor_ids": assigned_ids,
        "error_count": missing + duplicate,
        "error_rate": error_rate,
        "rule": "overlap_half_open",
    }


cue_metrics = cue_inclusion_metrics


def deduplicate_progressive_timed(cues: Iterable[VttCue]) -> list[VttCue]:
    """production の ``deduplicate_progressive`` と同じ timed cue 処理を行う。

    YouTube VTT の各 raw cue は前 cue の本文を含むことがあるため、時刻の
    window ではなく入力順と本文だけで差分を取り出す。range selection は
    この関数の呼び出し元で全 cue に適用した後に行う。
    """

    result: list[VttCue] = []
    prev_text = ""
    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue

        if prev_text:
            if text == prev_text:
                continue
            if text.startswith(prev_text):
                delta = text[len(prev_text) :].strip()
                if delta:
                    result.append(replace(cue, text=delta))
                prev_text = text
                continue
            if prev_text in text:
                index = text.find(prev_text)
                delta = (text[:index] + text[index + len(prev_text) :]).strip()
                if delta:
                    result.append(replace(cue, text=delta))
                prev_text = text
                continue

        result.append(cue)
        prev_text = text
    return result


def dedupe_near_duplicate_cues(
    cues: Iterable[VttCue],
    *,
    normalization: NormalizationConfig | None = None,
    window_ms: int = 10,
) -> list[VttCue]:
    """互換 API。実体は production parity の progressive dedupe である。

    ``normalization`` と ``window_ms`` は旧 API の引数として受け付けるが、
    production と同じく時刻 window や比較用正規化は適用しない。
    """

    return deduplicate_progressive_timed(cues)


deduplicate_progressive = deduplicate_progressive_timed
dedupe_vtt_boundary_duplicates = deduplicate_progressive_timed


def relative_cer_improvement(baseline_cer: float, candidate_cer: float) -> float:
    if not math.isfinite(baseline_cer) or not math.isfinite(candidate_cer) or baseline_cer < 0 or candidate_cer < 0:
        raise ValueError("CER は有限の非負値で指定してください。")
    if baseline_cer == 0:
        return 0.0 if candidate_cer == 0 else -1.0
    return (baseline_cer - candidate_cer) / baseline_cer


def paired_median_cer_relative_improvement(
    baseline_cers: Sequence[float] | Sequence[Mapping[str, Any]],
    candidate_cers: Sequence[float] | None = None,
) -> float:
    if candidate_cers is None:
        pairs = []
        for item in baseline_cers:  # type: ignore[union-attr]
            if not isinstance(item, Mapping):
                raise TypeError("candidate CER が必要です。")
            pairs.append((float(item["baseline_cer"]), float(item["candidate_cer"])))
    else:
        if len(baseline_cers) != len(candidate_cers):
            raise ValueError("baseline と candidate の case 数が一致しません。")
        pairs = list(zip((float(value) for value in baseline_cers), (float(value) for value in candidate_cers)))
    if not pairs:
        raise ValueError("paired CER には 1 件以上の case が必要です。")
    return statistics.median(relative_cer_improvement(baseline, candidate) for baseline, candidate in pairs)


@dataclass(frozen=True)
class GateConfig:
    relative_cer_improvement_min: float = 0.10
    glossary_non_regression: bool = True
    cue_error_rate_delta_max: float = 0.05
    wall_time_budget_ms: int | None = None
    peak_memory_budget_bytes: int | None = None
    fail_closed_unaudited_gold: bool = True
    wall_time_budget_ms_by_run_kind: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "GateConfig":
        value = value or {}
        allowed = {
            "relative_cer_improvement_min",
            "glossary_non_regression",
            "cue_error_rate_delta_max",
            "wall_time_budget_ms",
            "peak_memory_budget_bytes",
            "fail_closed_unaudited_gold",
            "wall_time_budget_ms_by_run_kind",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ManifestError("evaluation に未知の field があります。", details={"fields": sorted(unknown)})
        wall = value.get("wall_time_budget_ms")
        memory = value.get("peak_memory_budget_bytes")
        if wall is not None and (not isinstance(wall, int) or isinstance(wall, bool) or wall <= 0):
            raise ManifestError("wall_time_budget_ms は正の整数が必要です。")
        if memory is not None and (not isinstance(memory, int) or isinstance(memory, bool) or memory <= 0):
            raise ManifestError("peak_memory_budget_bytes は正の整数が必要です。")
        per_kind_value = value.get("wall_time_budget_ms_by_run_kind", {})
        if not isinstance(per_kind_value, Mapping):
            raise ManifestError("wall_time_budget_ms_by_run_kind は object が必要です。")
        per_kind: list[tuple[str, int]] = []
        for kind, budget in sorted(per_kind_value.items()):
            if kind not in ALLOWED_RUN_KINDS or not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
                raise ManifestError("run kind 別 wall time budget が不正です。", details={"run_kind": kind})
            per_kind.append((kind, budget))
        config = cls(
            relative_cer_improvement_min=float(value.get("relative_cer_improvement_min", 0.10)),
            glossary_non_regression=bool(value.get("glossary_non_regression", True)),
            cue_error_rate_delta_max=float(value.get("cue_error_rate_delta_max", 0.05)),
            wall_time_budget_ms=wall,
            peak_memory_budget_bytes=memory,
            fail_closed_unaudited_gold=bool(value.get("fail_closed_unaudited_gold", True)),
            wall_time_budget_ms_by_run_kind=tuple(per_kind),
        )
        if (
            not math.isfinite(config.relative_cer_improvement_min)
            or not math.isfinite(config.cue_error_rate_delta_max)
            or config.relative_cer_improvement_min < 0
            or config.cue_error_rate_delta_max < 0
        ):
            raise ManifestError("gate の閾値は非負で指定してください。")
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_cer_improvement_min": self.relative_cer_improvement_min,
            "glossary_non_regression": self.glossary_non_regression,
            "cue_error_rate_delta_max": self.cue_error_rate_delta_max,
            "wall_time_budget_ms": self.wall_time_budget_ms,
            "peak_memory_budget_bytes": self.peak_memory_budget_bytes,
            "fail_closed_unaudited_gold": self.fail_closed_unaudited_gold,
            "wall_time_budget_ms_by_run_kind": {key: value for key, value in self.wall_time_budget_ms_by_run_kind},
        }


def _metric_value(metrics: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in metrics:
            return metrics[name]
    return None


def evaluate_gates(
    cases: Sequence[Mapping[str, Any]],
    *,
    gate_config: GateConfig | Mapping[str, Any] | None = None,
    gold_audit_status: str = "audited",
    fail_closed_unaudited_gold: bool | None = None,
) -> dict[str, Any]:
    """事前宣言 gate を評価し、Go / No-Go を fail closed で返す。"""

    config = gate_config if isinstance(gate_config, GateConfig) else GateConfig.from_mapping(gate_config)
    fail_closed = config.fail_closed_unaudited_gold if fail_closed_unaudited_gold is None else fail_closed_unaudited_gold
    reasons: list[dict[str, Any]] = []
    gate_results: dict[str, Any] = {}
    valid_case_count = len(cases)
    if not cases:
        reasons.append({"code": "no_cases", "message": "比較対象 case がありません。"})
    pair_values: list[float] = []
    for case in cases:
        baseline = case.get("baseline", {})
        candidate = case.get("candidate", {})
        if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            reasons.append({"code": "invalid_case_metrics", "message": "case metrics が object ではありません。", "case_id": case.get("case_id")})
            continue
        if candidate.get("status", "ok") != "ok":
            reasons.append({"code": "candidate_not_success", "message": "candidate の出力が成功していません。", "case_id": case.get("case_id"), "error": candidate.get("error")})
            continue
        try:
            pair_values.append(relative_cer_improvement(float(baseline["cer"]), float(candidate["cer"])))
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append({"code": "missing_cer", "message": "CER が case metrics にありません。", "case_id": case.get("case_id"), "error": str(exc)})
    median_improvement = statistics.median(pair_values) if pair_values else None
    cer_passed = median_improvement is not None and median_improvement >= config.relative_cer_improvement_min and len(pair_values) == valid_case_count
    gate_results["relative_cer"] = {
        "passed": cer_passed,
        "value": median_improvement,
        "threshold": config.relative_cer_improvement_min,
        "definition": "median per-case (baseline_cer - candidate_cer) / baseline_cer; baseline 0 => 0 or -1",
    }
    if not cer_passed:
        reasons.append({"code": "cer_gate_failed", "message": "paired median CER relative improvement が閾値未達です。"})

    baseline_glossary = {"found": 0, "missing": 0, "incorrect": 0, "expected": 0, "exact_match_terms": 0}
    candidate_glossary = {"found": 0, "missing": 0, "incorrect": 0, "expected": 0, "exact_match_terms": 0}
    baseline_cue_errors = baseline_cue_anchors = candidate_cue_errors = candidate_cue_anchors = 0
    for case in cases:
        for side, accumulator in (("baseline", baseline_glossary), ("candidate", candidate_glossary)):
            metrics = case.get(side, {})
            glossary = metrics.get("glossary", {}) if isinstance(metrics, Mapping) else {}
            if isinstance(glossary, Mapping):
                for key in accumulator:
                    accumulator[key] += int(glossary.get(key, 0) or 0)
        baseline_metrics = case.get("baseline", {})
        candidate_metrics = case.get("candidate", {})
        base_cue = baseline_metrics.get("cue", {}) if isinstance(baseline_metrics, Mapping) else {}
        cand_cue = candidate_metrics.get("cue", {}) if isinstance(candidate_metrics, Mapping) else {}
        if isinstance(base_cue, Mapping):
            baseline_cue_errors += int(base_cue.get("error_count", 0) or 0)
            baseline_cue_anchors += int(base_cue.get("gold_anchor_count", 0) or 0)
        if isinstance(cand_cue, Mapping):
            candidate_cue_errors += int(cand_cue.get("error_count", 0) or 0)
            candidate_cue_anchors += int(cand_cue.get("gold_anchor_count", 0) or 0)
    glossary_passed = True
    if config.glossary_non_regression:
        glossary_passed = (
            candidate_glossary["found"] >= baseline_glossary["found"]
            and candidate_glossary["missing"] <= baseline_glossary["missing"]
            and candidate_glossary["incorrect"] <= baseline_glossary["incorrect"]
        )
    gate_results["glossary_exact_match"] = {
        "passed": glossary_passed,
        "baseline": baseline_glossary,
        "candidate": candidate_glossary,
        "definition": "found non-regression, missing/incorrect non-increase over the fixed glossary expected set",
    }
    if not glossary_passed:
        reasons.append({"code": "glossary_non_regression_failed", "message": "glossary exact match が baseline より悪化しています。"})

    baseline_cue_rate = baseline_cue_errors / baseline_cue_anchors if baseline_cue_anchors else (0.0 if candidate_cue_anchors == 0 else 0.0)
    candidate_cue_rate = candidate_cue_errors / candidate_cue_anchors if candidate_cue_anchors else (0.0 if candidate_cue_errors == 0 else 1.0)
    cue_passed = candidate_cue_anchors == baseline_cue_anchors and candidate_cue_rate <= baseline_cue_rate + config.cue_error_rate_delta_max
    gate_results["cue_missing_duplicate"] = {
        "passed": cue_passed,
        "baseline_rate": baseline_cue_rate,
        "candidate_rate": candidate_cue_rate,
        "allowed_candidate_rate": baseline_cue_rate + config.cue_error_rate_delta_max,
        "baseline_anchors": baseline_cue_anchors,
        "candidate_anchors": candidate_cue_anchors,
        "definition": "aggregate (missing + duplicate) / gold_anchor_count; candidate <= baseline + delta",
    }
    if not cue_passed:
        reasons.append({"code": "cue_gate_failed", "message": "cue の missing / duplicate rate が baseline + 許容差を超えています。"})

    wall_values = [
        _metric_value(case.get("candidate", {}), "wall_time_ms", "duration_ms")
        for case in cases
        if isinstance(case.get("candidate", {}), Mapping)
    ]
    wall_budgets = dict(config.wall_time_budget_ms_by_run_kind)
    wall_checks: list[dict[str, Any]] = []
    for case in cases:
        candidate = case.get("candidate", {})
        if not isinstance(candidate, Mapping):
            continue
        value = _metric_value(candidate, "wall_time_ms", "duration_ms")
        kind = candidate.get("run_kind")
        budget = wall_budgets.get(kind, config.wall_time_budget_ms)
        wall_checks.append({"case_id": case.get("case_id"), "run_kind": kind, "value_ms": value, "budget_ms": budget})
    if config.wall_time_budget_ms is None:
        wall_passed = bool(wall_checks) and all(
            check["budget_ms"] is None
            or (isinstance(check["value_ms"], (int, float)) and check["value_ms"] >= 0 and check["value_ms"] <= check["budget_ms"])
            for check in wall_checks
        )
        # A run-kind-specific budget is still a declared wall gate.
        if wall_budgets:
            wall_passed = bool(wall_checks) and all(
                check["budget_ms"] is not None
                and isinstance(check["value_ms"], (int, float))
                and check["value_ms"] >= 0
                and check["value_ms"] <= check["budget_ms"]
                for check in wall_checks
            )
        wall_reason = None
    else:
        wall_passed = bool(wall_checks) and all(
            check["budget_ms"] is not None
            and isinstance(check["value_ms"], (int, float))
            and check["value_ms"] >= 0
            and check["value_ms"] <= check["budget_ms"]
            for check in wall_checks
        )
        wall_reason = "candidate の wall time が未記録または budget 超過です。" if not wall_passed else None
    gate_results["wall_time"] = {"passed": wall_passed, "values_ms": wall_values, "budget_ms": config.wall_time_budget_ms, "checks": wall_checks}
    if wall_reason:
        reasons.append({"code": "wall_time_budget_failed", "message": wall_reason})

    memory_values = [
        _metric_value(case.get("candidate", {}), "peak_memory_bytes", "peak_rss_bytes")
        for case in cases
        if isinstance(case.get("candidate", {}), Mapping)
    ]
    if config.peak_memory_budget_bytes is None:
        memory_passed = True
    else:
        memory_passed = bool(memory_values) and all(isinstance(value, (int, float)) and value >= 0 and value <= config.peak_memory_budget_bytes for value in memory_values)
    gate_results["peak_memory"] = {"passed": memory_passed, "values_bytes": memory_values, "budget_bytes": config.peak_memory_budget_bytes}
    if not memory_passed:
        reasons.append({"code": "peak_memory_budget_failed", "message": "candidate の peak memory が未記録または budget 超過です。"})

    audit_passed = gold_audit_status == "audited" or not fail_closed
    gate_results["gold_audit"] = {
        "passed": audit_passed,
        "status": gold_audit_status,
        "fail_closed_unaudited_gold": fail_closed,
    }
    if not audit_passed:
        reasons.append({"code": "gold_not_audited", "message": "gold audit status が audited ではないため fail closed です。"})

    go = not reasons and all(bool(result.get("passed")) for result in gate_results.values())
    return {
        "status": "go" if go else "no_go",
        "go": go,
        "metrics_status": "audited" if gold_audit_status == "audited" else "provisional",
        "paired_median_cer_relative_improvement": median_improvement,
        "gates": gate_results,
        "reasons": reasons,
    }


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"{label} は object で指定してください。")
    return value


@dataclass(frozen=True)
class ModelMetadata:
    name: str
    path: str
    sha256: str
    bytes: int
    distribution_url: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelMetadata":
        required = {"name", "path", "sha256", "bytes", "distribution_url"}
        missing = sorted(required - set(value))
        if missing:
            raise ManifestError("model metadata の必須 field が不足しています。", details={"missing": missing})
        if not all(isinstance(value[key], str) for key in ("name", "path", "sha256", "distribution_url")):
            raise ManifestError("model metadata の文字列 field が不正です。")
        size = value["bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError("model.bytes は 0 以上の整数が必要です。")
        return cls(value["name"], value["path"], value["sha256"], size, value["distribution_url"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "distribution_url": self.distribution_url,
        }


def validate_model_metadata(value: ModelMetadata | Mapping[str, Any]) -> dict[str, Any]:
    metadata = value if isinstance(value, ModelMetadata) else ModelMetadata.from_mapping(value)
    if not metadata.name or not metadata.path or not metadata.sha256 or not metadata.distribution_url:
        raise ModelValidationError("model metadata の name/path/sha256/distribution_url は空にできません。")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", metadata.sha256):
        raise ModelValidationError("model.sha256 は 64 桁の hexadecimal で指定してください。", details={"path": metadata.path})
    path = Path(metadata.path)
    if not path.is_file():
        raise ModelValidationError("manifest の model path が存在しません。", details={"path": str(path)})
    actual_bytes = path.stat().st_size
    if actual_bytes != metadata.bytes:
        raise ModelValidationError(
            "model file の byte 数が manifest と一致しません。",
            details={"path": str(path), "expected_bytes": metadata.bytes, "actual_bytes": actual_bytes},
        )
    actual_sha = sha256_file(path)
    if actual_sha.lower() != metadata.sha256.lower():
        raise ModelValidationError(
            "model file の SHA-256 が manifest と一致しません。",
            details={"path": str(path), "expected_sha256": metadata.sha256, "actual_sha256": actual_sha},
        )
    result = metadata.to_dict()
    result["sha256"] = actual_sha
    result["validated_bytes"] = actual_bytes
    return result


@dataclass(frozen=True)
class WhisperSettings:
    language: str = "ja"
    initial_prompt: str = ""
    padding_ms: int = 0
    decode: tuple[tuple[str, Any], ...] = (("temperature", 0.0), ("beam_size", 5))
    output_schema: str = WHISPER_OUTPUT_SCHEMA

    _ALLOWED_DECODE = frozenset(
        {
            "temperature",
            "temperature_inc",
            "beam_size",
            "best_of",
            "threads",
            "processors",
            "max_context",
            "max_len",
            "no_speech_threshold",
            "no_fallback",
            "vad",
        }
    )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WhisperSettings":
        value = value or {}
        allowed = {"language", "initial_prompt", "padding_ms", "decode", "output_schema"}
        unknown = set(value) - allowed
        if unknown:
            raise ManifestError("whisper.settings に未知の field があります。", details={"fields": sorted(unknown)})
        language = value.get("language", "ja")
        prompt = value.get("initial_prompt", "")
        padding = value.get("padding_ms", 0)
        if language != "ja":
            raise ManifestError("S9-1 の whisper language は ja 固定です。")
        if not isinstance(prompt, str) or not isinstance(padding, int) or isinstance(padding, bool) or padding < 0:
            raise ManifestError("whisper.settings の initial_prompt / padding_ms が不正です。")
        decode_value = value.get("decode", {"temperature": 0.0, "beam_size": 5})
        if not isinstance(decode_value, Mapping):
            raise ManifestError("whisper.settings.decode は object で指定してください。")
        unknown_decode = set(decode_value) - cls._ALLOWED_DECODE
        if unknown_decode:
            raise ManifestError("whisper.settings.decode に未知の field があります。", details={"fields": sorted(unknown_decode)})
        decode: list[tuple[str, Any]] = []
        for key in sorted(decode_value):
            raw = decode_value[key]
            if key in {"no_fallback", "vad"}:
                if not isinstance(raw, bool):
                    raise ManifestError("whisper decode の boolean value が不正です。", details={"field": key})
            elif isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
                raise ManifestError("whisper decode value は有限の number が必要です。", details={"field": key})
            elif key == "processors" and raw != 1:
                raise ManifestError("S9-1 の whisper processors は 1 固定です。", details={"processors": raw})
            decode.append((key, raw))
        output_schema = value.get("output_schema", WHISPER_OUTPUT_SCHEMA)
        if output_schema not in WHISPER_OUTPUT_SCHEMAS:
            raise ManifestError("whisper の output_schema が未知です。", details={"output_schema": output_schema})
        return cls(language, prompt, padding, tuple(decode), output_schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "initial_prompt": self.initial_prompt,
            "padding_ms": self.padding_ms,
            "decode": {key: value for key, value in self.decode},
            "output_schema": self.output_schema,
        }


@dataclass(frozen=True)
class CachePolicy:
    mode: str = "declared"
    run_kinds: tuple[str, ...] = ("cold", "warm")
    repeat_count: int = 2
    cold_definition: str | None = None
    warm_definition: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "CachePolicy":
        value = value or {}
        allowed = {"mode", "run_kinds", "repeat_count", "cold_definition", "warm_definition"}
        unknown = set(value) - allowed
        if unknown:
            raise ManifestError("cache_policy に未知の field があります。", details={"fields": sorted(unknown)})
        raw_kinds = value.get("run_kinds", ["cold", "warm"])
        if isinstance(raw_kinds, str):
            raw_kinds = [raw_kinds]
        if not isinstance(raw_kinds, Sequence) or not raw_kinds:
            raise ManifestError("cache_policy.run_kinds は 1 件以上必要です。")
        kinds = tuple(str(item) for item in raw_kinds)
        if any(kind not in ALLOWED_RUN_KINDS for kind in kinds):
            raise ManifestError("cache_policy.run_kinds は cold / warm のみ許可します。")
        repeat_count = value.get("repeat_count", len(kinds))
        if not isinstance(repeat_count, int) or isinstance(repeat_count, bool) or repeat_count < 1:
            raise ManifestError("cache_policy.repeat_count は正の整数が必要です。")
        cold_definition = value.get("cold_definition")
        warm_definition = value.get("warm_definition")
        if cold_definition is not None and not isinstance(cold_definition, str):
            raise ManifestError("cache_policy.cold_definition は文字列が必要です。")
        if warm_definition is not None and not isinstance(warm_definition, str):
            raise ManifestError("cache_policy.warm_definition は文字列が必要です。")
        return cls(str(value.get("mode", "declared")), kinds, repeat_count, cold_definition, warm_definition)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"mode": self.mode, "run_kinds": list(self.run_kinds), "repeat_count": self.repeat_count}
        if self.cold_definition is not None:
            result["cold_definition"] = self.cold_definition
        if self.warm_definition is not None:
            result["warm_definition"] = self.warm_definition
        return result


@dataclass(frozen=True)
class WhisperRuntimeMetadata:
    binary_path: str
    version: str
    build: str
    capabilities: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WhisperRuntimeMetadata":
        required = {"binary_path", "version", "build"}
        missing = sorted(required - set(value))
        if missing:
            raise ManifestError("whisper runtime metadata の必須 field が不足しています。", details={"missing": missing})
        capabilities = value.get("capabilities", [])
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        if not isinstance(capabilities, Sequence):
            raise ManifestError("whisper.capabilities が不正です。")
        return cls(str(value["binary_path"]), str(value["version"]), str(value["build"]), tuple(str(item) for item in capabilities))

    def to_dict(self) -> dict[str, Any]:
        return {"binary_path": self.binary_path, "version": self.version, "build": self.build, "capabilities": list(self.capabilities)}


def _format_number(value: int | float) -> str:
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def build_whisper_argv(
    *,
    binary_path: str | Path,
    model_path: str | Path,
    audio_path: str | Path,
    output_json_path: str | Path,
    settings: WhisperSettings | Mapping[str, Any] | None = None,
    target_range: TimeRange | Mapping[str, Any] | Sequence[int] | None = None,
) -> list[str]:
    """whisper-cli へ渡す固定 argv。任意の command / arg list は受け取らない。"""

    config = settings if isinstance(settings, WhisperSettings) else WhisperSettings.from_mapping(settings)
    output_path = Path(output_json_path)
    output_prefix = output_path.with_suffix("")
    duration_ms: int | None = None
    if target_range is not None:
        selected = TimeRange.from_value(target_range)
        duration_ms = selected.end_ms - selected.start_ms + config.padding_ms * 2
    argv = [
        str(binary_path),
        "--model",
        str(model_path),
        "--file",
        str(audio_path),
        "--language",
        "ja",
        "--prompt",
        config.initial_prompt,
        "--output-json",
        "--output-file",
        str(output_prefix),
        "--no-prints",
        "-p",
        "1",
    ]
    if config.output_schema == "whisper-cli-json-full-v1":
        argv.insert(argv.index("--output-file"), "--output-json-full")
    if duration_ms is not None:
        argv.extend(["--offset-t", "0", "--duration", _format_number(duration_ms)])
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
    for key, value in config.decode:
        if key == "processors":
            continue
        if key == "no_fallback":
            if value:
                argv.append("--no-fallback")
            continue
        if key == "vad":
            # whisper-cli 1.9.1 has no --no-vad switch; false is the default.
            # A true value is rejected rather than emitting an unverified flag.
            if value:
                raise ManifestError("S9-1 では VAD を有効化できません。")
            continue
        argv.extend([decode_flags[key], _format_number(value)])
    return argv


def _decode_process_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _parse_peak_rss(stderr: str) -> int | None:
    # macOS /usr/bin/time -l emits a space-separated label without a colon.
    match = re.search(
        r"maximum resident set size\s*:?\s*(\d+)|\b(\d+)\s+maximum resident set size",
        stderr,
        flags=re.IGNORECASE,
    )
    return int(match.group(1) or match.group(2)) if match else None


def _coerce_ms(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise SchemaError(f"{label} は整数 millisecond で指定してください。")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return parse_timestamp_ms(value)
    raise SchemaError(f"{label} の時刻形式が不正です。")


def _parse_whisper_segment(segment: Mapping[str, Any], *, index: int, absolute_start_ms: int) -> VttCue:
    allowed = {"timestamps", "offsets", "text", "tokens", "speaker", "speaker_turn_next"}
    unknown = set(segment) - allowed
    if unknown:
        raise SchemaError("whisper JSON segment に未知の field があります。", details={"index": index, "fields": sorted(unknown)})
    text = segment.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SchemaError("whisper JSON segment の text が空です。", details={"index": index})
    start: int | None = None
    end: int | None = None
    timestamps = segment.get("timestamps")
    offsets = segment.get("offsets")
    if timestamps is not None:
        if not isinstance(timestamps, Mapping) or set(timestamps) - {"from", "to"} or not {"from", "to"} <= set(timestamps):
            raise SchemaError("whisper JSON timestamps schema が不正です。", details={"index": index})
        start = _coerce_ms(timestamps["from"], label="timestamps.from")
        end = _coerce_ms(timestamps["to"], label="timestamps.to")
    elif offsets is not None:
        if not isinstance(offsets, Mapping) or set(offsets) - {"from", "to"} or not {"from", "to"} <= set(offsets):
            raise SchemaError("whisper JSON offsets schema が不正です。", details={"index": index})
        start = _coerce_ms(offsets["from"], label="offsets.from")
        end = _coerce_ms(offsets["to"], label="offsets.to")
    if start is None or end is None or end <= start or start < 0:
        raise SchemaError("whisper JSON segment の時刻範囲が不正です。", details={"index": index})
    return VttCue(start + absolute_start_ms, end + absolute_start_ms, _clean_vtt_text(text))


def parse_whisper_json(
    payload: Mapping[str, Any],
    *,
    absolute_start_ms: int = 0,
    expected_schema: str = WHISPER_OUTPUT_SCHEMA,
) -> list[VttCue]:
    """whisper.cpp の既知 JSON schema だけを受け、未知 schema は拒否する。"""

    if not isinstance(payload, Mapping):
        raise SchemaError("whisper output は JSON object でなければなりません。")
    allowed_root = {"schema", "transcription", "result", "systeminfo", "model", "params"}
    unknown = set(payload) - allowed_root
    if unknown:
        raise SchemaError("未知の whisper JSON schema です。", details={"fields": sorted(unknown)})
    if "schema" in payload and (
        payload["schema"] != expected_schema
        and not (payload["schema"] in WHISPER_OUTPUT_SCHEMAS and expected_schema in WHISPER_OUTPUT_SCHEMAS)
    ):
        raise SchemaError("whisper output schema version が期待値と異なります。", details={"expected": expected_schema, "actual": payload.get("schema")})
    if expected_schema == "whisper-cli-json-full-v1":
        required_root = {"systeminfo", "model", "params", "result", "transcription"}
        missing_root = sorted(required_root - set(payload))
        if missing_root:
            raise SchemaError(
                "whisper.cpp full JSON の必須 root field が不足しています。",
                details={"missing": missing_root},
            )
        if not isinstance(payload["systeminfo"], str):
            raise SchemaError("whisper.cpp full JSON の systeminfo が不正です。")
        for field_name in ("model", "params", "result"):
            if not isinstance(payload[field_name], Mapping):
                raise SchemaError(
                    f"whisper.cpp full JSON の {field_name} が object ではありません。",
                )
    transcription = payload.get("transcription")
    if not isinstance(transcription, list) or not transcription:
        raise SchemaError("whisper JSON の transcription が空または不正です。")
    cues: list[VttCue] = []
    for index, raw_segment in enumerate(transcription):
        if not isinstance(raw_segment, Mapping):
            raise SchemaError("whisper JSON transcription の要素が object ではありません。", details={"index": index})
        cues.append(_parse_whisper_segment(raw_segment, index=index, absolute_start_ms=absolute_start_ms))
    return cues


def _parse_harness_cues(payload: Mapping[str, Any]) -> list[VttCue]:
    allowed = {"schema", "cues", "metrics", "run_kind", "cache_hit", "stdout", "stderr", "returncode", "duration_ms", "peak_memory_bytes"}
    unknown = set(payload) - allowed
    if unknown:
        raise SchemaError("candidate harness JSON に未知の field があります。", details={"fields": sorted(unknown)})
    if payload.get("schema", "s9-1-candidate-v1") != "s9-1-candidate-v1":
        raise SchemaError("candidate harness JSON schema が不正です。")
    raw_cues = payload.get("cues")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise SchemaError("candidate harness JSON の cues が空または不正です。")
    cues: list[VttCue] = []
    for index, raw in enumerate(raw_cues):
        if not isinstance(raw, Mapping):
            raise SchemaError("candidate cue が object ではありません。", details={"index": index})
        allowed_cue = {"start_ms", "end_ms", "start", "end", "text", "identifier", "id"}
        unknown_cue = set(raw) - allowed_cue
        if unknown_cue:
            raise SchemaError("candidate cue に未知の field があります。", details={"index": index, "fields": sorted(unknown_cue)})
        start = _coerce_ms(raw.get("start_ms", raw.get("start")), label="candidate.start_ms")
        end = _coerce_ms(raw.get("end_ms", raw.get("end")), label="candidate.end_ms")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SchemaError("candidate cue の text が空です。", details={"index": index})
        cues.append(VttCue(start, end, _clean_vtt_text(text), identifier=raw.get("identifier", raw.get("id"))))
    return cues


@dataclass
class WhisperRunResult:
    status: str
    argv: list[str]
    measured_argv: list[str]
    stdout: str
    stderr: str
    returncode: int | None
    duration_ms: int
    peak_memory_bytes: int | None
    output_paths: list[str]
    run_kind: str
    cache_hit: bool | None = None
    cues: list[VttCue] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "argv": self.argv,
            "measured_argv": self.measured_argv,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "duration_ms": self.duration_ms,
            "peak_memory_bytes": self.peak_memory_bytes,
            "output_paths": self.output_paths,
            "run_kind": self.run_kind,
            "cache_hit": self.cache_hit,
            "cues": [cue.to_dict() for cue in self.cues],
            "error": self.error,
        }


def run_whisper_cli(
    *,
    runtime: WhisperRuntimeMetadata | Mapping[str, Any],
    model: ModelMetadata | Mapping[str, Any],
    audio_path: str | Path,
    output_json_path: str | Path,
    target_range: TimeRange | Mapping[str, Any] | Sequence[int] | None,
    absolute_start_ms: int = 0,
    settings: WhisperSettings | Mapping[str, Any] | None = None,
    timeout_sec: float = 120.0,
    run_kind: str = "cold",
    cache_hit: bool | None = None,
    use_time_l: bool | None = None,
    subprocess_run: Callable[..., Any] = subprocess.run,
) -> WhisperRunResult:
    """固定 argv で whisper-cli を実行し、未知 schema / timeout / partial を失敗にする。"""

    if run_kind not in ALLOWED_RUN_KINDS:
        raise RunnerError("run_kind は cold または warm で指定してください。", details={"run_kind": run_kind})
    runtime_meta = runtime if isinstance(runtime, WhisperRuntimeMetadata) else WhisperRuntimeMetadata.from_mapping(runtime)
    model_meta = model if isinstance(model, ModelMetadata) else ModelMetadata.from_mapping(model)
    validated_model = validate_model_metadata(model_meta)
    binary = Path(runtime_meta.binary_path)
    if not binary.is_file():
        raise RunnerError("manifest の whisper binary path が存在しません。", details={"path": str(binary)})
    if not audio_path or not Path(audio_path).is_file():
        raise RunnerError("manifest の audio path が存在しません。", details={"path": str(audio_path)})
    if timeout_sec <= 0:
        raise RunnerError("timeout_sec は正の値が必要です。")
    config = settings if isinstance(settings, WhisperSettings) else WhisperSettings.from_mapping(settings)
    output_path = Path(output_json_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    argv = build_whisper_argv(
        binary_path=binary,
        model_path=validated_model["path"],
        audio_path=audio_path,
        output_json_path=output_path,
        settings=config,
        target_range=target_range,
    )
    measured_argv = list(argv)
    time_path = Path("/usr/bin/time")
    should_time = sys.platform == "darwin" and time_path.is_file() if use_time_l is None else use_time_l
    if should_time:
        measured_argv = [str(time_path), "-l", *argv]
    started = time.monotonic()
    stdout = ""
    stderr = ""
    returncode: int | None = None
    try:
        completed = subprocess_run(
            measured_argv,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        stdout = _decode_process_output(getattr(completed, "stdout", ""))
        stderr = _decode_process_output(getattr(completed, "stderr", ""))
        returncode = getattr(completed, "returncode", None)
    except subprocess.TimeoutExpired as exc:
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        stdout = _decode_process_output(getattr(exc, "stdout", ""))
        stderr = _decode_process_output(getattr(exc, "stderr", ""))
        error = RunnerError("whisper-cli が timeout しました。", details={"timeout_sec": timeout_sec}).to_dict()
        return WhisperRunResult("failed", argv, measured_argv, stdout, stderr, None, duration_ms, _parse_peak_rss(stderr), [str(output_path)], run_kind, cache_hit, error=error)
    except OSError as exc:
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        error = RunnerError("whisper-cli を起動できませんでした。", details={"error": str(exc)}).to_dict()
        return WhisperRunResult("failed", argv, measured_argv, stdout, stderr, None, duration_ms, None, [str(output_path)], run_kind, cache_hit, error=error)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    peak = _parse_peak_rss(stderr)
    if returncode != 0:
        error = RunnerError("whisper-cli が非ゼロ終了しました。", details={"returncode": returncode}).to_dict()
        return WhisperRunResult("failed", argv, measured_argv, stdout, stderr, returncode, duration_ms, peak, [str(output_path)], run_kind, cache_hit, error=error)
    if not output_path.is_file():
        error = RunnerError("whisper-cli の partial result: output JSON がありません。", details={"output_path": str(output_path)}).to_dict()
        return WhisperRunResult("failed", argv, measured_argv, stdout, stderr, returncode, duration_ms, peak, [str(output_path)], run_kind, cache_hit, error=error)
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        cues = parse_whisper_json(payload, absolute_start_ms=absolute_start_ms, expected_schema=config.output_schema)
    except (OSError, json.JSONDecodeError, BenchmarkError, ValueError) as exc:
        if isinstance(exc, BenchmarkError):
            error = exc.to_dict()
        else:
            error = SchemaError("whisper-cli output JSON を検証できません。", details={"error": str(exc)}).to_dict()
        return WhisperRunResult("failed", argv, measured_argv, stdout, stderr, returncode, duration_ms, peak, [str(output_path)], run_kind, cache_hit, error=error)
    return WhisperRunResult("ok", argv, measured_argv, stdout, stderr, returncode, duration_ms, peak, [str(output_path)], run_kind, cache_hit, cues=cues)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("JSON を読み込めません。", details={"path": str(path), "error": str(exc)}) from exc
    return _require_mapping(raw, label=str(path))


def _resolve_reference(value: Any, *, base_dir: Path, label: str) -> Any:
    if isinstance(value, Mapping):
        if "text" in value:
            if not isinstance(value["text"], str):
                raise ManifestError(f"{label}.text は文字列が必要です。")
            return value["text"]
        if "path" in value:
            value = value["path"]
        else:
            return value
    if isinstance(value, str):
        candidate = Path(value)
        path = candidate if candidate.is_absolute() else base_dir / candidate
        if path.is_file():
            return path
        return value
    return value


def _read_text_reference(value: Any, *, base_dir: Path, label: str) -> str:
    resolved = _resolve_reference(value, base_dir=base_dir, label=label)
    if isinstance(resolved, Path):
        try:
            return resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"{label} を読み込めません。", details={"path": str(resolved), "error": str(exc)}) from exc
    if isinstance(resolved, str):
        return resolved
    raise ManifestError(f"{label} は text / path で指定してください。")


def _load_candidate(value: Any, *, base_dir: Path, absolute_start_ms: int = 0) -> tuple[list[VttCue], dict[str, Any]]:
    resolved = _resolve_reference(value, base_dir=base_dir, label="candidate")
    metadata: dict[str, Any] = {}
    if isinstance(resolved, Path):
        metadata["source_path"] = str(resolved)
        if resolved.suffix.lower() == ".vtt":
            return parse_vtt_file(resolved), metadata
        payload = _read_json(resolved)
    elif isinstance(resolved, Mapping):
        payload = resolved
    elif isinstance(resolved, str):
        try:
            payload = json.loads(resolved)
        except json.JSONDecodeError as exc:
            raise SchemaError("candidate output は VTT path または JSON で指定してください。", details={"error": str(exc)}) from exc
    else:
        raise SchemaError("candidate output の形式が不正です。")
    if "transcription" in payload:
        cues = parse_whisper_json(payload, absolute_start_ms=absolute_start_ms)
    elif "cues" in payload:
        cues = _parse_harness_cues(payload)
    else:
        raise SchemaError("candidate output が既知の whisper / harness JSON schema ではありません。")
    for key in ("metrics", "run_kind", "cache_hit", "duration_ms", "peak_memory_bytes"):
        if key in payload:
            metadata[key] = payload[key]
    if isinstance(payload.get("metrics"), Mapping):
        metadata.update(payload["metrics"])
    return cues, metadata


def _cue_text(cues: Iterable[VttCue], target: TimeRange | None) -> str:
    selected = list(cues) if target is None else select_cues_by_overlap(cues, target)
    return "".join(cue.text for cue in selected)


def _exclude_marker_cues(cues: Iterable[VttCue], tokens: Iterable[str], normalization: NormalizationConfig) -> list[VttCue]:
    """manifest が明示した純粋 marker だけを評価対象から除外する。"""

    normalized_tokens = {normalize_ja_text(token, normalization) for token in tokens if isinstance(token, str)}
    if not normalized_tokens:
        return list(cues)
    return [cue for cue in cues if normalize_ja_text(cue.text, normalization) not in normalized_tokens]


def _numeric_metric(value: Any, *, label: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise ManifestError(f"{label} は非負の有限 number が必要です。")
    return value


def _case_metrics(
    *,
    gold_text: str,
    cues: list[VttCue],
    gold_anchors: list[CueAnchor],
    target: TimeRange | None,
    glossary: list[str | Mapping[str, Any] | GlossaryEntry],
    normalization: NormalizationConfig,
    wall_time_ms: int | float | None,
    peak_memory_bytes: int | float | None,
    run_kind: str | None,
    cache_hit: bool | None,
    status: str = "ok",
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hypothesis = _cue_text(cues, target)
    result = {
        "status": status,
        "normalized_gold": normalize_ja_text(gold_text, normalization),
        "normalized_hypothesis": normalize_ja_text(hypothesis, normalization),
        "levenshtein": levenshtein_distance(normalize_ja_text(gold_text, normalization), normalize_ja_text(hypothesis, normalization)),
        "cer": character_error_rate(gold_text, hypothesis, normalization=normalization),
        "glossary": glossary_exact_match(gold_text, hypothesis, glossary, normalization=normalization),
        "cue": cue_inclusion_metrics(cues, gold_anchors, target=target),
        "wall_time_ms": wall_time_ms,
        "peak_memory_bytes": peak_memory_bytes,
        "run_kind": run_kind,
        "cache_hit": cache_hit,
    }
    if error is not None:
        result["error"] = error
    return result


_MANIFEST_KEYS = {
    "schema",
    "benchmark_id",
    "gold_audit_status",
    "normalization",
    "cue_inclusion_rule",
    "evaluation",
    "model",
    "whisper",
    "cache_policy",
    "glossary",
    "cases",
    "metadata",
    "created_at",
    "model_candidates",
    "gold_audit_status_detail",
}


def _normalize_protocol_manifest(raw: Mapping[str, Any], *, model_name: str | None = None) -> Mapping[str, Any]:
    """親が固定した ``schema_version=1`` fixture を内部 schema へ写像する。

    既存の docs/benchmarks fixture は、複数 model と case 内 gold を持つため、
    harness の strict な実行 schema へ変換する。path は値として保持するが、
    fingerprint からは従来どおり除外される。model_name を指定しない場合は
    fixture の models 配列の先頭を選び、report に全候補 metadata を残す。
    """

    if raw.get("schema") == MANIFEST_SCHEMA:
        return raw
    if raw.get("schema_version") != 1:
        return raw
    models = raw.get("models")
    if not isinstance(models, list) or not models:
        raise ManifestError("protocol fixture の models は 1 件以上必要です。")
    candidates = [dict(_require_mapping(model, label="models の要素")) for model in models]
    selected: dict[str, Any] | None = None
    if model_name is not None:
        selected = next((model for model in candidates if model.get("name") == model_name), None)
        if selected is None:
            raise ManifestError("指定した model_name が fixture にありません。", details={"model_name": model_name})
    else:
        selected = candidates[0]
    normalization_value = _require_mapping(raw.get("normalization", {}), label="normalization")
    normalization = {
        "unicode_form": normalization_value.get("unicode", "NFKC"),
        "remove_whitespace": normalization_value.get("strip_whitespace", True),
        "ignore_punctuation": normalization_value.get("ignore_punctuation", False),
    }
    cue_value = _require_mapping(raw.get("cue_rule", {}), label="cue_rule")
    exclude_text_tokens = cue_value.get(
        "exclude_text_tokens",
        normalization_value.get("exclude_text_tokens", []),
    )
    if not isinstance(exclude_text_tokens, list) or any(not isinstance(token, str) for token in exclude_text_tokens):
        raise ManifestError("exclude_text_tokens は文字列の配列が必要です。")
    cue_rule = {
        "kind": "overlap_half_open",
        "definition": cue_value.get("overlap", "cue.start_ms < target.end_ms and cue.end_ms > target.start_ms"),
        "dedupe_window_ms": cue_value.get("dedupe_window_ms", 0),
        "exclude_text_tokens": exclude_text_tokens,
        "viewer_greeting_policy": cue_value.get(
            "viewer_greeting_policy",
            "not_auto_excluded_without_human_annotation",
        ),
        "anchor_matching": cue_value.get("anchor_matching", "maximum_overlap_then_earliest_start"),
    }
    gates_value = _require_mapping(raw.get("gates", {}), label="gates")
    cold_budget = gates_value.get("cold_wall_time_seconds")
    warm_budget = gates_value.get("warm_wall_time_seconds")
    budget_seconds = [float(value) for value in (cold_budget, warm_budget) if isinstance(value, (int, float))]
    budgets = [round(value * 1000) for value in budget_seconds]
    evaluation = {
        "relative_cer_improvement_min": gates_value.get("paired_median_relative_cer_improvement", 0.10),
        "glossary_non_regression": gates_value.get("glossary_exact_match_non_regression", True),
        "cue_error_rate_delta_max": float(gates_value.get("cue_error_rate_delta_points", 5.0)) / 100,
        "wall_time_budget_ms": round(max(budgets)) if budgets else None,
        "wall_time_budget_ms_by_run_kind": {
            "cold": round(float(cold_budget) * 1000) if isinstance(cold_budget, (int, float)) else None,
            "warm": round(float(warm_budget) * 1000) if isinstance(warm_budget, (int, float)) else None,
        },
        "peak_memory_budget_bytes": gates_value.get("peak_memory_bytes"),
        "fail_closed_unaudited_gold": bool(gates_value.get("require_gold_audit", True)),
    }
    evaluation["wall_time_budget_ms_by_run_kind"] = {
        kind: budget for kind, budget in evaluation["wall_time_budget_ms_by_run_kind"].items() if budget is not None
    }
    whisper_value = _require_mapping(raw.get("whisper", {}), label="whisper")
    decode_value = dict(_require_mapping(whisper_value.get("decode", {}), label="whisper.decode"))
    padding_ms = decode_value.pop("padding_ms", 0)
    for key in ("threads", "processors"):
        if key in whisper_value and key not in decode_value:
            decode_value[key] = whisper_value[key]
    whisper = {
        "binary_path": whisper_value.get("binary", whisper_value.get("binary_path")),
        "version": whisper_value.get("version", ""),
        "build": whisper_value.get("build", "unknown"),
        "capabilities": whisper_value.get("capabilities", []),
        "settings": {
            "language": whisper_value.get("language", "ja"),
            "initial_prompt": whisper_value.get("initial_prompt", ""),
            "padding_ms": padding_ms,
            "decode": decode_value,
            "output_schema": whisper_value.get("output_schema", WHISPER_OUTPUT_SCHEMA),
        },
        "timeout_sec": whisper_value.get("timeout_sec", max(budget_seconds) if budget_seconds else 120),
    }
    cache_value = _require_mapping(raw.get("cache_policy", {}), label="cache_policy")
    cache_policy = {
        "mode": "declared",
        "run_kinds": ["cold", "warm"],
        "repeat_count": 2,
        "cold_definition": cache_value.get("cold_definition"),
        "warm_definition": cache_value.get("warm_definition"),
    }
    normalized_cases: list[dict[str, Any]] = []
    audit_statuses: list[str] = []
    cases_value = raw.get("cases")
    if not isinstance(cases_value, list) or not cases_value:
        raise ManifestError("protocol fixture の cases は 1 件以上必要です。")
    for raw_case in cases_value:
        source_case = _require_mapping(raw_case, label="protocol case")
        source_files = _require_mapping(source_case.get("source_files", {}), label="source_files")
        gold = _require_mapping(source_case.get("gold", {}), label="gold")
        range_value = source_case.get("range_ms")
        if not isinstance(range_value, Sequence) or len(range_value) != 2:
            raise ManifestError("protocol case.range_ms は 2 要素配列が必要です。", details={"case_id": source_case.get("id")})
        anchors_value = gold.get("cue_anchors_ms", [])
        if not isinstance(anchors_value, list):
            raise ManifestError("protocol gold.cue_anchors_ms は配列が必要です。")
        anchors = []
        for index, anchor in enumerate(anchors_value):
            if not isinstance(anchor, Sequence) or len(anchor) != 2:
                raise ManifestError("protocol cue anchor は 2 要素配列が必要です。")
            anchors.append({"anchor_id": f"anchor-{index + 1}", "start_ms": anchor[0], "end_ms": anchor[1]})
        case_audit = str(gold.get("audit_status", "unverified_provisional"))
        audit_statuses.append(case_audit)
        audio_fixture = source_case.get("audio_fixture")
        audio_path = audio_fixture
        if isinstance(audio_fixture, str) and not Path(audio_fixture).is_absolute() and raw.get("audio_cache_root"):
            audio_path = str(Path(str(raw["audio_cache_root"])) / audio_fixture)
        normalized_cases.append(
            {
                "case_id": source_case.get("id"),
                "gold_transcript": {"text": gold.get("text", "")},
                "glossary": list(gold.get("glossary", [])),
                "baseline_vtt": source_files.get("vtt"),
                "audio_path": audio_path,
                "audio_bytes": source_case.get("audio_bytes"),
                "audio_sha256": source_case.get("audio_sha256"),
                "target_range": {"start_ms": range_value[0], "end_ms": range_value[1]},
                "gold_cue_anchors": anchors,
                "run_kind": "cold",
                "metadata": {"video_id": source_case.get("video_id"), "candidate_id": source_case.get("candidate_id")},
            }
        )
    normalized_audit = "audited" if audit_statuses and all(status == "audited" for status in audit_statuses) else "provisional"
    return {
        "schema": MANIFEST_SCHEMA,
        "benchmark_id": str(raw.get("benchmark_id", "s9-1-benchmark")),
        "gold_audit_status": normalized_audit,
        "gold_audit_status_detail": audit_statuses,
        "normalization": normalization,
        "cue_inclusion_rule": cue_rule,
        "evaluation": evaluation,
        "model": selected,
        "model_candidates": candidates,
        "whisper": whisper,
        "cache_policy": cache_policy,
        "glossary": [],
        "cases": normalized_cases,
        "metadata": {"source_schema": "s9-1-protocol-schema-v1", "source": raw.get("metadata", {})},
    }


def _load_manifest(manifest: str | Path | Mapping[str, Any], *, model_name: str | None = None) -> tuple[Mapping[str, Any], Path]:
    if isinstance(manifest, Mapping):
        raw = manifest
        base_dir = Path.cwd()
    else:
        path = Path(manifest)
        raw = _read_json(path)
        base_dir = path.resolve().parent
    raw = _normalize_protocol_manifest(raw, model_name=model_name)
    unknown = set(raw) - _MANIFEST_KEYS
    if unknown:
        raise ManifestError("manifest に未知の top-level field があります。", details={"fields": sorted(unknown)})
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError("manifest schema が s9-1-benchmark-manifest-v1 ではありません。", details={"schema": raw.get("schema")})
    benchmark_id = raw.get("benchmark_id")
    if not isinstance(benchmark_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,96}", benchmark_id):
        raise ManifestError("benchmark_id は安全な英数字識別子が必要です。")
    audit = raw.get("gold_audit_status", "unaudited")
    if audit not in {"audited", "unaudited", "provisional"}:
        raise ManifestError("gold_audit_status は audited / unaudited / provisional のいずれかです。")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ManifestError("manifest.cases は 1 件以上必要です。")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ManifestError("cases の各要素は object です。")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,96}", case_id):
            raise ManifestError("case_id は安全な英数字識別子が必要です。")
        if case_id in ids:
            raise ManifestError("case_id が重複しています。", details={"case_id": case_id})
        ids.add(case_id)
        if "baseline_vtt" not in case:
            raise ManifestError("各 case に baseline_vtt が必要です。", details={"case_id": case_id})
        if "gold_transcript" not in case:
            raise ManifestError("各 case に gold_transcript が必要です。", details={"case_id": case_id})
        if "target_range" in case:
            try:
                TimeRange.from_value(case["target_range"])
            except ValueError as exc:
                raise ManifestError("case.target_range が不正です。", details={"case_id": case_id, "error": str(exc)}) from exc
        if "gold_cue_anchors" not in case:
            raise ManifestError("各 case に固定 gold_cue_anchors が必要です。", details={"case_id": case_id})
        if not isinstance(case["gold_cue_anchors"], list):
            raise ManifestError("gold_cue_anchors は配列で指定してください。", details={"case_id": case_id})
        run_kind = case.get("run_kind", "cold")
        if run_kind not in ALLOWED_RUN_KINDS:
            raise ManifestError("case.run_kind は cold / warm のいずれかです。", details={"case_id": case_id})
    return raw, base_dir


def _safe_case_name(case_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,96}", case_id):
        raise ManifestError("case_id を output path に使えません。", details={"case_id": case_id})
    return case_id


def generate_report(
    manifest: str | Path | Mapping[str, Any],
    output_dir: str | Path,
    *,
    execute_whisper: bool = False,
    run_kind: str | None = None,
    model_name: str | None = None,
    report_path: str | Path | None = None,
    use_time_l: bool | None = None,
    subprocess_run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """manifest と既存成果物から report を生成する。"""

    raw, base_dir = _load_manifest(manifest, model_name=model_name)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    normalization = NormalizationConfig.from_mapping(raw.get("normalization"))
    cue_rule = _require_mapping(raw.get("cue_inclusion_rule", {"kind": "overlap_half_open"}), label="cue_inclusion_rule")
    if cue_rule.get("kind", "overlap_half_open") != "overlap_half_open":
        raise ManifestError("S9-1 の cue inclusion rule は overlap_half_open 固定です。")
    dedupe_window_ms = cue_rule.get("dedupe_window_ms", 10)
    if isinstance(dedupe_window_ms, bool) or not isinstance(dedupe_window_ms, int) or dedupe_window_ms < 0:
        raise ManifestError("cue_inclusion_rule.dedupe_window_ms は 0 以上の整数が必要です。")
    gates = GateConfig.from_mapping(raw.get("evaluation"))
    model_value = dict(_require_mapping(raw.get("model"), label="model"))
    model_path_value = model_value.get("path")
    if not isinstance(model_path_value, str):
        raise ManifestError("model.path は文字列で指定してください。")
    model_path = Path(model_path_value)
    if not model_path.is_absolute():
        model_path = base_dir / model_path
    model_value["path"] = str(model_path)
    model_meta = ModelMetadata.from_mapping(model_value)
    validated_model = validate_model_metadata(model_meta)
    whisper_value = dict(_require_mapping(raw.get("whisper"), label="whisper"))
    binary_path_value = whisper_value.get("binary_path")
    if not isinstance(binary_path_value, str):
        raise ManifestError("whisper.binary_path は文字列で指定してください。")
    binary_path = Path(binary_path_value)
    if not binary_path.is_absolute():
        binary_path = base_dir / binary_path
    whisper_value["binary_path"] = str(binary_path)
    runtime_meta = WhisperRuntimeMetadata.from_mapping(whisper_value)
    settings = WhisperSettings.from_mapping(whisper_value.get("settings"))
    timeout_sec = float(whisper_value.get("timeout_sec", 120.0))
    if timeout_sec <= 0:
        raise ManifestError("whisper.timeout_sec は正の値が必要です。")
    cache_policy = CachePolicy.from_mapping(raw.get("cache_policy"))
    default_glossary_value = raw.get("glossary", [])
    if not isinstance(default_glossary_value, list):
        raise ManifestError("manifest.glossary は配列で指定してください。")
    # normalization 時点で glossary も検証しておく。同じ glossary を gold から作らない。
    _normalized_glossary(default_glossary_value, normalization)
    all_input_fingerprints: list[dict[str, Any]] = []
    report_cases: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    for raw_case in raw["cases"]:
        case = _require_mapping(raw_case, label="case")
        case_id = str(case["case_id"])
        target = TimeRange.from_value(case["target_range"]) if "target_range" in case else None
        gold_text = _read_text_reference(case["gold_transcript"], base_dir=base_dir, label=f"{case_id}.gold_transcript")
        baseline_ref = _resolve_reference(case["baseline_vtt"], base_dir=base_dir, label=f"{case_id}.baseline_vtt")
        if not isinstance(baseline_ref, Path):
            raise ManifestError("baseline_vtt は VTT file path で指定してください。", details={"case_id": case_id})
        baseline_cues = deduplicate_progressive_timed(parse_vtt_file(baseline_ref))
        baseline_cues = _exclude_marker_cues(
            baseline_cues,
            cue_rule.get("exclude_text_tokens", []),
            normalization,
        )
        baseline_fingerprint = file_fingerprint(baseline_ref)
        all_input_fingerprints.append({"kind": "baseline_vtt", "case_id": case_id, **baseline_fingerprint})
        anchors_value = case["gold_cue_anchors"]
        if not isinstance(anchors_value, list):
            raise ManifestError("gold_cue_anchors は配列で指定してください。", details={"case_id": case_id})
        anchors = [CueAnchor.from_value(value, index) for index, value in enumerate(anchors_value)]
        anchor_source = "manifest_fixed"
        glossary_value = case.get("glossary", default_glossary_value)
        if not isinstance(glossary_value, list):
            raise ManifestError("case.glossary は配列で指定してください。", details={"case_id": case_id})
        _normalized_glossary(glossary_value, normalization)
        baseline_metadata = _require_mapping(case.get("baseline_metrics", {}), label=f"{case_id}.baseline_metrics")
        baseline_wall = _numeric_metric(_metric_value(baseline_metadata, "wall_time_ms", "duration_ms"), label="baseline wall time")
        baseline_memory = _numeric_metric(_metric_value(baseline_metadata, "peak_memory_bytes", "peak_rss_bytes"), label="baseline peak memory")
        baseline_metric = _case_metrics(
            gold_text=gold_text,
            cues=baseline_cues,
            gold_anchors=anchors,
            target=target,
            glossary=glossary_value,
            normalization=normalization,
            wall_time_ms=baseline_wall,
            peak_memory_bytes=baseline_memory,
            run_kind=baseline_metadata.get("run_kind"),
            cache_hit=baseline_metadata.get("cache_hit"),
        )
        candidate_cues: list[VttCue] = []
        candidate_metadata: dict[str, Any] = {}
        candidate_error: dict[str, Any] | None = None
        candidate_execution: dict[str, Any] | None = None
        candidate_path: Path | None = None
        candidate_run_kind = run_kind or case.get("run_kind", "cold")
        if candidate_run_kind not in ALLOWED_RUN_KINDS:
            raise ManifestError("candidate run_kind は cold / warm のいずれかです。", details={"case_id": case_id})
        if execute_whisper:
            audio_ref = _resolve_reference(case.get("audio_path"), base_dir=base_dir, label=f"{case_id}.audio_path")
            if not isinstance(audio_ref, Path):
                raise ManifestError("--execute-whisper には audio_path が必要です。", details={"case_id": case_id})
            audio_fingerprint = file_fingerprint(
                audio_ref,
                expected_sha256=case.get("audio_sha256"),
                expected_bytes=case.get("audio_bytes"),
            )
            all_input_fingerprints.append({"kind": "audio", "case_id": case_id, **audio_fingerprint})
            candidate_path = output_path / "whisper" / _safe_case_name(case_id) / f"{candidate_run_kind}.json"
            result = run_whisper_cli(
                runtime=runtime_meta,
                model=model_meta,
                audio_path=audio_ref,
                output_json_path=candidate_path,
                target_range=target,
                absolute_start_ms=target.start_ms if target else 0,
                settings=settings,
                timeout_sec=timeout_sec,
                run_kind=candidate_run_kind,
                cache_hit=case.get("cache_hit", candidate_run_kind == "warm"),
                use_time_l=use_time_l,
                subprocess_run=subprocess_run,
            )
            candidate_cues = _exclude_marker_cues(
                result.cues,
                cue_rule.get("exclude_text_tokens", []),
                normalization,
            )
            candidate_metadata = {
                "duration_ms": result.duration_ms,
                "peak_memory_bytes": result.peak_memory_bytes,
                "run_kind": result.run_kind,
                "cache_hit": result.cache_hit,
                "argv": result.argv,
                "measured_argv": result.measured_argv,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "output_paths": result.output_paths,
            }
            candidate_execution = result.to_dict()
            commands.append({"case_id": case_id, "run_kind": result.run_kind, "argv": result.measured_argv})
            if result.status != "ok":
                candidate_error = result.error
        else:
            candidate_ref_value = case.get("candidate_output_json", case.get("candidate_vtt", case.get("candidate_output")))
            if candidate_ref_value is None:
                candidate_error = RunnerError("candidate output が manifest にありません。", details={"case_id": case_id}).to_dict()
            else:
                resolved_candidate = _resolve_reference(candidate_ref_value, base_dir=base_dir, label=f"{case_id}.candidate")
                if isinstance(resolved_candidate, Path):
                    candidate_path = resolved_candidate
                    all_input_fingerprints.append({"kind": "candidate_output", "case_id": case_id, **file_fingerprint(resolved_candidate)})
                try:
                    candidate_cues, candidate_metadata = _load_candidate(resolved_candidate, base_dir=base_dir, absolute_start_ms=(target.start_ms if target else 0))
                    candidate_cues = _exclude_marker_cues(
                        candidate_cues,
                        cue_rule.get("exclude_text_tokens", []),
                        normalization,
                    )
                except (BenchmarkError, OSError, ValueError, json.JSONDecodeError) as exc:
                    candidate_error = exc.to_dict() if isinstance(exc, BenchmarkError) else SchemaError("candidate output の検証に失敗しました。", details={"error": str(exc)}).to_dict()
                    candidate_metadata = {}
        candidate_wall = _numeric_metric(_metric_value(candidate_metadata, "wall_time_ms", "duration_ms"), label="candidate wall time")
        candidate_memory = _numeric_metric(_metric_value(candidate_metadata, "peak_memory_bytes", "peak_rss_bytes"), label="candidate peak memory")
        candidate_cache = candidate_metadata.get("cache_hit", case.get("cache_hit"))
        if candidate_cache is not None and not isinstance(candidate_cache, bool):
            raise ManifestError("candidate cache_hit は boolean で指定してください。", details={"case_id": case_id})
        candidate_metric = _case_metrics(
            gold_text=gold_text,
            cues=candidate_cues,
            gold_anchors=anchors,
            target=target,
            glossary=glossary_value,
            normalization=normalization,
            wall_time_ms=candidate_wall,
            peak_memory_bytes=candidate_memory,
            run_kind=candidate_metadata.get("run_kind", candidate_run_kind),
            cache_hit=candidate_cache,
            status="failed" if candidate_error else "ok",
            error=candidate_error,
        )
        if candidate_execution is not None:
            candidate_metric["execution"] = candidate_execution
        case_report = {
            "case_id": case_id,
            "target_range": target.to_dict() if target else None,
            "gold_cue_anchors": [asdict(anchor) for anchor in anchors],
            "gold_cue_anchor_source": anchor_source,
            "baseline": baseline_metric,
            "candidate": candidate_metric,
        }
        if candidate_path is not None:
            case_report["candidate_output_path"] = str(candidate_path)
        report_cases.append(case_report)
    fixture_fp = manifest_fingerprint(raw)
    runtime_report = runtime_meta.to_dict()
    binary_path = Path(runtime_meta.binary_path)
    if binary_path.is_file():
        runtime_report["binary_fingerprint"] = file_fingerprint(binary_path)
    gate_report = evaluate_gates(
        report_cases,
        gate_config=gates,
        gold_audit_status=str(raw.get("gold_audit_status", "unaudited")),
    )
    report = {
        "schema": REPORT_SCHEMA,
        "benchmark_id": raw["benchmark_id"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "manifest_fingerprint": fixture_fp,
        "fingerprints": {
            "fixture_manifest_sha256": fixture_fp,
            "inputs": all_input_fingerprints,
            "model": {"path": validated_model["path"], "bytes": validated_model["validated_bytes"], "sha256": validated_model["sha256"]},
        },
        "normalization": normalization.to_dict(),
        "cue_inclusion_rule": {
            **dict(cue_rule),
            "kind": "overlap_half_open",
            "definition": "cue.start_ms < target.end_ms and cue.end_ms > target.start_ms",
            "error_rate": "(missing + duplicate) / gold_anchor_count",
        },
        "gold_audit_status": raw.get("gold_audit_status", "unaudited"),
        "gold_audit_status_detail": raw.get("gold_audit_status_detail", []),
        "metrics_status": gate_report["metrics_status"],
        "gates": {**gates.to_dict(), **gate_report},
        "model": validated_model,
        "model_candidates": raw.get("model_candidates", [validated_model]),
        "whisper_runtime": {**runtime_report, "settings": settings.to_dict(), "timeout_sec": timeout_sec, "output_schema": settings.output_schema},
        "cache_policy": cache_policy.to_dict(),
        "commands": commands,
        "reproduction": {
            "argv": [sys.executable, "benchmarks/s9_benchmark.py", "run", "--manifest", "<manifest>", "--output-dir", "<output-dir>"] + (["--report", "<report>"] if report_path is not None else []) + (["--execute-whisper", "--run-kind", run_kind] if execute_whisper and run_kind else (["--execute-whisper"] if execute_whisper else [])),
            "shell": False,
        },
        "cases": report_cases,
    }
    report_file = Path(report_path) if report_path is not None else output_path / f"{raw['benchmark_id']}.report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(_jsonable(report), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_file)
    return report


SCHEMA_HELP = """manifest は s9-1-benchmark-manifest-v1。必須 top-level は schema, benchmark_id, gold_audit_status, model, whisper, cases。各 case は case_id, gold_transcript, baseline_vtt, target_range, gold_cue_anchors, candidate_output_json または candidate_vtt を持つ。evaluation で CER 10%、glossary 非悪化、cue baseline + 0.05、wall time、peak memory を事前宣言する。model path は既存ファイルを指し、sha256 と bytes を一致させる。任意の shell command は受け付けない。"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s9_benchmark.py",
        description="S9-1 production 非変更 benchmark harness（固定 manifest から JSON report を生成）",
        epilog=SCHEMA_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="manifest を評価して report JSON を output-dir に書く", epilog=SCHEMA_HELP)
    run.add_argument("--manifest", required=True, help="s9-1-benchmark-manifest-v1 JSON")
    run.add_argument("--output-dir", required=True, help="report と任意の whisper output の保存先")
    run.add_argument("--execute-whisper", action="store_true", help="manifest の binary / model / audio で固定 argv を実行する")
    run.add_argument("--run-kind", choices=sorted(ALLOWED_RUN_KINDS), help="実測 run kind（cold / warm）")
    run.add_argument("--model-name", help="複数モデル fixture で選択する name（未指定時は先頭）")
    run.add_argument("--report", help="output-dir 外へ report JSON を明示保存する path")
    run.add_argument("--no-time-l", action="store_true", help="macOS /usr/bin/time -l を使わない（peak memory は未記録）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if args_list and args_list[0] not in {"run", "-h", "--help"} and "--manifest" in args_list:
        args_list.insert(0, "run")
    parser = build_arg_parser()
    args = parser.parse_args(args_list)
    if args.command != "run":
        parser.print_help()
        return 0
    try:
        report = generate_report(
            args.manifest,
            args.output_dir,
            execute_whisper=args.execute_whisper,
            run_kind=args.run_kind,
            model_name=args.model_name,
            report_path=args.report,
            use_time_l=False if args.no_time_l else None,
        )
    except BenchmarkError as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"report_path": report["report_path"], "status": report["gates"]["status"], "go": report["gates"]["go"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
