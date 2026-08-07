"""実行環境 warning の UI cache 境界テスト."""

from __future__ import annotations

import os

from yt_live_kit.config import Settings
from yt_live_kit.services.ytdlp import (
    MISSING_YTDLP_BINARY_IDENTITY,
    YtdlpBinaryIdentity,
    get_ytdlp_binary_identity,
)
from yt_live_kit.ui import runtime_checks


def test_ytdlp_warning_cache_reuses_read_only_warning(monkeypatch, tmp_path):
    binary = tmp_path / "yt-dlp"
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    settings = Settings(ytdlp_path=str(binary), ytdlp_timeout=17)
    calls: list[Settings] = []

    runtime_checks.clear_ytdlp_version_warning_cache()
    monkeypatch.setattr(
        runtime_checks,
        "check_ytdlp_version_warning",
        lambda current: calls.append(current) or "警告",
    )

    assert runtime_checks.check_ytdlp_version_warning_cached(settings) == "警告"
    assert runtime_checks.check_ytdlp_version_warning_cached(settings) == "警告"

    assert len(calls) == 1
    assert calls[0].ytdlp_path == str(binary)
    assert calls[0].ytdlp_timeout == 17


def test_ytdlp_warning_cache_key_changes_when_binary_identity_changes(
    monkeypatch,
    tmp_path,
):
    binary = tmp_path / "yt-dlp"
    binary.write_bytes(b"first")
    binary.chmod(0o755)
    settings = Settings(ytdlp_path=str(binary), ytdlp_timeout=17)
    calls: list[Settings] = []

    runtime_checks.clear_ytdlp_version_warning_cache()
    monkeypatch.setattr(
        runtime_checks,
        "check_ytdlp_version_warning",
        lambda current: calls.append(current) or None,
    )

    first_identity = get_ytdlp_binary_identity(binary)
    assert runtime_checks.check_ytdlp_version_warning_cached(settings) is None
    assert runtime_checks.check_ytdlp_version_warning_cached(settings) is None

    # Keep the same path while changing mtime and size so the cache cannot reuse it.
    binary.write_bytes(b"replacement-binary")
    os.utime(binary, ns=(first_identity.mtime_ns + 1_000_000, first_identity.mtime_ns + 1_000_000))

    second_identity = get_ytdlp_binary_identity(binary)
    assert second_identity != first_identity
    assert runtime_checks.check_ytdlp_version_warning_cached(settings) is None

    assert len(calls) == 2


def test_ytdlp_warning_cache_key_includes_timeout(monkeypatch, tmp_path):
    binary = tmp_path / "yt-dlp"
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    calls: list[Settings] = []
    monkeypatch.setattr(
        runtime_checks,
        "check_ytdlp_version_warning",
        lambda current: calls.append(current) or None,
    )
    runtime_checks.clear_ytdlp_version_warning_cache()

    assert runtime_checks.check_ytdlp_version_warning_cached(
        Settings(ytdlp_path=str(binary), ytdlp_timeout=17)
    ) is None
    assert runtime_checks.check_ytdlp_version_warning_cached(
        Settings(ytdlp_path=str(binary), ytdlp_timeout=18)
    ) is None

    assert len(calls) == 2


def test_ytdlp_warning_cache_stores_missing_binary_warning(monkeypatch):
    calls: list[Settings] = []
    monkeypatch.setattr(
        runtime_checks,
        "get_ytdlp_binary_identity",
        lambda _: MISSING_YTDLP_BINARY_IDENTITY,
    )
    monkeypatch.setattr(
        runtime_checks,
        "check_ytdlp_version_warning",
        lambda current: calls.append(current) or "見つかりません",
    )
    runtime_checks.clear_ytdlp_version_warning_cache()
    settings = Settings(ytdlp_path="missing-yt-dlp", ytdlp_timeout=17)

    assert runtime_checks.check_ytdlp_version_warning_cached(settings) == "見つかりません"
    assert runtime_checks.check_ytdlp_version_warning_cached(settings) == "見つかりません"
    assert len(calls) == 1


def test_check_whisper_model_warning_when_file_missing(tmp_path):
    """恒久対策: model が無いとき設定パスと環境変数名を含む警告を返すこと."""
    missing_model_path = str(tmp_path / "missing-model.bin")
    settings = Settings(whisper_model_path=missing_model_path)

    warning = runtime_checks.check_whisper_model_warning(settings)

    assert warning is not None
    assert missing_model_path in warning
    assert "YTLK_WHISPER_MODEL_PATH" in warning


def test_check_whisper_model_warning_when_file_present(tmp_path):
    model_path = tmp_path / "model.bin"
    model_path.write_bytes(b"model-fixture")
    settings = Settings(whisper_model_path=str(model_path))

    assert runtime_checks.check_whisper_model_warning(settings) is None


def test_check_whisper_model_warning_defaults_to_current_settings(monkeypatch):
    monkeypatch.setattr(
        runtime_checks,
        "get_settings",
        lambda: Settings(whisper_model_path="definitely-missing-model.bin"),
    )

    warning = runtime_checks.check_whisper_model_warning()

    assert warning is not None
    assert "YTLK_WHISPER_MODEL_PATH" in warning


def test_ytdlp_warning_cache_rechecks_when_missing_binary_appears(monkeypatch):
    installed_identity = YtdlpBinaryIdentity(
        resolved_path="/usr/local/bin/yt-dlp",
        device=1,
        inode=2,
        size=3,
        mtime_ns=4,
    )
    identities = iter((MISSING_YTDLP_BINARY_IDENTITY, installed_identity))
    calls: list[Settings] = []
    monkeypatch.setattr(
        runtime_checks,
        "get_ytdlp_binary_identity",
        lambda _: next(identities),
    )
    monkeypatch.setattr(
        runtime_checks,
        "check_ytdlp_version_warning",
        lambda current: calls.append(current) or None,
    )
    runtime_checks.clear_ytdlp_version_warning_cache()
    settings = Settings(ytdlp_path="yt-dlp", ytdlp_timeout=17)

    assert runtime_checks.check_ytdlp_version_warning_cached(settings) is None
    assert runtime_checks.check_ytdlp_version_warning_cached(settings) is None
    assert len(calls) == 2
