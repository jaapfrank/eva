"""Exercise Pydantic AI's client-only MCP path against an external provider."""

from __future__ import annotations

import argparse
import asyncio
import json

from pydantic_ai.mcp import MCPToolset


async def run(url: str) -> dict[str, object]:
    toolset = MCPToolset(url, id="external-fixture")
    tools = await toolset.list_tools()
    names = sorted(tool.name for tool in tools)
    result = await toolset.direct_call_tool(
        "observe_external_capability",
        {"subject": "provider-neutral"},
    )
    serialized = str(result)
    return {
        "tool_names": names,
        "call_result_present": "provider-neutral" in serialized,
        "provider_changed_state": '"changed":false' not in serialized.replace(" ", "")
        and "'changed': False" not in serialized,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.url)), sort_keys=True))


if __name__ == "__main__":
    main()
