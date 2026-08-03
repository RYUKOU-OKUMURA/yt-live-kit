"""ui/components/short_cut.py（FR-30）のヘルパー関数テスト."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.short_cut import ShortCutDocument
from yt_live_kit.models.transcript import TranscriptCue, TranscriptRange
from yt_live_kit.services.short_cut import validate_short_cut_selection
from yt_live_kit.services.subtitle_burn import TimedCue, parse_vtt_with_end
from yt_live_kit.services.transcript_artifact import (
    TranscriptArtifactStore,
    build_transcript_artifact,
)
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
    load_transcript_cues_for_document,
    parse_cut_timestamp,
    resolve_transcript_bounds,
    render_short_cut_section,
    S9_WHISPER_PROGRESS_PREFIX,
    _render_refine_preview,
    segments_to_pairs,
    short_cut_output_path,
    shift_cut_timestamp,
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


def _high_precision_artifact(video_id: str = "video-1", *, suffix: str = "one"):
    return build_transcript_artifact(
        video_id=video_id,
        source_kind="whisper_cpp",
        source_ref=f"transcripts/audio/{suffix}.wav",
        language="ja",
        ranges=[TranscriptRange(start_ms=10_000, end_ms=20_000)],
        cues=[TranscriptCue(start_ms=11_000, end_ms=12_000, text="artifact cue")],
        audio_bytes=f"audio-{suffix}".encode(),
        model={"name": "fixed-model", "fingerprint": "a" * 64},
        runtime={"version": "1.9.1", "fingerprint": "b" * 64},
        settings={"language": "ja", "padding_ms": 0},
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


def test_shift_cut_timestamp_assists_without_changing_parse_boundary() -> None:
    assert shift_cut_timestamp("00:39:10", -5) == ("00:39:05", None)
    assert shift_cut_timestamp("00:39:10", 5) == ("00:39:15", None)
    shifted, error = shift_cut_timestamp("00:00:02", -5)
    assert shifted is None
    assert error is not None and "0 秒より前" in error


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


def test_load_transcript_cues_for_document_uses_same_artifact_without_vtt(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    artifact = _high_precision_artifact()
    store = TranscriptArtifactStore("video-1", settings)
    store.save(artifact)
    reference = store.artifact_ref(artifact)
    document = _document().model_copy(
        update={
            "artifact_ref": reference,
            "artifact_fingerprint": artifact.artifact_fingerprint,
            "used_range_cue_digests": artifact.used_range_cue_digests,
        }
    )

    with patch(
        "yt_live_kit.ui.components.short_cut.load_transcript_cues",
        side_effect=AssertionError("high precision path must not read ja.vtt"),
    ):
        cues, notice = load_transcript_cues_for_document("video-1", document, settings)

    assert cues == (TimedCue(11.0, 12.0, "artifact cue"),)
    assert notice is None


def test_load_transcript_cues_for_document_missing_artifact_fails_closed_without_vtt(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    artifact = _high_precision_artifact()
    store = TranscriptArtifactStore("video-1", settings)
    store.save(artifact)
    reference = store.artifact_ref(artifact)
    document = _document().model_copy(
        update={
            "artifact_ref": reference,
            "artifact_fingerprint": artifact.artifact_fingerprint,
            "used_range_cue_digests": artifact.used_range_cue_digests,
        }
    )
    store._artifact_path(artifact.artifact_fingerprint).unlink()

    with patch(
        "yt_live_kit.ui.components.short_cut.load_transcript_cues",
        side_effect=AssertionError("artifact failure must not fall back to ja.vtt"),
    ):
        cues, notice = load_transcript_cues_for_document("video-1", document, settings)

    assert cues == ()
    assert notice is not None
    assert "表示を停止" in notice


def test_load_transcript_cues_for_document_mismatched_artifact_fails_closed_without_vtt(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    artifact = _high_precision_artifact()
    other = _high_precision_artifact(suffix="two")
    store = TranscriptArtifactStore("video-1", settings)
    store.save(artifact)
    store.save(other)
    reference = store.artifact_ref(artifact)
    document = _document().model_copy(
        update={
            "artifact_ref": reference,
            "artifact_fingerprint": artifact.artifact_fingerprint,
            "used_range_cue_digests": artifact.used_range_cue_digests,
        }
    )
    store._artifact_path(artifact.artifact_fingerprint).write_text(
        other.model_dump_json(),
        encoding="utf-8",
    )

    with patch(
        "yt_live_kit.ui.components.short_cut.load_transcript_cues",
        side_effect=AssertionError("artifact mismatch must not fall back to ja.vtt"),
    ):
        cues, notice = load_transcript_cues_for_document("video-1", document, settings)

    assert cues == ()
    assert notice is not None
    assert "表示を停止" in notice


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


def test_refine_preview_does_not_start_without_explicit_submit(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    option = MagicMock(id="clip_002", candidate=_clip())
    segments = _document().candidates
    with (
        patch("yt_live_kit.ui.components.short_cut.st.form") as form,
        patch("yt_live_kit.ui.components.short_cut.st.form_submit_button", return_value=False) as submit,
        patch("yt_live_kit.ui.components.short_cut.st.markdown"),
        patch("yt_live_kit.ui.components.short_cut.st.caption") as caption,
        patch("yt_live_kit.ui.components.short_cut.st.info") as info,
        patch("yt_live_kit.ui.components.short_cut._start_refine") as start,
    ):
        form.return_value.__enter__.return_value = form.return_value

        _render_refine_preview(
            video_id="video-1",
            title="動画",
            option=option,
            segments=segments,
            settings=settings,
            busy=False,
            disabled_message=None,
        )

    submit.assert_called_once_with("高精度字幕を準備", type="primary", disabled=False)
    start.assert_not_called()
    assert any("対象区間: 2 件" in item.args[0] for item in caption.call_args_list)
    assert any("padding: 0 ms" in item.args[0] for item in caption.call_args_list)
    assert "上書き・削除しません" in info.call_args.args[0]


def test_refine_preview_starts_only_after_explicit_submit(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    option = MagicMock(id="clip_002", candidate=_clip())
    segments = _document().candidates
    with (
        patch("yt_live_kit.ui.components.short_cut.st.form") as form,
        patch("yt_live_kit.ui.components.short_cut.st.form_submit_button", return_value=True),
        patch("yt_live_kit.ui.components.short_cut.st.markdown"),
        patch("yt_live_kit.ui.components.short_cut.st.caption"),
        patch("yt_live_kit.ui.components.short_cut.st.info"),
        patch("yt_live_kit.ui.components.short_cut._start_refine") as start,
    ):
        form.return_value.__enter__.return_value = form.return_value

        _render_refine_preview(
            video_id="video-1",
            title="動画",
            option=option,
            segments=segments,
            settings=settings,
            busy=False,
            disabled_message=None,
        )

    start.assert_called_once_with(
        video_id="video-1",
        title="動画",
        option=option,
        segments=segments,
        settings=settings,
    )


def test_refine_preview_is_disabled_while_another_job_is_busy(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    option = MagicMock(id="clip_002", candidate=_clip())
    with (
        patch("yt_live_kit.ui.components.short_cut.st.form") as form,
        patch("yt_live_kit.ui.components.short_cut.st.form_submit_button", return_value=True) as submit,
        patch("yt_live_kit.ui.components.short_cut.st.markdown"),
        patch("yt_live_kit.ui.components.short_cut.st.caption"),
        patch("yt_live_kit.ui.components.short_cut.st.info"),
        patch("yt_live_kit.ui.components.short_cut._start_refine") as start,
    ):
        form.return_value.__enter__.return_value = form.return_value

        _render_refine_preview(
            video_id="video-1",
            title="動画",
            option=option,
            segments=_document().candidates,
            settings=settings,
            busy=True,
            disabled_message=None,
        )

    assert submit.call_args.kwargs["disabled"] is True
    start.assert_not_called()


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


def test_refine_job_target_bridges_job_range_progress_without_streamlit(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    segments = _document().candidates
    progress_events = [
        MagicMock(
            job_id="whisper-job-1",
            range_index=1,
            range_total=2,
            status="audio_preparing",
            cache_hit=False,
            retryable=None,
            diagnostic="音声を準備しています。",
        ),
        MagicMock(
            job_id="whisper-job-1",
            range_index=2,
            range_total=2,
            status="success",
            cache_hit=True,
            retryable=False,
            diagnostic=None,
        ),
    ]

    def run_refine(*args, **kwargs):
        for event in progress_events:
            kwargs["on_progress"](event)

    report = MagicMock()
    with patch(
        "yt_live_kit.ui.components.short_cut.refine_selected_short_cut",
        side_effect=run_refine,
    ) as refine:
        from yt_live_kit.ui.components.short_cut import refine_short_cut_job_target

        refine_short_cut_job_target(
            report=report,
            settings=settings,
            video_id="video-1",
            parent_dict=_clip().model_dump(mode="json"),
            parent_is_clip=True,
            segment_dicts=[segment.model_dump(mode="json") for segment in segments],
            job_id="job-1",
        )

    refine.assert_called_once()
    assert report.call_args_list[0].kwargs == {
        "stage": "capability",
        "message": report.call_args_list[0].kwargs["message"],
        "current": 0,
        "total": 2,
    }
    assert report.call_args_list[1].kwargs["stage"] == "audio"
    progress_call = next(
        call
        for call in reversed(report.call_args_list)
        if call.kwargs["message"].startswith(S9_WHISPER_PROGRESS_PREFIX)
    )
    payload = json.loads(
        progress_call.kwargs["message"][len(S9_WHISPER_PROGRESS_PREFIX) :]
    )
    assert payload["job_id"] == "whisper-job-1"
    assert payload["stage"] == "artifact"
    assert payload["range_index"] == 2
    assert payload["range_total"] == 2
    assert payload["cache_hit"] is True
    assert payload["status"] == "success"
    assert payload["current_range"] == {
        "id": "cut_002",
        "start": "00:41:00",
        "end": "00:42:00",
    }
    assert report.call_args_list[-1].kwargs["current"] == 2
    assert report.call_args_list[-1].kwargs["total"] == 2


def test_render_section_reloads_saved_cutplan_after_job_rerun(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    option = MagicMock()
    option.id = "clip_002"
    option.label = "切り抜き候補"
    option.candidate = _clip()
    document = _document()
    session_state: dict[str, object] = {}
    with (
        patch(
            "yt_live_kit.ui.components.short_cut.collect_parent_options",
            return_value=[option],
        ),
        patch(
            "yt_live_kit.ui.components.short_cut.load_cut_plan",
            return_value=document,
        ) as load,
        patch("yt_live_kit.ui.components.short_cut._render_plan") as render_plan,
        patch("yt_live_kit.ui.components.short_cut.is_busy", return_value=False),
        patch("yt_live_kit.ui.components.short_cut.st.session_state", session_state),
        patch("yt_live_kit.ui.components.short_cut.st.caption"),
        patch("yt_live_kit.ui.components.short_cut.st.radio", return_value=0),
        patch("yt_live_kit.ui.components.short_cut.st.button", return_value=False),
    ):
        render_short_cut_section(
            video_id="video-1",
            title="動画",
            clip_candidates=(_clip(),),
            highlight_candidates=(),
            settings=settings,
            embedded=True,
        )

    load.assert_called_once_with("video-1", "clip_002", settings)
    render_plan.assert_called_once()
