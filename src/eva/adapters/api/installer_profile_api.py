"""Loopback API and small browser UI for Eva's non-secret setup profile."""

from __future__ import annotations

import hmac
import json
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eva.application.installer import (
    InstallerProfilePort,
    InstallerProfileStatusView,
    InstallerUseCaseError,
    InstallerUserInputError,
)


INSTALLER_PROFILE_SECTION = """<section><h2>Basis van deze installatie</h2><p>Deze vier keuzes bevatten geen wachtwoord en zetten nog niets aan.</p><label>Naam van de installatie <input id="profile-installation-name" maxlength="80" autocomplete="off" value="Eva thuis" disabled></label><label>Naam van de eigenaar <input id="profile-owner-label" maxlength="80" autocomplete="off" disabled></label><label>Taal <input id="profile-language" maxlength="35" autocomplete="off" value="nl-NL" disabled></label><label>Tijdzone <input id="profile-time-zone" maxlength="80" autocomplete="off" value="Europe/Amsterdam" disabled></label><div><button type="button" id="profile-stage" disabled>Bewaar deze keuzes</button> <button type="button" id="profile-confirm" disabled>Bevestig deze keuzes</button></div><p id="profile-result" role="status"></p></section>"""


INSTALLER_PROFILE_SCRIPT = r"""
(() => {
  const enabled = window.__evaProfileEnabled === true;
  const fields = {
    installation_name: document.getElementById('profile-installation-name'),
    owner_label: document.getElementById('profile-owner-label'),
    language: document.getElementById('profile-language'),
    time_zone: document.getElementById('profile-time-zone'),
  };
  const stageButton = document.getElementById('profile-stage');
  const confirmButton = document.getElementById('profile-confirm');
  const result = document.getElementById('profile-result');
  let current = null;

  function message(value) { result.textContent = value; }
  function lockFields(locked) {
    Object.values(fields).forEach((field) => { field.disabled = locked; });
  }
  function render(status) {
    current = status;
    if (status.profile) {
      Object.entries(fields).forEach(([key, field]) => {
        field.value = status.profile[key];
      });
    }
    if (!enabled || !status.enabled) {
      lockFields(true);
      stageButton.disabled = true;
      confirmButton.disabled = true;
      message('Deze basisstap is nog niet beschikbaar.');
      return;
    }
    if (status.phase === 'empty') {
      lockFields(false);
      stageButton.disabled = false;
      confirmButton.disabled = true;
      message('Vul de vier basiskeuzes in.');
      return;
    }
    lockFields(true);
    stageButton.disabled = true;
    if (status.phase === 'staged') {
      confirmButton.disabled = false;
      message('Keuzes teruggevonden. Controleer ze en bevestig daarna.');
      return;
    }
    confirmButton.disabled = true;
    message('De basiskeuzes zijn bevestigd. Ze zijn nog nergens geactiveerd.');
  }
  async function request(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'De stap mislukte.');
    return payload;
  }
  async function refresh() {
    const response = await fetch('/internal/v1/onboarding/profile/status', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!response.ok) throw new Error('De bewaarde basiskeuzes zijn niet leesbaar.');
    render(await response.json());
  }
  stageButton.addEventListener('click', async () => {
    try {
      message('Bezig met veilig bewaren…');
      await request('/internal/v1/onboarding/profile/stage', {
        installation_name: fields.installation_name.value,
        owner_label: fields.owner_label.value,
        language: fields.language.value,
        time_zone: fields.time_zone.value,
      });
      await refresh();
    } catch (_error) { message('Deze keuzes konden niet veilig worden bewaard.'); }
  });
  confirmButton.addEventListener('click', async () => {
    if (!current || current.phase !== 'staged' ||
        !window.confirm('Wil je deze vier basiskeuzes bevestigen?')) return;
    try {
      message('Bezig met bevestigen…');
      await request('/internal/v1/onboarding/profile/confirm', {
        profile_sha256: current.profile_sha256,
        confirmation: 'BEVESTIGEN',
      });
      await refresh();
    } catch (_error) { message('Deze keuzes konden niet worden bevestigd.'); }
  });
  refresh().catch(() => {
    lockFields(true); stageButton.disabled = true; confirmButton.disabled = true;
    message('De bewaarde basiskeuzes zijn niet leesbaar.');
  });
})();
"""


def _payload(
    status: InstallerProfileStatusView | None, *, enabled: bool
) -> dict[str, object]:
    profile = None
    if status is not None and status.profile is not None:
        profile = {
            "installation_name": status.profile.installation_name,
            "owner_label": status.profile.owner_label,
            "language": status.profile.language,
            "time_zone": status.profile.time_zone,
        }
    return {
        "contract": "EvaInstallerProfileStatus/v1",
        "enabled": enabled,
        "phase": status.phase if status is not None else "unavailable",
        "revision": status.revision if status is not None else 0,
        "profile_sha256": status.profile_sha256 if status is not None else None,
        "profile": profile,
        "updated_at": status.updated_at if status is not None else None,
        "non_claims": [
            "Deze keuzes maken nog geen installatie of eigenaaraccount.",
            "Deze route bewaart geen connectorsecret en activeert geen dienst.",
        ],
    }


def register_installer_profile_routes(
    app: FastAPI,
    *,
    workflow: InstallerProfilePort | None,
    enabled: bool,
    require_read: Callable[[Request], None],
    require_write: Callable[[Request], None],
    security_headers: Mapping[str, str],
) -> None:
    def current_status() -> InstallerProfileStatusView | None:
        if workflow is None:
            return None
        try:
            return workflow.status()
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=503, detail="installer profile workflow unavailable"
            ) from exc

    def writable() -> InstallerProfilePort:
        if workflow is None or not enabled:
            raise HTTPException(
                status_code=409, detail="installer profile changes are not enabled"
            )
        return workflow

    @app.get("/internal/v1/onboarding/profile/status")
    def profile_status(request: Request) -> JSONResponse:
        require_read(request)
        return JSONResponse(
            _payload(current_status(), enabled=enabled), headers=security_headers
        )

    @app.post("/internal/v1/onboarding/profile/stage")
    async def profile_stage(request: Request) -> JSONResponse:
        require_write(request)
        target = writable()
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="installer profile is invalid")
        expected = {"installation_name", "owner_label", "language", "time_zone"}
        if not isinstance(body, dict) or set(body) != expected:
            raise HTTPException(status_code=422, detail="installer profile is invalid")
        try:
            transition = target.stage(**body)
        except InstallerUserInputError as exc:
            raise HTTPException(
                status_code=422, detail="installer profile is invalid"
            ) from exc
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=409, detail="installer profile could not be staged"
            ) from exc
        return JSONResponse(
            {
                "contract": "EvaInstallerProfileTransition/v1",
                "phase": transition.phase,
                "revision": transition.revision,
                "profile_sha256": transition.profile_sha256,
                "already_in_phase": transition.already_in_phase,
                "active": False,
            },
            headers=security_headers,
        )

    @app.post("/internal/v1/onboarding/profile/confirm")
    async def profile_confirm(request: Request) -> JSONResponse:
        require_write(request)
        target = writable()
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=422, detail="profile confirmation is invalid"
            )
        if not isinstance(body, dict) or set(body) != {
            "profile_sha256",
            "confirmation",
        }:
            raise HTTPException(
                status_code=422, detail="profile confirmation is invalid"
            )
        confirmation = body.get("confirmation")
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            confirmation, "BEVESTIGEN"
        ):
            raise HTTPException(status_code=409, detail="profile is not confirmed")
        try:
            transition = target.confirm(
                expected_profile_sha256=body.get("profile_sha256")
            )
        except InstallerUseCaseError as exc:
            raise HTTPException(
                status_code=409, detail="installer profile could not be confirmed"
            ) from exc
        return JSONResponse(
            {
                "contract": "EvaInstallerProfileTransition/v1",
                "phase": transition.phase,
                "revision": transition.revision,
                "profile_sha256": transition.profile_sha256,
                "already_in_phase": transition.already_in_phase,
                "active": False,
            },
            headers=security_headers,
        )


__all__ = [
    "INSTALLER_PROFILE_SCRIPT",
    "INSTALLER_PROFILE_SECTION",
    "register_installer_profile_routes",
]
