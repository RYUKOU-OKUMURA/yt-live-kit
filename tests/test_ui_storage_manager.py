"""設定画面のストレージ管理テスト."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from yt_live_kit.config import Settings
from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.services.storage import (
    StorageError,
    StorageSummary,
    VideoStorage,
    purge_source,
)
from yt_live_kit.ui.components import storage_manager


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def _video(
    video_id: str,
    *,
    title: str | None = None,
    source: int = 100,
    intermediate: int = 20,
) -> VideoStorage:
    return VideoStorage(
        video_id=video_id,
        title=title or f"動画 {video_id}",
        source_bytes=source,
        output_bytes=30,
        intermediate_bytes=intermediate,
        other_bytes=5,
        total_bytes=source + intermediate + 35,
    )


def _processed(video_id: str, fetched_at: datetime | None) -> ProcessedVideo:
    return ProcessedVideo(
        video_id=video_id,
        title=f"処理済み {video_id}",
        fetched_at=fetched_at,
        has_chapters=True,
        has_transcript=True,
        has_clips=True,
    )


def test_format_video_storage_row_includes_source_intermediate_and_outputs() -> None:
    row = storage_manager.format_video_storage_row(_video("vid", source=1024))

    assert "元動画 1.0 KB" in row
    assert "中間ファイル" in row
    assert "成果物" in row


def test_all_47_videos_are_reachable_and_eleventh_item_is_rendered() -> None:
    videos = [_video(f"vid-{index:02d}") for index in range(47)]
    summary = StorageSummary(total_bytes=9999, videos=videos)

    with (
        patch.object(storage_manager.st, "text_input", return_value=""),
        patch.object(storage_manager.st, "container", side_effect=lambda **_kw: nullcontext()),
        patch.object(storage_manager.st, "button", return_value=False) as button,
        patch.object(storage_manager.st, "markdown") as markdown,
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
    ):
        storage_manager._render_video_rows(summary, MagicMock(), busy=False)

    assert call("**動画 vid-10**") in markdown.call_args_list
    row_keys = {
        item.kwargs.get("key")
        for item in button.call_args_list
        if str(item.kwargs.get("key", "")).startswith("storage_manager_delete_")
    }
    assert len(row_keys) == 47


def test_search_reaches_video_by_title_or_id() -> None:
    videos = [_video("alpha", title="朝の配信"), _video("beta", title="夜の配信")]

    assert storage_manager.filter_storage_videos(videos, "朝") == [videos[0]]
    assert storage_manager.filter_storage_videos(videos, "BETA") == [videos[1]]


def test_bulk_snapshot_contains_only_old_deletable_ids_and_capacity() -> None:
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    processed = [
        _processed("old", now - timedelta(days=40)),
        _processed("new", now - timedelta(days=2)),
        _processed("empty", now - timedelta(days=50)),
        _processed("unknown-date", None),
    ]
    summary = StorageSummary(
        total_bytes=1000,
        videos=[
            _video("old", source=400, intermediate=50),
            _video("new", source=300),
            _video("empty", source=0, intermediate=0),
        ],
    )

    snapshot = storage_manager.build_bulk_purge_snapshot(
        processed, summary, 30, now=now
    )

    assert [target.video_id for target in snapshot.targets] == ["old"]
    assert snapshot.count == 1
    assert snapshot.total_bytes == 450


def test_single_dialog_displays_identity_count_capacity_and_retained_artifacts() -> None:
    target = storage_manager.PurgeTarget("vid-1", "大切な動画", 2048)

    with (
        patch.object(storage_manager.st, "button", return_value=False),
        patch.object(storage_manager.st, "markdown") as markdown,
        patch.object(storage_manager.st, "caption") as caption,
        patch.object(storage_manager.st, "write") as write,
        patch.object(storage_manager.st, "info") as info,
        patch.object(storage_manager, "purge_source") as purge,
        patch.object(storage_manager, "is_busy", return_value=False),
    ):
        storage_manager._confirm_single_purge_dialog.__wrapped__(target, MagicMock())

    markdown.assert_called_once_with("**動画:** 大切な動画")
    caption.assert_called_once_with("動画 ID: vid-1")
    assert call("対象: 1 件") in write.call_args_list
    assert call("削除対象容量: 2.0 KB") in write.call_args_list
    assert "チャプター" in info.call_args.args[0]
    assert "全文" in info.call_args.args[0]
    assert "切り抜き候補" in info.call_args.args[0]
    assert "切り出し済み動画" in info.call_args.args[0]
    purge.assert_not_called()


def test_single_dialog_deletes_only_after_confirmation() -> None:
    target = storage_manager.PurgeTarget("vid-1", "動画", 1024)
    session_state: dict[str, object] = {}
    settings = MagicMock()

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun") as rerun,
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(storage_manager, "purge_source", return_value=1024) as purge,
        patch.object(storage_manager, "summarize", return_value=StorageSummary(0, [])),
    ):
        storage_manager._confirm_single_purge_dialog.__wrapped__(target, settings)

    purge.assert_called_once_with("vid-1", settings)
    rerun.assert_called_once_with()


def test_single_dialog_shows_storage_error_in_japanese() -> None:
    target = storage_manager.PurgeTarget("vid-1", "動画", 1024)

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "error") as error,
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(
            storage_manager, "purge_source", side_effect=StorageError("権限がありません")
        ),
    ):
        storage_manager._confirm_single_purge_dialog.__wrapped__(target, MagicMock())

    assert "削除に失敗しました" in error.call_args.args[0]
    assert "権限がありません" in error.call_args.args[0]


def test_single_dialog_handles_purge_os_error_in_japanese() -> None:
    target = storage_manager.PurgeTarget("vid-1", "動画", 1024)

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "error") as error,
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(storage_manager, "purge_source", side_effect=OSError("I/O error")),
    ):
        storage_manager._confirm_single_purge_dialog.__wrapped__(target, MagicMock())

    assert "削除に失敗しました" in error.call_args.args[0]
    assert "I/O error" in error.call_args.args[0]


def test_single_success_then_summary_failure_discards_stale_summary() -> None:
    target = storage_manager.PurgeTarget("vid-1", "動画", 1024)
    session_state: dict[str, object] = {
        storage_manager._SUMMARY_KEY: StorageSummary(999, [_video("stale")]),
    }

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun") as rerun,
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(storage_manager, "purge_source", return_value=1024),
        patch.object(storage_manager, "summarize", side_effect=OSError("scan failed")),
    ):
        storage_manager._confirm_single_purge_dialog.__wrapped__(target, MagicMock())

    assert storage_manager._SUMMARY_KEY not in session_state
    messages = session_state[storage_manager._FLASH_KEY]
    assert any("1.0 KB 削除しました" in message for _level, message in messages)
    assert any(
        "削除は完了したが容量再集計に失敗" in message
        for _level, message in messages
    )
    rerun.assert_called_once_with()


def test_single_dialog_is_disabled_and_does_not_purge_while_busy() -> None:
    target = storage_manager.PurgeTarget("vid-1", "動画", 1024)

    with (
        patch.object(storage_manager.st, "button", return_value=True) as button,
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager, "is_busy", return_value=True),
        patch.object(storage_manager, "purge_source") as purge,
    ):
        storage_manager._confirm_single_purge_dialog.__wrapped__(target, MagicMock())

    assert button.call_args.kwargs["disabled"] is True
    purge.assert_not_called()


def test_bulk_dialog_does_not_delete_before_confirmation() -> None:
    snapshot = storage_manager.BulkPurgeSnapshot(
        days=30,
        targets=(storage_manager.PurgeTarget("old-1", "古い動画", 1024),),
    )

    with (
        patch.object(storage_manager.st, "button", return_value=False),
        patch.object(storage_manager.st, "warning") as warning,
        patch.object(storage_manager.st, "info") as info,
        patch.object(storage_manager, "purge_source") as purge,
        patch.object(storage_manager, "is_busy", return_value=False),
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, MagicMock())

    assert "対象: 1 件" in warning.call_args.args[0]
    assert "1.0 KB" in warning.call_args.args[0]
    assert "チャプター" in info.call_args.args[0]
    purge.assert_not_called()


def test_bulk_confirm_uses_exact_snapshot_ids_without_rescan() -> None:
    snapshot = storage_manager.BulkPurgeSnapshot(
        days=30,
        targets=(
            storage_manager.PurgeTarget("old-1", "古い 1", 100),
            storage_manager.PurgeTarget("old-2", "古い 2", 200),
        ),
    )
    session_state: dict[str, object] = {
        storage_manager._SNAPSHOT_KEY: snapshot,
    }
    settings = MagicMock()

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "warning"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun"),
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(storage_manager, "purge_source", side_effect=[100, 200]) as purge,
        patch.object(storage_manager, "summarize", return_value=StorageSummary(0, [])),
        patch.object(storage_manager, "list_processed_videos") as rescan,
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, settings)

    assert purge.call_args_list == [call("old-1", settings), call("old-2", settings)]
    rescan.assert_not_called()
    assert storage_manager._SNAPSHOT_KEY not in session_state
    assert isinstance(
        session_state[storage_manager._SUMMARY_KEY], StorageSummary
    )


def test_bulk_partial_failure_keeps_only_failed_snapshot_and_refreshes_summary() -> None:
    targets = (
        storage_manager.PurgeTarget("ok", "成功", 100),
        storage_manager.PurgeTarget("storage-error", "失敗 1", 200),
        storage_manager.PurgeTarget("os-error", "失敗 2", 300),
    )
    snapshot = storage_manager.BulkPurgeSnapshot(30, targets)
    refreshed = StorageSummary(123, [_video("storage-error")])
    session_state: dict[str, object] = {
        storage_manager._SNAPSHOT_KEY: snapshot,
        storage_manager._SUMMARY_KEY: StorageSummary(999, [_video("stale")]),
    }
    settings = MagicMock()

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "warning"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun") as rerun,
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(
            storage_manager,
            "purge_source",
            side_effect=[100, StorageError("権限なし"), OSError("I/O error")],
        ) as purge,
        patch.object(storage_manager, "summarize", return_value=refreshed),
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, settings)

    assert purge.call_args_list == [
        call("ok", settings),
        call("storage-error", settings),
        call("os-error", settings),
    ]
    remaining = session_state[storage_manager._SNAPSHOT_KEY]
    assert isinstance(remaining, storage_manager.BulkPurgeSnapshot)
    assert [target.video_id for target in remaining.targets] == [
        "storage-error",
        "os-error",
    ]
    assert session_state[storage_manager._SUMMARY_KEY] is refreshed
    messages = session_state[storage_manager._FLASH_KEY]
    assert any("1 件から 100 B 削除" in message for _level, message in messages)
    assert any(
        "storage-error" in message and "os-error" in message
        for _level, message in messages
    )
    rerun.assert_called_once_with()


def test_bulk_success_then_summary_failure_discards_stale_summary() -> None:
    snapshot = storage_manager.BulkPurgeSnapshot(
        30,
        (storage_manager.PurgeTarget("ok", "成功", 100),),
    )
    session_state: dict[str, object] = {
        storage_manager._SNAPSHOT_KEY: snapshot,
        storage_manager._SUMMARY_KEY: StorageSummary(999, [_video("stale")]),
    }

    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "warning"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun"),
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(storage_manager, "purge_source", return_value=100),
        patch.object(
            storage_manager,
            "summarize",
            side_effect=StorageError("scan failed"),
        ),
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, MagicMock())

    assert storage_manager._SNAPSHOT_KEY not in session_state
    assert storage_manager._SUMMARY_KEY not in session_state
    messages = session_state[storage_manager._FLASH_KEY]
    assert any("1 件から 100 B 削除" in message for _level, message in messages)
    assert any(
        "削除処理後の容量再集計に失敗" in message
        and "削除結果は確定済み" in message
        for _level, message in messages
    )


def test_bulk_dialog_shows_storage_error_in_japanese() -> None:
    snapshot = storage_manager.BulkPurgeSnapshot(
        days=30,
        targets=(storage_manager.PurgeTarget("old-1", "古い", 100),),
    )

    session_state: dict[str, object] = {}
    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "warning"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "error") as error,
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun"),
        patch.object(storage_manager, "is_busy", return_value=False),
        patch.object(
            storage_manager, "purge_source", side_effect=StorageError("削除不能")
        ),
        patch.object(storage_manager, "summarize", return_value=StorageSummary(0, [])),
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, MagicMock())
        storage_manager._show_flash()

    assert "1 件の削除に失敗しました" in error.call_args.args[0]
    assert "削除不能" in error.call_args.args[0]
    remaining = session_state[storage_manager._SNAPSHOT_KEY]
    assert isinstance(remaining, storage_manager.BulkPurgeSnapshot)
    assert [target.video_id for target in remaining.targets] == ["old-1"]
    assert isinstance(
        session_state[storage_manager._SUMMARY_KEY], StorageSummary
    )


def test_bulk_dialog_is_disabled_and_does_not_purge_while_busy() -> None:
    snapshot = storage_manager.BulkPurgeSnapshot(
        30,
        (storage_manager.PurgeTarget("old-1", "古い", 100),),
    )

    with (
        patch.object(storage_manager.st, "button", return_value=True) as button,
        patch.object(storage_manager.st, "warning"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager, "is_busy", return_value=True),
        patch.object(storage_manager, "purge_source") as purge,
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, MagicMock())

    assert button.call_args.kwargs["disabled"] is True
    purge.assert_not_called()


def test_busy_list_and_preview_are_disabled_without_opening_or_scanning() -> None:
    summary = StorageSummary(100, [_video("vid-1")])
    session_state: dict[str, object] = {
        storage_manager._SNAPSHOT_KEY: storage_manager.BulkPurgeSnapshot(
            30,
            (storage_manager.PurgeTarget("old-1", "古い", 100),),
        )
    }

    with (
        patch.object(storage_manager.st, "text_input", return_value=""),
        patch.object(
            storage_manager.st,
            "container",
            side_effect=lambda **_kw: nullcontext(),
        ),
        patch.object(storage_manager.st, "button", return_value=True) as button,
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "number_input", return_value=30),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager, "_confirm_single_purge_dialog") as single_dialog,
        patch.object(storage_manager, "_confirm_bulk_purge_dialog") as bulk_dialog,
        patch.object(storage_manager, "list_processed_videos") as scan,
        patch.object(storage_manager, "purge_source") as purge,
    ):
        storage_manager._render_video_rows(summary, MagicMock(), busy=True)
        storage_manager._render_bulk_purge(summary, MagicMock(), busy=True)

    disabled = {
        item.kwargs.get("key"): item.kwargs.get("disabled")
        for item in button.call_args_list
    }
    assert disabled["storage_manager_delete_vid-1"] is True
    assert disabled["storage_manager_preview"] is True
    assert disabled["storage_manager_open_bulk_confirm"] is True
    single_dialog.assert_not_called()
    bulk_dialog.assert_not_called()
    scan.assert_not_called()
    purge.assert_not_called()


def test_row_delete_button_only_opens_dialog_without_purge() -> None:
    summary = StorageSummary(100, [_video("vid-1")])

    def click_delete(_label: str, **kwargs: object) -> bool:
        return kwargs.get("key") == "storage_manager_delete_vid-1"

    with (
        patch.object(storage_manager.st, "text_input", return_value=""),
        patch.object(
            storage_manager.st,
            "container",
            side_effect=lambda **_kw: nullcontext(),
        ),
        patch.object(storage_manager.st, "button", side_effect=click_delete),
        patch.object(storage_manager.st, "markdown"),
        patch.object(storage_manager.st, "caption"),
        patch.object(storage_manager.st, "write"),
        patch.object(storage_manager, "_confirm_single_purge_dialog") as dialog,
        patch.object(storage_manager, "purge_source") as purge,
    ):
        storage_manager._render_video_rows(summary, MagicMock(), busy=False)

    dialog.assert_called_once()
    assert dialog.call_args.args[0].video_id == "vid-1"
    purge.assert_not_called()


def test_actual_purge_retains_all_artifacts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    video_dir = settings.data_dir / "vid-retain"
    deletable = [
        video_dir / "clips/source/source.mp4",
        video_dir / "highlights/segments/segment.mp4",
        video_dir / "shorts/segments/segment.mp4",
    ]
    retained = [
        video_dir / "chapters/chapters.md",
        video_dir / "transcript/full.txt",
        video_dir / "clips/candidates.json",
        video_dir / "clips/output/clip.mp4",
    ]
    for path in deletable + retained:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")

    deleted = purge_source("vid-retain", settings)

    assert deleted == 12
    assert all(not path.exists() for path in deletable)
    assert all(path.is_file() for path in retained)


def test_bulk_dialog_retains_artifacts_for_every_snapshot_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    targets: list[storage_manager.PurgeTarget] = []
    retained: list[Path] = []
    for video_id in ("bulk-1", "bulk-2"):
        video_dir = settings.data_dir / video_id
        source = video_dir / "clips/source/source.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"source")
        for relative in (
            "chapters/chapters.md",
            "transcript/full.txt",
            "clips/candidates.json",
            "clips/output/clip.mp4",
        ):
            artifact = video_dir / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"artifact")
            retained.append(artifact)
        targets.append(storage_manager.PurgeTarget(video_id, video_id, 6))

    snapshot = storage_manager.BulkPurgeSnapshot(30, tuple(targets))
    session_state: dict[str, object] = {
        storage_manager._SNAPSHOT_KEY: snapshot,
    }
    with (
        patch.object(storage_manager.st, "button", return_value=True),
        patch.object(storage_manager.st, "warning"),
        patch.object(storage_manager.st, "info"),
        patch.object(storage_manager.st, "session_state", session_state),
        patch.object(storage_manager.st, "rerun"),
        patch.object(storage_manager, "is_busy", return_value=False),
    ):
        storage_manager._confirm_bulk_purge_dialog.__wrapped__(snapshot, settings)

    assert all(path.is_file() for path in retained)
    assert all(
        not (settings.data_dir / target.video_id / "clips/source").exists()
        for target in targets
    )
