"""Existing installer session controls for a prepared image-only update."""
from threading import Lock

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from eva.adapters.api.installer_request_json import read_small_json_request


INSTALLER_UPDATE_SECTION = """<section><h2>Eva bijwerken</h2><p id="runtime-update-label"></p><p id="runtime-update-status" role="status"></p><p>Deze update vervangt alleen de voorbereide kern- of werkerversie en behoudt de aangesloten opslag. Bij een fout blijven installatie en gegevens staan voor gericht herstel.</p><button id="runtime-update-apply" type="button" disabled>Voorbereide update installeren</button></section>"""

INSTALLER_UPDATE_SCRIPT = r"""
(() => {
  const button = document.getElementById('runtime-update-apply');
  const status = document.getElementById('runtime-update-status');
  const labels = {ready:'Update staat klaar.', unavailable:'Geen updatepakket voorbereid.',
    running:'Update wordt uitgevoerd…', running_with_retained_mounts:'Update geïnstalleerd; opslag behouden.',
    unchanged:'Deze versie was al geselecteerd.',
    incomplete_inspect_retained_installation:'Update niet afgerond. Bekijk de bewaarde uitvoer; niet opnieuw starten.'};
  function render(data) {
    document.getElementById('runtime-update-label').textContent = data.label || '';
    status.textContent = labels[data.status] || 'Uitvoering nog niet bevestigd; bekijk de bewaarde uitvoer.';
    button.disabled = !data.can_apply;
  }
  async function refresh() {
    const response = await fetch('/installer/update');
    if (response.ok) render(await response.json());
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = labels.running;
    try {
      await fetch('/installer/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      await refresh();
    } catch (_) { status.textContent = 'Verbinding onderbroken. Herlaad om de opgeslagen status te bekijken.'; }
  });
  refresh();
})();
"""


def register_installer_update_routes(app, *, workflow, require_read, require_write, security_headers):
    lock = Lock()
    running = False

    @app.get("/installer/update")
    def status(request: Request):
        require_read(request)
        result = ({"status": "unavailable", "can_apply": False} if workflow is None
                  else workflow.status())
        if running:
            result = {**result, "status": "running", "can_apply": False}
        return JSONResponse(result, headers=security_headers)

    @app.post("/installer/update")
    async def apply(request: Request):
        nonlocal running
        require_write(request)
        if await read_small_json_request(request, 128, "update") != {}:
            raise HTTPException(400, "update accepts no browser configuration")
        if workflow is None:
            raise HTTPException(409, "no prepared update")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "update already running")
        running = True
        try:
            if not workflow.status()["can_apply"]:
                raise HTTPException(409, "inspect the previous update")
            try:
                result = await run_in_threadpool(workflow.apply)
            except (OSError, RuntimeError, ValueError):
                raise HTTPException(409, "update not confirmed; inspect retained result") from None
            return JSONResponse(result, headers=security_headers)
        finally:
            running = False
            lock.release()
