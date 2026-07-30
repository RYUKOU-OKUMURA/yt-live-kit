"""パイプラインのユニットテスト."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ai_prompt import ChapterGenerationResult
from yt_live_kit.services.clips import ClipSuggestResult
from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_CLIPS_SUGGEST,
    STAGE_FETCH,
    STAGE_TRANSCRIPT,
    PipelineError,
    run,
)
from yt_live_kit.services.ytdlp import SubtitleNotFoundError


def _sample_meta() -> VideoMeta:
    return VideoMeta(
        id="test1234567",
        title="テスト動画",
        url="https://www.youtube.com/watch?v=test1234567",
        upload_date="20260101",
        duration=3600,
        ytdlp_version="2026.7.4",
        fetched_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        subtitle_lang="ja",
    )


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_success(mock_fetch, mock_transcript, mock_chapters, mock_clips, tmp_path):
    meta = _sample_meta()
    mock_fetch.return_value = meta

    full_path = tmp_path / "full.txt"
    full_path.write_text("[00:00:00] こんにちは\n", encoding="utf-8")
    compressed_path = tmp_path / "compressed.txt"
    compressed_path.write_text("compressed\n", encoding="utf-8")
    mock_transcript.return_value = (full_path, compressed_path)

    chapters_path = tmp_path / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    mock_chapters.return_value = ChapterGenerationResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )

    candidates_path = tmp_path / "candidates.json"
    mock_clips.return_value = ClipSuggestResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt_clips.txt",
        candidates_path=candidates_path,
        used_codex=True,
        candidates=(
            ClipCandidate(
                id="clip_001",
                title="テスト候補",
                start="03:42",
                end="16:30",
                duration_sec=768,
                reason="テスト理由",
            ),
        ),
    )

    progress: list[tuple[str, str]] = []

    def on_progress(stage: str, message: str) -> None:
        progress.append((stage, message))

    result = run("https://www.youtube.com/watch?v=test1234567", on_progress=on_progress)

    assert result.video_id == meta.id
    assert result.title == "テスト動画"
    assert "0:00 開始" in result.chapters_text
    assert result.full_transcript_text.startswith("[00:00:00]")
    assert len(result.clips_candidates) == 1
    assert [p[0] for p in progress] == [
        STAGE_FETCH,
        STAGE_TRANSCRIPT,
        STAGE_CHAPTERS,
        STAGE_CLIPS_SUGGEST,
    ]


@patch("yt_live_kit.services.pipeline.fetch")
def test_run_wraps_subtitle_error(mock_fetch):
    mock_fetch.side_effect = SubtitleNotFoundError()

    with pytest.raises(PipelineError, match="字幕が取得できませんでした"):
        run("https://www.youtube.com/watch?v=test1234567")
