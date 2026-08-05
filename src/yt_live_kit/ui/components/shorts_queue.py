"""ショート量産の選択・台本確認・結果表示 UI."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.models.transcript import TranscriptArtifactRef
from yt_live_kit.services.ai_prompt import AiPromptError
from yt_live_kit.services.clips import load_candidates_file
from yt_live_kit.services.ffmpeg import FfmpegError, ensure_subtitles_filter
from yt_live_kit.services.highlights import load_segments_file
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.shorts_queue import (
    ShortsQueueClipSpec,
    ShortsQueueError,
    ShortsQueueResult,
    ShortsQueueTarget,
    build_shorts_queue_targets,
    can_start_shorts_queue,
    load_latest_shorts_queue_result,
    load_shorts_queue_result,
    make_shorts_queue_clip_spec,
    make_shorts_queue_fingerprint,
    normalize_queue_candidates,
    run_shorts_queue_job_target,
    select_queue_candidates_by_id,
)
from yt_live_kit.services.subtitle_burn import TELOP_PRESETS
from yt_live_kit.services.transcript_artifact import TranscriptArtifactError, TranscriptArtifactStore
from yt_live_kit.services.telop import (
    TelopError,
    generate_telop_script,
    save_confirmed_telop_script,
)
from yt_live_kit.ui.components.clipboard import render_copy_button
from yt_live_kit.ui.state import get_selected_video_id, set_active_job_id

_JOB_IDS_KEY = "shorts_queue_job_ids"
_SOURCE_LABELS = {"clips": "切り抜き候補", "highlights": "ハイライト候補"}
_MODE_LABELS = {"individual": "個別", "concat": "連結"}
_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"


@dataclass(frozen=True)
class PreparedLineSnapshot:
    """Pure line-mode session projection installed after durable commit."""

    video_id: str
    target: ShortsQueueTarget
    fingerprint: str
    payload: Mapping[str, Any]


def _safe_text(value: object) -> str:
    return str(value).replace("<", "〈").replace(">", "〉")


def _state_key(video_id: str, name: str) -> str:
    return f"shorts_queue_{video_id}_{name}"


def _clear_snapshot(video_id: str) -> None:
    for name in ("snapshot", "drafts", "confirmed", "errors"):
        st.session_state.pop(_state_key(video_id, name), None)


def _job_ids() -> dict[str, str]:
    raw = st.session_state.get(_JOB_IDS_KEY)
    if not isinstance(raw, dict):
        raw = {}
        st.session_state[_JOB_IDS_KEY] = raw
    return raw


def _snapshot(video_id: str) -> dict[str, Any] | None:
    value = st.session_state.get(_state_key(video_id, "snapshot"))
    return value if isinstance(value, dict) else None


def _dict_state(video_id: str, name: str) -> dict[str, Any]:
    key = _state_key(video_id, name)
    value = st.session_state.get(key)
    if not isinstance(value, dict):
        value = {}
        st.session_state[key] = value
    return value


def _candidate_label(candidate: ClipCandidate | HighlightSegment) -> str:
    return _safe_text(
        f"{candidate.start} → {candidate.end} / {candidate.duration_sec} 秒 / "
        f"{candidate.title}"
    )


def prepare_line_snapshot(
    *,
    video_id: str,
    source: str,
    original_candidate: ClipCandidate | HighlightSegment,
    segments: Sequence[HighlightSegment],
    layout: str,
    preset: str,
    hook_preset: str,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
) -> PreparedLineSnapshot:
    """Build the exact S4 line-mode projection without mutating session state."""
    normalized = normalize_queue_candidates(segments, source="highlights")
    targets = build_shorts_queue_targets(normalized, mode="concat")
    fingerprint = make_shorts_queue_fingerprint(
        video_id=video_id,
        source=source,
        mode="concat",
        original_candidates=(original_candidate,),
        segments=normalized,
        layout=layout,
        preset=preset,
        hook_preset=hook_preset,
    )
    payload = {
        "line_mode": True,
        "fingerprint": fingerprint,
        "source": source,
        "mode": "concat",
        "layout": layout,
        "preset": preset,
        "hook_preset": hook_preset,
        "targets": targets,
        "original_candidates": (original_candidate,),
        "selected_ids": (original_candidate.id,),
        "segments": normalized,
        "artifact_ref": artifact_ref,
        "artifact_fingerprint": artifact_fingerprint,
        "used_range_cue_digests": tuple(used_range_cue_digests),
    }
    return PreparedLineSnapshot(video_id, targets[0], fingerprint, payload)


def install_prepared_line_snapshot(prepared: PreparedLineSnapshot) -> None:
    """Install a previously validated projection after durable line commit."""
    if not isinstance(prepared, PreparedLineSnapshot):
        raise ShortsQueueError("ライン snapshot の入力が正しくありません。")
    _clear_snapshot(prepared.video_id)
    st.session_state[_state_key(prepared.video_id, "snapshot")] = dict(
        prepared.payload
    )


def install_line_snapshot(
    *,
    video_id: str,
    source: str,
    original_candidate: ClipCandidate | HighlightSegment,
    segments: Sequence[HighlightSegment],
    layout: str,
    preset: str,
    hook_preset: str,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
) -> tuple[ShortsQueueTarget, str]:
    """Compatibility wrapper that prepares and immediately installs a snapshot."""
    prepared = prepare_line_snapshot(
        video_id=video_id,
        source=source,
        original_candidate=original_candidate,
        segments=segments,
        layout=layout,
        preset=preset,
        hook_preset=hook_preset,
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        used_range_cue_digests=used_range_cue_digests,
    )
    install_prepared_line_snapshot(prepared)
    return prepared.target, prepared.fingerprint


def install_line_confirmed_spec(
    video_id: str,
    spec: ShortsQueueClipSpec,
) -> None:
    """工程 UI で確認した spec を既存 S4 の確認済み集合へ登録する."""
    _dict_state(video_id, "confirmed")[spec.target_id] = spec


def clear_line_confirmed_spec(video_id: str, target_id: str) -> None:
    """台本 fingerprint 変更時に旧 S4 spec を生成候補から外す。"""
    _dict_state(video_id, "confirmed").pop(target_id, None)


def clear_line_snapshot(video_id: str) -> None:
    """破棄を確認した正式ラインの session snapshot だけを消す。"""
    _clear_snapshot(video_id)


def restore_line_snapshot(
    *,
    video_id: str,
    source: str,
    original_candidate: ClipCandidate | HighlightSegment,
    target: ShortsQueueTarget,
    layout: str,
    preset: str,
    hook_preset: str,
    expected_fingerprint: str,
    confirmed_spec: ShortsQueueClipSpec | None = None,
    artifact_ref: TranscriptArtifactRef | None = None,
    artifact_fingerprint: str | None = None,
    used_range_cue_digests: Sequence[str] = (),
) -> None:
    """永続 material evidence と一致する exact line-mode S4 state を復元する。"""
    restored_target, fingerprint = install_line_snapshot(
        video_id=video_id,
        source=source,
        original_candidate=original_candidate,
        segments=target.highlight_segments(),
        layout=layout,
        preset=preset,
        hook_preset=hook_preset,
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        used_range_cue_digests=used_range_cue_digests,
    )
    if restored_target != target or fingerprint != expected_fingerprint:
        _clear_snapshot(video_id)
        raise ShortsQueueError(
            "保存済みラインの生成 snapshot が queue fingerprint と一致しません。"
        )
    if confirmed_spec is not None:
        if (
            confirmed_spec.target_id != target.target_id
            or confirmed_spec.segments != target.segments
            or confirmed_spec.output_name != target.output_name
        ):
            _clear_snapshot(video_id)
            raise ShortsQueueError("保存済み生成 spec がライン対象と一致しません。")
        if (
            confirmed_spec.artifact_ref != artifact_ref
            or confirmed_spec.artifact_fingerprint != artifact_fingerprint
            or tuple(confirmed_spec.used_range_cue_digests) != tuple(used_range_cue_digests)
        ):
            _clear_snapshot(video_id)
            raise ShortsQueueError("保存済み生成 spec の artifact lineage がラインと一致しません。")
        install_line_confirmed_spec(video_id, confirmed_spec)


def _store_job_id(video_id: str, job_id: str) -> None:
    values = dict(_job_ids())
    values[video_id] = job_id
    st.session_state[_JOB_IDS_KEY] = values


def _start_queue_job(
    *,
    video_id: str,
    title: str,
    specs: Sequence[ShortsQueueClipSpec],
    settings: Settings,
    resume_job_id: str | None = None,
) -> None:
    """単一ジョブを開始し、返却 ID を現在動画へ紐付ける."""
    if is_busy(settings):
        st.error(_BUSY_MESSAGE)
        return
    try:
        ensure_subtitles_filter(settings.ffmpeg_path)
    except FfmpegError as exc:
        st.error(_safe_text(exc))
        return
    try:
        job_kwargs: dict[str, object] = {
            "video_id": video_id,
            "title": title,
            "total": len(specs),
            "settings": settings,
            "clip_spec_dicts": [spec.to_dict() for spec in specs],
        }
        if resume_job_id is not None:
            job_kwargs["resume_job_id"] = resume_job_id
        job_id = start_job(
            "shorts_queue",
            run_shorts_queue_job_target,
            **job_kwargs,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    _store_job_id(video_id, job_id)
    set_active_job_id(job_id)
    st.rerun()


def _validate_overwrite_confirmation(
    *,
    video_id: str,
    specs: tuple[ShortsQueueClipSpec, ...],
    snapshot_fingerprint: str,
    existing_names: tuple[str, ...],
    settings: Settings,
) -> tuple[bool, str | None]:
    """dialog fragment 内で現在状態を再取得し、stale な開始を拒否する."""
    if get_selected_video_id() != video_id:
        return False, "表示中の動画が変わりました。確認画面を閉じてやり直してください。"
    snapshot = _snapshot(video_id)
    if snapshot is None or snapshot.get("fingerprint") != snapshot_fingerprint:
        return False, "選択内容が変わりました。台本確認からやり直してください。"
    if snapshot.get("line_mode") is True:
        current_fingerprint = str(snapshot.get("fingerprint") or "")
        current_targets = tuple(snapshot.get("targets", ()))
    else:
        try:
            source = str(snapshot["source"])
            if source == "clips":
                document = load_candidates_file(video_id, settings)
            elif source == "highlights":
                document = load_segments_file(video_id, settings)
            else:
                return False, "候補ソースが変わりました。選択し直してください。"
            if document is None:
                return False, "候補ファイルが見つかりません。選択し直してください。"
            current_selected = select_queue_candidates_by_id(
                document.candidates,
                tuple(snapshot["selected_ids"]),
            )
            current_segments = normalize_queue_candidates(
                current_selected,
                source=source,
            )
            current_fingerprint = make_shorts_queue_fingerprint(
                video_id=video_id,
                source=source,
                mode=str(snapshot["mode"]),
                original_candidates=current_selected,
                segments=current_segments,
                layout=str(snapshot["layout"]),
                preset=str(snapshot["preset"]),
                hook_preset=str(snapshot["hook_preset"]),
            )
            current_targets = build_shorts_queue_targets(
                current_segments,
                mode=str(snapshot["mode"]),
            )
        except (ShortsQueueError, TelopError, OSError, ValueError) as exc:
            return False, _safe_text(exc)
    target_ids = tuple(target.target_id for target in current_targets)
    snapshot_targets = tuple(snapshot.get("targets", ()))
    if target_ids != tuple(target.target_id for target in snapshot_targets):
        return False, "生成対象が変わりました。台本確認からやり直してください。"
    confirmed = _dict_state(video_id, "confirmed")
    allowed, reason = can_start_shorts_queue(
        expected_fingerprint=snapshot_fingerprint,
        current_fingerprint=current_fingerprint,
        target_ids=target_ids,
        confirmed_specs=confirmed,
        busy=is_busy(settings),
    )
    if not allowed:
        return False, reason
    current_specs = tuple(confirmed[target_id] for target_id in target_ids)
    if current_specs != specs:
        return False, "確定済み台本が変わりました。確認画面を開き直してください。"
    output_dir = settings.data_dir / video_id / "shorts" / "output"
    try:
        current_existing = tuple(
            spec.output_name
            for spec in current_specs
            if (output_dir / spec.output_name).is_file()
        )
    except OSError:
        return False, "既存ショート動画を確認できませんでした。もう一度お試しください。"
    if current_existing != existing_names:
        return False, "上書き対象が変わりました。確認画面を開き直してください。"
    return True, None


@st.dialog("ショート動画の上書きを確認")
def _confirm_queue_overwrite_dialog(
    *,
    video_id: str,
    title: str,
    specs: tuple[ShortsQueueClipSpec, ...],
    snapshot_fingerprint: str,
    existing_names: tuple[str, ...],
    settings: Settings,
) -> None:
    """固定済み対象を表示し、確定時だけキュージョブを開始する."""
    st.warning("次の既存ショート動画を上書きします。")
    for name in existing_names:
        st.markdown(f"- `{_safe_text(name)}`")
    busy = is_busy(settings)
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "上書きしてまとめて生成",
        key=f"shorts_queue_confirm_{video_id}",
        type="primary",
        disabled=busy,
    ):
        allowed, reason = _validate_overwrite_confirmation(
            video_id=video_id,
            specs=specs,
            snapshot_fingerprint=snapshot_fingerprint,
            existing_names=existing_names,
            settings=settings,
        )
        if not allowed:
            st.error(reason or "現在の状態を確認できませんでした。")
            return
        _start_queue_job(
            video_id=video_id,
            title=title,
            specs=specs,
            settings=settings,
        )


def start_or_confirm_line_generation(
    *,
    video_id: str,
    title: str,
    spec: ShortsQueueClipSpec,
    snapshot_fingerprint: str,
    settings: Settings,
) -> None:
    """工程 UI の 1 本を既存 S4 ジョブ・上書き確認境界へ接続する."""
    output_path = settings.data_dir / video_id / "shorts" / "output" / spec.output_name
    if output_path.is_file():
        _confirm_queue_overwrite_dialog(
            video_id=video_id,
            title=title,
            specs=(spec,),
            snapshot_fingerprint=snapshot_fingerprint,
            existing_names=(spec.output_name,),
            settings=settings,
        )
        return
    _start_queue_job(
        video_id=video_id,
        title=title,
        specs=(spec,),
        settings=settings,
    )


@st.dialog("中断したショート量産の再実行を確認")
def _confirm_interrupted_queue_recovery_dialog(
    *,
    video_id: str,
    title: str,
    result: ShortsQueueResult,
    settings: Settings,
) -> None:
    """途中結果を done に変えず、明示確認後に新しい job として再実行する."""
    st.warning(
        "前回のショート量産は処理中に中断されました。"
        "途中結果を完了扱いにはせず、証明できる成功 item だけを再利用します。"
    )
    st.caption(f"再実行対象 {len(result.clip_specs)} 本 / 元ジョブ {_safe_text(result.job_id)}")
    for spec in result.clip_specs:
        st.write(_safe_text(spec.output_name))
    if st.button(
        "確認してショート量産を再実行",
        key=f"shorts_queue_recovery_confirm_{video_id}_{result.job_id}",
        type="primary",
    ):
        _start_queue_job(
            video_id=video_id,
            title=title,
            specs=result.clip_specs,
            settings=settings,
            resume_job_id=result.job_id,
        )


def _render_snapshot_form(
    *,
    video_id: str,
    source: str,
    candidates: Sequence[ClipCandidate | HighlightSegment],
) -> None:
    """候補表示順の選択と設定を一括 submit する."""
    prefix = _state_key(video_id, "selection")
    with st.form(f"{prefix}_form"):
        st.markdown("**生成する候補を選択**")
        selected: list[ClipCandidate | HighlightSegment] = []
        for candidate in candidates:
            if st.checkbox(
                _candidate_label(candidate),
                key=f"{prefix}_{source}_{candidate.id}",
            ):
                selected.append(candidate)
        mode = st.segmented_control(
            "生成モード",
            ["individual", "concat"],
            default="individual",
            required=True,
            format_func=lambda value: _MODE_LABELS[value],
            key=f"{prefix}_mode",
        )
        layout = st.segmented_control(
            "レイアウト",
            ["blur", "crop"],
            default="blur",
            required=True,
            format_func=lambda value: "ぼかし背景" if value == "blur" else "中央クロップ",
            key=f"{prefix}_layout",
        )
        preset = st.selectbox(
            "通常テロップ",
            list(TELOP_PRESETS),
            key=f"{prefix}_preset",
        )
        hook_preset = st.selectbox(
            "Hook テロップ",
            list(TELOP_PRESETS),
            index=list(TELOP_PRESETS).index("hook"),
            key=f"{prefix}_hook_preset",
        )
        submitted = st.form_submit_button("選択内容を確定", type="primary")

    if not submitted:
        return
    try:
        normalized = normalize_queue_candidates(selected, source=source)
        targets = build_shorts_queue_targets(normalized, mode=mode)
        fingerprint = make_shorts_queue_fingerprint(
            video_id=video_id,
            source=source,
            mode=mode,
            original_candidates=selected,
            segments=normalized,
            layout=layout,
            preset=preset,
            hook_preset=hook_preset,
        )
    except (ShortsQueueError, TelopError) as exc:
        st.error(_safe_text(exc))
        return

    previous = _snapshot(video_id)
    if previous is None or previous.get("fingerprint") != fingerprint:
        _clear_snapshot(video_id)
    st.session_state[_state_key(video_id, "snapshot")] = {
        "fingerprint": fingerprint,
        "source": source,
        "mode": mode,
        "layout": layout,
        "preset": preset,
        "hook_preset": hook_preset,
        "targets": targets,
        "original_candidates": tuple(selected),
        "selected_ids": tuple(candidate.id for candidate in selected),
        "segments": normalized,
    }
    st.rerun()


def _document_from_editor(
    draft: TelopScriptDocument,
    *,
    hook_text: str,
    title_text: str,
    description: str,
    tags_text: str,
    segments_text: str,
) -> TelopScriptDocument:
    """editor 値を Pydantic document へ戻す."""
    try:
        raw_segments = json.loads(segments_text)
    except json.JSONDecodeError as exc:
        raise TelopError("テロップ本文 JSON の形式が正しくありません。") from exc
    try:
        return TelopScriptDocument.model_validate(
            {
                "hook_text": hook_text,
                "title_candidates": [line for line in title_text.splitlines() if line.strip()],
                "description": description,
                "tags": [tag.strip() for tag in tags_text.split(",") if tag.strip()],
                "segments": raw_segments,
                "artifact_ref": draft.artifact_ref,
                "artifact_fingerprint": draft.artifact_fingerprint,
                "used_range_cue_digests": draft.used_range_cue_digests,
            }
        )
    except Exception as exc:
        raise TelopError("編集したテロップ台本の形式が正しくありません。") from exc


def _render_target_editor(
    *,
    video_id: str,
    snapshot: Mapping[str, Any],
    target: ShortsQueueTarget,
    settings: Settings,
) -> None:
    drafts = _dict_state(video_id, "drafts")
    confirmed = _dict_state(video_id, "confirmed")
    errors = _dict_state(video_id, "errors")
    fingerprint = str(snapshot["fingerprint"])
    target_key = f"{video_id}_{fingerprint[:12]}_{target.target_id}"

    if target.target_id in confirmed:
        spec = confirmed[target.target_id]
        document = spec.telop_document
        with st.container(border=True):
            st.markdown(f"**{_safe_text(document.title_candidates[0])}**")
            st.caption("台本は確定済みです。")
            st.write(_safe_text(document.hook_text))
            if st.button("修正する", key=f"queue_edit_{target_key}"):
                confirmed.pop(target.target_id, None)
                st.rerun()
        return

    draft = drafts.get(target.target_id)
    if not isinstance(draft, TelopScriptDocument):
        with st.container(border=True):
            st.markdown(f"**生成対象 {_safe_text(target.target_id)}**")
            st.caption(f"{len(target.segments)} 区間")
            label = "再試行" if target.target_id in errors else "台本を生成"
            if target.target_id in errors:
                st.error(_safe_text(errors[target.target_id]))
            if st.button(label, key=f"queue_generate_{target_key}"):
                try:
                    artifact = None
                    raw_fingerprint = snapshot.get("artifact_fingerprint")
                    if raw_fingerprint:
                        artifact = TranscriptArtifactStore(video_id, settings).load_artifact(
                            str(raw_fingerprint)
                        )
                        raw_ref = snapshot.get("artifact_ref")
                        if raw_ref is not None:
                            if not isinstance(raw_ref, TranscriptArtifactRef):
                                raw_ref = TranscriptArtifactRef.model_validate(raw_ref)
                            actual_ref = TranscriptArtifactStore(video_id, settings).artifact_ref(
                                artifact
                            )
                            if actual_ref != raw_ref:
                                raise TelopError(
                                    "保存済み artifact reference と実体が一致しません。"
                                )
                    generated = generate_telop_script(
                        video_id,
                        target.highlight_segments(),
                        settings,
                        transcript_artifact=artifact,
                    )
                    if generated.document is None:
                        raise TelopError("テロップ台本を生成できませんでした。")
                except (AiPromptError, TranscriptArtifactError, ValueError) as exc:
                    errors[target.target_id] = str(exc)
                else:
                    drafts[target.target_id] = generated.document
                    errors.pop(target.target_id, None)
                st.rerun()
        return

    with st.form(f"queue_editor_{target_key}"):
        hook_text = st.text_input(
            "フック文言",
            value=draft.hook_text,
            key=f"queue_hook_{target_key}",
        )
        title_text = st.text_area(
            "タイトル案（1 行 1 件）",
            value="\n".join(draft.title_candidates),
            key=f"queue_titles_{target_key}",
        )
        description = st.text_area(
            "説明文",
            value=draft.description,
            key=f"queue_description_{target_key}",
        )
        tags_text = st.text_input(
            "タグ（カンマ区切り）",
            value=",".join(draft.tags),
            key=f"queue_tags_{target_key}",
        )
        segments_text = st.text_area(
            "テロップ本文 JSON",
            value=json.dumps(
                [segment.model_dump(mode="json") for segment in draft.segments],
                ensure_ascii=False,
                indent=2,
            ),
            height=320,
            key=f"queue_segments_{target_key}",
        )
        submitted = st.form_submit_button("台本を確定", type="primary")

    if not submitted:
        return
    try:
        edited = _document_from_editor(
            draft,
            hook_text=hook_text,
            title_text=title_text,
            description=description,
            tags_text=tags_text,
            segments_text=segments_text,
        )
        saved = save_confirmed_telop_script(
            video_id,
            [segment.to_tuple() for segment in target.segments],
            edited,
            settings,
        )
        spec = make_shorts_queue_clip_spec(
            target,
            saved.document,
            layout=str(snapshot["layout"]),
            preset=str(snapshot["preset"]),
            hook_preset=str(snapshot["hook_preset"]),
        )
    except (TelopError, ShortsQueueError) as exc:
        st.error(_safe_text(exc))
        return
    confirmed[target.target_id] = spec
    drafts[target.target_id] = saved.document
    st.rerun()


def _open_video(path: Path):
    """download_button の別 thread で file-like を遅延生成する."""
    try:
        return path.open("rb")
    except OSError as exc:
        raise OSError(
            "動画ファイルを開けませんでした。"
            "ファイルが移動・削除されていないか確認してください。"
        ) from exc


def _is_nonempty_file(path: Path) -> tuple[bool, str | None]:
    """描画直前の Path 競合を日本語エラーへ変換する."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False, "生成済み動画ファイルが見つからないか、空になっています。"
    except OSError:
        return False, "生成済み動画ファイルを確認できませんでした。もう一度お試しください。"
    return True, None


def _render_result(
    result: ShortsQueueResult,
    *,
    video_id: str | None = None,
    title: str = "",
    settings: Settings | None = None,
) -> None:
    st.markdown("#### まとめて生成したショート")
    st.caption(
        f"成功 {result.success_count} 件 / 失敗 {result.failure_count} 件 / "
        f"ジョブ {_safe_text(result.job_id)}"
    )
    if result.status == "interrupted":
        st.error(
            "ショート生成は前回の処理中に中断されました。"
            "途中の成果物は完了扱いにしていません。"
            "確認後に再実行すると、安全性を確認できた成功 item だけを再利用します。"
        )
        if video_id is not None and settings is not None and st.button(
            "中断したキューを再実行する",
            key=f"shorts_queue_recovery_{video_id}_{result.job_id}",
            type="primary",
        ):
            _confirm_interrupted_queue_recovery_dialog(
                video_id=video_id,
                title=title,
                result=result,
                settings=settings,
            )
        return
    if result.status == "running":
        st.info("ショート量産を実行中です。完了するまで予約投稿できません。")
        return
    if result.failure_count and result.success_count == 0:
        st.error(
            f"すべてのショート生成に失敗しました（失敗 {result.failure_count} 件）。"
            "下の生成対象ごとに理由を確認して、生成工程から再試行してください。"
        )
    elif result.failure_count:
        st.warning(
            f"一部のショート生成に失敗しました"
            f"（成功 {result.success_count} 件 / 失敗 {result.failure_count} 件）。"
            "失敗した対象は理由を確認して再試行してください。"
        )
    elif result.success_count:
        st.success(f"ショート生成に成功しました（成功 {result.success_count} 件）。")
    for item in result.items:
        with st.container(border=True):
            if item.status == "failed":
                st.error(
                    f"{_safe_text(item.target_id)}: {_safe_text(item.error or '生成に失敗しました。')}"
                )
                continue
            primary_title = item.title_candidates[0]
            st.markdown(f"**{_safe_text(primary_title)}**")
            output_path = item.output_path
            output_ready = False
            output_warning = "生成済み動画ファイルが見つからないか、空になっています。"
            if output_path is not None:
                output_ready, output_warning = _is_nonempty_file(output_path)
            if not output_ready:
                st.warning(output_warning)
            else:
                with st.container(width=360):
                    st.video(output_path)
                st.download_button(
                    "mp4 を保存",
                    data=lambda path=output_path: _open_video(path),
                    file_name=output_path.name,
                    mime="video/mp4",
                    key=f"queue_download_{result.job_id}_{item.target_id}",
                    on_click="ignore",
                    width="stretch",
                )
            if item.font_warning:
                st.warning(_safe_text(item.font_warning))
            st.markdown("**タイトル案**")
            for title in item.title_candidates:
                st.write(_safe_text(title))
            st.markdown("**説明文**")
            st.write(_safe_text(item.description))
            st.markdown("**タグ**")
            st.write(_safe_text(",".join(item.tags)))
            key_base = f"{result.job_id}_{item.target_id}"
            render_copy_button(
                "\n".join(item.title_candidates),
                label="タイトル案をコピー",
                key=f"{key_base}_titles",
            )
            render_copy_button(
                item.description,
                label="説明文をコピー",
                key=f"{key_base}_description",
            )
            render_copy_button(
                ",".join(item.tags),
                label="タグをコピー",
                key=f"{key_base}_tags",
            )


def _render_current_result(
    video_id: str,
    settings: Settings,
    *,
    title: str = "",
) -> None:
    job_id = _job_ids().get(video_id)
    try:
        if job_id is not None:
            result = load_shorts_queue_result(video_id, job_id, settings)
            if result is None:
                st.info("ショート量産ジョブを準備しています。")
                return
        else:
            result = load_latest_shorts_queue_result(video_id, settings)
    except ShortsQueueError as exc:
        st.error(_safe_text(exc))
        return
    if result is not None:
        _render_result(result, video_id=video_id, title=title, settings=settings)


def render_shorts_queue(
    *,
    video_id: str,
    title: str,
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
    settings: Settings,
) -> None:
    """S4 の選択、明示台本生成、全確定、結果表示を描画する."""
    st.divider()
    st.markdown("**ショートをまとめて生成**")
    existing_snapshot = _snapshot(video_id)
    if existing_snapshot is not None and existing_snapshot.get("line_mode") is True:
        st.info(
            "現在の 1 本はショート生産ラインで管理されています。"
            "まとめて生成はライン完了後に利用してください。"
        )
        _render_current_result(video_id, settings, title=title)
        return
    available_sources: list[str] = []
    if clip_candidates:
        available_sources.append("clips")
    if highlight_candidates:
        available_sources.append("highlights")
    if not available_sources:
        st.info("まとめて生成できる候補がありません。")
        _render_current_result(video_id, settings, title=title)
        return

    source_key = _state_key(video_id, "source")
    source = st.segmented_control(
        "候補ソース",
        available_sources,
        default=available_sources[0],
        required=True,
        format_func=lambda value: _SOURCE_LABELS[value],
        key=source_key,
    )
    previous_source_key = _state_key(video_id, "applied_source")
    previous_source = st.session_state.get(previous_source_key)
    if previous_source is not None and previous_source != source:
        _clear_snapshot(video_id)
    st.session_state[previous_source_key] = source
    candidates: Sequence[ClipCandidate | HighlightSegment] = (
        clip_candidates if source == "clips" else highlight_candidates
    )

    snapshot = _snapshot(video_id)
    if snapshot is None:
        _render_snapshot_form(
            video_id=video_id,
            source=source,
            candidates=candidates,
        )
    else:
        try:
            current_selected = select_queue_candidates_by_id(
                candidates,
                tuple(snapshot["selected_ids"]),
            )
            current_segments = normalize_queue_candidates(
                current_selected,
                source=source,
            )
            current_fingerprint = make_shorts_queue_fingerprint(
                video_id=video_id,
                source=source,
                mode=str(snapshot["mode"]),
                original_candidates=current_selected,
                segments=current_segments,
                layout=str(snapshot["layout"]),
                preset=str(snapshot["preset"]),
                hook_preset=str(snapshot["hook_preset"]),
            )
        except (ShortsQueueError, TelopError) as exc:
            _clear_snapshot(video_id)
            st.warning(_safe_text(exc))
            st.rerun()
            return
        if current_fingerprint != snapshot["fingerprint"]:
            _clear_snapshot(video_id)
            st.warning("候補内容が変更されました。選択と台本確認をやり直してください。")
            st.rerun()
            return
        targets = tuple(snapshot["targets"])
        st.caption(
            f"{_SOURCE_LABELS[str(snapshot['source'])]} / "
            f"{_MODE_LABELS[str(snapshot['mode'])]} / {len(targets)} 本"
        )
        if st.button("選択内容を変更", key=_state_key(video_id, "unlock")):
            _clear_snapshot(video_id)
            st.rerun()
        for target in targets:
            _render_target_editor(
                video_id=video_id,
                snapshot=snapshot,
                target=target,
                settings=settings,
            )

        confirmed = _dict_state(video_id, "confirmed")
        target_ids = tuple(target.target_id for target in targets)
        allowed, reason = can_start_shorts_queue(
            expected_fingerprint=str(snapshot["fingerprint"]),
            current_fingerprint=current_fingerprint,
            target_ids=target_ids,
            confirmed_specs=confirmed,
            busy=is_busy(settings),
        )
        if reason:
            st.caption(reason)
        if st.button(
            "確定したショートをまとめて生成",
            type="primary",
            key=_state_key(video_id, "start"),
            disabled=not allowed,
        ):
            specs = tuple(confirmed[target_id] for target_id in target_ids)
            output_dir = settings.data_dir / video_id / "shorts" / "output"
            existing = tuple(
                spec.output_name
                for spec in specs
                if (output_dir / spec.output_name).is_file()
            )
            if existing:
                _confirm_queue_overwrite_dialog(
                    video_id=video_id,
                    title=title,
                    specs=specs,
                    snapshot_fingerprint=current_fingerprint,
                    existing_names=existing,
                    settings=settings,
                )
            else:
                _start_queue_job(
                    video_id=video_id,
                    title=title,
                    specs=specs,
                    settings=settings,
                )

    _render_current_result(video_id, settings, title=title)
