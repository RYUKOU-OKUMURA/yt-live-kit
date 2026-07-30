"""実行ページ・結果表示の UI ヘルパー関数テスト（Streamlit 非依存部分）."""

from __future__ import annotations

import json

from yt_live_kit.ui.components.results import (
    _TEMPLATE_NOT_SET_MESSAGE,
    build_clipboard_copy_html,
)
from yt_live_kit.ui.pages.run import (
    _NO_TARGET_MESSAGE,
    batch_summary_severity,
    single_run_disabled,
)


def test_single_run_disabled_when_both_targets_off() -> None:
    assert single_run_disabled(
        busy=False,
        url="https://www.youtube.com/watch?v=abc",
        do_chapters=False,
        do_clips=False,
    ) is True


def test_single_run_disabled_when_one_target_on() -> None:
    assert single_run_disabled(
        busy=False,
        url="https://www.youtube.com/watch?v=abc",
        do_chapters=True,
        do_clips=False,
    ) is False
    assert single_run_disabled(
        busy=False,
        url="https://www.youtube.com/watch?v=abc",
        do_chapters=False,
        do_clips=True,
    ) is False


def test_single_run_disabled_when_busy_or_empty_url() -> None:
    assert single_run_disabled(
        busy=True,
        url="https://www.youtube.com/watch?v=abc",
        do_chapters=True,
        do_clips=True,
    ) is True
    assert single_run_disabled(
        busy=False,
        url="  ",
        do_chapters=True,
        do_clips=True,
    ) is True


def test_no_target_message_is_japanese_guidance() -> None:
    assert "チャプター" in _NO_TARGET_MESSAGE
    assert "切り抜き候補" in _NO_TARGET_MESSAGE


def test_build_clipboard_copy_html_embeds_json_encoded_text() -> None:
    text = '0:00 開始\n5:00 "引用"\n\\バックスラッシュ'
    html = build_clipboard_copy_html(
        text=text,
        button_id="copy_test123",
        button_label="コピー",
    )

    assert json.dumps(text, ensure_ascii=False) in html
    assert "navigator.clipboard.writeText" in html
    assert "copy_test123" in html
    assert "コピーしました" in html
    assert text not in html


def test_build_clipboard_copy_html_includes_button_and_success_message() -> None:
    html = build_clipboard_copy_html(
        text="plain text",
        button_id="copy_plain",
        button_label="コピー",
    )
    assert 'type="button"' in html
    assert "コピーしました" in html
    assert "copy_plain-msg" in html


def test_template_not_set_message_mentions_config_path() -> None:
    assert "description_template.txt" in _TEMPLATE_NOT_SET_MESSAGE
    assert "{{timeline}}" in _TEMPLATE_NOT_SET_MESSAGE


def test_batch_summary_severity_still_works_from_run_page() -> None:
    assert batch_summary_severity(success=0, failed=0) == "info"
    assert batch_summary_severity(success=5, failed=0) == "success"
