"""Display installer choices and invoke trusted target preparation."""

from __future__ import annotations

from typing import Callable
from threading import Lock

from fastapi.concurrency import run_in_threadpool
from eva.adapters.api.installer_request_json import read_small_json_request

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


INSTALLER_INSTALLATION_PLAN_SECTION = """<section><h2>Installatieplan</h2><div id="installation-plan"><p>Het plan wordt veilig geladen…</p></div><p><small>Het plan bekijken verandert niets. Installatie uitvoeren maakt opslag en bereidt de databases voor. Een pakket voor nieuwe installatie start daarna Eva; een herstelpakket wacht op herstel.</small></p><button id="installation-prepare" type="button" disabled>Installatie uitvoeren</button><p id="installation-preparation-status" role="status"></p><a id="installed-eva" href="/eva/internal/v1/dashboard/conversation" hidden>Open de nieuwe Eva en begin een gesprek</a><p><a id="installed-voice" hidden>Praten met de nieuwe Eva</a></p></section>"""


INSTALLER_INSTALLATION_PLAN_SCRIPT = r"""
(() => {
  const target = document.getElementById('installation-plan');
  const prepare = document.getElementById('installation-prepare');
  const preparationStatus = document.getElementById('installation-preparation-status');
  let currentPlan = null;
  let attempted = false;
  function render(plan) {
    currentPlan = plan;
    target.replaceChildren();
    const state = document.createElement('strong');
    state.textContent = plan.status === 'ready_for_release_preparation' ?
      'Klaar voor de veilige releasevoorbereiding.' :
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
    const statusResponse = await fetch('/internal/v1/onboarding/install/preparation', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!statusResponse.ok) throw new Error('preparation status unavailable');
    const status = await statusResponse.json();
    preparationStatus.textContent = status.message;
    document.getElementById('installed-eva').hidden = status.status !== 'runtime_ready' || status.owner_access !== true;
    const voice = document.getElementById('installed-voice');
    const voiceUrl = typeof status.voice_url === 'string' && /^http:\/\/(localhost|127\.0\.0\.1):[0-9]+$/.test(status.voice_url) ? status.voice_url : null;
    voice.hidden = status.status !== 'runtime_ready' || !voiceUrl;
    if (voiceUrl) voice.href = voiceUrl;
    prepare.disabled = attempted || status.status !== 'available' || currentPlan.status !== 'ready_for_release_preparation';
  }
  prepare.onclick = async () => {
    if (prepare.disabled || !currentPlan) return;
    attempted = true; prepare.disabled = true;
    preparationStatus.textContent = 'Installatie wordt uitgevoerd volgens de startroute van dit pakket. Sluit dit venster niet.';
    try {
      const response = await fetch('/internal/v1/onboarding/install/prepare', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plan_sha256: currentPlan.plan_sha256})
      });
      const value = await response.json();
      preparationStatus.textContent = value.message || value.detail || 'Voorbereiding niet bevestigd. Controleer de bewaarde toestand.';
      await refresh();
    } catch (error) {
      preparationStatus.textContent = 'Verbinding onderbroken. De voorbereiding kan nog lopen; herhaal de actie niet. Herlaad om de toestand te bekijken.';
    }
  };
  window.addEventListener('eva-installer-state-changed', () => {
    refresh().catch(() => {});
  });
  refresh().catch(() => {
    prepare.disabled = true;
    target.textContent = 'Het installatieplan is niet betrouwbaar leesbaar. De voorbereidingstoestand is onbekend.';
  });
})();
"""


def register_installer_installation_plan_routes(
    app: FastAPI,
    *,
    build_plan: Callable[[], dict[str, object]],
    require_read: Callable[[Request], None],
    security_headers: dict[str, str],
    require_write: Callable[[Request], None] | None = None,
    prepare_target: Callable[[dict[str, object]], None] | None = None,
    read_preparation_status: Callable[[], str] | None = None,
    runtime_owner_access: Callable | None = None,
    runtime_voice_access: Callable | None = None,
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

    preparation_lock = Lock()
    preparation_state = "available" if prepare_target is not None and require_write is not None and read_preparation_status is not None else "unavailable"
    messages = {
        "unavailable": "Automatische doelvoorbereiding is nog niet aangesloten op deze installer.",
        "available": "Voer de installatie uit met de getoonde keuzes en de startroute van dit pakket.",
        "running": "Doelvoorbereiding loopt. Start geen tweede poging.",
        "prepared": "Doelinstallatie voorbereid. Eva is nog niet gestart; ga verder met de herstelroute.",
        "runtime_ready": "De nieuwe Eva-diensten zijn gestart en technisch bereikbaar. De kanaal- en gebruiksproef staat nog open.",
        "needs_inspection": "Voorbereiding niet bevestigd. Gedeeltelijke resultaten blijven bewaard. Laat de toestand controleren voordat je verdergaat.",
    }

    def refresh_preparation_state():
        nonlocal preparation_state
        if preparation_state == "available" and read_preparation_status is not None:
            try:
                stored = read_preparation_status()
                if stored not in messages:
                    raise ValueError("unknown preparation status")
                preparation_state = stored
            except Exception:
                preparation_state = "needs_inspection"

    def preparation_payload():
        return {"status": preparation_state, "message": messages[preparation_state],
                "owner_access": bool(preparation_state == "runtime_ready" and runtime_owner_access is not None),
                "voice_url": runtime_voice_access() if preparation_state == "runtime_ready" and runtime_voice_access else None}

    @app.get("/internal/v1/onboarding/install/preparation")
    def preparation_status(request: Request) -> JSONResponse:
        require_read(request)
        if preparation_lock.acquire(blocking=False):
            try:
                refresh_preparation_state()
            finally:
                preparation_lock.release()
        return JSONResponse(preparation_payload(), headers=security_headers)

    @app.post("/internal/v1/onboarding/install/prepare")
    async def prepare_installation(request: Request) -> JSONResponse:
        nonlocal preparation_state
        if require_write is None:
            raise HTTPException(409, "Doelvoorbereiding is niet aangesloten.")
        require_write(request)
        raw = await read_small_json_request(request, 1024, "installation preparation")
        if (not isinstance(raw, dict) or set(raw) != {"plan_sha256"}
                or not isinstance(raw["plan_sha256"], str) or len(raw["plan_sha256"]) != 64):
            raise HTTPException(400, "Ongeldige planbevestiging.")
        if not preparation_lock.acquire(blocking=False):
            raise HTTPException(409, "Doelvoorbereiding loopt al.")
        try:
            refresh_preparation_state()
            if preparation_state != "available" or prepare_target is None:
                return JSONResponse(preparation_payload(), status_code=409, headers=security_headers)
            try:
                plan = build_plan()
            except Exception:
                raise HTTPException(503, "Actueel installatieplan is niet beschikbaar.") from None
            if plan.get("plan_sha256") != raw["plan_sha256"]:
                raise HTTPException(409, "Installatiekeuzes zijn gewijzigd. Bekijk eerst het actuele plan.")
            if plan.get("status") != "ready_for_release_preparation" or plan.get("blockers"):
                raise HTTPException(409, "Rond eerst de ontbrekende installatiekeuzes af.")
            preparation_state = "running"
            try:
                # The trusted backend owns durable receipts and rejects reuse after restart.
                # Browser input is only a confirmation of the freshly rebuilt server plan.
                await run_in_threadpool(prepare_target, plan)
            except Exception:
                preparation_state = "needs_inspection"
                return JSONResponse(preparation_payload(), status_code=409, headers=security_headers)
            try:
                stored = read_preparation_status() if read_preparation_status is not None else "prepared"
                preparation_state = stored if stored in {"prepared", "runtime_ready"} else "needs_inspection"
            except Exception:
                preparation_state = "needs_inspection"
            return JSONResponse(preparation_payload(), headers=security_headers)
        finally:
            preparation_lock.release()
