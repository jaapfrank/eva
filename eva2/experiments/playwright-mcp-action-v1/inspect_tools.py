#!/usr/bin/env python3
"""Print the exact click descriptor from the pinned Playwright MCP sidecar."""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlsplit


ROOT = Path("/candidate")
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from eva.adapters.connectors.mcp import McpStreamableHttpSession
    from eva.contracts.canonical_json import canonical_sha256

    endpoint = sys.argv[1]
    parsed = urlsplit(endpoint)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Playwright MCP did not become reachable")

    session = McpStreamableHttpSession(
        endpoint=endpoint,
        opener=urllib_request.build_opener(urllib_request.ProxyHandler({})),
        deadline=time.monotonic() + 10,
        maximum_request_bytes=8_192,
        maximum_response_bytes=262_144,
        response_progress_timeout_seconds=5,
    )
    initialized = session.request(
        request_id=1,
        method="initialize",
        params={
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "eva2-playwright-tool-inspection", "version": "1"},
        },
        initialization=True,
    )
    session.complete_initialization(initialized)
    session.notify_initialized()
    tools = session.request(request_id=2, method="tools/list", params={})["tools"]
    click = next(tool for tool in tools if tool["name"] == "browser_click")
    print(
        json.dumps(
            {"descriptor": click, "sha256": canonical_sha256(click)},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
