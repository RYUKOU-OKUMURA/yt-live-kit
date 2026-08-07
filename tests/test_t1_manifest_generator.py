from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks.t1.annotation_packet import (
    PRODUCTION_DATA_ROOT,
    load_manifest,
    manifest_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmarks" / "t1" / "manifest.json"
GENERATOR_PATH = ROOT / "benchmarks" / "t1" / "generate_manifest_v35.py"

# production データ（data/ 配下、gitignore 対象）が存在しない環境（CI 等）では、
# generator が validate_manifest(check_sources=True) / b5d ASS evidence を検証する際に
# production ファイルの不在で必ず失敗する。その環境では実行不能なテストとして明示的に skip する。
_requires_production_data = pytest.mark.skipif(
    not Path(PRODUCTION_DATA_ROOT).is_dir(),
    reason="production データ（gitignore 対象の data/ 配下）が無い環境では実行できません。",
)


def _copy_previous(tmp_path: Path) -> Path:
    previous = tmp_path / "previous.json"
    previous.write_bytes(MANIFEST_PATH.read_bytes())
    return previous


def _copy_d94_previous(tmp_path: Path) -> Path:
    previous = tmp_path / "d94-previous.json"
    completed = subprocess.run(
        ["git", "show", "d94a8c5:benchmarks/t1/manifest.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    previous.write_bytes(completed.stdout)
    return previous


def _run_generator(previous: Path, output: Path, test_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(GENERATOR_PATH),
        "--previous",
        str(previous),
        "--output",
        str(output),
    ]
    if test_root is not None:
        command.extend(["--test-root", str(test_root)])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)


@_requires_production_data
def test_generator_is_idempotent_from_current_canonical_manifest(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH, check_sources=True)
    assert manifest["manifest_revision"] == "corrected_pre_measurement_freeze_v3_7"
    assert manifest["manifest_fingerprint"] == "b4f33f33c6b7f23be0d28c13bdb0bdde946659a7a8f9909894e8b0d4807a11ec"
    previous = _copy_previous(tmp_path)
    output = tmp_path / "generated.json"
    completed = _run_generator(previous, output, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == manifest


@_requires_production_data
def test_generator_reproduces_v37_from_committed_d94_v35_predecessor(tmp_path: Path) -> None:
    previous = _copy_d94_previous(tmp_path)
    output = tmp_path / "generated-from-d94.json"
    completed = _run_generator(previous, output, tmp_path)
    assert completed.returncode == 0, completed.stderr
    candidate = json.loads(output.read_text(encoding="utf-8"))
    assert candidate["manifest_revision"] == "corrected_pre_measurement_freeze_v3_7"
    assert candidate["manifest_fingerprint"] == "b4f33f33c6b7f23be0d28c13bdb0bdde946659a7a8f9909894e8b0d4807a11ec"
    assert len(candidate["rows"]) == 64


@_requires_production_data
def test_generator_validates_previous_source_hash_before_production_read(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    broken = copy.deepcopy(manifest)
    source_document = broken["telop_documents"]["lb4_e1ff"]
    tampered = tmp_path / "tampered-telop.json"
    tampered.write_bytes(Path(source_document["path"]).read_bytes() + b"\n")
    source_document["path"] = str(tampered)
    # Keep the original bytes/hash contract; only refresh the outer fingerprint.
    broken["manifest_fingerprint"] = manifest_fingerprint(broken)
    previous = tmp_path / "tampered-previous.json"
    previous.write_text(json.dumps(broken, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output = tmp_path / "must-not-be-written.json"

    completed = _run_generator(previous, output, tmp_path)

    assert completed.returncode != 0
    assert not output.exists()
    assert "telop" in (completed.stderr + completed.stdout).lower()


@pytest.mark.parametrize(
    "output_name",
    [
        "data/should-not-write.json",
        "docs/benchmarks/should-not-write.json",
        "benchmarks/t1/annotation_packet.py",
        "/tmp/t1-generator-outside-root.json",
    ],
)
def test_generator_output_allowlist_rejects_protected_or_noncanonical_paths(
    tmp_path: Path, output_name: str
) -> None:
    previous = _copy_previous(tmp_path)
    output = Path(output_name)
    if not output.is_absolute():
        output = ROOT / output
    before = output.read_bytes() if output.exists() and output.is_file() else None

    completed = _run_generator(previous, output, tmp_path)

    assert completed.returncode != 0
    if before is not None:
        assert output.read_bytes() == before


def test_generator_rejects_symlink_previous_and_output(tmp_path: Path) -> None:
    previous_real = _copy_previous(tmp_path)
    previous_link = tmp_path / "previous-link.json"
    previous_link.symlink_to(previous_real)
    output = tmp_path / "output.json"
    completed = _run_generator(previous_link, output, tmp_path)
    assert completed.returncode != 0
    assert not output.exists()

    second_root = tmp_path / "second"
    second_root.mkdir()
    previous = _copy_previous(second_root)
    output_real = tmp_path / "output-real.json"
    output_real.write_text("sentinel\n", encoding="utf-8")
    output_link = tmp_path / "output-link.json"
    output_link.symlink_to(output_real)
    completed = _run_generator(previous, output_link, tmp_path)
    assert completed.returncode != 0
    assert output_real.read_text(encoding="utf-8") == "sentinel\n"


def test_generator_accepts_macos_tmp_alias_as_canonical_temp_root() -> None:
    sys.path.insert(0, str(GENERATOR_PATH.parent))
    try:
        import generate_manifest_v35 as generator

        assert generator._resolve_test_root(Path("/tmp")) == Path("/tmp").resolve()
    finally:
        sys.path.pop(0)
