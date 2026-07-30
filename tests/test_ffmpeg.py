"""ffmpeg サービスのユニットテスト."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ffmpeg import (
    FfmpegError,
    build_ffmpeg_command,
    cut_clip,
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
