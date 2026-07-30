"""パイプライン — fetch → transcript → chapters → clips suggest を統括."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate, ClipCandidatesDocument
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
from yt_live_kit.services.ytdlp import (
    SubtitleNotFoundError,
    YtdlpError,
    extract_video_id,
    fetch,
)

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

_SKIP_SUFFIX = "（取得済みのためスキップ）"

_REGENERATE_TARGETS = frozenset({"chapters", "clips"})


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
    clips_error: str | None = None


def _notify(
    callback: ProgressCallback | None,
    stage: str,
    message: str | None = None,
) -> None:
    if callback is not None:
        callback(stage, message or STAGE_MESSAGES.get(stage, stage))


def _video_dir(video_id: str, settings: Settings) -> Path:
    return settings.data_dir / video_id


def _fetch_artifacts_exist(video_dir: Path) -> bool:
    return (video_dir / "meta.json").is_file()


def _transcript_artifacts_exist(video_dir: Path) -> bool:
    transcript_dir = video_dir / "transcript"
    return (
        (transcript_dir / "full.txt").is_file()
        and (transcript_dir / "compressed.txt").is_file()
    )


def _load_meta(video_dir: Path) -> VideoMeta:
    meta_path = video_dir / "meta.json"
    return VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))


def _transcript_paths(video_dir: Path) -> tuple[Path, Path]:
    transcript_dir = video_dir / "transcript"
    return transcript_dir / "full.txt", transcript_dir / "compressed.txt"


def _chapters_path(video_dir: Path) -> Path:
    return video_dir / "chapters" / "chapters.md"


def _backup_file(path: Path) -> None:
    """既存成果物を .bak として退避する."""
    if path.is_file():
        shutil.copy2(path, Path(f"{path}.bak"))


def _load_clips_from_disk(video_dir: Path) -> tuple[tuple[ClipCandidate, ...], Path | None]:
    candidates_path = video_dir / "clips" / "candidates.json"
    if not candidates_path.is_file():
        return (), None
    doc = ClipCandidatesDocument.model_validate_json(
        candidates_path.read_text(encoding="utf-8")
    )
    return tuple(doc.candidates), candidates_path


def _ensure_transcript_prerequisites(video_dir: Path) -> None:
    if not _fetch_artifacts_exist(video_dir) or not _transcript_artifacts_exist(video_dir):
        raise PipelineError("先に字幕の取得と整形を実行してください")


def _assemble_result(
    video_id: str,
    settings: Settings,
    *,
    clips_candidates: tuple[ClipCandidate, ...] | None = None,
    clips_candidates_path: Path | None = None,
    clips_error: str | None = None,
) -> PipelineResult:
    """保存済み成果物から PipelineResult を組み立てる."""
    video_dir = _video_dir(video_id, settings)
    meta = _load_meta(video_dir)
    full_path, _compressed_path = _transcript_paths(video_dir)
    chapters_path = _chapters_path(video_dir)

    chapters_text = (
        chapters_path.read_text(encoding="utf-8")
        if chapters_path.is_file()
        else ""
    )
    full_transcript_text = full_path.read_text(encoding="utf-8")

    if clips_candidates is None:
        clips_candidates, clips_candidates_path = _load_clips_from_disk(video_dir)

    return PipelineResult(
        video_id=video_id,
        title=meta.title,
        meta=meta,
        chapters_text=chapters_text,
        chapters_path=chapters_path,
        full_transcript_path=full_path,
        full_transcript_text=full_transcript_text,
        clips_candidates=clips_candidates,
        clips_candidates_path=clips_candidates_path,
        clips_error=clips_error,
    )


def run(
    url: str,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
    *,
    do_chapters: bool = True,
    do_clips: bool = True,
) -> PipelineResult:
    """URL から fetch → transcript → chapters → clips suggest まで順に実行する."""
    settings = settings or get_settings()

    try:
        video_id = extract_video_id(url)
    except YtdlpError as exc:
        raise PipelineError(str(exc)) from exc

    video_dir = _video_dir(video_id, settings)
    fetch_exists = _fetch_artifacts_exist(video_dir)
    transcript_exists = _transcript_artifacts_exist(video_dir)

    if fetch_exists:
        _notify(
            on_progress,
            STAGE_FETCH,
            f"{STAGE_MESSAGES[STAGE_FETCH]}{_SKIP_SUFFIX}",
        )
        meta = _load_meta(video_dir)
    else:
        try:
            _notify(on_progress, STAGE_FETCH)
            meta = fetch(url, settings)
        except SubtitleNotFoundError as exc:
            raise PipelineError(str(exc)) from exc
        except YtdlpError as exc:
            raise PipelineError(str(exc)) from exc

    video_id = meta.id
    video_dir = _video_dir(video_id, settings)

    if transcript_exists:
        _notify(
            on_progress,
            STAGE_TRANSCRIPT,
            f"{STAGE_MESSAGES[STAGE_TRANSCRIPT]}{_SKIP_SUFFIX}",
        )
        full_path, _compressed_path = _transcript_paths(video_dir)
    else:
        try:
            _notify(on_progress, STAGE_TRANSCRIPT)
            full_path, _compressed_path = build_transcripts(video_id, settings)
        except TranscriptError as exc:
            raise PipelineError(str(exc)) from exc

    chapters_path = _chapters_path(video_dir)
    if do_chapters:
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
        chapters_path = chapter_result.chapters_path
        chapters_text = chapters_path.read_text(encoding="utf-8")
    else:
        chapters_text = (
            chapters_path.read_text(encoding="utf-8")
            if chapters_path.is_file()
            else ""
        )

    full_transcript_text = full_path.read_text(encoding="utf-8")

    clips_candidates: tuple[ClipCandidate, ...] = ()
    clips_candidates_path: Path | None = None
    clips_error: str | None = None

    if do_clips:
        try:
            _notify(on_progress, STAGE_CLIPS_SUGGEST)
            clips_result = suggest_clips(video_id, settings)
            clips_candidates = clips_result.candidates
            clips_candidates_path = clips_result.candidates_path
        except (CodexNotFoundError, ClipValidationError, ClipsError, AiPromptError) as exc:
            clips_error = str(exc)
    else:
        clips_candidates, clips_candidates_path = _load_clips_from_disk(video_dir)

    return PipelineResult(
        video_id=video_id,
        title=meta.title,
        meta=meta,
        chapters_text=chapters_text,
        chapters_path=chapters_path,
        full_transcript_path=full_path,
        full_transcript_text=full_transcript_text,
        clips_candidates=clips_candidates,
        clips_candidates_path=clips_candidates_path,
        clips_error=clips_error,
    )


def regenerate(
    video_id: str,
    *,
    target: str,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """保存済み字幕を入力に、指定ステップのみ再実行する."""
    if target not in _REGENERATE_TARGETS:
        raise PipelineError(f"未知の再生成対象です: {target}")

    settings = settings or get_settings()
    video_dir = _video_dir(video_id, settings)

    try:
        _ensure_transcript_prerequisites(video_dir)
    except PipelineError:
        raise

    clips_candidates: tuple[ClipCandidate, ...] | None = None
    clips_candidates_path: Path | None = None
    clips_error: str | None = None

    if target == "chapters":
        try:
            _notify(on_progress, STAGE_CHAPTERS)
            _backup_file(_chapters_path(video_dir))
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
    elif target == "clips":
        try:
            _notify(on_progress, STAGE_CLIPS_SUGGEST)
            _backup_file(video_dir / "clips" / "candidates.json")
            clips_result = suggest_clips(video_id, settings)
            clips_candidates = clips_result.candidates
            clips_candidates_path = clips_result.candidates_path
        except (CodexNotFoundError, ClipValidationError, ClipsError, AiPromptError) as exc:
            clips_error = str(exc)

    return _assemble_result(
        video_id,
        settings,
        clips_candidates=clips_candidates,
        clips_candidates_path=clips_candidates_path,
        clips_error=clips_error,
    )


def run_single_job_target(*, report, settings, url: str) -> None:
    """start_job 用: 単本 URL を pipeline.run で処理する."""
    from yt_live_kit.services.jobs import get_active_job, update_job

    def on_progress(stage: str, message: str) -> None:
        report(stage=stage, message=message)

    result = run(url.strip(), settings, on_progress=on_progress)
    active = get_active_job(settings)
    if active is not None:
        update_job(
            active.job_id,
            settings=settings,
            video_id=result.video_id,
            title=result.title,
        )


def load_result_from_disk(
    video_id: str,
    settings: Settings | None = None,
) -> PipelineResult | None:
    """data/{video_id}/ から保存済み成果物を読み込む."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    meta_path = video_dir / "meta.json"
    chapters_path = video_dir / "chapters" / "chapters.md"
    full_path = video_dir / "transcript" / "full.txt"

    if not meta_path.is_file() or not chapters_path.is_file() or not full_path.is_file():
        return None

    return _assemble_result(video_id, settings)
