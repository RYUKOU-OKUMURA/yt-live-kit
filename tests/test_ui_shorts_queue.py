"""ショート量産 Streamlit 部品の状態境界テスト."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_kit.config import Settings
from yt_live_kit.models.clips import ClipCandidate
from yt_live_kit.models.telop import TelopScriptDocument
from yt_live_kit.services.ffmpeg import FfmpegError
from yt_live_kit.services.shorts_queue import (
    ShortsQueueItemResult,
    ShortsQueueResult,
    build_shorts_queue_targets,
    make_shorts_queue_clip_spec,
    make_shorts_queue_fingerprint,
    normalize_queue_candidates,
    run_shorts_queue_job_target,
)
from yt_live_kit.ui.components.shorts_queue import (
    _JOB_IDS_KEY,
    _confirm_queue_overwrite_dialog,
    _render_current_result,
    _render_result,
    _render_snapshot_form,
    _render_target_editor,
    _state_key,
    _start_queue_job,
    _validate_overwrite_confirmation,
    render_shorts_queue,
    start_or_confirm_line_generation,
)


def _clip() -> ClipCandidate:
    return ClipCandidate(
        id="clip_001",
        title="候補",
        start="0:00:00",
        end="0:00:10",
        duration_sec=10,
        reason="理由",
    )


def _target():
    segments = normalize_queue_candidates([_clip()], source="clips")
    return build_shorts_queue_targets(segments, mode="individual")[0]


def _document(target=None) -> TelopScriptDocument:
    target = target or _target()
    segment = target.segments[0]
    return TelopScriptDocument.model_validate(
        {
            "hook_text": "重要ポイント",
            "title_candidates": ["タイトル案"],
            "description": "説明文",
            "tags": ["タグ1", "タグ2"],
            "segments": [
                {
                    "start_sec": segment.start_ms / 1000,
                    "end_sec": segment.end_ms / 1000,
                    "lines": [
                        {
                            "text": "本文",
                            "start_sec": segment.start_ms / 1000,
                            "end_sec": segment.end_ms / 1000,
                            "emphasis": False,
                        }
                    ],
                }
            ],
        }
    )


def _confirmed_spec():
    target = _target()
    return make_shorts_queue_clip_spec(
        target,
        _document(target),
        layout="blur",
        preset="default",
        hook_preset="hook",
    )


def _result(tmp_path: Path, *, exists: bool = True) -> ShortsQueueResult:
    output = tmp_path / "short.mp4"
    if exists:
        output.write_bytes(b"video")
    item = ShortsQueueItemResult(
        target_id="target",
        status="succeeded",
        output_path=output,
        log_path=tmp_path / "short.log",
        font_warning="フォント警告",
        title_candidates=("タイトル1", "タイトル2"),
        description="説明文",
        tags=("タグ1", "タグ2"),
        error=None,
    )
    now = datetime.now(timezone.utc)
    return ShortsQueueResult(
        video_id="video-a",
        job_id="job-a",
        status="done",
        created_at=now,
        updated_at=now,
        clip_specs=(),
        items=(item,),
        success_count=1,
        failure_count=0,
        manifest_path=tmp_path / "manifest.json",
    )


def _failed_item(target_id: str = "failed-target") -> ShortsQueueItemResult:
    return ShortsQueueItemResult(
        target_id=target_id,
        status="failed",
        output_path=None,
        log_path=None,
        font_warning=None,
        title_candidates=("失敗したタイトル",),
        description="説明文",
        tags=("タグ",),
        error="subtitles フィルタを利用できません。",
    )


def _queue_result(
    tmp_path: Path,
    items: tuple[ShortsQueueItemResult, ...],
) -> ShortsQueueResult:
    now = datetime.now(timezone.utc)
    return ShortsQueueResult(
        video_id="video-a",
        job_id="job-a",
        status="done",
        created_at=now,
        updated_at=now,
        clip_specs=(),
        items=items,
        success_count=sum(item.status == "succeeded" for item in items),
        failure_count=sum(item.status == "failed" for item in items),
        manifest_path=tmp_path / "manifest.json",
    )


def test_snapshot_selection_keys_follow_source_and_candidate_id_after_reorder():
    first = _clip()
    second = first.model_copy(update={"id": "clip_002", "title": "候補 2"})
    with (
        patch(
            "yt_live_kit.ui.components.shorts_queue.st.form",
            return_value=nullcontext(),
        ),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch(
            "yt_live_kit.ui.components.shorts_queue.st.checkbox",
            return_value=False,
        ) as checkbox,
        patch("yt_live_kit.ui.components.shorts_queue.st.segmented_control"),
        patch("yt_live_kit.ui.components.shorts_queue.st.selectbox"),
        patch(
            "yt_live_kit.ui.components.shorts_queue.st.form_submit_button",
            return_value=False,
        ),
    ):
        _render_snapshot_form(
            video_id="video-a",
            source="clips",
            candidates=(first, second),
        )
        _render_snapshot_form(
            video_id="video-a",
            source="clips",
            candidates=(second, first),
        )

    prefix = _state_key("video-a", "selection")
    assert [call.kwargs["key"] for call in checkbox.call_args_list] == [
        f"{prefix}_clips_{first.id}",
        f"{prefix}_clips_{second.id}",
        f"{prefix}_clips_{second.id}",
        f"{prefix}_clips_{first.id}",
    ]
    assert [call.args[0] for call in checkbox.call_args_list] == [
        "0:00:00 → 0:00:10 / 10 秒 / 候補",
        "0:00:00 → 0:00:10 / 10 秒 / 候補 2",
        "0:00:00 → 0:00:10 / 10 秒 / 候補 2",
        "0:00:00 → 0:00:10 / 10 秒 / 候補",
    ]


def test_duplicate_candidate_ids_fail_closed_before_checkbox_rendering():
    first = _clip()
    duplicate = first.model_copy(update={"title": "重複候補"})
    state: dict[str, object] = {}
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state),
        patch("yt_live_kit.ui.components.shorts_queue.st.divider"),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch(
            "yt_live_kit.ui.components.shorts_queue.st.segmented_control",
            return_value="clips",
        ),
        patch("yt_live_kit.ui.components.shorts_queue.st.checkbox") as checkbox,
        patch("yt_live_kit.ui.components.shorts_queue.st.error") as error,
    ):
        render_shorts_queue(
            video_id="video-a",
            title="動画 A",
            clip_candidates=(first, duplicate),
            highlight_candidates=(),
            settings=MagicMock(),
        )

    checkbox.assert_not_called()
    error.assert_called_once()
    assert "選択済み候補 ID が重複しています" in error.call_args.args[0]
    assert _state_key("video-a", "snapshot") not in state


def test_start_queue_stores_job_id_by_video_and_updates_global_status():
    state: dict[str, object] = {}
    settings = MagicMock()
    spec = MagicMock()
    spec.to_dict.return_value = {"spec": 1}
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state),
        patch("yt_live_kit.ui.components.shorts_queue.is_busy", return_value=False),
        patch(
            "yt_live_kit.ui.components.shorts_queue.ensure_subtitles_filter",
            return_value="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        ),
        patch("yt_live_kit.ui.components.shorts_queue.start_job", return_value="job-1") as start,
        patch("yt_live_kit.ui.components.shorts_queue.set_active_job_id") as active,
        patch("yt_live_kit.ui.components.shorts_queue.st.rerun"),
    ):
        _start_queue_job(
            video_id="video-a", title="動画", specs=(spec,), settings=settings
        )
    assert state[_JOB_IDS_KEY] == {"video-a": "job-1"}
    start.assert_called_once()
    assert start.call_args.args[:2] == (
        "shorts_queue",
        run_shorts_queue_job_target,
    )
    assert start.call_args.kwargs["video_id"] == "video-a"
    assert start.call_args.kwargs["title"] == "動画"
    assert start.call_args.kwargs["total"] == 1
    assert start.call_args.kwargs["clip_spec_dicts"] == [{"spec": 1}]
    active.assert_called_once_with("job-1")


def test_start_queue_busy_does_not_call_start_job():
    with (
        patch("yt_live_kit.ui.components.shorts_queue.is_busy", return_value=True),
        patch("yt_live_kit.ui.components.shorts_queue.start_job") as start,
        patch("yt_live_kit.ui.components.shorts_queue.st.error"),
    ):
        _start_queue_job(
            video_id="video-a", title="動画", specs=(MagicMock(),), settings=MagicMock()
        )
    start.assert_not_called()


def test_start_queue_rejects_missing_subtitles_capability_before_job_or_manifest(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        ffmpeg_path="/opt/homebrew/bin/ffmpeg",
    )
    with (
        patch("yt_live_kit.ui.components.shorts_queue.is_busy", return_value=False),
        patch(
            "yt_live_kit.ui.components.shorts_queue.ensure_subtitles_filter",
            side_effect=FfmpegError(
                "指定された FFmpeg で subtitles フィルタを利用できません。"
                "macOS では ffmpeg-full を導入し、YTLK_FFMPEG_PATH に"
                "ffmpeg-full の ffmpeg 実体パスを設定してください。"
                "（検査対象: /opt/homebrew/bin/ffmpeg）"
            ),
        ) as preflight,
        patch("yt_live_kit.ui.components.shorts_queue.start_job") as start,
        patch("yt_live_kit.ui.components.shorts_queue.st.error") as error,
    ):
        _start_queue_job(
            video_id="video-a",
            title="動画",
            specs=(_confirmed_spec(),),
            settings=settings,
        )
        _start_queue_job(
            video_id="video-a",
            title="動画",
            specs=(_confirmed_spec(),),
            settings=settings,
        )

    start.assert_not_called()
    assert preflight.call_count == 2
    assert not (tmp_path / "video-a" / "shorts" / "queue").exists()
    assert not (tmp_path / "_jobs").exists()
    message = error.call_args.args[0]
    assert "/opt/homebrew/bin/ffmpeg" in message
    assert "ffmpeg-full" in message
    assert "YTLK_FFMPEG_PATH" in message


def test_overwrite_dialog_only_starts_after_confirmation():
    kwargs = {
        "video_id": "video-a",
        "title": "動画",
        "specs": (MagicMock(),),
        "snapshot_fingerprint": "fingerprint",
        "existing_names": ("short_id.mp4",),
        "settings": MagicMock(),
    }
    with (
        patch("yt_live_kit.ui.components.shorts_queue.is_busy", return_value=False),
        patch("yt_live_kit.ui.components.shorts_queue.st.warning"),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.button", side_effect=[False, True]),
        patch(
            "yt_live_kit.ui.components.shorts_queue._validate_overwrite_confirmation",
            return_value=(True, None),
        ),
        patch("yt_live_kit.ui.components.shorts_queue._start_queue_job") as start,
    ):
        _confirm_queue_overwrite_dialog.__wrapped__(**kwargs)
        start.assert_not_called()
        _confirm_queue_overwrite_dialog.__wrapped__(**kwargs)
    start.assert_called_once_with(
        video_id="video-a",
        title="動画",
        specs=kwargs["specs"],
        settings=kwargs["settings"],
    )


def test_line_generation_keeps_overwrite_dialog_for_existing_old_output(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    spec = _confirmed_spec()
    output = tmp_path / "video-a" / "shorts" / "output" / spec.output_name
    output.parent.mkdir(parents=True)
    output.write_bytes(b"old-review-output")
    with (
        patch(
            "yt_live_kit.ui.components.shorts_queue._confirm_queue_overwrite_dialog"
        ) as dialog,
        patch("yt_live_kit.ui.components.shorts_queue._start_queue_job") as start,
    ):
        start_or_confirm_line_generation(
            video_id="video-a",
            title="動画",
            spec=spec,
            snapshot_fingerprint="a" * 64,
            settings=settings,
        )

    dialog.assert_called_once()
    assert dialog.call_args.kwargs["specs"] == (spec,)
    assert dialog.call_args.kwargs["existing_names"] == (spec.output_name,)
    start.assert_not_called()


def test_overwrite_confirmation_revalidates_current_snapshot_specs_and_outputs(
    tmp_path: Path,
):
    settings = Settings(data_dir=tmp_path)
    candidate = _clip()
    segments = normalize_queue_candidates([candidate], source="clips")
    target = build_shorts_queue_targets(segments, mode="individual")[0]
    spec = _confirmed_spec()
    fingerprint = make_shorts_queue_fingerprint(
        video_id="video-a",
        source="clips",
        mode="individual",
        original_candidates=[candidate],
        segments=segments,
        layout="blur",
        preset="default",
        hook_preset="hook",
    )
    output = tmp_path / "video-a" / "shorts" / "output" / spec.output_name
    output.parent.mkdir(parents=True)
    output.write_bytes(b"video")
    state = {
        _state_key("video-a", "snapshot"): {
            "fingerprint": fingerprint,
            "source": "clips",
            "mode": "individual",
            "layout": "blur",
            "preset": "default",
            "hook_preset": "hook",
            "selected_ids": (candidate.id,),
            "targets": (target,),
        },
        _state_key("video-a", "confirmed"): {target.target_id: spec},
    }
    document = MagicMock(candidates=[candidate])
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state),
        patch(
            "yt_live_kit.ui.components.shorts_queue.get_selected_video_id",
            return_value="video-a",
        ),
        patch(
            "yt_live_kit.ui.components.shorts_queue.load_candidates_file",
            return_value=document,
        ),
        patch("yt_live_kit.ui.components.shorts_queue.is_busy", return_value=False),
    ):
        assert _validate_overwrite_confirmation(
            video_id="video-a",
            specs=(spec,),
            snapshot_fingerprint=fingerprint,
            existing_names=(spec.output_name,),
            settings=settings,
        ) == (True, None)
        stale_spec = MagicMock()
        allowed, message = _validate_overwrite_confirmation(
            video_id="video-a",
            specs=(stale_spec,),
            snapshot_fingerprint=fingerprint,
            existing_names=(spec.output_name,),
            settings=settings,
        )
    assert allowed is False
    assert "台本" in str(message)


def test_overwrite_dialog_stale_confirmation_never_starts():
    kwargs = {
        "video_id": "video-a",
        "title": "動画",
        "specs": (MagicMock(),),
        "snapshot_fingerprint": "old",
        "existing_names": ("short_id.mp4",),
        "settings": MagicMock(),
    }
    with (
        patch("yt_live_kit.ui.components.shorts_queue.is_busy", return_value=False),
        patch("yt_live_kit.ui.components.shorts_queue.st.warning"),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.button", return_value=True),
        patch(
            "yt_live_kit.ui.components.shorts_queue._validate_overwrite_confirmation",
            return_value=(False, "選択内容が変わりました。"),
        ),
        patch("yt_live_kit.ui.components.shorts_queue.st.error") as error,
        patch("yt_live_kit.ui.components.shorts_queue._start_queue_job") as start,
    ):
        _confirm_queue_overwrite_dialog.__wrapped__(**kwargs)
    start.assert_not_called()
    error.assert_called_once()


def test_current_result_is_video_scoped_across_a_b_a_switch():
    state = {_JOB_IDS_KEY: {"video-a": "job-a"}}
    exact = MagicMock(name="a-result")
    latest_b = MagicMock(name="b-result")
    settings = MagicMock()
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state),
        patch(
            "yt_live_kit.ui.components.shorts_queue.load_shorts_queue_result",
            return_value=exact,
        ) as load_exact,
        patch(
            "yt_live_kit.ui.components.shorts_queue.load_latest_shorts_queue_result",
            return_value=latest_b,
        ) as load_latest,
        patch("yt_live_kit.ui.components.shorts_queue._render_result") as render,
    ):
        _render_current_result("video-a", settings)
        _render_current_result("video-b", settings)
        _render_current_result("video-a", settings)
    assert load_exact.call_count == 2
    load_exact.assert_called_with("video-a", "job-a", settings)
    load_latest.assert_called_once_with("video-b", settings)
    assert render.call_args_list[0].args == (exact,)
    assert render.call_args_list[1].args == (latest_b,)
    assert render.call_args_list[2].args == (exact,)


def test_new_job_without_manifest_shows_preparing_not_old_latest():
    state = {_JOB_IDS_KEY: {"video-a": "new-job"}}
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state),
        patch(
            "yt_live_kit.ui.components.shorts_queue.load_shorts_queue_result",
            return_value=None,
        ),
        patch(
            "yt_live_kit.ui.components.shorts_queue.load_latest_shorts_queue_result"
        ) as latest,
        patch("yt_live_kit.ui.components.shorts_queue.st.info") as info,
        patch("yt_live_kit.ui.components.shorts_queue._render_result") as render,
    ):
        _render_current_result("video-a", MagicMock())
    latest.assert_not_called()
    render.assert_not_called()
    info.assert_called_once()


def test_target_codex_runs_only_on_explicit_button():
    target = _target()
    snapshot = {
        "fingerprint": "f" * 64,
        "layout": "blur",
        "preset": "default",
        "hook_preset": "hook",
    }
    state: dict[str, object] = {}
    generated = MagicMock(document=_document(target))
    with ExitStack() as stack:
        stack.enter_context(
            patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state)
        )
        stack.enter_context(
            patch(
                "yt_live_kit.ui.components.shorts_queue.st.container",
                return_value=nullcontext(),
            )
        )
        for command in ("markdown", "caption", "error", "rerun"):
            stack.enter_context(
                patch(f"yt_live_kit.ui.components.shorts_queue.st.{command}")
            )
        stack.enter_context(
            patch(
                "yt_live_kit.ui.components.shorts_queue.st.button",
                return_value=False,
            )
        )
        generate = stack.enter_context(
            patch("yt_live_kit.ui.components.shorts_queue.generate_telop_script")
        )
        _render_target_editor(
            video_id="video-a",
            snapshot=snapshot,
            target=target,
            settings=MagicMock(),
        )
    generate.assert_not_called()

    state.clear()
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.session_state", state),
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.error"),
        patch("yt_live_kit.ui.components.shorts_queue.st.rerun"),
        patch("yt_live_kit.ui.components.shorts_queue.st.button", return_value=True),
        patch(
            "yt_live_kit.ui.components.shorts_queue.generate_telop_script",
            return_value=generated,
        ) as generate,
    ):
        _render_target_editor(
            video_id="video-a",
            snapshot=snapshot,
            target=target,
            settings=MagicMock(),
        )
    generate.assert_called_once()


def test_result_uses_path_video_callable_download_and_unique_copy_keys(tmp_path: Path):
    result = _result(tmp_path)
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.write"),
        patch("yt_live_kit.ui.components.shorts_queue.st.warning"),
        patch("yt_live_kit.ui.components.shorts_queue.st.video") as video,
        patch("yt_live_kit.ui.components.shorts_queue.st.download_button") as download,
        patch("yt_live_kit.ui.components.shorts_queue.render_copy_button") as copy,
    ):
        _render_result(result)
    output = result.items[0].output_path
    video.assert_called_once_with(output)
    kwargs = download.call_args.kwargs
    assert callable(kwargs["data"])
    with kwargs["data"]() as handle:
        assert handle.read() == b"video"
    assert kwargs["on_click"] == "ignore"
    assert kwargs["width"] == "stretch"
    assert [item.kwargs["key"] for item in copy.call_args_list] == [
        "job-a_target_titles",
        "job-a_target_description",
        "job-a_target_tags",
    ]
    assert copy.call_args_list[-1].args[0] == "タグ1,タグ2"


def test_all_failed_result_is_not_rendered_as_success(tmp_path: Path) -> None:
    result = _queue_result(tmp_path, (_failed_item(),))
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.error") as error,
        patch("yt_live_kit.ui.components.shorts_queue.st.warning") as warning,
        patch("yt_live_kit.ui.components.shorts_queue.st.success") as success,
    ):
        _render_result(result)

    success.assert_not_called()
    warning.assert_not_called()
    messages = [call.args[0] for call in error.call_args_list]
    assert any("すべてのショート生成に失敗" in message for message in messages)
    assert any("subtitles フィルタを利用できません" in message for message in messages)


def test_interrupted_result_is_not_rendered_as_done_and_has_recovery_action(
    tmp_path: Path,
) -> None:
    result = replace(
        _queue_result(tmp_path, (_failed_item(),)),
        status="interrupted",
        clip_specs=(_confirmed_spec(),),
    )
    settings = Settings(data_dir=tmp_path)
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.error") as error,
        patch("yt_live_kit.ui.components.shorts_queue.st.button", return_value=True),
        patch(
            "yt_live_kit.ui.components.shorts_queue._confirm_interrupted_queue_recovery_dialog"
        ) as dialog,
        patch("yt_live_kit.ui.components.shorts_queue.st.video") as video,
    ):
        _render_result(
            result,
            video_id="video-a",
            title="元動画",
            settings=settings,
        )

    assert any("中断" in call.args[0] and "完了扱い" in call.args[0] for call in error.call_args_list)
    dialog.assert_called_once()
    video.assert_not_called()


def test_partial_result_shows_counts_and_individual_failure_reason(
    tmp_path: Path,
) -> None:
    succeeded = _result(tmp_path).items[0]
    result = _queue_result(tmp_path, (succeeded, _failed_item()))
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption") as caption,
        patch("yt_live_kit.ui.components.shorts_queue.st.write"),
        patch("yt_live_kit.ui.components.shorts_queue.st.error") as error,
        patch("yt_live_kit.ui.components.shorts_queue.st.warning") as warning,
        patch("yt_live_kit.ui.components.shorts_queue.st.success") as success,
        patch("yt_live_kit.ui.components.shorts_queue.st.video"),
        patch("yt_live_kit.ui.components.shorts_queue.st.download_button"),
        patch("yt_live_kit.ui.components.shorts_queue.render_copy_button"),
    ):
        _render_result(result)

    success.assert_not_called()
    assert "成功 1 件 / 失敗 1 件" in caption.call_args.args[0]
    assert any(
        "一部のショート生成に失敗" in call.args[0]
        for call in warning.call_args_list
    )
    assert "subtitles フィルタを利用できません" in error.call_args.args[0]


def test_download_callable_reports_japanese_error_if_file_deleted_after_render(
    tmp_path: Path,
):
    result = _result(tmp_path)
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.write"),
        patch("yt_live_kit.ui.components.shorts_queue.st.warning"),
        patch("yt_live_kit.ui.components.shorts_queue.st.video"),
        patch("yt_live_kit.ui.components.shorts_queue.st.download_button") as download,
        patch("yt_live_kit.ui.components.shorts_queue.render_copy_button"),
    ):
        _render_result(result)
    result.items[0].output_path.unlink()
    with pytest.raises(OSError, match="動画ファイルを開けませんでした"):
        download.call_args.kwargs["data"]()


def test_result_path_oserror_is_converted_to_warning(tmp_path: Path):
    result = _result(tmp_path)
    broken_path = MagicMock(spec=Path)
    broken_path.is_file.side_effect = OSError("race")
    broken_item = replace(result.items[0], output_path=broken_path)
    broken_result = replace(result, items=(broken_item,))
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.write"),
        patch("yt_live_kit.ui.components.shorts_queue.st.warning") as warning,
        patch("yt_live_kit.ui.components.shorts_queue.st.video") as video,
        patch("yt_live_kit.ui.components.shorts_queue.st.download_button") as download,
        patch("yt_live_kit.ui.components.shorts_queue.render_copy_button"),
    ):
        _render_result(broken_result)
    assert "確認できませんでした" in warning.call_args_list[0].args[0]
    video.assert_not_called()
    download.assert_not_called()


def test_missing_result_file_skips_video_and_download(tmp_path: Path):
    result = _result(tmp_path, exists=False)
    with (
        patch("yt_live_kit.ui.components.shorts_queue.st.container", return_value=nullcontext()),
        patch("yt_live_kit.ui.components.shorts_queue.st.markdown"),
        patch("yt_live_kit.ui.components.shorts_queue.st.caption"),
        patch("yt_live_kit.ui.components.shorts_queue.st.write"),
        patch("yt_live_kit.ui.components.shorts_queue.st.warning") as warning,
        patch("yt_live_kit.ui.components.shorts_queue.st.video") as video,
        patch("yt_live_kit.ui.components.shorts_queue.st.download_button") as download,
        patch("yt_live_kit.ui.components.shorts_queue.render_copy_button"),
    ):
        _render_result(result)
    warning.assert_called()
    video.assert_not_called()
    download.assert_not_called()
