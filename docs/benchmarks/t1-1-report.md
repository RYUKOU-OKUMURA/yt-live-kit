# T1-1 候補測定報告書

- 生成日時: 2026-08-05T12:55:12.331926+00:00
- 結論: **No-Go (fallback-only)**
- token_alignment 全 gate PASS: False

## 群別 gate 判定（token_alignment）

| 群 | coverage | median | p90 | max | bias | wrong moves | PASS |
|---|---:|---:|---:|---:|---:|---:|:---:|
| long_single_cue | 0.90 | 545.0 | 1635.0 | 2310 | -25.0 | 4 | FAIL |
| multi_cross_cue | 0.79 | 340 | 1608.0 | 1900 | -210 | 6 | FAIL |
| pooled | 0.84 | 370 | 1640.0 | 2310 | -120 | 10 | FAIL |

## 候補比較（pooled）

| 候補 | coverage | median | p90 | max | bias | wrong moves | gate |
|---|---:|---:|---:|---:|---:|---:|:---:|
| current | 0.95 | 1630.0 | 4000.0 | 8000 | -90.0 | 26 | FAIL |
| segment_snap | 0.16 | 1400 | 4576.000000000003 | 8440 | -1400 | 4 | FAIL |
| token_alignment | 0.84 | 370 | 1640.0 | 2310 | -120 | 10 | FAIL |

## 低信頼・fallback 非回帰

- 低信頼行数: 39
- holdout 4 件（token_alignment）: [{'row_id': 't1-multi-021', 'low_confidence': False, 'draft_preserved': True, 'silent_move': False, 'reasons': []}, {'row_id': 't1-multi-022', 'low_confidence': False, 'draft_preserved': True, 'silent_move': False, 'reasons': []}, {'row_id': 't1-multi-023', 'low_confidence': False, 'draft_preserved': False, 'silent_move': False, 'reasons': []}, {'row_id': 't1-multi-024', 'low_confidence': True, 'draft_preserved': True, 'silent_move': False, 'reasons': ['monotonicity_violation']}]
- fallback 非回帰: {'automatic_moves': 0, 'silent_moves': 0, 'denominator': 20, 'evaluated_with_gold': 19, 'fail_closed_rows': ['t1-fallback-008']}

## t1-fallback-008 fail-closed 記録

- t1-fallback-008: 対象発話が bound audio 内で確認できず、onset を入力しない fail-closed 記録
  - 分母 20 のうち gold 評価 19 + fail-closed 1

## 診断（CER 等）

- gza_415-cut_001-audio: CER=0.5000, glossary=0, segments={'segment_count': 1, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- gza_415-cut_002-audio: CER=1.1600, glossary=0, segments={'segment_count': 4, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- gza_415-cut_003-audio: CER=1.0714, glossary=0, segments={'segment_count': 1, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- gza_f0-cut_002-audio: CER=0.8906, glossary=0, segments={'segment_count': 2, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- hpe_8ad-cut_003-audio: CER=1.1852, glossary=0, segments={'segment_count': 2, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- gza_f0-cut_001-audio: CER=1.2586, glossary=0, segments={'segment_count': 3, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- hpe_8ad-cut_001-audio: CER=0.5385, glossary=0, segments={'segment_count': 2, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}
- hpe_8ad-cut_002-audio: CER=0.2258, glossary=0, segments={'segment_count': 6, 'overlap_count': 0, 'out_of_order_count': 0, 'issues': []}

## 運用定義

- match_ratio_threshold: 0.6
- wrong_line_or_cross_cue_moves: confident 予測のうち (a) |error| が 1000 ms を超えるもの、または (b) 単調性違反で別 line 位置へ一致したもの
- wrong_move_counting_note: 集計は保守的で、low-confidence として draft 時刻を維持した単調性違反行、および時刻を移動しない current baseline の draft 誤差超過も件数に含める。No-Go 判定はこの件数に依存せず、median / p90 / max gate 単独でも成立する
- low_confidence_policy: flag + draft 時刻維持。coverage 分子に入れない
- validity_policy: owning range clamp・時系列・非重複・最低表示 500ms を満たせない confident 予測は fallback 扱い
- go_no_go_rule: token_alignment が全 gate を満たす場合のみ Go
- gold_measurement_limit: 人手 gold の 45/63 行は 1000ms 単位入力であり、これ以上細かい誤差は測定限界となる

## 再現手順

```
uv run python benchmarks/t1/candidate_measurement.py run --manifest benchmarks/t1/manifest.json --packet /tmp/yt-live-kit-t1-1-human-gold.json --timing-evidence docs/benchmarks/t1-1-timing-inputs.json --timing-dir /tmp/yt-live-kit-t1-timing --report-json docs/benchmarks/t1-1-report.json --report-md docs/benchmarks/t1-1-report.md
```
