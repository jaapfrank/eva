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
    tool = catalog["tools"][0]
    actual = provider.catalog_for({"write_file": rows[tool["execute_catalog_name"]],
                                   "read_text_file": rows[tool["observe_catalog_name"]]})
    if actual != catalog:
        raise RuntimeError("workspace registry differs from the admitted catalog")


def check_readback(helper, token: str, catalog: dict, expected_sha256: str) -> None:
    reply = helper._gateway_request(provider.GATEWAY, "POST", "/rpc", token=token,
        payload={"jsonrpc": "2.0", "id": "workspace-access-check", "method": "tools/call",
                 "params": {"name": catalog["tools"][0]["observe_tool_name"],
                            "arguments": {"path": "/proof-state/startcontrole.txt"}}})
    if reply.get("error") or reply.get("result", {}).get("isError"):
        raise RuntimeError("workspace access readback failed")
    direct = helper._docker("exec", provider.FILESYSTEM, "python3", "-c",
        "from pathlib import Path;import hashlib;print(hashlib.sha256(Path('/proof-state/startcontrole.txt').read_bytes()).hexdigest())")
    if direct != expected_sha256:
        raise RuntimeError("workspace access control file changed")
    provider.verify_readback(reply.get("result", {}), "Eva's duurzame werkmap is voorbereid.\n", direct)


def prepare(generation: int) -> dict:
    if os.geteuid() != 0 or generation < 2:
        raise RuntimeError("workspace credential preparation requires root and generation >= 2")
    root = provider.HOST_ROOT
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("workspace root permissions differ")
    with exclusive_access(root):
        return _prepare(root, generation)


def _prepare(root: Path, generation: int) -> dict:
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
              "tokenSha256": provider._digest(access / "token"), "catalogSha256": source["catalogSha256"],
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
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.generation)))
        return 0
    except Exception:
        print(json.dumps({"status": "refused_or_incomplete", "error": "Inspect retained private generation state; never delete or retry blindly."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
