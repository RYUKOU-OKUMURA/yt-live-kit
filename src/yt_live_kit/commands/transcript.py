"""transcript サブコマンド — VTT 整形・圧縮."""

import typer

from yt_live_kit.config import get_settings
from yt_live_kit.services.transcript import TranscriptError, build_transcripts


def transcript_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
) -> None:
    """VTT から full.txt / compressed.txt を生成する."""
    settings = get_settings()
    try:
        full_path, compressed_path = build_transcripts(video_id, settings)
    except TranscriptError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"全文版: {full_path}")
    typer.echo(f"圧縮版: {compressed_path}")
