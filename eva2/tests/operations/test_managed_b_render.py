from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from tools.core_b_preflight import CoreBPlan
from tools.managed_b_render import (
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
            name: {"labels": dict(labels), **({"environment": {"EVA_BACKGROUND_WORK_ENABLED": "false", "EVA_DELEGATED_WORK_ENABLED": "false"}} if name == "core" else {})} for name in sorted(EXPECTED_SERVICES)
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

    monkeypatch.setattr("tools.managed_b_render.subprocess.run", run)
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
            "EVA_BACKGROUND_WORK_ENABLED": "false", "EVA_DELEGATED_WORK_ENABLED": "false",
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

    monkeypatch.setattr("tools.managed_b_render.os.write", partial_write)

    _write_all(123, b"sealed-plan")

    assert bytes(written) == b"sealed-plan"


def test_renderer_refuses_a_zero_byte_environment_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tools.managed_b_render.os.write", lambda *_: 0)

    with pytest.raises(OSError, match="unable to write"):
        _write_all(123, b"sealed-plan")


def test_renderer_refuses_unresolved_graph_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    _mock_compose(monkeypatch, _rendered_graph(plan.snapshot_sha256, include_unresolved=True))

    with pytest.raises(RuntimeError, match="unresolved interpolation"):
        render_managed_b(plan)


@pytest.mark.parametrize(
    "value",
    (
        "${EVA_MISSING}",
        "prefix-${EVA_MISSING}",
        "$$${EVA_MISSING}",
        "$$$${EVA_MISSING}",
        "$$$$${EVA_MISSING}",
        "$x${EVA_MISSING}",
        "$${}",
        "$${MISSING_CLOSE",
        "$${POSTGRES_USER}",
        "$${POSTGRES_DB:-fallback}",
        "$${OUTER$${INNER}}",
        "$${POSTGRES_DB}${EVA_MISSING}",
    ),
)
def test_renderer_refuses_every_non_exactly_escaped_interpolation(value: str) -> None:
    assert _contains_unresolved_interpolation(value)


@pytest.mark.parametrize(
    "value",
    (
        "$${POSTGRES_DB}",
        "test -n \"$${POSTGRES_DB}\" && echo ready",
        "$${POSTGRES_DB}:$${POSTGRES_DB}",
    ),
)
def test_renderer_allows_only_exactly_escaped_container_variables(value: str) -> None:
    assert not _contains_unresolved_interpolation(value)


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
        "tools.managed_b_render.load_core_b_plan",
        lambda environment_file: plan,
    )
    monkeypatch.setattr(
        "tools.managed_b_render.render_managed_b",
        lambda rendered_plan: rendered,
    )

    assert main(["/private/eva2-b-runtime.env"]) == 0

    assert json.loads(capsys.readouterr().out) == rendered.receipt()


def test_renderer_cli_returns_failure_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_render.load_core_b_plan",
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
        "tools.managed_b_render.load_core_b_plan",
        lambda environment_file: _plan(),
    )
    monkeypatch.setattr(
        "tools.managed_b_render.render_managed_b",
        lambda rendered_plan: (_ for _ in ()).throw(OSError("private path")),
    )

    assert main(["/private/eva2-b-runtime.env"]) == 2

    assert json.loads(capsys.readouterr().out) == {
        "error": "managed B render refused",
        "status": "failed",
    }


@pytest.mark.parametrize("tamper", [None, "extra_service", "command", "secret_env", "owner", "writable", "extra_mount", "network_name", "public_network", "network_alias", "volume_removed", "duplicate_mount", "unresolved"])
def test_connector_render_accepts_only_exact_approved_delta(tamper):
    import copy
    from tools.managed_b_render import mcp_gateway_environment, validate_mcp_gateway_render
    settings = {"EVA_MCP_GATEWAY_URL": "http://eva2-mcp-gateway:4444", "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true",
                "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST": "a" * 64,
                "EVA_MCP_GATEWAY_ACCESS_DIR_HOST": "/private/access-v1", "EVA_MCP_GATEWAY_NETWORK_NAME": "eva2-connectors"}
    baseline = {"name": "eva2-test", "services": {
        "core": {"environment": {"EVA_TELEGRAM_BOT_TOKEN_FILE": "/run/secrets/telegram"},
                 "volumes": [{"type": "volume", "source": "work", "target": "/work"}],
                 "networks": {"default": None}, "command": ["original"]},
        "postgres": {"image": "original"}}, "networks": {"default": {"name": "default"}}}
    activated = copy.deepcopy(baseline)
    core = activated["services"]["core"]
    core["environment"].update(mcp_gateway_environment(settings))
    core["volumes"].append({"type": "bind", "source": "/private/access-v1", "target": "/run/eva/connectors/mcp", "read_only": True, "bind": {"create_host_path": False}})
    core["networks"] = {"default": {}, "mcp-gateway": {}}
    activated["networks"]["mcp-gateway"] = {"name": "eva2-connectors", "external": True, "ipam": {}}
    if tamper == "extra_service":
        activated["services"]["extra"] = {}
    if tamper == "command":
        core["command"] = ["unreviewed"]
    if tamper == "secret_env":
        core["environment"]["EVA_MCP_GATEWAY_TOKEN"] = "never-publish-this-synthetic-secret"
    if tamper == "owner":
        core["environment"]["EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST"] = "b" * 64
    if tamper == "writable":
        core["volumes"][-1]["read_only"] = False
    if tamper == "extra_mount":
        core["volumes"].append({"source": "/", "target": "/host"})
    if tamper == "network_name":
        activated["networks"]["mcp-gateway"]["name"] = "other"
    if tamper == "public_network":
        activated["networks"]["mcp-gateway"]["external"] = False
    if tamper == "network_alias":
        core["networks"]["mcp-gateway"] = {"aliases": ["eva2-mcp-gateway"]}
    if tamper == "volume_removed":
        core["volumes"].pop(0)
    if tamper == "duplicate_mount":
        core["volumes"].append(copy.deepcopy(core["volumes"][-1]))
    if tamper == "unresolved":
        core["environment"]["EVA_MCP_GATEWAY_URL"] = "${MISSING}"
    original = copy.deepcopy(activated)
    if tamper:
        with pytest.raises(RuntimeError, match="connector render") as error:
            validate_mcp_gateway_render(baseline, activated, settings)
        assert "never-publish" not in str(error.value)
    else:
        receipt = validate_mcp_gateway_render(baseline, activated, settings)
        assert set(receipt) == {"baselineSha256", "activatedSha256"}
        assert all(len(value) == 64 for value in receipt.values())
    assert activated == original


@pytest.mark.parametrize("household", [False, True])
@pytest.mark.parametrize("mutation", [None, "writable", "file_bind", "extra_env", "other_service", "network", "auto_create", "duplicate"])
def test_voice_overlay_preserves_every_other_binding(mutation, household):
    import copy
    from tools.managed_b_render import validate_dashboard_voice_render, validate_household_voice_render
    validate = validate_household_voice_render if household else validate_dashboard_voice_render
    prefix = "EVA_HOUSEHOLD_VOICE_" if household else "EVA_DASHBOARD_VOICE_"
    target = "/run/eva/household-voice" if household else "/run/eva/voice"
    baseline = {"services": {"core": {"environment": {"EVA_MCP_GATEWAY_URL": "http://gateway", "EVA_TELEGRAM_BOT_TOKEN_FILE": "/secret"},
                                      "volumes": [{"type": "bind", "source": "/connector", "target": "/run/eva/connectors/mcp"}],
                                      "networks": {"mcp-gateway": {}}},
                             "postgres": {"image": "unchanged"}}, "networks": {"mcp-gateway": {"external": True}}}
    activated = copy.deepcopy(baseline)
    core = activated["services"]["core"]
    core["environment"][prefix + "POLICY_FILE"] = target + "/current.json"
    if household:
        core["environment"][prefix + "TOKEN_FILE"] = target + "/token"
    mount = {"type": "bind", "source": "/private/voice", "target": target, "read_only": True, "bind": {"create_host_path": False}}
    core["volumes"].append(mount)
    if mutation == "writable":
        mount["read_only"] = False
    if mutation == "file_bind":
        mount["source"] += "/current.json"
    if mutation == "extra_env":
        core["environment"]["EVA_DASHBOARD_VOICE_OTHER"] = "true"
    if mutation == "other_service":
        activated["services"]["postgres"]["image"] = "changed"
    if mutation == "network":
        activated["networks"]["mcp-gateway"]["external"] = False
    if mutation == "auto_create":
        mount["bind"]["create_host_path"] = True
    if mutation == "duplicate":
        core["volumes"].append(copy.deepcopy(mount))
    settings = {prefix + "POLICY_DIR_HOST": "/private/voice"}
    if mutation:
        with pytest.raises(RuntimeError, match="voice render"):
            validate(baseline, activated, settings)
    else:
        assert validate(baseline, activated, settings)["activatedSha256"]


@pytest.mark.parametrize("value", ["true", None, "0"])
def test_staging_render_refuses_background_work_even_if_graph_otherwise_matches(monkeypatch, value):
    plan = _plan(EVA_BACKGROUND_WORK_ENABLED="true")
    graph = _rendered_graph(plan.snapshot_sha256)
    graph["services"]["core"]["environment"]["EVA_BACKGROUND_WORK_ENABLED"] = value
    _mock_compose(monkeypatch, graph)
    with pytest.raises(RuntimeError, match="staging must disable"):
        render_managed_b(plan)


def test_staging_render_overrides_enabled_configuration(monkeypatch):
    plan = _plan(EVA_BACKGROUND_WORK_ENABLED="true")
    calls, files = _mock_compose(monkeypatch, _rendered_graph(plan.snapshot_sha256))
    assert render_managed_b(plan).receipt()["background_work_enabled"] is False
    assert "EVA_BACKGROUND_WORK_ENABLED=true" in files[0][2]
    assert all(env["EVA_BACKGROUND_WORK_ENABLED"] == "false" for _, env in calls)


def test_connector_autonomy_render_keeps_one_mount_and_explicit_activation():
    import copy
    from tools.managed_b_render import (
        BROWSER_ENVIRONMENT_KEYS, MCP_AUTONOMY_ENVIRONMENT_KEYS,
        mcp_gateway_environment, validate_mcp_gateway_render,
    )
    from tools.eva2_quiescent_cutover import SystemBackend, QuiescentCutoverError
    settings = {"EVA_MCP_GATEWAY_URL": "http://eva2-mcp-gateway:4444",
                "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true",
                "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST": "a" * 64,
                "EVA_MCP_GATEWAY_ACCESS_DIR_HOST": "/private/access-v2",
                "EVA_MCP_GATEWAY_NETWORK_NAME": "eva2-connectors"}
    enabled = {"EVA_MCP_AUTONOMY_MANDATE_FILE": "/run/eva/connectors/mcp/mandate.json",
               "EVA_MCP_REGISTRATION_TOKEN_FILE": "/run/eva/connectors/mcp/registration-token",
               "EVA_MCP_REGISTRATION_VISIBILITY": "public",
               "EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "true"}
    baseline = {"services": {"core": {"environment": {"UNCHANGED": "yes"}, "volumes": [],
                                       "networks": {"default": {}}}}, "networks": {"default": {}}}
    installed = {**enabled,
                 "EVA_MCP_MANAGED_ENDPOINTS_FILE": "/run/eva/connectors/mcp/managed-endpoints.json",
                 "EVA_BROWSER_CONTROL_URL": "http://172.30.0.1:45163",
                 "EVA_BROWSER_CONTROL_TOKEN_FILE": "/run/eva/connectors/mcp/browser-control-token",
                 "EVA_BROWSER_OWNER_ORIGIN": "http://localhost:44710"}
    optional_keys = (*MCP_AUTONOMY_ENVIRONMENT_KEYS, *BROWSER_ENVIRONMENT_KEYS)
    for extra in ({}, enabled, installed,
                  {**enabled, "EVA_MCP_REGISTRATION_VISIBILITY": "private",
                   "EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "false"}):
        configuration = {**settings, **extra}
        expected = mcp_gateway_environment(configuration)
        assert {key: expected[key] for key in optional_keys} == {
            key: extra.get(key, "") for key in optional_keys}
        graph = copy.deepcopy(baseline)
        core = graph["services"]["core"]
        core["environment"].update(expected)
        core["volumes"].append({"type": "bind", "source": settings["EVA_MCP_GATEWAY_ACCESS_DIR_HOST"],
                                "target": "/run/eva/connectors/mcp", "read_only": True, "bind": {}})
        core["networks"]["mcp-gateway"] = {}
        graph["networks"]["mcp-gateway"] = {"name": "eva2-connectors", "external": True}
        assert validate_mcp_gateway_render(baseline, graph, configuration)["activatedSha256"]
        inspected = {"Config": {"Env": [key + "=" + value for key, value in expected.items()]},
                     "Mounts": [{"Type": "bind", "Source": settings["EVA_MCP_GATEWAY_ACCESS_DIR_HOST"],
                                 "Destination": "/run/eva/connectors/mcp", "RW": False}],
                     "NetworkSettings": {"Networks": {"eva2-connectors": {}}}}
        SystemBackend._verify_connector(inspected, configuration)
        if not extra:
            # Existing retained cores without the optional empty variables remain valid.
            inspected["Config"]["Env"] = [key + "=" + value for key, value in expected.items()
                                          if key not in optional_keys]
            SystemBackend._verify_connector(inspected, configuration)
        for key, value in (("EVA_MCP_REGISTRATION_TOKEN_FILE", "/other/token"),
                           ("EVA_MCP_MANAGED_ENDPOINTS_FILE", "/other/endpoints"),
                           ("EVA_BROWSER_CONTROL_URL", "http://unexpected:45163"),
                           ("EVA_BROWSER_CONTROL_TOKEN_FILE", "/other/browser-token"),
                           ("EVA_BROWSER_OWNER_ORIGIN", "http://unexpected:44710"),
                           ("EVA_MCP_AUTONOMY_MANDATE_FILE", "/other/mandate"),
                           ("EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY", "false"),
                           ("EVA_MCP_REGISTRATION_TOKEN", "synthetic-secret")):
            # A changed false value is already expected in the private case.
            if expected.get(key) == value:
                continue
            changed = copy.deepcopy(graph)
            changed["services"]["core"]["environment"][key] = value
            with pytest.raises(RuntimeError, match="connector render"):
                validate_mcp_gateway_render(baseline, changed, configuration)
            changed_inspection = copy.deepcopy(inspected)
            changed_env = dict(row.split("=", 1) for row in inspected["Config"]["Env"])
            changed_env[key] = value
            changed_inspection["Config"]["Env"] = [k + "=" + v for k, v in changed_env.items()]
            with pytest.raises(QuiescentCutoverError):
                SystemBackend._verify_connector(changed_inspection, configuration)
    for changes in ({"EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "false"},
                    {"EVA_MCP_AUTONOMY_MANDATE_FILE": ""},
                    {"EVA_MCP_REGISTRATION_TOKEN_FILE": "/wrong/token"},
                    {"EVA_MCP_REGISTRATION_VISIBILITY": "team"},
                    {"EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "TRUE"}):
        with pytest.raises(RuntimeError, match="connector autonomy"):
            mcp_gateway_environment({**settings, **enabled, **changes})
    assert all(f"${{{key}:-}}" in (MANAGED_B_MANIFEST.parent / "mcp-gateway.compose.yml").read_text()
               for key in MCP_AUTONOMY_ENVIRONMENT_KEYS)


def test_provisioning_receives_exact_verified_graph_without_extra_commands(monkeypatch):
    from tools.managed_b_render import render_managed_b_configuration
    plan = _plan()
    graph = _rendered_graph(plan.snapshot_sha256)
    calls, _ = _mock_compose(monkeypatch, graph)
    receipt, actual = render_managed_b_configuration(plan)
    assert actual == graph
    assert receipt.rendered_sha256 == hashlib.sha256(
        json.dumps(actual, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert len(calls) == 2
    assert calls[-1][0][-3:] == ["config", "--format", "json"]
