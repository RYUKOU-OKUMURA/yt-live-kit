# yt-live-kit 要件定義書 v3

**バージョン:** v3（ショート量産・投稿）
**最終更新:** 2026-08-01
**対象:** ローカル Web UI ツール「yt-live-kit」
**関連:** [v1 要件定義書](./requirements.md) / [v2 実行計画](./execution-plan-v2.md) / [v3 実行計画](./execution-plan-v3.md) / [AGENTS.md](../AGENTS.md)

---

## 1. 概要・背景

### 1.1 概要

v3 のテーマは **「ショート動画の安定量産」** である。v1 でタイムライン生成と切り抜き素材づくりを、v2 で非同期実行・チャンネル取り込み・ハイライト動画・縦型ショート動画を実現した。v3 では、これらの上に「複数区間をつないだショートをテロップ付きで量産し、予約投稿まで通す」パイプラインを構築する。

v3 は次の 3 フェーズで構成する。

| フェーズ | 名称 | 内容 |
|----------|------|------|
| **フェーズ U** | UI 骨格リファクタ | サイドバー事故の解消。機能軸 → 動画軸の情報設計への作り替え |
| **フェーズ S** | ショート量産パイプライン | ジャンプカット連結・テロップ焼き込み・メタデータ生成・キュー量産 UI |
| **フェーズ P** | 投稿・予約投稿 | YouTube への非公開アップロード + `publishAt` 予約投稿 |

**フェーズ U を先に行う理由:** ショート機能（フェーズ S）は 1 本の動画に対して「区間選択 → 台本確認 → 生成 → メタデータコピー」という工程が積み重なり、既存 UI の中で最も画面が重くなる。悪い土台（後述 §3 の 4 つの事実）の上にこれを載せると、ショート機能を作った直後に UI を作り直す二度手間になる。そのため v3 ではまず UI 骨格を作り替えてから機能を積む。

### 1.2 背景 — v2 までの到達点

2026-08-01 時点で、v2 の機能（[`services/highlights.py`](../src/yt_live_kit/services/highlights.py)、[`services/shorts.py`](../src/yt_live_kit/services/shorts.py)、[`services/channel.py`](../src/yt_live_kit/services/channel.py)、[`services/storage.py`](../src/yt_live_kit/services/storage.py)、[`services/jobs.py`](../src/yt_live_kit/services/jobs.py) 等）は実装済みで、`uv run pytest` は 375 件全通過している。処理済み動画は 47 本（`data/` 配下）。YouTube Data API 連携（[`services/youtube_api.py`](../src/yt_live_kit/services/youtube_api.py)）による概要欄への反映も実装済みで、OAuth スコープは `youtube.force-ssl`（アップロード権限も含む）。

一方で、[`docs/execution-plan-v2.md`](./execution-plan-v2.md) の進捗チェックは実装の完了に追いついていない（実機確認・受け入れ確認タスクが未消化のまま残っている）。この扱いは [`docs/execution-plan-v3.md`](./execution-plan-v3.md) の「v2 未完了タスクの扱い」節で仕分けする。

### 1.3 v3 の動機

47 本の処理済み動画がありながら、ショート動画量産は依然として手作業（候補選び → 手元の編集ソフトで加工 → 手動アップロード）に依存している。v3 で解決したい課題は次の 3 点である。

| 課題 | 内容 |
|------|------|
| **量産できない** | 区間の連結・テロップ焼き込み・メタデータ作成が個別の手作業で、1 本あたりの制作コストが高い |
| **迷う** | 現行 UI は「機能軸」（実行 / チャンネル / 処理済み一覧の 3 タブ）で並んでおり、実際の作業である「動画軸」（1 本の動画に対してチャプター→候補→ショート→概要欄反映と進む）と噛み合わない。次に何をすべきか画面から読み取れない |
| **誤操作が起きやすい** | 処理済み一覧は 47 件フラット表示 + 破壊的ボタン 5 連発が確認なしで並んでおり、誤操作を誘発する構造になっている |

### 1.4 v3 の利用シーン

| シーン | 説明 |
|--------|------|
| **新着の取り込み** | 取り込みページを開くと、登録済みチャンネルの新着一覧が自動で表示される。未処理分を選んで「処理開始」を押すだけでよい（URL の手入力は例外ルート） |
| **1 本を仕上げる** | ライブラリページで動画を選ぶと動画詳細ページに遷移し、ステッパーに従って「次にやること」だけを進める。チャプター確認 → ハイライト候補選定 → ショート作成 → 概要欄反映まで、1 画面で完結する |
| **ショートの量産** | 動画詳細ページで複数のハイライト候補にチェックを入れ、生成前に 1 本ずつテロップ台本を生成・確認・修正する。全対象を確定してから「まとめて生成」を押し、単一のバックグラウンドジョブが入力順に処理する |
| **量産物の投稿** | 生成済みショートのカードから「予約投稿」を押すと、実チャンネル、対象ファイル、サイズ・尺、タイトル・説明文・タグ、公開予定日時、通知設定を含むプレビューが表示される。確認して確定すると、同じ内容を再検証したうえでスケジュールポリシーに従った枠へ非公開アップロードされる |
| **誤操作からの保護** | 削除・再生成・概要欄反映・投稿は、すべて確認ダイアログ（`st.dialog`）を経由する。特に概要欄反映と投稿は反映前後の内容比較を必須で表示する |

---

## 2. v2 からのスコープ改訂

[`docs/requirements.md`](./requirements.md) §6.1.1 は v2 時点で「自動編集」の一部（区間連結・縦横比変換・既存字幕焼き込み）のみを解禁し、ジャンプカット・テロップ生成・自動投稿は明示的にスコープ外としていた。**v3 ではこの一部をさらに解禁する。**

| 項目 | v2 までの扱い | v3 での扱い | 理由 |
|------|----------------|-------------|------|
| ジャンプカット（無音・フィラー除去に限らず、複数区間の連結） | スコープ外 | **解禁**（FR-25） | 「主観的な自動判定」ではなく「人が選んだ複数区間を機械的に連結する」処理として再定義する。区間選定は人（または AI 提案 + 人の確認）が行い、ffmpeg は決定論的に連結するだけ |
| テロップ生成（新規テキストの作成・配置） | スコープ外 | **解禁**（FR-22〜FR-24） | 自動字幕（VTT）をベースに、Codex CLI が誤字修正・分割・強調行フラグの付与までを行った「台本」を **人が確認してから** 焼き込む。全自動の生成配置ではなく、人の確認ステップを必須にすることで品質を担保する |
| YouTube への自動投稿 | スコープ外 | **解禁**（FR-27〜FR-28） | `videos.insert` は既に OAuth スコープでカバー済み（新規認可不要）。`privacyStatus=private` + `publishAt` による予約投稿に限定し、即時公開は行わない |
| BGM・効果音の付与 | スコープ外 | **スコープ外のまま** | 権利処理が別問題として発生する |
| ズーム・トランジション・エフェクト | スコープ外 | **スコープ外のまま** | デザイン判断を含み、CapCut 等の外部ツールに任せる方が品質・速度ともに優れる |

**要約:** v3 で解禁するのは「人が選定・確認した素材を、決定論的な処理で連結・焼き込み・投稿する」までである。**見た目のデザイン判断（BGM・ズーム・トランジション）は引き続き CapCut 等の外部ツールに委ねる。** この線引きは変わらない。

継続する制約（NFR、v2 から変更なし）:

- **従量課金 API は使わない。** AI 生成は Codex CLI（ChatGPT サブスクリプション内、`codex login` 済み）のみ。YouTube Data API は無料枠クォータ内で使用する（§6 参照）
- **新しい pip 依存を追加しない。** `google-api-python-client` は v2 で導入済み。ffmpeg / libass の機能拡張で完結させる
- Web UI は **localhost のみ** で動作する

---

## 3. UI 診断（v3 着手前の現状分析）

フェーズ U の設計判断の根拠となる、現状の事実を記録する。

### 3.1 サイドバーの事故

Streamlit は、エントリスクリプト（[`src/yt_live_kit/ui/app.py`](../src/yt_live_kit/ui/app.py)）と同じディレクトリにある `pages/` ディレクトリを自動検出し、その中の各モジュールをマルチページナビゲーションとして表示する仕様を持つ。[`src/yt_live_kit/ui/pages/`](../src/yt_live_kit/ui/pages/) が存在するため、`channel.py` `highlights.py` `history.py` `run.py` `shorts.py` がそのままサイドバーに並んでしまう。これらは `render_*_page()` という **関数を定義しているだけのモジュール** であり、Streamlit の自動ナビゲーションがページとして直接実行するとほぼ白紙の画面になる。

現行の `app.py` は `st.navigation` / `st.Page` を使わず、`st.tabs(["実行", "チャンネル", "処理済み一覧"])` で明示的にページを呼び出している（正しい導線はタブの方）。**サイドバーの事故導線と、意図した導線（タブ）が同一画面に同居している。**

### 3.2 機能軸 UI と動画軸ワークフローの不一致

現行のタブ構成（実行 / チャンネル / 処理済み一覧）は **機能軸**（何を実行するか）で分かれている。しかし実際の作業は 1 本の動画に対して「字幕取得 → チャプター → 切り抜き候補 → ハイライト → ショート → 概要欄反映」と **動画軸** で進む。ユーザーは 1 本の動画を仕上げるために複数タブを行き来する必要があり、「次に何をすべきか」が画面から読み取れない。

### 3.3 チャンネルページの取り込み口・設定の混在

[`ui/pages/channel.py`](../src/yt_live_kit/ui/pages/channel.py) はチャンネル URL の正規化・一覧取得・一括投入という「取り込み」機能と、取得件数の上限選択などの「設定的な操作」が同一画面に混在している。ハンドルは処理のたびに入力する運用になっており、チャンネル既定値として保存されていない。

### 3.4 処理済み一覧の誤操作リスク

[`ui/pages/history.py`](../src/yt_live_kit/ui/pages/history.py) は 47 件をフラット表示し、各行に「チャプター再生成」「切り抜き候補を生成」「元動画を削除」「概要欄に反映」「チャプターを表示」の 5 ボタンを並べている。削除には確認導線があるが `st.dialog` ではなく行内の 2 段階ボタンで、確認テキストも他のボタンに紛れて見落としやすい。「概要欄に反映」は YouTube 上の公開データを書き換える唯一の操作でありながら、見た目上は他のボタンと同じ重みで並んでいる。アーカイブ（活用済みを畳む）の概念も無い。

---

## 4. 新しい情報設計（IA）の要件

現行の 3 タブ構成を、**公開ナビゲーション 3 画面（ライブラリ / 取り込み / 設定）+ ライブラリから選択したときだけ開く非表示の動画詳細 1 画面**の、合計 4 画面構成に置き換える。旧「処理済み一覧」はライブラリと動画詳細へ役割を統合し、フェーズ U 完了時には公開ナビゲーション・ソースコードの双方から削除する。ページ実装は [`docs/execution-plan-v3.md`](./execution-plan-v3.md) のフェーズ U（U0〜U5）を参照。

### FR-16: ライブラリページ（ホーム）

| 項目 | 内容 |
|------|------|
| **入力** | なし（`data/` を走査） |
| **処理** | 全処理済み動画を一覧化する。各行はタイトル + 状態バッジ（チャプター✓ / 候補✓ / ショート n 本）のコンパクト表示。検索（タイトル部分一致）とフィルタ（状態別）を提供する |
| **出力** | 一覧表示。行を選択すると動画詳細ページ（FR-17）へ遷移する |
| **スコープ** | **アーカイブ機能を持つ。** 「活用済み」（ショート生成済み・概要欄反映済み等）とみなせる動画を既定で畳み、必要な行だけを見えるようにする。アーカイブ解除も可能。**アーカイブ状態は `data/_config/archived_videos.json` に永続化し、アプリを再起動しても保持される**（`start.command` で毎回起動し直す運用のため、セッション内のみの一時状態にはしない） |

### FR-17: 動画詳細ページ

| 項目 | 内容 |
|------|------|
| **入力** | 動画 ID（ライブラリページからの選択） |
| **処理** | 1 本の動画に関するすべての操作を、パイプライン順のセクション（字幕 → チャプター → ハイライト候補 → ショート作成 → 概要欄反映）として並べる |
| **出力** | 先頭に「次にやること」ステッパー（例: 字幕 ✓ → チャプター ✓ → 候補 ● → ショート ○ → 概要欄 ○）を表示し、次のステップのボタンだけを大きく目立たせる |
| **スコープ** | 完了済みステップの再実行ボタンは expander に畳んで配置し、誤操作を構造的に防ぐ。削除・再生成等の破壊的操作は `st.dialog` による確認を必須とする |

### FR-18: 共通コピー部品

| 項目 | 内容 |
|------|------|
| **入力** | コピー対象テキスト（全文・タイムライン・概要欄テキスト・ショートのタイトル案 / 説明文 / タグ 等） |
| **処理** | クリップボードへのワンクリックコピーを、単一の共通コンポーネントで提供する |
| **出力** | コピー完了のフィードバック表示 |
| **スコープ** | v2 の [`ui/components/results.py`](../src/yt_live_kit/ui/components/results.py) にある `build_clipboard_copy_html` / `render_copy_button` を土台に、動画詳細ページ・ショート量産の両方から同じ部品を呼ぶ形に統一する（コピーボタンの実装が複数箇所に分岐しないようにする） |

### FR-19: 取り込みページ

| 項目 | 内容 |
|------|------|
| **入力** | 登録済みチャンネルのハンドル（設定に保存済み）、または URL（単本 / 複数行一括、例外ルート） |
| **処理** | 初期状態は「登録済みチャンネルの新着を確認 → 未処理の新着を 1 クリックで処理開始」。チャンネルのハンドルは設定ページ（FR-20）に保存し、取り込みのたびに入力しない |
| **出力** | 新着一覧、選択して一括処理へ投入 |
| **スコープ** | URL 入力（単本 / 複数行一括）は例外ルートとして同ページ内に格下げ配置する（v2 のチャンネルタブの主機能ではなく、補助導線に位置づけを変える）。「チャプターを作る」「切り抜き候補を出す」チェックは新着一括・URL 単本・URL 複数行一括のすべてで実行ボタンと同じカードにまとめ、選択値を実処理に反映する |

### FR-20: 設定ページ

| 項目 | 内容 |
|------|------|
| **入力** | ユーザーによる設定値入力（チャンネル既定ハンドルのみ編集可） |
| **処理** | チャンネル既定値（ハンドル）は画面から編集・保存できるようにする。ffmpeg パス・字幕フォント・`data_dir` は現在の有効値を **表示専用** で確認できるようにし、変更方法（`.env` 経由）を案内する。Codex CLI の稼働状況を確認する。加えて、v2 のストレージ管理（容量集計、全動画の内訳、元動画・中間ファイルの個別削除 / N 日以上前の一括削除）をこの画面へ移す。全動画へ到達できる全件表示、検索またはページングを備え、各動画に元動画容量と個別削除導線を表示する |
| **出力** | チャンネル既定ハンドルの保存、ffmpeg / フォント設定の表示、Codex CLI の稼働確認結果、ストレージ容量サマリーと削除結果 |
| **スコープ** | ffmpeg パス・字幕フォントの UI からの編集・永続化は v3 のスコープに含めない（`config.py` を変更しないという方針のため）。ストレージ削除は元動画と中間ファイルだけを対象とし、チャプター・全文・候補・切り出し済み動画を残す。個別削除ダイアログには動画識別子・対象 1 件・削除対象バイト数（元動画 + 中間）・残る成果物を表示する。一括削除はプレビュー時の対象動画 ID を不変スナップショットとしてダイアログへ渡し、対象件数・総容量・残る成果物を表示する。各ダイアログの確定前は削除せず、確定後だけダイアログへ渡した正確な動画 ID を削除し、確認後の再走査で対象を増やさない。削除失敗は日本語で表示する。フェーズ P で投稿スケジュールポリシー（FR-28）の設定もこのページに追加する |

### FR-21: 概要欄反映の差分プレビュー

| 項目 | 内容 |
|------|------|
| **入力** | 動画 ID、生成済みチャプター |
| **処理** | 動画詳細の「概要欄に反映」を押すと、OAuth 設定・チャプター存在 / 形式を検証し、不正時は日本語で案内してプレビュー・更新を開始しない。検証後に `fetch_video_snippet` で現在値を取得して `merge_chapters_into_description` で反映後を組み立てる。`st.dialog(width="large")` 内に現在の概要欄と反映後の概要欄を別々の読み取り専用表示として並べ、確認ボタンを押した場合だけ `update_video_description` を実行する。確定前のダイアログ再描画では取得済みプレビューを再利用するが、確定時は既存 `update_video_description` 内部の `fetch_video_snippet` を維持し、`services/youtube_api.py` は変更しない |
| **出力** | 反映前後の差分プレビュー、書き込み確認ボタン |
| **スコープ** | 概要欄反映は YouTube 上の公開データを書き換える唯一の操作であるため、他の破壊的操作（削除・再生成）とも異なる、**より強い確認導線**（外側の `type="primary"` ボタン、警告表示、差分を必ず見せる）を要求する。ステッパー CTA と通常の概要欄ボタンは同じフローを使う。更新成功後に限り `description_applied_videos.json` へ動画 ID を記録し、更新失敗時は記録しない。YouTube 更新後にローカル記録だけ失敗した場合は、YouTube 側は更新済みであることを日本語で明示する。v2 の旧「処理済み一覧」にある確認なしの更新経路は廃止し、概要欄更新経路を動画詳細へ一本化する |

---

## 5. ショート量産の機能要件

スコープ: 複数区間のジャンプカット連結 + テロップ + 冒頭 1〜2 秒のフックタイトル（大テロップ）+ メタデータ生成。トランジション（xfade）・ズーム・BGM・SE は §2 のとおりスコープ外。

### FR-22: テロップ台本の生成

| 項目 | 内容 |
|------|------|
| **入力** | 選択済み区間（複数）の自動字幕（VTT 由来）、プロンプトテンプレート。カット単位字幕は開始・終了とミリ秒を保持した `[HH:MM:SS.mmm --> HH:MM:SS.mmm] テキスト` 形式で渡す |
| **処理** | Codex CLI に区間の字幕を渡し、誤認識の修正・句読点付与・短い行への分割・強調行フラグの付与まで済んだ「テロップ台本」を JSON で生成させる。[`services/highlights.py`](../src/yt_live_kit/services/highlights.py) と同じパターン（テンプレート結合 → Codex CLI 実行 → JSON 抽出 → バリデーション → 保存）を踏襲する |
| **出力** | テロップ台本 JSON（区間ごとの行・行全体に対する強調行フラグ） |
| **成功条件** | 生成後、UI 上でテキストを人が確認・微修正できること。**自動字幕の品質が上限を決めるため、確認ステップは省略しない** |

### FR-23: フックタイトル・メタデータ生成

| 項目 | 内容 |
|------|------|
| **入力** | FR-22 と同じ Codex CLI 呼び出し |
| **処理** | テロップ台本の生成と **同じ Codex 呼び出しの中で**、冒頭のフック文言・タイトル案・説明文・タグまで一括生成する（独立した機能ではなく、出力 JSON の項目拡張として実装する） |
| **出力** | フック文言（1 本）、タイトル案、説明文、タグのリスト |
| **スコープ** | Codex 呼び出し回数を増やさない（NFR-01 のコスト制約と、待ち時間の両方に効く） |

**テロップ台本 JSON フォーマット（例）:** 既存の `candidates.json`（切り抜き候補）・`segments.json`（ハイライト区間）と同じく、Codex CLI の出力は次の形式の JSON を想定する。

```json
{
  "hook_text": "実はこのやり方、9割の人が間違えています",
  "title_candidates": ["AI経営で9割が間違える1つのこと", "その使い方、実は逆効果です"],
  "description": "配信内で話した「よくある間違い」をまとめました。",
  "tags": ["AI経営", "Codex", "業務効率化"],
  "segments": [
    {
      "start_sec": 222.0,
      "end_sec": 250.0,
      "lines": [
        { "start_sec": 222.0, "end_sec": 225.0, "text": "実はこのやり方", "emphasis": true },
        { "start_sec": 225.0, "end_sec": 228.0, "text": "9割の人が間違えています", "emphasis": false }
      ]
    }
  ]
}
```

区間と各行の `start_sec` / `end_sec` は、元動画の先頭を 0 秒とする絶対秒で保持する。ジャンプカット連結後の行時刻は、`先行区間の累積尺 + 行の絶対秒 - 対応する元区間の開始秒` で求める。`hook_text`・行本文・タイトル案・`description`・タグは strip 後に非空とし、タイトル案とタグはそれぞれ 1 件以上必要とする。半角の `<` `>` は全生成文字列で使用禁止（他の生成物と同じ出力ルール）。行の区切りは画面に収まる短さ（目安 13〜16 文字）を基準にし、16 文字超は警告として扱う。

### FR-24: テロップスタイルプリセット

| 項目 | 内容 |
|------|------|
| **入力** | プリセット選択、テロップ台本（FR-22） |
| **処理** | [`services/subtitle_burn.py`](../src/yt_live_kit/services/subtitle_burn.py) の ASS スタイル（現状は白 54px の 1 スタイルのみ、`PlayResX/PlayResY` は 1080x1920）を、テロップ風の複数プリセット（太字 + 縁取り + 座布団、強調行の色替え 等）に拡張する。`TelopLine.emphasis` は語単位の範囲ではなく、その行全体を強調する真偽値として扱う |
| **出力** | プリセットを反映した ASS 字幕ファイル |
| **スコープ** | libass の機能内で完結させる。**追加の pip 依存を発生させない** |

強調行の色は選択したプリセットの強調色を使い、行末では同プリセットの本文色へ戻す。通常字幕とフックタイトルのユーザー由来文字列は同じ安全化処理を通し、ASS の override tag・制御列・物理的な `Dialogue` 行を注入できないこと。ジャンプカット連結ショートでも選択した通常字幕 / フックの両プリセットを維持し、ASS 内のスタイルを ffmpeg 側の一括 `force_style` で上書きしない。

### FR-25: ジャンプカット連結ショート生成

| 項目 | 内容 |
|------|------|
| **入力** | 複数の小区間（人が選択、または AI 候補から選択）、テロップ台本、テロップスタイル |
| **処理** | 既存の `encode_segment` + `concat_segments`（[`services/ffmpeg.py`](../src/yt_live_kit/services/ffmpeg.py)）と [`services/shorts.py`](../src/yt_live_kit/services/shorts.py) の縦型レイアウト処理（blur / crop、2 パス方式、`INTERMEDIATE_CRF=16`）を組み合わせ、複数の小区間を 1 本のショートに連結する。区間境界は共通 API で `Decimal(str(value))` + `ROUND_HALF_UP` により整数ミリ秒へ正規化し、以後その値を ID、合計尺、エンコード引数、字幕の累積 offset / clip の唯一の基準とする。公開境界では同じ共通 API により冪等に再検証する。入力順を再生順・ID 生成順・エンコード順として保持し、自動で並べ替え・重複除去しない。確認済みテロップ台本は ffmpeg 実行前と字幕関数の公開境界で入力区間との一致を再検証し、フックタイトルは冒頭 1〜2 秒の大テロップとして同じ ASS に載せる |
| **出力** | 1 本の縦型 mp4（1080x1920）とコマンドログ |
| **スコープ** | ショートは **10〜180 秒**（[`shorts.py`](../src/yt_live_kit/services/shorts.py) の `MIN_DURATION_SEC` / `MAX_DURATION_SEC`）を維持する。ハイライト候補は最大 300 秒（`highlights.py` の `MAX_DURATION_SEC`）なので、**180 秒を超える区間を選んだ場合はエラーで止めず、分割・短縮を促す導線にする** |

連結処理の中間物はクリップ ID ごとの専用ディレクトリへ隔離し、既定では成功・失敗のどちらでも削除する。最終 mp4 は同じ出力ディレクトリ内の一時 `.mp4` へ生成し、非ゼロの生成成功を確認してから正式出力へ原子的に置換する。再生成失敗時は既存の正式 mp4 を維持し、最終 ffmpeg コマンドログは正式出力名に追従する `{正式出力stem}.ffmpeg.log` として出力ディレクトリへ残す。元動画の取得や ffmpeg を始める前に、区間・レイアウト・出力名・フック文言・通常 / Hook プリセットをすべて検証する。

### FR-26: キュー量産 UI

| 項目 | 内容 |
|------|------|
| **入力** | form 外の前段でハイライトまたは切り抜き候補のいずれか一方を選び、変更時は即 rerun する。そのソース文書の候補表示順を選択順として固定し、その後の単一 form で複数選択、個別 / 連結モード、layout、通常 / Hook プリセットを不変 snapshot として確定する |
| **処理** | 個別モードは選択 n 区間を n 本、連結モードは選択 n 区間を入力順の 1 本とする。候補正規化、target 構築、衝突 / 整数 ms 尺検証、fingerprint、失効 / 開始可否、直列化は service の純粋関数が担い、UI は表示と呼び出しだけを行う。Codex は対象ごとの明示的な「台本を生成」/「再試行」submit 時だけ呼び、通常 rerun では draft を再利用する。各台本を人が編集し、再検証・保存に成功した全対象の確定後だけ、[`services/jobs.py`](../src/yt_live_kit/services/jobs.py) の単一ジョブで順次処理する。1 本の失敗は対象単位の softfail として残りを継続する |
| **出力** | manifest は `run_shorts_queue()` だけが所有し、schema version、job ID、UTC timestamp、入力順 item、成功 / 失敗数を開始時と各 item 後に atomic 保存する。`start_job()` の返却 job ID は video ID 別の session state map に保持し、現在動画の ID に対応する manifest だけを表示する。現在動画の key が無い場合だけ、その動画に限定して `(created_at, job_id)` 順の latest へ fallback する。成功カードに動画プレイヤー、遅延読み込みの mp4 保存、タイトル / 説明文 / タグのコピーを表示し、失敗カードに日本語エラーを表示する |
| **スコープ** | 同時実行ジョブは v2 から変更せず 1 件。エンコード中に未確定台本を追加する producer-consumer 型は対象外。fingerprint は video ID、元候補全内容、候補順、正規化全区間、source / mode / layout / 両 preset を含み、変更時は draft / 確定を全て失効させる。既存 mp4 の上書きは不変の対象一覧を表示する確認 dialog の確定時だけ開始し、busy、キャンセル、確定前は開始しない |

`ClipCandidate` は `HighlightSegment` 相当の frozen primitive tuple へ正規化してから FR-22 を呼び、候補型ごとの分岐を S1 へ持ち込まない。台本は Pydantic `model_dump(mode="json")` 由来の canonical JSON として snapshot 化し、`to_dict()` / `from_dict()` で型・区間・台本を再検証する。異なる候補ソースの混在と同一 clip ID / 決定的な `short_{clip_id}.mp4` の衝突は開始前に拒否し、暗黙に sort / dedupe / 自動 suffix を行わない。人が修正した台本は入力区間との一致を再検証し、S1 と同じ `telop_{clip_id}.json` へ atomic 保存した path と正規化済み document の両方を返し、その返却 document だけを FR-25 へ渡す。S1 失敗はその生成対象だけ再試行可能とし、他の draft / 確定と既存成果物を維持する。

`ShortsQueueResult.manifest_path` は読込元を表す実行時情報であり、manifest JSON / `to_dict()` には保存しない。loader が実際に開いた path を `from_dict(..., manifest_path=...)` へ注入し、JSON 内の `manifest_path` は未知 field として拒否する。

---

## 6. 投稿・予約の機能要件

### FR-27: 予約投稿（アップロード）

| 項目 | 内容 |
|------|------|
| **入力** | 生成済みショート mp4、タイトル・説明文・タグ（FR-23）、timezone-aware な公開予定日時 |
| **処理** | YouTube Data API の `videos.insert` を **`privacyStatus=private` 固定**、`publishAt` 指定、`notifySubscribers=false` 固定で呼び出す。ユーザーが毎回必須選択した `status.selfDeclaredMadeForKids` と `status.containsSyntheticMedia` も body に反映する。`public` / `unlisted` / `publishAt` 無しは受け付けない。公開予定日時は現在より最低 10 分先を必須とし、IANA timezone を持つ aware datetime として検証後、UTC の RFC 3339 `Z` 形式へ正規化する。過去またはリードタイム未満の日時を即時公開へフォールバックせず日本語で拒否する。既存 OAuth は `youtube.force-ssl` スコープ（[`services/youtube_api.py`](../src/yt_live_kit/services/youtube_api.py) の `SCOPES`）を使う |
| **出力** | 永続 upload operation（operation ID、動画 ID、公開予定日時、処理状態、job ID、エラー、時刻）とアップロード結果 |
| **スコープ** | 縦 3 分以内の動画は自動的に Shorts 扱いになる（専用 API は無い）。**投稿は外部へ公開される操作のため、概要欄反映（FR-21）と同じ「確認ダイアログ + 内容プレビュー」の作法で統一する。** P0 は lock 非該当なら probe 動画が指定時刻に public となり得ることまで提示して承認を得る。P0 実アップロード、必要な審査フォーム提出、P3 の実予約公開はそれぞれ別のユーザー明示承認を必要とし、承認前は実行しない |

アップロード前の安全契約を次のとおり固定する。

- `channels.list(part="snippet", mine=true)` で取得した実チャンネル ID・名称、対象ファイルの絶対パス、サイズ、`ffprobe` 尺、タイトル、説明文、タグ、予約日時、`notifySubscribers=false`、`selfDeclaredMadeForKids`、`containsSyntheticMedia` を確認ダイアログにすべて表示する。後二者は既定値を推測せず、ユーザーに「はい / いいえ」を毎回必須選択させる
- 確認ダイアログに「YouTube Community Guidelines に準拠する内容であることを確認した」チェックを**既定未チェック**で置く。未チェックでは確定できず、この同意の真偽と確認時刻を content snapshot に記録するが、YouTube status field へは送らない
- タイトルは strip 後に非空かつ 100 文字以下、説明文は UTF-8 で 5000 bytes 以下、タグは各要素を strip 後に非空とし `",".join(tags)` が 500 文字以下、すべて半角山カッコ禁止とする
- 確定ボタン後、API session を作る前に、実チャンネル、ファイル identity（絶対パス・サイズ・更新時刻）、尺、content snapshot、予約枠、当日試行枠を再検証する。プレビューから変化していれば upload を開始せず、新しい確認を要求する
- `MediaFileUpload(..., resumable=True)` と同一 `videos.insert` request の `next_chunk()` を使う。ネットワーク例外と HTTP 500 / 502 / 503 / 504 だけを、同一 resumable session 内で最大 5 回、1・2・4・8・16 秒（上限 16 秒）の bounded exponential backoff で再試行する。4xx、再試行上限到達、応答喪失等で結果が確定できない場合、新しい `videos.insert` を自動実行せず `needs_reconciliation` とする
- `data/_schedule/queue.json` を予約 slot と full operation の単一正本とする。各 record は `reserved` / `uploading` / `uploaded` / `failed` / `needs_reconciliation` の状態、operation ID、元動画 ID、source 種別、clip ID、ファイルパス、`selfDeclaredMadeForKids` / `containsSyntheticMedia` / Community Guidelines 同意を含む確認済み content snapshot、job ID、YouTube video ID、作成・更新・開始・完了時刻、エラー、入力順の `poll_history` を保持する。各 `UploadStatusObservation` は UTC 時刻、processing / publication phase、取得 status / processingDetails、判定、日本語エラーを持ち、最新値で過去履歴を上書きしない。別の operation JSON と二重管理せず、lock 下の一時ファイル + replace で原子的に更新し、壊れた JSON は空扱いにせず fail closed で停止する
- upload 完了後は `videos.list(part="status,processingDetails")` を bounded poll して processing 状態と公開前後の status を operation に記録する。processing は 10 秒間隔・最大 30 回で `succeeded` を成功、`failed/terminated` を失敗、上限到達を timeout とする。公開は予約時刻後に 30 秒間隔・最大 20 回で `privacyStatus=public` を成功、processing failure を失敗、private のまま上限到達を timeout とする。sleep / clock はテストで注入可能にする
- private lock 判定は、予約時刻前の private + 期待 `publishAt` を正常 scheduled、processing 成功後の `publishAt` 欠落または予約時刻 + 5 分後も private を `suspected_private_lock`、public を `published` とする。API だけで確定せず P0 の YouTube Studio 確認で `confirmed_private_lock / no_private_lock` を記録し、`uploaded` と予約公開可否を分ける。結果不明時は operation ID / channel ID / file snapshot から手動照合できる案内を出す

### FR-28: 投稿スケジュールポリシー

| 項目 | 内容 |
|------|------|
| **入力** | `daily_time`（厳密な `HH:MM`）、`interval_days >= 1`、IANA timezone（既定 `Asia/Tokyo`）からなるスケジュールポリシー、量産済みショートのキュー、timezone-aware な現在時刻 |
| **処理** | 設定ページ（FR-20）にポリシーを保存し、「次の空き枠に自動割り当て」を提供する。計算は `zoneinfo.ZoneInfo` で行い、DST を含むローカル予約枠を aware datetime として扱う。YouTube API へ渡すときだけ UTC RFC 3339 `Z` へ変換する。予約枠と full operation は `data/_schedule/queue.json` の同一 record、upload attempt は `data/_schedule/upload_attempts.json` で別管理する |
| **出力** | 割り当て結果と、再起動後にも復元できる永続 upload operation |
| **スコープ** | 予約枠の件数と upload attempt の日次上限を混同しない。upload attempt は公開日ではなく、**実際に API session を開始しようとした日の `America/Los_Angeles` 暦日**で数える。`YTLK_VIDEO_UPLOAD_DAILY_LIMIT` は 1〜100、既定 100 とし、失敗・結果不明も 1 試行に含める |

予約確定と upload attempt の競合制御を次のとおり固定する。

- プレビュー時に slot / quota snapshot を作り、確定後に lock を取り直して予約枠の空き、operation の重複、America/Los_Angeles 当日試行数を再検証する
- `MediaFileUpload` / `videos.insert` の resumable upload session 前に `upload_attempts.json` へ試行を atomic 記録する。事前の read-only `channels.list` は attempt に数えない。記録に失敗した場合は upload を開始しない。上限到達後は同日中の実 upload を拒否し、予約公開日をずらすことで試行枠を空いたことにはしない
- 同一 `job_id` / `operation_id` / content snapshot の再送は冪等に扱い、二重クリック、ジョブ再実行、アプリ再起動で新しい operation や `videos.insert` を重複作成しない。`needs_reconciliation` は自動再送せず、人の照合後に明示的な新 operation として再試行する
- confirm 時に operation ID と job ID を先行生成して単一 queue record へ atomic 保存し、jobs は同じ requested job ID の JSON を worker thread 起動前に作る。起動時は `jobs.close_orphans()` の後に upload recovery を行い、attempt ledger を外部効果開始の正本とする。active state（`reserved/uploading`）だけを recovery 遷移対象とし、attempt 無しは `failed` + slot 解放として新しい preview / 承認を要求し、attempt 有りまたは ledger が壊れて有無を確定できない場合は `needs_reconciliation` + slot 保持として自動再送しない。terminal state（`uploaded/failed/needs_reconciliation`）は変更せず、ledger 不整合時は queue / slot 非変更のまま日本語エラーで全新規 upload を fail closed にして手動修復を要求する

**P0（テストアップロード検証）について:** P0 を危険な最小経路にはしない。FR-27 / FR-28 の安全契約、永続 operation、冪等性、試行上限、resumable upload、reconciliation、polling を P1 / P2 でモック実装・自動テストした後に、その同じ本番経路で実 upload を 1 本だけ行う。ロックされる場合は Google のコンプライアンス審査を別承認で申請し、**P1 / P2 の開発とテストは実操作の承認・審査待ちで止めない**。この検証は [`docs/execution-plan-v3.md`](./execution-plan-v3.md) の P0 タスクとして扱う。

---

## 7. 非機能要件（v3 追加分）

### NFR-11: コスト制約の継続

- v1 の NFR-01 を継続する。**従量課金 API は使用しない。** AI 連携は Codex CLI（ChatGPT サブスクリプション内、`codex login` 済み）のみとする
- **新しい pip 依存パッケージを追加しない。** `google-api-python-client` / `google-auth-oauthlib` は v2 で導入済みであり、v3 はこれと yt-dlp / ffmpeg / Streamlit / pydantic / typer で完結させる

### NFR-12: YouTube Data API クォータ制約

**公式仕様の確認日:** 2026-08-01（[Quota Calculator](https://developers.google.com/youtube/v3/determine_quota_cost) / [Videos: insert](https://developers.google.com/youtube/v3/docs/videos/insert)）

- 2026-06-01 以降、`videos.insert` は共通 10,000 ユニット枠ではなく **Video Uploads 専用クォータバケット**で管理される
- `videos.insert`（アップロード）は **1 回 = 1、既定 100 回/日**。上限は `YTLK_VIDEO_UPLOAD_DAILY_LIMIT`（整数 1〜100、既定 100）でより小さく設定できる
- 日次判定は公開予定日ではなく upload attempt を開始する `America/Los_Angeles` の暦日で行う。resumable upload session 前に lock + atomic write で記録し、成功・失敗・結果不明をすべて数える。read-only API は数えず、予約枠件数とは別集計にする
- Google Cloud Console でプロジェクト固有の上限が 100 未満の場合は、その値を環境変数へ設定する。UI からの上限変更は持たない
- `videos.update`（概要欄反映）は **50 ユニット/回**
- `videos.update` 等のその他エンドポイントは、従来どおり共通 **1 日 10,000 ユニット**枠で管理される。Video Uploads 専用バケットとは別に扱う
- 必要になった場合は Google Cloud Console でクォータ増枠を申請する。FR-28 の自動割り当ては上限を超えないこと

**クォータ試算（参考）:**

| 操作 | ユニット/回 | 1 日の無料枠内での上限 |
|------|-------------|--------------------------|
| `videos.insert`（ショートのアップロード） | Video Uploads 専用枠で 1 | 既定 100 本 |
| `videos.update`（概要欄反映） | 共通枠で 50 | 共通 10,000 ユニット枠なら 200 回 |
| `videos.list`（概要欄の現在値取得） | 1 | 実質無制限 |

`videos.insert` と `videos.update` は別バケットで数える。P2 は Video Uploads 専用枠の 100 回/日を上限とし、概要欄反映の共通枠とは合算しない。

### NFR-13: 破壊的操作の確認導線

- 削除（元動画・成果物）、再生成（既存成果物の上書き）、概要欄への反映、YouTube への投稿は、**すべて確認ステップを経てから実行する。** 特に概要欄反映と投稿は公開データを書き換える操作であるため、差分プレビュー（FR-21）または投稿内容プレビューを必須とする
- 確認 UI には `st.dialog` を用いることを既定とする（v2 の行内 2 段階ボタン方式より視覚的に独立させる）
- 投稿確認は `channels.list(mine=true)` で得た実チャンネル ID / 名称、ファイル、サイズ / 尺、全 metadata、UTC 変換前後の予約日時、`notifySubscribers=false`、Made for Kids / synthetic media の必須選択を表示し、Community Guidelines 同意は既定未チェックとする。確定後にも同じ snapshot と slot / attempt 上限を再検証する

### NFR-14: 素材の尺制約の継続

- ショート動画は **10〜180 秒**（v2 から変更なし）
- ハイライト・ショート候補の元となる区間は **最大 300 秒**（v2 から変更なし）
- 180 秒を超える区間の選択はエラーで拒否するのではなく、分割・短縮を促す日本語の案内を表示する（FR-25）

### NFR-15: ローカル実行の継続

- Web UI は **localhost のみ** で動作する（v1 の NFR-10 を継続）。投稿機能を追加しても、YouTube への通信は Data API 経由のみで、Web UI 自体を外部公開することはない

---

## 8. 受け入れ条件（AC）

### AC-18: ライブラリページ

- [x] サイドバーに内部モジュール名（`app` `channel` `highlights` `history` `run` `shorts` 等）が表示されない
- [x] 公開ナビゲーションは「ライブラリ」「取り込み」「設定」の 3 画面だけで、動画詳細は非表示ページとしてライブラリから遷移し、旧「処理済み一覧」は表示されない
- [x] 47 件の処理済み動画が状態バッジ（チャプター✓ / 候補✓ / ショート n 本）付きで一覧表示される
- [x] タイトルの部分一致検索で絞り込める
- [x] 「活用済み」動画を畳んで非表示にでき、再表示もできる
- [x] アプリを再起動してもアーカイブ状態が保持される
- [x] 行を選択すると動画詳細ページに遷移する

### AC-19: 動画詳細ページ

- [x] 画面上部のステッパーが、その動画の進捗（字幕 / チャプター / 候補 / ショート / 概要欄）を正しく表示する
- [x] 次にやるべきステップのボタンが、他より視覚的に目立つ
- [x] 完了済みステップの再実行ボタンは expander に畳まれており、うっかり押せない
- [x] 削除・再生成等の破壊的操作を実行すると `st.dialog` の確認が挟まる

### AC-20: 取り込みページ

- [x] 登録済みチャンネルのハンドルを毎回入力しなくてよい（設定ページに保存済みの値が使われる）
- [x] 「新着を確認」→「未処理の新着を選択して処理開始」が 1 ページ内で完結する
- [x] URL 入力（単本 / 複数行一括）は例外ルートとして同じページ内から使える
- [x] 新着一括・URL 単本・URL 複数行一括の全 3 ルートで、「チャプターを作る」「切り抜き候補を出す」の選択が実処理に反映される
- [x] 両方を未選択にすると日本語の案内が表示され、すべてのルートで処理を開始できない

### AC-21: 設定ページ

- [x] チャンネルの既定ハンドルを画面から設定・保存できる
- [x] ffmpeg パス・字幕フォントの現在の有効値と、変更方法（`.env` 経由）が画面上で確認できる
- [x] Codex CLI の稼働状況が画面上で確認できる
- [x] ストレージ容量と全動画の内訳を確認でき、10 件を超えても 11 件目以降へ全件表示・検索またはページングで到達できる。各動画に元動画容量と個別削除導線がある
- [x] 個別削除では動画識別子・対象 1 件・削除対象バイト数・残る成果物、一括削除では対象件数・総容量・残る成果物を確認ダイアログに表示し、確定前は削除されず、確定後だけ表示した正確な対象が削除される。失敗時は日本語エラーが表示される
- [x] ストレージ削除後も、チャプター・全文・候補・切り出し済み動画が残る

### AC-22: 概要欄反映の差分プレビュー

- [x] 「概要欄に反映」を押すと、反映前と反映後の内容を対比できる形で表示される
- [x] 反映ボタンは他のボタンと視覚的に区別された色・強調で表示される
- [x] 反映を確定するまで YouTube 側は書き換わらない
- [x] 更新成功後だけ概要欄ステップが完了として記録され、更新失敗時は記録されない
- [x] 旧「処理済み一覧」を含め、差分確認を迂回して概要欄を更新できる経路が残っていない

### AC-23: テロップ台本・メタデータ生成

- [x] 選択した複数区間の字幕から、誤字修正・分割済みのテロップ台本が JSON で生成される
- [x] 同じ生成結果に、フック文言・タイトル案・説明文・タグが含まれる
- [x] 生成後、UI 上でテロップ本文をテキストとして修正できる
- [x] 修正内容が焼き込み時に反映される

### AC-24: テロップスタイル・フックタイトル

- [x] 複数のテロップスタイルプリセットから選んで焼き込める
- [x] 冒頭 1〜2 秒にフックタイトルが大きく表示される
- [x] 日本語が豆腐にならず、縁取り・座布団等の装飾が正しく表示される

### AC-25: ジャンプカット連結ショート生成

- [x] 候補 3 件を選んでまとめて生成すると、1 本の mp4（複数区間が連結されたショート）とメタデータが揃う
- [x] 出力が 1080x1920 で、10〜180 秒の範囲に収まっている
- [x] 180 秒を超える区間を選んだ場合、エラーで落ちるのではなく分割・短縮の案内が出る
- [x] 区間のつなぎ目でフリーズ・音ズレが発生しない

### AC-26: キュー量産

- [x] 候補 3 件を選んでまとめて生成すると、3 本の mp4 とメタデータ（タイトル・説明文・タグ）が揃う
- [x] 個別モードでは候補 3 件が 3 本、連結モードでは同じ 3 区間が入力順の 1 本になり、候補ソース / 出力衝突を暗黙に混在・重複除去しない
- [x] 生成前に全対象のテロップ台本を 1 本ずつ修正・確定でき、選択 / 順序 / モード / layout / preset 変更時は確定が失効し、全確定前はジョブが始まらない
- [x] 候補ソース変更は form 外で即 rerun し、選択順はソース文書の候補表示順になる。台本生成は明示 submit 時だけ 1 回行われ、通常 rerun で Codex を再実行しない
- [x] 180,000 ms 超の選択は Codex / ffmpeg 前に分割・削減・短縮の案内が出て、キューに積めない
- [x] fingerprint が video ID、元候補全内容、表示順、正規化全区間、source / mode / layout / 両 preset を含み、いずれかの変更で draft / 確定が失効する
- [x] 確定済み spec は frozen primitive tuple と canonical 台本 JSON で deep immutable になり、`to_dict()` / `from_dict()` が改変・不正値を再検証する。出力名は `short_{clip_id}.mp4` で決定的かつ衝突時に拒否される
- [x] 1 本が失敗しても残りが順次処理され、全件失敗時を含め対象単位の日本語エラーが表示される
- [x] 既存 mp4 は確認 dialog の確定後だけ上書き対象となり、busy、キャンセル、確定前は二重開始しない
- [x] 完成したショートが manifest から入力順のグリッドに再構築され、UI 再起動後も動画再生・mp4 保存・タイトル / 説明文 / タグのコピーができる
- [x] manifest は `run_shorts_queue()` だけが schema version / UTC timestamp / item path / Pydantic JSON を検証して開始時・各 item 後に atomic 更新する。`manifest_path` は JSON に保存せず loader が読込元から注入する。通常時は現在 video ID 用の返却 job ID だけ、その動画の key が無い場合だけ当該動画の `(created_at, job_id)` tie-break latest を表示し、新 manifest 作成前に旧結果を表示しない
- [x] 動画 A の job ID を session state に保持したまま動画 B へ切り替えても A の結果を表示せず B の latest へ fallback し、A へ戻ると A の保持 job ID の結果を再表示する

### AC-27: 予約投稿

- [x] 予約投稿した動画が指定時刻に公開される
- [x] upload は `privacyStatus=private`、未来 10 分以上の aware `publishAt`、UTC RFC 3339 `Z`、`notifySubscribers=false` 以外を拒否し、過去時刻を即時公開へ変換しない
- [x] 投稿前に確認ダイアログで実チャンネル ID / 名称、対象ファイル、サイズ / 尺、タイトル、説明文、タグ、予約日時、`notifySubscribers=false`、Made for Kids / synthetic media の選択、Community Guidelines 同意を確認でき、確定後の再検証で変更・枠競合を検出すると upload が始まらない
- [x] metadata のタイトル非空・100 文字、説明文 UTF-8 5000 bytes、タグ合計 500 文字、半角山カッコ禁止の境界が自動テストされている
- [x] `selfDeclaredMadeForKids` と `containsSyntheticMedia` は未選択を拒否し、ユーザー選択値が preview / snapshot / `videos.insert.status` で一致する。Community Guidelines 同意は既定未チェックで、未同意では upload を開始できない
- [x] resumable upload は同一 session の `next_chunk()` だけを限定再試行し、4xx / 結果不明時に新規 `videos.insert` を自動再実行せず `needs_reconciliation` を永続化する
- [x] upload operation が全必須 field と状態遷移を atomic / lock 付きで保持し、壊れた JSON は fail closed、同一 job / operation の重複実行と再起動復元が自動テストされている
- [x] upload attempt は America/Los_Angeles の実試行日で resumable upload session 前に記録され、失敗も数え、`YTLK_VIDEO_UPLOAD_DAILY_LIMIT` の 1・上限・上限超過境界を超えない。read-only API と予約枠件数は別に扱われる
- [x] upload job target が `job_id` を受け、`YouTubeAPIError` が jobs の既知例外として日本語表示され、status bar が upload / shorts queue 等の非 pipeline 完了結果を誤って pipeline として読まない
- [x] `videos.list(part="status,processingDetails")` が processing 10 秒 × 30、公開 30 秒 × 20 の terminal / timeout 契約と fake clock でテストされ、判定表により private lock を予約投稿成功として扱わない
- [x] P0 のテストアップロードで非公開ロックの有無が確認され、記録されている
- [x] P0 の専用承認に、private lock 非該当時は probe 動画が指定時刻に public となり得ることまで含まれている

### AC-28: v3 総合受け入れ

- [x] AC-18〜AC-27 がすべて満たされている（未達は明示的に「次イテレーション」へ移す）
- [x] v1 / v2 の機能に回帰が無い（`uv run pytest` が全件通過する）
- [x] 実配信 1 本から、チャプター生成 → ショート複数本の生成 → 予約投稿までを通しで実行できる
- [x] 実 upload、審査フォーム提出、P3 の実予約公開について、各操作ごとの対象と内容を提示した別々の明示承認記録がある

---

## 用語集（v3 追加分）

| 用語 | 説明 |
|------|------|
| ステッパー | 動画詳細ページ上部の、パイプライン進捗を示す UI（字幕 → チャプター → 候補 → ショート → 概要欄） |
| テロップ台本 | Codex CLI が自動字幕を基に誤字修正・分割・行全体に対する強調行フラグの付与まで行った、焼き込み前のテキスト案 |
| フックタイトル | ショート冒頭 1〜2 秒に表示する、視聴継続を促す大きなテロップ |
| キュー量産 | 複数のショート候補を選択し、まとめてバックグラウンド生成する操作（内部では単一ジョブが順次処理する） |
| スケジュールポリシー | 「毎日 18:00 に 1 本」のような、予約投稿の自動割り当てルール |
| 非公開ロック | 未審査の YouTube API プロジェクトからのアップロードが自動的に非公開のまま固定される仕様。審査完了まで `publishAt` による予約公開が機能しない可能性がある |

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-01 | P 安全監査の独立レビューを反映。P0 probe の公開可能性、単一 queue record、job ID 先行予約と crash recovery、poll 上限・terminal・private lock 判定表、P1 → P2 → P0 → P3 の依存順を明確化 |
| 2026-08-01 | S4 計画レビューを反映。候補ソースの form 外切替、表示順、明示 Codex submit、deep immutable spec、完全 fingerprint、manifest 単独所有、job ID 表示境界、決定的出力名を FR-26 / AC-26 に固定 |
| 2026-08-01 | P0〜P3 着手前安全監査を反映。private 固定、aware `publishAt`、metadata 制約、実チャンネル確認、Made for Kids / synthetic media 必須選択、Community Guidelines 同意、resumable / reconciliation、America/Los_Angeles 基準の試行上限、永続 operation、確認後再検証、polling、実操作ごとの個別承認を FR-27 / FR-28 / NFR-12 / NFR-13 / AC-27 / AC-28 に固定。一般 uploader ではなく scheduled-only feature として即時 public / unlisted を引き続き対象外とした |
| 2026-08-01 | S4 着手前監査を反映。不変選択 snapshot、台本確定と atomic 保存、単一ジョブの対象単位 softfail、job ID 付き manifest、上書き確認、再起動可能な結果表示を FR-26 / AC-26 に固定 |
| 2026-08-01 | S3 着手前監査を反映。整数ミリ秒の単一正規化基準、入力順、二重境界検証、ffmpeg 前の全入力検証、クリップ ID 単位の cleanup、atomic replace、再生成失敗時の既存 mp4 保護を FR-25 に追加 |
| 2026-08-01 | S2 着手前監査・計画レビューを反映。`emphasis` を行全体の強調行フラグとして統一し、プリセット色、ASS 文字列安全化、S3 へのスタイル伝播契約を明確化 |
| 2026-08-01 | S1 着手前監査を反映。FR-22 の JSON 例を元動画基準の絶対秒 `start_sec` / `end_sec` に統一し、連結後タイムラインへの変換式を明記 |
| 2026-08-01 | U5 着手前監査を反映。正式 IA を公開 3 画面 + 非表示詳細に固定し、ストレージ管理の設定画面移設、旧概要欄更新経路の廃止、update / mark の成功境界を明確化 |
| 2026-08-01 | 2026-06-01 からの YouTube Data API granular quota を反映。`videos.insert` を Video Uploads 専用バケット 1 回 = 1、既定 100 回/日に更新。キュー量産は全台本の事前確認後に開始する v3 境界を明記 |
| 2026-08-01 | v3 初版。FR-16〜FR-28、NFR-11〜NFR-15、AC-18〜AC-28 を追加。v2 からのスコープ改訂（ジャンプカット・テロップ・自動投稿の解禁）を明記 |
