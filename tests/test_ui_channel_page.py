"""ui/views/channel.py のヘルパー関数テスト."""

from __future__ import annotations

from datetime import datetime, timezone

from yt_live_kit.models.channel import ChannelVideo
from yt_live_kit.ui.views.channel import (
    checkbox_key,
    collect_selected_urls,
    count_selected,
    filter_display_items,
    format_duration,
    format_fetched_at,
    format_upload_date,
)


def _video(video_id: str, *, url: str | None = None) -> ChannelVideo:
    return ChannelVideo(
        video_id=video_id,
        title=f"title-{video_id}",
        url=url or f"https://www.youtube.com/watch?v={video_id}",
        duration=125,
        upload_date="20250730",
    )


def test_format_duration_formats_h_mm_ss() -> None:
    assert format_duration(None) == "—"
    assert format_duration(65) == "0:01:05"
    assert format_duration(3661) == "1:01:01"


def test_format_upload_date_formats_yyyymmdd() -> None:
    assert format_upload_date(None) == "—"
    assert format_upload_date("invalid") == "—"
    assert format_upload_date("20250730") == "2025-07-30"


def test_format_fetched_at_uses_local_time() -> None:
    fetched_at = datetime(2026, 7, 30, 3, 15, tzinfo=timezone.utc)
    formatted = format_fetched_at(fetched_at)
    assert len(formatted) == 16
    assert formatted[4] == "-"
    assert formatted[7] == "-"
    assert formatted[10] == " "


def test_filter_display_items_unprocessed_only() -> None:
    items = [
        (_video("a"), False),
        (_video("b"), True),
        (_video("c"), False),
    ]

    filtered = filter_display_items(items, unprocessed_only=True)

    assert [video.video_id for video, _ in filtered] == ["a", "c"]


def test_filter_display_items_shows_all_when_toggle_off() -> None:
    items = [
        (_video("a"), False),
        (_video("b"), True),
    ]

    filtered = filter_display_items(items, unprocessed_only=False)

    assert len(filtered) == 2


def test_checkbox_key_includes_video_id() -> None:
    assert checkbox_key("abc123") == "channel_cb_abc123"


def test_count_selected_and_collect_selected_urls() -> None:
    items = [
        (_video("a"), False),
        (_video("b"), True),
        (_video("c"), False),
    ]
    session_state = {
        checkbox_key("a"): True,
        checkbox_key("b"): False,
        checkbox_key("c"): True,
    }

    assert count_selected(items, session_state) == 2
    assert collect_selected_urls(items, session_state) == [
        "https://www.youtube.com/watch?v=a",
        "https://www.youtube.com/watch?v=c",
    ]
