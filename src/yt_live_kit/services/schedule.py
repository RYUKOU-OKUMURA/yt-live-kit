"""予約投稿ポリシー、プレビュー、原子的な確定トランザクション."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from yt_live_kit.config import Settings
from yt_live_kit.models.upload import (
    UploadChannel,
    UploadContentSnapshot,
    UploadDescriptionRequirementsSnapshot,
    UploadOperation,
)
from yt_live_kit.services.description import (
    DescriptionError,
    ShortsDescriptionRequirements,
    validate_shorts_description_snapshot,
)
from yt_live_kit.services.jobs import start_job
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services._paths import PathConfinementError, confined_path
from yt_live_kit.services.upload_queue import (
    UploadQueueError,
    count_upload_attempts,
    create_reserved_operation,
    list_operations,
    load_operation,
    schedule_lock,
    transition_operation,
    upload_job_target,
)
from yt_live_kit.services.youtube_api import (
    YouTubeAPIError,
    build_upload_snapshot,
    fetch_mine_channel,
)

_DAILY_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", re.ASCII)
_MIN_LEAD = timedelta(minutes=10)


class ScheduleError(Exception):
    """予約投稿の安全な準備・確定に失敗したエラー."""


class SchedulePolicyNotConfigured(ScheduleError):
    """投稿時刻がまだ設定されていない."""


class SchedulePolicy(BaseModel):
    """IANA timezone 上の複数投稿時刻と日間隔."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    daily_times: tuple[str, ...]
    interval_days: int = Field(default=1, ge=1)
    timezone: str = "Asia/Tokyo"

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_daily_time(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        has_legacy = "daily_time" in value
        has_plural = "daily_times" in value
        if has_legacy and has_plural:
            raise ValueError(
                "投稿時刻の daily_time と daily_times は同時に指定できません。"
            )
        if not has_legacy:
            return value
        migrated = dict(value)
        migrated["daily_times"] = (migrated.pop("daily_time"),)
        return migrated

    @field_validator("daily_times", mode="before")
    @classmethod
    def _valid_daily_times(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("投稿時刻一覧は1件以上の配列で指定してください。")
        if not value:
            raise ValueError("投稿時刻一覧は1件以上指定してください。")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or _DAILY_TIME_RE.fullmatch(item) is None:
                raise ValueError(
                    "各投稿時刻は半角数字の HH:MM 形式で指定してください。"
                )
            normalized.append(item)
        if len(set(normalized)) != len(normalized):
            raise ValueError("投稿時刻一覧に重複した時刻は指定できません。")
        return tuple(sorted(normalized))

    @property
    def daily_time(self) -> str:
        """移行期間向けに、正規化済みの先頭投稿枠を返す."""
        return self.daily_times[0]

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise ValueError("存在する IANA timezone を指定してください。") from exc
        return value


class UploadPreview(BaseModel):
    """UI と確定時再検証を結ぶ不変の read-only preview."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_video_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    channel: UploadChannel
    video_path: Path
    file_size: int = Field(gt=0)
    file_mtime_ns: int = Field(ge=0)
    duration_sec: float = Field(ge=10, le=180)
    title: str = Field(min_length=1, max_length=100)
    description: str
    tags: tuple[str, ...]
    # legacy preview は field 無しでも読み込める。P6 UI で新規作成する
    # Shorts preview は build_upload_preview に requirements を渡して凍結する。
    requirements: UploadDescriptionRequirementsSnapshot | None = None
    policy: SchedulePolicy
    publish_at: datetime
    publish_at_utc_z: str
    privacy_status: Literal["private"]
    notify_subscribers: Literal[False]
    attempt_count_la: int = Field(ge=0)
    attempt_limit: int = Field(ge=1, le=100)
    fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("video_path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("動画ファイルは絶対パスで指定してください。")
        return value

    @field_validator("publish_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("予約日時はタイムゾーン付きで指定してください。")
        return value


def _config_path(settings: Settings) -> Path:
    try:
        return confined_path(
            settings.data_dir,
            "_config",
            "schedule_policy.json",
            label="投稿スケジュール設定保存先",
        )
    except PathConfinementError as exc:
        raise ScheduleError(
            "投稿スケジュール設定の保存先を安全に扱えません。"
        ) from exc


def _policy_text(policy: SchedulePolicy) -> str:
    return json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def load_schedule_policy(settings: Settings) -> SchedulePolicy:
    """保存済み policy を読む。未作成は未設定、破損は fail closed."""
    try:
        with schedule_lock(settings):
            path = _config_path(settings)
            try:
                path.stat()
            except FileNotFoundError as exc:
                # stat() だけでは壊れた symlink も未作成と区別できないため、
                # lstat() で path 自体の存在を確認してから未設定を判定する。
                try:
                    path.lstat()
                except FileNotFoundError:
                    raise SchedulePolicyNotConfigured(
                        "投稿スケジュールが未設定です。設定ページで投稿時刻を保存してください。"
                    ) from exc
                except OSError as lstat_exc:
                    raise ScheduleError(
                        "投稿スケジュール設定が壊れているため読み込めません。手動で修復してください。"
                    ) from lstat_exc
                raise ScheduleError(
                    "投稿スケジュール設定が壊れているため読み込めません。手動で修復してください。"
                ) from exc
            except OSError as exc:
                raise ScheduleError(
                    "投稿スケジュール設定が壊れているため読み込めません。手動で修復してください。"
                ) from exc
            raw = json.loads(path.read_text(encoding="utf-8"))
            return SchedulePolicy.model_validate(raw)
    except ScheduleError:
        raise
    except UploadQueueError as exc:
        raise ScheduleError(
            "投稿スケジュール設定の lock を取得できないため読み込めません。"
        ) from exc
    except PathConfinementError as exc:
        raise ScheduleError(
            "投稿スケジュール設定の保存先を安全に扱えません。"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ScheduleError(
            "投稿スケジュール設定が壊れているため読み込めません。手動で修復してください。"
        ) from exc


def save_schedule_policy(policy: SchedulePolicy, settings: Settings) -> Path:
    if not isinstance(policy, SchedulePolicy):
        raise ScheduleError("投稿スケジュールの入力が正しくありません。")
    try:
        with schedule_lock(settings):
            path = _config_path(settings)
            write_text_atomically(path, _policy_text(policy), mode=0o600)
            return path
    except ScheduleError:
        raise
    except UploadQueueError as exc:
        raise ScheduleError(
            "投稿スケジュール設定の lock を取得できないため保存できません。"
        ) from exc
    except PathConfinementError as exc:
        raise ScheduleError(
            "投稿スケジュール設定の保存先を安全に扱えません。"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ScheduleError(
            "投稿スケジュールを安全に保存できませんでした。"
        ) from exc


def make_schedule_policy(
    *,
    daily_time: str | None = None,
    daily_times: tuple[str, ...] | list[str] | None = None,
    interval_days: int,
    timezone_name: str,
) -> SchedulePolicy:
    """UI 入力を検証し、Pydantic の内部詳細を日本語エラーへ正規化する."""
    if daily_time is not None and daily_times is not None:
        raise ScheduleError(
            "投稿時刻の daily_time と daily_times は同時に指定できません。"
        )
    values: dict[str, Any] = {
        "interval_days": interval_days,
        "timezone": timezone_name,
    }
    if daily_time is not None:
        values["daily_time"] = daily_time
    if daily_times is not None:
        values["daily_times"] = daily_times
    try:
        return SchedulePolicy.model_validate(values)
    except ValidationError as exc:
        raise ScheduleError(
            "投稿スケジュールの入力が正しくありません。"
            "時刻一覧は1件以上、重複なしの半角 HH:MM、"
            "間隔は 1 以上の整数、timezone は IANA 名で指定してください。"
        ) from exc


def _validated_local_datetime(
    day: date,
    hour: int,
    minute: int,
    zone: ZoneInfo,
    *,
    second: int = 0,
    microsecond: int = 0,
) -> datetime:
    naive = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        second,
        microsecond,
    )
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive and round_trip.fold == fold:
            candidates.append(candidate)
    offsets = {item.utcoffset() for item in candidates}
    if not candidates:
        raise ScheduleError(
            "投稿時刻が DST により存在しません。別の時刻へ設定してください。"
        )
    if len(offsets) > 1:
        raise ScheduleError(
            "投稿時刻が DST により重複します。曖昧でない時刻へ設定してください。"
        )
    return candidates[0]


def make_requested_publish_at(
    policy: SchedulePolicy,
    publish_date: date,
    publish_time: time,
) -> datetime:
    """UI のローカル日付・時刻を policy timezone の aware datetime にする."""
    if not isinstance(publish_date, date) or isinstance(publish_date, datetime):
        raise ScheduleError("予約日を正しく指定してください。")
    if not isinstance(publish_time, time):
        raise ScheduleError("予約時刻を正しく指定してください。")
    if publish_time.tzinfo is not None and publish_time.utcoffset() is not None:
        raise ScheduleError(
            "予約時刻は日付と現在の timezone を使って指定してください。"
        )
    zone = ZoneInfo(policy.timezone)
    return _validated_local_datetime(
        publish_date,
        publish_time.hour,
        publish_time.minute,
        zone,
        second=publish_time.second,
        microsecond=publish_time.microsecond,
    )


def _validate_requested_slot(
    policy: SchedulePolicy,
    requested_publish_at: datetime,
    existing_reservations: tuple[datetime, ...] | list[datetime],
    *,
    now: datetime,
) -> datetime:
    """手動指定 slot を policy timezone・lead・重複の境界で検証する."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ScheduleError("現在日時はタイムゾーン付きで指定してください。")
    if not isinstance(requested_publish_at, datetime):
        raise ScheduleError("予約日時を正しく指定してください。")
    if (
        requested_publish_at.tzinfo is None
        or requested_publish_at.utcoffset() is None
    ):
        raise ScheduleError("予約日時はタイムゾーン付きで指定してください。")
    zone = ZoneInfo(policy.timezone)
    local = requested_publish_at.astimezone(zone)
    canonical = _validated_local_datetime(
        local.date(),
        local.hour,
        local.minute,
        zone,
        second=local.second,
        microsecond=local.microsecond,
    )
    if canonical.astimezone(timezone.utc) != requested_publish_at.astimezone(timezone.utc):
        raise ScheduleError(
            "予約日時を timezone 上で一意に確定できません。別の時刻を指定してください。"
        )
    if canonical.astimezone(timezone.utc) < now.astimezone(timezone.utc) + _MIN_LEAD:
        raise ScheduleError("予約日時は現在から 10 分以上先を指定してください。")

    occupied: set[datetime] = set()
    for reservation in existing_reservations:
        if reservation.tzinfo is None or reservation.utcoffset() is None:
            raise ScheduleError("既存の予約日時にタイムゾーンがありません。")
        occupied.add(reservation.astimezone(timezone.utc))
    if canonical.astimezone(timezone.utc) in occupied:
        raise ScheduleError(
            "指定した予約日時は既に使われています。別の日時を指定してください。"
        )
    return canonical


def assign_next_slot(
    policy: SchedulePolicy,
    existing_reservations: tuple[datetime, ...] | list[datetime],
    *,
    now: datetime,
) -> datetime:
    """日内の時刻順に、現在から 10 分以上先の非重複 slot を返す."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ScheduleError("現在日時はタイムゾーン付きで指定してください。")
    zone = ZoneInfo(policy.timezone)
    local_now = now.astimezone(zone)
    threshold = now.astimezone(timezone.utc) + _MIN_LEAD
    occupied: set[datetime] = set()
    occupied_local_days = []
    for reservation in existing_reservations:
        if reservation.tzinfo is None or reservation.utcoffset() is None:
            raise ScheduleError("既存の予約日時にタイムゾーンがありません。")
        occupied.add(reservation.astimezone(timezone.utc))
        occupied_local_days.append(reservation.astimezone(zone).date())

    daily_clock_times = tuple(
        tuple(int(part) for part in daily_time.split(":"))
        for daily_time in policy.daily_times
    )
    day = local_now.date()
    if occupied_local_days:
        # 最新の予約日を cadence の位相に使い、現在日以上で最初の同位相日へ
        # 前後どちらからでも整列する。未来の予約だけでも今日が同位相なら戻れる。
        anchor = max(occupied_local_days)
        delta_days = (day - anchor).days
        cadence_steps = -(-delta_days // policy.interval_days)
        day = anchor + timedelta(days=cadence_steps * policy.interval_days)
    for _ in range(36600):
        # その日の全枠を先に検証する。一部の枠だけを暗黙に飛ばすと、
        # 設定どおりの投稿頻度を証明できないため、DST 不正は日単位で fail closed にする。
        candidates = tuple(
            _validated_local_datetime(day, hour, minute, zone)
            for hour, minute in daily_clock_times
        )
        for candidate in candidates:
            candidate_utc = candidate.astimezone(timezone.utc)
            if candidate_utc >= threshold and candidate_utc not in occupied:
                return candidate
        day += timedelta(days=policy.interval_days)
    raise ScheduleError("次の空き予約枠を計算できませんでした。")


def get_next_upload_slot(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[SchedulePolicy, datetime]:
    """UI 初期値用に policy と現在の次の空き枠を同じ lock snapshot で返す."""
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ScheduleError("現在日時はタイムゾーン付きで指定してください。")
    try:
        with schedule_lock(settings):
            policy = load_schedule_policy(settings)
            operations = list_operations(settings)
            slot_holders = _slot_holding_operations(operations)
            slot = assign_next_slot(
                policy,
                [item.content.publish_at for item in slot_holders],
                now=reference,
            )
    except UploadQueueError as exc:
        raise ScheduleError(str(exc)) from exc
    return policy, slot


def to_utc_rfc3339_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleError("予約日時はタイムゾーン付きで指定してください。")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slot_holding_operations(
    operations: tuple[UploadOperation, ...],
) -> tuple[UploadOperation, ...]:
    # failed だけが P2-6 で明示的に slot 解放される。結果不明や upload 済みを
    # 空き扱いにすると、再起動・照合中に同一 slot を二重確定してしまう。
    return tuple(item for item in operations if item.state != "failed")


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def make_content_fingerprint(snapshot: UploadContentSnapshot) -> str:
    """選択値と同意時刻を含む P1 content snapshot の canonical hash."""
    return _canonical_fingerprint(snapshot.model_dump(mode="json"))


_DESCRIPTION_REQUIREMENTS_FIELDS = (
    "generated_description",
    "source_title",
    "source_url",
    "fixed_cta",
    "template_bytes_fingerprint",
    "meta_json_fingerprint",
    "generated_description_occurrences",
    "source_title_occurrences",
    "source_url_occurrences",
    "fixed_cta_line_occurrences",
)


def make_description_requirements_snapshot(
    requirements: ShortsDescriptionRequirements
    | UploadDescriptionRequirementsSnapshot,
) -> UploadDescriptionRequirementsSnapshot:
    """P6-2 の service 要件を永続可能な canonical model へ変換する."""
    if isinstance(requirements, UploadDescriptionRequirementsSnapshot):
        return requirements
    if not isinstance(requirements, ShortsDescriptionRequirements):
        raise ScheduleError("概要欄要件 snapshot の形式が正しくありません。")
    try:
        return UploadDescriptionRequirementsSnapshot.model_validate(
            {
                field: getattr(requirements, field)
                for field in _DESCRIPTION_REQUIREMENTS_FIELDS
            }
        )
    except ValidationError as exc:
        raise ScheduleError("概要欄要件 snapshot の形式が正しくありません。") from exc


def validate_upload_description(
    description: str,
    requirements: ShortsDescriptionRequirements
    | UploadDescriptionRequirementsSnapshot,
) -> None:
    """同じ immutable 要件 snapshot で投稿本文を純粋に再検証する."""
    snapshot = make_description_requirements_snapshot(requirements)
    try:
        validate_shorts_description_snapshot(
            description,
            snapshot.model_dump(mode="python"),
        )
    except DescriptionError as exc:
        raise ScheduleError(str(exc)) from exc


def _preview_payload(preview: UploadPreview | None = None, **values: Any) -> dict[str, Any]:
    if preview is not None:
        values = {
            "source_video_id": preview.source_video_id,
            "source_kind": preview.source_kind,
            "clip_id": preview.clip_id,
            "channel": preview.channel.model_dump(mode="json"),
            "video_path": str(preview.video_path),
            "file_size": preview.file_size,
            "file_mtime_ns": preview.file_mtime_ns,
            "duration_sec": preview.duration_sec,
            "title": preview.title,
            "description": preview.description,
            "tags": list(preview.tags),
            "requirements": (
                preview.requirements.model_dump(mode="json")
                if preview.requirements is not None
                else None
            ),
            "policy": preview.policy.model_dump(mode="json"),
            "publish_at": preview.publish_at.isoformat(),
            "publish_at_utc_z": preview.publish_at_utc_z,
            "privacy_status": preview.privacy_status,
            "notify_subscribers": preview.notify_subscribers,
            "attempt_count_la": preview.attempt_count_la,
            "attempt_limit": preview.attempt_limit,
        }
    return values


def build_upload_preview(
    *,
    source_video_id: str,
    source_kind: str,
    clip_id: str,
    video_path: Path,
    title: str,
    description: str,
    tags: tuple[str, ...] | list[str],
    settings: Settings,
    now: datetime | None = None,
    requested_publish_at: datetime | None = None,
    requirements: ShortsDescriptionRequirements
    | UploadDescriptionRequirementsSnapshot
    | None = None,
) -> UploadPreview:
    """自動または手動 slot で read-only な immutable preview を作る."""
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ScheduleError("現在日時はタイムゾーン付きで指定してください。")
    requirements_snapshot = (
        make_description_requirements_snapshot(requirements)
        if requirements is not None
        else None
    )
    # 概要欄 gate は channels.list、slot / attempt の読み出しより前に通す。
    # confirm 境界でも同じ snapshot を渡すため、mutable な template / meta は
    # ここでは再読込しない。
    if requirements_snapshot is not None:
        validate_upload_description(description, requirements_snapshot)
    try:
        policy = load_schedule_policy(settings)
        operations = list_operations(settings)
        slot_holders = _slot_holding_operations(operations)
        existing_reservations = [item.content.publish_at for item in slot_holders]
        slot = (
            assign_next_slot(policy, existing_reservations, now=reference)
            if requested_publish_at is None
            else _validate_requested_slot(
                policy,
                requested_publish_at,
                existing_reservations,
                now=reference,
            )
        )
        channel = fetch_mine_channel(settings)
        # audience / synthetic は preview では決めない。ここでは共通部分だけを
        # P1 validator に通し、値は UploadPreview へ保存しない。
        checked = build_upload_snapshot(
            channel=channel,
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
            publish_at=slot,
            self_declared_made_for_kids=False,
            contains_synthetic_media=False,
            community_guidelines_confirmed=True,
            community_guidelines_confirmed_at=reference,
            settings=settings,
            now=reference,
        )
        if requirements_snapshot is not None:
            checked = checked.model_copy(
                update={"requirements": requirements_snapshot}
            )
        attempts = count_upload_attempts(settings, now=reference)
    except (UploadQueueError, YouTubeAPIError) as exc:
        raise ScheduleError(str(exc)) from exc
    values = {
        "source_video_id": source_video_id,
        "source_kind": source_kind,
        "clip_id": clip_id,
        "channel": checked.channel.model_dump(mode="json"),
        "video_path": str(checked.video_path),
        "file_size": checked.file_size,
        "file_mtime_ns": checked.file_mtime_ns,
        "duration_sec": checked.duration_sec,
        "title": checked.title,
        "description": checked.description,
        "tags": list(checked.tags),
        "requirements": (
            requirements_snapshot.model_dump(mode="json")
            if requirements_snapshot is not None
            else None
        ),
        "policy": policy.model_dump(mode="json"),
        "publish_at": slot.isoformat(),
        "publish_at_utc_z": to_utc_rfc3339_z(slot),
        "privacy_status": "private",
        "notify_subscribers": False,
        "attempt_count_la": attempts,
        "attempt_limit": settings.video_upload_daily_limit,
    }
    try:
        return UploadPreview.model_validate(
            {
                **values,
                "publish_at": slot,
                "fingerprint": _canonical_fingerprint(values),
            }
        )
    except ValidationError as exc:
        raise ScheduleError("投稿プレビューの内容が正しくありません。入力を確認してください。") from exc


def _same_preview(expected: UploadPreview, actual: UploadPreview) -> bool:
    return expected.fingerprint == actual.fingerprint and _canonical_fingerprint(
        _preview_payload(expected)
    ) == expected.fingerprint


def latest_operation_for_source(
    source_video_id: str,
    clip_id: str,
    settings: Settings,
) -> UploadOperation | None:
    matches = [
        item for item in list_operations(settings)
        if item.source_video_id == source_video_id and item.clip_id == clip_id
    ]
    return max(matches, key=lambda item: (item.created_at, item.operation_id)) if matches else None


def _mark_failed_or_raise_unknown(
    operation_id: str,
    settings: Settings,
    *,
    error: str,
    now: datetime,
) -> None:
    """slot 解放を永続確認できた場合だけ failed として扱う."""
    try:
        transition_operation(
            operation_id,
            "failed",
            settings,
            error=error,
            now=now,
        )
    except UploadQueueError as exc:
        raise ScheduleError(
            "投稿 operation の状態を更新できず、予約枠の状態を確定できません。"
            "自動再送せず、投稿キューを手動修復してください。"
        ) from exc


def confirm_and_start_upload(
    preview: UploadPreview,
    *,
    self_declared_made_for_kids: bool | None,
    contains_synthetic_media: bool | None,
    community_guidelines_confirmed: bool,
    settings: Settings,
    now: datetime | None = None,
    operation_id_factory: Callable[[], str] | None = None,
    job_id_factory: Callable[[], str] | None = None,
    start_job_fn: Callable[..., str] | None = None,
) -> UploadOperation:
    """再検証、単一 record 保存、同一 ID の job 起動を順に行う."""
    if preview.requirements is None:
        raise ScheduleError(
            "旧形式の投稿プレビューは再利用できません。"
            "概要欄要件 snapshot を含む新しいプレビューを作成してください。"
        )
    if type(self_declared_made_for_kids) is not bool:
        raise ScheduleError("子ども向けかどうかを「はい」または「いいえ」で選択してください。")
    if type(contains_synthetic_media) is not bool:
        raise ScheduleError("合成メディアを含むか「はい」または「いいえ」で選択してください。")
    if community_guidelines_confirmed is not True:
        raise ScheduleError("YouTube Community Guidelines への準拠を確認してください。")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ScheduleError("現在日時はタイムゾーン付きで指定してください。")
    # queue / channel / job / attempt の読み出し・保存より前に、confirm 時点の
    # 同じ本文を同じ immutable requirements で再検証する。
    validate_upload_description(preview.description, preview.requirements)

    with schedule_lock(settings):
        current = build_upload_preview(
            source_video_id=preview.source_video_id,
            source_kind=preview.source_kind,
            clip_id=preview.clip_id,
            video_path=preview.video_path,
            title=preview.title,
            description=preview.description,
            tags=preview.tags,
            settings=settings,
            now=reference,
            requested_publish_at=preview.publish_at,
            requirements=preview.requirements,
        )
        if not _same_preview(preview, current):
            raise ScheduleError("確認後に投稿内容・予約枠・試行枠が変わりました。新しく確認してください。")
        operations = list_operations(settings)
        if any(
            item.source_video_id == preview.source_video_id
            and item.clip_id == preview.clip_id
            and item.state != "failed"
            for item in operations
        ):
            raise ScheduleError("同じショートの投稿処理が既に進行中です。")
        if current.attempt_count_la >= current.attempt_limit:
            raise ScheduleError("本日の YouTube upload 試行上限に達しました。")
        try:
            content: UploadContentSnapshot = build_upload_snapshot(
                channel=current.channel,
                video_path=current.video_path,
                title=current.title,
                description=current.description,
                tags=current.tags,
                publish_at=current.publish_at,
                self_declared_made_for_kids=self_declared_made_for_kids,
                contains_synthetic_media=contains_synthetic_media,
                community_guidelines_confirmed=True,
                community_guidelines_confirmed_at=reference,
                settings=settings,
                now=reference,
            )
            if current.requirements is not None:
                content = content.model_copy(
                    update={"requirements": current.requirements}
                )
        except YouTubeAPIError as exc:
            raise ScheduleError(str(exc)) from exc
        content_fingerprint = make_content_fingerprint(content)
        operation_id = (operation_id_factory or (lambda: uuid.uuid4().hex))()
        job_id = (job_id_factory or (lambda: uuid.uuid4().hex))()
        try:
            create_reserved_operation(
                operation_id=operation_id,
                job_id=job_id,
                source_video_id=current.source_video_id,
                source_kind=current.source_kind,
                clip_id=current.clip_id,
                content=content,
                now=reference,
                settings=settings,
            )
        except UploadQueueError as exc:
            raise ScheduleError(str(exc)) from exc

        try:
            operation = load_operation(operation_id, settings)
        except UploadQueueError as exc:
            raise ScheduleError("保存した投稿内容を再確認できませんでした。投稿を停止します。") from exc
        if make_content_fingerprint(operation.content) != content_fingerprint:
            # queue 保存時に選択値・同意時刻を含む snapshot が変わることはない。
            # 万一の schema / serializer 不整合では worker を起動しない。
            _mark_failed_or_raise_unknown(
                operation_id,
                settings,
                error="確認内容を安全に保存できなかったため予約枠を解放しました。",
                now=reference,
            )
            raise ScheduleError("確認内容を安全に保存できませんでした。新しく確認してください。")

    try:
        starter = start_job_fn or start_job
        returned_job_id = starter(
            "upload",
            upload_job_target,
            video_id=preview.source_video_id,
            title=preview.title,
            requested_job_id=job_id,
            operation_id=operation_id,
            settings=settings,
        )
        if returned_job_id != job_id:
            try:
                transition_operation(
                    operation_id,
                    "needs_reconciliation",
                    settings,
                    error="投稿ジョブの開始結果を確定できません。自動再送せず手動照合してください。",
                    now=reference,
                )
            except UploadQueueError:
                pass
            raise ScheduleError("投稿ジョブ ID が予約内容と一致しないため手動照合が必要です。")
    except Exception as exc:
        if isinstance(exc, ScheduleError):
            raise
        _mark_failed_or_raise_unknown(
            operation_id,
            settings,
            error="投稿ジョブを開始できなかったため予約枠を解放しました。新しく確認してください。",
            now=reference,
        )
        raise ScheduleError(
            "投稿ジョブを開始できなかったため予約枠を解放しました。新しく確認してください。"
        ) from exc
    return operation
