from __future__ import annotations

import json
from pathlib import Path
import wave

import pytest
from streamlit.testing.v1 import AppTest

from benchmarks.t1 import annotation_packet as packet_tool
from benchmarks.t1.review_helpers import (
    PreparedPlayback,
    commit_annotation,
    configured_packet_path,
    existing_playback,
    load_review_session,
    prepare_playback,
    unfinished_row_index,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmarks" / "t1" / "manifest.json"
APP_PATH = ROOT / "benchmarks" / "t1" / "review_app.py"


def _write_test_wav(path: Path, duration_ms: int) -> dict[str, int | str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * (duration_ms * 16))
    return packet_tool._wav_output_info(path)


def test_review_app_uses_the_fixed_default_packet_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("T1_REVIEW_PACKET_PATH", raising=False)
    assert configured_packet_path() == Path("/tmp/yt-live-kit-t1-1-human-gold.json")


def test_review_helpers_prepare_and_commit_preserve_packet_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet_path = tmp_path / "human-gold.json"
    session = load_review_session(MANIFEST_PATH, packet_path)
    row_id = session.packet["rows"][0]["row_id"]
    expected_duration = 1000

    def fake_write(source, source_span, output_path, **kwargs):
        return _write_test_wav(output_path, expected_duration)

    monkeypatch.setattr(packet_tool, "write_source_span_wav", fake_write)
    prepared = prepare_playback(
        session,
        row_id,
        from_ms=0,
        duration_ms=expected_duration,
        packet_path=packet_path,
    )
    assert prepared.is_existing is False
    assert prepared.wav_path.is_file()
    original_bytes = packet_path.read_bytes()

    validation = commit_annotation(
        session,
        row_id,
        onset_ms=250,
        annotator_id="ryukou",
        audio_listened=True,
        prepared=prepared,
        packet_path=packet_path,
    )
    assert validation["complete_row_count"] == 1
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet_path.read_bytes() != original_bytes
    assert packet["rows"][0]["gold"]["line_onset_ms"] == 250
    final_wav = Path(packet["playback_receipts"][row_id]["playback_wav_path"])
    assert final_wav.is_file()
    assert unfinished_row_index(packet) == 1
    reloaded = load_review_session(MANIFEST_PATH, packet_path)
    existing = existing_playback(reloaded, row_id, packet_path)
    assert existing is not None and existing.is_existing is True


def test_review_helper_rejects_onset_outside_playback_window_without_packet_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet_path = tmp_path / "human-gold.json"
    session = load_review_session(MANIFEST_PATH, packet_path)
    row_id = session.packet["rows"][0]["row_id"]
    expected_duration = 1000

    def fake_write(source, source_span, output_path, **kwargs):
        return _write_test_wav(output_path, expected_duration)

    monkeypatch.setattr(packet_tool, "write_source_span_wav", fake_write)
    prepared = prepare_playback(
        session,
        row_id,
        from_ms=1000,
        duration_ms=expected_duration,
        packet_path=packet_path,
    )
    original_bytes = packet_path.read_bytes()
    with pytest.raises(packet_tool.AnnotationError, match="再生した窓の外"):
        commit_annotation(
            session,
            row_id,
            onset_ms=0,
            annotator_id="ryukou",
            audio_listened=True,
            prepared=prepared,
            packet_path=packet_path,
        )
    assert packet_path.read_bytes() == original_bytes


def test_review_app_initial_render_is_local_and_blind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packet_path = tmp_path / "app-test-human-gold.json"
    monkeypatch.setenv("T1_REVIEW_PACKET_PATH", str(packet_path))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=60)

    assert not app.exception
    rendered_text = "\n".join(
        item.value
        for collection in (app.title, app.header, app.subheader, app.markdown, app.caption)
        for item in collection
    )
    assert "1 / 64" in rendered_text
    assert "t1-long-001" in rendered_text
    assert "long_single_cue" in rendered_text
    assert "candidate" not in rendered_text.lower()
    assert packet_path.is_file()


def test_review_app_save_gate_requires_listening_and_keeps_packet_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packet_path = tmp_path / "app-test-save-gate.json"
    monkeypatch.setenv("T1_REVIEW_PACKET_PATH", str(packet_path))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=60)
    original_bytes = packet_path.read_bytes()

    app.text_input(key="t1_review:t1-long-001:onset").set_value("250")
    app.button(key="t1_review:t1-long-001:save").click().run(timeout=60)

    assert packet_path.read_bytes() == original_bytes
    assert any("先に音声を再生・確認してください" in item.value for item in app.error)
