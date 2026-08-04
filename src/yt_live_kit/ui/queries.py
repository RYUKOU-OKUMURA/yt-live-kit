"""Read-only filesystem queries used by multiple Streamlit views."""

from __future__ import annotations

from yt_live_kit.config import Settings


def count_generated_shorts(video_id: str, settings: Settings) -> int:
    """Return the number of generated MP4 files for one source video."""
    output_dir = settings.data_dir / video_id / "shorts" / "output"
    if not output_dir.is_dir():
        return 0
    return sum(1 for path in output_dir.glob("*.mp4") if path.is_file())
