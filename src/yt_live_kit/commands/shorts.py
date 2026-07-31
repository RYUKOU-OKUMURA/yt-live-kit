"""short サブコマンド — 縦型ショート動画生成."""

from typing import Literal

import typer

from yt_live_kit.config import get_settings
from yt_live_kit.services.chapter_validator import parse_timestamp_to_seconds
from yt_live_kit.services.shorts import ShortsError, build_short

LayoutChoice = Literal["blur", "crop"]


def _format_timestamp_error(exc: ValueError, raw: str) -> str:
    """時刻パース失敗時のメッセージを日本語に整形する.

    int() が投げる英語メッセージ（invalid literal for int() ...）はそのまま
    ユーザーに見せず日本語に差し替える。parse_timestamp_to_seconds 自身が
    返す日本語メッセージ（秒の範囲エラー等）はそのまま活かす。
    """
    message = str(exc)
    if "invalid literal" in message:
        return f"時刻は HH:MM:SS または M:SS の形式で入力してください: {raw}"
    return message


def short_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
    start: str = typer.Option(..., "--start", help="開始時刻（HH:MM:SS または M:SS）"),
    end: str = typer.Option(..., "--end", help="終了時刻（HH:MM:SS または M:SS）"),
    layout: LayoutChoice = typer.Option(
        "blur",
        "--layout",
        help="縦型レイアウト（blur: ぼかし背景 / crop: 中央クロップ）",
    ),
    no_subtitles: bool = typer.Option(
        False,
        "--no-subtitles",
        help="字幕を焼き込まない",
    ),
) -> None:
    """指定区間から縦型ショート動画を生成する."""
    settings = get_settings()

    try:
        start_sec = parse_timestamp_to_seconds(start)
    except ValueError as exc:
        typer.secho(_format_timestamp_error(exc, start), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    try:
        end_sec = parse_timestamp_to_seconds(end)
    except ValueError as exc:
        typer.secho(_format_timestamp_error(exc, end), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    try:
        result = build_short(
            video_id,
            float(start_sec),
            float(end_sec),
            settings,
            layout=layout,
            burn_subtitles=not no_subtitles,
            ffmpeg_path=settings.ffmpeg_path,
        )
        typer.echo(f"ショート生成完了: {result.output_path}")
        typer.echo(f"レイアウト: {result.layout}")
        typer.echo(f"字幕: {'あり' if result.burned_subtitles else 'なし'}")
        typer.echo(f"コマンドログ: {result.command_log_path}")
        if result.font_warning:
            typer.secho(result.font_warning, fg=typer.colors.YELLOW, err=True)
    except ShortsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
