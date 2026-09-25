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
_TRANSPORT_BOUND = 2097152
_current = ContextVar("connector_result_capture", default=None)


class ConnectorSelectionError(ConversationConflict):
    """A local selection can be corrected without repeating an external action."""


def selection_schema():
    return {"type": "object", "additionalProperties": False,
            "required": ["pointer", "offset", "limit", "query"],
            "properties": {
                "pointer": {"type": "string", "maxLength": 2048,
                            "description": "JSON pointer to an existing value; empty selects the root, otherwise start with /."},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8192},
                "query": {"type": ["string", "null"], "minLength": 1, "maxLength": 256,
                          "description": "Exact text search from offset; null reads directly."}}}



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
        return raw, seal["sha256"]

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
        raw = _encoded(output)
        if len(raw) > _TRANSPORT_BOUND:
            raise ConversationConflict("connector result exceeds existing transport bound")
        # Each existing timeline event remains smaller than the model-result cap.
        for index, offset in enumerate(range(0, len(raw), 32768)):
            state = self.timeline.transition(thread_id=thread, expected_revision=state.revision,
                checkpoint_id=thread+":chunk:"+str(index), phase="capturing", status="active",
                event_type="CONNECTOR_CAPTURE_CHUNK", occurred_at=datetime.now(UTC).replace(microsecond=0),
                operation_id=command.operation_id,
                data={"index": index, "bytes": base64.b64encode(raw[offset:offset+32768]).decode()})
        self.timeline.transition(thread_id=thread, expected_revision=state.revision,
            checkpoint_id=thread+":sealed", phase="captured", status="completed",
            event_type="CONNECTOR_CAPTURE_SEALED", occurred_at=datetime.now(UTC).replace(microsecond=0),
            operation_id=command.operation_id,
            data={"length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        return output

    def manifest(self, command, phase, output):
        identifier = canonical_sha256(self._binding(command, phase))
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
                "instructions": "Read saved bytes locally. pointer is a JSON pointer; offset/limit count characters. Optional query searches within the selected value from offset. No connector is invoked."}}

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
        if not isinstance(arguments, dict) or set(arguments) != {"pointer", "offset", "limit", "query"}:
            raise ConnectorSelectionError("stored connector selection is invalid")
        pointer, offset, limit, query = (arguments[k] for k in ("pointer", "offset", "limit", "query"))
        if (not isinstance(pointer, str) or len(pointer) > 2048 or (pointer and not pointer.startswith("/"))
                or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8192
                or (query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 256))):
            raise ConnectorSelectionError("stored connector selection bounds are invalid")
        raw, digest = self._bytes(match[1])
        if digest != match[2]:
            raise ConversationConflict("stored connector result reference hash differs")
        value = json.loads(raw)
        try:
            for part in ([] if not pointer else pointer[1:].split("/")):
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) and key.isdecimal() else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ConnectorSelectionError("stored connector JSON pointer is absent") from None
        text = value if isinstance(value, str) else _encoded(value).decode()
        selected = text.find(query, offset) if query is not None else min(offset, len(text))
        found = selected >= 0
        start = selected if found else len(text)
        page = text[start:start+limit]
        next_offset = start + len(page)
        result = {"contract": "EvaStoredConnectorPage/v1", "sha256": digest, "byte_length": len(raw),
            "pointer": pointer, "offset": start, "next_offset": next_offset,
            "selection_length": len(text), "found": found, "text": page,
            "has_more": found and next_offset < len(text), "external_state_verified": False}
        if len(_encoded(result)) > _BOUND:
            raise ConversationConflict("stored connector page exceeds bound")
        return result
