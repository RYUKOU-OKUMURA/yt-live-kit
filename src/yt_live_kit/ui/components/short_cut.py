"""長い候補を刻んでショートにする導線（FR-30）.

180 秒を超える切り抜き候補 / ハイライト候補を親として選び、AI にサブ区間を
提案させ、人が採否と境界を確認してから FR-25 のジャンプカット連結へ渡す。
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.short_cut import ShortCutDocument
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.jobs import JobBusyError, is_busy, start_job
from yt_live_kit.services.short_cut import (
    MAX_TOTAL_MS,
    MIN_TOTAL_MS,
    ParentCandidate,
    ShortCutError,
    ShortCutValidationResult,
    cutplan_artifact_lineage_is_current,
    load_cut_plan,
    needs_short_cut,
    refine_selected_short_cut,
    suggest_short_cuts,
    validate_short_cut_selection,
)
from yt_live_kit.services.shorts import build_short_from_segments
from yt_live_kit.services.subtitle_burn import (
    TimedCue,
    filter_cues_for_segment,
    parse_vtt_with_end,
)
from yt_live_kit.services.telop import make_clip_id
from yt_live_kit.services.transcript_artifact import (
    TranscriptArtifactError,
    TranscriptArtifactStore,
)
from yt_live_kit.services.whisper_runtime import WhisperSettingsContract
from yt_live_kit.ui.state import set_active_job_id

_BUSY_MESSAGE = "他の処理が実行中です。完了までお待ちください。"
_TIMESTAMP_FORMAT_ERROR = "時刻は HH:MM:SS の形式で入力してください。"
_SECTION_NOTE = (
    "180 秒を超える候補から、ショート 1 本分の区間を AI に提案させます。"
    "提案は必ず確認・調整してから作成してください。"
)
_NO_LONG_CANDIDATE_MESSAGE = (
    "180 秒を超える候補がありません。"
    "そのままショートにできる候補は上の「ショートを作成」から生成してください。"
)
_NO_TRANSCRIPT_MESSAGE = "この区間に表示できる文字起こしはありません。"
_INVALID_TRANSCRIPT_BOUNDS_MESSAGE = (
    "入力中の時刻が不正なため、提案時の区間を表示しています。"
)
_HH_MM_SS_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")

LAYOUT_BLUR_LABEL = "ぼかし背景（推奨）"
LAYOUT_CROP_LABEL = "中央クロップ"
LAYOUT_LABELS = (LAYOUT_BLUR_LABEL, LAYOUT_CROP_LABEL)
S9_WHISPER_PROGRESS_PREFIX = "S9_WHISPER_PROGRESS:"
S9_WHISPER_ERROR_PREFIX = "S9_WHISPER_ERROR:"


def _whisper_stage_for_status(status: str) -> str:
    if status == "audio_preparing":
        return "span"
    if status == "whisper_running":
        return "Whisper"
    if status in {"success", "failed", "partial"}:
        return "artifact"
    return "Whisper"


def _whisper_progress_payload(
    progress: object,
    segments: Sequence[HighlightSegment],
    *,
    stage: str,
) -> dict[str, object]:
    """WhisperProgress を jobs の既存 message 欄へ渡す表示用 snapshot にする."""
    try:
        range_index = int(getattr(progress, "range_index"))
        range_total = int(getattr(progress, "range_total"))
    except (TypeError, ValueError):
        range_index = 0
        range_total = len(segments)
    current_range: dict[str, str] | None = None
    if 1 <= range_index <= len(segments):
        segment = segments[range_index - 1]
        current_range = {
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
        }
    return {
        "schema": "s9-whisper-progress-v1",
        "job_id": str(getattr(progress, "job_id", "")),
        "stage": stage,
        "status": str(getattr(progress, "status", "unknown")),
        "range_index": range_index,
        "range_total": range_total,
        "current_range": current_range,
        "cache_hit": getattr(progress, "cache_hit", None),
        "retryable": getattr(progress, "retryable", None),
        "diagnostic": getattr(progress, "diagnostic", None),
    }


def _report_whisper_progress(
    report,
    progress: object,
    segments: Sequence[HighlightSegment],
    *,
    stage: str,
) -> None:
    payload = _whisper_progress_payload(progress, segments, stage=stage)
    report(
        stage=stage,
        message=S9_WHISPER_PROGRESS_PREFIX
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        current=int(payload["range_index"]),
        total=int(payload["range_total"]),
    )


def _whisper_error_payload(
    job_id: str,
    segments: Sequence[HighlightSegment],
    progress_events: Sequence[object],
    error: BaseException,
) -> dict[str, object]:
    reason = str(error).replace("<", "〈").replace(">", "〉").strip()
    lowered = reason.lower()
    if "一部" in reason or "partial" in lowered:
        code = "partial_failure"
    elif "timeout" in lowered or "タイムアウト" in reason:
        code = "timeout"
    elif "model" in lowered or "モデル" in reason:
        code = "model_mismatch"
    elif "cache" in lowered or "キャッシュ" in reason:
        code = "cache_corruption"
    elif "artifact" in lowered or "保存" in reason:
        code = "artifact_failure"
    else:
        code = "refine_failed"
    ranges = [
        _whisper_progress_payload(
            event,
            segments,
            stage=_whisper_stage_for_status(str(getattr(event, "status", "unknown"))),
        )
        for event in progress_events
    ]
    retryable = any(bool(item.get("retryable")) for item in ranges)
    return {
        "schema": "s9-whisper-error-v1",
        "code": code,
        "job_id": job_id,
        "range_index": ranges[-1].get("range_index") if ranges else None,
        "range_total": len(segments),
        "ranges": ranges,
        "retryable": retryable,
        "existing_artifacts": "維持",
        "next_action": (
            "対象区間を確認して再試行してください。"
            if retryable
            else "既存 VTT fallback を明示的に選ぶか、処理を停止してください。"
        ),
        "reason": reason,
    }


def _whisper_error_message(payload: dict[str, object]) -> str:
    return (
        "選択区間の高精度字幕に失敗しました。"
        f"job ID: {payload.get('job_id', '不明')}。"
        f"再試行: {'可' if payload.get('retryable') else '不可'}。"
        f"既存成果物: {payload.get('existing_artifacts', '維持')}。"
        f"次操作: {payload.get('next_action', '詳細を確認してください。')}"
        f"理由: {payload.get('reason', '不明')}\n"
        f"{S9_WHISPER_ERROR_PREFIX}"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


@dataclass(frozen=True)
class ParentOption:
    """親候補の選択肢（表示用ソース名つき）."""

    source_label: str
    candidate: ParentCandidate

    @property
    def id(self) -> str:
        return self.candidate.id

    @property
    def source(self) -> str:
        """表示順や翻訳文言に依存しない候補ソースを返す."""
        return "clip" if isinstance(self.candidate, ClipCandidate) else "highlight"

    @property
    def identity(self) -> str:
        """同じ ID が別ソースに存在しても衝突しない選択 identity."""
        return f"{self.source}:{self.id}"

    @property
    def label(self) -> str:
        return (
            f"[{self.source_label}] {self.candidate.id}: {self.candidate.title}"
            f"（{self.candidate.start} → {self.candidate.end}、"
            f"{self.candidate.duration_sec} 秒）"
        )


def collect_parent_options(
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
) -> list[ParentOption]:
    """刻む対象になり得る（180 秒超の）候補だけを表示順で返す."""
    options: list[ParentOption] = []
    for candidate in clip_candidates:
        if needs_short_cut(candidate):
            options.append(ParentOption("切り抜き", candidate))
    for segment in highlight_candidates:
        if needs_short_cut(segment):
            options.append(ParentOption("ハイライト", segment))
    return options


def resolve_parent_option_identity(
    options: Sequence[ParentOption],
    stored_value: object,
    *,
    preferred_candidate_ids: Sequence[str] = (),
) -> str:
    """保存済み選択を source + ID identity へ安全に正規化する.

    R2 より前の session は配列 index を保存していたため、有効な整数だけを現在の
    選択肢へ移行する。削除済み・不正値は preferred、先頭の順で決定的に戻す。
    """
    if not options:
        raise ValueError("親候補がありません。")

    identities = {option.identity for option in options}
    if isinstance(stored_value, str) and stored_value in identities:
        return stored_value
    if type(stored_value) is int and 0 <= stored_value < len(options):
        return options[stored_value].identity
    for preferred_id in preferred_candidate_ids:
        for option in options:
            if option.id == preferred_id:
                return option.identity
    return options[0].identity


def layout_from_label(label: str) -> str:
    """UI ラベルからレイアウト値（blur / crop）を返す."""
    return "crop" if label == LAYOUT_CROP_LABEL else "blur"


def parse_cut_timestamp(text: str) -> tuple[float | None, str | None]:
    """HH:MM:SS を秒に変換する。不正ならエラーメッセージを返す."""
    stripped = text.strip()
    if not stripped or not _HH_MM_SS_RE.match(stripped):
        return None, _TIMESTAMP_FORMAT_ERROR
    try:
        return float(parse_timestamp_to_seconds(stripped)), None
    except ValueError:
        return None, _TIMESTAMP_FORMAT_ERROR


def shift_cut_timestamp(text: str, delta_sec: int) -> tuple[str | None, str | None]:
    """正規の HH:MM:SS 境界を整数秒だけ前後へ移動する."""
    seconds, error = parse_cut_timestamp(text)
    if error is not None or seconds is None:
        return None, error
    shifted = int(seconds) + delta_sec
    if shifted < 0:
        return None, "開始・終了時刻は 0 秒より前にできません。"
    hours, remainder = divmod(shifted, 3600)
    minutes, value = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{value:02d}", None


def _shift_cut_input(key: str, delta_sec: int) -> None:
    value = str(st.session_state.get(key, ""))
    shifted, error = shift_cut_timestamp(value, delta_sec)
    if shifted is not None and error is None:
        st.session_state[key] = shifted


def _render_time_assist(key: str, label: str) -> None:
    with st.container(horizontal=True, gap="small"):
        st.caption(label)
        for delta in (-5, -1, 1, 5):
            sign = "+" if delta > 0 else ""
            st.button(
                f"{sign}{delta} 秒",
                key=f"{key}_shift_{delta}",
                on_click=_shift_cut_input,
                args=(key, delta),
            )


def resolve_transcript_bounds(
    start_text: str,
    end_text: str,
    fallback_start: str,
    fallback_end: str,
) -> tuple[float | None, float | None, bool]:
    """文字起こし表示用の境界を解決し、不正入力時は提案値へ戻す."""
    start_sec, start_error = parse_cut_timestamp(start_text)
    end_sec, end_error = parse_cut_timestamp(end_text)
    if (
        start_error is None
        and end_error is None
        and start_sec is not None
        and end_sec is not None
        and start_sec < end_sec
    ):
        return start_sec, end_sec, False

    fallback_start_sec, fallback_start_error = parse_cut_timestamp(fallback_start)
    fallback_end_sec, fallback_end_error = parse_cut_timestamp(fallback_end)
    if (
        fallback_start_error is None
        and fallback_end_error is None
        and fallback_start_sec is not None
        and fallback_end_sec is not None
        and fallback_start_sec < fallback_end_sec
    ):
        return fallback_start_sec, fallback_end_sec, True
    return None, None, True


def extract_segment_text(
    cues: Sequence[TimedCue],
    start_sec: float,
    end_sec: float,
) -> str:
    """区間と重なる字幕を、連続する完全同一行をまとめて本文にする."""
    if end_sec <= start_sec:
        return ""

    lines: list[str] = []
    for cue in filter_cues_for_segment(list(cues), start_sec, end_sec):
        text = cue.text.strip()
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return "\n".join(lines)


def _vtt_file_identity(vtt_path: Path) -> tuple[object, ...]:
    """VTT cache を失効させる軽量な file identity を返す."""
    try:
        metadata = vtt_path.stat()
    except FileNotFoundError:
        return ("missing",)
    except OSError:
        return ("unreadable",)
    if not stat.S_ISREG(metadata.st_mode):
        return ("missing",)
    return (
        "file",
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
    )


@st.cache_data(show_spinner=False, max_entries=64)
def _load_transcript_cues_cached(
    vtt_path: Path,
    file_identity: tuple[object, ...],
) -> tuple[tuple[TimedCue, ...], str | None]:
    """path と file identity が同じ間だけ VTT の parse 結果を再利用する."""
    if file_identity[0] == "missing":
        return (), "文字起こしファイルが見つからないため、区間内容を表示できません。"
    if file_identity[0] != "file":
        return (), "文字起こしファイルを読み込めないため、区間内容を表示できません。"
    try:
        content = vtt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return (), "文字起こしファイルを読み込めないため、区間内容を表示できません。"
    try:
        cues = parse_vtt_with_end(content)
    except Exception:
        return (), "文字起こしファイルを解析できないため、区間内容を表示できません。"
    if not cues:
        return (), "文字起こしに表示できる内容がありません。"
    return tuple(cues), None


def load_transcript_cues(
    video_id: str,
    data_dir: Path,
) -> tuple[tuple[TimedCue, ...], str | None]:
    """VTT を file identity 付き cache から読み込む."""
    vtt_path = data_dir / video_id / "subtitles" / "ja.vtt"
    return _load_transcript_cues_cached(vtt_path, _vtt_file_identity(vtt_path))


# 既存テスト・呼び出し元が利用する cache clear API を互換維持する。
load_transcript_cues.clear = (  # type: ignore[attr-defined]
    _load_transcript_cues_cached.clear
)


def load_transcript_cues_for_document(
    video_id: str,
    document: ShortCutDocument,
    settings: Settings,
) -> tuple[tuple[TimedCue, ...], str | None]:
    """cutplan の artifact ref があれば同じ absolute cue だけを表示する."""
    if document.artifact_ref is None:
        return load_transcript_cues(video_id, settings.data_dir)
    reference = document.artifact_ref
    if reference.video_id != video_id:
        return (), "高精度字幕 artifact の動画 ID が一致しないため表示を停止しました。"
    if reference.source_kind.value != "whisper_cpp":
        return (), "高精度化済み cutplan が Whisper artifact ではないため表示を停止しました。"
    if document.artifact_fingerprint is None or not document.used_range_cue_digests:
        return (), "高精度字幕 artifact lineage が不完全なため表示を停止しました。"
    if not cutplan_artifact_lineage_is_current(video_id, document, settings):
        return (), (
            "保存済み高精度字幕 artifact が cutplan の lineage と一致しないため、"
            "表示を停止しました。既存 VTT へ自動差替えしません。"
        )
    try:
        store = TranscriptArtifactStore(video_id, settings)
        artifact = store.load_artifact(document.artifact_fingerprint)
    except (TranscriptArtifactError, OSError, ValueError):
        return (), "保存済み高精度字幕 artifact を読み込めないため表示を停止しました。"
    cues = tuple(
        TimedCue(cue.start_ms / 1000, cue.end_ms / 1000, cue.text)
        for cue in artifact.cues
    )
    if not cues:
        return (), "高精度字幕 artifact に表示できる cue がないため表示を停止しました。"
    return cues, None


def format_total_ms(total_ms: int) -> str:
    """合計尺の表示文字列を返す."""
    return f"{total_ms / 1000:.1f} 秒"


def _short_cut_editor_prefix(video_id: str, parent_identity: str) -> str:
    parent_digest = hashlib.sha256(parent_identity.encode("utf-8")).hexdigest()
    return f"short_cut_editor_{video_id}_{parent_digest}_"


def short_cut_draft_identity(document: ShortCutDocument) -> str:
    """提案 document 全体と artifact lineage の immutable identity を返す."""
    canonical = json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def checkbox_key(
    video_id: str,
    cut_id: str,
    *,
    parent_identity: str | None = None,
) -> str:
    """採否チェックボックスの session_state キーを返す."""
    if parent_identity is not None:
        return f"{_short_cut_editor_prefix(video_id, parent_identity)}cb_{cut_id}"
    return f"short_cut_cb_{video_id}_{cut_id}"


def start_key(
    video_id: str,
    cut_id: str,
    *,
    parent_identity: str | None = None,
) -> str:
    """開始時刻入力の session_state キーを返す."""
    if parent_identity is not None:
        return f"{_short_cut_editor_prefix(video_id, parent_identity)}start_{cut_id}"
    return f"short_cut_start_{video_id}_{cut_id}"


def end_key(
    video_id: str,
    cut_id: str,
    *,
    parent_identity: str | None = None,
) -> str:
    """終了時刻入力の session_state キーを返す."""
    if parent_identity is not None:
        return f"{_short_cut_editor_prefix(video_id, parent_identity)}end_{cut_id}"
    return f"short_cut_end_{video_id}_{cut_id}"


def sync_short_cut_editor_state(
    document: ShortCutDocument,
    video_id: str,
    parent_identity: str,
    session_state: MutableMapping[str, object],
) -> str:
    """draft revision ごとに対象親の editor buffer だけを初期化する.

    同じ identity の再描画では手編集を保持する。新しい提案・lineage では widget
    描画前に対象親 prefix を消し、document の値を唯一の初期値として設定する。
    """
    prefix = _short_cut_editor_prefix(video_id, parent_identity)
    identity_key = f"{prefix}draft_identity"
    identity = short_cut_draft_identity(document)
    if session_state.get(identity_key) != identity:
        for key in tuple(session_state):
            if isinstance(key, str) and key.startswith(prefix):
                del session_state[key]
        session_state[identity_key] = identity

    for candidate in document.candidates:
        session_state.setdefault(
            checkbox_key(
                video_id,
                candidate.id,
                parent_identity=parent_identity,
            ),
            True,
        )
        session_state.setdefault(
            start_key(
                video_id,
                candidate.id,
                parent_identity=parent_identity,
            ),
            candidate.start,
        )
        session_state.setdefault(
            end_key(
                video_id,
                candidate.id,
                parent_identity=parent_identity,
            ),
            candidate.end,
        )
    return identity


def collect_edited_segments(
    document: ShortCutDocument,
    video_id: str,
    session_state: Mapping[str, object],
    *,
    parent_identity: str | None = None,
) -> tuple[list[HighlightSegment], list[str]]:
    """session_state から採用中の区間を、編集後の時刻で組み立てる."""
    segments: list[HighlightSegment] = []
    errors: list[str] = []
    for candidate in document.candidates:
        if not session_state.get(
            checkbox_key(
                video_id,
                candidate.id,
                parent_identity=parent_identity,
            ),
            True,
        ):
            continue
        start_text = session_state.get(
            start_key(
                video_id,
                candidate.id,
                parent_identity=parent_identity,
            )
        )
        end_text = session_state.get(
            end_key(
                video_id,
                candidate.id,
                parent_identity=parent_identity,
            )
        )
        start_value = candidate.start if start_text is None else str(start_text)
        end_value = candidate.end if end_text is None else str(end_text)

        start_sec, start_error = parse_cut_timestamp(start_value)
        if start_error:
            errors.append(f"{candidate.id}: 開始{start_error}")
            continue
        end_sec, end_error = parse_cut_timestamp(end_value)
        if end_error:
            errors.append(f"{candidate.id}: 終了{end_error}")
            continue
        assert start_sec is not None and end_sec is not None
        if end_sec <= start_sec:
            errors.append(f"{candidate.id}: 終了時刻は開始時刻より後にしてください。")
            continue

        segments.append(
            HighlightSegment(
                id=candidate.id,
                title=candidate.title,
                start=start_value.strip(),
                end=end_value.strip(),
                duration_sec=int(end_sec - start_sec),
                reason=candidate.reason,
            )
        )
    return segments, errors


def segments_to_pairs(segments: Sequence[HighlightSegment]) -> list[tuple[float, float]]:
    """区間列を build_short_from_segments 用の秒ペアへ変換する."""
    pairs: list[tuple[float, float]] = []
    for segment in segments:
        start_sec, _ = parse_cut_timestamp(segment.start)
        end_sec, _ = parse_cut_timestamp(segment.end)
        if start_sec is None or end_sec is None:
            raise ValueError(f"{segment.id}: {_TIMESTAMP_FORMAT_ERROR}")
        pairs.append((start_sec, end_sec))
    return pairs


def short_cut_output_path(
    video_id: str,
    segments: Sequence[HighlightSegment],
    settings: Settings,
) -> Path:
    """確定区間から決まる出力 mp4 のパスを返す（FR-25 と同じ命名）."""
    clip_id = make_clip_id(segments_to_pairs(segments))
    return (
        settings.data_dir / video_id / "shorts" / "output" / f"short_{clip_id}.mp4"
    )


def build_disabled_message(
    validation: ShortCutValidationResult,
    parse_errors: Sequence[str],
) -> str | None:
    """作成ボタンを無効にする理由（日本語）を返す."""
    if parse_errors:
        return "、".join(parse_errors)
    if not validation.ok:
        return "、".join(validation.errors)
    return None


def suggest_short_cut_job_target(
    *,
    report,
    settings: Settings,
    video_id: str,
    parent_dict: dict,
    parent_is_clip: bool,
    job_id: str | None = None,
) -> None:
    """start_job 用: 親候補のサブ区間を提案して保存する."""

    def on_progress(stage: str, message: str) -> None:
        report(stage=stage, message=message)

    parent: ParentCandidate = (
        ClipCandidate.model_validate(parent_dict)
        if parent_is_clip
        else HighlightSegment.model_validate(parent_dict)
    )
    suggest_short_cuts(
        video_id,
        parent,
        settings,
        on_progress=on_progress,
    )


def refine_short_cut_job_target(
    *,
    report,
    settings: Settings,
    video_id: str,
    parent_dict: dict,
    parent_is_clip: bool,
    segment_dicts: list[dict],
    job_id: str | None = None,
) -> None:
    """start_job 用: 人が選択した区間だけを高精度化する."""
    parent: ParentCandidate = (
        ClipCandidate.model_validate(parent_dict)
        if parent_is_clip
        else HighlightSegment.model_validate(parent_dict)
    )
    try:
        segments = tuple(HighlightSegment.model_validate(item) for item in segment_dicts)
    except Exception as exc:
        raise ValueError("高精度化する区間の形式が正しくありません。") from exc

    report(
        stage="capability",
        message=(
            f"{S9_WHISPER_PROGRESS_PREFIX}"
            + json.dumps(
                {
                    "schema": "s9-whisper-progress-v1",
                    "job_id": job_id or "未確定",
                    "stage": "capability",
                    "status": "checking",
                    "range_index": 0,
                    "range_total": len(segments),
                    "current_range": None,
                    "cache_hit": None,
                    "retryable": None,
                    "diagnostic": "固定 runtime capability を確認しています。",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        current=0,
        total=len(segments),
    )
    report(
        stage="audio",
        message="選択区間の音声のみを準備します。既存成果物は維持します。",
        current=0,
        total=len(segments),
    )

    progress_events: list[object] = []

    def on_progress(progress: object) -> None:
        progress_events.append(progress)
        _report_whisper_progress(
            report,
            progress,
            segments,
            stage=_whisper_stage_for_status(str(getattr(progress, "status", "unknown"))),
        )

    try:
        refine_selected_short_cut(
            video_id,
            parent,
            segments,
            settings,
            job_id=job_id,
            on_progress=on_progress,
        )
    except Exception as exc:
        payload = _whisper_error_payload(
            job_id or "未確定",
            segments,
            progress_events,
            exc,
        )
        report(
            stage="resolver",
            message=S9_WHISPER_ERROR_PREFIX
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            current=len(segments),
            total=len(segments),
        )
        raise ShortCutError(_whisper_error_message(payload)) from exc
    report(
        stage="artifact",
        message="高精度 artifact を保存しました。",
        current=len(segments),
        total=len(segments),
    )
    report(
        stage="resolver",
        message="resolver が同じ artifact lineage の cutplan を保存しました。",
        current=len(segments),
        total=len(segments),
    )


def build_short_cut_job_target(
    *,
    report,
    settings: Settings,
    video_id: str,
    segment_pairs: list[list[float]],
    layout: str,
    job_id: str | None = None,
) -> None:
    """start_job 用: 確定区間を連結してショートを生成する."""

    def on_progress(current: int, total: int, message: str) -> None:
        report(current=current, total=total, message=message)

    result = build_short_from_segments(
        video_id,
        [(float(start), float(end)) for start, end in segment_pairs],
        settings,
        layout=layout,
        on_progress=on_progress,
        ffmpeg_path=settings.ffmpeg_path,
    )
    meta_path = result.output_path.with_name(f"{result.output_path.stem}.meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "video_id": result.video_id,
                "output_path": str(result.output_path),
                "command_log_path": str(result.command_log_path),
                "layout": result.layout,
                "burned_subtitles": result.burned_subtitles,
                "duration_sec": result.duration_sec,
                "font_warning": result.font_warning,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _start_suggest(
    *,
    video_id: str,
    title: str,
    option: ParentOption,
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "short_cut",
            suggest_short_cut_job_target,
            video_id=video_id,
            title=title,
            settings=settings,
            parent_dict=option.candidate.model_dump(mode="json"),
            parent_is_clip=isinstance(option.candidate, ClipCandidate),
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


def _start_refine(
    *,
    video_id: str,
    title: str,
    option: ParentOption,
    segments: Sequence[HighlightSegment],
    settings: Settings,
) -> None:
    try:
        job_id = start_job(
            "short_cut",
            refine_short_cut_job_target,
            video_id=video_id,
            title=title,
            total=len(segments),
            settings=settings,
            parent_dict=option.candidate.model_dump(mode="json"),
            parent_is_clip=isinstance(option.candidate, ClipCandidate),
            segment_dicts=[segment.model_dump(mode="json") for segment in segments],
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


def _start_build(
    *,
    video_id: str,
    title: str,
    segments: Sequence[HighlightSegment],
    layout: str,
    settings: Settings,
) -> None:
    try:
        pairs = segments_to_pairs(segments)
    except ValueError as exc:
        st.error(str(exc))
        return
    try:
        job_id = start_job(
            "shorts",
            build_short_cut_job_target,
            video_id=video_id,
            title=title,
            total=len(pairs) + 3,
            settings=settings,
            segment_pairs=[[start, end] for start, end in pairs],
            layout=layout,
        )
    except JobBusyError:
        st.error(_BUSY_MESSAGE)
        return
    set_active_job_id(job_id)
    st.rerun()


@st.dialog("ショート動画の再作成を確認")
def _confirm_build_dialog(
    *,
    video_id: str,
    title: str,
    segments: Sequence[HighlightSegment],
    layout: str,
    settings: Settings,
) -> None:
    st.warning("同じ区間構成のショート動画を上書きして再作成します。")
    busy = is_busy(settings)
    if busy:
        st.info(_BUSY_MESSAGE)
    if st.button(
        "再作成を実行",
        key=f"short_cut_confirm_build_{video_id}",
        type="primary",
        disabled=busy,
    ):
        _start_build(
            video_id=video_id,
            title=title,
            segments=segments,
            layout=layout,
            settings=settings,
        )


def _render_refine_preview(
    *,
    video_id: str,
    title: str,
    option: ParentOption,
    segments: Sequence[HighlightSegment],
    settings: Settings,
    busy: bool,
    disabled_message: str | None,
) -> None:
    """高精度化の対象と非破壊条件を確認してから明示 submit を受け付ける."""
    contract = WhisperSettingsContract.from_settings(settings)
    candidate = option.candidate

    st.markdown("**高精度字幕の準備プレビュー**")
    st.caption(f"親候補範囲: {candidate.start} → {candidate.end}")
    st.caption(f"対象区間: {len(segments)} 件")
    for segment in segments:
        st.caption(f"{segment.id}: {segment.start} → {segment.end}")
    st.caption(f"padding: {contract.padding_ms} ms（固定）")
    st.caption(
        "予想処理: 選択区間の音声のみ準備 → span → Whisper → "
        "artifact 保存 → resolver"
    )
    st.info(
        "既存の ja.vtt、親候補、cutplan、テロップ、mp4 は上書き・削除しません。"
    )

    with st.form(key=f"short_cut_refine_form_{video_id}_{option.id}"):
        submitted = st.form_submit_button(
            "高精度字幕を準備",
            type="primary",
            disabled=busy or disabled_message is not None,
        )
    if submitted and not busy and disabled_message is None:
        _start_refine(
            video_id=video_id,
            title=title,
            option=option,
            segments=segments,
            settings=settings,
        )


def artifact_provenance_payload(document: object) -> dict[str, object] | None:
    """既存 document の lineage を表示用 payload にする。fingerprint は再計算しない."""
    artifact_ref = getattr(document, "artifact_ref", None)
    artifact_fingerprint = getattr(document, "artifact_fingerprint", None)
    digests = tuple(getattr(document, "used_range_cue_digests", ()) or ())
    if artifact_ref is None:
        return None
    ref_payload = (
        artifact_ref.model_dump(mode="json")
        if hasattr(artifact_ref, "model_dump")
        else str(artifact_ref)
    )
    return {
        "artifact_ref": ref_payload,
        "artifact_fingerprint": artifact_fingerprint,
        "used_range_cue_digests": list(digests),
    }


def render_cutplan_provenance(document: ShortCutDocument) -> None:
    """工程 2 panel 固定位置の字幕照合データを折り畳みへ表示する."""
    ranges = "、".join(
        f"{candidate.id} {candidate.start} → {candidate.end}"
        for candidate in document.candidates
    ) or "なし"
    payload = artifact_provenance_payload(document)
    with st.expander("字幕の照合データ", expanded=False):
        if payload is None:
            st.caption("この区間は自動字幕（通常精度）のまま扱っています。")
            st.caption("対象区間: " + ranges)
            return
        st.caption("この区間は高精度字幕に対応づけて固定しています。")
        st.caption("対象区間: " + ranges)
        st.code(json.dumps(payload, ensure_ascii=False, indent=2))


def _render_plan(
    *,
    video_id: str,
    title: str,
    option: ParentOption,
    document: ShortCutDocument,
    settings: Settings,
    on_segments_confirmed: (
        Callable[[Sequence[HighlightSegment], ParentOption], None] | None
    ) = None,
) -> None:
    st.markdown("**提案された区間**（採用するものにチェックし、必要なら時刻を調整）")
    parent_identity = option.identity
    sync_short_cut_editor_state(
        document,
        video_id,
        parent_identity,
        st.session_state,
    )
    cues, transcript_notice = load_transcript_cues_for_document(
        video_id,
        document,
        settings,
    )
    if transcript_notice:
        if document.artifact_ref is not None:
            st.error(transcript_notice)
        else:
            st.info(transcript_notice)

    for candidate in document.candidates:
        candidate_checkbox_key = checkbox_key(
            video_id,
            candidate.id,
            parent_identity=parent_identity,
        )
        candidate_start_key = start_key(
            video_id,
            candidate.id,
            parent_identity=parent_identity,
        )
        candidate_end_key = end_key(
            video_id,
            candidate.id,
            parent_identity=parent_identity,
        )
        st.checkbox(
            f"{candidate.id}: {candidate.title}（{candidate.duration_sec} 秒）",
            key=candidate_checkbox_key,
            persist_state="session",
        )
        columns = st.columns(2)
        with columns[0]:
            _render_time_assist(candidate_start_key, "開始を調整")
            st.text_input(
                "開始",
                key=candidate_start_key,
                persist_state="session",
            )
        with columns[1]:
            _render_time_assist(candidate_end_key, "終了を調整")
            st.text_input(
                "終了",
                key=candidate_end_key,
                persist_state="session",
            )
        st.caption(candidate.reason)

        current_start = str(
            st.session_state.get(candidate_start_key, candidate.start)
        )
        current_end = str(
            st.session_state.get(candidate_end_key, candidate.end)
        )
        transcript_start, transcript_end, used_fallback = resolve_transcript_bounds(
            current_start,
            current_end,
            candidate.start,
            candidate.end,
        )
        if used_fallback:
            st.caption(_INVALID_TRANSCRIPT_BOUNDS_MESSAGE)
        transcript = ""
        if transcript_start is not None and transcript_end is not None:
            transcript = extract_segment_text(cues, transcript_start, transcript_end)
        st.markdown("**この区間の文字起こし**")
        with st.container(height=160, border=True):
            st.write(transcript or _NO_TRANSCRIPT_MESSAGE)

    segments, parse_errors = collect_edited_segments(
        document,
        video_id,
        st.session_state,
        parent_identity=parent_identity,
    )
    validation = validate_short_cut_selection(segments, parent=option.candidate)
    total_ms = validation.total_ms

    st.markdown(
        f"**合計: {format_total_ms(total_ms)}** "
        f"（{int(MIN_TOTAL_MS / 1000)}〜{int(MAX_TOTAL_MS / 1000)} 秒に収める必要があります）"
    )

    # 区間そのものが不正なら高精度化も先へ進むのも止める。artifact lineage の
    # 不一致は「先へ進む」だけを止める。高精度字幕の準備はその失効を解消する
    # 復旧操作であり、これを無効化すると失効した cutplan から抜け出せない。
    selection_disabled_message = build_disabled_message(validation, parse_errors)
    disabled_message = selection_disabled_message
    if document.artifact_ref is not None and transcript_notice:
        disabled_message = transcript_notice
    if disabled_message:
        st.warning(disabled_message)

    render_cutplan_provenance(document)

    busy = is_busy(settings)
    is_high_precision = document.artifact_ref is not None and transcript_notice is None
    if is_high_precision:
        st.success("選択区間の高精度字幕を固定済みです。")
    else:
        st.caption(
            "親候補の探索・通常 rerun は既存 VTT のままです。"
            "人が確認した選択区間だけを高精度化できます。"
        )
        _render_refine_preview(
            video_id=video_id,
            title=title,
            option=option,
            segments=segments,
            settings=settings,
            busy=busy,
            disabled_message=selection_disabled_message,
        )
    if on_segments_confirmed is not None:
        if st.button(
            "区間列を確定してテロップ確認へ",
            type="primary",
            key=f"short_cut_confirm_segments_{video_id}",
            disabled=busy or disabled_message is not None,
        ):
            on_segments_confirmed(tuple(segments), option)
        return

    layout_label = st.radio(
        "レイアウト",
        LAYOUT_LABELS,
        index=0,
        key=f"short_cut_layout_{video_id}",
        persist_state="session",
    )
    layout = layout_from_label(layout_label)

    output_path: Path | None = None
    if disabled_message is None:
        output_path = short_cut_output_path(video_id, segments, settings)

    if st.button(
        "刻んでショートを作成",
        type="primary",
        key=f"short_cut_build_{video_id}",
        disabled=busy or disabled_message is not None,
    ):
        action = (
            _confirm_build_dialog
            if output_path is not None and output_path.is_file()
            else _start_build
        )
        action(
            video_id=video_id,
            title=title,
            segments=segments,
            layout=layout,
            settings=settings,
        )

    if output_path is not None and output_path.is_file():
        st.success("ショート動画が生成されています。")
        with st.container(width=360):
            st.video(str(output_path))
        st.markdown(f"**保存先:** `{output_path}`")


def render_short_cut_section(
    *,
    video_id: str,
    title: str,
    clip_candidates: Sequence[ClipCandidate],
    highlight_candidates: Sequence[HighlightSegment],
    settings: Settings,
    embedded: bool = False,
    preferred_candidate_ids: Sequence[str] = (),
    on_segments_confirmed: (
        Callable[[Sequence[HighlightSegment], ParentOption], None] | None
    ) = None,
) -> None:
    """長い候補を刻んでショートにするセクションを描画する."""

    def render_contents() -> None:
        st.caption(_SECTION_NOTE)

        options = collect_parent_options(clip_candidates, highlight_candidates)
        if not options:
            st.info(_NO_LONG_CANDIDATE_MESSAGE)
            return

        options_by_identity = {option.identity: option for option in options}

        if embedded:
            # embedded 経路では呼び出し元（shorts_line.py）が候補を
            # 「今回作る候補」として既に確定表示している。ここで親候補の
            # 再選択 widget を描かず、standalone 経路の session_state key
            # （short_cut_parent_{video_id}）も汚さない。
            selected_identity = resolve_parent_option_identity(
                options,
                None,
                preferred_candidate_ids=preferred_candidate_ids,
            )
            option = options_by_identity[selected_identity]
        else:
            selected_key = f"short_cut_parent_{video_id}"
            selected_identity = resolve_parent_option_identity(
                options,
                st.session_state.get(selected_key),
                preferred_candidate_ids=preferred_candidate_ids,
            )
            if st.session_state.get(selected_key) != selected_identity:
                st.session_state[selected_key] = selected_identity

            selected_value = st.radio(
                "刻む候補",
                tuple(options_by_identity),
                format_func=lambda identity: options_by_identity[identity].label,
                key=selected_key,
                persist_state="session",
            )
            selected_identity = resolve_parent_option_identity(
                options,
                selected_value,
                preferred_candidate_ids=preferred_candidate_ids,
            )
            option = options_by_identity[selected_identity]

        document = load_cut_plan(video_id, option.id, settings)
        busy = is_busy(settings)

        if st.button(
            "区間を提案し直す" if document is not None else "ショート用の区間を提案",
            key=f"short_cut_suggest_{video_id}",
            disabled=busy,
        ):
            _start_suggest(
                video_id=video_id,
                title=title,
                option=option,
                settings=settings,
            )

        if document is None:
            st.info(
                "まだ提案がありません。「ショート用の区間を提案」を押してください。"
            )
            return

        _render_plan(
            video_id=video_id,
            title=title,
            option=option,
            document=document,
            settings=settings,
            on_segments_confirmed=on_segments_confirmed,
        )

    if embedded:
        render_contents()
        return

    expander = st.expander(
        "長い候補を刻んでショートにする",
        expanded=False,
        key=f"short_cut_expander_{video_id}",
        on_change="rerun",
    )
    if expander.open:
        with expander:
            render_contents()
