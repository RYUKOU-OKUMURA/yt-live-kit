"""概要欄テンプレート合成のユニットテスト."""

import json

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.description import (
    DescriptionError,
    build_description,
    build_shorts_description,
    get_shorts_template_path,
    get_template_path,
    save_shorts_template,
    save_template,
)


def _write_chapters(tmp_path, video_id: str, text: str) -> None:
    chapters_dir = tmp_path / video_id / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    (chapters_dir / "chapters.md").write_text(text, encoding="utf-8")


def _write_meta(tmp_path, video_id: str, *, title: str = "元のライブ配信") -> None:
    video_dir = tmp_path / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "upload_date": "20260424",
                "duration": 3465,
                "ytdlp_version": "2026.07.04",
                "fetched_at": "2026-07-31T08:06:31.918175Z",
                "subtitle_lang": "ja-orig",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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


def test_get_shorts_template_path_is_separate_from_long_form(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert get_shorts_template_path(settings) == (
        tmp_path / "_config" / "shorts_description_template.txt"
    )
    assert get_shorts_template_path(settings) != get_template_path(settings)


def test_build_shorts_description_without_template_returns_input(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _write_meta(tmp_path, "short001")

    result = build_shorts_description(
        "AI の話をしました。", video_id="short001", start_ms=90_000, settings=settings
    )

    assert result == "AI の話をしました。"


def test_build_shorts_description_inserts_links_and_start_seconds(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _write_meta(tmp_path, "short002")
    save_shorts_template(
        "{{description}}\n\n"
        "▼ 元のライブ配信\n{{source_title}}\n{{source_url}}\n\n"
        "▼ チャンネル\nhttps://www.youtube.com/@ai.seitai\n",
        settings=settings,
    )

    result = build_shorts_description(
        " AI の話をしました。 ",
        video_id="short002",
        start_ms=90_500,
        settings=settings,
    )

    assert "AI の話をしました。" in result
    assert "元のライブ配信" in result
    assert "https://www.youtube.com/watch?v=short002&t=90s" in result
    assert "https://www.youtube.com/@ai.seitai" in result
    assert "{{" not in result


def test_build_shorts_description_is_deterministic_and_replaces_existing_start(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _write_meta(tmp_path, "short003")
    (tmp_path / "short003" / "meta.json").write_text(
        json.dumps(
            {
                "id": "short003",
                "title": "元のライブ配信",
                "url": "https://www.youtube.com/watch?v=short003&t=10s",
                "ytdlp_version": "2026.07.04",
                "fetched_at": "2026-07-31T08:06:31.918175Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    save_shorts_template("{{source_url}}", settings=settings)

    first = build_shorts_description(
        "本文", video_id="short003", start_ms=125_000, settings=settings
    )
    second = build_shorts_description(
        "本文", video_id="short003", start_ms=125_000, settings=settings
    )

    assert first == second
    assert first.endswith("t=125s")
    assert "t=10s" not in first


def test_build_shorts_description_omits_start_when_clip_starts_at_head(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _write_meta(tmp_path, "short004")
    save_shorts_template("{{source_url}}", settings=settings)

    assert build_shorts_description(
        "本文", video_id="short004", start_ms=0, settings=settings
    ) == "https://www.youtube.com/watch?v=short004"
    assert build_shorts_description(
        "本文", video_id="short004", settings=settings
    ) == "https://www.youtube.com/watch?v=short004"


def test_build_shorts_description_without_source_placeholders_needs_no_meta(tmp_path):
    settings = Settings(data_dir=tmp_path)
    save_shorts_template(
        "{{description}}\n\nhttps://www.youtube.com/@ai.seitai", settings=settings
    )

    result = build_shorts_description("本文", video_id="missing001", settings=settings)

    assert result == "本文\n\nhttps://www.youtube.com/@ai.seitai"


def test_build_shorts_description_missing_meta_raises(tmp_path):
    settings = Settings(data_dir=tmp_path)
    save_shorts_template("{{source_url}}", settings=settings)

    with pytest.raises(DescriptionError, match="元配信の情報が見つかりません"):
        build_shorts_description("本文", video_id="missing002", settings=settings)


def test_build_shorts_description_broken_meta_raises(tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "short005"
    video_dir.mkdir(parents=True)
    (video_dir / "meta.json").write_text("{壊れた", encoding="utf-8")
    save_shorts_template("{{source_url}}", settings=settings)

    with pytest.raises(DescriptionError, match="元配信の情報を読み込めませんでした"):
        build_shorts_description("本文", video_id="short005", settings=settings)


def test_build_shorts_description_rejects_angle_brackets(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _write_meta(tmp_path, "short006")
    save_shorts_template("{{description}}\n<b>強調</b>", settings=settings)

    with pytest.raises(DescriptionError, match="半角の山カッコは使えません"):
        build_shorts_description("本文", video_id="short006", settings=settings)


def test_build_shorts_description_rejects_over_byte_limit(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _write_meta(tmp_path, "short007")
    save_shorts_template("{{description}}\n" + "あ" * 1700, settings=settings)

    with pytest.raises(DescriptionError, match="5000 bytes を超えます"):
        build_shorts_description("本文", video_id="short007", settings=settings)


def test_shorts_template_does_not_affect_long_form_description(tmp_path):
    video_id = "short008"
    settings = Settings(data_dir=tmp_path)
    _write_chapters(tmp_path, video_id, "0:00 開始\n5:00 本編\n")
    save_shorts_template("ショート専用\n{{description}}", settings=settings)

    assert build_description(video_id, settings=settings) == "0:00 開始\n5:00 本編"
