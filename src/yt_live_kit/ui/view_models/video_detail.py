"""Pure state projections for the video detail page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.clips import make_candidate_fingerprint as _make_fingerprint
from yt_live_kit.services.jobs import JobState

Workspace = Literal["materials", "shorts", "publish"]
CandidateSource = Literal["clips", "highlights"]
CandidateKey = tuple[CandidateSource, str]
Candidate = ClipCandidate | HighlightSegment


@dataclass(frozen=True)
class DetailSummary:
    """Read-only values shown in the video detail summary cards."""

    candidate_count: int
    generated_short_count: int
    reservable_short_count: int
    description_applied: bool


@dataclass(frozen=True)
class CandidateTransfer:
    """Pre-line candidate selection retained only in session state."""

    source: CandidateSource
    selected_ids: tuple[str, ...]
    fingerprint: str


def calculate_detail_summary(
    *,
    clip_count: int,
    highlight_count: int,
    generated_short_count: int,
    reservable_short_count: int,
    description_applied: bool,
) -> DetailSummary:
    return DetailSummary(
        candidate_count=max(0, clip_count) + max(0, highlight_count),
        generated_short_count=max(0, generated_short_count),
        reservable_short_count=max(0, reservable_short_count),
        description_applied=description_applied,
    )


def choose_initial_workspace(
    *,
    video_id: str,
    candidate_count: int,
    reservable_short_count: int,
    active_job: JobState | None = None,
) -> Workspace:
    if (
        active_job is not None
        and active_job.status == "running"
        and active_job.video_id == video_id
    ):
        if active_job.kind == "upload":
            return "publish"
        if active_job.kind in {"shorts", "shorts_queue", "short_cut"}:
            return "shorts"
        if active_job.kind in {"highlights", "regenerate", "cut_clip"}:
            return "materials"
    if candidate_count <= 0:
        return "materials"
    if reservable_short_count <= 0:
        return "shorts"
    return "publish"


def make_candidate_fingerprint(
    source: CandidateSource,
    candidates: Sequence[Candidate],
) -> str:
    """Legacy/content fingerprint kept for highlights and old clip documents."""
    # The service helper owns the canonical payload.  HighlightSegment has the
    # same candidate schema and is deliberately accepted by this UI projection.
    return _make_fingerprint(source, list(candidates))  # type: ignore[arg-type]


def current_candidate_fingerprint(
    source: CandidateSource,
    candidates: Sequence[Candidate],
    *,
    persisted_candidate_fingerprint: str | None = None,
) -> str:
    """Prefer the saved coarse-lineage fingerprint for clip documents."""
    if source == "clips" and persisted_candidate_fingerprint is not None:
        return persisted_candidate_fingerprint
    return make_candidate_fingerprint(source, candidates)


def validate_candidate_transfer(
    transfer: CandidateTransfer | None,
    *,
    current_fingerprint: str,
    candidate_ids: set[str],
) -> CandidateTransfer | None:
    if transfer is None:
        return None
    if transfer.fingerprint != current_fingerprint:
        return None
    if not transfer.selected_ids or not set(transfer.selected_ids) <= candidate_ids:
        return None
    return transfer
