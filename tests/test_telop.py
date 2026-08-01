"""テロップ台本生成サービスのユニットテスト."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.ai_prompt import AiPromptError, CodexNotFoundError
from yt_live_kit.services.subtitle_burn import parse_vtt_with_end
from yt_live_kit.services.telop import (
    CODEX_INSTALL_HINT,
    ConfirmedTelopScriptResult,
    TelopError,
    TelopValidationError,
    _extract_json_object,
    build_telop_prompt,
    generate_telop_script,
    make_clip_id,
    normalize_seconds_to_milliseconds,
    normalize_segment_bounds,
    save_confirmed_telop_script,
    validate_telop_script,
)

SAMPLE_VTT = """WEBVTT

1
00:00:09.500 --> 00:00:12.250
最初の字幕です

2
00:00:12.250 --> 00:00:15.750
次の字幕です

3
00:00:20.000 --> 00:00:23.500
別の区間です
"""


def _segment(
    index: int = 1,
    *,
    start: str = "00:00:10",
    end: str = "00:00:16",
) -> HighlightSegment:
    start_sec = int(start.rsplit(":", 1)[-1])
    end_sec = int(end.rsplit(":", 1)[-1])
    return HighlightSegment(
        id=f"hl_{index:03d}",
        title=f"区間 {index}",
        start=start,
        end=end,
        duration_sec=end_sec - start_sec,
        reason="テスト用",
    )


def _segments() -> list[HighlightSegment]:
    return [
        _segment(),
        _segment(2, start="00:00:20", end="00:00:24"),
    ]


def _valid_document() -> dict:
    return {
        "hook_text": "大事なポイント",
        "title_candidates": ["配信の要点"],
        "description": "配信の一部を紹介します。",
        "tags": ["要点"],
        "segments": [
            {
                "start_sec": 10.0,
                "end_sec": 16.0,
                "lines": [
                    {
                        "start_sec": 10.0,
                        "end_sec": 12.25,
                        "text": "最初の字幕です",
                        "emphasis": True,
                    },
                    {
                        "start_sec": 12.25,
                        "end_sec": 15.75,
                        "text": "次の字幕です",
                        "emphasis": False,
                    },
                ],
            },
            {
                "start_sec": 20.0,
                "end_sec": 24.0,
                "lines": [
                    {
                        "start_sec": 20.0,
                        "end_sec": 23.5,
                        "text": "別の区間です",
                        "emphasis": False,
                    }
                ],
            },
        ],
    }


def _prepare_video(tmp_path: Path, video_id: str = "video123") -> Settings:
    subtitles = tmp_path / video_id / "subtitles"
    subtitles.mkdir(parents=True)
    (subtitles / "ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")
    return Settings(data_dir=tmp_path)


def test_save_confirmed_telop_script_returns_path_and_normalized_document(
    tmp_path: Path,
):
    settings = _prepare_video(tmp_path)
    data = _valid_document()
    data["hook_text"] = "  大事なポイント  "
    result = save_confirmed_telop_script(
        "video123", _segments(), data, settings
    )
    assert isinstance(result, ConfirmedTelopScriptResult)
    assert result.path.is_file()
    assert result.document.hook_text == "大事なポイント"
    assert json.loads(result.path.read_text(encoding="utf-8"))["hook_text"] == "大事なポイント"


def test_save_confirmed_telop_script_failure_preserves_existing_json(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    valid = save_confirmed_telop_script(
        "video123", _segments(), _valid_document(), settings
    )
    before = valid.path.read_text(encoding="utf-8")
    invalid = _valid_document()
    invalid["hook_text"] = "禁止<文字>"
    with pytest.raises(TelopValidationError):
        save_confirmed_telop_script("video123", _segments(), invalid, settings)
    assert valid.path.read_text(encoding="utf-8") == before


def test_save_confirmed_telop_script_replace_failure_is_japanese_and_preserves_existing(
    tmp_path: Path,
):
    settings = _prepare_video(tmp_path)
    valid = save_confirmed_telop_script(
        "video123", _segments(), _valid_document(), settings
    )
    before = valid.path.read_text(encoding="utf-8")
    changed = _valid_document()
    changed["hook_text"] = "変更したフック"
    with (
        patch("yt_live_kit.services._fsutil.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(TelopError, match="保存できませんでした"),
    ):
        save_confirmed_telop_script("video123", _segments(), changed, settings)
    assert valid.path.read_text(encoding="utf-8") == before


def test_public_vtt_parser_keeps_end_and_milliseconds():
    cues = parse_vtt_with_end(SAMPLE_VTT)
    assert cues[0].start_seconds == pytest.approx(9.5)
    assert cues[0].end_seconds == pytest.approx(12.25)


def test_build_prompt_uses_clipped_absolute_times():
    prompt = build_telop_prompt(SAMPLE_VTT, [_segment()])
    assert "[00:00:10.000 --> 00:00:12.250] 最初の字幕です" in prompt
    assert "[00:00:12.250 --> 00:00:15.750] 次の字幕です" in prompt
    assert "{{segment_transcripts}}" not in prompt


def test_validate_valid_document_normalizes_strings():
    data = _valid_document()
    data["hook_text"] = "  大事なポイント  "
    result = validate_telop_script(data, segments=_segments())
    assert result.ok
    assert result.errors == ()
    assert result.document is not None
    assert result.document.hook_text == "大事なポイント"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda data: data["segments"].pop(), "区間数"),
        (lambda data: data["segments"][0].update(start_sec=10.001), "入力区間"),
        (lambda data: data["segments"][0]["lines"].clear(), "1 件以上"),
        (
            lambda data: data["segments"][0]["lines"][0].update(start_sec=9.9),
            "範囲外",
        ),
        (
            lambda data: data["segments"][0]["lines"][1].update(
                start_sec=9.0, end_sec=9.5
            ),
            "時系列順",
        ),
        (
            lambda data: data["segments"][0]["lines"][1].update(start_sec=12.0),
            "重複",
        ),
        (
            lambda data: data["segments"][0]["lines"][0].update(
                start_sec=11.0, end_sec=11.0
            ),
            "開始時刻より後",
        ),
        (lambda data: data["segments"][0]["lines"][0].update(text="  "), "本文"),
        (lambda data: data.update(hook_text="  "), "フック文言"),
        (lambda data: data.update(title_candidates=[]), "タイトル案"),
        (lambda data: data.update(title_candidates=[" "]), "タイトル案 1"),
        (lambda data: data.update(description=" "), "説明文"),
        (lambda data: data.update(tags=[]), "タグ"),
        (lambda data: data.update(tags=[" "]), "タグ 1"),
    ],
)
def test_validate_rejects_rule_violations(mutate, expected: str):
    data = _valid_document()
    mutate(data)
    result = validate_telop_script(data, segments=_segments())
    assert not result.ok
    assert any(expected in error for error in result.errors)
    assert result.document is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(hook_text="フック<禁止>"),
        lambda data: data["segments"][0]["lines"][0].update(text="本文<禁止>"),
        lambda data: data.update(title_candidates=["タイトル<禁止>"]),
        lambda data: data.update(description="説明<禁止>"),
        lambda data: data.update(tags=["タグ<禁止>"]),
    ],
)
def test_validate_rejects_halfwidth_angles_in_all_generated_text(mutate):
    data = _valid_document()
    mutate(data)
    result = validate_telop_script(data, segments=_segments())
    assert not result.ok
    assert any("半角の山カッコ" in error for error in result.errors)


def test_validate_long_line_is_warning_not_error():
    data = _valid_document()
    data["segments"][0]["lines"][0]["text"] = "あ" * 17
    result = validate_telop_script(data, segments=_segments())
    assert result.ok
    assert any("16 文字" in warning for warning in result.warnings)


def test_validate_schema_error_is_japanese_and_hides_pydantic_details():
    result = validate_telop_script({"hook_text": "不足"}, segments=_segments())
    assert not result.ok
    joined = "\n".join(result.errors)
    assert "JSON 形式が想定と異なります" in joined
    assert "pydantic" not in joined.lower()
    assert "validation error" not in joined.lower()
    assert "For further information" not in joined


def test_make_clip_id_is_same_for_highlight_and_tuple():
    highlight = _segment(start="00:00:10", end="00:00:16")
    assert make_clip_id([highlight]) == make_clip_id([(10.0, 16.0)])


def test_make_clip_id_changes_with_order():
    ordered = [(10.0, 16.0), (20.0, 24.0)]
    assert make_clip_id(ordered) != make_clip_id(list(reversed(ordered)))


def test_make_clip_id_rounds_half_millisecond_up():
    expected = hashlib.sha256(b"1-1001").hexdigest()[:12]
    assert make_clip_id([(0.0005, 1.0005)]) == expected
    assert make_clip_id([(0.0005, 1.0005)]) == make_clip_id([(0.001, 1.001)])


def test_public_segment_normalization_uses_half_up_and_derived_properties():
    assert normalize_seconds_to_milliseconds(0.00049) == 0
    assert normalize_seconds_to_milliseconds(0.0005) == 1
    bounds = normalize_segment_bounds([(0.0005, 9.99951)])[0]
    assert bounds.start_ms == 1
    assert bounds.end_ms == 10_000
    assert bounds.start_sec == pytest.approx(0.001)
    assert bounds.end_sec == pytest.approx(10.0)
    assert bounds.duration_ms == 9_999
    assert bounds.duration_sec == pytest.approx(9.999)


def test_public_segment_normalization_preserves_input_order_and_duplicates():
    segments = [(20.0, 24.0), (10.0, 16.0), (20.0, 24.0)]
    normalized = normalize_segment_bounds(segments)
    assert [(item.start_ms, item.end_ms) for item in normalized] == [
        (20_000, 24_000),
        (10_000, 16_000),
        (20_000, 24_000),
    ]


@pytest.mark.parametrize(
    "value", ["1", None, True, float("nan"), float("inf"), -0.0004]
)
def test_public_seconds_normalization_rejects_invalid_values(value):
    with pytest.raises(TelopError):
        normalize_seconds_to_milliseconds(value)


@pytest.mark.parametrize("value", [10**100, 1e308])
def test_public_seconds_normalization_rejects_unrepresentable_large_values(value):
    with pytest.raises(TelopError, match="整数ミリ秒へ変換できません"):
        normalize_seconds_to_milliseconds(value)


def test_validate_telop_tuple_segments_uses_same_millisecond_boundaries():
    data = _valid_document()
    result = validate_telop_script(data, segments=[(10.00049, 15.99951), (20.0, 24.0)])
    assert result.ok
    assert result.document is not None
    assert result.document.segments[0].start_sec == 10.0
    assert result.document.segments[0].end_sec == 16.0


@pytest.mark.parametrize(
    ("segments", "expected"),
    [
        ([], "1 件以上"),
        ([(float("nan"), 1.0)], "有限"),
        ([(0.0, float("inf"))], "有限"),
        ([(-1.0, 1.0)], "負"),
        ([(-0.0004, 1.0)], "負"),
        ([(1.0, 1.0)], "開始時刻より後"),
        ([(2.0, 1.0)], "開始時刻より後"),
    ],
)
def test_make_clip_id_invalid_boundaries_are_japanese(segments, expected: str):
    with pytest.raises(TelopError, match=expected):
        make_clip_id(segments)


def test_extract_json_plain_fenced_and_surrounded():
    data = _valid_document()
    raw = json.dumps(data, ensure_ascii=False)
    assert _extract_json_object(raw) == data
    assert _extract_json_object(f"```json\n{raw}\n```") == data
    assert _extract_json_object(f"前置きです\n{raw}\n以上です") == data


def test_validate_rejects_negative_submillisecond_line_start():
    segment = _segment(start="00:00:00", end="00:00:06")
    data = _valid_document()
    data["segments"] = [
        {
            "start_sec": 0.0,
            "end_sec": 6.0,
            "lines": [
                {
                    "start_sec": -0.0004,
                    "end_sec": 1.0,
                    "text": "先頭の字幕",
                    "emphasis": False,
                }
            ],
        }
    ]
    result = validate_telop_script(data, segments=[segment])
    assert not result.ok
    assert any("負の値" in error for error in result.errors)


def test_extract_json_failure_is_japanese():
    with pytest.raises(TelopValidationError, match="解析できません"):
        _extract_json_object("JSON ではありません")


def test_prompt_only_succeeds_without_codex_check(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    with patch(
        "yt_live_kit.services.telop.is_codex_available",
        side_effect=AssertionError("Codex 可用性を確認してはいけない"),
    ):
        result = generate_telop_script(
            "video123", [_segment()], settings, prompt_only=True
        )
    assert result.prompt_path.is_file()
    assert result.script_path is None
    assert result.used_codex is False
    assert result.document is None


def test_segment_without_cues_stops_before_prompt_save_and_codex(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    segment = _segment(start="00:00:30", end="00:00:36")
    with (
        patch("yt_live_kit.services.telop.is_codex_available") as available,
        patch("yt_live_kit.services.telop.invoke_codex") as invoke,
    ):
        with pytest.raises(TelopError) as error:
            generate_telop_script("video123", [segment], settings)

    message = str(error.value)
    assert "区間 1" in message
    assert "字幕がありません" in message
    assert "字幕のある範囲へ変更" in message
    available.assert_not_called()
    invoke.assert_not_called()
    assert not list((tmp_path / "video123" / "shorts").glob("**/prompt_telop_*.txt"))


def test_codex_missing_has_install_and_manual_hint(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    with patch("yt_live_kit.services.telop.is_codex_available", return_value=False):
        with pytest.raises(CodexNotFoundError) as error:
            generate_telop_script("video123", [_segment()], settings)
    message = str(error.value)
    assert "npm install -g @openai/codex" in message
    assert "codex login" in message
    assert "prompt_telop_" in message
    assert "telop_" in message
    assert "インストール手順" in CODEX_INSTALL_HINT


def test_generation_invokes_codex_once_and_saves_valid_document(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    raw = json.dumps(_valid_document(), ensure_ascii=False)
    with (
        patch("yt_live_kit.services.telop.is_codex_available", return_value=True),
        patch("yt_live_kit.services.telop.invoke_codex", return_value=raw) as invoke,
    ):
        result = generate_telop_script("video123", _segments(), settings)
    invoke.assert_called_once()
    assert result.used_codex is True
    assert result.script_path is not None and result.script_path.is_file()
    saved = json.loads(result.script_path.read_text(encoding="utf-8"))
    assert saved["hook_text"] == "大事なポイント"
    assert result.document is not None


def test_codex_failure_preserves_existing_script_and_other_outputs(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    segments = [_segment()]
    clip_id = make_clip_id(segments)
    telop_dir = tmp_path / "video123" / "shorts" / "telop"
    telop_dir.mkdir(parents=True)
    existing = telop_dir / f"telop_{clip_id}.json"
    existing.write_text("既存台本", encoding="utf-8")
    chapter = tmp_path / "video123" / "chapters" / "chapters.md"
    chapter.parent.mkdir()
    chapter.write_text("既存チャプター", encoding="utf-8")
    candidate = tmp_path / "video123" / "clips" / "candidates.json"
    candidate.parent.mkdir()
    candidate.write_text("既存候補", encoding="utf-8")
    highlight = tmp_path / "video123" / "highlights" / "segments.json"
    highlight.parent.mkdir()
    highlight.write_text("既存ハイライト", encoding="utf-8")

    with (
        patch("yt_live_kit.services.telop.is_codex_available", return_value=True),
        patch(
            "yt_live_kit.services.telop.invoke_codex",
            side_effect=AiPromptError("Codex CLI の実行に失敗しました。"),
        ),
    ):
        with pytest.raises(AiPromptError):
            generate_telop_script("video123", segments, settings)

    assert existing.read_text(encoding="utf-8") == "既存台本"
    assert chapter.read_text(encoding="utf-8") == "既存チャプター"
    assert candidate.read_text(encoding="utf-8") == "既存候補"
    assert highlight.read_text(encoding="utf-8") == "既存ハイライト"


def test_validation_failure_preserves_existing_script(tmp_path: Path):
    settings = _prepare_video(tmp_path)
    segments = [_segment()]
    clip_id = make_clip_id(segments)
    existing = tmp_path / "video123" / "shorts" / "telop" / f"telop_{clip_id}.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("既存台本", encoding="utf-8")
    invalid = json.dumps({"hook_text": "不足"}, ensure_ascii=False)

    with (
        patch("yt_live_kit.services.telop.is_codex_available", return_value=True),
        patch("yt_live_kit.services.telop.invoke_codex", return_value=invalid),
    ):
        with pytest.raises(TelopValidationError):
            generate_telop_script("video123", segments, settings)

    assert existing.read_text(encoding="utf-8") == "既存台本"
