# S9-1 代表素材 benchmark protocol

測定日: 2026-08-03
タスク: S9-1
前提 commit: `87574c0` → `e244f6e` → `99f7284` → `5c04e2c`

## 目的と判定方針

YouTube VTT を baseline とし、同じ音声 span を whisper.cpp 1.9.1 の日本語モデルで処理して比較する。S9-1 は production code、既存 `data`、既存 `subtitles/ja.vtt`、既存 mp4 を変更しない。

今回の repository 上の fixture gold は、既存 transcript / VTT / ASS / cutplan と文脈から手作業で整えた固定 reference であり、文字単位・句読点単位・cue anchor の正確なミリ秒までの full exact gold ではない。ユーザーは 2026-08-03 に4本の provisional gold transcript を開いて確認した後、「4本とも文字起こしは概ね問題なし」と明示した。この自然文は [`s9-1-human-audit-v2.json`](./s9-1-human-audit-v2.json) へ原文のまま4 caseに対応付けるが、character / punctuation exactness、glossary 個別 exact approval、cue anchor exact milliseconds へは昇格させない。

この S9-1-AUDIT-APPLY では、要件との齟齬を A として解消する。すなわち displayed transcript content だけを operational transcript reference として採用し、CER は固定 reference に対する比較指標として残す。NFR-11 のローカル whisper.cpp と FR-36 の選択区間・人確認の契約は変えず、AC-37 の numeric gate 閾値も変えない。exact gold 不足を隠す変更ではなく、exact gold の状態と operational reference の採否を別 dimension にした明示的な decision mode である。boundary の自動採用、Whisper timestamp 単独の境界確定、無確認の downstream 進行は認めない。

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

- gold は fixture の `gold.text` と `gold.glossary` を使う。`s9-1-cases.json` の `gold.audit_status` は全 case で `unverified_provisional` のまま維持する。別 artifact の displayed transcript content だけを operational reference として受け入れる。
- gold は VTT を無条件に正解扱いしない。既存 `transcript/full.txt`、`prompt_chapters.txt`、既存 ASS、cutplan の文脈を参照し、固有名詞と明らかな誤認識を手修正した候補である。
- ユーザーの自然文は「4本とも文字起こしは概ね問題なし」であり、4 case の表示 transcript content に対する human reviewed / no material issue reported の証跡である。文字単位・句読点単位の exactness、glossary 個別 exact approval、cue anchor exact milliseconds は未監査・未承認のままにする。
- CER、glossary、cue の数値は固定 reference に対する指標として扱う。数値は exact human truth の証明ではないが、事前宣言 gate の閾値と同じ条件でモデル間比較に使う。
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
| transcript operational reference | 4 case の displayed transcript content が自然文監査で human reviewed / no material issue reported と対応付けられていること | 未達なら No-Go |

既存の numeric gate（CER 10％、glossary 非悪化、cue baseline +5 percentage points、wall time、peak memory）は変更しない。旧 protocol の「full exact gold が無ければ必ず No-Go」という条件は、ユーザーの自然文を exact approval に偽装しないために、A の decision mode では「exact gold は未主張のまま、displayed transcript content の operational reference gate を別に評価する」契約へ明示的に分離した。これにより `s9-1-cases.json` の provisional status を audited へ書き換えず、NFR-11 のローカル運用、FR-36 の選択区間と人確認、AC-37 の閾値を弱めない。

全 numeric gate と operational transcript reference gate が通過した場合だけ、S9-3 が参照するモデルを決める。文字・句読点 exactness、glossary 個別 exact approval、cue anchor exact milliseconds は adopted model の根拠ではなく、report では `not_claimed` / `not_explicitly_audited` / `unapproved` と固定する。No-Go の場合は後続 S9 実装を高精度経路として進めず、既存 YouTube VTT を明示的な fallback とする。

canonical report の status は次の意味に分ける。`gold_audit_status` は fixture の `unverified_provisional` を維持し、`transcript_reference_status` だけを `accepted_operational_benchmark_reference` とする。`decision.go` は operational transcript reference の採否範囲に限る。`decision.boundary_decision.status` は `no_go`、`automation_adopted` は false、human review は required のままであり、`decision.s9_2_start_allowed` は TranscriptArtifact / resolver の着手を表すだけで境界自動化を解禁しない。

境界部分監査の expected editorial outcome は既存 artifact のまま保持する。A で Go になっても、これは境界自動化の採用を意味しない。Whisper timestamp 単独の確定、約6秒・約2〜26秒の普遍的閾値、無確認の cutplan / downstream は禁止し、人の audio preview と区間確認を必須にする。

### 候補が複数通過した場合の tie-break

この順序は今回の audit-apply 再計測 command と canonical report の生成より前に固定した運用規則である。ただし final3 の q5 / turbo の provisional 結果は既に既知だったため、「全結果を見る前に宣言した」とは主張しない。根拠はユーザーが以前から重視している待ち時間、ローカル運用、memory、model size、case 別品質であり、今回の audit-apply 実測結果に合わせた後付けの規則ではない。pass 閾値の変更ではない。従量課金 API を使わず、ローカル絶対 path の whisper-cli と手動 cache model を使う候補だけを eligible とする。eligible 候補を次の lexicographic key の昇順で並べる。

1. local absolute runtime / model path を満たすこと
2. case ごとの cold / warm wall time median の最大値が小さいこと（worst-case 待ち時間）
3. 全 cold / warm run の median wall time が短いこと
4. 全 run の最大 wall time が短いこと
5. 全 run の最大 peak memory が小さいこと
6. model file bytes が小さいこと
7. case 別 CER 相対改善の最小値が大きいこと
8. paired median CER 相対改善が大きいこと
9. model name の昇順

q5 と turbo は全 numeric gate を通過したためこの規則を適用し、待ち時間・memory・model bytes・worst case quality の観点で q5 を採用する。turbo の paired median が高いことは report に残すが、結果を見て規則を後付けしない。

## 実行条件

- host: Apple M4 Pro / 64 GB / arm64
- whisper runtime: `/opt/homebrew/bin/whisper-cli` 1.9.1、Metal capability を実行ログに保存
- model candidates: 公式 `ggerganov/whisper.cpp` の `ggml-large-v3-turbo.bin` と `ggml-large-v3-turbo-q5_0.bin`
- source: [whisper.cpp v1.9.1](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.1) / [official Hugging Face model repository](https://huggingface.co/ggerganov/whisper.cpp)
- language: `ja`
- audio: 16 kHz / mono / PCM WAV、固定 range、同じ source span を全 model で再利用
- initial prompt: fixture に固定した日本語・固有名詞 prompt。候補間で同一
- decode: temperature 0、beam size 5、best-of 5、threads 8、processors 1、flash attention default、VAD disabled、whisper.cpp full JSON schema
- cache policy: model / audio を手動で cache。各 case/model の最初の process を `cold`、同じ audio / model / settings の別 process invocation を `warm` と記録する。OS page cache を完全に消去した cold ではなく、永続 artifact cache hit も計測していないため、warm を cache hit の証明とは扱わない
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

上記修正後の audit-apply rerun は q5 / turbo の cold・warm 各4 caseを再実行し、全16 run が成功した。audit-apply の raw report と統合 report だけを今回の判定証跡として採用する。

## 再現性と報告

report には、source fixture / model-specific run manifest fingerprint、source file hash、audio hash、model hash / size / URL、whisper-cli / ffmpeg / yt-dlp 実体、固定 argv、case range、gold audit status、baseline / model の全文出力 fingerprint、CER、glossary、cue、cold/warm wall time、peak RSS、raw report の model / input / runtime / range / run-kind identity、cold/warm output SHA equality、gate の個別結果、Go / No-Go、残余リスクを保存する。fixture fingerprint は canonical JSON の SHA-256 とし、path や mtime だけに依存しない。既存 numeric threshold は変更しない。

実行 command（model と cache kind ごとに同じ 4 case を実行）:

```text
uv run python benchmarks/s9_benchmark.py run \
  --manifest docs/benchmarks/s9-1-cases.json \
  --model-name ggml-large-v3-turbo-q5_0 \
  --output-dir /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-audit-apply \
  --report /Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-audit-apply/report.json \
  --execute-whisper --run-kind cold

# 同じ command を q5 / turbo と cold / warm の各組み合わせで実行する。
```

実際の harness の help と report に残った command を正本とし、上記は固定入力・出力先を示す実測再現 command である。比較 report は `benchmarks/s9_compare.py` で q5 / turbo × cold / warm の4 command（各 command は固定4 case、合計16 case runs）を統合し、cold / warm の全 wall time と peak RSS、raw identity、および cold / warm output SHA equality を保持する。output stability は再現性の integrity check であり、numeric pass threshold の変更ではない。
