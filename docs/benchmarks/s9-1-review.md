# S9-1 defect-first review（暫定）

## 判定

P1 が 2 件、P2 が 2 件。現状の No-Go 自体は誇張ではない。gold は `provisional` のまま fail-closed され、モデル採用を決めていない点は protocol と一致する。

確認した final2 report は、指定された literal path `runs/*/final2/report.json` ではなく、既存の `q5/cold-final2`、`q5/warm-final2`、`turbo/cold-final2`、`turbo/warm-final2` の 4 report。

## Findings

### P1 — marker と視聴者挨拶の除外設定が実行時に落ちている

- `docs/benchmarks/s9-1-cases.json:7-12` は `normalization.exclude_text_tokens` に `[音楽]` などを宣言する。
- しかし `benchmarks/s9_benchmark.py:1801-1813` はその field を `cue_rule` へ移さず、`cue_rule.exclude_text_tokens` だけを読む。
- 実際の q5 final2 report の `cue_inclusion_rule.exclude_text_tokens` は空配列で、`generate_report` も `2044-2047` と `2140-2143` で空配列を渡す。
- その結果、mkw の candidate raw 出力には `[鼻息]`、`[笑い]` と「カスさんこんばんは」「いつもありがとうございます」等が残っている。`include_viewer_greeting: false` も実装された除外規則になっていない。

CER と candidate raw の cue gate が、protocol が除外すると宣言したノイズ込みで算出されている。fixture field を一箇所へ正しく写像し、視聴者挨拶の判定規則を明示したうえで、report の実効設定と回帰テストを追加すべき。

### P1 — wall-time gate と runner timeout の単位が不一致

- protocol / fixture は cold 180 秒、warm 120 秒を宣言する。
- `benchmarks/s9_benchmark.py:1815-1825` で gate 用 budget は millisecond に変換されるが、`1851` は同じ millisecond 値の `max(budgets)` を `timeout_sec` の既定値に使う。
- final2 report の `whisper_runtime.timeout_sec` は `180000.0`。一方 gate の budget は `180000 ms` と記録されている。

したがって wall gate は 180 秒でも、実プロセスは timeout まで 180000 秒待ち得る。秒と millisecond を分離し、run kind ごとの timeout を seconds で渡すべき。

### P2 — cue anchor の契約と実装が違う

- `docs/benchmarks/s9-1-cases.json:20` と比較 report は `maximum_overlap_then_earliest_start` を宣言する。
- `benchmarks/s9_benchmark.py:744-746,763-773` は overlap 候補の先頭を選ぶだけで、overlap 最大値を比較しない。

現行 4 case の anchor はほぼ非重複なので結果に直ちに差が出た証拠はないが、重なる anchor / candidate cue では missing と duplicate の割当が契約どおりにならない。最大 overlap の tie-break を実装し、重複 anchor のテストを追加すべき。

### P2 — full JSON schema の必須性が検証されていない

`parse_whisper_json` は `schema` が存在する場合だけ version を検査し、`1469-1471` では transcription の存在しか必須にしていない。テスト `tests/test_s9_benchmark.py:224-251` も schema field なしの payload を full schema として受け入れる。`systeminfo`、`model`、`params`、`result` の型も検査されない。

今回の argv は `--output-json --output-json-full` を記録し、実測 report も output hash と cold/warm equality を保持しているため、今回の実測が直ちに invalid だとは断定しない。ただし「full JSON schema」契約としては partial / schema-less output を受け入れるため、必須 field と型を strict に検証すべき。

## 確認できた PASS / 残余リスク

- VTT baseline のみ progressive dedupe、candidate は raw のまま、という実装・テスト・parity report の方向は一致している。parity は 4/4 case で text sequence 一致。
- gold の `unverified_provisional` は report と gate の両方で fail-closed。No-Go の理由はこの一件で、CER / glossary / cue / wall / peak RSS の metric gate が通ったことと矛盾しない。
- model SHA-256 / bytes、公式 Hugging Face URL、runtime binary SHA、固定 argv、full JSON flag、wall time、`/usr/bin/time -l` の peak RSS、cold/warm output SHA equality は report 上確認できる。
- production integrity は `unchanged: true`。ただし report markdown の「対象 16 ファイル」と comparison JSON の `checked_file_count: 15` は不一致で、証跡の件数表現は修正が必要。
- cold は OS page cache purge ではなく、公開 YouTube audio span の取得条件にも依存する。これらは report に残余リスクとして明記されている。

## Re-review（final3、2026-08-03）

P1/P2 の再確認は PASS。未解決の actionable finding はない。

- `normalization.exclude_text_tokens` は effective `cue_inclusion_rule.exclude_text_tokens` に写像され、final3 report に 5 token が記録されている。baseline / candidate とも純粋 marker の完全一致だけを除外し、発話中の文字列は残す契約どおり。viewer greeting は `not_auto_excluded_without_human_annotation` で、final3 candidate にも残るため自動除外していない。
- `timeout_sec` は `180.0`。cold の wall budget は `180000 ms`、warm は各 check で `120000 ms`。anchor は最大 overlap、同率なら最早開始の実装と unit test を確認した。
- full JSON は `systeminfo`、`model`、`params`、`result`、`transcription` の必須 root と型を検証し、欠落 root の fail test がある。candidate は progressive dedupe なしの raw cue のまま（実行 cue 数と評価 cue 数も一致）。
- final3 は 16/16 run が `ok`。cold/warm output SHA は全 case で一致し、production hash は 15 files、`unchanged: true`。比較 report の No-Go 理由は両 model とも `gold_not_audited` のみ。

残余リスクは gold の独立音声監査前、OS page cache を purge しない cold、viewer greeting を含む raw 評価であり、いずれも report / protocol に明記された範囲。

## Final re-review（execution-plan-v3、2026-08-03）

PASS。S9-1 の plan semantics に未解決 finding はない。進捗行は `[~] 進行中`、S9-1-0〜4 は `[x]`。元の Done 条件 3 件は文言不変かつすべて `[ ]` のままである。S9-1 証跡は正式 Done 未達・正式な採用モデルなしを明記し、§14 の「S9-1 の No-Go は未完了のまま残す」方針とも整合する。
