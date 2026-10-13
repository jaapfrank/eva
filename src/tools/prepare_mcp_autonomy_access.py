"""Prepare one private MCP registration credential and gateway-only egress.

This opt-in preparation does not register upstream services, change a core, or
replace workspace credentials. Partial state is retained for operator recovery;
an existing discovery generation is never silently reused or overwritten.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess

from tools import provision_workspace_provider as provider
from tools.workspace_provider_access import ACCESS_UID, check_catalog, exclusive_access, read_private


EGRESS = "eva2-mcp-egress"
PERMISSIONS = ("gateways.create", "gateways.read", "tools.read")
TOKEN_NAME = "eva_connector_registration_generation_1"
OVERLAY = """# Opt-in gateway egress; the filesystem stays on its internal network.
services:
  gateway:
    networks:
      egress:
        gw_priority: 1
networks:
  egress:
    name: eva2-mcp-egress
    external: true
"""
_HTTPS_PROBE = r"""
import hashlib,json,urllib.parse,urllib.request,urllib.error
try:
    response=urllib.request.urlopen('https://gitmcp.io/',timeout=20)
except urllib.error.HTTPError as error:
    # An HTTP denial still proves DNS, outbound TCP and verified TLS. Record
    # it honestly; MCP availability is established later by Eva's own call.
    response=error
with response:
    final_url=response.geturl()
    if urllib.parse.urlsplit(final_url).scheme != 'https':
        raise RuntimeError('GitMCP probe redirected outside HTTPS')
    body=response.read(65536)
    print(json.dumps({'requestedUrl':'https://gitmcp.io/',
                     'finalUrl':final_url,'status':response.status,
                     'tlsCertificateVerified':True,
                     'websiteRequestAccepted':200 <= response.status < 400,
                     'mcpServiceAvailabilityProven':False,
                     'sampleBytes':len(body),
                     'sampleSha256':hashlib.sha256(body).hexdigest()}))
"""


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict) -> None:
    provider._write_private(path, json.dumps(payload, ensure_ascii=False, indent=2).encode())


def _topology(helper) -> dict:
    containers = {}
    for name in (provider.GATEWAY, provider.FILESYSTEM):
        value = helper._docker_json("inspect", name)[0]
        containers[name] = {
            "id": value["Id"], "image": value["Image"],
            "running": value["State"]["Running"],
            "portBindings": value["HostConfig"].get("PortBindings") or {},
            "networks": {key: {
                "address": network["IPAddress"], "gateway": network["Gateway"],
                "gatewayPriority": network.get("GwPriority", 0),
            } for key, network in value["NetworkSettings"]["Networks"].items()},
        }
    network = helper._docker_json("network", "inspect", provider.NETWORK)[0]
    return {
        "containers": containers,
        "connectorNetwork": {"id": network["Id"], "name": network["Name"],
                             "driver": network["Driver"], "internal": network["Internal"]},
    }


def _registrations(helper, token: str) -> list[dict]:
    rows = helper._gateway_request(provider.GATEWAY, "GET",
        "/gateways?include_pagination=false&limit=0", token=token)
    if not isinstance(rows, list):
        raise RuntimeError("gateway registration readback is malformed")
    return sorted([{
        "id": row["id"], "name": row["name"], "transport": row["transport"],
        "urlSha256": hashlib.sha256(row["url"].encode()).hexdigest(),
        "visibility": row["visibility"],
    } for row in rows], key=lambda row: row["id"])


def prepare(*, execution_generation: int) -> dict:
    if os.geteuid() != 0 or execution_generation < 1:
        raise RuntimeError("MCP autonomy preparation requires root and an existing generation")
    root = provider.HOST_ROOT
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("workspace root permissions differ")
    with exclusive_access(root):
        return _prepare(root, execution_generation)


def _prepare(root: Path, execution_generation: int) -> dict:
    access = root / "discovery-1"
    if access.exists() or access.is_symlink():
        raise RuntimeError("discovery-1 already exists; inspect retained state instead of retrying")
    helper = provider._helper()
    helper._require_docker_resource_absent("network", EGRESS)
    before = _topology(helper)
    if not before["connectorNetwork"]["internal"]:
        raise RuntimeError("workspace connector network is no longer internal")
    for name, image in ((provider.GATEWAY, provider.GATEWAY_IMAGE),
                        (provider.FILESYSTEM, provider.FILESYSTEM_IMAGE)):
        row = before["containers"][name]
        if (row["image"] != image.rsplit("@", 1)[1] or not row["running"]
                or row["portBindings"] or set(row["networks"]) != {provider.NETWORK}):
            raise RuntimeError("workspace provider topology differs before preparation")
    previous = root / f"access-{execution_generation}"
    token = read_private(previous / "token", ACCESS_UID, 0o600).decode()
    catalog_bytes = read_private(previous / "catalog.json", ACCESS_UID, 0o600)
    catalog = json.loads(catalog_bytes)
    previous_hash = hashlib.sha256(token.encode()).hexdigest()
    password = read_private(root / "admin-password", 0, 0o600).decode()
    admin = helper._login_contextforge(provider.GATEWAY, password)
    existing_registrations = _registrations(helper, admin)
    os.umask(0o077)
    access.mkdir(mode=0o700)
    os.chown(access, ACCESS_UID, ACCESS_UID)
    overlay = access / "compose.egress.yml"
    provider._write_private(overlay, OVERLAY.encode())
    compose = ["docker", "compose", "--project-name", "eva2-workspace-provider",
               "--env-file", str(root / "provider.env"),
               "--file", str(provider.MANIFEST), "--file", str(overlay)]
    restart = [*compose, "up", "--detach", "--no-deps", "gateway"]
    plan = {
        "contract": "EvaMcpAutonomyAccessPreparation/v1", "status": "preparing",
        "preparedAt": _timestamp(), "operatorSha256": provider._digest(Path(__file__)),
        "accessDirectory": str(access), "registrationTokenName": TOKEN_NAME,
        "registrationTokenPermissions": list(PERMISSIONS),
        "existingExecutionDirectory": str(previous), "existingExecutionTokenSha256": previous_hash,
        "existingCatalogSha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "beforeTopology": before, "existingRegistrations": existing_registrations,
        "restartCommand": restart, "composeOverlaySha256": provider._digest(overlay),
        "coreConfigurationChanged": False, "upstreamRegistered": False,
    }
    _write_json(access / "plan.json", plan)
    try:
        # Validate the persistent opt-in topology before changing any service.
        subprocess.run([*compose, "config", "--quiet"], capture_output=True, check=True, timeout=30)
        issued = helper._gateway_request(provider.GATEWAY, "POST", "/tokens", token=admin,
            payload={"name": TOKEN_NAME, "scope": {"permissions": list(PERMISSIONS)},
                     "expires_in_days": 30}, expected=(201,))
        registration_token = issued["access_token"]
        provider._write_private(access / "registration-token", registration_token.encode(), uid=ACCESS_UID)
        metadata = {
            "tokenId": issued["token"]["id"], "expiresAt": issued["token"]["expires_at"],
            "permissions": issued["token"]["resource_scopes"],
            "tokenSha256": provider._digest(access / "registration-token"),
        }
        _write_json(access / "token-metadata.json", metadata)
        if set(metadata["permissions"]) != set(PERMISSIONS):
            raise RuntimeError("registration token permissions differ")
        helper._gateway_request(provider.GATEWAY, "GET", "/tokens", token=registration_token, expected=(403,))
        helper._docker("network", "create", "--driver", "bridge", "--label",
                       "io.eva2.component=mcp-discovery-egress", EGRESS)
        helper._docker("network", "connect", "--gw-priority", "1", EGRESS, provider.GATEWAY)
        return _verify_and_finish(helper, root, access, plan, metadata)
    except Exception as error:
        _write_json(access / "failure.json", {
            "status": "incomplete_retained_for_inspection", "failedAt": _timestamp(),
            "errorType": type(error).__name__,
            "instruction": "Inspect plan, token metadata and topology; do not reissue or retry blindly.",
        })
        raise


def _verify_and_finish(helper, root: Path, access: Path, plan: dict, metadata: dict) -> dict:
    """Finish verification of the same credential/network; never provision again."""
    previous = Path(plan["existingExecutionDirectory"])
    if previous.parent != root or not previous.name.startswith("access-"):
        raise RuntimeError("execution access path differs from the recorded workspace")
    before = plan["beforeTopology"]
    previous_hash = plan["existingExecutionTokenSha256"]
    existing_registrations = plan["existingRegistrations"]
    token = read_private(previous / "token", ACCESS_UID, 0o600).decode()
    catalog_bytes = read_private(previous / "catalog.json", ACCESS_UID, 0o600)
    if hashlib.sha256(catalog_bytes).hexdigest() != plan["existingCatalogSha256"]:
        raise RuntimeError("existing execution catalog changed")
    catalog = json.loads(catalog_bytes)
    registration_token = read_private(access / "registration-token", ACCESS_UID, 0o600).decode()
    if (hashlib.sha256(registration_token.encode()).hexdigest() != metadata["tokenSha256"]
            or set(metadata["permissions"]) != set(PERMISSIONS)
            or provider._digest(access / "compose.egress.yml") != plan["composeOverlaySha256"]):
        raise RuntimeError("prepared credential or overlay differs from its immutable metadata")
    restart = plan["restartCommand"]
    after = _topology(helper)
    network = helper._docker_json("network", "inspect", EGRESS)[0]
    egress = {
        "id": network["Id"], "name": network["Name"], "driver": network["Driver"],
        "internal": network["Internal"],
        "containers": sorted(row["Name"] for row in network["Containers"].values()),
    }
    gateway = after["containers"][provider.GATEWAY]
    if (egress["internal"] or egress["driver"] != "bridge"
            or egress["containers"] != [provider.GATEWAY]
            or set(gateway["networks"]) != {provider.NETWORK, EGRESS}
            or gateway["networks"][EGRESS]["gatewayPriority"] != 1
            or not gateway["networks"][EGRESS]["gateway"]
            or gateway["portBindings"]
            or after["containers"][provider.FILESYSTEM] != before["containers"][provider.FILESYSTEM]
            or gateway["id"] != before["containers"][provider.GATEWAY]["id"]
            or gateway["networks"][provider.NETWORK] != before["containers"][provider.GATEWAY]["networks"][provider.NETWORK]):
        raise RuntimeError("prepared topology differs from gateway-only egress")
    probe = json.loads(helper._docker("exec", provider.GATEWAY, "python3", "-c", _HTTPS_PROBE, timeout=30))
    if not 200 <= probe["status"] <= 599 or probe["tlsCertificateVerified"] is not True:
        raise RuntimeError("gateway HTTPS connectivity was not established")
    if _registrations(helper, registration_token) != existing_registrations:
        raise RuntimeError("upstream registrations changed during preparation")
    if provider._digest(previous / "token") != previous_hash:
        raise RuntimeError("existing execution credential changed")
    check_catalog(helper, token, catalog)
    receipt = {
        **plan, "status": "prepared_core_disabled", "completedAt": _timestamp(),
        "completionOperatorSha256": provider._digest(Path(__file__)),
        "registrationCredential": metadata, "registrationTokenListingDenied": True,
        "afterTopology": after, "egressNetwork": egress, "httpsConnectivity": probe,
        "existingExecutionCatalogReadable": True, "existingRegistrationsUnchanged": True,
        "rollback": {
            "disconnectCommand": ["docker", "network", "disconnect", EGRESS, provider.GATEWAY],
            "revokeNewToken": {"method": "DELETE", "path": "/tokens/" + metadata["tokenId"],
                               "expectedStatus": 204, "credential": "existing private admin login"},
            "preserve": [provider.NETWORK, provider.FILESYSTEM, str(previous),
                         "all upstream gateway registrations", "all workspace volumes"],
        },
    }
    _write_json(access / "receipt.json", receipt)
    documentation = (
        "# MCP autonomy preparation\n\n"
        "Prepared only: Eva's core must explicitly mount registration-token read-only. "
        "No upstream service was registered and no core configuration was changed.\n\n"
        "The execution credential remains in " + str(previous) + ".\n\n"
        "Recreate/start the gateway with its opt-in egress overlay:\n\n```sh\n"
        + shlex.join(restart) + "\n```\n\n"
        "Rollback disconnects only the added egress network:\n\n```sh\n"
        + shlex.join(receipt["rollback"]["disconnectCommand"]) + "\n```\n\n"
        "Then use the existing private admin login with the pinned provider helper to send "
        "DELETE " + receipt["rollback"]["revokeNewToken"]["path"] + " (expect HTTP 204). "
        "Do not delete gateway registrations, workspace volumes, the connector network, "
        "or previous access generations. Stop selecting this overlay after rollback.\n"
    )
    provider._write_private(access / "README.md", documentation.encode())
    return receipt


def finish_existing() -> dict:
    """Explicit verification-only recovery after inspection of partial state."""
    if os.geteuid() != 0:
        raise RuntimeError("MCP autonomy finalization requires root")
    root = provider.HOST_ROOT
    access = root / "discovery-1"
    with exclusive_access(root):
        if (access / "receipt.json").exists():
            raise RuntimeError("preparation is already complete")
        plan = json.loads(read_private(access / "plan.json", 0, 0o600))
        metadata = json.loads(read_private(access / "token-metadata.json", 0, 0o600))
        if (plan["contract"] != "EvaMcpAutonomyAccessPreparation/v1"
                or plan["registrationTokenName"] != TOKEN_NAME
                or plan["accessDirectory"] != str(access)):
            raise RuntimeError("retained preparation identity differs")
        helper = provider._helper()
        network = helper._docker_json("network", "inspect", EGRESS)[0]
        if network.get("Labels", {}).get("io.eva2.component") != "mcp-discovery-egress":
            raise RuntimeError("retained network was not created by this preparation")
        return _verify_and_finish(helper, root, access, plan, metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-generation", type=int)
    parser.add_argument("--finish-existing", action="store_true",
                        help="After inspection, finish verification only; issue no token and change no network")
    arguments = parser.parse_args()
    try:
        if arguments.finish_existing:
            if arguments.execution_generation is not None:
                raise ValueError("select preparation or finalization, not both")
            result = finish_existing()
        else:
            if arguments.execution_generation is None:
                raise ValueError("execution generation is required for preparation")
            result = prepare(execution_generation=arguments.execution_generation)
    except Exception as error:
        print(json.dumps({"status": "refused_or_incomplete", "errorType": type(error).__name__,
                          "stateDirectory": str(provider.HOST_ROOT / "discovery-1")}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
