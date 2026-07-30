"""概要欄テンプレート合成."""

from __future__ import annotations

from pathlib import Path

from yt_live_kit.config import Settings, get_settings

_CONFIG_DIR = "_config"
_TEMPLATE_FILENAME = "description_template.txt"


class DescriptionError(Exception):
    """概要欄生成エラー（ユーザー向け日本語メッセージ）."""


def get_template_path(settings: Settings | None = None) -> Path:
    """概要欄テンプレートファイルのパスを返す."""
    settings = settings or get_settings()
    return settings.data_dir / _CONFIG_DIR / _TEMPLATE_FILENAME


def save_template(text: str, settings: Settings | None = None) -> Path:
    """概要欄テンプレートを保存してパスを返す."""
    settings = settings or get_settings()
    path = get_template_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_description(video_id: str, settings: Settings | None = None) -> str:
    """チャプター本文とテンプレートを合成した概要欄テキストを返す."""
    settings = settings or get_settings()
    chapters_path = settings.data_dir / video_id / "chapters" / "chapters.md"
    if not chapters_path.is_file():
        raise DescriptionError(
            "チャプターが見つかりません。先にチャプターを生成してください。"
        )

    chapters_text = chapters_path.read_text(encoding="utf-8").strip()
    if not chapters_text:
        raise DescriptionError(
            "チャプターが空です。先にチャプターを生成してください。"
        )

    template_path = get_template_path(settings)
    if not template_path.is_file():
        return chapters_text

    template = template_path.read_text(encoding="utf-8")
    return template.replace("{{timeline}}", chapters_text)
