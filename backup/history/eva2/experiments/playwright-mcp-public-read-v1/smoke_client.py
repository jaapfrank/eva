#!/usr/bin/env python3
"""Use exactly two Playwright MCP tools against one release-owned public URL."""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


ROOT = Path("/candidate")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def wait_for_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    require(parsed.hostname is not None and parsed.port is not None, "invalid browser endpoint")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Playwright MCP did not become reachable inside the cell")


def text_result(result: object, *, stage: str) -> str:
    require(isinstance(result, dict) and result.get("isError") is not True, f"{stage} failed")
    content = result.get("content")
    require(
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and set(content[0]) == {"type", "text"}
        and content[0]["type"] == "text"
        and isinstance(content[0]["text"], str),
        f"{stage} returned unsupported content",
    )
    return content[0]["text"]


def page_header(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    require(len(lines) >= 4 and lines[0] == "### Page", "missing page header")
    require(lines[1].startswith("- Page URL: "), "missing page URL")
    require(lines[2].startswith("- Page Title: "), "missing page title")
    require(lines[3] == "### Snapshot", "missing page snapshot marker")
    return lines[1][12:], lines[2][14:]


def main() -> None:
    from eva.adapters.connectors.mcp import McpStreamableHttpSession
    from eva.adapters.connectors.playwright_mcp import (
        PLAYWRIGHT_MCP_AMD64_DIGEST,
        PLAYWRIGHT_NAVIGATE_TOOL_SHA256,
        PLAYWRIGHT_SERVER_VERSION,
        PLAYWRIGHT_SNAPSHOT_TOOL_SHA256,
    )
    from eva.contracts.canonical_json import canonical_sha256

    require(len(sys.argv) == 2, "usage: smoke_client.py ENDPOINT")
    endpoint = sys.argv[1]
    profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    target = profile["target"]
    require(profile["image"]["amd64Digest"] == PLAYWRIGHT_MCP_AMD64_DIGEST, "image drift")
    wait_for_endpoint(endpoint)
    session = McpStreamableHttpSession(
        endpoint=endpoint,
        opener=build_opener(ProxyHandler({})),
        deadline=time.monotonic() + 15,
        maximum_request_bytes=8192,
        maximum_response_bytes=262144,
        response_progress_timeout_seconds=5.0,
    )
    initialized = session.request(
        request_id=1,
        method="initialize",
        params={"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "eva2-public-read", "version": "1"}},
        initialization=True,
    )
    require(isinstance(initialized, dict), "invalid initialization")
    require(initialized.get("protocolVersion") == "2025-06-18", "protocol drift")
    require(initialized.get("serverInfo") == {"name": "Playwright", "version": PLAYWRIGHT_SERVER_VERSION}, "server drift")
    session.complete_initialization(initialized)
    session.notify_initialized()
    tools = session.request(request_id=2, method="tools/list", params={})
    require(isinstance(tools, dict) and isinstance(tools.get("tools"), list), "invalid tool catalog")
    expected = {
        "browser_navigate": PLAYWRIGHT_NAVIGATE_TOOL_SHA256,
        "browser_snapshot": PLAYWRIGHT_SNAPSHOT_TOOL_SHA256,
    }
    for name, expected_hash in expected.items():
        matches = [tool for tool in tools["tools"] if isinstance(tool, dict) and tool.get("name") == name]
        require(len(matches) == 1 and canonical_sha256(matches[0]) == expected_hash, f"tool drift: {name}")
    navigate = session.request(
        request_id=3,
        method="tools/call",
        params={"name": "browser_navigate", "arguments": {"url": target["url"]}},
    )
    navigate_text = text_result(navigate, stage="navigation")
    navigate_url, navigate_title = page_header(navigate_text)
    require((navigate_url, navigate_title) == (target["url"], target["expectedTitle"]), "navigation target drift")
    snapshot = session.request(
        request_id=4,
        method="tools/call",
        params={"name": "browser_snapshot", "arguments": {"depth": 8}},
    )
    snapshot_text = text_result(snapshot, stage="snapshot")
    final_url, title = page_header(snapshot_text)
    require((final_url, title) == (target["url"], target["expectedTitle"]), "snapshot target drift")
    for expected_text in target["requiredVisibleTexts"]:
        require(expected_text in snapshot_text, "required public text missing")
    print(json.dumps({
        "finalUrl": final_url,
        "snapshotSha256": hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest(),
        "title": title,
        "toolSequence": ["browser_navigate", "browser_snapshot"],
    }, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
