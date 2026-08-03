# S9-1 人手音声監査パケット

監査状態: **transcript content の operational reference 確認済み**。exact gold ではありません。
採用モデル: このパケット自体では決めません。固定 gate と比較 report の決定を参照してください。
benchmark ID: `s9-1-20260803`
human audit fingerprint: `9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263`

この文書は、2026-08-03 のユーザー自然文監査を構造化した canonical packet です。追加の定型フォーマット入力は要求しません。固定4音声、表示 transcript、監査範囲、exact と境界の未承認事項を同じ証跡へまとめています。

## ユーザー原文と監査範囲

- 原文: `4本とも文字起こしは概ね問題なし`
- 確認 context: 2026-08-03に4本のProvisional gold transcriptを開いて確認した後のユーザー所見。
- displayed transcript content: human reviewed / no material issue reported / accepted as operational benchmark reference
- glossary: not explicitly audited。個別用語の exact approval には昇格しません。
- character / punctuation exactness: not claimed。
- cue anchor exact milliseconds: unapproved。
- boundary/editorial outcomes: 既存の partial boundary audit を保持します。
- boundary auto adoption: prohibited。human boundary review: required。

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

今回の自然文監査は、4本の表示 transcript content に対する operational reference の確認として記録済みです。別の定型フォーマットを再入力しません。
「概ね問題なし」は文字単位・句読点単位の完全一致、glossary の個別 exact approval、cue anchor の正確なミリ秒を意味しません。
固定 fixture の範囲、音声 bytes / SHA-256、model identity、numeric gate は変更せず、境界の partial audit は独立 artifact として保持します。

## 境界・発話連続性の部分監査（2026-08-03）

監査者: `user` / 監査日: `2026-08-03`
boundary audit fingerprint: `0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1`
base fixture fingerprint: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`

これはユーザーが4本の固定音声を聴いた、開始境界・発話連続性だけの部分監査です。transcript 全文、glossary、cue anchor の正確な時刻は audited にしません。固定音声 span、video ID、range、bytes、SHA-256 は変更していません。

### 前回表示順と所見

| 前回表示順 | case ID | 自然文の所見 | expected editorial outcome |
|---:|---|---|---|
| 1 | `lb4-clip002-short-proper-nouns` | 前回表示順 case 1（lb4-clip002-short-proper-nouns）は「ほぼ問題ない」。 | `pass` |
| 2 | `hpe-audio-variation` | 前回表示順 case 2（hpe-audio-variation）は開始から約6秒まで意味ある発話がなく、ショート開始として不利、開始境界NG。 | `opening_trim_or_review_required` |
| 3 | `cgal-proper-nouns` | 前回表示順 case 3（cgal-proper-nouns）も開始から約6秒まで意味ある発話がなく、開始境界NG。背景音があっても意味ある発話がなければ編集上の無発話として扱う。 | `opening_trim_or_review_required` |
| 4 | `mkw-long-local-asr` | 前回表示順 case 4（mkw-long-local-asr）は開始直後に発話はあるが、約2秒から26秒までほぼ発話がなく、ショートとして致命的。 | `internal_gap_removal_or_review_required` |

### 部分監査の判定契約

- 背景音があっても意味ある発話がなければ、編集上は無発話として扱う。
- case 1 の `pass` は、今回確認した境界・発話連続性で追加処置なしという意味だけであり、transcript 全文、glossary、cue anchor、最終 short の品質承認ではない。
- 約6秒、約2〜26秒という所見は観察メモであり、production の普遍的な秒数閾値ではない。
- 開始直後に一言あれば通る単純な onset-only gate は使わない。
- Whisper timestamp を唯一の境界正本にせず、audio activity、cue、padding、human preview を併用する。
- 親候補の固定音声 span を良好と判定することと、最終 short cutplan から冒頭の無発話・長い内部無発話を残さないことは別判定である。

S9-1 の採否はこの packet だけでなく、同じ fixture を再計測した canonical comparison report の全 effective gate と tie-break で決めます。境界自動化はこの packet から採用しません。

## 音声ファイルの hash と size

| case ID | bytes | SHA-256 |
|---|---:|---|
| `lb4-clip002-short-proper-nouns` | 1818958 | `da80cdd933fb8738dc6ee7aa980b8ca64a4f8143d5fcbf7a971b058adb5c4687` |
| `hpe-audio-variation` | 2880078 | `1eb008e4a05f87877304474f9d65c4b22658a384ea84e6bff2165ff2b9f5d18e` |
| `cgal-proper-nouns` | 3840102 | `b3341ee5bbe1288e9919eacb3b94c4aa7fb49001dfd9d5ee8f39a48736e427ba` |
| `mkw-long-local-asr` | 5760114 | `aa8cd9dd543a3e6b84c25319aeed2c8fdc7bf1761aa45dcade65db179977d4ba` |

## Fixture case 1: `lb4-clip002-short-proper-nouns`

### 固定入力

- video ID: `LB4px1wRFnY`
- absolute range: `2853160–2910000 ms`
- source time: `00:47:33.160〜00:48:30.000`
- candidate: `clip_002`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/LB4px1wRFnY-2853160-2910000.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `1818958`
- SHA-256: `da80cdd933fb8738dc6ee7aa980b8ca64a4f8143d5fcbf7a971b058adb5c4687`

### Displayed transcript content の operational reference

cases.json の `gold.text` を表示 reference として転記した値です。ユーザーはこの case を含む4本について「4本とも文字起こしは概ね問題なし」と述べました。exact transcript とは主張しません。

- human review status: `human_reviewed_no_material_issue_reported`
- acceptance: `operational_benchmark_reference`

```text
いうのがあるからですね。なんか、僕の個人的な感覚ですけど、クロードの方が割と本質的な話ができるっていうか、なんて言ったらいいかな。やっぱりまだまだなんだかんだ自分はこういうのを作りたいんだよねの抽象的なところで壁打ちをしていって、なんでそれが作りたいの、みたいな本質的な問いを深めていくっていうのはクロードの方がやりやすいんですよね。で、コーデックスは本当に実装とかさせたらマジでエラー少なくやってくれるんで、理想はね、使い分けるがいいんでしょうけど、またその要件定義とかを書く時に自分でしっかりと頭の中整理しながら書ける人だったら、もうクロードいらずに実装だけコーデックスに任せるとかでもいいような気もするんすけどね。
```

### Glossary（個別 exact audit ではない）

| glossary label | fixed reference term | human audit status |
|---|---|---|
| `glossary-1` | `クロード` | not_explicitly_audited |
| `glossary-2` | `コーデックス` | not_explicitly_audited |
| `glossary-3` | `要件定義` | not_explicitly_audited |

### Character / punctuation exactness

`not_claimed`。自然文の「概ね問題なし」を文字単位・句読点単位の exact approval へ昇格しません。

### Cue anchor（正確なミリ秒の監査ではない）

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | human audit status |
|---|---|---|---|
| `anchor-1` | `2853160–2857000 ms` | `00:47:33.160〜00:47:37.000` | unapproved |
| `anchor-2` | `2857000–2865000 ms` | `00:47:37.000〜00:47:45.000` | unapproved |
| `anchor-3` | `2865000–2879000 ms` | `00:47:45.000〜00:47:59.000` | unapproved |
| `anchor-4` | `2879000–2897000 ms` | `00:47:59.000〜00:48:17.000` | unapproved |
| `anchor-5` | `2897000–2910000 ms` | `00:48:17.000〜00:48:30.000` | unapproved |

### Boundary/editorial dimension

`preserved_partial_boundary_audit`。開始境界・発話連続性の所見は [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) のまま保持します。境界の自動採用はせず human review を必須にします。

## Fixture case 2: `mkw-long-local-asr`

### 固定入力

- video ID: `mKwn-93gg90`
- absolute range: `1120000–1300000 ms`
- source time: `00:18:40.000〜00:21:40.000`
- candidate: `clip_001`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/mKwn-93gg90-1120-1300.16k.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `5760114`
- SHA-256: `aa8cd9dd543a3e6b84c25319aeed2c8fdc7bf1761aa45dcade65db179977d4ba`

### Displayed transcript content の operational reference

cases.json の `gold.text` を表示 reference として転記した値です。ユーザーはこの case を含む4本について「4本とも文字起こしは概ね問題なし」と述べました。exact transcript とは主張しません。

- human review status: `human_reviewed_no_material_issue_reported`
- acceptance: `operational_benchmark_reference`

```text
よくわかんないけど、これワンチャンローカルで使えんかな。えっと、OllamaとかでローカルLLMを立ち上げて、そのモデルを設定して使うことって可能です。これワンチャンローカルで行けるんだったらマジありだよな。うん。Together AIの料金がわかんねえな。Distil Large V3。あ、こっちか。ん？ 文字起こし。あ、こっちか。ああ。え、0.0015ドル。1分あたり0.0015ドル。だいぶ安いね。だいぶ安いんじゃない？ まあ、音質もだけど。Whisperの部分だけローカルにする。そういうのやってる人いそうだよな。そういうのこれやれなくはないんじゃないかな。いや、マジ楽しみっすね。メモリ8GB。あ、ストレージ512。ああ、いいっすね。なんか新しいガジェットっていいっすよね、マジで。ああ。はいはいはいはい。はい。なるほど。
```

### Glossary（個別 exact audit ではない）

| glossary label | fixed reference term | human audit status |
|---|---|---|
| `glossary-1` | `Ollama` | not_explicitly_audited |
| `glossary-2` | `ローカルLLM` | not_explicitly_audited |
| `glossary-3` | `Together AI` | not_explicitly_audited |
| `glossary-4` | `Distil Large V3` | not_explicitly_audited |
| `glossary-5` | `Whisper` | not_explicitly_audited |

### Character / punctuation exactness

`not_claimed`。自然文の「概ね問題なし」を文字単位・句読点単位の exact approval へ昇格しません。

### Cue anchor（正確なミリ秒の監査ではない）

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | human audit status |
|---|---|---|---|
| `anchor-1` | `1120000–1140000 ms` | `00:18:40.000〜00:19:00.000` | unapproved |
| `anchor-2` | `1140000–1165000 ms` | `00:19:00.000〜00:19:25.000` | unapproved |
| `anchor-3` | `1165000–1190000 ms` | `00:19:25.000〜00:19:50.000` | unapproved |
| `anchor-4` | `1190000–1220000 ms` | `00:19:50.000〜00:20:20.000` | unapproved |
| `anchor-5` | `1220000–1260000 ms` | `00:20:20.000〜00:21:00.000` | unapproved |
| `anchor-6` | `1260000–1300000 ms` | `00:21:00.000〜00:21:40.000` | unapproved |

### Boundary/editorial dimension

`preserved_partial_boundary_audit`。開始境界・発話連続性の所見は [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) のまま保持します。境界の自動採用はせず human review を必須にします。

## Fixture case 3: `cgal-proper-nouns`

### 固定入力

- video ID: `CGalA8SISPE`
- absolute range: `4220000–4340000 ms`
- source time: `01:10:20.000〜01:12:20.000`
- candidate: `clip_003`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/CGalA8SISPE-4220-4340.16k.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `3840102`
- SHA-256: `b3341ee5bbe1288e9919eacb3b94c4aa7fb49001dfd9d5ee8f39a48736e427ba`

### Displayed transcript content の operational reference

cases.json の `gold.text` を表示 reference として転記した値です。ユーザーはこの case を含む4本について「4本とも文字起こしは概ね問題なし」と述べました。exact transcript とは主張しません。

- human review status: `human_reviewed_no_material_issue_reported`
- acceptance: `operational_benchmark_reference`

```text
最近のGPUも高性能になってきてるので、そんなハードなゲームでなければそこそこ楽しめるのでは。行けるんすかね。あ、これか。DirectXはMicrosoftが開発したWindows向けのゲームや映像音声処理で使われるAPIの集合です。APIの集合です。DirectXを使うとゲーム側はPCごとに違うCPUやGPUの細かい違いをあまり意識せずにグラフィック処理ができます。WindowsのPCゲームではDirectXが前提になっていることが多く、ハードウェアの性能を効率よく引き出すために使われます。うーん。あ、Steamのゲーム説明でDX12対応と書いてあれば、そのゲームがそのAPI世代を前提にグラフィック処理を行うという。はあ、そういうのがあんだ。すげえな。すげえ世界だな。あ、スケルトンは初代iMac。これもっと前、見たことないっすもん。こんなん全然見たことない。すごいっすね。これがいわゆるあれですよね。これこの上にディスプレイをまた自分でつけてってことっすよね。そういうことっすよね。Apple IIは相当高価な商品だったはず。
```

### Glossary（個別 exact audit ではない）

| glossary label | fixed reference term | human audit status |
|---|---|---|
| `glossary-1` | `DirectX` | not_explicitly_audited |
| `glossary-2` | `Microsoft` | not_explicitly_audited |
| `glossary-3` | `Windows` | not_explicitly_audited |
| `glossary-4` | `Steam` | not_explicitly_audited |
| `glossary-5` | `DX12` | not_explicitly_audited |
| `glossary-6` | `iMac` | not_explicitly_audited |
| `glossary-7` | `Apple II` | not_explicitly_audited |

### Character / punctuation exactness

`not_claimed`。自然文の「概ね問題なし」を文字単位・句読点単位の exact approval へ昇格しません。

### Cue anchor（正確なミリ秒の監査ではない）

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | human audit status |
|---|---|---|---|
| `anchor-1` | `4220000–4238000 ms` | `01:10:20.000〜01:10:38.000` | unapproved |
| `anchor-2` | `4238000–4260000 ms` | `01:10:38.000〜01:11:00.000` | unapproved |
| `anchor-3` | `4260000–4285000 ms` | `01:11:00.000〜01:11:25.000` | unapproved |
| `anchor-4` | `4285000–4310000 ms` | `01:11:25.000〜01:11:50.000` | unapproved |
| `anchor-5` | `4310000–4340000 ms` | `01:11:50.000〜01:12:20.000` | unapproved |

### Boundary/editorial dimension

`preserved_partial_boundary_audit`。開始境界・発話連続性の所見は [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) のまま保持します。境界の自動採用はせず human review を必須にします。

## Fixture case 4: `hpe-audio-variation`

### 固定入力

- video ID: `hPeRSA9YVIM`
- absolute range: `8640000–8730000 ms`
- source time: `02:24:00.000〜02:25:30.000`
- candidate: `clip_003`
- audio path: `/Users/ryukouokumura/Library/Caches/yt-live-kit/s9-benchmark/hPeRSA9YVIM-8640-8730.16k.wav`
- audio format: `16,000 Hz / mono / 16-bit PCM WAV`
- bytes: `2880078`
- SHA-256: `1eb008e4a05f87877304474f9d65c4b22658a384ea84e6bff2165ff2b9f5d18e`

### Displayed transcript content の operational reference

cases.json の `gold.text` を表示 reference として転記した値です。ユーザーはこの case を含む4本について「4本とも文字起こしは概ね問題なし」と述べました。exact transcript とは主張しません。

- human review status: `human_reviewed_no_material_issue_reported`
- acceptance: `operational_benchmark_reference`

```text
MacでHHKBを最大限に利用するには、HHKBの物理的なファンクションキーとは別にmacOS固有のファンクションキーをHHKB上のどこかのキーに割り当てるのが最もスマートな方法です。これがしたいんだよね。そう、それがしたいんだよね。ただ、ファンクションキーを押してるとこうなるよってことなんだよね。まあ、みんなこんな感じのことをやってるのか。すごいな。面白。
```

### Glossary（個別 exact audit ではない）

| glossary label | fixed reference term | human audit status |
|---|---|---|
| `glossary-1` | `HHKB` | not_explicitly_audited |
| `glossary-2` | `Mac` | not_explicitly_audited |
| `glossary-3` | `macOS` | not_explicitly_audited |
| `glossary-4` | `ファンクションキー` | not_explicitly_audited |

### Character / punctuation exactness

`not_claimed`。自然文の「概ね問題なし」を文字単位・句読点単位の exact approval へ昇格しません。

### Cue anchor（正確なミリ秒の監査ではない）

ラベルは fixture の anchor ID です。時刻は video の絶対時刻で、音声ファイル内の相対時刻ではありません。

| anchor label | 絶対 range | source time | human audit status |
|---|---|---|---|
| `anchor-1` | `8640000–8660000 ms` | `02:24:00.000〜02:24:20.000` | unapproved |
| `anchor-2` | `8660000–8680000 ms` | `02:24:20.000〜02:24:40.000` | unapproved |
| `anchor-3` | `8680000–8710000 ms` | `02:24:40.000〜02:25:10.000` | unapproved |
| `anchor-4` | `8710000–8730000 ms` | `02:25:10.000〜02:25:30.000` | unapproved |

### Boundary/editorial dimension

`preserved_partial_boundary_audit`。開始境界・発話連続性の所見は [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json) のまま保持します。境界の自動採用はせず human review を必須にします。

## 今回の監査記録と次手順

1. ユーザー原文を改変せず、固定4 caseへ statement scope と表示順を対応付けました。
2. displayed transcript content の operational reference と、glossary / character / punctuation / cue / boundary の状態を別 dimension に保存しました。
3. `s9-1-cases.json` の gold status、音声 path、bytes、SHA-256、video ID、absolute range は変更していません。
4. 同じ cold / warm 手順で q5 / turbo を再測定し、numeric gate、operational reference gate、tie-break を canonical report へ固定します。
5. A で Go になっても boundary の自動採用はせず、人の preview と区間確認を downstream の必須条件として維持します。

## 関連証跡

- [`s9-1-cases.json`](./s9-1-cases.json): 固定 fixture と provisional gold の正本
- [`s9-1-human-audit-v2.json`](./s9-1-human-audit-v2.json): 自然文監査の strict artifact と fingerprint
- [`s9-1-boundary-audit.json`](./s9-1-boundary-audit.json): 境界・発話連続性だけの strict audit artifact
- [`s9-1-protocol.md`](./s9-1-protocol.md): 同じ評価契約・gate・再現手順
- [`s9-1-report.md`](./s9-1-report.md): operational transcript reference の canonical decision（q5採用）。exact dimension は未承認、boundary automation は不採用
- [`s9-1-report.json`](./s9-1-report.json): 機械可読な現在の gate status
