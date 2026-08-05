"""T1-1 ローカル review UI の副作用境界。

Streamlit の再実行に依存しない packet 読込、音声準備、gold 保存だけを置く。
manifest の内部境界や候補時刻を UI へ渡さず、検証と WAV 抽出は
annotation_packet.py の既存契約へ委譲する。
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import sys
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.t1 import annotation_packet as packet_tool


DEFAULT_MANIFEST_PATH = Path("benchmarks/t1/manifest.json")
DEFAULT_PACKET_PATH = Path("/tmp/yt-live-kit-t1-1-human-gold.json")


def configured_packet_path() -> Path:
    """通常は固定契約path、テスト時だけ隔離した同型の一時pathを許可する。"""

    override = os.environ.get("T1_REVIEW_PACKET_PATH")
    return Path(override) if override else DEFAULT_PACKET_PATH


@dataclass(frozen=True)
class ReviewSession:
    manifest: dict[str, Any]
    packet: dict[str, Any]
    validation: dict[str, Any]


@dataclass(frozen=True)
class PreparedPlayback:
    row_id: str
    receipt: dict[str, Any]
    wav_path: Path
    wav_bytes: bytes
    is_existing: bool


def load_review_session(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    packet_path: Path = DEFAULT_PACKET_PATH,
) -> ReviewSession:
    """source hash を毎回確認して packet を fail-closed で読み込む。"""

    packet_tool._assert_isolated_packet_path(packet_path)
    manifest = packet_tool.load_manifest(
        manifest_path,
        check_sources=True,
        check_runtime_sources=False,
    )
    if not packet_path.exists():
        packet_tool._write_json_atomic(packet_path, packet_tool.create_packet(manifest))
    packet = packet_tool._read_json(packet_path)
    validation = packet_tool.validate_packet(
        packet,
        manifest,
        check_sources=True,
        check_runtime_sources=False,
        packet_path=packet_path,
    )
    return ReviewSession(manifest=manifest, packet=packet, validation=validation)


def row_position(packet: Mapping[str, Any], row_id: str) -> int:
    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise packet_tool.AnnotationError("packet rows がありません。")
    for index, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("row_id") == row_id:
            return index
    raise packet_tool.AnnotationError(f"row_id がありません: {row_id}")


def unfinished_row_index(packet: Mapping[str, Any]) -> int | None:
    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise packet_tool.AnnotationError("packet rows がありません。")
    for index, row in enumerate(rows):
        if isinstance(row, Mapping) and not packet_tool._validate_gold(row, require_complete=False):
            return index
    return None


def next_unfinished_row_index(packet: Mapping[str, Any], after_index: int) -> int | None:
    """保存後の遷移先。手前にスキップした行より、現在位置より後の未完了行を優先する。"""

    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise packet_tool.AnnotationError("packet rows がありません。")
    if after_index < -1 or after_index >= len(rows):
        raise packet_tool.AnnotationError("after_index が不正です。")
    for index in range(after_index + 1, len(rows)):
        row = rows[index]
        if isinstance(row, Mapping) and not packet_tool._validate_gold(row, require_complete=False):
            return index
    return unfinished_row_index(packet)


def row_at(packet: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    rows = packet.get("rows")
    if not isinstance(rows, list) or not rows:
        raise packet_tool.AnnotationError("packet rows が空です。")
    if index < 0 or index >= len(rows) or not isinstance(rows[index], Mapping):
        raise packet_tool.AnnotationError("現在の row index が不正です。")
    return rows[index]


def row_duration_ms(row: Mapping[str, Any]) -> int:
    return packet_tool._row_duration_ms(row)


def _source_for_row(packet: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any]:
    return packet_tool._source_entry(packet, row)


@contextmanager
def _packet_write_lock(packet_path: Path):
    """同一 packet への複数 UI session の read/validate/write を直列化する。"""

    packet_tool._assert_isolated_packet_path(packet_path)
    lock_path = packet_path.parent / f".{packet_path.name}.lock"
    packet_tool._assert_isolated_packet_path(lock_path)
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _receipt_payload(
    manifest: Mapping[str, Any],
    row: Mapping[str, Any],
    source: Mapping[str, Any],
    playback_span: Mapping[str, Any],
    from_ms: int,
    played_duration_ms: int,
    wav_path: Path,
    playback_info: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "row_id": row["row_id"],
        "audio_source_id": row["audio_source_id"],
        "source_content_sha256": source["source_content_sha256"],
        "target_text": row["target_text"],
        "row_source_span": copy.deepcopy(row["source_span"]),
        "source_span": copy.deepcopy(playback_span),
        "played_from_ms": from_ms,
        "played_duration_ms": played_duration_ms,
        "playback_wav_path": str(wav_path),
        "playback_wav_sha256": playback_info["sha256"],
        "playback_wav_bytes": playback_info["bytes"],
        "playback_format": {
            "channels": playback_info["channels"],
            "sample_width": playback_info["sample_width"],
            "sample_rate": playback_info["sample_rate"],
        },
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_existing_playback(
    session: ReviewSession,
    row: Mapping[str, Any],
    receipt: Mapping[str, Any],
    packet_path: Path,
) -> PreparedPlayback:
    source = _source_for_row(session.packet, row)
    packet_tool._validate_receipt(
        receipt,
        row,
        source,
        session.manifest,
        packet_path=packet_path,
    )
    wav_path = Path(str(receipt["playback_wav_path"]))
    return PreparedPlayback(
        row_id=str(row["row_id"]),
        receipt=dict(receipt),
        wav_path=wav_path,
        wav_bytes=wav_path.read_bytes(),
        is_existing=True,
    )


def ensure_default_playback(
    session: ReviewSession,
    row_id: str,
    packet_path: Path = DEFAULT_PACKET_PATH,
    *,
    cached: PreparedPlayback | None = None,
) -> PreparedPlayback:
    """未準備行は先頭から行末までの既定再生窓を自動で用意する。"""

    if cached is not None and cached.row_id == row_id:
        return cached
    existing = existing_playback(session, row_id, packet_path)
    if existing is not None:
        return existing
    return prepare_playback(
        session,
        row_id,
        from_ms=0,
        duration_ms=None,
        packet_path=packet_path,
    )


def existing_playback(
    session: ReviewSession,
    row_id: str,
    packet_path: Path = DEFAULT_PACKET_PATH,
) -> PreparedPlayback | None:
    row = row_at(session.packet, row_position(session.packet, row_id))
    receipts = session.packet.get("playback_receipts")
    if not isinstance(receipts, Mapping):
        raise packet_tool.AnnotationError("playback_receipts が不正です。")
    receipt = receipts.get(row_id)
    if receipt is None:
        return None
    if not isinstance(receipt, Mapping):
        raise packet_tool.AnnotationError(f"{row_id} の playback receipt が不正です。")
    return _validate_existing_playback(session, row, receipt, packet_path)


def prepare_playback(
    session: ReviewSession,
    row_id: str,
    *,
    from_ms: int,
    duration_ms: int | None,
    packet_path: Path = DEFAULT_PACKET_PATH,
) -> PreparedPlayback:
    """既存の安全な source hash 検証・WAV 抽出・receipt検証を使い、ブラウザ用 WAV を準備する。"""

    row = row_at(session.packet, row_position(session.packet, row_id))
    source = _source_for_row(session.packet, row)
    packet_tool._verify_play_source(source)
    total_duration = row_duration_ms(row)
    effective_duration = total_duration - from_ms if duration_ms is None else duration_ms
    playback_span = packet_tool._slice_source_span(
        row["source_span"],
        from_ms=from_ms,
        duration_ms=effective_duration,
    )
    played_duration = row_duration_ms({"row_id": "browser-playback", "source_span": playback_span})
    ffmpeg = session.manifest["runtime"]["ffmpeg"]
    staging_path = packet_tool._new_playback_staging_path(packet_path, row_id)
    try:
        playback_info = packet_tool.write_source_span_wav(
            source,
            playback_span,
            staging_path,
            ffmpeg_path=Path(ffmpeg["path"]),
            ffmpeg_bytes=ffmpeg["bytes"],
            ffmpeg_sha256=ffmpeg["sha256"],
        )
        packet_tool._validate_playback_info(playback_info)
        receipt = _receipt_payload(
            session.manifest,
            row,
            source,
            playback_span,
            from_ms,
            played_duration,
            staging_path,
            playback_info,
        )
        packet_tool._validate_receipt(
            receipt,
            row,
            source,
            session.manifest,
            packet_path=packet_path,
            expected_playback_path=staging_path,
        )
        return PreparedPlayback(
            row_id=row_id,
            receipt=receipt,
            wav_path=staging_path,
            wav_bytes=staging_path.read_bytes(),
            is_existing=False,
        )
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise


def commit_annotation(
    session: ReviewSession,
    row_id: str,
    onset_ms: int,
    annotator_id: str,
    audio_listened: bool,
    prepared: PreparedPlayback,
    *,
    packet_path: Path = DEFAULT_PACKET_PATH,
) -> dict[str, Any]:
    """gold、receipt、WAV を全て検証してから packet を atomic 保存する。"""

    if not audio_listened:
        raise packet_tool.AnnotationError("音声を実際に確認した場合だけ保存できます。")
    with _packet_write_lock(packet_path):
        current = load_review_session(packet_path=packet_path)
        row_index = row_position(current.packet, row_id)
        row = current.packet["rows"][row_index]
        if prepared.row_id != row_id:
            raise packet_tool.AnnotationError("再生済み row と保存対象 row が一致しません。")
        source = _source_for_row(current.packet, row)
        playback_path = prepared.wav_path
        if prepared.is_existing:
            packet_tool._validate_receipt(
                prepared.receipt,
                row,
                source,
                current.manifest,
                packet_path=packet_path,
            )
        else:
            packet_tool._validate_receipt(
                prepared.receipt,
                row,
                source,
                current.manifest,
                packet_path=packet_path,
                expected_playback_path=playback_path,
            )

        candidate = copy.deepcopy(current.packet)
        candidate_row = candidate["rows"][row_index]
        candidate_row["gold"] = {
            "line_onset_ms": packet_tool._require_int(onset_ms, label="発話開始位置 (ms)"),
            "timebase": "source_audio_relative_ms",
            "annotator_id": annotator_id,
            "annotated_at": datetime.now(timezone.utc).isoformat(),
            "audio_listened": True,
        }
        packet_tool._validate_gold(candidate_row, require_complete=True)
        packet_tool._validate_receipt(
            prepared.receipt,
            candidate_row,
            source,
            current.manifest,
            onset_ms=onset_ms,
            packet_path=packet_path,
            **({} if prepared.is_existing else {"expected_playback_path": playback_path}),
        )

        receipt = copy.deepcopy(prepared.receipt)
        if not prepared.is_existing:
            final_path = packet_tool._final_playback_path(packet_path, row_id, receipt["playback_wav_sha256"])
            # same sibling directory and a fresh UUID path make this a no-overwrite promotion.
            os.link(playback_path, final_path)
            playback_path.unlink()
            receipt["playback_wav_path"] = str(final_path)
        candidate["playback_receipts"][row_id] = receipt
        packet_tool._validate_receipt(
            receipt,
            candidate_row,
            source,
            current.manifest,
            onset_ms=onset_ms,
            packet_path=packet_path,
        )
        validation = packet_tool.validate_packet(
            candidate,
            current.manifest,
            check_sources=True,
            check_runtime_sources=False,
            packet_path=packet_path,
        )
        packet_tool._write_json_atomic(packet_path, candidate)
        return validation


def complete_validation(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    packet_path: Path = DEFAULT_PACKET_PATH,
) -> dict[str, Any]:
    session = load_review_session(manifest_path=manifest_path, packet_path=packet_path)
    return packet_tool.validate_packet(
        session.packet,
        session.manifest,
        require_complete=True,
        check_sources=True,
        check_runtime_sources=False,
        packet_path=packet_path,
    )
