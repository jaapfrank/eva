"""Loopback-only read view of Eva's fail-closed installation plan."""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


INSTALLER_INSTALLATION_PLAN_SECTION = """<section><h2>Installatieplan</h2><div id="installation-plan"><p>Het plan wordt veilig geladen…</p></div><p><small>Deze stap kijkt alleen. Er wordt nog niets gestart of geïnstalleerd.</small></p></section>"""


INSTALLER_INSTALLATION_PLAN_SCRIPT = r"""
(() => {
  const target = document.getElementById('installation-plan');
  function render(plan) {
    target.replaceChildren();
    const state = document.createElement('strong');
    state.textContent = plan.status === 'ready_to_install' ?
      'Klaar om de installatie voor te bereiden.' :
      'Nog niet klaar om te installeren.';
    target.append(state);
    if (plan.blockers.length) {
      const list = document.createElement('ul');
      plan.blockers.forEach((blocker) => {
        const item = document.createElement('li');
        item.textContent = blocker.message;
        list.append(item);
      });
      target.append(list);
    }
    const fingerprint = document.createElement('p');
    fingerprint.textContent = 'Plancontrole: ' + plan.plan_sha256.slice(0, 12) + '…';
    target.append(fingerprint);
  }
  async function refresh() {
    const response = await fetch('/internal/v1/onboarding/install/plan', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!response.ok) throw new Error('installation plan unavailable');
    render(await response.json());
  }
  window.addEventListener('eva-installer-state-changed', () => {
    refresh().catch(() => {});
  });
  refresh().catch(() => {
    target.textContent = 'Het installatieplan is niet betrouwbaar leesbaar. Er is niets gewijzigd.';
  });
})();
"""


def register_installer_installation_plan_routes(
    app: FastAPI,
    *,
    build_plan: Callable[[], dict[str, object]],
    require_read: Callable[[Request], None],
    security_headers: dict[str, str],
) -> None:
    @app.get("/internal/v1/onboarding/install/plan")
    def installation_plan(request: Request) -> JSONResponse:
        require_read(request)
        try:
            payload = build_plan()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="installer installation plan unavailable"
            ) from error
        return JSONResponse(payload, headers=security_headers)
