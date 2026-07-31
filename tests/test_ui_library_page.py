"""ライブラリページとローカル設定のテスト."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.services.history import ProcessedVideo
from yt_live_kit.ui.state import (
    SESSION_SELECTED_VIDEO_ID,
    get_selected_video_id,
    set_selected_video_id,
)
from yt_live_kit.ui.views._local_settings import (
    load_archived_ids,
    save_archived_ids,
)
from yt_live_kit.ui.views.library import (
    count_shorts,
    filter_library_videos,
    title_matches,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def _video(
    video_id: str,
    title: str,
    *,
    has_chapters: bool = True,
    has_clips: bool = True,
) -> ProcessedVideo:
    return ProcessedVideo(
        video_id=video_id,
        title=title,
        fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        has_chapters=has_chapters,
        has_transcript=True,
        has_clips=has_clips,
    )


def test_count_shorts_returns_zero_when_directory_is_missing(tmp_path: Path) -> None:
    assert count_shorts("missing", _settings(tmp_path)) == 0


def test_count_shorts_returns_zero_for_empty_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (settings.data_dir / "video" / "shorts" / "output").mkdir(parents=True)

    assert count_shorts("video", settings) == 0


def test_count_shorts_counts_only_mp4_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    output_dir = settings.data_dir / "video" / "shorts" / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "one.mp4").touch()
    (output_dir / "two.mp4").touch()
    (output_dir / "notes.txt").touch()
    (output_dir / "directory.mp4").mkdir()

    assert count_shorts("video", settings) == 2


def test_title_matches_is_case_insensitive_partial_match() -> None:
    assert title_matches("Streamlit 入門 LIVE", "streamLIT") is True
    assert title_matches("Streamlit 入門 LIVE", " 入門 ") is True
    assert title_matches("Streamlit 入門 LIVE", "Python") is False
    assert title_matches("任意のタイトル", "  ") is True


def test_archived_ids_roundtrip_is_sorted_json_array(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    path = save_archived_ids({"video-b", "video-a"}, settings)

    assert path == settings.data_dir / "_config" / "archived_videos.json"
    assert json.loads(path.read_text(encoding="utf-8")) == ["video-a", "video-b"]
    assert load_archived_ids(settings) == {"video-a", "video-b"}


def test_load_archived_ids_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert load_archived_ids(_settings(tmp_path)) == set()


def test_load_archived_ids_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = settings.data_dir / "_config" / "archived_videos.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")

    assert load_archived_ids(settings) == set()


def test_load_archived_ids_returns_empty_for_non_string_array(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = settings.data_dir / "_config" / "archived_videos.json"
    path.parent.mkdir(parents=True)
    path.write_text('["valid", 123]', encoding="utf-8")

    assert load_archived_ids(settings) == set()


def test_filter_library_videos_switches_between_active_and_archived() -> None:
    active = _video("active", "Active video")
    archived = _video("archived", "Archived video")
    videos = [active, archived]

    assert filter_library_videos(videos, archived_ids={"archived"}) == [active]
    assert filter_library_videos(
        videos, archived_ids={"archived"}, show_archived=True
    ) == [active, archived]


def test_filter_library_videos_combines_search_and_status() -> None:
    videos = [
        _video("ready", "Python Live"),
        _video("missing", "Python basics", has_chapters=False, has_clips=False),
        _video("other", "Streamlit Live"),
    ]

    assert filter_library_videos(
        videos,
        query="PYTHON",
        status="チャプター未生成",
    ) == [videos[1]]
    assert filter_library_videos(
        videos,
        status="ショートあり",
        shorts_counts={"ready": 2},
    ) == [videos[0]]


def test_selected_video_id_state_roundtrip() -> None:
    set_selected_video_id(None)
    assert get_selected_video_id() is None

    set_selected_video_id("video123")

    assert get_selected_video_id() == "video123"
    assert st.session_state[SESSION_SELECTED_VIDEO_ID] == "video123"


def test_app_registers_default_library_and_hidden_detail_pages() -> None:
    app_path = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    page_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
        and node.func.attr == "Page"
    ]
    page_options = {
        next(
            keyword.value.value
            for keyword in call.keywords
            if keyword.arg == "title" and isinstance(keyword.value, ast.Constant)
        ): {
            keyword.arg: keyword.value.value
            for keyword in call.keywords
            if isinstance(keyword.value, ast.Constant)
        }
        for call in page_calls
    }

    assert page_options["ライブラリ"]["default"] is True
    assert page_options["動画詳細"]["visibility"] == "hidden"
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "partial"
        for node in ast.walk(tree)
    )
