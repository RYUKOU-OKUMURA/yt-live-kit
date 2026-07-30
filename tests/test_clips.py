"""clips サービスのユニットテスト."""

import json
from pathlib import Path

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.clips import (
    ClipValidationError,
    build_clips_prompt,
    find_project_root,
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
