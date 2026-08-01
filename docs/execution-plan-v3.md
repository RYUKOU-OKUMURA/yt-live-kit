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
| S3 | ジャンプカット連結ショート生成（services 拡張） | [x] 完了 |
| S4 | キュー量産 UI + 台本確認フロー | [x] 完了 |
| S5 | フェーズ S 受け入れ（実配信 1 本からショート複数本を通しで作る） | [x] 完了 |
| P0 | 安全な実機 upload probe（P1 / P2 完了後） | [x] 完了 |
| P1 | 安全なアップロードサービス | [x] 完了 |
| P2 | スケジュールポリシー + 原子的な予約確定 + 投稿確認 UI | [x] 完了 |
| P3 | フェーズ P 受け入れ（予約投稿が実際に公開される） | [x] 完了 |
| P4 | ショート概要欄の定型リンク差し込み（v3 追加要件） | [x] 完了 |
| S6 | 切り抜き候補からのショート用サブ区間提案（v3 追加要件） | [x] 完了 |
| S7 | FFmpeg 字幕フィルタの環境検査と復旧（ホットフィックス） | [x] 完了 |
| S8 | 区間内容の可視化 + プレビュー幅修正（v3.2・最優先） | [x] 完了 |
| U6 | ショート生産ライン UI（v3.2 改訂: 作業選択型 IA + 工程 UI） | [~] 進行中 |
| P5 | 投稿枠の複数化 + ライン既定値の設定化（v3.2） | [ ] 未着手 |
| U7 | 概要欄反映の最新性判定（保留: 優先度③のため v4 送り。候補引き継ぎは U6 に統合） | [保留] |
| U8 | エラー通知の構造化とページ先頭の整理 | [ ] 未着手 |
| S9 | ローカル Whisper による字幕精度の底上げ（要件改訂が前提） | [ ] 未着手 |

**状態の書き方:** `[ ] 未着手` / `[~] 進行中` / `[x] 完了`

**マイルストーン:**

| ID | 内容 | 状態 |
|----|------|------|
| M11 | サイドバーの事故導線が消える（U0 完了） | [x] |
| M12 | 動画軸で迷わず作業できる新 IA が揃う（U5 完了） | [x] |
| M13 | テロップ付きショートが量産できる（S5 完了） | [x] |
| M14 | 予約投稿が実際に公開される（v3 完了・P3 完了） | [x] |
| M15 | 毎日 3 本のショート生産ラインが確立する（S8 → U6 → P5 完了、実機でライン 3 周） | [ ] |

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
| 処理層 | `services/` 共通。CLI は補助 | 変更なし。フェーズ U 中は原則 `services/` を変更しない。U3 の入力伝播と U6 のライン状態永続化に限る最小例外は §3.2 参照 |
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
  - **U3 の最小例外:** 既存バッチ処理で「チャプターを作る」「切り抜き候補を出す」の選択を実効させるため、`services/batch.py` の `run_batch()` / `run_batch_job_target()` へ `do_chapters` / `do_clips` 引数を追加し、`run_batch_job_target()` → `run_batch()` → 各 URL の `pipeline.run()` の全呼び出しへ伝播し、両方 `False` を service 入力でも拒否する変更だけは許可する。既定値はどちらも `True` とし、既存 CLI / UI との後方互換を維持する。この例外をその他の `services/` 変更に拡大しない
  - **U6 のみの最小例外:** 工程通過と人確認は生成・投稿可否を決める安全状態であり、UI 固有の表示状態ではない。`services/shorts_line.py` を新設し、ライン状態の型・fingerprint・atomic 保存・fail closed 読み込み・工程遷移判定だけを置く。既存生成・投稿 service の実処理は変更しない
  - 上記以外の **UI 固有の軽量な状態**（例: チャンネル既定ハンドル、ライブラリのアーカイブ表示切り替え）は、新しい `services/` モジュールを作らず UI 層内で完結させる。具体的な方針は U1・U3 のタスク詳細に明記する
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
| **P0** | 安全な実機 upload probe | P1 / P2 の本番経路で非公開ロックと processing を確認 | AC-27 の実機前提 |
| **P1** | 安全なアップロードサービス | private 固定、resumable upload、永続 operation、試行台帳、reconciliation | FR-27, NFR-12, AC-27 |
| **P2** | スケジュールポリシー + 原子的な予約確定 + 投稿確認 UI | 予約枠の自動割り当て、確認後再検証、ジョブ / UI 復元 | FR-28, AC-27 |
| **P3** | フェーズ P 受け入れ | 実際の予約公開確認、README・版数更新 | AC-27, AC-28 |
| **P4** | ショート概要欄の定型リンク差し込み | ショート専用テンプレート、開始秒付き元配信 URL、preview 前合成 | FR-29, AC-29 |
| **S6** | 切り抜き候補からのショート用サブ区間提案 | 親区間内カットプラン生成、採否・境界調整 UI、FR-25 連結への受け渡し | FR-30, AC-30 |
| **S7** | FFmpeg 字幕フィルタの環境検査と復旧 | libass 対応 FFmpeg の明示設定、生成前 capability 検査、設定画面の診断表示 | FR-24, FR-25, AC-24, AC-25 |
| **S8** | 区間内容の可視化 + プレビュー幅修正 | 区間ごとの文字起こし表示（境界追従）、`st.video` 幅制限 4 箇所 | FR-34, AC-34 |
| **U6** | ショート生産ライン UI | 3 ワークスペース + 左パネル + 6 工程 + 人確認ゲート + 永続ライン状態 | FR-17 v3.2, FR-31, FR-33, AC-31, AC-35 |
| **P5** | 投稿枠の複数化 + ライン既定値の設定化 | `daily_times` リスト、設定ページの枠・既定値編集 | FR-28 v3.2, FR-20 v3.2, AC-36 |
| **U7** | （保留）概要欄反映の最新性判定 | 優先度③（保守のみ）のため v4 送り。候補引き継ぎ部分は U6 の工程接続に統合済み | FR-21 v3.1, AC-32 |
| **U8** | エラー通知の構造化とページ先頭の整理 | 動画 ID 別の構造化エラー、要約表示、技術ログの詳細領域集約 | FR-32, AC-33 |
| **S9** | ローカル Whisper による字幕精度の底上げ | 方式選定（whisper.cpp 優先）、要件改訂、精度実測 | 要件改訂後に確定 |

各タスクは「実装 → 単体確認 → Done 条件チェック → **タスク完了コミット**」で閉じる。フェーズ末（U5 / S5 / P3）は「フェーズ受け入れ」も兼ねる。P4 / S6 は v3 受け入れ（P3）完了後に追加された要件であり、P3 の証跡・版数には手を入れない。

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

> **v3.1 注記（2026-08-01）:** 本タスクで実装した「パイプライン順の全セクション + 5 段ステッパー」構成は、FR-17 v3.1 改訂により U6 で作業選択型 IA へ再構成される。以下のチェックは U2 完了時点の記録として維持し、書き換えない。現行の設計正本は U6 を参照。

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

**背景:** U0 移設後の旧 `ui/views/history.py` にあった `_render_description_preview` は「反映後」のマージ済みテキストのみを表示し、`st.dialog` を通らず、成功時に概要欄反映済み ID も記録しなかった。このページが公開ナビゲーションに残っていたため、動画詳細だけを改善しても差分確認を迂回できた。U5 で旧ページを削除して更新経路を動画詳細へ一本化した。一方、旧ページ固有のストレージ管理は v1 / v2 の AC-15 を維持するため、共通部品へ分離して設定ページへ移設した。フェーズ U 完了時の 4 画面は、**公開 3 画面（ライブラリ / 取り込み / 設定）+ 非表示の動画詳細 1 画面**とする。

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

- [x] S3-1. `build_short_from_segments(video_id, segments: list[tuple[float, float]], settings=None, *, layout="blur", telop_script: TelopScriptDocument | None = None, hook_text: str | None = None, preset="default", hook_preset="hook", output_name: str | None = None, ffmpeg_path=FFMPEG_DEFAULT, on_progress: ShortsProgressCallback = None, keep_intermediate=False) -> ShortResult` を実装する。`ShortsProgressCallback` は `Callable[[int, int, str], None] | None` とし、`total = len(segments) + 3`、各区間を `i`、連結を `n + 1`、字幕準備を `n + 2`、焼き込みを `n + 3` として通知する
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
- [x] S3-2. `subtitle_burn.build_concatenated_subtitle(video_id: str, segments: Sequence[tuple[float, float]], settings: Settings | None = None, *, telop_script: TelopScriptDocument | None = None, hook_text: str | None = None, preset: str = "default", hook_preset: str = "hook") -> Path` を実装する
  - 関数単独で呼ばれた場合も、関数内 import した `normalize_seconds_to_milliseconds()` / `normalize_segment_bounds()` / `make_clip_id()` / `validate_telop_script()` へ一本化し、区間正規化と telop 再検証を必ず行う。S3-1 との二重検証は公開境界の安全性のため許容し、呼び出し側から `clip_id` や正規化結果を受け取る別経路は作らない
  - 明示 `hook_text` はこの公開境界自身でも検証し、strip 後の空文字と半角山カッコを拒否する。これにより `telop_script=None` の直呼びでも固定出力ルールを迂回できないようにする
  - 出力先は `data/{video_id}/shorts/subtitles/short_{clip_id}.ass`。上記の関数内 import により `telop.py` → `subtitle_burn.py` の既存 import との循環を避ける
  - 各区間のカットは元動画のタイムコードを持つため、連結後の行時刻を整数ミリ秒で `先行区間の累積 ms + 行の絶対 ms - 元区間開始 ms` と計算する。防御的に各相対時刻を `0..区間尺 ms` へ clip し、clip 後に終了が開始以下なら日本語エラーにする
  - `telop_script` がある場合は関数内再検証で得た正規化済み document を使う。`hook_text` の明示値を優先し、`None` なら document の `hook_text` を使う
  - `telop_script` が無い場合（S1 を経ずに生成する場合）は `subtitles/ja.vtt` を 1 回だけ読み、VTT 由来の字幕（既存の `parse_vtt_with_end` / `filter_cues_for_segment`）へフォールバックする。各 filter 結果の区間相対 cue を共通 helper で整数ミリ秒へ正規化し、先行区間の累積 ms を加えて連結 timeline へ変換する。VTT 不在は日本語エラー、個々の区間で cue が 0 件なのは許容し、VTT が存在して `hook_text` があれば Hook 単独 ASS も生成できる
  - 通常字幕と `hook_text`、`preset`、`hook_preset` は S2 の同一 ASS API へ一度に渡し、ffmpeg の字幕焼き込みも 1 回だけ行う。フック用の別 ASS を後段で重ねず、選択プリセットを全呼び出し段で欠落させない
- [x] S3-3. 尺バリデーション: 正規化済み整数ミリ秒の合計で 10,000 ms・180,000 ms を許可し、9,000 ms は `MIN_DURATION_SEC` 未満、181,000 ms は `MAX_DURATION_SEC` 超として `ShortsError` にする。180 秒超の**エラーメッセージには「区間を減らすか短くしてください」という具体的な対処を含める**（UI 側の分割・短縮誘導は S4 で実装する）
- [x] S3-4. ユニットテスト
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

- [x] `uv run pytest` が全件通る
- [x] 出力パスの命名規則が S1（`telop_{clip_id}.json`）と揃っている
- [x] タスク完了コミット済み

**見積もり目安:** 2 日

---

### S4: キュー量産 UI + 台本確認フロー

**目的:** 複数のショート候補をまとめて生成できるようにし、生成前に必ず 1 本ずつテロップ台本を確認させる。
**変更ファイル範囲:**
- `src/yt_live_kit/services/shorts_queue.py`（新規。確定済み snapshot の型、順次生成、softfail、manifest 永続化 / 読み込みを担当）
- `src/yt_live_kit/services/telop.py`（人が修正した台本を再検証して atomic 保存する `save_confirmed_telop_script()` を追加）
- `src/yt_live_kit/services/jobs.py`（`JobKind` のコメントに `"shorts_queue"` を追加するのみ。ロジックは変更しない）
- `src/yt_live_kit/ui/views/shorts.py`（既存の候補読み込み・単本ショート UI と共存させ、キュー部品を呼ぶ）
- `src/yt_live_kit/ui/components/shorts_queue.py`（新規。選択、台本 editor / 確定、上書き確認 dialog、結果グリッドの表示のみ。検証・保存・キュー実行ロジックは services へ置く）
- `src/yt_live_kit/ui/views/video_detail.py`（既存 `render_shorts_section()` との呼び出し契約が変わる場合のみ）
- `tests/test_shorts_queue.py` / `tests/test_telop.py` / `tests/test_ui_shorts_queue.py`（新規または追記）
- `tests/test_ui_shorts.py` / `tests/test_ui_video_detail_page.py`（既存呼び出し契約が変わる場合のみ追記）

**作業:**

- [x] S4-1. 候補選択と不変 snapshot: 候補ソース（ハイライト / 切り抜き候補のいずれか一方）は snapshot form の外側・前段で選び、変更時は即 rerun して downstream の draft / 確定を失効させる。その後の単一 `st.form` でチェックボックス、生成モード、layout、通常 preset、Hook preset を一括確定する。候補は選択したソース文書の表示順を維持し、選択済み区間の順序もチェックされた候補をその表示順で走査した順とする（クリック時刻を順序として扱わない）
  - 生成モードは `st.segmented_control` 等のコンパクトなネイティブ widget で「個別」と「連結」を切り替える。個別は選択 n 区間から n 本、連結は選択 n 区間から 1 本の生成対象を入力順で作る
  - 候補変換・target 構築は `services/shorts_queue.py` の純粋関数に置く。`normalize_queue_candidates()` は `ClipCandidate` を既存 `clip_to_highlight_segment()` と同等に `HighlightSegment` へ正規化し、`build_shorts_queue_targets()` は個別 / 連結の target を表示順のまま構築する。S1 の公開入力型に候補型ごとの分岐を持ち込まない。異なる候補ソースを同時混在させず、同一 `clip_id` / 出力先になる生成対象の衝突は開始前に日本語で拒否する。暗黙の sort / dedupe / 自動 suffix は行わない
  - snapshot の区間は S3 の公開整数 ms API で Codex 呼び出し前に検証する。10,000 ms 未満は選択変更を案内し、180,000 ms 超は「区間を分割するか減らす、または短くする」と案内し、Codex / ジョブ / ffmpeg を呼ばない
- [x] S4-2. 生成前の台本確認と保存: snapshot から作った各生成対象は、対象カードの「台本を生成」または失敗後の「再試行」を submit した時だけ `generate_telop_script()` を 1 回呼ぶ。通常 rerun や snapshot form の確定だけでは Codex を呼ばず、生成済み draft は session state から再表示する。各台本のフック、テロップ本文、タイトル案、説明文、タグを対象ごとの editor `st.form` で表示・修正できるようにする
  - frozen dataclass `ConfirmedTelopScriptResult(path: Path, document: TelopScriptDocument)` を追加する。`save_confirmed_telop_script(video_id, segments, document, settings=None) -> ConfirmedTelopScriptResult` は `validate_telop_script(..., segments=segments)` を再実行し、正規化済み document だけを S1 と同じ `shorts/telop/telop_{clip_id}.json` へ atomic 保存し、保存 path とその正規化済み document を同時に返す。不正時は既存 JSON を維持し、日本語 `TelopError` にする
  - editor の「台本を確定」が成功した対象だけを不変の確定済み snapshot に変換する。確定後は editor widget を非表示にして読み取り専用サマリと「修正する」のみを表示し、未 submit のフォーム値を無視して開始できる経路を作らない
  - `make_shorts_queue_fingerprint()` は `video_id`、候補ソース、選択した元候補の全内容、表示順、正規化後の全 segments、生成モード、layout、通常 preset、Hook preset を canonical JSON 化して SHA-256 を返す。いずれかが変わったら `is_shorts_queue_snapshot_current()` により既存の draft / 確定を全て失効させる。`can_start_shorts_queue(..., busy: bool)` は外部状態を引数で受け、fingerprint 一致、全対象確定、衝突なし、尺、busy を副作用なく判定し、全条件成立時だけ開始 CTA を有効にする
  - S1 の失敗は生成対象単位で捕捉し、そのカードだけ再試行可能にする。失敗対象は確定 / キュー追加不可とし、他対象の draft / 確定、既存 telop JSON、他成果物を維持する
- [x] S4-3. `services/shorts_queue.py` に pure な候補正規化、個別 / 連結 target 構築、衝突 / 尺検証、fingerprint、失効 / 開始可否、直列化と、次の frozen dataclass / 例外を実装する。UI は widget・session state・dialog・表示と service 呼び出しだけを担当し、候補変換や snapshot 判定を再実装しない
  - frozen `ShortsQueueSegmentSpec`: `id: str`、`title: str`、`start_ms: int`、`end_ms: int`、`reason: str` の primitive 値だけを持つ。`ShortsQueueClipSpec`: `target_id: str`、入力順の `segments: tuple[ShortsQueueSegmentSpec, ...]`、`telop_document_json: str`、`layout: str`、`preset: str`、`hook_preset: str`、`output_name: str` を持つ。台本は `TelopScriptDocument.model_dump(mode="json")` を `ensure_ascii=False, sort_keys=True, separators=(",", ":")` で canonical JSON 化して保持し、mutable な Pydantic model / list / dict を snapshot 内に保持しない
  - `ShortsQueueClipSpec.to_dict()` は tuple を JSON 配列へ、canonical 台本 JSON を JSON object へ変換する。`from_dict()` は未知 / 欠落 field、型、区間、台本、出力名を再検証し、台本を `TelopScriptDocument.model_validate()` して再 canonical 化してから frozen spec を返す。`output_name` は target の決定的 `clip_id` から必ず `short_{clip_id}.mp4` とし、自動 suffix を付けず、衝突は開始前に拒否する
  - frozen `ShortsQueueItemResult`: `target_id: str`、`status: Literal["succeeded", "failed"]`、`output_path: Path | None`、`log_path: Path | None`、`font_warning: str | None`、`title_candidates: tuple[str, ...]`、`description: str`、`tags: tuple[str, ...]`、`error: str | None` を持つ
  - frozen `ShortsQueueResult`: `video_id: str`、`job_id: str`、`status: Literal["running", "done"]`、`created_at: datetime`、`updated_at: datetime`、入力順の `clip_specs: tuple[ShortsQueueClipSpec, ...]` と `items: tuple[ShortsQueueItemResult, ...]`、`success_count: int`、`failure_count: int`、実行時専用の `manifest_path: Path` を持つ。item / result にも `to_dict()` / `from_dict()` を実装する。`manifest_path` は manifest JSON と `to_dict()` へ保存せず、`ShortsQueueResult.from_dict(data, *, manifest_path: Path)` の必須 keyword として loader が実際の読込元 path を注入する
  - `ShortsQueueError(ShortsError)`: 空入力や queue 全体を開始 / 継続できないエラー用。既存 jobs の日本語既知エラー経路に載せる
- [x] S4-4. `run_shorts_queue(video_id: str, clip_specs: Sequence[ShortsQueueClipSpec], settings: Settings | None = None, *, job_id: str, on_progress: ShortsQueueProgressCallback = None) -> ShortsQueueResult` を実装し、manifest の作成・更新をこの関数だけが所有する
  - 空 `clip_specs` は一件も処理せず `ShortsQueueError` にする。全 spec を再検証した後、`build_short_from_segments()` を入力順に 1 本ずつ呼び、台本、layout、preset、Hook preset、output name を欠落なく伝播する
  - `ShortsProgressCallback` の内部メッセージは対象の outer `current` を進めず表示し、対象の成功 / 失敗確定時に outer `on_progress(current, total, message)` を `1..len(clip_specs)` へ進める。失敗時も current を進め、残りを続行する
  - 1 本の `ShortsError` は item error として捕捉し、他対象の生成を止めない。全件失敗でも `ShortsQueueResult` と manifest を残し、ジョブを `done` として UI が各エラーを表示できるようにする。queue 全体の入力 / 永続化失敗だけを `ShortsQueueError` にする
  - manifest schema は `schema_version=1`、`video_id`、`job_id`、`status`、UTC ISO 8601 の `created_at` / `updated_at`、入力順の `clip_specs` / `items`、`success_count`、`failure_count` に固定する。item の出力 / ログ `Path` は `to_dict()` で文字列、spec 内の Pydantic 台本は `model_dump(mode="json")` 由来の JSON object にし、`from_dict()` で schema version / 型 / timestamp / path / model / count と items が specs の入力順部分列であることを再検証する。`manifest_path` field が JSON に混入した場合も未知 field として拒否し、開始時と各 item 確定後に一時ファイル + `replace` で atomic 保存する
- [x] S4-5. `run_shorts_queue_job_target(*, report, settings: Settings, job_id: str, video_id: str, clip_spec_dicts: list[dict[str, object]]) -> None` を実装し、`jobs.start_job("shorts_queue", run_shorts_queue_job_target, video_id=..., title=..., total=len(clip_specs), settings=..., clip_spec_dicts=...)` から単一ジョブを開始する
  - target は `ShortsQueueClipSpec.from_dict()` で frozen spec を再構築・再検証し、`report(current=..., total=..., message=...)` へブリッジする。manifest は作成せず `run_shorts_queue()` に一任する。`jobs.py` は target の戻り値を保存しないため、結果は manifest から再構築する
  - `data/{video_id}/shorts/queue/queue_{job_id}.json` を読む `load_shorts_queue_result(video_id, job_id, settings=None)` と `load_latest_shorts_queue_result(video_id, settings=None)` を公開する。latest は検証済み manifest の `(created_at, job_id)` 降順（同時刻は job ID の辞書順降順）で決め、壊れた manifest は日本語エラーへ変換する
  - UI は `is_busy(settings)` と確定済み snapshot を開始直前に再確認する。二重クリック / 同時ジョブは既存 `start_job()` の lock / `JobBusyError` でも防ぎ、既存正式 mp4 が 1 件でもある場合は不変の対象一覧を `st.dialog` に表示し、ダイアログ内の確定ボタンだけが `start_job()` を呼ぶ。キャンセル、確定前再描画、busy 時は開始しない
  - `start_job()` が返した job ID は即座に `st.session_state["shorts_queue_job_ids"][video_id]` の動画別 map へ保持し、現在の `video_id` に対応する ID の manifest だけを表示する。manifest がまだ作られていない間は「準備中」とし、旧 latest を表示しない。現在動画の key が無い場合だけ、その動画に限定して `load_latest_shorts_queue_result(video_id, ...)` へ fallback する。他動画の保持 job ID は参照せず、通常 rerun / 新規開始直後に旧結果へ戻らない
- [x] S4-6. 結果グリッド: session state の現在 video ID 用 job ID（その動画の key が無い場合だけ当該動画 latest fallback）に対応する manifest から入力順に再構築し、成功 item は `st.video(Path)` 付きのカード、失敗 item は対象名と日本語エラーのカードで表示する
  - mp4 の保存は、クリック時に file-like を返す引数なし callable を `st.download_button(data=callable, file_name=..., mime="video/mp4", on_click="ignore", width="stretch")` へ渡して遅延読み込みし、大きな動画を描画時に bytes 化しない。`use_container_width` は新規コードで使わない
  - 第 1 タイトル案を主タイトルとし、タイトル案全件、説明文、タグを表示する。コピーは `ui/components/clipboard.py` を再利用し、key は `job_id + target_id + 種類` で一意にする。タグのコピー形式はカンマ区切りに固定する
  - manifest の出力パスが消失・空ファイルの場合は日本語警告を表示し、`st.video` / download を呼ばない。`font_warning` は `st.warning` で表示する
- [x] S4-7. ユニットテスト
  - form 外の候補ソース変更と即 rerun / downstream 失効、ソース文書の表示順を選択順に固定、個別 n 本 / 連結 1 本の pure spec 構築、`ClipCandidate` 正規化、同一 clip ID / `short_{clip_id}.mp4` 衝突拒否、自動 suffix なし
  - 整数 ms の 10,000 / 180,000 ms 許容、9,999 / 180,001 ms の Codex 前拒否、180 秒超の分割・削減・短縮案内
  - `save_confirmed_telop_script()` の再検証、`ConfirmedTelopScriptResult` の path + 正規化 document、atomic 保存、失敗時の既存 JSON 維持、その返却 document の S3 伝播
  - 通常 rerun / snapshot submit で Codex 未呼び出し、明示生成 / 再試行 submit だけで 1 回呼び出し、draft 再利用。全確定前、S1 失敗対象あり、busy、fingerprint 不一致、未 submit editor が残る状態で `start_job()` 未呼び出し
  - fingerprint に video ID、元候補全内容、表示順、正規化全 segments、source / mode / layout / 両 preset が含まれ、各値変更で確定失効する。pure な失効 / 開始可否関数と UI 判定が一致し、確定後 editor は非表示
  - frozen segment tuple と canonical telop JSON の deep immutability、spec / result / manifest の `to_dict()` / `from_dict()` round-trip、mutable 元入力変更の非波及、不正型 / 不正 schema / 改変値の再検証拒否。manifest JSON / `to_dict()` に `manifest_path` が無く、loader が読込元 path を必須注入し、JSON 内の偽 `manifest_path` を拒否する
  - `start_job("shorts_queue", ...)` の kind、total、video ID、title、serializable spec kwargs、返却 job ID の動画別 session state map 保存、二重開始防止、既存 mp4 dialog の未確定 / 確定境界
  - 対象単位 S1 / S3 softfail、outer progress、全件失敗、`run_shorts_queue()` だけによる schema version / UTC timestamp / Path / Pydantic JSON を含む開始時・各 item 後の atomic manifest 更新、manifest round-trip / `(created_at, job_id)` latest tie-break / 壊れた JSON
  - 新 job の manifest 作成前は旧 latest を表示せず準備中となり、同一 session では現在 video ID の保持 job ID の結果だけ、その動画の key が無い場合だけ当該動画 latest を表示する。動画 A で job ID を保持したまま動画 B へ切り替えても A の manifest を表示せず B の latest へ fallback し、A へ戻ると A の保持 ID を再利用する
  - layout / preset / Hook preset / 確定済み台本 / 安全な output name の全段伝播、結果順序、font warning、出力欠損、`st.video`、callable download、コピー値 / key
  - Codex CLI、subprocess、ffmpeg はすべて mock し、実課金 API、実 Codex、実 ffmpeg をユニットテストで呼ばない

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] 台本確認を経ずに生成が始まらないことを確認済み
- [x] ジョブ終了後または UI 再起動後も manifest から成功 / 失敗結果を再表示できる
- [x] タスク完了コミット済み

**見積もり目安:** 2.5 日

---

### S5: フェーズ S 受け入れ（実配信 1 本からショート複数本を通しで作る）

**目的:** フェーズ S 全体を実データで受け入れる。
**変更ファイル範囲:** なし（確認作業のみ。不具合が見つかった場合は該当タスクの範囲内で修正する）

**作業:**

- [x] S5-1. 実配信 1 本で、ハイライト候補選定 → 複数区間選択 → テロップ台本確認・修正 → ジャンプカット連結ショート生成 → キュー量産で複数本、を通しで実行する
- [x] S5-2. 生成物を目視確認する: テロップの可読性（縁取り・座布団・強調色）、フックタイトルの表示、ジャンプカットのつなぎ目（フリーズ・音ズレの有無）、出力が 1080x1920 であること（`ffprobe` で確認）
- [x] S5-3. `docs/requirements-v3.md` の AC-23〜AC-26 を確認し、結果を報告する
- [x] S5-4. v2 未完了タスク（§4）のうち S5 に吸収した項目（V5-7 / V6-7 実機確認）をここで消化する

**受け入れ記録（2026-08-01）:**

- 対象は実配信 `IJvd6k6ZmUo`（No.119）。3 区間、合計 60 秒から、連結 1 本と個別 3 本を生成した
- 生成物はすべて H.264、1080x1920、AAC stereo、17.043〜60.040 秒。連結点 24 秒・41 秒前後のフレーム hash と映像 DTS・音声 PTS の連続性を確認した
- boxed / bold_outline の両 preset、冒頭 0〜2 秒のフック、強調色、Hiragino Sans の実フォント選択を確認し、豆腐・装飾崩れがないことを目視確認した
- テロップ本文を「テラよりルナが上でした」へ修正し、再生成した mp4 と成功ログで焼き込み反映を確認した
- 初回の実 ffmpeg 失敗 manifest、修正後の連結 1 成功、個別 3 成功を保持し、公開 loader と UI 再起動後のグリッド復元、動画再生・保存・メタデータ表示を実ブラウザで確認した
- 180 秒超の選択は Codex / ffmpeg 実行前に日本語の分割・削減・短縮案内で停止することを実ブラウザで確認した
- 独立レビューは APPROVE。標準テストは 608 passed / 2 skipped、実 ffmpeg opt-in 統合テストは 2 passed

**Done 条件:**

- [x] AC-23〜AC-26 が満たされている（未達は明示的に申し送りする）
- [x] つなぎ目のフリーズ・音ズレが無いことを目視確認済み
- [x] `uv run pytest` が全件通る
- [x] フェーズ完了コミット済み

**見積もり目安:** 1 日

---

### P0: 安全な実機 upload probe

**目的:** P1 / P2 で完成した本番安全契約をそのまま使い、未審査 API プロジェクトの private lock、processing、予約公開可否を実機で確認する。**P0 用の簡易 upload 経路は作らず、P1 / P2 のモック実装・全自動テスト完了後にだけ着手する。**
**変更ファイル範囲:**
- `docs/execution-plan-v3.md`（承認・operation ID・YouTube video ID・poll 結果・ロック判定・審査申請の事実だけを記録）
- 不具合が見つかった場合は P0 を完了扱いにせず、P1 / P2 の変更範囲へ戻して修正・レビューする

**作業:**

- [x] P0-1. P1 / P2 の Done 条件と全モックテストが完了し、実 API を自動テストが呼ばないことを確認する。実操作の承認待ちでも P1 / P2 の開発・レビュー・コミットを止めない
- [x] P0-2. `channels.list(mine=true)` の実チャンネル ID / 名称、対象ファイル、絶対パス、サイズ、尺、タイトル、説明文、タグ、timezone 付き予約日時と UTC `Z`、`privacyStatus=private`、`notifySubscribers=false`、Made for Kids / synthetic media の選択、Community Guidelines 同意、当日 attempt 数をユーザーに提示する。**private lock が非該当なら、この probe 動画も指定時刻に public となり得る**ことを明記し、その外部公開まで含む P0 専用の明示承認を得るまで確定しない
- [x] P0-3. 承認後も P2 の確認後再検証を通し、operation ID を発行した本番経路から 1 本だけ upload する。operation の `reserved → uploading → uploaded` または `needs_reconciliation` / `failed`、job ID、YouTube video ID、attempt 台帳を記録する
- [x] P0-4. `videos.list(part="status,processingDetails")` の bounded poll と YouTube Studio で processing 状態、指定時刻前の private、公開予約可否、private lock の有無を確認する。**private lock は probe 成功や予約投稿成功として扱わない**
- [x] P0-5. private lock がある場合、審査フォームの提出内容をユーザーに提示し、実 upload の承認とは別の**審査フォーム提出専用の明示承認**を得てから提出する。承認前は提出しない。審査待ちは P1 / P2 のブロッカーにしない
- [x] P0-6. 承認時刻、operation ID、YouTube video ID、upload / processing / schedule status、private lock、審査申請の有無と申請日を受け入れ証跡へ記録する。動画削除等の追加操作はこの承認に含めない

**P0 read-only 準備記録（2026-08-01 06:01 JST）:**

- P1 / P2 は独立レビュー APPROVE、`uv run pytest -q` は 820 passed / 2 skipped。自動テストによる実 API・実 upload は無い
- `channels.list(mine=true)` でチャンネル `AI整体師`（`UCVAkt5l6kD4igMdVoEGTGIg`）を確認した
- 対象候補は `/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/IJvd6k6ZmUo/shorts/output/short_b71e7cdb2b8d.mp4`、2,209,539 bytes、24.031995 秒
- タイトルは「同じ条件でゲームを作らせた結果」、説明文は「ソル、テラ、ルナに同じ要件定義と技術スタックでゲームを作らせたときの意外な感想。」、タグは「ゲーム制作、ベンチマーク、ソル、テラ、ルナ」
- policy は毎日 09:00、1 日間隔、`Asia/Tokyo`。準備時の候補 slot は 2026-08-01 09:00 JST / 2026-08-01 00:00 UTC、private 固定、notify false、LA 当日 attempt は 0 / 100
- 2026-08-01 07:12 JST、ユーザーから Made for Kids「いいえ」、synthetic media「いいえ」、Community Guidelines「確認済み」、上記動画の実 upload と指定時刻の外部公開可能性を含む P0 専用承認を取得した。動画削除と審査フォーム提出は承認範囲外
- 承認後の本番 preview 再検証で全項目、LA 当日 attempt 0 / 100、fingerprint `097707e51c7723bc0a0cc31a1aabd0834635cabd5e4c6418ee2606179e913ca3` が一致し、最低 10 分の lead を維持していることを確認した

**P0 実 upload・公開前確認記録（2026-08-01 07:13 JST）:**

- P2 の確認後再検証を通した同一の本番経路から 1 本だけ upload した。operation ID は `886c300a9a1142058f24e99249fe79ca`、job ID は `83eb9e997b3c400582472bdbd9e57888`、YouTube video ID は `4WYXdB5p0K0`、URL は `https://youtube.com/shorts/4WYXdB5p0K0`
- operation は `reserved → uploading → uploaded`、job は `done`。LA 当日 attempt は upload 前 0 / 100、upload 後 1 / 100 で、追加の `videos.insert` は実行していない
- `videos.list(part="status,processingDetails")` の bounded poll は 2026-07-31 22:13:26 UTC、22:13:36 UTC、22:13:46 UTC の 3 回で、`processing → processing → processing_succeeded`。最終 `uploadStatus=processed`、`privacyStatus=private`、`publishAt=2026-08-01T00:00:00Z`
- YouTube Studio でも Standard / HD processing 完了、公開設定「公開予約」、公開までは非公開、2026-08-01 09:00、GMT+0900 を確認した。Made for Kids は「いいえ」。保存・変更操作は行っていない
- 2026-08-01 07:30 JST、ユーザーから検証のため公開時刻を早める追加承認を取得した。10 分 lead を維持して 07:45 JST / 2026-07-31 22:45 UTC へ変更し、YouTube Studio の「すべての変更を保存しました」と API の `privacyStatus=private`、`processingStatus=succeeded`、`publishAt=2026-07-31T22:45:00Z` を照合した。operation の upload 時 snapshot は元の 09:00 を不変記録として保持し、公開 poll には実際の 07:45 を使う
- 予約時刻後の 2026-07-31 22:46:19 UTC、production の bounded publication poll は初回で `uploadStatus=processed`、`processingStatus=succeeded`、`privacyStatus=public`、classification `published` を返した。operation の poll history は processing 3 件 + publication 1 件の計 4 件
- YouTube Studio は公開設定「公開」、公開 URL `https://youtube.com/shorts/4WYXdB5p0K0` は動画プレーヤー、チャンネル `@ai.seitai`、タイトル「同じ条件でゲームを作らせた結果」を表示し、実視聴可能と確認した。operation の予約公開可否を `no_private_lock` として記録した
- private lock は非該当のため審査フォームは不要で、提出・申請日は無し。動画削除その他の追加操作も行っていない

**Done 条件:**

- [x] P1 / P2 の安全契約を迂回する P0 専用 upload コードが無い
- [x] 実 upload と審査フォーム提出について、対象を示した別々の明示承認記録がある（審査不要ならその判定記録がある）
- [x] operation / attempt / poll の実結果と private lock 判定が記録されている
- [x] 4xx / 結果不明が発生した場合に新しい `videos.insert` が自動実行されず、`needs_reconciliation` のまま手動照合へ止まる
- [x] `uv run pytest` が全件通る（実 API を呼ぶテストは含めない）
- [x] タスク完了コミット済み

**見積もり目安:** 0.5 日（+ 審査待ち日数は開発のブロッカーにしない）

---

### P1: 安全なアップロードサービス（private 固定 + resumable + 永続 operation）

**目的:** 実 API を呼ばずに、本番 upload の入力・再試行・結果不明・日次試行数・ジョブ復元を安全な契約として先に完成させる。
**変更ファイル範囲:**
- `src/yt_live_kit/models/upload.py`（新規。channel / content snapshot / operation / result の frozen model）
- `src/yt_live_kit/services/youtube_api.py`（実チャンネル取得、入力検証、resumable upload、status / processing poll）
- `src/yt_live_kit/services/upload_queue.py`（新規。operation / attempt 台帳、状態遷移、冪等 job target、reconciliation）
- `src/yt_live_kit/services/ffmpeg.py`（公開 `probe_duration()` の追加）
- `src/yt_live_kit/services/jobs.py`（`"upload"` kind、`YouTubeAPIError` / `UploadQueueError` の既知例外化、target が設定した非 pipeline `result_ref` の保持）
- `src/yt_live_kit/ui/components/status_bar.py`（kind 別の完了結果 dispatch。pipeline 以外を `load_result_from_disk()` へ渡さない）
- `src/yt_live_kit/config.py`（`video_upload_daily_limit: int = 100`、1〜100、環境変数 `YTLK_VIDEO_UPLOAD_DAILY_LIMIT`）
- `tests/test_youtube_api.py` / `tests/test_ffmpeg.py` / `tests/test_upload_queue.py`（新規または追記）
- `tests/test_jobs.py` / `tests/test_ui_app.py`（jobs / status bar 契約の追記）

**作業:**

- [x] P1-1. `models/upload.py` に frozen な `UploadChannel`、`UploadContentSnapshot`、`UploadStatusObservation`、`UploadOperation`、`UploadResult` を定義する。snapshot は `self_declared_made_for_kids: bool`、`contains_synthetic_media: bool`、`community_guidelines_confirmed: Literal[True]`、同意 UTC timestamp を必須とする。`UploadStatusObservation` は UTC `polled_at`、`phase: Literal["processing", "publication"]`、取得した `status` / `processing_details`、`classification`、日本語 `error` を持つ。operation は `operation_id`、`source_video_id`、`source_kind`、`clip_id`、絶対 `video_path`、content snapshot、`reserved/uploading/uploaded/failed/needs_reconciliation`、`job_id`、YouTube `video_id`、UTC の `created_at/updated_at/started_at/finished_at`、日本語 `error`、入力順の `poll_history: tuple[UploadStatusObservation, ...]` を必須 field として保持し、unknown / 欠落 field と不正状態遷移を拒否する。最新状態は poll history 末尾から導出し、履歴を上書きしない
- [x] P1-2. upload preflight を実装する。mp4 の存在・通常ファイル・絶対パス化、サイズと `mtime_ns`、`probe_duration()` の 10〜180 秒、title strip 後 1〜100 文字、description UTF-8 5000 bytes 以下、全 tag 非空かつ `",".join(tags)` 500 文字以下、全 metadata の半角山カッコ禁止を検証し、canonical content snapshot を作る
- [x] P1-3. publish 契約を固定する。`privacy_status` は引数で緩めず `private` のみ、`publish_at` は aware かつ `now + 10 分` 以上、`notify_subscribers` は `False` のみを許可し、API body は UTC RFC 3339 `Z` へ正規化する。`status.selfDeclaredMadeForKids` / `status.containsSyntheticMedia` は snapshot の必須 bool をそのまま反映する。Community Guidelines 未同意、naive / 過去 / リード不足 / `public` / `unlisted` / publishAt 無しを API 構築前に日本語で拒否する
- [x] P1-4. `fetch_mine_channel(settings) -> UploadChannel` を `channels.list(part="snippet", mine=True)` で実装し、0 件 / 複数件 / 欠落 field を日本語エラーにする。確認 snapshot に channel ID / 名称を含める
- [x] P1-5. `MediaFileUpload(..., resumable=True)` と単一 `videos.insert(part="snippet,status", notifySubscribers=False, ...)` request の `next_chunk()` loop を実装する。ネットワーク例外と HTTP 500 / 502 / 503 / 504 だけを同一 session で最大 5 回、1 / 2 / 4 / 8 / 16 秒に bounded retry し、retry 後は同じ request の `next_chunk()` を続ける。4xx、retry exhaustion、response 欠落 / video ID 欠落は新規 insert を作らず `needs_reconciliation` を返す。preflight の確定的失敗だけ `failed` とし、全例外を日本語 `YouTubeAPIError` または typed outcome にする
- [x] P1-6. `data/_schedule/queue.json` を full operation と予約 slot の単一正本、`upload_attempts.json` を試行台帳として、advisory file lock + process lock 下の一時ファイル + replace で各ファイルを atomic 保存する。queue record は P1-1 の全 operation field を保持し、別の operation JSON と二重管理しない。壊れた JSON、schema 不一致、重複 operation ID は fail closed の日本語 `UploadQueueError` とし、空配列へ回復しない。attempt は `America/Los_Angeles` の upload attempt 開始日、operation ID、job ID、UTC timestamp を **`MediaFileUpload` / `videos.insert` による resumable upload session 作成前**に 1 回記録し、失敗・結果不明も残す。事前の read-only `channels.list` は attempt に数えない。設定上限 1〜100 の境界を守り、予約公開日・予約件数は参照しない
- [x] P1-7. `upload_job_target(*, report, settings: Settings, job_id: str, operation_id: str) -> None` を jobs 契約に合わせる。confirm transaction は operation ID と job ID を先に生成し、同じ queue record へ `reserved` として atomic 保存する。`jobs.start_job()` は `requested_job_id` を受けてその ID の job JSON を thread 起動前に作り、既存 ID を黙って上書きしない。target は queue の job ID と受領 job ID の一致を確認し、`reserved` だけを `uploading` へ進めて attempt を 1 回記録後に upload session を作る。`uploading/uploaded/failed/needs_reconciliation` の再実行は insert せず既存結果を返すか手動照合を案内する。完了時に jobs の `result_ref=operation_id` を設定し、jobs worker は target が設定した `result_ref` を video ID で上書きしない
- [x] P1-8. 起動順を `jobs.close_orphans()` → upload recovery に固定し、attempt ledger を外部効果開始の正本とする。recovery の状態遷移対象は active state の `reserved` / `uploading` だけとし、当該 operation の attempt が無ければ upload session 未作成の契約により `failed` + slot 解放として新しい preview / 承認を要求する。attempt が 1 件以上、job / operation 不一致、または attempt ledger が壊れて有無を確定できない場合は `needs_reconciliation` + slot 保持として自動再送しない。terminal の `uploaded/failed/needs_reconciliation` は変更せず、ledger と不整合なら queue / slot を一切変更せず日本語 `UploadQueueError` で全新規 upload を fail closed にし、手動修復を要求する
- [x] P1-9. `videos.list(part="status,processingDetails", id=...)` の poll 契約を固定する。processing poll は sleep / clock 注入可能、10 秒間隔・最大 30 回とし、`processingStatus=succeeded` を成功 terminal、`failed/terminated` を失敗 terminal、30 回後を timeout とする。公開 poll は予約時刻到来後に 30 秒間隔・最大 20 回とし、`privacyStatus=public` を公開成功、processing の `failed/terminated` を失敗、20 回後も private を timeout とする。各 response と時刻を operation へ記録する
- [x] P1-10. private lock 判定表を固定する。予約時刻前の `privacyStatus=private` かつ期待する `publishAt` は正常な scheduled、processing 成功後に `publishAt` が欠落、または予約時刻 + 5 分を過ぎても private なら `suspected_private_lock`、public なら `published` とする。API だけで確定せず、P0 では YouTube Studio の表示を併用して `confirmed_private_lock / no_private_lock` を記録する。lock は `uploaded` と別の予約公開可否 field として保持する
- [x] P1-11. status bar は `single/regenerate` 等の pipeline kind だけを pipeline loader へ、`batch` は batch loader、`shorts_queue` は manifest UI、`upload` は operation loader へ dispatch する。未知 kind / result_ref 欠落 / operation 読込失敗も日本語で表示し、pipeline 読み込みを誤実行しない
- [x] P1-12. ユニットテスト
  - metadata / file / duration / aware time / 10 分 lead / UTC `Z` / private 固定 / notify false、Made for Kids / synthetic media の必須 bool、Community Guidelines 同意の正常系と全境界
  - `channels.list(mine=True)` と `videos.insert` body、`MediaFileUpload(resumable=True)`、`next_chunk()` 複数 chunk
  - network と 500 / 502 / 503 / 504 の同一 request 内 5 回 retry、backoff 列、4xx / retry exhaustion / response 不明で insert 1 回かつ `needs_reconciliation`
  - operation の全状態遷移、atomic write、process / file lock、壊れた JSON fail closed、LA 日付境界と DST、上限 1 / 100 / configured limit、失敗 attempt の算入、resumable upload session より先の記録、read-only channel lookup が attempt 非算入
  - `job_id` 必須、同一 job / operation の二重実行、`uploading` で中断した再起動、`needs_reconciliation` の自動再送禁止、result_ref 保持
  - queue record 保存前 / 保存後、job JSON 作成前 / 作成後、thread 起動前 / 起動後、`uploading` 保存前 / 保存後、attempt 保存前 / 保存後の fault injection。`close_orphans()` 後の active recovery が attempt 無しは failed + slot 解放、attempt 有りまたは ledger 読込不能は needs_reconciliation + slot 保持になり、insert call count が増えないこと。terminal state は不変で、ledger 不整合時は queue / slot 非変更 + 全新規 upload fail closed になること
  - processing poll の 10 秒 × 30、公開 poll の 30 秒 × 20、全 terminal / timeout、予約前 private、publishAt 欠落、予約 + 5 分 private、public の判定表を fake clock / sleep で検証すること
  - すべての processing / publication response が `UploadStatusObservation` として順序・時刻・phase・classification を保って round-trip し、unknown / 欠落 field を拒否して最新値だけに縮退しないこと
  - status bar の kind 別 dispatch と upload / shorts_queue / batch で pipeline loader 未呼び出し

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] 実 API・sleep・ffprobe subprocess はすべてモックされ、実 upload を行っていない
- [x] 4xx / 結果不明から新しい `videos.insert` が自動作成されないことを call count で確認済み
- [x] 壊れた永続 JSON、同時確定、再起動復元、同一 operation 再実行が fail closed / 冪等である
- [x] 全ユーザー向けエラーが日本語で、従量課金 AI API を追加していない
- [x] タスク完了コミット済み

**見積もり目安:** 2.5 日

---

### P2: スケジュールポリシー + 原子的な予約確定 + 投稿確認 UI

**目的:** IANA timezone の予約枠を安全に割り当て、確認内容と upload operation を原子的に結び、二重クリック・確認中の競合・再起動でも重複 upload を起こさない。
**変更ファイル範囲:**
- `src/yt_live_kit/services/schedule.py`（新規。policy、予約 queue、slot snapshot、confirm transaction）
- `src/yt_live_kit/services/upload_queue.py`（P1 の operation と confirm transaction の接続のみ）
- `src/yt_live_kit/ui/views/settings.py`（スケジュールポリシー編集欄）
- `src/yt_live_kit/ui/views/video_detail.py`（生成済みショートの予約投稿入口）
- `src/yt_live_kit/ui/components/upload.py`（新規。preview / dialog / job 起動 / operation 復元の表示だけ）
- `tests/test_schedule.py` / `tests/test_ui_upload.py`（新規）
- `tests/test_ui_video_detail_page.py` / `tests/test_ui_settings_page.py` / `tests/test_ui_app.py`（追記）

**作業:**

- [x] P2-1. `SchedulePolicy(daily_time: str, interval_days: int, timezone: str = "Asia/Tokyo")` を pydantic で定義する。`daily_time` は ASCII の厳密な `HH:MM`、`interval_days >= 1`、timezone は `zoneinfo.ZoneInfo` で存在確認し、`data/_config/schedule_policy.json` を atomic 保存・fail closed 読み込みする
- [x] P2-2. `assign_next_slot(policy, existing_reservations, *, now: datetime) -> datetime` を pure に実装する。`now` は aware 必須、計算結果も policy timezone の aware datetime、現在から最低 10 分先、既存 slot と非重複、`interval_days` 間隔とする。DST の ambiguous / nonexistent local time は暗黙補正せず日本語で拒否する。API 変換 helper は UTC RFC 3339 `Z` を返す
- [x] P2-3. P1 の `data/_schedule/queue.json` を拡張せず同じ full operation record の `publish_at` / content snapshot / state を予約 slot と operation の単一正本として使う。upload attempt 台帳とは別 schema / 別集計にし、予約公開日が同じでも LA 当日の attempt 上限を消費した扱いにしない。全 read / write は同じ lock 順序、atomic replace、壊れた JSON fail closed を使う
- [x] P2-4. preview service は `channels.list(mine=true)`、file stat、`probe_duration()`、metadata、policy、slot、attempt count から immutable `UploadPreview` / fingerprint を作る。UI dialog は実チャンネル ID / 名称、絶対ファイル、サイズ / 尺、title、description 全文、tags、policy timezone の予約日時、UTC `Z`、`privacyStatus=private`、`notifySubscribers=false` を読み取り専用で表示する。Made for Kids と synthetic media は既定値なしの「はい / いいえ」を毎回必須選択し、Community Guidelines 同意 checkbox は既定未チェックとする。外側ボタン・通常 rerun・未選択・未同意・dialog 未確定では operation / job / attempt を作らない
- [x] P2-5. dialog 確定時の service transaction は固定 lock 順序で queue / attempt snapshot を再読込し、実チャンネル再取得、file identity、duration、Made for Kids / synthetic media / Community Guidelines 同意を含む content fingerprint、slot 空き、同一 source / clip の active operation、LA 当日 attempt 残数を再検証する。変化・競合・上限到達時は予約も job も作らず新しい preview を要求する。成功時だけ operation ID / job ID を生成し、slot + `reserved` operation + job ID を queue の単一 record として 1 回 atomic 保存してから、同じ ID を `start_job(..., requested_job_id=job_id, operation_id=operation_id)` へ渡す
- [x] P2-6. `start_job()` の同期失敗時は予約済み operation を日本語 error 付き `failed` にして slot を解放する。保存後のプロセスクラッシュは P1-8 の recovery table に従う。job target 開始後の slot は自動解放せず、`needs_reconciliation` を含む operation 状態から復元する。同じ preview / operation の二重クリック、同一 job の再入、プロセス再起動では新 operation / slot / insert を作らない
- [x] P2-7. UI は session state に video ID → operation ID を保持し、その operation だけを表示する。key が無い場合だけ source video / clip に限定した latest operation を復元する。`reserved/uploading/uploaded/failed/needs_reconciliation`、processing / private lock、job ID / YouTube video ID、エラーを日本語表示し、`needs_reconciliation` に自動再試行ボタンを出さない。ワーカースレッドから `st.*` を呼ばない
- [x] P2-8. settings UI は policy の `HH:MM`、interval、IANA timezone と、読み取り専用の `YTLK_VIDEO_UPLOAD_DAILY_LIMIT`、LA 当日 attempts を表示する。policy 保存は form submit 時だけ行い、不正値と壊れた JSON を日本語表示する
- [x] P2-9. ユニットテスト
  - `HH:MM` の 00:00 / 23:59 と不正文字列、interval 0 / 1、IANA timezone / 不正 zone、aware now / naive 拒否、Asia/Tokyo と DST zone の slot / UTC `Z`
  - slot 重複、10 分 lead、interval 跨ぎ、queue と attempt の独立、予約公開日と LA attempt 日が異なるケース
  - preview 全項目表示、Made for Kids / synthetic media 未選択と Community Guidelines 既定未チェック、未確定時 side effect なし、確定後の channel / file / metadata / audience / synthetic / consent / slot / attempt 再検証、各 race で start_job / API 未呼び出し
  - lock 順序と同時 confirm で勝者 1 件、単一 queue record の operation / slot / 先行 job ID、各境界の fault injection、start_job 同期失敗時 rollback、二重クリック、同一 job / operation、P1-8 に従う再起動復元
  - status bar / upload component が upload operation を表示し pipeline result と混同しないこと、`needs_reconciliation` の自動再送導線が無いこと

**Done 条件:**

- [x] `uv run pytest` が全件通る
- [x] 予約 slot と LA upload attempt が別概念として境界テストされている
- [x] 確認前および確認後再検証失敗時に operation / job / resumable upload session が作られない（preview の read-only `channels.list` は許容）
- [x] 同時 confirm・二重クリック・再起動で operation ID / job ID が一貫し、重複 insert が起きない
- [x] 実 API はすべてモック、ユーザー向けエラーは日本語、従量課金 AI API 追加なし
- [x] タスク完了コミット済み

**見積もり目安:** 2.5 日

---

### P3: フェーズ P 受け入れ（予約投稿が実際に公開される）

**目的:** P0 の private lock / 審査結果を前提に、v3 の予約公開を 1 本だけ別承認で受け入れ、公開前後の API status まで証跡化する。
**変更ファイル範囲:**
- `README.md`（安全な予約投稿、attempt / reconciliation、確認項目、private lock の説明）
- `src/yt_live_kit/__init__.py`（版数を `0.3.0` に更新）
- `pyproject.toml`（版数を `0.3.0` に更新）
- `docs/execution-plan-v3.md`（承認・poll 証跡、進捗・マイルストーン最終更新）

**作業:**

- [x] P3-1. P0 の private lock が解消または非該当で、P1 / P2 の全テストが通ることを確認する。lock 中は予約公開成功を装わず P3 未完了のままにする
- [x] P3-2. 実チャンネル ID / 名称、対象ファイル、サイズ / 尺、タイトル、説明文、タグ、policy timezone / UTC `Z` の予約日時、private、notify false、Made for Kids / synthetic media の選択、Community Guidelines 同意、operation / slot / attempt snapshot をユーザーに提示し、**P0 upload や審査承認とは別の P3 実予約公開専用承認**を得る。承認前は確定しない
- [x] P3-3. 承認後の再検証を通して 1 本を予約 upload し、`videos.list(part="status,processingDetails")` を upload 後 processing 完了まで、予約時刻前、予約時刻後に bounded poll して operation に記録する。時刻前 private、時刻後 public と実視聴可能を確認する
- [x] P3-4. `docs/requirements-v3.md` の AC-18〜AC-28 を総点検する。4xx / unknown の reconciliation、LA attempt、再起動復元、status bar kind dispatch はモック証跡、実公開は P3 の operation 証跡を使う
- [x] P3-5. 実配信 1 本でチャプター生成 → ショート複数本生成までを通し、上記の承認済み 1 本だけを予約投稿対象にする（AC-28）
- [x] P3-6. README を更新する。private 固定、10 分 lead、IANA timezone、確認ダイアログ全項目、notify false、LA upload attempt 上限、`needs_reconciliation` は自動再送しないこと、private lock / 審査、実 API を自動テストしないことを記載する
- [x] P3-7. 版数を `0.3.0` に更新し、進捗サマリー、M14、受け入れ証跡を最終更新する

**P3-4 事前 AC 監査（2026-08-01、実 API 未実行）:**

- AC-27 の安全な公開契約は `TestUploadPreflight.test_rejects_unsafe_boundaries`、`TestUploadPreflight.test_publish_at_none_and_lead_boundary`、`test_upload_body_is_private_utc_z_and_contains_required_booleans` で確認した
- AC-27 の確認 UI と確定後再検証は `test_dialog_displays_complete_preview_and_defaults_are_unselected`、`test_confirm_revalidates_preview_and_starts_no_job_on_change`、`test_slot_race_and_needs_reconciliation_hold_slot_and_source` で確認した
- AC-27 の metadata 境界は `TestUploadPreflight.test_metadata_boundaries` と `test_build_upload_body_revalidates_tampered_snapshot` で確認した
- AC-27 の audience / synthetic / consent は `test_confirm_rejects_unselected_audience_synthetic_and_consent_without_writes`、`test_confirm_saves_single_record_then_starts_same_ids`、`test_dialog_confirm_passes_explicit_choices_and_stores_operation` で確認した
- AC-27 の resumable / reconciliation は `test_resumable_upload_retries_same_request_and_never_reinserts`、`test_upload_error_never_creates_second_insert`、`test_missing_response_or_video_id_never_reinserts`、`test_crash_after_attempt_save_recovers_without_resend` で確認した
- AC-27 の operation / atomic / lock / fail closed / recovery は `test_operation_models_are_frozen_and_reject_unknown_fields`、`test_queue_is_single_full_operation_record_and_atomic_temp_is_removed`、`test_broken_queue_fails_closed`、`test_advisory_file_lock_is_used`、`test_recovery_with_attempt_requires_reconciliation_and_never_resends` で確認した
- AC-27 の LA attempt と上限は `test_attempt_is_idempotent_and_uses_los_angeles_date`、`test_upload_limit_environment_boundaries`、`test_attempt_limit_counts_failed_or_unknown_attempts`、`test_job_target_records_attempt_before_session_and_is_idempotent`、`test_attempt_race_is_independent_from_publish_date` で確認した
- AC-27 の job / error / kind dispatch は `test_job_target_records_attempt_before_session_and_is_idempotent`、`test_start_job_passes_job_id_to_target_fn`、`test_start_job_preserves_japanese_youtube_api_error_without_log`、`test_non_pipeline_finished_jobs_never_use_pipeline_loader` で確認した。`YouTubeAPIError` の日本語 message は実 `start_job` worker 経路で failed job の error / message に保持され、汎用エラー表示や不要な error log に落ちない
- AC-27 の bounded poll / private lock 判定は `test_processing_poll_has_fixed_interval_limit_and_typed_history`、`test_publication_poll_has_fixed_interval_limit_and_preserves_responses`、`test_processing_poll_stops_on_each_terminal`、`test_publication_poll_stops_on_terminal`、`test_private_lock_decision_table` で確認した
- AC-28 の v1 / v2 回帰なしは `uv run pytest -q` の全 821 件通過で確認した
- 実予約公開、P0 private lock / 専用承認、AC-28 の総合充足・実通し・操作別承認は実機証跡がないため未チェックを維持した。P3-4 本体も実公開 operation 証跡を得るまで未完了とする

**P3 実予約公開の承認記録（2026-08-01 07:53 JST）:**

- P0 operation `886c300a9a1142058f24e99249fe79ca` は API / Studio / 公開 URL で public、`no_private_lock` と確認済み。P1 / P2 を含む直近の `uv run pytest -q` は 821 passed / 2 skipped で、以後コード変更はない
- チャンネル `AI整体師`（`UCVAkt5l6kD4igMdVoEGTGIg`）、対象 `/Users/ryukouokumura/Desktop/boss-workspace/yt-live-kit/data/IJvd6k6ZmUo/shorts/output/short_5ea2b4ff8e08.mp4`、3,791,982 bytes、17.042993 秒を提示した
- タイトルは「テラとルナを比較した結果」、説明文は「テラとルナを比較した感想と、Grok 4.5 Highで作成した結果を紹介します。」、タグは「AI、テラ、ルナ、Grok 4.5」
- policy は毎日 09:00、1 日間隔、`Asia/Tokyo`。slot は 2026-08-02 09:00 JST / 2026-08-02 00:00 UTC、private 固定、notify false、LA 当日 attempt は 1 / 100、fingerprint は `1677a3b1f97285ae614ceea2280474b1cc0e4787b1af3119e9a4271fe5ac4df7`。承認前のため operation / job ID は未発行
- ユーザーから P0 と別の P3 実予約公開、Made for Kids「いいえ」、synthetic media「いいえ」、Community Guidelines「確認済み」、実 upload と外部公開、upload 後に 10 分 lead を保つ最短時刻へ前倒しして検証することへの明示承認を取得した
- 承認後の本番 preview 再検証で、上記の全項目、attempt、fingerprint が一致した

**P3 実 upload・公開前確認記録（2026-08-01 07:57〜07:58 JST）:**

- 全 821 tests 通過と独立レビュー APPROVE 後、本番経路から承認済み `short_5ea2b4ff8e08.mp4` だけを 1 本 upload した。operation ID は `7a6bb640baf74362b83a06625c08f7c9`、job ID は `500950330a6942bd98af9d6389428e45`、YouTube video ID は `1bxDoF52DEs`、URL は `https://youtube.com/shorts/1bxDoF52DEs`
- operation は `reserved → uploading → uploaded`、job は `done`。LA 当日 attempt は upload 前 1 / 100、upload 後 2 / 100 で、追加の `videos.insert` は実行していない
- processing poll は 2026-07-31 22:57:06、22:57:16、22:57:26、22:57:36 UTC の 4 回で `processing → processing → processing → processing_succeeded`。最終 `uploadStatus=processed`、`processingStatus=succeeded`、`privacyStatus=private`、upload 時 `publishAt=2026-08-02T00:00:00Z`
- 前倒し承認に基づき、10 分 lead を維持して 2026-08-01 08:15 JST / 2026-07-31 23:15 UTC へ変更した。YouTube Studio の「すべての変更を保存しました」と API の `privacyStatus=private`、`processingStatus=succeeded`、`publishAt=2026-07-31T23:15:00Z` を照合した。operation の upload 時 snapshot は元の slot を不変記録として保持し、公開 poll には実際の 08:15 を使う
- 元の実配信 `IJvd6k6ZmUo` は S5 でチャプター生成からショート複数本生成まで完了済みで、P3ではそのうち専用承認を得た clip `5ea2b4ff8e08` だけを投稿対象にした

**P3 公開後確認・最終 AC 監査記録（2026-08-01 08:25 JST）:**

- 予約時刻前の 2026-07-31 23:00:53 UTC に `uploadStatus=processed`、`processingStatus=succeeded`、`privacyStatus=private`、`publishAt=2026-07-31T23:15:00Z`、classification `scheduled` を operation へ追記した
- 予約時刻後の bounded publication poll は 2026-07-31 23:25:58 UTC の初回で `uploadStatus=processed`、`processingStatus=succeeded`、`privacyStatus=public`、classification `published` を返した。operation の poll history は processing 4 件、公開前 1 件、公開後 1 件の計 6 件
- API の `snippet.publishedAt=2026-07-31T23:15:36Z` で予約時刻から 36 秒後の公開を確認した。YouTube Studio は公開設定「公開」、Standard / HD processing 完了。公開 URL `https://youtube.com/shorts/1bxDoF52DEs` は動画プレーヤー、チャンネル `@ai.seitai`、タイトル「テラとルナを比較した結果」を表示し、実視聴可能と確認した
- AC-18〜AC-26 は既存の自動テストと U5 / S5 実機証跡、AC-27 は P0 / P3 operation と安全契約テスト、AC-28 は実配信 `IJvd6k6ZmUo` のチャプター生成、3 本のショート生成、専用承認済み 1 本だけの予約公開、および全 821 tests で総点検した。未達・次イテレーションへの申し送りは無い

**Done 条件:**

- [x] P3 実予約公開の対象と全内容を提示した専用の明示承認記録がある
- [x] operation に upload 後、公開前、公開後の status / processingDetails と poll 時刻が記録されている
- [x] private lock を成功扱いにせず、予約時刻後に実際に public かつ視聴可能である
- [x] AC-18〜AC-28 が確認済み（未達は明示的に次イテレーションへ移す）
- [x] `uv run pytest` が全件通り、実 API を呼ぶ自動テストが無く、v1 / v2 に回帰が無い
- [x] v3 完了コミット済み

**見積もり目安:** 1 日

---

### P4: ショート概要欄の定型リンク差し込み（v3 追加要件）

**目的:** 投稿するショートの概要欄に、チャンネル URL と元になったライブ配信のリンクを定型で入れられるようにする（FR-29 / AC-29）。
**フェーズ状態:** [x] 完了
**前提:** P3 完了（0.3.0）。本タスクは v3 受け入れ後の追加要件であり、P3 の証跡・版数・受け入れ記録は変更しない。

**変更ファイル範囲:**
- `src/yt_live_kit/services/description.py`（ショート用テンプレートの読み書きと合成関数を追加。既存の長尺用関数は変更しない）
- `src/yt_live_kit/ui/components/upload.py`（preview 生成前に合成、先頭区間の開始秒を受け渡し、テンプレート未設定の案内）
- `tests/test_description.py` / `tests/test_ui_upload.py`（合成境界と UI 受け渡しのテスト追加）
- `README.md`（テンプレートの置き場とプレースホルダーの説明）
- `docs/requirements-v3.md` / `docs/execution-plan-v3.md`（FR-29 / AC-29 / 本タスクの進捗）

**作業:**

- [x] P4-1. `services/description.py` に `get_shorts_template_path()` / `save_shorts_template()` / `build_shorts_description()` を追加する。テンプレートは `data/_config/shorts_description_template.txt`、置換対象は `{{description}}` / `{{source_title}}` / `{{source_url}}`。未設定時は入力の説明文をそのまま返す
- [x] P4-2. `{{source_url}}` に切り抜き先頭区間の開始秒を `t` クエリとして付与する。既存の `t` は置き換え、同じ入力から常に同じ URL を返す
- [x] P4-3. 半角山カッコ、UTF-8 5000 bytes 超過、`meta.json` 欠損・破損を、テンプレートが原因と分かる日本語 `DescriptionError` で拒否する（空文字フォールバック禁止）
- [x] P4-4. `ui/components/upload.py` で、`build_upload_preview()` を呼ぶ前に合成する。開始秒は最新 manifest の `clip_specs` から `target_id` 一致で先頭区間を引く。`DescriptionError` は日本語で表示し preview を開かない。テンプレート未設定時は設置場所を案内する
- [x] P4-5. テストを追加する（テンプレート有無、開始秒付与、連結モードの先頭区間基準、山カッコ・5000 bytes・`meta.json` 欠損の拒否、UI が合成後の本文を `build_upload_preview` に渡すこと、長尺用 `build_description()` の非回帰）
- [x] P4-6. README にテンプレートの置き場・プレースホルダー・チャンネル URL は直接記載する運用を追記する
- [x] P4-7. `uv run pytest` を全件通し、進捗チェックと AC-29 を更新してコミットする

**Done 条件:**

- [x] AC-29 の全項目が満たされている
- [x] 確認ダイアログに出た説明文と `videos.insert` へ送られる説明文が一致している（合成が preview 前に完結している）
- [x] テンプレート未設定時の挙動が従来と同一で、既存の投稿導線・長尺概要欄反映に回帰が無い
- [x] エラーはすべて日本語で、半角山カッコを含まない
- [x] `uv run pytest` が全件通過し、従量課金 API・新規 pip 依存の追加が無い
- [x] タスク完了コミット済み

**見積もり目安:** 0.5 日

---

### S6: 切り抜き候補からのショート用サブ区間提案（v3 追加要件）

**目的:** 10〜15 分の切り抜き候補を選んだときに、その中から合計 180 秒以内のサブ区間を AI が提案し、人が採否・境界を確認して、既存のジャンプカット連結（FR-25）で 1 本のショートにできるようにする（FR-30 / AC-30）。
**フェーズ状態:** [x] 完了
**前提:** S3 / S4 完了、P3 完了（0.3.0）。本タスクは v3 受け入れ後の追加要件であり、P3 の証跡・版数・受け入れ記録は変更しない。

**背景:** 現在、ショートセクションは切り抜き候補の `start` / `end` をそのまま 1 区間として扱うため（[`ui/views/shorts.py`](../src/yt_live_kit/ui/views/shorts.py) の `interval_from_timestamps` → `validate_interval_duration`）、660 秒の候補は 180 秒上限で弾かれて先へ進めない。連結経路（`build_short_from_segments`）は既にあるが、キュー量産は候補 1 件 = 1 区間として扱うため、長い候補の内部を刻む経路が存在しない。S6 はこの欠けている 1 手を埋める。

**変更ファイル範囲:**
- `prompts/short_cut.md`（新規。親区間内の字幕からサブ区間を提案させるテンプレート）
- `src/yt_live_kit/models/short_cut.py`（新規。カットプラン文書モデル）
- `src/yt_live_kit/services/short_cut.py`（新規。プロンプト構築・Codex 呼び出し・検証・保存・読込。`highlights.py` と同型）
- `src/yt_live_kit/ui/components/short_cut.py`（新規。提案生成・採否・境界調整・連結生成の導線）
- `src/yt_live_kit/ui/views/shorts.py`（新設セクションの呼び出しのみ追加。既存の単一区間経路は変更しない）
- `tests/test_short_cut.py` / `tests/test_ui_short_cut.py`（新規）
- `README.md`（導線の説明を追記）
- `docs/requirements-v3.md` / `docs/execution-plan-v3.md`（FR-30 / AC-30 / 本タスクの進捗）

**作業:**

- [x] S6-1. `prompts/short_cut.md` を追加する。プレースホルダは `{{segment_transcript}}`、出力は `candidates` 配列（`cut_001` 連番、`title` / `start` / `end` / `duration_sec` / `reason`）。制約は「2〜5 個・各区間 5〜120 秒・合計 30〜170 秒・時系列順・非重複・すべて親区間内・文の途中で切らない」
- [x] S6-2. `models/short_cut.py` に `ShortCutDocument`（`parent_id` / `parent_start_ms` / `parent_end_ms` / `candidates: list[HighlightSegment]`）を追加する
- [x] S6-3. `services/short_cut.py` を実装する。`build_short_cut_prompt()` は [`services/telop.py`](../src/yt_live_kit/services/telop.py) の区間内 VTT 整形を再利用し、絶対時刻付きで親区間だけを渡す。`validate_short_cut()` は純粋関数として親区間包含・時系列・非重複・個別尺・合計尺・`duration_sec` 一致・半角山カッコを検証する。合計尺の境界は `shorts.py` の `MIN_DURATION_SEC` / `MAX_DURATION_SEC` を import して単一の正本にする
- [x] S6-4. `suggest_short_cuts()` を実装する。保存先は `data/{video_id}/shorts/cutplan/cut_{parent_id}.json` へ atomic 書き込み。検証失敗時は既存ファイルを書き換えず日本語エラーで拒否する。Codex 未導入時は保存済みプロンプトを使う手動フォールバックを案内する（`highlights.py` と同じ扱い）
- [x] S6-5. `ui/components/short_cut.py` を実装する。親候補が 180 秒超のときだけ導線を出し、「区間を提案」submit 時だけ Codex ジョブを開始する。提案は採否チェックと開始 / 終了の調整入力を持ち、合計尺を常時表示する。10〜180 秒の範囲外では作成ボタンを無効化し日本語で案内する。生成は既存 `build_short_from_segments()` をジョブから呼ぶ
- [x] S6-6. `ui/views/shorts.py` から新セクションを呼び出す。既存の単一区間経路・キュー量産導線には手を入れない
- [x] S6-7. テストを追加する（提案 JSON の正常系、親範囲外 / 逆順 / 重複 / 個別尺 / 合計尺 / `duration_sec` 不一致 / 山カッコの各拒否、atomic 保存と既存ファイル保護、Codex 未導入時の案内、180 秒以下の候補で導線が出ないこと、UI 純粋関数の合計尺・作成可否判定、既存ショート生成の非回帰）
- [x] S6-8. `uv run pytest` を全件通し、README と進捗チェック・AC-30 を更新してコミットする（pytest 934 件通過・README 追記・AC 更新まで完了。コミットと実機確認が残り）
- [x] S6-9. 実機確認: 実配信 1 本で、660 秒級の切り抜き候補 → 区間提案 → 採否・境界調整 → 連結生成 を通しで実行し、UI 表示・Codex の再実行が起きないこと・つなぎ目・出力解像度（`ffprobe`）を目視確認する。AC-30 の残り 4 項目はここで確定する

**Done 条件:**

- [x] AC-30 の全項目が満たされている
- [x] 660 秒の切り抜き候補から、人の確認を経て 180 秒以内のショートが 1 本生成できる
- [x] Codex 呼び出しが明示 submit 時に限られ、通常 rerun で再実行されない
- [x] 既存の単一区間ショート生成・キュー量産・ハイライトまとめに回帰が無い
- [x] エラーはすべて日本語で、半角山カッコを含まない
- [x] `uv run pytest` が全件通過し、従量課金 API・新規 pip 依存の追加が無い
- [x] タスク完了コミット済み

**見積もり目安:** 1.5 日

---

### S7: FFmpeg 字幕フィルタの環境検査と復旧（ホットフィックス）

**目的:** Homebrew 通常版 FFmpeg に `libass` が含まれず、ショート生成の最終工程で `No such filter: 'subtitles'` となる環境を生成前に検知し、`libass` 対応版を明示設定して復旧できるようにする（FR-24 / FR-25）。
**フェーズ状態:** [x] 完了
**前提:** S3 / S4 完了。本タスクは完了済みフェーズのチェックを戻さず、実機障害に対する独立した修復タスクとして扱う。

**変更ファイル範囲:**
- `src/yt_live_kit/services/ffmpeg.py`
- `src/yt_live_kit/services/shorts.py`
- `src/yt_live_kit/ui/views/settings.py`
- `tests/test_ffmpeg.py`
- `tests/test_shorts.py`
- `tests/test_ui_settings_page.py`
- `README.md`
- `docs/tech-stack.md`
- `docs/execution-plan-v3.md`
- `.env`（ローカル実行設定。Git 管理対象外）

**作業:**

- [x] S7-1. 実際に後段へ渡す FFmpeg パスを一度だけ解決し、短い専用タイムアウトで `-filters` を実行してフィルタ名列の `subtitles` を完全一致検査する service API を追加する。バイナリ不在・実行不能・タイムアウト・異常終了・フィルタ不足は、原因と `YTLK_FFMPEG_PATH` による対処を含む日本語の `FfmpegError` にする
- [x] S7-2. `build_short_from_segments()` は全純粋入力検証後かつ元動画取得・encode・concat 前に `subtitles` capability を必須検査する。`build_short()` は layout を含む全純粋入力検証後、`burn_subtitles=True` の場合だけ同じ検査を行い、`False` では通常版 FFmpeg を許容する。設定値と明示引数がずれないよう、有効 FFmpeg パスを全後段へ伝播する
- [x] S7-3. 設定画面へ、設定値、解決済み実パス、バージョン、字幕フィルタ利用可否を読み取り専用で表示する。診断失敗でページ全体を落とさず、日本語の警告と `ffmpeg-full` / `.env` の案内を出す
- [x] S7-4. README と技術スタックを更新し、macOS の字幕付きショートでは keg-only の `ffmpeg-full` を明示パスで使うこと、`libass` 単体導入や通常版 FFmpeg のアップグレードだけでは既存バイナリへフィルタが追加されないこと、字幕なし生成は通常版でも可能なことを記載する
- [x] S7-5. capability parser の完全一致、missing binary / OSError / timeout / nonzero / missing filter、入力不正時の probe 未実行、capability 不足時の download / encode / concat / layout 未実行、字幕なし分岐、設定 UI の成功・警告をユニットテストする。opt-in 実統合テストは未 opt-in 時だけ skip し、opt-in 後の設定バイナリ不在・フィルタ不足を fail にする
- [x] S7-6. `ffmpeg-full` をローカル導入し、`.env` の `YTLK_FFMPEG_PATH` を明示設定する。指定バイナリの `subtitles` フィルタ、同梱 `ffprobe`、実 ASS 焼き込み統合テスト、対象ショートの再生成を確認する

**Done 条件:**

- [x] `subtitles` 非対応 FFmpeg では動画取得・encode より前に対処可能な日本語エラーで停止する
- [x] 字幕なし単一区間生成は `subtitles` 非対応 FFmpeg でも回帰しない
- [x] 設定画面だけで実際の FFmpeg と字幕対応可否を確認できる
- [x] `ffmpeg-full` を明示したローカル環境で実 ASS 焼き込みと対象ショート生成が成功する
- [x] `uv run pytest` が全件通過し、新規 pip 依存・グローバル強制 link・字幕なし自動再試行が無い
- [x] 独立レビューで重大指摘が無い
- [x] タスク完了コミット済み

**見積もり目安:** 0.5 日

---

### S8: 区間内容の可視化 + プレビュー幅修正（v3.2・最優先）

**目的:** サブ区間提案（S6）の「人の確認」を実効化する。現状は提案区間の話している内容が画面に無く、境界を調整しようがない（実運用で「重要な部分が最後で切れる」が発生し投稿不採用になった）。あわせて縦動画プレビューが画面幅いっぱいに広がる問題を直す（FR-34 / AC-34）。
**フェーズ状態:** [x] 完了
**前提:** S6 実装コミット済み（`33b82a5`）・S7 完了。S6-9 の実機確認は本タスクの実機確認（AC-34「話が途中で切れないショートを 1 本作れる」）に統合して消化する。

**変更ファイル範囲:**
- `src/yt_live_kit/ui/components/short_cut.py`（区間テキスト表示、境界追従、`st.video` 幅）
- `src/yt_live_kit/ui/views/shorts.py` / `src/yt_live_kit/ui/components/shorts_queue.py` / `src/yt_live_kit/ui/views/highlights.py`（`st.video` の幅制限のみ）
- `tests/test_ui_short_cut.py`（テキスト抽出・境界追従の純粋関数テスト）
- `docs/execution-plan-v3.md`（進捗チェック）
- （`services/` は変更しない。VTT 区間抽出は [`services/subtitle_burn.py`](../src/yt_live_kit/services/subtitle_burn.py) の `parse_vtt_with_end()` / `filter_cues_for_segment()` / progressive 重複除去を **import して再利用**する）

**作業:**

- [x] S8-1. 区間テキスト抽出の純粋関数を `ui/components/short_cut.py` に実装する: `(vtt cues, start_ms, end_ms) -> 表示用テキスト`。既存の `parse_vtt_with_end()` + `filter_cues_for_segment()` + progressive 重複除去を再利用し、読みやすい行のリストへ整形する。VTT の読み込みは 1 回だけ行い、rerun ではキャッシュ（`st.session_state` または `st.cache_data`）を使う
- [x] S8-2. `_render_plan()` の各候補カードに、現在の境界入力値（`start_key` / `end_key` の編集後の値）に追従する文字起こしを表示する。境界が不正な間は元の提案区間のテキストを表示し、日本語でその旨を示す
- [x] S8-3. `st.video` の幅を制限する（4 箇所）。縦 9:16 動画が読みやすいサイズ（目安: 幅 320〜400px 相当。`st.video(width=...)` または列レイアウト）で表示されるようにする
- [x] S8-4. ユニットテスト: 区間抽出（境界一致 / 部分重なり / 空区間）、重複除去、境界変更への追従、不正境界時のフォールバック
- [x] S8-5. `uv run pytest` 全件通過。実機確認: 660 秒級候補 → 提案 → **テキストを読んで境界を調整** → 生成 → 話が途中で切れていないことを確認（S6-9 の残項目もここで消化: 通常 rerun で Codex が再実行されないこと、採否チェックと合計尺表示）。進捗チェック更新・コミット

**Done 条件:**

- [x] AC-34 の全項目が満たされている
- [x] AC-30 の残項目（採否チェック付き表示・合計尺と作成可否・Codex 非再実行・確定区間からの生成）が実機で確認され、S6 がクローズできる
- [x] `services/` に変更が無く、`uv run pytest` が全件通る
- [x] タスク完了コミット済み

**見積もり目安:** 0.5〜1 日

---

### U6: ショート生産ライン UI（v3.2 改訂）

**目的:** 動画詳細ページを「作業を選んで進めるページ」へ再構成し（FR-17 v3.2 / AC-31）、その中核であるショート作成ワークスペースを **FR-33 のショート生産ライン（工程 UI）** として実装する（AC-35）。
**フェーズ状態:** [~] 進行中
**前提:** S8 完了（ゲート①の確認材料が先に必要）。要件は FR-17 v3.2 + FR-31 + FR-33 / AC-31 + AC-35（2026-08-01 確定済み）。

**v3.2 での改訂内容:** v3.1 版 U6 の「量産（`render_shorts_queue`）を主導線に」という設計を撤回する。実測で切り抜き候補 142 / 142 本が 180 秒超であり量産フローに直接乗らないこと、および運用目標（§1.3.1 = ケイデンス×品質）が「1 本を仕上げる工程」を要求することが理由。3 ワークスペース骨格・状態サマリー・詳細・再生成・回復用空状態（AC-31）は v3.1 の計画のまま維持する。

**背景:** UX 監査（[`.codex/audits/video-detail-ux/audit.md`](../.codex/audits/video-detail-ux/audit.md)）で次の実測が出た。(1) 1280×720 環境でショート作成見出しまでスクロール 2,418px、(2) ステッパー CTA を押しても画面下方の expander が開くだけで結果が見えない、(3) 同じ CTA がステップごとにページ遷移 / ダイアログ / expander 展開 / API プレビュー開始と異なる動作をする、(4) 予約投稿（`ui/components/upload.py`）は量産 manifest しか読まないため単体生成ショートは投稿できないが、その制約が画面から読み取れない。独立コードレビューとの一致点である縦積み + 入れ子の廃止、チャプターの概要欄への統合は維持し、当初案の量産主導線だけを v3.2 の実測に基づき 1 本の工程ラインへ置き換える。

**設計上の決定（レビュー済み）:**

- 作業切り替えは `st.tabs` ではなく **`st.segmented_control`** を使う。S4（`ui/components/shorts_queue.py`）で動画 ID 付き key + `format_func` の実績があり、session state での選択保持・初期選択制御が確実なため
- 選択状態の key は必ず動画 ID を含める（動画 A / B の混在防止。FR-17 v3.2 の初期選択規則を純粋関数として実装しテストする）
- 状態カードはクリック不可・ボタン非配置。移動の入口は作業切り替えのみ。動画ヘッダーには単なる「形式: ショート（9:16）」表示を置かない
- 概要欄反映状態は既存 `description_applied_videos.json`（ID 配列）による「反映済み / 未反映」の 2 状態でよい。6 状態表示（要再反映・最新性不明）は U7 とともに保留（優先度③: チャプターは保守のみ）
- 3 ワークスペースは仕事の種類、6 工程は作成中ショート 1 本の状態とする。フル工程表示はショート作成内だけに置き、左パネルには縮約状態を常設する。手動切り替えはラインを破棄せず、副作用を起こさない。工程 6 の明示 CTA だけが対象を保持して公開・投稿へ移動する
- 左パネル上部はグローバルナビ、下部は「作成中のショート」とする。プレビュー・対象名・`工程 N／6`・次の確認・「本日のライン完了 N／3」を表示するが、編集・確定ボタンは置かない。狭幅または折り畳み時はメイン上部に縮約工程状態を残す
- 左プレビューは生成前 = 元動画の選択区間、生成中 = 進捗またはサムネイル、生成後 = 完成 mp4、元素材欠損 = 再取得案内へ切り替える
- 既存の確認境界（再生成 / 削除 / 概要欄 before・after / ショート上書き / 予約投稿の各 `st.dialog`）と `render_shorts_queue` の queue fingerprint / job ID 分離は変更しない。工程のゲートとして再利用する

**工程 UI の設計（FR-33。v3.2 追加）:**

- ショート作成ワークスペース = ショート 1 本の生産ライン。工程は「素材選定 → 区間決定〔ゲート①〕→ テロップ確認〔ゲート②〕→ 生成 → 最終確認〔ゲート③〕→ 予約」の 6 段階で、現在の工程と次の確認事項を常時表示する。各ゲートを通過するまで次工程に進めない判定は純粋関数として実装しテストする
- **接続（FR-31 統合）:** 区間決定（S6 のカットプラン確定区間列）からゲート②のテロップ生成（`services/telop.py`、S6 候補は `HighlightSegment` 型のため既存関数がそのまま受けられる）へ進める。テロップ確認・生成・予約は既存の S4 確定機構 / `build_short_from_segments()` / P2 予約フローを**組み合わせて**使い、新しい生成経路を作らない。既存 service 関数の組み合わせで不足が判明した場合は、実装前に計画改訂へ戻す
- **ゲート②の 4 分離:** 自動ハード判定（形式・必須値・時刻・区間整合）、自動警告（1 行 16 文字超）、人の全文確認（「台本全体の誤字・固有名詞を確認した」。既定未チェック）、生成条件（ハード判定通過 + 人確認済み + 現在 review fingerprint と確認時 fingerprint が一致）を別々に実装する。生成直前に現在値で再検証する
- **編集と差分:** 行別本文・時刻・行全体の強調トグルを編集できる。AI 案からユーザーが変更した箇所は「AI案から変更」と補助表示するが、差分だけで確認を完了させない。現行データで出所を証明できない「Codex が修正」表示はしない
- **失効:** review fingerprint は `(video_id, clip_id)`、既存 queue fingerprint、`TelopScriptDocument.model_dump(mode="json")` の canonical JSON から計算する。本文・強調・メタデータの変更で人確認を失効させ、一度編集して元に戻しても自動復帰させない
- **出力 fingerprint:** `output_fingerprint` は `video_id`、`clip_id`、生成に使った `review_fingerprint`、解決済み絶対パス、`st_size`、`st_mtime_ns`、mp4 内容 SHA-256 の canonical JSON から計算する。工程 6 直前にも再計算し、不一致・欠損時は台本確認ではなく最終プレビュー確認だけを失効させる
- **永続ライン状態:** `data/{video_id}/shorts/line/line_{clip_id}.json` を `(video_id, clip_id)` ごとの正本とする。`schema_version`、`video_id`、`clip_id`、`queue_fingerprint`、`review_fingerprint`、`review_confirmed_fingerprint`、`review_confirmed_at`、`output_fingerprint`、`preview_confirmed_fingerprint`、`preview_confirmed_at`、`current_stage`、`upload_operation_id`、`updated_at` を atomic 保存する。`active_line.json` に明示選択中の `clip_id` と更新日時を atomic 保存し、無効・欠落時は非完了 line を `updated_at` 降順、同値なら `clip_id` 昇順で復元する。非完了 line がなければ空状態とする。欠落・破損時は機械成果物から証明できる状態だけを復元し、人確認は未確認へ倒す
- **日次カウンター:** 現在の `SchedulePolicy.timezone`（未設定時は Asia/Tokyo）で `UploadOperation.created_at` を日付化し、同一 `(source_video_id, source_kind, clip_id)` の当日最新 operation のうち `reserved` / `uploading` / `uploaded` を完了数へ含める。`failed` / `needs_reconciliation` は除外し「要対応 N 件」と表示する。timezone 変更時は現在値で再集計する。America/Los_Angeles の upload attempt 日付は YouTube クォータ専用であり混同しない
- **既定値（毎回選ばせない）:** レイアウト・通常 / Hook プリセットは `ui/views/_local_settings.py` の既定値読み取り関数（P5 で編集 UI を追加。ファイル未設定時は現行既定 = blur / 既定プリセット）を使う。工程には読み取り専用の適用値と「設定で変更」だけを表示する
- 量産（`render_shorts_queue`）は「確定済み複数本の一括エンコード」の実行エンジンとして補助配置し、単体手動生成（時刻手入力）は補助のさらに奥に置く。工程の主導線には出さない

**視覚リファレンス:** [`references/u6-short-production-line-v3.2.png`](./references/u6-short-production-line-v3.2.png)。完成時の情報階層・左パネル・工程 3 の見え方・主要文言を合わせる。ただし状態遷移、失効、生成条件は要件書と本計画を正本とし、画像より本文を優先する。

**変更ファイル範囲:**
- `src/yt_live_kit/services/shorts_line.py`（U6 の限定 service 例外。ライン状態モデル、active pointer、review / output fingerprint、atomic 保存・読み込み、fail closed 復元、工程遷移・日次集計の純粋関数）
- `src/yt_live_kit/ui/app.py`（左パネルのグローバルナビ + 作成中ショート表示、折り畳み時の縮約状態）
- `src/yt_live_kit/ui/views/video_detail.py`（中核。ステッパー廃止、動画ヘッダー、状態サマリー、作業切り替え、3 ワークスペース条件描画、詳細・再生成、回復用空状態）
- `src/yt_live_kit/ui/views/shorts.py`（外側「縦型ショート動画」expander の除去。工程 UI を主導線に、量産・単体作成を補助領域へ再配置）
- `src/yt_live_kit/ui/components/shorts_line.py`（6 工程、左プレビュー、行別台本エディタ、品質表示、人確認チェック、CTA の表示部品）
- `src/yt_live_kit/ui/components/short_cut.py`（工程 UI の区間決定ステージとしての呼び出し境界整理。S8 の表示は変更しない）
- `src/yt_live_kit/ui/views/_local_settings.py`（レイアウト・プリセット既定値の読み取り関数を追加。編集 UI は P5）
- `src/yt_live_kit/ui/views/highlights.py`（素材候補ワークスペースから呼ぶための表示境界整理。「ハイライトまとめ動画」expander の外し込みと補助配置）
- `src/yt_live_kit/ui/components/upload.py`（「7. YouTube 予約投稿」見出しを外し公開・投稿カードへ統合。「生成ファイル」と「予約可能」の区別表示、単体生成のみの場合の量産導線案内）
- `src/yt_live_kit/ui/components/shorts_queue.py`（`st.divider()` + `### ` 見出しをワークスペース内の表示に合わせて調整。状態契約は変更しない）
- `src/yt_live_kit/ui/views/library.py`（`count_shorts` の再利用のみ。挙動変更なし）
- `tests/test_shorts_line.py`（ライン状態 round-trip、active line 選択、atomic / fail closed、fingerprint、編集時失効、日次集計、工程遷移）
- `tests/test_ui_video_detail_page.py`（状態サマリー計算、初期選択規則、ワークスペース条件描画、回復用空状態、詳細・再生成への移設）
- `tests/test_ui_app.py` / `tests/test_ui_shorts.py` / `tests/test_ui_highlights.py` / `tests/test_ui_upload.py` / `tests/test_ui_shorts_queue.py`（左パネル、expander / 見出し前提の期待値を新しい表示契約へ更新。確定・上書き・job ID 分離の非回帰確認）
- `README.md`（動画詳細ページの説明を新構成へ更新）
- `docs/references/u6-short-production-line-v3.2.png`（確定 UI の視覚リファレンス）
- （`services/` の変更は `services/shorts_line.py` の新設だけ。既存 service の挙動変更は禁止）

**作業:**

- [x] U6-1. 状態計算の作り替え: `calculate_progress_steps()`（旧 5 段ステッパー用）を廃止し、状態サマリー、FR-17 v3.2 の初期選択規則、日次完了 / 要対応数を返す純粋関数を実装しテストする
- [x] U6-2. 動画ヘッダー + 状態サマリー + 3 作業切り替え + 左パネルを実装する。`st.segmented_control` の key は動画 ID を含め、手動切り替えは描画切り替えだけにする。左パネルには工程別プレビューと縮約状態を表示し、編集ボタンは置かない。折り畳み時の代替状態をメイン上部に出す
- [x] U6-3. 回復用空状態: `load_result_from_disk()` が `None` の場合、通常ワークスペースを描画せず「保存済みの字幕成果物を読み込めませんでした」+ 原因説明 + 「取り込みで再処理」CTA（`st.switch_page` で取り込みへ。ジョブは開始しない）+ 詳細（動画 ID・存在する成果物）を表示する
- [x] U6-4. 素材候補ワークスペース: 切り抜き候補とハイライト候補をカード表示し、両方ある場合のみソース切り替えを出す。候補カードの「ショート作成対象へ追加」は、ライン開始前だけ動画 ID 別 session state に候補ソース・選択 ID・候補 fingerprint・選択順を保持し、ショート作成で同順に事前選択する。候補変更・再生成・ID 不在時は未確定選択を破棄して再選択を案内し、正式 snapshot / job / line state は区間列確定まで作らない。候補の生成・再生成は空状態または末尾の補助操作に置き、ハイライトまとめ動画の生成導線は補助として維持する
- [x] U6-4b. 実機フィードバック 2 件を解消する（S8 の実機確認 2026-08-01 で判明）。(a) **時刻入力のアシスト**: 現在は `HH:MM:SS` の生テキスト入力のみで調整しづらい。前後シフト（±1 / ±5 秒等）のボタン、または区間スライダー等のアシストを付ける。境界の正本は引き続き `parse_cut_timestamp()` → 整数ミリ秒正規化とし、UI 独自の丸めを入れない。(b) **開いた領域が勝手に閉じる / 先頭へ飛ぶ**: ボタン押下で全体が rerun され、`st.expander(expanded=False)` が既定値へ戻るために発生する。工程 UI では expander に主機能を置かない設計で構造的に解消するが、補助領域に残す expander には `key=` を付けて開閉状態を session_state で保持する（Streamlit 1.60 の stateful expander。`on_change="rerun"` との組み合わせを実装時に確認する）
- [x] U6-5. `services/shorts_line.py` を新設し、ライン状態の schema、active pointer と決定的 fallback、review / output fingerprint、atomic 保存、破損・欠落時の fail closed 復元、工程遷移、日次集計を実装する。queue fingerprint の既存計算は変更しない
- [x] U6-5b. ゲート②と接続を実装する: カットプラン確定区間列 → テロップ生成 → 行別編集 + 強調トグル → 自動ハード判定 / 警告 / 人の全文確認 → `build_short_from_segments()` → 工程別左プレビュー。編集時は人確認を必ず失効させ、生成直前に再検証する。既定値は読み取り専用表示とし、量産・単体作成は補助領域へ移す
- [x] U6-5c. ゲート③と工程 6 を実装する: 完成 mp4 の fingerprint と最終確認を結び付け、出力変更・欠損時に確認を失効させる。工程 6 の CTA でだけ対象とライン状態を保持したまま公開・投稿へ移動し、予約 operation ID を記録する
- [x] U6-6. 公開・投稿ワークスペース: (a) 元動画の概要欄カード — チャプター状態（U6 では生成済み・件数・形式 OK / 未生成 / 形式エラーと反映済み / 未反映）+ 「概要欄に反映」ボタン（既存 `_start_description_preview` → before / after ダイアログのフローを維持）。チャプター本文は表示しない。(b) ショートの予約投稿カード — `render_upload_section` を統合し、予約可能 0 本かつ生成ファイルありの場合は「生成済みですが予約対象に追加されていません」+ ショート生産ラインへの導線、両方 0 本の場合は「先にショートを作成してください」+ ショート作成への導線を表示する
- [x] U6-7. 詳細・再生成: 最下部の `st.expander("詳細・再生成", expanded=False)` 1 つに、字幕全文 + 全文コピー、チャプター本文 + タイムラインコピー（OAuth 未設定時の手貼りフォールバックとして維持）、チャプター / 切り抜き候補の再生成、元動画と中間ファイルの管理を集約する。各確認ダイアログは既存のまま維持する
- [x] U6-8. テスト更新: 状態 round-trip / active pointer / 複数 line の決定的 fallback / atomic / fail closed、全 fingerprint と各確認の失効、4 種の品質判定、日次 timezone と最新 operation 集計、6 工程遷移、左パネル 4 状態、3 ワークスペース、回復用空状態、既存確認ダイアログの非回帰を固定する
- [ ] U6-9. `uv run pytest` 全件通過、実ブラウザで確定リファレンスとの比較、左パネル 4 状態、折り畳み、3 ワークスペース、編集後の未確認化、生成・最終確認・予約まで 1 周を目視する。README・進捗チェックを更新して大タスクコミットする
- [ ] U6-10. ショート生成失敗契約のホットフィックス: テロップ生成前に既存 FFmpeg capability 検査を実行し、非対応時は job / queue snapshot を作らず生成工程と人確認を維持する。`shorts_queue` の全件失敗・部分失敗を成功表示せず、出力のない item を最終確認へ進めない。`short_cut` を既知の非 pipeline ジョブとして扱い、正常完了時は保存済み cutplan を画面側で再読込し、失敗時は元エラーを表示する。再現テスト、全件テスト、欠陥優先レビューを通して大タスクコミットする

**Done 条件:**

- [ ] AC-31 の全項目（工程 UI 前提で読み替えるもの以外）と AC-35 の全項目が満たされている
- [x] 初期表示で字幕全文・チャプター本文が描画されず、選択中の 1 ワークスペースだけが描画される
- [x] 作業切り替えに副作用が無く、実処理はワークスペース内の明示ボタンからのみ開始される
- [ ] ショート 1 本が工程の一本道（刻む → テロップ → 生成 → 確認 → 予約）で完成し、YouTube 自動字幕の生焼き込みが工程上発生しない
- [x] 人確認が既定未チェックで、台本編集後に失効し、再起動後も証明できない人確認が復元されない。出力変更・欠損では最終プレビュー確認だけが失効する
- [x] 左パネルに工程別プレビュー・縮約工程・次の確認・現在の `SchedulePolicy.timezone` 基準の日次完了数が常時表示され、操作入口が重複しない
- [x] 既存の確認ダイアログ・S4 の状態契約・FR-21 の安全契約に回帰が無い
- [x] `services/` の変更が新規 `services/shorts_line.py` だけで、既存 service の挙動に不要な変更が無い
- [x] `uv run pytest` が全件通る
- [ ] 実機でライン 3 周（3 本を予約まで）を通し確認済み
- [ ] タスク完了コミット済み

**見積もり目安:** 4〜5 日（P5 の 1 日と合わせてライン完成まで 5〜6 日）

---

### U7: 概要欄反映の最新性判定（v3.2 で保留）

> **v3.2 注記（2026-08-01）:** 運用目標の優先順位（§1.3.1: チャプター反映は優先度③・保守のみ）により、fingerprint 化（FR-21 v3.1 / AC-32）は **v4 候補へ保留**する。素材候補の選択引き継ぎ（FR-31）は **U6 / AC-31 へ移管済み**で、本 U7 の目的・変更範囲・作業・Done 条件には含めない。以下はチャプター反映だけの保留記録である。

**目的:** チャプター再生成後も「反映済み」に見える実データ上の不整合を、反映記録の fingerprint 化で解消する（FR-21 v3.1 / AC-32）。
**フェーズ状態:** [保留]
**前提:** U6 完了。

**変更ファイル範囲:**
- `src/yt_live_kit/ui/views/_local_settings.py`（反映記録を `{video_id: {fingerprint, applied_at}}` 形式へ拡張。旧 ID 配列の読み込み互換 = 「最新性不明」扱い。正規化済みチャプター本文の SHA-256 fingerprint 計算関数）
- `src/yt_live_kit/ui/views/video_detail.py`（概要欄カードをチャプター 6 状態表示へ拡張。`mark_description_applied` 呼び出しへ fingerprint・日時を渡す）
- `tests/test_ui_video_detail_page.py`（6 状態の表示分岐、fingerprint 一致 / 不一致 / 旧形式）
- （`services/youtube_api.py` は変更しない。FR-21 の安全契約を維持）

**作業:**

- [ ] U7-1. `_local_settings.py` の反映記録を拡張する。新形式は動画 ID → `{fingerprint, applied_at}`、旧配列は読み込み互換で「最新性不明」へマップする。保存は atomic 書き込み、fingerprint は正規化済みチャプター本文の SHA-256 とし、計算関数を純粋関数として切り出す
- [ ] U7-2. `update_video_description()` 成功後の記録処理へ fingerprint・UTC 日時を渡す。保存順序（YouTube 更新成功後のみ記録）は変更しない
- [ ] U7-3. 公開・投稿ワークスペースのチャプター状態表示を FR-21 v3.1 の 6 状態表（未生成 / 形式エラー / 生成済み・正常 / 最新を反映済み / 要再反映 / 最新性不明）へ拡張する。「要再反映」では反映 CTA を primary に戻す
- [ ] U7-4. テストを追加し、`uv run pytest` 全件通過・実ブラウザ確認（チャプター再生成 → 要再反映表示 → 再反映 → 最新反映済み表示）・進捗チェック更新・コミット

**Done 条件:**

- [ ] AC-32 の全項目が満たされている
- [ ] 旧形式の反映記録を持つ既存 47 本が読み込みエラーにならず「最新性不明」と表示される
- [ ] `services/` に変更が無く、`uv run pytest` が全件通る
- [ ] タスク完了コミット済み

**見積もり目安:** 1 日

---

### U8: エラー通知の構造化とページ先頭の整理

**目的:** ffmpeg 出力等の長い技術ログがページ先頭を占有する現状を廃止し、ジョブエラーを動画 ID 別の構造化通知として扱う（FR-32 / AC-33）。
**フェーズ状態:** [ ] 未着手
**前提:** U6 完了（エラー詳細の表示先である詳細・再生成領域が U6 で作られるため）。U7 とは独立で、並行着手可。

**変更ファイル範囲:**
- `src/yt_live_kit/ui/state.py`(動画 ID 別の構造化エラー通知（動画 ID / job ID / 処理種別 / 要約 / 詳細 / 発生日時）と保持上限（動画ごと直近 3 件）を追加)
- `src/yt_live_kit/ui/app.py`（`st.error(job_error)` による生ログ全文表示を廃止し、1 行要約 + 対象動画への導線に変更）
- `src/yt_live_kit/ui/components/status_bar.py`（長いエラーの要約表示と対象動画への導線）
- `src/yt_live_kit/ui/views/video_detail.py`（詳細・再生成領域に現在動画のエラー詳細（直近 3 件）を表示）
- `tests/test_ui_state.py`（構造化通知、保持上限、他動画との分離）
- `tests/test_ui_app.py` / `tests/test_ui_video_detail_page.py`（要約表示と詳細表示の期待値）
- （`services/jobs.py` の変更は原則不要。既存のエラー情報から UI 層で構造化する。job 側の情報が不足する場合のみ、エラー payload への項目追加に限って最小変更を許可する）

**作業:**

- [ ] U8-1. `ui/state.py` に構造化エラー通知を実装する。動画ごとに直近 3 件、動画に紐づかないエラーはグローバル要約のみ・上限付きで保持する
- [ ] U8-2. `ui/app.py` / `status_bar.py` の先頭表示を「（処理名）に失敗しました + 対象動画 + 再試行方法」の 1 行要約に変更する
- [ ] U8-3. 詳細・再生成領域に現在動画のエラー詳細（技術ログ含む）を表示する。他動画のエラーは表示しない
- [ ] U8-4. テストを追加し、`uv run pytest` 全件通過・実ブラウザ確認（長い ffmpeg エラーで先頭が占有されないこと）・進捗チェック更新・コミット

**Done 条件:**

- [ ] AC-33 の全項目が満たされている
- [ ] 長い技術ログがページ先頭・ステータスバーを占有しない
- [ ] `uv run pytest` が全件通る
- [ ] タスク完了コミット済み

**見積もり目安:** 1 日

---

### P5: 投稿枠の複数化 + ライン既定値の設定化（v3.2）

**目的:** 「毎日 3 本」を予約側で受けられるようにする。スケジュールポリシーを 1 日 1 枠（`daily_time`）から複数枠（`daily_times` リスト）へ拡張し、ショート生産ラインの既定値（レイアウト・プリセット）と枠リストを設定ページで編集できるようにする（FR-28 v3.2 / FR-20 v3.2 / AC-36）。
**フェーズ状態:** [ ] 未着手
**前提:** U6 完了（既定値の読み取り関数は U6 で先行実装済み。本タスクは編集 UI と枠拡張）。フェーズ P 系タスクのため `services/schedule.py` の変更を許可する。

**変更ファイル範囲:**
- `src/yt_live_kit/services/schedule.py`（`SchedulePolicy` を `daily_times` リスト対応へ拡張。旧 `daily_time` 単一値の読み込み互換 = 要素 1 個のリスト。`assign_next_slot` は日内の枠を時刻順に埋めてから翌 `interval_days` 日へ進む。重複時刻・不正形式は日本語エラー）
- `src/yt_live_kit/ui/views/settings.py`（枠リストの編集 UI、ショート既定値（レイアウト・通常 / Hook プリセット）の編集 UI）
- `src/yt_live_kit/ui/views/_local_settings.py`（既定値の保存関数を追加。保存先 `data/_config/short_defaults.json`）
- `tests/test_schedule.py`（複数枠の割り当て順、互換読み込み、重複・不正の拒否、DST 跨ぎ）
- `tests/test_ui_settings_page.py`（編集フォームが保存関数を正しく呼ぶこと）
- `docs/execution-plan-v3.md`（進捗チェック）

**作業:**

- [ ] P5-1. `SchedulePolicy` の `daily_times` 拡張と互換読み込み・検証（重複禁止・厳密 `HH:MM`・1 個以上）
- [ ] P5-2. `assign_next_slot` の複数枠対応（同日内は時刻順、埋まったら翌 `interval_days` 日。UTC 変換・aware datetime の既存契約を維持）
- [ ] P5-3. 設定ページに枠リスト編集とショート既定値編集を実装（保存は atomic。FR-27 の安全契約・確認ダイアログは変更しない）
- [ ] P5-4. テスト追加、`uv run pytest` 全件通過、実機で 3 枠設定 → 3 本予約が枠順に割り当たることを確認、進捗チェック更新・コミット

**Done 条件:**

- [ ] AC-36 の全項目が満たされている
- [ ] 既存の予約済み operation・queue.json に影響が無い（互換読み込みの自動テストあり）
- [ ] `uv run pytest` が全件通る
- [ ] タスク完了コミット済み

**見積もり目安:** 1 日

---

### S9: ローカル Whisper による字幕精度の底上げ（v3.2 追加・要件改訂が前提）

**目的:** テロップ品質の上限を決めている YouTube 自動字幕の精度を、ローカル Whisper による文字起こしで底上げする。
**フェーズ状態:** [ ] 未着手（**着手前に要件改訂が必要**。§13 スコープ外の「全本 Whisper 再文字起こし」と NFR-11 の依存方針に触れる）
**前提:** U6 完了後に着手する。理由は下記「先に U6 を通す理由」を参照。

**背景（実機で観測した事実）:** S8 の実機確認で、焼き込まれた字幕に固有名詞の誤認識が目立つことを確認した。実データ例（`data/LB4px1wRFnY`）では「クロード」が次行で「フロード」になり、「感覚**す**けど」のように助詞が脱落している。原因は YouTube 自動字幕（VTT）そのものの精度であり、区間の切り方や連結処理の問題ではない。

**先に U6 を通す理由:** U6 でサブ区間からテロップ台本生成（FR-22）へ接続すると、Codex が文脈から誤字修正・句読点付与・行分割を行い、人が確認・修正する工程が入る。誤認識のうち文脈から復元できるものはここで直る。**S9 に着手する前に、U6 通過後の実際の品質を測り、なお Whisper が必要かを判断する。** 先に測らずに大きな依存を増やさない。

**方式の候補（NFR-11 との整合が判断の中心）:**

| 方式 | 新規 pip 依存 | 従量課金 | 備考 |
|------|---------------|----------|------|
| `whisper.cpp` をサブプロセス呼び出し | **無し** | 無し | ffmpeg / yt-dlp / Codex CLI と同じ「外部バイナリを呼ぶ」既存パターンに乗る。Apple Silicon なら Metal で高速。**第一候補** |
| `faster-whisper`（pip） | 有り | 無し | NFR-11 の「新しい pip 依存を追加しない」に抵触するため要件改訂が必要 |
| OpenAI Whisper API | 無し | **有り** | NFR-11 の「従量課金 API を使わない」に抵触。**採用しない** |

**着手前に必要な意思決定（未確定）:**

- `whisper.cpp` の導入方法・モデルサイズ・処理時間の実測（1 時間の配信 1 本あたり何分か）
- 全 47 本の一括再文字起こしを行うのか、ショートに使う区間だけを対象にするのか（後者なら処理時間が現実的になり、§13 の「全本 Whisper 再文字起こし」スコープ外とも衝突しない）
- 固有名詞（Claude / Codex / 整体 等）を `initial_prompt` 相当で与えて認識精度を上げるか
- 既存 VTT との併存方針（既存パイプラインの入力は `subtitles/ja.vtt`。差し替えるのか別ファイルにするのか）

**見積もり目安:** 未確定（要件改訂 + 実測後に見積もる）

---

## 7. 出力ディレクトリ・ソースコード構成（v3 追加分）

### 7.1 ソースコード構成

```
src/yt_live_kit/ui/
  app.py                    # st.navigation によるページ登録 + U6 の左パネル工程コンテキスト
  state.py                  # session_state ヘルパー（v2 から継続）
  views/                    ← v3（U0 で pages/ から改名）
    library.py              # U1: ライブラリページ
    video_detail.py         # U2: 動画詳細ページ（U6 で作業選択型 IA へ再構成）
    intake.py               # U3: 取り込みページ（旧 channel.py + run.py の統合）
    settings.py             # U4: 設定ページ
    _local_settings.py      # U1 で新設・U3 で追記: アーカイブ状態・チャンネル既定ハンドルの軽量永続化（services を使わない例外）
    highlights.py           # v2 から継続。呼び出し元が video_detail.py に変わる
    shorts.py                # v2 から継続。build_short_from_segments 呼び出しは S4 で追加
    # history.py は U5 で削除。行アクションは video_detail.py へ統合
  components/
    clipboard.py             ← v3（U2 で results.py から移設）
    storage_manager.py       ← v3（U5。旧 history.py のストレージ管理を設定ページから利用）
    upload.py                ← v3（P2。予約 preview / confirm / operation 復元表示だけ）
    short_cut.py             ← v3（S6。サブ区間提案の生成 / 採否 / 境界調整 / 連結生成の導線）
    shorts_line.py           ← v3（U6。6 工程・左プレビュー・台本確認ゲートの表示）
    results.py                # v2 から継続。クリップボード関数は clipboard.py に委譲
    status_bar.py             # v2 から継続。U5 で案内先をライブラリへ更新

src/yt_live_kit/models/
  upload.py                  ← v3（P1。channel / content snapshot / operation / result）
  short_cut.py               ← v3（S6。カットプラン文書）

src/yt_live_kit/services/
  youtube_api.py             # v2 から継続。P1 で mine channel / resumable upload / poll を追加
  upload_queue.py            ← v3（P1。operation / attempt / job target / reconciliation）
  schedule.py                ← v3（P2。IANA policy / slot queue / confirm transaction）
  short_cut.py               ← v3（S6。親区間内カットプランの生成 / 検証 / 保存）
  shorts_line.py             ← v3（U6 限定例外。永続ライン状態 / fingerprint / 遷移判定）
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
│   ├── queue.json                  # full operation + 予約枠の単一正本（snapshot / 状態 / job・video ID / poll 履歴）
│   └── upload_attempts.json        # America/Los_Angeles の実試行日で数える全 attempt
└── {video_id}/
    ├── ...（v1/v2 と同じ）
    └── shorts/
        ├── telop/                  ← v3（S1）
        │   └── telop_{clip_id}.json    # テロップ台本 + フック文言 + タイトル案 + 説明文 + タグ
        ├── cutplan/                ← v3（S6）
        │   ├── cut_{parent_id}.json     # 親候補内のサブ区間提案（人が確認する前の AI 提案）
        │   └── cut_{parent_id}.prompt.md # Codex 未導入時の手動フォールバック用プロンプト
        ├── line/                   ← v3（U6）
        │   ├── line_{clip_id}.json      # 工程・確認 fingerprint・upload operation ID の atomic 正本
        │   └── active_line.json         # 左パネルへ表示する明示選択中 clip_id の atomic pointer
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
                        └─ P1 安全な upload 契約・永続 operation（全 API mock）
                            └─ P2 スケジュール・原子的 confirm・UI（全 API mock）
                                ├─ P0 同じ本番経路で実機 probe（明示承認待ちは開発を止めない）
                                └─ P3 実予約公開受け入れ（P0 の lock 解消後、別承認）

（v3 受け入れ後の追加タスク）
P3 完了（0.3.0）
 ├─ P4 ショート概要欄の定型リンク差し込み（完了）
 ├─ S7 FFmpeg 環境検査（完了）
 └─ S6 サブ区間提案（実装済み）
     └─ S8 区間内容の可視化 ★最優先。S6 の実機確認を吸収してクローズ
         └─ U6 ショート生産ライン UI（v3.2。3 ワークスペース骨格 + 工程 UI + 接続）
             ├─ P5 投稿枠の複数化 + 既定値設定  → 実機ライン 3 周で M15 達成
             └─ U8 構造化エラー通知（P5 と独立・並行可）

U7（概要欄 fingerprint 化）は保留 = v4 候補（優先度③: チャプターは保守のみ）
```

**順序の根拠:**

| 判断 | 理由 |
|------|------|
| U0 を最優先 | サイドバー事故を残したまま機能を積むと、あとで UI 全体を作り直す二度手間になる |
| U2 を U フェーズの中核に置く | フェーズ S の全機能（テロップ確認・キュー量産・予約投稿ボタン）はこのページに積まれる。土台が粗いと S フェーズが総崩れになる |
| S1・S2 を S3 より先に | S3（連結ショート生成）はテロップ台本（S1）とスタイルプリセット（S2）の両方を利用する |
| P1 / P2 を P0 より先にする | 実機 probe のための危険な最小経路を作らず、private / publishAt / resumable / attempt / operation / confirm race をモックで完成させた同じ本番経路だけを実操作に使う |
| P0 と P3 を分ける | P0 は lock / processing の probe、P3 は実公開の受け入れで目的と承認対象が異なる。審査待ちでも P1 / P2 は止めない |
| S8 を最優先にする | ゲート①（区間の中身確認）の材料が無いと、工程 UI（U6）を作っても品質を担保できない。かつ S8 だけで「話が切れないショートを作れる」ようになり、今日の運用が先に改善する |
| U6 を S8 完了後にする | 工程 UI はゲート①の確認材料（S8）を前提とする。また S6 / S7 と同じ UI ファイルを触るため、両者のクローズ後に大規模変更を行う |
| P5 を U6 の後にする | 既定値の読み取りは U6 で先行し（ファイル未設定時は現行既定）、編集 UI と枠拡張を P5 でまとめる。U6 は既存 service を変更せず新規 `services/shorts_line.py` だけ、P5 は既存 `services/schedule.py` の変更ありと、レイヤ制約の異なる作業を別 diff に分ける |
| U7 を保留にする | 運用目標の優先度③（チャプターは保守のみ）。候補引き継ぎ部分は U6 の工程接続に統合済みで、独立タスクの意味が消えた |

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
| P1 安全なアップロードサービス | 2.5 日 |
| P2 スケジュールポリシー + 原子的な予約確定 | 2.5 日 |
| P0 安全な実機 upload probe | 0.5 日 |
| P3 フェーズ P 受け入れ | 1 日 |
| **合計** | **約 20 日**（1 人、AI 支援あり。P0 の承認・審査待ち日数は含まない） |

**v3 受け入れ後の追加タスク（0.3.0 以降）:**

| タスク | 見積もり |
|--------|----------|
| P4 ショート概要欄の定型リンク差し込み | 済み |
| S6 サブ区間提案 | 1.5 日 |
| S7 FFmpeg 環境検査 | 0.5 日 |
| S8 区間内容の可視化 + プレビュー幅修正 | 0.5〜1 日 |
| U6 ショート生産ライン UI | 4〜5 日 |
| P5 投稿枠の複数化 + 既定値設定 | 1 日 |
| U8 構造化エラー通知 | 1 日 |
| U7 概要欄反映の最新性判定 | 保留（v4 候補） |

**段階リリース案:**

| リリース | 含むタスク | 累計 | 内容 |
|----------|-------------|------|------|
| **0.2.1** | U0〜U5 | 6.5 日 | 迷わない UI に生まれ変わる |
| **0.2.2** | + S1〜S5 | 13.5 日 | テロップ付きショートが量産できる |
| **0.3.0** | + P1 → P2 → P0 → P3 | 20 日 | 安全契約・実機 probe・予約公開まで含む v3 完了 |
| **0.3.1** | + P4・S6・S7・S8 | — | ショート導線の追加要件・ホットフィックス・区間内容の可視化 |
| **0.4.0** | + S8 → U6 → P5（+ U8） | — | 毎日 3 本のショート生産ラインが確立する（v3.2） |

---

## 10. マイルストーン定義

| マイルストーン | 判定 |
|----------------|------|
| **M11: サイドバーの事故導線が消える** | U0 完了。サイドバーに内部モジュール名が出ない |
| **M12: 動画軸で迷わず作業できる新 IA が揃う** | U5 完了。公開 3 画面 + 非表示詳細の 4 画面構成が完成し、設定のストレージ管理、ステッパー、確認ダイアログ、差分プレビューが動く |
| **M13: テロップ付きショートが量産できる** | S5 完了。複数区間を連結したショートがテロップ・フックタイトル付きで複数本まとめて作れる |
| **M14: 予約投稿が実際に公開される（v3 完了）** | P3 完了。AC-18〜AC-28 充足、実際の予約公開を確認済み |
| **M15: 毎日 3 本のショート生産ラインが確立する（v3.2）** | S8（AC-34）→ U6（AC-31 / AC-35）→ P5（AC-36）完了。実機でライン 3 周（3 本を予約まで）を通し確認済み。U8（AC-33）は安定性の仕上げとして並行 |

---

## 11. テスト計画（フェーズ横断）

| 種類 | 対象 | タイミング |
|------|------|------------|
| ユニット | U6 の状態サマリー・初期選択・6 工程遷移・review / output fingerprint・編集時失効・atomic / fail closed ライン状態・timezone 日次集計、`_local_settings` の永続化、テロップ台本バリデータ、累積タイムオフセット計算、`assign_next_slot`、metadata / audience / synthetic / consent、resumable retry、operation / attempt / confirm race、kind 別 status bar、反映記録 fingerprint（U7）、構造化エラー通知（U8） | 各タスク内 |
| 回帰 | v1 / v2 の既存テスト（`tests/` 全件）が通ること | 各タスク末 |
| 実機結合 | 公開アーカイブ 2 本（V7-2 を U5 / S5 で同じ 2 本を通して確認）、実アップロード 1 本（P0 / P3） | U5 / S5 / P0 / P3 |
| 目視確認 | サイドバー導線（U0）、確認ダイアログ（U5）、確定リファレンスと左パネル 4 状態・6 工程・編集時失効（U6）、テロップ可読性・つなぎ目（S5）、YouTube 上の実際の公開挙動（P3） | 各フェーズ受け入れ |
| UI 受け入れ | README 手順どおりの全機能操作（P3） | P3 |

**方針:**

- yt-dlp / ffmpeg / Codex CLI / `googleapiclient` の実行は**すべてモックする**（`subprocess.run` および `googleapiclient` のクライアントをパッチ）。P0 / P3 の実 API はユーザー承認後の手動受け入れだけで行い、自動テストへ含めない
- 動画を実際に生成・アップロードするテストは CI 必須にしない（手動・任意）
- **v1 / v2 の既存テストを 1 件も壊さないこと。** 壊れた場合は後方互換の設計ミスとして扱う

---

## 12. リスクと計画上の対策

| リスク | 計画上の扱い |
|--------|--------------|
| フェーズ U で UI を作り替えている最中に、v2 機能が回帰する | 各タスクで `uv run pytest` を必須化し、`services/` を原則変更しないことで回帰範囲を限定する。U3 は `services/batch.py` の入力伝播、U6 は新規 `services/shorts_line.py` の安全状態だけを例外とし、既存 service の挙動を変えない |
| 非公開ロックにより予約投稿が機能しない | P1 / P2 の安全契約を先に完成し、P0 は同じ経路で probe する。private lock は成功扱いにせず、審査待ちでも P1 / P2 の実装は止めない |
| YouTube Data API の upload 上限超過 | 予約公開日でなく America/Los_Angeles の実 attempt 日を API session 前に atomic 記録し、失敗も数える。`YTLK_VIDEO_UPLOAD_DAILY_LIMIT`（1〜100）を守り、予約 slot と別集計にする |
| network / 5xx retry が重複動画を作る | 同一 resumable session の `next_chunk()` だけを bounded retry し、4xx / 結果不明は新規 insert を行わず `needs_reconciliation` へ止める |
| 確認後に channel / file / slot / quota が変わる | immutable preview を確定後に再検証し、固定 lock 順序の atomic transaction で slot と operation を確定する。競合時は新しい preview を要求する |
| confirm 保存後に process が落ち、job / attempt と食い違う | operation ID / job ID を queue の単一 record へ先行保存し、job JSON は thread 前に同じ ID で作る。attempt ledger を外部効果開始の正本とし、`close_orphans()` 後の active state は attempt 無しを failed + slot 解放、attempt 有りまたは ledger 読込不能を needs_reconciliation + slot 保持へ倒す。terminal 不整合は非変更 + 全新規 upload fail closed とする |
| 一般 uploader の要件と scheduled-only scope が混同される | 本機能は AGENTS.md / requirements-v3 の予約投稿専用 feature であり、`private + publishAt` 固定を維持する。即時 `public` / `unlisted` は実装せず、将来一般 uploader を作る場合は別要件・別安全レビューにする |
| 子ども向け / 合成メディア / ガイドライン確認が暗黙値になる | Made for Kids と synthetic media は毎回必須選択し、Community Guidelines checkbox は既定未チェックにする。preview / snapshot / API body の一致をテストする |
| ジャンプカット連結で累積オフセット計算を誤り、字幕がずれる | S3 のユニットテストで 3 区間以上・区間間隔ありのケースを必須検証項目にする |
| Codex CLI の応答が不安定（テロップ台本） | S1 は例外を送出し、S4 等の呼び出し側が生成対象単位で局所捕捉する。失敗時は既存の同一 telop と他の成果物（チャプター・候補・ハイライト）を変更しない |
| テロップの自動生成品質が低く、そのまま焼き込むと粗が目立つ | S1 で必ず人の確認・修正ステップを挟む（自動焼き込みにしない） |
| 台本確認後の編集や再起動で、古い確認状態のまま生成できる | review fingerprint と確認 fingerprint の一致を生成条件にし、編集時は失効、生成直前は再検証する。ライン状態は atomic 保存し、壊れた状態・証明できない人確認は未確認へ倒す |
| 「本日」の基準が YouTube クォータ日と混ざる | 生産ラインは `SchedulePolicy.timezone`、upload attempt 上限は America/Los_Angeles と用途を分離し、境界時刻のテストを固定する |
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

1. **P1 に着手する。** 実 API をすべてモックし、安全な upload / operation / attempt / jobs / status bar 契約を完成させる
2. P1 完了後に P2 の schedule / confirm transaction / UI を実装し、同時確定・二重クリック・再起動をモックで検証する
3. P1 / P2 のレビュー・コミット後、実 upload の明示承認が得られた時だけ P0 を行う。承認・審査待ち中も開発完了状態を維持する
4. private lock 解消後、P0 とは別の明示承認を得て P3 の予約公開受け入れを行う

**P1〜P3 完了後（0.3.0 リリース済み）の次アクション:**

1. ~~**P4 に着手する。**~~ 完了。~~**S7 を閉じる。**~~ 完了（`9ebf251`）
2. ~~**S8 に着手する（最優先）。**~~ 完了。実機確認で S6-9 を吸収し、S6 / S8 をクローズ済み
3. **U6 に着手する。** ショート生産ライン UI（FR-17 v3.2 + FR-31 + FR-33 / AC-31 + AC-35）。確定リファレンスと状態遷移表を正本として 4〜5 日で実装する
4. U6 完了後、**P5**（投稿枠の複数化 + 既定値設定、AC-36）。実機でライン 3 周を確認し M15 を達成する
5. **U8**（構造化エラー通知、AC-33）は U6 完了後いつでも着手可（P5 と独立）。**U7 は保留（v4 候補）**
6. **S9**（ローカル Whisper）は U6 完了後にテロップ品質を実測してから、着手可否と方式を判断する。着手する場合は先に要件改訂（NFR-11 と §13 スコープ外の見直し）を行う

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-01 | **U6 確定仕様を計画化。** 3 ワークスペースと 6 工程の責務、左パネルの常設プレビュー、品質チェックの 4 分離、review fingerprint と編集時失効、atomic / fail closed な `services/shorts_line.py`、`SchedulePolicy.timezone` 基準の日次完了数を固定。U6 の見積もりを 4〜5 日へ改訂し、完成イメージを視覚リファレンスとして追加。あわせて S7 / S8 本文のフェーズ状態を進捗サマリーどおり完了へ整合 |
| 2026-08-01 | **S6 / S8 クローズ。** S8 実装（`5bcad1c`）後の実機確認で AC-30 の残 4 項目と AC-34 の全項目を充足し、S6 / S8 を完了にした。実機で新たに判明した 2 件を U6-4b に追加（時刻入力のアシスト、rerun で expander が閉じて先頭へ飛ぶ問題 = stateful expander の `key=` で解消）。字幕精度の底上げ要求を **S9（ローカル Whisper）** として登録し、方式は whisper.cpp のサブプロセス呼び出しを第一候補（NFR-11 の pip 依存・従量課金の両制約を満たすため）、着手は U6 通過後の品質実測の後と定義した |
| 2026-08-01 | **v3.2 生産ライン改訂。** 運用目標を「毎日 3 本のケイデンス×品質」（requirements-v3.md §1.3.1）に固定したことを受けて計画を再編。S8（区間内容の可視化・最優先。S6 実機確認を吸収）と P5（投稿枠の複数化 + 既定値設定）を新設し、U6 を「作業選択型 IA + FR-33 工程 UI」（量産主導線の撤回、刻む→テロップ→生成→予約の接続、既定値化、3 日）へ改訂。U7 は保留 = v4 候補（候補引き継ぎは U6 に統合）。M15 を「生産ライン確立」に再定義し、実装順を S8 → U6 → P5（U8 並行）とした |
| 2026-08-01 | **v3.1 UI 改訂を計画化。** 動画詳細ページの UX 監査（`.codex/audits/video-detail-ux/audit.md`）と独立コードレビューの一致点に基づき、U6（作業選択型 IA への再構成）/ U7（反映記録 fingerprint 化 + 候補引き継ぎ）/ U8（構造化エラー通知）を新設。U6 は S6 / S7 完了後着手、見た目の再構成（U6）とデータ形式移行（U7）を別 diff に分離、既存の確認境界・S4 状態契約・FR-21 安全契約は全タスクで非回帰とすることを固定。M15 と 0.3.1 / 0.4.0 リリース案を追加 |
| 2026-08-01 | v3 受け入れ完了後の追加要件として S6（切り抜き候補からのショート用サブ区間提案、FR-30 / AC-30）を定義。長い候補を刻む経路が既存 UI に無いことを背景として明記し、提案 service の純粋関数化、明示 submit 時のみの Codex 呼び出し、FR-25 と同一の境界正規化、既存導線の非回帰を計画上固定 |
| 2026-08-01 | v3 受け入れ完了後の追加要件として P4（ショート概要欄の定型リンク差し込み、FR-29 / AC-29）を定義。長尺用テンプレートと分離し、preview 生成前の合成と日本語 fail closed を計画上固定 |
| 2026-08-01 | P 安全監査の独立レビューを反映。P0 の公開可能性を承認範囲へ追加し、slot + full operation を単一 queue record へ統合、job ID 先行予約とクラッシュ recovery / fault injection、poll 回数・間隔・terminal・private lock 判定表、P1 → P2 → P0 → P3 の必須順序を固定 |
| 2026-08-01 | P0〜P3 着手前安全監査を反映。P1 / P2 の本番安全契約を実機 probe より先に変更し、private 固定、aware `publishAt`、Made for Kids / synthetic media 必須選択、Community Guidelines 同意、metadata 制約、resumable / reconciliation、LA attempt 台帳、永続 operation、confirm race、jobs / status bar、polling、実操作別承認を固定。一般 uploader ではなく scheduled-only feature として即時 public / unlisted を対象外のまま維持 |
| 2026-08-01 | S4 着手前監査・計画レビューを反映。form 外 source と表示順、明示 Codex submit、pure service、deep immutable spec、完全 fingerprint、正規化台本返却、manifest 単独 writer、job ID / latest 表示境界、決定的出力名とテスト境界を固定 |
| 2026-08-01 | S3 着手前監査・計画レビューを反映。共通整数 ms 正規化 API、入力順、二重検証境界、累積字幕、VTT / Hook fallback、全入力 preflight、進捗契約、force_style 分離、安全な出力名、固定ログ名、atomic replace、中間物 cleanup、既存出力保護を固定 |
| 2026-08-01 | S2 着手前監査・計画レビューを反映。`TimedCue.emphasis`、ASS プリセットの完全な型・色導出、既定出力互換、フック固定時間、安全化順序、同一 ASS 統合、S3 への preset 伝播と `force_style` 分離を固定 |
| 2026-08-01 | S1 着手前監査を反映。テロップ JSON の絶対秒スキーマ、公開シグネチャ、検証結果型、プロンプト保存先、`make_clip_id()` のミリ秒丸め、VTT parser の公開互換、softfail 境界を固定 |
| 2026-08-01 | PLAN0-7: U5 着手前監査を反映。正式 IA を公開 3 画面 + 非表示詳細に固定し、旧処理済み一覧の削除、ストレージ管理の設定画面移設、概要欄更新経路・成功記録・安全な受け入れ境界を明確化 |
| 2026-08-01 | PLAN0-6: 実装前監査の指摘を反映。YouTube granular quota を 100 uploads/day に更新し、進捗更新権限・概要欄完了記録・`clip_id`・S4 確認境界・P1 ffprobe 範囲を明確化 |
| 2026-08-01 | v3 実行計画の初版作成。PLAN0、U0〜U5、S1〜S5、P0〜P3 を定義。v2 未完了タスクの仕分けを実施 |
