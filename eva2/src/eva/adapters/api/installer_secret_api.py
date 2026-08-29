"""One-time loopback secret intake for selected installer capabilities."""

from __future__ import annotations

import json
from threading import Lock
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_secret_store import (
    SECRET_SLOTS,
    InstallerSecretInputError,
    InstallerSecretSlotStatus,
    InstallerSecretStore,
    InstallerSecretStoreError,
)
from eva.operations.installer_secret_lifecycle import (
    InstallerSecretLifecycle,
    InstallerSecretLifecycleError,
    InstallerSecretLifecycleResult,
)


_MAX_JSON_BYTES = 12_288
_LABELS = {
    "model_provider": "Modelverbinding",
    "telegram": "Telegram",
    "home_assistant": "Home Assistant",
    "email": "E-mail",
}

INSTALLER_SECRET_SECTION = """<section><h2>Eenmalige toegang invoeren</h2><p>Alleen een eerder gekozen koppeling verschijnt hier. De waarde wordt niet teruggestuurd en niet in de browser bewaard.</p><label>Koppeling <select id="secret-slot" disabled></select></label><label>Toegangscode <input id="secret-value" type="password" minlength="16" maxlength="8192" autocomplete="new-password" spellcheck="false" disabled></label><button type="button" id="secret-store" disabled>Veilig bewaren</button><div id="secret-list"><p>De secretstatus wordt veilig geladen…</p></div><p id="secret-result" role="status"></p></section>"""


INSTALLER_SECRET_SCRIPT = r"""
(() => {
  const enabled = window.__evaSecretEnabled === true;
  const slot = document.getElementById('secret-slot');
  const value = document.getElementById('secret-value');
  const store = document.getElementById('secret-store');
  const list = document.getElementById('secret-list');
  const result = document.getElementById('secret-result');
  function message(text) { result.textContent = text; }
  function render(payload) {
    list.replaceChildren(); slot.replaceChildren();
    payload.slots.forEach((item) => {
      const row = document.createElement('p');
      row.textContent = item.label + ': ' +
        (item.state === 'present' ? 'privé bewaard' :
         item.state === 'needs_help' ? 'hulp nodig' : 'nog leeg');
      list.append(row);
      if (payload.lifecycle_enabled && item.state === 'present') {
        const replacement = document.createElement('input');
        replacement.type = 'password'; replacement.minLength = 16;
        replacement.maxLength = 8192; replacement.autocomplete = 'new-password';
        replacement.spellcheck = false; replacement.dataset.rotateSecret = item.slot_id;
        replacement.placeholder = 'Nieuwe toegang';
        const rotate = document.createElement('button');
        rotate.type = 'button'; rotate.dataset.rotate = item.slot_id;
        rotate.textContent = 'Toegang vervangen';
        const remove = document.createElement('button');
        remove.type = 'button'; remove.dataset.remove = item.slot_id;
        remove.textContent = 'Koppeling losmaken';
        row.append(replacement, rotate, remove);
      } else if (payload.lifecycle_enabled && item.state === 'needs_help') {
        const recover = document.createElement('button');
        recover.type = 'button'; recover.dataset.recover = item.slot_id;
        recover.textContent = 'Onderbroken wijziging herstellen';
        row.append(recover);
      }
      if (enabled && payload.enabled && item.eligible && item.state === 'missing') {
        const option = document.createElement('option');
        option.value = item.slot_id; option.textContent = item.label; slot.append(option);
      }
    });
    const writable = slot.options.length > 0;
    slot.disabled = !writable; value.disabled = !writable; store.disabled = !writable;
    message(writable ? 'De waarde blijft alleen in de private installermap.' :
      'Kies eerst een koppeling of de benodigde toegang is al bewaard.');
  }
  async function refresh() {
    const response = await fetch('/internal/v1/onboarding/secrets/status', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!response.ok) throw new Error('secret status unavailable');
    render(await response.json());
  }
  store.addEventListener('click', async () => {
    const secret = value.value;
    value.value = '';
    try {
      message('Eenmalig privé bewaren…');
      const response = await fetch('/internal/v1/onboarding/secrets/' +
        encodeURIComponent(slot.value) + '/store', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({secret})
      });
      if (!response.ok) throw new Error('secret rejected');
      await refresh();
      window.dispatchEvent(new Event('eva-installer-state-changed'));
    } catch (_error) {
      value.value = '';
      message('De toegang kon niet veilig worden bewaard. Er wordt niets getoond.');
    }
  });
  list.addEventListener('click', async (event) => {
    const rotate = event.target.closest('button[data-rotate]');
    const remove = event.target.closest('button[data-remove]');
    const recover = event.target.closest('button[data-recover]');
    const control = rotate || remove || recover;
    if (!control) return;
    const slotId = control.dataset.rotate || control.dataset.remove || control.dataset.recover;
    let action = rotate ? 'rotate' : remove ? 'remove' : 'recover';
    let body;
    if (rotate) {
      const input = list.querySelector('input[data-rotate-secret="' + slotId + '"]');
      const secret = input ? input.value : '';
      if (input) input.value = '';
      if (!window.confirm('De oude toegang direct vervangen?')) return;
      body = {secret, confirmation: 'TOEGANG VERVANGEN'};
    } else if (remove) {
      if (!window.confirm('Deze koppeling echt losmaken?')) return;
      body = {confirmation: 'KOPPELING LOSMAKEN'};
    } else {
      if (!window.confirm('De onderbroken wijziging veilig afmaken?')) return;
      body = {confirmation: 'WIJZIGING HERSTELLEN'};
    }
    control.disabled = true;
    try {
      message(action === 'rotate' ? 'Toegang veilig vervangen…' :
        action === 'remove' ? 'Koppeling veilig losmaken…' :
        'Onderbroken wijziging herstellen…');
      const response = await fetch('/internal/v1/onboarding/secrets/' +
        encodeURIComponent(slotId) + '/' + action, {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
        });
      if (!response.ok) throw new Error('secret lifecycle rejected');
      await refresh();
      window.dispatchEvent(new Event('eva-installer-state-changed'));
    } catch (_error) {
      control.disabled = false;
      message('De wijziging kon niet veilig worden afgerond.');
    }
  });
  refresh().catch(() => {
    slot.disabled = true; value.disabled = true; store.disabled = true;
    list.textContent = 'De secretstatus is niet leesbaar.'; message('Er is niets gewijzigd.');
  });
})();
"""


async def _small_json(request: Request) -> object:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > _MAX_JSON_BYTES:
                raise HTTPException(
                    status_code=413, detail="secret request is too large"
                )
        except ValueError:
            raise HTTPException(status_code=422, detail="secret request is invalid")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_JSON_BYTES:
            raise HTTPException(status_code=413, detail="secret request is too large")
        raw.extend(chunk)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="secret request is invalid")


def _payload(
    slots: tuple[InstallerSecretSlotStatus, ...] | None,
    *,
    capability_workflow: InstallerCapabilityWorkflow | None,
    lifecycle: InstallerSecretLifecycle | None,
    enabled: bool,
) -> dict[str, object]:
    capability_states: dict[str, str] = {}
    if capability_workflow is not None:
        try:
            capability_states = {
                item.step_id: item.state for item in capability_workflow.status().steps
            }
        except InstallerCapabilityWorkflowError as exc:
            raise HTTPException(
                status_code=503, detail="installer capability status unavailable"
            ) from exc
    by_id = {item.slot_id: item for item in slots or ()}
    rows = []
    for slot_id in SECRET_SLOTS:
        item = by_id.get(slot_id)
        state = item.state if item is not None else "unavailable"
        rows.append(
            {
                "slot_id": slot_id,
                "label": _LABELS[slot_id],
                "state": state,
                "eligible": capability_states.get(slot_id) in {"selected", "blocked"},
                "reference": item.reference if item is not None else None,
                "created_at": item.created_at if item is not None else None,
            }
        )
    return {
        "contract": "EvaInstallerSecretStatus/v1",
        "enabled": enabled,
        "lifecycle_enabled": lifecycle is not None and enabled,
        "slots": rows,
        "non_claims": [
            "Status bevat nooit secretwaarde, secrethash, lengte of hostpad.",
            "Een bewaard secret maakt de koppeling nog niet ready.",
            "Roteren, verwijderen en herstel schrijven alleen geschoonde receipts.",
        ],
    }


def register_installer_secret_routes(
    app: FastAPI,
    *,
    store: InstallerSecretStore | None,
    capability_workflow: InstallerCapabilityWorkflow | None,
    lifecycle: InstallerSecretLifecycle | None,
    enabled: bool,
    require_read: Callable[[Request], None],
    require_write: Callable[[Request], None],
    security_headers: Mapping[str, str],
) -> None:
    locks = {slot_id: Lock() for slot_id in SECRET_SLOTS}

    def statuses() -> tuple[InstallerSecretSlotStatus, ...] | None:
        if store is None:
            return None
        try:
            return store.status()
        except InstallerSecretStoreError as exc:
            raise HTTPException(
                status_code=503, detail="installer secret status unavailable"
            ) from exc

    @app.get("/internal/v1/onboarding/secrets/status")
    def secret_status(request: Request) -> JSONResponse:
        require_read(request)
        return JSONResponse(
            _payload(
                statuses(),
                capability_workflow=capability_workflow,
                lifecycle=lifecycle,
                enabled=enabled,
            ),
            headers=security_headers,
        )

    @app.post("/internal/v1/onboarding/secrets/{slot_id}/store")
    async def secret_store(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        if store is None or capability_workflow is None or not enabled:
            raise HTTPException(
                status_code=409, detail="installer secret intake is not enabled"
            )
        if slot_id not in SECRET_SLOTS:
            raise HTTPException(
                status_code=422, detail="installer secret slot is invalid"
            )
        try:
            capability = capability_workflow.status().step(slot_id)
        except InstallerCapabilityWorkflowError as exc:
            raise HTTPException(
                status_code=503, detail="installer capability status unavailable"
            ) from exc
        if capability.state not in {"selected", "blocked"}:
            raise HTTPException(
                status_code=409, detail="select the capability before storing access"
            )
        body = await _small_json(request)
        if not isinstance(body, dict) or set(body) != {"secret"}:
            raise HTTPException(status_code=422, detail="secret request is invalid")
        try:
            retained = store.store(slot_id=slot_id, secret=body.get("secret"))
        except InstallerSecretInputError as exc:
            raise HTTPException(
                status_code=422, detail="secret value is invalid"
            ) from exc
        except InstallerSecretStoreError as exc:
            raise HTTPException(
                status_code=409, detail="secret could not be stored"
            ) from exc
        return JSONResponse(
            {
                "contract": "EvaInstallerSecretStored/v1",
                "slot_id": retained.slot_id,
                "state": retained.state,
                "reference": retained.reference,
                "created_at": retained.created_at,
                "provider_verified": False,
                "secret_returned": False,
            },
            headers=security_headers,
        )

    def lifecycle_target(slot_id: str) -> InstallerSecretLifecycle:
        if lifecycle is None or not enabled or slot_id not in SECRET_SLOTS:
            raise HTTPException(
                status_code=409, detail="installer secret lifecycle is not enabled"
            )
        return lifecycle

    def lifecycle_response(result: InstallerSecretLifecycleResult) -> JSONResponse:
        return JSONResponse(
            {
                "capability_head_sha256": result.capability_head_sha256,
                "capability_revision": result.capability_revision,
                "completion_evidence_sha256": result.completion_evidence_sha256,
                "contract": "EvaInstallerSecretLifecycleUiResult/v1",
                "generation": result.generation,
                "invalidation_evidence_sha256": (
                    result.invalidation_evidence_sha256
                ),
                "operation": result.operation,
                "secret_returned": False,
                "slot_id": result.slot_id,
                "state": result.state,
            },
            headers=security_headers,
        )

    async def lifecycle_body(request: Request) -> object:
        return await _small_json(request)

    @app.post("/internal/v1/onboarding/secrets/{slot_id}/rotate")
    async def secret_rotate(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        target = lifecycle_target(slot_id)
        body = await lifecycle_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"confirmation", "secret"}
            or body.get("confirmation") != "TOEGANG VERVANGEN"
        ):
            raise HTTPException(status_code=422, detail="secret rotation is invalid")
        lock = locks[slot_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="secret change is already running")
        try:
            try:
                result = target.rotate(slot_id=slot_id, secret=body.get("secret"))
            except InstallerSecretLifecycleError as exc:
                raise HTTPException(
                    status_code=409, detail="secret rotation could not be completed"
                ) from exc
        finally:
            lock.release()
        return lifecycle_response(result)

    @app.post("/internal/v1/onboarding/secrets/{slot_id}/remove")
    async def secret_remove(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        target = lifecycle_target(slot_id)
        body = await lifecycle_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"confirmation"}
            or body.get("confirmation") != "KOPPELING LOSMAKEN"
        ):
            raise HTTPException(status_code=422, detail="secret removal is invalid")
        lock = locks[slot_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="secret change is already running")
        try:
            try:
                result = target.remove(slot_id=slot_id)
            except InstallerSecretLifecycleError as exc:
                raise HTTPException(
                    status_code=409, detail="secret removal could not be completed"
                ) from exc
        finally:
            lock.release()
        return lifecycle_response(result)

    @app.post("/internal/v1/onboarding/secrets/{slot_id}/recover")
    async def secret_recover(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        target = lifecycle_target(slot_id)
        body = await lifecycle_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"confirmation"}
            or body.get("confirmation") != "WIJZIGING HERSTELLEN"
        ):
            raise HTTPException(status_code=422, detail="secret recovery is invalid")
        lock = locks[slot_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="secret change is already running")
        try:
            try:
                result = target.recover(slot_id=slot_id)
            except InstallerSecretLifecycleError as exc:
                raise HTTPException(
                    status_code=409, detail="secret recovery could not be completed"
                ) from exc
        finally:
            lock.release()
        return lifecycle_response(result)


__all__ = [
    "INSTALLER_SECRET_SCRIPT",
    "INSTALLER_SECRET_SECTION",
    "register_installer_secret_routes",
]
