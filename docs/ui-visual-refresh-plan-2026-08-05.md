# UI 視覚刷新の調査・方針

**調査日:** 2026-08-05
**対象:** `yt-live-kit` v0.3.0、ショート生産ラインの Streamlit UI 視覚レイヤー
**基準:** `docs/ui-refactor-review-2026-08-04.md`（R2、完了済み）の境界整理結果
**視覚リファレンス:** `docs/references/u6-short-production-line-v3.2.png`（`docs/requirements-v3.md:257` の FR-33）

この文書は R2 完了後に残る視覚刷新レイヤーの調査結果と方針をまとめたものであり、2026-08-05 時点の実装・テスト状態を基準とする。タスク定義と進捗チェックの正本は `docs/execution-plan-v3.md` であり、本文書はその実行計画に投入するフェーズ定義（`U9`）の根拠資料として位置づける。フェーズ定義の下書きは別途 execution-plan-v3.md への挿入用として作成し、本文書自体は execution-plan-v3.md を編集しない。

## 1. 結論

視覚刷新は Streamlit ネイティブテーマ（案 A+）を適用し、限定 CSS 注入（案 B）とカスタム React コンポーネント（案 C）は採らない。A+ は `.streamlit/config.toml` のみで Python コードを 0 行に保てるため回帰リスクが実質ゼロである。2026-08-05 の A+ 適用後実測では、CSS だけで追加できる主要差分は精巧な丸数字ステッパー、完全な下線タブ、赤い波下線だった。一方、工程ステッパーは U9-6 のネイティブ縦表示で現在地・一本道・次の一手を満たし、赤い波下線を含むテロップ編集器は T1（テロップ行時刻同期）の `T1-4` へ合流済みである。`unsafe_allow_html` 0 件を崩し Streamlit 内部 DOM 依存を持つだけの便益はないため、案 B は不採用とする。

## 2. 現在地

リファレンス画像 `docs/references/u6-short-production-line-v3.2.png` は要件書の正式な視覚リファレンス（FR-33）であるが、状態遷移・失効・生成条件は本文を正本とし、画像と不一致の場合は本文を優先するというルールが付いている。機能面は U6 完了・M15 達成済みであり、U9 も 2026-08-05 に完了した。

R2（UI 大幅刷新前の境界整理・回帰リスク監査）は 2026-08-04 に完了した。R2 の目的文はこのリファレンス画像を名指しし、「この視覚階層へ大幅刷新する前に境界を整理する。見た目の変更は行わない」としている。R2 後に実行計画へ U9 を定義し、テーマ・shell・掃除を完了した。テロップ編集器だけは時刻同期契約と合わせるため `T1-4` へ分離している。

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

**最終結論: A+ を実施し、B と C は採らない。**

理由:

1. A+ は `config.toml` のみで Python コード 0 行のため回帰リスクが実質ゼロである
2. A+ を当てる前に B の範囲を決められない。A+ 適用後に残差を実測すれば、残るのは実質ステッパー 1 個という具体的な問いになる
3. A+ は B の前提であって代替ではない。B を選んでも A+ は必ず実施することになる。順番の問題であり選択の問題ではない
4. `unsafe_allow_html` 0 件という一貫性を捨てる判断は、それに見合う残差が実測で見えてから行う

A+ と native shell で届かないのは色や余白ではなく「形」に絞られる。完全な下線タブ、精巧な丸数字ステッパー、赤い波下線であり、前二者は意図的残差、テロップ編集器に属する波下線は `T1-4` の判断対象とする。KPI カードの Material icon と値の階層は native `st.metric` で実装済みである。

### スタイリング方針の現状（A+ 適用後実測）

- `src/` 全体で `unsafe_allow_html` が 0 件。`<style>` 注入も 0 件
- `st.data_editor` は 0 件
- `streamlit.components.v1.html` は `src/yt_live_kit/ui/components/clipboard.py:62` の 1 箇所のみ（クリップボード用）
- `.streamlit/config.toml` の `[theme]` に Streamlit 1.60 の 23 オプションを設定済みで、`base = "dark"` と `[theme.sidebar]` によりダーク配色・文字・形状・チャート・サイドバー配色を固定している
- 導入版 Streamlit は 1.60.0、`pyproject.toml` の宣言は `streamlit>=1.59.0`

Streamlit 1.60 のネイティブテーマ機能は実測で 23 オプションある。配色: `base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `linkColor`, `codeBackgroundColor`。文字: `font`, `baseFontSize`, `headingFont`, `headingFontSizes`, `headingFontWeights`, `codeFont`, `codeFontSize`, `codeFontWeight`。形状: `baseRadius`, `buttonRadius`, `borderColor`, `dataframeBorderColor`, `showWidgetBorder`, `showSidebarBorder`。チャート: `chartCategoricalColors`, `chartSequentialColors`。セクションは `[theme]` / `[theme.sidebar]` / `[theme.light]` / `[theme.dark]` / `[theme.light.sidebar]` / `[theme.dark.sidebar]` が利用可能で、サイドバーを本体と別配色にできる。

`[theme]` の `base` は「継承元テーマ」を指定するオプションで、`"light"` / `"dark"` / TOML テーマファイルへのローカルパス / TOML テーマファイルの URL のいずれかを取る。`[theme.light]` / `[theme.dark]` は Streamlit の config 定義上「`[theme]` で定義したプロパティを拡張する light / dark 個別のテーマプロパティ」であり、mode 別の上書きセクションである。同様に `[theme.light.sidebar]` / `[theme.dark.sidebar]` は `[theme.sidebar]` を拡張する。したがって OS / ブラウザ設定への追従を止めてダーク固定にしたい場合に使うのは `[theme] base = "dark"` であり、`[theme.light]` / `[theme.dark]` の定義は mode 追従を前提とした上書き機構である。リファレンス画像はダーク前提のため、本フェーズでは `base = "dark"` で固定する方針とする。

## 5. リファレンス画像と現行実装の差分一覧

これらの差分は要件書本文優先ルール（FR-33 の「状態遷移・失効・生成条件は本文を正本とし、画像と不一致の場合は本文を優先する」）の範囲内であり、契約違反ではない。どこまで画像に寄せるかは仕様判断である。

| 項目 | 現行実装 | リファレンス画像 |
|---|---|---|
| 6 工程ステッパー | U9-6 でメインの横並び `st.badge` を削除し、サイドバーのネイティブ縦ステッパーへ一本化。完了 ✅ / 現在 ◉ / 未到達 🔒、接続線、次の一手を表示する | 丸数字 + 接続線 + 鍵アイコン。円形の精巧さだけを意図的な残差として許容する |
| テロップ確認テーブル | 行ごとの native widget 縦積み。T1 の時刻同期契約と同時に扱う必要があるため `T1-4` へ延期 | 時間を `00:00 - 00:02` のレンジ表記した表形式 |
| AI 案からの変更差分 | `AI案から変更` の静的フラグ。編集器全体とともに `T1-4` へ延期 | 具体的な旧→新テキスト |
| 赤い波下線のスペルチェック風表現 | 不在。限定 CSS が必要だが `T1-4` の編集器判断へ延期 | あり |
| 検証チップ | 「自動ハード判定」「自動警告」の native 表示。編集器全体とともに `T1-4` へ延期 | 個別 3 チップ |
| サイドバーの進捗表示 | U9-6 の縦ステッパー + 日次カウンタ + 次の一手で解消。割合だけの進捗バーは使わない | 進捗バー + 日次カウンタ |
| 「下書きを保存」ボタン | 不在。全 widget が `persist_state="session"` で自動永続化されるため概念自体が無い | あり |
| ヘッダ / KPI カード | 共通 main 見出しを compact sidebar brand へ移し、動画タイトルを主見出し化。Material icon 付き `st.metric(border=True)` 3 枚で値の階層も刷新済み | 左アイコン付き KPI カード 3 枚 |
| ワークスペース切替 | `st.segmented_control` の conditional rendering を維持。完全な下線だけは CSS が必要 | 下線タブ |

### 案 B の最終判断（U9-5）

**限定 CSS 注入は採らない。** U9-6 の縦ステッパーは FR-33 / AC-35 が要求する現在地・一本道・次の一手を native UI だけで満たし、完全な下線タブは見た目だけの差である。赤い波下線とテロップ表は `T1-4` の契約確定後に編集器全体として判断する。現時点で `unsafe_allow_html` 0 件の一貫性と Streamlit 内部 DOM 非依存を捨てる合理性はない。U9-7 / U9-8 は native container、columns、Material icon、`st.segmented_control` の範囲で仕上げる。

## 6. 実装順（3 弾構成）

R2 監査文書 `docs/ui-refactor-review-2026-08-04.md` §6「リファレンス画像へ進む実装順」と整合する。

- **第 1 弾: テーマ適用（A+）** — `config.toml` のみ。R2 §6 の手順 1 に対応する
- **（間に挟む）AppTest 視覚回帰スモークの追加** — 第 2 弾の安全網。第 1 弾は config のみなので安全網なしで通せるが、第 2 弾は widget 構成を組み替えるため必須とする
- **第 2 弾: shell 刷新** — サイドバーの「作成中のショート」縦ステッパー・日次カウンタ、ヘッダ、KPI カード 3 枚、ワークスペース切替。R2 §6 の手順 2〜4 に対応する。U9-4 の実測により B（限定 CSS 注入）は不採用とし、native UI で仕上げる
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
