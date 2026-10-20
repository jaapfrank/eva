"""Resolve one service target from current HA metadata inside one invocation.

This is provider plumbing, not language routing. The caller still chooses the
service and fields; unknown or ambiguous requests never invoke a service.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from eva.application.cancellation import check_cancellation
from eva.application.conversation import ConversationConflict


class InvalidTargetQuery(ConversationConflict):
    """Correctable composite shape, rejected before admission or transport."""


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def check_deadline(deadline):
    check_cancellation()
    if time.monotonic() >= deadline:
        raise TimeoutError("Home Assistant service query deadline expired")


def service_path(arguments, body):
    parsed = urlsplit(arguments["path"])
    match = re.fullmatch(r"/api/services/([a-z][a-z0-9_]*)/([a-z][a-z0-9_]*)", parsed.path)
    if (arguments["transport"] != "rest" or arguments["method"] != "POST"
            or parsed.query != "return_response" or match is None
            or not isinstance(body, dict)
            or {"entity_id", "target", "area_id", "device_id", "floor_id", "label_id"} & set(body)):
        raise InvalidTargetQuery("target_query requires one response service without a caller target")
    return match.groups()


def _domains(definition):
    target = definition.get("target")
    if not isinstance(target, dict) or set(target) != {"entity"}:
        return None
    selectors = target["entity"]
    if not isinstance(selectors, list) or not selectors or len(selectors) > 16:
        return None
    domains = set()
    for selector in selectors:
        # More complex selectors remain available through the ordinary API.
        if not isinstance(selector, dict) or set(selector) != {"domain"}:
            return None
        value = selector["domain"]
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not values or any(
                not isinstance(v, str) or re.fullmatch(r"[a-z][a-z0-9_]*", v) is None for v in values):
            return None
        domains.update(values)
    return domains


def _selector_matches(selector, row):
    domains = selector["domain"]
    if isinstance(domains, str):domains = [domains]
    if row["entity_id"].partition(".")[0] not in domains:
        return False
    features = selector.get("supported_features")
    if features is None:return True
    actual = row.get("attributes", {}).get("supported_features")
    return type(actual) is int and any(actual & feature == feature for feature in features)


def resolve_service_query(tool, arguments, body, deadline):
    domain, service = service_path(arguments, body)
    query = arguments["target_query"].strip().casefold()
    def rejected(code, **details):
        return None, {"success": False, "service_request_sent": False,
            "external_state_verified": False, "error_code": code,
            "guidance": "No service was invoked. Use the returned evidence to clarify or correct this request.",
            "discovery": {"service": domain + "." + service, **details}}

    def read(socket, identifier, kind):
        check_deadline(deadline)
        socket.send(json.dumps({"id": identifier, "type": kind}))
        frame = tool.admin._decode(socket.recv(timeout=min(8, max(.001, deadline-time.monotonic()))))
        check_deadline(deadline)
        if frame.get("id") != identifier or frame.get("type") != "result" or frame.get("success") is not True:
            raise RuntimeError("Home Assistant service discovery failed")
        return frame.get("result")

    check_deadline(deadline)
    with tool.admin._session() as socket:
        services = read(socket, 1, "get_services")
        definition = services.get(domain, {}).get(service) if isinstance(services, dict) else None
        if not isinstance(definition, dict) or definition.get("response") != {"optional": False}:
            return rejected("mandatory_response_service_unavailable")
        # Supported-feature masks are an OR of allowed alternatives, as in HA.
        target = definition.get("target")
        if not isinstance(target, dict) or set(target) != {"entity"}:
            return rejected("unsupported_service_target")
        selectors = target["entity"]
        if not isinstance(selectors, list) or not 1 <= len(selectors) <= 16:
            return rejected("unsupported_service_target")
        stripped = []
        for selector in selectors:
            if not isinstance(selector, dict) or not set(selector) <= {"domain", "supported_features"} or "domain" not in selector:
                return rejected("unsupported_service_target")
            features = selector.get("supported_features")
            if features is not None and (not isinstance(features, list) or not features
                    or any(type(v) is not int or v <= 0 for v in features)):
                return rejected("unsupported_service_target")
            stripped.append({"domain": selector["domain"]})
        if _domains({"target": {"entity": stripped}}) is None:
            return rejected("unsupported_service_target")
        fields = definition.get("fields")
        if not isinstance(fields, dict) or set(body)-set(fields):
            return rejected("service_fields_differ", definition_json=json.dumps(definition, ensure_ascii=False)[:8000])
        for key, field in fields.items():
            if not isinstance(field, dict):return rejected("unsupported_service_fields")
            if field.get("required") is True and key not in body:
                return rejected("required_service_field_missing", definition_json=json.dumps(definition, ensure_ascii=False)[:8000])
            select = field.get("selector", {}).get("select") if isinstance(field.get("selector", {}), dict) else None
            if key in body and isinstance(select, dict) and select.get("custom_value", False) is not True:
                options = select.get("options")
                if not isinstance(options, list):return rejected("unsupported_service_fields")
                options = [v.get("value") if isinstance(v, dict) else v for v in options]
                values = body[key] if select.get("multiple") is True else [body[key]]
                if not isinstance(values, list) or any(v not in options for v in values):
                    return rejected("service_field_value_invalid", definition_json=json.dumps(definition, ensure_ascii=False)[:8000])
        states = read(socket, 2, "get_states")
    if not isinstance(states, list) or len(states) > 20000:
        raise RuntimeError("Home Assistant state discovery is invalid")
    candidates = []
    seen = set()
    for row in states:
        if not isinstance(row, dict) or not isinstance(row.get("entity_id"), str):
            raise RuntimeError("Home Assistant state identity is invalid")
        identifier = row["entity_id"]
        if identifier in seen:raise RuntimeError("Home Assistant state identity is duplicated")
        seen.add(identifier)
        if not any(_selector_matches(s, row) for s in selectors):continue
        name = row.get("attributes", {}).get("friendly_name", "")
        if not isinstance(name, str):name = ""
        if query != "*" and query not in {identifier.casefold(), identifier.partition(".")[2].casefold(), name.casefold()}:
            continue
        candidates.append((row, name))
    if len(candidates) != 1:
        return rejected("target_absent_or_ambiguous", candidate_count=len(candidates),
            candidates=[{"entity_id": r["entity_id"], "name": n} for r,n in candidates[:8]])
    row, name = candidates[0]
    if row.get("state") in {None, "unknown", "unavailable"}:
        return rejected("target_unavailable", entity_id=row["entity_id"], name=name)
    check_deadline(deadline)
    resolved = {**arguments, "target_query": "", "body_json": json.dumps({**body, "entity_id": row["entity_id"]})}
    discovery = {"entity_id": row["entity_id"], "name": name,
        "service": domain + "." + service, "service_definition_sha256": _sha(definition),
        "entity_catalog_sha256": _sha(states), "captured_at": datetime.now(UTC).isoformat(),
        "guidance": "This entity label is source evidence, not proof of a precise town. No service result proves physical state."}
    return resolved, discovery
