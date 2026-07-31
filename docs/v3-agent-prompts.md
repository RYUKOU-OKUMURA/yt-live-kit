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

**進捗更新:** `impl-sonnet` ワーカーは `docs/execution-plan-v3.md` のチェックボックスを更新してよい（v2 のウェーブ運用と異なり、並列競合が起きないため）。ただし更新前後で `git diff` を確認し、意図しない箇所を変更していないかレビューする。

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
- 従量課金 API を呼ばない。AI 連携は Codex CLI サブプロセスのみ（NFR-11）
- ユーザー向けエラーメッセージは必ず日本語にする。スタックトレースをそのまま出さない
- 半角の山カッコ `<` `>` を成果物テキストに出さない。必要なら全角 〈〉 を使う
- 新規依存パッケージを追加しない（yt-dlp / ffmpeg / Streamlit / pydantic / typer /
  google-api-python-client で完結させる）
- 既存のテストを壊さない
- 【フェーズ U のタスクのみ】services/ を原則変更しない。新しい永続化が必要な場合は、
  execution-plan-v3.md の該当タスクの「設計メモ」に従い ui/ 層内で完結させる。例外は U3 の
  services/batch.py における do_chapters / do_clips の引数追加・全呼び出しへの伝播・両方 False の入力検証だけとする
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

## Done 条件
（execution-plan-v3.md の当該タスクの「Done 条件」をそのまま転記する）

## テスト実行コマンド
uv run pytest

## 完了後に必ず行うこと
1. docs/execution-plan-v3.md の当該タスクのチェックボックス（作業・Done 条件）を
   完了した事実だけ `- [x]` にする。未確認の項目にはチェックを入れない
2. タスクが「大きな実装タスク」（execution-plan-v3.md §3.4 のリスト）に該当する場合、
   コミットが必要である旨を報告に明記する（コミット自体はオーケストレーターが行う）

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

- [ ] `git diff --stat` に `src/yt_live_kit/services/` 配下のファイルが含まれていないこと（U0〜U4 は原則違反。ただし U3 の `services/batch.py` は `do_chapters` / `do_clips` の引数追加・`run_batch_job_target()` → `run_batch()` → `pipeline.run()` の全呼び出し伝播・両方 `False` の入力検証に限り許可。U5 も `services/youtube_api.py` は変更しない）
- [ ] 新しい永続化（チャンネル既定ハンドル等）が `services/` を新設せず、`ui/views/_local_settings.py` のような UI 層のヘルパーで完結しているか
- [ ] `ui/pages/` に相当する旧ディレクトリ・旧 import パスが残っていないか（U0 完了後は全タスクで確認）
- [ ] U5 完了時の IA が公開 3 画面（ライブラリ / 取り込み / 設定）+ `visibility="hidden"` の動画詳細 1 画面であり、`history.py` / `url_path="history"` が残っていないか
- [ ] 旧「処理済み一覧」を削除しても、v1 / v2 のストレージ管理が `ui/components/storage_manager.py` 経由で設定ページから利用できるか。10 件を超えても 11 件目以降を含む全動画へ到達でき、各動画の元動画容量と個別削除導線、個別 / 一括削除の確認ダイアログ、成果物保持が回帰していないか
- [ ] U5 の概要欄反映が、外側 primary ボタン → OAuth / チャプター検証 → fetch / merge → `st.dialog(width="large")` の更新前 / 更新後表示 → 確認時だけ update → 成功後だけ mark、の順になっているか。確定前のダイアログ再描画だけは取得済みプレビューを再利用し、確定時は既存 update 内部の fetch を維持しているか
- [ ] update 失敗時に mark されず、YouTube 更新成功後のローカル mark 失敗は「YouTube 側は更新済み」と分かる日本語警告になるか
- [ ] ステッパー・確認ダイアログ・共通コピー部品が、複数ページで同じ実装を再発明していないか（`ui/components/clipboard.py` を再利用しているか）

### 3.3 フェーズ S 特有の観点

- [ ] Codex CLI 呼び出しが 1 区間セットあたり 1 回で完結しているか（テロップ台本とメタデータを別々に 2 回呼んでいないか、S1）
- [ ] `-ss` を `-i` の後ろに置く精密シークになっているか（`encode_segment` を正しく使っているか、S3）
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
| **U** | `services/` を原則触らない。UI 層の並べ替えとテストの再構成だけで完結させる。新しい永続化が必要なら `ui/views/_local_settings.py` のような UI 層ヘルパーに留める。U3 の `services/batch.py` における引数追加・全呼び出し伝播・両方 `False` の入力検証だけは最小例外（[execution-plan-v3.md](./execution-plan-v3.md) §3.2 参照） |
| **S** | 従量課金 API を使わない。AI 生成は Codex CLI のみ、かつテロップ台本とメタデータは同じ呼び出しで一括生成する。**自動生成をそのまま焼き込まず、必ず人の確認ステップを挟む** |
| **P** | P1 / P2 を全 API mock で完成してから、同じ本番経路だけを P0 / P3 で別承認により実操作する。private + future publishAt + notify false、audience / synthetic / consent、LA attempt、resumable / reconciliation、永続 operation、confirm race、polling を固定する。private lock 中は P3 を成功扱いにしない |

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
| S1 | テロップ台本とメタデータを 1 回の Codex 呼び出しで生成しているか。入力字幕が開始・終了・ミリ秒を保持し、区間・行の `start_sec` / `end_sec` が元動画基準の絶対秒で、`TelopSegmentScript` 自身にも区間境界があり、S3 の `累積尺 + 行の絶対秒 - 元区間開始秒` へ一意に変換できるか。S1 は失敗時に例外を送出し、呼び出し側が局所捕捉して既存 telop と他成果物を維持する softfail になっているか |
| S2 | `TimedCue.emphasis=False` の追加が既存 3 引数構築を壊していないか。プリセット省略 + フックなしで `build_segment_subtitle()` と既存 ASS 出力が v2 完全互換か。通常字幕 / Hook の `\`・波括弧・C0 制御文字・実改行を指定順で安全化してから、選択 preset の強調色と本文復帰色を導出して行全体へ管理タグを付けているか。不明 preset / 不正色 / 空 hook を日本語エラーにし、通常字幕と Hook を同一 ASS に出せるか |
| S3 | 公開 `NormalizedSegmentBounds` / 正規化 helper の整数 ms を ID・尺（10,000 / 180,000 ms）・encode・字幕 offset / clip の唯一の基準にし、0.5 ms 境界でも一貫するか。入力順を再生・ID・encode 順として保持し、全区間・layout・output 名・明示 hook・preset を source 取得前に検証するか。telop は ffmpeg 前と `build_concatenated_subtitle()` 直呼び境界の両方で tuple 入力に対して再検証し、直呼びでも明示 hook の空文字・半角山カッコを拒否するか。VTT の区間相対 cue に累積 ms を加え、1 回だけ読む fallback、Hook 単独、preset 全伝播、`force_style` なしの 1 回焼き込み、`len(segments) + 3` の進捗契約を満たすか。安全な output 名、`{正式出力stem}.ffmpeg.log`、atomic replace、失敗時の既存 mp4 保護、専用中間ディレクトリ単位の cleanup / keep、エラー型変換、font warning、既存 `build_short()` 不変を確認する |
| S4 | form 外の source 切替と表示順、明示 Codex submit、不変 fingerprint と全台本確定が一致する時だけ開始するか。pure service、deep immutable spec、正規化台本、決定的出力名、softfail、単独 writer manifest、非永続 `manifest_path`、video ID 別 job ID と A → B → A 表示分離、latest tie-break、上書き dialog、busy / 二重開始防止まで確認する |
| P0 | P1 / P2 の安全経路だけを使い、実 upload と審査フォームが別承認か。private lock / processing を poll し、lock を成功扱いにしていないか |
| P1 | private / aware future publishAt / notify false、metadata、audience / synthetic / consent、resumable、LA attempt、operation、job_id、kind dispatch を API mock で固定したか |
| P2 | IANA policy、slot と attempt の分離、全項目 preview、既定未同意、確定後再検証、同時 confirm、operation / job ID の再起動復元を検証したか |
| P3 | README・版数更新が変更範囲内か。P0 / 審査とは別の専用承認後だけ実予約公開し、upload 後・公開前後の status / processingDetails を記録したか |

---

## 変更履歴

| 日付 | 内容 |
|------|------|
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
