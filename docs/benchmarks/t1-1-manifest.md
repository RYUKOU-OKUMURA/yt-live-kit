# T1-1 timing spike manifest

## 固定 identity

- 基点: `3396ab3`（T1-PLAN）
- canonical predecessor: `d94a8c5`（corrected v3.5、候補測定0）
- formal manifest-only freeze commit: `d152230`
- revision: `corrected_pre_measurement_freeze_v3_7`
- manifest: `benchmarks/t1/manifest.json`
- schema: `t1-1-timing-spike-manifest-v3`
- fingerprint: `b4f33f33c6b7f23be0d28c13bdb0bdde946659a7a8f9909894e8b0d4807a11ec`
- status: `manifest_frozen_waiting_for_human_gold`
- candidate measurement / human gold: `0`

`24c777d`、`9822ff2`、`9e66122`、v3.6 working candidate は、候補測定前の review-rejected draft として supersede した。`d152230` は manifest-only freezeであり、harness／packet／result／docsは後続の独立レビュー対象 commitへ分離する。mainへ mergeしない。

## fixture population

| fixture group | 件数 | 内容 |
|---|---:|---|
| `long_single_cue` | 20 | 同一 artifact cue 内の保存済み telop line。cue-start anchor 6、後続 line 14。audio は既存 cut 全体で、cue/token 境界から切らない |
| `multi_cross_cue` | 24 | 通常の multi/cross sequence 20 + 既知 ambiguous artifact holdout 4。holdout は同群の coverage 分母に含め、全件 low-confidence flag／元時刻維持／黙った移動0を判定 |
| `vtt_fallback_concat` | 20 | LB4 genuine VTT 16 target + 既存 b5d VTT→ASS dialogue 4。全20件を fallback 非回帰分母に含める |
| 合計 | **64** | human gold が全64件揃うまで候補測定不可 |

artifact-backed 44 unique tuple は、long 20 / 通常 multi 20 / ambiguous holdout 4 に重複なく割り当て、未使用は0。saved telop は59 unique line/time tuple、legacy LB4 は15行、manual は `lb4_e1ff:s4:l2:1113149-1116110` の1原文だけを候補時刻を見ずに意味境界で `やばい` / `止まってないね` の2独立 scenarioへ置換する。元行は別途数えない。`saved_target_rows_without_manual_split=58`、`non_manual_target_rows=62` である。

manual split の delimiter「、」は provenance のみへ保持し、target textへコピーしない。validator は空、同一、文字欠落・重複、原文再結合不一致、既知の単語途中 split を fail closed にする。各 scenario は兄弟 a/b を同時出力せず、元行保存時刻は baseline reference であって gold ではない。

## b5d genuine VTT fallback evidence

holdout 4 は fallback から除外し、次の既存 read-only VTT/ASS 素材を4 targetに使う。

| asset | path | bytes | SHA-256 |
|---|---|---:|---|
| ASS | `data/LB4px1wRFnY/shorts/subtitles/short_b5d345c4379e.ass` | 4638 | `cde04f97ae351c77738e73673103d209de9c61f266547cf62d317b119341026a` |
| VTT | `data/LB4px1wRFnY/subtitles/ja.vtt` | 353340 | `cc6d7fe8f89ffe3ae22f411ece80dcba7c9ab48f96b90b13fa21e7b4216c3fb2` |
| cutplan | `data/LB4px1wRFnY/shorts/cutplan/cut_clip_003.json` | 1123 | `e2fc48665b85aae24164f163d12682442ec19150c702196dff30868f225f296d` |
| ffmpeg log | `data/LB4px1wRFnY/shorts/output/short_b5d345c4379e.ffmpeg.log` | 12512 | `fc11f894d5f3475f31bc68e360f293b639e51f887b3843c3ddf5f47b4cbd1e02` |

source MP4 は `data/LB4px1wRFnY/clips/source/LB4px1wRFnY.mp4`、bytes `502876028`、SHA-256 `5f8e8187fd3520da8ae6186f851995346eb8213123a456fdeb608ead00476aed`。cutplan003 の3 partは `3700000–3721000`、`4015000–4052000`、`4086000–4100000` ms、concat offset は `0 / 21000 / 58000` ms、duration は `72000` ms、gap は `294000 / 34000` ms。`canonical_clip_id` は absolute millisecond bounds の `sha256("3700000-3721000|4015000-4052000|4086000-4100000")[:12]` から導出する `b5d345c4379e` である。

選定 Dialogue は event `2 / 12 / 35 / 41`。ASS centisecond 時刻、VTT millisecond 時刻、absolute source 時刻を別 field で保持する。validator は各 event について cumulative part offset を使い、`source_abs = part.start + (ass_concat - cumulative_offset)`、VTT start の最大±5ms、cut end clamp、target part containment を機械検証する。ASS text と VTT は production の progressive-delta semantics で照合し、text 一致だけの誤 pair は通さない。ffmpeg log が同じ ASS basename と canonical concat clip を入力した証跡も検証する。ASS/VTT event 時刻は gold ではない。

ASS は isolated temp に VTT を copy し、`telop_script=None`、`Hiragino Sans` で `build_concatenated_subtitle` 相当を再現して既存 ASS と byte-for-byte 一致する。production data、ASS、VTT、音声 bytes は commit しない。

## source、timing、runtime

- audio context は **15 distinct span**（既存 LB4 4 + gZA 5 + hPe 3 + b5d cutplan003 3）。b5d source id は既存 cutplan001 id を再利用しない
- `max_selected_spans=8`、`selected_span_count=8`
- `audio_context_span_count=15`
- `max_whisper_invocations=8`、`whisper_invocation_count=0`
- artifact JSON は cue-only で raw token timing ではない。selected 8 spanだけを、freeze後に bounded whisper-cli、isolated temp、source hash binding、1 span 1 invocation の契約で予定する
- b5d 4 fallback rowsに timing input / Whisper は付けない

LB4 gold playback は旧 oversized audio cacheを使わず、source MP4から isolated extractionする。旧 cache の実測 duration は `24993.5 / 21993.5 / 40993.5 / 39993.5` ms、要求値と別 fieldの rejected evidence である。

configured ffmpeg は `.env` の `YTLK_FFMPEG_PATH=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` を正本とする。version `8.1.2`、SHA-256 `ddf547c2aa50cc487c2d96e5d4b10a7bb35d8a8299a40d0ebafd12dfdaa6f044`。source MP4 hash と ffmpeg bytes/hash は subprocess 直前に再検証し、absolute seek、`-accurate_seek`、`0:a:0`、`aresample=16000`、`atrim=end_sample=duration_ms*16`、mono `pcm_s16le` WAV、part別期待／実読込／最終 frames、format、SHA を検証する。production cacheへ書かない。

## gold と gate

gold は人が音声を再生して付けた line onset のみ。VTT、ASS、cue、Whisper token、既存 telop 時刻、manual split boundary、候補値は gold へコピーしない。`human_gold.completion_rule` は全64行の `audio_listened=true`、整数 onset、`source_audio_relative_ms`、非空 annotator／timestamp を要求する。

pooled、long、multi の provisional gate は測定前固定で、coverage 80%以上、absolute onset median 250ms以下、p90 500ms以下、max 1000ms以下、signed median bias 絶対値200ms以下、wrong line/cross-cue move 0。CER、固有名詞、cue欠落・重複、wall time、peak memoryも記録する。fallback は coverage 外の非回帰群で、全20件の現行出力同等・自動移動0を判定する。結果後の閾値緩和は禁止。

## packet / 再現

```sh
python3 -m benchmarks.t1.annotation_packet validate-manifest \
  --manifest benchmarks/t1/manifest.json --check-sources
```

generator は canonical predecessor `d94a8c5:benchmarks/t1/manifest.json` を source-check して v3.7を生成でき、v3.7を previous にして再生成しても fingerprint／JSONが同一になる。候補結果を見ず、manifestを固定・検証してから人手注釈へ進む。

packet は repository 外の隔離 tempへ作る。row は row id、group、audio source id、再生用 full context span、target text、goldだけの allowlist、source は playback path／bytes／SHAだけである。draft、VTT cue、ASS event、telop tuple、artifact metadata、raw timing、rejected cacheは packetへ出さない。packet sources、row件数・順序、source hash、receipt WAV実体／format／frames／SHAを fail closed に検証する。

play は configured `ffplay` sibling、`which ffplay`、`afplay` の順。`--duration-ms` 省略時は from-msから row終端まで、明示時だけ短窓。stats の表示は playback positionであり、`gold = from_ms + 表示秒×1000`。row id省略は常に先頭未完了、明示再生後は annotateにも同じ row idを指定する。candidate／draft時刻は表示・自動入力しない。annotated-atはUTC現在時刻を自動記録する。

```sh
python3 -m benchmarks.t1.annotation_packet create-packet \
  --manifest benchmarks/t1/manifest.json \
  --output /tmp/yt-live-kit-t1-1-human-gold.json
python3 -m benchmarks.t1.annotation_packet play \
  --manifest benchmarks/t1/manifest.json \
  --packet /tmp/yt-live-kit-t1-1-human-gold.json
python3 -m benchmarks.t1.annotation_packet annotate \
  --manifest benchmarks/t1/manifest.json \
  --packet /tmp/yt-live-kit-t1-1-human-gold.json \
  --row-id t1-fallback-001 --onset-ms 1234 --annotator 人手確認者 --audio-listened
```

## integrity と停止条件

production baseline / after snapshot は既存の15-file証跡を bytes／SHA、同一固定 absolute root、live fileまで再検証する。manifest-bound source 15、telop document 4、artifact document 3に加え、b5d ASS/VTT/cutplan/ffmpeg logの4 filesを候補測定後にも再hashする。削除、bytes改竄、内容差替え、root外／symlink escapeは fail closed。候補測定、Go / No-Go、T1-2、AC-40 は human gold、再hash、全証跡、独立レビューが揃うまで記録しない。
