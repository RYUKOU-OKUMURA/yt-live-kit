"""CR-F15: CLI コマンドの基本パスと F-15 非対称解消のテスト."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from yt_live_kit.cli import app
from yt_live_kit.models.clips import ClipCandidate, ClipCandidatesDocument
from yt_live_kit.models.highlights import HighlightSegment, HighlightsDocument
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ai_prompt import ChapterGenerationResult
from yt_live_kit.services.chapter_validator import ValidationResult
from yt_live_kit.services.batch import BatchItemResult
from yt_live_kit.services.clips import ClipSuggestResult
from yt_live_kit.services.pipeline import PipelineResult

runner = CliRunner()


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


def _sample_pipeline_result(tmp_path: Path) -> PipelineResult:
    meta = _sample_meta()
    chapters_path = tmp_path / "chapters.md"
    chapters_path.write_text("0:00 開始\n", encoding="utf-8")
    full_path = tmp_path / "full.txt"
    full_path.write_text("全文\n", encoding="utf-8")
    return PipelineResult(
        video_id=meta.id,
        title=meta.title,
        meta=meta,
        chapters_text="0:00 開始\n",
        chapters_path=chapters_path,
        full_transcript_path=full_path,
        full_transcript_text="全文\n",
        clips_candidates=(),
        clips_candidates_path=None,
        clips_error=None,
        highlights_error=None,
    )


@patch("yt_live_kit.commands.fetch.fetch")
def test_fetch_cmd_success(mock_fetch):
    mock_fetch.return_value = _sample_meta()

    result = runner.invoke(
        app,
        ["fetch", "https://www.youtube.com/watch?v=test1234567"],
    )

    assert result.exit_code == 0
    mock_fetch.assert_called_once()
    assert "取得完了" in result.stdout
    assert "テスト動画" in result.stdout


@patch("yt_live_kit.commands.transcript.build_transcripts")
def test_transcript_cmd_success(mock_build, tmp_path):
    full_path = tmp_path / "full.txt"
    compressed_path = tmp_path / "compressed.txt"
    mock_build.return_value = (full_path, compressed_path)

    result = runner.invoke(app, ["transcript", "test1234567"])

    assert result.exit_code == 0
    mock_build.assert_called_once()
    assert "全文版" in result.stdout
    assert "圧縮版" in result.stdout


@patch("yt_live_kit.commands.chapters.generate_chapters")
def test_chapters_cmd_success(mock_generate, tmp_path):
    chapters_path = tmp_path / "chapters.md"
    mock_generate.return_value = ChapterGenerationResult(
        video_id="test1234567",
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )

    result = runner.invoke(app, ["chapters", "test1234567"])

    assert result.exit_code == 0
    mock_generate.assert_called_once()
    assert "チャプター" in result.stdout


@patch("yt_live_kit.commands.chapters.generate_chapters")
def test_chapters_cmd_creates_backup_before_generate(mock_generate, tmp_path, monkeypatch):
    video_id = "test1234567"
    chapters_dir = tmp_path / video_id / "chapters"
    chapters_dir.mkdir(parents=True)
    chapters_path = chapters_dir / "chapters.md"
    chapters_path.write_text("0:00 旧チャプター\n", encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_generate.return_value = ChapterGenerationResult(
        video_id=video_id,
        prompt_path=tmp_path / "prompt.txt",
        chapters_path=chapters_path,
        used_codex=True,
        validation=None,
    )

    result = runner.invoke(app, ["chapters", video_id])

    assert result.exit_code == 0
    backup_path = Path(f"{chapters_path}.bak")
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == "0:00 旧チャプター\n"


@patch("yt_live_kit.commands.chapters.save_chapters_from_file")
def test_chapters_cmd_from_file_creates_backup(mock_save, tmp_path, monkeypatch):
    video_id = "test1234567"
    chapters_dir = tmp_path / video_id / "chapters"
    chapters_dir.mkdir(parents=True)
    chapters_path = chapters_dir / "chapters.md"
    chapters_path.write_text("0:00 旧\n", encoding="utf-8")
    source_path = tmp_path / "manual.md"
    source_path.write_text("0:00 新\n5:00 本編\n10:00 終了\n", encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_save.return_value = (
        chapters_path,
        ValidationResult(ok=True, errors=(), chapters=()),
    )

    result = runner.invoke(
        app,
        ["chapters", video_id, "--from-file", str(source_path)],
    )

    assert result.exit_code == 0
    mock_save.assert_called_once()
    backup_path = Path(f"{chapters_path}.bak")
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == "0:00 旧\n"


@patch("yt_live_kit.commands.clips.suggest_clips")
def test_clips_suggest_cmd_success(mock_suggest, tmp_path):
    mock_suggest.return_value = ClipSuggestResult(
        video_id="test1234567",
        prompt_path=tmp_path / "prompt.txt",
        candidates_path=tmp_path / "candidates.json",
        used_codex=True,
        candidates=(
            ClipCandidate(
                id="clip_001",
                title="候補",
                start="00:03:42",
                end="00:16:30",
                duration_sec=768,
                reason="理由",
            ),
        ),
    )

    result = runner.invoke(app, ["clips", "suggest", "test1234567"])

    assert result.exit_code == 0
    mock_suggest.assert_called_once()
    assert "候補数: 1" in result.stdout


@patch("yt_live_kit.commands.clips.suggest_clips")
def test_clips_suggest_cmd_creates_backup_before_generate(
    mock_suggest, tmp_path, monkeypatch
):
    video_id = "test1234567"
    clips_dir = tmp_path / video_id / "clips"
    clips_dir.mkdir(parents=True)
    candidates_path = clips_dir / "candidates.json"
    candidates_path.write_text('{"candidates": []}', encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_suggest.return_value = ClipSuggestResult(
        video_id=video_id,
        prompt_path=tmp_path / "prompt.txt",
        candidates_path=candidates_path,
        used_codex=True,
        candidates=(),
    )

    result = runner.invoke(app, ["clips", "suggest", video_id])

    assert result.exit_code == 0
    backup_path = Path(f"{candidates_path}.bak")
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == '{"candidates": []}'


@patch("yt_live_kit.commands.clips.save_candidates_from_file")
def test_clips_suggest_cmd_from_file_creates_backup(mock_save, tmp_path, monkeypatch):
    video_id = "test1234567"
    clips_dir = tmp_path / video_id / "clips"
    clips_dir.mkdir(parents=True)
    candidates_path = clips_dir / "candidates.json"
    candidates_path.write_text('{"candidates": []}', encoding="utf-8")
    source_path = tmp_path / "manual.json"
    source_path.write_text('{"candidates": []}', encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_save.return_value = (
        candidates_path,
        ClipCandidatesDocument(candidates=[]),
    )

    result = runner.invoke(
        app,
        ["clips", "suggest", video_id, "--from-file", str(source_path)],
    )

    assert result.exit_code == 0
    mock_save.assert_called_once()
    backup_path = Path(f"{candidates_path}.bak")
    assert backup_path.is_file()


@patch("yt_live_kit.commands.highlights.save_segments_from_file")
def test_highlights_suggest_from_file(mock_save, tmp_path, monkeypatch):
    video_id = "test1234567"
    segments = [
        HighlightSegment(
            id="hl_001",
            title="山場",
            start="00:00:00",
            end="00:02:00",
            duration_sec=120,
            reason="理由",
        )
    ]
    segments_path = tmp_path / video_id / "highlights" / "segments.json"
    source_path = tmp_path / "manual.json"
    source_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_save.return_value = (segments_path, HighlightsDocument(candidates=segments))

    result = runner.invoke(
        app,
        ["highlights", "suggest", video_id, "--from-file", str(source_path)],
    )

    assert result.exit_code == 0
    mock_save.assert_called_once()
    assert "区間数: 1" in result.stdout


@patch("yt_live_kit.commands.highlights.suggest_highlights")
def test_highlights_suggest_cmd_creates_backup_before_generate(
    mock_suggest, tmp_path, monkeypatch
):
    video_id = "test1234567"
    highlights_dir = tmp_path / video_id / "highlights"
    highlights_dir.mkdir(parents=True)
    segments_path = highlights_dir / "segments.json"
    segments_path.write_text('{"candidates": []}', encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_suggest.return_value = MagicMock(
        prompt_path=tmp_path / "prompt.txt",
        segments_path=segments_path,
        segments=(),
    )

    result = runner.invoke(app, ["highlights", "suggest", video_id])

    assert result.exit_code == 0
    mock_suggest.assert_called_once()
    backup_path = Path(f"{segments_path}.bak")
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == '{"candidates": []}'


@patch("yt_live_kit.commands.highlights.suggest_highlights")
def test_highlights_suggest_cmd_prompt_only_skips_backup(mock_suggest, tmp_path, monkeypatch):
    video_id = "test1234567"
    highlights_dir = tmp_path / video_id / "highlights"
    highlights_dir.mkdir(parents=True)
    segments_path = highlights_dir / "segments.json"
    segments_path.write_text('{"candidates": []}', encoding="utf-8")

    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path))
    mock_suggest.return_value = MagicMock(
        prompt_path=tmp_path / "prompt.txt",
        segments_path=None,
        segments=(),
    )

    result = runner.invoke(
        app,
        ["highlights", "suggest", video_id, "--prompt-only"],
    )

    assert result.exit_code == 0
    assert not Path(f"{segments_path}.bak").exists()


@patch("yt_live_kit.commands.run.check_ytdlp_version_warning", return_value=None)
@patch("yt_live_kit.commands.run.run")
def test_run_cmd_success(mock_run, _mock_warning, tmp_path):
    mock_run.return_value = _sample_pipeline_result(tmp_path)

    result = runner.invoke(
        app,
        ["run", "https://www.youtube.com/watch?v=test1234567"],
    )

    assert result.exit_code == 0
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["do_chapters"] is True
    assert mock_run.call_args.kwargs["do_clips"] is True
    assert "処理完了" in result.stdout


@patch("yt_live_kit.commands.run.check_ytdlp_version_warning", return_value=None)
@patch("yt_live_kit.commands.run.run")
def test_run_cmd_passes_chapter_and_clip_flags(mock_run, _mock_warning, tmp_path):
    mock_run.return_value = _sample_pipeline_result(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "https://www.youtube.com/watch?v=test1234567",
            "--no-chapters",
            "--clips",
        ],
    )

    assert result.exit_code == 0
    assert mock_run.call_args.kwargs["do_chapters"] is False
    assert mock_run.call_args.kwargs["do_clips"] is True


@patch("yt_live_kit.commands.run.check_ytdlp_version_warning", return_value=None)
def test_run_cmd_rejects_when_both_targets_disabled(_mock_warning):
    result = runner.invoke(
        app,
        [
            "run",
            "https://www.youtube.com/watch?v=test1234567",
            "--no-chapters",
            "--no-clips",
        ],
    )

    assert result.exit_code == 1
    assert "どちらかを選んでください" in result.stderr


@patch("yt_live_kit.commands.run.check_ytdlp_version_warning", return_value=None)
@patch("yt_live_kit.commands.run.run_batch")
def test_run_cmd_batch_passes_flags(mock_run_batch, _mock_warning, tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://www.youtube.com/watch?v=test1234567\n", encoding="utf-8")
    mock_run_batch.return_value = [
        BatchItemResult(
            url="https://www.youtube.com/watch?v=test1234567",
            video_id="test1234567",
            status="success",
        )
    ]

    result = runner.invoke(
        app,
        ["run", "--urls-file", str(urls_file), "--no-chapters", "--clips"],
    )

    assert result.exit_code == 0
    assert mock_run_batch.call_args.kwargs["do_chapters"] is False
    assert mock_run_batch.call_args.kwargs["do_clips"] is True


@patch("yt_live_kit.commands.run.check_ytdlp_version_warning", return_value=None)
def test_run_cmd_batch_rejects_when_both_targets_disabled(_mock_warning, tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://www.youtube.com/watch?v=test1234567\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["run", "--urls-file", str(urls_file), "--no-chapters", "--no-clips"],
    )

    assert result.exit_code == 1
    assert "どちらかを選んでください" in result.stderr
