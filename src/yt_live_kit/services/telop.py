"""選択区間からテロップ台本とメタデータを一括生成する."""

from __future__ import annotations

import hashlib
import json
import logging
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
from yt_live_kit.services._fsutil import write_text_atomically
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
TITLE_DIRECTIONS = ("検索明快型", "仕事影響型", "好奇心型")
TITLE_MAX_LENGTH = 100
TITLE_RECOMMENDED_MIN_LENGTH = 18
TITLE_RECOMMENDED_MAX_LENGTH = 32

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


@dataclass(frozen=True)
class ConfirmedTelopScriptResult:
    """人が修正・確定した正規化済みテロップ台本."""

    path: Path
    document: TelopScriptDocument


@dataclass(frozen=True)
class NormalizedSegmentBounds:
    """整数ミリ秒へ正規化した区間境界."""

    start_ms: int
    end_ms: int

    @property
    def start_sec(self) -> float:
        return self.start_ms / 1000

    @property
    def end_sec(self) -> float:
        return self.end_ms / 1000

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def duration_sec(self) -> float:
        return self.duration_ms / 1000


def normalize_seconds_to_milliseconds(value: float | int) -> int:
    """秒を十進表現の四捨五入で整数ミリ秒へ変換する."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelopError("区間時刻は数値で指定してください。")
    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite():
            raise TelopError("区間時刻は有限の数値で指定してください。")
        if decimal_value < 0:
            raise TelopError("区間時刻に負の値は指定できません。")
        return int(
            (decimal_value * 1000).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    except TelopError:
        raise
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise TelopError(
            "区間時刻が大きすぎるため、整数ミリ秒へ変換できません。"
        ) from exc


# S1 までの内部参照との互換性を維持する。
_to_milliseconds = normalize_seconds_to_milliseconds


def _segment_bounds(
    segment: HighlightSegment | tuple[float, float]
) -> tuple[float, float]:
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
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in segment
    ):
        raise TelopError("区間時刻は数値で指定してください。")
    return segment[0], segment[1]


def _normalized_bounds(
    segment: HighlightSegment | tuple[float, float],
) -> tuple[float, float, int, int]:
    start_sec, end_sec = _segment_bounds(segment)
    bounds = normalize_segment_bounds([segment])[0]
    return start_sec, end_sec, bounds.start_ms, bounds.end_ms


def normalize_segment_bounds(
    segments: Sequence[HighlightSegment | tuple[float, float]],
) -> tuple[NormalizedSegmentBounds, ...]:
    """区間列を入力順の整数ミリ秒境界へ正規化する."""
    if not segments:
        raise TelopError("区間を 1 件以上指定してください。")

    normalized: list[NormalizedSegmentBounds] = []
    for segment in segments:
        start_sec, end_sec = _segment_bounds(segment)
        start_ms = normalize_seconds_to_milliseconds(start_sec)
        end_ms = normalize_seconds_to_milliseconds(end_sec)
        if end_ms <= start_ms:
            raise TelopError("区間の終了時刻は開始時刻より後にしてください。")
        normalized.append(NormalizedSegmentBounds(start_ms=start_ms, end_ms=end_ms))
    return tuple(normalized)


def make_clip_id(
    segments: Sequence[HighlightSegment | tuple[float, float]],
) -> str:
    """入力順の区間境界から安定したクリップ ID を生成する."""
    parts = [
        f"{bounds.start_ms}-{bounds.end_ms}"
        for bounds in normalize_segment_bounds(segments)
    ]
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


def _title_direction(index: int) -> str:
    if 1 <= index <= len(TITLE_DIRECTIONS):
        return TITLE_DIRECTIONS[index - 1]
    return f"タイトル案 {index}"


def _title_label(index: int, *, require_three_title_candidates: bool) -> str:
    if require_three_title_candidates:
        return f"{_title_direction(index)}（タイトル案 {index}）"
    return f"タイトル案 {index}"


def validate_telop_script(
    doc: dict | TelopScriptDocument,
    *,
    segments: Sequence[HighlightSegment | tuple[float, float]],
    require_three_title_candidates: bool = False,
) -> TelopValidationResult:
    """台本のスキーマ、区間境界、文字列、行配置を検証する.

    ``require_three_title_candidates`` は Codex による新規生成の境界だけで
    有効にする。保存済みの台本を読み込み・編集する既存経路は、1〜2 件の
    タイトル候補を受け入れて後方互換を保つ。
    """
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
        direction_hint = ""
        if require_three_title_candidates:
            direction_hint = (
                "新規生成では検索明快型・仕事影響型・好奇心型のタイトル案を"
                "固定順で 3 件返してください。"
            )
        return TelopValidationResult(
            ok=False,
            errors=(
                "テロップ台本の JSON 形式が想定と異なります。"
                "必須項目の不足か、値の型が誤っています。"
                + direction_hint,
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
        if require_three_title_candidates:
            errors.append(
                "新規生成のタイトル案は、検索明快型・仕事影響型・好奇心型を"
                "固定順でちょうど 3 件必要です。"
            )
        else:
            errors.append("タイトル案は 1 件以上必要です。")
    elif require_three_title_candidates and len(data.title_candidates) != len(
        TITLE_DIRECTIONS
    ):
        errors.append(
            "新規生成のタイトル案は、検索明快型・仕事影響型・好奇心型を"
            f"固定順でちょうど {len(TITLE_DIRECTIONS)} 件必要です。"
            f"（現在 {len(data.title_candidates)} 件）"
        )
    titles = [
        clean_required(
            value,
            _title_label(
                index,
                require_three_title_candidates=require_three_title_candidates,
            ),
        )
        for index, value in enumerate(data.title_candidates, start=1)
    ]
    if require_three_title_candidates:
        seen_titles: dict[str, int] = {}
        for index, title in enumerate(titles, start=1):
            if not title:
                continue
            previous_index = seen_titles.get(title)
            if previous_index is not None:
                previous_direction = _title_direction(previous_index)
                current_direction = _title_direction(index)
                errors.append(
                    f"{previous_direction}と{current_direction}のタイトル案が"
                    "重複しています。"
                )
            else:
                seen_titles[title] = index
    for index, title in enumerate(titles, start=1):
        label = _title_label(
            index,
            require_three_title_candidates=require_three_title_candidates,
        )
        if not title:
            continue
        if require_three_title_candidates and len(title) > TITLE_MAX_LENGTH:
            errors.append(f"{label}は {TITLE_MAX_LENGTH} 文字以下にしてください。")
        elif (
            len(title) < TITLE_RECOMMENDED_MIN_LENGTH
            or len(title) > TITLE_RECOMMENDED_MAX_LENGTH
        ):
            warnings.append(
                f"{label}は日本語 {TITLE_RECOMMENDED_MIN_LENGTH}〜"
                f"{TITLE_RECOMMENDED_MAX_LENGTH} 文字を目安にしてください。"
            )
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
            input_bounds = normalize_segment_bounds([segments[index - 1]])[0]
            input_start_ms = input_bounds.start_ms
            input_end_ms = input_bounds.end_ms
            script_start_ms = normalize_seconds_to_milliseconds(
                script_segment.start_sec
            )
            script_end_ms = normalize_seconds_to_milliseconds(script_segment.end_sec)
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
                line_start_ms = normalize_seconds_to_milliseconds(line.start_sec)
                line_end_ms = normalize_seconds_to_milliseconds(line.end_sec)
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


def _save_prompt(video_id: str, clip_id: str, prompt: str, settings: Settings) -> Path:
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise TelopError(f"動画ディレクトリが見つかりません: {video_dir}")
    path = video_dir / "shorts" / "telop" / f"prompt_telop_{clip_id}.txt"
    write_text_atomically(path, prompt)
    return path


def _save_document(
    video_id: str,
    clip_id: str,
    document: TelopScriptDocument,
    settings: Settings,
) -> Path:
    path = settings.data_dir / video_id / "shorts" / "telop" / f"telop_{clip_id}.json"
    write_text_atomically(path, document.model_dump_json(indent=2))
    return path


def save_confirmed_telop_script(
    video_id: str,
    segments: Sequence[HighlightSegment | tuple[float, float]],
    document: dict | TelopScriptDocument,
    settings: Settings | None = None,
) -> ConfirmedTelopScriptResult:
    """人が修正した台本を再検証し、正規化済み document だけを保存する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise TelopError(f"動画ディレクトリが見つかりません: {video_dir}")
    validation = validate_telop_script(document, segments=segments)
    if not validation.ok or validation.document is None:
        detail = "\n".join(f"- {error}" for error in validation.errors)
        raise TelopValidationError(
            f"テロップ台本の形式が正しくありません:\n{detail}",
            validation.errors,
        )
    clip_id = make_clip_id(segments)
    try:
        path = _save_document(video_id, clip_id, validation.document, settings)
    except OSError as exc:
        raise TelopError(
            "確定済みテロップ台本を保存できませんでした。"
            "保存先の権限と空き容量を確認してください。"
        ) from exc
    return ConfirmedTelopScriptResult(path=path, document=validation.document)


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
    validation = validate_telop_script(
        extracted,
        segments=segments,
        require_three_title_candidates=True,
    )
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
