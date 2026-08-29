#!/usr/bin/env python3
"""Prove the read-only installer plan over real loopback HTTP and restarts."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from typing import Iterator

import uvicorn

from eva.adapters.api.installer_wizard import InstallerBootstrap, create_installer_wizard
from eva.operations.installer_budget_workflow import InstallerBudgetWorkflow
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_profile_workflow import InstallerProfileWorkflow
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_SECRET = "Eva2SyntheticPlanAccess_1234567890"
SOURCE_PATHS = (
    "src/eva/adapters/api/installer_installation_plan_api.py",
    "src/eva/adapters/api/installer_budget_api.py",
    "src/eva/adapters/api/installer_wizard.py",
    "src/eva/operations/installer_installation_plan.py",
    "src/eva/operations/installer_budget_workflow.py",
    "tests/api/test_installer_wizard.py",
    "tests/operations/test_installer_installation_plan.py",
    "tests/operations/test_installer_budget_workflow.py",
    "tools/prove_installer_installation_plan_wizard.py",
)


@contextmanager
def _wizard_server(app: object) -> Iterator[int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,  # type: ignore[arg-type]
            access_log=False,
            lifespan="off",
            log_level="critical",
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("installer wizard did not start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("installer wizard did not stop")


def _receipt() -> dict[str, object]:
    return {
        "checks": [
            {"detail": "Ondersteund", "id": "release_support", "state": "passed"}
        ],
        "contract": "EvaInstallerPreflight/v1",
        "host": {
            "architecture": "amd64",
            "docker_version": "29.1.3",
            "operating_system": "Ubuntu",
            "python_version": "3.12.3",
        },
        "next": "De lokale wizard mag verder.",
        "non_claims": ["geen installatie"],
        "platform_lock_claim_state": "selected_for_validation",
        "read_only": True,
        "status": "ready",
    }


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    cookie: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(raw))
    if cookie is not None:
        headers["Cookie"] = cookie
    if origin is not None:
        headers["Origin"] = origin
    connection = HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        return (
            response.status,
            {key.casefold(): value for key, value in response.getheaders()},
            response.read(),
        )
    finally:
        connection.close()


def _bootstrap(port: int, token: str) -> str:
    status, headers, raw = _request(
        port,
        "POST",
        "/internal/v1/bootstrap",
        body={"token": token},
        origin=f"http://127.0.0.1:{port}",
    )
    cookie = headers.get("set-cookie", "").split(";", 1)[0]
    if status != 204 or raw or not cookie.startswith("eva_installer_session="):
        raise RuntimeError("installer bootstrap exchange differs")
    return cookie


def _json(raw: bytes) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("installer response was not an object")
    return value


def _post(
    port: int,
    path: str,
    body: dict[str, object],
    cookie: str,
    responses: list[bytes],
) -> dict[str, object]:
    status, _, raw = _request(
        port,
        "POST",
        path,
        body=body,
        cookie=cookie,
        origin=f"http://127.0.0.1:{port}",
    )
    responses.append(raw)
    if status != 200:
        raise RuntimeError(f"installer wizard request failed: {path}")
    return _json(raw)


def _get_plan(port: int, cookie: str, responses: list[bytes]) -> dict[str, object]:
    status, headers, raw = _request(
        port,
        "GET",
        "/internal/v1/onboarding/install/plan",
        cookie=cookie,
    )
    responses.append(raw)
    if status != 200 or headers.get("cache-control") != "no-store":
        raise RuntimeError("installer plan response differs")
    plan = _json(raw)
    if plan.get("contract") != "EvaInstallerInstallationPlan/v2":
        raise RuntimeError("installer plan contract differs")
    return plan


def _private(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def _app(
    *,
    profile: Path,
    capability: Path,
    secrets: Path,
    budgets: Path,
    bootstrap_token: str,
):
    bootstrap = InstallerBootstrap(
        token=bootstrap_token, created_at=1.0, clock=lambda: 1.0
    )
    capability_workflow = InstallerCapabilityWorkflow(workspace=capability)
    return create_installer_wizard(
        _receipt(),
        bootstrap,
        profile_workflow=InstallerProfileWorkflow(workspace=profile),
        capability_workflow=capability_workflow,
        secret_store=InstallerSecretStore(workspace=secrets),
        budget_workflow=InstallerBudgetWorkflow(
            workspace=budgets,
            capability_workflow=capability_workflow,
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def prove(*, focused_tests_passed: int, broad_tests_passed: int) -> dict[str, object]:
    if focused_tests_passed < 1 or broad_tests_passed < focused_tests_passed:
        raise ValueError("test counts are invalid")
    responses: list[bytes] = []
    plan_hashes: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix="eva2-installer-plan-proof-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        profile, capability, secrets, budgets = (
            root / "profile",
            root / "capabilities",
            root / "secrets",
            root / "budgets",
        )
        for path in (profile, capability, secrets, budgets):
            _private(path)

        first_token = "i" * 43
        with _wizard_server(
            _app(
                profile=profile,
                capability=capability,
                secrets=secrets,
                budgets=budgets,
                bootstrap_token=first_token,
            )
        ) as port:
            cookie = _bootstrap(port, first_token)
            initial = _get_plan(port, cookie, responses)
            if initial.get("status") != "blocked":
                raise RuntimeError("empty wizard unexpectedly became installable")
            initial_codes = {
                item.get("code")
                for item in initial.get("blockers", [])
                if isinstance(item, dict)
            }
            if not {"profile_not_ready", "capability_pending"} <= initial_codes:
                raise RuntimeError("empty wizard blockers differ")
            staged = _post(
                port,
                "/internal/v1/onboarding/profile/stage",
                {
                    "installation_name": "Bewijsinstallatie",
                    "language": "nl-NL",
                    "owner_label": "Eigenaar",
                    "time_zone": "Europe/Amsterdam",
                },
                cookie,
                responses,
            )
            _post(
                port,
                "/internal/v1/onboarding/profile/confirm",
                {
                    "confirmation": "BEVESTIGEN",
                    "profile_sha256": staged["profile_sha256"],
                },
                cookie,
                responses,
            )
            _post(
                port,
                "/internal/v1/onboarding/capabilities/select",
                {"step_id": "model_provider", "wanted": True},
                cookie,
                responses,
            )
            for step_id in (
                "telegram",
                "home_assistant",
                "email",
                "adaptive_connector",
            ):
                _post(
                    port,
                    "/internal/v1/onboarding/capabilities/select",
                    {"step_id": step_id, "wanted": False},
                    cookie,
                    responses,
                )
            _post(
                port,
                "/internal/v1/onboarding/capabilities/select",
                {"step_id": "budgets", "wanted": True},
                cookie,
                responses,
            )
            staged_budget = _post(
                port,
                "/internal/v1/onboarding/budgets/stage",
                {
                    "background_reflections_per_installation_day": 40,
                    "background_reflections_per_person_day": 4,
                    "external_monthly_spend_cap_eur_cents": 0,
                    "max_input_tokens": 12_000,
                    "max_output_tokens": 1_200,
                    "memory_bytes_per_person": 20_971_520,
                    "memory_records_per_person": 2_000,
                    "profile_context_tokens": 800,
                    "recall_context_tokens": 3_000,
                    "retention_days": 365,
                    "retrieval_nodes": 50,
                },
                cookie,
                responses,
            )
            selected = _get_plan(port, cookie, responses)
            if selected.get("status") != "blocked":
                raise RuntimeError("unverified provider unexpectedly became installable")

        real_record_verification = InstallerCapabilityWorkflow.record_verification

        def interrupt_budget_binding(
            _workflow: InstallerCapabilityWorkflow, **_values: object
        ) -> object:
            raise RuntimeError("synthetic process boundary")

        InstallerCapabilityWorkflow.record_verification = interrupt_budget_binding  # type: ignore[method-assign]
        interrupted_token = "m" * 43
        try:
            with _wizard_server(
                _app(
                    profile=profile,
                    capability=capability,
                    secrets=secrets,
                    budgets=budgets,
                    bootstrap_token=interrupted_token,
                )
            ) as port:
                cookie = _bootstrap(port, interrupted_token)
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/budgets/confirm",
                    body={
                        "budget_sha256": staged_budget["budget_sha256"],
                        "confirmation": "LIMIETEN BEVESTIGEN",
                    },
                    cookie=cookie,
                    origin=f"http://127.0.0.1:{port}",
                )
                responses.append(raw)
                if status != 409:
                    raise RuntimeError("interrupted budget binding was not refused")
                status, _, raw = _request(
                    port,
                    "GET",
                    "/internal/v1/onboarding/budgets/status",
                    cookie=cookie,
                )
                responses.append(raw)
                budget_status = _json(raw)
                if (
                    status != 200
                    or budget_status.get("phase") != "ready"
                    or budget_status.get("capability_bound") is not False
                ):
                    raise RuntimeError("interrupted budget binding was not recoverable")
        finally:
            InstallerCapabilityWorkflow.record_verification = real_record_verification  # type: ignore[method-assign]

        workflow = InstallerCapabilityWorkflow(workspace=capability)
        workflow.record_verification(
            step_id="model_provider", outcome="ready", evidence_sha256="a" * 64
        )
        InstallerSecretStore(workspace=secrets).store(
            slot_id="model_provider", secret=SYNTHETIC_SECRET
        )

        for token in ("j" * 43, "k" * 43):
            with _wizard_server(
                _app(
                    profile=profile,
                    capability=capability,
                    secrets=secrets,
                    budgets=budgets,
                    bootstrap_token=token,
                )
            ) as port:
                cookie = _bootstrap(port, token)
                if token.startswith("j"):
                    recovered = _post(
                        port,
                        "/internal/v1/onboarding/budgets/confirm",
                        {
                            "budget_sha256": staged_budget["budget_sha256"],
                            "confirmation": "LIMIETEN BEVESTIGEN",
                        },
                        cookie,
                        responses,
                    )
                    if recovered.get("already_in_phase") is not True:
                        raise RuntimeError("budget binding recovery was not resumed")
                ready = _get_plan(port, cookie, responses)
                if ready.get("status") != "ready_for_release_preparation" or ready.get(
                    "blockers"
                ) != []:
                    raise RuntimeError("completed wizard did not become installable")
                plan_hash = ready.get("plan_sha256")
                if not isinstance(plan_hash, str):
                    raise RuntimeError("installer plan fingerprint is missing")
                plan_hashes.append(plan_hash)
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/install/apply",
                    body={"confirmation": "INSTALLEREN"},
                    cookie=cookie,
                    origin=f"http://127.0.0.1:{port}",
                )
                responses.append(raw)
                if status != 404:
                    raise RuntimeError("read-only plan exposed an apply route")

        if len(set(plan_hashes)) != 1:
            raise RuntimeError("installer plan changed across restart")

        workflow.record_invalidation(
            step_id="model_provider", evidence_sha256="c" * 64
        )
        final_token = "l" * 43
        with _wizard_server(
            _app(
                profile=profile,
                capability=capability,
                secrets=secrets,
                budgets=budgets,
                bootstrap_token=final_token,
            )
        ) as port:
            cookie = _bootstrap(port, final_token)
            blocked = _get_plan(port, cookie, responses)
            if blocked.get("status") != "blocked":
                raise RuntimeError("invalidated provider left plan installable")
            if blocked.get("plan_sha256") == plan_hashes[0]:
                raise RuntimeError("invalidated plan retained its prior fingerprint")

        if any(SYNTHETIC_SECRET.encode() in response for response in responses):
            raise RuntimeError("installer plan returned secret bytes")

    return {
        "boundaries": {
            "applyRouteExposed": False,
            "dockerOrServiceCalled": False,
            "providerCalled": False,
            "secretReturned": False,
        },
        "contract": "EvaInstallerInstallationPlanWizardReceipt/v2",
        "issuedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "loopbackRun": {
            "blockedBeforeCompletion": True,
            "blockedAfterProviderInvalidation": True,
            "planStableAcrossRestart": True,
            "budgetsConfirmedThroughWizard": True,
            "interruptedBudgetBindingRecovered": True,
            "temporaryWorkspaceRemovedAfterRun": True,
            "transport": "real_http_on_127.0.0.1",
            "wizardServerStarts": 5,
        },
        "nonclaims": [
            "This proof built and read an installation plan; it did not install, start, update or switch Eva.",
            "Provider readiness used synthetic retained evidence and did not contact a live provider.",
            "This is not a fresh-machine installation, managed-B promotion or consumer acceptance test.",
        ],
        "source": {
            "commit": _source_commit(),
            "files": [
                {"path": path, "sha256": _sha256(ROOT / path)}
                for path in SOURCE_PATHS
            ],
        },
        "status": "passed",
        "verification": {
            "broadInstallerTestsPassed": broad_tests_passed,
            "focusedTestsPassed": focused_tests_passed,
            "networkUsedOutsideLoopback": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--broad-tests-passed", type=int, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    receipt = prove(
        focused_tests_passed=arguments.focused_tests_passed,
        broad_tests_passed=arguments.broad_tests_passed,
    )
    raw = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    if arguments.output is not None:
        descriptor = os.open(
            arguments.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    print(raw.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
