"""パイプライン — fetch → transcript → chapters → clips suggest を統括."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ai_prompt import (
    AiPromptError,
    ChapterValidationError,
    CodexNotFoundError,
    generate_chapters,
)
from yt_live_kit.services.clips import (
    ClipValidationError,
    ClipsError,
    suggest_clips,
)
from yt_live_kit.services.transcript import TranscriptError, build_transcripts
from yt_live_kit.services.ytdlp import SubtitleNotFoundError, YtdlpError, fetch

ProgressCallback = Callable[[str, str], None]

STAGE_FETCH = "fetch"
STAGE_TRANSCRIPT = "transcript"
STAGE_CHAPTERS = "chapters"
STAGE_CLIPS_SUGGEST = "clips_suggest"

STAGE_LABELS: dict[str, str] = {
    STAGE_FETCH: "字幕取得",
    STAGE_TRANSCRIPT: "整形",
    STAGE_CHAPTERS: "チャプター生成",
    STAGE_CLIPS_SUGGEST: "切り抜き候補",
}

STAGE_MESSAGES: dict[str, str] = {
    STAGE_FETCH: "字幕を取得しています…",
    STAGE_TRANSCRIPT: "字幕を整形しています…",
    STAGE_CHAPTERS: "チャプターを生成しています…",
    STAGE_CLIPS_SUGGEST: "切り抜き候補を生成しています…",
}


class PipelineError(Exception):
    """パイプライン実行エラー（ユーザー向け日本語メッセージ）."""


@dataclass(frozen=True)
class PipelineResult:
    """パイプライン完了結果."""

    video_id: str
    title: str
    meta: VideoMeta
    chapters_text: str
    chapters_path: Path
    full_transcript_path: Path
    full_transcript_text: str
    clips_candidates: tuple[ClipCandidate, ...]
    clips_candidates_path: Path | None


def _notify(
    callback: ProgressCallback | None,
    stage: str,
    message: str | None = None,
) -> None:
    if callback is not None:
        callback(stage, message or STAGE_MESSAGES.get(stage, stage))


def run(
    url: str,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """URL から fetch → transcript → chapters → clips suggest まで順に実行する."""
    settings = settings or get_settings()

    try:
        _notify(on_progress, STAGE_FETCH)
        meta = fetch(url, settings)
    except SubtitleNotFoundError as exc:
        raise PipelineError(str(exc)) from exc
    except YtdlpError as exc:
        raise PipelineError(str(exc)) from exc

    video_id = meta.id

    try:
        _notify(on_progress, STAGE_TRANSCRIPT)
        full_path, _compressed_path = build_transcripts(video_id, settings)
    except TranscriptError as exc:
        raise PipelineError(str(exc)) from exc

    try:
        _notify(on_progress, STAGE_CHAPTERS)
        chapter_result = generate_chapters(video_id, settings)
    except CodexNotFoundError as exc:
        raise PipelineError(str(exc)) from exc
    except ChapterValidationError as exc:
        raise PipelineError(str(exc)) from exc
    except AiPromptError as exc:
        raise PipelineError(str(exc)) from exc

    if chapter_result.chapters_path is None:
        raise PipelineError(
            "チャプターの生成に失敗しました。Codex CLI の設定を確認してください。"
        )

    try:
        _notify(on_progress, STAGE_CLIPS_SUGGEST)
        clips_result = suggest_clips(video_id, settings)
    except CodexNotFoundError as exc:
        raise PipelineError(str(exc)) from exc
    except ClipValidationError as exc:
        raise PipelineError(str(exc)) from exc
    except ClipsError as exc:
        raise PipelineError(str(exc)) from exc

    chapters_text = chapter_result.chapters_path.read_text(encoding="utf-8")
    full_transcript_text = full_path.read_text(encoding="utf-8")

    return PipelineResult(
        video_id=video_id,
        title=meta.title,
        meta=meta,
        chapters_text=chapters_text,
        chapters_path=chapter_result.chapters_path,
        full_transcript_path=full_path,
        full_transcript_text=full_transcript_text,
        clips_candidates=clips_result.candidates,
        clips_candidates_path=clips_result.candidates_path,
    )
