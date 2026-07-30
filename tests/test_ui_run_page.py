"""実行ページ・結果表示の UI ヘルパー関数テスト（Streamlit 非依存部分）."""

from __future__ import annotations

import json

from yt_live_kit.ui.components.results import (
    _CHAPTERS_NOT_GENERATED_MESSAGE,
    _CLIPS_EMPTY_NOT_REQUESTED_MESSAGE,
    _CLIPS_EMPTY_REQUESTED_MESSAGE,
    _TEMPLATE_NOT_SET_MESSAGE,
    build_clipboard_copy_html,
    clips_empty_message,
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


def test_build_clipboard_copy_html_default_hide_after_ms_is_2000() -> None:
    html = build_clipboard_copy_html(
        text="plain text",
        button_id="copy_default_ms",
        button_label="コピー",
    )
    assert "2000" in html
    assert "3000" not in html


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


def test_build_clipboard_copy_html_escapes_closing_script_tag() -> None:
    text = "本文</script><script>alert(1)</script>"
    html = build_clipboard_copy_html(
        text=text,
        button_id="copy_xss",
        button_label="コピー",
    )

    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script>" in html
    # 実際に閉じている </script> はコピー用ラッパー自身の 1 つだけ
    # （テキスト中の </script> はすべて <\/script> にエスケープされ、
    #   早期にタグを閉じない）。
    assert html.count("</script>") == 1


def test_chapters_not_generated_message_mentions_processed_list() -> None:
    assert "チャプター" in _CHAPTERS_NOT_GENERATED_MESSAGE
    assert "処理済み一覧" in _CHAPTERS_NOT_GENERATED_MESSAGE


def test_clips_empty_message_when_requested_mentions_codex() -> None:
    assert clips_empty_message(True) == _CLIPS_EMPTY_REQUESTED_MESSAGE
    assert "Codex" in clips_empty_message(True)


def test_clips_empty_message_when_not_requested_avoids_codex() -> None:
    assert clips_empty_message(False) == _CLIPS_EMPTY_NOT_REQUESTED_MESSAGE
    assert "Codex" not in clips_empty_message(False)
