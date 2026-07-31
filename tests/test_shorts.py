"""shorts サービスのユニットテスト."""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.ffmpeg import FfmpegError, concat_segments as real_concat_segments
from yt_live_kit.services.subtitle_burn import SubtitleBurnError
from yt_live_kit.services.telop import TelopError, make_clip_id
from yt_live_kit.services.shorts import (
    BLUR_LAYOUT_FILTER,
    CROP_LAYOUT_FILTER,
    INTERMEDIATE_CRF,
    ShortsError,
    build_ass_subtitle_filter,
    build_layout_filter,
    build_short,
    build_short_from_segments,
    build_subtitle_filter,
    build_video_filter_chain,
    escape_ffmpeg_subtitles_path,
    validate_short_duration,
)


def test_build_layout_filter_blur():
    assert build_layout_filter("blur") == BLUR_LAYOUT_FILTER


def test_build_layout_filter_crop():
    assert build_layout_filter("crop") == CROP_LAYOUT_FILTER


def test_build_video_filter_chain_blur_without_subtitles():
    chain, use_complex = build_video_filter_chain("blur")
    assert chain == BLUR_LAYOUT_FILTER
    assert use_complex is True


def test_build_video_filter_chain_crop_without_subtitles():
    chain, use_complex = build_video_filter_chain("crop")
    assert chain == CROP_LAYOUT_FILTER
    assert use_complex is False


def test_build_video_filter_chain_with_subtitles(tmp_path):
    ass_path = tmp_path / "sub.ass"
    ass_path.write_text("ass", encoding="utf-8")
    chain, use_complex = build_video_filter_chain(
        "crop",
        ass_path=ass_path,
        font_name="Hiragino Sans",
    )
    assert chain.startswith(CROP_LAYOUT_FILTER + ",")
    assert "subtitles=" in chain
    assert "FontName=Hiragino Sans" in chain
    assert use_complex is False


def test_build_video_filter_chain_blur_with_subtitles(tmp_path):
    ass_path = tmp_path / "sub.ass"
    ass_path.write_text("ass", encoding="utf-8")
    chain, use_complex = build_video_filter_chain(
        "blur",
        ass_path=ass_path,
        font_name="Hiragino Sans",
    )
    assert chain.startswith(BLUR_LAYOUT_FILTER + ",")
    assert "subtitles=" in chain
    assert use_complex is True


def test_escape_ffmpeg_subtitles_path_colon_and_backslash(tmp_path):
    ass_path = tmp_path / "subtitle dir" / "file:it's\\test.ass"
    ass_path.parent.mkdir(parents=True)
    ass_path.write_text("ass", encoding="utf-8")

    escaped = escape_ffmpeg_subtitles_path(ass_path)
    assert "subtitle dir" in escaped
    assert "file\\\\:it\\\\\\'s\\\\\\\\test.ass" in escaped


def test_escape_ffmpeg_subtitles_path_windows_drive():
    path = Path(r"C:\Videos\subtitle dir\file:it's.ass")
    escaped = escape_ffmpeg_subtitles_path(path)
    assert escaped == "C\\\\:/Videos/subtitle dir/file\\\\:it\\\\\\'s.ass"


def test_escape_ffmpeg_subtitles_path_windows_unc():
    path = Path(r"\\server\share\subtitle dir\file:it's.ass")
    escaped = escape_ffmpeg_subtitles_path(path)
    assert escaped == "//server/share/subtitle dir/file\\\\:it\\\\\\'s.ass"


def test_build_subtitle_filter_contains_force_style(tmp_path):
    ass_path = tmp_path / "test.ass"
    ass_path.write_text("ass", encoding="utf-8")
    filt = build_subtitle_filter(ass_path, "Noto Sans CJK JP")
    assert filt.startswith("subtitles=filename=")
    assert "force_style=" in filt
    assert "FontName=Noto Sans CJK JP" in filt
    assert "MarginV=180" in filt


def _ffmpeg_with_subtitles_filter() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    filters = f"{probe.stdout}\n{probe.stderr}"
    if probe.returncode != 0 or not any(
        len(parts) >= 2 and parts[1] == "subtitles"
        for line in filters.splitlines()
        if (parts := line.split())
    ):
        return None
    return ffmpeg


@pytest.mark.skipif(
    os.environ.get("YTLK_RUN_FFMPEG_INTEGRATION") != "1",
    reason="実 ffmpeg 統合テストは明示実行時だけ有効です",
)
@pytest.mark.parametrize("with_force_style", [False, True])
def test_real_ffmpeg_burns_ass_from_escaped_path(tmp_path, with_force_style):
    ffmpeg = _ffmpeg_with_subtitles_filter()
    if ffmpeg is None:
        pytest.skip("subtitles フィルタを利用できる ffmpeg がありません")

    source_path = tmp_path / "color source.mp4"
    source = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=10:d=1",
            "-an",
            "-c:v",
            "mpeg4",
            str(source_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert source.returncode == 0, source.stderr
    assert source_path.is_file()
    assert source_path.stat().st_size > 0

    ass_path = tmp_path / "subtitle dir" / "clip:it's\\safe.ass"
    ass_path.parent.mkdir(parents=True)
    ass_path.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,字幕テスト
""",
        encoding="utf-8",
    )
    subtitle_filter = (
        build_subtitle_filter(ass_path, "Arial")
        if with_force_style
        else build_ass_subtitle_filter(ass_path)
    )
    output_path = tmp_path / f"burned-{with_force_style}.mp4"
    burned = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vf",
            subtitle_filter,
            "-an",
            "-c:v",
            "mpeg4",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert burned.returncode == 0, burned.stderr
    assert output_path.is_file()
    assert output_path.stat().st_size > 0


@pytest.mark.parametrize(
    ("start", "end", "ok"),
    [
        (0.0, 9.0, False),
        (0.0, 10.0, True),
        (0.0, 180.0, True),
        (0.0, 181.0, False),
    ],
)
def test_validate_short_duration_bounds(start, end, ok):
    if ok:
        assert validate_short_duration(start, end) == pytest.approx(end - start)
    else:
        with pytest.raises(ShortsError):
            validate_short_duration(start, end)


def test_validate_short_duration_start_ge_end():
    with pytest.raises(ShortsError, match="終了時刻"):
        validate_short_duration(30.0, 30.0)


def _setup_video_dir(tmp_path: Path, video_id: str = "testvid1234") -> Path:
    video_dir = tmp_path / video_id
    source_dir = video_dir / "clips" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / f"{video_id}.mp4").write_bytes(b"fake video")

    subtitles_dir = video_dir / "subtitles"
    subtitles_dir.mkdir(parents=True)
    (subtitles_dir / "ja.vtt").write_text(
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:05.000\nテスト\n",
        encoding="utf-8",
    )

    meta = VideoMeta(
        id=video_id,
        title="テスト",
        url="https://www.youtube.com/watch?v=testvid1234",
        upload_date="20260101",
        duration=3600,
        ytdlp_version="2026.7.4",
        fetched_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        subtitle_lang="ja",
    )
    (video_dir / "meta.json").write_text(meta.model_dump_json(), encoding="utf-8")
    return video_dir


def _fake_encode_segment(source, output, start_sec, end_sec, **kwargs):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"intermediate data")
    return output


def _fake_ffmpeg_run(cmd, **kwargs):
    output_path = Path(cmd[-1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"short data")
    return MagicMock(returncode=0, stdout="", stderr="")


def _fake_ffmpeg_run_fail(cmd, **kwargs):
    """パス2（整形）が失敗するケースを模倣する（出力ファイルは作らない）."""
    return MagicMock(returncode=1, stdout="", stderr="boom")


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_blur_filter_in_command(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"
    mock_encode_segment.side_effect = _fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        result = build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="blur",
            burn_subtitles=False,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

    mock_encode_segment.assert_called_once()

    cmd = mock_run.call_args[0][0]
    assert "-ss" not in cmd
    fc_index = cmd.index("-filter_complex")
    filter_graph = cmd[fc_index + 1]
    assert BLUR_LAYOUT_FILTER in filter_graph
    assert "subtitles=" not in filter_graph
    assert result.layout == "blur"
    assert result.burned_subtitles is False
    assert result.duration_sec == pytest.approx(15.0)
    assert result.font_warning is None


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_crop_with_subtitles(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"
    mock_encode_segment.side_effect = _fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ), patch(
        "yt_live_kit.services.shorts.resolve_font",
        return_value="Hiragino Sans",
    ):
        result = build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=True,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

    cmd = mock_run.call_args[0][0]
    assert "-ss" not in cmd
    vf_index = cmd.index("-vf")
    vf_chain = cmd[vf_index + 1]
    assert CROP_LAYOUT_FILTER in vf_chain
    assert "subtitles=" in vf_chain
    assert "FontName=Hiragino Sans" in vf_chain
    assert result.burned_subtitles is True
    assert result.font_warning is None


@patch("subprocess.run")
@patch("shutil.which")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_two_pass_commands_no_mock_of_encode_segment(
    mock_ensure,
    mock_which,
    mock_run,
    tmp_path,
):
    """encode_segment 自体はモックせず、実際に生成される2本のコマンドを検証する."""
    mock_which.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        result = build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=False,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

    assert mock_run.call_count == 2
    pass1_cmd, pass2_cmd = (call.args[0] for call in mock_run.call_args_list)

    # パス1（切り出し）: -ss は -i の後ろ（精密シーク）
    i_index = pass1_cmd.index("-i")
    ss_index = pass1_cmd.index("-ss")
    assert ss_index > i_index
    assert pass1_cmd[i_index + 1] == str(tmp_path / "source.mp4")

    # パス2（整形）: -ss を一切含まない
    assert "-ss" not in pass2_cmd
    assert CROP_LAYOUT_FILTER in pass2_cmd[pass2_cmd.index("-vf") + 1]

    assert result.output_path.is_file()


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_removes_intermediate_by_default(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"

    created_intermediate: list[Path] = []

    def fake_encode_segment(source, output, start_sec, end_sec, **kwargs):
        _fake_encode_segment(source, output, start_sec, end_sec, **kwargs)
        created_intermediate.append(output)
        return output

    mock_encode_segment.side_effect = fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=False,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

    assert created_intermediate
    assert not created_intermediate[0].exists()


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_keeps_intermediate_when_requested(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"

    created_intermediate: list[Path] = []

    def fake_encode_segment(source, output, start_sec, end_sec, **kwargs):
        _fake_encode_segment(source, output, start_sec, end_sec, **kwargs)
        created_intermediate.append(output)
        return output

    mock_encode_segment.side_effect = fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=False,
            ffmpeg_path="/usr/bin/ffmpeg",
            keep_intermediate=True,
        )

    assert created_intermediate
    assert created_intermediate[0].exists()


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_font_warning_when_font_missing(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"
    mock_encode_segment.side_effect = _fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    progress_messages: list[str] = []

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=False,
    ), patch(
        "yt_live_kit.services.shorts.resolve_font",
        return_value="sans-serif",
    ):
        result = build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=True,
            ffmpeg_path="/usr/bin/ffmpeg",
            on_progress=progress_messages.append,
        )

    assert result.font_warning is not None
    assert "フォント" in result.font_warning
    assert not any("警告" in msg for msg in progress_messages)


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_no_font_warning_when_font_available(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"
    mock_encode_segment.side_effect = _fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ), patch(
        "yt_live_kit.services.shorts.resolve_font",
        return_value="Hiragino Sans",
    ):
        result = build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=True,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

    assert result.font_warning is None


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_duration_validation_in_build(
    mock_ensure,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(ShortsError, match="10 秒以上"):
        build_short(video_id, 0.0, 9.0, settings)

    mock_run.assert_not_called()


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_removes_intermediate_when_pass2_fails(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    """パス2（整形）が失敗しても、keep_intermediate=False なら中間ファイルを片付ける."""
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"

    created_intermediate: list[Path] = []

    def fake_encode_segment(source, output, start_sec, end_sec, **kwargs):
        _fake_encode_segment(source, output, start_sec, end_sec, **kwargs)
        created_intermediate.append(output)
        return output

    mock_encode_segment.side_effect = fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run_fail

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        with pytest.raises(ShortsError, match="ショート動画の生成に失敗"):
            build_short(
                video_id,
                10.0,
                25.0,
                settings,
                layout="crop",
                burn_subtitles=False,
                ffmpeg_path="/usr/bin/ffmpeg",
            )

    assert created_intermediate
    assert not created_intermediate[0].exists()


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_keeps_intermediate_when_pass2_fails_and_keep_requested(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    """keep_intermediate=True の場合は、パス2 失敗時も中間ファイルを残す（デバッグ用途）."""
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"

    created_intermediate: list[Path] = []

    def fake_encode_segment(source, output, start_sec, end_sec, **kwargs):
        _fake_encode_segment(source, output, start_sec, end_sec, **kwargs)
        created_intermediate.append(output)
        return output

    mock_encode_segment.side_effect = fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run_fail

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        with pytest.raises(ShortsError, match="ショート動画の生成に失敗"):
            build_short(
                video_id,
                10.0,
                25.0,
                settings,
                layout="crop",
                burn_subtitles=False,
                ffmpeg_path="/usr/bin/ffmpeg",
                keep_intermediate=True,
            )

    assert created_intermediate
    assert created_intermediate[0].exists()


@patch("yt_live_kit.services.shorts.subprocess.run")
@patch("yt_live_kit.services.shorts.find_ffmpeg")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_pass1_uses_intermediate_crf(
    mock_ensure,
    mock_encode_segment,
    mock_find_ffmpeg,
    mock_run,
    tmp_path,
):
    """パス1（切り出し）の encode_segment 呼び出しには高品質な crf（世代劣化対策）を渡す."""
    mock_find_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_ensure.return_value = tmp_path / "source.mp4"
    mock_encode_segment.side_effect = _fake_encode_segment
    mock_run.side_effect = _fake_ffmpeg_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with patch(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        return_value=True,
    ):
        build_short(
            video_id,
            10.0,
            25.0,
            settings,
            layout="crop",
            burn_subtitles=False,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

    mock_encode_segment.assert_called_once()
    _, call_kwargs = mock_encode_segment.call_args
    assert call_kwargs["crf"] == INTERMEDIATE_CRF == 16


@patch("yt_live_kit.services.shorts.build_segment_subtitle")
@patch("yt_live_kit.services.shorts.encode_segment")
@patch("yt_live_kit.services.shorts.ensure_source_video")
def test_build_short_removes_intermediate_when_subtitle_build_fails(
    mock_ensure,
    mock_encode,
    mock_subtitle,
    tmp_path,
):
    """字幕生成が失敗しても中間ファイルを残さない."""
    from yt_live_kit.services.subtitle_burn import SubtitleBurnError

    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "vid123"
    video_dir.mkdir(parents=True)
    source = video_dir / "clips" / "source" / "src.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"src")
    mock_ensure.return_value = source

    intermediate = video_dir / "shorts" / "segments" / "short_10_40_src.mp4"

    def _fake_encode(*args, **kwargs):
        intermediate.parent.mkdir(parents=True, exist_ok=True)
        intermediate.write_bytes(b"seg")
        return intermediate

    mock_encode.side_effect = _fake_encode
    mock_subtitle.side_effect = SubtitleBurnError("字幕ファイルが見つかりません。")

    with pytest.raises(SubtitleBurnError):
        build_short("vid123", 10, 40, settings)

    assert not intermediate.exists()


# --- S3: ジャンプカット連結ショート ---------------------------------------


def _valid_s3_document(segments: list[tuple[float, float]]) -> TelopScriptDocument:
    return TelopScriptDocument.model_validate(
        {
            "hook_text": "冒頭フック",
            "title_candidates": ["タイトル"],
            "description": "説明文",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": start,
                    "end_sec": end,
                    "lines": [
                        {
                            "start_sec": start,
                            "end_sec": min(start + 1.0, end),
                            "text": f"字幕{index}",
                            "emphasis": index == 1,
                        }
                    ],
                }
                for index, (start, end) in enumerate(segments, start=1)
            ],
        }
    )


def _install_s3_success_mocks(monkeypatch, tmp_path: Path):
    calls: dict[str, list] = {"encode": [], "concat": [], "subtitle": [], "run": []}
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    monkeypatch.setattr(
        "yt_live_kit.services.shorts.ensure_source_video", lambda *args: source
    )

    def encode(source_path, output, start, end, **kwargs):
        calls["encode"].append((source_path, output, start, end, kwargs))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"segment")
        return output

    def concat(paths, output, **kwargs):
        calls["concat"].append((list(paths), output, kwargs))
        output.write_bytes(b"concat")
        return output

    def subtitle(video_id, segments, settings, **kwargs):
        calls["subtitle"].append((video_id, list(segments), kwargs))
        output = settings.data_dir / video_id / "shorts" / "subtitles" / "result.ass"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("ass", encoding="utf-8")
        return output

    def run(cmd, **kwargs):
        calls["run"].append(cmd)
        Path(cmd[-1]).write_bytes(b"final")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("yt_live_kit.services.shorts.encode_segment", encode)
    monkeypatch.setattr("yt_live_kit.services.shorts.concat_segments", concat)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.build_concatenated_subtitle", subtitle
    )
    monkeypatch.setattr("yt_live_kit.services.shorts.find_ffmpeg", lambda path: path)
    monkeypatch.setattr("yt_live_kit.services.shorts.subprocess.run", run)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.is_japanese_font_available", lambda preferred: True
    )
    return calls


def test_build_ass_subtitle_filter_has_no_force_style(tmp_path):
    result = build_ass_subtitle_filter(tmp_path / "style.ass")
    assert result.startswith("subtitles=filename=")
    assert "force_style" not in result


@pytest.mark.parametrize("layout", ["blur", "crop"])
def test_build_short_from_segments_preserves_order_progress_and_presets(
    tmp_path, monkeypatch, layout
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    calls = _install_s3_success_mocks(monkeypatch, tmp_path)
    segments = [(20.0, 24.0), (5.0, 9.0), (20.0, 24.0)]
    progress = []

    result = build_short_from_segments(
        video_id,
        segments,
        settings,
        layout=layout,
        telop_script=_valid_s3_document(segments),
        preset="boxed",
        hook_preset="hook",
        ffmpeg_path="ffmpeg-test",
        on_progress=lambda current, total, message: progress.append(
            (current, total, message)
        ),
    )

    assert [(call[2], call[3]) for call in calls["encode"]] == segments
    assert all(call[4]["crf"] == INTERMEDIATE_CRF for call in calls["encode"])
    encoded_paths = [call[1] for call in calls["encode"]]
    assert calls["concat"][0][0] == encoded_paths
    assert [item[:2] for item in progress] == [
        (1, 6),
        (2, 6),
        (3, 6),
        (4, 6),
        (5, 6),
        (6, 6),
    ]
    assert calls["subtitle"][0][2]["preset"] == "boxed"
    assert calls["subtitle"][0][2]["hook_preset"] == "hook"
    cmd = calls["run"][0]
    filter_flag = "-filter_complex" if layout == "blur" else "-vf"
    filter_value = cmd[cmd.index(filter_flag) + 1]
    assert filter_value.count("subtitles=filename=") == 1
    assert "force_style" not in filter_value
    assert "-ss" not in cmd and "-t" not in cmd
    assert result.output_path.is_file()
    assert result.output_path.name == f"short_{make_clip_id(segments)}.mp4"
    assert result.burned_subtitles is True
    assert result.duration_sec == pytest.approx(12.0)
    assert result.command_log_path.name == f"{result.output_path.stem}.ffmpeg.log"
    assert not any((tmp_path / video_id / "shorts" / "segments").iterdir())


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ([], "1 件以上"),
        ([("x", 10.0)], "数値"),
        ([(float("nan"), 10.0)], "有限"),
        ([(-0.0004, 10.0)], "負"),
        ([(2.0, 1.0)], "開始時刻より後"),
        ([(1.0, 1.0004)], "開始時刻より後"),
        ([(0.0, 9.0)], "10 秒以上"),
        ([(0.0, 181.0)], "区間を減らすか短くしてください"),
    ],
)
def test_build_short_from_segments_rejects_bad_segments_before_source(
    tmp_path, monkeypatch, segments, message
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    ensure = MagicMock()
    monkeypatch.setattr("yt_live_kit.services.shorts.ensure_source_video", ensure)
    with pytest.raises(ShortsError, match=message):
        build_short_from_segments(video_id, segments, Settings(data_dir=tmp_path))
    ensure.assert_not_called()


@pytest.mark.parametrize("duration", [10.0, 180.0])
def test_build_short_from_segments_accepts_integer_ms_duration_boundaries(
    tmp_path, monkeypatch, duration
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    calls = _install_s3_success_mocks(monkeypatch, tmp_path)
    result = build_short_from_segments(
        video_id, [(0.00049, duration + 0.00049)], Settings(data_dir=tmp_path)
    )
    assert result.duration_sec == duration
    assert calls["encode"][0][2:4] == (0.0, duration)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"layout": "square"},
        {"output_name": ""},
        {"output_name": "../bad.mp4"},
        {"output_name": "bad.mov"},
        {"output_name": "bad\x00name.mp4"},
        {"output_name": "bad\nname.mp4"},
        {"output_name": "bad\x1fname.mp4"},
        {"hook_text": "   "},
        {"hook_text": "禁止<文字>"},
        {"preset": "missing"},
        {"hook_preset": "missing"},
    ],
)
def test_build_short_from_segments_preflight_rejects_options_before_source(
    tmp_path, monkeypatch, kwargs
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    ensure = MagicMock()
    encode = MagicMock()
    monkeypatch.setattr("yt_live_kit.services.shorts.ensure_source_video", ensure)
    monkeypatch.setattr("yt_live_kit.services.shorts.encode_segment", encode)
    with pytest.raises(ShortsError):
        build_short_from_segments(
            video_id, [(0.0, 10.0)], Settings(data_dir=tmp_path), **kwargs
        )
    ensure.assert_not_called()
    encode.assert_not_called()


def test_build_short_from_segments_rejects_invalid_telop_before_source(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    ensure = MagicMock()
    monkeypatch.setattr("yt_live_kit.services.shorts.ensure_source_video", ensure)
    document = _valid_s3_document([(0.0, 10.0)])
    invalid = document.model_copy(
        update={"segments": [document.segments[0].model_copy(update={"end_sec": 9.0})]}
    )
    with pytest.raises(ShortsError, match="入力区間"):
        build_short_from_segments(
            video_id,
            [(0.0, 10.0)],
            Settings(data_dir=tmp_path),
            telop_script=invalid,
        )
    ensure.assert_not_called()


def test_build_short_from_segments_converts_telop_validation_error_before_source(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    ensure = MagicMock()
    monkeypatch.setattr("yt_live_kit.services.shorts.ensure_source_video", ensure)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.validate_telop_script",
        MagicMock(side_effect=TelopError("台本検証失敗")),
    )
    with pytest.raises(ShortsError, match="台本検証失敗"):
        build_short_from_segments(
            video_id,
            [(0.0, 10.0)],
            Settings(data_dir=tmp_path),
            telop_script=_valid_s3_document([(0.0, 10.0)]),
        )
    ensure.assert_not_called()


def test_build_short_from_segments_custom_name_atomic_replace_and_keep(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)
    existing = video_dir / "shorts" / "output" / "custom.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")

    result = build_short_from_segments(
        video_id,
        [(0.0, 10.0)],
        settings,
        output_name="custom.mp4",
        keep_intermediate=True,
    )

    assert existing.read_bytes() == b"final"
    assert result.command_log_path.name == "custom.ffmpeg.log"
    assert any((video_dir / "shorts" / "segments").iterdir())


def test_build_short_from_segments_pass2_failure_preserves_existing_and_cleans(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)
    existing = video_dir / "shorts" / "output" / "custom.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")

    monkeypatch.setattr(
        "yt_live_kit.services.shorts.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=1, stdout="", stderr="失敗"),
    )
    with pytest.raises(ShortsError, match="生成に失敗"):
        build_short_from_segments(
            video_id,
            [(0.0, 10.0)],
            settings,
            output_name="custom.mp4",
        )

    assert existing.read_bytes() == b"old"
    assert not any((video_dir / "shorts" / "segments").iterdir())
    assert not list(existing.parent.glob(".custom.*.mp4"))


def test_build_short_from_segments_font_warning_matches_legacy(tmp_path, monkeypatch):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    _install_s3_success_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.is_japanese_font_available",
        lambda preferred: False,
    )
    result = build_short_from_segments(
        video_id, [(0.0, 10.0)], Settings(data_dir=tmp_path)
    )
    assert result.font_warning == ("日本語フォントが見つかりません。字幕が正しく表示されない可能性があります。")


@pytest.mark.parametrize(
    ("stage", "keep_intermediate"),
    [
        ("encode", False),
        ("concat", False),
        ("subtitle", False),
        ("pass2_setup", False),
        ("encode", True),
        ("concat", True),
        ("subtitle", True),
        ("pass2_setup", True),
    ],
)
def test_build_short_from_segments_stage_failures_convert_and_apply_cleanup_policy(
    tmp_path, monkeypatch, stage, keep_intermediate
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)

    if stage == "encode":

        def fail_encode(source, output, *args, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            (output.parent / "marker.txt").write_text("keep", encoding="utf-8")
            raise FfmpegError("区間エンコード失敗")

        monkeypatch.setattr("yt_live_kit.services.shorts.encode_segment", fail_encode)
    elif stage == "concat":
        monkeypatch.setattr(
            "yt_live_kit.services.shorts.concat_segments",
            MagicMock(side_effect=FfmpegError("連結失敗")),
        )
    elif stage == "subtitle":
        monkeypatch.setattr(
            "yt_live_kit.services.shorts.build_concatenated_subtitle",
            MagicMock(side_effect=SubtitleBurnError("字幕失敗")),
        )
    else:
        monkeypatch.setattr(
            "yt_live_kit.services.shorts.find_ffmpeg",
            MagicMock(side_effect=FfmpegError("ffmpeg 未検出")),
        )

    with pytest.raises(ShortsError):
        build_short_from_segments(
            video_id,
            [(0.0, 10.0)],
            settings,
            keep_intermediate=keep_intermediate,
        )

    clip_dirs = list((video_dir / "shorts" / "segments").glob("*"))
    if keep_intermediate:
        assert len(clip_dirs) == 1
        assert any(clip_dirs[0].iterdir())
    else:
        assert clip_dirs == []


def test_build_short_from_segments_success_cleanup_failure_is_reported(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.shutil.rmtree",
        MagicMock(side_effect=OSError("削除拒否")),
    )

    with pytest.raises(ShortsError, match="中間ファイルの削除に失敗"):
        build_short_from_segments(video_id, [(0.0, 10.0)], settings)


def test_build_short_from_segments_primary_failure_survives_cleanup_failure(
    tmp_path, monkeypatch, caplog
):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=1, stdout="", stderr="本体失敗"),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.shutil.rmtree",
        MagicMock(side_effect=OSError("削除拒否")),
    )

    with caplog.at_level("WARNING", logger="yt_live_kit.services.shorts"):
        with pytest.raises(ShortsError, match="ショート動画の生成に失敗") as error:
            build_short_from_segments(video_id, [(0.0, 10.0)], settings)

    assert "本体失敗" in str(error.value)
    assert "中間ファイルを削除できませんでした" in caplog.text


@pytest.mark.parametrize("keep_intermediate", [False, True])
def test_build_short_from_segments_real_concat_failure_keeps_or_cleans_all_artifacts(
    tmp_path, monkeypatch, keep_intermediate
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)

    def encode_with_log(source, output, start, end, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"segment")
        output.with_suffix(".ffmpeg.log").write_text("区間ログ", encoding="utf-8")
        return output

    monkeypatch.setattr("yt_live_kit.services.shorts.encode_segment", encode_with_log)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.concat_segments", real_concat_segments
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.find_ffmpeg", lambda ffmpeg_path: ffmpeg_path
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.subprocess.run",
        lambda *args, **kwargs: MagicMock(
            returncode=1, stdout="", stderr="concat failure"
        ),
    )

    with pytest.raises(ShortsError, match="ffmpeg の連結に失敗"):
        build_short_from_segments(
            video_id,
            [(0.0, 10.0)],
            settings,
            keep_intermediate=keep_intermediate,
        )

    clip_dirs = list((video_dir / "shorts" / "segments").glob("*"))
    if keep_intermediate:
        assert len(clip_dirs) == 1
        names = {path.name for path in clip_dirs[0].iterdir()}
        assert {
            "seg_001.mp4",
            "seg_001.ffmpeg.log",
            "concat.txt",
            "concat.ffmpeg.log",
        } <= names
    else:
        assert clip_dirs == []


@pytest.mark.parametrize("keep_intermediate", [False, True])
def test_build_short_from_segments_pass2_returncode_failure_keeps_or_cleans(
    tmp_path, monkeypatch, keep_intermediate
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)
    _install_s3_success_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "yt_live_kit.services.shorts.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=1, stdout="", stderr="pass2 failure"),
    )

    with pytest.raises(ShortsError, match="ショート動画の生成に失敗"):
        build_short_from_segments(
            video_id,
            [(0.0, 10.0)],
            settings,
            keep_intermediate=keep_intermediate,
        )

    clip_dirs = list((video_dir / "shorts" / "segments").glob("*"))
    if keep_intermediate:
        assert len(clip_dirs) == 1
        assert {"seg_001.mp4", "concat.mp4"} <= {
            path.name for path in clip_dirs[0].iterdir()
        }
    else:
        assert clip_dirs == []
