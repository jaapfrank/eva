"""Render the sealed managed-B Compose graph without changing Docker state."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from eva.operations.core_b_preflight import (
    MANAGEMENT_LABEL,
    MANAGEMENT_VALUE,
    MANAGED_SERVICE_NAMES,
    SNAPSHOT_LABEL,
    CoreBPlan,
    load_core_b_plan,
)


ROOT = Path(__file__).resolve().parents[3]
MANAGED_B_MANIFEST = ROOT / "deploy" / "living-core" / "managed-b.compose.yml"
EXPECTED_SERVICES = MANAGED_SERVICE_NAMES
_ALLOWED_ESCAPED_CONTAINER_INTERPOLATIONS = frozenset({"$${POSTGRES_DB}"})


@dataclass(frozen=True, slots=True)
class RenderedBManifest:
    """Non-secret receipt for one exact Compose render."""

    configuration_sha256: str
    compose_version: str
    manifest_sha256: str
    project_name: str
    rendered_sha256: str
    service_names: tuple[str, ...]

    def receipt(self) -> dict[str, object]:
        return {
            "configuration_sha256": self.configuration_sha256,
            "compose_version": self.compose_version,
            "contract": "EvaManagedBRenderReceipt/v1",
            "manifest_sha256": self.manifest_sha256,
            "project_name": self.project_name,
            "rendered_sha256": self.rendered_sha256,
            "service_names": list(self.service_names),
            "status": "passed",
        }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _contains_unresolved_interpolation(value: object) -> bool:
    if isinstance(value, str):
        # Compose deliberately preserves $${POSTGRES_DB} in the PostgreSQL
        # container healthcheck.  It is the *only* escaped interpolation in
        # the sealed managed manifest.  Every other ${...} spelling, including
        # an empty, unfinished or nested form, must fail closed rather than
        # being treated as a harmless shell expression.
        start = 0
        while True:
            interpolation = value.find("${", start)
            if interpolation < 0:
                return False
            closing = value.find("}", interpolation + 2)
            if (
                interpolation == 0
                or value[interpolation - 1] != "$"
                or (interpolation >= 2 and value[interpolation - 2] == "$")
                or closing < 0
                or value[interpolation - 1 : closing + 1]
                not in _ALLOWED_ESCAPED_CONTAINER_INTERPOLATIONS
            ):
                return True
            start = closing + 1
    if isinstance(value, dict):
        return any(
            _contains_unresolved_interpolation(key)
            or _contains_unresolved_interpolation(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_unresolved_interpolation(nested) for nested in value)
    return False


def _validate_rendered_graph(rendered: object, plan: CoreBPlan) -> dict[str, Any]:
    if not isinstance(rendered, dict):
        raise RuntimeError("managed B Compose returned no JSON document")
    if _contains_unresolved_interpolation(rendered):
        raise RuntimeError("managed B Compose left unresolved interpolation")
    if "secrets" in rendered or "env_file" in rendered:
        raise RuntimeError("managed B Compose must not use Docker secrets or env_file")
    services = rendered.get("services")
    if not isinstance(services, dict) or set(services) != EXPECTED_SERVICES:
        raise RuntimeError("managed B Compose service graph differs from its sealed contract")
    for name, service in services.items():
        if not isinstance(service, dict):
            raise RuntimeError(f"managed B service {name} is invalid")
        if "secrets" in service or "env_file" in service:
            raise RuntimeError(f"managed B service {name} has an unsealed secret source")
        labels = service.get("labels")
        if not isinstance(labels, dict):
            raise RuntimeError(f"managed B service {name} has no verifiable labels")
        if labels.get(MANAGEMENT_LABEL) != MANAGEMENT_VALUE:
            raise RuntimeError(f"managed B service {name} has the wrong management label")
        if labels.get(SNAPSHOT_LABEL) != plan.snapshot_sha256:
            raise RuntimeError(f"managed B service {name} has the wrong configuration label")
    return rendered


def _run(command: list[str], environment: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            env=environment,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("managed B Compose render failed") from error
    return completed.stdout.strip()


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("unable to write sealed managed B render environment")
        remaining = remaining[written:]


def _render_environment_file(plan: CoreBPlan) -> tempfile.TemporaryDirectory[str]:
    """Materialize only the already preflighted snapshot in a private directory."""

    directory = tempfile.TemporaryDirectory(
        prefix="eva2-managed-b-render-",
        dir="/tmp",
    )
    os.chmod(directory.name, 0o700)
    environment_file = Path(directory.name) / "sealed.env"
    descriptor = os.open(
        environment_file,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        os.chmod(environment_file, 0o600)
        payload = "".join(
            f"{name}={value}\n" for name, value in plan.configuration
        ).encode("utf-8")
        _write_all(descriptor, payload)
    finally:
        os.close(descriptor)
    return directory


def render_managed_b(plan: CoreBPlan) -> RenderedBManifest:
    """Render from a preflighted snapshot; never create, start, or replace B."""

    manifest_bytes = MANAGED_B_MANIFEST.read_bytes()
    manifest_sha256 = _sha256(manifest_bytes)
    if manifest_sha256 != plan.value("EVA_B_MANAGED_MANIFEST_SHA256"):
        raise RuntimeError("managed B manifest differs from the sealed template sha256")

    environment = {
        "EVA_B_CONFIGURATION_SHA256": plan.snapshot_sha256,
        "LC_ALL": "C",
    }
    prefix = [
        plan.value("EVA_B_DOCKER_BINARY"),
        "--config",
        plan.value("EVA_B_DOCKER_CONFIG_DIR"),
        "--context",
        plan.value("EVA_B_DOCKER_CONTEXT"),
        "compose",
    ]
    compose_version = _run([*prefix, "version", "--short"], environment)
    with _render_environment_file(plan) as directory:
        rendered_raw = _run(
            [
                *prefix,
                "--profile",
                "schema-install",
                "--project-name",
                plan.value("EVA_B_COMPOSE_PROJECT"),
                "--env-file",
                str((Path(directory) / "sealed.env").resolve(strict=True)),
                "-f",
                str(MANAGED_B_MANIFEST),
                "config",
                "--format",
                "json",
            ],
            environment,
        )
    try:
        rendered = json.loads(rendered_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("managed B Compose returned invalid JSON") from error
    graph = _validate_rendered_graph(rendered, plan)
    return RenderedBManifest(
        configuration_sha256=plan.snapshot_sha256,
        compose_version=compose_version,
        manifest_sha256=manifest_sha256,
        project_name=plan.value("EVA_B_COMPOSE_PROJECT"),
        rendered_sha256=_sha256(_canonical_json(graph)),
        service_names=tuple(sorted(graph["services"])),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the non-mutating sealed render and print only its receipt."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Render the sealed managed B Compose graph without changing Docker state."
    )
    parser.add_argument("environment_file")
    arguments = parser.parse_args(argv)
    try:
        receipt = render_managed_b(load_core_b_plan(arguments.environment_file)).receipt()
    except RuntimeError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    except OSError:
        print(
            json.dumps(
                {"error": "managed B render refused", "status": "failed"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
