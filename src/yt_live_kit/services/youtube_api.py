"""YouTube Data API — 概要欄へのチャプター反映."""

from __future__ import annotations

import os
import socket
import time
from http.client import (
    BadStatusLine,
    CannotSendHeader,
    CannotSendRequest,
    IncompleteRead,
    NotConnected,
    ResponseNotReady,
)
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    PollClassification,
    UploadChannel,
    UploadContentSnapshot,
    UploadResult,
    UploadStatusObservation,
)
from yt_live_kit.services._fsutil import advisory_lock, write_text_atomically
from yt_live_kit.services._paths import PathConfinementError, confined_path
from yt_live_kit.services.ffmpeg import FfmpegError, ffprobe_path_for, probe_duration

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
MARKER_BEGIN = "▼ タイムライン"
MARKER_END = "▲ タイムラインここまで"
_DESCRIPTION_LIMIT = 5000
_UPLOAD_DESCRIPTION_BYTE_LIMIT = 5000
_UPLOAD_TAGS_LIMIT = 500
_MIN_UPLOAD_DURATION = 10.0
_MAX_UPLOAD_DURATION = 180.0
_MIN_PUBLISH_LEAD = timedelta(minutes=10)
_RETRYABLE_HTTP_STATUSES = frozenset({500, 502, 503, 504})
_RETRY_DELAYS = (1, 2, 4, 8, 16)
_HTTP_TIMEOUT_SEC = 60


class YouTubeAPIError(Exception):
    """YouTube Data API 関連のエラー."""


def is_configured(settings: Settings) -> bool:
    """OAuth クライアントシークレットが配置済みか."""
    # 存在確認も _config の解決先を検査してから行う。標準の secret path が
    # 外部 symlink を経由する構成で、外部ファイルを読んだことにしない。
    try:
        confined_path(settings.data_dir, "_config", label="OAuth 設定保存先")
    except PathConfinementError as exc:
        raise YouTubeAPIError(str(exc)) from exc
    return settings.youtube_client_secret.is_file()


def _token_path(settings: Settings) -> Path:
    try:
        return confined_path(
            settings.data_dir,
            "_config",
            "youtube_token.json",
            label="OAuth トークン保存先",
        )
    except PathConfinementError as exc:
        raise YouTubeAPIError(str(exc)) from exc


def _save_token(settings: Settings, creds_json: str) -> None:
    """OAuth トークン JSON を保存し、ファイル・親ディレクトリの権限を制限する."""
    token_path = _token_path(settings)

    lock_name = f".{token_path.name}.lock"
    try:
        lock_path = confined_path(
            settings.data_dir,
            "_config",
            lock_name,
            label="OAuth トークンロック保存先",
        )
    except PathConfinementError as exc:
        raise YouTubeAPIError(str(exc)) from exc

    token_path.parent.mkdir(parents=True, exist_ok=True)
    with advisory_lock(lock_path):
        # 既存ファイル（client_secret.json 等）のモードは変えず、ディレクトリの一覧権限のみ制限する。
        os.chmod(token_path.parent, 0o700)
        write_text_atomically(token_path, creds_json, mode=0o600)


def _http_error_to_message(exc: Exception) -> str:
    return f"YouTube API の呼び出しに失敗しました。\n（詳細: {exc}）"


def get_credentials(settings: Settings) -> Any:
    """OAuth 認証情報を取得する。未認証・refresh 失効時はブラウザで同意フローを実行する."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = _token_path(settings)
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # refresh token が失効している（例: OAuth 同意画面が「テスト」公開
                # ステータスで 7 日失効）。下の再同意フローへフォールスルーする。
                pass
            else:
                _save_token(settings, creds.to_json())
                return creds

    if not is_configured(settings):
        raise YouTubeAPIError(
            "OAuth クライアントシークレットが見つかりません"
            f"（{settings.youtube_client_secret}）。"
            "認証情報が失効して再同意が必要な場合も、再同意にはこのファイルが必要です。"
            "README の「概要欄への反映」節のセットアップ手順に従って配置してください。"
        )

    # 初回のみブラウザが開いて OAuth 同意画面が表示される（ローカルツールのため許容）。
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.youtube_client_secret), SCOPES
    )
    creds = flow.run_local_server(port=0)
    _save_token(settings, creds.to_json())
    return creds


def _build_service(settings: Settings) -> Any:
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    http = AuthorizedHttp(
        get_credentials(settings),
        http=httplib2.Http(timeout=_HTTP_TIMEOUT_SEC),
    )
    return build("youtube", "v3", http=http)


def _require_aware(value: datetime | None, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise YouTubeAPIError(f"{label}はタイムゾーン付き日時で指定してください。")
    return value.astimezone(timezone.utc)


def _rfc3339_z(value: datetime) -> str:
    return _require_aware(value, "公開予定日時").isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _validate_metadata(title: str, description: str, tags: tuple[str, ...]) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(title, str):
        raise YouTubeAPIError("タイトルは文字列で入力してください。")
    if not isinstance(description, str):
        raise YouTubeAPIError("説明文は文字列で入力してください。")
    if not isinstance(tags, tuple) or any(not isinstance(tag, str) for tag in tags):
        raise YouTubeAPIError("タグは文字列の一覧で入力してください。")
    canonical_title = title.strip()
    canonical_tags = tuple(tag.strip() for tag in tags)
    if not canonical_title:
        raise YouTubeAPIError("タイトルを入力してください。")
    if len(canonical_title) > 100:
        raise YouTubeAPIError("タイトルは 100 文字以内にしてください。")
    if len(description.encode("utf-8")) > _UPLOAD_DESCRIPTION_BYTE_LIMIT:
        raise YouTubeAPIError("説明文は UTF-8 で 5000 bytes 以内にしてください。")
    if any(not tag for tag in canonical_tags):
        raise YouTubeAPIError("タグに空の項目は使えません。")
    if len(",".join(canonical_tags)) > _UPLOAD_TAGS_LIMIT:
        raise YouTubeAPIError("タグはカンマ区切りで 500 文字以内にしてください。")
    values = (canonical_title, description, *canonical_tags)
    if any("<" in value or ">" in value for value in values):
        raise YouTubeAPIError("投稿内容に半角の山カッコは使えません。")
    return canonical_title, description, canonical_tags


def build_upload_snapshot(
    *,
    channel: UploadChannel,
    video_path: Path,
    title: str,
    description: str,
    tags: tuple[str, ...] | list[str],
    publish_at: datetime,
    self_declared_made_for_kids: bool,
    contains_synthetic_media: bool,
    community_guidelines_confirmed: bool,
    community_guidelines_confirmed_at: datetime,
    settings: Settings,
    privacy_status: str = "private",
    notify_subscribers: bool = False,
    now: datetime | None = None,
) -> UploadContentSnapshot:
    """ファイル・metadata・公開契約を検証し canonical snapshot を作る."""
    if privacy_status != "private":
        raise YouTubeAPIError("予約投稿は非公開設定のみ利用できます。")
    if notify_subscribers is not False:
        raise YouTubeAPIError("予約投稿の通知設定はオフ固定です。")
    if type(self_declared_made_for_kids) is not bool:
        raise YouTubeAPIError("子ども向けかどうかを「はい」または「いいえ」で選択してください。")
    if type(contains_synthetic_media) is not bool:
        raise YouTubeAPIError("合成メディアを含むか「はい」または「いいえ」で選択してください。")
    if community_guidelines_confirmed is not True:
        raise YouTubeAPIError("YouTube Community Guidelines への準拠を確認してください。")

    reference = _require_aware(now or datetime.now(timezone.utc), "現在日時")
    publish_utc = _require_aware(publish_at, "公開予定日時")
    if publish_utc < reference + _MIN_PUBLISH_LEAD:
        raise YouTubeAPIError("公開予定日時は現在から 10 分以上先にしてください。")
    confirmed_at = _require_aware(
        community_guidelines_confirmed_at, "ガイドライン確認日時"
    )

    path = video_path.expanduser().resolve()
    if path.suffix.lower() != ".mp4":
        raise YouTubeAPIError("投稿する動画は mp4 ファイルを指定してください。")
    if not path.exists():
        raise YouTubeAPIError(f"投稿する動画ファイルが見つかりません: {path}")
    if not path.is_file():
        raise YouTubeAPIError("投稿対象は通常の動画ファイルを指定してください。")
    stat = path.stat()
    if stat.st_size <= 0:
        raise YouTubeAPIError("空の動画ファイルは投稿できません。")
    if isinstance(tags, (str, bytes)):
        raise YouTubeAPIError("タグは文字列の一覧で入力してください。")
    try:
        duration = probe_duration(
            path,
            ffprobe_path=ffprobe_path_for(settings.ffmpeg_path),
        )
    except FfmpegError as exc:
        raise YouTubeAPIError(str(exc)) from exc
    if not _MIN_UPLOAD_DURATION <= duration <= _MAX_UPLOAD_DURATION:
        raise YouTubeAPIError("投稿する動画の尺は 10 秒以上 180 秒以下にしてください。")
    try:
        raw_tags = tuple(tags)
    except TypeError as exc:
        raise YouTubeAPIError("タグは文字列の一覧で入力してください。") from exc
    canonical_title, canonical_description, canonical_tags = _validate_metadata(
        title, description, raw_tags
    )
    return UploadContentSnapshot(
        channel=channel,
        video_path=path,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
        duration_sec=duration,
        title=canonical_title,
        description=canonical_description,
        tags=canonical_tags,
        publish_at=publish_utc,
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=self_declared_made_for_kids,
        contains_synthetic_media=contains_synthetic_media,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=confirmed_at,
    )


# 呼び出し側で用途が明瞭になる別名。
preflight_upload = build_upload_snapshot


def validate_snapshot_identity(
    snapshot: UploadContentSnapshot, settings: Settings
) -> None:
    """確定後、API session 前にファイルと尺が変わっていないか再検証する."""
    path = snapshot.video_path
    if not path.is_file():
        raise YouTubeAPIError("確認後に動画ファイルが見つからなくなりました。新しく確認してください。")
    try:
        stat = path.stat()
    except OSError as exc:
        raise YouTubeAPIError(
            "確認後の動画ファイル情報を取得できませんでした。新しく確認してください。"
        ) from exc
    if stat.st_size != snapshot.file_size or stat.st_mtime_ns != snapshot.file_mtime_ns:
        raise YouTubeAPIError("確認後に動画ファイルが変更されました。新しく確認してください。")
    try:
        duration = probe_duration(
            path,
            ffprobe_path=ffprobe_path_for(settings.ffmpeg_path),
        )
    except FfmpegError as exc:
        raise YouTubeAPIError(str(exc)) from exc
    if abs(duration - snapshot.duration_sec) > 0.001:
        raise YouTubeAPIError("確認後に動画の尺が変更されました。新しく確認してください。")


def fetch_mine_channel(settings: Settings) -> UploadChannel:
    """OAuth 認証中の唯一のチャンネルを取得する."""
    from googleapiclient.errors import HttpError

    service = _build_service(settings)
    try:
        response = service.channels().list(part="snippet", mine=True).execute()
    except HttpError as exc:
        raise YouTubeAPIError(_http_error_to_message(exc)) from exc
    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list) or not items:
        raise YouTubeAPIError("認証中の YouTube チャンネルを取得できませんでした。")
    if len(items) != 1:
        raise YouTubeAPIError("認証結果に複数の YouTube チャンネルが含まれています。")
    item = items[0]
    channel_id = item.get("id") if isinstance(item, dict) else None
    snippet = item.get("snippet") if isinstance(item, dict) else None
    title = snippet.get("title") if isinstance(snippet, dict) else None
    if not isinstance(channel_id, str) or not channel_id.strip() or not isinstance(title, str) or not title.strip():
        raise YouTubeAPIError("YouTube チャンネル情報に ID または名称がありません。")
    return UploadChannel(channel_id=channel_id.strip(), title=title.strip())


def build_upload_body(snapshot: UploadContentSnapshot, *, now: datetime | None = None) -> dict[str, Any]:
    """scheduled-only 契約を再検証して videos.insert body を作る."""
    reference = _require_aware(now or datetime.now(timezone.utc), "現在日時")
    if snapshot.privacy_status != "private" or snapshot.notify_subscribers is not False:
        raise YouTubeAPIError("予約投稿の公開・通知設定が安全契約と一致しません。")
    if snapshot.community_guidelines_confirmed is not True:
        raise YouTubeAPIError("YouTube Community Guidelines への準拠を確認してください。")
    if snapshot.publish_at < reference + _MIN_PUBLISH_LEAD:
        raise YouTubeAPIError("公開予定日時は現在から 10 分以上先にしてください。")
    canonical_title, canonical_description, canonical_tags = _validate_metadata(
        snapshot.title,
        snapshot.description,
        snapshot.tags,
    )
    if (
        canonical_title != snapshot.title
        or canonical_description != snapshot.description
        or canonical_tags != snapshot.tags
    ):
        raise YouTubeAPIError(
            "保存された投稿内容が canonical 形式ではありません。新しく確認してください。"
        )
    return {
        "snippet": {
            "title": snapshot.title,
            "description": snapshot.description,
            "tags": list(snapshot.tags),
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": _rfc3339_z(snapshot.publish_at),
            "selfDeclaredMadeForKids": snapshot.self_declared_made_for_kids,
            "containsSyntheticMedia": snapshot.contains_synthetic_media,
        },
    }


def _is_retryable(exc: BaseException) -> bool:
    try:
        from googleapiclient.errors import HttpError
    except ImportError:  # pragma: no cover - dependency is part of the project
        HttpError = ()
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None) in _RETRYABLE_HTTP_STATUSES
    from httplib2 import HttpLib2Error

    network_errors = (
        socket.timeout,
        socket.gaierror,
        socket.herror,
        TimeoutError,
        ConnectionError,
        ConnectionResetError,
        ConnectionAbortedError,
        ConnectionRefusedError,
        BrokenPipeError,
        NotConnected,
        IncompleteRead,
        CannotSendRequest,
        CannotSendHeader,
        ResponseNotReady,
        BadStatusLine,
        HttpLib2Error,
    )
    return isinstance(exc, network_errors)


def upload_video_resumable(
    snapshot: UploadContentSnapshot,
    settings: Settings,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
    service: Any | None = None,
) -> UploadResult:
    """単一 resumable request の next_chunk だけを限定再試行する."""
    from googleapiclient.http import MediaFileUpload

    try:
        body = build_upload_body(snapshot, now=now)
        media = MediaFileUpload(str(snapshot.video_path), mimetype="video/mp4", resumable=True)
        youtube = service or _build_service(settings)
        request = youtube.videos().insert(
            part="snippet,status",
            notifySubscribers=False,
            body=body,
            media_body=media,
        )
    except YouTubeAPIError:
        raise
    except Exception as exc:
        return UploadResult(
            state="needs_reconciliation",
            video_id=None,
            error=f"アップロード session の結果を確認できません。手動で照合してください。詳細: {exc}",
        )

    retry_index = 0
    while True:
        try:
            chunk_status, response = request.next_chunk()
            retry_index = 0
        except Exception as exc:
            if _is_retryable(exc) and retry_index < len(_RETRY_DELAYS):
                sleep_fn(_RETRY_DELAYS[retry_index])
                retry_index += 1
                continue
            return UploadResult(
                state="needs_reconciliation",
                video_id=None,
                error="アップロード結果を確定できません。新しく送信せず YouTube Studio で手動照合してください。"
                f"詳細: {exc}",
            )
        if response is None:
            if chunk_status is None:
                return UploadResult(
                    state="needs_reconciliation",
                    video_id=None,
                    error="YouTube の upload 応答が欠落しています。新しく送信せず手動照合してください。",
                )
            continue
        if not isinstance(response, dict) or not isinstance(response.get("id"), str) or not response["id"].strip():
            return UploadResult(
                state="needs_reconciliation",
                video_id=None,
                error="YouTube の応答に動画 ID がありません。新しく送信せず手動照合してください。",
            )
        return UploadResult(
            state="uploaded", video_id=response["id"].strip(), error=None
        )


def _fetch_upload_status(service: Any, video_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from googleapiclient.errors import HttpError

    try:
        response = service.videos().list(
            part="status,processingDetails", id=video_id
        ).execute()
    except HttpError as exc:
        raise YouTubeAPIError(_http_error_to_message(exc)) from exc
    except Exception as exc:
        if _is_retryable(exc):
            raise YouTubeAPIError(
                "YouTube の動画処理状態をネットワーク経由で取得できませんでした。"
                "通信状態を確認し、YouTube Studio で手動確認してください。"
            ) from exc
        raise
    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise YouTubeAPIError("アップロードした動画の処理状態を取得できませんでした。")
    item = items[0]
    status = item.get("status", {})
    details = item.get("processingDetails", {})
    if not isinstance(status, dict) or not isinstance(details, dict):
        raise YouTubeAPIError("YouTube の処理状態応答が正しくありません。")
    return status, details


def poll_processing_status(
    video_id: str,
    settings: Settings,
    *,
    service: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_observation: Callable[[UploadStatusObservation], None] | None = None,
) -> tuple[UploadStatusObservation, ...]:
    """processing を 10 秒間隔、最大 30 回 poll する."""
    youtube = service or _build_service(settings)
    observations: list[UploadStatusObservation] = []
    for index in range(30):
        status, details = _fetch_upload_status(youtube, video_id)
        processing = details.get("processingStatus")
        classification: PollClassification
        if processing == "succeeded":
            classification = "processing_succeeded"
        elif processing in {"failed", "terminated"}:
            classification = "processing_failed"
        elif index == 29:
            classification = "processing_timeout"
        else:
            classification = "processing"
        if classification == "processing_failed":
            error = "YouTube 側の動画処理に失敗しました。"
        elif classification == "processing_timeout":
            error = "YouTube 側の動画処理を時間内に確認できませんでした。"
        else:
            error = None
        observation = UploadStatusObservation(
            polled_at=_require_aware(clock(), "取得日時"), phase="processing", status=status,
            processing_details=details, classification=classification, error=error,
        )
        observations.append(observation)
        if on_observation:
            on_observation(observation)
        if classification != "processing":
            break
        sleep_fn(10)
    return tuple(observations)


def classify_publication_status(
    *, status: dict[str, Any], processing_details: dict[str, Any],
    publish_at: datetime, observed_at: datetime, processing_succeeded: bool,
) -> tuple[PollClassification, str | None]:
    """private lock の具体的判定表を適用する."""
    privacy = status.get("privacyStatus")
    processing = processing_details.get("processingStatus")
    observed = _require_aware(observed_at, "取得日時")
    expected = _require_aware(publish_at, "公開予定日時")
    if privacy == "public":
        return "published", None
    if processing in {"failed", "terminated"}:
        return "processing_failed", "YouTube 側の動画処理に失敗しました。"
    if processing_succeeded and not status.get("publishAt"):
        return "suspected_private_lock", "公開予定日時が YouTube の応答から欠落しています。"
    if privacy == "private" and observed < expected and status.get("publishAt") == _rfc3339_z(expected):
        return "scheduled", None
    if privacy == "private" and observed > expected + timedelta(minutes=5):
        return "suspected_private_lock", "予約時刻を 5 分過ぎても非公開のため private lock の可能性があります。"
    return "scheduled", None


def poll_publication_status(
    video_id: str, publish_at: datetime, settings: Settings, *,
    processing_succeeded: bool = True, service: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_observation: Callable[[UploadStatusObservation], None] | None = None,
) -> tuple[UploadStatusObservation, ...]:
    """予約時刻到来後、公開状態を 30 秒間隔、最大 20 回 poll する."""
    expected_publish = _require_aware(publish_at, "公開予定日時")
    first_observed_at = _require_aware(clock(), "取得日時")
    if first_observed_at < expected_publish:
        raise YouTubeAPIError("公開状態の確認は予約時刻の到来後に開始してください。")
    youtube = service or _build_service(settings)
    observations: list[UploadStatusObservation] = []
    for index in range(20):
        status, details = _fetch_upload_status(youtube, video_id)
        observed_at = (
            first_observed_at
            if index == 0
            else _require_aware(clock(), "取得日時")
        )
        classification, error = classify_publication_status(
            status=status, processing_details=details, publish_at=publish_at,
            observed_at=observed_at, processing_succeeded=processing_succeeded,
        )
        if index == 19 and classification == "scheduled":
            classification = "publication_timeout"
            error = "公開状態の確認が時間内に完了しませんでした。"
        observation = UploadStatusObservation(
            polled_at=observed_at, phase="publication", status=status,
            processing_details=details, classification=classification, error=error,
        )
        observations.append(observation)
        if on_observation:
            on_observation(observation)
        if classification in {"published", "processing_failed", "suspected_private_lock", "publication_timeout"}:
            break
        sleep_fn(30)
    return tuple(observations)


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
