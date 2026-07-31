"""YouTube Data API — 概要欄へのチャプター反映."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yt_live_kit.config import Settings

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
MARKER_BEGIN = "▼ タイムライン"
MARKER_END = "▲ タイムラインここまで"
_DESCRIPTION_LIMIT = 5000


class YouTubeAPIError(Exception):
    """YouTube Data API 関連のエラー."""


def is_configured(settings: Settings) -> bool:
    """OAuth クライアントシークレットが配置済みか."""
    return settings.youtube_client_secret.is_file()


def _token_path(settings: Settings) -> Path:
    return settings.data_dir / "_config" / "youtube_token.json"


def _http_error_to_message(exc: Exception) -> str:
    return f"YouTube API の呼び出しに失敗しました。\n（詳細: {exc}）"


def get_credentials(settings: Settings) -> Any:
    """OAuth 認証情報を取得する。未認証の場合はブラウザで同意フローを実行する."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = _token_path(settings)
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds

    if not is_configured(settings):
        raise YouTubeAPIError(
            "OAuth クライアントシークレットが見つかりません"
            f"（{settings.youtube_client_secret}）。"
            "README の「概要欄への反映」節のセットアップ手順に従って配置してください。"
        )

    # 初回のみブラウザが開いて OAuth 同意画面が表示される（ローカルツールのため許容）。
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.youtube_client_secret), SCOPES
    )
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _build_service(settings: Settings) -> Any:
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=get_credentials(settings))


def fetch_video_snippet(video_id: str, settings: Settings) -> dict:
    """動画の snippet（title / description / categoryId 等）を取得する."""
    from googleapiclient.errors import HttpError

    service = _build_service(settings)
    try:
        response = service.videos().list(part="snippet", id=video_id).execute()
    except HttpError as exc:
        raise YouTubeAPIError(_http_error_to_message(exc)) from exc

    items = response.get("items", [])
    if not items:
        raise YouTubeAPIError(
            "動画が見つかりませんでした。自分のチャンネルの動画か確認してください。"
        )
    return items[0]["snippet"]


def merge_chapters_into_description(current: str, chapters_text: str) -> str:
    """概要欄テキストにチャプターブロックをマージする."""
    block = f"{MARKER_BEGIN}\n{chapters_text.strip()}\n{MARKER_END}"

    lines = current.splitlines()
    begin_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if begin_idx is None and line == MARKER_BEGIN:
            begin_idx = i
            continue
        if begin_idx is not None and end_idx is None and line == MARKER_END:
            end_idx = i
            break

    if begin_idx is not None and end_idx is not None:
        merged_lines = lines[:begin_idx] + block.splitlines() + lines[end_idx + 1 :]
        result = "\n".join(merged_lines)
    else:
        stripped = current.rstrip()
        result = block if not stripped else f"{stripped}\n\n{block}"

    if len(result) > _DESCRIPTION_LIMIT:
        raise YouTubeAPIError(
            "概要欄が 5000 文字を超えるため反映できません。"
            "テンプレートやチャプターを短くしてください。"
        )
    return result


def update_video_description(
    video_id: str, new_description: str, settings: Settings
) -> None:
    """概要欄を新しい内容で上書きする（title / categoryId は保持）."""
    from googleapiclient.errors import HttpError

    service = _build_service(settings)
    snippet = fetch_video_snippet(video_id, settings)
    snippet["description"] = new_description

    try:
        service.videos().update(
            part="snippet",
            body={"id": video_id, "snippet": snippet},
        ).execute()
    except HttpError as exc:
        raise YouTubeAPIError(_http_error_to_message(exc)) from exc
