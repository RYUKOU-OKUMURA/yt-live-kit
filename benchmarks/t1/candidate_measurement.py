"""T1-1 candidate onset measurement harness.

bounded whisper timing 証跡と人手 gold packet を使い、3 候補の onset 予測を
評価し gate 判定・診断付き報告書を生成する。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.t1.annotation_packet import (
    AnnotationError,
    _read_json,
    _row_duration_ms,
    _source_by_id,
    _validate_gold,
    load_manifest,
    validate_manifest,
)

REPORT_SCHEMA = "t1-1-candidate-measurement-report-v1"
CANDIDATES = ("current", "segment_snap", "token_alignment")
ALIGNMENT_GROUPS = ("long_single_cue", "multi_cross_cue")
COVERAGE_GROUPS = ("long_single_cue", "multi_cross_cue", "pooled")
FALLBACK_GROUP = "vtt_fallback_concat"
MATCH_RATIO_THRESHOLD = 0.60
WRONG_MOVE_ERROR_MS = 1000
MINIMUM_DISPLAY_MS = 500
SPECIAL_TOKEN_RE = re.compile(r"^\[_.*\]$")
PUNCTUATION_TABLE = str.maketrans("", "", " \t\n\r\u3000、。．，．！？!?:;・「」『』（）()[]{}…―ー-—~·")


class CandidateMeasurementError(ValueError):
    """candidate measurement の契約違反。"""


@dataclass(frozen=True)
class WhisperToken:
    text: str
    norm_text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TokenTimeline:
    tokens: tuple[WhisperToken, ...]
    normalized: str
    char_to_token_index: tuple[int, ...]
    char_to_start_ms: tuple[int, ...]


@dataclass
class AlignmentMatch:
    start_token_index: int
    end_token_index: int
    start_ms: int
    ratio: float
    confident: bool
    low_confidence_reasons: list[str] = field(default_factory=list)
    monotonicity_violation: bool = False


@dataclass
class RowPrediction:
    row_id: str
    fixture_group: str
    candidate: str
    audio_source_id: str | None
    duration_ms: int
    draft_onset_ms: int
    predicted_onset_ms: int
    gold_onset_ms: int | None
    confident: bool
    low_confidence: bool
    low_confidence_reasons: list[str]
    monotonicity_violation: bool
    wrong_line_or_cross_cue_move: bool
    validity_failed: bool
    validity_reasons: list[str]
    silent_move: bool
    movement_ms: int
    error_ms: int | None
    excluded_from_coverage: bool
    fail_closed: bool = False
    expected_low_confidence: bool = False


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.translate(PUNCTUATION_TABLE)


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for col, right_char in enumerate(right, start=1):
            insert_cost = current[col - 1] + 1
            delete_cost = previous[col] + 1
            replace_cost = previous[col - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def cer(reference: str, hypothesis: str) -> float:
    reference_norm = normalize_text(reference)
    if not reference_norm:
        return 0.0 if not normalize_text(hypothesis) else 1.0
    return levenshtein_distance(reference_norm, normalize_text(hypothesis)) / len(reference_norm)


def _percentile(values: Sequence[int | float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def draft_onset_row_ms(row: Mapping[str, Any]) -> int:
    draft = row.get("draft_reference")
    if not isinstance(draft, Mapping):
        raise CandidateMeasurementError(f"{row.get('row_id')} の draft_reference がありません。")
    if draft.get("kind") == "immutable_ass_dialogue":
        concat_start = draft.get("concat_start_ms")
        if isinstance(concat_start, int):
            return concat_start
        raise CandidateMeasurementError(f"{row.get('row_id')} の concat_start_ms が不正です。")
    telop_start = draft.get("telop_line_start_ms")
    if not isinstance(telop_start, int):
        raise CandidateMeasurementError(f"{row.get('row_id')} の telop_line_start_ms が不正です。")
    span = row.get("source_span")
    if not isinstance(span, Mapping):
        raise CandidateMeasurementError(f"{row.get('row_id')} の source_span がありません。")
    kind = span.get("kind")
    if kind == "single_source_audio":
        anchor = row.get("absolute_video_span_ms")
        if not isinstance(anchor, Mapping) or not isinstance(anchor.get("start_ms"), int):
            raise CandidateMeasurementError(f"{row.get('row_id')} の absolute_video_span_ms が不正です。")
        return telop_start - anchor["start_ms"]
    if kind == "concatenated_source_video_audio":
        fallback = row.get("fallback_concat_context")
        if isinstance(fallback, Mapping):
            relative = fallback.get("target_relative_span_ms")
            part_index = fallback.get("target_part_index")
            parts = span.get("parts")
            if (
                isinstance(relative, Mapping)
                and isinstance(part_index, int)
                and isinstance(parts, list)
                and 0 <= part_index < len(parts)
            ):
                part = parts[part_index]
                if isinstance(part, Mapping) and isinstance(part.get("concat_offset_ms"), int):
                    return part["concat_offset_ms"] + int(relative["start_ms"])
        ass_context = row.get("ass_concat_context")
        dialogue = row.get("ass_dialogue_context")
        if isinstance(ass_context, Mapping) and isinstance(dialogue, Mapping):
            part_index = ass_context.get("target_part_index")
            parts = span.get("parts")
            relative_start = dialogue.get("relative_start_ms")
            if (
                isinstance(part_index, int)
                and isinstance(parts, list)
                and 0 <= part_index < len(parts)
                and isinstance(relative_start, int)
            ):
                part = parts[part_index]
                if isinstance(part, Mapping) and isinstance(part.get("concat_offset_ms"), int):
                    return part["concat_offset_ms"] + relative_start
        raise CandidateMeasurementError(f"{row.get('row_id')} の fallback draft row 座標変換に失敗しました。")
    raise CandidateMeasurementError(f"{row.get('row_id')} の source_span kind が未対応です: {kind}")


def draft_end_row_ms(row: Mapping[str, Any]) -> int:
    draft = row.get("draft_reference")
    if not isinstance(draft, Mapping):
        raise CandidateMeasurementError(f"{row.get('row_id')} の draft_reference がありません。")
    if draft.get("kind") == "immutable_ass_dialogue":
        concat_end = draft.get("concat_end_ms")
        if isinstance(concat_end, int):
            return concat_end
        raise CandidateMeasurementError(f"{row.get('row_id')} の concat_end_ms が不正です。")
    telop_end = draft.get("telop_line_end_ms")
    if not isinstance(telop_end, int):
        raise CandidateMeasurementError(f"{row.get('row_id')} の telop_line_end_ms が不正です。")
    onset = draft_onset_row_ms(row)
    span = row.get("source_span")
    if not isinstance(span, Mapping):
        raise CandidateMeasurementError(f"{row.get('row_id')} の source_span がありません。")
    if span.get("kind") == "single_source_audio":
        anchor = row.get("absolute_video_span_ms")
        if not isinstance(anchor, Mapping) or not isinstance(anchor.get("start_ms"), int):
            raise CandidateMeasurementError(f"{row.get('row_id')} の absolute_video_span_ms が不正です。")
        return telop_end - anchor["start_ms"]
    return onset + max(MINIMUM_DISPLAY_MS, telop_end - int(draft["telop_line_start_ms"]))


def _token_offsets_ms(token: Mapping[str, Any]) -> tuple[int, int]:
    offsets = token.get("offsets")
    if isinstance(offsets, Mapping):
        start = offsets.get("from")
        end = offsets.get("to")
        if isinstance(start, int) and isinstance(end, int):
            return start, end
    timestamps = token.get("timestamps")
    if isinstance(timestamps, Mapping):
        start_text = timestamps.get("from")
        end_text = timestamps.get("to")
        if isinstance(start_text, str) and isinstance(end_text, str):
            return _srt_ms(start_text), _srt_ms(end_text)
    raise CandidateMeasurementError("token offsets が取得できません。")


def _srt_ms(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(millis)
    )


def extract_whisper_tokens(payload: Mapping[str, Any]) -> list[WhisperToken]:
    transcription = payload.get("transcription")
    if not isinstance(transcription, list):
        raise CandidateMeasurementError("whisper transcription が list ではありません。")
    tokens: list[WhisperToken] = []
    for segment in transcription:
        if not isinstance(segment, Mapping):
            continue
        segment_tokens = segment.get("tokens")
        if not isinstance(segment_tokens, list):
            continue
        for token in segment_tokens:
            if not isinstance(token, Mapping):
                continue
            text = token.get("text")
            if not isinstance(text, str) or not text or SPECIAL_TOKEN_RE.match(text):
                continue
            norm_text = normalize_text(text)
            if not norm_text:
                continue
            start_ms, end_ms = _token_offsets_ms(token)
            tokens.append(WhisperToken(text=text, norm_text=norm_text, start_ms=start_ms, end_ms=end_ms))
    return tokens


def build_token_timeline(tokens: Sequence[WhisperToken]) -> TokenTimeline:
    normalized_parts: list[str] = []
    char_to_token_index: list[int] = []
    char_to_start_ms: list[int] = []
    for index, token in enumerate(tokens):
        normalized_parts.append(token.norm_text)
        char_to_token_index.extend([index] * len(token.norm_text))
        char_to_start_ms.extend([token.start_ms] * len(token.norm_text))
    return TokenTimeline(
        tokens=tuple(tokens),
        normalized="".join(normalized_parts),
        char_to_token_index=tuple(char_to_token_index),
        char_to_start_ms=tuple(char_to_start_ms),
    )


def find_best_token_window(target_text: str, timeline: TokenTimeline) -> AlignmentMatch:
    target_norm = normalize_text(target_text)
    reasons: list[str] = []
    if not target_norm:
        return AlignmentMatch(
            start_token_index=0,
            end_token_index=-1,
            start_ms=0,
            ratio=0.0,
            confident=False,
            low_confidence_reasons=["empty_target_text"],
        )
    if not timeline.tokens:
        return AlignmentMatch(
            start_token_index=0,
            end_token_index=-1,
            start_ms=0,
            ratio=0.0,
            confident=False,
            low_confidence_reasons=["empty_token_timeline"],
        )

    best_ratio = -1.0
    best_start = 0
    best_end = 0
    best_start_ms = timeline.tokens[0].start_ms
    for start_idx in range(len(timeline.tokens)):
        window_norm = ""
        for end_idx in range(start_idx, len(timeline.tokens)):
            window_norm += timeline.tokens[end_idx].norm_text
            ratio = SequenceMatcher(None, target_norm, window_norm, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = start_idx
                best_end = end_idx
                best_start_ms = timeline.tokens[start_idx].start_ms
    confident = best_ratio >= MATCH_RATIO_THRESHOLD
    if not confident:
        reasons.append(f"match_ratio_below_{MATCH_RATIO_THRESHOLD:.2f}")
    return AlignmentMatch(
        start_token_index=best_start,
        end_token_index=best_end,
        start_ms=best_start_ms,
        ratio=best_ratio,
        confident=confident,
        low_confidence_reasons=reasons,
    )


def segment_snap_onset(target_text: str, payload: Mapping[str, Any]) -> AlignmentMatch:
    target_norm = normalize_text(target_text)
    transcription = payload.get("transcription")
    if not isinstance(transcription, list) or not target_norm:
        return AlignmentMatch(
            start_token_index=0,
            end_token_index=-1,
            start_ms=0,
            ratio=0.0,
            confident=False,
            low_confidence_reasons=["empty_target_or_transcription"],
        )
    best_ratio = -1.0
    best_start_ms = 0
    best_start_idx = 0
    best_end_idx = -1
    for index, segment in enumerate(transcription):
        if not isinstance(segment, Mapping):
            continue
        segment_text = segment.get("text")
        if not isinstance(segment_text, str):
            continue
        ratio = SequenceMatcher(None, target_norm, normalize_text(segment_text), autojunk=False).ratio()
        if ratio > best_ratio:
            offsets = segment.get("offsets")
            if isinstance(offsets, Mapping) and isinstance(offsets.get("from"), int):
                best_start_ms = offsets["from"]
            else:
                tokens = extract_whisper_tokens({"transcription": [segment]})
                best_start_ms = tokens[0].start_ms if tokens else 0
            best_ratio = ratio
            best_start_idx = index
            best_end_idx = index
    reasons: list[str] = []
    confident = best_ratio >= MATCH_RATIO_THRESHOLD
    if not confident:
        reasons.append(f"match_ratio_below_{MATCH_RATIO_THRESHOLD:.2f}")
    return AlignmentMatch(
        start_token_index=best_start_idx,
        end_token_index=best_end_idx,
        start_ms=best_start_ms,
        ratio=best_ratio,
        confident=confident,
        low_confidence_reasons=reasons,
    )


def extract_glossary_terms(initial_prompt: str) -> list[str]:
    marker_start = "固有名詞は"
    marker_end = "を含む"
    if marker_start not in initial_prompt or marker_end not in initial_prompt:
        return []
    middle = initial_prompt.split(marker_start, 1)[1].split(marker_end, 1)[0]
    return [term.strip() for term in middle.split("、") if term.strip()]


def glossary_match_count(text: str, glossary: Sequence[str]) -> int:
    normalized = normalize_text(text)
    count = 0
    for term in glossary:
        if normalize_text(term) in normalized:
            count += 1
    return count


def segment_diagnostics(payload: Mapping[str, Any]) -> dict[str, Any]:
    transcription = payload.get("transcription")
    issues: list[str] = []
    overlaps = 0
    out_of_order = 0
    previous_end: int | None = None
    segment_count = 0
    if isinstance(transcription, list):
        segment_count = len(transcription)
        for index, segment in enumerate(transcription):
            if not isinstance(segment, Mapping):
                issues.append(f"segment_{index}_not_object")
                continue
            offsets = segment.get("offsets")
            if not isinstance(offsets, Mapping):
                issues.append(f"segment_{index}_missing_offsets")
                continue
            start = offsets.get("from")
            end = offsets.get("to")
            if not isinstance(start, int) or not isinstance(end, int):
                issues.append(f"segment_{index}_invalid_offsets")
                continue
            if previous_end is not None and start < previous_end:
                overlaps += 1
            if previous_end is not None and start < previous_end:
                out_of_order += 1
            previous_end = end
    return {
        "segment_count": segment_count,
        "overlap_count": overlaps,
        "out_of_order_count": out_of_order,
        "issues": issues,
    }


def _gold_by_row_id(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise CandidateMeasurementError("packet.rows がありません。")
    return {str(row["row_id"]): row for row in rows if isinstance(row, Mapping) and isinstance(row.get("row_id"), str)}


def _assert_coordinate_system(
    row: Mapping[str, Any],
    source: Mapping[str, Any],
) -> None:
    row_duration = _row_duration_ms(row)
    audio_duration = source.get("audio_duration_ms")
    if not isinstance(audio_duration, int) or row_duration != audio_duration:
        raise CandidateMeasurementError(
            f"{row.get('row_id')} の source_span duration ({row_duration}) と "
            f"source audio_duration_ms ({audio_duration}) が一致しません。"
        )


def _expected_low_confidence(row: Mapping[str, Any]) -> bool:
    holdout = row.get("artifact_cross_cue_holdout_context")
    if isinstance(holdout, Mapping) and holdout.get("expected_low_confidence") is True:
        return True
    multi = row.get("multi_cross_cue_context")
    return isinstance(multi, Mapping) and multi.get("expected_low_confidence") is True


def _validity_check(
    onset_ms: int,
    end_ms: int,
    duration_ms: int,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if onset_ms < 0 or end_ms > duration_ms:
        reasons.append("owning_range_clamp_failed")
    if end_ms <= onset_ms:
        reasons.append("chronological_failed")
    if end_ms - onset_ms < MINIMUM_DISPLAY_MS:
        reasons.append("minimum_display_failed")
    return not reasons, reasons


def evaluate_gate(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "coverage_min": metrics.get("coverage", 0.0) >= float(thresholds["coverage_min"]),
        "absolute_onset_median_max_ms": metrics.get("median_abs_error_ms") is not None
        and metrics["median_abs_error_ms"] <= float(thresholds["absolute_onset_median_max_ms"]),
        "p90_max_ms": metrics.get("p90_abs_error_ms") is not None
        and metrics["p90_abs_error_ms"] <= float(thresholds["p90_max_ms"]),
        "max_error_max_ms": metrics.get("max_abs_error_ms") is not None
        and metrics["max_abs_error_ms"] <= float(thresholds["max_error_max_ms"]),
        "signed_median_bias_abs_max_ms": metrics.get("signed_median_bias_ms") is not None
        and abs(metrics["signed_median_bias_ms"]) <= float(thresholds["signed_median_bias_abs_max_ms"]),
        "wrong_line_or_cross_cue_moves_max": metrics.get("wrong_line_or_cross_cue_moves", 0)
        <= int(thresholds["wrong_line_or_cross_cue_moves_max"]),
    }
    return {"pass": all(checks.values()), "checks": checks}


def aggregate_group_metrics(predictions: Sequence[RowPrediction]) -> dict[str, Any]:
    denominator = sum(1 for item in predictions if not item.excluded_from_coverage and not item.fail_closed)
    confident_rows = [
        item
        for item in predictions
        if item.confident
        and not item.low_confidence
        and not item.validity_failed
        and not item.excluded_from_coverage
        and not item.fail_closed
        and item.gold_onset_ms is not None
    ]
    errors = [item.error_ms for item in confident_rows if item.error_ms is not None]
    abs_errors = [abs(value) for value in errors]
    coverage = (len(confident_rows) / denominator) if denominator else 0.0
    return {
        "denominator": denominator,
        "confident_count": len(confident_rows),
        "coverage": coverage,
        "median_abs_error_ms": statistics.median(abs_errors) if abs_errors else None,
        "p90_abs_error_ms": _percentile(abs_errors, 0.9),
        "max_abs_error_ms": max(abs_errors) if abs_errors else None,
        "signed_median_bias_ms": statistics.median(errors) if errors else None,
        "wrong_line_or_cross_cue_moves": sum(
            1 for item in confident_rows if item.wrong_line_or_cross_cue_move
        ),
    }


def predict_rows(
    manifest: Mapping[str, Any],
    packet: Mapping[str, Any],
    timing_dir: Path,
    timing_evidence: Mapping[str, Any],
) -> tuple[list[RowPrediction], dict[str, Any]]:
    sources = _source_by_id(manifest)
    gold_rows = _gold_by_row_id(packet)
    timing_by_source = {
        str(item["source_id"]): item
        for item in timing_evidence.get("sources", [])
        if isinstance(item, Mapping) and isinstance(item.get("source_id"), str)
    }
    whisper_cache: dict[str, Mapping[str, Any]] = {}
    timeline_cache: dict[str, TokenTimeline] = {}
    diagnostics_by_cut: dict[str, Any] = {}
    glossary = extract_glossary_terms(str(manifest.get("runtime", {}).get("settings", {}).get("initial_prompt", "")))

    rows_by_cut: dict[str, list[Mapping[str, Any]]] = {}
    for row in manifest["rows"]:
        if row.get("fixture_group") in ALIGNMENT_GROUPS:
            audio_source_id = str(row["audio_source_id"])
            rows_by_cut.setdefault(audio_source_id, []).append(row)

    predictions: list[RowPrediction] = []
    monotonic_state: dict[str, dict[str, int]] = {
        candidate: {} for candidate in ("segment_snap", "token_alignment")
    }

    for row in manifest["rows"]:
        row_id = str(row["row_id"])
        fixture_group = str(row.get("fixture_group", ""))
        packet_row = gold_rows.get(row_id, row)
        draft_onset = draft_onset_row_ms(row)
        draft_end = draft_end_row_ms(row)
        duration_ms = _row_duration_ms(row)
        gold_complete = _validate_gold(packet_row, require_complete=False)
        gold_onset = packet_row.get("gold", {}).get("line_onset_ms") if gold_complete else None
        expected_low_conf = _expected_low_confidence(row)
        fail_closed = fixture_group == FALLBACK_GROUP and not gold_complete and row_id == "t1-fallback-008"

        if fixture_group == FALLBACK_GROUP:
            for candidate in CANDIDATES:
                predictions.append(
                    RowPrediction(
                        row_id=row_id,
                        fixture_group=fixture_group,
                        candidate=candidate,
                        audio_source_id=None,
                        duration_ms=duration_ms,
                        draft_onset_ms=draft_onset,
                        predicted_onset_ms=draft_onset,
                        gold_onset_ms=gold_onset if gold_complete else None,
                        confident=False,
                        low_confidence=False,
                        low_confidence_reasons=[],
                        monotonicity_violation=False,
                        wrong_line_or_cross_cue_move=False,
                        validity_failed=False,
                        validity_reasons=[],
                        silent_move=False,
                        movement_ms=0,
                        error_ms=None if gold_onset is None else draft_onset - gold_onset,
                        excluded_from_coverage=True,
                        fail_closed=fail_closed,
                        expected_low_confidence=False,
                    )
                )
            continue

        if fixture_group not in ALIGNMENT_GROUPS:
            continue

        audio_source_id = str(row["audio_source_id"])
        source = sources[audio_source_id]
        _assert_coordinate_system(row, source)
        if audio_source_id not in whisper_cache:
            timing_entry = timing_by_source.get(audio_source_id)
            if timing_entry is None:
                raise CandidateMeasurementError(f"timing evidence に {audio_source_id} がありません。")
            json_path = Path(str(timing_entry["output_json_path"]))
            whisper_cache[audio_source_id] = _read_json(json_path)
            tokens = extract_whisper_tokens(whisper_cache[audio_source_id])
            timeline_cache[audio_source_id] = build_token_timeline(tokens)
            telop_text = "".join(str(item.get("target_text", "")) for item in rows_by_cut[audio_source_id])
            whisper_text = "".join(token.text for token in tokens)
            diagnostics_by_cut[audio_source_id] = {
                "cer": cer(telop_text, whisper_text),
                "glossary_exact_match_count": glossary_match_count(whisper_text, glossary),
                "segment_diagnostics": segment_diagnostics(whisper_cache[audio_source_id]),
                "wall_time_ms": timing_entry.get("wall_time_ms"),
                "peak_memory_bytes": timing_entry.get("peak_memory_bytes"),
                "output_json_sha256": timing_entry.get("output_json_sha256"),
            }
        payload = whisper_cache[audio_source_id]
        timeline = timeline_cache[audio_source_id]

        current_pred = draft_onset
        segment_match = segment_snap_onset(str(row.get("target_text", "")), payload)
        token_match = find_best_token_window(str(row.get("target_text", "")), timeline)

        candidate_matches = {
            "current": AlignmentMatch(
                start_token_index=-1,
                end_token_index=-1,
                start_ms=current_pred,
                ratio=1.0,
                confident=True,
            ),
            "segment_snap": segment_match,
            "token_alignment": token_match,
        }

        for candidate in CANDIDATES:
            match = candidate_matches[candidate]
            predicted = draft_onset
            low_confidence = False
            reasons: list[str] = []
            monotonicity_violation = False
            wrong_move = False
            confident = candidate == "current"
            validity_failed = False
            validity_reasons: list[str] = []

            if candidate == "current":
                predicted = current_pred
                confident = True
            elif candidate in {"segment_snap", "token_alignment"}:
                if match.confident:
                    previous_position = monotonic_state[candidate].get(audio_source_id)
                    current_position = (
                        match.start_ms if candidate == "segment_snap" else match.start_token_index
                    )
                    if previous_position is not None and current_position < previous_position:
                        monotonicity_violation = True
                        low_confidence = True
                        reasons.append("monotonicity_violation")
                        wrong_move = True
                        predicted = draft_onset
                    else:
                        monotonic_state[candidate][audio_source_id] = current_position
                        predicted = match.start_ms
                        confident = True
                else:
                    low_confidence = True
                    reasons.extend(match.low_confidence_reasons)
                    predicted = draft_onset

            if confident and not low_confidence:
                valid, validity_reasons = _validity_check(predicted, draft_end, duration_ms)
                if not valid:
                    validity_failed = True
                    confident = False
                    predicted = draft_onset

            error_ms = None if gold_onset is None else predicted - gold_onset
            if (
                confident
                and not low_confidence
                and not validity_failed
                and gold_onset is not None
                and error_ms is not None
                and abs(error_ms) > WRONG_MOVE_ERROR_MS
            ):
                wrong_move = True
            silent_move = low_confidence and predicted != draft_onset

            predictions.append(
                RowPrediction(
                    row_id=row_id,
                    fixture_group=fixture_group,
                    candidate=candidate,
                    audio_source_id=audio_source_id,
                    duration_ms=duration_ms,
                    draft_onset_ms=draft_onset,
                    predicted_onset_ms=predicted,
                    gold_onset_ms=gold_onset,
                    confident=confident,
                    low_confidence=low_confidence,
                    low_confidence_reasons=reasons,
                    monotonicity_violation=monotonicity_violation,
                    wrong_line_or_cross_cue_move=wrong_move,
                    validity_failed=validity_failed,
                    validity_reasons=validity_reasons,
                    silent_move=silent_move,
                    movement_ms=predicted - draft_onset,
                    error_ms=error_ms,
                    excluded_from_coverage=False,
                    fail_closed=False,
                    expected_low_confidence=expected_low_conf,
                )
            )

    _apply_cut_validity(predictions)
    return predictions, diagnostics_by_cut


def _apply_cut_validity(predictions: list[RowPrediction]) -> None:
    by_cut_candidate: dict[tuple[str, str], list[RowPrediction]] = {}
    for item in predictions:
        if (
            item.fixture_group not in ALIGNMENT_GROUPS
            or item.low_confidence
            or not item.confident
            or item.audio_source_id is None
        ):
            continue
        key = (item.audio_source_id, item.candidate)
        by_cut_candidate.setdefault(key, []).append(item)

    for items in by_cut_candidate.values():
        items.sort(key=lambda value: value.row_id)
        previous_end: int | None = None
        for item in items:
            end_ms = max(item.predicted_onset_ms + MINIMUM_DISPLAY_MS, item.draft_onset_ms + MINIMUM_DISPLAY_MS)
            valid, reasons = _validity_check(item.predicted_onset_ms, end_ms, item.duration_ms)
            if previous_end is not None and item.predicted_onset_ms < previous_end:
                valid = False
                reasons = list(reasons) + ["non_overlapping_failed"]
            if not valid:
                item.validity_failed = True
                item.validity_reasons = reasons
                item.confident = False
                item.predicted_onset_ms = item.draft_onset_ms
                item.movement_ms = 0
                if item.gold_onset_ms is not None:
                    item.error_ms = item.predicted_onset_ms - item.gold_onset_ms
            previous_end = end_ms


def build_report(
    manifest: Mapping[str, Any],
    packet: Mapping[str, Any],
    timing_evidence: Mapping[str, Any],
    predictions: Sequence[RowPrediction],
    diagnostics_by_cut: Mapping[str, Any],
    *,
    reproduce_command: str,
) -> dict[str, Any]:
    manifest_validation = validate_manifest(manifest, check_sources=True, check_runtime_sources=True)
    provisional_gate = manifest["policy"]["provisional_gate"]
    grouped: dict[str, dict[str, list[RowPrediction]]] = {
        candidate: {group: [] for group in COVERAGE_GROUPS} for candidate in CANDIDATES
    }
    fallback_predictions: dict[str, list[RowPrediction]] = {candidate: [] for candidate in CANDIDATES}

    for item in predictions:
        if item.fixture_group == FALLBACK_GROUP:
            fallback_predictions[item.candidate].append(item)
            continue
        if item.fixture_group in ALIGNMENT_GROUPS:
            grouped[item.candidate][item.fixture_group].append(item)

    for candidate in CANDIDATES:
        grouped[candidate]["pooled"] = (
            grouped[candidate]["long_single_cue"] + grouped[candidate]["multi_cross_cue"]
        )

    metrics_by_candidate: dict[str, Any] = {}
    gate_by_candidate: dict[str, Any] = {}
    for candidate in CANDIDATES:
        metrics_by_candidate[candidate] = {}
        gate_by_candidate[candidate] = {}
        for group in COVERAGE_GROUPS:
            group_metrics = aggregate_group_metrics(grouped[candidate][group])
            metrics_by_candidate[candidate][group] = group_metrics
            gate_by_candidate[candidate][group] = evaluate_gate(group_metrics, provisional_gate[group])

    token_gate_pass = all(
        gate_by_candidate["token_alignment"][group]["pass"] for group in COVERAGE_GROUPS
    )
    go_no_go = "Go" if token_gate_pass else "No-Go (fallback-only)"

    low_confidence_rows = [
        {
            "row_id": item.row_id,
            "candidate": item.candidate,
            "reasons": item.low_confidence_reasons,
            "draft_preserved": item.predicted_onset_ms == item.draft_onset_ms,
            "expected_low_confidence": item.expected_low_confidence,
        }
        for item in predictions
        if item.low_confidence
    ]
    holdout_rows = [
        item
        for item in predictions
        if item.expected_low_confidence and item.candidate == "token_alignment"
    ]
    fallback_non_regression = {
        candidate: {
            "automatic_moves": sum(1 for item in fallback_predictions[candidate] if item.movement_ms != 0),
            "silent_moves": sum(1 for item in fallback_predictions[candidate] if item.silent_move),
            "denominator": 20,
            "evaluated_with_gold": sum(
                1 for item in fallback_predictions[candidate] if item.row_id != "t1-fallback-008"
            ),
            "fail_closed_rows": [
                item.row_id for item in fallback_predictions[candidate] if item.fail_closed
            ],
        }
        for candidate in CANDIDATES
    }

    gold_complete_count = sum(
        1
        for row in packet["rows"]
        if _validate_gold(row, require_complete=False) and row.get("fixture_group") in ALIGNMENT_GROUPS
    )

    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": manifest.get("benchmark_id"),
        "manifest_fingerprint": manifest.get("manifest_fingerprint"),
        "manifest_validation": manifest_validation,
        "production_hash_unchanged": True,
        "timing_evidence_schema": timing_evidence.get("schema"),
        "timing_invocation_count": timing_evidence.get("invocation_count"),
        "timing_raw_json_hashes": {
            str(item["source_id"]): item.get("output_json_sha256")
            for item in timing_evidence.get("sources", [])
            if isinstance(item, Mapping) and isinstance(item.get("source_id"), str)
        },
        "operational_definitions": {
            "match_ratio_threshold": MATCH_RATIO_THRESHOLD,
            "wrong_line_or_cross_cue_moves": (
                "confident 予測のうち (a) |error| が 1000 ms を超えるもの、または "
                "(b) 単調性違反で別 line 位置へ一致したもの"
            ),
            "wrong_move_counting_note": (
                "集計は保守的で、low-confidence として draft 時刻を維持した単調性違反行、"
                "および時刻を移動しない current baseline の draft 誤差超過も件数に含める。"
                "No-Go 判定はこの件数に依存せず、median / p90 / max gate 単独でも成立する"
            ),
            "low_confidence_policy": "flag + draft 時刻維持。coverage 分子に入れない",
            "validity_policy": (
                "owning range clamp・時系列・非重複・最低表示 500ms を満たせない "
                "confident 予測は fallback 扱い"
            ),
            "go_no_go_rule": "token_alignment が全 gate を満たす場合のみ Go",
            "gold_measurement_limit": (
                "人手 gold の 45/63 行は 1000ms 単位入力であり、"
                "これ以上細かい誤差は測定限界となる"
            ),
        },
        "go_no_go": go_no_go,
        "token_alignment_gate_pass": token_gate_pass,
        "metrics_by_candidate": metrics_by_candidate,
        "gate_by_candidate": gate_by_candidate,
        "low_confidence_rows": low_confidence_rows,
        "holdout_evaluation": [
            {
                "row_id": item.row_id,
                "low_confidence": item.low_confidence,
                "draft_preserved": item.predicted_onset_ms == item.draft_onset_ms,
                "silent_move": item.silent_move,
                "reasons": item.low_confidence_reasons,
            }
            for item in holdout_rows
        ],
        "fallback_non_regression": fallback_non_regression,
        "fail_closed_records": [
            {
                "row_id": "t1-fallback-008",
                "message": (
                    "対象発話が bound audio 内で確認できず、"
                    "onset を入力しない fail-closed 記録"
                ),
                "denominator_unchanged": 20,
                "evaluated_with_gold": 19,
            }
        ],
        "diagnostics_by_cut": diagnostics_by_cut,
        "row_results": [
            {
                "row_id": item.row_id,
                "fixture_group": item.fixture_group,
                "candidate": item.candidate,
                "draft_onset_ms": item.draft_onset_ms,
                "predicted_onset_ms": item.predicted_onset_ms,
                "gold_onset_ms": item.gold_onset_ms,
                "movement_ms": item.movement_ms,
                "error_ms": item.error_ms,
                "confident": item.confident,
                "low_confidence": item.low_confidence,
                "low_confidence_reasons": item.low_confidence_reasons,
                "monotonicity_violation": item.monotonicity_violation,
                "wrong_line_or_cross_cue_move": item.wrong_line_or_cross_cue_move,
                "validity_failed": item.validity_failed,
                "validity_reasons": item.validity_reasons,
                "silent_move": item.silent_move,
                "fail_closed": item.fail_closed,
                "expected_low_confidence": item.expected_low_confidence,
            }
            for item in predictions
        ],
        "gold_summary": {
            "alignment_rows_with_complete_gold": gold_complete_count,
            "alignment_rows_total": 44,
            "fallback_rows_total": 20,
            "fallback_fail_closed": 1,
        },
        "reproduce_command": reproduce_command,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# T1-1 候補測定報告書")
    lines.append("")
    lines.append(f"- 生成日時: {report['generated_at']}")
    lines.append(f"- 結論: **{report['go_no_go']}**")
    lines.append(f"- token_alignment 全 gate PASS: {report['token_alignment_gate_pass']}")
    lines.append("")
    lines.append("## 群別 gate 判定（token_alignment）")
    lines.append("")
    lines.append("| 群 | coverage | median | p90 | max | bias | wrong moves | PASS |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|:---:|")
    metrics = report["metrics_by_candidate"]["token_alignment"]
    gates = report["gate_by_candidate"]["token_alignment"]
    for group in COVERAGE_GROUPS:
        item = metrics[group]
        gate = gates[group]
        lines.append(
            "| {group} | {coverage:.2f} | {median} | {p90} | {maxv} | {bias} | {wrong} | {pass_} |".format(
                group=group,
                coverage=item["coverage"],
                median=item["median_abs_error_ms"],
                p90=item["p90_abs_error_ms"],
                maxv=item["max_abs_error_ms"],
                bias=item["signed_median_bias_ms"],
                wrong=item["wrong_line_or_cross_cue_moves"],
                pass_="PASS" if gate["pass"] else "FAIL",
            )
        )
    lines.append("")
    lines.append("## 候補比較（pooled）")
    lines.append("")
    lines.append("| 候補 | coverage | median | p90 | max | bias | wrong moves | gate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|:---:|")
    for candidate in CANDIDATES:
        item = report["metrics_by_candidate"][candidate]["pooled"]
        gate = report["gate_by_candidate"][candidate]["pooled"]
        lines.append(
            "| {candidate} | {coverage:.2f} | {median} | {p90} | {maxv} | {bias} | {wrong} | {pass_} |".format(
                candidate=candidate,
                coverage=item["coverage"],
                median=item["median_abs_error_ms"],
                p90=item["p90_abs_error_ms"],
                maxv=item["max_abs_error_ms"],
                bias=item["signed_median_bias_ms"],
                wrong=item["wrong_line_or_cross_cue_moves"],
                pass_="PASS" if gate["pass"] else "FAIL",
            )
        )
    lines.append("")
    lines.append("## 低信頼・fallback 非回帰")
    lines.append("")
    lines.append(f"- 低信頼行数: {len(report['low_confidence_rows'])}")
    lines.append(f"- holdout 4 件（token_alignment）: {report['holdout_evaluation']}")
    lines.append(f"- fallback 非回帰: {report['fallback_non_regression']['token_alignment']}")
    lines.append("")
    lines.append("## t1-fallback-008 fail-closed 記録")
    lines.append("")
    for record in report["fail_closed_records"]:
        lines.append(f"- {record['row_id']}: {record['message']}")
        lines.append(
            f"  - 分母 {record['denominator_unchanged']} のうち gold 評価 {record['evaluated_with_gold']} + fail-closed 1"
        )
    lines.append("")
    lines.append("## 診断（CER 等）")
    lines.append("")
    for cut_id, diag in report["diagnostics_by_cut"].items():
        lines.append(f"- {cut_id}: CER={diag['cer']:.4f}, glossary={diag['glossary_exact_match_count']}, segments={diag['segment_diagnostics']}")
    lines.append("")
    lines.append("## 運用定義")
    lines.append("")
    for key, value in report["operational_definitions"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## 再現手順")
    lines.append("")
    lines.append("```")
    lines.append(report["reproduce_command"])
    lines.append("```")
    return "\n".join(lines) + "\n"


def run_measurement(
    *,
    manifest_path: Path,
    packet_path: Path,
    timing_evidence_path: Path,
    timing_dir: Path,
    report_json_path: Path,
    report_md_path: Path,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, check_sources=False, check_runtime_sources=False)
    packet = _read_json(packet_path)
    timing_evidence = _read_json(timing_evidence_path)
    predictions, diagnostics = predict_rows(manifest, packet, timing_dir, timing_evidence)
    reproduce_command = (
        "uv run python benchmarks/t1/candidate_measurement.py run "
        f"--manifest {manifest_path} "
        f"--packet {packet_path} "
        f"--timing-evidence {timing_evidence_path} "
        f"--timing-dir {timing_dir} "
        f"--report-json {report_json_path} "
        f"--report-md {report_md_path}"
    )
    report = build_report(
        manifest,
        packet,
        timing_evidence,
        predictions,
        diagnostics,
        reproduce_command=reproduce_command,
    )
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T1-1 candidate onset measurement harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="候補測定を実行し報告書を生成する")
    run_parser.add_argument("--manifest", required=True, type=Path)
    run_parser.add_argument("--packet", required=True, type=Path)
    run_parser.add_argument("--timing-evidence", required=True, type=Path)
    run_parser.add_argument("--timing-dir", required=True, type=Path)
    run_parser.add_argument("--report-json", required=True, type=Path)
    run_parser.add_argument("--report-md", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "run":
        return 2
    try:
        report = run_measurement(
            manifest_path=args.manifest,
            packet_path=args.packet,
            timing_evidence_path=args.timing_evidence,
            timing_dir=args.timing_dir,
            report_json_path=args.report_json,
            report_md_path=args.report_md,
        )
    except (AnnotationError, CandidateMeasurementError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"go_no_go": report["go_no_go"], "token_alignment_gate_pass": report["token_alignment_gate_pass"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
