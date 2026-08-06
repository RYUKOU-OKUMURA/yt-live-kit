"""UI ヘルパー関数のテスト（Streamlit 非依存部分）."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import streamlit as st

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
    job_display_label,
    kind_label,
    retry_hint_for_job,
    should_show_running_bar,
)
from yt_live_kit.ui.state import (
    SESSION_GLOBAL_JOB_ERRORS,
    SESSION_HANDLED_JOBS,
    SESSION_JOB_ERROR_HISTORY,
    SESSION_PIPELINE_COMPLETIONS,
    SESSION_UNREAD_JOB_ERRORS,
    consume_unread_job_error_notifications,
    format_job_error_summary_for_display,
    get_global_job_error_notifications,
    get_job_error_history,
    get_pipeline_completion_notifications,
    get_selected_video_id,
    get_unread_job_error_notifications,
    record_pipeline_completion,
    record_job_error,
    set_selected_video_id,
)

_STAGE_ORDER = [STAGE_FETCH, STAGE_TRANSCRIPT, STAGE_CHAPTERS, STAGE_CLIPS_SUGGEST]


def _clear_job_error_notifications() -> None:
    for key in (
        SESSION_JOB_ERROR_HISTORY,
        SESSION_GLOBAL_JOB_ERRORS,
        SESSION_UNREAD_JOB_ERRORS,
    ):
        st.session_state.pop(key, None)


def _clear_pipeline_completion_notifications() -> None:
    st.session_state.pop(SESSION_PIPELINE_COMPLETIONS, None)
    st.session_state.pop(SESSION_HANDLED_JOBS, None)


def _load_unread_renderer(
    fake_st,
    *,
    selected_video_id=set_selected_video_id,
    consume=consume_unread_job_error_notifications,
):
    """app.pyの通知描画関数だけを、実APIと画面モックで実行する。"""
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_top_summary", "_render_unread_job_errors"}
    ]
    namespace = {
        "st": fake_st,
        "_TOP_SUMMARY_MAX_CHARS": 180,
        "format_job_error_summary_for_display": format_job_error_summary_for_display,
        "get_unread_job_error_notifications": get_unread_job_error_notifications,
        "consume_unread_job_error_notifications": consume,
        "set_selected_video_id": selected_video_id,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(app_path), "exec"), namespace)
    return namespace["_render_unread_job_errors"]


class _PageSwitch(Exception):
    pass


class _AppRerun(Exception):
    pass


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


def test_brand_and_running_status_are_rendered_inside_sidebar() -> None:
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

    assert calls == [
        "_render_sidebar_brand",
        "render_status_bar",
        "render_sidebar_line_context",
    ]
    assert not any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "render_status_bar"
        for node in tree.body
    )


def test_global_brand_is_compact_and_not_repeated_in_main() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    source = app_path.read_text(encoding="utf-8")

    assert 'st.markdown("### yt-live-kit")' in source
    assert 'st.caption(f"v{__version__}")' in source
    assert 'st.title("yt-live-kit")' not in source
    assert "YouTube ライブアーカイブのタイムライン生成" not in source


def test_app_defines_navigation_before_rendering_unread_job_errors() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    source = app_path.read_text(encoding="utf-8")

    assert source.index("page = st.navigation") < source.rindex(
        "_render_unread_job_errors(detail_page)"
    )
    assert "st.error(job_error)" not in source
    assert "clear_job_error" not in source


def test_app_target_video_link_sets_selection_before_hidden_detail_switch() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    source = app_path.read_text(encoding="utf-8")

    selected = source.index("set_selected_video_id(notice.video_id)")
    switched = source.index("st.switch_page(detail_page)")
    assert selected < switched
    assert "if notice.video_id:" in source


def test_unread_notice_initial_render_is_read_only_and_has_both_actions() -> None:
    _clear_job_error_notifications()
    set_selected_video_id(None)
    record_job_error("video-a", "job-a", "single", "処理に失敗しました。", "detail")

    fake_st = MagicMock()
    fake_st.button.return_value = False
    renderer = _load_unread_renderer(fake_st)
    renderer(object())

    assert len(get_unread_job_error_notifications()) == 1
    assert get_job_error_history("video-a")[0].detail == "detail"
    labels = [call.args[0] for call in fake_st.button.call_args_list]
    assert labels == ["対象動画を開く", "通知を閉じる"]
    _clear_job_error_notifications()


def test_video_notice_click_orders_selection_consume_switch_and_keeps_history() -> None:
    _clear_job_error_notifications()
    set_selected_video_id(None)
    record_job_error("video-a", "job-a", "single", "処理に失敗しました。", "detail")
    events: list[tuple[str, object]] = []

    fake_st = MagicMock()
    fake_st.button.side_effect = lambda label, key: label == "対象動画を開く"
    fake_st.switch_page.side_effect = lambda page: (
        events.append(("switch", page)),
        (_ for _ in ()).throw(_PageSwitch()),
    )[1]

    def select(video_id):
        events.append(("select", video_id))
        set_selected_video_id(video_id)

    def consume():
        events.append(("consume", None))
        return consume_unread_job_error_notifications()

    renderer = _load_unread_renderer(
        fake_st,
        selected_video_id=select,
        consume=consume,
    )
    detail_page = object()
    with pytest.raises(_PageSwitch):
        renderer(detail_page)

    assert events == [("select", "video-a"), ("consume", None), ("switch", detail_page)]
    assert get_unread_job_error_notifications() == []
    assert get_job_error_history("video-a")[0].detail == "detail"
    assert st.session_state.get("selected_video_id") == "video-a"
    _clear_job_error_notifications()


def test_global_notice_dismiss_consumes_only_unread_and_keeps_global_summary() -> None:
    _clear_job_error_notifications()
    record_job_error(None, "global-job", "batch", "一括処理に失敗しました。", "secret")
    events: list[str] = []

    fake_st = MagicMock()
    fake_st.button.side_effect = lambda label, key: label == "通知を閉じる"
    fake_st.rerun.side_effect = lambda **kwargs: (
        events.append(f"rerun:{kwargs['scope']}"),
        (_ for _ in ()).throw(_AppRerun()),
    )[1]
    renderer = _load_unread_renderer(
        fake_st,
        consume=lambda: (events.append("consume"), consume_unread_job_error_notifications())[1],
    )

    with pytest.raises(_AppRerun):
        renderer(object())

    assert events == ["consume", "rerun:app"]
    assert get_unread_job_error_notifications() == []
    global_errors = get_global_job_error_notifications()
    assert len(global_errors) == 1
    assert global_errors[0].detail is None
    _clear_job_error_notifications()


def test_pipeline_completion_state_is_bounded_and_deduplicated_by_job() -> None:
    _clear_pipeline_completion_notifications()
    completed_at = datetime(2026, 8, 4, 1, 2, tzinfo=timezone.utc)

    first = record_pipeline_completion(
        "video-b",
        "job-b",
        "single",
        "動画 B",
        completed_at,
    )
    duplicate = record_pipeline_completion(
        "wrong-video",
        "job-b",
        "single",
        "重複通知",
        completed_at + timedelta(seconds=1),
    )

    assert duplicate == first
    assert get_pipeline_completion_notifications() == [first]
    for offset, suffix in enumerate(("c", "d", "e"), start=2):
        record_pipeline_completion(
            f"video-{suffix}",
            f"job-{suffix}",
            "single",
            f"動画 {suffix.upper()}",
            completed_at + timedelta(seconds=offset),
        )
    assert [
        notice.job_id for notice in get_pipeline_completion_notifications()
    ] == ["job-e", "job-d", "job-c"]
    _clear_pipeline_completion_notifications()


def test_pipeline_completion_initial_render_is_read_only() -> None:
    from yt_live_kit.ui.components import status_bar

    _clear_pipeline_completion_notifications()
    notice = record_pipeline_completion("video-b", "job-b", "single", "動画 B")

    with (
        patch.object(status_bar.st, "success") as success,
        patch.object(status_bar.st, "button", return_value=False) as button,
    ):
        status_bar.render_pipeline_completion_notices(detail_page=object())

    assert get_pipeline_completion_notifications() == [notice]
    success.assert_called_once_with("「動画 B」の処理が完了しました。")
    assert [call.args[0] for call in button.call_args_list] == [
        "対象動画を開く",
        "通知を閉じる",
    ]
    _clear_pipeline_completion_notifications()


def test_pipeline_completion_for_video_b_opens_b_without_mixing_into_a() -> None:
    from yt_live_kit.ui.components import status_bar

    _clear_pipeline_completion_notifications()
    set_selected_video_id("video-a")
    record_pipeline_completion("video-b", "job-b", "single", "動画 B")
    detail_page = object()

    def click_target(label: str, **_kwargs) -> bool:
        return label == "対象動画を開く"

    with (
        patch.object(status_bar.st, "success"),
        patch.object(status_bar.st, "button", side_effect=click_target),
        patch.object(status_bar.st, "switch_page", side_effect=_PageSwitch),
    ):
        with pytest.raises(_PageSwitch):
            status_bar.render_pipeline_completion_notices(detail_page=detail_page)

    assert get_selected_video_id() == "video-b"
    assert get_pipeline_completion_notifications() == []
    _clear_pipeline_completion_notifications()


def test_status_summary_is_one_line_safe_and_bounded() -> None:
    from yt_live_kit.ui.components import status_bar

    summary = status_bar._format_error_summary(
        "処理に失敗しました <raw>\n" + "詳細" * 300,
        fallback="処理に失敗しました。",
    )

    assert "<" not in summary and ">" not in summary
    assert "\n" not in summary
    assert len(summary) == status_bar._ERROR_SUMMARY_MAX_CHARS


def test_empty_status_summary_uses_japanese_fallback() -> None:
    from yt_live_kit.ui.components import status_bar

    assert status_bar._format_error_summary("\n", fallback="処理に失敗しました。") == (
        "処理に失敗しました。"
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
    assert kind_label("upload_publication") == "公開状態確認"
    assert kind_label("unknown") == "unknown"


def test_job_display_label_distinguishes_whisper_refine_from_short_cut_suggest() -> None:
    assert (
        job_display_label(
            "short_cut",
            stage="resolver",
            error="選択区間の高精度字幕に失敗しました。\nS9_WHISPER_ERROR:{}",
        )
        == "選択区間の高精度字幕"
    )
    assert job_display_label("short_cut", message="候補を解析中") == "ショート区間提案"


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
        finished_at=datetime(2026, 8, 4, 1, 2, tzinfo=timezone.utc),
    )
    mock_result = MagicMock(video_id="video1234567", title="動画 123")

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled") as mark_handled,
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk", return_value=mock_result) as load_result,
        patch("yt_live_kit.ui.components.status_bar.record_pipeline_completion") as record_completion,
        patch("yt_live_kit.ui.components.status_bar.clear_result") as clear_result,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    load_result.assert_called_once_with("video1234567", status_bar.get_settings())
    record_completion.assert_called_once_with(
        "video1234567",
        "done123",
        "single",
        "動画 123",
        status_bar._job_finished_at(job),
    )
    clear_result.assert_called_once()
    clear_active.assert_called_once()
    mark_handled.assert_called_once_with("done123")
    rerun.assert_called_once_with(scope="app")


def test_finished_pipeline_notification_does_not_change_selected_video_and_handles_once() -> None:
    from yt_live_kit.ui.components import status_bar

    _clear_pipeline_completion_notifications()
    set_selected_video_id("video-a")
    job = JobState(
        job_id="done-video-b",
        kind="single",
        status="done",
        video_id="video-b",
        result_ref="video-b",
    )
    result = MagicMock(video_id="video-b", title="動画 B")

    with (
        patch(
            "yt_live_kit.ui.components.status_bar.load_result_from_disk",
            return_value=result,
        ) as load_result,
        patch("yt_live_kit.ui.components.status_bar.clear_result"),
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)
        status_bar._handle_finished_job(job)

    assert load_result.call_count == 1
    assert get_selected_video_id() == "video-a"
    notices = get_pipeline_completion_notifications()
    assert [(notice.video_id, notice.job_id) for notice in notices] == [
        ("video-b", "done-video-b")
    ]
    _clear_pipeline_completion_notifications()


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
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    loader.assert_not_called()
    record_error.assert_not_called()
    rerun.assert_called_once_with(scope="app")


def test_failed_short_cut_records_structured_error_detail() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="failed-short-cut",
        kind="short_cut",
        status="failed",
        video_id="video-short-cut",
        error="サブ区間の提案に失敗しました。字幕を確認してください。",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.read_job_error_log", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    args = record_error.call_args.args
    assert args[:3] == ("video-short-cut", "failed-short-cut", "short_cut")
    assert "ショート区間提案に失敗しました" in args[3]
    assert "サブ区間の提案に失敗しました" in args[4]


def test_failed_short_cut_refine_uses_high_precision_summary() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="failed-short-cut-refine",
        kind="short_cut",
        status="failed",
        stage="resolver",
        video_id="video-short-cut",
        error=(
            "選択区間の高精度字幕に失敗しました。"
            "S9_WHISPER_ERROR:{\"schema\":\"s9-whisper-error-v1\"}"
        ),
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.read_job_error_log", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    summary = record_error.call_args.args[3]
    assert "選択区間の高精度字幕に失敗しました" in summary
    assert "ショート区間提案に失敗しました" not in summary
    assert "高精度化を再試行" in summary


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
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    load_result.assert_called_once_with("video-1", "done-queue", status_bar.get_settings())
    record_error.assert_called_once()
    summary, detail = record_error.call_args.args[3:5]
    assert "全件失敗" in summary
    assert "clip<1>: subtitles <filter> がありません。" in detail
    assert "<" not in summary and ">" not in summary
    assert "\n" not in summary


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
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    summary, detail = record_error.call_args.args[3:5]
    assert "成功 1 件、失敗 2 件" in summary
    assert "clip<1>: 字幕<filter>がありません。" in detail
    assert "clip-2: 動画を生成できませんでした。" in detail
    assert "<" not in summary and ">" not in summary
    assert "\n" not in summary


def test_unknown_finished_job_reports_japanese_error_without_pipeline_load() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(job_id="done-unknown", kind="mystery", status="done", result_ref="x")
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.load_result_from_disk") as loader,
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)
    loader.assert_not_called()
    assert "未対応" in record_error.call_args.args[3]
    assert "<" not in record_error.call_args.args[3]


def test_handle_finished_job_shows_error_on_failed() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="fail123",
        kind="single",
        status="failed",
        video_id="video-failed",
        error="字幕が見つかりません",
    )

    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled") as mark_handled,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.read_job_error_log", return_value=None),
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    args = record_error.call_args.args
    assert args[:3] == ("video-failed", "fail123", "single")
    assert "字幕が見つかりません" in args[4]
    assert "単本処理に失敗しました" in args[3]
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
        patch("yt_live_kit.ui.components.status_bar.record_pipeline_completion") as record_completion,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id") as clear_active,
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.st.rerun") as rerun,
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    summary, detail = record_error.call_args.args[3:5]
    assert "成果物を読み込めませんでした" in summary
    assert "meta.json" in detail
    assert "<" not in summary and ">" not in summary
    record_completion.assert_not_called()
    clear_active.assert_called_once()
    mark_handled.assert_called_once_with("done-missing")
    rerun.assert_called_once_with(scope="app")


def test_finished_upload_without_operation_id_records_video_scoped_error() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="upload-missing-operation",
        kind="upload",
        status="done",
        video_id="video-upload",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    args = record_error.call_args.args
    assert args[:3] == ("video-upload", "upload-missing-operation", "upload")
    assert "operation ID" in args[3]
    assert "result_ref" in args[4]


def test_finished_upload_operation_load_error_keeps_queue_detail() -> None:
    from yt_live_kit.services.upload_queue import UploadQueueError
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="upload-corrupt-operation",
        kind="upload_publication",
        status="done",
        video_id="video-upload",
        result_ref="operation-1",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch(
            "yt_live_kit.ui.components.status_bar.load_operation",
            side_effect=UploadQueueError("operationが壊れています。"),
        ),
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    args = record_error.call_args.args
    assert args[0] == "video-upload"
    assert "投稿状態を読み込めませんでした" in args[3]
    assert "operationが壊れています" in args[4]


def test_pipeline_loader_exception_records_bounded_detail() -> None:
    from yt_live_kit.ui.components import status_bar

    job = JobState(
        job_id="pipeline-loader-error",
        kind="single",
        status="done",
        video_id="video-pipeline",
        result_ref="video-pipeline",
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch("yt_live_kit.ui.components.status_bar.mark_job_handled"),
        patch(
            "yt_live_kit.ui.components.status_bar.load_result_from_disk",
            side_effect=ValueError("meta <broken>\ntrace"),
        ),
        patch("yt_live_kit.ui.components.status_bar.record_job_error") as record_error,
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    record_error.assert_called_once()
    summary, detail = record_error.call_args.args[3:5]
    assert "成果物を読み込めませんでした" in summary
    assert "<broken>" in detail
    assert "<" not in summary and ">" not in summary


def test_interrupted_job_reads_log_and_records_before_handled() -> None:
    from yt_live_kit.ui.components import status_bar

    events: list[str] = []
    finished_at = datetime(2026, 8, 3, 1, 2, tzinfo=timezone.utc)
    job = JobState(
        job_id="interrupted-job",
        kind="shorts_queue",
        status="interrupted",
        video_id="video-interrupted",
        error="前回の処理が中断されました。",
        finished_at=finished_at,
    )
    with (
        patch("yt_live_kit.ui.components.status_bar.is_job_handled", return_value=False),
        patch(
            "yt_live_kit.ui.components.status_bar.read_job_error_log",
            return_value="traceback <raw>\nline",
        ),
        patch(
            "yt_live_kit.ui.components.status_bar.record_job_error",
            side_effect=lambda *args: events.append("record"),
        ) as record_error,
        patch(
            "yt_live_kit.ui.components.status_bar.mark_job_handled",
            side_effect=lambda *_args: events.append("handled"),
        ),
        patch("yt_live_kit.ui.components.status_bar.clear_active_job_id"),
        patch("yt_live_kit.ui.components.status_bar.st.rerun"),
    ):
        status_bar._handle_finished_job(job)

    assert events == ["record", "handled"]
    args = record_error.call_args.args
    assert args[:3] == ("video-interrupted", "interrupted-job", "shorts_queue")
    assert args[5] == finished_at
    assert "traceback <raw>" in args[4]


def test_interrupted_short_cut_refine_uses_high_precision_notice() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_record_interrupted_jobs"
    )
    job = JobState(
        job_id="interrupted-refine",
        kind="short_cut",
        status="interrupted",
        stage="resolver",
        video_id="video-refine",
        error=(
            "選択区間の高精度字幕に失敗しました。"
            "S9_WHISPER_ERROR:{\"schema\":\"s9-whisper-error-v1\"}"
        ),
        finished_at=datetime(2026, 8, 3, 1, 2, tzinfo=timezone.utc),
    )
    record_error = MagicMock()
    namespace = {
        "read_job": lambda _job_id, _settings: job,
        "read_job_error_log": lambda _job_id, _settings: None,
        "record_job_error": record_error,
        "_top_summary": str,
        "datetime": datetime,
        "timezone": timezone,
        "job_display_label": job_display_label,
        "retry_hint_for_job": retry_hint_for_job,
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(app_path), "exec"),
        namespace,
    )

    namespace["_record_interrupted_jobs"]([job.job_id], object())

    summary = record_error.call_args.args[3]
    assert "選択区間の高精度字幕に失敗しました" in summary
    assert "ショート区間提案に失敗しました" not in summary
    assert "高精度化を再試行" in summary


def test_running_fragment_does_not_clear_structured_job_history() -> None:
    from yt_live_kit.ui.components import status_bar

    source = Path(status_bar.__file__).read_text(encoding="utf-8")
    assert "clear_job_error" not in source


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
