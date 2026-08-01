"""FR-33 のショート生産ラインを既存 S4 / P2 境界へ接続する UI."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopLine, TelopScriptDocument, TelopSegmentScript
from yt_live_kit.services.ai_prompt import AiPromptError
from yt_live_kit.services.jobs import get_active_job
from yt_live_kit.services.schedule import ScheduleError, load_schedule_policy
from yt_live_kit.services.shorts_line import (
    DailyLineSummary,
    LineStage,
    LineState,
    LineStateError,
    confirm_preview,
    confirm_review,
    create_line_state,
    evaluate_telop_gate,
    record_output,
    record_upload_operation,
    reconcile_output,
    resolve_active_line,
    save_active_line,
    save_line_state,
    set_review_fingerprint,
    summarize_daily_lines,
    make_review_fingerprint,
)
from yt_live_kit.services.shorts_queue import (
    ShortsQueueClipSpec,
    ShortsQueueError,
    ShortsQueueTarget,
    load_latest_shorts_queue_result,
    make_shorts_queue_clip_spec,
)
from yt_live_kit.services.telop import (
    TelopError,
    generate_telop_script,
    save_confirmed_telop_script,
    validate_telop_script,
)
from yt_live_kit.services.upload_queue import UploadQueueError, list_operations
from yt_live_kit.ui.components.short_cut import ParentOption, render_short_cut_section
from yt_live_kit.ui.components.shorts_queue import (
    install_line_confirmed_spec,
    install_line_snapshot,
    start_or_confirm_line_generation,
)
from yt_live_kit.ui.views._local_settings import (
    ShortsLineDefaults,
    load_shorts_line_defaults,
)

_STAGES: tuple[LineStage, ...] = (
    LineStage.MATERIAL_SELECTION,
    LineStage.SEGMENT_DECISION,
    LineStage.TELOP_REVIEW,
    LineStage.GENERATION,
    LineStage.FINAL_REVIEW,
    LineStage.RESERVATION,
)
_STAGE_LABELS = {
    LineStage.MATERIAL_SELECTION: "素材選定",
    LineStage.SEGMENT_DECISION: "区間決定",
    LineStage.TELOP_REVIEW: "テロップ確認",
    LineStage.GENERATION: "生成",
    LineStage.FINAL_REVIEW: "最終確認",
    LineStage.RESERVATION: "予約",
    LineStage.RESERVED: "予約済み",
}
_NEXT_ACTIONS = {
    LineStage.MATERIAL_SELECTION: "候補を選ぶ",
    LineStage.SEGMENT_DECISION: "区間の文字起こしと境界を確認",
    LineStage.TELOP_REVIEW: "台本全体を確認",
    LineStage.GENERATION: "確認済み台本から動画を生成",
    LineStage.FINAL_REVIEW: "完成動画をプレビュー",
    LineStage.RESERVATION: "投稿内容を確認して予約",
    LineStage.RESERVED: "予約完了",
}
_SESSION_PREFIX = "shorts_line_context"
PreviewMode = Literal["source", "generating", "output", "source_missing"]


def _safe(value: object) -> str:
    return str(value).replace("<", "〈").replace(">", "〉")


def stage_number(stage: LineStage) -> int:
    """完了状態を含む工程番号を返す."""
    if stage == LineStage.RESERVED:
        return 6
    return _STAGES.index(stage) + 1


def choose_preview_mode(
    *,
    output_available: bool,
    generation_running: bool,
    source_available: bool,
) -> PreviewMode:
    """左プレビューの 4 状態を副作用なく選ぶ."""
    if output_available:
        return "output"
    if generation_running:
        return "generating"
    if source_available:
        return "source"
    return "source_missing"


def _context_key(video_id: str) -> str:
    return f"{_SESSION_PREFIX}_{video_id}"


def _context(video_id: str) -> dict[str, object] | None:
    raw = st.session_state.get(_context_key(video_id))
    return raw if isinstance(raw, dict) else None


def _save_context(video_id: str, value: dict[str, object]) -> None:
    st.session_state[_context_key(video_id)] = value


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


def _start_line(
    *,
    video_id: str,
    title: str,
    segments: Sequence[HighlightSegment],
    option: ParentOption,
    settings: Settings,
) -> None:
    """区間確定を既存 queue snapshot と永続 line state へ移す."""
    defaults = load_shorts_line_defaults(settings)
    try:
        target, queue_fingerprint = install_line_snapshot(
            video_id=video_id,
            source=_source_for(option),
            original_candidate=option.candidate,
            segments=segments,
            layout=defaults.layout,
            preset=defaults.preset,
            hook_preset=defaults.hook_preset,
        )
        state = create_line_state(video_id, target.target_id, queue_fingerprint)
        save_line_state(state, settings)
        save_active_line(video_id, target.target_id, settings)
    except (LineStateError, ShortsQueueError, TelopError) as exc:
        st.error(_safe(exc))
        return

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
    try:
        generated = generate_telop_script(
            video_id,
            target.highlight_segments(),
            settings,
        )
        if generated.document is None:
            raise TelopError("テロップ台本を生成できませんでした。")
        review = make_review_fingerprint(
            video_id,
            target.target_id,
            queue_fingerprint,
            generated.document,
        )
        state = set_review_fingerprint(state, review)
        save_line_state(state, settings)
        context["draft"] = generated.document
        _save_context(video_id, context)
    except (AiPromptError, LineStateError) as exc:
        context["telop_error"] = str(exc)
        _save_context(video_id, context)
    st.rerun()


def _restore_context(
    video_id: str,
    state: LineState,
    settings: Settings,
) -> dict[str, object] | None:
    """検証済み manifest から機械的に証明できる対象だけを復元する."""
    try:
        result = load_latest_shorts_queue_result(video_id, settings)
    except ShortsQueueError:
        return None
    if result is None:
        return None
    spec = next(
        (value for value in result.clip_specs if value.target_id == state.clip_id),
        None,
    )
    if spec is None:
        return None
    target = ShortsQueueTarget(spec.target_id, spec.segments, spec.output_name)
    context: dict[str, object] = {
        "title": video_id,
        "target": target,
        "queue_fingerprint": state.queue_fingerprint,
        "defaults": ShortsLineDefaults(spec.layout, spec.preset, spec.hook_preset),
        "draft": spec.telop_document,
        "confirmed_spec": spec,
    }
    _save_context(video_id, context)
    return context


def _editor_document(
    draft: TelopScriptDocument,
    *,
    video_id: str,
    clip_id: str,
) -> TelopScriptDocument:
    prefix = f"line_editor_{video_id}_{clip_id}"
    st.session_state.setdefault(f"{prefix}_hook", draft.hook_text)
    st.session_state.setdefault(
        f"{prefix}_titles", "\n".join(draft.title_candidates)
    )
    st.session_state.setdefault(f"{prefix}_description", draft.description)
    st.session_state.setdefault(f"{prefix}_tags", ",".join(draft.tags))
    st.text_input("フック文言", key=f"{prefix}_hook")
    st.text_area("タイトル案（1 行 1 件）", key=f"{prefix}_titles")
    st.text_area("説明文", key=f"{prefix}_description")
    st.text_input("タグ（カンマ区切り）", key=f"{prefix}_tags")

    segments: list[TelopSegmentScript] = []
    for segment_index, segment in enumerate(draft.segments):
        st.markdown(f"**区間 {segment_index + 1}**")
        lines: list[TelopLine] = []
        for line_index, line in enumerate(segment.lines):
            line_prefix = f"{prefix}_{segment_index}_{line_index}"
            st.session_state.setdefault(f"{line_prefix}_text", line.text)
            st.session_state.setdefault(f"{line_prefix}_start", line.start_sec)
            st.session_state.setdefault(f"{line_prefix}_end", line.end_sec)
            st.session_state.setdefault(f"{line_prefix}_emphasis", line.emphasis)
            with st.container(border=True):
                st.text_input("テロップ本文", key=f"{line_prefix}_text")
                time_columns = st.columns(2)
                time_columns[0].number_input(
                    "開始秒",
                    step=0.1,
                    format="%.3f",
                    key=f"{line_prefix}_start",
                )
                time_columns[1].number_input(
                    "終了秒",
                    step=0.1,
                    format="%.3f",
                    key=f"{line_prefix}_end",
                )
                st.toggle("行全体を強調", key=f"{line_prefix}_emphasis")
                if (
                    str(st.session_state[f"{line_prefix}_text"]) != line.text
                    or bool(st.session_state[f"{line_prefix}_emphasis"])
                    != line.emphasis
                ):
                    st.caption("AI案から変更")
            lines.append(
                TelopLine(
                    text=str(st.session_state[f"{line_prefix}_text"]),
                    start_sec=float(st.session_state[f"{line_prefix}_start"]),
                    end_sec=float(st.session_state[f"{line_prefix}_end"]),
                    emphasis=bool(st.session_state[f"{line_prefix}_emphasis"]),
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
        hook_text=str(st.session_state[f"{prefix}_hook"]),
        title_candidates=tuple(
            line
            for line in str(st.session_state[f"{prefix}_titles"]).splitlines()
            if line.strip()
        ),
        description=str(st.session_state[f"{prefix}_description"]),
        tags=tuple(
            value.strip()
            for value in str(st.session_state[f"{prefix}_tags"]).split(",")
            if value.strip()
        ),
        segments=segments,
    )


def _find_output(
    video_id: str,
    clip_id: str,
    settings: Settings,
) -> tuple[Path | None, ShortsQueueClipSpec | None]:
    try:
        result = load_latest_shorts_queue_result(video_id, settings)
    except ShortsQueueError:
        return None, None
    if result is None:
        return None, None
    spec = next((value for value in result.clip_specs if value.target_id == clip_id), None)
    item = next(
        (
            value
            for value in result.items
            if value.target_id == clip_id and value.status == "succeeded"
        ),
        None,
    )
    if item is None or item.output_path is None or not item.output_path.is_file():
        return None, spec
    return item.output_path, spec


def render_stage_bar(stage: LineStage) -> None:
    """現在工程と通過済みゲートを 1 本の縮約表示で描画する."""
    current = stage_number(stage)
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        for index, value in enumerate(_STAGES, start=1):
            if index < current or stage == LineStage.RESERVED:
                marker = "完了"
            elif index == current:
                marker = "進行中"
            else:
                marker = "待機中"
            st.badge(f"{index}. {_STAGE_LABELS[value]}・{marker}")


def render_compact_line_status(
    state: LineState | None,
    daily: DailyLineSummary | None,
    *,
    title: str | None = None,
) -> None:
    """左パネルと狭幅代替で共有する表示専用の縮約状態."""
    st.markdown("**作成中のショート**")
    if state is None:
        st.caption("作成中のラインはありません。")
    else:
        st.caption(_safe(title or state.clip_id))
        st.write(
            f"工程 {stage_number(state.current_stage)}／6 "
            f"{_STAGE_LABELS[state.current_stage]}"
        )
        st.caption(f"次: {_NEXT_ACTIONS[state.current_stage]}")
    if daily is not None:
        st.write(f"本日のライン完了 {daily.completed_count}／{daily.target_count}")
        st.caption(f"要対応 {daily.needs_attention_count} 件")


def load_daily_line_summary(settings: Settings) -> DailyLineSummary | None:
    try:
        return summarize_daily_lines(
            list_operations(settings),
            load_schedule_policy(settings),
            now=datetime.now(timezone.utc),
        )
    except (LineStateError, ScheduleError, UploadQueueError):
        return None


def render_sidebar_line_context(video_id: str | None, settings: Settings) -> None:
    """グローバルナビ下に表示専用の現在ラインを置く."""
    st.divider()
    state: LineState | None = None
    if video_id:
        try:
            state = resolve_active_line(video_id, settings)
        except LineStateError:
            st.warning("ライン状態を安全に復元できませんでした。")
    if video_id and state is not None:
        output_path, _spec = _find_output(video_id, state.clip_id, settings)
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
        mode = choose_preview_mode(
            output_available=output_path is not None,
            generation_running=generating,
            source_available=bool(source_files),
        )
        if mode == "output" and output_path is not None:
            st.video(output_path, width=240)
        elif mode == "generating":
            st.info("ショートを生成中です。進捗は画面上部で確認できます。")
        elif mode == "source" and source_files:
            context = _context(video_id)
            target = context.get("target") if context else None
            if isinstance(target, ShortsQueueTarget):
                st.video(
                    source_files[0],
                    start_time=int(target.segments[0].start_ms / 1000),
                    end_time=int(target.segments[-1].end_ms / 1000),
                    width=240,
                )
            else:
                st.video(source_files[0], width=240)
        else:
            st.warning("元素材がありません。取り込みで元動画を再取得してください。")
    render_compact_line_status(state, load_daily_line_summary(settings))


def record_line_upload(
    video_id: str,
    clip_id: str,
    operation_id: str,
    output_path: Path,
    settings: Settings,
) -> None:
    """P2 確定後の operation を現在出力の再検証付きで記録する."""
    state = resolve_active_line(video_id, settings)
    if state is None or state.clip_id != clip_id:
        return
    updated = record_upload_operation(state, operation_id, output_path)
    save_line_state(updated, settings)


def render_shorts_line(
    *,
    video_id: str,
    title: str,
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
    settings: Settings,
    preferred_candidate_ids: Sequence[str] = (),
) -> None:
    """1 本の区間確定から予約導線までを工程として描画する."""
    try:
        state = resolve_active_line(video_id, settings)
    except LineStateError as exc:
        st.error(_safe(exc))
        return
    context = _context(video_id)
    if state is not None and context is None:
        context = _restore_context(video_id, state, settings)

    if state is None:
        render_stage_bar(LineStage.MATERIAL_SELECTION)
        st.subheader("素材を選び、区間を決める")
        long_candidates = [
            value
            for value in (*clip_candidates, *highlight_candidates)
            if value.duration_sec > 180
        ]
        if long_candidates:
            render_short_cut_section(
                video_id=video_id,
                title=title,
                clip_candidates=clip_candidates,
                highlight_candidates=highlight_candidates,
                settings=settings,
                embedded=True,
                preferred_candidate_ids=preferred_candidate_ids,
                on_segments_confirmed=lambda segments, option: _start_line(
                    video_id=video_id,
                    title=title,
                    segments=segments,
                    option=option,
                    settings=settings,
                ),
            )
            return
        candidates = [*clip_candidates, *highlight_candidates]
        if not candidates:
            st.info("素材候補がありません。素材候補ワークスペースで生成してください。")
            return
        selected = next(
            (value for value in candidates if value.id in preferred_candidate_ids),
            candidates[0],
        )
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

    render_stage_bar(state.current_stage)
    render_compact_line_status(state, load_daily_line_summary(settings), title=title)
    if context is None:
        st.error(
            "区間 snapshot を安全に復元できませんでした。"
            "候補を選び直し、ラインを再開してください。"
        )
        return
    target = context.get("target")
    defaults = context.get("defaults")
    draft = context.get("draft")
    if not isinstance(target, ShortsQueueTarget) or not isinstance(
        defaults, ShortsLineDefaults
    ):
        st.error("ラインの対象を安全に読み込めませんでした。")
        return
    st.caption(
        f"適用中: {'ぼかし背景' if defaults.layout == 'blur' else '中央クロップ'}"
        f"・{defaults.preset}・Hook {defaults.hook_preset}　設定で変更"
    )
    if context.get("telop_error"):
        st.error(_safe(context["telop_error"]))
    if not isinstance(draft, TelopScriptDocument):
        st.info("テロップ台本がありません。区間を選び直して生成してください。")
        return

    edited = _editor_document(draft, video_id=video_id, clip_id=target.target_id)
    validation = validate_telop_script(
        edited,
        segments=[segment.to_tuple() for segment in target.segments],
    )
    try:
        review_fingerprint = make_review_fingerprint(
            video_id,
            target.target_id,
            state.queue_fingerprint,
            edited,
        )
        if review_fingerprint != state.review_fingerprint:
            state = set_review_fingerprint(state, review_fingerprint)
            save_line_state(state, settings)
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

    check_key = f"line_human_check_{video_id}_{target.target_id}_{state.updated_at.timestamp()}"
    human_checked = st.checkbox(
        "台本全体の誤字・固有名詞を確認した",
        value=gate.human_confirmed and gate.fingerprint_current,
        key=check_key,
        disabled=not gate.hard_valid,
    )
    if human_checked and not gate.can_generate:
        try:
            state = confirm_review(
                state,
                review_fingerprint,
                hard_errors=validation.errors,
            )
            save_line_state(state, settings)
        except LineStateError as exc:
            st.error(_safe(exc))
        else:
            st.rerun()

    gate = evaluate_telop_gate(
        validation.errors,
        validation.warnings,
        review_fingerprint,
        state.review_confirmed_fingerprint,
    )
    spec = context.get("confirmed_spec")
    if gate.can_generate and not isinstance(spec, ShortsQueueClipSpec):
        try:
            saved = save_confirmed_telop_script(
                video_id,
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
            context["confirmed_spec"] = spec
            context["draft"] = saved.document
            _save_context(video_id, context)
            install_line_confirmed_spec(video_id, spec)
        except (TelopError, ShortsQueueError) as exc:
            st.error(_safe(exc))

    output_path, manifest_spec = _find_output(video_id, target.target_id, settings)
    if isinstance(manifest_spec, ShortsQueueClipSpec):
        spec = manifest_spec
    if output_path is not None:
        try:
            state = (
                record_output(state, output_path)
                if state.output_fingerprint is None
                else reconcile_output(state, output_path)
            )
            save_line_state(state, settings)
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
            latest_gate = evaluate_telop_gate(
                latest_validation.errors,
                latest_validation.warnings,
                make_review_fingerprint(
                    video_id,
                    target.target_id,
                    state.queue_fingerprint,
                    edited,
                ),
                state.review_confirmed_fingerprint,
            )
            if not latest_gate.can_generate:
                st.error("台本が変更されました。もう一度全文を確認してください。")
            else:
                start_or_confirm_line_generation(
                    video_id=video_id,
                    title=title,
                    spec=spec,
                    snapshot_fingerprint=state.queue_fingerprint,
                    settings=settings,
                )
        return

    st.subheader("完成動画を最終確認")
    with st.container(width=360):
        st.video(output_path)
    preview_current = (
        state.output_fingerprint is not None
        and state.preview_confirmed_fingerprint == state.output_fingerprint
    )
    if not preview_current:
        if st.button("完成動画を確認して予約へ", type="primary"):
            try:
                state = confirm_preview(state, output_path)
                save_line_state(state, settings)
            except LineStateError as exc:
                st.error(_safe(exc))
            else:
                st.rerun()
        return
    st.success("完成動画の最終確認済み")
    if st.button("公開・投稿で予約する", type="primary"):
        st.session_state[f"detail_workspace_{video_id}"] = "publish"
        st.rerun()
