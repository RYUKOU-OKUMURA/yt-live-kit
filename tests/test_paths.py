"""H1-2 path confinement の境界テスト。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import UploadChannel, UploadContentSnapshot
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_identifier_path,
    confined_path,
)
from yt_live_kit.services.history import HistoryError, is_video_processed
from yt_live_kit.services.jobs import create_job, update_job
from yt_live_kit.services.shorts_line import (
    LineStateError,
    line_state_path,
    run_line_reservation_transaction,
)
from yt_live_kit.services.shorts_queue import ShortsQueueError, run_shorts_queue
from yt_live_kit.services.upload_queue import (
    UploadQueueError,
    create_reserved_operation,
    list_operations,
)
from yt_live_kit.services.ytdlp import YtdlpError, fetch


VIDEO_ID = "dQw4w9WgXcQ"
NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "identifier",
    ["", ".", "..", "../escape", "nested/id", r"nested\\id", "/absolute", "with space", "nul\x00id"],
)
def test_identifier_rejection_is_side_effect_free(tmp_path: Path, identifier: str) -> None:
    data_dir = tmp_path / "data"
    with pytest.raises(PathConfinementError):
        confined_identifier_path(data_dir, identifier, label="動画 ID")
    assert not data_dir.exists()


def test_normal_youtube_id_remains_compatible(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = confined_identifier_path(data_dir, VIDEO_ID, "subtitles", label="動画保存先")
    assert path == data_dir / VIDEO_ID / "subtitles"
    assert path.resolve().is_relative_to(data_dir.resolve())
    assert not data_dir.exists()


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("ytdlp", YtdlpError),
        ("history", HistoryError),
        ("jobs", ValueError),
        ("shorts_queue", ShortsQueueError),
        ("shorts_line", LineStateError),
        ("upload_queue", UploadQueueError),
    ],
)
@pytest.mark.parametrize("identifier", ["", ".", "..", "../escape", "nested/id", "nul\x00id"])
def test_public_service_rejects_invalid_identifier_before_filesystem_side_effect(
    tmp_path: Path, service: str, expected: type[Exception], identifier: str
) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir)
    with pytest.raises(expected) as raised:
        if service == "ytdlp":
            fetch(identifier, settings)
        elif service == "history":
            is_video_processed(identifier, settings)
        elif service == "jobs":
            create_job("single", video_id=identifier, settings=settings)
        elif service == "shorts_queue":
            run_shorts_queue(identifier, (), settings, job_id="job")
        elif service == "shorts_line":
            line_state_path(identifier, "clip", settings)
        else:
            create_reserved_operation(
                operation_id="op",
                job_id="job",
                source_video_id=identifier,
                source_kind="shorts_queue",
                clip_id="clip",
                content=_content(data_dir / "short.mp4"),
                now=NOW,
                settings=settings,
            )
    if identifier:
        assert identifier not in str(raised.value)
    assert not data_dir.exists()


def test_jobs_update_rejects_invalid_video_id_before_lock_side_effect(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with pytest.raises(ValueError, match="パス"):
        update_job(
            "job",
            settings=Settings(data_dir=data_dir),
            video_id="../escape",
        )
    assert not data_dir.exists()


def _symlink(directory: Path, link: Path, target: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform capability
        pytest.skip(f"symlink を作成できない環境: {exc}")


def test_ytdlp_rejects_symlink_escape_before_download_side_effect(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / VIDEO_ID, outside)
    settings = Settings(data_dir=data_dir)
    with (
        patch("yt_live_kit.services.ytdlp.shutil.which", return_value="/usr/bin/yt-dlp"),
        patch("yt_live_kit.services.ytdlp.get_ytdlp_version", return_value="2026.07.04"),
        patch("yt_live_kit.services.ytdlp._fetch_metadata", return_value={"id": VIDEO_ID}),
        pytest.raises(YtdlpError, match="安全に扱えません"),
    ):
        fetch(f"https://www.youtube.com/watch?v={VIDEO_ID}", settings)
    assert not (outside / "subtitles").exists()


def test_ytdlp_rejects_existing_subtitle_symlink_before_download(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    subtitles = data_dir / VIDEO_ID / "subtitles"
    outside = tmp_path / "outside"
    subtitles.mkdir(parents=True)
    outside.mkdir()
    try:
        (subtitles / f"{VIDEO_ID}.ja.vtt").symlink_to(
            outside / "stolen.vtt", target_is_directory=False
        )
    except OSError as exc:  # pragma: no cover - platform capability
        pytest.skip(f"symlink を作成できない環境: {exc}")
    settings = Settings(data_dir=data_dir)
    with (
        patch("yt_live_kit.services.ytdlp.shutil.which", return_value="/usr/bin/yt-dlp"),
        patch("yt_live_kit.services.ytdlp.get_ytdlp_version", return_value="2026.07.04"),
        patch("yt_live_kit.services.ytdlp._fetch_metadata", return_value={"id": VIDEO_ID}),
        patch("yt_live_kit.services.ytdlp._run_ytdlp") as run,
        pytest.raises(YtdlpError, match="安全に扱えません"),
    ):
        fetch(f"https://www.youtube.com/watch?v={VIDEO_ID}", settings)
    run.assert_not_called()
    assert not (outside / "stolen.vtt").exists()


def test_history_rejects_symlink_escape_without_reading_outside(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / VIDEO_ID, outside)
    with pytest.raises(HistoryError, match="安全に扱えません"):
        is_video_processed(VIDEO_ID, Settings(data_dir=data_dir))
    assert not (outside / "chapters").exists()


def test_jobs_rejects_external_jobs_directory_before_lock_side_effect(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / "_jobs", outside)
    with pytest.raises(ValueError, match="安全に扱えません"):
        create_job("single", video_id=VIDEO_ID, settings=Settings(data_dir=data_dir))
    assert not (outside / ".jobs.lock").exists()


def test_shorts_queue_rejects_symlink_escape_before_manifest_side_effect(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / VIDEO_ID, outside)
    with pytest.raises(ShortsQueueError, match="安全に扱えません"):
        run_shorts_queue(VIDEO_ID, (), Settings(data_dir=data_dir), job_id="job")
    assert not (outside / "shorts").exists()


def test_shorts_line_rejects_symlink_escape_before_line_directory_creation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / VIDEO_ID, outside)
    with pytest.raises(LineStateError, match="安全に扱えません"):
        line_state_path(VIDEO_ID, "clip", Settings(data_dir=data_dir))
    assert not (outside / "shorts").exists()


def test_shorts_line_rejects_external_output_before_callback(tmp_path: Path) -> None:
    output = tmp_path / "outside.mp4"
    output.write_bytes(b"not-used")
    called = False

    def start_upload():
        nonlocal called
        called = True
        raise AssertionError("投稿 callback は呼ばれてはいけません")

    with pytest.raises(LineStateError, match="安全に扱えません"):
        run_line_reservation_transaction(
            VIDEO_ID,
            "clip",
            output,
            Settings(data_dir=tmp_path / "data"),
            start_upload,
        )
    assert called is False


def _content(path: Path) -> UploadContentSnapshot:
    return UploadContentSnapshot(
        channel=UploadChannel(channel_id="UC1", title="テストチャンネル"),
        video_path=path,
        file_size=1,
        file_mtime_ns=0,
        duration_sec=30,
        title="タイトル",
        description="説明",
        tags=("タグ",),
        publish_at=NOW,
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=False,
        contains_synthetic_media=False,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=NOW,
    )


def test_upload_queue_rejects_symlink_escape_before_schedule_side_effect(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / VIDEO_ID, outside)
    video_path = data_dir / VIDEO_ID / "short.mp4"
    with pytest.raises(UploadQueueError, match="安全に扱えません"):
        create_reserved_operation(
            operation_id="op",
            job_id="job",
            source_video_id=VIDEO_ID,
            source_kind="shorts_queue",
            clip_id="clip",
            content=_content(video_path),
            now=NOW,
            settings=Settings(data_dir=data_dir),
        )
    assert not (outside / "_schedule").exists()
    assert not (data_dir / "_schedule").exists()


def test_upload_queue_rejects_external_snapshot_path_before_schedule_side_effect(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir)
    video_path = data_dir / "video" / "short.mp4"
    operation = create_reserved_operation(
        operation_id="op",
        job_id="job",
        source_video_id=VIDEO_ID,
        source_kind="shorts_queue",
        clip_id="clip",
        content=_content(video_path),
        now=NOW,
        settings=settings,
    )
    outside = tmp_path / "outside.mp4"
    tampered = operation.model_copy(
        update={
            "video_path": outside,
            "content": operation.content.model_copy(update={"video_path": outside}),
        }
    )
    queue_path = data_dir / "_schedule" / "queue.json"
    queue_path.write_text(
        json.dumps(
            {"schema_version": 1, "operations": [tampered.model_dump(mode="json")]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(UploadQueueError, match="安全に扱えません"):
        list_operations(settings)
    assert not outside.exists()


def test_confined_path_rejects_external_symlink_without_creating_parent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    _symlink(data_dir, data_dir / "child", outside)
    with pytest.raises(PathConfinementError, match="安全に扱えません"):
        confined_path(data_dir, "child", "new.json", label="保存先")
    assert not (outside / "new.json").exists()
