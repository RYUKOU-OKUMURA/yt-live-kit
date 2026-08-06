"""subtitle_burn サービスのユニットテスト."""

from dataclasses import fields, replace
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services import subtitle_burn
from yt_live_kit.services.subtitle_burn import (
    TELOP_PRESETS,
    SubtitleBurnError,
    TelopPreset,
    TimedCue,
    build_concatenated_subtitle,
    build_segment_subtitle,
    filter_cues_for_segment,
    get_telop_preset,
    is_japanese_font_available,
    resolve_font,
    write_ass,
    write_hook_ass,
)
from yt_live_kit.services.vtt_parser import Cue, deduplicate_progressive
from yt_live_kit.services.telop import TelopValidationResult, make_clip_id

# YouTube 自動字幕によくある「前の行 + 新しい語」が積み上がるプログレッシブ形式。
PROGRESSIVE_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
こんにちは

2
00:00:04.000 --> 00:00:07.000
こんにちは 今日は

3
00:00:07.000 --> 00:00:10.000
こんにちは 今日は 配信です
"""

SAMPLE_VTT = """WEBVTT

1
00:00:05.000 --> 00:00:08.000
区間前の字幕

2
00:00:10.000 --> 00:00:15.000
区間内の字幕

3
00:00:18.000 --> 00:00:22.000
またぎ字幕

4
00:00:25.000 --> 00:00:30.000
区間後の字幕
"""


def _parse_sample() -> list[TimedCue]:
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    return _parse_vtt_with_end(SAMPLE_VTT)


def test_filter_cues_keeps_only_overlapping():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 12.0, 20.0)

    texts = [cue.text for cue in filtered]
    assert "区間前の字幕" not in texts
    assert "区間内の字幕" in texts
    assert "またぎ字幕" in texts
    assert "区間後の字幕" not in texts


def test_filter_cues_before_segment():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 30.0, 45.0)
    assert filtered == []


def test_filter_cues_after_segment():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 0.0, 4.0)
    assert filtered == []


def test_filter_cues_spanning_start():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 20.0, 24.0)
    assert len(filtered) == 1
    assert filtered[0].text == "またぎ字幕"
    assert filtered[0].start_seconds == 0.0
    assert filtered[0].end_seconds == pytest.approx(2.0)


def test_filter_cues_time_offset_subtracted():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 10.0, 20.0)

    inner = next(cue for cue in filtered if cue.text == "区間内の字幕")
    assert inner.start_seconds == pytest.approx(0.0)
    assert inner.end_seconds == pytest.approx(5.0)


def test_filter_cues_never_negative_start():
    cues = _parse_sample()
    filtered = filter_cues_for_segment(cues, 19.0, 24.0)

    for cue in filtered:
        assert cue.start_seconds >= 0.0
        assert cue.end_seconds >= cue.start_seconds


def test_write_ass_header_and_dialogue(tmp_path):
    cues = [
        TimedCue(start_seconds=1.5, end_seconds=4.25, text="テスト字幕"),
    ]
    output = tmp_path / "test.ass"
    write_ass(cues, output, font_name="Hiragino Sans")

    content = output.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Format: Layer, Start, End" in content
    assert "Dialogue: 0,0:00:01.50,0:00:04.25,Default,,0,0,0,,テスト字幕" in content


def test_build_segment_subtitle_writes_ass(tmp_path):
    video_id = "vid123"
    vtt_dir = tmp_path / video_id / "subtitles"
    vtt_dir.mkdir(parents=True)
    (vtt_dir / "ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")

    from yt_live_kit.config import Settings

    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.subtitle_burn.resolve_font",
        return_value="Hiragino Sans",
    ):
        ass_path = build_segment_subtitle(video_id, 10.0, 20.0, settings)

    assert ass_path.is_file()
    assert ass_path.name == "short_10_20.ass"
    content = ass_path.read_text(encoding="utf-8")
    assert "区間内の字幕" in content
    assert "区間前の字幕" not in content


@patch("yt_live_kit.services.subtitle_burn._font_exists")
def test_resolve_font_prefers_preferred(mock_exists):
    mock_exists.return_value = True
    assert resolve_font("My Font") == "My Font"
    mock_exists.assert_called_once_with("My Font")


@patch("yt_live_kit.services.subtitle_burn._font_exists")
def test_resolve_font_fallback_order(mock_exists):
    def side_effect(name: str) -> bool:
        return name == "Noto Sans CJK JP"

    mock_exists.side_effect = side_effect
    assert resolve_font(None) == "Noto Sans CJK JP"
    assert mock_exists.call_args_list[0][0][0] == "Hiragino Sans"
    assert mock_exists.call_args_list[1][0][0] == "Noto Sans CJK JP"


@patch("yt_live_kit.services.subtitle_burn._font_exists", return_value=False)
def test_resolve_font_sans_serif_last(mock_exists):
    assert resolve_font(None) == "sans-serif"


@patch("yt_live_kit.services.subtitle_burn._font_exists", return_value=False)
def test_is_japanese_font_available_false_when_only_sans(mock_exists):
    assert is_japanese_font_available() is False


@patch("yt_live_kit.services.subtitle_burn._font_exists", return_value=True)
def test_is_japanese_font_available_true(mock_exists):
    assert is_japanese_font_available() is True


@patch("yt_live_kit.services.subtitle_burn.shutil.which")
@patch("yt_live_kit.services.subtitle_burn.subprocess.run")
def test_font_detection_via_fc_list(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/fc-list"
    mock_run.return_value = MagicMock(returncode=0, stdout="Hiragino Sans\n")

    from yt_live_kit.services.subtitle_burn import _font_available_via_fc_list

    assert _font_available_via_fc_list("Hiragino Sans") is True
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("timeout") == subtitle_burn._FC_LIST_TIMEOUT_SEC


@patch("yt_live_kit.services.subtitle_burn.shutil.which")
@patch("yt_live_kit.services.subtitle_burn.subprocess.run")
def test_font_detection_via_fc_list_timeout_returns_false(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/fc-list"
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["fc-list"], timeout=60)

    from yt_live_kit.services.subtitle_burn import _font_available_via_fc_list

    assert _font_available_via_fc_list("Hiragino Sans") is False


# --- 修正1: PlayResX / PlayResY ---------------------------------------------


def test_write_ass_includes_play_res(tmp_path):
    """PlayRes が無いと libass は 384x288 既定で約 6.7 倍にスケールされ字幕が巨大化する."""
    cues = [TimedCue(start_seconds=0.0, end_seconds=2.0, text="テスト")]
    output = tmp_path / "test.ass"
    write_ass(cues, output, font_name="Hiragino Sans")

    content = output.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content


def test_write_ass_play_res_overridable(tmp_path):
    """将来 1080x1920 以外に焼く場合に備え、引数で上書きできる."""
    cues = [TimedCue(start_seconds=0.0, end_seconds=2.0, text="テスト")]
    output = tmp_path / "test.ass"
    write_ass(cues, output, font_name="Hiragino Sans", play_res_x=720, play_res_y=1280)

    content = output.read_text(encoding="utf-8")
    assert "PlayResX: 720" in content
    assert "PlayResY: 1280" in content


# --- 修正2: プログレッシブ重複除去 ------------------------------------------


def test_parse_vtt_with_end_removes_progressive_duplicates():
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    cues = _parse_vtt_with_end(PROGRESSIVE_VTT)
    texts = [cue.text for cue in cues]

    assert texts == ["こんにちは", "今日は", "配信です"]
    # 同じ語が繰り返し現れない
    assert " ".join(texts).count("こんにちは") == 1


def test_parse_vtt_with_end_preserves_end_seconds_after_dedup():
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    cues = _parse_vtt_with_end(PROGRESSIVE_VTT)

    assert [cue.end_seconds for cue in cues] == pytest.approx([4.0, 7.0, 10.0])


def test_deduplicate_progressive_timed_matches_vtt_parser():
    """subtitle_burn 独自の除去関数が vtt_parser.deduplicate_progressive と同じ text 列を返す."""
    starts_texts = [
        (1.0, "こんにちは"),
        (4.0, "こんにちは 今日は"),
        (7.0, "こんにちは 今日は 配信です"),
        (10.0, "こんにちは 今日は 配信です"),  # 完全一致重複
        (13.0, "配信です 今日は 配信です"),  # 部分文字列一致（末尾以外）
    ]

    plain_cues = [Cue(start_seconds=s, text=t) for s, t in starts_texts]
    timed_cues = [
        TimedCue(start_seconds=s, end_seconds=s + 3.0, text=t) for s, t in starts_texts
    ]

    expected_texts = [cue.text for cue in deduplicate_progressive(plain_cues)]
    actual_texts = [
        cue.text for cue in subtitle_burn._deduplicate_progressive_timed(timed_cues)
    ]

    assert actual_texts == expected_texts


def test_deduplicate_progressive_timed_keeps_end_seconds():
    timed_cues = [
        TimedCue(start_seconds=1.0, end_seconds=4.0, text="こんにちは"),
        TimedCue(start_seconds=4.0, end_seconds=7.0, text="こんにちは 今日は"),
    ]

    result = subtitle_burn._deduplicate_progressive_timed(timed_cues)

    assert [cue.end_seconds for cue in result] == pytest.approx([4.0, 7.0])


def test_dedup_applied_before_segment_filter():
    """区間の直前のキューとの重複も取れるよう、絞り込み前に全体へ除去を適用する."""
    from yt_live_kit.services.subtitle_burn import _parse_vtt_with_end

    vtt = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
こんにちは

2
00:00:04.000 --> 00:00:07.000
こんにちは 今日は
"""
    cues = _parse_vtt_with_end(vtt)
    segment_cues = filter_cues_for_segment(cues, 4.0, 7.0)

    assert [cue.text for cue in segment_cues] == ["今日は"]


# --- 修正4: フォント検出のマッチが緩い --------------------------------------


def test_font_dir_no_false_positive_for_short_filename(tmp_path):
    """"sans.ttf" のような短いファイル名が "Hiragino Sans" に誤検出しない."""
    (tmp_path / "sans.ttf").write_bytes(b"")

    with patch.object(subtitle_burn, "_MAC_FONT_DIRS", (tmp_path,)):
        assert subtitle_burn._font_available_in_mac_dirs("Hiragino Sans") is False


def test_font_dir_detects_japanese_hiragino_filename(tmp_path):
    """実ファイル名が日本語（ヒラギノ角ゴシック W3.ttc）でも検出できる."""
    (tmp_path / "ヒラギノ角ゴシック W3.ttc").write_bytes(b"")

    with patch.object(subtitle_burn, "_MAC_FONT_DIRS", (tmp_path,)):
        assert subtitle_burn._font_available_in_mac_dirs("Hiragino Sans") is True


def test_font_dir_returns_english_family_name_not_japanese(tmp_path):
    """検出できても返す値は英語ファミリ名のまま（force_style に渡すため）."""
    (tmp_path / "ヒラギノ角ゴシック W3.ttc").write_bytes(b"")

    with patch.object(subtitle_burn, "_MAC_FONT_DIRS", (tmp_path,)):
        with patch.object(subtitle_burn, "_font_available_via_fc_list", return_value=False):
            assert resolve_font(None) == "Hiragino Sans"


# --- S2: テロップスタイルプリセット + フックタイトル ------------------------


def test_timed_cue_three_argument_compatibility_and_emphasis_propagation():
    cue = TimedCue(10.0, 15.0, "強調字幕")
    assert cue.emphasis is False

    emphasized = TimedCue(10.0, 15.0, "強調字幕", emphasis=True)
    filtered = filter_cues_for_segment([emphasized], 12.0, 14.0)
    assert filtered == [TimedCue(0.0, 2.0, "強調字幕", emphasis=True)]

    progressive = [
        TimedCue(0.0, 1.0, "前", emphasis=False),
        TimedCue(1.0, 2.0, "前 続き", emphasis=True),
    ]
    deduplicated = subtitle_burn._deduplicate_progressive_timed(progressive)
    assert deduplicated[1] == TimedCue(1.0, 2.0, "続き", emphasis=True)


def test_telop_presets_have_complete_fields_valid_colours_and_expected_borders():
    expected_fields = {field.name for field in fields(TelopPreset)}
    assert set(TELOP_PRESETS) == {"default", "bold_outline", "boxed", "hook"}
    assert TELOP_PRESETS["default"].border_style == 1
    assert TELOP_PRESETS["bold_outline"].bold is True
    assert TELOP_PRESETS["bold_outline"].outline > TELOP_PRESETS["default"].outline
    assert TELOP_PRESETS["boxed"].border_style == 3
    assert TELOP_PRESETS["hook"].font_size > 54

    for preset in TELOP_PRESETS.values():
        assert set(preset.__dataclass_fields__) == expected_fields
        for field_name in (
            "primary_colour",
            "secondary_colour",
            "outline_colour",
            "back_colour",
            "emphasis_colour",
        ):
            assert subtitle_burn._ASS_STYLE_COLOUR_RE.fullmatch(
                getattr(preset, field_name)
            )


def test_default_style_and_hook_free_output_are_v2_compatible(tmp_path):
    output = tmp_path / "default.ass"
    write_ass([TimedCue(0, 1, "本文")], output, font_name="Hiragino Sans")
    content = output.read_text(encoding="utf-8")

    assert (
        "Style: Default,Hiragino Sans,54,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,180,1"
    ) in content
    assert "Style: Hook" not in content
    assert ",Hook,," not in content


@pytest.mark.parametrize("preset_name", ["bold_outline", "boxed", "hook"])
def test_additional_preset_style_differs_from_default(tmp_path, preset_name):
    output = tmp_path / f"{preset_name}.ass"
    write_ass([], output, font_name="Test Font", preset=preset_name)
    content = output.read_text(encoding="utf-8")
    default_style = subtitle_burn._style_line(
        "Default", "Test Font", TELOP_PRESETS["default"]
    )
    selected_style = subtitle_burn._style_line(
        "Default", "Test Font", TELOP_PRESETS[preset_name]
    )
    assert selected_style in content
    assert selected_style != default_style


def test_unknown_presets_are_japanese_errors_with_available_names(tmp_path):
    with pytest.raises(SubtitleBurnError, match="利用可能: default、bold_outline、boxed、hook"):
        write_ass([], tmp_path / "unknown.ass", font_name="Font", preset="missing")
    with pytest.raises(SubtitleBurnError, match="利用可能: default、bold_outline、boxed、hook"):
        write_ass(
            [],
            tmp_path / "unknown_hook.ass",
            font_name="Font",
            hook_text="フック",
            hook_preset="missing",
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "primary_colour",
        "secondary_colour",
        "outline_colour",
        "back_colour",
        "emphasis_colour",
    ],
)
def test_invalid_preset_colour_is_japanese_error_and_does_not_create_file(
    tmp_path, monkeypatch, field_name
):
    bad = replace(TELOP_PRESETS["default"], **{field_name: "&H123456"})
    monkeypatch.setitem(TELOP_PRESETS, "invalid", bad)
    output = tmp_path / "invalid.ass"

    with pytest.raises(SubtitleBurnError, match="色.*が不正"):
        write_ass([], output, font_name="Font", preset="invalid")
    assert not output.exists()


def test_emphasis_uses_selected_preset_colours_for_entire_sanitized_line(tmp_path):
    output = tmp_path / "emphasis.ass"
    cues = [
        TimedCue(0, 1, "通常"),
        TimedCue(1, 2, r"強調{\c&HFFFFFF&}", emphasis=True),
    ]
    write_ass(cues, output, font_name="Font", preset="boxed")
    content = output.read_text(encoding="utf-8")

    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,通常" in content
    assert "{\\c" not in content.split("Dialogue: 0,0:00:00.00", 1)[1].splitlines()[0]
    emphasis_colour = subtitle_burn._inline_colour(
        TELOP_PRESETS["boxed"].emphasis_colour
    )
    primary_colour = subtitle_burn._inline_colour(TELOP_PRESETS["boxed"].primary_colour)
    assert (
        f"{{\\c{emphasis_colour}}}強調｛＼c&HFFFFFF&｝"
        f"{{\\c{primary_colour}}}"
    ) in content


@pytest.mark.parametrize("target", ["cue", "hook"])
def test_user_ass_control_sequences_are_sanitized_without_physical_dialogue_injection(
    tmp_path, target
):
    unsafe = "一行目\\N{\\bord20}\r\n二行目\x00\x1f終端"
    output = tmp_path / f"safe_{target}.ass"
    if target == "cue":
        write_ass([TimedCue(0, 1, unsafe)], output, font_name="Font")
    else:
        write_hook_ass(unsafe, output, font_name="Font")
    content = output.read_text(encoding="utf-8")
    dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]

    assert len(dialogue_lines) == 1
    assert "＼N｛＼bord20｝\\N二行目  終端" in dialogue_lines[0]
    assert "{\\bord20}" not in dialogue_lines[0]


def test_write_hook_ass_has_fixed_timing_resolution_and_large_font(tmp_path):
    output = tmp_path / "hook.ass"
    write_hook_ass("冒頭フック", output, font_name="Font")
    content = output.read_text(encoding="utf-8")

    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Hook,Font,88," in content
    assert "Dialogue: 1,0:00:00.00,0:00:02.00,Hook,,0,0,0,,冒頭フック" in content


@pytest.mark.parametrize("writer", ["hook_only", "combined"])
def test_blank_hook_is_rejected_before_output_creation(tmp_path, writer):
    output = tmp_path / f"{writer}.ass"
    with pytest.raises(SubtitleBurnError, match="フックタイトルを入力"):
        if writer == "hook_only":
            write_hook_ass(" \t\r\n ", output, font_name="Font")
        else:
            write_ass([], output, font_name="Font", hook_text=" \t\r\n ")
    assert not output.exists()


def test_write_ass_combines_default_and_hook_styles_and_layers(tmp_path):
    output = tmp_path / "combined.ass"
    write_ass(
        [TimedCue(2, 3, "本文")],
        output,
        font_name="Font",
        preset="bold_outline",
        hook_text="フック",
        hook_preset="boxed",
    )
    content = output.read_text(encoding="utf-8")

    assert subtitle_burn._style_line(
        "Default", "Font", TELOP_PRESETS["bold_outline"]
    ) in content
    assert subtitle_burn._style_line("Hook", "Font", TELOP_PRESETS["boxed"]) in content
    assert "Dialogue: 0,0:00:02.00,0:00:03.00,Default" in content
    assert "Dialogue: 1,0:00:00.00,0:00:02.00,Hook" in content


# --- S3: 複数区間の連結字幕 -----------------------------------------------


def _s3_settings(tmp_path: Path, vtt: str = "WEBVTT\n"):
    from yt_live_kit.config import Settings

    subtitle_dir = tmp_path / "video" / "subtitles"
    subtitle_dir.mkdir(parents=True)
    (subtitle_dir / "ja.vtt").write_text(vtt, encoding="utf-8")
    return Settings(data_dir=tmp_path)


def test_get_telop_preset_is_public_and_validates_name():
    assert get_telop_preset("boxed") is TELOP_PRESETS["boxed"]
    with pytest.raises(SubtitleBurnError, match="利用可能"):
        get_telop_preset("missing")


def test_build_concatenated_subtitle_vtt_uses_cumulative_timeline(tmp_path):
    vtt = """WEBVTT

1
00:00:10.500 --> 00:00:11.500
一つ目

2
00:00:20.500 --> 00:00:21.500
二つ目

3
00:00:30.500 --> 00:00:31.500
三つ目
"""
    settings = _s3_settings(tmp_path, vtt)
    segments = [(10.0, 14.0), (20.0, 24.0), (30.0, 34.0)]

    def capture(cues, output_path, **kwargs):
        assert [(cue.start_seconds, cue.end_seconds) for cue in cues] == [
            (0.5, 1.5),
            (4.5, 5.5),
            (8.5, 9.5),
        ]
        assert [cue.text for cue in cues] == ["一つ目", "二つ目", "三つ目"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("ass", encoding="utf-8")
        return output_path

    with (
        patch(
            "yt_live_kit.services.subtitle_burn.write_ass", side_effect=capture
        ) as writer,
        patch(
            "yt_live_kit.services.subtitle_burn.parse_vtt_with_end",
            wraps=subtitle_burn.parse_vtt_with_end,
        ) as parser,
        patch("yt_live_kit.services.subtitle_burn.resolve_font", return_value="Font"),
    ):
        result = build_concatenated_subtitle("video", segments, settings)

    writer.assert_called_once()
    parser.assert_called_once()
    assert result.name == f"short_{make_clip_id(segments)}.ass"


def test_build_concatenated_subtitle_telop_clips_and_propagates_presets(tmp_path):
    settings = _s3_settings(tmp_path)
    segments = [(10.00049, 15.99951), (30.0, 34.0)]
    document = TelopScriptDocument.model_validate(
        {
            "hook_text": "台本フック",
            "title_candidates": ["題"],
            "description": "説明",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": 10.0,
                    "end_sec": 16.0,
                    "lines": [
                        {
                            "start_sec": 10.0,
                            "end_sec": 16.0,
                            "text": "一つ目",
                            "emphasis": True,
                        }
                    ],
                },
                {
                    "start_sec": 30.0,
                    "end_sec": 34.0,
                    "lines": [
                        {
                            "start_sec": 30.0,
                            "end_sec": 31.0,
                            "text": "二つ目",
                            "emphasis": False,
                        }
                    ],
                },
            ],
        }
    )
    with patch("yt_live_kit.services.subtitle_burn.write_ass") as writer:
        writer.side_effect = lambda cues, output_path, **kwargs: output_path
        build_concatenated_subtitle(
            "video",
            segments,
            settings,
            telop_script=document,
            preset="boxed",
            hook_preset="bold_outline",
        )

    cues = writer.call_args.args[0]
    assert [(cue.start_seconds, cue.end_seconds) for cue in cues] == [
        (0.0, 6.0),
        (6.0, 7.0),
    ]
    assert cues[0].emphasis is True
    assert writer.call_args.kwargs["hook_text"] == "台本フック"
    assert writer.call_args.kwargs["preset"] == "boxed"
    assert writer.call_args.kwargs["hook_preset"] == "bold_outline"


def test_build_concatenated_subtitle_explicit_hook_wins_over_document(tmp_path):
    settings = _s3_settings(tmp_path)
    document = TelopScriptDocument.model_validate(
        {
            "hook_text": "台本フック",
            "title_candidates": ["題"],
            "description": "説明",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 10.0,
                    "lines": [
                        {
                            "start_sec": 0.0,
                            "end_sec": 1.0,
                            "text": "本文",
                            "emphasis": False,
                        }
                    ],
                }
            ],
        }
    )
    with patch("yt_live_kit.services.subtitle_burn.write_ass") as writer:
        writer.side_effect = lambda cues, output_path, **kwargs: output_path
        build_concatenated_subtitle(
            "video",
            [(0.0, 10.0)],
            settings,
            telop_script=document,
            hook_text="明示フック",
        )
    assert writer.call_args.kwargs["hook_text"] == "明示フック"


def test_build_concatenated_subtitle_rejects_invalid_telop_document(tmp_path):
    settings = _s3_settings(tmp_path)
    document = TelopScriptDocument.model_validate(
        {
            "hook_text": "フック",
            "title_candidates": ["題"],
            "description": "説明",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 9.0,
                    "lines": [
                        {
                            "start_sec": 0.0,
                            "end_sec": 1.0,
                            "text": "本文",
                            "emphasis": False,
                        }
                    ],
                }
            ],
        }
    )
    with pytest.raises(SubtitleBurnError, match="入力区間"):
        build_concatenated_subtitle(
            "video", [(0.0, 10.0)], settings, telop_script=document
        )


def test_build_concatenated_subtitle_rejects_line_invalid_after_defensive_clip(
    tmp_path,
):
    settings = _s3_settings(tmp_path)
    document = TelopScriptDocument.model_validate(
        {
            "hook_text": "フック",
            "title_candidates": ["題"],
            "description": "説明",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 10.0,
                    "lines": [
                        {
                            "start_sec": 11.0,
                            "end_sec": 12.0,
                            "text": "範囲外",
                            "emphasis": False,
                        }
                    ],
                }
            ],
        }
    )
    forced_validation = TelopValidationResult(
        ok=True,
        errors=(),
        warnings=(),
        document=document,
    )
    with patch(
        "yt_live_kit.services.telop.validate_telop_script",
        return_value=forced_validation,
    ):
        with pytest.raises(SubtitleBurnError, match="補正した結果"):
            build_concatenated_subtitle(
                "video", [(0.0, 10.0)], settings, telop_script=document
            )


def test_build_concatenated_subtitle_allows_empty_cues_and_hook_only(tmp_path):
    settings = _s3_settings(tmp_path)
    with patch("yt_live_kit.services.subtitle_burn.write_ass") as writer:
        writer.side_effect = lambda cues, output_path, **kwargs: output_path
        build_concatenated_subtitle(
            "video", [(10.0, 20.0)], settings, hook_text="冒頭フック"
        )
    assert writer.call_args.args[0] == []
    assert writer.call_args.kwargs["hook_text"] == "冒頭フック"


def test_build_concatenated_subtitle_missing_vtt_is_japanese(tmp_path):
    from yt_live_kit.config import Settings

    (tmp_path / "video").mkdir()
    with pytest.raises(SubtitleBurnError, match="字幕ファイルが見つかりません"):
        build_concatenated_subtitle(
            "video", [(10.0, 20.0)], Settings(data_dir=tmp_path)
        )


@pytest.mark.parametrize("hook", [" ", "禁止<文字>"])
def test_build_concatenated_subtitle_rejects_invalid_explicit_hook(tmp_path, hook):
    settings = _s3_settings(tmp_path)
    with pytest.raises(SubtitleBurnError):
        build_concatenated_subtitle("video", [(10.0, 20.0)], settings, hook_text=hook)


@pytest.mark.parametrize(
    "segments",
    [[], [("bad", 10.0)], [(float("nan"), 10.0)], [(1.0, 1.0004)]],
)
def test_build_concatenated_subtitle_revalidates_tuple_input(tmp_path, segments):
    settings = _s3_settings(tmp_path)
    with pytest.raises(SubtitleBurnError):
        build_concatenated_subtitle("video", segments, settings)
