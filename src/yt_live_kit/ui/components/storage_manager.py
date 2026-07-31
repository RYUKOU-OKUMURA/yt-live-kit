"""設定画面で利用するストレージ管理 UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.services.history import ProcessedVideo, list_processed_videos
from yt_live_kit.services.jobs import is_busy
from yt_live_kit.services.storage import (
    StorageError,
    StorageSummary,
    VideoStorage,
    format_bytes,
    purge_source,
    summarize,
)

_SUMMARY_KEY = "storage_manager_summary"
_SNAPSHOT_KEY = "storage_manager_bulk_snapshot"
_FLASH_KEY = "storage_manager_flash"
_RETAINED_ARTIFACTS = (
    "チャプター・全文・切り抜き候補・切り出し済み動画は残ります。"
)


@dataclass(frozen=True)
class PurgeTarget:
    """確認時点で固定した削除対象."""

    video_id: str
    title: str
    deletable_bytes: int


@dataclass(frozen=True)
class BulkPurgeSnapshot:
    """一括削除プレビュー時点の不変スナップショット."""

    days: int
    targets: tuple[PurgeTarget, ...]

    @property
    def count(self) -> int:
        return len(self.targets)

    @property
    def total_bytes(self) -> int:
        return sum(target.deletable_bytes for target in self.targets)


def deletable_bytes(video: VideoStorage) -> int:
    """元動画キャッシュと中間ファイルの削除可能容量を返す."""
    return video.source_bytes + video.intermediate_bytes


def format_video_storage_row(video: VideoStorage) -> str:
    """動画ごとの容量内訳を表示用に整形する."""
    return (
        f"元動画 {format_bytes(video.source_bytes)} / "
        f"中間ファイル {format_bytes(video.intermediate_bytes)} / "
        f"成果物 {format_bytes(video.output_bytes)} / "
        f"その他 {format_bytes(video.other_bytes)} / "
        f"合計 {format_bytes(video.total_bytes)}"
    )


def filter_storage_videos(
    videos: list[VideoStorage], query: str
) -> list[VideoStorage]:
    """タイトルまたは動画 ID で絞り込む."""
    needle = query.strip().casefold()
    if not needle:
        return videos
    return [
        video
        for video in videos
        if needle in (video.title or "").casefold()
        or needle in video.video_id.casefold()
    ]


def build_bulk_purge_snapshot(
    processed: list[ProcessedVideo],
    summary: StorageSummary,
    days: int,
    *,
    now: datetime | None = None,
) -> BulkPurgeSnapshot:
    """指定日数より古い削除対象を ID と容量で固定する."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=days)
    storage_by_id = {video.video_id: video for video in summary.videos}
    targets: list[PurgeTarget] = []

    for video in processed:
        if video.fetched_at is None:
            continue
        fetched_at = video.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        else:
            fetched_at = fetched_at.astimezone(timezone.utc)
        storage = storage_by_id.get(video.video_id)
        if fetched_at >= cutoff or storage is None:
            continue
        target_bytes = deletable_bytes(storage)
        if target_bytes <= 0:
            continue
        targets.append(
            PurgeTarget(
                video_id=video.video_id,
                title=video.title,
                deletable_bytes=target_bytes,
            )
        )

    return BulkPurgeSnapshot(days=days, targets=tuple(targets))


def _refresh_summary(settings: Settings) -> None:
    st.session_state[_SUMMARY_KEY] = summarize(settings)


def _refresh_summary_best_effort(settings: Settings) -> bool:
    """削除後の再集計を試み、失敗時は古い集計結果を破棄する."""
    try:
        _refresh_summary(settings)
    except (OSError, StorageError):
        st.session_state.pop(_SUMMARY_KEY, None)
        return False
    return True


def _set_flash(*messages: tuple[str, str]) -> None:
    st.session_state[_FLASH_KEY] = messages


def _show_flash() -> None:
    messages = st.session_state.pop(_FLASH_KEY, ())
    if isinstance(messages, str):
        messages = (("success", messages),)
    for level, message in messages:
        if level == "error":
            st.error(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.success(message)


def _describe_target(target: PurgeTarget) -> None:
    st.markdown(f"**動画:** {target.title}")
    st.caption(f"動画 ID: {target.video_id}")
    st.write("対象: 1 件")
    st.write(f"削除対象容量: {format_bytes(target.deletable_bytes)}")
    st.info(_RETAINED_ARTIFACTS)


@st.dialog("元動画キャッシュの削除確認")
def _confirm_single_purge_dialog(target: PurgeTarget, settings: Settings) -> None:
    """個別削除の対象と保持成果物を確認してから削除する."""
    _describe_target(target)
    busy = is_busy()
    confirmed = st.button(
        "この 1 件を削除",
        type="primary",
        disabled=busy,
        key=f"storage_manager_confirm_{target.video_id}",
    )
    if not confirmed or busy:
        return

    try:
        deleted = purge_source(target.video_id, settings)
    except (OSError, StorageError) as exc:
        st.error(f"削除に失敗しました。{exc}")
        return

    success_message = (
        f"{target.title} の元動画キャッシュを {format_bytes(deleted)} 削除しました。"
    )
    if _refresh_summary_best_effort(settings):
        _set_flash(("success", success_message))
    else:
        _set_flash(
            ("success", success_message),
            (
                "warning",
                "削除は完了したが容量再集計に失敗しました。"
                "設定画面で容量を再集計してください。",
            ),
        )
    st.rerun()


@st.dialog("古い元動画キャッシュの一括削除確認")
def _confirm_bulk_purge_dialog(
    snapshot: BulkPurgeSnapshot, settings: Settings
) -> None:
    """プレビュー時に固定した動画 ID だけを一括削除する."""
    st.warning(
        f"対象: {snapshot.count} 件\n\n"
        f"削除対象容量: {format_bytes(snapshot.total_bytes)}"
    )
    st.info(_RETAINED_ARTIFACTS)
    busy = is_busy()
    confirmed = st.button(
        "表示中の対象だけを削除",
        type="primary",
        disabled=busy,
        key="storage_manager_bulk_confirm",
    )
    if not confirmed or busy:
        return

    successes: list[tuple[PurgeTarget, int]] = []
    failures: list[tuple[PurgeTarget, Exception]] = []
    for target in snapshot.targets:
        try:
            deleted = purge_source(target.video_id, settings)
        except (OSError, StorageError) as exc:
            failures.append((target, exc))
        else:
            successes.append((target, deleted))

    if failures:
        remaining = BulkPurgeSnapshot(
            days=snapshot.days,
            targets=tuple(target for target, _exc in failures),
        )
        st.session_state[_SNAPSHOT_KEY] = remaining
    else:
        st.session_state.pop(_SNAPSHOT_KEY, None)

    deleted_total = sum(deleted for _target, deleted in successes)
    messages: list[tuple[str, str]] = []
    if successes:
        messages.append(
            (
                "success",
                f"{len(successes)} 件から {format_bytes(deleted_total)} 削除しました。",
            )
        )
    if failures:
        failure_details = "、".join(
            f"{target.video_id}（{exc}）" for target, exc in failures
        )
        level = "warning" if successes else "error"
        messages.append(
            (
                level,
                f"{len(failures)} 件の削除に失敗しました: {failure_details}。"
                "失敗した動画だけを削除対象として保持しました。",
            )
        )
    if not _refresh_summary_best_effort(settings):
        messages.append(
            (
                "warning",
                "削除処理後の容量再集計に失敗しました。"
                "上記の削除結果は確定済みです。設定画面で容量を再集計してください。",
            )
        )
    _set_flash(*messages)
    st.rerun()


def _render_video_rows(summary: StorageSummary, settings: Settings, busy: bool) -> None:
    query = st.text_input(
        "動画を検索",
        placeholder="タイトルまたは動画 ID",
        key="storage_manager_query",
    )
    videos = filter_storage_videos(summary.videos, query or "")
    st.caption(f"{len(videos)} / {len(summary.videos)} 件を表示")

    if not videos:
        st.info("条件に一致する動画はありません。")
        return

    for video in videos:
        title = video.title or "タイトル不明"
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(f"動画 ID: {video.video_id}")
            st.write(format_video_storage_row(video))
            target_bytes = deletable_bytes(video)
            clicked = st.button(
                f"元動画・中間ファイルを削除（{format_bytes(target_bytes)}）",
                key=f"storage_manager_delete_{video.video_id}",
                disabled=busy or target_bytes <= 0,
            )
            if clicked and not busy and target_bytes > 0:
                _confirm_single_purge_dialog(
                    PurgeTarget(video.video_id, title, target_bytes), settings
                )


def _render_bulk_purge(
    summary: StorageSummary, settings: Settings, busy: bool
) -> None:
    st.markdown("#### 古い元動画キャッシュの一括削除")
    days = int(
        st.number_input(
            "何日以上前の動画を対象にするか",
            min_value=1,
            value=30,
            step=1,
            key="storage_manager_days",
        )
    )
    preview_clicked = st.button(
        "削除対象を確認",
        disabled=busy,
        key="storage_manager_preview",
    )
    if preview_clicked and not busy:
        try:
            processed = list_processed_videos(settings)
        except OSError:
            st.error("削除対象を確認できませんでした。データ保存先を確認してください。")
        else:
            st.session_state[_SNAPSHOT_KEY] = build_bulk_purge_snapshot(
                processed, summary, days
            )

    snapshot = st.session_state.get(_SNAPSHOT_KEY)
    if not isinstance(snapshot, BulkPurgeSnapshot):
        return
    st.write(
        f"確認済み対象: {snapshot.count} 件 / "
        f"{format_bytes(snapshot.total_bytes)}"
    )
    st.caption(f"プレビュー条件: {snapshot.days} 日以上前")
    st.info(_RETAINED_ARTIFACTS)
    if snapshot.count == 0:
        return
    confirm_clicked = st.button(
        "一括削除の確認を開く",
        disabled=busy,
        key="storage_manager_open_bulk_confirm",
    )
    if confirm_clicked and not busy:
        _confirm_bulk_purge_dialog(snapshot, settings)


def render_storage_manager(settings: Settings) -> None:
    """ストレージ集計・検索・個別削除・一括削除を描画する."""
    st.subheader("ストレージ管理")
    st.caption(
        "元動画キャッシュと中間ファイルだけを削除します。"
        "生成済みの成果物は保持されます。"
    )
    _show_flash()
    busy = is_busy()
    if busy:
        st.warning("処理中はストレージを変更できません。")

    summary = st.session_state.get(_SUMMARY_KEY)
    if not isinstance(summary, StorageSummary):
        st.info("容量を集計すると、全動画の内訳と削除候補を確認できます。")
        if st.button(
            "容量を集計",
            disabled=busy,
            key="storage_manager_summarize",
        ):
            try:
                _refresh_summary(settings)
            except (OSError, StorageError):
                st.error(
                    "ストレージ容量を集計できませんでした。"
                    "データ保存先を確認してください。"
                )
            else:
                st.rerun()
        return

    st.metric("データ使用量", format_bytes(summary.total_bytes))
    if st.button(
        "容量を再集計",
        disabled=busy,
        key="storage_manager_resummarize",
    ):
        try:
            _refresh_summary(settings)
        except (OSError, StorageError):
            st.error(
                "ストレージ容量を再集計できませんでした。"
                "データ保存先を確認してください。"
            )
        else:
            st.rerun()

    _render_video_rows(summary, settings, busy)
    st.divider()
    _render_bulk_purge(summary, settings, busy)
