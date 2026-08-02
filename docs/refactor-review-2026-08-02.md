# 全体リファクタリング・性能・長期運用レビュー

**日付:** 2026-08-02  
**対象:** `yt-live-kit` 全体  
**タスク:** R1  
**方針:** 既存挙動と安全境界を守る局所修正を先に行い、生成方式・永続化 schema・プロセス間排他へ触れる変更は個別タスクとして計測と設計を先行させる。

## 1. 回帰基準と実測

**計測環境:** MacBook Pro `Mac14,7`、Apple M2、16 GB、macOS 26.5.2。2026-08-02 に repository root から `uv run` で実行した。履歴と manifest は同一 process の warm 計測 30 回、SHA-256 と `yt-dlp --version` は 10 回。対象 SHA は `data/nfzo_dKvN10/shorts/output/short_8beaa023c04b.mp4`、20,328,048 bytes。FFmpeg の 60 秒 / 48 秒は既存運用ログの 1 観測であり、codec、fixture、反復を固定した G1 前は傾向としてだけ扱う。

| 項目 | 実測 | 判定 |
|------|------|------|
| 全テスト | 変更前 1063 passed / 2 skipped、変更後 1074 passed / 2 skipped | 回帰なし、新規 11 tests |
| 処理済み動画 | 48 本。30 回 min 2.44 / mean 2.65 / p95 2.95 / max 3.59 ms | 今回の規模では優先度低 |
| queue manifest 全件走査 | 48 本。30 回 min 3.84 / mean 4.19 / p95 4.58 / max 6.75 ms | 件数増加時の候補。現在は保留 |
| `yt-dlp --version` | 10 回 min 207.29 / mean 237.28 / p95 241.85 / max 277.95 ms | 全 rerun から外す効果を再計測する |
| FFmpeg capability | 約 61 ms / 呼び出し | fail-fast 要件を維持した binary identity cache が将来候補 |
| FFmpeg 設定診断 | 約 120〜150 ms / 設定画面 rerun | 手動再検査付き cache が将来候補 |
| mp4 内容 SHA-256 | 10 回 min 56.38 / mean 65.25 / p95 60.85 / max 131.43 ms | 工程 6 直前の厳格な内容再検証は維持 |
| 60 秒ショートの最終 FFmpeg pass | 1 観測で約 48 秒、約 1.25 倍速 | G1 で反復・区間別に再計測 |
| データ使用量 | 約 1.5 GB、空き約 113 GB | 直近の容量問題なし |

今回の環境では、テストや JSON 読込より FFmpeg encode と Streamlit rerun ごとの外部バイナリ起動が大きい。生成全体の主ボトルネック断定は G1 の end-to-end stage breakdown 後に行う。

## 2. R1 で直した項目

| 優先 | 項目 | 理由 | 安全境界 |
|------|------|------|----------|
| P1 | 未完了 queue manifest の予約拒否 | クラッシュ後の `running` manifest に部分成功 mp4 があると、完成していない batch を予約対象にできる | `status=done` だけを予約可能にする。既存の表示・復旧用読込は維持 |
| P1 | 旧履歴の datetime 正規化 | aware / naive datetime が混在すると一覧 sort が `TypeError` で落ちる | UTC 正規化してから比較し、保存済み metadata は破壊しない |
| P1 | 旧単体生成の atomic 出力 | `build_short()` は完成 mp4 を直接上書きし、失敗や中断で以前の成果物を壊し得る | 同じ出力ディレクトリの一時 mp4 を検証後に `replace()` |
| P2 | `yt-dlp --version` の rerun cache | 全 rerun で平均約 237 ms の subprocess が走る | resolved path、device、inode、size、`mtime_ns`、設定 path、timeout を key に TTL 600 秒・最大 4 件。警告文字列だけを cache し fetch 本体は変えない |
| P2 | 依存・FFmpeg 契約の文書整合 | stateful expander は Streamlit 1.55 で導入された一方、最低版が 1.40。seek の文書と実装・テストが逆 | 最低版を 1.55、lock 解決版を 1.60 と区別する。seek 順は変更せず現行 input seek を正本として文書を直し、G1 で境界 frame と速度を比較 |

5 項目はすべて実装済み。安全修正は commit `be83adb`、依存・文書・rerun cache は `a969681` に分けた。独立レビューは safety / performance の 2 系統で行い、未解消 blocker はない。

**rerun 再計測:** cache miss は 240.56 ms、同じ binary identity / path / timeout の cache hit 30 回は min 0.178 / mean 0.215 / p95 0.222 / max 0.899 ms。通常 rerun から平均約 237 ms の subprocess 待ちを外せた。これは Streamlit runtime 外の `MemoryCacheStorageManager` での再計測であり、実ブラウザでは U6-9 の操作確認時にも体感と warning 更新を確認する。

## 3. H1 / G1 として分離した構造課題

### H1-1: jobs のプロセス間排他と pointer fail-closed

**再現条件:** Streamlit を複数プロセスで起動する、または `data/_jobs/current.json` が途中書込み・破損・指し先欠落になる。  
**影響:** `_START_LOCK` は同一プロセス内だけなので二重開始できる。`read_current_job()` が壊れた pointer を `None` とし、実 worker が動作中でも `is_busy()` が false になり得る。固定名 `.current.tmp` もプロセス間で衝突する。  
**推奨:** data root 単位の advisory lock、PID と owner token、UUID temp、flush と fsync、pointer が壊れた際の running job scan を一体で実装する。thread-only テストに multiprocessing 境界を追加する。

### H1-3: queue crash recovery

**再現条件:** 複数 item のうち一部成功後に process が終了する。  
**影響:** manifest が `running` のまま残り、失敗理由や再実行境界が明確でない。R1 の予約 gate で誤投稿は止められるが、復旧 UX は残る。  
**推奨:** `interrupted` 等の terminal state、owner job ID、再起動 recovery table、既存成功 item の再利用可否を schema として先に定義する。

### H1-2: video ID の path confinement

**再現条件:** service / CLI へ `..` を渡す、metadata を手編集する、custom yt-dlp が不正 ID を返す、または data root 内に外向き symlink がある。  
**影響:** queue path 等が `data_dir` の外へ解決され得る。通常の YouTube ID では起きにくいが、サービス境界として一貫していない。  
**推奨:** 文字種検査だけでなく `resolve()` 後の root containment と symlink 方針を持つ共通 helper を導入し、全 raw `data_dir / video_id` を段階移行する。

### H1-5: 公開後 polling の運用接続

**再現条件:** 予約時刻後もアプリを再起動しない、または公開状態を手動確認しない。  
**影響:** `poll_publication_status()` は存在するが本番 upload job から自動実行されず、operation の publication eligibility が unknown のまま残る。  
**要件状態:** P3 の 1 本では手動接続して公開前後を確認済みだが、通常 operation の production 導線としては FR-27 / AC-28 が未完了。  
**推奨:** upload job と分離した明示 CTA または時刻後の bounded follow-up job を設計する。YouTube API quota と再試行の安全契約を先に固定する。

### H1-4: token とローカル設定の atomic 保存・排他

**再現条件:** OAuth token refresh 中の中断、複数 tab の同時 read-modify-write。  
**影響:** token JSON の切断、ローカル ID 集合の lost update が起き得る。  
**推奨:** 同一ディレクトリ temp、権限 600、flush と fsync、atomic replace、advisory lock、lock 内での再読込と merge を共通化する。

### G1: FFmpeg single-pass benchmark

**観測:** 60 秒の最終 layout / subtitle pass だけで約 48 秒かかり、前段の区間別 encode と合わせて同じ映像を複数回 encode している。  
**可能性:** trim、setpts、scale / crop、concat、ASS burn を 1 filter graph に統合できれば、生成時間を大きく短縮できる余地がある。  
**保留理由:** 現要件は区間別 encode、concat、最終 pass の既存経路を固定しており、frame accuracy、音声同期、字幕時刻、VFR 素材、途中失敗時の既存出力保護へ影響する。  
**推奨:** 代表素材 3 種以上で現行 input seek、output seek、single-pass 試作の wall time、出力尺、先頭・末尾 frame、音声同期、字幕 cue、画質を比較する benchmark を先行し、十分な差が出た場合だけ要件を改訂する。

## 4. 今回行わない変更

- 全ファイルの formatter / lint 一括適用。diff が大きくなり、意味変更のレビューを妨げるため
- 1000 行級 UI / service の無計測な全面分割。まず public contract と状態遷移の characterization test が必要なため
- mp4 内容 SHA-256 を size / mtime だけで代替する cache。工程 6 直前の内容一致要件を弱めるため
- FFmpeg capability 検査の削除。生成開始前に字幕 filter 非対応を fail-fast する要件があるため
- queue manifest 全件読込や transcript の先行最適化。現データでは 1〜5 ms 程度で効果が小さいため
- Whisper、BGM、transition、外部 API 等の機能追加。R1 は既存範囲の非回帰監査であるため

## 5. 推奨順

1. R1 の局所修正と全件回帰 — 完了
2. H1-1 jobs hardening
3. H1-5 公開後 polling の運用接続
4. H1-2 path confinement
5. H1-3 queue crash recovery
6. H1-4 atomic persistence の共通化
7. G1 FFmpeg single-pass benchmark

H1-1 / H1-2 は長期運用でのデータ整合性に直結し、H1-5 は現行 FR の production 接続ギャップである。G1 は生成速度への効果が最も大きい可能性があるが、安全性と品質の比較証跡を作ってから着手する。各 ID の変更範囲、Done、依存、見積もりは `docs/execution-plan-v3.md` を正本とする。
