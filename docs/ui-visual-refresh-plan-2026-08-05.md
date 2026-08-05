# UI 視覚刷新の調査・方針

**調査日:** 2026-08-05
**対象:** `yt-live-kit` v0.3.0、ショート生産ラインの Streamlit UI 視覚レイヤー
**基準:** `docs/ui-refactor-review-2026-08-04.md`（R2、完了済み）の境界整理結果
**視覚リファレンス:** `docs/references/u6-short-production-line-v3.2.png`（`docs/requirements-v3.md:257` の FR-33）

この文書は R2 完了後に残る視覚刷新レイヤーの調査結果と方針をまとめたものであり、2026-08-05 時点の実装・テスト状態を基準とする。タスク定義と進捗チェックの正本は `docs/execution-plan-v3.md` であり、本文書はその実行計画に投入するフェーズ定義（`U9`）の根拠資料として位置づける。フェーズ定義の下書きは別途 execution-plan-v3.md への挿入用として作成し、本文書自体は execution-plan-v3.md を編集しない。

## 1. 結論

視覚刷新は Streamlit ネイティブテーマ（案 A+）を先に適用し、限定 CSS 注入（案 B）は保留する。カスタム React コンポーネント（案 C）は採らない。A+ は `.streamlit/config.toml` のみで Python コードを 0 行に保てるため回帰リスクが実質ゼロであり、B の適用範囲は A+ 適用後の残差を実測してから判断する。実装順は「第 1 弾: テーマ適用（A+）」「AppTest 視覚回帰スモークの追加」「第 2 弾: shell 刷新」「第 3 弾: テロップ編集器の刷新」の 3 弾構成とし、第 3 弾は独立フェーズとせず T1（テロップ行時刻同期）の `T1-4` に合流させる。

## 2. 現在地

リファレンス画像 `docs/references/u6-short-production-line-v3.2.png` は要件書の正式な視覚リファレンス（FR-33）であるが、状態遷移・失効・生成条件は本文を正本とし、画像と不一致の場合は本文を優先するというルールが付いている。機能面は U6 完了・M15 達成済みであり、`1694 tests collected` である。

R2（UI 大幅刷新前の境界整理・回帰リスク監査）は 2026-08-04 に完了した。R2 の目的文はこのリファレンス画像を名指しし、「この視覚階層へ大幅刷新する前に境界を整理する。見た目の変更は行わない」としている。R2 で P1 / P2 は解消済みであり、残っているのは視覚レイヤーのみである。実行計画にはこの視覚レイヤーに対応するタスク ID がまだ定義されていない。

## 3. Findings

新規に発見した構造課題を R2 の P1 / P2 / P3 分類に倣って整理する。R2 監査文書 §3「Findings」の P3 リストに記載済みの項目はここに再掲しない。

### P1 — 視覚刷新（第 2 弾）着手前に解消する項目

- UI テストは 14 ファイルあるが `streamlit.testing` / `AppTest` の使用が 0 件である。`tests/test_ui_video_detail_page.py` は `import streamlit` すらせず `_render_*` を `MagicMock` 直呼びしており、「ボタンが押されていない分岐」しか実質検証していない。視覚回帰の安全網が存在しないため、shell を組み替える第 2 弾より前に AppTest スモークを追加する
- `src/yt_live_kit/ui/components/shorts_queue.py:502` の候補選択チェックボックス key は `f"{prefix}_{source}_{index}"` とループ index 依存になっている。候補は `.id` を持つのに使っておらず、R2-4 が定めた「配列 index ではなく source + ID」の契約から漏れている

### P2 — 視覚刷新と同時に解消して構わない軽微な項目

- `src/yt_live_kit/ui/views/video_detail.py:653` の `_render_clips`（見出し「3. 切り抜き候補」）は定義のみで呼び出し箇所がゼロのデッドコードである。刷新時に生きたコードと誤認する危険があるため早期に削除する
- `src/yt_live_kit/ui/pages/` に `__pycache__` のみが残存している。`st.navigation` 移行時の掃除漏れである。実測では `.gitignore` に `__pycache__/` があり、`git ls-files src/yt_live_kit/ui/pages/` は空、すなわち git 管理外である。したがってこれはコミット差分の発生しないローカルファイルシステム上の整理であり、コミット対象にはならない

### P3 — 視覚刷新と分けて追跡する項目

- `st.rerun()` は 44 箇所あり、うち `scope="app"` 指定は `src/yt_live_kit/ui/app.py:121` と `src/yt_live_kit/ui/components/status_bar.py:383,563,582` の 4 箇所のみである。`st.fragment` は `status_bar.py:585` の 1 箇所のみで、部分再描画の仕組みがほぼ無い。視覚刷新（A+ / B の適用）自体の必須条件ではないため、別途設計検討とする

## 4. スタイリング方針の比較と結論

| 案 | 内容 | 到達度 | 難易度 |
|---|---|---|---|
| A+ | Streamlit ネイティブテーマ 23 オプションを使い切る | 6〜7 割 | 極小（`config.toml` のみ、Python コード 0 行） |
| B | A+ に限定的な CSS 注入を足す | 8〜9 割 | 中（Streamlit 内部 DOM への依存が生まれる） |
| C | カスタム React コンポーネント | 10 割 | 大（Node / ビルド系依存が増える） |

**結論: A+ を先に実施し、B は保留とする。C は採らない。**

理由:

1. A+ は `config.toml` のみで Python コード 0 行のため回帰リスクが実質ゼロである
2. A+ を当てる前に B の範囲を決められない。A+ 適用後に残差を実測すれば、残るのは実質ステッパー 1 個という具体的な問いになる
3. A+ は B の前提であって代替ではない。B を選んでも A+ は必ず実施することになる。順番の問題であり選択の問題ではない
4. `unsafe_allow_html` 0 件という一貫性を捨てる判断は、それに見合う残差が実測で見えてから行う

A+ で届かないのは色や余白ではなく「形」に絞られる。丸数字ステッパー + 接続線 + 鍵アイコン、赤い波下線、KPI カード内の左アイコン配置である。

### スタイリング方針の現状（実測）

- `src/` 全体で `unsafe_allow_html` が 0 件。`<style>` 注入も 0 件
- `st.data_editor` は 0 件
- `streamlit.components.v1.html` は `src/yt_live_kit/ui/components/clipboard.py:62` の 1 箇所のみ（クリップボード用）
- `.streamlit/config.toml` は `[server] address = "127.0.0.1"` のみで `[theme]` セクションが存在しない。ダーク表示は OS / ブラウザ設定への完全依存である
- 導入版 Streamlit は 1.60.0、`pyproject.toml` の宣言は `streamlit>=1.59.0`

Streamlit 1.60 のネイティブテーマ機能は実測で 23 オプションある。配色: `base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `linkColor`, `codeBackgroundColor`。文字: `font`, `baseFontSize`, `headingFont`, `headingFontSizes`, `headingFontWeights`, `codeFont`, `codeFontSize`, `codeFontWeight`。形状: `baseRadius`, `buttonRadius`, `borderColor`, `dataframeBorderColor`, `showWidgetBorder`, `showSidebarBorder`。チャート: `chartCategoricalColors`, `chartSequentialColors`。セクションは `[theme]` / `[theme.sidebar]` / `[theme.light]` / `[theme.dark]` / `[theme.light.sidebar]` / `[theme.dark.sidebar]` が利用可能で、サイドバーを本体と別配色にできる。

`[theme]` の `base` は「継承元テーマ」を指定するオプションで、`"light"` / `"dark"` / TOML テーマファイルへのローカルパス / TOML テーマファイルの URL のいずれかを取る。`[theme.light]` / `[theme.dark]` は Streamlit の config 定義上「`[theme]` で定義したプロパティを拡張する light / dark 個別のテーマプロパティ」であり、mode 別の上書きセクションである。同様に `[theme.light.sidebar]` / `[theme.dark.sidebar]` は `[theme.sidebar]` を拡張する。したがって OS / ブラウザ設定への追従を止めてダーク固定にしたい場合に使うのは `[theme] base = "dark"` であり、`[theme.light]` / `[theme.dark]` の定義は mode 追従を前提とした上書き機構である。リファレンス画像はダーク前提のため、本フェーズでは `base = "dark"` で固定する方針とする。

## 5. リファレンス画像と現行実装の差分一覧

これらの差分は要件書本文優先ルール（FR-33 の「状態遷移・失効・生成条件は本文を正本とし、画像と不一致の場合は本文を優先する」）の範囲内であり、契約違反ではない。どこまで画像に寄せるかは仕様判断である。

| 項目 | 現行実装 | リファレンス画像 |
|---|---|---|
| 6 工程ステッパー | `src/yt_live_kit/ui/components/shorts_line.py:933-944` の `st.badge` 横並び（例「3. テロップ確認・進行中」） | 丸数字 + 接続線 + 鍵アイコン |
| テロップ確認テーブル | `shorts_line.py:745-780` で行ごとに `st.container(border=True)` + `st.text_input`（本文）+ `st.number_input` × 2（開始秒・終了秒）+ `st.toggle`（強調）の縦積み | 時間を `00:00 - 00:02` のレンジ表記した表形式 |
| AI 案からの変更差分 | `shorts_line.py:775-780` の `st.caption("AI案から変更")` という静的フラグのみ | 具体的な旧→新テキスト |
| 赤い波下線のスペルチェック風表現 | 不在（CSS 必須） | あり |
| 検証チップ | `shorts_line.py:1393-1405` で「自動ハード判定」「自動警告」の 2 行集約 | 個別 3 チップ |
| サイドバーの進捗バー | 不在（`shorts_line.py:947-966` は `st.write` / `st.caption` のみ） | あり |
| 「下書きを保存」ボタン | 不在。全 widget が `persist_state="session"` で自動永続化されるため概念自体が無い | あり |
| ワークスペース切替 | `src/yt_live_kit/ui/views/video_detail.py:1188` の `st.segmented_control` | 下線タブ |

## 6. 実装順（3 弾構成）

R2 監査文書 `docs/ui-refactor-review-2026-08-04.md` §6「リファレンス画像へ進む実装順」と整合する。

- **第 1 弾: テーマ適用（A+）** — `config.toml` のみ。R2 §6 の手順 1 に対応する
- **（間に挟む）AppTest 視覚回帰スモークの追加** — 第 2 弾の安全網。第 1 弾は config のみなので安全網なしで通せるが、第 2 弾は widget 構成を組み替えるため必須とする
- **第 2 弾: shell 刷新** — サイドバーの「作成中のショート」カード・進捗バー・日次カウンタ、ヘッダ、KPI カード 3 枚、ワークスペース切替、6 工程ステッパー。R2 §6 の手順 2〜4 に対応する。ここで初めて B（限定 CSS 注入）の要否を判断する。B を使わずステッパーを `st.badge` のまま妥協して終える選択肢も残す
- **第 3 弾: テロップ編集器の刷新** — R2 §6 の手順 5 に対応する。A / B の判断とは独立に T1（テロップ行時刻同期）の契約確定を待つ。`docs/execution-plan-v3.md:1836` および `:1849` が「ページ shell〜工程 bar の刷新は計画更新後の別 diff とし、telop editor の刷新は T1 contract 後に分離する」と既に指示しているため、`T1-4` に合流させる。第 3 弾には新規タスク ID を振らない

## 7. 継承する安全境界

R2 監査文書 §5「変更してはいけない安全境界」を全項目そのまま継承する。特に視覚刷新で踏みやすいものとして以下を明記する。

- `st.tabs` への単純な置換を行わない。Streamlit の tab は非表示 tab の内容も毎 rerun 実行するため、3 ワークスペースは conditional rendering を維持する。リファレンス画像は下線タブに見えるが、見た目だけタブ風にして `st.segmented_control` の条件描画を維持する
- worker thread / target から `st.*` を呼ばない
- 確認 dialog より前に upload、概要欄更新、削除、再生成を起動しない
- service transaction を view ファイルへコピーしない。既存 controller / adapter の呼び出し点を保ったまま presentation を交換する

## 8. 検証方針

- 第 1 弾は `.streamlit/config.toml` のみの変更であるため、Python コード差分が発生していないことと既存の全件回帰テストが通ることを検証の中心とする
- AppTest 視覚回帰スモークは、第 2 弾着手前に主要ページが例外なく描画できることを確認する
- 第 2 弾では `st.segmented_control` による 3 ワークスペースの conditional rendering が維持されていることを確認する
- R2 監査文書 §5 の安全境界（transaction 順序、確認 dialog、worker thread からの `st.*` 禁止、`st.tabs` への単純置換禁止）に差分が無いことを確認する
- 全件 `uv run pytest` が変更前の基準から回帰しないことを確認する
