"""設定ページ — チャンネル既定値と実行環境の確認."""

from __future__ import annotations

import streamlit as st

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.ai_prompt import is_codex_available
from yt_live_kit.ui.views._local_settings import (
    get_default_channel_handle,
    save_default_channel_handle,
)


def _save_channel_handle(raw_handle: str, settings: Settings) -> None:
    """入力されたチャンネルハンドルを保存し、結果を日本語で表示する."""
    handle = raw_handle.strip()
    if not handle:
        st.error("チャンネルハンドルを入力してください。")
        return

    try:
        save_default_channel_handle(handle, settings)
    except ValueError as exc:
        st.error(str(exc))
    except (OSError, UnicodeError):
        st.error(
            "チャンネルハンドルを保存できませんでした。"
            "データ保存先の権限を確認して、もう一度お試しください。"
        )
    else:
        st.success("チャンネルハンドルを保存しました。")


def _render_channel_settings(settings: Settings) -> None:
    st.subheader("チャンネル")
    st.caption("取り込みページで最初に表示するチャンネルを設定します。")

    current_handle = get_default_channel_handle(settings) or ""
    with st.form("default_channel_handle_form"):
        handle = st.text_input(
            "既定のチャンネルハンドル",
            value=current_handle,
            placeholder="@channel_name",
            help="YouTube チャンネルのハンドルを入力してください。",
        )
        submitted = st.form_submit_button(
            "保存",
            type="primary",
            icon=":material/save:",
        )

    if submitted:
        _save_channel_handle(handle or "", settings)


def _render_environment_settings(settings: Settings) -> None:
    st.subheader("実行環境")
    st.caption("現在有効な値です。この画面からは変更できません。")

    with st.container(border=True):
        st.markdown("**ffmpeg パス**")
        st.code(str(settings.ffmpeg_path))
        st.markdown("**字幕フォント**")
        st.code(settings.subtitle_font or "未指定（自動検出）")
        st.markdown("**データ保存先**")
        st.code(str(settings.data_dir))

    st.markdown("**変更方法**")
    st.caption(
        "プロジェクト直下の `.env` に必要な項目を記載し、アプリを再起動してください。"
    )
    st.code(
        "YTLK_FFMPEG_PATH=/usr/local/bin/ffmpeg\n"
        "YTLK_SUBTITLE_FONT=Noto Sans CJK JP\n"
        "YTLK_DATA_DIR=./data",
        language="dotenv",
    )


def _render_codex_status() -> None:
    st.subheader("Codex CLI")
    if is_codex_available():
        st.success(
            "Codex CLI は利用可能です。",
            icon=":material/check_circle:",
        )
    else:
        st.warning(
            "Codex CLI が見つかりません。インストール後に `codex login` を実行し、"
            "アプリを再起動してください。",
            icon=":material/warning:",
        )


def _render_schedule_placeholder() -> None:
    st.subheader("投稿スケジュール")
    st.caption("投稿スケジュールの設定はフェーズ P で追加します。")


def render_settings_page() -> None:
    """設定ページを描画する."""
    st.header("設定")
    settings = get_settings()

    _render_channel_settings(settings)
    _render_environment_settings(settings)
    _render_codex_status()
    _render_schedule_placeholder()
