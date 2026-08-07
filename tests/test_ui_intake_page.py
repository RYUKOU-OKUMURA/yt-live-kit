"""取り込みページのテスト."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.channel import ChannelVideo
from yt_live_kit.ui.components.clipboard import build_clipboard_copy_html
from yt_live_kit.ui.views import intake
from yt_live_kit.ui.views._local_settings import (
    get_default_channel_handle,
    save_default_channel_handle,
)
from yt_live_kit.ui.views.intake import (
    _NO_TARGET_MESSAGE,
    batch_run_disabled,
    batch_summary_severity,
    checkbox_key,
    collect_selected_urls,
    filter_unprocessed,
    format_duration,
    format_fetched_at,
    format_upload_date,
    single_run_disabled,
)


def _video(video_id: str) -> ChannelVideo:
    return ChannelVideo(
        video_id=video_id,
        title=f"title-{video_id}",
        url=f"https://www.youtube.com/watch?v={video_id}",
        duration=125,
        upload_date="20250730",
    )


def test_channel_formatters_and_unprocessed_filter() -> None:
    assert format_duration(None) == "—"
    assert format_duration(3661) == "1:01:01"
    assert format_upload_date(None) == "—"
    assert format_upload_date("20250730") == "2025-07-30"
    formatted = format_fetched_at(datetime(2026, 7, 30, tzinfo=timezone.utc))
    assert len(formatted) == 16

    items = [(_video("a"), False), (_video("b"), True)]
    assert [video.video_id for video, _ in filter_unprocessed(items)] == ["a"]


def test_collect_selected_urls_only_uses_displayed_items() -> None:
    items = [(_video("a"), False), (_video("b"), False)]
    state = {checkbox_key("a"): True, checkbox_key("b"): False}
    assert collect_selected_urls(items, state) == [
        "https://www.youtube.com/watch?v=a"
    ]


def test_run_disabled_requires_target_and_input() -> None:
    assert single_run_disabled(
        busy=False, url="https://example.com", do_chapters=False, do_clips=False
    )
    assert not single_run_disabled(
        busy=False, url="https://example.com", do_chapters=True, do_clips=False
    )
    assert batch_run_disabled(
        busy=False, urls=["https://example.com"], do_chapters=False, do_clips=False
    )
    assert not batch_run_disabled(
        busy=False, urls=["https://example.com"], do_chapters=False, do_clips=True
    )
    assert "チャプター" in _NO_TARGET_MESSAGE


def test_default_channel_handle_round_trip_and_overwrite(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    assert get_default_channel_handle(settings) is None

    path = save_default_channel_handle("  @first  ", settings)
    assert path.read_text(encoding="utf-8") == "@first\n"
    assert get_default_channel_handle(settings) == "@first"

    save_default_channel_handle("@second", settings)
    assert get_default_channel_handle(settings) == "@second"


@pytest.mark.parametrize("value", ["", "   ", "abc\ndef"])
def test_default_channel_handle_rejects_empty_or_multiline(tmp_path, value) -> None:
    with pytest.raises(ValueError, match="1 行"):
        save_default_channel_handle(value, Settings(data_dir=tmp_path))


def test_missing_default_handle_shows_guidance(tmp_path) -> None:
    mock_st = MagicMock()
    with (
        patch.object(intake, "st", mock_st),
        patch.object(intake, "get_default_channel_handle", return_value=None),
    ):
        intake._render_channel_intake(
            busy=False, settings=Settings(data_dir=tmp_path)
        )
    assert "未設定" in mock_st.info.call_args.args[0]


def test_saved_handle_uses_cache_without_automatic_archive_fetch(tmp_path) -> None:
    mock_st = MagicMock()
    mock_st.selectbox.return_value = 50
    mock_st.button.return_value = False
    settings = Settings(data_dir=tmp_path)
    with (
        patch.object(intake, "st", mock_st),
        patch.object(intake, "get_default_channel_handle", return_value="@saved"),
        patch.object(
            intake,
            "normalize_channel_url",
            return_value=("https://youtube.com/@saved/streams", "saved"),
        ),
        patch.object(intake, "load_cache", return_value=None) as load,
        patch.object(intake, "list_archives") as list_archives,
    ):
        intake._render_channel_intake(busy=False, settings=settings)
    load.assert_called_once_with("saved", settings=settings)
    list_archives.assert_not_called()
    assert "保存済み" in mock_st.info.call_args.args[0]


@pytest.mark.parametrize(
    ("starter", "target"),
    [
        ("start_channel_batch_job", intake.run_batch_job_target),
        ("start_url_batch_job", intake.run_batch_job_target),
        ("start_single_url_job", intake.run_single_job_target),
    ],
)
def test_all_three_routes_pass_flags_to_start_job(tmp_path, starter, target) -> None:
    settings = Settings(data_dir=tmp_path)
    kwargs = {
        "do_chapters": False,
        "do_clips": True,
        "settings": settings,
    }
    if starter == "start_single_url_job":
        kwargs["url"] = " https://example.com/single "
    else:
        kwargs["urls"] = ["https://example.com/batch"]
    if starter == "start_url_batch_job":
        kwargs["skip_existing"] = False

    with (
        patch.object(intake, "start_job", return_value="job-1") as start,
        patch.object(intake, "set_active_job_id"),
        patch.object(intake, "_prepare_job_state"),
    ):
        getattr(intake, starter)(**kwargs)

    args = start.call_args.args
    call_kwargs = start.call_args.kwargs
    assert args[1] is target
    assert call_kwargs["do_chapters"] is False
    assert call_kwargs["do_clips"] is True


def test_url_route_is_a_collapsed_exception_expander() -> None:
    source = inspect.getsource(intake.render_intake_page)
    assert "URL を直接入力する（例外ルート）" in source
    assert "expanded=False" in source


def test_batch_summary_severity() -> None:
    assert batch_summary_severity(0, 0) == "info"
    assert batch_summary_severity(5, 0) == "success"
    assert batch_summary_severity(1, 1) == "warning"
    assert batch_summary_severity(0, 1) == "error"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, 3),
        (0, 0),
        (2.7, 2),
        ("4", 4),
        (None, 0),
        (True, 0),
        ("abc", 0),
        ([1, 2], 0),
        ({"count": 1}, 0),
        (float("nan"), 0),
    ],
)
def test_coerce_summary_count_never_raises(value: object, expected: int) -> None:
    """壊れた summary JSON の件数欄でもサマリー表示を落とさない（CR-F18）.

    ``read_batch_summary`` は永続 JSON をそのまま返すため件数欄に任意の型が
    入りうる。以前は ``int()`` へ直接渡しており ``"abc"`` や ``[1, 2]`` で
    TypeError / ValueError が送出され、取り込みページのサマリーごと落ちていた。
    """
    assert intake._coerce_summary_count(value) == expected


def test_render_batch_summary_survives_corrupt_counts() -> None:
    """件数欄が壊れていても _render_batch_summary が例外を送出しない（CR-F18）."""
    corrupt = {"summary": "一括処理の結果", "failed": "abc", "success": [1, 2]}
    with (
        patch.object(intake, "get_batch_summary", return_value=corrupt),
        patch.object(intake, "st") as fake_st,
    ):
        intake._render_batch_summary()
    # severity は success=0 / failed=0 の "info" になり、本文はそのまま渡る。
    fake_st.info.assert_called_once_with("一括処理の結果")


def test_clipboard_html_remains_safe_after_page_merge() -> None:
    import json

    text = '0:00 開始\n5:00 "引用"\n\\バックスラッシュ'
    html = build_clipboard_copy_html(
        text=text, button_id="copy_test123", button_label="コピー"
    )
    assert json.dumps(text, ensure_ascii=False) in html
    assert "navigator.clipboard.writeText" in html
    assert text not in html

    hostile = build_clipboard_copy_html(
        text="本文</script><script>alert(1)</script>",
        button_id="copy_xss",
        button_label="コピー",
    )
    assert "</script><script>alert(1)</script>" not in hostile
    assert hostile.count("</script>") == 1
