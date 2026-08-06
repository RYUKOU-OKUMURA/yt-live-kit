"""P2 投稿スケジュールと確定トランザクションのテスト."""

from __future__ import annotations

import json
import stat
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadDescriptionRequirementsSnapshot,
    UploadResult,
)
from yt_live_kit.services.description import SHORTS_DESCRIPTION_CTA
from yt_live_kit.services.schedule import (
    ScheduleError,
    SchedulePolicy,
    SchedulePolicyNotConfigured,
    assign_next_slot,
    build_upload_preview,
    confirm_and_start_upload,
    get_next_upload_slot,
    load_schedule_policy,
    make_content_fingerprint,
    make_requested_publish_at,
    make_schedule_policy,
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
    upload_job_target,
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
    path = (tmp_path / "data" / name).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    return path


def _preview(tmp_path: Path, settings: Settings, **overrides):
    generated_description = "説明文"
    source_title = "元動画タイトル"
    source_url = "https://example.com/source?t=0s"
    description = (
        f"{generated_description}\n{source_title}\n{source_url}\n"
        f"{SHORTS_DESCRIPTION_CTA}"
    )
    requirements = UploadDescriptionRequirementsSnapshot(
        generated_description=generated_description,
        source_title=source_title,
        source_url=source_url,
        fixed_cta=SHORTS_DESCRIPTION_CTA,
        template_bytes_fingerprint="a" * 64,
        meta_json_fingerprint="b" * 64,
        generated_description_occurrences=1,
        source_title_occurrences=1,
        source_url_occurrences=1,
        fixed_cta_line_occurrences=1,
    )
    values = {
        "source_video_id": "source-1",
        "source_kind": "shorts_queue",
        "clip_id": "clip-1",
        "video_path": _video(tmp_path),
        "title": "予約タイトル",
        "description": description,
        "tags": ("タグ1", "タグ2"),
        "requirements": requirements,
        "settings": settings,
        "now": NOW,
    }
    values.update(overrides)
    save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings)
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
        requirements=preview.requirements,
    )


@pytest.mark.parametrize("daily_time", ["00:00", "23:59"])
def test_schedule_policy_accepts_ascii_hhmm_boundaries(daily_time: str) -> None:
    policy = SchedulePolicy(daily_time=daily_time, interval_days=1)
    assert policy.daily_time == daily_time
    assert policy.daily_times == (daily_time,)


def test_schedule_policy_accepts_and_sorts_multiple_daily_times() -> None:
    policy = SchedulePolicy(daily_times=["23:59", "00:00", "12:30"])
    assert policy.daily_times == ("00:00", "12:30", "23:59")
    # 移行期間の単一値 accessor は、正規化後の先頭枠を返す。
    assert policy.daily_time == "00:00"


@pytest.mark.parametrize(
    ("daily_times", "reason"),
    [
        ([], "1件以上"),
        (["09:00", "09:00"], "重複"),
        (["09:00", "9:30"], "HH:MM"),
        (["09:00", "１２:００"], "HH:MM"),
    ],
)
def test_schedule_policy_rejects_invalid_multiple_daily_times(
    daily_times: list[str], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        SchedulePolicy(daily_times=daily_times)


def test_schedule_policy_rejects_singular_and_plural_time_together() -> None:
    with pytest.raises(ValidationError, match="同時に指定できません"):
        SchedulePolicy(daily_time="09:00", daily_times=["12:00"])


def test_make_schedule_policy_accepts_plural_and_rejects_ambiguous_input() -> None:
    policy = make_schedule_policy(
        daily_times=["18:00", "09:00"],
        interval_days=2,
        timezone_name="Asia/Tokyo",
    )
    assert policy.daily_times == ("09:00", "18:00")

    with pytest.raises(ScheduleError, match="同時に指定できません"):
        make_schedule_policy(
            daily_time="09:00",
            daily_times=["18:00"],
            interval_days=2,
            timezone_name="Asia/Tokyo",
        )


@pytest.mark.parametrize(
    "daily_time",
    ["0:00", "24:00", "12:60", "１２:００", "12：00", " 12:00", "12:00 "],
)
def test_schedule_policy_rejects_invalid_hhmm(daily_time: str) -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        SchedulePolicy(daily_time=daily_time, interval_days=1)


def test_schedule_policy_interval_and_timezone_boundaries() -> None:
    assert SchedulePolicy(
        daily_times=["09:00"],
        interval_days=1,
        timezone="America/Los_Angeles",
    ).interval_days == 1
    with pytest.raises(ValidationError):
        SchedulePolicy(daily_times=["09:00"], interval_days=0)
    with pytest.raises(ValidationError, match="IANA"):
        SchedulePolicy(daily_times=["09:00"], timezone="Not/A_Zone")
    with pytest.raises(ValidationError):
        SchedulePolicy(daily_times=["09:00"], interval_days="1")
    with pytest.raises(ValidationError):
        SchedulePolicy(daily_times=["09:00"], interval_days=True)


def test_schedule_policy_atomic_round_trip_and_corrupt_fail_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    policy = SchedulePolicy(
        daily_times=["21:30", "09:00", "18:30"],
        interval_days=3,
        timezone="Asia/Tokyo",
    )
    path = save_schedule_policy(policy, settings)
    assert load_schedule_policy(settings) == policy
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["daily_times"] == ["09:00", "18:30", "21:30"]
    assert "daily_time" not in saved
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not tuple(path.parent.glob("*.tmp"))
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ScheduleError, match="壊れて"):
        load_schedule_policy(settings)


def test_schedule_policy_rejects_config_symlink_outside_without_leaking_path(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    outside = tmp_path / "outside-config"
    outside.mkdir()
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "_config").symlink_to(outside, target_is_directory=True)

    for operation in (
        lambda: load_schedule_policy(settings),
        lambda: save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings),
    ):
        with pytest.raises(ScheduleError) as caught:
            operation()
        assert "outside-config" not in str(caught.value)
        assert "投稿スケジュール設定" in str(caught.value)


def test_schedule_policy_save_failure_preserves_existing_formal_file(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = save_schedule_policy(SchedulePolicy(daily_time="18:00"), settings)
    before = path.read_text(encoding="utf-8")

    with (
        patch(
            "yt_live_kit.services.schedule.write_text_atomically",
            side_effect=OSError("write /secret/path"),
        ),
        pytest.raises(ScheduleError, match="安全に保存できませんでした") as caught,
    ):
        save_schedule_policy(SchedulePolicy(daily_time="21:00"), settings)

    assert "write /secret/path" not in str(caught.value)
    assert path.read_text(encoding="utf-8") == before


def test_schedule_policy_read_oserror_is_normalized_without_leaking_detail(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings)

    with (
        patch(
            "yt_live_kit.services.schedule.Path.read_text",
            side_effect=OSError("read /secret/path"),
        ),
        pytest.raises(ScheduleError, match="壊れて") as caught,
    ):
        load_schedule_policy(settings)

    assert "read /secret/path" not in str(caught.value)


@pytest.mark.parametrize("operation", ["load", "save"])
def test_schedule_policy_lock_error_is_normalized_at_service_boundary(
    tmp_path: Path,
    operation: str,
) -> None:
    settings = _settings(tmp_path)

    with (
        patch(
            "yt_live_kit.services.schedule.schedule_lock",
            side_effect=UploadQueueError("lock /secret/path"),
        ),
        pytest.raises(ScheduleError) as caught,
    ):
        if operation == "load":
            load_schedule_policy(settings)
        else:
            save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings)

    assert "lock /secret/path" not in str(caught.value)
    assert "投稿スケジュール設定" in str(caught.value)


def test_schedule_policy_loads_legacy_daily_time_and_saves_plural_format(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = settings.data_dir / "_config" / "schedule_policy.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"daily_time": "18:30", "interval_days": 2, "timezone": "Asia/Tokyo"}
        ),
        encoding="utf-8",
    )

    policy = load_schedule_policy(settings)

    assert policy.daily_times == ("18:30",)
    assert policy.daily_time == "18:30"
    save_schedule_policy(policy, settings)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {
        "daily_times": ["18:30"],
        "interval_days": 2,
        "timezone": "Asia/Tokyo",
    }

    path.write_text(
        json.dumps(
            {"daily_time": "09:00", "interval_days": "1", "timezone": "Asia/Tokyo"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScheduleError, match="壊れて"):
        load_schedule_policy(settings)


def test_schedule_policy_load_requires_explicit_configuration(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(SchedulePolicyNotConfigured, match="未設定"):
        load_schedule_policy(settings)

    assert not (settings.data_dir / "_config" / "schedule_policy.json").exists()


def test_unconfigured_schedule_stops_before_preview_or_slot_side_effects(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    video_path = _video(tmp_path)

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch_channel,
        patch("yt_live_kit.services.schedule.list_operations") as list_operations_mock,
        patch("yt_live_kit.services.schedule.count_upload_attempts") as attempts,
        pytest.raises(SchedulePolicyNotConfigured, match="未設定"),
    ):
        build_upload_preview(
            source_video_id="source-1",
            source_kind="shorts_queue",
            clip_id="clip-1",
            video_path=video_path,
            title="予約タイトル",
            description="説明文",
            tags=("タグ1",),
            settings=settings,
            now=NOW,
        )

    fetch_channel.assert_not_called()
    list_operations_mock.assert_not_called()
    attempts.assert_not_called()

    with (
        patch("yt_live_kit.services.schedule.list_operations") as list_operations_mock,
        pytest.raises(SchedulePolicyNotConfigured, match="未設定"),
    ):
        get_next_upload_slot(settings, now=NOW)

    list_operations_mock.assert_not_called()


def test_build_upload_preview_skips_fetch_when_channel_is_provided(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch,
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        preview = build_upload_preview(
            source_video_id="source-1",
            source_kind="shorts_queue",
            clip_id="clip-1",
            video_path=_video(tmp_path),
            title="予約タイトル",
            description="説明文",
            tags=("タグ1",),
            settings=settings,
            now=NOW,
            channel=CHANNEL,
        )
    fetch.assert_not_called()
    assert preview.channel == CHANNEL


def test_confirm_fetches_channel_before_schedule_lock(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    call_order: list[str] = []

    class TrackingLock:
        def __enter__(self) -> None:
            call_order.append("lock_enter")

        def __exit__(self, *args: object) -> bool:
            return False

    def track_fetch(*args: object, **kwargs: object) -> UploadChannel:
        call_order.append("fetch_channel")
        return CHANNEL

    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", side_effect=track_fetch),
        patch(
            "yt_live_kit.services.schedule.schedule_lock",
            return_value=TrackingLock(),
        ),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "lock-order-operation",
            job_id_factory=lambda: "lock-order-job",
            start_job_fn=lambda *args, **kwargs: kwargs["requested_job_id"],
        )

    assert call_order.index("fetch_channel") < call_order.index("lock_enter")


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


def test_assign_next_slot_uses_earlier_open_slot_despite_later_reservations() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(daily_time="12:00", interval_days=3)
    latest = datetime(2026, 8, 10, 12, 0, tzinfo=zone)
    slot = assign_next_slot(
        policy,
        [datetime(2026, 8, 4, 12, 0, tzinfo=zone), latest],
        now=NOW,
    )
    # 将来の予約を起点にせず、現在日以降で最初の未使用枠を選ぶ。
    assert slot == datetime(2026, 8, 1, 12, 0, tzinfo=zone)


def test_assign_next_slot_fills_same_day_in_time_order_then_advances_interval() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(
        daily_times=["18:00", "09:10", "12:00"],
        interval_days=2,
    )
    first = datetime(2026, 8, 1, 9, 10, tzinfo=zone)
    second = datetime(2026, 8, 1, 12, 0, tzinfo=zone)
    third = datetime(2026, 8, 1, 18, 0, tzinfo=zone)

    # 09:10 は現在 + 10 分ちょうどなので割り当て可能。
    assert assign_next_slot(policy, [], now=NOW) == first
    assert assign_next_slot(policy, [first.astimezone(timezone.utc)], now=NOW) == second
    assert assign_next_slot(policy, [first, second], now=NOW) == third
    assert assign_next_slot(policy, [first, second, third], now=NOW) == datetime(
        2026, 8, 3, 9, 10, tzinfo=zone
    )


def test_assign_next_slot_aligns_current_day_to_existing_cadence() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(daily_times=["12:00", "18:00"], interval_days=2)
    occupied = [
        datetime(2026, 8, 1, 12, 0, tzinfo=zone),
        datetime(2026, 8, 1, 18, 0, tzinfo=zone),
    ]
    now = datetime(2026, 8, 2, 0, 0, tzinfo=zone)

    assert assign_next_slot(policy, occupied, now=now) == datetime(
        2026, 8, 3, 12, 0, tzinfo=zone
    )


def test_assign_next_slot_backtracks_future_anchor_to_today_on_same_cadence() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(daily_times=["12:00", "18:00"], interval_days=3)
    future = datetime(2026, 8, 10, 12, 0, tzinfo=zone)
    now = datetime(2026, 8, 1, 0, 0, tzinfo=zone)

    assert assign_next_slot(policy, [future], now=now) == datetime(
        2026, 8, 1, 12, 0, tzinfo=zone
    )


def test_assign_next_slot_backtracks_future_anchor_to_next_aligned_day() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(daily_times=["12:00", "18:00"], interval_days=3)
    future = datetime(2026, 8, 10, 12, 0, tzinfo=zone)
    now = datetime(2026, 8, 2, 0, 0, tzinfo=zone)

    assert assign_next_slot(policy, [future], now=now) == datetime(
        2026, 8, 4, 12, 0, tzinfo=zone
    )


def test_assign_next_slot_does_not_skip_open_same_day_slot_for_later_booking() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    policy = SchedulePolicy(daily_times=["09:10", "12:00", "18:00"])
    later = datetime(2026, 8, 2, 9, 10, tzinfo=zone)

    assert assign_next_slot(policy, [later], now=NOW) == datetime(
        2026, 8, 1, 9, 10, tzinfo=zone
    )


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
    policy = SchedulePolicy(daily_times=["09:00"], timezone="America/New_York")
    with pytest.raises(ScheduleError, match="DST"):
        make_requested_publish_at(policy, publish_date, publish_time)


def test_requested_publish_at_is_policy_aware_and_exactly_preserved_in_preview(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    policy = SchedulePolicy(daily_times=["09:00"])
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
        SchedulePolicy(daily_times=["09:00"]),
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
        SchedulePolicy(daily_times=["09:00"]),
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
    save_schedule_policy(SchedulePolicy(daily_times=["09:00"]), settings)
    policy, slot = get_next_upload_slot(settings, now=NOW)
    assert policy == SchedulePolicy(daily_times=["09:00"])
    assert slot == datetime(2026, 8, 2, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def test_assign_next_slot_fails_closed_when_one_of_multiple_slots_is_dst_invalid() -> None:
    policy = SchedulePolicy(
        daily_times=["01:00", "02:30", "04:00"],
        interval_days=1,
        timezone="America/New_York",
    )
    now = datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)

    with pytest.raises(ScheduleError, match="DST"):
        assign_next_slot(policy, [], now=now)


def test_preview_contains_all_read_only_fields_and_no_queue_side_effect(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    assert preview.channel == CHANNEL
    assert preview.video_path.is_absolute()
    assert preview.file_size == 5
    assert preview.duration_sec == 30
    assert preview.title == "予約タイトル"
    assert preview.description.startswith("説明文\n元動画タイトル")
    assert preview.tags == ("タグ1", "タグ2")
    assert preview.privacy_status == "private"
    assert preview.notify_subscribers is False
    assert preview.publish_at_utc_z.endswith("Z")
    assert preview.attempt_count_la == 0
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


def test_p6_description_requirements_round_trip_and_fingerprint_tracks_inputs(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)

    restored_preview = type(preview).model_validate_json(preview.model_dump_json())
    assert restored_preview.requirements == preview.requirements
    assert restored_preview.fingerprint == preview.fingerprint
    assert restored_preview.requirements is not None
    assert restored_preview.requirements.template_bytes_fingerprint == "a" * 64
    assert restored_preview.requirements.meta_json_fingerprint == "b" * 64

    content = _content_from_preview(preview)
    restored_content = UploadContentSnapshot.model_validate_json(
        content.model_dump_json()
    )
    assert restored_content == content
    assert restored_content.requirements == preview.requirements

    legacy_preview_payload = json.loads(preview.model_dump_json())
    legacy_preview_payload.pop("requirements")
    legacy_preview = type(preview).model_validate(legacy_preview_payload)
    assert legacy_preview.requirements is None

    changed_template_requirements = preview.requirements.model_copy(
        update={"template_bytes_fingerprint": "c" * 64}
    )
    changed_template = _preview(
        tmp_path,
        settings,
        requirements=changed_template_requirements,
    )
    changed_meta_requirements = preview.requirements.model_copy(
        update={"meta_json_fingerprint": "d" * 64}
    )
    changed_meta = _preview(
        tmp_path,
        settings,
        requirements=changed_meta_requirements,
    )

    assert changed_template.description == preview.description
    assert changed_meta.description == preview.description
    assert changed_template.fingerprint != preview.fingerprint
    assert changed_meta.fingerprint != preview.fingerprint
    assert make_content_fingerprint(_content_from_preview(changed_template)) != (
        make_content_fingerprint(content)
    )
    assert make_content_fingerprint(_content_from_preview(changed_meta)) != (
        make_content_fingerprint(content)
    )


def test_p6_requirements_model_is_strict_and_service_rejects_changed_fixed_cta(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    values = preview.requirements.model_dump()
    values["template_bytes_fingerprint"] = "not-a-sha256"
    with pytest.raises(ValidationError):
        UploadDescriptionRequirementsSnapshot.model_validate(values)

    invalid_cta = preview.requirements.model_copy(update={"fixed_cta": "別の CTA"})
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch,
        pytest.raises(ScheduleError, match="固定 CTA"),
    ):
        build_upload_preview(
            source_video_id=preview.source_video_id,
            source_kind=preview.source_kind,
            clip_id=preview.clip_id,
            video_path=preview.video_path,
            title=preview.title,
            description=preview.description,
            tags=preview.tags,
            settings=settings,
            now=NOW,
            requirements=invalid_cta,
        )
    fetch.assert_not_called()


def test_build_preview_description_gate_stops_before_queue_attempt_job_or_api(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    broken_description = preview.description.replace(SHORTS_DESCRIPTION_CTA, "")
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch,
        patch("yt_live_kit.services.schedule.list_operations") as queue,
        patch("yt_live_kit.services.schedule.count_upload_attempts") as attempts,
        patch("yt_live_kit.services.schedule.create_reserved_operation") as create,
        patch("yt_live_kit.services.schedule.start_job") as start_job,
        patch("yt_live_kit.services.schedule.build_upload_snapshot") as api_snapshot,
        pytest.raises(ScheduleError, match="チャンネル登録 CTA"),
    ):
        build_upload_preview(
            source_video_id=preview.source_video_id,
            source_kind=preview.source_kind,
            clip_id=preview.clip_id,
            video_path=preview.video_path,
            title=preview.title,
            description=broken_description,
            tags=preview.tags,
            settings=settings,
            now=NOW,
            requirements=preview.requirements,
        )
    fetch.assert_not_called()
    queue.assert_not_called()
    attempts.assert_not_called()
    create.assert_not_called()
    start_job.assert_not_called()
    api_snapshot.assert_not_called()


def test_confirm_description_mutation_stops_before_operation_job_or_api(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    original = _preview(tmp_path, settings)
    preview = original.model_copy(
        update={"description": original.description.replace(SHORTS_DESCRIPTION_CTA, "")}
    )
    start_job = MagicMock()
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch,
        patch("yt_live_kit.services.schedule.create_reserved_operation") as create,
        patch("yt_live_kit.services.schedule.build_upload_snapshot") as api_snapshot,
        pytest.raises(ScheduleError, match="チャンネル登録 CTA"),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=start_job,
        )
    fetch.assert_not_called()
    create.assert_not_called()
    api_snapshot.assert_not_called()
    start_job.assert_not_called()
    assert list_operations(settings) == ()
    assert count_upload_attempts(settings, now=NOW) == 0


def test_confirm_uses_frozen_description_requirements_without_rereading_template_or_meta(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
        patch(
            "yt_live_kit.services.description._read_shorts_template_bytes",
            side_effect=AssertionError("template must remain frozen"),
        ),
        patch(
            "yt_live_kit.services.description._load_video_meta_with_fingerprint",
            side_effect=AssertionError("meta must remain frozen"),
        ),
    ):
        operation = confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "frozen-operation",
            job_id_factory=lambda: "frozen-job",
            start_job_fn=lambda *args, **kwargs: kwargs["requested_job_id"],
        )

    assert operation.content.description == preview.description
    assert operation.content.requirements == preview.requirements
    assert list_operations(settings)[0].content.description == preview.description


def test_confirm_rejects_legacy_preview_before_channel_queue_or_job_reads(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings).model_copy(update={"requirements": None})
    start_job = MagicMock()
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel") as fetch,
        patch("yt_live_kit.services.schedule.create_reserved_operation") as create,
        pytest.raises(ScheduleError, match="旧形式.*新しいプレビュー"),
    ):
        confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            start_job_fn=start_job,
        )
    fetch.assert_not_called()
    create.assert_not_called()
    start_job.assert_not_called()
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


def test_confirmed_preview_description_reaches_worker_and_insert_body_unchanged(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preview = _preview(tmp_path, settings)
    with (
        patch("yt_live_kit.services.schedule.fetch_mine_channel", return_value=CHANNEL),
        patch("yt_live_kit.services.youtube_api.probe_duration", return_value=30.0),
    ):
        operation = confirm_and_start_upload(
            preview,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            settings=settings,
            now=NOW,
            operation_id_factory=lambda: "worker-operation",
            job_id_factory=lambda: "worker-job",
            start_job_fn=lambda *args, **kwargs: kwargs["requested_job_id"],
        )

    persisted = list_operations(settings)[0]
    assert persisted.content.description == preview.description

    insert_bodies: list[dict[str, object]] = []

    def fake_body(snapshot):
        body = {"snippet": {"description": snapshot.description}}
        insert_bodies.append(body)
        return body

    upload_result = UploadResult(state="uploaded", video_id="youtube-worker", error=None)
    with (
        patch(
            "yt_live_kit.services.upload_queue.fetch_mine_channel",
            return_value=persisted.content.channel,
        ),
        patch("yt_live_kit.services.upload_queue.validate_snapshot_identity"),
        patch(
            "yt_live_kit.services.upload_queue.build_upload_body",
            side_effect=fake_body,
        ) as build_body,
        patch(
            "yt_live_kit.services.upload_queue.upload_video_resumable",
            return_value=upload_result,
        ) as upload,
        patch("yt_live_kit.services.upload_queue.poll_processing_status", return_value=()),
        patch("yt_live_kit.services.jobs.update_job"),
    ):
        upload_job_target(
            report=MagicMock(),
            settings=settings,
            job_id=operation.job_id,
            operation_id=operation.operation_id,
        )

    worker_snapshot = upload.call_args.args[0]
    insert_body = insert_bodies[0]
    build_body.assert_called_once_with(persisted.content)
    assert worker_snapshot.description == preview.description
    assert insert_body["snippet"]["description"] == preview.description


def test_confirm_preserves_manual_publish_at_through_operation_and_job_start(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    requested = make_requested_publish_at(
        SchedulePolicy(daily_times=["09:00"]),
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
        SchedulePolicy(daily_times=["09:00"]),
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
        SchedulePolicy(daily_times=["09:00"]),
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
            requirements=preview.requirements,
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
