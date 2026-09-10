"""Owner-file authority and startup composition for native household voice.

This grants shared household conversation/memory, never owner impersonation.
The existing conversation/tool resource budgets remain in the supplied runtime.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from eva.application.memory_audience import HouseholdConsentReference
from eva.contracts.base import Identifier, Sha256, Timestamp, Version1Contract
from eva.contracts.canonical_json import canonical_sha256


class HouseholdVoicePolicy(Version1Contract):
    contract: Literal["HouseholdVoicePolicy/v1"]
    policy_id: Identifier
    policy_version: int = Field(ge=1)
    state: Literal["enabled", "revoked"]
    tenant_id: Identifier
    installation_id: Identifier
    owner_principal_id: UUID
    principal_binding_id: UUID
    identity_sha256: Sha256
    token_sha256: Sha256
    household_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    consent_operation_id: str = Field(pattern=r"^household-consent:[A-Za-z0-9._:-]{1,200}$")
    devices: dict[
        Annotated[str, StringConstraints(pattern=r"^assist_satellite\.[a-z0-9_]{1,183}$")],
        Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")],
    ] = Field(min_length=1, max_length=64)
    effective_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def validate_bindings(self):
        if len(set(self.devices.values())) != len(self.devices):
            raise ValueError("Household voice devices must be unique")
        if self.effective_at >= self.expires_at:
            raise ValueError("Household voice authority has no valid lifetime")
        return self


def _read_policy(path: str) -> HouseholdVoicePolicy:
    try:
        return HouseholdVoicePolicy.model_validate_json(read_private_runtime_file_bytes(
            path, setting_name="EVA_HOUSEHOLD_VOICE_POLICY_FILE"))
    except (RuntimeError, ValueError) as error:
        raise ValueError("Household voice authority is unavailable or invalid") from error


class HouseholdVoiceAuthority:
    def __init__(self, *, path, token_path, policy, token, identity_loader, clock):
        self.policy = policy
        self.token = token
        self._path = path
        self._token_path = token_path
        self._hash = canonical_sha256(policy.model_dump(mode="json"))
        self._identity_loader = identity_loader
        self._clock = clock

    def validate_current(self) -> None:
        actual = _read_policy(self._path)
        identity = self._identity_loader()
        token = read_private_runtime_file_bytes(
            self._token_path, setting_name="EVA_HOUSEHOLD_VOICE_TOKEN_FILE").decode().strip()
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Household voice clock must be timezone aware")
        stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if (canonical_sha256(actual.model_dump(mode="json")) != self._hash
                or actual.state != "enabled"
                or not actual.effective_at <= stamp < actual.expires_at
                or identity.state != "current"
                or canonical_sha256(identity.model_dump(mode="json")) != actual.identity_sha256
                or hashlib.sha256(token.encode()).hexdigest() != actual.token_sha256):
            raise ValueError("Household voice authority is no longer current")

    def active_reference(self, *, principal_id: str, household_id: str,
                         now: datetime) -> HouseholdConsentReference | None:
        try:
            self.validate_current()
        except (RuntimeError, ValueError, OSError):
            return None
        if (now.tzinfo is None or household_id != self.policy.household_id
                or principal_id not in {"household-device:" + value
                                        for value in self.policy.devices.values()}):
            return None
        stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.policy.effective_at <= stamp < self.policy.expires_at:
            return None
        return HouseholdConsentReference(principal_id=principal_id, household_id=household_id,
                                         operation_id=self.policy.consent_operation_id)

    def build_service(self, *, runtime, engine) -> NativeVoiceService:
        from eva.adapters.api.native_voice import NativeVoiceService
        from eva.adapters.postgres.voice_requests import PostgresVoiceRequests
        from eva.bootstrap.native_voice import NativeHouseholdResponder
        self.validate_current()
        policy = self.policy
        return NativeVoiceService(
            token=self.token, devices=policy.devices,
            ledger=PostgresVoiceRequests(engine, tenant_id=policy.tenant_id,
                                         installation_id=policy.installation_id),
            respond=NativeHouseholdResponder(
                runtime=runtime, tenant_id=policy.tenant_id, installation_id=policy.installation_id,
                household_id=policy.household_id, devices=policy.devices, consent=self),
            validate_current=self.validate_current,
        )


def load_household_voice_authority(settings, *, runtime_identity, identity_loader,
                                   clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
    path = settings.household_voice_policy_file
    if path is None:
        return None
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return None
    policy = _read_policy(path)
    if tuple(str(value) for value in (
        policy.tenant_id, policy.installation_id, policy.owner_principal_id,
        policy.principal_binding_id,
    )) != (settings.background_tenant_id, settings.background_installation_id,
           settings.background_principal_id, settings.dashboard_principal_binding_id):
        raise ValueError("Household voice authority differs from runtime owner scope")
    if (runtime_identity.state != "current"
            or canonical_sha256(runtime_identity.model_dump(mode="json")) != policy.identity_sha256):
        raise ValueError("Household voice identity differs from runtime")
    now = clock()
    if now.tzinfo is None:
        raise ValueError("Household voice clock must be timezone aware")
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if policy.state != "enabled" or not policy.effective_at <= stamp < policy.expires_at:
        return None
    token_path = settings.household_voice_token_file
    if token_path is None:
        raise ValueError("Household voice requires a private integration token file")
    token = read_private_runtime_file_bytes(
        token_path, setting_name="EVA_HOUSEHOLD_VOICE_TOKEN_FILE").decode().strip()
    if (not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token)
            or hashlib.sha256(token.encode()).hexdigest() != policy.token_sha256):
        raise ValueError("Household voice integration token is invalid")
    authority = HouseholdVoiceAuthority(path=path, token_path=token_path, policy=policy,
                                        token=token, identity_loader=identity_loader, clock=clock)
    authority.validate_current()
    return authority
