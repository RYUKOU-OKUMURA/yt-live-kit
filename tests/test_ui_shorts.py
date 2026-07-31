"""ui/views/shorts.py のヘルパー関数テスト."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.ui.views.shorts import (
    _confirm_build_short_dialog,
    _start_or_confirm_build_short,
    LAYOUT_BLUR_LABEL,
    LAYOUT_CROP_LABEL,
    ShortInterval,
    interval_from_timestamps,
    layout_from_label,
    parse_manual_timestamp,
    short_meta_path,
    short_output_path,
    validate_interval_duration,
)


def test_parse_manual_timestamp_accepts_hh_mm_ss() -> None:
    seconds, error = parse_manual_timestamp("1:23:45")
    assert error is None
    assert seconds == 5025.0


def test_parse_manual_timestamp_rejects_m_ss() -> None:
    seconds, error = parse_manual_timestamp("5:30")
    assert seconds is None
    assert error == "時刻は HH:MM:SS の形式で入力してください。"


def test_parse_manual_timestamp_rejects_invalid() -> None:
    seconds, error = parse_manual_timestamp("abc")
    assert seconds is None
    assert error == "時刻は HH:MM:SS の形式で入力してください。"


def test_validate_interval_duration_accepts_valid_range() -> None:
    ok, message = validate_interval_duration(0.0, 60.0)
    assert ok is True
    assert message is None


def test_validate_interval_duration_rejects_too_short() -> None:
    ok, message = validate_interval_duration(0.0, 9.0)
    assert ok is False
    assert message is not None
    assert "10 秒以上" in message


def test_validate_interval_duration_rejects_too_long() -> None:
    ok, message = validate_interval_duration(0.0, 181.0)
    assert ok is False
    assert message is not None
    assert "180 秒以下" in message


def test_validate_interval_duration_rejects_end_before_start() -> None:
    ok, message = validate_interval_duration(100.0, 50.0)
    assert ok is False
    assert message == "終了時刻は開始時刻より後である必要があります。"


def test_layout_from_label_maps_ui_labels() -> None:
    assert layout_from_label(LAYOUT_BLUR_LABEL) == "blur"
    assert layout_from_label(LAYOUT_CROP_LABEL) == "crop"
    assert layout_from_label("不明") == "blur"


def test_interval_from_timestamps_parses_clip_times() -> None:
    interval, error = interval_from_timestamps("0:01:00", "0:02:30")
    assert error is None
    assert interval == ShortInterval(start=60.0, end=150.0)


def test_short_output_path_matches_services_naming(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    path = short_output_path("vid123", 10.0, 40.5, settings)
    assert path == tmp_path / "vid123" / "shorts" / "output" / "short_10_40.5.mp4"


def test_short_meta_path_is_adjacent_to_output(tmp_path: Path) -> None:
    output = tmp_path / "short_10_40.mp4"
    assert short_meta_path(output) == tmp_path / "short_10_40.meta.json"


def test_existing_short_output_opens_dialog_without_starting_job() -> None:
    settings = MagicMock()
    interval = ShortInterval(start=10.0, end=40.0)
    with (
        patch("yt_live_kit.ui.views.shorts._confirm_build_short_dialog") as dialog,
        patch("yt_live_kit.ui.views.shorts._start_build_short") as start,
    ):
        _start_or_confirm_build_short(
            video_id="vid123",
            title="テスト",
            interval=interval,
            layout="blur",
            burn_subtitles=True,
            settings=settings,
            output_exists=True,
        )

    dialog.assert_called_once_with(
        video_id="vid123",
        title="テスト",
        interval=interval,
        layout="blur",
        burn_subtitles=True,
        settings=settings,
    )
    start.assert_not_called()


def test_missing_short_output_starts_without_dialog() -> None:
    settings = MagicMock()
    interval = ShortInterval(start=10.0, end=40.0)
    with (
        patch("yt_live_kit.ui.views.shorts._confirm_build_short_dialog") as dialog,
        patch("yt_live_kit.ui.views.shorts._start_build_short") as start,
    ):
        _start_or_confirm_build_short(
            video_id="vid123",
            title="テスト",
            interval=interval,
            layout="crop",
            burn_subtitles=False,
            settings=settings,
            output_exists=False,
        )

    start.assert_called_once_with(
        video_id="vid123",
        title="テスト",
        interval=interval,
        layout="crop",
        burn_subtitles=False,
        settings=settings,
    )
    dialog.assert_not_called()


def test_short_build_dialog_requires_confirmation() -> None:
    settings = MagicMock()
    interval = ShortInterval(start=10.0, end=40.0)
    with (
        patch("yt_live_kit.ui.views.shorts.is_busy", return_value=False),
        patch("yt_live_kit.ui.views.shorts.st.warning"),
        patch(
            "yt_live_kit.ui.views.shorts.st.button",
            side_effect=[False, True],
        ),
        patch("yt_live_kit.ui.views.shorts._start_build_short") as start,
    ):
        kwargs = {
            "video_id": "vid123",
            "title": "テスト",
            "interval": interval,
            "layout": "blur",
            "burn_subtitles": True,
            "settings": settings,
        }
        _confirm_build_short_dialog.__wrapped__(**kwargs)
        start.assert_not_called()
        _confirm_build_short_dialog.__wrapped__(**kwargs)

    start.assert_called_once_with(**kwargs)
