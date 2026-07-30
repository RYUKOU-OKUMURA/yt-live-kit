"""上級者向け CLI エントリポイント."""

import typer

from yt_live_kit.commands.chapters import chapters_cmd
from yt_live_kit.commands.clips import clips_app
from yt_live_kit.commands.fetch import fetch_cmd
from yt_live_kit.commands.run import run_cmd
from yt_live_kit.commands.transcript import transcript_cmd

app = typer.Typer(
    name="yt-live-kit",
    help="YouTube ライブアーカイブから字幕・チャプター・切り抜き素材を生成する",
    no_args_is_help=True,
)


@app.callback()
def callback() -> None:
    """YouTube ライブアーカイブ処理ツール."""
    pass


app.command("fetch")(fetch_cmd)
app.command("transcript")(transcript_cmd)
app.command("chapters")(chapters_cmd)
app.command("run")(run_cmd)
app.add_typer(clips_app, name="clips")


@app.command()
def version() -> None:
    """バージョンを表示する."""
    from yt_live_kit import __version__

    typer.echo(f"yt-live-kit {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
