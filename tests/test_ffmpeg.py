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
    FFMPEG_CAPABILITY_TIMEOUT_DEFAULT,
    FfmpegError,
    MediaStreams,
    build_concat_list,
    build_ffmpeg_command,
    concat_segments,
    cut_clip,
    diagnose_ffmpeg,
    encode_segment,
    ensure_source_video,
    ensure_subtitles_filter,
    ffprobe_path_for,
    parse_ffmpeg_filter_names,
    parse_media_streams_json,
    probe_ffmpeg_capabilities,
    probe_duration,
    probe_media_streams,
    require_audio_video_streams,
)


@pytest.fixture(autouse=True)
def _stub_media_stream_validation_for_existing_tests(monkeypatch):
    """既存テストでは追加した stream 検査を独立させる."""
    streams = MediaStreams(video_count=1, audio_count=1)
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.probe_media_streams", lambda *args, **kwargs: streams
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.require_audio_video_streams",
        lambda *args, **kwargs: streams,
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


def _fake_ffmpeg_tools(tmp_path: Path) -> tuple[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    ffmpeg = bin_dir / "ffmpeg"
    ffprobe = bin_dir / "ffprobe"
    for path in (ffmpeg, ffprobe):
        path.write_text("tool", encoding="utf-8")
        path.chmod(0o755)
    return str(ffmpeg), str(ffprobe)


def test_parse_ffmpeg_filter_names_uses_exact_second_column_from_both_streams():
    names = parse_ffmpeg_filter_names(
        """Filters:
  .. subtitles V->V Render text subtitles using libass
  T.. subtitles_cuda V->V GPU subtitle renderer
  ... scale V->V Scale video
""",
        "  TS subtitles_graphics V->V Render graphical subtitles\n",
    )

    assert "subtitles" in names
    assert "subtitles_cuda" in names
    assert "subtitles_graphics" in names
    assert "subtitle" not in names


def test_parse_ffmpeg_filter_names_ignores_warning_and_legend_lines():
    names = parse_ffmpeg_filter_names(
        """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ..C = Command support
  ... scale V->V Scale video
""",
        "warning subtitles unavailable\n",
    )

    assert names == frozenset({"scale"})
    assert "subtitles" not in names


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_probe_ffmpeg_capabilities_uses_resolved_binary_and_short_timeout(
    mock_which,
    mock_run,
):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="  T.. subtitles V->V Render text subtitles\n",
        stderr="",
    )

    result = probe_ffmpeg_capabilities("configured-ffmpeg")

    mock_which.assert_called_once_with("configured-ffmpeg")
    assert mock_run.call_args.args[0] == [
        "/opt/ffmpeg/bin/ffmpeg",
        "-hide_banner",
        "-filters",
    ]
    assert mock_run.call_args.kwargs["timeout"] == FFMPEG_CAPABILITY_TIMEOUT_DEFAULT
    assert result.configured_path == "configured-ffmpeg"
    assert result.resolved_path == "/opt/ffmpeg/bin/ffmpeg"
    assert result.subtitles_available is True


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_probe_ffmpeg_capabilities_warning_does_not_fake_subtitles_support(
    mock_which,
    mock_run,
):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="  ... scale V->V Scale video\n",
        stderr="warning subtitles unavailable\n",
    )

    result = probe_ffmpeg_capabilities("configured-ffmpeg")

    assert result.filter_names == frozenset({"scale"})
    assert result.subtitles_available is False


@patch("yt_live_kit.services.ffmpeg.shutil.which", return_value=None)
def test_probe_ffmpeg_capabilities_missing_binary_is_actionable(mock_which):
    with pytest.raises(FfmpegError, match="YTLK_FFMPEG_PATH"):
        probe_ffmpeg_capabilities("missing-ffmpeg")


@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    side_effect=OSError("path lookup failed"),
)
def test_probe_ffmpeg_capabilities_path_lookup_oserror_is_actionable(mock_which):
    with pytest.raises(FfmpegError, match="YTLK_FFMPEG_PATH") as error:
        probe_ffmpeg_capabilities("broken-ffmpeg")

    assert "path lookup failed" not in str(error.value)


@patch("yt_live_kit.services.ffmpeg.subprocess.run", side_effect=OSError("exec denied"))
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_probe_ffmpeg_capabilities_execution_oserror_is_actionable(
    mock_which,
    mock_run,
):
    with pytest.raises(FfmpegError, match="実行権限") as error:
        probe_ffmpeg_capabilities("configured-ffmpeg")

    assert "YTLK_FFMPEG_PATH" in str(error.value)
    assert "exec denied" in str(error.value)


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_probe_ffmpeg_capabilities_timeout_is_actionable(mock_which, mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/opt/ffmpeg/bin/ffmpeg", "-hide_banner", "-filters"],
        timeout=3,
    )

    with pytest.raises(FfmpegError, match="3 秒でタイムアウト") as error:
        probe_ffmpeg_capabilities("configured-ffmpeg", timeout=3)

    assert "YTLK_FFMPEG_PATH" in str(error.value)


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_probe_ffmpeg_capabilities_nonzero_is_actionable(mock_which, mock_run):
    mock_run.return_value = MagicMock(
        returncode=2,
        stdout="",
        stderr="invalid option",
    )

    with pytest.raises(FfmpegError, match="終了コード: 2") as error:
        probe_ffmpeg_capabilities("configured-ffmpeg")

    assert "YTLK_FFMPEG_PATH" in str(error.value)
    assert "invalid option" in str(error.value)


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_ensure_subtitles_filter_rejects_similar_but_missing_filter(
    mock_which,
    mock_run,
):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="  T.. subtitles_cuda V->V GPU subtitle renderer\n",
        stderr="",
    )

    with pytest.raises(FfmpegError, match="ffmpeg-full") as error:
        ensure_subtitles_filter("configured-ffmpeg")

    assert "YTLK_FFMPEG_PATH" in str(error.value)
    assert "YTLK_FFMPEG_PATH に ffmpeg-full" in str(error.value)


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch(
    "yt_live_kit.services.ffmpeg.shutil.which",
    return_value="/opt/ffmpeg/bin/ffmpeg",
)
def test_diagnose_ffmpeg_uses_same_resolved_binary_for_filters_and_version(
    mock_which,
    mock_run,
):
    mock_run.side_effect = [
        MagicMock(
            returncode=0,
            stdout="  T.. subtitles V->V Render text subtitles\n",
            stderr="",
        ),
        MagicMock(
            returncode=0,
            stdout="ffmpeg version 8.0.1 Copyright\nconfiguration details\n",
            stderr="",
        ),
    ]

    result = diagnose_ffmpeg("configured-ffmpeg")

    mock_which.assert_called_once_with("configured-ffmpeg")
    commands = [item.args[0] for item in mock_run.call_args_list]
    assert commands == [
        ["/opt/ffmpeg/bin/ffmpeg", "-hide_banner", "-filters"],
        ["/opt/ffmpeg/bin/ffmpeg", "-version"],
    ]
    assert result.version == "ffmpeg version 8.0.1 Copyright"
    assert result.subtitles_available is True


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


def test_parse_media_streams_json_accepts_ffprobe_shape_and_counts_av():
    streams = parse_media_streams_json(
        json.dumps(
            {
                "programs": [],
                "stream_groups": [],
                "streams": [
                    {"codec_type": "video"},
                    {"codec_type": "audio"},
                    {"codec_type": "audio"},
                    {"codec_type": "subtitle"},
                ],
            }
        )
    )
    assert streams == MediaStreams(video_count=1, audio_count=2)
    assert streams.has_audio_video


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"streams": "invalid"}',
        '{"streams": [{"codec_type": null}]}',
        '{"streams": [{"codec_type": "video", "index": 0}]}',
        '{"streams": [], "unknown": []}',
        '{"streams": [], "programs": {}}',
    ],
)
def test_parse_media_streams_json_rejects_invalid_schema(payload):
    with pytest.raises(FfmpegError, match="ffprobe"):
        parse_media_streams_json(payload)


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_probe_media_streams_uses_colocated_ffprobe_argv_and_timeout(
    mock_which, mock_run, tmp_path
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    ffmpeg, ffprobe = _fake_ffmpeg_tools(tmp_path)
    mock_which.side_effect = [ffmpeg, ffprobe]
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"streams":[{"codec_type":"video"},{"codec_type":"audio"}]}',
        stderr="",
    )

    streams = probe_media_streams(
        source, ffmpeg_path="configured-ffmpeg", ffmpeg_timeout=17
    )

    assert streams.has_audio_video
    assert mock_run.call_args.args[0] == [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(source.resolve()),
    ]
    assert mock_run.call_args.kwargs["timeout"] == 17
    assert "shell" not in mock_run.call_args.kwargs


@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_probe_media_streams_missing_colocated_ffprobe_fails_closed(
    mock_which, tmp_path
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    ffmpeg, _ffprobe = _fake_ffmpeg_tools(tmp_path)
    mock_which.side_effect = [ffmpeg, None]
    with pytest.raises(FfmpegError, match="同じ場所に ffprobe"):
        probe_media_streams(source, ffmpeg_path="configured-ffmpeg")


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_probe_media_streams_timeout_fails_closed(mock_which, mock_run, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    ffmpeg, ffprobe = _fake_ffmpeg_tools(tmp_path)
    mock_which.side_effect = [ffmpeg, ffprobe]
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=3)
    with pytest.raises(FfmpegError, match="タイムアウト"):
        probe_media_streams(
            source, ffmpeg_path="configured-ffmpeg", ffmpeg_timeout=3
        )


@patch("yt_live_kit.services.ffmpeg.subprocess.run")
@patch("yt_live_kit.services.ffmpeg.shutil.which")
def test_probe_media_streams_nonzero_fails_closed(mock_which, mock_run, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    ffmpeg, ffprobe = _fake_ffmpeg_tools(tmp_path)
    mock_which.side_effect = [ffmpeg, ffprobe]
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="invalid media")
    with pytest.raises(FfmpegError, match="映像・音声 stream"):
        probe_media_streams(source, ffmpeg_path="configured-ffmpeg")


def test_require_audio_video_streams_rejects_video_only(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.probe_media_streams",
        lambda *args, **kwargs: MediaStreams(video_count=1, audio_count=0),
    )
    with pytest.raises(FfmpegError, match="音声付きで再取得"):
        require_audio_video_streams(source, label="完成したショート動画")


def test_ensure_source_video_rejects_video_only_candidate_and_downloads(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    existing = video_dir / "clips" / "source" / f"{video_id}.mp4"
    downloaded = existing.parent / "downloaded.mp4"
    probe = MagicMock(
        side_effect=[
            MediaStreams(video_count=1, audio_count=0),
            MediaStreams(video_count=1, audio_count=1),
        ]
    )
    def download_result(*args):
        downloaded.write_bytes(b"downloaded")
        return downloaded

    download = MagicMock(side_effect=download_result)
    monkeypatch.setattr("yt_live_kit.services.ffmpeg.probe_media_streams", probe)
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.require_audio_video_streams",
        lambda path, **kwargs: probe(path, **kwargs),
    )
    monkeypatch.setattr("yt_live_kit.services.ffmpeg.download_video", download)
    settings = Settings(data_dir=tmp_path, ffmpeg_path="configured-ffmpeg")

    assert ensure_source_video(video_id, settings) == downloaded
    download.assert_called_once()
    assert existing.is_file()


def test_ensure_source_video_reuses_only_existing_av_candidate(tmp_path, monkeypatch):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    source_dir = video_dir / "clips" / "source"
    first = source_dir / "000-video-only.mp4"
    first.write_bytes(b"video-only")
    expected = source_dir / f"{video_id}.mp4"
    probe = MagicMock(
        side_effect=[
            MediaStreams(video_count=1, audio_count=0),
            MediaStreams(video_count=1, audio_count=1),
        ]
    )
    download = MagicMock()
    monkeypatch.setattr("yt_live_kit.services.ffmpeg.probe_media_streams", probe)
    monkeypatch.setattr("yt_live_kit.services.ffmpeg.download_video", download)

    result = ensure_source_video(video_id, Settings(data_dir=tmp_path))

    assert result == expected
    download.assert_not_called()


def test_ensure_source_video_rejects_download_result_without_audio(
    tmp_path, monkeypatch
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    downloaded = video_dir / "clips" / "source" / "downloaded.mp4"
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.probe_media_streams",
        MagicMock(
            side_effect=[
                MediaStreams(video_count=1, audio_count=0),
                MediaStreams(video_count=1, audio_count=0),
            ]
        ),
    )
    def download_result(*args):
        downloaded.write_bytes(b"downloaded")
        return downloaded

    monkeypatch.setattr("yt_live_kit.services.ffmpeg.download_video", download_result)
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.require_audio_video_streams",
        require_audio_video_streams,
    )

    with pytest.raises(FfmpegError, match="音声付きで再取得"):
        ensure_source_video(video_id, Settings(data_dir=tmp_path))


def test_encode_segment_rejects_output_without_audio(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "segment.mp4"

    def run(cmd, **kwargs):
        output.write_bytes(b"video-only")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("yt_live_kit.services.ffmpeg.find_ffmpeg", lambda path: path)
    monkeypatch.setattr("yt_live_kit.services.ffmpeg.subprocess.run", run)
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.require_audio_video_streams",
        MagicMock(side_effect=FfmpegError("切り出した区間動画に音声がありません。")),
    )
    with pytest.raises(FfmpegError, match="音声"):
        encode_segment(source, output, 0, 10, ffmpeg_path="ffmpeg-test")


def test_concat_segments_rejects_output_without_audio(tmp_path, monkeypatch):
    segment = tmp_path / "segment.mp4"
    segment.write_bytes(b"segment")
    output = tmp_path / "concat.mp4"

    def run(cmd, **kwargs):
        output.write_bytes(b"video-only")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("yt_live_kit.services.ffmpeg.find_ffmpeg", lambda path: path)
    monkeypatch.setattr("yt_live_kit.services.ffmpeg.subprocess.run", run)
    monkeypatch.setattr(
        "yt_live_kit.services.ffmpeg.require_audio_video_streams",
        MagicMock(side_effect=FfmpegError("連結した区間動画に音声がありません。")),
    )
    with pytest.raises(FfmpegError, match="音声"):
        concat_segments([segment], output, ffmpeg_path="ffmpeg-test")


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
