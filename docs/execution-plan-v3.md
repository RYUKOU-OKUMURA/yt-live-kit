# yt-live-kit 実行計画書 v3

**バージョン:** v3（ショート量産・投稿）
**最終更新:** 2026-08-04
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
| U6 | ショート生産ライン UI（v3.2 改訂: 作業選択型 IA + 工程 UI） | [x] 完了 |
| P5 | 投稿枠の複数化 + ライン既定値の設定化（v3.2） | [x] 完了 |
| R1 | 全体リファクタリング・性能・長期運用監査 | [x] 完了 |
| H1 | 長期運用 hardening | [x] 完了 |
| G1 | FFmpeg single-pass benchmark | [x] 完了 |
| U7 | 概要欄反映の最新性判定（保留: 優先度③のため v4 送り。候補引き継ぎは U6 に統合） | [保留] |
| U8 | エラー通知の構造化とページ先頭の整理 | [x] 完了 |
| S9-PLAN | S9 要件・依存順計画の確定（docs-only） | [x] 完了 |
| S9-0 | 既存 VTT 互換・非上書き保存契約 | [x] 完了 |
| S9-1 | 代表素材 benchmark・モデル決定 | [x] 完了 |
| S9-2 | TranscriptArtifact / resolver / fingerprint / persistent cache | [x] 完了 |
| S9-3 | whisper.cpp runtime・capability・音声区間準備 | [x] 完了 |
| S9-4 | 親候補区間 Whisper 精査 → short_cut / telop / line 再利用 | [x] 完了 |
| S9-5 | UI 設定・進捗・エラー・失効表示 | [x] 完了 |
| T1-PLAN | テロップ行時刻同期計画（docs-only） | [x] 完了 |
| T1 | テロップ行時刻同期・明示確認 | [~] 進行中 |
| T1-1 | production 非変更 timing spike・評価 manifest | [ ] 未着手 |
| T1-2 | timing 保存契約・extractor | [ ] 未着手 |
| T1-3 | pure aligner・telop・fingerprint 統合 | [ ] 未着手 |
| T1-4 | Streamlit UI 時刻確認 gate | [ ] 未着手 |
| T1-5 | 同期 component acceptance | [ ] 未着手 |
| S9-6 | A/B 受け入れ・回帰・フェーズ判定 | [~] 進行中 |
| S9 | 選択親候補区間のローカル Whisper 精査（実装） | [~] 進行中 |
| P6-PLAN | Shorts 投稿メタデータ品質ゲート計画（docs-only） | [x] 完了 |
| P6-1 | タイトル 3 方向生成・検証 | [x] 完了 |
| P6-2 | 概要欄必須構成・投稿前再検証 service | [x] 完了 |
| P6-3 | 関連動画の Studio 手動確認・永続状態 | [x] 完了 |
| P6-4 | 投稿 UI 統合・確認ダイアログ | [x] 完了 |
| P6-5 | P6 統合受け入れ・回帰 | [x] 完了 |
| P6 | Shorts 投稿メタデータ品質ゲート + 関連動画確認追跡 | [x] 完了 |
| R2 | UI 大幅刷新前の境界整理・回帰リスク監査 | [x] 完了 |
| U9 | UI 視覚刷新（テーマ適用 + shell 刷新） | [ ] 未着手 |

**状態の書き方:** `[ ] 未着手` / `[~] 進行中` / `[x] 完了`

**P3 と H1-5 の区別:** P3 は承認済み 1 本で公開前後の bounded poll と実公開を確認した受け入れ履歴として完了を維持する。R1 で、通常予約 operation には同じ publication poll を起動する導線がないことが判明したため、反復可能な production 要件は H1-5 と AC-27 / AC-28 の未完了項目として追跡する。

**マイルストーン:**

| ID | 内容 | 状態 |
|----|------|------|
| M11 | サイドバーの事故導線が消える（U0 完了） | [x] |
| M12 | 動画軸で迷わず作業できる新 IA が揃う（U5 完了） | [x] |
| M13 | テロップ付きショートが量産できる（S5 完了） | [x] |
| M14 | 予約投稿が実際に公開される（v3 完了・P3 完了） | [x] |
| M15 | 毎日 3 本のショート生産ラインが確立する（S8 → U6 → P5 完了、実機でライン 3 周） | [x] |
| M16 | 親候補探索は VTT、選択区間は provenance 付き Whisper artifact で精査できる | [ ] |
| M17 | 投稿前のタイトル・概要欄と、アップロード後の関連動画設定を人が保証できる（P6 完了） | [x] |

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
          transcript_artifact, whisper_runtime,
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
- S9 の transcript artifact、Whisper runtime、cue digest、resolver、cache 失効は `services/` と `models/` に置き、UI は設定・進捗・明示操作・日本語エラーの表示だけを担う。`ja.vtt` の直接読込を UI に増やさない

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
- S9-0 `services/ytdlp.py` の既存 VTT 非上書き保存 + source artifact
- S9-1 benchmark / model decision（production 非変更の計測証跡）
- S9-2 `models/transcript.py` + `services/transcript_artifact.py`
- S9-3 `services/whisper_runtime.py` + 音声区間準備
- S9-4 `short_cut.py` / `telop.py` / queue / line の artifact 再利用統合
- S9-5 S9 UI / 進捗 / 失効表示
- S9-6 benchmark / acceptance と S9 フェーズ判定

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
| **R1** | 全体リファクタリング・性能・長期運用監査 | 回帰基準、即時の fail-closed 修正、rerun 高速化、構造課題の優先順位 | 既存 FR / NFR / AC の非回帰 |
| **H1** | 長期運用 hardening | jobs 排他、path confinement、queue crash recovery、atomic persistence、公開後 poll 接続 | FR-26, FR-27, NFR-13, AC-26〜28 |
| **G1** | FFmpeg single-pass benchmark | 代表素材で現行 2 段 encode と single-pass 試作を比較し、採否を決める | FR-25 / AC-25 の変更前検証 |
| **U7** | （保留）概要欄反映の最新性判定 | 優先度③（保守のみ）のため v4 送り。候補引き継ぎ部分は U6 の工程接続に統合済み | FR-21 v3.1, AC-32 |
| **U8** | エラー通知の構造化とページ先頭の整理 | 動画 ID 別の構造化エラー、要約表示、技術ログの詳細領域集約 | FR-32, AC-33 |
| **S9-PLAN** | S9 要件・計画の確定 | VTT と選択区間 Whisper の責務分離、artifact / fingerprint / cache / 失効、依存順の docs-only 更新 | S9-PLAN |
| **S9-0** | 既存 VTT 互換・非上書き保存契約 | incoming の隔離、既存 `ja.vtt` の bytes 保持、source VTT の immutable 保存、失敗時非変更 | FR-35, AC-37 |
| **S9-1** | 代表素材 benchmark・モデル決定 | 代表素材 A/B、gold transcript / 固有名詞表、精度・時間・memory gate、Go / No-Go | NFR-11, FR-36, AC-37 |
| **S9-2** | TranscriptArtifact / resolver / fingerprint / persistent cache | strict schema、候補 lineage、用途別 resolver、range digest、atomic index | FR-30, FR-35, AC-30, AC-37 |
| **S9-3** | whisper.cpp runtime・capability・音声区間準備 | capability preflight、音声 only adapter、複数 span の 1 job serial 処理 | NFR-11, FR-36, AC-37 |
| **S9-4** | 親候補 Whisper 精査と short_cut / telop / line 再利用 | 同一 artifact snapshot の cutplan / telop / queue / line / review 伝播 | FR-22, FR-25, FR-30, FR-33, AC-35, AC-37 |
| **S9-5** | S9 UI / 進捗 / 失効表示 | 設定、CTA、job / range 進捗、日本語エラー、fallback、範囲単位失効 | FR-33, FR-35, FR-36, AC-35, AC-37 |
| **S9-6** | A/B 受け入れ・回帰・フェーズ判定 | 再現 benchmark、cache / failure injection、実機、Go / No-Go、scope guard | AC-30, AC-35, AC-37 |
| **S9** | 選択親候補区間のローカル Whisper 精査 | 代表素材 benchmark、TranscriptArtifact、whisper.cpp runtime、cutplan / telop 再利用、UI、A/B 受け入れ | FR-35, FR-36, AC-30, AC-35, AC-37 |
| **P6-PLAN** | P6 要件・計画・writer 境界の確定 | FR / AC、タスク分解、変更範囲、並列時の単一 writer 規約 | FR-37, FR-38, AC-38, AC-39 |
| **P6-1** | タイトル 3 方向生成・検証 | 固定順 3 候補、legacy 読み込み互換、文字数警告 | FR-23, FR-37, AC-23, AC-38 |
| **P6-2** | 概要欄必須構成・投稿前再検証 service | 既定テンプレート、必須項目 validator、final body 再検証 | FR-29, FR-37, AC-29, AC-38 |
| **P6-3** | 関連動画の Studio 手動確認・永続状態 | operation schema、atomic 状態遷移、Studio handoff | FR-38, AC-39 |
| **P6-4** | 投稿 UI 統合・確認ダイアログ | タイトル警告、最終概要欄 gate、関連動画チェックリスト | FR-27, FR-37, FR-38, AC-27, AC-38, AC-39 |
| **P6-5** | P6 統合受け入れ・回帰 | API mock、全件テスト、scope guard、独立レビュー | AC-38, AC-39 |
| **P6** | Shorts 投稿メタデータ品質ゲート + 関連動画確認追跡 | タイトル 3 方向、必須概要欄、upload 後の関連動画人確認 | FR-37, FR-38, AC-38, AC-39 |
| **R2** | UI 大幅刷新前の境界整理・回帰リスク監査 | UI view model、widget state 契約、候補 lineage、投稿安全境界の characterization | 既存 FR / NFR / AC の非回帰 |
| **U9** | UI 視覚刷新（テーマ適用 + shell 刷新） | ネイティブテーマ適用、AppTest 視覚回帰スモーク、sidebar / header / KPI カード / 工程 bar の shell 刷新 | FR-33、AC-35、既存 FR / NFR / AC の非回帰 |

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
- [x] P2-10. 投稿ごとの予約日時編集を追加する。現在の次の空き枠を初期値として、公開・投稿カードで policy timezone の日付・時刻を変更できるようにする。指定日時は aware datetime、現在から最低 10 分先、既存 slot と非重複、DST の ambiguous / nonexistent 拒否を preview 前に検証する。確認ダイアログへ指定日時と UTC `Z` を固定表示し、確定時は同じ指定日時で channel / file / metadata / slot / attempt を再検証する。未確認・競合・不正日時では operation / job / upload attempt を作らず、対象テスト・全件テスト・欠陥優先レビュー・コミットを行う

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
**R1 監査注記（2026-08-02）:** 下記 P3 の単発受け入れと実公開証跡は完了事実として維持する。ただし通常の予約 operation は publication poll を自動起動する運用導線がなく、日常運用の FR-27 接続要件は未完了へ戻して H1-5 で修正する。P3 の履歴を消さず、受け入れ実験の完了と反復可能な production 導線の未完了を区別する。
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
**フェーズ状態:** [x] 完了
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
- [x] U6-9. `uv run pytest` 全件通過、実ブラウザで確定リファレンスとの比較、左パネル 4 状態、折り畳み、3 ワークスペース、編集後の未確認化、生成・最終確認・予約まで 1 周を目視する。README・進捗チェックを更新して大タスクコミットする
- [x] U6-10. ショート生成失敗契約のホットフィックス: テロップ生成前に既存 FFmpeg capability 検査を実行し、非対応時は job / queue snapshot を作らず生成工程と人確認を維持する。`shorts_queue` の全件失敗・部分失敗を成功表示せず、出力のない item を最終確認へ進めない。`short_cut` を既知の非 pipeline ジョブとして扱い、正常完了時は保存済み cutplan を画面側で再読込し、失敗時は元エラーを表示する。再現テスト、全件テスト、欠陥優先レビューを通して大タスクコミットする
- [x] U6-11. macOS の生産ライン実行環境を固定する: Homebrew の keg-only `ffmpeg-full` 実体に `subtitles` フィルタと同梱 `ffprobe` があることを確認し、ローカル `.env` の `YTLK_FFMPEG_PATH` に明示設定する。README へショート生産ラインでは必須であり字幕なしへ自動フォールバックしないこと、capability 確認、設定、アプリ再起動の手順を主導線付近にも明記し、全件テスト・レビュー・コミットを行う
- [x] U6-12. ショート生産ラインの実機フィードバックを反映する:
  - [x] U6-12a. `short_cut` の実行中表示・正常終了時の cutplan 再利用・失敗時の元エラー表示を既知の非 pipeline ジョブ契約として回帰確認する
  - [x] U6-12b. 生成開始前の FFmpeg `subtitles` capability 検査、job / queue snapshot 非作成、工程 4 と台本確認の維持、全件 / 一部失敗表示、mp4 欠損時の工程 5 禁止を回帰確認する。アプリは FFmpeg の導入や `.env` 変更を行わない
  - [x] U6-12c. 実行中ジョブ名・進捗・処理件数・対象ショート・現在工程・次の確認・経過時間・本日の完了数・要対応件数を左サイドバーへ集約し、メインは操作関連エラー・完成動画・折り畳み時の縮約状態だけにする。重複案内を削除する
  - [x] U6-12d. 「公開・投稿で予約する」は callback または描画前の遷移要求適用で workspace を切り替え、描画後の widget key 直接代入を行わない。例外時は operation を作らず、動画と確認済みライン状態を維持する
  - [x] U6-12e. 公開・投稿でタイトル候補選択・タイトル自由編集・説明文編集・タグ追加削除・予約日時確認を可能にする。編集値から新しい preview を構築し、編集後は以前の確認を失効させ、実送信 snapshot を確認ダイアログへ固定表示する。metadata 検証前および最終確定前は operation / 投稿 job を作らない
  - [x] U6-12f. 対象回帰テストと `uv run pytest` 全件を通し、欠陥優先レビューを完了して大タスクコミットする
- [x] U6-13. workspace 遷移の残存例外を解消する。「選択した候補でショート作成へ」「ショート生産ラインへ」を含む `detail_workspace_{video_id}` の全プログラム遷移を callback または widget 描画前の遷移要求へ統一し、描画済み widget key を直接変更しない。現在の生成 job、候補引き継ぎ、確認済みライン状態を維持し、遷移だけでは operation / job を作らない。全遷移経路の回帰テスト・全件テスト・欠陥優先レビュー・コミットを行う

**Done 条件:**

- [x] AC-31 の全項目（工程 UI 前提で読み替えるもの以外）と AC-35 の全項目が満たされている
- [x] 初期表示で字幕全文・チャプター本文が描画されず、選択中の 1 ワークスペースだけが描画される
- [x] 作業切り替えに副作用が無く、実処理はワークスペース内の明示ボタンからのみ開始される
- [x] ショート 1 本が工程の一本道（刻む → テロップ → 生成 → 確認 → 予約）で完成し、YouTube 自動字幕の生焼き込みが工程上発生しない
- [x] 人確認が既定未チェックで、台本編集後に失効し、再起動後も証明できない人確認が復元されない。出力変更・欠損では最終プレビュー確認だけが失効する
- [x] 左パネルに工程別プレビュー・縮約工程・次の確認・現在の `SchedulePolicy.timezone` 基準の日次完了数が常時表示され、操作入口が重複しない
- [x] 既存の確認ダイアログ・S4 の状態契約・FR-21 の安全契約に回帰が無い
- [x] `services/` の変更が新規 `services/shorts_line.py` だけで、既存 service の挙動に不要な変更が無い
- [x] `uv run pytest` が全件通る
- [x] 実機でライン 3 周（3 本を予約まで）を通し確認済み
- [x] タスク完了コミット済み

**実機受け入れ証跡（2026-08-03 記録）:** 実ブラウザで確定リファレンス、左パネル 4 状態、サイドバー折り畳み、3 ワークスペース、編集後の確認失効を確認した。異なる 3 ラインについて、区間・台本・出力の fingerprint と人確認状態を照合し、各ラインが予約 operation と結び付いて `reserved` まで到達していることを確認した。3 operation の作成日が同じ `Asia/Tokyo` の 2026-08-02 だった時点では、日次表示が `3／3`・要対応 0 件になった。YouTube 自動字幕を直接焼き込む経路は使用していない。

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
**フェーズ状態:** [x] 完了
**前提:** U6 完了（エラー詳細の表示先である詳細・再生成領域が U6 で作られるため）。U7 とは独立で、並行着手可。

**変更ファイル範囲:**
- `src/yt_live_kit/ui/state.py`(動画 ID 別の構造化エラー通知（動画 ID / job ID / 処理種別 / 要約 / 詳細 / 発生日時）と保持上限（動画ごと直近 3 件）を追加)
- `src/yt_live_kit/ui/app.py`（`st.error(job_error)` による生ログ全文表示を廃止し、1 行要約 + 対象動画への導線に変更）
- `src/yt_live_kit/ui/components/status_bar.py`（長いエラーの要約表示と対象動画への導線）
- `src/yt_live_kit/ui/views/video_detail.py`（詳細・再生成領域に現在動画のエラー詳細（直近 3 件）を表示）
- `src/yt_live_kit/services/jobs.py`（既存の別ファイル技術ログを path confinement・サイズ上限付きで読み取る副作用なし helper の追加だけ。job 実行・保存・cleanup 契約は変更しない）
- `tests/test_ui_state.py`（構造化通知、保持上限、他動画との分離）
- `tests/test_ui_app.py` / `tests/test_ui_video_detail_page.py`（要約表示と詳細表示の期待値）
- `tests/test_jobs.py`（技術ログ helper の正常・欠損・上限・不正 job ID のテスト）
- （`services/jobs.py` の変更は上記 read-only helper に限定する。JobState schema、worker、error 保存、cleanup の挙動は変更しない）

**作業:**

- [x] U8-1. `ui/state.py` に構造化エラー通知を実装する。動画ごとに直近 3 件、動画に紐づかないエラーはグローバル要約のみ・上限付きで保持する
- [x] U8-2. `ui/app.py` / `status_bar.py` の先頭表示を「（処理名）に失敗しました + 対象動画 + 再試行方法」の 1 行要約に変更する
- [x] U8-3. 詳細・再生成領域に現在動画のエラー詳細（技術ログ含む）を表示する。他動画のエラーは表示しない
- [x] U8-4. テストを追加し、`uv run pytest` 全件通過・実ブラウザ確認（長い ffmpeg エラーで先頭が占有されないこと）・進捗チェック更新・コミット

**Done 条件:**

- [x] AC-33 の全項目が満たされている
- [x] 長い技術ログがページ先頭・ステータスバーを占有しない
- [x] `uv run pytest` が全件通る
- [x] タスク完了コミット済み

**見積もり目安:** 1 日

---

### P5: 投稿枠の複数化 + ライン既定値の設定化（v3.2）

**目的:** 「毎日 3 本」を予約側で受けられるようにする。スケジュールポリシーを 1 日 1 枠（`daily_time`）から複数枠（`daily_times` リスト）へ拡張し、ショート生産ラインの既定値（レイアウト・プリセット）と枠リストを設定ページで編集できるようにする（FR-28 v3.2 / FR-20 v3.2 / AC-36）。
**フェーズ状態:** [x] 完了
**前提:** U6-8 のコード実装完了（既定値の読み取り関数は U6 で先行実装済み。本タスクは編集 UI と枠拡張）。P5-3 は R1 完了後に着手でき、U6-9 と P5-4 の実機確認は設定 UI 完成後に同じライン 3 周でまとめて閉じる。フェーズ P 系タスクのため `services/schedule.py` の変更を許可する。

**変更ファイル範囲:**
- `src/yt_live_kit/services/schedule.py`（`SchedulePolicy` を `daily_times` リスト対応へ拡張。旧 `daily_time` 単一値の読み込み互換 = 要素 1 個のリスト。`assign_next_slot` は日内の枠を時刻順に埋めてから翌 `interval_days` 日へ進む。重複時刻・不正形式は日本語エラー）
- `src/yt_live_kit/ui/views/settings.py`（枠リストの編集 UI、ショート既定値（レイアウト・通常 / Hook プリセット）の編集 UI）
- `src/yt_live_kit/ui/views/_local_settings.py`（既定値の保存関数を追加。保存先 `data/_config/shorts_defaults.json`）
- `tests/test_schedule.py`（複数枠の割り当て順、互換読み込み、重複・不正の拒否、DST 跨ぎ）
- `tests/test_ui_settings_page.py`（編集フォームが保存関数を正しく呼ぶこと）
- `docs/execution-plan-v3.md`（進捗チェック）

**作業:**

- [x] P5-1. `SchedulePolicy` の `daily_times` 拡張と互換読み込み・検証（重複禁止・厳密 `HH:MM`・1 個以上）
- [x] P5-2. `assign_next_slot` の複数枠対応（同日内は時刻順、埋まったら翌 `interval_days` 日。UTC 変換・aware datetime の既存契約を維持）
- [x] P5-3. 設定ページに枠リスト編集とショート既定値編集を実装（保存は atomic。FR-27 の安全契約・確認ダイアログは変更しない）
- [x] P5-4. テスト追加、`uv run pytest` 全件通過、実機で 3 枠設定 → 3 本予約が枠順に割り当たることを確認、進捗チェック更新・コミット

**Done 条件:**

- [x] AC-36 の全項目が満たされている
- [x] 既存の予約済み operation・queue.json に影響が無い（互換読み込みの自動テストあり）
- [x] `uv run pytest` が全件通る
- [x] タスク完了コミット済み

**実機受け入れ証跡（2026-08-03）:** `09:00`、`13:00`、`18:00`、間隔 1 日、`Asia/Tokyo` を設定画面から保存し、設定後の 3 本が既存予約との競合を避けながら `2026-08-03 13:00` → `2026-08-03 18:00` → `2026-08-04 13:00` の空き枠順で割り当たることを確認した（8 月 3 日 09:00 と 8 月 4 日 09:00 は既存予約済み）。追加 2 本は upload / processing が成功し、live 状態が private、指定 `publishAt`、Made for Kids = false、要照合なしであることを確認した。合成・改変コンテンツの選択値はローカル operation snapshot に false と保存され、YouTube の `videos.list` 応答では当該キーが省略されるため live 値は未判定として扱う。

**見積もり目安:** 1 日

---

### R1: 全体リファクタリング・性能・長期運用監査

**目的:** U6 / P5 の途中で一度立ち止まり、既存挙動を変えずにコード全体の回帰基準、生成・画面応答のボトルネック、再起動や途中失敗を含む長期運用上の穴を確認する。即時に安全性と効果を証明できる小さな変更だけを実装し、生成方式・永続化境界・プロセス間排他に触れる構造変更は別タスクとして切り出す。
**フェーズ状態:** [x] 完了
**前提:** U6-8 / P5-2 までのコードを freeze した独立タスクとして扱う。R1 はフェーズ U の追加実装ではないため、下記に限定した既存 service の保守変更を許可する。外部 API、実 upload、実 Codex 呼び出しは行わない。

**変更ファイル範囲:**
- `docs/refactor-review-2026-08-02.md`（実測、優先順位、保留理由、次タスク候補）
- `docs/requirements-v3.md` / `docs/execution-plan-v3.md` / `docs/tech-stack.md` / `docs/v3-agent-prompts.md`（実装契約と現状のずれ、通常 operation の公開後 poll 未接続を記録）
- `pyproject.toml` / `uv.lock`（stateful expander に必要な Streamlit 最低版と uv 設定形式の整合のみ。新規依存は禁止）
- `src/yt_live_kit/services/ytdlp.py` / `src/yt_live_kit/ui/runtime_checks.py` / `src/yt_live_kit/ui/app.py`（binary identity の純粋 helper と、全 rerun で行っている read-only バージョン検査の表示用 bounded cache）
- `src/yt_live_kit/services/history.py`（旧データを含む日時比較の UTC 正規化）
- `src/yt_live_kit/services/shorts.py`（既存単体生成経路の完成 mp4 を atomic replace へ統一）
- `src/yt_live_kit/ui/views/video_detail.py` / `src/yt_live_kit/ui/components/upload.py`（完了していない queue manifest を予約可能と扱わない fail-closed gate）
- `tests/test_ytdlp.py` / `tests/test_ui_runtime_checks.py` / `tests/test_history.py` / `tests/test_shorts.py` / `tests/test_ui_video_detail_page.py` / `tests/test_ui_upload.py`（上記の局所テスト）

**R1 限定の変更制約:** `build_short_from_segments()`、queue fingerprint、upload transaction、工程 6 の output fingerprint は変更しない。`services/shorts.py` は legacy `build_short()` の最終出力保護、`services/history.py` は sort 用日時正規化、`services/ytdlp.py` は副作用のない binary identity helper に限定する。

**作業:**

- [x] R1-1. 回帰基準と実測を採取する。Apple M2 / 16 GB / macOS 26.5.2 で `uv run pytest -q` は 1063 passed / 2 skipped、4.23〜4.91 秒。処理済み 48 本の warm 計測は履歴読込 30 回平均 2.65 ms、latest manifest 全件走査 30 回平均 4.19 ms、`yt-dlp --version` 10 回平均 237.28 ms、20,328,048 bytes mp4 の SHA-256 10 回平均 65.25 ms。60 秒ショートの最終 FFmpeg pass 約 48 秒は 1 回の運用観測であり、G1 の再現 benchmark 前は傾向としてだけ扱う
- [x] R1-2. 監査結果を `docs/refactor-review-2026-08-02.md` に記録し、今回修正・計測後に着手・保留を分ける。要件違反となる一括整形、無計測の大規模分割、生成方式の変更は行わない
- [x] R1-3. 実装契約のずれを直す。`st.expander` の `key` / `on_change` を導入した Streamlit 1.55 を最低版とし、現在 lock 済みの 1.60 は解決版として区別する。uv の deprecated 設定を現行形式へ移す。FFmpeg seek は現行実装・テストの `-ss` を `-i` より前に置く input seek を正本とし、長尺後半の decode を省きながら再 encode で 0 秒始まりの中間ファイルを作る契約へ文書だけを合わせる。R1 では seek 順を変更せず、既存 S5 の実機受け入れを根拠に維持し、G1 で input / output seek の境界 frame と速度も比較する
- [x] R1-4. 小さな fail-closed 修正を行う。未完了 queue manifest は予約対象にしない、旧 metadata の naive datetime は UTC として正規化して aware 値との混在で履歴一覧を落とさない、既存単体生成経路も失敗時に以前の完成 mp4 を保持する。`shorts_line` の途中結果表示は維持するが、upload と予約可能件数は manifest 全体が `done` になるまで 0 とする
- [x] R1-5. 安全な rerun 高速化を行う。service の純粋 helper が解決済みパス、device、inode、size、`mtime_ns` を binary identity として返し、UI はその identity、設定パス、timeout を key に TTL 600 秒・最大 4 件で `yt-dlp --version` の警告文字列だけを cache する。出力 mp4 の SHA-256 は工程 6 直前の内容再検証を維持し、stat だけを信頼する cache へ置き換えない
- [x] R1-6. 構造課題を実行可能な H1 と G1 に分け、各タスク ID、変更範囲、Done 条件、依存、見積もりを本計画へ追加する。P3 の単発公開受け入れ証跡は維持する一方、通常予約 operation の publication poll 未接続は現行 FR-27 / AC-28 の未完了項目として明示する
- [x] R1-7. 対象テストと `uv run pytest -q` を通し、実装者と別のサブエージェントが欠陥優先レビューを行う。指摘修正後に計測を再実行し、進捗更新・コミットする

**Done 条件:**

- [x] 既存 1063 passed / 2 skipped から回帰が無く、新規テストを含む 1074 passed / 2 skipped が通る
- [x] 外部 API、実 upload、実 Codex 呼び出し、新規 pip 依存、生成品質の変更が無い
- [x] 即時修正と構造課題が混ざらず、後者に再現条件・影響・推奨順が記録されている
- [x] rerun 高速化は cache miss 240.56 ms、hit 30 回平均 0.215 ms と実測され、安全境界の再検証を弱めていない
- [x] `uv lock --check` と `uv sync --locked` が警告なく成功する
- [x] safety / performance の独立レビューで blocker が解消され、タスク完了コミット済み

**見積もり目安:** 0.5〜1 日

---

### H1: 長期運用 hardening

**目的:** R1 で再現したプロセス間競合、root 外 path、途中クラッシュ、非 atomic 永続化、通常予約 operation の publication poll 未接続を、個別 commit と回帰テストで解消する。
**フェーズ状態:** [x] 完了
**前提:** R1 完了。各小タスクは `impl-sonnet` ワーカーが実装し、別サブエージェントが欠陥優先レビューを行う。H1-1〜H1-5 はタスク単位で commit し、順番に進める。

**作業:**

- [x] H1-1. jobs のプロセス間排他と pointer fail-closed。`services/jobs.py` / `tests/test_jobs.py` を対象に、data root 単位の advisory lock、owner PID / token、UUID temp + flush + fsync + replace、壊れた `current.json` からの running job scan を実装する。2 process 同時開始の勝者が 1 件、live worker を orphan close しない、破損 pointer で busy を fail-open にしないテストを Done とする。`1083 passed, 2 skipped`。タスク単位コミット済み。見積もり 1〜1.5 日
- [x] H1-2. video ID の path confinement。`services/_paths.py` を新設し、`services/ytdlp.py` / `history.py` / `jobs.py` / `shorts_queue.py` / `shorts_line.py` / `upload_queue.py` と対応テストを段階移行した。通常の 11 文字 ID（アンダースコア先頭を含む）は互換、`.` / `..` / separator / root 外 symlink は副作用前に日本語で拒否し、解決後 path が `data_dir` 配下であることを確認した。予約namespaceは `_jobs` / `_schedule` / `_config` / `_channels` / `_batch` の完全一致だけとし、historyも同じ条件でskipする。識別子の side-effect-before-rejection、resolved path、manifest / upload snapshot、yt-dlp 出力内 symlink の境界テストを追加し、`uv run pytest -q` は `1174 passed, 2 skipped`、独立レビューは P0/P1 なし。タスク単位コミット済み。見積もり 1 日
- [x] H1-3. queue crash recovery。`services/shorts_queue.py` / `ui/components/shorts_queue.py` / `ui/components/upload.py` と対応テストを対象に、owner job ID、`interrupted` terminal state、再起動 recovery table、既存成功 item の再利用可否を schema で固定した。schema 2 と v1 done 移行、atomic manifest / recovery、H1-1 owner 判定、入力・出力 fingerprint 検証、欠損 output の予約拒否、クラッシュ境界テストを実装済み。focused 91 passed、全体 `1187 passed, 2 skipped`、独立 defect-first review は P0/P1 なし。タスク単位コミット済み（`1a9ac7b`, `88475f9`, `765c49a`, `3c62915`）。見積もり 0.5〜1 日
- [x] H1-4. OAuth token とローカル設定の atomic persistence。`services/youtube_api.py`、`ui/views/_local_settings.py` と対応テストを対象に、権限 600 の同一 directory temp、flush + fsync + replace、advisory lock、lock 内再読込 + merge を共通化する。中断時に旧 JSON が残り、2 process 更新で ID 集合を失わないことを Done とする。見積もり 0.5〜1 日
- [x] H1-5. 通常予約 operation の公開後 poll 接続。`services/upload_queue.py` / `ui/components/upload.py` / `ui/components/status_bar.py` と対応テスト・README・requirements を対象に、upload worker を予約時刻まで占有しない明示 CTA または bounded follow-up job を実装する。同じ operation ID へ publication observation を atomic 追記し、二重 poll、再起動、時刻前、timeout、private lock、public の各境界を通す。FR-27 と AC-27 / AC-28 を再度 `[x]` にできることを Done とする。見積もり 1 日
- [x] H1-6. 全件テスト、実機を伴わない fault injection、独立レビューを通した。統合 main で `1205 passed, 2 skipped`、`git diff --check fe4c7d7..HEAD` 通過。初回統合レビューの P1 4 件（相対 data root の symlink、`_config` symlink、worker lease、legacy v1 成果物取り違え）を修正し、同じ独立 reviewer の再レビューで P0/P1/P2 なし・APPROVE を確認した。各 task commit とフェーズ完了 commit を実施する

**Done 条件:**

- [x] jobs の同時実行 1 件制約が process 境界でも成立し、壊れた pointer が fail-open にならない
- [x] data root 外への path 解決、途中 queue の予約、token / 設定の切断・lost update を自動テストで拒否できる
- [x] 通常予約 operation の公開後状態を再起動後も安全に観測でき、FR-27 / AC-27 / AC-28 が再び充足する
- [x] `uv run pytest -q` 全件通過、独立レビュー済み、フェーズ完了 commit 済み

**見積もり目安:** 4〜5.5 日

---

### G1: FFmpeg single-pass benchmark

**目的:** 生成時間の大半を占める複数 encode pass を変更する前に、速度差と品質差を再現可能な形で測り、production 実装へ進む価値があるか判断する。
**フェーズ状態:** [x] 完了
**前提:** R1 完了。production の `services/shorts.py` / `ffmpeg.py` は変更せず、benchmark 用試作だけを作る。ローカル素材以外の外部 API は使わない。
**変更ファイル範囲:** `benchmarks/ffmpeg_single_pass.py`、`docs/benchmarks/ffmpeg-single-pass.md`、`docs/execution-plan-v3.md`。採用決定時の要件変更と production 実装は G2 として別計画にする。

**作業:**

- [x] G1-1. 15 秒 / 60 秒 / 180 秒、単一区間 / 3 区間、字幕なし / 通常字幕 / Hook 付きの代表 fixture を 3 種固定し、現行 input seek、同じ encode 条件の output seek、single-pass filter graph 試作を同じ codec / CRF / preset で warmup 1 回後に各 3 回計測した。case ごとに mode 順を rotate し、FFmpeg 8.1.2-full の字幕 filter を使用した。標準版で字幕 filter が無い場合の blocker も確認した
- [x] G1-2. warm wall time の median / min / max、出力尺、先頭・末尾 frame、audio start / end PTS、区間接続、字幕 cue、解像度・pixel format、出力容量を `docs/benchmarks/ffmpeg-single-pass.md` と harness の JSON report に記録した。single-pass の短縮は 15 秒 21.98％、60 秒 22.68％、180 秒 23.08％で、境界差は最大 1 frame、audio expected duration 誤差は 0 秒だった。保持した一時 output の代表フレームを 3 mode で目視し、字幕 / Hook / layout / 接続の明確な回帰がないことを確認した
- [x] G1-3. 全ケース共通の速度 25％ gate を満たさず不採用とした。requirements-v3 の FR-25 / AC-25 改訂、G2 の production 実装、rollback 変更は行っていない

**Done 条件:**

- [x] 別環境で再実行できる command、fixture 条件、FFmpeg version、計測結果が残っている
- [x] production code と既存生成物を変更していない
- [x] 採否と根拠が独立レビューされ、計画更新を commit 済み

**見積もり目安:** 0.5〜1 日

---

### S9: 選択親候補区間のローカル Whisper 精査（v3.2 追加）

**目的:** 良好な YouTube VTT を候補探索の粗い親入力として維持しながら、人が選択した親候補の必要区間だけをローカル Whisper で精査し、サブ区間判断とテロップ台本で同じ字幕結果を再利用する。全編再文字起こしを通常経路にしない。
**フェーズ状態:** [~] 進行中。S9-PLAN（要件・計画の確定）は [x] 完了し、S9-6 は最終受け入れ専用として未完了のまま T1 を先行する。
**前提:** U6、S8、S6、S1、S3、S4、U8、R1、H1、G1 が完了済みで、既存の `(video_id, clip_id)` ライン状態、FR-30 の cutplan、FR-22 の telop、FR-25 の整数ミリ秒境界を正本として再利用する。S9 で `video_id` を `asset_id` へ移行しない。

**背景（実機で観測した事実）:** S8 の実機確認で、焼き込まれた字幕に固有名詞の誤認識が目立つことを確認した。実データ例（`data/LB4px1wRFnY`）では「クロード」が次行で「フロード」になり、「感覚すけど」のように助詞が脱落している。原因は YouTube 自動字幕（VTT）そのものの精度であり、区間の切り方や連結処理の問題ではない。U6 の AI 案と人の全文確認で直る誤りもあるため、先に S9 の A/B で追加効果を測定する。

**初版のスコープ境界:**

- **通常経路:** YouTube VTT は `clips.py` の親候補探索と既存の全体 transcript 経路に残す。`subtitles/ja.vtt` は読み取り元として保持し、上書き・改名・自動置換をしない
- **精査経路:** 人が選択した親候補の 1 件以上の絶対時刻区間だけを、音声のみの入力から whisper.cpp 1.9.1 で処理する。現行の 1 ジョブ制約内で入力順に直列処理し、音声 cache と複数区間の結果を永続化する
- **共通結果:** `TranscriptArtifact` に取得元、`source_kind`、モデル、runtime、設定、音声入力 fingerprint、対象区間、絶対時刻 cue、cue digest、artifact fingerprint を保存する。resolver が返した同一 artifact を `short_cut.py` と `telop.py` へ渡し、cutplan / telop / review fingerprint へ使用区間の digest を伝播する
- **境界:** Whisper timestamp は境界の唯一の正本にしない。padding、必要な VAD、既存 cue、動画プレビュー、人の区間確認を残し、FR-25 の整数ミリ秒正規化と U6 のゲートを維持する
- **失敗時:** runtime 不備、モデル不一致、音声準備失敗、未知の出力 schema、cache 破損は高精度 artifact を有効にせず、日本語の診断と明示的な粗い VTT fallback を返す。古い高精度結果や人確認を黙って再利用しない

#### S9-0: 既存 VTT 互換・非上書き保存契約

**目的:** 現行の YouTube 字幕取得を先に安全化し、S9 の resolver / artifact 実装が既存 `subtitles/ja.vtt` を再取得で上書きしないことをコードと fixture で固定する。
**対応要件 / AC:** FR-35、AC-30、AC-37。

**前提:** 現行 `services/ytdlp.py` の `_normalize_subtitle_path` と `ja-orig` / `ja.vtt` 保存経路、`tests/test_ytdlp.py` の既存挙動、`data/{video_id}/subtitles/ja.vtt` の後方互換を確認する。S9-0 は S9-1〜S9-6 の全実装に先行し、既存 VTT の意味を変えない。

**変更ファイル範囲:** `src/yt_live_kit/services/ytdlp.py`、`tests/test_ytdlp.py`、`tests/test_transcript.py`、必要な字幕 source metadata の model / fixture。README、既存 full / compressed transcript、Whisper runtime は変更しない。

**作業:**

- [x] S9-0-1. 新しく取得した VTT を video data path の隔離した incoming temporary file へ保存し、検証完了前に canonical `ja.vtt` を触らない atomic 境界を作る
- [x] S9-0-2. `ja.vtt` が存在しない初回だけ検証済み incoming を bootstrap し、既存なら bytes・mtime の意味を保ったまま変更せず、`subtitles/sources/{source_fingerprint}.vtt` と source metadata へ保存する
- [x] S9-0-3. download / parse / rename の失敗、空 VTT、未知言語、partial file、process crash では既存 `ja.vtt` と既存 downstream を変更せず、incoming の cleanup と日本語診断を行う
- [x] S9-0-4. source fingerprint と既存 `ja.vtt` の compatibility を S9-2 の `TranscriptArtifact` / resolver が参照できる形にし、S9-0 後のタスクで `ytdlp.py` の非上書き契約を再変更しない

**テスト:** 既存 `ja.vtt` の hash / bytes が再取得前後で不変、初回 bootstrap、`ja-orig` / `ja.vtt` の各命名、source VTT の immutable 保存、失敗時の既存成果物保持、partial / empty / malformed input、atomic replace failure、incoming cleanup、path confinement、既存 transcript / candidate 読み込みの回帰。

**Done 条件:**

- [x] 既存 `ja.vtt` がある動画を再取得しても bytes が変わらず、新しい VTT は source artifact として別保存される
- [x] 取得失敗・parse 失敗・プロセス中断のどの境界でも既存 VTT / downstream が壊れず、再試行可能な日本語エラーになる
- [x] S9-2〜S9-6 がこの保存契約を前提にでき、`ja.vtt` の上書き・改名・自動置換を追加しないことを独立レビューできる

**コミット境界:** `ytdlp.py`、字幕 source metadata、関連 tests の互換性修正だけを `S9-0` としてコミットする。メッセージに `S9-0` を含め、S9-1 benchmark や resolver schema を混ぜない。

#### S9-1: 代表素材 benchmark とモデル決定

**目的:** 実モデルの精度・固有名詞・時刻品質・処理時間を先に比較し、後続実装が未決定モデルへ依存しないようにする。
**対応要件 / AC:** NFR-11、FR-36、AC-37（S9-1 は benchmark 証跡と Go / No-Go を確定するタスクであり、実装受け入れは S9-6 で判定する）。

**前提:** S9-0 完了、S9-PLAN 完了、U6 実機証跡、代表素材を 3〜5 本（短い候補、長い候補、固有名詞が多い候補、音声条件が異なる候補）選ぶ。各素材の使用 span、手作業で固定した gold transcript、固有名詞・製品名の glossary、測定条件、改善閾値、wall time / peak memory budget を Whisper 実行前に固定する。production data、既存 `ja.vtt`、既存 mp4 は変更しない。モデルは手動で取得済みの候補だけを比較し、自動ダウンロードを行わない。

**変更ファイル範囲:** `benchmarks/`、`docs/benchmarks/`、S9 用 fixture / 計測記録のみ。gold transcript / glossary は benchmark fixture として固定し、production の `src/`、既存 `data/`、`README.md` は変更しない。

**作業:**

- [x] S9-1-0. 素材ごとの gold transcript、固有名詞 glossary、cue inclusion rule、評価対象 range、CER / 固有名詞 / cue 品質 / wall time / peak memory の判定基準を実行前に固定する。初版 gate は paired median CER の相対改善 10％以上、固有名詞 exact match の非悪化、cue 欠落 / 重複率が VTT baseline +5％以内、かつ事前宣言 budget 内とし、未達は No-Go とする
- [x] S9-1-1. `whisper-cli` 1.9.1 の実体、build capability、モデル file fingerprint、ffmpeg / yt-dlp の実体を記録する
- [x] S9-1-2. 同じ音声 span、言語 ja、padding、出力 schema で候補モデルを実行し、YouTube VTT を baseline として比較する
- [x] S9-1-3. gold transcript に対する日本語 CER、glossary の exact match / 誤り件数、cue の欠落・重複、候補区間の境界確認に必要な情報、wall time、CPU / memory、cache 前提を記録する
- [x] S9-1-4. Go の場合の採用モデル・設定、または No-Go の未採用理由、再現 command、fixture fingerprint、測定日、Go / No-Go を `docs/benchmarks/` に固定する。A の operational transcript reference を採用する場合も exact gold とは記録しない
- [x] S9-1-BND-AUDIT. ユーザーの前回表示順と case ID を固定対応し、開始境界・発話連続性だけの partial audit、expected editorial outcome、strict schema、boundary artifact の fingerprint を記録した。full transcript / glossary / cue anchor exact は未承認のままにし、境界自動化を採用しない
- [x] S9-1-AUDIT-APPLY. ユーザー原文「4本とも文字起こしは概ね問題なし」を改変せず strict artifact へ4 case対応付けし、displayed transcript content の operational reference、glossary、character / punctuation exactness、cue anchor exact milliseconds、boundary/editorial の状態を分離した。固定4 case・同じ gate・q5 / turbo の cold / warm 16 run を再評価し、A の decision mode、q5 の採用設定、tie-break、S9-3 参照値、再現 command、残余リスクを canonical report へ固定した

**テスト:** benchmark harness の deterministic fixture、自然文 audit artifact の原文 / 4 case mapping / unknown / missing / cross-case / exact 昇格拒否、transcript operational status と cue exact status の分離、boundary findings 維持、audit / fixture fingerprint、deterministic model selection / tie-break、canonical report / packet、q5 / turbo cold / warm 16 run、production hash unchanged、`uv run pytest`、`uv lock --check`、`git diff --check`。実ネットワーク・従量課金 API・実 YouTube 書き込みは使わない。

**Done 条件:**
- [x] 代表素材の A/B 表、採用モデルと設定、処理時間、精度指標、再現 command、未採用理由、Go / No-Go 判定が docs に残り、S9-3 が参照する唯一のモデル設定が決まっている
- [x] gold transcript、固有名詞 glossary、選択 span、CER / exact match / cue 欠落・重複 / wall time / peak memory の測定値と判定閾値が同じ fixture fingerprint に結び付き、A では numeric gate と operational transcript reference gate、exact dimension の未承認状態を分離して記録する
- [x] 速度や精度に根拠が無い場合は「実装を進める」判定にせず、fallback-only の根拠を残す。今回は両候補の固定 numeric gate と operational reference gate が通過したため、失敗時 fallback を残した上で q5 を採用した

**S9-1 実測証跡（2026-08-03、A / Done）:** 4 case（`LB4px1wRFnY` 2853160–2910000、`mKwn-93gg90` 1120000–1300000、`CGalA8SISPE` 4220000–4340000、`hPeRSA9YVIM` 8640000–8730000）を固定し、fixture fingerprint `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718` に結び付けた。q5 は paired median CER 相対改善 78.69％、turbo は 80.85％。CER、glossary、cue、wall time、peak memory の numeric gate と operational transcript reference gate は両候補で通過した。ユーザー原文は audit fingerprint `9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263` の strict artifact へ固定し、exact gold とは記録していない。worst-case case wait を先に見る deterministic tie-break で q5（`ggml-large-v3-turbo-q5_0`）を採用し、S9-3 が参照する model SHA、設定、cold / warm、full JSON、baseline parity、production hash unchanged、16 run raw report、canonical report を [`docs/benchmarks/s9-1-report.md`](./benchmarks/s9-1-report.md) と [`docs/benchmarks/s9-1-report.json`](./benchmarks/s9-1-report.json) に固定した。canonical report v7 は fixture gold を `unverified_provisional` のまま保持し、fixture benchmark quality gate と effective operational gateを別 namespace・validatorで定義した。raw の model / input / runtime / range / run-kind identity、実ファイルの model / audio / VTT / whisper-cli bytes・SHA-256、full JSON 再parseとCER / glossary / cue再計算、canonical candidate output path / confinement / symlink拒否 / output fingerprint、argv / output schema / text fingerprint、stderr real time / peak RSS、全16 case run成功、cold / warm output SHA equalityを確認し、boundary automation は採用せず、人確認必須のままにする。strict VTT parity v2 は固定4 caseの実体を再計算した effective gate、production scope は fixture source_files 14件 + 保護対象1件の exact 15件として fail closed に検証する。fixture exact goldはbenchmark quality gateには必要だが、Aの operational transcript Go、S9-2開始、q5選定の必須条件ではない。

**S9-1-AUDIT-PACK 更新（2026-08-03、自然文監査反映）:** 4 case の固定 range、16,000 Hz mono WAV の絶対 path・bytes・SHA-256、表示 transcript 全文、glossary、cue anchor、自然文監査の次元分離を [`docs/benchmarks/s9-1-human-audit.md`](./benchmarks/s9-1-human-audit.md) と [`docs/benchmarks/s9-1-human-audit-v2.json`](./benchmarks/s9-1-human-audit-v2.json) に固定した。`uv run python benchmarks/s9_audit_packet.py check --manifest docs/benchmarks/s9-1-cases.json --boundary-audit docs/benchmarks/s9-1-boundary-audit.json --transcript-audit docs/benchmarks/s9-1-human-audit-v2.json --document docs/benchmarks/s9-1-human-audit.md` は manifest、strict audit、boundary artifact、音声 cache の実体を再検査して PASS する。追加の定型フォーマット入力は要求しない。

**S9-1-BND-AUDIT 完了記録（2026-08-03、partial boundary audit）:** ユーザーが確認した前回表示順 1〜4 を `lb4-clip002-short-proper-nouns`、`hpe-audio-variation`、`cgal-proper-nouns`、`mkw-long-local-asr` に対応付け、strict artifact [`s9-1-boundary-audit.json`](./benchmarks/s9-1-boundary-audit.json) として自然文所見を保存した。機械検証する outcome は順に `pass`、`opening_trim_or_review_required`、`opening_trim_or_review_required`、`internal_gap_removal_or_review_required`。case 1 の `pass` は今回確認した境界・発話連続性で追加処置なしという意味だけで、全文品質や最終 short の品質承認ではない。transcript content の operational reference 採用後も、glossary 個別 exact、character / punctuation exact、cue anchor exact times は未承認のままにし、boundary automation は採用しない。base fixture fingerprint は `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718` のまま変更せず、boundary audit は独立 fingerprint `0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1` で追跡する。

**次アクション:** S9-1 は A / Done とし、S9-3 は固定した q5 の model identity・runtime・decode 設定を唯一の参照値にする。S9-2 以降は開始できるが、artifact / cutplan / preview の境界は人確認必須であり、runtime・cache・人確認の失敗時は既存 VTT fallback を明示する。

**コミット境界:** `docs/benchmarks/` と harness / fixture の production 非変更コミット。メッセージに `S9-1` を含める。S9-1 の計測証跡だけで S9 フェーズ完了にはしない。

#### S9-2: TranscriptArtifact / resolver / fingerprint / persistent cache

**目的:** `ja.vtt` を壊さず、VTT と Whisper の provenance を同じ型で扱い、再実行・再起動・失効判定を決定論的にする。
**対応要件 / AC:** FR-35、FR-30 の artifact / digest 伝播、AC-30、AC-37。

**前提:** S9-0 の VTT 非上書き契約、S9-1 の採用候補または fallback-only 判定、既存 `vtt_parser.py` / `subtitle_burn.py` の絶対時刻・区間抽出契約、`_fsutil.py` の atomic write、`_paths.py` の path confinement、現行の `video_id` data path。

**変更ファイル範囲:** `src/yt_live_kit/models/transcript.py`、`src/yt_live_kit/services/transcript_artifact.py`、`src/yt_live_kit/models/clips.py`、`src/yt_live_kit/services/clips.py`、関連 unit test（`tests/test_transcript.py`、`tests/test_clips.py`、S9 専用 fixture）。必要な `models/__init__.py` の export を含む。`ytdlp.py` の VTT 保存契約は S9-0 のものを利用し、既存 `transcript.py` の full / compressed 出力は変更しない。

**作業:**

- [x] S9-2-1. `TranscriptArtifact` と cue の strict schema（schema version、`extra=forbid` 相当、`source_kind` enum、既存 `video_id`、source ref、range start / end、absolute cue、language、model / runtime / settings、audio input fingerprint、cue digest、artifact fingerprint、created_at、status）を定義する。status は `success` / `fallback` / `failed` / `partial` を区間単位と artifact 単位で扱う
- [x] S9-2-2. cache identity と artifact fingerprint を分離する。前者は実音声 bytes と sample rate / channel / codec / ffmpeg 設定 / source、model / runtime / decode / initial prompt / padding / VAD、入力 range 列から計算し、後者は成功 cue digest と schema を加える。path / mtime だけの再利用、float の暗黙丸め、相対時刻だけの digest、表示順の sort を許さない
- [x] S9-2-3. cue digest は cue の絶対 start / end、本文、順序を canonical JSON 化して計算し、`used_range_cue_digest` は normalized range、padding、cue inclusion rule を含める。source / input / model / settings / range / cue digest を artifact provenance として検証する
- [x] S9-2-4. `resolver` に `coarse_search` と `selected_range` の用途を分ける。前者は S9-0 の有効な YouTube VTT、後者は一致する Whisper artifact を優先し、schema / path / input / model / cue digest の不一致は高精度扱いから除外する。partial artifact は返さない
- [x] S9-2-5. `data/{video_id}/transcripts/artifacts/{artifact_fingerprint}.json` と lock 付き atomic な `index.json` を保存し、正常終了後の再構築、crash recovery、cache corruption、部分 JSON、偽の fingerprint、未知 field、範囲外 cue は fail closed にする
- [x] S9-2-6. coarse 候補 document に YouTube VTT artifact fingerprint、全 cue digest、親候補内容・表示順から計算した candidate fingerprint を保存する。候補 fingerprint を FR-31 の引き継ぎと FR-30 の cutplan lineage へ渡し、候補探索を Whisper に置き換えない

**テスト:** strict schema round-trip / unknown field、status と整数ミリ秒、canonical digest の順序 / 時刻境界 / padding / inclusion rule、同一 input の cache hit、実音声 bytes / codec / ffmpeg 設定 / model build / settings / range / source / cue 変更の cache miss、artifact / index の lock・crash recovery・破損 / 部分 / symlink / path confinement、使用範囲内変更だけの失効、使用範囲外変更の非失効、coarse candidate の lineage / fingerprint round-trip、atomic replace failure。

**S9-2 実装・検証実績（2026-08-03）:** strict model と canonical digest、用途別 resolver、lock 付き artifact/index cache、crash orphan recovery、fail-closed 検証、VTT candidate lineage を実装した。focused tests は 30 passed、全体は 1,482 passed / 2 skipped。`uv lock --check` と `git diff --check` も通過し、既存 `ja.vtt` と production data は変更していない。S9-0 の非上書き、S9-1 の q5 / operational transcript、exact transcript 未承認、boundary automation No-Go・人確認必須を維持する。

**S9-2 follow-up（2026-08-03）:** Whisper success artifact の model / runtime / settings / audio input fingerprint を必須化し、`selected_range` は language と expected provenance が揃わない場合に高精度扱いせず明示 fallback とした。VTT の path + content は実体 bytes を比較し、malformed timing block は拒否する。focused tests は 38 passed、全体は 1,490 passed / 2 skipped。

**S9-2 follow-up 2（2026-08-03）:** YouTube VTT success artifact に source path、source fingerprint、実 VTT bytes SHA-256 を必須化し、保存・再読込・resolver の各境界で confinement、symlink、実体 bytes、fingerprint、strict VTT 構造を再検証するようにした。canonical `ja.vtt` と `subtitles/sources/` の既存 path は維持し、path の無い content-only fallback は永続化しない。`selected_range` は expected cache identity と ordered `used_range_cue_digests` も必須一致とし、省略・不一致を coarse fallback へ落とす。focused tests は 45 passed、全体は 1,497 passed / 2 skipped。

**Done 条件:**
- [x] resolver が用途別に deterministic な artifact を返し、既存 VTT が untouched のまま、cache hit / miss と失効理由を検査可能である
- [x] coarse candidate が VTT provenance と candidate fingerprint を保持し、既存 clips の候補探索・表示順・FR-31 引き継ぎを壊さない
- [x] artifact と index の crash / corruption / lock 競合が高精度結果を返さず、cache identity と artifact fingerprint の差を検査できる
- [x] S9-3 と S9-4 がこの型と digest だけを使える

**コミット境界:** transcript / clips model、resolver / cache service、candidate lineage、unit test を `S9-2` 単位でコミットする。S9-2 では whisper subprocess、UI、cutplan / telop の実処理を変更しない。

#### S9-3: whisper.cpp runtime・capability・モデル設定・音声区間準備

**目的:** whisper.cpp 1.9.1 を安全に検査・実行し、動画全体ではなく選択区間の音声だけを 1 ジョブで準備する。
**対応要件 / AC:** NFR-11、FR-36、AC-35、AC-37。

**前提:** S9-1 の採用モデル設定、S9-2 の artifact schema / cache API、S9-0 の VTT 保存契約、既存 `ytdlp.py` の subprocess / timeout / path confinement、既存 `ffmpeg.py` の audio / time range 操作、`Settings` の env 設定方式。

**変更ファイル範囲:** `src/yt_live_kit/services/whisper_runtime.py`、`src/yt_live_kit/services/ytdlp.py` の選択区間 audio-only helper、必要な `src/yt_live_kit/config.py` の S9 設定フィールド、runtime / audio preparation tests（`tests/test_ytdlp.py` を含む）。将来の local video adapter、`asset_id` 移行、全編 Whisper fallback は変更範囲に含めない。

**作業:**

- [x] S9-3-1. `whisper-cli` の path、version 1.9.1、JSON timestamp capability、言語 ja、モデル path / fingerprint、ffmpeg capability、timeout を preflight し、実行ファイル・モデルの自動取得や shell command の自由入力を許さない
- [x] S9-3-2. `ytdlp.py` に `bestaudio` 相当の音声のみ取得 helper を追加し、video ID、source metadata、実音声 bytes、sample rate、channel、codec、ffmpeg 変換設定から audio input fingerprint を作る。既存の動画 mp4 download を S9 の prerequisite にしない
- [x] S9-3-3. 選択親候補の absolute ranges を入力順の span manifest にし、padding / seek / 必要な VAD / cue inclusion rule を固定する。複数区間は 1 ジョブ内で serial に処理し、部分成功を resolver が高精度として返さない
- [x] S9-3-4. `whisper-cli` を `subprocess.run` 相当で呼び、stdout / stderr / exit code / timeout / version / build capability / model / language / initial prompt / decode settings / range を typed result にする。1.9.1 の JSON schema 以外は保存せず、日本語の診断へ変換する
- [x] S9-3-5. 相対 timestamp を元動画基準の絶対時刻へ変換し、S9-2 の artifact writer へ渡す。temporary span は成功・失敗後に既定で削除し、音声 cache と artifact は atomic に残す
- [x] S9-3-6. 区間ごとの `success` / `failed` / `partial` status、job ID、range index、retry 可否、cache hit / miss を typed error / progress contract にし、1 区間失敗時は artifact 全体を高精度成功として返さない

**テスト:** missing binary / wrong version / missing model / wrong model fingerprint、build capability 不足、audio-only ytdlp command と mp4 非取得、実音声 bytes / sample rate / channel / codec / ffmpeg 設定 fingerprint、複数 range の順序、padding と offset、timeout / non-zero / malformed JSON、job ID / range index / retry 可否付き partial failure、cache hit 時の subprocess 非実行、同時実行を拒否する 1 job gate。

**Done 条件:**
- [x] 採用モデルで選択区間の artifact を再現可能に作れ、動画全体を取得せず、複数区間を 1 ジョブで処理できる
- [x] runtime 不備は既存 VTT を壊さず、エラー分類と fallback 情報を返す
- [x] audio helper が選択 range の音声のみを取得し、実体・変換条件を含む fingerprint と per-range status を S9-2 の artifact writer へ渡せる
- [x] timeout、malformed output、partial failure が job ID / range index / retry 可否付きの日本語エラーになり、古い高精度 artifact を黙って返さない

**S9-3 実装・検証実績（2026-08-03、REQUEST_CHANGES follow-up）:** 固定採用の q5 model / whisper-cli 1.9.1 / full JSON contract を preflight し、選択区間の audio-only cache、入力順 serial 処理、相対 timestamp の絶対化、S9-2 artifact writer、atomic 保存、既存 YouTube VTT fallback を実装した。focused tests は 56 passed、全体は 1529 passed / 2 skipped。`uv run python` の read-only smoke で、設定 binary `/opt/homebrew/bin/whisper-cli`（resolved `/opt/homebrew/Cellar/whisper-cpp/1.9.1/bin/whisper-cli`、SHA-256 `1fbabb51a45906bd36684695de9025eab63618a6eedc26971c47fa5affc5fe49`）、model `ggml-large-v3-turbo-q5_0.bin`（SHA-256 `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2`）、benchmark audio `LB4px1wRFnY-2853160-2910000.wav`（1,818,958 bytes、SHA-256 `da80cdd933fb8738dc6ee7aa980b8ca64a4f8143d5fcbf7a971b058adb5c4687`）、range `2,853,160–2,910,000 ms`、FFmpeg `8.1.2` を固定確認した。実 process の1回目は7 cue、artifact fingerprint `10f4616b7c0f0dfe9d8092b2d06e3e8b33274db05355702df87bb637c12c9587` の cache miss、2回目は同 fingerprint の cache hit で Whisper subprocess 1回のみだった。出力は OS temp / isolated cache のみで、ネットワーク取得・YouTube write・production data・既存 `ja.vtt` は変更していない。S9-4 以降と S9 / AC-37 の受け入れ判定は未完了とする。

**コミット境界:** runtime / audio preparation / unit test を `S9-3` 単位でコミットする。whisper-cli の実機 benchmark 証跡は S9-1 の commit に含め、ここでは production service のみを追加する。

#### S9-4: 親候補 Whisper 精査と short_cut / telop 再利用

**目的:** 既存 VTT による親候補探索を保ち、選択済み親候補区間の精査結果を cutplan、テロップ、連結生成へ一度だけ渡す。
**対応要件 / AC:** FR-22、FR-25、FR-30、FR-33、FR-35、FR-36、AC-30、AC-35、AC-37。

**前提:** S9-2 の resolver / digest / candidate lineage、S9-3 の runtime / audio span、S6 の `short_cut.py`、S1 の `telop.py`、S3 の整数ミリ秒境界、U6 の line state / review fingerprint。S9-1 が fallback-only の場合は coarse artifact での明示 fallback を実装し、高精度扱いにしない。downstream は S9-2 の artifact reference を受け取り、resolver を再実行しない。

**S9-1 境界品質の分離契約:** 親候補の固定音声 span は冒頭または内部に無発話を含み得るため、その span 自体を切り詰めたり fixture identity を変更したりしない。最終 cutplan / final short は冒頭の無発話と長い内部無発話を残さず、audio activity・cue・padding・human preview と人確認で opening trim または internal gap removal / review を判定する。Whisper timestamp 単独の正本、単純 onset-only gate、今回の約秒数の production 閾値化は許可しない。

**変更ファイル範囲:** `src/yt_live_kit/services/short_cut.py`、`src/yt_live_kit/services/telop.py`、`src/yt_live_kit/services/shorts_queue.py`、`src/yt_live_kit/services/shorts_line.py`、`src/yt_live_kit/ui/components/shorts_queue.py`、cutplan / telop / line model の fingerprint / lineage field、関連 service / UI unit test（`tests/test_short_cut.py`、`tests/test_telop.py`、`tests/test_shorts_queue.py`、`tests/test_shorts_line.py`、`tests/test_ui_shorts_queue.py`、`tests/test_ui_shorts_line.py`）。`clips.py` の親候補生成は VTT 入力を維持し、`ytdlp.py` の `ja.vtt` 保存契約は S9-0 のものを利用して再変更しない。

**作業:**

- [x] S9-4-1. 親候補を VTT artifact から選ぶ既存導線に、明示的な「選択区間を高精度化」入口を追加する。候補全体への Whisper 呼び出し、通常 rerun での自動呼び出し、候補探索の Whisper 化は行わない
- [x] S9-4-2. resolver から返された artifact reference と順序付き `used_range_cue_digest` 配列を cutplan の immutable snapshot に保存し、境界調整後は FR-25 の `normalize_segment_bounds()` と人確認を通す。Whisper timestamp の自動境界採用はしない
- [x] S9-4-3. FR-22 の prompt builder が直接 `ja.vtt` を再読込せず、選択区間と同じ artifact の absolute cues を受け取るようにする。Codex の台本生成は既存どおり 1 区間セット 1 回、人の全文確認を維持する
- [x] S9-4-4. `telop_{clip_id}.json`、queue / line snapshot、review fingerprint、output preflight に同じ artifact reference、artifact fingerprint、使用区間 digest 配列を欠落なく伝播する。生成直前は snapshot と resolver の schema / input identity だけを再検証し、別 artifact へ差し替えない
- [x] S9-4-5. `shorts_queue.py` の既存 queue fingerprint の意味は変更せず、immutable queue spec に artifact lineage を追加する。`shorts_line.py` と `ui/components/shorts_queue.py` は同じ reference を受け渡し、UI で fingerprint を再計算しない
- [x] S9-4-6. line state の schema version を上げ、artifact lineage / digest 配列を持たない legacy state は台本確認・最終確認を再利用せず未確認へ戻す。使用区間外の字幕変更は downstream を保持し、使用区間内の変更、artifact の欠損、音声 / model / settings 不一致は cutplan / telop / review confirmation を再確認対象へ戻す。元に戻しても人確認を自動復帰しない

**テスト:** VTT 粗い探索の非回帰、選択区間だけの Whisper 呼び出し、同一 artifact snapshot の cutplan / telop / queue / line 再利用、resolver 再実行が無いこと、multiple range の入力順、padding と preview、人確認必須、digest の in-range / out-of-range 失効、古い artifact と legacy line state の fail closed、queue fingerprint 非回帰、既存 `ja.vtt` の内容不変、FR-25 の字幕 offset / clip ID 非回帰。

**Done 条件:**
- [x] 1 本のラインで「VTT で親選択 → 必要区間を精査 → 同じ artifact でサブ区間・台本 → 人確認 → 生成」が成立し、artifact の provenance が画面と保存 JSON で追跡できる
- [x] 精査失敗時も既存 VTT の明示 fallback または停止となり、古い結果の黙った再利用が無い
- [x] queue / line / UI handoff が同じ immutable artifact reference と digest 配列を保持し、旧 line state の確認を再利用せず、queue fingerprint の既存意味を変えない

**コミット境界:** `short_cut.py` / `telop.py` / queue / line / UI handoff / model / tests を `S9-4` 単位でコミットする。S9-4 のコード変更に UI 設定画面の大規模再構成や local video adapter を混ぜない。

**S9-4 実装・検証実績（2026-08-03、実装ワーカー）:** `TranscriptArtifactRef`、artifact fingerprint、入力順の `used_range_cue_digests` を cutplan → telop → queue spec → line state → output/reuse preflight へ伝播する最小縦断を実装した。既存 VTT 親候補探索と通常 rerun は Whisper を呼ばず、明示した「選択区間を高精度化」だけが S9-3 の selected-range runtime を入力順・整数 ms・padding 付きで呼ぶ。telop prompt は高精度経路で `ja.vtt` を読まず、同一 artifact の absolute cues を使い、Codex は1区間セット1回のままにした。legacy schema 1 line state、欠損 artifact、lineage 不一致は確認・出力を再利用せず未確認へ戻す。focused tests は 269 passed、全体は 1,559 passed / 2 skipped、`uv lock --check`、`git diff --check`、`uv run python -m compileall -q src` も通過した。

**S9-4 独立レビュー・main 統合実績（2026-08-03）:** 独立レビューで queue fingerprint 非回帰、失効状態の永続化、高精度でない artifact の preflight 拒否、lineage strict schema、同一 artifact cue の UI 利用を再確認し、最終判定は APPROVE（P0 / P1 なし）。main は `a739d58` → `c8b3ab7` → `5f9dcad` の順に統合し、main 上でも focused 269 passed、全体 1,559 passed / 2 skipped、lock / diff / compile を再確認した。S9-4 Done 条件は完了とし、S9 全体と AC-37 は S9-5 / S9-6 の受け入れ前なので未完了を維持する。

`ui/components/shorts_line.py` は、cutplan の同一 artifact reference を line snapshot / state へ渡すために必要な最小 handoff として変更した。

#### S9-5: UI 設定・進捗・エラー・失効表示

**目的:** 高精度化を明示的に操作でき、長い処理でも状態・失敗・fallback・失効理由が日本語で分かるようにする。
**対応要件 / AC:** FR-33、FR-35、FR-36、NFR-13、AC-35、AC-37。

**前提:** S9-2〜S9-4 の service API、U6 の 6 工程・左パネル・line state、U8 の構造化エラー通知、P5 の設定ページ / 既定値、現行 1 ジョブ制約。

**変更ファイル範囲:** `src/yt_live_kit/ui/views/settings.py`、`src/yt_live_kit/ui/views/video_detail.py`、`src/yt_live_kit/ui/components/short_cut.py`、`src/yt_live_kit/ui/components/shorts_line.py`、必要な `_local_settings.py` と UI tests。UI は Whisper の schema / fingerprint 計算を再実装しない。

**作業:**

- [x] S9-5-1. 設定ページに runtime capability、選択済みモデル、言語 ja、model fingerprint、timeout、cache の場所 / 状態を読み取り専用で表示する。モデル path や shell command の自由入力、モデル自動ダウンロードは持たない
- [x] S9-5-2. 工程 2 に親候補区間の「高精度字幕を準備」CTA を置き、対象区間、padding、予想処理、上書き対象が無いことを preview してから明示 submit する。通常 rerun では再実行しない
- [x] S9-5-3. 進捗を job ID、段階（capability / audio / span / Whisper / artifact / resolver）、現在区間、range index、全区間数、cache hit / miss、per-range status で表示する。worker thread から `st.*` を呼ばず、既存 progress bridge を使う
- [x] S9-5-4. missing runtime、model mismatch、timeout、malformed output、cache corruption、in-range invalidation、coarse fallback、partial range failure を構造化 error として日本語表示する。各エラーに job ID、range index、retry 可否、既存成果物を維持したかを含める。既存候補・cutplan・telop・mp4 は確認なく削除・再生成しない
- [x] S9-5-5. artifact / cue digest 不一致時は台本確認または最終確認を証明できる状態だけ失効させる。使用区間外の変更でライン全体を失効させず、使用区間・対象 clip・次の gate を表示する
- [x] S9-5-6. 表示場所を固定する。候補カード header は coarse VTT provenance / candidate fingerprint、工程 2 の cutplan panel は refined / coarse fallback と対象 range、telop editor header は同じ artifact reference と digest 配列、最終確認 banner は invalidation / fallback 理由を示す。partial は区間ごとに表示し、全体を高精度成功と表示しない

**テスト:** settings capability 表示、CTA の明示 submit、通常 rerun で subprocess 非実行、single-job busy / 二重クリック、progress の job ID / range 分離、動画 A / B の状態分離、partial range の status と retry 表示、候補 card / cutplan panel / telop editor / final review banner の provenance 表示、構造化エラーの日本語要約、coarse fallback 表示、in-range / out-of-range 失効、再起動後の fail closed line state、既存確認 dialog の非回帰。

**Done 条件:**
- [x] 非エンジニアが「どの候補区間を、どのモデルで、どこまで処理したか」を確認でき、失敗時に次の操作が分かる
- [x] 高精度 artifact を使ったと表示する条件と coarse fallback の表示が一致し、1 ジョブ制約・既存の破壊操作確認・U6 のゲートを壊さない
- [x] candidate card、cutplan、telop、final review の各画面が同じ artifact reference / digest と status を表示し、partial / failed を高精度成功と誤表示しない

**S9-5 実装・検証実績（2026-08-04、実装ワーカー）:** 設定ページへ S9-1 の immutable contract と S9-3 の preflight capability を使った runtime / model / language / fingerprint / timeout / cache の読み取り専用表示を追加した。工程 2 は対象区間・固定 padding・音声のみの予想処理・既存成果物を上書きしない条件を preview し、`st.form` の明示 submit だけが既存 S9-4 `start_job` を起動する。通常 rerun は submit 前に service を呼ばず、busy 中は CTA を無効化し、既存の `JobBusyError` 競合処理を維持した。進捗は既存 job report bridge で job ID、stage、range、cache、per-range status を UI thread に渡し、worker thread から `st.*` を呼ばない。構造化 error は既存成果物維持・retry 可否・次操作を日本語で表示し、partial / fallback は区間単位で扱う。candidate card、cutplan、telop editor、final review は既存 artifact provenance / digest を再計算せず同じ lineage と失効理由を表示する。focused UI tests は 154 passed、全体は 1572 passed / 2 skipped、`uv lock --check`、`git diff --check`、`uv run python -m compileall -q src` も通過した。S9-5 Done 条件、S9-6、S9 全体、AC-35 / AC-37 の受け入れ判定は未完了のまま残す。

**S9-5 独立レビュー・main 統合実績（2026-08-04）:** 初回独立レビューで高精度化失敗時の未 import `ShortCutError` により構造化日本語 error が `NameError` へ上書きされる P1 を検出した。follow-up `c566111` で import と timeout 失敗経路の回帰 test を追加し、再レビューは APPROVE（P0 / P1 なし）。main は実装 `27e4021`、follow-up `c6c481c` の順に統合し、main 上で focused 154 passed、全体 1572 passed / 2 skipped、lock / diff / compile を再確認した。S9-5 Done 条件は完了とし、S9-6 と S9 全体、AC-35 / AC-37 の受け入れ判定は未完了を維持する。

**コミット境界:** UI / component / UI test を `S9-5` 単位でコミットする。設定・進捗・エラー表示だけを変更し、S9-4 の service 契約を UI に複製しない。

### T1: テロップ行時刻同期・明示確認（v3.2 追加）

**目的:** S9-6 を受け入れ専用の未完了状態で保持したまま、Codex が作成したテロップ draft の行時刻を、既存の production 境界を変えずに評価・保存・補正・表示する独立した実装列を追加する。低信頼行は元時刻を維持して警告し、誤字・固有名詞の全文確認とは別に、要確認行の時刻確認を明示的に記録する。
**フェーズ状態:** [~] 進行中。T1-PLAN は [x] 完了、次の未着手タスクは T1-1。T1-5 の同期受け入れ後に S9-6 の正式受け入れを一度だけ行う。
**対応要件 / AC:** FR-22、FR-25、FR-33、FR-35、FR-36、FR-39、AC-23、AC-35、AC-37、AC-40。
**依存:** `S9-5 → T1-PLAN → T1-1 → T1-2 → T1-3 → T1-4 → T1-5 → S9-6`。既存 S9-6 の ID・目的・受け入れ専用性は変更しない。

**T1 全体の固定契約:**

- Codex draft 後、人の全文確認前に pure monotonic aligner を実行できるのは T1-3 以降とし、T1-1 は production 非変更の測定、T1-2 は timing payload の保存契約に限定する。通常経路・production artifact / cache への追加 Whisper は禁止するが、T1-1 では固定した選択 span を隔離 temp へ bounded に再実行する benchmark だけ許可する
- 一意かつ高信頼な行だけを補正する。低信頼、要約、省略、重複、cross-cue 曖昧、token 欠落は元時刻を保持し、全件 flag と日本語警告を付ける。各行への無意味な編集は要求しない
- owning cue / range clamp、時系列、非重複、最低表示 500 ms を満たせない補正は採用せず fallback とする。token end は時刻の唯一の正本にしない
- timing confirmation は全文確認とは別の gate とし、low-confidence 行がある場合だけ必須にする。本文、時刻、alignment input、policy の変更で失効し、A → B → A でも自動復帰しない
- `subtitle_burn.py`、FFmpeg、cut 境界、queue fingerprint の意味、投稿予約、Codex 回数を変更しない。通常 rerun で外部処理を開始しない
- T1-PLAN では方式選定 ADR を作らず、T1-1 の spike 合格後にだけ T1-2 が Artifact v2 と immutable timing sidecar の比較を ADR で決める
- **Whisper 実行境界:** T1-1 だけは、固定 manifest の選定済み span に対象・回数の上限を設け、isolated temp へ bounded に whisper-cli を再実行してよい。production data / artifact / cache / output / hash は不変とする。T1-2 以降、本番経路、manifest 外の区間、全動画再解析、全編 Whisper、47 本 backfill は禁止する
- 実 upload、公開データ変更、Studio 操作、local video、asset ID 移行、従量課金 API、新規依存は全 T1 で禁止する

#### T1-PLAN: テロップ行時刻同期計画（docs-only）

**タスク状態:** [x] 完了。S9-5 完了後、S9-6 を開いたまま T1 を挿入する計画を確定した。
**目的:** 各 T1 を独立 commit・独立レビュー可能な境界へ分解し、評価・永続化・pure alignment・UI gate・同期受け入れの責務と安全境界を先に固定する。実装方式の選択や production schema の変更は行わない。
**前提:** S9-1〜S9-5 完了、S9-6 は未確認の受け入れ専用、R2 の UI 安全境界と刷新順が確定済み。
**変更ファイル範囲:** `docs/execution-plan-v3.md`、`docs/requirements-v3.md`、`docs/v3-agent-prompts.md`、`docs/tech-stack.md` の 4 ファイルだけ。ADR、コード、tests、benchmarks、fixture / data、production artifact / cache、`.codex/learning/user-decisions.md`、skill pointer は作成・編集しない。

**作業:**

- [x] T1-PLAN-1. FR-22 / FR-25 / FR-33 / FR-35 / FR-36 と AC-23 / AC-35 / AC-37 の既存契約を保ったまま、FR-39 / AC-40、用語、低信頼・timing confirmation・fallback の境界を要件書へ追加する
- [x] T1-PLAN-2. T1-1〜T1-5 の目的、前提、変更範囲、作業、テスト、Done、コミット境界、推定工数、禁止事項と、S9-6 を最後にする依存を実行計画へ追加する
- [x] T1-PLAN-3. 通常 / S9 ワーカー指示、T1 専用報告項目、レビュー観点、scope guard を `v3-agent-prompts.md` へ追加する
- [x] T1-PLAN-4. timing payload、atomic 保存、pure service、Streamlit session-state、既存 FFmpeg / queue / upload 境界を `tech-stack.md` へ反映する。方式選定 ADR は作成しない
- [x] T1-PLAN-5. `git diff --check`、docs 間の task ID / 依存 / FR / AC / 進捗 / 次アクション整合、変更範囲を確認する

**テスト / 検証:** docs-only のため `uv run pytest` は実行対象外。`git diff --check`、Markdown 内の ID / 依存参照確認、許可された 4 docs 以外の変更が無いことを検証する。

**Done 条件:**

- [x] T1-1 が次の未着手タスクとして明示され、T1-2 以降と S9-6 の依存順が一意である
- [x] 各 T1 タスクに目的、前提、変更ファイル範囲、作業、テスト、Done、commit 境界、推定工数、禁止事項がある
- [x] 低信頼行の元時刻維持、独立 timing confirmation、入力変更時失効、全文確認・最終 preview 維持が FR / AC / prompt / tech-stack で一致する
- [x] S9-6、S9、M16、AC-37 の未確認状態を完了へ変更していない
- [x] T1-PLAN は方式選定 ADR、production 変更、tests / benchmarks / data / learning log を含まず、task ID 入り docs commit の境界が明示されている

**コミット境界:** この 4 docs のみを `docs(T1-PLAN): define telop timing sync contract` としてコミットする。main へ統合しない。
**推定工数:** 0.5 日。

#### T1-1: production 非変更 timing spike と評価 manifest 固定

**タスク状態:** [ ] 未着手。T1-PLAN 完了後の次の着手タスク。
**目的:** token timing alignment を production に導入する前に、固定 fixture と人音声 gold で現行・短 cue 候補・token timing alignment 候補を比較し、採用可否の根拠を production 非変更で得る。現行 artifact が raw token timing を保持しない場合は、固定した選択 span だけを隔離 temp へ bounded に whisper-cli 再実行し、raw full JSON と token timing 候補を benchmark input として取得する。
**前提:** S9-1 の固定評価・S9-5 完了・T1-PLAN 完了。測定開始前に manifest、gold、coverage 分母、gate、実行環境を immutable に固定する。
**変更ファイル範囲:** `benchmarks/t1/` の専用 harness / fixture / manifest、`docs/benchmarks/` の T1-1 manifest・結果・再現記録、必要な benchmark 用 tests のみ。隔離 temp は repository 外または明示した一時 `data_dir` とし、`src/`、既存 `tests/`、既存 `data/`、production artifact / cache / output / hash、既存 S9-1 証跡は変更しない。

**作業:**

- [ ] T1-1-1. 長い単一 cue、multi / cross-cue、VTT fallback + 連結を各 20 行以上、全体 60 行以上含む評価 manifest を測定前に固定し、fixture fingerprint と変更禁止の記録を残す。manifest に有限の整数 `max_selected_spans` と `max_whisper_invocations` を各実行で明記し、実績が上限を超えないことを検証する
- [ ] T1-1-2. gold は人が音声を聞いて付けた line onset だけとし、推測 token end、字幕 end、既存自動境界を gold に昇格しない
- [ ] T1-1-3. 現行 artifact が raw token timing を持たない場合、manifest の `max_selected_spans` と `max_whisper_invocations` の範囲内で固定した選択 span を隔離 temp へ bounded に whisper-cli 再実行し、runtime / model / settings fingerprint、再現 command、raw full JSON の hash を記録する。production data / artifact / cache / output / hash は変更せず、全編処理・47 本 backfill は行わない
- [ ] T1-1-4. 現行、短 cue 候補、token timing alignment 候補を同じ fixture・同じ policy で A/B 比較し、CER、固有名詞、cue 欠落 / 重複、wall time、peak memory も同じ証跡へ記録する
- [ ] T1-1-5. coverage 分母を固定 manifest 内の検証済み token timing を持ち alignment 対象になり得る全 telop 行とし、VTT fallback と timing 無し legacy は coverage 外の非回帰群として分離する。VTT fallback + 連結群は現行出力同等、誤った自動移動 0 を別に判定する
- [ ] T1-1-6. provisional Go gate を pooled と、alignment 対象の長い単一 cue 群・multi / cross-cue 群の各群で個別に満たす。各群と pooled の coverage 80％以上、absolute onset median 250 ms 以下、p90 500 ms 以下、max 1000 ms 以下、signed median bias の絶対値 200 ms 以下、誤った line / cross-cue 移動 0 を測定前に記録する。結果後の基準緩和は禁止し、変更は別承認と理由記録を要する
- [ ] T1-1-7. low-confidence は全件 flag、元時刻維持、黙った移動 0 を確認し、owning cue / range clamp、時系列、非重複、最低表示 500 ms を満たせない場合は fallback にする。長い単一 cue 群・multi / cross-cue 群の signed bias と VTT fallback + 連結群の非回帰結果を別集計する

**テスト / 検証:** 固定 manifest の再現実行、固定選択 span の隔離 bounded whisper-cli 実行、runtime / model fingerprint と再現 command の検証、候補別 metric 再計算、群別 / pooled gate、gold / glossary / cue 欠落重複監査、VTT fallback + 連結の現行出力同等性、failure / fallback、wall / peak memory、production hash unchanged、`git diff --check`。production 経路への追加 Whisper、実 upload、外部 API は使わない。

**Done 条件:** 上記 3 fixture 群と 60 行以上の件数、gold、分母、gate、A/B 全指標、低信頼・fallback 証跡、再現 command、production 非変更が独立レビュー可能であり、Go / No-Go が事後緩和なしに判定できること。No-Go の場合は T1-2 へ進まず fallback-only と記録する。
**コミット境界:** benchmark 専用ファイルと証跡だけを `T1-1` としてコミットする。production code / artifact / cache を混ぜない。
**推定工数:** 1〜1.5 日。
**禁止事項:** production data / artifact / cache / output / hash の変更、固定選択 span 外の Whisper、全編 Whisper、47 本 backfill、gold の自動生成、結果後の閾値緩和、候補の黙った移動、T1-2 の方式決定や production 採用。隔離 temp の bounded benchmark 以外の Whisper 実行は禁止する。

#### T1-2: timing 保存契約・extractor

**タスク状態:** [ ] 未着手。T1-1 の provisional Go 後だけ着手する。
**目的:** Artifact v2 と immutable timing sidecar を比較し、既存 full JSON から一度だけ安全に token timing を抽出・永続化し、legacy / VTT fallback を壊さない immutable contract を実装する。
**前提:** T1-1 の fixed manifest / Go 証跡、S9-2 / S9-3 の artifact・runtime・cache identity 契約、既存 full JSON schema。
**変更ファイル範囲:** `docs/adr/0001-telop-timing-persistence.md`、`src/yt_live_kit/models/transcript.py`、`src/yt_live_kit/services/transcript.py`、`src/yt_live_kit/services/transcript_artifact.py`、`src/yt_live_kit/services/whisper_runtime.py`、必要なら新規 `src/yt_live_kit/models/transcript_timing.py` / `src/yt_live_kit/services/transcript_timing.py`、`tests/test_transcript.py`、`tests/test_whisper_runtime.py`、新規 timing tests。T1-2 が所有しない telop UI、subtitle burn、FFmpeg、cutplan、queue、upload は編集しない。

**作業:**

- [ ] T1-2-1. Artifact v2 と immutable timing sidecar の保存単位、互換性、失効、atomic replace、再検証条件を ADR で比較し、T1-1 の根拠に基づき一方を選ぶ。T1-PLAN では ADR を作らない
- [ ] T1-2-2. sidecar を選ぶ場合は parent artifact reference だけでなく、正規化 token payload または raw full JSON hash、model / runtime / settings / ranges、schema / policy version、自身の fingerprint を保存する。Artifact v2 の場合も同じ provenance を strict schema で保持する
- [ ] T1-2-3. T1-2 の extractor は既存 full JSON の結果だけを再利用し、T1-1 の isolated benchmark 例外を持ち越して追加 Whisper を呼ばない。atomic 保存後に parent、payload、policy、fingerprint、範囲、cue 所属を再検証する
- [ ] T1-2-4. whitespace / metadata、zero / reverse end、日本語 subword を安全に扱い、解釈できない token は alignment 不可にする。token end を時刻の唯一の正本にしない
- [ ] T1-2-5. 既存 artifact は backfill せず、timing 無し artifact / VTT / legacy は明示的 timing fallback として返す

**テスト / 検証:** strict schema、未知 field、payload / parent hash、model / runtime / settings / range / policy mismatch、atomic crash、cache restart、zero / reverse end、whitespace / metadata、日本語 subword、legacy fallback、T1-2 の追加 Whisper 呼び出し無しを unit / integration test で確認する。

**Done 条件:** ADR と実装が同じ保存選択を示し、保存・再検証・失効・fallback が deterministic で、既存 artifact / VTT / legacy の bytes と意味を変更せず、full JSON 一回の処理から timing payload を再利用できること。T1-3 が読む typed contract と fingerprint が固定されること。
**コミット境界:** ADR、timing model / extractor / artifact 接続、該当 tests だけを `T1-2` としてコミットする。
**推定工数:** 1〜1.5 日。
**禁止事項:** T1-1 不合格時の着手、T1-1 の isolated benchmark 例外の持ち越し、manifest 外の解析、既存 artifact の backfill、T1-2 の追加 Whisper、token end の正本化、UI での schema 複製、production data の一括変換、新規依存。

#### T1-3: pure aligner・telop・fingerprint 統合

**タスク状態:** [ ] 未着手。T1-2 の保存 contract が review PASS した後に着手する。
**目的:** Codex draft と T1-2 timing payload を純粋関数で対応付け、一意高信頼行だけを補正し、行別状態と policy / provenance / fingerprint を telop review lineage へ統合する。
**前提:** T1-1 Go、T1-2 commit / review PASS、FR-25 の整数ミリ秒・区間・入力順、S9-4 の immutable artifact handoff、R2 の service 境界。
**変更ファイル範囲:** `src/yt_live_kit/models/telop.py`、`src/yt_live_kit/services/telop.py`、新規 `src/yt_live_kit/services/timing_alignment.py`、必要最小限の `src/yt_live_kit/services/shorts_line.py` の review lineage / fingerprint 接続、`tests/test_telop.py`、`tests/test_shorts_line.py`、新規 timing alignment tests。`subtitle_burn.py`、`services/ffmpeg.py`、cut boundary、queue fingerprint の意味、投稿予約、Codex 呼び出し回数は変更しない。

**作業:**

- [ ] T1-3-1. monotonic・cue ownership・confidence 判定を持つ副作用のない aligner を draft 生成後、人確認前に呼べる形へする。行と token が一意かつ高信頼のときだけ start / end を補正する
- [ ] T1-3-2. 要約・省略・重複・cross-cue 曖昧・token 欠落は元時刻と flag を返し、自動移動や編集強制をしない。低信頼が混在しても高信頼行だけの補正を silent な全体移動にしない
- [ ] T1-3-3. owning cue / range clamp、時系列、非重複、最低表示 500 ms を検証し、違反時は元時刻へ fallback する。VTT / legacy / timing 無し artifact は既存 route を維持する
- [ ] T1-3-4. telop review lineage に timing policy、provenance、parent / payload fingerprint、status、行別 flag を含め、A → B → A を含む text / time / alignment / policy 変更で review と timing confirmation を失効可能にする
- [ ] T1-3-5. 人が本文または時刻を編集した後、明示的な再生成なしに自動再配置しない。subtitle_burn、FFmpeg、cut、queue、upload の入力意味と Codex 回数を固定する

**テスト / 検証:** pure unit test、長い単一 cue / multi / cross-cue、Japanese subword / whitespace、低信頼全件 flag、clamp / chronology / non-overlap / 500 ms、VTT / legacy fallback、fingerprint、A → B → A、編集後の自動再配置無し、既存 burn / cut / queue の回帰。

**Done 条件:** aligner が pure で、補正対象と fallback 対象を deterministic に分け、lineage / fingerprint が T1-4 と生成 preflight で再検証できる。既存 subtitle burn、FFmpeg、cut 境界、queue fingerprint、投稿予約、Codex 回数に差分が無いことをレビューできる。
**コミット境界:** aligner、telop model / service、review lineage 接続、該当 tests だけを `T1-3` としてコミットする。
**推定工数:** 1〜1.5 日。
**禁止事項:** UI への business logic 複製、低信頼行の自動移動、無意味な編集要求、subtitle burn / FFmpeg / cut / queue / upload 契約の改変、text edit 後の暗黙再配置、追加 Codex。

#### T1-4: Streamlit UI 時刻確認 gate

**タスク状態:** [ ] 未着手。T1-3 の service / lineage contract が review PASS した後に着手する。
**目的:** 同期状態、時刻確認の必要性、現在の start / end editor、独立 confirmation、再起動後の fail closed を UI へ接続し、通常 rerun で外部処理を発生させない。
**前提:** T1-3 の typed status / flags / policy / fingerprint、U6 の line state、S9-5 の status 表示、R2 の `ui-refactor-review-2026-08-04.md` の安全境界と刷新順。
**変更ファイル範囲:** `src/yt_live_kit/ui/components/shorts_line.py`、`src/yt_live_kit/ui/view_models/shorts_line.py`、必要な `src/yt_live_kit/ui/session_keys.py`、`src/yt_live_kit/services/shorts_line.py` の timing confirmation 永続化境界、`tests/test_ui_shorts_line.py`、該当 service tests。page shell〜工程 bar の刷新は計画更新後の別 diff とし、telop editor の刷新は T1 contract 後に分離する。

**作業:**

- [ ] T1-4-1. `synchronized`、`timing_review_required`、`cannot_sync` と行別 warning / fallback 理由を日本語で表示する
- [ ] T1-4-2. 現行の開始秒・終了秒 editor を維持し、全文の誤字・固有名詞確認と別の「要確認行の時刻を確認した」 gate を実装する。low-confidence が無い場合は timing gate を必須にしない
- [ ] T1-4-3. confirmation snapshot に text / time / alignment input / policy / lineage fingerprint を結び付け、変更、範囲変更、cache restart、A → B → A で失効させる。確認済みを推測復元しない
- [ ] T1-4-4. Streamlit session state は draft buffer / widget identity に限定し、validator・alignment・line state 遷移を UI に複製しない。通常 rerun、表示切替、再描画で Codex / Whisper / ffmpeg / upload を呼ばない
- [ ] T1-4-5. R2 の conditional rendering、explicit CTA、破壊操作確認、service の atomic / fail closed 境界と、UI 刷新順を壊さない

**テスト / 検証:** Streamlit AppTest または既存 UI harness、3 status 表示、low-confidence 有無、editor 保持、timing gate と全文確認の分離、入力変更 / A → B → A 失効、cache restart、failure / fallback、通常 rerun の外部処理無し、line state atomic / fail closed、既存 preview / generation gate。

**Done 条件:** ユーザーが現在の時刻、要確認行、次の確認操作を理解でき、low-confidence があるときだけ timing confirmation 後に生成へ進める。full-text confirmation と final preview を維持し、UI に business logic がなく、再起動後に証明できない確認を再利用しない。
**コミット境界:** UI、view model、timing confirmation service 境界、該当 tests だけを `T1-4` としてコミットする。ページ shell / 工程 bar の刷新差分を混ぜない。
**推定工数:** 1〜1.5 日。
**禁止事項:** 通常 rerun の外部処理、UI への schema / fingerprint / alignment 複製、全行編集の強制、低信頼行の黙った移動、確認状態の推測、破壊操作の確認省略。

#### T1-5: 同期 component acceptance

**タスク状態:** [ ] 未着手。T1-1〜T1-4 の独立 review PASS 後に着手する。完了しても S9-6 は開いたままにする。
**目的:** T1 の component contract である A/B、gold、保存・失効・fallback、UI gate、scope guard を一度の同期受け入れで検証し、S9-6 が最後に formal phase acceptance を実施できる状態を作る。T1-5 PASS は S9-6 の人 preview、A/B、gold、失効、cache、fallback、scope gate を省略する根拠にならない。
**位置づけ:** T1-5 は同期 component acceptance、S9-6 は S9 の formal phase acceptance である。T1-5 の immutable evidence は、manifest / gold / runtime / model / policy / artifact fingerprint が一致し再検証できる範囲で S9-6 が参照してよいが、S9-6 は必要な人 preview と最終 gate を独立に確認する。
**前提:** T1-1〜T1-4 の commit / review PASS、S9-1〜S9-5 の証跡、R2 の安全境界、実 upload / Studio 操作を行わない受け入れ環境。
**変更ファイル範囲:** `benchmarks/t1/` の受け入れ harness、`docs/benchmarks/` の T1-5 証跡、新規または T1 専用の `tests/test_t1_timing_sync.py`、検証用 copy / 隔離 `data_dir` への preview 出力、`docs/execution-plan-v3.md` と `docs/requirements-v3.md` の事実ベースのチェック更新。production `src/`、production artifact / cache / output / hash、既存 S9-1 監査節、learning log は変更しない。

**作業:**

- [ ] T1-5-1. T1-1 の固定 manifest / gold / coverage / gate で A/B と metric、CER、固有名詞、cue 欠落 / 重複、wall、peak memory を再現する
- [ ] T1-5-2. in-range / out-of-range VTT、parent / payload / model / settings / audio、policy、text / time の変更と cache restart を検証し、失効対象を証跡化する
- [ ] T1-5-3. failure / fallback、legacy、VTT、low-confidence、timing confirmation、全文確認、final preview、通常 rerun、scope guard を通しで確認する
- [ ] T1-5-4. 全 `uv run pytest`、`git diff --check`、`uv run python -m compileall -q src`、再現 command、隔離 `data_dir` または production 素材の検証用 copy への再生成 preview を実行し、production artifact / cache / output / hash unchanged を保存する
- [ ] T1-5-5. 実 upload、公開データ変更、Studio、全編 Whisper、47 本 backfill が無いことを確認し、T1-5 のみを完了へ更新する。T1-5 は AC-40 の証跡を揃えるが AC-40 を `[x]` にせず、S9-6 の未確認 checkbox と S9 / M16 も更新しない

**テスト / 検証:** T1 A/B と fixed gold、in / out range 失効、cache restart、failure / fallback、legacy、scope guard、隔離 `data_dir` / 検証用 copy への再生成 preview、production hash unchanged、全 pytest、diff check、compileall、独立レビュー。

**Done 条件:** T1 の全契約と AC-40 の証跡が揃い、T1-5 の component Go / No-Go を記録できる。T1-5 が PASS しても S9-6 が一度だけ人 preview・最終 A/B・gold・失効・cache・fallback・scope の formal phase 判定を行うまで、S9-6、S9、M16、AC-37、AC-40 は未完了のまま残る。AC-40 は S9-6 formal PASS 時にだけ `[x]` へ更新する。
**コミット境界:** T1 受け入れ harness / 証跡 / tests と事実ベースの計画更新だけを `T1-5` としてコミットする。S9-6 の判定や production code の追加変更を混ぜない。
**推定工数:** 1 日。
**禁止事項:** 実 upload、公開データ変更、Studio、production data / artifact / cache / output の再生成、全編 Whisper、47 本 backfill、S9-6 の先行完了、AC-37 / AC-40 の先行完了。preview は必ず隔離 `data_dir` または検証用 copy へ出力する。

**T1-PLAN 完了時点の次アクション:** `T1-1` の固定 manifest を測定前に確定し、production 非変更 spike の Go / No-Go を独立 review する。T1-1 が PASS するまで T1-2 の ADR や production schema を作らない。

#### S9-6: A/B 受け入れ・回帰・フェーズ判定

**目的:** 精度改善が実運用の毎日 3 本に効くことと、既存 VTT 経路・人確認・境界安全性が非回帰であることを証跡化し、S9 を完了または fallback-only と判定する。
**位置づけ:** S9-6 は T1-5 の component acceptance とは別の S9 formal phase acceptance である。T1-5 の immutable evidence は fingerprint と入力条件が一致する場合に参照できるが、人 preview、A/B、gold、失効、cache、fallback、scope の最終 gate を省略しない。
**対応要件 / AC:** 運用目標、FR-22、FR-25、FR-30、FR-33、FR-35、FR-36、FR-39、AC-30、AC-35、AC-37、AC-40。

**前提:** S9-1〜S9-5 と T1-5 の完了、S9-1 と同じ 3〜5 本の代表素材・gold transcript・固有名詞 glossary・固定 gate、可能なら短い候補と 180 秒超候補を含む実配信アーカイブ 2 本以上、ユーザーが確認できるローカル runtime / model。実 YouTube upload はこのタスクの自動試験に含めない。

**S9-1 境界 evidence の再確認:** final short preview では4 caseの editorial outcome（case 1 は今回の境界・連続性で追加処置なし、case 2・3 は opening trim / review、case 4 は internal gap removal / review）を再確認する。親候補の無発話を許すことと、最終 short に無発話を残さないことは別の acceptance とし、S9-1 の partial auditだけで S9 を完了・Go にしない。

**変更ファイル範囲:** `docs/benchmarks/` の A/B 記録、S9 専用受け入れ fixture / test、`docs/execution-plan-v3.md` と `docs/requirements-v3.md` のチェック更新。既存 production data、README、投稿 API は変更しない。

**作業:**

- [ ] S9-6-1. 同じ親候補について VTT route と精査 route を比較し、CER、固有名詞、cue 欠落 / 重複、境界確認、テロップの人確認結果、wall time、cache hit 時間を記録する
- [ ] S9-6-2. 代表素材で「VTT で親候補選定 → 選択区間だけ Whisper → cutplan → 同じ artifact で telop → 人確認 → 生成」を通し、180 秒以下の単一区間経路も確認する
- [ ] S9-6-3. VTT を使用範囲内・範囲外で変え、candidate / cutplan / telop / review / output の失効差を確認する。Whisper artifact の model / settings / audio input を変えた場合は高精度扱いが解除されることを確認する
- [ ] S9-6-4. `uv run pytest` 全件、`git diff --check`、S9 benchmark の再現 command、手動 UI 証跡、導入できない runtime の日本語 fallback をまとめる
- [ ] S9-6-5. Go の場合だけ進捗サマリーの S9 実装タスクと AC-30 / AC-35 / AC-37 / AC-40 を更新する。AC-40 は T1-5 で証跡が揃っていても、この formal PASS 時にだけ `[x]` にする。No-Go または fallback-only の場合は S9 と AC-40 を完了にせず、根拠と次段階候補を記録する
- [ ] S9-6-6. S9-1 の CER 相対改善 10％、固有名詞 exact match 非悪化、cue 欠落 / 重複 baseline +5％以内、wall time / peak memory budget を同じ fixture で再判定し、閾値未達は No-Go のまま残す

**テスト:** unit / integration / UI tests、同じ 3〜5 本の fixture A/B、実配信アーカイブ 2 本以上（取得できる場合）、cache restart、failure injection、実機 UI 1 本。実 YouTube の字幕取得は承認済み素材に限り、投稿・概要欄反映・削除・全本 backfill は行わない。

**Done 条件:**
- [ ] A/B 数値と目視・人確認の証跡、選択モデル、処理時間、失効差、回帰結果、fallback の挙動、Go / No-Go が独立レビュー可能な形で残る
- [ ] S9-1 と同じ gold / glossary / threshold / budget が再現 command と fixture fingerprint に結び付き、代表素材と実配信アーカイブの差が記録される
- [ ] S9 初版の scope 外（全編 Whisper、字幕なし通常経路、local video、asset ID）は実装されていない
- [ ] T1-5 の immutable evidence の再利用条件が確認され、人 preview、A/B、gold、失効、cache、fallback、scope の formal gate を独立に通過し、AC-40 を `[x]` に更新する

**S9-6 受け入れ証跡の追記:** 2026-08-04 main の `3d113ef` / `071929d` 統合後、初回レビューで P1 二点を指摘し、follow-up で APPROVE とした。main focused S9 は123件 passedしたが、判定は fallback-only のまま人 UI 確認待ちである。次の具体的な確認は case2 / case3 の opening trim 後 preview、case4 の internal gap removal 後 preview、final short に無発話がないことの確認とする。exact gold / glossary / cue anchor 監査または明示的 waiverも未完了であり、S9-6 の全チェック、S9、M16、AC-30 / AC-35 / AC-37 の受け入れ判定は未完了を維持し、AC-40 も S9-6 formal PASS まで未完了とする。

**S9-6 実機障害 hotfix・統合実績（2026-08-04）:** 実 UI で選択区間の高精度字幕 job が resolver 段階で失敗した原因を、`YTLK_FFMPEG_PATH` に設定済みの `ffmpeg-full` が yt-dlp の部分音声取得へ渡らず、壊れた PATH 上の `ffmpeg` に依存していたことと特定した。HF1 `4a37533` で設定済み実行ファイルの解決・起動確認を network 前に fail closed で行い、yt-dlp へ明示的な `--ffmpeg-location` を渡した。cache hit は従来どおり不要な preflight / network を省略する。HF2 `b68429c` では S9 の構造化 error を持つ失敗だけを「選択区間の高精度字幕」と表示し、通常の short cut 提案ラベルを維持した。さらに current review fingerprint、queue fingerprint、artifact reference / fingerprint / ordered cue digests と実 artifact の現行性がすべて一致する場合だけ保存済み strict telop document を復元して古い一時 error を除き、不一致・破損・失効時は fail closed を維持した。Codex の自動再呼び出し・自動承認・自動生成は追加していない。各実装の独立レビュー後、両修正の統合レビューも APPROVE（P0 / P1 なし）。main 上で focused 294 passed、全体 1595 passed / 2 skipped、lock / diff / compile を再確認し、設定値 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` が実体 `/opt/homebrew/Cellar/ffmpeg-full/8.1.2_2/bin/ffmpeg` へ解決され version preflight を通ることを確認した。これは実機障害の修正記録であり、case2 / case3 の opening trim 後 preview、case4 の internal gap removal 後 preview、final short の人確認は未実施のため、S9-6 の全チェック、S9、M16、AC-30 / AC-35 / AC-37 は未完了を維持する。

**S9-6 実機障害 HF3・統合実績（2026-08-04）:** HF1 後の実行で yt-dlp の `--download-sections` 出力が要求区間を末尾方向へ 3,994〜8,994 ms 超過し、strict parser が範囲外 cue を正しく拒否していたこと、および whisper.cpp 1.9.1 の正規 full JSON が `text` に空白を持つ token metadata を出力することを追加原因として特定した。HF3-1 は初回独立レビューで `-frames:a` が PCM sample 単位の exact trim にならない P1 を検出してコミットを止め、16 kHz への `aresample` 後に `atrim=end_sample` で `requested_duration_ms × 16` frames へ正規化する方式へ修正した。正規化後 bytes だけを cache / fingerprint に採用し、旧 oversized cache、undersized input、FFmpeg timeout / nonzero、欠損・形式不一致・frame 不一致は fail closed とした。HF3-2 は `token.text` だけ空文字・空白文字列を許容し、文字列型、NUL 禁止、strict schema / timing、非空の segment 本文、範囲外 cue 拒否を維持した。個別レビューと統合レビューは APPROVE（P0 / P1 なし）で、main へ `84d31f9` → `a769f64` の順に統合した。main 上の focused は 83 passed、全体は 1612 passed / 2 skipped、lock / diff / compile を再確認し、再起動後の Streamlit health は `ok` だった。隔離した一時 data directory で動画 `LB4px1wRFnY` の選択 4 区間だけを実 YouTube audio-only route で処理し、857000〜875000 ms は 288000 frames / 1 cue、996000〜1012000 ms は 256000 frames / 2 cues、1069000〜1101000 ms は 512000 frames / 5 cues、1104000〜1140000 ms は 576000 frames / 6 cuesで全件 success、job status は success、`is_high_precision=true`、全体 wall time は 79.987 秒だった。新しい別原因はなく HF3 は Go とするが、case2 / case3 の opening trim 後 preview、case4 の internal gap removal 後 preview、final short の人確認と benchmark gold 監査は未実施のため、S9-6 の全チェック、S9、M16、AC-30 / AC-35 / AC-37 は未完了を維持する。

**S9-6 実機障害 HF4・音声復旧実績（2026-08-04）:** 動画 `gZAoIbp4lBc` でショート生成が成功表示になった一方、完成 mp4 に音声 stream が無かった原因を、yt-dlp が映像 `f399.mp4` と音声 `f140.m4a` を取得した後の merge に設定済み `ffmpeg-full` を使わず、video-only の mp4 を source として選択していたこと、および生成経路が `-map 0:a?` により音声欠損を許容していたことと特定した。HF4-1 `4220600` は設定済み FFmpeg の network 前 preflight、yt-dlp への `--ffmpeg-location` と `--keep-video`、同居する ffprobe で映像・音声の両 stream を持つ source だけを採用する fail closed 契約を追加した。初回統合レビューでは yt-dlp の既定後処理が sidecar を削除し得る P1 を検出し、`--keep-video` と非破壊 test を追加して解消した。HF4-2 `b7394dc` は source、各 segment、concat、最終一時出力の全段で映像・音声を必須化し、明示的な audio mapping、検査済み一時出力だけの atomic replace、video-only output の queue 再利用拒否を追加した。修正後は元の `f399.mp4` と `f140.m4a` の SHA-256 を変えずに 7,499.047 秒の映像・音声付き source を生成し、同じ queue spec を新 job `4b0443196d4a4aa28a724691ccc3ef2e` で再実行した。完成 `short_f0e28ecf6638.mp4` は H.264 53.000 秒、AAC stereo 53.026 秒、音量 mean -25.7 dB / max -1.4 dB、全 decode 成功で、旧 video-only output は再利用していない。UI は工程 5／6「最終確認」に戻り、`preview_confirmed_fingerprint` を null として新しい完成動画の再確認を要求する。個別レビューと統合レビューは最終 APPROVE（P0 / P1 / P2 なし）で、focused 239 passed / 2 skipped、main 全体 1645 passed / 2 skipped、lock / diff / compile を再確認し、Streamlit health は `ok` だった。音声生成基盤の障害は解消したが、完成内容の人確認、case2 / case3 の opening trim 後 preview、case4 の internal gap removal 後 preview、benchmark gold 監査は未実施のため、S9-6 の全チェック、S9、M16、AC-30 / AC-35 / AC-37 は未完了を維持する。

**コミット境界:** benchmark / acceptance docs と進捗更新を `S9-6` のフェーズ受け入れコミットに含める。S9 を完了にする場合だけフェーズ状態を `[x] 完了`、M16 と AC の該当チェックを更新する。

**S9 / T1 のコミット順:** `S9-0` → `S9-1` → `S9-2` → `S9-3` → `S9-4` → `S9-5` → `T1-PLAN` → `T1-1` → `T1-2` → `T1-3` → `T1-4` → `T1-5` → `S9-6`。各タスクは実装、単体確認、Done 条件、レビュー後に個別コミットし、未確認の AC を先に `[x]` にしない。

**見積もり目安:** S9-0 0.5 日、S9-1 0.5〜1 日、S9-2 1〜1.5 日、S9-3 1〜2 日、S9-4 1.5〜2 日、S9-5 1〜1.5 日、S9-6 1 日。合計 6.5〜9.5 日。benchmark の No-Go は実装を止めるため、速度は固定の完了条件ではなく S9-6 で採否を判断する。

### P6: Shorts 投稿メタデータ品質ゲート + 関連動画確認追跡

**目的:** 新規ショートのタイトルを役割の異なる 3 方向から選べるようにし、チャンネル登録 CTA と切り抜き元動画案内を投稿前 hard gate で概要欄から欠落させない。YouTube Studio の「関連動画」は upload 成功後の人確認工程として再起動後も追跡するが、未確認を理由に既存の予定公開を技術的に止めない。

**背景:** P4 は任意テンプレートの合成までを実装したが、テンプレート未設定時の後方互換 fallback を許しているため、現在の予約投稿経路ではチャンネル登録 CTA と元動画案内を必須にできない。また Shorts 概要欄の URL は主要クリック導線として扱えないため、元動画への導線は YouTube Studio の「関連動画」を別の人確認工程として管理する必要がある。YouTube Data API に書き込み可能な関連動画 field があるとは仮定せず、P6 は Studio 手動操作のローカル記録だけを実装する。関連動画は upload 後にしか設定できないため、`pending` は要対応表示と集計に使うだけで、`publishAt` の取消・延期・変更や publication poll の停止を行わない。

**フェーズ状態:** [x] 完了

**全体依存:** `P6-PLAN → {P6-1, P6-2, P6-3} → P6-4 → P6-5`。P6-1〜P6-3 は変更ファイルが重ならない分離 worktree で並行実装できる。P6-4 は 3 タスクが main に統合された後だけ開始する。P6-1 の `telop.py` は S9-4 と競合するため、P6-1 のレビュー PASS・main 統合・依存元タスクへの完了報告を S9-4 着手より先に行う。

#### P6-PLAN: 要件・AC・変更範囲・単一 writer 規約

**目的:** コード変更前に FR-37 / FR-38、AC-38 / AC-39、実装分割、依存順、並列 worktree の writer 境界を docs の正本へ固定する。

**タスク状態:** [x] 完了

**作業:**

- [x] P6-PLAN-1. `requirements-v3.md` にタイトル 3 方向、概要欄必須構成と二重再検証、関連動画の Studio 手動確認・永続状態を定義する
- [x] P6-PLAN-2. P6-1〜P6-5 の目的、変更ファイル範囲、Done 条件、テスト、統合順を本節へ定義する
- [x] P6-PLAN-3. `AGENTS.md` と `v3-agent-prompts.md` に P6 並列期間だけの単一 writer 例外を定義し、S9-1 監査節と `.codex/learning/user-decisions.md` を保護対象として明記する
- [x] P6-PLAN-4. 独立レビューで要件矛盾、ファイル競合、後方互換、確認境界、API scope を欠陥優先で確認し、指摘を反映する
- [x] P6-PLAN-5. docs 4 ファイルだけを `P6-PLAN` コミット `410e99a` として main に記録し、既存の未コミット `.codex/learning/user-decisions.md` を混ぜない

**Done 条件:**

- [x] FR-37 / FR-38 と AC-38 / AC-39 が相互に矛盾せず、P4 の歴史的 fallback と P6 投稿ゲートの優先関係が明記されている
- [x] 3 並列タスクの変更範囲が重ならず、共有 UI・docs・進捗チェックの writer が一意である
- [x] P6-1 main 統合前に S9-4 を開始しない依存が計画とワーカー指示の両方にある
- [x] 実 upload・公開データ変更・Studio 自動操作を行わず、API mock だけで検証する scope が固定されている
- [x] 独立レビュー PASS 後の docs-only commit から各 worktree を作成できる

**コミット境界:** `AGENTS.md`、`docs/requirements-v3.md`、`docs/execution-plan-v3.md`、`docs/v3-agent-prompts.md` だけを `P6-PLAN` としてコミットする。

#### P6 並列期間の単一 writer 規約

| 所有者 | 書き込み可能範囲 | 禁止事項 |
|--------|------------------|----------|
| P6 オーケストレーター | `AGENTS.md`、P6 関連の `docs/requirements-v3.md` / `docs/execution-plan-v3.md` / `docs/v3-agent-prompts.md`、main への cherry-pick と進捗更新 | S9-1 の benchmark / gold 監査節・証跡を編集しない。`.codex/learning/user-decisions.md` を P6 commit に含めない |
| P6-1 worktree | `prompts/telop_script.md`、`src/yt_live_kit/services/telop.py`、`tests/test_telop.py` | docs、UI、description、upload model / queue を編集しない |
| P6-2 worktree | `src/yt_live_kit/services/description.py`、`tests/test_description.py` | docs、UI、schedule、upload model / queue、既存テンプレート実データを編集しない |
| P6-3 worktree | `src/yt_live_kit/models/upload.py`、`src/yt_live_kit/services/upload_queue.py`、必要な場合だけ新規 `src/yt_live_kit/services/related_video.py`、`tests/test_upload_queue.py`、必要な場合だけ新規 `tests/test_related_video.py` | docs、UI、description、schedule、telop を編集しない |
| P6-4 worktree | P6-1〜P6-3 統合後の `src/yt_live_kit/ui/components/upload.py`、`src/yt_live_kit/services/schedule.py`、`src/yt_live_kit/models/upload.py`、`tests/test_ui_upload.py`、`tests/test_schedule.py`。公開 API の接続不備が判明した場合だけ P6-2 の `description.py` / `test_description.py` または P6-3 の `upload_queue.py` / `test_upload_queue.py` を最小修正できる | P6-1〜P6-3 と同時に開始しない。`prompts/telop_script.md`、`services/telop.py`、`tests/test_telop.py`、S9 docs / code、設定画面を変更しない |
| P6-5 オーケストレーター | P6 関連チェック、受け入れ証跡、必要な test / docs のみ | 実 YouTube 書き込み、未確認 AC の先行チェックをしない |

並列 worktree のワーカーは `execution-plan-v3.md` を**読取専用**とし、完了チェックを編集しない。各ワーカーは変更ファイル、テスト件数、commit hash、未完了事項を報告し、レビュー PASS と main 統合後にオーケストレーターだけが P6 節のチェックを更新する。P6 以外の S9-1〜S9-6 節と進捗行は依存元タスクの所有とし、P6 側は同じ行を編集しない。

#### P6-1: タイトル 3 方向生成・検証

**対応要件 / AC:** FR-23、FR-37、AC-23、AC-38。

**変更ファイル範囲:** `prompts/telop_script.md`、`src/yt_live_kit/services/telop.py`、`tests/test_telop.py` のみ。

**作業:**

- [x] P6-1-1. 同一 Codex 呼び出しの `title_candidates` を固定順の検索明快型・仕事影響型・好奇心型の 3 件にするプロンプト契約を追加する
- [x] P6-1-2. 新規生成境界では 3 件ちょうど、strip 後非空、相互非重複、100 文字以下、半角山カッコ無しを検証し、方向名を含む日本語エラーを返す
- [x] P6-1-3. 18〜32 文字は警告に留め、既存の 1〜2 件候補を持つ保存済み document の読み込み・編集・再保存を壊さない検証 API を設計する
- [x] P6-1-4. Codex 呼び出し回数が増えず、既存 telop segments / hook / description / tags / softfail を変更しないことをテストする

**Done 条件:**

- [x] 新規生成は方向の異なるタイトル 3 件が無い限り成功にならず、legacy document は読み込み互換を保つ
- [x] 長さ警告、重複、空、半角山カッコ、100 文字境界が自動テストされる
- [x] `uv run pytest tests/test_telop.py` と全件 `uv run pytest` が通る（対象 71 passed、全件 1329 passed / 2 skipped）
- [x] 独立レビューで P0 / P1 finding がなく、main に `P6-1` commit `96aa302` / `0e03a23` として統合される
- [x] 統合完了を依存元タスク `019fc522-6501-71c1-aed4-03aa03a4ae7c` へ報告し、S9-4 は `0e03a23` 以降から開始可能であることを通知した

**コミット境界:** 変更範囲だけを `P6-1` としてワーカー commit にし、レビュー PASS 後に main へ cherry-pick する。P6-1 の main 統合を P6 内で最優先する。

#### P6-2: 概要欄必須構成・投稿前再検証 service

**対応要件 / AC:** FR-29、FR-37、AC-29、AC-38。

**変更ファイル範囲:** `src/yt_live_kit/services/description.py`、`tests/test_description.py` のみ。

**作業:**

- [x] P6-2-1. テンプレートが無い場合だけ、必須 placeholder 3 種と固定 CTA 文「チャンネル登録は動画下のチャンネル名からお願いします。」を含む既定テンプレートを lock + atomic write で初回作成する。既存ファイルは bytes を含めて変更しない
- [x] P6-2-2. 生成説明、元動画タイトル、開始秒付き元動画 URL、固定 CTA 文の完全一致、template bytes fingerprint、`meta.json` fingerprint を不変の要件 object に構造化し、合成結果と最終編集済み本文を同じ純粋 validator で検証できる service API を追加する
- [x] P6-2-3. 必須 placeholder / 解決後項目の欠落、壊れた meta、半角山カッコ、5000 bytes、同名競合、atomic write 失敗を不足箇所が分かる日本語エラーで fail closed にする
- [x] P6-2-4. P4 の非投稿 fallback と長尺用 `description_template.txt` の動作を維持し、投稿ゲートは P6-4 から明示的に呼べる後方互換 API とする

**Done 条件:**

- [x] 初回作成、既存保持、同時作成、失敗時の部分ファイル非残存が自動テストされる
- [x] 合成直後と最終編集後に同じ不変要件 object で必須項目を検証でき、日本語エラーが安定している。mutable な template / meta の再読込を confirm 境界へ要求しない
- [x] P4 fallback、開始秒 URL、長尺概要欄、5000 bytes、半角山カッコの既存テストに回帰が無い
- [x] `uv run pytest tests/test_description.py` と全件 `uv run pytest` が通る（対象 30 passed、全件 1330 passed / 2 skipped）
- [x] 独立レビューで P0 / P1 finding がなく、main に `P6-2` commit `1a70646` / `18a811b` として統合される

**コミット境界:** description service と専用テストだけを `P6-2` として commit する。UI / schedule への接続は P6-4 へ分離する。

#### P6-3: 関連動画の Studio 手動確認・永続状態

**対応要件 / AC:** FR-38、AC-39。

**変更ファイル範囲:** `src/yt_live_kit/models/upload.py`、`src/yt_live_kit/services/upload_queue.py`、必要な場合だけ新規 `src/yt_live_kit/services/related_video.py`、`tests/test_upload_queue.py`、必要な場合だけ新規 `tests/test_related_video.py`。

**作業:**

- [x] P6-3-1. upload operation に後方互換な `related_video_status`（`not_ready` / `pending` / `confirmed`）と UTC の `related_video_confirmed_at` だけを追加し、対象 ID は既存 `source_video_id` / `video_id` を唯一の正本として再利用する。P6 専用の重複 ID field、未知状態、不整合 field を拒否する
- [x] P6-3-2. upload 成功時だけ `pending` へ遷移し、対象 ID が一致する明示確認時だけ `confirmed` にする lock + atomic queue API を追加する。API / ブラウザを呼ぶ処理は持たせない
- [x] P6-3-3. legacy queue で related field が欠落する場合は `state=uploaded` かつ `video_id` ありだけ `pending`、それ以外を `not_ready` に移行し、`confirmed` は推測しない。再起動、二重確認、対象 ID 競合、壊れた JSON、atomic fault injection で既存 operation を保護する
- [x] P6-3-4. 既存の upload / publication poll / slot / attempt / reconciliation の状態遷移と schema 読み込みを回帰テストする
- [x] P6-3-5. lock 付き queue 読み出しから `pending` の件数と対象 operation 一覧を返す service API を追加し、UI が queue JSON を直接走査しなくてよい契約と再起動テストを固定する
- [x] P6-3-6. `pending` のままでも既存 `publishAt`、publication eligibility、poll history を変更せず、publication poll と予定公開を停止しないことをテストする

**Done 条件:**

- [x] upload 前は `not_ready`、成功後は `pending`、明示確認後だけ `confirmed` となる
- [x] legacy operation を読み込め、欠落 field から確認済みを推測しない
- [x] queue 更新が lock + atomic で、競合・破損・失敗時に既存 record を保持する
- [x] pending 件数と対象一覧が service から決定的に取得でき、壊れた queue を 0 件扱いにしない
- [x] 関連動画 field は既存 2 ID と二重管理せず、pending は予定公開の hard gate にならない
- [x] `uv run pytest tests/test_upload_queue.py` と全件 `uv run pytest` が通る（対象 75 passed、全件 1328 passed / 2 skipped）
- [x] 独立レビューで P0 / P1 finding がなく、main に `P6-3` commit `1ab7658` として統合される

**コミット境界:** upload model / queue の状態契約と専用テストだけを `P6-3` として commit する。Streamlit UI は P6-4 へ分離する。

#### P6-4: 投稿 UI 統合・確認ダイアログ

**前提:** P6-1、P6-2、P6-3 がすべてレビュー PASS 後に main へ統合済みであること。

**対応要件 / AC:** FR-27、FR-37、FR-38、NFR-13、AC-27、AC-38、AC-39。

**変更ファイル範囲:** `src/yt_live_kit/ui/components/upload.py`、`src/yt_live_kit/services/schedule.py`、`src/yt_live_kit/models/upload.py`、`tests/test_ui_upload.py`、`tests/test_schedule.py`。公開 API の接続不備が判明した場合だけ、P6-2 の `src/yt_live_kit/services/description.py` / `tests/test_description.py` または P6-3 の `src/yt_live_kit/services/upload_queue.py` / `tests/test_upload_queue.py` を最小修正できる。**P6-1 所有の `prompts/telop_script.md`、`src/yt_live_kit/services/telop.py`、`tests/test_telop.py` は変更禁止**とし、設定画面と S9 ファイルも変更しない。

**作業:**

- [x] P6-4-1. タイトル 3 方向を固定順で表示し、18〜32 文字外の警告と legacy 不足案内を出す。最終タイトルの自由編集は維持する
- [x] P6-4-2. P6-2 の既定テンプレート確保と必須構成 validator を投稿フォームへ接続し、編集後・preview 作成前・確認確定後の service 境界で同じ不変要件 snapshot を再検証する。期待 4 項目と template / meta fingerprint を preview / content snapshot / fingerprint に凍結し、confirm 後に mutable file を再読込しない
- [x] P6-4-2a. content snapshot への概要欄要件 field は legacy operation を読める optional default とし、新しい P6 投稿 preview / confirm では必須にする。legacy record を自動で確認済みにせず、新しい preview の作り直しを日本語で案内する
- [x] P6-4-3. 必須項目欠落時は日本語で不足箇所を示し、確認 dialog、operation、job、upload attempt、API session を開始しない。確認 snapshot と送信 body の一致を維持する
- [x] P6-4-4. uploaded operation に Studio 編集先、対象元動画、手順、状態を表示し、P6-3 service から得た pending 総件数と対象一覧を表示する。対象 ID を含む `st.dialog` 確定後だけ local confirmed API を呼び、UI が queue JSON を直接集計しない
- [x] P6-4-5. rerun、再起動、二重クリック、legacy operation、publication poll、予約枠、投稿確認の既存導線を UI / service テストで回帰確認する

**Done 条件:**

- [x] AC-38 / AC-39 の UI・service 接続がモックで通り、確定前に外部 write が無い
- [x] 概要欄を最終編集して必須項目を削除する race が preview 前と confirm 後の両方で止まる
- [x] legacy content snapshot は読み込めるが新しい P6 投稿には再利用されず、要件 snapshot を持つ新 preview が必須になる
- [x] 関連動画の確認は local queue だけを更新し、YouTube API / ブラウザを呼ばない
- [x] pending 表示・集計が既存 `publishAt` や publication poll を変更せず、予定公開の hard gate にならない
- [x] `uv run pytest tests/test_ui_upload.py tests/test_schedule.py` と全件 `uv run pytest` が通る
- [x] 独立レビューで P0 / P1 finding がなく、main に `P6-4` commit `aaa89cf` として統合される

**P6-4 受け入れ証跡（2026-08-03）:** 対象 4 テスト群 235 件、全件 1,380 件（skip 2 件）が main で通過。実 YouTube / Studio write は行わず、API 境界はモックで検証した。実装セッション内レビューとオーケストレーター独立レビューはいずれも最終 APPROVE（残存 P0 / P1 なし）。

**コミット境界:** 3 service の UI / schedule 接続と統合テストだけを `P6-4` として commit する。

#### P6-5: 統合受け入れ・回帰

**前提:** P6-1〜P6-4 が個別レビュー PASS 後に main へ統合済みであること。

**作業:**

- [x] P6-5-1. AC-38 / AC-39 の全境界を API mock と一時 data dir で確認し、実 YouTube upload・公開データ変更が無いことを記録する
- [x] P6-5-2. 半角山カッコ、100 文字タイトル、5000 bytes 説明文、500 文字タグ、日本語エラー、confirmation race、legacy queue / telop / template の後方互換を欠陥優先で再確認する
- [x] P6-5-3. `uv run pytest`、変更範囲 diff、P6-1 main 統合が S9-4 より先であることを確認する
- [x] P6-5-4. 独立最終レビューの指摘を同じ実装セッションへ戻して修正し、再レビュー PASS 後だけ AC と進捗を完了へ更新する

**Done 条件:**

- [x] AC-38 / AC-39 がすべて `[x]` で、証跡がテスト名・件数・commit hash に結び付く
- [x] P6 scope 外の Studio 自動操作・関連動画 API write・実 upload・新規依存が無い
- [x] 全件テストと独立最終レビューが PASS する
- [x] P6-1〜P6-5 と P6 の進捗が完了し、未コミットの学習ログと S9-1 監査節が変更されていない

**P6-5 最終受け入れ証跡（2026-08-03）:** P6 境界 6 ファイルの 410 件と全件 1,380 件（skip 2 件）が main `99bfc9c` で通過し、`git diff --check` も通過した。代表テストは `test_generation_invokes_codex_once_and_saves_valid_document`、`test_quality_gate_creates_default_template_and_freezes_requirements`、`test_confirmed_preview_description_reaches_worker_and_insert_body_unchanged`、`test_job_target_description_gate_fails_operation_before_api_attempt_or_report`、`test_related_video_confirmation_requires_both_canonical_ids_and_is_idempotency_safe`、`test_upload_section_keeps_global_related_pending_reachable_without_latest_manifest`、`test_metadata_boundaries`。P6 の main 統合は `410e99a` / `0202eb6`（計画）、`96aa302` / `0e03a23` / `665ba2f`（P6-1）、`1a70646` / `18a811b` / `4e6b274`（P6-2）、`1ab7658` / `9af5a4e`（P6-3）、`aaa89cf` / `99bfc9c`（P6-4）。テストは YouTube API をモックし、実 upload、公開データ変更、Studio 自動操作、関連動画 API write、追加 Codex 呼び出しを行っていない。P6-1 は S9-4 より先に main へ統合済みで、S9-4 commit はまだ存在しない。P6 開始前後で S9-1 監査節の SHA-256 は `7872b8aa5425087ee4d0a31e754a27a6f6ee3ec207899dfaf12018354c87e5ef` のまま一致し、`.codex/learning/user-decisions.md` の既存未コミット変更は P6 commit に含めていない。実装セッション内再レビュー、P6-4 独立レビュー、P6-5 独立最終レビューはすべて APPROVE（残存 P0 / P1 なし）。

**P6 のコミット順:** `P6-PLAN` → `P6-1` / `P6-2` / `P6-3`（分離 worktree、個別レビュー後に順次 main 統合。P6-1 を最優先）→ `P6-4` → `P6-5`。main への統合はオーケストレーターだけが行う。

---

### R2: UI 大幅刷新前の境界整理・回帰リスク監査

**目的:** 手動 E2E でショート生成から予約投稿まで完走した現行挙動を基準に、`docs/references/u6-short-production-line-v3.2.png` の視覚階層へ大幅刷新する前に、表示・session state・永続 state・投稿 transaction の境界を整理する。見た目の変更は行わず、UI 再配置で壊れやすい状態契約を純粋 view model と characterization test へ固定し、監査結果を独立文書に残す。

**フェーズ状態:** [x] 完了

**前提・制約:** 2026-08-04 の手動 E2E 完走を受け入れ基準とし、外部 API、実 upload、実 Codex 呼び出し、動画再生成、既存成果物の削除は行わない。queue / review / output fingerprint、概要欄の immutable requirements、`run_line_reservation_transaction()`、`confirm_and_start_upload()` の transaction は挙動を変えない。S9-6 の人確認と benchmark 判定は別タスクとして未完了を維持する。

**変更ファイル範囲:** `docs/execution-plan-v3.md`、`docs/tech-stack.md`、新規監査文書、`pyproject.toml`、`uv.lock`、`src/yt_live_kit/ui/app.py`、`ui/state.py`、`ui/components/results.py` / `status_bar.py` / `shorts_line.py` / `shorts_queue.py` / `short_cut.py` / `upload.py`、`ui/views/video_detail.py` / `library.py`、新規 `ui/view_models/` / `ui/session_keys.py` / `ui/queries.py` / `ui/controllers/`、対応する `tests/test_ui_*.py` を許可する。ライン開始を原子的・再試行可能な command にするため必要な場合だけ `services/shorts_line.py` / `tests/test_shorts_line.py` を最小変更できる。`requirements-v3.md`、`docs/benchmarks/`、S9-6 fixture / evidence、production `data/`、prompt、`models/`、description / schedule / upload queue / YouTube / transcript artifact service は read-only とする。

**追加の不変条件:** `TranscriptArtifactRef`、artifact fingerprint、ordered cue digest、coarse candidate fingerprint、高精度 / fallback 表示を変えず、通常 rerun で Whisper / Codex を起動しない。P6 の description requirements snapshot、upload operation、schedule / publication poll を変更しない。実 browser 確認は隔離した一時 `data_dir` または検証用コピーで行い、production data を読み書きしない。

**作業:**

- [x] R2-1. `1645 passed / 2 skipped` を回帰基準に、現行画面・リファレンス画像、`app.py` / `results.py` / `status_bar.py` / `video_detail.py` / `shorts_line.py` / `short_cut.py` / `upload.py`、状態永続化、テスト結合度を監査し、優先度、再現条件、刷新時の影響、保護すべき安全境界を `docs/ui-refactor-review-2026-08-04.md` に記録する
- [x] R2-2. ページ境界を整理する。全ページ末尾へ旧 `render_results()` を混入させず、全ページで描画されるサイドバーは session snapshot を復元・保存しない読み取り専用 projection にする
- [x] R2-3. 詳細・投稿境界を整理する。予約可能件数は `can_reserve_shorts_queue_item()` と同じ service gate で数え、永続 operation / 関連動画 pending / 要照合 / publication poll を最新 manifest の件数 0 でも表示する。ライン投稿は必須 adapter で preview 検証と reservation transaction を一体で渡す
- [x] R2-4. 再生成・並び替えに強い widget state 契約を追加する。候補引き継ぎは保存済み coarse lineage fingerprint を優先する。telop は編集前 document 全体、queue fingerprint、artifact lineage、short-cut は提案 document 全体から immutable draft identity を作り、同じ identity では buffer を保持、新 identity では widget 描画前に限定 prefix だけを初期化する。親候補は配列 index ではなく source + ID で保持し、条件描画をまたぐ編集 widget は session persistence を明示する。`persist_state` の導入版に合わせ Streamlit の宣言最低版を 1.59 とし、lock 解決版 1.60 と区別する
- [x] R2-5. view 間 import と巨大 renderer 内の純粋計算を `ui/view_models/`、`ui/session_keys.py`、`ui/queries.py`、必要な `ui/controllers/` へ分離し、既存 import 名は互換 re-export で維持する。通常 rerun と表示だけの render は durable write を行わず、保存は名前付き command / callback に限定する。ライン開始は line state と active pointer を単一 command 境界で保存し、成功後にだけ session projection を適用する。各書込み失敗後は新 active line と line-mode snapshot を残さず、同じ操作を安全に再試行できるようにする
- [x] R2-6. 上記境界の characterization test、対象 UI test、全件 `uv run pytest -q`、`uv lock --check`、`git diff --check`、`compileall`、隔離 `data_dir` の実ブラウザ確認を行う。同一 artifact の新台本、A → B → A、同じ candidate ID の境界変更、line / pointer 各 fault、同じ状態の二重 render、動画 A 表示中の動画 B 完了通知と詳細導線を含める。upload、概要欄反映、高精度化、生成、候補確定、人確認、削除ボタンは押さない
- [x] R2-7. 実装者と別のサブエージェントが欠陥優先レビューを行い、P0 / P1 を解消してから進捗、監査文書、コミットを閉じる。既存の未コミット学習ログと skill pointer は commit に含めない

**Done 条件:**

- [x] 旧結果のページ混入とサイドバー描画時の session state 変更が無く、ページ・workspace の見た目を移動しても生成 state を暗黙変更しない
- [x] 同じ clip / candidate ID の再生成、候補並び替え、workspace 往復で、古い編集値を新 provenance へ結合せず、未再生成の手編集は保持する
- [x] 永続投稿 tracking は新規予約候補の有無から独立し、表示件数と実際の予約 gate が一致する。ライン投稿 caller は安全 callback の一部を落とせない
- [x] line state / active pointer の各永続化失敗時に新 active line と session snapshot が残らず、孤立 state を成功扱いせずに再試行できる。既存の reservation / upload transaction と確認 dialog の順序は変わらない
- [x] 同じ状態を二度表示しても line / review / spec / output / operation の durable write と Whisper / Codex 起動がなく、pipeline 完了時は旧結果を全ページへ描画せず対象動画 ID の通知と詳細導線を維持する
- [x] 外部 write、新規依存、生成品質変更がなく、変更前の `1645 passed / 2 skipped` から回帰せずに全件テストが通る
- [x] Streamlit 1.59 以上という宣言と `persist_state` の利用が一致し、`uv lock --check` と `uv sync --locked` が通る
- [x] 監査文書に未解消の構造課題、UI 刷新で触れてよい層、触れてはいけない transaction、推奨実装順が記録され、独立レビューが PASS する

**完了証跡（2026-08-04）:** 計画 commit `dcec05f`、Streamlit persistence baseline `ef07b59`、実装・test・監査 commit `8992266`。全件 `1692 passed, 2 skipped`、upload 関連 175 件、`uv lock --check`、`uv sync --locked`、`git diff --check`、`compileall` が成功した。一時 `YTLK_DATA_DIR` の実ブラウザで library / intake / settings の空状態と navigation shell を非破壊確認し、終了後に一時 data を削除した。実装者と別のサブエージェントによる最終再レビューは残存 P0 / P1 / P2 なしで PASS。実 upload、YouTube / Studio write、追加 Codex / Whisper、動画生成、概要欄更新、成果物削除は行わず、既存の未コミット学習ログと skill pointer は commit に含めていない。S9-6 と M16 は未完了のまま維持する。

**コミット境界:** 先に本計画の開始を docs commit とし、実装・test・監査文書を `R2` implementation commit、検証済みの完了チェックを直後の docs commit に分ける。S9-6 の証跡と `.codex/learning/user-decisions.md` は含めない。

---

### U9: UI 視覚刷新（テーマ適用 + shell 刷新）

**目的:** `docs/references/u6-short-production-line-v3.2.png` の視覚階層へ、R2 で整理した表示・session state・永続 state の境界を壊さずに近づける。第 1 弾は Streamlit ネイティブテーマ（案 A+）のみを `.streamlit/config.toml` で適用し Python コードを 0 行に保つ。第 2 弾はサイドバー・ヘッダ・KPI カード・ワークスペース切替・6 工程ステッパーの shell 刷新を行う。テロップ編集器の刷新（第 3 弾）は本フェーズに含めず、T1（テロップ行時刻同期）の `T1-4` に合流させる。

**フェーズ状態:** [ ] 未着手

**前提・制約:** R2（`docs/ui-refactor-review-2026-08-04.md`）が 2026-08-04 に完了し、旧結果混入・session snapshot 復元・draft revision・親候補 index 依存・予約可能件数不一致の P1 / P2 は解消済みであることを前提とする。U6 は完了・M15 達成済みで、現在の回帰基準は `1694 tests collected` である。導入版 Streamlit は 1.60.0、`pyproject.toml` の宣言は `streamlit>=1.59.0`。第 2 弾で限定 CSS 注入（案 B）の要否を判断するのは、第 1 弾（A+）適用後に残差を実測してからとする。第 3 弾（テロップ編集器の刷新）は独立フェーズとせず、`docs/execution-plan-v3.md:1836` / `:1849` の指示どおり T1 contract 確定後の `T1-4` へ合流させ、本フェーズでは新規タスク ID を振らない。

**変更ファイル範囲:** 第 1 弾は `.streamlit/config.toml` のみ。AppTest 視覚回帰スモークは新規 `tests/test_ui_visual_smoke.py`。第 2 弾は `src/yt_live_kit/ui/` の該当 view / component（`app.py`、`views/video_detail.py`、`components/shorts_line.py`、`components/status_bar.py` 等の刷新対象）と対応する既存 `tests/test_ui_*.py`。掃除対象として `src/yt_live_kit/ui/views/video_detail.py`、`src/yt_live_kit/ui/components/shorts_queue.py`、`src/yt_live_kit/ui/pages/` の `__pycache__`。文書は `docs/execution-plan-v3.md` の進捗チェックと、U9-4 の残差実測・U9-5 の案 B 判断を記録する `docs/ui-visual-refresh-plan-2026-08-05.md` を許可する。`services/`、`docs/requirements-v3.md`、`docs/ui-refactor-review-2026-08-04.md`、production `data/` は read-only とする。

**追加の不変条件:** R2 監査文書 §5「変更してはいけない安全境界」を全項目継承する。`st.tabs` への単純な置換を行わず、3 ワークスペースの conditional rendering（`st.segmented_control`）を維持する。worker thread / target から `st.*` を呼ばない。確認 dialog より前に upload、概要欄更新、削除、再生成を起動しない。service transaction を view ファイルへコピーせず、既存 controller / adapter の呼び出し点を保ったまま presentation だけを交換する。

**作業:**

- [ ] U9-1. `.streamlit/config.toml` に `[theme]` セクションを追加し、23 オプションのうちリファレンス画像に合わせる配色・文字・形状・チャート項目を設定する。Python コードは変更しない
- [ ] U9-2. `[theme] base = "dark"` でダークを既定に固定し、`[theme.sidebar]` でサイドバーを本体と別配色にする。`[theme.light]` / `[theme.dark]` は `[theme]` を拡張する mode 別上書きであり定義すると mode 追従が前提になるため、ダーク前提のリファレンスに合わせる本フェーズでは定義しない
- [ ] U9-3. 新規 `tests/test_ui_visual_smoke.py` を追加し、`streamlit.testing` / `AppTest` でライブラリ・詳細・設定ページを起動し、例外なく描画できることを検証する。第 2 弾着手前の安全網として先に導入する
- [ ] U9-4. A+ 適用後の実装とリファレンス画像の残差を実測し、差分一覧を更新する。丸数字ステッパー + 接続線 + 鍵アイコン、赤い波下線、KPI カード内の左アイコン配置など「形」の差分を確認する
- [ ] U9-5. U9-4 の残差実測を踏まえ、限定 CSS 注入（案 B）の要否を判断する。ステッパーを `st.badge` のまま妥協する選択肢を含めて判断を記録する
- [ ] U9-6. サイドバーに「作成中のショート」カード・進捗バー・日次カウンタを追加する。`shorts_line.py:947-966` の `st.write` / `st.caption` 実装を置き換える
- [ ] U9-7. ヘッダと KPI カード 3 枚を刷新する
- [ ] U9-8. ワークスペース切替の見た目を下線タブ風に整える。`video_detail.py:1188` の `st.segmented_control` による conditional rendering は維持し、`st.tabs` へ置換しない
- [ ] U9-9. 6 工程ステッパーを刷新する。`shorts_line.py:933-944` の `st.badge` 横並びを置き換える。U9-5 で案 B が必要と判断された場合のみ限定 CSS を用いる
- [ ] U9-10. `video_detail.py:653` の呼び出し箇所ゼロの `_render_clips` を削除する
- [ ] U9-11. `shorts_queue.py:502` の候補選択チェックボックス key を配列 index 依存から `source + .id` へ改め、R2-4 の契約に合わせる
- [ ] U9-12. `src/yt_live_kit/ui/pages/` に残存する `__pycache__` を削除する。`.gitignore` に `__pycache__/` があり `git ls-files src/yt_live_kit/ui/pages/` は空、すなわち git 管理外であるため、この削除はコミット差分の発生しないローカル整理として実施し、コミット境界には含めない

**Done 条件:**

- [ ] `.streamlit/config.toml` の `[theme]` 系設定のみで A+ が適用され、Python コード差分がゼロである
- [ ] 新規 `tests/test_ui_visual_smoke.py` が AppTest で主要ページの無例外描画を検証し、全件テストに組み込まれている
- [ ] A+ 適用後の残差実測が記録され、案 B の要否判断（採否いずれでも可）が記録されている
- [ ] サイドバーの「作成中のショート」カード・進捗バー・日次カウンタ、ヘッダ、KPI カード、ワークスペース切替、6 工程ステッパーが刷新され、`st.segmented_control` の conditional rendering が維持されている
- [ ] `_render_clips` デッドコードと `shorts_queue.py` の index 依存 key が解消されている。`ui/pages/` の `__pycache__` は git 管理外のローカル整理であり、コミット対象の Done 条件には含めない
- [ ] R2 §5 の安全境界（transaction 順序、確認 dialog、worker thread からの `st.*` 禁止、`st.tabs` への単純置換禁止）に変更が無い
- [ ] 全件 `uv run pytest` が変更前の基準から回帰せずに通る

**コミット境界:** テーマ適用（第 1 弾、`.streamlit/config.toml` のみ）を最初のコミットとする。AppTest 視覚回帰スモークの追加を次のコミットとする。shell 刷新（第 2 弾）を独立コミットとする。デッドコード（`_render_clips` の削除）と `shorts_queue.py` の index 依存 key 修正の掃除は最後のコミットに分ける。`ui/pages/` の `__pycache__` は git 管理外でコミット差分が発生しないため、この掃除コミットの対象に含めない。テロップ編集器の刷新（第 3 弾）は `T1-4` に合流するため、本フェーズのコミットには含めない。

---

## 7. 出力ディレクトリ・ソースコード構成（v3 追加分）

### 7.1 ソースコード構成

```
src/yt_live_kit/ui/
  app.py                    # st.navigation によるページ登録 + U6 の左パネル工程コンテキスト
  state.py                  # session_state ヘルパー（v2 から継続）
  session_keys.py           # R2: revision-aware widget key と限定 prefix
  queries.py                # R2: UI から使う読取入口
  controllers/              # R2: view が安全 transaction を部分接続できない adapter
  view_models/              # R2: durable state から作る純粋な表示 projection
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
  transcript.py              ← S9。TranscriptArtifact と絶対時刻 cue の型

src/yt_live_kit/services/
  youtube_api.py             # v2 から継続。P1 で mine channel / resumable upload / poll を追加
  upload_queue.py            ← v3（P1。operation / attempt / job target / reconciliation）
  schedule.py                ← v3（P2。IANA policy / slot queue / confirm transaction）
  short_cut.py               ← v3（S6。親区間内カットプランの生成 / 検証 / 保存）
  shorts_line.py             ← v3（U6 限定例外。永続ライン状態 / fingerprint / 遷移判定）
  transcript_artifact.py     ← S9。VTT / Whisper artifact の resolver、cue digest、永続 cache
  whisper_runtime.py         ← S9。whisper-cli capability、モデル設定、音声 span、subprocess 実行
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
    ├── transcripts/                 ← S9。既存 subtitles/ja.vtt とは別の字幕 artifact
    │   ├── artifacts/
    │   │   └── {artifact_fingerprint}.json  # source / model / settings / absolute cues / digest
    │   ├── index.json                # resolver が参照する有効 cache index。atomic 更新
    │   ├── audio/
    │   │   └── {audio_fingerprint}.m4a       # yt-dlp で取得した音声のみの永続 cache
    │   └── spans/                    # Whisper 実行用一時音声。完了後は既定で削除
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
             ├─ R1 全体リファクタリング・性能・長期運用監査
             │   ├─ H1 長期運用 hardening
             │   │   └─ P5 投稿枠の複数化 + 既定値設定  → U6-9 と実機ライン 3 周で M15 達成
             │   └─ G1 FFmpeg single-pass benchmark（production 変更なし）
             └─ U8 構造化エラー通知（P5 と独立・並行可）

S9-PLAN（docs-only 完了）
 └─ S9-0 既存 VTT 互換・非上書き保存契約
     └─ S9-1 代表素材 benchmark・モデル決定
         └─ S9-2 TranscriptArtifact / resolver / fingerprint / persistent cache
             └─ S9-3 whisper.cpp runtime・capability・モデル設定・音声区間準備
                 └─ S9-4 親候補区間 Whisper 精査 → short_cut / telop / line 再利用
                     └─ S9-5 UI 設定・進捗・エラー・失効表示
                         └─ T1-PLAN テロップ行時刻同期計画（docs-only）
                             └─ T1-1 production 非変更 timing spike
                                 └─ T1-2 timing 保存契約・extractor
                                     └─ T1-3 pure aligner・telop・fingerprint 統合
                                         └─ T1-4 Streamlit UI 時刻確認 gate
                                             └─ T1-5 同期受け入れ
                                                 └─ S9-6 A/B 受け入れ・回帰・フェーズ判定

P6-PLAN（docs-only。S9-1 の人手 gold 監査と並行可）
 ├─ P6-1 タイトル 3 方向生成・検証 ── ★main 統合を S9-4 より先に完了
 ├─ P6-2 概要欄必須構成・投稿前再検証 service
 └─ P6-3 関連動画の Studio 手動確認・永続状態
      └─ P6-4 投稿 UI 統合・確認ダイアログ（P6-1〜P6-3 全統合後）
          └─ P6-5 統合受け入れ・回帰

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
| R1 を P5 の途中で挟む | U6 の大規模 UI と P5 の service 拡張が同時に進行中のため、次の機能追加前に回帰基準と長期運用の安全境界を固定する。安全に証明できる局所修正だけを行い、構造変更は別タスクへ分離する |
| H1 を実機ライン確認より先にする | process 間の二重 job、途中 queue の誤予約、公開後 poll 未接続は反復運用で顕在化する。実機 3 周を新しい運用基準にする前に fail-closed 境界を直す |
| G1 を production 実装と分ける | FFmpeg pass 統合は最大の速度改善余地だが、frame 境界・音声同期・字幕時刻を変え得る。試作と比較証跡だけを先に作り、採用時は G2 として要件改訂する |
| U7 を保留にする | 運用目標の優先度③（チャプターは保守のみ）。候補引き継ぎ部分は U6 の工程接続に統合済みで、独立タスクの意味が消えた |
| S9-0 を先頭にする | 現行 ytdlp の再取得経路が canonical `ja.vtt` を触る可能性を先に閉じ、benchmark・cache・resolver が既存字幕を破壊しない前提を作る |
| S9-1 を S9-0 の後にする | 実モデルと設定を代表素材で決める前に、既存 VTT の保存境界を安全化する。S9-1 は production 非変更の benchmark として先に閉じる |
| S9-2 を S9-3 より先にする | subprocess の出力形式や cache の正本を先に決め、runtime が独自 JSON や独自 fingerprint を作らないようにする |
| S9-4 を S9-5 より先にする | UI は `TranscriptArtifact`、resolver、失効理由を表示するだけにし、short_cut / telop への再利用契約を UI 実装と混ぜない |
| T1-1 を T1-2 より先にする | production 非変更 spike で固定 manifest、gold、coverage 分母、時刻 gate、低信頼 fallback を測定してから保存方式を選ぶ。結果後の基準緩和を防ぐ |
| T1-2 を T1-3 より先にする | pure aligner が読む timing payload、parent lineage、policy、fingerprint、legacy fallback を先に immutable に固定する |
| T1-3 を T1-4 より先にする | UI は同期 status / flag / confirmation を表示するだけにし、alignment・validator・lineage を Streamlit に複製しない |
| T1-4 を T1-5 より先にする | UI の独立 timing gate、再起動、失効、通常 rerun の副作用無しを同期受け入れでまとめて検証できる状態にする |
| S9-6 を最後にする | T1-5 までの timing 契約を先に証明したうえで、benchmark の精度・時間、使用範囲だけの失効、既存 VTT 経路、人確認、1 ジョブ制約を同じ最終受け入れ証跡で一度だけ判定する。No-Go は S9 完了にしない |
| P6-1〜P6-3 を分離して並行する | telop、description、upload operation の変更範囲を物理的に分け、共通 UI と schedule の接続を P6-4 の単一 writer に集約する |
| P6-1 を S9-4 より先に統合する | 両タスクが `services/telop.py` と telop tests を変更するため、P6 のタイトル契約を main の前提にしてから S9 artifact 伝播を実装する |
| P6-4 を 3 service 統合後にする | 最終説明文の二重再検証と関連動画状態表示は P6-1〜P6-3 の公開契約を消費する統合作業であり、並行中に UI へ暫定ロジックを複製しない |

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
| R1 全体リファクタリング・性能・長期運用監査 | 0.5〜1 日 |
| H1 長期運用 hardening | 4〜5.5 日 |
| G1 FFmpeg single-pass benchmark | 0.5〜1 日 |
| P5 投稿枠の複数化 + 既定値設定 | 1 日 |
| U8 構造化エラー通知 | 1 日 |
| U7 概要欄反映の最新性判定 | 保留（v4 候補） |
| S9-0 既存 VTT 互換・非上書き保存契約 | 0.5 日 |
| S9-1 代表素材 benchmark・モデル決定 | 0.5〜1 日 |
| S9-2 TranscriptArtifact / resolver / fingerprint / persistent cache | 1〜1.5 日 |
| S9-3 whisper.cpp runtime・capability・音声区間準備 | 1〜2 日 |
| S9-4 親候補区間 Whisper 精査・short_cut / telop / queue / line 再利用 | 1.5〜2 日 |
| S9-5 UI 設定・進捗・エラー・失効表示 | 1〜1.5 日 |
| T1-PLAN テロップ行時刻同期計画（docs-only） | 0.5 日 |
| T1-1 production 非変更 timing spike・評価 manifest | 1〜1.5 日 |
| T1-2 timing 保存契約・extractor | 1〜1.5 日 |
| T1-3 pure aligner・telop・fingerprint 統合 | 1〜1.5 日 |
| T1-4 Streamlit UI 時刻確認 gate | 1〜1.5 日 |
| T1-5 同期受け入れ | 1 日 |
| S9-6 A/B 受け入れ・回帰・フェーズ判定 | 1 日 |
| P6-PLAN 要件・AC・writer 境界 | 0.5 日 |
| P6-1 タイトル 3 方向生成・検証 | 0.5〜1 日 |
| P6-2 概要欄必須構成・再検証 service | 0.5〜1 日 |
| P6-3 関連動画の永続状態 | 0.5〜1 日 |
| P6-4 投稿 UI 統合 | 1〜1.5 日 |
| P6-5 統合受け入れ | 0.5 日 |

**段階リリース案:**

| リリース | 含むタスク | 累計 | 内容 |
|----------|-------------|------|------|
| **0.2.1** | U0〜U5 | 6.5 日 | 迷わない UI に生まれ変わる |
| **0.2.2** | + S1〜S5 | 13.5 日 | テロップ付きショートが量産できる |
| **0.3.0** | + P1 → P2 → P0 → P3 | 20 日 | 安全契約・実機 probe・予約公開まで含む v3 完了 |
| **0.3.1** | + P4・S6・S7・S8 | — | ショート導線の追加要件・ホットフィックス・区間内容の可視化 |
| **0.4.0** | + S8 → U6 → R1 → H1 → P5（+ U8、G1） | — | 毎日 3 本のショート生産ラインが長期運用の安全境界を含めて確立する（v3.2） |
| **0.4.1** | + P6 | — | タイトル 3 方向、概要欄必須構成、関連動画の Studio 手動確認を投稿品質ゲートとして追加 |

---

## 10. マイルストーン定義

| マイルストーン | 判定 |
|----------------|------|
| **M11: サイドバーの事故導線が消える** | U0 完了。サイドバーに内部モジュール名が出ない |
| **M12: 動画軸で迷わず作業できる新 IA が揃う** | U5 完了。公開 3 画面 + 非表示詳細の 4 画面構成が完成し、設定のストレージ管理、ステッパー、確認ダイアログ、差分プレビューが動く |
| **M13: テロップ付きショートが量産できる** | S5 完了。複数区間を連結したショートがテロップ・フックタイトル付きで複数本まとめて作れる |
| **M14: 予約投稿が実際に公開される（v3 完了）** | P3 完了。AC-18〜AC-28 充足、実際の予約公開を確認済み |
| **M15: 毎日 3 本のショート生産ラインが確立する（v3.2）** | S8（AC-34）→ U6（AC-31 / AC-35）→ P5（AC-36）完了。実機でライン 3 周（3 本を予約まで）を通し確認済み。安定性の仕上げである U8（AC-33）も完了 |
| **M17: 投稿導線の品質ゲートが確立する** | P6-1〜P6-5 と AC-38 / AC-39 完了。投稿前にタイトル 3 方向と概要欄の登録 CTA・元動画案内を保証し、アップロード後の関連動画 Studio 人確認を再起動後も追跡できる |

---

## 11. テスト計画（フェーズ横断）

| 種類 | 対象 | タイミング |
|------|------|------------|
| ユニット | U6 の状態サマリー・初期選択・6 工程遷移・review / output fingerprint・編集時失効・atomic / fail closed ライン状態・timezone 日次集計、`_local_settings` の永続化、テロップ台本バリデータ、P6 のタイトル 3 方向・概要欄必須構成・関連動画状態、累積タイムオフセット計算、`assign_next_slot`、metadata / audience / synthetic / consent、resumable retry、operation / attempt / confirm race、kind 別 status bar、反映記録 fingerprint（U7）、構造化エラー通知（U8）、S9 の artifact schema / cue digest / resolver / cache / runtime capability / range offset /失効 | 各タスク内 |
| 回帰 | v1 / v2 の既存テスト（`tests/` 全件）が通ること | 各タスク末 |
| 実機結合 | 公開アーカイブ 2 本（V7-2 を U5 / S5 で同じ 2 本を通して確認）、S9 の代表素材 2〜5 本で音声のみ・複数区間・cache 再利用・精査済み字幕から生成までを確認、実アップロード 1 本（P0 / P3） | U5 / S5 / S9-1 / S9-6 / P0 / P3 |
| 目視確認 | サイドバー導線（U0）、確認ダイアログ（U5）、確定リファレンスと左パネル 4 状態・6 工程・編集時失効（U6）、S9 の粗い VTT / 精査済み artifact provenance、進捗・fallback・範囲内外失効、テロップ可読性・つなぎ目（S5）、YouTube 上の実際の公開挙動（P3） | 各フェーズ受け入れ |
| UI 受け入れ | README 手順どおりの全機能操作（P3） | P3 |

**方針:**

- yt-dlp / ffmpeg / Codex CLI / `googleapiclient` の実行は**すべてモックする**（`subprocess.run` および `googleapiclient` のクライアントをパッチ）。P0 / P3 の実 API はユーザー承認後の手動受け入れだけで行い、自動テストへ含めない
- 動画を実際に生成・アップロードするテストは CI 必須にしない（手動・任意）
- **v1 / v2 の既存テストを 1 件も壊さないこと。** 壊れた場合は後方互換の設計ミスとして扱う
- S9 の実機 benchmark は production data を上書きせず、音声のみの一時 span と `docs/benchmarks/` の計測記録を使う。whisper-cli / yt-dlp / ffmpeg の実体は受け入れ時だけ使い、通常の unit / CI では subprocess をモックする
- S9 の A/B は精度だけでなく処理時間、cache hit、境界を人が確認できること、artifact provenance、使用範囲だけの失効を同じ fixture で記録する。S9-1 の numeric gate / operational reference gate が未達なら No-Go とし、今回の A / Done では exact dimension 未承認と人確認必須を引き継ぐ

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
| 複数の Streamlit プロセスや壊れた `current.json` で同時実行制約が fail-open になる | R1 では再現条件と影響を記録する。プロセス間 advisory lock、所有 PID、pointer の atomic 保存・検証、running job scan を一体の hardening タスクとして実装する |
| queue 生成中のクラッシュ後に部分成果物が予約対象になる | R1 で `status=done` の manifest だけを予約可能とする。running manifest の復旧・terminal 化は schema を含む別タスクで扱う |
| 不正な動画 ID や symlink で `data_dir` の外へ書き込む | R1 で再現と影響を記録し、全 service 共通の path confinement helper と移行テストを別 hardening タスクとして実装する |
| 生成高速化のために FFmpeg pass を安易に統合し、字幕・切り替え・frame accuracy が回帰する | 現行の区間 encode、concat、最終 pass は維持する。代表素材で出力品質と wall time を比較する benchmark タスクを先に行い、要件改訂後にだけ変更する |
| Whisper モデルが日本語の固有名詞や時間精度を改善しない | S9-1 を production 非変更で先行し、CER・固有名詞・cue 品質・wall time を VTT と A/B 比較する。Go 根拠が無ければ S9 は fallback-only で止める |
| `ja.vtt` と Whisper artifact の出所が混ざり、古い字幕を再利用する | `TranscriptArtifact` の source / model / settings / input / range / cue digest / artifact fingerprint を必須化し、resolver の用途別選択と S9-4 / S9-5 の fail closed を通す。`ja.vtt` は上書きしない |
| Whisper timestamp を境界の唯一の正本にして話途中のカットや字幕ずれを作る | padding、必要な VAD、既存 cue、動画プレビュー、整数ミリ秒正規化、人確認を残し、S9-4 の生成直前に再検証する |
| cue digest を全体で伝播して無関係な字幕変更まで全成果物を失効させる | 親候補全体の粗い探索 digest と、cutplan / telop / review の使用範囲 digest を分ける。範囲内変更は fail closed、範囲外変更は downstream を維持するテストを必須にする |
| whisper-cli の build / JSON schema / model が環境ごとに違い、cache が壊れる | S9-1 で実体と model fingerprint を記録し、S9-3 の capability preflight と未知 schema 拒否を通す。モデル自動取得・自由な shell command は許可しない |
| 音声準備が動画全体取得や複数ジョブ実行に膨張する | S9-3 で yt-dlp の音声のみ、選択 range の span manifest、1 ジョブ内の入力順 serial 処理、永続 audio cache を固定し、全編 Whisper と local video を別フェーズへ残す |
| 高精度化の失敗が既存 S6 / U6 の導線を止めるか、逆に高精度と偽って進める | runtime 不備は日本語の明示 coarse fallback または停止として扱い、旧 VTT・cutplan・mp4 を削除しない。高精度 artifact と人確認の証明が無い状態は fail closed にする |
| token timing が精密に見えても whitespace / metadata / 日本語 subword / cross-cue の曖昧さで行を誤移動する | T1-1 で固定 manifest と人音声 onset gold を先に作り、一意高信頼行だけを対象にする。低信頼は元時刻 + flag、誤った line / cross-cue 移動 0、clamp / 時系列 / 非重複 / 500 ms を満たせない場合は fallback にする |
| coverage や閾値を結果に合わせて都合よく緩和する | manifest、coverage 分母、Go gate を測定前に fingerprint 付きで固定し、結果後の変更を禁止する。変更は別承認と理由記録を要求する |
| timing payload の保存方式が parent artifact と混ざり、legacy や cache restart で古い時刻を再利用する | T1-2 の ADR で Artifact v2 / immutable sidecar を比較し、strict provenance、schema / policy version、自身 fingerprint、atomic 保存・再検証、既存 artifact の timing 無し fallback、backfill 無しを固定する |
| timing confirmation を全文確認と混同し、低信頼行への無意味な編集や未確認状態の自動通過が起きる | T1-4 で独立 gate と状態表示を持ち、low-confidence がある場合だけ明示確認を必須にする。本文・時刻・alignment input・policy 変更、A → B → A、再起動では失効し、全文確認と最終 preview は維持する |
| UI 刷新が alignment / fingerprint / line state の安全境界を複製し、通常 rerun で外部処理を起こす | R2 の刷新順を守り、T1-3 の pure service と T1-4 の view model / session-state 境界を分離する。通常 rerun は Codex / Whisper / ffmpeg / upload を呼ばず、破壊操作は既存 dialog を通す |

---

## 13. 本計画のスコープ外（実装しない）

[`docs/requirements-v3.md`](./requirements-v3.md) §2 の改訂を反映した上で、以下は引き続き実装しない。

S9 初版で実装するのは、既存 YouTube `video_id` の良好な VTT を親候補探索に使い、人が選択した親候補区間だけを whisper.cpp 1.9.1 で精査する経路である。精査結果は `TranscriptArtifact` として保存し、FR-30 / FR-22 / FR-25 / FR-33 で再利用する。下記の全編・別入力・自動置換はこの経路に含めない。

- **BGM・効果音の付与**
- **ズーム・トランジション・エフェクト**
- 字幕なし・低品質字幕を起点にした全編 Whisper 再文字起こし
- 全 47 本など既存資産の一括 Whisper backfill、候補探索を Whisper へ置き換える自動運用
- `asset_id` を新しい path key にする移行、`source_kind=local_video` のローカル動画入力、ローカル動画からの音声抽出
- YouTube VTT の上書き・改名・自動置換、Whisper timestamp だけによる境界自動確定
- YouTube 概要欄・動画への **即時** 公開投稿（`publishAt` による予約投稿のみ）
- Cookie 認証による限定公開・メンバー限定動画対応
- Web UI の外部公開
- ジョブのキューイング（同時実行は 1 件まで。キュー量産も内部的には単一ジョブの順次処理）
- 複数チャンネルの同時運用（チャンネル既定値は 1 件のみ）

**次イテレーション（v4）候補:**

- 複数チャンネルの管理・切り替え
- 投稿後のパフォーマンス（再生数等）のダッシュボード表示
- YouTube Data API クォータの増枠申請後の量産上限緩和
- 字幕なし・低品質字幕の全編 Whisper と、その品質判定・再処理ポリシー
- `asset_id` / `source_kind` 抽象化とローカル動画入力。既存 `video_id` path との移行仕様を別計画で定義する

---

## 14. 直近の次アクション

1. ~~**P1 に着手する。**~~ 完了。安全な upload / operation / attempt / jobs / status bar 契約を実装済み
2. ~~**P2 を実装する。**~~ 完了。schedule / confirm transaction / UI と競合・再起動契約を実装済み
3. ~~**P0 の実 upload probe を行う。**~~ 個別承認のもと完了
4. ~~**P3 の予約公開受け入れを行う。**~~ 個別承認のもと完了

**P1〜P3 完了後（0.3.0 リリース済み）の次アクション:**

1. ~~**P4 に着手する。**~~ 完了。~~**S7 を閉じる。**~~ 完了（`9ebf251`）
2. ~~**S8 に着手する（最優先）。**~~ 完了。実機確認で S6-9 を吸収し、S6 / S8 をクローズ済み
3. ~~**U6 のコード実装と実機確認を進める。**~~ U6-9 まで完了し、実機ライン 3 周でクローズ済み
4. ~~**R1 を先に完了する。**~~ 完了（`6632793` / `be83adb` / `a969681`）。回帰基準、局所的な fail-closed 修正、rerun 高速化、H1 / G1 の実行計画を確定済み
5. ~~**H1-1 → H1-5 → H1-2 → H1-3 → H1-4 の順で hardening する。**~~ 完了。通常予約 operation の publication poll を再接続し、FR-27 / AC-27 / AC-28 を再度完了へ戻した
6. ~~**U6-9 + P5-4** を同じ実機ライン 3 周で確認する。~~ 完了。M15 を達成し、U6 / P5 をクローズ済み
7. ~~**G1** を production 非変更で実行する。~~ 完了。single-pass は速度 gate 未達のため不採用とし、production は変更していない
8. ~~**U8**（構造化エラー通知、AC-33）に着手する。~~ 完了。**U7 は保留（v4 候補）**
9. ~~**S9-PLAN** を完了する。~~ 完了。`requirements-v3.md` の FR-35 / FR-36 と AC-30 / AC-35 / AC-37、実行可能な S9-0〜S9-6、`v3-agent-prompts.md` の実行テンプレートを確定した
10. ~~**S9-0**（既存 VTT 互換・非上書き保存契約）を完了する。~~ 完了。再取得時の `ja.vtt` bytes 保持、source VTT の immutable 保存、失敗時非変更を閉じた
11. ~~**S9-1**（代表素材 benchmark・モデル決定）を完了する。~~ 完了。production 非変更で VTT と whisper.cpp 1.9.1 の精度・固有名詞・時間・cache 根拠を記録した
12. ~~**S9-2 → S9-3 → S9-4 → S9-5** の順に完了する。~~ 完了。S9-6 は受け入れ専用として開いたまま、**次は T1-PLAN → T1-1 → T1-2 → T1-3 → T1-4 → T1-5** を進める。T1-5 が PASS した後に S9-6 の A/B 受け入れ・回帰・フェーズ判定を一度だけ行い、受け入れが PASS するまで S9 と M16 は未完了を維持する
13. ~~**P6-PLAN を S9-1 の人手 gold 監査と並行して docs-only で閉じる。**~~ 完了。独立レビュー済み plan commit から分離 worktree を作成した
14. ~~**P6-1 / P6-2 / P6-3 を分離 worktree で並行実装し、個別レビュー後に main へ統合する。**~~ 完了。P6-1 を S9-4 より先に統合・依存元へ報告し、P6-4 → P6-5 まで独立レビュー後に閉じた
15. ~~**R2 を完了する。**~~ 完了。手動 E2E 済みの現行挙動を基準に、UI 大幅刷新前の page / session / durable state / upload gate を整理し、監査文書・characterization test・独立レビューで固定した
16. ~~**T1-PLAN を docs-only で完了する。**~~ 完了。FR-39 / AC-40、T1-1〜T1-5 の独立境界、S9-6 最終受け入れ順、R2 安全境界を 4 docs に反映した。**次は T1-1 の production 非変更 spike と評価 manifest 固定。**
17. **U9（UI 視覚刷新）を第 1 弾から進める。** R2 で境界整理が完了し、残るのは視覚レイヤーのみ。`.streamlit/config.toml` のネイティブテーマ適用（案 A+）を最初のコミットとし、AppTest 視覚回帰スモークを挟んでから shell 刷新へ進む。限定 CSS 注入（案 B）の要否は A+ 適用後の残差実測で判断する。テロップ編集器の刷新は T1-4 へ合流させ、U9 には含めない。

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-05 | **U9（UI 視覚刷新）を計画。** R2 完了により残るのは視覚レイヤーのみと確認し、実行計画に未定義だったフェーズを追加した。Streamlit 1.60 のネイティブテーマ 23 オプションを使い切る案 A+ を第 1 弾、限定 CSS 注入の案 B を A+ 適用後の残差実測まで保留、カスタムコンポーネントの案 C は不採用と決めた。AppTest による視覚回帰スモークを第 2 弾の前提として追加し、テロップ編集器の刷新は T1-4 へ合流させて U9 に含めない。R2 §5 の安全境界を全項目継承し、`st.tabs` への単純置換禁止を不変条件として明記した。調査根拠は `docs/ui-visual-refresh-plan-2026-08-05.md` に分離した。 |
| 2026-08-04 | **T1-PLAN 親レビュー指摘を反映。** T1-1 に固定選択 span の隔離 bounded whisper-cli benchmark を許可し、pooled / 長い単一 cue / multi-cross-cue の群別 gate、VTT fallback + 連結の非回帰、再現 fingerprint を追加した。T1-5 を同期 component acceptance、S9-6 を formal phase acceptance と明記し、隔離 preview、production 不変、T1-5 evidence の条件付き再利用、AC-40 を S9-6 formal PASS 時だけ完了する規約、T1-1〜T1-5 の進捗行、標準 ADR path を反映した。 |
| 2026-08-04 | **T1-PLAN を docs-only で完了。** S9-6 を受け入れ専用の未完了状態で保持し、S9-5 → T1-PLAN → T1-1 → T1-2 → T1-3 → T1-4 → T1-5 → S9-6 の依存を追加した。固定 manifest と人音声 gold による production 非変更 spike、timing payload 保存 ADR、pure aligner、独立 timing confirmation、R2 の UI 安全境界、AC-40 の同期受け入れを計画し、次の未着手を T1-1 とした。 |
| 2026-08-04 | **R2 完了。** 旧 global result、描画時 session 復元、再生成時 widget state、候補 lineage、ライン開始の部分保存、投稿 tracking と reservation gate の不整合を pure view model、query、session key、必須 adapter、rollback-safe command へ分離した。変更前 `1645 passed, 2 skipped` から変更後 `1692 passed, 2 skipped`、lock / sync / diff / compile、隔離ブラウザ確認を通過し、独立最終レビューは残存 P0 / P1 / P2 なしで PASS。見た目は変更せず、巨大 renderer、canonical gate の再計算、legacy read migration、queue snapshot、library paging を視覚刷新側の P3 として監査文書に残した。実 upload、外部 write、追加 Codex / Whisper、動画生成、成果物削除は行っていない。 |
| 2026-08-04 | **R2 を開始。** 手動 E2E でショート生成から予約投稿まで完走した現行挙動を基準に、UI 大幅刷新前の page 境界、サイドバー純粋性、再生成時 widget state、候補 lineage、投稿 tracking / reservation gate、ライン開始時の session projection を監査・整理する計画を追加。外部 API、実 upload、実 Codex、動画再生成、成果物削除は行わず、S9-6 の受け入れ判定を変更しない。あわせて進捗サマリーと不整合だった P6 本文のフェーズ状態を完了へ整合した。 |
| 2026-08-03 | **P6 完了。** タイトル固定 3 方向、ショート概要欄の生成説明・元動画タイトル・開始秒付き URL・チャンネル登録 CTA の不変要件 gate、YouTube Studio 関連動画確認の永続追跡を統合した。明示編集本文の黙った差し戻しを独立レビューで検出・修正後、P6 境界 410 件、全体 `1380 passed, 2 skipped`、diff-check、実 YouTube / Studio write なしを確認。AC-38 / AC-39 と M17 を完了した。 |
| 2026-08-03 | **P6-PLAN を開始。** タイトルを検索明快型・仕事影響型・好奇心型の固定 3 方向で生成し、概要欄の生成説明・チャンネル登録 CTA・元動画タイトル・開始秒付き URL を投稿前に二重再検証し、関連動画は API 自動設定せず YouTube Studio の手動確認状態を upload operation に永続化する FR-37 / FR-38 と AC-38 / AC-39 を追加。P6-1〜P6-3 の分離 worktree、P6-4 の単一 UI writer、P6-1 を S9-4 より先に main 統合する依存、S9-1 監査節と学習ログの保護を固定した |
| 2026-08-03 | **S9-PLAN を確定。** S9 を S9-0 既存 VTT 非上書き契約 → S9-1 benchmark → S9-2 TranscriptArtifact / resolver / fingerprint / persistent cache → S9-3 whisper.cpp runtime / 音声区間 → S9-4 short_cut / telop / queue / line 再利用 → S9-5 UI / 進捗 / 失効 → S9-6 A/B 受け入れの依存順へ分割。各タスクの目的・前提・変更範囲・テスト・Done・コミット境界、候補 lineage、cache identity 分離、gold / glossary / 評価 gate、`ja.vtt` 非破壊、使用範囲 cue digest の fail closed、全編 Whisper / local video / asset ID の将来分離を固定した |
| 2026-08-03 | **U8 完了。** job error を動画 ID / job ID / 処理種別 / 1 行要約 / 技術詳細 / 発生日時へ構造化し、動画別直近 3 件と上限付き global 要約を session state で分離した。ページ先頭は要約と対象動画導線だけにし、技術ログは現在動画の「詳細・再生成」内の bounded なスクロール領域へ集約。親レビューで初回描画時 consume によりリンクが消える P0 を検出・修正後、U8 対象 159 件、全体 `1266 passed, 2 skipped`、diff-check、43 KiB の疑似 ffmpeg log を使う実ブラウザ確認を通過。独立最終レビューは Finding なし（P0 / P1 なし）。残余 P2 は孤児復元ログの state 上限統一、敵対的 symlink 交換時の TOCTOU、batch 部分失敗を構造化通知へ含める場合の仕様拡張。 |
| 2026-08-03 | **U6 / P5 クローズ、M15 達成。** 実ブラウザで確定リファレンス、左パネル 4 状態、折り畳み、3 ワークスペース、確認失効を確認し、異なる 3 ラインが予約済みまで到達する工程証跡を照合した。投稿枠を 09:00 / 13:00 / 18:00（Asia/Tokyo）に設定し、既存予約を避けた 3 本が空き枠順に割り当たること、追加 2 本の upload / processing / live private / publishAt / Made for Kids = false を確認した |
| 2026-08-02 | **H1 完了。** jobs の process lock / owner lease、path confinement、queue crash recovery、OAuth token / local settings の atomic persistence、通常予約 operation の publication poll を統合した。初回独立レビューの P1 4 件を追加修正し、修正後再レビューは P0/P1/P2 なし・APPROVE。統合 main は `1205 passed, 2 skipped`、diff-check 通過。実データ・実 YouTube・外部 API は変更していない。残余リスクは symlink 検証後の敵対的 TOCTOU、atomic replace 後の親 directory fsync、process crash 時の stale lease file であり、stale file の存在だけでは live 判定しない。 |
| 2026-08-02 | **G1 完了。** `f8c2034` で production 非変更の FFmpeg benchmark harness と測定記録を追加。独立再レビューは APPROVE、速度 gate は 15 秒 21.98％ / 60 秒 22.68％ / 180 秒 23.08％で全ケース不採用。境界差は最大 1 frame、audio expected duration 誤差は 0 秒、代表フレームで字幕 / Hook / layout / 接続を確認した。 |
| 2026-08-02 | **H1-1 完了。** `jobs.py` に data root advisory lock、owner PID / token、UUID temp + flush / fsync / atomic replace、current pointer の状態区別、strict running scan、live worker 保護を実装。実 2 process 競合と fault injection を含む jobs テスト 42 件、全体 `1083 passed, 2 skipped` を確認した。H1-2〜H1-5 は未着手のまま維持する |
| 2026-08-02 | **R1 完了。** 途中 queue の予約 fail-closed、旧 datetime の UTC 正規化、legacy `build_short()` の atomic 出力保護を `be83adb`、binary identity 付き `yt-dlp --version` warning cache・Streamlit 1.55 下限・uv 設定・seek 文書整合を `a969681` で実装。変更前 1063 から変更後 1074 passed / 2 skipped、cache miss 240.56 ms から hit 平均 0.215 ms、`uv lock --check` / `uv sync --locked` 成功、safety / performance の独立レビュー APPROVE を確認した |
| 2026-08-02 | **R1 計画の独立レビューを反映。** P3 の単発公開証跡と通常 operation の publication poll 未接続を区別し、後者を FR-27 / AC-28 の未完了へ戻した。構造課題を H1-1〜H1-6、生成速度検証を G1-1〜G1-3 として変更範囲・Done・依存・見積もり付きで正式化。Streamlit 最低版は stateful expander 導入版 1.55、FFmpeg seek は現行 input seek を正本とし G1 で境界比較する方針へ明確化した |
| 2026-08-02 | **R1 全体リファクタリング・性能・長期運用監査を開始。** 1063 tests の回帰基準と実データの所要時間を採取し、即時の fail-closed 修正・安全な rerun 高速化・別タスクへ分ける構造課題を定義した。U6 / P5 の実機完了前に R1 を挟み、FFmpeg 生成方式の変更は benchmark と要件改訂を先行させる |
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
