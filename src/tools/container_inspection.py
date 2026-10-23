"""Read-only Docker inventory and exact shared-DSN consumer selection.

Never starts, stops or adopts containers. Mount-source equality cannot detect
other DSN files or non-Docker clients. Inspection is a point-in-time snapshot.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
import json
import os
import re
import subprocess
from typing import Any

DOCKER = "/usr/bin/docker"


class ContainerInspectionError(RuntimeError):
    """The requested read-only inventory could not be established."""


def _read_docker(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=180,
            env={"HOME": "/root", "LC_ALL": "C", "PATH": os.environ["PATH"]},
        )
    except subprocess.CalledProcessError as exc:
        diagnostic = "\n".join((exc.stdout or "", exc.stderr or ""))
        diagnostic = re.sub(r"postgres(?:ql)?://[^\s'\"]+", "postgresql://<redacted>", diagnostic)
        diagnostic = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<redacted>", diagnostic)
        diagnostic = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b", "<redacted>", diagnostic)
        diagnostic = " ".join(diagnostic.split())[-1200:]
        raise ContainerInspectionError(
            f"command failed closed: {command[-1]}: {diagnostic or 'no diagnostic'}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContainerInspectionError(f"command failed closed: {command[-1]}") from exc
    return completed.stdout


def inspect_all(
    *, run: Callable[[list[str]], str] | None = None,
    error_type: type[RuntimeError] = ContainerInspectionError,
) -> list[dict[str, Any]]:
    """Inspect all containers once; optional runner preserves legacy test seams."""
    execute = _read_docker if run is None else run
    names = execute([DOCKER, "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    if not names:
        return []
    try:
        documents = json.loads(execute([DOCKER, "inspect", *names]))
    except (ValueError, TypeError) as exc:
        raise error_type("Docker inspection was invalid") from exc
    if not isinstance(documents, list) or any(not isinstance(item, dict) for item in documents):
        raise error_type("Docker inspection was invalid")
    return documents


def shared_database_consumers(
    *, retained_core: str, inspected: Iterable[dict[str, Any]],
    error_type: type[RuntimeError] = ContainerInspectionError,
) -> list[str]:
    """Return running sibling containers mounting the exact core DSN source."""
    documents = list(inspected)
    core = next((item for item in documents
                 if str(item.get("Name", "")).lstrip("/") == retained_core), None)
    if core is None:
        raise error_type("retained slot core inspection is missing")
    sources = {str(mount.get("Source", "")) for mount in core.get("Mounts", [])
               if mount.get("Destination") == "/run/secrets/database_dsn" and mount.get("Source")}
    if len(sources) != 1:
        raise error_type("retained slot database mount is invalid")
    source = next(iter(sources))
    consumers = []
    for item in documents:
        name = str(item.get("Name", "")).lstrip("/")
        if not name or name == retained_core or item.get("State", {}).get("Running") is not True:
            continue
        if any(mount.get("Destination") == "/run/secrets/database_dsn"
               and mount.get("Source") == source for mount in item.get("Mounts", [])):
            consumers.append(name)
    return sorted(consumers)
