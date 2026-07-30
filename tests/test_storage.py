"""storage サービスのユニットテスト."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.storage import (
    StorageError,
    dir_size,
    format_bytes,
    purge_source,
    purge_sources_older_than,
    summarize,
)


def _write_meta(
    video_dir: Path,
    video_id: str,
    *,
    title: str = "テスト動画",
    fetched_at: datetime | None = None,
) -> None:
    fetched_at = fetched_at or datetime(2026, 7, 30, tzinfo=timezone.utc)
    meta = {
        "id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": 3600,
        "ytdlp_version": "2026.7.4",
        "fetched_at": fetched_at.isoformat(),
        "subtitle_lang": "ja",
    }
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _write_file(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_video_tree(
    video_dir: Path,
    video_id: str,
    *,
    fetched_at: datetime | None = None,
) -> None:
    """成果物と削除対象を含む擬似動画ディレクトリを作る."""
    _write_meta(video_dir, video_id, fetched_at=fetched_at)
    _write_file(video_dir / "clips" / "source" / "video.mp4", b"a" * 1000)
    _write_file(video_dir / "highlights" / "segments" / "seg.mp4", b"b" * 500)
    _write_file(video_dir / "chapters" / "chapters.md", b"0:00 start\n")
    _write_file(video_dir / "transcript" / "full.txt", b"full text")
    _write_file(video_dir / "clips" / "output" / "clip.mp4", b"c" * 200)
    _write_file(video_dir / "subtitles" / "ja.vtt", b"WEBVTT\n")


def test_dir_size_missing_path(tmp_path: Path):
    assert dir_size(tmp_path / "missing") == 0


def test_dir_size_recursive(tmp_path: Path):
    _write_file(tmp_path / "a.txt", b"12345")
    _write_file(tmp_path / "sub" / "b.txt", b"678")
    assert dir_size(tmp_path) == 8


def test_summarize_breakdown(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    video_dir = tmp_path / "videoAaaaaaa"
    _build_video_tree(video_dir, "videoAaaaaaa")
    (tmp_path / "_batch").mkdir()

    summary = summarize(settings)
    assert summary.total_bytes == dir_size(tmp_path)
    assert len(summary.videos) == 1

    video = summary.videos[0]
    assert video.video_id == "videoAaaaaaa"
    assert video.title == "テスト動画"
    assert video.source_bytes == 1000
    assert video.intermediate_bytes == 500
    assert video.output_bytes == 200
    assert video.total_bytes == video.source_bytes + video.intermediate_bytes + video.output_bytes + video.other_bytes


def test_summarize_sorts_by_total_bytes_desc(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    small = tmp_path / "smallVideo1"
    large = tmp_path / "largeVideo1"
    _build_video_tree(small, "smallVideo1")
    _build_video_tree(large, "largeVideo1")
    _write_file(large / "clips" / "source" / "extra.mp4", b"z" * 5000)

    summary = summarize(settings)
    assert summary.videos[0].video_id == "largeVideo1"
    assert summary.videos[1].video_id == "smallVideo1"


def test_purge_source_removes_only_deletable_dirs(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    video_id = "purgeTest01"
    video_dir = tmp_path / video_id
    _build_video_tree(video_dir, video_id)

    deleted = purge_source(video_id, settings)
    assert deleted == 1500
    assert not (video_dir / "clips" / "source").exists()
    assert not (video_dir / "highlights" / "segments").exists()
    assert (video_dir / "chapters" / "chapters.md").is_file()
    assert (video_dir / "transcript" / "full.txt").is_file()
    assert (video_dir / "clips" / "output" / "clip.mp4").is_file()
    assert (video_dir / "meta.json").is_file()


def test_purge_source_missing_dir_returns_zero(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    assert purge_source("noSuchVideo", settings) == 0


def test_purge_source_rejects_outside_data_dir(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(StorageError, match="データディレクトリ外"):
        purge_source("..", settings)


def test_purge_sources_older_than_boundary(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    old_id = "oldVideo1111"
    recent_id = "recentVideo1"
    _build_video_tree(
        tmp_path / old_id,
        old_id,
        fetched_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    _build_video_tree(
        tmp_path / recent_id,
        recent_id,
        fetched_at=datetime.now(timezone.utc) - timedelta(days=3),
    )

    results = purge_sources_older_than(7, settings)
    assert results == [(old_id, 1500)]
    assert not (tmp_path / old_id / "clips" / "source").exists()
    assert (tmp_path / recent_id / "clips" / "source").exists()


def test_purge_sources_older_than_uses_directory_name_not_meta_id(tmp_path: Path):
    """meta.id とディレクトリ名が不一致でも、走査中のディレクトリだけ削除する."""
    settings = Settings(data_dir=tmp_path)
    dir_name = "dirMismatch1"
    meta_id = "metaIdDiff1"
    other_id = "otherVideo1"
    old_fetched_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    recent_fetched_at = datetime.now(timezone.utc) - timedelta(days=3)

    # ディレクトリ名 dirMismatch1 だが meta.id は metaIdDiff1（古い）
    _build_video_tree(
        tmp_path / dir_name,
        meta_id,
        fetched_at=old_fetched_at,
    )
    # meta.id と同名の別ディレクトリ（recent — 走査中 dirMismatch1 からは触らない）
    _build_video_tree(
        tmp_path / meta_id,
        meta_id,
        fetched_at=recent_fetched_at,
    )
    # 通常の recent 動画（削除対象外）
    _build_video_tree(
        tmp_path / other_id,
        other_id,
        fetched_at=recent_fetched_at,
    )

    results = purge_sources_older_than(7, settings)
    assert results == [(dir_name, 1500)]
    assert not (tmp_path / dir_name / "clips" / "source").exists()
    assert (tmp_path / meta_id / "clips" / "source").exists()
    assert (tmp_path / other_id / "clips" / "source").exists()


def test_purge_sources_older_than_skips_unreadable_meta(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    broken_id = "brokenMeta1"
    video_dir = tmp_path / broken_id
    _build_video_tree(
        video_dir,
        broken_id,
        fetched_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    (video_dir / "meta.json").write_text("{invalid", encoding="utf-8")

    results = purge_sources_older_than(7, settings)
    assert results == []
    assert (video_dir / "clips" / "source").exists()


def test_format_bytes_boundaries():
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024**2) == "1.0 MB"
    assert format_bytes(1024**3) == "1.0 GB"
    assert format_bytes(int(3.2 * 1024**3)) == "3.2 GB"
