"""ハイライト区間の提案・保存."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.highlights import HighlightSegment, HighlightsDocument
from yt_live_kit.services.ai_prompt import (
    AiPromptError,
    CodexNotFoundError,
    invoke_codex,
    is_codex_available,
)
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds

logger = logging.getLogger(__name__)

PLACEHOLDER = "{{compressed_transcript}}"
TEMPLATE_NAME = "highlights.md"
MIN_SEGMENTS = 2
MAX_SEGMENTS = 20
MIN_DURATION_SEC = 10
MAX_DURATION_SEC = 300

CODEX_INSTALL_HINT = """\
Codex CLI が見つかりません。ハイライト区間の自動生成には Codex CLI が必要です。

【インストール手順】
1. Node.js がインストールされていることを確認してください
2. 次のコマンドで Codex CLI をインストールします:
   npm install -g @openai/codex
3. インストール後、認証を行います:
   codex login

【フォールバック（Cursor 手動運用）】
1. 次のプロンプトファイルを Cursor のチャットに貼り付けてください:
   {prompt_path}
2. 生成された JSON を {segments_path} に保存してください
3. 保存後、再度この画面から実行すると検証されます。
   （CLI からの検証・保存は今後追加予定です）
"""

ProgressCallback = Callable[[str, str], None] | None


class HighlightsError(AiPromptError):
    """ハイライト区間生成エラー."""


class HighlightValidationError(HighlightsError):
    """区間バリデーション失敗."""

    def __init__(self, message: str, errors: tuple[str, ...]) -> None:
        super().__init__(message)
        self.errors = errors


@dataclass(frozen=True)
class HighlightValidationResult:
    """ハイライト区間バリデーション結果（正規化済み区間つき）."""

    ok: bool
    errors: tuple[str, ...]
    segments: tuple[HighlightSegment, ...] = ()


@dataclass(frozen=True)
class HighlightsResult:
    """ハイライト区間生成結果."""

    video_id: str
    prompt_path: Path
    segments_path: Path | None
    used_codex: bool
    segments: tuple[HighlightSegment, ...]


def find_project_root() -> Path:
    """リポジトリルート（prompts/highlights.md があるディレクトリ）を返す."""
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in [start, *start.parents]:
            if (candidate / "prompts" / TEMPLATE_NAME).is_file():
                return candidate
    raise HighlightsError(
        f"プロンプトテンプレートが見つかりません: prompts/{TEMPLATE_NAME}。"
        "リポジトリルートで実行してください。"
    )


def load_highlights_template(project_root: Path | None = None) -> str:
    """ハイライト区間生成用テンプレートを読み込む."""
    root = project_root or find_project_root()
    template_path = root / "prompts" / TEMPLATE_NAME
    if not template_path.is_file():
        raise HighlightsError(f"プロンプトテンプレートが見つかりません: {template_path}")
    return template_path.read_text(encoding="utf-8")


def build_highlights_prompt(compressed_text: str, project_root: Path | None = None) -> str:
    """圧縮テキストを埋め込んだ完成プロンプトを返す."""
    template = load_highlights_template(project_root)
    if PLACEHOLDER not in template:
        raise HighlightsError(f"テンプレートにプレースホルダ {PLACEHOLDER} がありません。")
    return template.replace(PLACEHOLDER, compressed_text.strip())


def _read_compressed_transcript(video_dir: Path) -> str:
    compressed_path = video_dir / "transcript" / "compressed.txt"
    if not compressed_path.is_file():
        raise HighlightsError(
            f"圧縮版テキストが見つかりません: {compressed_path}。"
            "先に transcript を実行してください。"
        )
    return compressed_path.read_text(encoding="utf-8")


def save_highlights_prompt_file(video_id: str, prompt: str, settings: Settings) -> Path:
    """完成プロンプトを data/{video_id}/prompt_highlights.txt に保存する."""
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise HighlightsError(f"動画ディレクトリが見つかりません: {video_dir}")
    prompt_path = video_dir / "prompt_highlights.txt"
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
            raise HighlightValidationError(
                "ハイライト区間の JSON を解析できませんでした。",
                ("JSON 形式が不正です。",),
            ) from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise HighlightValidationError(
                "ハイライト区間の JSON を解析できませんでした。",
                (str(exc),),
            ) from exc

    if not isinstance(data, dict):
        raise HighlightValidationError(
            "ハイライト区間の JSON を解析できませんでした。",
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


def validate_highlights(
    segments: dict | HighlightsDocument,
    *,
    video_duration: int | None = None,
) -> HighlightValidationResult:
    """ハイライト区間 JSON を検証する."""
    errors: list[str] = []

    if isinstance(segments, HighlightsDocument):
        data = segments
    else:
        try:
            data = HighlightsDocument.model_validate(segments)
        except Exception as exc:
            logger.warning("ハイライト区間 JSON のスキーマ検証に失敗しました: %s", exc)
            return HighlightValidationResult(
                ok=False,
                errors=(
                    "ハイライト区間の JSON 形式が想定と異なります。"
                    "必須項目の不足か、値の型が誤っています。",
                ),
                segments=(),
            )

    if len(data.candidates) < MIN_SEGMENTS:
        errors.append(
            f"ハイライト区間は {MIN_SEGMENTS} 個以上必要です（現在 {len(data.candidates)} 個）。"
        )
    if len(data.candidates) > MAX_SEGMENTS:
        errors.append(
            f"ハイライト区間は {MAX_SEGMENTS} 個以下である必要があります"
            f"（現在 {len(data.candidates)} 個）。"
        )

    normalized: list[tuple[HighlightSegment, int, int]] = []
    for i, segment in enumerate(data.candidates):
        prefix = f"区間 {i + 1} ({segment.id})"

        if "<" in segment.title or ">" in segment.title:
            errors.append(f"{prefix}: タイトルに半角の 〈 〉 は使えません。")
        if "<" in segment.reason or ">" in segment.reason:
            errors.append(f"{prefix}: 理由に半角の 〈 〉 は使えません。")

        try:
            start_sec = parse_timestamp_to_seconds(segment.start)
            end_sec = parse_timestamp_to_seconds(segment.end)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
            continue

        if end_sec <= start_sec:
            errors.append(f"{prefix}: 終了時刻は開始時刻より後である必要があります。")
            continue

        if video_duration is not None and end_sec > video_duration:
            errors.append(
                f"{prefix}: 終了時刻 ({segment.end}) が動画長"
                f" ({video_duration // 60}:{video_duration % 60:02d}) を超えています。"
            )

        duration = end_sec - start_sec
        if duration < MIN_DURATION_SEC:
            errors.append(
                f"{prefix}: 区間長が {MIN_DURATION_SEC} 秒未満です（{duration} 秒）。"
            )
        if duration > MAX_DURATION_SEC:
            errors.append(
                f"{prefix}: 区間長が {MAX_DURATION_SEC // 60} 分を超えています"
                f"（{duration // 60} 分 {duration % 60} 秒）。"
            )

        if segment.duration_sec != duration:
            errors.append(
                f"{prefix}: duration_sec ({segment.duration_sec}) が"
                f" start/end から計算した値 ({duration}) と一致しません。"
            )

        normalized.append(
            (
                HighlightSegment(
                    id=segment.id,
                    title=segment.title.strip(),
                    start=_normalize_timestamp(segment.start),
                    end=_normalize_timestamp(segment.end),
                    duration_sec=duration,
                    reason=segment.reason.strip(),
                ),
                start_sec,
                end_sec,
            )
        )

    # 時系列順チェック（隣接比較）
    for i in range(1, len(normalized)):
        seg_i, start_i, _end_i = normalized[i]
        _, prev_start, _prev_end = normalized[i - 1]
        if start_i < prev_start:
            errors.append(
                f"区間 {seg_i.id} は前の区間より前の開始時刻になっています"
                f"（時系列順に並べてください）。"
            )

    # 重複チェック（全ペア）
    for i in range(len(normalized)):
        seg_i, start_i, end_i = normalized[i]
        for j in range(i + 1, len(normalized)):
            seg_j, start_j, end_j = normalized[j]
            if start_i < end_j and start_j < end_i:
                errors.append(
                    f"区間 {seg_i.id} と {seg_j.id} の区間が重複しています。"
                )

    ok = len(errors) == 0
    normalized_segments = tuple(seg for seg, _start, _end in normalized)
    return HighlightValidationResult(
        ok=ok, errors=tuple(errors), segments=normalized_segments
    )


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


def save_segments_file(
    video_id: str,
    raw_json_text: str,
    settings: Settings,
) -> tuple[Path, HighlightsDocument]:
    """バリデーション通過後に highlights/segments.json に保存する."""
    data = _extract_json_object(raw_json_text)
    video_duration = _load_video_duration(video_id, settings)
    validation = validate_highlights(data, video_duration=video_duration)
    if not validation.ok:
        detail = "\n".join(f"- {err}" for err in validation.errors)
        raise HighlightValidationError(
            f"ハイライト区間の形式が正しくありません:\n{detail}",
            validation.errors,
        )

    doc = HighlightsDocument(candidates=list(validation.segments))
    video_dir = settings.data_dir / video_id
    highlights_dir = video_dir / "highlights"
    highlights_dir.mkdir(parents=True, exist_ok=True)
    segments_path = highlights_dir / "segments.json"
    segments_path.write_text(
        doc.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return segments_path, doc


def load_segments_file(video_id: str, settings: Settings) -> HighlightsDocument | None:
    """保存済み segments.json を読み込む."""
    path = settings.data_dir / video_id / "highlights" / "segments.json"
    if not path.is_file():
        return None
    return HighlightsDocument.model_validate_json(path.read_text(encoding="utf-8"))


def suggest_highlights(
    video_id: str,
    settings: Settings | None = None,
    *,
    on_progress: ProgressCallback = None,
    prompt_only: bool = False,
    codex_path: str = "codex",
) -> HighlightsResult:
    """圧縮テキストからハイライト区間を生成して保存する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise HighlightsError(f"動画ディレクトリが見つかりません: {video_dir}")

    compressed_text = _read_compressed_transcript(video_dir)
    project_root = find_project_root()
    prompt = build_highlights_prompt(compressed_text, project_root)
    prompt_path = save_highlights_prompt_file(video_id, prompt, settings)

    if prompt_only:
        return HighlightsResult(
            video_id=video_id,
            prompt_path=prompt_path,
            segments_path=None,
            used_codex=False,
            segments=(),
        )

    if not is_codex_available(codex_path):
        segments_path = video_dir / "highlights" / "segments.json"
        hint = CODEX_INSTALL_HINT.format(
            prompt_path=prompt_path,
            segments_path=segments_path,
            video_id=video_id,
        )
        raise CodexNotFoundError(hint)

    if on_progress is not None:
        on_progress("highlights", "ハイライト区間を生成しています…")

    raw_output = invoke_codex(prompt, codex_path=codex_path)
    segments_path, doc = save_segments_file(video_id, raw_output, settings)

    return HighlightsResult(
        video_id=video_id,
        prompt_path=prompt_path,
        segments_path=segments_path,
        used_codex=True,
        segments=tuple(doc.candidates),
    )


def save_segments_from_file(
    video_id: str,
    source_path: Path,
    settings: Settings | None = None,
) -> tuple[Path, HighlightsDocument]:
    """外部ファイル（Cursor 手動生成等）から区間を検証・保存する."""
    settings = settings or get_settings()
    if not source_path.is_file():
        raise HighlightsError(f"ファイルが見つかりません: {source_path}")
    text = source_path.read_text(encoding="utf-8")
    return save_segments_file(video_id, text, settings)
