"""設定ページのテスト."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.ffmpeg import FfmpegDiagnostics, FfmpegError
from yt_live_kit.services.schedule import ScheduleError, SchedulePolicy
from yt_live_kit.ui.views import settings as settings_page


@pytest.fixture(autouse=True)
def _stub_ffmpeg_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(
        settings_page,
        "diagnose_ffmpeg",
        lambda configured_path: FfmpegDiagnostics(
            configured_path=configured_path,
            resolved_path="/resolved/bin/ffmpeg",
            version="ffmpeg version 8.0.1",
            subtitles_available=True,
        ),
    )


def _settings(tmp_path: Path, *, subtitle_font: str | None = None) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        ffmpeg_path="/opt/tools/ffmpeg",
        subtitle_font=subtitle_font,
    )


@patch.object(settings_page.st, "success")
@patch.object(settings_page, "is_codex_available", return_value=True)
def test_codex_status_shows_available_in_japanese(
    available: MagicMock,
    success: MagicMock,
) -> None:
    settings_page._render_codex_status()

    available.assert_called_once_with()
    assert "利用可能" in success.call_args.args[0]


@patch.object(settings_page.st, "warning")
@patch.object(settings_page, "is_codex_available", return_value=False)
def test_codex_status_shows_not_found_in_japanese(
    available: MagicMock,
    warning: MagicMock,
) -> None:
    settings_page._render_codex_status()

    available.assert_called_once_with()
    assert "見つかりません" in warning.call_args.args[0]
    assert "codex login" in warning.call_args.args[0]


@patch.object(settings_page.st, "success")
@patch.object(settings_page, "save_default_channel_handle")
def test_save_channel_handle_uses_local_settings_helper(
    save_handle: MagicMock,
    success: MagicMock,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    settings_page._save_channel_handle("  @my-channel  ", settings)

    save_handle.assert_called_once_with("@my-channel", settings)
    assert "保存しました" in success.call_args.args[0]


@patch.object(settings_page.st, "error")
@patch.object(settings_page, "save_default_channel_handle")
def test_save_channel_handle_rejects_blank_input(
    save_handle: MagicMock,
    error: MagicMock,
    tmp_path: Path,
) -> None:
    settings_page._save_channel_handle("   ", _settings(tmp_path))

    save_handle.assert_not_called()
    assert "入力してください" in error.call_args.args[0]


@patch.object(settings_page.st, "error")
@patch.object(
    settings_page,
    "save_default_channel_handle",
    side_effect=OSError("permission denied"),
)
def test_save_channel_handle_shows_japanese_error_on_io_failure(
    save_handle: MagicMock,
    error: MagicMock,
    tmp_path: Path,
) -> None:
    settings_page._save_channel_handle("@my-channel", _settings(tmp_path))

    save_handle.assert_called_once()
    message = error.call_args.args[0]
    assert "保存できませんでした" in message
    assert "権限" in message
    assert "permission denied" not in message


@patch.object(settings_page.st, "code")
@patch.object(settings_page.st, "container")
def test_environment_settings_are_read_only_and_show_env_guidance(
    container: MagicMock,
    code: MagicMock,
    tmp_path: Path,
) -> None:
    container.return_value.__enter__.return_value = container.return_value
    settings = _settings(tmp_path, subtitle_font="Noto Sans JP")

    settings_page._render_environment_settings(settings)

    assert call("/opt/tools/ffmpeg") in code.call_args_list
    assert call("Noto Sans JP") in code.call_args_list
    assert call(str(settings.data_dir)) in code.call_args_list
    env_example = code.call_args_list[-1].args[0]
    assert "YTLK_FFMPEG_PATH=" in env_example
    assert "YTLK_SUBTITLE_FONT=" in env_example
    assert "YTLK_DATA_DIR=" in env_example


@patch.object(settings_page.st, "code")
@patch.object(settings_page.st, "container")
def test_environment_settings_explains_automatic_font_detection(
    container: MagicMock,
    code: MagicMock,
    tmp_path: Path,
) -> None:
    container.return_value.__enter__.return_value = container.return_value

    settings_page._render_environment_settings(_settings(tmp_path))

    assert call("未指定（自動検出）") in code.call_args_list


@patch.object(settings_page.st, "success")
@patch.object(settings_page.st, "warning")
@patch.object(settings_page.st, "code")
@patch.object(settings_page.st, "container")
def test_environment_settings_shows_ffmpeg_diagnostics_success(
    container: MagicMock,
    code: MagicMock,
    warning: MagicMock,
    success: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    container.return_value.__enter__.return_value = container.return_value
    diagnose = MagicMock(
        return_value=FfmpegDiagnostics(
            configured_path="/opt/tools/ffmpeg",
            resolved_path="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            version="ffmpeg version 8.0.1-full",
            subtitles_available=True,
        )
    )
    monkeypatch.setattr(settings_page, "diagnose_ffmpeg", diagnose)
    settings = _settings(tmp_path)

    settings_page._render_environment_settings(settings)

    diagnose.assert_called_once_with("/opt/tools/ffmpeg")
    assert call("/opt/tools/ffmpeg") in code.call_args_list
    assert call("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg") in code.call_args_list
    assert call("ffmpeg version 8.0.1-full") in code.call_args_list
    assert any("利用できます" in item.args[0] for item in success.call_args_list)
    warning.assert_not_called()


@patch.object(settings_page.st, "success")
@patch.object(settings_page.st, "warning")
@patch.object(settings_page.st, "code")
@patch.object(settings_page.st, "container")
def test_environment_settings_keeps_page_alive_on_ffmpeg_diagnostic_failure(
    container: MagicMock,
    code: MagicMock,
    warning: MagicMock,
    success: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    container.return_value.__enter__.return_value = container.return_value
    diagnose = MagicMock(
        side_effect=FfmpegError(
            "ffmpeg が見つかりません。YTLK_FFMPEG_PATH を確認してください。"
        )
    )
    monkeypatch.setattr(settings_page, "diagnose_ffmpeg", diagnose)

    settings_page._render_environment_settings(_settings(tmp_path))

    assert call("取得できません") in code.call_args_list
    message = warning.call_args.args[0]
    assert "ffmpeg-full" in message
    assert ".env" in message
    assert "YTLK_FFMPEG_PATH" in message
    success.assert_not_called()


@patch.object(settings_page.st, "success")
@patch.object(settings_page.st, "warning")
@patch.object(settings_page.st, "code")
@patch.object(settings_page.st, "container")
def test_environment_settings_warns_when_subtitles_filter_is_missing(
    container: MagicMock,
    code: MagicMock,
    warning: MagicMock,
    success: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    container.return_value.__enter__.return_value = container.return_value
    monkeypatch.setattr(
        settings_page,
        "diagnose_ffmpeg",
        MagicMock(
            return_value=FfmpegDiagnostics(
                configured_path="ffmpeg",
                resolved_path="/opt/homebrew/bin/ffmpeg",
                version="ffmpeg version 8.0.1",
                subtitles_available=False,
            )
        ),
    )

    settings_page._render_environment_settings(_settings(tmp_path))

    message = warning.call_args.args[0]
    assert "ffmpeg-full" in message
    assert ".env" in message
    assert "YTLK_FFMPEG_PATH" in message
    success.assert_not_called()


@patch.object(settings_page.st, "form_submit_button", return_value=True)
@patch.object(settings_page.st, "text_input", return_value="@new-channel")
@patch.object(settings_page.st, "form")
@patch.object(settings_page, "get_default_channel_handle", return_value="@old-channel")
@patch.object(settings_page, "_save_channel_handle")
def test_channel_form_saves_submitted_handle(
    save_handle: MagicMock,
    get_handle: MagicMock,
    form: MagicMock,
    text_input: MagicMock,
    submit: MagicMock,
    tmp_path: Path,
) -> None:
    form.return_value.__enter__.return_value = form.return_value
    settings = _settings(tmp_path)

    settings_page._render_channel_settings(settings)

    get_handle.assert_called_once_with(settings)
    assert text_input.call_args.kwargs["value"] == "@old-channel"
    submit.assert_called_once()
    save_handle.assert_called_once_with("@new-channel", settings)


@patch.object(settings_page.st, "form_submit_button", return_value=False)
@patch.object(settings_page.st, "text_input", return_value="@new-channel")
@patch.object(settings_page.st, "form")
@patch.object(settings_page, "get_default_channel_handle", return_value=None)
@patch.object(settings_page, "_save_channel_handle")
def test_channel_form_does_not_save_before_submit(
    save_handle: MagicMock,
    get_handle: MagicMock,
    form: MagicMock,
    text_input: MagicMock,
    submit: MagicMock,
    tmp_path: Path,
) -> None:
    form.return_value.__enter__.return_value = form.return_value

    settings_page._render_channel_settings(_settings(tmp_path))

    assert text_input.call_args.kwargs["value"] == ""
    submit.assert_called_once()
    save_handle.assert_not_called()


@patch.object(settings_page.st, "caption")
@patch.object(settings_page.st, "subheader")
def test_schedule_placeholder_is_visible(
    subheader: MagicMock,
    caption: MagicMock,
    tmp_path: Path,
) -> None:
    with (
        patch.object(
            settings_page,
            "load_schedule_policy",
            side_effect=ScheduleError("設定が壊れています。"),
        ),
        patch.object(settings_page.st, "error"),
    ):
        settings_page._render_schedule_placeholder(_settings(tmp_path))

    subheader.assert_called_once_with("投稿スケジュール")
    assert "次の空き枠" in caption.call_args.args[0]


@patch.object(settings_page, "_save_schedule_policy")
@patch.object(settings_page.st, "container")
@patch.object(settings_page.st, "form")
@patch.object(settings_page.st, "form_submit_button", return_value=False)
@patch.object(settings_page.st, "text_input", side_effect=["09:00", "Asia/Tokyo"])
@patch.object(settings_page.st, "number_input", return_value=1)
@patch.object(settings_page, "count_upload_attempts", return_value=3)
@patch.object(settings_page, "load_schedule_policy", return_value=SchedulePolicy())
def test_schedule_form_does_not_save_before_submit(
    load: MagicMock,
    attempts: MagicMock,
    number: MagicMock,
    text_input: MagicMock,
    submit: MagicMock,
    form: MagicMock,
    container: MagicMock,
    save: MagicMock,
    tmp_path: Path,
) -> None:
    form.return_value.__enter__.return_value = form.return_value
    container.return_value.__enter__.return_value = container.return_value
    settings_page._render_schedule_placeholder(_settings(tmp_path))
    save.assert_not_called()


@patch.object(settings_page.st, "success")
@patch.object(settings_page, "save_schedule_policy")
def test_save_schedule_policy_validates_and_saves_only_valid_values(
    save: MagicMock,
    success: MagicMock,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings_page._save_schedule_policy("18:30", 2, "Asia/Tokyo", settings)
    policy = save.call_args.args[0]
    assert policy == SchedulePolicy(daily_time="18:30", interval_days=2)
    assert save.call_args.args[1] == settings
    success.assert_called_once()


@patch.object(settings_page.st, "error")
@patch.object(settings_page, "save_schedule_policy")
def test_save_schedule_policy_shows_japanese_validation_error(
    save: MagicMock,
    error: MagicMock,
    tmp_path: Path,
) -> None:
    settings_page._save_schedule_policy("9:00", 0, "bad-zone", _settings(tmp_path))
    save.assert_not_called()
    message = error.call_args.args[0]
    assert "入力が正しくありません" in message
    assert "errors.pydantic.dev" not in message
    assert "validation error" not in message.lower()


@patch.object(settings_page, "_render_schedule_placeholder")
@patch.object(settings_page, "render_storage_manager")
@patch.object(settings_page, "_render_codex_status")
@patch.object(settings_page, "_render_environment_settings")
@patch.object(settings_page, "_render_channel_settings")
@patch.object(settings_page, "get_settings")
def test_settings_page_connects_storage_manager(
    get_settings: MagicMock,
    channel: MagicMock,
    environment: MagicMock,
    codex: MagicMock,
    storage: MagicMock,
    schedule: MagicMock,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    get_settings.return_value = settings

    settings_page.render_settings_page()

    storage.assert_called_once_with(settings)
    schedule.assert_called_once_with(settings)
