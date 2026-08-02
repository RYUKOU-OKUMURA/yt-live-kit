"""ショート量産サービスのユニットテスト."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.shorts import ShortResult, ShortsError
from yt_live_kit.services.telop import make_clip_id
from yt_live_kit.services.shorts_queue import (
    SCHEMA_VERSION,
    ShortsQueueClipSpec,
    ShortsQueueError,
    ShortsQueueItemResult,
    ShortsQueueResult,
    build_shorts_queue_targets,
    can_start_shorts_queue,
    load_latest_shorts_queue_result,
    load_shorts_queue_result,
    make_shorts_queue_clip_spec,
    make_shorts_queue_fingerprint,
    normalize_queue_candidates,
    run_shorts_queue,
    run_shorts_queue_job_target,
    select_queue_candidates_by_id,
)


def _clip(index: int, start: int, end: int) -> ClipCandidate:
    return ClipCandidate(
        id=f"clip_{index:03d}",
        title=f"候補 {index}",
        start=f"0:00:{start:02d}",
        end=f"0:00:{end:02d}",
        duration_sec=end - start,
        reason=f"理由 {index}",
    )


def _highlight(index: int, start: int, end: int) -> HighlightSegment:
    return HighlightSegment(
        id=f"hl_{index:03d}",
        title=f"候補 {index}",
        start=f"0:00:{start:02d}",
        end=f"0:00:{end:02d}",
        duration_sec=end - start,
        reason=f"理由 {index}",
    )


def _document(target) -> TelopScriptDocument:
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


def _specs(count: int = 2) -> tuple[ShortsQueueClipSpec, ...]:
    candidates = [_clip(i, (i - 1) * 10, i * 10) for i in range(1, count + 1)]
    segments = normalize_queue_candidates(candidates, source="clips")
    targets = build_shorts_queue_targets(segments, mode="individual")
    return tuple(
        make_shorts_queue_clip_spec(
            target,
            _document(target),
            layout="blur",
            preset="default",
            hook_preset="hook",
        )
        for target in targets
    )


def _spec_payload_for_duration(duration_ms: int) -> dict[str, object]:
    target_id = make_clip_id([(0.0, duration_ms / 1000)])
    return {
        "target_id": target_id,
        "segments": [
            {
                "id": "segment",
                "title": "境界テスト",
                "start_ms": 0,
                "end_ms": duration_ms,
                "reason": "境界値",
            }
        ],
        "telop_document": {
            "hook_text": "重要ポイント",
            "title_candidates": ["タイトル"],
            "description": "説明文",
            "tags": ["タグ"],
            "segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": duration_ms / 1000,
                    "lines": [
                        {
                            "text": "本文",
                            "start_sec": 0.0,
                            "end_sec": duration_ms / 1000,
                            "emphasis": False,
                        }
                    ],
                }
            ],
        },
        "layout": "blur",
        "preset": "default",
        "hook_preset": "hook",
        "output_name": f"short_{target_id}.mp4",
    }


def test_normalize_candidates_preserves_display_order_and_rejects_mixed_source():
    candidates = [_clip(2, 10, 20), _clip(1, 0, 10)]
    normalized = normalize_queue_candidates(candidates, source="clips")
    assert [item.id for item in normalized] == ["clip_002", "clip_001"]
    with pytest.raises(ShortsQueueError, match="混在"):
        normalize_queue_candidates([candidates[0], _highlight(1, 0, 10)], source="clips")


def test_select_current_candidates_detects_source_changes_and_preserves_snapshot_order():
    first = _clip(1, 0, 10)
    second = _clip(2, 10, 20)
    assert select_queue_candidates_by_id([second, first], ["clip_001", "clip_002"]) == (
        second,
        first,
    )
    with pytest.raises(ShortsQueueError, match="変更"):
        select_queue_candidates_by_id([first], ["clip_002"])


@pytest.mark.parametrize("duration_ms", [10_000, 180_000])
def test_spec_from_dict_accepts_duration_boundaries(duration_ms):
    spec = ShortsQueueClipSpec.from_dict(_spec_payload_for_duration(duration_ms))
    assert spec.segments[0].end_ms == duration_ms


@pytest.mark.parametrize("duration_ms", [9_999, 180_001])
def test_spec_from_dict_rejects_duration_outside_boundaries(duration_ms):
    with pytest.raises(ShortsQueueError):
        ShortsQueueClipSpec.from_dict(_spec_payload_for_duration(duration_ms))


def test_target_builder_individual_and_concat_are_deterministic():
    segments = normalize_queue_candidates(
        [_clip(1, 0, 10), _clip(2, 10, 20)], source="clips"
    )
    individual = build_shorts_queue_targets(segments, mode="individual")
    concatenated = build_shorts_queue_targets(segments, mode="concat")
    assert len(individual) == 2
    assert len(concatenated) == 1
    assert concatenated[0].segments == segments
    assert concatenated[0].output_name == f"short_{concatenated[0].target_id}.mp4"


@pytest.mark.parametrize(
    ("candidate", "message"),
    [(_clip(1, 0, 9), "10 秒"), (_clip(1, 0, 59).model_copy(update={"end": "0:03:01", "duration_sec": 181}), "180 秒")],
)
def test_target_builder_validates_duration_before_external_work(candidate, message):
    segments = normalize_queue_candidates([candidate], source="clips")
    with pytest.raises(ShortsQueueError, match=message):
        build_shorts_queue_targets(segments, mode="individual")


def test_target_builder_rejects_duplicate_clip_id_without_suffix():
    candidate = _clip(1, 0, 10)
    segments = normalize_queue_candidates([candidate, candidate], source="clips")
    with pytest.raises(ShortsQueueError, match="同じ clip ID"):
        build_shorts_queue_targets(segments, mode="individual")


def test_fingerprint_covers_video_candidate_and_configuration():
    candidates = [_clip(1, 0, 10)]
    segments = normalize_queue_candidates(candidates, source="clips")
    base = dict(
        video_id="video-a",
        source="clips",
        mode="individual",
        original_candidates=candidates,
        segments=segments,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    fingerprint = make_shorts_queue_fingerprint(**base)
    assert fingerprint != make_shorts_queue_fingerprint(**{**base, "video_id": "video-b"})
    assert fingerprint != make_shorts_queue_fingerprint(**{**base, "layout": "crop"})
    changed = [_clip(1, 0, 10).model_copy(update={"reason": "変更"})]
    assert fingerprint != make_shorts_queue_fingerprint(
        **{**base, "original_candidates": changed}
    )


def test_can_start_requires_matching_fingerprint_all_confirmed_and_idle():
    spec = _specs(1)[0]
    confirmed = {spec.target_id: spec}
    assert can_start_shorts_queue(
        expected_fingerprint="same",
        current_fingerprint="same",
        target_ids=[spec.target_id],
        confirmed_specs=confirmed,
        busy=False,
    ) == (True, None)
    assert not can_start_shorts_queue(
        expected_fingerprint="old",
        current_fingerprint="new",
        target_ids=[spec.target_id],
        confirmed_specs=confirmed,
        busy=False,
    )[0]
    assert not can_start_shorts_queue(
        expected_fingerprint="same",
        current_fingerprint="same",
        target_ids=[spec.target_id],
        confirmed_specs={},
        busy=False,
    )[0]
    assert not can_start_shorts_queue(
        expected_fingerprint="same",
        current_fingerprint="same",
        target_ids=[spec.target_id],
        confirmed_specs=confirmed,
        busy=True,
    )[0]


def test_can_start_accepts_confirmations_completed_out_of_display_order():
    first, second = _specs(2)
    confirmed = {second.target_id: second, first.target_id: first}
    assert can_start_shorts_queue(
        expected_fingerprint="same",
        current_fingerprint="same",
        target_ids=[first.target_id, second.target_id],
        confirmed_specs=confirmed,
        busy=False,
    ) == (True, None)


def test_spec_is_deep_immutable_canonical_and_strict():
    spec = _specs(1)[0]
    payload = spec.to_dict()
    restored = ShortsQueueClipSpec.from_dict(payload)
    assert restored == spec
    assert restored.segments == tuple(restored.segments)
    assert restored.telop_document_json == json.dumps(
        restored.telop_document.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(FrozenInstanceError):
        restored.output_name = "changed.mp4"  # type: ignore[misc]
    payload["output_name"] = "short_changed.mp4"
    with pytest.raises(ShortsQueueError, match="一致"):
        ShortsQueueClipSpec.from_dict(payload)


def test_result_round_trip_omits_manifest_path_and_loader_injects_it(tmp_path: Path):
    spec = _specs(1)[0]
    now = datetime.now(timezone.utc)
    path = tmp_path / "queue_job.json"
    result = ShortsQueueResult(
        video_id="video",
        job_id="job",
        status="running",
        created_at=now,
        updated_at=now,
        clip_specs=(spec,),
        items=(),
        success_count=0,
        failure_count=0,
        manifest_path=path,
    )
    payload = result.to_dict()
    assert "manifest_path" not in payload
    restored = ShortsQueueResult.from_dict(payload, manifest_path=path)
    assert restored.manifest_path == path
    payload["manifest_path"] = "/forged"
    with pytest.raises(ShortsQueueError, match="未対応"):
        ShortsQueueResult.from_dict(payload, manifest_path=path)


def test_run_queue_softfails_per_item_updates_manifest_and_progress(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    specs = _specs(2)
    output = tmp_path / "video" / "shorts" / "output"
    progress = MagicMock()
    success = ShortResult(
        video_id="video",
        output_path=output / specs[1].output_name,
        command_log_path=output / "second.ffmpeg.log",
        layout="blur",
        burned_subtitles=True,
        duration_sec=10.0,
        font_warning="フォント警告",
    )
    with patch(
        "yt_live_kit.services.shorts_queue.build_short_from_segments",
        side_effect=[ShortsError("1 本目失敗"), success],
    ) as build:
        result = run_shorts_queue(
            "video", specs, settings, job_id="job-1", on_progress=progress
        )

    assert build.call_count == 2
    assert result.status == "done"
    assert result.success_count == 1
    assert result.failure_count == 1
    assert [item.status for item in result.items] == ["failed", "succeeded"]
    loaded = load_shorts_queue_result("video", "job-1", settings)
    assert loaded is not None
    assert loaded.items == result.items
    assert progress.call_args_list[-2:] == [
        call(1, 2, f"{specs[0].target_id}: 失敗"),
        call(2, 2, f"{specs[1].target_id}: 完了"),
    ]


def test_run_queue_all_failed_still_finishes(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    specs = _specs(2)
    with patch(
        "yt_live_kit.services.shorts_queue.build_short_from_segments",
        side_effect=ShortsError("生成失敗"),
    ):
        result = run_shorts_queue("video", specs, settings, job_id="all-failed")
    assert result.status == "done"
    assert result.failure_count == 2


def test_run_queue_propagates_all_confirmed_inputs_to_s3(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, ffmpeg_path="fake-ffmpeg")
    spec = _specs(1)[0]
    output = tmp_path / "out.mp4"
    success = ShortResult(
        video_id="video",
        output_path=output,
        command_log_path=tmp_path / "out.log",
        layout=spec.layout,
        burned_subtitles=True,
        duration_sec=10.0,
    )
    with patch(
        "yt_live_kit.services.shorts_queue.build_short_from_segments",
        return_value=success,
    ) as build:
        run_shorts_queue("video", [spec], settings, job_id="job")
    kwargs = build.call_args.kwargs
    assert kwargs["layout"] == spec.layout
    assert kwargs["preset"] == spec.preset
    assert kwargs["hook_preset"] == spec.hook_preset
    assert kwargs["output_name"] == spec.output_name
    assert kwargs["telop_script"] == spec.telop_document
    assert kwargs["ffmpeg_path"] == "fake-ffmpeg"


def test_job_target_rebuilds_specs_and_bridges_report(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    spec = _specs(1)[0]
    report = MagicMock()
    with patch("yt_live_kit.services.shorts_queue.run_shorts_queue") as run:
        run_shorts_queue_job_target(
            report=report,
            settings=settings,
            job_id="job",
            video_id="video",
            clip_spec_dicts=[spec.to_dict()],
        )
        callback = run.call_args.kwargs["on_progress"]
        callback(1, 1, "完了")
    report.assert_called_once_with(current=1, total=1, message="完了")


def test_latest_uses_created_at_then_job_id_and_is_video_scoped(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    spec = _specs(1)[0]
    created = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for job_id in ("job-a", "job-b"):
        path = tmp_path / "video-a" / "shorts" / "queue" / f"queue_{job_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        result = ShortsQueueResult(
            video_id="video-a",
            job_id=job_id,
            status="running",
            created_at=created,
            updated_at=created,
            clip_specs=(spec,),
            items=(),
            success_count=0,
            failure_count=0,
            manifest_path=path,
        )
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")
    assert load_latest_shorts_queue_result("video-a", settings).job_id == "job-b"
    assert load_latest_shorts_queue_result("video-b", settings) is None


def test_manifest_schema_version_is_fixed():
    assert SCHEMA_VERSION == 1


def test_loader_rejects_manifest_artifact_path_outside_data_root(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    spec = _specs(1)[0]
    now = datetime.now(timezone.utc)
    manifest = settings.data_dir / "video" / "shorts" / "queue" / "queue_job.json"
    result = ShortsQueueResult(
        video_id="video",
        job_id="job",
        status="done",
        created_at=now,
        updated_at=now,
        clip_specs=(spec,),
        items=(
            ShortsQueueItemResult(
                target_id=spec.target_id,
                status="succeeded",
                output_path=tmp_path / "outside.mp4",
                log_path=None,
                font_warning=None,
                title_candidates=("タイトル",),
                description="説明",
                tags=("タグ",),
                error=None,
            ),
        ),
        success_count=1,
        failure_count=0,
        manifest_path=manifest,
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ShortsQueueError, match="安全に扱えません"):
        load_shorts_queue_result("video", "job", settings)
