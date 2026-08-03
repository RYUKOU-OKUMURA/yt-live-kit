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


DEFAULT_MANIFEST = Path("docs/benchmarks/s9-1-cases.json")
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


def render_packet(manifest: dict[str, Any], audio_infos: dict[str, dict[str, Any]]) -> str:
    cases = manifest["cases"]
    validated = [validate_case_shape(case) for case in cases]
    ordered_cases = sorted(cases, key=lambda case: case["range_ms"][1] - case["range_ms"][0])
    total_ms = sum(case["range_ms"][1] - case["range_ms"][0] for case in cases)

    lines = [
        "# S9-1 人手音声監査パケット",
        "",
        "監査状態: **未完了**。gold は全4 case とも `unverified_provisional` です。",
        "採用モデル: **未決定**。現行の S9-1 は No-Go のままです。",
        "benchmark ID: `" + str(manifest.get("benchmark_id", "不明")) + "`",
        "",
        "この文書は、4つの固定音声 span を人が直接聴いて provisional gold を承認または訂正するための準備物です。ユーザーの返答前に、gold を監査済みとして扱ったり、S9-1 の Done、進捗、採用モデル判定を変更したりしません。",
        "",
        "## 先に読む注意",
        "",
        "- 音声を実際に聴かず承認しないでください。",
        "- AI出力同士の比較だけでは監査完了にしないでください。",
        "- 4 case 全件が必要です。1件でも未回答、保留、訂正未確定があれば人手監査完了にしません。",
        "- provisional gold は既存 transcript、VTT、ASS、cutplan の文脈から作った仮値です。音声を聴く前の正解ではありません。",
        "- viewer greeting やフィラーを、音声確認なしに自動で削除・除外しないでください。",
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
            "1. 上の順番で音声ファイルを1本ずつ最後まで聴きます。macOS では次の形式で再生できます。",
            "   `afplay \"音声ファイルの絶対 path\"`",
            "2. case ごとの provisional gold transcript と照合し、聞こえない、足りない、順序が違う、固有名詞が違う箇所を訂正文にします。",
            "3. glossary は表記を一語ずつ確認します。音声で判断できない場合は承認せず、保留または訂正として返します。",
            "4. cue anchor は `anchor-1` からのラベルと絶対時刻を確認します。時刻またはラベルが違う場合は anchor ID と訂正値を返します。",
            "5. 下の最小フォーマットを case ごとに1回ずつ、合計4回返します。",
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
                f"## Case {number}: `{case_id}`",
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
                "### Provisional gold transcript",
                "",
                "cases.json の `gold.text` を機械的に転記した値です。音声監査前の provisional です。",
                "",
                "```text",
                text,
                "```",
                "",
                "### Glossary",
                "",
                "| glossary label | provisional expected | 人手確認 |",
                "|---|---|---|",
            ]
        )
        for term_index, term in enumerate(glossary, 1):
            lines.append(f"| `glossary-{term_index}` | `{term}` | 承認 / 訂正 |")

        lines.extend(
            [
                "",
                "### Cue anchor",
                "",
                "ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。",
                "",
                "| anchor label | 絶対 range | source time | 人手確認 |",
                "|---|---|---|---|",
            ]
        )
        for anchor_index, (anchor_start, anchor_end) in enumerate(anchors, 1):
            lines.append(
                f"| `anchor-{anchor_index}` | `{format_range(anchor_start, anchor_end)}` | "
                f"`{format_source_timestamp(anchor_start)}〜{format_source_timestamp(anchor_end)}` | 承認 / 訂正 |"
            )

    lines.extend(
        [
            "",
            "## ユーザー返答の最小フォーマット",
            "",
            "以下を case ごとに4回返してください。承認の場合は `承認`、訂正の場合は全文または訂正対象が特定できる値を書いてください。",
            "",
            "```text",
            "case ID: lb4-clip002-short-proper-nouns",
            "transcript: 承認 / 訂正文",
            "glossary: 承認 / 訂正",
            "cue anchor: 承認 / 訂正",
            "監査者:",
            "監査日: YYYY-MM-DD",
            "```",
            "",
            "訂正時は、transcript は訂正後の全文、glossary は用語ごとの期待表記、cue anchor は `anchor-ID: 絶対 range / ラベル` の形式で返してください。4 case 全件について transcript、glossary、cue anchor の3項目を埋めてください。",
            "",
            "## 監査後の次手順",
            "",
            "1. ユーザーの4 case 分の返答を受け取り、監査者・監査日と、承認または訂正の根拠を記録します。返答前に `audit_status` を変更しません。",
            "2. 訂正があれば `s9-1-cases.json` の gold.text、gold.glossary、gold.cue_anchors_ms へ人手の結果だけを反映します。音声 path、bytes、SHA-256、video ID、absolute range は固定したままにします。",
            "3. 4 case 全件の監査がそろった後、fixture fingerprint を再計算し、同じ4音声の hash / size と protocol の normalization、cue rule、wall time、memory gate が変わっていないことを確認します。",
            "4. [`s9-1-protocol.md`](./s9-1-protocol.md) の同じ cold / warm 手順で、固定した4 case と候補2モデルを再測定します。gold だけを監査結果へ更新し、音声 span や評価 gate を都合よく変更しません。",
            "5. paired median CER 相対改善、glossary exact match 非悪化、cue 欠落・重複率、cold / warm wall time、peak memory、gold audit 必須条件を同じ gate で判定します。",
            "6. 全 gate を満たした場合だけ採用モデルと設定を決めます。未達なら No-Go とし、既存 YouTube VTT の fallback-only を維持します。人手監査済みでも自動的に Go にはしません。",
            "",
            "再測定と採用判定が完了するまで、このパケット自体は S9-1 の Done 証跡ではなく、監査の準備済み証跡として扱います。",
            "",
            "## 関連証跡",
            "",
            "- [`s9-1-cases.json`](./s9-1-cases.json): 固定 fixture と provisional gold の正本",
            "- [`s9-1-protocol.md`](./s9-1-protocol.md): 同じ評価契約・gate・再現手順",
            "- [`s9-1-report.md`](./s9-1-report.md): 現在の provisional 指標と No-Go",
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


def build_packet(manifest_path: Path) -> str:
    manifest = load_manifest(manifest_path)
    audio_infos = {
        case["id"]: inspect_audio(manifest, case) for case in manifest["cases"]
    }
    return render_packet(manifest, audio_infos)


def generate(manifest_path: Path, document_path: Path) -> None:
    document = build_packet(manifest_path)
    try:
        document_path.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise AuditPacketError(f"監査パケットを書けません: {document_path}: {exc}") from exc
    print(f"generated: {document_path}")


def check(manifest_path: Path, document_path: Path) -> None:
    expected = build_packet(manifest_path)
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
        subparser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "generate":
            generate(args.manifest, args.document)
        else:
            check(args.manifest, args.document)
    except AuditPacketError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
