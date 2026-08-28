from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.prepare_telegram_conversation_test import prepare_conversation_inputs


ROOT = Path(__file__).resolve().parents[2]
IMAGE = "example.invalid/eva@sha256:" + "a" * 64
HINDSIGHT_IMAGE = "example.invalid/hindsight@sha256:" + "b" * 64


def _private(path: Path, payload: object | bytes) -> Path:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def _valid_sources(tmp_path: Path) -> dict[str, Path]:
    return {
        "model_access_file": _private(
            tmp_path / "model.json",
            {
                "access_token": "access-only",
                "account_id": "account",
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "c" * 64,
            },
        ),
        "hindsight_access_file": _private(
            tmp_path / "hindsight.json",
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "access-only", "account_id": "account"},
            },
        ),
        "identity_file": _private(
            tmp_path / "identity.json",
            (ROOT / "src/eva/application/default_identity_pack.json").read_bytes(),
        ),
        "identity_trust_file": _private(
            tmp_path / "trust.json",
            (
                ROOT / "src/eva/application/default_identity_pack_trust.json"
            ).read_bytes(),
        ),
    }


def _prepare(tmp_path: Path, sources: dict[str, Path]) -> dict[str, object]:
    return prepare_conversation_inputs(
        **sources,
        core_image=IMAGE,
        hindsight_image=HINDSIGHT_IMAGE,
        destination=tmp_path / "test-root" / "conversation-secrets",
        overlay_env=tmp_path / "test-root" / "conversation.env",
        runtime_owner=os.geteuid(),
        hindsight_owner=os.geteuid(),
        overlay_owner=os.geteuid(),
    )


def test_preparer_seals_only_access_credentials_and_signed_identity(
    private_tmp_path: Path,
) -> None:
    tmp_path = private_tmp_path
    (tmp_path / "test-root").mkdir()
    receipt = _prepare(tmp_path, _valid_sources(tmp_path))

    destination = tmp_path / "test-root" / "conversation-secrets"
    assert receipt["contract"] == "EvaTelegramConversationTestPreparationReceipt/v1"
    assert receipt["refresh_token_present"] is False
    assert receipt["sealed_file_count"] == 5
    assert set(json.loads((destination / "model-access.json").read_bytes())) == {
        "access_token",
        "account_id",
        "contract",
        "source_sha256",
    }
    hindsight = json.loads((destination / "hindsight-access/auth.json").read_bytes())
    assert set(hindsight["tokens"]) == {"access_token", "account_id"}
    assert "refresh_token" not in json.dumps(hindsight)
    assert stat_mode(destination / "model-access.json") == 0o600
    environment = (tmp_path / "test-root" / "conversation.env").read_text()
    assert "@sha256:" in environment
    assert "access-only" not in environment


def test_preparer_rejects_refresh_authority_and_mutable_images(
    private_tmp_path: Path,
) -> None:
    tmp_path = private_tmp_path
    (tmp_path / "test-root").mkdir()
    sources = _valid_sources(tmp_path)
    _private(
        sources["model_access_file"],
        {
            "access_token": "access-only",
            "refresh_token": "must-not-copy",
            "account_id": "account",
            "contract": "EvaModelAccessLease/v1",
            "source_sha256": "c" * 64,
        },
    )
    with pytest.raises(RuntimeError, match="not access-only"):
        _prepare(tmp_path, sources)

    sources = _valid_sources(tmp_path)
    with pytest.raises(RuntimeError, match="exact sha256"):
        prepare_conversation_inputs(
            **sources,
            core_image="example.invalid/eva:latest",
            hindsight_image=HINDSIGHT_IMAGE,
            destination=tmp_path / "test-root" / "conversation-secrets",
            overlay_env=tmp_path / "test-root" / "conversation.env",
            runtime_owner=os.geteuid(),
            hindsight_owner=os.geteuid(),
            overlay_owner=os.geteuid(),
        )


def test_preparer_rejects_linked_invalid_or_existing_inputs(
    private_tmp_path: Path,
) -> None:
    tmp_path = private_tmp_path
    (tmp_path / "test-root").mkdir()
    sources = _valid_sources(tmp_path)
    linked = tmp_path / "linked-model.json"
    linked.hardlink_to(sources["model_access_file"])
    with pytest.raises(RuntimeError, match="private regular file"):
        _prepare(tmp_path, sources)
    linked.unlink()

    sources["identity_file"].write_bytes(b'{"contract":"IdentityPack/v1"}')
    with pytest.raises(RuntimeError, match="signed IdentityPack"):
        _prepare(tmp_path, sources)

    sources = _valid_sources(tmp_path)
    (tmp_path / "test-root" / "conversation-secrets").mkdir()
    with pytest.raises(RuntimeError, match="already exist"):
        _prepare(tmp_path, sources)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
