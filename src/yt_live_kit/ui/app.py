"""Streamlit Web UI — URL → タイムライン縦一本 + 切り抜き + 一覧・一括."""

from __future__ import annotations

import streamlit as st

from yt_live_kit import __version__
from yt_live_kit.config import get_settings
from yt_live_kit.services.batch import parse_urls, run_batch
from yt_live_kit.services.ffmpeg import CutResult, FfmpegError, cut_clip
from yt_live_kit.services.history import list_processed_videos
from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_CLIPS_SUGGEST,
    STAGE_FETCH,
    STAGE_LABELS,
    STAGE_MESSAGES,
    STAGE_TRANSCRIPT,
    PipelineError,
    PipelineResult,
    load_result_from_disk,
    run,
)
from yt_live_kit.services.ytdlp import check_ytdlp_version_warning

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
        elif state == "warning":
            lines.append(f"⚠️ **{label}** — 警告（他の結果は利用できます）")
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

            st.session_state[_SESSION_CUT_RESULT] = cut_result

        cut_result: CutResult | None = st.session_state.get(_SESSION_CUT_RESULT)
        if cut_result is not None and cut_result.video_id == result.video_id:
            st.success("切り出しが完了しました。")
            st.markdown(f"**保存先:** `{cut_result.output_path}`")
            st.markdown(f"**コマンドログ:** `{cut_result.command_log_path}`")
    elif not result.clips_error:
        st.info("切り抜き候補がありません。Codex CLI の設定を確認してください。")


def _run_single_url(url: str) -> None:
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
                if s == STAGE_CLIPS_SUGGEST and result.clips_error:
                    progress_state[s] = "warning"
                else:
                    progress_state[s] = "complete"
            progress_placeholder.markdown(_render_progress(progress_state, progress_ctx))
            label = "完了（一部警告あり）" if result.clips_error else "完了"
            status.update(label=label, state="complete", expanded=False)
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


def _run_batch_urls(urls: list[str], skip_existing: bool) -> None:
    settings = get_settings()
    total = len(urls)

    st.session_state[_SESSION_RESULT] = None
    st.session_state[_SESSION_CUT_RESULT] = None

    progress_bar = st.progress(0, text=f"0 / {total} 件")
    status_text = st.empty()
    log_area = st.empty()

    log_lines: list[str] = []
    last_result: PipelineResult | None = None

    def on_progress(current: int, _total: int, item_url: str, message: str) -> None:
        progress_bar.progress(current / total, text=f"{current} / {total} 件")
        status_text.markdown(f"**{current}/{total}** — `{item_url}` — {message}")

    results = run_batch(
        urls,
        settings,
        skip_existing=skip_existing,
        on_progress=on_progress,
    )

    for item in results:
        if item.status == "success":
            log_lines.append(f"✅ {item.url} → {item.video_id}")
            if item.result is not None:
                last_result = item.result
        elif item.status == "skipped":
            log_lines.append(f"⏭️ {item.url} → スキップ（処理済み）")
        else:
            log_lines.append(f"❌ {item.url} → {item.error}")

    log_area.markdown("\n\n".join(log_lines))

    success = sum(1 for r in results if r.status == "success")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")

    if failed == 0:
        st.success(f"一括処理完了: 成功 {success} / スキップ {skipped}")
    else:
        st.warning(f"一括処理完了: 成功 {success} / スキップ {skipped} / 失敗 {failed}")

    if last_result is not None:
        st.session_state[_SESSION_RESULT] = last_result


st.title("yt-live-kit")
st.caption(f"v{__version__} — YouTube ライブアーカイブのタイムライン生成")

ytdlp_warning = check_ytdlp_version_warning()
if ytdlp_warning:
    st.warning(ytdlp_warning)

tab_run, tab_history = st.tabs(["実行", "処理済み一覧"])

with tab_run:
    st.markdown(
        "YouTube **公開アーカイブ** の URL を貼り付けて「実行」を押すと、"
        "概要欄用のタイムライン（チャプター）、文字起こし全文、切り抜き候補が生成されます。"
    )

    mode = st.radio(
        "実行モード",
        ["単本", "一括"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if mode == "単本":
        url = st.text_input(
            "YouTube URL",
            placeholder="https://www.youtube.com/watch?v=...",
            help="公開アーカイブのみ対応しています。",
        )
        run_clicked = st.button("実行", type="primary", disabled=not url.strip())

        if run_clicked and url.strip():
            _run_single_url(url.strip())
    else:
        batch_urls = st.text_area(
            "YouTube URL（1 行 1 本）",
            placeholder="https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/watch?v=...",
            height=150,
            help="複数 URL を改行区切りで貼り付けます。# で始まる行はコメントとして無視されます。",
        )
        skip_existing = st.checkbox(
            "処理済みをスキップ",
            value=True,
            help="チャプターが既にある動画 ID はスキップします。",
        )
        urls = parse_urls(batch_urls)
        batch_clicked = st.button(
            "一括実行",
            type="primary",
            disabled=len(urls) == 0,
        )
        if batch_clicked and urls:
            _run_batch_urls(urls, skip_existing)

with tab_history:
    settings = get_settings()
    processed = list_processed_videos(settings)

    if not processed:
        st.info("処理済みの動画がありません。実行タブから URL を処理してください。")
    else:
        st.caption(f"{len(processed)} 件の処理済み動画")
        for video in processed:
            badges = []
            if video.has_chapters:
                badges.append("チャプター")
            if video.has_transcript:
                badges.append("全文")
            if video.has_clips:
                badges.append("候補")
            badge_text = " · ".join(badges) if badges else "メタのみ"
            fetched = (
                video.fetched_at.strftime("%Y-%m-%d %H:%M")
                if video.fetched_at
                else "日時不明"
            )

            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{video.title}**")
                st.caption(f"`{video.video_id}` — {fetched} — {badge_text}")
            with col2:
                if st.button("開く", key=f"open_{video.video_id}"):
                    loaded = load_result_from_disk(video.video_id, settings)
                    if loaded is None:
                        st.error("成果物を読み込めませんでした。")
                    else:
                        st.session_state[_SESSION_RESULT] = loaded
                        st.session_state[_SESSION_CUT_RESULT] = None
                        st.rerun()

if st.session_state.get(_SESSION_RESULT) is not None:
    st.divider()
    _render_results(st.session_state[_SESSION_RESULT])
