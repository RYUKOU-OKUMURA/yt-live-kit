# S9-1 代表素材 benchmark protocol

測定日: 2026-08-03
タスク: S9-1
前提 commit: `87574c0` → `e244f6e` → `99f7284` → `5c04e2c`

## 目的と判定方針

YouTube VTT を baseline とし、同じ音声 span を whisper.cpp 1.9.1 の日本語モデルで処理して比較する。S9-1 は production code、既存 `data`、既存 `subtitles/ja.vtt`、既存 mp4 を変更しない。

今回の repository 上には、音声を人が直接聴取して確認した gold transcript の full audit 証跡がない。そのため fixture の gold は既存 transcript / VTT / ASS / cutplan と文脈から手作業で整えた「未監査の仮 gold」とし、実行結果の数値は provisional と記録する。今回追加した [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) は、ユーザーが直接聴いた開始境界・発話連続性だけの部分監査であり、transcript 全文、glossary、cue anchor の正確な時刻を audited にはしない。full gold audit が完了しない限り Go にはしない。これは VTT をそのまま正解にする評価リークと、根拠のない採用判定を防ぐための fail-closed 条件である。

## 代表素材と固定 span

詳細な fixture は [`s9-1-cases.json`](./s9-1-cases.json) に固定する。

| case | video ID / 既存候補 | range | 選定理由 | 音声条件の記録 |
|---|---|---:|---|---|
| `lb4-clip002-short-proper-nouns` | `LB4px1wRFnY` / `clip_002` の cutplan `cut_001`〜`cut_003` | 00:47:33.160–00:48:30.000 | 57 秒の短い候補。Claude / Codex / 要件定義など固有名詞が連続し、既存 cutplan と ASS、既存 mp4 がある | production の既存 mp4 から音声 span を作る。既存 mp4 の hash を前後比較する |
| `mkw-long-local-asr` | `mKwn-93gg90` / `clip_001` | 00:18:40.000–00:21:40.000 | 180 秒の長い候補。Ollama / Together AI / Whisper などローカル文字起こし関連の用語を含む | 公開 YouTube から音声のみの span を cache に取得する。元動画を保存しない |
| `cgal-proper-nouns` | `CGalA8SISPE` / `clip_003` | 01:10:20.000–01:12:20.000 | DirectX / Microsoft / Windows / Steam / DX12 / iMac / Apple II と固有名詞が多い | 別配信日の公開音声 span。音声条件は実測 loudness と silence 比率を report に残す |
| `hpe-audio-variation` | `hPeRSA9YVIM` / `clip_003` | 02:24:00.000–02:25:30.000 | 90 秒の別候補。HHKB / Mac / macOS / ファンクションキーを含み、他素材と録音時期・区間条件が異なる | 別配信日の公開音声 span。実測値以外の SNR 推測はしない |

開始時に各 `meta.json`、`subtitles/ja.vtt`、`clips/candidates.json`、該当 cutplan、既存 `LB4px1wRFnY.mp4` の SHA-256 を [`s9-1-production-hash-before.json`](./s9-1-production-hash-before.json) へ保存した。benchmark 後に同じ command を再実行し、値が変わらないことを確認する。

## 境界・発話連続性の部分監査

監査者はユーザー、監査日は 2026-08-03。所見の自然文と機械検証用の strict schema は [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) に固定する。既存の `s9-1-cases.json` は変更せず、base fixture fingerprint `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718` を保持する。boundary audit は別 artifact fingerprint `0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1` で追跡し、base fixture に含めない。したがって旧・新の base fixture fingerprint を置き換える変更ではなく、固定音声と provisional gold の identity を守るための分離である。

前回表示順と case ID の対応は次のとおりである。

| 前回表示順 | case ID | ユーザー所見の要約 | 機械検証する editorial outcome |
|---:|---|---|---|
| 1 | `lb4-clip002-short-proper-nouns` | ほぼ問題ない | `pass` |
| 2 | `hpe-audio-variation` | 開始から約6秒まで意味ある発話がなく、開始境界NG | `opening_trim_or_review_required` |
| 3 | `cgal-proper-nouns` | 開始から約6秒まで意味ある発話がなく、背景音は意味ある発話として数えない | `opening_trim_or_review_required` |
| 4 | `mkw-long-local-asr` | 開始直後に発話はあるが、約2秒から26秒までほぼ発話がなく致命的 | `internal_gap_removal_or_review_required` |

この部分監査は、S9-1 の production 非変更 benchmark に対して「無発話部分への cue 幻覚」と、意味ある発話の開始・内部 gap を確認する材料を追加する。約6秒、約2〜26秒は今回の自然な聴取所見であり、production の普遍的な秒数閾値ではない。開始直後に一言あれば通る単純な onset-only gate は禁止する。背景音があっても意味ある発話がなければ編集上は無発話として扱う。

case 1 の `pass` は、今回確認した境界・発話連続性で追加処置なしという意味だけであり、transcript 全文、glossary、cue anchor、最終 short の品質承認ではない。

S9-4 / S9-6 はこの evidence を親候補の固定 span の品質承認へ昇格させない。最終 cutplan / final short が冒頭の無発話と長い内部無発話を残さないことを、audio activity、cue、padding、human preview と人の確認で別途検証する。Whisper timestamp を唯一の境界正本にせず、親候補を切り詰める判断と最終 short の品質判定を分離する。

## 固定評価契約

### Gold と正規化

- gold は fixture の `gold.text` と `gold.glossary` を使う。gold の `audit_status` は全 case で `unverified_provisional` とする。
- gold は VTT を無条件に正解扱いしない。既存 `transcript/full.txt`、`prompt_chapters.txt`、既存 ASS、cutplan の文脈を参照し、固有名詞と明らかな誤認識を手修正した候補である。
- 直接音声を人が確認していない箇所は未監査として扱う。数値は「仮 gold に対する provisional 指標」であり、Go 判定の根拠に昇格させない。
- テキストは Unicode NFKC、前後空白除去、連続空白の除去を行う。比較用の空白は無視するが、句読点は既定では保持する。VTT/ASS の HTML・karaoke tag と純粋な `[音楽]`、`[笑い]`、`[拍手]`、`[鼻息]`、`[咳払い]` のみ除外する。
- CER は正規化済み Unicode codepoint 列の Levenshtein 距離を gold 文字数で割る。gold が空の場合は不正とする。
- glossary は case ごとの canonical surface を固定し、gold text から自動生成しない。term ごとの exact found / missing を記録し、alias を正解として黙って加点しない。

### Cue inclusion rule

- target range と cue の重なりを、cue の終了が range の開始より後で、cue の開始が range の終了より前であることとして判定する。終了境界だけが一致する cue は含めない。
- baseline の dedupe は production `src/yt_live_kit/services/vtt_parser.py::deduplicate_progressive` と同じ順序意味論に固定する。全 VTT を range filter より先に処理し、`text == prev_text` は skip、`text.startswith(prev_text)` は新しい差分だけを残し、`prev_text in text` は最初の一致部分を除いた差分だけを残し、それ以外は cue 本文を残す。各 raw cue の本文を次の比較用 `prev_text` に更新する。実データで見える 10 ms 境界はこの rolling VTT の一形態であり、duration だけで cue を削除する規則ではない。
- dedupe 後の cue は元の絶対時刻を保持し、target range との overlap を再度適用する。benchmark harness の parity test は production parser と同じ入力の text 列が一致することを検証する。
- 4 本の production VTT を全編で比較した parity 結果は [`s9-1-vtt-progressive-parity.json`](./s9-1-vtt-progressive-parity.json) に保存し、4/4 case で raw cue 数・dedup 後 cue 数・dedup 済み text sequence SHA-256 が一致した。
- Whisper candidate の JSON cue は rolling VTT ではないため、candidate 側には progressive dedupe を適用しない。identical / contained cue は raw のまま残し、cue inclusion の duplicate と CER の反復として測定する。marker token の除外と target overlap だけを共通化する。
- 純粋な非音声 marker は fixture の `normalization.exclude_text_tokens` に列挙した完全一致だけを除外する。話者の発話中に含まれる文字列は勝手に削らない。
- 視聴者挨拶は未監査 gold から自動分類しない。明示的な人手注記がない限り raw cue に残し、挨拶を除外したかのような評価上の加点は行わない。
- fixture の cue anchor と出力 cue を overlap で対応付け、未対応 anchor を missing、1 anchor に複数対応した余分な cue を duplicate とする。境界・重複・短い cue を harness unit test でも固定する。

### 事前宣言 gate

| gate | 条件 | 未達時 |
|---|---|---|
| CER | case ごとの `(CER_VTT - CER_model) / CER_VTT` の paired median が `0.10` 以上。CER_VTT が 0 の case は相対改善を 0 とする | No-Go |
| 固有名詞 | glossary exact match 数が VTT baseline 以上。誤り件数も report | No-Go |
| cue | candidate の missing + duplicate rate が VTT baseline + 5 percentage points 以下 | No-Go |
| wall time | 各 case の cold run 180 秒以下、warm/reuse run 120 秒以下 | No-Go |
| peak memory | 各 run 8 GiB 以下 | No-Go |
| gold 独立性 | 全 case の gold が人手音声監査済みであること | 未監査の間は必ず No-Go |

全 gate が閾値内でも、gold audit が `unverified_provisional` の間は採用モデルを決定しない。No-Go の場合は後続 S9 実装を高精度経路として進めず、既存 YouTube VTT を fallback-only とする。

境界部分監査の expected editorial outcome が4 caseすべて機械検証できても、full transcript / glossary gold 未完了と既存 cue proxy の盲点があるため、S9-1 は No-Go のままにする。S9-2 以降を開始可能にはしない。

## 実行条件

- host: Apple M4 Pro / 64 GB / arm64
- whisper runtime: `/opt/homebrew/bin/whisper-cli` 1.9.1、Metal capability を実行ログに保存
- model candidates: 公式 `ggerganov/whisper.cpp` の `ggml-large-v3-turbo.bin` と `ggml-large-v3-turbo-q5_0.bin`
- source: [whisper.cpp v1.9.1](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.1) / [official Hugging Face model repository](https://huggingface.co/ggerganov/whisper.cpp)
- language: `ja`
- audio: 16 kHz / mono / PCM WAV、固定 range、同じ source span を全 model で再利用
- initial prompt: fixture に固定した日本語・固有名詞 prompt。候補間で同一
- decode: temperature 0、beam size 5、best-of 5、threads 8、processors 1、flash attention default、VAD disabled、whisper.cpp full JSON schema
- cache policy: model / audio を手動で cache。各 case/model の最初の process を `cold`、直後の同条件再実行を `warm` と記録する。OS page cache を完全に消去した cold ではないため、その限界を report に明記する
- model download: production から自動取得しない。モデルは `/Users/ryukouokumura/Library/Caches/whisper.cpp/models/` に手動保存し URL / bytes / SHA-256 を report へ記録する
- benchmark audio cache: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/`
- extraction tool: `/opt/homebrew/Cellar/ffmpeg-full/8.1.2_2/bin/ffmpeg` (Homebrew `/opt/homebrew/bin/ffmpeg` はこの環境では libvpx 不足で使用不可)。公開 YouTube の mKwn / CGal / hPe は `yt-dlp 2026.07.04` で音声 span のみ取得し、hPe は android_vr client fallback を使った。取得失敗・client 差異は残余リスクとして記録し、音声 fixture の SHA-256 を固定する。
- production data は読み取り専用。YouTube 書き込み・投稿・概要欄変更は行わない。

## 実測前に検出した baseline parity defect

schema-check の構造確認後、実測前に production parser と benchmark dedupe の意味論が一致していないことを検出した。初版 harness は 10 ms の短 cue と既存 cue の完全一致だけを除外しており、rolling VTT の「隣接 cue に本文が追加される」「前本文が新本文に含まれる」形式を除去できなかった。これは baseline CER を水増しし得るため、production `deduplicate_progressive` と timed cue text parity を実装・テストしてから実測を開始する。schema-check の report は実測結果として採用せず、修正後に全 A/B report を再生成する。

## final3 前の defect fix 記録

独立 defect-first review で、最終実測前に次の実装上の不一致を検出した。final2 は最終報告に採用せず、規則を測定結果に合わせて変更しない状態で修正してから final3 を実行した。

- fixture の `normalization.exclude_text_tokens` が内部 `cue_inclusion_rule` に写像されず、marker 除外が実効化されていなかった。harness の写像を修正し、実効値を `[音楽]`、`[笑い]`、`[拍手]`、`[鼻息]`、`[咳払い]` に固定した。
- `include_viewer_greeting` は実装されていない自動分類規則だったため削除し、明示的な人手注記がない viewer greeting は raw cue に残す契約へ改めた。
- seconds の wall budget を millisecond に変換した値が timeout の default に流入していた。gate budget は millisecond、whisper timeout は 180 seconds として分離した。
- cue anchor は最初の overlap ではなく、最大 overlap、同率なら最も早い開始時刻で対応付ける実装と unit test に固定した。
- `whisper-cli-json-full-v1` は実際の whisper.cpp 1.9.1 output にある `systeminfo`、`model`、`params`、`result`、`transcription` と型を必須化した。実データ full JSON 16件の再読に成功した。

上記修正後の final3 は q5 / turbo の cold・warm 各4 caseを再実行し、全16 run が成功した。final3 の raw report と統合 report だけを判定証跡として採用する。

## 再現性と報告

report には、manifest / fixture fingerprint、source file hash、audio hash、model hash / size / URL、whisper-cli / ffmpeg / yt-dlp 実体、固定 argv、case range、gold audit status、baseline / model の全文出力 fingerprint、CER、glossary、cue、cold/warm wall time、peak RSS、gate の個別結果、Go / No-Go、残余リスクを保存する。fixture fingerprint は canonical JSON の SHA-256 とし、path や mtime だけに依存しない。

実行 command（model と cache kind ごとに同じ 4 case を実行）:

```text
uv run python benchmarks/s9_benchmark.py run \
  --manifest docs/benchmarks/s9-1-cases.json \
  --model-name ggml-large-v3-turbo-q5_0 \
  --output-dir /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-final3 \
  --report /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-final3/report.json \
  --execute-whisper --run-kind cold

# 同じ command を q5 / turbo と cold / warm の各組み合わせで実行する。
```

実際の harness の help と report に残った command を正本とし、上記は固定入力・出力先を示す実測再現 command である。比較 report は `benchmarks/s9_compare.py` で4実行結果を model ごとに統合し、cold / warm の全 wall time と peak RSS、および cold / warm output SHA equality を保持する。
