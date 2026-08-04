"""Pure upload-operation projections used independently of queue manifests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from yt_live_kit.models.upload import UploadOperation

_PUBLICATION_POLL_TERMINAL = frozenset(
    {
        "published",
        "suspected_private_lock",
        "processing_failed",
        "publication_timeout",
    }
)


def _is_actionable(operation: UploadOperation) -> bool:
    """Keep durable work that still needs a future poll or human action."""
    if operation.state in {"reserved", "uploading"}:
        return True
    if operation.state == "needs_reconciliation":
        return True
    if operation.related_video_status == "pending":
        return True
    if operation.state != "uploaded":
        return False
    publication_observations = tuple(
        observation
        for observation in operation.poll_history
        if observation.phase == "publication"
    )
    return not publication_observations or (
        publication_observations[-1].classification
        not in _PUBLICATION_POLL_TERMINAL
    )


@dataclass(frozen=True)
class SourceOperationProjection:
    """One queue read projected into tracking rows and new-reservation gates."""

    operations: tuple[UploadOperation, ...]
    operation_ids: frozenset[str]
    blocking_clip_ids: frozenset[str]


def project_source_operations(
    operations: Sequence[UploadOperation],
    source_video_id: str,
) -> SourceOperationProjection:
    """Project all source operations without losing blockers behind newer failures."""
    latest: dict[str, UploadOperation] = {}
    matching: list[UploadOperation] = []
    for operation in operations:
        if operation.source_video_id != source_video_id:
            continue
        matching.append(operation)
        previous = latest.get(operation.clip_id)
        if previous is None or (
            operation.created_at,
            operation.operation_id,
        ) > (
            previous.created_at,
            previous.operation_id,
        ):
            latest[operation.clip_id] = operation
    selected = {
        operation.operation_id: operation
        for operation in matching
        if _is_actionable(operation)
    }
    selected.update(
        (operation.operation_id, operation)
        for operation in latest.values()
    )
    tracked = tuple(
        sorted(
            selected.values(),
            key=lambda operation: (
                operation.created_at,
                operation.operation_id,
            ),
            reverse=True,
        )
    )
    return SourceOperationProjection(
        operations=tracked,
        operation_ids=frozenset(operation.operation_id for operation in tracked),
        blocking_clip_ids=frozenset(
            operation.clip_id
            for operation in matching
            if operation.state != "failed"
        ),
    )


def latest_operations_for_source(
    operations: Sequence[UploadOperation],
    source_video_id: str,
) -> tuple[UploadOperation, ...]:
    """Compatibility projection of tracking rows only."""
    return project_source_operations(operations, source_video_id).operations
