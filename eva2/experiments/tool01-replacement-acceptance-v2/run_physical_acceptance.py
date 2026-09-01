#!/usr/bin/env python3
"""Run the disposable physical TOOL01 v2 acceptance stack.

This is deliberately a host orchestrator.  It creates only nonce-bound Docker
resources, never publishes a port, never joins an existing network and never
mounts an existing Docker volume.  The evidence bundle is retained while all
containers, networks and volumes created by this process are removed.

The phase program is supplied explicitly with ``--runtime-script``.  It runs
inside three distinct containers from the exact candidate image and must use
the following small interface:

* positional phase: ``start``, ``resume`` or ``replay``;
* ``EVA_PROOF_RUN_ID``, ``EVA_PROOF_NONCE`` and the externally inspected full
  ``EVA_PROOF_CONTAINER_ID``;
* shared candidate-artifact directory: ``/run/proof-state``;
* private model auth at ``/run/eva/model-lease/auth.json`` and the two proof
  passwords at their fixed ``/run/secrets`` paths;
* ``start`` writes ``effect-returned.json`` after the real write call returns
  and then waits to be killed;
* after ``replay`` the state directory contains the exact five candidate
  artifacts required by the v2 checker.

The filesystem provider uses another private volume mounted at
``/proof-state``.  It is never shared with the candidate artifact directory.
The runtime harness is external proof source: its required SHA-256 and its own
clean Git commit are independent from the older candidate-image source commit.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlencode


HERE = Path(__file__).resolve().parent
CHECKER_PATH = HERE / "check_acceptance.py"
SOURCE_GENERATOR = (
    HERE.parent / "tool01-replacement-acceptance-v1" / "generate_source_authority.py"
)
_CHECKER_SPEC = importlib.util.spec_from_file_location(
    "tool01_v2_physical_checker", CHECKER_PATH
)
if _CHECKER_SPEC is None or _CHECKER_SPEC.loader is None:
    raise RuntimeError("TOOL01 v2 checker is unavailable")
checker = importlib.util.module_from_spec(_CHECKER_SPEC)
_CHECKER_SPEC.loader.exec_module(checker)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REFERENCE = re.compile(r"^(?:sha256:[0-9a-f]{64}|[^\s@]+@sha256:[0-9a-f]{64})$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UID = re.compile(r"^[1-9][0-9]{0,8}$")

DB_NAME = "eva2_living_disposable_tool01"
POSTGRES_VERSION_NUM = 160013
MODEL = "gpt-5.4-mini"
REASONING = "medium"


class PhysicalAcceptanceError(RuntimeError):
    """The physical acceptance could not be completed honestly."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_json(path: Path, value: object, *, mode: int = 0o600) -> None:
    raw = _canonical(value) + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PhysicalAcceptanceError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise PhysicalAcceptanceError(f"{label} is not a JSON object")
    return value


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
        raise PhysicalAcceptanceError(
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
        raise PhysicalAcceptanceError("Docker returned invalid JSON") from error


def _require_docker_resource_absent(kind: str, name: str) -> None:
    result = _run(["docker", kind, "inspect", name], check=False, timeout=15)
    if result.returncode == 0 or not _is_absent_inspection(result):
        raise PhysicalAcceptanceError(
            f"refusing to reuse pre-existing Docker {kind} resource {name}"
        )


def _is_absent_inspection(result: subprocess.CompletedProcess[bytes]) -> bool:
    if result.returncode == 0:
        return False
    detail = (
        (result.stderr + result.stdout).decode("utf-8", errors="replace").casefold()
    )
    return "no such" in detail or "not found" in detail


def _cleanup_disposable_resources(
    *,
    containers: tuple[str, ...],
    volumes: tuple[str, ...],
    networks: tuple[str, ...],
) -> None:
    failures: list[str] = []
    for kind, names in (
        ("container", containers),
        ("volume", volumes),
        ("network", networks),
    ):
        for name in names:
            remove = ["docker", kind, "rm"]
            if kind == "container":
                remove.append("--force")
            try:
                removed = _run([*remove, name], check=False, timeout=60)
                if removed.returncode != 0 and not _is_absent_inspection(removed):
                    failures.append(f"{kind}:{name}:remove")
            except (OSError, subprocess.SubprocessError):
                failures.append(f"{kind}:{name}:remove-exception")
            try:
                after = _run(["docker", kind, "inspect", name], check=False, timeout=15)
            except (OSError, subprocess.SubprocessError):
                failures.append(f"{kind}:{name}:inspect-exception")
                continue
            if not _is_absent_inspection(after):
                failures.append(f"{kind}:{name}:still-present")
    if failures:
        raise PhysicalAcceptanceError(
            "disposable cleanup was not complete: " + ",".join(sorted(set(failures)))
        )


def _require_file(path: Path, *, label: str, maximum_bytes: int = 5_000_000) -> Path:
    if path.is_symlink():
        raise PhysicalAcceptanceError(f"{label} must not be a symbolic link")
    resolved = path.resolve(strict=True)
    if (
        not resolved.is_file()
        or resolved.stat().st_size <= 0
        or resolved.stat().st_size > maximum_bytes
    ):
        raise PhysicalAcceptanceError(f"{label} is unsafe or has an invalid size")
    return resolved


def _require_private_file(
    path: Path, *, label: str, maximum_bytes: int = 1_000_000
) -> Path:
    resolved = _require_file(path, label=label, maximum_bytes=maximum_bytes)
    status = resolved.stat()
    permissions = stat.S_IMODE(status.st_mode)
    if (
        status.st_uid != os.geteuid()
        or status.st_nlink != 1
        or permissions & (stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO)
        or (permissions & stat.S_IRGRP and status.st_gid != 65532)
    ):
        raise PhysicalAcceptanceError(
            f"{label} must be caller-owned and private to the sealed reader group"
        )
    return resolved


def _validated_model_auth(path: Path) -> tuple[bytes, str]:
    resolved = _require_private_file(path, label="model auth file")
    named = resolved.stat()
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
        try:
            opened = os.fstat(descriptor)
            opened_permissions = stat.S_IMODE(opened.st_mode)
            if (
                (opened.st_dev, opened.st_ino, opened.st_ctime_ns, opened.st_size)
                != (named.st_dev, named.st_ino, named.st_ctime_ns, named.st_size)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or opened_permissions & (stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO)
                or (opened_permissions & stat.S_IRGRP and opened.st_gid != 65532)
            ):
                raise PhysicalAcceptanceError(
                    "model auth file changed during private preflight"
                )
            raw = os.read(descriptor, 1_000_001)
            after = os.fstat(descriptor)
            if len(raw) > 1_000_000 or (
                after.st_size,
                after.st_ctime_ns,
                after.st_mtime_ns,
            ) != (opened.st_size, opened.st_ctime_ns, opened.st_mtime_ns):
                raise PhysicalAcceptanceError("model auth file changed while read")
        finally:
            os.close(descriptor)
        value = json.loads(raw.decode("ascii", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PhysicalAcceptanceError(
            "model auth file is not valid ASCII JSON"
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"access_token", "account_id", "contract", "source_sha256"}
        or value.get("contract") != "EvaModelAccessLease/v1"
        or not isinstance(value.get("source_sha256"), str)
        or _HEX_SHA256.fullmatch(value["source_sha256"]) is None
    ):
        raise PhysicalAcceptanceError("model auth file is not an access-only lease")
    access_token = value.get("access_token")
    account_id = value.get("account_id")
    if (
        not isinstance(access_token, str)
        or not 16 <= len(access_token) <= 8_192
        or not access_token.isascii()
        or any(
            character.isspace() or ord(character) == 0x7F for character in access_token
        )
        or not isinstance(account_id, str)
        or not 3 <= len(account_id) <= 256
        or not account_id.isascii()
        or any(
            character.isspace() or ord(character) == 0x7F for character in account_id
        )
    ):
        raise PhysicalAcceptanceError("model auth access authority is invalid")
    canonical = _canonical(value)
    return canonical, str(value["source_sha256"])


def _inspect_image(reference: str, *, candidate: bool = False) -> dict[str, Any]:
    if _IMAGE_REFERENCE.fullmatch(reference) is None:
        raise PhysicalAcceptanceError(
            "every image must be supplied as sha256:<image-id> or name@sha256:<digest>"
        )
    rows = _docker_json("image", "inspect", reference)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise PhysicalAcceptanceError(f"image authority is ambiguous: {reference}")
    row = rows[0]
    image_id = row.get("Id")
    if not isinstance(image_id, str) or _DIGEST.fullmatch(image_id) is None:
        raise PhysicalAcceptanceError(f"image has no immutable local ID: {reference}")
    if candidate and reference != image_id:
        raise PhysicalAcceptanceError(
            "candidate image must be passed by its exact local sha256 image ID"
        )
    return row


def _candidate_uid(image: dict[str, Any]) -> int:
    config = image.get("Config")
    user = config.get("User") if isinstance(config, dict) else None
    raw_uid = user.split(":", 1)[0] if isinstance(user, str) else ""
    if _UID.fullmatch(raw_uid) is None or user != "65532:65532":
        raise PhysicalAcceptanceError(
            "candidate image must declare the sealed non-root user 65532:65532"
        )
    return int(raw_uid)


def _require_declared_uid(
    image: dict[str, Any], expected_uid: int, *, label: str
) -> None:
    config = image.get("Config")
    user = config.get("User") if isinstance(config, dict) else None
    raw_uid = user.split(":", 1)[0] if isinstance(user, str) else ""
    if raw_uid != str(expected_uid):
        raise PhysicalAcceptanceError(
            f"{label} must declare immutable runtime UID {expected_uid}"
        )


def _source_commit(image: dict[str, Any]) -> str:
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    value = labels.get("io.eva2.source-commit") if isinstance(labels, dict) else None
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise PhysicalAcceptanceError(
            "candidate image has no valid source-commit label"
        )
    return value


def _require_clean_source(source_root: Path, expected_commit: str) -> Path:
    root = source_root.resolve(strict=True)
    actual = (
        _run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"], timeout=10)
        .stdout.decode()
        .strip()
    )
    if actual != expected_commit:
        raise PhysicalAcceptanceError("source root HEAD differs from candidate label")
    tracked = _run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        timeout=20,
    ).stdout
    if tracked:
        raise PhysicalAcceptanceError("source root has tracked modifications")
    return root


def _require_commit_bound_file(
    source_root: Path,
    relative_path: str,
    expected_commit: str,
    *,
    label: str,
    maximum_bytes: int,
) -> Path:
    candidate = _require_file(
        source_root / relative_path, label=label, maximum_bytes=maximum_bytes
    )
    tracked = _run(
        ["git", "-C", str(source_root), "ls-files", "--error-unmatch", relative_path],
        check=False,
        timeout=15,
    )
    if tracked.returncode != 0:
        raise PhysicalAcceptanceError(f"{label} is not tracked by the source commit")
    committed = _run(
        ["git", "-C", str(source_root), "show", f"{expected_commit}:{relative_path}"],
        check=False,
        timeout=30,
    )
    if committed.returncode != 0 or committed.stdout != candidate.read_bytes():
        raise PhysicalAcceptanceError(f"{label} differs from the exact source commit")
    return candidate


def _require_external_proof_source(path: Path) -> tuple[Path, str]:
    runtime_script = _require_file(
        path, label="runtime proof harness", maximum_bytes=1_000_000
    )
    if runtime_script.name != "prove_filesystem_provider_sigkill_v2.py":
        raise PhysicalAcceptanceError("runtime proof harness filename differs")
    root_result = _run(
        ["git", "-C", str(runtime_script.parent), "rev-parse", "--show-toplevel"],
        check=False,
        timeout=15,
    )
    if root_result.returncode != 0:
        raise PhysicalAcceptanceError("runtime proof harness has no Git authority")
    proof_root = Path(root_result.stdout.decode("utf-8", errors="strict").strip())
    relative = runtime_script.relative_to(proof_root).as_posix()
    proof_commit = (
        _run(
            ["git", "-C", str(proof_root), "rev-parse", "--verify", "HEAD"],
            timeout=15,
        )
        .stdout.decode("ascii")
        .strip()
    )
    if _COMMIT.fullmatch(proof_commit) is None:
        raise PhysicalAcceptanceError("runtime proof source commit is invalid")
    dirty = _run(
        ["git", "-C", str(proof_root), "status", "--porcelain", "--untracked-files=no"],
        timeout=30,
    ).stdout
    committed = _run(
        ["git", "-C", str(proof_root), "show", f"{proof_commit}:{relative}"],
        check=False,
        timeout=30,
    )
    if (
        dirty
        or committed.returncode != 0
        or committed.stdout != runtime_script.read_bytes()
    ):
        raise PhysicalAcceptanceError(
            "runtime proof harness is not clean commit-bound source"
        )
    return runtime_script, proof_commit


def _create_output_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise PhysicalAcceptanceError("output directory must be absent or empty")
    else:
        resolved.mkdir(mode=0o700, parents=False)
    resolved.chmod(0o700)
    return resolved


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


def _assert_volume_metadata(
    *,
    helper_image: str,
    volume: str,
    container_name: str,
    run_id: str,
    directory_owner: tuple[int, int, int],
    files: dict[str, tuple[int, int, int]],
) -> None:
    program = r"""
import json,os,stat,sys
expected=json.load(sys.stdin)
root='/state'
root_stat=os.lstat(root)
directory_owner=tuple(expected['directoryOwner'])
if (root_stat.st_uid,root_stat.st_gid,stat.S_IMODE(root_stat.st_mode)) != directory_owner:
    raise SystemExit('directory metadata differs')
if set(os.listdir(root)) != set(expected['files']):
    raise SystemExit('file set differs')
for name,metadata in expected['files'].items():
    row=os.lstat(os.path.join(root,name))
    observed=(row.st_uid,row.st_gid,stat.S_IMODE(row.st_mode))
    if not stat.S_ISREG(row.st_mode) or row.st_nlink != 1 or observed != tuple(metadata):
        raise SystemExit('file metadata differs')
print('metadata-ok')
"""
    result = _run(
        [
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
            f"{directory_owner[0]}:{directory_owner[1]}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *_resource_arguments(run_id),
            "--mount",
            f"type=volume,src={volume},dst=/state,readonly",
            "--entrypoint",
            "python3",
            helper_image,
            "-c",
            program,
        ],
        input_bytes=_canonical({"directoryOwner": directory_owner, "files": files}),
    )
    if result.stdout.strip() != b"metadata-ok":
        raise PhysicalAcceptanceError("private volume metadata was not confirmed")


def _wait_postgres(container: str) -> None:
    for _ in range(90):
        result = _run(
            [
                "docker",
                "exec",
                container,
                "pg_isready",
                "-U",
                "postgres",
                "-d",
                DB_NAME,
            ],
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise PhysicalAcceptanceError("disposable PostgreSQL did not become ready")


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
    raise PhysicalAcceptanceError("filesystem MCP did not pass initialize readiness")


def _wait_time_provider(container: str) -> None:
    for _ in range(90):
        result = _run(
            [
                "docker",
                "exec",
                container,
                "curl",
                "-sf",
                "http://127.0.0.1:9080/health",
            ],
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise PhysicalAcceptanceError("fast-time distractor did not become ready")


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
        raise PhysicalAcceptanceError(
            "ContextForge request helper returned invalid JSON"
        ) from error
    if not isinstance(envelope, dict) or envelope.get("status") not in expected:
        status = envelope.get("status") if isinstance(envelope, dict) else None
        raise PhysicalAcceptanceError(
            f"ContextForge {method} {path} returned unexpected HTTP {status}"
        )
    return envelope.get("value")


def _wait_contextforge(container: str) -> None:
    for _ in range(120):
        try:
            value = _gateway_request(container, "GET", "/health")
            if isinstance(value, dict) and value.get("status") == "healthy":
                return
        except PhysicalAcceptanceError:
            pass
        time.sleep(1)
    raise PhysicalAcceptanceError("ContextForge did not become healthy")


def _login_contextforge(container: str, password: str) -> str:
    value = _gateway_request(
        container,
        "POST",
        "/auth/login",
        payload={"email": "admin@example.com", "password": password},
    )
    token = value.get("access_token") if isinstance(value, dict) else None
    if not isinstance(token, str) or not token:
        raise PhysicalAcceptanceError("ContextForge login returned no access token")
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
        raise PhysicalAcceptanceError("ContextForge tool list is malformed")
    return value


def _row_id(row: dict[str, Any], *, label: str) -> str:
    value = row.get("id")
    if not isinstance(value, str) or not value:
        raise PhysicalAcceptanceError(f"{label} omitted its ID")
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
        raise PhysicalAcceptanceError("ContextForge gateway registration is malformed")
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
            raise PhysicalAcceptanceError(
                f"ContextForge discovered duplicate {original_name!r} tools"
            )
        time.sleep(1)
    raise PhysicalAcceptanceError(
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
        raise PhysicalAcceptanceError("ContextForge discovered tool authority is malformed")
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
    raise PhysicalAcceptanceError(
        f"ContextForge did not persist public visibility for tool {tool_id}"
    )


def _disable_unselected_tools(
    container: str,
    token: str,
    selected_ids: set[str],
) -> None:
    for row in _list_tools(container, token, include_metrics=False):
        tool_id = _row_id(row, label="discovered tool")
        if tool_id in selected_ids:
            continue
        if row.get("enabled") is True:
            _gateway_request(
                container,
                "POST",
                f"/tools/{tool_id}/state?activate=false",
                token=token,
            )
    active = [
        row
        for row in _list_tools(container, token, include_metrics=False)
        if row.get("enabled") is True
    ]
    if {str(row.get("id")) for row in active} != selected_ids:
        raise PhysicalAcceptanceError(
            "ContextForge active tool set is not exactly three"
        )


def _create_virtual_server(
    container: str,
    token: str,
    *,
    name: str,
    tool_ids: list[str],
) -> str:
    value = _gateway_request(
        container,
        "POST",
        "/servers",
        payload={
            "associated_tools": tool_ids,
            "description": "Disposable TOOL01 v2 filesystem acceptance server.",
            "name": name,
            "visibility": "public",
        },
        token=token,
        expected=(200, 201),
    )
    if not isinstance(value, dict):
        raise PhysicalAcceptanceError("ContextForge virtual server is malformed")
    server_id = _row_id(value, label="virtual server")
    server_tools = _gateway_request(
        container,
        "GET",
        f"/servers/{server_id}/tools?include_inactive=false&include_metrics=false",
        token=token,
    )
    if not isinstance(server_tools, list) or {
        str(row.get("id")) for row in server_tools if isinstance(row, dict)
    } != set(tool_ids):
        raise PhysicalAcceptanceError(
            "virtual server does not expose exactly three tools"
        )
    return server_id


def _tool_metric(row: dict[str, Any]) -> int:
    value = row.get("execution_count", row.get("executionCount"))
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        metrics = row.get("metrics")
        value = metrics.get("total_executions") if isinstance(metrics, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PhysicalAcceptanceError("ContextForge omitted an immediate tool metric")
    return value


def _read_metrics(
    container: str,
    token: str,
    server_id: str,
    selected: dict[str, dict[str, Any]],
) -> dict[str, int]:
    rows = _gateway_request(
        container,
        "GET",
        f"/servers/{server_id}/tools?include_inactive=false&include_metrics=true",
        token=token,
    )
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PhysicalAcceptanceError("ContextForge server metrics are malformed")
    by_id = {str(row.get("id")): row for row in rows}
    expected_ids = {
        _row_id(authority, label=f"{role} tool") for role, authority in selected.items()
    }
    if set(by_id) != expected_ids:
        raise PhysicalAcceptanceError("ContextForge server metric set is not exact")
    result: dict[str, int] = {}
    for role, authority in selected.items():
        row = by_id.get(_row_id(authority, label=f"{role} tool"))
        if row is None:
            raise PhysicalAcceptanceError(f"ContextForge metrics omitted {role}")
        result[role] = _tool_metric(row)
    return result


def _wait_metrics(
    container: str,
    token: str,
    server_id: str,
    selected: dict[str, dict[str, Any]],
    expected: dict[str, int],
) -> dict[str, int]:
    for _ in range(30):
        current = _read_metrics(container, token, server_id, selected)
        if current == expected:
            return current
        if any(current[key] > expected[key] for key in expected):
            raise PhysicalAcceptanceError(
                f"ContextForge provider count exceeded expectation: {current}"
            )
        time.sleep(1)
    raise PhysicalAcceptanceError(
        f"ContextForge provider counts did not become immediate: expected {expected}"
    )


def _populate_secret_volume(
    *,
    helper_image: str,
    volume: str,
    container_name: str,
    run_id: str,
    name: str,
    value: bytes,
    uid: int,
) -> None:
    program = r"""
import json, os, sys
row=json.load(sys.stdin)
name=row['name']
if '/' in name or name in {'', '.', '..'}:
    raise SystemExit('unsafe secret name')
target='/state/'+name
descriptor=os.open(target, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
try:
    os.write(descriptor, bytes.fromhex(row['contentHex']))
    os.fsync(descriptor)
finally:
    os.close(descriptor)
uid=int(row['uid'])
os.chmod(target,0o400)
os.chown(target,uid,uid)
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
    _run(
        command,
        input_bytes=_canonical({"contentHex": value.hex(), "name": name, "uid": uid}),
    )


def _container_state(name: str) -> dict[str, Any]:
    rows = _docker_json("container", "inspect", name)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise PhysicalAcceptanceError(f"container inspect failed for {name}")
    state = rows[0].get("State")
    if not isinstance(state, dict):
        raise PhysicalAcceptanceError(f"container state is missing for {name}")
    return state


def _container_id(name: str) -> str:
    rows = _docker_json("container", "inspect", name)
    value = rows[0].get("Id") if isinstance(rows, list) and rows else None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PhysicalAcceptanceError(f"container ID is invalid for {name}")
    return value


def _create_eva_container(
    *,
    name: str,
    process_id: str,
    candidate_image: str,
    candidate_uid: int,
    source_commit: str,
    run_id: str,
    nonce: str,
    proof_network: str,
    state_volume: str,
    candidate_secret_volume: str,
    model_secret_volume: str | None,
    runtime_script: Path,
    virtual_server_id: str,
) -> str:
    script_target = "/run/proof-harness/prove_filesystem_provider_sigkill_v2.py"
    phase = {"p1": "start", "p2": "resume", "p3": "replay"}.get(process_id)
    if phase is None:
        raise PhysicalAcceptanceError("unknown Eva proof phase")
    id_file = f"/run/secrets/container-{process_id}-id"
    wrapper = (
        "import os,pathlib,sys;"
        f"os.environ['EVA_PROOF_CONTAINER_ID']=pathlib.Path({id_file!r})."
        "read_text(encoding='ascii').strip();"
        f"os.execv(sys.executable,[sys.executable,{script_target!r},{phase!r}])"
    )
    command = [
        "docker",
        "create",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        proof_network,
        "--network-alias",
        process_id,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size=128m,uid={candidate_uid},gid={candidate_uid},mode=0700",
        *_resource_arguments(run_id),
        "--mount",
        f"type=volume,src={state_volume},dst=/run/proof-state",
        "--mount",
        f"type=volume,src={candidate_secret_volume},dst=/run/secrets,readonly",
        "--mount",
        f"type=bind,src={runtime_script},dst={script_target},readonly",
        "--env",
        f"EVA_PROOF_COUNTER_SERVER_ID={virtual_server_id}",
        "--env",
        f"EVA_PROOF_IMAGE_ID={candidate_image}",
        "--env",
        f"EVA_PROOF_NONCE={nonce}",
        "--env",
        f"EVA_PROOF_RUN_ID={run_id}",
        "--env",
        f"EVA_PROOF_SOURCE_COMMIT={source_commit}",
    ]
    if model_secret_volume is not None:
        command.extend(
            [
                "--mount",
                f"type=volume,src={model_secret_volume},dst=/run/eva/model-lease,readonly",
            ]
        )
    command.extend(["--entrypoint", "python3", candidate_image, "-c", wrapper])
    container_id = _docker(*command[1:])
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise PhysicalAcceptanceError(f"Docker returned an invalid {process_id} ID")
    return container_id


def _start_attached(name: str, log_path: Path) -> None:
    result = _run(["docker", "start", "--attach", name], check=False, timeout=300)
    log_path.write_bytes(result.stdout + result.stderr)
    state = _container_state(name)
    if (
        result.returncode != 0
        or state.get("Status") != "exited"
        or state.get("ExitCode") != 0
        or state.get("OOMKilled") is not False
    ):
        raise PhysicalAcceptanceError(f"{name} exited unsuccessfully")


def _wait_marker(container: str, marker: str) -> None:
    for _ in range(120):
        state = _container_state(container)
        if state.get("Running") is not True:
            raise PhysicalAcceptanceError("p1 exited before the post-provider marker")
        result = _run(
            ["docker", "exec", container, "test", "-f", f"/run/proof-state/{marker}"],
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise PhysicalAcceptanceError("p1 did not expose the post-provider kill window")


def _copy_runtime_state(container: str, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    _docker("cp", f"{container}:/run/proof-state/.", str(destination), timeout=120)


def _artifact_reference(root: Path, path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _container_audit_row(
    *,
    process_id: str,
    name: str,
    image_id: str,
    source_commit: str,
) -> dict[str, Any]:
    rows = _docker_json("container", "inspect", name)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise PhysicalAcceptanceError(f"container audit inspect failed for {name}")
    inspected = rows[0]
    state = inspected.get("State")
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        inspected.get("Image") != image_id
        or not isinstance(state, dict)
        or not isinstance(labels, dict)
        or labels.get("io.eva2.source-commit") != source_commit
    ):
        raise PhysicalAcceptanceError(f"container authority differs for {name}")
    container_id = inspected.get("Id")
    if (
        not isinstance(container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise PhysicalAcceptanceError(f"container audit ID is invalid for {name}")
    return {
        "containerId": container_id,
        "imageId": image_id,
        "processId": process_id,
        "sourceCommitLabel": source_commit,
        "state": {
            "exitCode": state.get("ExitCode"),
            "oomKilled": state.get("OOMKilled"),
            "status": state.get("Status"),
        },
    }


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--filesystem-image", required=True)
    parser.add_argument("--time-image", required=True)
    parser.add_argument("--contextforge-image", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--runtime-script", required=True, type=Path)
    parser.add_argument("--expected-runtime-script-sha256", required=True)
    parser.add_argument("--expected-proof-source-commit", required=True)
    parser.add_argument("--model-auth-file", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    model_auth, model_auth_source_sha256 = _validated_model_auth(
        arguments.model_auth_file
    )
    candidate = _inspect_image(arguments.candidate_image, candidate=True)
    filesystem_image = _inspect_image(arguments.filesystem_image)
    time_image = _inspect_image(arguments.time_image)
    contextforge_image = _inspect_image(arguments.contextforge_image)
    postgres_image = _inspect_image(arguments.postgres_image)
    _require_declared_uid(filesystem_image, 10001, label="filesystem image")
    _require_declared_uid(contextforge_image, 10001, label="ContextForge image")
    source_commit = _source_commit(candidate)
    source_root = _require_clean_source(arguments.source_root, source_commit)
    if _HEX_SHA256.fullmatch(arguments.expected_runtime_script_sha256) is None:
        raise PhysicalAcceptanceError(
            "expected runtime proof harness SHA-256 is invalid"
        )
    runtime_script, proof_source_commit = _require_external_proof_source(
        arguments.runtime_script
    )
    if (
        _COMMIT.fullmatch(arguments.expected_proof_source_commit) is None
        or proof_source_commit != arguments.expected_proof_source_commit
    ):
        raise PhysicalAcceptanceError(
            "runtime proof source differs from the external commit authority"
        )
    if _file_sha256(runtime_script) != arguments.expected_runtime_script_sha256:
        raise PhysicalAcceptanceError(
            "runtime proof harness differs from external hash"
        )
    contextforge_start = _require_commit_bound_file(
        source_root,
        "eva2/experiments/contextforge-gateway-v1/start-contextforge.sh",
        source_commit,
        label="ContextForge start script",
        maximum_bytes=100_000,
    )
    candidate_uid = _candidate_uid(candidate)
    output_root = _create_output_root(arguments.output_dir)

    nonce = secrets.token_hex(6)
    run_id = f"tool01-v2-{nonce}"
    prefix = f"eva2-tool01-{nonce}"
    proof_network = f"{prefix}-internal"
    egress_network = f"{prefix}-egress"
    postgres_volume = f"{prefix}-postgres"
    filesystem_volume = f"{prefix}-filesystem"
    state_volume = f"{prefix}-state"
    contextforge_secret_volume = f"{prefix}-contextforge-secrets"
    filesystem_secret_volume = f"{prefix}-filesystem-secrets"
    postgres_secret_volume = f"{prefix}-postgres-secret"
    candidate_secret_volume = f"{prefix}-candidate-secrets"
    model_secret_volume = f"{prefix}-model-secret"
    containers = {
        "postgres": f"{prefix}-postgres",
        "filesystem": f"{prefix}-filesystem",
        "time": f"{prefix}-time",
        "contextforge": f"{prefix}-contextforge",
        "p1": f"{prefix}-p1",
        "p2": f"{prefix}-p2",
        "p3": f"{prefix}-p3",
    }
    all_networks = (proof_network, egress_network)
    all_volumes = (
        postgres_volume,
        filesystem_volume,
        state_volume,
        contextforge_secret_volume,
        filesystem_secret_volume,
        postgres_secret_volume,
        candidate_secret_volume,
        model_secret_volume,
    )
    all_containers = (
        *containers.values(),
        f"{prefix}-contextforge-secret-init",
        f"{prefix}-filesystem-secret-init",
        f"{prefix}-postgres-secret-init",
        f"{prefix}-candidate-secret-init",
        f"{prefix}-model-secret-init",
        f"{prefix}-model-secret-check",
        f"{prefix}-filesystem-init",
        f"{prefix}-state-init",
        f"{prefix}-p1-id-init",
        f"{prefix}-p2-id-init",
        f"{prefix}-p3-id-init",
    )
    for network in all_networks:
        _require_docker_resource_absent("network", network)
    for volume in all_volumes:
        _require_docker_resource_absent("volume", volume)
    for container in all_containers:
        _require_docker_resource_absent("container", container)
    host_authority_path = output_root / "hostAuthority.json"
    _write_json(
        host_authority_path,
        {
            "contextforgeStartSha256": _file_sha256(contextforge_start),
            "contract": "EvaTool01PhysicalHostAuthority/v1",
            "imageIds": {
                "candidate": candidate["Id"],
                "contextforge": contextforge_image["Id"],
                "filesystem": filesystem_image["Id"],
                "postgres": postgres_image["Id"],
                "time": time_image["Id"],
            },
            "modelLease": {
                "accessOnly": True,
                "leaseSha256": hashlib.sha256(model_auth).hexdigest(),
                "sourceSha256": model_auth_source_sha256,
            },
            "proofHarnessSha256": arguments.expected_runtime_script_sha256,
            "proofSourceCommit": proof_source_commit,
            "runId": run_id,
            "sourceCommit": source_commit,
        },
    )
    success_result: dict[str, str] | None = None
    try:
        for network, internal in ((proof_network, True), (egress_network, False)):
            command = ["network", "create", *_resource_arguments(run_id)]
            if internal:
                command.append("--internal")
            command.append(network)
            _docker(*command)
        for volume in all_volumes:
            _docker("volume", "create", *_resource_arguments(run_id), volume)

        jwt = secrets.token_urlsafe(48).encode()
        encryption = secrets.token_urlsafe(48).encode()
        admin_password = secrets.token_urlsafe(32)
        filesystem_jwt = secrets.token_urlsafe(48).encode()
        filesystem_encryption = secrets.token_urlsafe(48).encode()
        filesystem_admin_password = secrets.token_urlsafe(32).encode()
        postgres_password = secrets.token_urlsafe(32).encode()
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=contextforge_secret_volume,
            container_name=f"{prefix}-contextforge-secret-init",
            run_id=run_id,
            files={
                "contextforge-jwt": (jwt, 10001, 10001, 0o400),
                "contextforge-encryption": (encryption, 10001, 10001, 0o400),
                "contextforge-admin-password": (
                    admin_password.encode(),
                    10001,
                    10001,
                    0o400,
                ),
            },
        )
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=filesystem_secret_volume,
            container_name=f"{prefix}-filesystem-secret-init",
            run_id=run_id,
            files={
                "filesystem-jwt": (filesystem_jwt, 10001, 10001, 0o400),
                "filesystem-encryption": (
                    filesystem_encryption,
                    10001,
                    10001,
                    0o400,
                ),
                "filesystem-admin-password": (
                    filesystem_admin_password,
                    10001,
                    10001,
                    0o400,
                ),
            },
        )
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=postgres_secret_volume,
            container_name=f"{prefix}-postgres-secret-init",
            run_id=run_id,
            files={"postgres-password": (postgres_password, 0, 0, 0o400)},
        )
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=candidate_secret_volume,
            container_name=f"{prefix}-candidate-secret-init",
            run_id=run_id,
            files={
                "gateway-password": (
                    admin_password.encode(),
                    candidate_uid,
                    candidate_uid,
                    0o400,
                ),
                "postgres-password": (
                    postgres_password,
                    candidate_uid,
                    candidate_uid,
                    0o400,
                ),
            },
        )
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=model_secret_volume,
            container_name=f"{prefix}-model-secret-init",
            run_id=run_id,
            files={"auth.json": (model_auth, 1000, 65532, 0o640)},
            directory_owner=(1000, 65532, 0o750),
        )
        _assert_volume_metadata(
            helper_image=arguments.contextforge_image,
            volume=model_secret_volume,
            container_name=f"{prefix}-model-secret-check",
            run_id=run_id,
            directory_owner=(1000, 65532, 0o750),
            files={"auth.json": (1000, 65532, 0o640)},
        )
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=filesystem_volume,
            container_name=f"{prefix}-filesystem-init",
            run_id=run_id,
            files={},
            directory_owner=(10001, 10001, 0o700),
        )
        _init_volume(
            helper_image=arguments.contextforge_image,
            volume=state_volume,
            container_name=f"{prefix}-state-init",
            run_id=run_id,
            files={},
            directory_owner=(candidate_uid, candidate_uid, 0o700),
        )

        _docker(
            "run",
            "--detach",
            "--name",
            containers["postgres"],
            "--pull",
            "never",
            "--network",
            proof_network,
            "--network-alias",
            "proof-postgres",
            *_resource_arguments(run_id),
            "--env",
            f"POSTGRES_DB={DB_NAME}",
            "--env",
            "POSTGRES_USER=postgres",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password",
            "--mount",
            f"type=volume,src={postgres_volume},dst=/var/lib/postgresql/data",
            "--mount",
            f"type=volume,src={postgres_secret_volume},dst=/run/secrets,readonly",
            arguments.postgres_image,
        )
        _wait_postgres(containers["postgres"])
        postgres_version = _docker(
            "exec",
            containers["postgres"],
            "psql",
            "-U",
            "postgres",
            "-d",
            DB_NAME,
            "-Atqc",
            "SHOW server_version_num",
        )
        if postgres_version != str(POSTGRES_VERSION_NUM):
            raise PhysicalAcceptanceError("disposable PostgreSQL is not exact 16.13")

        _docker(
            "run",
            "--detach",
            "--name",
            containers["filesystem"],
            "--pull",
            "never",
            "--network",
            proof_network,
            "--network-alias",
            "proof-filesystem",
            "--no-healthcheck",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--env",
            "EVA_CONTEXTFORGE_JWT_SECRET_FILE=/run/secrets/filesystem-jwt",
            "--env",
            "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE=/run/secrets/filesystem-encryption",
            "--env",
            "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE=/run/secrets/filesystem-admin-password",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700",
            *_resource_arguments(run_id),
            "--mount",
            f"type=volume,src={filesystem_volume},dst=/proof-state",
            "--mount",
            f"type=volume,src={filesystem_secret_volume},dst=/run/secrets,readonly",
            arguments.filesystem_image,
        )
        _wait_filesystem_mcp(containers["filesystem"])

        _docker(
            "run",
            "--detach",
            "--name",
            containers["time"],
            "--pull",
            "never",
            "--network",
            proof_network,
            "--network-alias",
            "proof-time",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            *_resource_arguments(run_id),
            arguments.time_image,
        )
        _wait_time_provider(containers["time"])

        _docker(
            "run",
            "--detach",
            "--name",
            containers["contextforge"],
            "--pull",
            "never",
            "--network",
            proof_network,
            "--network-alias",
            "contextforge",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700",
            *_resource_arguments(run_id),
            "--env",
            "AUTH_REQUIRED=true",
            "--env",
            "CACHE_TYPE=none",
            "--env",
            "DATABASE_URL=sqlite:////tmp/contextforge.db",
            "--env",
            "DB_METRICS_RECORDING_ENABLED=true",
            "--env",
            "ENABLE_METRICS=false",
            "--env",
            "ENVIRONMENT=production",
            "--env",
            "GATEWAY_TOOL_NAME_SEPARATOR=_",
            "--env",
            "GUNICORN_PRELOAD_APP=true",
            "--env",
            "GUNICORN_WORKERS=1",
            "--env",
            "HOST=0.0.0.0",
            "--env",
            "LOG_TO_FILE=false",
            "--env",
            "MCPGATEWAY_SKIP_MIGRATIONS=false",
            "--env",
            "MCPGATEWAY_STDIO_TRANSPORT_ENABLED=false",
            "--env",
            "MCPGATEWAY_UI_AIRGAPPED=true",
            "--env",
            "METRICS_BUFFER_ENABLED=false",
            "--env",
            "METRICS_CACHE_ENABLED=false",
            "--env",
            "PLUGINS_CAN_OVERRIDE_RBAC=false",
            "--env",
            "REQUIRE_STRONG_SECRETS=true",
            "--env",
            "SSRF_ALLOW_LOCALHOST=false",
            "--env",
            "SSRF_ALLOW_PRIVATE_NETWORKS=true",
            "--env",
            "SSRF_DNS_FAIL_CLOSED=true",
            "--env",
            "UAID_ALLOWED_DOMAINS=[]",
            "--env",
            "EVA_CONTEXTFORGE_JWT_SECRET_FILE=/run/secrets/contextforge-jwt",
            "--env",
            "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE=/run/secrets/contextforge-encryption",
            "--env",
            "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE=/run/secrets/contextforge-admin-password",
            "--mount",
            f"type=volume,src={contextforge_secret_volume},dst=/run/secrets,readonly",
            "--mount",
            f"type=bind,src={contextforge_start},dst=/run/eva2/start-contextforge.sh,readonly",
            "--entrypoint",
            "/bin/sh",
            arguments.contextforge_image,
            "/run/eva2/start-contextforge.sh",
        )
        _wait_contextforge(containers["contextforge"])
        token = _login_contextforge(containers["contextforge"], admin_password)

        filesystem_gateway = _register_gateway(
            containers["contextforge"],
            token,
            name=f"filesystem_{nonce}",
            url="http://proof-filesystem:9000/mcp",
        )
        time_gateway = _register_gateway(
            containers["contextforge"],
            token,
            name=f"time_{nonce}",
            url="http://proof-time:9080/mcp",
        )
        effect = _make_tool_public(
            containers["contextforge"],
            token,
            _wait_gateway_tool(
                containers["contextforge"],
                token,
                gateway_id=filesystem_gateway,
                original_name="write_file",
            ),
        )
        readback = _make_tool_public(
            containers["contextforge"],
            token,
            _wait_gateway_tool(
                containers["contextforge"],
                token,
                gateway_id=filesystem_gateway,
                original_name="read_text_file",
            ),
        )
        distractor = _make_tool_public(
            containers["contextforge"],
            token,
            _wait_gateway_tool(
                containers["contextforge"],
                token,
                gateway_id=time_gateway,
                original_name="echo",
            ),
        )
        selected = {
            "effect": effect,
            "readback": readback,
            "distractor": distractor,
        }
        selected_ids = {
            _row_id(row, label=f"{role} tool") for role, row in selected.items()
        }
        _disable_unselected_tools(containers["contextforge"], token, selected_ids)
        virtual_server_id = _create_virtual_server(
            containers["contextforge"],
            token,
            name=f"tool01_{nonce}",
            tool_ids=[_row_id(selected[role], label=role) for role in selected],
        )
        baseline = _read_metrics(
            containers["contextforge"], token, virtual_server_id, selected
        )
        if baseline != {"effect": 0, "readback": 0, "distractor": 0}:
            raise PhysicalAcceptanceError(
                "new ContextForge tools did not start at zero"
            )

        p1_id = _create_eva_container(
            name=containers["p1"],
            process_id="p1",
            candidate_image=arguments.candidate_image,
            candidate_uid=candidate_uid,
            source_commit=source_commit,
            run_id=run_id,
            nonce=nonce,
            proof_network=proof_network,
            state_volume=state_volume,
            candidate_secret_volume=candidate_secret_volume,
            model_secret_volume=model_secret_volume,
            runtime_script=runtime_script,
            virtual_server_id=virtual_server_id,
        )
        _populate_secret_volume(
            helper_image=arguments.contextforge_image,
            volume=candidate_secret_volume,
            container_name=f"{prefix}-p1-id-init",
            run_id=run_id,
            name="container-p1-id",
            value=p1_id.encode("ascii"),
            uid=candidate_uid,
        )
        _docker(
            "network",
            "connect",
            "--gw-priority",
            "1",
            egress_network,
            containers["p1"],
        )
        _docker("start", containers["p1"])
        _wait_marker(containers["p1"], "effect-returned.json")
        _wait_metrics(
            containers["contextforge"],
            token,
            virtual_server_id,
            selected,
            {"effect": 1, "readback": 0, "distractor": 0},
        )
        pre_kill = _container_state(containers["p1"])
        if pre_kill.get("Running") is not True:
            raise PhysicalAcceptanceError(
                "p1 was not running immediately before SIGKILL"
            )
        kill = _run(
            ["docker", "kill", "--signal", "KILL", containers["p1"]],
            check=False,
            timeout=30,
        )
        if kill.returncode != 0:
            raise PhysicalAcceptanceError("external p1 SIGKILL failed")
        _docker("wait", containers["p1"])
        p1_state = _container_state(containers["p1"])
        if not (
            p1_state.get("Status") == "exited"
            and p1_state.get("ExitCode") == 137
            and p1_state.get("OOMKilled") is False
        ):
            raise PhysicalAcceptanceError("p1 did not end as external non-OOM exit 137")
        p1_logs = _run(["docker", "logs", containers["p1"]], check=False, timeout=30)
        (output_root / "p1.log").write_bytes(p1_logs.stdout + p1_logs.stderr)

        p2_id = _create_eva_container(
            name=containers["p2"],
            process_id="p2",
            candidate_image=arguments.candidate_image,
            candidate_uid=candidate_uid,
            source_commit=source_commit,
            run_id=run_id,
            nonce=nonce,
            proof_network=proof_network,
            state_volume=state_volume,
            candidate_secret_volume=candidate_secret_volume,
            model_secret_volume=model_secret_volume,
            runtime_script=runtime_script,
            virtual_server_id=virtual_server_id,
        )
        _populate_secret_volume(
            helper_image=arguments.contextforge_image,
            volume=candidate_secret_volume,
            container_name=f"{prefix}-p2-id-init",
            run_id=run_id,
            name="container-p2-id",
            value=p2_id.encode("ascii"),
            uid=candidate_uid,
        )
        _docker(
            "network",
            "connect",
            "--gw-priority",
            "1",
            egress_network,
            containers["p2"],
        )
        _start_attached(containers["p2"], output_root / "p2.log")
        _wait_metrics(
            containers["contextforge"],
            token,
            virtual_server_id,
            selected,
            {"effect": 1, "readback": 1, "distractor": 0},
        )

        p3_id = _create_eva_container(
            name=containers["p3"],
            process_id="p3",
            candidate_image=arguments.candidate_image,
            candidate_uid=candidate_uid,
            source_commit=source_commit,
            run_id=run_id,
            nonce=nonce,
            proof_network=proof_network,
            state_volume=state_volume,
            candidate_secret_volume=candidate_secret_volume,
            model_secret_volume=None,
            runtime_script=runtime_script,
            virtual_server_id=virtual_server_id,
        )
        _populate_secret_volume(
            helper_image=arguments.contextforge_image,
            volume=candidate_secret_volume,
            container_name=f"{prefix}-p3-id-init",
            run_id=run_id,
            name="container-p3-id",
            value=p3_id.encode("ascii"),
            uid=candidate_uid,
        )
        _start_attached(containers["p3"], output_root / "p3.log")
        _wait_metrics(
            containers["contextforge"],
            token,
            virtual_server_id,
            selected,
            {"effect": 1, "readback": 1, "distractor": 0},
        )

        runtime_state = output_root / "runtime-state"
        _copy_runtime_state(containers["p3"], runtime_state)
        crash_audit = {
            "containers": [
                _container_audit_row(
                    process_id=process_id,
                    name=containers[process_id],
                    image_id=arguments.candidate_image,
                    source_commit=source_commit,
                )
                for process_id in ("p1", "p2", "p3")
            ],
            "contract": checker.CRASH_CONTRACT,
            "kill": {
                "commandExitCode": kill.returncode,
                "preKillRunning": pre_kill.get("Running") is True,
                "signal": "KILL",
                "targetContainerId": p1_id,
            },
            "runId": run_id,
        }
        crash_path = output_root / "containerCrashAudit.json"
        _write_json(crash_path, crash_audit)
        artifact_paths = {
            "candidateAuthority": runtime_state / "candidateAuthority.json",
            "catalog": runtime_state / "catalog.json",
            "containerCrashAudit": crash_path,
            "durableTimeline": runtime_state / "durableTimeline.json",
            "frameworkTrajectory": runtime_state / "frameworkTrajectory.json",
            "hostAuthority": host_authority_path,
            "providerCounters": runtime_state / "providerCounters.json",
        }
        for name, path in artifact_paths.items():
            _read_json(path, label=name)

        source_path = output_root / "sourceAuthority.json"
        source_result = _run(
            [
                sys.executable,
                str(SOURCE_GENERATOR),
                str(source_root),
                "--git-commit",
                source_commit,
                "--run-id",
                run_id,
                "--output",
                str(source_path),
            ],
            check=False,
            timeout=120,
        )
        if source_result.returncode != 0:
            raise PhysicalAcceptanceError("TOOL01 source authority is not complete")
        source_value = _read_json(source_path, label="source authority")
        if source_value.get("status") != "complete":
            raise PhysicalAcceptanceError("TOOL01 source authority stayed pending")

        bundle = {
            "artifacts": {
                name: _artifact_reference(output_root, path)
                for name, path in artifact_paths.items()
            },
            "contract": checker.BUNDLE_CONTRACT,
            "runId": run_id,
            "schemaVersion": 2,
            "sourceAuthority": {
                "artifact": _artifact_reference(output_root, source_path),
                "status": "complete",
            },
        }
        bundle_path = output_root / "bundle.json"
        _write_json(bundle_path, bundle)
        checked = _run(
            [
                sys.executable,
                str(CHECKER_PATH),
                str(bundle_path),
                "--expected-image-id",
                arguments.candidate_image,
                "--expected-contextforge-image-id",
                contextforge_image["Id"],
                "--expected-filesystem-image-id",
                filesystem_image["Id"],
                "--expected-proof-harness-sha256",
                arguments.expected_runtime_script_sha256,
                "--expected-proof-source-commit",
                arguments.expected_proof_source_commit,
                "--expected-postgres-image-id",
                postgres_image["Id"],
                "--expected-source-commit",
                source_commit,
                "--expected-time-image-id",
                time_image["Id"],
                "--source-root",
                str(source_root),
            ],
            check=False,
            timeout=180,
        )
        (output_root / "checker-result.json").write_bytes(checked.stdout)
        if checked.returncode != 0:
            raise PhysicalAcceptanceError(
                "TOOL01 v2 checker rejected the physical bundle"
            )
        try:
            result = json.loads(checked.stdout)
        except json.JSONDecodeError as error:
            raise PhysicalAcceptanceError(
                "TOOL01 checker returned invalid JSON"
            ) from error
        if (
            not isinstance(result, dict)
            or result.get("replacementStatus") != "replacement_passed"
        ):
            raise PhysicalAcceptanceError("TOOL01 physical acceptance did not pass")
        success_result = {
            "bundle": str(bundle_path),
            "candidateImageId": arguments.candidate_image,
            "replacementStatus": "replacement_passed",
            "runId": run_id,
        }
    finally:
        _cleanup_disposable_resources(
            containers=tuple(reversed(all_containers)),
            volumes=tuple(reversed(all_volumes)),
            networks=tuple(reversed(all_networks)),
        )
    if success_result is None:
        raise AssertionError("successful run omitted its result")
    print(json.dumps(success_result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
