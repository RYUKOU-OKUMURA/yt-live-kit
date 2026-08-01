"""clips サービスのユニットテスト."""

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.services.clips import (
    ClipValidationError,
    build_clips_prompt,
    cut_clip_job_target,
    cut_result_from_ref,
    cut_result_to_ref,
    find_project_root,
    save_candidates_file,
    suggest_clips,
    validate_clip_candidates,
)
from yt_live_kit.services.ffmpeg import CutResult, FfmpegError
from yt_live_kit.services.jobs import create_job, read_job, start_job


def _sample_candidates_json() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "id": "clip_001",
                    "title": "Cursor Agent デモまとめ",
                    "start": "00:03:42",
                    "end": "00:16:30",
                    "duration_sec": 768,
                    "reason": "AI ツール実演が一続きで説明されている区間",
                },
                {
                    "id": "clip_002",
                    "title": "質疑応答ハイライト",
                    "start": "00:25:30",
                    "end": "00:38:00",
                    "duration_sec": 750,
                    "reason": "視聴者質問への回答がまとまっている",
                },
            ]
        },
        ensure_ascii=False,
    )


def test_find_project_root():
    root = find_project_root()
    assert (root / "prompts" / "clips_suggest.md").is_file()


def test_build_clips_prompt_embeds_transcript():
    prompt = build_clips_prompt("[00:00:00] テスト字幕")
    assert "[00:00:00] テスト字幕" in prompt
    assert "{{compressed_transcript}}" not in prompt


def test_validate_clip_candidates_ok():
    data = json.loads(_sample_candidates_json())
    doc, errors = validate_clip_candidates(data)
    assert not errors
    assert len(doc.candidates) == 2


def test_validate_clip_candidates_too_few():
    data = {
        "candidates": [
            {
                "id": "clip_001",
                "title": "単独候補",
                "start": "00:03:42",
                "end": "00:16:30",
                "duration_sec": 768,
                "reason": "テスト",
            }
        ]
    }
    _, errors = validate_clip_candidates(data)
    assert any("2 件以上" in err for err in errors)


def test_validate_clip_candidates_duration_mismatch():
    data = json.loads(_sample_candidates_json())
    data["candidates"][0]["duration_sec"] = 100
    _, errors = validate_clip_candidates(data)
    assert any("duration_sec" in err for err in errors)


def test_validate_clip_candidates_end_exceeds_duration():
    data = json.loads(_sample_candidates_json())
    _, errors = validate_clip_candidates(data, video_duration_sec=600)
    assert any("動画長" in err for err in errors)


def test_save_candidates_file(tmp_path: Path):
    video_id = "test_clips"
    video_dir = tmp_path / video_id
    video_dir.mkdir()
    settings = Settings(data_dir=tmp_path)

    path, doc = save_candidates_file(video_id, _sample_candidates_json(), settings)
    assert path.is_file()
    assert len(doc.candidates) == 2
    assert path.name == "candidates.json"


def test_save_candidates_file_invalid_raises(tmp_path: Path):
    video_id = "test_invalid"
    (tmp_path / video_id).mkdir()
    settings = Settings(data_dir=tmp_path)

    with pytest.raises(ClipValidationError):
        save_candidates_file(video_id, '{"candidates": []}', settings)


def test_suggest_clips_prompt_only(tmp_path: Path):
    video_id = "test_prompt"
    video_dir = tmp_path / video_id
    (video_dir / "transcript").mkdir(parents=True)
    (video_dir / "transcript" / "compressed.txt").write_text(
        "[00:00:00] hello", encoding="utf-8"
    )

    settings = Settings(data_dir=tmp_path)
    result = suggest_clips(video_id, settings, prompt_only=True)
    assert result.prompt_path.is_file()
    assert result.candidates_path is None
    assert result.used_codex is False


def test_cut_result_ref_roundtrip(tmp_path: Path) -> None:
    result = CutResult(
        video_id="vid123",
        output_path=tmp_path / "vid123" / "clips" / "output" / "clip_001.mp4",
        command_log_path=tmp_path / "vid123" / "clips" / "output" / "clip_001.ffmpeg.log",
        start="00:03:42",
        end="00:16:30",
        duration_sec=768,
    )

    restored = cut_result_from_ref(cut_result_to_ref(result))

    assert restored == result


def test_cut_result_from_ref_returns_none_for_invalid_json() -> None:
    assert cut_result_from_ref("not-json") is None
    assert cut_result_from_ref("{}") is None


@patch("yt_live_kit.services.clips.cut_clip")
def test_cut_clip_job_target_calls_cut_clip_and_updates_job(mock_cut, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, ffmpeg_path="/usr/bin/ffmpeg")
    output_path = tmp_path / "vid123" / "clips" / "output" / "clip_001.mp4"
    log_path = tmp_path / "vid123" / "clips" / "output" / "clip_001.ffmpeg.log"
    cut_result = CutResult(
        video_id="vid123",
        output_path=output_path,
        command_log_path=log_path,
        start="00:03:42",
        end="00:16:30",
        duration_sec=768,
    )
    mock_cut.return_value = cut_result
    reports: list[dict[str, object]] = []

    def report(**kwargs: object) -> None:
        reports.append(kwargs)

    create_job("cut_clip", video_id="vid123", settings=settings, requested_job_id="job-cut-1")

    cut_clip_job_target(
        report=report,
        settings=settings,
        video_id="vid123",
        start="00:03:42",
        end="00:16:30",
        candidate_id="clip_001",
        job_id="job-cut-1",
    )

    mock_cut.assert_called_once_with(
        "vid123",
        "00:03:42",
        "00:16:30",
        settings,
        output_name="clip_001.mp4",
        ffmpeg_path="/usr/bin/ffmpeg",
    )
    assert any(item.get("message") == "切り出しを開始しています…" for item in reports)
    assert any(item.get("message") == "切り出しが完了しました" for item in reports)

    job = read_job("job-cut-1", settings)
    assert job is not None
    assert job.result_ref == cut_result_to_ref(cut_result)


@contextmanager
def _patch_real_thread():
    threads: list[threading.Thread] = []
    real = threading.Thread

    def factory(*args, **kwargs):
        thread = real(*args, **kwargs)
        threads.append(thread)
        return thread

    with patch("yt_live_kit.services.jobs.threading.Thread", side_effect=factory):
        yield threads


@patch("yt_live_kit.services.clips.cut_clip")
def test_start_job_reports_ffmpeg_error_for_cut_clip(mock_cut, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    mock_cut.side_effect = FfmpegError("ffmpeg の切り出しに失敗しました。")
    done = threading.Event()

    with _patch_real_thread() as threads:
        job_id = start_job(
            "cut_clip",
            cut_clip_job_target,
            video_id="vid123",
            settings=settings,
            start="00:03:42",
            end="00:16:30",
            candidate_id="clip_001",
        )
        threads[-1].join(timeout=5)

    for _ in range(50):
        state = read_job(job_id, settings)
        if state is not None and state.status == "failed":
            done.set()
            break
        time.sleep(0.05)
    assert done.wait(timeout=5)

    state = read_job(job_id, settings)
    assert state is not None
    assert state.status == "failed"
    assert state.error == "ffmpeg の切り出しに失敗しました。"
