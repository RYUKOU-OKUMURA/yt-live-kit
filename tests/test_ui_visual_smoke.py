"""AppTest による視覚回帰スモークテスト（U9-3）.

``streamlit.testing.v1.AppTest`` は DOM / CSS を持たないため、ここで検証できるのは
「主要ページが例外なく構築され、期待する見出し・主要ウィジェットが存在すること」までである。
配色・形状・レイアウトといった見た目のピクセル差分は検出できない。U9 第 2 弾（shell 刷新）の
回帰は、本テストが green のまま壊れていないことと、目視確認を組み合わせて判断する。

対象は ``src/yt_live_kit/ui/app.py`` が ``st.navigation`` で登録している 4 画面
（ライブラリ / 取り込み / 設定 / 動画詳細）。いずれも production の ``data/`` を使わず、
``YTLK_DATA_DIR`` を ``tmp_path`` の隔離ディレクトリへ向けた空状態で描画できることを確認する。
外部プロセス（yt-dlp / ffmpeg / Codex CLI / whisper.cpp）は一切起動せず、必ずモックする。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate, ClipCandidatesDocument
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.ffmpeg import FfmpegError, MediaStreams
from yt_live_kit.services.shorts import ShortResult
from yt_live_kit.services.shorts_queue import (
    build_shorts_queue_targets,
    make_shorts_queue_clip_spec,
    normalize_queue_candidates,
    run_shorts_queue,
)
from yt_live_kit.services.whisper_runtime import WhisperRuntimeError
from yt_live_kit.ui.session_keys import detail_workspace_key
from yt_live_kit.ui.state import SESSION_SELECTED_VIDEO_ID

_APP_PATH = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
_RUN_TIMEOUT_SECONDS = 30.0
_POPULATED_VIDEO_ID = "vid1234567"
_POPULATED_CHAPTERS = "0:00 はじめに\n0:10 本題\n0:20 まとめ\n"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """production の data/ を読み書きしないよう、隔離ディレクトリへ切り替える."""
    monkeypatch.setenv("YTLK_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _stub_external_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """yt-dlp / ffmpeg / Codex CLI / whisper.cpp の実起動をすべて止める."""
    monkeypatch.setattr(
        "yt_live_kit.ui.runtime_checks.check_ytdlp_version_warning_cached",
        lambda settings=None: None,
    )
    monkeypatch.setattr(
        "yt_live_kit.ui.runtime_checks.check_whisper_model_warning",
        lambda settings=None: None,
    )
    monkeypatch.setattr(
        "yt_live_kit.ui.views.settings.diagnose_ffmpeg",
        lambda configured_path: (_ for _ in ()).throw(FfmpegError("smoke test stub")),
    )
    monkeypatch.setattr(
        "yt_live_kit.ui.views.settings.preflight_whisper_runtime",
        lambda settings: (_ for _ in ()).throw(
            WhisperRuntimeError("smoke test stub")
        ),
    )
    monkeypatch.setattr(
        "yt_live_kit.ui.views.settings.is_codex_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "yt_live_kit.services.shorts_queue.probe_media_streams",
        lambda *args, **kwargs: MediaStreams(video_count=1, audio_count=1),
    )


def _smoke_clip(index: int) -> ClipCandidate:
    start = (index - 1) * 10
    end = index * 10
    return ClipCandidate(
        id=f"clip_{index:03d}",
        title=f"候補 {index}",
        start=f"0:00:{start:02d}",
        end=f"0:00:{end:02d}",
        duration_sec=end - start,
        reason=f"理由 {index}",
    )


def _smoke_telop_document(target) -> TelopScriptDocument:
    return TelopScriptDocument.model_validate(
        {
            "hook_text": "重要ポイント",
            "title_candidates": [f"タイトル {target.target_id}"],
            "description": "説明文です。",
            "tags": ["配信", "要点"],
            "segments": [
                {
                    "start_sec": segment.start_ms / 1000,
                    "end_sec": segment.end_ms / 1000,
                    "lines": [
                        {
                            "text": "テロップ本文",
                            "start_sec": segment.start_ms / 1000,
                            "end_sec": segment.end_ms / 1000,
                            "emphasis": False,
                        }
                    ],
                }
                for segment in target.segments
            ],
        }
    )


def _smoke_queue_spec():
    candidates = [_smoke_clip(1)]
    segments = normalize_queue_candidates(candidates, source="clips")
    targets = build_shorts_queue_targets(segments, mode="individual")
    target = targets[0]
    return make_shorts_queue_clip_spec(
        target,
        _smoke_telop_document(target),
        layout="blur",
        preset="default",
        hook_preset="hook",
    )


def _successful_short_result(
    data_dir: Path,
    video_id: str,
    spec,
) -> ShortResult:
    output = data_dir / video_id / "shorts" / "output" / spec.output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"complete mp4 fixture")
    log = output.with_suffix(".ffmpeg.log")
    log.write_text("fixture", encoding="utf-8")
    return ShortResult(
        video_id=video_id,
        output_path=output,
        command_log_path=log,
        layout=spec.layout,
        burned_subtitles=True,
        duration_sec=10.0,
    )


def _populate_populated_video_detail_state(
    data_dir: Path,
    *,
    video_id: str = _POPULATED_VIDEO_ID,
) -> None:
    """候補・チャプター・生成済みショートを含む動画詳細向けの最小 fixture."""
    video_dir = data_dir / video_id
    (video_dir / "chapters").mkdir(parents=True)
    (video_dir / "transcript").mkdir()
    (video_dir / "clips").mkdir()

    meta = VideoMeta(
        id=video_id,
        title="テスト動画",
        url=f"https://example.com/watch?v={video_id}",
        upload_date="20260101",
        duration=3600,
        ytdlp_version="2026.7.4",
        fetched_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        subtitle_lang="ja",
    )
    (video_dir / "meta.json").write_text(meta.model_dump_json(), encoding="utf-8")
    (video_dir / "chapters" / "chapters.md").write_text(
        _POPULATED_CHAPTERS,
        encoding="utf-8",
    )
    (video_dir / "transcript" / "full.txt").write_text(
        "[00:00:00] 全文です\n",
        encoding="utf-8",
    )
    clip_doc = ClipCandidatesDocument(
        candidates=[
            _smoke_clip(1),
            _smoke_clip(2),
        ]
    )
    (video_dir / "clips" / "candidates.json").write_text(
        clip_doc.model_dump_json(),
        encoding="utf-8",
    )

    settings = Settings(data_dir=data_dir)
    spec = _smoke_queue_spec()
    with patch(
        "yt_live_kit.services.shorts_queue.build_short_from_segments",
        side_effect=lambda *args, **kwargs: _successful_short_result(
            data_dir,
            video_id,
            spec,
        ),
    ):
        run_shorts_queue(video_id, [spec], settings, job_id="visual-smoke-job")


def _run_page(url_path: str | None) -> AppTest:
    """app.py を起動し、``url_path`` に対応するページを描画した ``AppTest`` を返す.

    ``url_path`` が ``None`` の場合は既定ページ（ライブラリ）を描画する。
    ``st.Page`` はコールバック定義のため ``AppTest.switch_page`` はファイルパスを
    要求し使えない。ページ選択は ``StreamlitPage._script_hash`` と同じ
    ``calc_hash(url_path)`` を直接 ``AppTest._page_hash`` に設定して行う。
    """
    at = AppTest.from_file(str(_APP_PATH))
    if url_path is not None:
        at._page_hash = calc_hash(url_path)
    return at.run(timeout=_RUN_TIMEOUT_SECONDS)


def _run_populated_video_detail(
    workspace: str,
    video_id: str = _POPULATED_VIDEO_ID,
) -> AppTest:
    """データあり状態の動画詳細ページを、指定ワークスペースで描画する."""
    at = AppTest.from_file(str(_APP_PATH))
    at.session_state[SESSION_SELECTED_VIDEO_ID] = video_id
    at.session_state[detail_workspace_key(video_id)] = workspace
    at._page_hash = calc_hash("video-detail")
    return at.run(timeout=_RUN_TIMEOUT_SECONDS)


def test_library_page_renders_without_exception() -> None:
    """ライブラリページ（既定ページ）が空状態で例外なく描画できる."""
    at = _run_page(None)

    assert not list(at.exception)
    assert "ライブラリ" in [item.value for item in at.header]
    assert "タイトル検索" in [widget.label for widget in at.main.get("text_input")]


def test_intake_page_renders_without_exception() -> None:
    """取り込みページが空状態で例外なく描画できる."""
    at = _run_page("intake")

    assert not list(at.exception)
    markdown_values = [item.value for item in at.main.markdown]
    assert any("チャプターと切り抜き候補の生成" in value for value in markdown_values)
    expander_labels = [item.label for item in at.main.expander]
    assert "URL を直接入力する（例外ルート）" in expander_labels


def test_settings_page_renders_without_exception() -> None:
    """設定ページが空状態で例外なく描画できる（ffmpeg / Codex / whisper 未検出時）."""
    at = _run_page("settings")

    assert not list(at.exception)
    assert "設定" in [item.value for item in at.header]
    subheader_values = [item.value for item in at.subheader]
    for expected in (
        "チャンネル",
        "実行環境",
        "高精度字幕 runtime",
        "Codex CLI",
        "ストレージ管理",
        "投稿スケジュール",
        "ショート生産ライン",
    ):
        assert expected in subheader_values


def test_video_detail_page_renders_empty_selection_without_exception() -> None:
    """動画詳細ページは未選択状態でも例外なく描画され、案内文が出る."""
    at = _run_page("video-detail")

    assert not list(at.exception)
    assert ":material/movie: 動画詳細" in [item.value for item in at.header]
    assert "ライブラリから動画を選択してください。" in [item.value for item in at.info]


def test_video_detail_shorts_workspace_renders_broken_line_recovery_once(
    tmp_path: Path,
) -> None:
    """破損ライン状態でも、要約と工程が同一 run で widget key を衝突させない.

    ``render_main_line_summary`` は無条件に、``render_shorts_line`` は
    ショートワークスペース選択時に描画される。両方が復旧ボタンを出すと
    ``StreamlitDuplicateElementKey`` でページごと落ちるため、復旧操作は
    工程側だけが持つ（CR-2026-08-06 F-19 の回帰）。
    """
    data_dir = tmp_path / "data"
    _populate_populated_video_detail_state(data_dir)
    line_dir = data_dir / _POPULATED_VIDEO_ID / "shorts" / "line"
    line_dir.mkdir(parents=True, exist_ok=True)
    (line_dir / "line_clip_001.json").write_text(
        "{ broken json", encoding="utf-8"
    )
    (line_dir / "active_line.json").write_text(
        json.dumps({"clip_id": "clip_001"}), encoding="utf-8"
    )

    at = _run_populated_video_detail("shorts")

    assert not list(at.exception)
    evacuate_buttons = [
        widget
        for widget in at.button
        if widget.label == "破損状態を退避して素材選定へ戻る"
    ]
    assert len(evacuate_buttons) == 1
    error_values = [item.value for item in at.error]
    assert any("安全に復元できません" in value for value in error_values)


@pytest.mark.parametrize("workspace", ("materials", "shorts", "publish"))
def test_video_detail_populated_workspace_renders_without_exception(
    workspace: str,
    tmp_path: Path,
) -> None:
    """候補・チャプター・生成済みショートがある状態で各ワークスペースが描画できる."""
    data_dir = tmp_path / "data"
    _populate_populated_video_detail_state(data_dir)

    at = _run_populated_video_detail(workspace)

    assert not list(at.exception)
    assert ":material/movie: テスト動画" in [item.value for item in at.header]
    markdown_values = [item.value for item in at.main.markdown]
    if workspace == "materials":
        caption_values = [item.value for item in at.main.caption]
        assert any("候補を確認し" in value for value in caption_values)
    elif workspace == "shorts":
        subheader_values = [item.value for item in at.subheader]
        assert any("素材を選び" in value for value in subheader_values)
    else:
        assert any("ショートの予約投稿" in value for value in markdown_values)
