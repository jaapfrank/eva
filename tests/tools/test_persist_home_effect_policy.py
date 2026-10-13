from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

import pytest

from tools.persist_home_effect_policy import (
    HomeEffectPolicyError,
    persist_home_effect_policy,
)


IMAGE = "sha256:" + "a" * 64
IMAGE_REFERENCE = "eva2/test-core@" + IMAGE


@pytest.fixture
def private_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(dir="/var/tmp") as root:
        yield Path(root)


def _runner(
    *,
    allowlist: str = "light.hal:turn_off,light.hal:turn_on",
    principals: str = "b" * 64,
    health: str = "healthy",
):
    def run(_arguments: tuple[str, ...]) -> bytes:
        if _arguments[1:3] == ("image", "inspect"):
            return json.dumps([{"RepoDigests": [IMAGE_REFERENCE]}]).encode()
        return json.dumps(
            [
                {
                    "Config": {
                        "Env": [
                            "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST=" + allowlist,
                            "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST="
                            + principals,
                        ]
                    },
                    "Image": IMAGE,
                    "State": {"Health": {"Status": health}, "Status": "running"},
                }
            ]
        ).encode()

    return run


def _environment(path: Path) -> Path:
    path.write_text(
        "KEEP=value\n"
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST=light.old:turn_off\n"
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=\n"
    )
    path.chmod(0o600)
    return path


def test_persists_policy_atomically_without_returning_policy_values(
    private_tmp_path: Path,
) -> None:
    path = _environment(private_tmp_path / "candidate.env")
    receipt = persist_home_effect_policy(path, "eva2-candidate", runner=_runner())

    assert path.read_text() == (
        "KEEP=value\n"
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST="
        "light.hal:turn_off,light.hal:turn_on\n"
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=" + "b" * 64 + "\n"
    )
    assert receipt["effectRuleCount"] == 2
    assert receipt["principalHashCount"] == 1
    assert receipt["sourceImage"] == IMAGE
    assert "light.hal" not in json.dumps(receipt)
    assert "b" * 64 not in json.dumps(receipt)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("allowlist", "principals"),
    [
        ("light.hal:toggle", "b" * 64),
        ("light.hal:turn_on,light.hal:turn_on", "b" * 64),
        ("light.hal:turn_on", "raw-principal"),
        ("", "b" * 64),
    ],
)
def test_rejects_invalid_source_policy(
    private_tmp_path: Path, allowlist: str, principals: str
) -> None:
    path = _environment(private_tmp_path / "candidate.env")
    before = path.read_bytes()
    with pytest.raises(HomeEffectPolicyError, match="policy is invalid"):
        persist_home_effect_policy(
            path,
            "eva2-candidate",
            runner=_runner(allowlist=allowlist, principals=principals),
        )
    assert path.read_bytes() == before


def test_rejects_unhealthy_source(private_tmp_path: Path) -> None:
    path = _environment(private_tmp_path / "candidate.env")
    with pytest.raises(HomeEffectPolicyError, match="not healthy and exact"):
        persist_home_effect_policy(
            path, "eva2-candidate", runner=_runner(health="unhealthy")
        )


def test_rejects_non_private_or_linked_destination(private_tmp_path: Path) -> None:
    path = _environment(private_tmp_path / "candidate.env")
    path.chmod(0o640)
    with pytest.raises(HomeEffectPolicyError, match="not private"):
        persist_home_effect_policy(path, "eva2-candidate", runner=_runner())

    path.chmod(0o600)
    linked = private_tmp_path / "linked.env"
    os.link(path, linked)
    with pytest.raises(HomeEffectPolicyError, match="not private"):
        persist_home_effect_policy(path, "eva2-candidate", runner=_runner())


def test_rejects_missing_or_repeated_destination_keys(private_tmp_path: Path) -> None:
    path = private_tmp_path / "candidate.env"
    path.write_text(
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=" + "c" * 64 + "\n"
    )
    path.chmod(0o600)
    with pytest.raises(HomeEffectPolicyError, match="policy keys differ"):
        persist_home_effect_policy(path, "eva2-candidate", runner=_runner())


def test_appends_new_principal_policy_key_to_legacy_environment(
    private_tmp_path: Path,
) -> None:
    path = private_tmp_path / "candidate.env"
    path.write_text(
        "KEEP=value\n"
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST=light.old:turn_off\n"
    )
    path.chmod(0o600)

    receipt = persist_home_effect_policy(path, "eva2-candidate", runner=_runner())

    assert receipt["status"] == "persisted"
    assert path.read_text().endswith(
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=" + "b" * 64 + "\n"
    )
