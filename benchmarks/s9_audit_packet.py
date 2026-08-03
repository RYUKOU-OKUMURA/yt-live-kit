#!/usr/bin/env python3
"""Generate and verify the S9-1 human-audit packet.

The generator reads the benchmark manifest and the existing audio cache. It
never downloads, rewrites, or deletes benchmark inputs. ``check`` regenerates
the expected Markdown in memory and compares it byte-for-byte with the saved
packet, while also verifying the cached WAV metadata, size, and SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.s9_benchmark import (  # noqa: E402
    BoundaryAuditError,
    evaluate_boundary_audit,
    manifest_fingerprint,
)
from benchmarks.s9_human_audit import HumanAuditError, load_human_audit  # noqa: E402


DEFAULT_MANIFEST = Path("docs/benchmarks/s9-1-cases.json")
DEFAULT_BOUNDARY_AUDIT = Path("docs/benchmarks/s9-1-boundary-audit.json")
DEFAULT_TRANSCRIPT_AUDIT = Path("docs/benchmarks/s9-1-human-audit-v2.json")
DEFAULT_DOCUMENT = Path("docs/benchmarks/s9-1-human-audit.md")
EXPECTED_CASE_COUNT = 4
EXPECTED_AUDIT_STATUS = "unverified_provisional"

AUDIT_HINTS = {
    "lb4-clip002-short-proper-nouns": ("2〜3分", "短い。固有名詞3件と全文の聞き取り"),
    "hpe-audio-variation": ("2〜3分", "音声条件の違い。HHKB / Mac / macOS"),
    "cgal-proper-nouns": ("3〜4分", "固有名詞7件と cue anchor の境界"),
    "mkw-long-local-asr": ("4〜6分", "最長。フィラーと用語5件を含む"),
}


class AuditPacketError(RuntimeError):
    """Raised when the manifest, cache, or generated packet is invalid."""


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuditPacketError(f"manifest を読めません: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AuditPacketError(f"manifest が JSON として不正です: {path}: {exc}") from exc

    if not isinstance(manifest, dict):
        raise AuditPacketError("manifest の root は object である必要があります")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASE_COUNT:
        raise AuditPacketError(
            f"監査対象 case は {EXPECTED_CASE_COUNT} 件必要です: {len(cases) if isinstance(cases, list) else '不明'}"
        )
    if len({case.get("id") for case in cases if isinstance(case, dict)}) != EXPECTED_CASE_COUNT:
        raise AuditPacketError("case ID は4件すべて一意である必要があります")

    cache_root = manifest.get("audio_cache_root")
    if not isinstance(cache_root, str) or not Path(cache_root).is_absolute():
        raise AuditPacketError("audio_cache_root は絶対 path である必要があります")

    for case in cases:
        if not isinstance(case, dict):
            raise AuditPacketError("case は object である必要があります")
        if case.get("gold", {}).get("audit_status") != EXPECTED_AUDIT_STATUS:
            raise AuditPacketError(
                f"{case.get('id', 'unknown')} の gold audit status は {EXPECTED_AUDIT_STATUS} である必要があります"
            )
        if case.get("id") not in AUDIT_HINTS:
            raise AuditPacketError(f"未定義の case ID です: {case.get('id')}")
    return manifest


def load_boundary_audit(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuditPacketError(f"boundary audit を読めません: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AuditPacketError(f"boundary audit が JSON として不正です: {path}: {exc}") from exc
    try:
        result = evaluate_boundary_audit(
            value,
            expected_base_fixture_fingerprint=manifest_fingerprint(manifest),
            expected_benchmark_id=manifest["benchmark_id"],
        )
    except BoundaryAuditError as exc:
        raise AuditPacketError(f"boundary audit の strict schema 検証に失敗しました: {exc.message}") from exc
    result["artifact"] = str(path)
    return result


def load_transcript_audit(
    path: Path,
    manifest: dict[str, Any],
    boundary_audit: dict[str, Any],
) -> dict[str, Any]:
    try:
        result = load_human_audit(
            path,
            expected_base_fixture_fingerprint=manifest_fingerprint(manifest),
            expected_boundary_audit_fingerprint=boundary_audit["fingerprint"],
            expected_benchmark_id=manifest["benchmark_id"],
        )
    except HumanAuditError as exc:
        raise AuditPacketError(f"transcript audit の strict schema 検証に失敗しました: {exc.message}") from exc
    result["artifact"] = str(path)
    return result


def format_source_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_elapsed(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_range(start_ms: int, end_ms: int) -> str:
    return f"{start_ms}–{end_ms} ms"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AuditPacketError(f"audio cache を読めません: {path}: {exc}") from exc
    return digest.hexdigest()


def audio_path(manifest: dict[str, Any], case: dict[str, Any]) -> Path:
    fixture = case.get("audio_fixture")
    if not isinstance(fixture, str) or not fixture or Path(fixture).name != fixture:
        raise AuditPacketError(f"audio_fixture は単一ファイル名である必要があります: {case.get('id')}")
    return Path(manifest["audio_cache_root"]) / fixture


def inspect_audio(manifest: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    path = audio_path(manifest, case)
    if not path.is_file():
        raise AuditPacketError(f"audio cache がありません: {path}")

    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
    except (OSError, wave.Error) as exc:
        raise AuditPacketError(f"WAV として読めません: {path}: {exc}") from exc

    if channels != 1 or sample_rate != 16_000 or sample_width != 2:
        raise AuditPacketError(
            f"16-bit 16kHz mono WAV ではありません: {path} "
            f"channels={channels}, rate={sample_rate}, width={sample_width}"
        )

    actual_bytes = path.stat().st_size
    expected_bytes = case.get("audio_bytes")
    if actual_bytes != expected_bytes:
        raise AuditPacketError(
            f"audio bytes 不一致: {case['id']} expected={expected_bytes}, actual={actual_bytes}"
        )

    actual_sha256 = sha256_file(path)
    expected_sha256 = case.get("audio_sha256")
    if actual_sha256 != expected_sha256:
        raise AuditPacketError(
            f"audio SHA-256 不一致: {case['id']} expected={expected_sha256}, actual={actual_sha256}"
        )

    return {
        "path": str(path),
        "bytes": actual_bytes,
        "sha256": actual_sha256,
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
    }


def validate_case_shape(case: dict[str, Any]) -> tuple[int, int, str, list[str], list[list[int]], str]:
    case_id = case["id"]
    range_ms = case.get("range_ms")
    if not isinstance(range_ms, list) or len(range_ms) != 2:
        raise AuditPacketError(f"{case_id} の range_ms は start/end の2要素が必要です")
    start_ms, end_ms = range_ms
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or not start_ms < end_ms:
        raise AuditPacketError(f"{case_id} の range_ms が不正です: {range_ms}")

    gold = case.get("gold")
    if not isinstance(gold, dict):
        raise AuditPacketError(f"{case_id} の gold がありません")
    text = gold.get("text")
    glossary = gold.get("glossary")
    anchors = gold.get("cue_anchors_ms")
    if not isinstance(text, str) or not text:
        raise AuditPacketError(f"{case_id} の gold.text が空です")
    if "```" in text or "\n" in text or "\r" in text:
        raise AuditPacketError(f"{case_id} の gold.text は単一行の fenced block に収まる必要があります")
    if not isinstance(glossary, list) or not glossary or not all(isinstance(term, str) for term in glossary):
        raise AuditPacketError(f"{case_id} の gold.glossary が不正です")
    if not isinstance(anchors, list) or not anchors:
        raise AuditPacketError(f"{case_id} の gold.cue_anchors_ms が空です")
    for anchor in anchors:
        if (
            not isinstance(anchor, list)
            or len(anchor) != 2
            or not all(isinstance(value, int) for value in anchor)
            or not anchor[0] < anchor[1]
        ):
            raise AuditPacketError(f"{case_id} の cue anchor が不正です: {anchor}")
    return start_ms, end_ms, text, glossary, anchors, case_id


def render_packet(
    manifest: dict[str, Any],
    audio_infos: dict[str, dict[str, Any]],
    boundary_audit: dict[str, Any],
    transcript_audit: dict[str, Any],
) -> str:
    cases = manifest["cases"]
    validated = [validate_case_shape(case) for case in cases]
    ordered_cases = sorted(cases, key=lambda case: case["range_ms"][1] - case["range_ms"][0])
    cases_by_id = {case["case_id"]: case for case in boundary_audit["cases"]}
    transcript_cases_by_id = {case["case_id"]: case for case in transcript_audit["cases"]}
    total_ms = sum(case["range_ms"][1] - case["range_ms"][0] for case in cases)

    lines = [
        "# S9-1 人手音声監査パケット",
        "",
        "監査状態: **transcript content の operational reference 確認済み**。exact gold ではありません。",
        "採用モデル: このパケット自体では決めません。固定 gate と比較 report の決定を参照してください。",
        "benchmark ID: `" + str(manifest.get("benchmark_id", "不明")) + "`",
        "human audit fingerprint: `" + transcript_audit["audit_fingerprint"] + "`",
        "",
        "この文書は、2026-08-03 のユーザー自然文監査を構造化した canonical packet です。追加の定型フォーマット入力は要求しません。固定4音声、表示 transcript、監査範囲、exact と境界の未承認事項を同じ証跡へまとめています。",
        "",
        "## ユーザー原文と監査範囲",
        "",
        f"- 原文: `{transcript_audit['source']['exact_quote']}`",
        f"- 確認 context: {transcript_audit['source']['review_context']}",
        "- displayed transcript content: human reviewed / no material issue reported / accepted as operational benchmark reference",
        "- glossary: not explicitly audited。個別用語の exact approval には昇格しません。",
        "- character / punctuation exactness: not claimed。",
        "- cue anchor exact milliseconds: unapproved。",
        "- boundary/editorial outcomes: 既存の partial boundary audit を保持します。",
        "- boundary auto adoption: prohibited。human boundary review: required。",
        "",
        "## 推奨確認順と所要時間",
        "",
        f"対象は4 case、音声 span の合計は `{format_elapsed(total_ms)}`（約7.5分）です。短いものから確認し、再生・巻き戻し・返答記入を含めた監査の目安は約11〜16分です。",
        "",
        "| 順 | case ID | video ID | 絶対 range | 音声 span | 確認目安 |",
        "|---:|---|---|---|---:|---|",
    ]
    for index, case in enumerate(ordered_cases, 1):
        start_ms, end_ms = case["range_ms"]
        hint = AUDIT_HINTS[case["id"]]
        lines.append(
            f"| {index} | `{case['id']}` | `{case['video_id']}` | "
            f"`{format_range(start_ms, end_ms)}` "
            f"（{format_source_timestamp(start_ms)}〜{format_source_timestamp(end_ms)}） | "
            f"{format_elapsed(end_ms - start_ms)} | {hint[0]}: {hint[1]} |"
        )

    lines.extend(
        [
            "",
            "## 音声ファイル絶対 path 一覧",
            "",
            "以下の4本だけを監査対象にします。いずれも cache 内の 16,000 Hz・mono・16-bit PCM WAV です。",
            "",
        ]
    )
    for case in ordered_cases:
        info = audio_infos[case["id"]]
        lines.append(f"- `{case['id']}`: `{info['path']}`")

    lines.extend(
        [
            "",
            "## 監査方法",
            "",
            "今回の自然文監査は、4本の表示 transcript content に対する operational reference の確認として記録済みです。別の定型フォーマットを再入力しません。",
            "「概ね問題なし」は文字単位・句読点単位の完全一致、glossary の個別 exact approval、cue anchor の正確なミリ秒を意味しません。",
            "固定 fixture の範囲、音声 bytes / SHA-256、model identity、numeric gate は変更せず、境界の partial audit は独立 artifact として保持します。",
            "",
            "## 境界・発話連続性の部分監査（2026-08-03）",
            "",
            f"監査者: `{boundary_audit['auditor']}` / 監査日: `{boundary_audit['audit_date']}`",
            f"boundary audit fingerprint: `{boundary_audit['fingerprint']}`",
            f"base fixture fingerprint: `{boundary_audit['base_fixture_fingerprint']}`",
            "",
            "これはユーザーが4本の固定音声を聴いた、開始境界・発話連続性だけの部分監査です。transcript 全文、glossary、cue anchor の正確な時刻は audited にしません。固定音声 span、video ID、range、bytes、SHA-256 は変更していません。",
            "",
            "### 前回表示順と所見",
            "",
            "| 前回表示順 | case ID | 自然文の所見 | expected editorial outcome |",
            "|---:|---|---|---|",
            *(
                f"| {entry['display_order']} | `{cases_by_id[entry['case_id']]['case_id']}` | {cases_by_id[entry['case_id']]['source_feedback']} | `{cases_by_id[entry['case_id']]['expected_editorial_outcome']}` |"
                for entry in boundary_audit["previous_display_order"]
            ),
            "",
            "### 部分監査の判定契約",
            "",
            "- 背景音があっても意味ある発話がなければ、編集上は無発話として扱う。",
            "- case 1 の `pass` は、今回確認した境界・発話連続性で追加処置なしという意味だけであり、transcript 全文、glossary、cue anchor、最終 short の品質承認ではない。",
            "- 約6秒、約2〜26秒という所見は観察メモであり、production の普遍的な秒数閾値ではない。",
            "- 開始直後に一言あれば通る単純な onset-only gate は使わない。",
            "- Whisper timestamp を唯一の境界正本にせず、audio activity、cue、padding、human preview を併用する。",
            "- 親候補の固定音声 span を良好と判定することと、最終 short cutplan から冒頭の無発話・長い内部無発話を残さないことは別判定である。",
            "",
            "S9-1 の採否はこの packet だけでなく、同じ fixture を再計測した canonical comparison report の全 effective gate と tie-break で決めます。境界自動化はこの packet から採用しません。",
            "",
            "## 音声ファイルの hash と size",
            "",
            "| case ID | bytes | SHA-256 |",
            "|---|---:|---|",
        ]
    )
    for case in ordered_cases:
        info = audio_infos[case["id"]]
        lines.append(f"| `{case['id']}` | {info['bytes']} | `{info['sha256']}` |")

    for number, case in enumerate(cases, 1):
        start_ms, end_ms, text, glossary, anchors, case_id = validate_case_shape(case)
        info = audio_infos[case_id]
        lines.extend(
            [
                "",
                f"## Fixture case {number}: `{case_id}`",
                "",
                "### 固定入力",
                "",
                f"- video ID: `{case['video_id']}`",
                f"- absolute range: `{format_range(start_ms, end_ms)}`",
                f"- source time: `{format_source_timestamp(start_ms)}〜{format_source_timestamp(end_ms)}`",
                f"- candidate: `{case.get('candidate_id', '不明')}`",
                f"- audio path: `{info['path']}`",
                f"- audio format: `16,000 Hz / mono / 16-bit PCM WAV`",
                f"- bytes: `{info['bytes']}`",
                f"- SHA-256: `{info['sha256']}`",
                "",
                "### Displayed transcript content の operational reference",
                "",
                "cases.json の `gold.text` を表示 reference として転記した値です。ユーザーはこの case を含む4本について「4本とも文字起こしは概ね問題なし」と述べました。exact transcript とは主張しません。",
                "",
                f"- human review status: `{transcript_cases_by_id[case_id]['displayed_transcript_content']['status']}`",
                f"- acceptance: `{transcript_cases_by_id[case_id]['displayed_transcript_content']['acceptance']}`",
                "",
                "```text",
                text,
                "```",
                "",
                "### Glossary（個別 exact audit ではない）",
                "",
                "| glossary label | fixed reference term | human audit status |",
                "|---|---|---|",
            ]
        )
        for term_index, term in enumerate(glossary, 1):
            lines.append(f"| `glossary-{term_index}` | `{term}` | not_explicitly_audited |")

        lines.extend(
            [
                "",
                "### Character / punctuation exactness",
                "",
                "`not_claimed`。自然文の「概ね問題なし」を文字単位・句読点単位の exact approval へ昇格しません。",
                "",
                "### Cue anchor（正確なミリ秒の監査ではない）",
                "",
                "ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。",
                "",
                "| anchor label | 絶対 range | source time | human audit status |",
                "|---|---|---|---|",
            ]
        )
        for anchor_index, (anchor_start, anchor_end) in enumerate(anchors, 1):
            lines.append(
                f"| `anchor-{anchor_index}` | `{format_range(anchor_start, anchor_end)}` | "
                f"`{format_source_timestamp(anchor_start)}〜{format_source_timestamp(anchor_end)}` | unapproved |"
            )
        lines.extend(
            [
                "",
                "### Boundary/editorial dimension",
                "",
                f"`{transcript_cases_by_id[case_id]['boundary_editorial']['status']}`。開始境界・発話連続性の所見は [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) のまま保持します。境界の自動採用はせず human review を必須にします。",
            ]
        )

    lines.extend(
        [
            "",
            "## 今回の監査記録と次手順",
            "",
            "1. ユーザー原文を改変せず、固定4 caseへ statement scope と表示順を対応付けました。",
            "2. displayed transcript content の operational reference と、glossary / character / punctuation / cue / boundary の状態を別 dimension に保存しました。",
            "3. `s9-1-cases.json` の gold status、音声 path、bytes、SHA-256、video ID、absolute range は変更していません。",
            "4. 同じ cold / warm 手順で q5 / turbo を再測定し、numeric gate、operational reference gate、tie-break を canonical report へ固定します。",
            "5. A で Go になっても boundary の自動採用はせず、人の preview と区間確認を downstream の必須条件として維持します。",
            "",
            "## 関連証跡",
            "",
            "- [`s9-1-cases.json`](./s9-1-cases.json): 固定 fixture と provisional gold の正本",
            "- [`s9-1-human-audit-v2.json`](./s9-1-human-audit-v2.json): 自然文監査の strict artifact と fingerprint",
            "- [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json): 境界・発話連続性だけの strict audit artifact",
            "- [`s9-1-protocol.md`](./s9-1-protocol.md): 同じ評価契約・gate・再現手順",
            "- [`s9-1-report.md`](./s9-1-report.md): operational transcript reference の canonical decision（q5採用）。exact dimension は未承認、boundary automation は不採用",
            "- [`s9-1-report.json`](./s9-1-report.json): 機械可読な現在の gate status",
            "",
        ]
    )

    document = "\n".join(lines)
    if "<" in document or ">" in document:
        raise AuditPacketError("生成文書に半角の山カッコが含まれています")
    if "<audio" in document.lower() or "<video" in document.lower():
        raise AuditPacketError("生成文書に HTML audio / video tag が含まれています")
    return document


def build_packet(
    manifest_path: Path,
    boundary_audit_path: Path = DEFAULT_BOUNDARY_AUDIT,
    transcript_audit_path: Path = DEFAULT_TRANSCRIPT_AUDIT,
) -> str:
    manifest = load_manifest(manifest_path)
    boundary_audit = load_boundary_audit(boundary_audit_path, manifest)
    transcript_audit = load_transcript_audit(transcript_audit_path, manifest, boundary_audit)
    audio_infos = {
        case["id"]: inspect_audio(manifest, case) for case in manifest["cases"]
    }
    return render_packet(manifest, audio_infos, boundary_audit, transcript_audit)


def generate(
    manifest_path: Path,
    document_path: Path,
    boundary_audit_path: Path = DEFAULT_BOUNDARY_AUDIT,
    transcript_audit_path: Path = DEFAULT_TRANSCRIPT_AUDIT,
) -> None:
    document = build_packet(manifest_path, boundary_audit_path, transcript_audit_path)
    try:
        document_path.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise AuditPacketError(f"監査パケットを書けません: {document_path}: {exc}") from exc
    print(f"generated: {document_path}")


def check(
    manifest_path: Path,
    document_path: Path,
    boundary_audit_path: Path = DEFAULT_BOUNDARY_AUDIT,
    transcript_audit_path: Path = DEFAULT_TRANSCRIPT_AUDIT,
) -> None:
    expected = build_packet(manifest_path, boundary_audit_path, transcript_audit_path)
    try:
        actual = document_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditPacketError(f"監査パケットを読めません: {document_path}: {exc}") from exc
    if actual != expected:
        raise AuditPacketError(
            f"監査パケットが manifest から再生成した内容と一致しません: {document_path}"
        )
    print(f"ok: {document_path} matches manifest and audio cache")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "check"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        subparser.add_argument("--boundary-audit", type=Path, required=True)
        subparser.add_argument("--transcript-audit", type=Path, default=DEFAULT_TRANSCRIPT_AUDIT)
        subparser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "generate":
            generate(args.manifest, args.document, args.boundary_audit, args.transcript_audit)
        else:
            check(args.manifest, args.document, args.boundary_audit, args.transcript_audit)
    except AuditPacketError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
