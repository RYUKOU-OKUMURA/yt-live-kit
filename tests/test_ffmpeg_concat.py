"""ffmpeg cut_and_concat のユニットテスト."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from yt_live_kit.config import Settings
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.ffmpeg import cut_and_concat


def _setup_video_dir(tmp_path: Path, video_id: str = "testvid1234") -> Path:
    video_dir = tmp_path / video_id
    source_dir = video_dir / "clips" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / f"{video_id}.mp4").write_bytes(b"fake video")

    meta = VideoMeta(
        id=video_id,
        title="テスト",
        url="https://www.youtube.com/watch?v=testvid1234",
        upload_date="20260101",
        duration=3600,
        ytdlp_version="2026.7.4",
        fetched_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        subtitle_lang="ja",
    )
    (video_dir / "meta.json").write_text(meta.model_dump_json(), encoding="utf-8")
    return video_dir


def _sample_segments(count: int = 3) -> list[HighlightSegment]:
    segments: list[HighlightSegment] = []
    for i in range(count):
        start_min = i * 5
        segments.append(
            HighlightSegment(
                id=f"hl_{i + 1:03d}",
                title=f"山場 {i + 1}",
                start=f"{start_min:02d}:00",
                end=f"{start_min + 2:02d}:00",
                duration_sec=120,
                reason="テスト",
            )
        )
    return segments


@patch("yt_live_kit.services.ffmpeg.concat_segments")
@patch("yt_live_kit.services.ffmpeg.encode_segment")
@patch("yt_live_kit.services.ffmpeg._probe_video_scale", return_value="scale=1280:720")
@patch("yt_live_kit.services.ffmpeg.ensure_source_video")
def test_cut_and_concat_calls_encode_per_segment(
    mock_ensure,
    mock_scale,
    mock_encode,
    mock_concat,
    tmp_path: Path,
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    source = video_dir / "clips" / "source" / f"{video_id}.mp4"
    mock_ensure.return_value = source

    def fake_encode(source_path, output_path, start_sec, end_sec, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"segment")
        return output_path

    mock_encode.side_effect = fake_encode

    def fake_concat(segment_paths, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"highlight")
        return output_path

    mock_concat.side_effect = fake_concat

    settings = Settings(data_dir=tmp_path)
    segments = _sample_segments(3)

    result = cut_and_concat(video_id, segments, settings)

    assert mock_encode.call_count == 3
    assert result.segment_count == 3
    assert result.total_duration_sec == 360.0
    assert result.output_path.is_file()


@patch("yt_live_kit.services.ffmpeg.concat_segments")
@patch("yt_live_kit.services.ffmpeg.encode_segment")
@patch("yt_live_kit.services.ffmpeg._probe_video_scale", return_value=None)
@patch("yt_live_kit.services.ffmpeg.ensure_source_video")
def test_cut_and_concat_on_progress(
    mock_ensure,
    mock_scale,
    mock_encode,
    mock_concat,
    tmp_path: Path,
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    source = video_dir / "clips" / "source" / f"{video_id}.mp4"
    mock_ensure.return_value = source

    def fake_encode(source_path, output_path, start_sec, end_sec, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"segment")
        return output_path

    mock_encode.side_effect = fake_encode

    def fake_concat(segment_paths, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"highlight")
        return output_path

    mock_concat.side_effect = fake_concat

    progress_calls: list[tuple[int, int, str]] = []

    def on_progress(current: int, total: int, message: str) -> None:
        progress_calls.append((current, total, message))

    settings = Settings(data_dir=tmp_path)
    segments = _sample_segments(2)

    cut_and_concat(video_id, segments, settings, on_progress=on_progress)

    assert progress_calls == [
        (1, 2, "区間 1/2 を処理中…"),
        (2, 2, "区間 2/2 を処理中…"),
    ]


@patch("yt_live_kit.services.ffmpeg.concat_segments")
@patch("yt_live_kit.services.ffmpeg.encode_segment")
@patch("yt_live_kit.services.ffmpeg._probe_video_scale", return_value=None)
@patch("yt_live_kit.services.ffmpeg.ensure_source_video")
def test_cut_and_concat_removes_intermediate_segments(
    mock_ensure,
    mock_scale,
    mock_encode,
    mock_concat,
    tmp_path: Path,
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    source = video_dir / "clips" / "source" / f"{video_id}.mp4"
    mock_ensure.return_value = source
    created_segments: list[Path] = []

    def fake_encode(source_path, output_path, start_sec, end_sec, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"segment")
        created_segments.append(output_path)
        return output_path

    mock_encode.side_effect = fake_encode

    def fake_concat(segment_paths, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"highlight")
        return output_path

    mock_concat.side_effect = fake_concat

    settings = Settings(data_dir=tmp_path)
    cut_and_concat(video_id, _sample_segments(2), settings, keep_segments=False)

    assert created_segments
    for seg_path in created_segments:
        assert not seg_path.exists()


@patch("yt_live_kit.services.ffmpeg.concat_segments")
@patch("yt_live_kit.services.ffmpeg.encode_segment")
@patch("yt_live_kit.services.ffmpeg._probe_video_scale", return_value=None)
@patch("yt_live_kit.services.ffmpeg.ensure_source_video")
def test_cut_and_concat_builds_concat_command(
    mock_ensure,
    mock_scale,
    mock_encode,
    mock_concat,
    tmp_path: Path,
):
    video_id = "testvid1234"
    video_dir = _setup_video_dir(tmp_path, video_id)
    source = video_dir / "clips" / "source" / f"{video_id}.mp4"
    mock_ensure.return_value = source
    captured_segment_paths: list[Path] = []

    def fake_encode(source_path, output_path, start_sec, end_sec, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"segment")
        captured_segment_paths.append(output_path)
        return output_path

    mock_encode.side_effect = fake_encode

    def fake_concat(segs, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"highlight")
        log_path = output_path.parent / "ffmpeg_test.log"
        log_path.write_text(" ".join(["ffmpeg", "-f", "concat"]), encoding="utf-8")
        return output_path

    mock_concat.side_effect = fake_concat

    settings = Settings(data_dir=tmp_path)
    result = cut_and_concat(
        video_id,
        _sample_segments(2),
        settings,
        ffmpeg_path="/usr/bin/ffmpeg",
        keep_segments=True,
    )

    assert len(captured_segment_paths) == 2
    assert all(p.name.startswith("seg_") for p in captured_segment_paths)
    mock_concat.assert_called_once()
    concat_kwargs = mock_concat.call_args.kwargs
    assert concat_kwargs.get("ffmpeg_path") == "/usr/bin/ffmpeg"
    assert result.output_path.name == "highlight.mp4"
