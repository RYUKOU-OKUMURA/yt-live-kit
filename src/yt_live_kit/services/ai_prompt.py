"""プロンプト生成・Codex CLI 連携・チャプター保存."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.services._fsutil import write_text_atomically
from yt_live_kit.services.chapter_validator import (
    ValidationResult,
    format_chapters_markdown,
    validate_chapters,
)

PLACEHOLDER = "{{compressed_transcript}}"
TEMPLATE_NAME = "chapters.md"
CODEX_INSTALL_HINT = """\
Codex CLI が見つかりません。チャプターの自動生成には Codex CLI が必要です。

【インストール手順】
1. Node.js がインストールされていることを確認してください
2. 次のコマンドで Codex CLI をインストールします:
   npm install -g @openai/codex
3. インストール後、認証を行います:
   codex login

【フォールバック（Cursor 手動運用）】
1. 次のプロンプトファイルを Cursor のチャットに貼り付けてください:
   {prompt_path}
2. 生成されたチャプター行を {chapters_path} に保存してください
3. 保存後、次のコマンドで検証できます:
   uv run yt-live-kit chapters {video_id} --from-file {chapters_path}
"""


class AiPromptError(Exception):
    """プロンプト生成・AI 連携エラー."""


class CodexNotFoundError(AiPromptError):
    """Codex CLI 未導入."""


class ChapterValidationError(AiPromptError):
    """チャプターバリデーション失敗."""

    def __init__(self, message: str, validation: ValidationResult) -> None:
        super().__init__(message)
        self.validation = validation


@dataclass(frozen=True)
class ChapterGenerationResult:
    """チャプター生成結果."""

    video_id: str
    prompt_path: Path
    chapters_path: Path | None
    used_codex: bool
    validation: ValidationResult | None


def find_project_root() -> Path:
    """リポジトリルート（prompts/ があるディレクトリ）を返す."""
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in [start, *start.parents]:
            if (candidate / "prompts" / TEMPLATE_NAME).is_file():
                return candidate
    raise AiPromptError(
        f"プロンプトテンプレートが見つかりません: prompts/{TEMPLATE_NAME}。"
        "リポジトリルートで実行してください。"
    )


def load_chapters_template(project_root: Path | None = None) -> str:
    """チャプター生成用テンプレートを読み込む."""
    root = project_root or find_project_root()
    template_path = root / "prompts" / TEMPLATE_NAME
    if not template_path.is_file():
        raise AiPromptError(f"プロンプトテンプレートが見つかりません: {template_path}")
    return template_path.read_text(encoding="utf-8")


def build_chapters_prompt(compressed_text: str, project_root: Path | None = None) -> str:
    """圧縮テキストを埋め込んだ完成プロンプトを返す."""
    template = load_chapters_template(project_root)
    if PLACEHOLDER not in template:
        raise AiPromptError(f"テンプレートにプレースホルダ {PLACEHOLDER} がありません。")
    return template.replace(PLACEHOLDER, compressed_text.strip())


def is_codex_available(codex_path: str = "codex") -> bool:
    """Codex CLI が PATH 上にあるか."""
    return shutil.which(codex_path) is not None


def _read_compressed_transcript(video_dir: Path) -> str:
    compressed_path = video_dir / "transcript" / "compressed.txt"
    if not compressed_path.is_file():
        raise AiPromptError(
            f"圧縮版テキストが見つかりません: {compressed_path}。"
            "先に transcript を実行してください。"
        )
    return compressed_path.read_text(encoding="utf-8")


def save_prompt_file(video_id: str, prompt: str, settings: Settings) -> Path:
    """完成プロンプトを data/{video_id}/prompt_chapters.txt に保存する."""
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise AiPromptError(f"動画ディレクトリが見つかりません: {video_dir}")
    prompt_path = video_dir / "prompt_chapters.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def save_chapters_file(
    video_id: str,
    chapters_text: str,
    settings: Settings,
) -> tuple[Path, ValidationResult]:
    """バリデーション通過後に chapters/chapters.md に保存する."""
    validation = validate_chapters(chapters_text)
    if not validation.ok:
        detail = "\n".join(f"- {err}" for err in validation.errors)
        raise ChapterValidationError(
            f"チャプターの形式が正しくありません:\n{detail}",
            validation,
        )

    video_dir = settings.data_dir / video_id
    chapters_dir = video_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    chapters_path = chapters_dir / "chapters.md"
    content = format_chapters_markdown(validation.chapters)
    write_text_atomically(chapters_path, content)
    return chapters_path, validation


def invoke_codex(prompt: str, *, codex_path: str = "codex", timeout: int = 600) -> str:
    """Codex CLI exec でプロンプトを実行し、応答テキストを返す."""
    if not is_codex_available(codex_path):
        raise CodexNotFoundError(CODEX_INSTALL_HINT.format(
            prompt_path="（プロンプトファイルのパス）",
            chapters_path="（保存先パス）",
            video_id="VIDEO_ID",
        ))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        output_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                codex_path,
                "exec",
                "-s",
                "read-only",
                "--skip-git-repo-check",
                "-o",
                str(output_path),
                "-",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"終了コード {result.returncode}"
            raise AiPromptError(f"Codex CLI の実行に失敗しました: {detail}")

        if output_path.is_file() and output_path.stat().st_size > 0:
            return output_path.read_text(encoding="utf-8")

        stdout = (result.stdout or "").strip()
        if stdout:
            return stdout

        raise AiPromptError("Codex CLI からチャプター出力を取得できませんでした。")
    except subprocess.TimeoutExpired as exc:
        raise AiPromptError(f"Codex CLI がタイムアウトしました（{timeout} 秒）。") from exc
    finally:
        output_path.unlink(missing_ok=True)


def generate_chapters(
    video_id: str,
    settings: Settings | None = None,
    *,
    prompt_only: bool = False,
    codex_path: str = "codex",
) -> ChapterGenerationResult:
    """圧縮テキストからプロンプト生成し、Codex でチャプターを生成して保存する."""
    settings = settings or get_settings()
    video_dir = settings.data_dir / video_id
    if not video_dir.is_dir():
        raise AiPromptError(f"動画ディレクトリが見つかりません: {video_dir}")

    compressed_text = _read_compressed_transcript(video_dir)
    project_root = find_project_root()
    prompt = build_chapters_prompt(compressed_text, project_root)
    prompt_path = save_prompt_file(video_id, prompt, settings)

    if prompt_only:
        return ChapterGenerationResult(
            video_id=video_id,
            prompt_path=prompt_path,
            chapters_path=None,
            used_codex=False,
            validation=None,
        )

    if not is_codex_available(codex_path):
        chapters_path = video_dir / "chapters" / "chapters.md"
        hint = CODEX_INSTALL_HINT.format(
            prompt_path=prompt_path,
            chapters_path=chapters_path,
            video_id=video_id,
        )
        raise CodexNotFoundError(hint)

    raw_output = invoke_codex(prompt, codex_path=codex_path)
    chapters_path, validation = save_chapters_file(video_id, raw_output, settings)

    return ChapterGenerationResult(
        video_id=video_id,
        prompt_path=prompt_path,
        chapters_path=chapters_path,
        used_codex=True,
        validation=validation,
    )


def save_chapters_from_file(
    video_id: str,
    source_path: Path,
    settings: Settings | None = None,
) -> tuple[Path, ValidationResult]:
    """外部ファイル（Cursor 手動生成等）からチャプターを検証・保存する."""
    settings = settings or get_settings()
    if not source_path.is_file():
        raise AiPromptError(f"ファイルが見つかりません: {source_path}")
    text = source_path.read_text(encoding="utf-8")
    return save_chapters_file(video_id, text, settings)
