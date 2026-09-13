from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from eva.contracts.platform import PlatformLock, PlatformLockV2, PlatformLockV3
from eva.operations.installer_preflight import (
    HostFacts,
    InstallerPreflightError,
    _ubuntu_pretty_name,
    inspect_host,
    load_platform_lock,
    preflight,
)


ROOT = Path(__file__).resolve().parents[2]


def _lock() -> PlatformLockV3:
    return PlatformLockV3.model_validate_json(
        (ROOT / "release/platform-lock.json").read_bytes()
    )


def _facts(**overrides: object) -> HostFacts:
    values: dict[str, object] = {
        "operating_system": "Ubuntu Server 24.04.4 LTS",
        "architecture": "amd64",
        "python_version": "3.12.14",
        "docker_version": "29.7.2",
        "docker_error": None,
        "os_id": "ubuntu",
        "version_id": "24.04",
        "os_version": "24.04.4 LTS (Noble Numbat)",
        "variant_id": "server",
    }
    values.update(overrides)
    return HostFacts(**values)  # type: ignore[arg-type]


def test_selected_platform_lock_stops_before_any_installation_claim() -> None:
    receipt = preflight(_lock(), _facts())

    assert receipt["contract"] == "EvaInstallerPreflight/v1"
    assert receipt["read_only"] is True
    assert receipt["status"] == "blocked"
    assert (
        receipt["next"]
        == "Er is niets geïnstalleerd of gewijzigd. Los eerst de geblokkeerde controle op."
    )
    support = next(
        item for item in receipt["checks"] if item["id"] == "release_support"
    )
    assert support == {
        "id": "release_support",
        "state": "blocked",
        "detail": "Deze release heeft dit platform nog niet als ondersteund bewezen",
    }


@pytest.mark.parametrize(
    ("facts", "check_id"),
    [
        (_facts(python_version="3.12.13"), "python"),
        (_facts(os_id="debian"), "host"),
        (_facts(docker_version="29.7.1"), "container_engine"),
        (
            _facts(
                docker_version=None, docker_error="Docker Engine is niet beschikbaar"
            ),
            "container_engine",
        ),
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

    receipt = preflight(PlatformLockV3.model_validate(value), _facts())

    assert receipt["status"] == "ready"
    assert receipt["next"] == "De lokale wizard mag starten."


def test_platform_lock_loader_refuses_a_link(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("{}")
    link = tmp_path / "platform-lock.json"
    link.symlink_to(source)

    with pytest.raises(InstallerPreflightError, match="regular private input"):
        load_platform_lock(link)


def test_platform_lock_loader_refuses_the_historical_v1_contract(
    tmp_path: Path,
) -> None:
    value = json.loads((ROOT / "release/platform-lock.json").read_text())
    value.update(
        {
            "contract": "PlatformLock/v1",
            "schema_version": 1,
            "pydantic_ai_slim_version": "2.32.1",
        }
    )
    PlatformLock.model_validate(value)
    path = tmp_path / "platform-lock-v1.json"
    path.write_text(json.dumps(value))

    with pytest.raises(InstallerPreflightError, match="platform lock is invalid"):
        load_platform_lock(path)


def test_platform_lock_loader_refuses_the_historical_v2_contract(
    tmp_path: Path,
) -> None:
    value = json.loads((ROOT / "release/platform-lock.json").read_text())
    value.update(
        {
            "contract": "PlatformLock/v2",
            "schema_version": 2,
            "pydantic_ai_slim_version": "2.36.0",
        }
    )
    PlatformLockV2.model_validate(value)
    path = tmp_path / "platform-lock-v2.json"
    path.write_text(json.dumps(value))

    with pytest.raises(InstallerPreflightError, match="platform lock is invalid"):
        load_platform_lock(path)


def test_host_metadata_uses_a_regular_system_copy_when_etc_is_a_link(
    tmp_path: Path,
) -> None:
    system = tmp_path / "system-os-release"
    system.write_text('PRETTY_NAME="Ubuntu Server 24.04.4 LTS"\n')

    assert (
        _ubuntu_pretty_name(Path("/etc/os-release"), system_os_release_path=system)
        == "Ubuntu Server 24.04.4 LTS"
    )


# Exact relevant os-release fields captured from the authenticated 20260209
# Ubuntu cloudimage guest. It contains no VARIANT_ID: do not invent one.
GUEST_METADATA = '''PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
'''


def test_actual_guest_metadata_reports_engine_but_missing_variant(tmp_path, monkeypatch):
    metadata = tmp_path / "os-release"
    metadata.write_text(GUEST_METADATA)
    monkeypatch.setattr("eva.operations.installer_preflight.host_platform.system", lambda: "Linux")
    monkeypatch.setattr("eva.operations.installer_preflight.host_platform.machine", lambda: "x86_64")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "29.7.2\n", "")
    facts = inspect_host(os_release_path=metadata, run=run)
    assert facts.os_id == "ubuntu" and facts.variant_id is None
    assert facts.os_version == "24.04.4 LTS (Noble Numbat)"
    receipt = preflight(_lock(), replace(facts, python_version="3.12.14"))
    checks = {item["id"]: item for item in receipt["checks"]}
    assert checks["container_engine"]["state"] == "passed"
    assert checks["host"]["state"] == "blocked"
    assert "servervariant is niet bewezen" in checks["host"]["detail"]
    assert checks["release_support"]["state"] == "blocked"
    assert receipt["status"] == "blocked"
    assert calls == [["docker", "version", "--format", "{{.Server.Version}}"]]


@pytest.mark.parametrize("changes", [
    {"os_id": "debian"}, {"version_id": "22.04"},
    {"os_version": "24.04.3 LTS (Noble Numbat)"},
    {"os_version": "24.04.4fake LTS"},
    {"variant_id": "desktop"}, {"variant_id": "unknown"},
    {"variant_id": None}, {"os_id": None},
])
def test_display_label_cannot_override_structured_host_evidence(changes):
    receipt = preflight(_lock(), _facts(**changes))
    checks = {item["id"]: item["state"] for item in receipt["checks"]}
    assert checks["host"] == "blocked" and checks["release_support"] == "blocked"
    assert receipt["status"] == "blocked"


def test_server_metadata_with_generic_label_keeps_release_gate_closed():
    receipt = preflight(_lock(), _facts(operating_system="Ubuntu 24.04.4 LTS"))
    checks = {item["id"]: item["state"] for item in receipt["checks"]}
    assert checks == {"python": "passed", "host": "passed", "container_engine": "passed", "release_support": "blocked"}


def test_duplicate_metadata_does_not_supply_host_authority(tmp_path):
    metadata = tmp_path / "os-release"
    metadata.write_text(GUEST_METADATA + "ID=debian\nVARIANT_ID=server\n")
    facts = inspect_host(os_release_path=metadata, run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "29.7.2", ""))
    assert facts.os_id is None
    assert preflight(_lock(), facts)["status"] == "blocked"
