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

| case | video ID | preview 要否 | 音声付き source | case 区間の Whisper artifact |
| --- | --- | --- | --- | --- |
| `lb4-clip002-short-proper-nouns` | `LB4px1wRFnY` | 不要 | あり（502,876,028 bytes） | あり |
| `hpe-audio-variation` | `hPeRSA9YVIM` | **必要** | あり（1,528,294,692 bytes、2026-08-04 取得済み） | **なし**（既存 artifact は 3498000–3638000 ms のみで case 区間 8640000–8730000 ms を含まない） |
| `cgal-proper-nouns` | `CGalA8SISPE` | 今回は対象外 | なし | なし |
| `mkw-long-local-asr` | `mKwn-93gg90` | **必要** | あり（435,794,499 bytes、2026-08-06 に `ensure_source_video` で取得、45.2 秒） | **なし**（`transcripts/artifacts` 自体が未作成） |

初回記録で `hpe-audio-variation` を「音声付き source なし」としたのは誤りで、ファイル一覧の出力切り詰めによる誤読だった。実際には 2026-08-04 時点で音声付き `hPeRSA9YVIM.mp4` が存在しており、上表が再確認後の事実である。今回の取得は `mKwn-93gg90` の 1 本だけである。

## 人 preview の実施範囲（2026-08-06、ユーザー決定）

- 対象: `hpe-audio-variation`（opening trim）と `mkw-long-local-asr`（internal gap removal）の 2 本
- 対象外: `cgal-proper-nouns`
- 根拠: editorial outcome の種類を網羅する最小構成である。`cgal-proper-nouns` は `hpe-audio-variation` と同じ `opening_trim_required_then_human_preview`（いずれも冒頭約 6 秒の無音）であり、個別 preview を実施しないことを明記した上で case2 の結果を援用する。

## 音声活動の実測（2026-08-06）

境界監査 policy が `whisper_timestamp_sole_authority: false` とし、`required_evidence` に `audio_activity` を含めているため、トリム位置は Whisper timestamp ではなく音声活動の実測で決めた。

`hpe-audio-variation` の高精度 artifact は先頭 cue が range 開始と同値（8640000 ms）で、しかも 12 秒の無発話をまたいで 1 cue に併合していた。Whisper timestamp を境界の根拠にできないことは T1-1 の No-Go と整合する。

| case | 実測 | 監査所見 | 差 |
| --- | --- | --- | --- |
| `hpe-audio-variation` | 0–1.194s 無音 → 1.194–4.115s 断続音 → **4.115–16.131s に 12.0 秒の無発話** → 16.131s 以降は連続発話 | 開始から約 6 秒まで意味ある発話がない | 方向は一致するが、持続的な発話開始は実測 **16.1 秒**で、監査の「約 6 秒」は無発話の長さを過小評価していた |
| `mkw-long-local-asr` | 0–2.356s 発話 → **2.356–18.584s に 16.2 秒の無発話** → 20.1–25.9s も断続 → 34.961–50.535s に 15.6 秒 → 53.328–58.266s に 4.9 秒 | 冒頭に発話はあるが、約 2 秒から約 26 秒は大半が無発話 | 一致 |

これに基づき決めた区間は次のとおりである。

- `hpe-audio-variation`（opening trim）: **02:24:16 → 02:24:33**（17.0 秒）。開始は持続発話の起点、終了は 17.499s から始まる 5.3 秒の無発話の手前。
- `mkw-long-local-asr`（internal gap removal）: **00:19:06 → 00:19:14** と **00:19:39 → 00:19:55**（合計 24.0 秒）。16.2 秒・15.6 秒・4.9 秒の無発話を除去した 2 区間の連結。

## 実 UI ラインの進捗（2026-08-06）

`http://127.0.0.1:8502` の実アプリを Playwright で操作し、両 case を **工程 3／6（テロップ確認）** まで進めた。

| case | 候補 | 高精度字幕 | artifact fingerprint | テロップ |
| --- | --- | --- | --- | --- |
| `hpe-audio-variation` | `clip_003` | 固定済み | `30ee4756d26f581fb313b4c5f5c02b900a1c95930f580236b2a3c9db57e15098` | 初回は schema 不一致で失敗し、構造化日本語 error と再試行 UI が出て fail-closed で停止。再生成で成功し、自動ハード判定 通過 / 自動警告 なし |
| `mkw-long-local-asr` | `clip_001` | 固定済み | `bedb198a442282ad9c5f39b0e2ed664368f15b22edb15565e4ad55488f6fcf09` | 初回で成功。自動ハード判定 通過 / 自動警告 なし |

**「台本全体の誤字・固有名詞を確認した」チェックボックスは意図的に押していない。** これは人が行う確認ゲートであり、agent が代行すると人確認の事実を偽装することになるためである。

残る人の作業は次の 3 つである。

1. 両 case で台本の誤字・固有名詞を確認してチェックする
2. 「台本を確定して生成へ」で生成する
3. 工程 6 の最終確認で、冒頭に無発話がないこと・連結部が不自然でないことを preview で確認する

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
