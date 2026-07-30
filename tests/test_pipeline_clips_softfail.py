"""パイプラインの clips ソフトフェイルテスト."""

from datetime import datetime, timezone
from unittest.mock import patch

from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ai_prompt import AiPromptError, ChapterGenerationResult
from yt_live_kit.services.clips import ClipValidationError, ClipsError
from yt_live_kit.services.pipeline import load_result_from_disk, run


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
def test_run_clips_failure_returns_chapters(
    mock_fetch, mock_transcript, mock_chapters, mock_clips, tmp_path
):
    meta = _sample_meta()
    mock_fetch.return_value = meta

    full_path = tmp_path / "full.txt"
    full_path.write_text("[00:00:00] こんにちは\n", encoding="utf-8")
    compressed_path = tmp_path / "compressed.txt"
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

    mock_clips.side_effect = ClipValidationError("候補が不正", ("テストエラー",))

    result = run("https://www.youtube.com/watch?v=test1234567")

    assert result.chapters_text.startswith("0:00 開始")
    assert result.clips_candidates == ()
    assert result.clips_error is not None
    assert "候補が不正" in result.clips_error


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_clips_error_still_succeeds(
    mock_fetch, mock_transcript, mock_chapters, mock_clips, tmp_path
):
    meta = _sample_meta()
    mock_fetch.return_value = meta

    full_path = tmp_path / "full.txt"
    full_path.write_text("全文\n", encoding="utf-8")
    mock_transcript.return_value = (full_path, tmp_path / "compressed.txt")

    chapters_path = tmp_path / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    mock_chapters.return_value = ChapterGenerationResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )

    mock_clips.side_effect = ClipsError("Codex 失敗")

    result = run("https://www.youtube.com/watch?v=test1234567")
    assert "0:00 開始" in result.chapters_text
    assert result.clips_error == "Codex 失敗"


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_clips_ai_prompt_error_still_succeeds(
    mock_fetch, mock_transcript, mock_chapters, mock_clips, tmp_path
):
    meta = _sample_meta()
    mock_fetch.return_value = meta

    full_path = tmp_path / "full.txt"
    full_path.write_text("全文\n", encoding="utf-8")
    mock_transcript.return_value = (full_path, tmp_path / "compressed.txt")

    chapters_path = tmp_path / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    mock_chapters.return_value = ChapterGenerationResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )

    mock_clips.side_effect = AiPromptError("Codex CLI がタイムアウトしました（300 秒）。")

    result = run("https://www.youtube.com/watch?v=test1234567")
    assert "0:00 開始" in result.chapters_text
    assert result.clips_error == "Codex CLI がタイムアウトしました（300 秒）。"


def test_load_result_from_disk(tmp_path):
    from yt_live_kit.config import Settings
    from yt_live_kit.models.clips import ClipCandidate, ClipCandidatesDocument

    video_id = "loadtest123"
    video_dir = tmp_path / video_id
    (video_dir / "chapters").mkdir(parents=True)
    (video_dir / "transcript").mkdir()
    (video_dir / "clips").mkdir()

    meta = _sample_meta()
    meta_path = video_dir / "meta.json"
    meta_path.write_text(meta.model_dump_json(), encoding="utf-8")

    chapters_path = video_dir / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")

    full_path = video_dir / "transcript" / "full.txt"
    full_path.write_text("[00:00:00] test\n", encoding="utf-8")

    doc = ClipCandidatesDocument(
        candidates=[
            ClipCandidate(
                id="clip_001",
                title="テスト",
                start="03:42",
                end="16:30",
                duration_sec=768,
                reason="理由",
            ),
            ClipCandidate(
                id="clip_002",
                title="テスト2",
                start="25:30",
                end="38:00",
                duration_sec=750,
                reason="理由2",
            ),
        ]
    )
    candidates_path = video_dir / "clips" / "candidates.json"
    candidates_path.write_text(doc.model_dump_json(), encoding="utf-8")

    settings = Settings(data_dir=tmp_path)
    result = load_result_from_disk(video_id, settings)

    assert result is not None
    assert result.video_id == video_id
    assert len(result.clips_candidates) == 2
    assert "0:00 開始" in result.chapters_text


def test_load_result_from_disk_missing_returns_none(tmp_path):
    from yt_live_kit.config import Settings

    settings = Settings(data_dir=tmp_path)
    assert load_result_from_disk("nonexistent", settings) is None
