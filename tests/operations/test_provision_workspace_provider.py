"""Workspace provider deployment contract and preservation checks."""
import os
import re

import pytest
import yaml

from tools import provision_workspace_provider as provider


def test_workspace_storage_and_credentials_are_independent_of_core_slots():
    graph = yaml.safe_load(provider.MANIFEST.read_text())
    assert set(graph["services"]) == {"gateway", "filesystem"}
    assert len(graph["volumes"]) == 4
    for volume in graph["volumes"].values():
        assert volume["external"] is True and "slot" not in volume["name"]
    credential_volumes = []
    for service in graph["services"].values():
        assert "ports" not in service and "network_mode" not in service
        assert service["read_only"] is True and service["user"] == "10001:10001"
        assert service["cap_drop"] == ["ALL"] and service["pull_policy"] == "never"
        mounts = [v for v in service["volumes"] if v["target"] == "/run/secrets"]
        assert len(mounts) == 1 and mounts[0]["read_only"] is True
        credential_volumes.append(mounts[0]["source"])
    assert len(set(credential_volumes)) == 2
    assert graph["services"]["gateway"]["environment"]["DATABASE_URL"] == "sqlite:////data/contextforge.db"
    assert not any("TOKEN" in key and not key.endswith("_FILE") for s in graph["services"].values() for key in s.get("environment", {}))


def test_workspace_catalog_preserves_explicit_overwrite_and_independent_readback():
    selected = {}
    for name in ("write_file", "read_text_file"):
        selected[name] = {"name": "eva-workspace-" + name, "integration_type": "MCP", "enabled": True,
                          "reachable": True, "visibility": "public", "request_type": "SSE",
                          "input_schema": {"type": "object"}}
    catalog = provider.catalog_for(selected)
    tool = catalog["tools"][0]
    assert tool["execute_tool_name"] != tool["observe_tool_name"]
    assert tool["proof_bindings"] == [{"argument_name": "content", "observed_name": "content"}]
    assert re.fullmatch(r"[0-9a-f]{64}", tool["execute_catalog_sha256"])
    pattern = tool["input_schema"]["properties"]["path"]["pattern"]
    assert re.fullmatch(pattern, "/proof-state/gezinsadvies.md")
    for path in ("/run/secrets/token", "/proof-state/../secret", "/proof-state/.hidden", "relative.md"):
        assert re.fullmatch(pattern, path) is None
    assert "vervangen" in tool["description"]


@pytest.mark.parametrize("existing", [False, True])
def test_workspace_plan_does_not_write_or_adopt_existing_state(monkeypatch, tmp_path, existing):
    class ReadOnlyHelper:
        def _docker_json(self, *args):
            assert args[:2] == ("image", "inspect")
            return [{"Id": args[2].rsplit("@", 1)[1]}]
        def _require_docker_resource_absent(self, kind, name):
            assert kind in {"container", "network", "volume"}
    root = tmp_path / "provider"
    if existing:
        root.mkdir()
        (root / "document.txt").write_text("preserve")
    monkeypatch.setattr(provider, "HOST_ROOT", root)
    monkeypatch.setattr(provider, "_helper", lambda: ReadOnlyHelper())
    if existing:
        with pytest.raises(RuntimeError, match="already exists"):
            provider.plan()
        assert (root / "document.txt").read_text() == "preserve"
    else:
        plan = provider.plan()
        assert plan["coreAccessEnabled"] is False and plan["status"] == "plan_only"
        assert not root.exists()


def test_private_creation_refuses_to_replace_an_existing_token(tmp_path):
    path = tmp_path / "token"
    provider._write_private(path, b"original", uid=os.getuid())
    with pytest.raises(FileExistsError):
        provider._write_private(path, b"replacement", uid=os.getuid())
    assert path.read_bytes() == b"original"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("observed", ["expected", "same wrong reply", None])
def test_gateway_readback_must_match_direct_file_not_just_itself(observed):
    import hashlib
    content = "expected"
    direct = hashlib.sha256(content.encode()).hexdigest()
    response = {"structuredContent": {"content": observed}}
    if observed == content:
        provider.verify_readback(response, content, direct)
    else:
        with pytest.raises(RuntimeError, match="actual file"):
            provider.verify_readback(response, content, direct)
