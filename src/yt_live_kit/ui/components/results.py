"""処理結果の表示（タイムライン・全文・切り抜き候補）."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.config import get_settings
from yt_live_kit.services.ffmpeg import CutResult, FfmpegError, cut_clip
from yt_live_kit.services.pipeline import PipelineResult
from yt_live_kit.ui.state import get_cut_result, set_cut_result

_UNEXPECTED_ERROR_MSG = (
    "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
)


def render_results(result: PipelineResult) -> None:
    st.success(f"「{result.title}」の処理が完了しました。")

    if result.clips_error:
        st.warning(
            "切り抜き候補の生成に失敗しましたが、タイムラインは利用できます。\n\n"
            f"{result.clips_error}"
        )

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

            set_cut_result(cut_result)

        cut_result: CutResult | None = get_cut_result()
        if cut_result is not None and cut_result.video_id == result.video_id:
            st.success("切り出しが完了しました。")
            st.markdown(f"**保存先:** `{cut_result.output_path}`")
            st.markdown(f"**コマンドログ:** `{cut_result.command_log_path}`")
    elif not result.clips_error:
        st.info("切り抜き候補がありません。Codex CLI の設定を確認してください。")
