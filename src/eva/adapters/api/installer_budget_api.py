"""Loopback-only controls for Eva's non-secret installer budgets."""

from __future__ import annotations

import hmac
import json
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.application.installer import (
    InstallerBudgetPort,
    InstallerBudgetStatusView,
    InstallerUseCaseError,
    InstallerUserInputError,
)


INSTALLER_BUDGET_SECTION = """<section><h2>Harde gebruikslimieten</h2><p>Deze maxima voorkomen onbeperkt geheugen, modelgebruik en achtergrondwerk. Onbekende autonome kosten blijven altijd geblokkeerd.</p><div id="budget-fields"><label>Geheugencontext per antwoord (tokens) <input type="number" data-budget="recall_context_tokens" min="2000" max="4000" value="3000"></label><label>Geheugenzoekstappen <input type="number" data-budget="retrieval_nodes" min="20" max="100" value="50"></label><label>Maximale modelinvoer per gewoon antwoord <input type="number" data-budget="max_input_tokens" min="4000" max="65536" value="12000"></label><label>Maximale modeluitvoer per gewoon antwoord <input type="number" data-budget="max_output_tokens" min="100" max="1200" value="1200"></label><label>Persoonsprofiel in context (tokens) <input type="number" data-budget="profile_context_tokens" min="100" max="1000" value="800"></label><label>Geheugenregels per persoon <input type="number" data-budget="memory_records_per_person" min="100" max="10000" value="2000"></label><label>Geheugenbytes per persoon <input type="number" data-budget="memory_bytes_per_person" min="1048576" max="104857600" value="20971520"></label><label>Geheugenregels per groep <input type="number" data-budget="memory_records_per_group" min="0" max="100000" value="2000"></label><label>Geheugenbytes per groep <input type="number" data-budget="memory_bytes_per_group" min="0" max="1073741824" value="20971520"></label><label>Geheugenregels per project <input type="number" data-budget="memory_records_per_project" min="0" max="100000" value="2000"></label><label>Geheugenbytes per project <input type="number" data-budget="memory_bytes_per_project" min="0" max="1073741824" value="20971520"></label><label>Geheugenregels hele installatie <input type="number" data-budget="memory_records_per_installation" min="100" max="1000000" value="20000"></label><label>Geheugenbytes hele installatie <input type="number" data-budget="memory_bytes_per_installation" min="1048576" max="10737418240" value="209715200"></label><label>Bewaren (dagen) <input type="number" data-budget="retention_days" min="7" max="3650" value="365"></label><label>Achtergrondreflecties per persoon per dag <input type="number" data-budget="background_reflections_per_person_day" min="0" max="100" value="4"></label><label>Achtergrondreflecties hele installatie per dag <input type="number" data-budget="background_reflections_per_installation_day" min="0" max="1000" value="40"></label><label>Externe autonome uitgaven per maand (eurocent) <input type="number" data-budget="external_monthly_spend_cap_eur_cents" min="0" max="1000000" value="0"></label></div><button type="button" id="budget-stage">Bewaar limieten</button> <button type="button" id="budget-confirm" disabled>Bevestig limieten</button><p id="budget-result" role="status"></p></section>"""


INSTALLER_BUDGET_SCRIPT = r"""
(() => {
  const enabled = window.__evaBudgetEnabled === true;
  const fields = Array.from(document.querySelectorAll('[data-budget]'));
  const stage = document.getElementById('budget-stage');
  const confirm = document.getElementById('budget-confirm');
  const result = document.getElementById('budget-result');
  let current = null;
  function confirmable(status) { return status && (status.phase === 'staged' || (status.phase === 'ready' && !status.capability_bound)); }
  function message(text) { result.textContent = text; }
  function lock(value) { fields.forEach((field) => { field.disabled = value; }); }
  function render(status) {
    current = status;
    if (status.budget) fields.forEach((field) => { field.value = status.budget[field.dataset.budget]; });
    if (!enabled || !status.enabled) {
      lock(true); stage.disabled = true; confirm.disabled = true;
      message('De limieten zijn nog niet beschikbaar.'); return;
    }
    if (status.phase === 'empty') {
      lock(false); stage.disabled = false; confirm.disabled = true;
      message('Kies begrensde maxima. Nul euro betekent: geen autonome externe uitgaven.'); return;
    }
    lock(true); stage.disabled = true;
    if (confirmable(status)) {
      confirm.disabled = false; message('Limieten teruggevonden. Bevestig ze apart.'); return;
    }
    confirm.disabled = true; message('De limieten zijn bevestigd en aan het installatieplan gebonden.');
  }
  async function post(path, body) {
    const response = await fetch(path, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error('budget request failed');
    return response.json();
  }
  async function refresh() {
    const response = await fetch('/internal/v1/onboarding/budgets/status', {
      credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) throw new Error('budget status unavailable');
    render(await response.json());
  }
  stage.addEventListener('click', async () => {
    const body = {}; fields.forEach((field) => { body[field.dataset.budget] = Number(field.value); });
    try { message('Limieten bewaren…'); await post('/internal/v1/onboarding/budgets/stage', body); await refresh(); window.dispatchEvent(new Event('eva-installer-state-changed')); }
    catch (_error) { message('Deze limieten zijn ongeldig of konden niet veilig worden bewaard.'); }
  });
  confirm.addEventListener('click', async () => {
    if (!enabled || !current?.enabled || !confirmable(current) || !window.confirm('Wil je deze harde limieten bevestigen?')) return;
    try { message('Limieten bevestigen…'); await post('/internal/v1/onboarding/budgets/confirm', {budget_sha256: current.budget_sha256, confirmation: 'LIMIETEN BEVESTIGEN'}); await refresh(); window.dispatchEvent(new Event('eva-installer-state-changed')); }
    catch (_error) { message('De limieten konden niet worden bevestigd.'); }
  });
  refresh().catch(() => { lock(true); stage.disabled = true; confirm.disabled = true; message('De limieten zijn niet betrouwbaar leesbaar.'); });
})();
"""


def _payload(
    status: InstallerBudgetStatusView | None,
    *,
    enabled: bool,
    capability_bound: bool,
) -> dict[str, object]:
    return {
        "budget": status.budget.as_dict() if status and status.budget else None,
        "budget_sha256": status.budget_sha256 if status else None,
        "capability_bound": capability_bound,
        "contract": "EvaInstallerBudgetStatus/v1",
        "enabled": enabled,
        "non_claims": [
            "Deze limieten installeren of starten nog niets.",
            "Een bevestigde configuratie bewijst nog niet dat de eindruntime ieder quotum handhaaft.",
            "Onbekende kosten worden nooit als nul gemeten.",
        ],
        "phase": status.phase if status else "unavailable",
        "revision": status.revision if status else 0,
        "updated_at": status.updated_at if status else None,
    }


def register_installer_budget_routes(
    app: FastAPI,
    *,
    workflow: InstallerBudgetPort | None,
    enabled: bool,
    require_read: Callable[[Request], None],
    require_write: Callable[[Request], None],
    security_headers: Mapping[str, str],
) -> None:
    def current() -> InstallerBudgetStatusView | None:
        if workflow is None:
            return None
        try:
            return workflow.status()
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=503, detail="installer budget status unavailable"
            ) from exc

    def writable() -> InstallerBudgetPort:
        if workflow is None or not enabled:
            raise HTTPException(
                status_code=409, detail="installer budgets are not enabled"
            )
        return workflow

    def payload() -> dict[str, object]:
        status = current()
        bound = False
        if workflow is not None and status is not None and status.budget_sha256:
            try:
                step = workflow.capability_workflow.status().step("budgets")
            except InstallerUseCaseError as exc:
                raise HTTPException(
                    status_code=503, detail="installer budget binding unavailable"
                ) from exc
            bound = (
                status.phase == "ready"
                and step.state == "ready"
                and step.evidence_sha256 == status.budget_sha256
            )
        return _payload(status, enabled=enabled, capability_bound=bound)

    @app.get("/internal/v1/onboarding/budgets/status")
    def budget_status(request: Request) -> JSONResponse:
        require_read(request)
        return JSONResponse(payload(), headers=security_headers)

    @app.post("/internal/v1/onboarding/budgets/stage")
    async def budget_stage(request: Request) -> JSONResponse:
        require_write(request)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="installer budgets are invalid")
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="installer budgets are invalid")
        try:
            transition = writable().stage(**body)
        except InstallerUserInputError as exc:
            raise HTTPException(
                status_code=422, detail="installer budgets are invalid"
            ) from exc
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=409, detail="installer budgets could not be staged"
            ) from exc
        return JSONResponse(
            {
                "already_in_phase": transition.already_in_phase,
                "budget_sha256": transition.budget_sha256,
                "contract": "EvaInstallerBudgetTransition/v1",
                "phase": transition.phase,
                "revision": transition.revision,
            },
            headers=security_headers,
        )

    @app.post("/internal/v1/onboarding/budgets/confirm")
    async def budget_confirm(request: Request) -> JSONResponse:
        require_write(request)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=422, detail="budget confirmation is invalid"
            )
        if not isinstance(body, dict) or set(body) != {
            "budget_sha256",
            "confirmation",
        }:
            raise HTTPException(
                status_code=422, detail="budget confirmation is invalid"
            )
        confirmation = body.get("confirmation")
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            confirmation, "LIMIETEN BEVESTIGEN"
        ):
            raise HTTPException(status_code=409, detail="budgets are not confirmed")
        try:
            transition = writable().confirm(
                expected_budget_sha256=body.get("budget_sha256")
            )
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=409, detail="installer budgets could not be confirmed"
            ) from exc
        return JSONResponse(
            {
                "already_in_phase": transition.already_in_phase,
                "budget_sha256": transition.budget_sha256,
                "contract": "EvaInstallerBudgetTransition/v1",
                "phase": transition.phase,
                "revision": transition.revision,
            },
            headers=security_headers,
        )


__all__ = [
    "INSTALLER_BUDGET_SCRIPT",
    "INSTALLER_BUDGET_SECTION",
    "register_installer_budget_routes",
]
