from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from eva.operations.core_b_preflight import CoreBPlan
from eva.operations.managed_b_render import (
    EXPECTED_SERVICES,
    MANAGED_B_MANIFEST,
    _contains_unresolved_interpolation,
    _write_all,
    main,
    render_managed_b,
)


def _plan(**overrides: str) -> CoreBPlan:
    values = {
        "EVA_B_COMPOSE_PROJECT": "eva2-b-live",
        "EVA_B_DOCKER_BINARY": "/usr/bin/docker",
        "EVA_B_DOCKER_CONFIG_DIR": "/private/docker-config",
        "EVA_B_DOCKER_CONTEXT": "eva2-b-local",
        "EVA_B_MANAGED_MANIFEST_SHA256": hashlib.sha256(
            MANAGED_B_MANIFEST.read_bytes()
        ).hexdigest(),
    }
    values.update(overrides)
    return CoreBPlan(
        configuration=tuple(sorted(values.items())),
        snapshot_sha256="b" * 64,
    )


def _rendered_graph(
    configuration_sha256: str,
    *,
    include_unresolved: bool = False,
) -> dict[str, object]:
    labels = {
        "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
        "io.eva2.configuration-sha256": configuration_sha256,
    }
    return {
        "services": {
            name: {"labels": dict(labels)} for name in sorted(EXPECTED_SERVICES)
        },
        "x-note": "${LEFT_OPEN}" if include_unresolved else "sealed",
    }


def _mock_compose(
    monkeypatch: pytest.MonkeyPatch,
    graph: dict[str, object],
) -> tuple[list[tuple[list[str], dict[str, str]]], list[tuple[str, int, str]]]:
    calls: list[tuple[list[str], dict[str, str]]] = []
    environment_files: list[tuple[str, int, str]] = []

    def run(
        command: list[str],
        **kwargs: object,
    ) -> SimpleNamespace:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        calls.append((command, environment))
        if command[-2:] == ["version", "--short"]:
            return SimpleNamespace(stdout="2.40.3-desktop.1\n")
        environment_path = Path(command[command.index("--env-file") + 1])
        environment_files.append(
            (
                environment_path.as_posix(),
                stat.S_IMODE(environment_path.stat().st_mode),
                environment_path.read_text(encoding="utf-8"),
            )
        )
        return SimpleNamespace(stdout=json.dumps(graph))

    monkeypatch.setattr("eva.operations.managed_b_render.subprocess.run", run)
    return calls, environment_files


def test_renderer_binds_template_snapshot_and_compose_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    graph = _rendered_graph(plan.snapshot_sha256)
    calls, environment_files = _mock_compose(monkeypatch, graph)

    rendered = render_managed_b(plan)

    assert rendered.project_name == "eva2-b-live"
    assert rendered.service_names == tuple(sorted(EXPECTED_SERVICES))
    assert rendered.manifest_sha256 == plan.value("EVA_B_MANAGED_MANIFEST_SHA256")
    assert rendered.rendered_sha256 == hashlib.sha256(
        json.dumps(
            graph,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert calls[1][0] == [
        "/usr/bin/docker",
        "--config",
        "/private/docker-config",
        "--context",
        "eva2-b-local",
        "compose",
        "--profile",
        "schema-install",
        "--project-name",
        "eva2-b-live",
        "--env-file",
        environment_files[0][0],
        "-f",
        str(MANAGED_B_MANIFEST),
        "config",
        "--format",
        "json",
    ]
    for _, environment in calls:
        assert environment == {
            "EVA_B_CONFIGURATION_SHA256": plan.snapshot_sha256,
            "LC_ALL": "C",
        }
    assert environment_files == [
        (
            environment_files[0][0],
            0o600,
            "".join(f"{name}={value}\n" for name, value in plan.configuration),
        )
    ]
    assert not Path(environment_files[0][0]).exists()


def test_renderer_refuses_template_hash_mismatch() -> None:
    with pytest.raises(RuntimeError, match="sealed template sha256"):
        render_managed_b(_plan(EVA_B_MANAGED_MANIFEST_SHA256="a" * 64))


def test_renderer_retries_partial_environment_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = bytearray()

    def partial_write(_: int, payload: memoryview) -> int:
        portion = bytes(payload[:2])
        written.extend(portion)
        return len(portion)

    monkeypatch.setattr("eva.operations.managed_b_render.os.write", partial_write)

    _write_all(123, b"sealed-plan")

    assert bytes(written) == b"sealed-plan"


def test_renderer_refuses_a_zero_byte_environment_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("eva.operations.managed_b_render.os.write", lambda *_: 0)

    with pytest.raises(OSError, match="unable to write"):
        _write_all(123, b"sealed-plan")


def test_renderer_refuses_unresolved_graph_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    _mock_compose(monkeypatch, _rendered_graph(plan.snapshot_sha256, include_unresolved=True))

    with pytest.raises(RuntimeError, match="unresolved interpolation"):
        render_managed_b(plan)


def test_renderer_allows_a_compose_escaped_container_variable() -> None:
    assert not _contains_unresolved_interpolation("$${POSTGRES_DB}")
    assert _contains_unresolved_interpolation("${EVA_MISSING}")


def test_renderer_refuses_service_with_wrong_configuration_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    graph = _rendered_graph(plan.snapshot_sha256)
    graph["services"]["core"]["labels"]["io.eva2.configuration-sha256"] = "a" * 64
    _mock_compose(monkeypatch, graph)

    with pytest.raises(RuntimeError, match="core has the wrong configuration label"):
        render_managed_b(plan)


def test_renderer_cli_prints_only_a_non_secret_receipt(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    rendered = SimpleNamespace(
        receipt=lambda: {
            "configuration_sha256": plan.snapshot_sha256,
            "contract": "EvaManagedBRenderReceipt/v1",
            "status": "passed",
        }
    )
    monkeypatch.setattr(
        "eva.operations.managed_b_render.load_core_b_plan",
        lambda environment_file: plan,
    )
    monkeypatch.setattr(
        "eva.operations.managed_b_render.render_managed_b",
        lambda rendered_plan: rendered,
    )

    assert main(["/private/eva2-b-runtime.env"]) == 0

    assert json.loads(capsys.readouterr().out) == rendered.receipt()


def test_renderer_cli_returns_failure_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "eva.operations.managed_b_render.load_core_b_plan",
        lambda environment_file: (_ for _ in ()).throw(RuntimeError("refused")),
    )

    assert main(["/private/eva2-b-runtime.env"]) == 2

    assert json.loads(capsys.readouterr().out) == {
        "error": "refused",
        "status": "failed",
    }


def test_renderer_cli_sanitizes_operating_system_failures(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "eva.operations.managed_b_render.load_core_b_plan",
        lambda environment_file: _plan(),
    )
    monkeypatch.setattr(
        "eva.operations.managed_b_render.render_managed_b",
        lambda rendered_plan: (_ for _ in ()).throw(OSError("private path")),
    )

    assert main(["/private/eva2-b-runtime.env"]) == 2

    assert json.loads(capsys.readouterr().out) == {
        "error": "managed B render refused",
        "status": "failed",
    }
