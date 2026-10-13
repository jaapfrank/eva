"""Compose voice from an owner-private, revocable media policy.

This is an actual voice-specific policy, not a claim that bootstrap has loaded
an authoritative global PolicyPack. It grants no tool effects or memory writes.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from eva.adapters.api.dashboard_voice import DashboardVoice
from eva.adapters.connectors.home_assistant_voice import (
    HomeAssistantSpeechInput, HomeAssistantSpeechOutput,
)
from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from eva.contracts.base import Identifier, LanguageTag, Sha256, Timestamp, Version1Contract
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack
from eva.contracts.voice import VoicePrivacy, VoiceSession
from eva.providers.home_assistant import HomeAssistantProviderSettings


class DashboardVoicePolicy(Version1Contract):
    contract: Literal["DashboardVoicePolicy/v1"]
    policy_id: Identifier
    policy_version: int = Field(ge=1, le=2_147_483_647)
    state: Literal["enabled", "revoked"]
    tenant_id: Identifier
    installation_id: Identifier
    dashboard_tenant_id: UUID
    dashboard_installation_id: UUID
    owner_principal_id: UUID
    principal_binding_id: UUID
    identity_sha256: Sha256
    allowed_origin: str = Field(min_length=1, max_length=512)
    provider: Literal["home_assistant"]
    endpoint_url: str = Field(min_length=1, max_length=512)
    pipeline_id: Identifier
    pipeline_language: LanguageTag
    stt_engine: Identifier
    tts_engine: Identifier
    tts_language: LanguageTag
    privacy: VoicePrivacy
    privacy_evidence_ref: str = Field(min_length=1, max_length=1024)
    effective_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _closed_media_policy(self) -> "DashboardVoicePolicy":
        origin = urlsplit(self.allowed_origin)
        if (origin.scheme not in {"http", "https"} or not origin.hostname
                or origin.username or origin.password or origin.path or origin.query
                or origin.fragment or self.allowed_origin != f"{origin.scheme}://{origin.netloc}"
                or (origin.scheme == "http" and origin.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("voice origin requires HTTPS or loopback")
        endpoint = urlsplit(self.endpoint_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or endpoint.path not in {"", "/"}):
            raise ValueError("voice provider endpoint must be an origin")
        if self.privacy.raw_audio_retention != "none":
            raise ValueError("voice diagnostics have no implemented retention path")
        if self.effective_at >= self.expires_at:
            raise ValueError("voice policy has no valid lifetime")
        return self


def _read_policy(path: str) -> DashboardVoicePolicy:
    try:
        return DashboardVoicePolicy.model_validate_json(read_private_runtime_file_bytes(
            path, setting_name="EVA_DASHBOARD_VOICE_POLICY_FILE",
        ))
    except (RuntimeError, ValueError) as error:
        raise ValueError("dashboard voice policy is unavailable or invalid") from error


def build_dashboard_voice(
    settings, *, runtime_identity: IdentityPack,
    identity_loader: Callable[[], IdentityPack],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DashboardVoice | None:
    path = settings.dashboard_voice_policy_file
    if path is None:
        return None
    if not settings.dashboard_access_token or not settings.dashboard_principal_binding_id:
        raise ValueError("voice requires an authenticated dashboard owner")
    try:
        Path(path).lstat()
    except FileNotFoundError:
        # Removal revokes voice, including after a core restart. Other channels stay usable.
        return None
    policy = _read_policy(path)
    expected_scope = (
        settings.background_tenant_id, settings.background_installation_id,
        settings.background_principal_id, settings.dashboard_principal_binding_id,
    )
    # Existing conversation and administration scopes may differ. Bind both;
    # voice admission follows dashboard_turn's runtime conversation scope.
    if (str(policy.dashboard_tenant_id) != settings.dashboard_tenant_id
            or str(policy.dashboard_installation_id) != settings.dashboard_installation_id
            or tuple(str(value) for value in (
                policy.tenant_id, policy.installation_id, policy.owner_principal_id,
                policy.principal_binding_id,
            )) != expected_scope):
        raise ValueError("voice policy differs from the runtime owner scope")
    loaded = () if settings.external_provider is None else settings.external_provider.providers
    matches = [item.settings for item in loaded if item.provider_id == policy.provider]
    if len(matches) != 1 or not isinstance(matches[0], HomeAssistantProviderSettings):
        raise ValueError("voice media provider is not configured")
    provider = matches[0]
    if provider.endpoint_url.rstrip("/") != policy.endpoint_url.rstrip("/"):
        raise ValueError("voice endpoint differs from configured provider")
    policy_hash = canonical_sha256(policy.model_dump(mode="json"))
    identity_hash = canonical_sha256(runtime_identity.model_dump(mode="json"))
    if policy.identity_sha256 != identity_hash or runtime_identity.state != "current":
        raise ValueError("voice identity differs from active runtime")

    def current(*, allow_inactive: bool = False) -> bool:
        actual = _read_policy(path)
        current_identity = identity_loader()
        now = clock()
        if now.tzinfo is None:
            raise ValueError("voice clock must be timezone aware")
        stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if (canonical_sha256(actual.model_dump(mode="json")) != policy_hash
                or current_identity.state != "current"
                or canonical_sha256(current_identity.model_dump(mode="json")) != identity_hash):
            raise ValueError("voice authority changed")
        active = actual.state == "enabled" and actual.effective_at <= stamp < actual.expires_at
        if not active and not allow_inactive:
            raise ValueError("voice authority is inactive")
        return active

    # A valid inactive media grant must not disable unrelated conversation channels.
    if not current(allow_inactive=True):
        return None

    def issue() -> VoiceSession:
        current()
        now = clock().astimezone(UTC).replace(microsecond=0)
        expiry = datetime.fromisoformat(policy.expires_at.replace("Z", "+00:00"))
        return VoiceSession(
            contract="VoiceSession/v1", schema_version=1,
            session_id=f"voice-session:{uuid4()}", tenant_id=str(policy.tenant_id),
            installation_id=str(policy.installation_id), principal_id=str(policy.owner_principal_id),
            conversation_id=f"dashboard:{policy.principal_binding_id}", audience="private",
            channel="local_assist", device_binding_id=f"dashboard:{policy.principal_binding_id}",
            identity_id=runtime_identity.identity_id, identity_pack_version=runtime_identity.pack_version,
            identity_pack_sha256=identity_hash, policy_id=policy.policy_id,
            policy_pack_version=policy.policy_version, policy_pack_sha256=policy_hash,
            privacy=policy.privacy.model_dump(mode="json"), created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            deadline=min(now+timedelta(minutes=30), expiry).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def validate(session: VoiceSession) -> None:
        current()
        if (session.tenant_id != str(policy.tenant_id)
                or session.installation_id != str(policy.installation_id)
                or session.principal_id != str(policy.owner_principal_id)
                or session.conversation_id != f"dashboard:{policy.principal_binding_id}"
                or session.device_binding_id != f"dashboard:{policy.principal_binding_id}"
                or session.identity_id != runtime_identity.identity_id
                or session.identity_pack_version != runtime_identity.pack_version
                or session.channel != "local_assist"
                or session.identity_pack_sha256 != identity_hash
                or session.policy_pack_sha256 != policy_hash
                or session.policy_id != policy.policy_id
                or session.policy_pack_version != policy.policy_version
                or session.privacy != policy.privacy):
            raise ValueError("voice session authority differs")

    media = dict(base_url=provider.endpoint_url, token=provider.credential,
                 pipeline_id=policy.pipeline_id)
    return DashboardVoice(
        allowed_origin=policy.allowed_origin, issue_session=issue, validate_current=validate,
        speech_input=HomeAssistantSpeechInput(
            **media, expected_stt_engine=policy.stt_engine,
            expected_pipeline_language=policy.pipeline_language,
        ),
        speech_output=HomeAssistantSpeechOutput(
            **media, tts_engine=policy.tts_engine, tts_language=policy.tts_language,
        ), language=policy.pipeline_language, clock=clock,
    )
