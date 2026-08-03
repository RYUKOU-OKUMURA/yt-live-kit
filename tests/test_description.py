"""概要欄テンプレート合成のユニットテスト."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.description import (
    DEFAULT_SHORTS_DESCRIPTION_TEMPLATE,
    DescriptionError,
    SHORTS_DESCRIPTION_CTA,
    ShortsDescriptionRequirements,
    build_description,
    build_shorts_description,
    build_shorts_description_for_upload,
    ensure_shorts_description_template,
    get_shorts_template_path,
    get_template_path,
    save_shorts_template,
    save_template,
    validate_shorts_description,
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


def _valid_quality_template(extra: str = "") -> str:
    return (
        "{{description}}\n\n"
        "元動画: {{source_title}}\n"
        "{{source_url}}\n\n"
        f"{SHORTS_DESCRIPTION_CTA}\n"
        f"{extra}"
    )


def test_quality_gate_creates_default_template_and_freezes_requirements(tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "gate001"
    _write_meta(tmp_path, video_id)

    result = build_shorts_description_for_upload(
        "生成説明", video_id=video_id, start_ms=90_500, settings=settings
    )
    template_path = get_shorts_template_path(settings)
    meta_path = tmp_path / video_id / "meta.json"

    assert template_path.read_text(encoding="utf-8") == (
        DEFAULT_SHORTS_DESCRIPTION_TEMPLATE
    )
    assert result.description.startswith("生成説明")
    assert "https://www.youtube.com/watch?v=gate001&t=90s" in result.description
    assert SHORTS_DESCRIPTION_CTA in result.description
    assert result.requirements.generated_description == "生成説明"
    assert result.requirements.source_title == "元のライブ配信"
    assert result.requirements.source_url.endswith("t=90s")
    assert result.requirements.template_bytes_fingerprint == hashlib.sha256(
        template_path.read_bytes()
    ).hexdigest()
    assert result.requirements.meta_json_fingerprint == hashlib.sha256(
        meta_path.read_bytes()
    ).hexdigest()
    assert validate_shorts_description(result.description, result.requirements) == ()

    with pytest.raises((AttributeError, TypeError, ValueError)):
        result.requirements.source_title = "変更"


def test_existing_shorts_template_bytes_are_never_rewritten_by_quality_gate(tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "gate002"
    _write_meta(tmp_path, video_id)
    template_path = get_shorts_template_path(settings)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    original = _valid_quality_template().replace("\n", "\r\n").encode("utf-8")
    template_path.write_bytes(original)

    build_shorts_description_for_upload(
        "生成説明", video_id=video_id, settings=settings
    )

    assert template_path.read_bytes() == original


def test_quality_gate_uses_same_pure_validator_for_final_edited_body(tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "gate003"
    _write_meta(tmp_path, video_id)
    save_shorts_template(_valid_quality_template(), settings=settings)

    result = build_shorts_description_for_upload(
        "生成説明", video_id=video_id, settings=settings
    )
    final_body = result.description + "\nユーザーが編集した補足。"

    assert validate_shorts_description(final_body, result.requirements) == ()
    assert "チャンネル登録 CTA" in validate_shorts_description(
        final_body.replace(SHORTS_DESCRIPTION_CTA, ""), result.requirements
    )
    decorated_cta = final_body.replace(
        SHORTS_DESCRIPTION_CTA, f"前置き {SHORTS_DESCRIPTION_CTA}"
    )
    assert "チャンネル登録 CTA" in validate_shorts_description(
        decorated_cta, result.requirements
    )


def test_quality_gate_requires_fixed_cta_as_an_exact_template_line(tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "gate003b"
    _write_meta(tmp_path, video_id)
    save_shorts_template(
        _valid_quality_template().replace(
            f"{SHORTS_DESCRIPTION_CTA}\n",
            f"前置き {SHORTS_DESCRIPTION_CTA}\n",
        ),
        settings=settings,
    )

    with pytest.raises(DescriptionError, match="固定 CTA 文"):
        build_shorts_description_for_upload(
            "生成説明", video_id=video_id, settings=settings
        )


def test_quality_gate_rejects_missing_required_placeholders_without_overwriting_file(
    tmp_path,
):
    settings = Settings(data_dir=tmp_path)
    video_id = "gate004"
    _write_meta(tmp_path, video_id)
    path = get_shorts_template_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "{{description}}\n{{source_title}}\n"
        f"{SHORTS_DESCRIPTION_CTA}\n"
    ).encode("utf-8")
    path.write_bytes(original)

    with pytest.raises(DescriptionError, match=r"\{\{source_url\}\}"):
        build_shorts_description_for_upload(
            "生成説明", video_id=video_id, settings=settings
        )

    assert path.read_bytes() == original


def test_quality_gate_rejects_broken_meta_fail_closed(tmp_path):
    settings = Settings(data_dir=tmp_path)
    video_id = "gate005"
    video_dir = tmp_path / video_id
    video_dir.mkdir(parents=True)
    (video_dir / "meta.json").write_bytes("{壊れた".encode("utf-8"))
    save_shorts_template(_valid_quality_template(), settings=settings)

    with pytest.raises(DescriptionError, match="元配信の情報を読み込めませんでした"):
        build_shorts_description_for_upload(
            "生成説明", video_id=video_id, settings=settings
        )


def test_quality_gate_rejects_angle_brackets_and_byte_limit(tmp_path):
    settings = Settings(data_dir=tmp_path)
    angle_id = "gate006"
    _write_meta(tmp_path, angle_id)
    save_shorts_template(_valid_quality_template(), settings=settings)

    with pytest.raises(DescriptionError, match="半角の山カッコは使えません"):
        build_shorts_description_for_upload(
            "生成説明 <b>禁止</b>", video_id=angle_id, settings=settings
        )

    byte_id = "gate007"
    _write_meta(tmp_path, byte_id)
    with pytest.raises(DescriptionError, match="5000 bytes を超えます"):
        build_shorts_description_for_upload(
            "あ" * 1700, video_id=byte_id, settings=settings
        )


def test_default_template_creation_is_safe_under_same_process_concurrency(tmp_path):
    settings = Settings(data_dir=tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(
            executor.map(
                lambda _index: ensure_shorts_description_template(settings),
                range(8),
            )
        )

    path = get_shorts_template_path(settings)
    assert paths == [path] * 8
    assert path.read_text(encoding="utf-8") == DEFAULT_SHORTS_DESCRIPTION_TEMPLATE
    assert not list(path.parent.glob(".*.tmp"))


def test_default_template_creation_fails_closed_when_atomic_write_fails(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)

    def fail_atomic(*_args, **_kwargs):
        raise OSError("atomic failure")

    monkeypatch.setattr(
        "yt_live_kit.services.description.write_text_atomically", fail_atomic
    )

    with pytest.raises(DescriptionError, match="安全に初回作成できません"):
        ensure_shorts_description_template(settings)

    path = get_shorts_template_path(settings)
    assert not path.exists()
    assert not list(path.parent.glob(".*.tmp"))


def test_requirements_object_rejects_non_fixed_cta():
    with pytest.raises(ValueError, match="固定 CTA は変更できません"):
        ShortsDescriptionRequirements(
            generated_description="説明",
            source_title="元動画",
            source_url="https://example.com/watch?v=1",
            fixed_cta="別の CTA",
            template_bytes_fingerprint="template",
            meta_json_fingerprint="meta",
        )
