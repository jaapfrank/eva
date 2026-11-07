"""Pure wire parsing and presentation for already retained connector results."""

import hashlib
import json
import re

from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256

CONNECTOR_ACTION_NAME = "use_connector_tool"
CONNECTOR_ARGUMENT_MAX_BYTES = 65_536
READER_PREFIX = "eva-stored-result:"
MODEL_INLINE_BOUND = 8192
_RESULT_BOUND = 65536
LEGACY_SOURCE_RESPONSE_FIELDS = ("success", "http_status", "truncated", "selection_error",
                                "request_sent", "response_sha256", "external_state_verified")
SOURCE_RESPONSE_FIELDS = (*LEGACY_SOURCE_RESPONSE_FIELDS,
                          "section", "area_id", "domain", "captured_at", "snapshot_sha256",
                          "offset", "total", "has_more", "next_offset", "selected_fields", "absent_fields", "page_size")


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    output: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate argument key")
        output[key] = value
    return output


def parse_connector_arguments(call: ConversationActionCall) -> tuple[str, dict[str, JsonValue]]:
    if call.name != CONNECTOR_ACTION_NAME or set(call.arguments) != {"tool_id", "arguments_json"}:
        raise ConversationConflict("connector tool call has invalid fields")
    tool_id, raw = call.arguments["tool_id"], call.arguments["arguments_json"]
    if (
        not isinstance(tool_id, str) or not tool_id.strip() or len(tool_id) > 240
        or not isinstance(raw, str) or not 2 <= len(raw.encode("utf-8")) <= CONNECTOR_ARGUMENT_MAX_BYTES
    ):
        raise ConversationConflict("connector tool arguments are invalid")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("arguments must be an object")
        validate_json_value(value)
    except (ValueError, TypeError, RecursionError) as error:
        raise ConversationConflict("connector arguments_json must be one valid JSON object") from error
    return tool_id, value


def _single_selection_schema():
    return {"type": "object", "additionalProperties": False,
            "required": ["pointer", "offset", "limit", "query"],
            "properties": {
                "pointer": {"type": "string", "maxLength": 2048,
                            "description": "JSON pointer to an existing value; empty selects the root, otherwise start with /."},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8192},
                "query": {"type": ["string", "null"], "minLength": 1, "maxLength": 256,
                          "description": "Exact text search from offset; null reads directly."}}}


def _legacy_selection_schema():
    return {"description": "Read one selection, or 1..8 selections from the same saved result. "
            "For a batch the sum of all requested limits must be at most 8192 characters. "
            "The complete output and recorded receipt must each fit 65536 UTF-8 bytes; "
            "reduce selection lengths if needed. No selection invokes an external connector.",
            "oneOf": [_single_selection_schema(),
                {"type": "object", "additionalProperties": False, "required": ["selections"],
                 "properties": {"selections": {"type": "array", "minItems": 1, "maxItems": 8,
                                               "items": _single_selection_schema()}}}]}


def selection_schema():
    schema = _legacy_selection_schema()
    filtered = _single_selection_schema()
    filtered["properties"]["json_filter"] = {
        "type": "object", "additionalProperties": False,
        "required": ["field", "equals"], "properties": {
            "field": {"type": "string", "maxLength": 2048,
                      "description": "JSON pointer within each array item; empty compares the item."},
            "equals": {"type": ["string", "integer", "boolean", "null"], "maxLength": 256}},
        "description": "Select all array items with this exact scalar field value BEFORE character pagination. "
                       "If pointer selects JSON text, decode that text once. Missing fields do not match. "
                       "Use source_items/matched_items and has_more to check completeness; this makes no API call."}
    schema["oneOf"][0] = filtered
    schema["oneOf"][-1]["properties"]["selections"]["items"] = filtered
    schema["description"] += (
        " Optional json_filter selects relevant JSON-array records locally from the COMPLETE saved source, "
        "including arrays stored as JSON text. Offsets/limits still count characters in the selected JSON.")
    return schema


def encode_connector_result(value):
    validate_json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def retained_source_navigation(observation):
    """Expose only bounded navigation already bound by the original receipt.

    This decorates the ephemeral outer model view, never stored manifests or
    source bytes. A nested reader and its pointer must remain paired.
    """
    inner = observation.get("source_response_manifest")
    pointer = observation.get("source_response_pointer")
    guidance = observation.get("source_response_guidance")
    if (not isinstance(inner, dict)
            or inner.get("contract") != "EvaStoredConnectorResult/v1"
            or inner.get("stored_complete") is not True
            or inner.get("external_state_verified") is not False
            or not isinstance(pointer, str) or not pointer.startswith("/")
            or len(pointer) > 512
            or not isinstance(guidance, str) or len(guidance) > 2048):
        return None
    reader = inner.get("reader")
    if (not isinstance(reader, dict) or reader.get("action") != "use_connector_tool"
            or not isinstance(reader.get("tool_id"), str)):
        return None
    match = re.fullmatch(re.escape(READER_PREFIX) + r"[0-9a-f]{64}:([0-9a-f]{64})", reader["tool_id"])
    if match is None or match[1] != inner.get("sha256"):
        return None
    navigation = {"reader": {key: reader[key] for key in ("action", "tool_id")},
                  "pointer": pointer, "guidance": guidance}
    return navigation if len(encode_connector_result(navigation)) <= 4096 else None


def stored_result_manifest(identifier, output, raw, digest):
    reader = READER_PREFIX + identifier + ":" + digest
    return {"contract": "EvaStoredConnectorResult/v1", "stored_complete": True,
        "sha256": digest, "byte_length": len(raw), "external_state_verified": False,
        "preview": raw.decode()[:2048], "has_more": True,
        "reader": {"action": "use_connector_tool", "tool_id": reader,
            "arguments_schema": selection_schema(),
            "arguments_json": json.dumps({"pointer": "/text" if isinstance(output.get("text"), str) else "",
                "offset": 0, "limit": 8192, "query": None}),
            "instructions": "Read saved bytes locally. pointer is a JSON pointer; offset/limit count characters. Optional query searches within the selected value from offset. For multiple passages, use {selections: [...]} with 1..8 selections and sum(limit) <= 8192. No connector is invoked."}}


def retained_action_body(receipt):
    return {"call": {"call_id": receipt.call.call_id, "name": receipt.call.name,
                     "arguments": receipt.call.arguments}, "output": receipt.output}


def ancestor_action_presentation(receipt, projection):
    """Validate a host-produced rendering against the untouched receipt bytes."""
    body = retained_action_body(receipt)
    manifest = projection.get("manifest") if isinstance(projection, dict) else None
    source = projection.get("source") if isinstance(projection, dict) else None
    if (not isinstance(projection, dict) or set(projection) != {"contract", "source", "manifest"}
            or projection["contract"] != "EvaRetainedActionBodyModelView/v1"
            or not isinstance(source, dict) or set(source) != {
                "thread_id", "call_id", "step_index", "observation_key", "receipt_sha256",
                "proof_sha256", "source_ingress_sha256"}
            or source["observation_key"] != "action_body"
            or source["call_id"] != receipt.call.call_id
            or type(source["step_index"]) is not int or source["step_index"] < 0
            or source["receipt_sha256"] != canonical_sha256(receipt.output)
            or source["proof_sha256"] != canonical_sha256(receipt.output.get("proof"))
            or not re.fullmatch(r"[0-9a-f]{64}", str(source["source_ingress_sha256"]))
            or receipt.output.get("status") != "verified"
            or not isinstance(manifest, dict)
            or manifest.get("contract") != "EvaStoredConnectorResult/v1"
            or manifest.get("stored_complete") is not True
            or manifest.get("external_state_verified") is not False
            or manifest.get("sha256") != hashlib.sha256(encode_connector_result(body)).hexdigest()
            or manifest.get("byte_length") != len(encode_connector_result(body))
            or manifest.get("preview") != "" or manifest.get("preview_omitted") is not True
            or not isinstance(manifest.get("reader"), dict)
            or not re.fullmatch(re.escape(READER_PREFIX)+r"[0-9a-f]{64}:"+manifest["sha256"],
                                str(manifest["reader"].get("tool_id", "")))
            or manifest["reader"].get("action") != "use_connector_tool"
            or manifest["reader"].get("arguments_schema") not in (
                _legacy_selection_schema(), selection_schema())):
        raise ConversationConflict("retained action body projection differs")
    identifier = manifest["reader"]["tool_id"][len(READER_PREFIX):].split(":", 1)[0]
    expected = stored_result_manifest(identifier, body, encode_connector_result(body), manifest["sha256"])
    # Old immutable receipts retain the exact schema with which they were sealed.
    expected["reader"]["arguments_schema"] = manifest["reader"]["arguments_schema"]
    expected.update(preview="", preview_omitted=True)
    if manifest != expected:
        raise ConversationConflict("retained action body manifest differs")
    # Keep the semantic request and selector visible. Only a large document
    # content field needs a retained-byte reference; raw call arguments stay intact.
    arguments = receipt.call.arguments
    content = arguments.get("content")
    if (receipt.call.name == "save_workspace_document" and isinstance(content, str)
            and len(content.encode("utf-8")) > MODEL_INLINE_BOUND):
        content_bytes = content.encode("utf-8")
        arguments = {**arguments, "content": {
            "model_view_encoding": "retained_document_content_v1",
            "original_content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "byte_length": len(content_bytes),
            "reader_tool_id": manifest["reader"]["tool_id"],
            "pointer": "/call/arguments/content"}}
    if (receipt.call.name == "use_connector_tool"
            and isinstance(arguments.get("arguments_json"), str)):
        try:
            nested = json.loads(arguments["arguments_json"])
        except (TypeError, ValueError):
            nested = None
        content = nested.get("content") if isinstance(nested, dict) else None
        proof = receipt.output.get("proof")
        expected, observed = (proof.get("expected"), proof.get("observed")) if isinstance(proof, dict) else (None, None)
        if (isinstance(content, str) and len(content.encode("utf-8")) > MODEL_INLINE_BOUND
                and isinstance(expected, dict) and expected == observed
                and expected.get("content") == content):
            # The same proven document write can arrive through connector
            # discovery. Preserve its selector and all other fields; its exact
            # original JSON string remains readable through the bound body.
            content_bytes = content.encode("utf-8")
            arguments = {**arguments, "arguments_json": json.dumps({**nested,
                "content": {"model_view_encoding": "retained_document_content_v1",
                    "original_content_sha256": hashlib.sha256(content_bytes).hexdigest(),
                    "byte_length": len(content_bytes),
                    "reader_tool_id": manifest["reader"]["tool_id"],
                    "pointer": "/call/arguments/arguments_json",
                    "source_encoding": "json_string_v1", "content_key": "content"}},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"))}
    if receipt.call.name == "use_connector_tool" and receipt.output.get("source") == "stored_connector_result":
        # A local page is already bounded. Keep its text and next offset visible;
        # wrapping it again would force the model to reread what it just read.
        reader, _ = parse_connector_arguments(receipt.call)
        match = re.fullmatch(re.escape(READER_PREFIX) + r"[0-9a-f]{64}:([0-9a-f]{64})", reader)
        result = receipt.output.get("result")
        pages = ([result] if isinstance(result, dict) and result.get("contract") == "EvaStoredConnectorPage/v1"
                 else result.get("pages") if isinstance(result, dict) and result.get("contract") == "EvaStoredConnectorPages/v1" else None)
        if (match is None or not isinstance(pages, list) or not 1 <= len(pages) <= 8
                or receipt.output.get("external_state_verified") is not False
                or result.get("sha256") != match[1] or result.get("external_state_verified") is not False
                or len(encode_connector_result(receipt.output)) > _RESULT_BOUND
                or any(not isinstance(page, dict) or page.get("contract") != "EvaStoredConnectorPage/v1"
                       or page.get("sha256") != match[1] or page.get("external_state_verified") is not False
                       or not isinstance(page.get("text"), str)
                       or type(page.get("next_offset")) is not int or type(page.get("offset")) is not int
                       or page["next_offset"] != page["offset"] + len(page["text"]) for page in pages)
                or sum(len(page["text"]) for page in pages) > 8192):
            raise ConversationConflict("retained local page contract or bound differs")
        return arguments, receipt.output
    output = {key: receipt.output[key] for key in (
        "contract", "durable", "status", "source", "operation_kind", "external_state_verified")
        if key in receipt.output}
    # The full manifest/source above remains the validation and raw-read boundary.
    # The older model view need not repeat its audit fields or reader instructions.
    reader = {key: value for key, value in manifest["reader"].items()
              if key not in {"arguments_json", "instructions"}}
    model_manifest = {key: value for key, value in manifest.items()
                      if key not in {"has_more", "preview", "preview_omitted"}}
    output.update(retained_action_body={**model_manifest, "reader": reader},
        model_view_encoding="retained_ancestor_action_body_v1",
        source_receipt_sha256=source["receipt_sha256"])
    for key in ("result", "inventory"):
        observation = receipt.output.get(key)
        if isinstance(observation, dict):
            output["source_response"] = {k: observation[k] for k in SOURCE_RESPONSE_FIELDS if k in observation}
    return arguments, output

