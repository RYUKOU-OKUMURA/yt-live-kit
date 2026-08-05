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

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from yt_live_kit.services.ffmpeg import FfmpegError
from yt_live_kit.services.whisper_runtime import WhisperRuntimeError

_APP_PATH = Path(__file__).parents[1] / "src/yt_live_kit/ui/app.py"
_RUN_TIMEOUT_SECONDS = 30.0


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
    assert "動画詳細" in [item.value for item in at.header]
    assert "ライブラリから動画を選択してください。" in [item.value for item in at.info]
