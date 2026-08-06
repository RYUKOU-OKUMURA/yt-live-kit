"""chapters サブコマンド — プロンプト生成・Codex 連携・チャプター保存."""

from pathlib import Path

import typer

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services.ai_prompt import (
    AiPromptError,
    ChapterValidationError,
    CodexNotFoundError,
    generate_chapters,
    save_chapters_from_file,
)
from yt_live_kit.services.pipeline import backup_file


def _chapters_path(video_id: str, settings: Settings) -> Path:
    return settings.data_dir / video_id / "chapters" / "chapters.md"


def chapters_cmd(
    video_id: str = typer.Argument(..., help="YouTube 動画 ID"),
    prompt_only: bool = typer.Option(
        False,
        "--prompt-only",
        help="プロンプトファイルのみ生成（Codex を呼ばない）",
    ),
    from_file: Path | None = typer.Option(
        None,
        "--from-file",
        help="手動生成したチャプターファイルを検証して保存",
    ),
) -> None:
    """圧縮テキストからチャプター案を生成する."""
    settings = get_settings()

    try:
        if from_file is not None:
            backup_file(_chapters_path(video_id, settings))
            chapters_path, validation = save_chapters_from_file(video_id, from_file, settings)
            typer.echo(f"チャプター保存完了: {chapters_path}")
            typer.echo(f"チャプター数: {len(validation.chapters)}")
            return

        if not prompt_only:
            backup_file(_chapters_path(video_id, settings))

        result = generate_chapters(video_id, settings, prompt_only=prompt_only)
        typer.echo(f"プロンプト: {result.prompt_path}")

        if prompt_only or result.chapters_path is None:
            typer.echo("プロンプトのみ生成しました。Codex または Cursor でチャプターを生成してください。")
            return

        typer.echo(f"チャプター: {result.chapters_path}")
        if result.validation:
            typer.echo(f"チャプター数: {len(result.validation.chapters)}")
    except CodexNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=2) from exc
    except ChapterValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except AiPromptError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
