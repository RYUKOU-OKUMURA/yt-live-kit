"""short_cut サービス（FR-30）のユニットテスト."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.ai_prompt import CodexNotFoundError
from yt_live_kit.services.short_cut import (
    CODEX_INSTALL_HINT,
    MAX_CUTS,
    ShortCutError,
    ShortCutValidationError,
    build_parent_transcript,
    build_short_cut_prompt,
    cut_plan_path,
    load_cut_plan,
    needs_short_cut,
    parent_bounds_ms,
    save_cut_plan,
    selected_total_ms,
    suggest_short_cuts,
    validate_short_cut,
    validate_short_cut_selection,
)

_VTT = """WEBVTT

00:39:10.000 --> 00:39:20.000
まず結論から言うと

00:39:20.000 --> 00:39:40.000
使い分けが全部です

00:41:00.000 --> 00:41:30.000
具体的な例を出します
"""


def _parent(
    *,
    id: str = "clip_002",
    start: str = "00:39:00",
    end: str = "00:50:00",
    duration_sec: int = 660,
) -> ClipCandidate:
    return ClipCandidate(
        id=id,
        title="Claude Code と Codex はどちらを選ぶべきか",
        start=start,
        end=end,
        duration_sec=duration_sec,
        reason="比較している区間",
    )


def _cut(
    index: int,
    *,
    start: str,
    end: str,
    duration_sec: int,
    title: str = "小区間",
    reason: str = "理由",
) -> dict:
    return {
        "id": f"cut_{index:03d}",
        "title": title,
        "start": start,
        "end": end,
        "duration_sec": duration_sec,
        "reason": reason,
    }


def _valid_payload() -> dict:
    return {
        "candidates": [
            _cut(1, start="00:39:10", end="00:40:00", duration_sec=50),
            _cut(2, start="00:41:00", end="00:42:00", duration_sec=60),
        ]
    }


def _prepare_video_dir(tmp_path: Path, video_id: str, *, vtt: str = _VTT) -> Settings:
    video_dir = tmp_path / video_id
    (video_dir / "subtitles").mkdir(parents=True)
    (video_dir / "subtitles" / "ja.vtt").write_text(vtt, encoding="utf-8")
    return Settings(data_dir=tmp_path)


def test_needs_short_cut_only_for_long_candidates():
    assert needs_short_cut(_parent()) is True
    assert needs_short_cut(_parent(start="00:00:00", end="00:03:00", duration_sec=180)) is False
    assert needs_short_cut(_parent(start="00:00:00", end="00:03:01", duration_sec=181)) is True


def test_parent_bounds_uses_integer_milliseconds():
    assert parent_bounds_ms(_parent()) == (2_340_000, 3_000_000)


def test_validate_short_cut_accepts_valid_payload():
    result = validate_short_cut(_valid_payload(), parent=_parent())
    assert result.ok is True
    assert result.errors == ()
    assert [segment.id for segment in result.segments] == ["cut_001", "cut_002"]
    assert result.total_ms == 110_000


def test_validate_short_cut_rejects_segment_outside_parent():
    payload = {
        "candidates": [
            _cut(1, start="00:30:00", end="00:31:00", duration_sec=60),
            _cut(2, start="00:41:00", end="00:42:00", duration_sec=60),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("外に出ています" in error for error in result.errors)


def test_validate_short_cut_rejects_out_of_order_segments():
    payload = {
        "candidates": [
            _cut(1, start="00:45:00", end="00:45:30", duration_sec=30),
            _cut(2, start="00:41:00", end="00:41:30", duration_sec=30),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("時系列順" in error for error in result.errors)


def test_validate_short_cut_rejects_overlapping_segments():
    payload = {
        "candidates": [
            _cut(1, start="00:41:00", end="00:42:00", duration_sec=60),
            _cut(2, start="00:41:30", end="00:42:30", duration_sec=60),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("重複しています" in error for error in result.errors)


def test_validate_short_cut_rejects_too_long_single_cut():
    payload = {
        "candidates": [
            _cut(1, start="00:39:10", end="00:42:00", duration_sec=170),
            _cut(2, start="00:43:00", end="00:43:30", duration_sec=30),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("120 秒を超えています" in error for error in result.errors)


def test_validate_short_cut_rejects_total_over_max():
    payload = {
        "candidates": [
            _cut(1, start="00:39:00", end="00:40:40", duration_sec=100),
            _cut(2, start="00:41:00", end="00:42:40", duration_sec=100),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("180 秒以下" in error for error in result.errors)


def test_validate_short_cut_rejects_total_under_min():
    payload = {
        "candidates": [
            _cut(1, start="00:39:00", end="00:39:03", duration_sec=3),
            _cut(2, start="00:41:00", end="00:41:05", duration_sec=5),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("10 秒以上" in error for error in result.errors)


def test_validate_short_cut_rejects_duration_mismatch():
    payload = {
        "candidates": [
            _cut(1, start="00:39:10", end="00:40:00", duration_sec=99),
            _cut(2, start="00:41:00", end="00:42:00", duration_sec=60),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("duration_sec" in error for error in result.errors)


def test_validate_short_cut_rejects_halfwidth_angle_brackets():
    payload = {
        "candidates": [
            _cut(1, start="00:39:10", end="00:40:00", duration_sec=50, title="<b>強調"),
            _cut(2, start="00:41:00", end="00:42:00", duration_sec=60, reason="a > b"),
        ]
    }
    result = validate_short_cut(payload, parent=_parent())
    assert result.ok is False
    assert any("タイトルに半角" in error for error in result.errors)
    assert any("理由に半角" in error for error in result.errors)


def test_validate_short_cut_rejects_cut_count_bounds():
    single = {"candidates": [_cut(1, start="00:39:10", end="00:40:00", duration_sec=50)]}
    assert validate_short_cut(single, parent=_parent()).ok is False

    too_many = {
        "candidates": [
            _cut(
                index,
                start=f"00:{39 + index}:00",
                end=f"00:{39 + index}:10",
                duration_sec=10,
            )
            for index in range(1, MAX_CUTS + 2)
        ]
    }
    result = validate_short_cut(too_many, parent=_parent())
    assert result.ok is False
    assert any(f"{MAX_CUTS} 個以下" in error for error in result.errors)


def test_validate_short_cut_rejects_broken_schema():
    result = validate_short_cut({"candidates": [{"id": "cut_001"}]}, parent=_parent())
    assert result.ok is False
    assert any("JSON 形式" in error for error in result.errors)


def test_validate_selection_allows_single_segment_and_skips_cut_limits():
    segments = [
        HighlightSegment(
            id="cut_001",
            title="小区間",
            start="00:39:10",
            end="00:39:23",
            duration_sec=13,
            reason="理由",
        )
    ]
    result = validate_short_cut_selection(segments, parent=_parent())
    assert result.ok is True
    assert result.total_ms == 13_000


def test_validate_selection_requires_at_least_one_segment():
    result = validate_short_cut_selection([], parent=_parent())
    assert result.ok is False
    assert any("1 個以上" in error for error in result.errors)


def test_validate_selection_enforces_total_and_parent_bounds():
    too_long = [
        HighlightSegment(
            id="cut_001",
            title="小区間",
            start="00:39:00",
            end="00:43:00",
            duration_sec=240,
            reason="理由",
        )
    ]
    assert validate_short_cut_selection(too_long, parent=_parent()).ok is False

    outside = [
        HighlightSegment(
            id="cut_001",
            title="小区間",
            start="00:10:00",
            end="00:10:30",
            duration_sec=30,
            reason="理由",
        )
    ]
    result = validate_short_cut_selection(outside, parent=_parent())
    assert result.ok is False
    assert any("外に出ています" in error for error in result.errors)


def test_selected_total_ms_matches_normalized_bounds():
    segments = [
        HighlightSegment(
            id="cut_001",
            title="a",
            start="00:39:10",
            end="00:40:00",
            duration_sec=50,
            reason="理由",
        ),
        HighlightSegment(
            id="cut_002",
            title="b",
            start="00:41:00",
            end="00:42:00",
            duration_sec=60,
            reason="理由",
        ),
    ]
    assert selected_total_ms(segments) == 110_000
    assert selected_total_ms([]) == 0


def test_build_parent_transcript_uses_absolute_timestamps():
    transcript = build_parent_transcript(_VTT, _parent())
    assert transcript.splitlines()[0] == "## 対象区間 [00:39:00 --> 00:50:00]"
    assert "[00:39:10 --> 00:39:20] まず結論から言うと" in transcript


def test_build_parent_transcript_rejects_segment_without_subtitles():
    with pytest.raises(ShortCutError, match="字幕がありません"):
        build_parent_transcript(
            _VTT, _parent(start="01:00:00", end="01:20:00", duration_sec=1200)
        )


def test_build_short_cut_prompt_embeds_transcript():
    prompt = build_short_cut_prompt(_VTT, _parent())
    assert "{{segment_transcript}}" not in prompt
    assert "## 対象区間 [00:39:00 --> 00:50:00]" in prompt
    assert "まず結論から言うと" in prompt


def test_save_cut_plan_writes_document_and_load_round_trips(tmp_path: Path):
    video_id = "vid_save"
    settings = _prepare_video_dir(tmp_path, video_id)
    parent = _parent()

    path, document = save_cut_plan(
        video_id, parent, json.dumps(_valid_payload(), ensure_ascii=False), settings
    )

    assert path == cut_plan_path(video_id, parent.id, settings)
    assert document.parent_id == "clip_002"
    assert document.parent_start_ms == 2_340_000
    assert document.parent_end_ms == 3_000_000
    assert [candidate.id for candidate in document.candidates] == ["cut_001", "cut_002"]

    loaded = load_cut_plan(video_id, parent.id, settings)
    assert loaded is not None
    assert loaded.model_dump() == document.model_dump()


def test_save_cut_plan_keeps_existing_file_when_validation_fails(tmp_path: Path):
    video_id = "vid_keep"
    settings = _prepare_video_dir(tmp_path, video_id)
    parent = _parent()
    save_cut_plan(
        video_id, parent, json.dumps(_valid_payload(), ensure_ascii=False), settings
    )
    before = cut_plan_path(video_id, parent.id, settings).read_text(encoding="utf-8")

    broken = {"candidates": [_cut(1, start="00:10:00", end="00:10:30", duration_sec=30)]}
    with pytest.raises(ShortCutValidationError):
        save_cut_plan(video_id, parent, json.dumps(broken), settings)

    after = cut_plan_path(video_id, parent.id, settings).read_text(encoding="utf-8")
    assert after == before


def test_save_cut_plan_accepts_fenced_json(tmp_path: Path):
    video_id = "vid_fence"
    settings = _prepare_video_dir(tmp_path, video_id)
    raw = "```json\n" + json.dumps(_valid_payload(), ensure_ascii=False) + "\n```"

    _path, document = save_cut_plan(video_id, _parent(), raw, settings)
    assert len(document.candidates) == 2


def test_load_cut_plan_returns_none_for_missing_or_broken(tmp_path: Path):
    video_id = "vid_missing"
    settings = _prepare_video_dir(tmp_path, video_id)
    assert load_cut_plan(video_id, "clip_002", settings) is None

    path = cut_plan_path(video_id, "clip_002", settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ broken", encoding="utf-8")
    assert load_cut_plan(video_id, "clip_002", settings) is None


def test_suggest_short_cuts_prompt_only_writes_prompt(tmp_path: Path):
    video_id = "vid_prompt"
    settings = _prepare_video_dir(tmp_path, video_id)

    result = suggest_short_cuts(video_id, _parent(), settings, prompt_only=True)

    assert result.prompt_path.is_file()
    assert result.cut_plan_path is None
    assert result.used_codex is False
    assert "## 対象区間" in result.prompt_path.read_text(encoding="utf-8")


def test_suggest_short_cuts_rejects_short_parent(tmp_path: Path):
    video_id = "vid_short_parent"
    settings = _prepare_video_dir(tmp_path, video_id)

    with pytest.raises(ShortCutError, match="そのままショートを作成できます"):
        suggest_short_cuts(
            video_id,
            _parent(start="00:39:00", end="00:41:00", duration_sec=120),
            settings,
        )


def test_suggest_short_cuts_requires_subtitles(tmp_path: Path):
    video_id = "vid_no_vtt"
    (tmp_path / video_id).mkdir(parents=True)
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(ShortCutError, match="字幕ファイルが見つかりません"):
        suggest_short_cuts(video_id, _parent(), settings)


@patch("yt_live_kit.services.short_cut.is_codex_available", return_value=False)
def test_suggest_short_cuts_codex_not_found_message(_mock_available, tmp_path: Path):
    video_id = "vid_no_codex"
    settings = _prepare_video_dir(tmp_path, video_id)

    with pytest.raises(CodexNotFoundError) as exc_info:
        suggest_short_cuts(video_id, _parent(), settings)

    message = str(exc_info.value)
    assert "Codex CLI が見つかりません" in message
    assert "npm install -g @openai/codex" in message
    assert "cut_clip_002.json" in message
    assert "cut_clip_002.prompt.md" in message


@patch("yt_live_kit.services.short_cut.is_codex_available", return_value=True)
@patch("yt_live_kit.services.short_cut.invoke_codex")
def test_suggest_short_cuts_saves_validated_plan(
    mock_invoke, _mock_available, tmp_path: Path
):
    video_id = "vid_codex"
    settings = _prepare_video_dir(tmp_path, video_id)
    mock_invoke.return_value = json.dumps(_valid_payload(), ensure_ascii=False)

    result = suggest_short_cuts(video_id, _parent(), settings)

    assert result.used_codex is True
    assert result.cut_plan_path is not None and result.cut_plan_path.is_file()
    assert result.document is not None
    assert len(result.document.candidates) == 2
    assert mock_invoke.call_count == 1


@patch("yt_live_kit.services.short_cut.is_codex_available", return_value=True)
@patch("yt_live_kit.services.short_cut.invoke_codex")
def test_suggest_short_cuts_rejects_invalid_codex_output(
    mock_invoke, _mock_available, tmp_path: Path
):
    video_id = "vid_codex_bad"
    settings = _prepare_video_dir(tmp_path, video_id)
    mock_invoke.return_value = json.dumps(
        {"candidates": [_cut(1, start="00:10:00", end="00:10:30", duration_sec=30)]}
    )

    with pytest.raises(ShortCutValidationError):
        suggest_short_cuts(video_id, _parent(), settings)

    assert not cut_plan_path(video_id, "clip_002", settings).is_file()


def test_codex_install_hint_has_no_unimplemented_cli_command():
    assert "short-cut" not in CODEX_INSTALL_HINT
    assert "--from-file" not in CODEX_INSTALL_HINT


def test_cut_plan_path_sanitizes_parent_id(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    path = cut_plan_path("vid", "clip/../002", settings)
    assert path.parent == tmp_path / "vid" / "shorts" / "cutplan"
    assert path.name == "cut_clip____002.json"

    with pytest.raises(ShortCutError):
        cut_plan_path("vid", "   ", settings)
