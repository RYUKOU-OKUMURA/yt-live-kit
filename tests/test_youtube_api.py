"""YouTube Data API サービスのテスト."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from yt_live_kit.services import youtube_api


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
        settings = MagicMock()
        settings.youtube_client_secret = secret

        assert youtube_api.is_configured(settings) is True

    def test_false_when_file_missing(self, tmp_path) -> None:
        settings = MagicMock()
        settings.youtube_client_secret = tmp_path / "missing.json"

        assert youtube_api.is_configured(settings) is False


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
