"""Patch only exact pinned ContextForge inputs in an offline build directory."""
from __future__ import annotations

import argparse
import hashlib
import json
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root), sort_keys=True))
