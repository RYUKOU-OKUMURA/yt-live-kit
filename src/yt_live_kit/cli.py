"""上級者向け CLI エントリポイント（スタブ）."""

import typer

app = typer.Typer(
    name="yt-live-kit",
    help="YouTube ライブアーカイブから字幕・チャプター・切り抜き素材を生成する",
    no_args_is_help=True,
)


@app.callback()
def callback() -> None:
    """YouTube ライブアーカイブ処理ツール."""
    pass


@app.command()
def version() -> None:
    """バージョンを表示する."""
    from yt_live_kit import __version__

    typer.echo(f"yt-live-kit {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
