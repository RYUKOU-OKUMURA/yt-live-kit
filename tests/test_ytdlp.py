"""yt-dlp ラッパーのユニットテスト."""

import subprocess
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.ytdlp import (
    MISSING_YTDLP_BINARY_IDENTITY,
    YtdlpError,
    _find_subtitle_file,
    _run_ytdlp,
    extract_video_id,
    fetch,
    get_ytdlp_binary_identity,
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


@patch("yt_live_kit.services.ytdlp.write_text_atomically")
@patch("yt_live_kit.services.ytdlp.get_ytdlp_version", return_value="2026.07.04")
@patch("yt_live_kit.services.ytdlp._normalize_subtitle_path")
@patch("yt_live_kit.services.ytdlp._find_subtitle_file")
@patch("yt_live_kit.services.ytdlp._download_subtitles")
@patch("yt_live_kit.services.ytdlp._fetch_metadata")
@patch("yt_live_kit.services.ytdlp.shutil.which", return_value="/usr/bin/yt-dlp")
def test_fetch_saves_meta_json_atomically(
    mock_which,
    mock_fetch_metadata,
    mock_download_subtitles,
    mock_find_subtitle,
    mock_normalize,
    mock_version,
    mock_write_atomic,
    tmp_path,
):
    video_id = "IJvd6k6ZmUo"
    mock_fetch_metadata.return_value = {
        "id": video_id,
        "title": "テスト動画",
        "upload_date": "20260101",
        "duration": 3600,
    }
    subtitles_dir = tmp_path / video_id / "subtitles"
    subtitles_dir.mkdir(parents=True)
    vtt_path = subtitles_dir / f"{video_id}.ja.vtt"
    vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
    mock_find_subtitle.return_value = (vtt_path, "ja")
    mock_normalize.return_value = subtitles_dir / "ja.vtt"

    settings = Settings(data_dir=tmp_path)
    meta = fetch(f"https://www.youtube.com/watch?v={video_id}", settings)

    assert meta.id == video_id
    mock_write_atomic.assert_called_once()
    path, text = mock_write_atomic.call_args.args
    assert path.name == "meta.json"
    assert video_id in text
