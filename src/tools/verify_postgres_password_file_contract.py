#!/usr/bin/env python3
"""Prove PostgreSQL's password-file behavior on disposable Docker resources.

This intentionally does not inspect, restart, or mount any Eva B resource.  It
uses a unique volume and private temporary files, then removes only those exact
resources in ``finally``.  The result deliberately contains no secret bytes,
container logs, or generated resource name.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


POSTGRES_IMAGE = (
    "docker.io/library/postgres@sha256:"
    "472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
)
DATABASE = "eva2_contract"
USER = "eva2_contract"
READY_TIMEOUT_SECONDS = 45


def _run(command: list[str], *, permitted_failure: bool = False) -> bool:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return True
    if permitted_failure:
        return False
    raise RuntimeError(f"Docker command failed with exit status {completed.returncode}")


def _write_private(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = value.encode("utf-8")
        while data:
            written = os.write(descriptor, data)
            data = data[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _start(
    docker: str,
    container: str,
    volume: str,
    bootstrap_password: Path,
    restart_password: Path,
    selected_password: Path,
) -> None:
    _run(
        [
            docker,
            "run",
            "--detach",
            "--name",
            container,
            "--label",
            "eva2.contract-test=postgres-password-file",
            "--mount",
            f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "--mount",
            f"type=bind,source={bootstrap_password},target=/run/eva2/bootstrap-password,readonly",
            "--mount",
            f"type=bind,source={restart_password},target=/run/eva2/restart-password,readonly",
            "--env",
            f"POSTGRES_DB={DATABASE}",
            "--env",
            f"POSTGRES_USER={USER}",
            "--env",
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
            "--env",
            f"POSTGRES_PASSWORD_FILE={selected_password}",
            POSTGRES_IMAGE,
        ]
    )


def _wait_until_ready(docker: str, container: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _run(
            [docker, "exec", container, "pg_isready", "-U", USER, "-d", DATABASE],
            permitted_failure=True,
        ):
            return
        time.sleep(0.5)
    raise RuntimeError("PostgreSQL did not become ready before the timeout")


def _password_probe(docker: str, container: str, password_path: str) -> bool:
    return _run(
        [
            docker,
            "exec",
            container,
            "sh",
            "-ec",
            'export PGPASSWORD="$(cat "$1")"; '
            'psql -h 127.0.0.1 -U "' + USER + '" -d "' + DATABASE + '" '
            "-Atqc 'SELECT 1' | grep -qx 1",
            "postgres-password-probe",
            password_path,
        ],
        permitted_failure=True,
    )


def verify(docker: str) -> None:
    suffix = uuid4().hex
    container = f"eva2-pg-password-contract-{suffix}"
    volume = f"eva2-pg-password-contract-{suffix}"
    volume_created = False
    try:
        with tempfile.TemporaryDirectory(prefix="eva2-pg-password-contract-", dir="/tmp") as raw:
            temporary = Path(raw)
            bootstrap_password = temporary / "bootstrap-password"
            restart_password = temporary / "restart-password"
            _write_private(bootstrap_password, secrets.token_urlsafe(32))
            _write_private(restart_password, secrets.token_urlsafe(32))

            _run([docker, "volume", "create", volume])
            volume_created = True
            _start(
                docker,
                container,
                volume,
                bootstrap_password,
                restart_password,
                Path("/run/eva2/bootstrap-password"),
            )
            _wait_until_ready(docker, container)
            if not _password_probe(docker, container, "/run/eva2/bootstrap-password"):
                raise RuntimeError("fresh PostgreSQL rejected its password-file value")

            _run([docker, "rm", "--force", container])
            _start(
                docker,
                container,
                volume,
                bootstrap_password,
                restart_password,
                Path("/run/eva2/restart-password"),
            )
            _wait_until_ready(docker, container)
            if not _password_probe(docker, container, "/run/eva2/bootstrap-password"):
                raise RuntimeError("initialized PostgreSQL no longer accepted its original password")
            if _password_probe(docker, container, "/run/eva2/restart-password"):
                raise RuntimeError("initialized PostgreSQL unexpectedly reset its password")
    finally:
        _run([docker, "rm", "--force", container], permitted_failure=True)
        if volume_created:
            _run([docker, "volume", "rm", volume], permitted_failure=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="/usr/bin/docker")
    arguments = parser.parse_args()
    verify(arguments.docker)
    print(
        json.dumps(
            {
                "contract": "EvaPostgresPasswordFileContract/v1",
                "fresh_volume": "passed",
                "initialized_volume": "passed",
                "image": POSTGRES_IMAGE,
                "result": "passed",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
