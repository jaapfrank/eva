"""Host-selected, source-bound profile for private digital background work."""
from dataclasses import dataclass
from datetime import UTC
import hashlib
from pathlib import Path
import re

from eva.adapters.postgres.delegated_work import DelegatedWorkReleaseSelector
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_digital_work import (
    DelegatedDigitalWorkGrant, DelegatedDigitalWorkSpec, DelegatedHouseholdDigitalWorkSpec,
    HOUSEHOLD_DIGITAL_CAPABILITIES,
)


DIGITAL_PROFILE_ID = "worker:digital-work:v4"
DIGITAL_OUTPUT_SHA256 = canonical_sha256({
    "contract": "EvaDigitalWorkOutput/v4", "format": "grounded_conversation_reply",
    "tool_evidence": "indexed_verified_connector_receipts", "semantic_verification": False,
})
_ROOT = Path(__file__).resolve().parents[1]
_SOURCES = (
    "contracts/delegated_work.py", "contracts/delegated_digital_work.py",
    "bootstrap/delegated_digital_profile.py", "bootstrap/household_work_corrections.py",
    "adapters/postgres/scheduled_work.py", "adapters/postgres/work_corrections.py",
    "application/background_execution.py", "application/initiative_execution.py",
    "adapters/postgres/initiative_execution.py", "bootstrap/native_initiative_work.py",
    "adapters/postgres/owner_initiatives.py", "application/personal_interests.py",
    "bootstrap/runtime_settings.py", "bootstrap/delegated_work_service.py",
    "contracts/dashboard_access_token.py", "contracts/tool_deadlines.py",
    "application/conversation.py", "application/cancellation.py",
    "adapters/postgres/background_model_journal.py",
    "adapters/postgres/conversation_journal.py", "adapters/postgres/conversation_actions.py",
    "adapters/postgres/external_conversation_tools.py", "adapters/postgres/delegated_work.py",
    "operations/delegated_work_worker.py",
)


@dataclass(frozen=True, slots=True)
class DelegatedDigitalProfile:
    """Configuration comes from the admitted host route, never model-selected URLs."""
    model_profile_id: str
    configuration_sha256: str
    engine_release_sha256: str
    capability_ids: tuple[str, ...]
    execution_source_sha256: tuple[tuple[str, str], ...]
    maximum_model_runs: int = 8
    maximum_completed_model_responses: int = 8
    maximum_tool_calls: int = 6
    maximum_input_tokens: int = 65_536
    maximum_output_tokens: int = 4_000
    maximum_response_input_tokens: int | None = None
    maximum_response_output_tokens: int | None = None
    timeout_seconds: int = 300
    maximum_cost_usd_micros: int | None = None
    historical_grants: tuple[DelegatedDigitalWorkGrant, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.model_profile_id, str) or not self.model_profile_id.strip()
                or not re.fullmatch(r"[0-9a-f]{64}", self.configuration_sha256)
                or not re.fullmatch(r"[0-9a-f]{64}", self.engine_release_sha256)):
            raise ValueError("digital profile requires explicit host model and engine releases")
        if not isinstance(self.capability_ids, tuple) or not isinstance(self.execution_source_sha256, tuple):
            raise ValueError("digital profile collections must be immutable")
        if not self.execution_source_sha256:
            raise ValueError("digital profile must pin its execution implementation")
        names = []
        for name, digest in self.execution_source_sha256:
            path = Path(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name
                    or not name.endswith(".py") or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("digital execution source pin is invalid")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("digital execution source pins must be unique")
        for name, response, attempt in (
                ("input", self.maximum_response_input_tokens, self.maximum_input_tokens),
                ("output", self.maximum_response_output_tokens, self.maximum_output_tokens)):
            if response is not None and (
                    type(response) is not int or response < 1
                    or attempt != response * self.maximum_completed_model_responses):
                raise ValueError(f"digital {name} budget must bind response and attempt limits")
        if (not isinstance(self.historical_grants, tuple)
                or any(not isinstance(grant, DelegatedDigitalWorkGrant)
                       for grant in self.historical_grants)):
            raise ValueError("historical digital grants must be explicit immutable host grants")
        self.grant()

    def grant(self):
        return DelegatedDigitalWorkGrant(
            contract="DelegatedDigitalWorkGrant/v4", schema_version=4,
            context_refs=[], capability_ids=list(self.capability_ids), workspace_ref=None,
            child_network_access=False, delegation_depth=1, maximum_claims_before_call_start=2,
            maximum_model_runs=self.maximum_model_runs,
            maximum_completed_model_responses=self.maximum_completed_model_responses,
            maximum_tool_calls=self.maximum_tool_calls, maximum_input_tokens=self.maximum_input_tokens,
            maximum_output_tokens=self.maximum_output_tokens,
            maximum_total_tokens=self.maximum_input_tokens + self.maximum_output_tokens,
            maximum_cost_usd_micros=self.maximum_cost_usd_micros, timeout_seconds=self.timeout_seconds,
        )

    def compatible_grants(self):
        """Accept exact historical envelopes without upgrading an admitted attempt."""
        current = self.grant()
        if self.historical_grants:
            return (current, *self.historical_grants)
        values = [current]
        if self.maximum_response_input_tokens is not None:
            current = current.model_copy(update={
                "maximum_input_tokens": self.maximum_response_input_tokens,
                "maximum_total_tokens": self.maximum_response_input_tokens + current.maximum_output_tokens})
            values.append(current)
        if self.maximum_response_output_tokens is not None:
            values.append(current.model_copy(update={
                "maximum_output_tokens": self.maximum_response_output_tokens,
                "maximum_total_tokens": current.maximum_input_tokens + self.maximum_response_output_tokens}))
        return tuple(values)

    def accepts_grant(self, grant):
        """Old work keeps its admitted bounds; compatible is not an upgrade."""
        if not isinstance(grant, DelegatedDigitalWorkGrant):
            return False
        requested = grant.to_payload()
        capabilities = requested.pop("capability_ids")
        if not set(capabilities).issubset(self.capability_ids):
            return False
        for candidate in self.compatible_grants():
            installed = candidate.to_payload()
            installed.pop("capability_ids")
            if requested == installed:
                return True
        return False

    def accepts_prior_profile(self, grant, profile_release):
        """Bind historical household reads to both the old release and its budget."""
        requested = grant.to_payload()
        requested["capability_ids"] = list(self.capability_ids)
        if not self.accepts_grant(grant):
            return False
        for candidate in self.compatible_grants():
            if requested != candidate.to_payload():
                continue
            payload = candidate.to_payload()
            profiles = [payload]
            # Read-only compatibility with the exact pre-research profile.
            # Execution still requires the current release and original grant.
            if "research_current_options" in payload["capability_ids"]:
                profiles.append({**payload, "capability_ids": [
                    name for name in payload["capability_ids"]
                    if name != "research_current_options"]})
            if any(profile_release == canonical_sha256({
                    "profile": DIGITAL_PROFILE_ID, "grant": value}) for value in profiles):
                return True
        return False

    @property
    def release(self):
        sources = {name: hashlib.sha256((_ROOT / name).read_bytes()).hexdigest() for name in _SOURCES}
        for name, expected in self.execution_source_sha256:
            actual = hashlib.sha256((_ROOT / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("digital execution source differs from host release")
            sources[name] = actual
        return DelegatedWorkReleaseSelector(
            canonical_sha256({"profile": DIGITAL_PROFILE_ID, "grant": self.grant().to_payload()}),
            self.configuration_sha256,
            canonical_sha256({"contract": "EvaDigitalWorkProgram/v4", "sources": sources}),
            self.engine_release_sha256, DIGITAL_OUTPUT_SHA256,
        )

    def build_spec(self, *, source, source_ingress_sha256, grant=None, origin=None, initiative_origin=None, **identity):
        def stamp(value):
            if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
                raise ValueError("digital work timestamps require aware whole seconds")
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        selected_grant = self.grant() if grant is None else grant
        if origin is not None and initiative_origin is not None:
            raise ValueError("digital work cannot combine initiative and household origins")
        if origin is not None and grant is None:
            selected_grant = DelegatedDigitalWorkGrant.model_validate({
                **selected_grant.to_payload(),
                "capability_ids": [name for name in selected_grant.capability_ids
                                   if name in HOUSEHOLD_DIGITAL_CAPABILITIES],
            })
        if not self.accepts_grant(selected_grant):
            raise ValueError("digital work grant is incompatible with the installed profile")
        created, deadline = identity.pop("created_at"), identity.pop("deadline")
        release = self.release
        if "execution_id" not in identity:
            if identity.get("generation") != 1:
                raise ValueError("digital resume must retain the initial execution identity")
            identity["execution_id"] = identity["work_id"]
        spec_type = DelegatedDigitalWorkSpec
        envelope = dict(contract="DelegatedDigitalWork/v4", schema_version=4, audience="private")
        if initiative_origin is not None:
            from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
            spec_type = DelegatedInitiativeDigitalWorkSpec
            envelope = dict(contract="DelegatedInitiativeDigitalWork/v1", schema_version=1,
                audience="private", origin=initiative_origin)
        if origin is not None:
            from uuid import NAMESPACE_URL, uuid5
            from eva.contracts.delegated_work import HouseholdWorkOrigin
            origin = HouseholdWorkOrigin.model_validate(origin)
            spec_type = DelegatedHouseholdDigitalWorkSpec
            envelope = dict(contract="DelegatedHouseholdDigitalWork/v1", schema_version=1,
                audience="household", origin=origin.to_payload())
            identity.setdefault("episode_id", str(uuid5(NAMESPACE_URL,
                "household-digital-work:" + canonical_sha256(origin.destination()))))
            identity.setdefault("destination_sha256", canonical_sha256({
                "origin": origin.destination(), "work_id": identity["work_id"],
                "generation": identity["generation"],
            }))
        return spec_type(
            **envelope,
            worker_name="digital-worker", worker_profile_id=DIGITAL_PROFILE_ID,
            worker_profile_release_sha256=release.worker_profile_release_sha256,
            model_profile_id=self.model_profile_id, model_profile_release_sha256=self.configuration_sha256,
            worker_program_sha256=release.worker_program_sha256,
            engine_release_sha256=self.engine_release_sha256, output_schema_sha256=DIGITAL_OUTPUT_SHA256,
            grant=selected_grant.to_payload(), source=source, source_ingress_sha256=source_ingress_sha256,
            created_at=stamp(created), deadline=stamp(deadline), **identity,
        )
