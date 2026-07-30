"""概要欄テンプレート合成のユニットテスト."""

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.description import (
    DescriptionError,
    build_description,
    get_template_path,
    save_template,
)


def _write_chapters(tmp_path, video_id: str, text: str) -> None:
    chapters_dir = tmp_path / video_id / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    (chapters_dir / "chapters.md").write_text(text, encoding="utf-8")


def test_build_description_with_template(tmp_path):
    video_id = "desc001"
    settings = Settings(data_dir=tmp_path)
    _write_chapters(tmp_path, video_id, "0:00 開始\n5:00 本編\n")

    save_template("【配信概要】\n\n{{timeline}}\n\n#タグ", settings=settings)

    result = build_description(video_id, settings=settings)

    assert result.startswith("【配信概要】")
    assert "0:00 開始" in result
    assert "5:00 本編" in result
    assert "#タグ" in result
    assert "{{timeline}}" not in result


def test_build_description_without_template(tmp_path):
    video_id = "desc002"
    settings = Settings(data_dir=tmp_path)
    chapters_text = "0:00 開始\n5:00 本編\n10:00 終了\n"
    _write_chapters(tmp_path, video_id, chapters_text)

    result = build_description(video_id, settings=settings)

    assert result == chapters_text.strip()


def test_build_description_missing_chapters_raises(tmp_path):
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(DescriptionError, match="チャプターが見つかりません"):
        build_description("missing001", settings=settings)


def test_get_template_path(tmp_path):
    settings = Settings(data_dir=tmp_path)
    path = get_template_path(settings)
    assert path == tmp_path / "_config" / "description_template.txt"
