"""Owner-bound inspection and deliberate changes using existing memory routes."""
from uuid import UUID
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from eva.application.conversation import ConversationConflict


def install_dashboard_memory(app, *, access, runtime):
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

    def require_source(request, source_id):
        access.require(request)
        try:
            if request.query_params or str(UUID(source_id)) != source_id:
                raise ValueError()
        except ValueError as error:
            raise HTTPException(422, "memory source request is invalid", headers=headers) from error
        if request.method == "POST" and request.headers.get("origin") not in {
            None, str(request.base_url).rstrip("/"),
        }:
            raise HTTPException(403, "memory change origin differs", headers=headers)

    def invoke(name, *args):
        method = getattr(runtime, name, None)
        if not callable(method):
            raise HTTPException(503, "owner memory unavailable", headers=headers)
        try:
            return method(*args)
        except LookupError as error:
            raise HTTPException(404, "owner memory source unavailable", headers=headers) from error
        except ConversationConflict as error:
            raise HTTPException(409, str(error), headers=headers) from error
        except ValueError as error:
            raise HTTPException(422, "memory change is invalid", headers=headers) from error
        except Exception as error:
            raise HTTPException(503, "memory change could not be confirmed; refresh before retrying", headers=headers) from error

    @app.get("/internal/v1/dashboard/memory-sources")
    def dashboard_memory_sources(request: Request) -> JSONResponse:
        from eva.application.people_memory_overview import MemorySourcePage, memory_source_page_request

        access.require(request)
        try:
            if (set(request.query_params) - {"limit", "before"}
                    or len(request.query_params.multi_items()) != len(request.query_params)):
                raise ValueError("unknown or repeated query field")
            raw_limit = request.query_params.get("limit", "20")
            if not raw_limit.isascii() or not raw_limit.isdecimal():
                raise ValueError("invalid limit")
            limit = int(raw_limit)
            before = request.query_params.get("before")
            memory_source_page_request(limit, before)
        except ValueError as error:
            raise HTTPException(422, "memory source page is invalid") from error
        projection = getattr(runtime, "dashboard_memory_sources", None)
        if not callable(projection):
            raise HTTPException(503, "owner memory sources unavailable")
        try:
            page = projection(limit=limit, before=before)
            if not isinstance(page, MemorySourcePage):
                raise ValueError("invalid memory page")
        except Exception as error:
            raise HTTPException(503, "owner memory sources unavailable") from error
        return JSONResponse(page.to_payload(), headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/memory-sources/{source_id}")
    def detail(request: Request, source_id: str):
        require_source(request, source_id)
        return JSONResponse(invoke("dashboard_memory_source", source_id), headers=headers)

    @app.post("/internal/v1/dashboard/memory-sources/{source_id}/forget")
    def forget(request: Request, source_id: str, payload: dict):
        require_source(request, source_id)
        if payload != {"confirm": True} or type(payload.get("confirm")) is not bool:
            raise HTTPException(422, "explicit source erasure confirmation is required", headers=headers)
        return JSONResponse(invoke("dashboard_forget_memory_source", source_id), headers=headers)

    @app.post("/internal/v1/dashboard/memory-sources/{source_id}/correct")
    def correct(request: Request, source_id: str, payload: dict):
        require_source(request, source_id)
        if (set(payload) != {"text"} or not isinstance(payload["text"], str)
                or not payload["text"].strip() or len(payload["text"]) > 12000):
            raise HTTPException(422, "memory correction is invalid", headers=headers)
        return JSONResponse(invoke("dashboard_correct_memory_source", source_id, payload["text"]),
                            status_code=202, headers=headers)

    # Browser input selects text/source only; host settings bind person and installation.
    @app.get("/internal/v1/dashboard/memory-view")
    def view(request: Request):
        access.require(request)
        if request.query_params: raise HTTPException(422, "memory view scope is server-bound", headers=headers)
        from eva.bootstrap.dashboard_memory import memory_view
        try: return JSONResponse(memory_view(runtime), headers=headers)
        except Exception: raise HTTPException(503, "owner memory view unavailable", headers=headers) from None

    @app.post("/internal/v1/dashboard/memory-view/search")
    def search(request: Request, payload: dict):
        access.require(request)
        if request.query_params or set(payload) != {"text"}: raise HTTPException(422, "memory search is invalid", headers=headers)
        if request.headers.get("origin") not in {None, str(request.base_url).rstrip("/")}: raise HTTPException(403, "memory search origin differs", headers=headers)
        from eva.bootstrap.dashboard_memory import memory_view
        try: return JSONResponse(memory_view(runtime, payload["text"]), headers=headers)
        except ValueError: raise HTTPException(422, "memory search is invalid", headers=headers) from None
        except Exception: raise HTTPException(503, "owner memory search unavailable", headers=headers) from None

    @app.get("/internal/v1/dashboard/memory-view/sources/{source_id}")
    def source_presentation(request: Request, source_id: str):
        require_source(request, source_id)
        from eva.bootstrap.dashboard_memory import source_view
        try: return JSONResponse(source_view(runtime, source_id), headers=headers)
        except LookupError: raise HTTPException(404, "owner source unavailable", headers=headers) from None
        except Exception: raise HTTPException(503, "owner source view unavailable", headers=headers) from None

    @app.post("/internal/v1/dashboard/memory-view/sources/{source_id}/{operation}")
    def source_change(request: Request, source_id: str, operation: str, payload: dict):
        require_source(request, source_id)
        from eva.bootstrap.dashboard_memory import correct_source, forget_source
        if ((operation == "forget" and (payload != {"confirm": True} or type(payload.get("confirm")) is not bool))
            or (operation == "correct" and set(payload) != {"text"}) or operation not in {"correct", "forget"}):
            raise HTTPException(422, "memory change is invalid", headers=headers)
        try:
            result = forget_source(runtime, source_id) if operation == "forget" else correct_source(runtime, source_id, payload["text"])
            return JSONResponse(result, status_code=202 if operation == "correct" else 200, headers=headers)
        except LookupError: raise HTTPException(404, "owner source unavailable", headers=headers) from None
        except ValueError: raise HTTPException(422, "memory change is invalid", headers=headers) from None
        except ConversationConflict: raise HTTPException(409, "memory change could not be applied; refresh before retrying", headers=headers) from None
        except Exception: raise HTTPException(503, "memory change could not be confirmed; refresh before retrying", headers=headers) from None
