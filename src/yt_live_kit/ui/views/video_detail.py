"""選択した動画の成果物と次の作業をまとめて表示するページ."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.chapter_validator import validate_chapters
from yt_live_kit.services.history import ProcessedVideo, list_processed_videos
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import (
    PipelineResult,
    load_result_from_disk,
    regenerate_job_target,
)
from yt_live_kit.services.storage import StorageError, format_bytes, purge_source
from yt_live_kit.services.youtube_api import (
    fetch_video_snippet,
    is_configured,
    merge_chapters_into_description,
    update_video_description,
)
from yt_live_kit.ui.components.clipboard import render_copy_button
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

StepStatus = Literal["complete", "next", "pending"]

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_STEP_LABELS = ("字幕", "チャプター", "候補", "ショート", "概要欄")
_STATUS_LABELS: dict[StepStatus, str] = {
    "complete": "✓ 完了",
    "next": "● 次にやる",
    "pending": "○ 未着手",
}
_DESCRIPTION_UPDATED_IDS_KEY = "detail_description_updated_ids"
_DESCRIPTION_SUCCESS_KEY = "detail_description_success"


@dataclass(frozen=True)
class ProgressStep:
    """動画詳細ステッパーの 1 段階."""

    label: str
    status: StepStatus


def calculate_progress_steps(
    video: ProcessedVideo,
    result: PipelineResult | None,
    *,
    shorts_count: int,
    description_applied_ids: Collection[str],
    has_highlights: bool = False,
) -> tuple[ProgressStep, ...]:
    """保存済み成果物から 5 段階の状態を計算する純粋関数."""
    completion = (
        video.has_transcript,
        video.has_chapters or bool(result and result.chapters_text.strip()),
        video.has_clips
        or bool(result and result.clips_candidates)
        or has_highlights,
        shorts_count > 0,
        video.video_id in description_applied_ids,
    )
    first_incomplete = next(
        (index for index, complete in enumerate(completion) if not complete),
        None,
    )

    steps: list[ProgressStep] = []
    for index, (label, complete) in enumerate(zip(_STEP_LABELS, completion)):
        if complete:
            status: StepStatus = "complete"
        elif index == first_incomplete:
            status = "next"
        else:
            status = "pending"
        steps.append(ProgressStep(label=label, status=status))
    return tuple(steps)


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


def _render_stepper(steps: tuple[ProgressStep, ...]) -> str | None:
    """ステッパーを表示し、押された次ステップ名を返す."""
    st.subheader("次にやること")
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        for step in steps:
            st.badge(f"{step.label}  {_STATUS_LABELS[step.status]}")

    next_step = next((step for step in steps if step.status == "next"), None)
    if next_step is None:
        st.success("すべてのステップが完了しています。")
        return None
    if st.button(
        f"次にやる: {next_step.label}",
        key="detail_next_step",
        type="primary",
        width="stretch",
    ):
        return next_step.label
    return None


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


def _handle_next_action(
    next_action: str | None,
    video: ProcessedVideo,
    settings: Settings,
    *,
    run_page: StreamlitPage | None,
    chapters_text: str = "",
) -> bool:
    """次ステップ CTA を実行し、ショート UI を開くか返す."""
    if next_action == "字幕":
        if run_page is None:
            st.info("実行ページで動画 URL を入力し、字幕を取得してください。")
        else:
            st.switch_page(run_page)
    elif next_action == "チャプター":
        _confirm_regenerate_dialog(video, "chapters", settings)
    elif next_action == "候補":
        _confirm_regenerate_dialog(video, "clips", settings)
    elif next_action == "概要欄":
        _start_description_preview(video, chapters_text, settings)
    return next_action == "ショート"


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
        with st.expander(f"{target_label}を再実行", expanded=False):
            st.caption("既存成果物を退避してから再生成します。")
            render_button()
    else:
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


def render_video_detail_page(
    *,
    run_page: StreamlitPage | None = None,
) -> None:
    """選択中の動画について、保存済み成果物から詳細ページを再構築する."""
    st.header("動画詳細")
    video_id = get_selected_video_id()
    if video_id is None:
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

    st.markdown(f"**{video.title}**")
    st.caption(video.video_id)
    description_success = st.session_state.pop(_DESCRIPTION_SUCCESS_KEY, None)
    if description_success:
        st.success(description_success)
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)

    has_highlights = (
        settings.data_dir / video.video_id / "highlights" / "segments.json"
    ).is_file()
    result = load_result_from_disk(video_id, settings)
    steps = calculate_progress_steps(
        video,
        result,
        shorts_count=count_shorts(video.video_id, settings),
        description_applied_ids=load_description_applied_ids(settings),
        has_highlights=has_highlights,
    )
    next_action = _render_stepper(steps)
    shorts_expanded = _handle_next_action(
        next_action,
        video,
        settings,
        run_page=run_page,
        chapters_text=result.chapters_text if result is not None else "",
    )

    st.divider()

    if result is None:
        st.warning(
            "保存済みの字幕成果物を読み込めませんでした。"
            "実行ページから字幕の取得と整形をやり直してください。"
        )
        return

    _render_transcript(result)
    _render_chapters(video, result, busy=busy, settings=settings)
    _render_clips(
        video,
        result,
        busy=busy,
        settings=settings,
        has_highlights=has_highlights,
    )

    st.subheader("4. ハイライト候補")
    if result.highlights_error:
        st.warning(
            "ハイライト候補の生成に失敗しましたが、他の成果物は利用できます。\n\n"
            f"{result.highlights_error}"
        )
    render_highlights_section(result)

    st.subheader("5. ショート作成")
    render_shorts_section(result, expanded=shorts_expanded)

    st.subheader("6. 概要欄反映")
    _render_description_control(
        video,
        result.chapters_text,
        settings,
        busy=busy,
    )

    render_upload_section(video.video_id, settings)

    with st.expander("元動画と中間ファイルの管理", expanded=False):
        st.caption(
            "削除してもチャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
        )
        if st.button(
            "元動画を削除",
            key=f"detail_purge_{video.video_id}",
            disabled=busy,
        ):
            _confirm_source_purge_dialog(video, settings)
