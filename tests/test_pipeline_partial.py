"""パイプライン部分実行・再生成のユニットテスト."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ai_prompt import ChapterGenerationResult
from yt_live_kit.services.clips import ClipSuggestResult
from yt_live_kit.services.pipeline import (
    STAGE_FETCH,
    STAGE_TRANSCRIPT,
    PipelineError,
    regenerate,
    run,
)


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


def _setup_video_dir(tmp_path, meta: VideoMeta | None = None) -> Settings:
    meta = meta or _sample_meta()
    video_dir = tmp_path / meta.id
    (video_dir / "transcript").mkdir(parents=True)
    (video_dir / "chapters").mkdir(parents=True)
    (video_dir / "clips").mkdir(parents=True)
    (video_dir / "meta.json").write_text(meta.model_dump_json(), encoding="utf-8")
    return Settings(data_dir=tmp_path)


def _mock_transcript_paths(tmp_path, video_id: str):
    video_dir = tmp_path / video_id
    transcript_dir = video_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    full_path = transcript_dir / "full.txt"
    compressed_path = transcript_dir / "compressed.txt"
    full_path.write_text("[00:00:00] こんにちは\n", encoding="utf-8")
    compressed_path.write_text("compressed\n", encoding="utf-8")
    return full_path, compressed_path


def _mock_chapter_result(tmp_path, video_id: str) -> ChapterGenerationResult:
    chapters_dir = tmp_path / video_id / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    chapters_path = chapters_dir / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    return ChapterGenerationResult(
        video_id=video_id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )


def _mock_clips_result(tmp_path, video_id: str) -> ClipSuggestResult:
    candidates_path = tmp_path / video_id / "clips" / "candidates.json"
    return ClipSuggestResult(
        video_id=video_id,
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


@pytest.mark.parametrize(
    ("do_chapters", "do_clips", "expect_chapters", "expect_clips"),
    [
        (True, True, True, True),
        (True, False, True, False),
        (False, True, False, True),
        (False, False, False, False),
    ],
)
@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_flag_combinations(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
    do_chapters,
    do_clips,
    expect_chapters,
    expect_clips,
):
    meta = _sample_meta()
    mock_fetch.return_value = meta

    def _build_transcripts_side_effect(vid: str, settings: Settings):
        return _mock_transcript_paths(tmp_path, vid)

    mock_transcript.side_effect = _build_transcripts_side_effect
    mock_chapters.return_value = _mock_chapter_result(tmp_path, meta.id)
    mock_clips.return_value = _mock_clips_result(tmp_path, meta.id)

    run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=Settings(data_dir=tmp_path),
        do_chapters=do_chapters,
        do_clips=do_clips,
    )

    mock_fetch.assert_called_once()
    mock_transcript.assert_called_once()
    if expect_chapters:
        mock_chapters.assert_called_once()
    else:
        mock_chapters.assert_not_called()
    if expect_clips:
        mock_clips.assert_called_once()
    else:
        mock_clips.assert_not_called()


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_skips_fetch_and_transcript_when_artifacts_exist(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)

    mock_chapters.return_value = _mock_chapter_result(tmp_path, meta.id)
    mock_clips.return_value = _mock_clips_result(tmp_path, meta.id)

    progress: list[tuple[str, str]] = []

    def on_progress(stage: str, message: str) -> None:
        progress.append((stage, message))

    run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=settings,
        on_progress=on_progress,
    )

    mock_fetch.assert_not_called()
    mock_transcript.assert_not_called()
    mock_chapters.assert_called_once()
    mock_clips.assert_called_once()

    fetch_msg = next(msg for stage, msg in progress if stage == STAGE_FETCH)
    transcript_msg = next(msg for stage, msg in progress if stage == STAGE_TRANSCRIPT)
    assert "取得済みのためスキップ" in fetch_msg
    assert "取得済みのためスキップ" in transcript_msg


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_do_chapters_false_reads_existing_chapters(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)
    chapters_path = tmp_path / meta.id / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 保存済み\n5:00 本編\n", encoding="utf-8")

    mock_fetch.return_value = meta
    mock_transcript.return_value = _mock_transcript_paths(tmp_path, meta.id)

    result = run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=settings,
        do_chapters=False,
        do_clips=False,
    )

    mock_chapters.assert_not_called()
    assert "0:00 保存済み" in result.chapters_text


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_do_chapters_false_without_file_returns_empty(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    mock_fetch.return_value = meta

    def _build_transcripts_side_effect(vid: str, settings: Settings):
        return _mock_transcript_paths(tmp_path, vid)

    mock_transcript.side_effect = _build_transcripts_side_effect

    result = run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=Settings(data_dir=tmp_path),
        do_chapters=False,
        do_clips=False,
    )

    assert result.chapters_text == ""
    assert result.clips_candidates == ()


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_regenerate_chapters_only(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)

    chapters_path = tmp_path / meta.id / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 旧チャプター\n", encoding="utf-8")

    def _regenerate_chapters(vid: str, settings: Settings) -> ChapterGenerationResult:
        chapters_path.write_text("0:00 新チャプター\n5:00 本編\n", encoding="utf-8")
        return ChapterGenerationResult(
            video_id=vid,
            prompt_path=tmp_path / "prompt.txt",
            chapters_path=chapters_path,
            used_codex=True,
            validation=None,
        )

    mock_chapters.side_effect = _regenerate_chapters

    result = regenerate(meta.id, target="chapters", settings=settings)

    mock_fetch.assert_not_called()
    mock_transcript.assert_not_called()
    mock_clips.assert_not_called()
    mock_chapters.assert_called_once_with(meta.id, settings)

    backup_path = Path(f"{chapters_path}.bak")
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == "0:00 旧チャプター\n"
    assert "0:00 新チャプター" in result.chapters_text


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
def test_regenerate_clips_backs_up_candidates(
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)

    chapters_path = tmp_path / meta.id / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")

    candidates_path = tmp_path / meta.id / "clips" / "candidates.json"
    candidates_path.write_text('{"candidates": []}', encoding="utf-8")

    mock_clips.return_value = _mock_clips_result(tmp_path, meta.id)

    regenerate(meta.id, target="clips", settings=settings)

    backup_path = Path(f"{candidates_path}.bak")
    assert backup_path.is_file()
    mock_chapters.assert_not_called()


def test_regenerate_unknown_target_raises(tmp_path):
    settings = _setup_video_dir(tmp_path)
    _mock_transcript_paths(tmp_path, _sample_meta().id)

    with pytest.raises(PipelineError, match="未知の再生成対象"):
        regenerate("test1234567", target="highlights", settings=settings)


def test_regenerate_missing_transcript_raises(tmp_path):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)

    with pytest.raises(PipelineError, match="先に字幕の取得と整形を実行してください"):
        regenerate(meta.id, target="chapters", settings=settings)
