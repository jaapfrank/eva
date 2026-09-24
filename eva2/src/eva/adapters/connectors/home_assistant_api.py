"""Configured Home Assistant API access through the existing durable tool boundary.

No device/domain vocabulary is encoded here. The model discovers the live API,
chooses a request and reads back changes. Credentials never enter tool arguments.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import unquote, urlsplit

import httpx2 as httpx

from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.application.conversation import (
    ConversationConflict, ConversationToolDescriptor, ConversationToolPreparation,
)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.contracts.canonical_json import canonical_sha256

_ACTION = "home_assistant_request"
_MAX_BYTES = 2_097_152


class HomeAssistantApiTool:
    action_names = (_ACTION,)

    def __init__(self, *, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.admin = HomeAssistantAdminAdapter(base_url=base_url, token=token)

    @staticmethod
    def conversation_tools():
        return (ConversationToolDescriptor(
            name=_ACTION,
            description=(
                "Gebruik de echte Home Assistant REST- of WebSocket-API. Ontdek eerst "
                "beschikbare services, entiteiten, apparaten en ruimtes; kies zelf de "
                "passende API en parameters voor de gebruikersvraag. Geen vaste lijst "
                "apparaattypen of automatiseringssjablonen. REST: GET /api/services, "
                "GET /api/states, GET /api/states/<entity_id>, POST "
                "/api/services/<domain>/<service>. Automatiseringsconfiguratie: "
                "GET/POST /api/config/automation/config/<id>; lees bestaande inhoud "
                "voor wijzigingen, behoud niet-gevraagde instellingen en lees daarna terug. "
                "WebSocket: body_json bevat een command met type, bijvoorbeeld get_services, "
                "config/area_registry/list of config/entity_registry/list. Gebruik levende "
                "API-resultaten/documentatie; verzin geen apparaatnamen of schema's. "
                "Een API-antwoord bewijst geen fysiek resultaat. Controleer veranderingen "
                "met een aparte leesaanvraag. Lang werk kan via start_digital_work doorgaan. "
                "response_pointer is een JSON Pointer om in een groot antwoord te kijken; "
                "offset/limit pagineren arrays of objectvelden. Leeg pointer toont de root."
            ),
            input_schema={"type": "object", "additionalProperties": False,
                "properties": {
                    "transport": {"type": "string", "enum": ["rest", "websocket"]},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "description": "Relative /api/ path; empty for WebSocket."},
                    "body_json": {"type": "string", "description": "JSON request body; empty for no REST body."},
                    "response_pointer": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                "required": ["transport", "method", "path", "body_json", "response_pointer", "offset", "limit"]},
            completion_guidance=(
                "result is the persisted API response, not independent physical-state proof. "
                "Check http_status/success and response before claiming success. Read changes "
                "back with another request. Never repeat an uncertain write automatically. "
                "Use ordinary Dutch names in your reply, not API implementation details."
            ),
        ),)

    def _arguments(self, arguments):
        a = dict(arguments)
        if set(a) != {"transport", "method", "path", "body_json", "response_pointer", "offset", "limit"}:
            raise ConversationConflict("Home Assistant API arguments differ")
        if a["transport"] not in {"rest", "websocket"} or a["method"] not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ConversationConflict("Home Assistant API transport is invalid")
        if type(a["offset"]) is not int or a["offset"] < 0 or type(a["limit"]) is not int or not 1 <= a["limit"] <= 50:
            raise ConversationConflict("Home Assistant response page is invalid")
        for name in ("path", "body_json", "response_pointer"):
            if not isinstance(a[name], str) or len(a[name]) > 100_000:
                raise ConversationConflict("Home Assistant API text is invalid")
        if a["response_pointer"] and not a["response_pointer"].startswith("/"):
            raise ConversationConflict("Response pointer must be a JSON Pointer")
        try:
            body = json.loads(a["body_json"]) if a["body_json"] else None
        except ValueError as error:
            raise ConversationConflict("Home Assistant request body must be JSON") from error
        if a["transport"] == "rest":
            path = a["path"]
            parsed = urlsplit(path)
            decoded = unquote(parsed.path)
            if (not path.startswith("/api/") or parsed.scheme or parsed.netloc or parsed.fragment
                or "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/"))
                or any(ord(char) < 32 for char in path) or "%" in decoded):
                raise ConversationConflict("Home Assistant path must remain on the configured API")
        elif (a["path"] or not isinstance(body, dict) or not isinstance(body.get("type"), str)
              or not body["type"] or "id" in body or "access_token" in body
              or body["type"] in {"auth", "subscribe_events", "subscribe_trigger"}):
            raise ConversationConflict("Use a single authenticated WebSocket command without id or token")
        return a, body

    async def prepare(self, turn, call):
        del turn
        if call.name != _ACTION:
            raise ConversationConflict("Home Assistant API tool is unavailable")
        args, _ = self._arguments(call.arguments)
        return ConversationToolPreparation(
            tool_name="home_assistant.api.request", arguments=args, expected_proof={},
            receipt_output={"operation_kind": "invoke", "external_state_verified": False},
            observation_output_key="result", execution_timeout_seconds=25,
            verification_timeout_seconds=1,
        )

    def _request(self, arguments):
        a, body = self._arguments(arguments)
        if a["transport"] == "rest":
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                with client.stream(a["method"], self.base_url + a["path"],
                        headers={"Authorization": "Bearer " + self.token},
                        **({"json": body} if body is not None else {})) as response:
                    chunks = []; size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            raise RuntimeError("Home Assistant response exceeds the transport limit")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    try:
                        value = json.loads(raw) if raw else None
                    except ValueError:
                        value = raw.decode("utf-8", errors="replace")
                    status = {"http_status": response.status_code, "success": 200 <= response.status_code < 300}
        else:
            with self.admin._session() as socket:
                socket.send(json.dumps({**body, "id": 1}))
                frame = self.admin._decode(socket.recv(timeout=15))
                if frame.get("type") != "result" or frame.get("id") != 1:
                    raise RuntimeError("Home Assistant WebSocket response does not match request")
                status = {"success": frame.get("success") is True}
                value = frame.get("result") if status["success"] else frame.get("error")
        # Selection is presentation only; the full response hash retains its origin.
        result = {**status, "response_sha256": canonical_sha256(value), "external_state_verified": False}
        try:
            selected = value
            for segment in a["response_pointer"].split("/")[1:]:
                key = segment.replace("~1", "/").replace("~0", "~")
                selected = selected[int(key)] if isinstance(selected, list) else selected[key]
            if isinstance(selected, (list, dict)):
                entries = list(selected.items()) if isinstance(selected, dict) else selected
                page = entries[a["offset"]:a["offset"] + a["limit"]]
                result.update(total=len(entries), next_offset=(a["offset"] + len(page) if a["offset"] + len(page) < len(entries) else None))
                selected = dict(page) if isinstance(selected, dict) else page
            encoded = json.dumps(selected, ensure_ascii=False)
            if len(encoded) > 40_000:
                result.update(response_preview=encoded[:40_000], truncated=True,
                    guidance="Use a narrower response_pointer or smaller page; preview is incomplete.")
            else:
                result.update(response=selected, truncated=False)
        except (KeyError, IndexError, TypeError, ValueError):
            result.update(selection_error="Response pointer does not exist; API call already completed. Do not repeat a write to change the selection.")
        return result

    async def execute(self, request):
        result = await asyncio.to_thread(self._request, request.arguments)
        return ToolDispatchResult(True, canonical_sha256(result), {
            "response": result, "request_sha256": canonical_sha256(request.arguments)})

    async def observe(self, request, dispatch):
        if dispatch is None:
            return ToolProofObservation("pending", {}, {
                "reason": "Missing durable response; automatic replay is unsafe.",
                "external_state_verified": False})
        result = dispatch.details.get("response")
        if (not dispatch.accepted or not isinstance(result, dict)
            or dispatch.details.get("request_sha256") != canonical_sha256(request.arguments)
            or dispatch.provider_ref != canonical_sha256(result)):
            raise RuntimeError("Home Assistant durable API response differs")
        return ToolProofObservation("verified" if result.get("success") is True else "conflict", {}, {"observation": result,
            "source": "home_assistant_api_response", "external_state_verified": False})
