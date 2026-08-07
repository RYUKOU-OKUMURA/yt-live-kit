from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import wave

import pytest

from benchmarks.t1.annotation_packet import (
    ALIGNMENT_LONG_TUPLE_IDS,
    ALIGNMENT_MULTI_TUPLE_IDS,
    AnnotationError,
    PRODUCTION_AFTER_ARTIFACT,
    KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS,
    KNOWN_MULTI_LOW_CONFIDENCE_ROW_IDS,
    MANUAL_SPLIT_BOUNDARIES,
    PRODUCTION_DATA_ROOT,
    create_packet,
    load_manifest,
    manifest_fingerprint,
    _player_command,
    _select_player,
    _slice_source_span,
    _parser,
    main,
    validate_manifest,
    validate_packet,
    write_source_span_wav,
    write_span_wav,
    _expected_pre_measurement_result,
    validate_result,
    _validate_production_integrity_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmarks" / "t1" / "manifest.json"
RESULT_PATH = ROOT / "docs" / "benchmarks" / "t1-1-result.json"

# production データ（data/ 配下、gitignore 対象）が存在しない環境（CI 等）では、
# manifest / result の check_sources=True 検証が production ファイルの不在で必ず失敗する。
# その環境では実行不能なテストとして明示的に skip する。
_requires_production_data = pytest.mark.skipif(
    not Path(PRODUCTION_DATA_ROOT).is_dir(),
    reason="production データ（gitignore 対象の data/ 配下）が無い環境では実行できません。",
)


def _span_duration(span: dict) -> int:
    return span["end_ms"] - span["start_ms"] if "end_ms" in span else span["duration_ms"]


def _receipt(manifest: dict, row: dict, playback_root: Path) -> dict:
    from_ms = 0
    duration = min(1000, _span_duration(row["source_span"]))
    playback_root.mkdir(parents=True, exist_ok=True)
    playback_path = playback_root / f"{row['row_id']}.wav"
    with wave.open(str(playback_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * (duration * 16))
    playback_bytes = playback_path.stat().st_size
    playback_sha256 = sha256(playback_path.read_bytes()).hexdigest()
    return {
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "row_id": row["row_id"],
        "audio_source_id": row["audio_source_id"],
        "source_content_sha256": next(
            source["source_content_sha256"]
            for source in manifest["sources"].values()
            if source["source_id"] == row["audio_source_id"]
        ),
        "target_text": row["target_text"],
        "row_source_span": copy.deepcopy(row["source_span"]),
        "source_span": _slice_source_span(
            row["source_span"], from_ms=from_ms, duration_ms=duration
        ),
        "played_from_ms": from_ms,
        "played_duration_ms": duration,
        "playback_wav_path": str(playback_path),
        "playback_wav_sha256": playback_sha256,
        "playback_wav_bytes": playback_bytes,
        "playback_format": {"channels": 1, "sample_width": 2, "sample_rate": 16000},
        "recorded_at": "2026-08-04T12:00:00+00:00",
    }


def test_t1_manifest_is_frozen_with_v3_limits_and_groups() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    result = validate_manifest(manifest)

    assert result["row_count"] == 64
    assert result["group_counts"] == {
        "long_single_cue": 20,
        "multi_cross_cue": 24,
        "vtt_fallback_concat": 20,
    }
    assert manifest["limits"] == {
        "max_selected_spans": 8,
        "selected_span_count": 8,
        "audio_context_span_count": 15,
        "max_whisper_invocations": 8,
        "whisper_invocation_count": 0,
    }
    assert manifest["runtime"]["ffmpeg"] == {
        **manifest["runtime"]["ffmpeg"],
        "path": "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "configured_version": "8.1.2",
        "preflight_status": "pass",
    }


def test_manifest_fixes_the_audited_artifact_allocation_without_overlap() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    by_group = {
        group: {row["source_telop_line_tuple_id"] for row in manifest["rows"] if row["fixture_group"] == group}
        for group in ("long_single_cue", "multi_cross_cue")
    }
    holdouts = {
        row["source_telop_line_tuple_id"]
        for row in manifest["rows"]
        if row.get("artifact_cross_cue_holdout_context")
    }
    assert by_group["long_single_cue"] == ALIGNMENT_LONG_TUPLE_IDS
    assert by_group["multi_cross_cue"] == ALIGNMENT_MULTI_TUPLE_IDS | KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS
    assert holdouts == KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS
    assert not by_group["long_single_cue"] & by_group["multi_cross_cue"]
    assert not by_group["long_single_cue"] & holdouts
    assert by_group["multi_cross_cue"] & holdouts == holdouts


def test_manifest_manual_splits_use_fixed_meaning_boundaries() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    manual = [row for row in manifest["rows"] if row["manual_pre_measurement_fixture"]]
    assert len(manual) == 2
    for original, prefix in MANUAL_SPLIT_BOUNDARIES.items():
        rows = [row for row in manual if row["manual_split"]["original_text"] == original]
        assert sorted(row["target_text"] for row in rows) == sorted([prefix, "止まってないね"])
        delimiter = rows[0]["manual_split"]["delimiter_text"]
        assert prefix + delimiter + "止まってないね" == original
        assert {row["manual_split"]["subtarget"] for row in rows} == {"a", "b"}
    assert all(row["manual_split"]["rule"] == "fixed_meaning_boundary_non_candidate" for row in manual)


def test_manifest_manual_split_rejects_known_bad_midpoint() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    row = next(row for row in manifest["rows"] if row["manual_pre_measurement_fixture"])
    broken = copy.deepcopy(row)
    broken["target_text"] = broken["target_text"][:-1]
    with pytest.raises(AnnotationError, match="target_text"):
        from benchmarks.t1.annotation_packet import _validate_manual_split

        _validate_manual_split(broken)


def test_manifest_requires_fixed_low_confidence_rows_and_manual_source_population() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    broken = copy.deepcopy(manifest)
    low = next(row for row in broken["rows"] if row["row_id"] == "t1-multi-021")
    low["artifact_cross_cue_holdout_context"]["expected_low_confidence"] = False
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    with pytest.raises(AnnotationError, match="low-confidence"):
        validate_manifest(broken)

    broken = copy.deepcopy(manifest)
    saved = next(row for row in broken["rows"] if row["row_id"] == "t1-fallback-001")
    saved["source_telop_line_tuple_id"] = "lb4_e1ff:s4:l2:1113149-1116110"
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    with pytest.raises(AnnotationError, match="元tuple"):
        validate_manifest(broken)


@_requires_production_data
def test_manifest_sources_and_provenance_are_checked_read_only() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    result = validate_manifest(manifest, check_sources=True)

    assert result["source_hashes_checked"] is True
    assert manifest["timing_inputs"]["selected_span_count"] == 8
    assert manifest["timing_inputs"]["whisper_invocation_count"] == 0
    assert all(
        cache["requested_duration_ms"] != cache["actual_audio_duration_ms"]
        and "audio_duration_ms" not in cache
        for cache in manifest["rejected_legacy_audio_caches"]
    )


def test_v37_b5d_mapping_evidence_and_revision_are_fail_closed() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest["manifest_revision"] == "corrected_pre_measurement_freeze_v3_7"
    assert manifest["vtt_fallback_evidence"]["canonical_clip_id"] == "b5d345c4379e"
    assert manifest["production_integrity_contract"]["bound_benchmark_evidence"]["count"] == 4

    broken = copy.deepcopy(manifest)
    ass_row = next(row for row in broken["rows"] if row.get("ass_dialogue_context", {}).get("event_index") == 2)
    ass_row["ass_dialogue_context"]["source_absolute_start_ms"] += 1
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    with pytest.raises(AnnotationError, match="ASS event / VTT dialogue mapping"):
        validate_manifest(broken)

    broken = copy.deepcopy(manifest)
    broken["vtt_fallback_evidence"]["canonical_clip_id"] = "0" * 12
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    with pytest.raises(AnnotationError, match="固定ASS/VTT契約"):
        validate_manifest(broken)

    broken = copy.deepcopy(manifest)
    broken["production_integrity_contract"]["bound_benchmark_evidence"]["files"][0]["bytes"] += 1
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    with pytest.raises(AnnotationError, match="benchmark evidence"):
        validate_manifest(broken)

    broken = copy.deepcopy(manifest)
    broken["manifest_revision"] = "corrected_pre_measurement_freeze_v9"
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    with pytest.raises(AnnotationError, match="manifest_revision"):
        validate_manifest(broken)


def test_fallback_rows_use_adjacent_noncontiguous_cut_pairs_and_keep_all_20_in_denominator() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    sources = {source["source_id"]: source for source in manifest["sources"].values()}
    rows = [row for row in manifest["rows"] if row["fixture_group"] == "vtt_fallback_concat"]

    assert len(rows) == 20
    for row in rows:
        draft = row["draft_reference"]
        if row.get("ass_dialogue_context"):
            assert row["fallback_non_regression_required"] is True
            assert row["coverage_excluded"] is True
            assert len(row["source_span"]["parts"]) == 3
            assert row["ass_dialogue_context"]["event_index"] in {2, 12, 35, 41}
            continue
        context = row["fallback_concat_context"]
        assert len(context["source_part_source_ids"]) == 2
        first = sources[context["source_part_source_ids"][0]]["audio_span_origin_ms"]
        second = sources[context["source_part_source_ids"][1]]["audio_span_origin_ms"]
        assert context["gap_ms"] == second["start_ms"] - first["end_ms"] > 0
        assert row["source_span"]["parts"] == [
            {"start_ms": first["start_ms"], "end_ms": first["end_ms"], "concat_offset_ms": 0},
            {"start_ms": second["start_ms"], "end_ms": second["end_ms"], "concat_offset_ms": first["end_ms"] - first["start_ms"]},
        ]
        assert row["source_span"]["duration_ms"] == (first["end_ms"] - first["start_ms"]) + (second["end_ms"] - second["start_ms"])
        target_origin = sources[row["audio_source_id"]]["audio_span_origin_ms"]
        assert target_origin["start_ms"] <= draft["telop_line_start_ms"] < draft["telop_line_end_ms"] <= target_origin["end_ms"]
        assert context["target_relative_span_ms"] == {
            "start_ms": draft["telop_line_start_ms"] - target_origin["start_ms"],
            "end_ms": draft["telop_line_end_ms"] - target_origin["start_ms"],
        }
        assert row["fallback_non_regression_required"] is True
        if row["row_id"] == "t1-fallback-001":
            clipped = _slice_source_span(row["source_span"], from_ms=16000, duration_ms=5000)
            assert clipped["kind"] == "concatenated_source_video_audio"
            assert len(clipped["parts"]) == 2
            assert clipped["parts"] == [
                {"start_ms": 873000, "end_ms": 875000, "concat_offset_ms": 0},
                {"start_ms": 996000, "end_ms": 999000, "concat_offset_ms": 2000},
            ]
            assert clipped["duration_ms"] == 5000
        if row["manual_pre_measurement_fixture"]:
            evaluation = row["manual_split_evaluation"]
            assert evaluation["scenario_id"] == row["row_id"]
            assert evaluation["scenario_emits_only_this_subtarget"] is True
            assert evaluation["sibling_subtargets_coemitted"] is False
            assert evaluation["included_in_vtt_non_regression_denominator"] is True
            assert evaluation["baseline_is_gold"] is False
            assert evaluation["baseline_draft_time_ms"] == {
                "start_ms": draft["telop_line_start_ms"],
                "end_ms": draft["telop_line_end_ms"],
            }


def test_b5d_three_part_playback_window_preserves_all_part_offsets() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    row = next(row for row in manifest["rows"] if row.get("ass_dialogue_context", {}).get("event_index") == 12)
    clipped = _slice_source_span(row["source_span"], from_ms=20000, duration_ms=40000)
    assert clipped == {
        "kind": "concatenated_source_video_audio",
        "coordinate_system": "absolute_video_ms",
        "parts": [
            {"start_ms": 3720000, "end_ms": 3721000, "concat_offset_ms": 0},
            {"start_ms": 4015000, "end_ms": 4052000, "concat_offset_ms": 1000},
            {"start_ms": 4086000, "end_ms": 4088000, "concat_offset_ms": 38000},
        ],
        "duration_ms": 40000,
    }


def test_play_duration_is_full_remaining_context_when_omitted() -> None:
    args = _parser().parse_args(
        [
            "play",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            "/tmp/t1-packet.json",
            "--from-ms",
            "1250",
        ]
    )
    assert args.duration_ms is None
    manifest = load_manifest(MANIFEST_PATH)
    row = manifest["rows"][0]
    assert _span_duration(row["source_span"]) - args.from_ms > 0


def test_player_selection_prefers_configured_ffplay_and_shows_position(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffplay = tmp_path / "ffplay"
    ffmpeg.write_bytes(b"ffmpeg")
    ffplay.write_bytes(b"ffplay")
    ffplay.chmod(0o755)
    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet.shutil.which",
        lambda name: "/usr/bin/afplay" if name == "afplay" else None,
    )

    player, kind = _select_player(ffmpeg)
    command = _player_command(player, kind, tmp_path / "window.wav")

    assert player == str(ffplay)
    assert kind == "ffplay"
    assert command[1:7] == ["-nodisp", "-autoexit", "-stats", "-hide_banner", "-loglevel", "error"]


def test_player_selection_uses_ffplay_before_afplay_and_has_afplay_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet.shutil.which",
        lambda name: {"ffplay": "/usr/bin/ffplay", "afplay": "/usr/bin/afplay"}.get(name),
    )
    player, kind = _select_player(Path("/missing/ffmpeg"))
    assert (player, kind) == ("/usr/bin/ffplay", "ffplay")

    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet.shutil.which",
        lambda name: "/usr/bin/afplay" if name == "afplay" else None,
    )
    player, kind = _select_player(Path("/missing/ffmpeg"))
    assert (player, kind) == ("/usr/bin/afplay", "afplay")
    assert _player_command(player, kind, Path("window.wav")) == ["/usr/bin/afplay", "window.wav"]


def test_gold_row_end_is_exclusive() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    row = packet["rows"][0]
    row["gold"] = {
        "line_onset_ms": _span_duration(row["source_span"]),
        "timebase": "source_audio_relative_ms",
        "annotator_id": "human-reviewer",
        "annotated_at": "2026-08-04T21:00:00+09:00",
        "audio_listened": True,
    }
    with pytest.raises(AnnotationError, match="gold"):
        validate_packet(packet, manifest, require_complete=True)


def test_gold_partial_values_are_not_waiting_placeholders() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    packet["rows"][0]["gold"]["line_onset_ms"] = 1
    with pytest.raises(AnnotationError, match="placeholder"):
        validate_packet(packet, manifest)

    packet = create_packet(manifest)
    packet["rows"][0]["gold"]["annotator_id"] = "human-reviewer"
    with pytest.raises(AnnotationError, match="placeholder"):
        validate_packet(packet, manifest)


def test_annotate_invalid_gold_does_not_write_packet(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    row = manifest["rows"][0]
    duration = _span_duration(row["source_span"])
    cases = [
        {"name": "row-end", "onset": duration},
        {"name": "bad-annotator", "onset": 1, "annotator": "human\nreviewer"},
        {"name": "bad-timestamp", "onset": 1, "annotated_at": "not-a-timestamp"},
        {"name": "outside-playback-window", "onset": 1500},
    ]

    for case in cases:
        packet = create_packet(manifest)
        packet_path = tmp_path / f"{case['name']}.json"
        packet["playback_receipts"] = {
            row["row_id"]: _receipt(
                manifest,
                row,
                packet_path.parent / f".{packet_path.name}.playback",
            )
        }
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        before = packet_path.read_bytes()
        argv = [
            "annotate",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_path),
            "--row-id",
            row["row_id"],
            "--onset-ms",
            str(case["onset"]),
            "--annotator",
            case.get("annotator", "human-reviewer"),
            "--audio-listened",
        ]
        if "annotated_at" in case:
            argv.extend(["--annotated-at", case["annotated_at"]])

        assert main(argv) == 2
        assert packet_path.read_bytes() == before


def test_play_invalid_receipt_does_not_write_packet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet._select_player",
        lambda ffmpeg_path: ("/usr/bin/afplay", "afplay"),
    )
    monkeypatch.setattr("benchmarks.t1.annotation_packet.subprocess.run", lambda *args, **kwargs: None)

    invalid_infos = [
        {
            "frames": 16000,
            "bytes": 10,
            "sha256": "a" * 64,
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 16000,
        },
        {
            "frames": 16000,
            "bytes": 100,
            "sha256": "z" * 64,
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 16000,
        },
        {
            "frames": 16000,
            "bytes": 100,
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 16000,
        },
    ]
    for index, invalid_info in enumerate(invalid_infos):
        packet = create_packet(manifest)
        row = packet["rows"][0]
        packet_path = tmp_path / f"play-invalid-receipt-{index}.json"
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        before = packet_path.read_bytes()
        monkeypatch.setattr(
            "benchmarks.t1.annotation_packet.write_source_span_wav",
            lambda *args, _info=invalid_info, **kwargs: _info,
        )

        assert main(
            [
                "play",
                "--manifest",
                str(MANIFEST_PATH),
                "--packet",
                str(packet_path),
                "--row-id",
                row["row_id"],
                "--from-ms",
                "0",
                "--duration-ms",
                "1000",
            ]
        ) == 2
        assert packet_path.read_bytes() == before


@_requires_production_data
def test_play_replay_never_overwrites_existing_receipt_wav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    row = manifest["rows"][0]
    packet = create_packet(manifest)
    packet_path = tmp_path / "existing-packet.json"
    old_playback_root = packet_path.parent / f".{packet_path.name}.playback"
    old_receipt = _receipt(manifest, row, old_playback_root)
    packet["playback_receipts"] = {row["row_id"]: old_receipt}
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before_packet = packet_path.read_bytes()
    old_wav_path = Path(old_receipt["playback_wav_path"])
    before_old_wav = old_wav_path.read_bytes()
    validate_packet(json.loads(before_packet), manifest, packet_path=packet_path)

    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet._select_player",
        lambda ffmpeg_path: ("/usr/bin/afplay", "afplay"),
    )
    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet.write_source_span_wav",
        lambda *args, **kwargs: {
            "frames": 16000,
            "bytes": 10,
            "sha256": "a" * 64,
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 16000,
        },
    )
    assert main(
        [
            "play",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_path),
            "--row-id",
            row["row_id"],
            "--from-ms",
            "0",
            "--duration-ms",
            "1000",
        ]
    ) == 2
    assert packet_path.read_bytes() == before_packet
    assert old_wav_path.read_bytes() == before_old_wav
    validate_packet(json.loads(before_packet), manifest, packet_path=packet_path)

    def valid_replay(_source: dict, _span: dict, output_path: Path, **_kwargs: object) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 16000)
        payload = output_path.read_bytes()
        return {
            "frames": 16000,
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 16000,
        }

    monkeypatch.setattr("benchmarks.t1.annotation_packet.write_source_span_wav", valid_replay)
    monkeypatch.setattr("benchmarks.t1.annotation_packet.subprocess.run", lambda *args, **kwargs: None)
    assert main(
        [
            "play",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_path),
            "--row-id",
            row["row_id"],
            "--from-ms",
            "0",
            "--duration-ms",
            "1000",
        ]
    ) == 0
    assert old_wav_path.read_bytes() == before_old_wav
    updated_packet = json.loads(packet_path.read_text(encoding="utf-8"))
    new_wav_path = Path(updated_packet["playback_receipts"][row["row_id"]]["playback_wav_path"])
    assert new_wav_path != old_wav_path
    assert new_wav_path.is_file()
    validate_packet(updated_packet, manifest, packet_path=packet_path)


@_requires_production_data
def test_result_identity_and_post_measurement_rehash_contract_are_fixed() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    validated = validate_result(result, manifest)

    assert validated["benchmark_id"] == manifest["benchmark_id"]
    assert validated["manifest_fingerprint"] == manifest["manifest_fingerprint"]
    assert validated["bound_source_count"] == 15
    assert validated["bound_telop_document_count"] == 4
    assert validated["bound_artifact_document_count"] == 3

    broken = copy.deepcopy(result)
    broken["benchmark_id"] = "t1-1-timing-spike-20260804"
    with pytest.raises(AnnotationError, match="benchmark_id"):
        validate_result(broken, manifest)

    broken = copy.deepcopy(result)
    broken["production_integrity"]["after_measurement_rehash_contract"]["benchmark_evidence_rehash"]["binding"]["files"][0]["bytes"] += 1
    with pytest.raises(AnnotationError, match="pre-measurement field"):
        validate_result(broken, manifest)


def _production_integrity_fixture(tmp_path: Path) -> tuple[dict, dict, Path, Path, Path]:
    before_path = tmp_path / "docs" / "benchmarks" / "s9-1-production-hash-before.json"
    after_path = tmp_path / PRODUCTION_AFTER_ARTIFACT
    production_root = tmp_path / "production-data"
    before_path.parent.mkdir(parents=True, exist_ok=True)
    after_path.parent.mkdir(parents=True, exist_ok=True)
    production_root.mkdir(parents=True, exist_ok=True)
    files = {}
    for index in range(15):
        relative = f"fixture-{index:02d}.bin"
        payload = f"fixture-production-file-{index}\n".encode("utf-8")
        live_path = production_root / relative
        live_path.write_bytes(payload)
        files[relative] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
        }
    root_text = str(production_root.resolve())
    before_snapshot = {
        "captured_at": "2026-08-04",
        "root": root_text,
        "files": files,
    }
    before_bytes = json.dumps(before_snapshot, ensure_ascii=False, indent=2) + "\n"
    before_path.write_text(before_bytes, encoding="utf-8")
    before_sha256 = sha256(before_bytes.encode("utf-8")).hexdigest()
    after_snapshot = {
        "schema": "t1-1-production-hash-after-v1",
        "captured_at": "2026-08-04",
        "root": root_text,
        "before_artifact": "docs/benchmarks/s9-1-production-hash-before.json",
        "before_artifact_sha256": before_sha256,
        "file_count": 15,
        "matches_before": True,
        "purpose": "test fixture",
        "files": files,
    }
    after_path.write_text(json.dumps(after_snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "production_hash_baseline": {
            "artifact_path": "docs/benchmarks/s9-1-production-hash-before.json",
            "artifact_bytes": before_path.stat().st_size,
            "artifact_sha256": before_sha256,
            "root": root_text,
            "file_count": 15,
        }
    }
    result = {
        "production_integrity": {
            "before_artifact": "docs/benchmarks/s9-1-production-hash-before.json",
            "before_artifact_bytes": before_path.stat().st_size,
            "before_artifact_sha256": before_sha256,
            "after_artifact": PRODUCTION_AFTER_ARTIFACT,
            "after_artifact_bytes": after_path.stat().st_size,
            "after_artifact_sha256": sha256(after_path.read_bytes()).hexdigest(),
        }
    }
    return manifest, result, before_path, after_path, production_root


def test_result_rehashes_before_and_after_artifacts_and_rejects_missing_or_tampered_files(tmp_path: Path) -> None:
    manifest, result, before_path, after_path, production_root = _production_integrity_fixture(tmp_path)
    validated = _validate_production_integrity_artifacts(
        result, manifest, repository_root=tmp_path, production_data_root=production_root
    )
    assert validated["file_count"] == 15
    assert validated["matches_before"] is True
    assert validated["production_root"] == str(production_root.resolve())

    after_path.unlink()
    with pytest.raises(AnnotationError, match="after artifact"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path, production_data_root=production_root
        )

    manifest, result, before_path, after_path, production_root = _production_integrity_fixture(tmp_path / "bytes")
    after_path.write_bytes(after_path.read_bytes() + b"x")
    with pytest.raises(AnnotationError, match="after artifact"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path / "bytes", production_data_root=production_root
        )

    manifest, result, before_path, after_path, production_root = _production_integrity_fixture(tmp_path / "content")
    changed = json.loads(after_path.read_text(encoding="utf-8"))
    file_key = next(iter(changed["files"]))
    changed["files"][file_key]["sha256"] = "0" * 64
    after_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["production_integrity"]["after_artifact_bytes"] = after_path.stat().st_size
    result["production_integrity"]["after_artifact_sha256"] = sha256(after_path.read_bytes()).hexdigest()
    with pytest.raises(AnnotationError, match="15-file entries"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path / "content", production_data_root=production_root
        )

    before_path.write_bytes(before_path.read_bytes() + b"x")
    with pytest.raises(AnnotationError, match="before artifact"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path / "content", production_data_root=production_root
        )


def test_result_rejects_production_root_mismatch_parent_path_and_live_hash_drift(tmp_path: Path) -> None:
    manifest, result, before_path, after_path, production_root = _production_integrity_fixture(tmp_path)
    changed = json.loads(after_path.read_text(encoding="utf-8"))
    changed["root"] = str(tmp_path / "other-root")
    after_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["production_integrity"]["after_artifact_bytes"] = after_path.stat().st_size
    result["production_integrity"]["after_artifact_sha256"] = sha256(after_path.read_bytes()).hexdigest()
    with pytest.raises(AnnotationError, match="root"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path, production_data_root=production_root
        )

    manifest, result, before_path, after_path, production_root = _production_integrity_fixture(tmp_path / "parent")
    changed = json.loads(after_path.read_text(encoding="utf-8"))
    file_key = next(iter(changed["files"]))
    entry = changed["files"].pop(file_key)
    changed["files"]["../escaped.bin"] = entry
    after_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["production_integrity"]["after_artifact_bytes"] = after_path.stat().st_size
    result["production_integrity"]["after_artifact_sha256"] = sha256(after_path.read_bytes()).hexdigest()
    with pytest.raises(AnnotationError, match="file entry"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path / "parent", production_data_root=production_root
        )

    manifest, result, before_path, after_path, production_root = _production_integrity_fixture(tmp_path / "symlink")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = production_root / "fixture-00.bin"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(AnnotationError, match="production live file"):
        _validate_production_integrity_artifacts(
            result, manifest, repository_root=tmp_path / "symlink", production_data_root=production_root
        )


@_requires_production_data
def test_validate_result_rehashes_manifest_baseline_before_exact_result_validation() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(manifest)
    tampered["production_hash_baseline"]["artifact_sha256"] = "0" * 64
    tampered["manifest_fingerprint"] = manifest_fingerprint(tampered)
    tampered_result = copy.deepcopy(result)
    tampered_result["manifest_fingerprint"] = tampered["manifest_fingerprint"]
    tampered_result["production_integrity"] = _expected_pre_measurement_result(tampered)["production_integrity"]
    with pytest.raises(AnnotationError, match="production hash baseline|production before artifact"):
        validate_result(tampered_result, tampered)


@_requires_production_data
def test_pre_measurement_result_is_exact_and_cannot_be_promoted() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    validate_result(result, manifest)

    broken = copy.deepcopy(result)
    broken["status"] = "ready_for_measurement"
    with pytest.raises(AnnotationError, match="status"):
        validate_result(broken, manifest)

    for field, replacement in (
        ("decision", "go"),
        ("go", True),
        ("no_go", False),
        ("ac_40_update_allowed", True),
        ("human_gold", {"status": "complete"}),
        ("metrics", {}),
        ("production_integrity", {}),
    ):
        tampered = copy.deepcopy(result)
        tampered[field] = replacement
        with pytest.raises(AnnotationError, match=field):
            validate_result(tampered, manifest)

    tampered_counts = copy.deepcopy(result)
    tampered_counts["fixture_counts"]["total"] = 59
    with pytest.raises(AnnotationError, match="fixture_counts"):
        validate_result(tampered_counts, manifest)

    tampered_limits = copy.deepcopy(result)
    tampered_limits["limits"]["max_whisper_invocations"] = 999
    with pytest.raises(AnnotationError, match="limits"):
        validate_result(tampered_limits, manifest)

    tampered_extra = copy.deepcopy(result)
    tampered_extra["unexpected"] = True
    with pytest.raises(AnnotationError, match="top-level"):
        validate_result(tampered_extra, manifest)


def test_packet_stays_fail_closed_until_human_gold_is_entered() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)

    result = validate_packet(packet, manifest)

    assert result["status"] == "awaiting_human_audio_annotation"
    assert result["measurement_allowed"] is False
    assert len(result["incomplete_row_ids"]) == 64
    with pytest.raises(AnnotationError):
        validate_packet(packet, manifest, require_complete=True)


def test_packet_redacts_candidate_and_production_metadata() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    row = packet["rows"][0]

    assert set(row) == {
        "row_id",
        "fixture_group",
        "audio_source_id",
        "source_span",
        "target_text",
        "gold",
        "gold_provenance",
    }
    assert not {"draft_reference", "source_hashes", "vtt_cue_ids", "source_telop_line_tuple_id"} & set(row)
    assert all(
        set(source) == {
            "source_id",
            "source_content_kind",
            "source_content_path",
            "source_content_bytes",
            "source_content_sha256",
        }
        for source in packet["sources"].values()
    )
    assert all(
        forbidden not in json.dumps(
            {"rows": packet["rows"], "sources": packet["sources"]}, ensure_ascii=False
        )
        for forbidden in ("draft_reference", "source_telop_line_tuple_id", "rejected_legacy_audio_cache")
    )

    row["draft_reference"] = {"telop_line_start_ms": 1}
    with pytest.raises(AnnotationError, match="固定allowlist"):
        validate_packet(packet, manifest)

    source_tampered = create_packet(manifest)
    next(iter(source_tampered["sources"].values()))["vtt_path"] = "/tmp/hidden.vtt"
    with pytest.raises(AnnotationError, match="固定allowlist"):
        validate_packet(source_tampered, manifest)


def test_packet_requires_exact_sources_and_rejects_source_changes() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    source_key = next(iter(packet["sources"]))
    packet["sources"][source_key]["source_content_path"] = "/tmp/other.wav"

    with pytest.raises(AnnotationError, match="sources"):
        validate_packet(packet, manifest)


def test_cli_create_and_validate_always_check_bound_source_bytes_and_hash(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    tampered = copy.deepcopy(manifest)
    source = next(iter(tampered["sources"].values()))
    source["source_content_path"] = "/tmp/t1-missing-bound-source.wav"
    tampered["manifest_fingerprint"] = manifest_fingerprint(tampered)
    tampered_path = tmp_path / "tampered-manifest.json"
    tampered_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_path = tmp_path / "packet.json"

    assert main(
        [
            "create-packet",
            "--manifest",
            str(tampered_path),
            "--output",
            str(output_path),
        ]
    ) == 2
    assert not output_path.exists()
    assert main(
        [
            "validate-manifest",
            "--manifest",
            str(tampered_path),
        ]
    ) == 2


def test_packet_rejects_duplicate_or_reordered_rows() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    packet["rows"][1] = packet["rows"][0].copy()

    with pytest.raises(AnnotationError, match="件数・順序"):
        validate_packet(packet, manifest)


def test_complete_packet_requires_receipts_and_is_measurement_ready(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    for row in packet["rows"]:
        row["gold"] = {
            "line_onset_ms": 1,
            "timebase": "source_audio_relative_ms",
            "annotator_id": "human-reviewer",
            "annotated_at": "2026-08-04T21:00:00+09:00",
            "audio_listened": True,
        }
    with pytest.raises(AnnotationError, match="playback receipt"):
        validate_packet(packet, manifest, require_complete=True)

    packet["playback_receipts"] = {
        row["row_id"]: _receipt(manifest, row, tmp_path) for row in packet["rows"]
    }
    result = validate_packet(packet, manifest, require_complete=True)

    assert result["status"] == "ready_for_measurement"
    assert result["measurement_allowed"] is True
    assert result["complete_row_count"] == 64


def test_complete_packet_rejects_fake_receipt_hash_or_bytes(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    for row in packet["rows"]:
        row["gold"] = {
            "line_onset_ms": 1,
            "timebase": "source_audio_relative_ms",
            "annotator_id": "human-reviewer",
            "annotated_at": "2026-08-04T21:00:00+09:00",
            "audio_listened": True,
        }
    packet["playback_receipts"] = {
        row["row_id"]: _receipt(manifest, row, tmp_path) for row in packet["rows"]
    }
    packet["playback_receipts"][packet["rows"][0]["row_id"]]["playback_wav_sha256"] = "z" * 64
    with pytest.raises(AnnotationError, match="WAV SHA-256"):
        validate_packet(packet, manifest, require_complete=True)

    packet["playback_receipts"][packet["rows"][0]["row_id"]] = _receipt(
        manifest, packet["rows"][0], tmp_path
    )
    packet["playback_receipts"][packet["rows"][0]["row_id"]]["playback_wav_bytes"] = 100
    with pytest.raises(AnnotationError, match="bytes 不一致"):
        validate_packet(packet, manifest, require_complete=True)


def test_gold_onset_must_be_inside_played_window_and_after_receipt(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    for row in packet["rows"]:
        row["gold"] = {
            "line_onset_ms": 1,
            "timebase": "source_audio_relative_ms",
            "annotator_id": "human-reviewer",
            "annotated_at": "2026-08-04T12:00:00+00:00",
            "audio_listened": True,
        }
    packet["playback_receipts"] = {
        row["row_id"]: _receipt(manifest, row, tmp_path) for row in packet["rows"]
    }
    packet["rows"][0]["gold"]["line_onset_ms"] = 1500
    with pytest.raises(AnnotationError, match="窓の外"):
        validate_packet(packet, manifest, require_complete=True)

    packet["rows"][0]["gold"]["line_onset_ms"] = 1
    packet["rows"][0]["gold"]["annotated_at"] = "2026-08-04T11:59:00+00:00"
    with pytest.raises(AnnotationError, match="recorded_at より前"):
        validate_packet(packet, manifest, require_complete=True)


def test_gold_extra_machine_field_is_rejected() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    packet["rows"][0]["gold"]["candidate_onset_ms"] = 3

    with pytest.raises(AnnotationError, match="gold fields"):
        validate_packet(packet, manifest)


def test_packet_rejects_unknown_receipt_and_top_level_fields() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    packet["unexpected"] = True
    with pytest.raises(AnnotationError, match="top-level"):
        validate_packet(packet, manifest)

    packet = create_packet(manifest)
    packet["playback_receipts"]["not-a-manifest-row"] = {}
    with pytest.raises(AnnotationError, match="未知の playback receipt"):
        validate_packet(packet, manifest)


def test_write_span_wav_extracts_only_requested_frames(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    output = tmp_path / "isolated" / "span.wav"
    with wave.open(str(source), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(1000)
        writer.writeframes(b"\x01\x00" * 1000)

    info = write_span_wav(
        source, {"kind": "single_source_audio", "start_ms": 100, "end_ms": 350}, output
    )

    assert info["frames"] == 250
    assert info["sha256"] == sha256(output.read_bytes()).hexdigest()


def test_write_span_wav_rejects_out_of_range_span(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(1000)
        writer.writeframes(b"\x01\x00" * 1000)

    with pytest.raises(AnnotationError, match="範囲外"):
        write_span_wav(
            source,
            {"kind": "single_source_audio", "start_ms": 900, "end_ms": 1200},
            tmp_path / "bad.wav",
        )


def test_playback_window_can_be_repeated_from_a_short_offset() -> None:
    clipped = _slice_source_span(
        {
            "kind": "concatenated_source_audio",
            "parts": [
                {"start_ms": 100, "end_ms": 400, "concat_offset_ms": 0},
                {"start_ms": 700, "end_ms": 1000, "concat_offset_ms": 300},
            ],
            "duration_ms": 600,
        },
        from_ms=250,
        duration_ms=200,
    )

    assert clipped == {
        "kind": "concatenated_source_audio",
        "parts": [
            {"start_ms": 350, "end_ms": 400, "concat_offset_ms": 0},
            {"start_ms": 700, "end_ms": 850, "concat_offset_ms": 50},
        ],
        "duration_ms": 200,
    }


def test_concat_playback_window_can_normalize_to_one_part() -> None:
    clipped = _slice_source_span(
        {
            "kind": "concatenated_source_audio",
            "parts": [
                {"start_ms": 100, "end_ms": 400, "concat_offset_ms": 0},
                {"start_ms": 700, "end_ms": 1000, "concat_offset_ms": 300},
            ],
            "duration_ms": 600,
        },
        from_ms=25,
        duration_ms=100,
    )
    assert clipped == {
        "kind": "concatenated_source_audio",
        "parts": [{"start_ms": 125, "end_ms": 225, "concat_offset_ms": 0}],
        "duration_ms": 100,
    }


def test_source_mp4_extraction_binds_hash_and_audio_stream(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fixture-mp4")
    ffmpeg_path = tmp_path / "ffmpeg"
    ffmpeg_path.write_bytes(b"fixture-ffmpeg")
    commands: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> None:
        commands.append(command)
        output = Path(command[-1])
        with wave.open(str(output), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 4000)

    monkeypatch.setattr("benchmarks.t1.annotation_packet.subprocess.run", fake_run)
    source = {
        "source_content_kind": "source_mp4",
        "source_content_path": str(source_path),
        "source_content_bytes": source_path.stat().st_size,
        "source_content_sha256": sha256(source_path.read_bytes()).hexdigest(),
    }
    output = tmp_path / "out.wav"
    info = write_source_span_wav(
        source,
        {
            "kind": "single_source_video_audio",
            "coordinate_system": "absolute_video_ms",
            "start_ms": 1000,
            "end_ms": 1250,
        },
        output,
        ffmpeg_path=ffmpeg_path,
        ffmpeg_bytes=ffmpeg_path.stat().st_size,
        ffmpeg_sha256=sha256(ffmpeg_path.read_bytes()).hexdigest(),
    )

    assert info["frames"] == 4000
    assert commands
    command = commands[0]
    assert ["-map", "0:a:0"] == command[command.index("-map") : command.index("-map") + 2]
    assert command.index("-accurate_seek") < command.index("-i")
    assert "aresample=16000" in command[command.index("-af") + 1]
    assert "atrim=end_sample=4000" in command[command.index("-af") + 1]


def test_source_mp4_extraction_concats_three_noncontiguous_parts(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fixture-mp4-three-part")
    ffmpeg_path = tmp_path / "ffmpeg"
    ffmpeg_path.write_bytes(b"fixture-ffmpeg-three-part")
    commands: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> None:
        commands.append(command)
        output = Path(command[-1])
        filter_text = command[command.index("-af") + 1]
        expected_frames = int(filter_text.split("atrim=end_sample=", 1)[1].split(",", 1)[0])
        with wave.open(str(output), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * expected_frames)

    monkeypatch.setattr("benchmarks.t1.annotation_packet.subprocess.run", fake_run)
    source = {
        "source_content_kind": "source_mp4",
        "source_content_path": str(source_path),
        "source_content_bytes": source_path.stat().st_size,
        "source_content_sha256": sha256(source_path.read_bytes()).hexdigest(),
    }
    span = {
        "kind": "concatenated_source_video_audio",
        "coordinate_system": "absolute_video_ms",
        "parts": [
            {"start_ms": 0, "end_ms": 100, "concat_offset_ms": 0},
            {"start_ms": 200, "end_ms": 350, "concat_offset_ms": 100},
            {"start_ms": 500, "end_ms": 650, "concat_offset_ms": 250},
        ],
        "duration_ms": 400,
    }
    output = tmp_path / "three-part.wav"
    info = write_source_span_wav(
        source,
        span,
        output,
        ffmpeg_path=ffmpeg_path,
        ffmpeg_bytes=ffmpeg_path.stat().st_size,
        ffmpeg_sha256=sha256(ffmpeg_path.read_bytes()).hexdigest(),
    )

    assert len(commands) == 3
    assert info["frames"] == 6400
    with wave.open(str(output), "rb") as reader:
        assert reader.getnframes() == 6400


def test_source_mp4_extraction_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fixture-mp4")
    with pytest.raises(AnnotationError, match="bytes / SHA-256"):
        write_source_span_wav(
            {
                "source_content_kind": "source_mp4",
                "source_content_path": str(source_path),
                "source_content_bytes": source_path.stat().st_size,
                "source_content_sha256": "0" * 64,
            },
            {
                "kind": "single_source_video_audio",
                "coordinate_system": "absolute_video_ms",
                "start_ms": 1000,
                "end_ms": 1250,
            },
            tmp_path / "out.wav",
            ffmpeg_path=tmp_path / "ffmpeg",
        )


def test_source_mp4_hash_is_rechecked_immediately_before_subprocess(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fixture-mp4")
    ffmpeg_path = tmp_path / "ffmpeg"
    ffmpeg_path.write_bytes(b"fixture-ffmpeg")
    labels: list[str] = []

    def fail_on_second_source_check(path: Path, expected_bytes: int, expected_sha: str, *, label: str) -> None:
        labels.append(label)
        if label == "source MP4 before subprocess":
            raise AnnotationError("source MP4 TOCTOU mismatch")

    monkeypatch.setattr("benchmarks.t1.annotation_packet._check_source_file", fail_on_second_source_check)
    with pytest.raises(AnnotationError, match="TOCTOU"):
        write_source_span_wav(
            {
                "source_content_kind": "source_mp4",
                "source_content_path": str(source_path),
                "source_content_bytes": source_path.stat().st_size,
                "source_content_sha256": sha256(source_path.read_bytes()).hexdigest(),
            },
            {
                "kind": "single_source_video_audio",
                "coordinate_system": "absolute_video_ms",
                "start_ms": 1000,
                "end_ms": 1250,
            },
            tmp_path / "out.wav",
            ffmpeg_path=ffmpeg_path,
        )
    assert labels == ["play source", "source MP4 before subprocess"]


@_requires_production_data
def test_packet_path_and_parent_symlinks_fail_closed_and_regular_path_validates(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    packet = create_packet(manifest)
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    real_packet = real_directory / "packet.json"
    real_packet.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = real_packet.read_bytes()

    packet_link = tmp_path / "packet-link.json"
    packet_link.symlink_to(real_packet)
    for command in (
        [
            "validate-packet",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_link),
        ],
        [
            "play",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_link),
            "--row-id",
            packet["rows"][0]["row_id"],
            "--duration-ms",
            "1000",
        ],
        [
            "annotate",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_link),
            "--row-id",
            packet["rows"][0]["row_id"],
            "--onset-ms",
            "1",
            "--annotator",
            "human-reviewer",
            "--audio-listened",
        ],
        [
            "create-packet",
            "--manifest",
            str(MANIFEST_PATH),
            "--output",
            str(packet_link),
            "--force",
        ],
    ):
        assert main(command) == 2
    assert packet_link.is_symlink()
    assert real_packet.read_bytes() == before

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_directory, target_is_directory=True)
    parent_packet = parent_link / "packet.json"
    assert main(
        [
            "validate-packet",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(parent_packet),
        ]
    ) == 2

    assert main(
        [
            "validate-packet",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(real_packet),
        ]
    ) == 0


@_requires_production_data
def test_cli_tempfile_create_play_noop_validate_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet_path = tmp_path / "cli-roundtrip.json"
    assert main(
        [
            "create-packet",
            "--manifest",
            str(MANIFEST_PATH),
            "--output",
            str(packet_path),
            "--check-sources",
        ]
    ) == 0

    monkeypatch.setattr(
        "benchmarks.t1.annotation_packet._select_player",
        lambda _ffmpeg_path: ("/usr/bin/afplay", "afplay"),
    )

    def valid_replay(_source: dict, _span: dict, output_path: Path, **_kwargs: object) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 16000)
        payload = output_path.read_bytes()
        return {
            "frames": 16000,
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "channels": 1,
            "sample_width": 2,
            "sample_rate": 16000,
        }

    monkeypatch.setattr("benchmarks.t1.annotation_packet.write_source_span_wav", valid_replay)
    monkeypatch.setattr("benchmarks.t1.annotation_packet.subprocess.run", lambda *args, **kwargs: None)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    row_id = packet["rows"][0]["row_id"]
    assert main(
        [
            "play",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_path),
            "--row-id",
            row_id,
            "--from-ms",
            "0",
            "--duration-ms",
            "1000",
        ]
    ) == 0
    assert main(
        [
            "validate-packet",
            "--manifest",
            str(MANIFEST_PATH),
            "--packet",
            str(packet_path),
            "--check-sources",
        ]
    ) == 0
