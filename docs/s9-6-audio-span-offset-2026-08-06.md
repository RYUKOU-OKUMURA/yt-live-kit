# 選択区間音声の開始ずれ調査（2026-08-06）

## 要約

S9-6 の人 preview でユーザーが指摘した「発話とテロップのタイミングが合っていない」の原因を調査した。原因は telop 生成側ではなく、**選択区間の音声取得が要求範囲より数秒早く始まっている**ことである。

`prepare_audio_span` は yt-dlp `--download-sections` で音声を取得し、`atrim=end_sample=N` で正規化する。この正規化は**末尾しか切らない**ため、`--download-sections` が keyframe 手前から出力した先頭の余剰がそのまま残る。結果として、

- 音声の**長さ**は要求どおり（frames 検証は通る）
- 音声の**内容**は要求範囲より数秒前から始まる
- cue 時刻は要求範囲の絶対オフセットで採番される

ため、artifact の cue 時刻が実際の発話より数秒ずれる。テロップはこの cue 時刻を継承するので、ずれがそのまま完成ショートに出る。

実測したずれは **−6.06 秒 〜 −9.94 秒**で、一定ではない（keyframe 間隔に依存する）。

## 検証した実体

| 宣言範囲 | 実際に含まれる範囲 | ずれ | 動画 |
| --- | --- | --- | --- |
| 8640.000 – 8690.000 | 8630.06 – 8680.06 | **−9.94 秒** | `hPeRSA9YVIM` |
| 1179.000 – 1195.000 | 1169.94 – 1185.94 | **−9.06 秒** | `mKwn-93gg90` |
| 1146.000 – 1154.000 | 1139.94 – 1147.94 | **−6.06 秒** | `mKwn-93gg90` |

判定方法は、cache 済み wav と、ローカル source mp4 から精密シーク（`-i` の後に `-ss`）で切り出した候補との無音パターン一致である。一致誤差は 0.004〜0.056 秒だった。

### 例: `mKwn-93gg90` 宣言 1179–1195

cache wav の無音開始位置（秒）:

```
0  3.271937  9.573  11.375813  14.058125
```

source 1170 秒起点 16 秒の無音開始位置（秒）:

```
0  3.328187  9.617875  11.420687  14.102813
```

source 1179 秒起点では一致しない:

```
0.617875  2.420688  5.102812  7.093375  8.142625  10.47425  13.403937
```

### 絶対時刻の正解との突き合わせ

`mKwn-93gg90` の 1140 秒起点 60 秒を精密シークで文字起こしした正解:

| 絶対時刻 | 発話 |
| --- | --- |
| 1140.00 – 1154.76 | オラマとかでローカルLLMを立ち上げて、そのモデルを設定して使うことって可能です? |
| 1170.00 – 1174.00 | これワンチャンローカルで行けるんだったら、マジアリだよなぁ。 |
| 1178.00 – 1182.00 | トゥギャザーAIの料金がわかんねぇなぁ。 |
| 1182.00 – 1187.00 | ウィスパーラージ、V3、あ、こっちか。 |
| 1187.00 – 1188.00 | ん? |
| 1188.00 – 1191.00 | 文字起こし。あ、こっちか。 |

production artifact `bedb198a442282ad9c5f39b0e2ed664368f15b22edb15565e4ad55488f6fcf09` の主張:

| artifact の cue | 実際の時刻 | ずれ |
| --- | --- | --- |
| 1179 – 1183「これワンチャンローカルで行けるんだったらマジアリだよなぁ。」 | 1170 – 1174 | −9 秒 |
| 1187 – 1191「Together AIの料金がわかんねぇなぁ。」 | 1178 – 1182 | −9 秒 |
| 1191 – 1195「Wisper Large V3…あ、こっちか。」 | 1182 – 1187 | −9 秒 |

## 該当コード

`src/yt_live_kit/services/ytdlp.py`

- `_audio_download_argv`（933 行〜）: `--download-sections` に `*START-END` を渡す。`--force-keyframes-at-cuts` は指定していないため、出力は要求開始位置より前の keyframe から始まりうる。
- `_audio_normalize_argv`（419 行〜）: 正規化フィルタが `aresample=16000,atrim=end_sample={requested_frames}` である。`atrim` に `start_sample` が無いため、**先頭の余剰は切られず末尾だけが切られる**。

```
"-af",
(
    f"aresample={_AUDIO_FORMAT_SETTINGS['sample_rate']},"
    f"atrim=end_sample={requested_frames}"
),
```

`_requested_audio_frames` は `requested_duration_ms × 16000 ÷ 1000` を返し、検証も frames 数と duration しか見ない。開始位置の整合は検証されていない。cache metadata は `requested_start_ms: 1179000` / `duration_ms: 16000` / `frames: 256000` を記録しており、**長さ検証は通るのに内容がずれている**状態を検出できない。

## HF3 との関係

HF3（2026-08-04）は「`--download-sections` 出力が要求区間を末尾方向へ 3,994〜8,994 ms 超過」と記録し、`atrim=end_sample` による長さ正規化で対処している。今回の実測では余剰は**先頭側**に出ており、末尾のみを切る正規化では開始ずれが残る。HF3 の超過量（約 4〜9 秒）と今回のずれ量（約 6〜10 秒）は同じオーダーであり、同一現象を末尾超過と解釈していた可能性が高い。

## 影響範囲

- **影響あり**: production の「選択区間の高精度字幕」経路（`prepare_audio_span` を使う全区間）。既存の高精度 artifact と、それを継承したテロップ時刻はすべて疑わしい。
- **影響なし**: T1-1 の benchmark harness。`benchmarks/t1/annotation_packet.py` はローカル source mp4 に対して `-ss <start> -accurate_seek -i` で切り出しており、開始位置は正確である。**T1-1 の No-Go 判定はこの不具合の影響を受けない。**
- **影響なし**: 既存 VTT 経路（`ja.vtt` は変更されない）。

## 副作用として観測された事象

1. **テロップの欠落**: `mKwn-93gg90` の完成ショートで、出力 12.0〜16.0 秒に発話があるのにテロップが 1 行も無い。artifact の cue が 1183–1187 に存在しないためである。
2. **短区間での timestamp 劣化**: 16 秒の区間を単独で whisper-cli にかけると `0.000 → 16.000` の 1 セグメントしか返らない。同じ音声を含む 60 秒区間では 18.000→21.700、22.260→26.160 のように自然な粒度になる。区間を短く切るほど timestamp が粗くなる。
3. **cue 内の比例分割**: Codex は 1 つの粗い cue を複数のテロップ行に分けるとき、cue の長さを比例配分している（例: 8 秒 cue → 1146.0–1150.0 と 1150.0–1154.0）。実発話とは無関係な時刻になる。

## この調査で判明した既存記録の誤り

`docs/benchmarks/s9-6-acceptance.md` に記録した `hpe-audio-variation` の音声活動実測は、**ずれた cache wav を対象に測っていた**ため、cache 座標の値をそのまま候補冒頭からの経過時間として記録していた点が誤りである。

- cache 座標での記録: 冒頭 1.194 秒無音 → 4.115–16.131 秒に 12 秒の無発話 → 持続発話開始は 16.1 秒
- cache は宣言より 9.95 秒早く始まるので、絶対時刻に直すと持続発話開始は 8630.05 + 16.131 = **8646.18 秒**

### 2026-08-06 の一次訂正そのものが誤りだった

本文書の初版では「正（source 8640 起点、精密シーク）: 0 – 28.604 秒が無音」と記録したが、**これは silencedetect 出力の読み違いである**。`silence_start` / `silence_end` を対で読まず、30 秒以内で最後に現れた `silence_end: 28.546` を「そこまで無音」と解釈していた。

修正後の経路で `hPeRSA9YVIM` を 8640.000 起点・精密シークで切り出し、再測定した確定値は次のとおりである。

| 候補冒頭からの経過 | 状態 |
| --- | --- |
| 0 – 6.19 秒 | 無音（RMS −68〜−55 dBFS） |
| 6.19 – 33.5 秒 | 連続発話（RMS −25〜−35 dBFS）。0.5〜1.8 秒の自然な間を挟む |
| 33.5 – 38.8 秒 | 5.33 秒の無発話 |
| 38.8 – 45.9 秒 | 発話 |
| 45.9 – 50 秒 | 無音 |

絶対時刻では持続発話開始は **8646.19 秒（02:24:06.2）** であり、cache 座標から換算した 8646.18 秒と一致する。つまり **2026-08-04 の元の実測は絶対時刻としては正しく、8-06 の「28.6 秒無音」訂正だけが誤り**だった。

したがって、

- 候補冒頭の無発話は約 **6.2 秒**であり、境界監査の「開始から約 6 秒まで意味ある発話がない」という所見と一致する
- 当方が設定した cut 開始 02:24:16 は無発話区間の内側ではなく、**発話の途中**である（16 秒地点の RMS は −28.4 dBFS）。誤りであることは変わらないが、理由が違う
- opening trim の妥当な開始位置は **02:24:06.2 以降**である

`hpe-audio-variation` のショートは生成しておらず（`short_8ad3b1c9bd32.mp4` は 2026-08-04 生成の別物）、誤った区間で人 preview は行っていない。

`mkw-long-local-asr` 側の実測は source 由来の精密シークで行っており、この誤りの影響を受けない。

## 再現手順

```bash
FF=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
SRC=data/mKwn-93gg90/clips/source/mKwn-93gg90.mp4
W=data/mKwn-93gg90/transcripts/audio_cache/3f6805109af314a88730457777c43b9f5d458a168302699bb4c4b68029a8f2e6.wav

# cache 済み音声の無音パターン
$FF -hide_banner -nostats -i "$W" -af "silencedetect=noise=-35dB:d=0.4" -f null -

# 宣言どおり 1179 起点で切り出すと一致しない
$FF -hide_banner -loglevel error -i "$SRC" -ss 1179 -t 16 -vn -ac 1 -ar 16000 -c:a pcm_s16le -y /tmp/c1179.wav
$FF -hide_banner -nostats -i /tmp/c1179.wav -af "silencedetect=noise=-35dB:d=0.4" -f null -

# 1170 起点なら一致する
$FF -hide_banner -loglevel error -i "$SRC" -ss 1170 -t 16 -vn -ac 1 -ar 16000 -c:a pcm_s16le -y /tmp/c1170.wav
$FF -hide_banner -nostats -i /tmp/c1170.wav -af "silencedetect=noise=-35dB:d=0.4" -f null -
```

## 実施した修正（2026-08-06、ユーザー承認済み）

ユーザーは**修正方針 1**（ローカル source からの精密切り出し）を選び、fallback にも `--force-keyframes-at-cuts` を足す方を選んだ。実装は `272d5f3` と `3455910` である。

### 1. 取得経路

`prepare_audio_span` は cache miss のとき、まず `data/{video_id}/clips/source/` の音声付き container を探す。同居 ffprobe で音声 stream と尺を確認し、要求区間を丸ごと含むものがあれば

```
ffmpeg -ss <start> -accurate_seek -i <source> -map 0:a:0 \
  -af "aresample=16000,atrim=start_sample=0:end_sample=N,asetpts=N/SR/TB" \
  -vn -ar 16000 -ac 1 -c:a pcm_s16le -f wav <out>
```

で切り出す。source が無い・音声 stream が無い・尺が足りない場合だけ従来の yt-dlp 経路へ落ち、そちらには `--force-keyframes-at-cuts` を渡す。yt-dlp の存在確認は fallback 経路でだけ要求する。

### 2. 開始位置の fail-closed 検証

長さ検証では「長さは正しいのに内容がずれる」状態を検出できない。そこで**同じ絶対区間を別 anchor から切り出して PCM を照合する**。

- 参照は `requested_start_ms − 5,000 ms` を anchor にし、`atrim=start_sample` で 5 秒分読み飛ばす。5 秒手前が取れない場合は seek 自体が起きない 0 を anchor にする
- 先頭 250 ms は AAC decoder の warm-up 窓として比較から除く
- 残りの RMS 差が `max(64.0, 0.02 × 参照 RMS)` を超えたら `AudioSpanError` にする

anchor が違っても一致するなら、seek 位置は keyframe ではなく要求した絶対時刻に従っている。実測値は次のとおりで、分離は 3 桁ある。

| 対象 | RMS 差 | 許容 |
| --- | --- | --- |
| `mKwn-93gg90` 1179000–1195000（正常） | 0.060 | 64.0 |
| `mKwn-93gg90` 1146000–1154000（正常） | 0.207 | 64.0 |
| `hPeRSA9YVIM` 8640000–8690000（正常） | 0.090 | 64.0 |
| 9 秒ずれた旧 cache との比較（参考） | 1,010 | 64.0 |

warm-up 窓が要る理由は実測にある。`mKwn-93gg90` 1146000–1154000 では、128,000 sample 中 833 sample だけが別 anchor と一致せず、その大半が先頭 50 ms 未満に集中していた。除外前の RMS 差は 83.5 で、正常な span が誤って弾かれていた。sample 単位の lag は 0 であり、タイミングのずれではない。

### 3. cache と artifact の失効

- 音声 cache schema を `s9-3-audio-cache-v2` へ上げ、`seek.method` を `local-source-accurate-seek-preferred` に変えた。fingerprint が変わるため旧 cache は参照されない
- cache metadata に `audio_route` と `alignment` を記録する。開始位置を検証できない経路で作った cache は、accurate seek できる source が現れた時点で cache miss として再生成する
- `TranscriptArtifact.is_high_precision` に「全音声 span が取得経路を記録していること」を条件として足した。**旧 artifact はファイルを動かさずに高精度扱いだけ外れる**。これは旧 artifact と旧音声 cache が `benchmarks/t1/manifest.json` の不変証跡に含まれており、物理的に退避すると T1-1 の再現性が壊れるためである（実際に一度退避して T1 テスト 16 件を落とし、復元した）
- fallback 経路（`verified: false`）は高精度扱いを維持する。source が無い環境で高精度字幕が一切使えなくなるのを避けるため

### 4. 再生成と照合

| case | artifact | 経路 | 結果 |
| --- | --- | --- | --- |
| `mkw-long-local-asr` | `1b1c8643a89d…` | local_source_accurate_seek | 内容が宣言区間と一致 |
| `hpe-audio-variation` | `7ef688303557…` | local_source_accurate_seek | 8640 起点で生成 |

`mkw-long-local-asr` の cue を、本文書の「絶対時刻の正解」と突き合わせた結果:

| artifact の cue | 内容 | 正解との関係 |
| --- | --- | --- |
| 1146000–1154000 | 「LlamaとかでローカルLLMを立ち上げて、そのモデルを設定して使うことって考えられますか?」 | 正解 1140.00–1154.76 の発話の後半。区間どおり |
| 1179000–1195000 | 「Iの料金がわかんねーな。Whisper Large V3。あ、こっちか。ん?文字起こし。あ、こっちか。…」 | 正解 1178–1182 の途中から始まり、1182–1187 / 1187–1188 / 1188–1191 と順に一致 |

修正前の artifact は 1179–1183 に「これワンチャンローカルで行けるんだったら…」（実際は 1170–1174）を置いていた。−9 秒のずれは解消している。

### 5. この修正で解決していないこと

**cue の粒度**は変わっていない。16 秒の区間は依然として 1 cue にまとまり、その中でテロップ行を分けるときは比例配分になる。本文書「副作用として観測された事象」の 2 と 3 は残っており、これは T1 の担当範囲である（T1-1 は No-Go / fallback-only）。開始位置が合ったことで cue 内の相対位置は正しくなるが、cue 内の行単位の追従は改善しない。

## 修正方針の候補（着手前の検討記録）

いずれも production code の変更であり、S9-6 の宣言済み変更ファイル範囲外である。着手には別承認が必要である。

1. **ローカル source からの精密切り出しに変える。** 音声付き source mp4 が既にある場合、`-ss <start> -accurate_seek -i <source>` で切り出す。T1-1 harness で実績があり、network も不要で最も正確。source が無い場合のみ yt-dlp へ落とす。
2. **yt-dlp に `--force-keyframes-at-cuts` を渡す。** 切り出し位置が正確になるが再エンコードが入り遅くなる。
3. **先頭余剰を測って切る。** 取得後に実開始位置を推定して `atrim=start_sample` を足す。実開始位置を信頼して求める手段が必要で、1 や 2 より脆い。

推奨は 1 である。あわせて、長さだけでなく**開始位置の整合**を検証する fail-closed チェックを追加し、既存の高精度 artifact を失効させる必要がある。

→ 方針 1 を採用し、fallback にも `--force-keyframes-at-cuts` を足した。詳細は「実施した修正」節を参照。

## S9-6 判定への影響

これは S9 の中核成果物（選択区間の高精度字幕）における実機の欠陥であり、非回帰ではない。修正後も S9-6 は **No-Go を維持**する。理由は次のとおりである。

- 開始ずれ自体は解消し、`mkw-long-local-asr` の cue は宣言区間と一致することを確認した
- ただし人 preview（`mkw-long-local-asr` の再確認、`hpe-audio-variation` の opening trim 後 preview、final short の致命的無発話なし確認）は**未実施**であり、これはユーザーが行う gate である
- cue 粒度の問題（16 秒区間が 1 cue）は未解決で、T1-1 は No-Go / fallback-only のまま

gold 監査 waiver、A/B 数値 gate、production hash 非回帰（15/15 不変）はいずれも変わらない。フェーズ完了・AC-30 / AC-35 / AC-37 / AC-40 の更新は行わない。
