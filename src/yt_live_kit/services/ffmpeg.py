"""ffmpeg による動画区間切り出し."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.ytdlp import YtdlpError, download_video

FFMPEG_DEFAULT = "ffmpeg"
FFPROBE_DEFAULT = "ffprobe"
FFMPEG_TIMEOUT_DEFAULT = 3600
FFMPEG_CAPABILITY_TIMEOUT_DEFAULT = 10

ConcatProgressCallback = Callable[[int, int, str], None] | None


class FfmpegError(Exception):
    """ffmpeg 実行エラー."""


@dataclass(frozen=True)
class FfmpegCapabilities:
    """FFmpeg バイナリの解決結果と利用可能なフィルタ."""

    configured_path: str
    resolved_path: str
    filter_names: frozenset[str]

    @property
    def subtitles_available(self) -> bool:
        """subtitles フィルタを利用できるか返す."""
        return "subtitles" in self.filter_names


@dataclass(frozen=True)
class FfmpegDiagnostics:
    """設定画面に表示する FFmpeg 診断結果."""

    configured_path: str
    resolved_path: str
    version: str
    subtitles_available: bool


def _run_ffmpeg_command(
    cmd: list[str],
    *,
    ffmpeg_timeout: int = FFMPEG_TIMEOUT_DEFAULT,
) -> subprocess.CompletedProcess[str]:
    """ffmpeg / ffprobe コマンドをタイムアウト付きで実行する."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=ffmpeg_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(
            f"ffmpeg の実行が {ffmpeg_timeout} 秒でタイムアウトしました。"
            "処理が長時間かかっている可能性があります。"
        ) from exc


def ffprobe_path_for(ffmpeg_path: str) -> str:
    """ffmpeg コマンドと同じディレクトリの ffprobe パスを返す."""
    path = Path(ffmpeg_path)
    return str(path.with_name("ffprobe"))


def probe_duration(
    source: Path,
    *,
    ffprobe_path: str = FFPROBE_DEFAULT,
    ffmpeg_timeout: int = FFMPEG_TIMEOUT_DEFAULT,
) -> float:
    """ffprobe で動画の尺を秒として取得する."""
    path = source.resolve()
    if not path.is_file():
        raise FfmpegError(f"動画ファイルが見つかりません: {path}")
    ffprobe = shutil.which(ffprobe_path)
    if ffprobe is None:
        raise FfmpegError(
            "ffprobe が見つかりません。ffmpeg をインストールするか設定を確認してください。"
        )
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = _run_ffmpeg_command(command, ffmpeg_timeout=ffmpeg_timeout)
    except OSError as exc:
        raise FfmpegError(f"ffprobe を実行できませんでした: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise FfmpegError(f"動画の尺を取得できませんでした。詳細: {detail or '不明'}")
    try:
        duration = float((result.stdout or "").strip())
    except ValueError as exc:
        raise FfmpegError("ffprobe の尺取得結果が正しくありません。") from exc
    if duration <= 0:
        raise FfmpegError("動画の尺が 0 秒以下です。")
    return duration


@dataclass(frozen=True)
class CutResult:
    """切り出し結果."""

    video_id: str
    output_path: Path
    command_log_path: Path
    start: str
    end: str
    duration_sec: int


@dataclass(frozen=True)
class ConcatResult:
    """ハイライト連結結果."""

    video_id: str
    output_path: Path
    command_log_path: Path
    segment_count: int
    total_duration_sec: float


def find_ffmpeg(ffmpeg_path: str = FFMPEG_DEFAULT) -> str:
    try:
        resolved = shutil.which(ffmpeg_path)
    except OSError as exc:
        raise FfmpegError(
            f"ffmpeg のパスを確認できませんでした（パス: {ffmpeg_path}）。"
            "実行可能な ffmpeg を確認し、YTLK_FFMPEG_PATH に明示してください。"
        ) from exc
    if resolved is None:
        raise FfmpegError(
            f"ffmpeg が見つかりません（パス: {ffmpeg_path}）。"
            "インストール後 PATH に通すか、YTLK_FFMPEG_PATH を設定してください。"
        )
    return resolved


_find_ffmpeg = find_ffmpeg


def parse_ffmpeg_filter_names(stdout: str, stderr: str) -> frozenset[str]:
    """``ffmpeg -filters`` 出力の表データ行からフィルタ名を取得する."""
    names: set[str] = set()
    for line in f"{stdout}\n{stderr}".splitlines():
        columns = line.split()
        if len(columns) < 3:
            continue

        flags, filter_name, signature = columns[:3]
        valid_flags = (
            len(flags) in {2, 3}
            and flags[0] in {"T", "."}
            and flags[1] in {"S", "."}
            and (len(flags) == 2 or flags[2] in {"C", "."})
        )
        input_types, separator, output_types = signature.partition("->")
        valid_signature = (
            separator == "->"
            and bool(input_types)
            and bool(output_types)
            and all(character in "AVN|" for character in input_types)
            and all(character in "AVN|" for character in output_types)
        )
        if valid_flags and valid_signature:
            names.add(filter_name)
    return frozenset(names)


def _run_ffmpeg_inspection(
    cmd: list[str],
    *,
    timeout: int,
    purpose: str,
) -> subprocess.CompletedProcess[str]:
    """FFmpeg 診断用の短時間サブプロセスを実行する."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(
            f"ffmpeg の{purpose}が {timeout} 秒でタイムアウトしました。"
            "実行可能な ffmpeg か確認し、YTLK_FFMPEG_PATH に正しいパスを設定してください。"
        ) from exc
    except OSError as exc:
        raise FfmpegError(
            f"ffmpeg の{purpose}を実行できませんでした。"
            "ファイルの実行権限と YTLK_FFMPEG_PATH の設定を確認してください。"
            f"（原因: {exc}）"
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise FfmpegError(
            f"ffmpeg の{purpose}に失敗しました（終了コード: {result.returncode}）。"
            "YTLK_FFMPEG_PATH に実行可能な FFmpeg のパスを設定してください。"
            + (f"（詳細: {detail}）" if detail else "")
        )
    return result


def probe_ffmpeg_capabilities(
    ffmpeg_path: str = FFMPEG_DEFAULT,
    *,
    timeout: int = FFMPEG_CAPABILITY_TIMEOUT_DEFAULT,
) -> FfmpegCapabilities:
    """実際の FFmpeg バイナリが公開するフィルタを調べる."""
    resolved_path = find_ffmpeg(ffmpeg_path)
    result = _run_ffmpeg_inspection(
        [resolved_path, "-hide_banner", "-filters"],
        timeout=timeout,
        purpose="字幕フィルタ検査",
    )
    return FfmpegCapabilities(
        configured_path=ffmpeg_path,
        resolved_path=resolved_path,
        filter_names=parse_ffmpeg_filter_names(
            result.stdout or "",
            result.stderr or "",
        ),
    )


def get_ffmpeg_version(
    ffmpeg_path: str = FFMPEG_DEFAULT,
    *,
    timeout: int = FFMPEG_CAPABILITY_TIMEOUT_DEFAULT,
) -> str:
    """``ffmpeg -version`` の先頭行を返す."""
    resolved_path = find_ffmpeg(ffmpeg_path)
    return _get_ffmpeg_version_from_resolved(resolved_path, timeout=timeout)


def _get_ffmpeg_version_from_resolved(
    resolved_path: str,
    *,
    timeout: int,
) -> str:
    """解決済みパスに対して ``-version`` を実行する."""
    result = _run_ffmpeg_inspection(
        [resolved_path, "-version"],
        timeout=timeout,
        purpose="バージョン確認",
    )
    lines = [
        line.strip()
        for line in f"{result.stdout or ''}\n{result.stderr or ''}".splitlines()
        if line.strip()
    ]
    if not lines:
        raise FfmpegError(
            "ffmpeg のバージョン情報が空でした。"
            "YTLK_FFMPEG_PATH に正しい FFmpeg のパスを設定してください。"
        )
    return lines[0]


def diagnose_ffmpeg(
    ffmpeg_path: str = FFMPEG_DEFAULT,
    *,
    timeout: int = FFMPEG_CAPABILITY_TIMEOUT_DEFAULT,
) -> FfmpegDiagnostics:
    """パス、バージョン、subtitles 利用可否を診断する."""
    capabilities = probe_ffmpeg_capabilities(ffmpeg_path, timeout=timeout)
    version = _get_ffmpeg_version_from_resolved(
        capabilities.resolved_path,
        timeout=timeout,
    )
    return FfmpegDiagnostics(
        configured_path=capabilities.configured_path,
        resolved_path=capabilities.resolved_path,
        version=version,
        subtitles_available=capabilities.subtitles_available,
    )


def ensure_subtitles_filter(
    ffmpeg_path: str = FFMPEG_DEFAULT,
    *,
    timeout: int = FFMPEG_CAPABILITY_TIMEOUT_DEFAULT,
) -> str:
    """subtitles フィルタが使える解決済み FFmpeg パスを返す."""
    capabilities = probe_ffmpeg_capabilities(ffmpeg_path, timeout=timeout)
    if not capabilities.subtitles_available:
        raise FfmpegError(
            "指定された FFmpeg で subtitles フィルタを利用できません。"
            "macOS では ffmpeg-full を導入し、YTLK_FFMPEG_PATH に"
            "ffmpeg-full の ffmpeg 実体パスを設定してください。"
            f"（検査対象: {capabilities.resolved_path}）"
        )
    return capabilities.resolved_path


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
    filename: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if filename is not None:
        log_path = output_dir / filename
    else:
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
    extra_filters: list[str] | None = None,
    preset: str = "medium",
    crf: int = 20,
    ffmpeg_timeout: int = FFMPEG_TIMEOUT_DEFAULT,
) -> Path:
    """指定区間を入力シークで再エンコードする.

    -ss を -i より前に置く入力シークにより長尺動画の後半切り出しを高速化する。
    libx264 再エンコードではフレーム精度が保たれる。
    """
    if end_sec <= start_sec:
        raise FfmpegError("終了時刻は開始時刻より後である必要があります。")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    duration = end_sec - start_sec
    cmd: list[str] = [
        ffmpeg,
        "-y",
        "-ss",
        str(start_sec),
        "-i",
        str(source),
        "-t",
        str(duration),
    ]

    filters: list[str] = []
    if scale:
        filters.append(scale)
    if extra_filters:
        filters.extend(extra_filters)
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
            "-b:a",
            "192k",
            str(output),
        ]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg_command(cmd, ffmpeg_timeout=ffmpeg_timeout)
    log_path = save_command_log(
        output.parent,
        cmd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        filename=f"{output.stem}.ffmpeg.log",
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
    ffmpeg_timeout: int = FFMPEG_TIMEOUT_DEFAULT,
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
    result = _run_ffmpeg_command(cmd, ffmpeg_timeout=ffmpeg_timeout)
    log_path = save_command_log(
        effective_log_dir,
        cmd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        filename=f"{output_path.stem}.ffmpeg.log",
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


def validate_output_filename(
    output_name: str,
    *,
    required_suffix: str | None = None,
) -> str:
    """出力ファイル名のパストラバーサル等を検証する."""
    if not isinstance(output_name, str):
        raise ValueError("出力ファイル名は文字列で指定してください。")
    if not output_name.strip():
        raise ValueError("出力ファイル名を入力してください。")
    if any(ord(character) < 32 for character in output_name):
        raise ValueError("出力ファイル名に制御文字は使えません。")
    if output_name in {".", ".."}:
        raise ValueError("出力ファイル名にドットだけの名前は使えません。")
    path = Path(output_name)
    if path.is_absolute() or "/" in output_name or "\\" in output_name:
        raise ValueError("出力ファイル名にパスは指定できません。")
    if required_suffix is not None and path.suffix != required_suffix:
        raise ValueError(
            f"出力ファイル名は {required_suffix} で終わる名前にしてください。"
        )
    return output_name


def resolve_output_filename(
    output_name: str | None,
    default_name: str,
    *,
    required_suffix: str | None = None,
) -> str:
    """出力ファイル名を解決し、指定時は検証する."""
    name = default_name if output_name is None else output_name
    return validate_output_filename(name, required_suffix=required_suffix)


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
    else:
        try:
            output_name = validate_output_filename(
                output_name, required_suffix=".mp4"
            )
        except ValueError as exc:
            raise FfmpegError(str(exc)) from exc
    output_path = output_dir / output_name

    cmd = build_ffmpeg_command(
        source_path,
        output_path,
        start_sec,
        end_sec,
        ffmpeg_path=ffmpeg_path,
        reencode=reencode,
    )

    result = _run_ffmpeg_command(cmd, ffmpeg_timeout=settings.ffmpeg_timeout)
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


def _probe_video_scale(
    source: Path,
    *,
    ffprobe_path: str = FFPROBE_DEFAULT,
    ffmpeg_timeout: int = FFMPEG_TIMEOUT_DEFAULT,
) -> str | None:
    """元動画の解像度から scale フィルタ文字列を返す（取得失敗時は None）."""
    ffprobe = shutil.which(ffprobe_path)
    if ffprobe is None:
        return None

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(source),
    ]
    result = _run_ffmpeg_command(cmd, ffmpeg_timeout=ffmpeg_timeout)
    if result.returncode != 0:
        return None

    dimensions = (result.stdout or "").strip()
    if not dimensions or "x" not in dimensions:
        return None

    parts = dimensions.split("x")
    if len(parts) != 2:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None

    return f"scale={width}:{height}"


def cut_and_concat(
    video_id: str,
    segments: list[HighlightSegment],
    settings: Settings | None = None,
    *,
    output_name: str = "highlight.mp4",
    ffmpeg_path: str = FFMPEG_DEFAULT,
    ffprobe_path: str = FFPROBE_DEFAULT,
    keep_segments: bool = False,
    on_progress: ConcatProgressCallback = None,
) -> ConcatResult:
    """複数区間を再エンコードして連結し、ハイライト動画を出力する."""
    if not segments:
        raise FfmpegError("連結するハイライト区間が指定されていません。")

    try:
        output_name = validate_output_filename(output_name, required_suffix=".mp4")
    except ValueError as exc:
        raise FfmpegError(str(exc)) from exc

    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise FfmpegError(f"動画ディレクトリが見つかりません: {video_dir}")

    source_path = ensure_source_video(video_id, settings)
    ffmpeg_timeout = settings.ffmpeg_timeout
    scale = _probe_video_scale(
        source_path,
        ffprobe_path=ffprobe_path,
        ffmpeg_timeout=ffmpeg_timeout,
    )

    segments_dir = video_dir / "highlights" / "segments"
    output_dir = video_dir / "highlights" / "output"
    segments_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name

    total = len(segments)
    encoded_paths: list[Path] = []
    total_duration_sec = 0.0

    for index, segment in enumerate(segments, start=1):
        if on_progress is not None:
            on_progress(index, total, f"区間 {index}/{total} を処理中…")

        try:
            start_sec = float(parse_timestamp_to_seconds(segment.start))
            end_sec = float(parse_timestamp_to_seconds(segment.end))
        except ValueError as exc:
            raise FfmpegError(str(exc)) from exc

        duration = end_sec - start_sec
        total_duration_sec += duration

        seg_path = segments_dir / f"seg_{index:03d}.mp4"
        encode_segment(
            source_path,
            seg_path,
            start_sec,
            end_sec,
            ffmpeg_path=ffmpeg_path,
            scale=scale,
            ffmpeg_timeout=ffmpeg_timeout,
        )
        encoded_paths.append(seg_path)

        if on_progress is not None:
            on_progress(index, total, f"区間 {index}/{total} を処理しました")

    if on_progress is not None:
        on_progress(total, total, "区間を連結中…")

    concat_log_dir = output_dir
    concat_segments(
        encoded_paths,
        output_path,
        ffmpeg_path=ffmpeg_path,
        log_dir=concat_log_dir,
        ffmpeg_timeout=ffmpeg_timeout,
    )
    command_log_path = concat_log_dir / f"{output_path.stem}.ffmpeg.log"

    if not keep_segments:
        for seg_path in encoded_paths:
            seg_path.unlink(missing_ok=True)

    return ConcatResult(
        video_id=video_id,
        output_path=output_path,
        command_log_path=command_log_path,
        segment_count=len(segments),
        total_duration_sec=total_duration_sec,
    )
