"""縦型ショート動画の生成."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.ffmpeg import (
    FFMPEG_DEFAULT,
    FfmpegError,
    encode_segment,
    ensure_source_video,
    find_ffmpeg,
    save_command_log,
)
from yt_live_kit.services.subtitle_burn import (
    build_segment_subtitle,
    is_japanese_font_available,
    resolve_font,
)

MIN_DURATION_SEC = 10.0
MAX_DURATION_SEC = 180.0

BLUR_LAYOUT_FILTER = (
    "split[a][b];"
    "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=20[bg];"
    "[b]scale=1080:-1[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)
CROP_LAYOUT_FILTER = "crop=ih*9/16:ih,scale=1080:1920"


class ShortsError(Exception):
    """ショート動画生成エラー."""


@dataclass(frozen=True)
class ShortResult:
    """ショート動画生成結果."""

    video_id: str
    output_path: Path
    command_log_path: Path
    layout: str
    burned_subtitles: bool
    duration_sec: float
    font_warning: str | None = None


def _segment_output_name(start: float, end: float, output_name: str | None) -> str:
    if output_name is not None:
        return output_name
    return f"short_{start:g}_{end:g}.mp4"


def _intermediate_segment_name(start: float, end: float) -> str:
    return f"short_{start:g}_{end:g}_src.mp4"


def validate_short_duration(start: float, end: float) -> float:
    """ショート区間の長さを検証し、秒数を返す."""
    if end <= start:
        raise ShortsError("終了時刻は開始時刻より後である必要があります。")

    duration = end - start
    if duration < MIN_DURATION_SEC:
        raise ShortsError(
            f"ショート動画の長さは {int(MIN_DURATION_SEC)} 秒以上である必要があります。"
            f"（指定: {duration:.1f} 秒）"
        )
    if duration > MAX_DURATION_SEC:
        raise ShortsError(
            f"ショート動画の長さは {int(MAX_DURATION_SEC)} 秒以下である必要があります。"
            f"（指定: {duration:.1f} 秒）"
        )
    return duration


def escape_ffmpeg_subtitles_path(path: Path) -> str:
    """ffmpeg subtitles フィルタ用にパスをエスケープする."""
    raw = str(path)
    if len(raw) >= 2 and raw[1] == ":":
        escaped = raw.replace("\\", "/")
    else:
        escaped = path.resolve().as_posix()
    escaped = escaped.replace("'", "\\'")
    if len(escaped) >= 2 and escaped[1] == ":":
        escaped = escaped[0] + "\\:" + escaped[2:].replace(":", "\\:")
    else:
        escaped = escaped.replace(":", "\\:")
    return escaped


def build_subtitle_filter(ass_path: Path, font_name: str) -> str:
    """字幕焼き込みフィルタ文字列を返す."""
    escaped_path = escape_ffmpeg_subtitles_path(ass_path)
    return (
        f"subtitles={escaped_path}:force_style="
        f"'FontName={font_name},FontSize=54,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "Outline=3,Alignment=2,MarginV=180'"
    )


def build_layout_filter(layout: str) -> str:
    """レイアウト用フィルタ文字列を返す."""
    if layout == "blur":
        return BLUR_LAYOUT_FILTER
    if layout == "crop":
        return CROP_LAYOUT_FILTER
    raise ShortsError(
        f"不明なレイアウトです: {layout}（'blur' または 'crop' を指定してください）"
    )


def build_video_filter_chain(
    layout: str,
    *,
    ass_path: Path | None = None,
    font_name: str | None = None,
) -> tuple[str, bool]:
    """動画フィルタチェーンと filter_complex 要否を返す."""
    layout_filter = build_layout_filter(layout)
    use_complex = layout == "blur"

    if ass_path is not None and font_name is not None:
        subtitle_filter = build_subtitle_filter(ass_path, font_name)
        layout_filter = f"{layout_filter},{subtitle_filter}"

    return layout_filter, use_complex


def _build_short_layout_command(
    source: Path,
    output: Path,
    filter_chain: str,
    *,
    use_filter_complex: bool,
    ffmpeg_path: str = FFMPEG_DEFAULT,
) -> list[str]:
    """パス2（整形）用コマンドを組み立てる（-ss / -t は付与しない）."""
    ffmpeg = find_ffmpeg(ffmpeg_path)
    cmd: list[str] = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
    ]

    if use_filter_complex:
        cmd.extend(["-filter_complex", f"[0:v]{filter_chain}[vout]", "-map", "[vout]", "-map", "0:a?"])
    else:
        cmd.extend(["-vf", filter_chain])

    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
    )
    return cmd


def build_short(
    video_id: str,
    start: float,
    end: float,
    settings: Settings | None = None,
    *,
    layout: str = "blur",
    burn_subtitles: bool = True,
    output_name: str | None = None,
    ffmpeg_path: str = FFMPEG_DEFAULT,
    on_progress: Callable[[str], None] | None = None,
    keep_intermediate: bool = False,
) -> ShortResult:
    """縦型ショート動画を生成する（2パス: 精密シークで切り出してから字幕・レイアウトを焼き込む）."""
    settings = settings or get_settings()
    duration_sec = validate_short_duration(start, end)

    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise ShortsError(f"動画ディレクトリが見つかりません: {video_dir}")

    source_path = ensure_source_video(video_id, settings)

    # パス1（切り出し）: 精密シークで [start, end] を 0 秒始まりの中間ファイルへ切り出す。
    segments_dir = video_dir / "shorts" / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    intermediate_path = segments_dir / _intermediate_segment_name(start, end)

    if on_progress:
        on_progress("切り出し中…")
    try:
        encode_segment(
            source_path,
            intermediate_path,
            start,
            end,
            ffmpeg_path=ffmpeg_path,
        )
    except FfmpegError as exc:
        raise ShortsError(str(exc)) from exc

    ass_path: Path | None = None
    font_name: str | None = None
    font_warning: str | None = None
    if burn_subtitles:
        if on_progress:
            on_progress("字幕を準備中…")
        ass_path = build_segment_subtitle(
            video_id,
            start,
            end,
            settings,
            ffmpeg_path=ffmpeg_path,
        )
        font_name = resolve_font(settings.subtitle_font)
        if not is_japanese_font_available(settings.subtitle_font):
            font_warning = (
                "日本語フォントが見つかりません。"
                "字幕が正しく表示されない可能性があります。"
            )

    filter_chain, use_filter_complex = build_video_filter_chain(
        layout,
        ass_path=ass_path,
        font_name=font_name,
    )

    output_dir = video_dir / "shorts" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _segment_output_name(start, end, output_name)

    if on_progress:
        if burn_subtitles:
            on_progress("字幕を焼き込み中…")
        else:
            on_progress("変換中…")

    # パス2（整形）: 0 秒始まりの中間ファイルに対して -ss / -t を使わずレイアウト・字幕を焼き込む。
    cmd = _build_short_layout_command(
        intermediate_path,
        output_path,
        filter_chain,
        use_filter_complex=use_filter_complex,
        ffmpeg_path=ffmpeg_path,
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
        stderr = (result.stderr or "").strip()
        raise ShortsError(
            f"ショート動画の生成に失敗しました。ログ: {log_path}"
            + (f"\n（詳細: {stderr}）" if stderr else "")
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ShortsError(
            f"ショート動画ファイルが生成されませんでした。ログ: {log_path}"
        )

    if not keep_intermediate:
        intermediate_path.unlink(missing_ok=True)

    return ShortResult(
        video_id=video_id,
        output_path=output_path,
        command_log_path=log_path,
        layout=layout,
        burned_subtitles=burn_subtitles,
        duration_sec=duration_sec,
        font_warning=font_warning,
    )
