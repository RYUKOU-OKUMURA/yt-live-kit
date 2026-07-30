# yt-live-kit

YouTube 公開ライブアーカイブから字幕を取得し、AI でチャプター（タイムライン）と切り抜き候補を生成するローカル Web UI ツールです。

## 前提ソフト

| ソフト | 用途 | 備考 |
|--------|------|------|
| **Python 3.11+** | 実行環境 | pyenv 等で管理 |
| **[uv](https://github.com/astral-sh/uv)** | 依存管理・実行 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **yt-dlp** | 字幕・動画取得 | **最新版推奨**（2025.02.19 以前では字幕取得失敗の実績あり） |
| **ffmpeg** | 動画切り出し | システム PATH 上に配置（切り抜き機能で使用） |
| **Codex CLI** | チャプター・候補の自動生成 | 未導入時は画面に日本語で手順が表示されます |

## セットアップ（初回のみ）

```bash
# リポジトリを clone した後
uv sync
```

`start.command` を使う場合、初回起動時に自動で `uv sync` が走ります（`.venv` がないとき）。

---

## 起動方法

### 非エンジニア向け（macOS・推奨）

1. **`start.command` をダブルクリック**  
   Finder でプロジェクトフォルダを開き、`start.command` をダブルクリックします。
2. **初回のみ**  
   「初回セットアップ中…」と表示されたら、完了まで待ちます（数分かかることがあります）。
3. **ブラウザが自動で開く**  
   `http://127.0.0.1:8501` にアプリ画面が表示されます。開かない場合は、ターミナルに表示される URL をブラウザのアドレスバーに貼り付けてください。
4. **終了するとき**  
   ターミナルウィンドウ（黒い画面）を **閉じる** だけで OK です。ブラウザのタブだけ閉じても、アプリはバックグラウンドで動き続ける場合があります。

#### うまく起動しないとき

| 症状 | 対処 |
|------|------|
| 「uv がインストールされていません」と出る | ターミナルで `curl -LsSf https://astral.sh/uv/install.sh \| sh` を実行し、ターミナルを再起動してから再度ダブルクリック |
| 「開発元を確認できないため開けません」 | 右クリック →「開く」→ もう一度「開く」 |
| ブラウザが開かない | ターミナルに表示された `http://127.0.0.1:8501` を手動で開く |

### 開発者・上級者向け

```bash
uv run streamlit run src/yt_live_kit/ui/app.py --server.address 127.0.0.1
```

---

## 日常操作

### 単本実行（タイムライン生成）

1. アプリを起動する（上記「起動方法」参照）
2. **実行** タブで **単本** を選択
3. **YouTube URL** 欄に、公開アーカイブの URL を貼り付ける  
   例: `https://www.youtube.com/watch?v=xxxxxxxxxxx`
4. **「実行」** ボタンを押す
5. 進捗が表示されます（字幕取得 → 整形 → チャプター生成 → 切り抜き候補）
6. 完了後、**タイムライン** が表示されます
7. コードブロック右上の **コピーアイコン** で概要欄用テキストをコピーし、YouTube 概要欄に貼り付ける
8. **「文字起こし全文」** を開くと、整形済み字幕の確認と **ダウンロード** ができます
9. **切り抜き候補** から区間を選び **「切り出し」** で mp4 を取得できます

### 一括処理（複数 URL）

1. **実行** タブで **一括** を選択
2. テキストエリアに URL を **1 行 1 本** で貼り付ける（`#` で始まる行はコメントとして無視）
3. **「処理済みをスキップ」** にチェックを入れると、チャプター済みの動画を飛ばします
4. **「一括実行」** を押す
5. 進捗バーとログで成功・スキップ・失敗を確認できます
6. 失敗した URL があっても、残りは継続して処理されます

### 処理済み一覧

1. **処理済み一覧** タブを開く
2. 過去に処理した動画（タイトル・video_id・成果物の有無）が表示されます
3. **「開く」** で、保存済みのタイムライン・全文・切り抜き候補を再表示できます

---

## Codex CLI 未導入時

チャプター・切り抜き候補の自動生成には Codex CLI が必要です。未導入の場合、画面に日本語でインストール手順が表示されます。

```bash
npm install -g @openai/codex
codex login
```

手動運用する場合は CLI の `--prompt-only` オプションも利用できます（上記 CLI 節参照）。

---

## CLI（上級者向け補助）

Web UI と同じ services 層を呼び出します。

```bash
uv run yt-live-kit version

# 単本: 一括パイプライン
uv run yt-live-kit run "https://www.youtube.com/watch?v=VIDEO_ID"

# 段階実行
uv run yt-live-kit fetch "https://www.youtube.com/watch?v=VIDEO_ID"
uv run yt-live-kit transcript VIDEO_ID
uv run yt-live-kit chapters VIDEO_ID
uv run yt-live-kit clips suggest VIDEO_ID

# 切り抜き（人が選んだ区間）
uv run yt-live-kit clips cut VIDEO_ID --start 00:03:42 --end 00:16:30 --output clip_001.mp4

# バッチ（URL 一覧ファイル、処理済みスキップ、スリープ間隔）
uv run yt-live-kit run --urls-file urls.txt --skip-existing --sleep 2
```

### Codex CLI 未導入時（チャプター生成のフォールバック）

1. `uv run yt-live-kit chapters VIDEO_ID --prompt-only` でプロンプトを生成
2. 生成された `data/{video_id}/prompt_chapters.txt` を Cursor チャットに貼り付け
3. 出力されたチャプター行をファイルに保存し、検証・取り込み:
   ```bash
   uv run yt-live-kit chapters VIDEO_ID --from-file /path/to/chapters.txt
   ```

### 共通オプション（run）

| オプション | 説明 |
|------------|------|
| `--data-dir` | 成果物ルート（デフォルト: `./data`） |
| `--skip-existing` | チャプター済み動画 ID をスキップ |
| `--sleep` | URL 間のスリープ秒数（デフォルト: 1 秒、`YTLK_SLEEP` でも設定可） |
| `--yt-dlp-path` | yt-dlp バイナリパス |

バッチ処理のステータスは `data/_batch/status.json` に記録されます。

---

## yt-dlp の更新

字幕取得に失敗する場合、まず yt-dlp を最新版に更新してください。**2025.02.19 以前** のバージョンでは字幕取得に失敗する実績があります。アプリ起動時に古いバージョンが検出されると警告が表示されます。

```bash
# pip / uv 経由
uv pip install -U yt-dlp

# Homebrew（macOS）
brew upgrade yt-dlp

# バージョン確認
yt-dlp --version
```

---

## ffmpeg

切り抜き機能ではシステム PATH 上の `ffmpeg` を使用します。未インストールの場合:

```bash
# macOS（Homebrew）
brew install ffmpeg

# バージョン確認
ffmpeg -version
```

環境変数 `YTLK_FFMPEG_PATH` で別パスを指定できます。

---

## トラブルシュート

| 症状 | 原因と対処 |
|------|------------|
| 「字幕が取得できませんでした」 | 公開アーカイブか確認。yt-dlp を最新に更新して再実行 |
| チャプターが生成されない | Codex CLI のインストール・認証を確認。または `--prompt-only` で手動生成 |
| 切り抜き候補だけ失敗 | タイムラインは利用可能。Codex CLI または手動 JSON 保存を試す |
| 一括処理で一部失敗 | ログで失敗 URL を確認。`--skip-existing` で再実行すると成功分はスキップされる |
| yt-dlp 警告が出る | 上記「yt-dlp の更新」を実施 |
| 切り出しが失敗 | ffmpeg が PATH にあるか確認。`YTLK_FFMPEG_PATH` を設定 |

---

## データ保存先

成果物は `data/{video_id}/` 配下に保存されます（git 管理外）。

```
data/
  _batch/
    status.json          # バッチ処理ステータス
  {video_id}/
    meta.json
    subtitles/ja.vtt
    transcript/full.txt
    transcript/compressed.txt
    chapters/chapters.md
    clips/candidates.json
    clips/*.mp4          # 切り出し結果
```

設定は環境変数 `YTLK_` プレフィックスで変更できます（例: `YTLK_DATA_DIR=./my-data`、`YTLK_SLEEP=2`）。

## セキュリティ

Web UI は **localhost（127.0.0.1）のみ** で動作します。インターネット上に公開されません。

## ライセンス

（未定）
