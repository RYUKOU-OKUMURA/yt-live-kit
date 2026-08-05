"""T1-1 の人手 line-onset annotation packet と validator。

production data / artifact / cache は読取専用で扱い、gold を推測・補完しない。
再生用 PCM と packet は一時・隔離領域へだけ書き込む。
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping
import uuid
import wave


MANIFEST_SCHEMA = "t1-1-timing-spike-manifest-v3"
PACKET_SCHEMA = "t1-1-human-audio-annotation-packet-v1"
RESULT_SCHEMA = "t1-1-timing-spike-result-v1"
FORMAL_MANIFEST_FREEZE_COMMIT = "d152230"
PRODUCTION_AFTER_ARTIFACT = "docs/benchmarks/t1-1-production-hash-after.json"
PRODUCTION_AFTER_ARTIFACT_BYTES = 2798
PRODUCTION_AFTER_ARTIFACT_SHA256 = "9fa4de94e03eb8d250d1e39297923e294f9217016aca5e6494cee53abd153d26"
PRODUCTION_DATA_ROOT = "/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data"
RESULT_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "benchmark_id",
        "manifest_fingerprint",
        "manifest_freeze_commit",
        "status",
        "measurement_status",
        "decision",
        "go",
        "no_go",
        "t1_1_complete",
        "t1_2_allowed",
        "ac_40_update_allowed",
        "fixture_counts",
        "limits",
        "human_gold",
        "timing_inputs",
        "ffmpeg_smoke",
        "concat_playback_smoke",
        "metrics",
        "production_integrity",
        "reason",
    }
)
REQUIRED_GROUPS = ("long_single_cue", "multi_cross_cue", "vtt_fallback_concat")
PACKET_GOLD_PROVENANCE = "human_audio_listening_line_onset_only"
GOLD_FIELDS = frozenset(
    {
        "line_onset_ms",
        "timebase",
        "annotator_id",
        "annotated_at",
        "audio_listened",
    }
)
GOLD_PLACEHOLDER = {
    "line_onset_ms": None,
    "timebase": "source_audio_relative_ms",
    "annotator_id": None,
    "annotated_at": None,
    "audio_listened": False,
}
MUTABLE_PACKET_FIELDS = frozenset({"gold", "gold_provenance"})
PACKET_ROW_IMMUTABLE_FIELDS = frozenset(
    {
        "row_id",
        "fixture_group",
        "audio_source_id",
        "source_span",
        "target_text",
    }
)
PACKET_ROW_FIELDS = PACKET_ROW_IMMUTABLE_FIELDS | MUTABLE_PACKET_FIELDS
PACKET_SOURCE_FIELDS = frozenset(
    {
        "source_id",
        "source_content_kind",
        "source_content_path",
        "source_content_bytes",
        "source_content_sha256",
    }
)
PACKET_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "benchmark_id",
        "manifest_fingerprint",
        "status",
        "annotation_contract",
        "sources",
        "rows",
        "playback_receipts",
    }
)
RECEIPT_FIELDS = frozenset(
    {
        "manifest_fingerprint",
        "row_id",
        "audio_source_id",
        "source_content_sha256",
        "target_text",
        "row_source_span",
        "source_span",
        "played_from_ms",
        "played_duration_ms",
        "playback_wav_path",
        "playback_wav_sha256",
        "playback_wav_bytes",
        "playback_format",
        "recorded_at",
    }
)
WAV_SINGLE_KINDS = frozenset({"single_source_audio"})
WAV_CONCAT_KINDS = frozenset({"concatenated_source_audio"})
VIDEO_SINGLE_KINDS = frozenset({"single_source_video_audio"})
VIDEO_CONCAT_KINDS = frozenset({"concatenated_source_video_audio"})
MANUAL_SPLIT_BOUNDARIES = {
    "やばい、止まってないね": "やばい",
}
MANUAL_SPLIT_DELIMITERS = {
    "やばい、止まってないね": "、",
}
MANUAL_SPLIT_ORIGINAL_TUPLE_IDS = frozenset(
    {
        "lb4_e1ff:s4:l2:1113149-1116110",
    }
)
KNOWN_LOW_CONFIDENCE_ROW_IDS = frozenset(
    {"t1-fallback-017", "t1-fallback-018", "t1-fallback-019", "t1-fallback-020"}
)
KNOWN_MULTI_LOW_CONFIDENCE_ROW_IDS = frozenset(
    {"t1-multi-021", "t1-multi-022", "t1-multi-023", "t1-multi-024"}
)
KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS = frozenset(
    {
        "gza_f0:s1:l5:3494040-3498000",
        "gza_f0:s1:l6:3498000-3502000",
        "hpe_8ad:s2:l6:3604000-3608000",
        "hpe_8ad:s3:l3:3625460-3630000",
    }
)
ASS_FALLBACK_EVIDENCE = {
    "asset_id": "lb4_b5d345c4379e",
    "canonical_clip_id": "b5d345c4379e",
    "ass_path": "/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/LB4px1wRFnY/shorts/subtitles/short_b5d345c4379e.ass",
    "ass_bytes": 4638,
    "ass_sha256": "cde04f97ae351c77738e73673103d209de9c61f266547cf62d317b119341026a",
    "vtt_path": "/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/LB4px1wRFnY/subtitles/ja.vtt",
    "vtt_bytes": 353340,
    "vtt_sha256": "cc6d7fe8f89ffe3ae22f411ece80dcba7c9ab48f96b90b13fa21e7b4216c3fb2",
    "cutplan_path": "/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/LB4px1wRFnY/shorts/cutplan/cut_clip_003.json",
    "cutplan_bytes": 1123,
    "cutplan_sha256": "e2fc48665b85aae24164f163d12682442ec19150c702196dff30868f225f296d",
    "ffmpeg_log_path": "/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/LB4px1wRFnY/shorts/output/short_b5d345c4379e.ffmpeg.log",
    "ffmpeg_log_bytes": 12512,
    "ffmpeg_log_sha256": "fc11f894d5f3475f31bc68e360f293b639e51f887b3843c3ddf5f47b4cbd1e02",
    "source_content_path": "/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/LB4px1wRFnY/clips/source/LB4px1wRFnY.mp4",
    "source_content_bytes": 502876028,
    "source_content_sha256": "5f8e8187fd3520da8ae6186f851995346eb8213123a456fdeb608ead00476aed",
    "source_ids": (
        "lb4_b5d-cut_001-audio",
        "lb4_b5d-cut_002-audio",
        "lb4_b5d-cut_003-audio",
    ),
    "segments": (
        {"cut_id": "cut_001", "start_ms": 3700000, "end_ms": 3721000, "duration_ms": 21000},
        {"cut_id": "cut_002", "start_ms": 4015000, "end_ms": 4052000, "duration_ms": 37000},
        {"cut_id": "cut_003", "start_ms": 4086000, "end_ms": 4100000, "duration_ms": 14000},
    ),
    "telop_script": None,
    "reproduction": {
        "method": "isolated_temp_copy_vtt_build_concatenated_subtitle_telop_script_none",
        "subtitle_font": "Hiragino Sans",
        "generated_bytes": 4638,
        "generated_sha256": "cde04f97ae351c77738e73673103d209de9c61f266547cf62d317b119341026a",
        "byte_for_byte_match": True,
        "production_write": False,
    },
    "dialogues": (
        {
            "event_index": 2,
            "concat_start_ms": 600,
            "concat_end_ms": 5230,
            "source_absolute_start_ms": 3700600,
            "source_absolute_end_ms": 3705230,
            "text": "の人が使っちゃダメなやつっすね。",
            "vtt_cue_index": 1198,
            "vtt_start_ms": 3700599,
            "vtt_end_ms": 3705230,
        },
        {
            "event_index": 12,
            "concat_start_ms": 21120,
            "concat_end_ms": 23870,
            "source_absolute_start_ms": 4015120,
            "source_absolute_end_ms": 4017870,
            "text": "トクン効率自体は良くなってるらしいんす",
            "vtt_cue_index": 1322,
            "vtt_start_ms": 4015119,
            "vtt_end_ms": 4017870,
        },
        {
            "event_index": 35,
            "concat_start_ms": 58280,
            "concat_end_ms": 61430,
            "source_absolute_start_ms": 4086280,
            "source_absolute_end_ms": 4089430,
            "text": "じゃあ同じ200ドルを払うとして",
            "vtt_cue_index": 1358,
            "vtt_start_ms": 4086279,
            "vtt_end_ms": 4089430,
        },
        {
            "event_index": 41,
            "concat_start_ms": 69760,
            "concat_end_ms": 72000,
            "source_absolute_start_ms": 4097760,
            "source_absolute_end_ms": 4100000,
            "text": "今んところチャットGPT優勢なんだよな",
            "vtt_cue_index": 1364,
            "vtt_start_ms": 4097759,
            "vtt_end_ms": 4101950,
        },
    ),
}
ALIGNMENT_LONG_TUPLE_IDS = frozenset(
    {
        "gza_415:s1:l1:3984000-3987000",
        "gza_415:s1:l2:3987000-3991000",
        "gza_415:s1:l3:3991000-3995000",
        "gza_415:s2:l2:4002760-4006500",
        "gza_415:s2:l3:4006500-4011000",
        "gza_415:s2:l4:4011000-4016140",
        "gza_415:s3:l1:4038000-4043000",
        "gza_415:s3:l2:4043000-4047000",
        "gza_415:s3:l3:4047000-4052000",
        "gza_415:s3:l4:4052000-4057000",
        "gza_415:s3:l5:4057000-4060500",
        "gza_415:s3:l6:4060500-4064000",
        "gza_f0:s2:l1:3502000-3506000",
        "gza_f0:s2:l2:3506000-3510000",
        "gza_f0:s2:l3:3510000-3515000",
        "gza_f0:s2:l4:3515000-3520000",
        "gza_f0:s2:l5:3520000-3525000",
        "gza_f0:s2:l6:3525000-3530000",
        "hpe_8ad:s3:l1:3611000-3616000",
        "hpe_8ad:s3:l2:3616000-3623540",
    }
)
ALIGNMENT_MULTI_TUPLE_IDS = frozenset(
    {
        "gza_415:s2:l1:3998000-3999380",
        "gza_415:s2:l5:4016140-4020000",
        "gza_415:s2:l6:4020000-4024720",
        "gza_415:s2:l7:4025420-4030500",
        "gza_415:s2:l8:4030500-4036000",
        "gza_f0:s1:l1:3477000-3480500",
        "gza_f0:s1:l2:3480500-3483940",
        "gza_f0:s1:l3:3485500-3489500",
        "gza_f0:s1:l4:3489500-3493460",
        "hpe_8ad:s1:l1:3498000-3503000",
        "hpe_8ad:s1:l2:3503000-3510000",
        "hpe_8ad:s1:l3:3510000-3514000",
        "hpe_8ad:s1:l4:3514000-3517000",
        "hpe_8ad:s2:l1:3581000-3588000",
        "hpe_8ad:s2:l2:3588000-3591000",
        "hpe_8ad:s2:l3:3591000-3595000",
        "hpe_8ad:s2:l4:3595000-3600000",
        "hpe_8ad:s2:l5:3600000-3604000",
        "hpe_8ad:s3:l4:3630000-3633000",
        "hpe_8ad:s3:l5:3633000-3638000",
    }
)


class AnnotationError(ValueError):
    """入力・manifest・packet が契約に違反した。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"JSONを読み込めません: {path}") from exc
    if not isinstance(value, dict):
        raise AnnotationError(f"JSON object が必要です: {path}")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    _assert_isolated_packet_path(path)
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _assert_no_symlink_chain(path: Path) -> None:
    raw = path.expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    # macOS commonly exposes the canonical temporary roots through /tmp or
    # /var symlink aliases.  Those OS-owned aliases are safe to canonicalize;
    # every symlink below them, including the packet or playback directory,
    # remains fail-closed.
    os_aliases = {Path("/tmp"), Path("/var")}
    current = raw
    while True:
        if current.is_symlink() and current not in os_aliases:
            raise AnnotationError(f"packet path または親 directory に symlink は使用できません: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _assert_isolated_packet_path(path: Path) -> None:
    """packet の出力先を一時領域へ限定し、production/repo 書込みを防ぐ。"""

    _assert_no_symlink_chain(path)
    resolved = path.expanduser().resolve()
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if not any(resolved == root or root in resolved.parents for root in temp_roots):
        raise AnnotationError(
            f"packet の出力先は一時ディレクトリ配下に限定されます: {resolved}"
        )
    forbidden = (
        Path(__file__).resolve().parents[2],
        Path("/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data"),
        Path("/Users/ryukouokumura/Library/Caches"),
    )
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise AnnotationError(f"packet の出力先が保護領域です: {resolved}")


def _playback_directory(packet_path: Path) -> Path:
    _assert_isolated_packet_path(packet_path)
    resolved_packet = packet_path.expanduser().resolve()
    playback_directory = resolved_packet.parent / f".{resolved_packet.name}.playback"
    _assert_isolated_packet_path(playback_directory)
    return playback_directory


def _playback_wav_path(packet_path: Path, row_id: str) -> Path:
    if not isinstance(row_id, str) or not row_id:
        raise AnnotationError("playback row_id が空です。")
    playback_directory = _playback_directory(packet_path)
    playback_path = playback_directory / f"{row_id}.wav"
    _assert_isolated_packet_path(playback_path)
    return playback_path


def _new_playback_staging_path(packet_path: Path, row_id: str) -> Path:
    playback_directory = _playback_directory(packet_path)
    playback_directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{row_id}.{uuid.uuid4().hex}.",
        suffix=".wav.tmp",
        dir=playback_directory,
    )
    os.close(descriptor)
    staging_path = Path(name)
    _assert_isolated_packet_path(staging_path)
    return staging_path


def _final_playback_path(packet_path: Path, row_id: str, wav_sha256: str) -> Path:
    if not isinstance(row_id, str) or not row_id or not _is_sha256_hex(wav_sha256):
        raise AnnotationError("final playback path の row_id / SHA-256 が不正です。")
    playback_directory = _playback_directory(packet_path)
    playback_directory.mkdir(parents=True, exist_ok=True)
    final_path = playback_directory / f"{row_id}.{wav_sha256}.{uuid.uuid4().hex}.wav"
    _assert_isolated_packet_path(final_path)
    if final_path.exists():
        raise AnnotationError("unique final playback WAV が既に存在します。上書きしません。")
    return final_path


def _validate_packet_playback_path(packet_path: Path, row_id: str, playback_path: Path) -> None:
    playback_directory = _playback_directory(packet_path).resolve()
    resolved = playback_path.expanduser().resolve()
    if resolved.parent != playback_directory:
        raise AnnotationError(f"{row_id} の playback WAV path が packet sibling と不一致です。")
    fixed_name = f"{row_id}.wav"
    content_addressed_prefix = f"{row_id}."
    if not (
        resolved.name == fixed_name
        or (
            resolved.name.startswith(content_addressed_prefix)
            and resolved.name.endswith(".wav")
            and "/" not in resolved.name
        )
    ):
        raise AnnotationError(f"{row_id} の playback WAV pathが世代別allowlistと不一致です。")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AnnotationError(f"入力ファイルを読めません: {path}") from exc
    return digest.hexdigest()


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _strip_external_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _strip_external_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_external_paths(item) for item in value]
    if isinstance(value, str) and value.startswith("/"):
        return "__external_path__"
    return value


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    body = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_fingerprint"
    }
    encoded = json.dumps(
        _strip_external_paths(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AnnotationError(f"{label} は {minimum} 以上の整数が必要です。")
    return value


def _source_by_id(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    sources = manifest.get("sources")
    if not isinstance(sources, Mapping):
        raise AnnotationError("manifest.sources がありません。")
    result: dict[str, Mapping[str, Any]] = {}
    for value in sources.values():
        if not isinstance(value, Mapping) or not isinstance(value.get("source_id"), str):
            raise AnnotationError("manifest.sources の source_id が不正です。")
        source_id = value["source_id"]
        if source_id in result:
            raise AnnotationError(f"source_id が重複しています: {source_id}")
        result[source_id] = value
    return result


def _packet_sources(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    packet_sources: dict[str, dict[str, Any]] = {}
    for source_key, source in manifest.get("sources", {}).items():
        if not isinstance(source_key, str) or not isinstance(source, Mapping):
            raise AnnotationError("manifest.sources の packet source が不正です。")
        if any(field not in source for field in PACKET_SOURCE_FIELDS):
            raise AnnotationError(f"{source_key} の playback source 必須fieldが不足しています。")
        packet_sources[source_key] = {
            field: copy.deepcopy(source[field]) for field in sorted(PACKET_SOURCE_FIELDS)
        }
    return packet_sources


def _source_content_path(source: Mapping[str, Any]) -> Path:
    path = source.get("source_content_path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise AnnotationError("source_content_path は絶対パスが必要です。")
    return Path(path)


def _source_content_kind(source: Mapping[str, Any]) -> str:
    kind = source.get("source_content_kind")
    if kind not in {"wav_cache", "source_mp4"}:
        raise AnnotationError(f"未知の source_content_kind です: {kind}")
    return str(kind)


def _source_span_origin(source: Mapping[str, Any]) -> tuple[int, int, int]:
    origin = source.get("audio_span_origin_ms")
    duration = source.get("audio_duration_ms")
    if not isinstance(origin, Mapping) or not isinstance(duration, int) or duration <= 0:
        raise AnnotationError("source audio origin / duration が不正です。")
    start = _require_int(origin.get("start_ms"), label="audio_span_origin.start_ms")
    end = _require_int(origin.get("end_ms"), label="audio_span_origin.end_ms")
    if end <= start or end - start != duration:
        raise AnnotationError("source audio duration が origin span と一致しません。")
    return start, end, duration


def _span_kind_family(span: Mapping[str, Any]) -> str:
    kind = span.get("kind")
    if kind in WAV_SINGLE_KINDS or kind in WAV_CONCAT_KINDS:
        return "wav"
    if kind in VIDEO_SINGLE_KINDS or kind in VIDEO_CONCAT_KINDS:
        return "video"
    raise AnnotationError(f"未知の source_span.kind です: {kind}")


def _parts_and_duration(span: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], int]:
    kind = span.get("kind")
    if kind in WAV_SINGLE_KINDS or kind in VIDEO_SINGLE_KINDS:
        start = _require_int(span.get("start_ms"), label="source_span.start_ms")
        end = _require_int(span.get("end_ms"), label="source_span.end_ms")
        if end <= start:
            raise AnnotationError("source span の end は start より後が必要です。")
        return [{"start_ms": start, "end_ms": end, "concat_offset_ms": 0}], end - start
    if kind not in WAV_CONCAT_KINDS and kind not in VIDEO_CONCAT_KINDS:
        raise AnnotationError(f"未知の source_span.kind です: {kind}")
    duration = _require_int(span.get("duration_ms"), label="source_span.duration_ms")
    parts = span.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 3:
        raise AnnotationError("concat source span は1〜3 part必要です。")
    normalized: list[Mapping[str, Any]] = []
    expected_offset = 0
    total = 0
    for part in parts:
        if not isinstance(part, Mapping):
            raise AnnotationError("concat part が不正です。")
        start = _require_int(part.get("start_ms"), label="concat.start_ms")
        end = _require_int(part.get("end_ms"), label="concat.end_ms")
        offset = _require_int(part.get("concat_offset_ms"), label="concat.concat_offset_ms")
        if end <= start or offset != expected_offset:
            raise AnnotationError("concat part の span / offset が不正です。")
        normalized.append(
            {"start_ms": start, "end_ms": end, "concat_offset_ms": offset}
        )
        total += end - start
        expected_offset += end - start
    if total != duration:
        raise AnnotationError("concat duration が part 合計と一致しません。")
    return normalized, duration


def _row_duration_ms(row: Mapping[str, Any]) -> int:
    span = row.get("source_span")
    if not isinstance(span, Mapping):
        raise AnnotationError(f"{row.get('row_id', 'row')} の source_span がありません。")
    return _parts_and_duration(span)[1]


def _check_source_file(
    path_text: Any,
    expected_bytes: Any,
    expected_sha256: Any,
    *,
    label: str,
) -> None:
    if isinstance(path_text, Path):
        path = path_text
    elif isinstance(path_text, str) and path_text.startswith("/"):
        path = Path(path_text)
    else:
        raise AnnotationError(f"{label} の path は絶対パスが必要です。")
    if not isinstance(expected_bytes, int) or not isinstance(expected_sha256, str):
        raise AnnotationError(f"{label} の bytes / SHA-256 が不正です。")
    if not path.is_file():
        raise AnnotationError(f"{label} が存在しません: {path}")
    if path.stat().st_size != expected_bytes or sha256_file(path) != expected_sha256:
        raise AnnotationError(f"{label} の bytes / SHA-256 が manifest と一致しません: {path}")


def _check_canonical_source(source: Mapping[str, Any], *, label: str = "source") -> None:
    _check_source_file(
        _source_content_path(source),
        source.get("source_content_bytes"),
        source.get("source_content_sha256"),
        label=label,
    )


def _expected_ass_evidence() -> dict[str, Any]:
    """Return the JSON-shaped immutable evidence contract for the b5d clip."""

    return json.loads(json.dumps(ASS_FALLBACK_EVIDENCE, ensure_ascii=False))


def ass_evidence_integrity_contract(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return the four-file integrity binding for an existing ASS/VTT fixture."""

    normalized = json.loads(json.dumps(evidence, ensure_ascii=False))
    return {
        "asset_id": normalized["asset_id"],
        "canonical_clip_id": normalized["canonical_clip_id"],
        "count": 4,
        "files": [
            {
                "role": "ass",
                "path": normalized["ass_path"],
                "bytes": normalized["ass_bytes"],
                "sha256": normalized["ass_sha256"],
            },
            {
                "role": "vtt",
                "path": normalized["vtt_path"],
                "bytes": normalized["vtt_bytes"],
                "sha256": normalized["vtt_sha256"],
            },
            {
                "role": "cutplan",
                "path": normalized["cutplan_path"],
                "bytes": normalized["cutplan_bytes"],
                "sha256": normalized["cutplan_sha256"],
            },
            {
                "role": "ffmpeg_log",
                "path": normalized["ffmpeg_log_path"],
                "bytes": normalized["ffmpeg_log_bytes"],
                "sha256": normalized["ffmpeg_log_sha256"],
            },
        ],
        "reproduction": copy.deepcopy(normalized["reproduction"]),
    }


def ass_evidence_rehash_contract(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return a structured post-measurement rehash obligation for b5d evidence."""

    return {
        "required": True,
        "status_at_freeze": "pending_candidate_measurement",
        "fail_closed_on_mismatch": True,
        "binding": ass_evidence_integrity_contract(evidence),
    }


def _parse_ass_timestamp(value: str, *, label: str) -> int:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", value.strip())
    if match is None:
        raise AnnotationError(f"{label} のASS timestampが不正です: {value}")
    hours, minutes, seconds, centiseconds = (int(part) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise AnnotationError(f"{label} のASS timestampが不正です: {value}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + centiseconds * 10


def _parse_ass_dialogues(path: Path) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AnnotationError(f"ASSを読めません: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            raise AnnotationError(f"ASS Dialogue のfield数が不正です: {path}:{line_number}")
        start = _parse_ass_timestamp(fields[1], label=f"{path}:{line_number}:start")
        end = _parse_ass_timestamp(fields[2], label=f"{path}:{line_number}:end")
        if end <= start:
            raise AnnotationError(f"ASS Dialogue の終了が開始以前です: {path}:{line_number}")
        dialogues.append(
            {
                "event_index": len(dialogues) + 1,
                "concat_start_ms": start,
                "concat_end_ms": end,
                "text": fields[9],
            }
        )
    return dialogues


_VTT_TIMESTAMP_RE = re.compile(
    r"^(?:(\d{2}):)?(\d{2}):(\d{2})\.(\d{3})\s+-->\s+"
    r"(?:(\d{2}):)?(\d{2}):(\d{2})\.(\d{3})"
)


def _parse_vtt_evidence_cues(path: Path) -> list[dict[str, Any]]:
    """Reproduce production VTT cleaning and progressive-delta semantics locally."""

    try:
        lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    except OSError as exc:
        raise AnnotationError(f"VTTを読めません: {path}") from exc

    def timestamp(hours: str | None, minutes: str, seconds: str, millis: str) -> int:
        return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)

    raw_cues: list[dict[str, Any]] = []
    index = 0
    line_index = 0
    while line_index < len(lines):
        match = _VTT_TIMESTAMP_RE.match(lines[line_index].strip())
        if match is None:
            line_index += 1
            continue
        start = timestamp(*match.group(1, 2, 3, 4))
        end = timestamp(*match.group(5, 6, 7, 8))
        line_index += 1
        text_lines: list[str] = []
        while line_index < len(lines) and lines[line_index].strip():
            text_lines.append(lines[line_index].strip())
            line_index += 1
        text = " ".join(text_lines)
        text = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", text)
        text = re.sub(r"<c>([^<]*)</c>", r"\1", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = " ".join(text.split())
        if not text or end <= start:
            continue
        raw_cues.append({"start_ms": start, "end_ms": end, "text": text})

    deduplicated: list[dict[str, Any]] = []
    previous_text = ""
    for cue in raw_cues:
        text = cue["text"].strip()
        if previous_text:
            if text == previous_text:
                continue
            if text.startswith(previous_text):
                delta = text[len(previous_text) :].strip()
                if delta:
                    deduplicated.append({**cue, "text": delta})
                previous_text = text
                continue
            if previous_text in text:
                position = text.find(previous_text)
                delta = (text[:position] + text[position + len(previous_text) :]).strip()
                if delta:
                    deduplicated.append({**cue, "text": delta})
                previous_text = text
                continue
        deduplicated.append(cue)
        previous_text = text
    for index, cue in enumerate(deduplicated, 1):
        cue["cue_index"] = index
    return deduplicated


def _validate_ass_evidence_files(evidence: Mapping[str, Any]) -> None:
    expected = _expected_ass_evidence()
    normalized_evidence = json.loads(json.dumps(evidence, ensure_ascii=False))
    if normalized_evidence != expected:
        raise AnnotationError("b5d VTT/ASS evidence contract が固定値と一致しません。")
    evidence = normalized_evidence
    for key, label in (
        ("ass_path", "b5d ASS"),
        ("vtt_path", "b5d VTT"),
        ("cutplan_path", "b5d cutplan"),
        ("ffmpeg_log_path", "b5d ffmpeg log"),
        ("source_content_path", "b5d source MP4"),
    ):
        _check_source_file(evidence[key], evidence[f"{key.removesuffix('_path')}_bytes"], evidence[f"{key.removesuffix('_path')}_sha256"], label=label)
    ass_dialogues = _parse_ass_dialogues(Path(evidence["ass_path"]))
    expected_dialogues = evidence["dialogues"]
    if not isinstance(expected_dialogues, list) or len(expected_dialogues) != 4:
        raise AnnotationError("b5d selected ASS dialogue が4件固定ではありません。")
    for expected_dialogue in expected_dialogues:
        matches = [
            event
            for event in ass_dialogues
            if event["event_index"] == expected_dialogue["event_index"]
        ]
        if len(matches) != 1 or any(
            matches[0][key] != expected_dialogue[key]
            for key in ("concat_start_ms", "concat_end_ms", "text")
        ):
            raise AnnotationError("b5d selected ASS Dialogue event が実体と不一致です。")
    cues = _parse_vtt_evidence_cues(Path(evidence["vtt_path"]))
    for expected_dialogue in expected_dialogues:
        matches = [
            cue
            for cue in cues
            if cue["cue_index"] == expected_dialogue["vtt_cue_index"]
            and cue["start_ms"] == expected_dialogue["vtt_start_ms"]
            and cue["end_ms"] == expected_dialogue["vtt_end_ms"]
            and cue["text"] == expected_dialogue["text"]
        ]
        if len(matches) != 1:
            raise AnnotationError("b5d selected VTT cue がASS Dialogueと一意対応しません。")
    segments = evidence.get("segments")
    if not isinstance(segments, list) or len(segments) != 3:
        raise AnnotationError("b5d concat segment は3 part固定です。")
    expected_clip_id = hashlib.sha256(
        "|".join(
            f"{segment['start_ms']}-{segment['end_ms']}"
            for segment in segments
        ).encode("utf-8")
    ).hexdigest()[:12]
    if evidence.get("canonical_clip_id") != expected_clip_id:
        raise AnnotationError("b5d canonical clip id がsegment absolute spanから導出した値と不一致です。")
    cumulative_offsets: list[int] = []
    cumulative = 0
    for segment in segments:
        cumulative_offsets.append(cumulative)
        cumulative += segment["duration_ms"]
    try:
        ffmpeg_log = Path(evidence["ffmpeg_log_path"]).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AnnotationError("b5d ffmpeg logを読めません。") from exc
    ass_name = Path(evidence["ass_path"]).name
    if ass_name not in ffmpeg_log or evidence["canonical_clip_id"] not in ffmpeg_log or "concat.mp4" not in ffmpeg_log:
        raise AnnotationError("b5d ffmpeg log が同一ASS / canonical concat clipの入力証跡を含みません。")
    for expected_dialogue in expected_dialogues:
        ass_start = expected_dialogue["concat_start_ms"]
        ass_end = expected_dialogue["concat_end_ms"]
        source_start = expected_dialogue["source_absolute_start_ms"]
        source_end = expected_dialogue["source_absolute_end_ms"]
        target_index = next(
            (
                index
                for index, (segment, offset) in enumerate(zip(segments, cumulative_offsets))
                if offset <= ass_start < offset + segment["duration_ms"]
                and offset < ass_end <= offset + segment["duration_ms"]
            ),
            None,
        )
        if target_index is None:
            raise AnnotationError("b5d ASS dialogueが単一partへcontainされません。")
        segment = segments[target_index]
        offset = cumulative_offsets[target_index]
        derived_start = segment["start_ms"] + ass_start - offset
        derived_end = segment["start_ms"] + ass_end - offset
        if source_start != derived_start or source_end != derived_end:
            raise AnnotationError("b5d ASS concat時刻からabsolute source時刻へのmappingが不一致です。")
        if abs(expected_dialogue["vtt_start_ms"] - source_start) > 5:
            raise AnnotationError("b5d VTT startとabsolute source startのcentisecond差が許容範囲外です。")
        expected_vtt_end = min(expected_dialogue["vtt_end_ms"], segment["end_ms"])
        if source_end != expected_vtt_end:
            raise AnnotationError("b5d VTT endとcut clamp後のabsolute source endが不一致です。")
    try:
        cutplan = json.loads(Path(evidence["cutplan_path"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError("b5d cutplan JSONを読めません。") from exc
    if cutplan.get("parent_id") != "clip_003" or cutplan.get("parent_start_ms") != 3440000 or cutplan.get("parent_end_ms") != 4100000:
        raise AnnotationError("b5d cutplan parent contract が不一致です。")
    expected_cutplan = {
        "cut_001": ("01:01:40", "01:02:01"),
        "cut_002": ("01:06:55", "01:07:32"),
        "cut_003": ("01:08:06", "01:08:20"),
    }
    candidates = {item.get("id"): item for item in cutplan.get("candidates", []) if isinstance(item, Mapping)}
    for segment in evidence["segments"]:
        candidate = candidates.get(segment["cut_id"])
        if not isinstance(candidate, Mapping) or candidate.get("start") != expected_cutplan[segment["cut_id"]][0] or candidate.get("end") != expected_cutplan[segment["cut_id"]][1] or candidate.get("duration_sec") * 1000 != segment["duration_ms"]:
            raise AnnotationError("b5d cutplan segment が固定source spanと不一致です。")


def _validate_ass_evidence_contract(manifest: Mapping[str, Any], *, check_sources: bool) -> Mapping[str, Any]:
    evidence = manifest.get("vtt_fallback_evidence")
    if not isinstance(evidence, Mapping):
        raise AnnotationError("vtt_fallback_evidence がありません。")
    expected = _expected_ass_evidence()
    normalized = json.loads(json.dumps(evidence, ensure_ascii=False))
    if normalized != expected:
        raise AnnotationError("vtt_fallback_evidence が固定ASS/VTT契約と一致しません。")
    if check_sources:
        _validate_ass_evidence_files(normalized)
    return normalized


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_repository_relative_path(
    path_text: Any,
    *,
    label: str,
    repository_root: Path | None = None,
) -> Path:
    """Resolve an artifact path without permitting absolute or escaping paths."""

    if not isinstance(path_text, str) or not path_text or "\x00" in path_text:
        raise AnnotationError(f"{label} は空でないrepo相対pathが必要です。")
    relative = Path(path_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise AnnotationError(f"{label} はrepo相対pathでなければなりません: {path_text}")
    root = (repository_root or _repository_root()).resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AnnotationError(f"{label} がrepository root外を指しています: {path_text}") from exc
    if resolved == root:
        raise AnnotationError(f"{label} がrepository root自身を指しています。")
    return resolved


def _snapshot_files(snapshot: Mapping[str, Any], *, label: str, expected_count: int) -> dict[str, Any]:
    files = snapshot.get("files")
    if not isinstance(files, Mapping) or len(files) != expected_count:
        raise AnnotationError(f"{label} の15-file snapshot件数が不一致です。")
    normalized: dict[str, Any] = {}
    for relative_name, entry in files.items():
        if (
            not isinstance(relative_name, str)
            or not relative_name
            or Path(relative_name).is_absolute()
            or ".." in Path(relative_name).parts
            or not isinstance(entry, Mapping)
            or set(entry) != {"bytes", "sha256"}
            or isinstance(entry.get("bytes"), bool)
            or not isinstance(entry.get("bytes"), int)
            or entry.get("bytes") < 0
            or not _is_sha256_hex(entry.get("sha256"))
        ):
            raise AnnotationError(f"{label} のfile entryが不正です: {relative_name}")
        normalized[relative_name] = {
            "bytes": entry["bytes"],
            "sha256": entry["sha256"],
        }
    return normalized


def _resolve_production_relative_path(
    relative_name: Any,
    *,
    production_root: Path,
    label: str,
) -> Path:
    if (
        not isinstance(relative_name, str)
        or not relative_name
        or "\x00" in relative_name
        or Path(relative_name).is_absolute()
        or ".." in Path(relative_name).parts
    ):
        raise AnnotationError(f"{label} はsafeなproduction相対pathでなければなりません: {relative_name}")
    root = production_root.resolve()
    resolved = (root / relative_name).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AnnotationError(f"{label} がproduction root外を指しています: {relative_name}") from exc
    if resolved == root:
        raise AnnotationError(f"{label} がproduction root自身を指しています。")
    return resolved


def _validate_production_integrity_artifacts(
    result: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    repository_root: Path | None = None,
    production_data_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the committed before/after hash artifacts, not only their claims."""

    integrity = result.get("production_integrity")
    baseline = manifest.get("production_hash_baseline")
    if not isinstance(integrity, Mapping) or not isinstance(baseline, Mapping):
        raise AnnotationError("production integrity / manifest baseline がありません。")
    baseline_relative = baseline.get("artifact_path")
    before_relative = integrity.get("before_artifact")
    after_relative = integrity.get("after_artifact")
    baseline_path = _resolve_repository_relative_path(
        baseline_relative, label="manifest production baseline", repository_root=repository_root
    )
    before_path = _resolve_repository_relative_path(
        before_relative, label="result production before artifact", repository_root=repository_root
    )
    after_path = _resolve_repository_relative_path(
        after_relative, label="result production after artifact", repository_root=repository_root
    )
    expected_production_root = (production_data_root or Path(PRODUCTION_DATA_ROOT)).resolve()
    expected_production_root_text = str(expected_production_root)
    baseline_root = baseline.get("root")
    if baseline_root != expected_production_root_text or not expected_production_root.is_dir():
        raise AnnotationError("manifest production baselineの固定absolute rootが不正です。")
    if before_path != baseline_path:
        raise AnnotationError("result before artifact がmanifest baselineと一致しません。")
    if integrity.get("before_artifact_bytes") != baseline.get("artifact_bytes"):
        raise AnnotationError("result before artifact bytes がmanifest baselineと一致しません。")
    if integrity.get("before_artifact_sha256") != baseline.get("artifact_sha256"):
        raise AnnotationError("result before artifact SHA-256 がmanifest baselineと一致しません。")
    _check_source_file(
        before_path,
        baseline.get("artifact_bytes"),
        baseline.get("artifact_sha256"),
        label="production before artifact",
    )

    after_bytes = integrity.get("after_artifact_bytes")
    after_sha256 = integrity.get("after_artifact_sha256")
    if (
        isinstance(after_bytes, bool)
        or not isinstance(after_bytes, int)
        or after_bytes < 0
        or not _is_sha256_hex(after_sha256)
    ):
        raise AnnotationError("result after artifactのbytes / SHA-256が不正です。")
    _check_source_file(
        after_path,
        after_bytes,
        after_sha256,
        label="production after artifact",
    )

    before_snapshot = _read_json(before_path)
    after_snapshot = _read_json(after_path)
    expected_count = baseline.get("file_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count != 15:
        raise AnnotationError("manifest production baselineのfile_countは15固定です。")
    if before_snapshot.get("root") != expected_production_root_text or after_snapshot.get("root") != expected_production_root_text:
        raise AnnotationError("production before/after snapshotのrootが同一の固定absolute rootではありません。")
    before_files = _snapshot_files(before_snapshot, label="production before artifact", expected_count=expected_count)
    after_files = _snapshot_files(after_snapshot, label="production after artifact", expected_count=expected_count)
    if before_files != after_files:
        raise AnnotationError("production after artifactの15-file entries / hashがbefore snapshotと不一致です。")
    if (
        after_snapshot.get("before_artifact") != baseline_relative
        or after_snapshot.get("before_artifact_sha256") != baseline.get("artifact_sha256")
        or after_snapshot.get("file_count") != expected_count
        or after_snapshot.get("matches_before") is not True
    ):
        raise AnnotationError("production after artifactのbefore参照または一致フラグが不正です。")
    for relative_name, entry in after_files.items():
        live_path = _resolve_production_relative_path(
            relative_name,
            production_root=expected_production_root,
            label="production live file",
        )
        _check_source_file(
            live_path,
            entry["bytes"],
            entry["sha256"],
            label=f"production live file {relative_name}",
        )
    return {
        "before_artifact": str(before_path),
        "after_artifact": str(after_path),
        "production_root": expected_production_root_text,
        "file_count": expected_count,
        "matches_before": True,
    }


def _validate_absolute_span(row: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    row_id = str(row.get("row_id", "row"))
    origin_start, origin_end, audio_duration = _source_span_origin(source)
    span = row.get("source_span")
    absolute = row.get("absolute_video_span_ms")
    if not isinstance(span, Mapping) or not isinstance(absolute, Mapping):
        raise AnnotationError(f"{row_id} の source / absolute span がありません。")
    source_kind = _source_content_kind(source)
    family = _span_kind_family(span)
    expected_family = "video" if source_kind == "source_mp4" else "wav"
    if family != expected_family:
        raise AnnotationError(f"{row_id} の source span kind と source content kind が不一致です。")
    parts, duration = _parts_and_duration(span)
    if source_kind == "wav_cache":
        if span.get("kind") in WAV_SINGLE_KINDS:
            start, end = parts[0]["start_ms"], parts[0]["end_ms"]
            if not 0 <= start < end <= audio_duration:
                raise AnnotationError(f"{row_id} の WAV span が source 範囲外です。")
            expected_absolute = {"start_ms": origin_start + start, "end_ms": origin_start + end}
            if absolute != expected_absolute:
                raise AnnotationError(f"{row_id} の absolute span が source origin と不一致です。")
            return
        absolute_parts = absolute.get("parts")
        if not isinstance(absolute_parts, list) or len(absolute_parts) != len(parts):
            raise AnnotationError(f"{row_id} の WAV concat absolute span が不正です。")
        for part, absolute_part in zip(parts, absolute_parts):
            start, end = part["start_ms"], part["end_ms"]
            if not 0 <= start < end <= audio_duration:
                raise AnnotationError(f"{row_id} の WAV concat part が source 範囲外です。")
            if absolute_part != {
                "start_ms": origin_start + start,
                "end_ms": origin_start + end,
            }:
                raise AnnotationError(f"{row_id} の WAV concat absolute part が不一致です。")
        if duration != sum(p["end_ms"] - p["start_ms"] for p in parts):
            raise AnnotationError(f"{row_id} の WAV concat duration が不一致です。")
        return

    if absolute.get("coordinate_system") != "absolute_video_ms":
        raise AnnotationError(f"{row_id} の video absolute coordinate system が不正です。")
    if span.get("kind") in VIDEO_SINGLE_KINDS:
        start, end = parts[0]["start_ms"], parts[0]["end_ms"]
        if not origin_start <= start < end <= origin_end:
            raise AnnotationError(f"{row_id} の source MP4 span が cut 範囲外です。")
        if absolute != {"coordinate_system": "absolute_video_ms", "start_ms": start, "end_ms": end}:
            raise AnnotationError(f"{row_id} の source MP4 absolute span が不一致です。")
        return
    absolute_parts = absolute.get("parts")
    if not isinstance(absolute_parts, list) or len(absolute_parts) != len(parts):
        raise AnnotationError(f"{row_id} の source MP4 concat absolute span が不正です。")
    for part, absolute_part in zip(parts, absolute_parts):
        start, end = part["start_ms"], part["end_ms"]
        if not origin_start <= start < end <= origin_end:
            raise AnnotationError(f"{row_id} の source MP4 concat part が cut 範囲外です。")
        if absolute_part != {"start_ms": start, "end_ms": end}:
            raise AnnotationError(f"{row_id} の source MP4 concat part が不一致です。")


def _validate_row_hashes(row: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    hashes = row.get("source_hashes")
    if not isinstance(hashes, Mapping):
        raise AnnotationError(f"{row['row_id']} の source_hashes がありません。")
    if hashes.get("source_content_sha256") != source.get("source_content_sha256"):
        raise AnnotationError(f"{row['row_id']} の source content hash が不一致です。")
    if hashes.get("vtt_sha256") != source.get("vtt_sha256"):
        raise AnnotationError(f"{row['row_id']} の VTT hash が不一致です。")
    if hashes.get("raw_timing_sha256") is not None:
        raise AnnotationError(f"{row['row_id']} は未生成 raw timing を保持できません。")


def _validate_gold_placeholder(row: Mapping[str, Any]) -> None:
    gold = row.get("gold")
    if not isinstance(gold, Mapping) or dict(gold) != GOLD_PLACEHOLDER:
        raise AnnotationError(f"{row['row_id']} の固定manifestにgold値を入れられません。")


def _validate_manual_split(row: Mapping[str, Any]) -> None:
    split = row.get("manual_split")
    provenance = row.get("draft_reference", {}).get("manual_split_provenance")
    if not isinstance(split, Mapping) or split != provenance:
        raise AnnotationError(f"{row['row_id']} の manual split provenance が不一致です。")
    if split.get("kind") != "manual_pre_measurement_text_split":
        raise AnnotationError(f"{row['row_id']} の manual split kind が不正です。")
    if split.get("candidate_results_seen") is not False or split.get("original_telop_time_not_copied") is not True:
        raise AnnotationError(f"{row['row_id']} の manual split は候補非由来でなければなりません。")
    original = split.get("original_text")
    boundary = MANUAL_SPLIT_BOUNDARIES.get(original)
    split_at = split.get("split_at_codepoint")
    if not isinstance(original, str) or boundary is None or not isinstance(split_at, int):
        raise AnnotationError(f"{row['row_id']} の manual split 境界が不正です。")
    if split.get("rule") != "fixed_meaning_boundary_non_candidate" or split_at != len(boundary):
        raise AnnotationError(f"{row['row_id']} の manual split は固定意味境界でなければなりません。")
    suffix = split.get("subtarget")
    if original[:split_at] != boundary or not boundary or boundary == original:
        raise AnnotationError(f"{row['row_id']} の manual split prefix が固定境界と不一致です。")
    delimiter = MANUAL_SPLIT_DELIMITERS.get(original, "")
    if split.get("delimiter_text") != delimiter:
        raise AnnotationError(f"{row['row_id']} の manual split delimiter provenance が不一致です。")
    suffix_text = original[split_at + len(delimiter):]
    expected = boundary if suffix == "a" else suffix_text if suffix == "b" else None
    if expected != row.get("target_text"):
        raise AnnotationError(f"{row['row_id']} の manual split target_text が不一致です。")
    if not expected or expected == original or boundary + delimiter + suffix_text != original:
        raise AnnotationError(f"{row['row_id']} の manual split が空・同一・再結合不能です。")
    if not str(row.get("source_telop_line_tuple_id", "")).endswith(f":manual:{suffix}"):
        raise AnnotationError(f"{row['row_id']} の manual split tuple id が不正です。")


def _validate_adjacent_fallback_context(
    row: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int]:
    row_id = str(row.get("row_id", "row"))
    span = row.get("source_span")
    context = row.get("fallback_concat_context")
    if not isinstance(span, Mapping) or span.get("kind") != "concatenated_source_video_audio":
        raise AnnotationError(f"{row_id} の fallback source span は2 cut concatが必要です。")
    if span.get("coordinate_system") != "absolute_video_ms":
        raise AnnotationError(f"{row_id} の fallback concat coordinate system が不正です。")
    parts = span.get("parts")
    if not isinstance(parts, list) or len(parts) != 2 or not isinstance(context, Mapping):
        raise AnnotationError(f"{row_id} の fallback concat context が不正です。")
    source_ids = context.get("source_part_source_ids")
    if not isinstance(source_ids, list) or len(source_ids) != 2 or len(set(source_ids)) != 2:
        raise AnnotationError(f"{row_id} の fallback concat source pair が不正です。")
    part_sources = [sources.get(source_id) for source_id in source_ids]
    if any(source is None or source.get("source_content_kind") != "source_mp4" for source in part_sources):
        raise AnnotationError(f"{row_id} の fallback concat source pair はsource MP4 2件が必要です。")
    first_cut = str(part_sources[0].get("cut_id", ""))
    second_cut = str(part_sources[1].get("cut_id", ""))
    try:
        if int(first_cut.rsplit("_", 1)[1]) + 1 != int(second_cut.rsplit("_", 1)[1]):
            raise AnnotationError(f"{row_id} の fallback concat pair は隣接cutでなければなりません。")
    except (IndexError, ValueError) as exc:
        raise AnnotationError(f"{row_id} の fallback concat cut id が不正です。") from exc
    expected_parts: list[dict[str, int]] = []
    expected_offset = 0
    for source in part_sources:
        start, end, _ = _source_span_origin(source)
        expected_parts.append(
            {"start_ms": start, "end_ms": end, "concat_offset_ms": expected_offset}
        )
        expected_offset += end - start
    if parts != expected_parts or span.get("duration_ms") != expected_offset:
        raise AnnotationError(f"{row_id} の fallback concat duration / concat_offset が不一致です。")
    gap_ms = expected_parts[1]["start_ms"] - expected_parts[0]["end_ms"]
    if context.get("gap_ms") != gap_ms or gap_ms <= 0:
        raise AnnotationError(f"{row_id} の fallback concat gap は正の実cut gapが必要です。")
    if context.get("candidate_boundary_used") is not False or context.get("rule") != "adjacent_noncontiguous_bound_cut_pair_full_context":
        raise AnnotationError(f"{row_id} の fallback concat は候補非依存の隣接cut pairでなければなりません。")
    target_source_id = context.get("target_source_id")
    if target_source_id not in source_ids or row.get("audio_source_id") != target_source_id:
        raise AnnotationError(f"{row_id} の target source mapping が不一致です。")
    target_index = source_ids.index(target_source_id)
    draft = row.get("draft_reference")
    if not isinstance(draft, Mapping):
        raise AnnotationError(f"{row_id} の fallback draft reference がありません。")
    draft_start = _require_int(draft.get("telop_line_start_ms"), label="draft_reference.telop_line_start_ms")
    draft_end = _require_int(draft.get("telop_line_end_ms"), label="draft_reference.telop_line_end_ms", minimum=1)
    target_part = expected_parts[target_index]
    if not target_part["start_ms"] <= draft_start < draft_end <= target_part["end_ms"]:
        raise AnnotationError(f"{row_id} の target draft が対象cut partに完全containされません。")
    expected_relative = {
        "start_ms": draft_start - target_part["start_ms"],
        "end_ms": draft_end - target_part["start_ms"],
    }
    if context.get("target_part_index") != target_index or context.get("target_relative_span_ms") != expected_relative:
        raise AnnotationError(f"{row_id} の target draft relative mapping が不一致です。")
    absolute = row.get("absolute_video_span_ms")
    if absolute != {
        "coordinate_system": "absolute_video_ms",
        "start_ms": target_part["start_ms"],
        "end_ms": target_part["end_ms"],
    }:
        raise AnnotationError(f"{row_id} の target absolute span が対象cut partと不一致です。")
    return draft_start, draft_end


def _validate_ass_fallback_context(
    row: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    evidence: Mapping[str, Any],
) -> None:
    """Validate a selected ASS dialogue against the full three-cut b5d span."""

    row_id = str(row.get("row_id", "row"))
    span = row.get("source_span")
    if not isinstance(span, Mapping) or span.get("kind") != "concatenated_source_video_audio" or span.get("coordinate_system") != "absolute_video_ms":
        raise AnnotationError(f"{row_id} の ASS fallback source span が不正です。")
    parts = span.get("parts")
    source_ids = list(evidence["source_ids"])
    context = row.get("ass_concat_context")
    dialogue = row.get("ass_dialogue_context")
    if not isinstance(parts, list) or len(parts) != 3 or not isinstance(context, Mapping) or not isinstance(dialogue, Mapping):
        raise AnnotationError(f"{row_id} の ASS fallback context が不正です。")
    if context.get("contract") != "existing_vtt_generated_ass_dialogue_full_noncontiguous_cutplan_context" or context.get("source_part_source_ids") != source_ids or context.get("candidate_boundary_used") is not False:
        raise AnnotationError(f"{row_id} の ASS fallback concat contract が不正です。")
    expected_parts: list[dict[str, int]] = []
    expected_offset = 0
    gaps: list[int] = []
    previous_end: int | None = None
    for source_id in source_ids:
        source = sources.get(source_id)
        if source is None or source.get("source_content_kind") != "source_mp4":
            raise AnnotationError(f"{row_id} の ASS fallback source pair が不正です。")
        start, end, _ = _source_span_origin(source)
        if previous_end is not None:
            gaps.append(start - previous_end)
        expected_parts.append({"start_ms": start, "end_ms": end, "concat_offset_ms": expected_offset})
        expected_offset += end - start
        previous_end = end
    if parts != expected_parts or span.get("duration_ms") != expected_offset or gaps != [294000, 34000]:
        raise AnnotationError(f"{row_id} の ASS fallback 3-part offset / duration / gap が不一致です。")
    if context.get("gap_ms") != gaps or context.get("concat_duration_ms") != expected_offset:
        raise AnnotationError(f"{row_id} の ASS fallback gap / duration provenance が不一致です。")

    event_index = dialogue.get("event_index")
    expected_dialogues = evidence.get("dialogues")
    if isinstance(event_index, bool) or not isinstance(event_index, int) or not isinstance(expected_dialogues, list):
        raise AnnotationError(f"{row_id} の ASS event index が不正です。")
    expected = next((item for item in expected_dialogues if item.get("event_index") == event_index), None)
    if not isinstance(expected, Mapping) or dict(dialogue) != dict(expected):
        raise AnnotationError(f"{row_id} の ASS event / VTT dialogue mapping が固定証跡と不一致です。")
    if row.get("target_text") != expected.get("text") or row.get("vtt_cue_ids") != [str(expected["vtt_cue_index"])]:
        raise AnnotationError(f"{row_id} の ASS target text / VTT cue mapping が不一致です。")

    target_source_id = row.get("audio_source_id")
    target_index = next(
        (index for index, part in enumerate(expected_parts) if part["start_ms"] <= expected["source_absolute_start_ms"] < part["end_ms"]),
        None,
    )
    if target_index is None or target_source_id != source_ids[target_index] or context.get("target_source_id") != target_source_id or context.get("target_part_index") != target_index:
        raise AnnotationError(f"{row_id} の ASS target source mapping が不一致です。")
    target_part = expected_parts[target_index]
    if not target_part["start_ms"] <= expected["source_absolute_start_ms"] < expected["source_absolute_end_ms"] <= target_part["end_ms"]:
        raise AnnotationError(f"{row_id} の ASS dialogue がtarget cutへcontainされません。")
    relative = {
        "start_ms": expected["source_absolute_start_ms"] - target_part["start_ms"],
        "end_ms": expected["source_absolute_end_ms"] - target_part["start_ms"],
    }
    if context.get("target_relative_span_ms") != relative:
        raise AnnotationError(f"{row_id} の ASS target relative mapping が不一致です。")
    if row.get("absolute_video_span_ms") != {
        "coordinate_system": "absolute_video_ms",
        "start_ms": target_part["start_ms"],
        "end_ms": target_part["end_ms"],
    }:
        raise AnnotationError(f"{row_id} の ASS absolute target span が不一致です。")
    draft = row.get("draft_reference")
    if not isinstance(draft, Mapping) or draft.get("kind") != "immutable_ass_dialogue" or draft.get("asset_id") != evidence.get("asset_id") or draft.get("event_index") != event_index or draft.get("candidate_boundary_used") is not False or draft.get("gold_must_ignore_ass_event_time") is not True:
        raise AnnotationError(f"{row_id} の ASS draft reference が不正です。")
    fallback = row.get("vtt_fallback_context")
    if not isinstance(fallback, Mapping) or fallback.get("contract") != "legacy_vtt_target_cues_with_existing_ass_concat_context" or fallback.get("automatic_line_or_cross_cue_moves_max") != 0 or fallback.get("human_containment_check_required") is not True:
        raise AnnotationError(f"{row_id} の ASS VTT fallback policy が不正です。")


def _validate_artifact_holdout(row: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    row_id = str(row.get("row_id", "row"))
    if (
        source.get("source_content_kind") != "wav_cache"
        or row.get("raw_timing_status") != "pending_bounded_whisper_cli"
        or row.get("timing_input_source_id") != row.get("audio_source_id")
        or row.get("source_span", {}).get("kind") != "single_source_audio"
        or row["source_span"].get("start_ms") != 0
        or row["source_span"].get("end_ms") != source.get("audio_duration_ms")
    ):
        raise AnnotationError(f"{row_id} の artifact holdout source/timing 契約が不正です。")
    context = row.get("artifact_cross_cue_holdout_context")
    if (
        not isinstance(context, Mapping)
        or context.get("subtype") != "artifact_cross_cue_holdout"
        or context.get("coverage_excluded") is not True
        or context.get("expected_low_confidence") is not True
        or context.get("expected_policy_action") != "flag_low_confidence_preserve_draft_time"
        or context.get("candidate_results_seen") is not False
        or not isinstance(context.get("artifact_cue_indices_in_same_cut"), list)
        or len(set(context["artifact_cue_indices_in_same_cut"])) < 2
    ):
        raise AnnotationError(f"{row_id} の artifact cross-cue holdout low-confidence 契約が不正です。")
    if row.get("coverage_excluded") is not True or row.get("fallback_non_regression_required") is not False:
        raise AnnotationError(f"{row_id} の artifact holdout coverage policy が不正です。")


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    check_sources: bool = False,
    check_runtime_sources: bool = True,
) -> dict[str, Any]:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise AnnotationError("T1-1 manifest schema が不正です。")
    actual_fingerprint = manifest_fingerprint(manifest)
    if manifest.get("manifest_fingerprint") != actual_fingerprint:
        raise AnnotationError(
            f"manifest fingerprint が不一致です: declared={manifest.get('manifest_fingerprint')} actual={actual_fingerprint}"
        )
    if manifest.get("status") != "manifest_frozen_waiting_for_human_gold":
        raise AnnotationError("gold入力前の固定manifest statusが不正です。")
    if manifest.get("measurement_status") != "not_run_gold_missing":
        raise AnnotationError("gold入力前に測定済みstatusを設定できません。")

    manifest_revision = manifest.get("manifest_revision")
    if manifest_revision not in {
        "corrected_pre_measurement_freeze_v3_5",
        "corrected_pre_measurement_freeze_v3_6",
        "corrected_pre_measurement_freeze_v3_7",
    }:
        raise AnnotationError("manifest_revision は既知のv3.5/v3.6/v3.7 freeze候補に限定されます。")
    legacy_layout = manifest_revision in {
        "corrected_pre_measurement_freeze_v3_5",
        "corrected_pre_measurement_freeze_v3_6",
    }
    human_gold = manifest.get("human_gold")
    if not isinstance(human_gold, Mapping):
        raise AnnotationError("human_gold contract がありません。")
    if not legacy_layout:
        expected_human_gold = {
            "status": "missing_waiting_for_human_annotation",
            "source": PACKET_GOLD_PROVENANCE,
            "source_provenance": "no existing row-level human gold found; saved_telop_time, VTT, cue, token, and text split are not gold",
            "forbidden_sources": [
                "whisper_token_timing",
                "artifact_cue_start_or_end",
                "vtt_cue_start_or_end",
                "existing_telop_time",
                "machine_candidate",
                "text_boundary_guess",
            ],
            "required_per_row": [
                "gold.line_onset_ms",
                "gold.timebase=source_audio_relative_ms",
                "gold.annotator_id",
                "gold.annotated_at",
                "gold.audio_listened",
            ],
            "completion_rule": "全64行で audio_listened=true、gold.line_onset_msが整数、timebaseがsource_audio_relative_ms、annotator_idとannotated_atが非空になるまで測定不可。",
            "manual_subtarget_rule": "manual split subtargetのgoldも音声を実際に再生して決め、split位置や元行時刻を候補として入力しない。manual rowsは兄弟subtargetを同時出力しない独立scenarioで、元行保存時刻はbaseline referenceのみである。",
        }
        if dict(human_gold) != expected_human_gold:
            raise AnnotationError("v3.7 human_gold completion contract が固定64行契約と一致しません。")
    rows = manifest.get("rows")
    counts = manifest.get("fixture_counts")
    expected_row_count = 60 if legacy_layout else 64
    if not isinstance(rows, list) or len(rows) != expected_row_count or not isinstance(counts, Mapping):
        raise AnnotationError(f"manifest は固定{expected_row_count}行と fixture_counts が必要です。")
    sources = _source_by_id(manifest)
    evidence = None if legacy_layout else _validate_ass_evidence_contract(manifest, check_sources=check_sources)
    expected_source_count = 12 if legacy_layout else 15
    if len(sources) != expected_source_count:
        raise AnnotationError(f"audio context source は{expected_source_count} spanで固定します。")
    if not legacy_layout:
        assert evidence is not None
        for source_id, segment in zip(evidence["source_ids"], evidence["segments"]):
            source = sources.get(source_id)
            if (
                not isinstance(source, Mapping)
                or source.get("source_content_kind") != "source_mp4"
                or source.get("source_content_path") != evidence["source_content_path"]
                or source.get("source_content_bytes") != evidence["source_content_bytes"]
                or source.get("source_content_sha256") != evidence["source_content_sha256"]
                or source.get("vtt_path") != evidence["vtt_path"]
                or source.get("vtt_bytes") != evidence["vtt_bytes"]
                or source.get("vtt_sha256") != evidence["vtt_sha256"]
                or source.get("audio_span_origin_ms") != {"start_ms": segment["start_ms"], "end_ms": segment["end_ms"]}
                or source.get("audio_duration_ms") != segment["duration_ms"]
                or source.get("raw_timing_path") is not None
                or source.get("raw_timing_bytes") is not None
                or source.get("raw_timing_sha256") is not None
                or source.get("artifact_document_id") is not None
                or source.get("timing_input_role") != "legacy_vtt_fallback_only"
                or source.get("source_provenance") != "pre_t1_saved_cutplan003_source_mp4_bound_read_only"
                or "rejected_legacy_audio_cache" in source
            ):
                raise AnnotationError(f"{source_id} のb5d cutplan003 source bindingが不正です。")
    actual_counts = {group: 0 for group in REQUIRED_GROUPS}
    row_ids: set[str] = set()
    saved_tuple_ids: set[str] = set()
    all_tuple_ids: set[str] = set()
    manual_splits: dict[str, dict[str, str]] = {}
    manual_count = 0
    ass_dialogue_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            raise AnnotationError("manifest row が object ではありません。")
        row_id = row.get("row_id")
        if not isinstance(row_id, str) or not row_id or row_id in row_ids:
            raise AnnotationError("row_id が空または重複しています。")
        row_ids.add(row_id)
        group = row.get("fixture_group")
        if group not in REQUIRED_GROUPS:
            raise AnnotationError(f"未知の fixture group です: {group}")
        actual_counts[group] += 1
        source_id = row.get("audio_source_id")
        source = sources.get(source_id)
        if source is None:
            raise AnnotationError(f"{row_id} の audio source がありません。")
        _validate_row_hashes(row, source)
        if not isinstance(row.get("target_text"), str) or not row["target_text"].strip():
            raise AnnotationError(f"{row_id} の target_text は空にできません。")
        if row.get("candidate_or_boundary_fields_are_not_gold") is not True:
            raise AnnotationError(f"{row_id} の candidate boundary isolation flag がありません。")
        if not isinstance(row.get("vtt_cue_ids"), list) or not row["vtt_cue_ids"]:
            raise AnnotationError(f"{row_id} の VTT cue id がありません。")
        _row_duration_ms(row)
        if not (
            group == "vtt_fallback_concat"
            and isinstance(row.get("source_span"), Mapping)
            and row["source_span"].get("kind") == "concatenated_source_video_audio"
        ):
            _validate_absolute_span(row, source)
        raw_available = row.get("raw_timing_available")
        if raw_available is not False:
            raise AnnotationError(f"{row_id} の raw timing は未生成で固定します。")

        if group in {"long_single_cue", "multi_cross_cue"}:
            if source.get("source_content_kind") != "wav_cache":
                raise AnnotationError(f"{row_id} の alignment source は WAV cache span である必要があります。")
            if row.get("raw_timing_status") != "pending_bounded_whisper_cli" or row.get("timing_input_source_id") != source_id:
                raise AnnotationError(f"{row_id} の bounded timing pending 契約が不正です。")
            if row["source_span"].get("kind") != "single_source_audio" or row["source_span"].get("start_ms") != 0 or row["source_span"].get("end_ms") != source["audio_duration_ms"]:
                raise AnnotationError(f"{row_id} の alignment audio span は既存 cut range 全体である必要があります。")
            context_key = "long_single_cue_context" if group == "long_single_cue" else "multi_cross_cue_context"
            context = row.get(context_key)
            if not isinstance(context, Mapping):
                raise AnnotationError(f"{row_id} の group context がありません。")
            if group == "long_single_cue":
                if context.get("contract") != "same_artifact_cue_multiple_telop_lines":
                    raise AnnotationError(f"{row_id} の long single cue 契約が不正です。")
                line_position = _require_int(context.get("line_position_in_artifact_cue"), label="line_position_in_artifact_cue", minimum=1)
                line_count = _require_int(context.get("line_count_in_artifact_cue"), label="line_count_in_artifact_cue", minimum=2)
                if line_position > line_count:
                    raise AnnotationError(f"{row_id} の long cue line position が不正です。")
                if line_position == 1:
                    if context.get("cue_start_anchor") is not True or context.get("second_or_later_line_required") is not False:
                        raise AnnotationError(f"{row_id} の long cue anchor provenance が不正です。")
                elif context.get("second_or_later_line_required") is not True or context.get("cue_start_anchor") is not False:
                    raise AnnotationError(f"{row_id} の long cue subsequent-line provenance が不正です。")
            else:
                indices = context.get("artifact_cue_indices_in_same_cut")
                if context.get("contract") != "artifact_cue_boundary_sequence_same_cut_range" or context.get("line_is_not_required_to_span_two_artifact_cues") is not True or not isinstance(indices, list) or len(set(indices)) < 2:
                    raise AnnotationError(f"{row_id} の multi/cross cue 契約が不正です。")
                holdout_context = row.get("artifact_cross_cue_holdout_context")
                if isinstance(holdout_context, Mapping):
                    if not legacy_layout:
                        if (
                            holdout_context.get("subtype") != "artifact_cross_cue_holdout"
                            or holdout_context.get("coverage_excluded") is not False
                            or holdout_context.get("coverage_denominator") != "multi_cross_cue_24"
                            or holdout_context.get("expected_low_confidence") is not True
                            or holdout_context.get("expected_policy_action") != "flag_low_confidence_preserve_draft_time"
                            or holdout_context.get("candidate_results_seen") is not False
                            or holdout_context.get("artifact_cue_indices_in_same_cut") != indices
                            or context.get("expected_low_confidence") is not True
                            or context.get("expected_policy_action") != "flag_low_confidence_preserve_draft_time"
                            or row.get("coverage_excluded") is not False
                            or row.get("fallback_non_regression_required") is not False
                        ):
                            raise AnnotationError(f"{row_id} の multi low-confidence holdout policy が不正です。")
                    else:
                        _validate_artifact_holdout(row, source)
                elif context.get("expected_low_confidence") is not False or context.get("expected_policy_action") != "evaluate_normal_alignment_policy":
                    raise AnnotationError(f"{row_id} の low-confidence policy が不正です。")
            if not isinstance(row.get("draft_reference", {}).get("artifact_context"), Mapping) or row["draft_reference"]["artifact_context"].get("raw_token_timing_present") is not False:
                raise AnnotationError(f"{row_id} の artifact は cue-only provenance である必要があります。")
        else:
            holdout = row.get("artifact_cross_cue_holdout_context")
            ass_dialogue = row.get("ass_dialogue_context")
            if not legacy_layout and isinstance(ass_dialogue, Mapping):
                if (
                    isinstance(holdout, Mapping)
                    or row.get("source_content_kind") == "wav_cache"
                    or source.get("source_content_kind") != "source_mp4"
                    or row.get("raw_timing_status") != "not_applicable_legacy_vtt"
                    or row.get("timing_input_source_id") is not None
                    or row.get("source_boundary_basis") != "existing_vtt_generated_ass_dialogue_full_noncontiguous_cutplan_context_non_candidate"
                    or row.get("coverage_excluded") is not True
                    or row.get("fallback_non_regression_required") is not True
                ):
                    raise AnnotationError(f"{row_id} のASS fallback source/timing policyが不正です。")
                _validate_ass_fallback_context(row, sources, evidence or {})
                if row.get("manual_pre_measurement_fixture") is not False or row.get("manual_split") is not None or row.get("manual_split_evaluation") is not None:
                    raise AnnotationError(f"{row_id} のASS fallbackにmanual splitを混在できません。")
            elif isinstance(holdout, Mapping):
                if not legacy_layout:
                    raise AnnotationError(f"{row_id} のartifact holdoutをfallbackへ置けません。")
                if group != "vtt_fallback_concat":
                    raise AnnotationError(f"{row_id} の artifact holdout group が不正です。")
                _validate_artifact_holdout(row, source)
                _validate_absolute_span(row, source)
                if row.get("vtt_fallback_context") is not None or row.get("fallback_concat_context") is not None:
                    raise AnnotationError(f"{row_id} の artifact holdout にVTT fallback contextを混在できません。")
            else:
                if source.get("source_content_kind") != "source_mp4" or row.get("raw_timing_status") != "not_applicable_legacy_vtt" or row.get("timing_input_source_id") is not None:
                    raise AnnotationError(f"{row_id} の fallback source/timing 契約が不正です。")
                if row.get("source_boundary_basis") != "bound_source_mp4_adjacent_noncontiguous_cut_pair_full_context_non_candidate":
                    raise AnnotationError(f"{row_id} の fallback audio span basis が不正です。")
                draft_start, draft_end = _validate_adjacent_fallback_context(row, sources)
                draft = row.get("draft_reference")
                if not isinstance(draft, Mapping) or draft.get("source_boundary_basis") != "bound_source_mp4_adjacent_noncontiguous_cut_pair_full_context_non_candidate":
                    raise AnnotationError(f"{row_id} の fallback draft reference がありません。")
                fallback = row.get("vtt_fallback_context")
                if (
                    not isinstance(fallback, Mapping)
                    or fallback.get("contract") != "legacy_vtt_target_cues_with_adjacent_noncontiguous_bound_source_mp4_context"
                    or fallback.get("candidate_boundary_used") is not False
                    or fallback.get("audio_source_is_bound_source_mp4") is not True
                    or fallback.get("audio_context_is_full_bound_source_mp4_cut_pair") is not True
                    or fallback.get("cut_pair_gap_ms") != row["fallback_concat_context"].get("gap_ms")
                    or fallback.get("target_audio_containment_machine_check") != "draft_reference_telop_interval_within_target_cut_part"
                    or fallback.get("human_containment_check_required") is not True
                ):
                    raise AnnotationError(f"{row_id} の VTT fallback 契約が不正です。")
                if row.get("fallback_non_regression_required") is not True:
                    raise AnnotationError(f"{row_id} は fallback 非回帰分母へ含める必要があります。")
                if row.get("manual_pre_measurement_fixture") is True:
                    evaluation = row.get("manual_split_evaluation")
                    expected_baseline = {"start_ms": draft_start, "end_ms": draft_end}
                    if (
                        not isinstance(evaluation, Mapping)
                        or evaluation.get("scope") != "independent_manual_subtarget_fallback_scenario"
                        or evaluation.get("scenario_id") != row_id
                        or evaluation.get("scenario_emits_only_this_subtarget") is not True
                        or evaluation.get("sibling_subtargets_coemitted") is not False
                        or evaluation.get("included_in_vtt_non_regression_denominator") is not True
                        or evaluation.get("baseline_draft_time_ms") != expected_baseline
                        or evaluation.get("baseline_is_gold") is not False
                        or evaluation.get("automatic_line_or_cross_cue_moves_max") != 0
                        or evaluation.get("gold_requires_human_audio_listening") is not True
                    ):
                        raise AnnotationError(f"{row_id} の manual fallback scenario 契約が不正です。")
                elif row.get("manual_split_evaluation") is not None:
                    raise AnnotationError(f"{row_id} に不要な manual fallback scenario 契約があります。")

        tuple_id = row.get("source_telop_line_tuple_id")
        if not isinstance(tuple_id, str) or not tuple_id or tuple_id in all_tuple_ids:
            raise AnnotationError(f"{row_id} の tuple id が空または重複しています。")
        all_tuple_ids.add(tuple_id)
        if row.get("manual_pre_measurement_fixture") is True:
            manual_count += 1
            if group != "vtt_fallback_concat":
                raise AnnotationError(f"{row_id} の manual fixture group が不正です。")
            _validate_manual_split(row)
            split = row["manual_split"]
            original = split["original_source_telop_line_tuple_id"]
            manual_splits.setdefault(original, {})[split["subtarget"]] = row["target_text"]
        elif not legacy_layout and isinstance(row.get("ass_dialogue_context"), Mapping):
            ass_dialogue_count += 1
        else:
            saved_tuple_ids.add(tuple_id)
        _validate_gold_placeholder(row)

    expected_group_counts = {"long_single_cue": 20, "multi_cross_cue": 24 if not legacy_layout else 20, "vtt_fallback_concat": 20}
    if actual_counts != {group: counts.get(group) for group in REQUIRED_GROUPS} or actual_counts != expected_group_counts:
        raise AnnotationError(f"fixture group 件数が{expected_group_counts}と一致しません。")
    expected_counts = {
        "long_single_cue": 20,
        "multi_cross_cue": 24 if not legacy_layout else 20,
        "vtt_fallback_concat": 20,
        "total": 64 if not legacy_layout else 60,
        "saved_telop_unique_line_time_tuples": 59,
        "artifact_backed_rows_available": 44,
        "artifact_backed_rows_selected_for_alignment": 44 if not legacy_layout else 40,
        "artifact_cross_cue_holdout_rows": 4,
        "artifact_backed_rows_unused": 0,
        **({"ass_dialogue_rows": 4} if not legacy_layout else {}),
        "legacy_lb4_saved_source_lines": 15,
        "legacy_lb4_exact_target_rows": 14,
        "manual_split_base_lines": 1,
        "manual_split_subtargets": 2,
        "saved_target_rows_without_manual_split": 58,
        **({"non_manual_target_rows": 62} if not legacy_layout else {}),
    }
    if set(counts) != set(expected_counts) or any(counts.get(key) != value for key, value in expected_counts.items()):
        raise AnnotationError("fixture_counts の corrected v4 固定値が不一致です。")
    expected_ass_count = 0 if legacy_layout else 4
    expected_saved_count = 58 if legacy_layout else 58
    expected_total = 60 if legacy_layout else 64
    if len(saved_tuple_ids) != expected_saved_count or manual_count != 2 or ass_dialogue_count != expected_ass_count or len(all_tuple_ids) != expected_total:
        raise AnnotationError(f"保存済みtarget行{expected_saved_count}、manual subtarget2、ASS target{expected_ass_count}、合計{expected_total}の一意性が必要です。")
    if len(manual_splits) != 1 or any(set(parts) != {"a", "b"} for parts in manual_splits.values()):
        raise AnnotationError("manual split は1原文を各a/bの2 subtargetへ固定します。")
    if any(parts["a"] == parts["b"] for parts in manual_splits.values()):
        raise AnnotationError("manual split のa/b target_text重複は許可しません。")
    if set(manual_splits) != MANUAL_SPLIT_ORIGINAL_TUPLE_IDS or set(manual_splits) & saved_tuple_ids:
        raise AnnotationError("manual split の元tupleは固定1件で、saved rowへ混入できません。")
    if len(saved_tuple_ids | set(manual_splits)) != 59:
        raise AnnotationError("saved telop 59 unique line/time tuple の母集団と不一致です。")

    long_rows = [row for row in rows if row["fixture_group"] == "long_single_cue"]
    long_anchor_count = sum(
        row["long_single_cue_context"].get("line_position_in_artifact_cue") == 1
        for row in long_rows
    )
    long_subsequent_count = sum(
        row["long_single_cue_context"].get("line_position_in_artifact_cue", 0) >= 2
        for row in long_rows
    )
    if long_anchor_count != 6 or long_subsequent_count != 14:
        raise AnnotationError("long group は14件のcue後続行と6件の明示cue-start anchorで固定します。")
    actual_long_tuples = {
        row["source_telop_line_tuple_id"] for row in long_rows
    }
    actual_multi_tuples = {
        row["source_telop_line_tuple_id"]
        for row in rows
        if row["fixture_group"] == "multi_cross_cue"
    }
    expected_multi_tuples = ALIGNMENT_MULTI_TUPLE_IDS | (KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS if not legacy_layout else frozenset())
    if actual_long_tuples != ALIGNMENT_LONG_TUPLE_IDS or actual_multi_tuples != expected_multi_tuples:
        raise AnnotationError("artifact alignmentのlong/multi tuple割当が固定集合と一致しません。")

    actual_low_confidence = {
        row["row_id"]
        for row in rows
        if row.get("multi_cross_cue_context", {}).get("expected_low_confidence") is True
        or row.get("artifact_cross_cue_holdout_context", {}).get("expected_low_confidence") is True
    }
    expected_low_confidence = KNOWN_MULTI_LOW_CONFIDENCE_ROW_IDS if not legacy_layout else KNOWN_LOW_CONFIDENCE_ROW_IDS
    if actual_low_confidence != expected_low_confidence:
        raise AnnotationError("既知artifact cross-cue holdoutのlow-confidence 4行が固定契約と一致しません。")
    actual_holdouts = {
        row.get("source_telop_line_tuple_id")
        for row in rows
        if isinstance(row.get("artifact_cross_cue_holdout_context"), Mapping)
    }
    if actual_holdouts != KNOWN_ARTIFACT_HOLDOUT_TUPLE_IDS:
        raise AnnotationError("artifact cross-cue holdoutの4 tupleが固定契約と一致しません。")
    if not legacy_layout and any(
        row["fixture_group"] != "multi_cross_cue"
        for row in rows
        if isinstance(row.get("artifact_cross_cue_holdout_context"), Mapping)
    ):
        raise AnnotationError("final v3.7 artifact holdoutはmulti_cross_cueへ限定します。")

    limits = manifest.get("limits")
    timing_inputs = manifest.get("timing_inputs")
    if not isinstance(limits, Mapping) or not isinstance(timing_inputs, Mapping):
        raise AnnotationError("limits / timing_inputs がありません。")
    expected_audio_context_count = 12 if legacy_layout else 15
    if limits.get("max_selected_spans") != 8 or limits.get("selected_span_count") != 8 or limits.get("audio_context_span_count") != expected_audio_context_count or limits.get("max_whisper_invocations") != 8 or limits.get("whisper_invocation_count") != 0:
        raise AnnotationError(f"8 selected timing spans / {expected_audio_context_count} audio context / max 8 invocation 固定が不正です。")
    selected_ids = timing_inputs.get("selected_source_ids")
    expected_selected_ids = sorted(source["source_id"] for source in sources.values() if source.get("timing_input_role") == "bounded_whisper_input")
    if timing_inputs.get("selected_span_count") != 8 or timing_inputs.get("max_selected_spans") != 8 or timing_inputs.get("max_whisper_invocations") != 8 or timing_inputs.get("whisper_invocation_count") != 0 or sorted(selected_ids or []) != expected_selected_ids or len(expected_selected_ids) != 8 or timing_inputs.get("output_committed") is not False or timing_inputs.get("production_write_allowed") is not False:
        raise AnnotationError("timing_inputs の bounded invocation 契約が不正です。")

    integrity = manifest.get("production_integrity_contract")
    if not isinstance(integrity, Mapping):
        raise AnnotationError("production integrity contract がありません。")
    bound_sources = integrity.get("bound_manifest_sources")
    bound_telops = integrity.get("bound_telop_documents")
    bound_artifacts = integrity.get("bound_artifact_documents")
    rehash = integrity.get("after_measurement_rehash")
    expected_source_ids = sorted(source["source_id"] for source in manifest["sources"].values())
    expected_telop_ids = sorted(manifest.get("telop_documents", {}).keys())
    expected_artifact_ids = sorted(manifest.get("artifact_documents", {}).keys())
    expected_bound_source_ids = sorted(source["source_id"] for source in sources.values())
    expected_benchmark_evidence = None if legacy_layout else ass_evidence_integrity_contract(evidence or {})
    if (
        not isinstance(bound_sources, Mapping)
        or bound_sources.get("count") != expected_source_count
        or sorted(bound_sources.get("source_ids", [])) != expected_bound_source_ids
        or not isinstance(bound_telops, Mapping)
        or bound_telops.get("count") != 4
        or sorted(bound_telops.get("document_ids", [])) != expected_telop_ids
        or not isinstance(bound_artifacts, Mapping)
        or bound_artifacts.get("count") != 3
        or sorted(bound_artifacts.get("document_ids", [])) != expected_artifact_ids
        or not isinstance(rehash, Mapping)
        or rehash.get("required") is not True
        or rehash.get("status_at_freeze") != "pending_candidate_measurement"
        or rehash.get("fail_closed_on_mismatch") is not True
    ):
        raise AnnotationError(f"production integrity の{expected_source_count} source / telop / artifact再hash契約が不正です。")
    if not legacy_layout:
        if integrity.get("bound_benchmark_evidence") != expected_benchmark_evidence:
            raise AnnotationError("production integrity のb5d benchmark evidence bindingが不正です。")
        rehash_benchmark = rehash.get("benchmark_evidence")
        if rehash_benchmark != "re-hash the bound b5d ASS, VTT, cutplan, and ffmpeg log bytes/SHA before candidate measurement and after measurement":
            raise AnnotationError("production integrity のb5d after-measurement rehash契約が不正です。")

    if check_sources:
        for source in sources.values():
            _check_canonical_source(source)
            _check_source_file(source.get("vtt_path"), source.get("vtt_bytes"), source.get("vtt_sha256"), label="VTT")
            if source.get("source_content_kind") == "wav_cache":
                _check_source_file(source.get("audio_cache_metadata_path"), source.get("audio_cache_metadata_bytes"), source.get("audio_cache_metadata_sha256"), label="audio cache metadata")
            rejected = source.get("rejected_legacy_audio_cache")
            if isinstance(rejected, Mapping):
                _check_source_file(rejected.get("audio_path"), rejected.get("audio_bytes"), rejected.get("audio_sha256"), label="rejected legacy audio cache")
                _check_source_file(rejected.get("audio_cache_metadata_path"), rejected.get("audio_cache_metadata_bytes"), rejected.get("audio_cache_metadata_sha256"), label="rejected legacy audio cache metadata")
        for document in manifest.get("telop_documents", {}).values():
            _check_source_file(document.get("path"), document.get("bytes"), document.get("sha256"), label="telop document")
        for document in manifest.get("artifact_documents", {}).values():
            _check_source_file(document.get("path"), document.get("bytes"), document.get("sha256"), label="artifact document")
        if check_runtime_sources:
            runtime = manifest.get("runtime")
            if not isinstance(runtime, Mapping):
                raise AnnotationError("runtime がありません。")
            _check_source_file(runtime.get("binary", {}).get("path"), runtime.get("binary", {}).get("bytes"), runtime.get("binary", {}).get("sha256"), label="runtime binary")
            _check_source_file(runtime.get("model", {}).get("path"), runtime.get("model", {}).get("bytes"), runtime.get("model", {}).get("sha256"), label="runtime model")
            ffmpeg = runtime.get("ffmpeg")
            if not isinstance(ffmpeg, Mapping):
                raise AnnotationError("runtime.ffmpeg がありません。")
            _check_source_file(ffmpeg.get("path"), ffmpeg.get("bytes"), ffmpeg.get("sha256"), label="ffmpeg")
            baseline = manifest.get("production_hash_baseline")
            if isinstance(baseline, Mapping):
                baseline_path = Path(str(baseline.get("artifact_path", "")))
                if not baseline_path.is_absolute():
                    baseline_path = Path(__file__).resolve().parents[2] / baseline_path
                _check_source_file(baseline_path, baseline.get("artifact_bytes"), baseline.get("artifact_sha256"), label="production hash baseline")

    return {
        "manifest_fingerprint": actual_fingerprint,
        "row_count": len(rows),
        "group_counts": actual_counts,
        "source_count": len(sources),
        "selected_timing_span_count": limits["selected_span_count"],
        "audio_context_span_count": limits["audio_context_span_count"],
        "max_whisper_invocations": limits["max_whisper_invocations"],
        "whisper_invocation_count": limits["whisper_invocation_count"],
        "source_hashes_checked": check_sources,
        "gold_status": manifest["human_gold"]["status"],
        "measurement_status": manifest["measurement_status"],
    }


def _expected_pre_measurement_result(manifest: Mapping[str, Any]) -> dict[str, Any]:
    limits = manifest["limits"]
    timing = manifest["timing_inputs"]
    smoke = manifest["runtime"]["ffmpeg"]["smoke_test"]
    baseline = manifest["production_hash_baseline"]
    integrity_contract = manifest["production_integrity_contract"]
    expected_sources = integrity_contract["bound_manifest_sources"]["source_ids"]
    expected_telops = integrity_contract["bound_telop_documents"]["document_ids"]
    expected_artifacts = integrity_contract["bound_artifact_documents"]["document_ids"]
    after_contract = {
        "status_at_result": "pending_candidate_measurement",
        "required_before_candidate_result_or_go_no_go": True,
        "fail_closed_on_mismatch": True,
        "bound_source_ids": copy.deepcopy(expected_sources),
        "bound_telop_document_ids": copy.deepcopy(expected_telops),
        "bound_artifact_document_ids": copy.deepcopy(expected_artifacts),
        "bound_benchmark_evidence": copy.deepcopy(integrity_contract["bound_benchmark_evidence"]),
        "benchmark_evidence_rehash": {
            "required": True,
            "status_at_freeze": "pending_candidate_measurement",
            "fail_closed_on_mismatch": True,
            "binding": copy.deepcopy(integrity_contract["bound_benchmark_evidence"]),
        },
        "after_measurement_artifact": None,
    }
    return {
        "schema": RESULT_SCHEMA,
        "benchmark_id": manifest["benchmark_id"],
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "manifest_freeze_commit": FORMAL_MANIFEST_FREEZE_COMMIT,
        "status": "blocked_waiting_for_human_gold",
        "measurement_status": "not_run",
        "decision": "not_decidable_before_human_audio_annotation",
        "go": None,
        "no_go": None,
        "t1_1_complete": False,
        "t1_2_allowed": False,
        "ac_40_update_allowed": False,
        "fixture_counts": copy.deepcopy(manifest["fixture_counts"]),
        "limits": copy.deepcopy(limits),
        "human_gold": {
            "status": "missing",
            "required_rows": len(manifest["rows"]),
            "complete_rows": 0,
            "source": PACKET_GOLD_PROVENANCE,
            "playback_receipts_required": True,
            "onset_window_contract": "played_from_ms <= line_onset_ms < played_from_ms + played_duration_ms",
        },
        "timing_inputs": {
            "artifact_json_raw_token_timing": False,
            "selected_span_count": timing["selected_span_count"],
            "max_whisper_invocations": timing["max_whisper_invocations"],
            "whisper_invocation_count": timing["whisper_invocation_count"],
            "status": timing["status"],
            "output_root": timing["output_root"],
        },
        "ffmpeg_smoke": {
            "status": smoke["status"],
            "source_span_ms": [
                smoke["absolute_span_ms"]["start_ms"],
                smoke["absolute_span_ms"]["end_ms"],
            ],
            "frames": smoke["actual_frames"],
            "format": copy.deepcopy(smoke["format"]),
            "output_committed": smoke["output_committed"],
        },
        "concat_playback_smoke": {
            "status": "pass",
            "row_id": "t1-fallback-001",
            "played_from_ms": 16000,
            "played_duration_ms": 5000,
            "parts": 2,
            "frames": 80000,
            "bytes": 160044,
            "format": {"channels": 1, "sample_width": 2, "sample_rate": 16000},
            "receipt_revalidated": True,
            "candidate_measurement": False,
            "output_committed": False,
        },
        "metrics": None,
        "production_integrity": {
            "before_artifact": baseline["artifact_path"],
            "before_artifact_bytes": baseline["artifact_bytes"],
            "before_artifact_sha256": baseline["artifact_sha256"],
            "after_artifact": PRODUCTION_AFTER_ARTIFACT,
            "after_artifact_bytes": PRODUCTION_AFTER_ARTIFACT_BYTES,
            "after_artifact_sha256": PRODUCTION_AFTER_ARTIFACT_SHA256,
            "after_matches_before": True,
            "after_scope_file_count": baseline["file_count"],
            "after_measurement_rehash_contract": after_contract,
        },
        "reason": "既存 S9 human audit は operational transcript reference / partial boundary audit であり、line onset gold ではない。candidate measurement と Go / No-Go は行わず、T1-2 へ進まない。",
    }


def validate_result(result: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """未測定 result の identity、未測定値、fixture、limits、integrity を固定する。"""

    validate_manifest(manifest, check_sources=True)
    if set(result) != RESULT_TOP_LEVEL_FIELDS:
        raise AnnotationError("T1-1 result top-level fields が固定schemaと一致しません。")
    expected = _expected_pre_measurement_result(manifest)
    for field, expected_value in expected.items():
        if result.get(field) != expected_value:
            raise AnnotationError(f"T1-1 result の pre-measurement field が不正です: {field}")
    _validate_production_integrity_artifacts(result, manifest)
    return {
        "schema": result["schema"],
        "benchmark_id": result["benchmark_id"],
        "manifest_fingerprint": result["manifest_fingerprint"],
        "measurement_status": result["measurement_status"],
        "after_measurement_rehash_status": expected["production_integrity"]["after_measurement_rehash_contract"]["status_at_result"],
        "bound_source_count": len(manifest["production_integrity_contract"]["bound_manifest_sources"]["source_ids"]),
        "bound_telop_document_count": len(manifest["production_integrity_contract"]["bound_telop_documents"]["document_ids"]),
        "bound_artifact_document_count": len(manifest["production_integrity_contract"]["bound_artifact_documents"]["document_ids"]),
    }


def load_manifest(
    path: Path,
    *,
    check_sources: bool = False,
    check_runtime_sources: bool = True,
) -> dict[str, Any]:
    manifest = _read_json(path)
    validate_manifest(manifest, check_sources=check_sources, check_runtime_sources=check_runtime_sources)
    return manifest


def _annotation_contract(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": PACKET_GOLD_PROVENANCE,
        "onset_definition": manifest["policy"]["onset_definition"],
        "do_not_copy": [
            "draft_reference",
            "VTT cue boundary",
            "Whisper token boundary",
            "existing telop time",
            "manual text split boundary",
        ],
        "required_fields": [
            "gold.line_onset_ms",
            "gold.annotator_id",
            "gold.annotated_at",
            "gold.audio_listened",
        ],
        "completion": "各rowを実際に再生して playback receipt を残し、全行を入力するまで測定しない。",
    }


def create_packet(manifest: Mapping[str, Any]) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    rows = []
    for source_row in manifest["rows"]:
        row = {
            key: copy.deepcopy(value)
            for key, value in source_row.items()
            if key in PACKET_ROW_IMMUTABLE_FIELDS
        }
        row["gold"] = copy.deepcopy(GOLD_PLACEHOLDER)
        row["gold_provenance"] = PACKET_GOLD_PROVENANCE
        rows.append(row)
    return {
        "schema": PACKET_SCHEMA,
        "benchmark_id": manifest["benchmark_id"],
        "manifest_fingerprint": validation["manifest_fingerprint"],
        "status": "awaiting_human_audio_annotation",
        "annotation_contract": _annotation_contract(manifest),
        "sources": _packet_sources(manifest),
        "rows": rows,
        "playback_receipts": {},
    }


def _parse_timestamp(value: Any, *, row_id: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AnnotationError(f"{row_id} の annotated_at が空です。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnnotationError(f"{row_id} の annotated_at は ISO 8601 が必要です。") from exc
    if parsed.tzinfo is None:
        raise AnnotationError(f"{row_id} の annotated_at は timezone-aware が必要です。")
    return parsed


def _validate_gold(row: Mapping[str, Any], *, require_complete: bool) -> bool:
    row_id = str(row.get("row_id", "row"))
    gold = row.get("gold")
    if not isinstance(gold, Mapping) or set(gold) != GOLD_FIELDS:
        raise AnnotationError(f"{row_id} の gold fields が不正です。")
    onset = gold.get("line_onset_ms")
    timebase = gold.get("timebase")
    annotator = gold.get("annotator_id")
    annotated_at = gold.get("annotated_at")
    listened = gold.get("audio_listened")
    complete = True
    if isinstance(onset, bool) or not isinstance(onset, int) or onset < 0 or onset >= _row_duration_ms(row):
        complete = False
    if timebase != "source_audio_relative_ms":
        complete = False
    if not isinstance(annotator, str) or not annotator.strip() or "\n" in annotator or "\r" in annotator:
        complete = False
    try:
        _parse_timestamp(annotated_at, row_id=row_id)
    except AnnotationError:
        complete = False
    if listened is not True:
        complete = False
    if not complete and dict(gold) != GOLD_PLACEHOLDER:
        raise AnnotationError(f"{row_id} の gold は未入力placeholderまたは全field completeだけを許可します。")
    if require_complete and not complete:
        raise AnnotationError(f"{row_id} の人手音声 gold が未入力または不正です。")
    return complete


def _receipt_for(packet: Mapping[str, Any], row_id: str) -> Mapping[str, Any] | None:
    receipts = packet.get("playback_receipts")
    if not isinstance(receipts, Mapping):
        return None
    receipt = receipts.get(row_id)
    return receipt if isinstance(receipt, Mapping) else None


def _validate_receipt(
    receipt: Mapping[str, Any],
    row: Mapping[str, Any],
    source: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    onset_ms: int | None = None,
    packet_path: Path | None = None,
    expected_playback_path: Path | None = None,
) -> None:
    if set(receipt) != RECEIPT_FIELDS:
        raise AnnotationError(f"{row['row_id']} の playback receipt fields が不正です。")
    expected_source_hash = source.get("source_content_sha256")
    if (
        receipt.get("manifest_fingerprint") != manifest.get("manifest_fingerprint")
        or receipt.get("row_id") != row.get("row_id")
        or receipt.get("audio_source_id") != row.get("audio_source_id")
        or receipt.get("source_content_sha256") != expected_source_hash
        or receipt.get("target_text") != row.get("target_text")
        or receipt.get("row_source_span") != row.get("source_span")
    ):
        raise AnnotationError(f"{row['row_id']} の playback receipt が固定source/rowと不一致です。")
    from_ms = receipt.get("played_from_ms")
    played_duration = receipt.get("played_duration_ms")
    if isinstance(from_ms, bool) or not isinstance(from_ms, int) or from_ms < 0 or isinstance(played_duration, bool) or not isinstance(played_duration, int) or played_duration <= 0:
        raise AnnotationError(f"{row['row_id']} の playback receipt window が不正です。")
    total = _row_duration_ms(row)
    if from_ms >= total or played_duration > total - from_ms:
        raise AnnotationError(f"{row['row_id']} の playback receipt window が row 範囲外です。")
    if onset_ms is not None and not from_ms <= onset_ms < from_ms + played_duration:
        raise AnnotationError(f"{row['row_id']} の gold onset が実際に再生した窓の外です。")
    if receipt.get("source_span") != _slice_source_span(row["source_span"], from_ms=from_ms, duration_ms=played_duration):
        raise AnnotationError(f"{row['row_id']} の playback receipt span が window と不一致です。")
    playback_path_text = receipt.get("playback_wav_path")
    if not isinstance(playback_path_text, str) or not playback_path_text.startswith("/"):
        raise AnnotationError(f"{row['row_id']} の playback WAV path は絶対パスが必要です。")
    playback_path = Path(playback_path_text)
    if packet_path is not None:
        if expected_playback_path is not None:
            expected_path = expected_playback_path.expanduser().resolve()
            _assert_isolated_packet_path(expected_path)
            if expected_path.parent != _playback_directory(packet_path).resolve():
                raise AnnotationError(f"{row['row_id']} の staging playback WAV path が packet sibling と不一致です。")
        else:
            _validate_packet_playback_path(packet_path, row["row_id"], playback_path)
            expected_path = playback_path.resolve()
        if playback_path.resolve() != expected_path:
            raise AnnotationError(f"{row['row_id']} の playback WAV path が検証対象pathと不一致です。")
    else:
        _assert_isolated_packet_path(playback_path)
    wav_sha = receipt.get("playback_wav_sha256")
    wav_bytes = receipt.get("playback_wav_bytes")
    if not _is_sha256_hex(wav_sha):
        raise AnnotationError(f"{row['row_id']} の playback WAV SHA-256 hex receipt が不正です。")
    if isinstance(wav_bytes, bool) or not isinstance(wav_bytes, int) or wav_bytes <= 44:
        raise AnnotationError(f"{row['row_id']} の playback WAV bytes receipt が不正です。")
    expected_format = {
        "channels": 1,
        "sample_width": 2,
        "sample_rate": 16000,
    }
    if receipt.get("playback_format") != expected_format:
        raise AnnotationError(f"{row['row_id']} の playback WAV format receipt が不正です。")
    if not playback_path.is_file() or playback_path.stat().st_size != wav_bytes:
        raise AnnotationError(f"{row['row_id']} の playback WAV が存在しないか bytes 不一致です。")
    if sha256_file(playback_path) != wav_sha:
        raise AnnotationError(f"{row['row_id']} の playback WAV SHA-256 が receipt と不一致です。")
    try:
        with wave.open(str(playback_path), "rb") as reader:
            actual_format = {
                "channels": reader.getnchannels(),
                "sample_width": reader.getsampwidth(),
                "sample_rate": reader.getframerate(),
            }
            actual_frames = reader.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise AnnotationError(f"{row['row_id']} の playback WAV を検証できません。") from exc
    if actual_format != expected_format or actual_frames != played_duration * 16:
        raise AnnotationError(f"{row['row_id']} の playback WAV format / frame 数が期待値と不一致です。")
    recorded_at = _parse_timestamp(receipt.get("recorded_at"), row_id=row["row_id"])
    if onset_ms is not None:
        annotated_at = _parse_timestamp(row["gold"].get("annotated_at"), row_id=row["row_id"])
        if annotated_at < recorded_at:
            raise AnnotationError(f"{row['row_id']} の annotated_at が playback recorded_at より前です。")


def validate_packet(
    packet: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    require_complete: bool = False,
    check_sources: bool = False,
    check_runtime_sources: bool = True,
    packet_path: Path | None = None,
) -> dict[str, Any]:
    if packet_path is not None:
        _assert_isolated_packet_path(packet_path)
    validate_manifest(manifest, check_sources=check_sources, check_runtime_sources=check_runtime_sources)
    if set(packet) != PACKET_TOP_LEVEL_FIELDS:
        raise AnnotationError("annotation packet の top-level fields が固定契約と一致しません。")
    if packet.get("schema") != PACKET_SCHEMA or packet.get("benchmark_id") != manifest.get("benchmark_id"):
        raise AnnotationError("annotation packet schema / benchmark_id が不一致です。")
    if packet.get("status") != "awaiting_human_audio_annotation":
        raise AnnotationError("annotation packet status が不正です。")
    if packet.get("annotation_contract") != _annotation_contract(manifest):
        raise AnnotationError("annotation packet annotation_contract が固定manifestと一致しません。")
    expected_fingerprint = manifest_fingerprint(manifest)
    if packet.get("manifest_fingerprint") != expected_fingerprint:
        raise AnnotationError("annotation packet が固定manifestの fingerprint と一致しません。")
    expected_packet_sources = _packet_sources(manifest)
    if packet.get("sources") != expected_packet_sources:
        raise AnnotationError("annotation packet の playback sources が固定allowlistと完全一致しません。")
    receipts = packet.get("playback_receipts")
    if not isinstance(receipts, Mapping):
        raise AnnotationError("annotation packet playback_receipts は object が必要です。")
    packet_rows = packet.get("rows")
    manifest_rows = {row["row_id"]: row for row in manifest["rows"]}
    if any(row_id not in manifest_rows for row_id in receipts):
        raise AnnotationError("annotation packet に未知の playback receipt があります。")
    expected_order = list(manifest_rows)
    actual_order = (
        [row.get("row_id") for row in packet_rows]
        if isinstance(packet_rows, list) and all(isinstance(row, Mapping) for row in packet_rows)
        else []
    )
    if not isinstance(packet_rows, list) or len(packet_rows) != len(expected_order) or actual_order != expected_order:
        raise AnnotationError("annotation packet の row 集合・件数・順序が manifest と一致しません。")
    sources = _source_by_id(packet)
    for receipt_row_id, receipt in receipts.items():
        if not isinstance(receipt, Mapping):
            raise AnnotationError(f"{receipt_row_id} の playback receipt は object が必要です。")
        receipt_row = manifest_rows[receipt_row_id]
        _validate_receipt(
            receipt,
            receipt_row,
            sources[receipt_row["audio_source_id"]],
            manifest,
            packet_path=packet_path,
        )
    incomplete: list[str] = []
    for row in packet_rows:
        if not isinstance(row, Mapping):
            raise AnnotationError("annotation packet row が object ではありません。")
        row_id = row.get("row_id")
        if row_id not in manifest_rows:
            raise AnnotationError("annotation packet に未知の row があります。")
        expected = manifest_rows[row_id]
        if set(row) != PACKET_ROW_FIELDS:
            raise AnnotationError(f"{row_id} の packet row fields が固定allowlistと一致しません。")
        for field in PACKET_ROW_IMMUTABLE_FIELDS:
            if row.get(field) != expected.get(field):
                raise AnnotationError(f"{row_id} の immutable field {field} が変更されています。")
        if row.get("gold_provenance") != PACKET_GOLD_PROVENANCE:
            raise AnnotationError(f"{row_id} の gold provenance は音声を聞いた人手入力に固定します。")
        complete = _validate_gold(row, require_complete=require_complete)
        receipt = _receipt_for(packet, row_id)
        if complete or row["gold"].get("audio_listened") is True:
            if receipt is None:
                raise AnnotationError(f"{row_id} は playback receipt がないため gold を受理できません。")
            _validate_receipt(
                receipt,
                row,
                sources[row["audio_source_id"]],
                manifest,
                onset_ms=row["gold"].get("line_onset_ms") if complete else None,
                packet_path=packet_path,
            )
        if not complete:
            incomplete.append(row_id)
    if require_complete and incomplete:
        raise AnnotationError("人手 gold が未完了です。")
    return {
        "manifest_fingerprint": expected_fingerprint,
        "row_count": len(packet_rows),
        "complete_row_count": len(packet_rows) - len(incomplete),
        "incomplete_row_ids": incomplete,
        "status": "ready_for_measurement" if not incomplete else "awaiting_human_audio_annotation",
        "measurement_allowed": not incomplete,
    }


def _source_entry(packet: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any]:
    source_id = row.get("audio_source_id")
    for source in packet.get("sources", {}).values():
        if isinstance(source, Mapping) and source.get("source_id") == source_id:
            return source
    raise AnnotationError(f"{row['row_id']} の音声 source が packet にありません。")


def _next_unfinished_row(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise AnnotationError("packet rows がありません。")
    for row in rows:
        if isinstance(row, Mapping) and not _validate_gold(row, require_complete=False):
            return row
    raise AnnotationError("未完了の annotation row はありません。")


def _verify_play_source(source: Mapping[str, Any]) -> None:
    _check_canonical_source(source, label="play source")


def _select_player(ffmpeg_path: Path) -> tuple[str, str]:
    configured_ffplay = ffmpeg_path.with_name("ffplay")
    if configured_ffplay.is_file() and os.access(configured_ffplay, os.X_OK):
        return str(configured_ffplay), "ffplay"
    system_ffplay = shutil.which("ffplay")
    if system_ffplay:
        return system_ffplay, "ffplay"
    afplay = shutil.which("afplay")
    if afplay:
        return afplay, "afplay"
    raise AnnotationError("ffplay または afplay が見つかりません。音声を再生できる環境で実行してください。")


def _player_command(player: str, player_kind: str, wav_path: Path) -> list[str]:
    if player_kind == "ffplay":
        return [
            player,
            "-nodisp",
            "-autoexit",
            "-stats",
            "-hide_banner",
            "-loglevel",
            "error",
            str(wav_path),
        ]
    if player_kind == "afplay":
        return [player, str(wav_path)]
    raise AnnotationError(f"未対応の audio player です: {player_kind}")


def _validate_playback_info(playback_info: Any) -> None:
    required = {"frames", "bytes", "sha256", "channels", "sample_width", "sample_rate"}
    if not isinstance(playback_info, Mapping) or not required.issubset(playback_info):
        raise AnnotationError("playback WAV info の必須fieldが不足しています。")
    if (
        isinstance(playback_info["frames"], bool)
        or not isinstance(playback_info["frames"], int)
        or playback_info["frames"] <= 0
        or isinstance(playback_info["bytes"], bool)
        or not isinstance(playback_info["bytes"], int)
        or playback_info["bytes"] <= 44
        or not _is_sha256_hex(playback_info["sha256"])
        or playback_info["channels"] != 1
        or playback_info["sample_width"] != 2
        or playback_info["sample_rate"] != 16000
    ):
        raise AnnotationError("playback WAV info の hash / frame / PCM format が不正です。")


def _wav_output_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as reader:
        info = {
            "frames": reader.getnframes(),
            "channels": reader.getnchannels(),
            "sample_width": reader.getsampwidth(),
            "sample_rate": reader.getframerate(),
        }
    info["bytes"] = path.stat().st_size
    info["sha256"] = sha256_file(path)
    return info


def write_span_wav(audio_path: Path, source_span: Mapping[str, Any], output_path: Path) -> dict[str, Any]:
    """WAV の指定 span を取り出し、各partと最終frame数を検証する。"""

    _assert_isolated_packet_path(output_path)
    if not audio_path.is_file():
        raise AnnotationError(f"音声が存在しません: {audio_path}")
    if source_span.get("kind") not in WAV_SINGLE_KINDS | WAV_CONCAT_KINDS:
        raise AnnotationError("WAV span kind が不正です。")
    parts, _ = _parts_and_duration(source_span)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(audio_path), "rb") as reader:
        params = reader.getparams()
        frame_width = params.nchannels * params.sampwidth
        source_frame_count = reader.getnframes()
        expected_total = 0
        chunks: list[bytes] = []
        for part in parts:
            start = part["start_ms"]
            end = part["end_ms"]
            rate = reader.getframerate()
            start_frame = int(start * rate / 1000)
            expected_frames = int((end - start) * rate / 1000)
            if expected_frames <= 0 or start_frame < 0 or start_frame + expected_frames > source_frame_count:
                raise AnnotationError("audio span が source WAV の範囲外です。")
            reader.setpos(start_frame)
            chunk = reader.readframes(expected_frames)
            actual_frames = len(chunk) // frame_width
            if actual_frames != expected_frames or len(chunk) != expected_frames * frame_width or reader.tell() != start_frame + expected_frames:
                raise AnnotationError("source WAV の期待 frame 数と実読込 frame 数が一致しません。")
            chunks.append(chunk)
            expected_total += expected_frames
    with wave.open(str(output_path), "wb") as writer:
        writer.setnchannels(params.nchannels)
        writer.setsampwidth(params.sampwidth)
        writer.setframerate(params.framerate)
        writer.setcomptype(params.comptype, params.compname)
        for chunk in chunks:
            writer.writeframes(chunk)
    with wave.open(str(output_path), "rb") as reader:
        if reader.getnframes() != expected_total:
            raise AnnotationError("isolated WAV の最終 frame 数が期待値と一致しません。")
    return _wav_output_info(output_path)


def _ffmpeg_extract_part(
    ffmpeg_path: Path,
    source_path: Path,
    start_ms: int,
    end_ms: int,
    output_path: Path,
    *,
    expected_source_bytes: int | None = None,
    expected_source_sha256: str | None = None,
    expected_ffmpeg_bytes: int | None = None,
    expected_ffmpeg_sha256: str | None = None,
) -> dict[str, Any]:
    _assert_isolated_packet_path(output_path)
    if end_ms <= start_ms:
        raise AnnotationError("ffmpeg span の end は start より後が必要です。")
    expected_frames = int((end_ms - start_ms) * 16000 / 1000)
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-accurate_seek",
        "-i",
        str(source_path),
        "-map",
        "0:a:0",
        "-af",
        f"aresample=16000,atrim=end_sample={expected_frames},asetpts=N/SR/TB",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    if expected_source_bytes is not None or expected_source_sha256 is not None:
        _check_source_file(
            source_path,
            expected_source_bytes,
            expected_source_sha256,
            label="source MP4 before subprocess",
        )
    if expected_ffmpeg_bytes is not None or expected_ffmpeg_sha256 is not None:
        _check_source_file(
            ffmpeg_path,
            expected_ffmpeg_bytes,
            expected_ffmpeg_sha256,
            label="configured ffmpeg before subprocess",
        )
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AnnotationError(f"isolated ffmpeg extraction failed: {exc}") from exc
    if not output_path.is_file():
        raise AnnotationError("ffmpeg が一時 WAV を生成しませんでした。")
    with wave.open(str(output_path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != 16000:
            raise AnnotationError("ffmpeg 出力 WAV の PCM format が不正です。")
        actual = reader.getnframes()
    if actual != expected_frames:
        raise AnnotationError(f"ffmpeg 出力 frame 数が不一致です: expected={expected_frames} actual={actual}")
    info = _wav_output_info(output_path)
    if info["frames"] != expected_frames or info["channels"] != 1 or info["sample_width"] != 2 or info["sample_rate"] != 16000:
        raise AnnotationError("ffmpeg 出力の frame / format 検証に失敗しました。")
    return info


def write_source_span_wav(
    source: Mapping[str, Any],
    source_span: Mapping[str, Any],
    output_path: Path,
    *,
    ffmpeg_path: Path,
    ffmpeg_bytes: int | None = None,
    ffmpeg_sha256: str | None = None,
) -> dict[str, Any]:
    """WAV cache または source MP4 から隔離 WAV を作る。productionへ書かない。"""

    _assert_isolated_packet_path(output_path)
    _check_canonical_source(source, label="play source")
    kind = _source_content_kind(source)
    if kind == "wav_cache":
        return write_span_wav(_source_content_path(source), source_span, output_path)
    if source_span.get("kind") not in VIDEO_SINGLE_KINDS | VIDEO_CONCAT_KINDS:
        raise AnnotationError("source MP4 には video source span が必要です。")
    parts, _ = _parts_and_duration(source_span)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yt-live-kit-t1-ffmpeg-") as directory:
        temporary_parts: list[Path] = []
        expected_total = 0
        for index, part in enumerate(parts):
            part_path = Path(directory) / f"part-{index:02d}.wav"
            info = _ffmpeg_extract_part(
                ffmpeg_path,
                _source_content_path(source),
                part["start_ms"],
                part["end_ms"],
                part_path,
                expected_source_bytes=source.get("source_content_bytes"),
                expected_source_sha256=source.get("source_content_sha256"),
                expected_ffmpeg_bytes=ffmpeg_bytes,
                expected_ffmpeg_sha256=ffmpeg_sha256,
            )
            expected_total += info["frames"]
            temporary_parts.append(part_path)
        with wave.open(str(output_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            for part_path in temporary_parts:
                with wave.open(str(part_path), "rb") as reader:
                    frames = reader.readframes(reader.getnframes())
                    expected_part = reader.getnframes()
                    if len(frames) != expected_part * 2:
                        raise AnnotationError("ffmpeg concat part の実読込 frame 数が不足しています。")
                    writer.writeframes(frames)
        with wave.open(str(output_path), "rb") as reader:
            if reader.getnframes() != expected_total:
                raise AnnotationError("ffmpeg concat の最終 frame 数が不一致です。")
    info = _wav_output_info(output_path)
    if info["frames"] != expected_total or info["channels"] != 1 or info["sample_width"] != 2 or info["sample_rate"] != 16000:
        raise AnnotationError("ffmpeg concat の最終 frame / format 検証に失敗しました。")
    return info


def _slice_source_span(
    source_span: Mapping[str, Any],
    *,
    from_ms: int,
    duration_ms: int,
) -> dict[str, Any]:
    """concat 後の相対時間で短い再生窓を切り出す。gold値は変更しない。"""

    from_ms = _require_int(from_ms, label="--from-ms")
    duration_ms = _require_int(duration_ms, label="--duration-ms", minimum=1)
    parts, total_duration = _parts_and_duration(source_span)
    if from_ms >= total_duration:
        raise AnnotationError("--from-ms が row の音声 span 外です。")
    end_ms = min(total_duration, from_ms + duration_ms)
    kind = source_span["kind"]
    if kind in WAV_SINGLE_KINDS or kind in VIDEO_SINGLE_KINDS:
        start = parts[0]["start_ms"] + from_ms
        end = parts[0]["start_ms"] + end_ms
        result = {"kind": kind, "start_ms": start, "end_ms": end}
        if kind in VIDEO_SINGLE_KINDS:
            result["coordinate_system"] = "absolute_video_ms"
        return result
    clipped: list[dict[str, int]] = []
    for part in parts:
        part_start = part["concat_offset_ms"]
        part_end = part_start + part["end_ms"] - part["start_ms"]
        overlap_start = max(from_ms, part_start)
        overlap_end = min(end_ms, part_end)
        if overlap_start >= overlap_end:
            continue
        clipped.append(
            {
                "start_ms": part["start_ms"] + overlap_start - part_start,
                "end_ms": part["start_ms"] + overlap_end - part_start,
                "concat_offset_ms": sum(item["end_ms"] - item["start_ms"] for item in clipped),
            }
        )
    if not clipped:
        raise AnnotationError("再生窓に音声 part がありません。")
    clipped_duration = sum(item["end_ms"] - item["start_ms"] for item in clipped)
    result = {"kind": kind, "parts": clipped, "duration_ms": clipped_duration}
    if kind in VIDEO_CONCAT_KINDS:
        result["coordinate_system"] = "absolute_video_ms"
    return result


def _load_packet_and_manifest(
    packet_path: Path,
    manifest_path: Path,
    *,
    check_sources: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _assert_isolated_packet_path(packet_path)
    manifest = load_manifest(manifest_path, check_sources=check_sources)
    packet = _read_json(packet_path)
    validate_packet(packet, manifest, check_sources=check_sources, packet_path=packet_path)
    return packet, manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T1-1 human audio onset annotation packet tool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-manifest")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--check-sources", action="store_true")
    result_validate = subparsers.add_parser("validate-result")
    result_validate.add_argument("--manifest", type=Path, required=True)
    result_validate.add_argument("--result", type=Path, required=True)
    create = subparsers.add_parser("create-packet")
    create.add_argument("--manifest", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--check-sources", action="store_true")
    create.add_argument("--force", action="store_true")
    packet_validate = subparsers.add_parser("validate-packet")
    packet_validate.add_argument("--manifest", type=Path, required=True)
    packet_validate.add_argument("--packet", type=Path, required=True)
    packet_validate.add_argument("--check-sources", action="store_true")
    packet_validate.add_argument("--complete", action="store_true")
    annotate = subparsers.add_parser("annotate")
    annotate.add_argument("--manifest", type=Path, required=True)
    annotate.add_argument("--packet", type=Path, required=True)
    annotate.add_argument("--row-id")
    annotate.add_argument("--onset-ms", type=int, required=True)
    annotate.add_argument("--annotator", required=True)
    annotate.add_argument("--annotated-at")
    annotate.add_argument("--audio-listened", action="store_true")
    play = subparsers.add_parser("play")
    play.add_argument("--manifest", type=Path, required=True)
    play.add_argument("--packet", type=Path, required=True)
    play.add_argument("--row-id")
    play.add_argument("--from-ms", type=int, default=0)
    play.add_argument("--duration-ms", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-manifest":
            print(json.dumps(validate_manifest(_read_json(args.manifest), check_sources=True), ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate-result":
            manifest = load_manifest(args.manifest, check_sources=True)
            print(json.dumps(validate_result(_read_json(args.result), manifest), ensure_ascii=False, indent=2))
            return 0
        if args.command == "create-packet":
            _assert_isolated_packet_path(args.output)
            if args.output.exists() and not args.force:
                raise AnnotationError("出力先が既にあります。上書きする場合だけ --force を指定してください。")
            manifest = load_manifest(args.manifest, check_sources=True)
            _write_json_atomic(args.output, create_packet(manifest))
            print(f"annotation packet を作成しました: {args.output}")
            return 0
        if args.command == "validate-packet":
            packet, manifest = _load_packet_and_manifest(args.packet, args.manifest, check_sources=True)
            print(json.dumps(validate_packet(packet, manifest, require_complete=args.complete, check_sources=True, packet_path=args.packet), ensure_ascii=False, indent=2))
            return 0
        if args.command == "annotate":
            _assert_isolated_packet_path(args.packet)
            packet, manifest = _load_packet_and_manifest(args.packet, args.manifest, check_sources=True)
            if not args.audio_listened:
                raise AnnotationError("音声を再生して確認した場合だけ --audio-listened を指定してください。")
            target = (
                next((row for row in packet["rows"] if row["row_id"] == args.row_id), None)
                if args.row_id
                else _next_unfinished_row(packet)
            )
            if target is None:
                raise AnnotationError(f"row_id がありません: {args.row_id}")
            receipt = _receipt_for(packet, args.row_id)
            if receipt is None:
                receipt = _receipt_for(packet, target["row_id"])
            if receipt is None:
                raise AnnotationError("先に play を実行して playback receipt を作成してください。")
            source = _source_entry(packet, target)
            onset = _require_int(args.onset_ms, label="--onset-ms")
            if onset >= _row_duration_ms(target):
                raise AnnotationError("--onset-ms が row の音声 span 外です。")
            annotated_at = args.annotated_at or datetime.now(timezone.utc).isoformat()
            target["gold"] = {
                "line_onset_ms": onset,
                "timebase": "source_audio_relative_ms",
                "annotator_id": args.annotator,
                "annotated_at": annotated_at,
                "audio_listened": True,
            }
            _validate_gold(target, require_complete=True)
            _validate_receipt(receipt, target, source, manifest, onset_ms=onset, packet_path=args.packet)
            validate_packet(packet, manifest, packet_path=args.packet)
            _write_json_atomic(args.packet, packet)
            print(json.dumps(validate_packet(packet, manifest, packet_path=args.packet), ensure_ascii=False, indent=2))
            return 0
        if args.command == "play":
            _assert_isolated_packet_path(args.packet)
            packet, manifest = _load_packet_and_manifest(args.packet, args.manifest, check_sources=True)
            target = (
                next((row for row in packet["rows"] if row["row_id"] == args.row_id), None)
                if args.row_id
                else _next_unfinished_row(packet)
            )
            if target is None:
                raise AnnotationError(f"row_id がありません: {args.row_id}")
            source = _source_entry(packet, target)
            _verify_play_source(source)
            source_duration = _row_duration_ms(target)
            duration_ms = source_duration - args.from_ms if args.duration_ms is None else args.duration_ms
            playback_span = _slice_source_span(target["source_span"], from_ms=args.from_ms, duration_ms=duration_ms)
            played_duration = _row_duration_ms({"row_id": "playback", "source_span": playback_span})
            print(f"row_id: {target['row_id']}")
            print(f"target_text: {target['target_text']}")
            print(f"source_duration_ms: {source_duration}")
            print(f"playback_window: from_ms={args.from_ms}, duration_ms={played_duration}")
            ffmpeg_path = Path(manifest["runtime"]["ffmpeg"]["path"])
            player, player_kind = _select_player(ffmpeg_path)
            if player_kind == "ffplay":
                print("playback_position: ffplay -stats の audio playback position（candidate/draft 時刻ではありません）")
            else:
                print("playback_position: afplay fallback（terminal の位置表示なし。短窓を反復して確認してください）")
            staging_path = _new_playback_staging_path(args.packet, target["row_id"])
            try:
                playback_info = write_source_span_wav(
                    source,
                    playback_span,
                    staging_path,
                    ffmpeg_path=ffmpeg_path,
                    ffmpeg_bytes=manifest["runtime"]["ffmpeg"]["bytes"],
                    ffmpeg_sha256=manifest["runtime"]["ffmpeg"]["sha256"],
                )
                _validate_playback_info(playback_info)
                command = _player_command(player, player_kind, staging_path)
                subprocess.run(command, check=True)
                staging_receipt = {
                    "manifest_fingerprint": manifest["manifest_fingerprint"],
                    "row_id": target["row_id"],
                    "audio_source_id": target["audio_source_id"],
                    "source_content_sha256": source["source_content_sha256"],
                    "target_text": target["target_text"],
                    "row_source_span": copy.deepcopy(target["source_span"]),
                    "source_span": playback_span,
                    "played_from_ms": args.from_ms,
                    "played_duration_ms": played_duration,
                    "playback_wav_path": str(staging_path),
                    "playback_wav_sha256": playback_info["sha256"],
                    "playback_wav_bytes": playback_info["bytes"],
                    "playback_format": {
                        "channels": playback_info["channels"],
                        "sample_width": playback_info["sample_width"],
                        "sample_rate": playback_info["sample_rate"],
                    },
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
                _validate_receipt(
                    staging_receipt,
                    target,
                    source,
                    manifest,
                    packet_path=args.packet,
                    expected_playback_path=staging_path,
                )
                final_path = _final_playback_path(args.packet, target["row_id"], playback_info["sha256"])
                os.rename(staging_path, final_path)
                receipt = copy.deepcopy(staging_receipt)
                receipt["playback_wav_path"] = str(final_path)
                candidate_packet = copy.deepcopy(packet)
                candidate_packet.setdefault("playback_receipts", {})[target["row_id"]] = receipt
                _validate_receipt(receipt, target, source, manifest, packet_path=args.packet)
                validate_packet(candidate_packet, manifest, packet_path=args.packet)
                _write_json_atomic(args.packet, candidate_packet)
                return 0
            finally:
                staging_path.unlink(missing_ok=True)
    except (AnnotationError, OSError, subprocess.CalledProcessError) as exc:
        print(f"T1-1 annotation error: {exc}", file=sys.stderr)
        return 2
    raise AnnotationError("未対応の command です。")


if __name__ == "__main__":
    raise SystemExit(main())
