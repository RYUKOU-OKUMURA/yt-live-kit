"""トランスクリプト生成 — VTT から full / compressed を保存."""

from __future__ import annotations

from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.compressor import compress_lines
from yt_live_kit.services.vtt_parser import EmptyVttError, parse_vtt_file


class TranscriptError(Exception):
    """トランスクリプト生成エラー."""


def _find_vtt_path(video_dir: Path) -> Path:
    subtitles_dir = video_dir / "subtitles"
    candidates = [
        subtitles_dir / "ja.vtt",
        subtitles_dir / "ja-orig.vtt",
    ]
    for path in candidates:
        if path.is_file():
            return path

    vtt_files = sorted(subtitles_dir.glob("*.vtt"))
    if vtt_files:
        return vtt_files[0]

    raise TranscriptError(
        f"字幕ファイルが見つかりません: {subtitles_dir}。"
        "先に fetch を実行してください。"
    )


def build_transcripts(video_id: str, settings: Settings | None = None) -> tuple[Path, Path]:
    """VTT から full.txt / compressed.txt を生成して保存する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id

    if not video_dir.is_dir():
        raise TranscriptError(f"動画ディレクトリが見つかりません: {video_dir}")

    vtt_path = _find_vtt_path(video_dir)
    try:
        full_lines = parse_vtt_file(vtt_path)
    except EmptyVttError as exc:
        raise TranscriptError(str(exc)) from exc
    compressed_lines = compress_lines(full_lines)

    transcript_dir = video_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    full_path = transcript_dir / "full.txt"
    compressed_path = transcript_dir / "compressed.txt"

    full_path.write_text("\n".join(full_lines) + ("\n" if full_lines else ""), encoding="utf-8")
    compressed_path.write_text(
        "\n".join(compressed_lines) + ("\n" if compressed_lines else ""),
        encoding="utf-8",
    )

    return full_path, compressed_path
