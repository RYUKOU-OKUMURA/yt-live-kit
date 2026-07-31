"""jobs サービスのユニットテスト."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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
from yt_live_kit.services.shorts import ShortsError


@contextmanager
def _patch_real_thread():
    threads: list[threading.Thread] = []
    real = threading.Thread

    def factory(*args, **kwargs):
        t = real(*args, **kwargs)
        threads.append(t)
        return t

    with patch("yt_live_kit.services.jobs.threading.Thread", side_effect=factory) as mock_thread:
        yield mock_thread, threads


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


def test_requested_job_id_is_written_before_thread_and_not_overwritten(tmp_path):
    settings = Settings(data_dir=tmp_path)
    observed = threading.Event()

    def target_fn(*, report, settings, job_id, **_kwargs):
        state = read_job(job_id, settings)
        assert state is not None
        assert state.job_id == "requested-job"
        update_job(job_id, settings=settings, result_ref="operation-1")
        observed.set()

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job(
            "upload", target_fn, settings=settings,
            requested_job_id="requested-job",
        )
        threads[-1].join(timeout=5)
    assert observed.is_set()
    assert job_id == "requested-job"
    state = read_job(job_id, settings)
    assert state is not None
    assert state.status == "done"
    assert state.result_ref == "operation-1"

    with pytest.raises(ValueError, match="既に存在"):
        start_job(
            "upload", target_fn, settings=settings,
            requested_job_id="requested-job",
        )


def test_requested_job_json_failure_does_not_start_thread(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with (
        patch("yt_live_kit.services.jobs._write_job", side_effect=OSError("fault")),
        patch("yt_live_kit.services.jobs.threading.Thread") as thread,
    ):
        with pytest.raises(OSError, match="fault"):
            start_job(
                "upload", lambda **_kwargs: None, settings=settings,
                requested_job_id="job-before-thread",
            )
    thread.assert_not_called()


def test_thread_start_failure_leaves_requested_job_json_for_recovery(tmp_path):
    settings = Settings(data_dir=tmp_path)
    thread = MagicMock()
    thread.start.side_effect = RuntimeError("thread fault")
    with patch("yt_live_kit.services.jobs.threading.Thread", return_value=thread):
        with pytest.raises(RuntimeError, match="thread fault"):
            start_job(
                "upload", lambda **_kwargs: None, settings=settings,
                requested_job_id="job-saved-before-thread",
            )
    state = read_job("job-saved-before-thread", settings)
    assert state is not None
    assert state.status == "running"


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

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("single", target_fn, settings=settings, total=2)
        threads[-1].join(timeout=5)
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

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("single", target_fn, settings=settings)
        threads[-1].join(timeout=5)
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

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("single", target_fn, settings=settings)
        threads[-1].join(timeout=5)
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
    with _patch_real_thread() as (_mock_thread, threads):
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
        threads[-1].join(timeout=5)
    assert not is_busy(settings)


def test_start_job_concurrent_calls_only_start_one_job(tmp_path):
    """_START_LOCK により、同時に複数回 start_job() を呼んでも 1 件しか作られない."""
    settings = Settings(data_dir=tmp_path)

    def target_fn(*, report, settings, **_kwargs):
        time.sleep(0.2)

    barrier = threading.Barrier(5)
    results: list[tuple[str, str | None]] = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait(timeout=5)
        try:
            job_id = start_job("single", target_fn, settings=settings)
            outcome: tuple[str, str | None] = ("ok", job_id)
        except JobBusyError:
            outcome = ("busy", None)
        with results_lock:
            results.append(outcome)

    with _patch_real_thread() as (_mock_thread, threads):
        workers = [threading.Thread(target=worker) for _ in range(5)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=5)

        for _ in range(50):
            if not is_busy(settings):
                break
            time.sleep(0.05)
        threads[-1].join(timeout=5)

    assert len(results) == 5
    ok_results = [r for r in results if r[0] == "ok"]
    busy_results = [r for r in results if r[0] == "busy"]
    assert len(ok_results) == 1
    assert len(busy_results) == 4
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


def test_close_orphans_runs_upload_recovery_after_job_is_interrupted(tmp_path):
    settings = Settings(data_dir=tmp_path)
    job = create_job(
        "upload", requested_job_id="orphan-upload", settings=settings
    )

    def assert_closed(_settings):
        current = read_job(job.job_id, settings)
        assert current is not None
        assert current.status == "interrupted"
        return ()

    with patch(
        "yt_live_kit.services.upload_queue.recover_upload_operations",
        side_effect=assert_closed,
    ) as recover:
        close_orphans(settings)
    recover.assert_called_once_with(settings)


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


def test_start_job_passes_job_id_to_target_fn(tmp_path):
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()
    received: dict[str, object] = {}

    def target_fn(*, report, settings, job_id=None, **_kwargs):
        received["job_id"] = job_id
        done.set()

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("single", target_fn, settings=settings)
        threads[-1].join(timeout=5)
    assert done.wait(timeout=5)
    assert received["job_id"] == job_id


def test_start_job_passes_video_id_when_target_declares_it(tmp_path):
    """video_id を必須キーワードで宣言する target には video_id が渡ること（修正1 の再現テスト）."""
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()
    received: dict[str, object] = {}

    def target_fn(*, report, settings, video_id, job_id=None, **_kwargs):
        received["video_id"] = video_id
        done.set()

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("highlights", target_fn, video_id="vid1", settings=settings)
        threads[-1].join(timeout=5)
    assert done.wait(timeout=5)

    for _ in range(50):
        state = read_job(job_id, settings)
        if state is not None and state.status in ("done", "failed"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("ジョブが完了しませんでした")

    assert state is not None
    assert state.status == "done"
    assert received["video_id"] == "vid1"


def test_start_job_does_not_pass_video_id_when_target_lacks_it(tmp_path):
    """video_id を宣言しない target（run_single_job_target 相当）は TypeError にならず done になる（退行防止）."""
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()

    def target_fn(*, report, settings, job_id=None, url=None):
        done.set()

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("single", target_fn, settings=settings, url="https://example.com")
        threads[-1].join(timeout=5)
    assert done.wait(timeout=5)

    for _ in range(50):
        state = read_job(job_id, settings)
        if state is not None and state.status in ("done", "failed"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("ジョブが完了しませんでした")

    assert state is not None
    assert state.status == "done"
    assert state.error is None


def test_start_job_reports_shorts_error_message(tmp_path):
    """ShortsError は _KNOWN_ERRORS に含まれ、日本語メッセージがそのまま error に入る（修正2）."""
    settings = Settings(data_dir=tmp_path)
    done = threading.Event()

    def target_fn(*, report, settings, **_kwargs):
        raise ShortsError("動画ディレクトリが見つかりません: /tmp/video1234567")

    with _patch_real_thread() as (_mock_thread, threads):
        job_id = start_job("shorts", target_fn, settings=settings)
        threads[-1].join(timeout=5)

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
    assert state.error == "動画ディレクトリが見つかりません: /tmp/video1234567"
