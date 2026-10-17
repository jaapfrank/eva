"""Home Assistant receipt interpretation; transport success alone is insufficient."""
import json
from urllib.parse import unquote, urlsplit


def _automation_config_target(arguments):
    """Recognize one existing REST configuration resource, not a device rule."""
    if arguments.get("transport") != "rest" or not isinstance(arguments.get("path"), str):
        return None
    parsed = urlsplit(arguments["path"])
    path = unquote(parsed.path)
    prefix = "/api/config/automation/config/"
    identifier = path[len(prefix):] if path.startswith(prefix) else ""
    if (parsed.scheme or parsed.netloc or parsed.fragment or not identifier
            or "/" in identifier or identifier in {".", ".."}):
        return None
    return path


def _full_automation_readback(arguments, result):
    """A full configuration read is evidence of readback, not semantic equality."""
    if (arguments.get("method") != "GET" or arguments.get("response_pointer") != ""
            or arguments.get("offset") != 0 or urlsplit(arguments["path"]).query
            or result.get("truncated") is not False or "selection_error" in result
            or result.get("next_offset") is not None):
        return False
    try:
        value = json.loads(result["response_json"])
    except (KeyError, TypeError, ValueError):
        return False
    return isinstance(value, dict) and type(result.get("total")) is int and result["total"] == len(value)


def _automation_batch_readbacks(receipt):
    """Accept only full requested configuration rows from the existing reader."""
    arguments, output = receipt.call.arguments, receipt.output
    if (output.get("status") != "verified"
            or arguments.get("section") != "automation_configurations"
            or arguments.get("offset") != 0):
        return set()
    requested = arguments.get("automation_ids")
    inventory = output.get("inventory")
    if not isinstance(requested, list) or not isinstance(inventory, dict):
        return set()
    rows = inventory.get("automations")
    if not isinstance(rows, list):
        return set()
    targets = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("automation_id")
        if (not isinstance(identifier, str) or identifier not in requested
                or row.get("configuration_read") is not True
                or not isinstance(row.get("coverage"), dict)
                or row["coverage"].get("complete") is not True
                or row.get("truncated", False) is not False):
            continue
        try:
            configuration = json.loads(row["configuration_json"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(configuration, dict):
            targets.add("/api/config/automation/config/" + identifier)
    return targets


