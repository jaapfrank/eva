#!/usr/bin/env python3
"""Inspect the sealed fixture snapshot and one real click during development."""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import time
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlsplit


ROOT = Path("/candidate")
sys.path.insert(0, str(ROOT / "src"))


def _text(result: object) -> str:
    assert isinstance(result, dict)
    content = result["content"]
    assert isinstance(content, list) and len(content) == 1
    return content[0]["text"]


def main() -> None:
    from eva.adapters.connectors.mcp import McpStreamableHttpSession

    endpoint = sys.argv[1]
    page_url = sys.argv[2]
    parsed = urlsplit(endpoint)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    session = McpStreamableHttpSession(
        endpoint=endpoint,
        opener=urllib_request.build_opener(urllib_request.ProxyHandler({})),
        deadline=time.monotonic() + 15,
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
            "clientInfo": {"name": "eva2-playwright-action-inspection", "version": "1"},
        },
        initialization=True,
    )
    session.complete_initialization(initialized)
    session.notify_initialized()
    navigate = _text(
        session.request(
            request_id=2,
            method="tools/call",
            params={"name": "browser_navigate", "arguments": {"url": page_url}},
        )
    )
    before = _text(
        session.request(
            request_id=3,
            method="tools/call",
            params={"name": "browser_snapshot", "arguments": {"depth": 8}},
        )
    )
    clicked = _text(
        session.request(
            request_id=4,
            method="tools/call",
            params={
                "name": "browser_click",
                "arguments": {
                    "element": "Bevestig Eva testactie",
                    "target": "e6",
                },
            },
        )
    )
    print(
        json.dumps(
            {
                "before": before,
                "beforeSha256": hashlib.sha256(before.encode()).hexdigest(),
                "click": clicked,
                "clickSha256": hashlib.sha256(clicked.encode()).hexdigest(),
                "navigateSha256": hashlib.sha256(navigate.encode()).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
