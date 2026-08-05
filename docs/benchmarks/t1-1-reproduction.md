# T1-1 reproduction record

## identity and status

- base: `3396ab3`
- committed predecessor: `d94a8c5` / corrected v3.5
- formal manifest-only freeze: `d152230`
- candidate revision: corrected v3.7
- candidate fingerprint: `b4f33f33c6b7f23be0d28c13bdb0bdde946659a7a8f9909894e8b0d4807a11ec`
- rows: 64 (`long 20 / multi 24 / fallback 20`)
- human gold: `0 / 64`
- candidate measurements: `0`
- Go / No-Go: not decidable; T1-1本体、AC-40、T1-2は未完了

v3.5、v3.6、旧24c777d／9822ff2／9e66122 は候補測定前の review-rejected draft。v3.7 は d94a8c5 を source-check して deterministic に生成し、現 v3.7を previous にした再生成も同一JSON／fingerprintとなる。`d152230` は manifest-only freezeであり、harness／packet／result／docsは後続commitへ分離する。mainへ統合しない。

## reproduction commands

```sh
uv run python benchmarks/t1/generate_manifest_v35.py \
  --previous /path/to/d94-v35/manifest.json \
  --output /tmp/t1-v37/manifest.json \
  --test-root /tmp/t1-v37

python3 -m benchmarks.t1.annotation_packet validate-manifest \
  --manifest benchmarks/t1/manifest.json --check-sources

uv run pytest -q tests/test_t1_annotation_packet.py tests/test_t1_manifest_generator.py
git diff --check
```

generatorは production実行時に previous/output を `benchmarks/t1/manifest.json` へ限定し、test-root時は明示した一時rootのみへ出力する。source／previousの hash preflightに失敗したら production文書を読まず、symlink、outside、protected outputは拒否する。atomic replaceは candidate JSONのparse／validator／source check後だけ実行する。

## fixed population

- artifact-backed 44 unique tuple: long 20、normal multi 20、multi low-confidence holdout 4
- saved telop 59 unique line/time tuple: legacy LB4 15、gZA/hPe artifact 44
- manual: LB4 `e1ff/s4/l2` の1原文だけを `やばい`／`止まってないね` へ置換（net +1、元行は別カウントしない）
- vtt fallback concat 20: LB4 genuine VTT 16 + b5d existing VTT/ASS dialogue 4
- total 64、saved target rows without manual split 58、non-manual target rows 62
- all 20 fallback rows have `fallback_non_regression_required=true`; multi holdout 4はfallbackではなくmulti coverage分母へ含める

manual subtargetは候補時刻を見ずに固定した text-only boundaryであり、元時刻は baseline referenceのみ。goldは必ず音声を実際に聞いて新たに入力する。

## b5d ASS/VTT evidence

既存 production dataから次をread-only bindする。bytes／SHAはmanifestとvalidatorで固定し、commitするのはpath／hash／metadataだけで音声・ASS・VTT bytesではない。

| file | bytes | SHA-256 |
|---|---:|---|
| `short_b5d345c4379e.ass` | 4638 | `cde04f97ae351c77738e73673103d209de9c61f266547cf62d317b119341026a` |
| `ja.vtt` | 353340 | `cc6d7fe8f89ffe3ae22f411ece80dcba7c9ab48f96b90b13fa21e7b4216c3fb2` |
| `cut_clip_003.json` | 1123 | `e2fc48665b85aae24164f163d12682442ec19150c702196dff30868f225f296d` |
| `short_b5d345c4379e.ffmpeg.log` | 12512 | `fc11f894d5f3475f31bc68e360f293b639e51f887b3843c3ddf5f47b4cbd1e02` |

cutplan003 segments are absolute `3700000–3721000` / `4015000–4052000` / `4086000–4100000` ms; cumulative concat offsets `0 / 21000 / 58000`, duration `72000`, gaps `294000 / 34000`。canonical clip ID is derived from `sha256("3700000-3721000|4015000-4052000|4086000-4100000")[:12]` = `b5d345c4379e`。ASS event `2 / 12 / 35 / 41` is paired with VTT cue `1198 / 1322 / 1358 / 1364` by event index、production progressive-delta text、separate timebase; text equality alone is insufficient.

validatorは各 dialogueについて cumulative offset mapping、VTT start ±5ms、cut clamp、source containment、target partを確認し、ffmpeg logが同じ ASS basename／canonical concatを入力したことを確認する。`telop_script=None` の isolated VTT再生成ASSは既存ASSとbyte-for-byte一致する。b5d rowsには Whisper timing inputを付けない。

## source and runtime contract

- audio context: 15 distinct spans（LB4 4 + gZA 5 + hPe 3 + b5d cutplan003 3）
- timing selected: 8、max selected: 8、max Whisper invocation: 8、actual invocation: 0
- artifact JSONはraw token timingではなくcue-only。Whisperはfreeze後にisolated tempで予定するだけ
- LB4は旧 audio cacheをgold sourceにせず、source MP4 hashを確認して configured ffmpeg-fullでisolated extractionする
- ffmpeg: `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`、8.1.2、SHA `ddf547c2aa50cc487c2d96e5d4b10a7bb35d8a8299a40d0ebafd12dfdaa6f044`
- extraction: absolute seek、`-accurate_seek` before input、`0:a:0`、`aresample=16000`、`atrim=end_sample=duration_ms*16`、mono PCM s16le、part別 frame／format／SHA検証

実source smoke は LB4 `857000–858000` ms、16000 frames、32044 bytes、isolated temp only。manifest concat no-op smoke は `t1-fallback-001` の cut1+cut2で `from_ms=16000`、`duration_ms=5000`、parts2、80000 frames、160044 bytes、receipt再検証PASS。候補／goldではない。

## human annotation shortest path

```sh
python3 -m benchmarks.t1.annotation_packet create-packet \
  --manifest benchmarks/t1/manifest.json \
  --output /tmp/yt-live-kit-t1-1-human-gold.json
python3 -m benchmarks.t1.annotation_packet play \
  --manifest benchmarks/t1/manifest.json \
  --packet /tmp/yt-live-kit-t1-1-human-gold.json
```

`play`／`annotate` のrow id省略は常にmanifest順の先頭未完了row。`play --row-id X` の後は `annotate --row-id X` を明示する。duration省略時は `from_ms` から row終端までfull remaining context、明示duration時は短窓。configured sibling `ffplay`を優先し `-nodisp -autoexit -stats -hide_banner` を使う。terminal表示は playback positionのみで、表示秒をsとした gold入力は `from_ms + s*1000`。candidate／draft時刻は表示しない。annotated-atはtoolがUTC現在時刻を自動記録する。

短窓で反復する例:

```sh
python3 -m benchmarks.t1.annotation_packet play \
  --manifest benchmarks/t1/manifest.json \
  --packet /tmp/yt-live-kit-t1-1-human-gold.json \
  --row-id t1-fallback-001 --from-ms 2500 --duration-ms 3000
python3 -m benchmarks.t1.annotation_packet annotate \
  --manifest benchmarks/t1/manifest.json \
  --packet /tmp/yt-live-kit-t1-1-human-gold.json \
  --row-id t1-fallback-001 --onset-ms 3000 --annotator 人手確認者 --audio-listened
```

onsetは再生窓の開始を含み終端を含まない。row durationと再生窓外、invalid annotator／timestamp、receiptより前のannotated-at、実WAVのhash／bytes／PCM format／frames不一致は保存前に拒否し、元packet bytesを変更しない。全64 rowsがcompleteになるまで測定不可。

## integrity evidence

production baseline は S9 15-file snapshot（before bytes `2134`、SHA `35ed5d9d624095a0ce3e39076bdd0c539488fc961e39697b62c0f5957a4a0faa`）。after artifact は `docs/benchmarks/t1-1-production-hash-after.json` bytes `2798`、SHA `9fa4de94e03eb8d250d1e39297923e294f9217016aca5e6494cee53abd153d26`。validatorは両JSONを開き、同一固定 absolute production root、before/after 15 entries、live fileのbytes／SHA、safe path／symlinkを全件確認する。

manifest-bound source 15、telop document 4、artifact document 3、b5d evidence 4は候補測定後にも再hashする契約を持つ。現statusは pendingであり、candidate result、Go／No-Go、AC-40、T1-2への進行は許可しない。production data、existing cache、artifact、output、hash、S9証跡、learning logは変更していない。
