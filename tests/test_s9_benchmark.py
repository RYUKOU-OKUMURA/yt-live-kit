from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
from types import SimpleNamespace
import subprocess

import pytest
from yt_live_kit.services.vtt_parser import Cue as ProductionCue
from yt_live_kit.services.vtt_parser import deduplicate_progressive as production_deduplicate_progressive

from benchmarks.s9_benchmark import (
    GateConfig,
    FingerprintError,
    ModelValidationError,
    NormalizationConfig,
    SchemaError,
    TimeRange,
    VttCue,
    build_whisper_argv,
    canonical_json_bytes,
    character_error_rate,
    cue_inclusion_metrics,
    evaluate_gates,
    generate_report,
    glossary_exact_match,
    levenshtein_distance,
    manifest_fingerprint,
    normalize_ja_text,
    paired_median_cer_relative_improvement,
    parse_timestamp_ms,
    parse_vtt,
    parse_whisper_json,
    run_whisper_cli,
    select_cues_by_overlap,
    sha256_file,
    validate_model_metadata,
)
from benchmarks.s9_benchmark import _parse_peak_rss
from benchmarks.s9_benchmark import _normalize_protocol_manifest


def _write_model(tmp_path: Path, content: bytes = b"model-fixture") -> dict[str, object]:
    path = tmp_path / "model.bin"
    path.write_bytes(content)
    return {
        "name": "fixture-model.bin",
        "path": str(path),
        "sha256": sha256(content).hexdigest(),
        "bytes": len(content),
        "distribution_url": "https://example.invalid/manual-model",
    }


def _manifest(tmp_path: Path, *, audit: str = "audited", candidate_payload: dict | None = None) -> dict:
    baseline = tmp_path / "baseline.vtt"
    baseline.write_text(
        "WEBVTT\n\n"
        "a1\n00:00:01.000 --> 00:00:02.000\nフロードです\n\n",
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            candidate_payload
            or {
                "schema": "s9-1-candidate-v1",
                "cues": [{"start_ms": 1000, "end_ms": 2000, "text": "クロードです"}],
                "metrics": {"wall_time_ms": 50, "peak_memory_bytes": 100},
                "run_kind": "warm",
                "cache_hit": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "schema": "s9-1-benchmark-manifest-v1",
        "benchmark_id": "fixture-s9",
        "gold_audit_status": audit,
        "normalization": {"unicode_form": "NFKC", "remove_whitespace": True, "ignore_punctuation": False},
        "cue_inclusion_rule": {"kind": "overlap_half_open"},
        "evaluation": {
            "relative_cer_improvement_min": 0.10,
            "glossary_non_regression": True,
            "cue_error_rate_delta_max": 0.05,
            "wall_time_budget_ms": 1000,
            "peak_memory_budget_bytes": 1000,
            "fail_closed_unaudited_gold": True,
        },
        "model": _write_model(tmp_path),
        "whisper": {
            "binary_path": str(tmp_path / "whisper-cli"),
            "version": "1.9.1",
            "build": "fixture",
            "capabilities": ["json", "timestamps"],
            "settings": {
                "language": "ja",
                "initial_prompt": "クロード",
                "padding_ms": 100,
                "decode": {"temperature": 0.0, "beam_size": 5},
                "output_schema": "whisper.cpp-json-v1",
            },
            "timeout_sec": 5,
        },
        "cache_policy": {"mode": "declared", "run_kinds": ["cold", "warm"], "repeat_count": 2},
        "glossary": [{"term": "クロード", "incorrect_forms": ["フロード"]}],
        "cases": [
            {
                "case_id": "case-01",
                "gold_transcript": {"text": "クロードです"},
                "baseline_vtt": str(baseline),
                "candidate_output_json": str(candidate),
                "target_range": {"start_ms": 1000, "end_ms": 2000},
                "gold_cue_anchors": [{"anchor_id": "a1", "start_ms": 1000, "end_ms": 2000}],
                "run_kind": "warm",
            }
        ],
    }


def test_parse_timestamp_ms_supports_minute_and_hour_forms() -> None:
    assert parse_timestamp_ms("00:01.002") == 1002
    assert parse_timestamp_ms("01:02:03.004") == 3_723_004
    assert parse_timestamp_ms("00:00:01,5") == 1500


def test_parse_timestamp_ms_rejects_invalid_components() -> None:
    with pytest.raises(ValueError):
        parse_timestamp_ms("00:60.000")
    with pytest.raises(ValueError):
        parse_timestamp_ms("1.000")


def test_parse_vtt_removes_html_and_vtt_tags_but_keeps_duplicate_and_short_cues() -> None:
    content = """WEBVTT

1
00:00:00.000 --> 00:00:00.001
<c.yellow>Ａ</c> <00:00:00.000> &amp; <b>B</b>

2
00:00:00.001 --> 00:00:01.001 position:10%
<v Speaker>Ａ</v>

"""
    cues = parse_vtt(content)
    assert len(cues) == 2
    assert cues[0].end_ms == 1
    assert cues[0].text == "Ａ &amp; B"
    assert cues[1].settings == "position:10%"


def test_normalize_ja_text_nfkc_removes_whitespace_and_preserves_punctuation_by_default() -> None:
    assert normalize_ja_text(" ＡＢＣ　です。\n") == "ABCです。"


def test_normalize_ja_text_can_ignore_only_explicit_punctuation() -> None:
    config = NormalizationConfig(ignore_punctuation=True)
    assert normalize_ja_text("Ａ、Ｂ。ー", config) == "ABー"
    assert normalize_ja_text("Ａ、Ｂ。ー") == "A、B。ー"


def test_levenshtein_is_unicode_codepoint_based() -> None:
    assert levenshtein_distance("かな", "か") == 1
    assert levenshtein_distance("", "漢") == 1


def test_cer_handles_nfkc_and_zero_gold_deterministically() -> None:
    assert character_error_rate("Ａ Ｂ", "AB") == 0.0
    assert character_error_rate("", "") == 0.0
    assert character_error_rate("", "a") == 1.0


def test_parse_peak_rss_accepts_macos_time_l_format() -> None:
    assert _parse_peak_rss("           907018240  maximum resident set size") == 907018240


def test_glossary_uses_fixed_entries_and_reports_found_missing_incorrect() -> None:
    result = glossary_exact_match(
        "クロードとCodex",
        "フロードとCodex",
        [{"term": "クロード", "incorrect_forms": ["フロード"]}, "Codex"],
    )
    assert result["found"] == 1
    assert result["missing"] == 1
    assert result["incorrect"] == 1
    assert result["expected"] == 2


def test_cue_inclusion_uses_half_open_overlap_at_boundaries() -> None:
    cues = [
        VttCue(0, 1000, "left"),
        VttCue(1000, 1001, "short"),
        VttCue(2000, 3000, "right"),
    ]
    assert [cue.text for cue in select_cues_by_overlap(cues, TimeRange(1000, 2000))] == ["short"]


def test_cue_inclusion_counts_duplicate_and_missing_deterministically() -> None:
    anchors = [{"anchor_id": "a1", "start_ms": 1000, "end_ms": 1500}, {"anchor_id": "a2", "start_ms": 1500, "end_ms": 2000}]
    output = [VttCue(1000, 1500, "one"), VttCue(1000, 1500, "duplicate")]
    result = cue_inclusion_metrics(output, anchors, target=TimeRange(1000, 2000))
    assert result["missing"] == 1
    assert result["duplicate"] == 1
    assert result["error_rate"] == 1.0


def test_cue_inclusion_chooses_maximum_overlap_then_earliest_start() -> None:
    anchors = [
        {"anchor_id": "short-overlap", "start_ms": 1000, "end_ms": 1700},
        {"anchor_id": "long-overlap", "start_ms": 1500, "end_ms": 2500},
    ]
    result = cue_inclusion_metrics(
        [VttCue(1600, 2400, "one cue")],
        anchors,
        target=TimeRange(1000, 2500),
    )
    assert result["assigned_anchor_ids"] == ["long-overlap"]
    assert result["missing_anchor_ids"] == ["short-overlap"]
    assert result["duplicate"] == 0


def test_ten_ms_near_boundary_duplicate_is_deduped_but_short_standalone_cue_is_kept() -> None:
    from benchmarks.s9_benchmark import deduplicate_progressive_timed

    cues = [
        VttCue(1000, 2000, "同じ本文"),
        VttCue(1995, 2005, "同じ本文"),
        VttCue(3000, 3005, "短いが別の本文"),
    ]
    deduped = deduplicate_progressive_timed(cues)
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in deduped] == [
        (1000, 2000, "同じ本文"),
        (3000, 3005, "短いが別の本文"),
    ]


def test_timed_progressive_dedupe_text_matches_production_semantics() -> None:
    from benchmarks.s9_benchmark import deduplicate_progressive_timed

    starts_texts = [
        (1000, "僕の個人的"),
        (2000, "僕の個人的な話"),  # previous text is a prefix
        (3000, "僕の個人的な話"),  # exact duplicate
        (4000, "これは僕の個人的な話"),  # previous text is a suffix
        (5000, "前置きこれは僕の個人的な話後置き"),  # previous text is contained
        (6000, "単独の短い cue"),
        (120000, "単独の短い cue"),  # time distance does not affect production parity
    ]
    timed = [VttCue(start, start + 10, text) for start, text in starts_texts]
    production = production_deduplicate_progressive([ProductionCue(start / 1000, text) for start, text in starts_texts])

    deduped = deduplicate_progressive_timed(timed)
    assert [cue.text for cue in deduped] == [cue.text for cue in production]
    assert [cue.text for cue in deduped] == [
        "僕の個人的",
        "な話",
        "これは",
        "前置き後置き",
        "単独の短い cue",
    ]


def test_range_selection_happens_after_full_progressive_dedupe() -> None:
    from benchmarks.s9_benchmark import deduplicate_progressive_timed, _cue_text

    cues = [
        VttCue(0, 1000, "前の文"),
        VttCue(1000, 2000, "前の文 続き"),
    ]
    deduped = deduplicate_progressive_timed(cues)
    assert _cue_text(deduped, TimeRange(1000, 2000)) == "続き"


def test_marker_exclusion_happens_after_progressive_context_is_updated() -> None:
    from benchmarks.s9_benchmark import _exclude_marker_cues, deduplicate_progressive_timed

    cues = [
        VttCue(0, 1000, "前の文"),
        VttCue(1000, 2000, "[拍手]"),
        VttCue(2000, 3000, "前の文 続き"),
    ]
    deduped = deduplicate_progressive_timed(cues)
    filtered = _exclude_marker_cues(deduped, ["[拍手]"], NormalizationConfig())
    # marker が raw cue の途中にあっても、production parity では dedupe
    # が先に prev_text を更新するため、後続 cue は full text のままになる。
    assert [cue.text for cue in filtered] == ["前の文", "前の文 続き"]


def test_adjacent_short_duplicates_keep_one_deterministic_canonical_cue() -> None:
    from benchmarks.s9_benchmark import dedupe_near_duplicate_cues

    deduped = dedupe_near_duplicate_cues(
        [VttCue(1000, 1010, "短い重複"), VttCue(1000, 1010, "短い重複")]
    )
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in deduped] == [(1000, 1010, "短い重複")]


def test_paired_median_improvement_handles_zero_baseline() -> None:
    assert paired_median_cer_relative_improvement([0.0], [0.0]) == 0.0
    assert paired_median_cer_relative_improvement([0.0], [0.1]) == -1.0
    assert paired_median_cer_relative_improvement([0.5, 0.2], [0.25, 0.1]) == 0.5


def test_gate_no_go_when_cer_or_glossary_regresses() -> None:
    cases = [
        {
            "case_id": "case",
            "baseline": {"cer": 0.2, "glossary": {"found": 2, "missing": 0, "incorrect": 0}, "cue": {"error_count": 0, "gold_anchor_count": 2}},
            "candidate": {"cer": 0.19, "glossary": {"found": 1, "missing": 1, "incorrect": 1}, "cue": {"error_count": 0, "gold_anchor_count": 2}, "wall_time_ms": 1, "peak_memory_bytes": 1},
        }
    ]
    result = evaluate_gates(cases, gate_config=GateConfig(wall_time_budget_ms=10, peak_memory_budget_bytes=10))
    assert result["go"] is False
    assert any(reason["code"] == "cer_gate_failed" for reason in result["reasons"])
    assert any(reason["code"] == "glossary_non_regression_failed" for reason in result["reasons"])


def test_gate_unaudited_gold_is_provisional_and_fail_closed() -> None:
    cases = [
        {
            "case_id": "case",
            "baseline": {"cer": 0.2, "glossary": {}, "cue": {"error_count": 0, "gold_anchor_count": 1}},
            "candidate": {"cer": 0.1, "glossary": {}, "cue": {"error_count": 0, "gold_anchor_count": 1}, "wall_time_ms": 1, "peak_memory_bytes": 1},
        }
    ]
    result = evaluate_gates(cases, gate_config=GateConfig(wall_time_budget_ms=10, peak_memory_budget_bytes=10), gold_audit_status="provisional")
    assert result["metrics_status"] == "provisional"
    assert result["go"] is False
    assert any(reason["code"] == "gold_not_audited" for reason in result["reasons"])


def test_manifest_fingerprint_is_canonical_and_ignores_path_and_mtime() -> None:
    left = {
        "b": 2,
        "path": "/tmp/a",
        "data_root": "/tmp/fixture-a",
        "nested": {"mtime_ns": 1, "sha256": "x"},
        "a": 1,
    }
    right = {
        "a": 1,
        "data_root": "/other/fixture-b",
        "nested": {"sha256": "x", "mtime_ns": 999},
        "path": "/other/b",
        "b": 2,
    }
    assert manifest_fingerprint(left) == manifest_fingerprint(right)
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_model_validator_rejects_checksum_mismatch(tmp_path: Path) -> None:
    metadata = _write_model(tmp_path)
    metadata["sha256"] = "0" * 64
    with pytest.raises(ModelValidationError):
        validate_model_metadata(metadata)


def test_protocol_normalization_preserves_audio_fingerprint_metadata(tmp_path: Path) -> None:
    protocol = {
        "schema_version": 1,
        "benchmark_id": "protocol-fixture",
        "audio_cache_root": str(tmp_path),
        "models": [{"name": "model.bin", "path": "model.bin", "sha256": "0" * 64, "bytes": 1, "distribution_url": "https://example.invalid/model"}],
        "normalization": {
            "unicode": "NFKC",
            "strip_whitespace": True,
            "exclude_text_tokens": ["[拍手]"],
        },
        "cue_rule": {"overlap": "half_open", "dedupe_window_ms": 10},
        "gates": {
            "paired_median_relative_cer_improvement": 0.1,
            "cue_error_rate_delta_points": 5,
            "cold_wall_time_seconds": 180,
            "warm_wall_time_seconds": 120,
            "peak_memory_bytes": 1000,
        },
        "whisper": {"binary": "/bin/whisper-cli", "version": "fixture", "decode": {"padding_ms": 0}},
        "cache_policy": {},
        "cases": [
            {
                "id": "case-01",
                "audio_fixture": "case.wav",
                "audio_bytes": 123,
                "audio_sha256": "a" * 64,
                "range_ms": [1000, 2000],
                "source_files": {"vtt": "fixture.vtt"},
                "gold": {"text": "gold", "cue_anchors_ms": [[1000, 2000]], "audit_status": "audited"},
            }
        ],
    }

    normalized = _normalize_protocol_manifest(protocol)
    case = normalized["cases"][0]
    assert case["audio_path"] == str(tmp_path / "case.wav")
    assert case["audio_bytes"] == 123
    assert case["audio_sha256"] == "a" * 64
    assert normalized["cue_inclusion_rule"]["exclude_text_tokens"] == ["[拍手]"]
    assert normalized["evaluation"]["wall_time_budget_ms_by_run_kind"] == {"cold": 180000, "warm": 120000}
    assert normalized["whisper"]["timeout_sec"] == 180.0


def test_audio_input_fingerprint_checks_manifest_checksum_and_size(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    binary = tmp_path / "whisper-cli"
    binary.write_text("fixture", encoding="utf-8")
    audio = tmp_path / "span.wav"
    audio.write_bytes(b"audio")
    manifest = _manifest(tmp_path)
    manifest["model"] = model
    manifest["whisper"]["binary_path"] = str(binary)
    manifest["cases"][0]["audio_path"] = str(audio)
    manifest["cases"][0]["audio_bytes"] = audio.stat().st_size
    manifest["cases"][0]["audio_sha256"] = sha256_file(audio)

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        prefix = Path(argv[argv.index("--output-file") + 1])
        Path(str(prefix) + ".json").write_text(
            json.dumps({"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": "クロードです"}], "schema": "whisper.cpp-json-v1"}, ensure_ascii=False),
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    report = generate_report(manifest, tmp_path / "report", execute_whisper=True, use_time_l=False, subprocess_run=fake_run)
    audio_input = next(item for item in report["fingerprints"]["inputs"] if item["kind"] == "audio")
    assert audio_input["bytes"] == audio.stat().st_size
    assert audio_input["sha256"] == sha256_file(audio)

    manifest["cases"][0]["audio_sha256"] = "0" * 64
    with pytest.raises(FingerprintError):
        generate_report(manifest, tmp_path / "report-mismatch", execute_whisper=True, use_time_l=False, subprocess_run=fake_run)


def test_build_argv_is_fixed_for_ja_prompt_padding_decode_and_json() -> None:
    argv = build_whisper_argv(
        binary_path="/bin/whisper-cli",
        model_path="/models/model.bin",
        audio_path="/tmp/span.wav",
        output_json_path="/tmp/out.json",
        settings={"language": "ja", "initial_prompt": "クロード", "padding_ms": 500, "decode": {"temperature": 0.0, "beam_size": 5}, "output_schema": "whisper-cli-json-full-v1"},
        target_range=TimeRange(1000, 3000),
    )
    assert argv[0] == "/bin/whisper-cli"
    assert "--language" in argv and argv[argv.index("--language") + 1] == "ja"
    assert "--prompt" in argv and argv[argv.index("--prompt") + 1] == "クロード"
    assert "--output-json" in argv
    assert "--output-json-full" in argv
    assert "--no-vad" not in argv
    assert "--duration" in argv and argv[argv.index("--duration") + 1] == "3000"
    assert "-p" in argv and argv[argv.index("-p") + 1] == "1"
    assert "--beam-size" in argv


def test_runner_adds_audio_fixture_absolute_start_to_whisper_cues(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    binary = tmp_path / "whisper-cli"
    binary.write_text("fixture", encoding="utf-8")
    audio = tmp_path / "span.wav"
    audio.write_bytes(b"audio")

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        prefix = Path(argv[argv.index("--output-file") + 1])
        Path(str(prefix) + ".json").write_text(
            json.dumps(
                {
                    "schema": "whisper.cpp-json-v1",
                    "transcription": [
                        {"offsets": {"from": 0, "to": 100}, "text": "音声"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    result = run_whisper_cli(
        runtime={"binary_path": str(binary), "version": "1.9.1", "build": "fixture"},
        model=model,
        audio_path=audio,
        output_json_path=tmp_path / "out.json",
        target_range=TimeRange(5000, 6000),
        absolute_start_ms=5000,
        use_time_l=False,
        subprocess_run=fake_run,
    )
    assert result.status == "ok"
    assert [(cue.start_ms, cue.end_ms) for cue in result.cues] == [(5000, 5100)]


def test_runner_uses_shell_false_and_rejects_unknown_schema(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    binary = tmp_path / "whisper-cli"
    binary.write_text("fixture", encoding="utf-8")
    audio = tmp_path / "span.wav"
    audio.write_bytes(b"audio")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((argv, kwargs))
        prefix = Path(argv[argv.index("--output-file") + 1])
        Path(str(prefix) + ".json").write_text(json.dumps({"unexpected": []}), encoding="utf-8")
        return SimpleNamespace(stdout="out", stderr="err", returncode=0)

    result = run_whisper_cli(
        runtime={"binary_path": str(binary), "version": "1.9.1", "build": "fixture"},
        model=model,
        audio_path=audio,
        output_json_path=tmp_path / "out.json",
        target_range=TimeRange(0, 1000),
        use_time_l=False,
        subprocess_run=fake_run,
    )
    assert result.status == "failed"
    assert result.error and result.error["code"] == "unknown_or_invalid_schema"
    assert calls[0][1]["shell"] is False
    assert calls[0][0][calls[0][0].index("--language") + 1] == "ja"


def test_runner_timeout_is_typed_fail_closed(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    binary = tmp_path / "whisper-cli"
    binary.write_text("fixture", encoding="utf-8")
    audio = tmp_path / "span.wav"
    audio.write_bytes(b"audio")

    def timeout_run(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="whisper-cli", timeout=1)

    result = run_whisper_cli(
        runtime={"binary_path": str(binary), "version": "1.9.1", "build": "fixture"},
        model=model,
        audio_path=audio,
        output_json_path=tmp_path / "out.json",
        target_range=TimeRange(0, 1000),
        use_time_l=False,
        subprocess_run=timeout_run,
    )
    assert result.status == "failed"
    assert result.error and result.error["code"] == "runner_failed"


def test_parse_whisper_json_rejects_unknown_root_schema() -> None:
    with pytest.raises(SchemaError):
        parse_whisper_json({"unexpected": [], "transcription": []})


def test_parse_whisper_json_accepts_full_metadata_root_and_known_segment_metadata() -> None:
    payload = {
        "systeminfo": "fixture",
        "model": {"type": "large-v3-turbo", "multilingual": True},
        "params": {"model": "model.bin", "language": "ja", "translate": False},
        "result": {"language": "ja"},
        "transcription": [
            {
                "timestamps": {"from": "00:00:00,000", "to": "00:00:01,000"},
                "offsets": {"from": 0, "to": 1000},
                "text": "音声",
                "tokens": [
                    {
                        "text": "音声",
                        "timestamps": {"from": "00:00:00,000", "to": "00:00:01,000"},
                        "offsets": {"from": 0, "to": 1000},
                        "id": 1,
                        "p": 0.9,
                        "t_dtw": -1,
                    }
                ],
                "speaker": "0",
                "speaker_turn_next": False,
            }
        ],
    }
    cues = parse_whisper_json(payload, expected_schema="whisper-cli-json-full-v1")
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [(0, 1000, "音声")]

    with pytest.raises(SchemaError):
        parse_whisper_json(
            {
                "transcription": [
                    {"offsets": {"from": 0, "to": 1000}, "text": "音声"},
                ],
            },
            expected_schema="whisper-cli-json-full-v1",
        )

    with pytest.raises(SchemaError):
        parse_whisper_json(
            {
                "model": {},
                "transcription": [
                    {"offsets": {"from": 0, "to": 1000}, "text": "音声", "unknown_segment_field": True}
                ],
            }
        )


def test_candidate_cues_are_not_progressively_deduped_and_count_as_duplicates(tmp_path: Path) -> None:
    report = generate_report(
        _manifest(
            tmp_path,
            candidate_payload={
                "schema": "s9-1-candidate-v1",
                "cues": [
                    {"start_ms": 1000, "end_ms": 1500, "text": "クロード"},
                    {"start_ms": 1500, "end_ms": 2000, "text": "クロードです"},
                    {"start_ms": 1000, "end_ms": 2000, "text": "クロード"},
                ],
                "metrics": {"wall_time_ms": 50, "peak_memory_bytes": 100},
            },
        ),
        tmp_path / "report",
    )
    candidate = report["cases"][0]["candidate"]
    assert candidate["normalized_hypothesis"] == "クロードクロードですクロード"
    assert candidate["cue"]["duplicate"] == 2


def test_generate_report_records_fingerprints_metrics_and_go(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    report = generate_report(manifest, tmp_path / "report")
    assert report["schema"] == "s9-1-benchmark-report-v1"
    assert report["gates"]["go"] is True
    assert report["metrics_status"] == "audited"
    assert report["normalization"]["unicode_form"] == "NFKC"
    assert report["cue_inclusion_rule"]["kind"] == "overlap_half_open"
    assert report["cases"][0]["candidate"]["run_kind"] == "warm"
    assert report["fingerprints"]["model"]["sha256"] == sha256_file(manifest["model"]["path"])
    assert Path(report["report_path"]).is_file()


def test_generate_report_unaudited_gold_is_provisional_no_go(tmp_path: Path) -> None:
    report = generate_report(_manifest(tmp_path, audit="provisional"), tmp_path / "report")
    assert report["metrics_status"] == "provisional"
    assert report["gates"]["go"] is False
    assert any(reason["code"] == "gold_not_audited" for reason in report["gates"]["reasons"])


def test_execute_defaults_warm_cache_hit_to_true_and_records_execution(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    binary = tmp_path / "whisper-cli"
    binary.write_text("fixture", encoding="utf-8")
    audio = tmp_path / "span.wav"
    audio.write_bytes(b"audio")
    manifest["whisper"]["binary_path"] = str(binary)
    manifest["cases"][0]["audio_path"] = str(audio)
    manifest["cases"][0].pop("cache_hit", None)

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        prefix = Path(argv[argv.index("--output-file") + 1])
        Path(str(prefix) + ".json").write_text(
            json.dumps(
                {
                    "transcription": [
                        {"offsets": {"from": 0, "to": 1000}, "text": "クロードです"},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="stdout", stderr="", returncode=0)

    report = generate_report(
        manifest,
        tmp_path / "report",
        execute_whisper=True,
        run_kind="warm",
        use_time_l=False,
        subprocess_run=fake_run,
    )
    candidate = report["cases"][0]["candidate"]
    assert candidate["cache_hit"] is True
    assert candidate["execution"]["stdout"] == "stdout"
    assert candidate["execution"]["output_paths"]
