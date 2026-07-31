"""縦型ショート動画セクション（結果画面内）."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.clips import load_candidates_file
from yt_live_kit.services.highlights import load_segments_file
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import PipelineResult
from yt_live_kit.services.shorts import (
    MAX_DURATION_SEC,
    MIN_DURATION_SEC,
    ShortResult,
    build_short,
)
from yt_live_kit.services.subtitle_burn import is_japanese_font_available
from yt_live_kit.ui.state import set_active_job_id

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_TIMESTAMP_FORMAT_ERROR = "時刻は HH:MM:SS の形式で入力してください。"
_DURATION_NOTE = (
    "ショート生成は 2 パスで再エンコードが 2 回走るため、"
    "数十秒〜数分かかります。進捗は画面上部のステータスバーに表示されます。"
)
_FONT_PRE_WARNING = (
    "日本語フォントが見つかりません。"
    "字幕を焼き込むと文字化けする可能性があります。"
)

SOURCE_CLIPS = "clips"
SOURCE_HIGHLIGHTS = "highlights"
SOURCE_MANUAL = "manual"

SOURCE_LABELS: dict[str, str] = {
    SOURCE_CLIPS: "切り抜き候補から選ぶ",
    SOURCE_HIGHLIGHTS: "ハイライト候補から選ぶ",
    SOURCE_MANUAL: "開始・終了時刻を手入力",
}

LAYOUT_BLUR_LABEL = "ぼかし背景（推奨）"
LAYOUT_CROP_LABEL = "中央クロップ"
LAYOUT_LABELS = (LAYOUT_BLUR_LABEL, LAYOUT_CROP_LABEL)

_HH_MM_SS_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")


@dataclass(frozen=True)
class ShortInterval:
    """ショート区間（秒）."""

    start: float
    end: float


def layout_from_label(label: str) -> str:
    """UI ラベルからレイアウト値（blur / crop）を返す."""
    if label == LAYOUT_CROP_LABEL:
        return "crop"
    return "blur"


def parse_manual_timestamp(text: str) -> tuple[float | None, str | None]:
    """手入力時刻（HH:MM:SS）を秒に変換する。不正ならエラーメッセージを返す."""
    stripped = text.strip()
    if not stripped or not _HH_MM_SS_RE.match(stripped):
        return None, _TIMESTAMP_FORMAT_ERROR
    try:
        return float(parse_timestamp_to_seconds(stripped)), None
    except ValueError:
        return None, _TIMESTAMP_FORMAT_ERROR


def interval_from_timestamps(start_ts: str, end_ts: str) -> tuple[ShortInterval | None, str | None]:
    """候補の開始・終了時刻文字列から区間を作る."""
    try:
        start = float(parse_timestamp_to_seconds(start_ts))
        end = float(parse_timestamp_to_seconds(end_ts))
    except ValueError:
        return None, "候補の時刻形式が不正です。"
    return ShortInterval(start=start, end=end), None


def validate_interval_duration(start: float, end: float) -> tuple[bool, str | None]:
    """ショート区間の尺（10〜180 秒）を検証する."""
    if end <= start:
        return False, "終了時刻は開始時刻より後である必要があります。"
    duration = end - start
    if duration < MIN_DURATION_SEC:
        return False, (
            f"ショート動画の長さは {int(MIN_DURATION_SEC)} 秒以上である必要があります。"
            f"（指定: {duration:.1f} 秒）"
        )
    if duration > MAX_DURATION_SEC:
        return False, (
            f"ショート動画の長さは {int(MAX_DURATION_SEC)} 秒以下である必要があります。"
            f"（指定: {duration:.1f} 秒）"
        )
    return True, None


def short_output_path(
    video_id: str,
    start: float,
    end: float,
    settings: Settings,
) -> Path:
    """ショート出力 mp4 のパスを返す（services と同じ命名）."""
    return (
        settings.data_dir
        / video_id
        / "shorts"
        / "output"
        / f"short_{start:g}_{end:g}.mp4"
    )


def short_meta_path(output_path: Path) -> Path:
    """ショート出力に対応するメタ JSON のパスを返す."""
    return output_path.with_name(f"{output_path.stem}.meta.json")


def write_short_meta(result: ShortResult) -> Path:
    """生成結果のメタデータを出力隣に保存する（font_warning 表示用）."""
    meta_path = short_meta_path(result.output_path)
    payload = {
        "video_id": result.video_id,
        "output_path": str(result.output_path),
        "command_log_path": str(result.command_log_path),
        "layout": result.layout,
        "burned_subtitles": result.burned_subtitles,
        "duration_sec": result.duration_sec,
        "font_warning": result.font_warning,
    }
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta_path


def load_short_meta(output_path: Path) -> dict[str, Any] | None:
    """保存済みショートメタを読み込む."""
    meta_path = short_meta_path(output_path)
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def build_short_job_target(
    *,
    report: Any,
    settings: Settings,
    job_id: str,
    video_id: str,
    start: float,
    end: float,
    layout: str,
    burn_subtitles: bool,
) -> None:
    """start_job 用: 縦型ショートを生成し、メタ JSON を書き出す."""

    def on_progress(message: str) -> None:
        report(message=message)

    result = build_short(
        video_id,
        start,
        end,
        settings,
        layout=layout,
        burn_subtitles=burn_subtitles,
        on_progress=on_progress,
        ffmpeg_path=settings.ffmpeg_path,
    )
    write_short_meta(result)


def _resolve_manual_interval(
    start_text: str,
    end_text: str,
) -> tuple[ShortInterval | None, str | None]:
    start, start_err = parse_manual_timestamp(start_text)
    if start_err:
        return None, start_err
    end, end_err = parse_manual_timestamp(end_text)
    if end_err:
        return None, end_err
    assert start is not None and end is not None
    return ShortInterval(start=start, end=end), None


def _start_build_short(
    *,
    video_id: str,
    title: str,
    interval: ShortInterval,
    layout: str,
    burn_subtitles: bool,
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "short",
            build_short_job_target,
            video_id=video_id,
            title=title,
            settings=settings,
            start=interval.start,
            end=interval.end,
            layout=layout,
            burn_subtitles=burn_subtitles,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


def _render_existing_output(
    output_path: Path,
    *,
    interval: ShortInterval,
) -> None:
    if not output_path.is_file():
        return

    st.success("ショート動画が生成されています。")
    st.video(str(output_path))

    meta = load_short_meta(output_path)
    command_log = (
        meta.get("command_log_path") if meta else None
    )
    st.markdown(f"**保存先:** `{output_path}`")
    if command_log:
        st.markdown(f"**コマンドログ:** `{command_log}`")

    font_warning = meta.get("font_warning") if meta else None
    if font_warning:
        st.warning(str(font_warning))

    duration = interval.end - interval.start
    st.caption(f"区間: {interval.start:g} 秒 → {interval.end:g} 秒（{duration:.1f} 秒）")


def render_shorts_section(result: PipelineResult) -> None:
    """結果画面の末尾に縦型ショート動画セクションを描画する."""
    settings = get_settings()
    video_id = result.video_id
    key_prefix = f"shorts_{video_id}"

    with st.expander("縦型ショート動画", expanded=False):
        st.caption(_DURATION_NOTE)

        clip_doc = load_candidates_file(video_id, settings)
        clip_candidates: list[ClipCandidate] = (
            list(clip_doc.candidates)
            if clip_doc is not None
            else list(result.clips_candidates)
        )

        highlight_doc = load_segments_file(video_id, settings)
        highlight_candidates: list[HighlightSegment] = (
            list(highlight_doc.candidates) if highlight_doc is not None else []
        )

        available_sources = [SOURCE_CLIPS, SOURCE_MANUAL]
        if highlight_candidates:
            available_sources.insert(1, SOURCE_HIGHLIGHTS)

        source = st.radio(
            "区間の指定方法",
            available_sources,
            format_func=lambda value: SOURCE_LABELS[value],
            key=f"{key_prefix}_source",
        )

        interval: ShortInterval | None = None
        interval_error: str | None = None

        if source == SOURCE_CLIPS:
            if not clip_candidates:
                st.info("切り抜き候補がありません。先に切り抜き候補を生成してください。")
            else:
                labels = [
                    f"{c.id}: {c.title}（{c.start} → {c.end}、{c.duration_sec} 秒）"
                    for c in clip_candidates
                ]
                selected_idx = st.radio(
                    "候補を選択",
                    range(len(clip_candidates)),
                    format_func=lambda i: labels[i],
                    label_visibility="collapsed",
                    key=f"{key_prefix}_clip",
                )
                selected = clip_candidates[selected_idx]
                interval, interval_error = interval_from_timestamps(
                    selected.start,
                    selected.end,
                )
                st.markdown(f"**理由:** {selected.reason}")

        elif source == SOURCE_HIGHLIGHTS:
            labels = [
                f"{s.id}: {s.title}（{s.start} → {s.end}、{s.duration_sec} 秒）"
                for s in highlight_candidates
            ]
            selected_idx = st.radio(
                "候補を選択",
                range(len(highlight_candidates)),
                format_func=lambda i: labels[i],
                label_visibility="collapsed",
                key=f"{key_prefix}_highlight",
            )
            selected = highlight_candidates[selected_idx]
            interval, interval_error = interval_from_timestamps(
                selected.start,
                selected.end,
            )
            st.markdown(f"**理由:** {selected.reason}")

        else:
            manual_cols = st.columns(2)
            with manual_cols[0]:
                start_text = st.text_input(
                    "開始時刻",
                    value="0:00:00",
                    placeholder="0:00:00",
                    key=f"{key_prefix}_start",
                )
            with manual_cols[1]:
                end_text = st.text_input(
                    "終了時刻",
                    value="0:01:00",
                    placeholder="0:01:00",
                    key=f"{key_prefix}_end",
                )
            interval, interval_error = _resolve_manual_interval(start_text, end_text)

        if interval_error:
            st.error(interval_error)

        layout_label = st.radio(
            "レイアウト",
            LAYOUT_LABELS,
            index=0,
            key=f"{key_prefix}_layout",
        )
        if layout_label == LAYOUT_BLUR_LABEL:
            st.caption("元の映像を切らずに中央に配置します")
        else:
            st.caption("左右が切れます。話者が中央にいる配信向けです")

        burn_subtitles = st.checkbox(
            "字幕を焼き込む",
            value=True,
            key=f"{key_prefix}_subtitles",
        )
        if burn_subtitles and not is_japanese_font_available(settings.subtitle_font):
            st.warning(_FONT_PRE_WARNING)

        layout = layout_from_label(layout_label)
        busy = is_busy()

        duration_ok = False
        duration_message: str | None = None
        if interval is not None and not interval_error:
            duration_ok, duration_message = validate_interval_duration(
                interval.start,
                interval.end,
            )
            if not duration_ok and duration_message:
                st.warning(duration_message)

        create_disabled = (
            busy
            or interval is None
            or interval_error is not None
            or not duration_ok
        )

        if st.button(
            "ショートを作成",
            type="secondary",
            key=f"{key_prefix}_create",
            disabled=create_disabled,
        ):
            assert interval is not None
            _start_build_short(
                video_id=video_id,
                title=result.title,
                interval=interval,
                layout=layout,
                burn_subtitles=burn_subtitles,
                settings=settings,
            )

        if interval is not None and duration_ok:
            output_path = short_output_path(
                video_id,
                interval.start,
                interval.end,
                settings,
            )
            _render_existing_output(output_path, interval=interval)
