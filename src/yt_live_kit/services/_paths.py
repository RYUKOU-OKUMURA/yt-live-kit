"""ファイル保存先と識別子の共通安全境界。"""

from __future__ import annotations

import re
from pathlib import Path


class PathConfinementError(ValueError):
    """識別子または保存先が安全境界を越えた。"""


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
RESERVED_DATA_DIR_NAMES = frozenset(
    {"_jobs", "_schedule", "_config", "_channels", "_batch"}
)


def safe_identifier(value: object, label: str) -> str:
    """path の 1 要素として安全な識別子を返す。

    YouTube の通常 ID（英数字・``_``・``-`` の 11 文字）を含む、既存の
    job / clip ID の互換範囲を保つ。値や path はエラーメッセージへ含めない。
    """
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise PathConfinementError(f"{label}が正しくありません。")
    if any(separator in value for separator in ("/", "\\", "\x00")):
        raise PathConfinementError(f"{label}にパスは指定できません。")
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise PathConfinementError(f"{label}に使用できない文字が含まれています。")
    return value


def safe_video_identifier(value: object, label: str = "動画 ID") -> str:
    """動画 directory 用の識別子を返す（予約 namespace だけを除外）。"""
    safe = safe_identifier(value, label)
    if safe in RESERVED_DATA_DIR_NAMES:
        raise PathConfinementError(f"{label}が正しくありません。")
    return safe


def confined_path(
    data_dir: Path,
    *parts: str | Path,
    label: str = "保存先",
) -> Path:
    """解決後も data root 配下にある lexical path を返す。

    ``resolve(strict=False)`` を使うため、まだ作成されていない末尾も検査
    できる。既存の親 directory / symlink が root 外を指す場合は拒否する。
    返却値は既存の相対・絶対 path 形状を保ち、検査だけを canonical path で行う。
    """
    for part in parts:
        if any(component in {".", ".."} for component in Path(part).parts):
            raise PathConfinementError(
                f"{label}にパスのトラバーサルは指定できません。"
            )

    root = Path(data_dir)
    candidate = root.joinpath(*parts)
    try:
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathConfinementError(f"{label}がデータディレクトリ外のため安全に扱えません。") from exc
    return candidate


def confined_identifier_path(
    data_dir: Path,
    identifier: object,
    *parts: str | Path,
    label: str,
) -> Path:
    """識別子を検証してから data root 配下の path を返す。"""
    safe = safe_identifier(identifier, label)
    return confined_path(data_dir, safe, *parts, label=label)


def confined_video_path(
    data_dir: Path,
    video_id: object,
    *parts: str | Path,
    label: str = "動画保存先",
) -> Path:
    """予約 namespace だけを除外した動画 ID の data root 内 path を返す。"""
    safe = safe_video_identifier(video_id, "動画 ID")
    return confined_path(data_dir, safe, *parts, label=label)
