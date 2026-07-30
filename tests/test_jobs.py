"""jobs サービスのユニットテスト."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.clips import ClipsError
from yt_live_kit.services.jobs import (
    JobBusyError,
    JobState,
    _error_message_for,
    close_orphans,
    create_job,
    get_active_job,
    is_busy,
    read_current_job,
    read_job,
    start_job,
    update_job,
)
from yt_live_kit.services.pipeline import PipelineError


def test_create_update_read_roundtrip(tmp_path):
    settings = Settings(data_dir=tmp_path)

    created = create_job(
        "single",
        video_id="video1234567",
        title="テスト動画",
        total=3,
        settings=settings,
    )
    assert created.status == "running"
    assert created.kind == "single"
    assert created.video_id == "video1234567"
    assert created.total == 3

    updated = update_job(
        created.job_id,
        settings=settings,
        stage="fetch",
        message="字幕を取得しています",
        current=1,
    )
    assert updated.stage == "fetch"
    assert updated.message == "字幕を取得しています"
    assert updated.current == 1

    loaded = read_job(created.job_id, settings)
    assert loaded is not None
    assert loaded.job_id == created.job_id
    assert loaded.stage == "fetch"
    assert loaded.message == "字幕を取得しています"


def test_job_state_to_dict_from_dict():
    started = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 7, 30, 12, 5, tzinfo=timezone.utc)
    state = JobState(
        job_id="abc123",
        kind="batch",
        status="done",
        video_id=None,
        title="一括",
        stage="complete",
        message="完了",
        current=2,
        total=2,
        started_at=started,
        finished_at=finished,
        error=None,
        result_ref="video1234567",
    )

    restored = JobState.from_dict(state.to_dict())
    assert restored.job_id == "abc123"
    assert restored.status == "done"
    assert restored.started_at == started
    assert restored.finished_at == finished
    assert restored.result_ref == "video1234567"


def test_read_job_returns_none_for_broken_json(tmp_path):
    settings = Settings(data_dir=tmp_path)
    jobs_dir = tmp_path / "_jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "broken.json").write_text("{ invalid json", encoding="utf-8")

    assert read_job("broken", settings) is None


def test_read_job_returns_none_for_missing_file(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert read_job("missing", settings) is None


def test_start_job_updates_state_via_report(tmp_path):
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()

    def target_fn(*, report, settings, **_kwargs):
        report(stage="fetch", message="取得中", current=1, total=2)
        done.set()

    with patch("yt_live_kit.services.jobs.threading.Thread", wraps=threading.Thread) as mock_thread:
        job_id = start_job("single", target_fn, settings=settings, total=2)
        mock_thread.return_value.join(timeout=5)
    assert done.wait(timeout=5)
    for _ in range(50):
        state = read_job(job_id, settings)
        if state is not None and state.status == "done":
            break
        time.sleep(0.05)
    else:
        pytest.fail("ジョブが done になりませんでした")

    assert state is not None
    assert state.status == "done"
    assert state.stage == "fetch"
    assert state.message == "完了しました"
    assert state.current == 1
    assert state.total == 2
    assert state.finished_at is not None


def test_start_job_marks_failed_on_exception(tmp_path):
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()

    def target_fn(*, report, settings, **_kwargs):
        # PipelineError は _KNOWN_ERRORS に含まれるため、メッセージがそのまま使われる。
        raise PipelineError("処理に失敗しました")

    with patch("yt_live_kit.services.jobs.threading.Thread", wraps=threading.Thread) as mock_thread:
        job_id = start_job("single", target_fn, settings=settings)
        mock_thread.return_value.join(timeout=5)
    for _ in range(50):
        state = read_job(job_id, settings)
        if state is not None and state.status == "failed":
            done.set()
            break
        time.sleep(0.05)
    assert done.wait(timeout=5)

    state = read_job(job_id, settings)
    assert state is not None
    assert state.status == "failed"
    assert state.error == "処理に失敗しました"
    assert state.finished_at is not None


def test_start_job_writes_log_for_unexpected_exception(tmp_path):
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()

    def target_fn(*, report, settings, **_kwargs):
        raise ValueError()

    with patch("yt_live_kit.services.jobs.threading.Thread", wraps=threading.Thread) as mock_thread:
        job_id = start_job("single", target_fn, settings=settings)
        mock_thread.return_value.join(timeout=5)
    for _ in range(50):
        state = read_job(job_id, settings)
        if state is not None and state.status == "failed":
            done.set()
            break
        time.sleep(0.05)
    assert done.wait(timeout=5)

    state = read_job(job_id, settings)
    assert state is not None
    assert state.error == "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
    log_path = tmp_path / "_jobs" / f"{job_id}.log"
    assert log_path.is_file()


def test_is_busy_and_job_busy_error(tmp_path):
    settings = Settings(data_dir=tmp_path)
    started = threading.Event()

    def target_fn(*, report, settings, **_kwargs):
        started.set()
        time.sleep(0.2)

    assert not is_busy(settings)
    with patch("yt_live_kit.services.jobs.threading.Thread", wraps=threading.Thread) as mock_thread:
        job_id = start_job("single", target_fn, settings=settings)
        assert started.wait(timeout=5)
        assert is_busy(settings)
        assert get_active_job(settings) is not None
        assert get_active_job(settings).job_id == job_id

        with pytest.raises(JobBusyError, match="別の処理が実行中です"):
            start_job("single", target_fn, settings=settings)

        for _ in range(50):
            if not is_busy(settings):
                break
            time.sleep(0.05)
        mock_thread.return_value.join(timeout=5)
    assert not is_busy(settings)


def test_close_orphans(tmp_path):
    settings = Settings(data_dir=tmp_path)
    running = create_job("single", settings=settings)
    done = create_job("batch", settings=settings)
    update_job(
        done.job_id,
        settings=settings,
        status="done",
        finished_at=datetime.now(timezone.utc),
    )

    interrupted = close_orphans(settings)
    assert interrupted == [running.job_id]

    loaded_running = read_job(running.job_id, settings)
    assert loaded_running is not None
    assert loaded_running.status == "interrupted"
    assert loaded_running.finished_at is not None
    assert loaded_running.error == "前回の処理が中断されました"

    loaded_done = read_job(done.job_id, settings)
    assert loaded_done is not None
    assert loaded_done.status == "done"


def test_cleanup_finished_removes_old_jobs(tmp_path):
    from yt_live_kit.services.jobs import cleanup_finished

    settings = Settings(data_dir=tmp_path)
    old_job = create_job("single", settings=settings)
    old_finished = datetime.now(timezone.utc) - timedelta(hours=48)
    update_job(
        old_job.job_id,
        settings=settings,
        status="done",
        finished_at=old_finished,
    )

    recent_job = create_job("single", settings=settings)
    update_job(
        recent_job.job_id,
        settings=settings,
        status="failed",
        finished_at=datetime.now(timezone.utc),
    )

    removed = cleanup_finished(older_than_hours=24, settings=settings)
    assert removed == 1
    assert read_job(old_job.job_id, settings) is None
    assert read_job(recent_job.job_id, settings) is not None


def test_error_message_for_known_error_passes_message_through():
    exc = PipelineError("字幕が取得できませんでした。")
    message, needs_log = _error_message_for(exc)
    assert message == "字幕が取得できませんでした。"
    assert needs_log is False


def test_error_message_for_known_subclass_passes_message_through():
    # ClipsError は AiPromptError のサブクラス。_KNOWN_ERRORS 経由で拾われる。
    exc = ClipsError("クリップ候補の生成に失敗しました。")
    message, needs_log = _error_message_for(exc)
    assert message == "クリップ候補の生成に失敗しました。"
    assert needs_log is False


def test_error_message_for_unknown_error_uses_generic_message():
    message, needs_log = _error_message_for(ValueError("boom"))
    assert message == "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
    assert needs_log is True


def test_error_message_for_unknown_file_not_found_uses_generic_message():
    message, needs_log = _error_message_for(
        FileNotFoundError(2, "No such file or directory", "/tmp/ja.vtt")
    )
    assert message == "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
    assert needs_log is True


def test_get_active_job_uses_current_json_and_never_calls_list_jobs(tmp_path):
    settings = Settings(data_dir=tmp_path)
    running = create_job("single", settings=settings)

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("list_jobs should not be called by get_active_job")

    with patch("yt_live_kit.services.jobs.list_jobs", side_effect=_fail_if_called):
        active = get_active_job(settings)

    assert active is not None
    assert active.job_id == running.job_id


def test_get_active_job_returns_none_when_current_not_running(tmp_path):
    settings = Settings(data_dir=tmp_path)
    job = create_job("single", settings=settings)
    update_job(job.job_id, settings=settings, status="done")

    with patch("yt_live_kit.services.jobs.list_jobs") as mock_list_jobs:
        active = get_active_job(settings)

    assert active is None
    mock_list_jobs.assert_not_called()


def test_read_current_job_missing_file_returns_none(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert read_current_job(settings) is None


def test_read_current_job_broken_json_returns_none(tmp_path):
    settings = Settings(data_dir=tmp_path)
    jobs_dir = tmp_path / "_jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "current.json").write_text("{ not valid json", encoding="utf-8")

    assert read_current_job(settings) is None


def test_read_current_job_missing_target_job_returns_none(tmp_path):
    settings = Settings(data_dir=tmp_path)
    jobs_dir = tmp_path / "_jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "current.json").write_text(
        json.dumps({"job_id": "does-not-exist"}), encoding="utf-8"
    )

    assert read_current_job(settings) is None


def test_read_current_job_returns_pointed_job(tmp_path):
    settings = Settings(data_dir=tmp_path)
    job = create_job("single", settings=settings)

    current = read_current_job(settings)
    assert current is not None
    assert current.job_id == job.job_id


def test_cleanup_finished_keeps_current_job(tmp_path):
    from yt_live_kit.services.jobs import cleanup_finished

    settings = Settings(data_dir=tmp_path)
    # create_job は current.json を上書きするため、最後に作った done ジョブが
    # current.json に指されるようにする。
    old_job = create_job("single", settings=settings)
    old_finished = datetime.now(timezone.utc) - timedelta(hours=48)
    update_job(
        old_job.job_id,
        settings=settings,
        status="done",
        finished_at=old_finished,
    )
    # old_job が current.json の指す最新ジョブになっている。
    removed = cleanup_finished(older_than_hours=24, settings=settings)

    assert removed == 0
    assert read_job(old_job.job_id, settings) is not None


