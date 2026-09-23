"""Lossless presentation of known saved work output."""

import json

def readable_work_result(output: str) -> str:
    """Render the known tool-free output shape without hiding unknown fields."""
    try:
        value = json.loads(output)
    except (ValueError, RecursionError):
        return output
    if (not isinstance(value, dict) or set(value) != {"summary", "findings"}
            or not isinstance(value["summary"], str) or not value["summary"].strip()
            or not isinstance(value["findings"], list)
            or any(not isinstance(item, str) or not item.strip() for item in value["findings"])):
        return output
    findings = "\n".join(f"- {item}" for item in value["findings"])
    return value["summary"] + ("\n\n" + findings if findings else "")

