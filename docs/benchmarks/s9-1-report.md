# S9-1 代表素材 benchmark report

測定日: 2026-08-03
fixture fingerprint: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`
human audit fingerprint: `9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263`

## 判定

Go。decision mode は operational transcript reference、採用モデルは `ggml-large-v3-turbo-q5_0`。

ユーザー原文「4本とも文字起こしは概ね問題なし」は、表示 transcript content の運用上の reference としてのみ採用した。human verified exact transcript とは記録しない。
fixture gold の `gold_audit_status` は `unverified_provisional` のまま。glossary の個別 exact approval、文字・句読点 exactness、cue anchor の正確なミリ秒は未承認・未主張のまま。
operational transcript reference は Go だが、boundary automation は No-Go / 不採用で、人の preview / 区間確認を必須とする。S9-2 start allowed は TranscriptArtifact / resolver の着手範囲だけを示す。

## 人手監査の次元分離

- 原文: `4本とも文字起こしは概ね問題なし`
- displayed transcript content: human reviewed / no material issue reported / operational benchmark reference
- glossary: not explicitly audited
- character and punctuation exactness: not claimed
- cue anchor exact milliseconds: unapproved
- boundary/editorial outcomes: existing partial audit preserved

## 境界・発話連続性の部分監査

監査者: user / 監査日: 2026-08-03
boundary audit fingerprint: `0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1`
base fixture fingerprint: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`（既存4音声の fixture fingerprint は変更していない）

この証跡は開始境界と発話連続性だけの部分監査であり、transcript 全文、glossary、cue anchor の正確な時刻を audited にはしない。背景音は意味ある発話として数えず、単純な onset-only gate と Whisper timestamp 単独の境界確定は採用しない。
case 1 の `pass` は、今回確認した境界・発話連続性で追加処置なしという意味だけであり、全文品質・glossary・cue anchor・最終 short の品質承認ではない。

| 前回表示順 | case ID | 観察 | 期待 editorial outcome |
|---:|---|---|---|
| 1 | lb4-clip002-short-proper-nouns | 前回表示順 case 1（lb4-clip002-short-proper-nouns）は「ほぼ問題ない」。 | pass |
| 2 | hpe-audio-variation | 前回表示順 case 2（hpe-audio-variation）は開始から約6秒まで意味ある発話がなく、ショート開始として不利、開始境界NG。 | opening_trim_or_review_required |
| 3 | cgal-proper-nouns | 前回表示順 case 3（cgal-proper-nouns）も開始から約6秒まで意味ある発話がなく、開始境界NG。背景音があっても意味ある発話がなければ編集上の無発話として扱う。 | opening_trim_or_review_required |
| 4 | mkw-long-local-asr | 前回表示順 case 4（mkw-long-local-asr）は開始直後に発話はあるが、約2秒から26秒までほぼ発話がなく、ショートとして致命的。 | internal_gap_removal_or_review_required |

S9-1 はこの部分監査を境界自動化の採用根拠にはしない。S9-4 / S9-6 は親候補の固定音声 span を機械的に確定せず、最終 short cutplan / preview で opening trim または内部 gap removal / review を人確認し、audio activity・cue・padding・human preview を併用する。今回の約時刻は観察メモであり、production の普遍的な秒数閾値ではない。

## 代表素材

| case | video / candidate | range | 選定理由 |
|---|---|---:|---|
| lb4-clip002-short-proper-nouns | LB4px1wRFnY / clip_002 | 2853160–2910000 ms | short candidate cutplan cut_001-cut_003 |
| mkw-long-local-asr | mKwn-93gg90 / clip_001 | 1120000–1300000 ms | long candidate first 180 seconds |
| cgal-proper-nouns | CGalA8SISPE / clip_003 | 4220000–4340000 ms | proper noun dense candidate first 120 seconds |
| hpe-audio-variation | hPeRSA9YVIM / clip_003 | 8640000–8730000 ms | different recording context and device terms |

## 比較結果

| model | case | baseline CER | candidate CER | relative improvement | glossary found | cue rate baseline → candidate | cold ms | warm ms | peak RSS max |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ggml-large-v3-turbo-q5_0 | lb4-clip002-short-proper-nouns | 1.016129 | 0.138710 | 86.35% | 2 | 6.20 → 0.80 | 2263 | 2165 | 908165120 |
| ggml-large-v3-turbo-q5_0 | mkw-long-local-asr | 0.616022 | 0.389503 | 36.77% | 1 | 5.83 → 4.50 | 5169 | 5183 | 933560320 |
| ggml-large-v3-turbo-q5_0 | cgal-proper-nouns | 0.797009 | 0.138889 | 82.57% | 6 | 10.40 → 2.60 | 4310 | 4334 | 923729920 |
| ggml-large-v3-turbo-q5_0 | hpe-audio-variation | 0.750000 | 0.188889 | 74.81% | 4 | 5.25 → 0.75 | 2921 | 2928 | 916389888 |
| **ggml-large-v3-turbo-q5_0 median** | 4 case | — | — | **78.69%** | 13 / 10 | gate pass | — | — | 933560320 |
| ggml-large-v3-turbo | lb4-clip002-short-proper-nouns | 1.016129 | 0.100000 | 90.16% | 2 | 6.20 → 0.60 | 2238 | 2229 | 2001256448 |
| ggml-large-v3-turbo | mkw-long-local-asr | 0.616022 | 0.469613 | 23.77% | 1 | 5.83 → 4.17 | 5616 | 5650 | 2017837056 |
| ggml-large-v3-turbo | cgal-proper-nouns | 0.797009 | 0.128205 | 83.91% | 6 | 10.40 → 2.60 | 4221 | 4217 | 2009055232 |
| ggml-large-v3-turbo | hpe-audio-variation | 0.750000 | 0.166667 | 77.78% | 4 | 5.25 → 0.75 | 2939 | 2978 | 2003910656 |
| **ggml-large-v3-turbo median** | 4 case | — | — | **80.85%** | 13 / 10 | gate pass | — | — | 2017837056 |

## 実行条件と証跡

- host: Apple M4 Pro / 64 GB / arm64
- whisper.cpp: 1.9.1、Metal、ja、threads 8、processors 1、temperature 0、beam size 5、best-of 5、padding 0、full JSON、timeout 180 秒
- model cache: `/Users/ryukouokumura/Library/Caches/whisper.cpp/models/`
- audio cache: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/`
- baseline: production progressive dedupe parity 4/4。candidate: raw cue のまま評価
- production hash scope: fixture source_files 14件 + protected cut_clip_003 1件 = exact 15件。root、relative path、完全な file set、path traversal、symlink escape、実ファイル bytes / SHA-256 を before / after とも fail-closed に再検証し、既存 `ja.vtt` と mp4 は非変更。
- raw evidence: model / audio / baseline VTT / whisper-cli の実体 bytes / SHA-256、full JSON の再parse、CER / glossary / cue 指標の再計算、argv / range / run-kind / output schema / candidate text / output fingerprint、stderr の real time / peak RSS を再検証。case runs は 16 / 16 成功。
- cold / warm output SHA equality は全 case で確認済み。warm は別 process invocation の再利用観測で、永続 artifact cache hit は計測・主張していない。
- tie-break metadata: audit-apply 再計測前に固定。prior provisional results known。policy basis は user_wait_time_and_local_constraints。全結果を見る前に宣言したとは主張せず、pass 閾値の変更でもない。
- selected model: `ggml-large-v3-turbo-q5_0`。tie-break は local-only、worst-case 待ち時間、全体待ち時間、peak memory、model bytes、per-case quality の lexicographic rule。
- VTT progressive parity: strict v2 artifactを benchmark / fixture identity、固定4 case順、source VTT bytes / SHA-256、raw / dedup count、text sequence SHA-256から再計算し、4/4 case `text_sequence_equal=true` を effective Go gateへ含めた。

## 再現 command

- `uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name ggml-large-v3-turbo-q5_0 --output-dir /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-audit-apply --report /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-audit-apply/report.json --execute-whisper --run-kind cold`
- `uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name ggml-large-v3-turbo --output-dir /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/cold-audit-apply --report /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/cold-audit-apply/report.json --execute-whisper --run-kind cold`
- `uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name ggml-large-v3-turbo-q5_0 --output-dir /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/warm-audit-apply --report /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/warm-audit-apply/report.json --execute-whisper --run-kind warm`
- `uv run python benchmarks/s9_benchmark.py run --manifest docs/benchmarks/s9-1-cases.json --model-name ggml-large-v3-turbo --output-dir /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/warm-audit-apply --report /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/warm-audit-apply/report.json --execute-whisper --run-kind warm`
- `uv run python benchmarks/s9_compare.py --manifest docs/benchmarks/s9-1-cases.json --q5-cold /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-audit-apply/report.json --q5-warm /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/warm-audit-apply/report.json --turbo-cold /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/cold-audit-apply/report.json --turbo-warm /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/warm-audit-apply/report.json --production-before docs/benchmarks/s9-1-production-hash-before.json --production-after docs/benchmarks/s9-1-production-hash-after.json --parity docs/benchmarks/s9-1-vtt-progressive-parity.json --boundary-audit docs/benchmarks/s9-1-boundary-audit.json --transcript-audit docs/benchmarks/s9-1-human-audit-v2.json --output-json docs/benchmarks/s9-1-report.json --output-md docs/benchmarks/s9-1-report.md`

## 残余リスク

- gold は既存 VTT / transcript / ASS / cutplan を文脈利用した仮作成で、音声の独立人手監査をしていない。
- cold は OS page cache を消去した完全 cold ではなく、各 model の cold wave と warm wave を分離した reuse 観測である。
- mKwn / CGal / hPe は公開 YouTube の audio-only span 取得で、client / network 条件差が残る。
- candidate cue は raw のまま評価し、rolling VTT dedupe を候補へ適用していない。
- モデルは Git 管理外の手動 cache にあり、production 自動 download は実装していない。
- 自然文の「概ね問題なし」は transcript content の operational reference であり、character / punctuation exactness への昇格ではない。
- glossary は個別表記の明示監査ではなく、cue anchor exact milliseconds も未承認である。
- 境界監査は transcript / glossary / cue anchor exact times の承認ではなく、4 case の部分的な人手所見である。
- case 1 の pass は今回確認した境界・発話連続性で追加処置なしという意味だけで、全文品質や最終 short の品質承認ではない。
- case 2・3 の約6秒、case 4 の約2〜26秒は今回の観察メモであり、production の普遍的な秒数閾値ではない。
- 親候補の固定 span と最終 short cutplan の品質は分離し、S9-4 / S9-6 では audio activity・cue・padding・human preview を併用する必要がある。
