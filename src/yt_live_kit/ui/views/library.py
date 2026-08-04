"""処理済み動画のライブラリページ."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Literal

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.history import ProcessedVideo, list_processed_videos
from yt_live_kit.ui.queries import count_generated_shorts
from yt_live_kit.ui.state import SESSION_SHOW_ARCHIVED, set_selected_video_id
from yt_live_kit.ui.views._local_settings import (
    load_archived_ids,
    save_archived_ids,
)

if TYPE_CHECKING:
    from streamlit.navigation.page import StreamlitPage

LibraryStatus = Literal[
    "すべて", "チャプター未生成", "候補未生成", "ショートあり"
]
LIBRARY_STATUS_OPTIONS: tuple[LibraryStatus, ...] = (
    "すべて",
    "チャプター未生成",
    "候補未生成",
    "ショートあり",
)


# Existing imports from this view remain valid while the implementation lives in
# the shared read-only query layer.
count_shorts = count_generated_shorts


def title_matches(title: str, query: str) -> bool:
    """タイトルが検索語に部分一致するか返す.

    大文字と小文字は区別しない。
    """
    normalized_query = query.strip().casefold()
    return not normalized_query or normalized_query in title.casefold()


def filter_library_videos(
    videos: list[ProcessedVideo],
    *,
    query: str = "",
    archived_ids: Collection[str] = (),
    show_archived: bool = False,
    status: LibraryStatus = "すべて",
    shorts_counts: dict[str, int] | None = None,
) -> list[ProcessedVideo]:
    """検索・アーカイブ表示・状態条件で動画を絞り込む."""
    counts = shorts_counts or {}
    filtered: list[ProcessedVideo] = []
    for video in videos:
        is_archived = video.video_id in archived_ids
        if is_archived and not show_archived:
            continue
        if not title_matches(video.title, query):
            continue
        if status == "チャプター未生成" and video.has_chapters:
            continue
        if status == "候補未生成" and video.has_clips:
            continue
        if status == "ショートあり" and counts.get(video.video_id, 0) <= 0:
            continue
        filtered.append(video)
    return filtered


def _change_archive_state(
    video_id: str,
    *,
    archived: bool,
    archived_ids: set[str],
    settings: Settings,
) -> None:
    updated = set(archived_ids)
    if archived:
        updated.add(video_id)
    else:
        updated.discard(video_id)
    try:
        save_archived_ids(updated, settings)
    except OSError:
        st.error(
            "アーカイブ状態を保存できませんでした。"
            "data/_config の書き込み権限を確認してください。"
        )
        return
    st.rerun()


def _render_video_row(
    video: ProcessedVideo,
    *,
    shorts_count: int,
    archived: bool,
    archived_ids: set[str],
    settings: Settings,
    detail_page: StreamlitPage,
) -> None:
    with st.container(border=True, gap="xsmall"):
        title_column, badges_column, actions_column = st.columns(
            [5, 4, 3], vertical_alignment="center"
        )
        with title_column:
            st.markdown(f"**{video.title}**")
            st.caption(video.video_id)
        with badges_column:
            with st.container(horizontal=True, gap="xsmall"):
                st.badge(
                    (
                        "チャプター"
                        if video.has_chapters
                        else "チャプター未生成"
                    ),
                    icon=":material/check_circle:"
                    if video.has_chapters
                    else ":material/pending:",
                    color="green" if video.has_chapters else "gray",
                )
                st.badge(
                    "候補" if video.has_clips else "候補未生成",
                    icon=":material/check_circle:"
                    if video.has_clips
                    else ":material/pending:",
                    color="green" if video.has_clips else "gray",
                )
                st.badge(
                    f"ショート {shorts_count} 本",
                    icon=":material/vertical_shades:",
                    color="violet" if shorts_count else "gray",
                )
        with actions_column:
            with st.container(
                horizontal=True,
                horizontal_alignment="right",
                gap="xsmall",
            ):
                if st.button(
                    "開く",
                    key=f"library_open_{video.video_id}",
                    type="primary",
                    icon=":material/arrow_forward:",
                ):
                    set_selected_video_id(video.video_id)
                    st.switch_page(detail_page)
                archive_label = "表示に戻す" if archived else "アーカイブ"
                archive_icon = (
                    ":material/unarchive:"
                    if archived
                    else ":material/archive:"
                )
                if st.button(
                    archive_label,
                    key=f"library_archive_{video.video_id}",
                    icon=archive_icon,
                ):
                    _change_archive_state(
                        video.video_id,
                        archived=not archived,
                        archived_ids=archived_ids,
                        settings=settings,
                    )


def render_library_page(*, detail_page: StreamlitPage) -> None:
    """ライブラリページを描画する."""
    settings = get_settings()
    st.header("ライブラリ")
    st.caption("処理済み動画から、次に仕上げる一本を選びます。")

    search_column, status_column, archive_column = st.columns(
        [5, 3, 3], vertical_alignment="bottom"
    )
    with search_column:
        query = st.text_input(
            "タイトル検索",
            placeholder="タイトルの一部を入力",
            icon=":material/search:",
        )
    with status_column:
        status = st.selectbox("状態", LIBRARY_STATUS_OPTIONS)
    with archive_column:
        show_archived = st.toggle(
            "アーカイブ済みを表示",
            key=SESSION_SHOW_ARCHIVED,
        )

    videos = list_processed_videos(settings)
    archived_ids = load_archived_ids(settings)
    shorts_counts = {
        video.video_id: count_shorts(video.video_id, settings) for video in videos
    }
    filtered = filter_library_videos(
        videos,
        query=query,
        archived_ids=archived_ids,
        show_archived=show_archived,
        status=status,
        shorts_counts=shorts_counts,
    )

    st.caption(f"{len(filtered)} 本を表示")
    if not filtered:
        st.info(
            "条件に一致する動画はありません。"
            "検索や表示条件を変更してください。"
        )
        return

    for video in filtered:
        _render_video_row(
            video,
            shorts_count=shorts_counts[video.video_id],
            archived=video.video_id in archived_ids,
            archived_ids=archived_ids,
            settings=settings,
            detail_page=detail_page,
        )
