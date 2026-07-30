"""ai_prompt サービスのユニットテスト."""

from pathlib import Path

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.ai_prompt import (
    build_chapters_prompt,
    find_project_root,
    generate_chapters,
    save_prompt_file,
)
from yt_live_kit.services.chapter_validator import validate_chapters


def test_find_project_root():
    root = find_project_root()
    assert (root / "prompts" / "chapters.md").is_file()


def test_build_chapters_prompt_embeds_transcript():
    prompt = build_chapters_prompt("[00:00:00] テスト字幕")
    assert "[00:00:00] テスト字幕" in prompt
    assert "{{compressed_transcript}}" not in prompt


def test_save_prompt_file(tmp_path: Path):
    video_id = "test123"
    video_dir = tmp_path / video_id
    video_dir.mkdir()
    (video_dir / "transcript").mkdir()
    (video_dir / "transcript" / "compressed.txt").write_text(
        "[00:00:00] sample", encoding="utf-8"
    )

    settings = Settings(data_dir=tmp_path)
    prompt_path = save_prompt_file(video_id, "test prompt content", settings)
    assert prompt_path.is_file()
    assert prompt_path.read_text(encoding="utf-8") == "test prompt content"


def test_generate_chapters_prompt_only(tmp_path: Path):
    video_id = "test456"
    video_dir = tmp_path / video_id
    (video_dir / "transcript").mkdir(parents=True)
    (video_dir / "transcript" / "compressed.txt").write_text(
        "[00:00:00] hello", encoding="utf-8"
    )

    settings = Settings(data_dir=tmp_path)
    result = generate_chapters(video_id, settings, prompt_only=True)
    assert result.prompt_path.is_file()
    assert result.chapters_path is None
    assert result.used_codex is False
    assert "{{compressed_transcript}}" not in result.prompt_path.read_text(encoding="utf-8")


def test_validate_sample_for_manual_check():
    """P2-5: 手動確認用の正/不正サンプル."""
    valid = validate_chapters(
        "0:00 配信開始\n5:30 Codex デモ\n12:00 まとめ\n"
    )
    assert valid.ok

    invalid = validate_chapters(
        "0:00 開始\n0:03 短すぎ\n5:00 本編\n"
    )
    assert not invalid.ok
