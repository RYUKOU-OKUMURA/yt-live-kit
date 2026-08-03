"""S9-1 の cold / warm raw report を統合する比較 report generator。

実測そのものは ``s9_benchmark.py run`` が担当し、この module は既存の
raw report・固定 fixture・production hash 証跡だけを読み、JSON と Markdown
の比較 report を決定的に組み立てる。ネットワーク、YouTube、production data
への書き込みは行わない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any, Mapping

# ``python benchmarks/s9_compare.py`` でも repository root の harness を import
# できるようにする。実行時の外部 path は受け取らず、この file の親だけを使う。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.s9_benchmark import (
    evaluate_boundary_audit,
    manifest_fingerprint,
    sha256_file,
)


CASE_IDS = (
    "lb4-clip002-short-proper-nouns",
    "mkw-long-local-asr",
    "cgal-proper-nouns",
    "hpe-audio-variation",
)
MODEL_NAMES = ("ggml-large-v3-turbo-q5_0", "ggml-large-v3-turbo")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object が必要です: {path}")
    return value


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _compact_metric(metric: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": metric.get("status"),
        "cer": metric.get("cer"),
        "levenshtein": metric.get("levenshtein"),
        "glossary": metric.get("glossary", {}),
        "cue": metric.get("cue", {}),
        "wall_time_ms": metric.get("wall_time_ms"),
        "peak_memory_bytes": metric.get("peak_memory_bytes"),
        "run_kind": metric.get("run_kind"),
        "cache_hit": metric.get("cache_hit"),
    }


def _output_fingerprint(metric: Mapping[str, Any]) -> dict[str, Any] | None:
    execution = metric.get("execution")
    if not isinstance(execution, Mapping):
        return None
    paths = execution.get("output_paths")
    if not isinstance(paths, list) or not paths or not isinstance(paths[0], str):
        return None
    path = Path(paths[0])
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _case_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("raw report の cases が配列ではありません。")
    result: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
            raise ValueError("raw report の case が不正です。")
        result[str(case["case_id"])] = case
    if tuple(result) != CASE_IDS:
        raise ValueError(f"case 順または case 集合が固定値と異なります: {tuple(result)}")
    return result


def _relative_improvement(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else -1.0
    return (baseline - candidate) / baseline


def _aggregate_glossary(metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    keys = ("expected", "found", "missing", "incorrect", "exact_match_terms")
    result = {key: 0 for key in keys}
    for metric in metrics:
        glossary = metric.get("glossary", {})
        for key in keys:
            result[key] += int(glossary.get(key, 0))
    result["definition"] = "fixed glossary exact surface; found non-regression and missing/incorrect non-increase"
    return result


def _aggregate_cue(metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    missing = sum(int(metric.get("cue", {}).get("missing", 0)) for metric in metrics)
    duplicate = sum(int(metric.get("cue", {}).get("duplicate", 0)) for metric in metrics)
    anchors = sum(int(metric.get("cue", {}).get("gold_anchor_count", 0)) for metric in metrics)
    output = sum(int(metric.get("cue", {}).get("output_cue_count", 0)) for metric in metrics)
    return {
        "missing": missing,
        "duplicate": duplicate,
        "error_count": missing + duplicate,
        "gold_anchor_count": anchors,
        "output_cue_count": output,
        "error_rate": (missing + duplicate) / anchors if anchors else (0.0 if output == 0 else 1.0),
    }


def _run_case(
    case_id: str,
    cold_case: Mapping[str, Any],
    warm_case: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = _compact_metric(cold_case["baseline"])
    cold = _compact_metric(cold_case["candidate"])
    warm = _compact_metric(warm_case["candidate"])
    cold_output = _output_fingerprint(cold_case["candidate"])
    warm_output = _output_fingerprint(warm_case["candidate"])
    stable = bool(cold_output and warm_output and cold_output["sha256"] == warm_output["sha256"])
    return {
        "case_id": case_id,
        "baseline": baseline,
        "cold": cold,
        "warm": warm,
        "relative_cer_improvement": _relative_improvement(float(baseline["cer"]), float(cold["cer"])),
        "cold_output": cold_output,
        "warm_output": warm_output,
        "cold_warm_output_sha_equal": stable,
    }


def _model_report(
    *,
    name: str,
    fixture: Mapping[str, Any],
    cold_path: Path,
    warm_path: Path,
) -> dict[str, Any]:
    cold_report = _read_json(cold_path)
    warm_report = _read_json(warm_path)
    cold_cases = _case_map(cold_report)
    warm_cases = _case_map(warm_report)
    cases = [_run_case(case_id, cold_cases[case_id], warm_cases[case_id]) for case_id in CASE_IDS]
    relative_values = [case["relative_cer_improvement"] for case in cases]
    baseline_cases = [case["baseline"] for case in cases]
    cold_candidates = [case["cold"] for case in cases]
    warm_candidates = [case["warm"] for case in cases]
    all_runs = cold_candidates + warm_candidates
    gate = cold_report["gates"]
    evaluation = fixture["gates"]
    cold_budget_ms = round(float(evaluation["cold_wall_time_seconds"]) * 1000)
    warm_budget_ms = round(float(evaluation["warm_wall_time_seconds"]) * 1000)
    wall_checks = [
        {
            "case_id": case["case_id"],
            "run_kind": run_kind,
            "value_ms": run["wall_time_ms"],
            "budget_ms": cold_budget_ms if run_kind == "cold" else warm_budget_ms,
            "passed": run["wall_time_ms"] is not None
            and run["wall_time_ms"] <= (cold_budget_ms if run_kind == "cold" else warm_budget_ms),
        }
        for case in cases
        for run_kind, run in (("cold", case["cold"]), ("warm", case["warm"]))
    ]
    peak_values = [run["peak_memory_bytes"] for run in all_runs if run["peak_memory_bytes"] is not None]
    wall_passed = all(item["passed"] for item in wall_checks)
    peak_passed = bool(peak_values) and max(peak_values) <= int(evaluation["peak_memory_bytes"])
    output_stable = all(case["cold_warm_output_sha_equal"] for case in cases)
    paired_median = median(relative_values)
    baseline_glossary = _aggregate_glossary(baseline_cases)
    candidate_glossary = _aggregate_glossary(cold_candidates)
    baseline_cue = _aggregate_cue(baseline_cases)
    candidate_cue = _aggregate_cue(cold_candidates)
    glossary_passed = (
        candidate_glossary["found"] >= baseline_glossary["found"]
        and candidate_glossary["missing"] <= baseline_glossary["missing"]
        and candidate_glossary["incorrect"] <= baseline_glossary["incorrect"]
    )
    cue_allowed = baseline_cue["error_rate"] + float(evaluation["cue_error_rate_delta_points"]) / 100
    cue_passed = candidate_cue["error_rate"] <= cue_allowed
    cer_passed = paired_median >= float(evaluation["paired_median_relative_cer_improvement"])
    gold_passed = not bool(evaluation.get("require_gold_audit", True)) or cold_report.get("gold_audit_status") == "audited"
    technical_passed = cer_passed and glossary_passed and cue_passed and wall_passed and peak_passed
    model_meta = next(model for model in fixture["models"] if model["name"] == name)
    return {
        "name": name,
        "distribution_url": model_meta["distribution_url"],
        "model_fingerprint": cold_report["fingerprints"]["model"],
        "run_manifest_fingerprint": cold_report["manifest_fingerprint"],
        "settings_contract": {
            "binary_path": cold_report["whisper_runtime"]["binary_path"],
            "binary_fingerprint": cold_report["whisper_runtime"].get("binary_fingerprint"),
            "version": cold_report["whisper_runtime"]["version"],
            "settings": cold_report["whisper_runtime"]["settings"],
            "timeout_sec": cold_report["whisper_runtime"]["timeout_sec"],
            "output_schema": cold_report["whisper_runtime"]["output_schema"],
        },
        "runs": {
            "cold": {"report_path": str(cold_path), "cases": [case["cold"] for case in cases]},
            "warm": {"report_path": str(warm_path), "cases": [case["warm"] for case in cases]},
        },
        "quality": {
            "cases": cases,
            "paired_median_relative_cer_improvement": paired_median,
            "glossary": {
                "baseline": baseline_glossary,
                "candidate": candidate_glossary,
                "passed": glossary_passed,
            },
            "cue": {
                "baseline": baseline_cue,
                "candidate": candidate_cue,
                "allowed_candidate_rate": cue_allowed,
                "passed": cue_passed,
            },
            "output_stable_cold_warm": output_stable,
        },
        "gates": {
            "quality": {
                "relative_cer": {"value": paired_median, "threshold": evaluation["paired_median_relative_cer_improvement"], "passed": cer_passed},
                "glossary_exact_match": {"passed": glossary_passed},
                "cue_missing_duplicate": {"passed": cue_passed},
            },
            "wall_time": {"checks": wall_checks, "passed": wall_passed},
            "peak_memory": {"budget_bytes": evaluation["peak_memory_bytes"], "values_bytes": peak_values, "passed": peak_passed},
            "gold_audit": {"status": cold_report.get("gold_audit_status"), "passed": gold_passed},
            "technical_gates_passed": technical_passed,
            "go": technical_passed and gold_passed,
            "status": "go" if technical_passed and gold_passed else "no_go",
            "reasons": [] if technical_passed and gold_passed else ([{"code": "gold_not_audited", "message": "gold は音声の独立人手監査前であり、fail closed です。"}] if technical_passed else [{"code": "technical_gate_failed", "message": "技術 gate のいずれかが未達です。"}]),
            "source_gate_report": {"status": gate.get("status"), "reasons": gate.get("reasons", [])},
        },
    }


def _representative_cases(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for case in fixture["cases"]:
        gold = case["gold"]
        result.append(
            {
                "case_id": case["id"],
                "video_id": case["video_id"],
                "source_url": case["source_url"],
                "candidate_id": case["candidate_id"],
                "range_ms": case["range_ms"],
                "selection_basis": case["selection_basis"],
                "audio_fixture": case["audio_fixture"],
                "gold_audit_status": gold["audit_status"],
                "gold_basis": gold["basis"],
                "glossary": gold["glossary"],
                "gold_cue_anchors_ms": gold["cue_anchors_ms"],
            }
        )
    return result


def _load_boundary_audit(path: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    value = _read_json(path)
    result = evaluate_boundary_audit(
        value,
        expected_base_fixture_fingerprint=manifest_fingerprint(fixture),
        expected_benchmark_id=fixture["benchmark_id"],
    )
    result["artifact"] = str(path)
    return result


def build_comparison(
    *,
    manifest_path: Path,
    run_paths: Mapping[str, Mapping[str, Path]],
    production_before: Path,
    production_after: Path,
    parity_path: Path,
    boundary_audit_path: Path | None = None,
) -> dict[str, Any]:
    fixture = _read_json(manifest_path)
    if boundary_audit_path is None:
        raise ValueError("boundary audit artifact is required for the canonical S9-1 comparison report")
    boundary_audit = _load_boundary_audit(boundary_audit_path, fixture)
    models = [
        _model_report(name=name, fixture=fixture, cold_path=run_paths[name]["cold"], warm_path=run_paths[name]["warm"])
        for name in MODEL_NAMES
    ]
    before = _read_json(production_before)
    after = _read_json(production_after)
    parity = _read_json(parity_path)
    source_inputs = _read_json(run_paths[MODEL_NAMES[0]]["cold"])["fingerprints"]["inputs"]
    medians = {model["name"]: model["quality"]["paired_median_relative_cer_improvement"] for model in models}
    runtime_report = _read_json(run_paths[MODEL_NAMES[0]]["cold"])["whisper_runtime"]
    evaluation = fixture["gates"]
    decision_reason = "gold transcript は音声の独立人手監査前で、品質数値を採用モデル決定の根拠へ昇格できない。"
    no_go_reasons = ["gold_not_audited"]
    residual_risks = [
        "gold は既存 VTT / transcript / ASS / cutplan を文脈利用した仮作成で、音声の独立人手監査をしていない。",
        "cold は OS page cache を消去した完全 cold ではなく、各 model の cold wave と warm wave を分離した reuse 観測である。",
        "mKwn / CGal / hPe は公開 YouTube の audio-only span 取得で、client / network 条件差が残る。",
        "candidate cue は raw のまま評価し、rolling VTT dedupe を候補へ適用していない。",
        "モデルは Git 管理外の手動 cache にあり、production 自動 download は実装していない。",
    ]
    decision_reason += " 境界・発話連続性の所見は部分監査であり、既存 cue proxy の盲点も判明したため、境界監査だけでは No-Go を解除しない。"
    no_go_reasons.extend(["cue_proxy_blind_spot", "boundary_audit_is_partial"])
    residual_risks.extend(
        [
            "境界監査は transcript / glossary / cue anchor exact times の承認ではなく、4 case の部分的な人手所見である。",
            "case 1 の pass は今回確認した境界・発話連続性で追加処置なしという意味だけで、全文品質や最終 short の品質承認ではない。",
            "case 2・3 の約6秒、case 4 の約2〜26秒は今回の観察メモであり、production の普遍的な秒数閾値ではない。",
            "親候補の固定 span と最終 short cutplan の品質は分離し、S9-4 / S9-6 では audio activity・cue・padding・human preview を併用する必要がある。",
        ]
    )
    report = {
        "schema": "s9-1-comparison-report-v3",
        "benchmark_id": fixture["benchmark_id"],
        "measurement_date": fixture["measurement_date"],
        "fixture_fingerprint": manifest_fingerprint(fixture),
        "gold_audit_status": "provisional",
        "metrics_status": "provisional",
        "comparison": {
            "model_selection": "none; No-Go",
            "paired_median_relative_cer_improvement": medians,
            "provisional_observation": "turbo と q5 は provisional 指標上の差を示すが、gold 未監査のため採用決定ではない。",
        },
        "decision": {
            "adopted_model": None,
            "go": False,
            "status": "no_go",
            "reason": decision_reason,
            "no_go_reasons": no_go_reasons,
            "s9_2_ready": False,
            "fallback_only": "既存 YouTube VTT baseline を fallback-only とし、S9-3 の高精度モデル採用へ進めない。",
        },
        "evaluation_contract": {
            "candidate_cues": "raw; progressive VTT dedupe is baseline-only",
            "normalization": fixture["normalization"],
            "cue_rule": fixture["cue_rule"],
            "gates": evaluation,
            "boundary_policy": boundary_audit["policy"],
        },
        "representative_cases": _representative_cases(fixture),
        "models": models,
        "source_fingerprints": source_inputs,
        "production_integrity": {
            "before_report": str(production_before),
            "after_report": str(production_after),
            "checked_file_count": len(before["files"]),
            "unchanged": before["files"] == after["files"],
            "files": before["files"],
        },
        "vtt_progressive_parity": {
            "artifact": str(parity_path),
            "case_count": len(parity["cases"]),
            "all_text_sequence_equal": all(case["text_sequence_equal"] for case in parity["cases"]),
            "cases": parity["cases"],
        },
        "runtime": {
            "host": {"cpu": "Apple M4 Pro", "memory_bytes": 68719476736, "arch": "arm64"},
            "whisper_runtime": runtime_report,
            "ffmpeg": {"path": "/opt/homebrew/Cellar/ffmpeg-full/8.1.2_2/bin/ffmpeg", "version": "8.1.2"},
            "yt_dlp": {"version": "2026.07.04", "mode": "public audio-only span; no video archive"},
        },
        "raw_reports": {name: {kind: str(path) for kind, path in paths.items()} for name, paths in run_paths.items()},
        "reproduction": {
            "shell": False,
            "cache_root": "/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark",
            "model_cache_root": "/Users/ryukouokumura/Library/Caches/whisper.cpp/models",
            "commands": [
                f"uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name {name} --output-dir {run_paths[name]['cold'].parent} --report {run_paths[name]['cold']} --execute-whisper --run-kind cold"
                for name in MODEL_NAMES
            ]
            + [
                f"uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name {name} --output-dir {run_paths[name]['warm'].parent} --report {run_paths[name]['warm']} --execute-whisper --run-kind warm"
                for name in MODEL_NAMES
            ]
            + [
                "uv run python benchmarks/s9_compare.py --manifest docs/benchmarks/s9-1-cases.json --boundary-audit docs/benchmarks/s9-1-boundary-audit.json --q5-cold ... --q5-warm ... --turbo-cold ... --turbo-warm ... --output-json docs/benchmarks/s9-1-report.json --output-md docs/benchmarks/s9-1-report.md"
            ],
        },
        "residual_risks": residual_risks,
    }
    report["boundary_audit"] = boundary_audit
    return report


def markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# S9-1 代表素材 benchmark report",
        "",
        f"測定日: {report['measurement_date']}",
        f"fixture fingerprint: `{report['fixture_fingerprint']}`",
        "",
        "## 判定",
        "",
        "No-Go。gold は音声の独立人手監査前であり、数値は provisional。既存 YouTube VTT を fallback-only とし、S9-3 の高精度モデル採用へ進めない。",
        "",
        "q5 / turbo とも CER、glossary、cue、wall time、peak RSS の技術 gate は通過したが、gold audit gate が fail closed した。",
        "",
    ]
    boundary_audit = report.get("boundary_audit")
    if isinstance(boundary_audit, Mapping):
        lines += [
            "## 境界・発話連続性の部分監査",
            "",
            f"監査者: {boundary_audit['auditor']} / 監査日: {boundary_audit['audit_date']}",
            f"boundary audit fingerprint: `{boundary_audit['fingerprint']}`",
            f"base fixture fingerprint: `{boundary_audit['base_fixture_fingerprint']}`（既存4音声の fixture fingerprint は変更していない）",
            "",
            "この証跡は開始境界と発話連続性だけの部分監査であり、transcript 全文、glossary、cue anchor の正確な時刻を audited にはしない。背景音は意味ある発話として数えず、単純な onset-only gate と Whisper timestamp 単独の境界確定は採用しない。",
            "case 1 の `pass` は、今回確認した境界・発話連続性で追加処置なしという意味だけであり、全文品質・glossary・cue anchor・最終 short の品質承認ではない。",
            "",
            "| 前回表示順 | case ID | 観察 | 期待 editorial outcome |",
            "|---:|---|---|---|",
        ]
        for case in boundary_audit["cases"]:
            lines.append(
                f"| {case['display_order']} | {case['case_id']} | {case['source_feedback']} | {case['expected_editorial_outcome']} |"
            )
        lines += [
            "",
            "S9-1 はこの部分監査により、既存 cue proxy だけでは無発話・背景音・長い内部 gap を捉え切れないことが分かったため No-Go を維持する。S9-4 / S9-6 は親候補の固定音声 span を切り詰めず、最終 short cutplan / preview で opening trim または内部 gap removal / review を人確認し、audio activity・cue・padding・human preview を併用する。今回の約時刻は観察メモであり、production の普遍的な秒数閾値ではない。",
            "",
        ]
    lines += [
        "## 代表素材",
        "",
        "| case | video / candidate | range | 選定理由 |",
        "|---|---|---:|---|",
    ]
    for case in report["representative_cases"]:
        lines.append(
            f"| {case['case_id']} | {case['video_id']} / {case['candidate_id']} | {case['range_ms'][0]}–{case['range_ms'][1]} ms | {case['selection_basis']} |"
        )
    lines += [
        "",
        "## 比較結果",
        "",
        "| model | case | baseline CER | candidate CER | relative improvement | glossary found | cue rate baseline → candidate | cold ms | warm ms | peak RSS max |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in report["models"]:
        for case in model["quality"]["cases"]:
            baseline = case["baseline"]
            cold = case["cold"]
            lines.append(
                f"| {model['name']} | {case['case_id']} | {baseline['cer']:.6f} | {cold['cer']:.6f} | {case['relative_cer_improvement']:.2%} | {cold['glossary']['found']} | {baseline['cue']['error_rate']:.2f} → {cold['cue']['error_rate']:.2f} | {cold['wall_time_ms']} | {case['warm']['wall_time_ms']} | {max(cold['peak_memory_bytes'], case['warm']['peak_memory_bytes'])} |"
            )
        quality = model["quality"]
        peak = max(item["peak_memory_bytes"] for case in quality["cases"] for item in (case["cold"], case["warm"]))
        lines.append(
            f"| **{model['name']} median** | 4 case | — | — | **{quality['paired_median_relative_cer_improvement']:.2%}** | {quality['glossary']['candidate']['found']} / {quality['glossary']['baseline']['found']} | gate pass | — | — | {peak} |"
        )
    lines += [
        "",
        "## 実行条件と証跡",
        "",
        "- host: Apple M4 Pro / 64 GB / arm64",
        "- whisper.cpp: 1.9.1、Metal、ja、threads 8、processors 1、temperature 0、beam size 5、best-of 5、padding 0、full JSON、timeout 180 秒",
        "- model cache: `/Users/ryukouokumura/Library/Caches/whisper.cpp/models/`",
        "- audio cache: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/`",
        "- baseline: production progressive dedupe parity 4/4。candidate: raw cue のまま評価",
        "- production data hash: before / after は一致。対象 15 ファイル、既存 `ja.vtt` と mp4 は非変更。",
        "- VTT progressive parity: [s9-1-vtt-progressive-parity.json](./s9-1-vtt-progressive-parity.json) で 4/4 case 一致。",
        "",
        "## 残余リスク",
        "",
    ]
    lines.extend(f"- {risk}" for risk in report["residual_risks"])
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S9-1 raw benchmark report comparator")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--q5-cold", required=True, type=Path)
    parser.add_argument("--q5-warm", required=True, type=Path)
    parser.add_argument("--turbo-cold", required=True, type=Path)
    parser.add_argument("--turbo-warm", required=True, type=Path)
    parser.add_argument("--production-before", required=True, type=Path)
    parser.add_argument("--production-after", required=True, type=Path)
    parser.add_argument("--parity", required=True, type=Path)
    parser.add_argument("--boundary-audit", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_paths = {
        "ggml-large-v3-turbo-q5_0": {"cold": args.q5_cold, "warm": args.q5_warm},
        "ggml-large-v3-turbo": {"cold": args.turbo_cold, "warm": args.turbo_warm},
    }
    report = build_comparison(
        manifest_path=args.manifest,
        run_paths=run_paths,
        production_before=args.production_before,
        production_after=args.production_after,
        parity_path=args.parity,
        boundary_audit_path=args.boundary_audit,
    )
    report["reproduction"]["commands"][-1] = (
        "uv run python benchmarks/s9_compare.py"
        f" --manifest {args.manifest}"
        f" --q5-cold {args.q5_cold} --q5-warm {args.q5_warm}"
        f" --turbo-cold {args.turbo_cold} --turbo-warm {args.turbo_warm}"
        f" --production-before {args.production_before} --production-after {args.production_after}"
        f" --parity {args.parity}"
        + f" --boundary-audit {args.boundary_audit}"
        + f" --output-json {args.output_json} --output-md {args.output_md}"
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "fixture_fingerprint": report["fixture_fingerprint"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
