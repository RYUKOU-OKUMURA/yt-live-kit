# S9-1 代表素材 benchmark report

測定日: 2026-08-03
fixture fingerprint: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`

## 判定

No-Go。gold は音声の独立人手監査前であり、数値は provisional。既存 YouTube VTT を fallback-only とし、S9-3 の高精度モデル採用へ進めない。

q5 / turbo とも CER、glossary、cue、wall time、peak RSS の技術 gate は通過したが、gold audit gate が fail closed した。

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
