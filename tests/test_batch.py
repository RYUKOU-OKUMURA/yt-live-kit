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


@patch("yt_live_kit.services.batch.is_video_processed")
def test_run_batch_skip_existing(mock_is_processed, tmp_path):
    settings = Settings(data_dir=tmp_path)
    mock_is_processed.return_value = True

    results = run_batch(
        ["https://www.youtube.com/watch?v=existing123"],
        settings,
        skip_existing=True,
        sleep_sec=0,
    )

    assert len(results) == 1
    assert results[0].status == "skipped"


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

    loaded = load_batch_status(settings)
    assert len(loaded) == 1
    assert loaded[0].video_id == "test1234567"


def test_run_batch_invalid_url(tmp_path):
    settings = Settings(data_dir=tmp_path)
    results = run_batch(["not-a-valid-url"], settings, sleep_sec=0)
    assert len(results) == 1
    assert results[0].status == "failed"


@patch("yt_live_kit.services.batch.get_active_job")
@patch("yt_live_kit.services.batch.run_batch")
def test_run_batch_job_target_raises_when_all_failed(mock_run_batch, mock_get_active, tmp_path):
    settings = Settings(data_dir=tmp_path)
    mock_get_active.return_value = MagicMock(job_id="job-all-fail")
    mock_run_batch.return_value = [
        MagicMock(status="failed", url="https://a", error="失敗A"),
        MagicMock(status="failed", url="https://b", error="失敗B"),
    ]

    with pytest.raises(PipelineError, match="成功した動画がありません"):
        run_batch_job_target(report=MagicMock(), settings=settings, urls=["https://a", "https://b"])

    summary_path = batch_summary_path(settings, "job-all-fail")
    assert summary_path.is_file()
    summary = read_batch_summary(settings, "job-all-fail")
    assert summary is not None
    assert summary["failed"] == 2
    assert summary["success"] == 0


@patch("yt_live_kit.services.batch.get_active_job")
@patch("yt_live_kit.services.batch.run_batch")
def test_run_batch_job_target_raises_when_all_skipped(mock_run_batch, mock_get_active, tmp_path):
    settings = Settings(data_dir=tmp_path)
    mock_get_active.return_value = MagicMock(job_id="job-all-skip")
    mock_run_batch.return_value = [
        MagicMock(status="skipped", url="https://a", error=None),
    ]

    with pytest.raises(PipelineError, match="すべてスキップされました"):
        run_batch_job_target(report=MagicMock(), settings=settings, urls=["https://a"])

    summary = read_batch_summary(settings, "job-all-skip")
    assert summary is not None
    assert summary["skipped"] == 1


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
