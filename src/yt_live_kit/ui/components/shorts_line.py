"""FR-33 のショート生産ラインを既存 S4 / P2 境界へ接続する UI."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.models.upload import UploadOperation
from yt_live_kit.services.ai_prompt import AiPromptError
from yt_live_kit.services.jobs import get_active_job
from yt_live_kit.services.schedule import ScheduleError, load_schedule_policy
from yt_live_kit.services.shorts_line import (
    DailyLineSummary,
    LineStage,
    LineState,
    LineStateError,
    abandon_line_state,
    confirm_preview,
    confirm_review,
    create_line_state,
    evaluate_telop_gate,
    record_output,
    record_upload_operation,
    reconcile_output,
    load_line_state,
    make_generation_spec_fingerprint,
    materialize_line_state_projection,
    persist_line_start,
    resolve_active_line_read_only,
    resolve_active_line,
    save_active_line,
    save_line_state,
    run_line_reservation_transaction,
    set_review_fingerprint,
    set_generation_spec,
    summarize_daily_lines,
    make_review_fingerprint,
)
from yt_live_kit.services.shorts_queue import (
    ShortsQueueClipSpec,
    ShortsQueueError,
    ShortsQueueSegmentSpec,
    ShortsQueueTarget,
    build_shorts_queue_targets,
    load_latest_shorts_queue_result,
    make_shorts_queue_clip_spec,
    make_shorts_queue_fingerprint,
)
from yt_live_kit.services.telop import (
    TelopError,
    generate_telop_script,
    save_confirmed_telop_script,
    validate_telop_script,
)
from yt_live_kit.services.transcript_artifact import TranscriptArtifactError, TranscriptArtifactStore
from yt_live_kit.services.short_cut import load_cut_plan
from yt_live_kit.services.subtitle_burn import TELOP_PRESETS
from yt_live_kit.services.upload_queue import UploadQueueError, list_operations
from yt_live_kit.ui.components.short_cut import (
    ParentOption,
    artifact_provenance_payload,
    render_short_cut_section,
)
from yt_live_kit.ui.components.shorts_queue import (
    clear_line_confirmed_spec,
    clear_line_snapshot,
    install_prepared_line_snapshot,
    install_line_confirmed_spec,
    install_line_snapshot,
    prepare_line_snapshot,
    restore_line_snapshot,
    start_or_confirm_line_generation,
)
from yt_live_kit.ui.controllers.upload import LineUploadAdapter
from yt_live_kit.ui.session_keys import (
    detail_workspace_key,
    line_material_selection_key,
    line_material_target_key,
    line_material_transfer_marker_key,
    line_review_invalidated_key,
    line_review_seen_key,
    shorts_line_context_key,
)
from yt_live_kit.ui.view_models.shorts_line import (
    CandidateKey,
    LineStepperItem,
    SidebarLineProjection,
    StepStatus,
    choose_preview_mode,
    is_human_review_current,
    line_next_action_text,
    line_stepper_items,
    ordered_candidate_keys,
    project_review_state,
    sidebar_projection_from_target,
    stage_number,
    sync_telop_editor_state,
    telop_document_from_editor_state,
)
from yt_live_kit.ui.views._local_settings import (
    ShortsLineDefaults,
    load_shorts_line_defaults,
)

PreviewMode = Literal["source", "generating", "output", "source_missing"]


def _safe(value: object) -> str:
    return str(value).replace("<", "〈").replace(">", "〉")


def _inspect_artifact_lineage(
    state: LineState,
    settings: Settings,
) -> tuple[bool, str]:
    """S9-4 store の検証結果だけを使い、対象範囲を保った失効理由を返す."""
    if state.artifact_ref is None:
        return True, "字幕 provenance: coarse VTT fallback。境界は人が確認してください。"
    if state.artifact_fingerprint is None or not state.used_range_cue_digests:
        return (
            False,
            "高精度字幕 artifact lineage が不完全です。"
            f"対象 clip: {state.clip_id}。台本確認と最終確認だけを失効しました。"
            "次 gate: 工程 3 の台本を再確認してください。",
        )
    try:
        store = TranscriptArtifactStore(state.video_id, settings)
        artifact = store.load_artifact(state.artifact_fingerprint)
        actual_ref = store.artifact_ref(artifact)
    except (TranscriptArtifactError, OSError, ValueError):
        return (
            False,
            "保存済み高精度字幕 artifact を確認できないため失効しました。"
            f"対象 clip: {state.clip_id}。使用区間外の候補や動画全体は削除していません。"
            "次 gate: 工程 3 の台本を再確認してください。",
        )
    if (
        actual_ref != state.artifact_ref
        or artifact.video_id != state.video_id
        or artifact.artifact_fingerprint != state.artifact_fingerprint
        or tuple(artifact.used_range_cue_digests)
        != tuple(state.used_range_cue_digests)
        or not artifact.is_high_precision
    ):
        return (
            False,
            "高精度字幕 artifact と使用区間の証跡が一致しないため失効しました。"
            f"対象 clip: {state.clip_id}。使用範囲外の変更でライン全体は失効していません。"
            "次 gate: 工程 3 の台本を再確認してください。",
        )
    return True, "高精度 artifact lineage を確認済みです。"


def _render_telop_provenance_header(draft: TelopScriptDocument) -> None:
    payload = artifact_provenance_payload(draft)
    if payload is None:
        st.caption("telop editor provenance: coarse VTT fallback（高精度 artifact なし）")
        return
    st.caption("telop editor provenance: refined artifact（同一 ref / digest 配列）")
    st.code(json.dumps(payload, ensure_ascii=False, indent=2))


def _candidate_handoff_label(
    candidate: ClipCandidate | HighlightSegment,
    source: Literal["clips", "highlights"] | None = None,
) -> str:
    source_label = (
        "切り抜き" if source == "clips" else "ハイライト" if source else None
    )
    prefix = f"［{source_label}］" if source_label else ""
    return _safe(
        f"{prefix}{candidate.id}: {candidate.title}（{candidate.start} → {candidate.end}）"
    )


def _context_key(video_id: str) -> str:
    return shorts_line_context_key(video_id)


def _context(video_id: str) -> dict[str, object] | None:
    raw = st.session_state.get(_context_key(video_id))
    return raw if isinstance(raw, dict) else None


def _save_context(video_id: str, value: dict[str, object]) -> None:
    st.session_state[_context_key(video_id)] = value


def _spec_matches_lineage(spec: ShortsQueueClipSpec, state: LineState) -> bool:
    """persisted full-spec proof と一致する manifest spec だけを認める。"""
    if (
        spec.target_id != state.clip_id
        or state.review_fingerprint is None
        or state.generation_spec_fingerprint is None
    ):
        return False
    try:
        return (
            make_generation_spec_fingerprint(
                state.video_id,
                state.clip_id,
                state.queue_fingerprint,
                state.review_fingerprint,
                spec.to_dict(),
            )
            == state.generation_spec_fingerprint
        )
    except (LineStateError, ShortsQueueError, ValueError):
        return False


def _current_context_spec(
    video_id: str,
    context: dict[str, object],
    state: LineState,
) -> ShortsQueueClipSpec | None:
    """旧 review の session spec を採用せず、S4 confirmed 集合からも外す。"""
    spec = context.get("confirmed_spec")
    if isinstance(spec, ShortsQueueClipSpec) and _spec_matches_lineage(spec, state):
        return spec
    if spec is not None:
        context.pop("confirmed_spec", None)
        clear_line_confirmed_spec(video_id, state.clip_id)
        _save_context(video_id, context)
    return None


def _source_for(option: ParentOption) -> str:
    return "clips" if isinstance(option.candidate, ClipCandidate) else "highlights"


def _as_highlight(candidate: ClipCandidate | HighlightSegment) -> HighlightSegment:
    if isinstance(candidate, HighlightSegment):
        return candidate
    return HighlightSegment(
        id=candidate.id,
        title=candidate.title,
        start=candidate.start,
        end=candidate.end,
        duration_sec=candidate.duration_sec,
        reason=candidate.reason,
    )


def _material_context_payload(
    *,
    source: str,
    original_candidate: ClipCandidate | HighlightSegment,
    target: ShortsQueueTarget,
    defaults: ShortsLineDefaults,
) -> dict[str, object]:
    return {
        "source": source,
        "original_kind": (
            "clip" if isinstance(original_candidate, ClipCandidate) else "highlight"
        ),
        "original_candidate": original_candidate.model_dump(mode="json"),
        "target": {
            "target_id": target.target_id,
            "segments": [segment.to_dict() for segment in target.segments],
            "output_name": target.output_name,
        },
        "defaults": {
            "layout": defaults.layout,
            "preset": defaults.preset,
            "hook_preset": defaults.hook_preset,
        },
    }


def _restore_material_context(
    state: LineState,
) -> tuple[
    str,
    ClipCandidate | HighlightSegment,
    ShortsQueueTarget,
    ShortsLineDefaults,
] | None:
    """LineState 内の immutable material evidence を full queue hash で検証する。"""
    if state.material_context_json is None:
        return None
    try:
        raw = json.loads(state.material_context_json)
        if not isinstance(raw, dict) or set(raw) != {
            "source",
            "original_kind",
            "original_candidate",
            "target",
            "defaults",
        }:
            return None
        source = raw["source"]
        kind = raw["original_kind"]
        if source not in {"clips", "highlights"}:
            return None
        if kind == "clip" and source == "clips":
            original: ClipCandidate | HighlightSegment = ClipCandidate.model_validate(
                raw["original_candidate"]
            )
        elif kind == "highlight" and source == "highlights":
            original = HighlightSegment.model_validate(raw["original_candidate"])
        else:
            return None
        target_raw = raw["target"]
        defaults_raw = raw["defaults"]
        if not isinstance(target_raw, dict) or set(target_raw) != {
            "target_id",
            "segments",
            "output_name",
        }:
            return None
        if not isinstance(defaults_raw, dict) or set(defaults_raw) != {
            "layout",
            "preset",
            "hook_preset",
        }:
            return None
        segments_raw = target_raw["segments"]
        if not isinstance(segments_raw, list):
            return None
        segments = tuple(
            ShortsQueueSegmentSpec.from_dict(value) for value in segments_raw
        )
        target = ShortsQueueTarget(
            str(target_raw["target_id"]),
            segments,
            str(target_raw["output_name"]),
        )
        rebuilt = build_shorts_queue_targets(segments, mode="concat")[0]
        if target != rebuilt or target.target_id != state.clip_id:
            return None
        defaults = ShortsLineDefaults(
            str(defaults_raw["layout"]),
            str(defaults_raw["preset"]),
            str(defaults_raw["hook_preset"]),
        )
        if (
            defaults.layout not in {"blur", "crop"}
            or defaults.preset not in TELOP_PRESETS
            or defaults.hook_preset not in TELOP_PRESETS
        ):
            return None
        queue_fingerprint = make_shorts_queue_fingerprint(
            video_id=state.video_id,
            source=source,
            mode="concat",
            original_candidates=(original,),
            segments=segments,
            layout=defaults.layout,
            preset=defaults.preset,
            hook_preset=defaults.hook_preset,
        )
        if queue_fingerprint != state.queue_fingerprint:
            return None
        return source, original, target, defaults
    except (TypeError, ValueError, ShortsQueueError):
        return None


def _persisted_generation_spec(state: LineState) -> ShortsQueueClipSpec | None:
    if state.generation_spec_json is None:
        return None
    try:
        raw = json.loads(state.generation_spec_json)
        spec = ShortsQueueClipSpec.from_dict(raw)
    except (TypeError, ValueError, json.JSONDecodeError, ShortsQueueError):
        return None
    return spec if _spec_matches_lineage(spec, state) else None


def _cutplan_lineage(
    video_id: str,
    option: ParentOption,
    settings: Settings,
) -> dict[str, object]:
    """明示高精度化済み cutplan の provenance だけを line に引き渡す."""
    document = load_cut_plan(video_id, option.id, settings)
    if document is None or document.artifact_ref is None:
        return {}
    if document.artifact_fingerprint is None:
        raise LineStateError("cutplan の artifact lineage が不完全です。")
    return {
        "artifact_ref": document.artifact_ref,
        "artifact_fingerprint": document.artifact_fingerprint,
        "used_range_cue_digests": tuple(document.used_range_cue_digests),
    }


def _generate_line_telop(
    *,
    video_id: str,
    target: ShortsQueueTarget,
    state: LineState,
    context: dict[str, object],
    settings: Settings,
) -> None:
    """明示操作時だけ台本を生成し、旧 spec/output lineage を失効させる。"""
    try:
        state = materialize_line_state_projection(state, settings)
        artifact = None
        if state.artifact_ref is not None:
            if state.artifact_fingerprint is None:
                raise TelopError("ライン state の artifact lineage が不完全です。")
            try:
                artifact = TranscriptArtifactStore(video_id, settings).load_artifact(
                    state.artifact_fingerprint
                )
                actual_ref = TranscriptArtifactStore(video_id, settings).artifact_ref(artifact)
            except TranscriptArtifactError as exc:
                raise TelopError(
                    "固定済み字幕 artifact を読み込めません。古い結果は再利用せず、VTT fallback を明示的に選んでください。"
                ) from exc
            if actual_ref != state.artifact_ref:
                raise TelopError("固定済み字幕 artifact reference が state と一致しません。")
        generated = generate_telop_script(
            video_id,
            target.highlight_segments(),
            settings,
            transcript_artifact=artifact,
            artifact_ref=state.artifact_ref,
        )
        if generated.document is None:
            raise TelopError("テロップ台本を生成できませんでした。")
        review = make_review_fingerprint(
            video_id,
            target.target_id,
            state.queue_fingerprint,
            generated.document,
        )
        previous_state = state
        updated = set_review_fingerprint(previous_state, review)
        if updated != previous_state:
            save_line_state(updated, settings, expected_state=previous_state)
        context["draft"] = generated.document
        context.pop("telop_error", None)
        context.pop("confirmed_spec", None)
        clear_line_confirmed_spec(video_id, target.target_id)
        _save_context(video_id, context)
    except (AiPromptError, LineStateError, TelopError) as exc:
        context["telop_error"] = str(exc)
        _save_context(video_id, context)


@st.dialog("ショート生産ラインを素材選定へ戻す")
def _confirm_abandon_line_dialog(state: LineState, settings: Settings) -> None:
    """ライン状態だけを退避し、動画・台本・出力成果物は削除しない。"""
    st.warning("現在の工程と人確認状態を終了し、素材選定へ戻ります。")
    st.write(
        "生成済み動画・テロップ台本・マニフェストは削除しません。"
        "このライン状態は退避され、操作後は自動では再開されません。"
    )
    confirmed = st.checkbox(
        "ライン状態を退避して素材選定へ戻ることを確認した",
        value=False,
        key=f"line_abandon_confirm_{state.video_id}_{state.clip_id}",
    )
    if st.button(
        "ラインを終了して素材選定へ戻る",
        type="primary",
        disabled=not confirmed,
        key=f"line_abandon_execute_{state.video_id}_{state.clip_id}",
    ):
        try:
            canonical = materialize_line_state_projection(state, settings)
            abandon_line_state(canonical, settings)
        except LineStateError as exc:
            st.error(_safe(exc))
            return
        st.session_state.pop(_context_key(state.video_id), None)
        clear_line_confirmed_spec(state.video_id, state.clip_id)
        clear_line_snapshot(state.video_id)
        st.success("ライン状態を退避しました。素材を選び直せます。")
        st.rerun()


def _render_line_recovery_actions(
    state: LineState,
    settings: Settings,
    *,
    retry: Callable[[], None] | None = None,
    retry_label: str = "テロップ台本を再生成",
) -> None:
    """失敗・復元不能時の retry と確認付き終了を必ず提示する。"""
    with st.container(horizontal=True):
        if retry is not None and st.button(
            retry_label,
            key=f"line_telop_retry_{state.video_id}_{state.clip_id}",
        ):
            retry()
            st.rerun()
        if st.button(
            "ラインを終了して素材選定へ戻る",
            key=f"line_abandon_open_{state.video_id}_{state.clip_id}",
        ):
            _confirm_abandon_line_dialog(state, settings)


def _start_line(
    *,
    video_id: str,
    title: str,
    segments: Sequence[HighlightSegment],
    option: ParentOption,
    settings: Settings,
) -> None:
    """区間確定を既存 queue snapshot と永続 line state へ移す."""
    try:
        defaults = load_shorts_line_defaults(settings)
    except (OSError, TypeError, ValueError, RuntimeError):
        st.error(
            "ショート生産ラインの既定値を読み込めませんでした。"
            "設定ファイルの保存先が安全なデータディレクトリ内にあるか、"
            "読み込み権限と設定値の形式を確認してください。"
            "設定ページでショート既定値を保存し直してから再試行してください。"
            "ラインは開始していません。"
        )
        return
    try:
        lineage = _cutplan_lineage(video_id, option, settings)
        prepared = prepare_line_snapshot(
            video_id=video_id,
            source=_source_for(option),
            original_candidate=option.candidate,
            segments=segments,
            layout=defaults.layout,
            preset=defaults.preset,
            hook_preset=defaults.hook_preset,
            **lineage,
        )
        target = prepared.target
        queue_fingerprint = prepared.fingerprint
        material_context = _material_context_payload(
            source=_source_for(option),
            original_candidate=option.candidate,
            target=target,
            defaults=defaults,
        )
        state = create_line_state(
            video_id,
            target.target_id,
            queue_fingerprint,
            material_context=material_context,
            **lineage,
        )
        persist_line_start(state, settings)
    except (LineStateError, ShortsQueueError, TelopError) as exc:
        st.error(_safe(exc))
        return

    # Durable line + pointer が揃った後にだけ UI projection を切り替える。
    install_prepared_line_snapshot(prepared)
    context: dict[str, object] = {
        "title": title,
        "target": target,
        "queue_fingerprint": queue_fingerprint,
        "defaults": defaults,
        "original_candidate": option.candidate,
        "source": _source_for(option),
    }
    _save_context(video_id, context)

    # Codex 呼び出しは、この明示確定ボタンの操作時だけ行う。
    _generate_line_telop(
        video_id=video_id,
        target=target,
        state=state,
        context=context,
        settings=settings,
    )
    st.rerun()


def _restore_context(
    video_id: str,
    state: LineState,
    settings: Settings,
) -> dict[str, object] | None:
    """永続 material/spec proof から exact S4 line-mode state まで復元する。"""
    material = _restore_material_context(state)
    if material is None:
        return None
    source, original, target, defaults = material
    spec = _persisted_generation_spec(state)
    if spec is not None and (
        spec.target_id != target.target_id
        or spec.segments != target.segments
        or spec.output_name != target.output_name
        or spec.layout != defaults.layout
        or spec.preset != defaults.preset
        or spec.hook_preset != defaults.hook_preset
    ):
        return None
    document: TelopScriptDocument | None = None
    if spec is not None:
        try:
            document = spec.telop_document
        except ShortsQueueError:
            return None
    elif state.review_fingerprint is not None:
        script_path = (
            settings.data_dir
            / video_id
            / "shorts"
            / "telop"
            / f"telop_{state.clip_id}.json"
        )
        try:
            document = TelopScriptDocument.model_validate_json(
                script_path.read_text(encoding="utf-8")
            )
            if (
                make_review_fingerprint(
                    video_id,
                    state.clip_id,
                    state.queue_fingerprint,
                    document,
                )
                != state.review_fingerprint
            ):
                document = None
        except (OSError, UnicodeError, ValueError, LineStateError):
            document = None
    if document is not None:
        document_lineage = (
            document.artifact_ref,
            document.artifact_fingerprint,
            tuple(document.used_range_cue_digests),
        )
        state_lineage = (
            state.artifact_ref,
            state.artifact_fingerprint,
            tuple(state.used_range_cue_digests),
        )
        if document_lineage != state_lineage:
            # review fingerprintだけが一致する旧/別artifactの台本は復元しない。
            document = None
    try:
        lineage = (
            {
                "artifact_ref": state.artifact_ref,
                "artifact_fingerprint": state.artifact_fingerprint,
                "used_range_cue_digests": state.used_range_cue_digests,
            }
            if state.artifact_ref is not None
            else {}
        )
        restore_line_snapshot(
            video_id=video_id,
            source=source,
            original_candidate=original,
            target=target,
            layout=defaults.layout,
            preset=defaults.preset,
            hook_preset=defaults.hook_preset,
            expected_fingerprint=state.queue_fingerprint,
            confirmed_spec=spec,
            **lineage,
        )
    except ShortsQueueError:
        return None
    context: dict[str, object] = {
        "title": video_id,
        "target": target,
        "queue_fingerprint": state.queue_fingerprint,
        "defaults": defaults,
        "original_candidate": original,
        "source": source,
    }
    if document is not None:
        context["draft"] = document
    if spec is not None:
        context["confirmed_spec"] = spec
    _save_context(video_id, context)
    return context


def _restore_stale_telop_context(
    video_id: str,
    state: LineState,
    context: dict[str, object],
    settings: Settings,
) -> dict[str, object]:
    """保存台本が現行line証跡に一致する場合だけ古い失敗表示を置き換える."""
    if not context.get("telop_error"):
        return context
    if state.review_fingerprint is None:
        return context
    lineage_current, _ = _inspect_artifact_lineage(state, settings)
    if not lineage_current:
        return context
    restored = _restore_context(video_id, state, settings)
    if restored is not None and isinstance(
        restored.get("draft"), TelopScriptDocument
    ):
        # _restore_context は review / queue / artifact lineage を検証済み。
        # 生のCodex出力や不一致の保存ファイルはここへ到達しない。
        return restored
    _save_context(video_id, context)
    return context


def _editor_document(
    draft: TelopScriptDocument,
    *,
    video_id: str,
    clip_id: str,
    queue_fingerprint: str,
) -> TelopScriptDocument:
    prefix = sync_telop_editor_state(
        st.session_state,
        draft,
        video_id=video_id,
        clip_id=clip_id,
        queue_fingerprint=queue_fingerprint,
    )
    st.text_input(
        "フック文言",
        key=f"{prefix}_hook",
        persist_state="session",
    )
    st.text_area(
        "タイトル案（1 行 1 件）",
        key=f"{prefix}_titles",
        persist_state="session",
    )
    st.text_area(
        "説明文",
        key=f"{prefix}_description",
        persist_state="session",
    )
    st.text_input(
        "タグ（カンマ区切り）",
        key=f"{prefix}_tags",
        persist_state="session",
    )

    for segment_index, segment in enumerate(draft.segments):
        st.markdown(f"**区間 {segment_index + 1}**")
        for line_index, line in enumerate(segment.lines):
            line_prefix = f"{prefix}_{segment_index}_{line_index}"
            with st.container(border=True):
                st.text_input(
                    "テロップ本文",
                    key=f"{line_prefix}_text",
                    persist_state="session",
                )
                time_columns = st.columns(2)
                time_columns[0].number_input(
                    "開始秒",
                    step=0.1,
                    format="%.3f",
                    key=f"{line_prefix}_start",
                    persist_state="session",
                )
                time_columns[1].number_input(
                    "終了秒",
                    step=0.1,
                    format="%.3f",
                    key=f"{line_prefix}_end",
                    persist_state="session",
                )
                st.toggle(
                    "行全体を強調",
                    key=f"{line_prefix}_emphasis",
                    persist_state="session",
                )
                if (
                    str(st.session_state[f"{line_prefix}_text"]) != line.text
                    or bool(st.session_state[f"{line_prefix}_emphasis"])
                    != line.emphasis
                ):
                    st.caption("AI案から変更")
    return telop_document_from_editor_state(
        st.session_state,
        draft,
        prefix=prefix,
    )


def _project_editor_review_state(
    state: LineState,
    review_fingerprint: str,
) -> LineState:
    """Invalidate an observed edit in session, including an A → B → A revert."""
    seen_key = line_review_seen_key(state.video_id, state.clip_id)
    invalidated_key = line_review_invalidated_key(state.video_id, state.clip_id)
    previous = st.session_state.get(seen_key, state.review_fingerprint)
    if previous != review_fingerprint:
        st.session_state[invalidated_key] = True
    st.session_state[seen_key] = review_fingerprint
    return project_review_state(
        state,
        review_fingerprint,
        force_unconfirmed=bool(st.session_state.get(invalidated_key, False)),
    )


def _mark_editor_review_committed(state: LineState) -> None:
    st.session_state[line_review_seen_key(state.video_id, state.clip_id)] = (
        state.review_fingerprint
    )
    st.session_state[line_review_invalidated_key(state.video_id, state.clip_id)] = False


def _commit_telop_review(
    *,
    persisted_state: LineState,
    projected_state: LineState,
    review_fingerprint: str,
    hard_errors: Sequence[str],
    edited: TelopScriptDocument,
    target: ShortsQueueTarget,
    defaults: ShortsLineDefaults,
    settings: Settings,
) -> tuple[LineState, ShortsQueueClipSpec, TelopScriptDocument]:
    """Persist one explicit human-review command and its generation proof."""
    canonical = materialize_line_state_projection(persisted_state, settings)
    rebased = project_review_state(
        canonical,
        review_fingerprint,
        force_unconfirmed=projected_state.review_confirmed_fingerprint is None,
    )
    confirmed = confirm_review(
        rebased,
        review_fingerprint,
        hard_errors=hard_errors,
    )
    saved = save_confirmed_telop_script(
        canonical.video_id,
        target.highlight_segments(),
        edited,
        settings,
    )
    spec = make_shorts_queue_clip_spec(
        target,
        saved.document,
        layout=defaults.layout,
        preset=defaults.preset,
        hook_preset=defaults.hook_preset,
    )
    completed = set_generation_spec(
        confirmed,
        review_fingerprint,
        spec.to_dict(),
    )
    save_line_state(completed, settings, expected_state=canonical)
    return completed, spec, saved.document


def _confirm_line_preview(
    displayed_state: LineState,
    output_path: Path,
    settings: Settings,
) -> LineState:
    """Confirm the current output from a canonical command baseline."""
    canonical = materialize_line_state_projection(displayed_state, settings)
    observed = (
        record_output(canonical, output_path)
        if canonical.output_fingerprint is None
        else reconcile_output(canonical, output_path)
    )
    confirmed = confirm_preview(observed, output_path)
    save_line_state(confirmed, settings, expected_state=canonical)
    return confirmed


def _start_line_generation_command(
    *,
    displayed_state: LineState,
    review_fingerprint: str,
    spec: ShortsQueueClipSpec,
    video_id: str,
    title: str,
    settings: Settings,
) -> None:
    """Revalidate the durable proof before starting a generation job."""
    canonical = materialize_line_state_projection(displayed_state, settings)
    if (
        canonical.review_fingerprint != review_fingerprint
        or canonical.review_confirmed_fingerprint != review_fingerprint
        or _persisted_generation_spec(canonical) != spec
    ):
        raise LineStateError(
            "台本または生成条件が別の操作で更新されました。最新状態を読み直してください。"
        )
    start_or_confirm_line_generation(
        video_id=video_id,
        title=title,
        spec=spec,
        snapshot_fingerprint=canonical.queue_fingerprint,
        settings=settings,
    )


def _find_output(
    state: LineState,
    settings: Settings,
) -> tuple[Path | None, ShortsQueueClipSpec | None]:
    """exact review lineage の最新 manifest 出力だけを返す。"""
    try:
        result = load_latest_shorts_queue_result(state.video_id, settings)
    except ShortsQueueError:
        return None, None
    if result is None:
        return None, None
    spec = next(
        (value for value in result.clip_specs if _spec_matches_lineage(value, state)),
        None,
    )
    if spec is None:
        return None, None
    item = next(
        (
            value
            for value in result.items
            if value.target_id == spec.target_id and value.status == "succeeded"
        ),
        None,
    )
    if item is None or item.output_path is None or not item.output_path.is_file():
        return None, spec
    return item.output_path, spec


_STEP_ICONS: dict[StepStatus, str] = {
    "done": "✅",
    "current": "◉",
    "locked": "🔒",
}
# 接続線「│」を記号（全角相当の幅を持つアイコン）の中心へ寄せるための余白。
# unsafe_allow_html は使わず、素の文字だけで揃える。
_CONNECTOR = " │"


def _render_line_stepper(items: Sequence[LineStepperItem]) -> None:
    """6 工程の縦ステッパーを、素の Streamlit ウィジェットと文字だけで描画する."""
    last_index = len(items)
    for item in items:
        icon = _STEP_ICONS[item.status]
        line = f"{icon}　{item.index}　{item.label}"
        if item.status == "current":
            line += "　← 今"
        st.markdown(line)
        if item.index < last_index:
            st.caption(_CONNECTOR)


def render_compact_line_status(
    state: LineState | None,
    daily: DailyLineSummary | None,
    *,
    title: str | None = None,
) -> None:
    """サイドバーの縦ステッパーと日次カウンタを描画する表示専用関数.

    ``state`` だけを工程判定の唯一の入力にすることで、メイン領域の
    縮約表示（``render_main_line_summary``）と同じ ``LineState`` から
    導出し、状態表示の矛盾（サイドバーとメインで異なる工程を示す）を防ぐ。
    """
    current_stage = state.current_stage if state is not None else None
    st.markdown("**作成中のショート**")
    if state is None:
        st.caption("まだ開始していません")
    else:
        st.caption(f"対象ショート: {_safe(title or state.clip_id)}")

    _render_line_stepper(line_stepper_items(current_stage))

    st.markdown("── 次にやること ──")
    st.write(line_next_action_text(current_stage))

    if daily is not None:
        st.caption(
            f"本日 {daily.completed_count}／{daily.target_count}　・　"
            f"要対応 {daily.needs_attention_count} 件"
        )


def load_daily_line_summary(settings: Settings) -> DailyLineSummary | None:
    try:
        return summarize_daily_lines(
            list_operations(settings),
            load_schedule_policy(settings),
            now=datetime.now(timezone.utc),
        )
    except (LineStateError, ScheduleError, UploadQueueError):
        return None


def build_sidebar_line_projection(
    video_id: str | None,
    settings: Settings,
) -> SidebarLineProjection:
    """Read durable state into a display model without touching session state."""
    daily = load_daily_line_summary(settings)
    if not video_id:
        return SidebarLineProjection(state=None, daily=daily)

    state = resolve_active_line_read_only(video_id, settings)
    if state is None:
        return SidebarLineProjection(state=None, daily=daily)

    material = _restore_material_context(state)
    target = material[2] if material is not None else None
    output_path, _spec = _find_output(state, settings)
    source_files = sorted(
        (settings.data_dir / video_id / "clips" / "source").glob("*.mp4")
    ) + sorted(
        (settings.data_dir / video_id / "clips" / "source").glob("*.mkv")
    )
    active_job = get_active_job(settings)
    generating = bool(
        active_job
        and active_job.status == "running"
        and active_job.video_id == video_id
        and active_job.kind in {"shorts", "shorts_queue"}
    )
    return sidebar_projection_from_target(
        state=state,
        daily=daily,
        target=target,
        output_path=output_path,
        source_path=source_files[0] if source_files else None,
        generation_running=generating,
    )


def render_sidebar_line_context(video_id: str | None, settings: Settings) -> None:
    """グローバルナビ下に表示専用の現在ラインを置く."""
    st.divider()
    try:
        projection = build_sidebar_line_projection(video_id, settings)
    except LineStateError:
        st.warning("ライン状態を安全に読み込めませんでした。")
        projection = SidebarLineProjection(
            state=None,
            daily=load_daily_line_summary(settings),
        )

    if projection.state is not None:
        if projection.preview_mode == "output" and projection.preview_path is not None:
            st.video(projection.preview_path, width=240)
        elif projection.preview_mode == "generating":
            st.info("ショートを生成中です。")
        elif projection.preview_mode == "source" and projection.preview_path is not None:
            if projection.preview_start_sec is not None:
                st.video(
                    projection.preview_path,
                    start_time=projection.preview_start_sec,
                    end_time=projection.preview_end_sec,
                    width=240,
                )
            else:
                st.video(projection.preview_path, width=240)
        else:
            st.warning("元素材がありません。取り込みで元動画を再取得してください。")
    render_compact_line_status(
        projection.state,
        projection.daily,
        title=projection.title,
    )


def render_main_line_summary(video_id: str, settings: Settings) -> None:
    """サイドバー折り畳み時にも残る表示専用の工程要約."""
    try:
        state = resolve_active_line_read_only(video_id, settings)
    except LineStateError:
        state = None
    if state is None:
        return
    active_job = get_active_job(settings)
    generating = bool(
        active_job
        and active_job.status == "running"
        and active_job.video_id == video_id
        and active_job.kind in {"shorts", "shorts_queue"}
    )
    prefix = "生成中・" if generating else ""
    st.caption(f"{prefix}工程 {stage_number(state.current_stage)}／6")


def _switch_to_publish_workspace(video_id: str) -> None:
    """widget callback 内で次 rerun の作業選択を更新する."""
    st.session_state[detail_workspace_key(video_id)] = "publish"


def record_line_upload(
    video_id: str,
    clip_id: str,
    operation_id: str,
    output_path: Path,
    settings: Settings,
) -> None:
    """P2 確定後の operation を現在出力の再検証付きで記録する."""
    state = load_line_state(video_id, clip_id, settings)
    if state is None:
        return
    updated = record_upload_operation(
        state,
        operation_id,
        output_path,
        settings=settings,
    )
    if updated.current_stage != LineStage.RESERVED:
        raise LineStateError(
            "完成動画が最終確認時から変わりました。もう一度プレビューしてください。"
        )


def run_line_upload_transaction(
    video_id: str,
    clip_id: str,
    output_path: Path,
    settings: Settings,
    start_upload: Callable[[], UploadOperation],
) -> UploadOperation:
    """投稿 side effect を exact line lock 内の検証・記録へ接続する。"""
    return run_line_reservation_transaction(
        video_id,
        clip_id,
        output_path,
        settings,
        start_upload,
    )


def validate_line_reservation(
    video_id: str,
    clip_id: str,
    output_path: Path,
    settings: Settings,
) -> None:
    """投稿 preview を開く前に最終確認済み出力との一致を固定する."""
    state = load_line_state(video_id, clip_id, settings)
    # S4 単体生成は従来 P2 導線を維持し、exact line がある item だけを gate する。
    if state is None:
        return
    checked = reconcile_output(state, output_path)
    if checked != state:
        save_line_state(checked, settings, expected_state=state)
    if (
        checked.output_fingerprint is None
        or checked.preview_confirmed_fingerprint != checked.output_fingerprint
    ):
        raise LineStateError(
            "完成動画が最終確認時から変わりました。もう一度プレビューしてください。"
        )


def make_line_upload_adapter(
    video_id: str,
    settings: Settings,
) -> LineUploadAdapter:
    """Bind both reservation safety callbacks to one source-video context."""

    def validate(clip_id: str, output_path: Path) -> None:
        validate_line_reservation(
            video_id,
            clip_id,
            output_path,
            settings,
        )

    def reserve(
        clip_id: str,
        output_path: Path,
        start_upload: Callable[[], UploadOperation],
    ) -> UploadOperation:
        return run_line_upload_transaction(
            video_id,
            clip_id,
            output_path,
            settings,
            start_upload,
        )

    return LineUploadAdapter(
        validate_preview=validate,
        reservation_transaction=reserve,
    )


def render_shorts_line(
    *,
    video_id: str,
    title: str,
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
    settings: Settings,
    preferred_candidate_keys: Sequence[CandidateKey] = (),
) -> None:
    """1 本の区間確定から予約導線までを工程として描画する."""
    try:
        state = resolve_active_line_read_only(video_id, settings)
    except LineStateError as exc:
        st.error(_safe(exc))
        return
    context = _context(video_id)
    if state is not None and context is None:
        context = _restore_context(video_id, state, settings)
    if state is not None and context is not None:
        context = _restore_stale_telop_context(
            video_id,
            state,
            context,
            settings,
        )
    lineage_current = True
    lineage_message = ""
    if state is not None:
        lineage_current, lineage_message = _inspect_artifact_lineage(state, settings)
        if not lineage_current and context is not None:
            context.pop("draft", None)
            context.pop("confirmed_spec", None)
            context["telop_error"] = lineage_message
            _save_context(video_id, context)

    if state is None:
        # 6 工程の進行表示はサイドバー（render_compact_line_status）へ一本化した。
        # ここで工程バーを描かないことで、ライン未開始時にメイン領域が
        # 「1.素材選定・進行中」のような矛盾した状態を示さないようにする。
        st.subheader("素材を選び、区間を決める")
        candidate_by_key: dict[CandidateKey, ClipCandidate | HighlightSegment] = {
            **{("clips", candidate.id): candidate for candidate in clip_candidates},
            **{
                ("highlights", candidate.id): candidate
                for candidate in highlight_candidates
            },
        }
        if not candidate_by_key:
            st.info("素材候補がありません。素材候補ワークスペースで生成してください。")
            return
        ordered_keys = ordered_candidate_keys(
            clip_candidates,
            highlight_candidates,
            preferred_candidate_keys,
        )
        preferred = tuple(
            key for key in preferred_candidate_keys if key in candidate_by_key
        )
        default_keys = list(preferred or ordered_keys[:1])
        selection_key = line_material_selection_key(video_id)
        transfer_marker_key = line_material_transfer_marker_key(video_id)
        stored_selection = st.session_state.get(selection_key)
        transfer_marker = tuple(preferred)
        if (
            st.session_state.get(transfer_marker_key) != transfer_marker
            or not isinstance(stored_selection, list)
            or any(value not in candidate_by_key for value in stored_selection)
        ):
            st.session_state.pop(selection_key, None)
            st.session_state[transfer_marker_key] = transfer_marker
        selected_keys = st.multiselect(
            "ショート作成対象（選択順）",
            ordered_keys,
            default=default_keys,
            format_func=lambda value: _candidate_handoff_label(
                candidate_by_key[value], value[0]
            ),
            key=selection_key,
            persist_state="session",
        )
        if not selected_keys:
            st.info("ショート作成対象を 1 件以上選択してください。")
            return
        st.caption(
            "選択順: "
            + " → ".join(
                f"{index}. {_candidate_handoff_label(candidate_by_key[candidate_key], candidate_key[0])}"
                for index, candidate_key in enumerate(selected_keys, start=1)
            )
        )
        target_key = line_material_target_key(video_id)
        if st.session_state.get(target_key) not in selected_keys:
            st.session_state[target_key] = selected_keys[0]
        selected_key = st.radio(
            "今回作る候補（1 本ずつ進めます）",
            selected_keys,
            format_func=lambda value: _candidate_handoff_label(
                candidate_by_key[value], value[0]
            ),
            key=target_key,
            persist_state="session",
        )
        selected = candidate_by_key[selected_key]
        if selected.duration_sec > 180:
            render_short_cut_section(
                video_id=video_id,
                title=title,
                clip_candidates=(selected,) if isinstance(selected, ClipCandidate) else (),
                highlight_candidates=(
                    (selected,) if isinstance(selected, HighlightSegment) else ()
                ),
                settings=settings,
                embedded=True,
                preferred_candidate_ids=(selected.id,),
                on_segments_confirmed=lambda segments, option: _start_line(
                    video_id=video_id,
                    title=title,
                    segments=segments,
                    option=option,
                    settings=settings,
                ),
            )
            return
        st.write(f"{_safe(selected.title)}（{selected.start} → {selected.end}）")
        if st.button("この区間を確定してテロップ確認へ", type="primary"):
            option = ParentOption(
                "切り抜き" if isinstance(selected, ClipCandidate) else "ハイライト",
                selected,
            )
            _start_line(
                video_id=video_id,
                title=title,
                segments=(_as_highlight(selected),),
                option=option,
                settings=settings,
            )
        return

    # 6 工程の進行表示はサイドバー（render_compact_line_status）へ一本化した。
    if context is None:
        st.error(
            "区間 snapshot を安全に復元できませんでした。"
            "候補を選び直し、ラインを再開してください。"
        )
        _render_line_recovery_actions(
            state,
            settings,
            retry=lambda: _restore_context(video_id, state, settings),
            retry_label="保存状態を再読み込み",
        )
        return
    target = context.get("target")
    defaults = context.get("defaults")
    draft = context.get("draft")
    if not isinstance(target, ShortsQueueTarget) or not isinstance(
        defaults, ShortsLineDefaults
    ):
        st.error("ラインの対象を安全に読み込めませんでした。")
        _render_line_recovery_actions(
            state,
            settings,
            retry=lambda: _restore_context(video_id, state, settings),
            retry_label="保存状態を再読み込み",
        )
        return
    if not lineage_current:
        target_ranges = "、".join(
            f"{segment.id} {segment.start_ms / 1000:.3f} → {segment.end_ms / 1000:.3f}秒"
            for segment in target.segments
        ) or "不明"
        st.error(f"{lineage_message} 対象 range: {target_ranges}。")
    st.caption(
        f"適用中: {'ぼかし背景' if defaults.layout == 'blur' else '中央クロップ'}"
        f"・{defaults.preset}・Hook {defaults.hook_preset}　設定で変更"
    )
    if context.get("telop_error"):
        st.error(_safe(context["telop_error"]))
    if not isinstance(draft, TelopScriptDocument):
        st.info("テロップ台本がありません。再生成するか、素材選定へ戻れます。")
        _render_line_recovery_actions(
            state,
            settings,
            retry=lambda: _generate_line_telop(
                video_id=video_id,
                target=target,
                state=state,
                context=context,
                settings=settings,
            ),
        )
        return

    _render_telop_provenance_header(draft)
    edited = _editor_document(
        draft,
        video_id=video_id,
        clip_id=target.target_id,
        queue_fingerprint=state.queue_fingerprint,
    )
    validation = validate_telop_script(
        edited,
        segments=[segment.to_tuple() for segment in target.segments],
    )
    persisted_state = state
    try:
        review_fingerprint = make_review_fingerprint(
            video_id,
            target.target_id,
            state.queue_fingerprint,
            edited,
        )
        state = _project_editor_review_state(state, review_fingerprint)
    except LineStateError as exc:
        st.error(_safe(exc))
        return
    gate = evaluate_telop_gate(
        validation.errors,
        validation.warnings,
        review_fingerprint,
        state.review_confirmed_fingerprint,
    )
    with st.container(horizontal=True):
        if gate.hard_valid:
            st.success("自動ハード判定: 通過")
        else:
            st.error("自動ハード判定: 要修正")
        if gate.warnings:
            st.warning(f"自動警告: {len(gate.warnings)} 件")
        else:
            st.success("自動警告: なし")
    for error in gate.hard_errors:
        st.error(_safe(error))
    for warning in gate.warnings:
        st.warning(_safe(warning))

    spec = _current_context_spec(video_id, context, state)
    review_was_invalidated = bool(
        st.session_state.get(
            line_review_invalidated_key(video_id, target.target_id),
            False,
        )
    )
    check_key = (
        f"line_human_check_{video_id}_{target.target_id}_{review_fingerprint}_"
        f"{'edited' if review_was_invalidated else 'current'}"
    )
    human_checked = st.checkbox(
        "台本全体の誤字・固有名詞を確認した",
        value=is_human_review_current(state, review_fingerprint),
        key=check_key,
        disabled=not gate.hard_valid or gate.can_generate,
        persist_state="session",
    )
    confirm_requested = st.button(
        "確認内容を確定して生成条件を保存",
        key=f"line_review_commit_{video_id}_{target.target_id}_{review_fingerprint}",
        type="primary",
        disabled=(
            not gate.hard_valid
            or not human_checked
            or (gate.can_generate and isinstance(spec, ShortsQueueClipSpec))
        ),
    )
    if confirm_requested:
        try:
            state, spec, confirmed_document = _commit_telop_review(
                persisted_state=persisted_state,
                projected_state=state,
                review_fingerprint=review_fingerprint,
                hard_errors=validation.errors,
                edited=edited,
                target=target,
                defaults=defaults,
                settings=settings,
            )
        except (LineStateError, TelopError, ShortsQueueError) as exc:
            st.error(_safe(exc))
        else:
            context["confirmed_spec"] = spec
            context["draft"] = confirmed_document
            context.pop("telop_error", None)
            _save_context(video_id, context)
            install_line_confirmed_spec(video_id, spec)
            _mark_editor_review_committed(state)
            st.rerun()
            return

    output_path, manifest_spec = _find_output(state, settings)
    if isinstance(manifest_spec, ShortsQueueClipSpec):
        spec = manifest_spec
    if output_path is not None:
        try:
            state = (
                record_output(state, output_path)
                if state.output_fingerprint is None
                else reconcile_output(state, output_path)
            )
        except LineStateError as exc:
            st.error(_safe(exc))

    if output_path is None:
        st.caption("生成条件: ハード判定通過 + 人確認済み + fingerprint 一致")
        if st.button(
            "台本を確定して生成へ",
            type="primary",
            disabled=not gate.can_generate or not isinstance(spec, ShortsQueueClipSpec),
        ):
            assert isinstance(spec, ShortsQueueClipSpec)
            # 生成直前も現在値で再検証する。
            latest_validation = validate_telop_script(
                edited,
                segments=[segment.to_tuple() for segment in target.segments],
            )
            latest_review_fingerprint = make_review_fingerprint(
                video_id,
                target.target_id,
                state.queue_fingerprint,
                edited,
            )
            latest_gate = evaluate_telop_gate(
                latest_validation.errors,
                latest_validation.warnings,
                latest_review_fingerprint,
                state.review_confirmed_fingerprint,
            )
            if not latest_gate.can_generate:
                st.error("台本が変更されました。もう一度全文を確認してください。")
            else:
                try:
                    _start_line_generation_command(
                        displayed_state=persisted_state,
                        review_fingerprint=latest_review_fingerprint,
                        spec=spec,
                        video_id=video_id,
                        title=title,
                        settings=settings,
                    )
                except LineStateError as exc:
                    st.error(_safe(exc))
        return

    st.subheader("完成動画を最終確認")
    if not lineage_current:
        st.error(
            "最終確認 banner: 高精度字幕の失効理由を確認してください。"
            f"対象 clip: {state.clip_id}。次 gate: 台本を再確認してください。"
        )
    elif state.artifact_ref is None:
        st.warning(
            "最終確認 banner: coarse VTT fallback です。"
            "高精度成功とは表示せず、境界を人が確認してください。"
        )
    else:
        st.info("最終確認 banner: refined artifact lineage を使用しています。")
    with st.container(width=360):
        st.video(output_path)
    preview_current = (
        state.output_fingerprint is not None
        and state.preview_confirmed_fingerprint == state.output_fingerprint
    )
    if not preview_current:
        if st.button("完成動画を確認して予約へ", type="primary"):
            try:
                state = _confirm_line_preview(
                    persisted_state,
                    output_path,
                    settings,
                )
            except LineStateError as exc:
                st.error(_safe(exc))
            else:
                st.rerun()
        return
    st.success("完成動画の最終確認済み")
    st.button(
        "公開・投稿で予約する",
        type="primary",
        on_click=_switch_to_publish_workspace,
        args=(video_id,),
    )
