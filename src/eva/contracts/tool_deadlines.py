"""Existing execution deadlines shared by preparation and orchestration."""

def maximum_tool_execution_seconds(tool_name: str) -> int:
    return 125 if tool_name == "research.public_sources" else 60
