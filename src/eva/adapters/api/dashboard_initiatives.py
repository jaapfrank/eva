"""Authenticated owner routes for Eva's self-chosen work."""
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response


def install_dashboard_initiatives(app, *, access, runtime):
    def invoke(name, *args):
        call = getattr(runtime, name, None)
        if not callable(call):
            raise HTTPException(503, "owner initiatives unavailable")
        try:
            return call(*args)
        except LookupError as error:
            raise HTTPException(404, "initiative unavailable") from error
        except ValueError as error:
            raise HTTPException(422, "invalid initiative request") from error
        except Exception as error:
            raise HTTPException(503, "owner initiatives unavailable") from error

    @app.get("/internal/v1/dashboard/initiatives/history")
    def history(request: Request):
        access.require(request)
        if request.query_params:raise HTTPException(422,"initiative history scope is server-bound")
        from eva.bootstrap.dashboard_learning import initiative_history
        try:result=initiative_history(runtime)
        except Exception:raise HTTPException(503,"initiative history unavailable") from None
        return JSONResponse(result,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

    @app.get("/internal/v1/dashboard/initiatives")
    def initiatives(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "initiative scope is server-bound")
        return JSONResponse(invoke("dashboard_initiatives"), headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/household-provisions")
    def household_provisions(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "household provision scope is server-bound")
        return JSONResponse(invoke("dashboard_household_provisions"),
                            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/household-provisions/register")
    def register_household_provision(request: Request, payload: dict):
        access.require(request)
        if (request.query_params or set(payload) != {"request_id"}
                or not isinstance(payload["request_id"], str)
                or not 1 <= len(payload["request_id"]) <= 160
                or not payload["request_id"].strip()):
            raise HTTPException(422, "invalid household provision registration")
        # Source, household, evidence and verifier callbacks belong to the host.
        return JSONResponse(invoke("dashboard_register_household_provision",
                                   payload["request_id"]),
                            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/household-provisions/command")
    def household_provision_command(request: Request, payload: dict):
        access.require(request)
        import re
        fields = {"provision_id", "action", "expected_revision", "binding_sha256", "request_id"}
        if (request.query_params or set(payload) != fields
                or not isinstance(payload["action"], str)
                or payload["action"] not in {"adopt", "pause", "resume", "reject", "retire", "stop"}
                or type(payload["expected_revision"]) is not int
                or payload["expected_revision"] < 1
                or not isinstance(payload["binding_sha256"], str)
                or re.fullmatch("[0-9a-f]{64}", payload["binding_sha256"]) is None
                or any(not isinstance(payload[key], str)
                       or not 1 <= len(payload[key]) <= 160 or not payload[key].strip()
                       for key in ("provision_id", "request_id"))):
            raise HTTPException(422, "invalid household provision command")
        return JSONResponse(invoke("dashboard_household_provision_command",
            payload["provision_id"], payload["action"], payload["expected_revision"],
            payload["binding_sha256"], payload["request_id"]),
            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/initiatives/review/abandon")
    def abandon_review(request: Request, payload: dict):
        access.require(request)
        if request.query_params or set(payload) != {"thread_id", "expected_revision"}:
            raise HTTPException(422, "invalid private review recovery request")
        return JSONResponse(invoke("dashboard_abandon_private_review",
            payload["thread_id"], payload["expected_revision"]),
            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/initiatives/stop")
    def stop(request: Request, payload: dict):
        access.require(request)
        if request.query_params or set(payload) != {"intent_id"}:
            raise HTTPException(422, "invalid initiative stop request")
        return JSONResponse(invoke("dashboard_stop_initiative", payload["intent_id"]),
                            headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/initiatives/{intent_id}/artifact")
    def artifact(request: Request, intent_id: str):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "initiative scope is server-bound")
        result = invoke("dashboard_initiative_artifact", intent_id)
        return Response(result["content"], media_type="text/markdown", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(result["filename"], safe=""),
            "X-Content-SHA256": result["sha256"],
        })

    @app.post("/internal/v1/dashboard/initiatives/feedback")
    def feedback(request: Request, payload: dict):
        access.require(request)
        if request.query_params or set(payload) != {"intent_id", "feedback"}:
            raise HTTPException(422, "invalid initiative feedback")
        return JSONResponse(invoke("dashboard_initiative_feedback", payload["intent_id"], payload["feedback"]),
                            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/initiatives/revoke")
    def revoke(request: Request, payload: dict):
        access.require(request)
        if request.query_params or payload:
            raise HTTPException(422, "initiative mandate scope is server-bound")
        return JSONResponse(invoke("dashboard_revoke_initiative_mandate"),
                            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/initiatives/mandate/revise")
    def revise(request: Request, payload: dict):
        access.require(request)
        if request.query_params or set(payload) != {"expected_sha256", "candidate_sha256"}:
            raise HTTPException(422, "invalid initiative mandate revision")
        return JSONResponse(invoke("dashboard_revise_initiative_mandate",
            payload["expected_sha256"], payload["candidate_sha256"]),
            headers={"Cache-Control": "no-store"})
