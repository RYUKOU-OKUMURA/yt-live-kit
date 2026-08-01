"""_fsutil のユニットテスト."""

from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.services._fsutil import write_text_atomically


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
