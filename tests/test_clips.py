"""clips サービスのユニットテスト."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import (
    ClipCandidate,
    ClipCandidatesDocument,
    ClipCandidatesLineage,
)
from yt_live_kit.services.transcript_artifact import build_transcript_artifact
from yt_live_kit.services.clips import (
    ClipsError,
    ClipValidationError,
    build_clips_prompt,
    find_project_root,
    make_candidate_fingerprint,
    load_candidates_file,
    save_candidates_file,
    suggest_clips,
    validate_clip_candidates,
)


def _sample_candidates_json() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "id": "clip_001",
                    "title": "Cursor Agent デモまとめ",
                    "start": "00:03:42",
                    "end": "00:16:30",
                    "duration_sec": 768,
                    "reason": "AI ツール実演が一続きで説明されている区間",
                },
                {
                    "id": "clip_002",
                    "title": "質疑応答ハイライト",
                    "start": "00:25:30",
                    "end": "00:38:00",
                    "duration_sec": 750,
                    "reason": "視聴者質問への回答がまとまっている",
                },
            ]
        },
        ensure_ascii=False,
    )


def test_find_project_root():
    root = find_project_root()
    assert (root / "prompts" / "clips_suggest.md").is_file()


def test_build_clips_prompt_embeds_transcript():
    prompt = build_clips_prompt("[00:00:00] テスト字幕")
    assert "[00:00:00] テスト字幕" in prompt
    assert "{{compressed_transcript}}" not in prompt


def test_validate_clip_candidates_ok():
    data = json.loads(_sample_candidates_json())
    doc, errors = validate_clip_candidates(data)
    assert not errors
    assert len(doc.candidates) == 2


def _sample_candidate_item() -> dict:
    return {
        "id": "clip_001",
        "title": "Cursor Agent デモまとめ",
        "start": "00:03:42",
        "end": "00:16:30",
        "duration_sec": 768,
        "reason": "AI ツール実演が一続きで説明されている区間",
    }


def test_clip_models_reject_unknown_fields():
    candidate_payload = _sample_candidate_item()
    assert ClipCandidate.model_validate(candidate_payload)
    with pytest.raises(ValidationError):
        ClipCandidate.model_validate({**candidate_payload, "unknown": True})

    doc_payload = {"candidates": [candidate_payload]}
    assert ClipCandidatesDocument.model_validate(doc_payload)
    with pytest.raises(ValidationError):
        ClipCandidatesDocument.model_validate({**doc_payload, "unknown": True})
    with pytest.raises(ValidationError):
        ClipCandidatesDocument.model_validate(
            {"candidates": [{**candidate_payload, "unknown": True}]}
        )


def test_validate_clip_candidates_rejects_unknown_fields():
    data = json.loads(_sample_candidates_json())
    _, errors = validate_clip_candidates({**data, "unknown": True})
    assert any("JSON 形式が想定と異なります" in err for err in errors)

    tampered = {
        **data,
        "candidates": [{**data["candidates"][0], "unknown": True}, data["candidates"][1]],
    }
    _, errors = validate_clip_candidates(tampered)
    assert any("JSON 形式が想定と異なります" in err for err in errors)


def test_validate_clip_candidates_too_few():
    data = {
        "candidates": [
            {
                "id": "clip_001",
                "title": "単独候補",
                "start": "00:03:42",
                "end": "00:16:30",
                "duration_sec": 768,
                "reason": "テスト",
            }
        ]
    }
    _, errors = validate_clip_candidates(data)
    assert any("2 件以上" in err for err in errors)


def test_validate_clip_candidates_duration_mismatch():
    data = json.loads(_sample_candidates_json())
    data["candidates"][0]["duration_sec"] = 100
    _, errors = validate_clip_candidates(data)
    assert any("duration_sec" in err for err in errors)


def test_validate_clip_candidates_end_exceeds_duration():
    data = json.loads(_sample_candidates_json())
    _, errors = validate_clip_candidates(data, video_duration_sec=600)
    assert any("動画長" in err for err in errors)


def test_save_candidates_file(tmp_path: Path):
    video_id = "test_clips"
    video_dir = tmp_path / video_id
    video_dir.mkdir()
    settings = Settings(data_dir=tmp_path)

    path, doc = save_candidates_file(video_id, _sample_candidates_json(), settings)
    assert path.is_file()
    assert len(doc.candidates) == 2
    assert path.name == "candidates.json"
    assert list(path.parent.glob(".*.tmp")) == []


@patch("yt_live_kit.services.clips.write_text_atomically")
def test_save_candidates_file_uses_atomic_helper(mock_write_atomic, tmp_path: Path):
    video_id = "test_atomic"
    (tmp_path / video_id).mkdir()
    settings = Settings(data_dir=tmp_path)

    path, _ = save_candidates_file(video_id, _sample_candidates_json(), settings)

    mock_write_atomic.assert_called_once()
    assert mock_write_atomic.call_args.args[0] == path


def test_save_candidates_file_invalid_raises(tmp_path: Path):
    video_id = "test_invalid"
    (tmp_path / video_id).mkdir()
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(ClipValidationError):
        save_candidates_file(video_id, '{"candidates": []}', settings)


def test_suggest_clips_prompt_only(tmp_path: Path):
    video_id = "test_prompt"
    video_dir = tmp_path / video_id
    (video_dir / "transcript").mkdir(parents=True)
    (video_dir / "transcript" / "compressed.txt").write_text(
        "[00:00:00] hello", encoding="utf-8"
    )

    settings = Settings(data_dir=tmp_path)
    result = suggest_clips(video_id, settings, prompt_only=True)
    assert result.prompt_path.is_file()
    assert result.candidates_path is None
    assert result.used_codex is False


def test_coarse_candidate_lineage_round_trips_without_changing_display_order(tmp_path: Path):
    video_id = "test_lineage"
    video_dir = tmp_path / video_id
    video_dir.mkdir()
    settings = Settings(data_dir=tmp_path)
    artifact = build_transcript_artifact(
        video_id=video_id,
        source_kind="youtube_vtt",
        source_ref="subtitles/ja.vtt",
        language="ja",
        ranges=[{"start_ms": 0, "end_ms": 3_000_000}],
        cues=[{"start_ms": 222_000, "end_ms": 223_000, "text": "候補"}],
        source_bytes=b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsource\n",
    )
    # build の VTT artifact は source bytes と cue 内容が一致する必要はなく、
    # 候補 lineage は artifact の検証済み digest だけを参照する。
    path, document = save_candidates_file(
        video_id,
        _sample_candidates_json(),
        settings,
        coarse_artifact=artifact,
    )

    assert document.lineage is not None
    assert document.lineage.coarse_vtt_artifact_fingerprint == artifact.artifact_fingerprint
    assert [item.id for item in document.candidates] == ["clip_001", "clip_002"]
    loaded = load_candidates_file(video_id, settings)
    assert loaded is not None
    assert loaded.lineage == document.lineage
    assert [item.id for item in loaded.candidates] == ["clip_001", "clip_002"]
    assert path.read_text(encoding="utf-8").find("lineage") >= 0


def test_candidate_fingerprint_keeps_order_and_rejects_tampered_lineage(tmp_path: Path):
    candidates = list(validate_clip_candidates(json.loads(_sample_candidates_json()))[0].candidates)
    first = make_candidate_fingerprint("clips", candidates)
    second = make_candidate_fingerprint("clips", list(reversed(candidates)))
    assert first != second

    video_id = "test_lineage_tamper"
    (tmp_path / video_id).mkdir()
    settings = Settings(data_dir=tmp_path)
    lineage = ClipCandidatesLineage(
        coarse_vtt_artifact_fingerprint="a" * 64,
        coarse_full_cue_digest="b" * 64,
        candidate_fingerprint="c" * 64,
    )
    with pytest.raises(ClipsError):
        save_candidates_file(
            video_id,
            _sample_candidates_json(),
            settings,
            lineage=lineage,
        )
