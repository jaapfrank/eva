"""User-facing, loopback-only controls for installer provider verification."""

from __future__ import annotations

import asyncio
import hmac
import json
from threading import Lock
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderVerificationError,
    InstallerProviderVerifier,
)
from eva.operations.installer_secret_store import InstallerSecretStoreError


_MAX_JSON_BYTES = 1_024
_LABELS = {
    "model_provider": "Modelverbinding",
    "telegram": "Telegram",
    "home_assistant": "Home Assistant",
    "email": "E-mail",
}
_CONFIRMATIONS = {
    "model_provider": "MODEL CONTROLEREN",
    "telegram": "VERBINDING CONTROLEREN",
    "home_assistant": "VERBINDING CONTROLEREN",
    "email": "VERBINDING CONTROLEREN",
}


INSTALLER_PROVIDER_SECTION = """<section><h2>Verbinding controleren</h2><p>Eva voert alleen de kleinste controle uit. De modelcontrole gebruikt één kleine modelgeneratie en kan dus meetellen in je gebruik.</p><div id="provider-list"><p>De controles worden veilig geladen…</p></div><p id="provider-result" role="status"></p></section>"""


INSTALLER_PROVIDER_SCRIPT = r"""
(() => {
  const enabled = window.__evaProviderVerificationEnabled === true;
  const list = document.getElementById('provider-list');
  const result = document.getElementById('provider-result');
  function message(text) { result.textContent = text; }
  function render(payload) {
    list.replaceChildren();
    payload.slots.forEach((item) => {
      const row = document.createElement('article');
      const title = document.createElement('strong'); title.textContent = item.label;
      const status = document.createElement('p');
      status.textContent = item.state === 'ready' ? 'gecontroleerd en klaar' :
        item.state === 'blocked' ? 'controle niet gelukt' :
        item.secret_present ? 'toegang aanwezig · nog niet gecontroleerd' :
        'eerst kiezen en toegang invoeren';
      row.append(title, status);
      if (item.available && item.can_verify) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.slot = item.slot_id;
        button.dataset.confirmation = item.confirmation;
        button.textContent = item.billable_request ?
          'Controleer met één modelantwoord' : 'Controleer verbinding';
        button.disabled = !enabled;
        row.append(button);
      }
      list.append(row);
    });
    message(payload.enabled ?
      'Een groene status komt alleen uit de echte providercontrole.' :
      'Providercontrole is in deze installatiesessie niet beschikbaar.');
  }
  async function refresh() {
    const response = await fetch('/internal/v1/onboarding/providers/status', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!response.ok) throw new Error('provider status unavailable');
    render(await response.json());
  }
  list.addEventListener('click', async (event) => {
    const control = event.target.closest('button[data-slot]');
    if (!control) return;
    control.disabled = true;
    try {
      message(control.dataset.slot === 'model_provider' ?
        'Eén klein modelantwoord controleren…' : 'Verbinding controleren…');
      const response = await fetch('/internal/v1/onboarding/providers/' +
        encodeURIComponent(control.dataset.slot) + '/verify', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({confirmation: control.dataset.confirmation})
        });
      if (!response.ok) throw new Error('provider verification rejected');
      const outcome = await response.json();
      message(outcome.outcome === 'ready' ? 'De verbinding is echt gecontroleerd.' :
        'De controle is niet gelukt. De koppeling blijft geblokkeerd.');
      await refresh();
      window.dispatchEvent(new Event('eva-installer-state-changed'));
    } catch (_error) {
      control.disabled = false;
      message('De controle kon niet veilig worden uitgevoerd.');
    }
  });
  window.addEventListener('eva-installer-state-changed', () => {
    refresh().catch(() => {});
  });
  refresh().catch(() => {
    list.textContent = 'De providerstatus is niet leesbaar.';
    message('Er is niets gewijzigd.');
  });
})();
"""


async def _small_json(request: Request) -> object:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > _MAX_JSON_BYTES:
                raise HTTPException(
                    status_code=413, detail="provider verification request is too large"
                )
        except ValueError:
            raise HTTPException(
                status_code=422, detail="provider verification request is invalid"
            )
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_JSON_BYTES:
            raise HTTPException(
                status_code=413, detail="provider verification request is too large"
            )
        raw.extend(chunk)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=422, detail="provider verification request is invalid"
        )


def _payload(
    verifier: InstallerProviderVerifier | None, *, enabled: bool
) -> dict[str, object]:
    capabilities: dict[str, object] = {}
    secrets: dict[str, object] = {}
    available: frozenset[str] = frozenset()
    if verifier is not None:
        try:
            capabilities = {
                item.step_id: item
                for item in verifier.capability_workflow.status().steps
            }
            secrets = {
                item.slot_id: item for item in verifier.secret_store.status()
            }
        except (
            InstallerCapabilityWorkflowError,
            InstallerSecretStoreError,
        ) as exc:
            raise HTTPException(
                status_code=503, detail="provider verification status unavailable"
            ) from exc
        available = frozenset(verifier.probes)
    rows = []
    for slot_id, label in _LABELS.items():
        capability = capabilities.get(slot_id)
        secret = secrets.get(slot_id)
        state = getattr(capability, "state", "unavailable")
        secret_present = getattr(secret, "state", None) == "present"
        is_available = slot_id in available
        rows.append(
            {
                "available": is_available,
                "billable_request": slot_id == "model_provider",
                "can_verify": (
                    enabled
                    and is_available
                    and state in {"selected", "blocked"}
                    and secret_present
                ),
                "confirmation": _CONFIRMATIONS[slot_id] if is_available else None,
                "evidence_sha256": getattr(capability, "evidence_sha256", None),
                "label": label,
                "secret_present": secret_present,
                "slot_id": slot_id,
                "state": state,
            }
        )
    return {
        "contract": "EvaInstallerProviderVerificationStatus/v1",
        "enabled": enabled,
        "slots": rows,
        "non_claims": [
            "De browser ontvangt geen secret of providerantwoord.",
            "De modelcontrole gebruikt precies één begrensde generatie.",
            "Een mislukte of onduidelijke controle blijft geblokkeerd.",
        ],
    }


def register_installer_provider_routes(
    app: FastAPI,
    *,
    verifier: InstallerProviderVerifier | None,
    enabled: bool,
    require_read: Callable[[Request], None],
    require_write: Callable[[Request], None],
    security_headers: Mapping[str, str],
) -> None:
    locks = {slot_id: Lock() for slot_id in _LABELS}

    @app.get("/internal/v1/onboarding/providers/status")
    def provider_status(request: Request) -> JSONResponse:
        require_read(request)
        return JSONResponse(
            _payload(verifier, enabled=enabled), headers=security_headers
        )

    @app.post("/internal/v1/onboarding/providers/{slot_id}/verify")
    async def provider_verify(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        if verifier is None or not enabled or slot_id not in verifier.probes:
            raise HTTPException(
                status_code=409, detail="provider verification is not enabled"
            )
        body = await _small_json(request)
        if not isinstance(body, dict) or set(body) != {"confirmation"}:
            raise HTTPException(
                status_code=422, detail="provider verification request is invalid"
            )
        confirmation = body.get("confirmation")
        expected = _CONFIRMATIONS[slot_id]
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            confirmation, expected
        ):
            raise HTTPException(
                status_code=409, detail="provider verification is not confirmed"
            )
        lock = locks[slot_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409, detail="provider verification is already running"
            )
        try:
            try:
                result = await asyncio.to_thread(
                    verifier.verify, slot_id=slot_id, timeout_seconds=10.0
                )
            except InstallerProviderVerificationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="provider verification could not be completed",
                ) from exc
        finally:
            lock.release()
        return JSONResponse(
            {
                "capability_revision": result.capability_revision,
                "contract": "EvaInstallerProviderVerificationUiResult/v1",
                "evidence_sha256": result.evidence_sha256,
                "outcome": result.outcome,
                "provider_payload_returned": False,
                "reason_code": result.reason_code,
                "secret_returned": False,
                "slot_id": result.slot_id,
            },
            headers=security_headers,
        )


__all__ = [
    "INSTALLER_PROVIDER_SCRIPT",
    "INSTALLER_PROVIDER_SECTION",
    "register_installer_provider_routes",
]
