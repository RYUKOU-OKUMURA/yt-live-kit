# yt-live-kit 要件定義書 v3

**バージョン:** v3（ショート量産・投稿）
**最終更新:** 2026-08-03
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

### 1.3.1 運用目標（v3.2 追記・2026-08-01）

実運用開始後のすり合わせで、v3 の目的「安定量産」の解釈が「一度に大量生成するバッチ機構」へ寄っていたことが判明した。正しい目的を次のとおり固定する。

> **毎日 3 本のショート動画を、人が品質確認したうえで、安定して予約投稿し続ける。**

- 価値は生成ボリュームではなく**ケイデンス（毎日のリズム）× 品質**である。品質はその日の注意力ではなく**工程のゲート**が保証する（FR-33）
- 優先順位: **① ショート動画の生産フローの確立（最優先）** ② 横型ハイライト動画は派生（生産ラインが確立すれば同じ型で低コストに実現できる）③ 概要欄へのチャプター反映は実装済み・機能済みであり、今後のライブ配信後に漏れなく反映されれば十分（**保守のみ。再設計の対象にしない**）
- まとめて生成（FR-26）は「確定済みの複数本のエンコードを裏で流す実行エンジン」と位置づけ直す。UI の主導線は 1 本を仕上げる工程（FR-33）とする
- 字幕精度の底上げも、全本を先に再文字起こしする運用ではなく、FR-33 で選んだ親候補の必要区間だけを 1 ジョブで処理する。粗い候補探索は既存の YouTube VTT を使い、品質確認の工程で同じ精査済み字幕を再利用する

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

旧 `ui/pages/channel.py` はチャンネル URL の正規化・一覧取得・一括投入という「取り込み」機能と、取得件数の上限選択などの「設定的な操作」が同一画面に混在していた。ハンドルは処理のたびに入力する運用になっており、チャンネル既定値として保存されていなかった。

### 3.4 処理済み一覧の誤操作リスク

旧 `ui/pages/history.py` は 47 件をフラット表示し、各行に「チャプター再生成」「切り抜き候補を生成」「元動画を削除」「概要欄に反映」「チャプターを表示」の 5 ボタンを並べていた。削除には確認導線があるが `st.dialog` ではなく行内の 2 段階ボタンで、確認テキストも他のボタンに紛れて見落としやすかった。「概要欄に反映」は YouTube 上の公開データを書き換える唯一の操作でありながら、見た目上は他のボタンと同じ重みで並んでいた。アーカイブ（活用済みを畳む）の概念も無かった。

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

**v3.1 改訂（2026-08-01）:** U2 で実装した「パイプライン順の全セクション縦積み + 5 段ステッパー」構成は、実運用の UX 監査（[`.codex/audits/video-detail-ux/audit.md`](../.codex/audits/video-detail-ux/audit.md)）で「1 画面に全成果物と全操作を縦積みし、その中を `expander` で入れ子にしているため、CTA の結果が見えず、目的の作業まで 2,400px 超のスクロールが必要」と診断された。v3.1 では本 FR を「実際に行う仕事を選んで進めるページ」へ改訂する。旧構成の記述は AC-19（履歴）に残る。

**v3.2 確定（2026-08-01）:** 3 ワークスペースは仕事の種類、FR-33 の 6 工程は作成中のショート 1 本の状態として併存させる。フル工程表示はショート作成ワークスペース内だけに置き、左パネルには作成中ショートの縮約状態を常設する。

| 項目 | 内容 |
|------|------|
| **入力** | 動画 ID（ライブラリページからの選択） |
| **処理** | 画面を上から次の 5 領域で構成する。(1) 動画ヘッダー（タイトル・動画 ID・実行中ジョブ状態・エラー要約。単なる「形式: ショート」の表示は置かない）、(2) 状態サマリー（素材候補数 / ショート生成ファイル数と予約可能数 / 概要欄反映状態の 3 カード。読み取り専用でクリック不可）、(3) 作業切り替え（`st.segmented_control`。選択肢は「素材候補」「ショート作成」「公開・投稿」の 3 つ）、(4) 選択中ワークスペース（選択した 1 作業だけを描画）、(5) 詳細・再生成（最下部の `expander` 1 つ。字幕全文・チャプター本文・コピー・再生成・元動画管理を集約） |
| **出力** | 初期表示の 1 画面内で、動画タイトル・素材候補数・ショート本数・概要欄反映状態が分かる。字幕全文とチャプター本文は初期表示で描画しない |
| **スコープ** | 5 段ステッパーは廃止する。チャプターは独立した作業ステップではなく **概要欄反映の入力データ** として扱い、通常時は本文を見せず状態（生成済み・形式 OK・件数）だけを公開・投稿ワークスペースに表示する。作業切り替えはワークスペースを開くだけで副作用を持たず、ジョブ開始・YouTube 更新等の実処理は各ワークスペース内の明示ボタンからのみ開始する。削除・再生成等の破壊的操作は引き続き `st.dialog` による確認を必須とする |

**ワークスペースの構成:**

| ワークスペース | 内容 |
|----------------|------|
| 素材候補 | 切り抜き候補とハイライト候補を「ショート素材」として確認する。両方ある場合のみソース切り替えを表示する。候補の生成・再生成は空状態または末尾の補助操作とする。ハイライトまとめ動画（16:9 連結）の生成導線も本ワークスペース内に補助として維持する |
| ショート作成 | 主導線は FR-33 の「素材選定 → 区間決定 → テロップ確認 → 生成 → 最終確認 → 予約」に一本化する。FR-26 は確定済み対象を裏で順次エンコードする実行エンジンとして再利用する。開始・終了時刻の手入力による単体作成（v2 経路）は補助領域に残し、外側の「縦型ショート動画」expander は廃止する |
| 公開・投稿 | 元動画の概要欄反映（FR-21）と、ショートの予約投稿（FR-27）を別カードで集約する。予約投稿には「予約可能」（最新の検証済み量産 manifest の成功 item で出力ファイルが存在するもの）だけを表示する。「生成ファイル」（`shorts/output` の全 mp4。単体手動生成を含む）と「予約可能」は別指標として扱い、単体手動生成だけがある場合は予約投稿フォームを出さずショート生産ラインへの導線を出す |

**初期選択の規則（上から順に評価）:**

1. 字幕成果物（`PipelineResult`）を読み込めない場合はワークスペースを描画せず、回復用の空状態（原因説明 + 「取り込みで再処理」CTA。押してもジョブは開始しない）を表示する
2. 当該動画のジョブが進行中なら、そのジョブに対応するワークスペース
3. 候補が 0 件なら「素材候補」
4. 候補があり予約可能ショートが 0 本なら「ショート作成」
5. 予約可能ショートが 1 本以上なら「公開・投稿」
6. ユーザーが同じ動画で切り替えた後は、最後に選んだワークスペースを維持する（選択状態の key には動画 ID を含め、動画 A / B の状態を混ぜない）

**左パネルとワークスペースの責務（v3.2）:** 左パネル上部はグローバルナビゲーション、下部は「作成中のショート」の常設コンテキストとする。ここには縦型プレビュー、対象名、`工程 3／6 テロップ確認` のような縮約状態、次に行う確認、本日のライン完了数を表示するが、編集・確定・生成・投稿の操作ボタンは置かない。プレビューは工程に応じて、生成前は選択中の元動画区間、生成中は進捗またはサムネイル、生成後は完成 mp4、元素材削除時は再取得案内へ切り替える。サイドバーを折り畳んだ狭幅表示では、メイン上部に縮約工程状態を残す。手動のワークスペース切り替えは工程状態を破棄せず、副作用を起こさない。工程 6 の CTA だけが対象ショートを保持したまま公開・投稿ワークスペースへ明示的に移動する。

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
| **スコープ** | **v3.2 追記:** ショート生産ラインの既定値（レイアウト blur / crop、通常・Hook テロッププリセット）を設定ページで編集できるようにする（保存先は `data/_config/` の UI 層軽量ファイル。FR-33 の「毎回選ばせない」の正本）。投稿スケジュールポリシーの `daily_times` リスト編集（FR-28 v3.2）もこのページに置く。— ffmpeg パス・字幕フォントの UI からの編集・永続化は v3 のスコープに含めない（`config.py` を変更しないという方針のため）。ストレージ削除は元動画と中間ファイルだけを対象とし、チャプター・全文・候補・切り出し済み動画を残す。個別削除ダイアログには動画識別子・対象 1 件・削除対象バイト数（元動画 + 中間）・残る成果物を表示する。一括削除はプレビュー時の対象動画 ID を不変スナップショットとしてダイアログへ渡し、対象件数・総容量・残る成果物を表示する。各ダイアログの確定前は削除せず、確定後だけダイアログへ渡した正確な動画 ID を削除し、確認後の再走査で対象を増やさない。削除失敗は日本語で表示する。フェーズ P で投稿スケジュールポリシー（FR-28）の設定もこのページに追加する |

### FR-21: 概要欄反映の差分プレビュー

| 項目 | 内容 |
|------|------|
| **入力** | 動画 ID、生成済みチャプター |
| **処理** | 動画詳細の「概要欄に反映」を押すと、OAuth 設定・チャプター存在 / 形式を検証し、不正時は日本語で案内してプレビュー・更新を開始しない。検証後に `fetch_video_snippet` で現在値を取得して `merge_chapters_into_description` で反映後を組み立てる。`st.dialog(width="large")` 内に現在の概要欄と反映後の概要欄を別々の読み取り専用表示として並べ、確認ボタンを押した場合だけ `update_video_description` を実行する。確定前のダイアログ再描画では取得済みプレビューを再利用するが、確定時は既存 `update_video_description` 内部の `fetch_video_snippet` を維持し、`services/youtube_api.py` は変更しない |
| **出力** | 反映前後の差分プレビュー、書き込み確認ボタン |
| **スコープ** | 概要欄反映は YouTube 上の公開データを書き換える唯一の操作であるため、他の破壊的操作（削除・再生成）とも異なる、**より強い確認導線**（外側の `type="primary"` ボタン、警告表示、差分を必ず見せる）を要求する。入口は公開・投稿ワークスペース（FR-17 v3.1）の「概要欄に反映」ボタンに一本化する。更新成功後に限り反映記録を保存し、更新失敗時は記録しない。YouTube 更新後にローカル記録だけ失敗した場合は、YouTube 側は更新済みであることを日本語で明示する。v2 の旧「処理済み一覧」にある確認なしの更新経路は廃止し、概要欄更新経路を動画詳細へ一本化する |

**チャプター状態の表示と反映可否（v3.1 追加、FR-17 の公開・投稿ワークスペースに表示）:**

| チャプター状態 | 表示と動作 |
|------------------|------------|
| 未生成 | 「チャプターがありません」を表示し、概要欄反映を無効化。「生成する」を表示 |
| 形式エラー | エラー要約を表示し、概要欄反映を無効化。「再生成」または詳細確認を表示 |
| 生成済み・正常 | 「形式 OK・n 件」のように表示し、概要欄反映を有効化。**本文は表示しない**（本文・コピーは詳細・再生成領域） |
| 最新のチャプターを反映済み | 完了状態を表示し、再反映は secondary 操作に格下げ |
| 反映後にチャプターが変更された | 「チャプター更新あり・要再反映」を表示し、反映 CTA を再び primary にする |
| 旧形式の反映履歴だけがある | 「過去に反映済み・最新性不明」を表示し、現在内容のプレビューを案内 |

**反映記録の fingerprint 化（v3.1 追加 → v3.2 で保留）:** チャプター反映は運用目標の優先度③（§1.3.1: 保守のみ）のため、本段落の fingerprint 化は **v4 候補として保留**する。当面は既存の ID 配列に基づく「反映済み / 未反映」の 2 状態表示を正とする。以下は保留時点の設計記録。— 現行の `description_applied_videos.json` は動画 ID の配列だけであり、チャプター再生成後も「反映済み」に見える。新規の成功記録では、動画 ID ごとに正規化済みチャプター本文の SHA-256 fingerprint と反映日時（UTC）を保存する形式へ移行する。既存配列は読み込み互換を維持し、該当動画は「過去に反映済み・最新性不明」として扱う。現在のチャプター fingerprint と保存済み fingerprint が一致する場合だけ「最新のチャプターを反映済み」と表示する。fingerprint の計算と保存は UI 固有の軽量状態として `ui/views/_local_settings.py` に置き、`services/youtube_api.py` の安全契約（`validate_chapters()` → `fetch_video_snippet()` → 差分プレビュー → 確定時のみ `update_video_description()`）は変更しない。なお fingerprint 化の完了までは、既存の ID 配列に基づく「反映済み / 未反映」の 2 状態表示を許容する（実装順序は [`docs/execution-plan-v3.md`](./execution-plan-v3.md) の U6 / U7 参照）。

### FR-31: 素材候補からショート作成への選択引き継ぎ（v3.1 追加要件）

| 項目 | 内容 |
|------|------|
| **入力** | 素材候補ワークスペース（FR-17 v3.2）で選択した候補（切り抜き候補またはハイライト候補） |
| **処理** | 候補カードからそのままショート作成対象へ追加できるようにし、ショート作成ワークスペースを開いたときに該当候補を事前選択する。候補を工程へ乗せる前の引き継ぎ状態は動画 ID 別の session state で持ち、候補ソース・選択候補 ID の配列・候補ファイル全体の fingerprint・選択順を保持する。ユーザーが区間列を確定して FR-33 のラインを開始した後は、再起動に耐えるライン状態へ移す |
| **出力** | ショート作成の未確定フォームで同じ候補が同じ順序のまま選択済みになり、ライン開始後は同じ工程・対象・確認状態を安全に復元できる |
| **スコープ** | ライン開始前の引き継ぎ状態はジョブ仕様や確定済み台本として扱わない。候補ファイルの fingerprint が変わった場合・候補が再生成された場合・選択 ID が現在の候補に存在しない場合は、引き継ぎ選択と未確定 snapshot を破棄して日本語で再選択を案内する。ライン開始後の状態は FR-33 の `line_{clip_id}.json` を正本とし、証明できない人確認を復元しない。素材候補内でソースを切り替えても、切り抜き候補とハイライト候補の選択は別々に保持する。FR-26 の既存 queue fingerprint・失効・開始可否の契約は変更しない |

### FR-32: ジョブエラーの構造化通知（v3.1 追加要件）

| 項目 | 内容 |
|------|------|
| **入力** | バックグラウンドジョブの失敗（ffmpeg・Codex CLI・YouTube API 等の技術ログを含む） |
| **処理** | ジョブエラーを「動画 ID・job ID・処理種別・要約（1 行の日本語）・詳細（技術ログ）・発生日時」を持つ構造化通知として扱う。ページ先頭には要約だけを表示し、技術ログ全文は当該動画の詳細・再生成領域からのみ参照できるようにする |
| **出力** | ユーザー向けには「（処理名）に失敗しました」+ 対象動画 + 再試行方法の要約。技術ログは詳細表示 |
| **スコープ** | 動画ごとに直近 3 件までのエラー詳細を session state に保持し、現在表示中の動画の詳細・再生成だけに表示する。動画に紐づかないエラーはグローバル要約だけを表示し、無制限に保持しない。ffmpeg 出力等の長い技術ログがページ先頭を占有する現状（[`ui/app.py`](../src/yt_live_kit/ui/app.py) の `st.error(job_error)`）を廃止する |

### FR-33: ショート生産ライン（工程 UI）（v3.2 追加要件）

運用目標（§1.3.1）を UI として実装する中核要件。FR-17 v3.1 の「ショート作成ワークスペース」の内部設計を本 FR で置き換える（3 ワークスペースの骨格・詳細・再生成・回復用空状態は FR-17 v3.1 のまま維持する）。

| 項目 | 内容 |
|------|------|
| **入力** | 動画 1 本と、その切り抜き候補（実データは 142 / 142 本が 180 秒超のため、サブ区間提案（FR-30）が事実上必須の入口になる）。候補の探索は YouTube VTT、選択後の区間確認は resolver が返す粗いまたは精査済み `TranscriptArtifact` を使う |
| **処理** | ショート 1 本を次の工程で仕上げる。**(1) 素材選定**（候補を選ぶ）→ **(2) 区間決定**〔ゲート①: 必要なら選択親候補区間のローカル精査を明示実行し、区間ごとの文字起こしを見て採否・境界を確定〕→ **(3) テロップ確認**〔ゲート②: FR-22 の AI 台本を人が全文確認・修正して確定〕→ **(4) 生成**（FR-25 連結。確定済み複数本は裏でまとめてエンコードしてよい = FR-26 を実行エンジンとして使う）→ **(5) 最終確認**〔ゲート③: プレビューを見て OK / NG〕→ **(6) 予約**（FR-27 / FR-28 の次の空き枠へ）。各ゲートを通過するまで次工程に進めない |
| **出力** | 「いま何本目のどの工程にいて、次に何を確認するか」が常に画面から分かる工程表示。1 日 3 本はこのラインを 3 周まわすことで達成する |
| **スコープ** | **行き止まりゼロ:** ラインに乗せた素材は必ず「予約済み」まで到達できる。180 秒超で弾く・単体生成物が予約に進めない等の途中切れを作らない。**毎回選ばせない:** レイアウト・テロッププリセットは設定ページ（FR-20）の既定値を使い、工程には読み取り専用の適用値と「設定で変更」だけを表示する。単体手動生成（時刻手入力）は補助のまま残すが工程には出さない。既存の安全契約（FR-26 の queue fingerprint / 確定、FR-27 の確認ダイアログ、上書き確認）は工程の各ゲートとして再利用する |

**S9 artifact handoff:** ゲート①で区間列を確定した時点で、resolver の artifact reference と順序付き `used_range_cue_digest` 配列を immutable な line / cutplan snapshot に凍結する。FR-22、FR-25、queue / line、review fingerprint は同じ snapshot を参照し、downstream で resolver を再実行しない。S9 の schema version が無い旧 line state、artifact lineage が無い旧確認、破損・不一致 snapshot は人確認を再利用せず未確認へ戻す。

**ゲート②の品質判定:** 台本の品質表示と生成条件を次の 4 種類に分離する。

| 種類 | 例 | 動作 |
|------|----|------|
| 自動ハード判定 | JSON / 必須値 / 時刻 / 区間と台本の整合 | 失敗時は生成不可。現在値で生成直前にも再検証する |
| 自動警告 | 1 行 16 文字超 | 注意を促すが、警告だけでは生成を止めない |
| 人の全文確認 | 「台本全体の誤字・固有名詞を確認した」 | 既定未チェック。ユーザー操作でのみ通過する |
| 生成条件 | ハード判定通過 + 人確認済み + 現在の review fingerprint と確認時 fingerprint が一致 | すべて満たすときだけ「台本を確定して生成へ」を有効化する |

行別エディタには本文、時刻、行全体の強調トグルを表示する。AI 案からユーザーが変更した箇所は補助表示してよいが、AI が見逃した誤認識は差分に現れないため、差分確認を全文確認の代わりにしてはならない。また現行の保存形式では AI 内部の修正由来を証明できないため、「Codex が修正」のような出所表示はしない。UI 文言は「AI案から変更」とする。

**fingerprint と失効:** FR-26 の queue fingerprint は既存の意味を変えず、テロップ確認専用の review fingerprint を別に持つ。line snapshot には artifact reference と順序付き使用区間 cue digest 配列を保存し、queue fingerprint 自体の意味は変更しない。review fingerprint は `(video_id, clip_id)`、queue fingerprint、`TelopScriptDocument.model_dump(mode="json")` の canonical JSON、artifact fingerprint、使用区間 digest 配列から計算し、テロップ本文・強調フラグ・台本メタデータ・使用区間字幕のいずれかが変われば人確認を失効させる。一度編集して元に戻っても自動で確認済みへ戻さず、再確認を必須とする。生成直前に fingerprint とハード判定を再検証する。

**出力 fingerprint と最終確認の失効:** `output_fingerprint` は `video_id`、`clip_id`、生成に使った `review_fingerprint`、`Path.resolve(strict=True)` で得た絶対パス、`st_size`、`st_mtime_ns`、mp4 ファイル内容の SHA-256 を canonical JSON 化して SHA-256 を計算する。生成完了後に計算し、最終確認時の `preview_confirmed_fingerprint` と結び付ける。工程 6 へ進む直前にも再計算し、出力の上書き・置換・欠損または review fingerprint の変更で一致しなければ、台本確認ではなく**最終プレビュー確認だけ**を失効させる。

**ライン状態の永続化:** ライン開始後は `data/{video_id}/shorts/line/line_{clip_id}.json` を `(video_id, clip_id)` ごとの正本とし、schema version、queue / review / 確認済み review / output / 確認済み preview の各 fingerprint、現在工程、確認日時、upload operation ID、更新日時を atomic 保存する。加えて `data/{video_id}/shorts/line/active_line.json` に明示選択中の `clip_id` と更新日時を atomic 保存する。再起動時は有効な active pointer を優先し、無効・欠落時は `current_stage != reserved` の line state を `updated_at` 降順、同値なら `clip_id` 昇順で 1 件選ぶ。非完了ラインが無ければ左パネルを空状態にし、完了済みラインを勝手に再開しない。壊れた状態や欠落時は fail closed とし、出力ファイル等から機械的に証明できる状態だけを再構成する。人の台本確認・最終確認は証明できない限り未確認へ戻す。

**本日の進捗:** 左パネルの指標は「本日のライン完了 1／3」と表記し、現在設定されている `SchedulePolicy.timezone`（未設定時の既定 `Asia/Tokyo`）で日付を判定する。`UploadOperation.created_at` を同 timezone へ変換し、同一 `(source_video_id, source_kind, clip_id)` の当日最新 operation だけを対象に `reserved` / `uploading` / `uploaded` を完了数へ含め、`failed` / `needs_reconciliation` は除外して「要対応 N 件」と併記する。timezone 設定を変更した場合は、現在値で当日分を再集計する。YouTube クォータ用の `America/Los_Angeles` 基準 upload attempt 台帳とは混同しない。

刻んだサブ区間（FR-30 の確定区間列）からゲート②のテロップ生成（FR-22）へ直接接続する。これにより YouTube 自動字幕の誤認識を、必要な区間だけローカル Whisper で精査し、さらに AI 案 + 人の全文確認で直してから焼き込む経路が、長い候補に対しても成立する。区間決定で選ばれた resolver 結果の artifact fingerprint と使用区間 cue digest は cutplan、台本、review fingerprint へ同じ値を伝播する。FR-31（選択引き継ぎ）はこの接続に統合する。

完成時のレイアウトと文言の視覚リファレンスは [`references/u6-short-production-line-v3.2.png`](./references/u6-short-production-line-v3.2.png) とする。ただし状態遷移・失効・生成条件は本要件書を正本とし、画像と不一致の場合は本文を優先する。

### FR-34: 区間内容の可視化（v3.2 追加要件・S8）

| 項目 | 内容 |
|------|------|
| **入力** | サブ区間提案（FR-30）の各候補区間、resolver が返す coarse または精査済み `TranscriptArtifact`。coarse artifact の取得元は後方互換の `data/{video_id}/subtitles/ja.vtt` |
| **処理** | 提案された各サブ区間について、**その区間で実際に話している内容（文字起こしテキスト）**を採否チェック・境界入力の隣に表示する。テキストは artifact の絶対時刻 cue を使い、既存の `parse_vtt_with_end()` + `filter_cues_for_segment()`（[`services/subtitle_burn.py`](../src/yt_live_kit/services/subtitle_burn.py)）相当の区間抽出と progressive 重複除去を通した読みやすい形とする。境界（開始・終了）を変更したら表示テキストも追従する。S9 の精査済み artifact を使う場合も、同じ artifact fingerprint を区間確認から台本へ渡す |
| **出力** | 区間ごとの文字起こし表示。人が「どこで切れているか」を読んで境界を調整できる |
| **スコープ** | FR-30 の「人の確認を必須とする」を実効化する要件である（現状は確認材料が画面に無く、確認しようがない）。あわせて生成済み動画のプレビュー表示は縦動画が画面幅いっぱいに広がらないよう幅を制限する（`st.video(width=...)`。[`short_cut.py`](../src/yt_live_kit/ui/components/short_cut.py) / [`shorts.py`](../src/yt_live_kit/ui/views/shorts.py) / [`shorts_queue.py`](../src/yt_live_kit/ui/components/shorts_queue.py) / [`highlights.py`](../src/yt_live_kit/ui/views/highlights.py) の 4 箇所）。`services/` の変更は VTT 区間抽出の既存純粋関数の再利用に留め、新設が必要な場合も読み取り専用ヘルパーに限る |

### FR-35: 字幕アーティファクトの解決と永続キャッシュ（S9）

| 項目 | 内容 |
|------|------|
| **入力** | 現行の YouTube 動画 ID、取得済み `data/{video_id}/subtitles/ja.vtt`、または S9 が生成したローカル Whisper の区間字幕 |
| **処理** | `TranscriptArtifact` は取得元、`source_kind`（`youtube_vtt` または `whisper_cpp`）、schema version、モデルと実行環境、設定、音声入力 fingerprint、入力区間の順序付き配列、区間ごとの status（`success` / `fallback` / `failed` / `partial`）、絶対時刻 cue、区間ごとの cue digest、artifact fingerprint を持つ不変の成果物とする。schema は未知 field を拒否する strict モードとし、時刻・padding・VAD を含む境界値は整数ミリ秒で保存する。cache identity は `whisper_cpp` では実際の音声 bytes、sample rate、channel、codec、ffmpeg 設定、取得元、model / runtime / decode 設定・initial prompt・padding・VAD・入力区間列から、`youtube_vtt` では source VTT bytes と取得 metadata・入力区間列から計算し、いずれも path / mtime だけには依存しない。artifact fingerprint は cache identity に成功した cue digest と schema を加えて計算する。Whisper の model / runtime fingerprint には model file、whisper-cli の version / build capability、language、initial prompt、decode / VAD / padding、出力 schema を含める。`resolver` は親候補探索では有効な YouTube VTT を粗い字幕として選び、選択済み親候補区間では要求された全区間が成功した Whisper artifact を優先する。既存 VTT の内容・パス・意味を置き換えず、再取得時も既存 `ja.vtt` の bytes を変更しない。取得中の新しい VTT は隔離した一時領域へ保存し、`ja.vtt` が無い場合だけ初回 bootstrap し、既存なら `subtitles/sources/{source_fingerprint}.vtt` へ immutable source として保存する。取得失敗時は既存 `ja.vtt` と既存成果物を変更しない。artifact と resolver の index は lock 付きで atomic に保存し、クラッシュ後の再構築・破損・不一致は fail closed とする。cache hit は入力音声または source bytes、モデル、設定、区間が一致し、artifact の schema と cue digest を再検証できる場合だけ成立する |
| **出力** | `data/{video_id}/transcripts/artifacts/{artifact_fingerprint}.json` と `data/{video_id}/transcripts/index.json`。既存の `subtitles/ja.vtt`、`transcript/full.txt`、`transcript/compressed.txt` は後方互換のため残し、source VTT は `subtitles/sources/` に別名で保存する |
| **スコープ** | S9 初版の保存キーは既存の `video_id` を維持する。`asset_id` による抽象化、`source_kind=local_video`、ローカル動画を入力にする経路は別フェーズで追加し、S9 で現行の data path や `VideoMeta.id` を移行しない |

### FR-36: 選択親候補区間のローカル精査（S9）

| 項目 | 内容 |
|------|------|
| **入力** | FR-30 の親候補を人が選択した 1 件以上の絶対時刻区間、既存 VTT の cue、yt-dlp で取得した音声のみの入力、受け入れ済み whisper.cpp 1.9.1 runtime とモデル設定 |
| **処理** | 明示操作で選択区間列を音声 span として準備し、現行の 1 ジョブ制約の中で入力順に複数区間を処理する。whisper.cpp はサブプロセスとして呼び、出力を絶対時刻へ変換して 1 件の `TranscriptArtifact` に区間順で保存する。`used_range_cue_digest` は正規化済み range、padding、cue inclusion rule、cue の絶対時刻・本文・順序から計算する。区間境界は Whisper の timestamp だけで確定せず、padding、必要な VAD、既存 cue、動画プレビュー、人の確認を使う。精査済み artifact の immutable reference と順序付き区間 digest 配列を FR-30 の cutplan、FR-22 のテロップ台本、FR-25 の生成 preflight、FR-33 の line / review snapshot へ渡し、resolver を downstream で再実行しない |
| **出力** | 区間ごとの絶対時刻 cue と status を含む精査済み `TranscriptArtifact`、runtime / model / input の診断情報、cache hit / miss と処理時間。1 区間でも失敗した partial artifact は要求全体の高精度結果として resolver が返さず、runtime 不備や精査失敗時は日本語エラーと明示された YouTube VTT fallback を返す |
| **スコープ** | 字幕なし・低品質字幕を理由に全編 Whisper を通常経路へ入れない。全編再文字起こし、47 本の一括 backfill、VTT の自動置換、無確認の自動境界確定は将来フェーズとする |

### FR-37: ショート投稿メタデータの品質ゲート（P6）

| 項目 | 内容 |
|------|------|
| **入力** | FR-22 / FR-23 の同一 Codex CLI 呼び出し結果、FR-29 のショート専用テンプレート、元動画 `VideoMeta`、投稿直前にユーザーが編集した最終タイトル・説明文・タグ |
| **処理** | 新規のテロップ台本生成では、`title_candidates` を固定順の 3 件、すなわち ①検索明快型（固有名詞と主題を前半へ置く）、②仕事影響型（誰にどんな変化があるかを示す）、③好奇心型（結論を過度に隠さない疑問・意外性）として同じ Codex 呼び出し内で生成する。3 件は strip 後に非空、相互に同一でなく、100 文字以下、半角山カッコ無しを必須とする。日本語タイトル 18〜32 文字は推奨警告であり、保存拒否の条件にはしない。既存の 1〜2 件タイトルを持つ保存済み台本は読み込み互換を維持するが、新規生成の完了条件には使わず、UI で再生成または手動補完を案内する。概要欄は `{{description}}`、`{{source_title}}`、`{{source_url}}` と、固定 CTA 文「チャンネル登録は動画下のチャンネル名からお願いします。」を必須構成とする。テンプレートが無い場合は既存ファイルを上書きせず、この必須構成を満たす安全な既定テンプレートを原子的に初回作成する。合成時と、投稿確認ダイアログを開く直前・確定後の upload 再検証時の両方で、最終編集済み説明文に生成説明、元動画タイトル、開始秒付き元動画 URL、固定 CTA 文が残っていることを検証する。合成時に期待する 4 項目、template bytes fingerprint、`meta.json` fingerprint を不変の要件 snapshot として preview / content snapshot / fingerprint へ凍結し、確定後は mutable な template / meta を再読込せず、この snapshot と最終本文を再検証する。template / meta の変更を反映する場合は新しい preview と確認を作り直す |
| **出力** | 方向の異なるタイトル候補 3 件、必須構成を満たす最終説明文、不変の概要欄要件 snapshot、投稿可否と日本語の不足理由。確認済み content snapshot と `videos.insert` body は再検証を通った同一本文だけを使う |
| **スコープ** | タイトル候補を得るための Codex 呼び出し回数は増やさない。概要欄 URL を Shorts フィード上のクリック導線とはみなさず、元動画への主要導線は FR-38 の YouTube Studio「関連動画」とする。既存テンプレートは自動上書きせず、不足時は投稿だけを fail closed にして修正箇所を日本語で案内する。実 YouTube 投稿や公開データ変更を P6 の自動テストで行わない |

### FR-38: 関連動画の Studio 手動確認と永続状態（P6）

| 項目 | 内容 |
|------|------|
| **入力** | upload operation の元動画 ID、アップロード成功後の Shorts の YouTube video ID、ユーザーによる YouTube Studio での関連動画設定結果 |
| **処理** | YouTube Data API に書き込み可能な関連動画 field があると仮定せず、アップロード成功後に YouTube Studio の編集画面と設定対象の元動画を明示する手動チェックリストを表示する。対象 ID の唯一の正本は既存 `UploadOperation.source_video_id` と、upload 成功後に入る `UploadOperation.video_id` とし、P6 専用の重複 ID field を追加しない。新規 field は `related_video_status`（`not_ready` / `pending` / `confirmed`）と UTC の `related_video_confirmed_at` だけにする。`uploaded` になる前は `not_ready`、アップロード成功後は `pending`、正本の 2 ID を表示する確認ダイアログでユーザーが「Studio で関連動画を設定済み」と確定した場合だけ `confirmed` に遷移する。確認操作はローカル状態だけを lock + atomic write で更新し、YouTube API を呼ばない。既存 operation で両 field が欠落する場合は、`state=uploaded` かつ `video_id` があるものだけ `pending`、それ以外を `not_ready` とする後方互換 migration を行い、`confirmed` は絶対に推測しない |
| **出力** | operation ごとの関連動画状態、正本 field から表示する対象元動画 ID・対象 Shorts video ID、確認時刻、`pending` の総件数と対象一覧、未確認時の日本語案内。集計は lock 付き queue service が行い、UI は queue JSON を直接走査しない。再起動後も同じ状態を表示する |
| **スコープ** | 関連動画の自動設定、YouTube Studio のブラウザ自動操作、実 API upload、公開データ変更は P6 の対象外。対象は自チャンネルの元動画を Studio で人が設定した事実の記録に限定する。関連動画確認は upload 後の追跡工程であり、`pending` のままでも既存の `publishAt` を取消・延期・変更せず、公開を技術的に止める hard gate にはしない。未確認は「要対応」として残し、publication poll は既存どおり継続する |

---

## 5. ショート量産の機能要件

スコープ: 複数区間のジャンプカット連結 + テロップ + 冒頭 1〜2 秒のフックタイトル（大テロップ）+ メタデータ生成。トランジション（xfade）・ズーム・BGM・SE は §2 のとおりスコープ外。

### FR-22: テロップ台本の生成

| 項目 | 内容 |
|------|------|
| **入力** | 選択済み区間（複数）と、その区間を解決した `TranscriptArtifact` の immutable reference。親候補探索は YouTube VTT を使い、S9 の精査が有効なら同じ artifact の絶対時刻 cue と順序付き `used_range_cue_digest` 配列を渡す。カット単位字幕は開始・終了とミリ秒を保持した VTT 相当の時刻付きテキスト形式で渡す |
| **処理** | cutplan が確定した artifact reference と digest 配列を snapshot として凍結し、downstream で resolver を再実行せず Codex CLI に区間の字幕を渡す。誤認識の修正・句読点付与・短い行への分割・強調行フラグの付与まで済んだ「テロップ台本」を JSON で生成させる。[`services/highlights.py`](../src/yt_live_kit/services/highlights.py) と同じパターン（テンプレート結合 → Codex CLI 実行 → JSON 抽出 → バリデーション → 保存）を踏襲する。保存する台本には使用した artifact fingerprint と順序付き使用区間 cue digest を含め、入力 artifact が変わった場合は確認済み台本を有効扱いにしない |
| **出力** | テロップ台本 JSON（区間ごとの行・行全体に対する強調行フラグ、使用 artifact reference / fingerprint、順序付き使用区間 cue digest 配列） |
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
| **入力** | 複数の小区間（人が選択、または AI 候補から選択）、artifact reference / fingerprint と順序付き `used_range_cue_digest` 配列を記録した確認済みテロップ台本、テロップスタイル |
| **処理** | 既存の `encode_segment` + `concat_segments`（[`services/ffmpeg.py`](../src/yt_live_kit/services/ffmpeg.py)）と [`services/shorts.py`](../src/yt_live_kit/services/shorts.py) の縦型レイアウト処理（blur / crop、2 パス方式、`INTERMEDIATE_CRF=16`）を組み合わせ、複数の小区間を 1 本のショートに連結する。区間境界は共通 API で `Decimal(str(value))` + `ROUND_HALF_UP` により整数ミリ秒へ正規化し、以後その値を ID、合計尺、エンコード引数、字幕の累積 offset / clip の唯一の基準とする。公開境界では同じ共通 API により冪等に再検証する。入力順を再生順・ID 生成順・エンコード順として保持し、自動で並べ替え・重複除去しない。確認済みテロップ台本は ffmpeg 実行前と字幕関数の公開境界で入力区間との一致および artifact fingerprint / digest 配列を再検証し、字幕の関係範囲が変わっていれば fail closed で生成を止める。フックタイトルは冒頭 1〜2 秒の大テロップとして同じ ASS に載せる |
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

**R1 監査注記（2026-08-02）:** P3 の 1 本では公開前後の bounded poll を手動で本番 service へ接続し、上記の受け入れ証跡を得た。一方、通常の予約 operation は upload job 内の processing poll までしか自動接続されず、予約時刻後の publication poll を起動する運用導線がない。poll service と単体テストの存在だけでは通常運用の要件を満たさないため、H1-5 で upload job と分離した明示 CTA または follow-up job を実装するまで、この接続要件を未完了へ戻す。

**H1-5 実装契約:** 通常の upload worker は processing 確認後に終了する。予約時刻後の operation カードに表示する明示 CTA は既存 job API で bounded な publication poll job を起動し、予約時刻前は YouTube API を呼ばない。poll job は operation 単位の private lock で二重起動を拒否し、同じ `operation_id` の `queue.json` へ各 observation を atomic 追記する。永続 job JSON と queue の poll history は再起動後の復元に利用する。

### FR-28: 投稿スケジュールポリシー

| 項目 | 内容 |
|------|------|
| **入力** | `daily_time`（厳密な `HH:MM`）、`interval_days >= 1`、IANA timezone（既定 `Asia/Tokyo`）からなるスケジュールポリシー、量産済みショートのキュー、timezone-aware な現在時刻 |
| **処理** | 設定ページ（FR-20）にポリシーを保存し、「次の空き枠に自動割り当て」を提供する。計算は `zoneinfo.ZoneInfo` で行い、DST を含むローカル予約枠を aware datetime として扱う。YouTube API へ渡すときだけ UTC RFC 3339 `Z` へ変換する。予約枠と full operation は `data/_schedule/queue.json` の同一 record、upload attempt は `data/_schedule/upload_attempts.json` で別管理する |
| **出力** | 割り当て結果と、再起動後にも復元できる永続 upload operation |
| **スコープ** | 予約枠の件数と upload attempt の日次上限を混同しない。upload attempt は公開日ではなく、**実際に API session を開始しようとした日の `America/Los_Angeles` 暦日**で数える。`YTLK_VIDEO_UPLOAD_DAILY_LIMIT` は 1〜100、既定 100 とし、失敗・結果不明も 1 試行に含める |

**投稿ごとの日時変更（v3.2 追加）:** 公開・投稿ワークスペースでは、自動計算した次の空き枠を初期値として、投稿ごとに日付と時刻を変更できる。指定値は現在の `SchedulePolicy.timezone` の aware datetime として構築し、現在から最低 10 分先、既存の slot と非重複、DST の ambiguous / nonexistent ではないことを preview 前と確定時の両方で検証する。確認ダイアログには実際に送信するローカル予約日時と UTC RFC 3339 `Z` を固定表示する。編集・不正値・競合時は以前の確認を再利用せず、operation / job / upload attempt を作成しない。

**複数枠拡張（v3.2 追記・P5）:** 運用目標「毎日 3 本」（§1.3.1）のため、ポリシーを `daily_time` 1 つから **`daily_times`（1〜N 個の厳密な `HH:MM` リスト、重複禁止、設定ページで編集可能）**へ拡張する。`assign_next_slot` は日内の複数枠を時刻順に埋めてから翌 `interval_days` 日へ進む。既存の単一 `daily_time` 設定は読み込み互換（要素 1 個のリスト）として扱い、FR-27 の安全契約・UTC 変換・attempt 台帳の扱いは変更しない。公開時刻の具体値は未定のため、既定値は定めず設定画面での編集を正とする。

予約確定と upload attempt の競合制御を次のとおり固定する。

- プレビュー時に slot / quota snapshot を作り、確定後に lock を取り直して予約枠の空き、operation の重複、America/Los_Angeles 当日試行数を再検証する
- `MediaFileUpload` / `videos.insert` の resumable upload session 前に `upload_attempts.json` へ試行を atomic 記録する。事前の read-only `channels.list` は attempt に数えない。記録に失敗した場合は upload を開始しない。上限到達後は同日中の実 upload を拒否し、予約公開日をずらすことで試行枠を空いたことにはしない
- 同一 `job_id` / `operation_id` / content snapshot の再送は冪等に扱い、二重クリック、ジョブ再実行、アプリ再起動で新しい operation や `videos.insert` を重複作成しない。`needs_reconciliation` は自動再送せず、人の照合後に明示的な新 operation として再試行する
- confirm 時に operation ID と job ID を先行生成して単一 queue record へ atomic 保存し、jobs は同じ requested job ID の JSON を worker thread 起動前に作る。起動時は `jobs.close_orphans()` の後に upload recovery を行い、attempt ledger を外部効果開始の正本とする。active state（`reserved/uploading`）だけを recovery 遷移対象とし、attempt 無しは `failed` + slot 解放として新しい preview / 承認を要求し、attempt 有りまたは ledger が壊れて有無を確定できない場合は `needs_reconciliation` + slot 保持として自動再送しない。terminal state（`uploaded/failed/needs_reconciliation`）は変更せず、ledger 不整合時は queue / slot 非変更のまま日本語エラーで全新規 upload を fail closed にして手動修復を要求する

**P0（テストアップロード検証）について:** P0 を危険な最小経路にはしない。FR-27 / FR-28 の安全契約、永続 operation、冪等性、試行上限、resumable upload、reconciliation、polling を P1 / P2 でモック実装・自動テストした後に、その同じ本番経路で実 upload を 1 本だけ行う。ロックされる場合は Google のコンプライアンス審査を別承認で申請し、**P1 / P2 の開発とテストは実操作の承認・審査待ちで止めない**。この検証は [`docs/execution-plan-v3.md`](./execution-plan-v3.md) の P0 タスクとして扱う。

### FR-29: ショート概要欄の定型リンク差し込み（v3 追加要件）

| 項目 | 内容 |
|------|------|
| **入力** | FR-23 で生成したショートの説明文、元動画 ID、切り抜き先頭区間の開始ミリ秒、ユーザーが編集する定型文テンプレート `data/_config/shorts_description_template.txt` |
| **処理** | テンプレートの `{{description}}` / `{{source_title}}` / `{{source_url}}` を置換して、投稿用の説明文を合成する。`{{source_title}}` と `{{source_url}}` は `data/{video_id}/meta.json`（FR-01 の `VideoMeta`）を正本とし、`{{source_url}}` には切り抜き先頭区間の開始秒を `t` クエリとして付与する。チャンネル URL は専用プレースホルダーを設けず、テンプレート本文へユーザーが直接記載する |
| **出力** | 合成済みの説明文。確認ダイアログ（FR-27）の「説明文全文」に合成後の本文が表示され、その本文がそのまま `videos.insert` の `snippet.description` になる |
| **スコープ** | 長尺用の `description_template.txt`（FR-21 のタイムライン合成）とはファイル・関数ともに分離する。P4 時点の非投稿利用ではテンプレート未設定時に FR-23 の説明文をそのまま使う後方互換を維持する。P6 の予約投稿経路では FR-37 の既定テンプレート初回作成と必須構成ゲートを優先し、不完全な本文の投稿は許可しない。UI 上のテンプレート編集画面は持たず、ファイル直接編集とする |

合成の安全契約を次のとおり固定する。

- 合成は `build_upload_preview()` を呼ぶ**前**に完了させる。preview の fingerprint、確定後の再検証、content snapshot、`videos.insert` body はすべて合成後の本文だけを見る。確認ダイアログに出た本文と実際に送信される本文が一致しない状態を作らない
- 合成結果に半角の山カッコを含む場合、および UTF-8 で 5000 bytes を超える場合は、テンプレートが原因であると分かる日本語エラーで拒否する。preview を作らず、投稿を開始しない
- テンプレートが `{{source_title}}` / `{{source_url}}` を含むのに `meta.json` が無い・壊れている場合は、空文字へ暗黙にフォールバックせず日本語エラーで拒否する
- 連結モード（FR-25）で複数区間を含むショートは、**先頭区間の開始秒**を `t` に使う。同じ入力からは常に同じ URL になること

### FR-30: 切り抜き候補からのショート用サブ区間提案（v3 追加要件）

| 項目 | 内容 |
|------|------|
| **入力** | 親候補 1 件（`ClipCandidate` または `HighlightSegment`）、親候補探索に使った YouTube VTT、S9 で選択区間を精査した `TranscriptArtifact`、人が調整した採否と区間境界 |
| **処理** | 親候補の探索は既存 VTT を粗い入力として維持し、候補 document に coarse VTT artifact fingerprint、全 cue digest、候補 fingerprint を保存する。親区間を選択した後は resolver が返す同一 artifact の immutable reference、絶対時刻 cue、順序付き `used_range_cue_digest` 配列を Codex CLI へ渡し、合計 180 秒以内に収まるサブ区間を 2〜5 個提案させる。提案は service の純粋関数が「親区間への包含・時系列・非重複・個別尺・合計尺・`duration_sec` 一致・半角山カッコ禁止」で検証し、`data/{video_id}/shorts/cutplan/cut_{parent_id}.json` へ atomic 保存する。人は各区間の採否と開始・終了時刻を調整でき、生成 submit 時に同じ純粋関数で再検証する。確定した区間列は入力順のまま FR-25 の `build_short_from_segments()` へ渡し、cutplan には artifact reference / fingerprint と順序付き使用区間 cue digest 配列を immutable snapshot として保存する。FR-22 以降はこの snapshot を使い、resolver を再実行しない |
| **出力** | coarse artifact provenance と候補 fingerprint を持つサブ区間提案 JSON、精査済み artifact reference / digest 配列を持つ cutplan、確定区間から生成した 1 本の縦型ショート mp4（1080x1920）+ コマンドログ |
| **スコープ** | 提案は Codex の 1 回呼び出しのみ。**自動採用・自動生成は行わず、人の確認を必須とする**（FR-25 の「区間選定は人、ffmpeg は決定論的に連結」原則を維持）。無音・フィラーの音声解析による自動検出は対象外。テロップ台本（FR-22）の生成・確認は本導線では行わず、既存のキュー量産（FR-26）に委ねる |

提案・確定の安全契約を次のとおり固定する。

- 親候補の尺が 180 秒以下の場合は提案導線を出さず、既存の単一区間生成（FR-25 の 1 区間経路）をそのまま使う。S9 の精査を行った場合も、同じ artifact の provenance を単一区間台本へ引き継ぐ
- 提案 JSON が検証に落ちた場合は、空や部分採用へ暗黙にフォールバックせず日本語エラーで拒否する。保存済みの提案ファイルは書き換えない
- Codex 呼び出しは明示的な「区間を提案」submit 時だけ行う。通常 rerun では保存済み提案を再利用し、再実行しない
- 区間境界は FR-25 と同じ `normalize_segment_bounds()` による整数ミリ秒を唯一の基準とし、UI 側で独自に丸めない。合計尺の下限・上限は [`services/shorts.py`](../src/yt_live_kit/services/shorts.py) の `MIN_DURATION_SEC` / `MAX_DURATION_SEC` を正本として参照する
- clip ID と出力名は FR-25 と同じ `make_clip_id()` 由来で決定的にする。既存 mp4 の上書きは確認 dialog の確定時だけ行う
- Codex CLI 未導入時は、保存済みプロンプトファイルを使う手動フォールバックを日本語で案内する（FR-22 / 既存 highlights と同じ扱い）

---

## 7. 非機能要件（v3 追加分）

### NFR-11: コスト制約の継続

- v1 の NFR-01 を継続する。**従量課金 API は使用しない。** テキスト生成は Codex CLI（ChatGPT サブスクリプション内、`codex login` 済み）を使い、字幕精査はローカルの whisper.cpp 1.9.1 をサブプロセスで呼ぶ。いずれも外部の従量課金 API を経由しない
- **新しい pip 依存パッケージを追加しない。** `google-api-python-client` / `google-auth-oauthlib` は v2 で導入済みであり、v3 はこれと yt-dlp / ffmpeg / Streamlit / pydantic / typer で完結させる
- whisper.cpp の実行ファイルとモデルはアプリが自動取得・自動更新しない。設定された実体、モデル fingerprint、対応 capability を実行前に検証し、未導入・不一致時は高精度 artifact を作成せず日本語で案内する

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

### AC-19: 動画詳細ページ（U2 時点の受け入れ履歴。v3.1 で AC-31 に置き換え）

> **注記（2026-08-01）:** 以下は U2 完了時点の受け入れ記録として維持する。FR-17 v3.1 改訂により、5 段ステッパー等の前提は AC-31 の新構成に置き換わった。チェックは書き換えない。

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
- [x] 通常の予約 operation が processing poll 後に終了しても、予約時刻後の publication poll を明示 CTA または bounded follow-up job から起動でき、再起動後も同じ operation へ結果を追記できる
- [x] P0 のテストアップロードで非公開ロックの有無が確認され、記録されている
- [x] P0 の専用承認に、private lock 非該当時は probe 動画が指定時刻に public となり得ることまで含まれている

### AC-28: v3 総合受け入れ

- [x] AC-18〜AC-27 がすべて満たされている（P3 の実公開受け入れは完了済み。R1 監査で判明した通常 operation の公開後 poll 接続は H1-5 で完了）
- [x] v1 / v2 の機能に回帰が無い（`uv run pytest` が全件通過する）
- [x] 実配信 1 本から、チャプター生成 → ショート複数本の生成 → 予約投稿までを通しで実行できる
- [x] 実 upload、審査フォーム提出、P3 の実予約公開について、各操作ごとの対象と内容を提示した別々の明示承認記録がある

### AC-29: ショート概要欄の定型リンク差し込み

- [x] `data/_config/shorts_description_template.txt` の `{{description}}` / `{{source_title}}` / `{{source_url}}` が置換され、テンプレートに直接書いたチャンネル URL がそのまま説明文へ入る
- [x] `{{source_url}}` に切り抜き先頭区間の開始秒が `t` クエリとして付き、同じ入力から常に同じ URL になる（連結モードでも先頭区間基準）
- [x] P4 時点ではテンプレート未設定時に FR-23 の説明文がそのまま使われ、既存の投稿導線に回帰が無い。P6 の予約投稿経路は AC-38 の必須構成ゲートへ移行する
- [x] 確認ダイアログの「説明文全文」が合成後の本文を表示し、その本文が preview fingerprint・content snapshot・`videos.insert` body と一致する
- [x] 半角山カッコ・5000 bytes 超過・`meta.json` 欠損の各ケースが、テンプレートが原因と分かる日本語エラーで拒否され、preview が作られない
- [x] 長尺用 `description_template.txt` の合成（FR-21）に影響が無い

### AC-30: 切り抜き候補からのショート用サブ区間提案

- [x] 180 秒を超える切り抜き候補を選ぶと「ショート用の区間を提案」導線が出て、生成後に 2〜5 個のサブ区間が採否チェック付きで表示される
- [x] 採用区間の合計尺が常に表示され、10〜180 秒の範囲外では作成ボタンが押せず、分割・短縮を促す日本語案内が出る
- [x] 採用区間を確定すると、ジャンプカット連結された 1080x1920・10〜180 秒のショートが 1 本生成される
- [x] 提案が親候補の範囲外・時系列違反・重複・尺違反・`duration_sec` 不一致・半角山カッコを含む場合、日本語エラーで拒否され、保存済み提案が壊れない
- [x] 通常 rerun では Codex が再実行されず、保存済み提案（`cut_{parent_id}.json`）が再利用される
- [x] 180 秒以下の候補では提案導線が出ず、既存の単一区間ショート生成に回帰が無い
- [x] Codex CLI 未導入時は、プロンプトファイルを使う手動フォールバックが日本語で案内される
- [x] 人が調整した区間境界が整数ミリ秒として FR-25 と同じ正規化を通り、UI 独自の丸めが入らない
**S9 非回帰:** 親候補探索に YouTube VTT を残すこと、既存 cutplan の境界・検証・再利用を壊さないことは S9-6 で確認する。S9 固有の artifact / resolver / digest / 失効の受け入れは AC-37 に集約する。

### AC-31: 動画詳細ページの作業選択型 IA（FR-17 v3.2 / U6）

- [x] 初期表示の 1 画面内で、動画タイトル・素材候補数・ショート本数（生成ファイル / 予約可能の別）・概要欄反映状態が分かる
- [x] 初期表示で字幕全文とチャプター本文を描画しない
- [x] 素材候補・ショート作成・公開・投稿の 3 ワークスペースのうち、選択中の 1 つだけが描画される
- [x] 状態カードはクリックできず、作業切り替え（`st.segmented_control`）だけがワークスペース移動を担う
- [x] 作業を切り替えてもジョブ・YouTube 更新等の副作用が発生しない
- [x] 字幕成果物（`PipelineResult`）を読み込めない場合、通常ワークスペースを表示せず「取り込みで再処理」の回復用空状態を表示する。ボタンを押してもジョブは開始しない
- [x] 初期選択が FR-17 v3.2 の規則（ジョブ進行中 → 対応ワークスペース、候補 0 件 → 素材候補、予約可能 0 本 → ショート作成、予約可能あり → 公開・投稿）に従う
- [x] チャプターが正常なら、本文を開かなくても件数と形式 OK が公開・投稿ワークスペースで分かる。未生成・形式エラー時は概要欄反映を開始できない
- [x] 概要欄反映前に更新前と更新後を必ず表示し、確定前は YouTube を更新しない（FR-21 の安全契約の維持）
- [x] チャプター本文・タイムラインコピー・チャプター / 候補の再生成・字幕全文・元動画管理は詳細・再生成から利用できる
- [x] ショート作成の主導線が、候補選択 → サブ区間の採否・境界確定 → テロップ全文確認 → 生成 → 最終確認 → 予約の 6 工程になっている。180 秒超の候補ではサブ区間提案が工程 2 の実質的な入口であり、単体手動作成だけを補助領域に置く
- [x] 生成ファイル数と予約可能本数が別々に表示され、単体手動生成だけがある場合は予約投稿フォームを出さずショート生産ラインへの導線が出る
- [x] 既存ショートの上書き・概要欄更新・予約投稿・元動画削除の確認境界（`st.dialog`）に回帰が無い
- [x] 動画 A と動画 B で選択中ワークスペースやジョブ結果が混ざらない
- [x] 左パネルに作成中ショートのプレビュー・縮約工程・次の確認・本日のライン完了数が常時表示され、編集や確定の操作入口は重複して置かれない
- [x] ワークスペースを手動で切り替えてもライン状態を破棄せず、副作用が発生しない。工程 6 の明示 CTA だけが対象を保持して公開・投稿へ移動する
- [x] サイドバー折り畳み時もメイン上部に縮約工程状態が残る
- [x] 素材候補で選択した候補が、ショート作成の未確定フォームで同じ順序のまま選択済みになる
- [x] 候補再生成または候補 fingerprint 変更時は、ライン開始前の引き継ぎ選択と未確定 snapshot が破棄され、日本語で再選択が案内される
- [x] 引き継ぎだけでは FR-26 の正式 snapshot・ジョブ・永続ライン状態が作られず、区間列を確定してラインを開始した時点で初めて作られる
- [x] FR-26 の既存契約（queue fingerprint 失効・確定・上書き確認・job ID 分離）に回帰が無い
- [x] U6 の限定例外 `services/shorts_line.py` 以外の `services/` 契約に不要な変更が無く、`uv run pytest` が全件通過する

### AC-32: 概要欄反映の最新性判定（FR-21 v3.1 / U7・v4 候補）

- [ ] 最新のチャプター fingerprint と反映記録が一致するときだけ「最新のチャプターを反映済み」と表示される
- [ ] チャプター再生成後は「チャプター更新あり・要再反映」と表示され、反映 CTA が再び primary になる
- [ ] 旧配列形式（ID のみ）の反映履歴は「過去に反映済み・最新性不明」と表示され、読み込みエラーにならない
- [ ] 反映記録の保存は YouTube 更新成功後だけ行われる順序が維持されている

### AC-33: エラー通知の構造化（FR-32 / U8）

- [x] ジョブエラーが動画 ID・job ID・処理種別・要約・詳細・発生日時を持つ構造化通知として扱われる
- [x] ページ先頭には 1 行要約だけが表示され、長い技術ログが先頭を占有しない
- [x] 動画ごとに直近 3 件までのエラー詳細が保持され、現在動画の詳細・再生成だけに表示される
- [x] 動画に紐づかないエラーはグローバル要約だけが表示され、無制限に保持されない
- [x] 動画 A のエラーが動画 B の詳細に表示されない

### AC-34: 区間内容の可視化（FR-34 / S8）

- [x] サブ区間提案の各候補に、その区間の文字起こしテキストが表示される
- [x] 開始・終了の境界を変更すると、表示テキストが変更後の区間に追従する
- [x] 文字起こしは progressive 重複（同一行の 2 連続表示）が除去された読みやすい形である
- [x] 生成済みショートのプレビューが画面幅いっぱいに広がらない（4 箇所すべて）
- [x] 実機で、テキストを読んで境界を調整し「話が途中で切れないショート」を 1 本作れる
- [x] `uv run pytest` が全件通過し、既存のサブ区間提案・生成経路に回帰が無い

### AC-35: ショート生産ライン（FR-33 / U6 改）

- [x] ショート 1 本が「素材選定 → 区間決定 → テロップ確認 → 生成 → 最終確認 → 予約」の一本道で完成する
- [x] 各ゲート（区間・テロップ・最終確認）を通過するまで次工程に進めない
- [x] いまどの工程にいて、次に何を確認するかが画面から常に分かる
- [x] 刻んだサブ区間からテロップ生成（FR-22）へ直接進め、AI 案 + 人の全文確認を経た字幕だけが焼き込まれる（YouTube 自動字幕の生焼き込みが工程上発生しない）
- [x] 自動ハード判定、自動警告、人の全文確認、生成条件が UI と実装で分離され、「台本全体の誤字・固有名詞を確認した」は既定未チェックである
- [x] 1 行 16 文字超は警告として表示されるが、それだけでは生成を禁止しない
- [x] review fingerprint が確認時から変わると人確認が失効し、編集を元に戻しても自動復帰しない。生成直前にも fingerprint と自動ハード判定を再検証する
- [x] AI 案からユーザーが変更した箇所は補助表示できるが、差分だけで確認を完了できず、出所を証明できない「Codex が修正」表示がない
- [x] レイアウト・テロッププリセットが工程中に選択肢として出ず、設定ページの既定値が使われる
- [x] ラインに乗せた素材が「予約済み」まで到達できる（180 秒超の候補も刻む工程を経て予約まで通る）
- [x] 既存の安全契約（台本確定、上書き確認、投稿確認ダイアログ）が工程のゲートとして維持されている
- [x] ライン状態が `(video_id, clip_id)` 単位で atomic 保存され、再起動・壊れた JSON・出力欠損時に証明できない人確認を未確認へ戻す fail closed になっている
- [x] 左パネルの「本日のライン完了 N／3」は `SchedulePolicy.timezone` 基準で集計され、LA 基準の upload attempt 日付と分離される。失敗・要照合は完了数へ含めず「要対応 N 件」と表示される
- [x] 左プレビューが生成前・生成中・生成後・元素材欠損の工程状態に応じて切り替わる
- [x] 1 日 3 本の運用がこのライン 3 周で完結することを実機で通し確認済み
**S9 非回帰:** S9-6 は生産ラインの 6 工程、人確認、既存確認境界、既存 VTT fallback を確認する。S9 固有の UI・runtime・artifact・失効の受け入れは AC-37 に集約する。

### AC-37: 選択区間 Whisper と TranscriptArtifact（FR-35 / FR-36 / S9）

- [ ] `subtitles/ja.vtt` が S9 実行前後で上書きされず、粗い親候補探索と既存 v1〜v3 経路が回帰しない
- [ ] 再取得は incoming VTT を隔離して保存し、既存 `ja.vtt` がある場合はその bytes を変えず、無い場合だけ初回 bootstrap する。新しい source VTT は `subtitles/sources/` の immutable artifact とし、取得失敗時は既存成果物を変更しない
- [ ] artifact が strict schema（schema version、未知 field 拒否、`source_kind` enum、整数ミリ秒、`success` / `fallback` / `failed` / `partial` status）で、取得元、video ID、対象区間、絶対時刻 cue、cue digest、音声入力 fingerprint、モデル / runtime / 設定、artifact fingerprint を持ち、atomic 保存を通過する
- [ ] cache identity と artifact fingerprint が分離され、音声 bytes・sample rate・channel・codec・ffmpeg 設定・source、model file・whisper-cli build / capability・language・initial prompt・decode / VAD / padding・output schema を含む。path / mtime だけの再利用が無い
- [ ] resolver / artifact index が lock・crash recovery・破損検出を備え、部分 JSON、偽 fingerprint、範囲外 cue、未知 field、不一致 cache は fail closed になる
- [ ] resolver が親候補探索には有効な YouTube VTT、選択済み区間には有効な whisper.cpp artifact を返し、無効・不一致・部分成果物は高精度として返さない
- [ ] whisper.cpp 1.9.1 の capability とモデル fingerprint を実行前に検証し、新規 pip 依存・従量課金 API・モデル自動ダウンロードが無い
- [ ] 音声のみの入力を永続 cache し、複数区間を現行の 1 ジョブ内で入力順に処理できる。動画全体を取得して字幕精査する経路は無い
- [ ] coarse 候補 document が VTT artifact fingerprint、全 cue digest、candidate fingerprint を持ち、同じ候補内容・表示順から決定的に再構成できる。使用区間に関係する cue の変更だけが downstream を失効させる
- [ ] 同じ immutable artifact reference と順序付き `used_range_cue_digest` 配列が FR-30 の cutplan、FR-22 の telop、FR-25 の preflight、queue / line、FR-33 の review fingerprint で再利用され、downstream が resolver を再実行しない
- [ ] 進捗と失敗が job ID、range index、全区間数、cache hit / miss、partial / failed status、retry 可否とともに表示され、runtime 不備・timeout・malformed output・cache corruption は日本語で fallback または停止を示す
- [ ] 代表素材 3〜5 本の A/B で YouTube VTT と whisper.cpp の精度・固有名詞・境界確認・処理時間を記録し、固定 gold transcript / 固有名詞表、事前宣言した改善閾値・wall time・peak memory budget、採用モデルと Go / No-Go 根拠を docs に残す
- [ ] 字幕なし・低品質字幕の全編 Whisper、47 本の一括 backfill、local video 入力、asset ID 移行は S9 初版の受け入れ対象に含めず、将来フェーズとして明記されている

### AC-38: タイトル 3 方向と概要欄必須構成（FR-37 / P6）

- [ ] 新規テロップ台本生成 1 回の出力に、固定順の検索明快型・仕事影響型・好奇心型のタイトル候補がちょうど 3 件含まれ、非空・重複・100 文字・半角山カッコの境界がテストされている
- [ ] 日本語タイトル 18〜32 文字の範囲外は警告されるが保存可能で、既存の 1〜2 件候補を持つ台本は読み込める。新規生成では不足を成功扱いにせず、再生成または手動補完を日本語で案内する
- [ ] ショート専用テンプレートが無い場合だけ、`{{description}}`、`{{source_title}}`、`{{source_url}}` と固定 CTA 文「チャンネル登録は動画下のチャンネル名からお願いします。」を含む既定テンプレートが原子的に作成され、既存テンプレートは自動上書きされない
- [ ] 合成済み説明文が生成説明、元動画タイトル、開始秒付き元動画 URL、固定 CTA 文の完全一致を含み、1 項目でも欠ける場合は不足箇所を示す日本語エラーで preview / operation / job / API 開始前に拒否される
- [ ] ユーザーが確認画面で最終説明文を編集して必須項目を削除した場合、投稿確認ダイアログを開く直前と確定後の両方で再検証され、古い fingerprint・snapshot・本文を再利用しない
- [ ] 期待する 4 項目、template bytes fingerprint、`meta.json` fingerprint が preview / content snapshot / fingerprint に不変 snapshot として保存され、確定後は template / meta を再読込せず同じ snapshot で再検証する。変更を反映する場合は preview から作り直す
- [ ] 確認ダイアログの最終本文、content snapshot、`videos.insert` の `snippet.description` が同一で、半角山カッコ・5000 bytes・100 文字タイトル・500 文字タグの既存安全契約と後方互換テストが通る
- [ ] P6 の自動テストは YouTube API をモックし、実 upload・実公開データ変更・追加の Codex 呼び出しを行わない

### AC-39: 関連動画の Studio 手動確認（FR-38 / P6）

- [ ] upload operation が後方互換な `related_video_status` と `related_video_confirmed_at` を持ち、`not_ready` / `pending` / `confirmed` と確認時刻を lock + atomic write で永続化する。対象 ID は既存 `source_video_id` / `video_id` を唯一の正本として再利用し、重複 field を追加しない
- [ ] upload 成功前は `not_ready`、成功後は `pending` となる。legacy の欠落 field は `state=uploaded` かつ `video_id` ありだけ `pending`、それ以外は `not_ready` に移行し、再起動復元から `confirmed` を推測しない
- [ ] UI に Shorts の Studio 編集先、設定対象の元動画、手順が日本語で表示され、対象 2 ID を示す確認ダイアログの確定時だけローカル状態が `confirmed` になる
- [ ] `confirmed` への更新は YouTube API、ブラウザ自動操作、upload を呼ばず、二重クリック・壊れた queue・対象 ID 不一致を fail closed にする
- [ ] 未確認件数と operation ごとの状態が再起動後も復元され、既存 queue JSON・既存投稿・publication poll・予約枠・attempt 台帳に回帰が無い
- [ ] `pending` は要対応として表示するが、既存 `publishAt` を取消・延期・変更せず、未確認を理由に publication poll や予定公開を技術的に停止しない

### AC-36: 投稿枠の複数化と既定値設定（FR-28 v3.2 / FR-20 v3.2 / P5）

- [x] `daily_times` に複数時刻を設定でき、`assign_next_slot` が日内の枠を時刻順に埋めてから翌日に進む
- [x] 既存の単一 `daily_time` 設定が読み込み互換で動き、回帰が無い
- [x] 重複時刻・不正形式が日本語エラーで拒否される
- [x] 設定ページで枠リストとショート既定値（レイアウト・プリセット）を編集・保存できる

---

## 用語集（v3 追加分）

| 用語 | 説明 |
|------|------|
| ステッパー | （v3.1 で廃止）動画詳細ページ上部の、パイプライン進捗を示す UI（字幕 → チャプター → 候補 → ショート → 概要欄）。FR-17 v3.1 で状態サマリー + 作業切り替えに置き換えた |
| ワークスペース | v3.1 の動画詳細ページで、作業切り替えにより 1 つだけ表示される作業領域（素材候補 / ショート作成 / 公開・投稿） |
| 状態サマリー | 動画詳細ページ上部の読み取り専用カード 3 枚（素材候補 / ショート / 公開準備）。操作の入口にはしない |
| 予約可能（ショート） | 最新の検証済み量産 manifest にある成功 item のうち出力ファイルが存在するもの。予約投稿への誘導はこの指標だけを使う。「生成ファイル」（`shorts/output` の全 mp4）とは区別する |
| 詳細・再生成 | 動画詳細ページ最下部の補助領域。字幕全文・チャプター本文・コピー・再生成・技術ログ・元動画管理を集約する |
| サブ区間提案（カットプラン） | 10〜15 分の切り抜き候補の中から、ショート 1 本分（合計 180 秒以内）に収まる小区間を AI が提案したもの。人が採否と境界を確認してから連結生成に使う |
| テロップ台本 | Codex CLI が自動字幕を基に誤字修正・分割・行全体に対する強調行フラグの付与まで行った、焼き込み前のテキスト案 |
| queue fingerprint | FR-26 の候補・区間・レイアウト・プリセット等の選択 snapshot を識別する既存 fingerprint。review fingerprint とは役割を分ける |
| review fingerprint | テロップ本文・強調フラグ・台本メタデータと対象ショートを識別し、人の台本確認を現在値へ結び付ける fingerprint |
| ライン状態 | 作成中ショート 1 本の現在工程、各 fingerprint、人の確認記録、upload operation ID を保持する再起動可能な状態 |
| TranscriptArtifact | YouTube VTT または選択区間の whisper.cpp 結果を、取得元・モデル・設定・絶対時刻 cue・cue digest・入力 / 成果物 fingerprint とともに固定した不変の字幕成果物 |
| cue digest | artifact の絶対時刻 cue と本文を canonical 化して得る digest。候補・cutplan・台本・review の入力範囲ごとに伝播し、使用範囲の変更を検出する |
| transcript resolver | 粗い候補探索には YouTube VTT、選択済み区間には有効な Whisper artifact を返し、不一致や破損を高精度扱いにしない解決器 |
| 精査済み字幕 | S9 で選択した親候補区間だけを whisper.cpp で再文字起こしした artifact。Whisper の時刻は境界の唯一の正本ではなく、人確認用の材料である |
| 高精度字幕 fallback | runtime 不備や cache 不一致時に、精査済みと偽らず既存 YouTube VTT を明示的に使う経路 |
| タイトル 3 方向 | P6 で固定する検索明快型・仕事影響型・好奇心型の 3 候補。新規生成は同じ Codex 呼び出し内で固定順に返す |
| 関連動画確認状態 | YouTube Studio で Shorts の関連動画を元動画へ設定した事実を、`not_ready` / `pending` / `confirmed` で upload operation に保存するローカル状態 |
| フックタイトル | ショート冒頭 1〜2 秒に表示する、視聴継続を促す大きなテロップ |
| キュー量産 | 複数のショート候補を選択し、まとめてバックグラウンド生成する操作（内部では単一ジョブが順次処理する） |
| スケジュールポリシー | 「毎日 18:00 に 1 本」のような、予約投稿の自動割り当てルール |
| 非公開ロック | 未審査の YouTube API プロジェクトからのアップロードが自動的に非公開のまま固定される仕様。審査完了まで `publishAt` による予約公開が機能しない可能性がある |

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-08-03 | **P6 投稿メタデータ品質ゲートを追加。** FR-37 / AC-38 に同一 Codex 呼び出し内のタイトル固定 3 方向、概要欄の生成説明・チャンネル登録 CTA・元動画タイトル・開始秒付き URL、合成時と投稿確定時の再検証を定義。FR-38 / AC-39 に YouTube API 自動設定を行わない Studio 手動確認と `not_ready` / `pending` / `confirmed` の永続状態を定義し、P4 の歴史的 fallback と P6 投稿ゲートの優先関係を明記した |
| 2026-08-03 | **AC-33 完了。** ジョブエラーを必須 6 field の構造化通知へ移行し、上部 1 行要約、対象動画導線、動画別直近 3 件、上限付き global 要約、現在動画だけの技術詳細表示を実装。長い ffmpeg log の実ブラウザ確認、全件テスト、独立レビューを通過した |
| 2026-08-03 | **AC-31 / AC-35 / AC-36 受け入れ完了。** 実ブラウザで 3 ワークスペース、左パネル 4 状態、折り畳み、確認失効、生産ライン 3 周を確認した。09:00 / 13:00 / 18:00（Asia/Tokyo）の複数枠設定と、既存予約を避ける空き枠順の 3 本予約を実機で確認し、全件テストと独立レビューを通過した |
| 2026-08-01 | **v3.2 UI 確定仕様。** 3 ワークスペースと 6 工程の責務、左パネルの常設プレビューと縮約工程、品質チェックの 4 分離、人確認の既定未チェック、review fingerprint による編集時失効、atomic / fail closed なライン状態、`SchedulePolicy.timezone` 基準の日次完了数を FR-17 / FR-31 / FR-33 と AC-31 / AC-35 に固定。完成イメージを視覚リファレンスとして追加 |
| 2026-08-01 | **v3.2 改訂。** 実運用すり合わせで目的を「毎日 3 本を品質担保して安定生産（ケイデンス×品質）」に固定（§1.3.1）。優先順位を①ショート生産ライン ②ハイライト（派生）③チャプター反映（保守のみ）と明記。FR-33（工程 UI・ゲート・行き止まりゼロ・既定値化）/ FR-34（区間内容の可視化 = S8）を新設し、FR-28 を複数枠 `daily_times` へ、FR-20 をライン既定値編集へ拡張。AC-34〜36 を追加。背景の実測: 切り抜き候補 142 / 142 本が 180 秒超で単発・量産経路に乗らず、サブ区間提案が事実上の入口。FR-31 は FR-33 の接続に統合 |
| 2026-08-01 | **v3.1 改訂。** 動画詳細ページの UX 監査（`.codex/audits/video-detail-ux/audit.md`）を受け、FR-17 を「パイプライン順の全セクション縦積み + 5 段ステッパー」から「状態サマリー + 3 ワークスペース（素材候補 / ショート作成 / 公開・投稿）+ 詳細・再生成」の作業選択型 IA へ改訂。チャプターを概要欄反映の入力データとして再定義し、チャプター状態表示表と反映記録の fingerprint 化を FR-21 に追加。FR-31（候補選択の引き継ぎ）/ FR-32（構造化エラー通知）/ AC-31〜AC-33 を新設。AC-19 は U2 時点の履歴として凍結 |
| 2026-08-01 | v3 完了後の追加要件として FR-30 / AC-30（切り抜き候補からのショート用サブ区間提案）を追加。長い候補を人の確認付きで刻み、FR-25 の連結経路へ渡す導線を定義。Codex 呼び出しは明示 submit 時のみ、境界正規化は FR-25 と同一基準、自動採用は禁止として固定 |
| 2026-08-01 | v3 完了後の追加要件として FR-29 / AC-29（ショート概要欄への元配信リンク・チャンネル URL 差し込み）を追加。長尺用テンプレートと分離し、preview 生成前の合成・開始秒付き URL・日本語エラーでの fail closed を固定 |
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
| 2026-08-03 | **S9-PLAN を確定。** 良好な YouTube VTT を親候補探索に残し、選択親候補区間だけを whisper.cpp 1.9.1 で精査する `TranscriptArtifact` / resolver / 永続 cache を追加要件化。`ja.vtt` は上書きせず、絶対時刻 cue・cue digest・取得元・モデル・設定・fingerprint を cutplan / telop / review へ伝播し、使用範囲だけを fail closed で失効させる契約を FR-22 / FR-25 / FR-30 / FR-33 / FR-35 / FR-36 / AC-30 / AC-35 / AC-37 に固定。全編 Whisper、字幕なし通常経路、ローカル動画・asset ID は将来フェーズへ分離した |
