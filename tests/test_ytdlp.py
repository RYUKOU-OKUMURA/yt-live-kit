"""yt-dlp ラッパーのユニットテスト."""

import hashlib
import io
import json
import os
import subprocess
import sys
import wave
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.subtitle import SubtitleSourceMetadata
from yt_live_kit.services.ytdlp import (
    AudioSpanRange,
    MISSING_YTDLP_BINARY_IDENTITY,
    YtdlpError,
    _download_subtitles,
    _find_subtitle_file,
    _run_ytdlp,
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


def _wav_bytes(*, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * channels * 160)
    return buffer.getvalue()


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
    settings = Settings(
        data_dir=tmp_path,
        ytdlp_path="yt-dlp-test",
        ytdlp_timeout=17,
    )
    calls: list[list[str]] = []
    content = _wav_bytes()

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

    first = prepare_audio_span(
        video_id,
        AudioSpanRange(12_345, 23_456, padding_before_ms=250, padding_after_ms=500),
        settings,
        source_metadata={"title": "fixture"},
    )

    assert first.cache_hit is False
    assert first.audio_bytes == content
    assert first.sample_rate == 16_000
    assert first.channel == 1
    assert first.codec == "pcm_s16le"
    assert first.range.requested_start_ms == 12_095
    assert first.range.requested_end_ms == 23_956
    assert len(calls) == 1
    command = calls[0]
    assert command[command.index("-f") + 1] == "bestaudio/best"
    assert "--download-sections" in command
    section = command[command.index("--download-sections") + 1]
    assert section == "*00:00:12.095-00:00:23.956"
    assert "bestvideo" not in " ".join(command)
    assert ".mp4" not in " ".join(command)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("cache hit では yt-dlp を再実行しない")

    monkeypatch.setattr("yt_live_kit.services.ytdlp._run_ytdlp", fail_if_called)
    second = prepare_audio_span(
        video_id,
        AudioSpanRange(12_345, 23_456, padding_before_ms=250, padding_after_ms=500),
        settings,
        source_metadata={"title": "fixture"},
    )
    assert second.cache_hit is True
    assert second.audio_input_fingerprint == first.audio_input_fingerprint


def test_prepare_audio_span_corrupt_cache_is_a_miss(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path, ytdlp_path="yt-dlp-test")
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
