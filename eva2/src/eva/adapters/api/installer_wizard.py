"""Temporary loopback-only entry page for Eva's future installer wizard."""

from __future__ import annotations

import argparse
import hmac
import html
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from eva.application.conversation import load_current_identity_pack
from eva.application.identity_drafts import identity_presentation_diff
from eva.application.soul_markdown import (
    SoulMarkdownError,
    soul_markdown_sha256,
    soul_markdown_to_draft,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack
from eva.operations.installer_preflight import (
    InstallerPreflightError,
    inspect_host,
    load_platform_lock,
    preflight,
)


_COOKIE = "eva_installer_session"
_MAX_BOOTSTRAP_AGE_SECONDS = 600
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@dataclass(slots=True)
class InstallerBootstrap:
    token: str
    created_at: float
    clock: Callable[[], float] = time.monotonic
    session: str | None = field(default=None, init=False)

    @classmethod
    def create(cls, *, clock: Callable[[], float] = time.monotonic) -> "InstallerBootstrap":
        return cls(token=secrets.token_urlsafe(32), created_at=clock(), clock=clock)

    def exchange(self, candidate: object) -> str:
        if self.session is not None or self.clock() - self.created_at > _MAX_BOOTSTRAP_AGE_SECONDS:
            raise HTTPException(status_code=401, detail="installer bootstrap expired")
        if not isinstance(candidate, str) or not hmac.compare_digest(candidate, self.token):
            raise HTTPException(status_code=401, detail="installer bootstrap denied")
        self.session = secrets.token_urlsafe(32)
        return self.session

    def require_session(self, request: Request) -> None:
        candidate = request.cookies.get(_COOKIE)
        if self.session is None or not isinstance(candidate, str) or not hmac.compare_digest(candidate, self.session):
            raise HTTPException(status_code=401, detail="installer session required")


def _loopback_request(request: Request) -> None:
    raw_host = request.headers.get("host", "").strip()
    if raw_host.startswith("["):
        host = raw_host[1:].split("]", 1)[0].casefold()
    else:
        host = raw_host.split(":", 1)[0].casefold()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=400, detail="installer only accepts loopback host")
    if request.client is not None:
        try:
            if not ipaddress.ip_address(request.client.host).is_loopback:
                raise HTTPException(status_code=400, detail="installer only accepts loopback client")
        except ValueError:
            raise HTTPException(status_code=400, detail="installer client is invalid")


def _same_origin_or_absent(request: Request) -> None:
    """Reject a cross-origin write, even when the result is only a preview."""

    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.headers.get('host')}":
        raise HTTPException(status_code=403, detail="installer origin denied")


def _safe_receipt(value: dict[str, object]) -> dict[str, object]:
    expected = {"contract", "read_only", "status", "platform_lock_claim_state", "host", "checks", "next", "non_claims"}
    if set(value) != expected or value.get("contract") != "EvaInstallerPreflight/v1" or value.get("read_only") is not True:
        raise ValueError("installer preflight receipt is invalid")
    if value.get("status") not in {"ready", "blocked"} or not isinstance(value.get("checks"), list):
        raise ValueError("installer preflight receipt is invalid")
    return value


def _page(receipt: dict[str, object], identity: IdentityPack) -> str:
    status = html.escape(str(receipt["status"]))
    rows = "".join(
        f"<li class=\"{html.escape(str(item.get('state')))}\"><strong>{html.escape(str(item.get('id')))}</strong> {html.escape(str(item.get('detail')))}</li>"
        for item in receipt["checks"]
        if isinstance(item, dict)
    )
    next_step = html.escape(str(receipt["next"]))
    name = html.escape(identity.display_name)
    return f"""<!doctype html><html lang=\"nl\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Eva installeren</title><style>body{{font:16px system-ui;margin:0;background:#07111c;color:#edf6ff;display:grid;min-height:100vh;place-items:center}}main{{max-width:720px;padding:2rem}}section{{background:#102337;border:1px solid #31516d;border-radius:18px;padding:1.5rem;margin:1rem 0}}.blocked{{color:#ffca78}}.passed{{color:#86f4ba}}small{{color:#aec4d7}}li{{margin:.7rem 0}}textarea{{box-sizing:border-box;width:100%;min-height:9rem;background:#07111c;color:#edf6ff;border:1px solid #52718d;border-radius:8px;padding:.8rem;font:14px ui-monospace,monospace}}button{{margin-top:.7rem;background:#2b8a61;color:white;border:0;border-radius:8px;padding:.65rem .9rem;font-weight:700}}#soul-result{{white-space:pre-wrap}}</style><main><p><small>EVA · lokale installatiecontrole · alleen-lezen</small></p><h1>Voordat Eva iets installeert</h1><section><h2 class=\"{status}\">{status}</h2><p>{next_step}</p><ul>{rows}</ul><p><small>Deze pagina heeft geen connector-, secret- of installatieknop. Zij toont alleen waarom Eva wel of niet verder mag.</small></p></section><section><h2>Hoe {name} zich aan jou voorstelt</h2><p>Plak optioneel een kleine <code>SOUL.md</code>. Je ziet alleen een voorstelverschil. Niets wordt bewaard, ondertekend of actief gemaakt.</p><textarea id=\"soul\" maxlength=\"8192\" spellcheck=\"false\" placeholder=\"# Eva\\n\\n## Naam\\nEva\\n\\n## Temperament\\nwarm, nieuwsgierig\"></textarea><button type=\"button\" id=\"soul-preview\">Toon voorstel</button><p id=\"soul-result\" role=\"status\"></p></section></main><script>const result=document.getElementById('soul-result');document.getElementById('soul-preview').addEventListener('click',async()=>{{result.textContent='Voorstel controleren…';const response=await fetch('/internal/v1/identity/soul-import/diff',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{draft_id:'installer-preview',soul_markdown:document.getElementById('soul').value}})}});if(!response.ok){{result.textContent='Dit voorstel is niet geldig. Eva heeft niets bewaard of gewijzigd.';return}}const preview=await response.json();result.textContent=preview.changes.map(change=>`${{change.field}}:\n  was: ${{JSON.stringify(change.before)}}\n  wordt: ${{JSON.stringify(change.after)}}`).join('\n\n')}}</script></html>"""


def create_installer_wizard(
    receipt: dict[str, object],
    bootstrap: InstallerBootstrap,
    identity: IdentityPack | None = None,
) -> FastAPI:
    receipt = _safe_receipt(receipt)
    identity = identity or load_current_identity_pack()
    app = FastAPI(title="Eva local installer", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def installer_error(_request: Request, error: HTTPException) -> JSONResponse:
        headers = dict(_SECURITY_HEADERS)
        if error.headers:
            headers.update(error.headers)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=headers)

    @app.get("/bootstrap")
    def bootstrap_page(request: Request) -> HTMLResponse:
        _loopback_request(request)
        body = """<!doctype html><meta charset=\"utf-8\"><title>Eva installeren</title><script>const token=location.hash.slice(1);history.replaceState(null,'','/bootstrap');fetch('/internal/v1/bootstrap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})}).then(r=>r.ok?location.replace('/'):document.body.textContent='De lokale installatiesessie kon niet starten.');</script>"""
        return HTMLResponse(body, headers=_SECURITY_HEADERS)

    @app.post("/internal/v1/bootstrap")
    async def exchange(request: Request) -> Response:
        _loopback_request(request)
        _same_origin_or_absent(request)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="installer bootstrap is invalid")
        if not isinstance(payload, dict) or set(payload) != {"token"}:
            raise HTTPException(status_code=422, detail="installer bootstrap is invalid")
        session = bootstrap.exchange(payload["token"])
        response = Response(status_code=204, headers=_SECURITY_HEADERS)
        response.set_cookie(_COOKIE, session, httponly=True, samesite="strict", secure=False, max_age=_MAX_BOOTSTRAP_AGE_SECONDS)
        return response

    @app.get("/")
    def home(request: Request) -> HTMLResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        return HTMLResponse(_page(receipt, identity), headers=_SECURITY_HEADERS)

    @app.get("/internal/v1/preflight")
    def report(request: Request) -> JSONResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        return JSONResponse(receipt, headers=_SECURITY_HEADERS)

    @app.post("/internal/v1/identity/soul-import/diff")
    async def soul_import_diff(request: Request) -> JSONResponse:
        """Preview a human identity proposal without retaining or activating it."""

        _loopback_request(request)
        bootstrap.require_session(request)
        _same_origin_or_absent(request)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        if not isinstance(payload, dict) or set(payload) != {"draft_id", "soul_markdown"}:
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        draft_id, markdown = payload["draft_id"], payload["soul_markdown"]
        if not isinstance(draft_id, str) or not isinstance(markdown, str):
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        try:
            draft = soul_markdown_to_draft(markdown, draft_id=draft_id, base=identity)
            changes = identity_presentation_diff(draft, identity)
        except SoulMarkdownError:
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        return JSONResponse(
            {
                "contract": "EvaInstallerSoulMarkdownPreview/v1",
                "read_only": True,
                "draft_id": draft.draft_id,
                "source_sha256": soul_markdown_sha256(markdown),
                "base_pack_sha256": canonical_sha256(identity),
                "changes": [
                    {"field": item.field, "before": item.before, "after": item.after}
                    for item in changes
                ],
                "non_claims": [
                    "SOUL.md is niet opgeslagen of geactiveerd.",
                    "SOUL.md kan geen prompt, tool, policy, geheugen of permissie wijzigen.",
                ],
            },
            headers=_SECURITY_HEADERS,
        )

    return app


def _loopback_host(value: str) -> str:
    try:
        if not ipaddress.ip_address(value).is_loopback:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError("installer bind address must be loopback") from error
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eva-installer")
    parser.add_argument("--platform-lock", type=Path, default=Path("release/platform-lock.json"))
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("installer port must be between 1024 and 65535")
    try:
        receipt = preflight(load_platform_lock(args.platform_lock), inspect_host())
    except InstallerPreflightError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 2
    bootstrap = InstallerBootstrap.create()
    print(f"Open lokaal: http://{args.host}:{args.port}/bootstrap#{bootstrap.token}")
    uvicorn.run(create_installer_wizard(receipt, bootstrap), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
