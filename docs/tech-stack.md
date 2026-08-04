# yt-live-kit 技術スタック定義書

**バージョン:** v1（MVP）  
**最終更新:** 2026-08-04
**関連:** [要件定義書](./requirements.md)

---

## 1. 概要

yt-live-kit は Python 製の **ローカル Web UI ツール** である。Streamlit でブラウザ上から操作し、YouTube 公開アーカイブから字幕・動画を取得し、整形テキストを既存 AI サブスクリプション（Codex CLI 等）に渡してチャプター案を生成し、ffmpeg で切り抜きを行う。従量課金 API は使用せず、ファイルベースで成果物を保存する。CLI は内部実装・上級者向け補助として提供し、Web UI と同一の services 層を呼び出す。

---

## 2. 言語・実行環境

| 項目 | 選定 | 理由 |
|------|------|------|
| **言語** | Python 3.11+ | yt-dlp と同一エコシステム。字幕処理・Web UI・CLI 構築に適する |
| **Python 管理** | pyenv（既存環境あり） | プロジェクトごとにバージョン固定可能 |
| **OS** | macOS（主）、Linux も想定 | 開発環境が macOS。ffmpeg / yt-dlp はクロスプラットフォーム |
| **シェル** | zsh | 開発者の標準シェル |

---

## 3. 主要依存

### 3.1 外部バイナリ（必須）

| ツール | 用途 | バージョン要件 |
|--------|------|----------------|
| **yt-dlp** | 字幕・動画・メタデータ取得 | **最新版を推奨**（2026.07.04 で検証済み。2025.02.19 では字幕取得失敗） |
| **ffmpeg** | 動画の区間切り出し・連結・字幕焼き込み | 字幕付きショートは `subtitles` フィルタ対応版が必要 |

yt-dlp は Python パッケージとしてもインストール可能だが、実行時は CLI として呼び出す。ffmpeg は `YTLK_FFMPEG_PATH` で指定したバイナリを使い、未指定時は PATH 上の `ffmpeg` を解決する。macOS の字幕焼き込みには keg-only の `ffmpeg-full` を強制 link せず、例えば `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` のような実体パスを明示設定する。`libass` の単体導入や通常版 FFmpeg のアップグレードは、既存バイナリに後から `subtitles` フィルタを追加しない。字幕なし単一区間ショートと切り出しは通常版でも実行できる。

### 3.2 Python パッケージ（想定）

| パッケージ | 用途 |
|------------|------|
| `yt-dlp` | メタデータ・字幕・動画ダウンロード（サブプロセスまたは Python API） |
| `streamlit` | ローカル Web UI（主要インターフェース） |
| `click` または `typer` | 上級者向け CLI フレームワーク（サブコマンド定義） |
| `pydantic` | 設定・メタデータ・候補 JSON のスキーマ検証 |
| `jinja2` | プロンプトテンプレートのレンダリング（任意） |

v1 では Whisper 関連パッケージは **含めない**（v1.5 フォールバック用）。

### 3.3 依存管理

- **推奨:** `pyproject.toml` + [uv](https://github.com/astral-sh/uv) による依存管理
- **代替:** `requirements.txt` + pip / venv
- ロックファイル（`uv.lock` 等）で再現性を確保する

---

## 4. UI レイヤ

### 4.1 選定: Streamlit（推奨）

| 項目 | 内容 |
|------|------|
| **フレームワーク** | [Streamlit](https://streamlit.io/) 1.59+（`persist_state` 導入版。lock 解決版は 1.60） |
| **理由** | Python 製でパイプラインと同一言語。1 ファイルから始められる。進捗表示（`st.status` / `st.progress`）、コピー（`st.code` + ボタン）、ダウンロード（`st.download_button`）等の UI が標準部品で足りる |
| **実行形態** | ローカルホスト（`localhost`）のみ。外部公開しない |
| **代替案** | Gradio — 同様に Python 製で UI を素早く構築できるが、v1 では Streamlit を推奨 |

### 4.2 起動方法

非エンジニアが日常利用できるよう、起動手順を最小化する。

| 方式 | 用途 |
|------|------|
| **`.command` ファイル（macOS）** | ダブルクリックで Streamlit アプリを起動。非エンジニア向けの主手段 |
| **`uv run streamlit run ...`** | 1 コマンド起動。開発者・上級者向け |

README には非エンジニア向けの起動手順（`.command` のダブルクリック手順、ブラウザが自動で開くこと、終了方法）を記載する方針とする。

**起動例:**

```bash
uv run streamlit run src/yt_live_kit/ui/app.py
```

---

## 5. AI 連携方針

### 5.1 基本方針

- **従量課金 API は使用しない。**
- チャプター生成・切り抜き候補生成は、**プロンプトテンプレート + 整形テキスト** を Cursor / Codex（既存サブスク）に渡して実行する。
- v1 では AI 呼び出し方式を **差し替え可能** にするため、プロンプトテンプレートをコードから分離してファイル管理する。
- **Web UI からの自動実行** を成立させるため、v1 では方式 (b) を主とする（後述）。

### 5.2 連携方式（2 案）

| 方式 | 説明 | v1 での位置づけ |
|------|------|-----------------|
| **(a) Cursor エージェント直接運用** | 圧縮版テキストとプロンプトを Cursor チャット／エージェントに渡し、チャプター案を生成。成果物を手動で保存 | **補助運用。** 開発・検証フェーズ、または AI 自動実行が失敗したときのフォールバック |
| **(b) Codex CLI exec 等** | `codex exec` 等のサブスク内 CLI にプロンプトテンプレートとテキストを渡し、stdout からチャプター案を取得 | **v1 の主方式。** Web UI の「実行」ボタンからサブプロセスとして呼び出し、結果を画面に表示する |

**v1 設計:** どちらの方式でも動くよう、以下をファイルとして分離する。

```
prompts/
├── chapters.md          # チャプター生成用プロンプトテンプレート
└── clips_suggest.md     # 切り抜き候補生成用プロンプトテンプレート
```

services 層（`pipeline.py` 等）および上級者向け CLI の `chapters` / `clips suggest` は、最低限以下を行う。

1. 圧縮版テキストを読み込む
2. プロンプトテンプレートに埋め込んだ完成プロンプトを `data/{video_id}/` 配下に出力する（例: `prompt_chapters.txt`）
3. 方式 (b): Codex CLI をサブプロセスで呼び出し、結果を `chapters/chapters.md` に保存し、Web UI に返す

方式 (a) の場合、ユーザーが Cursor のチャットにプロンプトファイルを渡し、生成結果を `chapters/chapters.md` に保存する運用も許容する（UI からの自動実行が主）。

### 5.3 プロンプトに含める制約（テンプレート側で明示）

- YouTube 概要欄形式: `0:00 タイトル`
- 半角 `<>` 禁止、先頭 `0:00` 必須、3 件以上・各 10 秒以上
- 視聴者挨拶・雑談ノイズの除外
- 固有名詞の補正（Codex、Cursor 等）

---

## 6. 文字起こしフォールバック（v1.5）

| 項目 | 内容 |
|------|------|
| **ツール** | faster-whisper（ローカル実行、無料） |
| **トリガー** | 自動字幕が取得できない、または品質が著しく低い場合のみ |
| **v1 での扱い** | 実装しない。依存パッケージも v1 には含めない |
| **想定フロー** | yt-dlp で音声抽出 → faster-whisper で再文字起こし → FR-02 と同様の整形パイプラインへ |

---

## 7. データ保存

| 項目 | 選定 |
|------|------|
| **方式** | ファイルベース（JSON + Markdown / テキスト） |
| **DB** | 不使用 |
| **ルートディレクトリ** | `data/`（設定で変更可能） |
| **命名** | 動画 ID（YouTube `id` フィールド）をディレクトリ名とする |

バッチ処理の進捗・失敗 URL は `data/_batch/` 等にステータス JSON またはログファイルで記録する。

---

## 8. パイプライン層（内部 API / 上級者向け CLI）

Web UI と CLI は **同一の services 層** を呼び出す。CLI は上級者向けの補助手段であり、v1 の主要インターフェースではない。

### 8.1 CLI サブコマンド

サブコマンド形式。エントリポイント: `yt-live-kit`（または `python -m yt_live_kit.cli`）。

| サブコマンド | 概要 | 対応 FR |
|--------------|------|---------|
| `fetch` | URL からメタデータ・字幕 VTT を取得 | FR-01 |
| `transcript` | VTT を整形（全文版 + 圧縮版） | FR-02 |
| `chapters` | プロンプト生成、（任意）AI 呼び出し、チャプター案保存 | FR-03 |
| `clips suggest` | 切り抜き候補リスト生成 | FR-04 |
| `clips cut` | 指定区間を ffmpeg で切り出し | FR-05 |
| `run` | fetch → transcript → chapters → clips suggest を一括実行 | FR-07 |

### 8.2 コマンド例

```bash
# 単本: 一括実行
yt-live-kit run "https://www.youtube.com/watch?v=VIDEO_ID"

# 段階実行
yt-live-kit fetch "https://www.youtube.com/watch?v=VIDEO_ID"
yt-live-kit transcript VIDEO_ID
yt-live-kit chapters VIDEO_ID
yt-live-kit clips suggest VIDEO_ID

# 切り抜き（人が選んだ区間）
yt-live-kit clips cut VIDEO_ID --start 00:03:42 --end 00:16:30 --output clip_001.mp4

# バッチ
yt-live-kit run --urls-file urls.txt --skip-existing
```

### 8.3 共通オプション（想定）

| オプション | 説明 |
|------------|------|
| `--data-dir` | 成果物ルート（デフォルト: `./data`） |
| `--skip-existing` | 処理済み動画 ID をスキップ |
| `--sleep` | URL 間のスリープ秒数（レート制限対策） |
| `--yt-dlp-path` | yt-dlp バイナリパス（デフォルト: PATH 上の yt-dlp） |

---

## 9. ディレクトリ構成案

```
yt-live-kit/
├── pyproject.toml              # 依存定義（uv 推奨）
├── README.md                   # 非エンジニア向け起動手順を含む
├── start.command               # macOS ダブルクリック起動用
├── docs/
│   ├── requirements.md
│   └── tech-stack.md
├── prompts/
│   ├── chapters.md
│   └── clips_suggest.md
├── src/
│   └── yt_live_kit/
│       ├── __init__.py
│       ├── cli.py              # 上級者向け CLI エントリ（click / typer）
│       ├── ui/
│       │   └── app.py          # Streamlit アプリ（主要インターフェース）
│       ├── commands/
│       │   ├── fetch.py
│       │   ├── transcript.py
│       │   ├── chapters.py
│       │   ├── clips.py
│       │   └── run.py
│       ├── services/
│       │   ├── pipeline.py     # 一括実行オーケストレーション（UI/CLI 共通）
│       │   ├── ytdlp.py        # yt-dlp ラッパー
│       │   ├── vtt_parser.py   # VTT 重複除去・整形
│       │   ├── compressor.py   # 20 秒バケット圧縮
│       │   ├── ffmpeg.py       # 切り出しコマンド生成・実行
│       │   └── ai_prompt.py    # プロンプトテンプレート読み込み・AI呼び出し
│       ├── models/
│       │   ├── meta.py
│       │   └── clips.py
│       └── config.py
├── tests/
│   ├── test_vtt_parser.py
│   └── fixtures/
│       └── sample.vtt
└── data/                       # 成果物（.gitignore 対象）
    ├── _batch/
    │   └── status.json
    └── {video_id}/
        └── ...
```

**レイヤ構成:**

```
┌─────────────────────────────────────┐
│  Web UI (Streamlit)  │  CLI (typer) │  ← インターフェース層
├─────────────────────────────────────┤
│           services/ (pipeline 等)   │  ← 共通ビジネスロジック
├─────────────────────────────────────┤
│  yt-dlp  │  ffmpeg  │  Codex CLI    │  ← 外部ツール
└─────────────────────────────────────┘
```

---

## 10. 処理パイプライン

```
[Web UI: URL 貼り付け → 実行ボタン]
      │  （上級者向け CLI からも同一パイプラインを呼び出し可能）
      ▼
┌─────────────┐
│ fetch       │  yt-dlp → meta.json, ja.vtt
└──────┬──────┘
       ▼
┌─────────────┐
│ transcript  │  VTT 重複除去 → full.txt, compressed.txt
└──────┬──────┘
       ▼
┌─────────────┐
│ chapters    │  プロンプト生成 → Codex CLI → chapters.md
└──────┬──────┘
       ▼
┌─────────────┐
│ clips       │  候補生成 → candidates.json
│ suggest     │
└──────┬──────┘
       ▼
[Web UI: タイムライン表示・コピー / 候補一覧]
       ▼
  [人が候補を選択 → 切り出しボタン]
       ▼
┌─────────────┐
│ clips cut   │  yt-dlp（動画DL）→ ffmpeg 切り出し
└─────────────┘
```

---

## 11. 主要モジュール設計メモ

### 11.1 Web UI（`ui/app.py`）

- URL 入力・実行ボタン、進捗表示（`st.status`）、結果表示
- タイムラインの表示とクリップボードコピー
- 文字起こし全文の表示・ダウンロード（`st.download_button`）
- 切り抜き候補一覧、選択、切り出しボタン、保存先表示
- 処理済み動画一覧（`data/` ディレクトリをスキャン）
- 複数 URL 一括処理（テキストエリア）
- エラー時の日本語メッセージ表示

### 11.2 パイプライン（`pipeline.py`）

- FR-07 相当の一括実行をオーケストレーション
- Web UI と CLI の双方から呼び出される
- 各工程の進捗をコールバックまたは yield で UI に通知

### 11.3 VTT パーサー（`vtt_parser.py`）

- WebVTT のキュー（cue）をパース
- プログレッシブ表示による部分文字列の重複を除去（前の cue のテキストが次の cue に包含されるケース）
- 出力: `[HH:MM:SS] テキスト` 形式の行リスト

### 11.4 圧縮器（`compressor.py`）

- 20 秒単位の時間バケットに字幕を集約
- 同一バケット内のテキストを連結（必要に応じて重複フレーズ除去）
- 検証実績: 1h24m → 約 950 キュー → 約 41KB 圧縮テキスト

### 11.5 yt-dlp ラッパー（`ytdlp.py`）

- 字幕: `--write-auto-sub --sub-langs "ja-orig,ja" --sub-format vtt --skip-download`（`ja-orig` が話者オリジナルの自動字幕。検証済みの組み合わせ）
- 動画: 切り出し用に必要なフォーマットを 1 本ダウンロード
- 実行バージョンを `meta.json` に記録
- 失敗時は stderr をログに残す

### 11.6 ffmpeg ラッパー（`ffmpeg.py`）

- 切り出し: `-ss` / `-to` または `-t` を用いたストリームコピーまたは再エンコード
- 生成したコマンドを `clips/output/` 配下のログに保存
- 字幕 capability: 後段と同じ解決済みバイナリで `-hide_banner -filters` を短い専用タイムアウト付きで実行し、フィルタ名列の `subtitles` 完全一致で判定
- バージョン診断: capability 出力に依存せず、同じ解決済みバイナリの `-version` を別途実行
- 字幕必須処理は元動画取得や encode の前に fail fast し、字幕なしへの自動再試行は行わない

---

## 12. リスクと対策

| リスク | 影響 | 対策 |
|--------|------|------|
| **yt-dlp の仕様変更** | 字幕・動画取得失敗 | 定期的な yt-dlp 更新。バージョンをログ記録。README に更新手順を記載 |
| **古い yt-dlp の利用** | 字幕取得不可（2025.02.19 で失敗実績） | 起動時または fetch 時にバージョン警告。最低推奨バージョンをドキュメント化 |
| **JS ランタイム未導入** | yt-dlp が「No supported JavaScript runtime」警告を出し、一部フォーマットが欠落する可能性（2026.07.04 で確認。字幕取得自体は成功） | deno のインストールを推奨としてREADMEに記載。字幕のみの用途では必須ではない |
| **字幕なし動画** | パイプライン停止 | v1: エラー記録してスキップ。v1.5: faster-whisper フォールバック |
| **自動字幕の品質** | 固有名詞誤変換、挨拶ノイズ | AI プロンプトで補正・除外指示。圧縮版でノイズ低減 |
| **YouTube レート制限** | バッチ処理の一時ブロック | URL 間スリープ（`--sleep`）。指数バックオフ（将来） |
| **AI 出力の形式逸脱** | 概要欄に貼れないチャプター | プロンプトで形式を厳密指定。保存前にバリデーション（0:00 先頭、`<>` 禁止、件数・秒数） |
| **2 時間超の長尺配信** | AI コンテキスト超過 | 圧縮版テキストの利用。必要ならチャンク分割（v1.5） |
| **ffmpeg / コーデック差異** | 切り出し失敗 | エラーログ保存。再エンコードオプションの提供 |
| **`subtitles` 非対応 FFmpeg** | 字幕付きショートの最終工程で失敗 | 元動画取得前の capability 検査で停止し、`ffmpeg-full` と `YTLK_FFMPEG_PATH` による復旧を日本語で案内 |
| **限定公開・メンバー限定** | 取得不可 | v1 対象外として明示。公開 URL のみ受け付け |
| **Streamlit 長時間処理** | UI タイムアウト・進捗不明 | `st.status` で工程表示。長時間処理はバックグラウンド実行＋ポーリング（必要に応じて） |
| **Codex CLI 未インストール** | チャプター自動生成不可 | 起動時チェック。日本語でインストール手順を表示。方式 (a) へのフォールバック案内 |

---

## 13. 開発・運用

### 13.1 テスト方針

- VTT パーサー・圧縮器はユニットテスト（fixtures に検証済み VTT の抜粋）
- yt-dlp / ffmpeg は統合テストで短尺公開動画 1 本を使用（CI では optional / manual）

### 13.2 バージョン管理

- セマンティックバージョニング（v0.x = MVP 開発中）
- `meta.json` に yt-dlp バージョン、ツールバージョンを記録

### 13.3 .gitignore（推奨）

```
data/
__pycache__/
.venv/
*.pyc
.DS_Store
```

---

## 14. 技術選定サマリー

| レイヤ | 選定 |
|--------|------|
| 言語 | Python 3.11+ |
| **UI（主要）** | **Streamlit（ローカル Web UI）** |
| CLI（補助） | click / typer |
| 取得 | yt-dlp（最新版必須） |
| 動画処理 | ffmpeg 8.0.1 |
| AI | Codex CLI 主 / Cursor 補助（既存サブスク、API 課金なし） |
| 文字起こし FB | faster-whisper（v1.5、ローカル） |
| 保存 | ファイル（JSON + MD/TXT） |
| 依存管理 | pyproject.toml + uv（推奨） |
| 起動 | `.command`（macOS）/ `uv run streamlit run ...` |

---

## 15. v2 追加技術方針

v2 実装（ハイライト連結・縦型ショート・非同期 UI）に伴い、以下の技術方針を固定する。

### 15.1 ffmpeg 連結方針

| 項目 | 方針 | 根拠 |
|------|------|------|
| **区間連結** | 各区間を **必ず再エンコード** してから連結する | `-c copy` で切るとキーフレーム境界の問題で、繋ぎ目に映像フリーズ・音ズレが発生する |
| **`-c copy` 連結** | **採用しない** | 上記。中間ファイルは統一コーデック（例: libx264 + AAC）で揃えてから concat demuxer で連結する |
| **切り出し** | `-ss` は `-i` の**前**に置く（入力シーク）。再エンコードで 0 秒始まりの中間ファイルを作る | 長尺動画の後半で不要な decode を省き、現行実装・テストと契約を合わせる。境界 frame と速度の比較は G1 で行う |

### 15.2 Streamlit 非同期方針

| 項目 | 方針 | 根拠 |
|------|------|------|
| **長時間処理** | `threading.Thread(daemon=True)` でバックグラウンド実行 | Codex CLI 呼び出し・ffmpeg 再エンコードは数分かかる。同期実行では UI がブロックされる |
| **状態永続化** | `data/_jobs/{job_id}.json` に書き込む | ブラウザを閉じても進行状況を復元できる。書き込みは一時ファイル + `os.replace()` で原子的に行う |
| **ワーカースレッド** | **`st.*` を一切呼ばない** | ワーカースレッドには `ScriptRunContext` が無く、未定義動作・警告の原因になる |
| **UI 側ポーリング** | `@st.fragment(run_every="1s")` でジョブ状態を読み込み描画 | Streamlit **1.55+** で利用可能（lock 解決版は **1.60**） |
| **同時実行** | 1 件まで | yt-dlp レート制限対策。2 件目は「実行中です」と拒否 |

### 15.3 日本語字幕フォント

| 項目 | 方針 | 根拠 |
|------|------|------|
| **フォント指定** | ffmpeg `subtitles` フィルタの `force_style` に **`FontName` を明示指定** | 未指定だと macOS で日本語が豆腐（□）になる |
| **優先順** | Hiragino Sans → Noto Sans CJK JP → sans-serif | macOS 標準 → クロスプラットフォーム代替 → 最終フォールバック |
| **設定上書き** | 環境変数 `YTLK_SUBTITLE_FONT` で変更可能 | 環境ごとに利用可能フォントが異なるため |

**force_style 例:**

```
FontName=Hiragino Sans,FontSize=54,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=180
```

---

## 16. v3.2 T1 テロップ行時刻同期方針

T1 は既存 S9 の transcript / telop / FFmpeg 経路を置き換えるのではなく、Codex draft の行時刻に対する評価、timing payload の保存、純粋な alignment、独立した人確認を追加する。T1-PLAN では方式を選ばず、T1-1 の production 非変更 spike が provisional Go になった後、T1-2 の ADR で Artifact v2 と immutable timing sidecar の一方を選ぶ。

### 16.1 timing payload と provenance

- T1-2 以降と本番経路では、Whisper full JSON の token timing は既存の一回の処理結果から抽出し、UI rerun や alignment 実行のために追加 Whisper を呼ばない。T1-1 だけは、固定 manifest の選定済み span に対象・回数の上限を設け、isolated temp へ bounded に whisper-cli を再実行して benchmark input を得てもよい
- 現行 artifact が raw token timing を保持しない T1-1 の評価だけは、測定前 manifest に固定した有限整数 `max_selected_spans` / `max_whisper_invocations` の範囲内で選定済み span を隔離 temp へ bounded に whisper-cli 再実行してよい。再現 command、runtime / model / settings fingerprint、raw full JSON hash と実績を記録し、production data / artifact / cache / output / hash は変更しない。全編処理と 47 本 backfill は行わない
- 選択した保存方式は、parent artifact reference、正規化 token payload または raw full JSON hash、model / runtime / settings / ranges、schema / policy version、自身の fingerprint を strict schema で保持する
- 保存は一時ファイル + atomic replace と再検証を使う。parent、payload、range、policy、fingerprint の不一致、未知 field、破損、cache restart 後の証明不能状態は fail closed にする
- 既存 artifact、VTT、timing 無し legacy は backfill せず、明示的な timing 無し fallback として扱う。whitespace / metadata、zero / reverse end、日本語 subword は入力妥当性を検証し、解釈不能なら alignment 不可にする
- token end は補助的な timing evidence に留め、字幕区間・cut 境界・生成前 preflight の時刻正本にはしない

### 16.2 pure alignment と telop lineage

- aligner は副作用のない service とし、Codex draft と timing payload から一意かつ高信頼な行だけを monotonic に補正する
- 要約・省略・重複・cross-cue 曖昧・token 欠落・low-confidence は元時刻を保持し、行別 flag と日本語警告を返す。各行への無意味な編集は要求しない
- owning cue / range clamp、時系列、非重複、最低表示 500 ms を満たさない補正は採用せず fallback にする
- telop review lineage には timing policy、provenance、parent / payload fingerprint、status、行別 flag を含める。本文・時刻・alignment input・policy が変われば review と timing confirmation を失効させ、A → B → A でも自動復帰させない
- `subtitle_burn.py`、FFmpeg の encode / concat、cut 境界、queue fingerprint、予約投稿、Codex 呼び出し回数の意味は変更しない。既存の整数ミリ秒正規化と input order を再利用する

### 16.3 Streamlit の状態境界

- UI は service / view model が返す `synchronized`、`timing_review_required`、`cannot_sync`、行別警告、次の操作を表示するだけとする。schema、fingerprint、alignment、validator を UI へ複製しない
- 現行の start / end editor、全文の誤字・固有名詞確認、最終 preview confirmation を維持する。low-confidence 行がある場合だけ、別の「要確認行の時刻を確認した」 gate を必須にする
- session state は draft buffer と widget identity に限定し、timing confirmation は line state の atomic / fail closed 境界へ保存する。通常 rerun、表示切替、再起動で Codex / Whisper / ffmpeg / upload を起動しない
- UI 大幅刷新は `ui-refactor-review-2026-08-04.md` の page shell → readonly view model → summary / workspace → stage bar → revision-aware editor → upload tracking → destructive confirmation の順で別 diff に分ける。T1 contract 前に telop editor を刷新しない

### 16.4 依存と検証

- T1 の依存は `S9-5 → T1-PLAN → T1-1 → T1-2 → T1-3 → T1-4 → T1-5 → S9-6`。S9-6 は T1-5 後の最終受け入れ専用として未完了のまま保持する
- 新規 pip 依存、従量課金 API、T1-2 以降・本番経路の追加 Whisper、manifest 外の解析、全動画再解析、全編 Whisper、47 本 backfill、実 upload、Studio 操作は追加しない。T1-1 の isolated bounded benchmark 以外の Whisper 実行は許可しない
- T1-1 は固定 manifest、`max_selected_spans` / `max_whisper_invocations` と実績、human audio onset gold、coverage、pooled / 群別 gate、各群の signed bias、CER、固有名詞、cue 欠落 / 重複、VTT fallback + 連結の現行出力同等性、wall、peak memory、bounded whisper-cli の再現情報を証跡化する。T1-5 は同じ evidence に加え、scope guard、全 pytest、diff check、compileall、隔離 data_dir / 検証用 copy への再生成 preview、production hash unchanged を証跡化する。結果後に gate を緩和しない
- T1-5 は同期 component acceptance、S9-6 は formal phase acceptance とする。T1-5 の immutable evidence は入力 fingerprint が一致する場合に S9-6 が参照できるが、人 preview、A/B、gold、失効、cache、fallback、scope の最終 gate を省略しない。AC-40 は S9-6 formal PASS 時にだけ完了へ更新する

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-04 | **T1 親レビュー指摘を反映。** raw token timing が無い場合の T1-1 隔離 bounded whisper-cli benchmark、群別 / pooled gate、T1-5 の隔離 preview と production 不変、component acceptance / formal phase acceptance の分離、AC-40 の完了時点を追記した。 |
| 2026-08-04 | **T1 技術方針を追加。** timing payload の Artifact v2 / immutable sidecar 選択を T1-2 ADR へ保留し、full JSON 再利用、strict provenance、atomic 保存、pure alignment、低信頼 fallback、独立 timing confirmation、R2 の Streamlit 境界、既存 subtitle burn / FFmpeg / queue / upload 非変更を固定した。 |
| 2026-07-30 | 初版作成（MVP v1） |
| 2026-07-30 | 主要インターフェースを CLI からローカル Web UI（Streamlit）に変更 |
| 2026-07-30 | v2 追加: ffmpeg 連結方針、Streamlit 非同期方針、日本語字幕フォント |
