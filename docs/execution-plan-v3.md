# yt-live-kit 実行計画書 v3

**バージョン:** v3（ショート量産・投稿）
**最終更新:** 2026-08-01
**実装開始予定:** 2026-08-02
**関連:** [要件定義書 v3](./requirements-v3.md) / [v2 実行計画](./execution-plan-v2.md) / [v3 エージェント指示](./v3-agent-prompts.md) / [AGENTS.md](../AGENTS.md)

---

## 進捗サマリー

オーケストレーター（Claude / Fable 5）が、タスク完了時に対応する `- [ ]` を `- [x]` に更新する。フェーズ完了時は下表も更新する。**v3 作業では本ファイルが進捗の正本になる。**

| フェーズ | 名称 | 状態 |
|----------|------|------|
| PLAN0 | 要件・計画の確定 | [x] 完了 |
| U0 | `ui/pages` → `ui/views` リネーム + `st.navigation` 導入 | [x] 完了 |
| U1 | ライブラリページ | [x] 完了 |
| U2 | 動画詳細ページ + ステッパー + 確認ダイアログ + 共通コピー部品 | [x] 完了 |
| U3 | 取り込みページ | [x] 完了 |
| U4 | 設定ページ | [x] 完了 |
| U5 | 正式 4 画面 IA + ストレージ管理移設 + 概要欄差分プレビュー + フェーズ U 受け入れ | [x] 完了 |
| S1 | テロップ台本 + メタデータ生成 | [x] 完了 |
| S2 | ASS テロップスタイルプリセット + フックタイトル | [x] 完了 |
| S3 | ジャンプカット連結ショート生成（services 拡張） | [ ] 未着手 |
| S4 | キュー量産 UI + 台本確認フロー | [ ] 未着手 |
| S5 | フェーズ S 受け入れ（実配信 1 本からショート複数本を通しで作る） | [ ] 未着手 |
| P0 | テストアップロード検証 | [ ] 未着手 |
| P1 | アップロードサービス（`videos.insert` + `publishAt`） | [ ] 未着手 |
| P2 | スケジュールポリシー + キューからの自動割り当て + 投稿確認 UI | [ ] 未着手 |
| P3 | フェーズ P 受け入れ（予約投稿が実際に公開される） | [ ] 未着手 |

**状態の書き方:** `[ ] 未着手` / `[~] 進行中` / `[x] 完了`

**マイルストーン:**

| ID | 内容 | 状態 |
|----|------|------|
| M11 | サイドバーの事故導線が消える（U0 完了） | [x] |
| M12 | 動画軸で迷わず作業できる新 IA が揃う（U5 完了） | [x] |
| M13 | テロップ付きショートが量産できる（S5 完了） | [ ] |
| M14 | 予約投稿が実際に公開される（v3 完了・P3 完了） | [ ] |

---

## 1. 目的

v2（0.2.0）は「日常運用の摩擦を消す」「成果物の幅を広げる」を達成した。v3 では次の 1 点に集中する。

**ショート動画の安定量産。** 47 本の処理済み動画がありながら、ショート化は依然として手作業に依存している。v3 では、複数区間の連結・テロップ焼き込み・メタデータ生成・予約投稿までを、UI から迷わず実行できるようにする。

そのために 3 フェーズで進める。

1. **フェーズ U（UI 骨格リファクタ）** — 機能軸 UI を動画軸 UI に作り替える。ショート機能を積む前に土台を整える
2. **フェーズ S（ショート量産パイプライン）** — ジャンプカット連結・テロップ・メタデータ生成・キュー量産
3. **フェーズ P（投稿・予約投稿）** — YouTube への非公開アップロード + `publishAt` 予約投稿

v1/v2 で作った `services/` 分離、Codex CLI 連携、ジョブ機構（[`services/jobs.py`](../src/yt_live_kit/services/jobs.py)）の構造をそのまま踏襲する。**新しい pip 依存は追加しない。**

---

## 2. 前提・制約（v2 から引き継ぐもの）

| 項目 | 内容 | v3 での変更 |
|------|------|--------------|
| 主要 UI | Streamlit（localhost のみ） | 情報設計（IA）を作り替える。動作環境は変更なし |
| 処理層 | `services/` 共通。CLI は補助 | 変更なし。フェーズ U 中は原則 `services/` を変更しない。U3 の引数追加・全呼び出し伝播・両方 `False` 入力検証に限る最小例外は §3.2 参照 |
| コスト | 従量課金 API 禁止。Codex CLI（主） | 変更なし（NFR-11）。YouTube Data API は無料枠クォータ内（NFR-12） |
| 依存パッケージ | yt-dlp / ffmpeg / Streamlit / pydantic / typer / google-api-python-client | **新規追加なし**。google-api-python-client は v2 で導入済み |
| 自動編集 | v2 でジャンプカット・テロップ・自動投稿はスコープ外 | **v3 で解禁**（[requirements-v3.md §2](./requirements-v3.md#2-v2-からのスコープ改訂) 参照） |
| 対象動画 | 公開アーカイブのみ | 変更なし |
| 同時実行ジョブ | 1 件まで（キューイングなし） | 変更なし。キュー量産（S4）も内部的には単一ジョブが順次処理する |

---

## 3. 実装方針

### 3.1 スコープの改訂点（requirements-v3.md §2 と同一）

[`docs/requirements.md`](./requirements.md) §6.1.1 は v2 時点で自動編集の一部（区間連結・縦横比変換・既存字幕焼き込み）のみを解禁していた。v3 ではこれをさらに広げる。

| 項目 | v2 までの扱い | v3 での扱い | 理由 |
|------|----------------|-------------|------|
| ジャンプカット（複数区間の連結） | スコープ外 | **解禁** | 区間選定は人（または AI 提案 + 人の確認）が行い、ffmpeg は決定論的に連結するだけ |
| テロップ生成 | スコープ外 | **解禁** | Codex CLI が下書きを作り、**必ず人が確認してから**焼き込む。全自動生成配置ではない |
| YouTube への自動投稿 | スコープ外 | **解禁**（非公開アップロード + 予約公開に限定） | `videos.insert` は既存 OAuth スコープでカバー済み。即時公開は行わない |
| BGM・効果音 / ズーム・トランジション | スコープ外 | **スコープ外のまま** | デザイン判断・権利処理を伴い、CapCut 等の外部ツールに委ねる |

### 3.2 レイヤ分離（フェーズごとの追加ルール）

```
UI (Streamlit) / CLI (typer)
        ↓
services (pipeline, jobs, ytdlp, channel, vtt_parser, compressor,
          ai_prompt, clips, highlights, ffmpeg, shorts, subtitle_burn,
          storage, description, youtube_api, chapter_validator, history)
        ↓
data/{video_id}/ ...
```

- UI にビジネスロジックを書かない（v1/v2 と同じ）
- **フェーズ U では原則 `services/` を変更しない。** UI 層の並べ替えとテストの再構成だけで完結させる。これは「まず土台を安全に作り替え、業務ロジックの変更と混ぜて事故を起こさない」ための意図的な制約である
  - **U3 のみの最小例外:** 既存バッチ処理で「チャプターを作る」「切り抜き候補を出す」の選択を実効させるため、`services/batch.py` の `run_batch()` / `run_batch_job_target()` へ `do_chapters` / `do_clips` 引数を追加し、`run_batch_job_target()` → `run_batch()` → 各 URL の `pipeline.run()` の全呼び出しへ伝播し、両方 `False` を service 入力でも拒否する変更だけは許可する。既定値はどちらも `True` とし、既存 CLI / UI との後方互換を維持する。この例外をその他の `services/` 変更に拡大しない
  - この制約により、ページをまたいで永続化したい **UI 固有の軽量な状態**（例: チャンネル既定ハンドル、ライブラリのアーカイブ表示切り替え）は、新しい `services/` モジュールを作らずに UI 層内で完結させる。具体的な方針は U1・U3 のタスク詳細に明記する
  - フェーズ S・P では通常どおり `services/` を拡張してよい
- ワーカースレッドから `st.*` を一切呼ばない（v2 の原則を継続）
- ffmpeg のフィルタグラフ・ASS 生成は `services/` 内で組み立て、UI からは渡さない

### 3.3 AI 連携（v2 を維持 + テロップ台本を追加）

1. プロンプトテンプレートは `prompts/` に分離する（[`prompts/chapters.md`](../prompts/chapters.md) / [`prompts/clips_suggest.md`](../prompts/clips_suggest.md) / [`prompts/highlights.md`](../prompts/highlights.md) と同じ場所）
2. 主方式は Codex CLI サブプロセス呼び出し（[`services/ai_prompt.py`](../src/yt_live_kit/services/ai_prompt.py) のパターンを踏襲）
3. Codex CLI 未導入時は、[`services/highlights.py`](../src/yt_live_kit/services/highlights.py) の `CODEX_INSTALL_HINT` と同じ形式のフォールバック案内を出す
4. **保存前に必ずバリデーションを通す。** テロップ台本も、チャプター・切り抜き候補・ハイライト区間と同様のバリデータを持つ（S1）
5. **テロップ台本とフックタイトル・メタデータ（タイトル案・説明文・タグ）は、同じ Codex 呼び出しの中で一括生成する。** 独立した機能として 2 回 Codex を呼ばない（コスト・待ち時間の両方に効く）

### 3.4 Git コミット方針（AGENTS.md §3 と同一）

| タイミング | ルール |
|------------|--------|
| **フェーズ完了時** | **必ずコミットする。** メッセージにタスク ID（例: `U0`, `S3`）と完了内容を含める |
| **大きな実装タスク完了時** | **そのタスク単位でもコミットする** |
| **小さな修正のみ** | 同一タスク内でまとめてよいが、タスク境界では必ず切る |
| **進捗チェック更新** | 対応する実装コミットに含める（または直後の docs コミットで反映） |

大きな実装タスク（タスク単位コミット対象）:

- U0 `ui/pages` → `ui/views` リネーム + `st.navigation` 導入
- U2 `ui/views/video_detail.py` 新設
- S1 `services/telop.py` 新規作成
- S3 `services/shorts.py` の複数区間連結拡張
- P1 `services/youtube_api.py` のアップロード機能追加

**進捗更新の共通例外:** 各タスクの「変更ファイル範囲」に個別記載がなくても、完了した事実を記録するための `docs/execution-plan-v3.md` のチェック更新は全タスクで許可する。実装内容や要件を変える編集はこの例外に含めず、先に計画変更として独立レビュー・コミットする。

---

## 4. v2 未完了タスクの扱い

2026-08-01 時点で `uv run pytest` は 375 件全通過しており、[`docs/execution-plan-v2.md`](./execution-plan-v2.md) 記載の機能（V1〜V6）は実装済みである。一方で同ファイルのチェックボックスは以下が未消化のまま残っている。実コードの状態を確認したうえで仕分けした結果を記す。

| v2 タスク / Done 条件 | 内容 | 扱い | 理由 |
|------------------------|------|------|------|
| V1-8 / V3-7 / V4-6 / V5-8 / V6-8 | 進捗チェック更新 + フェーズ完了コミット | **クローズ**（対応不要） | 対応コードは実装済みで 375 件のテストが通過している。v3 では本ファイル（execution-plan-v3.md）が進捗の正本になるため、v2 側のチェック更新を追走する必要はない |
| V3-6 実機確認（チャンネル一覧取得〜一括投入） | 実データでの動作確認 | **v3 に吸収**（U5 フェーズ受け入れ） | U3 で取り込みページ自体を作り替えるため、旧 UI（`ui/pages/channel.py`）での実機確認は二度手間になる。新 UI 完成後の U5 でまとめて確認する |
| V5-7 実機確認（ハイライト繋ぎ目のフリーズ・音ズレ） | 実動画での目視確認 | **v3 に吸収**（S5 フェーズ受け入れ） | S3 でジャンプカット連結が `encode_segment` / `concat_segments` の使い方を拡張するため、確認は S5 でまとめて行う方が効率的 |
| V6-7 実機確認（ショート 4 パターンの目視） | 実動画での目視確認 | **v3 に吸収**（S5 フェーズ受け入れ） | S2 でテロップスタイルが複数プリセットに増えるため、既存 1 スタイルの確認は S5 の複数プリセット確認に統合される |
| V7-1 AC-01〜17 全確認 | 受け入れ基準の総点検 | **一部クローズ・一部吸収** | AC-01〜10（v1）は 375 件のテスト通過により回帰なしと確認できるため実質クローズ。AC-11〜17（v2 新規）は上記の実機確認未了分を含むため、対応する v3 フェーズ受け入れ（U5 / S5）に吸収する |
| V7-2 実機 2 本通し | 公開アーカイブ 2 本で V1〜V6 を通し実行 | **v3 に吸収**（U5 / S5 フェーズ受け入れ） | 新 UI 上で通しで確認する方が実態に即する。旧 UI での確認は行わない |
| V7-5 リスク表の主要項目を潰す | v2 のリスク表消化 | **クローズ**（実質消化済み） | V7-4（README 更新、済み）で大半の運用手順は文書化されている。残るリスク（フォント・容量・繋ぎ目）はいずれも実装済みの対策（フォールバック・既定削除・再エンコード必須化）でカバーされている |
| V7-7 進捗チェック更新 + v2 完了コミット | 最終更新 | **クローズ**（対応不要。必要ならオーケストレーターが別途実施） | v3 が進捗の正本になるため、v2 の完全クローズは v3 実装の前提条件ではない |

**要約:** v2 の実装そのものはブロッカーになっていない。未消化タスクはすべて「実機・目視での最終確認」であり、UI がフェーズ U でそのまま作り替えられるため、**確認は新 UI 完成後にまとめて行う**方針とする。

---

## 5. フェーズ一覧

| タスク | 名称 | 主な成果 | 対応 FR / AC |
|--------|------|----------|---------------|
| **PLAN0** | 要件・計画の確定 | requirements-v3 / execution-plan-v3 / v3-agent-prompts / AGENTS.md 改訂 | — |
| **U0** | UI 骨格の物理的な作り替え | `ui/views/`、`st.navigation` | AC-18 の前提 |
| **U1** | ライブラリページ | 一覧・状態バッジ・検索・アーカイブ | FR-16, AC-18 |
| **U2** | 動画詳細ページ | ステッパー、確認ダイアログ、共通コピー部品 | FR-17/18, AC-19 |
| **U3** | 取り込みページ | 新着チェック優先 UI、URL 例外ルート | FR-19, AC-20 |
| **U4** | 設定ページ | チャンネル既定値、ffmpeg/フォント確認、Codex 状態 | FR-20, AC-21 |
| **U5** | 正式 4 画面 IA、ストレージ管理移設、概要欄差分プレビュー + フェーズ受け入れ | 旧処理済み一覧撤去、設定へのストレージ移設、差分表示、確認導線統一 | FR-20/21, AC-18〜22 全体受け入れ |
| **S1** | テロップ台本 + メタデータ生成 | Codex 呼び出し、台本 JSON、確認 UI 前提 | FR-22/23, AC-23 |
| **S2** | ASS テロップスタイルプリセット | 複数プリセット、フックタイトル ASS | FR-24, AC-24 |
| **S3** | ジャンプカット連結ショート生成 | 複数区間連結 + レイアウト + テロップ焼き込み | FR-25, AC-25 |
| **S4** | キュー量産 UI + 台本確認フロー | 複数本まとめ生成、結果グリッド | FR-26, AC-26 |
| **S5** | フェーズ S 受け入れ | 実配信 1 本での通し確認 | AC-23〜26 全体受け入れ |
| **P0** | テストアップロード検証 | 非公開ロックの有無確認、審査要否判断 | AC-27 の前提 |
| **P1** | アップロードサービス | `videos.insert` + `publishAt` | FR-27, AC-27 |
| **P2** | スケジュールポリシー + 自動割り当て | 予約枠の自動割り当て、投稿確認 UI | FR-28, AC-27 |
| **P3** | フェーズ P 受け入れ | 実際の予約公開確認、README・版数更新 | AC-27, AC-28 |

各タスクは「実装 → 単体確認 → Done 条件チェック → **タスク完了コミット**」で閉じる。フェーズ末（U5 / S5 / P3）は「フェーズ受け入れ」も兼ねる。

---

## 6. フェーズ詳細

### PLAN0: 要件・計画の確定

**目的:** 実装より先に、v3 の要件と計画を確定する（AGENTS.md §1 のルール）。
**フェーズ状態:** [x] 完了

**作業:**

- [x] PLAN0-1. `docs/requirements-v3.md` を新規作成する（FR-16〜FR-28、NFR-11〜NFR-15、AC-18〜AC-28）
- [x] PLAN0-2. `docs/execution-plan-v3.md`（本ファイル）を新規作成する
- [x] PLAN0-3. `docs/v3-agent-prompts.md` を新規作成する
- [x] PLAN0-4. `AGENTS.md` を v3 体制に合わせて改訂する
- [x] PLAN0-5. v2 未完了タスクの仕分け（§4）を完了する
- [x] PLAN0-6. 実装前監査で判明した計画矛盾を補正する（現行 YouTube granular quota、進捗更新権限、概要欄完了判定、`clip_id` 規則、S4 確認境界、P1 ffprobe 範囲）
- [x] PLAN0-7. U5 着手前監査で判明した IA / 変更範囲の漏れを補正する（公開 3 画面 + 非表示詳細、旧処理済み一覧の撤去、ストレージ管理の設定画面移設、概要欄更新経路の一本化）

**Done 条件:**

- [x] requirements-v3.md と本計画の間に矛盾がない
- [x] v2 との差分（スコープ改訂）が明記されている
- [x] ファイルパス・関数名がすべてコードで実在確認済みである

**見積もり目安:** 済み（本タスクで完了）

---

### U0: `ui/pages` → `ui/views` リネーム + `st.navigation` 導入

**目的:** サイドバー事故（[requirements-v3.md §3.1](./requirements-v3.md#31-サイドバーの事故)）を解消し、以降のページ追加を安全に行えるようにする。**挙動を変えない移行**として実施する。
**変更ファイル範囲:**
- `src/yt_live_kit/ui/app.py`
- `src/yt_live_kit/ui/pages/*.py` → `src/yt_live_kit/ui/views/*.py`（`channel.py` `highlights.py` `history.py` `run.py` `shorts.py` `__init__.py` を `git mv`）
- `src/yt_live_kit/ui/components/results.py`（`ui.pages.highlights` / `ui.pages.shorts` の import パスのみ更新）
- `tests/test_ui_app.py` / `tests/test_ui_channel_page.py` / `tests/test_ui_highlights.py` / `tests/test_ui_history_page.py` / `tests/test_ui_run_page.py` / `tests/test_ui_shorts.py`（import パスのみ更新）
- （`services/` は一切変更しない）

**背景:** Streamlit はエントリスクリプト（`ui/app.py`）と同じディレクトリの `pages/` を自動検出し、中のモジュールをサイドバーに並べる。`ui/pages/` を `ui/views/` にリネームするだけで自動検出を止められる。その後、`app.py` で `st.navigation([...])` + `st.Page(...)` により明示的にページを登録する。

**作業:**

- [x] U0-1. `ui/pages/` の 5 ファイルを `ui/views/` に移動する（内容は変更しない）
- [x] U0-2. `ui/app.py` を `st.navigation` ベースに書き換える
  - 現時点では中身を変えず、既存の `render_run_page` / `render_channel_page` / `render_history_page` を、暫定的に `st.Page` でラップして登録する（U1〜U5 で正式な「公開 3 画面 + 非表示詳細」の 4 画面へ置き換わるまでの繋ぎ）
  - `st.set_page_config` は `st.navigation` より前に呼ぶ（Streamlit の制約）
- [x] U0-3. `ui/components/results.py` の `from yt_live_kit.ui.pages.highlights import ...` / `...pages.shorts import ...` を `ui.views.*` に更新する
- [x] U0-4. 既存テストの import パスを更新する（テストの検証内容自体は変更しない）
- [x] U0-5. 手動確認: `uv run streamlit run src/yt_live_kit/ui/app.py` を起動し、サイドバーに `app` `channel` `highlights` `history` `run` `shorts` のような内部モジュール名が **表示されない**ことを確認する

**Done 条件:**

- [x] `src/yt_live_kit/ui/pages/` ディレクトリが存在しない
- [x] `uv run pytest` が全件通る（挙動不変であることの裏付け）
- [x] サイドバーに内部モジュール名が出ないことを手動確認済み（AC-18 の前提）
- [x] タスク完了コミット済み

**見積もり目安:** 0.5 日

---

### U1: ライブラリページ

**目的:** 47 件の処理済み動画を、状態が一目で分かる形で一覧化し、検索とアーカイブで見通しを保つ。
**変更ファイル範囲:**
- `src/yt_live_kit/ui/views/library.py`（新規）
- `src/yt_live_kit/ui/views/_local_settings.py`（新規。当初 U3 で新設する予定だったが、アーカイブ永続化のため U1 で先に新設する。U3・U4 はこのファイルに追記する）
- `src/yt_live_kit/ui/app.py`（ナビゲーション登録）
- `src/yt_live_kit/ui/state.py`（選択中動画 ID・アーカイブ表示切り替えのセッションキー追加）
- `tests/test_ui_app.py`（U0 のナビゲーション期待値を U1 のライブラリ・hidden 詳細追加に合わせて更新）
- `tests/test_ui_library_page.py`（新規）
- （`services/` は変更しない）

**設計メモ（重要）:** フェーズ U は `services/` を変更しない制約があるため、以下は新しい `services/` モジュールを作らず UI 層内で完結させる。

- **状態バッジ「ショート n 本」の集計:** [`services/history.py`](../src/yt_live_kit/services/history.py) の `ProcessedVideo` にはショート件数フィールドが無い。`library.py` 内に `count_shorts(video_id, settings) -> int` という純粋関数を定義し、`settings.data_dir / video_id / "shorts" / "output"` を `glob("*.mp4")` で数える（[`ui/components/results.py`](../src/yt_live_kit/ui/components/results.py) の `source_cache_note()` が `services.storage.dir_size` を直接呼んでいるのと同じ「UI 層の集計用ヘルパー」パターンを踏襲する）
- **アーカイブ（活用済みを畳む）:** `data/_config/archived_videos.json`（`video_id` の配列）に永続化する。`ui/views/_local_settings.py` に `load_archived_ids(settings) -> set[str]` / `save_archived_ids(ids, settings) -> Path` を実装する。**この起動を跨ぐ永続化は `services/` の新設ではなく、U3 で導入する `channel_handle.txt`（チャンネル既定ハンドル）とまったく同じ発想・同じ置き場所（`ui/views/_local_settings.py` が `data/_config/` の軽量ファイルを読み書きする UI 層ヘルパー）である**。理由: `start.command` でアプリを毎回起動し直す運用のため、`st.session_state` のみのアーカイブは起動のたびにリセットされ、「活用済みが残って整理できない」という v3 の動機（[requirements-v3.md §1.3](./requirements-v3.md#13-v3-の動機)）を解決できない

**作業:**

- [x] U1-1. `list_processed_videos()`（[`services/history.py`](../src/yt_live_kit/services/history.py)）の結果を、タイトル・状態バッジ付きで一覧表示する
- [x] U1-2. バッジ判定: チャプター（`ProcessedVideo.has_chapters`）、候補（`has_clips`）、ショート（`count_shorts()`）
- [x] U1-3. タイトルの部分一致検索ボックスを実装する
- [x] U1-4. `_local_settings.py` に `load_archived_ids(settings) -> set[str]` / `save_archived_ids(ids, settings) -> Path` を実装する（`data/_config/archived_videos.json` に JSON 配列で保存）
- [x] U1-5. 「アーカイブする / 表示に戻す」ボタンと、「アーカイブ済みを表示」トグル（既定 OFF）を実装する。トグル自体の状態は `st.session_state` でよいが、**どの動画がアーカイブ済みかは `load_archived_ids` / `save_archived_ids` 経由で永続化する**
- [x] U1-6. 行クリック（ボタンまたは `st.dataframe` の選択）で `ui/state.py` に選択中 `video_id` をセットし、動画詳細ページ（U2）へ遷移する
- [x] U1-7. ユニットテスト
  - `count_shorts` のカウント（0 件 / 複数件 / ディレクトリ無し）
  - 検索フィルタの部分一致
  - `load_archived_ids` / `save_archived_ids` の保存・読み込み往復（ファイル無し時は空集合を返すこと、`tmp_path` を使うこと）★必須
  - アーカイブ表示切り替えの純粋関数

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] 47 件が状態バッジ付きで表示される（実データで確認）
- [x] アプリを再起動（プロセス再実行）してもアーカイブ状態が保持されることを確認する
- [x] `services/` に変更が無い（`git diff --stat` で確認）
- [x] タスク完了コミット済み

**見積もり目安:** 1 日

---

### U2: 動画詳細ページ + ステッパー + 確認ダイアログ + 共通コピー部品

**目的:** 1 本の動画に関するすべての操作を、パイプライン順に迷わず進められる 1 画面にまとめる。v3 で最も画面が重くなるページであり、後続のショート機能（フェーズ S）もこのページに積まれる。
**変更ファイル範囲:**
- `src/yt_live_kit/ui/views/video_detail.py`（新規）
- `src/yt_live_kit/ui/components/clipboard.py`（新規。`results.py` の `build_clipboard_copy_html` / `render_copy_button` を移設）
- `src/yt_live_kit/ui/components/results.py`（クリップボード関数の移設に伴う import 整理。ハイライト・ショートのセクション呼び出しは `video_detail.py` に移す）
- `src/yt_live_kit/ui/views/highlights.py` / `shorts.py`（`render_highlights_section` / `render_shorts_section` の呼び出し元を `video_detail.py` に変更。既存の表示・生成ロジックは維持しつつ、既存成果物を上書きする場合に限り確認 `st.dialog` を追加してよい）
- `src/yt_live_kit/ui/app.py`（ナビゲーション登録）
- `src/yt_live_kit/ui/views/_local_settings.py`（概要欄反映済み ID の軽量永続化を追加。U5 で成功時に記録する）
- `tests/test_ui_video_detail_page.py`（新規）
- `tests/test_ui_run_page.py` / `tests/test_ui_history_page.py`（クリップボード移設と確認ダイアログへの置換に伴う既存期待の更新）
- `tests/test_ui_highlights.py` / `tests/test_ui_shorts.py`（既存成果物を上書きする経路の確認ダイアログ追加に伴うテスト）
- （`services/` は変更しない）

**背景:** 現状は「実行」ページの結果表示（[`ui/components/results.py`](../src/yt_live_kit/ui/components/results.py) の `render_results()`）が直後の 1 回分の実行結果しか扱えず、「処理済み一覧」（`ui/views/history.py`）の行アクションは過去動画への再生成・削除・概要欄反映を扱う、という 2 つの入口に処理が分かれている。動画詳細ページはこの 2 つを統合し、`services.pipeline.load_result_from_disk(video_id, settings)`（既存関数、`ui/views/history.py` の `_start_description_preview` が既に使っている）を使って **保存済みの成果物から** 常に画面を再構築する。

**作業:**

- [x] U2-1. ステッパー: 字幕 → チャプター → 候補 → ショート → 概要欄の 5 段階を、`ProcessedVideo` / `PipelineResult` / U1 の `count_shorts()` / UI 層の概要欄反映済み ID から計算する純粋関数として実装し、テストする
  - 記法は「✓ 完了」「● 次にやる」「○ 未着手」の 3 状態
  - 「次にやる」ステップのボタンを他より大きく・目立つ色で表示する
  - 概要欄の完了状態は `data/_config/description_applied_videos.json` に video ID の配列として永続化する。U2 では `_local_settings.py` に読み書き関数を用意し、U5 の `update_video_description()` 成功後にのみ記録する
- [x] U2-2. パイプライン順セクションを実装する
  1. 字幕・文字起こし全文（`ui/components/clipboard.py` の共通コピー部品でコピー）
  2. チャプター（表示 + `regenerate(target="chapters")` を `jobs.start_job()` 経由で実行するボタンは expander 内）
  3. 切り抜き候補（表示 + `regenerate(target="clips")` は expander 内）
  4. ハイライト候補（`render_highlights_section` の内容をこのページから呼ぶ）
  5. ショート作成（`render_shorts_section` の内容をこのページから呼ぶ。フェーズ S で拡張される）
  6. 概要欄反映（U5 で差分プレビューに置き換える。このタスクでは呼び出し位置だけ用意する）
- [x] U2-3. `ui/components/clipboard.py` を新設し、`build_clipboard_copy_html` / `render_copy_button` を `results.py` から移設する。**関数のシグネチャ・実装は変更しない**（移動のみ）。呼び出し側の import を更新する
- [x] U2-4. 破壊的操作（元動画削除・成果物の再生成による上書き）を `st.dialog` の確認ダイアログに統一する
  - `ui/views/history.py` の `_render_row_actions` にある「削除を実行 / キャンセル」の 2 段階ボタン方式を、`st.dialog` を開く方式に置き換える
  - 確認文言（「チャプター・全文・切り抜き候補・切り出し済み動画は残ります」等）は既存文言を流用する
- [x] U2-5. 完了済みステップの再実行導線はすべて `st.expander` に畳む（初期状態は閉じる）
- [x] U2-6. ユニットテスト
  - ステッパーの状態計算（5 パターン以上）
  - `st.dialog` 分岐（開く条件・確定時に呼ばれる関数）を `st.dialog` をモックして検証
  - クリップボード部品の移設後もコピー用 HTML が生成されること（既存テストの移設）

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] ステッパーが実データで正しい状態を表示する
- [x] 削除・再生成の破壊的操作がすべて `st.dialog` を経由する
- [x] `services/` に変更が無い
- [x] タスク完了コミット済み

**見積もり目安:** 2 日

---

### U3: 取り込みページ

**目的:** 「登録済みチャンネルの新着を確認 → 1 クリックで処理開始」を基本導線にし、URL 手入力を例外ルートに格下げする。
**変更ファイル範囲:**
- `src/yt_live_kit/ui/views/intake.py`（新規。`ui/views/channel.py` と旧「実行」ページの単本/一括 URL 入力を統合する）
- `src/yt_live_kit/ui/views/channel.py`（`intake.py` へ移設後に削除）
- `src/yt_live_kit/ui/views/run.py`（`intake.py` へ移設後に削除）
- `src/yt_live_kit/ui/views/_local_settings.py`（**U1 で新設済み**。チャンネル既定ハンドルの永続化関数を追記する。U4 も参照する）
- `src/yt_live_kit/ui/app.py`（「取り込み」を登録し、旧「実行」「チャンネル」導線を外す）
- `src/yt_live_kit/services/batch.py`（**U3 に限るフェーズ U の最小例外**。`do_chapters` / `do_clips` の引数追加、`run_batch_job_target()` → `run_batch()` → `pipeline.run()` の全呼び出し伝播、両方 `False` の入力検証のみ）
- `tests/test_ui_intake_page.py`（新規）
- `tests/test_ui_app.py`（「取り込み」登録と旧導線の撤去に合わせてナビゲーション期待を更新）
- `tests/test_ui_channel_page.py` / `tests/test_ui_run_page.py`（有効な検証を `tests/test_ui_intake_page.py` へ移行後に削除）
- `tests/test_batch.py`（既定値の後方互換、全呼び出し伝播、両方 `False` の入力検証を追加検証）
- （上記以外の `services/` は変更しない）

**設計メモ:** チャンネルの既定ハンドルは `Settings`（`config.py`）のフィールドではなく、UI からその場で編集したい値のため、`data/_config/channel_handle.txt`（1 行テキスト）に UI 層の関数で直接読み書きする。`services/description.py` の `get_template_path()` / `save_template()` と同じ発想だが、**U フェーズの制約により `services/` には置かず** `ui/views/_local_settings.py` に置く。この考え方は U1 で先に実装したアーカイブ状態の永続化（`data/_config/archived_videos.json`）と同一であり、`_local_settings.py` はこの 2 つ目の用途として関数を追記するだけでよい（新規ファイル作成は不要）。U4（設定ページ）はこのファイルの関数を再利用する。

**作業:**

- [x] U3-1. `_local_settings.py` に `get_default_channel_handle(settings) -> str | None` / `save_default_channel_handle(handle, settings) -> Path` を実装する
- [x] U3-2. ページ初期状態: 既定ハンドルが保存済みなら、そのチャンネルの新着一覧を自動取得（[`services/channel.py`](../src/yt_live_kit/services/channel.py) の `load_cache()` を優先し、無ければ案内を出す。**自動で `list_archives()` を呼ばない**（NFR-05 のレート制限対策を維持）
- [x] U3-3. 「未処理の新着」を [`services/channel.py`](../src/yt_live_kit/services/channel.py) の `mark_processed()` で絞り込み、チェックボックス付きで表示する
- [x] U3-4. 「選択した N 本を処理開始」ボタン（既存の `run_batch_job_target` を `jobs.start_job()` 経由で実行するロジックを `ui/views/channel.py` から移設し、`do_chapters` / `do_clips` をジョブへ渡す）
- [x] U3-5. URL 入力（単本 / 複数行一括）を、ページ下部の折りたたみに「例外ルート」として配置する（旧「実行」ページの内容を移設）
- [x] U3-6. 「チャプターを作る」「切り抜き候補を出す」チェックを、新着一括処理・URL 入力（単本 / 複数行一括）のすべてで実行ボタンと同じカード内に配置し、選択値を実処理へ反映する
  - `run_batch()` / `run_batch_job_target()` に `do_chapters: bool = True` / `do_clips: bool = True` を追加し、`run_batch_job_target()` → `run_batch()` →各 URL の `pipeline.run()` の全呼び出しへそのまま渡す
  - 両方 `False` の場合は UI で実行ボタンを無効化して日本語で選択を促し、service 側でも明示的に拒否する
- [x] U3-7. ユニットテスト
  - `_local_settings` の保存・読み込み往復
  - 新着一覧が既定ハンドル未設定時に案内を出すこと
  - URL 入力ルートが折りたたみ内にあること（純粋関数化できる部分のみ検証）
  - UI 3 ルートのジョブ起動: 新着一括 / URL 複数行一括は `intake` → `start_job()` → `run_batch_job_target` の kwargs、URL 単本は `intake` → `start_job()` → `run_single_job_target` の kwargs に両 flag が反映されること
  - service の全呼び出し: `run_batch_job_target()` → `run_batch()`、`run_batch()` →各 URL の `pipeline.run()` に両 flag の有効な全組み合わせ（`True` / `True`、`True` / `False`、`False` / `True`）が伝播されること
  - バッチの既定 `True` / `True` が従来挙動を維持し、両方 `False` は `run_batch_job_target()` / `run_batch()` の各 service 入力で明示的に拒否されること
- [x] U3-8. `app.py` のナビゲーションを「取り込み」へ統合し、旧「実行」「チャンネル」導線を外す。移行後の `ui/views/channel.py` / `ui/views/run.py` と対応する旧 UI テストを削除する

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] チャンネルのハンドルを毎回入力しなくてよいことを実データで確認
- [x] `services/` の変更が `services/batch.py` の引数追加・全呼び出し伝播・両方 `False` の入力検証のみで、その他の処理ロジックに変更が無い
- [x] ナビゲーションに旧「実行」「チャンネル」導線が残らず、`ui/views/channel.py` / `ui/views/run.py` が存在しない
- [x] タスク完了コミット済み

**見積もり目安:** 1 日

---

### U4: 設定ページ

**目的:** チャンネル既定値・ffmpeg / フォント設定・Codex CLI の状態を 1 画面で確認できるようにする。
**変更ファイル範囲:**
- `src/yt_live_kit/ui/views/settings.py`（新規）
- `src/yt_live_kit/ui/app.py`（ナビゲーション登録）
- `tests/test_ui_settings_page.py`（新規）
- `tests/test_ui_app.py`（「設定」ページの title / `url_path` 追加に伴うナビゲーション期待値の更新）
- （`services/` は変更しない。`config.py` も変更しない）

**設計メモ:** `Settings`（[`config.py`](../src/yt_live_kit/config.py)）の `ffmpeg_path` / `subtitle_font` / `data_dir` は環境変数（`.env`）経由の設定であり、UI からの永続化編集機能は v3 のスコープに含めない（`config.py` を変更しないという制約と、環境変数運用を崩さないための判断）。設定ページではこれらを **表示専用** とし、変更方法（`.env` の書き方）を案内文で示す。**編集可能な項目は、U3 で実装した「チャンネル既定ハンドル」のみ**とする。フェーズ P では投稿スケジュールポリシー（P2 で `services/schedule.py` を新設）の編集をこのページに追加する。

**作業:**

- [x] U4-1. チャンネル既定ハンドルの表示・編集フォーム（`ui/views/_local_settings.py` の関数を再利用）
- [x] U4-2. ffmpeg パス・字幕フォント・`data_dir` を読み取り専用で表示し、`.env` での変更方法を案内する
- [x] U4-3. Codex CLI の稼働確認: [`services/ai_prompt.py`](../src/yt_live_kit/services/ai_prompt.py) の `is_codex_available()` を呼び、結果を日本語で表示する（利用可能 / 見つからないの 2 状態。**この関数を呼ぶだけで `services/` は変更しない**）
- [x] U4-4. フェーズ P で追加するスケジュールポリシー欄のプレースホルダ（見出しのみ）を用意する
- [x] U4-5. ユニットテスト
  - `is_codex_available` の結果に応じた表示文言（モックして検証）
  - 既定ハンドルの保存フォームが `_local_settings` の関数を正しく呼ぶこと
  - 「設定」の `st.Page`（または代入した `settings_page`）が `st.navigation` へ渡すページ列に実際に含まれることを AST で検証し、全ページの `url_path` が一意であること

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] Codex CLI の状態が画面に表示される（実環境で確認）
- [x] 自動テストで「設定」が `st.navigation` へ実際に登録され、`url_path="settings"` が他ページと重複しないことを確認済み
- [x] `services/` / `config.py` に変更が無い
- [x] タスク完了コミット済み

**見積もり目安:** 0.5 日

---

### U5: 正式 4 画面 IA + ストレージ管理移設 + 概要欄差分プレビュー + フェーズ受け入れ

**目的:** 公開 3 画面 + 非表示詳細の正式 IA を完成させ、旧処理済み一覧のストレージ管理を失わず設定へ移す。概要欄反映（YouTube 上の公開データを書き換える唯一の操作）には他と異なる緊張感のある確認導線を実装し、フェーズ U 全体を受け入れる。
**変更ファイル範囲:**
- `src/yt_live_kit/ui/views/video_detail.py`（概要欄セクションの実装）
- `src/yt_live_kit/ui/views/_local_settings.py`（`update_video_description()` 成功後に概要欄反映済み ID を記録）
- `src/yt_live_kit/ui/views/settings.py`（ストレージ管理セクションを呼び出す）
- `src/yt_live_kit/ui/components/storage_manager.py`（新規。旧 `history.py` のストレージ管理 UI / 純粋ヘルパーを移設）
- `src/yt_live_kit/ui/views/history.py`（旧「処理済み一覧」と確認なしの概要欄更新経路を削除）
- `src/yt_live_kit/ui/app.py`（公開ナビゲーションから旧「処理済み一覧」を外す）
- `src/yt_live_kit/ui/components/status_bar.py` / `src/yt_live_kit/ui/components/results.py`（「処理済み一覧から」の案内を「ライブラリから」へ更新）
- `tests/test_ui_video_detail_page.py`（追記）
- `tests/test_ui_storage_manager.py`（新規。`test_ui_history_page.py` のストレージ管理テストを移設・拡張）
- `tests/test_ui_settings_page.py`（ストレージ管理セクションの接続テストを追記）
- `tests/test_ui_app.py`（正式 4 画面のナビゲーション、status bar / results の案内更新）
- `tests/test_ui_history_page.py`（有効なストレージ管理テストを移設後に削除）
- `docs/execution-plan-v3.md`（完了した作業・Done 条件・フェーズ状態・M12 を更新）
- （`services/youtube_api.py` は変更しない。既存の `fetch_video_snippet` / `merge_chapters_into_description` / `update_video_description` をそのまま使う）
- （`src/yt_live_kit/services/` / `src/yt_live_kit/config.py` は変更しない）

**背景:** 現状の [`ui/views/history.py`](../src/yt_live_kit/ui/views/history.py)（U0 移設後）の `_render_description_preview` は「反映後」のマージ済みテキストのみを表示し、`st.dialog` を通らず、成功時に概要欄反映済み ID も記録しない。このページが公開ナビゲーションに残っているため、動画詳細だけを改善しても差分確認を迂回できる。U5 で旧ページを削除して更新経路を動画詳細へ一本化する。一方、旧ページ固有のストレージ管理は v1 / v2 の AC-15 を維持するため、共通部品へ分離して設定ページへ移設する。フェーズ U 完了時の 4 画面は、**公開 3 画面（ライブラリ / 取り込み / 設定）+ 非表示の動画詳細 1 画面**とする。

**作業:**

- [x] U5-1. 正式 4 画面 IA とストレージ管理移設
  - `app.py` の公開ナビゲーションを「ライブラリ」「取り込み」「設定」の 3 画面にし、「動画詳細」は `visibility="hidden"` のまま維持する
  - `history.py` と `test_ui_history_page.py` を削除し、旧「処理済み一覧」にあった概要欄更新の迂回経路を廃止する
  - 旧 `history.py` のストレージ容量集計、全動画の内訳、個別削除、N 日以上前の一括削除を `ui/components/storage_manager.py` へ移し、設定ページから呼ぶ。全件表示、検索またはページングにより全動画へ到達可能にし、各動画に元動画容量と個別削除導線を表示する
  - 個別削除ダイアログには動画識別子・対象 1 件・削除対象バイト数（元動画 + 中間）・残る成果物を表示する。一括削除はプレビュー時に対象動画 ID の不変スナップショットを作り、ダイアログにはその件数・総容量・残る成果物を表示する。いずれも確定前は purge を呼ばず、確定後だけダイアログへ渡した動画 ID を削除し、確認後の再走査で対象を増やさない。`StorageError` は日本語で表示する。削除後もチャプター・全文・候補・切り出し済み動画を残す
  - `status_bar.py` / `results.py` の「処理済み一覧から」という案内を「ライブラリから」へ更新する
- [x] U5-2. 概要欄プレビューを開く共通フローを `video_detail.py` に実装する
  - 外側の「概要欄に反映」は `type="primary"` と警告表示で強調する
  - OAuth 設定、チャプター存在・形式を検証し、不正時は日本語で案内してプレビュー / 更新を開始しない。検証成功後に `fetch_video_snippet` で反映前を取得し、`merge_chapters_into_description` で反映後を作る
  - ステッパーの「次にやる: 概要欄」CTA と通常の概要欄ボタンは、同じプレビュー開始関数を呼ぶ
- [x] U5-3. `st.dialog(width="large")` 内に「更新前」「更新後」を別々の読み取り専用表示として並べる。**確定前のダイアログ再描画**では API を再取得しないよう、取得済みの更新前 / 更新後をダイアログ引数として渡す。確定時は既存 `update_video_description()` 内部の `fetch_video_snippet()` を維持し、`services/youtube_api.py` は変更しない
- [x] U5-4. 確認ボタンを押した場合だけ `update_video_description()` を呼び、成功後に限り `mark_description_applied()` を呼ぶ
  - update 失敗時は mark せず、日本語エラーを表示する
  - YouTube 更新成功後にローカル mark だけ失敗した場合は、「YouTube 側は更新済みだが完了状態を保存できなかった」と日本語で明示する
  - 成功時は再描画後にステッパーの概要欄が完了になる
- [x] U5-5. ユニットテスト
  - 外側ボタン未クリック時は snippet 取得 / update / mark のいずれも呼ばれない
  - プレビュー開始時は OAuth・チャプター検証後に fetch / merge だけが呼ばれ、update は呼ばれない
  - ダイアログに更新前 / 更新後が別々に表示され、確定前は update / mark が呼ばれない
  - 確定時だけ update → mark の順で各 1 回呼ばれる
  - update 失敗時は mark されない。mark 失敗時は YouTube 更新済みと分かる日本語警告を表示する
  - OAuth 未設定、チャプター無し / 形式不正、5000 文字超過、snippet 取得失敗を検証する
  - ステッパー CTA と通常ボタンが同じフローを呼ぶこと、成功記録後にステッパーが完了することを検証する
  - 正式 4 画面の登録、旧 history import / URL の不在、ストレージ管理の設定ページ接続、既存ストレージ管理テストの移設後の回帰を検証する
  - ストレージ一覧が 10 件を超えても 11 件目以降へ到達でき、その動画の元動画容量と個別削除導線が表示されることを検証する
  - 個別削除は、動画識別子・1 件・削除対象バイト数・残る成果物の表示、未確定時 purge なし、確定時だけ正確な動画 ID を purge、`StorageError` の日本語表示を検証する
  - 一括削除は、対象動画 ID の不変スナップショット、対象件数・総容量・残る成果物の表示、未確定時 purge なし、確定時だけダイアログに渡した正確な動画 ID を purge、確認後の再走査で対象が増えないこと、`StorageError` の日本語表示を検証する
  - 個別 / 一括削除の双方で、チャプター・全文・候補・切り出し済み動画が残る回帰テストを行う
- [x] U5-6. **フェーズ U 受け入れ:**
  - `docs/requirements-v3.md` の AC-18〜AC-22 をすべて手で確認し、結果をタスク完了報告に記載する
  - v2 未完了タスク（§4）のうち U5 に吸収した項目（V3-6 実機確認、V7-1/V7-2 の該当分）を実データで確認する
  - 概要欄は、実 YouTube 書き込みを行わず、ダイアログの更新前 / 更新後表示と「確定前は更新されない」ことを安全に手動確認する。update / mark の成功・失敗はモックと隔離した一時 `data_dir` で確認する
  - `uv run pytest` が全件通ることを確認する
  - `src/yt_live_kit/ui/pages/` に相当する旧構成が残っていないことを確認する（U0 の確認の再確認）

**U5 受け入れ証跡（2026-08-01）:**

| 対象 | 実証拠 |
|------|--------|
| IA / ライブラリ | 実ブラウザで公開ナビゲーションが「ライブラリ / 取り込み / 設定」の 3 画面だけであることを確認。投入前に 47 本を表示し、No.83 の部分一致検索で 1 本に絞り込み、非表示の動画詳細へ遷移した。V3-6 の実投入後は 48 本になった |
| 動画詳細 | No.83（`d4RpmRKh1mw`）で 5 段ステッパー、全 6 セクション、primary の概要欄ボタンを確認。概要欄は YouTube から読み取りだけを行い、large dialog に更新前 / 更新後の読み取り専用表示と「YouTube は更新せず完了状態だけ保存」を確認した。確定せず閉じたため write / mark は未実行 |
| 取り込み / V3-6 | 実キャッシュ 50 件、未処理 3 件、既定ハンドル `@aiseitai`、新着 / URL 単本 / URL 複数行の全ルートと両オプションを確認。No.119（`IJvd6k6ZmUo`）を両オプション有効で 1 本投入し、39 秒で成功 1 / スキップ 0 / 失敗 0。字幕・チャプター・候補が生成され、未処理 2 件、ライブラリ 48 本になった |
| 設定 / ストレージ | 実画面で既定ハンドル、環境値、Codex CLI 利用可能、ストレージ管理を確認。投入前の容量集計で 47 / 47 件と 11 件目以降の個別削除導線を確認。削除操作は未実行 |
| V7-2 の U5 該当分 | No.83 と No.119 の実 2 本で字幕・チャプター・候補・動画詳細を確認。ハイライト / ショート固有の通し確認は計画どおり S5 の受け入れ対象であり、U5 をブロックしない |
| 自動テスト / 構成 | `uv run pytest` 441 件全通過。AppTest / AST / 一時 `data_dir` のモック証跡を併用。旧 `ui/pages`、`history.py`、`test_ui_history_page.py`、`url_path="history"` は存在せず、`services/` / `config.py` に差分なし |

**Done 条件:**

- [x] AC-18〜AC-22 が満たされている（未達は明示的に申し送りする）
- [x] 反映前 → 反映後の対比が画面に表示される
- [x] 公開ナビゲーションが 3 画面 + 非表示詳細の合計 4 画面になり、`history.py` / `test_ui_history_page.py` / `url_path="history"` が残っていない
- [x] ストレージ管理が設定ページへ移り、v1 / v2 の AC-15 に回帰がない
- [x] update 成功後だけ mark されること、update 失敗時に mark されないこと、mark 単独失敗時の日本語警告を自動テストで確認済み
- [x] 実 YouTube 書き込みを受け入れ試験で実行していない
- [x] `uv run pytest` が全件通る
- [x] `services/` / `config.py` に変更が無い
- [x] フェーズ完了コミット済み

**見積もり目安:** 1.5 日

---

### S1: テロップ台本 + メタデータ生成

**目的:** 選択した複数区間の自動字幕から、人が確認・微修正できる「テロップ台本」と、フック文言・タイトル案・説明文・タグを一括生成する。
**変更ファイル範囲:**
- `prompts/telop_script.md`（新規）
- `src/yt_live_kit/models/telop.py`（新規）
- `src/yt_live_kit/services/telop.py`（新規）
- `src/yt_live_kit/services/subtitle_burn.py`（VTT カット単位字幕を取得する既存非公開処理の公開ヘルパー化のみ）
- `tests/test_telop.py`（新規）
- （`ui/` は変更しない。UI からの呼び出しは S4 で行う）

**背景:** [`services/highlights.py`](../src/yt_live_kit/services/highlights.py) の「テンプレート結合 → Codex CLI 実行 → JSON 抽出 → バリデーション → 保存」パターンをそのまま踏襲する。ただし入力は圧縮版全文ではなく、**選択区間のカット単位の字幕**（[`services/subtitle_burn.py`](../src/yt_live_kit/services/subtitle_burn.py) の `_parse_vtt_with_end` / `filter_cues_for_segment` が返す `TimedCue` 相当）である。これらは現状 `subtitle_burn.py` の非公開関数のため、S1 の実装時に必要な範囲で公開関数化する（`services/subtitle_burn.py` への軽微な追加は許容。フェーズ S は `services/` を変更してよい）。

**作業:**

- [x] S1-1. `prompts/telop_script.md` を作成する
  - 入力: 区間ごとの `[HH:MM:SS.mmm --> HH:MM:SS.mmm] テキスト` 形式のカット単位字幕（圧縮版ではなく原文に近い粒度）。開始・終了の両方とミリ秒を保持し、Codex が行の絶対秒を区間内へ配置できるようにする
  - 出力 JSON: 区間ごとのテロップ行（元動画基準の絶対秒 `start_sec` / `end_sec`、本文、強調フラグ）+ `hook_text`（1 本）+ `title_candidates`（複数）+ `description` + `tags`
  - 出力ルール: 半角 `<` `>` 禁止、各行は画面に収まる短さ（目安 13〜16 文字）に分割すること、誤字脱字・固有名詞の誤変換は補正してよいが**話していない内容を追加しない**こと
- [x] S1-2. `models/telop.py` に `TelopLine`（`text`, `start_sec`, `end_sec`, `emphasis: bool`）、`TelopSegmentScript`（元動画基準の絶対秒 `start_sec`, `end_sec` と区間ごとの `TelopLine` リスト）、`TelopScriptDocument`（`hook_text`, `title_candidates`, `description`, `tags`, `segments`）を pydantic で定義する
- [x] S1-3. `services/telop.py` に以下を実装する
  - `generate_telop_script(video_id: str, segments: Sequence[HighlightSegment], settings=None, *, on_progress=None, prompt_only=False, codex_path="codex") -> TelopScriptResult`
  - `TelopScriptResult` は `video_id: str`, `clip_id: str`, `prompt_path: Path`, `script_path: Path | None`, `used_codex: bool`, `document: TelopScriptDocument | None` を持つ frozen dataclass とする。`prompt_only=True` はプロンプトを保存し、`script_path=None`, `used_codex=False`, `document=None` で正常終了する
  - `validate_telop_script(doc: dict | TelopScriptDocument, *, segments: Sequence[HighlightSegment]) -> TelopValidationResult`
  - `TelopValidationResult` は `ok: bool`, `errors: tuple[str, ...]`, `warnings: tuple[str, ...]`, `document: TelopScriptDocument | None = None` を持つ frozen dataclass とする
  - 検証では、入力との区間数一致、入力順での区間境界のミリ秒単位一致、各区間 1 行以上、各行の `end_sec > start_sec`、対応区間内への収まり、行の時系列順と非重複、`title_candidates` 1 件以上、`tags` 1 件以上を必須とする。`hook_text`・行本文・各タイトル案・`description`・各タグは strip 後に非空でなければならない。半角 `<` `>` 禁止はこれら全生成文字列に適用する。行本文が 16 文字を超える場合だけ `warnings` に記録し、検証エラーにはしない。pydantic の詳細はそのまま出さず日本語のスキーマエラーへ変換する
  - `make_clip_id(segments: Sequence[HighlightSegment | tuple[float, float]]) -> str`: `HighlightSegment` の `start` / `end` は既存 `parse_timestamp_to_seconds()` で数値化してから、tuple と同じ正規化へ渡す。各秒値を `Decimal(str(value))` と `ROUND_HALF_UP` でミリ秒の整数へ正規化し、各区間を `start_ms-end_ms`、入力順の区間列を `|` で連結した UTF-8 文字列の SHA-256 先頭 12 桁を返す。空配列、非有限値、負値、`end <= start` は日本語エラーにする。S3 はこの関数を再利用する
  - プロンプト保存先: `data/{video_id}/shorts/telop/prompt_telop_{clip_id}.txt`
  - 台本保存先: `data/{video_id}/shorts/telop/telop_{clip_id}.json`（`clip_id` は上記 `make_clip_id()` を使い、S3 の出力ファイル名と揃える）
  - Codex 出力 JSON の抽出は、コードフェンス付き・前後テキスト付き出力に対応する非公開ヘルパーを `services/telop.py` 内に局所実装する。`highlights.py` の非公開関数は import せず、S1 では共通化のために `highlights.py` / `ai_prompt.py` を変更しない
  - `services/subtitle_burn.py` の `_parse_vtt_with_end()` を `parse_vtt_with_end()` として公開し、既存呼び出し互換のため旧名 alias を残す。S1 では公開 parser と `filter_cues_for_segment()` を使い、区間内相対秒へ元区間開始秒を足して絶対秒へ戻す
  - Codex 未導入時は `services/highlights.py` の `CODEX_INSTALL_HINT` と同じ形式のヒントを出す
- [x] S1-4. ユニットテスト
  - `validate_telop_script` の違反パターン（入力との区間数不一致・境界不一致、行なし、区間外の行、行の逆転・重複、strip 後の空本文、`hook_text` 空、`title_candidates` なし・空文字、`description` 空、`tags` なし・空文字、全生成文字列の半角 `<>`）と、16 文字超が error ではなく warning になること、pydantic 詳細を漏らさない日本語スキーマエラー
  - `make_clip_id` が同じ境界の `HighlightSegment` / tuple で同じ ID、順序を変えると異なる ID になること。`ROUND_HALF_UP` の 0.5 ミリ秒境界と、空配列・非有限値・負値・`end <= start` の日本語エラー
  - Codex 出力 JSON の抽出（コードフェンス付き / 前後にテキストがある場合）
  - Codex 未導入時のヒント文言

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] Codex CLI が失敗した場合は既存方針どおり例外を送出するが、既存の同一 `telop_{clip_id}.json` と他の成果物（チャプター・候補・ハイライト）を変更しない。S1 単体では AC-23 の UI 修正・焼き込み反映を完了扱いにせず、後続 S3 / S4 で閉じる
- [x] タスク完了コミット済み

**見積もり目安:** 1.5 日

---

### S2: ASS テロップスタイルプリセット + フックタイトル

**目的:** [`services/subtitle_burn.py`](../src/yt_live_kit/services/subtitle_burn.py) の ASS スタイル（現状は白 54px の 1 スタイルのみ）を、テロップ風の複数プリセットに拡張し、冒頭フックタイトル用の大テロップを生成できるようにする。
**変更ファイル範囲:**
- `src/yt_live_kit/services/subtitle_burn.py`（`write_ass()` の拡張、`write_hook_ass()` の新規追加）
- `tests/test_subtitle_burn.py`（追記）
- （`services/shorts.py` は S3 で変更する。このタスクでは触らない）

**背景:** 現行の `write_ass()` は `[V4+ Styles]` に `Default` の 1 スタイルだけを書き出す（`FontName=54px、白文字、縁取り 3px、Alignment=2、MarginV=180`）。libass の ASS 形式は `\b1`（太字）、`\bord`（縁取り太さ）、`3` の `BorderStyle`（不透明ボックス＝座布団）、`{\c&HBBGGRR&}` によるインライン色替えをサポートしており、**追加の pip 依存なしで**テロップ風の見た目を作れる。

**作業:**

- [x] S2-1. `TimedCue` の末尾に `emphasis: bool = False` を追加する。既存の 3 引数構築を壊さず、`TimedCue` を再生成する `filter_cues_for_segment()` 等ではフラグを引き継ぐ
- [x] S2-2. frozen dataclass の `TelopPreset` と `TELOP_PRESETS: dict[str, TelopPreset]` を定義する
  - `TelopPreset` は `font_size: float`、`primary_colour` / `secondary_colour` / `outline_colour` / `back_colour: str`、`bold` / `italic` / `underline` / `strike_out: bool`、`scale_x` / `scale_y` / `spacing` / `angle: float`、`border_style: int`、`outline` / `shadow: float`、`alignment: int`、`margin_l` / `margin_r` / `margin_v: int`、`encoding: int`、`emphasis_colour: str` を持つ。`font_name` は従来どおり呼び出し引数で渡す
  - ASS スタイル行の真偽値は `True=-1` / `False=0` で直列化する。スタイル色はアルファ込みの `&HAABBGGRR`、アルファを受けないインライン色は `&HBBGGRR&` とする。`BorderStyle` は通常縁取りを `1`、矩形の座布団を `3` とする
  - `primary_colour` / `secondary_colour` / `outline_colour` / `back_colour` / `emphasis_colour` は `&H` + 8 桁の 16 進数というスタイル色形式を必須とし、不正なプリセット定義を日本語の `SubtitleBurnError` にする。インライン色は、スタイル色の先頭 2 桁のアルファを除いて `&HBBGGRR&` に変換する
  - `"default"` は既存 v2 スタイル互換とし、`Style: Default,{font_name},54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,180,1` と完全一致させる。`"bold_outline"` は太字 + 強い通常縁取り、`"boxed"` は `BorderStyle=3` の座布団、`"hook"` は `font_size > 54` のフック専用スタイルとする
- [x] S2-3. `write_ass(cues, output_path, *, font_name, preset="default", hook_text: str | None = None, hook_preset="hook", play_res_x=1080, play_res_y=1920) -> Path` へ後方互換で拡張する
  - `hook_text is None` の場合は Hook スタイル / Hook イベントを一切追加せず、既定引数で既存のヘッダー・`Default` スタイル・イベント出力と完全互換にする
  - `hook_text` がある場合は、選択した通常字幕スタイルを `Default`、フック用スタイルを `Hook` として同一 ASS に出力する。通常字幕は `Layer=0`、フックは `Layer=1` とする
  - `TimedCue.emphasis=True` は語の一部ではなく、安全化済みの行全体を、選択した `TelopPreset.emphasis_colour` から導出したインライン色タグと、同じプリセットの `primary_colour` から導出した復帰色タグで囲む。本文を先に安全化し、その後でのみ管理下の色タグを付与する
  - 通常字幕とフックに共通する安全化は、CRLF / CR を LF へ正規化し、ユーザー由来の `\` / `{` / `}` を全角の `＼` / `｛` / `｝` へ置換し、LF 以外の C0 制御文字を空白へ置換してから、最後に実 LF だけを管理下の `\N` へ変換する。この順序により、ユーザー由来の ASS override tag / control sequence を残さず、物理的な `Dialogue` 行を増やさない
  - `hook_text is not None` かつ strip 後に空の場合は、`write_hook_ass()` と同じ日本語の `SubtitleBurnError` にする
  - 不明な `preset` / `hook_preset` は、利用可能な名前を含む日本語の `SubtitleBurnError` にする
- [x] S2-4. `write_hook_ass(hook_text, output_path, *, font_name, preset="hook") -> Path` を新規実装する。同じ ASS serializer へ委譲し、フックを `0:00:00.00` から `0:00:02.00` まで固定表示する。strip 後に空の `hook_text` は日本語の `SubtitleBurnError` にする。既存の `build_segment_subtitle()` は後方互換のため既定プリセットで動かし、v2 の呼び出し元を壊さない
- [x] S2-5. ユニットテスト
  - `TimedCue` を従来の 3 引数で構築でき、`emphasis=False` になること。再生成処理でもフラグを引き継ぐこと
  - `default` のスタイル行が既存文字列と完全一致し、`preset` 省略かつフックなしの出力に Hook スタイル / イベントが無いこと。各追加プリセットの必須フィールド・`BorderStyle`・色形式とスタイル行の差分
  - 不明な `preset` / `hook_preset` が利用可能名を含む日本語エラーになること
  - 全プリセット色が `&H` + 8 桁の 16 進数として検証され、不正な色を日本語エラーにすること。強調なしでは色タグが無く、強調ありでは選択プリセットの `emphasis_colour` から導出した色で安全化済みの行全体を囲み、`primary_colour` から導出した色へ復帰すること
  - 通常字幕と Hook の双方で、ユーザー由来の `\` / `{` / `}`、文字列として入力された `\N`、実改行、LF 以外の C0 制御文字を安全化できること。ユーザー由来の override / control sequence が残らず、実改行を含めても想定外の物理 `Dialogue` 行が増えないこと
  - `write_hook_ass` が `0:00:00.00`〜`0:00:02.00`、`PlayResX/PlayResY=1080x1920`、54 より大きいフォントで出力すること。`write_hook_ass()` と `write_ass(..., hook_text=...)` の双方が空白だけのフックを拒否すること
  - 同一 ASS に通常字幕と Hook の両スタイル・両イベントが入り、通常字幕 `Layer=0` / Hook `Layer=1` になること
  - `build_segment_subtitle()` が引き続き既定の `default` 相当で動くこと

**Done 条件:**

- [x] `uv run pytest` が全件通る（既存の `test_subtitle_burn.py` を壊さない）
- [x] 追加の pip 依存が無い
- [x] タスク完了コミット済み

**見積もり目安:** 1 日

---

### S3: ジャンプカット連結ショート生成（services 拡張）

**目的:** 複数の小区間を 1 本の縦型ショートに連結し、テロップとフックタイトルを焼き込む。v3 の中核機能。
**変更ファイル範囲:**
- `src/yt_live_kit/services/shorts.py`（新関数 `build_short_from_segments()` を追加）
- `src/yt_live_kit/services/subtitle_burn.py`（複数区間の累積タイムオフセット計算を行う `build_concatenated_subtitle()` を追加し、既存 `_get_preset()` を副作用なく preset 名を事前検証できる公開 `get_telop_preset(name: str) -> TelopPreset` へ refactor）
- `src/yt_live_kit/services/telop.py`（共通の整数ミリ秒正規化 API を公開し、`make_clip_id()` / `validate_telop_script()` を同 API 利用へ refactor。`validate_telop_script()` の `segments` 型注釈も `Sequence[HighlightSegment | tuple[float, float]]` へ広げる）
- `tests/test_shorts.py` / `tests/test_subtitle_burn.py` / `tests/test_telop.py`（追記）
- （`services/ffmpeg.py` は変更しない。既存の `encode_segment` / `concat_segments` をそのまま使う。`concat_segments()` が内部で `build_concat_list()` を呼ぶため、S3 から `build_concat_list()` を直接二重呼び出ししない）

**背景:** 既存の `cut_and_concat()`（[`services/ffmpeg.py`](../src/yt_live_kit/services/ffmpeg.py)）は横型のハイライト動画向けに「複数区間を再エンコードして連結する」処理を持ち、既存の `build_short()`（[`services/shorts.py`](../src/yt_live_kit/services/shorts.py)）は単一区間向けに「2 パス（精密シークで切り出し → レイアウト + 字幕焼き込み）」を持つ。S3 はこの 2 つを組み合わせる。

**作業:**

- [ ] S3-1. `build_short_from_segments(video_id, segments: list[tuple[float, float]], settings=None, *, layout="blur", telop_script: TelopScriptDocument | None = None, hook_text: str | None = None, preset="default", hook_preset="hook", output_name: str | None = None, ffmpeg_path=FFMPEG_DEFAULT, on_progress: ShortsProgressCallback = None, keep_intermediate=False) -> ShortResult` を実装する。`ShortsProgressCallback` は `Callable[[int, int, str], None] | None` とし、`total = len(segments) + 3`、各区間を `i`、連結を `n + 1`、字幕準備を `n + 2`、焼き込みを `n + 3` として通知する
  - `services/telop.py` に `NormalizedSegmentBounds` frozen dataclass（`start_ms` / `end_ms` と、それらから導出する `start_sec` / `end_sec` / `duration_ms` / `duration_sec` property）、公開 `normalize_seconds_to_milliseconds(value: float | int) -> int`、`normalize_segment_bounds(segments: Sequence[HighlightSegment | tuple[float, float]]) -> tuple[NormalizedSegmentBounds, ...]` を追加する。既存 `_to_milliseconds` / `_normalized_bounds` 相当の実装をここへ一本化し、`make_clip_id()`、`validate_telop_script()`、S3 の全処理が同じ `Decimal(str(value))` + `ROUND_HALF_UP` を使う
  - 入力順を再生順・`make_clip_id()` の ID 生成順・`encode_segment()` の呼び出し順としてそのまま保持し、自動で sort / dedupe しない
  - 空配列、数値でない値、NaN / 無限、負値、逆転区間、ミリ秒への `ROUND_HALF_UP` 後に開始・終了が同値になる区間は、ffmpeg を呼ぶ前に日本語の `ShortsError` にする
  - ID、合計尺、`encode_segment()` へ渡す秒、字幕の累積 offset / 区間 clip はすべて `NormalizedSegmentBounds` の整数ミリ秒を唯一の基準とする。合計尺も整数ミリ秒で `10_000 <= total_ms <= 180_000` を比較し、浮動小数の元入力から別計算しない
  - 次の手順へ入る前の preflight で、全区間、`layout`、`output_name`、明示 `hook_text`、`preset` / `hook_preset` と、`telop_script` があれば `validate_telop_script(..., segments=segments)` を検証する。明示 hook は strip 後の空文字と半角山カッコを拒否し、preset は公開 `get_telop_preset()` を使う。区間数・ミリ秒境界・行の区間内配置・時系列順・行の非重複・全テキスト規則を満たす正規化済み document を得られない限り、`ensure_source_video()` を含む ffmpeg / ダウンロード処理を一切始めない
  - 手順:
    1. `ensure_source_video()` で元動画を確保
    2. 各区間を正規化済み `start_sec` / `end_sec` で `encode_segment(..., crf=INTERMEDIATE_CRF)` へ渡して切り出す → `data/{video_id}/shorts/segments/{clip_id}/seg_001.mp4` …
    3. `concat_segments()` で 0 秒始まりの中間ファイルに連結する → `.../segments/{clip_id}/concat.mp4`。concat list は同関数が内部生成するため、S3 から `build_concat_list()` を直接呼ばない
    4. preflight で得た正規化済み document を `subtitle_burn.build_concatenated_subtitle()`（新規）へ渡し、`preset` / `hook_preset` を維持して区間ごとの行を**連結後のタイムライン**へ再計算する。さらに S2 の `write_ass(..., preset=preset, hook_text=..., hook_preset=hook_preset)` を使って通常字幕とフックを同一 ASS に生成する
    5. パス 2（[`build_short()`](../src/yt_live_kit/services/shorts.py) と同じ考え方）: レイアウト（blur / crop）+ 字幕焼き込みを連結済み中間ファイルに対して実行する。S3 の字幕フィルタは ASS 内の `Default` / `Hook` スタイルを優先するため `subtitles=...` を `force_style` なしで使う。既存 `build_short()` の単一区間向け `force_style` 経路は変更しない
    6. 最終動画は同じ output ディレクトリ内の一時 `.mp4` に書き、ファイル存在・非ゼロを確認してから正式出力へ atomic replace する。途中失敗時は一時出力だけを削除し、同名の既存正式 mp4 を維持する。最終 ffmpeg ログは `output/{正式出力stem}.ffmpeg.log` の固定名で残し、custom `output_name` にも追従させる
    7. `keep_intermediate=False`（既定）なら成功・失敗のどちらでも `segments/{clip_id}/` 全体（`seg_*.mp4`、各ログ、`concat.mp4`、concat ログ、失敗時の `concat.txt`）を削除する。`True` ならその時点で存在する中間物を成功・失敗とも残す。元動画、ASS、S1 JSON、最終 mp4、最終ログは削除しない
  - 出力: 既定は `data/{video_id}/shorts/output/short_{clip_id}.mp4`（単一区間の従来命名 `short_{start}_{end}.mp4` とは区別する）。`output_name` は空、絶対パス、パス区切り文字を含む値、`.` / `..`、`.mp4` 以外を日本語エラーで拒否する
  - `make_clip_id()` / `validate_telop_script()` の `TelopError`、`FfmpegError`、`SubtitleBurnError` は公開境界で日本語の `ShortsError` へ変換する。ASS を焼き込むため `ShortResult.burned_subtitles=True` とし、日本語フォント未解決時は既存 `build_short()` と同じ `font_warning` 文言を返す
- [ ] S3-2. `subtitle_burn.build_concatenated_subtitle(video_id: str, segments: Sequence[tuple[float, float]], settings: Settings | None = None, *, telop_script: TelopScriptDocument | None = None, hook_text: str | None = None, preset: str = "default", hook_preset: str = "hook") -> Path` を実装する
  - 関数単独で呼ばれた場合も、関数内 import した `normalize_seconds_to_milliseconds()` / `normalize_segment_bounds()` / `make_clip_id()` / `validate_telop_script()` へ一本化し、区間正規化と telop 再検証を必ず行う。S3-1 との二重検証は公開境界の安全性のため許容し、呼び出し側から `clip_id` や正規化結果を受け取る別経路は作らない
  - 明示 `hook_text` はこの公開境界自身でも検証し、strip 後の空文字と半角山カッコを拒否する。これにより `telop_script=None` の直呼びでも固定出力ルールを迂回できないようにする
  - 出力先は `data/{video_id}/shorts/subtitles/short_{clip_id}.ass`。上記の関数内 import により `telop.py` → `subtitle_burn.py` の既存 import との循環を避ける
  - 各区間のカットは元動画のタイムコードを持つため、連結後の行時刻を整数ミリ秒で `先行区間の累積 ms + 行の絶対 ms - 元区間開始 ms` と計算する。防御的に各相対時刻を `0..区間尺 ms` へ clip し、clip 後に終了が開始以下なら日本語エラーにする
  - `telop_script` がある場合は関数内再検証で得た正規化済み document を使う。`hook_text` の明示値を優先し、`None` なら document の `hook_text` を使う
  - `telop_script` が無い場合（S1 を経ずに生成する場合）は `subtitles/ja.vtt` を 1 回だけ読み、VTT 由来の字幕（既存の `parse_vtt_with_end` / `filter_cues_for_segment`）へフォールバックする。各 filter 結果の区間相対 cue を共通 helper で整数ミリ秒へ正規化し、先行区間の累積 ms を加えて連結 timeline へ変換する。VTT 不在は日本語エラー、個々の区間で cue が 0 件なのは許容し、VTT が存在して `hook_text` があれば Hook 単独 ASS も生成できる
  - 通常字幕と `hook_text`、`preset`、`hook_preset` は S2 の同一 ASS API へ一度に渡し、ffmpeg の字幕焼き込みも 1 回だけ行う。フック用の別 ASS を後段で重ねず、選択プリセットを全呼び出し段で欠落させない
- [ ] S3-3. 尺バリデーション: 正規化済み整数ミリ秒の合計で 10,000 ms・180,000 ms を許可し、9,000 ms は `MIN_DURATION_SEC` 未満、181,000 ms は `MAX_DURATION_SEC` 超として `ShortsError` にする。180 秒超の**エラーメッセージには「区間を減らすか短くしてください」という具体的な対処を含める**（UI 側の分割・短縮誘導は S4 で実装する）
- [ ] S3-4. ユニットテスト
  - 3 区間で `encode_segment(..., crf=INTERMEDIATE_CRF)` が入力順に 3 回呼ばれ、その 3 パスが順番どおり `concat_segments()` へ渡ること。S3 が `build_concat_list()` を直接二重呼び出ししないこと
  - 入力順を変えても sort / dedupe されず、ID・encode・再生順が入力どおりであること
  - 空配列、非数値、NaN / 無限、負値、逆転、ミリ秒丸め後の同値を ffmpeg 前に拒否すること
  - 公開正規化 helper、`make_clip_id()`、`validate_telop_script()` が同じ整数ミリ秒を使うこと。`0.00049` / `0.0005` / `9.99949` / `9.99951` 等の 0.5 ms 境界で ID、合計尺、encode 引数、累積 offset / clip が一貫すること
  - 合計尺の整数 ms 境界（9,000 / 10,000 / 180,000 / 181,000 ms）と、181 秒の案内文言
  - 累積オフセット計算（3 区間以上、区間間に間隔がある場合）、区間境界での clip、clip 後に無効になる行の日本語エラー
  - telop の区間数・ミリ秒境界・行範囲・行順序・行重複・テキスト規則の不一致を ffmpeg 前に拒否し、正規化済み document を使うこと
  - `build_concatenated_subtitle()` 直呼びでも不正 tuple / telop と、strip 後に空または半角山カッコを含む明示 hook を拒否すること。VTT を 1 回だけ読み、間隔のある 3 区間の相対 cue に累積 ms を加えた連結 timeline を直接検証すること。cue 0 件の許容、VTT 不在の日本語エラー、VTT あり + Hook 単独 ASS
  - 不正な layout / output_name / 明示 hook（strip 空・半角山カッコ）/ preset / hook_preset を preflight で拒否し、`ensure_source_video()` / ffmpeg が呼ばれないこと
  - `on_progress` の total が `len(segments) + 3` で、各区間 `i`、連結 `n + 1`、字幕準備 `n + 2`、焼き込み `n + 3` の順に呼ばれること
  - `preset` / `hook_preset` が `build_short_from_segments()` → `build_concatenated_subtitle()` → `write_ass()` へ欠落なく伝播すること
  - S3 の blur / crop 両コマンドが `subtitles=...` を 1 回だけ使い `force_style` を含まず、パス 2 に `-ss` / `-t` が無いこと。既存 `build_short()` の `force_style` 付きコマンド契約は変わらないこと
  - unsafe な `output_name` を拒否し、既定の mp4 / ASS が同じ `clip_id` を使うこと
  - encode / concat / ASS / パス 2 の各失敗と成功で、`keep_intermediate=False` は中間ディレクトリ全体を削除し、`True` は存在分を保持すること
  - パス 2 失敗時は一時 mp4 だけを削除して既存正式 mp4 を維持し、成功時だけ atomic replace すること。最終ログが `output/{正式出力stem}.ffmpeg.log` となり custom 名にも追従すること
  - `TelopError` / `FfmpegError` / `SubtitleBurnError` の `ShortsError` 変換、`burned_subtitles=True`、既存と同じ `font_warning` 条件・文言
  - subprocess はすべてモックし、実 ffmpeg を呼ばない

**Done 条件:**

- [ ] `uv run pytest` が全件通る
- [ ] 出力パスの命名規則が S1（`telop_{clip_id}.json`）と揃っている
- [ ] タスク完了コミット済み

**見積もり目安:** 2 日

---

### S4: キュー量産 UI + 台本確認フロー

**目的:** 複数のショート候補をまとめて生成できるようにし、生成前に必ず 1 本ずつテロップ台本を確認させる。
**変更ファイル範囲:**
- `src/yt_live_kit/services/shorts_queue.py`（新規。[`services/batch.py`](../src/yt_live_kit/services/batch.py) の `run_batch` と同じ「複数件を順次処理し進捗を報告する」パターンを踏襲する）
- `src/yt_live_kit/services/jobs.py`（`JobKind` のコメントに `"shorts_queue"` を追加するのみ。ロジックは変更しない）
- `src/yt_live_kit/ui/views/video_detail.py`（キュー量産セクションの追加）
- `tests/test_shorts_queue.py` / `tests/test_ui_video_detail_page.py`（追記）

**作業:**

- [ ] S4-1. 候補選択 UI: ハイライト候補・切り抜き候補の一覧にチェックボックスを追加する。「選択した区間を個別に生成する」と「選択した区間をまとめて 1 本に連結する」をトグルで切り替えられるようにする（個別 = 各候補ごとに `build_short_from_segments([1区間])`、連結 = `build_short_from_segments([複数区間])`）
- [ ] S4-2. 生成前の台本確認: 各生成対象（個別なら各候補、連結なら選択セット全体）について、`services.telop.generate_telop_script()` を呼び、結果をテキストエリアで表示・編集できるようにする。**確定操作を挟んでからでないとキューに積めない**
  - `ClipCandidate` を入力する場合は、既存 `ui/views/highlights.py` の `clip_to_highlight_segment()` と同等の変換で `HighlightSegment` へ正規化してから S1 を呼ぶ。S1 の公開入力型に候補型ごとの分岐を持ち込まない
  - v3 では選択した全生成対象の台本を確認・確定してからキュージョブを開始する。エンコード中に未確定台本を後から追加する最適化は次イテレーション候補とする
- [ ] S4-3. `services/shorts_queue.py` に `run_shorts_queue(video_id, clip_specs, settings=None, *, on_progress=None) -> ShortsQueueResult` を実装する
  - `clip_specs` は確定済みの区間セット + 確定済みテロップ台本のリスト
  - 内部で `build_short_from_segments()` を順番に呼び、`on_progress(current, total, message)` で進捗を報告する（`cut_and_concat` の `on_progress` シグネチャと合わせる）
  - 1 本の失敗が残りの生成を止めないこと（`services/batch.py` の失敗時継続方針を踏襲）
- [ ] S4-4. 「選んだ n 本をまとめて生成」ボタンで `jobs.start_job("shorts_queue", run_shorts_queue_job_target, ...)` を呼ぶ
- [ ] S4-5. 結果グリッド: 完成したショートを `st.video()` 付きカードで並べ、各カードに「動画を開く / タイトルコピー / 説明文コピー / タグコピー」（U2 の `ui/components/clipboard.py` を使う）を配置する
- [ ] S4-6. ユニットテスト
  - `run_shorts_queue` が `clip_specs` の件数ぶん `build_short_from_segments` を呼ぶこと
  - 1 本失敗しても残りが処理されること
  - 台本確定前はキューに積めないこと（UI 側の分岐を純粋関数化して検証）

**Done 条件:**

- [ ] `uv run pytest` が全件通る
- [ ] 台本確認を経ずに生成が始まらないことを確認済み
- [ ] タスク完了コミット済み

**見積もり目安:** 1.5 日

---

### S5: フェーズ S 受け入れ（実配信 1 本からショート複数本を通しで作る）

**目的:** フェーズ S 全体を実データで受け入れる。
**変更ファイル範囲:** なし（確認作業のみ。不具合が見つかった場合は該当タスクの範囲内で修正する）

**作業:**

- [ ] S5-1. 実配信 1 本で、ハイライト候補選定 → 複数区間選択 → テロップ台本確認・修正 → ジャンプカット連結ショート生成 → キュー量産で複数本、を通しで実行する
- [ ] S5-2. 生成物を目視確認する: テロップの可読性（縁取り・座布団・強調色）、フックタイトルの表示、ジャンプカットのつなぎ目（フリーズ・音ズレの有無）、出力が 1080x1920 であること（`ffprobe` で確認）
- [ ] S5-3. `docs/requirements-v3.md` の AC-23〜AC-26 を確認し、結果を報告する
- [ ] S5-4. v2 未完了タスク（§4）のうち S5 に吸収した項目（V5-7 / V6-7 実機確認）をここで消化する

**Done 条件:**

- [ ] AC-23〜AC-26 が満たされている（未達は明示的に申し送りする）
- [ ] つなぎ目のフリーズ・音ズレが無いことを目視確認済み
- [ ] `uv run pytest` が全件通る
- [ ] フェーズ完了コミット済み

**見積もり目安:** 1 日

---

### P0: テストアップロード検証

**目的:** 未審査の YouTube API プロジェクトからのアップロードが非公開ロックされ、`publishAt` が効かない可能性がある仕様を実機で確認する。**P1 の本実装より前に、最小限のアップロード経路で 1 本テストする。**
**変更ファイル範囲:**
- `src/yt_live_kit/services/youtube_api.py`（`upload_video()` の最小実装を追加。P1 で拡張・productionize する前提の土台とする）
- `tests/test_youtube_api.py`（追記。ユニットテストは `googleapiclient` をモックし、実アップロードは行わない）

**作業:**

- [ ] P0-1. `services/youtube_api.py` に `upload_video(video_path, title, description, tags, settings, *, privacy_status="private", publish_at=None) -> str`（動画 ID を返す）を実装する。`googleapiclient.http.MediaFileUpload` を使い、`videos.insert(part="snippet,status", body=..., media_body=...)` を呼ぶ
- [ ] P0-2. **実行内容・対象ファイル・タイトル・公開予定日時をユーザーに提示し、明示承認を得てから**、実際に 1 本、非公開・`publishAt` 指定でテストアップロードする（手動実施。自動テストでは検証できない。承認前は実行禁止）
- [ ] P0-3. YouTube Studio 上で、指定時刻より前に動画が非公開のままか、意図せず公開されないかを確認する。あわせて「非公開ロック」（未審査プロジェクトの制限）が掛かっているかを確認する
- [ ] P0-4. ロックが掛かっている場合、提出内容をユーザーに提示し、**別途明示承認を得てから** Google のコンプライアンス審査フォームを提出する。承認前は提出しない。**審査待ちの間も P1・P2 の実装は止めない**（審査結果が出るまでは非公開のまま検証を続ける）
- [ ] P0-5. 確認結果（ロックの有無、審査申請の有無と申請日）をタスク完了報告に記録する

**Done 条件:**

- [ ] 実アップロードと、必要な場合の審査フォーム提出について、各操作前にユーザーの明示承認を得た記録がある
- [ ] 実アップロード 1 本のテスト結果が記録されている
- [ ] ロックされていた場合、審査申請済みであることが記録されている
- [ ] `uv run pytest` が全件通る（ユニットテストはモックのみ）
- [ ] タスク完了コミット済み

**見積もり目安:** 0.5 日（+ 審査待ち日数は開発のブロッカーにしない）

---

### P1: アップロードサービス（`videos.insert` + `publishAt`）

**目的:** P0 の最小実装を、実運用に耐える形に拡張する。
**変更ファイル範囲:**
- `src/yt_live_kit/services/youtube_api.py`（`upload_video()` の拡張: バリデーション、エラーメッセージの日本語化、アップロード失敗時のリトライなし・明確なエラーで停止）
- `src/yt_live_kit/services/ffmpeg.py`（動画尺を取得する公開 `probe_duration()` の追加）
- `src/yt_live_kit/models/upload.py`（新規。`UploadResult`: `video_id`, `privacy_status`, `publish_at`, `uploaded_at`）
- `tests/test_youtube_api.py` / `tests/test_ffmpeg.py`（追記）

**作業:**

- [ ] P1-1. アップロード前チェック: ファイルの存在、拡張子、10〜180 秒の尺（`ffprobe` で確認、既存の `services/ffmpeg.py` のパターンを再利用）
- [ ] P1-2. `privacyStatus="private"` を既定にし、`publishAt` を渡す場合は ISO 8601 形式であることを検証する
- [ ] P1-3. HTTP エラー（`googleapiclient.errors.HttpError`）を日本語メッセージに変換する（クォータ超過・認証切れ・ファイルサイズ超過等、想定されるパターンごとに文言を用意する）
- [ ] P1-4. `jobs.start_job()` 経由で呼べるよう、`upload_job_target(*, video_path, title, description, tags, publish_at, settings, report) -> UploadResult` のラッパーを用意する
- [ ] P1-5. ユニットテスト
  - `videos.insert` 呼び出しパラメータの組み立て（`googleapiclient` をモック）
  - 尺バリデーション（9 秒 / 181 秒で拒否）
  - HTTP エラーの日本語変換

**Done 条件:**

- [ ] `uv run pytest` が全件通る
- [ ] 実アップロードを伴わないユニットテストのみで完結している
- [ ] タスク完了コミット済み

**見積もり目安:** 1 日

---

### P2: スケジュールポリシー + キューからの自動割り当て + 投稿確認 UI

**目的:** 「毎日 18:00 に 1 本」のようなポリシーを設定し、量産キューから予約枠を自動割り当てする。
**変更ファイル範囲:**
- `src/yt_live_kit/services/schedule.py`（新規）
- `src/yt_live_kit/ui/views/settings.py`（スケジュールポリシー編集欄。U4 で用意したプレースホルダを実装する）
- `src/yt_live_kit/ui/views/video_detail.py`（各ショートに「予約投稿」ボタン + `st.dialog` の投稿確認 UI を追加）
- `tests/test_schedule.py`（新規）
- `tests/test_ui_video_detail_page.py` / `tests/test_ui_settings_page.py`（追記）

**作業:**

- [ ] P2-1. `SchedulePolicy`（`daily_time: str`, `interval_days: int`, `timezone: str`）を pydantic で定義し、`data/_config/schedule_policy.json` に保存・読み込みする関数を実装する
- [ ] P2-2. 予約枠の管理: `data/_schedule/queue.json` に、割り当て済みの `(video_id, clip_id, publish_at)` を記録する
- [ ] P2-3. `assign_next_slot(policy, existing_queue, *, now=None) -> datetime` を実装する（既存の予約枠と重複しない直近の空き枠を返す純粋関数、テストしやすい形にする）
- [ ] P2-4. **クォータ上限のチェック**: `videos.insert` は Video Uploads 専用バケットで 1 回 = 1、既定 100 回/日（[`docs/requirements-v3.md`](./requirements-v3.md) NFR-12）。当日分の割り当て件数が 100 件に達している場合、翌日以降の枠に回す
- [ ] P2-5. 投稿確認 UI: `st.dialog` でタイトル・説明文・タグ・公開予定日時をプレビューし、確定すると `services.youtube_api.upload_video()` を `jobs.start_job()` 経由で呼ぶ。**概要欄反映（U5）と同じ「差分・内容プレビュー + 確認」の作法で統一する**
- [ ] P2-6. ユニットテスト
  - `assign_next_slot` の空き枠計算（重複回避、日をまたぐケース）
  - クォータ上限に達した場合に翌日へ回ること
  - ポリシーの保存・読み込み往復

**Done 条件:**

- [ ] `uv run pytest` が全件通る
- [ ] 1 日 100 本を超える割り当てが発生しないことをテストで確認済み
- [ ] タスク完了コミット済み

**見積もり目安:** 1.5 日

---

### P3: フェーズ P 受け入れ（予約投稿が実際に公開される）

**目的:** v3 全体を受け入れ、仕上げる。
**変更ファイル範囲:**
- `README.md`（チャンネル取り込み・ショート量産・予約投稿の使い方を追記）
- `src/yt_live_kit/__init__.py`（版数を `0.3.0` に更新）
- `pyproject.toml`（版数を `0.3.0` に更新）
- `docs/execution-plan-v3.md`（進捗チェック・マイルストーンの最終更新。各タスクの局所的な進捗更新は §3.4 の共通例外で許可済み）

**作業:**

- [ ] P3-1. 対象ファイル・タイトル・説明文・タグ・公開予定日時をユーザーに提示し、**明示承認を得てから**近い将来の時刻を指定して 1 本を予約投稿し、指定時刻に実際に公開されることを確認する（手動実施。自動テストでは検証できない。承認前は実行禁止）
- [ ] P3-2. `docs/requirements-v3.md` の AC-18〜AC-28 を総点検し、結果をチェックリスト形式で報告する
- [ ] P3-3. 実配信 1 本で、チャプター生成 → ショート複数本の生成までを通しで実行し、予約投稿は対象・投稿内容・公開予定日時をユーザーに提示して明示承認を得てから実行する（AC-28）
- [ ] P3-4. README を更新する（チャンネル取り込み、テロップ付きショートの作り方、キュー量産、予約投稿の設定と確認ダイアログ、YouTube API クォータの制約）
- [ ] P3-5. 版数を `0.3.0` に更新する
- [ ] P3-6. `docs/execution-plan-v3.md` の進捗チェック・マイルストーン表を最終更新する

**Done 条件:**

- [ ] 実予約投稿の操作前にユーザーの明示承認を得た記録がある
- [ ] AC-18〜AC-28 が確認済み（未達は明示的に次イテレーションへ移す）
- [ ] 予約投稿した動画が指定時刻に実際に公開されたことを確認済み
- [ ] `uv run pytest` が全件通る
- [ ] v1 / v2 の機能に回帰が無い
- [ ] v3 完了コミット済み

**見積もり目安:** 1 日

---

## 7. 出力ディレクトリ・ソースコード構成（v3 追加分）

### 7.1 ソースコード構成

```
src/yt_live_kit/ui/
  app.py                    # st.navigation によるページ登録のみ
  state.py                  # session_state ヘルパー（v2 から継続）
  views/                    ← v3（U0 で pages/ から改名）
    library.py              # U1: ライブラリページ
    video_detail.py         # U2: 動画詳細ページ
    intake.py               # U3: 取り込みページ（旧 channel.py + run.py の統合）
    settings.py             # U4: 設定ページ
    _local_settings.py      # U1 で新設・U3 で追記: アーカイブ状態・チャンネル既定ハンドルの軽量永続化（services を使わない例外）
    highlights.py           # v2 から継続。呼び出し元が video_detail.py に変わる
    shorts.py                # v2 から継続。build_short_from_segments 呼び出しは S4 で追加
    # history.py は U5 で削除。行アクションは video_detail.py へ統合
  components/
    clipboard.py             ← v3（U2 で results.py から移設）
    storage_manager.py       ← v3（U5。旧 history.py のストレージ管理を設定ページから利用）
    results.py                # v2 から継続。クリップボード関数は clipboard.py に委譲
    status_bar.py             # v2 から継続。U5 で案内先をライブラリへ更新
```

### 7.2 出力データ構成（v2 の構成に v3 分を追加）

```
data/
├── _config/
│   ├── description_template.txt    # v2
│   ├── archived_videos.json        ← v3（U1。ui/views/_local_settings.py が読み書き。video_id の配列）
│   ├── channel_handle.txt          ← v3（U3。ui/views/_local_settings.py が読み書き）
│   ├── schedule_policy.json        ← v3（P2）
│   ├── client_secret.json          # v2
│   └── youtube_token.json          # v2
├── _schedule/                      ← v3（P2）
│   └── queue.json                  # 割り当て済みの予約投稿枠
└── {video_id}/
    ├── ...（v1/v2 と同じ）
    └── shorts/
        ├── telop/                  ← v3（S1）
        │   └── telop_{clip_id}.json    # テロップ台本 + フック文言 + タイトル案 + 説明文 + タグ
        ├── segments/                ← v3（S3。中間ファイル、既定で削除）
        │   └── {clip_id}/
        │       ├── seg_001.mp4 ...
        │       └── concat.mp4
        ├── subtitles/
        │   ├── short_{start}_{end}.ass  # v2。S2 でプリセット・強調色に対応拡張
        │   └── short_{clip_id}.ass      ← v3（S3。連結後タイムラインの通常字幕 + Hook）
        └── output/
            ├── short_{start}_{end}.mp4  # 単一区間（v2 から継続）
            ├── short_{clip_id}.mp4      ← v3（S3。複数区間を連結したショート）
            └── {正式出力stem}.ffmpeg.log ← v3（S3。最終焼き込みコマンドログ）
```

---

## 8. 推奨実装順序（依存関係）

```
PLAN0 要件・計画の確定
 └─ U0 UI 骨格の物理的な作り替え     ★以降のページ追加の前提
     ├─ U1 ライブラリページ
     ├─ U2 動画詳細ページ            ★S フェーズの土台
     ├─ U3 取り込みページ
     ├─ U4 設定ページ
     └─ U5 正式 4 画面 IA + ストレージ管理移設 + 概要欄差分プレビュー + フェーズ U 受け入れ
         └─ S1 テロップ台本 + メタデータ生成
             ├─ S2 ASS テロップスタイルプリセット
             └─ S3 ジャンプカット連結ショート生成（S1/S2 の両方に依存）
                 └─ S4 キュー量産 UI
                     └─ S5 フェーズ S 受け入れ
                         └─ P0 テストアップロード検証
                             └─ P1 アップロードサービス
                                 └─ P2 スケジュールポリシー + 自動割り当て
                                     └─ P3 フェーズ P 受け入れ（v3 完了）
```

**順序の根拠:**

| 判断 | 理由 |
|------|------|
| U0 を最優先 | サイドバー事故を残したまま機能を積むと、あとで UI 全体を作り直す二度手間になる |
| U2 を U フェーズの中核に置く | フェーズ S の全機能（テロップ確認・キュー量産・予約投稿ボタン）はこのページに積まれる。土台が粗いと S フェーズが総崩れになる |
| S1・S2 を S3 より先に | S3（連結ショート生成）はテロップ台本（S1）とスタイルプリセット（S2）の両方を利用する |
| P0 を P フェーズの最初に固定する | 非公開ロックの有無は実装方針（リトライ・エラーメッセージ設計）に影響するため、本実装（P1）より先に確認する |

---

## 9. 全体スケジュール目安

| タスク | 見積もり |
|--------|----------|
| PLAN0 要件・計画の確定 | 済み |
| U0 UI 骨格の物理的な作り替え | 0.5 日 |
| U1 ライブラリページ | 1 日 |
| U2 動画詳細ページ | 2 日 |
| U3 取り込みページ | 1 日 |
| U4 設定ページ | 0.5 日 |
| U5 正式 IA・ストレージ移設・概要欄差分プレビュー + フェーズ受け入れ | 1.5 日 |
| S1 テロップ台本 + メタデータ生成 | 1.5 日 |
| S2 ASS テロップスタイルプリセット | 1 日 |
| S3 ジャンプカット連結ショート生成 | 2 日 |
| S4 キュー量産 UI | 1.5 日 |
| S5 フェーズ S 受け入れ | 1 日 |
| P0 テストアップロード検証 | 0.5 日 |
| P1 アップロードサービス | 1 日 |
| P2 スケジュールポリシー + 自動割り当て | 1.5 日 |
| P3 フェーズ P 受け入れ | 1 日 |
| **合計** | **約 17.5 日**（1 人、AI 支援あり。P0 の審査待ち日数は含まない） |

**段階リリース案:**

| リリース | 含むタスク | 累計 | 内容 |
|----------|-------------|------|------|
| **0.2.1** | U0〜U5 | 6.5 日 | 迷わない UI に生まれ変わる |
| **0.2.2** | + S1〜S5 | 13.5 日 | テロップ付きショートが量産できる |
| **0.3.0** | + P0〜P3 | 17.5 日 | 予約投稿まで含む v3 完了 |

---

## 10. マイルストーン定義

| マイルストーン | 判定 |
|----------------|------|
| **M11: サイドバーの事故導線が消える** | U0 完了。サイドバーに内部モジュール名が出ない |
| **M12: 動画軸で迷わず作業できる新 IA が揃う** | U5 完了。公開 3 画面 + 非表示詳細の 4 画面構成が完成し、設定のストレージ管理、ステッパー、確認ダイアログ、差分プレビューが動く |
| **M13: テロップ付きショートが量産できる** | S5 完了。複数区間を連結したショートがテロップ・フックタイトル付きで複数本まとめて作れる |
| **M14: 予約投稿が実際に公開される（v3 完了）** | P3 完了。AC-18〜AC-28 充足、実際の予約公開を確認済み |

---

## 11. テスト計画（フェーズ横断）

| 種類 | 対象 | タイミング |
|------|------|------------|
| ユニット | ステッパー計算、`_local_settings` の永続化、テロップ台本バリデータ、累積タイムオフセット計算、`assign_next_slot`、アップロードパラメータ組み立て | 各タスク内 |
| 回帰 | v1 / v2 の既存テスト（`tests/` 全件）が通ること | 各タスク末 |
| 実機結合 | 公開アーカイブ 2 本（V7-2 を U5 / S5 で同じ 2 本を通して確認）、実アップロード 1 本（P0 / P3） | U5 / S5 / P0 / P3 |
| 目視確認 | サイドバー導線（U0）、ステッパー・確認ダイアログ（U5）、テロップ可読性・つなぎ目（S5）、YouTube 上の実際の公開挙動（P3） | 各フェーズ受け入れ |
| UI 受け入れ | README 手順どおりの全機能操作（P3） | P3 |

**方針:**

- yt-dlp / ffmpeg / Codex CLI / `googleapiclient` の実行は**すべてモックする**（`subprocess.run` および `googleapiclient` のクライアントをパッチ）
- 動画を実際に生成・アップロードするテストは CI 必須にしない（手動・任意）
- **v1 / v2 の既存テストを 1 件も壊さないこと。** 壊れた場合は後方互換の設計ミスとして扱う

---

## 12. リスクと計画上の対策

| リスク | 計画上の扱い |
|--------|--------------|
| フェーズ U で UI を作り替えている最中に、v2 機能が回帰する | 各タスクで `uv run pytest` を必須化し、`services/` を原則変更しないことで回帰の原因を UI 層に限定する。U3 の例外は `services/batch.py` の引数追加・全呼び出し伝播・両方 `False` の入力検証に限定し、`tests/test_batch.py` で後方互換・伝播・入力検証を固定する |
| 非公開ロックにより予約投稿が機能しない | P0 を P フェーズの最初に固定し、審査申請の要否を早期に判断する。審査待ちでも P1/P2 の実装は止めない |
| YouTube Data API のクォータ超過 | P2 の `assign_next_slot` で Video Uploads 専用バケットの既定 1 日 100 本上限を機械的に守る（NFR-12） |
| ジャンプカット連結で累積オフセット計算を誤り、字幕がずれる | S3 のユニットテストで 3 区間以上・区間間隔ありのケースを必須検証項目にする |
| Codex CLI の応答が不安定（テロップ台本） | S1 は例外を送出し、S4 等の呼び出し側が生成対象単位で局所捕捉する。失敗時は既存の同一 telop と他の成果物（チャプター・候補・ハイライト）を変更しない |
| テロップの自動生成品質が低く、そのまま焼き込むと粗が目立つ | S1 で必ず人の確認・修正ステップを挟む（自動焼き込みにしない） |
| 180 秒を超える区間選択でユーザーが詰まる | S3 のエラーメッセージに具体的な対処を含め、S4 の UI で分割・短縮を誘導する |

---

## 13. 本計画のスコープ外（実装しない）

[`docs/requirements-v3.md`](./requirements-v3.md) §2 の改訂を反映した上で、以下は引き続き実装しない。

- **BGM・効果音の付与**
- **ズーム・トランジション・エフェクト**
- 全本 Whisper 再文字起こし
- YouTube 概要欄・動画への **即時** 公開投稿（`publishAt` による予約投稿のみ）
- Cookie 認証による限定公開・メンバー限定動画対応
- Web UI の外部公開
- ジョブのキューイング（同時実行は 1 件まで。キュー量産も内部的には単一ジョブの順次処理）
- 複数チャンネルの同時運用（チャンネル既定値は 1 件のみ）

**次イテレーション（v4）候補:**

- 複数チャンネルの管理・切り替え
- 投稿後のパフォーマンス（再生数等）のダッシュボード表示
- YouTube Data API クォータの増枠申請後の量産上限緩和

---

## 14. 直近の次アクション

1. **U0 に着手する**（2026-08-02 予定）。`ui/pages/` → `ui/views/` のリネームと `st.navigation` 導入から始める
2. U0 完了後、U1〜U4 は依存関係が緩いため、必要に応じて着手順を前後させてよい（ただし本計画は単一ワーカー運用を想定しており、並列化は行わない）
3. フェーズ U 完了（U5）後、S1 着手前に `docs/requirements-v3.md` の AC-18〜22 が満たされていることを確認する

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-01 | S3 着手前監査・計画レビューを反映。共通整数 ms 正規化 API、入力順、二重検証境界、累積字幕、VTT / Hook fallback、全入力 preflight、進捗契約、force_style 分離、安全な出力名、固定ログ名、atomic replace、中間物 cleanup、既存出力保護を固定 |
| 2026-08-01 | S2 着手前監査・計画レビューを反映。`TimedCue.emphasis`、ASS プリセットの完全な型・色導出、既定出力互換、フック固定時間、安全化順序、同一 ASS 統合、S3 への preset 伝播と `force_style` 分離を固定 |
| 2026-08-01 | S1 着手前監査を反映。テロップ JSON の絶対秒スキーマ、公開シグネチャ、検証結果型、プロンプト保存先、`make_clip_id()` のミリ秒丸め、VTT parser の公開互換、softfail 境界を固定 |
| 2026-08-01 | PLAN0-7: U5 着手前監査を反映。正式 IA を公開 3 画面 + 非表示詳細に固定し、旧処理済み一覧の削除、ストレージ管理の設定画面移設、概要欄更新経路・成功記録・安全な受け入れ境界を明確化 |
| 2026-08-01 | PLAN0-6: 実装前監査の指摘を反映。YouTube granular quota を 100 uploads/day に更新し、進捗更新権限・概要欄完了記録・`clip_id`・S4 確認境界・P1 ffprobe 範囲を明確化 |
| 2026-08-01 | v3 実行計画の初版作成。PLAN0、U0〜U5、S1〜S5、P0〜P3 を定義。v2 未完了タスクの仕分けを実施 |
