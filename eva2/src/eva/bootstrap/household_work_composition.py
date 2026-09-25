"""Explicit shared core/worker composition; no environment authority inference."""
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID
from eva.contracts.base import Version1Contract
from eva.contracts.runtime_budget import RuntimeResourceBudget
from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.bootstrap.household_voice import HouseholdVoiceAuthority, _read_policy
from eva.bootstrap.household_research_policy import HouseholdResearchAuthority, read_household_research_policy
from eva.bootstrap.delegated_research import HouseholdResearchProfile
from eva.bootstrap.delegated_work_provider import validate_delegated_model_access_lease


class HouseholdWorkConfiguration(Version1Contract):
    contract: Literal["HouseholdWorkConfiguration/v1"]
    task_tenant_id: UUID
    task_installation_id: UUID
    research_policy_file: str
    voice_policy_file: str
    voice_token_file: str
    identity_file: str
    identity_trust_file: str
    budget_file: str


def load_household_work_authority(path, *, scope, profile, model_lease_file, voice_authority=None, digital_profile=None):
    if path is None:
        return None
    config = HouseholdWorkConfiguration.model_validate_json(read_private_runtime_file_bytes(
        str(path), setting_name="EVA_HOUSEHOLD_WORK_CONFIG_FILE"))
    for name in HouseholdWorkConfiguration.model_fields:
        if name.endswith("_file") and not Path(getattr(config, name)).is_absolute():
            raise ValueError("household work configuration requires absolute files")
    def identity():
        return load_current_identity_pack(Path(config.identity_file),
            trust=load_identity_pack_trust_binding(Path(config.identity_trust_file)))
    voice_policy = _read_policy(config.voice_policy_file)
    token = read_private_runtime_file_bytes(config.voice_token_file,
        setting_name="EVA_HOUSEHOLD_VOICE_TOKEN_FILE").decode().strip()
    if voice_authority is None:
        voice_authority = HouseholdVoiceAuthority(path=config.voice_policy_file,
            token_path=config.voice_token_file, policy=voice_policy, token=token,
            identity_loader=identity, clock=lambda: datetime.now(UTC))
    elif (voice_authority.policy != voice_policy or voice_authority.token != token
            or Path(voice_authority._path) != Path(config.voice_policy_file)
            or Path(voice_authority._token_path) != Path(config.voice_token_file)):
        raise ValueError("household work differs from the current voice authority")
    voice_authority.validate_current()
    if scope is None or (config.task_tenant_id, config.task_installation_id) != (scope.tenant_id, scope.installation_id):
        raise ValueError("household work composition scope differs")
    def budget():
        return RuntimeResourceBudget.from_bytes(read_private_runtime_file_bytes(
            config.budget_file, setting_name="EVA_RUNTIME_RESOURCE_BUDGET_FILE"),
            expected_tenant_id=voice_policy.tenant_id,
            expected_installation_id=voice_policy.installation_id)
    authority = HouseholdResearchAuthority(voice_authority=voice_authority,
        policy_loader=lambda: read_household_research_policy(config.research_policy_file),
        budget_loader=budget,
        provider_validator=lambda: validate_delegated_model_access_lease(Path(model_lease_file)),
        profile=HouseholdResearchProfile(**asdict(profile)))

    if digital_profile is not None:
        from eva.bootstrap.household_digital_work import with_household_digital
        authority = with_household_digital(authority, digital_profile)
    return authority
