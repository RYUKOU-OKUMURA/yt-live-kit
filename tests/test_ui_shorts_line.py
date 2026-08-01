"""ショート生産ライン UI の表示状態と安全接続のテスト."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.shorts_line import (
    DailyLineSummary,
    LineStage,
    LineStateError,
    confirm_preview,
    confirm_review,
    create_line_state,
    load_line_state,
    record_output,
    save_active_line,
    save_line_state,
    set_review_fingerprint,
)
from yt_live_kit.ui.components import shorts_line
from yt_live_kit.ui.views._local_settings import (
    ShortsLineDefaults,
    load_shorts_line_defaults,
)


def _reviewed_output_state(output_path: Path, settings: Settings):
    review = "b" * 64
    state = create_line_state("video-1", "clip-1", "a" * 64, review_fingerprint=review)
    state = confirm_review(state, review)
    state = record_output(state, output_path)
    state = confirm_preview(state, output_path)
    save_line_state(state, settings)
    save_active_line("video-1", "clip-1", settings)
    return state


@pytest.mark.parametrize(
    ("output", "running", "source", "expected"),
    [
        (False, False, True, "source"),
        (False, True, True, "generating"),
        (True, True, False, "output"),
        (False, False, False, "source_missing"),
    ],
)
def test_choose_preview_mode_has_four_explicit_states(
    output: bool,
    running: bool,
    source: bool,
    expected: str,
) -> None:
    assert shorts_line.choose_preview_mode(
        output_available=output,
        generation_running=running,
        source_available=source,
    ) == expected


def test_stage_bar_renders_six_stages_and_current_marker() -> None:
    @contextmanager
    def container(*_args: object, **_kwargs: object):
        yield

    with (
        patch("yt_live_kit.ui.components.shorts_line.st.container", side_effect=container),
        patch("yt_live_kit.ui.components.shorts_line.st.badge") as badge,
    ):
        shorts_line.render_stage_bar(LineStage.TELOP_REVIEW)

    assert badge.call_count == 6
    labels = [call.args[0] for call in badge.call_args_list]
    assert labels[0].endswith("完了")
    assert "3. テロップ確認・進行中" in labels
    assert labels[-1].endswith("待機中")


def test_human_confirmation_does_not_return_after_edit_and_revert() -> None:
    original = "b" * 64
    changed = "c" * 64
    state = create_line_state("video-1", "clip-1", "a" * 64, review_fingerprint=original)
    state = confirm_review(state, original)
    assert shorts_line.is_human_review_current(state, original) is True

    state = set_review_fingerprint(state, changed)
    assert shorts_line.is_human_review_current(state, changed) is False
    state = set_review_fingerprint(state, original)
    assert shorts_line.is_human_review_current(state, original) is False


def test_validate_line_reservation_invalidates_changed_output(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    output_path = tmp_path / "video-1" / "shorts" / "output" / "short_clip-1.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"first")
    _reviewed_output_state(output_path, settings)

    output_path.write_bytes(b"changed")
    with pytest.raises(LineStateError, match="もう一度プレビュー"):
        shorts_line.validate_line_reservation(
            "video-1", "clip-1", output_path, settings
        )

    persisted = load_line_state("video-1", "clip-1", settings)
    assert persisted is not None
    assert persisted.current_stage == LineStage.FINAL_REVIEW
    assert persisted.preview_confirmed_fingerprint is None


def test_upload_callback_invalidates_if_output_changes_after_preview(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    output_path = tmp_path / "video-1" / "shorts" / "output" / "short_clip-1.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"first")
    _reviewed_output_state(output_path, settings)
    output_path.write_bytes(b"changed-after-preview")

    with pytest.raises(LineStateError, match="もう一度プレビュー"):
        shorts_line.record_line_upload(
            "video-1", "clip-1", "operation-1", output_path, settings
        )
    persisted = load_line_state("video-1", "clip-1", settings)
    assert persisted is not None
    assert persisted.current_stage == LineStage.FINAL_REVIEW
    assert persisted.upload_operation_id is None


def test_sidebar_is_display_only_and_shows_daily_progress(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    daily = DailyLineSummary(completed_count=1, needs_attention_count=2)
    with (
        patch("yt_live_kit.ui.components.shorts_line.resolve_active_line", return_value=None),
        patch("yt_live_kit.ui.components.shorts_line.load_daily_line_summary", return_value=daily),
        patch("yt_live_kit.ui.components.shorts_line.st.divider"),
        patch("yt_live_kit.ui.components.shorts_line.st.markdown") as markdown,
        patch("yt_live_kit.ui.components.shorts_line.st.caption") as caption,
        patch("yt_live_kit.ui.components.shorts_line.st.write") as write,
        patch("yt_live_kit.ui.components.shorts_line.st.button") as button,
    ):
        shorts_line.render_sidebar_line_context("video-1", settings)

    markdown.assert_called_once_with("**作成中のショート**")
    assert any("作成中のラインはありません" in call.args[0] for call in caption.call_args_list)
    assert any("本日のライン完了 1／3" in call.args[0] for call in write.call_args_list)
    button.assert_not_called()


def test_line_defaults_are_read_only_with_safe_fallback(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults()

    path = tmp_path / "_config" / "shorts_defaults.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"layout": "crop", "preset": "boxed", "hook_preset": "hook"}
        ),
        encoding="utf-8",
    )
    assert load_shorts_line_defaults(settings) == ShortsLineDefaults(
        "crop", "boxed", "hook"
    )
