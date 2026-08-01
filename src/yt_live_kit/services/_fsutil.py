"""ファイルシステムユーティリティ."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def write_text_atomically(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """同一ディレクトリの一時ファイル経由でテキストを原子的に書き込む."""
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "一時テキストファイルを削除できませんでした: %s",
                    temporary_path,
                    exc_info=True,
                )
