# UI 大幅刷新前のリファクタリング・コードレビュー

**監査日:** 2026-08-04
**対象:** `yt-live-kit` v0.3.0、ショート生産ラインから予約投稿までの Streamlit UI
**基準:** ユーザーによるゼロからの手動 E2E 完走、`1645 passed / 2 skipped`
**視覚リファレンス:** `docs/references/u6-short-production-line-v3.2.png`、`.codex/audits/video-detail-ux/06-proposed-video-detail.png`
**現行比較:** `.codex/audits/video-detail-ux/03-detail-top.png`

## 1. 結論

現行フローは手動 E2E でショート生成から予約投稿まで完走しており、service 側には queue、line、upload operation、immutable preview、fingerprint の強い安全境界がある。一方、UI 側は `video_detail.py`、`shorts_line.py`、`upload.py` に表示・session state・永続 state・副作用接続が集中している。リファレンス画像へ見た目を一気に寄せる前に、次の四点を先に直す必要がある。

1. ページやサイドバーの描画だけで別画面の結果や生成 state を混入・変更しない
2. 再生成後の draft と手編集 buffer を revision 単位で区別する
3. 新規投稿候補と既存 upload operation の追跡を分離する
4. UI が表示する「予約可能」と、service が許可する予約 gate を一致させる

この監査では見た目そのものを変更しない。まず pure view model、session key、query、必須 upload adapter と characterization test を作り、その上で視覚刷新へ進む。

## 2. 現状の構造と変更リスク

| 領域 | 現状 | UI 刷新時の主な危険 |
|------|------|----------------------|
| `ui/app.py` | navigation、全ページ共通 header、sidebar、旧結果描画を同じ shell で実行 | 別ページや別動画の旧結果が末尾へ混入する |
| `ui/views/video_detail.py` | summary、workspace、候補引き継ぎ、概要欄更新、ライン、投稿を統合 | component の移動で callback や安全 gate を落とす |
| `ui/components/shorts_line.py` | 6 工程、sidebar preview、line 永続化、telop editor、生成、予約接続を統合 | layout 変更が state 復元・確認 fingerprint・保存順へ波及する |
| `ui/components/upload.py` | 永続 operation 追跡、新規候補、metadata editor、dialog、transaction 接続を統合 | 候補の有無で追跡 UI まで隠す、必須 callback の一部を落とす |
| `ui/components/short_cut.py` | 親候補選択、提案起動、時刻 editor、字幕表示を統合 | 再提案・並び替え後に古い index や時刻が残る |
| UI tests | monkeypatch、AST、source 文字列、関数順への依存が多い | 安全な分割や文言変更でも実装詳細テストが大量に壊れる |

主要 UI は約 9,950 行あり、`video_detail.py`、`shorts_line.py`、`short_cut.py`、`upload.py` だけで約 4,700 行を占める。テスト量は十分だが、意味的な画面契約より private 関数とソース構造を固定するものが残っている。

## 3. Findings

### P1 — UI 刷新前に解消する項目

#### 3.1 旧結果が全ページ末尾へ混入する

`ui/app.py` は `page.run()` 後に page 種別を問わず session 上の `PipelineResult` を `render_results()` へ渡す。ライブラリ、設定、別動画の詳細にも古い全文文字起こしや成果物が表示され得る。正式な四画面 IA と衝突するため、global shell から旧結果描画を外す。

#### 3.2 表示専用 sidebar が session snapshot を復元・保存する

`render_sidebar_line_context()` は全ページで動き、context が無い場合に `_restore_context()` を呼ぶ。この復元は `restore_line_snapshot()` と `_save_context()` を通じて生成用 session state を変更する。sidebar は durable `LineState` と material context から読み取り専用 preview model を作り、正式な復元はショート workspace へ明示的に入った時だけ行う。

#### 3.3 同じ clip の再生成後も古い telop 編集値が残る

telop editor key は video ID、clip ID、行番号だけで、初期化は `setdefault` である。同じ clip ID の draft を再生成すると、本文・時刻・metadata は古いままなのに、新しい artifact ref と fingerprint を付けた document を返し得る。draft 全体の deterministic revision を別 key に保存し、revision が変わった場合だけ editor prefix 内を初期化する。同一 revision の workspace 往復では手編集を保持する。

#### 3.4 新規予約候補ゼロ件で既存投稿の追跡まで消える

詳細画面は `reservable_short_count == 0` の場合に `render_upload_section()` 自体を呼ばない。しかし同 component の先頭には manifest 非依存の関連動画 pending、既存 operation、要照合、publication poll がある。tracking panel は常時表示し、新規 upload candidate だけを manifest gate の内側へ置く。

### P2 — R2 で境界を固定する項目

#### 3.5 表示上の予約可能件数と service gate が一致しない

現行集計は succeeded と file existence だけを見るが、予約直前は canonical path、symlink、spec、input/output fingerprint まで検証する。summary と workspace 推奨値も `can_reserve_shorts_queue_item()` と同じ判定で数える。

#### 3.6 候補引き継ぎが coarse VTT lineage を失う

UI の候補 fingerprint は候補内容と順序だけで再計算される。保存済み `ClipCandidatesLineage` が持つ coarse artifact と cue digest が変わっても候補本文が同じなら、古い選択を有効とみなし得る。clips は保存済み `lineage.candidate_fingerprint` を優先し、legacy または highlights だけ内容 fingerprint へ fallback する。

#### 3.7 ライン開始時に session snapshot が先行する

`_start_line()` は line-mode session snapshot を作ってから line JSON と active pointer を保存する。後段失敗時に session だけが残る。snapshot payload を純粋に準備し、durable state と pointer の保存成功後にだけ session projection を適用する。既存 line / reservation transaction 自体は変更しない。

#### 3.8 投稿安全 gate が任意 callback の組み合わせになっている

現行 caller は正しく接続しているが、`before_preview`、`before_confirm`、`on_operation_started`、`reservation_transaction` は個別 optional である。UI 再構成時に一部を落としても API 上成立する。ライン投稿用には preview 検証と reservation transaction を持つ必須 adapter を一つ渡し、legacy 単体 component と区別する。

#### 3.9 short-cut editor も再提案と並び替えに弱い

時刻 key は candidate ID だけで `setdefault`、親候補は配列 index で保存される。同じ candidate ID の再提案や候補順変更で古い値・別候補を表示し得る。document revision で限定初期化し、親選択は source + ID で保持する。VTT cache は path だけでなく size と `mtime_ns` を key に含める。

#### 3.10 テストが実装構造へ強く結合している

AST、`source.index()`、private 関数抽出、ソース文字列 assertion が安全な component 分割を妨げる。R2 で触る境界から、pure view model の入出力 test と意味的な render contract test へ置き換える。画面全体は Streamlit AppTest または非破壊 browser smoke を追加し、文言や関数順だけを固定しない。

### P3 — 視覚刷新と分けて追跡する項目

- library は 47 本すべての card と button を毎 rerun 描画する。検索に加え paging または表示上限が必要
- `render_shorts_line()` は通常描画では review、spec、output を永続保存しない境界へ直したが、1,500 行超の中に表示と明示 command の配線が同居する。視覚刷新では command helper を維持したまま工程別 renderer へ分割する
- `status_bar._handle_finished_job()` は job 種別ごとの完了処理と UI state 更新を集約している。polling 表示と完了 controller を分ける余地がある
- `views/shorts.py` に出力 path、meta JSON、background job target が残る。worker schema を characterization test で固定後に service へ移す
- 壊れた `line_*.json` は安全に無視されるが、本当の空状態と UI 上区別できない。診断付き resolution model は別 hardening として追加する
- main detail の工程要約と sidebar の工程要約が重複する。視覚刷新時に狭幅 fallback 条件を明示する
- 正確な予約可能件数は canonical gate を再利用するため、現行 schema では各成功動画に ffprobe と全ファイル hash が走る。詳細画面は同じ gate を投稿候補でも再実行するため、見た目の部品を増やす前に request 単位の検証結果共有か、安全な file identity cache を別設計する。投稿確定直前の再検証は cache しない
- `load_latest_shorts_queue_result()` は孤児 manifest の復旧、`list_operations()` と関連動画 summary は legacy queue の正規化を読み込み時に atomic 保存し得る。現行データの通常 rerun は非変更だが、将来「read model」を厳密に純粋化する場合は bootstrap maintenance と read-only peek API を分ける。UI から JSON を直接読む回避策は採らない
- 投稿 workspace は関連動画 summary と source operation 一覧のため同じ queue を複数回 lock 付きで読む。queue service が summary と一覧を一 snapshot で返す API は、視覚刷新後の計測結果を見て別 hardening とする
- 監査後も `video_detail.py`、`shorts_line.py`、`short_cut.py`、`upload.py` は合計 5,000 行超である。R2 は pure projection と command 境界を抜き出す段階に留め、視覚刷新では workspace ごとの renderer 分割を行う

## 4. 変更後の境界

```text
Streamlit view
  ├─ widget value を intent に変換
  ├─ pure ViewModel を描画
  └─ 明示操作時だけ必須 adapter / controller を呼ぶ

ViewModel / query / session key
  ├─ VideoDetailVM: 件数、正確な予約可否、workspace 推奨
  ├─ ShortsLineVM: durable LineState から工程・警告・利用可能 intent
  ├─ UploadWorkspaceVM: tracking と new uploads を分離
  └─ editor revision: draft 同一なら保持、変更なら限定初期化

Durable source of truth
  ├─ line JSON
  ├─ shorts queue manifest
  ├─ upload queue / attempts
  └─ jobs JSON
```

`st.session_state` は workspace、ライン開始前の選択引き継ぎ、未確定の editor buffer、dialog nonce だけに限定する。確認済み state、予約 operation、job state を session だけに置かない。

条件描画から外れた editor buffer は widget の `persist_state="session"` で保持する。この引数は Streamlit 1.59 で導入されたため、宣言最低版も 1.55 から 1.59 へ上げ、lock 解決版 1.60 と整合させる。

## 5. 変更してはいけない安全境界

- `run_line_reservation_transaction()` の line 再検証、upload 開始、operation 記録の順序
- `confirm_and_start_upload()` の immutable requirements、preview 再照合、schedule lock、operation 先行保存、同一 job ID 起動
- upload worker の attempt 記録前に API session を始めない契約と、不明状態を自動再送しない契約
- `confirm_related_video()` の canonical 2 ID 一致と local atomic write。pending は予定公開の hard gate にしない
- queue、review、generation spec、output fingerprint と artifact lineage の fail-closed 判定
- 確認 dialog より前に upload、概要欄更新、削除、再生成を起動しないこと
- worker thread / target から `st.*` を呼ばないこと

単純な `st.tabs` への置換は避ける。Streamlit の tab は非表示 tab の内容も毎 rerun 実行するため、三 workspace は conditional rendering を維持するか、同等に hidden workspace の副作用を止める routing を使う。

## 6. リファレンス画像へ進む実装順

1. page shell と theme を作り、旧 global result を排除する
2. sidebar と header を read-only ViewModel だけで再現する
3. summary cards と三 workspace selector を pure state から再構成する
4. 素材候補と工程 bar を intent ベースで配置する
5. telop / short-cut editor を revision-aware buffer の上へ載せ替える
6. upload workspace を tracking と new upload candidates の二領域に分ける
7. 最後に destructive action と確認 dialog を接続し、mock E2E と手動 E2E を再実行する

各段階で service transaction を view ファイルへコピーしない。既存 controller / adapter の呼び出し点を保ったまま presentation を交換する。

## 7. 検証方針

- 同一 draft の rerun / workspace 往復で手編集が残る
- 同じ ID の新 draft では古い editor buffer が限定初期化される
- coarse VTT lineage だけの変更でも clips の引き継ぎが失効する
- candidate 順変更後も source + ID で同じ親を選ぶ
- line 保存または active pointer 保存失敗時に line-mode session snapshot を残さない
- 予約候補 0 件、manifest 欠損、schedule 利用不可でも既存 operation / pending を表示する
- summary の予約可能件数と `can_reserve_shorts_queue_item()` の結果が一致する
- sidebar 描画前後で session state が変わらない
- library、intake、settings、detail の末尾に旧 `render_results()` が混入しない
- 全件 pytest、lock check、diff check、compileall、非破壊 browser smoke を通す

実 YouTube upload、公開データ変更、Studio 自動操作、追加 Codex 呼び出し、動画再生成、成果物削除はこの検証では行わない。

## 8. R2 実装・検証結果

R2 では視覚デザインを変更せず、前節の P1 / P2 を UI 再配置に耐える境界へ置き換えた。

- global page shell から旧 `render_results()` を外し、完了通知は対象動画 ID、job ID、タイトル、詳細導線だけを保持した
- sidebar は durable state から作る読み取り専用 projection とし、通常描画で line-mode session snapshot を復元・保存しない
- `ui/view_models/`、`ui/session_keys.py`、`ui/queries.py`、`ui/controllers/` を追加し、純粋な表示計算、widget identity、読取入口、ライン投稿 adapter を view から分離した
- telop と short-cut は document 全体、artifact lineage、queue fingerprint を含む draft identity を使い、同一 identity の手編集保持と新 identity の限定初期化を両立した。親候補は source + ID で保持する
- ライン開始は line state と active pointer の両方を保存してから session projection を適用し、どちらの書込み失敗でも元の durable state と session state へ戻す
- legacy line state の読み取り projection は durable timestamp で選び、通常描画では保存しない。最初の明示 command で lock 内 canonical 化してから CAS 検証するため、古い projection から副作用を開始しない
- upload tracking は最新 manifest と独立して表示する。全 source operation の snapshot を一度だけ読み、追跡対象と `state != "failed"` の予約 blocker を別々に投影して service gate と一致させた
- Streamlit widget の session persistence 導入に合わせ、宣言最低版を 1.59、lock 解決版を 1.60 とした

| 検証 | 結果 |
|------|------|
| 変更前の自動回帰基準 | `1645 passed, 2 skipped` |
| 変更後の全件テスト | `1692 passed, 2 skipped` |
| 依存・静的確認 | `uv lock --check`、`uv sync --locked`、`git diff --check`、`compileall` がすべて成功 |
| 隔離ブラウザ確認 | 一時 `YTLK_DATA_DIR` で library / intake / settings の空状態と navigation shell を確認し、旧結果の混入なし。production data は未使用で、一時 data は終了後に削除 |
| 外部副作用 | 実 upload、YouTube / Studio write、追加 Codex / Whisper、動画生成、候補確定、人確認、概要欄更新、成果物削除は未実行 |
| 独立最終レビュー | 実装者と別のサブエージェントが再レビューし、残存 P0 / P1 / P2 なしで PASS |

以上により、P1 / P2 は R2 で解消した。視覚刷新では §3 の P3 を未解消課題の正本とし、とくに 5,000 行超の renderer 分割、canonical gate の ffprobe / hash 再計算、legacy read 時 migration、queue snapshot の重複読取、library paging を見た目の変更と混同せず段階的に扱う。
