# v3 実装エージェント指示プロンプト集

**対象計画:** [execution-plan-v3.md](./execution-plan-v3.md)
**作成:** 2026-08-01
**用途:** オーケストレーター（Claude / Fable 5）が `impl-sonnet` サブエージェント（Sonnet 5、Agent ツール経由）にタスクを渡すときのテンプレートと、完了後のレビュー観点をまとめる

---

## 1. v3 の体制（v2 との違い）

v2 は Cursor 上で Grok 4.5（オーケストレーター）+ Composer 2.5（ワーカー・レビュー）の並列ウェーブ体制だった。**v3 からは Claude Code 上で実施し、体制を変える。**

| 役割 | 担当 | 責務 |
|------|------|------|
| **オーケストレーター + レビュー** | Claude（Fable 5） | 進捗確認、タスク分解、ワーカーへの指示、品質判定、コミット判断。軽微な docs 修正・進捗チェック更新は直接行ってよいが、**大きなコード変更は自分で書かない** |
| **実装ワーカー** | `impl-sonnet` サブエージェント（Sonnet 5、Agent ツールで呼び出し） | 指示されたファイル範囲内での実装・テスト |

**v2 のような並列ウェーブ運用は行わない。** [`docs/execution-plan-v3.md`](./execution-plan-v3.md) のタスクは依存関係が強く、基本は依存順に 1 タスクずつ進める。U / S は計画どおり進め、**P フェーズだけは ID の見かけ順ではなく P1 → P2 → P0 → P3 を必須順序とする。** P1 / P2 の安全契約と全 API mock テストより前に P0 実機 probe を行ってはならない。並列化してよいのは、依存関係の無いことが明確な場合（例: U1 ライブラリページと U4 設定ページは互いに依存しない）に限り、オーケストレーターが個別に判断する。

**P6 の明示承認済み例外:** P6-PLAN の独立レビューと main commit 後に限り、P6-1〜P6-3 は GPT-5.6 luna / max の分離 worktree セッションで並行実装する。P6-1 は telop、P6-2 は description、P6-3 は upload operation / queue の非重複範囲だけを所有し、共有 UI / schedule は 3 件の main 統合後に P6-4 が単独で接続する。P6-1 の main 統合は S9-4 より先に完了し、依存元タスクへ報告する。P6 のオーケストレーターは GPT-5.6 sol として計画、欠陥優先レビュー、修正差し戻し、main 統合、進捗更新を担当する。

**進捗更新:** 通常の `impl-sonnet` ワーカーは `docs/execution-plan-v3.md` のチェックボックスを更新してよい（v2 のウェーブ運用と異なり、並列競合が起きないため）。ただし更新前後で `git diff` を確認し、意図しない箇所を変更していないかレビューする。**P6-1〜P6-4 の分離 worktree は例外として docs を読取専用にし、チェック更新・要件変更・変更履歴追加を行わない。** 完了事実、テスト結果、commit hash を報告し、レビュー PASS と main 統合後にオーケストレーターだけが P6 節を更新する。S9-1 の benchmark / gold 監査節と `.codex/learning/user-decisions.md` は全 P6 セッションの保護対象である。

---

## 2. `impl-sonnet` への指示テンプレート

以下をタスクごとに埋めて、Agent ツールで `impl-sonnet` に渡す。**タスク本文には、必ず次の 6 項目を含めること。**

```text
あなたは yt-live-kit リポジトリの実装ワーカーです。以下のルールに必ず従ってください。

## 必読ドキュメント（作業前に読む）
1. AGENTS.md — 出力ルールと品質基準
2. docs/requirements-v3.md — 該当する FR / NFR / AC のみ
3. docs/execution-plan-v3.md — 【タスク ID】の節（目的・背景・作業・Done 条件）

## タスク ID
【例: U2】

## 参照 docs 節
- docs/requirements-v3.md の FR-◯◯ / AC-◯◯
- docs/execution-plan-v3.md の「### 【タスク ID】: ...」節

## 変更してよいファイル範囲
（execution-plan-v3.md の当該タスクの「変更ファイル範囲」をそのまま転記する。
 それ以外のファイルは、読むのは自由だが編集してはいけない）

## 絶対に守るルール
- UI にビジネスロジックを書かない。処理は services/ に置き、UI は呼ぶだけにする
- 従量課金 API を呼ばない。テキスト生成は Codex CLI サブプロセス、S9 のローカル音声精査だけは whisper.cpp 1.9.1 の外部バイナリを使う（NFR-11）
- ユーザー向けエラーメッセージは必ず日本語にする。スタックトレースをそのまま出さない
- 半角の山カッコ `<` `>` を成果物テキストに出さない。必要なら全角 〈〉 を使う
- 新規依存パッケージを追加しない（yt-dlp / ffmpeg / Streamlit / pydantic / typer /
  google-api-python-client で完結させる）
- 既存のテストを壊さない
- 【フェーズ U のタスクのみ】services/ を原則変更しない。UI 固有の軽量な永続化は ui/ 層内で
  完結させる。限定例外は U3 の services/batch.py における do_chapters / do_clips の入力伝播と、
  U6 の新規 services/shorts_line.py における工程・人確認状態の atomic / fail closed 永続化だけとする。
  U6 でも既存 service の生成・投稿処理と queue fingerprint の意味は変更しない
- 【フェーズ P の実アップロードを伴うタスクのみ】実際に YouTube へアップロードする操作は、
  ユーザーの明示的な承認を得てから実行する。ユニットテストでは googleapiclient を必ずモックする
- 【フェーズ P】本機能は一般 uploader ではなく scheduled-only feature である。privacyStatus=private、
  timezone-aware かつ最低 10 分先の publishAt、notifySubscribers=false を固定し、即時 public / unlisted、
  過去時刻からの即時公開 fallback を実装しない
- 【フェーズ P】Made for Kids と synthetic media は既定値を推測せず毎回ユーザーに必須選択させ、
  Community Guidelines 準拠確認は既定未チェックにする。未選択・未同意では operation / job / API を開始しない
- 【U5 のみ】フェーズ受け入れでは実際の YouTube 概要欄を書き換えない。更新前 / 更新後の表示と
  確定前に update が呼ばれないことを安全に手動確認し、update / mark の成功・失敗はモックと
  隔離した一時 data_dir で検証する
- 【S9 のタスクのみ】`subtitles/ja.vtt` を上書き・改名・自動置換しない。S9-0 では incoming VTT を隔離し、既存 `ja.vtt` がある場合は bytes を保持して `subtitles/sources/` へ immutable 保存する。親候補探索は YouTube VTT、選択済み区間は provenance 付き `TranscriptArtifact` を resolver から受け取り、同じ artifact reference / fingerprint と順序付き使用区間 cue digest を cutplan / telop / queue / line / review へ渡す
- 【S9 のタスクのみ】字幕なし・低品質字幕の全編 Whisper、47 本の一括 backfill、local video 入力、`asset_id` への path 移行は実装しない。音声のみを選択区間へ使い、現行 1 ジョブ制約と人の境界確認を維持する
- 【S9 のタスクのみ】whisper-cli の version / capability / model fingerprint / JSON schema を実行前に検証し、未知形式・部分 artifact・cache 不一致は高精度扱いにしない。モデル自動取得、shell command の自由入力、実 YouTube 書き込みは禁止する
- 【T1 のタスクのみ】S9-6 は最終受け入れ専用として開いたまま、`S9-5 → T1-PLAN → T1-1 → T1-2 → T1-3 → T1-4 → T1-5 → S9-6` の順を守る。低信頼行は元時刻を維持して全件 flag とし、独立 timing confirmation、全文確認、最終 preview を混同しない。通常 rerun で Codex / Whisper / ffmpeg / upload を起動せず、`subtitle_burn.py`、FFmpeg、cut 境界、queue fingerprint、投稿予約、Codex 回数を変更しない
- 【T1-PLAN のみ】変更は `docs/execution-plan-v3.md`、`docs/requirements-v3.md`、`docs/v3-agent-prompts.md`、`docs/tech-stack.md` に限定する。ADR、コード、tests、benchmarks、fixture / data、production artifact / cache、`.codex/learning/user-decisions.md`、skill pointer を作成・編集しない。方式選定は T1-2 の ADR まで保留する
- 【P6 のタスクのみ】実 YouTube upload、公開データ変更、YouTube Studio のブラウザ自動操作を行わない。googleapiclient は必ずモックし、関連動画は local operation の手動確認状態だけを扱う
- 【P6-1〜P6-3】execution-plan-v3.md と requirements-v3.md は読取専用。割り当てられた変更ファイル範囲外、S9-1 監査節、`.codex/learning/user-decisions.md` を編集しない。worktree 内でタスク ID 入り commit を作り、main への merge / cherry-pick は行わない
- 【P6-4】P6-1〜P6-3 が main に統合された commit から開始する。UI / schedule の単一 writer として service の公開 API を接続し、UI に validator・queue 状態遷移・pending 集計を複製しない。P6-1 所有の `prompts/telop_script.md`、`services/telop.py`、`tests/test_telop.py` は変更しない

## Done 条件
（execution-plan-v3.md の当該タスクの「Done 条件」をそのまま転記する）

## テスト実行コマンド
uv run pytest

## 完了後に必ず行うこと
1. docs/execution-plan-v3.md の当該タスクのチェックボックス（作業・Done 条件）を
   完了した事実だけ `- [x]` にする。未確認の項目にはチェックを入れない。
   P6-1〜P6-4 は例外として docs を編集せず、完了項目を報告に列挙する
2. タスクが「大きな実装タスク」（execution-plan-v3.md §3.4 のリスト）に該当する場合、
   コミットが必要である旨を報告に明記する（通常タスクのコミット自体はオーケストレーターが行う）。
   **P6-1〜P6-4 は例外として、担当範囲だけをタスク ID 入りで worktree commit し、commit hash を報告する。main への merge / cherry-pick と進捗更新はオーケストレーターだけが行う**

## 完了時の報告フォーマット（必ずこの形式で報告する）
### 実装したもの
- （箇条書き。ファイルパスと関数名を含める）
### テスト結果
- `uv run pytest` の結果（件数と所要時間）
### 設計判断
- 迷った点と、どちらを選んだか、その理由
### 申し送り
- 未対応で残したこと、気づいた別の問題（あれば）
```

### 2.1 S9 実行セッション用テンプレート

S9 は通常テンプレートに次のブロックを追加して `impl-sonnet` へ渡す。S9-0〜S9-6 は順番を飛ばさず、前タスクの commit SHA と Done / AC の証跡を次タスクへ添付する。

```text
## S9 実行コンテキスト
- S9-0: 既存 VTT 互換・非上書き保存。incoming を隔離し、初回だけ `ja.vtt` を bootstrap、再取得は `subtitles/sources/{source_fingerprint}.vtt` へ保存し、失敗時は既存 bytes / downstream を変更しない
- S9-1: 代表素材 benchmark・モデル決定。production 非変更。固定 gold transcript / 固有名詞 glossary と事前宣言 gate で精度・固有名詞・cue 品質・wall time・peak memory・cache を VTT と A/B 比較し、採用または No-Go を docs に残す
- S9-2: TranscriptArtifact / resolver / cue digest / persistent cache。strict schema、cache identity と artifact fingerprint の分離、候補 VTT lineage、既存 ja.vtt untouched、用途別 resolver と範囲単位の失効を pure service で固定する
- S9-3: whisper.cpp 1.9.1 runtime / capability / model settings / 音声のみの span。複数区間を 1 ジョブ内で入力順に処理し、未知出力・部分成功・model mismatch は fail closed にする。audio-only ytdlp helper と job / range / retry contract を含む
- S9-4: VTT で親候補を選び、選択区間だけ Whisper。cutplan / telop / queue / line は同一 immutable artifact snapshot を再利用し、artifact fingerprint / ordered used_range_cue_digest を review fingerprint まで伝播する。legacy line confirmation は再利用しない
- S9-5: 設定・明示 CTA・job ID 付き進捗・日本語エラー・coarse fallback・使用範囲だけの失効表示。UI で schema / fingerprint を再実装しない
- S9-6: 同じ 3〜5 本の代表素材と、可能なら実配信アーカイブ 2 本以上で A/B、境界・人確認・cache restart・failure injection・全回帰を通して Go / No-Go を判定する。Go でない限り S9 を完了扱いにしない

## S9 固定禁止事項
- 全編 Whisper を通常経路にしない
- 音声精査のため動画 mp4 全体を取得しない
- `subtitles/ja.vtt`、既存 full / compressed transcript、既存 cutplan / telop / mp4 を黙って削除・置換しない
- Whisper timestamp だけで boundary を確定しない。padding / 必要な VAD / preview / 人確認を残す
- 使用区間外の字幕変更で無関係な downstream を失効させない。使用区間内変更・artifact 不一致は fail closed にする
- cache identity と artifact fingerprint を混同しない。unknown field、破損 index、partial artifact、range / padding / cue inclusion rule 不一致を高精度として返さない
- 新規 pip 依存、従量課金 API、モデル自動ダウンロード、自由な shell command を追加しない

## S9 完了報告に必ず含めるもの
- 対象タスク ID、前タスク commit SHA、変更ファイル一覧
- `uv run pytest` の件数と対象テスト、benchmark / 実機証跡の有無
- artifact schema、source_kind、model / runtime / settings、audio / artifact fingerprint、cue digest、失効理由
- 既存 `ja.vtt` の前後 hash、source VTT の保存先、候補 artifact / candidate fingerprint、gold / glossary / 評価 gate
- cache hit / miss、複数区間の処理順、1 ジョブ制約、timeout / malformed output / partial failure の挙動
- 既存 VTT 経路、人確認、FR-30 / FR-22 / FR-25 / FR-33 への伝播と、未対応の将来範囲
```

### 2.2 T1 実行セッション用テンプレート

T1 は S9-5 の後に開始し、S9-6 を最終受け入れ専用として開いたまま、下記の順で 1 タスクずつ実行する。通常テンプレートに次のブロックを追加し、前タスクの commit SHA と独立 review の結果を必ず添付する。

```text
## T1 実行コンテキスト
- T1-PLAN: docs-only。T1-1〜T1-5 の責務、固定契約、R2 安全境界、AC-40 を定義済み。方式選定 ADR は作らない
- T1-1: production 非変更 spike。長い単一 cue、multi / cross-cue、VTT fallback + 連結を各 20 行以上、全体 60 行以上で固定 manifest 化し、人音声 line onset gold、coverage 分母、pooled / 群別 Go gate を測定前に固定する。manifest に有限整数 `max_selected_spans` / `max_whisper_invocations` を明記し、現行 artifact に raw token timing が無い場合だけ、その上限内の選定済み span を隔離 temp へ bounded に whisper-cli 再実行して runtime / model fingerprint、再現 command、raw full JSON hash を記録する
- T1-2: T1-1 Go 後だけ着手。Artifact v2 と immutable timing sidecar を ADR で比較し、full JSON 一回の結果から strict / atomic timing payload を保存する。既存 artifact は backfill せず timing 無し fallback とする
- T1-3: Codex draft 後、人確認前の pure monotonic aligner。唯一高信頼行だけを補正し、低信頼・要約・省略・重複・cross-cue 曖昧は元時刻 + flag。telop lineage に policy / provenance / fingerprint を伝播する
- T1-4: Streamlit に synchronized / timing review required / cannot sync を表示し、現在の start / end editor と独立 timing confirmation を接続する。low-confidence がある場合だけ timing gate を必須にする
- T1-5: 同期 component acceptance として A/B、gold、in / out-range 失効、cache restart、failure / fallback、legacy、scope guard、全 pytest、diff、compileall、隔離 data_dir / 検証用 copy への再生成 preview をまとめる。production artifact / cache / output / hash は不変にし、完了しても S9-6 formal phase acceptance と AC-40 は未完了のままにする

## T1 固定禁止事項
- 低信頼行を自動移動しない。元時刻を維持し、全件 flag と日本語警告を表示する
- 各低信頼行への無意味な編集を要求しない。全文の誤字・固有名詞確認とは別に、要確認行の時刻確認を明示的に記録する
- 本文・時刻・alignment input・policy の変更、範囲変更、A → B → A、cache restart で timing confirmation を再利用しない
- owning cue / range clamp、時系列、非重複、最低表示 500 ms を満たさない補正は fallback にする。token end を唯一の正本にしない
- **Whisper 実行境界:** T1-1 だけは、固定 manifest の選定済み span に対象・回数の上限を設け、isolated temp へ bounded に whisper-cli を再実行してよい。production data / artifact / cache / output / hash は不変とする。T1-2 以降、本番経路、manifest 外の区間、全動画再解析、全編 Whisper、47 本 backfill は禁止する。実 upload、公開データ変更、Studio 操作、従量課金 API、新規依存も行わない
- `subtitle_burn.py`、FFmpeg、cut 境界、queue fingerprint、投稿予約、Codex 回数を変更しない。UI に validator / aligner / fingerprint の business logic を複製しない

## T1 完了報告に必ず含めるもの
- 対象 T1 task ID、前タスク commit SHA、変更ファイル一覧、許可範囲との差分
- T1-1 の場合は manifest fingerprint、3 fixture 群の行数、人音声 gold、coverage 分母、pooled / 群別 gate、群別 signed bias、VTT fallback + 連結の非回帰結果、`max_selected_spans` / `max_whisper_invocations` と実績、bounded whisper-cli の再現 command / runtime / model / settings fingerprint / raw full JSON hash、production hash unchanged
- T1-2 以降は timing status、low-confidence 行数、元時刻維持数、fallback 数、policy / provenance / parent / payload fingerprint、失効事由
- UI / acceptance の場合は全文確認、独立 timing confirmation、final preview、通常 rerun における外部処理無し、cache restart / failure / legacy 証跡。T1-5 は component acceptance、S9-6 は formal phase acceptance であり、隔離 preview と immutable evidence の再利用条件も明記する
- `uv run pytest`、`git diff --check`、`uv run python -m compileall -q src` の結果。docs-only の T1-PLAN は pytest 実行対象外と明記する
- 未対応、No-Go / fallback-only、S9-6 を完了扱いにしていないこと、次タスクへの依存と申し送り
```

---

## 3. オーケストレーターのレビュー観点チェックリスト

`impl-sonnet` の報告を受け取ったら、次を確認してから進捗チェックの承認・コミットに進む。

### 3.1 共通観点（全タスク）

- [ ] **要件との差分**: `docs/requirements-v3.md` の該当 FR / AC を満たしているか。実装が要件より狭い／広い場合、その理由が報告にあるか
- [ ] **テストの実在と通過**: 報告された `uv run pytest` の件数が、変更前より増えているか（新規テストが実際に追加されているか）。`git diff --stat` でテストファイルの変更を確認する
- [ ] **スコープ外への逸脱がないか**: [`docs/requirements-v3.md` §2](./requirements-v3.md) で明示的にスコープ外としたもの（BGM・SE・ズーム・トランジション、即時公開投稿、Cookie 認証等）に手を出していないか
- [ ] **変更ファイル範囲の逸脱がないか**: `git diff --stat` が、指示した「変更してよいファイル範囲」に収まっているか
- [ ] **破壊的操作の確認ダイアログ有無**: 削除・上書き・概要欄反映・投稿を伴う実装では、`st.dialog` による確認、または差分プレビューが実装されているか（U2 以降のタスクに適用）
- [ ] **概要欄更新経路の一本化**: U5 以降、旧 `history.py`、公開ナビゲーション、別の UI ヘルパーに、差分プレビューを迂回して `update_video_description()` を呼べる経路が残っていないか
- [ ] **日本語エラーメッセージ**: 新規追加したエラーパスがすべて日本語で、原因と対処が書かれているか。スタックトレースの直接表示が無いか
- [ ] **半角 `<>` の混入がないか**: プロンプトテンプレート・生成テキストの出力ルールに半角山カッコ禁止が明記され、バリデーションされているか（S1 のテロップ台本・メタデータ生成で特に重要）
- [ ] **従量課金 API を呼んでいないか**: AI 生成はすべて Codex CLI 経由か。新しい HTTP API 呼び出しを追加していないか

### 3.2 フェーズ U 特有の観点

- [ ] `git diff --stat` の `src/yt_live_kit/services/` 変更がタスクごとの限定例外内か。U3 は `services/batch.py` の入力伝播だけ、U6 は新規 `services/shorts_line.py` のライン安全状態だけを許可し、既存 service の挙動を変えていないか。U5 は `services/youtube_api.py` を変更しない
- [ ] UI 固有の軽量な永続化（チャンネル既定ハンドル等）が `ui/views/_local_settings.py` で完結しているか。生成・投稿可否を決める U6 の工程 / 人確認状態を UI 層だけへ置かず、限定例外 `services/shorts_line.py` で atomic / fail closed に扱っているか
- [ ] `ui/pages/` に相当する旧ディレクトリ・旧 import パスが残っていないか（U0 完了後は全タスクで確認）
- [ ] U5 完了時の IA が公開 3 画面（ライブラリ / 取り込み / 設定）+ `visibility="hidden"` の動画詳細 1 画面であり、`history.py` / `url_path="history"` が残っていないか
- [ ] 旧「処理済み一覧」を削除しても、v1 / v2 のストレージ管理が `ui/components/storage_manager.py` 経由で設定ページから利用できるか。10 件を超えても 11 件目以降を含む全動画へ到達でき、各動画の元動画容量と個別削除導線、個別 / 一括削除の確認ダイアログ、成果物保持が回帰していないか
- [ ] U5 の概要欄反映が、外側 primary ボタン → OAuth / チャプター検証 → fetch / merge → `st.dialog(width="large")` の更新前 / 更新後表示 → 確認時だけ update → 成功後だけ mark、の順になっているか。確定前のダイアログ再描画だけは取得済みプレビューを再利用し、確定時は既存 update 内部の fetch を維持しているか
- [ ] update 失敗時に mark されず、YouTube 更新成功後のローカル mark 失敗は「YouTube 側は更新済み」と分かる日本語警告になるか
- [ ] ステッパー・確認ダイアログ・共通コピー部品が、複数ページで同じ実装を再発明していないか（`ui/components/clipboard.py` を再利用しているか）
- [ ] U6 の 3 ワークスペースと 6 工程が別概念として実装され、フル工程はショート作成内、縮約工程は左パネルに常設されているか。手動切り替えがラインを破棄せず、工程 6 の明示 CTA だけが対象を保持して公開・投稿へ移るか
- [ ] U6 の左パネルが生成前 / 生成中 / 生成後 / 元素材欠損でプレビューを切り替え、編集・確定ボタンを重複配置していないか。折り畳み時もメイン上部に縮約工程が残るか
- [ ] U6 の品質表示が自動ハード判定 / 自動警告 / 人の全文確認 / 生成条件に分離され、人確認が既定未チェックか。1 行 16 文字超だけで生成を禁止していないか
- [ ] review fingerprint が `(video_id, clip_id)`、既存 queue fingerprint、canonical 台本 JSON を含み、本文・強調・メタデータ編集で確認を失効させるか。元に戻しても自動復帰せず、生成直前に再検証するか
- [ ] `line_{clip_id}.json` が atomic 保存され、欠落・破損・出力変更時に証明できない台本確認 / 最終確認を未確認へ戻すか。queue fingerprint の既存意味を変更していないか
- [ ] `output_fingerprint` が video / clip / review fingerprint、解決済み絶対パス、size、mtime_ns、mp4 内容 SHA-256 を含み、工程 6 直前の不一致で最終プレビュー確認だけを失効させるか
- [ ] `active_line.json` が atomic で、無効・欠落時は非完了 line を updated_at 降順・clip_id 昇順で決定的に復元し、完了済み line を勝手に再開しないか
- [ ] 「本日のライン完了 N／3」が現在の `SchedulePolicy.timezone` の日付と、同一 `(source_video_id, source_kind, clip_id)` の当日最新 operation で集計され、LA 基準 upload attempt 台帳と混同されていないか。timezone 変更時は現在値で再集計し、失敗・要照合は完了数から除外され「要対応 N 件」になるか
- [ ] 行別エディタが本文・時刻・行全体の強調を扱い、ユーザー編集差分を「AI案から変更」とだけ表示するか。差分だけで全文確認を完了できず、証明不能な「Codex が修正」表示がないか

### 3.3 フェーズ S 特有の観点

- [ ] Codex CLI 呼び出しが 1 区間セットあたり 1 回で完結しているか（テロップ台本とメタデータを別々に 2 回呼んでいないか、S1）
- [ ] `-ss` を `-i` の前に置く入力シークになっているか（`encode_segment` を正しく使っているか、長尺後半の decode を省き、再エンコードで 0 秒始まりの中間ファイルを作る契約、S3）。境界 frame と速度の比較は G1 で行う
- [ ] 複数区間連結時の字幕タイムオフセットが、区間の**累積**尺で正しく計算されているか（区間ごとの `start_sec` だけを引くと連結後にズレる、S3）
- [ ] S3 が `NormalizedSegmentBounds` / 公開正規化 helper の整数 ms を ID・尺・encode・字幕 offset / clip の唯一の基準にし、入力順を再生・ID・encode 順として保持して sort / dedupe していないか。ffmpeg 前と `build_concatenated_subtitle()` の公開境界で全区間と確認済み telop document を再検証しているか
- [ ] S3 の最終 mp4 が一時 `.mp4` への生成成功後だけ atomic replace され、失敗時に既存正式 mp4 が維持されるか。`keep_intermediate` が専用中間ディレクトリ全体へ成功・失敗とも適用され、元動画・ASS・S1 JSON・最終 mp4 / ログを削除していないか
- [ ] テロップ焼き込みが「Codex の生成結果をそのまま焼き込む」のではなく、**人が確認・修正できるステップを経てから**焼き込む設計になっているか（S1・S4）
- [ ] 180 秒超の区間選択が、エラーで落ちるだけでなく分割・短縮を促す案内になっているか（S3 のエラーメッセージ + S4 の UI 誘導）
- [ ] S4 の候補ソースは snapshot form 外の前段で選択・即 rerun され、候補ソース文書の表示順が選択順として固定されているか。その後の form で個別 / 連結、layout、通常 / Hook preset を snapshot 化し、変更時に draft / 確定が全失効するか
- [ ] S4 の Codex 呼び出しは対象カードの明示的な生成 / 再試行 submit 時だけで、通常 rerun / snapshot submit では session state の draft を再利用するか。確定後は editor が非表示で、全 snapshot 一致 + 全確定時だけ開始可能か
- [ ] `save_confirmed_telop_script()` が修正済み台本を入力区間で再検証し、`ConfirmedTelopScriptResult(path, document)` として保存 path と正規化 document を返すか。S1 と同名の JSON へ atomic 保存し、失敗時に既存 JSON を維持し、返却 document を焼き込みへ渡すか
- [ ] S4 の整数 ms 尺検証が Codex 前に行われ、180,000 ms 超は分割・削減・短縮を案内して Codex / job / ffmpeg を呼ばないか。異なる候補ソースの混在や同一 clip ID 衝突を暗黙 sort / dedupe せず拒否するか
- [ ] 候補変換、個別 / 連結 target 構築、衝突 / 尺検証、fingerprint、失効 / 開始可否、直列化が `services/shorts_queue.py` の pure 関数にあり、UI が再実装していないか。fingerprint は video ID、元候補全内容、表示順、正規化全 segments、source / mode / layout / 両 preset を含むか
- [ ] `ShortsQueueClipSpec` が frozen primitive segment tuple と canonical `model_dump(mode="json")` 台本 JSONによる deep immutable snapshot で、`to_dict()` / `from_dict()` が型・区間・台本を再検証するか。output name は決定的な `short_{clip_id}.mp4` で自動 suffix を付けず、衝突を拒否するか
- [ ] 台本、layout、preset、Hook preset、output name が `run_shorts_queue()` から S3 へ欠落なく伝播するか。空入力は queue 全体エラー、1 本の失敗は item softfail、全件失敗でも結果を残すか
- [ ] `run_shorts_queue()` が manifest の唯一の writer で、schema version、UTC created / updated timestamp、item Path の文字列化、Pydantic JSON、count を `to_dict()` / `from_dict()` で検証し、開始時と各 item 後に atomic 更新するか。`ShortsQueueResult.manifest_path` は JSON / `to_dict()` に保存せず、loader が実際の読込元 path を必須注入し、JSON 内の偽 field を拒否するか。job target は spec の再構築と report bridge だけか
- [ ] `start_job()` の返却 job ID を video ID 別 session state map に即保持し、現在 video ID 用 manifest だけを表示するか。新 manifest 作成前は旧 latest でなく準備中を表示し、現在動画の key が無い場合だけ当該動画の `(created_at, job_id)` 降順 tie-break latest へ fallback するか。動画 A → B → A の切替で他動画の結果が混ざらないか
- [ ] 既存 mp4 がある S4 開始は、不変の上書き対象一覧を表示する `st.dialog` の確定ボタンだけが `start_job()` を呼び、busy、キャンセル、確定前再描画、二重クリックで始まらないか
- [ ] S4 結果が manifest 順に `st.video(Path)` で表示され、mp4 保存は引数なし callable + `on_click="ignore"` + `width="stretch"` で遅延読み込みし、出力欠損 / font warning / item error を日本語表示するか。コピー key が job ID + target ID + 種類で一意か

### 3.3.1 S9 特有の観点

- [ ] S9-0 が incoming VTT を隔離し、既存 `ja.vtt` の bytes / downstream を再取得・失敗・crash 後も保持し、新しい source VTT を immutable 保存しているか。初回 bootstrap と既存保存の境界を `tests/test_ytdlp.py` で確認しているか
- [ ] S9-1 が production 非変更で代表素材を使い、YouTube VTT と候補 whisper.cpp モデルの CER・固有名詞・cue 品質・wall time・cache hit を同一条件で比較し、採用または No-Go の根拠を docs に残しているか
- [ ] S9-1 が gold transcript、固有名詞 glossary、選択 range、CER 相対改善 10％、固有名詞 exact match 非悪化、cue 欠落 / 重複 baseline +5％以内、wall time / peak memory budget を実行前に固定し、未達を No-Go としているか
- [ ] `TranscriptArtifact` が source kind、取得元、既存 video ID、対象 range、絶対時刻 cue、cue digest、audio input fingerprint、model / runtime / settings、artifact fingerprint、schema version を持ち、canonical JSON と atomic 保存を使っているか
- [ ] schema が strict / unknown field 拒否、整数ミリ秒、status（success / fallback / failed / partial）を持ち、cache identity と artifact fingerprint を分離しているか。audio bytes / codec / ffmpeg 設定と model file / binary build / capability / decode / VAD / padding を path / mtime の代用にしていないか
- [ ] `subtitles/ja.vtt` を上書きせず、親候補探索は YouTube VTT、選択区間は用途別 resolver の有効な Whisper artifact を使っているか。source / model / settings / range / cue digest 不一致を高精度として返していないか
- [ ] coarse candidate が VTT artifact fingerprint、全 cue digest、candidate fingerprint を持ち、FR-31 / cutplan / downstream の lineage が候補再生成時に fail closed になるか
- [ ] S9-3 が whisper-cli 1.9.1 の binary / version / capability / model fingerprint / JSON schema / timeout を実行前に検査し、未知出力・partial result・cache corruption を fail closed にしているか。モデル自動ダウンロードや自由な shell command がないか
- [ ] 音声のみの入力を使い、選択した複数区間を現行 1 ジョブ内で入力順に処理しているか。per-range status、job ID、range index、retry 可否があり、全編 video download、全編 Whisper、47 本 backfill、local video / asset ID 移行が混入していないか
- [ ] S9-4 が同じ immutable artifact reference / object / fingerprint / ordered used-range cue digest を short_cut、telop、queue / line、review fingerprint へ伝播し、`ja.vtt` の直接再読込や resolver 再実行で別結果を作っていないか。legacy line confirmation を再利用していないか
- [ ] Whisper timestamp だけで境界を確定せず、padding、必要な VAD、既存 cue、整数ミリ秒正規化、動画 preview、人確認を維持しているか
- [ ] 使用区間内の字幕変更・artifact 不一致・model / settings / input 変更は cutplan / telop / review を fail closed で失効させ、使用区間外だけの変更で無関係な downstream を失効させていないか。人確認を元に戻った本文から自動復帰させていないか
- [ ] S9-5 の UI が候補 card、cutplan panel、telop editor、final review banner で同じ provenance を示し、明示 CTA、対象 range、runtime / model、job ID、段階、現在区間、cache hit / miss、fallback、失効理由を日本語で示し、UI に schema / fingerprint / resolver の業務ロジックを複製していないか
- [ ] S9-6 が同じ 3〜5 本の fixture、可能なら実配信アーカイブ 2 本以上で既存 VTT / S6 / U6 / FR-25 の非回帰、cache restart、failure injection、A/B の精度・時間・人確認を確認し、未確認の AC を先に `[x]` にしていないか
- [ ] S9 で新規 pip 依存、従量課金 API、モデル自動取得、YouTube upload / description write、README 更新が追加されていないか

### 3.3.2 T1 特有の観点

- [ ] **T1-PLAN の変更範囲:** 4 docs 以外に差分がなく、ADR、コード、tests、benchmarks、fixture / data、production artifact / cache、learning log、skill pointer を作成・編集していないか。方式選定を先取りしていないか
- [ ] **依存と進捗:** `S9-5 → T1-PLAN → T1-1 → T1-2 → T1-3 → T1-4 → T1-5 → S9-6` が崩れていないか。T1-1 が次の未着手で、S9-6、S9、M16、AC-37 を未確認のまま残しているか
- [ ] **T1-1 固定評価:** manifest が測定前に fingerprint 付きで固定され、3 fixture 群が各 20 行以上、合計 60 行以上あり、人音声 line onset gold、coverage 分母、有限整数 `max_selected_spans` / `max_whisper_invocations`、CER / 固有名詞 / cue 欠落重複 / wall / peak memory、pooled / 群別 gate、各群の signed bias が同じ証跡にあるか。結果後の閾値緩和がないか
- [ ] **T1-1 production 非変更:** `src/`、既存 `tests/`、既存 `data/`、production artifact / cache / output / hash、S9-1 監査証跡が変更されておらず、許可される Whisper 実行が固定 manifest の選定済み spanを隔離 tempへ bounded に再実行する benchmark だけに限定されているか。manifest 外、全動画再解析、backfill、実 upload、外部 API が無いか。VTT fallback + 連結は現行出力同等・自動移動 0 で、低信頼の黙った移動、誤った line / cross-cue 移動が 0 か
- [ ] **T1-2 保存:** `docs/adr/0001-telop-timing-persistence.md` の Artifact v2 / immutable sidecar 選択が実装と一致し、parent、normalized token payload または raw full JSON hash、model / runtime / settings / ranges、schema / policy version、自身 fingerprint、atomic 保存・再検証が揃っているか。legacy / VTT は timing 無し fallback で、T1-1 の isolated benchmark 例外を除く T1-2 以降・本番経路の追加 Whisper、manifest 外の解析、backfill が無いか
- [ ] **T1-2 token 安全性:** whitespace / metadata、zero / reverse end、日本語 subword、未知 field、範囲不一致、cache restart / crash を検証し、token end を時刻の唯一の正本にしていないか
- [ ] **T1-3 pure alignment:** 一意高信頼行だけが補正され、低信頼・要約・省略・重複・cross-cue 曖昧は元時刻 + flag か。owning cue / range clamp、時系列、非重複、最低表示 500 ms を満たさないと fallback になるか
- [ ] **T1-3 lineage / 非回帰:** policy、provenance、parent / payload fingerprint が telop review lineage に含まれ、text / time / alignment / policy 変更と A → B → A で失効するか。subtitle burn、FFmpeg、cut、queue fingerprint、投稿予約、Codex 回数が変わっていないか
- [ ] **T1-4 UI gate:** synchronized / timing review required / cannot sync と日本語の低信頼警告、現行 start / end editor、独立 timing confirmation が表示され、low-confidence が無い場合に gate を不要とするか。全文確認と final preview を維持し、編集を強制していないか
- [ ] **T1-4 state safety:** UI に business logic を複製せず、session state は draft / widget identity に限定されるか。通常 rerun、表示切替、再起動で Codex / Whisper / ffmpeg / upload が動かず、証明できない confirmation を復元しないか
- [ ] **T1-5 acceptance:** 同期 component acceptance として A/B、gold、in / out-range 失効、cache restart、failure / fallback、legacy、scope guard、全 pytest、diff、compileall、隔離 data_dir / 検証用 copy への再生成 preview、production artifact / cache / output / hash unchanged が揃うか。T1-5 PASS でも S9-6 formal phase acceptance の人 preview / A-B / gold / 失効 / cache / fallback / scope gate と AC-40 を先行完了しておらず、実 upload・公開データ変更・Studio・全編 Whisper・47 本 backfill が無いか

T1 の判定は task ごとに行う。T1-1 No-Go は T1-2 の着手条件を満たさず、T1-5 PASS も S9-6 の最終受け入れを先取りしない。

### 3.4 フェーズ P 特有の観点

- [ ] **実際の YouTube アップロードは、ユーザーの明示的な承認を得てから実行しているか。** `impl-sonnet` が自律的に実アップロードを実行していないか（P0 のテストアップロードも含め、必ずユーザーに実行前に確認する）
- [ ] P1 / P2 の安全契約と全モックテストが P0 より先に完了し、P0 用の簡易 upload 経路が無いか。P0 実 upload、審査フォーム提出、P3 実予約公開に別々の明示承認があるか
- [ ] P0 承認には、private lock が非該当なら probe 動画が指定時刻に public となり得ることまで明示されているか
- [ ] `privacyStatus="private"`、未来 10 分以上の aware `publishAt`、UTC RFC 3339 `Z`、`notifySubscribers=false` が固定か。即時 `public` / `unlisted`、publishAt 無し、過去時刻 fallback を許していないか
- [ ] Made for Kids / synthetic media が毎回必須選択で、preview / snapshot / `status.selfDeclaredMadeForKids` / `status.containsSyntheticMedia` が一致するか。Community Guidelines checkbox は既定未チェックで、未同意時に side effect が無いか
- [ ] channel ID / 名称、ファイル、size / duration、title / description / tags、schedule、audience / synthetic / consent、notify false が preview にあり、確定後に channel / file / content / slot / attempt を再検証するか
- [ ] title 非空・100 文字、description UTF-8 5000 bytes、`",".join(tags)` 500 文字、全 metadata の半角山カッコ禁止を API 前に検証するか
- [ ] `MediaFileUpload(resumable=True)` と同一 request の `next_chunk()` を使い、network と 500 / 502 / 503 / 504 だけを bounded backoff するか。4xx / result unknown で新しい `videos.insert` を自動実行せず `needs_reconciliation` にするか
- [ ] operation が全必須 field と状態を atomic / lock 付きで保持し、壊れた JSON が fail closed か。同一 job / operation の二重実行、再起動時の `uploading` / `needs_reconciliation` が insert を再送しないか
- [ ] 予約 slot と full operation が単一 `queue.json` record の正本で、operation ID / job ID を先行保存しているか。`jobs.close_orphans()` → upload recovery の順で、queue 保存、job JSON 作成、thread 起動、uploading 保存、attempt 記録の各クラッシュ境界を検証し、attempt ledger を正本として active state だけを recovery するか。active は attempt 無しで failed + slot 解放、attempt 有りまたは ledger 読込不能で needs_reconciliation + slot 保持、terminal は不変、terminal / ledger 不整合は queue / slot 非変更 + 全新規 upload fail closed か
- [ ] `upload_job_target` が jobs 契約どおり `job_id` を受け、`YouTubeAPIError` / upload queue error が既知例外か。status bar は upload / shorts queue / batch の完了結果を pipeline loader へ渡さないか
- [ ] Video Uploads 専用上限を公開予定日でなく America/Los_Angeles の upload attempt 開始日で数え、resumable upload session 前に attempt を atomic 記録し、失敗も数えるか。read-only `channels.list` は数えず、`YTLK_VIDEO_UPLOAD_DAILY_LIMIT` の 1〜100 と上限超過、予約 slot との分離をテストしているか
- [ ] `SchedulePolicy` が厳密な `HH:MM`、`interval_days >= 1`、IANA `ZoneInfo`（既定 Asia/Tokyo）、aware now、DST、API UTC `Z` を検証するか。confirm race を固定 lock 順序で 1 operation に直列化するか
- [ ] `videos.list(part="status,processingDetails")` が processing 10 秒 × 30、公開 30 秒 × 20、明示 terminal / timeout、fake clock / sleep の契約を持つか。全応答を時刻・phase・status / processingDetails・classification 付き typed history へ追記し round-trip するか。publishAt 欠落または予約 + 5 分 private を suspected とし、Studio 確認まで private lock を確定・成功扱いしていないか
- [ ] `googleapiclient` の実呼び出しがユニットテストでモックされているか（実アップロードを伴うテストが CI 相当の自動実行に含まれていないか）

---

## 4. フェーズごとの注意点（要約）

| フェーズ | 最重要の注意点 |
|----------|----------------|
| **U** | `services/` は原則触らない。例外は U3 の `services/batch.py` 入力伝播と、U6 の新規 `services/shorts_line.py` による工程・人確認状態の atomic / fail closed 永続化だけ。UI 固有の軽量設定は引き続き `_local_settings.py` に置く（[execution-plan-v3.md](./execution-plan-v3.md) §3.2 参照） |
| **S** | 従量課金 API を使わない。テキスト生成は Codex CLI、S9 の区間精査は whisper.cpp 1.9.1 のみ。S9 は VTT で親候補を探し、選択区間だけを `TranscriptArtifact` に固定して再利用する。**自動生成をそのまま焼き込まず、必ず人の確認ステップを挟む** |
| **P** | P1 / P2 を全 API mock で完成してから、同じ本番経路だけを P0 / P3 で別承認により実操作する。private + future publishAt + notify false、audience / synthetic / consent、LA attempt、resumable / reconciliation、永続 operation、confirm race、polling を固定する。private lock 中は P3 を成功扱いにしない |
| **R1** | 既存 `build_short()` の legacy 経路は、失敗時に以前の完成 mp4 を保護する最終出力の atomic replace に限り変更できる。`build_short_from_segments()`、queue fingerprint、upload transaction、工程 6 の output fingerprint、seek 順は変更しない。R1 の入力 seek 契約は既存実装・テストを文書へ反映し、single-pass 化や seek 方式の変更は G1 で比較してから別タスクにする |

---

## 5. よくある申し送り事項（着手前に確認しておくこと）

`impl-sonnet` に渡す前に、オーケストレーターが次を把握しておくと手戻りが減る。

| 項目 | 内容 |
|------|------|
| ジョブの同時実行制限 | [`services/jobs.py`](../src/yt_live_kit/services/jobs.py) の `is_busy()` により、同時実行できるジョブは 1 件のみ（v2 から変更なし）。S4 も全対象確定後の単一ジョブが順次処理し、`run_shorts_queue()` だけが対象単位の成否を `queue_{job_id}.json` へ各 item 後に atomic 保存する。UI は `start_job()` の返却 job ID を video ID 別に保持し、現在動画の manifest だけを表示する |
| S4 の Streamlit 状態境界 | 候補ソースは form 外で変更時に即 rerun し、候補はソース文書の表示順を維持する。その後に選択・モード・layout・preset を 1 個の `st.form` で snapshot 化する。Codex は対象カードの明示生成 / 再試行 submit 時だけ呼び、editor も form submit の「台本を確定」でのみ確定する。確定後は editor を隠し、「修正する」で確定を解除する |
| S4 の snapshot / manifest | fingerprint は video ID、元候補全内容、表示順、正規化全区間、source / mode / layout / 両 preset の canonical JSON SHA-256。spec は frozen primitive tuple + canonical 台本 JSON とし、全直列化境界で再検証する。`manifest_path` は JSON 外で loader が注入する。新 job ID 保持後は manifest 未作成でも旧 latest を表示せず、現在動画の key が無い場合だけ当該動画の検証済み latest へ fallback する |
| `PipelineResult.highlights_error` / `clips_error` | 既存のこれらのフィールドは softfail の結果を保持する。動画詳細ページ（U2）で表示を移設する際、表示を落とさないよう注意する（v2 の W4 レビューで一度発生した不具合パターン） |
| `ShortResult.font_warning` | `build_short()` / S3 で新設する `build_short_from_segments()` のどちらも、日本語フォントが解決できなかった場合にこのフィールドへ日本語警告を入れる。UI 側で `st.warning` として拾うことを忘れない |
| クリップボード部品の移設（U2） | `build_clipboard_copy_html` / `render_copy_button` は **シグネチャ・実装を変えずに** `ui/components/results.py` から `ui/components/clipboard.py` へ移す。移設と機能追加を同時にやらない |
| U5 の正式 IA とストレージ管理 | 公開ナビゲーションはライブラリ / 取り込み / 設定の 3 画面だけとし、動画詳細は hidden のままにする。旧 `history.py` は削除するが、ストレージ管理は `ui/components/storage_manager.py` へ移し、設定ページから利用できるようにして v1 / v2 の AC-15 を維持する。全動画へ到達可能にし、個別ダイアログには動画識別子・1 件・削除対象バイト数（元動画 + 中間）・残る成果物を表示する。一括は対象動画 ID の不変スナップショットを渡して件数・総容量・残る成果物を表示する。双方で未確定時 purge なし、確定時だけダイアログへ渡した正確な ID、確認後の対象増加なし、`StorageError` の日本語表示、成果物保持をテストする |
| U5 の概要欄反映 | ステッパー CTA と通常ボタンを同じフローに統一する。取得済みの更新前 / 更新後を large dialog の引数に渡し、確定前のダイアログ再描画では再取得しない。確定時は既存 `update_video_description` 内部の fetch を維持し、確認時だけ update、成功後だけ `mark_description_applied` を呼ぶ。`services/youtube_api.py` は変更せず、受け入れでは実 YouTube 書き込みを行わない |
| ライブラリのアーカイブ状態の保存先 | `data/_config/archived_videos.json`（`video_id` の配列）。`ui/views/_local_settings.py` を **U1 で新設**し `load_archived_ids()` / `save_archived_ids()` を実装する。`start.command` で毎回起動し直す運用のため、`st.session_state` のみの一時状態にはしない（永続化必須） |
| チャンネル既定ハンドルの保存先 | `data/_config/channel_handle.txt`（1 行テキスト）。**U1 で新設済みの `ui/views/_local_settings.py` に U3 で追記**し、U4 が再利用する。`services/description.py` の `get_template_path()` と同じ発想だが、**フェーズ U の制約により `services/` には置かない** |
| テロップ台本・出力ファイルの命名規則 | `services.telop.make_clip_id()` は `HighlightSegment` と `(start_sec, end_sec)` tuple の両方を受ける。`HighlightSegment.start/end` は `parse_timestamp_to_seconds()` で数値化してから、各秒値を `Decimal(str(value))` + `ROUND_HALF_UP` で整数ミリ秒化し、入力順の `start_ms-end_ms` を `|` 連結した UTF-8 文字列の SHA-256 先頭 12 桁を返す。空配列・非有限値・負値・逆転区間は日本語エラーにする。S1 の `telop_{clip_id}.json` と S3 の `short_{clip_id}.mp4` は必ず同じ関数を使う |

---

## 6. タスク別クイックリファレンス（着手前チェック）

各タスクに着手する直前に、オーケストレーターが 1 行で確認しておくべき最大のリスクをまとめる。詳細は `docs/execution-plan-v3.md` の該当タスク節を参照する。

| タスク | 着手前に必ず確認すること |
|--------|---------------------------|
| U0 | `ui/pages/` を移動する前に `uv run pytest` のベースライン件数を記録し、移行後に同数（またはそれ以上）通ることを確認する |
| U1 | `services/history.py` の `ProcessedVideo` にフィールドを追加しようとしていないか（services 不可侵）。アーカイブ状態を `st.session_state` のみで済ませていないか（`archived_videos.json` への永続化が必須） |
| U2 | `render_highlights_section` / `render_shorts_section` の既存表示・生成ロジックを維持しているか。既存成果物を上書きする場合の確認 `st.dialog` 追加だけは許可する |
| U3 | `services/channel.py` の `list_archives()` を自動で呼んでいないか（レート制限対策・NFR-05 は v3 でも維持）。`services/batch.py` の変更が `do_chapters` / `do_clips` の引数追加・既定 `True` / `True`・`run_batch_job_target()` → `run_batch()` → `pipeline.run()` の全呼び出し伝播・両方 `False` の入力検証だけか。UI 3 ルートからの `start_job()` kwargs もテストされているか。`_local_settings.py` は U1 で新設済みのため、重複して新規作成していないか |
| U4 | `config.py` を変更しようとしていないか（設定ページは表示専用 + チャンネル既定ハンドルのみ編集可） |
| U5 | 正式 IA が公開 3 画面 + 非表示詳細になり旧 history が削除されているか。ストレージ管理を設定へ移し、全動画への到達性、個別 / 一括ダイアログの対象情報、未確定 / 確定境界、StorageError、成果物保持まで AC-15 を維持しているか。概要欄は外側 primary → 検証 → fetch / merge → large dialog の更新前後別表示 → 確認時だけ update → 成功後だけ mark の一本化された流れか。確定時の既存 update 内部 fetch を残し、実 YouTube 書き込みを受け入れ試験で実行していないか |
| U6 | 3 ワークスペース + 左パネル + 6 工程の責務、人確認の既定未チェック、review / output fingerprint と正しい失効対象、active line 復元、atomic / fail closed ライン状態、`SchedulePolicy.timezone` 日次集計が FR-17 / FR-33 と一致するか。既存 queue fingerprint、生成・投稿 service、各確認ダイアログを壊していないか |
| S1 | テロップ台本とメタデータを 1 回の Codex 呼び出しで生成しているか。入力字幕が開始・終了・ミリ秒を保持し、区間・行の `start_sec` / `end_sec` が元動画基準の絶対秒で、`TelopSegmentScript` 自身にも区間境界があり、S3 の `累積尺 + 行の絶対秒 - 元区間開始秒` へ一意に変換できるか。S1 は失敗時に例外を送出し、呼び出し側が局所捕捉して既存 telop と他成果物を維持する softfail になっているか |
| S2 | `TimedCue.emphasis=False` の追加が既存 3 引数構築を壊していないか。プリセット省略 + フックなしで `build_segment_subtitle()` と既存 ASS 出力が v2 完全互換か。通常字幕 / Hook の `\`・波括弧・C0 制御文字・実改行を指定順で安全化してから、選択 preset の強調色と本文復帰色を導出して行全体へ管理タグを付けているか。不明 preset / 不正色 / 空 hook を日本語エラーにし、通常字幕と Hook を同一 ASS に出せるか |
| S3 | 公開 `NormalizedSegmentBounds` / 正規化 helper の整数 ms を ID・尺（10,000 / 180,000 ms）・encode・字幕 offset / clip の唯一の基準にし、0.5 ms 境界でも一貫するか。入力順を再生・ID・encode 順として保持し、全区間・layout・output 名・明示 hook・preset を source 取得前に検証するか。telop は ffmpeg 前と `build_concatenated_subtitle()` 直呼び境界の両方で tuple 入力に対して再検証し、直呼びでも明示 hook の空文字・半角山カッコを拒否するか。VTT の区間相対 cue に累積 ms を加え、1 回だけ読む fallback、Hook 単独、preset 全伝播、`force_style` なしの 1 回焼き込み、`len(segments) + 3` の進捗契約を満たすか。安全な output 名、`{正式出力stem}.ffmpeg.log`、atomic replace、失敗時の既存 mp4 保護、専用中間ディレクトリ単位の cleanup / keep、エラー型変換、font warningを確認する。legacy `build_short()` は原則不変だが、R1 に限り失敗時の既存出力を守る atomic replace だけを許可し、それ以外の挙動を変えていないか確認する |
| S4 | form 外の source 切替と表示順、明示 Codex submit、不変 fingerprint と全台本確定が一致する時だけ開始するか。pure service、deep immutable spec、正規化台本、決定的出力名、softfail、単独 writer manifest、非永続 `manifest_path`、video ID 別 job ID と A → B → A 表示分離、latest tie-break、上書き dialog、busy / 二重開始防止まで確認する |
| S9-0 | incoming VTT の隔離、既存 `ja.vtt` bytes 保持、source VTT immutable 保存、失敗時非変更が先に固定されているか |
| S9-1 | 代表素材の production 非変更 A/B が先にあり、gold / glossary、VTT baseline、候補 model、精度・固有名詞・cue・wall time・memory・cache、再現 command、Go / No-Go が固定されているか |
| S9-2 | `TranscriptArtifact` の strict schema / canonical digest / cache identity 分離 / resolver 用途 / candidate lineage / persistent cache / atomic index が先に決まり、`ja.vtt` を変更せず、使用範囲内外の失効を分離できているか |
| S9-3 | whisper-cli 1.9.1 の capability / model fingerprint / JSON schema を検査し、音声のみ・複数区間・1 ジョブ・timeout / partial failure を扱い、モデル自動取得と全編処理がないか |
| S9-4 | VTT で親候補を選び、選択区間だけを Whisper。cutplan / telop / queue / line / review が同じ immutable artifact と ordered used-range cue digest を使い、padding / preview / 人確認・FR-25 の境界正規化を維持しているか |
| S9-5 | UI が明示 CTA、job ID、進捗、cache、runtime / model、coarse fallback、失効理由を日本語で表示し、service の resolver / fingerprint ロジックを複製せず、既存確認境界を維持しているか |
| T1-PLAN | 4 docs のみで T1 契約・依存・安全境界を確定し、ADR、コード、tests、benchmarks、data、learning log を編集していないか。T1-1 を次の未着手にし、S9-6 を完了扱いにしていないか |
| T1-1 | 測定前固定 manifest、3 fixture 群各 20 行以上、合計 60 行以上、人音声 onset gold、coverage 分母、pooled / 群別 gate、bounded whisper-cli の再現証跡、production 非変更、低信頼元時刻維持を確認したか |
| T1-2 | T1-1 Go 後だけ着手し、`docs/adr/0001-telop-timing-persistence.md` の Artifact v2 / sidecar ADR、strict provenance、atomic 保存、legacy fallback、T1-1 isolated benchmark 例外を持ち越さない追加 Whisper / manifest 外解析 / backfill 無しを確認したか |
| T1-3 | pure monotonic aligner が一意高信頼行だけを補正し、低信頼 flag、clamp / 500 ms、lineage / fingerprint、既存 burn / queue / cut 契約を維持しているか |
| T1-4 | 3 status、現行 start / end editor、独立 timing confirmation、全文確認・final preview、通常 rerun 無副作用、session-state 境界を確認したか |
| T1-5 | component acceptance の A/B、gold、失効、restart、failure / fallback、legacy、scope guard、全 pytest / diff / compile、隔離 preview、production hash unchanged を揃え、S9-6 formal acceptance と AC-40 を開いたままにしているか |
| S9-6 | A/B と全回帰、cache restart、failure injection、実機 1 本の証跡を揃え、S9-1 の No-Go や未確認 AC を完了扱いにしていないか |
| P6-1 | 同一 Codex 呼び出しで固定順 3 方向を生成し、新規生成は 3 件必須、legacy 1〜2 件は読み込み互換、18〜32 文字は警告だけか。`telop.py` 以外の P6 / S9 範囲を触っていないか |
| P6-2 | テンプレートが無い時だけ固定 CTA 文を含む必須構成の既定を atomic 作成し、既存 bytes を上書きせず、期待 4 項目と template / meta fingerprint を不変 object にして合成後と最終編集後を同じ純粋 validator で検証できるか。P4 fallback と長尺概要欄を壊していないか |
| P6-3 | related video が `not_ready` → upload 成功後 `pending` →対象 ID の明示確認後 `confirmed` だけで遷移し、既存 `source_video_id` / `video_id` を唯一の正本として重複 ID を追加せず、service が lock 付き queue から pending 件数・対象一覧を返すか。pending が `publishAt` / publication poll を止めず、legacy queue、lock / atomic、poll / slot / attempt が非回帰か。API / browser 呼び出しが無いか |
| P6-4 | 期待 4 項目と template / meta fingerprint の不変 snapshot を preview / content snapshot / fingerprint へ凍結し、最終説明文を preview 前と confirm 後に mutable file の再読込なしで二重再検証するか。欠落時に operation / job / attempt / API が始まらないか。Studio 手動確認は service の pending 集計を表示し、対象 ID を示す dialog 後に local queue だけを更新するか。P6-1 の3ファイルを再変更していないか |
| P6-5 | AC-38 / AC-39、半角山カッコ、日本語エラー、確認 race、後方互換、全件テスト、scope guard、P6-1 の S9-4 先行統合を証跡で確認したか |
| P0 | P1 / P2 の安全経路だけを使い、実 upload と審査フォームが別承認か。private lock / processing を poll し、lock を成功扱いにしていないか |
| P1 | private / aware future publishAt / notify false、metadata、audience / synthetic / consent、resumable、LA attempt、operation、job_id、kind dispatch を API mock で固定したか |
| P2 | IANA policy、slot と attempt の分離、全項目 preview、既定未同意、確定後再検証、同時 confirm、operation / job ID の再起動復元を検証したか |
| P3 | README・版数更新が変更範囲内か。P0 / 審査とは別の専用承認後だけ実予約公開し、upload 後・公開前後の status / processingDetails を記録したか |

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-04 | **T1 親レビュー指摘を反映。** T1-1 の隔離 bounded whisper-cli benchmark、pooled / 群別 gate、T1-5 の隔離 preview と production 不変、`docs/adr/0001-telop-timing-persistence.md`、component acceptance と S9-6 formal phase acceptance の分離、AC-40 の最終完了時点を追加した。 |
| 2026-08-04 | **T1 実行・レビュー指示を追加。** T1-PLAN docs-only、production 非変更 spike、timing payload 保存、pure monotonic alignment、独立 timing confirmation、同期受け入れ、S9-6 の最終判定境界を通常テンプレート補助、T1 専用テンプレート、レビュー観点、クイックリファレンスへ反映した。 |
| 2026-08-03 | **P6 分離セッション規約を追加。** GPT-5.6 sol のオーケストレーターと GPT-5.6 luna / max の P6 実装セッション、P6-1〜P6-3 の非重複 writer、P6-4 の共有 UI 単一 writer、docs / S9-1 監査節 / 学習ログの保護、P6-1 の S9-4 先行統合、API mock 限定、P6 欠陥優先レビュー項目を固定 |
| 2026-08-03 | **S9 実行セッションを追加。** S9-0〜S9-6 の依存順、既存 VTT 非上書き、`TranscriptArtifact` / resolver / cue digest / cache、候補 lineage、whisper-cli 1.9.1 capability、音声のみ・1 ジョブ・使用範囲だけの fail closed、gold / glossary / A-B gate、UI / A/B / Go-No-Go の指示テンプレートとレビュー観点を追加 |
| 2026-08-01 | U6 v3.2 確定仕様を反映。フェーズ U の限定 service 例外、左パネルと 6 工程、品質判定 4 分離、review fingerprint、fail closed ライン状態、timezone 日次集計、差分表示のレビュー観点とクイックリファレンスを追加 |
| 2026-08-01 | P 安全監査の独立レビューを反映。P1 → P2 → P0 → P3 の必須順序、P0 公開可能性の承認、単一 queue record、job crash recovery / fault injection、具体的 poll / lock 判定をレビュー観点へ追加 |
| 2026-08-01 | P0〜P3 着手前安全監査を反映。P1 / P2 先行、scheduled-only の private + future publishAt、Made for Kids / synthetic media 必須選択、Community Guidelines 既定未同意、metadata、resumable / reconciliation、LA attempt、永続 operation、confirm race、jobs / status bar、polling、実操作別承認をレビュー観点へ追加 |
| 2026-08-01 | S4 計画レビューを反映。form 外 source、表示順、明示 Codex submit、deep immutable spec、完全 fingerprint、manifest 単独 writer、job ID / latest 表示境界、決定的出力名をレビュー観点へ追加 |
| 2026-08-01 | S4 着手前監査を反映。Streamlit form / 確定状態、修正台本の再検証・atomic 保存、単一ジョブ softfail、job ID 付き manifest、上書き dialog、遅延 download とテスト境界をレビュー観点へ追加 |
| 2026-08-01 | S3 着手前監査を反映。共通整数 ms 正規化、入力順・全境界検証、二重 telop 再検証と累積字幕、VTT / Hook fallback、全入力 preflight、固定ログ名、進捗、atomic replace、cleanup、既存出力保護をレビュー観点へ追加 |
| 2026-08-01 | S2 着手前監査・計画レビューを反映。`TimedCue.emphasis` の後方互換、既定 ASS 完全互換、入力安全化、preset 色導出、同一 ASS への Hook 統合、S3 への preset 伝播と `force_style` 分離をクイックリファレンスへ追加 |
| 2026-08-01 | S1 着手前監査を反映。テロップ時刻の絶対秒基準、連結後変換式、`make_clip_id()` の入力型・丸め・連結規則を固定 |
| 2026-08-01 | U5 着手前監査を反映。正式 IA、ストレージ管理移設、概要欄更新経路の一本化、update / mark 順序、安全な受け入れ境界をレビュー観点へ追加 |
| 2026-08-01 | 実装前監査を反映。Video Uploads 専用クォータを 100 回/日に更新し、`make_clip_id()` 規則を固定 |
| 2026-08-01 | v3 初版。impl-sonnet 向け指示テンプレート、レビュー観点チェックリスト、フェーズ別注意点を定義 |
