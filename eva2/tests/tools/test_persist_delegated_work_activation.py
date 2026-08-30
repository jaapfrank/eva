from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.persist_delegated_work_activation import (
    DelegatedWorkActivationError,
    persist_delegated_work_activation,
)


CORE = "eva2/core@sha256:" + "1" * 64
WORKER = "eva2/worker@sha256:" + "2" * 64
COMMIT = "a" * 40
PRINCIPAL = "33333333-3333-3333-8333-333333333333"
BINDING = "44444444-4444-4444-8444-444444444444"


def _environment(path: Path) -> None:
    path.write_text(
        "EVA_LIVING_CORE_IMAGE=eva2/old@sha256:" + "3" * 64 + "\n"
        "EVA_DASHBOARD_RELEASE_SHA256=" + "3" * 64 + "\n"
        "EVA_B_CONFIGURATION_SHA256=" + "4" * 64 + "\n"
    )
    path.chmod(0o600)


def _runner(*, worker_commit: str = COMMIT):
    def run(arguments: tuple[str, ...]) -> bytes:
        image = arguments[-1]
        digest = image.rsplit("@", 1)[1]
        source = worker_commit if image == WORKER else COMMIT
        return json.dumps(
            [
                {
                    "Id": digest,
                    "RepoDigests": [image],
                    "Config": {
                        "Labels": {"io.eva2.source-commit": source},
                        "User": "65532:65532",
                    },
                }
            ]
        ).encode()

    return run


def test_activation_is_atomic_exact_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "candidate.env"
    _environment(path)

    first = persist_delegated_work_activation(
        path,
        core_image=CORE,
        worker_image=WORKER,
        owner_principal_id=PRINCIPAL,
        owner_binding_id=BINDING,
        runner=_runner(),
    )
    raw = path.read_text()
    assert f"EVA_LIVING_CORE_IMAGE={CORE}\n" in raw
    assert f"EVA_DASHBOARD_RELEASE_SHA256={'1' * 64}\n" in raw
    assert f"EVA_DELEGATED_WORK_IMAGE={WORKER}\n" in raw
    assert f"EVA_DELEGATED_WORK_OWNER_PRINCIPAL_ID={PRINCIPAL}\n" in raw
    assert f"EVA_DELEGATED_WORK_OWNER_BINDING_ID={BINDING}\n" in raw
    assert "EVA_B_CONFIGURATION_SHA256=" + "4" * 64 in raw
    assert first["ownerAuthorityFieldCount"] == 2
    assert set(first) == {
        "afterSha256",
        "beforeSha256",
        "contract",
        "imageSourceCommit",
        "ownerAuthorityFieldCount",
        "status",
    }

    second = persist_delegated_work_activation(
        path,
        core_image=CORE,
        worker_image=WORKER,
        owner_principal_id=PRINCIPAL,
        owner_binding_id=BINDING,
        runner=_runner(),
    )
    assert second["beforeSha256"] == second["afterSha256"]


@pytest.mark.parametrize(
    ("principal", "binding"),
    (("invalid", BINDING), (PRINCIPAL, "invalid")),
)
def test_activation_refuses_invalid_owner_authority(
    tmp_path: Path, principal: str, binding: str
) -> None:
    path = tmp_path / "candidate.env"
    _environment(path)
    before = path.read_bytes()
    with pytest.raises(DelegatedWorkActivationError, match="owner authority"):
        persist_delegated_work_activation(
            path,
            core_image=CORE,
            worker_image=WORKER,
            owner_principal_id=principal,
            owner_binding_id=binding,
            runner=_runner(),
        )
    assert path.read_bytes() == before


def test_activation_refuses_images_from_different_sources(tmp_path: Path) -> None:
    path = tmp_path / "candidate.env"
    _environment(path)
    before = path.read_bytes()
    with pytest.raises(DelegatedWorkActivationError, match="different source"):
        persist_delegated_work_activation(
            path,
            core_image=CORE,
            worker_image=WORKER,
            owner_principal_id=PRINCIPAL,
            owner_binding_id=BINDING,
            runner=_runner(worker_commit="b" * 40),
        )
    assert path.read_bytes() == before


def test_activation_refuses_duplicate_or_non_private_environment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.env"
    _environment(path)
    path.write_text(path.read_text() + "EVA_LIVING_CORE_IMAGE=duplicate\n")
    with pytest.raises(DelegatedWorkActivationError, match="repeats a key"):
        persist_delegated_work_activation(
            path,
            core_image=CORE,
            worker_image=WORKER,
            owner_principal_id=PRINCIPAL,
            owner_binding_id=BINDING,
            runner=_runner(),
        )
    path.chmod(0o644)
    with pytest.raises(DelegatedWorkActivationError, match="not private"):
        persist_delegated_work_activation(
            path,
            core_image=CORE,
            worker_image=WORKER,
            owner_principal_id=PRINCIPAL,
            owner_binding_id=BINDING,
            runner=_runner(),
        )
