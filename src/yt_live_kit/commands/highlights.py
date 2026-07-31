"""highlights サブコマンド — ハイライト区間提案・連結."""

import typer

from yt_live_kit.config import get_settings
from yt_live_kit.models.highlights import HighlightSegment
from yt_live_kit.services.ai_prompt import CodexNotFoundError
from yt_live_kit.services.ffmpeg import FfmpegError, cut_and_concat
from yt_live_kit.services.highlights import (
    HighlightValidationError,
    HighlightsError,
    load_segments_file,
    suggest_highlights,
)

highlights_app = typer.Typer(
    name="highlights",
    help="ハイライト区間の提案とまとめ動画の生成",
    no_args_is_help=True,
)


def _parse_segment_indices(text: str | None, total: int) -> list[int]:
    """1 始まりの番号文字列を 0 始まりインデックスに変換する."""
    if text is None or not text.strip():
        return list(range(total))

    indices: list[int] = []
    for part in text.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        try:
            number = int(stripped)
        except ValueError as exc:
            raise typer.BadParameter(
                f"区間番号は整数で指定してください: {stripped!r}"
            ) from exc
        if number < 1 or number > total:
            raise typer.BadParameter(
                f"区間番号は 1 から {total} の範囲で指定してください: {number}"
            )
        index = number - 1
        if index not in indices:
            indices.append(index)
    if not indices:
        raise typer.BadParameter("有効な区間番号が指定されていません。")
    return indices


def _print_segments(segments: list[HighlightSegment]) -> None:
    for index, segment in enumerate(segments, start=1):
        typer.echo(
            f"{index}. {segment.start} - {segment.end}  {segment.title}"
        )


@highlights_app.command("suggest")
def highlights_suggest_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
    prompt_only: bool = typer.Option(
        False,
        "--prompt-only",
        help="プロンプトファイルのみ生成（Codex を呼ばない）",
    ),
) -> None:
    """圧縮テキストからハイライト区間候補を生成する."""
    settings = get_settings()

    try:
        result = suggest_highlights(
            video_id,
            settings,
            prompt_only=prompt_only,
        )
        typer.echo(f"プロンプト: {result.prompt_path}")

        if prompt_only or result.segments_path is None:
            typer.echo(
                "プロンプトのみ生成しました。Codex または Cursor で区間 JSON を生成してください。"
            )
            return

        typer.echo(f"区間: {result.segments_path}")
        typer.echo(f"区間数: {len(result.segments)}")
        if result.segments:
            _print_segments(list(result.segments))
    except CodexNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=2) from exc
    except HighlightValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except HighlightsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@highlights_app.command("build")
def highlights_build_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
    segments: str | None = typer.Option(
        None,
        "--segments",
        help="連結する区間番号（1 始まり、カンマ区切り。省略時は全区間）",
    ),
    keep_segments: bool = typer.Option(
        False,
        "--keep-segments",
        help="中間セグメントファイルを残す",
    ),
) -> None:
    """保存済みハイライト区間を連結してまとめ動画を生成する."""
    settings = get_settings()

    doc = load_segments_file(video_id, settings)
    if doc is None or not doc.candidates:
        typer.secho(
            "ハイライト区間が見つかりません。"
            "先に highlights suggest を実行するか、segments.json を配置してください。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        indices = _parse_segment_indices(segments, len(doc.candidates))
    except typer.BadParameter as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    selected = [doc.candidates[i] for i in indices]

    try:
        result = cut_and_concat(
            video_id,
            selected,
            settings,
            keep_segments=keep_segments,
        )
        typer.echo(f"連結完了: {result.output_path}")
        typer.echo(f"区間数: {len(selected)}")
        typer.echo(f"コマンドログ: {result.command_log_path}")
    except FfmpegError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
