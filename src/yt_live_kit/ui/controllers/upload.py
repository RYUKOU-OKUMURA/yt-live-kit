"""Typed adapter that keeps the line reservation safety callbacks together."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.models.upload import UploadOperation


@dataclass(frozen=True)
class LineUploadAdapter:
    """Required pair of checks used by the shorts-line upload route."""

    validate_preview: Callable[[str, Path], None]
    reservation_transaction: Callable[
        [str, Path, Callable[[], UploadOperation]], UploadOperation
    ]

    def __post_init__(self) -> None:
        if not callable(self.validate_preview) or not callable(
            self.reservation_transaction
        ):
            raise TypeError("ライン投稿 adapter の callback が正しくありません。")
