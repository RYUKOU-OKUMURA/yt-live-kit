"""Streamlit Web UI — URL → タイムライン縦一本."""

from __future__ import annotations

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_FETCH,
    STAGE_LABELS,
    STAGE_MESSAGES,
    STAGE_TRANSCRIPT,
    PipelineError,
    run,
)

st.set_page_config(page_title="yt-live-kit", page_icon="📺", layout="wide")

st.title("yt-live-kit")
st.caption(f"v{__version__} — YouTube ライブアーカイブのタイムライン生成")

st.markdown(
    "YouTube **公開アーカイブ** の URL を貼り付けて「実行」を押すと、"
    "概要欄用のタイムライン（チャプター）と文字起こし全文が生成されます。"
)

url = st.text_input(
    "YouTube URL",
    placeholder="https://www.youtube.com/watch?v=...",
    help="公開アーカイブのみ対応しています。",
)

run_clicked = st.button("実行", type="primary", disabled=not url.strip())

if run_clicked and url.strip():
    stage_order = [STAGE_FETCH, STAGE_TRANSCRIPT, STAGE_CHAPTERS]
    progress_state: dict[str, str] = dict.fromkeys(stage_order, "pending")
    progress_ctx = {"message": STAGE_MESSAGES[STAGE_FETCH]}

    def _render_progress() -> str:
        lines: list[str] = []
        for stage_key in stage_order:
            label = STAGE_LABELS[stage_key]
            state = progress_state[stage_key]
            if state == "complete":
                lines.append(f"✅ **{label}** — 完了")
            elif state == "running":
                lines.append(f"🔄 **{label}** — {progress_ctx['message']}")
            else:
                lines.append(f"⏳ {label} — 待機中")
        return "\n\n".join(lines)

    with st.status("処理中…", expanded=True) as status:
        progress_placeholder = st.empty()
        progress_placeholder.markdown(_render_progress())

        def on_progress(stage: str, message: str) -> None:
            progress_ctx["message"] = message
            if stage in stage_order:
                idx = stage_order.index(stage)
                for i, s in enumerate(stage_order):
                    if i < idx:
                        progress_state[s] = "complete"
                    elif i == idx:
                        progress_state[s] = "running"
                    else:
                        progress_state[s] = "pending"
            progress_placeholder.markdown(_render_progress())

        try:
            result = run(url.strip(), on_progress=on_progress)
            for s in stage_order:
                progress_state[s] = "complete"
            progress_placeholder.markdown(_render_progress())
            status.update(label="完了", state="complete", expanded=False)
        except PipelineError as exc:
            status.update(label="エラー", state="error", expanded=True)
            st.error(str(exc))
            st.stop()

    st.success(f"「{result.title}」の処理が完了しました。")

    st.subheader("タイムライン（概要欄用）")
    st.caption("右上のコピーアイコン、または下のボタンでテキストを取得できます。")
    st.code(result.chapters_text, language=None)
    st.download_button(
        label="タイムラインをテキストでダウンロード",
        data=result.chapters_text,
        file_name=f"{result.video_id}_chapters.txt",
        mime="text/plain",
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
        )
