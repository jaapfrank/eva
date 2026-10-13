"""Probe existing HTTP and MCP metadata only; never invoke a workspace tool."""
from pathlib import Path
import hashlib
import json
import sys
from urllib.request import Request, build_opener, ProxyHandler


def check():
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:9000/healthz", timeout=2) as response:
        if response.status != 200 or response.read(16) != b"ok":
            raise RuntimeError("HTTP liveness failed")
    binding = hashlib.sha256(Path("/run/eva2/atomic-binding.json").read_bytes()).hexdigest()
    body = json.dumps({"jsonrpc": "2.0", "id": "health", "method": "src/tools/list", "params": {}}).encode()
    request = Request("http://127.0.0.1:9000/mcp", data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2024-11-05"})
    with opener.open(request, timeout=3) as response:
        raw = response.read(32769)
        if response.status != 200 or len(raw) > 32768:
            raise RuntimeError("metadata response bound")
    result = json.loads(raw)
    if result.get("jsonrpc") != "2.0" or result.get("id") != "health" or "error" in result:
        raise RuntimeError("metadata response invalid")
    payload = result["result"]
    tools = payload["tools"]
    if payload.get("nextCursor") or len(tools) != 2 or {tool["name"] for tool in tools} != {"workspace_create_bound", "workspace_observe_bound"}:
        raise RuntimeError("native tools differ")
    for tool in tools:
        if tool["inputSchema"].get("description") != "Provider binding SHA256 " + binding:
            raise RuntimeError("native binding differs")


if __name__ == "__main__":
    try:
        check()
    except Exception:
        print("document provider not ready", file=sys.stderr)
        raise SystemExit(1)
    print("document provider HTTP and native MCP metadata ready")
