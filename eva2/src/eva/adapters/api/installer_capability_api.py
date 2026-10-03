"""Loopback status UI for connector-neutral installer capabilities."""

from __future__ import annotations

import json
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.application.installer import (
    InstallerCapabilityPort,
    InstallerCapabilityStatusView,
    InstallerUseCaseError,
)


_LABELS = {
    "model_provider": "Modelverbinding",
    "telegram": "Telegram",
    "home_assistant": "Home Assistant",
    "browser": "Geïsoleerde browser",
    "email": "E-mail",
    "adaptive_connector": "Ander kanaal of dienst",
    "budgets": "Geheugen-, token- en kostenlimieten",
}
_CAPABILITY_REQUIRED = {
    "model_provider": True,
    "telegram": False,
    "home_assistant": False,
    "browser": False,
    "email": False,
    "adaptive_connector": False,
    "budgets": True,
}

INSTALLER_CAPABILITY_SECTION = """<section><h2>Wat wil je koppelen?</h2><p>Kiezen is nog niet koppelen. Alleen een echte controle kan later groen geven. De geïsoleerde browser wordt pas bij installatie toegevoegd.</p><div id="capability-list"><p>De koppelstatus wordt veilig geladen…</p></div><p id="capability-result" role="status"></p></section>"""


INSTALLER_CAPABILITY_SCRIPT = r"""
(() => {
  const enabled = window.__evaCapabilityEnabled === true;
  const list = document.getElementById('capability-list');
  const result = document.getElementById('capability-result');
  const labels = {
    model_provider: 'Modelverbinding', telegram: 'Telegram',
    home_assistant: 'Home Assistant', browser: 'Geïsoleerde browser', email: 'E-mail',
    adaptive_connector: 'Ander kanaal of dienst',
    budgets: 'Geheugen-, token- en kostenlimieten'
  };
  const states = {
    pending: 'nog niet gekozen', selected: 'gekozen · nog niet gecontroleerd',
    skipped: 'bewust overgeslagen', ready: 'echt gecontroleerd en klaar',
    blocked: 'controle niet gelukt'
  };
  function message(value) { result.textContent = value; }
  function button(step, wanted, text) {
    const control = document.createElement('button');
    control.type = 'button'; control.textContent = text;
    control.dataset.step = step.step_id; control.dataset.wanted = String(wanted);
    control.disabled = !enabled || step.state === 'ready' ||
      (wanted && step.state === 'selected') || (!wanted && step.state === 'skipped');
    return control;
  }
  function render(payload) {
    list.replaceChildren();
    payload.steps.forEach((step) => {
      const row = document.createElement('article');
      const title = document.createElement('strong');
      title.textContent = labels[step.step_id] + (step.required ? ' · verplicht' : ' · optioneel');
      const status = document.createElement('p');
      status.textContent = step.step_id === 'browser' && step.state === 'selected'
        ? 'gekozen · nog niet geïnstalleerd' : states[step.state];
      row.append(title, status, button(step, true, step.state === 'blocked' ? 'Opnieuw proberen' : 'Dit wil ik'));
      if (!step.required) row.append(button(step, false, 'Overslaan'));
      list.append(row);
    });
    message(payload.enabled ? 'Een keuze maakt nog geen verbinding en installeert niets.' : 'Deze stap is nog niet beschikbaar.');
  }
  async function refresh() {
    const response = await fetch('/internal/v1/onboarding/capabilities/status', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!response.ok) throw new Error('status unavailable');
    render(await response.json());
  }
  list.addEventListener('click', async (event) => {
    const control = event.target.closest('button[data-step]');
    if (!control) return;
    try {
      message('Keuze veilig bewaren…');
      const response = await fetch('/internal/v1/onboarding/capabilities/select', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({step_id: control.dataset.step, wanted: control.dataset.wanted === 'true'})
      });
      if (!response.ok) throw new Error('choice rejected');
      await refresh();
      window.dispatchEvent(new Event('eva-installer-state-changed'));
    } catch (_error) { message('Deze keuze kon niet veilig worden bewaard.'); }
  });
  refresh().catch(() => {
    list.textContent = 'De koppelstatus is niet leesbaar.';
    message('Er is niets gewijzigd.');
  });
})();
"""


def _payload(
    status: InstallerCapabilityStatusView | None, *, enabled: bool
) -> dict[str, object]:
    if status is None:
        steps = [
            {
                "step_id": step_id,
                "label": _LABELS[step_id],
                "required": required,
                "state": "unavailable",
                "evidence_sha256": None,
                "updated_at": None,
            }
            for step_id, required in _CAPABILITY_REQUIRED.items()
        ]
        revision = 0
        head_sha256 = None
    else:
        steps = [
            {
                "step_id": step.step_id,
                "label": _LABELS[step.step_id],
                "required": step.required,
                "state": step.state,
                "evidence_sha256": step.evidence_sha256,
                "updated_at": step.updated_at,
            }
            for step in status.steps
        ]
        revision = status.revision
        head_sha256 = status.head_sha256
    return {
        "contract": "EvaInstallerCapabilityStatus/v1",
        "enabled": enabled,
        "revision": revision,
        "head_sha256": head_sha256,
        "steps": steps,
        "non_claims": [
            "selected betekent alleen gekozen; niet geïnstalleerd, gekoppeld of klaar.",
            "Alleen een afzonderlijke providercontrole met receipt kan ready schrijven.",
            "Deze browserroute accepteert geen connectorsecret en geen verificatie-uitkomst.",
            "De gekozen lokale browser heeft geen providersecret nodig en wordt pas bij installatie toegevoegd.",
        ],
    }


def register_installer_capability_routes(
    app: FastAPI,
    *,
    workflow: InstallerCapabilityPort | None,
    enabled: bool,
    require_read: Callable[[Request], None],
    require_write: Callable[[Request], None],
    security_headers: Mapping[str, str],
) -> None:
    def current_status() -> InstallerCapabilityStatusView | None:
        if workflow is None:
            return None
        try:
            return workflow.status()
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=503, detail="installer capability status unavailable"
            ) from exc

    def writable() -> InstallerCapabilityPort:
        if workflow is None or not enabled:
            raise HTTPException(
                status_code=409, detail="installer capability choices are not enabled"
            )
        return workflow

    @app.get("/internal/v1/onboarding/capabilities/status")
    def capability_status(request: Request) -> JSONResponse:
        require_read(request)
        return JSONResponse(
            _payload(current_status(), enabled=enabled), headers=security_headers
        )

    @app.post("/internal/v1/onboarding/capabilities/select")
    async def capability_select(request: Request) -> JSONResponse:
        require_write(request)
        target = writable()
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="capability choice is invalid")
        if not isinstance(body, dict) or set(body) != {"step_id", "wanted"}:
            raise HTTPException(status_code=422, detail="capability choice is invalid")
        try:
            transition = target.select(
                step_id=body.get("step_id"), wanted=body.get("wanted")
            )
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=409, detail="capability choice could not be retained"
            ) from exc
        return JSONResponse(
            {
                "contract": "EvaInstallerCapabilityTransition/v1",
                "step_id": transition.step_id,
                "state": transition.state,
                "revision": transition.revision,
                "head_sha256": transition.head_sha256,
                "evidence_sha256": transition.evidence_sha256,
                "already_in_state": transition.already_in_state,
                "provider_verified": False,
            },
            headers=security_headers,
        )


__all__ = [
    "INSTALLER_CAPABILITY_SCRIPT",
    "INSTALLER_CAPABILITY_SECTION",
    "register_installer_capability_routes",
]
