"""結果画面内のハイライトまとめ動画セクション."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.clips import load_candidates_file
from yt_live_kit.services.highlights import HighlightsError, load_segments_file
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import PipelineResult, regenerate
from yt_live_kit.ui.state import session_state_mapping, set_active_job_id

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_BUILD_DURATION_NOTE = (
    "ffmpeg による再エンコードと連結のため、区間数によっては数分かかることがあります。"
)
_INTERMEDIATE_DELETED_NOTE = "中間ファイルは削除済みです。"
_NO_CANDIDATES_MESSAGE = (
    "ハイライト候補がありません。「ハイライト候補を生成」するか、"
    "「切り抜き候補から選ぶ」を ON にして候補を表示してください。"
)


class HighlightsJobError(HighlightsError):
    """highlights_error をジョブ失敗として伝播する."""


def format_duration_jp(total_sec: int) -> str:
    """秒数を「X 分 Y 秒」形式にフォーマットする."""
    minutes = total_sec // 60
    seconds = total_sec % 60
    if minutes > 0 and seconds > 0:
        return f"{minutes} 分 {seconds} 秒"
    if minutes > 0:
        return f"{minutes} 分"
    return f"{seconds} 秒"


def format_selection_summary(selected_count: int, total_duration_sec: int) -> str:
    """選択中の区間数と合計尺の表示文字列を返す."""
    return (
        f"選択中: {selected_count} 区間 / "
        f"合計 {format_duration_jp(total_duration_sec)}"
    )


def can_build_highlight(selected_count: int) -> bool:
    """ハイライト動画作成ボタンを有効にするかどうか."""
    return selected_count >= 2


def sum_duration_sec(segments: Sequence[HighlightSegment]) -> int:
    """区間リストの duration_sec 合計を返す."""
    return sum(segment.duration_sec for segment in segments)


def segment_checkbox_key(video_id: str, segment_id: str) -> str:
    """区間チェックボックスの session_state キーを返す."""
    return f"hl_cb_{video_id}_{segment_id}"


def use_clips_toggle_key(video_id: str) -> str:
    """切り抜き候補トグルの session_state キーを返す."""
    return f"highlights_use_clips_{video_id}"


def clip_to_highlight_segment(clip: ClipCandidate) -> HighlightSegment:
    """切り抜き候補をハイライト区間表示用に変換する."""
    return HighlightSegment(
        id=clip.id,
        title=clip.title,
        start=clip.start,
        end=clip.end,
        duration_sec=clip.duration_sec,
        reason=clip.reason,
    )


def load_display_segments(
    video_id: str,
    settings: Settings,
    *,
    use_clips: bool,
) -> list[HighlightSegment]:
    """表示用のハイライト区間リストを読み込む."""
    if use_clips:
        clips_doc = load_candidates_file(video_id, settings)
        if clips_doc is None:
            return []
        return [clip_to_highlight_segment(c) for c in clips_doc.candidates]

    highlights_doc = load_segments_file(video_id, settings)
    if highlights_doc is None:
        return []
    return list(highlights_doc.candidates)


def collect_selected_segments(
    segments: Sequence[HighlightSegment],
    video_id: str,
    session_state: Mapping[str, object],
) -> list[HighlightSegment]:
    """session_state から選択済み区間を集める."""
    selected: list[HighlightSegment] = []
    for segment in segments:
        key = segment_checkbox_key(video_id, segment.id)
        if session_state.get(key):
            selected.append(segment)
    return selected


def format_segment_label(segment: HighlightSegment) -> str:
    """区間チェックボックスのラベルを返す."""
    return (
        f"{segment.start} → {segment.end} / "
        f"{format_duration_jp(segment.duration_sec)} / {segment.title}"
    )


def regenerate_highlights_job_target(
    *,
    report,
    settings,
    video_id: str,
    job_id: str | None = None,
) -> None:
    """ハイライト候補再生成用: softfail の highlights_error はジョブ失敗に昇格する."""
    from yt_live_kit.services.jobs import get_active_job, update_job

    def on_progress(stage: str, message: str) -> None:
        report(stage=stage, message=message)

    result = regenerate(
        video_id,
        target="highlights",
        settings=settings,
        on_progress=on_progress,
    )
    if result.highlights_error:
        raise HighlightsJobError(result.highlights_error)

    if job_id is None:
        active = get_active_job(settings)
        job_id = active.job_id if active is not None else None
    if job_id is not None:
        update_job(
            job_id,
            settings=settings,
            video_id=result.video_id,
            title=result.title,
        )


def build_highlight_job_target(
    *,
    report,
    settings,
    video_id: str,
    segment_dicts: list[dict[str, object]],
    job_id: str | None = None,
) -> None:
    """ハイライト動画連結用: cut_and_concat の進捗を report に橋渡しする."""
    from yt_live_kit.services.ffmpeg import cut_and_concat
    from yt_live_kit.services.jobs import get_active_job, update_job

    segments = [HighlightSegment.model_validate(item) for item in segment_dicts]

    def on_progress(current: int, total: int, message: str) -> None:
        report(current=current, total=total, message=message)

    cut_and_concat(
        video_id,
        segments,
        settings,
        ffmpeg_path=settings.ffmpeg_path,
        on_progress=on_progress,
    )

    if job_id is None:
        active = get_active_job(settings)
        job_id = active.job_id if active is not None else None
    if job_id is not None:
        update_job(
            job_id,
            settings=settings,
            video_id=video_id,
        )


def _start_regenerate_highlights(result: PipelineResult, settings: Settings) -> None:
    try:
        job_id = start_job(
            "regenerate",
            regenerate_highlights_job_target,
            video_id=result.video_id,
            title=result.title,
            settings=settings,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


def _start_build_highlight(
    result: PipelineResult,
    selected: list[HighlightSegment],
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "highlights",
            build_highlight_job_target,
            video_id=result.video_id,
            title=result.title,
            total=len(selected),
            settings=settings,
            segment_dicts=[segment.model_dump() for segment in selected],
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


@st.dialog("ハイライト候補の再生成を確認")
def _confirm_regenerate_highlights_dialog(
    result: PipelineResult,
    settings: Settings,
) -> None:
    st.warning(
        "ハイライト候補を再生成します。既存の候補は退避された後に上書きされます。"
    )
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "再生成を実行",
        key=f"hl_confirm_generate_{result.video_id}",
        type="primary",
        disabled=busy,
    ):
        _start_regenerate_highlights(result, settings)


@st.dialog("ハイライト動画の再作成を確認")
def _confirm_build_highlight_dialog(
    result: PipelineResult,
    selected: list[HighlightSegment],
    settings: Settings,
) -> None:
    st.warning("既存のハイライト動画を上書きして再作成します。")
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "再作成を実行",
        key=f"hl_confirm_build_{result.video_id}",
        type="primary",
        disabled=busy,
    ):
        _start_build_highlight(result, selected, settings)


def _start_or_confirm_regenerate_highlights(
    result: PipelineResult,
    settings: Settings,
    *,
    output_exists: bool,
) -> None:
    if output_exists:
        _confirm_regenerate_highlights_dialog(result, settings)
    else:
        _start_regenerate_highlights(result, settings)


def _start_or_confirm_build_highlight(
    result: PipelineResult,
    selected: list[HighlightSegment],
    settings: Settings,
    *,
    output_exists: bool,
) -> None:
    if output_exists:
        _confirm_build_highlight_dialog(result, selected, settings)
    else:
        _start_build_highlight(result, selected, settings)


def _render_output_video(video_id: str, settings: Settings) -> None:
    output_path = settings.data_dir / video_id / "highlights" / "output" / "highlight.mp4"
    log_path = output_path.parent / "highlight.ffmpeg.log"
    if not output_path.is_file():
        return

    st.success("ハイライト動画が作成されました。")
    with st.container(width=720):
        st.video(str(output_path))
    st.markdown(f"**保存先:** `{output_path}`")
    st.markdown(f"**コマンドログ:** `{log_path}`")
    st.caption(_INTERMEDIATE_DELETED_NOTE)


def render_highlights_section(result: PipelineResult) -> None:
    """結果画面の末尾にハイライトまとめ動画セクションを描画する."""
    settings = get_settings()
    busy = is_busy()

    expander = st.expander(
        "補助: ハイライトまとめ動画",
        expanded=False,
        key=f"highlights_auxiliary_{result.video_id}",
        on_change="rerun",
    )
    if not expander.open:
        return
    with expander:
        if busy:
            st.info(_BUSY_MESSAGE)

        st.caption(_BUILD_DURATION_NOTE)

        gen_cols = st.columns([2, 2])
        with gen_cols[0]:
            segments_path = (
                settings.data_dir / result.video_id / "highlights" / "segments.json"
            )
            if st.button(
                "ハイライト候補を生成",
                key=f"hl_generate_{result.video_id}",
                disabled=busy,
            ):
                _start_or_confirm_regenerate_highlights(
                    result,
                    settings,
                    output_exists=segments_path.is_file(),
                )

        use_clips = st.toggle(
            "切り抜き候補から選ぶ",
            key=use_clips_toggle_key(result.video_id),
            disabled=busy,
        )

        segments = load_display_segments(
            result.video_id,
            settings,
            use_clips=use_clips,
        )

        if not segments:
            st.info(_NO_CANDIDATES_MESSAGE)
        else:
            source_label = "切り抜き候補" if use_clips else "ハイライト候補"
            st.caption(f"{source_label}: {len(segments)} 件")

            for segment in segments:
                st.checkbox(
                    format_segment_label(segment),
                    key=segment_checkbox_key(result.video_id, segment.id),
                    disabled=busy,
                )
                st.caption(f"理由: {segment.reason}")

            selected = collect_selected_segments(
                segments,
                result.video_id,
                session_state_mapping(),
            )
            st.markdown(format_selection_summary(len(selected), sum_duration_sec(selected)))

            build_disabled = busy or not can_build_highlight(len(selected))
            build_help = None
            if not busy and len(selected) < 2:
                build_help = "2 区間以上を選んでください。"

            if st.button(
                "ハイライト動画を作成",
                key=f"hl_build_{result.video_id}",
                type="primary",
                disabled=build_disabled,
                help=build_help,
            ):
                output_path = (
                    settings.data_dir
                    / result.video_id
                    / "highlights"
                    / "output"
                    / "highlight.mp4"
                )
                _start_or_confirm_build_highlight(
                    result,
                    selected,
                    settings,
                    output_exists=output_path.is_file(),
                )

        _render_output_video(result.video_id, settings)
