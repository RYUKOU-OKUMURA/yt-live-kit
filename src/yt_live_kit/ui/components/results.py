"""処理結果の表示（タイムライン・全文・切り抜き候補）."""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from yt_live_kit.config import get_settings
from yt_live_kit.services.description import (
    DescriptionError,
    build_description,
    get_template_path,
)
from yt_live_kit.services.ffmpeg import CutResult, FfmpegError, cut_clip
from yt_live_kit.services.pipeline import PipelineResult
from yt_live_kit.ui.state import get_cut_result, set_cut_result

_UNEXPECTED_ERROR_MSG = (
    "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
)
_TEMPLATE_NOT_SET_MESSAGE = (
    "定型文が未設定です。data/_config/description_template.txt に "
    "{{timeline}} を含むテンプレートを置くと、まとめてコピーできます。"
)


def build_clipboard_copy_html(
    *,
    text: str,
    button_id: str,
    button_label: str,
    success_message: str = "コピーしました",
    hide_after_ms: int = 3000,
) -> str:
    """クリップボードコピー用の HTML を生成する（テスト可能な純粋関数）."""
    encoded = json.dumps(text, ensure_ascii=False)
    return f"""
<div>
  <button id="{button_id}" type="button">{button_label}</button>
  <span id="{button_id}-msg" style="display:none;color:#0a7;margin-left:8px;">
    {success_message}
  </span>
</div>
<script>
  (function() {{
    const text = {encoded};
    const button = document.getElementById({json.dumps(button_id)});
    const message = document.getElementById({json.dumps(f"{button_id}-msg")});
    button.addEventListener("click", async function() {{
      try {{
        await navigator.clipboard.writeText(text);
        message.style.display = "inline";
        setTimeout(function() {{
          message.style.display = "none";
        }}, {hide_after_ms});
      }} catch (err) {{
        message.textContent = "コピーに失敗しました";
        message.style.display = "inline";
        message.style.color = "#c00";
      }}
    }});
  }})();
</script>
"""


def render_copy_button(
    text: str,
    *,
    label: str,
    key: str,
    height: int = 50,
) -> None:
    """navigator.clipboard.writeText を使うコピーボタンを描画する."""
    html = build_clipboard_copy_html(text=text, button_id=key, button_label=label)
    components.html(html, height=height)


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
