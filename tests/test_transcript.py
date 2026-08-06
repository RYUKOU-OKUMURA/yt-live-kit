"""トランスクリプト生成のユニットテスト."""

import json
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.transcript import (
    TranscriptArtifact,
    TranscriptArtifactStatus,
    TranscriptCue,
    TranscriptRange,
)
from yt_live_kit.services.transcript import TranscriptError, build_transcripts
from yt_live_kit.services.transcript_artifact import (
    TranscriptArtifactError,
    TranscriptArtifactStore,
    TranscriptCacheError,
    absolute_cue_digest,
    build_transcript_artifact,
    make_cache_identity,
    parse_vtt_cues,
    resolve_selected_range,
    used_range_cue_digest,
    used_range_invalidated,
)

SAMPLE_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
テスト字幕
"""


def test_build_transcripts_writes_atomically(tmp_path):
    video_id = "testvideo01"
    video_dir = tmp_path / video_id
    subtitles_dir = video_dir / "subtitles"
    subtitles_dir.mkdir(parents=True)
    (subtitles_dir / "ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")

    settings = Settings(data_dir=tmp_path)
    full_path, compressed_path = build_transcripts(video_id, settings)

    assert full_path.is_file()
    assert compressed_path.is_file()
    assert "テスト字幕" in full_path.read_text(encoding="utf-8")
    assert list((video_dir / "transcript").glob(".*.tmp")) == []


def test_build_transcripts_raises_on_empty_vtt(tmp_path):
    """有効キュー 0 件の VTT では空ファイルを保存せず TranscriptError を投げる."""
    video_id = "testvideo01"
    video_dir = tmp_path / video_id
    subtitles_dir = video_dir / "subtitles"
    subtitles_dir.mkdir(parents=True)
    (subtitles_dir / "ja.vtt").write_text("WEBVTT\n\n", encoding="utf-8")

    settings = Settings(data_dir=tmp_path)

    with pytest.raises(TranscriptError, match="有効なテキストがありません"):
        build_transcripts(video_id, settings)

    transcript_dir = video_dir / "transcript"
    assert not (transcript_dir / "full.txt").exists()
    assert not (transcript_dir / "compressed.txt").exists()


def test_build_transcripts_keeps_reading_canonical_vtt_with_source_artifacts(
    tmp_path,
):
    video_id = "testvideo01"
    video_dir = tmp_path / video_id
    subtitles_dir = video_dir / "subtitles"
    sources_dir = subtitles_dir / "sources"
    sources_dir.mkdir(parents=True)
    (subtitles_dir / "ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")
    (sources_dir / "new-source.vtt").write_text(
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n別の source\n",
        encoding="utf-8",
    )

    full_path, _ = build_transcripts(video_id, Settings(data_dir=tmp_path))

    assert "テスト字幕" in full_path.read_text(encoding="utf-8")
    assert "別の source" not in full_path.read_text(encoding="utf-8")


def _whisper_artifact(*, video_id: str = "vid1234567", text: str = "こんにちは", end_ms: int = 3_000):
    return build_transcript_artifact(
        video_id=video_id,
        source_kind="whisper_cpp",
        source_ref="transcripts/audio/range-1.wav",
        language="ja",
        ranges=[
            TranscriptRange(
                start_ms=1_000,
                end_ms=end_ms,
                padding_ms=200,
            )
        ],
        cues=[TranscriptCue(start_ms=1_200, end_ms=1_800, text=text)],
        audio_bytes=b"audio-range-1",
        sample_rate=16_000,
        channel=1,
        codec="pcm_s16le",
        ffmpeg_settings={"sample_rate": 16_000, "channel": 1},
        model={"name": "ggml-large-v3-turbo-q5_0", "file_sha256": "a" * 64},
        runtime={"version": "1.9.1", "build": "metal"},
        settings={"language": "ja", "decode": {"temperature": 0}},
        # S9-6 以降、高精度扱いには音声 span の取得経路の記録が要る。
        source_metadata={
            "audio_spans": [{"audio_route": "local_source_accurate_seek"}]
        },
    )


def test_transcript_artifact_round_trip_is_strict_and_keeps_integer_ms():
    artifact = _whisper_artifact()
    restored = TranscriptArtifact.model_validate(artifact.model_dump(mode="json"))

    assert restored == artifact
    assert restored.ranges[0].start_ms == 1_000
    assert restored.status is TranscriptArtifactStatus.SUCCESS

    with pytest.raises(ValueError):
        TranscriptArtifact.model_validate(
            {**artifact.model_dump(mode="json"), "unknown_field": "reject"}
        )
    with pytest.raises(TranscriptArtifactError, match="整数ミリ秒"):
        build_transcript_artifact(
            video_id="vid1234567",
            source_kind="whisper_cpp",
            source_ref="transcripts/audio/range-1.wav",
            language="ja",
            ranges=[{"start_ms": 1_000.5, "end_ms": 3_000}],
            cues=[],
            audio_bytes=b"audio",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model", {}),
        ("runtime", {}),
        ("settings", {}),
        ("audio_input_fingerprint", None),
    ),
)
def test_whisper_success_requires_provenance(field, value):
    artifact = _whisper_artifact()
    payload = artifact.model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValueError, match="provenance|fingerprint"):
        TranscriptArtifact.model_validate(payload)


def test_whisper_success_builder_fails_closed_without_provenance():
    with pytest.raises(TranscriptArtifactError, match="schema"):
        build_transcript_artifact(
            video_id="vid1234567",
            source_kind="whisper_cpp",
            source_ref="transcripts/audio/range-1.wav",
            language="ja",
            ranges=[{"start_ms": 1_000, "end_ms": 3_000}],
            cues=[],
        )


def test_youtube_vtt_success_requires_persistent_provenance():
    source = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsource\n"
    artifact = build_transcript_artifact(
        video_id="vid1234567",
        source_kind="youtube_vtt",
        source_ref="subtitles/ja.vtt",
        language="ja",
        ranges=[{"start_ms": 0, "end_ms": 2_000}],
        cues=[{"start_ms": 1_000, "end_ms": 2_000, "text": "source"}],
        source_bytes=source,
    )
    payload = artifact.model_dump(mode="json")

    for field, value in (
        ("source_ref", ""),
        ("source_fingerprint", None),
        ("source_content_sha256", None),
    ):
        with pytest.raises(ValueError, match="source|fingerprint|bytes"):
            TranscriptArtifact.model_validate({**payload, field: value})

    with pytest.raises(ValueError, match="source path"):
        TranscriptArtifact.model_validate(
            {**payload, "source_ref": "https://example.invalid/subtitle.vtt"}
        )

    malformed_source = (
        source + b"\n00:00:03.000 -> 00:00:04.000\nmalformed\n"
    )
    with pytest.raises(TranscriptArtifactError, match="malformed timing"):
        build_transcript_artifact(
            video_id="vid1234567",
            source_kind="youtube_vtt",
            source_ref="subtitles/ja.vtt",
            language="ja",
            ranges=[{"start_ms": 0, "end_ms": 2_000}],
            cues=[{"start_ms": 1_000, "end_ms": 2_000, "text": "source"}],
            source_bytes=malformed_source,
        )


def test_malformed_vtt_timing_block_is_not_silently_ignored():
    malformed = (
        b"WEBVTT\n\n"
        b"00:00:00.000 --> 00:00:01.000\nvalid\n\n"
        b"00:00:02.000 --> malformed\nignored\n"
    )

    with pytest.raises(TranscriptArtifactError, match="malformed timing"):
        parse_vtt_cues(malformed)
    with pytest.raises(TranscriptArtifactError, match="終了時刻"):
        parse_vtt_cues(
            b"WEBVTT\n\n00:00:02.000 --> 00:00:01.000\nreverse\n"
        )


@pytest.mark.parametrize(
    "malformed_timing",
    (
        "00:00:03.000 -> 00:00:04.000",
        "00:00:03.000 00:00:04.000",
    ),
)
def test_timestamp_like_malformed_vtt_block_is_rejected(malformed_timing):
    content = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\nvalid\n\n"
        f"{malformed_timing}\ninvalid\n"
    )
    with pytest.raises(TranscriptArtifactError, match="malformed timing"):
        parse_vtt_cues(content)


def test_vtt_metadata_blocks_can_contain_timestamp_like_text():
    content = (
        "WEBVTT\n\n"
        "NOTE\n00:00:03.000 -> 00:00:04.000\nnot a cue\n\n"
        "STYLE\n00:00:05.000 missing arrow\n\n"
        "REGION\n00:00:06.000 -> 00:00:07.000\n\n"
        "00:00:00.000 --> 00:00:01.000\nvalid\n"
    )
    cues = parse_vtt_cues(content)
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [
        (0, 1_000, "valid")
    ]


def test_cue_and_used_range_digest_are_order_absolute_and_rule_sensitive():
    cues = [
        {"start_ms": 1_000, "end_ms": 1_500, "text": "A"},
        {"start_ms": 2_000, "end_ms": 2_500, "text": "B"},
    ]
    reversed_cues = list(reversed(cues))
    assert absolute_cue_digest(cues) != absolute_cue_digest(reversed_cues)
    assert absolute_cue_digest(cues) != absolute_cue_digest(
        [{**cues[0], "start_ms": 1_001}, cues[1]]
    )
    range_value = {"start_ms": 1_000, "end_ms": 2_000}
    base = used_range_cue_digest(cues, [range_value])
    padded = used_range_cue_digest(cues, [range_value], padding=100)
    different_rule = used_range_cue_digest(
        cues, [range_value], inclusion_rule="half_open"
    )
    assert padded != base
    assert different_rule == base


def test_cache_identity_excludes_paths_but_changes_for_real_inputs():
    ranges = [{"start_ms": 1_000, "end_ms": 3_000}]
    common = {
        "source_kind": "whisper_cpp",
        "ranges": ranges,
        "audio_bytes": b"audio",
        "sample_rate": 16_000,
        "channel": 1,
        "codec": "pcm_s16le",
        "ffmpeg_settings": {"path": "/one/ffmpeg", "sample_rate": 16_000},
        "model": {"path": "/one/model", "file_sha256": "a" * 64},
        "runtime": {"path": "/one/whisper-cli", "build": "metal"},
        "settings": {"language": "ja", "padding_ms": 100},
    }
    same = make_cache_identity(**common)
    moved = make_cache_identity(
        **{
            **common,
            "ffmpeg_settings": {"path": "/two/ffmpeg", "sample_rate": 16_000},
            "model": {"path": "/two/model", "file_sha256": "a" * 64},
        }
    )
    assert moved == same
    assert make_cache_identity(**{**common, "audio_bytes": b"changed"}) != same
    assert make_cache_identity(**{**common, "source_bytes": b"WEBVTT A"}) != same
    assert make_cache_identity(**{**common, "codec": "aac"}) != same
    assert make_cache_identity(
        **{**common, "ffmpeg_settings": {"sample_rate": 8_000}}
    ) != same
    assert make_cache_identity(**{**common, "runtime": {"build": "cpu"}}) != same
    assert make_cache_identity(**{**common, "settings": {"language": "en"}}) != same
    assert make_cache_identity(
        **{**common, "ranges": [{"start_ms": 1_001, "end_ms": 3_000}]}
    ) != same


def test_artifact_and_index_persist_with_cache_hit_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = TranscriptArtifactStore("vid1234567", settings)
    artifact = _whisper_artifact()
    first_path = store.save(artifact)
    second_path = TranscriptArtifactStore("vid1234567", settings).save(artifact)

    assert first_path == second_path
    assert first_path == (
        tmp_path / "vid1234567" / "transcripts" / "artifacts"
        / f"{artifact.artifact_fingerprint}.json"
    )
    assert (tmp_path / "vid1234567" / "transcripts" / "index.json").is_file()
    assert len(TranscriptArtifactStore("vid1234567", settings).list_artifacts()) == 1
    assert not list(first_path.parent.glob(".*.tmp"))


def test_vtt_cache_hit_is_persistent_and_never_writes_canonical_source(tmp_path):
    settings = Settings(data_dir=tmp_path)
    source_path = tmp_path / "vid1234567" / "subtitles" / "ja.vtt"
    source_path.parent.mkdir(parents=True)
    source_bytes = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n既存\n".encode()
    source_path.write_bytes(source_bytes)
    store = TranscriptArtifactStore("vid1234567", settings)

    first, first_hit = store.save_vtt()
    second, second_hit = TranscriptArtifactStore("vid1234567", settings).save_vtt()

    assert first_hit is False
    assert second_hit is True
    assert second.artifact_fingerprint == first.artifact_fingerprint
    assert source_path.read_bytes() == source_bytes
    assert store.lock_path.is_file()


def test_content_only_vtt_fallback_is_not_persisted_without_a_source_path(tmp_path):
    settings = Settings(data_dir=tmp_path)
    content = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\ntransient\n"
    artifact, hit = TranscriptArtifactStore("vid1234567", settings).save_vtt(
        content=content
    )

    assert artifact.source_content_sha256 is not None
    assert hit is False
    assert not (tmp_path / "vid1234567" / "transcripts" / "index.json").exists()


def test_vtt_source_provenance_is_revalidated_and_stale_artifact_is_excluded(tmp_path):
    settings = Settings(data_dir=tmp_path)
    source_path = tmp_path / "vid1234567" / "subtitles" / "ja.vtt"
    source_path.parent.mkdir(parents=True)
    first_source = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nfirst\n"
    second_source = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsecond\n"
    source_path.write_bytes(first_source)
    store = TranscriptArtifactStore("vid1234567", settings)
    first, _ = store.save_vtt()

    source_path.write_bytes(second_source)
    with pytest.raises(TranscriptCacheError, match="fingerprint"):
        store.load_artifact(first.artifact_fingerprint)
    assert all(
        item.artifact_fingerprint != first.artifact_fingerprint
        for item in store.list_artifacts()
    )

    second, hit = store.save_vtt(vtt_path=source_path)
    assert hit is False
    assert second.artifact_fingerprint != first.artifact_fingerprint


def test_vtt_source_missing_fake_fingerprint_and_symlink_fail_closed(tmp_path):
    settings = Settings(data_dir=tmp_path)
    source_path = tmp_path / "vid1234567" / "subtitles" / "ja.vtt"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsource\n")
    store = TranscriptArtifactStore("vid1234567", settings)
    artifact, _ = store.save_vtt()
    artifact_path = (
        tmp_path
        / "vid1234567"
        / "transcripts"
        / "artifacts"
        / f"{artifact.artifact_fingerprint}.json"
    )

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["source_fingerprint"] = "b" * 64
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TranscriptCacheError, match="source fingerprint"):
        store.load_artifact(artifact.artifact_fingerprint)

    artifact_path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")
    outside = tmp_path / "outside.vtt"
    outside.write_bytes(source_path.read_bytes())
    source_path.unlink()
    source_path.symlink_to(outside)
    with pytest.raises(TranscriptCacheError, match="シンボリックリンク"):
        store.load_artifact(artifact.artifact_fingerprint)


def test_corrupt_index_recovers_only_valid_artifacts_and_partial_is_not_resolved(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = TranscriptArtifactStore("vid1234567", settings)
    artifact = _whisper_artifact()
    store.save(artifact)
    index_path = tmp_path / "vid1234567" / "transcripts" / "index.json"
    index_path.write_text("{\"partial\":", encoding="utf-8")
    broken_path = (
        tmp_path / "vid1234567" / "transcripts" / "artifacts"
        / ("b" * 64 + ".json")
    )
    broken_path.write_text("{\"partial\":", encoding="utf-8")

    loaded = store.list_artifacts()

    assert [item.artifact_fingerprint for item in loaded] == [artifact.artifact_fingerprint]
    recovered = json.loads(index_path.read_text(encoding="utf-8"))
    assert [item["artifact_fingerprint"] for item in recovered["artifacts"]] == [
        artifact.artifact_fingerprint
    ]


def test_fake_fingerprint_unknown_field_out_of_range_and_symlink_fail_closed(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = TranscriptArtifactStore("vid1234567", settings)
    artifact = _whisper_artifact()
    path = store.save(artifact)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TranscriptCacheError):
        store.load_artifact(artifact.artifact_fingerprint)

    path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")
    path.symlink_to(outside)
    with pytest.raises(TranscriptCacheError):
        store.load_artifact(artifact.artifact_fingerprint)

    with pytest.raises(ValueError):
        TranscriptArtifact.model_validate(
            {**artifact.model_dump(mode="json"), "source_ref": "../outside.wav"}
        )


def test_atomic_index_replace_failure_keeps_previous_index(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = TranscriptArtifactStore("vid1234567", settings)
    first = _whisper_artifact()
    store.save(first)
    index_path = tmp_path / "vid1234567" / "transcripts" / "index.json"
    before = index_path.read_bytes()
    second = build_transcript_artifact(
        video_id="vid1234567",
        source_kind="whisper_cpp",
        source_ref="transcripts/audio/range-2.wav",
        language="ja",
        ranges=[{"start_ms": 4_000, "end_ms": 6_000}],
        cues=[{"start_ms": 4_100, "end_ms": 4_800, "text": "次"}],
        audio_bytes=b"audio-range-2",
        model={"name": "ggml-large-v3-turbo-q5_0"},
        runtime={"version": "1.9.1", "build": "metal"},
        settings={"language": "ja"},
    )
    real_replace = __import__("os").replace

    def fail_index_replace(source, destination):
        if str(destination).endswith("index.json"):
            raise OSError("injected index replace failure")
        return real_replace(source, destination)

    with patch("yt_live_kit.services.transcript_artifact.os.replace", side_effect=fail_index_replace):
        with pytest.raises(TranscriptCacheError):
            store.save(second)
    assert index_path.read_bytes() == before
    assert not list(index_path.parent.glob(".index.json.*.tmp"))
    recovered = store.list_artifacts()
    assert {item.artifact_fingerprint for item in recovered} == {
        first.artifact_fingerprint,
        second.artifact_fingerprint,
    }


def test_used_range_change_invalidates_only_when_selected_cue_changes():
    ranges = [{"start_ms": 1_000, "end_ms": 2_000}]
    old = [
        {"start_ms": 500, "end_ms": 700, "text": "outside"},
        {"start_ms": 1_100, "end_ms": 1_500, "text": "inside"},
    ]
    outside_changed = [{**old[0], "text": "outside changed"}, old[1]]
    inside_changed = [old[0], {**old[1], "text": "inside changed"}]
    assert not used_range_invalidated(old, outside_changed, ranges)
    assert used_range_invalidated(old, inside_changed, ranges)


def test_selected_range_resolver_prefers_whisper_and_coarse_keeps_vtt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nVTT\n"
    resolver = resolve_selected_range(
        "vid1234567",
        settings,
        [{"start_ms": 1_000, "end_ms": 3_000}],
        vtt_content=vtt,
    )
    assert resolver.is_fallback is True
    assert resolver.artifact is not None
    assert resolver.artifact.source_kind.value == "youtube_vtt"

    whisper = _whisper_artifact()
    TranscriptArtifactStore("vid1234567", settings).save(whisper)
    selected = resolve_selected_range(
        "vid1234567",
        settings,
        [{"start_ms": 1_000, "end_ms": 3_000, "padding_ms": 200}],
        vtt_content=vtt,
        language=whisper.language,
        model=whisper.model,
        runtime=whisper.runtime,
        expected_settings=whisper.settings,
        audio_input_fingerprint=whisper.audio_input_fingerprint,
        expected_cache_identity_value=whisper.cache_identity,
        used_range_cue_digests=whisper.used_range_cue_digests,
    )
    assert selected.is_fallback is False
    assert selected.artifact is not None
    assert selected.artifact.source_kind.value == "whisper_cpp"


def test_selected_range_requires_language_and_expected_whisper_provenance(tmp_path):
    settings = Settings(data_dir=tmp_path)
    whisper = _whisper_artifact()
    TranscriptArtifactStore("vid1234567", settings).save(whisper)
    vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nVTT fallback\n"
    ranges = [{"start_ms": 1_000, "end_ms": 3_000, "padding_ms": 200}]

    missing = resolve_selected_range(
        "vid1234567", settings, ranges, vtt_content=vtt
    )
    assert missing.is_fallback is True
    assert "expected provenance" in (missing.fallback_reason or "")

    wrong_language = resolve_selected_range(
        "vid1234567",
        settings,
        ranges,
        vtt_content=vtt,
        language="en",
        model=whisper.model,
        runtime=whisper.runtime,
        expected_settings=whisper.settings,
        audio_input_fingerprint=whisper.audio_input_fingerprint,
        expected_cache_identity_value=whisper.cache_identity,
        used_range_cue_digests=whisper.used_range_cue_digests,
    )
    assert wrong_language.is_fallback is True
    assert wrong_language.artifact is not None
    assert wrong_language.artifact.source_kind.value == "youtube_vtt"

    wrong_model = resolve_selected_range(
        "vid1234567",
        settings,
        ranges,
        vtt_content=vtt,
        language=whisper.language,
        model={"name": "different-model"},
        runtime=whisper.runtime,
        expected_settings=whisper.settings,
        audio_input_fingerprint=whisper.audio_input_fingerprint,
        expected_cache_identity_value=whisper.cache_identity,
        used_range_cue_digests=whisper.used_range_cue_digests,
    )
    assert wrong_model.is_fallback is True
    assert wrong_model.artifact is not None
    assert wrong_model.artifact.source_kind.value == "youtube_vtt"

    missing_cache = resolve_selected_range(
        "vid1234567",
        settings,
        ranges,
        vtt_content=vtt,
        language=whisper.language,
        model=whisper.model,
        runtime=whisper.runtime,
        expected_settings=whisper.settings,
        audio_input_fingerprint=whisper.audio_input_fingerprint,
        used_range_cue_digests=whisper.used_range_cue_digests,
    )
    assert missing_cache.is_fallback is True
    assert "cache identity" in (missing_cache.fallback_reason or "")

    missing_used = resolve_selected_range(
        "vid1234567",
        settings,
        ranges,
        vtt_content=vtt,
        language=whisper.language,
        model=whisper.model,
        runtime=whisper.runtime,
        expected_settings=whisper.settings,
        audio_input_fingerprint=whisper.audio_input_fingerprint,
        expected_cache_identity_value=whisper.cache_identity,
    )
    assert missing_used.is_fallback is True
    assert "used_range_cue_digests" in (missing_used.fallback_reason or "")

    wrong_cache = resolve_selected_range(
        "vid1234567",
        settings,
        ranges,
        vtt_content=vtt,
        language=whisper.language,
        model=whisper.model,
        runtime=whisper.runtime,
        expected_settings=whisper.settings,
        audio_input_fingerprint=whisper.audio_input_fingerprint,
        expected_cache_identity_value="a" * 64,
        used_range_cue_digests=whisper.used_range_cue_digests,
    )
    assert wrong_cache.is_fallback is True
    assert wrong_cache.invalidated is True


def test_vtt_path_and_content_must_match_when_both_are_provided(tmp_path):
    settings = Settings(data_dir=tmp_path)
    source_path = tmp_path / "vid1234567" / "subtitles" / "incoming.vtt"
    source_path.parent.mkdir(parents=True)
    source = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsource\n"
    source_path.write_bytes(source)
    store = TranscriptArtifactStore("vid1234567", settings)

    store.save_vtt(vtt_path=source_path, content=source)
    with pytest.raises(TranscriptCacheError, match="bytes"):
        store.save_vtt(vtt_path=source_path, content=source.replace(b"source", b"other"))

    with pytest.raises(TranscriptCacheError, match="読み込めません"):
        store.save_vtt(
            vtt_path=tmp_path / "vid1234567" / "subtitles" / "missing.vtt",
            content=source,
        )


def test_selected_range_never_returns_partial_whisper_as_high_precision(tmp_path):
    settings = Settings(data_dir=tmp_path)
    partial = build_transcript_artifact(
        video_id="vid1234567",
        source_kind="whisper_cpp",
        source_ref="transcripts/audio/range-1.wav",
        language="ja",
        ranges=[
            TranscriptRange(
                start_ms=1_000,
                end_ms=3_000,
                padding_ms=200,
            )
        ],
        cues=[TranscriptCue(start_ms=1_200, end_ms=1_800, text="partial")],
        status=TranscriptArtifactStatus.PARTIAL,
        audio_bytes=b"partial-audio",
    )
    TranscriptArtifactStore("vid1234567", settings).save(partial)

    result = resolve_selected_range(
        "vid1234567",
        settings,
        [{"start_ms": 1_000, "end_ms": 3_000, "padding_ms": 200}],
        vtt_content=b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nVTT fallback\n",
    )

    assert result.is_fallback is True
    assert result.artifact is not None
    assert result.artifact.source_kind.value == "youtube_vtt"
    assert result.invalidated is True


def test_whisper_artifact_without_audio_route_is_not_high_precision():
    """S9-6 の開始ずれ修正前に作った artifact を高精度として再利用しない.

    旧 artifact は benchmark の不変証跡として disk に残ってよいが、経路を
    記録していない span から作られているため高精度扱いを外す。
    """

    legacy = build_transcript_artifact(
        video_id="vid1234567",
        source_kind="whisper_cpp",
        source_ref="transcripts/audio/range-1.wav",
        language="ja",
        ranges=[TranscriptRange(start_ms=1_000, end_ms=3_000)],
        cues=[TranscriptCue(start_ms=1_200, end_ms=1_800, text="旧 artifact")],
        audio_bytes=b"legacy-audio",
        model={"name": "ggml-large-v3-turbo-q5_0", "file_sha256": "a" * 64},
        runtime={"version": "1.9.1", "build": "metal"},
        settings={"language": "ja"},
        source_metadata={
            "audio_spans": [{"range": {"start_ms": 1_000, "end_ms": 3_000}}]
        },
    )

    assert legacy.status is TranscriptArtifactStatus.SUCCESS
    assert legacy.audio_spans_declare_route is False
    assert legacy.is_high_precision is False

    current = _whisper_artifact()
    assert current.audio_spans_declare_route is True
    assert current.is_high_precision is True


def test_whisper_artifact_with_fallback_route_stays_high_precision():
    """source が無い環境の yt-dlp fallback 経路も高精度扱いは維持する."""

    fallback = build_transcript_artifact(
        video_id="vid1234567",
        source_kind="whisper_cpp",
        source_ref="transcripts/audio/range-1.wav",
        language="ja",
        ranges=[TranscriptRange(start_ms=1_000, end_ms=3_000)],
        cues=[TranscriptCue(start_ms=1_200, end_ms=1_800, text="fallback")],
        audio_bytes=b"fallback-audio",
        model={"name": "ggml-large-v3-turbo-q5_0", "file_sha256": "a" * 64},
        runtime={"version": "1.9.1", "build": "metal"},
        settings={"language": "ja"},
        source_metadata={
            "audio_spans": [
                {
                    "audio_route": "ytdlp_download_sections_force_keyframes",
                    "alignment": {"method": "none", "verified": False},
                }
            ]
        },
    )

    assert fallback.is_high_precision is True
