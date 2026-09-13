"""Read-only installation preflight for an Eva release candidate.

This module deliberately does *not* install, download, start a container, or
read a secret.  It gives the local installer one honest answer before its
wizard is ever allowed to open: is this exact host an already validated target
of the release lock?
"""

from __future__ import annotations

import argparse
import json
import os
import platform as host_platform
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from eva.contracts.platform import PlatformLockV3, PlatformTarget


class InstallerPreflightError(RuntimeError):
    """The local installer cannot safely inspect its release input."""


_MAX_PLATFORM_LOCK_BYTES = 1_048_576
_ARCHITECTURES = {
    "aarch64": "arm64",
    "amd64": "amd64",
    "arm64": "arm64",
    "x86_64": "amd64",
}
_DOCKER_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True, slots=True)
class HostFacts:
    operating_system: str | None
    architecture: str | None
    python_version: str
    docker_version: str | None
    docker_error: str | None
    os_id: str | None = None
    version_id: str | None = None
    os_version: str | None = None
    variant_id: str | None = None


def _regular_read(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise InstallerPreflightError(f"{label} is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise InstallerPreflightError(f"{label} is not a regular private input")
    if metadata.st_size > maximum:
        raise InstallerPreflightError(f"{label} exceeds its byte limit")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise InstallerPreflightError(f"{label} cannot be read") from error
    if len(raw) != metadata.st_size:
        raise InstallerPreflightError(f"{label} changed while being read")
    return raw


def load_platform_lock(path: Path) -> PlatformLockV3:
    """Load one local, small, non-linked platform lock."""

    raw = _regular_read(path, label="platform lock", maximum=_MAX_PLATFORM_LOCK_BYTES)
    try:
        value = json.loads(raw)
        return PlatformLockV3.model_validate(value)
    except ValueError as error:
        raise InstallerPreflightError("platform lock is invalid") from error


def _os_release_metadata(
    path: Path = Path("/etc/os-release"),
    *,
    system_os_release_path: Path = Path("/usr/lib/os-release"),
) -> dict[str, str]:
    candidates = (path,)
    # Debian/Ubuntu conventionally makes /etc/os-release a symlink.  The
    # installer still refuses links for its release inputs, but host metadata
    # is not a release input and the canonical non-linked system copy is safe
    # to inspect as a fallback.
    if path == Path("/etc/os-release"):
        candidates += (system_os_release_path,)
    for candidate in candidates:
        try:
            raw = _regular_read(
                candidate, label="host operating-system metadata", maximum=65_536
            )
        except InstallerPreflightError:
            continue
        fields: dict[str, str] = {}
        for line in raw.decode("utf-8", errors="replace").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in {"PRETTY_NAME", "ID", "VERSION_ID", "VERSION", "VARIANT_ID"}:
                continue
            if key in fields:
                return {}  # Ambiguous system metadata is not evidence.
            value = value.strip()
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    return {}
                value = value[1:-1]
            fields[key] = value
        return fields
    return {}


def _ubuntu_pretty_name(
    path: Path = Path("/etc/os-release"),
    *,
    system_os_release_path: Path = Path("/usr/lib/os-release"),
) -> str | None:
    return _os_release_metadata(
        path, system_os_release_path=system_os_release_path
    ).get("PRETTY_NAME")


def _docker_version(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str | None, str | None]:
    try:
        result = run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "Docker Engine is niet beschikbaar"
    value = result.stdout.strip()
    if result.returncode != 0 or not _DOCKER_VERSION.fullmatch(value):
        return None, "Docker Engine-versie is niet leesbaar"
    return value, None


def inspect_host(
    *,
    os_release_path: Path = Path("/etc/os-release"),
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> HostFacts:
    """Collect only coarse local version data; no engine is started."""

    docker_version, docker_error = _docker_version(run)
    machine = host_platform.machine().lower()
    metadata = _os_release_metadata(os_release_path) if host_platform.system() == "Linux" else {}
    return HostFacts(
        operating_system=metadata.get("PRETTY_NAME"),
        architecture=_ARCHITECTURES.get(machine),
        python_version=".".join(map(str, sys.version_info[:3])),
        docker_version=docker_version,
        docker_error=docker_error,
        os_id=metadata.get("ID"),
        version_id=metadata.get("VERSION_ID"),
        os_version=metadata.get("VERSION"),
        variant_id=metadata.get("VARIANT_ID"),
    )


def _candidate_target(lock: PlatformLockV3, facts: HostFacts) -> PlatformTarget | None:
    # PRETTY_NAME is a display label, never evidence of the server variant.
    point = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+) LTS(?: \([^\n]*\))?", facts.os_version or "")
    if facts.os_id != "ubuntu" or point is None:
        return None
    version = point.group(1)
    if facts.version_id != version.rsplit(".", 1)[0]:
        return None
    for candidate in lock.tier1_targets:
        if (
            candidate.operating_system == f"Ubuntu Server {version} LTS"
            and candidate.architecture == facts.architecture
            and candidate.execution_mode == "containerized"
        ):
            return candidate
    return None


def _target(lock: PlatformLockV3, facts: HostFacts) -> PlatformTarget | None:
    return _candidate_target(lock, facts) if facts.variant_id == "server" else None


def preflight(lock: PlatformLockV3, facts: HostFacts) -> dict[str, object]:
    """Return a sanitized, fail-closed decision without making any change."""

    checks: list[dict[str, str]] = []

    def check(
        identifier: str, state: Literal["passed", "blocked"], detail: str
    ) -> None:
        checks.append({"id": identifier, "state": state, "detail": detail})

    check(
        "python",
        "passed" if facts.python_version == lock.python_version else "blocked",
        "Python-versie klopt"
        if facts.python_version == lock.python_version
        else f"Deze release vereist Python {lock.python_version}",
    )
    candidate = _candidate_target(lock, facts)
    target = _target(lock, facts)
    check(
        "host",
        "passed" if target is not None else "blocked",
        "Besturingssysteem en processor passen bij een gekozen Eva-target"
        if target is not None
        else (
            "Ubuntu-versie en processor passen; servervariant is niet bewezen in de hostmetadata"
            if candidate is not None and facts.variant_id is None
            else "Deze computer is geen gekozen Eva-target"
        ),
    )
    expected_engine = (
        candidate.container_engine.removeprefix("Docker Engine ") if candidate else None
    )
    engine_ok = (
        facts.docker_version is not None and facts.docker_version == expected_engine
    )
    check(
        "container_engine",
        "passed" if engine_ok else "blocked",
        "Docker Engine-versie klopt"
        if engine_ok
        else facts.docker_error
        or (
            f"Deze release vereist Docker Engine {expected_engine}"
            if expected_engine is not None
            else "Docker Engine wordt pas gecontroleerd nadat de host een gekozen Eva-target is"
        ),
    )
    target_validated = bool(
        target is not None
        and lock.claim_state == "validated"
        and target.validation_state == "validated"
        and target.support_status == "tier1"
    )
    check(
        "release_support",
        "passed" if target_validated else "blocked",
        "Deze exacte combinatie is eerder als Eva-releaseplatform bewezen"
        if target_validated
        else "Deze release heeft dit platform nog niet als ondersteund bewezen",
    )
    status: Literal["ready", "blocked"] = (
        "ready" if all(item["state"] == "passed" for item in checks) else "blocked"
    )
    return {
        "contract": "EvaInstallerPreflight/v1",
        "read_only": True,
        "status": status,
        "platform_lock_claim_state": lock.claim_state,
        "host": {
            "operating_system": facts.operating_system,
            "architecture": facts.architecture,
            "python_version": facts.python_version,
            "docker_version": facts.docker_version,
            "os_id": facts.os_id,
            "version_id": facts.version_id,
            "os_version": facts.os_version,
            "variant_id": facts.variant_id,
        },
        "checks": checks,
        "next": (
            "De lokale wizard mag starten."
            if status == "ready"
            else "Er is niets geïnstalleerd of gewijzigd. Los eerst de geblokkeerde controle op."
        ),
        "non_claims": [
            "Deze preflight opent geen secret, database, volume, connector of gebruikersdata.",
            "Deze preflight downloadt niets en start of wijzigt geen Docker-container.",
            "Een groene lokale versiecontrole is geen installatie-, connector- of gebruikersacceptatiebewijs.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eva-install-preflight")
    parser.add_argument(
        "--platform-lock",
        type=Path,
        default=Path("release/platform-lock.json"),
        help="alleen-lezen pad naar de gepinde platformlock",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = preflight(load_platform_lock(arguments.platform_lock), inspect_host())
    except InstallerPreflightError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 2
    print(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return 0 if receipt["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HostFacts",
    "InstallerPreflightError",
    "inspect_host",
    "load_platform_lock",
    "main",
    "preflight",
]
