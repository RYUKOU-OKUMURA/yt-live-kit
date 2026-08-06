"""TranscriptArtifact の fingerprint・resolver・永続 cache。

S9-2 は Whisper の実行を担当しない。この service は、既存 YouTube VTT と
将来 S9-3 が書き込む whisper.cpp 結果を同じ strict artifact として検証し、
再起動後も壊れた成果物を高精度字幕として返さない境界だけを提供する。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from yt_live_kit.config import WHISPER_ADOPTED_CONTRACT, Settings
from yt_live_kit.models.transcript import (
    ArtifactStatus,
    ResolverUse,
    SCHEMA_VERSION,
    SourceKind,
    TranscriptArtifact,
    TranscriptArtifactRef,
    TranscriptArtifactStatus,
    TranscriptCue,
    TranscriptRange,
    TranscriptResolverUse,
    TranscriptSourceKind,
)
from yt_live_kit.services._fsutil import advisory_lock
from yt_live_kit.services._paths import (
    PathConfinementError,
    confined_video_path,
    safe_video_identifier,
    validate_confined_candidate,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VTT_TIMING_RE = re.compile(
    r"^\s*(?P<start>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})\s+--\>\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})(?:\s+.*)?$"
)
_VTT_TIMESTAMP_LIKE_RE = re.compile(
    r"^\s*\d{1,3}(?::\d{2}){1,2}\.\d{1,3}\b"
)
_ARTIFACT_FILENAME_RE = re.compile(r"^(?P<fingerprint>[0-9a-f]{64})\.json$")
_INDEX_KEYS = frozenset({"schema_version", "video_id", "artifacts", "updated_at"})
_INDEX_ENTRY_KEYS = frozenset(
    {
        "artifact_fingerprint",
        "source_kind",
        "status",
        "cache_identity",
        "cue_digest",
        "path",
    }
)
HALF_OPEN_OVERLAP = "half_open_overlap"


class TranscriptArtifactError(Exception):
    """artifact を安全に作成・検証できないエラー。"""


class TranscriptCacheError(TranscriptArtifactError):
    """永続 cache / index が壊れている、または保存できないエラー。"""


class TranscriptResolutionError(TranscriptArtifactError):
    """resolver の引数が不正なエラー。"""


def canonical_json(value: Any) -> str:
    """sort key・compact・UTF-8 の deterministic JSON を返す。

    ``allow_nan=False`` により NaN / Infinity を fingerprint に混ぜない。
    時刻の float 丸めは行わず、range は別の strict helper で整数 ms に限定する。
    """

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TranscriptArtifactError(
            "字幕 fingerprint の入力を canonical JSON に変換できません。"
        ) from exc


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise TranscriptArtifactError(f"{label} が正しくありません。")
    return value.lower()


def _safe_json_metadata(value: Mapping[str, Any] | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        encoded = json.loads(canonical_json(value))
    except TranscriptArtifactError as exc:
        raise TranscriptArtifactError(f"{label} が JSON として正しくありません。") from exc
    if not isinstance(encoded, dict):
        raise TranscriptArtifactError(f"{label} は JSON object で指定してください。")
    return encoded


def _identity_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """path / mtime のような環境依存値を identity から除外する。

    実体 bytes の digest、model file の digest、runtime build は残す。単に
    path や mtime が変わっただけで cache miss にならないための正規化である。
    """

    if value is None:
        return {}
    ignored_exact = {
        "path",
        "source_path",
        "audio_path",
        "model_path",
        "runtime_path",
        "source_ref",
        "canonical_path",
        "mtime",
        "mtime_ns",
        "modified_at",
    }

    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): clean(child)
                for key, child in item.items()
                if str(key) not in ignored_exact
                and not str(key).endswith("_path")
                and not str(key).endswith("_mtime")
                and not str(key).endswith("_mtime_ns")
            }
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, float) and not math.isfinite(item):
            raise TranscriptArtifactError("fingerprint metadata に有限でない数値があります。")
        return item

    result = clean(value)
    if not isinstance(result, dict):  # pragma: no cover - Mapping input guarantees this
        raise TranscriptArtifactError("fingerprint metadata が object ではありません。")
    # canonical_json が unsupported object / NaN を最終的に拒否する。
    _ = canonical_json(result)
    return result


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TranscriptArtifactError(f"{label} は整数ミリ秒で指定してください。")
    return value


def normalize_range(value: TranscriptRange | Mapping[str, Any] | Sequence[Any]) -> TranscriptRange:
    """range を strict な TranscriptRange へ正規化する。

    Mapping では ``start_ms`` / ``end_ms`` を必須とし、短い fixture のために
    2 要素の integer sequence も許可する。秒 float や文字列は暗黙変換しない。
    """

    if isinstance(value, TranscriptRange):
        return value
    if isinstance(value, Mapping):
        if "start_ms" not in value or "end_ms" not in value:
            raise TranscriptArtifactError("対象区間には start_ms と end_ms が必要です。")
        payload = dict(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) != 2:
            raise TranscriptArtifactError("対象区間は start_ms / end_ms の 2 要素で指定してください。")
        payload = {"start_ms": value[0], "end_ms": value[1]}
    else:
        raise TranscriptArtifactError("対象区間の形式が正しくありません。")

    _strict_int(payload.get("start_ms"), "開始時刻")
    _strict_int(payload.get("end_ms"), "終了時刻")
    try:
        return TranscriptRange.model_validate(payload)
    except ValidationError as exc:
        raise TranscriptArtifactError("対象区間の値が正しくありません。") from exc


def normalize_ranges(
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]],
) -> tuple[TranscriptRange, ...]:
    normalized = tuple(normalize_range(value) for value in ranges)
    if not normalized:
        raise TranscriptArtifactError("対象区間は 1 件以上必要です。")
    return normalized


def _normalize_cue(value: TranscriptCue | Mapping[str, Any]) -> TranscriptCue:
    if isinstance(value, TranscriptCue):
        return value
    if not isinstance(value, Mapping):
        raise TranscriptArtifactError("cue は JSON object で指定してください。")
    payload = dict(value)
    if "start_ms" not in payload or "end_ms" not in payload or "text" not in payload:
        raise TranscriptArtifactError("cue には start_ms、end_ms、text が必要です。")
    _strict_int(payload["start_ms"], "cue 開始時刻")
    _strict_int(payload["end_ms"], "cue 終了時刻")
    try:
        return TranscriptCue.model_validate(payload)
    except ValidationError as exc:
        raise TranscriptArtifactError("cue の値が正しくありません。") from exc


def normalize_cues(cues: Iterable[TranscriptCue | Mapping[str, Any]]) -> tuple[TranscriptCue, ...]:
    return tuple(_normalize_cue(value) for value in cues)


def cue_payload(cues: Iterable[TranscriptCue | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """cue の入力順を維持した canonical payload を返す。"""

    return [
        {
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "text": cue.text,
        }
        for cue in normalize_cues(cues)
    ]


def absolute_cue_digest(cues: Iterable[TranscriptCue | Mapping[str, Any]]) -> str:
    """絶対 start / end・本文・入力順から cue digest を作る。"""

    payload = {"schema_version": SCHEMA_VERSION, "cues": cue_payload(cues)}
    return sha256_bytes(canonical_json_bytes(payload))


cue_digest = absolute_cue_digest
make_cue_digest = absolute_cue_digest
compute_cue_digest = absolute_cue_digest


def _padding_for_range(
    item: TranscriptRange,
    padding: int | Mapping[str, Any] | None,
) -> tuple[int, int]:
    if padding is None:
        return item.effective_padding_before_ms, item.effective_padding_after_ms
    if isinstance(padding, bool):
        raise TranscriptArtifactError("padding は整数ミリ秒で指定してください。")
    if isinstance(padding, int):
        if padding < 0:
            raise TranscriptArtifactError("padding は 0 以上で指定してください。")
        return padding, padding
    if isinstance(padding, Mapping):
        symmetric = padding.get("padding_ms")
        before = padding.get(
            "before_ms",
            padding.get("padding_before_ms", symmetric if symmetric is not None else 0),
        )
        after = padding.get(
            "after_ms",
            padding.get("padding_after_ms", symmetric if symmetric is not None else 0),
        )
        _strict_int(before, "padding 前")
        _strict_int(after, "padding 後")
        if before < 0 or after < 0:
            raise TranscriptArtifactError("padding は 0 以上で指定してください。")
        return before, after
    raise TranscriptArtifactError("padding の形式が正しくありません。")


def _normalize_inclusion_rule(value: str | None) -> str:
    if value is None:
        return HALF_OPEN_OVERLAP
    if not isinstance(value, str) or not value.strip():
        raise TranscriptArtifactError("cue inclusion rule が正しくありません。")
    aliases = {
        "half_open": HALF_OPEN_OVERLAP,
        "overlap": HALF_OPEN_OVERLAP,
        HALF_OPEN_OVERLAP: HALF_OPEN_OVERLAP,
    }
    try:
        return aliases[value.strip()]
    except KeyError as exc:
        raise TranscriptArtifactError("未対応の cue inclusion rule です。") from exc


def _select_cues_for_range(
    cues: tuple[TranscriptCue, ...],
    item: TranscriptRange,
    *,
    before_ms: int,
    after_ms: int,
    inclusion_rule: str,
) -> tuple[TranscriptCue, ...]:
    if inclusion_rule != HALF_OPEN_OVERLAP:  # pragma: no cover - normalized above
        raise TranscriptArtifactError("未対応の cue inclusion rule です。")
    start_ms = item.start_ms - before_ms
    end_ms = item.end_ms + after_ms
    return tuple(
        cue
        for cue in cues
        if cue.start_ms < end_ms and cue.end_ms > start_ms
    )


def used_range_cue_digest(
    cues: Iterable[TranscriptCue | Mapping[str, Any]],
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]],
    *,
    padding: int | Mapping[str, Any] | None = None,
    inclusion_rule: str | None = None,
) -> str:
    """range / padding / inclusion rule / selected absolute cue の digest。

    複数 range は caller の入力順で保持する。cue も sort しないため、同じ本文
    でも時刻や表示順が変わると digest が変わる。半開区間 overlap なので境界
    ちょうどで接する cue は含めない。
    """

    normalized_cues = normalize_cues(cues)
    normalized_ranges = normalize_ranges(ranges)
    selected: list[dict[str, Any]] = []
    range_payload: list[dict[str, Any]] = []
    for item in normalized_ranges:
        before_ms, after_ms = _padding_for_range(item, padding)
        rule = _normalize_inclusion_rule(
            item.inclusion_rule if inclusion_rule is None else inclusion_rule
        )
        selected_cues = _select_cues_for_range(
            normalized_cues,
            item,
            before_ms=before_ms,
            after_ms=after_ms,
            inclusion_rule=rule,
        )
        selected.append({"cues": cue_payload(selected_cues)})
        range_payload.append(
            {
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "padding": {"before_ms": before_ms, "after_ms": after_ms},
                "inclusion_rule": rule,
            }
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "ranges": range_payload,
        "selected": selected,
    }
    return sha256_bytes(canonical_json_bytes(payload))


make_used_range_cue_digest = used_range_cue_digest
compute_used_range_cue_digest = used_range_cue_digest


def _ranges_for_identity(
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]],
) -> list[dict[str, Any]]:
    """identity に使う range 列。入力順を維持し、status / digest は含めない。"""

    return [
        {
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "padding_ms": item.padding_ms,
            "padding_before_ms": item.padding_before_ms,
            "padding_after_ms": item.padding_after_ms,
            "inclusion_rule": _normalize_inclusion_rule(item.inclusion_rule),
        }
        for item in normalize_ranges(ranges)
    ]


def _bytes_identity(value: bytes | bytearray | memoryview | None, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TranscriptArtifactError(f"{label} は実体 bytes で指定してください。")
    content = bytes(value)
    return {"bytes": len(content), "sha256": sha256_bytes(content)}


def make_cache_identity(
    *,
    source_kind: TranscriptSourceKind | str,
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]],
    language: str | None = None,
    source_bytes: bytes | bytearray | memoryview | None = None,
    source_content_sha256: str | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    audio_bytes: bytes | bytearray | memoryview | None = None,
    audio_input_fingerprint: str | None = None,
    sample_rate: int | None = None,
    channel: int | str | None = None,
    codec: str | None = None,
    ffmpeg_settings: Mapping[str, Any] | None = None,
    source: Mapping[str, Any] | None = None,
    model: Mapping[str, Any] | None = None,
    model_fingerprint: str | None = None,
    runtime: Mapping[str, Any] | None = None,
    runtime_fingerprint: str | None = None,
    settings: Mapping[str, Any] | None = None,
    decode_settings: Mapping[str, Any] | None = None,
    initial_prompt: str | None = None,
    padding: int | Mapping[str, Any] | None = None,
    vad: Mapping[str, Any] | None = None,
    output_schema: str | None = None,
) -> str:
    """cache identity を作る。artifact fingerprint とは別の digest。"""

    try:
        kind = TranscriptSourceKind(source_kind)
    except ValueError as exc:
        raise TranscriptArtifactError("source_kind が正しくありません。") from exc
    normalized_ranges = normalize_ranges(ranges)
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise TranscriptArtifactError("language が正しくありません。")
    if padding is not None:
        _padding_for_range(normalized_ranges[0], padding)
    if initial_prompt is not None and not isinstance(initial_prompt, str):
        raise TranscriptArtifactError("initial prompt が正しくありません。")

    source_hash = _bytes_identity(source_bytes, "source bytes")
    if source_content_sha256 is not None:
        source_content_sha256 = _require_digest(source_content_sha256, "source content digest")
    if source_hash is not None and source_content_sha256 is not None:
        if source_hash["sha256"] != source_content_sha256:
            raise TranscriptArtifactError("source bytes と source content digest が一致しません。")
    if source_hash is not None:
        source_content_sha256 = source_hash["sha256"]

    audio_hash = _bytes_identity(audio_bytes, "audio bytes")
    if audio_input_fingerprint is not None:
        audio_input_fingerprint = _require_digest(
            audio_input_fingerprint, "audio input fingerprint"
        )
    if audio_hash is not None and audio_input_fingerprint is not None:
        if audio_hash["sha256"] != audio_input_fingerprint:
            raise TranscriptArtifactError("audio bytes と audio input fingerprint が一致しません。")
    if audio_hash is not None:
        audio_input_fingerprint = audio_hash["sha256"]

    for label, value in (("sample rate", sample_rate), ("channel", channel)):
        if isinstance(value, bool):
            raise TranscriptArtifactError(f"{label} が正しくありません。")
        if value is not None and not isinstance(value, (int, str)):
            raise TranscriptArtifactError(f"{label} が正しくありません。")
    if sample_rate is not None and isinstance(sample_rate, int) and sample_rate <= 0:
        raise TranscriptArtifactError("sample rate は 1 以上で指定してください。")

    # 音声 / source bytes は実体 identity、metadata は path / mtime を除いた値。
    source_payload = {
        "kind": kind.value,
        "language": language,
        "bytes": source_hash,
        "content_sha256": source_content_sha256,
        "metadata": _identity_metadata(source_metadata),
        "source": _identity_metadata(source),
    }
    audio_payload = {
        "bytes": audio_hash,
        "input_fingerprint": audio_input_fingerprint,
        "sample_rate": sample_rate,
        "channel": channel,
        "codec": codec,
        "ffmpeg": _identity_metadata(ffmpeg_settings),
    }
    model_payload = {
        "fingerprint": model_fingerprint,
        "metadata": _identity_metadata(model),
    }
    runtime_payload = {
        "fingerprint": runtime_fingerprint,
        "metadata": _identity_metadata(runtime),
    }
    settings_payload = {
        "settings": _identity_metadata(settings),
        "decode": _identity_metadata(decode_settings),
        "initial_prompt": initial_prompt,
        "padding": _identity_metadata(padding) if isinstance(padding, Mapping) else padding,
        "vad": _identity_metadata(vad),
        "output_schema": output_schema,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_kind": kind.value,
        "source": source_payload,
        "audio": audio_payload,
        "model": model_payload,
        "runtime": runtime_payload,
        "settings": settings_payload,
        "ranges": _ranges_for_identity(normalized_ranges),
    }
    return sha256_bytes(canonical_json_bytes(payload))


cache_identity = make_cache_identity
compute_cache_identity = make_cache_identity


def make_artifact_fingerprint(
    cache_identity_value: str,
    cue_digest_value: str,
    *,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    """cache identity + successful cue digest + schema の artifact fingerprint。"""

    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise TranscriptArtifactError("schema version が正しくありません。")
    cache_identity_value = _require_digest(cache_identity_value, "cache identity")
    cue_digest_value = _require_digest(cue_digest_value, "cue digest")
    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": schema_version,
                "cache_identity": cache_identity_value,
                "cue_digest": cue_digest_value,
            }
        )
    )


artifact_fingerprint = make_artifact_fingerprint
compute_artifact_fingerprint = make_artifact_fingerprint


def _parse_vtt_timestamp_ms(value: str) -> int:
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours_text, minutes, seconds = parts
        hours = int(hours_text)
    else:
        raise TranscriptArtifactError("VTT timestamp が正しくありません。")
    seconds_text, millis_text = seconds.split(".", 1)
    minutes_value = int(minutes)
    seconds_value = int(seconds_text)
    millis_value = int(millis_text)
    if not 0 <= minutes_value <= 59 or not 0 <= seconds_value <= 59:
        raise TranscriptArtifactError("VTT timestamp の範囲が正しくありません。")
    if not 0 <= millis_value <= 999:
        raise TranscriptArtifactError("VTT timestamp の millisecond が正しくありません。")
    return hours * 3_600_000 + minutes_value * 60_000 + seconds_value * 1_000 + millis_value


def parse_vtt_cues(content: str | bytes) -> tuple[TranscriptCue, ...]:
    """既存 VTT を絶対時刻 cue へ変換する read-only parser。"""

    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise TranscriptArtifactError("VTT を UTF-8 として読み取れません。") from exc
    if not isinstance(content, str):
        raise TranscriptArtifactError("VTT は文字列または bytes で指定してください。")
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[TranscriptCue] = []
    index = 0
    skip_block = False
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if skip_block:
            if not line:
                skip_block = False
            continue
        if not line:
            continue
        if line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            # NOTE / STYLE / REGION は空行までが metadata block。中身に
            # timestamp-like な文字列があっても cue timing と解釈しない。
            skip_block = True
            continue
        if line.startswith(("Kind:", "Language:")):
            continue
        if line.isdigit() and index < len(lines):
            line = lines[index].strip()
            index += 1
        match = _VTT_TIMING_RE.fullmatch(line)
        if match is None:
            if "-->" in line or _VTT_TIMESTAMP_LIKE_RE.match(line):
                raise TranscriptArtifactError("VTT に malformed timing block があります。")
            continue
        start_ms = _parse_vtt_timestamp_ms(match.group("start"))
        end_ms = _parse_vtt_timestamp_ms(match.group("end"))
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = " ".join(text_lines).strip()
        # 既存 parser と同じく VTT inline tag を除去するが、cue 時刻は変更しない。
        text = re.sub(r"<[^>]+>", "", text)
        if end_ms <= start_ms:
            raise TranscriptArtifactError("VTT timing block の終了時刻が開始時刻以前です。")
        if not text:
            raise TranscriptArtifactError("VTT timing block の本文が空です。")
        cues.append(TranscriptCue(start_ms=start_ms, end_ms=end_ms, text=text))
    if not cues:
        raise TranscriptArtifactError("VTT に有効な cue がありません。")
    return tuple(cues)


def _source_fingerprint(
    content: bytes,
    *,
    video_id: str,
    language: str,
    source_url: str | None,
    source_metadata: Mapping[str, Any] | None,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "source_kind": SourceKind.YOUTUBE_VTT.value,
                "video_id": video_id,
                "language": language,
                "content_sha256": sha256_bytes(content),
                "source_url": source_url,
                "metadata": _identity_metadata(source_metadata),
            }
        )
    )


def _range_with_padding(
    item: TranscriptRange,
    padding: int | Mapping[str, Any],
) -> TranscriptRange:
    """外部 padding を artifact range へ明示的に凍結する。"""

    before_ms, after_ms = _padding_for_range(item, padding)
    if isinstance(padding, int):
        return item.model_copy(
            update={
                "padding_ms": padding,
                "padding_before_ms": 0,
                "padding_after_ms": 0,
            }
        )
    return item.model_copy(
        update={
            "padding_ms": 0,
            "padding_before_ms": before_ms,
            "padding_after_ms": after_ms,
        }
    )


def build_transcript_artifact(
    *,
    video_id: str,
    source_kind: TranscriptSourceKind | str,
    source_ref: str,
    language: str,
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]],
    cues: Iterable[TranscriptCue | Mapping[str, Any]],
    status: TranscriptArtifactStatus | str = ArtifactStatus.SUCCESS,
    source_url: str | None = None,
    source_fingerprint: str | None = None,
    source_content_sha256: str | None = None,
    source_bytes: bytes | bytearray | memoryview | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    model: Mapping[str, Any] | None = None,
    model_fingerprint: str | None = None,
    runtime: Mapping[str, Any] | None = None,
    runtime_fingerprint: str | None = None,
    settings: Mapping[str, Any] | None = None,
    decode_settings: Mapping[str, Any] | None = None,
    initial_prompt: str | None = None,
    audio_bytes: bytes | bytearray | memoryview | None = None,
    audio_input_fingerprint: str | None = None,
    sample_rate: int | None = None,
    channel: int | str | None = None,
    codec: str | None = None,
    ffmpeg_settings: Mapping[str, Any] | None = None,
    padding: int | Mapping[str, Any] | None = None,
    vad: Mapping[str, Any] | None = None,
    output_schema: str | None = None,
    cache_identity_value: str | None = None,
    created_at: datetime | None = None,
) -> TranscriptArtifact:
    """canonical fingerprint を計算した TranscriptArtifact を構築する。"""

    try:
        kind = TranscriptSourceKind(source_kind)
        artifact_status = TranscriptArtifactStatus(status)
    except ValueError as exc:
        raise TranscriptArtifactError("source_kind または status が正しくありません。") from exc
    normalized_ranges = normalize_ranges(ranges)
    if padding is not None:
        effective_ranges: tuple[TranscriptRange, ...] = tuple(
            _range_with_padding(item, padding) for item in normalized_ranges
        )
    else:
        effective_ranges = normalized_ranges
    normalized_cues = normalize_cues(cues)
    digest = absolute_cue_digest(normalized_cues)

    range_digests: list[str] = []
    ranged: list[TranscriptRange] = []
    for item in effective_ranges:
        before_ms, after_ms = _padding_for_range(item, None)
        selected = _select_cues_for_range(
            normalized_cues,
            item,
            before_ms=before_ms,
            after_ms=after_ms,
            inclusion_rule=_normalize_inclusion_rule(item.inclusion_rule),
        )
        used_digest = used_range_cue_digest(
            normalized_cues,
            (item,),
            padding=None,
            inclusion_rule=item.inclusion_rule,
        )
        range_digests.append(used_digest)
        ranged.append(
            item.model_copy(
                update={
                    "cue_digest": absolute_cue_digest(selected),
                    "used_range_cue_digest": used_digest,
                }
            )
        )
    effective_ranges = tuple(ranged)

    source_bytes_identity = _bytes_identity(source_bytes, "source bytes")
    if source_bytes_identity is not None:
        if (
            source_content_sha256 is not None
            and source_content_sha256.lower() != source_bytes_identity["sha256"]
        ):
            raise TranscriptArtifactError("source bytes と source content digest が一致しません。")
        source_content_sha256 = source_bytes_identity["sha256"]
    if source_content_sha256 is not None:
        source_content_sha256 = _require_digest(source_content_sha256, "source content digest")
    if kind == SourceKind.YOUTUBE_VTT:
        if source_bytes is None:
            raise TranscriptArtifactError("YouTube VTT artifact には source bytes が必要です。")
        # builder の直呼びでも malformed timing block を success artifact
        # として固定できないよう、実 VTT の構造を先に検証する。失敗記録
        # は診断用 bytes を保持できるよう parse を強制しない。
        if artifact_status == TranscriptArtifactStatus.SUCCESS:
            parse_vtt_cues(bytes(source_bytes))
        expected_source_fingerprint = _source_fingerprint(
            bytes(source_bytes),
            video_id=video_id,
            language=language,
            source_url=source_url,
            source_metadata=source_metadata,
        )
        if source_fingerprint is not None and source_fingerprint != expected_source_fingerprint:
            raise TranscriptArtifactError("source fingerprint が VTT bytes / metadata と一致しません。")
        source_fingerprint = expected_source_fingerprint
    if source_fingerprint is not None:
        source_fingerprint = _require_digest(source_fingerprint, "source fingerprint")

    if audio_bytes is not None:
        audio_hash = _bytes_identity(audio_bytes, "audio bytes")
        assert audio_hash is not None
        if (
            audio_input_fingerprint is not None
            and audio_input_fingerprint.lower() != audio_hash["sha256"]
        ):
            raise TranscriptArtifactError("audio bytes と audio input fingerprint が一致しません。")
        audio_input_fingerprint = audio_hash["sha256"]
    if audio_input_fingerprint is not None:
        audio_input_fingerprint = _require_digest(
            audio_input_fingerprint, "audio input fingerprint"
        )

    normalized_model = _safe_json_metadata(model, "model")
    normalized_runtime = _safe_json_metadata(runtime, "runtime")
    normalized_settings = _safe_json_metadata(settings, "settings")
    if model_fingerprint is not None:
        model_fingerprint = _require_digest(model_fingerprint, "model fingerprint")
        if (
            "fingerprint" in normalized_model
            and normalized_model["fingerprint"] != model_fingerprint
        ):
            raise TranscriptArtifactError("model fingerprint と model metadata が一致しません。")
        normalized_model.setdefault("fingerprint", model_fingerprint)
    if runtime_fingerprint is not None:
        runtime_fingerprint = _require_digest(runtime_fingerprint, "runtime fingerprint")
        if (
            "fingerprint" in normalized_runtime
            and normalized_runtime["fingerprint"] != runtime_fingerprint
        ):
            raise TranscriptArtifactError("runtime fingerprint と runtime metadata が一致しません。")
        normalized_runtime.setdefault("fingerprint", runtime_fingerprint)

    normalized_source_metadata = _safe_json_metadata(source_metadata, "source metadata")
    expected_cache_identity = make_cache_identity(
        source_kind=kind,
        language=language,
        ranges=effective_ranges,
        source_bytes=source_bytes,
        source_content_sha256=source_content_sha256,
        source_metadata=normalized_source_metadata,
        audio_bytes=audio_bytes,
        audio_input_fingerprint=audio_input_fingerprint,
        sample_rate=sample_rate,
        channel=channel,
        codec=codec,
        ffmpeg_settings=ffmpeg_settings,
        source={
            "source_ref": source_ref,
            "source_url": source_url,
            "source_fingerprint": source_fingerprint,
        },
        model=normalized_model,
        model_fingerprint=model_fingerprint,
        runtime=normalized_runtime,
        runtime_fingerprint=runtime_fingerprint,
        settings=normalized_settings,
        decode_settings=decode_settings,
        initial_prompt=initial_prompt,
        padding=None,
        vad=vad,
        output_schema=output_schema,
    )

    if cache_identity_value is None:
        cache_identity_value = expected_cache_identity
    else:
        cache_identity_value = _require_digest(cache_identity_value, "cache identity")
        if cache_identity_value != expected_cache_identity:
            raise TranscriptArtifactError(
                "cache identity が source / input / model / runtime / range と一致しません。"
            )
    artifact_fp = make_artifact_fingerprint(cache_identity_value, digest)
    try:
        return TranscriptArtifact(
            schema_version=SCHEMA_VERSION,
            source_kind=kind,
            video_id=video_id,
            source_ref=source_ref,
            source_url=source_url,
            source_fingerprint=source_fingerprint,
            source_content_sha256=source_content_sha256,
            source_metadata=normalized_source_metadata,
            language=language,
            ranges=effective_ranges,
            cues=normalized_cues,
            status=artifact_status,
            model=normalized_model,
            runtime=normalized_runtime,
            settings=normalized_settings,
            audio_input_fingerprint=audio_input_fingerprint,
            cache_identity=cache_identity_value,
            cue_digest=digest,
            used_range_cue_digests=tuple(range_digests),
            artifact_fingerprint=artifact_fp,
            created_at=created_at or datetime.now(timezone.utc),
        )
    except ValidationError as exc:
        raise TranscriptArtifactError("TranscriptArtifact の schema が正しくありません。") from exc


create_transcript_artifact = build_transcript_artifact


def _lstat(path: Path, label: str) -> os.stat_result | None:
    try:
        result = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TranscriptCacheError(f"{label}を安全に確認できません。") from exc
    if stat.S_ISLNK(result.st_mode):
        raise TranscriptCacheError(f"{label}にシンボリックリンクがあるため扱えません。")
    return result


def _check_no_symlink_path(root: Path, path: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise TranscriptCacheError(f"{label}がデータディレクトリ外です。") from exc
    _lstat(root, "データディレクトリ")
    current = root
    for part in relative.parts:
        current = current / part
        result = _lstat(current, label)
        if result is not None and not current.is_dir() and current != path:
            raise TranscriptCacheError(f"{label}の親がディレクトリではありません。")


def _validate_video_path(settings: Settings, video_id: str, *parts: str) -> Path:
    try:
        path = confined_video_path(settings.data_dir, video_id, *parts, label="字幕 artifact 保存先")
        # ``Path.resolve`` は macOS の /tmp -> /private/tmp のような通常の
        # mount alias も変換するため、symlink 検査の lexical root と候補を
        # 同じ ``abspath`` 空間へ揃える。
        root = Path(os.path.abspath(settings.data_dir))
        absolute_path = Path(os.path.abspath(path))
        _check_no_symlink_path(root, absolute_path, "字幕 artifact 保存先")
        return absolute_path
    except PathConfinementError as exc:
        raise TranscriptCacheError(str(exc)) from exc


def _atomic_json_replace(path: Path, payload: Mapping[str, Any]) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise TranscriptCacheError(f"字幕 artifact を atomic 保存できません: {path.name}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail = []
        if missing:
            detail.append(f"不足: {', '.join(missing)}")
        if unknown:
            detail.append(f"未知: {', '.join(unknown)}")
        raise TranscriptCacheError(f"{label} の schema が正しくありません。{' / '.join(detail)}")


def _index_entry(artifact: TranscriptArtifact) -> dict[str, Any]:
    return {
        "artifact_fingerprint": artifact.artifact_fingerprint,
        "source_kind": artifact.source_kind.value,
        "status": artifact.status.value,
        "cache_identity": artifact.cache_identity,
        "cue_digest": artifact.cue_digest,
        "path": f"artifacts/{artifact.artifact_fingerprint}.json",
    }


def _artifact_identity_payload(artifact: TranscriptArtifact) -> dict[str, Any]:
    """同一 fingerprint の cache hit を比較する payload（作成日時を除く）。"""

    payload = artifact.canonical_dict()
    payload.pop("created_at", None)
    # path / mtime は cache identity から除外しているため、同じ artifact
    # fingerprint の再保存でも診断用の場所だけの差で衝突させない。source_ref
    # は URL の場合だけ意味を持ち、filesystem path は data root 内の参照として
    # identity から除外する。
    source_ref = payload.get("source_ref")
    payload["source_ref"] = (
        source_ref
        if isinstance(source_ref, str) and source_ref.startswith(("http://", "https://"))
        else None
    )
    for key in ("source_metadata", "model", "runtime", "settings"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            payload[key] = _identity_metadata(value)
    return payload


def _parse_index(data: Any, video_id: str) -> list[dict[str, Any]]:
    if not isinstance(data, Mapping):
        raise TranscriptCacheError("字幕 artifact index の root が object ではありません。")
    _exact_keys(data, _INDEX_KEYS, "字幕 artifact index")
    if (
        isinstance(data["schema_version"], bool)
        or not isinstance(data["schema_version"], int)
        or data["schema_version"] != SCHEMA_VERSION
        or not isinstance(data["video_id"], str)
        or data["video_id"] != video_id
    ):
        raise TranscriptCacheError("字幕 artifact index の対象または schema が一致しません。")
    if not isinstance(data["updated_at"], str):
        raise TranscriptCacheError("字幕 artifact index の updated_at が正しくありません。")
    try:
        updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise TranscriptCacheError("字幕 artifact index の updated_at が正しくありません。") from exc
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise TranscriptCacheError("字幕 artifact index の updated_at に timezone がありません。")
    artifacts = data["artifacts"]
    if not isinstance(artifacts, list):
        raise TranscriptCacheError("字幕 artifact index の artifacts が配列ではありません。")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in artifacts:
        if not isinstance(entry, Mapping):
            raise TranscriptCacheError("字幕 artifact index の entry が object ではありません。")
        _exact_keys(entry, _INDEX_ENTRY_KEYS, "字幕 artifact index entry")
        fingerprint = entry["artifact_fingerprint"]
        if not isinstance(fingerprint, str) or _ARTIFACT_FILENAME_RE.fullmatch(f"{fingerprint}.json") is None:
            raise TranscriptCacheError("字幕 artifact index の fingerprint が正しくありません。")
        fingerprint = fingerprint.lower()
        if fingerprint in seen:
            raise TranscriptCacheError("字幕 artifact index に重複 artifact があります。")
        seen.add(fingerprint)
        if not isinstance(entry["path"], str) or entry["path"] != f"artifacts/{fingerprint}.json":
            raise TranscriptCacheError("字幕 artifact index の path が fingerprint と一致しません。")
        if not isinstance(entry["source_kind"], str) or entry["source_kind"] not in {
            kind.value for kind in TranscriptSourceKind
        }:
            raise TranscriptCacheError("字幕 artifact index の source_kind が正しくありません。")
        if not isinstance(entry["status"], str) or entry["status"] not in {
            status.value for status in TranscriptArtifactStatus
        }:
            raise TranscriptCacheError("字幕 artifact index の status が正しくありません。")
        _require_digest(entry["cache_identity"], "cache identity")
        _require_digest(entry["cue_digest"], "cue digest")
        entries.append(dict(entry))
    return entries


def _new_index(video_id: str, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "video_id": video_id,
        "artifacts": [dict(entry) for entry in entries],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@dataclass(frozen=True)
class TranscriptResolution:
    """resolver の結果。fallback は高精度 artifact と区別して表示できる。"""

    purpose: TranscriptResolverUse
    artifact: TranscriptArtifact | None
    cache_hit: bool
    is_fallback: bool = False
    fallback_reason: str | None = None
    invalidated: bool = False
    considered_fingerprints: tuple[str, ...] = ()

    @property
    def quality(self) -> str:
        if self.artifact is None:
            return "unavailable"
        if self.is_fallback:
            return "coarse_fallback"
        return "high_precision" if self.artifact.is_high_precision else "coarse"

    @property
    def artifact_fingerprint(self) -> str | None:
        return None if self.artifact is None else self.artifact.artifact_fingerprint

    @property
    def resolved_artifact(self) -> TranscriptArtifact | None:
        """downstream が結果名を明示したい場合の immutable artifact alias。"""

        return self.artifact

    @property
    def fallback(self) -> bool:
        """is_fallback の短い公開 alias。"""

        return self.is_fallback


class TranscriptArtifactStore:
    """video 単位の artifact JSON と lock 付き index の単一 writer。"""

    def __init__(
        self,
        video_id_or_settings: str | Settings | Path | None = None,
        settings_or_video_id: Settings | Path | str | None = None,
        *,
        video_id: str | None = None,
        settings: Settings | Path | None = None,
    ) -> None:
        if video_id is not None or settings is not None:
            if video_id_or_settings is not None or settings_or_video_id is not None:
                raise TypeError("TranscriptArtifactStore は positional または keyword で指定してください。")
            video_id_or_settings = video_id
            settings_or_video_id = settings
        if video_id_or_settings is None or settings_or_video_id is None:
            raise TypeError("TranscriptArtifactStore には video_id と settings が必要です。")
        if isinstance(video_id_or_settings, str):
            video_id = video_id_or_settings
            settings = settings_or_video_id
        else:
            settings = video_id_or_settings
            video_id = settings_or_video_id
        if isinstance(settings, Settings):
            data_dir = settings.data_dir
        else:
            data_dir = Path(settings)
        try:
            self.video_id = safe_video_identifier(video_id, "動画 ID")
        except PathConfinementError as exc:
            raise TranscriptCacheError(str(exc)) from exc
        self.data_dir = Path(os.path.abspath(data_dir))

    @classmethod
    def for_video(cls, video_id: str, settings: Settings | Path) -> "TranscriptArtifactStore":
        return cls(video_id, settings)

    @property
    def video_dir(self) -> Path:
        return _validate_video_path(Settings(data_dir=self.data_dir), self.video_id)

    @property
    def transcripts_dir(self) -> Path:
        return _validate_video_path(Settings(data_dir=self.data_dir), self.video_id, "transcripts")

    @property
    def artifacts_dir(self) -> Path:
        return _validate_video_path(
            Settings(data_dir=self.data_dir), self.video_id, "transcripts", "artifacts"
        )

    @property
    def index_path(self) -> Path:
        return _validate_video_path(
            Settings(data_dir=self.data_dir), self.video_id, "transcripts", "index.json"
        )

    @property
    def lock_path(self) -> Path:
        return _validate_video_path(
            Settings(data_dir=self.data_dir), self.video_id, "transcripts", ".index.lock"
        )

    def _artifact_path(self, fingerprint: str) -> Path:
        fingerprint = _require_digest(fingerprint, "artifact fingerprint")
        return _validate_video_path(
            Settings(data_dir=self.data_dir),
            self.video_id,
            "transcripts",
            "artifacts",
            f"{fingerprint}.json",
        )

    def _ensure_dirs(self) -> None:
        root = self.data_dir
        try:
            # mkdir は既存 symlink を辿るため、作成前にも全親を検査する。
            _check_no_symlink_path(root, self.video_dir, "字幕 artifact 保存先")
            _check_no_symlink_path(root, self.transcripts_dir, "字幕 artifact 保存先")
            _check_no_symlink_path(root, self.artifacts_dir, "字幕 artifact 保存先")
            _check_no_symlink_path(root, self.index_path, "字幕 artifact index")
            _check_no_symlink_path(root, self.lock_path, "字幕 artifact index lock")
            root.mkdir(parents=True, exist_ok=True)
            _lstat(root, "データディレクトリ")
            self.video_dir.mkdir(parents=True, exist_ok=True)
            self.transcripts_dir.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            _check_no_symlink_path(root, self.artifacts_dir, "字幕 artifact 保存先")
            _check_no_symlink_path(root, self.index_path, "字幕 artifact index")
            _check_no_symlink_path(root, self.lock_path, "字幕 artifact index lock")
        except OSError as exc:
            raise TranscriptCacheError("字幕 artifact 保存先を作成できません。") from exc

    @contextmanager
    def _locked(self):
        self._ensure_dirs()
        try:
            with advisory_lock(self.lock_path):
                yield
        except TranscriptArtifactError:
            raise
        except OSError as exc:
            raise TranscriptCacheError("字幕 artifact index の lock を取得できません。") from exc

    def _read_index_unlocked(self) -> list[dict[str, Any]]:
        path = self.index_path
        result = _lstat(path, "字幕 artifact index")
        if result is None:
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TranscriptCacheError("字幕 artifact index が壊れているため読み込めません。") from exc
        return _parse_index(data, self.video_id)

    def _load_artifact_path(self, path: Path) -> TranscriptArtifact:
        _check_no_symlink_path(self.data_dir, path, "字幕 artifact")
        try:
            # model_validate_json は strict schema を維持したまま、保存した
            # enum / tuple / ISO datetime の JSON 表現を正しく復元する。
            artifact = TranscriptArtifact.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise TranscriptCacheError("字幕 artifact が壊れているため高精度結果として使えません。") from exc
        return self._validate_artifact(artifact, path)

    def _validate_artifact(
        self,
        artifact: TranscriptArtifact,
        path: Path | None = None,
    ) -> TranscriptArtifact:
        """保存前・読み込み後で同じ provenance 検証を通す。"""

        if artifact.video_id != self.video_id:
            raise TranscriptCacheError("字幕 artifact の動画 ID が保存先と一致しません。")
        if path is not None and path.name != f"{artifact.artifact_fingerprint}.json":
            raise TranscriptCacheError("字幕 artifact の filename と fingerprint が一致しません。")
        if make_artifact_fingerprint(artifact.cache_identity, artifact.cue_digest) != artifact.artifact_fingerprint:
            raise TranscriptCacheError("字幕 artifact fingerprint を再計算できません。")
        if absolute_cue_digest(artifact.cues) != artifact.cue_digest:
            raise TranscriptCacheError("字幕 artifact の cue digest が内容と一致しません。")
        expected_used = tuple(
            used_range_cue_digest(
                artifact.cues,
                (item,),
                padding=None,
                inclusion_rule=item.inclusion_rule,
            )
            for item in artifact.ranges
        )
        if tuple(artifact.used_range_cue_digests) != expected_used:
            raise TranscriptCacheError("字幕 artifact の used_range_cue_digest が内容と一致しません。")
        for item, expected in zip(artifact.ranges, expected_used):
            before_ms, after_ms = _padding_for_range(item, None)
            selected = _select_cues_for_range(
                artifact.cues,
                item,
                before_ms=before_ms,
                after_ms=after_ms,
                inclusion_rule=_normalize_inclusion_rule(item.inclusion_rule),
            )
            if item.cue_digest != absolute_cue_digest(selected):
                raise TranscriptCacheError("字幕 artifact の区間 cue digest が内容と一致しません。")
            if item.used_range_cue_digest != expected:
                raise TranscriptCacheError("字幕 artifact の区間 used digest が内容と一致しません。")
        self._validate_source_ref(artifact)
        if (
            artifact.source_kind == TranscriptSourceKind.YOUTUBE_VTT
            and artifact.status == TranscriptArtifactStatus.SUCCESS
        ):
            self._validate_vtt_source(artifact)
        return artifact

    def _validate_source_ref(self, artifact: TranscriptArtifact) -> None:
        if artifact.source_ref.startswith(("http://", "https://")):
            return
        source_path = _validate_video_path(
            Settings(data_dir=self.data_dir), self.video_id, *Path(artifact.source_ref).parts
        )
        # 存在しなくても whisper output の参照だけは復元できるが、壊れた
        # symlink を含む全 source path は fail closed。VTT resolver は別途
        # 実体 bytes を再検証する。
        _lstat(source_path, "字幕 source")

    def _validate_vtt_source(self, artifact: TranscriptArtifact) -> None:
        """success VTT の永続 provenance を source 実体と照合する。"""

        if artifact.source_ref.startswith(("http://", "https://")):
            raise TranscriptCacheError("YouTube VTT artifact の source path がありません。")
        source_path = _validate_video_path(
            Settings(data_dir=self.data_dir), self.video_id, *Path(artifact.source_ref).parts
        )
        source_stat = _lstat(source_path, "字幕 source")
        if source_stat is None:
            raise TranscriptCacheError("YouTube VTT artifact の source path が見つかりません。")
        if not stat.S_ISREG(source_stat.st_mode):
            raise TranscriptCacheError("YouTube VTT artifact の source path が通常のファイルではありません。")
        try:
            source_bytes = source_path.read_bytes()
        except (OSError, UnicodeError) as exc:
            raise TranscriptCacheError("YouTube VTT artifact の source bytes を読み込めません。") from exc
        try:
            parse_vtt_cues(source_bytes)
        except TranscriptArtifactError as exc:
            raise TranscriptCacheError("YouTube VTT artifact の source が有効な VTT ではありません。") from exc
        actual_content_sha256 = sha256_bytes(source_bytes)
        if artifact.source_content_sha256 != actual_content_sha256:
            raise TranscriptCacheError("YouTube VTT artifact の VTT bytes fingerprint が実体と一致しません。")
        expected_source_fingerprint = _source_fingerprint(
            source_bytes,
            video_id=artifact.video_id,
            language=artifact.language,
            source_url=artifact.source_url,
            source_metadata=artifact.source_metadata,
        )
        if artifact.source_fingerprint != expected_source_fingerprint:
            raise TranscriptCacheError("YouTube VTT artifact の source fingerprint が実体と一致しません。")

    def _rebuild_index_unlocked(self) -> list[dict[str, Any]]:
        self._ensure_dirs()
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(self.artifacts_dir.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise TranscriptCacheError("字幕 artifact directory を読み込めません。") from exc
        for path in children:
            if path.name.startswith(".") or path.suffix != ".json":
                continue
            match = _ARTIFACT_FILENAME_RE.fullmatch(path.name)
            if match is None:
                # 手動配置された別名 / 偽 fingerprint は index に入れない。
                continue
            try:
                artifact = self._load_artifact_path(path)
            except TranscriptCacheError:
                # 壊れた artifact は捨てず、resolver の候補から除外する。
                continue
            entries.append(_index_entry(artifact))
        entries.sort(key=lambda item: item["artifact_fingerprint"])
        _atomic_json_replace(self.index_path, _new_index(self.video_id, entries))
        return entries

    def rebuild_index(self) -> tuple[str, ...]:
        with self._locked():
            return tuple(
                entry["artifact_fingerprint"]
                for entry in self._rebuild_index_unlocked()
            )

    def _entries_with_recovery(self) -> list[dict[str, Any]]:
        self._ensure_dirs()
        try:
            entries = self._read_index_unlocked()
        except TranscriptCacheError:
            entries = self._rebuild_index_unlocked()
        valid: list[dict[str, Any]] = []
        dirty = False
        indexed_fingerprints: set[str] = set()
        for entry in entries:
            try:
                artifact = self._load_artifact_path(self._artifact_path(entry["artifact_fingerprint"]))
            except TranscriptCacheError:
                dirty = True
                continue
            actual = _index_entry(artifact)
            if actual != entry:
                dirty = True
            valid.append(actual)
            indexed_fingerprints.add(actual["artifact_fingerprint"])

        # artifact replace 後に index replace だけが失敗した crash では、index
        # 自体は syntactically valid なまま orphan が残る。独立検証できる
        # orphan だけを取り込むことで再起動後に cache を安全に復元する。
        try:
            artifact_paths = sorted(self.artifacts_dir.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise TranscriptCacheError("字幕 artifact directory を読み込めません。") from exc
        for path in artifact_paths:
            match = _ARTIFACT_FILENAME_RE.fullmatch(path.name)
            if match is None or match.group("fingerprint") in indexed_fingerprints:
                continue
            try:
                artifact = self._load_artifact_path(path)
            except TranscriptCacheError:
                continue
            valid.append(_index_entry(artifact))
            indexed_fingerprints.add(artifact.artifact_fingerprint)
            dirty = True
        if dirty:
            valid.sort(key=lambda item: item["artifact_fingerprint"])
            _atomic_json_replace(self.index_path, _new_index(self.video_id, valid))
        return valid

    def load_artifact(self, artifact_fingerprint_value: str) -> TranscriptArtifact:
        return self._load_artifact_path(self._artifact_path(artifact_fingerprint_value))

    def load(self, artifact_fingerprint_value: str) -> TranscriptArtifact:
        """load_artifact の downstream 向け短い alias。"""

        return self.load_artifact(artifact_fingerprint_value)

    def artifact_ref(self, artifact: TranscriptArtifact | str) -> TranscriptArtifactRef:
        """保存済み artifact の相対 immutable reference を返す。"""

        loaded = (
            artifact
            if isinstance(artifact, TranscriptArtifact)
            else self.load_artifact(artifact)
        )
        if loaded.video_id != self.video_id:
            raise TranscriptCacheError("artifact reference の動画 ID が一致しません。")
        return TranscriptArtifactRef(
            video_id=self.video_id,
            artifact_fingerprint=loaded.artifact_fingerprint,
            source_kind=loaded.source_kind,
            path=f"transcripts/artifacts/{loaded.artifact_fingerprint}.json",
        )

    def list_artifacts(self) -> tuple[TranscriptArtifact, ...]:
        with self._locked():
            entries = self._entries_with_recovery()
            artifacts: list[TranscriptArtifact] = []
            for entry in entries:
                try:
                    artifacts.append(
                        self._load_artifact_path(self._artifact_path(entry["artifact_fingerprint"]))
                    )
                except TranscriptCacheError:
                    continue
            return tuple(artifacts)

    def find_by_cache_identity(self, cache_identity_value: str) -> tuple[TranscriptArtifact, ...]:
        cache_identity_value = _require_digest(cache_identity_value, "cache identity")
        return tuple(
            artifact
            for artifact in self.list_artifacts()
            if artifact.cache_identity == cache_identity_value
        )

    def save(self, artifact: TranscriptArtifact) -> Path:
        if not isinstance(artifact, TranscriptArtifact):
            raise TranscriptCacheError("保存する字幕 artifact の型が正しくありません。")
        if artifact.video_id != self.video_id:
            raise TranscriptCacheError("字幕 artifact の動画 ID が保存先と一致しません。")
        expected_fp = make_artifact_fingerprint(artifact.cache_identity, artifact.cue_digest)
        if expected_fp != artifact.artifact_fingerprint:
            raise TranscriptCacheError("字幕 artifact fingerprint が入力内容と一致しません。")
        # Validation は path / source ref / cue digest を再検証する。
        path = self._artifact_path(artifact.artifact_fingerprint)
        self._validate_artifact(artifact, path)
        payload = artifact.canonical_dict()
        with self._locked():
            # source path は VTT の immutable input なので、外側の検証後に
            # bytes が差し替わっていないことも writer lock 下で再確認する。
            self._validate_artifact(artifact, path)
            existing = _lstat(path, "字幕 artifact")
            if existing is not None:
                loaded = self._load_artifact_path(path)
                candidate = TranscriptArtifact.model_validate(payload)
                if canonical_json(_artifact_identity_payload(loaded)) != canonical_json(
                    _artifact_identity_payload(candidate)
                ):
                    raise TranscriptCacheError("同じ artifact fingerprint に異なる内容が存在します。")
            else:
                _atomic_json_replace(path, payload)

            try:
                entries = self._read_index_unlocked()
            except TranscriptCacheError:
                entries = self._rebuild_index_unlocked()
            by_fingerprint = {
                entry["artifact_fingerprint"]: entry
                for entry in entries
            }
            by_fingerprint[artifact.artifact_fingerprint] = _index_entry(artifact)
            ordered = [by_fingerprint[key] for key in sorted(by_fingerprint)]
            _atomic_json_replace(self.index_path, _new_index(self.video_id, ordered))
        return path

    def save_artifact(self, artifact: TranscriptArtifact) -> Path:
        """save の downstream 向け明示 alias。"""

        return self.save(artifact)

    def save_vtt(
        self,
        *,
        vtt_path: Path | None = None,
        content: bytes | None = None,
        language: str = "ja",
        source_url: str | None = None,
        source_metadata: Mapping[str, Any] | None = None,
        source_fingerprint: str | None = None,
    ) -> tuple[TranscriptArtifact, bool]:
        """canonical ja.vtt / source VTT を read-only に読み、artifact を保存する。"""

        canonical_path = _validate_video_path(
            Settings(data_dir=self.data_dir), self.video_id, "subtitles", "ja.vtt"
        )
        selected_path = vtt_path or canonical_path
        try:
            selected_path = validate_confined_candidate(
                self.data_dir, selected_path, label="字幕 source"
            )
        except PathConfinementError as exc:
            raise TranscriptCacheError(str(exc)) from exc
        selected_path = Path(os.path.abspath(selected_path))
        _check_no_symlink_path(self.data_dir, selected_path, "字幕 source")
        if content is not None and not isinstance(content, bytes):
            raise TranscriptCacheError("字幕 source content は bytes で指定してください。")
        source_exists = _lstat(selected_path, "字幕 source") is not None
        # 明示 path と content の二重入力では、path の実体を必ず再読込して
        # 完全一致を確認する。canonical path がまだ無い content-only fallback
        # は後方互換のため許可するが、既存 path がある場合は同じ検証を行う。
        should_compare_content = content is not None and (
            vtt_path is not None or source_exists
        )
        if should_compare_content:
            try:
                resolved_content = selected_path.read_bytes()
            except OSError as exc:
                raise TranscriptCacheError("字幕 source path を読み込めません。") from exc
            if resolved_content != content:
                raise TranscriptCacheError(
                    "字幕 source path の実体 bytes と content が一致しません。"
                )
        if content is None:
            try:
                content = selected_path.read_bytes()
            except OSError as exc:
                raise TranscriptCacheError("字幕 source を読み込めません。") from exc
        cues = parse_vtt_cues(content)
        end_ms = max(cue.end_ms for cue in cues)
        ranges = (TranscriptRange(start_ms=0, end_ms=max(end_ms, 1)),)
        relative_ref = str(selected_path.relative_to(self.data_dir / self.video_id))
        source_fp = source_fingerprint or _source_fingerprint(
            content,
            video_id=self.video_id,
            language=language,
            source_url=source_url,
            source_metadata=source_metadata,
        )
        cache_fp = make_cache_identity(
            source_kind=SourceKind.YOUTUBE_VTT,
            language=language,
            ranges=ranges,
            source_bytes=content,
            source_metadata=source_metadata,
            source={
                "source_ref": relative_ref,
                "source_url": source_url,
                "source_fingerprint": source_fp,
            },
        )
        artifact = build_transcript_artifact(
            video_id=self.video_id,
            source_kind=SourceKind.YOUTUBE_VTT,
            source_ref=relative_ref,
            source_url=source_url,
            source_fingerprint=source_fp,
            language=language,
            ranges=ranges,
            cues=cues,
            source_bytes=content,
            source_metadata=source_metadata,
            cache_identity_value=cache_fp,
        )
        if not source_exists:
            # content-only は既存 resolver の一時 fallback としてだけ返す。
            # source path が無い success artifact は永続 cache に保存しない。
            return artifact, False
        hit = bool(self.find_by_cache_identity(cache_fp))
        self.save(artifact)
        return artifact, hit


class TranscriptResolver:
    """用途別 resolver の薄い facade。"""

    def __init__(self, video_id: str, settings: Settings | Path) -> None:
        self.store = TranscriptArtifactStore(video_id, settings)

    @property
    def video_id(self) -> str:
        return self.store.video_id

    def coarse_search(
        self,
        *,
        vtt_path: Path | None = None,
        content: bytes | None = None,
        language: str = "ja",
        source_url: str | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResolution:
        try:
            artifact, hit = self.store.save_vtt(
                vtt_path=vtt_path,
                content=content,
                language=language,
                source_url=source_url,
                source_metadata=source_metadata,
            )
        except TranscriptArtifactError as exc:
            return TranscriptResolution(
                purpose=ResolverUse.COARSE_SEARCH,
                artifact=None,
                cache_hit=False,
                fallback_reason=str(exc),
            )
        if (
            artifact.source_kind != SourceKind.YOUTUBE_VTT
            or artifact.status != TranscriptArtifactStatus.SUCCESS
            or any(item.status != TranscriptArtifactStatus.SUCCESS for item in artifact.ranges)
        ):
            return TranscriptResolution(
                purpose=ResolverUse.COARSE_SEARCH,
                artifact=None,
                cache_hit=hit,
                fallback_reason="有効な YouTube VTT artifact ではありません。",
            )
        return TranscriptResolution(
            purpose=ResolverUse.COARSE_SEARCH,
            artifact=artifact,
            cache_hit=hit,
        )

    def selected_range(
        self,
        ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]],
        *,
        cache_identity_value: str | None = None,
        expected_cache_identity_value: str | None = None,
        used_range_cue_digests: Sequence[str] | None = None,
        model: Mapping[str, Any] | None = None,
        runtime: Mapping[str, Any] | None = None,
        settings: Mapping[str, Any] | None = None,
        expected_settings: Mapping[str, Any] | None = None,
        audio_input_fingerprint: str | None = None,
        vtt_path: Path | None = None,
        vtt_content: bytes | None = None,
        language: str = "ja",
        source_url: str | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResolution:
        normalized_ranges = normalize_ranges(ranges)
        if expected_settings is not None:
            if settings is not None:
                raise TranscriptResolutionError(
                    "selected_range の expected settings が重複指定されています。"
                )
            settings = expected_settings
        if expected_cache_identity_value is not None:
            if cache_identity_value is not None:
                raise TranscriptResolutionError(
                    "selected_range の expected cache identity が重複指定されています。"
                )
            cache_identity_value = expected_cache_identity_value
        expected_cache = (
            None
            if cache_identity_value is None
            else _require_digest(cache_identity_value, "cache identity")
        )
        expected_used = (
            None
            if used_range_cue_digests is None
            else tuple(_require_digest(value, "used_range_cue_digest") for value in used_range_cue_digests)
        )
        try:
            expected_model = _identity_metadata(model) if isinstance(model, Mapping) else {}
            expected_runtime = _identity_metadata(runtime) if isinstance(runtime, Mapping) else {}
            expected_settings = _identity_metadata(settings) if isinstance(settings, Mapping) else {}
        except TranscriptArtifactError:
            expected_model = {}
            expected_runtime = {}
            expected_settings = {}
        try:
            expected_audio = (
                _require_digest(audio_input_fingerprint, "audio input fingerprint")
                if isinstance(audio_input_fingerprint, str)
                else None
            )
        except TranscriptArtifactError:
            expected_audio = None
        provenance_ready = bool(language) and all(
            (
                bool(expected_model),
                bool(expected_runtime),
                bool(expected_settings),
                expected_audio is not None,
            )
        )
        identity_ready = (
            expected_cache is not None
            and expected_used is not None
            and len(expected_used) == len(normalized_ranges)
        )
        considered: list[str] = []
        valid: list[TranscriptArtifact] = []
        invalidated = False
        for artifact in self.store.list_artifacts():
            considered.append(artifact.artifact_fingerprint)
            if artifact.source_kind != SourceKind.WHISPER_CPP:
                continue
            if len(artifact.ranges) != len(normalized_ranges):
                continue
            same_ranges = not any(
                (
                    left.start_ms,
                    left.end_ms,
                    left.padding_ms,
                    left.padding_before_ms,
                    left.padding_after_ms,
                    left.inclusion_rule,
                )
                != (
                    right.start_ms,
                    right.end_ms,
                    right.padding_ms,
                    right.padding_before_ms,
                    right.padding_after_ms,
                    right.inclusion_rule,
                )
                for left, right in zip(artifact.ranges, normalized_ranges)
            )
            if not same_ranges:
                continue
            if not artifact.is_high_precision:
                invalidated = True
                continue
            if not provenance_ready or not identity_ready or artifact.language != language:
                continue
            if expected_cache is not None and artifact.cache_identity != expected_cache:
                invalidated = True
                continue
            if expected_used is not None and tuple(artifact.used_range_cue_digests) != expected_used:
                invalidated = True
                continue
            if canonical_json(_identity_metadata(artifact.model)) != canonical_json(expected_model):
                invalidated = True
                continue
            if canonical_json(_identity_metadata(artifact.runtime)) != canonical_json(expected_runtime):
                invalidated = True
                continue
            if canonical_json(_identity_metadata(artifact.settings)) != canonical_json(expected_settings):
                invalidated = True
                continue
            if artifact.audio_input_fingerprint != expected_audio:
                invalidated = True
                continue
            valid.append(artifact)

        if valid:
            valid.sort(key=lambda item: item.artifact_fingerprint)
            return TranscriptResolution(
                purpose=ResolverUse.SELECTED_RANGE,
                artifact=valid[0],
                cache_hit=True,
                considered_fingerprints=tuple(considered),
            )

        coarse = TranscriptResolver(self.video_id, self.store.data_dir).coarse_search(
            vtt_path=vtt_path,
            content=vtt_content,
            language=language,
            source_url=source_url,
            source_metadata=source_metadata,
        )
        reason = (
            "selected_range の高精度解決には language、model、runtime、settings、"
            "audio input fingerprint の expected provenance が必要です。"
            if not provenance_ready
            else "selected_range の高精度解決には expected cache identity と ordered used_range_cue_digests が必要です。"
            if not identity_ready
            else "要求された全区間が success の Whisper artifact と一致しません。"
        )
        if coarse.artifact is None:
            reason += f" {coarse.fallback_reason or '粗い字幕 fallback も利用できません。'}"
        return TranscriptResolution(
            purpose=ResolverUse.SELECTED_RANGE,
            artifact=coarse.artifact,
            cache_hit=coarse.cache_hit,
            is_fallback=coarse.artifact is not None,
            fallback_reason=reason,
            invalidated=invalidated,
            considered_fingerprints=tuple(considered),
        )

    def resolve(
        self,
        purpose: TranscriptResolverUse | str,
        *,
        ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]] | None = None,
        **kwargs: Any,
    ) -> TranscriptResolution:
        try:
            use = TranscriptResolverUse(purpose)
        except ValueError as exc:
            raise TranscriptResolutionError("resolver の用途が正しくありません。") from exc
        if use == ResolverUse.COARSE_SEARCH:
            return self.coarse_search(**kwargs)
        if ranges is None:
            raise TranscriptResolutionError("selected_range には対象区間が必要です。")
        return self.selected_range(ranges, **kwargs)


def artifact_path(
    video_id: str,
    artifact_fingerprint_value: str,
    settings: Settings | Path,
) -> Path:
    return TranscriptArtifactStore(video_id, settings)._artifact_path(
        artifact_fingerprint_value
    )


def index_path(video_id: str, settings: Settings | Path) -> Path:
    return TranscriptArtifactStore(video_id, settings).index_path


def save_transcript_artifact(
    artifact: TranscriptArtifact,
    settings: Settings | Path,
) -> Path:
    return TranscriptArtifactStore(artifact.video_id, settings).save(artifact)


store_transcript_artifact = save_transcript_artifact


def reference_artifact_for_current_capability(
    stored: TranscriptArtifact,
    *,
    model: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
    settings: Mapping[str, Any] | None = None,
) -> TranscriptArtifact:
    """保存済み artifact を、現在の adopted contract 相当 provenance に差し替えた参照を返す。"""

    if not isinstance(stored, TranscriptArtifact):
        raise TranscriptArtifactError("失効判定には TranscriptArtifact が必要です。")
    contract = WHISPER_ADOPTED_CONTRACT
    updated_model = dict(stored.model)
    if model is not None:
        updated_model.update(dict(model))
    else:
        updated_model["name"] = contract.model_name
        updated_model["sha256"] = contract.model_sha256
        updated_model["fingerprint"] = contract.model_sha256
    updated_runtime = dict(stored.runtime)
    if runtime is not None:
        updated_runtime.update(dict(runtime))
    else:
        updated_runtime["binary_sha256"] = contract.binary_sha256
        updated_runtime["version"] = contract.binary_version
    if settings is not None:
        updated_settings = dict(settings)
    else:
        updated_settings = {
            "language": contract.language,
            "initial_prompt": contract.initial_prompt,
            "output_schema": contract.output_schema,
            "padding_ms": contract.padding_ms,
            "vad": contract.vad,
            "decode": contract.decode,
        }
    return stored.model_copy(
        update={
            "model": updated_model,
            "runtime": updated_runtime,
            "settings": updated_settings,
        }
    )


def should_invalidate_against_current_capability(
    stored: TranscriptArtifact,
    *,
    model: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
    settings: Mapping[str, Any] | None = None,
) -> bool:
    """保存済み artifact が現在の capability / adopted contract と一致しなければ True。"""

    reference = reference_artifact_for_current_capability(
        stored,
        model=model,
        runtime=runtime,
        settings=settings,
    )
    return should_invalidate_used_range(stored, reference)


def stored_artifact_lineage_is_current(
    *,
    video_id: str,
    artifact_ref: TranscriptArtifactRef,
    artifact_fingerprint: str,
    used_range_cue_digests: Sequence[str],
    settings: Settings,
    model: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
    capability_settings: Mapping[str, Any] | None = None,
) -> bool:
    """immutable artifact ref と保存実体・adopted contract が一致するか判定する。"""

    try:
        store = TranscriptArtifactStore(video_id, settings)
        artifact = store.load_artifact(artifact_fingerprint)
        actual_ref = store.artifact_ref(artifact)
    except (TranscriptArtifactError, OSError, ValueError):
        return False
    if (
        actual_ref != artifact_ref
        or artifact.video_id != video_id
        or artifact.artifact_fingerprint != artifact_fingerprint
        or tuple(artifact.used_range_cue_digests) != tuple(used_range_cue_digests)
        or not artifact.is_high_precision
    ):
        return False
    return not should_invalidate_against_current_capability(
        artifact,
        model=model,
        runtime=runtime,
        settings=capability_settings,
    )


def should_invalidate_used_range(
    previous: TranscriptArtifact,
    current: TranscriptArtifact,
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]] | None = None,
) -> bool:
    """artifact provenance と使用範囲 digest を分離した失効判定。"""

    if not isinstance(previous, TranscriptArtifact) or not isinstance(current, TranscriptArtifact):
        raise TranscriptArtifactError("失効判定には TranscriptArtifact が必要です。")
    if previous.source_kind != current.source_kind:
        return True
    if previous.cache_identity != current.cache_identity:
        return True
    if canonical_json(_identity_metadata(previous.model)) != canonical_json(_identity_metadata(current.model)):
        return True
    if canonical_json(_identity_metadata(previous.runtime)) != canonical_json(_identity_metadata(current.runtime)):
        return True
    if canonical_json(_identity_metadata(previous.settings)) != canonical_json(_identity_metadata(current.settings)):
        return True
    if ranges is None:
        return previous.used_range_cue_digests != current.used_range_cue_digests
    normalized_ranges = normalize_ranges(ranges)
    return used_range_cue_digest(previous.cues, normalized_ranges) != used_range_cue_digest(
        current.cues, normalized_ranges
    )


def invalidation_reason(
    previous: TranscriptArtifact,
    current: TranscriptArtifact,
    ranges: Iterable[TranscriptRange | Mapping[str, Any] | Sequence[Any]] | None = None,
) -> str | None:
    if should_invalidate_used_range(previous, current, ranges):
        return "使用範囲の artifact provenance または cue digest が変更されました。"
    return None
