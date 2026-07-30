"""UI ヘルパー関数のテスト（Streamlit 非依存部分）."""

from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_CLIPS_SUGGEST,
    STAGE_FETCH,
    STAGE_TRANSCRIPT,
)
from yt_live_kit.ui.app import _mark_failed_stage, _render_progress

_STAGE_ORDER = [STAGE_FETCH, STAGE_TRANSCRIPT, STAGE_CHAPTERS, STAGE_CLIPS_SUGGEST]


def test_render_progress_shows_error_state() -> None:
    progress_state = {
        STAGE_FETCH: "complete",
        STAGE_TRANSCRIPT: "error",
        STAGE_CHAPTERS: "pending",
        STAGE_CLIPS_SUGGEST: "pending",
    }
    progress_ctx = {"message": "test"}

    rendered = _render_progress(progress_state, progress_ctx)

    assert "✅" in rendered
    assert "❌" in rendered
    assert "エラー" in rendered
    assert "待機中" in rendered


def test_mark_failed_stage_marks_running_as_error() -> None:
    progress_state = {
        STAGE_FETCH: "complete",
        STAGE_TRANSCRIPT: "running",
        STAGE_CHAPTERS: "pending",
        STAGE_CLIPS_SUGGEST: "pending",
    }

    _mark_failed_stage(progress_state)

    assert progress_state[STAGE_TRANSCRIPT] == "error"
    assert progress_state[STAGE_CHAPTERS] == "pending"
