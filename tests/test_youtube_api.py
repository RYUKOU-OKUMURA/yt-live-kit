"""YouTube Data API サービスのテスト."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta, timezone
from http.client import (
    BadStatusLine,
    CannotSendHeader,
    CannotSendRequest,
    IncompleteRead,
    NotConnected,
    ResponseNotReady,
)
from pathlib import Path
import socket
from unittest.mock import MagicMock, call, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from yt_live_kit.services import youtube_api
from yt_live_kit.config import Settings
from yt_live_kit.models.upload import UploadChannel, UploadContentSnapshot


def _http_error(status: int = 403) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error detail")


class TestMergeChaptersIntoDescription:
    def test_appends_block_when_no_markers(self) -> None:
        current = "配信の概要です。\nチャンネル登録はこちら"
        chapters_text = "0:00 イントロ\n1:00 本編"

        result = youtube_api.merge_chapters_into_description(current, chapters_text)

        assert result.startswith("配信の概要です。\nチャンネル登録はこちら\n\n")
        assert youtube_api.MARKER_BEGIN in result
        assert youtube_api.MARKER_END in result
        assert "0:00 イントロ" in result

    def test_empty_current_produces_only_block(self) -> None:
        result = youtube_api.merge_chapters_into_description("", "0:00 イントロ")

        assert result == f"{youtube_api.MARKER_BEGIN}\n0:00 イントロ\n{youtube_api.MARKER_END}"

    def test_whitespace_only_current_produces_only_block(self) -> None:
        result = youtube_api.merge_chapters_into_description("   \n  ", "0:00 イントロ")

        assert result == f"{youtube_api.MARKER_BEGIN}\n0:00 イントロ\n{youtube_api.MARKER_END}"

    def test_replaces_existing_block_and_keeps_surrounding_text(self) -> None:
        current = (
            "本文の前半\n"
            f"{youtube_api.MARKER_BEGIN}\n"
            "0:00 古いチャプター\n"
            f"{youtube_api.MARKER_END}\n"
            "本文の後半"
        )
        chapters_text = "0:00 新しいイントロ\n1:00 本編"

        result = youtube_api.merge_chapters_into_description(current, chapters_text)

        assert result == (
            "本文の前半\n"
            f"{youtube_api.MARKER_BEGIN}\n"
            "0:00 新しいイントロ\n1:00 本編\n"
            f"{youtube_api.MARKER_END}\n"
            "本文の後半"
        )

    def test_missing_end_marker_falls_back_to_append(self) -> None:
        current = f"本文\n{youtube_api.MARKER_BEGIN}\n古い内容だけ"
        chapters_text = "0:00 イントロ"

        result = youtube_api.merge_chapters_into_description(current, chapters_text)

        # マーカーが片方しかないため末尾に追記される
        assert result.count(youtube_api.MARKER_BEGIN) == 2
        assert result.endswith(f"{youtube_api.MARKER_BEGIN}\n0:00 イントロ\n{youtube_api.MARKER_END}")

    def test_raises_when_result_exceeds_limit(self) -> None:
        current = "a" * 4000
        chapters_text = "0:00 イントロ\n" + ("あ" * 2000)

        with pytest.raises(youtube_api.YouTubeAPIError):
            youtube_api.merge_chapters_into_description(current, chapters_text)


class TestIsConfigured:
    def test_true_when_file_exists(self, tmp_path) -> None:
        secret = tmp_path / "client_secret.json"
        secret.write_text("{}", encoding="utf-8")
        settings = Settings(data_dir=tmp_path, youtube_client_secret=secret)

        assert youtube_api.is_configured(settings) is True

    def test_false_when_file_missing(self, tmp_path) -> None:
        settings = Settings(
            data_dir=tmp_path,
            youtube_client_secret=tmp_path / "missing.json",
        )

        assert youtube_api.is_configured(settings) is False


class TestSaveToken:
    def test_save_token_restricts_file_and_directory_permissions(self, tmp_path: Path) -> None:
        token_path = tmp_path / "_config" / "youtube_token.json"
        settings = Settings(data_dir=tmp_path)

        youtube_api._save_token(settings, '{"token": "secret"}')

        assert token_path.read_text(encoding="utf-8") == '{"token": "secret"}'
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700

    def test_save_token_does_not_change_existing_client_secret_mode(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "_config"
        config_dir.mkdir()
        secret_path = config_dir / "client_secret.json"
        secret_path.write_text("{}", encoding="utf-8")
        os.chmod(secret_path, 0o644)

        token_path = config_dir / "youtube_token.json"
        youtube_api._save_token(Settings(data_dir=tmp_path), '{"token": "secret"}')

        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o644
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


class TestGetCredentialsTokenPermissions:
    def test_refresh_path_saves_token_with_restricted_permissions(
        self, tmp_path: Path
    ) -> None:
        settings = Settings(data_dir=tmp_path)
        token_path = tmp_path / "_config" / "youtube_token.json"
        token_path.parent.mkdir(parents=True)
        token_path.write_text("{}", encoding="utf-8")

        creds = MagicMock()
        creds.valid = False
        creds.expired = True
        creds.refresh_token = "refresh"
        creds.to_json.return_value = '{"refreshed": true}'

        with (
            patch(
                "google.oauth2.credentials.Credentials.from_authorized_user_file",
                return_value=creds,
            ),
            patch("google.auth.transport.requests.Request"),
        ):
            result = youtube_api.get_credentials(settings)

        assert result is creds
        assert token_path.read_text(encoding="utf-8") == '{"refreshed": true}'
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700

    def test_initial_flow_saves_token_with_restricted_permissions(
        self, tmp_path: Path
    ) -> None:
        secret = tmp_path / "_config" / "client_secret.json"
        secret.parent.mkdir(parents=True)
        secret.write_text("{}", encoding="utf-8")
        settings = Settings(
            data_dir=tmp_path,
            youtube_client_secret=secret,
        )
        token_path = tmp_path / "_config" / "youtube_token.json"

        creds = MagicMock()
        creds.to_json.return_value = '{"initial": true}'
        flow = MagicMock()
        flow.run_local_server.return_value = creds

        with patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            return_value=flow,
        ):
            result = youtube_api.get_credentials(settings)

        assert result is creds
        assert token_path.read_text(encoding="utf-8") == '{"initial": true}'
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700


class TestFetchVideoSnippet:
    def test_returns_snippet_of_first_item(self) -> None:
        settings = MagicMock()
        service = MagicMock()
        service.videos().list().execute.return_value = {
            "items": [{"snippet": {"title": "t", "description": "d", "categoryId": "22"}}]
        }

        with patch(
            "yt_live_kit.services.youtube_api._build_service", return_value=service
        ):
            snippet = youtube_api.fetch_video_snippet("vid123", settings)

        assert snippet == {"title": "t", "description": "d", "categoryId": "22"}

    def test_raises_when_no_items(self) -> None:
        settings = MagicMock()
        service = MagicMock()
        service.videos().list().execute.return_value = {"items": []}

        with patch(
            "yt_live_kit.services.youtube_api._build_service", return_value=service
        ):
            with pytest.raises(youtube_api.YouTubeAPIError, match="動画が見つかりませんでした"):
                youtube_api.fetch_video_snippet("vid123", settings)

    def test_wraps_http_error(self) -> None:
        settings = MagicMock()
        service = MagicMock()
        service.videos().list().execute.side_effect = _http_error()

        with patch(
            "yt_live_kit.services.youtube_api._build_service", return_value=service
        ):
            with pytest.raises(youtube_api.YouTubeAPIError, match="YouTube API の呼び出しに失敗"):
                youtube_api.fetch_video_snippet("vid123", settings)


class TestUpdateVideoDescription:
    def test_preserves_title_and_category_id(self) -> None:
        settings = MagicMock()
        service = MagicMock()
        service.videos().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "title": "元のタイトル",
                        "description": "元の概要",
                        "categoryId": "22",
                    }
                }
            ]
        }

        with patch(
            "yt_live_kit.services.youtube_api._build_service", return_value=service
        ):
            youtube_api.update_video_description("vid123", "新しい概要", settings)

        update_call = service.videos().update.call_args
        body = update_call.kwargs["body"]
        assert body["id"] == "vid123"
        assert body["snippet"]["title"] == "元のタイトル"
        assert body["snippet"]["categoryId"] == "22"
        assert body["snippet"]["description"] == "新しい概要"

    def test_wraps_http_error_on_update(self) -> None:
        settings = MagicMock()
        service = MagicMock()
        service.videos().list().execute.return_value = {
            "items": [{"snippet": {"title": "t", "description": "d", "categoryId": "22"}}]
        }
        service.videos().update().execute.side_effect = _http_error()

        with patch(
            "yt_live_kit.services.youtube_api._build_service", return_value=service
        ):
            with pytest.raises(youtube_api.YouTubeAPIError, match="YouTube API の呼び出しに失敗"):
                youtube_api.update_video_description("vid123", "新しい概要", settings)


def _snapshot(tmp_path: Path, *, now: datetime) -> UploadContentSnapshot:
    video = (tmp_path / "short.mp4").resolve()
    video.write_bytes(b"video")
    stat = video.stat()
    return UploadContentSnapshot(
        channel=UploadChannel(channel_id="UC1", title="チャンネル"),
        video_path=video,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
        duration_sec=30,
        title="  タイトル  ".strip(),
        description="説明",
        tags=("tag",),
        publish_at=now + timedelta(minutes=20),
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=False,
        contains_synthetic_media=True,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=now,
    )


class TestUploadPreflight:
    def test_builds_canonical_snapshot_and_validates_file(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        video.write_bytes(b"video")
        settings = Settings(data_dir=tmp_path)
        with patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0):
            snapshot = youtube_api.build_upload_snapshot(
                channel=UploadChannel(channel_id="UC1", title="チャンネル"),
                video_path=video,
                title="  タイトル  ",
                description="説明",
                tags=[" tag1 ", "tag2"],
                publish_at=now + timedelta(minutes=10),
                self_declared_made_for_kids=False,
                contains_synthetic_media=True,
                community_guidelines_confirmed=True,
                community_guidelines_confirmed_at=now,
                settings=settings,
                now=now,
            )
        assert snapshot.video_path.is_absolute()
        assert snapshot.title == "タイトル"
        assert snapshot.tags == ("tag1", "tag2")
        assert snapshot.duration_sec == 30

    def test_preflight_uses_sibling_ffprobe_for_ffmpeg_full_path(
        self, tmp_path: Path
    ) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        video.write_bytes(b"video")
        settings = Settings(
            data_dir=tmp_path,
            ffmpeg_path="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        )
        with patch(
            "yt_live_kit.services.youtube_api.probe_duration", return_value=30
        ) as probe:
            youtube_api.build_upload_snapshot(
                channel=UploadChannel(channel_id="UC1", title="チャンネル"),
                video_path=video, title="タイトル", description="説明", tags=["tag"],
                publish_at=now + timedelta(minutes=10),
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                community_guidelines_confirmed_at=now,
                settings=settings, now=now,
            )
        assert probe.call_args.kwargs["ffprobe_path"] == (
            "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
        )

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"privacy_status": "public"}, "非公開"),
            ({"privacy_status": "unlisted"}, "非公開"),
            ({"notify_subscribers": True}, "オフ固定"),
            ({"self_declared_made_for_kids": None}, "子ども向け"),
            ({"contains_synthetic_media": None}, "合成メディア"),
            ({"community_guidelines_confirmed": False}, "Guidelines"),
            ({"publish_at": datetime(2026, 8, 1)}, "タイムゾーン"),
            ({"title": "<危険>"}, "山カッコ"),
            ({"description": "あ" * 1667}, "5000 bytes"),
            ({"tags": [""]}, "空"),
        ],
    )
    def test_rejects_unsafe_boundaries(self, tmp_path: Path, overrides, message) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        video.write_bytes(b"video")
        values = dict(
            channel=UploadChannel(channel_id="UC1", title="チャンネル"),
            video_path=video, title="タイトル", description="説明", tags=["tag"],
            publish_at=now + timedelta(minutes=20),
            self_declared_made_for_kids=False, contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            community_guidelines_confirmed_at=now,
            settings=Settings(data_dir=tmp_path), now=now,
        )
        values.update(overrides)
        with patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0):
            with pytest.raises(youtube_api.YouTubeAPIError, match=message):
                youtube_api.build_upload_snapshot(**values)

    @pytest.mark.parametrize(
        ("duration", "accepted"),
        [(9.0, False), (10.0, True), (180.0, True), (181.0, False)],
    )
    def test_duration_boundaries(
        self, tmp_path: Path, duration: float, accepted: bool
    ) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        video.write_bytes(b"video")
        kwargs = dict(
            channel=UploadChannel(channel_id="UC1", title="チャンネル"),
            video_path=video,
            title="タイトル",
            description="説明",
            tags=["tag"],
            publish_at=now + timedelta(minutes=10),
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            community_guidelines_confirmed_at=now,
            settings=Settings(data_dir=tmp_path),
            now=now,
        )
        with patch(
            "yt_live_kit.services.youtube_api.probe_duration", return_value=duration
        ):
            if accepted:
                assert youtube_api.build_upload_snapshot(**kwargs).duration_sec == duration
            else:
                with pytest.raises(youtube_api.YouTubeAPIError, match="尺"):
                    youtube_api.build_upload_snapshot(**kwargs)

    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            ("missing", "見つかりません"),
            ("suffix", "mp4"),
            ("directory", "通常"),
            ("empty", "空"),
        ],
    )
    def test_file_boundaries(self, tmp_path: Path, kind: str, message: str) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        if kind == "suffix":
            video = tmp_path / "short.mov"
            video.write_bytes(b"video")
        elif kind == "directory":
            video.mkdir()
        elif kind == "empty":
            video.write_bytes(b"")
        kwargs = dict(
            channel=UploadChannel(channel_id="UC1", title="チャンネル"),
            video_path=video,
            title="タイトル",
            description="説明",
            tags=["tag"],
            publish_at=now + timedelta(minutes=20),
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            community_guidelines_confirmed_at=now,
            settings=Settings(data_dir=tmp_path),
            now=now,
        )
        with patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30):
            with pytest.raises(youtube_api.YouTubeAPIError, match=message):
                youtube_api.build_upload_snapshot(**kwargs)

    @pytest.mark.parametrize(
        ("field", "value", "accepted"),
        [
            ("title", "a", True),
            ("title", "a" * 100, True),
            ("title", " ", False),
            ("title", "a" * 101, False),
            ("description", "a" * 5000, True),
            ("description", "a" * 5001, False),
            ("tags", ["a" * 500], True),
            ("tags", ["a" * 501], False),
            ("tags", ["   "], False),
            ("title", "危険<", False),
            ("description", "危険>", False),
            ("tags", ["<危険>"], False),
        ],
    )
    def test_metadata_boundaries(
        self, tmp_path: Path, field: str, value, accepted: bool
    ) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        video.write_bytes(b"video")
        kwargs = dict(
            channel=UploadChannel(channel_id="UC1", title="チャンネル"),
            video_path=video,
            title="タイトル",
            description="説明",
            tags=["tag"],
            publish_at=now + timedelta(minutes=20),
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            community_guidelines_confirmed_at=now,
            settings=Settings(data_dir=tmp_path),
            now=now,
        )
        kwargs[field] = value
        with patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30):
            if accepted:
                youtube_api.build_upload_snapshot(**kwargs)
            else:
                with pytest.raises(youtube_api.YouTubeAPIError):
                    youtube_api.build_upload_snapshot(**kwargs)

    def test_publish_at_none_and_lead_boundary(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        video = tmp_path / "short.mp4"
        video.write_bytes(b"video")
        base = dict(
            channel=UploadChannel(channel_id="UC1", title="チャンネル"),
            video_path=video, title="タイトル", description="説明", tags=["tag"],
            self_declared_made_for_kids=False, contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            community_guidelines_confirmed_at=now,
            settings=Settings(data_dir=tmp_path), now=now,
        )
        with patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30):
            with pytest.raises(youtube_api.YouTubeAPIError, match="タイムゾーン"):
                youtube_api.build_upload_snapshot(**base, publish_at=None)
            with pytest.raises(youtube_api.YouTubeAPIError, match="10 分"):
                youtube_api.build_upload_snapshot(
                    **base, publish_at=now + timedelta(minutes=9, seconds=59)
                )
            youtube_api.build_upload_snapshot(
                **base, publish_at=now + timedelta(minutes=10)
            )


def test_fetch_mine_channel_uses_mine_and_requires_one_channel() -> None:
    service = MagicMock()
    channels = service.channels.return_value
    request = channels.list.return_value
    request.execute.return_value = {
        "items": [{"id": "UC1", "snippet": {"title": "実チャンネル"}}]
    }
    with patch("yt_live_kit.services.youtube_api._build_service", return_value=service):
        result = youtube_api.fetch_mine_channel(MagicMock())
    assert result.channel_id == "UC1"
    channels.list.assert_called_once_with(part="snippet", mine=True)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [
            {"id": "UC1", "snippet": {"title": "一"}},
            {"id": "UC2", "snippet": {"title": "二"}},
        ],
        [{"snippet": {"title": "名称"}}],
        [{"id": "UC1", "snippet": {}}],
        [{"id": "UC1"}],
    ],
)
def test_fetch_mine_channel_rejects_count_and_missing_fields(items) -> None:
    service = MagicMock()
    service.channels.return_value.list.return_value.execute.return_value = {
        "items": items
    }
    with patch("yt_live_kit.services.youtube_api._build_service", return_value=service):
        with pytest.raises(youtube_api.YouTubeAPIError):
            youtube_api.fetch_mine_channel(MagicMock())


def test_upload_body_is_private_utc_z_and_contains_required_booleans(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    body = youtube_api.build_upload_body(_snapshot(tmp_path, now=now), now=now)
    assert body["status"] == {
        "privacyStatus": "private",
        "publishAt": "2026-08-01T00:20:00Z",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    assert "community" not in str(body).lower()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"title": "  タイトル  "}, "canonical"),
        ({"title": "<危険>"}, "山カッコ"),
        ({"description": "a" * 5001}, "5000 bytes"),
        ({"tags": (" tag ",)}, "canonical"),
        ({"tags": ("",)}, "空"),
        ({"tags": ("a" * 501,)}, "500"),
    ],
)
def test_build_upload_body_revalidates_tampered_snapshot(
    tmp_path: Path, updates: dict[str, object], message: str
) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    tampered = _snapshot(tmp_path, now=now).model_copy(update=updates)
    with pytest.raises(youtube_api.YouTubeAPIError, match=message):
        youtube_api.build_upload_body(tampered, now=now)


def test_resumable_upload_retries_same_request_and_never_reinserts(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    request = MagicMock()
    request.next_chunk.side_effect = [TimeoutError("network"), (MagicMock(), None), (None, {"id": "yt1"})]
    service.videos().insert.return_value = request
    sleep = MagicMock()
    with patch("googleapiclient.http.MediaFileUpload") as media:
        result = youtube_api.upload_video_resumable(
            _snapshot(tmp_path, now=now), Settings(data_dir=tmp_path),
            service=service, sleep_fn=sleep, now=now,
        )
    assert result.state == "uploaded"
    assert result.video_id == "yt1"
    assert service.videos().insert.call_count == 1
    insert_kwargs = service.videos().insert.call_args.kwargs
    assert insert_kwargs["part"] == "snippet,status"
    assert insert_kwargs["notifySubscribers"] is False
    assert insert_kwargs["body"]["status"]["privacyStatus"] == "private"
    media.assert_called_once_with(str((tmp_path / "short.mp4").resolve()), mimetype="video/mp4", resumable=True)
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("status", [400, 403, 500, 502, 503, 504])
def test_upload_error_never_creates_second_insert(tmp_path: Path, status: int) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    request = MagicMock()
    request.next_chunk.side_effect = _http_error(status)
    service.videos().insert.return_value = request
    sleep = MagicMock()
    with patch("googleapiclient.http.MediaFileUpload"):
        result = youtube_api.upload_video_resumable(
            _snapshot(tmp_path, now=now), Settings(data_dir=tmp_path),
            service=service, sleep_fn=sleep, now=now,
        )
    assert result.state == "needs_reconciliation"
    assert service.videos().insert.call_count == 1
    expected_retries = 5 if status in {500, 502, 503, 504} else 0
    assert sleep.call_count == expected_retries
    if status in {500, 502, 503, 504}:
        assert sleep.call_args_list == [call(1), call(2), call(4), call(8), call(16)]


@pytest.mark.parametrize(
    "exception",
    [
        socket.timeout("timeout"),
        ConnectionResetError("reset"),
        ConnectionAbortedError("aborted"),
        ConnectionRefusedError("refused"),
        BrokenPipeError("broken"),
        NotConnected(),
        IncompleteRead(b"", 1),
        CannotSendRequest(),
        CannotSendHeader(),
        ResponseNotReady(),
        BadStatusLine("bad"),
        HttpLib2Error("http"),
    ],
)
def test_expected_network_errors_retry_same_request(
    tmp_path: Path, exception: Exception
) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    request = MagicMock()
    request.next_chunk.side_effect = [exception, (None, {"id": "yt1"})]
    service.videos().insert.return_value = request
    sleep = MagicMock()
    with patch("googleapiclient.http.MediaFileUpload"):
        result = youtube_api.upload_video_resumable(
            _snapshot(tmp_path, now=now), Settings(data_dir=tmp_path),
            service=service, sleep_fn=sleep, now=now,
        )
    assert result.state == "uploaded"
    assert service.videos().insert.call_count == 1
    sleep.assert_called_once_with(1)


def test_non_network_oserror_does_not_retry(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    request = MagicMock()
    request.next_chunk.side_effect = PermissionError("permission")
    service.videos().insert.return_value = request
    sleep = MagicMock()
    with patch("googleapiclient.http.MediaFileUpload"):
        result = youtube_api.upload_video_resumable(
            _snapshot(tmp_path, now=now), Settings(data_dir=tmp_path),
            service=service, sleep_fn=sleep, now=now,
        )
    assert result.state == "needs_reconciliation"
    sleep.assert_not_called()


@pytest.mark.parametrize("response", [[], {}, {"id": ""}, {"wrong": "value"}])
def test_missing_response_or_video_id_never_reinserts(
    tmp_path: Path, response
) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    service.videos().insert.return_value.next_chunk.return_value = (None, response)
    with patch("googleapiclient.http.MediaFileUpload"):
        result = youtube_api.upload_video_resumable(
            _snapshot(tmp_path, now=now), Settings(data_dir=tmp_path),
            service=service, sleep_fn=MagicMock(), now=now,
        )
    assert result.state == "needs_reconciliation"
    assert service.videos().insert.call_count == 1


def test_fully_missing_chunk_response_never_loops_or_reinserts(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    request = service.videos().insert.return_value
    request.next_chunk.return_value = (None, None)
    with patch("googleapiclient.http.MediaFileUpload"):
        result = youtube_api.upload_video_resumable(
            _snapshot(tmp_path, now=now), Settings(data_dir=tmp_path),
            service=service, sleep_fn=MagicMock(), now=now,
        )
    assert result.state == "needs_reconciliation"
    assert request.next_chunk.call_count == 1
    assert service.videos().insert.call_count == 1


def test_processing_poll_has_fixed_interval_limit_and_typed_history(tmp_path: Path) -> None:
    service = MagicMock()
    service.videos().list().execute.return_value = {
        "items": [{"status": {"privacyStatus": "private"}, "processingDetails": {"processingStatus": "processing"}}]
    }
    sleep = MagicMock()
    times = iter(datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(seconds=10 * i) for i in range(30))
    history = youtube_api.poll_processing_status(
        "yt1", Settings(data_dir=tmp_path), service=service,
        sleep_fn=sleep, clock=lambda: next(times),
    )
    assert len(history) == 30
    assert history[-1].classification == "processing_timeout"
    assert history[-1].error
    assert all(item.phase == "processing" for item in history)
    assert sleep.call_args_list == [call(10)] * 29


def test_private_lock_decision_table() -> None:
    publish = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    scheduled = youtube_api.classify_publication_status(
        status={"privacyStatus": "private", "publishAt": "2026-08-01T01:00:00Z"},
        processing_details={"processingStatus": "succeeded"}, publish_at=publish,
        observed_at=publish - timedelta(minutes=1), processing_succeeded=True,
    )
    missing = youtube_api.classify_publication_status(
        status={"privacyStatus": "private"}, processing_details={"processingStatus": "succeeded"},
        publish_at=publish, observed_at=publish, processing_succeeded=True,
    )
    late = youtube_api.classify_publication_status(
        status={"privacyStatus": "private", "publishAt": "2026-08-01T01:00:00Z"},
        processing_details={"processingStatus": "succeeded"}, publish_at=publish,
        observed_at=publish + timedelta(minutes=6), processing_succeeded=False,
    )
    public = youtube_api.classify_publication_status(
        status={"privacyStatus": "public"}, processing_details={"processingStatus": "succeeded"},
        publish_at=publish, observed_at=publish, processing_succeeded=True,
    )
    assert scheduled[0] == "scheduled"
    assert missing[0] == "suspected_private_lock"
    assert late[0] == "suspected_private_lock"
    assert public[0] == "published"


def test_publication_poll_has_fixed_interval_limit_and_preserves_responses(tmp_path: Path) -> None:
    publish = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    service.videos().list().execute.return_value = {
        "items": [{
            "status": {"privacyStatus": "private", "publishAt": "2026-08-01T00:00:00Z"},
            "processingDetails": {"processingStatus": "succeeded", "processingProgress": {"partsTotal": "10"}},
        }]
    }
    sleep = MagicMock()
    history = youtube_api.poll_publication_status(
        "yt1", publish, Settings(data_dir=tmp_path), service=service,
        processing_succeeded=False, sleep_fn=sleep, clock=lambda: publish,
    )
    assert len(history) == 20
    assert history[-1].classification == "publication_timeout"
    assert history[0].status["privacyStatus"] == "private"
    assert history[0].processing_details["processingProgress"]["partsTotal"] == "10"
    assert sleep.call_args_list == [call(30)] * 19


def test_publication_poll_rejects_start_before_publish_without_api(tmp_path: Path) -> None:
    publish = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    service = MagicMock()
    with pytest.raises(youtube_api.YouTubeAPIError, match="予約時刻"):
        youtube_api.poll_publication_status(
            "yt1", publish, Settings(data_dir=tmp_path), service=service,
            sleep_fn=MagicMock(),
            clock=lambda: publish - timedelta(seconds=1),
        )
    service.videos.assert_not_called()


def test_publication_poll_rejects_naive_clock(tmp_path: Path) -> None:
    publish = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with pytest.raises(youtube_api.YouTubeAPIError, match="タイムゾーン"):
        youtube_api.poll_publication_status(
            "yt1", publish, Settings(data_dir=tmp_path), service=MagicMock(),
            sleep_fn=MagicMock(), clock=lambda: datetime(2026, 8, 1),
        )


@pytest.mark.parametrize(
    ("status", "observed_at"),
    [
        ({"privacyStatus": "private"}, datetime(2026, 8, 1, tzinfo=timezone.utc)),
        (
            {"privacyStatus": "private", "publishAt": "2026-08-01T00:00:00Z"},
            datetime(2026, 8, 1, 0, 6, tzinfo=timezone.utc),
        ),
    ],
)
def test_publication_poll_detects_private_lock(
    tmp_path: Path, status, observed_at: datetime
) -> None:
    publish = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    service.videos().list().execute.return_value = {
        "items": [{
            "status": status,
            "processingDetails": {"processingStatus": "succeeded"},
        }]
    }
    history = youtube_api.poll_publication_status(
        "yt1", publish, Settings(data_dir=tmp_path), service=service,
        sleep_fn=MagicMock(), clock=lambda: observed_at,
    )
    assert history[0].classification == "suspected_private_lock"


@pytest.mark.parametrize(
    ("status", "details", "classification"),
    [
        ({"privacyStatus": "public"}, {"processingStatus": "succeeded"}, "published"),
        ({"privacyStatus": "private"}, {"processingStatus": "failed"}, "processing_failed"),
        ({"privacyStatus": "private"}, {"processingStatus": "terminated"}, "processing_failed"),
    ],
)
def test_publication_poll_stops_on_terminal(
    tmp_path: Path, status, details, classification: str
) -> None:
    publish = datetime(2026, 8, 1, tzinfo=timezone.utc)
    service = MagicMock()
    service.videos().list().execute.return_value = {
        "items": [{"status": status, "processingDetails": details}]
    }
    sleep = MagicMock()
    history = youtube_api.poll_publication_status(
        "yt1", publish, Settings(data_dir=tmp_path), service=service,
        sleep_fn=sleep, clock=lambda: publish,
    )
    assert len(history) == 1
    assert history[0].classification == classification
    sleep.assert_not_called()


def test_processing_poll_stops_on_each_terminal(tmp_path: Path) -> None:
    for processing_status, expected in [
        ("succeeded", "processing_succeeded"),
        ("failed", "processing_failed"),
        ("terminated", "processing_failed"),
    ]:
        service = MagicMock()
        service.videos().list().execute.return_value = {
            "items": [{"status": {}, "processingDetails": {"processingStatus": processing_status}}]
        }
        sleep = MagicMock()
        history = youtube_api.poll_processing_status(
            "yt1", Settings(data_dir=tmp_path), service=service,
            sleep_fn=sleep, clock=lambda: datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        assert len(history) == 1
        assert history[0].classification == expected
        sleep.assert_not_called()


@pytest.mark.parametrize(
    "exception",
    [
        TimeoutError("timeout"),
        ConnectionError("connection"),
        socket.gaierror("name resolution"),
        HttpLib2Error("http transport"),
    ],
)
def test_fetch_upload_status_wraps_network_errors_in_japanese(
    exception: Exception,
) -> None:
    service = MagicMock()
    service.videos().list().execute.side_effect = exception
    with pytest.raises(
        youtube_api.YouTubeAPIError,
        match="ネットワーク.*手動確認",
    ):
        youtube_api._fetch_upload_status(service, "yt1")


def test_fetch_upload_status_does_not_reclassify_local_oserror() -> None:
    service = MagicMock()
    service.videos().list().execute.side_effect = PermissionError("local file")
    with pytest.raises(PermissionError, match="local file"):
        youtube_api._fetch_upload_status(service, "yt1")
