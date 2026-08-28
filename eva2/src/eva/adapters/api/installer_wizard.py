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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from eva.application.conversation import load_current_identity_pack
from eva.application.identity_drafts import identity_presentation_diff
from eva.application.soul_markdown import (
    SoulMarkdownError,
    identity_to_soul_markdown,
    soul_markdown_sha256,
    soul_markdown_to_draft,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle
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
    session_created_at: float | None = field(default=None, init=False)

    @classmethod
    def create(
        cls, *, clock: Callable[[], float] = time.monotonic
    ) -> "InstallerBootstrap":
        return cls(token=secrets.token_urlsafe(32), created_at=clock(), clock=clock)

    def exchange(self, candidate: object) -> str:
        if (
            self.session is not None
            or self.clock() - self.created_at > _MAX_BOOTSTRAP_AGE_SECONDS
        ):
            raise HTTPException(status_code=401, detail="installer bootstrap expired")
        if not isinstance(candidate, str) or not hmac.compare_digest(
            candidate, self.token
        ):
            raise HTTPException(status_code=401, detail="installer bootstrap denied")
        self.session = secrets.token_urlsafe(32)
        self.session_created_at = self.clock()
        return self.session

    def require_session(self, request: Request) -> None:
        candidate = request.cookies.get(_COOKIE)
        if (
            self.session is None
            or self.session_created_at is None
            or self.clock() - self.session_created_at > _MAX_BOOTSTRAP_AGE_SECONDS
            or not isinstance(candidate, str)
            or not hmac.compare_digest(candidate, self.session)
        ):
            raise HTTPException(status_code=401, detail="installer session required")

    def invalidate(self) -> None:
        self.session = None
        self.session_created_at = None


def _loopback_request(request: Request) -> None:
    raw_host = request.headers.get("host", "").strip()
    if raw_host.startswith("["):
        host = raw_host[1:].split("]", 1)[0].casefold()
    else:
        host = raw_host.split(":", 1)[0].casefold()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(
            status_code=400, detail="installer only accepts loopback host"
        )
    if request.client is not None:
        try:
            if not ipaddress.ip_address(request.client.host).is_loopback:
                raise HTTPException(
                    status_code=400, detail="installer only accepts loopback client"
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="installer client is invalid")


def _same_origin_or_absent(request: Request) -> None:
    """Reject a cross-origin write, even when the result is only a preview."""

    origin = request.headers.get("origin")
    if (
        origin is not None
        and origin != f"{request.url.scheme}://{request.headers.get('host')}"
    ):
        raise HTTPException(status_code=403, detail="installer origin denied")


def _safe_receipt(value: dict[str, object]) -> dict[str, object]:
    expected = {
        "contract",
        "read_only",
        "status",
        "platform_lock_claim_state",
        "host",
        "checks",
        "next",
        "non_claims",
    }
    if (
        set(value) != expected
        or value.get("contract") != "EvaInstallerPreflight/v1"
        or value.get("read_only") is not True
    ):
        raise ValueError("installer preflight receipt is invalid")
    if value.get("status") not in {"ready", "blocked"} or not isinstance(
        value.get("checks"), list
    ):
        raise ValueError("installer preflight receipt is invalid")
    return value


def _page(
    receipt: dict[str, object],
    identity: IdentityPack,
    *,
    identity_writable: bool,
) -> str:
    status = html.escape(str(receipt["status"]))
    rows = "".join(
        f'<li class="{html.escape(str(item.get("state")))}"><strong>{html.escape(str(item.get("id")))}</strong> {html.escape(str(item.get("detail")))}</li>'
        for item in receipt["checks"]
        if isinstance(item, dict)
    )
    next_step = html.escape(str(receipt["next"]))
    name = html.escape(identity.display_name)
    if identity_writable:
        controls = """<p><small>Je voorstel wordt pas actief na vier zichtbare stappen. De privésleutel blijft buiten de browser en buiten Eva.</small></p><div id="identity-actions"><button type="button" id="soul-stage" disabled>1. Bewaar voorstel</button> <button type="button" id="soul-issue" disabled>2. Onderteken</button> <button type="button" id="soul-activate" disabled>3. Activeer</button> <button type="button" id="soul-rollback" disabled>Vorige identiteit terugzetten</button></div>"""
    else:
        controls = """<p><small>Deze installer is niet met een private identiteit-authority gestart of de platformcontrole is nog geblokkeerd. Je kunt daarom alleen voorvertonen.</small></p>"""
    writable = "true" if identity_writable else "false"
    return f"""<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eva installeren</title><style>body{{font:16px system-ui;margin:0;background:#07111c;color:#edf6ff;display:grid;min-height:100vh;place-items:center}}main{{max-width:720px;padding:2rem}}section{{background:#102337;border:1px solid #31516d;border-radius:18px;padding:1.5rem;margin:1rem 0}}.blocked{{color:#ffca78}}.passed{{color:#86f4ba}}small{{color:#aec4d7}}li{{margin:.7rem 0}}textarea{{box-sizing:border-box;width:100%;min-height:9rem;background:#07111c;color:#edf6ff;border:1px solid #52718d;border-radius:8px;padding:.8rem;font:14px ui-monospace,monospace}}button{{margin-top:.7rem;background:#2b8a61;color:white;border:0;border-radius:8px;padding:.65rem .9rem;font-weight:700}}button:disabled{{opacity:.45}}#soul-result{{white-space:pre-wrap}}</style><main><p><small>EVA · lokale installatiecontrole</small></p><h1>Voordat Eva iets installeert</h1><section><h2 class="{status}">{status}</h2><p>{next_step}</p><ul>{rows}</ul><p><small>Deze pagina heeft geen connector-, secret- of installatieknop. Zij toont alleen waarom Eva wel of niet verder mag.</small></p></section><section><h2>Hoe {name} zich aan jou voorstelt</h2><p><a href="/internal/v1/identity/soul-export" download="SOUL.md">Download de huidige SOUL.md</a></p><p>Plak optioneel een kleine <code>SOUL.md</code>. Je ziet eerst het precieze voorstelverschil.</p><textarea id="soul" maxlength="8192" spellcheck="false" placeholder="# Eva\n\n## Naam\nEva\n\n## Temperament\nwarm, nieuwsgierig"></textarea><button type="button" id="soul-preview">Toon voorstel</button>{controls}<p id="soul-result" role="status"></p></section></main><script>const writable={writable};const result=document.getElementById('soul-result');const soul=document.getElementById('soul');const stageButton=document.getElementById('soul-stage');const issueButton=document.getElementById('soul-issue');const activateButton=document.getElementById('soul-activate');const rollbackButton=document.getElementById('soul-rollback');let state={{}};async function post(path,payload){{const response=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});if(!response.ok)throw new Error('geweigerd');return response.json()}}document.getElementById('soul-preview').addEventListener('click',async()=>{{try{{result.textContent='Voorstel controleren…';const preview=await post('/internal/v1/identity/soul-import/diff',{{draft_id:'installer-preview',soul_markdown:soul.value}});state={{base:preview.base_pack_sha256}};result.textContent=preview.changes.map(change=>`${{change.field}}:\n  was: ${{JSON.stringify(change.before)}}\n  wordt: ${{JSON.stringify(change.after)}}`).join('\n\n');if(writable)stageButton.disabled=false}}catch{{result.textContent='Dit voorstel is niet geldig. Eva heeft niets bewaard of gewijzigd.'}}}});if(writable){{stageButton.addEventListener('click',async()=>{{try{{const staged=await post('/internal/v1/identity/soul-import/stage',{{draft_id:'installer-preview',soul_markdown:soul.value,expected_base_sha256:state.base}});state={{...state,draft:staged.draft_sha256}};result.textContent='Voorstel veilig bewaard. Het is nog niet ondertekend of actief.';issueButton.disabled=false;stageButton.disabled=true;soul.disabled=true}}catch{{result.textContent='Bewaren is geweigerd; de identiteit is niet gewijzigd.'}}}});issueButton.addEventListener('click',async()=>{{try{{const issued=await post('/internal/v1/identity/staged/issue',{{draft_sha256:state.draft,expected_base_sha256:state.base,confirmation:'ONDERTEKENEN'}});state={{...state,candidate:issued.candidate_sha256}};result.textContent='Voorstel ondertekend. Het is nog niet actief.';activateButton.disabled=false;issueButton.disabled=true}}catch{{result.textContent='Ondertekenen is geweigerd; de identiteit is niet gewijzigd.'}}}});activateButton.addEventListener('click',async()=>{{if(!confirm('Wil je deze ondertekende identiteit nu echt activeren?'))return;try{{const activated=await post('/internal/v1/identity/candidate/activate',{{candidate_sha256:state.candidate,expected_current_sha256:state.base,confirmation:'ACTIVEREN'}});state={{...state,active:activated.current_sha256}};result.textContent='De nieuwe identiteit is actief. Je kunt haar hier direct weer terugzetten.';rollbackButton.disabled=false;activateButton.disabled=true}}catch{{result.textContent='Activeren is geweigerd; de huidige identiteit bleef behouden.'}}}});rollbackButton.addEventListener('click',async()=>{{if(!confirm('Wil je de direct vorige identiteit terugzetten?'))return;try{{await post('/internal/v1/identity/rollback',{{expected_current_sha256:state.active,confirmation:'TERUGZETTEN'}});result.textContent='De direct vorige identiteit is teruggezet.';rollbackButton.disabled=true}}catch{{result.textContent='Terugzetten is geweigerd; Eva doet geen stille gok.'}}}})}};</script></html>"""


def create_installer_wizard(
    receipt: dict[str, object],
    bootstrap: InstallerBootstrap,
    identity: IdentityPack | None = None,
    identity_lifecycle: InstallerIdentityLifecycle | None = None,
) -> FastAPI:
    receipt = _safe_receipt(receipt)
    if identity_lifecycle is not None:
        configured_identity = identity_lifecycle.current_identity()
        if identity is not None and canonical_sha256(identity) != canonical_sha256(
            configured_identity
        ):
            raise ValueError("installer identity differs from configured authority")
        identity = configured_identity
    else:
        identity = identity or load_current_identity_pack()
    identity_writable = identity_lifecycle is not None and receipt["status"] == "ready"
    app = FastAPI(
        title="Eva local installer", docs_url=None, redoc_url=None, openapi_url=None
    )

    def current_identity() -> IdentityPack:
        if identity_lifecycle is None:
            assert identity is not None
            return identity
        try:
            return identity_lifecycle.current_identity()
        except IdentityPackLifecycleError as error:
            raise HTTPException(
                status_code=503, detail="installer identity authority unavailable"
            ) from error

    def writable_identity_lifecycle() -> InstallerIdentityLifecycle:
        if not identity_writable or identity_lifecycle is None:
            raise HTTPException(
                status_code=409, detail="installer identity changes are not enabled"
            )
        return identity_lifecycle

    @app.exception_handler(HTTPException)
    async def installer_error(_request: Request, error: HTTPException) -> JSONResponse:
        headers = dict(_SECURITY_HEADERS)
        if error.headers:
            headers.update(error.headers)
        return JSONResponse(
            {"detail": error.detail}, status_code=error.status_code, headers=headers
        )

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
            raise HTTPException(
                status_code=422, detail="installer bootstrap is invalid"
            )
        if not isinstance(payload, dict) or set(payload) != {"token"}:
            raise HTTPException(
                status_code=422, detail="installer bootstrap is invalid"
            )
        session = bootstrap.exchange(payload["token"])
        response = Response(status_code=204, headers=_SECURITY_HEADERS)
        response.set_cookie(
            _COOKIE,
            session,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=_MAX_BOOTSTRAP_AGE_SECONDS,
        )
        return response

    @app.get("/")
    def home(request: Request) -> HTMLResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        return HTMLResponse(
            _page(
                receipt,
                current_identity(),
                identity_writable=identity_writable,
            ),
            headers=_SECURITY_HEADERS,
        )

    @app.get("/internal/v1/preflight")
    def report(request: Request) -> JSONResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        return JSONResponse(receipt, headers=_SECURITY_HEADERS)

    @app.get("/internal/v1/identity/soul-export")
    def soul_export(request: Request) -> PlainTextResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        try:
            markdown = identity_to_soul_markdown(current_identity())
        except SoulMarkdownError as error:
            raise HTTPException(
                status_code=409, detail="SOUL.md export is unavailable"
            ) from error
        return PlainTextResponse(
            markdown,
            headers={
                **_SECURITY_HEADERS,
                "Content-Disposition": 'attachment; filename="SOUL.md"',
            },
        )

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
        if not isinstance(payload, dict) or set(payload) != {
            "draft_id",
            "soul_markdown",
        }:
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        draft_id, markdown = payload["draft_id"], payload["soul_markdown"]
        if not isinstance(draft_id, str) or not isinstance(markdown, str):
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        try:
            base = current_identity()
            draft = soul_markdown_to_draft(markdown, draft_id=draft_id, base=base)
            changes = identity_presentation_diff(draft, base)
        except SoulMarkdownError:
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        return JSONResponse(
            {
                "contract": "EvaInstallerSoulMarkdownPreview/v1",
                "read_only": True,
                "draft_id": draft.draft_id,
                "source_sha256": soul_markdown_sha256(markdown),
                "base_pack_sha256": canonical_sha256(base),
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

    @app.post("/internal/v1/identity/soul-import/stage")
    async def soul_import_stage(request: Request) -> JSONResponse:
        """Persist only a closed, base-bound draft after an explicit preview."""

        _loopback_request(request)
        bootstrap.require_session(request)
        _same_origin_or_absent(request)
        lifecycle = writable_identity_lifecycle()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="SOUL.md staging is invalid")
        if not isinstance(payload, dict) or set(payload) != {
            "draft_id",
            "soul_markdown",
            "expected_base_sha256",
        }:
            raise HTTPException(status_code=422, detail="SOUL.md staging is invalid")
        if not isinstance(payload["draft_id"], str) or not isinstance(
            payload["soul_markdown"], str
        ):
            raise HTTPException(status_code=422, detail="SOUL.md staging is invalid")
        try:
            staged = lifecycle.stage(
                markdown=payload["soul_markdown"],
                draft_id=payload["draft_id"],
                expected_base_sha256=payload["expected_base_sha256"],
            )
        except SoulMarkdownError:
            raise HTTPException(status_code=422, detail="SOUL.md staging is invalid")
        except IdentityPackLifecycleError as error:
            raise HTTPException(
                status_code=409, detail="identity proposal could not be staged"
            ) from error
        return JSONResponse(
            {
                "contract": "EvaInstallerIdentityStage/v1",
                "status": "already_staged" if staged.already_staged else "staged",
                "draft_sha256": staged.draft_sha256,
                "source_sha256": staged.source_sha256,
                "base_pack_sha256": staged.base_pack_sha256,
                "changed_fields": list(staged.changed_fields),
                "active": False,
            },
            headers=_SECURITY_HEADERS,
        )

    @app.post("/internal/v1/identity/staged/issue")
    async def identity_issue(request: Request) -> JSONResponse:
        """Sign one retained draft without exposing the offline key."""

        _loopback_request(request)
        bootstrap.require_session(request)
        _same_origin_or_absent(request)
        lifecycle = writable_identity_lifecycle()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="identity issuance is invalid")
        if not isinstance(payload, dict) or set(payload) != {
            "draft_sha256",
            "expected_base_sha256",
            "confirmation",
        }:
            raise HTTPException(status_code=422, detail="identity issuance is invalid")
        confirmation = payload["confirmation"]
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            confirmation, "ONDERTEKENEN"
        ):
            raise HTTPException(
                status_code=409, detail="identity issuance not confirmed"
            )
        try:
            candidate = lifecycle.issue(
                draft_sha256=payload["draft_sha256"],
                expected_base_sha256=payload["expected_base_sha256"],
            )
        except IdentityPackLifecycleError as error:
            raise HTTPException(
                status_code=409, detail="identity proposal could not be signed"
            ) from error
        return JSONResponse(
            {
                "contract": "EvaInstallerIdentityCandidate/v1",
                "status": "already_issued" if candidate.already_issued else "issued",
                "draft_sha256": candidate.draft_sha256,
                "candidate_sha256": candidate.candidate_sha256,
                "base_pack_sha256": candidate.base_pack_sha256,
                "pack_version": candidate.pack_version,
                "active": False,
            },
            headers=_SECURITY_HEADERS,
        )

    @app.post("/internal/v1/identity/candidate/activate")
    async def identity_activate(request: Request) -> JSONResponse:
        """Atomically activate exactly one signed successor after confirmation."""

        _loopback_request(request)
        bootstrap.require_session(request)
        _same_origin_or_absent(request)
        lifecycle = writable_identity_lifecycle()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=422, detail="identity activation is invalid"
            )
        if not isinstance(payload, dict) or set(payload) != {
            "candidate_sha256",
            "expected_current_sha256",
            "confirmation",
        }:
            raise HTTPException(
                status_code=422, detail="identity activation is invalid"
            )
        confirmation = payload["confirmation"]
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            confirmation, "ACTIVEREN"
        ):
            raise HTTPException(
                status_code=409, detail="identity activation not confirmed"
            )
        try:
            transition = lifecycle.activate(
                candidate_sha256=payload["candidate_sha256"],
                expected_current_sha256=payload["expected_current_sha256"],
            )
        except IdentityPackLifecycleError as error:
            raise HTTPException(
                status_code=409, detail="identity proposal could not be activated"
            ) from error
        return JSONResponse(
            {
                "contract": "EvaInstallerIdentityTransition/v1",
                "status": transition.status,
                "previous_sha256": transition.previous_sha256,
                "current_sha256": transition.current_sha256,
                "rollback_sha256": transition.rollback_sha256,
            },
            headers=_SECURITY_HEADERS,
        )

    @app.post("/internal/v1/identity/rollback")
    async def identity_rollback(request: Request) -> JSONResponse:
        """Restore only the verified direct predecessor after confirmation."""

        _loopback_request(request)
        bootstrap.require_session(request)
        _same_origin_or_absent(request)
        lifecycle = writable_identity_lifecycle()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="identity rollback is invalid")
        if not isinstance(payload, dict) or set(payload) != {
            "expected_current_sha256",
            "confirmation",
        }:
            raise HTTPException(status_code=422, detail="identity rollback is invalid")
        confirmation = payload["confirmation"]
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            confirmation, "TERUGZETTEN"
        ):
            raise HTTPException(
                status_code=409, detail="identity rollback not confirmed"
            )
        try:
            transition = lifecycle.rollback(
                expected_current_sha256=payload["expected_current_sha256"]
            )
        except IdentityPackLifecycleError as error:
            raise HTTPException(
                status_code=409, detail="identity could not be rolled back"
            ) from error
        bootstrap.invalidate()
        return JSONResponse(
            {
                "contract": "EvaInstallerIdentityTransition/v1",
                "status": transition.status,
                "previous_sha256": transition.previous_sha256,
                "current_sha256": transition.current_sha256,
                "rollback_sha256": transition.rollback_sha256,
            },
            headers=_SECURITY_HEADERS,
        )

    return app


def _loopback_host(value: str) -> str:
    try:
        if not ipaddress.ip_address(value).is_loopback:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "installer bind address must be loopback"
        ) from error
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eva-installer")
    parser.add_argument(
        "--platform-lock", type=Path, default=Path("release/platform-lock.json")
    )
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--identity-current", type=Path)
    parser.add_argument("--identity-trust", type=Path)
    parser.add_argument("--identity-private-key", type=Path)
    parser.add_argument("--identity-workspace", type=Path)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("installer port must be between 1024 and 65535")
    identity_paths = (
        args.identity_current,
        args.identity_trust,
        args.identity_private_key,
        args.identity_workspace,
    )
    if any(path is not None for path in identity_paths) and not all(
        path is not None for path in identity_paths
    ):
        parser.error("all four identity lifecycle paths must be supplied together")
    try:
        receipt = preflight(load_platform_lock(args.platform_lock), inspect_host())
    except InstallerPreflightError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 2
    identity_lifecycle = None
    if all(path is not None for path in identity_paths):
        assert args.identity_workspace is not None
        try:
            if not args.identity_workspace.exists():
                args.identity_workspace.mkdir(mode=0o700)
            identity_lifecycle = InstallerIdentityLifecycle(
                current_path=args.identity_current,
                trust_path=args.identity_trust,
                private_key_path=args.identity_private_key,
                workspace=args.identity_workspace,
            )
        except (IdentityPackLifecycleError, OSError):
            print(
                json.dumps(
                    {"status": "failed", "error": "identity authority is invalid"},
                    sort_keys=True,
                )
            )
            return 2
    bootstrap = InstallerBootstrap.create()
    print(f"Open lokaal: http://{args.host}:{args.port}/bootstrap#{bootstrap.token}")
    uvicorn.run(
        create_installer_wizard(
            receipt,
            bootstrap,
            identity_lifecycle=identity_lifecycle,
        ),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
