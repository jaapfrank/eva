"""Bounded views of captured MCP output on the existing operation timeline."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from dataclasses import asdict
import base64
import hashlib
import json
import re

from eva.application.background_execution import background_execution_namespace
from eva.application.conversation import ConversationConflict
from eva.contracts.base import validate_json_value
from eva.contracts.canonical_json import canonical_sha256

READER_PREFIX = "eva-stored-result:"
_BOUND = 65536
MODEL_INLINE_BOUND = 8192
_TRANSPORT_BOUND = 2097152
_current = ContextVar("connector_result_capture", default=None)
LEGACY_SOURCE_RESPONSE_FIELDS = ("success", "http_status", "truncated", "selection_error",
                                "request_sent", "response_sha256", "external_state_verified")
SOURCE_RESPONSE_FIELDS = (*LEGACY_SOURCE_RESPONSE_FIELDS,
                          "section", "area_id", "domain", "captured_at", "snapshot_sha256",
                          "offset", "total", "has_more", "next_offset", "selected_fields", "absent_fields", "page_size")


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
    return navigation if len(_encoded(navigation)) <= 4096 else None


class ConnectorSelectionError(ConversationConflict):
    """A local selection can be corrected without repeating an external action."""


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


def _encoded(value):
    validate_json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@contextmanager
def result_capture(capture):
    token = _current.set(capture)
    try:
        yield
    finally:
        _current.reset(token)


def captured_call(command, phase, call):
    capture = _current.get()
    return call() if capture is None else capture.call(command, phase, call)


def recovered_result(command, phase):
    capture = _current.get()
    if capture is None:
        return None
    identifier = canonical_sha256(capture._binding(command, phase))
    if not capture.timeline.events("connector-result:" + identifier):
        return None
    return json.loads(capture._bytes(identifier)[0])


def bounded_result(command, phase, output):
    if len(_encoded(output)) <= _BOUND:
        return output
    capture = _current.get()
    if capture is None:
        raise RuntimeError(f"MCP gateway {phase} result exceeds 65536 bytes; request a smaller result")
    return capture.manifest(command, phase, output)


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
            or manifest.get("sha256") != hashlib.sha256(_encoded(body)).hexdigest()
            or manifest.get("byte_length") != len(_encoded(body))
            or manifest.get("preview") != "" or manifest.get("preview_omitted") is not True
            or not isinstance(manifest.get("reader"), dict)
            or not re.fullmatch(re.escape(READER_PREFIX)+r"[0-9a-f]{64}:"+manifest["sha256"],
                                str(manifest["reader"].get("tool_id", "")))
            or manifest["reader"].get("action") != "use_connector_tool"
            or manifest["reader"].get("arguments_schema") not in (
                _legacy_selection_schema(), selection_schema())):
        raise ConversationConflict("retained action body projection differs")
    identifier = manifest["reader"]["tool_id"][len(READER_PREFIX):].split(":", 1)[0]
    helper = object.__new__(ConnectorResults)
    helper._bytes = lambda _: (_encoded(body), manifest["sha256"])
    expected = helper._manifest(identifier, body)
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
        from eva.providers.mcp_discovery import _arguments
        reader, _ = _arguments(receipt.call)
        match = re.fullmatch(re.escape(READER_PREFIX) + r"[0-9a-f]{64}:([0-9a-f]{64})", reader)
        result = receipt.output.get("result")
        pages = ([result] if isinstance(result, dict) and result.get("contract") == "EvaStoredConnectorPage/v1"
                 else result.get("pages") if isinstance(result, dict) and result.get("contract") == "EvaStoredConnectorPages/v1" else None)
        if (match is None or not isinstance(pages, list) or not 1 <= len(pages) <= 8
                or receipt.output.get("external_state_verified") is not False
                or result.get("sha256") != match[1] or result.get("external_state_verified") is not False
                or len(_encoded(receipt.output)) > _BOUND
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


class ConnectorResults:
    def __init__(self, timeline, turn, authorize):
        self.timeline, self.turn, self.authorize = timeline, turn, authorize
        self.owner = canonical_sha256({"tenant": turn.tenant_id, "installation": turn.installation_id,
            "principal": turn.principal_id, "conversation": turn.conversation_id,
            "message": turn.message_id, "audience": asdict(turn.audience),
            "execution": background_execution_namespace(turn)})

    def _binding(self, command, phase):
        return {"owner": self.owner, "operation": command.operation_id,
            "configuration": command.tool_configuration_sha256, "phase": phase,
            "tool": command.tool_name, "arguments": command.arguments}

    def _events(self, identifier):
        self.authorize(self.turn)
        thread = "connector-result:" + identifier
        self.timeline.load(thread)
        events = self.timeline.events(thread)
        if not events or events[0].event_type != "CONNECTOR_CAPTURE_STARTED":
            raise ConversationConflict("stored connector result is absent")
        binding = events[0].payload["data"]
        if binding.get("owner") != self.owner or canonical_sha256(binding) != identifier:
            raise ConversationConflict("stored connector result owner or request differs")
        return events

    def _bytes(self, identifier):
        events = self._events(identifier)
        if events[-1].event_type != "CONNECTOR_CAPTURE_SEALED":
            raise ConversationConflict("connector outcome uncertain; automatic invocation replay refused")
        parts = []
        for index, event in enumerate(events[1:-1]):
            data = event.payload["data"]
            if event.event_type != "CONNECTOR_CAPTURE_CHUNK" or data.get("index") != index:
                raise ConversationConflict("stored connector result chunks differ")
            parts.append(base64.b64decode(data["bytes"], validate=True))
        raw = b"".join(parts)
        seal = events[-1].payload["data"]
        if (len(raw) != seal.get("length") or len(raw) > _TRANSPORT_BOUND
                or hashlib.sha256(raw).hexdigest() != seal.get("sha256")):
            raise ConversationConflict("stored connector result hash differs")
        binding = events[0].payload["data"]
        if "retained_source" in binding and raw != self._retained_bytes(binding["retained_source"]):
            raise ConversationConflict("stored result differs from retained action source")
        return raw, seal["sha256"]

    def _retained_bytes(self, source):
        """Recheck the original proof, not a new external observation."""
        from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        if (not isinstance(source, dict) or set(source) != {
                "thread_id", "call_id", "step_index", "observation_key", "receipt_sha256", "proof_sha256",
                "source_ingress_sha256"}
                or type(source.get("step_index")) is not int or source["step_index"] < 0
                or source.get("observation_key") not in {"result", "inventory", "action_body"}
                or source.get("source_ingress_sha256") != canonical_sha256(
                    PostgresConversationJournal._ingress_data(self.turn))):
            raise ConversationConflict("retained read source is malformed")
        self.authorize(self.turn)
        events = self.timeline.events(source["thread_id"])
        if source["observation_key"] == "action_body":
            return self._retained_action_bytes(source, events)
        if not events or events[-1].event_type != "STATE_VERIFIED":
            raise ConversationConflict("retained read proof is absent")
        self.timeline.load(source["thread_id"])
        admission = events[0].payload["data"]
        if (admission.get("action_name") not in {"home_assistant_request", "read_home_assistant_inventory"}
                or admission.get("execution_namespace") != background_execution_namespace(self.turn)
                or admission.get("action_call_id") != source["call_id"]):
            raise ConversationConflict("retained read namespace or call differs")
        port = object.__new__(PostgresExternalConversationTools)
        port._action_names = (admission["action_name"],)
        if source["thread_id"] != port._thread_id(self.turn, admission["action_name"], source["step_index"]):
            raise ConversationConflict("retained read thread differs")
        receipt = port._receipt(self.turn, events, source["step_index"], project=False)
        proof = receipt.output.get("proof")
        observation = receipt.output.get(source["observation_key"])
        if (canonical_sha256(receipt.output) != source["receipt_sha256"]
                or canonical_sha256(proof) != source["proof_sha256"]
                or not isinstance(observation, dict)
                or proof.get("evidence", {}).get("observation") != observation):
            raise ConversationConflict("retained read receipt or proof differs")
        return _encoded(observation)

    def _retained_action_bytes(self, source, events):
        from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
        from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
        if not events:
            raise ConversationConflict("retained action proof is absent")
        self.timeline.load(source["thread_id"])
        admission = events[0].payload["data"]
        name = admission.get("action_name")
        port = object.__new__(PostgresExternalConversationTools)
        port._action_names = (name,)
        if (source["thread_id"] != port._thread_id(self.turn, name, source["step_index"])
                or admission.get("step_index", 0) != source["step_index"]
                or admission.get("action_call_id") != source["call_id"]):
            raise ConversationConflict("retained action thread or call differs")
        if name == "use_connector_tool" and events[0].event_type == "CONNECTOR_SELECTION_ADMITTED":
            if (len(events) != 2 or events[0].event_type != "CONNECTOR_SELECTION_ADMITTED"
                    or events[-1].event_type != "CONNECTOR_SELECTION_COMPLETED"
                    or admission.get("owner") != self.owner):
                raise ConversationConflict("retained local selection differs")
            call = ConversationActionCall(source["call_id"], name, admission["action_arguments"])
            receipt = ConversationActionReceipt(call, events[-1].payload["data"]["receipt_output"])
            from eva.providers.mcp_discovery import _arguments
            reader, arguments = _arguments(call)
            if (receipt.output.get("status") != "verified"
                    or receipt.output.get("source") != "stored_connector_result"
                    or receipt.output.get("result") != self.read(reader, arguments)):
                raise ConversationConflict("retained local selection source differs")
        else:
            if (name not in {"home_assistant_request", "read_home_assistant_inventory", "save_workspace_document", "use_connector_tool"}
                    or events[0].event_type != "TOOL_REQUEST_ADMITTED"
                    or events[-1].event_type != "STATE_VERIFIED"
                    or admission.get("execution_namespace") != background_execution_namespace(self.turn)):
                raise ConversationConflict("retained action namespace or proof differs")
            if name == "use_connector_tool":
                from eva.providers.mcp_discovery import _arguments
                reader, _ = _arguments(ConversationActionCall(
                    source["call_id"], name, admission["action_arguments"]))
                if reader.startswith(READER_PREFIX):
                    raise ConversationConflict("retained external action is a local reader")
            receipt = port._receipt(self.turn, events, source["step_index"], project=False)
        if (receipt.output.get("status") != "verified"
                or canonical_sha256(receipt.output) != source["receipt_sha256"]
                or canonical_sha256(receipt.output.get("proof")) != source["proof_sha256"]):
            raise ConversationConflict("retained action receipt or proof differs")
        return _encoded(retained_action_body(receipt))

    def retained_body_model_view(self, receipt, thread_id, *, step_index):
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        source = {"thread_id": thread_id, "call_id": receipt.call.call_id,
            "step_index": step_index, "observation_key": "action_body",
            "receipt_sha256": canonical_sha256(receipt.output),
            "proof_sha256": canonical_sha256(receipt.output.get("proof")),
            "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(self.turn))}
        raw = self._retained_bytes(source)
        if raw != _encoded(retained_action_body(receipt)):
            raise ConversationConflict("retained action presentation differs")
        binding = {"owner": self.owner, "operation": thread_id, "retained_source": source}
        self._store(binding, json.loads(raw))
        manifest = self._manifest(canonical_sha256(binding), json.loads(raw))
        # Older bodies are reachable without repeating even a preview of content.
        manifest.update(preview="", preview_omitted=True)
        projection = {"contract": "EvaRetainedActionBodyModelView/v1", "source": source, "manifest": manifest}
        ancestor_action_presentation(receipt, projection)
        return projection

    def retained_model_view(self, receipt, thread_id, *, step_index, observation_key):
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        source = {"thread_id": thread_id, "call_id": receipt.call.call_id,
                  "step_index": step_index, "observation_key": observation_key,
                  "receipt_sha256": canonical_sha256(receipt.output),
                  "proof_sha256": canonical_sha256(receipt.output["proof"]),
                  "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(self.turn))}
        # Compare the caller's raw output with the actual retained observation
        # before making any capture or offering a reader.
        raw = self._retained_bytes(source)
        if raw != _encoded(receipt.output[observation_key]):
            raise ConversationConflict("retained read presentation differs")
        binding = {"owner": self.owner, "operation": thread_id,
                   "retained_source": source}
        self._store(binding, json.loads(raw))
        # Use the same manifest/reader; its key binds the exact retained source.
        identifier = canonical_sha256(binding)
        manifest = self._manifest(identifier, json.loads(raw))
        manifest["source_output_truncated"] = receipt.output[observation_key].get("truncated") is True
        manifest["source_response"] = {key: receipt.output[observation_key][key]
            for key in SOURCE_RESPONSE_FIELDS if key in receipt.output[observation_key]}
        navigation = retained_source_navigation(receipt.output[observation_key])
        if navigation is not None:
            manifest["source_navigation"] = navigation
        proof = receipt.output["proof"]
        reference = {"model_view_reference": "/" + observation_key,
            "observation_sha256": canonical_sha256(receipt.output[observation_key]),
            "durable_proof_sha256": source["proof_sha256"]}
        return {**receipt.output, observation_key: manifest,
                "proof": {**proof, "evidence": {**proof["evidence"], "observation": reference}},
                "model_projection": {"contract": "EvaRetainedActionModelView/v1",
                    "source_receipt_sha256": source["receipt_sha256"],
                    "source_proof_sha256": source["proof_sha256"],
                    "observation_key": observation_key}}

    def call(self, command, phase, call):
        self.authorize(self.turn)
        binding = self._binding(command, phase)
        identifier = canonical_sha256(binding)
        thread = "connector-result:" + identifier
        if self.timeline.events(thread):
            return json.loads(self._bytes(identifier)[0])
        state = self.timeline.create_thread(thread_id=thread, checkpoint_id=thread+":started",
            occurred_at=datetime.now(UTC).replace(microsecond=0), data=binding, event_type="CONNECTOR_CAPTURE_STARTED",
            phase="capture_started", operation_id=command.operation_id)
        output = call()
        self._store(binding, output, started_state=state)
        return output

    def _store(self, binding, output, *, started_state=None):
        self.authorize(self.turn)
        identifier = canonical_sha256(binding)
        thread = "connector-result:" + identifier
        if self.timeline.events(thread) and started_state is None:
            if self._bytes(identifier)[0] != _encoded(output):
                raise ConversationConflict("captured result differs from retained bytes")
            return
        raw = _encoded(output)
        if len(raw) > _TRANSPORT_BOUND:
            raise ConversationConflict("connector result exceeds existing transport bound")
        # The started capture also makes an interrupted copy fail closed.
        if started_state is not None:
            state = started_state
        elif not self.timeline.events(thread):
            state = self.timeline.create_thread(thread_id=thread, checkpoint_id=thread+":started",
                occurred_at=datetime.now(UTC).replace(microsecond=0), data=binding,
                event_type="CONNECTOR_CAPTURE_STARTED", phase="capture_started",
                operation_id=binding["operation"])
        else:
            state = self.timeline.load(thread)
        # Each existing timeline event remains smaller than the model-result cap.
        for index, offset in enumerate(range(0, len(raw), 32768)):
            state = self.timeline.transition(thread_id=thread, expected_revision=state.revision,
                checkpoint_id=thread+":chunk:"+str(index), phase="capturing", status="active",
                event_type="CONNECTOR_CAPTURE_CHUNK", occurred_at=datetime.now(UTC).replace(microsecond=0),
                operation_id=binding["operation"],
                data={"index": index, "bytes": base64.b64encode(raw[offset:offset+32768]).decode()})
        self.timeline.transition(thread_id=thread, expected_revision=state.revision,
            checkpoint_id=thread+":sealed", phase="captured", status="completed",
            event_type="CONNECTOR_CAPTURE_SEALED", occurred_at=datetime.now(UTC).replace(microsecond=0),
            operation_id=binding["operation"],
            data={"length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})

    def manifest(self, command, phase, output):
        identifier = canonical_sha256(self._binding(command, phase))
        return self._manifest(identifier, output)

    def _manifest(self, identifier, output):
        raw, digest = self._bytes(identifier)
        if raw != _encoded(output):
            raise ConversationConflict("captured connector result differs from returned output")
        reader = READER_PREFIX + identifier + ":" + digest
        return {"contract": "EvaStoredConnectorResult/v1", "stored_complete": True,
            "sha256": digest, "byte_length": len(raw), "external_state_verified": False,
            "preview": raw.decode()[:2048], "has_more": True,
            "reader": {"action": "use_connector_tool", "tool_id": reader,
                "arguments_schema": selection_schema(),
                "arguments_json": json.dumps({"pointer": "/text" if isinstance(output.get("text"), str) else "",
                    "offset": 0, "limit": 8192, "query": None}),
                "instructions": "Read saved bytes locally. pointer is a JSON pointer; offset/limit count characters. Optional query searches within the selected value from offset. For multiple passages, use {selections: [...]} with 1..8 selections and sum(limit) <= 8192. No connector is invoked."}}

    def read_recorded(self, reader, arguments, call_id):
        """Persist exactly the bounded page delivered by a successful local read."""
        output = self.read(reader, arguments)
        self.authorize(self.turn)
        binding = {"owner": self.owner, "call_id": call_id}
        thread = "connector-selection:" + canonical_sha256(binding)
        data = {**binding, "reader": reader, "arguments": arguments,
                "output": output, "output_sha256": hashlib.sha256(_encoded(output)).hexdigest()}
        if len(_encoded(data)) > _BOUND:
            raise ConnectorSelectionError("stored connector selection receipt exceeds bound; reduce limit")
        existing = self.timeline.events(thread)
        if existing:
            if len(existing) != 1 or existing[0].event_type != "CONNECTOR_SELECTION_READ" or existing[0].payload["data"] != data:
                raise ConversationConflict("stored connector selection call differs")
        else:
            self.timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":read",
                occurred_at=datetime.now(UTC).replace(microsecond=0), data=data,
                event_type="CONNECTOR_SELECTION_READ", phase="selection_read", operation_id=thread)
        return output

    def read(self, reader, arguments):
        match = re.fullmatch(re.escape(READER_PREFIX)+r"([0-9a-f]{64}):([0-9a-f]{64})", reader)
        if not match:
            raise ConversationConflict("stored connector result reference is invalid")
        batched = isinstance(arguments, dict) and set(arguments) == {"selections"}
        selections = arguments["selections"] if batched else [arguments]
        if not isinstance(selections, list) or not 1 <= len(selections) <= 8:
            raise ConnectorSelectionError("stored connector batch requires 1..8 selections")
        for selection in selections:
            self._validate_selection(selection)
        if sum(selection["limit"] for selection in selections) > 8192:
            raise ConnectorSelectionError("stored connector combined selection limits exceed 8192 characters")
        raw, digest = self._bytes(match[1])
        if digest != match[2]:
            raise ConversationConflict("stored connector result reference hash differs")
        document = json.loads(raw)
        pages = [self._select(document, selection, digest, len(raw)) for selection in selections]
        result = ({"contract": "EvaStoredConnectorPages/v1", "sha256": digest,
                   "byte_length": len(raw), "pages": pages,
                   "selected_characters": sum(len(page["text"]) for page in pages),
                   "external_state_verified": False} if batched else pages[0])
        if len(_encoded(result)) > _BOUND:
            raise ConnectorSelectionError("stored connector page output exceeds bound; reduce limits")
        return result

    @staticmethod
    def _validate_selection(arguments):
        if (not isinstance(arguments, dict)
                or set(arguments) not in ({"pointer", "offset", "limit", "query"},
                    {"pointer", "offset", "limit", "query", "json_filter"})):
            raise ConnectorSelectionError("stored connector selection is invalid")
        pointer, offset, limit, query = (arguments[k] for k in ("pointer", "offset", "limit", "query"))
        if (not isinstance(pointer, str) or len(pointer) > 2048 or (pointer and not pointer.startswith("/"))
                or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8192
                or (query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 256))):
            raise ConnectorSelectionError("stored connector selection bounds are invalid")
        if "json_filter" in arguments:
            selection = arguments["json_filter"]
            if (not isinstance(selection, dict) or set(selection) != {"field", "equals"}
                    or not isinstance(selection["field"], str) or len(selection["field"]) > 2048
                    or (selection["field"] and not selection["field"].startswith("/"))
                    or type(selection["equals"]) not in (str, int, bool, type(None))
                    or (isinstance(selection["equals"], str) and len(selection["equals"]) > 256)):
                raise ConnectorSelectionError("stored connector JSON filter is invalid")

    @staticmethod
    def _select(document, arguments, digest, byte_length):
        pointer, offset, limit, query = (arguments[k] for k in ("pointer", "offset", "limit", "query"))
        value = document
        try:
            for part in ([] if not pointer else pointer[1:].split("/")):
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) and key.isdecimal() else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ConnectorSelectionError("stored connector JSON pointer is absent") from None
        filter_metadata = {}
        if "json_filter" in arguments:
            selection = arguments["json_filter"]
            decoded = isinstance(value, str)
            if decoded:
                try:
                    value = json.loads(value)
                except ValueError:
                    raise ConnectorSelectionError("stored connector filter requires valid JSON text") from None
            if not isinstance(value, list):
                raise ConnectorSelectionError("stored connector filter requires a JSON array")
            source_items = len(value)
            matches = []
            for item in value:
                field = item
                try:
                    for part in ([] if not selection["field"] else selection["field"][1:].split("/")):
                        key = part.replace("~1", "/").replace("~0", "~")
                        field = field[int(key)] if isinstance(field, list) and key.isdecimal() else field[key]
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                # JSON booleans and numbers are distinct, unlike Python equality.
                if type(field) is type(selection["equals"]) and field == selection["equals"]:
                    matches.append(item)
            value = matches
            filter_metadata = {"json_filter": dict(selection), "decoded_json": decoded,
                               "source_items": source_items, "matched_items": len(matches)}
        if filter_metadata:
            try:
                # Decoded source floats stay JSON text, outside durable typed fields.
                text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False)
            except ValueError:
                raise ConnectorSelectionError("stored connector filter contains invalid JSON numbers") from None
        else:
            text = value if isinstance(value, str) else _encoded(value).decode()
        selected = text.find(query, offset) if query is not None else min(offset, len(text))
        found = selected >= 0
        start = selected if found else len(text)
        page = text[start:start+limit]
        next_offset = start + len(page)
        result = {"contract": "EvaStoredConnectorPage/v1", "sha256": digest, "byte_length": byte_length,
            "pointer": pointer, "offset": start, "next_offset": next_offset,
            "selection_length": len(text), "found": found, "text": page,
            "has_more": found and next_offset < len(text), "external_state_verified": False}
        result.update(filter_metadata)
        return result
