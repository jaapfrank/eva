"""Pure interpretation of retained Home Assistant receipts, independent of enablement.

Loading this module grants no tool access and performs no external calls. Installed
but disabled providers can still interpret immutable history. Without this module,
callers keep the literal original receipt instead of inventing a replacement.
"""
from __future__ import annotations

import hashlib
import json
import re

from eva.contracts.canonical_json import canonical_json_bytes


OBSERVATION_ACTION_NAMES = frozenset({"home_assistant_request", "read_home_assistant_inventory"})


def receipt_presentation(receipt, view):
    from eva.adapters.postgres.connector_results import (
        _encoded, READER_PREFIX, SOURCE_RESPONSE_FIELDS, LEGACY_SOURCE_RESPONSE_FIELDS,
        retained_source_navigation,
    )
    output = receipt.output
    metadata = view.get("model_projection")
    proof = output.get("proof")
    key = metadata.get("observation_key") if isinstance(metadata, dict) else None
    observation = output.get(key) if key in {"result", "inventory"} else None
    manifest = view.get(key) if key in {"result", "inventory"} else None
    if (output.get("contract") != "EvaConversationActionReceipt/v1"
            or output.get("durable") is not True or output.get("status") != "verified"
            or receipt.call.name not in {"home_assistant_request", "read_home_assistant_inventory"}
            or not isinstance(proof, dict) or not isinstance(observation, dict)
            or proof.get("evidence", {}).get("observation") != observation
            or not isinstance(manifest, dict)
            or manifest.get("contract") != "EvaStoredConnectorResult/v1"
            or manifest.get("stored_complete") is not True
            or manifest.get("external_state_verified") is not False
            or manifest.get("sha256") != hashlib.sha256(_encoded(observation)).hexdigest()
            or manifest.get("byte_length") != len(_encoded(observation))
            or manifest.get("source_output_truncated") != (observation.get("truncated") is True)
            or manifest.get("source_response") not in (
                {key: observation[key] for key in SOURCE_RESPONSE_FIELDS if key in observation},
                {key: observation[key] for key in LEGACY_SOURCE_RESPONSE_FIELDS if key in observation},
            )
            or ("source_navigation" in manifest and (
                retained_source_navigation(observation) is None
                or manifest["source_navigation"] != retained_source_navigation(observation)))
            or not isinstance(manifest.get("reader"), dict)
            or manifest["reader"].get("action") != "use_connector_tool"
            or not str(manifest["reader"].get("tool_id", "")).startswith(READER_PREFIX)):
        raise ValueError("retained action model projection is invalid")
    expected_metadata = {"contract": "EvaRetainedActionModelView/v1",
        "source_receipt_sha256": hashlib.sha256(canonical_json_bytes(output)).hexdigest(),
        "source_proof_sha256": hashlib.sha256(canonical_json_bytes(proof)).hexdigest(),
        "observation_key": key}
    reference = {"model_view_reference": "/" + key,
        "observation_sha256": hashlib.sha256(canonical_json_bytes(observation)).hexdigest(),
        "durable_proof_sha256": expected_metadata["source_proof_sha256"]}
    expected = {**output, key: manifest,
        "proof": {**proof, "evidence": {**proof["evidence"], "observation": reference}},
        "model_projection": expected_metadata}
    if view != expected or len(canonical_json_bytes(view)) >= len(canonical_json_bytes(output)):
        raise ValueError("retained action model projection differs from original receipt")
    return view


def checkpoint_source_read(receipt):
    """Return the proven observation key and source metadata, or decline it."""
    output, arguments = receipt.output, receipt.call.arguments
    if (output.get("contract") != "EvaConversationActionReceipt/v1"
            or output.get("durable") is not True or output.get("status") != "verified"
            or output.get("external_state_verified") is True):
        return None
    if receipt.call.name == "home_assistant_request":
        # These exact commands already back the read-only HA inventory.
        # GET on a WebSocket request alone says nothing about its effects.
        if arguments.get("method") != "GET":
            return None
        if arguments.get("transport") == "rest":
            if not str(arguments.get("path", "")).startswith("/api/"):
                return None
        elif arguments.get("transport") == "websocket":
            try:
                command = json.loads(arguments.get("body_json", ""))
            except (TypeError, ValueError):
                return None
            if (not isinstance(command, dict) or command.get("type") not in {
                "get_states", "get_config", "config_entries/get",
                "config/area_registry/list", "config/device_registry/list",
                "config/entity_registry/list", "hacs/repositories/list",
                # The upstream handlers return saved config/entries; their
                # save/update/remove commands remain separate and excluded.
                "lovelace/config", "lovelace/dashboards/list",
                "config/entity_registry/get_entries",
            }):
                return None
        else:
            return None
        key = "result"
        if not isinstance(output.get(key), dict) or output[key].get("success") is not True:
            return None
    elif receipt.call.name == "read_home_assistant_inventory":
        key = "inventory"
    else:
        return None
    observation, proof = output.get(key), output.get("proof")
    if (not isinstance(observation, dict) or not isinstance(proof, dict)
            or proof.get("evidence", {}).get("observation") != observation
            or observation.get("external_state_verified") is True
            or any(observation.get(k) for k in ("error", "errors", "selection_error"))):
        return None
    if key == "inventory":
        coverage = observation.get("coverage")
        if (isinstance(coverage, dict) and coverage.get("complete") is False
                or observation.get("missing")):
            return None
        for row in observation.get("automations", ()):
            if (not isinstance(row, dict)
                    or row.get("configuration_read") is False
                    or isinstance(row.get("coverage"), dict) and row["coverage"].get("complete") is False
                    or row.get("reason")):
                return None
    from eva.adapters.postgres.connector_results import SOURCE_RESPONSE_FIELDS
    metadata = {k: observation[k] for k in SOURCE_RESPONSE_FIELDS if k in observation}
    for k in ("total", "next_offset", "missing", "coverage", "requested",
              "configurations_read", "configurations_requested"):
        if k in observation:
            metadata[k] = observation[k]
    return key, metadata


def historical_read_error_proof(admission, terminal_event_type):
    """Classify exact old read admissions; never retry work or infer success."""
    action_name = admission.get("action_name")
    arguments = admission.get("action_arguments")
    receipt_output = admission.get("receipt_output")
    proof = None
    # Retained household reads from older releases can end before dispatch
    # without a typed proof. The admitted host tool is read-only; authorize
    # every fresh choice normally and never turn the old failure into success.
    if (proof is None and terminal_event_type == "RECOVERY_FAILED"
            and action_name == "read_home_assistant_inventory"
            and receipt_output.get("provider") == "home_assistant"
            and admission.get("request_tool_name") == action_name
            and admission.get("request_arguments") == arguments
            and admission.get("request_expected_proof") == arguments
            and admission.get("observation_output_key") == "inventory"):
        from eva.adapters.connectors.home_assistant_admin import HomeAssistantHouseholdTool
        HomeAssistantHouseholdTool._validate(arguments)
        proof = {"evidence": {"source": "home_assistant_inventory_read_error",
            "operation_kind": "read", "classification": "historical_host_admission",
            "error": {"messages": ["The earlier household read failed; its recorded cause is unknown. Obtain a fresh observation before completing this assignment."]}}}
    # A failed REST GET remains a failed read, not an uncertain mutation.
    # Preserve its terminal event and let the currently authorized worker
    # choose a fresh indexed read. Never infer a result from its old dispatch.
    if (proof is None and terminal_event_type in {
                "RECOVERY_FAILED", "STATE_VERIFICATION_TIMED_OUT"}
            and action_name == "home_assistant_request"
            and arguments.get("transport") == "rest"
            and arguments.get("method") == "GET"
            and arguments.get("body_json") == ""
            and admission.get("request_tool_name") == "home_assistant.api.request"
            and admission.get("request_arguments") == arguments
            and admission.get("request_expected_proof") == {}
            and admission.get("observation_output_key") == "result"
            and receipt_output == {"operation_kind": "invoke", "external_state_verified": False}
            and re.fullmatch(r"[0-9a-f]{64}", str(admission.get("request_tool_configuration_sha256", "")))):
        proof = {"evidence": {"source": "home_assistant_api_read_error",
            "operation_kind": "read", "classification": "historical_host_admission",
            "error": {"messages": ["The earlier REST GET has no verified result. Its failure remains recorded. Choose a fresh permitted read if this assignment still needs that information; no old write is retried."]}}}
    return proof


def recoverable_read_error(status, evidence):
    return (isinstance(evidence, dict)
            and (status == "failed" or (status == "timed_out"
                 and evidence.get("source") == "home_assistant_api_read_error"))
            and evidence.get("source") in {"home_assistant_inventory_read_error", "home_assistant_api_read_error"}
            and evidence.get("operation_kind") == "read")


def retained_source_observation(action_name, arguments):
    """Classify original admitted reads without demanding successful HTTP data.

    Historical initiative outcomes expose failure/partial-selection flags as
    evidence too. This deliberately does not use the stricter checkpoint filter.
    """
    if action_name == "read_home_assistant_inventory":
        return "inventory", None
    if (action_name == "home_assistant_request" and arguments.get("transport") == "rest"
            and arguments.get("method") == "GET"):
        return "result", None
    return None, "only_retained_rest_get_or_inventory"


def retained_source_attention_selectors(action_name, arguments, observation):
    """Only IDs from an actual read target/page, never query prose or a write."""
    import json
    import re
    if not isinstance(observation, dict):
        return ()
    entities = []
    if (action_name == "home_assistant_request" and arguments.get("transport") == "rest"
            and arguments.get("method") == "GET" and observation.get("success") is True
            and observation.get("http_status") == 200 and not observation.get("selection_error")):
        path = arguments.get("path", "")
        exact = re.fullmatch(r"/api/states/([a-z][a-z0-9_]*\.[a-zA-Z0-9_]+)", path)
        if exact:
            entities = [exact.group(1)]
        elif path == "/api/states" and observation.get("truncated") is False:
            try:
                page = json.loads(observation.get("response_json", ""))
            except (TypeError, ValueError):
                return ()
            if isinstance(page, list):
                entities = [v.get("entity_id") for v in page if isinstance(v, dict)]
    elif (action_name == "read_home_assistant_inventory" and arguments.get("section") == "entities"
            and observation.get("section") == "entities" and isinstance(observation.get("items"), list)):
        entities = [v.get("entity_id") for v in observation["items"] if isinstance(v, dict)]
    selected = []
    for entity in entities:
        if (not isinstance(entity, str) or len(entity) > 255
                or re.fullmatch(r"[a-z][a-z0-9_]*\.[a-zA-Z0-9_]+", entity) is None):
            continue
        value = {"source": "home_assistant", "event_type": "state_changed",
                 "selector_key": "entity_id", "selector_value": entity}
        if value not in selected:
            selected.append(value)
        if len(selected) == 4:
            break
    return tuple(selected)
