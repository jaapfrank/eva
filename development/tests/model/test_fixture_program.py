from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.support.model_fixture import FixtureProgramError, SealedFixtureProgram


EVA2_ROOT = Path(__file__).resolve().parents[3]
MODEL_ARTIFACT_PATH = EVA2_ROOT / "development/design/vertical/noor-4d-model-vectors-v1.json"
MODEL_ARTIFACT_SHA256 = (
    "e0a9fca6aed7f5146dd0255ba9ae4cdee4fa15dc274398e535cab68c8e28f593"
)
MODEL_MANIFEST_SHA256 = (
    "6652e778e6b5668c63dc22457b50e490d7b6fe81829f33e8df4b9f4274f469d7"
)
MODEL_PROGRAM_SHA256 = (
    "2874df00da8d78e23184c085b745f10e5449d2f8c605d21a647bed097de6aa62"
)


def test_loads_the_exact_content_addressed_manifest_program_and_table() -> None:
    artifact_bytes = MODEL_ARTIFACT_PATH.read_bytes()

    program = SealedFixtureProgram.from_artifact_bytes(artifact_bytes)

    assert hashlib.sha256(artifact_bytes).hexdigest() == MODEL_ARTIFACT_SHA256
    assert program.artifact_sha256 == MODEL_ARTIFACT_SHA256
    assert program.manifest_sha256 == MODEL_MANIFEST_SHA256
    assert program.program_sha256 == MODEL_PROGRAM_SHA256
    assert hashlib.sha256(program.manifest_bytes).hexdigest() == MODEL_MANIFEST_SHA256
    assert hashlib.sha256(program.program_bytes).hexdigest() == MODEL_PROGRAM_SHA256
    assert program.row_count == 20
    assert program.blob_count == 16
    assert len(program.selectors) == len(set(program.selectors)) == 20
    assert [selector[0] for selector in program.selectors[:4]] == [
        "happy-path-eva",
        "happy-path-eva",
        "happy-path-eva",
        "identity-pack-swap-mira",
    ]


def test_rejects_any_change_to_the_sealed_artifact_before_interpretation() -> None:
    artifact_bytes = bytearray(MODEL_ARTIFACT_PATH.read_bytes())
    position = artifact_bytes.index(b"happy-path-eva")
    artifact_bytes[position] = ord("H")

    with pytest.raises(FixtureProgramError, match="artifact SHA-256"):
        SealedFixtureProgram.from_artifact_bytes(bytes(artifact_bytes))


def test_rejects_non_bytes_artifact_input() -> None:
    with pytest.raises(FixtureProgramError, match="must be bytes"):
        SealedFixtureProgram.from_artifact_bytes("not bytes")  # type: ignore[arg-type]
