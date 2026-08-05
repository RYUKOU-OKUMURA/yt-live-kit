"""T1-1 専用のローカル human-gold annotation UI。

起動:
    uv run streamlit run benchmarks/t1/review_app.py

本番 UI からは参照されず、packet と再生 WAV は /tmp の隔離領域だけへ保存する。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ``uv run streamlit run benchmarks/t1/review_app.py`` では script 配下だけが
# sys.path に入るため、repository root を先に足して harness を import する。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st

from benchmarks.t1 import annotation_packet as packet_tool
from benchmarks.t1.review_helpers import (
    PreparedPlayback,
    ReviewSession,
    commit_annotation,
    complete_validation,
    configured_packet_path,
    ensure_default_playback,
    existing_playback,
    load_review_session,
    prepare_playback,
    row_at,
    next_unfinished_row_index,
    unfinished_row_index,
)
from benchmarks.t1.waveform import (
    chart_columns,
    overview_and_zoom,
    read_mono_pcm16,
    row_onset_from_playback_position,
    zoom_bounds,
)


APP_TITLE = "T1-1 human gold review"


def _session_key(row_id: str, field: str) -> str:
    return f"t1_review:{row_id}:{field}"


def _selected_index(session: ReviewSession) -> int:
    rows = session.packet["rows"]
    saved = st.session_state.get("t1_review:index")
    if isinstance(saved, int) and 0 <= saved < len(rows):
        return saved
    unfinished = unfinished_row_index(session.packet)
    index = 0 if unfinished is None else unfinished
    st.session_state["t1_review:index"] = index
    return index


def _set_index(index: int, row_count: int) -> None:
    st.session_state["t1_review:index"] = max(0, min(index, row_count - 1))


def _clear_prepared(row_id: str) -> None:
    st.session_state.pop(_session_key(row_id, "prepared"), None)


def _get_prepared(
    session: ReviewSession,
    row: dict[str, Any],
    packet_path: Path,
) -> PreparedPlayback | None:
    key = _session_key(str(row["row_id"]), "prepared")
    prepared = st.session_state.get(key)
    if isinstance(prepared, PreparedPlayback):
        try:
            packet_tool._validate_receipt(
                prepared.receipt,
                row,
                packet_tool._source_entry(session.packet, row),
                session.manifest,
                packet_path=packet_path,
                **({} if prepared.is_existing else {"expected_playback_path": prepared.wav_path}),
            )
            prepared.wav_path.read_bytes()
            return prepared
        except (packet_tool.AnnotationError, OSError):
            _clear_prepared(str(row["row_id"]))
    try:
        return existing_playback(session, str(row["row_id"]), packet_path)
    except (packet_tool.AnnotationError, OSError):
        raise


def _store_prepared(prepared: PreparedPlayback) -> None:
    st.session_state[_session_key(prepared.row_id, "prepared")] = prepared


def _parse_onset(raw: str) -> int:
    value = raw.strip()
    if not value or not value.isdecimal():
        raise packet_tool.AnnotationError("発話開始位置はミリ秒の整数で入力してください。")
    return int(value)


def _load_pcm_samples(prepared: PreparedPlayback) -> tuple[list[int], int]:
    cache_key = _session_key(prepared.row_id, f"samples:{prepared.receipt['playback_wav_sha256']}")
    cached = st.session_state.get(cache_key)
    if isinstance(cached, tuple) and len(cached) == 2:
        samples, sample_rate = cached
        if isinstance(samples, list) and isinstance(sample_rate, int):
            return samples, sample_rate
    samples, sample_rate = read_mono_pcm16(prepared.wav_path)
    st.session_state[cache_key] = (samples, sample_rate)
    return samples, sample_rate


def _render_waveform(prepared: PreparedPlayback, onset_key: str) -> None:
    played_duration = int(prepared.receipt["played_duration_ms"])
    cursor_key = _session_key(prepared.row_id, "waveform_cursor_ms")
    st.session_state.setdefault(cursor_key, 0)
    cursor_ms = st.slider(
        "波形上の位置 (ms、再生窓内)",
        min_value=0,
        max_value=max(0, played_duration - 1),
        step=1,
        key=cursor_key,
    )

    samples, sample_rate = _load_pcm_samples(prepared)
    overview, zoom, _ = overview_and_zoom(samples, sample_rate, cursor_ms=int(cursor_ms))

    st.caption("全体波形（最大振幅を間引き表示。候補時刻ではなく生信号です）")
    st.area_chart(chart_columns(overview), x="ms", y="amp", width="stretch")

    zoom_start, zoom_end = zoom_bounds(played_duration, int(cursor_ms))
    st.caption(f"拡大表示: {zoom_start} ms 〜 {zoom_end} ms")
    st.area_chart(chart_columns(zoom), x="ms", y="amp", width="stretch")

    row_onset_ms = row_onset_from_playback_position(prepared.receipt, int(cursor_ms))
    st.caption(f"選択位置の row 内 onset 換算: {row_onset_ms} ms")
    if st.button("この位置を onset に入れる", key=_session_key(prepared.row_id, "apply_waveform_onset")):
        st.session_state[onset_key] = str(row_onset_ms)
        st.rerun()


def _render_navigation(session: ReviewSession, index: int) -> None:
    rows = session.packet["rows"]
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        if st.button("前の行", disabled=index == 0, key="t1_review:prev"):
            _clear_prepared(str(rows[index]["row_id"]))
            _set_index(index - 1, len(rows))
            st.rerun()
        if st.button("先頭未完了へ", key="t1_review:unfinished"):
            target = unfinished_row_index(session.packet)
            if target is not None:
                _clear_prepared(str(rows[index]["row_id"]))
                _set_index(target, len(rows))
                st.rerun()
        if st.button("次の行", disabled=index >= len(rows) - 1, key="t1_review:next"):
            _clear_prepared(str(rows[index]["row_id"]))
            _set_index(index + 1, len(rows))
            st.rerun()


def _render_complete(session: ReviewSession, packet_path: Path) -> None:
    try:
        result = complete_validation(packet_path=packet_path)
    except packet_tool.AnnotationError:
        return
    st.success("全64行の complete validator を通過しました。")
    st.json(
        {
            "row_count": result["row_count"],
            "complete_row_count": result["complete_row_count"],
            "measurement_allowed": result["measurement_allowed"],
        }
    )


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":material/hearing:")
    st.title(APP_TITLE)
    st.caption("T1-1 benchmark 専用。音声を実際に聞いた line onset だけを隔離 packet へ保存します。")

    packet_path = configured_packet_path()
    try:
        session = load_review_session(packet_path=packet_path)
    except (packet_tool.AnnotationError, OSError) as exc:
        st.error(f"packet または source の検証に失敗しました。処理を停止します。\n\n{exc}")
        st.stop()

    rows = session.packet["rows"]
    index = _selected_index(session)
    row = row_at(session.packet, index)
    row_id = str(row["row_id"])
    complete_count = session.validation["complete_row_count"]
    st.write(f"{index + 1} / {len(rows)}")
    st.progress(complete_count / len(rows), text=f"完了 {complete_count} / {len(rows)}")

    with st.container(border=True):
        st.write(f"row_id: `{row_id}`")
        st.write(f"fixture group: `{row['fixture_group']}`")
        st.subheader(str(row["target_text"]))

    _render_navigation(session, index)

    listened_key = _session_key(row_id, "listened")
    st.session_state.setdefault(listened_key, False)
    try:
        cached = _get_prepared(session, row, packet_path)
    except (packet_tool.AnnotationError, OSError) as exc:
        st.error(f"既存 playback receipt の検証に失敗しました。処理を停止します。\n\n{exc}")
        st.stop()

    try:
        with st.spinner("音声を準備しています..."):
            prepared = ensure_default_playback(
                session,
                row_id,
                packet_path,
                cached=cached,
            )
        if cached is None:
            _store_prepared(prepared)
    except (packet_tool.AnnotationError, OSError) as exc:
        st.error(f"音声の準備に失敗しました。packet は変更していません。\n\n{exc}")
        st.stop()

    with st.container(border=True):
        st.subheader("音声")
        st.caption(
            "行を開くと先頭から行末までの再生窓を自動で用意します。"
            "ブラウザの音声操作で再生・一時停止・聞き直しを行ってください。"
        )
        st.audio(prepared.wav_bytes, format="audio/wav")
        st.caption(
            f"再生窓: 開始 {prepared.receipt['played_from_ms']} ms / 長さ {prepared.receipt['played_duration_ms']} ms"
        )

    existing_gold = row["gold"] if packet_tool._validate_gold(row, require_complete=False) else packet_tool.GOLD_PLACEHOLDER
    default_onset = "" if existing_gold is packet_tool.GOLD_PLACEHOLDER else str(existing_gold["line_onset_ms"])
    onset_key = _session_key(row_id, "onset")
    annotator_key = _session_key(row_id, "annotator")
    st.session_state.setdefault(onset_key, default_onset)
    st.session_state.setdefault(annotator_key, "ryukou")

    with st.container(border=True):
        st.subheader("波形")
        st.caption("生信号の振幅です。スライダーで位置を選び、必要なら拡大表示で確認してから onset に入れてください。")
        _render_waveform(prepared, onset_key)

    with st.container(border=True):
        st.subheader("短い再生窓での最終確認")
        st.caption("耳での確認が必要なときだけ、再生窓を狭めて聞き直してください。")
        row_duration = packet_tool._row_duration_ms(row)
        from_key = _session_key(row_id, "from_ms")
        short_key = _session_key(row_id, "short_window")
        duration_key = _session_key(row_id, "duration_ms")
        st.session_state.setdefault(from_key, 0)
        st.session_state.setdefault(short_key, False)
        from_ms = st.number_input(
            "再生窓の開始 (ms)",
            min_value=0,
            max_value=max(0, row_duration - 1),
            step=100,
            key=from_key,
        )
        short_window = st.checkbox("短い再生窓を指定する", key=short_key)
        duration_ms = None
        if short_window:
            max_duration = row_duration - int(from_ms)
            st.session_state[duration_key] = min(
                int(st.session_state.get(duration_key, 5000)),
                max_duration,
            )
            duration_ms = st.number_input(
                "再生窓の長さ (ms)",
                min_value=1,
                max_value=max_duration,
                step=100,
                key=duration_key,
            )
        if st.button("再生窓を変更して聞き直す", type="secondary", key=_session_key(row_id, "prepare")):
            try:
                prepared = prepare_playback(
                    session,
                    row_id,
                    from_ms=int(from_ms),
                    duration_ms=None if duration_ms is None else int(duration_ms),
                    packet_path=packet_path,
                )
                _store_prepared(prepared)
                st.session_state[listened_key] = False
                st.rerun()
            except (packet_tool.AnnotationError, OSError) as exc:
                st.error(f"音声の準備に失敗しました。packet は変更していません。\n\n{exc}")

    with st.form(_session_key(row_id, "annotation_form"), clear_on_submit=False):
        onset_raw = st.text_input(
            "発話開始位置 (ms、row内相対)",
            placeholder="音声を聞いて整数ミリ秒を入力",
            key=onset_key,
        )
        annotator = st.text_input("annotator ID", key=annotator_key)
        listened = st.checkbox("音声を実際に確認した", key=listened_key)
        save = st.form_submit_button(
            "保存して次の未完了行へ",
            type="primary",
            key=_session_key(row_id, "save"),
        )

    if save:
        try:
            commit_annotation(
                session,
                row_id,
                _parse_onset(onset_raw),
                annotator,
                listened,
                prepared,
                packet_path=packet_path,
            )
            latest = load_review_session(packet_path=packet_path)
            target = next_unfinished_row_index(latest.packet, index)
            _clear_prepared(row_id)
            if target is not None:
                _set_index(target, len(latest.packet["rows"]))
            st.rerun()
        except (packet_tool.AnnotationError, OSError) as exc:
            st.error(f"保存できませんでした。元の packet は変更していません。\n\n{exc}")

    if unfinished_row_index(session.packet) is None:
        _render_complete(session, packet_path)
    else:
        st.info("未完了行があります。候補時刻は表示せず、人が音声を聞いて入力してください。")


if __name__ == "__main__":
    main()
