# yt-live-kit

YouTube 公開ライブアーカイブから字幕を取得し、AI でチャプター（タイムライン）と切り抜き候補を生成するローカル Web UI ツールです。

## 前提ソフト

| ソフト | 用途 | 備考 |
|--------|------|------|
| **Python 3.11+** | 実行環境 | pyenv 等で管理 |
| **[uv](https://github.com/astral-sh/uv)** | 依存管理・実行 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **yt-dlp** | 字幕・動画取得 | **最新版推奨**（古い版では字幕取得失敗の実績あり） |
| **ffmpeg** | 動画切り出し | システム PATH 上に配置（切り抜き機能で使用） |
| **Codex CLI** | チャプター自動生成 | 未導入時は画面に日本語で手順が表示されます |

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

## 使い方（タイムライン生成）

1. アプリを起動する（上記「起動方法」参照）
2. **YouTube URL** 欄に、公開アーカイブの URL を貼り付ける  
   例: `https://www.youtube.com/watch?v=xxxxxxxxxxx`
3. **「実行」** ボタンを押す
4. 進捗が表示されます（字幕取得 → 整形 → チャプター生成）
5. 完了後、**タイムライン** が表示されます
6. コードブロック右上の **コピーアイコン** で概要欄用テキストをコピーし、YouTube 概要欄に貼り付ける
7. **「文字起こし全文」** を開くと、整形済み字幕の確認と **ダウンロード** ができます

### Codex CLI 未導入時

チャプター自動生成には Codex CLI が必要です。未導入の場合、画面に日本語でインストール手順が表示されます。  
手動運用する場合は CLI の `--prompt-only` オプションも利用できます（上級者向け）。

---

## CLI（上級者向け補助）

```bash
uv run yt-live-kit version
uv run yt-live-kit fetch "https://www.youtube.com/watch?v=VIDEO_ID"
uv run yt-live-kit transcript VIDEO_ID
uv run yt-live-kit chapters VIDEO_ID          # Codex CLI で自動生成
uv run yt-live-kit chapters VIDEO_ID --prompt-only  # プロンプトのみ（Cursor 手動用）
```

### Codex CLI 未導入時（チャプター生成のフォールバック）

1. `uv run yt-live-kit chapters VIDEO_ID --prompt-only` でプロンプトを生成
2. 生成された `data/{video_id}/prompt_chapters.txt` を Cursor チャットに貼り付け
3. 出力されたチャプター行をファイルに保存し、検証・取り込み:
   ```bash
   uv run yt-live-kit chapters VIDEO_ID --from-file /path/to/chapters.txt
   ```

---

## データ保存先

成果物は `data/{video_id}/` 配下に保存されます（git 管理外）。

```
data/{video_id}/
  meta.json
  subtitles/ja.vtt
  transcript/full.txt
  transcript/compressed.txt
  chapters/chapters.md
```

設定は環境変数 `YTLK_` プレフィックスで変更できます（例: `YTLK_DATA_DIR=./my-data`）。

## セキュリティ

Web UI は **localhost（127.0.0.1）のみ** で動作します。インターネット上に公開されません。

## ライセンス

（未定）
