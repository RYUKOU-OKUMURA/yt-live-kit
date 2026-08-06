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

`docs/benchmarks/s9-6-acceptance.md` に記録した `hpe-audio-variation` の音声活動実測は、**ずれた cache wav を対象に測っていた**ため誤りである。

- 誤: 冒頭 1.194 秒無音 → 4.115–16.131 秒に 12 秒の無発話 → 持続発話開始は 16.1 秒
- 正（source 8640 起点、精密シーク）: **0 – 28.604 秒が無音**。その後も 30.431–33.872 秒に 3.4 秒、34.500–35.615 秒に 1.1 秒と断続する

したがって候補冒頭の無発話は約 28.6 秒であり、境界監査の「約 6 秒」も、当方の「16.1 秒」も過小評価だった。当方が設定した cut 開始 02:24:16 は**無発話区間の内側**であり誤りである。ただし `hpe-audio-variation` のショートは今回生成しておらず（`short_8ad3b1c9bd32.mp4` は 2026-08-04 生成の別物）、誤った区間で人 preview は行っていない。

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

## 修正方針の候補

いずれも production code の変更であり、S9-6 の宣言済み変更ファイル範囲外である。着手には別承認が必要である。

1. **ローカル source からの精密切り出しに変える。** 音声付き source mp4 が既にある場合、`-ss <start> -accurate_seek -i <source>` で切り出す。T1-1 harness で実績があり、network も不要で最も正確。source が無い場合のみ yt-dlp へ落とす。
2. **yt-dlp に `--force-keyframes-at-cuts` を渡す。** 切り出し位置が正確になるが再エンコードが入り遅くなる。
3. **先頭余剰を測って切る。** 取得後に実開始位置を推定して `atrim=start_sample` を足す。実開始位置を信頼して求める手段が必要で、1 や 2 より脆い。

推奨は 1 である。あわせて、長さだけでなく**開始位置の整合**を検証する fail-closed チェックを追加し、既存の高精度 artifact を失効させる必要がある。

## S9-6 判定への影響

これは S9 の中核成果物（選択区間の高精度字幕）における実機の欠陥であり、非回帰ではない。したがって S9-6 は **No-Go を維持**する。gold 監査 waiver、A/B 数値 gate、production hash 非回帰はいずれも変わらないが、人 preview で実欠陥が出たため、フェーズ完了・AC-30 / AC-35 / AC-37 / AC-40 の更新は行わない。
