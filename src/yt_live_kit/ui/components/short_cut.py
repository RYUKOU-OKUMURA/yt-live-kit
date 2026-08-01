"""長い候補を刻んでショートにする導線（FR-30）.

180 秒を超える切り抜き候補 / ハイライト候補を親として選び、AI にサブ区間を
提案させ、人が採否と境界を確認してから FR-25 のジャンプカット連結へ渡す。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.short_cut import ShortCutDocument
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.short_cut import (
    MAX_TOTAL_MS,
    MIN_TOTAL_MS,
    ParentCandidate,
    ShortCutValidationResult,
    load_cut_plan,
    needs_short_cut,
    suggest_short_cuts,
    validate_short_cut_selection,
)
from yt_live_kit.services.shorts import build_short_from_segments
from yt_live_kit.services.telop import make_clip_id
from yt_live_kit.ui.state import set_active_job_id

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_TIMESTAMP_FORMAT_ERROR = "時刻は HH:MM:SS の形式で入力してください。"
_SECTION_NOTE = (
    "180 秒を超える候補から、ショート 1 本分の区間を AI に提案させます。"
    "提案は必ず確認・調整してから作成してください。"
)
_NO_LONG_CANDIDATE_MESSAGE = (
    "180 秒を超える候補がありません。"
    "そのままショートにできる候補は上の「ショートを作成」から生成してください。"
)
_HH_MM_SS_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")

LAYOUT_BLUR_LABEL = "ぼかし背景（推奨）"
LAYOUT_CROP_LABEL = "中央クロップ"
LAYOUT_LABELS = (LAYOUT_BLUR_LABEL, LAYOUT_CROP_LABEL)


@dataclass(frozen=True)
class ParentOption:
    """親候補の選択肢（表示用ソース名つき）."""

    source_label: str
    candidate: ParentCandidate

    @property
    def id(self) -> str:
        return self.candidate.id

    @property
    def label(self) -> str:
        return (
            f"[{self.source_label}] {self.candidate.id}: {self.candidate.title}"
            f"（{self.candidate.start} → {self.candidate.end}、"
            f"{self.candidate.duration_sec} 秒）"
        )


def collect_parent_options(
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
) -> list[ParentOption]:
    """刻む対象になり得る（180 秒超の）候補だけを表示順で返す."""
    options: list[ParentOption] = []
    for candidate in clip_candidates:
        if needs_short_cut(candidate):
            options.append(ParentOption("切り抜き", candidate))
    for segment in highlight_candidates:
        if needs_short_cut(segment):
            options.append(ParentOption("ハイライト", segment))
    return options


def layout_from_label(label: str) -> str:
    """UI ラベルからレイアウト値（blur / crop）を返す."""
    return "crop" if label == LAYOUT_CROP_LABEL else "blur"


def parse_cut_timestamp(text: str) -> tuple[float | None, str | None]:
    """HH:MM:SS を秒に変換する。不正ならエラーメッセージを返す."""
    stripped = text.strip()
    if not stripped or not _HH_MM_SS_RE.match(stripped):
        return None, _TIMESTAMP_FORMAT_ERROR
    try:
        return float(parse_timestamp_to_seconds(stripped)), None
    except ValueError:
        return None, _TIMESTAMP_FORMAT_ERROR


def format_total_ms(total_ms: int) -> str:
    """合計尺の表示文字列を返す."""
    return f"{total_ms / 1000:.1f} 秒"


def checkbox_key(video_id: str, cut_id: str) -> str:
    """採否チェックボックスの session_state キーを返す."""
    return f"short_cut_cb_{video_id}_{cut_id}"


def start_key(video_id: str, cut_id: str) -> str:
    """開始時刻入力の session_state キーを返す."""
    return f"short_cut_start_{video_id}_{cut_id}"


def end_key(video_id: str, cut_id: str) -> str:
    """終了時刻入力の session_state キーを返す."""
    return f"short_cut_end_{video_id}_{cut_id}"


def collect_edited_segments(
    document: ShortCutDocument,
    video_id: str,
    session_state: Mapping[str, object],
) -> tuple[list[HighlightSegment], list[str]]:
    """session_state から採用中の区間を、編集後の時刻で組み立てる."""
    segments: list[HighlightSegment] = []
    errors: list[str] = []
    for candidate in document.candidates:
        if not session_state.get(checkbox_key(video_id, candidate.id), True):
            continue
        start_text = session_state.get(start_key(video_id, candidate.id))
        end_text = session_state.get(end_key(video_id, candidate.id))
        start_value = candidate.start if start_text is None else str(start_text)
        end_value = candidate.end if end_text is None else str(end_text)

        start_sec, start_error = parse_cut_timestamp(start_value)
        if start_error:
            errors.append(f"{candidate.id}: 開始{start_error}")
            continue
        end_sec, end_error = parse_cut_timestamp(end_value)
        if end_error:
            errors.append(f"{candidate.id}: 終了{end_error}")
            continue
        assert start_sec is not None and end_sec is not None
        if end_sec <= start_sec:
            errors.append(f"{candidate.id}: 終了時刻は開始時刻より後にしてください。")
            continue

        segments.append(
            HighlightSegment(
                id=candidate.id,
                title=candidate.title,
                start=start_value.strip(),
                end=end_value.strip(),
                duration_sec=int(end_sec - start_sec),
                reason=candidate.reason,
            )
        )
    return segments, errors


def segments_to_pairs(segments: Sequence[HighlightSegment]) -> list[tuple[float, float]]:
    """区間列を build_short_from_segments 用の秒ペアへ変換する."""
    pairs: list[tuple[float, float]] = []
    for segment in segments:
        start_sec, _ = parse_cut_timestamp(segment.start)
        end_sec, _ = parse_cut_timestamp(segment.end)
        if start_sec is None or end_sec is None:
            raise ValueError(f"{segment.id}: {_TIMESTAMP_FORMAT_ERROR}")
        pairs.append((start_sec, end_sec))
    return pairs


def short_cut_output_path(
    video_id: str,
    segments: Sequence[HighlightSegment],
    settings: Settings,
) -> Path:
    """確定区間から決まる出力 mp4 のパスを返す（FR-25 と同じ命名）."""
    clip_id = make_clip_id(segments_to_pairs(segments))
    return (
        settings.data_dir / video_id / "shorts" / "output" / f"short_{clip_id}.mp4"
    )


def build_disabled_message(
    validation: ShortCutValidationResult,
    parse_errors: Sequence[str],
) -> str | None:
    """作成ボタンを無効にする理由（日本語）を返す."""
    if parse_errors:
        return "、".join(parse_errors)
    if not validation.ok:
        return "、".join(validation.errors)
    return None


def suggest_short_cut_job_target(
    *,
    report,
    settings: Settings,
    video_id: str,
    parent_dict: dict,
    parent_is_clip: bool,
    job_id: str | None = None,
) -> None:
    """start_job 用: 親候補のサブ区間を提案して保存する."""

    def on_progress(stage: str, message: str) -> None:
        report(stage=stage, message=message)

    parent: ParentCandidate = (
        ClipCandidate.model_validate(parent_dict)
        if parent_is_clip
        else HighlightSegment.model_validate(parent_dict)
    )
    suggest_short_cuts(
        video_id,
        parent,
        settings,
        on_progress=on_progress,
    )


def build_short_cut_job_target(
    *,
    report,
    settings: Settings,
    video_id: str,
    segment_pairs: list[list[float]],
    layout: str,
    job_id: str | None = None,
) -> None:
    """start_job 用: 確定区間を連結してショートを生成する."""

    def on_progress(current: int, total: int, message: str) -> None:
        report(current=current, total=total, message=message)

    result = build_short_from_segments(
        video_id,
        [(float(start), float(end)) for start, end in segment_pairs],
        settings,
        layout=layout,
        on_progress=on_progress,
        ffmpeg_path=settings.ffmpeg_path,
    )
    meta_path = result.output_path.with_name(f"{result.output_path.stem}.meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "video_id": result.video_id,
                "output_path": str(result.output_path),
                "command_log_path": str(result.command_log_path),
                "layout": result.layout,
                "burned_subtitles": result.burned_subtitles,
                "duration_sec": result.duration_sec,
                "font_warning": result.font_warning,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _start_suggest(
    *,
    video_id: str,
    title: str,
    option: ParentOption,
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "short_cut",
            suggest_short_cut_job_target,
            video_id=video_id,
            title=title,
            settings=settings,
            parent_dict=option.candidate.model_dump(mode="json"),
            parent_is_clip=isinstance(option.candidate, ClipCandidate),
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


def _start_build(
    *,
    video_id: str,
    title: str,
    segments: Sequence[HighlightSegment],
    layout: str,
    settings: Settings,
) -> None:
    try:
        pairs = segments_to_pairs(segments)
    except ValueError as exc:
        st.error(str(exc))
        return
    try:
        job_id = start_job(
            "shorts",
            build_short_cut_job_target,
            video_id=video_id,
            title=title,
            total=len(pairs) + 3,
            settings=settings,
            segment_pairs=[[start, end] for start, end in pairs],
            layout=layout,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


@st.dialog("ショート動画の再作成を確認")
def _confirm_build_dialog(
    *,
    video_id: str,
    title: str,
    segments: Sequence[HighlightSegment],
    layout: str,
    settings: Settings,
) -> None:
    st.warning("同じ区間構成のショート動画を上書きして再作成します。")
    busy = is_busy(settings)
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "再作成を実行",
        key=f"short_cut_confirm_build_{video_id}",
        type="primary",
        disabled=busy,
    ):
        _start_build(
            video_id=video_id,
            title=title,
            segments=segments,
            layout=layout,
            settings=settings,
        )


def _render_plan(
    *,
    video_id: str,
    title: str,
    option: ParentOption,
    document: ShortCutDocument,
    settings: Settings,
) -> None:
    st.markdown("**提案された区間**（採用するものにチェックし、必要なら時刻を調整）")

    for candidate in document.candidates:
        st.checkbox(
            f"{candidate.id}: {candidate.title}（{candidate.duration_sec} 秒）",
            value=True,
            key=checkbox_key(video_id, candidate.id),
        )
        columns = st.columns(2)
        with columns[0]:
            st.text_input(
                "開始",
                value=candidate.start,
                key=start_key(video_id, candidate.id),
            )
        with columns[1]:
            st.text_input(
                "終了",
                value=candidate.end,
                key=end_key(video_id, candidate.id),
            )
        st.caption(candidate.reason)

    segments, parse_errors = collect_edited_segments(
        document, video_id, st.session_state
    )
    validation = validate_short_cut_selection(segments, parent=option.candidate)
    total_ms = validation.total_ms

    st.markdown(
        f"**合計: {format_total_ms(total_ms)}** "
        f"（{int(MIN_TOTAL_MS / 1000)}〜{int(MAX_TOTAL_MS / 1000)} 秒に収める必要があります）"
    )

    layout_label = st.radio(
        "レイアウト",
        LAYOUT_LABELS,
        index=0,
        key=f"short_cut_layout_{video_id}",
    )
    layout = layout_from_label(layout_label)

    disabled_message = build_disabled_message(validation, parse_errors)
    if disabled_message:
        st.warning(disabled_message)

    busy = is_busy(settings)
    output_path: Path | None = None
    if disabled_message is None:
        output_path = short_cut_output_path(video_id, segments, settings)

    if st.button(
        "刻んでショートを作成",
        type="primary",
        key=f"short_cut_build_{video_id}",
        disabled=busy or disabled_message is not None,
    ):
        action = (
            _confirm_build_dialog
            if output_path is not None and output_path.is_file()
            else _start_build
        )
        action(
            video_id=video_id,
            title=title,
            segments=segments,
            layout=layout,
            settings=settings,
        )

    if output_path is not None and output_path.is_file():
        st.success("ショート動画が生成されています。")
        st.video(str(output_path))
        st.markdown(f"**保存先:** `{output_path}`")


def render_short_cut_section(
    *,
    video_id: str,
    title: str,
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
    settings: Settings,
) -> None:
    """長い候補を刻んでショートにするセクションを描画する."""
    with st.expander("長い候補を刻んでショートにする", expanded=False):
        st.caption(_SECTION_NOTE)

        options = collect_parent_options(clip_candidates, highlight_candidates)
        if not options:
            st.info(_NO_LONG_CANDIDATE_MESSAGE)
            return

        selected_index = st.radio(
            "刻む候補",
            range(len(options)),
            format_func=lambda index: options[index].label,
            key=f"short_cut_parent_{video_id}",
        )
        option = options[selected_index]

        document = load_cut_plan(video_id, option.id, settings)
        busy = is_busy(settings)

        if st.button(
            "区間を提案し直す" if document is not None else "ショート用の区間を提案",
            key=f"short_cut_suggest_{video_id}",
            disabled=busy,
        ):
            _start_suggest(
                video_id=video_id,
                title=title,
                option=option,
                settings=settings,
            )

        if document is None:
            st.info(
                "まだ提案がありません。「ショート用の区間を提案」を押してください。"
            )
            return

        _render_plan(
            video_id=video_id,
            title=title,
            option=option,
            document=document,
            settings=settings,
        )
