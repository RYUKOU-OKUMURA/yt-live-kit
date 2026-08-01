"""P2 投稿スケジュールと確定トランザクションのテスト."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import UploadChannel, UploadContentSnapshot
from yt_live_kit.services.schedule import (
    ScheduleError,
    SchedulePolicy,
    assign_next_slot,
    build_upload_preview,
    confirm_and_start_upload,
    get_next_upload_slot,
    load_schedule_policy,
    make_content_fingerprint,
    make_requested_publish_at,
    save_schedule_policy,
    to_utc_rfc3339_z,
)
from yt_live_kit.services.upload_queue import (
    UploadQueueError,
    count_upload_attempts,
    create_reserved_operation,
    list_operations,
    record_upload_attempt,
    recover_upload_operations,
    transition_operation,
)
from yt_live_kit.services.youtube_api import build_upload_snapshot


NOW = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
CHANNEL = UploadChannel(channel_id="channel-1", title="確認チャンネル")


def _settings(tmp_path: Path, *, limit: int = 100) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        ffmpeg_path="/mock/ffmpeg",
        video_upload_daily_limit=limit,
    )


def _video(tmp_path: Path, name: str = "short_a.mp4") -> Path:
    path = (tmp_path / name).resolve()
    path.write_bytes(b"video")
    return path


def _preview(tmp_path: Path, settings: Settings, **overrides):
    values = {
        "source_video_id": "source-1",
        "source_kind": "shorts_queue",
        "clip_id": "clip-1",
        "video_path": _video(tmp_path),
        "title": "予約タイトル",
        "description": "説明文",
        "tags": ("タグ1", "タグ2"),
        "settings": settings,
        "now": NOW,
    }
    values.update(overrides)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        return build_upload_preview(**values)


def _content_from_preview(preview) -> UploadContentSnapshot:
    return UploadContentSnapshot(
        channel=preview.channel,
        video_path=preview.video_path,
        file_size=preview.file_size,
        file_mtime_ns=preview.file_mtime_ns,
        duration_sec=preview.duration_sec,
        title=preview.title,
        description=preview.description,
        tags=preview.tags,
        publish_at=preview.publish_at,
        privacy_status="private",
        notify_subscribers=False,
        self_declared_made_for_kids=False,
        contains_synthetic_media=False,
        community_guidelines_confirmed=True,
        community_guidelines_confirmed_at=NOW,
    )


@pytest.mark.parametrize("daily_time", ["00:00", "23:59"])
def test_schedule_policy_accepts_ascii_hhmm_boundaries(daily_time: str) -> None:
    assert SchedulePolicy(daily_time=daily_time, interval_days=1).daily_time == daily_time


@pytest.mark.parametrize(
    "daily_time",
    ["0:00", "24:00", "12:60", "１２:００", "12：00", " 12:00", "12:00 "],
)
def test_schedule_policy_rejects_invalid_hhmm(daily_time: str) -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        SchedulePolicy(daily_time=daily_time, interval_days=1)


def test_schedule_policy_interval_and_timezone_boundaries() -> None:
    assert SchedulePolicy(interval_days=1, timezone="America/Los_Angeles").interval_days == 1
    with pytest.raises(ValidationError):
        SchedulePolicy(interval_days=0)
    with pytest.raises(ValidationError, match="IANA"):
        SchedulePolicy(timezone="Not/A_Zone")
    with pytest.raises(ValidationError):
        SchedulePolicy(interval_days="1")
    with pytest.raises(ValidationError):
        SchedulePolicy(interval_days=True)


def test_schedule_policy_atomic_round_trip_and_corrupt_fail_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    policy = SchedulePolicy(daily_time="18:30", interval_days=3, timezone="Asia/Tokyo")
    path = save_schedule_policy(policy, settings)
    assert load_schedule_policy(settings) == policy
    assert not tuple(path.parent.glob("*.tmp"))
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ScheduleError, match="壊れて"):
        load_schedule_policy(settings)

    path.write_text(
        json.dumps(
            {"daily_time": "09:00", "interval_days": "1", "timezone": "Asia/Tokyo"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScheduleError, match="壊れて"):
        load_schedule_policy(settings)


def test_assign_next_slot_requires_aware_now_and_returns_tokyo_aware() -> None:
    policy = SchedulePolicy(daily_time="12:00", interval_days=1)
    slot = assign_next_slot(policy, [], now=NOW)
    assert slot.isoformat() == "2026-08-01T12:00:00+09:00"
    assert to_utc_rfc3339_z(slot) == "2026-08-01T03:00:00Z"
    with pytest.raises(ScheduleError, match="タイムゾーン"):
        assign_next_slot(policy, [], now=NOW.replace(tzinfo=None))


def test_assign_next_slot_honors_lead_collision_and_interval() -> None:
    policy = SchedulePolicy(daily_time="09:05", interval_days=2)
    slot_zone = ZoneInfo("Asia/Tokyo")
    first = datetime(2026, 8, 3, 9, 5, tzinfo=slot_zone)
    slot = assign_next_slot(policy, [first], now=NOW)
    # 8/1 09:05 は現在から5分だけなので除外、8/3 は占有、次は8/5。
    assert slot == datetime(2026, 8, 5, 9, 5, tzinfo=slot_zone)


def test_assign_next_slot_continues_after_latest_reservation_without_backfill() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(daily_time="12:00", interval_days=3)
    latest = datetime(2026, 8, 10, 12, 0, tzinfo=zone)
    slot = assign_next_slot(
        policy,
        [datetime(2026, 8, 4, 12, 0, tzinfo=zone), latest],
        now=NOW,
    )
    assert slot == datetime(2026, 8, 13, 12, 0, tzinfo=zone)


@pytest.mark.parametrize(
    ("daily_time", "now"),
    [
        ("02:30", datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)),
        ("01:30", datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)),
    ],
)
def test_assign_next_slot_rejects_dst_nonexistent_and_ambiguous(
    daily_time: str, now: datetime
) -> None:
    policy = SchedulePolicy(
        daily_time=daily_time,
        interval_days=1,
        timezone="America/New_York",
    )
    with pytest.raises(ScheduleError, match="DST"):
        assign_next_slot(policy, [], now=now)


@pytest.mark.parametrize(
    ("publish_date", "publish_time"),
    [
        (date(2026, 3, 8), time(2, 30)),
        (date(2026, 11, 1), time(1, 30)),
    ],
)
def test_make_requested_publish_at_rejects_dst_nonexistent_and_ambiguous(
    publish_date: date,
    publish_time: time,
) -> None:
    policy = SchedulePolicy(timezone="America/New_York")
    with pytest.raises(ScheduleError, match="DST"):
        make_requested_publish_at(policy, publish_date, publish_time)


def test_requested_publish_at_is_policy_aware_and_exactly_preserved_in_preview(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    policy = SchedulePolicy()
    requested = make_requested_publish_at(policy, date(2026, 8, 4), time(15, 45))

    preview = _preview(tmp_path, settings, requested_publish_at=requested)

    assert getattr(requested.tzinfo, "key", None) == policy.timezone
    assert getattr(preview.publish_at.tzinfo, "key", None) == policy.timezone
    assert preview.publish_at.astimezone(timezone.utc) == requested.astimezone(timezone.utc)
    assert preview.publish_at_utc_z == "2026-08-04T06:45:00Z"
    assert preview.publish_at.hour == 15
    assert preview.publish_at.minute == 45
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


@pytest.mark.parametrize("minute", [0, 9])
def test_requested_publish_at_rejects_lead_shortage_without_side_effects(
    tmp_path: Path,
    minute: int,
) -> None:
    settings = _settings(tmp_path)
    requested = make_requested_publish_at(
        SchedulePolicy(),
        date(2026, 8, 1),
        time(9, minute),
    )
    with pytest.raises(ScheduleError, match="10 分以上"):
        _preview(tmp_path, settings, requested_publish_at=requested)
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


def test_requested_publish_at_accepts_exact_ten_minute_lead_and_rejects_naive(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    exact_boundary = make_requested_publish_at(
        SchedulePolicy(),
        date(2026, 8, 1),
        time(9, 10),
    )
    assert _preview(
        tmp_path,
        settings,
        requested_publish_at=exact_boundary,
    ).publish_at == exact_boundary

    with pytest.raises(ScheduleError, match="タイムゾーン付き"):
        _preview(
            tmp_path,
            settings,
            requested_publish_at=datetime(2026, 8, 2, 9, 0),
        )
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


def test_get_next_upload_slot_returns_policy_and_current_available_slot(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    policy, slot = get_next_upload_slot(settings, now=NOW)
    assert policy == SchedulePolicy()
    assert slot == datetime(2026, 8, 2, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def test_preview_contains_all_read_only_fields_and_no_queue_side_effect(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    assert preview.channel == CHANNEL
    assert preview.video_path.is_absolute()
    assert preview.file_size == 5
    assert preview.duration_sec == 30
    assert preview.title == "予約タイトル"
    assert preview.description == "説明文"
    assert preview.tags == ("タグ1", "タグ2")
    assert preview.privacy_status == "private"
    assert preview.notify_subscribers is False
    assert preview.publish_at_utc_z.endswith("Z")
    assert preview.attempt_count_la == 0
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


def test_confirm_rejects_unselected_audience_synthetic_and_consent_without_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    for made_for_kids, synthetic, consent in (
        (None, False, True),
        (False, None, True),
        (False, False, False),
    ):
        with pytest.raises(ScheduleError):
            confirm_and_start_upload(
                preview,
                self_declared_made_for_kids=made_for_kids,
                contains_synthetic_media=synthetic,
                community_guidelines_confirmed=consent,
                settings=settings,
                now=NOW,
                start_job_fn=lambda *args, **kwargs: "unused",
            )
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


def test_confirm_saves_single_record_then_starts_same_ids(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    calls = []

    def fake_start(*args, **kwargs):
        stored = list_operations(settings)
        assert len(stored) == 1
        assert stored[0].operation_id == "operation-1"
        assert stored[0].job_id == "job-1"
        calls.append((args, kwargs))
        return "job-1"

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        operation = confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=True,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "operation-1",
            job_id_factory=lambda: "job-1",
            start_job_fn=fake_start,
        )
    assert operation.state == "reserved"
    assert operation.content.self_declared_made_for_kids is True
    assert operation.content.contains_synthetic_media is False
    assert operation.content.community_guidelines_confirmed is True
    assert make_content_fingerprint(operation.content) == make_content_fingerprint(
        list_operations(settings)[0].content
    )
    assert calls[0][1]["requested_job_id"] == "job-1"
    assert calls[0][1]["operation_id"] == "operation-1"
    assert count_upload_attempts(settings, now=NOW) == 0


def test_confirm_preserves_manual_publish_at_through_operation_and_job_start(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    requested = make_requested_publish_at(
        SchedulePolicy(),
        date(2026, 8, 4),
        time(15, 45),
    )
    preview = _preview(tmp_path, settings, requested_publish_at=requested)
    started: list[dict[str, object]] = []

    def fake_start(*args, **kwargs):
        started.append(kwargs)
        return "manual-job"

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        operation = confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=True,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "manual-operation",
            job_id_factory=lambda: "manual-job",
            start_job_fn=fake_start,
        )

    assert operation.content.publish_at.astimezone(timezone.utc) == requested.astimezone(
        timezone.utc
    )
    assert list_operations(settings)[0].content.publish_at == operation.content.publish_at
    assert started[0]["operation_id"] == "manual-operation"
    assert started[0]["requested_job_id"] == "manual-job"
    assert count_upload_attempts(settings, now=NOW) == 0


def test_requested_publish_at_rejects_existing_slot_before_read_only_api(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    requested = make_requested_publish_at(
        SchedulePolicy(),
        date(2026, 8, 4),
        time(15, 45),
    )
    preview = _preview(tmp_path, settings, requested_publish_at=requested)
    create_reserved_operation(
        operation_id="slot-holder",
        job_id="slot-holder-job",
        source_video_id="other-source",
        source_kind="shorts_queue",
        clip_id="other-clip",
        content=_content_from_preview(preview),
        now=NOW,
        settings=settings,
    )

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch,
        pytest.raises(ScheduleError, match="既に使われています"),
    ):
        build_upload_preview(
            source_video_id="source-1",
            source_kind="shorts_queue",
            clip_id="clip-1",
            video_path=preview.video_path,
            title=preview.title,
            description=preview.description,
            tags=preview.tags,
            requested_publish_at=requested,
            settings=settings,
            now=NOW,
        )
    fetch.assert_not_called()
    assert len(list_operations(settings)) == 1
    assert count_upload_attempts(settings, now=NOW) == 0


def test_manual_slot_confirm_race_creates_no_new_operation_job_or_attempt(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    requested = make_requested_publish_at(
        SchedulePolicy(),
        date(2026, 8, 4),
        time(15, 45),
    )
    preview = _preview(tmp_path, settings, requested_publish_at=requested)
    create_reserved_operation(
        operation_id="race-winner",
        job_id="race-winner-job",
        source_video_id="other-source",
        source_kind="shorts_queue",
        clip_id="other-clip",
        content=_content_from_preview(preview),
        now=NOW,
        settings=settings,
    )
    started: list[bool] = []

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        pytest.raises(ScheduleError, match="既に使われています"),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *args, **kwargs: started.append(True),
        )

    assert started == []
    assert [item.operation_id for item in list_operations(settings)] == ["race-winner"]
    assert count_upload_attempts(settings, now=NOW) == 0


@pytest.mark.parametrize("change", ["channel", "file", "metadata", "policy"])
def test_confirm_revalidates_preview_and_starts_no_job_on_change(
    tmp_path: Path, change: str
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    if change == "file":
        preview.video_path.write_bytes(b"changed")
    elif change == "policy":
        save_schedule_policy(SchedulePolicy(daily_time="22:00"), settings)
    elif change == "metadata":
        preview = preview.model_copy(update={"title": "変更後タイトル"})
    channel = UploadChannel(channel_id="other", title="別チャンネル") if change == "channel" else CHANNEL
    started = []
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=channel),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        pytest.raises(ScheduleError),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *a, **k: started.append(True),
        )
    assert started == []
    assert list_operations(settings) == ()


def test_slot_race_and_needs_reconciliation_hold_slot_and_source(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        blocker = create_reserved_operation(
            operation_id="blocker",
            job_id="blocker-job",
            source_video_id="source-1",
            source_kind="shorts_queue",
            clip_id="clip-1",
            content=build_upload_snapshot(
                channel=CHANNEL,
                video_path=preview.video_path,
                title=preview.title,
                description=preview.description,
                tags=preview.tags,
                publish_at=preview.publish_at,
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                community_guidelines_confirmed_at=NOW,
                settings=settings,
                now=NOW,
            ),
            now=NOW,
            settings=settings,
        )
        transition_operation(
            blocker.operation_id,
            "needs_reconciliation",
            settings,
            error="手動照合が必要です。",
            now=NOW,
        )
        with pytest.raises(ScheduleError):
            confirm_and_start_upload(
                preview,
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                settings=settings,
                now=NOW,
                start_job_fn=lambda *a, **k: pytest.fail("job must not start"),
            )


def test_attempt_race_is_independent_from_publish_date(tmp_path: Path) -> None:
    settings = _settings(tmp_path, limit=1)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        operation = create_reserved_operation(
            operation_id="old",
            job_id="old-job",
            source_video_id="other-source",
            source_kind="shorts_queue",
            clip_id="other-clip",
            content=build_upload_snapshot(
                channel=CHANNEL,
                video_path=preview.video_path,
                title=preview.title,
                description=preview.description,
                tags=preview.tags,
                publish_at=preview.publish_at + timedelta(days=10),
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                community_guidelines_confirmed_at=NOW,
                settings=settings,
                now=NOW,
            ),
            now=NOW,
            settings=settings,
        )
        record_upload_attempt(operation.operation_id, operation.job_id, settings, now=NOW)
        transition_operation(operation.operation_id, "failed", settings, error="失敗", now=NOW)
        current_preview = build_upload_preview(
            source_video_id=preview.source_video_id,
            source_kind=preview.source_kind,
            clip_id=preview.clip_id,
            video_path=preview.video_path,
            title=preview.title,
            description=preview.description,
            tags=preview.tags,
            settings=settings,
            now=NOW,
        )
        with pytest.raises(ScheduleError, match="試行上限"):
            confirm_and_start_upload(
                current_preview,
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                settings=settings,
                now=NOW,
                start_job_fn=lambda *a, **k: pytest.fail("job must not start"),
            )


def test_start_job_sync_failure_marks_failed_and_releases_slot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        pytest.raises(ScheduleError, match="予約枠を解放"),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    assert list_operations(settings)[0].state == "failed"


def test_duration_change_and_queue_save_failure_create_no_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    started: list[bool] = []
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=31.0),
        pytest.raises(ScheduleError),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *a, **k: started.append(True),
        )
    assert started == []
    assert list_operations(settings) == ()

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        patch(
            "yt_live_kit.services.schedule.create_reserved_operation",
            side_effect=UploadQueueError("保存できません"),
        ),
        pytest.raises(ScheduleError, match="保存できません"),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *a, **k: started.append(True),
        )
    assert started == []
    assert list_operations(settings) == ()


def test_unknown_job_start_result_holds_slot_for_reconciliation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        pytest.raises(ScheduleError, match="手動照合"),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=True,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "operation-unknown",
            job_id_factory=lambda: "expected-job",
            start_job_fn=lambda *a, **k: "different-job",
        )
    operation = list_operations(settings)[0]
    assert operation.state == "needs_reconciliation"
    assert operation.job_id == "expected-job"


def test_process_crash_after_reservation_uses_p1_recovery_without_new_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        pytest.raises(KeyboardInterrupt),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "crashed-operation",
            job_id_factory=lambda: "crashed-job",
            start_job_fn=lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
    assert list_operations(settings)[0].state == "reserved"
    recovered = recover_upload_operations(settings)
    assert len(recovered) == 1
    assert recovered[0].state == "failed"
    assert count_upload_attempts(settings, now=NOW) == 0


def test_policy_save_cannot_interleave_confirm_revalidation_and_queue_save(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    entered_save_boundary = threading.Event()
    release_save_boundary = threading.Event()
    policy_saved = threading.Event()
    outcomes: list[str] = []
    from yt_live_kit.services import schedule as schedule_service

    real_create = schedule_service.create_reserved_operation

    def paused_create(**kwargs):
        entered_save_boundary.set()
        assert release_save_boundary.wait(timeout=5)
        return real_create(**kwargs)

    def run_confirm() -> None:
        try:
            confirm_and_start_upload(
                preview,
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                settings=settings,
                now=NOW,
                start_job_fn=lambda *a, **k: k["requested_job_id"],
            )
        except Exception as exc:  # pragma: no cover - assertion reports detail
            outcomes.append(str(exc))

    def run_save() -> None:
        save_schedule_policy(SchedulePolicy(daily_time="22:00"), settings)
        policy_saved.set()

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        patch("yt_live_kit.services.schedule.create_reserved_operation", side_effect=paused_create),
    ):
        confirm_thread = threading.Thread(target=run_confirm)
        confirm_thread.start()
        assert entered_save_boundary.wait(timeout=5)
        save_thread = threading.Thread(target=run_save)
        save_thread.start()
        assert not policy_saved.wait(timeout=0.1)
        release_save_boundary.set()
        confirm_thread.join(timeout=5)
        save_thread.join(timeout=5)
    assert outcomes == []
    assert policy_saved.is_set()
    assert len(list_operations(settings)) == 1
    assert load_schedule_policy(settings).daily_time == "22:00"


def test_sync_start_transition_failure_does_not_claim_slot_release(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        patch(
            "yt_live_kit.services.schedule.transition_operation",
            side_effect=UploadQueueError("write failed"),
        ),
        pytest.raises(ScheduleError, match="状態を確定できません") as caught,
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    assert "解放しました" not in str(caught.value)
    assert list_operations(settings)[0].state == "reserved"


def test_content_fingerprint_transition_failure_requires_manual_repair(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        patch(
            "yt_live_kit.services.schedule.make_content_fingerprint",
            side_effect=["before", "after"],
        ),
        patch(
            "yt_live_kit.services.schedule.transition_operation",
            side_effect=UploadQueueError("write failed"),
        ),
        pytest.raises(ScheduleError, match="手動修復") as caught,
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=lambda *a, **k: pytest.fail("job must not start"),
        )
    assert "新しく確認" not in str(caught.value)
    assert list_operations(settings)[0].state == "reserved"


def test_concurrent_confirm_has_one_winner_and_no_duplicate_operation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(index: int) -> None:
        barrier.wait()
        try:
            confirm_and_start_upload(
                preview,
                self_declared_made_for_kids=False,
                contains_synthetic_media=False,
                community_guidelines_confirmed=True,
                settings=settings,
                now=NOW,
                operation_id_factory=lambda: f"operation-{index}",
                job_id_factory=lambda: f"job-{index}",
                start_job_fn=lambda *a, **k: k["requested_job_id"],
            )
        except ScheduleError:
            outcomes.append("rejected")
        else:
            outcomes.append("accepted")

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert sorted(outcomes) == ["accepted", "rejected"]
    assert len(list_operations(settings)) == 1
