"""Owner-only API routes for Fase 5A/5B Household Observation & 7-Day Stability Gate."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.adapters.api.installer_request_json import read_small_json_request
from eva.application.household_observation import HouseholdObservationLedger

_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _has_explicit_owner_authority(access: Any, runtime: Any) -> bool:
    return bool(
        getattr(access, "token", None) is not None
        or getattr(runtime, "_dashboard_owner_principal_id", None)
        or getattr(runtime, "_optional_model_providers", None)
        or getattr(runtime, "_model_provider_control", None)
    )


def _resolve_ledger(runtime: Any) -> HouseholdObservationLedger:
    ledger = getattr(runtime, "_household_observation", None)
    if isinstance(ledger, HouseholdObservationLedger):
        return ledger
    fallback = HouseholdObservationLedger()
    try:
        setattr(runtime, "_household_observation", fallback)
    except Exception:
        pass
    return fallback


def _enforce_mutation_csrf_and_headers(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422, detail="query parameters are not allowed")
    ct = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ct != "application/json":
        raise HTTPException(status_code=415, detail="application/json content-type required")
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        raise HTTPException(status_code=403, detail="cross-site request refused")
    origin = request.headers.get("origin")
    if origin is not None:
        origin_host = urlsplit(origin).netloc.lower()
        req_host = (request.headers.get("host") or "").lower()
        if not origin_host or (req_host and origin_host != req_host):
            raise HTTPException(status_code=403, detail="cross-origin request refused")
    if request.headers.get("x-eva-owner-confirm") != "1":
        raise HTTPException(status_code=403, detail="missing X-Eva-Owner-Confirm header")


def install_dashboard_household_observation(
    app: FastAPI,
    *,
    access: Any,
    runtime: Any,
) -> None:
    @app.get("/internal/v1/dashboard/household-observation")
    async def get_household_observation(request: Request) -> JSONResponse:
        access.require(request)
        if not _has_explicit_owner_authority(access, runtime):
            raise HTTPException(status_code=403, detail="explicit owner authority required")
        if request.query_params:
            raise HTTPException(status_code=422, detail="query parameters are not allowed")
        ledger = _resolve_ledger(runtime)
        payload = await asyncio.to_thread(ledger.snapshot)
        return JSONResponse(payload, headers=_RESPONSE_HEADERS)

    @app.post("/internal/v1/dashboard/household-observation/accept")
    async def accept_household_release(request: Request) -> JSONResponse:
        access.require(request)
        if not _has_explicit_owner_authority(access, runtime):
            raise HTTPException(status_code=403, detail="explicit owner authority required")
        _enforce_mutation_csrf_and_headers(request)
        body = await read_small_json_request(request, 4096, "household_observation_accept")
        if (
            not isinstance(body, dict)
            or set(body) != {"note"}
            or not isinstance(body.get("note"), str)
        ):
            raise HTTPException(status_code=422, detail="exact JSON object {'note': str} required")
        note = body["note"].strip()
        ledger = _resolve_ledger(runtime)
        try:
            updated = await asyncio.to_thread(ledger.record_owner_acceptance, note=note)
        except ValueError as exc:
            msg = str(exc)
            status_code = 409 if "7 contiguous days" in msg else 400
            raise HTTPException(status_code=status_code, detail=msg) from None
        return JSONResponse(updated, headers=_RESPONSE_HEADERS)


__all__ = ["install_dashboard_household_observation"]
