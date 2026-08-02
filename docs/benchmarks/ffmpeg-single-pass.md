# FFmpeg single-pass benchmark

G1 の実装変更前検証。production の `services/shorts.py` / `services/ffmpeg.py` は呼び出さず、ローカルで生成した fixture に対して、現行 input-seek multi-pass、同じ encode 条件の output-seek、single-pass filter graph prototype を比較する。

## 結論

採用判定は次の固定 gate で行う。

- single-pass の warm wall time median が現行 input-seek より 25％以上短い
- 区間接続の境界差が 1 frame 以下
- audio の start / end PTS に回帰がない
- 字幕と Hook の表示に回帰がない。字幕表示は pixel 化されるため、最終判定に手動の視覚確認を含める

本計測では全ケースが自動 gate の速度条件に届かなかった。15 秒は 21.98％、60 秒は 22.68％、180 秒は 23.08％で、全ケース共通の採用には至らない。代表フレームの視覚確認は実施し、字幕 / Hook と区間接続の表示回帰は見つからなかったが、production 変更は行わず、現行 input-seek multi-pass を維持する。採用候補になっても、FR-25 / AC-25 の要件改訂と G2 の production 変更を同じ commit に混ぜない。

## 再実行

FFmpeg 8 系、Python 3.11 以上、`uv`、`libx264`、字幕ケースを含める場合は `subtitles` filter と libass が必要。macOS では通常版と `ffmpeg-full` の実体パスを混同しない。

本計測の入口は次のコマンド。`--report` はリポジトリ外の一時 JSON を指定する。

```bash
uv run python benchmarks/ffmpeg_single_pass.py \
  --ffmpeg-path /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
  --runs 3 \
  --warmups 1 \
  --report /tmp/yt-live-kit-ffmpeg-g1.json
```

ケース単位の再実行は次のように行える。

```bash
uv run python benchmarks/ffmpeg_single_pass.py \
  --ffmpeg-path /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
  --case-id 60-three-normal-subtitle \
  --runs 3 \
  --warmups 1 \
  --report /tmp/yt-live-kit-ffmpeg-60.json
```

既定では `tempfile.TemporaryDirectory` の下に fixture、ASS、区間中間ファイル、concat ファイル、各出力を作り、終了時にすべて削除する。既存の `data/`、`shorts/output/`、`clips/output/`、正式な mp4 は参照も変更もしない。JSON report に残る command は再実行用の記録であり、出力メディア自体は保存しない。

標準版 FFmpeg で字幕 filter が見つからない場合、15 秒字幕なしケースだけを実行し、通常字幕 / Hook ケースは exact blocker を report に残して終了コード 2 とする。これは字幕なしへの自動フォールバックではない。

## 固定 fixture とケース

fixture は 1280×720、30 fps、180 秒の synthetic media である。60 秒ずつの `testsrc2` 3 区間を色調変換して concat し、音声は 440 Hz、660 Hz、880 Hz の sine を同じ順で concat する。60 秒境界と 120 秒境界が映像・音声の接続点になるため、実素材や YouTube API に依存せず境界を再確認できる。

| Case ID | 尺 | 区間 | 字幕 variant | 内部境界 |
|---|---:|---:|---|---|
| `15-single-no-subtitle` | 15 秒 | 1 区間: 12–27 秒 | なし | なし |
| `60-three-normal-subtitle` | 60 秒 | 0–20、60–80、120–140 秒 | 通常字幕 3 cue | 出力 20、40 秒 |
| `180-three-hook-subtitle` | 180 秒 | 0–60、60–120、120–180 秒 | Hook 1 cue + 通常字幕 3 cue | 出力 60、120 秒 |

字幕 cue はすべて出力時間基準で ASS に固定する。通常字幕は 60 秒ケースで 4–8、24–28、44–48 秒、180 秒ケースで 8–14、68–74、128–134 秒。Hook は production の `write_ass()` と同じ 0–2 秒・Hook preset 相当の style にし、区間接続と重ならない。

## 比較する command

すべての mode は、最終 output について `libx264` / `medium` / `crf 20`、AAC / `192k`、現行 production と同じ blur layout、1080×1920 を使う。input-seek と output-seek の区間中間ファイルだけは現行と同じ `libx264` / `medium` / `crf 16` で作り、concat は stream copy する。

| Mode | 構成 |
|---|---|
| `input-seek` | 各区間で `-ss start -i source -t duration`、再 encode、concat demuxer、最終 layout / ASS encode。現行 production の再現 |
| `output-seek` | 各区間で `-i source -ss start -t duration`、それ以外は input-seek と同一 |
| `single-pass` | source 1 本を `trim` / `atrim`、各区間を `setpts` / `asetpts` で 0 秒へ戻し、`concat` filter、layout / ASS、最終 encode を 1 command で実行 |

report には fixture command、warmup command、各 timed run の全 expanded command、FFprobe の command、計測値を JSON で保存する。mode ごとに中間ファイルを再利用しない。実行順は case ごとに `input-seek`、`output-seek`、`single-pass` を決定的に rotate し、single-pass が常に最後になる OS cache 偏りを避ける。

## 記録する値

- FFmpeg version、ffprobe path、fixture 条件、codec / preset / CRF / audio bitrate
- warmup 回数、timed run 回数、wall time の median / min / max
- output duration、format start / end PTS、video first / last frame PTS、audio first / last frame PTS と audio end PTS
- 解像度、pixel format、video frame count、audio / subtitle stream 数、ファイル容量
- 期待する区間境界、1 px RGB の frame 差分から検出した境界、差分 frame 数
- subtitle cue と Hook cue、subtitle stream の有無、手動視覚確認の要否

wall time は FFmpeg command の開始から成功終了までで、終了後の FFprobe、frame probe、1 px RGB 境界 probe は含めない。fixture 生成時間も mode の wall time に含めない。

## 採用 gate

single-pass の median を現行 input-seek の median と比較し、`1 - single / input` が 0.25 以上であることを速度条件とする。境界は fixture の色変化を用いて expected time の前後 3 秒から検出し、全 expected boundary が検出済みで、expected time との差と input-seek との差が 1 video frame 以下であることを条件にする。audio は stream 数、start PTS、expected duration からの end PTS、baseline との差を記録し、開始・終了の expected duration 誤差または baseline との差が 1 video frame を超える場合を回帰と扱う。字幕は stream 数の一致だけで合格にせず、下記の視覚確認を必須とする。

この benchmark は採用 gate を自動的に満たしたとしても、production の seek 順、字幕時刻、atomic output、確認フローを変更しない。採用する場合は別 task G2 として requirements-v3 の FR-25 / AC-25、rollback 方針、production テストを先に更新する。

## 視覚確認手順

report の各 mode の出力を保持するには、`--keep-workdir` を追加する。保持先も system temporary directory であり、確認後に削除できる。既定実行では終了時に削除される。

```bash
uv run python benchmarks/ffmpeg_single_pass.py \
  --ffmpeg-path /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
  --runs 3 \
  --warmups 1 \
  --keep-workdir \
  --report /tmp/yt-live-kit-ffmpeg-visual.json
```

1. 同じ case の input-seek、output-seek、single-pass を同じプレイヤーで開く。
2. 60 秒ケースは出力 20 秒 / 40 秒、180 秒ケースは 60 秒 / 120 秒の前後を frame step で確認する。色付き source 区間の切替が早すぎたり遅すぎたりしないことを確認する。
3. 各字幕 cue の開始・終了、Hook の 0–2 秒、区間接続直後の字幕が欠落・二重・前後ずれしていないことを確認する。
4. 冒頭・末尾で audio が無音化せず、区間接続でクリック、音切れ、音ズレがないことを確認する。
5. 1080×1920、pixel format、縦 layout、ぼかし背景と前景の位置が 3 mode で一致することを確認する。
6. 結果を report と同じ日付のレビュー記録へ、case、mode、確認時刻、確認者、結果、差分の有無として残す。JSON の `subtitle_visual_check` は自動生成時には常に `manual_required` とし、人が確認済みという事実はこの文書へ記録する。自動 report が人の確認を自己証明しないようにするためである。

## 実測結果

2026-08-02 に次の環境で、warmup 1 回後に各 mode 3 回を計測した。FFmpeg は字幕対応の実体を明示した。

- macOS 26.5.2、Mac14,7、8 logical CPU、16 GiB
- `ffmpeg version 8.1.2`、`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`
- `uv 0.10.0`、Python 3.11.7
- 全 output は 1080×1920、`yuv420p`、video 30 fps、audio 1 stream、subtitle stream 0（ASS は焼き込み）

下表の時間は本計測の `wall_time_summary` から転記した。尺・PTS・容量は 3 run で同じだった。input / output seek の尺は concat の timestamp により約 1 frame 長くなり、single-pass は期待尺ちょうどになった。single-pass の audio expected duration 誤差は 0 秒、baseline との差は 15 秒で 40 ms、60 / 180 秒で 32 ms だった。audio の「差」はこの baseline 差と expected 誤差を分けて扱う。

| Case | input-seek median | output-seek median | single-pass median | single speedup | 境界差 | audio expected 誤差 / baseline 差 | 自動判定 | 視覚判定 |
|---|---:|---:|---:|---:|---:|---:|---|---|
| 15 秒 | 7.620 秒 | 7.715 秒 | 5.945 秒 | 21.98％ | 内部境界なし | 0 秒 / 40 ms | 不採用: 速度 / audio baseline 差 | 確認済み |
| 60 秒 | 30.917 秒 | 32.248 秒 | 23.906 秒 | 22.68％ | 1 frame | 0 秒 / 32 ms | 不採用: 速度 | 確認済み |
| 180 秒 | 88.402 秒 | 90.317 秒 | 68.002 秒 | 23.08％ | 1 frame | 0 秒 / 32 ms | 不採用: 速度 | 確認済み |

代表的な出力値は次のとおり。

| Case | output duration | video first / last frame PTS | audio first / end PTS | frames | input / single size |
|---|---:|---|---|---:|---:|
| 15 秒 | input 15.040 / single 15.000 秒 | input 0.033008 / 14.999674、single 0 / 14.966667 | input 0 / 15.040、single 0 / 15.000 | 450 | 4,265,702 / 4,339,435 bytes |
| 60 秒 | input 60.033008 / single 60.000 秒 | input 0.033008 / 59.999674、single 0 / 59.966667 | input 0 / 60.032、single 0 / 60.000 | 1,800 | 19,073,926 / 19,361,772 bytes |
| 180 秒 | input 180.033008 / single 180.000 秒 | input 0.033008 / 179.999674、single 0 / 179.966667 | input 0 / 180.032、single 0 / 180.000 | 5,400 | 56,871,586 / 57,733,574 bytes |

### G1-3 の判断

本計測では 15 秒 21.98％、60 秒 22.68％、180 秒 23.08％で全ケースが速度 gate 未達となった。境界は全 expected boundary が検出され、input-seek との差は最大 1 frame、audio の expected duration 誤差は 0 秒だった。代表フレームの視覚確認では、60 秒ケースの通常字幕 1 と出力 20 秒の接続、180 秒ケースの Hook 0–2 秒・通常字幕 1・出力 60 秒の接続を input-seek / output-seek / single-pass の 3 mode で確認し、字幕の欠落・二重表示・前後ずれ、layout の明確な差は見つからなかった。速度 gate 未達のため「不採用」とし、G2 へ進めず、現行 production の input-seek multi-pass を維持する。G2 の production 実装は本 task の変更範囲に含めない。
