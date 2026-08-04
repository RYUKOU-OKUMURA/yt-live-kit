"""Generate the corrected T1-1 pre-measurement manifest deterministically.

This harness only reads the immutable production documents and the previous
draft manifest.  It writes a candidate JSON to an isolated temporary file,
validates it, and atomically replaces the requested benchmark manifest only
after validation succeeds.  It never writes production data or audio bytes.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from annotation_packet import (
    ALIGNMENT_LONG_TUPLE_IDS,
    ALIGNMENT_MULTI_TUPLE_IDS,
    ASS_FALLBACK_EVIDENCE,
    ass_evidence_integrity_contract,
    KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS,
    MANUAL_SPLIT_BOUNDARIES,
    MANUAL_SPLIT_DELIMITERS,
    MANUAL_SPLIT_ORIGINAL_TUPLE_IDS,
    manifest_fingerprint,
    _validate_ass_evidence_files,
    validate_manifest,
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_without_symlink_escape(
    path: Path,
    *,
    label: str,
    protected_roots: tuple[Path, ...] = (),
) -> Path:
    """Canonicalize a CLI path and reject symlinks controlled below its roots.

    macOS commonly exposes ``/tmp`` and ``/var`` as symlink aliases.  Those
    OS-owned aliases are allowed; a symlink whose resolved target is inside a
    benchmark/test root is not.
    """

    raw = path.expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    canonical_roots = tuple(root.resolve() for root in protected_roots)
    os_aliases = {Path("/tmp"), Path("/var")}
    current = raw
    while True:
        if current.is_symlink() and current not in os_aliases:
            target = current.resolve()
            parent_target = current.parent.resolve()
            if any(
                target == root
                or root in target.parents
                or parent_target == root
                or root in parent_target.parents
                for root in canonical_roots
            ):
                raise ValueError(f"{label} に制御可能な symlink は使用できません: {current}")
        if current.is_symlink() and current in os_aliases:
            pass
        elif current.is_symlink() and not canonical_roots:
            raise ValueError(f"{label} に symlink は使用できません: {current}")
        parent = current.parent
        if parent == current:
            break
        current = parent
    return raw.resolve()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _temporary_roots() -> tuple[Path, ...]:
    return tuple(
        {
            Path(tempfile.gettempdir()).resolve(),
            Path("/tmp").resolve(),
        }
    )


def _resolve_test_root(path: Path) -> Path:
    root = _resolve_without_symlink_escape(
        path,
        label="test root",
        protected_roots=_temporary_roots(),
    )
    if not any(_is_within(root, temp_root) for temp_root in _temporary_roots()) or not root.is_dir():
        raise ValueError(f"test root は一時ディレクトリ配下の既存ディレクトリに限定されます: {root}")
    return root


def _resolve_allowed_path(path: Path, *, label: str, roots: tuple[Path, ...]) -> Path:
    resolved = _resolve_without_symlink_escape(path, label=label, protected_roots=roots)
    if not any(_is_within(resolved, root.resolve()) for root in roots):
        allowed = ", ".join(str(root.resolve()) for root in roots)
        raise ValueError(f"{label} は許可された benchmark/test root 配下に限定されます: {resolved} (allowed: {allowed})")
    return resolved


def _load_validated_previous(path: Path) -> dict[str, Any]:
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"previous manifest を読み込めません: {path}") from exc
    if not isinstance(previous, dict):
        raise ValueError("previous manifest は JSON object が必要です。")
    # Production documents are read only after this source/hash preflight.
    validate_manifest(previous, check_sources=True)
    return previous


def _preflight_ass_evidence() -> None:
    """Check fixed b5d evidence before reading it into the new manifest."""

    _validate_ass_evidence_files(ASS_FALLBACK_EVIDENCE)


def _add_b5_source_entries(manifest: dict[str, Any]) -> None:
    """Bind cutplan003's three distinct source spans without reusing cutplan001."""

    source_entries = manifest.setdefault("sources", {})
    template = copy.deepcopy(source_entries["lb4_e1ff-cut_001"])
    for index, segment in enumerate(ASS_FALLBACK_EVIDENCE["segments"], 1):
        key = f"lb4_b5d-cut_{index:03d}"
        source_id = f"lb4_b5d-cut_{index:03d}-audio"
        source = copy.deepcopy(template)
        source.pop("rejected_legacy_audio_cache", None)
        source.update(
            {
                "source_id": source_id,
                "case_id": key,
                "cut_id": f"cut_{index:03d}",
                "audio_path": None,
                "audio_bytes": None,
                "audio_sha256": None,
                "audio_duration_ms": segment["duration_ms"],
                "audio_span_origin_ms": {
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                },
                "vtt_path": ASS_FALLBACK_EVIDENCE["vtt_path"],
                "vtt_bytes": ASS_FALLBACK_EVIDENCE["vtt_bytes"],
                "vtt_sha256": ASS_FALLBACK_EVIDENCE["vtt_sha256"],
                "raw_timing_path": None,
                "raw_timing_bytes": None,
                "raw_timing_sha256": None,
                "raw_timing_status": "not_applicable_legacy_vtt",
                "artifact_document_id": None,
                "source_provenance": "pre_t1_saved_cutplan003_source_mp4_bound_read_only",
                "source_content_kind": "source_mp4",
                "source_content_path": ASS_FALLBACK_EVIDENCE["source_content_path"],
                "source_content_bytes": ASS_FALLBACK_EVIDENCE["source_content_bytes"],
                "source_content_sha256": ASS_FALLBACK_EVIDENCE["source_content_sha256"],
                "media_path": ASS_FALLBACK_EVIDENCE["source_content_path"],
                "media_bytes": ASS_FALLBACK_EVIDENCE["source_content_bytes"],
                "media_sha256": ASS_FALLBACK_EVIDENCE["source_content_sha256"],
                "media_coordinate_system": "absolute_video_ms",
                "source_coordinate_system": "absolute_video_ms",
                "audio_source_provenance": "pre_t1_saved_cutplan003_source_mp4_bound_read_only",
                "timing_input_role": "legacy_vtt_fallback_only",
            }
        )
        source_entries[key] = source


def _load_lines(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lines: dict[str, dict[str, Any]] = {}
    for prefix, document_id in (
        ("lb4_e1ff", "lb4_e1ff"),
        ("gza_415", "gza_415"),
        ("gza_f0", "gza_f0"),
        ("hpe_8ad", "hpe_8ad"),
    ):
        document = manifest["telop_documents"][document_id]
        payload = json.loads(Path(document["path"]).read_text(encoding="utf-8"))
        for segment_index, segment in enumerate(payload["segments"], 1):
            for line_index, line in enumerate(segment["lines"], 1):
                start_ms = round(line["start_sec"] * 1000)
                end_ms = round(line["end_sec"] * 1000)
                tuple_id = f"{prefix}:s{segment_index}:l{line_index}:{start_ms}-{end_ms}"
                lines[tuple_id] = {
                    "prefix": prefix,
                    "document_id": document_id,
                    "segment_index": segment_index,
                    "line_index": line_index,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": line["text"],
                }
    return lines


def _source_maps(manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    sources = {source["source_id"]: source for source in manifest["sources"].values()}
    by_prefix_segment: dict[tuple[str, int], dict[str, Any]] = {}
    for source in sources.values():
        prefix = source["source_id"].split("-cut_", 1)[0].removesuffix("-audio")
        segment = int(source["cut_id"].rsplit("_", 1)[1])
        by_prefix_segment[(prefix, segment)] = source
    return sources, by_prefix_segment


def _old_rows_by_tuple(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in manifest["rows"]:
        tuple_id = row["source_telop_line_tuple_id"]
        base = tuple_id.rsplit(":manual:", 1)[0] if ":manual:" in tuple_id else tuple_id
        result.setdefault(base, row)
    return result


def _artifact_context(
    info: dict[str, Any],
    source: dict[str, Any],
    lines: dict[str, dict[str, Any]],
    artifact_payloads: dict[str, dict[str, Any]],
    by_prefix_segment: dict[tuple[str, int], dict[str, Any]],
) -> tuple[int, int, int, list[int]]:
    payload = artifact_payloads[source["artifact_document_id"]]
    matches = [
        (index, cue)
        for index, cue in enumerate(payload["cues"])
        if cue["start_ms"] <= info["start_ms"] and info["end_ms"] <= cue["end_ms"]
    ]
    if len(matches) != 1:
        raise ValueError(f"artifact cue mapping is not unique: {info}")
    cue_index, cue = matches[0]
    same_cue_lines = [
        other
        for other in lines.values()
        if other["prefix"] == info["prefix"]
        and by_prefix_segment[(other["prefix"], other["segment_index"])] ["source_id"] == source["source_id"]
        and cue["start_ms"] <= other["start_ms"]
        and other["end_ms"] <= cue["end_ms"]
    ]
    same_cue_lines.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["line_index"]))
    position = next(index for index, other in enumerate(same_cue_lines, 1) if other is info)
    cue_indices = sorted(
        index
        for index, other_cue in enumerate(payload["cues"])
        if source["audio_span_origin_ms"]["start_ms"] <= other_cue["start_ms"]
        and other_cue["end_ms"] <= source["audio_span_origin_ms"]["end_ms"]
    )
    return cue_index, position, len(same_cue_lines), cue_indices


def _artifact_template(
    tuple_id: str,
    old_by_tuple: dict[str, dict[str, Any]],
    old_rows: list[dict[str, Any]],
    lines: dict[str, dict[str, Any]],
    by_prefix_segment: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    if tuple_id in old_by_tuple:
        return copy.deepcopy(old_by_tuple[tuple_id])
    info = lines[tuple_id]
    source = by_prefix_segment[(info["prefix"], info["segment_index"])]
    return copy.deepcopy(next(row for row in old_rows if row["audio_source_id"] == source["source_id"]))


def _artifact_row(
    tuple_id: str,
    row_id: str,
    group: str,
    manifest: dict[str, Any],
    old_by_tuple: dict[str, dict[str, Any]],
    old_rows: list[dict[str, Any]],
    lines: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    by_prefix_segment: dict[tuple[str, int], dict[str, Any]],
    artifact_payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    info = lines[tuple_id]
    source = by_prefix_segment[(info["prefix"], info["segment_index"])]
    row = _artifact_template(tuple_id, old_by_tuple, old_rows, lines, by_prefix_segment)
    cue_index, position, line_count, cue_indices = _artifact_context(
        info, source, lines, artifact_payloads, by_prefix_segment
    )
    row.update(
        {
            "row_id": row_id,
            "fixture_group": group,
            "audio_source_id": source["source_id"],
            "source_hashes": {
                "vtt_sha256": source["vtt_sha256"],
                "raw_timing_sha256": None,
                "source_content_sha256": source["source_content_sha256"],
            },
            "source_boundary_basis": "existing_cut_range_whole_audio_source_not_telop_or_whisper_boundary",
            "source_span": {"kind": "single_source_audio", "start_ms": 0, "end_ms": source["audio_duration_ms"]},
            "absolute_video_span_ms": copy.deepcopy(source["audio_span_origin_ms"]),
            "target_text": info["text"],
            "source_telop_line_tuple_id": tuple_id,
            "manual_pre_measurement_fixture": False,
            "coverage_excluded": False,
            "fallback_non_regression_required": False,
            "gold": {
                "line_onset_ms": None,
                "timebase": "source_audio_relative_ms",
                "annotator_id": None,
                "annotated_at": None,
                "audio_listened": False,
            },
            "gold_provenance": "missing_human_audio_annotation",
            "timing_source": "bounded_whisper_cli_output_pending_in_isolated_temp",
            "raw_timing_available": False,
            "candidate_or_boundary_fields_are_not_gold": True,
            "raw_timing_status": "pending_bounded_whisper_cli",
            "timing_input_source_id": source["source_id"],
        }
    )
    for field in (
        "fallback_concat_context",
        "vtt_fallback_context",
        "manual_split",
        "manual_split_evaluation",
        "long_single_cue_context",
        "fixture_anchor",
        "fixture_anchor_rule",
        "multi_cross_cue_context",
        "artifact_cross_cue_holdout_context",
    ):
        row.pop(field, None)
    document = manifest["telop_documents"][info["document_id"]]
    artifact_id = source["artifact_document_id"]
    row["draft_reference"] = {
        "kind": "immutable_telop_line",
        "telop_document_id": info["document_id"],
        "telop_document_path": document["path"],
        "telop_document_sha256": document["sha256"],
        "telop_segment_index": info["segment_index"],
        "telop_line_index": info["line_index"],
        "telop_line_start_ms": info["start_ms"],
        "telop_line_end_ms": info["end_ms"],
        "text_source": "saved_production_telop_document",
        "source_cut_id": source["cut_id"],
        "source_boundary_basis": "existing_cut_range",
        "candidate_boundary_used": False,
        "gold_must_ignore_telop_time": True,
        "artifact_context": {
            "artifact_document_id": artifact_id,
            "artifact_fingerprint": artifact_id,
            "artifact_cue_index": cue_index,
            "artifact_cue_start_ms": artifact_payloads[artifact_id]["cues"][cue_index]["start_ms"],
            "artifact_cue_end_ms": artifact_payloads[artifact_id]["cues"][cue_index]["end_ms"],
            "artifact_cue_line_position": position,
            "artifact_cue_line_count": line_count,
            "selection_only_not_gold_or_audio_boundary": True,
            "raw_token_timing_present": False,
            "artifact_json_role": "cue_only_provenance_not_raw_token_timing",
        },
    }
    if group == "long_single_cue":
        row["long_single_cue_context"] = {
            "contract": "same_artifact_cue_multiple_telop_lines",
            "artifact_cue_index": cue_index,
            "line_position_in_artifact_cue": position,
            "line_count_in_artifact_cue": line_count,
            "second_or_later_line_required": position >= 2,
            "cue_start_anchor": position == 1,
            "selection_does_not_use_artifact_or_token_start_as_audio_start": True,
            "audio_span_basis": "whole_existing_cut_range",
        }
    else:
        row["multi_cross_cue_context"] = {
            "contract": "artifact_cue_boundary_sequence_same_cut_range",
            "artifact_cue_indices_in_same_cut": cue_indices,
            "line_is_not_required_to_span_two_artifact_cues": True,
            "selection_basis": "saved_telop_line_sequence_in_cut_with_multiple_artifact_cues",
            "selection_does_not_use_artifact_or_token_start_as_audio_start": True,
            "expected_low_confidence": False,
            "expected_policy_action": "evaluate_normal_alignment_policy",
            "known_ambiguous_review_candidate": False,
        }
    return row


def _fallback_span(source_ids: list[str], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    offset = 0
    parts = []
    for source_id in source_ids:
        origin = sources[source_id]["audio_span_origin_ms"]
        parts.append({"start_ms": origin["start_ms"], "end_ms": origin["end_ms"], "concat_offset_ms": offset})
        offset += origin["end_ms"] - origin["start_ms"]
    return {
        "kind": "concatenated_source_video_audio",
        "coordinate_system": "absolute_video_ms",
        "parts": parts,
        "duration_ms": offset,
    }


def _lb_row(
    info: dict[str, Any],
    target_text: str,
    row_id: str,
    manual_suffix: str | None,
    manifest: dict[str, Any],
    old_by_tuple: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    by_prefix_segment: dict[tuple[str, int], dict[str, Any]],
    manual_original: str,
    lines: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tuple_id = f"lb4_e1ff:s{info['segment_index']}:l{info['line_index']}:{info['start_ms']}-{info['end_ms']}"
    row = copy.deepcopy(old_by_tuple[tuple_id])
    source = by_prefix_segment[("lb4_e1ff", info["segment_index"])]
    cut_number = info["segment_index"]
    pair_numbers = [1, 2] if cut_number in (1, 2) else [2, 3] if cut_number == 3 else [3, 4]
    part_sources = [
        by_prefix_segment[("lb4_e1ff", number)]["source_id"] for number in pair_numbers
    ]
    span = _fallback_span(part_sources, sources)
    target_index = part_sources.index(source["source_id"])
    target_origin = source["audio_span_origin_ms"]
    first_origin = sources[part_sources[0]]["audio_span_origin_ms"]
    second_origin = sources[part_sources[1]]["audio_span_origin_ms"]
    gap_ms = second_origin["start_ms"] - first_origin["end_ms"]
    row.update(
        {
            "row_id": row_id,
            "fixture_group": "vtt_fallback_concat",
            "audio_source_id": source["source_id"],
            "source_hashes": {
                "vtt_sha256": source["vtt_sha256"],
                "raw_timing_sha256": None,
                "source_content_sha256": source["source_content_sha256"],
            },
            "source_boundary_basis": "bound_source_mp4_adjacent_noncontiguous_cut_pair_full_context_non_candidate",
            "source_span": span,
            "absolute_video_span_ms": {"coordinate_system": "absolute_video_ms", **target_origin},
            "target_text": target_text,
            "timing_source": "legacy_vtt_fallback_no_raw_timing",
            "raw_timing_available": False,
            "candidate_or_boundary_fields_are_not_gold": True,
            "source_telop_line_tuple_id": tuple_id + (f":manual:{manual_suffix}" if manual_suffix else ""),
            "manual_pre_measurement_fixture": manual_suffix is not None,
            "coverage_excluded": True,
            "fallback_non_regression_required": True,
            "gold": {
                "line_onset_ms": None,
                "timebase": "source_audio_relative_ms",
                "annotator_id": None,
                "annotated_at": None,
                "audio_listened": False,
            },
            "gold_provenance": "missing_human_audio_annotation",
            "raw_timing_status": "not_applicable_legacy_vtt",
            "timing_input_source_id": None,
        }
    )
    for field in (
        "long_single_cue_context",
        "multi_cross_cue_context",
        "artifact_cross_cue_holdout_context",
        "manual_split",
        "manual_split_evaluation",
    ):
        row.pop(field, None)
    document = manifest["telop_documents"]["lb4_e1ff"]
    row["draft_reference"] = {
        "kind": "manual_pre_measurement_text_split" if manual_suffix else "immutable_telop_line",
        "telop_document_id": "lb4_e1ff",
        "telop_document_path": document["path"],
        "telop_document_sha256": document["sha256"],
        "telop_segment_index": info["segment_index"],
        "telop_line_index": info["line_index"],
        "telop_line_start_ms": info["start_ms"],
        "telop_line_end_ms": info["end_ms"],
        "text_source": "text_only_split_of_saved_legacy_telop_line" if manual_suffix else "saved_production_telop_document",
        "source_cut_id": source["cut_id"],
        "source_boundary_basis": "bound_source_mp4_adjacent_noncontiguous_cut_pair_full_context_non_candidate",
        "candidate_boundary_used": False,
        "gold_must_ignore_telop_time": True,
    }
    row["fallback_concat_context"] = {
        "contract": "legacy_vtt_target_cues_with_adjacent_noncontiguous_bound_source_mp4_context",
        "source_part_source_ids": part_sources,
        "gap_ms": gap_ms,
        "rule": "adjacent_noncontiguous_bound_cut_pair_full_context",
        "candidate_boundary_used": False,
        "target_source_id": source["source_id"],
        "target_part_index": target_index,
        "target_relative_span_ms": {
            "start_ms": info["start_ms"] - target_origin["start_ms"],
            "end_ms": info["end_ms"] - target_origin["start_ms"],
        },
    }
    row["vtt_fallback_context"] = {
        "contract": "legacy_vtt_target_cues_with_adjacent_noncontiguous_bound_source_mp4_context",
        "raw_timing_absent": True,
        "audio_source_is_bound_source_mp4": True,
        "candidate_boundary_used": False,
        "automatic_line_or_cross_cue_moves_max": 0,
        "manual_text_split": manual_suffix is not None,
        "audio_context_is_full_bound_source_mp4_cut_pair": True,
        "cut_pair_gap_ms": gap_ms,
        "target_audio_containment_machine_check": "draft_reference_telop_interval_within_target_cut_part",
        "human_containment_check_required": True,
    }
    if manual_suffix:
        original_info = lines[manual_original]
        boundary = MANUAL_SPLIT_BOUNDARIES[original_info["text"]]
        delimiter = MANUAL_SPLIT_DELIMITERS[original_info["text"]]
        provenance = {
            "kind": "manual_pre_measurement_text_split",
            "rule": "fixed_meaning_boundary_non_candidate",
            "original_source_telop_line_tuple_id": manual_original,
            "original_text": original_info["text"],
            "split_at_codepoint": len(boundary),
            "delimiter_text": delimiter,
            "subtarget": manual_suffix,
            "original_telop_time_not_copied": True,
            "candidate_results_seen": False,
            "gold_requires_human_audio_listening": True,
            "boundary_text": boundary,
        }
        row["manual_split"] = provenance
        row["draft_reference"]["manual_split_provenance"] = copy.deepcopy(provenance)
        row["manual_split_evaluation"] = {
            "scope": "independent_manual_subtarget_fallback_scenario",
            "scenario_id": row_id,
            "scenario_emits_only_this_subtarget": True,
            "sibling_subtargets_coemitted": False,
            "included_in_vtt_non_regression_denominator": True,
            "baseline_draft_time_ms": {"start_ms": info["start_ms"], "end_ms": info["end_ms"]},
            "baseline_is_gold": False,
            "automatic_line_or_cross_cue_moves_max": 0,
            "gold_requires_human_audio_listening": True,
        }
    return row


def _ass_row(
    dialogue: dict[str, Any],
    row_id: str,
    manifest: dict[str, Any],
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    asset = ASS_FALLBACK_EVIDENCE
    source_ids = list(asset["source_ids"])
    target_source_id = next(
        source_id
        for source_id in source_ids
        if sources[source_id]["audio_span_origin_ms"]["start_ms"]
        <= dialogue["source_absolute_start_ms"]
        < sources[source_id]["audio_span_origin_ms"]["end_ms"]
    )
    target_source = sources[target_source_id]
    target_index = source_ids.index(target_source_id)
    target_origin = target_source["audio_span_origin_ms"]
    span = _fallback_span(source_ids, sources)
    expected_relative = {
        "start_ms": dialogue["source_absolute_start_ms"] - target_origin["start_ms"],
        "end_ms": dialogue["source_absolute_end_ms"] - target_origin["start_ms"],
    }
    tuple_id = (
        f"{asset['asset_id']}:event{dialogue['event_index']}:"
        f"{dialogue['source_absolute_start_ms']}-{dialogue['source_absolute_end_ms']}"
    )
    evidence_dialogue = copy.deepcopy(dialogue)
    return {
        "row_id": row_id,
        "fixture_group": "vtt_fallback_concat",
        "audio_source_id": target_source_id,
        "source_hashes": {
            "vtt_sha256": asset["vtt_sha256"],
            "raw_timing_sha256": None,
            "source_content_sha256": asset["source_content_sha256"],
        },
        "source_boundary_basis": "existing_vtt_generated_ass_dialogue_full_noncontiguous_cutplan_context_non_candidate",
        "source_span": span,
        "absolute_video_span_ms": {
            "coordinate_system": "absolute_video_ms",
            "start_ms": target_origin["start_ms"],
            "end_ms": target_origin["end_ms"],
        },
        "vtt_cue_ids": [str(dialogue["vtt_cue_index"])],
        "target_text": dialogue["text"],
        "draft_reference": {
            "kind": "immutable_ass_dialogue",
            "asset_id": asset["asset_id"],
            "ass_path": asset["ass_path"],
            "ass_bytes": asset["ass_bytes"],
            "ass_sha256": asset["ass_sha256"],
            "canonical_clip_id": asset["canonical_clip_id"],
            "event_index": dialogue["event_index"],
            "concat_start_ms": dialogue["concat_start_ms"],
            "concat_end_ms": dialogue["concat_end_ms"],
            "vtt_cue_index": dialogue["vtt_cue_index"],
            "vtt_start_ms": dialogue["vtt_start_ms"],
            "vtt_end_ms": dialogue["vtt_end_ms"],
            "source_absolute_start_ms": dialogue["source_absolute_start_ms"],
            "source_absolute_end_ms": dialogue["source_absolute_end_ms"],
            "text_source": "existing_vtt_generated_ass_dialogue",
            "candidate_boundary_used": False,
            "gold_must_ignore_ass_event_time": True,
        },
        "source_telop_line_tuple_id": tuple_id,
        "manual_pre_measurement_fixture": False,
        "coverage_excluded": True,
        "fallback_non_regression_required": True,
        "gold": {
            "line_onset_ms": None,
            "timebase": "source_audio_relative_ms",
            "annotator_id": None,
            "annotated_at": None,
            "audio_listened": False,
        },
        "gold_provenance": "missing_human_audio_annotation",
        "timing_source": "legacy_vtt_fallback_no_raw_timing",
        "raw_timing_available": False,
        "candidate_or_boundary_fields_are_not_gold": True,
        "raw_timing_status": "not_applicable_legacy_vtt",
        "timing_input_source_id": None,
        "ass_dialogue_context": evidence_dialogue,
        "ass_concat_context": {
            "contract": "existing_vtt_generated_ass_dialogue_full_noncontiguous_cutplan_context",
            "asset_id": asset["asset_id"],
            "canonical_clip_id": asset["canonical_clip_id"],
            "source_part_source_ids": source_ids,
            "gap_ms": [294000, 34000],
            "concat_duration_ms": span["duration_ms"],
            "target_source_id": target_source_id,
            "target_part_index": target_index,
            "target_relative_span_ms": expected_relative,
            "candidate_boundary_used": False,
            "telop_script": None,
        },
        "vtt_fallback_context": {
            "contract": "legacy_vtt_target_cues_with_existing_ass_concat_context",
            "raw_timing_absent": True,
            "audio_source_is_bound_source_mp4": True,
            "candidate_boundary_used": False,
            "automatic_line_or_cross_cue_moves_max": 0,
            "audio_context_is_full_bound_source_mp4_cutplan_three_part": True,
            "human_containment_check_required": True,
        },
    }


def build_manifest(previous: dict[str, Any]) -> dict[str, Any]:
    _preflight_ass_evidence()
    result = copy.deepcopy(previous)
    _add_b5_source_entries(result)
    lines = _load_lines(result)
    sources, by_prefix_segment = _source_maps(result)
    old_rows = previous["rows"]
    old_by_tuple = _old_rows_by_tuple(previous)
    artifact_payloads = {
        document_id: json.loads(Path(document["path"]).read_text(encoding="utf-8"))
        for document_id, document in result["artifact_documents"].items()
    }
    long_ids = sorted(ALIGNMENT_LONG_TUPLE_IDS, key=lambda item: (lines[item]["prefix"], lines[item]["segment_index"], lines[item]["line_index"]))
    multi_ids = sorted(ALIGNMENT_MULTI_TUPLE_IDS, key=lambda item: (lines[item]["prefix"], lines[item]["segment_index"], lines[item]["line_index"]))
    rows = [
        _artifact_row(item, f"t1-long-{index:03d}", "long_single_cue", previous, old_by_tuple, old_rows, lines, sources, by_prefix_segment, artifact_payloads)
        for index, item in enumerate(long_ids, 1)
    ]
    multi_rows = [
        _artifact_row(item, f"t1-multi-{index:03d}", "multi_cross_cue", result, old_by_tuple, old_rows, lines, sources, by_prefix_segment, artifact_payloads)
        for index, item in enumerate(multi_ids, 1)
    ]
    holdout_ids = sorted(KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS, key=lambda item: (lines[item]["prefix"], lines[item]["segment_index"], lines[item]["line_index"]))
    for index, tuple_id in enumerate(holdout_ids, 21):
        row = _artifact_row(tuple_id, f"t1-multi-{index:03d}", "multi_cross_cue", result, old_by_tuple, old_rows, lines, sources, by_prefix_segment, artifact_payloads)
        source = by_prefix_segment[(lines[tuple_id]["prefix"], lines[tuple_id]["segment_index"])]
        _, _, _, cue_indices = _artifact_context(lines[tuple_id], source, lines, artifact_payloads, by_prefix_segment)
        row["coverage_excluded"] = False
        row["multi_cross_cue_context"]["expected_low_confidence"] = True
        row["multi_cross_cue_context"]["expected_policy_action"] = "flag_low_confidence_preserve_draft_time"
        row["multi_cross_cue_context"]["known_ambiguous_review_candidate"] = True
        row["multi_cross_cue_context"]["holdout_subtype"] = "artifact_cross_cue_holdout"
        row["artifact_cross_cue_holdout_context"] = {
            "subtype": "artifact_cross_cue_holdout",
            "coverage_excluded": False,
            "expected_low_confidence": True,
            "expected_policy_action": "flag_low_confidence_preserve_draft_time",
            "candidate_results_seen": False,
            "artifact_cue_indices_in_same_cut": cue_indices,
            "selection_basis": "known_ambiguous_cross_cue_multi_rows_from_pre_measurement_audit",
            "target_text_is_saved_telop_line": True,
            "audio_span_basis": "whole_existing_cut_range",
            "coverage_denominator": "multi_cross_cue_24",
        }
        row["fallback_non_regression_required"] = False
        multi_rows.append(row)
    rows.extend(multi_rows)
    lb_ids = sorted((item for item in lines if lines[item]["prefix"] == "lb4_e1ff"), key=lambda item: (lines[item]["segment_index"], lines[item]["line_index"]))
    manual_original = next(iter(MANUAL_SPLIT_ORIGINAL_TUPLE_IDS))
    manual_base = manual_original.split(":manual:", 1)[0]
    row_number = 1
    for tuple_id in lb_ids:
        info = lines[tuple_id]
        if tuple_id == manual_base:
            original = info["text"]
            boundary = MANUAL_SPLIT_BOUNDARIES[original]
            delimiter = MANUAL_SPLIT_DELIMITERS[original]
            suffix = original[len(boundary) + len(delimiter):]
            rows.append(_lb_row(info, boundary, f"t1-fallback-{row_number:03d}", "a", previous, old_by_tuple, sources, by_prefix_segment, manual_original, lines))
            row_number += 1
            rows.append(_lb_row(info, suffix, f"t1-fallback-{row_number:03d}", "b", previous, old_by_tuple, sources, by_prefix_segment, manual_original, lines))
            row_number += 1
        else:
            rows.append(_lb_row(info, info["text"], f"t1-fallback-{row_number:03d}", None, previous, old_by_tuple, sources, by_prefix_segment, manual_original, lines))
            row_number += 1
    rows.extend(
        _ass_row(dialogue, f"t1-fallback-{index:03d}", result, sources)
        for index, dialogue in enumerate(ASS_FALLBACK_EVIDENCE["dialogues"], 17)
    )
    if len(rows) != 64:
        raise ValueError(f"generated row count is not 64: {len(rows)}")
    result["manifest_revision"] = "corrected_pre_measurement_freeze_v3_7"
    predecessors = list(previous["supersedes"]["official_freeze_predecessors"])
    if not any(item.get("commit") == "9e66122" for item in predecessors):
        predecessors.append({
            "commit": "9e66122",
            "manifest_fingerprint": previous["manifest_fingerprint"],
            "status": "review_rejected_draft",
            "candidate_measurements_run": 0,
            "reason": "one manual split only, explicit artifact holdout rows, and adjacent noncontiguous LB4 cut-pair context supersede v3.4",
        })
    if not any(item.get("commit") == "d94a8c5" for item in predecessors):
        predecessors.append({
            "commit": "d94a8c5",
            "manifest_fingerprint": "64fa7b3b5cf26dbdfc956d516d06b7c526b2d4e558e24388c417aaa0d6ba9cba",
            "status": "review_rejected_draft",
            "candidate_measurements_run": 0,
            "reason": "pre-freeze audit required generator source preflight, safe output/previous path allowlists, and explicit fallback-subtype policy wording",
        })
    for predecessor in predecessors:
        if predecessor.get("commit") == "d94a8c5":
            predecessor["reason"] = "pre-freeze audit required generator source preflight, safe output/previous path allowlists, and explicit fallback-subtype policy wording"
    if not any(item.get("commit") == "d94a8c5" and item.get("manifest_fingerprint") == "4ffd5b5ce0d30d4b42cdf0d13c61f595d9b51748ef7d2cff22cf36a42551228f" for item in predecessors):
        predecessors.append({
            "commit": "d94a8c5",
            "manifest_fingerprint": "4ffd5b5ce0d30d4b42cdf0d13c61f595d9b51748ef7d2cff22cf36a42551228f",
            "status": "review_rejected_draft",
            "candidate_measurements_run": 0,
            "reason": "artifact holdout 4 was not genuine VTT fallback plus concat; corrected v3.7 replaces it with b5d existing VTT/ASS evidence and moves the four artifact rows into multi low-confidence coverage",
        })
    for predecessor in predecessors:
        if predecessor.get("manifest_fingerprint") == "4ffd5b5ce0d30d4b42cdf0d13c61f595d9b51748ef7d2cff22cf36a42551228f":
            predecessor["reason"] = "artifact holdout 4 was not genuine VTT fallback plus concat; corrected v3.7 replaces it with b5d existing VTT/ASS evidence and moves the four artifact rows into multi low-confidence coverage"
    result["supersedes"] = {
        "official_freeze_predecessors": predecessors,
        "replacement_reason": "v3.7 replaces the rejected artifact-holdout fallback subtype with four independently bound existing VTT/ASS dialogues from the non-contiguous three-part b5d cutplan. The final contract is long_single_cue 20, multi_cross_cue 24 including four low-confidence artifact holdouts, and genuine VTT fallback concat 20 including four ASS/VTT scenarios. Candidate measurements and human gold remain zero.",
        "candidate_measurements_run": 0,
    }
    result["vtt_fallback_evidence"] = copy.deepcopy(ASS_FALLBACK_EVIDENCE)
    result["limits"] = copy.deepcopy(result["limits"])
    result["limits"]["audio_context_span_count"] = 15
    result["production_integrity_contract"] = copy.deepcopy(result["production_integrity_contract"])
    result["production_integrity_contract"]["bound_manifest_sources"] = {
        "count": 15,
        "source_ids": sorted(source["source_id"] for source in result["sources"].values()),
    }
    result["production_integrity_contract"]["bound_benchmark_evidence"] = ass_evidence_integrity_contract(
        ASS_FALLBACK_EVIDENCE
    )
    result["production_integrity_contract"]["after_measurement_rehash"] = copy.deepcopy(
        result["production_integrity_contract"]["after_measurement_rehash"]
    )
    result["production_integrity_contract"]["after_measurement_rehash"]["benchmark_evidence"] = (
        "re-hash the bound b5d ASS, VTT, cutplan, and ffmpeg log bytes/SHA before candidate measurement and after measurement"
    )
    result["selection_policy"] = {
        "source_scope": "pre_T1 immutable saved telop docs, 15 distinct read-only audio context spans, existing VTT/cut ranges, cue-only artifact provenance, and bound LB4 source MP4 plus isolated b5d VTT/ASS evidence",
        "read_only_inputs": True,
        "candidate_results_seen_before_freeze": False,
        "whisper_segment_or_token_boundary_used_for_target_or_audio_span": False,
        "artifact_json_raw_token_timing_available": False,
        "artifact_selection_rule": "deterministic saved document/segment/line tuple allocation fixed by the pre-measurement audit; no row overlap",
        "alignment_long_tuple_ids": sorted(ALIGNMENT_LONG_TUPLE_IDS),
        "alignment_multi_tuple_ids": sorted(ALIGNMENT_MULTI_TUPLE_IDS),
        "artifact_holdout_tuple_ids": sorted(KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS),
        "artifact_holdout_rule": "the four known ambiguous artifact tuples remain in multi_cross_cue, are included in the 24-row coverage denominator, and must be flagged low-confidence while preserving draft time",
        "fallback_selection_rule": "15 genuine saved LB4 VTT line/time tuples are represented as 14 exact targets plus one original line replaced by two text-only subtargets; no other source line is split and the original is not separately counted",
        "manual_split_original": manual_original,
        "manual_split_targets": ["やばい", "止まってないね"],
        "manual_split_delimiter_provenance": "、 is retained only in the explicit rejoin provenance; it is not copied into either subtarget gold text",
        "ass_fallback_dialogue_event_indices": [item["event_index"] for item in ASS_FALLBACK_EVIDENCE["dialogues"]],
        "ass_fallback_asset_id": ASS_FALLBACK_EVIDENCE["asset_id"],
        "fixture_allocation": "59 unique saved line/time tuples = 44 artifact alignment + 15 LB4 saved source lines; the one manual replacement contributes net +1 target, and four existing b5d ASS/VTT dialogue scenarios add four non-telop target rows for 64 total rows.",
    }
    result["fixture_counts"] = {
        "long_single_cue": 20,
        "multi_cross_cue": 24,
        "vtt_fallback_concat": 20,
        "saved_telop_unique_line_time_tuples": 59,
        "artifact_backed_rows_available": 44,
        "artifact_backed_rows_selected_for_alignment": 44,
        "artifact_cross_cue_holdout_rows": 4,
        "artifact_backed_rows_unused": 0,
        "ass_dialogue_rows": 4,
        "legacy_lb4_saved_source_lines": 15,
        "legacy_lb4_exact_target_rows": 14,
        "manual_split_base_lines": 1,
        "manual_split_subtargets": 2,
        "saved_target_rows_without_manual_split": 58,
        "non_manual_target_rows": 62,
        "total": 64,
    }
    policy = copy.deepcopy(previous["policy"])
    policy.update(
        {
            "coverage_denominator": "human gold が揃った artifact alignment 44行。long 20とmulti 24を群別に判定し、multi 24には既知ambiguous artifact holdout 4行を含めてcoverageへ入れる。",
            "coverage_excluded": ["vtt_fallback_concat", "timing_missing_legacy"],
            "fallback_non_regression": {
                "required": "現行VTT fallback + 非連続cut連結と同等",
                "automatic_line_or_cross_cue_moves_max": 0,
                "denominator": "all 20 vtt_fallback_concat rows: LB4 16 targets plus four independently bound b5d ASS/VTT dialogue scenarios; each manual subtarget is an independent one-subtarget scenario",
                "manual_split_scenarios": {
                    "included_in_vtt_non_regression_denominator": True,
                    "scenario_model": "replace_original_line_with_this_subtarget_only",
                    "sibling_subtargets_coemitted": False,
                    "baseline": "original immutable saved telop row time per independent scenario; reference only, never gold",
                    "automatic_line_or_cross_cue_moves_max": 0,
                },
            },
            "fixture_validity": {
                "long_single_cue": "監査固定20件。同一artifact cue内の保存済みtelop行から後続14件とcue-start anchor 6件を含め、全体がcue開始発話だけにならないよう固定する。audio spanはartifact/token開始ではなく既存cut range全体。",
                "multi_cross_cue": "監査固定24件。同一cut rangeに2つ以上のartifact cueが存在するline sequenceから通常20件を選び、既知ambiguous artifact cross-cue holdout 4件も同群のcoverage分母へ含める。1行が必ず複数cueを跨ぐとは仮定しない。holdout 4件はexpected low-confidence、元draft時刻維持、黙った移動0を判定する。",
                "vtt_fallback_concat": "fallback 20件はLB4 genuine VTT 16 targetと、既存b5d VTTからisolated再現でbyte-for-byte bindしたASS Dialogue 4件で構成する。全20件をfallback非回帰分母へ含める。LB4 16 targetは実際に非連続な隣接bound cut pair full context、b5d 4件は非連続3-part cutplan全体を使い、gap、concat offset、duration、target relative containmentを検証する。",
                "manual_fixture": "manual subtargetは1原文のみ。targetは「やばい」/「止まってないね」、delimiter「、」はrejoin provenanceへ明示し、原文再結合時に文字欠落・重複がないことを検証する。各subtargetは兄弟を同時出力しない独立scenarioで、元行保存時刻はbaseline referenceのみでgoldではない。",
            },
            "human_containment_audit": {
                "machine_check": "LB4 16 target draft_reference telop intervals must be within their target bound cut part and source MP4 concat span; b5d ASS/VTT 4 target intervals must satisfy ASS centisecond concat-to-absolute mapping and VTT cue/clamp containment; neither is gold",
                "human_check": "LB4 fallback 16 targetはfull adjacent noncontiguous bound cut-pair contextを聞き、b5d ASS/VTT 4件はfull noncontiguous three-part contextを聞く。対象発話が存在しない、または曖昧ならonsetを入力せずfail closedとする。",
                "candidate_boundary_isolation": True,
            },
        }
    )
    result["policy"] = policy
    human_gold = copy.deepcopy(previous["human_gold"])
    human_gold["completion_rule"] = "全64行で audio_listened=true、gold.line_onset_msが整数、timebaseがsource_audio_relative_ms、annotator_idとannotated_atが非空になるまで測定不可。"
    human_gold["manual_subtarget_rule"] = "manual split subtargetのgoldも音声を実際に再生して決め、split位置や元行時刻を候補として入力しない。manual rowsは兄弟subtargetを同時出力しない独立scenarioで、元行保存時刻はbaseline referenceのみである。"
    result["human_gold"] = human_gold
    result["notes"] = [
        "24c777d、9822ff2、9e66122、d94a8c5、4ffd5b5ce0d30d4b42cdf0d13c61f595d9b51748ef7d2cff22cf36a42551228fを含む従前draftは候補測定0のreview-rejected draftであり、corrected v3.7だけを正式pre-measurement freeze候補とする。",
        "artifact 44件の重複なし割当は監査固定のlong 20 / multi通常20 / multi low-confidence holdout 4。holdout 4件はmulti coverage分母へ含め、low-confidence、元draft時刻維持、黙った移動0を判定する。",
        "configured ffmpeg の subprocess 直前hash再検証とLB4 source MP4の1000 ms isolated smoke（16000 frames、mono 16 kHz PCM、出力未commit）はPASS済み。",
        "artifact JSONはcue-onlyでraw token timingを保持しない。8 artifact-backed WAV spanはmanifest固定後にbounded whisper-cliをisolated tempで予定するが、freeze時点のinvocation_countは0。",
        "音声bytesはcommitしない。15 audio context spanのうちgZA/hPe 8 spanだけが将来のbounded Whisper入力、LB4旧4 spanとb5d cutplan003 3 spanはsource MP4からのisolated extraction playback契約に固定する。",
        "LB4旧audio_cache WAVは要求spanと実測durationが異なるlegacy cacheとしてrejected evidenceに留め、gold context・playbackには使用しない。requested_duration_msとactual_audio_duration_msを分離し、source MP4 SHA-256をbindする。",
        "LB4 fallbackはcut1+cut2、cut2+cut3、cut3+cut4の実在non-contiguous adjacent pairを使う。gap>0、target relative span、concat durationを機械検証し、候補境界やVTT cueで音声を切らない。",
        "b5d fallback 4行は既存ASS Dialogue event、production VTT delta cue、cutplan003の3 part absolute mapping、canonical clip ID、ffmpeg logを別々に機械検証し、ASS centisecond時刻とVTT millisecond時刻を混同しない。",
        "human goldが64行揃い、bounded timing入力が契約どおり生成されるまで候補測定、Go/No-Go、T1-2着手、AC-40更新は行わない。",
    ]
    result["rows"] = rows
    result.pop("manifest_fingerprint", None)
    result["manifest_fingerprint"] = manifest_fingerprint(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--test-root",
        type=Path,
        help="テスト時だけ許可する一時ディレクトリ root。production/repo path は許可しない。",
    )
    args = parser.parse_args(argv)
    repository_benchmark_root = _repository_root() / "benchmarks" / "t1"
    test_root = _resolve_test_root(args.test_root) if args.test_root is not None else None
    allowed_previous_roots = (repository_benchmark_root,)
    allowed_output_roots = (repository_benchmark_root,)
    if test_root is not None:
        allowed_previous_roots += (test_root,)
        # A test invocation may read the canonical previous manifest or a
        # copied one, but it may write only inside the explicitly supplied
        # disposable root.
        allowed_output_roots = (test_root,)
    previous_path = _resolve_allowed_path(
        args.previous,
        label="previous manifest",
        roots=allowed_previous_roots,
    )
    output_path = _resolve_allowed_path(
        args.output,
        label="output manifest",
        roots=allowed_output_roots,
    )
    if previous_path.suffix.lower() != ".json" or output_path.suffix.lower() != ".json":
        raise ValueError("previous/output は JSON ファイルに限定されます。")
    canonical_manifest_path = repository_benchmark_root / "manifest.json"
    if test_root is None and (
        previous_path != canonical_manifest_path.resolve()
        or output_path != canonical_manifest_path.resolve()
    ):
        raise ValueError("production 実行の previous/output は benchmarks/t1/manifest.json に限定されます。")
    previous = _load_validated_previous(previous_path)
    candidate = build_manifest(previous)
    validate_manifest(candidate, check_sources=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"manifest_fingerprint": candidate["manifest_fingerprint"], "row_count": len(candidate["rows"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
