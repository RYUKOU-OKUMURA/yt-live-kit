"""H1-4 のプロセス間永続化境界テスト."""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path
from queue import Empty

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services._fsutil import advisory_lock
from yt_live_kit.services import youtube_api
from yt_live_kit.ui.views._local_settings import (
    load_description_applied_ids,
    load_archived_ids,
    save_archived_ids,
    save_description_applied_ids,
    save_default_channel_handle,
)


def _save_id_set_worker(
    data_dir: str,
    video_id: str,
    barrier: multiprocessing.synchronize.Barrier,
    results: multiprocessing.queues.Queue,
    done: multiprocessing.synchronize.Event | None = None,
    attempted: multiprocessing.synchronize.Event | None = None,
) -> None:
    try:
        settings = Settings(data_dir=Path(data_dir))
        ids = load_description_applied_ids(settings)
        ids.add(video_id)
        barrier.wait(timeout=10)
        if attempted is not None:
            attempted.set()
        save_description_applied_ids(ids, settings)
    except BaseException as exc:  # pragma: no cover - asserted by parent
        results.put(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if done is not None:
            done.set()
    results.put("ok")


def _save_token_worker(
    token_path: str,
    value: str,
    barrier: multiprocessing.synchronize.Barrier,
    results: multiprocessing.queues.Queue,
    done: multiprocessing.synchronize.Event | None = None,
    attempted: multiprocessing.synchronize.Event | None = None,
) -> None:
    try:
        barrier.wait(timeout=10)
        if attempted is not None:
            attempted.set()
        youtube_api._save_token(Path(token_path), json.dumps({"value": value}))
    except BaseException as exc:  # pragma: no cover - asserted by parent
        results.put(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if done is not None:
            done.set()
    results.put("ok")


def _hold_lock_worker(
    lock_path: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    try:
        with advisory_lock(Path(lock_path)):
            ready.set()
            release.wait(timeout=10)
    except BaseException as exc:  # pragma: no cover - asserted by parent
        results.put(f"{type(exc).__name__}: {exc}")
        raise
    results.put("released")


def _join_processes(processes: list[multiprocessing.Process]) -> None:
    for process in processes:
        process.join(timeout=15)
        assert not process.is_alive()
        assert process.exitcode == 0


def _read_results(results: multiprocessing.queues.Queue) -> list[str]:
    values: list[str] = []
    for _ in range(2):
        try:
            values.append(results.get(timeout=5))
        except Empty:  # pragma: no cover - child exit assertion is the main signal
            break
    return values


def test_two_process_id_set_updates_merge_inside_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_save_id_set_worker,
            args=(str(tmp_path), video_id, barrier, results),
        )
        for video_id in ("video-a", "video-b")
    ]
    for process in processes:
        process.start()

    _join_processes(processes)
    assert _read_results(results) == ["ok", "ok"]
    assert load_description_applied_ids(Settings(data_dir=tmp_path)) == {
        "video-a",
        "video-b",
    }


def test_two_process_token_writes_leave_valid_complete_json(tmp_path: Path) -> None:
    token_path = tmp_path / "_config" / "youtube_token.json"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_save_token_worker,
            args=(str(token_path), value, barrier, results),
        )
        for value in ("first", "second")
    ]
    for process in processes:
        process.start()

    _join_processes(processes)
    assert _read_results(results) == ["ok", "ok"]
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert payload in ({"value": "first"}, {"value": "second"})


@pytest.mark.parametrize(
    ("kind", "lock_path", "writer_target", "writer_args"),
    [
        (
            "local settings",
            "_config/.description_applied_videos.json.lock",
            _save_id_set_worker,
            ("video",),
        ),
        (
            "OAuth token",
            "_config/.youtube_token.json.lock",
            _save_token_worker,
            ("token",),
        ),
    ],
)
def test_two_process_writer_waits_for_advisory_lock(
    tmp_path: Path,
    kind: str,
    lock_path: str,
    writer_target,
    writer_args: tuple[str],
) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    done = context.Event()
    attempted = context.Event()
    results = context.Queue()
    holder = context.Process(
        target=_hold_lock_worker,
        args=(str(tmp_path / lock_path), ready, release, results),
    )
    holder.start()
    assert ready.wait(timeout=10), f"{kind} lock holder did not start"
    writer_barrier = context.Barrier(1)

    if writer_args == ("video",):
        writer = context.Process(
            target=writer_target,
            args=(
                str(tmp_path),
                "blocked-video",
                writer_barrier,
                results,
                done,
                attempted,
            ),
        )
    else:
        writer = context.Process(
            target=writer_target,
            args=(
                str(tmp_path / "_config" / "youtube_token.json"),
                "blocked-token",
                writer_barrier,
                results,
                done,
                attempted,
            ),
        )
    writer.start()
    assert attempted.wait(timeout=10), f"{kind} writer did not reach save"
    time.sleep(0.1)
    assert not done.is_set(), f"{kind} writer bypassed advisory lock"

    release.set()
    _join_processes([holder, writer])
    assert done.is_set()


def test_id_set_merge_preserves_concurrent_addition_and_explicit_removal(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    save_archived_ids({"keep", "remove"}, settings)
    desired = load_archived_ids(settings)
    desired.remove("remove")

    path = tmp_path / "_config" / "archived_videos.json"
    path.write_text('["concurrent", "keep", "remove"]\n', encoding="utf-8")
    save_archived_ids(desired, settings)

    assert load_archived_ids(settings) == {"concurrent", "keep"}


@pytest.mark.parametrize(
    ("writer", "path_name", "old_value", "new_value"),
    [
        ("token", "youtube_token.json", '{"old": true}', '{"new": true}'),
        (
            "ids",
            "description_applied_videos.json",
            '["old-video"]\n',
            '["new-video"]\n',
        ),
    ],
)
def test_fault_injection_preserves_previous_json(
    tmp_path: Path,
    writer: str,
    path_name: str,
    old_value: str,
    new_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "_config"
    config_dir.mkdir()
    path = config_dir / path_name
    path.write_text(old_value, encoding="utf-8")

    if writer == "token":
        def save() -> None:
            youtube_api._save_token(path, new_value)
    else:
        settings = Settings(data_dir=tmp_path)

        def save() -> None:
            save_description_applied_ids({"new-video"}, settings)

    def fail_replace(*_args: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("yt_live_kit.services._fsutil.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        save()

    assert path.read_text(encoding="utf-8") == old_value
    assert list(config_dir.glob(".*.tmp")) == []


def test_fault_injection_preserves_previous_channel_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path)
    path = tmp_path / "_config" / "channel_handle.txt"
    path.parent.mkdir(parents=True)
    path.write_text("@old-channel\n", encoding="utf-8")

    def fail_replace(*_args: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("yt_live_kit.services._fsutil.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        save_default_channel_handle("@new-channel", settings)

    assert path.read_text(encoding="utf-8") == "@old-channel\n"
    assert list(path.parent.glob(".*.tmp")) == []


def test_failed_id_set_save_keeps_snapshot_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path)
    save_description_applied_ids({"base"}, settings)
    desired = load_description_applied_ids(settings)
    desired.add("new")
    path = tmp_path / "_config" / "description_applied_videos.json"

    real_replace = os.replace
    replace_calls = 0

    def fail_once(source: object, destination: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            raise OSError("injected replace failure")
        real_replace(source, destination)

    monkeypatch.setattr("yt_live_kit.services._fsutil.os.replace", fail_once)
    with pytest.raises(OSError, match="injected replace failure"):
        save_description_applied_ids(desired, settings)

    path.write_text('["base", "concurrent"]\n', encoding="utf-8")
    save_description_applied_ids(desired, settings)
    assert load_description_applied_ids(settings) == {
        "base",
        "concurrent",
        "new",
    }
