"""取り込みページ — チャンネル新着と URL 例外ルート."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.channel import ChannelListDocument, ChannelVideo
from yt_live_kit.services.batch import parse_urls, run_batch_job_target
from yt_live_kit.services.channel import (
    ChannelError,
    list_archives,
    load_cache,
    mark_processed,
    normalize_channel_url,
    save_cache,
)
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.pipeline import run_single_job_target
from yt_live_kit.ui.state import (
    clear_batch_summary,
    clear_result,
    get_batch_summary,
    set_active_job_id,
)
from yt_live_kit.ui.views._local_settings import get_default_channel_handle

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_NO_TARGET_MESSAGE = (
    "「チャプターを作る」「切り抜き候補を出す」"
    "のどちらかを選んでください。"
)
_UNEXPECTED_ERROR = "予期しないエラーが発生しました。しばらくしてから再度お試しください。"
_LIMIT_OPTIONS = [20, 50, 100]
_CHECKBOX_KEY_PREFIX = "intake_channel_cb_"


def format_duration(seconds: int | None) -> str:
    """秒数を H:MM:SS 形式に変換する."""
    if seconds is None:
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_upload_date(upload_date: str | None) -> str:
    """YYYYMMDD を YYYY-MM-DD に変換する."""
    if not upload_date or len(upload_date) != 8:
        return "—"
    return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"


def format_fetched_at(fetched_at: datetime) -> str:
    """取得日時を YYYY-MM-DD HH:MM 形式に変換する."""
    if fetched_at.tzinfo is not None:
        fetched_at = fetched_at.astimezone()
    return fetched_at.strftime("%Y-%m-%d %H:%M")


def filter_unprocessed(
    items: list[tuple[ChannelVideo, bool]],
) -> list[tuple[ChannelVideo, bool]]:
    """未処理の動画だけを返す."""
    return [(video, processed) for video, processed in items if not processed]


def checkbox_key(video_id: str) -> str:
    """動画 ID から選択チェック用 key を作る."""
    return f"{_CHECKBOX_KEY_PREFIX}{video_id}"


def collect_selected_urls(
    items: list[tuple[ChannelVideo, bool]],
    session_state: dict[str, object],
) -> list[str]:
    """表示中の未処理動画から選択済み URL を返す."""
    return [
        video.url
        for video, _processed in items
        if session_state.get(checkbox_key(video.video_id), False)
    ]


def batch_summary_severity(success: int, failed: int) -> str:
    """一括処理サマリーの表示レベルを返す."""
    if failed > 0 and success > 0:
        return "warning"
    if failed > 0:
        return "error"
    if success == 0:
        return "info"
    return "success"


def single_run_disabled(
    *, busy: bool, url: str, do_chapters: bool, do_clips: bool
) -> bool:
    """単本処理を開始できない状態かを返す."""
    return busy or not url.strip() or (not do_chapters and not do_clips)


def batch_run_disabled(
    *, busy: bool, urls: list[str], do_chapters: bool, do_clips: bool
) -> bool:
    """一括処理を開始できない状態かを返す."""
    return busy or not urls or (not do_chapters and not do_clips)


def _prepare_job_state() -> None:
    clear_result()
    clear_batch_summary()


def _start_batch_job(
    *,
    urls: list[str],
    do_chapters: bool,
    do_clips: bool,
    skip_existing: bool,
    settings: Settings,
) -> str:
    _prepare_job_state()
    job_id = start_job(
        "batch",
        run_batch_job_target,
        total=len(urls),
        urls=urls,
        skip_existing=skip_existing,
        do_chapters=do_chapters,
        do_clips=do_clips,
        settings=settings,
    )
    set_active_job_id(job_id)
    return job_id


def start_channel_batch_job(
    *,
    urls: list[str],
    do_chapters: bool,
    do_clips: bool,
    settings: Settings,
) -> str:
    """新着選択ルートから一括ジョブを開始する."""
    return _start_batch_job(
        urls=urls,
        do_chapters=do_chapters,
        do_clips=do_clips,
        skip_existing=True,
        settings=settings,
    )


def start_url_batch_job(
    *,
    urls: list[str],
    do_chapters: bool,
    do_clips: bool,
    skip_existing: bool,
    settings: Settings,
) -> str:
    """URL 複数行ルートから一括ジョブを開始する."""
    return _start_batch_job(
        urls=urls,
        do_chapters=do_chapters,
        do_clips=do_clips,
        skip_existing=skip_existing,
        settings=settings,
    )


def start_single_url_job(
    *,
    url: str,
    do_chapters: bool,
    do_clips: bool,
    settings: Settings,
) -> str:
    """URL 単本ルートからジョブを開始する."""
    _prepare_job_state()
    job_id = start_job(
        "single",
        run_single_job_target,
        url=url.strip(),
        do_chapters=do_chapters,
        do_clips=do_clips,
        settings=settings,
    )
    set_active_job_id(job_id)
    return job_id


def _render_batch_summary() -> None:
    summary = get_batch_summary()
    if not summary:
        return
    text = str(summary.get("summary", ""))
    failed = int(summary.get("failed", 0) or 0)
    success = int(summary.get("success", 0) or 0)
    severity = batch_summary_severity(success, failed)
    getattr(st, severity)(text)
    lines = summary.get("lines", [])
    if isinstance(lines, list) and lines:
        with st.expander("URL 別ログ", expanded=False):
            st.code("\n".join(str(line) for line in lines), language=None)


def _fetch_archives(
    channel_url: str, *, limit: int, settings: Settings
) -> ChannelListDocument:
    """ユーザーの明示操作時だけ一覧を取得する."""
    with st.spinner("配信アーカイブ一覧を取得しています…"):
        document = list_archives(channel_url, limit=limit, settings=settings)
        save_cache(document, settings=settings)
    return document


def _set_all(items: list[tuple[ChannelVideo, bool]], *, selected: bool) -> None:
    for video, _processed in items:
        st.session_state[checkbox_key(video.video_id)] = selected


def _render_flags(prefix: str) -> tuple[bool, bool]:
    do_chapters = st.checkbox(
        "チャプターを作る", value=True, key=f"{prefix}_chapters"
    )
    do_clips = st.checkbox(
        "切り抜き候補を出す", value=True, key=f"{prefix}_clips"
    )
    if not do_chapters and not do_clips:
        st.warning(_NO_TARGET_MESSAGE)
    return do_chapters, do_clips


def _render_channel_intake(*, busy: bool, settings: Settings) -> None:
    st.subheader("未処理の新着")
    saved_handle = get_default_channel_handle(settings)
    if saved_handle is None:
        st.info(
            "チャンネルの既定ハンドルが未設定です。"
            "設定ページで保存すると、ここに新着が表示されます。"
        )
        return

    try:
        channel_url, cache_handle = normalize_channel_url(saved_handle)
    except ChannelError as exc:
        st.error(str(exc))
        return

    document = load_cache(cache_handle, settings=settings)
    limit = st.selectbox(
        "再取得件数",
        _LIMIT_OPTIONS,
        index=1,
        format_func=lambda value: f"{value} 件",
        help="明示的に再取得するときの上限です。",
    )
    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.caption(f"登録チャンネル: {saved_handle}")
        refresh = st.button(
            "新着を再取得",
            icon=":material/refresh:",
            disabled=busy,
        )

    if refresh:
        try:
            document = _fetch_archives(channel_url, limit=limit, settings=settings)
        except ChannelError as exc:
            st.error(str(exc))
        except Exception:
            st.error(_UNEXPECTED_ERROR)

    if document is None:
        st.info(
            "保存済みの新着一覧がありません。"
            "「新着を再取得」を押して取得してください。"
        )
        return

    st.caption(f"前回取得: {format_fetched_at(document.fetched_at)}")
    items = filter_unprocessed(mark_processed(document, settings=settings))
    if not items:
        st.success("未処理の新着はありません。")
        return

    with st.container(horizontal=True):
        if st.button("すべて選択", key="intake_select_all"):
            _set_all(items, selected=True)
            st.rerun()
        if st.button("選択を解除", key="intake_clear_all"):
            _set_all(items, selected=False)
            st.rerun()

    for video, _processed in items:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.checkbox(
                "選択",
                key=checkbox_key(video.video_id),
                label_visibility="collapsed",
            )
            st.markdown(f"**{video.title}**")
            st.caption(format_duration(video.duration))
            st.caption(format_upload_date(video.upload_date))

    selected_urls = collect_selected_urls(items, st.session_state)
    with st.container(border=True):
        st.markdown("**選択した新着を処理**")
        do_chapters, do_clips = _render_flags("intake_channel")
        disabled = batch_run_disabled(
            busy=busy,
            urls=selected_urls,
            do_chapters=do_chapters,
            do_clips=do_clips,
        )
        if st.button(
            f"選択した {len(selected_urls)} 本を処理開始",
            type="primary",
            disabled=disabled,
            key="intake_channel_start",
        ):
            try:
                start_channel_batch_job(
                    urls=selected_urls,
                    do_chapters=do_chapters,
                    do_clips=do_clips,
                    settings=settings,
                )
                st.rerun()
            except JobBusyError:
                st.error(_BUSY_MESSAGE)


def _render_single_url(*, busy: bool, settings: Settings) -> None:
    with st.container(border=True):
        url = st.text_input(
            "YouTube URL",
            placeholder="https://www.youtube.com/watch?v=...",
            help="公開アーカイブのみ対応しています。",
            key="intake_single_url",
        )
        do_chapters, do_clips = _render_flags("intake_single")
        disabled = single_run_disabled(
            busy=busy,
            url=url,
            do_chapters=do_chapters,
            do_clips=do_clips,
        )
        if st.button(
            "1 本を処理開始",
            type="primary",
            disabled=disabled,
            key="intake_single_start",
        ):
            try:
                start_single_url_job(
                    url=url,
                    do_chapters=do_chapters,
                    do_clips=do_clips,
                    settings=settings,
                )
                st.rerun()
            except JobBusyError:
                st.error(_BUSY_MESSAGE)


def _render_batch_urls(*, busy: bool, settings: Settings) -> None:
    with st.container(border=True):
        raw_urls = st.text_area(
            "YouTube URL（1 行 1 本）",
            placeholder=(
                "https://www.youtube.com/watch?v=...\n"
                "https://www.youtube.com/watch?v=..."
            ),
            height=150,
            help="空行と # で始まる行は無視します。",
            key="intake_batch_urls",
        )
        skip_existing = st.checkbox(
            "処理済みをスキップ",
            value=True,
            key="intake_batch_skip",
        )
        do_chapters, do_clips = _render_flags("intake_url_batch")
        urls = parse_urls(raw_urls)
        disabled = batch_run_disabled(
            busy=busy,
            urls=urls,
            do_chapters=do_chapters,
            do_clips=do_clips,
        )
        if st.button(
            f"{len(urls)} 本を一括処理開始",
            type="primary",
            disabled=disabled,
            key="intake_batch_start",
        ):
            try:
                start_url_batch_job(
                    urls=urls,
                    do_chapters=do_chapters,
                    do_clips=do_clips,
                    skip_existing=skip_existing,
                    settings=settings,
                )
                st.rerun()
            except JobBusyError:
                st.error(_BUSY_MESSAGE)


def render_intake_page() -> None:
    """チャンネル新着を主導線にした取り込みページを表示する."""
    _render_batch_summary()
    st.markdown(
        "登録チャンネルの未処理の新着を選び、"
        "チャプターと切り抜き候補の生成を開始できます。"
    )
    settings = get_settings()
    busy = is_busy()
    if busy:
        st.info(_BUSY_MESSAGE)

    _render_channel_intake(busy=busy, settings=settings)

    with st.expander("URL を直接入力する（例外ルート）", expanded=False):
        mode = st.segmented_control(
            "入力方法",
            ["単本", "複数行一括"],
            default="単本",
            key="intake_url_mode",
        )
        if mode == "複数行一括":
            _render_batch_urls(busy=busy, settings=settings)
        else:
            _render_single_url(busy=busy, settings=settings)
