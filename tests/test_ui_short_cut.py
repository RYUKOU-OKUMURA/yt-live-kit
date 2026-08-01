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
from yt_live_kit.ui.components.short_cut import (
    LAYOUT_BLUR_LABEL,
    LAYOUT_CROP_LABEL,
    build_disabled_message,
    build_short_cut_job_target,
    checkbox_key,
    collect_edited_segments,
    collect_parent_options,
    end_key,
    format_total_ms,
    layout_from_label,
    parse_cut_timestamp,
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
