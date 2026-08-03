# S9-1-AUDIT-APPLY 独立レビュー記録

## 判定

S9-1 は A（operational transcript reference adoption）として Go。採用モデルは `ggml-large-v3-turbo-q5_0`。これは exact transcript の承認でも、boundary automation の採用でもない。

fixture の `gold_audit_status` は `unverified_provisional` のまま保持し、ユーザー原文「4本とも文字起こしは概ね問題なし」は `displayed transcript content` の operational benchmark reference に限って採用した。glossary 個別 exact、character / punctuation exactness、cue anchor exact milliseconds は未承認・未主張である。既存 boundary audit は partial のまま保存し、automation は不採用、人の preview / 区間確認を必須とする。

## 独立レビュー結果

修正後の読み取り専用レビューでは、次のP0〜P3指摘を確認し、該当する問題を修正して focused suite と全回帰を再実行した。

- P0: なし。
- P1: raw report の model、audio / baseline VTT input、runtime、range、run-kind identity の fail-open。production hash artifactへ結び付けたstrict validator、cross-model input/runtime照合、mutation testsで修正。
- P1: operational Go と boundary No-Go の判定空間混在。`decision.operational_transcript_decision`、`decision.boundary_decision`、`decision.exact_transcript_decision` へ分離し、`decision_scope` と S9-2 start scope を明記。
- P1: fixture gold が audited 相当に見える status。canonical report の `gold_audit_status` を `unverified_provisional` に戻し、transcript reference status を別 field にした。
- P2: production hash artifact のJSON同士だけの比較。before / after artifact の全15ファイルを実体へ再照合する fail-closed checkを追加。
- P2: packet / protocol の旧No-Go・final3 path。audit-apply report、q5採用、exact未承認、boundary不採用の記述へ再生成・更新。
- P3: legacy tie-break key、raw identity、packet/report整合の直接テスト不足。`declared_before_results` 不在、runtime flags、16 case run、packet/protocol現行記述、model/audio/VTT/run-kind/runtime/range mutation拒否を固定。

最終読み取り専用 reviewer Kuhn（019fc716-5ed6-7963-9090-4c076ec155a2）は、VTT identity 修正後の現行ファイルを確認し、P0/P1/P2/P3 すべてなし、PASS と判定した。review artifact の全回帰証跡は `1438 passed, 2 skipped` であることを確認している。

独立 reviewer は読み取り専用で、production、audio/model cache、学習ログ、commitを変更していない。

## 証跡と判定根拠

- benchmark fixture fingerprint: `6dae657f2b803c54c6af1afe4ed54ad4f447324c32802e1943dc5711a9bf1718`
- human audit fingerprint: `9c1fdca9e1c5b70bd40d84a219a81dedca976e70447d42e2523e2fc4b16cc263`
- boundary audit fingerprint: `0af9f5ce7888eabcc67fbe767db25c2e4da97c823ea76781eb9aeb25991fd9a1`
- q5 / turbo の cold / warm 各4 case、合計16 case runsは成功し、cold / warm output SHA equalityも確認した。
- q5 の paired median CER相対改善は78.69％、turboは80.85％。既存numeric threshold、fixture、range、音声bytes / SHA、model SHA、runtime / decode設定は変更していない。
- 両候補のnumeric gateとoperational transcript reference gateは通過。q5はlocal-only、worst-case wait、全体wait、memory、model bytes、case品質のlexicographic tie-breakで選択した。
- tie-break metadataは `declared_before_audit_apply_rerun=true`、`prior_provisional_results_known=true`、`policy_basis=user_wait_time_and_local_constraints`。`declared_before_results` は存在せず、pass閾値は変更していない。
- canonical report v5 は raw model / audio / baseline VTT / runtime / range / run-kind identity、production実体hash再照合、再現command、15 production files unchanged、S9-3の唯一のq5参照を記録する。runtime identity flagsはcanonical JSONでtrue。

## テスト

- focused S9 suite: 58 passed
- full `uv run pytest`: 1438 passed, 2 skipped
- `uv lock --check`: PASS
- `git diff --check`: PASS
- human audit packet check: PASS

## 残余リスク

- 「概ね問題なし」は文字・句読点完全一致を保証しない。glossaryとcue-msも未監査である。
- coldはOS page cacheを消去した完全coldではない。warmは別process invocationの再利用観測で、永続artifact cache hitは計測・主張していない。
- mKwn、CGal、hPeの音声は公開YouTubeからのaudio-only spanで、取得client / network条件差が残る。
- candidate cueはrawのままで、boundary所見の約秒数はproduction閾値ではない。
- runtime、cache、artifact、人確認の失敗時は既存YouTube VTTへfallbackする。S9-2以降の境界自動化は解禁しない。
