"""Streamlit Web UI — URL → タイムライン縦一本 + 切り抜き."""

from __future__ import annotations

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.config import get_settings
from yt_live_kit.services.ffmpeg import CutResult, FfmpegError, cut_clip
from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_CLIPS_SUGGEST,
    STAGE_FETCH,
    STAGE_LABELS,
    STAGE_MESSAGES,
    STAGE_TRANSCRIPT,
    PipelineError,
    PipelineResult,
    run,
)

st.set_page_config(page_title="yt-live-kit", page_icon="📺", layout="wide")

_SESSION_RESULT = "pipeline_result"
_SESSION_CUT_RESULT = "cut_result"
_UNEXPECTED_ERROR_MSG = (
    "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
)

_STAGE_ORDER = [STAGE_FETCH, STAGE_TRANSCRIPT, STAGE_CHAPTERS, STAGE_CLIPS_SUGGEST]


def _render_progress(
    progress_state: dict[str, str],
    progress_ctx: dict[str, str],
) -> str:
    lines: list[str] = []
    for stage_key in _STAGE_ORDER:
        label = STAGE_LABELS[stage_key]
        state = progress_state[stage_key]
        if state == "complete":
            lines.append(f"✅ **{label}** — 完了")
        elif state == "running":
            lines.append(f"🔄 **{label}** — {progress_ctx['message']}")
        elif state == "error":
            lines.append(f"❌ **{label}** — エラー")
        else:
            lines.append(f"⏳ {label} — 待機中")
    return "\n\n".join(lines)


def _mark_failed_stage(progress_state: dict[str, str]) -> None:
    for stage_key in _STAGE_ORDER:
        if progress_state[stage_key] == "running":
            progress_state[stage_key] = "error"
            return


def _show_pipeline_error(
    exc: Exception,
    *,
    progress_state: dict[str, str],
    progress_ctx: dict[str, str],
    progress_placeholder: st.delta_generator.DeltaGenerator,
    status: st.delta_generator.DeltaGenerator,
) -> None:
    _mark_failed_stage(progress_state)
    progress_placeholder.markdown(_render_progress(progress_state, progress_ctx))
    status.update(label="エラー", state="error", expanded=True)
    if isinstance(exc, PipelineError):
        st.error(str(exc))
    else:
        st.error(_UNEXPECTED_ERROR_MSG)
    st.stop()


def _render_results(result: PipelineResult) -> None:
    st.success(f"「{result.title}」の処理が完了しました。")

    st.subheader("タイムライン（概要欄用）")
    st.caption("右上のコピーアイコン、または下のボタンでテキストを取得できます。")
    st.code(result.chapters_text, language=None)
    st.download_button(
        label="タイムラインをテキストでダウンロード",
        data=result.chapters_text,
        file_name=f"{result.video_id}_chapters.txt",
        mime="text/plain",
        key="download_chapters",
    )

    with st.expander("文字起こし全文", expanded=False):
        st.text_area(
            "全文",
            value=result.full_transcript_text,
            height=400,
            disabled=True,
            label_visibility="collapsed",
        )
        st.download_button(
            label="全文をダウンロード",
            data=result.full_transcript_text,
            file_name=f"{result.video_id}_transcript.txt",
            mime="text/plain",
            key="download_transcript",
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
            key="clip_candidate_radio",
        )
        selected = result.clips_candidates[selected_idx]
        st.markdown(f"**理由:** {selected.reason}")

        cut_clicked = st.button("切り出し", type="secondary", key="cut_clip")

        if cut_clicked:
            settings = get_settings()
            with st.status("切り出し中…", expanded=True) as cut_status:
                try:
                    cut_result = cut_clip(
                        result.video_id,
                        selected.start,
                        selected.end,
                        settings,
                        output_name=f"{selected.id}.mp4",
                        ffmpeg_path=settings.ffmpeg_path,
                    )
                    cut_status.update(label="切り出し完了", state="complete", expanded=False)
                except FfmpegError as exc:
                    cut_status.update(label="切り出しエラー", state="error", expanded=True)
                    st.error(str(exc))
                    st.stop()
                except Exception:
                    cut_status.update(label="切り出しエラー", state="error", expanded=True)
                    st.error(_UNEXPECTED_ERROR_MSG)
                    st.stop()

            st.session_state[_SESSION_CUT_RESULT] = cut_result

        cut_result: CutResult | None = st.session_state.get(_SESSION_CUT_RESULT)
        if cut_result is not None and cut_result.video_id == result.video_id:
            st.success("切り出しが完了しました。")
            st.markdown(f"**保存先:** `{cut_result.output_path}`")
            st.markdown(f"**コマンドログ:** `{cut_result.command_log_path}`")
    else:
        st.info("切り抜き候補がありません。Codex CLI の設定を確認してください。")


st.title("yt-live-kit")
st.caption(f"v{__version__} — YouTube ライブアーカイブのタイムライン生成")

st.markdown(
    "YouTube **公開アーカイブ** の URL を貼り付けて「実行」を押すと、"
    "概要欄用のタイムライン（チャプター）、文字起こし全文、切り抜き候補が生成されます。"
)

url = st.text_input(
    "YouTube URL",
    placeholder="https://www.youtube.com/watch?v=...",
    help="公開アーカイブのみ対応しています。",
)

run_clicked = st.button("実行", type="primary", disabled=not url.strip())

if run_clicked and url.strip():
    st.session_state[_SESSION_RESULT] = None
    st.session_state[_SESSION_CUT_RESULT] = None

    progress_state: dict[str, str] = dict.fromkeys(_STAGE_ORDER, "pending")
    progress_ctx: dict[str, str] = {"message": STAGE_MESSAGES[STAGE_FETCH]}

    with st.status("処理中…", expanded=True) as status:
        progress_placeholder = st.empty()
        progress_placeholder.markdown(_render_progress(progress_state, progress_ctx))

        def on_progress(stage: str, message: str) -> None:
            progress_ctx["message"] = message
            if stage in _STAGE_ORDER:
                idx = _STAGE_ORDER.index(stage)
                for i, s in enumerate(_STAGE_ORDER):
                    if i < idx:
                        progress_state[s] = "complete"
                    elif i == idx:
                        progress_state[s] = "running"
                    else:
                        progress_state[s] = "pending"
            progress_placeholder.markdown(_render_progress(progress_state, progress_ctx))

        try:
            result = run(url.strip(), on_progress=on_progress)
            for s in _STAGE_ORDER:
                progress_state[s] = "complete"
            progress_placeholder.markdown(_render_progress(progress_state, progress_ctx))
            status.update(label="完了", state="complete", expanded=False)
        except Exception as exc:
            _show_pipeline_error(
                exc,
                progress_state=progress_state,
                progress_ctx=progress_ctx,
                progress_placeholder=progress_placeholder,
                status=status,
            )

    st.session_state[_SESSION_RESULT] = result
    st.session_state[_SESSION_CUT_RESULT] = None

if st.session_state.get(_SESSION_RESULT) is not None:
    _render_results(st.session_state[_SESSION_RESULT])
