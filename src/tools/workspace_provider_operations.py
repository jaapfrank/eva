"""Bounded workspace/browser provisioning operations owned by the tools package.

Extracted from the exact reviewed operational closure recorded below. Loading
this module has no operational effects and never imports acceptance machinery.
Consumers verify its complete source hash before loading it. Credentials remain
in stdin payloads; resource creation must keep the caller's no-adoption checks.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any
from urllib.parse import urlencode

SOURCE_PROVENANCE = {'source': 'development/validation/release-acceptance/v2/run_physical_acceptance.py', 'source_sha256': 'e733a44871860fb48cbc91dc89372272bb06c0a3c6872ec0c2efb44c3992b589', 'review': 'development/evidence/reassessment/e33-operator-support-review-20261017.json:dynamicClosureAddendum', 'ranges': [{'start': 73, 'end': 84, 'sha256': '7d74ce979db58913615ffd5c2ab61a597bdda0d0c959b5e9437aeda6a80f759d'}, {'start': 120, 'end': 172, 'sha256': '7c756ba2316a8139d1428c4434f62aa7166ff4b576576b33e38bd87057f51cf4'}, {'start': 476, 'end': 557, 'sha256': 'bd584cd71c5a38ce7f64bc7a81aefedafac664fddfe8d0e8f6bff9d4be470f00'}, {'start': 642, 'end': 669, 'sha256': '1aa42e305163662e9413ba43768d5e4afc57f224da7b9464696f26e1890d69aa'}, {'start': 692, 'end': 898, 'sha256': '27ca6c4d3cfb245e1b7c2f7e8529361286bccb4fb6cdd0f9bf7a3c6e5f9e1d4a'}], 'transformation': 'Exact selected code; exception class renamed and its docstring given operational ownership. No acceptance import, cases or criteria copied.'}


class WorkspaceProviderOperationError(RuntimeError):
    """A bounded workspace-provider operation could not be completed."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _run(
    command: list[str],
    *,
    check: bool = True,
    input_bytes: bytes | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        input=input_bytes,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        detail = stderr or stdout or f"exit {result.returncode}"
        raise WorkspaceProviderOperationError(
            f"command failed: {command[0]} {command[1] if len(command) > 1 else ''}: "
            f"{detail[:2_000]}"
        )
    return result


def _docker(*arguments: str, check: bool = True, timeout: int = 120) -> str:
    result = _run(["docker", *arguments], check=check, timeout=timeout)
    return result.stdout.decode("utf-8", errors="strict").strip()


def _docker_json(*arguments: str) -> Any:
    raw = _docker(*arguments)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkspaceProviderOperationError("Docker returned invalid JSON") from error


def _require_docker_resource_absent(kind: str, name: str) -> None:
    result = _run(["docker", kind, "inspect", name], check=False, timeout=15)
    if result.returncode == 0 or not _is_absent_inspection(result):
        raise WorkspaceProviderOperationError(
            f"refusing to reuse pre-existing Docker {kind} resource {name}"
        )


def _is_absent_inspection(result: subprocess.CompletedProcess[bytes]) -> bool:
    if result.returncode == 0:
        return False
    detail = (
        (result.stderr + result.stdout).decode("utf-8", errors="replace").casefold()
    )
    return "no such" in detail or "not found" in detail


def _resource_arguments(run_id: str) -> list[str]:
    return [
        "--label",
        f"io.eva2.tool01.run={run_id}",
        "--label",
        "io.eva2.tool01.disposable=true",
    ]


def _init_volume(
    *,
    helper_image: str,
    volume: str,
    container_name: str,
    run_id: str,
    files: dict[str, tuple[bytes, int, int, int]],
    directory_owner: tuple[int, int, int] | None = None,
) -> None:
    payload = {
        "directoryOwner": directory_owner,
        "files": {
            name: {
                "contentHex": content.hex(),
                "gid": gid,
                "mode": mode,
                "uid": uid,
            }
            for name, (content, uid, gid, mode) in files.items()
        },
    }
    program = r"""
import json, os, sys
payload=json.load(sys.stdin)
root='/state'
directory_owner=payload.get('directoryOwner')
for name, row in payload['files'].items():
    if '/' in name or name in {'', '.', '..'}:
        raise SystemExit('unsafe secret name')
    target=os.path.join(root, name)
    descriptor=os.open(target, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, bytes.fromhex(row['contentHex']))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(target,int(row['mode']))
    os.chown(target,int(row['uid']),int(row['gid']))
if directory_owner is not None:
    uid,gid,mode=(int(value) for value in directory_owner)
    os.chmod(root,mode)
    os.chown(root,uid,gid)
"""
    command = [
        "docker",
        "run",
        "--interactive",
        "--rm",
        "--name",
        container_name,
        "--pull",
        "never",
        "--network",
        "none",
        "--user",
        "0:0",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "CHOWN",
        "--security-opt",
        "no-new-privileges",
        *_resource_arguments(run_id),
        "--mount",
        f"type=volume,src={volume},dst=/state",
        "--entrypoint",
        "python3",
        helper_image,
        "-c",
        program,
    ]
    _run(command, input_bytes=_canonical(payload), timeout=120)


_FILESYSTEM_MCP_READINESS = r"""
import json, sys, urllib.request
payload={
  'jsonrpc':'2.0','id':'tool01-ready','method':'initialize',
  'params':{'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'tool01-readiness','version':'1'}}}
request=urllib.request.Request(
  'http://127.0.0.1:9000/mcp', data=json.dumps(payload).encode(), method='POST',
  headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json'})
with urllib.request.urlopen(request, timeout=5) as response:
  raw=response.read(1048577)
if len(raw)>1048576 or b'"result"' not in raw or b'"tool01-ready"' not in raw:
  raise SystemExit(2)
print('ready')
"""


def _wait_filesystem_mcp(container: str) -> None:
    for _ in range(90):
        result = _run(
            ["docker", "exec", container, "python3", "-c", _FILESYSTEM_MCP_READINESS],
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == b"ready":
            return
        time.sleep(1)
    raise WorkspaceProviderOperationError("filesystem MCP did not pass initialize readiness")


_CONTEXTFORGE_REQUEST = r"""
import json, sys, urllib.error, urllib.request
row=json.load(sys.stdin)
body=None if row['payload'] is None else json.dumps(row['payload']).encode()
headers={'Accept':'application/json','Content-Type':'application/json'}
if row['token'] is not None:
    headers['Authorization']='Bearer '+row['token']
request=urllib.request.Request(
    'http://127.0.0.1:4444'+row['path'], data=body, method=row['method'], headers=headers)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        raw=response.read(2097153)
        status=response.status
except urllib.error.HTTPError as error:
    raw=error.read(2097153)
    status=error.code
if len(raw)>2097152:
    raise SystemExit('response too large')
try:
    value=json.loads(raw) if raw else None
except json.JSONDecodeError:
    value={'raw':raw.decode('utf-8',errors='replace')[:1000]}
print(json.dumps({'status':status,'value':value},sort_keys=True,separators=(',',':')))
"""


def _gateway_request(
    container: str,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    token: str | None = None,
    expected: tuple[int, ...] = (200,),
) -> Any:
    request = _canonical(
        {"method": method, "path": path, "payload": payload, "token": token}
    )
    result = _run(
        ["docker", "exec", "-i", container, "python3", "-c", _CONTEXTFORGE_REQUEST],
        input_bytes=request,
        timeout=40,
    )
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorkspaceProviderOperationError(
            "ContextForge request helper returned invalid JSON"
        ) from error
    if not isinstance(envelope, dict) or envelope.get("status") not in expected:
        status = envelope.get("status") if isinstance(envelope, dict) else None
        raise WorkspaceProviderOperationError(
            f"ContextForge {method} {path} returned unexpected HTTP {status}"
        )
    return envelope.get("value")


def _wait_contextforge(container: str) -> None:
    for _ in range(120):
        try:
            value = _gateway_request(container, "GET", "/health")
            if isinstance(value, dict) and value.get("status") == "healthy":
                return
        except WorkspaceProviderOperationError:
            pass
        time.sleep(1)
    raise WorkspaceProviderOperationError("ContextForge did not become healthy")


def _login_contextforge(container: str, password: str) -> str:
    value = _gateway_request(
        container,
        "POST",
        "/auth/login",
        payload={"email": "admin@example.com", "password": password},
    )
    token = value.get("access_token") if isinstance(value, dict) else None
    if not isinstance(token, str) or not token:
        raise WorkspaceProviderOperationError("ContextForge login returned no access token")
    return token


def _list_tools(
    container: str, token: str, *, include_metrics: bool
) -> list[dict[str, Any]]:
    query = urlencode(
        {
            "include_inactive": "true",
            "include_metrics": "true" if include_metrics else "false",
            "include_pagination": "false",
            "limit": "0",
        }
    )
    value = _gateway_request(container, "GET", f"/tools?{query}", token=token)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise WorkspaceProviderOperationError("ContextForge tool list is malformed")
    return value


def _row_id(row: dict[str, Any], *, label: str) -> str:
    value = row.get("id")
    if not isinstance(value, str) or not value:
        raise WorkspaceProviderOperationError(f"{label} omitted its ID")
    return value


def _row_gateway_id(row: dict[str, Any]) -> str | None:
    value = row.get("gateway_id", row.get("gatewayId"))
    return value if isinstance(value, str) and value else None


def _original_name(row: dict[str, Any]) -> str | None:
    value = row.get("original_name", row.get("originalName"))
    return value if isinstance(value, str) else None


def _register_gateway(
    container: str,
    token: str,
    *,
    name: str,
    url: str,
) -> str:
    value = _gateway_request(
        container,
        "POST",
        "/gateways",
        payload={
            "name": name,
            "transport": "STREAMABLEHTTP",
            "url": url,
            "visibility": "public",
        },
        token=token,
        expected=(200, 201),
    )
    if not isinstance(value, dict):
        raise WorkspaceProviderOperationError("ContextForge gateway registration is malformed")
    return _row_id(value, label=f"gateway {name}")


def _wait_gateway_tool(
    container: str,
    token: str,
    *,
    gateway_id: str,
    original_name: str,
) -> dict[str, Any]:
    for _ in range(90):
        matches = [
            row
            for row in _list_tools(container, token, include_metrics=False)
            if _row_gateway_id(row) == gateway_id
            and _original_name(row) == original_name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise WorkspaceProviderOperationError(
                f"ContextForge discovered duplicate {original_name!r} tools"
            )
        time.sleep(1)
    raise WorkspaceProviderOperationError(
        f"ContextForge did not discover {original_name!r} for gateway {gateway_id}"
    )


def _make_tool_public(
    container: str,
    token: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    tool_id = _row_id(row, label="discovered tool")
    original_name = _original_name(row)
    runtime_name = row.get("name")
    gateway_id = _row_gateway_id(row)
    if (
        original_name is None
        or not isinstance(runtime_name, str)
        or not runtime_name
        or gateway_id is None
    ):
        raise WorkspaceProviderOperationError("ContextForge discovered tool authority is malformed")
    if row.get("visibility") != "public":
        _gateway_request(
            container,
            "PUT",
            f"/tools/{tool_id}",
            payload={"visibility": "public"},
            token=token,
        )
    for _ in range(30):
        matches = [
            item
            for item in _list_tools(container, token, include_metrics=False)
            if item.get("id") == tool_id
            and item.get("name") == runtime_name
            and _original_name(item) == original_name
            and _row_gateway_id(item) == gateway_id
            and item.get("visibility") == "public"
        ]
        if len(matches) == 1:
            return matches[0]
        time.sleep(1)
    raise WorkspaceProviderOperationError(
        f"ContextForge did not persist public visibility for tool {tool_id}"
    )

