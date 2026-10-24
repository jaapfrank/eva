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

    @app.get("/internal/v1/dashboard/initiatives/state")
    def housemate_state(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "housemate overview scope is server-bound")
        from eva.bootstrap.dashboard_housemate import housemate_state as project
        try:
            result = project(runtime)
        except Exception:
            raise HTTPException(503, "owner housemate overview unavailable") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

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

    @app.get("/internal/v1/dashboard/household-provisions/versions")
    def household_provision_versions(request: Request):
        access.require(request)
        if request.query_params:
            raise HTTPException(422, "household provision scope is server-bound")
        return JSONResponse(invoke("dashboard_household_provision_version_choices"),
                            headers={"Cache-Control": "no-store"})

    @app.get("/internal/v1/dashboard/household-provisions/{provision_id}/history")
    def household_provision_history(request: Request, provision_id: str):
        access.require(request)
        if request.query_params or not 1 <= len(provision_id) <= 160 or not provision_id.strip():
            raise HTTPException(422, "invalid household provision history request")
        return JSONResponse(invoke("dashboard_household_provision_version_history", provision_id),
                            headers={"Cache-Control": "no-store"})

    def version_mutation(request, payload, selector, method):
        access.require(request)
        import re
        fields = {"provision_id", "expected_revision", "binding_sha256", "request_id"}
        if selector is not None:
            fields.add(selector)
        if (request.query_params or set(payload) != fields
                or type(payload["expected_revision"]) is not int or payload["expected_revision"] < 1
                or any(not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 160
                       or not payload[key].strip() for key in ("provision_id", "request_id"))
                or any(not isinstance(payload[key], str)
                       or re.fullmatch("[0-9a-f]{64}", payload[key]) is None
                       for key in (["binding_sha256", selector] if selector else ["binding_sha256"]))):
            raise HTTPException(422, "invalid household provision version request")
        # Selection identifiers resolve existing reviewed host records. No caller
        # source, evaluation, principal, installation or delivery assertion.
        args = [payload["provision_id"]]
        if selector is not None:
            args.append(payload[selector])
        args.extend([payload["expected_revision"], payload["binding_sha256"], payload["request_id"]])
        return JSONResponse(invoke(method, *args), headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/household-provisions/versions/stage")
    def stage_household_provision_version(request: Request, payload: dict):
        return version_mutation(request, payload, "choice_id", "dashboard_stage_household_provision_version")

    @app.post("/internal/v1/dashboard/household-provisions/versions/change")
    def change_household_provision_version(request: Request, payload: dict):
        return version_mutation(request, payload, "version_id", "dashboard_change_household_provision_version")

    @app.post("/internal/v1/dashboard/household-provisions/versions/undo")
    def undo_household_provision_version(request: Request, payload: dict):
        return version_mutation(request, payload, None, "dashboard_undo_household_provision_version")

    def trial_mutation(request, payload, action):
        access.require(request)
        import re
        extra = {"authorize": ("expires_at_epoch", "max_effects"),
                 "evaluate": ("working", "useful", "feedback"),
                 "acknowledge": ("notice_id", "nonce")}[action]
        fields = {"provision_id", "expected_revision", "binding_sha256", "request_id", *extra}
        if (request.query_params or set(payload) != fields
                or type(payload["expected_revision"]) is not int or payload["expected_revision"] < 1
                or any(not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 160
                       or not payload[key].strip() for key in ("provision_id", "request_id"))
                or not isinstance(payload["binding_sha256"], str)
                or re.fullmatch("[0-9a-f]{64}", payload["binding_sha256"]) is None):
            raise HTTPException(422, "invalid household provision trial request")
        if action == "authorize":
            valid = (type(payload["expires_at_epoch"]) is int and payload["expires_at_epoch"] > 0
                     and type(payload["max_effects"]) is int and 1 <= payload["max_effects"] <= 16)
        elif action == "evaluate":
            valid = (type(payload["working"]) is bool and type(payload["useful"]) is bool
                     and isinstance(payload["feedback"], str) and bool(payload["feedback"].strip())
                     and len(payload["feedback"]) <= 1000)
        else:
            valid = all(isinstance(payload[key], str) and re.fullmatch("[0-9a-f]{64}", payload[key])
                        for key in extra)
        if not valid:
            raise HTTPException(422, "invalid household provision trial choices")
        method = {"authorize": "dashboard_authorize_household_provision_trial",
                  "evaluate": "dashboard_evaluate_household_provision_trial",
                  "acknowledge": "dashboard_acknowledge_household_provision_trial_notice"}[action]
        return JSONResponse(invoke(method, payload["provision_id"], *(payload[key] for key in extra),
            payload["expected_revision"], payload["binding_sha256"], payload["request_id"]),
            headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/dashboard/household-provisions/trial/authorize")
    def authorize_household_provision_trial(request: Request, payload: dict):
        return trial_mutation(request, payload, "authorize")

    @app.post("/internal/v1/dashboard/household-provisions/trial/evaluate")
    def evaluate_household_provision_trial(request: Request, payload: dict):
        return trial_mutation(request, payload, "evaluate")

    @app.post("/internal/v1/dashboard/household-provisions/trial/acknowledge")
    def acknowledge_household_provision_trial(request: Request, payload: dict):
        return trial_mutation(request, payload, "acknowledge")

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
