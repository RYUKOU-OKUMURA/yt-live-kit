"""縦型ショート動画の生成."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.ffmpeg import (
    FFMPEG_DEFAULT,
    FfmpegError,
    concat_segments,
    encode_segment,
    ensure_source_video,
    ensure_subtitles_filter,
    find_ffmpeg,
    resolve_output_filename,
    save_command_log,
)
from yt_live_kit.services.subtitle_burn import (
    SubtitleBurnError,
    build_concatenated_subtitle,
    build_segment_subtitle,
    get_telop_preset,
    is_japanese_font_available,
    resolve_font,
)
from yt_live_kit.services.telop import (
    TelopError,
    make_clip_id,
    normalize_segment_bounds,
    validate_telop_script,
)

logger = logging.getLogger(__name__)

MIN_DURATION_SEC = 10.0
MAX_DURATION_SEC = 180.0

# パス1（切り出し）の中間ファイルは一時的なので、パス2 で再エンコードされる
# 世代劣化を抑えるため、最終出力（crf=20）より高品質（低 crf）で書き出す。
INTERMEDIATE_CRF = 16

BLUR_LAYOUT_FILTER = (
    "split[a][b];"
    "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=20[bg];"
    "[b]scale=1080:-1[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)
CROP_LAYOUT_FILTER = "crop=ih*9/16:ih,scale=1080:1920"


class ShortsError(Exception):
    """ショート動画生成エラー."""


def _run_ffmpeg_layout(
    cmd: list[str],
    *,
    ffmpeg_timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=ffmpeg_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ShortsError(
            f"ffmpeg の実行が {ffmpeg_timeout} 秒でタイムアウトしました。"
            "処理が長時間かかっている可能性があります。"
        ) from exc


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


ShortsProgressCallback = Callable[[int, int, str], None] | None


def _resolve_short_output_name(
    output_name: str | None,
    default_name: str,
) -> str:
    try:
        return resolve_output_filename(
            output_name, default_name, required_suffix=".mp4"
        )
    except ValueError as exc:
        raise ShortsError(str(exc)) from exc


def _segment_output_name(start: float, end: float, output_name: str | None) -> str:
    return _resolve_short_output_name(output_name, f"short_{start:g}_{end:g}.mp4")


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
    """ffmpeg filtergraph 内の subtitles filename 用にパスをエスケープする.

    libavfilter は filter option と filtergraph の 2 段階で文字列を解析する。
    subprocess には argv を直接渡しているため shell 用の追加 escape は行わない。
    """
    raw = str(path)
    is_windows_drive = (
        len(raw) >= 3 and raw[1] == ":" and raw[2] in {"/", "\\"}
    )
    is_windows_unc = raw.startswith("\\\\")
    if is_windows_drive or is_windows_unc:
        normalized = raw.replace("\\", "/")
    else:
        normalized = path.resolve().as_posix()

    # 第 1 段: filename option の区切り文字と quote / escape 自体を保護する。
    option_escaped = "".join(
        f"\\{character}" if character in "\\':" else character
        for character in normalized
    )
    # 第 2 段: option を含む filtergraph 全体で特別扱いされる文字を保護する。
    return "".join(
        f"\\{character}" if character in "\\'[],;" else character
        for character in option_escaped
    )


def build_subtitle_filter(ass_path: Path, font_name: str) -> str:
    """字幕焼き込みフィルタ文字列を返す."""
    escaped_path = escape_ffmpeg_subtitles_path(ass_path)
    return (
        f"subtitles=filename={escaped_path}:force_style="
        f"'FontName={font_name},FontSize=54,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "Outline=3,Alignment=2,MarginV=180'"
    )


def build_ass_subtitle_filter(ass_path: Path) -> str:
    """ASS 内のスタイルを維持する字幕焼き込みフィルタを返す."""
    return f"subtitles=filename={escape_ffmpeg_subtitles_path(ass_path)}"


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


def _validate_explicit_hook(hook_text: str | None) -> str | None:
    if hook_text is None:
        return None
    cleaned = hook_text.strip()
    if not cleaned:
        raise ShortsError("フックタイトルを入力してください。")
    if "<" in cleaned or ">" in cleaned:
        raise ShortsError("フックタイトルに半角の山カッコは使えません。")
    return cleaned


def build_short_from_segments(
    video_id: str,
    segments: list[tuple[float, float]],
    settings: Settings | None = None,
    *,
    layout: str = "blur",
    telop_script: TelopScriptDocument | None = None,
    hook_text: str | None = None,
    preset: str = "default",
    hook_preset: str = "hook",
    output_name: str | None = None,
    ffmpeg_path: str | None = None,
    on_progress: ShortsProgressCallback = None,
    keep_intermediate: bool = False,
) -> ShortResult:
    """複数区間を入力順に連結し、ASS テロップ付き縦型動画を生成する."""
    settings = settings or get_settings()
    ffmpeg_timeout = settings.ffmpeg_timeout

    # ダウンロードや ffmpeg を始める前に、安価な入力検証をすべて完了する。
    try:
        normalized_segments = normalize_segment_bounds(segments)
        clip_id = make_clip_id(segments)
    except TelopError as exc:
        raise ShortsError(str(exc)) from exc

    total_ms = sum(bounds.duration_ms for bounds in normalized_segments)
    if total_ms < int(MIN_DURATION_SEC * 1000):
        raise ShortsError(
            f"ショート動画の長さは {int(MIN_DURATION_SEC)} 秒以上である必要があります。"
            f"（指定: {total_ms / 1000:.1f} 秒）"
        )
    if total_ms > int(MAX_DURATION_SEC * 1000):
        raise ShortsError(
            f"ショート動画の長さは {int(MAX_DURATION_SEC)} 秒以下である必要があります。"
            f"（指定: {total_ms / 1000:.1f} 秒）"
            "区間を減らすか短くしてください。"
        )

    build_layout_filter(layout)
    formal_name = _resolve_short_output_name(output_name, f"short_{clip_id}.mp4")
    explicit_hook = _validate_explicit_hook(hook_text)
    try:
        get_telop_preset(preset)
        get_telop_preset(hook_preset)
    except SubtitleBurnError as exc:
        raise ShortsError(str(exc)) from exc

    validated_document: TelopScriptDocument | None = None
    if telop_script is not None:
        try:
            validation = validate_telop_script(telop_script, segments=segments)
        except TelopError as exc:
            raise ShortsError(str(exc)) from exc
        if not validation.ok or validation.document is None:
            detail = "、".join(validation.errors)
            raise ShortsError(f"テロップ台本が入力区間と一致しません: {detail}")
        validated_document = validation.document

    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise ShortsError(f"動画ディレクトリが見つかりません: {video_dir}")

    configured_ffmpeg_path = ffmpeg_path or settings.ffmpeg_path
    try:
        effective_ffmpeg_path = ensure_subtitles_filter(configured_ffmpeg_path)
    except FfmpegError as exc:
        raise ShortsError(str(exc)) from exc

    output_dir = video_dir / "shorts" / "output"
    output_path = output_dir / formal_name
    log_path = output_dir / f"{output_path.stem}.ffmpeg.log"
    intermediate_dir = video_dir / "shorts" / "segments" / clip_id
    temporary_output: Path | None = None
    total_steps = len(normalized_segments) + 3
    processing_succeeded = False

    try:
        try:
            source_path = ensure_source_video(video_id, settings)
        except FfmpegError as exc:
            raise ShortsError(str(exc)) from exc

        segment_paths: list[Path] = []
        for index, bounds in enumerate(normalized_segments, start=1):
            if on_progress is not None:
                on_progress(index, total_steps, f"区間 {index} を切り出しています…")
            segment_path = intermediate_dir / f"seg_{index:03d}.mp4"
            try:
                encode_segment(
                    source_path,
                    segment_path,
                    bounds.start_sec,
                    bounds.end_sec,
                    ffmpeg_path=effective_ffmpeg_path,
                    crf=INTERMEDIATE_CRF,
                    ffmpeg_timeout=ffmpeg_timeout,
                )
            except FfmpegError as exc:
                raise ShortsError(str(exc)) from exc
            segment_paths.append(segment_path)

        if on_progress is not None:
            on_progress(
                len(normalized_segments) + 1,
                total_steps,
                "区間を連結しています…",
            )
        concat_path = intermediate_dir / "concat.mp4"
        try:
            concat_segments(
                segment_paths,
                concat_path,
                ffmpeg_path=effective_ffmpeg_path,
                log_dir=intermediate_dir,
            )
        except FfmpegError as exc:
            raise ShortsError(str(exc)) from exc

        if on_progress is not None:
            on_progress(
                len(normalized_segments) + 2,
                total_steps,
                "字幕を準備しています…",
            )
        try:
            ass_path = build_concatenated_subtitle(
                video_id,
                segments,
                settings,
                telop_script=validated_document,
                hook_text=explicit_hook,
                preset=preset,
                hook_preset=hook_preset,
            )
        except SubtitleBurnError as exc:
            raise ShortsError(str(exc)) from exc

        font_warning = None
        if not is_japanese_font_available(settings.subtitle_font):
            font_warning = (
                "日本語フォントが見つかりません。"
                "字幕が正しく表示されない可能性があります。"
            )

        filter_chain = (
            f"{build_layout_filter(layout)},{build_ass_subtitle_filter(ass_path)}"
        )
        use_filter_complex = layout == "blur"
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=f".{output_path.stem}.",
            suffix=".mp4",
            delete=False,
        ) as temporary:
            temporary_output = Path(temporary.name)
        temporary_output.unlink(missing_ok=True)

        if on_progress is not None:
            on_progress(
                len(normalized_segments) + 3,
                total_steps,
                "字幕を焼き込んでいます…",
            )
        try:
            cmd = _build_short_layout_command(
                concat_path,
                temporary_output,
                filter_chain,
                use_filter_complex=use_filter_complex,
                ffmpeg_path=effective_ffmpeg_path,
            )
            result = _run_ffmpeg_layout(cmd, ffmpeg_timeout=ffmpeg_timeout)
        except FfmpegError as exc:
            raise ShortsError(str(exc)) from exc
        except OSError as exc:
            raise ShortsError(f"ffmpeg の実行に失敗しました: {exc}") from exc
        log_path = save_command_log(
            output_dir,
            cmd,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            filename=f"{output_path.stem}.ffmpeg.log",
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise ShortsError(
                f"ショート動画の生成に失敗しました。ログ: {log_path}"
                + (f"\n（詳細: {stderr}）" if stderr else "")
            )
        if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
            raise ShortsError(
                f"ショート動画ファイルが生成されませんでした。ログ: {log_path}"
            )
        try:
            temporary_output.replace(output_path)
        except OSError as exc:
            raise ShortsError(f"ショート動画の保存に失敗しました: {exc}") from exc
        temporary_output = None
        processing_succeeded = True
    finally:
        if temporary_output is not None:
            try:
                temporary_output.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "失敗した一時出力を削除できませんでした: %s",
                    temporary_output,
                    exc_info=True,
                )
        if not keep_intermediate:
            try:
                shutil.rmtree(intermediate_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if processing_succeeded:
                    raise ShortsError(
                        "中間ファイルの削除に失敗しました。"
                        f"手動で削除してください: {intermediate_dir}"
                    ) from exc
                logger.warning(
                    "主処理の失敗後、中間ファイルを削除できませんでした: %s",
                    intermediate_dir,
                    exc_info=True,
                )

    return ShortResult(
        video_id=video_id,
        output_path=output_path,
        command_log_path=log_path,
        layout=layout,
        burned_subtitles=True,
        duration_sec=total_ms / 1000,
        font_warning=font_warning,
    )


def build_short(
    video_id: str,
    start: float,
    end: float,
    settings: Settings | None = None,
    *,
    layout: str = "blur",
    burn_subtitles: bool = True,
    output_name: str | None = None,
    ffmpeg_path: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    keep_intermediate: bool = False,
) -> ShortResult:
    """縦型ショート動画を生成する（2パス: 入力シークで切り出してから字幕・レイアウトを焼き込む）."""
    settings = settings or get_settings()
    ffmpeg_timeout = settings.ffmpeg_timeout
    duration_sec = validate_short_duration(start, end)
    resolved_output_name = _segment_output_name(start, end, output_name)
    build_layout_filter(layout)

    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise ShortsError(f"動画ディレクトリが見つかりません: {video_dir}")

    configured_ffmpeg_path = ffmpeg_path or settings.ffmpeg_path
    try:
        if burn_subtitles:
            effective_ffmpeg_path = ensure_subtitles_filter(configured_ffmpeg_path)
        else:
            effective_ffmpeg_path = find_ffmpeg(configured_ffmpeg_path)
        source_path = ensure_source_video(video_id, settings)
    except FfmpegError as exc:
        raise ShortsError(str(exc)) from exc

    # パス1（切り出し）: 入力シークで [start, end] を 0 秒始まりの中間ファイルへ切り出す。
    # libx264 再エンコードのため出力は 0 秒始まりとなり、パス2 の前提は変わらない。
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
            ffmpeg_path=effective_ffmpeg_path,
            crf=INTERMEDIATE_CRF,
            ffmpeg_timeout=ffmpeg_timeout,
        )
    except FfmpegError as exc:
        raise ShortsError(str(exc)) from exc

    ass_path: Path | None = None
    font_name: str | None = None
    font_warning: str | None = None
    temporary_output: Path | None = None

    # 中間ファイルを作った後は、字幕生成・パス2 のどこで失敗しても片付ける。
    try:
        if burn_subtitles:
            if on_progress:
                on_progress("字幕を準備中…")
            ass_path = build_segment_subtitle(
                video_id,
                start,
                end,
                settings,
                ffmpeg_path=effective_ffmpeg_path,
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
        output_path = output_dir / resolved_output_name
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=f".{output_path.stem}.",
            suffix=".mp4",
            delete=False,
        ) as temporary:
            temporary_output = Path(temporary.name)
        temporary_output.unlink(missing_ok=True)

        if on_progress:
            if burn_subtitles:
                on_progress("字幕を焼き込み中…")
            else:
                on_progress("変換中…")

        # パス2（整形）: 0 秒始まりの中間ファイルに対して -ss / -t を使わずレイアウト・字幕を焼き込む。
        try:
            cmd = _build_short_layout_command(
                intermediate_path,
                temporary_output,
                filter_chain,
                use_filter_complex=use_filter_complex,
                ffmpeg_path=effective_ffmpeg_path,
            )
            result = _run_ffmpeg_layout(cmd, ffmpeg_timeout=ffmpeg_timeout)
        except FfmpegError as exc:
            raise ShortsError(str(exc)) from exc
        except OSError as exc:
            raise ShortsError(f"ffmpeg の実行に失敗しました: {exc}") from exc
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

        if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
            raise ShortsError(
                f"ショート動画ファイルが生成されませんでした。ログ: {log_path}"
            )
        try:
            temporary_output.replace(output_path)
        except OSError as exc:
            raise ShortsError(f"ショート動画の保存に失敗しました: {exc}") from exc
        temporary_output = None
    finally:
        if temporary_output is not None:
            try:
                temporary_output.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "失敗した一時出力を削除できませんでした: %s",
                    temporary_output,
                    exc_info=True,
                )
        # keep_intermediate=False（既定）なら、パス2 の成否によらず中間ファイルを片付ける。
        # keep_intermediate=True はデバッグ用途のため成功・失敗どちらでも残す。
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
