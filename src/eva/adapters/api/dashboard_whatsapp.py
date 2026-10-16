"""Optional WhatsApp transport: server-bound owner, ordinary Eva, existing media."""
from datetime import UTC, datetime
import asyncio
import os
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
import httpx2 as httpx

from eva.application.conversation import ConversationTurn
from eva.adapters.api.installer_request_json import read_small_json_request


PHONE = re.compile(r"[1-9][0-9]{6,14}\Z")
MESSAGE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def whatsapp_conversation(phone):
    if not isinstance(phone, str) or not PHONE.fullmatch(phone):
        raise ValueError("invalid WhatsApp contact")
    return "whatsapp:" + phone


class WhatsAppControl:
    def __init__(self, token, *, client=None):
        self.token = token
        self.client = client or httpx.Client(
            base_url=os.environ.get("EVA_WHATSAPP_BRIDGE_URL", "http://eva2-whatsapp:8080"),
            timeout=10, follow_redirects=False, trust_env=False)

    def call(self, operation="status", payload=None):
        if not self.token:
            raise ValueError("WhatsApp requires the authenticated owner gateway")
        reply = self.client.request("GET" if operation == "status" else "POST", "/" + operation,
            headers={"Authorization": "Bearer " + self.token},
            **({} if payload is None else {"json": payload}))
        reply.raise_for_status()
        value = reply.json()
        if not isinstance(value, dict) or value.get("contract") != "EvaWhatsApp/v1":
            raise ValueError("invalid WhatsApp control response")
        return value

    def require_contact(self, phone):
        whatsapp_conversation(phone)
        state = self.call()
        if (state.get("enabled") is not True or state.get("connection") != "connected"
                or state.get("contact_phone") != phone):
            raise ValueError("WhatsApp contact is not currently connected")
        return state


def whatsapp_turn(runtime, phone, message_id, text, occurred_at):
    from eva.bootstrap.dashboard_work_context import owner_turn
    service, owner = owner_turn(runtime)
    if not isinstance(message_id, str) or not MESSAGE.fullmatch(message_id):
        raise ValueError("invalid WhatsApp message ID")
    if not isinstance(text, str) or not text.strip() or len(text) > 4096:
        raise ValueError("invalid WhatsApp text")
    stamp = datetime.strptime(occurred_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if abs((datetime.now(UTC) - stamp).total_seconds()) > 86400:
        raise ValueError("WhatsApp message is not current")
    conversation = whatsapp_conversation(phone)
    turn = ConversationTurn.private(owner.tenant_id, owner.installation_id,
        owner.principal_id, conversation, conversation + ":" + message_id,
        text, occurred_at)
    service.validate_authority(turn)
    return service, turn


def install_dashboard_whatsapp(app, *, access, runtime, media=None, control=None):
    control = control or WhatsAppControl(access.token)

    def authorize(request):
        access.require(request)
        if access.token is None or request.query_params:
            raise HTTPException(403, "authenticated owner required")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "same-origin owner request required")
        if (request.method == "POST" and not request.url.path.endswith("/transcribe")
                and request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json"):
            raise HTTPException(415, "JSON request required")
        from eva.bootstrap.dashboard_work_context import owner_turn
        try:
            owner_turn(runtime)
        except Exception:
            raise HTTPException(403, "current owner required") from None

    def status(value):
        # Fixed projection: never forward bridge credentials or arbitrary fields.
        keys = {"contract", "enabled", "connection", "contact_phone", "qr_data_url",
                "audio_enabled", "last_error"}
        result = {key: value.get(key) for key in keys}
        result["media_available"] = media is not None
        if media is not None:
            try:
                media.check(media.issue_session())
            except Exception:
                result["media_available"] = False
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/whatsapp")
    def get_status(request: Request):
        authorize(request)
        try:
            return status(control.call())
        except Exception:
            raise HTTPException(503, "WhatsApp adapter unavailable") from None

    @app.post("/internal/v1/dashboard/whatsapp/{operation}")
    async def change(operation: str, request: Request):
        authorize(request)
        payload = await read_small_json_request(request, 512, "WhatsApp settings")
        if operation == "configure":
            if (not isinstance(payload, dict) or set(payload) != {"contact_phone", "audio_enabled"}
                    or type(payload["audio_enabled"]) is not bool):
                raise HTTPException(422, "invalid WhatsApp settings")
            try:
                whatsapp_conversation(payload["contact_phone"])
            except ValueError:
                raise HTTPException(422, "international phone number required") from None
        elif operation not in {"enable", "disable", "logout"} or payload != {}:
            raise HTTPException(422, "invalid WhatsApp control")
        try:
            def invoke():
                authorize(request)
                return control.call(operation, payload)
            value = await asyncio.to_thread(invoke)
            authorize(request)
            return status(value)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "WhatsApp operation not confirmed") from None

    @app.post("/internal/v1/whatsapp/respond")
    async def respond(request: Request):
        authorize(request)
        payload = await read_small_json_request(request, 20000, "WhatsApp message")
        if not isinstance(payload, dict) or set(payload) != {"contact_phone", "message_id", "text", "occurred_at"}:
            raise HTTPException(422, "invalid WhatsApp message")
        def invoke():
            control.require_contact(payload["contact_phone"])
            _, turn = whatsapp_turn(runtime, payload["contact_phone"], payload["message_id"], payload["text"], payload["occurred_at"])
            runtime.admit_turn_audience(turn)
            reply = runtime.respond(turn)
            runtime.after_response(turn, reply)
            return {"text": reply.text, "conversation_id": turn.conversation_id, "message_id": turn.message_id}
        try:
            return await asyncio.to_thread(invoke)
        except ValueError:
            raise HTTPException(422, "WhatsApp message not admitted") from None
        except Exception:
            raise HTTPException(503, "WhatsApp response not confirmed") from None

    @app.post("/internal/v1/whatsapp/return/{operation}")
    async def work_return(operation: str, request: Request):
        authorize(request)
        payload = await read_small_json_request(request, 1200, "WhatsApp return")
        expected = {"contact_phone"} | (set() if operation == "poll" else {"work_id", "operation_id"})
        if operation not in {"poll", "authorize", "ack"} or not isinstance(payload, dict) or set(payload) != expected:
            raise HTTPException(422, "invalid WhatsApp return")
        def invoke():
            phone = payload["contact_phone"]
            control.require_contact(phone)
            _, turn = whatsapp_turn(runtime, phone, "return", "Resultaat bekijken", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
            result = runtime.private_work_return(turn, operation, **{k: v for k, v in payload.items() if k != "contact_phone"})
            if result.get("result") is not None:
                from eva.bootstrap.whatsapp_context import original_result_message
                result["result"]["origin_message_id"] = original_result_message(runtime, turn, result["result"]["work_id"])
                # Transport only receives the requested short excerpt. Hashes
                # still identify the unmodified full stored result for authorize.
                output = result["result"].pop("output")
                result["result"]["summary"] = output[:600]
                result["result"]["output_truncated"] = len(output) > 600
            return result
        try:
            return await asyncio.to_thread(invoke)
        except Exception:
            raise HTTPException(403, "WhatsApp return unavailable") from None

    @app.post("/internal/v1/whatsapp/authorize")
    async def authorize_delivery(request: Request):
        authorize(request)
        payload = await read_small_json_request(request, 512, "WhatsApp delivery")
        if not isinstance(payload, dict) or set(payload) != {"contact_phone", "message_id"}:
            raise HTTPException(422, "invalid WhatsApp delivery")
        try:
            await asyncio.to_thread(control.require_contact, payload["contact_phone"])
            whatsapp_turn(runtime, payload["contact_phone"], payload["message_id"], "Antwoord afleveren", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
            return {"authorized": True}
        except Exception:
            raise HTTPException(403, "WhatsApp delivery unavailable") from None

    def media_session(request, phone):
        authorize(request)
        state = control.require_contact(phone)
        if not state.get("audio_enabled") or media is None:
            raise HTTPException(503, "WhatsApp speech messages not available")
        session = media.issue_session()
        media.check(session)
        return session

    @app.post("/internal/v1/whatsapp/transcribe")
    async def transcribe(request: Request):
        phone = request.headers.get("x-eva-contact")
        try:
            session = await asyncio.to_thread(media_session, request, phone)
            if request.headers.get("content-type") != "audio/pcm":
                raise HTTPException(415, "16 kHz mono signed PCM required")
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > 3_840_000:
                    raise HTTPException(413, "speech message too long")
                data.extend(chunk)
            if not data or len(data) % 2:
                raise HTTPException(422, "invalid PCM")
            await asyncio.to_thread(media_session, request, phone)
            media.check(session)
            async def chunks():
                for offset in range(0, len(data), 3200):
                    yield bytes(data[offset:offset + 3200])
            text = await media.speech_input.transcribe(chunks())
            await asyncio.to_thread(media_session, request, phone)
            media.check(session)
            return {"text": text}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "speech message could not be transcribed") from None

    @app.post("/internal/v1/whatsapp/synthesize")
    async def synthesize(request: Request):
        authorize(request)
        payload = await read_small_json_request(request, 16000, "WhatsApp speech reply")
        if (not isinstance(payload, dict) or set(payload) != {"contact_phone", "text"}
                or not isinstance(payload["text"], str) or not 1 <= len(payload["text"]) <= 4096):
            raise HTTPException(422, "invalid speech reply")
        try:
            session = await asyncio.to_thread(media_session, request, payload["contact_phone"])
            data, mime = bytearray(), None
            async for chunk in media.speech_output.synthesize(payload["text"]):
                media.check(session)
                if mime is not None and mime != chunk.mime_type:
                    raise ValueError("inconsistent speech format")
                mime = chunk.mime_type
                if len(data) + len(chunk.data) > 12_000_000:
                    raise ValueError("speech output too long")
                data.extend(chunk.data)
            await asyncio.to_thread(media_session, request, payload["contact_phone"])
            if not data or not mime:
                raise ValueError("empty speech output")
            return Response(bytes(data), media_type=mime, headers={"Cache-Control": "no-store"})
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "speech reply unavailable") from None
