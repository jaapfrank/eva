from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eva.operations.initialize_dashboard_authority import (
    _sha256,
    _token_subject_sha256,
    _uuid,
    main,
)


def test_dashboard_authority_inputs_are_canonical_and_domain_separated() -> None:
    assert str(
        _uuid(
            "11111111-1111-1111-8111-111111111111",
            name="dashboard tenant",
        )
    ) == "11111111-1111-1111-8111-111111111111"
    assert _sha256("a" * 64, name="release") == bytes.fromhex("a" * 64)
    token_hash = _token_subject_sha256("owner-access-token-1234567890abcdef")
    assert len(token_hash) == 32
    assert token_hash.hex() != hashlib.sha256(
        b"owner-access-token-1234567890abcdef"
    ).hexdigest()


@pytest.mark.parametrize(
    ("value", "error"),
    (
        ("NOT-A-UUID", "must be a UUID"),
        ("11111111-1111-1111-8111-11111111111A", "canonical UUID text"),
    ),
)
def test_dashboard_authority_rejects_noncanonical_uuid(value: str, error: str) -> None:
    with pytest.raises(RuntimeError, match=error):
        _uuid(value, name="dashboard tenant")


def test_dashboard_authority_rejects_weak_or_whitespace_token() -> None:
    with pytest.raises(RuntimeError, match="invalid shape"):
        _token_subject_sha256("short")
    with pytest.raises(RuntimeError, match="invalid shape"):
        _token_subject_sha256("owner token " + ("x" * 32))


def test_cli_requires_private_file_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = tmp_path / "dashboard-token"
    token.write_text("owner-access-token-1234567890abcdef", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://inline")
    monkeypatch.setenv("EVA_DASHBOARD_ACCESS_TOKEN_FILE", str(token))

    assert main([]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "error": "EVA_DATABASE_DSN is forbidden; use EVA_DATABASE_DSN_FILE",
        "status": "failed",
    }
