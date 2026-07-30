"""パイプライン進捗表示の純粋関数（テスト用に旧ロジックを保持）."""

from __future__ import annotations

from yt_live_kit.services.pipeline import (
    STAGE_CHAPTERS,
    STAGE_CLIPS_SUGGEST,
    STAGE_FETCH,
    STAGE_LABELS,
    STAGE_TRANSCRIPT,
)

_STAGE_ORDER = [STAGE_FETCH, STAGE_TRANSCRIPT, STAGE_CHAPTERS, STAGE_CLIPS_SUGGEST]


def render_progress(
    progress_state: dict[str, str],
    progress_ctx: dict[str, str],
) -> str:
    lines: list[str] = []
    for stage_key in _STAGE_ORDER:
        label = STAGE_LABELS[stage_key]
        state = progress_state[stage_key]
        if state == "complete":
            lines.append(f"✅ **{label}** — 完了")
        elif state == "warning":
            lines.append(f"⚠️ **{label}** — 警告（他の結果は利用できます）")
        elif state == "running":
            lines.append(f"🔄 **{label}** — {progress_ctx['message']}")
        elif state == "error":
            lines.append(f"❌ **{label}** — エラー")
        else:
            lines.append(f"⏳ {label} — 待機中")
    return "\n\n".join(lines)


def mark_failed_stage(progress_state: dict[str, str]) -> None:
    for stage_key in _STAGE_ORDER:
        if progress_state[stage_key] == "running":
            progress_state[stage_key] = "error"
            return
