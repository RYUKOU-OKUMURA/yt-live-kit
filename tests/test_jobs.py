"""jobs サービスのユニットテスト."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.jobs import (
    JobBusyError,
    JobState,
    close_orphans,
    create_job,
    get_active_job,
    is_busy,
    read_job,
    start_job,
    update_job,
)


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

    job_id = start_job("single", target_fn, settings=settings, total=2)
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
        raise RuntimeError("処理に失敗しました")

    job_id = start_job("single", target_fn, settings=settings)
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

    job_id = start_job("single", target_fn, settings=settings)
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
