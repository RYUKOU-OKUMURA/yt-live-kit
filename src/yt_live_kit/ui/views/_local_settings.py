"""UI 固有の軽量設定をローカルファイルへ保存するヘルパー."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services._fsutil import advisory_lock, write_text_atomically
from yt_live_kit.services._paths import confined_path
from yt_live_kit.services.subtitle_burn import TELOP_PRESETS

_ARCHIVED_VIDEOS_FILENAME = "archived_videos.json"
_DESCRIPTION_APPLIED_VIDEOS_FILENAME = "description_applied_videos.json"
_CHANNEL_HANDLE_FILENAME = "channel_handle.txt"
_SHORTS_DEFAULTS_FILENAME = "shorts_defaults.json"
_LEGACY_SHORTS_DEFAULTS_FILENAME = "short_defaults.json"
_ID_SET_SNAPSHOTS = threading.local()


@dataclass(frozen=True)
class ShortsLineDefaults:
    """ショート生産ラインで毎回選ばず適用する既定値."""

    layout: str = "blur"
    preset: str = "default"
    hook_preset: str = "hook"


def _archived_videos_path(settings: Settings) -> Path:
    return confined_path(
        settings.data_dir,
        "_config",
        _ARCHIVED_VIDEOS_FILENAME,
        label="アーカイブ設定保存先",
    )


def _description_applied_videos_path(settings: Settings) -> Path:
    return confined_path(
        settings.data_dir,
        "_config",
        _DESCRIPTION_APPLIED_VIDEOS_FILENAME,
        label="概要欄反映設定保存先",
    )


def _channel_handle_path(settings: Settings) -> Path:
    return confined_path(
        settings.data_dir,
        "_config",
        _CHANNEL_HANDLE_FILENAME,
        label="チャンネル設定保存先",
    )


def _shorts_defaults_path(settings: Settings) -> Path:
    return confined_path(
        settings.data_dir,
        "_config",
        _SHORTS_DEFAULTS_FILENAME,
        label="ショート既定値保存先",
    )


def _legacy_shorts_defaults_path(settings: Settings) -> Path:
    return confined_path(
        settings.data_dir,
        "_config",
        _LEGACY_SHORTS_DEFAULTS_FILENAME,
        label="ショート既定値保存先",
    )


def _path_entry_exists(path: Path) -> bool:
    """通常ファイルだけでなく、壊れた symlink も存在するものとして扱う."""
    try:
        return os.path.lexists(path)
    except OSError:
        # canonical の存在確認に失敗した場合も legacy へフォールバックせず、
        # canonical の読み込み失敗として現行既定へ戻す。
        return True


def _read_shorts_line_defaults(path: Path) -> ShortsLineDefaults:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ShortsLineDefaults()
    if not isinstance(raw, dict):
        return ShortsLineDefaults()

    layout = raw.get("layout")
    preset = raw.get("preset")
    hook_preset = raw.get("hook_preset")
    if not isinstance(layout, str) or layout not in {"blur", "crop"}:
        return ShortsLineDefaults()
    if not isinstance(preset, str) or preset not in TELOP_PRESETS:
        return ShortsLineDefaults()
    if not isinstance(hook_preset, str) or hook_preset not in TELOP_PRESETS:
        return ShortsLineDefaults()
    return ShortsLineDefaults(layout, preset, hook_preset)


def load_shorts_line_defaults(
    settings: Settings | None = None,
) -> ShortsLineDefaults:
    """保存済み既定値を読み、欠落・破損時は現行既定へ安全に戻す.

    ``shorts_defaults.json`` が canonical であり、旧 ``short_defaults.json`` は
    canonical が存在しない場合だけ読み込む。canonical が壊れていても legacy
    の値で復旧しないことで、意図しない旧設定の復活を防ぐ。
    """
    settings = settings or get_settings()
    canonical_path = _shorts_defaults_path(settings)
    if _path_entry_exists(canonical_path):
        return _read_shorts_line_defaults(canonical_path)

    legacy_path = _legacy_shorts_defaults_path(settings)
    if not _path_entry_exists(legacy_path):
        return ShortsLineDefaults()
    return _read_shorts_line_defaults(legacy_path)


def save_shorts_line_defaults(
    defaults: ShortsLineDefaults, settings: Settings | None = None
) -> Path:
    """ショート生産ラインの既定値を検証して原子的に保存する."""
    if not isinstance(defaults, ShortsLineDefaults):
        raise ValueError("ショート既定値の形式が不正です。")
    if not isinstance(defaults.layout, str) or defaults.layout not in {
        "blur",
        "crop",
    }:
        raise ValueError("レイアウトは blur または crop を指定してください。")
    if not isinstance(defaults.preset, str) or defaults.preset not in TELOP_PRESETS:
        raise ValueError("通常テロッププリセットが不正です。")
    if (
        not isinstance(defaults.hook_preset, str)
        or defaults.hook_preset not in TELOP_PRESETS
    ):
        raise ValueError("Hook テロッププリセットが不正です。")

    settings = settings or get_settings()
    path = _shorts_defaults_path(settings)
    payload = json.dumps(
        {
            "layout": defaults.layout,
            "preset": defaults.preset,
            "hook_preset": defaults.hook_preset,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    with advisory_lock(_setting_lock_path(path)):
        write_text_atomically(path, payload, mode=0o600)
    return path


def _snapshot_map() -> dict[Path, set[str]]:
    snapshots = getattr(_ID_SET_SNAPSHOTS, "values", None)
    if snapshots is None:
        snapshots = {}
        _ID_SET_SNAPSHOTS.values = snapshots
    return snapshots


def _snapshot_key(path: Path) -> Path:
    return path.resolve(strict=False)


def _remember_id_set(path: Path, ids: set[str]) -> None:
    _snapshot_map()[_snapshot_key(path)] = set(ids)


def _consume_id_set_snapshot(path: Path) -> set[str] | None:
    return _snapshot_map().pop(_snapshot_key(path), None)


def _setting_lock_path(path: Path) -> Path:
    return confined_path(
        path.parent.parent,
        "_config",
        f".{path.name}.lock",
        label="ローカル設定ロック保存先",
    )


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
        snapshot = _snapshot_map().get(_snapshot_key(path))
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
