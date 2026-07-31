"""設定ページのテスト."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

from yt_live_kit.config import Settings
from yt_live_kit.ui.views import settings as settings_page


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
) -> None:
    settings_page._render_schedule_placeholder()

    subheader.assert_called_once_with("投稿スケジュール")
    assert "フェーズ P" in caption.call_args.args[0]
