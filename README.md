# yt-live-kit

YouTube 公開ライブアーカイブから字幕を取得し、AI でチャプター（タイムライン）と切り抜き候補を生成するローカル Web UI ツールです。

## 前提ソフト

| ソフト | 用途 | 備考 |
|--------|------|------|
| **Python 3.11+** | 実行環境 | pyenv 等で管理 |
| **[uv](https://github.com/astral-sh/uv)** | 依存管理・実行 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **yt-dlp** | 字幕・動画取得 | **最新版推奨**（古い版では字幕取得失敗の実績あり） |
| **ffmpeg** | 動画切り出し | システム PATH 上に配置 |

## セットアップ

```bash
# リポジトリを clone した後
uv sync
```

## 起動方法

### 非エンジニア向け（macOS）

1. `start.command` をダブルクリック
2. ブラウザが自動で開き、アプリ画面が表示されます
3. 終了するときはターミナルウィンドウを閉じてください

### 開発者・上級者向け

```bash
uv run streamlit run src/yt_live_kit/ui/app.py
```

### CLI（上級者向け補助）

```bash
uv run yt-live-kit version
```

## 使い方（概要）

1. YouTube 公開アーカイブ URL を貼り付けて「実行」を押す
2. 生成されたタイムラインを「コピー」して YouTube 概要欄に貼る
3. 切り抜き候補から区間を選び「切り出し」で動画ファイルを取得する

## データ保存先

成果物は `data/{video_id}/` 配下に保存されます（git 管理外）。

設定は環境変数 `YTLK_` プレフィックスで変更できます（例: `YTLK_DATA_DIR=./my-data`）。

## ライセンス

（未定）
