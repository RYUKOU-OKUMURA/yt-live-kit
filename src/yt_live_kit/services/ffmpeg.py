"""ffmpeg による動画区間切り出し."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.ytdlp import YtdlpError, download_video

FFMPEG_DEFAULT = "ffmpeg"


class FfmpegError(Exception):
    """ffmpeg 実行エラー."""


@dataclass(frozen=True)
class CutResult:
    """切り出し結果."""

    video_id: str
    output_path: Path
    command_log_path: Path
    start: str
    end: str
    duration_sec: int


def find_ffmpeg(ffmpeg_path: str = FFMPEG_DEFAULT) -> str:
    resolved = shutil.which(ffmpeg_path)
    if resolved is None:
        raise FfmpegError(
            f"ffmpeg が見つかりません（パス: {ffmpeg_path}）。"
            "インストール後 PATH に通すか、YTLK_FFMPEG_PATH を設定してください。"
        )
    return resolved


_find_ffmpeg = find_ffmpeg


def load_meta(video_dir: Path) -> VideoMeta:
    meta_path = video_dir / "meta.json"
    if not meta_path.is_file():
        raise FfmpegError(f"メタデータが見つかりません: {meta_path}")
    return VideoMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))


_load_meta = load_meta


def ensure_source_video(video_id: str, settings: Settings) -> Path:
    """切り出し用の元動画を取得する（未 DL なら yt-dlp でダウンロード）."""
    video_dir = settings.data_dir / video_id
    source_dir = video_dir / "clips" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(source_dir.glob("*.mp4")) + sorted(source_dir.glob("*.mkv"))
    if existing:
        return existing[0]

    meta = load_meta(video_dir)
    return download_video(meta.url, source_dir, settings)


_ensure_source_video = ensure_source_video


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    start_sec: int,
    end_sec: int,
    *,
    ffmpeg_path: str = FFMPEG_DEFAULT,
    reencode: bool = False,
) -> list[str]:
    """ffmpeg 切り出しコマンドを組み立てる."""
    ffmpeg = find_ffmpeg(ffmpeg_path)
    duration = end_sec - start_sec
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(start_sec),
        "-i",
        str(input_path),
        "-t",
        str(duration),
    ]
    if reencode:
        cmd.extend(["-c:v", "libx264", "-c:a", "aac"])
    else:
        cmd.extend(["-c", "copy"])
    cmd.append(str(output_path))
    return cmd


def save_command_log(
    output_dir: Path,
    cmd: list[str],
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = output_dir / f"ffmpeg_{timestamp}.log"
    lines = [
        " ".join(cmd),
        "",
        f"returncode: {returncode}",
        "",
        "--- stdout ---",
        stdout or "(empty)",
        "",
        "--- stderr ---",
        stderr or "(empty)",
    ]
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


_save_command_log = save_command_log


def encode_segment(
    source: Path,
    output: Path,
    start_sec: float,
    end_sec: float,
    *,
    ffmpeg_path: str = FFMPEG_DEFAULT,
    scale: str | None = None,
    extra_filters: str | None = None,
    preset: str = "medium",
    crf: int = 20,
) -> Path:
    """指定区間を精密シークで再エンコードする."""
    if end_sec <= start_sec:
        raise FfmpegError("終了時刻は開始時刻より後である必要があります。")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    duration = end_sec - start_sec
    cmd: list[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-ss",
        str(start_sec),
        "-t",
        str(duration),
    ]

    filters: list[str] = []
    if scale:
        filters.append(scale)
    if extra_filters:
        filters.append(extra_filters)
    if filters:
        cmd.extend(["-vf", ",".join(filters)])

    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            str(output),
        ]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log_path = save_command_log(
        output.parent,
        cmd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise FfmpegError(
            f"ffmpeg の区間エンコードに失敗しました。ログ: {log_path}"
            + (f"\n（詳細: {stderr}）" if stderr else "")
        )

    if not output.is_file() or output.stat().st_size == 0:
        raise FfmpegError(
            f"エンコードファイルが生成されませんでした。ログ: {log_path}"
        )

    return output


def build_concat_list(segment_paths: list[Path], list_path: Path) -> Path:
    """concat demuxer 用のリストファイルを生成する."""
    if not segment_paths:
        raise FfmpegError("連結する区間ファイルが指定されていません。")

    lines: list[str] = []
    for segment in segment_paths:
        absolute = segment.resolve()
        escaped = str(absolute).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")

    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def concat_segments(
    segment_paths: list[Path],
    output_path: Path,
    *,
    ffmpeg_path: str = FFMPEG_DEFAULT,
    log_dir: Path | None = None,
) -> Path:
    """エンコード済み区間を concat demuxer で連結する."""
    if not segment_paths:
        raise FfmpegError("連結する区間ファイルが指定されていません。")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    list_path = output_path.parent / "concat.txt"
    build_concat_list(segment_paths, list_path)

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output_path),
    ]

    effective_log_dir = log_dir if log_dir is not None else output_path.parent
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log_path = save_command_log(
        effective_log_dir,
        cmd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise FfmpegError(
            f"ffmpeg の連結に失敗しました。ログ: {log_path}"
            + (f"\n（詳細: {stderr}）" if stderr else "")
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FfmpegError(
            f"連結ファイルが生成されませんでした。ログ: {log_path}"
        )

    list_path.unlink(missing_ok=True)
    return output_path


def cut_clip(
    video_id: str,
    start: str,
    end: str,
    settings: Settings | None = None,
    *,
    output_name: str | None = None,
    ffmpeg_path: str = FFMPEG_DEFAULT,
    reencode: bool = False,
) -> CutResult:
    """指定区間を ffmpeg で切り出す."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise FfmpegError(f"動画ディレクトリが見つかりません: {video_dir}")

    try:
        start_sec = parse_timestamp_to_seconds(start)
        end_sec = parse_timestamp_to_seconds(end)
    except ValueError as exc:
        raise FfmpegError(str(exc)) from exc

    if end_sec <= start_sec:
        raise FfmpegError("終了時刻は開始時刻より後である必要があります。")

    duration_sec = end_sec - start_sec
    source_path = ensure_source_video(video_id, settings)

    output_dir = video_dir / "clips" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    if output_name is None:
        safe_start = start.replace(":", "")
        safe_end = end.replace(":", "")
        output_name = f"clip_{safe_start}_{safe_end}.mp4"
    output_path = output_dir / output_name

    cmd = build_ffmpeg_command(
        source_path,
        output_path,
        start_sec,
        end_sec,
        ffmpeg_path=ffmpeg_path,
        reencode=reencode,
    )

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log_path = save_command_log(
        output_dir,
        cmd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    if result.returncode != 0:
        if not reencode:
            return cut_clip(
                video_id,
                start,
                end,
                settings,
                output_name=output_name,
                ffmpeg_path=ffmpeg_path,
                reencode=True,
            )
        stderr = (result.stderr or "").strip()
        raise FfmpegError(
            f"ffmpeg の切り出しに失敗しました。"
            f"ログ: {log_path}"
            + (f"\n（詳細: {stderr}）" if stderr else "")
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FfmpegError(
            f"切り出しファイルが生成されませんでした。ログ: {log_path}"
        )

    return CutResult(
        video_id=video_id,
        output_path=output_path,
        command_log_path=log_path,
        start=start,
        end=end,
        duration_sec=duration_sec,
    )
