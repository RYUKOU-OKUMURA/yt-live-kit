from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = ROOT / "docs/benchmarks/s9-6-acceptance.json"
ACCEPTANCE_MARKDOWN_PATH = ROOT / "docs/benchmarks/s9-6-acceptance.md"
S9_1_REPORT_PATH = ROOT / "docs/benchmarks/s9-1-report.json"
EXECUTION_PLAN_PATH = ROOT / "docs/execution-plan-v3.md"

CANONICAL_FIXTURE_FINGERPRINT = (
    "6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718"
)
Q5_RUN_MANIFEST_FINGERPRINT = (
    "a25d0fbc8233a1db7f0c2ecbb332781b19e5fd5b31260a5b0b2d03be7270de5e"
)
Q5_MODEL_PATH = (
    "/Users/ryukouokumura/Library/Caches/whisper.cpp/models/"
    "ggml-large-v3-turbo-q5_0.bin"
)
REPRODUCTION_RUN_DIRECTORY = "q5/cold-s9-6-repro"
CASE_IDS = (
    "lb4-clip002-short-proper-nouns",
    "hpe-audio-variation",
    "cgal-proper-nouns",
    "mkw-long-local-asr",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_acceptance_decision_is_fail_closed() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)

    assert evidence["task_id"] == "S9-6"
    assert evidence["status"] == "fallback_only_human_confirmation_pending"
    assert {
        key: evidence[key]
        for key in (
            "phase_complete",
            "s9_complete",
            "m16_complete",
            "ac30",
            "ac35",
            "ac37",
        )
    } == {
        "phase_complete": False,
        "s9_complete": False,
        "m16_complete": False,
        "ac30": False,
        "ac35": False,
        "ac37": False,
    }

    decision = evidence["decision"]
    assert decision["verdict"] == "no_go"
    assert decision["self_approval"] is False
    assert decision["reason"] == [
        "single report は comparator不足かつ gold_audit_status=provisional のため no_go",
        "canonical evidence の gold は unverified_provisional で、operational transcript reference に限定される",
        "case2/3/4 の編集後 preview と final short の致命的無発話確認が未完了",
        "transcripts が概ね許容可能でも、それだけでは Go に不十分",
        "Whisper timestamp による境界の自動確定は false",
    ]


def test_fingerprints_are_complete_distinct_and_linked_to_s9_1() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)
    report = _load_json(S9_1_REPORT_PATH)

    assert evidence["canonical_fixture_fingerprint"] == CANONICAL_FIXTURE_FINGERPRINT
    assert evidence["q5_run_manifest_fingerprint"] == Q5_RUN_MANIFEST_FINGERPRINT
    assert evidence["canonical_fixture_fingerprint"] != evidence[
        "q5_run_manifest_fingerprint"
    ]
    assert evidence["fingerprint_semantics"]["same_fingerprint_claim"] is False
    assert (
        report["evaluation_contract"]["raw_report_identity"][
            "source_fixture_fingerprint"
        ]
        == CANONICAL_FIXTURE_FINGERPRINT
    )
    assert (
        report["evaluation_contract"]["raw_report_identity"][
            "run_manifest_fingerprints"
        ]["ggml-large-v3-turbo-q5_0"]
        == Q5_RUN_MANIFEST_FINGERPRINT
    )


def test_reproduction_metrics_have_four_case_cold_commands_and_values() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)
    reproduction = evidence["reproduction_metrics"]

    assert reproduction["source_report_path"] == (
        "/Users/ryukouokumura/Library/Caches/yt-live-kit/"
        "s9-benchmark/runs/q5/cold-s9-6-repro/report.json"
    )
    assert reproduction["run_id"] == "q5-cold-s9-6-repro"
    assert reproduction["run_directory"] == REPRODUCTION_RUN_DIRECTORY
    assert reproduction["case_count"] == 4
    assert reproduction["run_kind"] == "cold"
    assert reproduction["wall_time_ms"] == {"min": 2331, "max": 5286}
    assert reproduction["peak_rss_bytes"] == {
        "min": 904462336,
        "max": 926924800,
    }

    commands = reproduction["command_argv_by_case"]
    assert set(commands) == set(CASE_IDS)
    durations_ms = {
        "lb4-clip002-short-proper-nouns": "56840",
        "hpe-audio-variation": "90000",
        "cgal-proper-nouns": "120000",
        "mkw-long-local-asr": "180000",
    }
    for case_id in CASE_IDS:
        argv = commands[case_id]
        assert isinstance(argv, list)
        assert argv[:3] == ["/usr/bin/time", "-l", "/opt/homebrew/bin/whisper-cli"]
        assert _flag_value(argv, "--model") == Q5_MODEL_PATH
        assert _flag_value(argv, "--file").startswith("/")
        assert _flag_value(argv, "--file").endswith(".wav")
        assert _flag_value(argv, "--language") == "ja"
        assert "--output-json" in argv
        assert "--output-json-full" in argv
        assert _flag_value(argv, "--output-file") == (
            f"/Users/ryukouokumura/Library/Caches/yt-live-kit/"
            f"s9-benchmark/runs/{REPRODUCTION_RUN_DIRECTORY}/whisper/{case_id}/cold"
        )
        assert _flag_value(argv, "--offset-t") == "0"
        assert _flag_value(argv, "--duration") == durations_ms[case_id]
        assert _flag_value(argv, "--beam-size") == "5"
        assert _flag_value(argv, "--best-of") == "5"
        assert _flag_value(argv, "--temperature") == "0"
        assert _flag_value(argv, "--threads") == "8"


def test_metrics_and_canonical_collections_are_separately_evidenced() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)
    report = _load_json(S9_1_REPORT_PATH)

    assert evidence["metrics"] == {
        "cer_relative_improvement_percent": 78.694,
        "glossary_exact_match": {
            "q5": {"matched": 13, "total": 19},
            "vtt": {"matched": 10, "total": 19},
        },
        "cue_error": {"q5": 2.35, "vtt": 6.95},
    }

    canonical = evidence["canonical_evidence"]
    assert canonical["run_counts"] == {
        "q5": 8,
        "full_turbo": 8,
        "total": 16,
        "unit": "case_runs",
    }
    assert canonical["gold_audit_status"] == "unverified_provisional"
    assert canonical["production_scope"] == {
        "file_count": 15,
        "before_after_unchanged": True,
    }
    assert canonical["vtt_parity"] == {"matched": 4, "total": 4}

    run_directories = report["evaluation_contract"]["candidate_output_identity"][
        "run_kind_directories"
    ]
    canonical_collections = {
        f"q5/{run_directories['cold']}",
        f"q5/{run_directories['warm']}",
    }
    assert canonical_collections == {"q5/cold-audit-apply", "q5/warm-audit-apply"}
    assert evidence["reproduction_metrics"]["run_directory"] not in canonical_collections
    assert evidence["reproduction_metrics"]["run_directory"] != "q5/cold-audit-apply"


def test_human_and_existing_test_evidence_are_not_misclassified() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)
    human = evidence["human_evidence"]["existing_human"]

    assert human["auditor"] == "user"
    assert human["audit_date"] == "2026-08-03"
    assert human["human_audit_fingerprint"] == (
        "9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263"
    )
    assert human["boundary_audit_fingerprint"] == (
        "0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1"
    )
    assert tuple(item["case_id"] for item in human["case_observations"]) == CASE_IDS
    assert tuple(item["observation"] for item in human["case_observations"]) == (
        "pass/no additional edit",
        "opening trim/review",
        "opening trim/review",
        "internal gap removal/review",
    )
    serialized_human = json.dumps(human, ensure_ascii=False)
    assert "S9-4" not in serialized_human
    assert "S9-5" not in serialized_human

    existing_tests = evidence["existing_test_evidence"]
    assert existing_tests["classification"] == "existing_test_evidence"
    assert existing_tests["source"] == "S9-4/S9-5"
    assert existing_tests["items"] == [
        "同一artifact lineage",
        "range-local invalidation",
        "runtime unavailable時の日本語fallback",
    ]
    assert existing_tests["current_ui_edit_after_preview_verified"] is False


def test_pending_and_case_outcomes_keep_human_gates_explicit() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)

    assert evidence["human_evidence"]["current_ui_pending"] == [
        "case2/3のopening trim後preview",
        "case4のinternal gap removal後preview",
        "final shortに致命的な無発話がないことの確認",
    ]
    assert evidence["pending"] == [
        "case2/3 opening trim後preview",
        "case4 internal gap removal後preview",
        "final shortに致命的無発話なし確認",
        "exact gold/glossary/cue anchor監査、またはS9-6で採用可能な明示的waiver",
    ]

    assert tuple(item["case_id"] for item in evidence["case_outcomes"]) == CASE_IDS
    assert tuple(
        (
            item["editorial_outcome"],
            item["human_observation"],
            item["transcript_assessment"],
        )
        for item in evidence["case_outcomes"]
    ) == (
        ("no_additional_edit", "pass/no additional edit", "broadly_acceptable"),
        (
            "opening_trim_required_then_human_preview",
            "opening trim/review",
            "broadly_acceptable",
        ),
        (
            "opening_trim_required_then_human_preview",
            "opening trim/review",
            "broadly_acceptable",
        ),
        (
            "internal_gap_removal_required_then_human_preview",
            "internal gap removal/review",
            "broadly_acceptable",
        ),
    )

    assert evidence["acceptance_rules"] == {
        "parent_candidate_silence": "allowed",
        "final_short_silence": "not_allowed",
        "whisper_timestamp_auto_boundary_confirmation": False,
        "transcripts_broadly_acceptable_alone_sufficient_for_go": False,
    }
    assert evidence["transcript_scope"]["exact_gold_glossary_cue_anchor_approval"] is False


def test_json_and_markdown_repeat_the_same_major_values_without_angles() -> None:
    evidence = _load_json(ACCEPTANCE_PATH)
    raw_json = ACCEPTANCE_PATH.read_text(encoding="utf-8")
    markdown = ACCEPTANCE_MARKDOWN_PATH.read_text(encoding="utf-8")

    assert "<" not in raw_json
    assert ">" not in raw_json
    assert "<" not in markdown
    assert ">" not in markdown

    assert f"status は `{evidence['status']}`" in markdown
    for key in (
        "phase_complete",
        "s9_complete",
        "m16_complete",
        "ac30",
        "ac35",
        "ac37",
    ):
        assert f"- `{key}`: `false`" in markdown

    assert f"- canonical fixture: `{evidence['canonical_fixture_fingerprint']}`" in markdown
    assert f"- q5 run manifest: `{evidence['q5_run_manifest_fingerprint']}`" in markdown
    assert "- CER 相対改善率: `78.694`％" in markdown
    assert "- glossary exact match: q5 は `13/19`、VTT は `10/19`" in markdown
    assert "- cue error: q5 は `2.35`、VTT は `6.95`" in markdown

    reproduction = evidence["reproduction_metrics"]
    assert f"- source report path: `{reproduction['source_report_path']}`" in markdown
    assert f"- run id: `{reproduction['run_id']}`" in markdown
    assert f"- `case_count`: `{reproduction['case_count']}`" in markdown
    assert f"- `run_kind`: `{reproduction['run_kind']}`" in markdown
    assert "- wall time: 最小 `2331 ms`、最大 `5286 ms`" in markdown
    assert "- peak RSS: 最小 `904462336 bytes`、最大 `926924800 bytes`" in markdown

    counts = evidence["canonical_evidence"]["run_counts"]
    assert (
        f"canonical の run counts は q5 `{counts['q5']}` case runs、"
        f"full turbo `{counts['full_turbo']}` case runs、合計 `{counts['total']}` case runs。"
    ) in markdown
    assert "production scope は15ファイルで、変更前後に差分なし。" in markdown
    assert "VTT parity は `4/4`。" in markdown
    assert "gold は `unverified_provisional`" in markdown

    for item in evidence["human_evidence"]["existing_human"]["case_observations"]:
        assert f"- `{item['case_id']}`: `{item['observation']}`" in markdown
    assert "case2 / case3 の opening trim 後 preview" in markdown
    assert "case4 の internal gap removal 後 preview" in markdown
    assert "final short に致命的な無発話がないことの確認" in markdown
    assert "親候補の無音は許容するが、final short の無音は許容しない。" in markdown
    assert "Whisper timestamp による境界の自動確定は `false` とする。" in markdown
    assert "transcript が概ね許容可能であることだけでは Go に不十分" in markdown


def test_execution_plan_keeps_s9_acceptance_unfinished_and_records_next_checks() -> None:
    plan = EXECUTION_PLAN_PATH.read_text(encoding="utf-8")
    start = plan.index("#### S9-6: A/B 受け入れ・回帰・フェーズ判定")
    end = plan.index("### P6:", start)
    s9_6_section = plan[start:end]

    assert "| S9-6 | A/B 受け入れ・回帰・フェーズ判定 | [~] 進行中 |" in plan
    assert "| S9 | 選択親候補区間のローカル Whisper 精査（実装） | [~] 進行中 |" in plan
    assert "| M16 | 親候補探索は VTT、選択区間は provenance 付き Whisper artifact で精査できる | [ ] |" in plan
    for index in range(1, 7):
        assert f"- [ ] S9-6-{index}." in s9_6_section
        assert f"- [x] S9-6-{index}." not in s9_6_section

    assert "2026-08-04 main の `3d113ef` / `071929d` 統合" in s9_6_section
    assert "初回レビューで P1 二点を指摘し、follow-up で APPROVE" in s9_6_section
    assert "main focused S9 は123件 passed" in s9_6_section
    assert "fallback-only のまま人 UI 確認待ち" in s9_6_section
    assert "case2 / case3 の opening trim 後 preview" in s9_6_section
    assert "case4 の internal gap removal 後 preview" in s9_6_section
    assert "final short に無発話がないことの確認" in s9_6_section
    assert "exact gold / glossary / cue anchor 監査または明示的 waiver" in s9_6_section
    assert "AC-30 / AC-35 / AC-37 の受け入れ判定は未完了" in s9_6_section
