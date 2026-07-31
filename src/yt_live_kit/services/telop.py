"""選択区間からテロップ台本とメタデータを一括生成する."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import (
    TelopLine,
    TelopScriptDocument,
    TelopSegmentScript,
)
from yt_live_kit.services.ai_prompt import (
    AiPromptError,
    CodexNotFoundError,
    invoke_codex,
    is_codex_available,
)
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.subtitle_burn import (
    filter_cues_for_segment,
    parse_vtt_with_end,
)

logger = logging.getLogger(__name__)

PLACEHOLDER = "{{segment_transcripts}}"
TEMPLATE_NAME = "telop_script.md"

CODEX_INSTALL_HINT = """\
Codex CLI が見つかりません。テロップ台本の自動生成には Codex CLI が必要です。

【インストール手順】
1. Node.js がインストールされていることを確認してください
2. 次のコマンドで Codex CLI をインストールします:
   npm install -g @openai/codex
3. インストール後、認証を行います:
   codex login

【フォールバック（Cursor 手動運用）】
1. 次のプロンプトファイルを Cursor のチャットに貼り付けてください:
   {prompt_path}
2. 生成された JSON を次のパスに保存してください:
   {script_path}
3. 保存前に内容と時刻が元動画に合っているか確認してください。
"""

ProgressCallback = Callable[[str, str], None] | None


class TelopError(AiPromptError):
    """テロップ台本生成エラー."""


class TelopValidationError(TelopError):
    """テロップ台本の検証失敗."""

    def __init__(self, message: str, errors: tuple[str, ...]) -> None:
        super().__init__(message)
        self.errors = errors


@dataclass(frozen=True)
class TelopValidationResult:
    """テロップ台本の検証結果."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    document: TelopScriptDocument | None = None


@dataclass(frozen=True)
class TelopScriptResult:
    """テロップ台本生成結果."""

    video_id: str
    clip_id: str
    prompt_path: Path
    script_path: Path | None
    used_codex: bool
    document: TelopScriptDocument | None


def _to_milliseconds(value: float | int) -> int:
    """秒を十進表現の四捨五入で整数ミリ秒へ変換する."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TelopError("区間時刻は有限の数値で指定してください。") from exc
    if not decimal_value.is_finite():
        raise TelopError("区間時刻は有限の数値で指定してください。")
    if decimal_value < 0:
        raise TelopError("区間時刻に負の値は指定できません。")
    return int((decimal_value * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _segment_bounds(segment: HighlightSegment | tuple[float, float]) -> tuple[float, float]:
    if isinstance(segment, HighlightSegment):
        try:
            return (
                float(parse_timestamp_to_seconds(segment.start)),
                float(parse_timestamp_to_seconds(segment.end)),
            )
        except ValueError as exc:
            raise TelopError(f"ハイライト区間の時刻形式が正しくありません: {exc}") from None
    if not isinstance(segment, tuple) or len(segment) != 2:
        raise TelopError("区間はハイライト区間または開始秒と終了秒の組で指定してください。")
    try:
        return float(segment[0]), float(segment[1])
    except (TypeError, ValueError) as exc:
        raise TelopError("区間時刻は数値で指定してください。") from exc


def _normalized_bounds(
    segment: HighlightSegment | tuple[float, float],
) -> tuple[float, float, int, int]:
    start_sec, end_sec = _segment_bounds(segment)
    if not math.isfinite(start_sec) or not math.isfinite(end_sec):
        raise TelopError("区間時刻は有限の数値で指定してください。")
    start_ms = _to_milliseconds(start_sec)
    end_ms = _to_milliseconds(end_sec)
    if start_ms < 0 or end_ms < 0:
        raise TelopError("区間時刻に負の値は指定できません。")
    if end_ms <= start_ms:
        raise TelopError("区間の終了時刻は開始時刻より後にしてください。")
    return start_sec, end_sec, start_ms, end_ms


def make_clip_id(
    segments: Sequence[HighlightSegment | tuple[float, float]],
) -> str:
    """入力順の区間境界から安定したクリップ ID を生成する."""
    if not segments:
        raise TelopError("区間を 1 件以上指定してください。")
    parts = []
    for segment in segments:
        _start, _end, start_ms, end_ms = _normalized_bounds(segment)
        parts.append(f"{start_ms}-{end_ms}")
    source = "|".join(parts).encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:12]


def find_project_root() -> Path:
    """テロップ用プロンプトを持つリポジトリルートを返す."""
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in (start, *start.parents):
            if (candidate / "prompts" / TEMPLATE_NAME).is_file():
                return candidate
    raise TelopError(
        f"プロンプトテンプレートが見つかりません: prompts/{TEMPLATE_NAME}。"
        "リポジトリルートで実行してください。"
    )


def load_telop_template(project_root: Path | None = None) -> str:
    """テロップ台本生成用テンプレートを読み込む."""
    root = project_root or find_project_root()
    path = root / "prompts" / TEMPLATE_NAME
    if not path.is_file():
        raise TelopError(f"プロンプトテンプレートが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


def _format_timestamp(seconds: float) -> str:
    total_ms = _to_milliseconds(seconds)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def _build_segment_transcripts(
    vtt_content: str,
    segments: Sequence[HighlightSegment],
) -> str:
    cues = parse_vtt_with_end(vtt_content)
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        start_sec, end_sec, _start_ms, _end_ms = _normalized_bounds(segment)
        relative_cues = filter_cues_for_segment(cues, start_sec, end_sec)
        lines = [
            f"## 区間 {index} [{_format_timestamp(start_sec)} --> {_format_timestamp(end_sec)}]"
        ]
        for cue in relative_cues:
            absolute_start = cue.start_seconds + start_sec
            absolute_end = cue.end_seconds + start_sec
            lines.append(
                f"[{_format_timestamp(absolute_start)} --> "
                f"{_format_timestamp(absolute_end)}] {cue.text}"
            )
        if not relative_cues:
            raise TelopError(
                f"区間 {index} に字幕がありません。"
                "区間を字幕のある範囲へ変更してください。"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_telop_prompt(
    vtt_content: str,
    segments: Sequence[HighlightSegment],
    project_root: Path | None = None,
) -> str:
    """選択区間の絶対時刻付き字幕を埋め込んだプロンプトを返す."""
    if not segments:
        raise TelopError("区間を 1 件以上指定してください。")
    template = load_telop_template(project_root)
    if PLACEHOLDER not in template:
        raise TelopError(f"テンプレートにプレースホルダ {PLACEHOLDER} がありません。")
    transcripts = _build_segment_transcripts(vtt_content, segments)
    return template.replace(PLACEHOLDER, transcripts)


def _extract_json_object(text: str) -> dict:
    """コードフェンスや前後説明を含む応答から JSON オブジェクトを抽出する."""
    stripped = text.strip()
    if not stripped:
        raise TelopValidationError(
            "テロップ台本の JSON を解析できませんでした。",
            ("応答が空です。",),
        )

    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            data, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    raise TelopValidationError(
        "テロップ台本の JSON を解析できませんでした。",
        ("JSON オブジェクトの形式が不正です。",),
    )


def _has_halfwidth_angle(text: str) -> bool:
    return "<" in text or ">" in text


def validate_telop_script(
    doc: dict | TelopScriptDocument,
    *,
    segments: Sequence[HighlightSegment],
) -> TelopValidationResult:
    """台本のスキーマ、区間境界、文字列、行配置を検証する."""
    if not segments:
        return TelopValidationResult(
            ok=False,
            errors=("比較対象の区間を 1 件以上指定してください。",),
            warnings=(),
        )
    try:
        data = (
            doc
            if isinstance(doc, TelopScriptDocument)
            else TelopScriptDocument.model_validate(doc)
        )
    except Exception as exc:
        logger.warning("テロップ台本 JSON のスキーマ検証に失敗しました: %s", exc)
        return TelopValidationResult(
            ok=False,
            errors=(
                "テロップ台本の JSON 形式が想定と異なります。"
                "必須項目の不足か、値の型が誤っています。",
            ),
            warnings=(),
        )

    errors: list[str] = []
    warnings: list[str] = []

    def clean_required(value: str, label: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            errors.append(f"{label}を空にできません。")
        if _has_halfwidth_angle(cleaned):
            errors.append(f"{label}に半角の山カッコは使えません。")
        return cleaned

    hook_text = clean_required(data.hook_text, "フック文言")
    description = clean_required(data.description, "説明文")

    if not data.title_candidates:
        errors.append("タイトル案は 1 件以上必要です。")
    titles = [
        clean_required(value, f"タイトル案 {index}")
        for index, value in enumerate(data.title_candidates, start=1)
    ]
    if not data.tags:
        errors.append("タグは 1 件以上必要です。")
    tags = [
        clean_required(value, f"タグ {index}")
        for index, value in enumerate(data.tags, start=1)
    ]

    if len(data.segments) != len(segments):
        errors.append(
            f"台本の区間数が入力と一致しません（入力 {len(segments)} 件、"
            f"台本 {len(data.segments)} 件）。"
        )

    normalized_segments: list[TelopSegmentScript] = []
    for index, script_segment in enumerate(data.segments, start=1):
        prefix = f"区間 {index}"
        if index > len(segments):
            continue
        try:
            _input_start, _input_end, input_start_ms, input_end_ms = _normalized_bounds(
                segments[index - 1]
            )
            script_start_ms = _to_milliseconds(script_segment.start_sec)
            script_end_ms = _to_milliseconds(script_segment.end_sec)
        except TelopError as exc:
            errors.append(f"{prefix}: {exc}")
            continue

        if script_start_ms != input_start_ms or script_end_ms != input_end_ms:
            errors.append(f"{prefix}: 開始・終了時刻が入力区間と一致しません。")
        if not script_segment.lines:
            errors.append(f"{prefix}: テロップ行は 1 件以上必要です。")

        normalized_lines: list[TelopLine] = []
        previous_start_ms: int | None = None
        previous_end_ms: int | None = None
        for line_index, line in enumerate(script_segment.lines, start=1):
            line_prefix = f"{prefix} の行 {line_index}"
            text = clean_required(line.text, f"{line_prefix} の本文")
            try:
                line_start_ms = _to_milliseconds(line.start_sec)
                line_end_ms = _to_milliseconds(line.end_sec)
            except TelopError as exc:
                errors.append(f"{line_prefix}: {exc}")
                continue

            if line_end_ms <= line_start_ms:
                errors.append(f"{line_prefix}: 終了時刻は開始時刻より後にしてください。")
            if line_start_ms < input_start_ms or line_end_ms > input_end_ms:
                errors.append(f"{line_prefix}: 時刻が対応する入力区間の範囲外です。")
            if previous_start_ms is not None and line_start_ms < previous_start_ms:
                errors.append(f"{line_prefix}: 行を時系列順に並べてください。")
            if previous_end_ms is not None and line_start_ms < previous_end_ms:
                errors.append(f"{line_prefix}: 前の行と時刻が重複しています。")
            if len(text) > 16:
                warnings.append(f"{line_prefix}: 本文が 16 文字を超えています。")

            previous_start_ms = line_start_ms
            previous_end_ms = line_end_ms
            normalized_lines.append(
                TelopLine(
                    text=text,
                    start_sec=line_start_ms / 1000,
                    end_sec=line_end_ms / 1000,
                    emphasis=line.emphasis,
                )
            )

        normalized_segments.append(
            TelopSegmentScript(
                start_sec=script_start_ms / 1000,
                end_sec=script_end_ms / 1000,
                lines=normalized_lines,
            )
        )

    if errors:
        return TelopValidationResult(
            ok=False,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    normalized = TelopScriptDocument(
        hook_text=hook_text,
        title_candidates=titles,
        description=description,
        tags=tags,
        segments=normalized_segments,
    )
    return TelopValidationResult(
        ok=True,
        errors=(),
        warnings=tuple(warnings),
        document=normalized,
    )


def _write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _save_prompt(video_id: str, clip_id: str, prompt: str, settings: Settings) -> Path:
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise TelopError(f"動画ディレクトリが見つかりません: {video_dir}")
    path = video_dir / "shorts" / "telop" / f"prompt_telop_{clip_id}.txt"
    _write_text_atomically(path, prompt)
    return path


def _save_document(
    video_id: str,
    clip_id: str,
    document: TelopScriptDocument,
    settings: Settings,
) -> Path:
    path = settings.data_dir / video_id / "shorts" / "telop" / f"telop_{clip_id}.json"
    _write_text_atomically(path, document.model_dump_json(indent=2))
    return path


def generate_telop_script(
    video_id: str,
    segments: Sequence[HighlightSegment],
    settings: Settings | None = None,
    *,
    on_progress: ProgressCallback = None,
    prompt_only: bool = False,
    codex_path: str = "codex",
) -> TelopScriptResult:
    """選択区間から台本とメタデータを 1 回の Codex 呼び出しで生成する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise TelopError(f"動画ディレクトリが見つかりません: {video_dir}")
    if not segments:
        raise TelopError("区間を 1 件以上指定してください。")

    vtt_path = video_dir / "subtitles" / "ja.vtt"
    if not vtt_path.is_file():
        raise TelopError(f"字幕ファイルが見つかりません: {vtt_path}")

    clip_id = make_clip_id(segments)
    prompt = build_telop_prompt(
        vtt_path.read_text(encoding="utf-8"),
        segments,
        find_project_root(),
    )
    prompt_path = _save_prompt(video_id, clip_id, prompt, settings)
    script_path = video_dir / "shorts" / "telop" / f"telop_{clip_id}.json"

    if prompt_only:
        return TelopScriptResult(
            video_id=video_id,
            clip_id=clip_id,
            prompt_path=prompt_path,
            script_path=None,
            used_codex=False,
            document=None,
        )

    if not is_codex_available(codex_path):
        raise CodexNotFoundError(
            CODEX_INSTALL_HINT.format(
                prompt_path=prompt_path,
                script_path=script_path,
            )
        )

    if on_progress is not None:
        on_progress("telop", "テロップ台本とメタデータを生成しています…")

    raw_output = invoke_codex(prompt, codex_path=codex_path)
    extracted = _extract_json_object(raw_output)
    validation = validate_telop_script(extracted, segments=segments)
    if not validation.ok or validation.document is None:
        detail = "\n".join(f"- {error}" for error in validation.errors)
        raise TelopValidationError(
            f"テロップ台本の形式が正しくありません:\n{detail}",
            validation.errors,
        )

    saved_path = _save_document(video_id, clip_id, validation.document, settings)
    return TelopScriptResult(
        video_id=video_id,
        clip_id=clip_id,
        prompt_path=prompt_path,
        script_path=saved_path,
        used_codex=True,
        document=validation.document,
    )
