#!/bin/bash
# yt-live-kit 起動スクリプト（macOS ダブルクリック用）
set -e
cd "$(dirname "$0")" || exit 1

if ! command -v uv >/dev/null 2>&1; then
    echo ""
    echo "【エラー】uv がインストールされていません。"
    echo ""
    echo "yt-live-kit の起動には uv（Python パッケージ管理ツール）が必要です。"
    echo "ターミナルで次のコマンドを実行してインストールしてください:"
    echo ""
    echo '  curl -LsSf https://astral.sh/uv/install.sh | sh'
    echo ""
    echo "インストール後、ターミナルを再起動してから start.command を再度ダブルクリックしてください。"
    echo ""
    read -r -p "Enter キーを押して終了..."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "初回セットアップ中（依存パッケージをインストールしています）..."
    uv sync
    echo ""
fi

echo "yt-live-kit を起動しています..."
echo "ブラウザが自動で開きます。終了するときはこのターミナルウィンドウを閉じてください。"
echo ""

uv run streamlit run src/yt_live_kit/ui/app.py --server.address 127.0.0.1
