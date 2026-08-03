# S9-6 受け入れ証跡

## 判定

status は `fallback_only_human_confirmation_pending`。今回の判定は `no_go` であり、fallback-only のまま人確認待ちとする。

- `phase_complete`: `false`
- `s9_complete`: `false`
- `m16_complete`: `false`
- `ac30`: `false`
- `ac35`: `false`
- `ac37`: `false`

single report は comparator 不足で、`gold_audit_status=provisional` のため `no_go` とした。canonical evidence の gold も `unverified_provisional` であり、用途は operational transcript reference に限定する。transcript が概ね許容可能であることだけでは Go に不十分である。

## fingerprint

- canonical fixture: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`
- q5 run manifest: `a25d0fbc8233a1db7f0c2ecbb332781b19e5fd5b31260a5b0b2d03be7270de5e`

canonical fixture fingerprint は評価入力を識別し、q5 run manifest fingerprint は q5 turbo の実行条件と run 集合を識別する。2つは意味が異なるため、同一 fingerprint とは主張しない。

## 再現結果

- q5 cold は4ケースすべて終了コード0。
- focused S9 は123件 passed。
- single report は comparator 不足かつ `gold_audit_status=provisional` のため `no_go`。

## 数値

- CER 相対改善率: `78.694`％
- glossary exact match: q5 は `13/19`、VTT は `10/19`
- cue error: q5 は `2.35`、VTT は `6.95`
- wall time: 最小 `2331 ms`、最大 `5286 ms`
- peak RSS: 最小 `904462336 bytes`、最大 `926924800 bytes`

## canonical evidence

- q5 turbo は16 runs。
- cold と warm の output SHA は一致。
- production scope は15ファイルで、変更前後に差分なし。
- VTT parity は `4/4`。
- ただし gold は `unverified_provisional` で、operational transcript reference に限定する。

## 4ケースの editorial outcome

| ケース | outcome | 状況 |
| --- | --- | --- |
| case1 | `no_additional_edit` | transcript は概ね許容可能。追加編集なし |
| case2 | `opening_trim_required_then_human_preview` | 冒頭約6秒が無音。opening trim 後の人 preview が必要 |
| case3 | `opening_trim_required_then_human_preview` | 冒頭約6秒が無音。opening trim 後の人 preview が必要 |
| case4 | `internal_gap_removal_required_then_human_preview` | 冒頭に発話はあるが、約2秒から約26秒は大半が無発話。internal gap removal 後の人 preview が必要 |

親候補の無音は許容するが、final short の無音は許容しない。Whisper timestamp による境界の自動確定は `false` とする。

## 証跡レベル

### automated

q5 cold 4 cases exit0、focused S9 123 passed、q5 turbo 16 runs、cold/warm output SHA 一致、production scope 15ファイルの変更前後不変、VTT parity 4/4 を記録する。

### existing_human

S9-4 / S9-5 で確認済みの同一 artifact lineage、range-local invalidation、runtime unavailable 時の日本語 fallback は既存証跡として参照する。これらは今回の実 UI 編集後 preview の確認とは区別する。

### current_ui_pending

今回、実 UI で編集後 preview は未確認である。未完了は次のとおり。

- case2 / case3 の opening trim 後 preview
- case4 の internal gap removal 後 preview
- final short に致命的な無発話がないことの確認
- exact gold / glossary / cue anchor 監査、または S9-6 で採用可能な明示的 waiver

以上が残るため、AC と S9 の完了状態は更新しない。
