"""highlights サービスのユニットテスト."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.highlights import (
    HighlightValidationError,
    HighlightsError,
    _extract_json_object,
    build_highlights_prompt,
    find_project_root,
    save_segments_file,
    suggest_highlights,
    validate_highlights,
)


def _segment(
    index: int,
    *,
    start: str = "00:00:00",
    end: str = "00:02:00",
    duration_sec: int = 120,
    title: str | None = None,
    reason: str = "テスト理由",
) -> dict:
    return {
        "id": f"hl_{index:03d}",
        "title": title or f"山場 {index}",
        "start": start,
        "end": end,
        "duration_sec": duration_sec,
        "reason": reason,
    }


def _sample_highlights_data(count: int = 5) -> dict:
    candidates = []
    for i in range(count):
        start_min = i * 5
        candidates.append(
            _segment(
                i + 1,
                start=f"00:{start_min:02d}:00",
                end=f"00:{start_min + 2:02d}:00",
                duration_sec=120,
            )
        )
    return {"candidates": candidates}


def _sample_highlights_json(count: int = 5) -> str:
    return json.dumps(_sample_highlights_data(count), ensure_ascii=False)


def test_find_project_root():
    root = find_project_root()
    assert (root / "prompts" / "highlights.md").is_file()


def test_build_highlights_prompt_embeds_transcript():
    prompt = build_highlights_prompt("[00:00:00] テスト字幕")
    assert "[00:00:00] テスト字幕" in prompt
    assert "{{compressed_transcript}}" not in prompt


def test_validate_highlights_ok():
    result = validate_highlights(_sample_highlights_data())
    assert result.ok
    assert not result.errors


def test_validate_highlights_too_few():
    data = {"candidates": [_segment(1)]}
    result = validate_highlights(data)
    assert not result.ok
    assert any("2 個以上" in err for err in result.errors)


def test_validate_highlights_too_many():
    data = _sample_highlights_data(21)
    result = validate_highlights(data)
    assert not result.ok
    assert any("20 個以下" in err for err in result.errors)


def test_validate_highlights_too_short():
    data = {
        "candidates": [
            _segment(1, start="00:00:00", end="00:00:09", duration_sec=9),
            _segment(2, start="00:05:00", end="00:07:00", duration_sec=120),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("10 秒未満" in err for err in result.errors)


def test_validate_highlights_too_long():
    data = {
        "candidates": [
            _segment(1, start="00:00:00", end="00:05:01", duration_sec=301),
            _segment(2, start="00:10:00", end="00:12:00", duration_sec=120),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("5 分を超え" in err for err in result.errors)


def test_validate_highlights_start_not_before_end():
    data = {
        "candidates": [
            _segment(1, start="00:05:00", end="00:05:00", duration_sec=0),
            _segment(2, start="00:10:00", end="00:12:00", duration_sec=120),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("開始時刻より後" in err for err in result.errors)


def test_validate_highlights_reverse_order():
    data = {
        "candidates": [
            _segment(1, start="00:10:00", end="00:12:00", duration_sec=120),
            _segment(2, start="00:05:00", end="00:07:00", duration_sec=120),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("時系列順" in err for err in result.errors)


def test_validate_highlights_overlap():
    data = {
        "candidates": [
            _segment(1, start="00:00:00", end="00:03:00", duration_sec=180),
            _segment(2, start="00:02:00", end="00:05:00", duration_sec=180),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("重複" in err for err in result.errors)


def test_validate_highlights_exceeds_video_duration():
    data = _sample_highlights_data(2)
    result = validate_highlights(data, video_duration=300)
    assert not result.ok
    assert any("動画長" in err for err in result.errors)


def test_validate_highlights_halfwidth_angle_brackets_in_title():
    data = {
        "candidates": [
            _segment(1, title="テスト<NG>"),
            _segment(2, start="00:05:00", end="00:07:00", duration_sec=120),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("タイトル" in err and "<>" in err for err in result.errors)


def test_validate_highlights_halfwidth_angle_brackets_in_reason():
    data = {
        "candidates": [
            _segment(1, reason="理由<NG>"),
            _segment(2, start="00:05:00", end="00:07:00", duration_sec=120),
        ]
    }
    result = validate_highlights(data)
    assert not result.ok
    assert any("理由" in err and "<>" in err for err in result.errors)


def test_extract_json_object_plain():
    raw = _sample_highlights_json(2)
    data = _extract_json_object(raw)
    assert len(data["candidates"]) == 2


def test_extract_json_object_with_surrounding_text():
    raw = f"説明文\n{_sample_highlights_json(2)}\n以上です"
    data = _extract_json_object(raw)
    assert len(data["candidates"]) == 2


def test_extract_json_object_with_code_fence():
    inner = _sample_highlights_json(2)
    raw = f"```json\n{inner}\n```"
    data = _extract_json_object(raw)
    assert len(data["candidates"]) == 2


def test_save_segments_file(tmp_path: Path):
    video_id = "test_highlights"
    (tmp_path / video_id).mkdir()
    settings = Settings(data_dir=tmp_path)

    path, doc = save_segments_file(video_id, _sample_highlights_json(3), settings)
    assert path.is_file()
    assert path.name == "segments.json"
    assert len(doc.candidates) == 3


def test_save_segments_file_invalid_raises(tmp_path: Path):
    video_id = "test_invalid"
    (tmp_path / video_id).mkdir()
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(HighlightValidationError):
        save_segments_file(video_id, '{"candidates": []}', settings)


def test_suggest_highlights_prompt_only(tmp_path: Path):
    video_id = "test_prompt"
    video_dir = tmp_path / video_id
    (video_dir / "transcript").mkdir(parents=True)
    (video_dir / "transcript" / "compressed.txt").write_text(
        "[00:00:00] hello", encoding="utf-8"
    )

    settings = Settings(data_dir=tmp_path)
    result = suggest_highlights(video_id, settings, prompt_only=True)
    assert result.prompt_path.is_file()
    assert result.segments_path is None
    assert result.used_codex is False


@patch("yt_live_kit.services.highlights.is_codex_available", return_value=False)
def test_suggest_highlights_codex_not_found_message(_mock_available, tmp_path: Path):
    video_id = "test_no_codex"
    video_dir = tmp_path / video_id
    (video_dir / "transcript").mkdir(parents=True)
    (video_dir / "transcript" / "compressed.txt").write_text(
        "[00:00:00] hello", encoding="utf-8"
    )

    settings = Settings(data_dir=tmp_path)
    from yt_live_kit.services.ai_prompt import CodexNotFoundError

    with pytest.raises(CodexNotFoundError) as exc_info:
        suggest_highlights(video_id, settings)

    message = str(exc_info.value)
    assert "Codex CLI が見つかりません" in message
    assert "npm install -g @openai/codex" in message
    assert "prompt_highlights.txt" in message or str(
        settings.data_dir / video_id / "prompt_highlights.txt"
    ) in message
