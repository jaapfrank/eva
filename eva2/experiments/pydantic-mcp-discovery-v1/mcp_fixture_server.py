"""Tiny external MCP fixture for the client-only image boundary proof."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


server = FastMCP(
    "external-capability-fixture",
    instructions="One provider-owned capability used only for a transport smoke test.",
    host="0.0.0.0",
    port=8765,
    stateless_http=True,
    json_response=True,
)


@server.tool(
    description="Return a provider-owned observation without changing provider state."
)
async def observe_external_capability(subject: str) -> dict[str, object]:
    return {"subject": subject, "available": True, "changed": False}


if __name__ == "__main__":
    server.run(transport="streamable-http")
