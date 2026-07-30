"""shorts サービスのユニットテスト."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.shorts import (
    BLUR_LAYOUT_FILTER,
    CROP_LAYOUT_FILTER,
    ShortsError,
    build_layout_filter,
    build_short,
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
    ass_path = tmp_path / "dir" / "file:test.ass"
    ass_path.parent.mkdir(parents=True)
    ass_path.write_text("ass", encoding="utf-8")

    escaped = escape_ffmpeg_subtitles_path(ass_path)
    assert "\\:" in escaped or "file\\:test" in escaped


def test_escape_ffmpeg_subtitles_path_windows_drive():
    path = Path("C:/Videos/sub/file.ass")
    escaped = escape_ffmpeg_subtitles_path(path)
    assert escaped.startswith("C\\:")


def test_build_subtitle_filter_contains_force_style(tmp_path):
    ass_path = tmp_path / "test.ass"
    ass_path.write_text("ass", encoding="utf-8")
    filt = build_subtitle_filter(ass_path, "Noto Sans CJK JP")
    assert filt.startswith("subtitles=")
    assert "force_style=" in filt
    assert "FontName=Noto Sans CJK JP" in filt
    assert "MarginV=180" in filt


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
