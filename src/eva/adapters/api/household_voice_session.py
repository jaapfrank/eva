"""Continuous household voice uses existing authority, never dashboard identity."""
import secrets
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from eva.adapters.api.native_voice import VoiceRequest as NativeRequest
from eva.adapters.api.voice_turns import VoiceRequest, stream_voice_turn
from eva.bootstrap.native_voice import NativeHouseholdResponder


class HouseholdDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    satellite_id: str = Field(pattern=r"^assist_satellite\.[a-z0-9_]+$", max_length=200)
    device_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class HouseholdSessionRequest(VoiceRequest, HouseholdDevice):
    pass


def install_household_voice_session(app: FastAPI, *, runtime, authority, work_server=None):
    policy = authority.policy
    responder = NativeHouseholdResponder(runtime=runtime, tenant_id=policy.tenant_id,
        installation_id=policy.installation_id, household_id=policy.household_id,
        devices=policy.devices, consent=authority)
    def admit(request, payload):
        expected = "Bearer " + authority.token
        if not secrets.compare_digest(request.headers.get("Authorization", ""), expected):
            raise HTTPException(401, "Household voice credential required")
        try:
            authority.validate_current()
            if policy.devices.get(payload.satellite_id) != payload.device_id:
                raise ValueError("Device differs")
        except (ValueError, RuntimeError, OSError):
            raise HTTPException(403, "Household voice authority unavailable") from None

    from eva.adapters.api.household_work import install_household_work
    verification=getattr(runtime,"_voice_identity",None)
    install_household_work(app, admit=admit, server=work_server, verification=verification)

    @app.post("/internal/v1/ha-voice/session/ready")
    def ready(request: Request, payload: HouseholdDevice):
        admit(request, payload)
        return {"contract": "EvaHouseholdVoiceSessionReady/v1",
                "household_work_returns": work_server is not None}

    @app.post("/internal/v1/ha-voice/session/respond")
    def respond(request: Request, payload: HouseholdSessionRequest):
        admit(request, payload)
        service = None
        try:
            if work_server is None:
                turn = responder.make_turn(NativeRequest(request_id=payload.message_id.hex,
                    satellite_id=payload.satellite_id, device_id=payload.device_id,
                    conversation_id="livekit:"+str(payload.session_id), text=payload.text, language="nl"))
            else:
                service = work_server.session(token=request.headers["Authorization"].removeprefix("Bearer "),
                    satellite_id=payload.satellite_id, device_id=payload.device_id,
                    session_id=str(payload.session_id))
                turn = service.turn(str(payload.message_id), payload.text)
        except (ValueError, RuntimeError, OSError):
            raise HTTPException(403, "Household voice scope unavailable") from None
        personal = None
        if service is not None and verification is not None:
            try:
                personal=verification.private_turn(service.binding,payload.message_id,payload.text,source_turn=turn)
            except (ValueError,RuntimeError,OSError):
                raise HTTPException(403,"Personal voice authority unavailable") from None
        if personal is not None:
            try:
                verification.work_return(service.binding,"renew")
                payload=verification.personal_context(service.binding,payload)
                turn=personal
            except (ValueError, RuntimeError, OSError):
                raise HTTPException(403, "Personal voice context unavailable") from None
        def message_id(value):
            if personal is not None:return 'voice:'+str(payload.session_id)+':'+str(value)

            return turn.conversation_id + ":request:" + (str(value) if service is not None else value.hex)
        def check():
            authority.validate_current()
            if service is not None:
                if personal is None:service.validate_authority(turn)
                elif verification.principal(service.binding)!=turn.principal_id:
                    raise ValueError("Personal voice authority changed")
            runtime.admit_turn_audience(turn)
        return stream_voice_turn(runtime=runtime, payload=payload, turn=turn,
                                 message_id=message_id, check=check, user_label="Spreker",
                                 admit=runtime.admit_voice_turn if personal is not None else None,
                                 request_identity=(lambda:verification.request(service.binding))
                                     if service is not None and verification is not None and personal is None else None)
