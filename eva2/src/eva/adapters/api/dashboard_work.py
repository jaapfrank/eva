"""Thin owner dashboard adapter for the existing delegated-work lifecycle."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def install_dashboard_work(app, *, access, runtime):
    def invoke(operation, work_id=None, search=None):
        call = getattr(runtime, "dashboard_delegated_work", None)
        if not callable(call):
            raise HTTPException(503, "owner work unavailable")
        try:
            result = call(operation, work_id, **({"search": search} if search is not None else {}))
        except ValueError as error:
            raise HTTPException(422, "invalid owner work request") from error
        except Exception as error:
            raise HTTPException(503, "owner work unavailable") from error
        status = 404 if result.get("status") == "not_found" else 200
        return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/work/return/{operation}")
    def work_return(request: Request, operation: str, payload: dict):
        access.require(request)
        import re
        if (request.query_params or operation not in {"poll", "ack"}
                or (operation == "poll" and payload != {})
                or (operation == "ack" and (set(payload) != {"work_id", "operation_id"}
                    or not isinstance(payload["work_id"], str)
                    or not 1 <= len(payload["work_id"]) <= 240
                    or not isinstance(payload["operation_id"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", payload["operation_id"]) is None))):
            raise HTTPException(422, "invalid private return request")
        try:
            result = runtime.dashboard_delegated_work("return_"+operation, **payload)
        except Exception:
            raise HTTPException(403, "private return unavailable") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/work")
    def jobs(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "work scope is server-bound")
        return invoke("list")

    @app.post("/internal/v1/dashboard/work/search")
    def search_jobs(request: Request, payload: dict):
        access.require(request)
        if (request.query_params or not {"query"} <= set(payload) <= {"query", "before_work_id"}
                or not isinstance(payload["query"], str) or not 1 <= len(payload["query"].strip()) <= 200
                or ("before_work_id" in payload and (
                    not isinstance(payload["before_work_id"], str)
                    or not 1 <= len(payload["before_work_id"]) <= 240))):
            raise HTTPException(422, "invalid owner work search")
        return invoke("search", search=payload)

    @app.post("/internal/v1/dashboard/work/{operation}")
    def operation(request: Request, operation: str, payload: dict):
        access.require(request)
        key = "schedule_id" if operation in {"schedule_cancel", "schedule_cancel_chain"} else "work_id"
        if (request.query_params or operation not in {"get", "result", "stop", "resume", "schedule_cancel", "schedule_cancel_chain"}
                or set(payload) != {key}
                or not isinstance(payload[key], str)
                or not 1 <= len(payload[key]) <= 256):
            raise HTTPException(422, "invalid owner work request")
        return invoke(operation, payload[key])
