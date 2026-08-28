"""Atomic loopback traffic switch for Eva1's explicit ``/eva2`` canary."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
import urllib.request


SYSTEMCTL = "/usr/bin/systemctl"
SERVICE = "eva.service"
EVA1_BASE_URL = "http://127.0.0.1:3210"
EVA1_PATHS = (
    "/",
    "/api/overview",
    "/api/workstatus",
    "/api/capabilities",
    "/api/health",
)

Runner = Callable[[Sequence[str]], str]
ReadyProbe = Callable[[str], bool]
Eva1Probe = Callable[[], dict[str, int]]
Waiter = Callable[[Callable[[], bool]], bool]


class CanaryCutoverError(RuntimeError):
    """The candidate was not activated, or the previous route was restored."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validated_loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise CanaryCutoverError("canary URL has an invalid port") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1024 <= port <= 65535
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise CanaryCutoverError("canary URL must be one plain loopback HTTP port")
    canonical = f"http://127.0.0.1:{port}"
    if value.rstrip("/") != canonical:
        raise CanaryCutoverError("canary URL is not canonical")
    return canonical


def canonical_dropin(url: str) -> bytes:
    url = _validated_loopback_url(url)
    return (
        "[Service]\n"
        "# Keep Eva1 as the live user route; only explicit /eva2 turns use this\n"
        "# separately restartable and rollbackable Eva2-B candidate.\n"
        f"Environment=EVA2_CANARY_URL={url}\n"
    ).encode("utf-8")


def _read_exact_dropin(path: Path, url: str, *, owner_uid: int) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise CanaryCutoverError("canary systemd drop-in is inaccessible") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or metadata.st_uid != owner_uid
    ):
        raise CanaryCutoverError("canary systemd drop-in metadata is unsafe")
    raw = path.read_bytes()
    if raw != canonical_dropin(url):
        raise CanaryCutoverError("canary systemd drop-in differs from the expected route")
    return raw


def _atomic_dropin(path: Path, content: bytes, *, owner_uid: int) -> None:
    directory = path.parent
    metadata = os.lstat(directory)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CanaryCutoverError("canary systemd drop-in directory is unsafe")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.fchmod(handle.fileno(), 0o644)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise CanaryCutoverError("canary systemd drop-in update failed") from error
    _read_exact_dropin(
        path,
        content.decode("utf-8").rsplit("=", 1)[1].strip(),
        owner_uid=owner_uid,
    )


def _run(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CanaryCutoverError("systemd canary operation failed") from error
    return result.stdout


def _ready_probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/readyz", timeout=3) as response:
            payload: Any = json.load(response)
            return response.status == 200 and payload == {
                "database": "ready",
                "detail": None,
                "memory_projection": "ready",
                "model_provider": "ready",
                "ready": True,
            }
    except (OSError, ValueError):
        return False


def _eva1_probe() -> dict[str, int]:
    statuses: dict[str, int] = {}
    for path in EVA1_PATHS:
        try:
            with urllib.request.urlopen(f"{EVA1_BASE_URL}{path}", timeout=12) as response:
                statuses[path] = response.status
        except OSError:
            statuses[path] = 0
    return statuses


def _wait_for(
    check: Callable[[], bool], *, timeout_seconds: float = 45.0
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.5)
    return False


def _reload_restart_and_attest(url: str, *, runner: Runner) -> None:
    runner((SYSTEMCTL, "daemon-reload"))
    runner((SYSTEMCTL, "restart", SERVICE))
    environment = runner((SYSTEMCTL, "show", SERVICE, "--property=Environment", "--value"))
    if f"EVA2_CANARY_URL={url}" not in environment.split():
        raise CanaryCutoverError("Eva1 did not load the requested canary URL")


def switch_canary(
    *,
    dropin_path: Path,
    expected_current_url: str,
    target_url: str,
    owner_uid: int = 0,
    runner: Runner = _run,
    ready_probe: ReadyProbe = _ready_probe,
    eva1_probe: Eva1Probe = _eva1_probe,
    waiter: Waiter = _wait_for,
) -> dict[str, object]:
    """Switch only after target readiness and restore exact prior bytes on failure."""

    current = _validated_loopback_url(expected_current_url)
    target = _validated_loopback_url(target_url)
    if current == target:
        raise CanaryCutoverError("current and target canary URLs must differ")
    previous = _read_exact_dropin(dropin_path, current, owner_uid=owner_uid)
    replacement = canonical_dropin(target)
    if not ready_probe(target):
        raise CanaryCutoverError("target Eva2 candidate is not ready")

    switched = False
    try:
        _atomic_dropin(dropin_path, replacement, owner_uid=owner_uid)
        switched = True
        _reload_restart_and_attest(target, runner=runner)
        if not waiter(lambda: ready_probe(target)):
            raise CanaryCutoverError("target Eva2 candidate lost readiness")
        statuses: dict[str, int] = {}

        def eva1_is_ready() -> bool:
            nonlocal statuses
            statuses = eva1_probe()
            return statuses == {path: 200 for path in EVA1_PATHS}

        if not waiter(eva1_is_ready):
            raise CanaryCutoverError("Eva1 did not become healthy after canary switch")
    except Exception as error:
        if switched:
            try:
                _atomic_dropin(dropin_path, previous, owner_uid=owner_uid)
                _reload_restart_and_attest(current, runner=runner)
                if not ready_probe(current):
                    raise CanaryCutoverError("previous Eva2 candidate is not ready")
                if eva1_probe() != {path: 200 for path in EVA1_PATHS}:
                    raise CanaryCutoverError("Eva1 is unhealthy after canary rollback")
            except Exception as rollback_error:
                raise CanaryCutoverError(
                    "canary switch and automatic rollback both failed"
                ) from rollback_error
        if isinstance(error, CanaryCutoverError):
            raise
        raise CanaryCutoverError("canary switch failed and was rolled back") from error

    return {
        "contract": "Eva2CanaryCutoverReceipt/v1",
        "eva1HttpStatuses": statuses,
        "newDropinSha256": _sha256(replacement),
        "previousDropinSha256": _sha256(previous),
        "sourcePort": urlsplit(current).port,
        "status": "switched",
        "targetPort": urlsplit(target).port,
        "targetReady": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atomically switch Eva1's /eva2 canary")
    parser.add_argument("--dropin", type=Path, required=True)
    parser.add_argument("--expected-current-url", required=True)
    parser.add_argument("--target-url", required=True)
    arguments = parser.parse_args(argv)
    try:
        receipt = switch_canary(
            dropin_path=arguments.dropin,
            expected_current_url=arguments.expected_current_url,
            target_url=arguments.target_url,
        )
    except CanaryCutoverError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CanaryCutoverError", "canonical_dropin", "main", "switch_canary"]
