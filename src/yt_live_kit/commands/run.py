"""run サブコマンド — 一括パイプライン実行."""

from pathlib import Path

import typer

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.batch import load_urls_file, run_batch
from yt_live_kit.services.pipeline import run
from yt_live_kit.services.ytdlp import YtdlpError, check_ytdlp_version_warning

_NO_TARGET_MESSAGE = (
    "「チャプターを作る」「切り抜き候補を出す」"
    "のどちらかを選んでください。"
)


def _apply_cli_overrides(
    data_dir: Path | None,
    sleep: float | None,
    ytdlp_path: str | None,
) -> Settings:
    overrides: dict = {}
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if sleep is not None:
        overrides["sleep"] = sleep
    if ytdlp_path is not None:
        overrides["ytdlp_path"] = ytdlp_path
    return get_settings().model_copy(update=overrides) if overrides else get_settings()


def run_cmd(
    url: str | None = typer.Argument(None, help="YouTube 公開アーカイブ URL"),
    urls_file: Path | None = typer.Option(
        None,
        "--urls-file",
        help="URL 一覧ファイル（1 行 1 URL）",
    ),
    skip_existing: bool = typer.Option(
        False,
        "--skip-existing",
        help="処理済み動画 ID をスキップ",
    ),
    sleep: float | None = typer.Option(
        None,
        "--sleep",
        help="URL 間のスリープ秒数（デフォルト: 設定値）",
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="成果物ルートディレクトリ",
    ),
    ytdlp_path: str | None = typer.Option(
        None,
        "--yt-dlp-path",
        help="yt-dlp バイナリパス",
    ),
    chapters: bool = typer.Option(
        True,
        "--chapters/--no-chapters",
        help="チャプターを生成する",
    ),
    clips: bool = typer.Option(
        True,
        "--clips/--no-clips",
        help="切り抜き候補を生成する",
    ),
) -> None:
    """fetch → transcript → chapters → clips suggest を一括実行する."""
    settings = _apply_cli_overrides(data_dir, sleep, ytdlp_path)

    if not chapters and not clips:
        typer.secho(_NO_TARGET_MESSAGE, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    warning = check_ytdlp_version_warning(settings)
    if warning:
        typer.secho(f"警告: {warning}", fg=typer.colors.YELLOW, err=True)

    if url is None and urls_file is None:
        typer.secho("URL または --urls-file を指定してください。", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if urls_file is not None:
        try:
            urls = load_urls_file(urls_file)
        except FileNotFoundError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if not urls:
            typer.secho("URL 一覧が空です。", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        typer.echo(f"バッチ処理開始: {len(urls)} 件")

        def on_progress(current: int, total: int, item_url: str, message: str) -> None:
            typer.echo(f"[{current}/{total}] {item_url} — {message}")

        results = run_batch(
            urls,
            settings,
            skip_existing=skip_existing,
            sleep_sec=sleep,
            on_progress=on_progress,
            do_chapters=chapters,
            do_clips=clips,
        )

        success = sum(1 for r in results if r.status == "success")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed = sum(1 for r in results if r.status == "failed")
        typer.echo(f"完了: 成功 {success} / スキップ {skipped} / 失敗 {failed}")

        if failed:
            raise typer.Exit(code=1)
        return

    assert url is not None
    try:
        result = run(url, settings, do_chapters=chapters, do_clips=clips)
    except Exception as exc:
        if isinstance(exc, YtdlpError):
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
        else:
            from yt_live_kit.services.pipeline import PipelineError

            if isinstance(exc, PipelineError):
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
            else:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"処理完了: {result.title} ({result.video_id})")
    typer.echo(f"チャプター: {result.chapters_path}")
    if result.clips_error:
        typer.secho(f"切り抜き候補: 警告 — {result.clips_error}", fg=typer.colors.YELLOW, err=True)
    elif result.clips_candidates_path:
        typer.echo(f"切り抜き候補: {result.clips_candidates_path}")
