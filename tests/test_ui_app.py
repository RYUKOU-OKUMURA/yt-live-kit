"""UI ヘルパー関数のテスト（Streamlit 非依存部分）."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_live_kit.services.jobs import JobState
from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_CLIPS_SUGGEST,
    STAGE_FETCH,
    STAGE_TRANSCRIPT,
)
from yt_live_kit.ui.components.progress import mark_failed_stage, render_progress
from yt_live_kit.ui.components.status_bar import (
    elapsed_seconds,
    format_status_message,
    kind_label,
    should_show_running_bar,
)

_STAGE_ORDER = [STAGE_FETCH, STAGE_TRANSCRIPT, STAGE_CHAPTERS, STAGE_CLIPS_SUGGEST]


def test_app_registers_japanese_navigation_after_page_config() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))

    streamlit_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    ]
    call_positions = {
        call.func.attr: (call.lineno, call.col_offset) for call in streamlit_calls
    }
    assert call_positions["set_page_config"] < call_positions["navigation"]

    page_titles = {
        keyword.value.value
        for call in streamlit_calls
        if call.func.attr == "Page"
        for keyword in call.keywords
        if keyword.arg == "title" and isinstance(keyword.value, ast.Constant)
    }
    assert page_titles == {
        "ライブラリ",
        "動画詳細",
        "実行",
        "チャンネル",
        "処理済み一覧",
    }


def test_render_progress_shows_error_state() -> None:
    progress_state = {
        STAGE_FETCH: "complete",
        STAGE_TRANSCRIPT: "error",
        STAGE_CHAPTERS: "pending",
        STAGE_CLIPS_SUGGEST: "pending",
    }
    progress_ctx = {"message": "test"}

    rendered = render_progress(progress_state, progress_ctx)

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

    mark_failed_stage(progress_state)

    assert progress_state[STAGE_TRANSCRIPT] == "error"
    assert progress_state[STAGE_CHAPTERS] == "pending"


def test_kind_label_returns_japanese_name() -> None:
    assert kind_label("single") == "単本処理"
    assert kind_label("batch") == "一括処理"
    assert kind_label("unknown") == "unknown"


def test_format_status_message_includes_elapsed_and_counts() -> None:
    started = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 30, 12, 0, 45, tzinfo=timezone.utc)
    job = JobState(
        job_id="abc",
        kind="batch",
        status="running",
        message="処理中",
        current=2,
        total=5,
        started_at=started,
    )

    message = format_status_message(job, now=now)

    assert "一括処理" in message
    assert "処理中" in message
    assert "45 秒" in message
    assert "2/5" in message


def test_should_show_running_bar_only_for_running_jobs() -> None:
    running = JobState(job_id="a", kind="single", status="running")
    done = JobState(job_id="b", kind="single", status="done")

    assert should_show_running_bar(running) is True
    assert should_show_running_bar(done) is False
    assert should_show_running_bar(None) is False


def test_elapsed_seconds_is_non_negative() -> None:
    started = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 30, 12, 1, 30, tzinfo=timezone.utc)
    job = JobState(job_id="a", kind="single", status="running", started_at=started)

    assert elapsed_seconds(job, now=now) == 90


def test_handle_finished_job_loads_result_on_done() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="done123",
        kind="single",
        status="done",
        result_ref="video1234567",
    )
    mock_result = MagicMock()

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled") as mark_handled,
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk", return_value=mock_result) as load_result,
        patch("yt_live_kit.ui.components.status_bar.set_result") as set_result,
        patch("yt_live_kit.ui.components.status_bar.clear_cut_result") as clear_cut,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    load_result.assert_called_once_with("video1234567", status_bar.get_settings())
    set_result.assert_called_once_with(mock_result)
    clear_cut.assert_called_once()
    clear_active.assert_called_once()
    mark_handled.assert_called_once_with("done123")
    rerun.assert_called_once_with(scope="app")


def test_handle_finished_job_shows_error_on_failed() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="fail123",
        kind="single",
        status="failed",
        error="字幕が見つかりません",
    )

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled") as mark_handled,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    set_error.assert_called_once_with("字幕が見つかりません")
    clear_active.assert_called_once()
    mark_handled.assert_called_once_with("fail123")
    rerun.assert_called_once_with(scope="app")


def test_handle_finished_job_shows_error_when_result_missing() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="done-missing",
        kind="single",
        status="done",
        result_ref="missing1234567",
    )

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled") as mark_handled,
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.set_result") as set_result,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    set_error.assert_called_once_with(
        "成果物を読み込めませんでした。処理済み一覧から開き直してください。"
    )
    set_result.assert_not_called()
    clear_active.assert_called_once()
    mark_handled.assert_called_once_with("done-missing")
    rerun.assert_called_once_with(scope="app")


def test_handle_finished_job_loads_batch_summary_on_done_without_result_ref() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="batch-done",
        kind="batch",
        status="done",
        result_ref=None,
    )
    batch_summary = {
        "summary": "一括処理完了: 成功 1 / スキップ 0 / 失敗 0",
        "lines": ["✅ https://example.com"],
        "success": 1,
        "skipped": 0,
        "failed": 0,
    }

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.read_batch_summary", return_value=batch_summary),
        patch("yt_live_kit.ui.components.status_bar.set_batch_summary") as set_batch_summary,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    set_batch_summary.assert_called_once_with(batch_summary)


def test_find_restorable_job_uses_last_job_id() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(job_id="last-job", kind="batch", status="done")
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.components.status_bar.get_last_job_id", return_value="last-job"),
        patch("yt_live_kit.ui.components.status_bar.read_job", return_value=job),
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
    ):
        found = status_bar.find_restorable_job(settings)

    assert found is job


def test_find_restorable_job_skips_handled_jobs() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(job_id="handled-job", kind="single", status="done")
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.components.status_bar.get_last_job_id", return_value="handled-job"),
        patch("yt_live_kit.ui.components.status_bar.read_job", return_value=job),
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=True),
        patch("yt_live_kit.ui.components.status_bar.read_current_job") as read_current,
    ):
        found = status_bar.find_restorable_job(settings)

    assert found is None
    read_current.assert_not_called()


def test_find_restorable_job_falls_back_to_current_job() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="current-job",
        kind="single",
        status="failed",
        finished_at=datetime.now(timezone.utc),
    )
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.components.status_bar.get_last_job_id", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.read_current_job", return_value=job),
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
    ):
        found = status_bar.find_restorable_job(settings)

    assert found is job


def test_is_recently_finished_returns_false_when_finished_at_is_none() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(job_id="a", kind="single", status="done", finished_at=None)

    assert status_bar.is_recently_finished(job) is False


def test_is_recently_finished_true_within_window() -> None:
    from yt_live_kit.ui.components import status_bar

    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    finished = now - timedelta(minutes=5)
    job = JobState(job_id="a", kind="single", status="done", finished_at=finished)

    assert status_bar.is_recently_finished(job, now=now) is True


def test_is_recently_finished_false_outside_window() -> None:
    from yt_live_kit.ui.components import status_bar

    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    finished = now - timedelta(days=3)
    job = JobState(job_id="a", kind="single", status="done", finished_at=finished)

    assert status_bar.is_recently_finished(job, now=now) is False


def test_is_recently_finished_handles_naive_datetime() -> None:
    from yt_live_kit.ui.components import status_bar

    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    finished_naive = datetime(2026, 7, 30, 11, 58, 0)  # tz なし

    job = JobState(job_id="a", kind="single", status="done", finished_at=finished_naive)

    # 例外を送出せず、UTC とみなして窓内と判定される
    assert status_bar.is_recently_finished(job, now=now) is True


def test_find_restorable_job_via_current_job_ignores_old_finished_job() -> None:
    """read_current_job() 経路では、数日前に完了したジョブは復元対象にならない."""
    from yt_live_kit.ui.components import status_bar

    old_job = JobState(
        job_id="old-current-job",
        kind="single",
        status="failed",
        finished_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    settings = MagicMock()

    with (
        patch("yt_live_kit.ui.components.status_bar.get_last_job_id", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.read_current_job", return_value=old_job),
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
    ):
        found = status_bar.find_restorable_job(settings)

    assert found is None


def test_find_restorable_job_via_last_job_id_ignores_time_window() -> None:
    """get_last_job_id() 経路（同一セッション）では時間制限をかけない."""
    from yt_live_kit.ui.components import status_bar

    old_job = JobState(
        job_id="old-last-job",
        kind="single",
        status="done",
        finished_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    settings = MagicMock()

    with (
        patch(
            "yt_live_kit.ui.components.status_bar.get_last_job_id",
            return_value="old-last-job",
        ),
        patch("yt_live_kit.ui.components.status_bar.read_job", return_value=old_job),
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
    ):
        found = status_bar.find_restorable_job(settings)

    assert found is old_job


def test_batch_summary_severity_all_skipped_is_info():
    """全件スキップ（成功0・失敗0）は正常動作なのでエラー扱いにしない."""
    from yt_live_kit.ui.views.run import batch_summary_severity

    assert batch_summary_severity(success=0, failed=0) == "info"


def test_batch_summary_severity_branches():
    from yt_live_kit.ui.views.run import batch_summary_severity

    assert batch_summary_severity(success=3, failed=1) == "warning"
    assert batch_summary_severity(success=0, failed=2) == "error"
    assert batch_summary_severity(success=5, failed=0) == "success"
