"""切り抜き候補の内部から、ショート 1 本分のサブ区間を提案する（FR-30）.

親候補（10〜15 分の切り抜き候補など）の区間内の字幕だけを Codex CLI へ渡し、
合計 180 秒以内に収まる小区間を提案させる。提案の検証は純粋関数として閉じ、
確定した区間列は FR-25 の `build_short_from_segments()` へそのまま渡す。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.short_cut import ShortCutDocument
from yt_live_kit.models.transcript import TranscriptArtifact, TranscriptArtifactRef
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services.ai_prompt import (
    AiPromptError,
    CodexNotFoundError,
    invoke_codex,
    is_codex_available,
)
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.shorts import MAX_DURATION_SEC, MIN_DURATION_SEC
from yt_live_kit.services.subtitle_burn import (
    filter_cues_for_segment,
    parse_vtt_with_end,
)
from yt_live_kit.services.telop import TelopError, normalize_segment_bounds
from yt_live_kit.services.transcript_artifact import (
    TranscriptArtifactError,
    TranscriptArtifactStore,
    stored_artifact_lineage_is_current,
)
from yt_live_kit.services.whisper_runtime import run_selected_ranges

logger = logging.getLogger(__name__)

PLACEHOLDER = "{{segment_transcript}}"
TEMPLATE_NAME = "short_cut.md"

MIN_CUTS = 2
MAX_CUTS = 5
MIN_CUT_DURATION_SEC = 5
MAX_CUT_DURATION_SEC = 120

# 合計尺の下限・上限は FR-25 と同じ shorts.py の定数を唯一の正本として参照する。
MIN_TOTAL_MS = int(MIN_DURATION_SEC * 1000)
MAX_TOTAL_MS = int(MAX_DURATION_SEC * 1000)

CODEX_INSTALL_HINT = """\
Codex CLI が見つかりません。サブ区間の自動提案には Codex CLI が必要です。

【インストール手順】
1. Node.js がインストールされていることを確認してください
2. 次のコマンドで Codex CLI をインストールします:
   npm install -g @openai/codex
3. インストール後、認証を行います:
   codex login

【フォールバック（Cursor 手動運用）】
1. 次のプロンプトファイルを Cursor のチャットに貼り付けてください:
   {prompt_path}
2. 生成された JSON を {cut_plan_path} に保存してください
3. 保存後、再度この画面を開くと検証されます
"""

ProgressCallback = Callable[[str, str], None] | None

ParentCandidate = ClipCandidate | HighlightSegment


class ShortCutError(AiPromptError):
    """サブ区間提案エラー."""


class ShortCutValidationError(ShortCutError):
    """サブ区間バリデーション失敗."""

    def __init__(self, message: str, errors: tuple[str, ...]) -> None:
        super().__init__(message)
        self.errors = errors


@dataclass(frozen=True)
class ShortCutValidationResult:
    """サブ区間バリデーション結果（正規化済み区間つき）."""

    ok: bool
    errors: tuple[str, ...]
    segments: tuple[HighlightSegment, ...] = ()
    total_ms: int = 0


@dataclass(frozen=True)
class ShortCutResult:
    """サブ区間提案の生成結果."""

    video_id: str
    parent_id: str
    prompt_path: Path
    cut_plan_path: Path | None
    used_codex: bool
    document: ShortCutDocument | None
    artifact_ref: TranscriptArtifactRef | None = None
    artifact_fingerprint: str | None = None
    used_range_cue_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefinedShortCutResult:
    """選択区間だけを高精度化した cutplan と immutable provenance."""

    video_id: str
    parent_id: str
    job_id: str
    artifact: TranscriptArtifact
    artifact_ref: TranscriptArtifactRef
    artifact_fingerprint: str
    used_range_cue_digests: tuple[str, ...]
    document: ShortCutDocument


def find_project_root() -> Path:
    """リポジトリルート（prompts/short_cut.md があるディレクトリ）を返す."""
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in [start, *start.parents]:
            if (candidate / "prompts" / TEMPLATE_NAME).is_file():
                return candidate
    raise ShortCutError(
        f"プロンプトテンプレートが見つかりません: prompts/{TEMPLATE_NAME}。"
        "リポジトリルートで実行してください。"
    )


def load_short_cut_template(project_root: Path | None = None) -> str:
    """サブ区間提案用テンプレートを読み込む."""
    root = project_root or find_project_root()
    template_path = root / "prompts" / TEMPLATE_NAME
    if not template_path.is_file():
        raise ShortCutError(f"プロンプトテンプレートが見つかりません: {template_path}")
    return template_path.read_text(encoding="utf-8")


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parent_bounds_ms(parent: ParentCandidate) -> tuple[int, int]:
    """親候補の区間を FR-25 と同じ整数ミリ秒基準へ正規化する."""
    segment = _as_highlight(parent)
    try:
        bounds = normalize_segment_bounds([segment])[0]
    except TelopError as exc:
        raise ShortCutError(str(exc)) from exc
    return bounds.start_ms, bounds.end_ms


def _as_highlight(parent: ParentCandidate) -> HighlightSegment:
    if isinstance(parent, HighlightSegment):
        return parent
    if isinstance(parent, ClipCandidate):
        return HighlightSegment.model_validate(parent.model_dump(mode="json"))
    raise ShortCutError("親候補は切り抜き候補またはハイライト区間で指定してください。")


def needs_short_cut(parent: ParentCandidate) -> bool:
    """親候補がそのままではショートにできない（180 秒超）かどうかを返す."""
    start_ms, end_ms = parent_bounds_ms(parent)
    return end_ms - start_ms > MAX_TOTAL_MS


def build_parent_transcript(vtt_content: str, parent: ParentCandidate) -> str:
    """親区間の字幕を絶対時刻付きで整形する."""
    start_ms, end_ms = parent_bounds_ms(parent)
    start_sec = start_ms / 1000
    end_sec = end_ms / 1000
    cues = filter_cues_for_segment(parse_vtt_with_end(vtt_content), start_sec, end_sec)
    if not cues:
        raise ShortCutError(
            "対象区間に字幕がありません。字幕のある候補を選んでください。"
        )
    lines = [f"## 対象区間 [{_format_timestamp(start_sec)} --> {_format_timestamp(end_sec)}]"]
    for cue in cues:
        lines.append(
            f"[{_format_timestamp(cue.start_seconds + start_sec)} --> "
            f"{_format_timestamp(cue.end_seconds + start_sec)}] {cue.text}"
        )
    return "\n".join(lines)


def build_short_cut_prompt(
    vtt_content: str,
    parent: ParentCandidate,
    project_root: Path | None = None,
) -> str:
    """親区間の字幕を埋め込んだ完成プロンプトを返す."""
    template = load_short_cut_template(project_root)
    if PLACEHOLDER not in template:
        raise ShortCutError(f"テンプレートにプレースホルダ {PLACEHOLDER} がありません。")
    return template.replace(PLACEHOLDER, build_parent_transcript(vtt_content, parent))


def _extract_json_object(text: str) -> dict:
    """AI 出力から JSON オブジェクトを抽出する."""
    stripped = text.strip()
    if stripped.startswith("```"):
        json_lines: list[str] = []
        in_block = False
        for line in stripped.splitlines():
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
            raise ShortCutValidationError(
                "サブ区間の JSON を解析できませんでした。",
                ("JSON 形式が不正です。",),
            ) from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ShortCutValidationError(
                "サブ区間の JSON を解析できませんでした。",
                (str(exc),),
            ) from exc

    if not isinstance(data, dict):
        raise ShortCutValidationError(
            "サブ区間の JSON を解析できませんでした。",
            ("ルートは JSON オブジェクトである必要があります。",),
        )
    return data


def _has_halfwidth_angle(text: str) -> bool:
    return "<" in text or ">" in text


def _candidate_list(payload: dict | ShortCutDocument) -> list[HighlightSegment] | None:
    if isinstance(payload, ShortCutDocument):
        return list(payload.candidates)
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return None
    try:
        return [HighlightSegment.model_validate(item) for item in raw]
    except Exception as exc:  # pydantic ValidationError を含む
        logger.warning("サブ区間 JSON のスキーマ検証に失敗しました: %s", exc)
        return None


def validate_short_cut(
    payload: dict | ShortCutDocument,
    *,
    parent: ParentCandidate,
) -> ShortCutValidationResult:
    """AI が提案したサブ区間を親区間の制約つきで検証する（純粋関数）."""
    candidates = _candidate_list(payload)
    if candidates is None:
        return ShortCutValidationResult(
            ok=False,
            errors=(
                "サブ区間の JSON 形式が想定と異なります。"
                "必須項目の不足か、値の型が誤っています。",
            ),
        )
    return _validate_segments(
        candidates,
        parent=parent,
        min_cuts=MIN_CUTS,
        enforce_cut_duration=True,
    )


def validate_short_cut_selection(
    segments: Sequence[HighlightSegment],
    *,
    parent: ParentCandidate,
) -> ShortCutValidationResult:
    """人が採否・境界を調整した区間列を生成直前に再検証する（純粋関数）.

    個別区間の長さ制限は AI 提案時のガイドなので課さない。親区間への包含、
    時系列、非重複、合計尺（FR-25 と同じ 10〜180 秒）だけを境界として守る。
    """
    return _validate_segments(
        list(segments),
        parent=parent,
        min_cuts=1,
        enforce_cut_duration=False,
    )


def _validate_segments(
    candidates: list[HighlightSegment],
    *,
    parent: ParentCandidate,
    min_cuts: int,
    enforce_cut_duration: bool,
) -> ShortCutValidationResult:
    parent_start_ms, parent_end_ms = parent_bounds_ms(parent)

    errors: list[str] = []
    if len(candidates) < min_cuts:
        errors.append(
            f"サブ区間は {min_cuts} 個以上必要です（現在 {len(candidates)} 個）。"
        )
    if len(candidates) > MAX_CUTS:
        errors.append(
            f"サブ区間は {MAX_CUTS} 個以下である必要があります（現在 {len(candidates)} 個）。"
        )

    normalized: list[tuple[HighlightSegment, int, int]] = []
    for index, segment in enumerate(candidates):
        prefix = f"区間 {index + 1} ({segment.id})"

        if _has_halfwidth_angle(segment.title):
            errors.append(f"{prefix}: タイトルに半角の 〈 〉 は使えません。")
        if _has_halfwidth_angle(segment.reason):
            errors.append(f"{prefix}: 理由に半角の 〈 〉 は使えません。")

        try:
            start_sec = parse_timestamp_to_seconds(segment.start)
            end_sec = parse_timestamp_to_seconds(segment.end)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
            continue

        try:
            bounds = normalize_segment_bounds([(float(start_sec), float(end_sec))])[0]
        except TelopError as exc:
            errors.append(f"{prefix}: {exc}")
            continue

        if bounds.start_ms < parent_start_ms or bounds.end_ms > parent_end_ms:
            errors.append(
                f"{prefix}: 候補の区間"
                f"（{_format_timestamp(parent_start_ms / 1000)} 〜 "
                f"{_format_timestamp(parent_end_ms / 1000)}）の外に出ています。"
            )

        duration_sec = bounds.duration_ms / 1000
        if enforce_cut_duration:
            if duration_sec < MIN_CUT_DURATION_SEC:
                errors.append(
                    f"{prefix}: 区間長が {MIN_CUT_DURATION_SEC} 秒未満です"
                    f"（{duration_sec:.1f} 秒）。"
                )
            if duration_sec > MAX_CUT_DURATION_SEC:
                errors.append(
                    f"{prefix}: 区間長が {MAX_CUT_DURATION_SEC} 秒を超えています"
                    f"（{duration_sec:.1f} 秒）。"
                )

        expected_duration = int(end_sec - start_sec)
        if enforce_cut_duration and segment.duration_sec != expected_duration:
            errors.append(
                f"{prefix}: duration_sec ({segment.duration_sec}) が"
                f" start/end から計算した値 ({expected_duration}) と一致しません。"
            )

        normalized.append(
            (
                HighlightSegment(
                    id=segment.id,
                    title=segment.title.strip(),
                    start=_format_timestamp(bounds.start_ms / 1000),
                    end=_format_timestamp(bounds.end_ms / 1000),
                    duration_sec=expected_duration,
                    reason=segment.reason.strip(),
                ),
                bounds.start_ms,
                bounds.end_ms,
            )
        )

    for index in range(1, len(normalized)):
        segment, start_ms, _end_ms = normalized[index]
        _prev, prev_start_ms, _prev_end = normalized[index - 1]
        if start_ms < prev_start_ms:
            errors.append(
                f"区間 {segment.id} が前の区間より前から始まっています"
                "（時系列順に並べてください）。"
            )

    for index in range(len(normalized)):
        segment_i, start_i, end_i = normalized[index]
        for other in range(index + 1, len(normalized)):
            segment_j, start_j, end_j = normalized[other]
            if start_i < end_j and start_j < end_i:
                errors.append(
                    f"区間 {segment_i.id} と {segment_j.id} の区間が重複しています。"
                )

    total_ms = sum(end_ms - start_ms for _segment, start_ms, end_ms in normalized)
    if normalized:
        if total_ms < MIN_TOTAL_MS:
            errors.append(
                f"サブ区間の合計は {int(MIN_DURATION_SEC)} 秒以上である必要があります"
                f"（現在 {total_ms / 1000:.1f} 秒）。区間を追加するか伸ばしてください。"
            )
        if total_ms > MAX_TOTAL_MS:
            errors.append(
                f"サブ区間の合計は {int(MAX_DURATION_SEC)} 秒以下である必要があります"
                f"（現在 {total_ms / 1000:.1f} 秒）。区間を減らすか短くしてください。"
            )

    return ShortCutValidationResult(
        ok=not errors,
        errors=tuple(errors),
        segments=tuple(segment for segment, _start, _end in normalized),
        total_ms=total_ms,
    )


def cut_plan_path(video_id: str, parent_id: str, settings: Settings) -> Path:
    """カットプラン JSON のパスを返す."""
    return (
        settings.data_dir
        / video_id
        / "shorts"
        / "cutplan"
        / f"cut_{_safe_parent_id(parent_id)}.json"
    )


def cut_prompt_path(video_id: str, parent_id: str, settings: Settings) -> Path:
    """カットプラン用プロンプトのパスを返す."""
    return (
        settings.data_dir
        / video_id
        / "shorts"
        / "cutplan"
        / f"cut_{_safe_parent_id(parent_id)}.prompt.md"
    )


def _safe_parent_id(parent_id: str) -> str:
    """親候補 ID をファイル名として安全な形へ落とす."""
    if not isinstance(parent_id, str) or not parent_id.strip():
        raise ShortCutError("親候補の ID を取得できませんでした。")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", parent_id.strip())
    if not safe or set(safe) == {"_"}:
        raise ShortCutError(f"親候補の ID が不正です: {parent_id}")
    return safe


def _artifact_lineage(
    video_id: str,
    artifact: TranscriptArtifact,
    settings: Settings,
) -> tuple[TranscriptArtifactRef, str, tuple[str, ...]]:
    """高精度 job の結果から downstream 共通 provenance を一度だけ作る."""
    if not isinstance(artifact, TranscriptArtifact):
        raise ShortCutError("高精度字幕 artifact が正しくありません。")
    if artifact.video_id != video_id:
        raise ShortCutError("高精度字幕 artifact の動画 ID が一致しません。")
    if not artifact.is_high_precision:
        raise ShortCutError(
            "高精度字幕の生成に失敗しました。明示的な VTT fallback を選ぶか、処理を停止してください。"
        )
    try:
        reference = TranscriptArtifactStore(video_id, settings).artifact_ref(artifact)
    except TranscriptArtifactError as exc:
        raise ShortCutError("高精度字幕 artifact の immutable reference を作成できません。") from exc
    return reference, artifact.artifact_fingerprint, tuple(artifact.used_range_cue_digests)


def cutplan_artifact_lineage_is_current(
    video_id: str,
    document: ShortCutDocument,
    settings: Settings,
) -> bool:
    """cutplan が参照する高精度 artifact が保存実体と adopted contract と一致するか判定する."""
    if document.artifact_ref is None:
        return True
    if document.artifact_fingerprint is None or not document.used_range_cue_digests:
        return False
    return stored_artifact_lineage_is_current(
        video_id=video_id,
        artifact_ref=document.artifact_ref,
        artifact_fingerprint=document.artifact_fingerprint,
        used_range_cue_digests=document.used_range_cue_digests,
        settings=settings,
    )


def _document_with_lineage(
    parent: ParentCandidate,
    segments: Sequence[HighlightSegment],
    *,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
) -> ShortCutDocument:
    parent_start_ms, parent_end_ms = parent_bounds_ms(parent)
    return ShortCutDocument(
        parent_id=_as_highlight(parent).id,
        parent_start_ms=parent_start_ms,
        parent_end_ms=parent_end_ms,
        candidates=list(segments),
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        used_range_cue_digests=tuple(used_range_cue_digests),
    )


def save_cut_plan(
    video_id: str,
    parent: ParentCandidate,
    raw_json_text: str,
    settings: Settings,
    *,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
    artifact: TranscriptArtifact | None = None,
) -> tuple[Path, ShortCutDocument]:
    """検証を通過したサブ区間だけを atomic 保存する."""
    if artifact is not None:
        derived_ref, derived_fingerprint, derived_digests = _artifact_lineage(
            video_id,
            artifact,
            settings,
        )
        if artifact_ref is not None and artifact_ref != derived_ref:
            raise ShortCutError("cutplan の artifact reference が入力 artifact と一致しません。")
        if artifact_fingerprint is not None and artifact_fingerprint != derived_fingerprint:
            raise ShortCutError("cutplan の artifact fingerprint が入力 artifact と一致しません。")
        if used_range_cue_digests and tuple(used_range_cue_digests) != derived_digests:
            raise ShortCutError("cutplan の used_range_cue_digest が入力 artifact と一致しません。")
        artifact_ref = derived_ref
        artifact_fingerprint = derived_fingerprint
        used_range_cue_digests = derived_digests
    data = _extract_json_object(raw_json_text)
    validation = validate_short_cut(data, parent=parent)
    if not validation.ok:
        detail = "\n".join(f"- {error}" for error in validation.errors)
        raise ShortCutValidationError(
            f"サブ区間の形式が正しくありません:\n{detail}",
            validation.errors,
        )

    document = _document_with_lineage(
        parent,
        validation.segments,
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        used_range_cue_digests=used_range_cue_digests,
    )
    path = cut_plan_path(video_id, document.parent_id, settings)
    write_text_atomically(
        path,
        document.model_dump_json(indent=2, exclude_none=True, exclude_defaults=True),
    )
    return path, document


def load_cut_plan(
    video_id: str,
    parent_id: str,
    settings: Settings,
) -> ShortCutDocument | None:
    """保存済みカットプランを読み込む（壊れていれば None）."""
    path = cut_plan_path(video_id, parent_id, settings)
    if not path.is_file():
        return None
    try:
        return ShortCutDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pydantic ValidationError / JSON 破損
        logger.warning("カットプランを読み込めませんでした: %s (%s)", path, exc)
        return None


def suggest_short_cuts(
    video_id: str,
    parent: ParentCandidate,
    settings: Settings | None = None,
    *,
    on_progress: ProgressCallback = None,
    prompt_only: bool = False,
    codex_path: str = "codex",
) -> ShortCutResult:
    """親候補の区間内から、ショート 1 本分のサブ区間を提案して保存する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise ShortCutError(f"動画ディレクトリが見つかりません: {video_dir}")

    parent_segment = _as_highlight(parent)
    parent_id = parent_segment.id
    if not needs_short_cut(parent_segment):
        raise ShortCutError(
            f"この候補は {int(MAX_DURATION_SEC)} 秒以内のため、"
            "そのままショートを作成できます。"
        )

    vtt_path = video_dir / "subtitles" / "ja.vtt"
    if not vtt_path.is_file():
        raise ShortCutError(f"字幕ファイルが見つかりません: {vtt_path}")

    prompt = build_short_cut_prompt(
        vtt_path.read_text(encoding="utf-8"),
        parent_segment,
        find_project_root(),
    )
    prompt_path = cut_prompt_path(video_id, parent_id, settings)
    write_text_atomically(prompt_path, prompt)

    if prompt_only:
        return ShortCutResult(
            video_id=video_id,
            parent_id=parent_id,
            prompt_path=prompt_path,
            cut_plan_path=None,
            used_codex=False,
            document=None,
        )

    if not is_codex_available(codex_path):
        raise CodexNotFoundError(
            CODEX_INSTALL_HINT.format(
                prompt_path=prompt_path,
                cut_plan_path=cut_plan_path(video_id, parent_id, settings),
            )
        )

    if on_progress is not None:
        on_progress("short_cut", "ショート用の区間を提案しています…")

    raw_output = invoke_codex(prompt, codex_path=codex_path)
    saved_path, document = save_cut_plan(video_id, parent_segment, raw_output, settings)
    return ShortCutResult(
        video_id=video_id,
        parent_id=parent_id,
        prompt_path=prompt_path,
        cut_plan_path=saved_path,
        used_codex=True,
        document=document,
    )


def refine_selected_short_cut(
    video_id: str,
    parent: ParentCandidate,
    segments: Sequence[HighlightSegment],
    settings: Settings | None = None,
    *,
    job_id: str | None = None,
    on_progress: Callable[[object], None] | None = None,
) -> RefinedShortCutResult:
    """人が確定した区間だけを Whisper で精査し、同じ artifact を保存する.

    この関数は明示的な高精度化入口専用であり、候補探索・通常 rerun・動画全体の
    導線からは呼び出さない。失敗時は既存 cutplan を再利用せず、呼び出し側へ
    fallback/停止の選択を返す。
    """
    settings = settings or get_settings()
    parent_segment = _as_highlight(parent)
    validation = validate_short_cut_selection(segments, parent=parent_segment)
    if not validation.ok:
        detail = "\n".join(f"- {error}" for error in validation.errors)
        raise ShortCutValidationError(
            f"選択区間を高精度化できません:\n{detail}",
            validation.errors,
        )

    # pair 形式を runtime に渡し、S9-3 の設定 padding を runtime 側で適用させる。
    # 入力順はそのまま保持し、Whisper の timestamp で境界を置き換えない。
    selected_ranges = tuple(
        (bounds.start_ms, bounds.end_ms)
        for bounds in normalize_segment_bounds(validation.segments)
    )
    try:
        result = run_selected_ranges(
            video_id,
            selected_ranges,
            settings,
            job_id=job_id,
            on_progress=on_progress,
        )
    except Exception as exc:
        if isinstance(exc, (ShortCutError, ShortCutValidationError)):
            raise
        raise ShortCutError(
            "選択区間の高精度字幕に失敗しました。VTT fallback を明示的に選ぶか、処理を停止してください。"
        ) from exc
    if not result.is_high_precision or result.artifact is None:
        diagnostic = result.diagnostic or "Whisper の高精度結果が利用できません。"
        raise ShortCutError(
            f"選択区間の高精度字幕に失敗しました: {diagnostic}"
            " 明示的な VTT fallback を選ぶか、処理を停止してください。"
        )

    reference, fingerprint, digests = _artifact_lineage(
        video_id,
        result.artifact,
        settings,
    )
    document = _document_with_lineage(
        parent_segment,
        validation.segments,
        artifact_ref=reference,
        artifact_fingerprint=fingerprint,
        used_range_cue_digests=digests,
    )
    path = cut_plan_path(video_id, document.parent_id, settings)
    write_text_atomically(
        path,
        document.model_dump_json(indent=2, exclude_none=True, exclude_defaults=True),
    )
    return RefinedShortCutResult(
        video_id=video_id,
        parent_id=document.parent_id,
        job_id=result.job_id,
        artifact=result.artifact,
        artifact_ref=reference,
        artifact_fingerprint=fingerprint,
        used_range_cue_digests=digests,
        document=document,
    )


# API 名の揺れを避けるための薄い互換入口。処理本体は常に 1 job / 1 artifact。
refine_selected_short_cuts = refine_selected_short_cut


def selected_total_ms(segments: Sequence[HighlightSegment]) -> int:
    """選択中のサブ区間の合計を整数ミリ秒で返す."""
    if not segments:
        return 0
    try:
        return sum(bounds.duration_ms for bounds in normalize_segment_bounds(segments))
    except TelopError as exc:
        raise ShortCutError(str(exc)) from exc
