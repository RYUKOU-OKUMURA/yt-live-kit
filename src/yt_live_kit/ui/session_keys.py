"""Streamlit session state key builders shared across UI modules.

The functions in this module are intentionally pure.  Keeping widget and
projection keys here makes presentation refactors less likely to silently
change the identity of persisted session values.
"""

from __future__ import annotations


def detail_workspace_key(video_id: str) -> str:
    return f"detail_workspace_{video_id}"


def candidate_transfer_key(video_id: str, source: str) -> str:
    return f"shorts_line_transfer_{video_id}_{source}"


def candidate_transfer_order_key(video_id: str) -> str:
    return f"shorts_line_transfer_order_{video_id}"


def shorts_line_context_key(video_id: str) -> str:
    return f"shorts_line_context_{video_id}"


def telop_editor_prefix(video_id: str, clip_id: str) -> str:
    return f"line_editor_{video_id}_{clip_id}"


def line_material_selection_key(video_id: str) -> str:
    return f"line_material_selection_{video_id}"


def line_material_transfer_marker_key(video_id: str) -> str:
    return f"line_material_transfer_marker_{video_id}"


def line_material_target_key(video_id: str) -> str:
    return f"line_material_target_{video_id}"


def line_review_seen_key(video_id: str, clip_id: str) -> str:
    return f"line_review_seen_{video_id}_{clip_id}"


def line_review_invalidated_key(video_id: str, clip_id: str) -> str:
    return f"line_review_invalidated_{video_id}_{clip_id}"
