# v2 実装エージェント指示プロンプト集

**対象計画:** [execution-plan-v2.md](./execution-plan-v2.md)
**作成:** 2026-07-30
**用途:** 各タスクの本文をそのままワーカーエージェントに渡す

---

## 0. 並列実行の全体像

### 0.1 並列化の制約

**最大の競合ポイントは `src/yt_live_kit/ui/app.py`（361 行）である。** v2 の全フェーズがこの 1 ファイルを触るため、素直に並列化するとマージが破綻する。

そこで次の構成を取る。

```
W0  要件改訂                       単独
     ↓
W1  services 層（新規ファイル中心）   並列 5
     ↓
W2  UI 骨格分割 + 非同期化           単独  ★ここが直列のボトルネック
     ↓
W3  ページ単位の機能追加             並列 3
     ↓
W4  V5/V6 の services 層            並列 2
     ↓
W5  V5/V6 の UI + CLI              並列 3
     ↓
W6  受け入れ・仕上げ                 単独
```

### 0.2 ウェーブ別タスク一覧

| ウェーブ | タスク ID | 内容 | 主な編集ファイル | 並列 |
|----------|-----------|------|------------------|------|
| **W0** | W0-1 | 要件・技術スタック改訂 | `docs/` のみ | 単独 |
| **W1** | W1-A | パイプライン分離 + 概要欄テンプレ | `services/pipeline.py`, `services/description.py`(新) | ✅ |
| | W1-B | ジョブ機構 | `services/jobs.py`(新) | ✅ |
| | W1-C | チャンネル一覧取得 | `services/channel.py`(新), `models/channel.py`(新) | ✅ |
| | W1-D | ストレージ管理 | `services/storage.py`(新) | ✅ |
| | W1-E | ffmpeg 共通化 | `services/ffmpeg.py` | ✅ |
| **W2** | W2-1 | UI 分割 + ステータスバー + 非同期実行 | `ui/` 全体 | 単独 |
| **W3** | W3-A | 実行ページの機能追加 | `ui/pages/run.py` | ✅ |
| | W3-B | チャンネルページ新設 | `ui/pages/channel.py`(新) | ✅ |
| | W3-C | 一覧・ストレージページ | `ui/pages/history.py` | ✅ |
| **W4** | W4-A | ハイライト services | `services/highlights.py`(新), `prompts/highlights.md`(新), `services/ffmpeg.py` | ✅ |
| | W4-B | ショート services | `services/shorts.py`(新), `services/subtitle_burn.py`(新) | ✅ |
| | W4-C | W3 レビュー残件の UI クリーンアップ | `ui/pages/history.py`, `ui/pages/channel.py`, `ui/components/results.py` | ✅ |
| **W5** | W5-A | ハイライト UI | `ui/pages/highlights.py`(新) | ✅ |
| | W5-B | ショート UI | `ui/pages/shorts.py`(新) | ✅ |
| | W5-C | CLI 追加分 | `commands/*.py`(新), `cli.py` | ✅ |
| **W6** | W6-1 | 受け入れ・README・版数 | 全体 | 単独 |

### 0.3 オーケストレーター向けルール（重要）

以下は **AGENTS.md の運用ルールから意図的に外す**。並列実行に伴う競合を避けるためである。

| 項目 | 通常ルール | ウェーブ実行時 |
|------|------------|----------------|
| 実行計画のチェック更新 | ワーカーがタスク完了時に `- [x]` にする | **ワーカーは触らない。** ウェーブ完了後にオーケストレーターがまとめて更新する（全員が同じファイルを編集して競合するため） |
| コミット | ワーカーがタスク単位でコミット | **タスクごとに専用ブランチを切る。** `v2/w1-a-pipeline` のような命名。ウェーブ完了後にオーケストレーターが順にマージする |
| ブランチ運用 | — | `git worktree` の利用を推奨（同一ディレクトリで並列作業させない） |

**モデル指定:** AGENTS.md §4.2 のとおり、実装・レビューとも `composer-2.5-fast` を指定する。

**ウェーブの閉じ方:** 全タスクのマージ後に `uv run pytest` が全件通ることを確認してから次のウェーブへ進む。1 件でも落ちたら次に進まない。

---

## 1. 共通ヘッダ（全プロンプトの先頭に付ける）

> 以下をコピーして、各タスク本文の前に貼り付けること。

```text
あなたは yt-live-kit リポジトリの実装ワーカーです。以下のルールに必ず従ってください。

## 必読ドキュメント（作業前に読む）
1. docs/execution-plan-v2.md — 本タスクが属するフェーズの詳細
2. docs/requirements.md — FR / NFR / AC
3. AGENTS.md — 出力ルールと品質基準

## 絶対に守るルール
- **変更してよいファイルは、タスク本文の「変更対象ファイル」に列挙されたものだけ。**
  それ以外のファイルは、読むのは自由だが編集してはいけない。並列で他のエージェントが
  作業しているため、範囲外の編集はマージ競合を起こす。
- **docs/execution-plan-v2.md のチェックボックスは更新しない。** オーケストレーターが行う。
- **UI にビジネスロジックを書かない。** 処理は services/ に置き、UI は呼ぶだけにする。
- **従量課金 API を呼ばない。** AI 連携は Codex CLI サブプロセスのみ（NFR-01）。
- **ユーザー向けエラーメッセージは必ず日本語**にする。スタックトレースをそのまま出さない。
- **半角の山カッコ `<` `>` を成果物テキストに出さない。** 必要なら全角 `〈〉` を使う。
- 新規依存パッケージを追加しない（yt-dlp / ffmpeg / Streamlit / pydantic / typer で完結させる）。
- 既存のテストを壊さない。`uv run pytest` が全件通る状態で完了すること。

## コーディング方針
- 既存コードの書き方に合わせる（型注釈、`from __future__ import annotations`、
  docstring は日本語 1 行、例外は日本語メッセージを持つ独自例外クラス）。
- 外部プロセス実行は `subprocess.run(..., capture_output=True, text=True, check=False)` で、
  戻り値を見て日本語例外に変換する（services/ytdlp.py のパターンを踏襲）。
- テストでは subprocess を必ずモックする。実際に yt-dlp / ffmpeg / codex を起動しない。

## 完了時の報告フォーマット（必ずこの形式で報告する）
### 実装したもの
- （箇条書き。ファイルパスと関数名を含める）
### テスト結果
- `uv run pytest` の結果（件数と所要時間）
### 設計判断
- 迷った点と、どちらを選んだか、その理由
### 次のタスクへの申し送り
- 後続タスクが知っておくべき公開 API のシグネチャ
- 未対応で残したこと（あれば）
```

---

## 2. W0: 要件・技術スタック改訂（単独）

**ブランチ:** `v2/w0-docs`

```text
【タスク W0-1】v2 の要件・技術スタックを改訂する

## 変更対象ファイル
- docs/requirements.md
- docs/tech-stack.md
（他のファイルは編集しない）

## 作業内容

### 1. docs/requirements.md §3 に FR-09〜FR-15 を追加する
docs/execution-plan-v2.md の「§7 追加する機能要件」に定義済みの表を、
既存 FR-01〜FR-08 と同じ書式（項目/内容の2列テーブル）で転記する。

### 2. docs/requirements.md §7 に AC-11〜AC-17 を追加する
docs/execution-plan-v2.md の「§8 追加する受け入れ基準」を、
既存 AC-01〜AC-10 と同じ書式（チェックボックス箇条書き）で転記する。

### 3. docs/requirements.md §6.1 の自動編集スコープを改訂する ★最重要
現在「自動編集（ジャンプカット、テロップ、BGM、まとめ編集）」が一括でスコープ外に
なっている。これを docs/execution-plan-v2.md §3.1 の表に置き換える。

解禁するもの: 複数区間の無加工連結 / 縦横比変換（ぼかし背景合成）/ 既存 VTT 字幕の焼き込み
スコープ外のまま: ジャンプカット / テロップ生成 / BGM・効果音 / トランジション・エフェクト

「既にある素材を固定ルールで並べ替え・変形する処理は解禁、新しい表現を生成する処理は
スコープ外のまま」という原則を文章で明記すること。境界を曖昧にしない。

### 4. docs/requirements.md §6.3（v2 展望）を整理する
本計画で実装する項目を §6.3 から削除し、代わりに v3 候補
（チャプター手動編集 UI / 全文検索 / 複数動画をまたぐ総集編）を記載する。

### 5. docs/requirements.md §5.2 のディレクトリ構成を更新する
docs/execution-plan-v2.md §6 の構成に差し替える
（_jobs/, _channels/, _config/, highlights/, shorts/ を追加）。

### 6. docs/tech-stack.md に技術方針を追記する
以下 3 点を、根拠付きで記載する。
- **ffmpeg 連結方針:** 区間連結時は必ず再エンコードする。`-c copy` はキーフレーム境界の
  問題で繋ぎ目にフリーズと音ズレが出るため採用しない。
- **Streamlit 非同期方針:** 長時間処理は threading でバックグラウンド実行し、状態は
  data/_jobs/*.json に永続化する。ワーカースレッドから st.* を呼ばない
  （ScriptRunContext が無く未定義動作になるため）。UI 側は st.fragment(run_every=) で
  ポーリングする。streamlit 1.60 使用中のため利用可能。
- **日本語字幕フォント:** ffmpeg の subtitles フィルタでは force_style に FontName を
  明示指定する。未指定だと macOS で豆腐（□）になる。
  優先順: Hiragino Sans → Noto Sans CJK JP → sans-serif。

### 7. AGENTS.md §5 の出力ルールに変更が不要であることを確認する
（変更は不要なはず。確認だけして報告に含める）

## Done 条件
- requirements.md と execution-plan-v2.md の間に矛盾がない
- 「何を作らないか」が requirements.md 上で明文化されている
- FR / AC の番号に重複・欠番がない
```

---

## 3. W1: services 層（並列 5）

> **5 タスクを同時に走らせてよい。** 編集ファイルが完全に分離している。
> いずれのタスクも `ui/app.py` を編集してはいけない（W2 で統合する）。

### W1-A: パイプライン分離 + 概要欄テンプレート

**ブランチ:** `v2/w1-a-pipeline`

```text
【タスク W1-A】パイプラインのステップ分離と概要欄テンプレート合成を実装する

対応: docs/execution-plan-v2.md V1-1 / V1-2 / V1-6 / V1-7（UI 部分は別タスク）

## 変更対象ファイル
- src/yt_live_kit/services/pipeline.py（改修）
- src/yt_live_kit/services/description.py（新規）
- tests/test_pipeline_partial.py（新規）
- tests/test_description.py（新規）
（ui/app.py は絶対に編集しない）

## 背景
現在 services/pipeline.py の run() は fetch → transcript → chapters → clips を
直列固定で実行する。チャプターの出来が悪いとき、作り直すには字幕取得からやり直すしかない。
Codex CLI 呼び出しは 1 回数十秒〜数分かかるため、これが日常運用で最も痛い。

## 実装内容

### 1. run() に実行ステップの選択を追加する
    def run(url, settings=None, on_progress=None, *,
            do_chapters: bool = True, do_clips: bool = True) -> PipelineResult

- fetch / transcript は成果物が既に存在すればスキップする。
  判定: data/{video_id}/meta.json と transcript/full.txt および compressed.txt の存在。
  ただし URL から video_id を得るには fetch が必要なので、
  services/ytdlp.py の extract_video_id() で URL から先に ID を割り出してから判定すること。
- スキップ時も on_progress は呼び、message を「（取得済みのためスキップ）」等にする。
- do_chapters=False のとき chapters_text は保存済みファイルから読む。
  ファイルも無ければ空文字を入れ、PipelineResult は成立させる。
- **既存の run(url) 呼び出しが引数デフォルトでそのまま動くこと（後方互換必須）。**
  tests/test_pipeline.py と tests/test_pipeline_clips_softfail.py が無改修で通ること。

### 2. regenerate() を新規追加する
    def regenerate(video_id: str, *, target: str,
                   settings=None, on_progress=None) -> PipelineResult

- target は "chapters" | "clips"（"highlights" は W4-A で追加するので今は受け付けない。
  未知の target は日本語の PipelineError にする）
- 保存済みの transcript/compressed.txt を入力に、指定ステップのみ再実行する
- **上書き前に既存成果物を .bak として退避する**
  （chapters/chapters.md → chapters/chapters.md.bak、clips/candidates.json → .bak）
- 必要ファイルが無い場合は「先に字幕の取得と整形を実行してください」という日本語エラー

### 3. services/description.py を新規作成する
    def build_description(video_id: str, settings=None) -> str
    def get_template_path(settings=None) -> Path
    def save_template(text: str, settings=None) -> Path

- テンプレートの置き場: data/_config/description_template.txt
  （prompts/ ではない。ユーザーが編集する運用データのため）
- テンプレート内の {{timeline}} をチャプター本文に置換して返す
- テンプレート未設置（ファイルが無い）なら、チャプター本文をそのまま返す
- チャプターも無い場合は日本語の DescriptionError

### 4. テストを書く
- tests/test_pipeline_partial.py:
  フラグの組み合わせ 4 通りで、呼ばれる関数が正しいこと（fetch/transcript/chapters/clips を
  すべて monkeypatch でモック）。既存成果物ありの場合のスキップ動作。
  regenerate の .bak 退避。未知 target のエラー。
- tests/test_description.py:
  テンプレート有り（{{timeline}} 置換）、テンプレート無し、チャプター無しの 3 ケース。

## Done 条件
- uv run pytest が全件通る（既存テストを 1 件も壊さない）
- 「チャプターだけ再生成」で字幕再取得も Codex の切り抜き候補呼び出しも走らない
```

### W1-B: ジョブ機構

**ブランチ:** `v2/w1-b-jobs`

```text
【タスク W1-B】バックグラウンドジョブ機構を実装する

対応: docs/execution-plan-v2.md V2-1 / V2-2 / V2-6（UI 統合は別タスク）

## 変更対象ファイル
- src/yt_live_kit/services/jobs.py（新規）
- tests/test_jobs.py（新規）
（ui/app.py・services/pipeline.py・services/batch.py は編集しない）

## 背景
現在 ui/app.py は st.status 内で同期実行しており、処理中はページ全体がブロックされ、
完了すると進捗表示が畳まれて消える。Codex CLI 呼び出しが数分かかるため体感品質に直結する。
このモジュールは後続の全機能（ハイライト生成・ショート生成）の土台になる。

## 実装内容

### 1. JobState を定義する
@dataclass で以下のフィールドを持つ:
  job_id: str            # uuid4 の hex
  kind: str              # "single" | "batch" | "regenerate" | "highlights" | "shorts"
  status: str            # "running" | "done" | "failed" | "interrupted"
  video_id: str | None
  title: str | None
  stage: str | None      # pipeline の STAGE_* をそのまま入れる
  message: str           # 日本語の現在状況
  current: int           # 進捗の分子（一括処理の件数、区間番号など）
  total: int             # 進捗の分母。不明なら 0
  started_at: datetime
  finished_at: datetime | None
  error: str | None      # 日本語メッセージ
  result_ref: str | None # 完了後に UI が結果を復元するためのキー（通常は video_id）

pydantic ではなく dataclass + 手書きの to_dict/from_dict でよい
（models/ は pydantic だが、jobs は services 内部データのため）。

### 2. 永続化 API
    def create_job(kind, *, video_id=None, title=None, total=0, settings=None) -> JobState
    def update_job(job_id, *, settings=None, **fields) -> JobState
    def read_job(job_id, settings=None) -> JobState | None
    def list_jobs(settings=None) -> list[JobState]
    def get_active_job(settings=None) -> JobState | None   # status=="running" の最新1件
    def cleanup_finished(older_than_hours=24, settings=None) -> int

- 保存先: data/_jobs/{job_id}.json
- **書き込みは必ず「一時ファイルに書いて os.replace() で置換」する。**
  UI が st.fragment で毎秒読むため、途中状態を読ませてはいけない。
- 読み込み時に JSON が壊れていたら例外を投げず None を返す（UI を落とさない）

### 3. ワーカー起動 API
    def start_job(kind, target_fn, *, video_id=None, title=None, total=0,
                  settings=None, **kwargs) -> str   # job_id を返す

- threading.Thread(daemon=True) で target_fn を実行する
- target_fn には `report` という callable を kwargs で渡す。
  report(stage=None, message=None, current=None, total=None) を呼ぶと update_job される。
  （target_fn 側が jobs モジュールを import しなくて済むようにする）
- 例外を捕捉し、status="failed"、error に日本語メッセージを入れて終了する。
  想定外の例外は「予期しないエラーが発生しました。しばらくしてから再度お試しください。」
  とし、詳細は data/_jobs/{job_id}.log に書く。
- 正常終了時は status="done"、finished_at をセットする
- **ワーカースレッド内から streamlit を一切 import・呼び出ししないこと。**

### 4. 同時実行の制限
    def is_busy(settings=None) -> bool
- get_active_job() が None でなければ True
- start_job は is_busy() が True のとき JobBusyError（日本語メッセージ）を送出する

### 5. 孤児ジョブのクローズ
    def close_orphans(settings=None) -> list[str]
- status=="running" かつプロセスが存在しないジョブを "interrupted" にする
- プロセス生存判定は難しいので、**単純に「起動時に running のものは全部 interrupted」**
  とする（同時実行 1 件のため、これで実害がない）。この判断を docstring に明記する
- 戻り値は interrupted にした job_id のリスト

### 6. テストを書く
tests/test_jobs.py:
- create → update → read の往復
- os.replace による原子的書き込み（壊れた JSON を置いても read_job が None を返す）
- start_job が report コールバックで状態を更新すること
  （target_fn は即座に終わるダミー関数。thread.join(timeout=5) で必ず待つ）
- target_fn が例外を投げたとき status=="failed" で日本語 error が入ること
- is_busy / JobBusyError
- close_orphans

**スレッドを起こすテストは必ず終了を待つこと。** join せずに終わるとテストがフレークする。

## Done 条件
- uv run pytest が全件通る
- jobs.py 内に streamlit の import が 1 つも無い（grep で確認）
```

### W1-C: チャンネル一覧取得

**ブランチ:** `v2/w1-c-channel`

```text
【タスク W1-C】チャンネルの配信アーカイブ一覧取得を実装する

対応: docs/execution-plan-v2.md V3-1 / V3-2 / V3-5（UI・CLI は別タスク）

## 変更対象ファイル
- src/yt_live_kit/services/channel.py（新規）
- src/yt_live_kit/models/channel.py（新規）
- tests/test_channel.py（新規）
（ui/app.py・cli.py は編集しない）

## 背景
YouTube Data API も OAuth も Cookie も使わない。yt-dlp の --flat-playlist だけで
チャンネルの配信アーカイブ一覧が取れる。追加依存ゼロで、NFR-01（従量課金 API 禁止）と
requirements §6.1（YouTube Data API 不使用）を維持できる。

## 実装内容

### 1. models/channel.py
pydantic BaseModel で ChannelVideo を定義する:
  video_id: str
  title: str
  url: str
  duration: int | None      # 秒
  upload_date: str | None   # "YYYYMMDD"

ChannelListDocument:
  channel_url: str
  handle: str
  fetched_at: datetime
  videos: list[ChannelVideo]

### 2. services/channel.py

    def normalize_channel_url(text: str) -> tuple[str, str]   # (url, handle) を返す

受け付ける入力と正規化結果:
  "@handle"                                   → https://www.youtube.com/@handle/streams
  "handle"（@ なし、英数字とアンダースコア） → https://www.youtube.com/@handle/streams
  "https://www.youtube.com/@handle"           → .../@handle/streams
  "https://www.youtube.com/@handle/videos"    → .../@handle/streams
  "https://www.youtube.com/@handle/streams"   → そのまま
  "https://www.youtube.com/channel/UCxxxx"    → .../channel/UCxxxx/streams
  "https://www.youtube.com/c/name"            → .../c/name/streams
不正な入力は日本語の ChannelError を送出する。
handle はキャッシュのファイル名に使うので、英数字・ハイフン・アンダースコア以外を
除去した安全な文字列にすること。

    def list_archives(channel_url: str, *, limit: int = 50, settings=None)
        -> ChannelListDocument

- 実行コマンド:
  yt-dlp --flat-playlist --dump-json --playlist-end {limit} {正規化済みURL}
- **出力は JSON Lines（1 行 1 動画の JSON）である。行ごとに json.loads すること。**
  出力全体を丸ごと json.loads してはいけない。
- 空行と JSON パース失敗行はスキップする（全行失敗なら ChannelError）
- 取り出すキー: id, title, duration, upload_date, url
  url が無い場合は https://www.youtube.com/watch?v={id} を組み立てる
- 失敗時の日本語エラー例:
  「チャンネルが見つかりませんでした。URL またはハンドル名を確認してください。」
  「このチャンネルには公開されたライブ配信アーカイブがありません。」
  「一覧の取得に失敗しました。yt-dlp を最新版に更新して再実行してください。」

    def save_cache(doc: ChannelListDocument, settings=None) -> Path
    def load_cache(handle: str, settings=None) -> ChannelListDocument | None

- 保存先: data/_channels/{handle}.json
- レート制限対策（NFR-05）として、UI は既定でキャッシュを使い、
  明示的な再取得でのみ list_archives を呼ぶ想定。この意図を docstring に書く。

    def mark_processed(doc, settings=None) -> list[tuple[ChannelVideo, bool]]

- services/history.py の list_processed_videos() と突き合わせ、
  (動画, 処理済みか) のタプルのリストを返す

### 3. テストを書く
tests/test_channel.py:
- normalize_channel_url の全パターン（上記 7 種 + 不正入力）
- JSON Lines のパース（正常 3 行 / 空行混在 / 壊れた行混在 / 全行壊れ）
- yt-dlp が非ゼロ終了したときの日本語エラー
- キャッシュの保存・読み込み往復
- mark_processed の突き合わせ
subprocess.run は必ず monkeypatch でモックする。実際に yt-dlp を起動しない。

## Done 条件
- uv run pytest が全件通る
- --cookies-from-browser を使っていない
- 限定公開・メンバー限定を取得しようとする実装が入っていない
```

### W1-D: ストレージ管理

**ブランチ:** `v2/w1-d-storage`

```text
【タスク W1-D】ストレージ容量の集計と元動画キャッシュの削除を実装する

対応: docs/execution-plan-v2.md V4-1 / V4-5（UI は別タスク）

## 変更対象ファイル
- src/yt_live_kit/services/storage.py（新規）
- tests/test_storage.py（新規）
（ui/app.py・services/ffmpeg.py は編集しない）

## 背景
services/ffmpeg.py の _ensure_source_video() は切り出しのたびに元動画をフル解像度で
data/{video_id}/clips/source/ にダウンロードし、削除しない。2 時間配信 1 本で数 GB になる。
後続の V5（ハイライト）・V6（ショート）は中間ファイルをさらに生成するため、
先にこの機能を入れておく必要がある。

## 実装内容

### 1. データ構造
@dataclass VideoStorage:
  video_id: str
  title: str | None
  source_bytes: int      # clips/source/
  output_bytes: int      # clips/output/ + highlights/output/ + shorts/output/
  intermediate_bytes: int # highlights/segments/
  other_bytes: int       # 上記以外（字幕・テキスト・meta 等）
  total_bytes: int

@dataclass StorageSummary:
  total_bytes: int
  videos: list[VideoStorage]   # total_bytes の降順

### 2. API
    def dir_size(path: Path) -> int
    def summarize(settings=None) -> StorageSummary
    def purge_source(video_id: str, settings=None) -> int      # 削除したバイト数
    def purge_sources_older_than(days: int, settings=None) -> list[tuple[str, int]]
    def format_bytes(n: int) -> str    # "3.2 GB" のような日本語表示用文字列

### 3. 安全性（最重要）
- **削除する前に、対象パスを Path.resolve() で正規化し、
  settings.data_dir.resolve() 配下であることを必ず検証する。**
  配下でなければ StorageError（日本語）を送出し、削除しない。
- **削除してよいのは clips/source/ と highlights/segments/ だけ。**
  chapters/ transcript/ subtitles/ meta.json clips/output/ highlights/output/ shorts/output/
  は絶対に削除しない。この一覧を定数として定義し、それ以外は触らない実装にする。
- purge_sources_older_than の基準日時は meta.json の fetched_at を使う。
  meta.json が読めない動画はスキップする（削除しない）。
- ディレクトリが存在しない場合は 0 を返す（例外にしない）

### 4. テストを書く
tests/test_storage.py:
- tmp_path に擬似 data ディレクトリを組み立てて dir_size / summarize を検証
- purge_source が clips/source/ だけを消し、chapters/ transcript/ clips/output/ が
  残ることを確認する ★このテストは必須
- data_dir 外のパスを渡したときに StorageError になること ★このテストは必須
- purge_sources_older_than の日数境界と、meta.json 破損時のスキップ
- format_bytes（B / KB / MB / GB の境界）

## Done 条件
- uv run pytest が全件通る
- 成果物が消えないことがテストで保証されている
- data_dir 外を削除しようとすると必ず失敗する
```

### W1-E: ffmpeg 共通化

**ブランチ:** `v2/w1-e-ffmpeg`

```text
【タスク W1-E】ffmpeg モジュールの共通ヘルパーを公開し、区間エンコード関数を追加する

対応: docs/execution-plan-v2.md V5-3 の前提整備

## 変更対象ファイル
- src/yt_live_kit/services/ffmpeg.py（改修）
- tests/test_ffmpeg.py（追記）
（他は編集しない）

## 背景
後続の W4-A（ハイライト連結）と W4-B（縦型ショート）が、どちらも
「元動画の確保」「ffmpeg 実行とログ保存」「区間の再エンコード切り出し」を必要とする。
現在これらは services/ffmpeg.py のプライベート関数（_ensure_source_video、
_save_command_log、_find_ffmpeg）になっており、外から使えない。
W4-A / W4-B を並列で走らせるために、先にここを整える。

## 実装内容

### 1. プライベート関数を公開する（後方互換を保つ）
- _find_ffmpeg → find_ffmpeg
- _save_command_log → save_command_log
- _ensure_source_video → ensure_source_video
- _load_meta → load_meta

旧名は新名を呼ぶだけの薄いエイリアスとして残す（既存テストと内部呼び出しを壊さないため）。

### 2. encode_segment() を新規追加する
    def encode_segment(source_path: Path, output_path: Path,
                       start_sec: int, end_sec: int, *,
                       ffmpeg_path: str = FFMPEG_DEFAULT,
                       scale: str | None = None,
                       extra_filters: list[str] | None = None,
                       preset: str = "medium", crf: int = 20) -> Path

- **-ss を -i の「後ろ」に置く精密シークにすること。**
  -i の前に置く高速シークは先頭がズレる。ここは品質に直結する。
- 常に再エンコードする: -c:v libx264 -preset {preset} -crf {crf} -c:a aac -b:a 192k
- scale が指定されたら -vf の先頭に scale=... を入れる
  （可変解像度配信の区間で解像度が揃わない対策）
- extra_filters は scale の後ろに順に連結する（ショート生成が使う）
- 音声が無い動画でも失敗しないよう、-c:a aac は音声ストリームがある場合のみ効くよう
  ffmpeg の既定挙動に任せる（明示的な -an は入れない）
- 実行後、save_command_log でログを残す
- 失敗時は FfmpegError（日本語 + ログパス）

### 3. build_concat_list() を新規追加する
    def build_concat_list(segment_paths: list[Path], list_path: Path) -> Path

- ffmpeg の concat demuxer 用リストファイルを生成する
- 各行: file '{絶対パス}'
- **パスに含まれるシングルクォートを ' → '\'' でエスケープすること**
- 空リストなら FfmpegError

### 4. concat_segments() を新規追加する
    def concat_segments(segment_paths: list[Path], output_path: Path, *,
                        ffmpeg_path: str = FFMPEG_DEFAULT,
                        log_dir: Path | None = None) -> Path

- ffmpeg -y -f concat -safe 0 -i {list} -c copy {output}
- ここは -c copy でよい（encode_segment で既に全区間が同一パラメータに揃っているため）
- リストファイルは output_path と同じディレクトリに concat.txt として作り、成功後に削除する
- 失敗時は FfmpegError（日本語 + ログパス）

### 5. 既存 cut_clip() は変更しない
シグネチャも挙動もそのまま。既存の UI とテストが動き続けること。

### 6. テストを追記する
tests/test_ffmpeg.py に追加:
- encode_segment のコマンド組み立て（-ss が -i の後ろにあること ★必須）
- scale / extra_filters が -vf に正しい順で入ること
- build_concat_list の出力内容とシングルクォートのエスケープ ★必須
- concat_segments のコマンド組み立て
- 既存の build_ffmpeg_command / cut_clip のテストが無改修で通ること
subprocess.run はモックする。

## Done 条件
- uv run pytest が全件通る（既存 tests/test_ffmpeg.py を壊さない）
- 後続タスクが import できる公開 API が揃っている
- 報告に、公開した関数のシグネチャ一覧を必ず含めること（W4-A / W4-B が参照する）
```

---

## 4. W2: UI 骨格分割 + 非同期化（単独）★ボトルネック

**ブランチ:** `v2/w2-ui-core`
**前提:** W1 の全タスクがマージ済みで `uv run pytest` が通っていること

> このタスクだけは並列にできない。`ui/app.py` を分割し、以降のウェーブで
> ページ単位の並列作業を可能にするための土台を作る。**丁寧にやること。**

```text
【タスク W2-1】UI をページ分割し、非同期実行と常駐ステータスバーを導入する

対応: docs/execution-plan-v2.md V2-3 / V2-4 / V2-5

## 変更対象ファイル
- src/yt_live_kit/ui/app.py（分割・改修）
- src/yt_live_kit/ui/pages/__init__.py（新規）
- src/yt_live_kit/ui/pages/run.py（新規）
- src/yt_live_kit/ui/pages/history.py（新規）
- src/yt_live_kit/ui/components/__init__.py（新規）
- src/yt_live_kit/ui/components/status_bar.py（新規）
- src/yt_live_kit/ui/components/results.py（新規）
- src/yt_live_kit/ui/state.py（新規）
- src/yt_live_kit/services/batch.py（ジョブ機構への接続のみ）
- tests/test_ui_app.py（改修）
（services/ の他ファイルは編集しない）

## 目的
現在の ui/app.py（361行）は、実行・一覧・結果表示・進捗がすべて 1 ファイルに入っている。
今後 6 つの機能が追加されるため、このままでは破綻する。
ページ単位のモジュールに分割し、以降の作業を並列化できるようにする。

## 実装内容

### 1. ディレクトリ構成
src/yt_live_kit/ui/
  app.py                  # エントリのみ。ページ登録とタブ構成だけを持つ
  state.py                # session_state のキー定数と get/set ヘルパー
  components/
    status_bar.py         # 常駐ステータスバー（fragment）
    results.py            # 結果表示（タイムライン・全文・切り抜き候補）
  pages/
    run.py                # 実行タブ
    history.py            # 処理済み一覧タブ

**タブ構成は 3 つに保つ:「実行」「処理済み一覧」。**
（チャンネルタブは W3-B で追加するので、この時点では 2 つ）

### 2. 既存機能を「挙動を変えずに」移設する ★最重要
まずリファクタリングだけを行い、動作が変わらないことを確認してから
非同期化に着手すること。以下は現状の挙動を維持する:
- 実行タブの単本 / 一括の切り替え
- 処理済み一覧の表示と「開く」
- タイムライン表示・ダウンロード
- 文字起こし全文の expander とダウンロード
- 切り抜き候補のラジオ選択と「切り出し」
- yt-dlp バージョン警告
- 日本語エラー表示

### 3. state.py
session_state のキーを文字列リテラルで散らさず、定数と関数にまとめる:
  SESSION_RESULT / SESSION_CUT_RESULT / SESSION_ACTIVE_JOB / SESSION_LAST_JOB
  get_result() / set_result() / clear_result() など

### 4. 常駐ステータスバー（components/status_bar.py）
    @st.fragment(run_every="1s")
    def render_status_bar() -> None

- services.jobs.get_active_job() を呼ぶ。**fragment 内でのファイル読み込みは 1 回だけ。**
  全ジョブの走査はしない（毎秒実行されるため）
- 実行中: 進捗バー + 「{kind の日本語名} — {message}（経過 {N} 秒）」
  total > 0 なら「{current}/{total}」も表示
- 実行中でない: 何も描画しない（st.empty 相当）
- 直前のジョブが done になった瞬間を検知したら、結果を session_state に載せて
  st.rerun() を呼ぶ（fragment 内の st.rerun() はフラグメントのみ再実行するため、
  ページ全体を更新するには st.rerun(scope="app") を使うこと）
- failed / interrupted のときは日本語エラーを st.error で出す

### 5. 実行の非同期化
- 「実行」ボタンは services.jobs.start_job() を呼んで即座に return する
- services.jobs.is_busy() が True のときはボタンを無効化し、
  「他の処理が実行中です。完了までお待ちください。」と表示する
- 進捗コールバックの接続: pipeline.run の on_progress(stage, message) を、
  jobs の report(stage=..., message=...) に橋渡しするアダプタ関数を書く
- **ワーカーに渡す関数の中で st.* を絶対に呼ばない。**
  完了後の結果表示は、UI 側が job の result_ref（video_id）を見て
  pipeline.load_result_from_disk() で復元する

### 6. 一括処理をジョブ機構に載せる
- services/batch.py の run_batch を start_job 経由で呼ぶ
- 進捗は report(current=i, total=n, message=...) で通知
- **batch.py 側のシグネチャは変えない。** UI 側でラップする形にする
  （どうしても変更が必要なら on_progress の引数を増やすだけに留め、報告に明記すること）

### 7. 起動時の孤児ジョブ処理
- app.py の先頭で services.jobs.close_orphans() を 1 回だけ呼ぶ
  （st.session_state のフラグで初回のみ実行）
- interrupted になったジョブがあれば
  「前回の処理が中断されています（{タイトル}）。必要なら再実行してください。」を表示

### 8. テストを改修する
tests/test_ui_app.py:
- 現在のテストが何を検証しているか確認し、分割後も等価な検証を維持する
- 新規: status_bar のレンダリング分岐（実行中 / 非実行中 / 失敗）を
  jobs をモックして検証
- Streamlit の実行が必要なテストは、可能なら純粋関数に切り出して検証する

## Done 条件（すべて満たすこと）
- uv run pytest が全件通る
- **UI から手で操作して、v1 の全機能が以前と同じように動く**（実機確認して報告に書く）
- 処理中に「処理済み一覧」タブへ移動しても処理が継続する
- ブラウザのタブを閉じて開き直しても、進行状況が復元される
- **ui/ 配下のどのファイルにもビジネスロジックが無い**（services を呼ぶだけ）
- **ワーカースレッドから st.* を呼んでいる箇所が 0 件**
  （`grep -rn "st\." src/yt_live_kit/services/` が空であることを確認して報告に書く）

## 報告に必ず含めること
- 分割後のファイル構成と、各ファイルの責務（1 行ずつ）
- 後続タスクがページを追加する手順（app.py にどう登録するか）
- components/results.py の公開関数シグネチャ
```

---

## 5. W3: ページ単位の機能追加（並列 3）

**前提:** W2 マージ済み

> 3 タスクとも別ファイルを触るため並列可能。ただし `ui/app.py` のタブ登録行だけ
> 競合しうる（W3-B のみ 1 行追加）。競合したら手で解決する。

### W3-A: 実行ページの機能追加

**ブランチ:** `v2/w3-a-run-page`

```text
【タスク W3-A】実行ページに実行対象の選択とコピー支援を追加する

対応: docs/execution-plan-v2.md V1-3 / V1-4 / V1-5 / V1-6（UI 部分）

## 変更対象ファイル
- src/yt_live_kit/ui/pages/run.py
- src/yt_live_kit/ui/components/results.py
- tests/test_ui_run_page.py（新規）
（ui/pages/history.py・ui/app.py・services/ は編集しない）

## 実装内容

### 1. 実行対象チェックボックス（V1-3）
- 単本モードに「チャプターを作る」「切り抜き候補を出す」を並べる（初期値は両方 ON）
- 両方 OFF のときは実行ボタンを disabled にする
- services.pipeline.run(url, do_chapters=..., do_clips=...) に渡す
  （W1-A で実装済み。シグネチャを確認してから使うこと）

### 2. 全文コピーボタン（V1-5）
- components/results.py の文字起こし全文 expander 内、ダウンロードボタンの隣に配置
- st.components.v1.html で navigator.clipboard.writeText を使う
- **st.code に全文を流さないこと。** 数万文字で描画が極端に重くなる。
  表示は現行の st.text_area(disabled=True) のまま維持する
- コピー対象テキストは JSON エンコードして JS に埋め込む（改行・引用符の混入対策）
- クリック後に「コピーしました」を 2 秒だけ表示する
- 同じ仕組みでタイムラインにもコピーボタンを付ける

### 3. 概要欄テンプレート（V1-6 の UI）
- タイムライン表示の下に「概要欄用テキストをコピー」ボタン
- services.description.build_description(video_id) の結果をコピーさせる
- テンプレート未設定時は「定型文が未設定です。data/_config/description_template.txt に
  {{timeline}} を含むテンプレートを置くと、まとめてコピーできます。」と案内する
- テンプレートの編集 UI は作らない（ファイル直接編集の運用でよい）

## Done 条件
- uv run pytest が全件通る
- 実機で、チャプターのみ / 切り抜き候補のみ / 両方 の 3 通りが正しく動く
- 全文コピーで、実際にクリップボードに全文が入る（実機確認して報告に書く）
```

### W3-B: チャンネルページ新設

**ブランチ:** `v2/w3-b-channel-page`

```text
【タスク W3-B】チャンネルタブを新設する

対応: docs/execution-plan-v2.md V3-3

## 変更対象ファイル
- src/yt_live_kit/ui/pages/channel.py（新規）
- src/yt_live_kit/ui/app.py（タブ登録の 1 行のみ。他の行は触らない）
- tests/test_ui_channel_page.py（新規）
（ui/pages/run.py・history.py・services/ は編集しない）

## 実装内容

タブ構成を「実行 / チャンネル / 処理済み一覧」の 3 つにする。

### 1. チャンネル一覧の取得
- チャンネル URL / ハンドル入力欄（プレースホルダ: @handle）
- 「一覧を取得」ボタン
- **既定でキャッシュを使う。** services.channel.load_cache() が返せばそれを表示し、
  「前回取得: YYYY-MM-DD HH:MM」と「再取得」ボタンを出す
- 「再取得」を押したときだけ services.channel.list_archives() を呼ぶ
  （レート制限対策 = NFR-05。この意図をコード上のコメントに残す）
- 取得件数の上限をセレクトボックスで選ばせる（20 / 50 / 100、既定 50）

### 2. 一覧表示
- services.channel.mark_processed() の結果を使う
- 各行: チェックボックス / タイトル / 尺（H:MM:SS）/ 投稿日 / 処理済みバッジ
- 「未処理のみ表示」トグル（既定 ON）
- 「すべて選択」「選択を解除」ボタン
- 選択件数をリアルタイム表示

### 3. 一括投入
- 「選択した N 本を処理」ボタン
- services.jobs.start_job() で一括処理ジョブを起動する
- services.jobs.is_busy() が True ならボタンを無効化し、日本語で理由を出す
- 進捗は W2 の常駐ステータスバーに出るので、このページで進捗 UI を作らない

### 4. エラー表示
- services.channel の ChannelError を捕捉し、日本語メッセージをそのまま st.error で出す
- 想定外の例外は「予期しないエラーが発生しました。」に丸める

## Done 条件
- uv run pytest が全件通る
- 実機で、実在するチャンネル 1 件の一覧取得 → 未処理 2 本を選択 → 一括投入 が動く
- 限定公開・メンバー限定動画が一覧に出ない
```

### W3-C: 一覧・ストレージページ

**ブランチ:** `v2/w3-c-history-page`

```text
【タスク W3-C】処理済み一覧に個別再生成とストレージ管理を追加する

対応: docs/execution-plan-v2.md V1-4 / V4-2 / V4-3 / V4-4

## 変更対象ファイル
- src/yt_live_kit/ui/pages/history.py
- tests/test_ui_history_page.py（新規）
（ui/pages/run.py・channel.py・ui/app.py・services/ は編集しない）

## 実装内容

### 1. 個別再生成ボタン（V1-4）
処理済み一覧の各行に追加する:
- 「チャプター再生成」— 成果物があれば「再生成」、無ければ「生成」と表記を変える
- 「切り抜き候補を生成」— 同上
- services.pipeline.regenerate(video_id, target="chapters"|"clips") を
  services.jobs.start_job() 経由で呼ぶ（W1-A のシグネチャを確認すること）
- 再生成は既存成果物を .bak に退避してから上書きする旨を、ボタンの下に小さく注記する

### 2. 容量表示（V4-2）
- 各行に元動画の容量バッジ「元動画: 3.2 GB」（無い場合は表示しない）
- services.storage.summarize() は全走査で重いので、
  **ページ表示ごとに毎回呼ばない。** st.cache_data(ttl=60) でキャッシュするか、
  「容量を計算」ボタンを押したときだけ計算する方式にする（どちらか選んで理由を報告する）
- 各行に「元動画を削除」ボタン
  - 押すと確認ダイアログ（st.dialog または 2 段階ボタン）を出す
  - 「チャプター・全文・切り抜き候補・切り出し済み動画は残ります」と明記する
  - services.storage.purge_source() を呼ぶ

### 3. ストレージセクション（V4-3）
一覧の下部に折りたたみで配置する:
- 合計容量と、容量上位 10 件の内訳（元動画 / 成果物 / 中間 / その他）
- 「N 日以上前の元動画をまとめて削除」
  - 日数入力（既定 30）
  - **実行前に対象件数と合計容量を必ず提示し、確認を取る**
  - services.storage.purge_sources_older_than() を呼ぶ
  - 結果を「N 件、合計 X GB を削除しました」と日本語で表示

### 4. 切り出し完了時の案内（V4-4）
components/results.py は W3-A が触るため、**このタスクでは触らない。**
代わりに、必要なら報告に「results.py に案内文の追加が必要」と申し送りする。

## Done 条件
- uv run pytest が全件通る
- 実機で、元動画を削除してもチャプター・全文・候補・切り出し済み mp4 が残る
- 容量計算がページ表示のたびに走らない（重くない）
```

---

## 6. W4: V5/V6 の services 層 + W3 残件（並列 3）

**前提:** W3 マージ済み（正確には W1-E があれば着手可能。W3 と並行させてもよい）

> **W1-E で公開された ffmpeg の API を使う。** 着手前に W1-E の報告にある
> シグネチャ一覧を確認すること。

> **W4-C は W4-A / W4-B と 1 ファイルも競合しない**（W4-A/B は `services/` のみ、
> W4-C は `ui/` のみ）。3 本を同時に走らせてよい。

### W4 着手前の確認事項（オーケストレーターがワーカーに伝えること）

`docs/v2-agent-prompts.md` 執筆時点と実装で差異がある。**実コードを正とすること。**

| 項目 | プロンプト本文の記述 | 実装（正） |
|------|----------------------|------------|
| `encode_segment` の引数名 | `source_path, output_path` | `source, output` |
| `encode_segment` の秒数型 | `int` | `float` |
| `regenerate` の target 追加 | 分岐を足すだけ | [`pipeline.py:55`](../src/yt_live_kit/services/pipeline.py) の `_REGENERATE_TARGETS` frozenset にも `"highlights"` を追加する必要がある |

W1-E で公開済みの ffmpeg API（W4-A / W4-B が使う）:

```python
FFMPEG_DEFAULT = "ffmpeg"
find_ffmpeg(ffmpeg_path: str = FFMPEG_DEFAULT) -> str
load_meta(video_dir: Path) -> VideoMeta
ensure_source_video(video_id: str, settings: Settings) -> Path
save_command_log(...)  # 実装を読むこと
encode_segment(source: Path, output: Path, start_sec: float, end_sec: float, *,
               ffmpeg_path=FFMPEG_DEFAULT, scale: str | None = None,
               extra_filters: list[str] | None = None,
               preset: str = "medium", crf: int = 20) -> Path
build_concat_list(segment_paths: list[Path], list_path: Path) -> Path
concat_segments(segment_paths: list[Path], output_path: Path, *,
                ffmpeg_path=FFMPEG_DEFAULT, log_dir: Path | None = None) -> Path
cut_clip(...)  # 既存。変更しないこと
```

`models/clips.py` の `ClipCandidate`（W4-A が同形式で作る）:
`id / title / start / end / duration_sec / reason`（すべて必須、start/end は文字列）

### W4-A: ハイライト services

**ブランチ:** `v2/w4-a-highlights`

```text
【タスク W4-A】ハイライト区間の選定と連結を実装する

対応: docs/execution-plan-v2.md V5-1 / V5-2 / V5-3 / V5-6

## 変更対象ファイル
- prompts/highlights.md（新規）
- src/yt_live_kit/services/highlights.py（新規）
- src/yt_live_kit/models/highlights.py（新規）
- src/yt_live_kit/services/ffmpeg.py（cut_and_concat の追加のみ）
- src/yt_live_kit/services/pipeline.py（regenerate の target に "highlights" を追加）
- tests/test_highlights.py（新規）
- tests/test_ffmpeg_concat.py（新規）
（ui/ 配下・services/shorts.py は編集しない）

## 実装内容

### 1. prompts/highlights.md
prompts/clips_suggest.md を読み、同じ構造・同じプレースホルダ方式で作る。
**clips_suggest.md との違いを明確にすること:**
- clips_suggest: 10〜15 分の「一続きの区間」を 2 件以上
- highlights:    **30 秒〜3 分の「山場」を 5〜10 個**

プロンプトに必ず含める条件:
- 出力は JSON のみ（前後に説明文を付けない）
- 区間は時系列順、互いに重複しない
- 合計尺の目安は 3〜10 分
- 各区間に、なぜ見どころなのかの reason を日本語で付ける
- **半角の山カッコ `<` `>` を使わない**（必要なら全角 〈〉）
- 出力スキーマは clips の candidates.json と同形式

### 2. models/highlights.py
pydantic で HighlightSegment / HighlightsDocument を定義する。
models/clips.py の ClipCandidate と同じフィールド構成にする
（id, title, start, end, duration_sec, reason）。継承でもコピーでもよいが、
clips.py 側は変更しないこと。

### 3. services/highlights.py
services/clips.py と services/ai_prompt.py のパターンをそのまま踏襲する。

    def suggest_highlights(video_id, settings=None, on_progress=None) -> HighlightsResult

- 圧縮テキスト読み込み → プロンプト結合 → Codex CLI 実行 → JSON 抽出 →
  バリデーション → 保存（data/{video_id}/highlights/segments.json）
- Codex CLI 未導入時は services/ai_prompt.py の CODEX_INSTALL_HINT と同じ形式で
  日本語のインストール手順とフォールバック手順を出す
- プロンプトファイルは data/{video_id}/prompt_highlights.txt に残す

    def validate_highlights(segments, *, video_duration=None) -> ValidationResult

以下をすべて検証する（1 つでも違反したら日本語メッセージ付きで失敗）:
- 区間が 2 個以上、20 個以下
- 各区間が 10 秒以上、5 分（300 秒）以下
- start < end
- 区間が時系列順で、互いに重複しない
- video_duration が渡されたとき、end が動画尺を超えない
- title と reason に半角 `<` `>` を含まない

タイムスタンプのパースは services/chapter_validator.py の
parse_timestamp_to_seconds() を再利用する（新規実装しない）。

### 4. services/ffmpeg.py に cut_and_concat() を追加する
    def cut_and_concat(video_id, segments, settings=None, *,
                       output_name="highlight.mp4",
                       ffmpeg_path=FFMPEG_DEFAULT,
                       keep_segments=False,
                       on_progress=None) -> ConcatResult

**必ず再エンコードしてから連結すること。-c copy で切ると繋ぎ目でフリーズと音ズレが出る。**
手順:
1. ensure_source_video() で元動画を確保（W1-E で公開済み）
2. 元動画の解像度を ffprobe で取得し、全区間で同一の scale を決める
   （可変解像度配信対策。ffprobe が使えない場合は scale なしで続行する）
3. 各区間を encode_segment() で切り出す（W1-E で追加済み）
   → data/{video_id}/highlights/segments/seg_001.mp4 …
   各区間の完了ごとに on_progress(i, total, "区間 i/total を処理中…") を呼ぶ
4. build_concat_list() → concat_segments() で連結
   → data/{video_id}/highlights/output/{output_name}
5. keep_segments=False なら中間ファイルを削除する（既定）

ConcatResult には video_id, output_path, command_log_path, segment_count,
total_duration_sec を持たせる。

### 5. services/pipeline.py の regenerate() に "highlights" を追加する
**この 1 箇所以外は pipeline.py を編集しないこと。**
W1-A が実装した regenerate の target に "highlights" を許可し、
suggest_highlights を呼ぶ分岐を足すだけ。

### 6. テストを書く
tests/test_highlights.py:
- validate_highlights の違反パターンを網羅
  （1個 / 21個 / 9秒 / 301秒 / start>=end / 逆順 / 重複 / 尺超過 / 半角<> ）★必須
- Codex 出力の JSON 抽出（前後にテキストが付いている場合、コードフェンス付きの場合）
- Codex 未導入時のメッセージ

tests/test_ffmpeg_concat.py:
- cut_and_concat が区間数ぶん encode_segment を呼ぶこと
- on_progress が区間ごとに呼ばれること
- keep_segments=False で中間ファイルが消えること
- 連結コマンドの組み立て
すべて subprocess をモックする。

## Done 条件
- uv run pytest が全件通る
- Codex CLI が失敗しても、チャプターと切り抜き候補に影響しない
  （services/pipeline.py の clips softfail と同じ方針）
- 報告に、cut_and_concat と suggest_highlights のシグネチャを明記すること（W5-A が使う）
```

### W4-B: ショート services

**ブランチ:** `v2/w4-b-shorts`

```text
【タスク W4-B】縦型ショート動画の生成を実装する

対応: docs/execution-plan-v2.md V6-1 / V6-2 / V6-5 / V6-6

## 変更対象ファイル
- src/yt_live_kit/services/subtitle_burn.py（新規）
- src/yt_live_kit/services/shorts.py（新規）
- src/yt_live_kit/config.py（フォント設定の追加のみ）
- tests/test_subtitle_burn.py（新規）
- tests/test_shorts.py（新規）
（services/ffmpeg.py・services/highlights.py・ui/ は編集しない。
 ffmpeg.py の関数は import して使うだけ）

## 実装内容

### 1. services/subtitle_burn.py — 区間字幕の生成
    def build_segment_subtitle(video_id, start_sec, end_sec, settings=None,
                               *, ffmpeg_path="ffmpeg") -> Path

- 入力: data/{video_id}/subtitles/ja.vtt
- 出力: data/{video_id}/shorts/subtitles/short_{start}_{end}.ass
- 手順:
  1. VTT をパースする（services/vtt_parser.py を読み、再利用できるなら再利用する。
     できない場合のみ最小限のパーサを書き、その理由を報告する）
  2. [start_sec, end_sec] に重なるキューだけを残す
  3. **各キューの時刻から start_sec を引く**（切り出し後の動画は 0 秒始まりのため）
     開始が start_sec より前のキューは 0 にクランプする
  4. ASS 形式で書き出す

**時刻オフセットは ffmpeg の -itsoffset に頼らず、ここで再計算すること。**
-itsoffset は挙動が入力形式に依存して不安定なため。

    def resolve_font(preferred: str | None = None) -> str

- 日本語フォントを解決する。優先順: 指定値 → Hiragino Sans → Noto Sans CJK JP → sans-serif
- 存在確認は `fc-list` があればそれを使い、無ければ macOS の
  /System/Library/Fonts と /Library/Fonts を走査する
- どれも見つからない場合は "sans-serif" を返しつつ、
  呼び出し側が警告を出せるよう戻り値とは別に判定できる関数
  `is_japanese_font_available() -> bool` も用意する

### 2. config.py にフォント設定を追加する
- Settings に subtitle_font: str | None = None を追加
- 環境変数 YTLK_SUBTITLE_FONT で上書きできるようにする
- **既存フィールドの型や既定値は変更しない**

### 3. services/shorts.py
    def build_short(video_id, start, end, settings=None, *,
                    layout: str = "blur",
                    burn_subtitles: bool = True,
                    output_name: str | None = None,
                    ffmpeg_path="ffmpeg",
                    on_progress=None) -> ShortResult

- layout は "blur"（既定）と "crop" の 2 種類
- **バリデーション: 区間は 10 秒以上 180 秒以下。** 範囲外は日本語の ShortsError
- start >= end も日本語エラー

フィルタグラフ:

  layout="blur":
    split[a][b];
    [a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=20[bg];
    [b]scale=1080:-1[fg];
    [bg][fg]overlay=(W-w)/2:(H-h)/2

  layout="crop":
    crop=ih*9/16:ih,scale=1080:1920

字幕焼き込み（burn_subtitles=True のとき）は上記の**後段**に連結する:
    subtitles={ass_path}:force_style='FontName={font},FontSize=54,
    PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=180'

**ASS のパスは ffmpeg のフィルタ構文でエスケープが必要**
（`:` と `'` と `\` と Windows のドライブレター）。
絶対パスをそのまま渡すとフィルタが壊れるので、
一時的にカレントを移すか、パスをエスケープする関数を用意すること。

- 元動画確保は services.ffmpeg.ensure_source_video() を使う（W1-E で公開済み）
- 実行は services.ffmpeg.encode_segment() の extra_filters 経由でも、
  独自の subprocess 呼び出しでもよい。どちらにせよ
  services.ffmpeg.save_command_log() でログを残すこと
- 出力: data/{video_id}/shorts/output/short_{start}_{end}.mp4（1080x1920）
- on_progress があれば「変換中…」「字幕を焼き込み中…」等を通知する

ShortResult: video_id, output_path, command_log_path, layout,
             burned_subtitles(bool), duration_sec

### 4. テストを書く
tests/test_subtitle_burn.py:
- 区間に重なるキューだけが残ること（前・後・またぎの各ケース）★必須
- 時刻オフセットが正しく引かれること、負にならないこと ★必須
- ASS の出力形式（ヘッダと Dialogue 行）
- resolve_font のフォールバック順

tests/test_shorts.py:
- blur / crop それぞれのフィルタグラフ文字列 ★必須
- 字幕あり / なしでフィルタが変わること
- 尺バリデーション（9秒 / 10秒 / 180秒 / 181秒）★必須
- ASS パスのエスケープ ★必須
subprocess はモックする。

## Done 条件
- uv run pytest が全件通る
- 報告に build_short のシグネチャを明記すること（W5-B が使う）
- 報告に、日本語フォントが見つからなかった場合の挙動を明記すること
```

### W4-C: W3 レビュー残件の UI クリーンアップ

**ブランチ:** `v2/w4-c-ui-cleanup`

```text
【タスク W4-C】W3 のレビューで残した UI 側の指摘 6 件を直す

対応: W3 レビュー指摘（V4-2 / V4-3 の精度改善と、V1-4 / V3-3 の細部）

## 変更対象ファイル
- src/yt_live_kit/ui/pages/history.py
- src/yt_live_kit/ui/pages/channel.py
- src/yt_live_kit/ui/components/results.py
- tests/test_ui_history_page.py（追記）
- tests/test_ui_run_page.py（追記）
（services/ は 1 行も編集しない。ui/pages/run.py・ui/app.py も触らない。
 W4-A / W4-B が services/ を同時に編集しているため、範囲外編集は競合する）

## 前提
main の HEAD は W3 マージ + V1-3 修正済みの状態で、uv run pytest は 205 件通る。
着手前に uv run pytest を実行し、205 件通ることを確認してから始めること。

## 修正1: 一括削除プレビューの件数が実際の削除と合わない ★最優先

ui/pages/history.py の preview_purge_sources_older_than() は
clips/source のバイト数（VideoStorage.source_bytes）だけを見て件数を数えている。
一方 services.storage.purge_sources_older_than() は
DELETABLE_REL_PATHS = (clips/source, highlights/segments) の両方を削除し、
削除バイトが 1 でもあれば結果に含める。

現在は highlights/segments が存在しないため実害が無いが、W4-A がこのディレクトリを
作った時点で「対象 N 件・合計 X GB」の提示と実際の削除結果がズレ始める。
**削除の確認ダイアログの数字なので、ズレたまま出荷してはいけない。**

- プレビューの集計を source_bytes + intermediate_bytes に変える
  （VideoStorage には intermediate_bytes が既にある。services 側は変更不要）
- source_bytes_map() 相当の関数名・戻り値も実態に合わせて見直す
  （「元動画のバイト数」ではなく「削除対象のバイト数」になるため）
- 一覧の行バッジ「元動画: 3.2 GB」は元動画の容量表示なので source_bytes のまま変えない。
  **集計用と表示用を混同しないこと。**
- 確認文言も実態に合わせる。中間ファイルも消える旨を日本語で明記する
  （「チャプター・全文・切り抜き候補・切り出し済み動画は残ります」は維持する）

## 修正2: 元動画の個別削除後に画面が更新されない

_render_row_actions() の「削除を実行」成功パスに st.rerun() が無いため、
削除に成功しても確認用の警告ブロックが出たまま成功メッセージが並び、
容量バッジも次の操作まで古い値のままになる。

- 成功メッセージを session_state に退避してから st.rerun() する
- rerun 後に一度だけ表示し、表示したら消す（同じメッセージが残り続けないこと）
- 既存の _SESSION_PURGE_CONFIRM / _SESSION_BULK_PREVIEW と同じ書き方に合わせる

## 修正3: 未使用の引数を消す

_render_row_actions() の source_bytes 引数が本体で一度も使われていない。
引数と呼び出し側の両方から削除する。

## 修正4: 文字起こしが無い行で再生成ボタンが必ず失敗する

処理済み一覧の「チャプターを生成」「切り抜き候補を生成」は、
services.pipeline.regenerate() が transcript/compressed.txt を必要とするため、
文字起こしが無い動画では押しても必ず日本語エラーで失敗する。

- ProcessedVideo.has_transcript が False の行では両ボタンを disabled にする
- st.button の help= に「先に字幕の取得と整形を実行してください。」を出す
- **has_transcript は transcript/full.txt の有無を見ており、
  regenerate が必要とする compressed.txt の有無とは一致しない。**
  つまりこれは「必要条件の先出しチェック」であって完全な保証ではない。
  services 側のエラーは今までどおり残すこと（UI で握りつぶさない）。
  この点をコード上のコメントに 1 行残す。

## 修正5: コピー成功表示の秒数を仕様に合わせる

ui/components/results.py の build_clipboard_copy_html() の
hide_after_ms 既定値が 3000 になっている。V1-5 の仕様は 2 秒なので 2000 にする。

## 修正6: チャンネルページの start_job だけ settings を渡していない

ui/pages/channel.py の一括投入は start_job(...) に settings を渡していないが、
ui/pages/history.py は settings=settings を渡している。挙動は同じだが非対称。
channel.py 側も get_settings() の結果を渡して揃える。

## テスト
純粋関数に切り出せるものは切り出して検証する
（既存 tests/test_ui_history_page.py の _MockColumn / patch の書き方に合わせる）。

tests/test_ui_history_page.py:
- 修正1: 元動画 0 バイト + 中間ファイルありの動画がプレビュー件数に入ること ★必須
- 修正1: 行バッジ用の元動画バイト数は中間ファイルを含まないこと ★必須
- 修正2: 削除成功後に st.rerun が呼ばれ、メッセージが session_state に載ること
- 修正4: has_transcript=False の行で両ボタンが disabled=True で呼ばれること ★必須

tests/test_ui_run_page.py:
- 修正5: hide_after_ms の既定値が 2000 で、生成 HTML に 2000 が含まれること

## やらないこと（スコープ外。手を出さない）
- 全文コピーの二重転送（text_area と components.html の両方に全文を送っている件）。
  修正するとコピー操作が 2 段階になり UX が悪化するため、意図的に現状維持とする。
- services/ の変更。W4-A / W4-B と競合する。
- 新機能の追加。

## Done 条件
- uv run pytest が全件通る（205 件 + 追加分。既存を 1 件も壊さない）
- git diff --stat が上記 5 ファイルだけであること（報告に貼ること）
- 修正1 について、「プレビューの件数と purge_sources_older_than の戻り値の件数が
  どういう条件で一致するか」を報告に 2〜3 行で説明すること
```

---

## 7. W5: V5/V6 の UI + CLI（並列 3）

**前提:** W4 マージ済み

### W5 着手前の確認事項（オーケストレーターがワーカーに伝えること）

W4 のレビュー修正（`d517c92` / `655c542`）で、W5 が使う公開 API が変わっている。
**プロンプト本文より実コードを正とすること。** 以下は実装から転記した現物である。

```python
# services/highlights.py
suggest_highlights(video_id, settings=None, *, on_progress=None,
                   prompt_only: bool = False, codex_path: str = "codex") -> HighlightsResult
# HighlightsResult: video_id / prompt_path / segments_path / used_codex / segments
load_segments_file(video_id, settings) -> HighlightsDocument | None   # 保存済み候補の読み込み

# services/ffmpeg.py
cut_and_concat(video_id, segments: list[HighlightSegment], settings=None, *,
               output_name="highlight.mp4", ffmpeg_path=FFMPEG_DEFAULT,
               ffprobe_path=FFPROBE_DEFAULT, keep_segments: bool = False,
               on_progress=None) -> ConcatResult
# on_progress は (current: int, total: int, message: str) の 3 引数。
# 区間ごとに前後 2 回 + 連結開始時に 1 回呼ばれる。
# ConcatResult: video_id / output_path / command_log_path / segment_count / total_duration_sec

# services/shorts.py
build_short(video_id, start: float, end: float, settings=None, *,
            layout: str = "blur", burn_subtitles: bool = True,
            output_name: str | None = None, ffmpeg_path=FFMPEG_DEFAULT,
            on_progress: Callable[[str], None] | None = None,
            keep_intermediate: bool = False) -> ShortResult
# on_progress は (message: str) の 1 引数。cut_and_concat とは形が違うので注意。
# ShortResult: video_id / output_path / command_log_path / layout /
#              burned_subtitles / duration_sec / font_warning
```

**W5-A / W5-B が必ず対応すること（W4 レビューの申し送り）:**

| 対象 | 内容 |
|------|------|
| W5-A | **`PipelineResult.highlights_error` を表示すること。** `regenerate(target="highlights")` の失敗はこのフィールドに日本語メッセージとして入る。**表示しないと、Codex 失敗時にユーザーには何も起きていないように見える**（成功表示のまま候補 0 件になる）。`ui/components/results.py` の `clips_error` の `st.warning` と同じパターンで出す |
| W5-B | **`ShortResult.font_warning` を `st.warning` で表示すること。** 日本語フォントが解決できなかった場合にここへ日本語の警告が入る。`build_short` は `on_progress` 経由で警告を出さなくなった（進捗メッセージと警告を混ぜないため）。生成前の事前警告（`is_japanese_font_available()`）と、生成後の実績警告（`font_warning`）は別物なので両方出すこと |

**ショート生成の所要時間について（W5-B が UI 文言を書くときの前提）:**
`build_short()` は 2 パス（精密シークで中間ファイルを切り出す → レイアウトと字幕を焼き込む）で
動作するため、再エンコードが 2 回走る。60 秒のショートでも待ち時間は短くない。
「数十秒〜数分かかります」程度の注記を UI に置くこと。

### W5-A: ハイライト UI

**ブランチ:** `v2/w5-a-highlights-ui`

```text
【タスク W5-A】ハイライト生成の UI を追加する

対応: docs/execution-plan-v2.md V5-4

## 変更対象ファイル
- src/yt_live_kit/ui/pages/highlights.py（新規）
- src/yt_live_kit/ui/components/results.py（ハイライトセクションの呼び出し 1 箇所のみ）
- tests/test_ui_highlights.py（新規）
（ui/pages/shorts.py・他ページ・services/ は編集しない）

## 実装内容

結果表示の下に、折りたたみ（expander）で「ハイライトまとめ動画」セクションを置く。
タブは増やさない（UI が複雑になるため）。

### 1. 候補の取得
- 「ハイライト候補を生成」ボタン
  → services.pipeline.regenerate(video_id, target="highlights") を
    services.jobs.start_job() 経由で実行
- 保存済み候補（highlights/segments.json）があれば読み込んで表示する
- **「切り抜き候補から選ぶ」トグルも用意する。**
  ON にすると clips/candidates.json の区間を候補として表示する
  （AI をもう一度呼ばずに済むため、実運用ではこちらが使われる可能性が高い）

### 2. 区間選択
- チェックボックス付き一覧: 開始 → 終了 / 尺 / タイトル / 理由
- **選択区間の合計尺をリアルタイム表示する**（「選択中: 5 区間 / 合計 6 分 12 秒」）
- 選択が 2 区間未満のときは作成ボタンを無効化する

### 3. 生成
- 「ハイライト動画を作成」ボタン
  → services.ffmpeg.cut_and_concat() を services.jobs.start_job() 経由で実行
  （シグネチャは W4-A の報告を確認すること）
- 進捗は W2 の常駐ステータスバーに出るので、ここで進捗 UI を作らない
- 生成には数分かかる旨を事前に注記する

### 4. 結果表示
- 完成したら st.video() でその場再生
- 保存先パスとコマンドログパスを表示
- 「中間ファイルは削除済みです」の注記

### 5. エラー ★W4 レビューの申し送り
- Codex CLI 失敗時も、タイムラインと切り抜き候補の表示が壊れないこと
  （softfail。services/pipeline.py の clips_error と同じ扱い）
- **PipelineResult.highlights_error を必ず表示すること。**
  regenerate(video_id, target="highlights") が失敗すると、例外ではなく
  このフィールドに日本語メッセージが入る（clips_error と同じ設計）。
  **表示を実装しないと、Codex 未導入やバリデーション失敗のときに
  ジョブは成功扱いのまま候補 0 件になり、ユーザーに原因が一切伝わらない。**
  ui/components/results.py の clips_error 表示（st.warning）と同じパターンで出す。

## Done 条件
- uv run pytest が全件通る
- **Codex を意図的に失敗させたとき、日本語の原因メッセージが画面に出る** ★必須
- 実機で、5 区間・合計 5 分程度のハイライトを生成し、
  **繋ぎ目でフリーズ・音ズレが無いことを目視確認して報告に書く** ★必須
```

### W5-B: ショート UI

**ブランチ:** `v2/w5-b-shorts-ui`

```text
【タスク W5-B】縦型ショート生成の UI を追加する

対応: docs/execution-plan-v2.md V6-3

## 変更対象ファイル
- src/yt_live_kit/ui/pages/shorts.py（新規）
- src/yt_live_kit/ui/components/results.py（ショートセクションの呼び出し 1 箇所のみ）
- tests/test_ui_shorts.py（新規）
（ui/pages/highlights.py・他ページ・services/ は編集しない）

## 実装内容

結果表示の下に、折りたたみで「縦型ショート動画」セクションを置く。

### 1. 区間の指定（3 通り）
ラジオボタンで切り替える:
1. 切り抜き候補から選ぶ（clips/candidates.json）
2. ハイライト候補から選ぶ（highlights/segments.json、あれば）
3. 開始・終了時刻を手入力（HH:MM:SS 形式、st.text_input）

手入力は形式バリデーションをして、不正なら日本語で указ… ではなく
「時刻は HH:MM:SS の形式で入力してください。」と表示する。

### 2. オプション
- レイアウト選択（ラジオ）:
  - 「ぼかし背景（推奨）」— キャプション: 元の映像を切らずに中央に配置します
  - 「中央クロップ」— キャプション: 左右が切れます。話者が中央にいる配信向けです
- 「字幕を焼き込む」チェックボックス（既定 ON）
- 日本語フォントが見つからない環境では、字幕チェックの下に日本語で警告を出す
  （services.subtitle_burn.is_japanese_font_available() を使う）
- **生成に時間がかかる旨を注記する。** build_short は 2 パス構成で
  再エンコードが 2 回走るため、60 秒のショートでも待ち時間は短くない。

### 3. 生成
- 「ショートを作成」ボタン
  → services.shorts.build_short() を services.jobs.start_job() 経由で実行
  （シグネチャは W4-B の報告を確認すること）
- 区間が 10 秒未満 / 180 秒超のときはボタンを無効化し、理由を日本語で表示する
  （services 側でも弾かれるが、UI で先に止める）

### 4. 結果表示 ★W4 レビューの申し送り
- st.video() でその場再生（縦動画もそのまま再生できる）
- 保存先パスとコマンドログパスを表示
- **ShortResult.font_warning が None でなければ st.warning で表示すること。**
  build_short は on_progress 経由でフォント警告を出さなくなった
  （進捗メッセージと警告を混ぜないため）。ここで拾わないと警告がどこにも出ない。
  生成前の事前警告（is_japanese_font_available）と、生成後の実績警告
  （font_warning）は別物なので、**両方実装すること。**

## Done 条件
- uv run pytest が全件通る
- 実機で 60 秒のショートを「ぼかし / クロップ」×「字幕あり / なし」の 4 通り生成し、
  **日本語字幕が豆腐にならず、タイミングがズレないことを目視確認して報告に書く** ★必須
  （字幕のタイミングは W4 で 2 パス化して構造的にズレない設計にしたが、
   実映像で確認したのはこの W5-B が初めてになる。ここで必ず目視すること）
- 出力が 1080x1920 であることを ffprobe で確認して報告に書く
```

### W5-C: CLI 追加分

**ブランチ:** `v2/w5-c-cli`

```text
【タスク W5-C】上級者向け CLI にチャンネル・ハイライト・ショートを追加する

対応: docs/execution-plan-v2.md V3-4 / V5-5 / V6-4

## 変更対象ファイル
- src/yt_live_kit/commands/channel.py（新規）
- src/yt_live_kit/commands/highlights.py（新規）
- src/yt_live_kit/commands/shorts.py（新規）
- src/yt_live_kit/cli.py（コマンド登録のみ）
- tests/test_cli_v2.py（新規）
（services/・ui/ は編集しない。services を呼ぶだけの薄いラッパーにする）

## 実装内容

既存の commands/*.py と同じ書き方（typer、日本語ヘルプ、エラーは日本語で
typer.echo + raise typer.Exit(1)）に合わせること。

### 1. channel コマンド
    uv run yt-live-kit channel @handle --limit 50 [--refresh]
- 一覧を「{video_id}\t{title}\t{url}」形式で標準出力に 1 行ずつ
- --refresh が無ければキャッシュを使う
- 処理済みには行頭に "# " を付ける（そのまま一括処理の入力に流せるように）

### 2. highlights コマンド
    uv run yt-live-kit highlights suggest {video_id}
    uv run yt-live-kit highlights build {video_id} --segments 1,3,5 [--keep-segments]
- suggest は候補を生成して一覧表示
- build は --segments で指定した番号（1 始まり）の区間を連結
- --segments 省略時は全区間

### 3. short コマンド
    uv run yt-live-kit short {video_id} --start 00:12:30 --end 00:13:20
        [--layout blur|crop] [--no-subtitles]
- 既定は --layout blur、字幕あり

### 4. cli.py への登録
既存の app.add_typer / app.command のパターンに合わせて追加する。
**既存コマンドの登録行は変更しない。**

## Done 条件
- uv run pytest が全件通る
- 各コマンドの --help が日本語で表示される
- services の関数を呼ぶだけで、CLI 側にロジックが無い
```

---

## 8. W6: 受け入れ・仕上げ（単独）

**ブランチ:** `v2/w6-acceptance`

```text
【タスク W6-1】v2 の受け入れ確認と仕上げを行う

対応: docs/execution-plan-v2.md V7 全項目

## 変更対象ファイル
- README.md
- docs/execution-plan-v2.md（進捗チェックの最終更新。**このタスクでのみ許可**）
- src/yt_live_kit/__init__.py（版数）
- pyproject.toml（版数）
- 不具合が見つかった箇所（範囲を報告に明記すること）

## 作業内容

### 1. 受け入れ確認
- docs/requirements.md の AC-01〜AC-17 をすべて手で確認する
- **v1 分（AC-01〜AC-10）の回帰も必ず確認する**
- 結果をチェックリスト形式で報告する。未達があれば「次イテレーション」と明記する

### 2. 実機通し確認
- 公開アーカイブ 2 本で、V1〜V6 の全機能を通しで実行する
- 確認項目:
  - チャンネルから一覧取得 → 一括投入
  - チャプターだけ再生成
  - 全文・概要欄のコピー
  - ハイライト動画の生成（繋ぎ目の目視）
  - 縦型ショートの生成（日本語字幕の目視）
  - 元動画の削除後も成果物が残る
  - 処理中に別タブへ移動しても継続する

### 3. エラーメッセージのレビュー
新規追加分（channel / storage / highlights / shorts / jobs）のユーザー向け
メッセージがすべて日本語で、原因と対処が書かれているか確認する。
英語のまま・スタックトレース直出しがあれば直す。

### 4. README の更新
以下を追記する（既存の書き方・トーンに合わせる）:
- チャンネルからの取り込み手順
- ハイライトまとめ動画の作り方
- 縦型ショート動画の作り方
- ストレージ管理（元動画の削除）
- **日本語字幕を焼き込むためのフォント要件**（見つからない場合の対処含む）
- 新しい CLI コマンド

### 5. 版数の更新
- 0.2.0 に更新する（__init__.py と pyproject.toml の両方）

### 6. 実行計画の進捗を最終更新する
docs/execution-plan-v2.md の全チェックボックスとフェーズ状態・
マイルストーン表を実態に合わせて更新する。
**完了した事実だけをチェックすること。** 未確認の項目にチェックを入れない。

## Done 条件
- AC 全項目が確認済み（未達は明示的に次イテレーションへ移されている）
- README だけで非エンジニアが全機能を使える
- v1 の機能に回帰が無い
- uv run pytest が全件通る
```

---

## 9. レビュー依頼時のチェックポイント

オーケストレーターが各ウェーブ完了後にレビューを依頼する際、以下を伝えると
レビュー側が効率的に確認できる。

| ウェーブ | 重点的に見るべき点 |
|----------|--------------------|
| W0 | スコープの線引きが曖昧になっていないか。FR/AC の番号重複 |
| W1 | **後方互換**（既存テストが無改修で通るか）。storage の削除安全性。jobs の原子的書き込み |
| W2 | **ワーカーから st.\* を呼んでいないか**（最重要）。v1 機能の回帰。責務分離 |
| W3 | UI にロジックが漏れていないか。容量計算の呼び出し頻度 |
| W4 | **-ss の位置**（-i の後ろか）。連結が再エンコード経由か。字幕の時刻オフセット。W4-C は削除プレビューの集計と行バッジを取り違えていないか |
| W5 | 実機での目視確認結果（繋ぎ目・日本語字幕）が報告にあるか。**失敗が無音になっていないか**（highlights_error / font_warning を UI が拾っているか） |
| W6 | AC の未達が正直に報告されているか |

**全ウェーブ共通:**
- 変更対象ファイル外を編集していないか（`git diff --stat` で確認）
- 日本語エラーメッセージが揃っているか
- 半角 `<` `>` が成果物テキストに混入していないか
- 従量課金 API を呼んでいないか
