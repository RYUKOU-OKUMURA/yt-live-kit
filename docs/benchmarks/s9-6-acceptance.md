# S9-6 受け入れ証跡

## 判定

status は `fallback_only_human_confirmation_pending`。今回の判定は `no_go` であり、fallback-only のまま人確認待ちとする。

- `phase_complete`: `false`
- `s9_complete`: `false`
- `m16_complete`: `false`
- `ac30`: `false`
- `ac35`: `false`
- `ac37`: `false`
- `ac40`: `false`

canonical evidence の gold は `unverified_provisional` であり、用途は operational transcript reference に限定する。transcript が概ね許容可能であることだけでは Go に不十分である。2026-08-06 の gold 監査 waiver（下記）により gold は判定阻害要因から外れたが、case2 / case3 / case4 の編集後 preview と final short の致命的無発話確認が未完了のため、判定は `no_go`（fallback-only、人確認待ち）のまま維持する。

## gold 監査 waiver（2026-08-06）

fixture exact gold / glossary 個別 exact approval / cue anchor ミリ秒承認について、ユーザーが**明示的 waiver** を承認した。

- 根拠: S9-1 protocol は gate を `fixture_benchmark_quality` namespace（`validate_fixture_benchmark_quality_gate_v1`）と `operational_transcript_reference` namespace（`validate_effective_operational_gate_v1`）へ既に分離しており、fixture exact gold は前者の品質認定にのみ必要で、後者の effective operational Go には不要と機械検証されている。S9-6 はこの既存契約を踏襲する。
- waiver が**主張しないこと**: `s9-1-cases.json` の `gold_audit_status` を `audited` へ変更しない。文字・句読点の exactness、glossary の個別 exact approval、cue anchor の正確なミリ秒は未承認のまま残す。
- `benchmark_quality_gate` は未達のまま維持する。waiver は S9-6 の operational 判定から当該条件を外す明示記録であり、品質認定の付与ではない。

## T1 の扱い

T1-1 は **No-Go / fallback-only** で確定し、T1-2〜T1-5 は着手条件を満たさない。したがって T1-5 の component acceptance evidence は生成されず、S9-6 はそれを参照せずに formal phase acceptance を行う。**AC-40 は S9-6 が Go になった場合でも `[x]` にしない。**

## 統合後の再検証（2026-08-06）

T1-1 ブランチ（`codex/t1-1-timing-spike`、13 コミット）を merge commit `f9aa2b7` で main へ統合した後に再検証した。統合内容は `benchmarks/` / `docs/` / `tests/` のみで `src/` を変更していないため、S9 の production 経路は非回帰である。

- 全件 `uv run pytest`: `1828 passed, 2 skipped`
- focused S9 選択: `253 passed, 1577 deselected`
- `git diff --check`: クリーン
- production hash unchanged: `s9-1-production-hash-after.json` 15/15、`t1-1-production-hash-after.json` 15/15

## benchmark 音声 cache の状態

2026-08-06 時点で `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark` は存在しない。このため音声 fixture を再生成しない限り byte 単位の benchmark 再実行はできない。再現要件は 2026-08-04 の run id `q5-cold-s9-6-repro`、canonical fixture / q5 run manifest fingerprint、`reproduction_metrics.command_argv_by_case` の記録で満たすものとし、記録済み証跡は有効である。

## 人 preview の素材可用性（2026-08-06）

| case | video ID | preview 要否 | 音声付き source | Whisper artifact |
| --- | --- | --- | --- | --- |
| `lb4-clip002-short-proper-nouns` | `LB4px1wRFnY` | 不要 | あり | あり |
| `hpe-audio-variation` | `hPeRSA9YVIM` | 必要 | **なし**（`clips/source/hPeRSA9YVIM.f299.mp4` は video-only で HF4 契約により採用不可） | あり |
| `cgal-proper-nouns` | `CGalA8SISPE` | 必要 | **なし** | **なし** |
| `mkw-long-local-asr` | `mKwn-93gg90` | 必要 | **なし** | **なし** |

case2 / case3 / case4 の編集後 preview には音声付き source の再取得が必要であり、case3 / case4 は選択区間の Whisper artifact 生成も必要である。

## fingerprint

- canonical fixture: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`
- q5 run manifest: `a25d0fbc8233a1db7f0c2ecbb332781b19e5fd5b31260a5b0b2d03be7270de5e`

canonical fixture fingerprint は評価入力を識別し、q5 run manifest fingerprint は q5 turbo の実行条件と run 集合を識別する。2つは意味が異なるため、同一 fingerprint とは主張しない。

## 再現結果

- q5 cold は4ケースすべて終了コード0。
- focused S9 は123件 passed。
- single report は comparator 不足かつ `gold_audit_status=provisional` のため `no_go`。

## 数値

- CER 相対改善率: `78.694`％
- glossary exact match: q5 は `13/19`、VTT は `10/19`
- cue error: q5 は `2.35`、VTT は `6.95`

### reproduction metrics

以下の wall time と peak RSS は canonical の16 case runsではなく、q5 cold-s9-6-repro の4ケース単独再測定である。

- source report path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-s9-6-repro/report.json`
- run id: `q5-cold-s9-6-repro`
- `case_count`: `4`
- `run_kind`: `cold`
- wall time: 最小 `2331 ms`、最大 `5286 ms`
- peak RSS: 最小 `904462336 bytes`、最大 `926924800 bytes`

実 command の argv は JSON の `reproduction_metrics.command_argv_by_case` に、上記 report の `commands[].argv` から転記した。

## canonical evidence

- `q5_model` は `turbo`。
- canonical の run counts は q5 `8` case runs、full turbo `8` case runs、合計 `16` case runs。
- canonical q5 の wall time は最小 `2165 ms`、最大 `5183 ms`。
- canonical q5 の peak RSS は最小 `902742016 bytes`、最大 `933560320 bytes`。
- cold と warm の output SHA は一致。
- production scope は15ファイルで、変更前後に差分なし。
- VTT parity は `4/4`。
- ただし gold は `unverified_provisional` で、operational transcript reference に限定する。

## 4ケースの editorial outcome

| 実 case ID | outcome | 所見 |
| --- | --- | --- |
| `lb4-clip002-short-proper-nouns` | `pass/no additional edit` | 追加編集なし。transcript は概ね許容可能 |
| `hpe-audio-variation` | `opening trim/review` | 冒頭約6秒が無音。opening trim 後の人 preview が必要 |
| `cgal-proper-nouns` | `opening trim/review` | 冒頭約6秒が無音。opening trim 後の人 preview が必要 |
| `mkw-long-local-asr` | `internal gap removal/review` | 冒頭に発話はあるが、約2秒から約26秒は大半が無発話。internal gap removal 後の人 preview が必要 |

親候補の無音は許容するが、final short の無音は許容しない。Whisper timestamp による境界の自動確定は `false` とする。

4本とも文字起こしは概ね問題ない。ただし displayed transcript は operational reference に限定され、exact gold / glossary / cue anchor の承認を意味しない。

## 証跡レベル

### automated

q5 cold 4 cases exit0、focused S9 123 passed、canonical run counts q5=8 / full_turbo=8 / total=16 case runs、cold/warm output SHA 一致、production scope 15ファイルの変更前後不変、VTT parity 4/4 を記録する。wall time と peak RSS は q5 cold-s9-6-repro の4ケース単独再測定として分離する。

### existing_human

auditor は `user`、audit date は `2026-08-03`。

- human audit fingerprint: `9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263`
- boundary audit fingerprint: `0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1`
- `lb4-clip002-short-proper-nouns`: `pass/no additional edit`
- `hpe-audio-variation`: `opening trim/review`
- `cgal-proper-nouns`: `opening trim/review`
- `mkw-long-local-asr`: `internal gap removal/review`

4本とも文字起こしは概ね問題ないが、displayed transcript の operational reference に限定する。exact gold / glossary / cue anchor の承認ではない。

同一 artifact lineage、range-local invalidation、runtime unavailable 時の日本語 fallback は `existing_test_evidence` として S9-4 / S9-5 の既存テスト証跡に分類し、existing_human とは区別する。

### current_ui_pending

今回、実 UI で編集後 preview は未確認である。未完了は次のとおり。

- case2 / case3 の opening trim 後 preview
- case4 の internal gap removal 後 preview
- final short に致命的な無発話がないことの確認

exact gold / glossary / cue anchor 監査は 2026-08-06 の明示 waiver で判定阻害要因から外した。上記 3 件が残るため、AC と S9 の完了状態は更新しない。AC-40 は T1-1 が No-Go / fallback-only であるため、上記 3 件が PASS しても未完了のまま残す。
