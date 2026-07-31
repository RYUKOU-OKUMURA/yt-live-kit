"""v2 CLI コマンドのユニットテスト."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from yt_live_kit.cli import app
from yt_live_kit.models.channel import ChannelListDocument, ChannelVideo
from yt_live_kit.models.highlights import HighlightSegment, HighlightsDocument
from yt_live_kit.services.ffmpeg import ConcatResult
from yt_live_kit.services.highlights import HighlightsResult
from yt_live_kit.services.shorts import ShortResult

runner = CliRunner()


def _sample_channel_doc(*, handle: str = "testchannel") -> ChannelListDocument:
    return ChannelListDocument(
        channel_url=f"https://www.youtube.com/@{handle}/streams",
        handle=handle,
        fetched_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        videos=[
            ChannelVideo(
                video_id="vid001",
                title="配信 1",
                url="https://www.youtube.com/watch?v=vid001",
            ),
            ChannelVideo(
                video_id="vid002",
                title="配信 2",
                url="https://www.youtube.com/watch?v=vid002",
            ),
        ],
    )


def _sample_segments(count: int = 3) -> list[HighlightSegment]:
    segments: list[HighlightSegment] = []
    for i in range(count):
        start_min = i * 5
        segments.append(
            HighlightSegment(
                id=f"hl_{i + 1:03d}",
                title=f"山場 {i + 1}",
                start=f"00:{start_min:02d}:00",
                end=f"00:{start_min + 2:02d}:00",
                duration_sec=120,
                reason="テスト理由",
            )
        )
    return segments


@pytest.mark.parametrize(
    ("args", "expected_substring"),
    [
        (["channel", "--help"], "チャンネルの配信アーカイブ一覧"),
        (["highlights", "--help"], "ハイライト区間"),
        (["highlights", "suggest", "--help"], "ハイライト区間候補"),
        (["highlights", "build", "--help"], "まとめ動画"),
        (["short", "--help"], "縦型ショート"),
    ],
)
def test_cli_help_includes_japanese(args: list[str], expected_substring: str):
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert expected_substring in result.stdout


@patch("yt_live_kit.commands.channel.mark_processed")
@patch("yt_live_kit.commands.channel.load_cache")
@patch("yt_live_kit.commands.channel.normalize_channel_url")
def test_channel_uses_cache(
    mock_normalize,
    mock_load_cache,
    mock_mark_processed,
):
    doc = _sample_channel_doc()
    mock_normalize.return_value = (doc.channel_url, doc.handle)
    mock_load_cache.return_value = doc
    mock_mark_processed.return_value = [
        (doc.videos[0], False),
        (doc.videos[1], True),
    ]

    result = runner.invoke(app, ["channel", "@testchannel"])

    assert result.exit_code == 0
    mock_load_cache.assert_called_once()
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "vid001\t配信 1\thttps://www.youtube.com/watch?v=vid001"
    assert lines[1] == "# vid002\t配信 2\thttps://www.youtube.com/watch?v=vid002"


@patch("yt_live_kit.commands.channel.mark_processed")
@patch("yt_live_kit.commands.channel.save_cache")
@patch("yt_live_kit.commands.channel.list_archives")
@patch("yt_live_kit.commands.channel.normalize_channel_url")
def test_channel_refresh_fetches_and_saves(
    mock_normalize,
    mock_list_archives,
    mock_save_cache,
    mock_mark_processed,
):
    doc = _sample_channel_doc()
    mock_normalize.return_value = (doc.channel_url, doc.handle)
    mock_list_archives.return_value = doc
    mock_mark_processed.return_value = [(video, False) for video in doc.videos]

    result = runner.invoke(app, ["channel", "testchannel", "--refresh"])

    assert result.exit_code == 0
    mock_list_archives.assert_called_once()
    mock_save_cache.assert_called_once_with(doc, mock_list_archives.call_args.kwargs["settings"])


@patch("yt_live_kit.commands.channel.mark_processed")
@patch("yt_live_kit.commands.channel.save_cache")
@patch("yt_live_kit.commands.channel.list_archives")
@patch("yt_live_kit.commands.channel.load_cache")
@patch("yt_live_kit.commands.channel.normalize_channel_url")
def test_channel_fetches_when_cache_missing(
    mock_normalize,
    mock_load_cache,
    mock_list_archives,
    mock_save_cache,
    mock_mark_processed,
):
    doc = _sample_channel_doc()
    mock_normalize.return_value = (doc.channel_url, doc.handle)
    mock_load_cache.return_value = None
    mock_list_archives.return_value = doc
    mock_mark_processed.return_value = [(video, False) for video in doc.videos]

    result = runner.invoke(app, ["channel", "@testchannel"])

    assert result.exit_code == 0
    mock_load_cache.assert_called_once()
    mock_list_archives.assert_called_once()
    mock_save_cache.assert_called_once()


@patch("yt_live_kit.commands.highlights.suggest_highlights")
def test_highlights_suggest_lists_segments(mock_suggest):
    segments = _sample_segments(2)
    mock_suggest.return_value = HighlightsResult(
        video_id="abc12345678",
        prompt_path=Path("/tmp/prompt.md"),
        segments_path=Path("/tmp/segments.json"),
        used_codex=True,
        segments=tuple(segments),
    )

    result = runner.invoke(app, ["highlights", "suggest", "abc12345678"])

    assert result.exit_code == 0
    mock_suggest.assert_called_once()
    assert "区間数: 2" in result.stdout
    assert "1. 00:00:00 - 00:02:00  山場 1" in result.stdout
    assert "2. 00:05:00 - 00:07:00  山場 2" in result.stdout


@patch("yt_live_kit.commands.highlights.cut_and_concat")
@patch("yt_live_kit.commands.highlights.load_segments_file")
def test_highlights_build_with_segment_numbers(mock_load, mock_concat):
    segments = _sample_segments(3)
    mock_load.return_value = HighlightsDocument(candidates=segments)
    mock_concat.return_value = ConcatResult(
        video_id="abc12345678",
        output_path=Path("/tmp/highlight.mp4"),
        command_log_path=Path("/tmp/log.txt"),
        segment_count=2,
        total_duration_sec=240.0,
    )

    result = runner.invoke(
        app,
        ["highlights", "build", "abc12345678", "--segments", "1,3"],
    )

    assert result.exit_code == 0
    mock_concat.assert_called_once()
    passed_segments = mock_concat.call_args[0][1]
    assert len(passed_segments) == 2
    assert passed_segments[0].id == "hl_001"
    assert passed_segments[1].id == "hl_003"
    assert "連結完了" in result.stdout


@patch("yt_live_kit.commands.highlights.cut_and_concat")
@patch("yt_live_kit.commands.highlights.load_segments_file")
def test_highlights_build_all_segments_when_omitted(mock_load, mock_concat):
    segments = _sample_segments(2)
    mock_load.return_value = HighlightsDocument(candidates=segments)
    mock_concat.return_value = ConcatResult(
        video_id="abc12345678",
        output_path=Path("/tmp/highlight.mp4"),
        command_log_path=Path("/tmp/log.txt"),
        segment_count=2,
        total_duration_sec=480.0,
    )

    result = runner.invoke(app, ["highlights", "build", "abc12345678"])

    assert result.exit_code == 0
    passed_segments = mock_concat.call_args[0][1]
    assert len(passed_segments) == 2


@patch("yt_live_kit.commands.highlights.load_segments_file")
def test_highlights_build_missing_segments(mock_load):
    mock_load.return_value = None

    result = runner.invoke(app, ["highlights", "build", "abc12345678"])

    assert result.exit_code == 1
    assert "ハイライト区間が見つかりません" in result.stderr


@patch("yt_live_kit.commands.shorts.build_short")
def test_short_builds_with_timestamps(mock_build):
    mock_build.return_value = ShortResult(
        video_id="abc12345678",
        output_path=Path("/tmp/short.mp4"),
        command_log_path=Path("/tmp/log.txt"),
        layout="blur",
        burned_subtitles=True,
        duration_sec=50.0,
    )

    result = runner.invoke(
        app,
        [
            "short",
            "abc12345678",
            "--start",
            "00:12:30",
            "--end",
            "00:13:20",
        ],
    )

    assert result.exit_code == 0
    mock_build.assert_called_once()
    assert mock_build.call_args[0][1] == 750.0
    assert mock_build.call_args[0][2] == 800.0
    assert mock_build.call_args.kwargs["layout"] == "blur"
    assert mock_build.call_args.kwargs["burn_subtitles"] is True
    assert "ショート生成完了" in result.stdout


@patch("yt_live_kit.commands.shorts.build_short")
def test_short_no_subtitles_and_crop_layout(mock_build):
    mock_build.return_value = ShortResult(
        video_id="abc12345678",
        output_path=Path("/tmp/short.mp4"),
        command_log_path=Path("/tmp/log.txt"),
        layout="crop",
        burned_subtitles=False,
        duration_sec=60.0,
    )

    result = runner.invoke(
        app,
        [
            "short",
            "abc12345678",
            "--start",
            "1:00",
            "--end",
            "2:00",
            "--layout",
            "crop",
            "--no-subtitles",
        ],
    )

    assert result.exit_code == 0
    assert mock_build.call_args.kwargs["layout"] == "crop"
    assert mock_build.call_args.kwargs["burn_subtitles"] is False
