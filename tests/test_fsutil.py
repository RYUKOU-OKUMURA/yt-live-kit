"""_fsutil のユニットテスト."""

from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.services._fsutil import advisory_lock, write_bytes_atomically, write_text_atomically


def test_write_text_atomically_writes_content(tmp_path: Path):
    target = tmp_path / "nested" / "output.txt"
    write_text_atomically(target, "hello\nworld")
    assert target.read_text(encoding="utf-8") == "hello\nworld"


def test_write_text_atomically_leaves_no_tmp_files(tmp_path: Path):
    target = tmp_path / "data.json"
    write_text_atomically(target, '{"ok": true}')
    tmp_files = list(tmp_path.glob(".*.tmp"))
    assert tmp_files == []
    assert target.is_file()


def test_write_text_atomically_overwrites_existing(tmp_path: Path):
    target = tmp_path / "meta.json"
    target.write_text("old", encoding="utf-8")
    write_text_atomically(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_write_text_atomically_cleans_up_tmp_on_replace_failure(tmp_path: Path):
    target = tmp_path / "broken.txt"
    with patch("yt_live_kit.services._fsutil.os.replace", side_effect=OSError("fail")):
        with pytest.raises(OSError, match="fail"):
            write_text_atomically(target, "payload")
    assert not target.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_text_atomically_applies_explicit_mode_and_fsyncs(
    tmp_path: Path,
) -> None:
    target = tmp_path / "secret.json"
    with patch("yt_live_kit.services._fsutil.os.fsync") as fsync:
        write_text_atomically(target, "{}", mode=0o600)

    assert target.stat().st_mode & 0o777 == 0o600
    assert fsync.call_count == 2


def test_write_text_atomically_fsyncs_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "payload.json"
    with patch("yt_live_kit.services._fsutil.fsync_directory") as fsync_directory:
        write_text_atomically(target, '{"ok": true}')
    fsync_directory.assert_called_once_with(target.parent)


def test_write_bytes_atomically_writes_content_and_fsyncs_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "binary.bin"
    with patch("yt_live_kit.services._fsutil.fsync_directory") as fsync_directory:
        write_bytes_atomically(target, b"\x00\x01", mode=0o600)
    assert target.read_bytes() == b"\x00\x01"
    assert target.stat().st_mode & 0o777 == 0o600
    fsync_directory.assert_called_once_with(target.parent)


def test_advisory_lock_uses_lock_file_in_parent_directory(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / ".settings.lock"

    with advisory_lock(lock_path):
        assert lock_path.is_file()
        assert lock_path.stat().st_mode & 0o777 == 0o600
