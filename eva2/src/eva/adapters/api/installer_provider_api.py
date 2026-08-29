"""User-facing, loopback-only controls for installer provider verification."""

from __future__ import annotations

import asyncio
import hmac
import json
from threading import Lock
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.adapters.connectors.installer_home_assistant_probe import (
    HomeAssistantInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderProbe,
    InstallerProviderVerificationError,
    InstallerProviderVerifier,
    installer_provider_scope_manifest,
    installer_provider_scope_manifest_sha256,
)
from eva.operations.installer_provider_configuration import (
    PROVIDER_CONFIGURATION_SLOTS,
    InstallerProviderConfigurationError,
    InstallerProviderConfigurationInputError,
    InstallerProviderConfigurationStore,
)
from eva.operations.installer_secret_store import InstallerSecretStoreError


_MAX_JSON_BYTES = 2_048
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
      const scopeTitle = document.createElement('p');
      scopeTitle.textContent = 'Eva gebruikt deze toegang alleen voor:';
      const scopes = document.createElement('ul');
      item.provider_scopes.forEach((scope) => {
        const line = document.createElement('li');
        line.textContent = scope.description;
        scopes.append(line);
      });
      row.append(title, status, scopeTitle, scopes);
      if (item.can_configure) {
        const config = document.createElement('input');
        config.type = item.slot_id === 'home_assistant' ? 'url' : 'text';
        config.autocomplete = 'off'; config.spellcheck = false;
        config.maxLength = 1024; config.dataset.configuration = item.slot_id;
        config.placeholder = item.slot_id === 'home_assistant' ?
          'http://homeassistant.local:8123' : 'AgentMail inbox-ID';
        const save = document.createElement('button');
        save.type = 'button'; save.dataset.configure = item.slot_id;
        save.textContent = item.configuration_present ? 'Instelling wijzigen' : 'Instelling bewaren';
        save.disabled = !enabled;
        row.append(config, save);
      }
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
    const configure = event.target.closest('button[data-configure]');
    if (configure) {
      const slotId = configure.dataset.configure;
      const input = list.querySelector('input[data-configuration="' + slotId + '"]');
      const configuration = input ? input.value : '';
      if (input) input.value = '';
      configure.disabled = true;
      try {
        message('Instelling privé bewaren…');
        const response = await fetch('/internal/v1/onboarding/providers/' +
          encodeURIComponent(slotId) + '/configure', {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({configuration})
          });
        if (!response.ok) throw new Error('provider configuration rejected');
        await refresh();
        window.dispatchEvent(new Event('eva-installer-state-changed'));
      } catch (_error) {
        configure.disabled = false;
        message('De instelling kon niet veilig worden bewaard.');
      }
      return;
    }
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


def _configured_probe(slot_id: str, value: str) -> InstallerProviderProbe:
    if slot_id == "home_assistant":
        return HomeAssistantInstallerReadOnlyProbe(base_url=value)
    if slot_id == "email":
        return AgentMailInstallerReadOnlyProbe(inbox_id=value)
    raise ValueError("provider configuration slot is invalid")


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
    verifier: InstallerProviderVerifier | None,
    configuration_store: InstallerProviderConfigurationStore | None,
    *,
    enabled: bool,
) -> dict[str, object]:
    capabilities: dict[str, object] = {}
    secrets: dict[str, object] = {}
    available: frozenset[str] = frozenset()
    configurations: dict[str, object] = {}
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
    if configuration_store is not None:
        try:
            configurations = {
                item.slot_id: item for item in configuration_store.status()
            }
        except InstallerProviderConfigurationError as exc:
            raise HTTPException(
                status_code=503, detail="provider configuration status unavailable"
            ) from exc
        available = frozenset({*available, *PROVIDER_CONFIGURATION_SLOTS})
    rows = []
    for slot_id, label in _LABELS.items():
        capability = capabilities.get(slot_id)
        secret = secrets.get(slot_id)
        state = getattr(capability, "state", "unavailable")
        secret_present = getattr(secret, "state", None) == "present"
        is_available = slot_id in available
        configuration_required = slot_id in PROVIDER_CONFIGURATION_SLOTS
        configuration = configurations.get(slot_id)
        configuration_present = (
            getattr(configuration, "state", None) == "configured"
        )
        scope_manifest = installer_provider_scope_manifest(slot_id)
        rows.append(
            {
                "available": is_available,
                "billable_request": slot_id == "model_provider",
                "can_verify": (
                    enabled
                    and is_available
                    and state in {"selected", "blocked"}
                    and secret_present
                    and (not configuration_required or configuration_present)
                ),
                "can_configure": (
                    enabled
                    and configuration_required
                    and configuration_store is not None
                    and state in {"selected", "blocked"}
                ),
                "confirmation": _CONFIRMATIONS[slot_id] if is_available else None,
                "configuration_present": configuration_present,
                "configuration_required": configuration_required,
                "configuration_sha256": getattr(
                    configuration, "configuration_sha256", None
                ),
                "evidence_sha256": getattr(capability, "evidence_sha256", None),
                "label": label,
                "provider_scope_manifest_sha256": (
                    installer_provider_scope_manifest_sha256(slot_id)
                ),
                "provider_scopes": scope_manifest["provider_scopes"],
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
            "Een bewaarde endpoint- of inboxinstelling komt niet terug in de browser.",
            "De modelcontrole gebruikt precies één begrensde generatie.",
            "Deze rechten beschrijven Eva's gebruik; sommige providers kunnen een bredere toegangscode uitgeven.",
            "Een mislukte of onduidelijke controle blijft geblokkeerd.",
        ],
    }


def register_installer_provider_routes(
    app: FastAPI,
    *,
    verifier: InstallerProviderVerifier | None,
    configuration_store: InstallerProviderConfigurationStore | None,
    enabled: bool,
    require_read: Callable[[Request], None],
    require_write: Callable[[Request], None],
    security_headers: Mapping[str, str],
    configured_probe_factory: Callable[[str, str], InstallerProviderProbe] | None = None,
) -> None:
    probe_factory = configured_probe_factory or _configured_probe
    locks = {slot_id: Lock() for slot_id in _LABELS}

    @app.get("/internal/v1/onboarding/providers/status")
    def provider_status(request: Request) -> JSONResponse:
        require_read(request)
        return JSONResponse(
            _payload(
                verifier, configuration_store, enabled=enabled
            ),
            headers=security_headers,
        )

    @app.post("/internal/v1/onboarding/providers/{slot_id}/configure")
    async def provider_configure(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        if (
            verifier is None
            or configuration_store is None
            or not enabled
            or slot_id not in PROVIDER_CONFIGURATION_SLOTS
        ):
            raise HTTPException(
                status_code=409, detail="provider configuration is not enabled"
            )
        body = await _small_json(request)
        if not isinstance(body, dict) or set(body) != {"configuration"}:
            raise HTTPException(
                status_code=422, detail="provider configuration request is invalid"
            )
        value = body.get("configuration")
        lock = locks[slot_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409, detail="provider configuration is already changing"
            )
        try:
            try:
                capability = verifier.capability_workflow.status().step(slot_id)
            except InstallerCapabilityWorkflowError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="provider configuration status unavailable",
                ) from exc
            if capability.state not in {"selected", "blocked"}:
                raise HTTPException(
                    status_code=409,
                    detail="select the capability before configuration",
                )
            try:
                if not isinstance(value, str):
                    raise ValueError("provider configuration is invalid")
                probe_factory(slot_id, value)
                retained = configuration_store.configure(
                    slot_id=slot_id, value=value
                )
            except (ValueError, InstallerProviderConfigurationInputError) as exc:
                raise HTTPException(
                    status_code=422, detail="provider configuration is invalid"
                ) from exc
            except InstallerProviderConfigurationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="provider configuration could not be stored",
                ) from exc
        finally:
            lock.release()
        return JSONResponse(
            {
                "configuration_returned": False,
                "configuration_sha256": retained.configuration_sha256,
                "contract": "EvaInstallerProviderConfigured/v1",
                "revision": retained.revision,
                "slot_id": retained.slot_id,
                "state": "configured",
            },
            headers=security_headers,
        )

    @app.post("/internal/v1/onboarding/providers/{slot_id}/verify")
    async def provider_verify(request: Request, slot_id: str) -> JSONResponse:
        require_write(request)
        if verifier is None or not enabled:
            raise HTTPException(
                status_code=409, detail="provider verification is not enabled"
            )
        if (
            slot_id not in PROVIDER_CONFIGURATION_SLOTS
            and slot_id not in verifier.probes
        ):
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
                target_verifier = verifier
                if slot_id in PROVIDER_CONFIGURATION_SLOTS:
                    if configuration_store is None:
                        raise InstallerProviderConfigurationError(
                            "provider configuration is unavailable"
                        )
                    configuration = configuration_store.read(slot_id=slot_id)
                    probe = probe_factory(slot_id, configuration.value)
                    target_verifier = InstallerProviderVerifier(
                        capability_workflow=verifier.capability_workflow,
                        secret_store=verifier.secret_store,
                        evidence_workspace=verifier.evidence_workspace,
                        probes={slot_id: probe},
                        configuration_sha256_by_slot={
                            slot_id: configuration.configuration_sha256
                        },
                        clock=verifier.clock,
                        monotonic=verifier.monotonic,
                    )
            except (ValueError, InstallerProviderConfigurationError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail="provider verification configuration is unavailable",
                ) from exc
            try:
                result = await asyncio.to_thread(
                    target_verifier.verify, slot_id=slot_id, timeout_seconds=10.0
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
                "provider_scope_manifest_sha256": (
                    result.provider_scope_manifest_sha256
                ),
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
