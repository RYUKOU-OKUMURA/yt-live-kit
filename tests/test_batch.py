"""batch サービスのユニットテスト."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.batch import (
    BatchStatusEntry,
    append_batch_status,
    batch_summary_path,
    load_batch_status,
    parse_urls,
    read_batch_summary,
    run_batch,
    run_batch_job_target,
)
from yt_live_kit.services.pipeline import PipelineError, PipelineResult


def test_parse_urls():
    text = """
    https://www.youtube.com/watch?v=abc12345678
    # コメント行
    https://youtu.be/def98765432

    """
    urls = parse_urls(text)
    assert len(urls) == 2
    assert "abc12345678" in urls[0]


@pytest.mark.parametrize(
    ("do_chapters", "do_clips"),
    [(True, True), (True, False), (False, True)],
)
@patch("yt_live_kit.services.batch.run")
def test_run_batch_passes_all_valid_target_combinations_to_pipeline(
    mock_run, tmp_path, do_chapters, do_clips
):
    mock_run.return_value = MagicMock(video_id="abc12345678", title="title")
    settings = Settings(data_dir=tmp_path)

    results = run_batch(
        ["https://www.youtube.com/watch?v=abc12345678"],
        settings,
        do_chapters=do_chapters,
        do_clips=do_clips,
        sleep_sec=0,
    )

    assert results[0].status == "success"
    assert mock_run.call_args.kwargs["do_chapters"] is do_chapters
    assert mock_run.call_args.kwargs["do_clips"] is do_clips


@patch("yt_live_kit.services.batch.run")
def test_run_batch_defaults_preserve_both_targets(mock_run, tmp_path):
    mock_run.return_value = MagicMock(video_id="abc12345678", title="title")
    run_batch(
        ["https://www.youtube.com/watch?v=abc12345678"],
        Settings(data_dir=tmp_path),
        sleep_sec=0,
    )
    assert mock_run.call_args.kwargs["do_chapters"] is True
    assert mock_run.call_args.kwargs["do_clips"] is True


def test_run_batch_rejects_no_targets(tmp_path):
    with pytest.raises(ValueError, match="どちらか"):
        run_batch(
            ["https://www.youtube.com/watch?v=abc12345678"],
            Settings(data_dir=tmp_path),
            do_chapters=False,
            do_clips=False,
        )


@pytest.mark.parametrize(
    ("do_chapters", "do_clips"),
    [(True, True), (True, False), (False, True)],
)
@patch("yt_live_kit.services.batch.run_batch", return_value=[])
def test_run_batch_job_target_passes_all_valid_combinations_to_run_batch(
    mock_run_batch, tmp_path, do_chapters, do_clips
):
    with patch("yt_live_kit.services.batch.update_job"):
        run_batch_job_target(
            report=MagicMock(),
            settings=Settings(data_dir=tmp_path),
            urls=["https://example.com/video"],
            do_chapters=do_chapters,
            do_clips=do_clips,
            job_id="job-flags",
        )
    assert mock_run_batch.call_args.kwargs["do_chapters"] is do_chapters
    assert mock_run_batch.call_args.kwargs["do_clips"] is do_clips


def test_run_batch_job_target_rejects_no_targets(tmp_path):
    with pytest.raises(ValueError, match="どちらか"):
        run_batch_job_target(
            report=MagicMock(),
            settings=Settings(data_dir=tmp_path),
            urls=["https://example.com/video"],
            do_chapters=False,
            do_clips=False,
        )


@patch("yt_live_kit.services.batch.run")
def test_run_batch_continues_on_failure(mock_run, tmp_path):
    settings = Settings(data_dir=tmp_path)
    meta = VideoMeta(
        id="success1234",
        title="成功",
        url="https://www.youtube.com/watch?v=success1234",
        duration=100,
        ytdlp_version="2026.7.4",
        fetched_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        subtitle_lang="ja",
    )

    def side_effect(url, *_args, **_kwargs):
        if "fail" in url:
            raise PipelineError("テスト失敗")
        return PipelineResult(
            video_id=meta.id,
            title=meta.title,
            meta=meta,
            chapters_text="0:00 x\n5:00 y\n10:00 z\n",
            chapters_path=tmp_path / "chapters.md",
            full_transcript_path=tmp_path / "full.txt",
            full_transcript_text="full",
            clips_candidates=(),
            clips_candidates_path=None,
        )

    mock_run.side_effect = side_effect

    urls = [
        "https://www.youtube.com/watch?v=success1234",
        "https://www.youtube.com/watch?v=fail1234567",
    ]
    results = run_batch(urls, settings, sleep_sec=0)

    assert len(results) == 2
    assert results[0].status == "success"
    assert results[1].status == "failed"
    assert results[1].error is not None


@patch("yt_live_kit.services.batch.is_video_targets_complete")
def test_run_batch_skip_existing(mock_is_complete, tmp_path):
    settings = Settings(data_dir=tmp_path)
    mock_is_complete.return_value = True

    results = run_batch(
        ["https://www.youtube.com/watch?v=existing123"],
        settings,
        skip_existing=True,
        sleep_sec=0,
    )

    assert len(results) == 1
    assert results[0].status == "skipped"
    mock_is_complete.assert_called_once_with(
        "existing123",
        settings,
        do_chapters=True,
        do_clips=True,
    )


def _write_chapters_only(settings: Settings, video_id: str) -> None:
    video_dir = settings.data_dir / video_id
    video_dir.mkdir(parents=True)
    (video_dir / "chapters").mkdir()
    (video_dir / "chapters" / "chapters.md").write_text("0:00 x\n", encoding="utf-8")


def _write_chapters_and_clips(settings: Settings, video_id: str) -> None:
    _write_chapters_only(settings, video_id)
    (settings.data_dir / video_id / "clips").mkdir()
    (settings.data_dir / video_id / "clips" / "candidates.json").write_text(
        "[]", encoding="utf-8"
    )


@patch("yt_live_kit.services.batch.run")
def test_run_batch_skip_existing_clips_only_with_chapters_present(mock_run, tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "existing123"
    _write_chapters_only(settings, video_id)
    mock_run.return_value = MagicMock(video_id=video_id, title="title")

    results = run_batch(
        [f"https://www.youtube.com/watch?v={video_id}"],
        settings,
        skip_existing=True,
        do_chapters=False,
        do_clips=True,
        sleep_sec=0,
    )

    assert results[0].status == "success"
    mock_run.assert_called_once()


@patch("yt_live_kit.services.batch.run")
def test_run_batch_skip_existing_both_complete(mock_run, tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "existing123"
    _write_chapters_and_clips(settings, video_id)

    results = run_batch(
        [f"https://www.youtube.com/watch?v={video_id}"],
        settings,
        skip_existing=True,
        sleep_sec=0,
    )

    assert results[0].status == "skipped"
    mock_run.assert_not_called()


@patch("yt_live_kit.services.batch.run")
def test_run_batch_skip_existing_chapters_only(mock_run, tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "existing123"
    _write_chapters_only(settings, video_id)

    results = run_batch(
        [f"https://www.youtube.com/watch?v={video_id}"],
        settings,
        skip_existing=True,
        do_chapters=True,
        do_clips=False,
        sleep_sec=0,
    )

    assert results[0].status == "skipped"
    mock_run.assert_not_called()


def test_batch_status_persistence(tmp_path):
    settings = Settings(data_dir=tmp_path)

    entries = [
        BatchStatusEntry(
            url="https://example.com",
            video_id="test1234567",
            status="success",
            error=None,
            timestamp="2026-07-30T00:00:00+00:00",
        )
    ]
    path = append_batch_status(entries, settings)
    assert path.is_file()
    assert list(path.parent.glob(".*.tmp")) == []

    loaded = load_batch_status(settings)
    assert len(loaded) == 1
    assert loaded[0].video_id == "test1234567"


def test_run_batch_invalid_url(tmp_path):
    settings = Settings(data_dir=tmp_path)
    results = run_batch(["not-a-valid-url"], settings, sleep_sec=0)
    assert len(results) == 1
    assert results[0].status == "failed"


@patch("yt_live_kit.services.batch.update_job")
@patch("yt_live_kit.services.batch.get_active_job")
@patch("yt_live_kit.services.batch.run_batch")
def test_run_batch_job_target_all_failed_does_not_raise(
    mock_run_batch, mock_get_active, mock_update_job, tmp_path
):
    """成功 0 件でも例外にせず正常終了し、サマリーに失敗件数が記録されること."""
    settings = Settings(data_dir=tmp_path)
    mock_get_active.return_value = MagicMock(job_id="job-all-fail")
    mock_run_batch.return_value = [
        MagicMock(status="failed", url="https://a", error="失敗A"),
        MagicMock(status="failed", url="https://b", error="失敗B"),
    ]

    run_batch_job_target(report=MagicMock(), settings=settings, urls=["https://a", "https://b"])

    summary_path = batch_summary_path(settings, "job-all-fail")
    assert summary_path.is_file()
    summary = read_batch_summary(settings, "job-all-fail")
    assert summary is not None
    assert summary["failed"] == 2
    assert summary["success"] == 0

    # 成功結果が無いので video_id / title は更新しない。message のみ更新する。
    mock_update_job.assert_called_once()
    kwargs = mock_update_job.call_args.kwargs
    assert "video_id" not in kwargs
    assert "title" not in kwargs
    assert "失敗 2" in kwargs["message"]


@patch("yt_live_kit.services.batch.update_job")
@patch("yt_live_kit.services.batch.get_active_job")
@patch("yt_live_kit.services.batch.run_batch")
def test_run_batch_job_target_all_skipped_does_not_raise(
    mock_run_batch, mock_get_active, mock_update_job, tmp_path
):
    """処理済みスキップにより全件スキップされても、正常終了（ジョブ done）になること."""
    settings = Settings(data_dir=tmp_path)
    mock_get_active.return_value = MagicMock(job_id="job-all-skip")
    mock_run_batch.return_value = [
        MagicMock(status="skipped", url="https://a", error=None),
    ]

    run_batch_job_target(report=MagicMock(), settings=settings, urls=["https://a"])

    summary = read_batch_summary(settings, "job-all-skip")
    assert summary is not None
    assert summary["skipped"] == 1
    assert summary["success"] == 0
    assert summary["failed"] == 0


@patch("yt_live_kit.services.batch.update_job")
@patch("yt_live_kit.services.batch.get_active_job")
@patch("yt_live_kit.services.batch.run_batch")
def test_run_batch_job_target_partial_skip_and_failure_does_not_raise(
    mock_run_batch, mock_get_active, mock_update_job, tmp_path
):
    """0 成功 / 3 スキップ / 1 失敗 でも例外にならず、スキップ 3 件がサマリーに残ること."""
    settings = Settings(data_dir=tmp_path)
    mock_get_active.return_value = MagicMock(job_id="job-mixed")
    mock_run_batch.return_value = [
        MagicMock(status="skipped", url="https://a", error=None),
        MagicMock(status="skipped", url="https://b", error=None),
        MagicMock(status="skipped", url="https://c", error=None),
        MagicMock(status="failed", url="https://d", error="失敗D"),
    ]

    run_batch_job_target(
        report=MagicMock(),
        settings=settings,
        urls=["https://a", "https://b", "https://c", "https://d"],
    )

    summary = read_batch_summary(settings, "job-mixed")
    assert summary is not None
    assert summary["success"] == 0
    assert summary["skipped"] == 3
    assert summary["failed"] == 1


@patch("yt_live_kit.services.batch.update_job")
@patch("yt_live_kit.services.batch.get_active_job")
@patch("yt_live_kit.services.batch.run_batch")
def test_run_batch_job_target_updates_job_on_partial_success(
    mock_run_batch,
    mock_get_active,
    mock_update_job,
    tmp_path,
):
    settings = Settings(data_dir=tmp_path)
    mock_get_active.return_value = MagicMock(job_id="job-partial")
    meta = VideoMeta(
        id="success1234",
        title="成功動画",
        url="https://www.youtube.com/watch?v=success1234",
        duration=100,
        ytdlp_version="2026.7.4",
        fetched_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        subtitle_lang="ja",
    )
    success_result = PipelineResult(
        video_id=meta.id,
        title=meta.title,
        meta=meta,
        chapters_text="0:00 x\n5:00 y\n10:00 z\n",
        chapters_path=tmp_path / "chapters.md",
        full_transcript_path=tmp_path / "full.txt",
        full_transcript_text="full",
        clips_candidates=(),
        clips_candidates_path=None,
    )
    mock_run_batch.return_value = [
        MagicMock(status="success", url="https://ok", result=success_result, error=None),
        MagicMock(status="failed", url="https://ng", result=None, error="失敗"),
    ]

    run_batch_job_target(report=MagicMock(), settings=settings, urls=["https://ok", "https://ng"])

    mock_update_job.assert_called_once()
    kwargs = mock_update_job.call_args.kwargs
    assert kwargs["video_id"] == "success1234"
    assert kwargs["title"] == "成功動画"
    assert "成功 1" in kwargs["message"]

    summary = read_batch_summary(settings, "job-partial")
    assert summary is not None
    assert summary["success"] == 1
    assert summary["failed"] == 1
    assert any("✅" in line for line in summary["lines"])
    assert any("❌" in line for line in summary["lines"])
