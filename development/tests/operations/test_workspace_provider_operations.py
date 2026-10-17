"""Owned operational closure: synthetic inputs, no Docker or provider requests."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import provision_workspace_provider as provider
from tools import workspace_provider_operations as operations


def test_loader_uses_packaged_operational_source_without_acceptance_or_effects(monkeypatch):
    calls = []
    original = importlib.util.spec_from_file_location
    def spec(name, path, *args, **kwargs):
        calls.append(Path(path))
        assert Path(path).parent == Path(provider.__file__).parent
        assert Path(path).name == "workspace_provider_operations.py"
        return original(name, path, *args, **kwargs)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", spec)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("loading may not execute operations"))
    retained = set(sys.modules)
    helper = provider._helper()
    assert calls == [provider.HELPER]
    assert helper._canonical({"fixture": True}) == b'{"fixture":true}'
    assert not any("acceptance" in name for name in set(sys.modules) - retained)
    assert provider._digest(provider.HELPER) == provider.HELPER_SHA256
    assert helper.SOURCE_PROVENANCE["source_sha256"] == "e733a44871860fb48cbc91dc89372272bb06c0a3c6872ec0c2efb44c3992b589"


def test_changed_operational_source_is_refused_before_import(monkeypatch, tmp_path):
    path = tmp_path / "changed.py"
    path.write_text("raise AssertionError('must never execute')")
    monkeypatch.setattr(provider, "HELPER", path)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **k: pytest.fail("pin rejection precedes module execution"))
    with pytest.raises(RuntimeError, match="operational helper binding differs"):
        provider._helper()


@pytest.mark.parametrize("code,stderr,allowed", [
    (0, b"", False), (1, b"Error: No such object: fixture", True),
    (1, b"network fixture not found", True), (1, b"daemon permission denied", False),
])
def test_resource_absence_requires_definite_inspection_and_never_adopts(monkeypatch, code, stderr, allowed):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, code, b"", stderr)
    monkeypatch.setattr(operations.subprocess, "run", run)
    if allowed:
        operations._require_docker_resource_absent("volume", "fixture")
    else:
        with pytest.raises(operations.WorkspaceProviderOperationError, match="refusing to reuse"):
            operations._require_docker_resource_absent("volume", "fixture")
    assert calls == [(["docker", "volume", "inspect", "fixture"], {
        "check": False, "capture_output": True, "input": None, "timeout": 15})]


@pytest.mark.parametrize("status", [200, 403])
def test_gateway_request_credentials_stay_in_stdin_and_never_retry(monkeypatch, status):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps({"status": status, "value": {"fixture": True}}).encode(), b"")
    monkeypatch.setattr(operations, "_run", run)
    if status == 200:
        assert operations._gateway_request("fixture-container", "POST", "/rpc", token="synthetic-secret", payload={"fixture": 1}) == {"fixture": True}
    else:
        with pytest.raises(operations.WorkspaceProviderOperationError, match="unexpected HTTP 403") as error:
            operations._gateway_request("fixture-container", "POST", "/rpc", token="synthetic-secret", payload={"fixture": 1})
        assert "synthetic-secret" not in str(error.value)
    assert len(calls) == 1
    command, values = calls[0]
    assert command[:6] == ["docker", "exec", "-i", "fixture-container", "python3", "-c"]
    assert "synthetic-secret" not in repr(command)
    assert json.loads(values["input_bytes"]) == {"method": "POST", "path": "/rpc", "payload": {"fixture": 1}, "token": "synthetic-secret"}
    assert values["timeout"] == 40


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"status":500,"value":"secret-response"}'])
def test_gateway_response_protocol_failures_do_not_expose_provider_body(monkeypatch, raw):
    monkeypatch.setattr(operations, "_run", lambda *a, **k: subprocess.CompletedProcess([], 0, raw, b""))
    with pytest.raises(operations.WorkspaceProviderOperationError) as error:
        operations._gateway_request("fixture", "GET", "/health")
    assert "secret-response" not in str(error.value)


def test_volume_initializer_is_isolated_and_preserves_existing_secret(monkeypatch, tmp_path):
    commands = []
    native_run = subprocess.run
    def run(command, **kwargs):
        commands.append((command, kwargs))
        assert command[:2] == ["docker", "run"]
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--pull") + 1] == "never"
        assert "--read-only" in command and "--rm" in command
        assert command[command.index("--cap-drop") + 1] == "ALL"
        assert command[command.index("--cap-add") + 1] == "CHOWN"
        assert command[command.index("--security-opt") + 1] == "no-new-privileges"
        assert "synthetic-private" not in repr(command)
        # Execute only the copied file-initialization program in this temporary
        # directory. Docker, network and real credentials are never involved.
        program = command[-1]
        assert program.count("root='/state'") == 1
        program = program.replace("root='/state'", "root=" + repr(str(tmp_path)))
        result = native_run([sys.executable, "-c", program], input=kwargs["input_bytes"], capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError("synthetic initializer refused an existing file")
        return result
    monkeypatch.setattr(operations, "_run", run)
    arguments = dict(helper_image="image@sha256:" + "a" * 64, volume="fixture", container_name="fixture-init", run_id="fixture",
        files={"token": (b"synthetic-private", os.geteuid(), os.getegid(), 0o400)},
        directory_owner=(os.geteuid(), os.getegid(), 0o700))
    operations._init_volume(**arguments)
    assert (tmp_path / "token").read_bytes() == b"synthetic-private"
    assert (tmp_path / "token").stat().st_mode & 0o777 == 0o400
    with pytest.raises(RuntimeError, match="refused an existing"):
        operations._init_volume(**arguments)
    assert (tmp_path / "token").read_bytes() == b"synthetic-private"
    assert len(commands) == 2


@pytest.mark.parametrize("method,limit", [("_wait_contextforge", 120), ("_wait_filesystem_mcp", 90)])
def test_existing_readiness_windows_remain_finite(monkeypatch, method, limit):
    calls = []
    monkeypatch.setattr(operations.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(operations, "_gateway_request", lambda *a, **k: calls.append(1) or {"status": "unhealthy"})
    monkeypatch.setattr(operations, "_run", lambda *a, **k: calls.append(1) or subprocess.CompletedProcess([], 1, b"", b""))
    with pytest.raises(operations.WorkspaceProviderOperationError):
        getattr(operations, method)("fixture")
    assert len(calls) == limit


def test_gateway_tool_selection_rejects_duplicate_exact_route(monkeypatch):
    row = {"id": "id", "gateway_id": "gateway", "original_name": "read"}
    monkeypatch.setattr(operations, "_list_tools", lambda *a, **k: [row, dict(row)])
    with pytest.raises(operations.WorkspaceProviderOperationError, match="duplicate"):
        operations._wait_gateway_tool("fixture", "synthetic", gateway_id="gateway", original_name="read")


def test_existing_browser_preparation_can_load_real_owned_helper_without_operations(monkeypatch, tmp_path):
    from tools import installer_browser_gateway as browser
    monkeypatch.setattr(browser, "_gateway_service", lambda *a: {})
    monkeypatch.setattr(browser.os, "chown", lambda *a: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("preparing files cannot provision"))
    result = browser.prepare_browser_gateway_files(package_root=tmp_path, workspace=tmp_path, namespace="synthetic",
        browser_plan=None, gateway_port=45622, images={"mcp-gateway": browser.GATEWAY_IMAGE.rsplit("@", 1)[1]},
        connector_subnet="10.254.90.0/24", connector_scope={"public_https": True, "local_networks": []},
        identifiers={key:"fixture-" + key for key in ("tenant", "installation", "principal", "binding")})
    assert result["started"] is False
    assert not Path(result["token_file"]).exists()
    assert (Path(result["workspace"]) / "plan.json").exists()


def test_historical_helper_plan_is_not_silently_migrated(monkeypatch, tmp_path):
    helper = SimpleNamespace(_docker_json=lambda *args: [{"Id": args[2].rsplit("@", 1)[1]}], _require_docker_resource_absent=lambda *a: None)
    monkeypatch.setattr(provider, "_helper", lambda: helper)
    monkeypatch.setattr(provider, "HOST_ROOT", tmp_path / "uncreated")
    monkeypatch.setattr(provider.os, "geteuid", lambda: 0)
    old = provider.plan() | {"helperSha256": "a8dc2a31ea327a54f70850669c0c2c3f3b40893a7bb80863df6bdeb09527d3be"}
    with pytest.raises(RuntimeError, match="plan changed"):
        provider.provision(old)
    assert not provider.HOST_ROOT.exists()
