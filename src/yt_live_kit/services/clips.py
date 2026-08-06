"""切り抜き候補の提案・保存."""

from __future__ import annotations

import json
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import (
    ClipCandidate,
    ClipCandidatesDocument,
    ClipCandidatesLineage,
)
from yt_live_kit.models.transcript import TranscriptArtifact, TranscriptSourceKind
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services.ai_prompt import (
    AiPromptError,
    CodexNotFoundError,
    invoke_codex,
    is_codex_available,
)
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.transcript_artifact import (
    canonical_json,
    TranscriptArtifactError,
    TranscriptArtifactStore,
)

logger = logging.getLogger(__name__)

PLACEHOLDER = "{{compressed_transcript}}"
TEMPLATE_NAME = "clips_suggest.md"
MIN_CANDIDATES = 2
MIN_DURATION_SEC = 480  # 8 分
MAX_DURATION_SEC = 1080  # 18 分

CODEX_INSTALL_HINT = """\
Codex CLI が見つかりません。切り抜き候補の自動生成には Codex CLI が必要です。

【インストール手順】
1. Node.js がインストールされていることを確認してください
2. 次のコマンドで Codex CLI をインストールします:
   npm install -g @openai/codex
3. インストール後、認証を行います:
   codex login

【フォールバック（Cursor 手動運用）】
1. 次のプロンプトファイルを Cursor のチャットに貼り付けてください:
   {prompt_path}
2. 生成された JSON を {candidates_path} に保存してください
3. 保存後、次のコマンドで検証できます:
   uv run yt-live-kit clips suggest {video_id} --from-file {candidates_path}
"""


class ClipsError(AiPromptError):
    """切り抜き候補生成エラー."""


class ClipValidationError(ClipsError):
    """候補バリデーション失敗."""

    def __init__(self, message: str, errors: tuple[str, ...]) -> None:
        super().__init__(message)
        self.errors = errors


@dataclass(frozen=True)
class ClipSuggestResult:
    """切り抜き候補生成結果."""

    video_id: str
    prompt_path: Path
    candidates_path: Path | None
    used_codex: bool
    candidates: tuple[ClipCandidate, ...]


def make_candidate_fingerprint(
    source: str,
    candidates: list[ClipCandidate] | tuple[ClipCandidate, ...],
    *,
    coarse_vtt_artifact_fingerprint: str | None = None,
    coarse_full_cue_digest: str | None = None,
) -> str:
    """候補内容と表示順から deterministic fingerprint を作る。

    lineage を省略した場合は v3.2 の既存 UI helper と同じ payload を使う。
    S9-2 の provenance がある場合だけ、候補の親 VTT digest を root snapshot
    に加える。候補配列は表示順のまま扱い、sort / dedupe はしない。
    """

    payload: dict[str, object] = {
        "source": source,
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }
    if coarse_vtt_artifact_fingerprint is not None or coarse_full_cue_digest is not None:
        if not isinstance(coarse_vtt_artifact_fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", coarse_vtt_artifact_fingerprint
        ):
            raise ClipsError("coarse VTT artifact fingerprint が正しくありません。")
        if not isinstance(coarse_full_cue_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", coarse_full_cue_digest
        ):
            raise ClipsError("coarse VTT の全 cue digest が正しくありません。")
        payload["coarse_lineage"] = {
            "source_kind": TranscriptSourceKind.YOUTUBE_VTT.value,
            "artifact_fingerprint": coarse_vtt_artifact_fingerprint,
            "full_cue_digest": coarse_full_cue_digest,
        }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


compute_candidate_fingerprint = make_candidate_fingerprint


def make_coarse_candidate_lineage(
    candidates: list[ClipCandidate] | tuple[ClipCandidate, ...],
    artifact: TranscriptArtifact,
    *,
    source: str = "clips",
) -> ClipCandidatesLineage:
    """有効な YouTube VTT artifact と候補表示順を root lineage に凍結する。"""

    if not isinstance(artifact, TranscriptArtifact):
        raise ClipsError("候補 lineage には TranscriptArtifact が必要です。")
    if (
        artifact.source_kind != TranscriptSourceKind.YOUTUBE_VTT
        or artifact.status.value != "success"
        or any(item.status.value != "success" for item in artifact.ranges)
    ):
        raise ClipsError("候補探索の lineage には有効な YouTube VTT artifact が必要です。")
    candidate_fp = make_candidate_fingerprint(
        source,
        candidates,
        coarse_vtt_artifact_fingerprint=artifact.artifact_fingerprint,
        coarse_full_cue_digest=artifact.cue_digest,
    )
    return ClipCandidatesLineage(
        source_kind="youtube_vtt",
        coarse_vtt_artifact_fingerprint=artifact.artifact_fingerprint,
        coarse_full_cue_digest=artifact.cue_digest,
        candidate_fingerprint=candidate_fp,
    )


def _validate_candidate_lineage(
    document: ClipCandidatesDocument,
    *,
    source: str = "clips",
) -> ClipCandidatesDocument:
    lineage = document.lineage
    if lineage is None:
        return document
    expected = make_candidate_fingerprint(
        source,
        document.candidates,
        coarse_vtt_artifact_fingerprint=lineage.coarse_vtt_artifact_fingerprint,
        coarse_full_cue_digest=lineage.coarse_full_cue_digest,
    )
    if expected != lineage.candidate_fingerprint:
        raise ClipsError("候補 lineage の fingerprint が候補内容または表示順と一致しません。")
    return document


def _load_coarse_vtt_artifact(
    video_id: str,
    settings: Settings,
) -> TranscriptArtifact | None:
    """既存候補探索の前に VTT provenance を読み取る（Whisper は呼ばない）。"""

    vtt_path = settings.data_dir / video_id / "subtitles" / "ja.vtt"
    if not vtt_path.is_file():
        return None
    try:
        artifact, _cache_hit = TranscriptArtifactStore(video_id, settings).save_vtt(
            vtt_path=vtt_path
        )
        return artifact
    except TranscriptArtifactError as exc:
        logger.warning("候補探索用の VTT provenance を保存できませんでした: %s", exc)
        return None


def find_project_root() -> Path:
    """リポジトリルート（prompts/clips_suggest.md があるディレクトリ）を返す."""
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in [start, *start.parents]:
            if (candidate / "prompts" / TEMPLATE_NAME).is_file():
                return candidate
    raise ClipsError(
        f"プロンプトテンプレートが見つかりません: prompts/{TEMPLATE_NAME}。"
        "リポジトリルートで実行してください。"
    )


def load_clips_template(project_root: Path | None = None) -> str:
    """切り抜き候補生成用テンプレートを読み込む."""
    root = project_root or find_project_root()
    template_path = root / "prompts" / TEMPLATE_NAME
    if not template_path.is_file():
        raise ClipsError(f"プロンプトテンプレートが見つかりません: {template_path}")
    return template_path.read_text(encoding="utf-8")


def build_clips_prompt(compressed_text: str, project_root: Path | None = None) -> str:
    """圧縮テキストを埋め込んだ完成プロンプトを返す."""
    template = load_clips_template(project_root)
    if PLACEHOLDER not in template:
        raise ClipsError(f"テンプレートにプレースホルダ {PLACEHOLDER} がありません。")
    return template.replace(PLACEHOLDER, compressed_text.strip())


def _read_compressed_transcript(video_dir: Path) -> str:
    compressed_path = video_dir / "transcript" / "compressed.txt"
    if not compressed_path.is_file():
        raise ClipsError(
            f"圧縮版テキストが見つかりません: {compressed_path}。"
            "先に transcript を実行してください。"
        )
    return compressed_path.read_text(encoding="utf-8")


def save_clips_prompt_file(video_id: str, prompt: str, settings: Settings) -> Path:
    """完成プロンプトを data/{video_id}/prompt_clips.txt に保存する."""
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise ClipsError(f"動画ディレクトリが見つかりません: {video_dir}")
    prompt_path = video_dir / "prompt_clips.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def _extract_json_object(text: str) -> dict:
    """AI 出力から JSON オブジェクトを抽出する."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        json_lines: list[str] = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                if in_block:
                    break
                in_block = True
                continue
            if in_block:
                json_lines.append(line)
        stripped = "\n".join(json_lines).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            raise ClipValidationError(
                "切り抜き候補の JSON を解析できませんでした。",
                ("JSON 形式が不正です。",),
            ) from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ClipValidationError(
                "切り抜き候補の JSON を解析できませんでした。",
                (str(exc),),
            ) from exc

    if not isinstance(data, dict):
        raise ClipValidationError(
            "切り抜き候補の JSON を解析できませんでした。",
            ("ルートは JSON オブジェクトである必要があります。",),
        )
    return data


def _normalize_timestamp(timestamp: str) -> str:
    """M:SS を HH:MM:SS 風に正規化（表示用）."""
    parts = timestamp.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return f"{int(minutes):02d}:{seconds}"
    return timestamp


def validate_clip_candidates(
    data: dict,
    *,
    video_duration_sec: int | None = None,
) -> tuple[ClipCandidatesDocument, tuple[str, ...]]:
    """候補 JSON を検証し、正規化済みドキュメントを返す."""
    errors: list[str] = []

    try:
        doc = ClipCandidatesDocument.model_validate(data)
    except Exception as exc:
        logger.warning("切り抜き候補 JSON のスキーマ検証に失敗しました: %s", exc)
        return ClipCandidatesDocument(candidates=[]), (
            "切り抜き候補の JSON 形式が想定と異なります。"
            "必須項目の不足か、値の型が誤っています。",
        )

    try:
        _validate_candidate_lineage(doc)
    except ClipsError as exc:
        errors.append(str(exc))

    if len(doc.candidates) < MIN_CANDIDATES:
        errors.append(
            f"切り抜き候補は {MIN_CANDIDATES} 件以上必要です（現在 {len(doc.candidates)} 件）。"
        )

    normalized: list[ClipCandidate] = []
    for i, candidate in enumerate(doc.candidates):
        prefix = f"候補 {i + 1} ({candidate.id})"

        if "<" in candidate.title or ">" in candidate.title:
            errors.append(f"{prefix}: タイトルに半角の 〈 〉 は使えません。")

        try:
            start_sec = parse_timestamp_to_seconds(candidate.start)
            end_sec = parse_timestamp_to_seconds(candidate.end)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
            continue

        if end_sec <= start_sec:
            errors.append(f"{prefix}: 終了時刻は開始時刻より後である必要があります。")
            continue

        if video_duration_sec is not None and end_sec > video_duration_sec:
            errors.append(
                f"{prefix}: 終了時刻 ({candidate.end}) が動画長"
                f" ({video_duration_sec // 60}:{video_duration_sec % 60:02d}) を超えています。"
            )

        duration = end_sec - start_sec
        if duration < MIN_DURATION_SEC or duration > MAX_DURATION_SEC:
            errors.append(
                f"{prefix}: 区間長が {MIN_DURATION_SEC // 60}〜{MAX_DURATION_SEC // 60} 分の"
                f"目安から外れています（{duration // 60} 分 {duration % 60} 秒）。"
            )

        if candidate.duration_sec != duration:
            errors.append(
                f"{prefix}: duration_sec ({candidate.duration_sec}) が"
                f" start/end から計算した値 ({duration}) と一致しません。"
            )

        normalized.append(
            ClipCandidate(
                id=candidate.id,
                title=candidate.title.strip(),
                start=_normalize_timestamp(candidate.start),
                end=_normalize_timestamp(candidate.end),
                duration_sec=duration,
                reason=candidate.reason.strip(),
            )
        )

    # 重複チェック
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            a, b = normalized[i], normalized[j]
            a_start = parse_timestamp_to_seconds(a.start)
            a_end = parse_timestamp_to_seconds(a.end)
            b_start = parse_timestamp_to_seconds(b.start)
            b_end = parse_timestamp_to_seconds(b.end)
            if a_start < b_end and b_start < a_end:
                errors.append(
                    f"候補 {a.id} と {b.id} の区間が重複しています。"
                )

    if errors:
        return ClipCandidatesDocument(
            candidates=tuple(normalized), lineage=doc.lineage
        ), tuple(errors)

    return ClipCandidatesDocument(candidates=normalized, lineage=doc.lineage), ()


def _load_video_duration(video_id: str, settings: Settings) -> int | None:
    meta_path = settings.data_dir / video_id / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    duration = meta_data.get("duration")
    return int(duration) if duration is not None else None


def save_candidates_file(
    video_id: str,
    raw_json_text: str,
    settings: Settings,
    *,
    coarse_artifact: TranscriptArtifact | None = None,
    lineage: ClipCandidatesLineage | None = None,
) -> tuple[Path, ClipCandidatesDocument]:
    """バリデーション通過後に clips/candidates.json に保存する."""
    data = _extract_json_object(raw_json_text)
    if coarse_artifact is not None and lineage is not None:
        raise ClipsError("候補 lineage は artifact または lineage のどちらか一方で指定してください。")
    video_duration = _load_video_duration(video_id, settings)
    doc, errors = validate_clip_candidates(data, video_duration_sec=video_duration)
    if errors:
        detail = "\n".join(f"- {err}" for err in errors)
        raise ClipValidationError(
            f"切り抜き候補の形式が正しくありません:\n{detail}",
            errors,
        )

    if coarse_artifact is not None:
        doc = ClipCandidatesDocument(
            candidates=doc.candidates,
            lineage=make_coarse_candidate_lineage(doc.candidates, coarse_artifact),
        )
    elif lineage is not None:
        candidate_doc = ClipCandidatesDocument(candidates=doc.candidates, lineage=lineage)
        _validate_candidate_lineage(candidate_doc)
        doc = candidate_doc

    video_dir = settings.data_dir / video_id
    clips_dir = video_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = clips_dir / "candidates.json"
    write_text_atomically(candidates_path, doc.model_dump_json(indent=2))
    return candidates_path, doc


def load_candidates_file(video_id: str, settings: Settings) -> ClipCandidatesDocument | None:
    """保存済み candidates.json を読み込む."""
    path = settings.data_dir / video_id / "clips" / "candidates.json"
    if not path.is_file():
        return None
    try:
        document = ClipCandidatesDocument.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        return _validate_candidate_lineage(document)
    except (OSError, ValueError) as exc:
        raise ClipsError("切り抜き候補の lineage が壊れているため安全に読み込めません。") from exc


def suggest_clips(
    video_id: str,
    settings: Settings | None = None,
    *,
    prompt_only: bool = False,
    codex_path: str = "codex",
) -> ClipSuggestResult:
    """圧縮テキストから切り抜き候補を生成して保存する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise ClipsError(f"動画ディレクトリが見つかりません: {video_dir}")

    compressed_text = _read_compressed_transcript(video_dir)
    project_root = find_project_root()
    prompt = build_clips_prompt(compressed_text, project_root)
    prompt_path = save_clips_prompt_file(video_id, prompt, settings)

    if prompt_only:
        return ClipSuggestResult(
            video_id=video_id,
            prompt_path=prompt_path,
            candidates_path=None,
            used_codex=False,
            candidates=(),
        )

    if not is_codex_available(codex_path):
        candidates_path = video_dir / "clips" / "candidates.json"
        hint = CODEX_INSTALL_HINT.format(
            prompt_path=prompt_path,
            candidates_path=candidates_path,
            video_id=video_id,
        )
        raise CodexNotFoundError(hint)

    raw_output = invoke_codex(prompt, codex_path=codex_path)
    coarse_artifact = _load_coarse_vtt_artifact(video_id, settings)
    candidates_path, doc = save_candidates_file(
        video_id,
        raw_output,
        settings,
        coarse_artifact=coarse_artifact,
    )

    return ClipSuggestResult(
        video_id=video_id,
        prompt_path=prompt_path,
        candidates_path=candidates_path,
        used_codex=True,
        candidates=tuple(doc.candidates),
    )


def save_candidates_from_file(
    video_id: str,
    source_path: Path,
    settings: Settings | None = None,
) -> tuple[Path, ClipCandidatesDocument]:
    """外部ファイル（Cursor 手動生成等）から候補を検証・保存する."""
    settings = settings or get_settings()
    if not source_path.is_file():
        raise ClipsError(f"ファイルが見つかりません: {source_path}")
    text = source_path.read_text(encoding="utf-8")
    return save_candidates_file(video_id, text, settings)
