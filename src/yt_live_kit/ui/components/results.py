"""処理結果の表示（タイムライン・全文・切り抜き候補）."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.description import (
    DescriptionError,
    build_description,
    get_template_path,
)
from yt_live_kit.services.clips import cut_clip_job_target
from yt_live_kit.services.ffmpeg import CutResult
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import PipelineResult
from yt_live_kit.services.storage import dir_size, format_bytes
from yt_live_kit.ui.components.clipboard import render_copy_button
from yt_live_kit.ui.state import get_cut_result, set_active_job_id

_TEMPLATE_NOT_SET_MESSAGE = (
    "定型文が未設定です。data/_config/description_template.txt に "
    "{{timeline}} を含むテンプレートを置くと、まとめてコピーできます。"
)
_CHAPTERS_NOT_GENERATED_MESSAGE = (
    "チャプターは生成されていません。ライブラリから生成できます。"
)
_CLIPS_EMPTY_NOT_REQUESTED_MESSAGE = "この実行では切り抜き候補を生成していません。"
_CLIPS_EMPTY_REQUESTED_MESSAGE = (
    "切り抜き候補がありません。Codex CLI の設定を確認してください。"
)
_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"


def clips_empty_message(clips_requested: bool) -> str:
    """切り抜き候補が 0 件のときの案内文言を返す（テスト可能な純粋関数）."""
    if clips_requested:
        return _CLIPS_EMPTY_REQUESTED_MESSAGE
    return _CLIPS_EMPTY_NOT_REQUESTED_MESSAGE


def cut_clip_button_disabled(*, busy: bool) -> bool:
    """切り出しボタンを無効化するかどうか."""
    return busy


def clip_candidate_radio_key(video_id: str) -> str:
    """切り抜き候補ラジオの session_state キーを返す."""
    return f"clip_candidate_radio_{video_id}"


def cut_clip_button_key(video_id: str) -> str:
    """切り出しボタンの session_state キーを返す."""
    return f"cut_clip_{video_id}"


def _start_cut_clip(
    result: PipelineResult,
    selected,
    settings: Settings,
) -> None:
    """切り出しジョブを開始する."""
    try:
        job_id = start_job(
            "cut_clip",
            cut_clip_job_target,
            video_id=result.video_id,
            title=result.title,
            settings=settings,
            start=selected.start,
            end=selected.end,
            candidate_id=selected.id,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


def source_cache_note(video_id: str, settings: Settings) -> str | None:
    """元動画キャッシュの容量案内を返す（テスト可能な純粋関数）。0 バイトなら None."""
    source_dir = settings.data_dir / video_id / "clips" / "source"
    size = dir_size(source_dir)
    if size == 0:
        return None
    return (
        f"元動画 ({format_bytes(size)}) は再利用のため保持しています。"
        "不要なら一覧から削除できます。"
    )


def render_results(result: PipelineResult) -> None:
    st.success(f"「{result.title}」の処理が完了しました。")

    if result.clips_error:
        st.warning(
            "切り抜き候補の生成に失敗しましたが、タイムラインは利用できます。\n\n"
            f"{result.clips_error}"
        )

    if result.highlights_error:
        st.warning(
            "ハイライト候補の生成に失敗しましたが、タイムラインと切り抜き候補は利用できます。\n\n"
            f"{result.highlights_error}"
        )

    st.subheader("タイムライン（概要欄用）")
    if result.chapters_text.strip():
        st.caption("右上のコピーアイコン、または下のボタンでテキストを取得できます。")
        st.code(result.chapters_text, language=None)
        timeline_col, timeline_copy_col = st.columns([3, 1])
        with timeline_col:
            st.download_button(
                label="タイムラインをテキストでダウンロード",
                data=result.chapters_text,
                file_name=f"{result.video_id}_chapters.txt",
                mime="text/plain",
                key=f"download_chapters_{result.video_id}",
            )
        with timeline_copy_col:
            render_copy_button(
                result.chapters_text,
                label="タイムラインをコピー",
                key=f"copy_chapters_{result.video_id}",
            )

        settings = get_settings()
        if not get_template_path(settings).is_file():
            st.info(_TEMPLATE_NOT_SET_MESSAGE)

        try:
            description_text = build_description(result.video_id, settings=settings)
            render_copy_button(
                description_text,
                label="概要欄用テキストをコピー",
                key=f"copy_description_{result.video_id}",
            )
        except DescriptionError as exc:
            st.error(str(exc))
    else:
        st.info(_CHAPTERS_NOT_GENERATED_MESSAGE)

    with st.expander("文字起こし全文", expanded=False):
        st.text_area(
            "全文",
            value=result.full_transcript_text,
            height=400,
            disabled=True,
            label_visibility="collapsed",
        )
        transcript_col, transcript_copy_col = st.columns([3, 1])
        with transcript_col:
            st.download_button(
                label="全文をダウンロード",
                data=result.full_transcript_text,
                file_name=f"{result.video_id}_transcript.txt",
                mime="text/plain",
                key=f"download_transcript_{result.video_id}",
            )
        with transcript_copy_col:
            render_copy_button(
                result.full_transcript_text,
                label="全文をコピー",
                key=f"copy_transcript_{result.video_id}",
            )

    if result.clips_candidates:
        st.subheader("切り抜き候補")
        st.caption("候補を選んで「切り出し」を押すと ffmpeg で動画を切り出します。")

        candidate_labels = [
            f"{c.id}: {c.title}（{c.start} → {c.end}、{c.duration_sec // 60} 分）"
            for c in result.clips_candidates
        ]
        selected_idx = st.radio(
            "候補を選択",
            range(len(result.clips_candidates)),
            format_func=lambda i: candidate_labels[i],
            label_visibility="collapsed",
            key=clip_candidate_radio_key(result.video_id),
        )
        selected = result.clips_candidates[selected_idx]
        st.markdown(f"**理由:** {selected.reason}")

        settings = get_settings()
        busy = is_busy(settings)
        cut_clicked = st.button(
            "切り出し",
            type="secondary",
            key=cut_clip_button_key(result.video_id),
            disabled=cut_clip_button_disabled(busy=busy),
        )

        if cut_clicked:
            _start_cut_clip(result, selected, settings)

        cut_result: CutResult | None = get_cut_result()
        if cut_result is not None and cut_result.video_id == result.video_id:
            st.success("切り出しが完了しました。")
            st.markdown(f"**保存先:** `{cut_result.output_path}`")
            st.markdown(f"**コマンドログ:** `{cut_result.command_log_path}`")
            note = source_cache_note(result.video_id, get_settings())
            if note is not None:
                st.caption(note)
    elif not result.clips_error:
        st.info(clips_empty_message(result.clips_requested))
