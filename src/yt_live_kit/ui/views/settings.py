"""設定ページ — チャンネル既定値と実行環境の確認."""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from yt_live_kit.config import WHISPER_ADOPTED_CONTRACT, Settings, get_settings
from yt_live_kit.services.ai_prompt import is_codex_available
from yt_live_kit.services.ffmpeg import FfmpegError, diagnose_ffmpeg
from yt_live_kit.services.schedule import (
    ScheduleError,
    SchedulePolicyNotConfigured,
    load_schedule_policy,
    make_schedule_policy,
    save_schedule_policy,
)
from yt_live_kit.services.subtitle_burn import TELOP_PRESETS
from yt_live_kit.services.upload_queue import UploadQueueError, count_upload_attempts
from yt_live_kit.services.whisper_runtime import (
    WhisperRuntimeError,
    WhisperSettingsContract,
    preflight_whisper_runtime,
)
from yt_live_kit.ui.components.storage_manager import render_storage_manager
from yt_live_kit.ui.views import _local_settings
from yt_live_kit.ui.views._local_settings import (
    get_default_channel_handle,
    save_default_channel_handle,
)

_SHORTS_LAYOUT_OPTIONS = ("blur", "crop")


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
        st.markdown("**ffmpeg 設定値**")
        st.code(str(settings.ffmpeg_path))
        try:
            diagnostics = diagnose_ffmpeg(settings.ffmpeg_path)
        except FfmpegError as exc:
            st.markdown("**ffmpeg 解決済みパス**")
            st.code("取得できません")
            st.markdown("**ffmpeg バージョン**")
            st.code("取得できません")
            st.markdown("**subtitles フィルタ**")
            st.code("利用可否を確認できません")
            st.warning(
                f"FFmpeg の診断に失敗しました。{exc}"
                " macOS で字幕付きショートを作る場合は ffmpeg-full を導入し、"
                ".env の YTLK_FFMPEG_PATH にその実体パスを設定してください。",
                icon=":material/warning:",
            )
        else:
            st.markdown("**ffmpeg 解決済みパス**")
            st.code(diagnostics.resolved_path)
            st.markdown("**ffmpeg バージョン**")
            st.code(diagnostics.version)
            st.markdown("**subtitles フィルタ**")
            if diagnostics.subtitles_available:
                st.success("利用できます。", icon=":material/check_circle:")
            else:
                st.warning(
                    "利用できません。macOS では ffmpeg-full を導入し、"
                    ".env の YTLK_FFMPEG_PATH にその実体パスを設定してください。",
                    icon=":material/warning:",
                )
        st.markdown("**字幕フォント**")
        st.code(settings.subtitle_font or "未指定（自動検出）")
        st.markdown("**データ保存先**")
        st.code(str(settings.data_dir))

    st.markdown("**変更方法**")
    st.caption(
        "プロジェクト直下の `.env` に必要な項目を記載し、アプリを再起動してください。"
    )
    st.code(
        "YTLK_FFMPEG_PATH=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg\n"
        "YTLK_SUBTITLE_FONT=Noto Sans CJK JP\n"
        "YTLK_DATA_DIR=./data",
        language="dotenv",
    )


def _render_whisper_settings(settings: Settings) -> None:
    """S9 の固定 runtime / model contract を読み取り専用で表示する."""
    st.subheader("高精度字幕 runtime")
    st.caption(
        "固定採用された runtime と model の現在値です。この画面から path・shell・"
        "model の変更や自動ダウンロードはできません。"
    )

    contract = WhisperSettingsContract.from_settings(settings)
    capability = None
    preflight_error: WhisperRuntimeError | None = None
    try:
        capability = preflight_whisper_runtime(settings)
    except WhisperRuntimeError as exc:
        preflight_error = exc

    if preflight_error is not None:
        st.warning(
            "固定 runtime の事前検査に失敗しました。高精度字幕は実行せず、"
            f"既存 VTT を維持します。{preflight_error}"
        )

    capability_status = (
        "利用可能"
        if capability is not None and capability.json_timestamp_capability
        else "利用不可（実体未検証）"
    )
    model_name = (
        capability.model_name
        if capability is not None
        else WHISPER_ADOPTED_CONTRACT.model_name
    )
    model_fingerprint = (
        capability.model_sha256
        if capability is not None
        else f"{WHISPER_ADOPTED_CONTRACT.model_sha256}（実体未検証）"
    )
    runtime_fingerprint = (
        capability.runtime_fingerprint if capability is not None else "未確認"
    )
    binary_path = (
        capability.binary_path
        if capability is not None
        else settings.whisper_binary_path
    )
    model_path = (
        capability.model_path
        if capability is not None
        else settings.whisper_model_path
    )
    cache_path = settings.data_dir / "{video_id}" / "transcripts" / "audio_cache"
    try:
        cache_status = (
            "保存先を利用可能（区間単位で検証）"
            if settings.data_dir.is_dir()
            else "未作成（高精度化の実行時に作成）"
        )
    except OSError:
        cache_status = "保存先を確認できません"

    with st.container(border=True):
        st.markdown("**runtime capability（読み取り専用）**")
        st.code(capability_status)
        st.markdown("**固定選択 model**")
        st.code(model_name)
        st.markdown("**language**")
        st.code(contract.language)
        st.markdown("**model fingerprint**")
        st.code(model_fingerprint)
        st.markdown("**runtime fingerprint**")
        st.code(runtime_fingerprint)
        st.markdown("**timeout**")
        st.code(f"{contract.timeout_sec} 秒")
        st.markdown("**whisper-cli path（固定 location）**")
        st.code(binary_path)
        st.markdown("**model path（固定 location）**")
        st.code(model_path)
        st.markdown("**cache 場所**")
        st.code(str(cache_path))
        st.markdown("**cache 状態**")
        st.code(cache_status)


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


def _save_schedule_policy(
    daily_times: Sequence[str] | str,
    interval_days: int,
    timezone_name: str,
    settings: Settings,
) -> None:
    try:
        values = (
            daily_times.splitlines()
            if isinstance(daily_times, str)
            else list(daily_times)
        )
    except TypeError:
        st.error("投稿時刻を1行1件の一覧で入力してください。")
        return

    try:
        policy = make_schedule_policy(
            daily_times=values,
            interval_days=interval_days,
            timezone_name=timezone_name,
        )
        save_schedule_policy(policy, settings)
    except ScheduleError as exc:
        st.error(str(exc))
    else:
        st.success("投稿スケジュールを保存しました。")


def _save_shorts_line_defaults(
    layout: str,
    preset: str,
    hook_preset: str,
    settings: Settings,
) -> None:
    """ショート生産ラインの既定値を検証して保存する."""
    preset_options = tuple(TELOP_PRESETS)
    if layout not in _SHORTS_LAYOUT_OPTIONS:
        st.error("ショート生産ラインのレイアウトが不正です。blur または crop を選択してください。")
        return
    if preset not in preset_options or hook_preset not in preset_options:
        st.error(
            "テロッププリセットが不正です。画面に表示されたプリセットを選択してください。"
        )
        return

    save_defaults = getattr(_local_settings, "save_shorts_line_defaults", None)
    if not callable(save_defaults):
        st.error(
            "ショート生産ラインの保存機能を利用できません。アプリを更新してからお試しください。"
        )
        return

    defaults = _local_settings.ShortsLineDefaults(
        layout=layout,
        preset=preset,
        hook_preset=hook_preset,
    )
    try:
        save_defaults(defaults, settings)
    except ValueError:
        st.error(
            "ショート生産ラインの既定値が不正なため保存できませんでした。"
            "レイアウトとプリセットを選び直してください。"
        )
    except (OSError, UnicodeError):
        st.error(
            "ショート生産ラインの既定値を保存できませんでした。"
            "データ保存先の権限を確認して、もう一度お試しください。"
        )
    else:
        st.success("ショート生産ラインの既定値を保存しました。")


def _render_shorts_line_settings(settings: Settings) -> None:
    """ショート生産ラインのレイアウト・プリセット既定値を編集する."""
    st.subheader("ショート生産ライン")
    st.caption("ショート作成時に毎回選ばず適用するレイアウトとテロッププリセットです。")

    try:
        defaults = _local_settings.load_shorts_line_defaults(settings)
    except (OSError, UnicodeError, ValueError, TypeError):
        st.error(
            "ショート生産ラインの既定値を読み込めませんでした。"
            "設定ファイルを確認して、もう一度お試しください。"
        )
        return

    preset_options = tuple(TELOP_PRESETS)
    if not preset_options:
        st.error("利用可能なテロッププリセットがありません。アプリを更新してください。")
        return
    if (
        defaults.layout not in _SHORTS_LAYOUT_OPTIONS
        or defaults.preset not in preset_options
        or defaults.hook_preset not in preset_options
    ):
        st.error(
            "ショート生産ラインの既定値が不正です。設定ファイルを確認してください。"
        )
        return

    with st.form("shorts_line_defaults_form"):
        layout = st.selectbox(
            "レイアウト",
            options=_SHORTS_LAYOUT_OPTIONS,
            index=_SHORTS_LAYOUT_OPTIONS.index(defaults.layout),
            help="blur は背景ぼかし、crop は中央クロップで縦型に配置します。",
        )
        preset = st.selectbox(
            "通常テロッププリセット",
            options=preset_options,
            index=preset_options.index(defaults.preset),
        )
        hook_preset = st.selectbox(
            "Hook テロッププリセット",
            options=preset_options,
            index=preset_options.index(defaults.hook_preset),
        )
        submitted = st.form_submit_button(
            "ショート生産ラインの既定値を保存",
            type="primary",
            icon=":material/save:",
        )

    if submitted:
        _save_shorts_line_defaults(
            layout or "",
            preset or "",
            hook_preset or "",
            settings,
        )


def _render_schedule_placeholder(settings: Settings) -> None:
    """投稿 policy と read-only upload attempt 情報を表示する."""
    st.subheader("投稿スケジュール")
    st.caption("生成済みショートを次の空き枠へ予約するときの設定です。")
    try:
        policy = load_schedule_policy(settings)
    except SchedulePolicyNotConfigured:
        policy = None
    except ScheduleError as exc:
        st.error(str(exc))
        return
    try:
        attempts = count_upload_attempts(settings)
    except UploadQueueError as exc:
        st.error(str(exc))
        return

    with st.form("schedule_policy_form"):
        daily_times = st.text_area(
            "投稿時刻（1行1件、HH:MM）",
            value="" if policy is None else "\n".join(sorted(policy.daily_times)),
            help="半角数字の24時間表記を1行に1件ずつ入力してください。",
        )
        interval_days = st.number_input(
            "投稿間隔（日）",
            min_value=1,
            step=1,
            value=1 if policy is None else policy.interval_days,
        )
        timezone_name = st.text_input(
            "IANA timezone",
            value="Asia/Tokyo" if policy is None else policy.timezone,
            help="例: Asia/Tokyo",
        )
        submitted = st.form_submit_button(
            "投稿スケジュールを保存",
            type="primary",
            icon=":material/save:",
        )
    if submitted:
        _save_schedule_policy(
            (daily_times or "").splitlines(),
            int(interval_days),
            timezone_name or "",
            settings,
        )

    with st.container(border=True):
        st.markdown("**YouTube upload 試行上限（読み取り専用）**")
        st.write(f"America/Los_Angeles 当日: {attempts} / {settings.video_upload_daily_limit}")
        st.caption("上限は環境変数 YTLK_VIDEO_UPLOAD_DAILY_LIMIT で設定します。")


def render_settings_page() -> None:
    """設定ページを描画する."""
    st.header("設定")
    settings = get_settings()

    _render_channel_settings(settings)
    _render_environment_settings(settings)
    _render_whisper_settings(settings)
    _render_codex_status()
    render_storage_manager(settings)
    _render_schedule_placeholder(settings)
    _render_shorts_line_settings(settings)
