"""S9-3 の whisper.cpp runtime・音声 span・artifact writer。

S9-1 の採用 settings contract 以外を通常経路へ持ち込まず、実行前に
``whisper-cli``・model・FFmpeg の実体を検査する。呼び出し側から自由な argv や
shell command を受け取らず、選択した音声 span だけを 1 job 内で入力順に処理する。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import fcntl

from yt_live_kit.config import (
    WHISPER_ADOPTED_CONTRACT,
    WHISPER_BINARY_PATH,
    WHISPER_MODEL_PATH,
    WhisperAdoptedContract,
    Settings,
    get_settings,
)
from yt_live_kit.models.transcript import (
    ArtifactStatus,
    TranscriptArtifact,
    TranscriptCue,
    TranscriptRange,
    TranscriptArtifactStatus,
)
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services._paths import PathConfinementError, confined_path, safe_identifier
from yt_live_kit.services.transcript_artifact import (
    TranscriptArtifactError,
    TranscriptArtifactStore,
    build_transcript_artifact,
    make_cache_identity,
)
from yt_live_kit.services.ytdlp import (
    AudioSpanError,
    AudioSpanRange,
    AudioSpanResult,
    YtdlpError,
    make_audio_span_manifest,
    prepare_audio_span,
)


ADOPTED_WHISPER_BINARY_PATH = WHISPER_BINARY_PATH
ADOPTED_WHISPER_CONTRACT = WHISPER_ADOPTED_CONTRACT
ADOPTED_WHISPER_BINARY_VERSION = WHISPER_ADOPTED_CONTRACT.binary_version
ADOPTED_WHISPER_BINARY_SHA256 = WHISPER_ADOPTED_CONTRACT.binary_sha256
ADOPTED_WHISPER_MODEL_NAME = WHISPER_ADOPTED_CONTRACT.model_name
ADOPTED_WHISPER_MODEL_PATH = WHISPER_MODEL_PATH
ADOPTED_WHISPER_MODEL_SHA256 = WHISPER_ADOPTED_CONTRACT.model_sha256
ADOPTED_WHISPER_OUTPUT_SCHEMA = WHISPER_ADOPTED_CONTRACT.output_schema
ADOPTED_WHISPER_LANGUAGE = WHISPER_ADOPTED_CONTRACT.language
ADOPTED_WHISPER_INITIAL_PROMPT = WHISPER_ADOPTED_CONTRACT.initial_prompt

_REQUIRED_HELP_FLAGS = frozenset(
    {
        "--model",
        "--file",
        "--language",
        "--prompt",
        "--output-json",
        "--output-json-full",
        "--output-file",
        "--threads",
        "--processors",
        "--beam-size",
        "--best-of",
        "--temperature",
        "--no-fallback",
    }
)
_FULL_JSON_ROOT_KEYS = frozenset(
    {"schema", "systeminfo", "model", "params", "result", "transcription"}
)
_FULL_JSON_REQUIRED_ROOT_KEYS = frozenset(
    {"systeminfo", "model", "params", "result", "transcription"}
)
_FULL_JSON_SEGMENT_KEYS = frozenset(
    {"timestamps", "offsets", "text", "tokens", "speaker", "speaker_turn_next"}
)
_FULL_JSON_SEGMENT_REQUIRED_KEYS = frozenset({"timestamps", "offsets", "text", "tokens"})
_FULL_JSON_MODEL_KEYS = frozenset({"type", "multilingual", "vocab", "audio", "text", "mels", "ftype"})
_FULL_JSON_MODEL_DIM_KEYS = frozenset({"ctx", "state", "head", "layer"})
_FULL_JSON_PARAMS_KEYS = frozenset({"model", "language", "translate"})
_FULL_JSON_RESULT_KEYS = frozenset({"language"})
_FULL_JSON_TOKEN_KEYS = frozenset({"text", "timestamps", "offsets", "id", "p", "t_dtw"})
_TIMESTAMP_RE = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})[\.,](?P<millis>\d{3})$"
)
_VERSION_RE = re.compile(r"(?<!\d)(?P<version>\d+\.\d+\.\d+)(?!\d)")


def _diagnostic(value: object) -> str:
    """外部プロセス由来の診断を日本語表示可能な一行へ整える."""

    text = str(value or "").replace("<", "〈").replace(">", "〉").strip()
    return text


class WhisperRuntimeError(Exception):
    """S9 runtime の typed error."""

    code = "whisper_runtime_error"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = _diagnostic(message)
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


class WhisperPreflightError(WhisperRuntimeError):
    """runtime / model / FFmpeg の検査失敗."""

    code = "whisper_preflight_failed"


class WhisperBusyError(WhisperRuntimeError):
    """同時実行中の job があるため拒否した."""

    code = "whisper_job_busy"


class WhisperOutputError(WhisperRuntimeError):
    """whisper.cpp の full JSON が strict schema を満たさない."""

    code = "whisper_output_invalid"


class WhisperExecutionError(WhisperRuntimeError):
    """whisper-cli subprocess の実行失敗."""

    code = "whisper_execution_failed"


class WhisperArtifactError(WhisperRuntimeError):
    """TranscriptArtifact の構築・保存失敗."""

    code = "whisper_artifact_failed"


class WhisperRangeStatus(str, Enum):
    """1 区間の処理状態."""

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class WhisperSettingsContract:
    """S9-1 settings contract を immutable にした実行設定."""

    language: str
    initial_prompt: str
    output_schema: str
    timeout_sec: int
    threads: int
    processors: int
    beam_size: int
    best_of: int
    temperature: float
    no_fallback: bool
    vad: bool
    padding_ms: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "WhisperSettingsContract":
        del settings
        return cls.from_adopted(WHISPER_ADOPTED_CONTRACT)

    @classmethod
    def from_adopted(cls, contract: WhisperAdoptedContract) -> "WhisperSettingsContract":
        return cls(
            language=contract.language,
            initial_prompt=contract.initial_prompt,
            output_schema=contract.output_schema,
            timeout_sec=contract.timeout_sec,
            threads=contract.threads,
            processors=contract.processors,
            beam_size=contract.beam_size,
            best_of=contract.best_of,
            temperature=contract.temperature,
            no_fallback=contract.no_fallback,
            vad=contract.vad,
            padding_ms=contract.padding_ms,
        )

    @property
    def decode(self) -> dict[str, int | float | bool]:
        return {
            "beam_size": self.beam_size,
            "best_of": self.best_of,
            "no_fallback": self.no_fallback,
            "processors": self.processors,
            "temperature": self.temperature,
            "threads": self.threads,
            "vad": self.vad,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "initial_prompt": self.initial_prompt,
            "output_schema": self.output_schema,
            "timeout_sec": self.timeout_sec,
            "padding_ms": self.padding_ms,
            "decode": self.decode,
        }


@dataclass(frozen=True)
class WhisperCapability:
    """preflight で確認した runtime / model / FFmpeg の capability."""

    binary_path: str
    binary_bytes: int
    binary_sha256: str
    version: str
    supported_flags: tuple[str, ...]
    json_timestamp_capability: bool
    model_name: str
    model_path: str
    model_bytes: int
    model_sha256: str
    ffmpeg_path: str
    ffmpeg_version: str
    ffmpeg_capabilities: tuple[str, ...]

    @property
    def runtime_fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json_bytes(
                {
                    "binary_sha256": self.binary_sha256,
                    "version": self.version,
                    "supported_flags": list(self.supported_flags),
                    "json_timestamp_capability": self.json_timestamp_capability,
                    "ffmpeg_version": self.ffmpeg_version,
                    "ffmpeg_capabilities": list(self.ffmpeg_capabilities),
                }
            )
        ).hexdigest()

    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "fingerprint": self.runtime_fingerprint,
            "binary_path": self.binary_path,
            "binary_bytes": self.binary_bytes,
            "binary_sha256": self.binary_sha256,
            "version": self.version,
            "build_capability": {
                "json_timestamp": self.json_timestamp_capability,
                "supported_flags": list(self.supported_flags),
            },
            "ffmpeg": {
                "path": self.ffmpeg_path,
                "version": self.ffmpeg_version,
                "capabilities": list(self.ffmpeg_capabilities),
            },
        }

    def model_metadata(self) -> dict[str, Any]:
        return {
            "name": self.model_name,
            "path": self.model_path,
            "bytes": self.model_bytes,
            "sha256": self.model_sha256,
            "fingerprint": self.model_sha256,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary_path": self.binary_path,
            "binary_bytes": self.binary_bytes,
            "binary_sha256": self.binary_sha256,
            "version": self.version,
            "supported_flags": list(self.supported_flags),
            "json_timestamp_capability": self.json_timestamp_capability,
            "model_name": self.model_name,
            "model_path": self.model_path,
            "model_bytes": self.model_bytes,
            "model_sha256": self.model_sha256,
            "ffmpeg_path": self.ffmpeg_path,
            "ffmpeg_version": self.ffmpeg_version,
            "ffmpeg_capabilities": list(self.ffmpeg_capabilities),
            "runtime_fingerprint": self.runtime_fingerprint,
        }


@dataclass(frozen=True)
class WhisperProcessResult:
    """1 回の whisper-cli subprocess の typed 実行結果."""

    argv: tuple[str, ...]
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int

    @property
    def diagnostic(self) -> str:
        return _diagnostic(self.stderr or self.stdout)

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class WhisperProgress:
    """UI / queue が受け取れる日本語診断付き progress event."""

    job_id: str
    range_index: int
    range_total: int
    status: str
    cache_hit: bool | None
    retryable: bool | None
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "range_index": self.range_index,
            "range_total": self.range_total,
            "status": self.status,
            "cache_hit": self.cache_hit,
            "retryable": self.retryable,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True)
class WhisperRangeResult:
    """区間ごとの status / cache / subprocess / 日本語診断."""

    job_id: str
    range_index: int
    range_total: int
    selected_range: AudioSpanRange
    status: WhisperRangeStatus
    retryable: bool
    cache_hit: bool
    audio_input_fingerprint: str | None = None
    absolute_cues: tuple[TranscriptCue, ...] = ()
    process: WhisperProcessResult | None = None
    diagnostic: str | None = None

    @property
    def success(self) -> bool:
        return self.status is WhisperRangeStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "range_index": self.range_index,
            "range_total": self.range_total,
            "range": self.selected_range.to_dict(),
            "status": self.status.value,
            "retryable": self.retryable,
            "cache_hit": self.cache_hit,
            "audio_input_fingerprint": self.audio_input_fingerprint,
            "cues": [cue.model_dump(mode="json") for cue in self.absolute_cues],
            "process": self.process.to_dict() if self.process is not None else None,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True)
class WhisperJobResult:
    """1 job の全区間結果と保存した artifact の provenance."""

    job_id: str
    status: str
    range_results: tuple[WhisperRangeResult, ...]
    capability: WhisperCapability | None
    settings: WhisperSettingsContract
    artifact: TranscriptArtifact | None = None
    artifact_path: Path | None = None
    cache_hit: bool = False
    retryable: bool = False
    fallback_available: bool = True
    fallback_kind: str = "youtube_vtt"
    diagnostic: str | None = None

    @property
    def is_high_precision(self) -> bool:
        return self.status == "success" and self.artifact is not None and self.artifact.is_high_precision

    @property
    def artifact_fingerprint(self) -> str | None:
        return self.artifact.artifact_fingerprint if self.artifact is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "ranges": [item.to_dict() for item in self.range_results],
            "capability": self.capability.to_dict() if self.capability is not None else None,
            "settings": self.settings.to_dict(),
            "artifact_fingerprint": self.artifact_fingerprint,
            "artifact_path": str(self.artifact_path) if self.artifact_path is not None else None,
            "cache_hit": self.cache_hit,
            "retryable": self.retryable,
            "fallback": {
                "available": self.fallback_available,
                "kind": self.fallback_kind,
            },
            "diagnostic": self.diagnostic,
        }


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WhisperRuntimeError("whisper runtime の fingerprint を計算できません。") from exc


def _file_fingerprint(path: Path, label: str) -> tuple[Path, int, str]:
    try:
        resolved = path.resolve(strict=True)
        stat_result = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise WhisperPreflightError(f"{label}が見つかりません。", retryable=True) from exc
    if not stat.S_ISREG(stat_result.st_mode) or not os.access(resolved, os.R_OK):
        raise WhisperPreflightError(f"{label}を通常のファイルとして読み取れません。", retryable=True)
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WhisperPreflightError(f"{label}の fingerprint を計算できません。", retryable=True) from exc
    return resolved, int(stat_result.st_size), digest.hexdigest()


def _resolved_executable(value: str, label: str) -> Path:
    try:
        resolved_text = shutil.which(value)
    except (OSError, TypeError) as exc:
        raise WhisperPreflightError(f"{label}のパスを確認できません。", retryable=True) from exc
    if resolved_text is None:
        raise WhisperPreflightError(f"{label}が見つかりません。設定を確認してください。", retryable=True)
    resolved, _, _ = _file_fingerprint(Path(resolved_text), label)
    if not os.access(resolved, os.X_OK):
        raise WhisperPreflightError(f"{label}に実行権限がありません。", retryable=True)
    return resolved


def _run_inspection(argv: Sequence[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise WhisperPreflightError(
            f"{label}が {timeout} 秒でタイムアウトしました。",
            retryable=True,
        ) from exc
    except OSError as exc:
        raise WhisperPreflightError(f"{label}を実行できません。", retryable=True) from exc
    if result.returncode != 0:
        detail = _diagnostic(result.stderr or result.stdout)
        raise WhisperPreflightError(
            f"{label}に失敗しました。"
            + (f"（詳細: {detail}）" if detail else ""),
            retryable=True,
            details={"exit_code": result.returncode},
        )
    return result


def _parse_version(stdout: str, stderr: str) -> str:
    combined = f"{stdout}\n{stderr}"
    match = _VERSION_RE.search(combined)
    if match is None:
        raise WhisperPreflightError("whisper-cli の version を確認できません。", retryable=False)
    return match.group("version")


def _preflight_whisper_runtime(
    settings: Settings,
    *,
    adopted_contract: WhisperAdoptedContract,
) -> WhisperCapability:
    """内部 DI 用 preflight。production は immutable contract wrapper を使う."""

    immutable_fields = (
        "binary_version",
        "model_name",
        "output_schema",
        "language",
        "initial_prompt",
        "timeout_sec",
        "threads",
        "processors",
        "beam_size",
        "best_of",
        "temperature",
        "no_fallback",
        "vad",
        "padding_ms",
    )
    contract_mismatch = [
        field
        for field in immutable_fields
        if getattr(adopted_contract, field) != getattr(WHISPER_ADOPTED_CONTRACT, field)
    ]
    if contract_mismatch:
        raise WhisperPreflightError(
            "S9-1 の version / model / schema / prompt / decode / timeout contract は変更できません。",
            retryable=False,
            details={"fields": contract_mismatch},
        )

    contract = WhisperSettingsContract.from_adopted(adopted_contract)

    # production wrapper は S9-1 contract を固定する。ここは test fixture の
    # private injection でも別 contract を誤って正当化しないための fail closed。
    if adopted_contract.language != ADOPTED_WHISPER_LANGUAGE:
        raise WhisperPreflightError("S9 の言語設定は ja 固定です。", retryable=False)
    if adopted_contract.output_schema != ADOPTED_WHISPER_OUTPUT_SCHEMA:
        raise WhisperPreflightError("未知の whisper JSON schema は保存できません。", retryable=False)
    if contract.vad:
        raise WhisperPreflightError("S9 の VAD は採用設定で無効です。", retryable=False)
    if adopted_contract.model_name != ADOPTED_WHISPER_MODEL_NAME:
        raise WhisperPreflightError("S9 の採用 model name と設定が一致しません。", retryable=False)

    binary = _resolved_executable(settings.whisper_binary_path, "whisper-cli")
    binary_path, binary_bytes, binary_sha256 = _file_fingerprint(binary, "whisper-cli")
    if binary_sha256 != adopted_contract.binary_sha256:
        raise WhisperPreflightError(
            "whisper-cli の SHA-256 が設定値と一致しません。モデルや実行ファイルを自動更新せず確認してください。",
            retryable=False,
            details={"actual_sha256": binary_sha256, "expected_sha256": adopted_contract.binary_sha256},
        )
    version_result = _run_inspection(
        [str(binary_path), "--version"],
        timeout=min(adopted_contract.timeout_sec, 30),
        label="whisper-cli version 検査",
    )
    version = _parse_version(version_result.stdout or "", version_result.stderr or "")
    if version != adopted_contract.binary_version:
        raise WhisperPreflightError(
            "whisper-cli の version が設定値と一致しません。",
            retryable=False,
            details={"actual_version": version, "expected_version": adopted_contract.binary_version},
        )

    help_result = _run_inspection(
        [str(binary_path), "--help"],
        timeout=min(adopted_contract.timeout_sec, 30),
        label="whisper-cli capability 検査",
    )
    help_text = f"{help_result.stdout or ''}\n{help_result.stderr or ''}"
    supported_flags = tuple(sorted(flag for flag in _REQUIRED_HELP_FLAGS if flag in help_text))
    missing_flags = sorted(_REQUIRED_HELP_FLAGS - set(supported_flags))
    if missing_flags:
        raise WhisperPreflightError(
            "whisper-cli に必要な JSON timestamp capability がありません。",
            retryable=False,
            details={"missing_flags": missing_flags},
        )

    model_path, model_bytes, model_sha256 = _file_fingerprint(
        Path(settings.whisper_model_path),
        "whisper.cpp model",
    )
    if model_sha256 != adopted_contract.model_sha256:
        raise WhisperPreflightError(
            "whisper.cpp model の SHA-256 が設定値と一致しません。モデル自動取得は行いません。",
            retryable=False,
            details={"actual_sha256": model_sha256, "expected_sha256": adopted_contract.model_sha256},
        )

    ffmpeg_path = _resolved_executable(settings.ffmpeg_path, "FFmpeg")
    ffmpeg_result = _run_inspection(
        [str(ffmpeg_path), "-hide_banner", "-version"],
        timeout=min(settings.ffmpeg_timeout, 30),
        label="FFmpeg capability 検査",
    )
    ffmpeg_text = f"{ffmpeg_result.stdout or ''}\n{ffmpeg_result.stderr or ''}".strip()
    ffmpeg_version_match = re.search(r"ffmpeg version\s+([^\s]+)", ffmpeg_text, re.IGNORECASE)
    ffmpeg_version = ffmpeg_version_match.group(1) if ffmpeg_version_match else "unknown"
    filters_result = _run_inspection(
        [str(ffmpeg_path), "-hide_banner", "-filters"],
        timeout=min(settings.ffmpeg_timeout, 30),
        label="FFmpeg audio filter 検査",
    )
    filters_text = f"{filters_result.stdout or ''}\n{filters_result.stderr or ''}"
    if "aresample" not in filters_text:
        raise WhisperPreflightError(
            "FFmpeg に aresample capability がありません。音声変換を実行できません。",
            retryable=False,
        )
    encoders_result = _run_inspection(
        [str(ffmpeg_path), "-hide_banner", "-encoders"],
        timeout=min(settings.ffmpeg_timeout, 30),
        label="FFmpeg PCM encoder 検査",
    )
    encoders_text = f"{encoders_result.stdout or ''}\n{encoders_result.stderr or ''}"
    if "pcm_s16le" not in encoders_text:
        raise WhisperPreflightError(
            "FFmpeg に pcm_s16le capability がありません。S9 の音声変換を実行できません。",
            retryable=False,
        )

    return WhisperCapability(
        binary_path=str(binary_path),
        binary_bytes=binary_bytes,
        binary_sha256=binary_sha256,
        version=version,
        supported_flags=supported_flags,
        json_timestamp_capability="--output-json-full" in supported_flags,
        model_name=adopted_contract.model_name,
        model_path=str(model_path),
        model_bytes=model_bytes,
        model_sha256=model_sha256,
        ffmpeg_path=str(ffmpeg_path),
        ffmpeg_version=ffmpeg_version,
        ffmpeg_capabilities=("audio_conversion", "aresample", "pcm_s16le"),
    )


def preflight_whisper_runtime(settings: Settings | None = None) -> WhisperCapability:
    """immutable S9-1 contract で whisper-cli・model・FFmpeg を検査する."""

    return _preflight_whisper_runtime(
        settings or get_settings(),
        adopted_contract=WHISPER_ADOPTED_CONTRACT,
    )


def _format_number(value: int | float) -> str:
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def build_whisper_argv(
    *,
    binary_path: str | Path,
    model_path: str | Path,
    audio_path: str | Path,
    output_json_path: str | Path,
    settings: WhisperSettingsContract | Settings | None = None,
    selected_range: AudioSpanRange | None = None,
) -> list[str]:
    """S9-1 contract から固定 whisper-cli argv を構築する."""

    if isinstance(settings, Settings):
        contract = WhisperSettingsContract.from_settings(settings)
    elif isinstance(settings, WhisperSettingsContract):
        contract = settings
    else:
        contract = WhisperSettingsContract.from_adopted(WHISPER_ADOPTED_CONTRACT)
    if contract != WhisperSettingsContract.from_adopted(WHISPER_ADOPTED_CONTRACT):
        raise WhisperRuntimeError(
            "S9-1 の version / model / schema / prompt / decode / timeout contract は変更できません。"
        )
    if contract.vad:
        raise WhisperRuntimeError("S9 の固定 argv では VAD 有効化を許可していません。")
    if contract.output_schema != ADOPTED_WHISPER_OUTPUT_SCHEMA:
        raise WhisperRuntimeError("未知の whisper JSON schema は argv に設定できません。")
    output_path = Path(output_json_path)
    output_prefix = output_path.with_suffix("")
    argv = [
        str(binary_path),
        "--model",
        str(model_path),
        "--file",
        str(audio_path),
        "--language",
        contract.language,
        "--prompt",
        contract.initial_prompt,
        "--output-json",
        "--output-json-full",
        "--output-file",
        str(output_prefix),
        "--no-prints",
        "-t",
        str(contract.threads),
        "-p",
        str(contract.processors),
        "--beam-size",
        _format_number(contract.beam_size),
        "--best-of",
        _format_number(contract.best_of),
        "--temperature",
        _format_number(contract.temperature),
    ]
    if contract.no_fallback:
        argv.append("--no-fallback")
    if selected_range is not None:
        argv.extend(
            [
                "--offset-t",
                "0",
                "--duration",
                _format_number(selected_range.requested_duration_ms),
            ]
        )
    return argv


def _coerce_timestamp_ms(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise WhisperOutputError(f"{label} は整数ミリ秒で指定してください。", retryable=False)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return int(cleaned)
        match = _TIMESTAMP_RE.fullmatch(cleaned)
        if match is None:
            raise WhisperOutputError(f"{label} の timestamp 形式が正しくありません。", retryable=False)
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes"))
        seconds = int(match.group("seconds"))
        if minutes > 59 or seconds > 59:
            raise WhisperOutputError(f"{label} の timestamp 範囲が正しくありません。", retryable=False)
        return hours * 3_600_000 + minutes * 60_000 + seconds * 1_000 + int(match.group("millis"))
    raise WhisperOutputError(f"{label} の timestamp 形式が正しくありません。", retryable=False)


def _strict_mapping(
    value: Any,
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
    details: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WhisperOutputError(f"{label} は object でなければなりません。", retryable=False, details=details)
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        merged = dict(details or {})
        if unknown:
            merged["unknown"] = sorted(str(item) for item in unknown)
        if missing:
            merged["missing"] = sorted(missing)
        raise WhisperOutputError(f"{label} の schema が不正です。", retryable=False, details=merged)
    return value


def _strict_string(value: Any, *, label: str, non_empty: bool = True) -> str:
    if not isinstance(value, str) or (non_empty and not value.strip()) or "\x00" in value:
        raise WhisperOutputError(f"{label} は文字列として不正です。", retryable=False)
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise WhisperOutputError(f"{label} は boolean で指定してください。", retryable=False)
    return value


def _strict_int(value: Any, *, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WhisperOutputError(f"{label} は整数で指定してください。", retryable=False)
    if minimum is not None and value < minimum:
        raise WhisperOutputError(f"{label} の値が範囲外です。", retryable=False)
    return value


def _strict_timing_pair(
    value: Any,
    *,
    label: str,
    offsets: bool,
    allow_zero_length: bool = False,
    allow_unordered: bool = False,
) -> tuple[int, int]:
    pair = _strict_mapping(
        value,
        allowed=frozenset({"from", "to"}),
        required=frozenset({"from", "to"}),
        label=label,
    )
    if offsets:
        start = _strict_int(pair["from"], label=f"{label}.from", minimum=0)
        end = _strict_int(pair["to"], label=f"{label}.to", minimum=0)
    else:
        if not isinstance(pair["from"], str) or not isinstance(pair["to"], str):
            raise WhisperOutputError(f"{label} は timestamp 文字列が必要です。", retryable=False)
        if _TIMESTAMP_RE.fullmatch(pair["from"].strip()) is None or _TIMESTAMP_RE.fullmatch(pair["to"].strip()) is None:
            raise WhisperOutputError(f"{label} は timestamp 文字列が必要です。", retryable=False)
        start = _coerce_timestamp_ms(pair["from"], label=f"{label}.from")
        end = _coerce_timestamp_ms(pair["to"], label=f"{label}.to")
    if start < 0 or end < 0 or (
        not allow_unordered
        and (end < start if allow_zero_length else end <= start)
    ):
        raise WhisperOutputError(f"{label} の時刻範囲が正しくありません。", retryable=False)
    return start, end


def _validate_full_json_model(value: Any) -> None:
    model = _strict_mapping(
        value,
        allowed=_FULL_JSON_MODEL_KEYS,
        required=_FULL_JSON_MODEL_KEYS,
        label="whisper full JSON の model",
    )
    _strict_string(model["type"], label="model.type")
    _strict_bool(model["multilingual"], label="model.multilingual")
    for field in ("vocab", "mels", "ftype"):
        _strict_int(model[field], label=f"model.{field}", minimum=0)
    for field in ("audio", "text"):
        dimensions = _strict_mapping(
            model[field],
            allowed=_FULL_JSON_MODEL_DIM_KEYS,
            required=_FULL_JSON_MODEL_DIM_KEYS,
            label=f"model.{field}",
        )
        for dimension in _FULL_JSON_MODEL_DIM_KEYS:
            _strict_int(dimensions[dimension], label=f"model.{field}.{dimension}", minimum=0)


def _validate_full_json_params(value: Any) -> None:
    params = _strict_mapping(
        value,
        allowed=_FULL_JSON_PARAMS_KEYS,
        required=_FULL_JSON_PARAMS_KEYS,
        label="whisper full JSON の params",
    )
    _strict_string(params["model"], label="params.model")
    if _strict_string(params["language"], label="params.language") != ADOPTED_WHISPER_LANGUAGE:
        raise WhisperOutputError("whisper full JSON の language が ja ではありません。", retryable=False)
    if _strict_bool(params["translate"], label="params.translate"):
        raise WhisperOutputError("S9 の whisper full JSON は translate=false が必要です。", retryable=False)


def _validate_full_json_result(value: Any) -> None:
    result = _strict_mapping(
        value,
        allowed=_FULL_JSON_RESULT_KEYS,
        required=_FULL_JSON_RESULT_KEYS,
        label="whisper full JSON の result",
    )
    if _strict_string(result["language"], label="result.language") != ADOPTED_WHISPER_LANGUAGE:
        raise WhisperOutputError("whisper full JSON の result.language が ja ではありません。", retryable=False)


def _validate_full_json_token(value: Any, *, index: int, token_index: int) -> None:
    token = _strict_mapping(
        value,
        allowed=_FULL_JSON_TOKEN_KEYS,
        required=_FULL_JSON_TOKEN_KEYS,
        label="whisper JSON token",
        details={"index": index, "token_index": token_index},
    )
    _strict_string(token["text"], label="token.text")
    timestamp_pair = _strict_timing_pair(
        token["timestamps"],
        label="token.timestamps",
        offsets=False,
        allow_zero_length=True,
        allow_unordered=True,
    )
    offset_pair = _strict_timing_pair(
        token["offsets"],
        label="token.offsets",
        offsets=True,
        allow_zero_length=True,
        allow_unordered=True,
    )
    if timestamp_pair != offset_pair:
        raise WhisperOutputError("whisper token の timestamps と offsets が一致しません。", retryable=False)
    _strict_int(token["id"], label="token.id")
    probability = token["p"]
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise WhisperOutputError("token.p は数値で指定してください。", retryable=False)
    if not math.isfinite(float(probability)) or probability < 0 or probability > 1:
        raise WhisperOutputError("token.p の値が範囲外です。", retryable=False)
    _strict_int(token["t_dtw"], label="token.t_dtw")


def _validate_full_json_segment(value: Any, *, index: int) -> tuple[int, int]:
    segment = _strict_mapping(
        value,
        allowed=_FULL_JSON_SEGMENT_KEYS,
        required=_FULL_JSON_SEGMENT_REQUIRED_KEYS,
        label="whisper JSON segment",
        details={"index": index},
    )
    text = _strict_string(segment["text"], label="segment.text")
    if not text.strip():
        raise WhisperOutputError("whisper JSON segment の text が空です。", retryable=False, details={"index": index})
    timestamp_pair = _strict_timing_pair(
        segment["timestamps"],
        label="segment.timestamps",
        offsets=False,
    )
    offset_pair = _strict_timing_pair(
        segment["offsets"],
        label="segment.offsets",
        offsets=True,
    )
    if timestamp_pair != offset_pair:
        raise WhisperOutputError("whisper segment の timestamps と offsets が一致しません。", retryable=False, details={"index": index})
    tokens = segment["tokens"]
    if not isinstance(tokens, list) or not tokens:
        raise WhisperOutputError("whisper JSON segment の tokens が空または不正です。", retryable=False, details={"index": index})
    for token_index, token in enumerate(tokens):
        # 1.9.1 は segment の cue 時刻とは別に、先頭 token の metadata が
        # 逆順になる実出力を返すことがある。token は厳密に型・既知 field・
        # timestamps/offsets 一致だけを検証し、cue 境界には使用しない。
        _validate_full_json_token(token, index=index, token_index=token_index)
    if "speaker" in segment and not (
        isinstance(segment["speaker"], str)
        or (isinstance(segment["speaker"], int) and not isinstance(segment["speaker"], bool))
    ):
        raise WhisperOutputError("segment.speaker の型が不正です。", retryable=False, details={"index": index})
    if "speaker_turn_next" in segment:
        _strict_bool(segment["speaker_turn_next"], label="segment.speaker_turn_next")
    return timestamp_pair


def parse_whisper_full_json(
    payload: Mapping[str, Any],
    *,
    absolute_start_ms: int = 0,
    relative_duration_ms: int | None = None,
    allowed_absolute_range: AudioSpanRange | None = None,
    expected_schema: str = ADOPTED_WHISPER_OUTPUT_SCHEMA,
) -> tuple[TranscriptCue, ...]:
    """whisper.cpp 1.9.1 full JSON だけを strict parse して絶対 cue にする."""

    if expected_schema != ADOPTED_WHISPER_OUTPUT_SCHEMA:
        raise WhisperOutputError("未知の whisper JSON schema は保存できません。", retryable=False)
    if not isinstance(payload, Mapping):
        raise WhisperOutputError("whisper output は JSON object でなければなりません。", retryable=False)
    unknown_root = set(payload) - _FULL_JSON_ROOT_KEYS
    if unknown_root:
        raise WhisperOutputError("未知の whisper JSON schema です。", retryable=False)
    missing_root = _FULL_JSON_REQUIRED_ROOT_KEYS - set(payload)
    if missing_root:
        raise WhisperOutputError(
            "whisper.cpp full JSON の必須 field が不足しています。",
            retryable=False,
            details={"missing": sorted(missing_root)},
        )
    # 1.9.1 の実出力は root schema を出さないため、schema は optional とし、
    # 固定された full envelope と nested field 群で schema を識別する。
    if "schema" in payload and payload["schema"] != ADOPTED_WHISPER_OUTPUT_SCHEMA:
        raise WhisperOutputError("whisper JSON schema version が期待値と異なります。", retryable=False)
    if not isinstance(payload["systeminfo"], str) or not payload["systeminfo"].strip():
        raise WhisperOutputError("whisper full JSON の systeminfo が不正です。", retryable=False)
    _validate_full_json_model(payload["model"])
    _validate_full_json_params(payload["params"])
    _validate_full_json_result(payload["result"])
    transcription = payload["transcription"]
    if not isinstance(transcription, list) or not transcription:
        raise WhisperOutputError("whisper JSON の transcription が空または不正です。", retryable=False)
    if isinstance(absolute_start_ms, bool) or not isinstance(absolute_start_ms, int) or absolute_start_ms < 0:
        raise WhisperOutputError("絶対時刻 offset が正しくありません。", retryable=False)
    if relative_duration_ms is not None and (
        isinstance(relative_duration_ms, bool)
        or not isinstance(relative_duration_ms, int)
        or relative_duration_ms <= 0
    ):
        raise WhisperOutputError("音声 span の duration が正しくありません。", retryable=False)

    cues: list[TranscriptCue] = []
    previous_relative_end = -1
    for index, raw_segment in enumerate(transcription):
        if not isinstance(raw_segment, Mapping):
            raise WhisperOutputError("whisper transcription の要素が object ではありません。", retryable=False, details={"index": index})
        relative_start, relative_end = _validate_full_json_segment(raw_segment, index=index)
        if relative_start < 0 or relative_end <= relative_start:
            raise WhisperOutputError("whisper JSON segment の時刻範囲が正しくありません。", retryable=False, details={"index": index})
        if relative_start < previous_relative_end:
            raise WhisperOutputError("whisper JSON segment の timestamp 順序が正しくありません。", retryable=False, details={"index": index})
        if relative_duration_ms is not None and relative_end > relative_duration_ms:
            raise WhisperOutputError("whisper JSON segment が音声 span の範囲外です。", retryable=False, details={"index": index})
        absolute_start = absolute_start_ms + relative_start
        absolute_end = absolute_start_ms + relative_end
        if allowed_absolute_range is not None:
            if (
                absolute_start < allowed_absolute_range.requested_start_ms
                or absolute_end > allowed_absolute_range.requested_end_ms
            ):
                raise WhisperOutputError("whisper JSON cue が選択区間の範囲外です。", retryable=False, details={"index": index})
        cleaned_text = str(raw_segment["text"]).strip().replace("<", "〈").replace(">", "〉")
        cues.append(
            TranscriptCue(
                start_ms=absolute_start,
                end_ms=absolute_end,
                text=cleaned_text,
            )
        )
        previous_relative_end = relative_end
    return tuple(cues)


parse_whisper_json = parse_whisper_full_json
parse_whisper_output = parse_whisper_full_json


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object に重複した field があります。")
        result[key] = value
    return result


def _read_json_output(path: Path, stdout: str) -> Mapping[str, Any]:
    candidates: list[str] = []
    try:
        if path.is_file():
            candidates.append(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise WhisperOutputError("whisper JSON output を読み取れません。", retryable=False) from exc
    if not candidates and stdout.strip():
        candidates.append(stdout)
    if not candidates:
        raise WhisperOutputError("whisper JSON output が生成されませんでした。", retryable=False)
    raw = candidates[0]
    try:
        payload = json.loads(raw, object_pairs_hook=_json_object_without_duplicates)
    except (json.JSONDecodeError, TypeError, UnicodeError, ValueError) as exc:
        raise WhisperOutputError("whisper JSON output が壊れているか途中で切れています。", retryable=False) from exc
    if not isinstance(payload, Mapping):
        raise WhisperOutputError("whisper JSON output の root が object ではありません。", retryable=False)
    return payload


def _relative_video_path(path: Path, video_id: str, settings: Settings) -> str:
    try:
        root = (Path(settings.data_dir) / video_id).resolve()
        absolute = path.resolve()
        relative = absolute.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WhisperArtifactError("audio cache の source path が data ディレクトリ外です。", retryable=False) from exc
    if any(part in {".", ".."} for part in relative.parts):
        raise WhisperArtifactError("audio cache の source path が正しくありません。", retryable=False)
    return relative.as_posix()


def _combine_audio_bytes(spans: Sequence[AudioSpanResult]) -> bytes:
    combined = bytearray()
    for span in spans:
        content = span.audio_bytes
        combined.extend(len(content).to_bytes(8, "big", signed=False))
        combined.extend(content)
    return bytes(combined)


def _artifact_ranges(
    ranges: Sequence[AudioSpanRange],
    statuses: Sequence[WhisperRangeStatus],
) -> tuple[TranscriptRange, ...]:
    result: list[TranscriptRange] = []
    for item, status in zip(ranges, statuses, strict=True):
        result.append(
            TranscriptRange(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                padding_before_ms=item.padding_before_ms,
                padding_after_ms=item.padding_after_ms,
                inclusion_rule=item.inclusion_rule,
                status=(
                    TranscriptArtifactStatus.SUCCESS
                    if status is WhisperRangeStatus.SUCCESS
                    else TranscriptArtifactStatus.FAILED
                ),
            )
        )
    return tuple(result)


def _source_metadata_for_artifact(
    video_id: str,
    ranges: Sequence[AudioSpanRange],
    spans: Sequence[AudioSpanResult],
) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "source_kind": "youtube_audio_span",
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "range_manifest": [item.to_dict() for item in ranges],
        "audio_spans": [
            {
                "range": span.range.to_dict(),
                "audio_input_fingerprint": span.audio_input_fingerprint,
                "sample_rate": span.sample_rate,
                "channel": span.channel,
                "codec": span.codec,
                "ffmpeg": span.ffmpeg_settings,
                "source_metadata": span.source_metadata,
            }
            for span in spans
        ],
    }


def _settings_metadata(contract: WhisperSettingsContract) -> dict[str, Any]:
    return {
        "language": contract.language,
        "initial_prompt": contract.initial_prompt,
        "output_schema": contract.output_schema,
        "padding_ms": contract.padding_ms,
        "vad": contract.vad,
        "decode": contract.decode,
    }


def _build_artifact(
    *,
    video_id: str,
    settings: Settings,
    capability: WhisperCapability,
    contract: WhisperSettingsContract,
    ranges: Sequence[AudioSpanRange],
    range_statuses: Sequence[WhisperRangeStatus],
    cues: Sequence[TranscriptCue],
    spans: Sequence[AudioSpanResult],
) -> TranscriptArtifact:
    if not spans:
        raise WhisperArtifactError("保存する音声 span がありません。", retryable=False)
    combined_audio = _combine_audio_bytes(spans)
    first_source_ref = _relative_video_path(spans[0].path, video_id, settings)
    source_metadata = _source_metadata_for_artifact(video_id, ranges, spans)
    model = capability.model_metadata()
    runtime = capability.runtime_metadata()
    artifact_settings = _settings_metadata(contract)
    ffmpeg_settings = {
        "sample_rate": spans[0].sample_rate,
        "channel": spans[0].channel,
        "codec": spans[0].codec,
        "conversion": spans[0].ffmpeg_settings,
        "ffmpeg_version": capability.ffmpeg_version,
    }
    if any(
        (span.sample_rate, span.channel, span.codec)
        != (spans[0].sample_rate, spans[0].channel, spans[0].codec)
        for span in spans[1:]
    ):
        raise WhisperArtifactError("複数音声 span の形式が一致しません。", retryable=False)
    artifact_status = (
        TranscriptArtifactStatus.SUCCESS
        if all(status is WhisperRangeStatus.SUCCESS for status in range_statuses)
        else (
            TranscriptArtifactStatus.PARTIAL
            if any(status is WhisperRangeStatus.SUCCESS for status in range_statuses)
            else TranscriptArtifactStatus.FAILED
        )
    )
    try:
        return build_transcript_artifact(
            video_id=video_id,
            source_kind="whisper_cpp",
            source_ref=first_source_ref,
            language=contract.language,
            ranges=_artifact_ranges(ranges, range_statuses),
            cues=cues,
            status=artifact_status,
            source_metadata=source_metadata,
            model=model,
            model_fingerprint=capability.model_sha256,
            runtime=runtime,
            runtime_fingerprint=capability.runtime_fingerprint,
            settings=artifact_settings,
            decode_settings=contract.decode,
            initial_prompt=contract.initial_prompt,
            audio_bytes=combined_audio,
            sample_rate=spans[0].sample_rate,
            channel=spans[0].channel,
            codec=spans[0].codec,
            ffmpeg_settings=ffmpeg_settings,
            padding=None,
            vad={"enabled": contract.vad},
            output_schema=contract.output_schema,
        )
    except (TranscriptArtifactError, ValueError) as exc:
        raise WhisperArtifactError("TranscriptArtifact の構築に失敗しました。", retryable=False) from exc


_GATE_LOCKS: dict[str, threading.Lock] = {}
_GATE_LOCKS_GUARD = threading.Lock()


@contextmanager
def _job_gate(settings: Settings):
    root = Path(settings.data_dir).resolve()
    key = str(root)
    with _GATE_LOCKS_GUARD:
        local_lock = _GATE_LOCKS.setdefault(key, threading.Lock())
    if not local_lock.acquire(blocking=False):
        raise WhisperBusyError("Whisper job は同時に1件だけ実行できます。現在の処理が終わるまで待ってください。", retryable=True)
    descriptor = -1
    locked = False
    try:
        jobs_dir = confined_path(settings.data_dir, "_jobs", label="Whisper job lock")
        jobs_dir.mkdir(parents=True, exist_ok=True)
        lock_path = jobs_dir / "whisper.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise WhisperBusyError("Whisper job は同時に1件だけ実行できます。", retryable=True) from exc
        yield
    except WhisperRuntimeError:
        raise
    except (OSError, PathConfinementError) as exc:
        raise WhisperBusyError("Whisper job の排他 lock を取得できません。", retryable=True) from exc
    finally:
        if locked:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        local_lock.release()


def _new_job_id(job_id: str | None) -> str:
    if job_id is None:
        return f"whisper-{uuid.uuid4().hex}"
    try:
        return safe_identifier(job_id, "job ID")
    except PathConfinementError as exc:
        raise WhisperRuntimeError("job ID が正しくありません。", retryable=False) from exc


def _emit_progress(
    callback: Callable[[WhisperProgress], None] | None,
    event: WhisperProgress,
) -> None:
    if callback is not None:
        callback(event)


def _range_failure(
    *,
    job_id: str,
    index: int,
    total: int,
    selected_range: AudioSpanRange,
    diagnostic: str,
    retryable: bool,
    cache_hit: bool = False,
    audio_input_fingerprint: str | None = None,
    process: WhisperProcessResult | None = None,
) -> WhisperRangeResult:
    return WhisperRangeResult(
        job_id=job_id,
        range_index=index,
        range_total=total,
        selected_range=selected_range,
        status=WhisperRangeStatus.FAILED,
        retryable=retryable,
        cache_hit=cache_hit,
        audio_input_fingerprint=audio_input_fingerprint,
        process=process,
        diagnostic=_diagnostic(diagnostic),
    )


def _process_whisper(
    argv: Sequence[str],
    *,
    timeout_sec: int,
) -> WhisperProcessResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return WhisperProcessResult(
            argv=tuple(str(item) for item in argv),
            stdout=_diagnostic(completed.stdout),
            stderr=_diagnostic(completed.stderr),
            exit_code=int(completed.returncode),
            timed_out=False,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _diagnostic(exc.stdout)
        stderr = _diagnostic(exc.stderr)
        return WhisperProcessResult(
            argv=tuple(str(item) for item in argv),
            stdout=stdout,
            stderr=stderr,
            exit_code=None,
            timed_out=True,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    except OSError as exc:
        raise WhisperExecutionError("whisper-cli を実行できません。", retryable=True) from exc


def _whisper_output_path(temp_dir: Path) -> Path:
    path = temp_dir / "result.json"
    try:
        path.relative_to(temp_dir)
    except ValueError as exc:  # pragma: no cover - fixed path
        raise WhisperOutputError("whisper JSON の保存先が正しくありません。", retryable=False) from exc
    return path


def run_selected_ranges(
    video_id: str,
    selected_ranges: Sequence[AudioSpanRange | Any],
    settings: Settings | None = None,
    *,
    job_id: str | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    on_progress: Callable[[WhisperProgress], None] | None = None,
) -> WhisperJobResult:
    """選択区間だけを 1 job 内で直列に音声化・Whisper・保存する."""

    settings = settings or get_settings()
    job_id = _new_job_id(job_id)
    contract = WhisperSettingsContract.from_settings(settings)
    safe_video_id = str(video_id).strip()
    try:
        safe_identifier(safe_video_id, "動画 ID")
    except PathConfinementError as exc:
        raise WhisperRuntimeError("動画 ID が正しくありません。", retryable=False) from exc
    ranges = make_audio_span_manifest(
        safe_video_id,
        selected_ranges,
        padding_ms=contract.padding_ms,
    )
    total = len(ranges)
    with _job_gate(settings):
        try:
            capability = preflight_whisper_runtime(settings)
        except WhisperRuntimeError as exc:
            range_results = tuple(
                _range_failure(
                    job_id=job_id,
                    index=index,
                    total=total,
                    selected_range=item,
                    diagnostic=exc.message,
                    retryable=exc.retryable,
                )
                for index, item in enumerate(ranges, start=1)
            )
            for result in range_results:
                _emit_progress(
                    on_progress,
                    WhisperProgress(
                        job_id=job_id,
                        range_index=result.range_index,
                        range_total=total,
                        status=result.status.value,
                        cache_hit=False,
                        retryable=result.retryable,
                        diagnostic=result.diagnostic,
                    ),
                )
            return WhisperJobResult(
                job_id=job_id,
                status="failed",
                range_results=range_results,
                capability=None,
                settings=contract,
                retryable=exc.retryable,
                diagnostic=exc.message,
            )

        audio_spans: list[AudioSpanResult] = []
        audio_failures: dict[int, WhisperRangeResult] = {}
        for index, item in enumerate(ranges, start=1):
            _emit_progress(
                on_progress,
                WhisperProgress(job_id, index, total, "audio_preparing", None, None),
            )
            try:
                span = prepare_audio_span(
                    safe_video_id,
                    item,
                    settings,
                    source_metadata=source_metadata,
                )
                audio_spans.append(span)
            except (AudioSpanError, YtdlpError, WhisperRuntimeError) as exc:
                audio_failures[index] = _range_failure(
                    job_id=job_id,
                    index=index,
                    total=total,
                    selected_range=item,
                    diagnostic=str(exc),
                    retryable=isinstance(exc, (AudioSpanError, YtdlpError)) or getattr(exc, "retryable", False),
                )

        if audio_failures:
            # 取得できた span は cache として残すが、range 列が欠けた状態で
            # Whisper artifact を作らない。既存 VTT fallback は明示する。
            range_results = tuple(
                audio_failures.get(
                    index,
                    _range_failure(
                        job_id=job_id,
                        index=index,
                        total=total,
                        selected_range=item,
                        diagnostic="同一 job の音声 span が不足しているため高精度化できません。",
                        retryable=True,
                    ),
                )
                for index, item in enumerate(ranges, start=1)
            )
            for result in range_results:
                _emit_progress(
                    on_progress,
                    WhisperProgress(job_id, result.range_index, total, result.status.value, result.cache_hit, result.retryable, result.diagnostic),
                )
            return WhisperJobResult(
                job_id=job_id,
                status="partial" if any(item.success for item in range_results) else "failed",
                range_results=range_results,
                capability=capability,
                settings=contract,
                retryable=any(item.retryable for item in range_results),
                diagnostic="選択区間の一部音声を準備できませんでした。既存 YouTube VTT を明示的に fallback としてください。",
            )

        # prepare_audio_span は入力順に append するため、manifest と span の
        # index は同じである。音声 bytes / metadata を一度だけ束ねて cache identity
        # を作り、hit 時は Whisper subprocess を一切呼ばない。
        if len(audio_spans) != total:
            raise WhisperRuntimeError("音声 span の個数が job manifest と一致しません。", retryable=False)
        combined_audio = _combine_audio_bytes(audio_spans)
        source_metadata_for_artifact = _source_metadata_for_artifact(
            safe_video_id,
            ranges,
            audio_spans,
        )
        first_source_ref = _relative_video_path(audio_spans[0].path, safe_video_id, settings)
        model_metadata = capability.model_metadata()
        runtime_metadata = capability.runtime_metadata()
        artifact_settings = _settings_metadata(contract)
        ffmpeg_settings = {
            "sample_rate": audio_spans[0].sample_rate,
            "channel": audio_spans[0].channel,
            "codec": audio_spans[0].codec,
            "conversion": audio_spans[0].ffmpeg_settings,
            "ffmpeg_version": capability.ffmpeg_version,
        }
        identity_ranges = tuple(
            {
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "padding_before_ms": item.padding_before_ms,
                "padding_after_ms": item.padding_after_ms,
                "inclusion_rule": item.inclusion_rule,
            }
            for item in ranges
        )
        try:
            cache_identity = make_cache_identity(
                source_kind="whisper_cpp",
                language=contract.language,
                ranges=identity_ranges,
                source_metadata=source_metadata_for_artifact,
                audio_bytes=combined_audio,
                sample_rate=audio_spans[0].sample_rate,
                channel=audio_spans[0].channel,
                codec=audio_spans[0].codec,
                ffmpeg_settings=ffmpeg_settings,
                source={"source_ref": first_source_ref, "source_url": None, "source_fingerprint": None},
                model=model_metadata,
                model_fingerprint=capability.model_sha256,
                runtime=runtime_metadata,
                runtime_fingerprint=capability.runtime_fingerprint,
                settings=artifact_settings,
                decode_settings=contract.decode,
                initial_prompt=contract.initial_prompt,
                padding=None,
                vad={"enabled": contract.vad},
                output_schema=contract.output_schema,
            )
        except TranscriptArtifactError as exc:
            raise WhisperArtifactError("Whisper cache identity を計算できません。", retryable=False) from exc

        store = TranscriptArtifactStore(safe_video_id, settings)
        try:
            cache_candidates = store.find_by_cache_identity(cache_identity)
        except TranscriptArtifactError:
            cache_candidates = ()
        for candidate in cache_candidates:
            if (
                candidate.is_high_precision
                and candidate.status is TranscriptArtifactStatus.SUCCESS
                and len(candidate.ranges) == total
                and all(item.status is TranscriptArtifactStatus.SUCCESS for item in candidate.ranges)
            ):
                range_results = tuple(
                    WhisperRangeResult(
                        job_id=job_id,
                        range_index=index,
                        range_total=total,
                        selected_range=item,
                        status=WhisperRangeStatus.SUCCESS,
                        retryable=False,
                        cache_hit=True,
                        audio_input_fingerprint=audio_spans[index - 1].audio_input_fingerprint,
                        absolute_cues=tuple(
                            cue
                            for cue in candidate.cues
                            if cue.start_ms < item.requested_end_ms and cue.end_ms > item.requested_start_ms
                        ),
                    )
                    for index, item in enumerate(ranges, start=1)
                )
                for result in range_results:
                    _emit_progress(
                        on_progress,
                        WhisperProgress(job_id, result.range_index, total, "success", True, False),
                    )
                return WhisperJobResult(
                    job_id=job_id,
                    status="success",
                    range_results=range_results,
                    capability=capability,
                    settings=contract,
                    artifact=candidate,
                    artifact_path=store._artifact_path(candidate.artifact_fingerprint),
                    cache_hit=True,
                    retryable=False,
                )

        range_results_by_index: dict[int, WhisperRangeResult] = {}
        all_cues: list[TranscriptCue] = []
        for index, (item, span) in enumerate(zip(ranges, audio_spans, strict=True), start=1):
            _emit_progress(
                on_progress,
                WhisperProgress(job_id, index, total, "whisper_running", span.cache_hit, None),
            )
            temporary_dir: Path | None = None
            process: WhisperProcessResult | None = None
            try:
                temporary_dir = Path(tempfile.mkdtemp(prefix=".s9-whisper-span-", dir=span.path.parent))
                audio_path = span.path.resolve(strict=True)
                output_path = _whisper_output_path(temporary_dir)
                argv = build_whisper_argv(
                    binary_path=capability.binary_path,
                    model_path=capability.model_path,
                    audio_path=audio_path,
                    output_json_path=output_path,
                    settings=contract,
                    selected_range=item,
                )
                process = _process_whisper(argv, timeout_sec=contract.timeout_sec)
                if process.timed_out:
                    result = _range_failure(
                        job_id=job_id,
                        index=index,
                        total=total,
                        selected_range=item,
                        diagnostic="whisper-cli の実行がタイムアウトしました。再試行できます。",
                        retryable=True,
                        cache_hit=span.cache_hit,
                        audio_input_fingerprint=span.audio_input_fingerprint,
                        process=process,
                    )
                elif process.exit_code != 0:
                    result = _range_failure(
                        job_id=job_id,
                        index=index,
                        total=total,
                        selected_range=item,
                        diagnostic="Whisper（whisper-cli）がエラー終了しました。"
                        + (f"（詳細: {process.diagnostic}）" if process.diagnostic else ""),
                        retryable=True,
                        cache_hit=span.cache_hit,
                        audio_input_fingerprint=span.audio_input_fingerprint,
                        process=process,
                    )
                else:
                    payload = _read_json_output(output_path, process.stdout)
                    cues = parse_whisper_full_json(
                        payload,
                        absolute_start_ms=item.requested_start_ms,
                        relative_duration_ms=item.requested_duration_ms,
                        allowed_absolute_range=item,
                        expected_schema=contract.output_schema,
                    )
                    all_cues.extend(cues)
                    result = WhisperRangeResult(
                        job_id=job_id,
                        range_index=index,
                        range_total=total,
                        selected_range=item,
                        status=WhisperRangeStatus.SUCCESS,
                        retryable=False,
                        cache_hit=span.cache_hit,
                        audio_input_fingerprint=span.audio_input_fingerprint,
                        absolute_cues=cues,
                        process=process,
                    )
            except WhisperOutputError as exc:
                result = _range_failure(
                    job_id=job_id,
                    index=index,
                    total=total,
                    selected_range=item,
                    diagnostic=str(exc),
                    retryable=exc.retryable,
                    cache_hit=span.cache_hit,
                    audio_input_fingerprint=span.audio_input_fingerprint,
                    process=process,
                )
            except WhisperExecutionError as exc:
                result = _range_failure(
                    job_id=job_id,
                    index=index,
                    total=total,
                    selected_range=item,
                    diagnostic=str(exc),
                    retryable=exc.retryable,
                    cache_hit=span.cache_hit,
                    audio_input_fingerprint=span.audio_input_fingerprint,
                    process=process,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                result = _range_failure(
                    job_id=job_id,
                    index=index,
                    total=total,
                    selected_range=item,
                    diagnostic="whisper output の処理に失敗しました。保存せず既存 VTT fallback とします。",
                    retryable=False,
                    cache_hit=span.cache_hit,
                    audio_input_fingerprint=span.audio_input_fingerprint,
                    process=process,
                )
            finally:
                if temporary_dir is not None:
                    try:
                        shutil.rmtree(temporary_dir)
                    except OSError:
                        # temporary output を残したまま成功扱いにしない。
                        result = _range_failure(
                            job_id=job_id,
                            index=index,
                            total=total,
                            selected_range=item,
                            diagnostic="whisper temporary output を削除できません。",
                            retryable=True,
                            cache_hit=span.cache_hit,
                            audio_input_fingerprint=span.audio_input_fingerprint,
                        )
            range_results_by_index[index] = result
            _emit_progress(
                on_progress,
                WhisperProgress(job_id, index, total, result.status.value, result.cache_hit, result.retryable, result.diagnostic),
            )

        ordered_results = tuple(range_results_by_index[index] for index in range(1, total + 1))
        statuses = tuple(item.status for item in ordered_results)
        artifact: TranscriptArtifact | None = None
        artifact_path: Path | None = None
        artifact_diagnostic: str | None = None
        try:
            artifact = _build_artifact(
                video_id=safe_video_id,
                settings=settings,
                capability=capability,
                contract=contract,
                ranges=ranges,
                range_statuses=statuses,
                cues=all_cues,
                spans=audio_spans,
            )
            artifact_path = store.save(artifact)
        except WhisperArtifactError as exc:
            artifact_diagnostic = str(exc)
        except TranscriptArtifactError as exc:
            artifact_diagnostic = "TranscriptArtifact を atomic に保存できませんでした。"

        if all(item.success for item in ordered_results) and artifact is not None:
            status = "success"
            diagnostic = None
            retryable = False
        elif any(item.success for item in ordered_results):
            status = "partial"
            diagnostic = "選択区間の一部が失敗したため、高精度 artifact として返しません。既存 YouTube VTT を明示的に fallback としてください。"
            retryable = any(item.retryable for item in ordered_results)
        else:
            status = "failed"
            diagnostic = "選択区間の Whisper 精査に失敗しました。既存 YouTube VTT を明示的に fallback としてください。"
            retryable = any(item.retryable for item in ordered_results)
        if artifact_diagnostic:
            diagnostic = artifact_diagnostic if status == "success" else f"{diagnostic} {artifact_diagnostic}"
            if status == "success":
                status = "failed"
                retryable = False
        return WhisperJobResult(
            job_id=job_id,
            status=status,
            range_results=ordered_results,
            capability=capability,
            settings=contract,
            artifact=artifact,
            artifact_path=artifact_path,
            cache_hit=False,
            retryable=retryable,
            diagnostic=diagnostic,
        )


run_whisper_job = run_selected_ranges
transcribe_selected_ranges = run_selected_ranges
refine_selected_ranges = run_selected_ranges


__all__ = [
    "ADOPTED_WHISPER_CONTRACT",
    "ADOPTED_WHISPER_BINARY_PATH",
    "ADOPTED_WHISPER_BINARY_SHA256",
    "ADOPTED_WHISPER_BINARY_VERSION",
    "ADOPTED_WHISPER_INITIAL_PROMPT",
    "ADOPTED_WHISPER_LANGUAGE",
    "ADOPTED_WHISPER_MODEL_NAME",
    "ADOPTED_WHISPER_MODEL_PATH",
    "ADOPTED_WHISPER_MODEL_SHA256",
    "ADOPTED_WHISPER_OUTPUT_SCHEMA",
    "WhisperArtifactError",
    "WhisperBusyError",
    "WhisperCapability",
    "WhisperExecutionError",
    "WhisperJobResult",
    "WhisperOutputError",
    "WhisperPreflightError",
    "WhisperProcessResult",
    "WhisperProgress",
    "WhisperRangeResult",
    "WhisperRangeStatus",
    "WhisperRuntimeError",
    "WhisperSettingsContract",
    "build_whisper_argv",
    "parse_whisper_full_json",
    "parse_whisper_json",
    "parse_whisper_output",
    "preflight_whisper_runtime",
    "refine_selected_ranges",
    "run_selected_ranges",
    "run_whisper_job",
    "transcribe_selected_ranges",
]
