"""クリップボードへのコピーを提供する共通部品."""

from __future__ import annotations

import json

import streamlit.components.v1 as components


def build_clipboard_copy_html(
    *,
    text: str,
    button_id: str,
    button_label: str,
    success_message: str = "コピーしました",
    hide_after_ms: int = 2000,
) -> str:
    """クリップボードコピー用の HTML を生成する（テスト可能な純粋関数）."""
    # JSON エンコード結果に "</script>" などが含まれると script タグが
    # 途中で閉じてしまうため、"</" を "<\/" に置換して埋め込む
    # （JavaScript 文字列リテラルとしては等価）。
    encoded = json.dumps(text, ensure_ascii=False).replace("</", "<\\/")
    return f"""
<div>
  <button id="{button_id}" type="button">{button_label}</button>
  <span id="{button_id}-msg" style="display:none;color:#0a7;margin-left:8px;">
    {success_message}
  </span>
</div>
<script>
  (function() {{
    const text = {encoded};
    const button = document.getElementById({json.dumps(button_id)});
    const message = document.getElementById({json.dumps(f"{button_id}-msg")});
    button.addEventListener("click", async function() {{
      try {{
        await navigator.clipboard.writeText(text);
        message.style.display = "inline";
        setTimeout(function() {{
          message.style.display = "none";
        }}, {hide_after_ms});
      }} catch (err) {{
        message.textContent = "コピーに失敗しました";
        message.style.display = "inline";
        message.style.color = "#c00";
      }}
    }});
  }})();
</script>
"""


def render_copy_button(
    text: str,
    *,
    label: str,
    key: str,
    height: int = 50,
) -> None:
    """navigator.clipboard.writeText を使うコピーボタンを描画する."""
    html = build_clipboard_copy_html(text=text, button_id=key, button_label=label)
    components.html(html, height=height)
