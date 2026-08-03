# S9-1 代表素材 benchmark report

測定日: 2026-08-03
fixture fingerprint: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`

## 判定

No-Go。gold は音声の独立人手監査前であり、数値は provisional。既存 YouTube VTT を fallback-only とし、S9-3 の高精度モデル採用へ進めない。

q5 / turbo とも CER、glossary、cue、wall time、peak RSS の技術 gate は通過したが、gold audit gate が fail closed した。

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

S9-1 はこの部分監査により、既存 cue proxy だけでは無発話・背景音・長い内部 gap を捉え切れないことが分かったため No-Go を維持する。S9-4 / S9-6 は親候補の固定音声 span を切り詰めず、最終 short cutplan / preview で opening trim または内部 gap removal / review を人確認し、audio activity・cue・padding・human preview を併用する。今回の約時刻は観察メモであり、production の普遍的な秒数閾値ではない。

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
| ggml-large-v3-turbo-q5_0 | lb4-clip002-short-proper-nouns | 1.016129 | 0.138710 | 86.35% | 2 | 6.20 → 0.80 | 2149 | 2149 | 903921664 |
| ggml-large-v3-turbo-q5_0 | mkw-long-local-asr | 0.616022 | 0.389503 | 36.77% | 1 | 5.83 → 4.50 | 5170 | 5171 | 932773888 |
| ggml-large-v3-turbo-q5_0 | cgal-proper-nouns | 0.797009 | 0.138889 | 82.57% | 6 | 10.40 → 2.60 | 4300 | 4307 | 919830528 |
| ggml-large-v3-turbo-q5_0 | hpe-audio-variation | 0.750000 | 0.188889 | 74.81% | 4 | 5.25 → 0.75 | 2914 | 2915 | 920928256 |
| **ggml-large-v3-turbo-q5_0 median** | 4 case | — | — | **78.69%** | 13 / 10 | gate pass | — | — | 932773888 |
| ggml-large-v3-turbo | lb4-clip002-short-proper-nouns | 1.016129 | 0.100000 | 90.16% | 2 | 6.20 → 0.60 | 2268 | 2259 | 2002157568 |
| ggml-large-v3-turbo | mkw-long-local-asr | 0.616022 | 0.469613 | 23.77% | 1 | 5.83 → 4.17 | 5622 | 5629 | 2017689600 |
| ggml-large-v3-turbo | cgal-proper-nouns | 0.797009 | 0.128205 | 83.91% | 6 | 10.40 → 2.60 | 4234 | 4239 | 2007891968 |
| ggml-large-v3-turbo | hpe-audio-variation | 0.750000 | 0.166667 | 77.78% | 4 | 5.25 → 0.75 | 2965 | 2965 | 2009677824 |
| **ggml-large-v3-turbo median** | 4 case | — | — | **80.85%** | 13 / 10 | gate pass | — | — | 2017689600 |

## 実行条件と証跡

- host: Apple M4 Pro / 64 GB / arm64
- whisper.cpp: 1.9.1、Metal、ja、threads 8、processors 1、temperature 0、beam size 5、best-of 5、padding 0、full JSON、timeout 180 秒
- model cache: `/Users/ryukouokumura/Library/Caches/whisper.cpp/models/`
- audio cache: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/`
- baseline: production progressive dedupe parity 4/4。candidate: raw cue のまま評価
- production data hash: before / after は一致。対象 15 ファイル、既存 `ja.vtt` と mp4 は非変更。
- VTT progressive parity: [s9-1-vtt-progressive-parity.json](./s9-1-vtt-progressive-parity.json) で 4/4 case 一致。

## 残余リスク

- gold は既存 VTT / transcript / ASS / cutplan を文脈利用した仮作成で、音声の独立人手監査をしていない。
- cold は OS page cache を消去した完全 cold ではなく、各 model の cold wave と warm wave を分離した reuse 観測である。
- mKwn / CGal / hPe は公開 YouTube の audio-only span 取得で、client / network 条件差が残る。
- candidate cue は raw のまま評価し、rolling VTT dedupe を候補へ適用していない。
- モデルは Git 管理外の手動 cache にあり、production 自動 download は実装していない。
- 境界監査は transcript / glossary / cue anchor exact times の承認ではなく、4 case の部分的な人手所見である。
- case 1 の pass は今回確認した境界・発話連続性で追加処置なしという意味だけで、全文品質や最終 short の品質承認ではない。
- case 2・3 の約6秒、case 4 の約2〜26秒は今回の観察メモであり、production の普遍的な秒数閾値ではない。
- 親候補の固定 span と最終 short cutplan の品質は分離し、S9-4 / S9-6 では audio activity・cue・padding・human preview を併用する必要がある。
