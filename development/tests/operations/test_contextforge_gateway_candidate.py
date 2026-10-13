from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "deploy" / "gateway"
EXPECTED_IMAGE = (
    "ghcr.io/ibm/mcp-context-forge@"
    "sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd"
)


def test_contextforge_candidate_is_internal_pinned_and_disabled_by_default() -> None:
    manifest = yaml.safe_load((EXPERIMENT / "compose.yml").read_text())
    service = manifest["services"]["contextforge"]

    assert service["image"] == EXPECTED_IMAGE
    assert service["pull_policy"] == "never"
    assert service["profiles"] == ["mcp-gateway-candidate"]
    assert "ports" not in service
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert manifest["networks"]["contextforge-internal"]["internal"] is True

    environment = service["environment"]
    assert environment["AUTH_REQUIRED"] == "true"
    assert environment["REQUIRE_STRONG_SECRETS"] == "true"
    assert environment["GUNICORN_WORKERS"] == "1"
    assert environment["MCPGATEWAY_STDIO_TRANSPORT_ENABLED"] == "false"
    assert environment["PLUGINS_CAN_OVERRIDE_RBAC"] == "false"
    assert environment["SSRF_ALLOW_LOCALHOST"] == "false"
    assert environment["SSRF_ALLOW_PRIVATE_NETWORKS"] == "false"
    assert environment["SSRF_DNS_FAIL_CLOSED"] == "true"
    assert not any("SECRET_KEY" in name or name.endswith("PASSWORD") for name in environment)


def test_contextforge_secrets_are_file_only_and_fail_closed() -> None:
    manifest = yaml.safe_load((EXPERIMENT / "compose.yml").read_text())
    service = manifest["services"]["contextforge"]
    mounts = {row["target"]: row for row in service["volumes"]}

    for target in (
        "/run/secrets/contextforge-jwt",
        "/run/secrets/contextforge-encryption",
        "/run/secrets/contextforge-admin-password",
    ):
        assert mounts[target]["type"] == "bind"
        assert mounts[target]["read_only"] is True
        assert ":?" in mounts[target]["source"]

    wrapper = (EXPERIMENT / "start-contextforge.sh").read_text()
    assert 'stat -c %u -- "$path"' in wrapper
    assert 'stat -c %a -- "$path"' in wrapper
    assert '"400"' in wrapper
    assert "[ ! -L \"$path\" ]" in wrapper
    assert "byte_count" in wrapper
    assert "tr -d '\\r\\n'" in wrapper
    assert "exec /app/docker-entrypoint.sh" in wrapper


def test_gateway_bakeoff_receipt_keeps_live_and_authority_nonclaims() -> None:
    receipt = json.loads(
        (ROOT / "development/evidence" / "product" / "mcp-gateway-bakeoff-2026-08-30.json").read_text()
    )

    assert receipt["selectedForNextTrial"] == "IBM/mcp-context-forge@v1.0.8"
    assert receipt["contextForge"]["scan"] == {"critical": 0, "high": 0}
    assert receipt["contextForge"]["unauthenticated"] == {
        "adminStatus": 401,
        "toolsStatus": 401,
    }
    assert receipt["contextForge"]["openMultiUserRbacIssue"] == 6015
    assert receipt["dockerMcpGateway"]["decision"] == "reserve"
    assert receipt["candidateBoundary"]["wrapperProbe"]["health"] == "healthy"
    assert receipt["candidateBoundary"]["wrapperProbe"]["secretValuesInContainerConfig"] == 0
    assert receipt["liveRouteChanged"] is False
    assert receipt["providerCalled"] is False
