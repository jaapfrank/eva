#!/usr/bin/env python3
"""Read-only preflight for the currently running Living Core B candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib import request as urllib_request


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _command(*arguments: str) -> str:
    completed = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {arguments[0]}")
    return completed.stdout.strip()


def _http_json(url: str) -> tuple[dict[str, Any], str]:
    with urllib_request.urlopen(url, timeout=3) as response:  # noqa: S310
        raw = response.read(65_537)
    if len(raw) > 65_536:
        raise RuntimeError(f"health response is too large: {url}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"health response is not an object: {url}")
    return value, _sha256(raw)


def _load_evidence(root: Path, relative_path: str) -> tuple[dict[str, Any], str]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("evidence path escapes the repository")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("candidate evidence is invalid")
    bindings = value.get("source_files")
    if not isinstance(bindings, dict) or not bindings:
        raise RuntimeError("candidate evidence has no source bindings")
    for relative, expected in bindings.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise RuntimeError("candidate source binding is invalid")
        source = (root / relative).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise RuntimeError(f"candidate source is unavailable: {relative}")
        if _sha256(source.read_bytes()) != expected:
            raise RuntimeError(f"candidate source changed: {relative}")
    return value, _sha256(raw)


def _container(container: str) -> dict[str, Any]:
    raw = _command("docker", "inspect", container)
    decoded = json.loads(raw)
    if not isinstance(decoded, list) or len(decoded) != 1:
        raise RuntimeError(f"container inspect is invalid: {container}")
    value = decoded[0]
    if not isinstance(value, dict):
        raise RuntimeError(f"container inspect is invalid: {container}")
    return value


def _healthy(value: dict[str, Any], label: str) -> None:
    state = value.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise RuntimeError(f"{label} is not running")
    health = state.get("Health")
    if isinstance(health, dict) and health.get("Status") != "healthy":
        raise RuntimeError(f"{label} is not healthy")


def _mount(value: dict[str, Any], destination: str) -> dict[str, Any] | None:
    mounts = value.get("Mounts")
    if not isinstance(mounts, list):
        return None
    return next(
        (
            item
            for item in mounts
            if isinstance(item, dict) and item.get("Destination") == destination
        ),
        None,
    )


def check(arguments: argparse.Namespace) -> dict[str, object]:
    root = arguments.root.resolve()
    evidence, evidence_sha256 = _load_evidence(root, arguments.evidence)
    production_status = _command("systemctl", "is-active", arguments.production_service)
    if production_status != "active":
        raise RuntimeError("production A is not active")
    production_health, production_health_sha256 = _http_json(
        arguments.production_health_url
    )
    if production_health.get("gezond") is not True:
        raise RuntimeError("production A health is not green")

    core = _container(arguments.core_container)
    hindsight = _container(arguments.hindsight_container)
    lease_publisher = _container(arguments.lease_publisher_container)
    hindsight_lease_publisher = _container(
        arguments.hindsight_lease_publisher_container
    )
    postgres = _container(arguments.postgres_container)
    _healthy(core, "Living Core B")
    _healthy(hindsight, "Hindsight B")
    _healthy(lease_publisher, "model access lease publisher B")
    _healthy(hindsight_lease_publisher, "Hindsight access lease publisher B")
    _healthy(postgres, "PostgreSQL B")
    core_lease = _mount(core, "/run/eva/model-lease")
    hindsight_auth = _mount(hindsight, "/home/hindsight/.codex")
    publisher_auth = _mount(lease_publisher, "/run/eva/codex-owner")
    publisher_lease = _mount(lease_publisher, "/run/eva/model-lease")
    hindsight_publisher_auth = _mount(
        hindsight_lease_publisher, "/run/eva/codex-owner"
    )
    hindsight_publisher_lease = _mount(
        hindsight_lease_publisher, "/run/eva/hindsight-access"
    )
    if core_lease is None or core_lease.get("RW") is not False:
        raise RuntimeError("Living Core B access lease is not mounted read-only")
    if hindsight_auth is None or hindsight_auth.get("RW") is not False:
        raise RuntimeError("Hindsight B access credentials are not read-only")
    if publisher_auth is None or publisher_auth.get("RW") is not False:
        raise RuntimeError("model lease publisher auth source is not read-only")
    if publisher_lease is None or publisher_lease.get("RW") is not True:
        raise RuntimeError("model lease publisher output is not writable")
    if (
        hindsight_publisher_auth is None
        or hindsight_publisher_auth.get("RW") is not False
    ):
        raise RuntimeError("Hindsight publisher auth source is not read-only")
    if (
        hindsight_publisher_lease is None
        or hindsight_publisher_lease.get("RW") is not True
    ):
        raise RuntimeError("Hindsight publisher output is not writable")
    if publisher_auth.get("Source") != hindsight_publisher_auth.get("Source"):
        raise RuntimeError("access publishers differ from credential authority")
    if core_lease.get("Source") != publisher_lease.get("Source"):
        raise RuntimeError("Living Core B does not consume the published lease")
    if hindsight_auth.get("Source") != hindsight_publisher_lease.get("Source"):
        raise RuntimeError("Hindsight B does not consume its published lease")
    if core_lease.get("Source") == hindsight_auth.get("Source"):
        raise RuntimeError("core and Hindsight access leases are not isolated")
    if core_lease.get("Source") == publisher_auth.get("Source"):
        raise RuntimeError("Living Core B can read refresh-token authority")
    if hindsight_auth.get("Source") == publisher_auth.get("Source"):
        raise RuntimeError("Hindsight B can read refresh-token authority")
    core_health, core_health_sha256 = _http_json(arguments.core_health_url)
    if core_health != {"status": "alive"}:
        raise RuntimeError("Living Core B health contract differs")
    core_ready, core_ready_sha256 = _http_json(arguments.core_ready_url)
    if (
        core_ready.get("ready") is not True
        or core_ready.get("model_provider") != "ready"
    ):
        raise RuntimeError("Living Core B model credentials are not ready")
    hindsight_health, hindsight_health_sha256 = _http_json(
        arguments.hindsight_health_url
    )
    if hindsight_health.get("status") not in {"healthy", "ok"}:
        raise RuntimeError("Hindsight B health contract differs")
    revision = _command(
        "docker",
        "exec",
        arguments.postgres_container,
        "psql",
        "-U",
        arguments.postgres_user,
        "-d",
        arguments.postgres_database,
        "-Atc",
        "SELECT version_num FROM public.eva_alembic_version",
    )
    if revision != arguments.expected_revision:
        raise RuntimeError("PostgreSQL B revision differs")
    memory_outbox_open = int(
        _command(
            "docker",
            "exec",
            arguments.postgres_container,
            "psql",
            "-U",
            arguments.postgres_user,
            "-d",
            arguments.postgres_database,
            "-Atc",
            (
                "SELECT count(*) FROM eva_core.operational_checkpoints "
                "WHERE thread_id LIKE 'memory-retain:%' AND status <> 'completed'"
            ),
        )
    )
    memory_outbox_completed = int(
        _command(
            "docker",
            "exec",
            arguments.postgres_container,
            "psql",
            "-U",
            arguments.postgres_user,
            "-d",
            arguments.postgres_database,
            "-Atc",
            (
                "SELECT count(*) FROM eva_core.operational_checkpoints "
                "WHERE thread_id LIKE 'memory-retain:%' AND status = 'completed'"
            ),
        )
    )
    if memory_outbox_open != 0:
        raise RuntimeError("Living Core B has unfinished memory outbox work")
    image = core.get("Image")
    expected_image = evidence.get("environment", {}).get("image_sha256")
    if image != f"sha256:{expected_image}":
        raise RuntimeError("running Living Core B image differs from evidence")

    receipt: dict[str, object] = {
        "contract": "EvaLivingBCandidatePreflight/v1",
        "candidate": {
            "core_container": arguments.core_container,
            "core_health_sha256": core_health_sha256,
            "core_ready_sha256": core_ready_sha256,
            "evidence_path": arguments.evidence,
            "evidence_sha256": evidence_sha256,
            "hindsight_container": arguments.hindsight_container,
            "hindsight_health_sha256": hindsight_health_sha256,
            "image_sha256": expected_image,
            "model_authority": "single_writer_refresh_owner_access_lease_only",
            "hindsight_lease_publisher": (
                arguments.hindsight_lease_publisher_container
            ),
            "model_lease_publisher": arguments.lease_publisher_container,
            "memory_outbox_completed": memory_outbox_completed,
            "memory_outbox_open": memory_outbox_open,
            "postgres_container": arguments.postgres_container,
            "schema_revision": revision,
        },
        "checked_at": arguments.checked_at,
        "production_a": {
            "health_sha256": production_health_sha256,
            "service": arguments.production_service,
            "status": production_status,
        },
        "promotion": {
            "approved": False,
            "traffic_changed": False,
            "status": "isolated_b_candidate_only",
        },
        "status": "passed",
    }
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--checked-at", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--core-container", default="eva2-living-core-b")
    parser.add_argument("--postgres-container", default="eva2-postgres-b-live")
    parser.add_argument("--hindsight-container", default="eva2-hindsight-memory-live")
    parser.add_argument(
        "--lease-publisher-container", default="eva2-model-access-lease-b"
    )
    parser.add_argument(
        "--hindsight-lease-publisher-container",
        default="eva2-hindsight-access-lease-b",
    )
    parser.add_argument("--production-service", default="eva.service")
    parser.add_argument(
        "--production-health-url", default="http://127.0.0.1:3210/api/health"
    )
    parser.add_argument("--core-health-url", default="http://127.0.0.1:44674/healthz")
    parser.add_argument("--core-ready-url", default="http://127.0.0.1:44674/readyz")
    parser.add_argument(
        "--hindsight-health-url", default="http://127.0.0.1:44672/health"
    )
    parser.add_argument("--postgres-user", default="postgres")
    parser.add_argument("--postgres-database", default="eva2_b")
    parser.add_argument("--expected-revision", default="4e_0001_operational_timeline")
    return parser


def main() -> int:
    try:
        receipt = check(_parser().parse_args())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(_canonical({"status": "failed", "error": str(error)}).decode())
        return 2
    print(_canonical(receipt).decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
