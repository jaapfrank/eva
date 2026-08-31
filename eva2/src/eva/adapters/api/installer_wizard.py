"""Temporary loopback-only entry page for Eva's future installer wizard."""

from __future__ import annotations

import hmac
import html
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from eva.adapters.api.installer_capability_api import (
    INSTALLER_CAPABILITY_SCRIPT,
    INSTALLER_CAPABILITY_SECTION,
    register_installer_capability_routes,
)
from eva.adapters.api.installer_budget_api import (
    INSTALLER_BUDGET_SCRIPT,
    INSTALLER_BUDGET_SECTION,
    register_installer_budget_routes,
)
from eva.adapters.api.installer_identity_ui import INSTALLER_IDENTITY_SCRIPT
from eva.adapters.api.installer_installation_plan_api import (
    INSTALLER_INSTALLATION_PLAN_SCRIPT,
    INSTALLER_INSTALLATION_PLAN_SECTION,
    register_installer_installation_plan_routes,
)
from eva.adapters.api.installer_profile_api import (
    INSTALLER_PROFILE_SCRIPT,
    INSTALLER_PROFILE_SECTION,
    register_installer_profile_routes,
)
from eva.adapters.api.installer_provider_api import (
    INSTALLER_PROVIDER_SCRIPT,
    INSTALLER_PROVIDER_SECTION,
    register_installer_provider_routes,
)
from eva.adapters.api.installer_secret_api import (
    INSTALLER_SECRET_SCRIPT,
    INSTALLER_SECRET_SECTION,
    register_installer_secret_routes,
)
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
from eva.operations.installer_identity_lifecycle import (
    InstallerIdentityLifecycle,
    InstallerIdentityWorkflowStatus,
)
from eva.operations.installer_installation_plan import (
    build_installer_installation_plan,
)
from eva.operations.installer_budget_workflow import InstallerBudgetWorkflow
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_profile_workflow import InstallerProfileWorkflow
from eva.operations.installer_provider_configuration import (
    InstallerProviderConfigurationStore,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderProbe,
    InstallerProviderVerifier,
)
from eva.operations.installer_secret_store import InstallerSecretStore
from eva.operations.installer_secret_lifecycle import InstallerSecretLifecycle


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
    identity_workflow: InstallerIdentityWorkflowStatus | None = None,
    profile_enabled: bool = False,
    capability_enabled: bool = False,
    secret_enabled: bool = False,
    provider_verification_enabled: bool = False,
    budget_enabled: bool = False,
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
        controls = """<p><small>Je voorstel wordt pas actief na vier zichtbare stappen. De privésleutel blijft buiten de browser en buiten Eva.</small></p><div id="identity-actions"><button type="button" id="soul-stage" disabled>1. Bewaar voorstel</button> <button type="button" id="soul-issue" disabled>2. Onderteken</button> <button type="button" id="soul-activate" disabled>3. Activeer</button> <button type="button" id="soul-rollback" disabled>Vorige identiteit terugzetten</button> <button type="button" id="soul-new-proposal" hidden>Nieuwe wijziging voorbereiden</button></div>"""
    else:
        controls = """<p><small>Deze installer is niet met een private identiteit-authority gestart of de platformcontrole is nog geblokkeerd. Je kunt daarom alleen voorvertonen.</small></p>"""
    resume_payload = None
    if identity_workflow is not None:
        resume_payload = {
            "phase": identity_workflow.phase,
            "base_pack_sha256": identity_workflow.base_pack_sha256,
            "current_pack_sha256": identity_workflow.current_pack_sha256,
            "draft_sha256": identity_workflow.draft_sha256,
            "candidate_sha256": identity_workflow.candidate_sha256,
        }
    resume_json = json.dumps(resume_payload, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    writable_json = "true" if identity_writable else "false"
    profile_enabled_json = "true" if profile_enabled else "false"
    capability_enabled_json = "true" if capability_enabled else "false"
    secret_enabled_json = "true" if secret_enabled else "false"
    provider_enabled_json = "true" if provider_verification_enabled else "false"
    budget_enabled_json = "true" if budget_enabled else "false"
    return (
        f"""<!doctype html>
<html lang="nl">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eva installeren</title>
<style>
body{{font:16px system-ui;margin:0;background:#07111c;color:#edf6ff;display:grid;min-height:100vh;place-items:center}}
main{{max-width:720px;padding:2rem}}
section{{background:#102337;border:1px solid #31516d;border-radius:18px;padding:1.5rem;margin:1rem 0}}
.blocked{{color:#ffca78}}.passed{{color:#86f4ba}}small{{color:#aec4d7}}li{{margin:.7rem 0}}
textarea{{box-sizing:border-box;width:100%;min-height:9rem;background:#07111c;color:#edf6ff;border:1px solid #52718d;border-radius:8px;padding:.8rem;font:14px ui-monospace,monospace}}
button{{margin-top:.7rem;background:#2b8a61;color:white;border:0;border-radius:8px;padding:.65rem .9rem;font-weight:700}}
button:disabled{{opacity:.45}}#soul-result{{white-space:pre-wrap}}
</style>
<main>
<p><small>EVA · lokale installatiecontrole</small></p>
<h1>Voordat Eva iets installeert</h1>
<section><h2 class="{status}">{status}</h2><p>{next_step}</p><ul>{rows}</ul><p><small>De wizard start hier nog geen dienst. Iedere koppeling blijft afzonderlijk gekozen, ingevoerd en gecontroleerd.</small></p></section>
{INSTALLER_PROFILE_SECTION}
{INSTALLER_CAPABILITY_SECTION}
{INSTALLER_SECRET_SECTION}
{INSTALLER_PROVIDER_SECTION}
{INSTALLER_BUDGET_SECTION}
{INSTALLER_INSTALLATION_PLAN_SECTION}
<section><h2>Hoe {name} zich aan jou voorstelt</h2><p><a href="/internal/v1/identity/soul-export" download="SOUL.md">Download de huidige SOUL.md</a></p><p>Plak optioneel een kleine <code>SOUL.md</code>. Je ziet eerst het precieze voorstelverschil.</p><textarea id="soul" maxlength="8192" spellcheck="false" placeholder="# Eva&#10;&#10;## Naam&#10;Eva&#10;&#10;## Temperament&#10;warm, nieuwsgierig"></textarea><button type="button" id="soul-preview">Toon voorstel</button>{controls}<p id="soul-result" role="status"></p></section>
</main>
<script>window.__evaIdentityWritable={writable_json};window.__evaIdentityResume={resume_json};window.__evaProfileEnabled={profile_enabled_json};window.__evaCapabilityEnabled={capability_enabled_json};window.__evaSecretEnabled={secret_enabled_json};window.__evaProviderVerificationEnabled={provider_enabled_json};window.__evaBudgetEnabled={budget_enabled_json};</script>
<script>"""
        + INSTALLER_PROFILE_SCRIPT
        + "</script>\n<script>"
        + INSTALLER_CAPABILITY_SCRIPT
        + "</script>\n<script>"
        + INSTALLER_SECRET_SCRIPT
        + "</script>\n<script>"
        + INSTALLER_PROVIDER_SCRIPT
        + "</script>\n<script>"
        + INSTALLER_BUDGET_SCRIPT
        + "</script>\n<script>"
        + INSTALLER_INSTALLATION_PLAN_SCRIPT
        + "</script>\n<script>"
        + INSTALLER_IDENTITY_SCRIPT
        + "</script>\n</html>"
    )


def create_installer_wizard(
    receipt: dict[str, object],
    bootstrap: InstallerBootstrap,
    identity: IdentityPack | None = None,
    identity_lifecycle: InstallerIdentityLifecycle | None = None,
    profile_workflow: InstallerProfileWorkflow | None = None,
    capability_workflow: InstallerCapabilityWorkflow | None = None,
    secret_store: InstallerSecretStore | None = None,
    provider_verifier: InstallerProviderVerifier | None = None,
    provider_configuration_store: InstallerProviderConfigurationStore | None = None,
    configured_probe_factory: Callable[[str, str], InstallerProviderProbe] | None = None,
    secret_lifecycle: InstallerSecretLifecycle | None = None,
    budget_workflow: InstallerBudgetWorkflow | None = None,
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
    profile_enabled = profile_workflow is not None and receipt["status"] == "ready"
    capability_enabled = (
        capability_workflow is not None and receipt["status"] == "ready"
    )
    secret_enabled = (
        secret_store is not None
        and capability_workflow is not None
        and receipt["status"] == "ready"
    )
    if provider_verifier is not None and (
        capability_workflow is None
        or secret_store is None
        or provider_verifier.capability_workflow.workspace
        != capability_workflow.workspace
        or provider_verifier.secret_store.workspace != secret_store.workspace
    ):
        raise ValueError("installer provider verifier uses different workspaces")
    if secret_lifecycle is not None and (
        capability_workflow is None
        or secret_store is None
        or secret_lifecycle.capability_workflow.workspace
        != capability_workflow.workspace
        or secret_lifecycle.secret_store.workspace != secret_store.workspace
    ):
        raise ValueError("installer secret lifecycle uses different workspaces")
    if budget_workflow is not None and (
        capability_workflow is None
        or budget_workflow.capability_workflow.workspace
        != capability_workflow.workspace
    ):
        raise ValueError("installer budget workflow uses different workspaces")
    provider_verification_enabled = (
        provider_verifier is not None and receipt["status"] == "ready"
    )
    budget_enabled = budget_workflow is not None and receipt["status"] == "ready"
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

    def identity_workflow_status() -> InstallerIdentityWorkflowStatus | None:
        if identity_lifecycle is None:
            return None
        try:
            return identity_lifecycle.status()
        except IdentityPackLifecycleError as error:
            raise HTTPException(
                status_code=503, detail="installer identity workflow unavailable"
            ) from error

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
                identity_workflow=(
                    identity_workflow_status() if identity_writable else None
                ),
                profile_enabled=profile_enabled,
                capability_enabled=capability_enabled,
                secret_enabled=secret_enabled,
                provider_verification_enabled=provider_verification_enabled,
                budget_enabled=budget_enabled,
            ),
            headers=_SECURITY_HEADERS,
        )

    def require_installer_read(request: Request) -> None:
        _loopback_request(request)
        bootstrap.require_session(request)

    def require_installer_write(request: Request) -> None:
        require_installer_read(request)
        _same_origin_or_absent(request)

    register_installer_profile_routes(
        app,
        workflow=profile_workflow,
        enabled=profile_enabled,
        require_read=require_installer_read,
        require_write=require_installer_write,
        security_headers=_SECURITY_HEADERS,
    )
    register_installer_capability_routes(
        app,
        workflow=capability_workflow,
        enabled=capability_enabled,
        require_read=require_installer_read,
        require_write=require_installer_write,
        security_headers=_SECURITY_HEADERS,
    )
    register_installer_secret_routes(
        app,
        store=secret_store,
        capability_workflow=capability_workflow,
        lifecycle=secret_lifecycle,
        enabled=secret_enabled,
        require_read=require_installer_read,
        require_write=require_installer_write,
        security_headers=_SECURITY_HEADERS,
    )
    register_installer_provider_routes(
        app,
        verifier=provider_verifier,
        configuration_store=provider_configuration_store,
        enabled=provider_verification_enabled,
        require_read=require_installer_read,
        require_write=require_installer_write,
        security_headers=_SECURITY_HEADERS,
        configured_probe_factory=configured_probe_factory,
    )
    register_installer_budget_routes(
        app,
        workflow=budget_workflow,
        enabled=budget_enabled,
        require_read=require_installer_read,
        require_write=require_installer_write,
        security_headers=_SECURITY_HEADERS,
    )
    register_installer_installation_plan_routes(
        app,
        build_plan=lambda: build_installer_installation_plan(
            preflight=receipt,
            identity=current_identity(),
            profile=profile_workflow.status() if profile_workflow is not None else None,
            capabilities=(
                capability_workflow.status()
                if capability_workflow is not None
                else None
            ),
            secrets=secret_store.status() if secret_store is not None else None,
            budgets=budget_workflow.status() if budget_workflow is not None else None,
        ),
        require_read=require_installer_read,
        security_headers=_SECURITY_HEADERS,
    )

    @app.get("/internal/v1/preflight")
    def report(request: Request) -> JSONResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        return JSONResponse(receipt, headers=_SECURITY_HEADERS)

    @app.get("/internal/v1/identity/status")
    def identity_status(request: Request) -> JSONResponse:
        _loopback_request(request)
        bootstrap.require_session(request)
        workflow = identity_workflow_status()
        if workflow is None:
            payload: dict[str, object] = {
                "contract": "EvaInstallerIdentityWorkflowStatus/v1",
                "enabled": False,
                "phase": "unavailable",
                "base_pack_sha256": None,
                "current_pack_sha256": None,
                "draft_sha256": None,
                "candidate_sha256": None,
                "updated_at": None,
            }
        else:
            payload = {
                "contract": "EvaInstallerIdentityWorkflowStatus/v1",
                "enabled": identity_writable,
                "phase": workflow.phase,
                "base_pack_sha256": workflow.base_pack_sha256,
                "current_pack_sha256": workflow.current_pack_sha256,
                "draft_sha256": workflow.draft_sha256,
                "candidate_sha256": workflow.candidate_sha256,
                "updated_at": workflow.updated_at,
            }
        return JSONResponse(payload, headers=_SECURITY_HEADERS)

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
