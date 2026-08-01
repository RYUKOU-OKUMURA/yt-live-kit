"""ui/components/short_cut.py（FR-30）のヘルパー関数テスト."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.short_cut import ShortCutDocument
from yt_live_kit.services.short_cut import validate_short_cut_selection
from yt_live_kit.services.subtitle_burn import TimedCue, parse_vtt_with_end
from yt_live_kit.ui.components.short_cut import (
    LAYOUT_BLUR_LABEL,
    LAYOUT_CROP_LABEL,
    build_disabled_message,
    build_short_cut_job_target,
    checkbox_key,
    collect_edited_segments,
    collect_parent_options,
    end_key,
    extract_segment_text,
    format_total_ms,
    layout_from_label,
    load_transcript_cues,
    parse_cut_timestamp,
    resolve_transcript_bounds,
    segments_to_pairs,
    short_cut_output_path,
    start_key,
    suggest_short_cut_job_target,
)


def _clip(
    id: str = "clip_002",
    *,
    start: str = "00:39:00",
    end: str = "00:50:00",
    duration_sec: int = 660,
) -> ClipCandidate:
    return ClipCandidate(
        id=id,
        title="比較",
        start=start,
        end=end,
        duration_sec=duration_sec,
        reason="理由",
    )


def _document() -> ShortCutDocument:
    return ShortCutDocument(
        parent_id="clip_002",
        parent_start_ms=2_340_000,
        parent_end_ms=3_000_000,
        candidates=[
            HighlightSegment(
                id="cut_001",
                title="結論",
                start="00:39:10",
                end="00:40:00",
                duration_sec=50,
                reason="理由 1",
            ),
            HighlightSegment(
                id="cut_002",
                title="具体例",
                start="00:41:00",
                end="00:42:00",
                duration_sec=60,
                reason="理由 2",
            ),
        ],
    )


def test_collect_parent_options_keeps_only_long_candidates() -> None:
    clips = [
        _clip("clip_001"),
        _clip("clip_short", start="00:00:00", end="00:02:00", duration_sec=120),
    ]
    highlights = [
        HighlightSegment(
            id="hl_001",
            title="山場",
            start="00:05:00",
            end="00:11:00",
            duration_sec=360,
            reason="理由",
        ),
        HighlightSegment(
            id="hl_short",
            title="短い山場",
            start="00:20:00",
            end="00:21:00",
            duration_sec=60,
            reason="理由",
        ),
    ]

    options = collect_parent_options(clips, highlights)

    assert [option.id for option in options] == ["clip_001", "hl_001"]
    assert options[0].label.startswith("[切り抜き] clip_001: 比較")
    assert options[1].label.startswith("[ハイライト] hl_001: 山場")


def test_collect_parent_options_empty_when_all_short() -> None:
    clips = [_clip("clip_short", start="00:00:00", end="00:03:00", duration_sec=180)]
    assert collect_parent_options(clips, []) == []


def test_layout_from_label() -> None:
    assert layout_from_label(LAYOUT_BLUR_LABEL) == "blur"
    assert layout_from_label(LAYOUT_CROP_LABEL) == "crop"
    assert layout_from_label("未知") == "blur"


def test_parse_cut_timestamp() -> None:
    assert parse_cut_timestamp("00:39:10") == (2350.0, None)
    seconds, error = parse_cut_timestamp("39:10")
    assert seconds is None
    assert error == "時刻は HH:MM:SS の形式で入力してください。"


def test_extract_segment_text_matches_exact_boundaries() -> None:
    cues = [
        TimedCue(0.0, 1.0, "前"),
        TimedCue(1.0, 2.0, "対象"),
        TimedCue(2.0, 3.0, "後"),
    ]

    assert extract_segment_text(cues, 1.0, 2.0) == "対象"


def test_extract_segment_text_includes_partially_overlapping_cues() -> None:
    cues = [
        TimedCue(0.0, 1.0, "前半"),
        TimedCue(1.0, 2.0, "後半"),
    ]

    assert extract_segment_text(cues, 0.5, 1.5) == "前半\n後半"


def test_extract_segment_text_returns_empty_for_empty_segment() -> None:
    cues = [TimedCue(0.0, 1.0, "対象外")]

    assert extract_segment_text(cues, 2.0, 3.0) == ""
    assert extract_segment_text(cues, 1.0, 1.0) == ""


def test_extract_segment_text_merges_only_consecutive_exact_duplicates() -> None:
    cues = [
        TimedCue(0.0, 1.0, "同じ文"),
        TimedCue(1.0, 2.0, "同じ文"),
        TimedCue(2.0, 3.0, "別の文"),
        TimedCue(3.0, 4.0, "同じ文"),
    ]

    assert extract_segment_text(cues, 0.0, 4.0) == "同じ文\n別の文\n同じ文"


def test_extract_segment_text_follows_changed_boundaries() -> None:
    cues = [
        TimedCue(0.0, 1.0, "最初"),
        TimedCue(1.0, 2.0, "中央"),
        TimedCue(2.0, 3.0, "最後"),
    ]

    original = extract_segment_text(cues, 0.0, 2.0)
    changed = extract_segment_text(cues, 1.0, 3.0)

    assert original == "最初\n中央"
    assert changed == "中央\n最後"
    assert changed != original


def test_resolve_transcript_bounds_falls_back_for_invalid_input() -> None:
    assert resolve_transcript_bounds(
        "入力中", "00:00:20", "00:00:10", "00:00:30"
    ) == (10.0, 30.0, True)
    assert resolve_transcript_bounds(
        "00:00:25", "00:00:20", "00:00:10", "00:00:30"
    ) == (10.0, 30.0, True)
    assert resolve_transcript_bounds(
        "00:00:12", "00:00:22", "00:00:10", "00:00:30"
    ) == (12.0, 22.0, False)


def test_load_transcript_cues_missing_vtt_returns_japanese_notice(
    tmp_path: Path,
) -> None:
    load_transcript_cues.clear()

    cues, notice = load_transcript_cues("missing-video", tmp_path)

    assert cues == ()
    assert notice is not None
    assert "文字起こしファイルが見つからない" in notice


def test_load_transcript_cues_parses_once_per_video_across_reruns(
    tmp_path: Path,
) -> None:
    vtt_path = tmp_path / "video-a" / "subtitles" / "ja.vtt"
    vtt_path.parent.mkdir(parents=True)
    vtt_path.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n本文\n",
        encoding="utf-8",
    )
    load_transcript_cues.clear()
    with patch(
        "yt_live_kit.ui.components.short_cut.parse_vtt_with_end",
        wraps=parse_vtt_with_end,
    ) as parse:
        first = load_transcript_cues("video-a", tmp_path)
        second = load_transcript_cues("video-a", tmp_path)

    assert first == second
    assert first[0] == (TimedCue(0.0, 1.0, "本文"),)
    assert first[1] is None
    parse.assert_called_once()
    load_transcript_cues.clear()


def test_format_total_ms() -> None:
    assert format_total_ms(110_000) == "110.0 秒"
    assert format_total_ms(0) == "0.0 秒"


def test_collect_edited_segments_defaults_to_all_adopted() -> None:
    segments, errors = collect_edited_segments(_document(), "vid", {})
    assert errors == []
    assert [segment.id for segment in segments] == ["cut_001", "cut_002"]
    assert validate_short_cut_selection(segments, parent=_clip()).total_ms == 110_000


def test_collect_edited_segments_honours_unchecked_and_edited_values() -> None:
    state = {
        checkbox_key("vid", "cut_002"): False,
        start_key("vid", "cut_001"): "00:39:20",
        end_key("vid", "cut_001"): "00:39:50",
    }
    segments, errors = collect_edited_segments(_document(), "vid", state)

    assert errors == []
    assert len(segments) == 1
    assert (segments[0].start, segments[0].end, segments[0].duration_sec) == (
        "00:39:20",
        "00:39:50",
        30,
    )


def test_collect_edited_segments_reports_bad_timestamp_and_order() -> None:
    state = {
        start_key("vid", "cut_001"): "39:20",
        start_key("vid", "cut_002"): "00:42:00",
        end_key("vid", "cut_002"): "00:41:00",
    }
    segments, errors = collect_edited_segments(_document(), "vid", state)

    assert segments == []
    assert any("cut_001: 開始時刻は HH:MM:SS" in error for error in errors)
    assert any("cut_002: 終了時刻は開始時刻より後" in error for error in errors)


def test_segments_to_pairs_and_output_path_are_deterministic(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    segments = _document().candidates

    assert segments_to_pairs(segments) == [(2350.0, 2400.0), (2460.0, 2520.0)]

    first = short_cut_output_path("vid", segments, settings)
    second = short_cut_output_path("vid", segments, settings)
    assert first == second
    assert first.parent == tmp_path / "vid" / "shorts" / "output"
    assert first.name.startswith("short_") and first.suffix == ".mp4"


def test_build_disabled_message_prefers_parse_errors() -> None:
    validation = validate_short_cut_selection([], parent=_clip())
    assert build_disabled_message(validation, ["時刻が不正です"]) == "時刻が不正です"
    assert build_disabled_message(validation, []) is not None

    ok = validate_short_cut_selection(_document().candidates, parent=_clip())
    assert build_disabled_message(ok, []) is None


def test_suggest_job_target_rebuilds_parent_by_type() -> None:
    settings = MagicMock()
    clip = _clip()

    with patch(
        "yt_live_kit.ui.components.short_cut.suggest_short_cuts"
    ) as suggest:
        suggest_short_cut_job_target(
            report=MagicMock(),
            settings=settings,
            video_id="vid",
            parent_dict=clip.model_dump(mode="json"),
            parent_is_clip=True,
        )

    args, kwargs = suggest.call_args
    assert args[0] == "vid"
    assert isinstance(args[1], ClipCandidate)
    assert args[1].id == "clip_002"

    with patch(
        "yt_live_kit.ui.components.short_cut.suggest_short_cuts"
    ) as suggest_highlight:
        suggest_short_cut_job_target(
            report=MagicMock(),
            settings=settings,
            video_id="vid",
            parent_dict=_document().candidates[0].model_dump(mode="json"),
            parent_is_clip=False,
        )

    args, _kwargs = suggest_highlight.call_args
    assert isinstance(args[1], HighlightSegment)


def test_build_job_target_calls_concat_builder_and_writes_meta(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.ffmpeg_path = "ffmpeg"
    output_path = tmp_path / "short_abc.mp4"
    output_path.write_bytes(b"x")
    result = MagicMock()
    result.video_id = "vid"
    result.output_path = output_path
    result.command_log_path = tmp_path / "short_abc.ffmpeg.log"
    result.layout = "blur"
    result.burned_subtitles = True
    result.duration_sec = 110.0
    result.font_warning = None

    with patch(
        "yt_live_kit.ui.components.short_cut.build_short_from_segments",
        return_value=result,
    ) as build:
        build_short_cut_job_target(
            report=MagicMock(),
            settings=settings,
            video_id="vid",
            segment_pairs=[[2350.0, 2400.0], [2460.0, 2520.0]],
            layout="blur",
        )

    args, kwargs = build.call_args
    assert args[0] == "vid"
    assert args[1] == [(2350.0, 2400.0), (2460.0, 2520.0)]
    assert kwargs["layout"] == "blur"

    meta = json.loads((tmp_path / "short_abc.meta.json").read_text(encoding="utf-8"))
    assert meta["duration_sec"] == 110.0
    assert meta["output_path"] == str(output_path)
