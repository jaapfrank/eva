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

    @app.get("/internal/v1/dashboard/initiatives")
    def initiatives(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "initiative scope is server-bound")
        return JSONResponse(invoke("dashboard_initiatives"), headers={"Cache-Control": "no-store"})

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
