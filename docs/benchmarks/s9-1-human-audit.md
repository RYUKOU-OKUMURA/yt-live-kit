# S9-1 人手音声監査パケット

監査状態: **未完了**。gold は全4 case とも `unverified_provisional` です。
採用モデル: **未決定**。現行の S9-1 は No-Go のままです。
benchmark ID: `s9-1-20260803`

この文書は、4つの固定音声 span を人が直接聴いて provisional gold を承認または訂正するための準備物です。ユーザーの返答前に、gold を監査済みとして扱ったり、S9-1 の Done、進捗、採用モデル判定を変更したりしません。

## 先に読む注意

- 音声を実際に聴かず承認しないでください。
- AI出力同士の比較だけでは監査完了にしないでください。
- 4 case 全件が必要です。1件でも未回答、保留、訂正未確定があれば人手監査完了にしません。
- provisional gold は既存 transcript、VTT、ASS、cutplan の文脈から作った仮値です。音声を聴く前の正解ではありません。
- viewer greeting やフィラーを、音声確認なしに自動で削除・除外しないでください。

## 推奨確認順と所要時間

対象は4 case、音声 span の合計は `07:26.840`（約7.5分）です。短いものから確認し、再生・巻き戻し・返答記入を含めた監査の目安は約11〜16分です。

| 順 | case ID | video ID | 絶対 range | 音声 span | 確認目安 |
|---:|---|---|---|---:|---|
| 1 | `lb4-clip002-short-proper-nouns` | `LB4px1wRFnY` | `2853160–2910000 ms` （00:47:33.160〜00:48:30.000） | 00:56.840 | 2〜3分: 短い。固有名詞3件と全文の聞き取り |
| 2 | `hpe-audio-variation` | `hPeRSA9YVIM` | `8640000–8730000 ms` （02:24:00.000〜02:25:30.000） | 01:30.000 | 2〜3分: 音声条件の違い。HHKB / Mac / macOS |
| 3 | `cgal-proper-nouns` | `CGalA8SISPE` | `4220000–4340000 ms` （01:10:20.000〜01:12:20.000） | 02:00.000 | 3〜4分: 固有名詞7件と cue anchor の境界 |
| 4 | `mkw-long-local-asr` | `mKwn-93gg90` | `1120000–1300000 ms` （00:18:40.000〜00:21:40.000） | 03:00.000 | 4〜6分: 最長。フィラーと用語5件を含む |

## 音声ファイル絶対 path 一覧

以下の4本だけを監査対象にします。いずれも cache 内の 16,000 Hz・mono・16-bit PCM WAV です。

- `lb4-clip002-short-proper-nouns`: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/LB4px1wRFnY-2853160-2910000.wav`
- `hpe-audio-variation`: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/hPeRSA9YVIM-8640-8730.16k.wav`
- `cgal-proper-nouns`: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/CGalA8SISPE-4220-4340.16k.wav`
- `mkw-long-local-asr`: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/mKwn-93gg90-1120-1300.16k.wav`

## 監査方法

1. 上の順番で音声ファイルを1本ずつ最後まで聴きます。macOS では次の形式で再生できます。
   `afplay "音声ファイルの絶対 path"`
2. case ごとの provisional gold transcript と照合し、聞こえない、足りない、順序が違う、固有名詞が違う箇所を訂正文にします。
3. glossary は表記を一語ずつ確認します。音声で判断できない場合は承認せず、保留または訂正として返します。
4. cue anchor は `anchor-1` からのラベルと絶対時刻を確認します。時刻またはラベルが違う場合は anchor ID と訂正値を返します。
5. 下の最小フォーマットを case ごとに1回ずつ、合計4回返します。

## 音声ファイルの hash と size

| case ID | bytes | SHA-256 |
|---|---:|---|
| `lb4-clip002-short-proper-nouns` | 1818958 | `da80cdd933fb8738dc6ee7aa980b8ca64a4f8143d5fcbf7a971b058adb5c4687` |
| `hpe-audio-variation` | 2880078 | `1eb008e4a05f87877304474f9d65c4b22658a384ea84e6bff2165ff2b9f5d18e` |
| `cgal-proper-nouns` | 3840102 | `b3341ee5bbe1288e9919eacb3b94c4aa7fb49001dfd9d5ee8f39a48736e427ba` |
| `mkw-long-local-asr` | 5760114 | `aa8cd9dd543a3e6b84c25319aeed2c8fdc7bf1761aa45dcade65db179977d4ba` |

## Case 1: `lb4-clip002-short-proper-nouns`

### 固定入力

- video ID: `LB4px1wRFnY`
- absolute range: `2853160–2910000 ms`
- source time: `00:47:33.160〜00:48:30.000`
- candidate: `clip_002`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/LB4px1wRFnY-2853160-2910000.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `1818958`
- SHA-256: `da80cdd933fb8738dc6ee7aa980b8ca64a4f8143d5fcbf7a971b058adb5c4687`

### Provisional gold transcript

cases.json の `gold.text` を機械的に転記した値です。音声監査前の provisional です。

```text
いうのがあるからですね。なんか、僕の個人的な感覚ですけど、クロードの方が割と本質的な話ができるっていうか、なんて言ったらいいかな。やっぱりまだまだなんだかんだ自分はこういうのを作りたいんだよねの抽象的なところで壁打ちをしていって、なんでそれが作りたいの、みたいな本質的な問いを深めていくっていうのはクロードの方がやりやすいんですよね。で、コーデックスは本当に実装とかさせたらマジでエラー少なくやってくれるんで、理想はね、使い分けるがいいんでしょうけど、またその要件定義とかを書く時に自分でしっかりと頭の中整理しながら書ける人だったら、もうクロードいらずに実装だけコーデックスに任せるとかでもいいような気もするんすけどね。
```

### Glossary

| glossary label | provisional expected | 人手確認 |
|---|---|---|
| `glossary-1` | `クロード` | 承認 / 訂正 |
| `glossary-2` | `コーデックス` | 承認 / 訂正 |
| `glossary-3` | `要件定義` | 承認 / 訂正 |

### Cue anchor

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | 人手確認 |
|---|---|---|---|
| `anchor-1` | `2853160–2857000 ms` | `00:47:33.160〜00:47:37.000` | 承認 / 訂正 |
| `anchor-2` | `2857000–2865000 ms` | `00:47:37.000〜00:47:45.000` | 承認 / 訂正 |
| `anchor-3` | `2865000–2879000 ms` | `00:47:45.000〜00:47:59.000` | 承認 / 訂正 |
| `anchor-4` | `2879000–2897000 ms` | `00:47:59.000〜00:48:17.000` | 承認 / 訂正 |
| `anchor-5` | `2897000–2910000 ms` | `00:48:17.000〜00:48:30.000` | 承認 / 訂正 |

## Case 2: `mkw-long-local-asr`

### 固定入力

- video ID: `mKwn-93gg90`
- absolute range: `1120000–1300000 ms`
- source time: `00:18:40.000〜00:21:40.000`
- candidate: `clip_001`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/mKwn-93gg90-1120-1300.16k.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `5760114`
- SHA-256: `aa8cd9dd543a3e6b84c25319aeed2c8fdc7bf1761aa45dcade65db179977d4ba`

### Provisional gold transcript

cases.json の `gold.text` を機械的に転記した値です。音声監査前の provisional です。

```text
よくわかんないけど、これワンチャンローカルで使えんかな。えっと、OllamaとかでローカルLLMを立ち上げて、そのモデルを設定して使うことって可能です。これワンチャンローカルで行けるんだったらマジありだよな。うん。Together AIの料金がわかんねえな。Distil Large V3。あ、こっちか。ん？ 文字起こし。あ、こっちか。ああ。え、0.0015ドル。1分あたり0.0015ドル。だいぶ安いね。だいぶ安いんじゃない？ まあ、音質もだけど。Whisperの部分だけローカルにする。そういうのやってる人いそうだよな。そういうのこれやれなくはないんじゃないかな。いや、マジ楽しみっすね。メモリ8GB。あ、ストレージ512。ああ、いいっすね。なんか新しいガジェットっていいっすよね、マジで。ああ。はいはいはいはい。はい。なるほど。
```

### Glossary

| glossary label | provisional expected | 人手確認 |
|---|---|---|
| `glossary-1` | `Ollama` | 承認 / 訂正 |
| `glossary-2` | `ローカルLLM` | 承認 / 訂正 |
| `glossary-3` | `Together AI` | 承認 / 訂正 |
| `glossary-4` | `Distil Large V3` | 承認 / 訂正 |
| `glossary-5` | `Whisper` | 承認 / 訂正 |

### Cue anchor

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | 人手確認 |
|---|---|---|---|
| `anchor-1` | `1120000–1140000 ms` | `00:18:40.000〜00:19:00.000` | 承認 / 訂正 |
| `anchor-2` | `1140000–1165000 ms` | `00:19:00.000〜00:19:25.000` | 承認 / 訂正 |
| `anchor-3` | `1165000–1190000 ms` | `00:19:25.000〜00:19:50.000` | 承認 / 訂正 |
| `anchor-4` | `1190000–1220000 ms` | `00:19:50.000〜00:20:20.000` | 承認 / 訂正 |
| `anchor-5` | `1220000–1260000 ms` | `00:20:20.000〜00:21:00.000` | 承認 / 訂正 |
| `anchor-6` | `1260000–1300000 ms` | `00:21:00.000〜00:21:40.000` | 承認 / 訂正 |

## Case 3: `cgal-proper-nouns`

### 固定入力

- video ID: `CGalA8SISPE`
- absolute range: `4220000–4340000 ms`
- source time: `01:10:20.000〜01:12:20.000`
- candidate: `clip_003`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/CGalA8SISPE-4220-4340.16k.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `3840102`
- SHA-256: `b3341ee5bbe1288e9919eacb3b94c4aa7fb49001dfd9d5ee8f39a48736e427ba`

### Provisional gold transcript

cases.json の `gold.text` を機械的に転記した値です。音声監査前の provisional です。

```text
最近のGPUも高性能になってきてるので、そんなハードなゲームでなければそこそこ楽しめるのでは。行けるんすかね。あ、これか。DirectXはMicrosoftが開発したWindows向けのゲームや映像音声処理で使われるAPIの集合です。APIの集合です。DirectXを使うとゲーム側はPCごとに違うCPUやGPUの細かい違いをあまり意識せずにグラフィック処理ができます。WindowsのPCゲームではDirectXが前提になっていることが多く、ハードウェアの性能を効率よく引き出すために使われます。うーん。あ、Steamのゲーム説明でDX12対応と書いてあれば、そのゲームがそのAPI世代を前提にグラフィック処理を行うという。はあ、そういうのがあんだ。すげえな。すげえ世界だな。あ、スケルトンは初代iMac。これもっと前、見たことないっすもん。こんなん全然見たことない。すごいっすね。これがいわゆるあれですよね。これこの上にディスプレイをまた自分でつけてってことっすよね。そういうことっすよね。Apple IIは相当高価な商品だったはず。
```

### Glossary

| glossary label | provisional expected | 人手確認 |
|---|---|---|
| `glossary-1` | `DirectX` | 承認 / 訂正 |
| `glossary-2` | `Microsoft` | 承認 / 訂正 |
| `glossary-3` | `Windows` | 承認 / 訂正 |
| `glossary-4` | `Steam` | 承認 / 訂正 |
| `glossary-5` | `DX12` | 承認 / 訂正 |
| `glossary-6` | `iMac` | 承認 / 訂正 |
| `glossary-7` | `Apple II` | 承認 / 訂正 |

### Cue anchor

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | 人手確認 |
|---|---|---|---|
| `anchor-1` | `4220000–4238000 ms` | `01:10:20.000〜01:10:38.000` | 承認 / 訂正 |
| `anchor-2` | `4238000–4260000 ms` | `01:10:38.000〜01:11:00.000` | 承認 / 訂正 |
| `anchor-3` | `4260000–4285000 ms` | `01:11:00.000〜01:11:25.000` | 承認 / 訂正 |
| `anchor-4` | `4285000–4310000 ms` | `01:11:25.000〜01:11:50.000` | 承認 / 訂正 |
| `anchor-5` | `4310000–4340000 ms` | `01:11:50.000〜01:12:20.000` | 承認 / 訂正 |

## Case 4: `hpe-audio-variation`

### 固定入力

- video ID: `hPeRSA9YVIM`
- absolute range: `8640000–8730000 ms`
- source time: `02:24:00.000〜02:25:30.000`
- candidate: `clip_003`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/hPeRSA9YVIM-8640-8730.16k.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `2880078`
- SHA-256: `1eb008e4a05f87877304474f9d65c4b22658a384ea84e6bff2165ff2b9f5d18e`

### Provisional gold transcript

cases.json の `gold.text` を機械的に転記した値です。音声監査前の provisional です。

```text
MacでHHKBを最大限に利用するには、HHKBの物理的なファンクションキーとは別にmacOS固有のファンクションキーをHHKB上のどこかのキーに割り当てるのが最もスマートな方法です。これがしたいんだよね。そう、それがしたいんだよね。ただ、ファンクションキーを押してるとこうなるよってことなんだよね。まあ、みんなこんな感じのことをやってるのか。すごいな。面白。
```

### Glossary

| glossary label | provisional expected | 人手確認 |
|---|---|---|
| `glossary-1` | `HHKB` | 承認 / 訂正 |
| `glossary-2` | `Mac` | 承認 / 訂正 |
| `glossary-3` | `macOS` | 承認 / 訂正 |
| `glossary-4` | `ファンクションキー` | 承認 / 訂正 |

### Cue anchor

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | 人手確認 |
|---|---|---|---|
| `anchor-1` | `8640000–8660000 ms` | `02:24:00.000〜02:24:20.000` | 承認 / 訂正 |
| `anchor-2` | `8660000–8680000 ms` | `02:24:20.000〜02:24:40.000` | 承認 / 訂正 |
| `anchor-3` | `8680000–8710000 ms` | `02:24:40.000〜02:25:10.000` | 承認 / 訂正 |
| `anchor-4` | `8710000–8730000 ms` | `02:25:10.000〜02:25:30.000` | 承認 / 訂正 |

## ユーザー返答の最小フォーマット

以下を case ごとに4回返してください。承認の場合は `承認`、訂正の場合は全文または訂正対象が特定できる値を書いてください。

```text
case ID: lb4-clip002-short-proper-nouns
transcript: 承認 / 訂正文
glossary: 承認 / 訂正
cue anchor: 承認 / 訂正
監査者:
監査日: YYYY-MM-DD
```

訂正時は、transcript は訂正後の全文、glossary は用語ごとの期待表記、cue anchor は `anchor-ID: 絶対 range / ラベル` の形式で返してください。4 case 全件について transcript、glossary、cue anchor の3項目を埋めてください。

## 監査後の次手順

1. ユーザーの4 case 分の返答を受け取り、監査者・監査日と、承認または訂正の根拠を記録します。返答前に `audit_status` を変更しません。
2. 訂正があれば `s9-1-cases.json` の gold.text、gold.glossary、gold.cue_anchors_ms へ人手の結果だけを反映します。音声 path、bytes、SHA-256、video ID、absolute range は固定したままにします。
3. 4 case 全件の監査がそろった後、fixture fingerprint を再計算し、同じ4音声の hash / size と protocol の normalization、cue rule、wall time、memory gate が変わっていないことを確認します。
4. [`s9-1-protocol.md`](./s9-1-protocol.md) の同じ cold / warm 手順で、固定した4 case と候補2モデルを再測定します。gold だけを監査結果へ更新し、音声 span や評価 gate を都合よく変更しません。
5. paired median CER 相対改善、glossary exact match 非悪化、cue 欠落・重複率、cold / warm wall time、peak memory、gold audit 必須条件を同じ gate で判定します。
6. 全 gate を満たした場合だけ採用モデルと設定を決めます。未達なら No-Go とし、既存 YouTube VTT の fallback-only を維持します。人手監査済みでも自動的に Go にはしません。

再測定と採用判定が完了するまで、このパケット自体は S9-1 の Done 証跡ではなく、監査の準備済み証跡として扱います。

## 関連証跡

- [`s9-1-cases.json`](./s9-1-cases.json): 固定 fixture と provisional gold の正本
- [`s9-1-protocol.md`](./s9-1-protocol.md): 同じ評価契約・gate・再現手順
- [`s9-1-report.md`](./s9-1-report.md): 現在の provisional 指標と No-Go
- [`s9-1-report.json`](./s9-1-report.json): 機械可読な現在の gate status
