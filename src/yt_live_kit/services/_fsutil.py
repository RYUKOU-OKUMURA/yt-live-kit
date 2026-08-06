"""ファイルシステムユーティリティ."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import fcntl

logger = logging.getLogger(__name__)


@contextmanager
def advisory_lock(path: Path, *, mode: int = 0o600) -> Iterator[None]:
    """指定ファイルを使ってプロセス間の排他ロックを取得する."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        os.chmod(path, mode)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def fsync_directory_fd(directory_fd: int) -> None:
    """開いているディレクトリ fd のメタデータをディスクへ同期する."""
    try:
        os.fsync(directory_fd)
    except OSError:
        # directory entry の durability は filesystem に依存する。
        pass


def fsync_directory(directory: Path) -> None:
    """ディレクトリのメタデータをディスクへ同期する."""
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        fsync_directory_fd(directory_fd)
    finally:
        os.close(directory_fd)


def write_text_atomically(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
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
            temporary_path = Path(temporary.name)
            if mode is not None:
                os.chmod(temporary_path, mode)
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        fsync_directory(path.parent)
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


def write_bytes_atomically(
    path: Path,
    content: bytes,
    *,
    mode: int | None = None,
) -> None:
    """同一ディレクトリの一時ファイル経由でバイナリを原子的に書き込む."""
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            if mode is not None:
                os.chmod(temporary_path, mode)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        fsync_directory(path.parent)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "一時バイナリファイルを削除できませんでした: %s",
                    temporary_path,
                    exc_info=True,
                )
