# 全体コードレビュー — 完成度監査

**日付:** 2026-08-06
**対象:** `yt-live-kit` v0.3.0、commit `2cfe5cb`（working tree clean）
**規模:** `src/` 34,864 行 / `tests/` 37,792 行 / 156 Python ファイル
**回帰基準:** `uv run pytest -q` → **1842 passed / 2 skipped**（131.7 秒、exit 0）
**環境:** Python 3.14.3、Streamlit 1.60.0、macOS Darwin 25.5.0
**方法:** 8 領域を分割して `sonnet` サブエージェントに read-only レビューさせ、**全所見をオーケストレーターが実コードで再検証**した。本書に載せた指摘はすべて再検証済みである。未検証の推測は載せていない。

**修正ステータス（2026-08-06）:** F-01〜F-22 **実装完了**。確定方針は F-01 廃止（CLI `clips cut` 維持）/ F-03 サポート終了。回帰 **1889 passed / 2 skipped**。進捗は [`execution-plan-v3.md`](execution-plan-v3.md) の `CR-2026-08-06`。未コミット。

---

## 0. この文書の使い方（修正担当者向け）

- 各指摘には **ID / 対象ファイル:行 / 証拠 / 失敗シナリオ / 修正方針 / 影響範囲 / 判断が必要な点** を書いた。ID を commit message とタスク管理に使う。
- **「判断が必要な点」がある指摘は、独断で実装しない。** 選択肢と推奨を書いてあるので、ユーザーに確認してから着手する。特に §2 の機能喪失 3 件は「復活させる」か「正式に廃止する」かでコードの変更範囲が全く変わる。
- 進捗管理は [`docs/execution-plan-v3.md`](execution-plan-v3.md) に集約する（[AGENTS.md](../AGENTS.md) §2）。本書は調査記録であり、着手時にチェックを付ける対象ではない。
- 修正後は必ず `uv run pytest -q` 全件を通し、**1842 passed を下回らないこと**を確認する。既存テストを削除する場合は、削除理由を本書の該当 ID に追記する。
- UI に触る修正は、テスト全パスだけで完了と判断しない。実機ブラウザでの確認まで済ませる。

---

## 1. 結論

**完成度は高い。** 機能が足りない段階ではなく、**v1 → v3 の IA 作り替えで孤立したコードが溜まり、検証の非対称が残っている**段階である。

特にアップロード契約は実コードで確認して堅牢だった。`privacy_status: Literal["private"]` による型レベル固定、insert 前の試行台帳記録、`_ALLOWED_TRANSITIONS` による自動再送禁止、クラッシュ時の fail closed。[`docs/execution-plan-v3.md`](execution-plan-v3.md) の P1 / P2 / H1-5 が主張する契約は、public へ倒れる経路・二重 insert 経路ともに見つからなかった。

ショート量産パイプラインも同様に堅い。schema v1→v2 移行、CAS 永続化とロールバック、テロップ確認から生成・プレビュー・予約までの fingerprint 照合ゲート、queue の owner-liveness による recovery は、いずれも実装とテストが揃っている。

一方で、次の 4 つの構造的な問題がある。

1. **緑のテストが機能喪失を隠している。** UI から起動できなくなった機能が、テストだけは通り続けている（§2）。
2. **正しいロジックが未使用側にあり、production は簡略版か再実装で動いている。** Whisper 契約の失効判定（§3.1）と、ライン状態の復旧経路（§4.9）がこれに該当する。どちらも受け皿の関数は既に存在するのに配線されていない。
3. **ロック保持区間から外部 I/O を呼んでいる箇所が 2 つある。** `schedule_lock`（§4.2）と `_WRITE_LOCK`（§4.10）。どちらもハング時に無関係な操作まで巻き込む。
4. **CI・lint・型チェックが一切ない。** 34,864 行・1842 テストの規模で、これは実質的な穴である（§6）。

### 深刻度別の一覧

| ID | 深刻度 | 一行要約 | 節 |
|----|--------|----------|----|
| F-01 | **最重要** | 切り抜き候補からの単発 mp4 切り出しが UI から起動不能 | §2.1 |
| F-02 | **最重要** | Whisper 契約を更新しても既存 artifact が失効しない | §3.1 |
| F-03 | 高 | OAuth 未設定だと概要欄テキストを得る手段が一切ない | §2.2 |
| F-04 | 高 | `results.py` 全体（207 行）と長尺概要欄機能が到達不能 | §2.3 |
| F-05 | 高 | `transcript_artifact.py` の公開 API 5 関数が 0 参照 | §3.2 |
| F-19 | 高 | 破損したライン状態から復旧する導線がなく、UI が恒久的に停止する | §4.9 |
| F-06 | 中 | バッチの狭い except で成功済み履歴が全損する | §4.1 |
| F-07 | 中 | `schedule_lock` 保持中にタイムアウトなしのネットワーク呼び出し | §4.2 |
| F-08 | 中 | 壊れたソース動画 1 つで切り抜き機能が全停止 | §4.3 |
| F-09 | 中 | 一括削除がプレビュー後の再検証をしない | §4.4 |
| F-10 | 中 | 動画詳細ページの「データあり」状態が実 Streamlit API で未検証 | §5.1 |
| F-18 | 中 | CI / lint / 型チェックが存在しない | §6.1 |
| F-20 | 中 | ライン書き込みロックが全動画共通で、投稿 API 呼び出しを抱えたまま保持される | §4.10 |
| F-21 | 中 | 候補モデル 4 種が未知フィールドを黙って無視する | §4.11 |
| F-11 | 低 | `fc-list` だけタイムアウトなし | §4.5 |
| F-12 | 低 | 全文・チャプターのダウンロードボタンが消えた | §2.4 |
| F-13 | 低 | `ui/state.py` の 4 関数が 0 参照 | §2.5 |
| F-14 | 低 | パス境界契約を 2 ファイルだけ守っていない | §4.6 |
| F-15 | 低 | CLI と UI の機能非対称（バックアップ・フラグ・テスト） | §4.7 |
| F-16 | 低 | 原子的書き込みが 4 実装に分裂、共通版が最も弱い | §4.8 |
| F-17 | 低 | Whisper モデルパスがマシン固有ハードコード | §6.2 |
| F-22 | 低 | `record_upload_operation` の flock 自己デッドロック地雷（現状未発火） | §4.12 |

---

## 2. U8 / U9 の IA 刷新で孤立した機能

[`docs/ui-refactor-review-2026-08-04.md`](ui-refactor-review-2026-08-04.md) §3.1 は「`app.py` の global shell から `render_results` を外す」ことを推奨した。**これは正しく実施された。** しかし `render_results` が担っていた機能の移植先が一部用意されず、モジュールごと置き去りになっている。以下はその残骸である。

### 2.1 F-01（最重要）— 切り抜き候補からの単発 mp4 切り出しが UI から起動不能

**対象:** [`src/yt_live_kit/ui/components/results.py:57-79`](../src/yt_live_kit/ui/components/results.py)、[`src/yt_live_kit/ui/views/video_detail.py`](../src/yt_live_kit/ui/views/video_detail.py)

**証拠:**

- `start_job("cut_clip", cut_clip_job_target, ...)` を呼ぶ箇所は `results.py:57-66` の `_start_cut_clip` **のみ**（`src/` 全体を grep して確認）。
- `results.py` はどのビューからも import されていない（`grep -rn "components.results" src/` の結果ゼロ）。
- 下流は全て生きている: [`services/clips.py:552`](../src/yt_live_kit/services/clips.py) `cut_clip_job_target`、[`ui/components/status_bar.py:520`](../src/yt_live_kit/ui/components/status_bar.py) のジョブ完了処理、[`ui/view_models/video_detail.py:70`](../src/yt_live_kit/ui/view_models/video_detail.py) の実行中ジョブ判定、`tests/test_ui_app.py` の cut_clip テスト 3 件。
- `ui/state.py:444` `set_cut_result` は `status_bar.py:544` から呼ばれ結果を保存するが、読み出す `get_cut_result` は到達不能な `results.py:198` だけ。**結果は保存されるが表示されない。**
- [`status_bar.py:78`](../src/yt_live_kit/ui/components/status_bar.py) は失敗時に「対象動画を開いて切り出しを再実行してください」と案内するが、その導線が存在しない。

**失敗シナリオ:** 元動画のアスペクト比のまま区間を切り出したいユーザーは、UI 上に手段がない。`video_detail.py` が切り抜き候補に対して提供するのは「ショート生産ラインへの引き継ぎ」と「再生成」のみ。`shorts.py` の補助セクションが提供する「ショートを作成」は縦型・ぼかし/クロップ加工済みの別物である。CLI の `yt-live-kit clips cut` だけが生き残っている。

**判断が必要な点:** この機能を**復活させるのか、正式に廃止するのか**。中途半端な残存が最も悪い。

- **復活させる場合:** `results.py` の `_start_cut_clip` / `cut_clip_button_key` / `cut_clip_button_disabled` / `clip_candidate_radio_key` / `source_cache_note` を `video_detail.py` の materials workspace へ移植し、`get_cut_result` の表示も戻す。
- **廃止する場合:** `results.py` 全体、`services/clips.py:552 cut_clip_job_target`、`ui/state.py:440-449` の `get_cut_result` / `set_cut_result` / `clear_cut_result`、`status_bar.py` の `cut_clip` 分岐（56, 62, 78, 415, 520, 544 行）、`view_models/video_detail.py:70` の `"cut_clip"` 参照、`tests/test_ui_app.py:693-724, 1056-1102` を**まとめて**削除する。CLI の `clips cut` を残すかも合わせて決める。

**影響範囲:** `tests/test_ui_intake_page.py:16-27` が `results.py` の内部関数 7 個を直接 import している。どちらの判断でもこのテストの修正が必要。

---

### 2.2 F-03（高）— OAuth 未設定だと概要欄テキストを得る手段が一切ない

**対象:** [`src/yt_live_kit/ui/views/video_detail.py:447-474`](../src/yt_live_kit/ui/views/video_detail.py)

**証拠:**

```python
    if not configured:
        st.error(
            "YouTube OAuth が設定されていません。"
            "設定ファイルを配置してから、もう一度お試しください。"
        )
        return
```

`_start_description_preview` は `is_configured(settings)` が False なら即 return する。OAuth 不要でテンプレート合成する [`services/description.py:107`](../src/yt_live_kit/services/description.py) `build_description()` は、到達不能な `results.py:133` からしか呼ばれていない。

**失敗シナリオ:** OAuth を設定せずチャプター・切り抜き生成だけ使っているユーザーが「概要欄に反映」を押すと、エラーだけが出て終わる。旧 UI にあった「テンプレート合成した概要欄テキストをコピーする」手段が画面のどこにもない。

**判断が必要な点:** OAuth 未設定運用を**サポート対象とするか**。

- サポートする場合: `_render_publish_workspace` に、OAuth 未設定時のフォールバックとして `build_description()` の結果を `render_copy_button` でコピーできる導線を追加する。
- サポートしない場合: 仕様変更として [`docs/requirements-v3.md`](requirements-v3.md) に明記し、F-04 と合わせて `build_description` / `save_template` / `get_template_path` を削除する。

---

### 2.3 F-04（高）— `results.py` 全体と長尺概要欄機能が到達不能

**対象:** [`src/yt_live_kit/ui/components/results.py`](../src/yt_live_kit/ui/components/results.py)（207 行）、[`src/yt_live_kit/services/description.py:98, 107`](../src/yt_live_kit/services/description.py)

**証拠:** `render_results`（results.py:93）は `src/` 全体で 0 参照。連鎖して `build_description`（description.py:107）、`save_template`（98）、`get_template_path` も src からは 0 参照になっている。`services/description.py` のショート向け関数群（`build_shorts_description` ほか）は生きており、これらとは別系統である。

`results.py:22` の `_TEMPLATE_NOT_SET_MESSAGE` は「`data/_config/description_template.txt` を配置せよ」と案内するが、そのファイルを作る UI はもう存在しない。

**失敗シナリオ:** 実行時の障害はない。問題は、`tests/test_description.py` に `test_build_description_with_template` など長尺側のテストが 4 件残り、`tests/test_ui_intake_page.py` が `results.py` の内部関数を叩いているため、**到達不能なコードに緑のテストが付いている**こと。次にこの領域を触る人が「使われている機能だ」と誤認する。

**修正方針:** F-01 / F-03 の判断が確定してから着手する。廃止判断なら `results.py` 全体、`description.py` の長尺側 3 関数、対応テストを一括削除する。

---

### 2.4 F-12（低）— 全文・チャプターのダウンロードボタンが消えた

**対象:** [`src/yt_live_kit/ui/views/video_detail.py:558-571, 650-674`](../src/yt_live_kit/ui/views/video_detail.py)

**証拠:** 旧 `render_results()`（results.py:114, 154）は文字起こしとチャプターの両方に `st.download_button` を持っていた。新 `_render_transcript` / `_render_chapters` は `render_copy_button` のみ。`src/yt_live_kit/ui/` 全体で `download_button` が残るのは `shorts_queue.py:821`（ショート mp4）と到達不能な `results.py` の 2 箇所だけである。

**修正方針:** `_render_transcript` / `_render_chapters` に `st.download_button` を追加する。旧実装のファイル名パターンを流用できる。コピーで代替可能なため優先度は低いが、明確な後退である。

---

### 2.5 F-13（低）— `ui/state.py` の 4 関数が 0 参照

**対象:** [`src/yt_live_kit/ui/state.py:428, 432, 489, 504`](../src/yt_live_kit/ui/state.py)

`get_result` / `set_result` / `get_interrupted_notices` / `mark_interrupted_notices_shown` は src・tests のどこからも参照されていない。`init_orphans_once` は毎回 `set_interrupted_notices` を呼ぶが、読む画面がない（`_record_interrupted_jobs` が既に構造化通知として同じ情報を処理済み）。

**修正方針:** 該当関数と `SESSION_INTERRUPTED_*` キー、`init_orphans_once` 内の `set_interrupted_notices` 呼び出しを削除する。`get_result` / `set_result` は F-01 の判断に紐づくので同時に処理する。

---

## 3. S9 Whisper 層 — 正しいロジックが未使用側にある

### 3.1 F-02（最重要）— Whisper 契約を更新しても既存 artifact が失効しない

**対象:** [`src/yt_live_kit/services/shorts_line.py:1114-1134`](../src/yt_live_kit/services/shorts_line.py)、[`src/yt_live_kit/services/short_cut.py:460-478`](../src/yt_live_kit/services/short_cut.py)、[`src/yt_live_kit/ui/components/short_cut.py:428-461`](../src/yt_live_kit/ui/components/short_cut.py)

**証拠:** production の失効判定 `_artifact_lineage_is_current` が比較するのは次の 5 点のみ。

```python
    return (
        actual_ref == state.artifact_ref
        and artifact.video_id == state.video_id
        and artifact.artifact_fingerprint == state.artifact_fingerprint
        and tuple(artifact.used_range_cue_digests) == tuple(state.used_range_cue_digests)
        and artifact.is_high_precision
    )
```

**`artifact.model` / `artifact.runtime` を現在の `WHISPER_ADOPTED_CONTRACT` と突き合わせていない。**

一方 [`transcript_artifact.py:1745-1755`](../src/yt_live_kit/services/transcript_artifact.py) の `TranscriptResolver.selected_range` は `model` / `runtime` / `settings` を全て比較して `invalidated=True` にする。**この正しい判定が未使用の側にあり、production は簡略版を再実装している。**

`WHISPER_ADOPTED_CONTRACT` の参照先は [`whisper_runtime.py:32, 65-70`](../src/yt_live_kit/services/whisper_runtime.py)（新規実行の preflight）と [`ui/views/settings.py:161, 166`](../src/yt_live_kit/ui/views/settings.py)（表示）のみで、**保存済み artifact と突き合わせる箇所がない**。

**失敗シナリオ:** 品質改善のため `config.py` の `WHISPER_BINARY_SHA256` / `WHISPER_MODEL_SHA256` を更新する。新規実行は `_preflight_whisper_runtime`（whisper_runtime.py:535-676）が正しく fail closed で検証する。しかし既に `artifact_ref` を持つ `LineState` / cutplan は `_artifact_lineage_is_current` が True を返し続け、`is_high_precision` も True のままなので、**旧品質の transcript が「高精度」として永続的に再利用される。** SHA256 を pin している設計目的そのものが downstream で無効化されている。

**修正方針:** `transcript_artifact.py` の `should_invalidate_used_range`（1941 行付近）が既に model / runtime / settings 比較を実装しているので、これを「現在の capability と比較する」薄い公開関数として整え、3 箇所の呼び出し側から呼ぶ。F-05 の削除作業より**先に**判断すること（`invalidation_reason` / `should_invalidate_used_range` を消してしまうと再利用できなくなる）。

**影響範囲:** `services/shorts_line.py`、`services/short_cut.py`、`ui/components/short_cut.py` の 3 箇所を同時に直す必要がある。`tests/test_shorts_line.py` / `tests/test_short_cut.py` にこのシナリオのテストが**ない**ので新規追加する。

---

### 3.2 F-05（高）— `transcript_artifact.py` の公開 API が 0 参照

**対象:** [`src/yt_live_kit/services/transcript_artifact.py`](../src/yt_live_kit/services/transcript_artifact.py)（1979 行）

src・tests・benchmarks のどこからも参照されていない公開関数が 5 つある。

| 関数 | 行 | 判定 | 根拠 |
|------|----|------|------|
| `load_transcript_artifact` | 1836 | 削除可 | `TranscriptArtifactStore.load_artifact()` を直接呼ぶ経路（shorts_line.py:1123 ほか）が使われている薄いラッパー |
| `rebuild_transcript_index` | 1846 | 削除可 | `store.rebuild_index()` は load 時のリカバリ経路のみで使用。外部から明示的に再構築する運用導線がない |
| `resolve_coarse_search` | 1875 | 削除可 | production は `TranscriptArtifactStore.save_vtt()` を直接呼ぶ経路（clips.py:181）に置き換わっている |
| `resolve_artifact` | 1903 | 削除可（docstring は要修正） | docstring が「S9-3 / S9-4 向け convenience API」と書くが、S9-3/S9-4 のどちらからも未使用 |
| `invalidation_reason` | 1972 | **保留** | F-02 の修正で `should_invalidate_used_range` を再利用するため、F-02 確定まで消さない |

src からは 0 参照でテストのみが参照する関数も 2 つある。`resolve_selected_range`（1883）は**本来配線すべきだった**もので、F-02 の本体である。`used_range_invalidated`（1922）は `TranscriptArtifactStore._validate_vtt_source`（1290-1322）が load 時に毎回 source bytes を再検証しているため機能上の欠落はなく、削除可。

**注意:** `resolve_artifact` の docstring は「S9-3 / S9-4 向け convenience API」と実態に反する記述をしている。将来の実装者が「resolve_artifact 経由なら失効判定が効いている」と誤認する原因になるので、削除しないなら docstring を先に直す。

**確認済みの契約（変更不要）:**

- **S9-0 非上書き契約: 成立。** `save_vtt()`（1490-1579）は `ja.vtt` を read-only にしか読まない。`save()`（1445-1483）は同一 fingerprint の既存ファイルがあれば内容一致検証のみ行い、`_atomic_json_replace` は `existing is None` の場合しか呼ばれない。
- **S9-2 fingerprint: 成立。** `make_cache_identity` → `make_artifact_fingerprint` の二段構成。`_identity_metadata` が path / mtime を除外しつつ実体 bytes の sha256 は保持する（131-174）。`_validate_artifact`（1232-1277）が保存前後で同じ検証を通す。
- **S9-3 capability 検査: 成立。** `_preflight_whisper_runtime`（535-676）が binary / model の sha256 を厳密一致検証し、不一致なら `WhisperPreflightError`（retryable=False）で fail closed。DI 用の注入経路にも contract_mismatch チェック（558-568）がある。
- **音声区間オフセット:** [`docs/s9-6-audio-span-offset-2026-08-06.md`](s9-6-audio-span-offset-2026-08-06.md) の修正は `ytdlp.py` 側で解決済み。`whisper_runtime.py` は `--offset-t 0` 固定＋`absolute_start_ms=item.requested_start_ms` でその前提を正しく踏襲しており、同種の残存バグは見つからなかった。

---

## 4. 実際に壊れる経路

### 4.1 F-06（中）— バッチの狭い except で成功済み履歴が全損する

**対象:** [`src/yt_live_kit/services/batch.py:224, 247`](../src/yt_live_kit/services/batch.py)

**証拠:** `except (PipelineError, YtdlpError) as exc:` しか捕捉していない。`append_batch_status(status_entries, settings)` は for ループの**後**にあり、正常終了時しか到達しない。

**失敗シナリオ:** 長時間の無人バッチ中にディスク容量不足や権限エラーで [`transcript.py:58`](../src/yt_live_kit/services/transcript.py) の `write_text_atomically` が `OSError` を投げると、`TranscriptError` にも `PipelineError` にも変換されずループごと例外が伝播する。その回のバッチで**既に成功していた前の URL の記録が一切残らない**。`run_batch` の docstring「個別失敗はスキップして継続」に反する。`jobs.py` の worker が捕捉するので無限 running にはならない。

**修正方針:** `except (PipelineError, YtdlpError)` を `except Exception` に広げ、`append_batch_status` を try/finally に移す。テストは `run()` が任意の例外を投げるケースを新規追加する。

---

### 4.2 F-07（中）— `schedule_lock` 保持中にタイムアウトなしのネットワーク呼び出し

**対象:** [`src/yt_live_kit/services/schedule.py:749`](../src/yt_live_kit/services/schedule.py) → [`:616`](../src/yt_live_kit/services/schedule.py)、[`src/yt_live_kit/services/youtube_api.py`](../src/yt_live_kit/services/youtube_api.py)

**証拠:** `with schedule_lock(settings): current = build_upload_preview(...)` が、`build_upload_preview` 内の `channel = fetch_mine_channel(settings)`（616 行）を呼ぶ。`schedule_lock` はプロセス内 RLock に加え `.schedule.lock` の advisory flock（upload_queue.py:246-269）で、queue.json / attempts / policy の全読み書きを直列化している。さらに `youtube_api.py` に **HTTP タイムアウトの設定が一切ない**（`setdefaulttimeout` も `http=` 引数も grep で 0 件。googleapiclient のデフォルトは無制限）。

**失敗シナリオ:** ネットワークが遅延・ハングした状態でユーザーが確認を押すと、その呼び出しが返るまで `.schedule.lock` が保持され続け、読み取り専用の `list_operations`（キュー表示・status bar）まで含めて全操作がブロックされる。

**修正方針:** `fetch_mine_channel` をロック取得**前**に移すのが本筋。難しければ最低限 `youtube_api.py` の service 構築に明示タイムアウトを設定する（後者は 1 行に近く、効果が大きい）。

---

### 4.3 F-08（中）— 壊れたソース動画 1 つで切り抜き機能が全停止

**対象:** [`src/yt_live_kit/services/ffmpeg.py:489-497`](../src/yt_live_kit/services/ffmpeg.py)

**証拠:** `ensure_source_video` の候補ループが `probe_media_streams` を try なしで呼ぶ。`probe_media_streams` は ffprobe が非ゼロ終了すると [`ffmpeg.py:197`](../src/yt_live_kit/services/ffmpeg.py) で `FfmpegError` を送出する。

**失敗シナリオ:** ダウンロード中断・プロセス kill・ディスク満杯で `clips/source/` に壊れた mp4 が残ると、2 件目以降の候補も再ダウンロードも試さず例外が伝播する。ユーザーが手動でファイルを削除するまで `cut_clip` / `cut_and_concat` が一切実行できない。

**修正方針:** ループ内で `FfmpegError` を捕捉して次候補へ進み、全候補が失敗したら再ダウンロードへフォールバックする。壊れた候補を隔離ディレクトリへ退避するかは判断が要る（既存の `prepare_audio_span` の破損 cache 隔離パターンに合わせるのが自然）。

---

### 4.4 F-09（中）— 一括削除がプレビュー後の再検証をしない

**対象:** [`src/yt_live_kit/ui/components/storage_manager.py:200-269`](../src/yt_live_kit/ui/components/storage_manager.py)

**証拠:** `_confirm_bulk_purge_dialog` は `snapshot.targets`（プレビュー時に固定した video_id 一覧）をそのまま `purge_source` へ渡し、削除直前に `fetched_at` と cutoff を再照合しない。サービス層の [`storage.py:198`](../src/yt_live_kit/services/storage.py) `purge_sources_older_than` は削除直前に `meta.json` を再読込して cutoff 判定するが、**UI から一度も呼ばれていない**（呼び出し元は `tests/test_storage.py` のみ）。

**失敗シナリオ:** 一括削除のプレビューを開いたまま別画面で対象動画を再取得すると、`fetched_at` が更新されて「古い」条件を満たさなくなるのに、`is_busy()` は False なので削除が通る。同じファイル内の shorts_queue 上書き確認（`_validate_overwrite_confirmation`）は同種の staleness を再検証しており、ここだけ保護が抜けている。

**修正方針:** `_confirm_bulk_purge_dialog` の実行側を `purge_sources_older_than` に寄せるか、削除直前に snapshot の各 target を再判定する。前者なら死んでいたサービス関数が生き返り、重複も解消する。

---

### 4.5 F-11（低）— `fc-list` だけタイムアウトなし

**対象:** [`src/yt_live_kit/services/subtitle_burn.py:692-697`](../src/yt_live_kit/services/subtitle_burn.py)

同ファイルの ffmpeg 呼び出しは全て `_run_ffmpeg_command` でタイムアウト付きだが、`_font_available_via_fc_list` の `subprocess.run` だけ素のまま。フォントキャッシュ破損時に `resolve_font()` 経由で `build_segment_subtitle` / `build_concatenated_subtitle` が無期限ブロックしうる。`timeout=` を追加するだけで済む。

---

### 4.6 F-14（低）— パス境界契約を 2 ファイルだけ守っていない

**対象:** [`src/yt_live_kit/services/transcript.py:40`](../src/yt_live_kit/services/transcript.py)、[`src/yt_live_kit/services/pipeline.py:94`](../src/yt_live_kit/services/pipeline.py)

`_paths.py` の `safe_video_identifier` / `confined_video_path` を使うサービスは 11 個（ytdlp, jobs, history, shorts_queue, upload_queue, schedule, whisper_runtime, shorts_line, transcript_artifact, youtube_api, ui/views/_local_settings）。`tests/test_paths.py` は 5 サブシステムで「副作用前に拒否」をテストしている。`transcript.py` と `pipeline.py` の `_video_dir` だけが生の `settings.data_dir / video_id` である。

**位置づけ:** localhost 単一ユーザーツールで、到達経路は CLI の `transcript` / `chapters` / `clips` / `highlights` サブコマンド（攻撃者＝自分自身）。`run` は URL 引数 → `extract_video_id` 経由なので安全。**セキュリティ穴ではなく、自リポジトリが 11 箇所で明示的にテストしている契約を 2 箇所が守っていない一貫性の欠陥**として扱う。

---

### 4.7 F-15（低）— CLI と UI の機能非対称

| 項目 | 内容 |
|------|------|
| バックアップ | `pipeline.regenerate` は `_backup_file` で `.bak` を作る（[pipeline.py:355, 372, 383](../src/yt_live_kit/services/pipeline.py)）。CLI の `chapters` / `clips suggest` は同じサービス関数を直接呼ぶため**バックアップなしで上書き**する。手編集の復旧手段がない |
| `--from-file` | `chapters` / `clips` にはあるが `highlights` にない。[`services/highlights.py:403`](../src/yt_live_kit/services/highlights.py) `save_segments_from_file` は実装済みで未配線 |
| 個別選択フラグ | `run` に `do_chapters` / `do_clips` の個別選択がない。UI・batch・pipeline は対応済み |
| CLI テスト | `commands/fetch.py` `transcript.py` `chapters.py` `clips.py` `run.py` に `CliRunner` テストが**1 件もない**。`test_cli_v2.py` は channel / highlights / short のみ |

---

### 4.8 F-16（低）— 原子的書き込みが 4 実装に分裂、共通版が最も弱い

原子的書き込みの実装がリポジトリ内に **4 つ**あり、directory fsync の有無が割れている。

| 実装 | 場所 | directory fsync |
|------|------|-----------------|
| `_fsutil.write_text_atomically`（共通） | [_fsutil.py:30-65](../src/yt_live_kit/services/_fsutil.py) | **なし** |
| `shorts_line._atomic_write` | [shorts_line.py:1014-1032](../src/yt_live_kit/services/shorts_line.py) | **なし** |
| `ytdlp._atomic_create_bytes` | ytdlp.py:2056-2061 | あり |
| `shorts_queue._write_manifest_unlocked` | shorts_queue.py:1123-1157 | あり |

**共通ユーティリティが一番弱い**という逆転が起きている。`shorts_queue.py` は flock も `_fsutil.advisory_lock` を使わず独自実装している。

**修正方針:** `_fsutil.write_text_atomically` に directory fsync を追加し、独自実装 3 箇所を共通版へ寄せる。

---

### 4.9 F-19（高）— 破損したライン状態から復旧する導線がなく、UI が恒久的に停止する

**対象:** [`src/yt_live_kit/ui/components/shorts_line.py:1204-1208`](../src/yt_live_kit/ui/components/shorts_line.py)、[`src/yt_live_kit/services/shorts_line.py:1570-1638`](../src/yt_live_kit/services/shorts_line.py)

**証拠:** `recover_line_state`（services/shorts_line.py:1570）は **src からの呼び出しが皆無**（tests のみ参照）。実際の消費側は次のようになっている。

```python
    try:
        state = resolve_active_line_read_only(video_id, settings)
    except LineStateError as exc:
        st.error(_safe(exc))
        return
```

`_read_line_state()` が「壊れているため安全に復元できません」「対象が保存先と一致しません」を投げると、abandon 導線（`_confirm_abandon_line_dialog`）にすら遷移できず、render が毎回同じ場所で止まる。

さらに **同じ例外の扱いが 2 箇所で矛盾している。** `render_main_line_summary`（[shorts_line.py:1076-1081](../src/yt_live_kit/ui/components/shorts_line.py)）は `except LineStateError: state = None` で握り潰して黙って何も表示せず、`render_shorts_line`（1204-1208）はエラーを出して止まる。

**失敗シナリオ:** `line_<clip>.json` が手動編集・ディスク障害・別クリップへの誤コピー等で破損または identity 不一致になると、その動画のショート生産ライン UI が恒久的にエラー表示のままになる。ユーザーは JSON を手動削除する以外に復旧手段がない。**`recover_line_state` はまさにこの場面のために、queue manifest やアップロード記録といった機械的証跡から状態を再構成する目的で作られている。**

**修正方針:** `resolve_active_line_read_only` / `load_line_state` が `LineStateError` を投げたケースに、破損ファイルを退避して `recover_line_state` で再構成するか素材選定へ戻す UI アクションを追加する。あわせて `render_main_line_summary` と `render_shorts_line` の `LineStateError` ハンドリングを統一する。

**影響範囲:** `ui/components/shorts_line.py` の 2 箇所。`recover_line_state` 自体のシグネチャ・挙動は変更不要で、既存テストは維持できる。

**関連（別判断）:** 同ファイルの `calculate_line_stage`（[shorts_line.py:580-605](../src/yt_live_kit/services/shorts_line.py)）も src からの呼び出しがない。現在の状態遷移は各コマンド関数（`confirm_review`, `record_output`, `confirm_preview`, `record_upload_operation` 等）が `current_stage` を明示設定し、`LineState.model_validator` が整合性を強制する設計になっており、この関数は導出ロジックの参照実装に見える。ただし `test_calculate_line_stage_covers_six_stages_and_terminal` はこの関数単体しか検証しておらず、**実トランザクションの遷移結果と突き合わせていないため、実装が乖離しても検知できない。** 削除するか、実トランザクションの `current_stage` と照合するプロパティテストとして再利用するかを選ぶ。`recover_line_state` とは違い、こちらは削除しても機能的な穴は開かない。

---

### 4.10 F-20（中）— ライン書き込みロックが全動画共通で、投稿 API 呼び出しを抱えたまま保持される

**対象:** [`src/yt_live_kit/services/shorts_line.py:43, 1075-1094, 1431, 1452`](../src/yt_live_kit/services/shorts_line.py)

**証拠:** `_WRITE_LOCK = threading.RLock()` はモジュールレベルで video_id に依存しない。`_line_lock` の docstring は「同一動画の状態確認と atomic replace をプロセス間で直列化する」と書くが、実際は `with _WRITE_LOCK:` が**全動画を先に直列化**してから video 別の flock を取る。

そして `run_line_reservation_transaction`（1431 行で `with _line_lock(...)`）は、**1452 行の `operation = start_upload()`（外部アップロード API 呼び出し）をロック保持中に実行する。**

**失敗シナリオ:** Streamlit は 1 プロセスで複数セッション/スレッドを扱う。ある動画の予約投稿でネットワーク往復が進行している間、別動画の `save_line_state` / `persist_line_start` / `abandon_line_state` / 生産キューの書き込みが全て `_WRITE_LOCK` でブロックされ、無関係な操作までフリーズする。F-07（`schedule_lock` 保持中のネットワーク呼び出し）と同じ構造の問題である。

**修正方針:** `_WRITE_LOCK` を video_id ごとの辞書（`defaultdict(threading.RLock)` + GC 対策）に置き換えるか、flock だけでプロセス間直列化が足りている点を踏まえて `_WRITE_LOCK` の役割を縮小する。ロック粒度を変える際は、flock 単体でのプロセス間直列化が保たれることを `test_reservation_transaction_rejects_stale_state_and_serializes_tabs` 等の既存並行性テストで確認する。

**影響範囲:** `_line_lock` を使う全関数（`save_line_state`, `persist_line_start`, `save_active_line`, `abandon_line_state`, `run_line_reservation_transaction`, `materialize_line_state_projection`）。

---

### 4.11 F-21（中）— 候補モデル 4 種が未知フィールドを黙って無視する

**対象:** [`src/yt_live_kit/models/clips.py:11, 43`](../src/yt_live_kit/models/clips.py)、[`src/yt_live_kit/models/highlights.py:6, 17`](../src/yt_live_kit/models/highlights.py)

**証拠:** 各モデルの `model_config` 指定状況は次のとおり。

| モデル | 場所 | 未知フィールド |
|--------|------|----------------|
| `ClipCandidate` | clips.py:11 | **許容**（`model_config` なし = pydantic v2 既定の ignore） |
| `ClipCandidatesDocument` | clips.py:43 | **許容** |
| `HighlightSegment` | highlights.py:6 | **許容** |
| `HighlightsDocument` | highlights.py:17 | **許容** |
| `ClipCandidatesLineage` | clips.py:22 | 拒否（`extra="forbid", frozen=True, strict=True`） |
| `ShortCutDocument` | short_cut.py:17 | 拒否（`extra="forbid"`）＋ `_reject_unknown_candidate_fields` で候補内も手動チェック |

**失敗シナリオ:** `candidates.json` / `segments.json` は AI 生成物と手動編集が混ざる入力である。typo フィールドや想定外のデバッグ情報が混ざっても `ClipCandidate.model_validate` は無言で無視し、ユーザーに知らせない。一方 `ShortCutDocument` 側は「未定義の field があります」と拒否するため、**同じ形の入力でも通過するファイルと拒否されるファイルが混在する。**

**判断が必要な点:** `extra="forbid"` を追加すると、既存の `candidates.json` / `segments.json` に想定外キーが実際に入っている場合、**ロード時に破壊的変更になる**。`data/` 配下 53 動画分の実データを先に確認してから導入すること。

**修正方針:** 実データ確認後、上表の 4 モデルに `model_config = ConfigDict(extra="forbid")` を追加する。`models/short_cut.py` の手動 `_reject_unknown_candidate_fields` は冗長になりうるが、二重防御として残しても害はない。`tests/test_short_cut.py:138, 143` に相当するテストを clips / highlights 側にも追加する。

---

### 4.12 F-22（低）— `record_upload_operation` の flock 自己デッドロック地雷（現状未発火）

**対象:** [`src/yt_live_kit/services/shorts_line.py:932-980, 1240-1268`](../src/yt_live_kit/services/shorts_line.py)

**証拠:** `record_upload_operation` は `settings` が渡されると内部で `save_line_state()`（970, 979 行）を呼び、`save_line_state` は `with _line_lock(state.video_id, settings):`（1250 行）で**新しい fd を open して** `fcntl.flock(..., LOCK_EX)` する。flock は fd 単位のロックなので、同一プロセスが別 fd で LOCK_EX を保持していると 2 つ目の要求はブロックされうる。`_WRITE_LOCK` が `threading.RLock` のため Python レベルでは再入でき、この危険が隠れている。

**現状:** `run_line_reservation_transaction`（1431 行）は `record_upload_operation` を `settings` なしで呼んでおり（1460-1464 行）、ロックの二重取得を回避している。**未発火である。**

**失敗シナリオ:** 将来「一貫性のために」`record_upload_operation(..., settings=settings)` に変更されたり、他の `_line_lock` 保持区間から `settings` 付きで `record_upload_operation` / `save_line_state` を呼ぶコードが追加されると、該当スレッドが**例外にならず無期限にハングする**。

**修正方針:** `record_upload_operation` から `settings` 引数と内部の `save_line_state` 呼び出しを外し、永続化は常に呼び出し側が `_line_lock` 区間の外で行うよう統一する。もしくは `_line_lock` にスレッドローカルな再入検知を入れる。回帰テストを同時に追加すること。

**影響範囲:** 呼び出し元は `ui/components/shorts_line.py` の `record_line_upload`。なお `record_line_upload` 自体も src からの呼び出しは `video_detail.py` の import のみで、実際に呼ぶのはテストだけである（`run_line_upload_transaction` / `make_line_upload_adapter` 経由の投稿確定フローと役割が重複している可能性がある。要確認）。

---

## 5. テストの構造的な空白

### 5.1 F-10（中）— 動画詳細ページの「データあり」状態が実 Streamlit API で未検証

**対象:** [`tests/test_ui_visual_smoke.py`](../tests/test_ui_visual_smoke.py)、[`tests/test_ui_video_detail_page.py`](../tests/test_ui_video_detail_page.py)

`streamlit.testing.v1.AppTest` を使うのは `test_ui_visual_smoke.py` と `test_t1_review_app.py` のみ。前者の動画詳細テストは**未選択状態しか描画しない**。populated 状態のテストは `st.button` / `st.segmented_control` 等をほぼ全て patch したユニットテストで、実際のウィジェット呼び出し（引数名・実行時の key 衝突）は検証していない。`test_ui_video_detail_page.py` は `render_upload_section` / `render_shorts_line` 自体を `patch.object` でモック差し替えし、呼び出しの有無だけを見ている。

主要 5 コンポーネント（shorts_line / short_cut / upload / shorts_queue / storage_manager）は、`AppTest` による E2E 描画テストが**リポジトリ全体に存在しない**。1278 行・3 ワークスペースを持つ最複雑ページで実行時エラーが起きても、どのテストも検知できない。

**これが「UI 実装はテスト全パスでも実機ブラウザ確認まで済ませる」方針の構造的な根拠である。**

**修正方針:** `test_ui_visual_smoke.py` に、候補・チャプター・生成済みショートを用意した状態で materials / shorts / publish の 3 ワークスペースをそれぞれ `AppTest` で描画し、例外が出ないことを確認するケースを追加する。既存テストへの影響はない。

### 5.2 その他のテスト空白

- `ui/state.py::init_orphans_once`（起動時の孤児ジョブ close・24 時間超 cleanup・通知組み立て）の単体テストがゼロ。`test_ui_app.py` は `_record_interrupted_jobs` を AST 抽出して個別に呼ぶだけ。
- `whisper_runtime.py:1707-1722` の `shutil.rmtree` 失敗時に成功結果を FAILED へ差し替える分岐が未テスト。
- `append_batch_status` の並行書き込み（複数プロセス同時）が未テスト。`description.py` のショートテンプレートには並行性テストがあるのに非対称。
- ffmpeg が非ゼロで失敗した際に不完全な出力ファイルが残ることを検証するテストがない（`cut_clip` / `encode_segment` / `concat_segments` はいずれも失敗パスで `unlink` していない）。
- `extract_video_id` の `/embed/` パターン（ytdlp.py:75）がテストパラメータにない。`/shorts/` は未対応かつ未テスト。
- `_WRITE_LOCK` が全動画を直列化してしまうことを検出する「複数動画同時実行」テストがない（F-20 を検知できない）。
- `record_upload_operation(settings=...)` を `_line_lock` 保持区間内から呼んだ場合の自己デッドロック回帰テストがない（F-22）。将来のリファクタで踏みやすい。
- `resolve_active_line_read_only` / `load_line_state` が `LineStateError` を投げた後の UI 側の回復動作を検証するテストがない（F-19 の 2 箇所の食い違いを検知できない）。
- `ClipCandidate` / `HighlightSegment` 系が未知フィールドを許容することを保証する（または拒否すべきと主張する）テストがない。`ShortCutDocument` 側にのみ同種テストがある非対称（F-21）。

---

## 6. プロジェクト基盤

### 6.1 F-18（中）— CI / lint / 型チェックが存在しない

- `.github/workflows` ディレクトリが**存在しない**。
- `pyproject.toml` に ruff / mypy / black / flake8 の設定が**一切ない**（dev 依存は pytest のみ）。

34,864 行・1842 テストの規模で、pytest を回す CI すらない。ruff + mypy の導入と GitHub Actions での `uv run pytest -q` 実行を推奨する。

**良い点として記録しておく:** `src/` 内に TODO / FIXME / XXX / HACK が**0 件**、`print()` が**0 件**、`except Exception` 37 箇所すべてが握りつぶしなし（`pass` で終わるものが 0 件）。手動の規律は高い。

### 6.2 F-17（低）— Whisper モデルパスがマシン固有ハードコード

[`config.py:19-22`](../src/yt_live_kit/config.py) が `/Users/ryukouokumura/Library/Caches/whisper.cpp/models/...` を、`config.py:13` が `/opt/homebrew/bin/whisper-cli` を default に持つ。env 上書き可能なので動作は壊れないが、他マシンでは初回に必ず設定が要る。S9-1 の決定として意図的な pin であることはコメントに明記されている。

### 6.3 リポジトリ衛生（問題なし）

- 秘密情報のコミットなし。`.env`・`data/` は gitignore 済み、OAuth トークンは `data/_config/` 配下（[youtube_api.py:65](../src/yt_live_kit/services/youtube_api.py)）。
- `.streamlit/credentials.toml` は追跡されているが `email = ""` のみで実害なし。
- git 管理下 260 ファイル / size-pack 1.42 MiB。`data/` は 53 動画・5.5 GB で追跡外。

---

## 7. 推奨する着手順

| 順 | 対象 | 理由 |
|----|------|------|
| 1 | **F-01 / F-03 の方針決定**（復活 or 廃止） | 他の削除作業（F-04, F-05, F-13）が全てこの判断に依存する。**実装より先にユーザー確認が必要** |
| 2 | **F-19**（ライン状態の復旧導線） | 一度踏むと UI が恒久停止し、手動 JSON 削除以外に手がない。受け皿（`recover_line_state`）は既にある |
| 3 | **F-02**（Whisper 契約失効） | 潜在的だが、モデル差し替え時に静かに品質が劣化する。pin 設計の目的そのものが無効化されている |
| 4 | **F-07 / F-20 のロック区間からの外部呼び出し** | 同じ構造の問題。F-07 の HTTP タイムアウト設定は 1 行に近く効果が大きいので先に入れる |
| 5 | **F-06**（バッチ except 拡大 + try/finally） | 無人運用中の履歴全損を防ぐ |
| 6 | **F-18**（ruff + mypy + GitHub Actions） | 今の 1842 テストを回すだけでも価値がある。以降の修正の安全網になる |
| 7 | **F-04 / F-05 / F-13 の死骸削除** | 1 の判断確定後に一括で。**F-02 の修正で `should_invalidate_used_range` を再利用するので、F-02 より後に実施すること** |
| 8 | F-08, F-09, F-10, F-21 | 独立して着手可能。F-21 は実データ確認が前提 |
| 9 | F-11, F-12, F-14, F-15, F-16, F-17, F-22 | 低優先。まとめて 1 タスクにしてよい |

---

## 8. レビュー範囲と限界

**全行精読した領域（サブエージェント + オーケストレーター再検証）:**

| 領域 | 主なファイル |
|------|--------------|
| 取得層 | ytdlp.py, compressor.py, vtt_parser.py, transcript.py, _paths.py, _fsutil.py, config.py |
| Whisper / artifact | transcript_artifact.py, whisper_runtime.py, models/transcript.py |
| ショート量産 | shorts_line.py, shorts_queue.py, shorts.py, short_cut.py, models/short_cut.py, models/clips.py, models/highlights.py |
| 描画層 | ffmpeg.py, subtitle_burn.py, telop.py, clips.py, highlights.py |
| 投稿層 | upload_queue.py, schedule.py, youtube_api.py, jobs.py, channel.py |
| パイプライン | pipeline.py, batch.py, description.py, ai_prompt.py, storage.py, history.py, cli.py, commands/ |
| UI components | shorts_line.py, short_cut.py, upload.py, shorts_queue.py, status_bar.py, storage_manager.py, results.py, progress.py, clipboard.py |
| UI views | app.py, state.py, queries.py, session_keys.py, runtime_checks.py, views/, view_models/ |

**リポジトリ全体に対する機械的走査:** 到達不能な公開関数の全数検出、UI の同一リテラル widget key 重複検出（0 件）、TODO / print / 例外握りつぶしの全数検出、追跡ファイルの秘密情報検査。

**本レビューで扱っていないもの:** 実機ブラウザでの UI 動作確認、実際の YouTube API を叩く E2E、FFmpeg の出力品質そのもの、性能計測（[`docs/refactor-review-2026-08-02.md`](refactor-review-2026-08-02.md) §1 の R1 実測を参照）。
