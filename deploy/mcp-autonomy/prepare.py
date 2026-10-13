"""Patch only exact pinned ContextForge inputs in an offline build directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


INPUTS = {
    "mcpgateway/services/gateway_service.py": ("02e4afac654239672f8687e2374a80933bda6b8279c648764017394973080d83", 3),
    "mcpgateway/services/tool_service.py": ("0c2e856af5c785ef98cd5deeb7a623fa6b7a9ad86a60d82016b5c08741eb378a", 1),
}


def prepare(root: Path) -> dict:
    prepared = {}
    for relative, (digest, count) in INPUTS.items():
        data = (root / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("ContextForge source differs: " + relative)
        source = data.decode()
        needle = "return httpx.AsyncClient("
        if source.count(needle) != count:
            raise ValueError("ContextForge factory anchors differ")
        source = source.replace(needle, "return __import__('mcpgateway.eva_mcp_pinning', fromlist=['mcp_http_client']).mcp_http_client(")
        compile(source, relative, "exec")
        prepared[relative] = source.encode()
    # Validate every input before changing the offline output.
    for relative, data in prepared.items():
        (root / relative).write_bytes(data)
    helper = Path(__file__).with_name("eva_mcp_pinning.py")
    shutil.copyfile(helper, root / "mcpgateway/eva_mcp_pinning.py")
    return {relative: hashlib.sha256(data).hexdigest() for relative, data in prepared.items()} | {
        "mcpgateway/eva_mcp_pinning.py": hashlib.sha256(helper.read_bytes()).hexdigest()}


def seal_guard(
    overlay: Path, *, base_compose: Path, egress_compose: Path,
    environment_file: Path, receipt_path: Path,
    effective_file_sha256: dict[str, str],
) -> dict:
    """Seal an already prepared overlay and its canonical recreate route.

    Pass the file hashes returned by prepare() (or its retained private receipt).
    This writes one new private receipt only; it never changes or restarts Docker.
    Existing preparation receipt.restartCommand must be updated to recreateCommand;
    its former base+egress command is now rollback-only with autonomy disabled.
    """
    expected_targets = {"/app/" + name for name in (*INPUTS, "mcpgateway/eva_mcp_pinning.py")}
    paths = [base_compose, egress_compose, overlay, environment_file]
    if any(not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file() for path in paths):
        raise ValueError("guard Compose inputs must be canonical files")
    document = json.loads(overlay.read_text())
    gateway = document["services"]["gateway"]
    if set(document) != {"services"} or set(document["services"]) != {"gateway"} or set(gateway) != {"environment", "volumes"}:
        raise ValueError("guard overlay contains unrelated changes")
    environment = gateway["environment"]
    if set(environment) != {"EVA_MCP_PINNING_POLICY"} or not isinstance(json.loads(environment["EVA_MCP_PINNING_POLICY"]), dict):
        raise ValueError("guard policy is invalid")
    mounts = gateway["volumes"]
    if (not isinstance(mounts, list) or len(mounts) != 3
            or {mount.get("target") for mount in mounts} != expected_targets
            or set(effective_file_sha256) != {target.removeprefix("/app/") for target in expected_targets}):
        raise ValueError("guard source bindings differ")
    files = []
    for mount in mounts:
        source = Path(mount["source"])
        if (mount != {"type": "bind", "source": str(source), "target": mount["target"],
                      "read_only": True, "bind": {"create_host_path": False}}
                or not source.is_absolute() or source.resolve(strict=True) != source or not source.is_file()):
            raise ValueError("guard source mount differs")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != effective_file_sha256[mount["target"].removeprefix("/app/")]:
            raise ValueError("guard prepared source hash differs")
        files.append({"source": str(source), "target": mount["target"], "sha256": digest})
    prefix = ["/usr/bin/docker", "compose", "--project-name", "eva2-workspace-provider",
              "--env-file", str(environment_file)]
    for path in (base_compose, egress_compose):
        prefix.extend(("--file", str(path)))
    suffix = ["up", "--detach", "--no-deps", "gateway"]
    receipt = {
        "contract": "EvaMcpTransportGuard/v1",
        "upstreamImage": json.loads(Path(__file__).with_name("upstream.json").read_text())["image"],
        "composeFiles": [str(path) for path in (base_compose, egress_compose, overlay)],
        "inputSha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "environmentFile": str(environment_file),
        "policy": environment["EVA_MCP_PINNING_POLICY"],
        "files": sorted(files, key=lambda item: item["target"]),
        "recreateCommand": [*prefix, "--file", str(overlay), *suffix],
        "rollback": {"requiresAutonomyDisabled": True, "command": [*prefix, *suffix]},
    }
    if not receipt_path.is_absolute() or receipt_path.parent.resolve(strict=True) != receipt_path.parent:
        raise ValueError("guard receipt path is not canonical")
    with receipt_path.open("x", encoding="utf-8") as stream:
        os.chmod(receipt_path, 0o600)
        stream.write(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("--seal-overlay", type=Path)
    parser.add_argument("--base-compose", type=Path)
    parser.add_argument("--egress-compose", type=Path)
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--prepared-receipt", type=Path,
                        help="Retained private receipt with effectiveFileSha256 from source preparation")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.seal_overlay:
        if args.root or not all((args.base_compose, args.egress_compose, args.environment_file,
                                 args.prepared_receipt, args.receipt)):
            parser.error("sealing requires all retained Compose inputs and a new receipt path")
        hashes = json.loads(args.prepared_receipt.read_text())["effectiveFileSha256"]
        result = seal_guard(args.seal_overlay, base_compose=args.base_compose,
                            egress_compose=args.egress_compose, environment_file=args.environment_file,
                            receipt_path=args.receipt, effective_file_sha256=hashes)
    else:
        if args.root is None:
            parser.error("a source root or --seal-overlay is required")
        result = prepare(args.root)
    print(json.dumps(result, sort_keys=True))
