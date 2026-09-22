"""Device-authenticated transport for explicitly composed household work.

This adapter never chooses a speaker identity, opens private work, starts model
calls, or turns periodic delivery polling into user conversation messages.
"""
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from pydantic import Field
from eva.adapters.api.household_voice_session import HouseholdDevice


class HouseholdWorkRequest(HouseholdDevice):
    session_id: UUID | None = None
    work_id: str | None = Field(default=None, min_length=1, max_length=240)
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    disposition: Literal["offered", "completed", "interrupted", "ambiguous"] | None = None


def install_household_work(app: FastAPI, *, admit, server=None):
    @app.post("/internal/v1/ha-voice/session/work/{operation}")
    def work(request: Request, operation: str, payload: HouseholdWorkRequest):
        admit(request, payload)
        if server is None:
            raise HTTPException(503, "Household work unavailable")
        basic = operation in {"open", "renew", "close", "poll"}
        if (operation not in {"open", "renew", "close", "poll", "authorize", "ack"}
                or request.query_params
                or ((payload.session_id is None) != (operation == "open"))
                or (basic and any(value is not None for value in
                    (payload.work_id, payload.result_sha256, payload.disposition)))
                or (not basic and (payload.work_id is None or payload.result_sha256 is None))
                or ((payload.disposition is not None) != (operation == "ack"))):
            raise HTTPException(422, "Invalid household work operation")
        credentials = dict(token=request.headers["Authorization"].removeprefix("Bearer "),
            satellite_id=payload.satellite_id, device_id=payload.device_id)
        try:
            if operation == "open":
                binding = server.open_session(**credentials)
                server.authenticate(**credentials)
                result = {"state": "open", "session_id": binding["session_id"]}
            elif operation in {"renew", "close"}:
                server.authenticate(**credentials)
                server.sessions.read(str(payload.session_id), payload.satellite_id,
                    payload.device_id, operation=operation)
                server.authenticate(**credentials)
                result = {"state": "closed" if operation == "close" else "open",
                          "session_id": str(payload.session_id)}
            else:
                service = server.session(**credentials, session_id=str(payload.session_id))
                turn = service.control_turn()
                if operation == "poll":
                    claim = service.poll(turn)
                    result = {"state": "idle" if claim is None else "claimed", "result": claim}
                else:
                    binding = service.result_delivery(turn, operation,
                        work_id=payload.work_id, result_sha256=payload.result_sha256,
                        disposition=payload.disposition)
                    result = {"state": "unavailable" if binding is None else operation,
                              "result": binding}
                service.current()
        except (ValueError, RuntimeError, OSError):
            raise HTTPException(403, "Household work authority unavailable") from None
        return {"contract": "EvaHouseholdWorkReturn/v1", **result}
