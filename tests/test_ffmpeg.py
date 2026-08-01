"""ffmpeg サービスのユニットテスト."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ffmpeg import (
    FfmpegError,
    build_concat_list,
    build_ffmpeg_command,
    concat_segments,
    cut_clip,
    encode_segment,
    ffprobe_path_for,
    probe_duration,
)


def _setup_video_dir(tmp_path: Path, video_id: str = "testvid1234") -> Path:
    video_dir = tmp_path / video_id
    source_dir = video_dir / "clips" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / f"{video_id}.mp4").write_bytes(b"fake video")

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


@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_build_ffmpeg_command(mock_which):
    mock_which.return_value = "/usr/bin/ffmpeg"
    cmd = build_ffmpeg_command(
        Path("/tmp/input.mp4"),
        Path("/tmp/output.mp4"),
        222,
        990,
    )
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert "-ss" in cmd
    assert "222" in cmd
    assert "-t" in cmd
    assert "768" in cmd
    assert "-c" in cmd
    assert "copy" in cmd


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_cut_clip_success(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"

    def fake_run(cmd, **kwargs):
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"clip data")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    result = cut_clip(video_id, "03:42", "16:30", settings, ffmpeg_path="/usr/bin/ffmpeg")
    assert result.output_path.is_file()
    assert result.command_log_path.is_file()
    assert result.duration_sec == 768


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_cut_clip_fails_without_ffmpeg(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="codec error")

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(FfmpegError, match="ffmpeg"):
        cut_clip(
            video_id,
            "03:42",
            "16:30",
            settings,
            ffmpeg_path="/usr/bin/ffmpeg",
            reencode=True,
        )


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_encode_segment_ss_before_input(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"

    def fake_run(cmd, **kwargs):
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"segment data")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run

    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")
    output = tmp_path / "seg.mp4"

    encode_segment(source, output, 10.0, 40.0, ffmpeg_path="/usr/bin/ffmpeg")

    cmd = mock_run.call_args[0][0]
    i_index = cmd.index("-i")
    ss_index = cmd.index("-ss")
    # 入力シーク: -ss が -i より前（長尺動画の後半切り出しを高速化）
    assert ss_index < i_index
    assert cmd[i_index + 1] == str(source)
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-c:a" in cmd
    assert "aac" in cmd
    assert "-b:a" in cmd
    assert "192k" in cmd


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_encode_segment_vf_filter_order(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"

    def fake_run(cmd, **kwargs):
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"segment data")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run

    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")
    output = tmp_path / "seg.mp4"

    encode_segment(
        source,
        output,
        0.0,
        30.0,
        ffmpeg_path="/usr/bin/ffmpeg",
        scale="scale=1280:720",
        extra_filters=["format=yuv420p"],
    )

    cmd = mock_run.call_args[0][0]
    vf_index = cmd.index("-vf")
    assert cmd[vf_index + 1] == "scale=1280:720,format=yuv420p"
    assert "-b:a" in cmd
    assert "192k" in cmd


def test_build_concat_list_quote_escape(tmp_path):
    segment = tmp_path / "seg's clip.mp4"
    segment.write_bytes(b"data")
    list_path = tmp_path / "concat.txt"

    build_concat_list([segment], list_path)

    content = list_path.read_text(encoding="utf-8")
    assert "file '" in content
    assert "'\\''" in content
    assert content.endswith("\n")


def test_build_concat_list_empty_raises():
    with pytest.raises(FfmpegError, match="連結"):
        build_concat_list([], Path("/tmp/concat.txt"))


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_probe_duration_uses_ffprobe(mock_which, mock_run, tmp_path):
    source = tmp_path / "short.mp4"
    source.write_bytes(b"video")
    mock_which.return_value = "/usr/bin/ffprobe"
    mock_run.return_value = MagicMock(returncode=0, stdout="30.125\n", stderr="")

    assert probe_duration(source) == 30.125
    assert "format=duration" in mock_run.call_args.args[0]


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_probe_duration_rejects_invalid_result(mock_which, mock_run, tmp_path):
    source = tmp_path / "short.mp4"
    source.write_bytes(b"video")
    mock_which.return_value = "/usr/bin/ffprobe"
    mock_run.return_value = MagicMock(returncode=0, stdout="unknown", stderr="")
    with pytest.raises(FfmpegError, match="正しくありません"):
        probe_duration(source)


@pytest.mark.parametrize(
    ("ffmpeg_path", "expected"),
    [
        ("ffmpeg", "ffprobe"),
        ("/usr/local/bin/ffmpeg", "/usr/local/bin/ffprobe"),
        (
            "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
        ),
    ],
)
def test_ffprobe_path_for_replaces_basename_only(ffmpeg_path, expected):
    assert ffprobe_path_for(ffmpeg_path) == expected


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_concat_segments_command(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"

    def fake_run(cmd, **kwargs):
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"concat data")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run

    seg1 = tmp_path / "seg1.mp4"
    seg2 = tmp_path / "seg2.mp4"
    seg1.write_bytes(b"seg1")
    seg2.write_bytes(b"seg2")
    output = tmp_path / "output" / "highlight.mp4"
    list_path = output.parent / "concat.txt"

    result = concat_segments([seg1, seg2], output, ffmpeg_path="/usr/bin/ffmpeg")

    cmd = mock_run.call_args[0][0]
    assert "-f" in cmd
    assert "concat" in cmd
    assert "-safe" in cmd
    assert "0" in cmd
    assert "-c" in cmd
    assert "copy" in cmd
    assert result == output
    assert not list_path.exists()


@pytest.mark.parametrize(
    "output_name",
    ["../evil.mp4", "/tmp/evil.mp4", "subdir/evil.mp4", "evil.mov", ""],
)
def test_cut_clip_rejects_unsafe_output_name(tmp_path, output_name):
    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(FfmpegError, match="出力ファイル名"):
        cut_clip(
            video_id,
            "03:42",
            "16:30",
            settings,
            output_name=output_name,
        )


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_cut_clip_accepts_valid_custom_output_name(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"

    def fake_run(cmd, **kwargs):
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"clip data")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run

    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path)

    result = cut_clip(
        video_id,
        "03:42",
        "16:30",
        settings,
        output_name="my_clip.mp4",
        ffmpeg_path="/usr/bin/ffmpeg",
    )

    assert result.output_path == video_dir / "clips" / "output" / "my_clip.mp4"
    assert result.output_path.is_file()


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_encode_segment_timeout_raises_ffmpeg_error(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=60)

    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")
    output = tmp_path / "seg.mp4"

    with pytest.raises(FfmpegError, match="タイムアウト"):
        encode_segment(
            source,
            output,
            10.0,
            40.0,
            ffmpeg_path="/usr/bin/ffmpeg",
            ffmpeg_timeout=60,
        )


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_cut_clip_passes_ffmpeg_timeout(mock_which, mock_run, tmp_path):
    mock_which.return_value = "/usr/bin/ffmpeg"

    def fake_run(cmd, **kwargs):
        assert kwargs.get("timeout") == 120
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"clip data")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run

    video_id = "testvid1234"
    _setup_video_dir(tmp_path, video_id)
    settings = Settings(data_dir=tmp_path, ffmpeg_timeout=120)

    cut_clip(video_id, "03:42", "16:30", settings, ffmpeg_path="/usr/bin/ffmpeg")
