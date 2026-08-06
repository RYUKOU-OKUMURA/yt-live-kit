"""ストレージ容量の集計と元動画キャッシュの削除."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta

# 削除してよい相対パス（video_id ディレクトリ基準）
DELETABLE_REL_PATHS: tuple[Path, ...] = (
    Path("clips/source"),
    Path("highlights/segments"),
    Path("shorts/segments"),
)

# 成果物として絶対に削除しないパス（参照用定数）
PROTECTED_REL_PATHS: tuple[Path, ...] = (
    Path("meta.json"),
    Path("chapters"),
    Path("transcript"),
    Path("subtitles"),
    Path("clips/output"),
    Path("highlights/output"),
    Path("shorts/output"),
)

_SOURCE_REL = Path("clips/source")
_OUTPUT_RELS = (
    Path("clips/output"),
    Path("highlights/output"),
    Path("shorts/output"),
)
# 中間ファイル（再エンコード済みの一時セグメント）が置かれる相対パス群
_INTERMEDIATE_RELS = (
    Path("highlights/segments"),
    Path("shorts/segments"),
)


class StorageError(Exception):
    """ストレージ操作に関するエラー."""


def dir_size(path: Path) -> int:
    """パス配下のファイルサイズを再帰的に合計する。存在しない場合は 0."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def format_bytes(n: int) -> str:
    """バイト数を人間向けの文字列に変換する（例: 3.2 GB）."""
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    return f"{n / 1024**3:.1f} GB"


@dataclass(frozen=True)
class VideoStorage:
    """動画ごとのストレージ内訳."""

    video_id: str
    title: str | None
    source_bytes: int
    output_bytes: int
    intermediate_bytes: int
    other_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class StorageSummary:
    """data/ 全体のストレージサマリー."""

    total_bytes: int
    videos: list[VideoStorage]


def _data_dir_resolved(settings: Settings) -> Path:
    return settings.data_dir.resolve()


def _is_under(path: Path, root: Path) -> bool:
    """path が root 配下（または同一）かどうか."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_under_data_dir(path: Path, data_dir: Path) -> Path:
    """path を resolve し、data_dir 配下であることを検証する."""
    resolved = path.resolve()
    if not _is_under(resolved, data_dir):
        raise StorageError(
            f"削除対象がデータディレクトリ外です: {path}"
        )
    return resolved


def _video_dir(video_id: str, settings: Settings) -> Path:
    """video_id に対応するディレクトリを返す（安全性検証付き）."""
    if not video_id or video_id.startswith("_") or "/" in video_id or "\\" in video_id:
        raise StorageError(f"無効な動画 ID です: {video_id}")
    data_dir = _data_dir_resolved(settings)
    video_dir = (settings.data_dir / video_id).resolve()
    if not _is_under(video_dir, data_dir):
        raise StorageError(
            f"指定された動画 ID はデータディレクトリ外を指しています: {video_id}"
        )
    return video_dir


def _read_title(video_dir: Path) -> str | None:
    meta_path = video_dir / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        return meta.title
    except Exception:
        return None


def _video_storage(video_dir: Path, video_id: str) -> VideoStorage:
    source_bytes = dir_size(video_dir / _SOURCE_REL)
    output_bytes = sum(dir_size(video_dir / rel) for rel in _OUTPUT_RELS)
    intermediate_bytes = sum(dir_size(video_dir / rel) for rel in _INTERMEDIATE_RELS)
    total_bytes = dir_size(video_dir)
    categorized = source_bytes + output_bytes + intermediate_bytes
    other_bytes = max(0, total_bytes - categorized)
    return VideoStorage(
        video_id=video_id,
        title=_read_title(video_dir),
        source_bytes=source_bytes,
        output_bytes=output_bytes,
        intermediate_bytes=intermediate_bytes,
        other_bytes=other_bytes,
        total_bytes=total_bytes,
    )


def summarize(settings: Settings | None = None) -> StorageSummary:
    """data/ の容量を集計し、動画ごとの内訳を返す."""
    settings = settings or get_settings()
    data_dir = settings.data_dir
    if not data_dir.is_dir():
        return StorageSummary(total_bytes=0, videos=[])

    videos: list[VideoStorage] = []
    for entry in data_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        videos.append(_video_storage(entry, entry.name))

    videos.sort(key=lambda v: v.total_bytes, reverse=True)
    return StorageSummary(total_bytes=dir_size(data_dir), videos=videos)


def _purge_deletable_dirs(video_dir: Path, data_dir: Path) -> int:
    """削除可能なディレクトリのみを削除し、削除したバイト数を返す."""
    deleted = 0
    for rel in DELETABLE_REL_PATHS:
        target = video_dir / rel
        if not target.exists():
            continue
        _assert_under_data_dir(target, data_dir)
        deleted += dir_size(target)
        shutil.rmtree(target)
    return deleted


def purge_source(video_id: str, settings: Settings | None = None) -> int:
    """元動画キャッシュと中間ファイルのみを削除する（成果物は残す）."""
    settings = settings or get_settings()
    data_dir = _data_dir_resolved(settings)
    video_dir = _video_dir(video_id, settings)
    if not video_dir.is_dir():
        return 0
    return _purge_deletable_dirs(video_dir, data_dir)


def _normalize_fetched_at(fetched_at: datetime) -> datetime:
    if fetched_at.tzinfo is None:
        return fetched_at.replace(tzinfo=timezone.utc)
    return fetched_at.astimezone(timezone.utc)


def source_fetched_at(video_id: str, settings: Settings | None = None) -> datetime | None:
    """meta.json を再読込し、取得日時を返す."""
    settings = settings or get_settings()
    meta_path = _video_dir(video_id, settings) / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _normalize_fetched_at(meta.fetched_at)


def is_source_older_than(
    video_id: str,
    days: int,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """meta.json を再読込し、指定日数より古い取得日時か判定する."""
    fetched_at = source_fetched_at(video_id, settings)
    if fetched_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=days)
    return fetched_at < cutoff


def purge_sources_older_than(
    days: int,
    settings: Settings | None = None,
) -> list[tuple[str, int]]:
    """指定日数より古い元動画キャッシュを一括削除する."""
    settings = settings or get_settings()
    data_dir = _data_dir_resolved(settings)
    if not data_dir.is_dir():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results: list[tuple[str, int]] = []

    for entry in data_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        meta_path = entry / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        fetched_at = _normalize_fetched_at(meta.fetched_at)
        if fetched_at >= cutoff:
            continue

        deleted = purge_source(entry.name, settings)
        if deleted > 0:
            results.append((entry.name, deleted))

    return results
