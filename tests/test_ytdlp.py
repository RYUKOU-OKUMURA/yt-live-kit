"""yt-dlp ラッパーのユニットテスト."""

import hashlib
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.subtitle import SubtitleSourceMetadata
from yt_live_kit.services.ytdlp import (
    AudioSpanError,
    AudioSpanRange,
    MISSING_YTDLP_BINARY_IDENTITY,
    YtdlpError,
    _download_subtitles,
    _check_audio_ffmpeg,
    _find_subtitle_file,
    _resolve_audio_ffmpeg,
    _run_ytdlp,
    download_video,
    extract_video_id,
    fetch,
    get_ytdlp_binary_identity,
    prepare_audio_span,
    make_subtitle_source_fingerprint,
)


VIDEO_ID = "IJvd6k6ZmUo"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
VTT_ONE = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
最初の字幕
"""
VTT_TWO = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
再取得した字幕
"""


def _wav_bytes(
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    duration_ms: int = 30_000,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frame_count = duration_ms * sample_rate // 1000
        wav.writeframes(b"\x00\x00" * channels * frame_count)
    return buffer.getvalue()


def _fake_ffmpeg(tmp_path, *, name: str = "ffmpeg"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_text(
        f'''#!{sys.executable}
import sys
import wave
from pathlib import Path

args = sys.argv[1:]
if "-version" in args:
    raise SystemExit(0)
input_path = Path(args[args.index("-i") + 1])
output_path = Path(args[-1])
audio_filter = args[args.index("-af") + 1]
filter_prefix = "aresample=16000,atrim=end_sample="
if not audio_filter.startswith(filter_prefix):
    raise AssertionError(audio_filter)
requested_frames = int(audio_filter[len(filter_prefix):])
with wave.open(str(input_path), "rb") as source:
    source_rate = source.getframerate()
    source_channels = source.getnchannels()
    source_width = source.getsampwidth()
    source_frames = source.getnframes()
    source_bytes = source.readframes(source_frames)
source_frame_width = source_channels * source_width
output_bytes = bytearray()
for output_index in range(requested_frames):
    source_index = min((output_index * source_rate) // 16000, source_frames - 1)
    start = source_index * source_frame_width
    sample = source_bytes[start:start + source_width]
    output_bytes.extend(sample[:2] if len(sample) >= 2 else b"\\x00\\x00")
with wave.open(str(output_path), "wb") as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(16000)
    output.writeframes(bytes(output_bytes))
''',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _fake_video_toolchain(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "configured tools"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = bin_dir / "ffmpeg"
    ffprobe = bin_dir / "ffprobe"
    for path in (ffmpeg, ffprobe):
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return ffmpeg, ffprobe


def _video_settings(tmp_path: Path, ffmpeg: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg),
        ffmpeg_timeout=5,
    )


def _patch_ytdlp_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else None,
    )


def _stream_document(*stream_types: str) -> str:
    return json.dumps(
        {"streams": [{"codec_type": value} for value in stream_types]}
    )


def _patch_audio_download(monkeypatch, content: bytes):
    calls: list[list[str]] = []

    def fake_run(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        calls.append(list(args))
        current_fd = os.open(".", os.O_RDONLY)
        try:
            os.fchdir(cwd_fd)
            __import__("pathlib").Path("span.wav").write_bytes(content)
        finally:
            os.fchdir(current_fd)
            os.close(current_fd)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_run)
    return calls


def test_resolve_audio_ffmpeg_accepts_command_name_and_uses_resolved_path(
    monkeypatch,
    tmp_path,
):
    ffmpeg = _fake_ffmpeg(tmp_path, name="ffmpeg command")
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: str(ffmpeg) if value == "ffmpeg-test" else None,
    )

    assert _resolve_audio_ffmpeg("ffmpeg-test") == ffmpeg.resolve()


def test_resolve_audio_ffmpeg_accepts_absolute_path_with_spaces(tmp_path):
    ffmpeg = _fake_ffmpeg(tmp_path / "bin with spaces")

    assert _resolve_audio_ffmpeg(str(ffmpeg)) == ffmpeg.resolve()


def test_resolve_audio_ffmpeg_rejects_missing_command(monkeypatch):
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which", lambda _value: None
    )

    with pytest.raises(AudioSpanError, match="FFmpeg が見つかりません"):
        _resolve_audio_ffmpeg("missing-ffmpeg")


def test_resolve_audio_ffmpeg_rejects_empty_path():
    with pytest.raises(AudioSpanError, match="パスが空"):
        _resolve_audio_ffmpeg("  ")


def test_resolve_audio_ffmpeg_rejects_missing_absolute_path(tmp_path):
    with pytest.raises(AudioSpanError, match="FFmpeg を確認できません"):
        _resolve_audio_ffmpeg(str(tmp_path / "missing" / "ffmpeg"))


def test_resolve_audio_ffmpeg_rejects_non_executable_file(tmp_path):
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"not executable")
    ffmpeg.chmod(0o644)

    with pytest.raises(AudioSpanError, match="実行権限"):
        _resolve_audio_ffmpeg(str(ffmpeg))


def test_resolve_audio_ffmpeg_rejects_directory(tmp_path):
    directory = tmp_path / "ffmpeg-dir"
    directory.mkdir()

    with pytest.raises(AudioSpanError, match="通常のファイル"):
        _resolve_audio_ffmpeg(str(directory))


def test_check_audio_ffmpeg_rejects_unlaunchable_executable(tmp_path):
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    ffmpeg.chmod(0o755)

    with pytest.raises(AudioSpanError, match="起動確認に失敗"):
        _check_audio_ffmpeg(ffmpeg, Settings(ffmpeg_timeout=5))


def test_prepare_audio_span_uses_configured_ffmpeg_when_path_ffmpeg_is_broken(
    monkeypatch,
    tmp_path,
):
    good_ffmpeg = _fake_ffmpeg(tmp_path / "configured ffmpeg")
    broken_bin = tmp_path / "broken-bin"
    broken_bin.mkdir()
    broken_ffmpeg = _fake_ffmpeg(broken_bin)
    broken_ffmpeg.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    broken_ffmpeg.chmod(0o755)
    monkeypatch.setenv("PATH", str(broken_bin))
    settings = Settings(
        data_dir=tmp_path / "data",
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(good_ffmpeg),
    )
    content = _wav_bytes()
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else None,
    )

    def fake_run(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        calls.append(list(args))
        current_fd = os.open(".", os.O_RDONLY)
        try:
            os.fchdir(cwd_fd)
            __import__("pathlib").Path("span.wav").write_bytes(content)
        finally:
            os.fchdir(current_fd)
            os.close(current_fd)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_run)

    prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert calls
    command = calls[0]
    assert command[command.index("--ffmpeg-location") + 1] == str(
        good_ffmpeg.resolve()
    )
    assert str(broken_bin) not in command


def test_prepare_audio_span_rejects_unlaunchable_ffmpeg_before_ytdlp(
    monkeypatch,
    tmp_path,
):
    broken_ffmpeg = _fake_ffmpeg(tmp_path)
    broken_ffmpeg.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    broken_ffmpeg.chmod(0o755)
    settings = Settings(
        data_dir=tmp_path / "data",
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(broken_ffmpeg),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else None,
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("FFmpeg fail closed 前に yt-dlp を呼び出してはいけない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fail_if_called)

    with pytest.raises(AudioSpanError, match="起動確認に失敗"):
        prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)


def test_download_video_uses_configured_ffmpeg_when_path_ffmpeg_is_broken(
    monkeypatch,
    tmp_path,
):
    ffmpeg, ffprobe = _fake_video_toolchain(tmp_path)
    broken_bin = tmp_path / "broken-path"
    broken_bin.mkdir()
    (broken_bin / "ffmpeg").write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    (broken_bin / "ffmpeg").chmod(0o755)
    monkeypatch.setenv("PATH", str(broken_bin))
    settings = _video_settings(tmp_path, ffmpeg)
    output_dir = settings.data_dir / VIDEO_ID / "clips" / "source"
    ytdlp_calls: list[list[str]] = []
    process_calls: list[list[str]] = []
    _patch_ytdlp_available(monkeypatch)

    def fake_ytdlp(args, _settings, *, timeout=None, **_kwargs):
        ytdlp_calls.append(list(args))
        (output_dir / f"{VIDEO_ID}.mp4").write_bytes(b"merged av")
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_process(command, **kwargs):
        process_calls.append(list(command))
        assert kwargs["shell"] is False
        if command == [str(ffmpeg.resolve()), "-hide_banner", "-version"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[0] == str(ffprobe.resolve())
        return subprocess.CompletedProcess(
            command, 0, _stream_document("video", "audio"), ""
        )

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_ytdlp)
    monkeypatch.setattr("yt_live_kit.services.ytdlp.subprocess.run", fake_process)

    result = download_video(URL, output_dir, settings)

    assert result == output_dir / f"{VIDEO_ID}.mp4"
    assert ytdlp_calls
    command = ytdlp_calls[0]
    assert command[command.index("--ffmpeg-location") + 1] == str(ffmpeg.resolve())
    assert process_calls[-1][0] == str(ffprobe.resolve())
    assert str(broken_bin) not in command


def test_download_video_rejects_unlaunchable_ffmpeg_before_ytdlp(
    monkeypatch,
    tmp_path,
):
    ffmpeg, _ffprobe = _fake_video_toolchain(tmp_path)
    ffmpeg.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    settings = _video_settings(tmp_path, ffmpeg)
    _patch_ytdlp_available(monkeypatch)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("FFmpeg fail closed 前に yt-dlp を呼び出してはいけない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fail_if_called)

    with pytest.raises(YtdlpError, match="起動確認に失敗"):
        download_video(URL, settings.data_dir / VIDEO_ID / "clips" / "source", settings)


def test_download_video_rejects_missing_colocated_ffprobe_before_ytdlp(
    monkeypatch,
    tmp_path,
):
    ffmpeg, ffprobe = _fake_video_toolchain(tmp_path)
    ffprobe.unlink()
    settings = _video_settings(tmp_path, ffmpeg)
    _patch_ytdlp_available(monkeypatch)
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("ffprobe fail closed 前に yt-dlp を呼び出してはいけない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fail_if_called)

    with pytest.raises(YtdlpError, match="ffprobe が見つかりません"):
        download_video(URL, settings.data_dir / VIDEO_ID / "clips" / "source", settings)


def test_download_video_rejects_unmerged_sidecars_and_keeps_them(
    monkeypatch,
    tmp_path,
):
    ffmpeg, ffprobe = _fake_video_toolchain(tmp_path)
    settings = _video_settings(tmp_path, ffmpeg)
    output_dir = settings.data_dir / VIDEO_ID / "clips" / "source"
    _patch_ytdlp_available(monkeypatch)

    def fake_ytdlp(args, _settings, **_kwargs):
        (output_dir / f"{VIDEO_ID}.f399.mp4").write_bytes(b"video only")
        (output_dir / f"{VIDEO_ID}.f140.m4a").write_bytes(b"audio only")
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_process(command, **_kwargs):
        if command[0] == str(ffmpeg.resolve()):
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[0] == str(ffprobe.resolve())
        return subprocess.CompletedProcess(command, 0, _stream_document("video"), "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_ytdlp)
    monkeypatch.setattr("yt_live_kit.services.ytdlp.subprocess.run", fake_process)

    with pytest.raises(YtdlpError, match="映像と音声が結合"):
        download_video(URL, output_dir, settings)

    assert (output_dir / f"{VIDEO_ID}.f399.mp4").read_bytes() == b"video only"
    assert (output_dir / f"{VIDEO_ID}.f140.m4a").read_bytes() == b"audio only"


def test_download_video_leaves_existing_sidecars_for_ytdlp_local_merge(
    monkeypatch,
    tmp_path,
):
    ffmpeg, ffprobe = _fake_video_toolchain(tmp_path)
    settings = _video_settings(tmp_path, ffmpeg)
    output_dir = settings.data_dir / VIDEO_ID / "clips" / "source"
    output_dir.mkdir(parents=True)
    video_sidecar = output_dir / f"{VIDEO_ID}.f399.mp4"
    audio_sidecar = output_dir / f"{VIDEO_ID}.f140.m4a"
    video_sidecar.write_bytes(b"existing video")
    audio_sidecar.write_bytes(b"existing audio")
    _patch_ytdlp_available(monkeypatch)

    def fake_ytdlp(args, _settings, **_kwargs):
        assert video_sidecar.read_bytes() == b"existing video"
        assert audio_sidecar.read_bytes() == b"existing audio"
        assert "--force-overwrites" not in args
        assert "--keep-video" in args
        (output_dir / f"{VIDEO_ID}.mp4").write_bytes(b"locally merged")
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_process(command, **_kwargs):
        if command[0] == str(ffmpeg.resolve()):
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[0] == str(ffprobe.resolve())
        return subprocess.CompletedProcess(
            command, 0, _stream_document("video", "audio"), ""
        )

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_ytdlp)
    monkeypatch.setattr("yt_live_kit.services.ytdlp.subprocess.run", fake_process)

    result = download_video(URL, output_dir, settings)

    assert result == output_dir / f"{VIDEO_ID}.mp4"
    assert video_sidecar.read_bytes() == b"existing video"
    assert audio_sidecar.read_bytes() == b"existing audio"


def test_download_video_selects_first_deterministic_av_candidate(
    monkeypatch,
    tmp_path,
):
    ffmpeg, ffprobe = _fake_video_toolchain(tmp_path)
    settings = _video_settings(tmp_path, ffmpeg)
    output_dir = settings.data_dir / VIDEO_ID / "clips" / "source"
    probed: list[str] = []
    _patch_ytdlp_available(monkeypatch)

    def fake_ytdlp(args, _settings, **_kwargs):
        for name in (
            f"{VIDEO_ID}.f399.mp4",
            f"{VIDEO_ID}.mkv",
            f"{VIDEO_ID}.mp4",
            "unrelated.mp4",
        ):
            (output_dir / name).write_bytes(name.encode())
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_process(command, **_kwargs):
        if command[0] == str(ffmpeg.resolve()):
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[0] == str(ffprobe.resolve())
        candidate = Path(command[-1])
        probed.append(candidate.name)
        streams = (
            ("video", "audio")
            if candidate.suffix == ".mkv"
            else ("video",)
        )
        return subprocess.CompletedProcess(command, 0, _stream_document(*streams), "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_ytdlp)
    monkeypatch.setattr("yt_live_kit.services.ytdlp.subprocess.run", fake_process)

    result = download_video(URL, output_dir, settings)

    assert result == output_dir / f"{VIDEO_ID}.mkv"
    assert probed == [f"{VIDEO_ID}.mp4", f"{VIDEO_ID}.mkv"]


@pytest.mark.parametrize(
    ("failure", "error_match"),
    [
        ("nonzero", "映像・音声を確認できません"),
        ("timeout", "タイムアウト"),
        ("invalid_json", "確認結果が不正"),
        ("invalid_schema", "確認結果が不正"),
    ],
)
def test_download_video_ffprobe_failures_are_fail_closed(
    monkeypatch,
    tmp_path,
    failure,
    error_match,
):
    ffmpeg, ffprobe = _fake_video_toolchain(tmp_path)
    settings = _video_settings(tmp_path, ffmpeg)
    output_dir = settings.data_dir / VIDEO_ID / "clips" / "source"
    _patch_ytdlp_available(monkeypatch)

    def fake_ytdlp(args, _settings, **_kwargs):
        (output_dir / f"{VIDEO_ID}.mp4").write_bytes(b"candidate")
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_process(command, **_kwargs):
        if command[0] == str(ffmpeg.resolve()):
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[0] == str(ffprobe.resolve())
        if failure == "nonzero":
            return subprocess.CompletedProcess(command, 7, "", "probe failed")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, settings.ffmpeg_timeout)
        if failure == "invalid_json":
            return subprocess.CompletedProcess(command, 0, "not json", "")
        return subprocess.CompletedProcess(command, 0, '{"streams": [{}]}', "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_ytdlp)
    monkeypatch.setattr("yt_live_kit.services.ytdlp.subprocess.run", fake_process)

    with pytest.raises(YtdlpError, match=error_match):
        download_video(URL, output_dir, settings)


def test_download_video_returncode_zero_without_video_candidate_is_failure(
    monkeypatch,
    tmp_path,
):
    ffmpeg, _ffprobe = _fake_video_toolchain(tmp_path)
    settings = _video_settings(tmp_path, ffmpeg)
    output_dir = settings.data_dir / VIDEO_ID / "clips" / "source"
    _patch_ytdlp_available(monkeypatch)
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp._run_ytdlp",
        lambda args, _settings, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    with pytest.raises(YtdlpError, match="動画ファイルが見つかりません"):
        download_video(URL, output_dir, settings)


def _mock_fetch_dependencies(monkeypatch, metadata: dict) -> None:
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which", lambda _: "/usr/bin/yt-dlp"
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.get_ytdlp_version", lambda _: "2026.07.04"
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp._fetch_metadata", lambda _url, _settings: metadata
    )


def test_get_ytdlp_binary_identity_returns_resolved_stat_values(tmp_path):
    binary = tmp_path / "yt-dlp"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o755)
    expected = binary.resolve().stat()

    identity = get_ytdlp_binary_identity(str(binary))

    assert identity.resolved_path == str(binary.resolve())
    assert identity.device == expected.st_dev
    assert identity.inode == expected.st_ino
    assert identity.size == expected.st_size
    assert identity.mtime_ns == expected.st_mtime_ns
    assert identity.is_missing is False


def test_get_ytdlp_binary_identity_uses_deterministic_missing_sentinel(monkeypatch):
    monkeypatch.setattr("yt_live_kit.services.ytdlp.shutil.which", lambda _: None)

    first = get_ytdlp_binary_identity("missing-yt-dlp")
    second = get_ytdlp_binary_identity("missing-yt-dlp")

    assert first == MISSING_YTDLP_BINARY_IDENTITY
    assert second == first
    assert first.is_missing is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=IJvd6k6ZmUo", "IJvd6k6ZmUo"),
        ("https://youtu.be/IJvd6k6ZmUo", "IJvd6k6ZmUo"),
        ("IJvd6k6ZmUo", "IJvd6k6ZmUo"),
    ],
)
def test_extract_video_id(url: str, expected: str):
    assert extract_video_id(url) == expected


def test_find_subtitle_file_ja_vtt_records_ja_lang(tmp_path):
    """{video_id}.ja.vtt は subtitle_lang='ja' として記録される."""
    subtitles_dir = tmp_path / "subtitles"
    subtitles_dir.mkdir()
    video_id = "IJvd6k6ZmUo"
    vtt_path = subtitles_dir / f"{video_id}.ja.vtt"
    vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")

    path, lang = _find_subtitle_file(subtitles_dir, video_id)

    assert path == vtt_path
    assert lang == "ja"


def test_find_subtitle_file_ja_orig_takes_priority(tmp_path):
    """ja-orig と ja が両方ある場合は ja-orig を優先する."""
    subtitles_dir = tmp_path / "subtitles"
    subtitles_dir.mkdir()
    video_id = "IJvd6k6ZmUo"
    orig_path = subtitles_dir / f"{video_id}.ja-orig.vtt"
    ja_path = subtitles_dir / f"{video_id}.ja.vtt"
    orig_path.write_text("WEBVTT\n\n", encoding="utf-8")
    ja_path.write_text("WEBVTT\n\n", encoding="utf-8")

    path, lang = _find_subtitle_file(subtitles_dir, video_id)

    assert path == orig_path
    assert lang == "ja-orig"


@patch("yt_live_kit.services.ytdlp.subprocess.run")
def test_run_ytdlp_timeout_raises_ytdlp_error(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=300)
    settings = Settings(ytdlp_timeout=300)

    with pytest.raises(YtdlpError, match="タイムアウト"):
        _run_ytdlp(["--version"], settings)


@patch("yt_live_kit.services.ytdlp.subprocess.run")
def test_run_ytdlp_download_uses_download_timeout(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    settings = Settings(ytdlp_timeout=300, download_timeout=7200)

    _run_ytdlp(["--skip-download", "https://example.com"], settings, timeout=settings.download_timeout)

    assert mock_run.call_args.kwargs["timeout"] == 7200


@patch("yt_live_kit.services.ytdlp.subprocess.run")
def test_run_ytdlp_without_cwd_fd_executes_yt_dlp_directly(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    settings = Settings(ytdlp_path="yt-dlp")

    _run_ytdlp(["--version"], settings)

    assert mock_run.call_args.args[0] == ["yt-dlp", "--version"]
    assert "preexec_fn" not in mock_run.call_args.kwargs


@pytest.mark.skipif(os.name != "posix", reason="directory FD cwd requires POSIX")
@patch("yt_live_kit.services.ytdlp.subprocess.run")
def test_run_ytdlp_cwd_fd_uses_exec_wrapper_and_passes_fd(mock_run, tmp_path):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    directory = tmp_path / "incoming"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        settings = Settings(ytdlp_path="yt-dlp")

        _run_ytdlp(
            ["--write-auto-sub", "https://example.com/watch?v=123"],
            settings,
            pass_fds=(descriptor,),
            cwd_fd=descriptor,
        )
    finally:
        os.close(descriptor)

    command = mock_run.call_args.args[0]
    assert command[:2] == [sys.executable, "-c"]
    assert "os.fchdir(directory_fd)" in command[2]
    assert "os.execvp(program, [program, *sys.argv[3:]])" in command[2]
    assert command[3:] == [
        str(descriptor),
        "yt-dlp",
        "--write-auto-sub",
        "https://example.com/watch?v=123",
    ]
    assert mock_run.call_args.kwargs["pass_fds"] == (descriptor,)
    assert "preexec_fn" not in mock_run.call_args.kwargs


@pytest.mark.skipif(os.name != "posix", reason="directory FD cwd requires POSIX")
def test_run_ytdlp_cwd_fd_allows_relative_output_in_fd_directory(tmp_path):
    incoming = tmp_path / "incoming"
    outside = tmp_path / "outside"
    incoming.mkdir()
    outside.mkdir()
    descriptor = os.open(incoming, os.O_RDONLY | os.O_DIRECTORY)
    try:
        settings = Settings(ytdlp_path=sys.executable, ytdlp_timeout=30)
        result = _run_ytdlp(
            [
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('relative-output.txt').write_text('ok', encoding='utf-8')"
                ),
            ],
            settings,
            cwd_fd=descriptor,
        )
    finally:
        os.close(descriptor)

    assert result.returncode == 0
    assert (incoming / "relative-output.txt").read_text(encoding="utf-8") == "ok"
    assert not (outside / "relative-output.txt").exists()


def test_download_subtitles_passes_stable_incoming_fd_to_process(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    output_dir = tmp_path / VIDEO_ID / "subtitles" / ".incoming-test"
    output_dir.mkdir(parents=True)

    def fake_run(args, _settings, **kwargs):
        assert kwargs["pass_fds"]
        descriptor = kwargs["pass_fds"][0]
        assert kwargs["cwd_fd"] == descriptor
        (output_dir / f"{VIDEO_ID}.ja.vtt").write_text(VTT_ONE, encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_run)

    _download_subtitles(URL, output_dir, settings)

    assert (output_dir / f"{VIDEO_ID}.ja.vtt").read_text(encoding="utf-8") == VTT_ONE


@patch("yt_live_kit.services.ytdlp.write_text_atomically")
def test_fetch_saves_meta_json_atomically(mock_write_atomic, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which", lambda _: "/usr/bin/yt-dlp"
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.get_ytdlp_version", lambda _: "2026.07.04"
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp._fetch_metadata",
        lambda _url, _settings: {
            "id": VIDEO_ID,
            "title": "テスト動画",
            "upload_date": "20260101",
            "duration": 3600,
        },
    )

    def fake_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / f"{VIDEO_ID}.ja.vtt").write_text(VTT_ONE, encoding="utf-8")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._download_subtitles", fake_download)

    settings = Settings(data_dir=tmp_path)
    meta = fetch(URL, settings)

    assert meta.id == VIDEO_ID
    mock_write_atomic.assert_called_once()
    path, text = mock_write_atomic.call_args.args
    assert path.name == "meta.json"
    assert VIDEO_ID in text


def test_fetch_bootstraps_canonical_and_source_metadata(tmp_path, monkeypatch):
    _mock_fetch_dependencies(
        monkeypatch,
        {"id": VIDEO_ID, "title": "テスト動画"},
    )

    def fake_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / f"{VIDEO_ID}.ja-orig.vtt").write_text(VTT_ONE, encoding="utf-8")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._download_subtitles", fake_download)

    settings = Settings(data_dir=tmp_path)
    fetch(URL, settings)

    subtitles_dir = tmp_path / VIDEO_ID / "subtitles"
    canonical = subtitles_dir / "ja.vtt"
    assert canonical.read_text(encoding="utf-8") == VTT_ONE
    source_files = sorted((subtitles_dir / "sources").glob("*.vtt"))
    metadata_files = sorted((subtitles_dir / "sources").glob("*.json"))
    assert len(source_files) == 1
    assert len(metadata_files) == 1
    assert source_files[0].read_text(encoding="utf-8") == VTT_ONE
    metadata = SubtitleSourceMetadata.model_validate_json(
        metadata_files[0].read_bytes()
    )
    expected_fingerprint, expected_content_hash = make_subtitle_source_fingerprint(
        VTT_ONE.encode("utf-8"),
        video_id=VIDEO_ID,
        language="ja-orig",
        source_url=URL,
        ytdlp_version="2026.07.04",
    )
    assert metadata.source_fingerprint == expected_fingerprint
    assert metadata.content_sha256 == expected_content_hash
    assert metadata.language == "ja-orig"
    assert metadata.source_path == (
        f"subtitles/sources/{expected_fingerprint}.vtt"
    )
    assert metadata.canonical_path == "subtitles/ja.vtt"
    assert metadata.canonical_content_sha256 == expected_content_hash
    assert metadata.canonical_compatible is True
    assert not list(subtitles_dir.glob(".incoming-*"))


def test_fetch_does_not_overwrite_existing_canonical_on_retrieve(
    tmp_path, monkeypatch
):
    _mock_fetch_dependencies(monkeypatch, {"id": VIDEO_ID, "title": "テスト動画"})
    incoming = {"content": VTT_ONE}

    def fake_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / f"{VIDEO_ID}.ja.vtt").write_text(
            incoming["content"], encoding="utf-8"
        )

    monkeypatch.setattr("yt_live_kit.services.ytdlp._download_subtitles", fake_download)
    settings = Settings(data_dir=tmp_path)
    fetch(URL, settings)

    canonical = tmp_path / VIDEO_ID / "subtitles" / "ja.vtt"
    before_bytes = canonical.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    before_stat = canonical.stat()

    incoming["content"] = VTT_TWO
    fetch(URL, settings)

    assert canonical.read_bytes() == before_bytes
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == before_hash
    after_stat = canonical.stat()
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    source_files = sorted((canonical.parent / "sources").glob("*.vtt"))
    assert len(source_files) == 2
    assert {path.read_text(encoding="utf-8") for path in source_files} == {
        VTT_ONE,
        VTT_TWO,
    }
    source_metadata = [
        SubtitleSourceMetadata.model_validate_json(path.read_bytes())
        for path in sorted((canonical.parent / "sources").glob("*.json"))
    ]
    assert any(item.canonical_compatible is False for item in source_metadata)


@pytest.mark.parametrize(
    ("filename", "content", "error_match"),
    [
        (f"{VIDEO_ID}.ja.vtt", "WEBVTT\n\n", "有効なキュー"),
        (f"{VIDEO_ID}.ja.vtt", "not vtt", "WebVTT 形式"),
        (
            f"{VIDEO_ID}.ja.vtt",
            "WEBVTT\n\n1\n00:60:00.000 --> 00:61:00.000\n不正な時刻\n",
            "時刻",
        ),
        (f"{VIDEO_ID}.en.vtt", VTT_ONE, "日本語字幕"),
    ],
)
def test_fetch_rejects_invalid_incoming_without_touching_existing_outputs(
    tmp_path, monkeypatch, filename, content, error_match
):
    _mock_fetch_dependencies(monkeypatch, {"id": VIDEO_ID, "title": "テスト動画"})
    video_dir = tmp_path / VIDEO_ID
    subtitles_dir = video_dir / "subtitles"
    subtitles_dir.mkdir(parents=True)
    canonical = subtitles_dir / "ja.vtt"
    canonical.write_text(VTT_ONE, encoding="utf-8")
    transcript_path = video_dir / "transcript" / "full.txt"
    transcript_path.parent.mkdir()
    transcript_path.write_text("既存 downstream\n", encoding="utf-8")
    before_canonical = canonical.read_bytes()
    before_transcript = transcript_path.read_bytes()

    def fake_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / filename).write_text(content, encoding="utf-8")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._download_subtitles", fake_download)

    with pytest.raises(YtdlpError, match=error_match):
        fetch(URL, Settings(data_dir=tmp_path))

    assert canonical.read_bytes() == before_canonical
    assert transcript_path.read_bytes() == before_transcript
    assert not list(subtitles_dir.glob(".incoming-*"))
    assert not (subtitles_dir / "sources").exists()


def test_fetch_download_failure_cleans_incoming_and_preserves_existing(
    tmp_path, monkeypatch
):
    _mock_fetch_dependencies(monkeypatch, {"id": VIDEO_ID, "title": "テスト動画"})
    subtitles_dir = tmp_path / VIDEO_ID / "subtitles"
    subtitles_dir.mkdir(parents=True)
    canonical = subtitles_dir / "ja.vtt"
    canonical.write_text(VTT_ONE, encoding="utf-8")

    def failed_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / f"{VIDEO_ID}.ja.vtt.part").write_bytes(b"partial")
        raise RuntimeError("simulated process failure")

    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp._download_subtitles", failed_download
    )

    with pytest.raises(YtdlpError, match="予期せず中断"):
        fetch(URL, Settings(data_dir=tmp_path))

    assert canonical.read_text(encoding="utf-8") == VTT_ONE
    assert not list(subtitles_dir.glob(".incoming-*"))


def test_fetch_atomic_source_failure_keeps_existing_canonical(
    tmp_path, monkeypatch
):
    _mock_fetch_dependencies(monkeypatch, {"id": VIDEO_ID, "title": "テスト動画"})
    subtitles_dir = tmp_path / VIDEO_ID / "subtitles"
    subtitles_dir.mkdir(parents=True)
    canonical = subtitles_dir / "ja.vtt"
    canonical.write_text(VTT_ONE, encoding="utf-8")
    before = canonical.read_bytes()

    def fake_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / f"{VIDEO_ID}.ja.vtt").write_text(VTT_TWO, encoding="utf-8")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._download_subtitles", fake_download)
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.os.link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("atomic failure")),
    )

    with pytest.raises(YtdlpError, match="原子的に保存"):
        fetch(URL, Settings(data_dir=tmp_path))

    assert canonical.read_bytes() == before


def test_fetch_metadata_atomic_failure_does_not_leave_unpaired_source(
    tmp_path, monkeypatch
):
    _mock_fetch_dependencies(monkeypatch, {"id": VIDEO_ID, "title": "テスト動画"})
    subtitles_dir = tmp_path / VIDEO_ID / "subtitles"
    subtitles_dir.mkdir(parents=True)
    canonical = subtitles_dir / "ja.vtt"
    canonical.write_text(VTT_ONE, encoding="utf-8")

    def fake_download(_url: str, output_dir, _settings: Settings) -> None:
        (output_dir / f"{VIDEO_ID}.ja.vtt").write_text(VTT_TWO, encoding="utf-8")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._download_subtitles", fake_download)
    real_link = os.link
    calls = 0

    def fail_on_metadata(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("metadata atomic failure")
        return real_link(*args, **kwargs)

    monkeypatch.setattr("yt_live_kit.services.ytdlp.os.link", fail_on_metadata)

    with pytest.raises(YtdlpError, match="原子的に保存"):
        fetch(URL, Settings(data_dir=tmp_path))

    sources_dir = subtitles_dir / "sources"
    assert not list(sources_dir.glob("*.vtt"))
    assert not list(sources_dir.glob("*.json"))
    assert not list(sources_dir.glob("*.pending"))
    assert canonical.read_text(encoding="utf-8") == VTT_ONE


def test_source_fingerprint_changes_with_content_and_provenance():
    first = make_subtitle_source_fingerprint(
        b"same",
        video_id=VIDEO_ID,
        language="ja",
        source_url=URL,
        ytdlp_version="2026.07.04",
    )
    changed_content = make_subtitle_source_fingerprint(
        b"changed",
        video_id=VIDEO_ID,
        language="ja",
        source_url=URL,
        ytdlp_version="2026.07.04",
    )
    changed_provenance = make_subtitle_source_fingerprint(
        b"same",
        video_id=VIDEO_ID,
        language="ja-orig",
        source_url=URL,
        ytdlp_version="2026.07.04",
    )

    assert first[0] != changed_content[0]
    assert first[0] != changed_provenance[0]
    assert first[1] == hashlib.sha256(b"same").hexdigest()


def test_prepare_audio_span_uses_selected_audio_only_range_and_persistent_cache(
    monkeypatch,
    tmp_path,
):
    video_id = "IJvd6k6ZmUo"
    ffmpeg_path = _fake_ffmpeg(tmp_path / "configured ffmpeg", name="ffmpeg binary")
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ytdlp_timeout=17,
        ffmpeg_path=str(ffmpeg_path),
    )
    calls: list[list[str]] = []
    content = _wav_bytes(duration_ms=12_500)
    expected_normalized = _wav_bytes(duration_ms=11_861)

    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )

    def fake_run(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        calls.append(list(args))
        assert timeout == 17
        assert cwd_fd is not None
        assert pass_fds == (cwd_fd,)
        current_fd = os.open(".", os.O_RDONLY)
        try:
            os.fchdir(cwd_fd)
            __import__("pathlib").Path("span.wav").write_bytes(content)
        finally:
            os.fchdir(current_fd)
            os.close(current_fd)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_run)
    real_subprocess_run = subprocess.run
    ffmpeg_calls = []

    def spy_subprocess_run(*args, **kwargs):
        command = args[0]
        if isinstance(command, list) and command and command[0] == str(ffmpeg_path.resolve()):
            ffmpeg_calls.append((command, kwargs))
        return real_subprocess_run(*args, **kwargs)

    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.subprocess.run",
        spy_subprocess_run,
    )

    first = prepare_audio_span(
        video_id,
        AudioSpanRange(12_345, 23_456, padding_before_ms=250, padding_after_ms=500),
        settings,
        source_metadata={"title": "fixture"},
    )

    assert first.cache_hit is False
    assert first.audio_bytes == expected_normalized
    assert first.sample_rate == 16_000
    assert first.channel == 1
    assert first.codec == "pcm_s16le"
    with wave.open(io.BytesIO(first.audio_bytes), "rb") as normalized:
        assert normalized.getframerate() == 16_000
        assert normalized.getnchannels() == 1
        assert normalized.getsampwidth() == 2
        assert normalized.getnframes() == 11_861 * 16
    metadata = json.loads(first.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["audio"]["frames"] == 11_861 * 16
    assert metadata["audio"]["duration_ms"] == 11_861
    assert metadata["audio"]["sha256"] == hashlib.sha256(expected_normalized).hexdigest()
    assert first.audio_input_fingerprint == hashlib.sha256(expected_normalized).hexdigest()
    assert first.range.requested_start_ms == 12_095
    assert first.range.requested_end_ms == 23_956
    assert len(calls) == 1
    assert len(ffmpeg_calls) == 2
    normalize_command, normalize_kwargs = ffmpeg_calls[-1]
    assert normalize_command[0] == str(ffmpeg_path.resolve())
    assert normalize_command[normalize_command.index("-af") + 1] == (
        "aresample=16000,atrim=end_sample=189776"
    )
    assert normalize_kwargs["shell"] is False
    assert normalize_kwargs["timeout"] == settings.ffmpeg_timeout
    command = calls[0]
    assert command[command.index("-f") + 1] == "bestaudio"
    assert command[command.index("--ffmpeg-location") + 1] == str(ffmpeg_path.resolve())
    assert "--download-sections" in command
    section = command[command.index("--download-sections") + 1]
    assert section == "*00:00:12.095-00:00:23.956"
    assert "bestvideo" not in " ".join(command)
    assert ".mp4" not in " ".join(command)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("cache hit では yt-dlp を再実行しない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fail_if_called)

    def fail_preflight(*args, **kwargs):
        raise AssertionError("cache hit では FFmpeg の解決・preflight を再実行しない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._resolve_audio_ffmpeg", fail_preflight)
    monkeypatch.setattr("yt_live_kit.services.ytdlp._check_audio_ffmpeg", fail_preflight)
    second = prepare_audio_span(
        video_id,
        AudioSpanRange(12_345, 23_456, padding_before_ms=250, padding_after_ms=500),
        settings,
        source_metadata={"title": "fixture"},
    )
    assert second.cache_hit is True
    assert second.audio_input_fingerprint == first.audio_input_fingerprint


def test_prepare_audio_span_rejects_oversized_legacy_cache_and_regenerates(
    monkeypatch,
    tmp_path,
):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path / "configured ffmpeg")),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=2_000))

    first = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)
    oversized = _wav_bytes(duration_ms=2_000)
    first.path.write_bytes(oversized)
    metadata_path = first.path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["audio"]["bytes"] = len(oversized)
    metadata["audio"]["sha256"] = hashlib.sha256(oversized).hexdigest()
    metadata["audio"]["frames"] = 2_000 * 16
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    second = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert second.cache_hit is False
    assert len(calls) == 2
    with wave.open(io.BytesIO(second.audio_bytes), "rb") as normalized:
        assert normalized.getnframes() == 1_000 * 16
    assert list(first.path.parent.glob(f".{first.path.name}.corrupt-*"))


def test_prepare_audio_span_rejects_undersized_download_without_normalizing(
    monkeypatch,
    tmp_path,
):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path)),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=999))

    with pytest.raises(AudioSpanError, match="必要長未満"):
        prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert len(calls) == 1


@pytest.mark.parametrize(
    ("failure", "error_match"),
    [
        ("nonzero", "正規化に失敗"),
        ("timeout", "タイムアウト"),
        ("missing", "出力がありません"),
        ("malformed", "形式が不正"),
        ("oversized", "frame 数"),
        ("wrong_format", "frame 数"),
    ],
)
def test_prepare_audio_span_ffmpeg_output_failures_are_fail_closed(
    monkeypatch,
    tmp_path,
    failure,
    error_match,
):
    ffmpeg_path = _fake_ffmpeg(tmp_path / "ffmpeg path with spaces")
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else None,
    )
    download_calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=2_000))
    ffmpeg_calls = []

    def failing_subprocess_run(command, *args, **kwargs):
        assert isinstance(command, list)
        assert command[0] == str(ffmpeg_path.resolve())
        assert kwargs["shell"] is False
        ffmpeg_calls.append(command)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if failure == "nonzero":
            return subprocess.CompletedProcess(command, 1, "", "conversion failed")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, settings.ffmpeg_timeout)
        if failure == "malformed":
            Path(command[-1]).write_bytes(b"not a wav")
        elif failure == "oversized":
            Path(command[-1]).write_bytes(_wav_bytes(duration_ms=2_000))
        elif failure == "wrong_format":
            Path(command[-1]).write_bytes(
                _wav_bytes(sample_rate=8_000, duration_ms=2_000)
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.subprocess.run",
        failing_subprocess_run,
    )

    with pytest.raises(AudioSpanError, match=error_match):
        prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert len(download_calls) == 1
    assert len(ffmpeg_calls) == 2


def test_prepare_audio_span_corrupt_cache_is_a_miss(monkeypatch, tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path)),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    content = _wav_bytes()
    calls = 0

    def fake_run(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        nonlocal calls
        calls += 1
        current_fd = os.open(".", os.O_RDONLY)
        try:
            os.fchdir(cwd_fd)
            __import__("pathlib").Path("span.wav").write_bytes(content)
        finally:
            os.fchdir(current_fd)
            os.close(current_fd)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_run)
    first = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)
    metadata_path = first.path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["audio"]["sha256"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    second = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)
    assert second.cache_hit is False
    assert calls == 2


def test_prepare_audio_span_invalid_wav_cache_is_quarantined_and_regenerated(
    monkeypatch,
    tmp_path,
):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path)),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    content = _wav_bytes()
    calls = 0

    def fake_run(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        nonlocal calls
        calls += 1
        current_fd = os.open(".", os.O_RDONLY)
        try:
            os.fchdir(cwd_fd)
            __import__("pathlib").Path("span.wav").write_bytes(content)
        finally:
            os.fchdir(current_fd)
            os.close(current_fd)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fake_run)
    first = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)
    first.path.write_bytes(b"this is not a wav")

    second = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)
    assert second.cache_hit is False
    assert second.audio_bytes == _wav_bytes(duration_ms=1_000)
    assert calls == 2
    assert list(first.path.parent.glob(f".{first.path.name}.corrupt-*"))


def test_prepare_audio_span_rejects_invalid_generated_wav_with_audio_only_diagnostic(
    monkeypatch,
    tmp_path,
):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path)),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )

    def invalid_run(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        current_fd = os.open(".", os.O_RDONLY)
        try:
            os.fchdir(cwd_fd)
            __import__("pathlib").Path("span.wav").write_bytes(b"this is not a wav")
        finally:
            os.fchdir(current_fd)
            os.close(current_fd)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", invalid_run)
    with pytest.raises(AudioSpanError, match="形式"):
        prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)


def test_prepare_audio_span_does_not_fallback_to_video_format(monkeypatch, tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path)),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )

    def no_audio_format(args, _settings, *, timeout=None, pass_fds=(), cwd_fd=None):
        assert args[args.index("-f") + 1] == "bestaudio"
        assert "bestaudio/best" not in args
        assert not any("bestvideo" in item or ".mp4" in item for item in args)
        return subprocess.CompletedProcess(args, 1, "", "audio-only unavailable")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", no_audio_format)
    with pytest.raises(AudioSpanError, match="audio-only"):
        prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)


def _fake_source_toolchain(
    tmp_path: Path,
    *,
    misalign_reference_ms: int = 0,
    duration_ms: int = 3_600_000,
    stream_types: tuple[str, ...] = ("video", "audio"),
) -> tuple[Path, Path]:
    """accurate seek 切り出しと正規化の両方を扱う fake FFmpeg / ffprobe を作る.

    切り出しは絶対 sample 位置だけで決まる決定的な波形を返すため、anchor が
    違っても内容が一致する。``misalign_reference_ms`` を与えると別 anchor 側
    だけがずれ、開始位置検証の失敗経路を再現できる。
    """

    bin_dir = tmp_path / "source tools"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = bin_dir / "ffmpeg"
    ffmpeg.write_text(
        f'''#!{sys.executable}
import re
import sys
import wave
from pathlib import Path

args = sys.argv[1:]
if "-version" in args:
    raise SystemExit(0)
output_path = Path(args[-1])
audio_filter = args[args.index("-af") + 1]
cut = re.search(r"atrim=start_sample=(\\d+):end_sample=(\\d+)", audio_filter)
if cut is None:
    prefix = "aresample=16000,atrim=end_sample="
    if not audio_filter.startswith(prefix):
        raise AssertionError(audio_filter)
    requested_frames = int(audio_filter[len(prefix):])
    input_path = Path(args[args.index("-i") + 1])
    with wave.open(str(input_path), "rb") as source:
        source_frames = source.getnframes()
        source_bytes = source.readframes(source_frames)
    payload = bytearray()
    for index in range(requested_frames):
        start = min(index, source_frames - 1) * 2
        payload.extend(source_bytes[start:start + 2] or b"\\x00\\x00")
else:
    start_sample = int(cut.group(1))
    end_sample = int(cut.group(2))
    seconds, _, milliseconds = args[args.index("-ss") + 1].partition(".")
    seek_ms = int(seconds) * 1000 + int(milliseconds or 0)
    shift_ms = {misalign_reference_ms} if start_sample else 0
    base = (seek_ms + shift_ms) * 16 + start_sample
    payload = bytearray()
    for index in range(end_sample - start_sample):
        value = ((base + index) * 7919) % 20001 - 10000
        payload.extend(int(value).to_bytes(2, "little", signed=True))
with wave.open(str(output_path), "wb") as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(16000)
    output.writeframes(bytes(payload))
''',
        encoding="utf-8",
    )
    ffmpeg.chmod(0o755)

    streams = ",".join(f'{{"codec_type":"{item}"}}' for item in stream_types)
    ffprobe = bin_dir / "ffprobe"
    ffprobe.write_text(
        "#!/bin/sh\n"
        f'printf \'{{"streams":[{streams}],'
        f'"format":{{"duration":"{duration_ms / 1000:.3f}"}}}}\\n\'\n',
        encoding="utf-8",
    )
    ffprobe.chmod(0o755)
    return ffmpeg, ffprobe


def _write_local_source(data_dir: Path, video_id: str) -> Path:
    source_dir = data_dir / video_id / "clips" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / f"{video_id}.mp4"
    source_path.write_bytes(b"fake source container")
    return source_path


def test_prepare_audio_span_prefers_local_source_with_accurate_seek(
    monkeypatch,
    tmp_path,
):
    data_dir = tmp_path / "data"
    ffmpeg_path, _ = _fake_source_toolchain(tmp_path)
    settings = Settings(
        data_dir=data_dir,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    source_path = _write_local_source(data_dir, "IJvd6k6ZmUo")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("ローカル source があるとき yt-dlp は呼ばない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fail_if_called)
    real_subprocess_run = subprocess.run
    cut_commands: list[list[str]] = []

    def spy_subprocess_run(*args, **kwargs):
        command = args[0]
        if isinstance(command, list) and "-accurate_seek" in command:
            cut_commands.append(list(command))
        return real_subprocess_run(*args, **kwargs)

    monkeypatch.setattr("yt_live_kit.services.ytdlp.subprocess.run", spy_subprocess_run)

    result = prepare_audio_span("IJvd6k6ZmUo", (1_179_000, 1_195_000), settings)

    assert result.cache_hit is False
    assert result.audio_route == "local_source_accurate_seek"
    assert result.alignment["verified"] is True
    assert result.alignment["method"] == "cross_anchor_pcm_match"
    assert result.alignment["reference_seek_ms"] == 1_174_000
    with wave.open(io.BytesIO(result.audio_bytes), "rb") as normalized:
        assert normalized.getnframes() == 16_000 * 16
        assert normalized.getframerate() == 16_000

    assert len(cut_commands) == 2
    primary, reference = cut_commands
    assert primary[primary.index("-ss") + 1] == "1179.000"
    assert primary[primary.index("-i") + 1] == str(source_path)
    assert primary[primary.index("-af") + 1] == (
        "aresample=16000,atrim=start_sample=0:end_sample=256000,asetpts=N/SR/TB"
    )
    assert reference[reference.index("-ss") + 1] == "1174.000"
    assert reference[reference.index("-af") + 1] == (
        "aresample=16000,atrim=start_sample=80000:end_sample=336000,asetpts=N/SR/TB"
    )

    metadata = json.loads(result.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["audio_route"] == "local_source_accurate_seek"
    assert metadata["alignment"]["verified"] is True

    second = prepare_audio_span("IJvd6k6ZmUo", (1_179_000, 1_195_000), settings)
    assert second.cache_hit is True
    assert second.audio_route == "local_source_accurate_seek"


def test_prepare_audio_span_uses_zero_anchor_reference_near_stream_start(tmp_path):
    data_dir = tmp_path / "data"
    ffmpeg_path, _ = _fake_source_toolchain(tmp_path)
    settings = Settings(
        data_dir=data_dir,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    _write_local_source(data_dir, "IJvd6k6ZmUo")

    result = prepare_audio_span("IJvd6k6ZmUo", (1_000, 3_000), settings)

    assert result.audio_route == "local_source_accurate_seek"
    assert result.alignment["reference_seek_ms"] == 0
    assert result.alignment["reference_skip_frames"] == 16_000


def test_prepare_audio_span_start_offset_mismatch_is_fail_closed(tmp_path):
    data_dir = tmp_path / "data"
    ffmpeg_path, _ = _fake_source_toolchain(tmp_path, misalign_reference_ms=9_000)
    settings = Settings(
        data_dir=data_dir,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    _write_local_source(data_dir, "IJvd6k6ZmUo")

    with pytest.raises(AudioSpanError, match="開始位置"):
        prepare_audio_span("IJvd6k6ZmUo", (1_179_000, 1_195_000), settings)

    cache_dir = data_dir / "IJvd6k6ZmUo" / "transcripts" / "audio_cache"
    assert not list(cache_dir.glob("*.wav"))


def test_prepare_audio_span_falls_back_to_ytdlp_with_forced_keyframes(
    monkeypatch,
    tmp_path,
):
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(_fake_ffmpeg(tmp_path / "configured ffmpeg")),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=2_000))

    result = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert len(calls) == 1
    command = calls[0]
    assert "--force-keyframes-at-cuts" in command
    assert command.index("--force-keyframes-at-cuts") == (
        command.index("--download-sections") + 2
    )
    assert result.audio_route == "ytdlp_download_sections_force_keyframes"
    assert result.alignment["verified"] is False
    metadata = json.loads(result.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["audio_route"] == "ytdlp_download_sections_force_keyframes"


def test_prepare_audio_span_ignores_local_source_without_audio_stream(
    monkeypatch,
    tmp_path,
):
    data_dir = tmp_path / "data"
    ffmpeg_path, _ = _fake_source_toolchain(tmp_path, stream_types=("video",))
    settings = Settings(
        data_dir=data_dir,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    _write_local_source(data_dir, "IJvd6k6ZmUo")
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=2_000))

    result = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert len(calls) == 1
    assert result.audio_route == "ytdlp_download_sections_force_keyframes"


def test_prepare_audio_span_ignores_local_source_shorter_than_range(
    monkeypatch,
    tmp_path,
):
    data_dir = tmp_path / "data"
    ffmpeg_path, _ = _fake_source_toolchain(tmp_path, duration_ms=1_500)
    settings = Settings(
        data_dir=data_dir,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    _write_local_source(data_dir, "IJvd6k6ZmUo")
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=2_000))

    result = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert len(calls) == 1
    assert result.audio_route == "ytdlp_download_sections_force_keyframes"


def test_prepare_audio_span_regenerates_fallback_cache_when_source_appears(
    monkeypatch,
    tmp_path,
):
    data_dir = tmp_path / "data"
    ffmpeg_path, _ = _fake_source_toolchain(tmp_path)
    settings = Settings(
        data_dir=data_dir,
        ytdlp_path="yt-dlp-test",
        ffmpeg_path=str(ffmpeg_path),
    )
    monkeypatch.setattr(
        "yt_live_kit.services.ytdlp.shutil.which",
        lambda value: "/usr/bin/yt-dlp" if value == "yt-dlp-test" else value,
    )
    calls = _patch_audio_download(monkeypatch, _wav_bytes(duration_ms=2_000))

    first = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)
    assert first.audio_route == "ytdlp_download_sections_force_keyframes"

    _write_local_source(data_dir, "IJvd6k6ZmUo")
    second = prepare_audio_span("IJvd6k6ZmUo", (1_000, 2_000), settings)

    assert second.cache_hit is False
    assert second.audio_route == "local_source_accurate_seek"
    assert second.alignment["verified"] is True
    assert second.audio_bytes != first.audio_bytes
    assert len(calls) == 1
