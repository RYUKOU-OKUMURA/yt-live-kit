"""channel サブコマンド — チャンネル配信アーカイブ一覧."""

import typer

from yt_live_kit.config import get_settings
from yt_live_kit.services.channel import (
    ChannelError,
    list_archives,
    load_cache,
    mark_processed,
    normalize_channel_url,
    save_cache,
)


def channel_cmd(
    handle: str = typer.Argument(..., help="チャンネル URL または @handle / handle"),
    limit: int = typer.Option(50, "--limit", help="取得件数の上限"),
    refresh: bool = typer.Option(False, "--refresh", help="キャッシュを無視して再取得する"),
) -> None:
    """チャンネルの配信アーカイブ一覧を表示する."""
    settings = get_settings()

    try:
        channel_url, cache_handle = normalize_channel_url(handle)
    except ChannelError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    try:
        if refresh:
            doc = list_archives(channel_url, limit=limit, settings=settings)
            save_cache(doc, settings)
        else:
            doc = load_cache(cache_handle, settings=settings)
            if doc is None:
                doc = list_archives(channel_url, limit=limit, settings=settings)
                save_cache(doc, settings)
    except ChannelError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    for video, processed in mark_processed(doc, settings=settings):
        prefix = "# " if processed else ""
        typer.echo(f"{prefix}{video.video_id}\t{video.title}\t{video.url}")
