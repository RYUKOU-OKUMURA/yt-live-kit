# S9-1 benchmark 継続意思決定ログ

このログは、S9-1 の委任仕様と、以後この親タスクから受け取った明示的な意思決定・レビュー結論だけを時系列で記録する。推測や個人情報は追加しない。

## 2026-08-03 — 初期委任仕様の受領

### ユーザーが明示した事実

- このログの変更対象は `docs/benchmarks/s9-1-learning-log.md` のみ。ファイルが無い場合は作成できる。
- `src/` の production code、`data`、`README`、requirements、`docs/execution-plan-v3.md`、他の `docs/benchmarks` は変更しない。
- benchmark の実装、モデルの取得・実行、コミットはこのログ担当の対象外。
- 記録対象は、ユーザーが提示した S9-1 委任仕様と、今後この親タスクから送られる意思決定・レビュー結論に限る。
- 初期仕様を記録した後は待機し、追加の判断・レビュー結果を受けた時だけ追記する。
- 追記時は、ユーザーが明示した事実、このタスクで確定した判断、未確定事項を分ける。完了時にファイルと追記内容を報告する。

### 委任仕様から固定された安全境界

- S9-1 は production 非変更の代表素材 benchmark とする。既存の production data、既存 `ja.vtt`、既存 mp4 を変更しない。
- 実施対象のファイル範囲は benchmark harness、benchmark fixture、`docs/benchmarks/` の計測記録に限る。production の `src/`、既存 `data/`、`README.md` は対象外。
- 比較するモデルは手動で取得済みの候補だけとし、モデル自動ダウンロードを行わない。新規 pip 依存、従量課金 API、自由な shell command は追加しない。
- `whisper-cli` は 1.9.1 を対象とし、全編 Whisper、動画 mp4 全体の取得、全資産の一括 backfill、local video 入力、YouTube VTT の上書き・置換は S9 初版の対象外とする。
- 実 YouTube 書き込み、実ネットワーク、従量課金 API は benchmark テストで使用しない。
- S9-1 の計測証跡だけで S9 フェーズ完了とはしない。No-Go または根拠不足の場合は fallback-only として止められる状態を保つ。

### 委任仕様から固定された品質ゲート

- Whisper 実行前に、代表素材 3〜5 本、各素材の評価対象 span、gold transcript、固有名詞・製品名 glossary、cue inclusion rule、測定条件、判定閾値、wall time / peak memory budget を固定する。
- 各素材は、短い候補、長い候補、固有名詞が多い候補、音声条件が異なる候補を含む選定方針とする。
- YouTube VTT を baseline とし、同じ音声 span、言語 `ja`、padding、出力 schema、測定条件で候補モデルを A/B 比較する。
- 初版 gate は、paired median CER の相対改善 10％以上、固有名詞 exact match の非悪化、cue 欠落・重複率が VTT baseline に対して 5％以内、wall time / peak memory が事前宣言 budget 内であること。いずれかを満たさなければ No-Go とする。
- `whisper-cli` 1.9.1 の実体・build capability、モデル file fingerprint、ffmpeg / yt-dlp の実体を記録する。
- deterministic fixture、gold / glossary の固定値検証、未知 output schema の拒否、途中終了・timeout、同一入力の再実行比較、wall time / peak memory の記録、`git diff --check` を確認対象とする。
- cache hit / miss は同一条件の比較記録に含める。未知形式、部分結果、入力不一致、基準未達は高精度採用として扱わない。

### 委任仕様で定められた成果物

- 代表素材ごとの VTT / 候補 Whisper A/B 表。
- 固定した gold transcript、固有名詞 glossary、選択 span、cue inclusion rule、fixture fingerprint。
- `whisper-cli`、build capability、モデル、ffmpeg、yt-dlp の実体・fingerprint と、採用モデルの設定。
- 処理時間、CER、固有名詞 exact match / 誤り件数、cue 欠落・重複、境界確認に必要な情報、CPU / peak memory、cache hit / miss の計測記録。
- 採用モデル、設定、未採用理由、再現 command、測定日、判定閾値、Go / No-Go、および未達時の fallback-only 根拠を `docs/benchmarks/` に残す。
- S9-3 が参照する唯一のモデル設定を、S9-1 の fixture fingerprint と結び付けて固定する。

### 委任仕様で定められた報告項目

- 対象タスク ID、前タスク commit SHA、変更ファイル一覧。
- `uv run pytest` の件数・対象テスト、benchmark / 実機証跡の有無。
- artifact schema、source kind、model / runtime / settings、audio / artifact fingerprint、cue digest、失効理由。
- 既存 `ja.vtt` の前後 hash、source VTT の保存先、候補 artifact / candidate fingerprint、gold / glossary / 評価 gate。
- cache hit / miss、複数区間の処理順、1 ジョブ制約、timeout / malformed output / partial failure の挙動。
- 既存 VTT 経路、人確認、FR-30 / FR-22 / FR-25 / FR-33 への伝播、未対応の将来範囲。

### このタスクで確定した判断

- この担当は S9-1 の計測作業を代行せず、委任仕様の安全境界・品質ゲート・成果物・報告項目をこのログへ記録する。
- 追加の意思決定またはレビュー結論が届くまで、benchmark 実装・モデル操作・コミットを行わず待機する。

### 未確定

- 比較対象モデル、採用モデル、モデル設定。
- benchmark の代表素材、span、gold transcript、glossary、fixture fingerprint。
- 実測値、cache hit / miss、wall time、peak memory、CER、固有名詞・cue の比較結果。
- Go / No-Go、fallback-only の最終判定、レビュー結論、S9-1 の commit SHA。

## 2026-08-03 — 代表素材・gold・モデル候補の固定

### ユーザーが明示した事実

- 代表素材を次の4 caseに固定した。
  - `LB4 short`
  - `mKwn long`
  - `CGal proper-noun`
  - `hPe audio-variation`
- gold は既存成果物を元に手修正したが、音声の独立人手監査は未実施である。
- gold の `audit_status` は `unverified_provisional` とする。
- 数値は `provisional` とする。
- gold gate は fail-closed とし、現時点では Go 不可とした。
- モデル候補を公式 whisper.cpp HF の次の2候補に固定した。
  - `large-v3-turbo`
  - `large-v3-turbo-q5_0`
- production / data / `ja.vtt` は read-only とする。
- モデルは手動 cache のものだけを使用する。

### このタスクで確定した判断

- 4 case を S9-1 の代表素材セットとして扱う。
- `audit_status=unverified_provisional` の gold と `provisional` な数値は、独立人手監査が完了するまで確定済みの品質根拠として扱わない。
- gold gate fail-closed を維持し、現時点で Go 判定へ進めない。
- 比較対象は固定した2候補に限定し、production / data / `ja.vtt` の読み取り専用境界と手動 cache 限定を維持する。

### 未確定

- 音声の独立人手監査の結果と `audit_status` の確定値。
- 監査後に確定する gold、数値、gate 判定。
- 2候補の最終採用モデルと Go / No-Go の最終結論。

## 2026-08-03 — 親レビュー: dedupe parity defect の実測前検出

### ユーザーが明示した事実

- 実測前に benchmark dedupe の欠陥を検出した。
- production の `src/yt_live_kit/services/vtt_parser.py::deduplicate_progressive` の意味論は、時間幅 heuristic ではなく raw cue の順序に基づく。
- 既存 schema-check run は実測ではなく、候補欠落の構造確認だけを行ったものだった。
- gold は未監査であり、No-Go fail-closed を維持する。
- 今回のログ担当が変更するファイルは `docs/benchmarks/s9-1-learning-log.md` のみとする。

### この親レビューで確定した判断

- benchmark は `deduplicate_progressive` と timed cue text parity を実装してから実測する。
- dedupe の parity は raw cue 順に、次の意味論を再現する。
  1. text が `prev_text` と identical なら skip する。
  2. `text.startswith(prev_text)` なら差分を出力する。
  3. `prev_text in text` なら除去差分を出力する。
  4. それ以外は raw output を出力する。
  5. 処理後に `prev_text` を更新する。
- range filter は、全 VTT を dedupe した後に適用する。
- 既存 schema-check run は今回の実測前 defect fix により破棄し、parity 修正後に再生成する。既存 run を実測結果として扱わない。
- protocol と parity test を更新する。ただし、このログ担当は benchmark 実装、protocol、parity test、production code の変更を行わない。
- gold 未監査と No-Go fail-closed の境界を維持し、parity 修正前の結果を Go の根拠に昇格させない。

### 未確定

- parity 修正後に再生成された schema-check / benchmark 記録の内容。
- timed cue text parity の確認結果と実測値。
- gold の独立人手監査結果、gold gate の確定、Go / No-Go の最終結論。

## 2026-08-03 — 最終判断: final2 実測と fail-closed No-Go（final3 により superseded された履歴）

### ユーザーが明示した事実

- 実測前に、次の defect fixes を反映した。
  - production `deduplicate_progressive` と benchmark baseline の parity を一致させた。HTML entity を保持し、全 VTT を dedupe した後に range filter を適用する。
  - Whisper candidate は raw cue を維持し、candidate 側の dedupe は禁止した。
  - `whisper-cli` 1.9.1 の model / full JSON root、`--output-json-full`、`--no-vad` 非対応、macOS `time -l` の RSS 形式を修正した。
- 4本の実データ parity は 4/4 一致した。
- final2 実測は、q5（`large-v3-turbo-q5_0`）と turbo（`large-v3-turbo`）の各 cold / warm、4 case で全て成功した。
- paired median は q5 が `78.8772%`、turbo が `81.0149%` だった。
- glossary、cue、wall time、peak memory の各 technical gate は pass した。
- cold / warm の出力 SHA は一致した。
- production hash before / after は 15 ファイルで一致した。
- gold は `unverified_provisional` で、音声の独立人手監査は未実施である。
- モデルの cache 保存先は `~/Library/Caches/whisper.cpp/models/`、音声の cache 保存先は `~/Library/Caches/yt-live-kit/s9-benchmark/` である。
- final2 の raw reports は次の場所に保存した。
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-final2/report.json`
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/warm-final2/report.json`
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/cold-final2/report.json`
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/warm-final2/report.json`
- fixture fingerprint は `f7ca384959f5a52dcdde492b8ee25a390ed87f8131b194d6856232421e6785b0` である。

### このタスクで確定した判断

- technical gate は pass したが、gold の独立人手監査未実施により gold gate は fail-closed とし、最終判定は No-Go とする。
- adopted model は無しとする。
- production 経路は YouTube VTT fallback-only とする。
- final2 の数値・parity・hash・raw report・cache 保存先は、上記 fixture fingerprint に結び付く今回の benchmark 記録として扱う。
- 今回のログ担当による変更は `docs/benchmarks/s9-1-learning-log.md` のみとし、benchmark 実装・モデル操作・実測の再実行・コミットは行わない。

### 未確定

- gold の音声独立人手監査の実施結果。
- 監査済み gold に基づく最終数値と、No-Go を解除できるかどうか。
- adopted model と、YouTube VTT fallback-only から先へ進める最終 Go 判定。

## 2026-08-03 — final3 完了: 独立レビュー修正後の最終 benchmark

### ユーザーが明示した事実

- final3 は独立レビューで指摘された修正を反映した後に完了した。
- final3 の fixture fingerprint は `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718` である。
- paired median relative CER improvement は q5（`large-v3-turbo-q5_0`）が `0.7869427067818489`、turbo（`large-v3-turbo`）が `0.8084599344652964` だった。
- final3 の per-case candidate CER、cold / warm wall time、peak RSS と technical gate は次のとおり。全行の technical gate は pass した。

| model | case | candidate CER | cold / warm | peak RSS bytes | technical gate |
|---|---|---:|---:|---:|---|
| q5 | `lb4-clip002-short-proper-nouns` | 0.138710 | 2149 / 2149 ms | 903921664 | pass |
| q5 | `mkw-long-local-asr` | 0.389503 | 5170 / 5171 ms | 932773888 | pass |
| q5 | `cgal-proper-nouns` | 0.138889 | 4300 / 4307 ms | 919830528 | pass |
| q5 | `hpe-audio-variation` | 0.188889 | 2914 / 2915 ms | 920928256 | pass |
| turbo | `lb4-clip002-short-proper-nouns` | 0.100000 | 2268 / 2259 ms | 2002157568 | pass |
| turbo | `mkw-long-local-asr` | 0.469613 | 5622 / 5629 ms | 2017689600 | pass |
| turbo | `cgal-proper-nouns` | 0.128205 | 4234 / 4239 ms | 2007891968 | pass |
| turbo | `hpe-audio-variation` | 0.166667 | 2965 / 2965 ms | 2009677824 | pass |

- gate の失敗理由は `gold_not_audited` のみである。gold は音声の独立人手監査前で、数値は provisional とする。
- effective marker mapping は `[音楽]`、`[笑い]`、`[拍手]`、`[鼻息]`、`[咳払い]` の完全一致除外に固定した。
- viewer greeting は自動分類・自動除外しない。明示的な人手注記がない viewer greeting は raw cue に残す。
- Whisper timeout は 180 秒に固定した。
- cue anchor は最大 overlap、同率なら最も早い開始時刻で対応付ける規則と実装・unit testを固定した。
- `whisper-cli-json-full-v1` は `systeminfo`、`model`、`params`、`result`、`transcription` と各型を必須とする full-schema 契約へ修正し、実データ full JSON 16件の再読に成功した。
- production hash before / after は `checked_file_count=15` の全対象で一致し、`unchanged=true` だった。
- モデル cache は `~/Library/Caches/whisper.cpp/models/`、音声 cache は `~/Library/Caches/yt-live-kit/s9-benchmark/` に保存した。
- final3 の raw reports は次の場所に保存した。
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/cold-final3/report.json`
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/q5/warm-final3/report.json`
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/cold-final3/report.json`
  - `~/Library/Caches/yt-live-kit/s9-benchmark/runs/turbo/warm-final3/report.json`
- 比較 report は `docs/benchmarks/s9-1-report.json` と `docs/benchmarks/s9-1-report.md` に保存した。

### このタスクで確定した判断

- CER、glossary、cue、wall time、peak RSS の technical gate は pass したが、`gold_not_audited` が単独の fail-closed 条件となるため、final3 の最終判定は No-Go とする。
- adopted model は無しとする。
- production の字幕精査経路は既存 YouTube VTT の fallback-only とする。S9-3 の高精度モデル採用へは進めない。
- final3 の比較結果は上記 fixture fingerprint と raw report に結び付く証跡として、final2 より優先して扱う。

### 残余リスク

- gold は既存 VTT / transcript / ASS / cutplan を文脈利用した仮作成で、音声の独立人手監査をしていない。
- cold は OS page cache を消去した完全 cold ではなく、各 model の cold wave と warm wave を分離した reuse 観測である。
- mKwn / CGal / hPe は公開 YouTube の audio-only span 取得で、client / network 条件差が残る。
- candidate cue は raw のまま評価し、rolling VTT dedupe を候補へ適用していない。
- モデルは Git 管理外の手動 cache にあり、production 自動 download は実装していない。

### 未確定

- gold の音声独立人手監査の実施結果。
- 監査済み gold に基づく最終数値、adopted model、Go 判定。
