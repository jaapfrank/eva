"""Prepare replacement workspace credentials; activation and revocation are separate.

Never overwrite an existing generation or change a core configuration. A failed
attempt retains its private directory and token metadata for inspected recovery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from tools import provision_workspace_provider as provider

ACCESS_UID = 65532


def read_private(path: Path, uid: int, mode: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode):
            raise RuntimeError("workspace private file permissions differ")
        return stream.read()


@contextmanager
def exclusive_access(root: Path):
    descriptor = os.open(root / "access.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600):
            raise RuntimeError("workspace access lock differs")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def check_catalog(helper, token: str, catalog: dict) -> None:
    rows = {row["name"]: row for row in helper._list_tools(provider.GATEWAY, token, include_metrics=False)}
    specs = provider.parse_mcp_gateway_catalog(json.dumps(catalog))
    for spec in specs:
        for name, expected in ((spec.execute_catalog_name, spec.execute_catalog_sha256),
                               (spec.observe_catalog_name, spec.observe_catalog_sha256)):
            row = rows.get(name)
            if row is None or provider.canonical_sha256(provider.mcp_gateway_catalog_binding(
                    row, contract=spec.contract)) != expected:
                raise RuntimeError("workspace registry differs from the admitted catalog")


def check_readback(helper, token: str, catalog: dict, expected_sha256: str) -> None:
    reply = helper._gateway_request(provider.GATEWAY, "POST", "/rpc", token=token,
        payload={"jsonrpc": "2.0", "id": "workspace-access-check", "method": "src/tools/call",
                 "params": {"name": catalog["tools"][0]["observe_tool_name"],
                            "arguments": {"path": "/proof-state/startcontrole.txt"}}})
    if reply.get("error") or reply.get("result", {}).get("isError"):
        raise RuntimeError("workspace access readback failed")
    direct = helper._docker("exec", provider.FILESYSTEM, "python3", "-c",
        "from pathlib import Path;import hashlib;print(hashlib.sha256(Path('/proof-state/startcontrole.txt').read_bytes()).hexdigest())")
    if direct != expected_sha256:
        raise RuntimeError("workspace access control file changed")
    provider.verify_readback(reply.get("result", {}), "Eva's duurzame werkmap is voorbereid.\n", direct)


def prepare(generation: int, *, include_reads: bool = False) -> dict:
    if os.geteuid() != 0 or generation < 2:
        raise RuntimeError("workspace credential preparation requires root and generation >= 2")
    root = provider.HOST_ROOT
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("workspace root permissions differ")
    with exclusive_access(root):
        return _prepare(root, generation, include_reads=include_reads)


def _prepare(root: Path, generation: int, *, include_reads: bool = False) -> dict:
    previous = root / ("receipt.json" if generation == 2 else f"access-{generation - 1}-receipt.json")
    source = json.loads(read_private(previous, 0, 0o600))
    if source["status"] not in {"standalone_prepared_core_disabled", "replacement_prepared_not_activated"}:
        raise RuntimeError("workspace previous credential preparation is incomplete")
    access = root / f"access-{generation}"
    receipt_path = root / f"access-{generation}-receipt.json"
    if access.exists() or access.is_symlink() or receipt_path.exists() or receipt_path.is_symlink():
        raise RuntimeError("workspace generation already exists; inspect retained state")
    old_access = Path(source["accessDirectory"])
    if old_access != root / f"access-{generation - 1}":
        raise RuntimeError("workspace previous generation differs")
    info = old_access.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != ACCESS_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("workspace access directory permissions differ")
    old_token = read_private(old_access / "token", ACCESS_UID, 0o600)
    catalog_bytes = read_private(old_access / "catalog.json", ACCESS_UID, 0o600)
    if (hashlib.sha256(old_token).hexdigest() != source["tokenSha256"]
            or hashlib.sha256(catalog_bytes).hexdigest() != source["catalogSha256"]):
        raise RuntimeError("workspace previous access binding differs")
    helper = provider._helper()
    for name, image in ((provider.GATEWAY, provider.GATEWAY_IMAGE), (provider.FILESYSTEM, provider.FILESYSTEM_IMAGE)):
        actual = helper._docker_json("inspect", name)[0]
        if actual["Image"] != image.rsplit("@", 1)[1] or not actual["State"]["Running"]:
            raise RuntimeError("workspace provider image or running state differs")
    catalog = json.loads(catalog_bytes)
    check_catalog(helper, old_token.decode(), catalog)
    check_readback(helper, old_token.decode(), catalog, source["independentContentSha256"])
    if include_reads:
        original = json.loads(read_private(root / "receipt.json", 0, 0o600))
        rows = helper._list_tools(provider.GATEWAY, old_token.decode(), include_metrics=False)
        selected = {}
        for name in ("write_file", "read_text_file", "list_directory", "search_files", "get_file_info"):
            matches = [row for row in rows if helper._row_gateway_id(row) == original["gatewayId"]
                       and helper._original_name(row) == name]
            if len(matches) != 1:
                raise RuntimeError("workspace read tool catalog is incomplete")
            selected[name] = matches[0]
        catalog = provider.catalog_for(selected, include_reads=True)
        catalog_bytes = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode()
        check_catalog(helper, old_token.decode(), catalog)
    password = read_private(root / "admin-password", 0, 0o600).decode()
    admin = helper._login_contextforge(provider.GATEWAY, password)
    # Reserve the generation before issuing a credential; failure is never retried silently.
    access.mkdir(mode=0o700)
    os.chown(access, ACCESS_UID, ACCESS_UID)
    issued = helper._gateway_request(provider.GATEWAY, "POST", "/tokens", token=admin,
        payload={"name": f"eva_workspace_generation_{generation}",
                 "scope": {"permissions": ["tools.read", "tools.execute"]}, "expires_in_days": 30}, expected=(201,))
    token = issued["access_token"]
    provider._write_private(access / "token", token.encode(), uid=ACCESS_UID)
    provider._write_private(access / "catalog.json", catalog_bytes, uid=ACCESS_UID)
    result = {"contract": "EvaWorkspaceAccessPreparation/v1", "status": "replacement_issued_unverified",
              "generation": generation, "previousTokenId": source["tokenId"],
              "tokenId": issued["token"]["id"], "tokenExpiresAt": issued["token"]["expires_at"],
              "tokenScopes": issued["token"]["resource_scopes"], "accessDirectory": str(access),
              "tokenSha256": provider._digest(access / "token"), "catalogSha256": hashlib.sha256(catalog_bytes).hexdigest(),
              "independentContentSha256": source["independentContentSha256"],
              "operatorSha256": provider._digest(Path(__file__)),
              "coreConfigurationChanged": False, "previousTokenRevoked": False}
    provider._write_private(root / f"access-{generation}-issued.json", json.dumps(result).encode())
    if set(result["tokenScopes"]) != {"tools.read", "tools.execute"}:
        raise RuntimeError("workspace replacement token scopes differ")
    helper._gateway_request(provider.GATEWAY, "GET", "/tokens", token=token, expected=(403,))
    check_catalog(helper, token, catalog)
    check_readback(helper, token, catalog, source["independentContentSha256"])
    # The predecessor must still work for rollback.
    check_readback(helper, old_token.decode(), catalog, source["independentContentSha256"])
    result.update(status="replacement_prepared_not_activated", previousTokenStillValid=True,
                  tokenListingDenied=True, independentReadbackPassed=True)
    provider._write_private(receipt_path, json.dumps(result).encode())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generation", type=int)
    parser.add_argument("--include-reads", action="store_true", help="Include existing workspace read, list, search and metadata tools within the same private root")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.generation, include_reads=args.include_reads)))
        return 0
    except Exception:
        print(json.dumps({"status": "refused_or_incomplete", "error": "Inspect retained private generation state; never delete or retry blindly."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
