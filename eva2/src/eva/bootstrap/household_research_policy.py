"""Explicit owner-derived public work authority, separate from voice consent.

No default/environment activation: trusted composition must provide all current
voice, provider and resource-budget readers. Model arguments never enter here.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from eva.bootstrap.household_voice import HouseholdVoicePolicy
from eva.bootstrap.delegated_work_provider import (
    INPUT_PRICE_USD_MICROS_PER_MILLION, OUTPUT_PRICE_USD_MICROS_PER_MILLION,
)
from eva.contracts.base import Version1Contract, Identifier, Sha256, Timestamp
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import DelegatedHouseholdResearchSpec
from eva.contracts.runtime_budget import RuntimeResourceBudget


class HouseholdResearchPolicy(Version1Contract):
    contract: Literal["HouseholdResearchPolicy/v1"]
    policy_id: Identifier
    state: Literal["enabled", "revoked"]
    tenant_id: Identifier
    installation_id: Identifier
    household_id: str
    owner_principal_id: UUID
    principal_binding_id: UUID
    household_policy_id: Identifier
    household_policy_sha256: Sha256
    consent_operation_id: Identifier
    devices: dict[str, str] = Field(min_length=1, max_length=64)
    capability: Literal["research.public_sources"]
    result_scope: Literal["originating_household_session"]
    provider_configuration_sha256: Sha256
    resource_budget_sha256: Sha256
    retained_read_spec_sha256: tuple[Sha256, ...] = Field(default=(), max_length=4096)
    maximum_retention_days: Literal[7] = 7
    effective_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _lifetime(self):
        if self.effective_at >= self.expires_at:
            raise ValueError("household research policy lifetime invalid")
        return self


class HouseholdResearchAuthority:
    def __init__(
        self,
        *,
        voice_authority,
        policy_loader,
        budget_loader,
        provider_validator,
        profile,
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ):
        self.voice = voice_authority
        self._policy_loader, self._budget_loader = policy_loader, budget_loader
        self._provider_validator = provider_validator
        self.profile, self.clock = profile, clock
        self._release = profile.release
        self.policy = policy_loader()
        if not isinstance(self.policy, HouseholdResearchPolicy):
            raise TypeError("household research requires an explicit typed policy")
        self.sha256 = canonical_sha256(self.policy.model_dump(mode="json"))
        self.validate_current()

    def validate_current(self):
        self.voice.validate_current()
        current = self._policy_loader()
        budget = self._budget_loader()
        self._provider_validator()
        voice, policy = self.voice.policy, self.policy
        clock = self.clock()
        if clock.tzinfo is None or clock.utcoffset() is None or clock.microsecond:
            raise ValueError("household research clock requires aware whole seconds")
        now = clock.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if (
            not isinstance(voice, HouseholdVoicePolicy)
            or not isinstance(budget, RuntimeResourceBudget)
            or not isinstance(current, HouseholdResearchPolicy)
            or canonical_sha256(current.model_dump(mode="json")) != self.sha256
            or policy.state != "enabled"
            or not policy.effective_at <= now < policy.expires_at
            or policy.household_policy_id != voice.policy_id
            or policy.household_policy_sha256
            != canonical_sha256(voice.model_dump(mode="json"))
            or any(
                getattr(policy, key) != getattr(voice, key)
                for key in (
                    "tenant_id",
                    "installation_id",
                    "household_id",
                    "owner_principal_id",
                    "principal_binding_id",
                    "consent_operation_id",
                )
            )
            or policy.effective_at < voice.effective_at
            or policy.expires_at > voice.expires_at
            or any(voice.devices.get(sat) != dev for sat, dev in policy.devices.items())
            or (budget.tenant_id, budget.installation_id)
            != (policy.tenant_id, policy.installation_id)
            or policy.resource_budget_sha256 != canonical_sha256(budget.to_payload())
            or policy.provider_configuration_sha256 != self.profile.configuration_sha256
            or self.profile.maximum_input_tokens > budget.max_input_tokens
            or self.profile.maximum_output_tokens > budget.max_output_tokens
            or self.profile.maximum_tool_calls > 3
            or self.profile.maximum_cost_usd_micros > (
                (budget.max_input_tokens * INPUT_PRICE_USD_MICROS_PER_MILLION + 999_999) // 1_000_000
                + (budget.max_output_tokens * OUTPUT_PRICE_USD_MICROS_PER_MILLION + 999_999) // 1_000_000
            )
            or self.profile.timeout_seconds != 60
        ):
            raise ValueError(
                "household research authority changed or exceeds its parent"
            )

    def validate_device(self, satellite_id, device_id):
        self.validate_current()
        if self.policy.devices.get(satellite_id) != device_id:
            raise ValueError("household research device unavailable")

    def validate_spec(self, spec):
        """Authorize admission and execution only on the current released program."""
        self._validate_spec(spec, require_current_release=True)

    def permits_retained_read(self, spec):
        """Match only operator-registered stored envelopes, never future work."""
        return (isinstance(spec, DelegatedHouseholdResearchSpec)
                and canonical_sha256(spec.to_payload()) in self.policy.retained_read_spec_sha256)

    def validate_read_spec(self, spec):
        """Read retained stored work without authorizing its historical program."""
        self._validate_spec(spec, require_current_release=False)

    def _validate_spec(self, spec, *, require_current_release):
        self.validate_current()
        if not isinstance(spec, DelegatedHouseholdResearchSpec):
            raise ValueError("household work envelope required")
        origin, policy = spec.origin, self.policy
        from eva.adapters.postgres.delegated_work import DelegatedWorkReleaseSelector
        from eva.contracts.timestamps import parse_utc_timestamp

        retained_read = (
            not require_current_release
            and self.permits_retained_read(spec)
        )
        release = DelegatedWorkReleaseSelector.from_spec(spec)
        if not require_current_release:
            from dataclasses import replace
            # Stored rows already passed contract, hash and result validation.
            # Only executable source may differ; model, schema and grant pins
            # still match the current authority before any retained data leaves.
            release = replace(release, worker_program_sha256=self._release.worker_program_sha256)
        if not retained_read and (
            release != self._release
            or spec.worker_profile_id != self.profile.profile_id
            or spec.model_profile_id != self.profile.model_profile_id
            or spec.grant.maximum_input_tokens != self.profile.maximum_input_tokens
            or spec.grant.maximum_output_tokens != self.profile.maximum_output_tokens
            or spec.grant.maximum_total_tokens
            != self.profile.maximum_input_tokens + self.profile.maximum_output_tokens
            or spec.grant.maximum_claims_before_call_start != 2
            or spec.grant.maximum_tool_calls != self.profile.maximum_tool_calls
            or spec.grant.maximum_cost_usd_micros
            != self.profile.maximum_cost_usd_micros
            or spec.grant.timeout_seconds != self.profile.timeout_seconds
            or parse_utc_timestamp(spec.created_at) > self.clock()
        ):
            raise ValueError("household release or resource grant differs")
        if parse_utc_timestamp(spec.created_at) > self.clock():
            raise ValueError("household work creation time is in the future")
        self.validate_device(origin.satellite_id, origin.device_id)
        if (
            spec.owner_principal_id != str(policy.owner_principal_id)
            or spec.principal_binding_id != str(policy.principal_binding_id)
            or origin.research_policy_id != policy.policy_id
            or (not retained_read and origin.research_policy_sha256 != self.sha256)
            or origin.household_policy_id != policy.household_policy_id
            or origin.household_policy_sha256 != policy.household_policy_sha256
            or (
                origin.tenant_id,
                origin.installation_id,
                origin.household_id,
                origin.consent_operation_id,
            )
            != (
                policy.tenant_id,
                policy.installation_id,
                policy.household_id,
                policy.consent_operation_id,
            )
            or spec.deadline > policy.expires_at
            or spec.created_at < policy.effective_at
            or (not retained_read and spec.model_profile_release_sha256 != self.profile.configuration_sha256)
        ):
            raise ValueError("household work differs from current authority")


def read_household_research_policy(path):
    """Read an explicitly supplied private operator artifact; never infer a grant."""
    from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes

    return HouseholdResearchPolicy.model_validate_json(
        read_private_runtime_file_bytes(
            str(path), setting_name="household research policy"
        )
    )
