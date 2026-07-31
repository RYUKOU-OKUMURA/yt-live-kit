"""UI 固有の軽量設定をローカルファイルへ保存するヘルパー."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from yt_live_kit.config import Settings, get_settings

_ARCHIVED_VIDEOS_FILENAME = "archived_videos.json"


def _archived_videos_path(settings: Settings) -> Path:
    return settings.data_dir / "_config" / _ARCHIVED_VIDEOS_FILENAME


def load_archived_ids(settings: Settings | None = None) -> set[str]:
    """保存済みのアーカイブ動画 ID を返す.

    ファイルが無い場合や内容が不正な場合は、
    安全側に倒して空集合を返す。
    """
    settings = settings or get_settings()
    path = _archived_videos_path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()

    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return set()
    return {video_id for video_id in raw if video_id}


def save_archived_ids(
    ids: set[str], settings: Settings | None = None
) -> Path:
    """アーカイブ動画 ID を JSON 配列として原子的に保存する."""
    settings = settings or get_settings()
    path = _archived_videos_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(sorted(ids), temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return path
