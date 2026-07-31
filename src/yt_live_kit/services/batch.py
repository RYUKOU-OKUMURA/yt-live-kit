"""複数 URL の一括処理・ステータス記録."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.history import is_video_processed
from yt_live_kit.services.jobs import get_active_job, update_job
from yt_live_kit.services.pipeline import PipelineError, PipelineResult, run
from yt_live_kit.services.ytdlp import YtdlpError, extract_video_id

BatchStatus = Literal["success", "failed", "skipped"]

_NO_TARGET_MESSAGE = (
    "「チャプターを作る」「切り抜き候補を出す」"
    "のどちらかを選んでください。"
)

ProgressCallback = Callable[[int, int, str, str], None]
"""(current_index, total, url, message)"""


@dataclass(frozen=True)
class BatchItemResult:
    """1 URL のバッチ処理結果."""

    url: str
    video_id: str | None
    status: BatchStatus
    error: str | None = None
    result: PipelineResult | None = None


@dataclass
class BatchStatusEntry:
    """status.json に記録する 1 件."""

    url: str
    video_id: str | None
    status: BatchStatus
    error: str | None
    timestamp: str


def parse_urls(text: str) -> list[str]:
    """テキストから URL 一覧を抽出する（空行・# コメント行を除外）."""
    urls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


def load_urls_file(path: Path) -> list[str]:
    """URL 一覧ファイル（1 行 1 URL）を読み込む."""
    if not path.is_file():
        raise FileNotFoundError(f"URL 一覧ファイルが見つかりません: {path}")
    return parse_urls(path.read_text(encoding="utf-8"))


def batch_status_path(settings: Settings) -> Path:
    return settings.data_dir / "_batch" / "status.json"


def load_batch_status(settings: Settings | None = None) -> list[BatchStatusEntry]:
    """保存済みバッチステータスを読み込む."""
    settings = settings or get_settings()
    path = batch_status_path(settings)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return []
    result: list[BatchStatusEntry] = []
    for item in entries:
        if isinstance(item, dict):
            result.append(
                BatchStatusEntry(
                    url=item.get("url", ""),
                    video_id=item.get("video_id"),
                    status=item.get("status", "failed"),
                    error=item.get("error"),
                    timestamp=item.get("timestamp", ""),
                )
            )
    return result


def append_batch_status(
    entries: list[BatchStatusEntry],
    settings: Settings | None = None,
) -> Path:
    """バッチステータスを status.json に追記保存する."""
    settings = settings or get_settings()
    path = batch_status_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = load_batch_status(settings)
    combined = existing + entries
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entries": [
            {
                "url": e.url,
                "video_id": e.video_id,
                "status": e.status,
                "error": e.error,
                "timestamp": e.timestamp,
            }
            for e in combined
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_batch(
    urls: list[str],
    settings: Settings | None = None,
    *,
    skip_existing: bool = False,
    do_chapters: bool = True,
    do_clips: bool = True,
    sleep_sec: float | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[BatchItemResult]:
    """複数 URL を順次処理する。個別失敗はスキップして継続."""
    if not do_chapters and not do_clips:
        raise ValueError(_NO_TARGET_MESSAGE)

    settings = settings or get_settings()
    settings.ensure_data_dir()
    sleep = sleep_sec if sleep_sec is not None else settings.sleep

    total = len(urls)
    results: list[BatchItemResult] = []
    status_entries: list[BatchStatusEntry] = []

    for i, url in enumerate(urls):
        if on_progress is not None:
            on_progress(i + 1, total, url, "開始")

        video_id: str | None = None
        try:
            video_id = extract_video_id(url)
        except YtdlpError:
            pass

        if skip_existing and video_id and is_video_processed(video_id, settings):
            item = BatchItemResult(
                url=url,
                video_id=video_id,
                status="skipped",
                error=None,
            )
            results.append(item)
            status_entries.append(
                BatchStatusEntry(
                    url=url,
                    video_id=video_id,
                    status="skipped",
                    error=None,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )
            if on_progress is not None:
                on_progress(i + 1, total, url, "スキップ（処理済み）")
            continue

        try:
            if on_progress is not None:
                on_progress(i + 1, total, url, "処理中")

            def item_progress(stage: str, message: str) -> None:
                if on_progress is not None:
                    on_progress(i + 1, total, url, message)

            result = run(
                url,
                settings,
                on_progress=item_progress,
                do_chapters=do_chapters,
                do_clips=do_clips,
            )
            item = BatchItemResult(
                url=url,
                video_id=result.video_id,
                status="success",
                result=result,
            )
            results.append(item)
            status_entries.append(
                BatchStatusEntry(
                    url=url,
                    video_id=result.video_id,
                    status="success",
                    error=None,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )
        except (PipelineError, YtdlpError) as exc:
            item = BatchItemResult(
                url=url,
                video_id=video_id,
                status="failed",
                error=str(exc),
            )
            results.append(item)
            status_entries.append(
                BatchStatusEntry(
                    url=url,
                    video_id=video_id,
                    status="failed",
                    error=str(exc),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )
            if on_progress is not None:
                on_progress(i + 1, total, url, f"失敗: {exc}")

        if i < total - 1 and sleep > 0:
            time.sleep(sleep)

    if status_entries:
        append_batch_status(status_entries, settings)

    return results


def batch_summary_path(settings: Settings, job_id: str) -> Path:
    return settings.data_dir / "_jobs" / f"{job_id}.batch_summary.json"


def batch_log_path(settings: Settings, job_id: str) -> Path:
    return settings.data_dir / "_jobs" / f"{job_id}.batch_log.txt"


def _summarize_batch_results(
    results: list[BatchItemResult],
) -> tuple[int, int, int]:
    success = sum(1 for item in results if item.status == "success")
    skipped = sum(1 for item in results if item.status == "skipped")
    failed = sum(1 for item in results if item.status == "failed")
    return success, skipped, failed


def _format_batch_summary(success: int, skipped: int, failed: int) -> str:
    return f"一括処理完了: 成功 {success} / スキップ {skipped} / 失敗 {failed}"


def _format_batch_line(item: BatchItemResult) -> str:
    if item.status == "success":
        return f"✅ {item.url}"
    if item.status == "skipped":
        return f"⏭️ {item.url}"
    detail = f" — {item.error}" if item.error else ""
    return f"❌ {item.url}{detail}"


def write_batch_summary(
    settings: Settings,
    job_id: str,
    *,
    summary: str,
    lines: list[str],
    success: int,
    skipped: int,
    failed: int,
) -> Path:
    """バッチ完了サマリーを原子的に永続化する."""
    settings.ensure_data_dir()
    path = batch_summary_path(settings, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "lines": lines,
        "success": success,
        "skipped": skipped,
        "failed": failed,
    }
    tmp_path = path.parent / f".{job_id}.batch_summary.tmp"
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return path


def write_batch_log(settings: Settings, job_id: str, lines: list[str]) -> Path:
    """URL 別ログをテキストで永続化する."""
    settings.ensure_data_dir()
    path = batch_log_path(settings, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{job_id}.batch_log.tmp"
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def read_batch_summary(
    settings: Settings | None,
    job_id: str,
) -> dict[str, object] | None:
    """永続化済みバッチサマリーを読み込む."""
    settings = settings or get_settings()
    path = batch_summary_path(settings, job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def run_batch_job_target(
    *,
    report,
    settings: Settings | None = None,
    urls: list[str],
    skip_existing: bool = False,
    do_chapters: bool = True,
    do_clips: bool = True,
    job_id: str | None = None,
) -> None:
    """start_job 用: 一括処理を実行し、最後の成功結果をジョブに記録する.

    成功が 0 件（全件スキップ・全件失敗など）でも例外にはしない。
    「表示する結果がない」ことと「処理が失敗した」ことは別であり、
    サマリーは write_batch_summary() で永続化され、status_bar 側が
    それを読んで結果を表示するため、ここで異常終了させる必要はない。
    """

    if not do_chapters and not do_clips:
        raise ValueError(_NO_TARGET_MESSAGE)

    def on_progress(current: int, total: int, url: str, message: str) -> None:
        report(
            current=current,
            total=total,
            message=f"{current}/{total} — {url} — {message}",
        )

    results = run_batch(
        urls,
        settings,
        skip_existing=skip_existing,
        do_chapters=do_chapters,
        do_clips=do_clips,
        on_progress=on_progress,
    )

    success, skipped, failed = _summarize_batch_results(results)
    summary = _format_batch_summary(success, skipped, failed)
    lines = [_format_batch_line(item) for item in results]

    if job_id is None:
        active = get_active_job(settings)
        job_id = active.job_id if active is not None else None

    if job_id is not None:
        write_batch_summary(
            settings or get_settings(),
            job_id,
            summary=summary,
            lines=lines,
            success=success,
            skipped=skipped,
            failed=failed,
        )
        write_batch_log(settings or get_settings(), job_id, lines)

    last_result: PipelineResult | None = None
    for item in reversed(results):
        if item.status == "success" and item.result is not None:
            last_result = item.result
            break

    if job_id is not None and last_result is not None:
        update_job(
            job_id,
            settings=settings,
            video_id=last_result.video_id,
            title=last_result.title,
            message=summary,
        )
    elif job_id is not None:
        update_job(
            job_id,
            settings=settings,
            message=summary,
        )
