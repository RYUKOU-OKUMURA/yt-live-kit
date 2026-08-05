"""Pure projections and editor-buffer rules for the shorts production line."""

from __future__ import annotations

import hashlib
import json
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopLine, TelopScriptDocument, TelopSegmentScript
from yt_live_kit.services.shorts_line import (
    DailyLineSummary,
    LineStage,
    LineState,
    set_review_fingerprint,
)
from yt_live_kit.services.shorts_queue import ShortsQueueTarget
from yt_live_kit.ui.session_keys import telop_editor_prefix

PreviewMode = Literal["source", "generating", "output", "source_missing"]
CandidateKey = tuple[Literal["clips", "highlights"], str]

STAGES: tuple[LineStage, ...] = (
    LineStage.MATERIAL_SELECTION,
    LineStage.SEGMENT_DECISION,
    LineStage.TELOP_REVIEW,
    LineStage.GENERATION,
    LineStage.FINAL_REVIEW,
    LineStage.RESERVATION,
)
STAGE_LABELS = {
    LineStage.MATERIAL_SELECTION: "素材選定",
    LineStage.SEGMENT_DECISION: "区間決定",
    LineStage.TELOP_REVIEW: "テロップ確認",
    LineStage.GENERATION: "生成",
    LineStage.FINAL_REVIEW: "最終確認",
    LineStage.RESERVATION: "予約",
    LineStage.RESERVED: "予約済み",
}
NEXT_ACTIONS = {
    LineStage.MATERIAL_SELECTION: "候補を選ぶ",
    LineStage.SEGMENT_DECISION: "区間の文字起こしと境界を確認",
    LineStage.TELOP_REVIEW: "台本全体を確認",
    LineStage.GENERATION: "確認済み台本から動画を生成",
    LineStage.FINAL_REVIEW: "完成動画をプレビュー",
    LineStage.RESERVATION: "投稿内容を確認して予約",
    LineStage.RESERVED: "予約完了",
}


@dataclass(frozen=True)
class SidebarLineProjection:
    """Read-only data required by the global sidebar renderer."""

    state: LineState | None
    daily: DailyLineSummary | None
    title: str | None = None
    preview_mode: PreviewMode | None = None
    preview_path: Path | None = None
    preview_start_sec: int | None = None
    preview_end_sec: int | None = None


def stage_number(stage: LineStage) -> int:
    if stage == LineStage.RESERVED:
        return 6
    return STAGES.index(stage) + 1


StepStatus = Literal["done", "current", "locked"]


@dataclass(frozen=True)
class LineStepperItem:
    """サイドバー縦ステッパーの 1 工程分の表示状態."""

    index: int
    stage: LineStage
    label: str
    status: StepStatus


def line_stepper_items(current_stage: LineStage | None) -> tuple[LineStepperItem, ...]:
    """6 工程の状態を導出する純粋関数（サイドバーとメインの表示矛盾を防ぐ唯一の判定源）。

    ``current_stage`` が ``None`` の場合、``LineState`` はまだ作られていないが
    「これから素材を選ぶ」という現在地は決まっているため、工程 1（素材選定）を
    ``current`` とし、工程 2〜6 は一本道でまだ進めない ``locked`` として返す。
    これは表示の導出だけであり、``LineState`` の生成条件（区間確定まで
    ``LineState`` を作らない永続化の意味）は変えない。
    ``LineStage.RESERVED`` は既存 ``render_stage_bar`` の分岐と同様に全工程を
    ``done`` として扱う。
    """
    if current_stage is None:
        return tuple(
            LineStepperItem(
                index=index,
                stage=stage,
                label=STAGE_LABELS[stage],
                status="current" if index == 1 else "locked",
            )
            for index, stage in enumerate(STAGES, start=1)
        )
    current = stage_number(current_stage)
    reserved = current_stage == LineStage.RESERVED
    items: list[LineStepperItem] = []
    for index, stage in enumerate(STAGES, start=1):
        status: StepStatus
        if index < current or reserved:
            status = "done"
        elif index == current:
            status = "current"
        else:
            status = "locked"
        items.append(
            LineStepperItem(index=index, stage=stage, label=STAGE_LABELS[stage], status=status)
        )
    return tuple(items)


def line_next_action_text(current_stage: LineStage | None) -> str:
    """サイドバー「次にやること」の文言を導出する。

    サイドバーは全ページで描画されるため、「〇〇タブで」のような特定画面を
    前提にした表現は使わない。ライン未開始（``current_stage is None``）でも
    行き止まりを作らず、最初の一歩を案内する。
    """
    if current_stage is None:
        return "素材を選び、区間を決める"
    return NEXT_ACTIONS[current_stage]


def choose_preview_mode(
    *,
    output_available: bool,
    generation_running: bool,
    source_available: bool,
) -> PreviewMode:
    if generation_running:
        return "generating"
    if output_available:
        return "output"
    if source_available:
        return "source"
    return "source_missing"


def is_human_review_current(state: LineState, review_fingerprint: str) -> bool:
    return (
        state.review_fingerprint == review_fingerprint
        and state.review_confirmed_fingerprint == review_fingerprint
    )


def project_review_state(
    state: LineState,
    review_fingerprint: str,
    *,
    force_unconfirmed: bool,
) -> LineState:
    """Project editor changes without writing the durable line state."""
    projected = set_review_fingerprint(state, review_fingerprint)
    if projected is not state or not force_unconfirmed:
        return projected
    return state.model_copy(
        update={
            "review_confirmed_fingerprint": None,
            "review_confirmed_at": None,
            "generation_spec_json": None,
            "generation_spec_fingerprint": None,
            "output_fingerprint": None,
            "preview_confirmed_fingerprint": None,
            "preview_confirmed_at": None,
            "current_stage": LineStage.TELOP_REVIEW,
        }
    )


def ordered_candidate_keys(
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
    preferred_candidate_keys: Sequence[CandidateKey],
) -> tuple[CandidateKey, ...]:
    natural: list[CandidateKey] = [
        *(("clips", candidate.id) for candidate in clip_candidates),
        *(("highlights", candidate.id) for candidate in highlight_candidates),
    ]
    available = set(natural)
    values: list[CandidateKey] = []
    for key in preferred_candidate_keys:
        if key in available and key not in values:
            values.append(key)
    values.extend(key for key in natural if key not in values)
    return tuple(values)


def telop_draft_identity(
    draft: TelopScriptDocument,
    *,
    queue_fingerprint: str,
) -> str:
    """Hash the full immutable draft, queue snapshot, and artifact lineage."""
    payload = {
        "queue_fingerprint": queue_fingerprint,
        "document": draft.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _editor_initial_values(
    draft: TelopScriptDocument,
    *,
    prefix: str,
) -> dict[str, object]:
    values: dict[str, object] = {
        f"{prefix}_hook": draft.hook_text,
        f"{prefix}_titles": "\n".join(draft.title_candidates),
        f"{prefix}_description": draft.description,
        f"{prefix}_tags": ",".join(draft.tags),
    }
    for segment_index, segment in enumerate(draft.segments):
        for line_index, line in enumerate(segment.lines):
            line_prefix = f"{prefix}_{segment_index}_{line_index}"
            values[f"{line_prefix}_text"] = line.text
            values[f"{line_prefix}_start"] = line.start_sec
            values[f"{line_prefix}_end"] = line.end_sec
            values[f"{line_prefix}_emphasis"] = line.emphasis
    return values


def sync_telop_editor_state(
    state: MutableMapping[str, object],
    draft: TelopScriptDocument,
    *,
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
) -> str:
    """Keep edits for one identity and reset only this editor for a new draft."""
    prefix = telop_editor_prefix(video_id, clip_id)
    identity_key = f"{prefix}_draft_identity"
    identity = telop_draft_identity(draft, queue_fingerprint=queue_fingerprint)
    if state.get(identity_key) == identity:
        return prefix

    for key in tuple(state):
        if isinstance(key, str) and key.startswith(f"{prefix}_"):
            state.pop(key, None)
    state.update(_editor_initial_values(draft, prefix=prefix))
    state[identity_key] = identity
    return prefix


def telop_document_from_editor_state(
    state: MutableMapping[str, object],
    draft: TelopScriptDocument,
    *,
    prefix: str,
) -> TelopScriptDocument:
    segments: list[TelopSegmentScript] = []
    for segment_index, segment in enumerate(draft.segments):
        lines: list[TelopLine] = []
        for line_index, _line in enumerate(segment.lines):
            line_prefix = f"{prefix}_{segment_index}_{line_index}"
            lines.append(
                TelopLine(
                    text=str(state[f"{line_prefix}_text"]),
                    start_sec=float(state[f"{line_prefix}_start"]),
                    end_sec=float(state[f"{line_prefix}_end"]),
                    emphasis=bool(state[f"{line_prefix}_emphasis"]),
                )
            )
        segments.append(
            TelopSegmentScript(
                start_sec=segment.start_sec,
                end_sec=segment.end_sec,
                lines=lines,
            )
        )
    return TelopScriptDocument(
        hook_text=str(state[f"{prefix}_hook"]),
        title_candidates=tuple(
            line
            for line in str(state[f"{prefix}_titles"]).splitlines()
            if line.strip()
        ),
        description=str(state[f"{prefix}_description"]),
        tags=tuple(
            value.strip()
            for value in str(state[f"{prefix}_tags"]).split(",")
            if value.strip()
        ),
        segments=segments,
        artifact_ref=draft.artifact_ref,
        artifact_fingerprint=draft.artifact_fingerprint,
        used_range_cue_digests=draft.used_range_cue_digests,
    )


def sidebar_projection_from_target(
    *,
    state: LineState,
    daily: DailyLineSummary | None,
    target: ShortsQueueTarget | None,
    output_path: Path | None,
    source_path: Path | None,
    generation_running: bool,
) -> SidebarLineProjection:
    mode = choose_preview_mode(
        output_available=output_path is not None,
        generation_running=generation_running,
        source_available=source_path is not None,
    )
    preview_path = output_path if mode == "output" else source_path
    return SidebarLineProjection(
        state=state,
        daily=daily,
        title=(
            " + ".join(segment.title for segment in target.segments)
            if target is not None
            else None
        ),
        preview_mode=mode,
        preview_path=preview_path,
        preview_start_sec=(
            int(target.segments[0].start_ms / 1000)
            if mode == "source" and target is not None
            else None
        ),
        preview_end_sec=(
            int(target.segments[-1].end_ms / 1000)
            if mode == "source" and target is not None
            else None
        ),
    )
