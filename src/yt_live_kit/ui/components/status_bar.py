"""常駐ステータスバー（fragment）."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st

from yt_live_kit.config import get_settings
from yt_live_kit.services.batch import read_batch_summary
from yt_live_kit.services.jobs import (
    JobState,
    get_active_job,
    read_current_job,
    read_job,
    read_job_error_log,
)
from yt_live_kit.services.pipeline import load_result_from_disk
from yt_live_kit.services.shorts_queue import (
    ShortsQueueError,
    ShortsQueueResult,
    load_shorts_queue_result,
)
from yt_live_kit.services.upload_queue import UploadQueueError, load_operation
from yt_live_kit.ui.state import (
    clear_active_job_id,
    clear_cut_result,
    format_job_error_summary_for_display,
    get_active_job_id,
    get_last_job_id,
    is_job_handled,
    mark_job_handled,
    record_job_error,
    set_active_job_id,
    set_batch_summary,
    set_cut_result,
    set_last_job_id,
    set_result,
)

_KIND_LABELS: dict[str, str] = {
    "single": "単本処理",
    "batch": "一括処理",
    "regenerate": "再生成",
    "highlights": "ハイライト動画",
    "shorts": "ショート動画",
    "shorts_queue": "ショート量産",
    "short_cut": "ショート区間提案",
    "upload": "予約投稿",
    "upload_publication": "公開状態確認",
    "cut_clip": "切り出し",
}

_PIPELINE_KINDS = frozenset({"single", "regenerate", "highlights", "shorts"})
_UPLOAD_KINDS = frozenset({"upload", "upload_publication"})
_KNOWN_NON_PIPELINE_KINDS = frozenset(
    {"batch", "shorts_queue", "short_cut", "upload", "upload_publication", "cut_clip"}
)

_ERROR_SUMMARY_MAX_CHARS = 180
_ERROR_DETAIL_MAX_CHARS = 64 * 1024

_RETRY_HINTS: dict[str, str] = {
    "single": "対象動画を開いて再実行してください。",
    "batch": "対象を確認して一括処理を再実行してください。",
    "regenerate": "対象動画を開いて再生成してください。",
    "highlights": "対象動画を開いて再生成してください。",
    "shorts": "対象動画を開いてショート生成を再実行してください。",
    "shorts_queue": "生成工程を確認し、必要な対象だけ再試行してください。",
    "short_cut": "対象動画の素材を確認して再実行してください。",
    "upload": "投稿履歴を確認し、必要なら明示的に再試行してください。",
    "upload_publication": "投稿状態を確認し、必要なら明示的に再試行してください。",
    "cut_clip": "対象動画を開いて切り出しを再実行してください。",
}

# セッションをまたいで完了ジョブを復元する時間窓（分）。
# read_current_job() 経由（＝ブラウザを開き直した直後などの新規セッション）で
# 数日前に終わったジョブまで復元してしまわないよう制限する。
_RESTORE_WINDOW_MINUTES = 10


def _safe_summary_text(value: object) -> str:
    """status bar 用に半角山カッコと改行を安全な1行へ整える。"""
    return format_job_error_summary_for_display(value)


def _format_error_summary(value: object, *, fallback: str) -> str:
    """上部通知へ渡す要約を空文字・長文込みで安全な1行へ整える。"""
    summary = _safe_summary_text(value).strip()
    if not summary:
        summary = fallback
    if len(summary) > _ERROR_SUMMARY_MAX_CHARS:
        summary = summary[: _ERROR_SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary


def _bounded_error_detail(value: object | None) -> str | None:
    if value is None:
        return None
    detail = str(value)
    if len(detail) > _ERROR_DETAIL_MAX_CHARS:
        return detail[:_ERROR_DETAIL_MAX_CHARS] + "\n（詳細はサイズ上限により省略）"
    return detail


def _job_finished_at(job: JobState) -> datetime:
    occurred_at = job.finished_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        return occurred_at.replace(tzinfo=timezone.utc)
    return occurred_at.astimezone(timezone.utc)


def _job_video_id(job: JobState) -> str | None:
    if job.video_id:
        return job.video_id
    if job.kind in _PIPELINE_KINDS or job.kind == "shorts_queue":
        return job.result_ref or None
    return None


def _retry_hint(job: JobState) -> str:
    return _RETRY_HINTS.get(
        job.kind,
        "ログを確認し、アプリの対応状況を確認してから再試行してください。",
    )


def _record_job_error(
    job: JobState,
    *,
    cause: str,
    detail_source: str,
    detail: object | None = None,
    video_id: str | None = None,
) -> None:
    """ジョブ失敗をsummary/detailへ分離してU8-1 stateへ記録する。"""
    target_video_id = _job_video_id(job) if video_id is None else video_id
    target = (
        f"対象動画 {target_video_id}。"
        if target_video_id
        else ""
    )
    summary = _format_error_summary(
        f"{kind_label(job.kind)}に失敗しました。{target}"
        f"{cause}{_retry_hint(job)}",
        fallback="処理に失敗しました。詳細画面で確認して再試行してください。",
    )
    detail_text = _bounded_error_detail(detail)
    if detail_text:
        detail_text = f"詳細元: {detail_source}\n{detail_text}"
    else:
        detail_text = f"詳細元: {detail_source}"
    record_job_error(
        target_video_id,
        job.job_id,
        job.kind,
        summary,
        detail_text if target_video_id else None,
        _job_finished_at(job),
    )


def _job_failure_detail(job: JobState, settings) -> tuple[str, str]:
    """failed/interrupted の詳細元をjob log優先で選ぶ。"""
    log = read_job_error_log(job.job_id, settings)
    if log:
        return "ジョブログ", log
    if job.error:
        return "job.error", job.error
    return "ジョブ状態", "保存されたエラー詳細はありません。"


def _shorts_queue_failure_details(result: ShortsQueueResult) -> str:
    """typed manifest の失敗 item を入力順の日本語表示へ整える。"""
    details = [
        f"{item.target_id}: {item.error or '生成に失敗しました。'}"
        for item in result.items
        if item.status == "failed"
    ]
    return " / ".join(details)


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def elapsed_seconds(job: JobState, *, now: datetime | None = None) -> int:
    reference = now or datetime.now(timezone.utc)
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((reference - started).total_seconds()))


def format_status_message(job: JobState, *, now: datetime | None = None) -> str:
    label = kind_label(job.kind)
    elapsed = elapsed_seconds(job, now=now)
    message = _format_error_summary(job.message, fallback="処理中")
    parts = [f"{label} — {message}（経過 {elapsed} 秒）"]
    if job.total > 0:
        parts.append(f"{job.current}/{job.total}")
    return " ".join(parts)


def should_show_running_bar(job: JobState | None) -> bool:
    return job is not None and job.status == "running"


def is_recently_finished(
    job: JobState,
    *,
    now: datetime | None = None,
    window_minutes: int = _RESTORE_WINDOW_MINUTES,
) -> bool:
    """ジョブの完了時刻が復元時間窓内（=最近完了した）かどうかを判定する.

    finished_at が未設定（None）の場合は安全側に倒して False を返す。
    finished_at が tz-naive な場合は elapsed_seconds() と同様に UTC とみなす。
    """
    finished = job.finished_at
    if finished is None:
        return False
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return reference - finished <= timedelta(minutes=window_minutes)


def find_restorable_job(settings) -> JobState | None:
    """セッションまたは current.json 上の未処理完了ジョブを探す.

    list_jobs() による全走査は行わない。session_state の last_job_id を
    優先し、無ければ current.json が指す最新ジョブを見る。読み込みは
    最大でも current.json + 該当ジョブ json の 2 件で済む。

    last_job_id 経由（同一セッション内でユーザーが実際に開始・監視していた
    ジョブ）には時間制限をかけない。一方 read_current_job() 経由（セッション
    をまたいだ復元）は、数日前に終わったジョブまで起動直後に復元してしまう
    ことを避けるため、is_recently_finished() で直近完了のものに限定する。
    """
    last_id = get_last_job_id()
    if last_id:
        job = read_job(last_id, settings)
        if (
            job is not None
            and job.status in ("done", "failed", "interrupted")
            and not is_job_handled(job.job_id)
        ):
            return job
        return None

    job = read_current_job(settings)
    if (
        job is not None
        and job.status in ("done", "failed", "interrupted")
        and not is_job_handled(job.job_id)
        and is_recently_finished(job)
    ):
        return job
    return None


def _load_batch_summary_for_job(job: JobState, settings) -> None:
    if job.kind != "batch":
        return
    summary = read_batch_summary(settings, job.job_id)
    if summary is not None:
        set_batch_summary(summary)


def _render_running_job(job: JobState) -> None:
    st.markdown("**実行中の処理**")
    message = format_status_message(job)
    if job.total > 0:
        ratio = min(max(job.current / job.total, 0.0), 1.0)
        st.progress(ratio, text=message)
    else:
        st.progress(0, text=message)


def _handle_finished_job(job: JobState) -> None:
    if is_job_handled(job.job_id):
        return

    settings = get_settings()

    if job.status == "done":
        if job.kind in _PIPELINE_KINDS and job.result_ref:
            try:
                result = load_result_from_disk(job.result_ref, settings)
            except Exception as exc:
                _record_job_error(
                    job,
                    cause="成果物を読み込めませんでした。",
                    detail_source="成果物ローダー",
                    detail=exc,
                )
            else:
                if result is not None:
                    set_result(result)
                    clear_cut_result()
                else:
                    _record_job_error(
                        job,
                        cause="成果物を読み込めませんでした。",
                        detail_source="成果物ローダー",
                        detail=(
                            f"video_id={job.result_ref}\n"
                            "meta.json または transcript/full.txt が見つかりません。"
                        ),
                    )

        elif job.kind in _PIPELINE_KINDS:
            _record_job_error(
                job,
                cause="完了ジョブに成果物の参照先がありません。",
                detail_source="ジョブ状態",
                detail="result_ref が空です。",
            )
        elif job.kind in _UPLOAD_KINDS:
            if not job.result_ref:
                _record_job_error(
                    job,
                    cause="投稿処理の operation ID がありません。",
                    detail_source="ジョブ状態",
                    detail="result_ref が空です。",
                )
            else:
                try:
                    load_operation(job.result_ref, settings)
                except UploadQueueError as exc:
                    _record_job_error(
                        job,
                        cause="投稿状態を読み込めませんでした。",
                        detail_source="投稿キュー",
                        detail=exc,
                    )
        elif job.kind == "shorts_queue":
            video_id = _job_video_id(job)
            if not video_id:
                _record_job_error(
                    job,
                    cause="ショート量産の対象動画を特定できません。",
                    detail_source="ジョブ状態",
                    detail="video_id と result_ref が空です。",
                    video_id=None,
                )
            else:
                try:
                    queue_result = load_shorts_queue_result(
                        video_id,
                        job.job_id,
                        settings,
                    )
                except ShortsQueueError as exc:
                    _record_job_error(
                        job,
                        cause="ショート生成結果を読み込めませんでした。",
                        detail_source="ショート量産manifest",
                        detail=exc,
                        video_id=video_id,
                    )
                else:
                    if queue_result is None:
                        _record_job_error(
                            job,
                            cause="ショート量産の生成結果が見つかりません。",
                            detail_source="ショート量産manifest",
                            detail=(
                                f"video_id={video_id}\n"
                                f"job_id={job.job_id} のmanifestがありません。"
                            ),
                            video_id=video_id,
                        )
                    elif (
                        queue_result.failure_count > 0
                        and queue_result.success_count == 0
                    ):
                        failure_details = _shorts_queue_failure_details(queue_result)
                        _record_job_error(
                            job,
                            cause=(
                                "ショート生成が全件失敗しました。"
                                f"失敗 {queue_result.failure_count} 件。"
                            ),
                            detail_source="ショート量産manifestのfailed item",
                            detail=failure_details,
                            video_id=video_id,
                        )
                    elif queue_result.failure_count > 0:
                        failure_details = _shorts_queue_failure_details(queue_result)
                        _record_job_error(
                            job,
                            cause=(
                                "ショート生成が一部失敗しました。"
                                f"成功 {queue_result.success_count} 件、"
                                f"失敗 {queue_result.failure_count} 件。"
                            ),
                            detail_source="ショート量産manifestのfailed item",
                            detail=failure_details,
                            video_id=video_id,
                        )
        elif job.kind == "short_cut":
            # 保存済み cutplan は画面 rerun 後に short_cut 部品が再読込する。
            pass
        elif job.kind == "cut_clip":
            if not job.result_ref:
                _record_job_error(
                    job,
                    cause="切り出し成果物の参照先がありません。",
                    detail_source="ジョブ状態",
                    detail="result_ref が空です。",
                )
            else:
                from yt_live_kit.services.clips import cut_result_from_ref

                cut_result_error_recorded = False
                try:
                    cut_result = cut_result_from_ref(job.result_ref)
                except Exception as exc:
                    _record_job_error(
                        job,
                        cause="切り出し結果を読み込めません。",
                        detail_source="切り出し成果物参照",
                        detail=exc,
                    )
                    cut_result_error_recorded = True
                    cut_result = None
                if cut_result is not None:
                    set_cut_result(cut_result)
                elif not cut_result_error_recorded:
                    _record_job_error(
                        job,
                        cause="切り出し結果を読み込めません。",
                        detail_source="切り出し成果物参照",
                        detail="result_ref の形式または内容が正しくありません。",
                    )
        elif job.kind not in _KNOWN_NON_PIPELINE_KINDS:
            _record_job_error(
                job,
                cause=f"未対応のジョブ種別「{job.kind}」です。",
                detail_source="ジョブ状態",
                detail=f"kind={job.kind}",
            )

        _load_batch_summary_for_job(job, settings)
        mark_job_handled(job.job_id)
        clear_active_job_id()
        st.rerun(scope="app")
        return

    if job.status in ("failed", "interrupted"):
        _load_batch_summary_for_job(job, settings)
        detail_source, detail = _job_failure_detail(job, settings)
        cause = (
            "処理が中断されました。"
            if job.status == "interrupted"
            else "保存された処理エラーを確認してください。"
        )
        _record_job_error(
            job,
            cause=cause,
            detail_source=detail_source,
            detail=detail,
        )
        mark_job_handled(job.job_id)
        clear_active_job_id()
        st.rerun(scope="app")


@st.fragment(run_every="1s")
def render_status_bar(*, detail_page=None) -> None:
    """常駐処理だけを表示する。通知のconsumeはapp上部に限定する。"""
    del detail_page
    settings = get_settings()
    active = get_active_job(settings)

    if active is not None:
        set_active_job_id(active.job_id)
        set_last_job_id(active.job_id)
        _render_running_job(active)
        return

    tracked_id = get_active_job_id()
    if tracked_id:
        job = read_job(tracked_id, settings)
        if job is None:
            clear_active_job_id()
            return

        if job.status == "running":
            set_last_job_id(job.job_id)
            _render_running_job(job)
            return

        _handle_finished_job(job)
        return

    restorable = find_restorable_job(settings)
    if restorable is not None:
        set_last_job_id(restorable.job_id)
        _handle_finished_job(restorable)
