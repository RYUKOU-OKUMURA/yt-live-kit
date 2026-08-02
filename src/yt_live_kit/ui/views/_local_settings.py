"""UI 固有の軽量設定をローカルファイルへ保存するヘルパー."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services._fsutil import advisory_lock, write_text_atomically
from yt_live_kit.services.subtitle_burn import TELOP_PRESETS

_ARCHIVED_VIDEOS_FILENAME = "archived_videos.json"
_DESCRIPTION_APPLIED_VIDEOS_FILENAME = "description_applied_videos.json"
_CHANNEL_HANDLE_FILENAME = "channel_handle.txt"
_SHORTS_DEFAULTS_FILENAME = "shorts_defaults.json"
_ID_SET_SNAPSHOTS = threading.local()


@dataclass(frozen=True)
class ShortsLineDefaults:
    """ショート生産ラインで毎回選ばず適用する既定値."""

    layout: str = "blur"
    preset: str = "default"
    hook_preset: str = "hook"


def _archived_videos_path(settings: Settings) -> Path:
    return settings.data_dir / "_config" / _ARCHIVED_VIDEOS_FILENAME


def _description_applied_videos_path(settings: Settings) -> Path:
    return settings.data_dir / "_config" / _DESCRIPTION_APPLIED_VIDEOS_FILENAME


def _channel_handle_path(settings: Settings) -> Path:
    return settings.data_dir / "_config" / _CHANNEL_HANDLE_FILENAME


def _shorts_defaults_path(settings: Settings) -> Path:
    return settings.data_dir / "_config" / _SHORTS_DEFAULTS_FILENAME


def load_shorts_line_defaults(
    settings: Settings | None = None,
) -> ShortsLineDefaults:
    """保存済み既定値を読み、欠落・破損時は現行既定へ安全に戻す."""
    settings = settings or get_settings()
    try:
        raw = json.loads(_shorts_defaults_path(settings).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ShortsLineDefaults()
    if not isinstance(raw, dict):
        return ShortsLineDefaults()
    layout = raw.get("layout")
    preset = raw.get("preset")
    hook_preset = raw.get("hook_preset")
    if layout not in {"blur", "crop"}:
        return ShortsLineDefaults()
    if not isinstance(preset, str) or preset not in TELOP_PRESETS:
        return ShortsLineDefaults()
    if not isinstance(hook_preset, str) or hook_preset not in TELOP_PRESETS:
        return ShortsLineDefaults()
    return ShortsLineDefaults(layout, preset, hook_preset)


def _snapshot_map() -> dict[Path, set[str]]:
    snapshots = getattr(_ID_SET_SNAPSHOTS, "values", None)
    if snapshots is None:
        snapshots = {}
        _ID_SET_SNAPSHOTS.values = snapshots
    return snapshots


def _remember_id_set(path: Path, ids: set[str]) -> None:
    _snapshot_map()[path] = set(ids)


def _consume_id_set_snapshot(path: Path) -> set[str] | None:
    return _snapshot_map().pop(path, None)


def _setting_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _read_id_set(path: Path) -> set[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()

    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return set()
    return {video_id for video_id in raw if video_id}


def _load_id_set(path: Path) -> set[str]:
    ids = _read_id_set(path)
    _remember_id_set(path, ids)
    return ids


def _write_id_set_locked(ids: set[str], path: Path) -> Path:
    payload = json.dumps(sorted(ids), ensure_ascii=False, indent=2) + "\n"
    write_text_atomically(path, payload, mode=0o600)
    return path


def _save_id_set(ids: set[str], path: Path) -> Path:
    desired = set(ids)
    with advisory_lock(_setting_lock_path(path)):
        # Save 呼び出し前に読まれた snapshot があれば、その差分だけを
        # lock 内で読み直した最新値へ適用する。これで別プロセスの追加を失わない。
        current = _read_id_set(path)
        snapshot = _snapshot_map().get(path)
        if snapshot is None:
            merged = desired
        else:
            additions = desired - snapshot
            removals = snapshot - desired
            merged = (current - removals) | additions
        saved_path = _write_id_set_locked(merged, path)
        _consume_id_set_snapshot(path)
        return saved_path


def load_archived_ids(settings: Settings | None = None) -> set[str]:
    """保存済みのアーカイブ動画 ID を返す.

    ファイルが無い場合や内容が不正な場合は、
    安全側に倒して空集合を返す。
    """
    settings = settings or get_settings()
    return _load_id_set(_archived_videos_path(settings))


def save_archived_ids(
    ids: set[str], settings: Settings | None = None
) -> Path:
    """アーカイブ動画 ID を JSON 配列として原子的に保存する."""
    settings = settings or get_settings()
    return _save_id_set(ids, _archived_videos_path(settings))


def load_description_applied_ids(settings: Settings | None = None) -> set[str]:
    """概要欄への反映が成功した動画 ID を返す."""
    settings = settings or get_settings()
    return _load_id_set(_description_applied_videos_path(settings))


def save_description_applied_ids(
    ids: set[str], settings: Settings | None = None
) -> Path:
    """概要欄反映済み動画 ID を JSON 配列として原子的に保存する."""
    settings = settings or get_settings()
    return _save_id_set(ids, _description_applied_videos_path(settings))


def mark_description_applied(
    video_id: str, settings: Settings | None = None
) -> Path:
    """動画 ID を概要欄反映済みとして記録する（U5 成功時用）."""
    settings = settings or get_settings()
    path = _description_applied_videos_path(settings)
    with advisory_lock(_setting_lock_path(path)):
        ids = _read_id_set(path)
        ids.add(video_id)
        saved_path = _write_id_set_locked(ids, path)
        _consume_id_set_snapshot(path)
        return saved_path


def get_default_channel_handle(settings: Settings | None = None) -> str | None:
    """保存済みのチャンネル既定ハンドルを返す."""
    settings = settings or get_settings()
    try:
        handle = _channel_handle_path(settings).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return handle or None


def save_default_channel_handle(
    handle: str, settings: Settings | None = None
) -> Path:
    """チャンネル既定ハンドルを 1 行テキストで原子的に保存する."""
    settings = settings or get_settings()
    normalized = handle.strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise ValueError("チャンネルハンドルを 1 行で入力してください。")

    path = _channel_handle_path(settings)
    with advisory_lock(_setting_lock_path(path)):
        write_text_atomically(path, normalized + "\n", mode=0o600)
    return path
