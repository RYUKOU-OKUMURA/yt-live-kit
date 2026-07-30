"""fetch サブコマンド — メタデータ・字幕取得."""

import typer

from yt_live_kit.config import get_settings
from yt_live_kit.services.ytdlp import YtdlpError, fetch


def fetch_cmd(url: str = typer.Argument(..., help="YouTube 公開アーカイブ URL")) -> None:
    """URL からメタデータと字幕 VTT を取得する."""
    settings = get_settings()
    try:
        meta = fetch(url, settings)
    except YtdlpError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"取得完了: {meta.title} ({meta.id})")
    typer.echo(f"保存先: {settings.data_dir / meta.id}")
