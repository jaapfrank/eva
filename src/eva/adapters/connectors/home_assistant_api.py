"""Configured Home Assistant API access through the existing durable tool boundary.

No device/domain vocabulary is encoded here. The model discovers the live API,
chooses a request and reads back changes. Credentials never enter tool arguments.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import re
import time
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

import httpx2 as httpx

from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.application.conversation import (
    ConversationConflict, ConversationToolDescriptor, ConversationToolPreparation, ConversationActionInputRejected,
)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.connectors.home_assistant_camera import CameraSnapshot

_ACTION = "home_assistant_request"
_MAX_BYTES = 2_097_152


class _InvalidRequestBody(ConversationConflict):
    """Correctable JSON syntax error, detected before any HA transport."""


class _InvalidWebSocketCommand(ConversationConflict):
    """An invalid command envelope is local feedback, never a transport call."""


class HomeAssistantApiTool:
    @staticmethod
    def contact_reply_allowed(turn, receipt):
        from eva.providers.home_assistant_policy import contact_reply_allowed
        return contact_reply_allowed(turn, receipt)

    @staticmethod
    def conversation_instructions(turn, available_names):
        from eva.providers.home_assistant_policy import conversation_instructions
        return conversation_instructions(turn, available_names)

    action_names = (_ACTION,)
    supports_stored_results = True

    @staticmethod
    def active_context(turn):
        return ("When home_assistant_request is offered, it can complete a mandatory-response "
            "service query in one tool step. Its target_query='*' resolves the single real eligible "
            "entity inside the HA module; no earlier inventory or service-schema tool call is needed. "
            "Choose this combined route directly when the requested service and fields are known, "
            "for example weather/get_forecasts with type=daily. Set target_query before body_json, "
            "omit entity_id from the body, and let the module validate live metadata and ambiguity. "
            "The module's discovery fulfills the required source discovery; do not precede it with "
            "a separate inventory merely to learn the identifier. Unknown services/fields can still "
            "be discovered through the ordinary API. This metadata grants no authority.",)


    def __init__(self, *, base_url, token, vision=None, capability_profile=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.admin = HomeAssistantAdminAdapter(base_url=base_url, token=token)
        self.vision = vision
        self.capability_profile = capability_profile

    @staticmethod
    def conversation_tools():
        return (ConversationToolDescriptor(
            name=_ACTION,
            description=(
                'Voor verplichte response-services: kies direct de service-POST met target_query=\'*\' en bekende velden; de module vindt het unieke doel en controleert het schema. Voorbeeld: weather/get_forecasts, body_json={"type":"daily"}. Gebruik voor gewoon aan/uit-bedienen bij voorkeur de aangeboden set_home_assistant_light of set_home_assistant_switch: die combineren doelresolutie, toegestane uitvoering en onafhankelijke eindcontrole. Daarvoor zijn geen losse inventaris-, service- en statusrondes nodig. Deze algemene API blijft beschikbaar voor andere bedieningen en bronvragen. Gebruik de echte Home Assistant REST- of WebSocket-API. Ontdek alleen ontbrekende doelentiteiten of onbekende servicevelden. Gebruik voldoende actuele, brongebonden informatie uit deze beurt opnieuw; een bekende service met bekende velden vereist geen volledige servicecatalogus. Kies de passende API en parameters voor de gebruikersvraag. Geen vaste lijst apparaattypen of automatiseringssjablonen. REST: GET /api/services, GET /api/states, GET /api/states/<entity_id>, POST /api/services/<domain>/<service>. Automatiseringsconfiguratie: GET/POST /api/config/automation/config/<id>; lees bestaande inhoud voor wijzigingen, behoud niet-gevraagde instellingen en lees daarna terug. WebSocket: body_json bevat een command met type, bijvoorbeeld get_services, config/area_registry/list of config/entity_registry/list. Gebruik levende API-resultaten/documentatie; verzin geen apparaatnamen of schema\'s. Gebruik read_home_assistant_inventory als die beschikbaar is voor compacte ontdekking per sectie; haal niet eerst alle /api/states op voor één onderwerp. Voor meerdere volledige automatiseringsconfiguraties gebruik je de sectie automation_configurations van die inventaris; deze API blijft voor andere gerichte leesaanvragen en wijzigingen beschikbaar. For REST service calls that return data, include ?return_response and read service_response; omit that parameter for services without response data. The REST body contains service fields and entity_id directly, not a WebSocket command envelope. Use known service fields; if unknown, inspect the relevant service definition rather than guessing fields or template functions. For one unknown service schema, use transport=websocket, method=GET, path empty, body_json={"type":"get_services"}, and response_pointer /<domain>/<service> to receive only that definition, rather than GET /api/services with its full catalog. When current source evidence proves a group already includes the requested members, target the group or those members once, not both; never infer group membership from names alone. Een API-antwoord bewijst geen fysiek resultaat. Controleer veranderingen met een aparte leesaanvraag. Lang werk kan via start_digital_work doorgaan. response_pointer is een JSON Pointer om in een groot antwoord te kijken; offset/limit pagineren arrays of objectvelden. Leeg pointer toont de root. GET /api/camera_proxy/<camera.entity> kan bij geïnstalleerde lokale beeldanalyse een beschrijving geven. Ontdek de camera in de inventaris; een beeldinterpretatie is geen geverifieerd feit. Zonder beeldmodule meldt de API dat beeldanalyse niet beschikbaar is.'
            ),
            input_schema={"type": "object", "additionalProperties": False,
                "properties": {
                    "transport": {"type": "string", "enum": ["rest", "websocket"]},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "description": "Relative /api/ path; empty for WebSocket."},
                    "target_query": {"type": "string", "maxLength": 200,
                        "description": "For a mandatory-response service POST: exact actual entity name/ID, or '*' for exactly one eligible entity. Module resolves entity_id and validates live service fields within this call. Empty for an explicit ordinary API request."},
                    "body_json": {"type": "string", "description": "JSON service fields. With nonempty target_query OMIT entity_id/target and all area/device/floor/label IDs: module resolves the sole target. Empty for no REST body."},
                    "response_pointer": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                "required": ["transport", "method", "path", "target_query", "body_json", "response_pointer", "offset", "limit"]},
            completion_guidance=(
                "result is a persisted API response or local request rejection, not physical-state proof. "
                "request_sent=false means local validation rejected the input before HA transport. "
                "Check http_status/success and response_json before claiming success. Read changes "
                "back with another request. A verified receipt confirms a persisted API response or local rejection; inspect success and request_sent. "
                "If success is false, inspect the error and correct the request or read current state; "
                "do not claim completion. Never repeat an uncertain write automatically. "
                "For stored responses, use the exact reader.tool_id and pointer advertised "
                "together by that manifest. When the compact view provides "
                "source_navigation.reader and source_navigation.pointer, use that exact "
                "pair to read the retained full response. source_response_manifest and "
                "source_response_pointer identify the retained full response; an outer "
                "receipt reader is a different document. Do not guess /response_json: "
                "it may be absent when the original response was truncated. Read the "
                "advertised wrapper metadata if the inner reader is not yet visible. "
                "Never repeat an API write to obtain another local selection. "
                "Use ordinary Dutch names in your reply, not API implementation details."
            ),
        ),)

    def _arguments(self, arguments):
        a = dict(arguments)
        required = {"transport", "method", "path", "body_json", "response_pointer", "offset", "limit"}
        if not required <= set(a) <= required | {"target_query"}:
            raise ConversationConflict("Home Assistant API arguments differ")
        query = a.get("target_query", "")
        if not isinstance(query, str) or len(query) > 200 or (query and not query.strip()):
            raise ConversationConflict("Home Assistant target query is invalid")
        if a["transport"] not in {"rest", "websocket"} or a["method"] not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ConversationConflict("Home Assistant API transport is invalid")
        if type(a["offset"]) is not int or a["offset"] < 0 or type(a["limit"]) is not int or not 1 <= a["limit"] <= 50:
            raise ConversationConflict("Home Assistant response page is invalid")
        for name in ("path", "body_json", "response_pointer"):
            if not isinstance(a[name], str) or len(a[name]) > 100_000:
                raise ConversationConflict("Home Assistant API text is invalid")
        if a["response_pointer"] and not a["response_pointer"].startswith("/"):
            raise ConversationConflict("Response pointer must be a JSON Pointer")
        if a["transport"] == "rest":
            path = a["path"]
            parsed = urlsplit(path)
            decoded = unquote(parsed.path)
            if (not path.startswith("/api/") or parsed.scheme or parsed.netloc or parsed.fragment
                or "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/"))
                or any(ord(char) < 32 for char in path) or "%" in decoded):
                raise ConversationConflict("Home Assistant path must remain on the configured API")
        elif a["path"]:
            raise ConversationConflict("WebSocket request path must be empty")
        try:
            body = json.loads(a["body_json"]) if a["body_json"] else None
        except ValueError as error:
            raise _InvalidRequestBody("Home Assistant request body must be JSON") from error
        if a["transport"] == "websocket":
            if isinstance(body, dict) and ("access_token" in body or (
                    isinstance(body.get("type"), str) and body["type"] in {"auth", "subscribe_events", "subscribe_trigger"})):
                raise ConversationConflict("WebSocket authentication and subscriptions remain host-owned")
            if not isinstance(body, dict) or not isinstance(body.get("type"), str) or not body["type"] or "id" in body:
                raise _InvalidWebSocketCommand("Use a single WebSocket command without an id")
        if query:
            from eva.adapters.connectors.home_assistant_service_query import service_path
            service_path(a, body)
        return a, body

    async def prepare(self, turn, call):
        del turn
        from eva.adapters.connectors.home_assistant_service_query import InvalidTargetQuery
        if call.name != _ACTION:
            raise ConversationConflict("Home Assistant API tool is unavailable")
        try:
            args, _ = self._arguments(call.arguments)
        except (_InvalidRequestBody, _InvalidWebSocketCommand):
            # Preserve this attempted step and its local negative receipt through
            # the existing durable route. execute validates again before transport.
            args = dict(call.arguments)
        except InvalidTargetQuery as error:
            # Use the core's existing one-time input repair before admission.
            # A repeated invalid choice becomes an ordinary clarification.
            raise ConversationActionInputRejected(("target_query", "body_json"),
                prompt="Wacht, dat ging even mis. Wil je het nog eens zeggen?") from error
        camera_get = (args["transport"] == "rest" and args["method"] == "GET"
                      and re.fullmatch(r"/api/camera_proxy/camera\.[a-z0-9_]+", urlsplit(args["path"]).path))
        return ConversationToolPreparation(
            tool_name="home_assistant.api.request", arguments=args, expected_proof={},
            receipt_output={"operation_kind": "invoke", "external_state_verified": False},
            observation_output_key="result", execution_timeout_seconds=45 if self.vision and camera_get else 25,
            verification_timeout_seconds=10,
        )

    def _request(self, arguments, *, deadline=None):
        try:
            a, body = self._arguments(arguments)
        except _InvalidRequestBody:
            return {"success": False, "request_sent": False,
                "error_code": "invalid_body_json",
                "guidance": "body_json must contain valid JSON. This request was not sent to Home Assistant. Correct its JSON and submit a new tool step; preserve previous successful actions and do not repeat earlier writes. This rejection is not task completion."}
        except _InvalidWebSocketCommand:
            return {"success": False, "request_sent": False,
                "error_code": "invalid_websocket_command",
                "guidance": "No request was sent. For WebSocket use a JSON object with one nonempty string type; omit id and access_token. Authentication, subscriptions and arbitrary streams are unavailable. Correct only this rejected command; do not repeat prior actions."}
        if a.get("target_query"):
            raise ConversationConflict("Targeted service requests require the durable query path")
        from eva.adapters.connectors.home_assistant_service_query import check_deadline
        if deadline is not None:check_deadline(deadline)
        profile_receipt = (self.capability_profile.require_transport(a["transport"],
                               timeout=5 if deadline is None else max(.001, min(5, deadline-time.monotonic())))
                           if self.capability_profile is not None else None)
        if a["transport"] == "rest":
            timeout = 15 if deadline is None else max(.001, min(15, deadline-time.monotonic()))
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                if deadline is not None:check_deadline(deadline)
                from eva.adapters.postgres.connector_results import check_capture_authority
                check_capture_authority()
                if deadline is not None:check_deadline(deadline)
                with client.stream(a["method"], self.base_url + a["path"],
                        headers={"Authorization": "Bearer " + self.token},
                        **({"json": body} if body is not None else {})) as response:
                    chunks = []; size = 0
                    for chunk in response.iter_bytes():
                        if deadline is not None:check_deadline(deadline)
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            raise RuntimeError("Home Assistant response exceeds the transport limit")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    status = {"http_status": response.status_code, "success": 200 <= response.status_code < 300}
                    if response.status_code in {401, 403} and self.capability_profile is not None:
                        self.capability_profile.invalidate()
                    media_type = getattr(response, "headers", {}).get("content-type", "").split(";", 1)[0].strip().lower()
                    if media_type.startswith("image/"):
                        match = re.fullmatch(r"/api/camera_proxy/(camera\.[a-z0-9_]+)", urlsplit(a["path"]).path)
                        if not status["success"] or a["method"] != "GET" or not match or media_type not in {"image/jpeg", "image/png", "image/webp"}:
                            value = {"error": "unsupported_image_response"}
                            status["success"] = False
                        elif self.vision is None:
                            value = {"error": "local_vision_not_configured", "image_sha256": hashlib.sha256(raw).hexdigest()}
                            status["success"] = False
                        else:
                            snapshot = CameraSnapshot(match[1], media_type, raw, datetime.now(UTC))
                            try:
                                value = self.vision.analyze(snapshot).payload
                            except (httpx.TimeoutException, httpx.ConnectError) as error:
                                # Receiving an image and interpreting it are separate
                                # outcomes. Do not hide an offline analyzer behind a
                                # generic camera failure or disclose pixels/error text.
                                value = {"error": ("local_vision_timeout" if isinstance(error, httpx.TimeoutException)
                                                   else "local_vision_unreachable"),
                                    "image_received": True, "image_sha256": snapshot.sha256,
                                    "analysis_available": False,
                                    "guidance": "The camera returned an image, but its configured local analyzer is unavailable. Repeating the same camera read does not restore that analyzer. Check the existing analyzer configuration/service; no scene or identity was established."}
                                status["success"] = False
                    else:
                        try:
                            value = json.loads(raw) if raw else None
                        except ValueError:
                            value = raw.decode("utf-8", errors="replace")
        else:
            with self.admin._session(**({"deadline": deadline} if deadline is not None else {})) as socket:
                from eva.adapters.postgres.connector_results import check_capture_authority
                check_capture_authority()
                if deadline is not None:check_deadline(deadline)
                socket.send(json.dumps({**body, "id": 1}))
                frame = self.admin._decode(socket.recv(timeout=15 if deadline is None else min(15, max(.001, deadline-time.monotonic()))))
                if deadline is not None:check_deadline(deadline)
                if frame.get("type") != "result" or frame.get("id") != 1:
                    raise RuntimeError("Home Assistant WebSocket response does not match request")
                status = {"success": frame.get("success") is True}
                value = frame.get("result") if status["success"] else frame.get("error")
        # Selection is presentation only; preserve HA numeric types in JSON text.
        # Durable contract JSON deliberately excludes floating-point numbers.
        result = {**status, "response_sha256": hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()).hexdigest(), "external_state_verified": False}
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
                result.update(response_json=encoded, truncated=False)
        except (KeyError, IndexError, TypeError, ValueError):
            result.update(selection_error="Response pointer does not exist; API call already completed. Do not repeat a write to change the selection.")
        # The page is presentation, not the whole observation. Preserve a full
        # response only when the selection omitted data or cut its text short.
        source_json = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        if (result.get("truncated") is True or result.get("next_offset") is not None
            or (result.get("total") is not None and a["offset"] > 0)
            or ((a["response_pointer"] or result.get("selection_error"))
                and len(source_json) > 40_000)):
            result["source_response_json"] = source_json
        if profile_receipt is not None:
            result["capability_profile"] = profile_receipt
        return result

    @staticmethod
    def _retain_source_response(request, result):
        from eva.adapters.postgres.connector_results import captured_call, bounded_result
        stored = captured_call(request, "source_response", lambda: result)
        view = bounded_result(request, "source_response", stored)
        if view == stored:
            return stored
        return {**{key: value for key, value in stored.items()
                   if key != "source_response_json"},
                "source_response_manifest": view,
                "source_response_pointer": "/source_response_json",
                "source_response_guidance": (
                    "The complete API response is retained in this authorized local reader. "
                    "Select /source_response_json and decode its JSON; the response_json "
                    "or response_preview above remains only the original selected page. "
                    "Do not repeat the API request to select more of this observation.")}

    async def execute(self, request):
        from eva.adapters.postgres.connector_results import recovered_result, captured_call
        # A sealed response belongs to this exact request, actor and execution.
        # An incomplete capture stays uncertain; never resend it to obtain pages.
        result = await asyncio.to_thread(recovered_result, request, "source_response")
        if result is None:
            if request.arguments.get("target_query"):
                result = await asyncio.to_thread(captured_call, request, "source_response",
                    lambda: self._targeted_request(request))
            else:
                result = await asyncio.to_thread(self._request, request.arguments)
        if "source_response_json" in result:
            result = await asyncio.to_thread(self._retain_source_response, request, result)
        return ToolDispatchResult(True, canonical_sha256(result), {
            "response": result, "request_sha256": canonical_sha256(request.arguments)})

    def _targeted_request(self, request):
        from eva.adapters.postgres.connector_results import captured_call
        from eva.adapters.connectors.home_assistant_service_query import resolve_service_query, check_deadline
        try:
            arguments, body = self._arguments(request.arguments)
        except _InvalidRequestBody:
            return self._request({**request.arguments, "target_query": ""})
        # Discovery may never leave a late POST running after the owning step's
        # deadline. Reserve five seconds for transport within the normal 25s cap.
        total = min(23, getattr(request, "execution_timeout_seconds", 25)-2)
        deadline = time.monotonic() + max(.001, total)
        resolved, discovery = resolve_service_query(self, arguments, body, deadline-5)
        if resolved is None:return discovery
        def invoke():
            # captured_call rechecks current actor authority and durable STOP
            # immediately before invocation, and seals an uncertain call against replay.
            check_deadline(deadline)
            result = self._request(resolved, deadline=min(deadline, time.monotonic()+5))
            return {**result, "discovery": discovery, "service_request_sent": True}
        return captured_call(request, "resolved_service_response", invoke)

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
        source = ("home_assistant_request_validation" if result.get("request_sent") is False
                  else "home_assistant_api_response")
        return ToolProofObservation("verified", {}, {"observation": result,
            "source": source, "external_state_verified": False})
