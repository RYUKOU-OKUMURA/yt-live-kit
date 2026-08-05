from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.t1.candidate_measurement import (
    MATCH_RATIO_THRESHOLD,
    AlignmentMatch,
    CandidateMeasurementError,
    REPORT_SCHEMA,
    RowPrediction,
    aggregate_group_metrics,
    build_report,
    build_token_timeline,
    draft_onset_row_ms,
    evaluate_gate,
    extract_whisper_tokens,
    find_best_token_window,
    normalize_text,
    predict_rows,
    segment_snap_onset,
)


def _alignment_row(
    row_id: str,
    *,
    target_text: str,
    draft_start: int,
    draft_end: int,
    duration_ms: int = 10000,
    audio_source_id: str = "cut-audio",
    fixture_group: str = "long_single_cue",
    expected_low_confidence: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "row_id": row_id,
        "fixture_group": fixture_group,
        "audio_source_id": audio_source_id,
        "source_span": {"kind": "single_source_audio", "start_ms": 0, "end_ms": duration_ms},
        "absolute_video_span_ms": {"start_ms": 1000, "end_ms": 1000 + duration_ms},
        "target_text": target_text,
        "draft_reference": {
            "telop_line_start_ms": 1000 + draft_start,
            "telop_line_end_ms": 1000 + draft_end,
        },
        "gold": {
            "line_onset_ms": None,
            "timebase": "source_audio_relative_ms",
            "annotator_id": None,
            "annotated_at": None,
            "audio_listened": False,
        },
    }
    if expected_low_confidence:
        row["multi_cross_cue_context"] = {"expected_low_confidence": True}
        row["artifact_cross_cue_holdout_context"] = {"expected_low_confidence": True}
    return row


def _fallback_row(row_id: str, *, draft_start: int, draft_end: int) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "fixture_group": "vtt_fallback_concat",
        "audio_source_id": "lb4-cut-audio",
        "source_span": {
            "kind": "concatenated_source_video_audio",
            "coordinate_system": "absolute_video_ms",
            "parts": [{"start_ms": 1000, "end_ms": 5000, "concat_offset_ms": 0}],
            "duration_ms": 4000,
        },
        "absolute_video_span_ms": {
            "coordinate_system": "absolute_video_ms",
            "start_ms": 1000,
            "end_ms": 5000,
        },
        "target_text": "fallback line",
        "draft_reference": {
            "telop_line_start_ms": 1000 + draft_start,
            "telop_line_end_ms": 1000 + draft_end,
        },
        "fallback_concat_context": {
            "target_part_index": 0,
            "target_relative_span_ms": {"start_ms": draft_start, "end_ms": draft_end},
        },
        "gold": {
            "line_onset_ms": None,
            "timebase": "source_audio_relative_ms",
            "annotator_id": None,
            "annotated_at": None,
            "audio_listened": False,
        },
    }


def _whisper_payload(tokens: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "systeminfo": "x",
        "model": {},
        "params": {},
        "result": {},
        "transcription": [{"text": "x", "offsets": {"from": 0, "to": 1000}, "tokens": tokens}],
    }


def test_normalize_text_strips_punctuation() -> None:
    assert normalize_text("こんにちは、世界！") == "こんにちは世界"


def test_aligner_finds_known_position() -> None:
    tokens = extract_whisper_tokens(
        _whisper_payload(
            [
                {"text": "あ", "offsets": {"from": 100, "to": 200}},
                {"text": "い", "offsets": {"from": 200, "to": 300}},
                {"text": "う", "offsets": {"from": 300, "to": 400}},
            ]
        )
    )
    timeline = build_token_timeline(tokens)
    match = find_best_token_window("い", timeline)
    assert match.confident is True
    assert match.start_ms == 200


def test_aligner_low_confidence_preserves_draft(tmp_path: Path) -> None:
    manifest = {
        "benchmark_id": "test",
        "manifest_fingerprint": "x",
        "policy": {"provisional_gate": {}},
        "runtime": {"settings": {"initial_prompt": ""}},
        "sources": {
            "cut": {
                "source_id": "cut-audio",
                "audio_duration_ms": 10000,
            }
        },
        "rows": [_alignment_row("t1-long-001", target_text="存在しない語句", draft_start=500, draft_end=1500)],
    }
    packet = {
        "rows": [
            {
                **_alignment_row("t1-long-001", target_text="存在しない語句", draft_start=500, draft_end=1500),
                "gold": {
                    "line_onset_ms": 600,
                    "timebase": "source_audio_relative_ms",
                    "annotator_id": "tester",
                    "annotated_at": "2026-08-05T00:00:00+00:00",
                    "audio_listened": True,
                },
            }
        ]
    }
    timing_dir = tmp_path / "timing"
    timing_dir.mkdir()
    (timing_dir / "cut-audio.json").write_text(
        json.dumps(
            _whisper_payload([{"text": "全然違う", "offsets": {"from": 1000, "to": 2000}}])
        ),
        encoding="utf-8",
    )
    timing_evidence = {
        "sources": [
            {
                "source_id": "cut-audio",
                "output_json_path": str(timing_dir / "cut-audio.json"),
                "wall_time_ms": 1,
                "peak_memory_bytes": 1,
                "output_json_sha256": "abc",
            }
        ]
    }
    predictions, _ = predict_rows(manifest, packet, timing_dir, timing_evidence)
    token = next(item for item in predictions if item.candidate == "token_alignment")
    assert token.low_confidence is True
    assert token.predicted_onset_ms == token.draft_onset_ms == 500


def test_monotonicity_violation_detected(tmp_path: Path) -> None:
    manifest = {
        "benchmark_id": "test",
        "manifest_fingerprint": "x",
        "policy": {"provisional_gate": {}},
        "runtime": {"settings": {"initial_prompt": ""}},
        "sources": {"cut": {"source_id": "cut-audio", "audio_duration_ms": 10000}},
        "rows": [
            _alignment_row("t1-long-001", target_text="う", draft_start=100, draft_end=500),
            _alignment_row("t1-long-002", target_text="あ", draft_start=900, draft_end=1500),
        ],
    }
    packet = {
        "rows": [
            {
                **manifest["rows"][0],
                "gold": {
                    "line_onset_ms": 300,
                    "timebase": "source_audio_relative_ms",
                    "annotator_id": "tester",
                    "annotated_at": "2026-08-05T00:00:00+00:00",
                    "audio_listened": True,
                },
            },
            {
                **manifest["rows"][1],
                "gold": {
                    "line_onset_ms": 100,
                    "timebase": "source_audio_relative_ms",
                    "annotator_id": "tester",
                    "annotated_at": "2026-08-05T00:00:00+00:00",
                    "audio_listened": True,
                },
            },
        ]
    }
    timing_dir = tmp_path / "timing"
    timing_dir.mkdir()
    (timing_dir / "cut-audio.json").write_text(
        json.dumps(
            _whisper_payload(
                [
                    {"text": "あ", "offsets": {"from": 100, "to": 200}},
                    {"text": "う", "offsets": {"from": 300, "to": 400}},
                ]
            )
        ),
        encoding="utf-8",
    )
    timing_evidence = {
        "sources": [
            {
                "source_id": "cut-audio",
                "output_json_path": str(timing_dir / "cut-audio.json"),
            }
        ]
    }
    predictions, _ = predict_rows(manifest, packet, timing_dir, timing_evidence)
    second = next(item for item in predictions if item.row_id == "t1-long-002" and item.candidate == "token_alignment")
    assert second.monotonicity_violation is True
    assert second.low_confidence is True
    assert second.predicted_onset_ms == second.draft_onset_ms


def test_gate_boundary_values() -> None:
    thresholds = {
        "coverage_min": 0.8,
        "absolute_onset_median_max_ms": 250,
        "p90_max_ms": 500,
        "max_error_max_ms": 1000,
        "signed_median_bias_abs_max_ms": 200,
        "wrong_line_or_cross_cue_moves_max": 0,
    }
    pass_metrics = {
        "coverage": 0.8,
        "median_abs_error_ms": 250,
        "p90_abs_error_ms": 500,
        "max_abs_error_ms": 1000,
        "signed_median_bias_ms": 200,
        "wrong_line_or_cross_cue_moves": 0,
    }
    fail_metrics = dict(pass_metrics)
    fail_metrics["coverage"] = 0.79
    assert evaluate_gate(pass_metrics, thresholds)["pass"] is True
    assert evaluate_gate(fail_metrics, thresholds)["pass"] is False


def test_fallback_rows_do_not_move(tmp_path: Path) -> None:
    manifest = {
        "benchmark_id": "test",
        "manifest_fingerprint": "x",
        "policy": {"provisional_gate": {}},
        "runtime": {"settings": {"initial_prompt": ""}},
        "sources": {},
        "rows": [_fallback_row("t1-fallback-001", draft_start=1000, draft_end=2000)],
    }
    packet = {"rows": manifest["rows"]}
    predictions, _ = predict_rows(manifest, packet, tmp_path, {"sources": []})
    for item in predictions:
        assert item.movement_ms == 0
        assert item.predicted_onset_ms == item.draft_onset_ms


def test_fail_closed_row_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchmarks.t1.candidate_measurement.validate_manifest",
        lambda manifest, **kwargs: {"manifest_fingerprint": "x"},
    )
    manifest = {
        "benchmark_id": "test",
        "manifest_fingerprint": "x",
        "policy": {
            "provisional_gate": {
                "pooled": {
                    "coverage_min": 0.0,
                    "absolute_onset_median_max_ms": 99999,
                    "p90_max_ms": 99999,
                    "max_error_max_ms": 99999,
                    "signed_median_bias_abs_max_ms": 99999,
                    "wrong_line_or_cross_cue_moves_max": 99,
                },
                "long_single_cue": {
                    "coverage_min": 0.0,
                    "absolute_onset_median_max_ms": 99999,
                    "p90_max_ms": 99999,
                    "max_error_max_ms": 99999,
                    "signed_median_bias_abs_max_ms": 99999,
                    "wrong_line_or_cross_cue_moves_max": 99,
                },
                "multi_cross_cue": {
                    "coverage_min": 0.0,
                    "absolute_onset_median_max_ms": 99999,
                    "p90_max_ms": 99999,
                    "max_error_max_ms": 99999,
                    "signed_median_bias_abs_max_ms": 99999,
                    "wrong_line_or_cross_cue_moves_max": 99,
                },
            }
        },
        "runtime": {"settings": {"initial_prompt": ""}},
        "sources": {},
        "rows": [_fallback_row("t1-fallback-008", draft_start=1000, draft_end=2000)],
    }
    packet = {"rows": manifest["rows"]}
    predictions, _ = predict_rows(manifest, packet, tmp_path, {"sources": []})
    fail_closed = [item for item in predictions if item.fail_closed]
    assert len(fail_closed) == 3
    report = build_report(
        manifest,
        packet,
        {"sources": []},
        predictions,
        {},
        reproduce_command="test",
    )
    assert report["schema"] == REPORT_SCHEMA
    assert report["fail_closed_records"][0]["row_id"] == "t1-fallback-008"


def test_report_json_schema_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchmarks.t1.candidate_measurement.validate_manifest",
        lambda manifest, **kwargs: {"manifest_fingerprint": "x"},
    )
    predictions = [
        RowPrediction(
            row_id="t1-long-001",
            fixture_group="long_single_cue",
            candidate="token_alignment",
            audio_source_id="cut-audio",
            duration_ms=10000,
            draft_onset_ms=100,
            predicted_onset_ms=120,
            gold_onset_ms=110,
            confident=True,
            low_confidence=False,
            low_confidence_reasons=[],
            monotonicity_violation=False,
            wrong_line_or_cross_cue_move=False,
            validity_failed=False,
            validity_reasons=[],
            silent_move=False,
            movement_ms=20,
            error_ms=10,
            excluded_from_coverage=False,
        )
    ]
    manifest = {
        "benchmark_id": "test",
        "manifest_fingerprint": "x",
        "policy": {
            "provisional_gate": {
                group: {
                    "coverage_min": 0.0,
                    "absolute_onset_median_max_ms": 99999,
                    "p90_max_ms": 99999,
                    "max_error_max_ms": 99999,
                    "signed_median_bias_abs_max_ms": 99999,
                    "wrong_line_or_cross_cue_moves_max": 99,
                }
                for group in ("pooled", "long_single_cue", "multi_cross_cue")
            }
        },
    }
    report = build_report(manifest, {"rows": []}, {"sources": []}, predictions, {}, reproduce_command="test")
    assert "metrics_by_candidate" in report
    assert "gate_by_candidate" in report
    assert report["row_results"][0]["row_id"] == "t1-long-001"


def test_draft_onset_row_ms_single_source() -> None:
    row = _alignment_row("x", target_text="a", draft_start=250, draft_end=1000)
    assert draft_onset_row_ms(row) == 250


def test_segment_snap_uses_segment_start() -> None:
    payload = {
        "transcription": [
            {"text": "あいう", "offsets": {"from": 50, "to": 150}},
            {"text": "かきく", "offsets": {"from": 500, "to": 700}},
        ]
    }
    match = segment_snap_onset("かき", payload)
    assert match.start_ms == 500


def test_aggregate_group_metrics_coverage_denominator() -> None:
    rows = [
        RowPrediction(
            row_id="a",
            fixture_group="long_single_cue",
            candidate="token_alignment",
            audio_source_id="cut",
            duration_ms=1000,
            draft_onset_ms=0,
            predicted_onset_ms=0,
            gold_onset_ms=0,
            confident=True,
            low_confidence=False,
            low_confidence_reasons=[],
            monotonicity_violation=False,
            wrong_line_or_cross_cue_move=False,
            validity_failed=False,
            validity_reasons=[],
            silent_move=False,
            movement_ms=0,
            error_ms=0,
            excluded_from_coverage=False,
        ),
        RowPrediction(
            row_id="b",
            fixture_group="long_single_cue",
            candidate="token_alignment",
            audio_source_id="cut",
            duration_ms=1000,
            draft_onset_ms=0,
            predicted_onset_ms=0,
            gold_onset_ms=0,
            confident=False,
            low_confidence=True,
            low_confidence_reasons=["x"],
            monotonicity_violation=False,
            wrong_line_or_cross_cue_move=False,
            validity_failed=False,
            validity_reasons=[],
            silent_move=False,
            movement_ms=0,
            error_ms=None,
            excluded_from_coverage=False,
        ),
    ]
    metrics = aggregate_group_metrics(rows)
    assert metrics["denominator"] == 2
    assert metrics["confident_count"] == 1
    assert metrics["coverage"] == 0.5
