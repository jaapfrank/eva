#!/usr/bin/env python3
"""Run Eva's exact browser adapter from inside the gatewayless test cell."""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path("/candidate")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def wait_for_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.hostname is None or parsed.port is None:
        raise RuntimeError("browser endpoint is invalid")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Playwright MCP did not become reachable inside the cell")


def main() -> None:
    from eva.adapters.connectors.playwright_mcp import (
        PlaywrightMcpReadAdapter,
        PlaywrightMcpReadProfile,
    )

    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_client.py ENDPOINT")
    endpoint = sys.argv[1]
    wait_for_endpoint(endpoint)
    profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    fixture = profile["fixture"]
    result = PlaywrightMcpReadAdapter(
        PlaywrightMcpReadProfile(
            endpoint=endpoint,
            page_url=fixture["url"],
            expected_title=fixture["expectedTitle"],
            required_visible_texts=tuple(fixture["requiredVisibleTexts"]),
            expected_snapshot_sha256=fixture["expectedSnapshotSha256"],
            server_image_digest=profile["image"]["amd64Digest"],
            sandbox_profile_sha256=profile["sandboxProfileSha256"],
        )
    ).read_fixed_page()
    print(
        json.dumps(
            {
                "finalUrl": result.final_url,
                "matchedVisibleTexts": len(result.matched_visible_texts),
                "snapshotSha256": result.snapshot_sha256,
                "toolSequence": list(result.tool_sequence),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
