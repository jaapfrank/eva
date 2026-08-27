from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.contracts.platform import PlatformLock
from eva.operations.installer_preflight import (
    HostFacts,
    InstallerPreflightError,
    _ubuntu_pretty_name,
    load_platform_lock,
    preflight,
)


ROOT = Path(__file__).resolve().parents[2]


def _lock() -> PlatformLock:
    return PlatformLock.model_validate_json(
        (ROOT / "release/platform-lock.json").read_bytes()
    )


def _facts(**overrides: object) -> HostFacts:
    values: dict[str, object] = {
        "operating_system": "Ubuntu Server 24.04.4 LTS",
        "architecture": "amd64",
        "python_version": "3.12.14",
        "docker_version": "29.7.2",
        "docker_error": None,
    }
    values.update(overrides)
    return HostFacts(**values)  # type: ignore[arg-type]


def test_selected_platform_lock_stops_before_any_installation_claim() -> None:
    receipt = preflight(_lock(), _facts())

    assert receipt["contract"] == "EvaInstallerPreflight/v1"
    assert receipt["read_only"] is True
    assert receipt["status"] == "blocked"
    assert receipt["next"] == "Er is niets geïnstalleerd of gewijzigd. Los eerst de geblokkeerde controle op."
    support = next(item for item in receipt["checks"] if item["id"] == "release_support")
    assert support == {
        "id": "release_support",
        "state": "blocked",
        "detail": "Deze release heeft dit platform nog niet als ondersteund bewezen",
    }


@pytest.mark.parametrize(
    ("facts", "check_id"),
    [
        (_facts(python_version="3.12.13"), "python"),
        (_facts(operating_system="Ubuntu 24.04.4 LTS"), "host"),
        (_facts(docker_version="29.7.1"), "container_engine"),
        (_facts(docker_version=None, docker_error="Docker Engine is niet beschikbaar"), "container_engine"),
    ],
)
def test_preflight_explains_the_exact_blocking_local_requirement(
    facts: HostFacts, check_id: str
) -> None:
    receipt = preflight(_lock(), facts)

    assert receipt["status"] == "blocked"
    check = next(item for item in receipt["checks"] if item["id"] == check_id)
    assert check["state"] == "blocked"
    assert "None" not in check["detail"]
    assert "geïnstalleerd" in receipt["next"]


def test_preflight_can_only_be_ready_for_a_validated_release_target() -> None:
    value = json.loads((ROOT / "release/platform-lock.json").read_text())
    value["claim_state"] = "validated"
    for target in value["tier1_targets"]:
        target["validation_state"] = "validated"
        target["support_status"] = "tier1"
        target["validation_receipt_sha256"] = "a" * 64
    for image in value["oci_images"]:
        image["validation_state"] = "validated"
        image["runnable"] = True
        image["image_repository"] = "registry.example/eva"
        image["image_digest"] = "sha256:" + "b" * 64
        image["sbom_sha256"] = "c" * 64

    receipt = preflight(PlatformLock.model_validate(value), _facts())

    assert receipt["status"] == "ready"
    assert receipt["next"] == "De lokale wizard mag starten."


def test_platform_lock_loader_refuses_a_link(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("{}")
    link = tmp_path / "platform-lock.json"
    link.symlink_to(source)

    with pytest.raises(InstallerPreflightError, match="regular private input"):
        load_platform_lock(link)


def test_host_metadata_uses_a_regular_system_copy_when_etc_is_a_link(
    tmp_path: Path,
) -> None:
    system = tmp_path / "system-os-release"
    system.write_text('PRETTY_NAME="Ubuntu Server 24.04.4 LTS"\n')

    assert (
        _ubuntu_pretty_name(
            Path("/etc/os-release"), system_os_release_path=system
        )
        == "Ubuntu Server 24.04.4 LTS"
    )
