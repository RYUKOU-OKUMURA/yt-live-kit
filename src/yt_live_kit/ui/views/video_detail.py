"""選択した動画の成果物と次の作業をまとめて表示するページ."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.chapter_validator import validate_chapters
from yt_live_kit.services.clips import load_candidates_file
from yt_live_kit.services.highlights import load_segments_file
from yt_live_kit.services.history import ProcessedVideo, list_processed_videos
from yt_live_kit.services.jobs import JobBusyError, JobState, get_active_job, is_busy, start_job
from yt_live_kit.services.pipeline import (
    PipelineResult,
    load_result_from_disk,
    regenerate_job_target,
)
from yt_live_kit.services.storage import StorageError, format_bytes, purge_source
from yt_live_kit.services.shorts_queue import (
    ShortsQueueError,
    load_latest_shorts_queue_result,
)
from yt_live_kit.services.youtube_api import (
    fetch_video_snippet,
    is_configured,
    merge_chapters_into_description,
    update_video_description,
)
from yt_live_kit.ui.components.clipboard import render_copy_button
from yt_live_kit.ui.components.shorts_line import (
    record_line_upload,
    render_main_line_summary,
    render_shorts_line,
    validate_line_reservation,
)
from yt_live_kit.ui.components.upload import render_upload_section
from yt_live_kit.ui.state import get_selected_video_id, set_active_job_id
from yt_live_kit.ui.views._local_settings import (
    load_description_applied_ids,
    mark_description_applied,
)
from yt_live_kit.ui.views.highlights import render_highlights_section
from yt_live_kit.ui.views.library import count_shorts
from yt_live_kit.ui.views.shorts import render_shorts_section

if TYPE_CHECKING:
    from streamlit.navigation.page import StreamlitPage

Workspace = Literal["materials", "shorts", "publish"]

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_WORKSPACES: tuple[Workspace, ...] = ("materials", "shorts", "publish")
_WORKSPACE_LABELS: dict[Workspace, str] = {
    "materials": "素材候補",
    "shorts": "ショート作成",
    "publish": "公開・投稿",
}
_DESCRIPTION_UPDATED_IDS_KEY = "detail_description_updated_ids"
_DESCRIPTION_SUCCESS_KEY = "detail_description_success"


@dataclass(frozen=True)
class DetailSummary:
    """動画詳細の読み取り専用サマリー."""

    candidate_count: int
    generated_short_count: int
    reservable_short_count: int
    description_applied: bool


def calculate_detail_summary(
    *,
    clip_count: int,
    highlight_count: int,
    generated_short_count: int,
    reservable_short_count: int,
    description_applied: bool,
) -> DetailSummary:
    """UI 入力から状態カードの値を副作用なく計算する."""
    return DetailSummary(
        candidate_count=max(0, clip_count) + max(0, highlight_count),
        generated_short_count=max(0, generated_short_count),
        reservable_short_count=max(0, reservable_short_count),
        description_applied=description_applied,
    )


def choose_initial_workspace(
    *,
    video_id: str,
    candidate_count: int,
    reservable_short_count: int,
    active_job: JobState | None = None,
) -> Workspace:
    """FR-17 v3.2 の優先順で初期ワークスペースを返す純粋関数."""
    if (
        active_job is not None
        and active_job.status == "running"
        and active_job.video_id == video_id
    ):
        if active_job.kind in {"upload"}:
            return "publish"
        if active_job.kind in {"shorts", "shorts_queue", "short_cut"}:
            return "shorts"
        if active_job.kind in {"highlights", "regenerate", "cut_clip"}:
            return "materials"
    if candidate_count <= 0:
        return "materials"
    if reservable_short_count <= 0:
        return "shorts"
    return "publish"


def count_reservable_shorts(video_id: str, settings: Settings) -> int:
    """最新の検証済み manifest にある実在成功出力だけを数える."""
    result = load_latest_shorts_queue_result(video_id, settings)
    if result is None:
        return 0
    return sum(
        item.status == "succeeded"
        and item.output_path is not None
        and item.output_path.is_file()
        for item in result.items
    )


def _start_regenerate(
    video: ProcessedVideo,
    target: Literal["chapters", "clips"],
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "regenerate",
            regenerate_job_target,
            video_id=video.video_id,
            title=video.title,
            target=target,
            settings=settings,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


@st.dialog("成果物の再生成を確認")
def _confirm_regenerate_dialog(
    video: ProcessedVideo,
    target: Literal["chapters", "clips"],
    settings: Settings,
) -> None:
    target_label = "チャプター" if target == "chapters" else "切り抜き候補"
    st.warning(
        f"{target_label}を再生成します。既存の成果物は退避された後に上書きされます。"
    )
    if is_busy():
        st.info(_BUSY_MESSAGE)
    if st.button(
        "再生成を実行",
        key=f"detail_confirm_regen_{target}_{video.video_id}",
        type="primary",
        disabled=is_busy(),
    ):
        _start_regenerate(video, target, settings)


@st.dialog("元動画の削除を確認")
def _confirm_source_purge_dialog(
    video: ProcessedVideo,
    settings: Settings,
) -> None:
    st.warning(
        "元動画と中間ファイルを削除します。"
        "チャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
    )
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "削除を実行",
        key=f"detail_confirm_purge_{video.video_id}",
        type="primary",
        disabled=busy,
    ):
        try:
            deleted = purge_source(video.video_id, settings)
        except StorageError as exc:
            st.error(str(exc))
        else:
            st.success(
                f"元動画と中間ファイルを削除しました（{format_bytes(deleted)}）。"
            )
            st.rerun()


def _description_updated_ids() -> set[str]:
    value = st.session_state.get(_DESCRIPTION_UPDATED_IDS_KEY, set())
    return set(value) if isinstance(value, (set, list, tuple)) else set()


def _safe_user_text(value: object) -> str:
    """ユーザー表示用テキストの半角山カッコを全角へ置換する."""
    return str(value).replace("<", "〈").replace(">", "〉")


def _mark_description_update_started(video_id: str) -> None:
    updated_ids = _description_updated_ids()
    updated_ids.add(video_id)
    st.session_state[_DESCRIPTION_UPDATED_IDS_KEY] = updated_ids


def _clear_description_update_started(video_id: str) -> None:
    updated_ids = _description_updated_ids()
    updated_ids.discard(video_id)
    if updated_ids:
        st.session_state[_DESCRIPTION_UPDATED_IDS_KEY] = updated_ids
    else:
        st.session_state.pop(_DESCRIPTION_UPDATED_IDS_KEY, None)


def _save_description_completion(
    video: ProcessedVideo,
    settings: Settings,
) -> bool:
    """ローカル完了記録だけを保存し、成功時に再描画する."""
    try:
        mark_description_applied(video.video_id, settings)
    # ファイル I/O 境界では認証ラッパー等の例外型も一定しないため、
    # BaseException を除く全例外を日本語メッセージへ変換する。
    except Exception as exc:
        _mark_description_update_started(video.video_id)
        st.warning(
            "YouTube 側は更新済みですが、完了状態を保存できませんでした。"
            "データ保存先の権限を確認し、完了状態の保存を再試行してください。"
            f"（詳細: {_safe_user_text(exc)}）"
        )
        return False

    _clear_description_update_started(video.video_id)
    st.session_state[_DESCRIPTION_SUCCESS_KEY] = (
        f"{_safe_user_text(video.title)} の YouTube 概要欄を更新しました。"
    )
    st.rerun()
    return True


@st.dialog("YouTube 概要欄への反映確認", width="large")
def _description_preview_dialog(
    video: ProcessedVideo,
    before: str,
    after: str,
    settings: Settings,
) -> None:
    """取得済みの更新前後を表示し、確定時だけ YouTube を更新する."""
    st.warning(
        "YouTube 上の公開データを書き換えます。"
        "更新前と更新後を確認してから確定してください。"
    )
    before_column, after_column = st.columns(2)
    with before_column:
        st.markdown("**更新前**")
        st.text_area(
            "現在の概要欄",
            value=_safe_user_text(before),
            height=360,
            disabled=True,
            key=f"detail_description_before_{video.video_id}",
        )
    with after_column:
        st.markdown("**更新後**")
        st.text_area(
            "反映後の概要欄",
            value=_safe_user_text(after),
            height=360,
            disabled=True,
            key=f"detail_description_after_{video.video_id}",
        )

    busy = is_busy()
    already_updated = video.video_id in _description_updated_ids()
    completion_only = already_updated or before == after
    if busy:
        st.info(_BUSY_MESSAGE)
    if already_updated:
        st.warning(
            "YouTube 側は更新済みですが、完了状態を保存できていません。"
            "YouTube は再更新せず、完了状態の保存だけを再試行できます。"
        )
    elif completion_only:
        st.info(
            "YouTube の概要欄には同じタイムラインが既に反映されています。"
            "YouTube は更新せず、完了状態だけを保存します。"
        )

    with st.container(horizontal=True):
        cancel_clicked = st.button(
            "キャンセル",
            key=f"detail_description_cancel_{video.video_id}",
        )
        confirm_clicked = st.button(
            (
                "完了状態の保存を再試行"
                if completion_only
                else "この内容を概要欄に反映"
            ),
            key=f"detail_description_confirm_{video.video_id}",
            type="primary",
            disabled=busy,
        )

    if cancel_clicked:
        st.rerun()
    if not confirm_clicked or busy:
        return

    if completion_only:
        _mark_description_update_started(video.video_id)
        _save_description_completion(video, settings)
        return

    try:
        update_video_description(video.video_id, after, settings)
    # YouTube クライアント境界では I/O・認証ライブラリ由来の例外型が
    # 一定しないため、BaseException を除く全例外を安全な表示へ変換する。
    except Exception as exc:
        st.error(
            "YouTube の概要欄を更新できませんでした。"
            f"時間をおいて再試行してください（詳細: {_safe_user_text(exc)}）。"
        )
        return

    # YouTube 更新直後に記録し、ローカル保存失敗時の二重更新を防ぐ。
    _mark_description_update_started(video.video_id)
    _save_description_completion(video, settings)


def _start_description_preview(
    video: ProcessedVideo,
    chapters_text: str,
    settings: Settings,
) -> None:
    """検証後に概要欄の更新前後を取得し、共通ダイアログを開く."""
    if is_busy():
        st.info(_BUSY_MESSAGE)
        return
    if video.video_id in _description_updated_ids():
        _description_preview_dialog(video, "", "", settings)
        return
    try:
        configured = is_configured(settings)
    # OAuth 設定確認もファイル I/O 境界のため、予期可能な外部例外を
    # スタックトレースではなく日本語の案内へ変換する。
    except Exception as exc:
        st.error(
            "YouTube OAuth 設定を確認できませんでした。"
            f"設定ファイルを確認してください（詳細: {_safe_user_text(exc)}）。"
        )
        return
    if not configured:
        st.error(
            "YouTube OAuth が設定されていません。"
            "設定ファイルを配置してから、もう一度お試しください。"
        )
        return
    if not chapters_text.strip():
        st.error(
            "反映できるチャプターがありません。"
            "先にチャプターを生成してください。"
        )
        return

    validation = validate_chapters(chapters_text)
    if not validation.ok:
        st.error(
            "チャプターの形式が不正なため、概要欄プレビューを開始できません。\n\n"
            + "\n".join(
                f"・{_safe_user_text(error)}" for error in validation.errors
            )
        )
        return

    try:
        snippet = fetch_video_snippet(video.video_id, settings)
        before = str(snippet.get("description") or "")
        after = merge_chapters_into_description(before, chapters_text)
    # YouTube API・認証・I/O の外部境界では例外型が一定しないため、
    # BaseException を除く全例外をサニタイズした日本語表示へ変換する。
    except Exception as exc:
        st.error(
            "概要欄プレビューを作成できませんでした。"
            f"時間をおいて再試行してください（詳細: {_safe_user_text(exc)}）。"
        )
        return
    _description_preview_dialog(video, before, after, settings)


def _render_description_control(
    video: ProcessedVideo,
    chapters_text: str,
    settings: Settings,
    *,
    busy: bool,
) -> None:
    st.warning(
        "概要欄への反映は YouTube 上の公開データを書き換えます。"
        "確定前に更新前後を必ず確認できます。"
    )
    clicked = st.button(
        "概要欄に反映",
        key=f"detail_description_open_{video.video_id}",
        type="primary",
        disabled=busy,
    )
    if clicked and not busy:
        _start_description_preview(video, chapters_text, settings)


def _render_regenerate_control(
    video: ProcessedVideo,
    *,
    target: Literal["chapters", "clips"],
    complete: bool,
    busy: bool,
    settings: Settings,
) -> None:
    target_label = "チャプター" if target == "chapters" else "切り抜き候補"
    button_label = f"{target_label}を再生成" if complete else f"{target_label}を生成"

    def render_button() -> None:
        if st.button(
            button_label,
            key=f"detail_regen_{target}_{video.video_id}",
            type="secondary" if complete else "primary",
            disabled=busy or not video.has_transcript,
            help=(
                None
                if video.has_transcript
                else "先に字幕の取得と整形を実行してください。"
            ),
        ):
            _confirm_regenerate_dialog(video, target, settings)

    if complete:
        st.caption("既存成果物を退避してから再生成します。")
    render_button()


def _render_transcript(result: PipelineResult) -> None:
    st.subheader("1. 字幕・文字起こし全文")
    st.text_area(
        "全文",
        value=result.full_transcript_text,
        height=400,
        disabled=True,
        label_visibility="collapsed",
    )
    render_copy_button(
        result.full_transcript_text,
        label="全文をコピー",
        key=f"detail_copy_transcript_{result.video_id}",
    )


def _render_chapters(
    video: ProcessedVideo,
    result: PipelineResult,
    *,
    busy: bool,
    settings: Settings,
) -> None:
    st.subheader("2. チャプター")
    has_chapters = video.has_chapters or bool(result.chapters_text.strip())
    if has_chapters:
        st.code(result.chapters_text, language="markdown")
        render_copy_button(
            result.chapters_text,
            label="タイムラインをコピー",
            key=f"detail_copy_chapters_{result.video_id}",
        )
    else:
        st.info("チャプターはまだ生成されていません。")
    _render_regenerate_control(
        video,
        target="chapters",
        complete=has_chapters,
        busy=busy,
        settings=settings,
    )


def _render_clips(
    video: ProcessedVideo,
    result: PipelineResult,
    *,
    busy: bool,
    settings: Settings,
    has_highlights: bool,
) -> None:
    st.subheader("3. 切り抜き候補")
    if result.clips_error:
        st.warning(
            "切り抜き候補の生成に失敗しましたが、他の成果物は利用できます。\n\n"
            f"{result.clips_error}"
        )
    if result.clips_candidates:
        for candidate in result.clips_candidates:
            with st.container(border=True):
                st.markdown(f"**{candidate.title}**")
                st.caption(
                    f"{candidate.start} → {candidate.end}（{candidate.duration_sec} 秒）"
                )
                st.write(candidate.reason)
    elif not result.clips_error:
        st.info("切り抜き候補はまだ生成されていません。")

    _render_regenerate_control(
        video,
        target="clips",
        complete=video.has_clips or bool(result.clips_candidates) or has_highlights,
        busy=busy,
        settings=settings,
    )


@dataclass(frozen=True)
class CandidateTransfer:
    """正式ライン開始前だけ session state に置く候補引き継ぎ."""

    source: Literal["clips", "highlights"]
    selected_ids: tuple[str, ...]
    fingerprint: str


def make_candidate_fingerprint(
    source: Literal["clips", "highlights"],
    candidates: list[ClipCandidate] | list[HighlightSegment],
) -> str:
    """候補ファイル全体と表示順を表す安定 fingerprint を返す."""
    payload = {
        "source": source,
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_candidate_transfer(
    transfer: CandidateTransfer | None,
    *,
    current_fingerprint: str,
    candidate_ids: set[str],
) -> CandidateTransfer | None:
    """候補変更・ID 欠落を検出し、安全な引き継ぎだけを返す."""
    if transfer is None:
        return None
    if transfer.fingerprint != current_fingerprint:
        return None
    if not transfer.selected_ids or not set(transfer.selected_ids) <= candidate_ids:
        return None
    return transfer


def _transfer_key(video_id: str, source: str) -> str:
    return f"shorts_line_transfer_{video_id}_{source}"


def _load_transfer(video_id: str, source: str) -> CandidateTransfer | None:
    raw = st.session_state.get(_transfer_key(video_id, source))
    if not isinstance(raw, dict):
        return None
    try:
        return CandidateTransfer(
            source=source,  # type: ignore[arg-type]
            selected_ids=tuple(str(value) for value in raw["selected_ids"]),
            fingerprint=str(raw["fingerprint"]),
        )
    except (KeyError, TypeError):
        return None


def _save_transfer(video_id: str, transfer: CandidateTransfer) -> None:
    st.session_state[_transfer_key(video_id, transfer.source)] = {
        "source": transfer.source,
        "selected_ids": transfer.selected_ids,
        "fingerprint": transfer.fingerprint,
    }


def _set_workspace(video_id: str, workspace: Workspace) -> None:
    st.session_state[f"detail_workspace_{video_id}"] = workspace


def _render_state_summary(summary: DetailSummary) -> None:
    columns = st.columns(3)
    with columns[0].container(border=True, height="stretch"):
        st.markdown("**素材候補**")
        st.write(f"{summary.candidate_count} 件")
    with columns[1].container(border=True, height="stretch"):
        st.markdown("**ショート**")
        st.write(
            f"生成 {summary.generated_short_count} 本・"
            f"予約可能 {summary.reservable_short_count} 本"
        )
    with columns[2].container(border=True, height="stretch"):
        st.markdown("**概要欄**")
        st.write("反映済み" if summary.description_applied else "未反映")


def _existing_artifacts(video_id: str, settings: Settings) -> tuple[str, ...]:
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        return ()
    labels: list[str] = []
    for label, relative in (
        ("動画メタデータ", Path("meta.json")),
        ("字幕ファイル", Path("subtitles/ja.vtt")),
        ("チャプター", Path("chapters.md")),
        ("切り抜き候補", Path("clips/candidates.json")),
        ("ハイライト候補", Path("highlights/segments.json")),
    ):
        if (video_dir / relative).is_file():
            labels.append(label)
    return tuple(labels)


def _render_recovery_state(
    video_id: str,
    settings: Settings,
    *,
    run_page: StreamlitPage | None,
) -> None:
    st.warning("保存済みの字幕成果物を読み込めませんでした。")
    st.write(
        "字幕の保存結果が欠けているか壊れているため、安全のため通常の作業画面を"
        "停止しています。取り込みからこの動画を再処理してください。"
    )
    if st.button(
        "取り込みで再処理",
        key=f"detail_recover_{video_id}",
        type="primary",
        disabled=run_page is None,
    ) and run_page is not None:
        st.switch_page(run_page)

    details = st.expander(
        "読み込み状況の詳細",
        expanded=False,
        key=f"detail_recovery_details_{video_id}",
        on_change="rerun",
    )
    if details.open:
        with details:
            st.write(f"動画 ID: {_safe_user_text(video_id)}")
            artifacts = _existing_artifacts(video_id, settings)
            st.write(
                "存在する成果物: " + ("、".join(artifacts) if artifacts else "なし")
            )


def _load_material_candidates(
    result: PipelineResult,
    settings: Settings,
) -> tuple[list[ClipCandidate], list[HighlightSegment]]:
    clip_doc = load_candidates_file(result.video_id, settings)
    clips = (
        list(clip_doc.candidates)
        if clip_doc is not None
        else list(result.clips_candidates)
    )
    highlight_doc = load_segments_file(result.video_id, settings)
    highlights = list(highlight_doc.candidates) if highlight_doc is not None else []
    return clips, highlights


def _render_materials_workspace(
    result: PipelineResult,
    settings: Settings,
    *,
    clips: list[ClipCandidate],
    highlights: list[HighlightSegment],
) -> None:
    st.subheader("素材候補")
    st.caption("候補を確認し、作成するショートへ同じ順序で引き継ぎます。")
    available: list[Literal["clips", "highlights"]] = []
    if clips:
        available.append("clips")
    if highlights:
        available.append("highlights")
    if not available:
        st.info("素材候補がありません。詳細・再生成から候補を生成してください。")
        return

    source: Literal["clips", "highlights"] = available[0]
    if len(available) == 2:
        selected = st.segmented_control(
            "候補ソース",
            available,
            default=available[0],
            required=True,
            format_func=lambda value: (
                "切り抜き候補" if value == "clips" else "ハイライト候補"
            ),
            key=f"materials_source_{result.video_id}",
            width="stretch",
        )
        if selected in available:
            source = selected

    candidates: list[ClipCandidate] | list[HighlightSegment] = (
        clips if source == "clips" else highlights
    )
    fingerprint = make_candidate_fingerprint(source, candidates)
    transfer = _load_transfer(result.video_id, source)
    valid_transfer = validate_candidate_transfer(
        transfer,
        current_fingerprint=fingerprint,
        candidate_ids={candidate.id for candidate in candidates},
    )
    if transfer is not None and valid_transfer is None:
        st.session_state.pop(_transfer_key(result.video_id, source), None)
        st.warning("候補が更新されました。ショート作成対象を選び直してください。")
    selected_ids = list(valid_transfer.selected_ids if valid_transfer else ())

    for candidate in candidates:
        with st.container(border=True):
            st.markdown(f"**{_safe_user_text(candidate.title)}**")
            st.caption(
                f"{_safe_user_text(candidate.start)} → {_safe_user_text(candidate.end)}"
                f"（{candidate.duration_sec} 秒）"
            )
            st.write(_safe_user_text(candidate.reason))
            already_added = candidate.id in selected_ids
            if st.button(
                "追加済み" if already_added else "ショート作成対象へ追加",
                key=f"materials_add_{result.video_id}_{source}_{candidate.id}",
                disabled=already_added,
            ):
                selected_ids.append(candidate.id)
                _save_transfer(
                    result.video_id,
                    CandidateTransfer(source, tuple(selected_ids), fingerprint),
                )
                st.rerun()

    if selected_ids:
        st.caption(f"ショート作成へ引き継ぐ候補: {len(selected_ids)} 件")
        if st.button(
            "選択した候補でショート作成へ",
            key=f"materials_to_shorts_{result.video_id}_{source}",
            type="primary",
        ):
            _set_workspace(result.video_id, "shorts")
            st.rerun()

    st.divider()
    st.markdown("#### 補助操作")
    render_highlights_section(result)


def _render_publish_workspace(
    video: ProcessedVideo,
    result: PipelineResult,
    settings: Settings,
    *,
    busy: bool,
    summary: DetailSummary,
) -> None:
    st.subheader("公開・投稿")
    validation = validate_chapters(result.chapters_text)
    chapter_count = len(
        [line for line in result.chapters_text.splitlines() if line.strip()]
    )
    with st.container(border=True):
        st.markdown("**元動画の概要欄**")
        if not result.chapters_text.strip():
            st.info("チャプターは未生成です。")
        elif validation.ok:
            st.success(f"チャプター生成済み・{chapter_count} 件・形式 OK")
        else:
            st.error("チャプターの形式エラーがあります。詳細・再生成で確認してください。")
        st.caption("反映済み" if summary.description_applied else "未反映")
        _render_description_control(
            video,
            result.chapters_text,
            settings,
            busy=busy or not validation.ok,
        )

    with st.container(border=True):
        st.markdown("**ショートの予約投稿**")
        if summary.reservable_short_count == 0:
            if summary.generated_short_count:
                st.info(
                    "生成済みですが予約対象に追加されていません。"
                    "ショート生産ラインで最終確認まで進めてください。"
                )
            else:
                st.info("先にショートを作成してください。")
            if st.button(
                "ショート生産ラインへ",
                key=f"publish_to_shorts_{video.video_id}",
            ):
                _set_workspace(video.video_id, "shorts")
                st.rerun()
        else:
            render_upload_section(
                video.video_id,
                settings,
                before_preview=lambda clip_id, output_path: (
                    validate_line_reservation(
                        video.video_id,
                        clip_id,
                        output_path,
                        settings,
                    )
                ),
                on_operation_started=lambda clip_id, operation_id, output_path: (
                    record_line_upload(
                        video.video_id,
                        clip_id,
                        operation_id,
                        output_path,
                        settings,
                    )
                ),
            )


def _render_details_and_regeneration(
    video: ProcessedVideo,
    result: PipelineResult,
    *,
    settings: Settings,
    busy: bool,
) -> None:
    details = st.expander(
        "詳細・再生成",
        expanded=False,
        key=f"detail_regeneration_{video.video_id}",
        on_change="rerun",
    )
    if not details.open:
        return
    with details:
        _render_transcript(result)
        _render_chapters(video, result, busy=busy, settings=settings)
        st.markdown("**候補の再生成**")
        if result.clips_error:
            st.warning(_safe_user_text(result.clips_error))
        _render_regenerate_control(
            video,
            target="clips",
            complete=video.has_clips or bool(result.clips_candidates),
            busy=busy,
            settings=settings,
        )
        st.markdown("**元動画と中間ファイルの管理**")
        st.caption(
            "削除してもチャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
        )
        if st.button(
            "元動画を削除",
            key=f"detail_purge_{video.video_id}",
            disabled=busy,
        ):
            _confirm_source_purge_dialog(video, settings)


def render_video_detail_page(
    *,
    run_page: StreamlitPage | None = None,
) -> None:
    """選択中の動画について、保存済み成果物から詳細ページを再構築する."""
    video_id = get_selected_video_id()
    if video_id is None:
        st.header("動画詳細")
        st.info("ライブラリから動画を選択してください。")
        return

    settings = get_settings()
    video = next(
        (
            item
            for item in list_processed_videos(settings)
            if item.video_id == video_id
        ),
        None,
    )
    if video is None:
        st.warning(
            "選択した動画が見つかりません。"
            "ライブラリに戻り、動画を選び直してください。"
        )
        return

    st.header(_safe_user_text(video.title))
    st.caption(f"動画 ID: {_safe_user_text(video.video_id)}")
    description_success = st.session_state.pop(_DESCRIPTION_SUCCESS_KEY, None)
    if description_success:
        st.success(description_success)
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)

    result = load_result_from_disk(video_id, settings)
    if result is None:
        _render_recovery_state(video_id, settings, run_page=run_page)
        return

    clips, highlights = _load_material_candidates(result, settings)
    try:
        reservable_count = count_reservable_shorts(video.video_id, settings)
    except ShortsQueueError as exc:
        reservable_count = 0
        st.warning(
            "予約可能なショートを安全に確認できませんでした。"
            f"詳細: {_safe_user_text(exc)}"
        )
    summary = calculate_detail_summary(
        clip_count=len(clips),
        highlight_count=len(highlights),
        generated_short_count=count_shorts(video.video_id, settings),
        reservable_short_count=reservable_count,
        description_applied=video.video_id in load_description_applied_ids(settings),
    )
    _render_state_summary(summary)
    render_main_line_summary(video.video_id, settings)

    default_workspace = choose_initial_workspace(
        video_id=video.video_id,
        candidate_count=summary.candidate_count,
        reservable_short_count=summary.reservable_short_count,
        active_job=get_active_job(settings),
    )
    workspace = st.segmented_control(
        "作業を選択",
        _WORKSPACES,
        default=default_workspace,
        required=True,
        format_func=lambda value: _WORKSPACE_LABELS[value],
        key=f"detail_workspace_{video.video_id}",
        width="stretch",
    )
    selected_workspace: Workspace = (
        workspace if workspace in _WORKSPACES else default_workspace
    )

    if selected_workspace == "materials":
        _render_materials_workspace(
            result,
            settings,
            clips=clips,
            highlights=highlights,
        )
    elif selected_workspace == "shorts":
        st.subheader("ショート作成")
        preferred_ids: list[str] = []
        for source in ("clips", "highlights"):
            transfer = _load_transfer(video.video_id, source)
            if transfer is not None:
                preferred_ids.extend(transfer.selected_ids)
        render_shorts_line(
            video_id=video.video_id,
            title=result.title,
            clip_candidates=clips,
            highlight_candidates=highlights,
            settings=settings,
            preferred_candidate_ids=preferred_ids,
        )
        render_shorts_section(result, expanded=False)
    else:
        _render_publish_workspace(
            video,
            result,
            settings,
            busy=busy,
            summary=summary,
        )

    _render_details_and_regeneration(
        video,
        result,
        settings=settings,
        busy=busy,
    )
