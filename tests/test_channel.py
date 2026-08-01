"""channel サービスのユニットテスト."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.channel import ChannelListDocument, ChannelVideo
from yt_live_kit.services.channel import (
    ChannelError,
    list_archives,
    load_cache,
    mark_processed,
    normalize_channel_url,
    save_cache,
)


@pytest.mark.parametrize(
    ("text", "expected_url", "expected_handle"),
    [
        ("@mychannel", "https://www.youtube.com/@mychannel/streams", "mychannel"),
        ("mychannel", "https://www.youtube.com/@mychannel/streams", "mychannel"),
        (
            "https://www.youtube.com/@mychannel",
            "https://www.youtube.com/@mychannel/streams",
            "mychannel",
        ),
        (
            "https://www.youtube.com/@mychannel/videos",
            "https://www.youtube.com/@mychannel/streams",
            "mychannel",
        ),
        (
            "https://www.youtube.com/@mychannel/streams",
            "https://www.youtube.com/@mychannel/streams",
            "mychannel",
        ),
        (
            "https://www.youtube.com/channel/UCxxxxxxxxxx",
            "https://www.youtube.com/channel/UCxxxxxxxxxx/streams",
            "UCxxxxxxxxxx",
        ),
        (
            "https://www.youtube.com/c/MyCustomName",
            "https://www.youtube.com/c/MyCustomName/streams",
            "MyCustomName",
        ),
    ],
)
def test_normalize_channel_url_valid(text: str, expected_url: str, expected_handle: str):
    url, handle = normalize_channel_url(text)
    assert url == expected_url
    assert handle == expected_handle


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a url!!!",
        "https://www.youtube.com/watch?v=abc12345678",
        "@",
        "bad handle!",
    ],
)
def test_normalize_channel_url_invalid(text: str):
    with pytest.raises(ChannelError):
        normalize_channel_url(text)


def test_normalize_channel_url_sanitizes_handle():
    url, handle = normalize_channel_url("@my.channel-name")
    assert url == "https://www.youtube.com/@my.channel-name/streams"
    assert handle == "mychannel-name"


def _json_line(entry: dict) -> str:
    return json.dumps(entry, ensure_ascii=False)


def _make_doc(videos: list[ChannelVideo] | None = None) -> ChannelListDocument:
    return ChannelListDocument(
        channel_url="https://www.youtube.com/@test/streams",
        handle="test",
        fetched_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        videos=videos or [],
    )


@patch("yt_live_kit.services.channel._run_ytdlp")
def test_list_archives_parses_json_lines(mock_run):
    mock_run.return_value = CompletedProcess(
        args=[],
        returncode=0,
        stdout="\n".join(
            [
                _json_line(
                    {
                        "id": "vid11111111",
                        "title": "配信1",
                        "duration": 3600,
                        "upload_date": "20260701",
                        "url": "https://www.youtube.com/watch?v=vid11111111",
                    }
                ),
                _json_line(
                    {
                        "id": "vid22222222",
                        "title": "配信2",
                        "duration": 7200,
                        "upload_date": "20260702",
                    }
                ),
                _json_line(
                    {
                        "id": "vid33333333",
                        "title": "配信3",
                        "duration": 1800,
                        "upload_date": "20260703",
                        "url": "https://www.youtube.com/watch?v=vid33333333",
                    }
                ),
            ]
        ),
        stderr="",
    )

    doc = list_archives("@test", settings=Settings(ytdlp_path="yt-dlp"))

    assert len(doc.videos) == 3
    assert doc.videos[0].video_id == "vid11111111"
    assert doc.videos[1].url == "https://www.youtube.com/watch?v=vid22222222"
    assert doc.channel_url == "https://www.youtube.com/@test/streams"
    assert doc.handle == "test"
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "--flat-playlist" in args
    assert "--dump-json" in args
    assert "--playlist-end" in args
    assert "50" in args
    assert "--extractor-args" in args
    assert "youtube:lang=ja" in args


@patch("yt_live_kit.services.channel._run_ytdlp")
def test_list_archives_skips_empty_and_broken_lines(mock_run):
    mock_run.return_value = CompletedProcess(
        args=[],
        returncode=0,
        stdout="\n\n" + _json_line({"id": "ok123456789", "title": "OK"}) + "\n{broken\n",
        stderr="",
    )

    doc = list_archives("https://www.youtube.com/@test/streams", settings=Settings(ytdlp_path="yt-dlp"))
    assert len(doc.videos) == 1
    assert doc.videos[0].video_id == "ok123456789"


@patch("yt_live_kit.services.channel._run_ytdlp")
def test_list_archives_all_lines_broken_raises(mock_run):
    mock_run.return_value = CompletedProcess(
        args=[],
        returncode=0,
        stdout="not json\n{broken\n",
        stderr="",
    )

    with pytest.raises(ChannelError, match="一覧の取得に失敗"):
        list_archives("@test", settings=Settings(ytdlp_path="yt-dlp"))


@patch("yt_live_kit.services.channel._run_ytdlp")
def test_list_archives_empty_result_raises(mock_run):
    mock_run.return_value = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with pytest.raises(ChannelError, match="公開されたライブ配信アーカイブがありません"):
        list_archives("@test", settings=Settings(ytdlp_path="yt-dlp"))


@patch("yt_live_kit.services.channel._run_ytdlp")
def test_list_archives_ytdlp_not_found_raises(mock_run):
    mock_run.return_value = CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="ERROR: Unable to extract uploader id",
    )

    with pytest.raises(ChannelError, match="チャンネルが見つかりません"):
        list_archives("@missing", settings=Settings(ytdlp_path="yt-dlp"))


@patch("yt_live_kit.services.channel._run_ytdlp")
def test_list_archives_ytdlp_generic_failure(mock_run):
    mock_run.return_value = CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="network timeout",
    )

    with pytest.raises(ChannelError, match="一覧の取得に失敗"):
        list_archives("@test", settings=Settings(ytdlp_path="yt-dlp"))


@patch("yt_live_kit.services.channel.shutil.which", return_value=None)
def test_list_archives_ytdlp_binary_missing(mock_which):
    with pytest.raises(ChannelError, match="yt-dlp が見つかりません"):
        list_archives("@test", settings=Settings(ytdlp_path="missing-ytdlp"))


@patch("yt_live_kit.services.channel.subprocess.run")
@patch("yt_live_kit.services.channel.shutil.which", return_value="/usr/bin/yt-dlp")
def test_list_archives_ytdlp_timeout_raises_channel_error(mock_which, mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=300)

    with pytest.raises(ChannelError, match="タイムアウト"):
        list_archives("@test", settings=Settings(ytdlp_path="yt-dlp", ytdlp_timeout=300))


def test_save_and_load_cache_roundtrip(tmp_path):
    settings = Settings(data_dir=tmp_path)
    videos = [
        ChannelVideo(
            video_id="vid11111111",
            title="配信1",
            url="https://www.youtube.com/watch?v=vid11111111",
            duration=3600,
            upload_date="20260701",
        )
    ]
    doc = _make_doc(videos)

    path = save_cache(doc, settings)
    assert path == tmp_path / "_channels" / "test.json"
    assert path.is_file()

    loaded = load_cache("test", settings)
    assert loaded is not None
    assert loaded.channel_url == doc.channel_url
    assert loaded.handle == doc.handle
    assert len(loaded.videos) == 1
    assert loaded.videos[0].video_id == "vid11111111"


def test_load_cache_missing_returns_none(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert load_cache("nonexistent", settings) is None


def test_mark_processed(tmp_path):
    settings = Settings(data_dir=tmp_path)
    processed_id = "processed12"
    unprocessed_id = "unprocess12"

    video_dir = tmp_path / processed_id
    video_dir.mkdir()
    meta = {
        "id": processed_id,
        "title": "処理済み",
        "url": f"https://www.youtube.com/watch?v={processed_id}",
        "ytdlp_version": "2026.7.4",
        "fetched_at": datetime(2026, 7, 30, tzinfo=timezone.utc).isoformat(),
    }
    (video_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    doc = _make_doc(
        [
            ChannelVideo(
                video_id=processed_id,
                title="処理済み",
                url=f"https://www.youtube.com/watch?v={processed_id}",
            ),
            ChannelVideo(
                video_id=unprocessed_id,
                title="未処理",
                url=f"https://www.youtube.com/watch?v={unprocessed_id}",
            ),
        ]
    )

    marked = mark_processed(doc, settings)
    assert marked == [
        (doc.videos[0], True),
        (doc.videos[1], False),
    ]
