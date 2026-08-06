"""概要欄テンプレート合成."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from yt_live_kit.config import Settings, get_settings
from yt_live_kit.models.meta import VideoMeta
from yt_live_kit.services._fsutil import advisory_lock, write_text_atomically

_CONFIG_DIR = "_config"
_SHORTS_TEMPLATE_FILENAME = "shorts_description_template.txt"
_SHORTS_DESCRIPTION_BYTE_LIMIT = 5000
_SOURCE_TITLE_PLACEHOLDER = "{{source_title}}"
_SOURCE_URL_PLACEHOLDER = "{{source_url}}"
_DESCRIPTION_PLACEHOLDER = "{{description}}"
SHORTS_DESCRIPTION_CTA = "チャンネル登録は動画下のチャンネル名からお願いします。"
DEFAULT_SHORTS_DESCRIPTION_TEMPLATE = (
    f"{_DESCRIPTION_PLACEHOLDER}\n\n"
    "▼ 元のライブ配信\n"
    f"{_SOURCE_TITLE_PLACEHOLDER}\n"
    f"{_SOURCE_URL_PLACEHOLDER}\n\n"
    f"{SHORTS_DESCRIPTION_CTA}\n"
)

_SHORTS_TEMPLATE_LOCK_FILENAME = ".shorts_description_template.lock"
_SHORTS_PLACEHOLDER_NAMES = (
    _DESCRIPTION_PLACEHOLDER,
    _SOURCE_TITLE_PLACEHOLDER,
    _SOURCE_URL_PLACEHOLDER,
)
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
_SHORTS_TEMPLATE_THREAD_LOCK = Lock()


class DescriptionError(Exception):
    """概要欄生成エラー（ユーザー向け日本語メッセージ）."""


@dataclass(frozen=True, slots=True)
class ShortsDescriptionRequirements:
    """投稿用ショート概要欄が満たすべき不変の要件 snapshot.

    template / meta の fingerprint は本文へ埋め込む値ではないが、preview を
    作った時点の入力を示す証跡として同じ snapshot に凍結する。P6-4 はこの
    object と編集後本文だけを使って、mutable なファイルを再読込せずに再検証
    できる。
    """

    generated_description: str
    source_title: str
    source_url: str
    fixed_cta: str
    template_bytes_fingerprint: str
    meta_json_fingerprint: str
    generated_description_occurrences: int = 1
    source_title_occurrences: int = 1
    source_url_occurrences: int = 1
    fixed_cta_line_occurrences: int = 1

    def __post_init__(self) -> None:
        if self.fixed_cta != SHORTS_DESCRIPTION_CTA:
            raise ValueError("固定 CTA は変更できません。")
        for count in (
            self.generated_description_occurrences,
            self.source_title_occurrences,
            self.source_url_occurrences,
            self.fixed_cta_line_occurrences,
        ):
            if not isinstance(count, int) or count < 0:
                raise ValueError("概要欄の出現必要数は 0 以上の整数で指定してください。")


@dataclass(frozen=True, slots=True)
class ShortsDescriptionBuild:
    """投稿ゲートで合成した本文と、その入力要件 snapshot."""

    description: str
    requirements: ShortsDescriptionRequirements


def get_shorts_template_path(settings: Settings | None = None) -> Path:
    """ショート用概要欄テンプレートファイルのパスを返す."""
    settings = settings or get_settings()
    return settings.data_dir / _CONFIG_DIR / _SHORTS_TEMPLATE_FILENAME


@contextmanager
def _shorts_template_write_lock(path: Path) -> Iterator[None]:
    """既定作成とユーザー保存で共有する thread + process lock."""
    lock_path = path.parent / _SHORTS_TEMPLATE_LOCK_FILENAME
    with _SHORTS_TEMPLATE_THREAD_LOCK:
        with advisory_lock(lock_path):
            yield


def ensure_shorts_description_template(settings: Settings | None = None) -> Path:
    """投稿ゲート用の既定ショートテンプレートを初回だけ作成する.

    存在確認と初回作成を同じ advisory lock の中で行う。既存ファイルは
    bytes を読まず、書き込みもしないため、ユーザーが編集した内容を変更しない。
    """
    settings = settings or get_settings()
    path = get_shorts_template_path(settings)

    try:
        with _shorts_template_write_lock(path):
            if path.is_file():
                return path
            write_text_atomically(
                path,
                DEFAULT_SHORTS_DESCRIPTION_TEMPLATE,
                encoding="utf-8",
            )
            if not path.is_file():
                raise OSError("既定テンプレートの保存結果を確認できません。")
            return path
    except DescriptionError:
        raise
    except Exception as exc:
        raise DescriptionError(
            "ショート用概要欄の既定テンプレートを安全に初回作成できませんでした。"
            "保存先の権限と空き容量を確認してください。"
        ) from exc


def save_shorts_template(text: str, settings: Settings | None = None) -> Path:
    """ショート用概要欄テンプレートを保存してパスを返す."""
    settings = settings or get_settings()
    path = get_shorts_template_path(settings)
    try:
        with _shorts_template_write_lock(path):
            write_text_atomically(path, text, encoding="utf-8")
            return path
    except DescriptionError:
        raise
    except Exception as exc:
        raise DescriptionError(
            "ショート用概要欄テンプレートを安全に保存できませんでした。"
            f"{path} を確認してください。"
        ) from exc


def _load_video_meta(video_id: str, settings: Settings) -> VideoMeta:
    """元配信のメタデータを読む。欠損・破損は空へ倒さず拒否する."""
    meta, _ = _load_video_meta_with_fingerprint(video_id, settings)
    return meta


def _load_video_meta_with_fingerprint(
    video_id: str, settings: Settings
) -> tuple[VideoMeta, str]:
    """元配信のメタデータと raw bytes fingerprint を読む."""
    meta_path = settings.data_dir / video_id / "meta.json"
    if not meta_path.is_file():
        raise DescriptionError(
            "元配信の情報が見つかりません。"
            "ショート用概要欄テンプレートの元配信リンクを使うには、"
            "先に元動画を取り込み直してください。"
        )
    try:
        meta_bytes = meta_path.read_bytes()
        meta = VideoMeta.model_validate_json(meta_bytes)
    except (OSError, UnicodeError, ValidationError) as exc:
        raise DescriptionError(
            "元配信の情報を読み込めませんでした。"
            "ショート用概要欄テンプレートの元配信リンクを使うには、"
            "先に元動画を取り込み直してください。"
        ) from exc
    return meta, _sha256_bytes(meta_bytes)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_shorts_template_bytes(path: Path) -> tuple[bytes, str]:
    try:
        template_bytes = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise DescriptionError(
            "ショート用概要欄テンプレートを読み込めませんでした。"
            f"{path} を確認してください。"
        ) from exc
    return template_bytes, _sha256_bytes(template_bytes)


def _decode_shorts_template(template_bytes: bytes, path: Path) -> str:
    try:
        return template_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise DescriptionError(
            "ショート用概要欄テンプレートを UTF-8 として読み込めませんでした。"
            f"{path} を確認してください。"
        ) from exc


def _missing_template_parts(template: str) -> tuple[str, ...]:
    missing = tuple(
        placeholder
        for placeholder in _SHORTS_PLACEHOLDER_NAMES
        if placeholder not in template
    )
    if not _contains_exact_line(template, SHORTS_DESCRIPTION_CTA):
        return (*missing, "固定 CTA 文")
    return missing


def _format_missing_template_parts(path: Path, missing: tuple[str, ...]) -> str:
    labels = ", ".join(missing)
    return (
        "ショート用概要欄テンプレートに必須項目が不足しています: "
        f"{labels}。{path} を修正してください。"
    )


def _format_missing_description_parts(missing: tuple[str, ...]) -> str:
    return "ショート用概要欄の必須項目が不足しています: " + "、".join(missing)


def _contains_exact_line(text: str, expected: str) -> bool:
    """改行を正規化した本文に期待文言そのものの行があるか確認する."""
    return expected in text.splitlines()


def _count_occurrences(text: str, expected: str) -> int:
    """空文字を Python の特殊な count semantics にしない."""
    return text.count(expected) if expected else 0


def _with_start_seconds(url: str, start_ms: int | None) -> str:
    """元配信 URL へ切り抜き開始秒を t クエリとして付ける."""
    if start_ms is None or start_ms < 1000:
        return url
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "t"
    ]
    query.append(("t", f"{start_ms // 1000}s"))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def validate_shorts_description(
    text: str,
    requirements: ShortsDescriptionRequirements,
) -> tuple[str, ...]:
    """説明文を純粋に検証し、不足・違反箇所を日本語の tuple で返す.

    この関数はファイルを読まず、合成直後と投稿確認後で同じ snapshot を
    そのまま渡して再利用できる。空 tuple は検証成功を表す。
    """
    if not isinstance(text, str):
        return ("最終説明文（文字列）",)
    if not isinstance(requirements, ShortsDescriptionRequirements):
        return ("概要欄要件 snapshot",)

    missing: list[str] = []
    required_items = (
        (
            "生成した説明文",
            requirements.generated_description,
            requirements.generated_description_occurrences,
        ),
        (
            "元動画タイトル",
            requirements.source_title,
            requirements.source_title_occurrences,
        ),
        (
            "開始秒付き元動画 URL",
            requirements.source_url,
            requirements.source_url_occurrences,
        ),
    )
    for label, expected, required_count in required_items:
        if (
            not expected
            or required_count < 1
            or _count_occurrences(text, expected) < required_count
        ):
            missing.append(label)
    actual_cta_line_count = text.splitlines().count(requirements.fixed_cta)
    if (
        not requirements.fixed_cta
        or requirements.fixed_cta_line_occurrences < 1
        or actual_cta_line_count < requirements.fixed_cta_line_occurrences
    ):
        missing.append("チャンネル登録 CTA")

    if _UNRESOLVED_PLACEHOLDER_RE.search(text):
        missing.append("未置換のプレースホルダー")
    if "<" in text or ">" in text:
        missing.append("半角の山カッコ")
    if len(text.encode("utf-8")) > _SHORTS_DESCRIPTION_BYTE_LIMIT:
        missing.append("5000 bytes 制限")
    return tuple(dict.fromkeys(missing))


def _raise_description_validation_error(missing: tuple[str, ...]) -> None:
    if not missing:
        return
    if "半角の山カッコ" in missing:
        raise DescriptionError(
            "ショート用概要欄に半角の山カッコは使えません。"
            "テンプレート、元動画タイトル、最終編集本文を確認してください。"
        )
    if "5000 bytes 制限" in missing:
        raise DescriptionError(
            "ショート用概要欄が UTF-8 で 5000 bytes を超えます。"
            "テンプレートまたは説明文を短くしてください。"
        )
    raise DescriptionError(_format_missing_description_parts(missing))


def require_valid_shorts_description(
    text: str,
    requirements: ShortsDescriptionRequirements,
) -> None:
    """純粋 validator の結果を日本語の DescriptionError に変換する."""
    _raise_description_validation_error(
        validate_shorts_description(text, requirements)
    )


def requirements_from_snapshot(
    snapshot: Mapping[str, object],
) -> ShortsDescriptionRequirements:
    """永続 model の requirements を純粋 validator 用 dataclass へ変換する.

    model 層から service 層への循環 import を避けるため、入力は mapping に
    限定する。固定 CTA や出現回数は dataclass の既存契約で検証する。
    """
    if not isinstance(snapshot, Mapping):
        raise DescriptionError("概要欄要件 snapshot の形式が正しくありません。")
    try:
        return ShortsDescriptionRequirements(**dict(snapshot))
    except (TypeError, ValueError) as exc:
        message = str(exc).strip() or "概要欄要件 snapshot の形式が正しくありません。"
        raise DescriptionError(message) from exc


def validate_shorts_description_snapshot(
    text: str,
    snapshot: Mapping[str, object],
) -> None:
    """mutable な template / meta を読まず、永続 requirements だけで再検証する."""
    requirements = requirements_from_snapshot(snapshot)
    require_valid_shorts_description(text, requirements)


def _build_quality_gated_shorts_description(
    base_description: str,
    *,
    video_id: str,
    start_ms: int | None,
    settings: Settings,
) -> ShortsDescriptionBuild:
    template_path = ensure_shorts_description_template(settings)
    template_bytes, template_fingerprint = _read_shorts_template_bytes(template_path)
    template = _decode_shorts_template(template_bytes, template_path)

    missing_template_parts = _missing_template_parts(template)
    if missing_template_parts:
        raise DescriptionError(
            _format_missing_template_parts(template_path, missing_template_parts)
        )

    meta, meta_fingerprint = _load_video_meta_with_fingerprint(video_id, settings)
    generated_description = base_description.strip()
    source_url = _with_start_seconds(meta.url, start_ms)
    description = template.replace(_DESCRIPTION_PLACEHOLDER, generated_description)
    description = description.replace(_SOURCE_TITLE_PLACEHOLDER, meta.title)
    description = description.replace(_SOURCE_URL_PLACEHOLDER, source_url)
    requirements = ShortsDescriptionRequirements(
        generated_description=generated_description,
        source_title=meta.title,
        source_url=source_url,
        fixed_cta=SHORTS_DESCRIPTION_CTA,
        template_bytes_fingerprint=template_fingerprint,
        meta_json_fingerprint=meta_fingerprint,
        generated_description_occurrences=_count_occurrences(
            description, generated_description
        ),
        source_title_occurrences=_count_occurrences(description, meta.title),
        source_url_occurrences=_count_occurrences(description, source_url),
        fixed_cta_line_occurrences=description.splitlines().count(
            SHORTS_DESCRIPTION_CTA
        ),
    )
    require_valid_shorts_description(description, requirements)
    return ShortsDescriptionBuild(description=description, requirements=requirements)


def build_shorts_description_for_upload(
    base_description: str,
    *,
    video_id: str,
    start_ms: int | None = None,
    settings: Settings | None = None,
) -> ShortsDescriptionBuild:
    """投稿前の必須構成を合成・検証し、本文と不変 requirements を返す.

    P4 の ``build_shorts_description`` とは異なり、投稿ゲートから明示的に
    呼び出す API である。テンプレート未設定時はここで安全な既定値を初回作成
    するため、不完全な説明文を投稿経路へ渡さない。
    """
    settings = settings or get_settings()
    return _build_quality_gated_shorts_description(
        base_description,
        video_id=video_id,
        start_ms=start_ms,
        settings=settings,
    )


def build_shorts_description(
    base_description: str,
    *,
    video_id: str,
    start_ms: int | None = None,
    settings: Settings | None = None,
) -> str:
    """ショート説明文へ元配信リンク等の定型文を合成した本文を返す.

    テンプレート未設定時は base_description をそのまま返す。チャンネル URL は
    専用プレースホルダーを持たず、テンプレート本文へ直接記載する運用とする。
    """
    settings = settings or get_settings()
    template_path = get_shorts_template_path(settings)
    if not template_path.is_file():
        return base_description

    try:
        template = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DescriptionError(
            "ショート用概要欄テンプレートを読み込めませんでした。"
            f"{template_path} を確認してください。"
        ) from exc

    text = template.replace(_DESCRIPTION_PLACEHOLDER, base_description.strip())
    if _SOURCE_TITLE_PLACEHOLDER in text or _SOURCE_URL_PLACEHOLDER in text:
        meta = _load_video_meta(video_id, settings)
        text = text.replace(_SOURCE_TITLE_PLACEHOLDER, meta.title)
        text = text.replace(
            _SOURCE_URL_PLACEHOLDER, _with_start_seconds(meta.url, start_ms)
        )

    if "<" in text or ">" in text:
        raise DescriptionError(
            "ショート用概要欄に半角の山カッコは使えません。"
            f"{template_path} と元配信タイトルを確認してください。"
        )
    if len(text.encode("utf-8")) > _SHORTS_DESCRIPTION_BYTE_LIMIT:
        raise DescriptionError(
            "ショート用概要欄が UTF-8 で 5000 bytes を超えます。"
            f"{template_path} の定型文を短くしてください。"
        )
    return text
