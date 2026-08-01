"""UI ヘルパー関数のテスト（Streamlit 非依存部分）."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        "取り込み",
        "設定",
    }


def test_app_pages_have_unique_explicit_url_paths() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    page_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
        and node.func.attr == "Page"
    ]

    url_paths: list[str] = []
    for call in page_calls:
        keyword = next(
            (item for item in call.keywords if item.arg == "url_path"),
            None,
        )
        assert keyword is not None, "すべての st.Page に url_path が必要です"
        assert isinstance(keyword.value, ast.Constant)
        assert isinstance(keyword.value.value, str)
        assert keyword.value.value
        url_paths.append(keyword.value.value)

    assert len(url_paths) == 4
    assert len(url_paths) == len(set(url_paths))
    assert set(url_paths) == {
        "library",
        "intake",
        "video-detail",
        "settings",
    }


def test_app_has_four_pages_and_no_legacy_history_route() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    source = app_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    navigation_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "navigation"
    )
    assert isinstance(navigation_call.args[0], ast.List)
    assert len(navigation_call.args[0].elts) == 4
    assert "ui.views.history" not in source
    assert 'url_path="history"' not in source


def test_video_detail_page_is_hidden_from_public_navigation() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))

    detail_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "detail_page"
            for target in node.targets
        )
    )
    assert isinstance(detail_assignment.value, ast.Call)
    visibility = next(
        keyword
        for keyword in detail_assignment.value.keywords
        if keyword.arg == "visibility"
    )
    assert isinstance(visibility.value, ast.Constant)
    assert visibility.value.value == "hidden"


def test_settings_page_is_included_in_navigation_list() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))

    settings_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "settings_page"
            for target in node.targets
        )
    )
    assert isinstance(settings_assignment.value, ast.Call)
    title_keyword = next(
        keyword
        for keyword in settings_assignment.value.keywords
        if keyword.arg == "title"
    )
    url_keyword = next(
        keyword
        for keyword in settings_assignment.value.keywords
        if keyword.arg == "url_path"
    )
    assert isinstance(title_keyword.value, ast.Constant)
    assert title_keyword.value.value == "設定"
    assert isinstance(url_keyword.value, ast.Constant)
    assert url_keyword.value.value == "settings"

    navigation_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
        and node.func.attr == "navigation"
    )
    assert navigation_call.args
    assert isinstance(navigation_call.args[0], ast.List)
    navigation_names = {
        item.id
        for item in navigation_call.args[0].elts
        if isinstance(item, ast.Name)
    }
    assert "settings_page" in navigation_names


def test_running_status_is_rendered_inside_sidebar() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    sidebar = next(
        node
        for node in tree.body
        if isinstance(node, ast.With)
        and isinstance(node.items[0].context_expr, ast.Attribute)
        and isinstance(node.items[0].context_expr.value, ast.Name)
        and node.items[0].context_expr.value.id == "st"
        and node.items[0].context_expr.attr == "sidebar"
    )
    calls = [
        node.value.func.id
        for node in sidebar.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]

    assert calls == ["render_status_bar", "render_sidebar_line_context"]
    assert not any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "render_status_bar"
        for node in tree.body
    )


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
    assert kind_label("short_cut") == "ショート区間提案"
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


def test_running_short_cut_shows_known_name_progress_and_elapsed() -> None:
    from yt_live_kit.ui.components import status_bar

    started = datetime.now(timezone.utc) - timedelta(seconds=12)
    job = JobState(
        job_id="short-cut-running",
        kind="short_cut",
        status="running",
        message="候補を解析中",
        current=1,
        total=3,
        started_at=started,
    )
    with (
        patch.object(status_bar.st, "markdown") as markdown,
        patch.object(status_bar.st, "progress") as progress,
    ):
        status_bar._render_running_job(job)

    markdown.assert_called_once_with("**実行中の処理**")
    message = progress.call_args.kwargs["text"]
    assert "ショート区間提案" in message
    assert "候補を解析中" in message
    assert "1/3" in message
    assert "経過" in message


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


def test_handle_finished_cut_clip_job_sets_cut_result(tmp_path) -> None:
    from yt_live_kit.services.clips import cut_result_to_ref
    from yt_live_kit.services.ffmpeg import CutResult
    from yt_live_kit.ui.components import status_bar

    cut_result = CutResult(
        video_id="vid123",
        output_path=tmp_path / "vid123" / "clips" / "output" / "clip_001.mp4",
        command_log_path=tmp_path / "vid123" / "clips" / "output" / "clip_001.ffmpeg.log",
        start="00:03:42",
        end="00:16:30",
        duration_sec=768,
    )
    job = JobState(
        job_id="cut-done",
        kind="cut_clip",
        status="done",
        result_ref=cut_result_to_ref(cut_result),
    )

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled") as mark_handled,
        patch("yt_live_kit.ui.components.status_bar.set_cut_result") as set_cut,
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk") as load_result,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    load_result.assert_not_called()
    set_cut.assert_called_once_with(cut_result)
    clear_active.assert_called_once()
    mark_handled.assert_called_once_with("cut-done")
    rerun.assert_called_once_with(scope="app")


@pytest.mark.parametrize("kind", ["batch", "shorts_queue", "upload", "short_cut"])
def test_non_pipeline_finished_jobs_never_use_pipeline_loader(kind: str) -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id=f"done-{kind}", kind=kind, status="done",
        result_ref="operation-1" if kind == "upload" else "video1",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk") as pipeline_loader,
        patch("yt_live_kit.ui.components.status_bar.load_operation"),
        patch("yt_live_kit.ui.components.status_bar.read_batch_summary", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)
    pipeline_loader.assert_not_called()


def test_finished_short_cut_is_known_and_reloads_screen_without_error() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="done-short-cut",
        kind="short_cut",
        status="done",
        video_id="video-1",
        result_ref="video-1",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk") as loader,
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    loader.assert_not_called()
    set_error.assert_not_called()
    rerun.assert_called_once_with(scope="app")


def test_failed_short_cut_keeps_original_japanese_error() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="failed-short-cut",
        kind="short_cut",
        status="failed",
        error="サブ区間の提案に失敗しました。字幕を確認してください。",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    set_error.assert_called_once_with(
        "サブ区間の提案に失敗しました。字幕を確認してください。"
    )


def test_finished_all_failed_shorts_queue_reads_manifest_and_reports_failure() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="done-queue",
        kind="shorts_queue",
        status="done",
        video_id="video-1",
        result_ref="video-1",
    )
    failed_item = MagicMock(
        status="failed",
        target_id="clip<1>",
        error="subtitles <filter> がありません。",
    )
    result = MagicMock(success_count=0, failure_count=1, items=(failed_item,))
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch(
            "yt_live_kit.ui.components.status_bar.load_shorts_queue_result",
            return_value=result,
        ) as load_result,
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    load_result.assert_called_once_with("video-1", "done-queue", status_bar.get_settings())
    set_error.assert_called_once()
    message = set_error.call_args.args[0]
    assert "すべて失敗" in message
    assert "clip〈1〉: subtitles 〈filter〉 がありません。" in message
    assert "<" not in message and ">" not in message


def test_finished_partially_failed_shorts_queue_reports_each_safe_reason() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="partial-queue",
        kind="shorts_queue",
        status="done",
        video_id="video-1",
        result_ref="video-1",
    )
    succeeded = MagicMock(status="succeeded")
    first_failed = MagicMock(
        status="failed",
        target_id="clip<1>",
        error="字幕<filter>がありません。",
    )
    second_failed = MagicMock(
        status="failed",
        target_id="clip-2",
        error="動画を生成できませんでした。",
    )
    result = MagicMock(
        success_count=1,
        failure_count=2,
        items=(succeeded, first_failed, second_failed),
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch(
            "yt_live_kit.ui.components.status_bar.load_shorts_queue_result",
            return_value=result,
        ),
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    set_error.assert_called_once()
    message = set_error.call_args.args[0]
    assert "成功 1 件 / 失敗 2 件" in message
    assert "clip〈1〉: 字幕〈filter〉がありません。" in message
    assert "clip-2: 動画を生成できませんでした。" in message
    assert message.index("clip〈1〉") < message.index("clip-2")
    assert "<" not in message and ">" not in message


def test_unknown_finished_job_reports_japanese_error_without_pipeline_load() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(job_id="done-unknown", kind="mystery", status="done", result_ref="x")
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk") as loader,
        patch("yt_live_kit.ui.components.status_bar.set_job_error") as set_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)
    loader.assert_not_called()
    assert "未対応" in set_error.call_args.args[0]


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
        "成果物を読み込めませんでした。ライブラリから開き直してください。"
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
    from yt_live_kit.ui.views.intake import batch_summary_severity

    assert batch_summary_severity(success=0, failed=0) == "info"


def test_batch_summary_severity_branches():
    from yt_live_kit.ui.views.intake import batch_summary_severity

    assert batch_summary_severity(success=3, failed=1) == "warning"
    assert batch_summary_severity(success=0, failed=2) == "error"
    assert batch_summary_severity(success=5, failed=0) == "success"
