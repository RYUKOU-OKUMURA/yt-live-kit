"""概要欄テンプレート合成."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta

_CONFIG_DIR = "_config"
_TEMPLATE_FILENAME = "description_template.txt"
_SHORTS_TEMPLATE_FILENAME = "shorts_description_template.txt"
_SHORTS_DESCRIPTION_BYTE_LIMIT = 5000
_SOURCE_TITLE_PLACEHOLDER = "{{source_title}}"
_SOURCE_URL_PLACEHOLDER = "{{source_url}}"
_DESCRIPTION_PLACEHOLDER = "{{description}}"


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


def get_shorts_template_path(settings: Settings | None = None) -> Path:
    """ショート用概要欄テンプレートファイルのパスを返す."""
    settings = settings or get_settings()
    return settings.data_dir / _CONFIG_DIR / _SHORTS_TEMPLATE_FILENAME


def save_shorts_template(text: str, settings: Settings | None = None) -> Path:
    """ショート用概要欄テンプレートを保存してパスを返す."""
    settings = settings or get_settings()
    path = get_shorts_template_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load_video_meta(video_id: str, settings: Settings) -> VideoMeta:
    """元配信のメタデータを読む。欠損・破損は空へ倒さず拒否する."""
    meta_path = settings.data_dir / video_id / "meta.json"
    if not meta_path.is_file():
        raise DescriptionError(
            "元配信の情報が見つかりません。"
            "ショート用概要欄テンプレートの元配信リンクを使うには、"
            "先に元動画を取り込み直してください。"
        )
    try:
        return VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise DescriptionError(
            "元配信の情報を読み込めませんでした。"
            "ショート用概要欄テンプレートの元配信リンクを使うには、"
            "先に元動画を取り込み直してください。"
        ) from exc


def _with_start_seconds(url: str, start_ms: int | None) -> str:
    """元配信 URL へ切り抜き開始秒を t クエリとして付ける."""
    if start_ms is None or start_ms < 1000:
        return url
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "t"
    ]
    query.append(("t", f"{start_ms // 1000}s"))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def build_shorts_description(
    base_description: str,
    *,
    video_id: str,
    start_ms: int | None = None,
    settings: Settings | None = None,
) -> str:
    """ショート説明文へ元配信リンク等の定型文を合成した本文を返す.

    テンプレート未設定時は base_description をそのまま返す。チャンネル URL は
    専用プレースホルダーを持たず、テンプレート本文へ直接記載する運用とする。
    """
    settings = settings or get_settings()
    template_path = get_shorts_template_path(settings)
    if not template_path.is_file():
        return base_description

    try:
        template = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DescriptionError(
            "ショート用概要欄テンプレートを読み込めませんでした。"
            f"{template_path} を確認してください。"
        ) from exc

    text = template.replace(_DESCRIPTION_PLACEHOLDER, base_description.strip())
    if _SOURCE_TITLE_PLACEHOLDER in text or _SOURCE_URL_PLACEHOLDER in text:
        meta = _load_video_meta(video_id, settings)
        text = text.replace(_SOURCE_TITLE_PLACEHOLDER, meta.title)
        text = text.replace(
            _SOURCE_URL_PLACEHOLDER, _with_start_seconds(meta.url, start_ms)
        )

    if "<" in text or ">" in text:
        raise DescriptionError(
            "ショート用概要欄に半角の山カッコは使えません。"
            f"{template_path} と元配信タイトルを確認してください。"
        )
    if len(text.encode("utf-8")) > _SHORTS_DESCRIPTION_BYTE_LIMIT:
        raise DescriptionError(
            "ショート用概要欄が UTF-8 で 5000 bytes を超えます。"
            f"{template_path} の定型文を短くしてください。"
        )
    return text
