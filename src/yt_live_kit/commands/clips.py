"""clips サブコマンド — 切り抜き候補提案・切り出し."""

from pathlib import Path

import typer

from yt_live_kit.config import get_settings
from yt_live_kit.services.clips import (
    ClipValidationError,
    ClipsError,
    save_candidates_from_file,
    suggest_clips,
)
from yt_live_kit.services.ai_prompt import CodexNotFoundError
from yt_live_kit.services.ffmpeg import FfmpegError, cut_clip

clips_app = typer.Typer(
    name="clips",
    help="切り抜き候補の提案と動画切り出し",
    no_args_is_help=True,
)


@clips_app.command("suggest")
def clips_suggest_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
    prompt_only: bool = typer.Option(
        False,
        "--prompt-only",
        help="プロンプトファイルのみ生成（Codex を呼ばない）",
    ),
    from_file: Path | None = typer.Option(
        None,
        "--from-file",
        help="手動生成した candidates.json を検証して保存",
    ),
) -> None:
    """圧縮テキストから切り抜き候補を生成する."""
    settings = get_settings()

    try:
        if from_file is not None:
            candidates_path, doc = save_candidates_from_file(video_id, from_file, settings)
            typer.echo(f"候補保存完了: {candidates_path}")
            typer.echo(f"候補数: {len(doc.candidates)}")
            return

        result = suggest_clips(video_id, settings, prompt_only=prompt_only)
        typer.echo(f"プロンプト: {result.prompt_path}")

        if prompt_only or result.candidates_path is None:
            typer.echo(
                "プロンプトのみ生成しました。Codex または Cursor で候補 JSON を生成してください。"
            )
            return

        typer.echo(f"候補: {result.candidates_path}")
        typer.echo(f"候補数: {len(result.candidates)}")
    except CodexNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=2) from exc
    except ClipValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except ClipsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@clips_app.command("cut")
def clips_cut_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
    start: str = typer.Option(..., "--start", help="開始時刻（HH:MM:SS または M:SS）"),
    end: str = typer.Option(..., "--end", help="終了時刻（HH:MM:SS または M:SS）"),
    output: str | None = typer.Option(None, "--output", help="出力ファイル名"),
    reencode: bool = typer.Option(False, "--reencode", help="再エンコードで切り出す"),
) -> None:
    """指定区間を ffmpeg で切り出す."""
    settings = get_settings()

    try:
        result = cut_clip(
            video_id,
            start,
            end,
            settings,
            output_name=output,
            ffmpeg_path=settings.ffmpeg_path,
            reencode=reencode,
        )
        typer.echo(f"切り出し完了: {result.output_path}")
        typer.echo(f"コマンドログ: {result.command_log_path}")
    except FfmpegError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
