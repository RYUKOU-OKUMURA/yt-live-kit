"""clips / highlights ソフトフェイルのディスク永続化テスト."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from yt_live_kit.config import Settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ai_prompt import AiPromptError, ChapterGenerationResult
from yt_live_kit.services.clips import ClipValidationError, ClipsError
from yt_live_kit.services.highlights import HighlightsError
from yt_live_kit.services.pipeline import load_result_from_disk, regenerate, run


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


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_clips_softfail_persists_error_and_load_result_restores(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    mock_fetch.return_value = meta
    mock_transcript.return_value = _mock_transcript_paths(tmp_path, meta.id)

    chapters_path = tmp_path / meta.id / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    mock_chapters.return_value = ChapterGenerationResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )
    mock_clips.side_effect = ClipsError("Codex 失敗")

    result = run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=settings,
    )
    assert result.clips_error == "Codex 失敗"

    error_path = tmp_path / meta.id / "clips" / "last_error.txt"
    assert error_path.is_file()
    assert error_path.read_text(encoding="utf-8") == "Codex 失敗"

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.clips_error == "Codex 失敗"


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_clips_success_clears_persisted_error(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    from yt_live_kit.models.clips import ClipCandidate
    from yt_live_kit.services.clips import ClipSuggestResult

    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    mock_fetch.return_value = meta
    mock_transcript.return_value = _mock_transcript_paths(tmp_path, meta.id)

    chapters_path = tmp_path / meta.id / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    mock_chapters.return_value = ChapterGenerationResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )

    error_path = tmp_path / meta.id / "clips" / "last_error.txt"
    error_path.write_text("古いエラー", encoding="utf-8")

    candidates_path = tmp_path / meta.id / "clips" / "candidates.json"
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

    result = run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=settings,
    )
    assert result.clips_error is None
    assert not error_path.is_file()

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.clips_error is None


@patch("yt_live_kit.services.pipeline.suggest_highlights")
def test_regenerate_highlights_softfail_persists_error_and_load_restores(
    mock_highlights,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)
    mock_highlights.side_effect = HighlightsError("ハイライト生成に失敗しました。")

    result = regenerate(meta.id, target="highlights", settings=settings)
    assert result.highlights_error == "ハイライト生成に失敗しました。"

    error_path = tmp_path / meta.id / "highlights" / "last_error.txt"
    assert error_path.is_file()
    assert error_path.read_text(encoding="utf-8") == "ハイライト生成に失敗しました。"

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.highlights_error == "ハイライト生成に失敗しました。"


@patch("yt_live_kit.services.pipeline.suggest_highlights")
def test_regenerate_highlights_success_clears_persisted_error(
    mock_highlights,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)

    error_path = tmp_path / meta.id / "highlights" / "last_error.txt"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    error_path.write_text("古いエラー", encoding="utf-8")

    mock_highlights.return_value = MagicMock()

    result = regenerate(meta.id, target="highlights", settings=settings)
    assert result.highlights_error is None
    assert not error_path.is_file()

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.highlights_error is None


@patch("yt_live_kit.services.pipeline.suggest_clips")
def test_regenerate_clips_softfail_persists_error_and_load_restores(
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)
    mock_clips.side_effect = ClipValidationError("候補が不正", ("テストエラー",))

    result = regenerate(meta.id, target="clips", settings=settings)
    assert result.clips_error is not None
    assert "候補が不正" in result.clips_error

    error_path = tmp_path / meta.id / "clips" / "last_error.txt"
    assert error_path.is_file()

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.clips_error is not None
    assert "候補が不正" in loaded.clips_error


def test_load_result_from_disk_restores_clips_error_from_file_only(tmp_path):
    """成果物が無く last_error.txt だけある場合も clips_error を復元する."""
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    _mock_transcript_paths(tmp_path, meta.id)

    error_path = tmp_path / meta.id / "clips" / "last_error.txt"
    error_path.write_text("保存済みの Codex 失敗", encoding="utf-8")

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.clips_error == "保存済みの Codex 失敗"
    assert loaded.clips_candidates == ()


@patch("yt_live_kit.services.pipeline.suggest_clips")
@patch("yt_live_kit.services.pipeline.generate_chapters")
@patch("yt_live_kit.services.pipeline.build_transcripts")
@patch("yt_live_kit.services.pipeline.fetch")
def test_run_clips_ai_prompt_error_persists_message(
    mock_fetch,
    mock_transcript,
    mock_chapters,
    mock_clips,
    tmp_path,
):
    meta = _sample_meta()
    settings = _setup_video_dir(tmp_path, meta)
    mock_fetch.return_value = meta
    mock_transcript.return_value = _mock_transcript_paths(tmp_path, meta.id)

    chapters_path = tmp_path / meta.id / "chapters" / "chapters.md"
    chapters_path.write_text("0:00 開始\n5:00 本編\n10:00 終了\n", encoding="utf-8")
    mock_chapters.return_value = ChapterGenerationResult(
        video_id=meta.id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )
    mock_clips.side_effect = AiPromptError("Codex CLI がタイムアウトしました（300 秒）。")

    run(
        "https://www.youtube.com/watch?v=test1234567",
        settings=settings,
    )

    loaded = load_result_from_disk(meta.id, settings)
    assert loaded is not None
    assert loaded.clips_error == "Codex CLI がタイムアウトしました（300 秒）。"
