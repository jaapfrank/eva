"""Thin owner dashboard adapter for the existing delegated-work lifecycle."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def install_dashboard_work(app, *, access, runtime):
    def invoke(operation, work_id=None):
        call = getattr(runtime, "dashboard_delegated_work", None)
        if not callable(call):
            raise HTTPException(503, "owner work unavailable")
        try:
            result = call(operation, work_id)
        except ValueError as error:
            raise HTTPException(422, "invalid owner work request") from error
        except Exception as error:
            raise HTTPException(503, "owner work unavailable") from error
        status = 404 if result.get("status") == "not_found" else 200
        return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/work")
    def jobs(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "work scope is server-bound")
        return invoke("list")

    @app.post("/internal/v1/dashboard/work/{operation}")
    def operation(request: Request, operation: str, payload: dict):
        access.require(request)
        if (request.query_params or operation not in {"get", "result", "stop", "resume"}
                or set(payload) != {"work_id"}
                or not isinstance(payload["work_id"], str)
                or not 1 <= len(payload["work_id"]) <= 256):
            raise HTTPException(422, "invalid owner work request")
        return invoke(operation, payload["work_id"])
