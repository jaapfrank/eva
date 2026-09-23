"""Public research on the existing delegated lifecycle and indexed tool journal."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from uuid import UUID

from eva.adapters.model.research_tools import OwnerResearchContinuationTool, validate_public_projection
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import DelegatedWorkCall, DelegatedWorkReleaseSelector
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.subagents.tool_free_child import ToolFreeChildOutcome
from eva.application.cancellation import CancellationSignal, cancellation_scope
from eva.application.conversation import ConversationActionCall, ConversationTurn
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import (DelegatedResearchGrant, DelegatedResearchSpec, DelegatedHouseholdResearchSpec,
    DelegatedWorkResult, DelegatedWorkUsage, PublicResearchRequest)
from eva.contracts.timestamps import parse_utc_timestamp
from eva.bootstrap.delegated_work_provider import (
    ENGINE_RELEASE_SHA256, INPUT_PRICE_USD_MICROS_PER_MILLION,
    OUTPUT_PRICE_USD_MICROS_PER_MILLION, WORKER_PROGRAM_SHA256, MODEL_PROFILE_ID,
)

RESEARCH_PROFILE = "worker:public-research:v2"
RESEARCH_PROFILE_HASH = canonical_sha256({"profile": RESEARCH_PROFILE,
    "tool": "research.public_sources", "input_policy": "public_unlinked",
    "journal": "PostgresExternalConversationTools", "automatic_replay": False})
RESEARCH_OUTPUT_HASH = canonical_sha256({"format": "markdown",
    "source": "verified research_artifact_only receipt", "semantic_verification": False})


def research_program_hash():
    root = Path(__file__).resolve().parents[1]
    return canonical_sha256({"delegated": WORKER_PROGRAM_SHA256, "research": {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
            "bootstrap/delegated_research.py", "bootstrap/delegated_work_owner_service.py",
            "adapters/postgres/delegated_work_actions.py", "application/research.py",
            "adapters/model/research_tools.py", "adapters/model/pydantic_conversation.py",
            "adapters/model/research_transport.py",
            "adapters/postgres/external_conversation_tools.py",
            "adapters/postgres/conversation_actions.py", "adapters/postgres/conversation_journal.py",
            "application/cancellation.py", "application/orchestration.py")}})


@dataclass(frozen=True)
class DelegatedResearchProfile:
    """Host-selected provider and bounds, explicitly composed on both ends."""
    model_profile_id: str
    configuration_sha256: str
    maximum_input_tokens: int = 65_536
    maximum_output_tokens: int = 1_200
    maximum_tool_calls: int = 3
    timeout_seconds: int = 60
    maximum_cost_usd_micros: int = 100_000

    def __post_init__(self):
        if self.timeout_seconds != 60:
            raise ValueError("this research profile uses the existing 60-second tool deadline")
        if self.model_profile_id != MODEL_PROFILE_ID:
            raise ValueError("research accounting requires the existing Luna model profile")

    @property
    def release(self):
        return DelegatedWorkReleaseSelector(RESEARCH_PROFILE_HASH,
            self.configuration_sha256, research_program_hash(), ENGINE_RELEASE_SHA256,
            RESEARCH_OUTPUT_HASH)

    spec_type = DelegatedResearchSpec
    contract_name = "DelegatedResearchWork/v2"
    schema_version = 2
    audience = "private"
    profile_id = RESEARCH_PROFILE

    def build_spec(self, *, research, **identity):
        public = PublicResearchRequest.model_validate(research)
        validate_public_projection(public.arguments())
        def stamp(value):
            if value.tzinfo is None or value.microsecond:
                raise ValueError("research timestamps require aware whole seconds")
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        created, deadline = identity.pop("created_at"), identity.pop("deadline")
        grant = DelegatedResearchGrant(contract="DelegatedResearchGrant/v2", schema_version=2,
            context_refs=[], capability_ids=["research.public_sources"], workspace_ref=None,
            child_network_access=False, maximum_model_runs=1, maximum_completed_model_responses=1,
            maximum_input_tokens=self.maximum_input_tokens, maximum_output_tokens=self.maximum_output_tokens,
            maximum_total_tokens=self.maximum_input_tokens + self.maximum_output_tokens,
            maximum_cost_usd_micros=self.maximum_cost_usd_micros, timeout_seconds=self.timeout_seconds,
            maximum_tool_calls=self.maximum_tool_calls, maximum_claims_before_call_start=2, delegation_depth=1)
        return self.spec_type(contract=self.contract_name, schema_version=self.schema_version,
            **identity, audience=self.audience, worker_name="onderzoeker",
            worker_profile_id=self.profile_id, worker_profile_release_sha256=self.release.worker_profile_release_sha256,
            model_profile_id=self.model_profile_id, model_profile_release_sha256=self.configuration_sha256,
            worker_program_sha256=self.release.worker_program_sha256, engine_release_sha256=ENGINE_RELEASE_SHA256,
            output_schema_sha256=RESEARCH_OUTPUT_HASH, grant=grant.to_payload(),
            research=public.to_payload(), created_at=stamp(created), deadline=stamp(deadline))



class HouseholdResearchProfile(DelegatedResearchProfile):
    """A distinct release discriminator on the same bounded public tool runner."""
    spec_type = DelegatedHouseholdResearchSpec
    contract_name = "DelegatedHouseholdResearchWork/v3"
    schema_version = 3
    audience = "household"
    profile_id = "worker:household-public-research:v3"

    @property
    def release(self):
        root = Path(__file__).resolve().parents[1]
        program = canonical_sha256({"research": research_program_hash(), "household": {
            name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in (
                "bootstrap/household_research_policy.py", "bootstrap/household_work.py",
                "bootstrap/household_voice.py", "application/memory_audience.py",
                "adapters/postgres/household_work_sessions.py",
                "bootstrap/household_work_composition.py",
                "adapters/postgres/scoped_delegated_work_actions.py")}})
        return DelegatedWorkReleaseSelector(canonical_sha256({"profile": self.profile_id}),
            self.configuration_sha256, program, ENGINE_RELEASE_SHA256, RESEARCH_OUTPUT_HASH)


def build_public_research_composition(*, model_access_lease_file: Path, resource_budget):
    """Shared core/worker factory; inspect current authority without provider traffic."""
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.contracts.runtime_budget import RuntimeResourceBudget
    from eva.bootstrap.delegated_work_provider import (
        MODEL_NAME, MODEL_ENDPOINT, validate_delegated_model_access_lease)
    if not isinstance(resource_budget, RuntimeResourceBudget):
        raise TypeError("public research requires the installed runtime resource budget")
    if not model_access_lease_file.is_absolute():
        raise ValueError("public research lease must use an absolute path")
    validate_delegated_model_access_lease(model_access_lease_file)
    research = PydanticConversationModel(auth_file=model_access_lease_file,
        model=MODEL_NAME, endpoint=MODEL_ENDPOINT, reasoning_effort="low",
        timeout_seconds=60, research_timeout_seconds=60, resource_budget=resource_budget)
    profile = DelegatedResearchProfile(MODEL_PROFILE_ID, research.research_configuration_sha256(),
        maximum_input_tokens=resource_budget.max_input_tokens,
        maximum_output_tokens=resource_budget.max_output_tokens, maximum_tool_calls=3)
    return research, profile


class DelegatedResearchRunner:
    """One admitted read-only research action; no model planner or second scheduler."""
    def __init__(self, store, research, profile: DelegatedResearchProfile, *, clock=None):
        self.store, self.research, self.profile = store, research, profile
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    async def run(self, spec, *, call_id, cancellation_event=None):
        usage = DelegatedWorkUsage.unmeasured()
        signal = CancellationSignal()
        tools = turn = None
        call = DelegatedWorkCall(spec.work_id, "research:" + spec.work_id, call_id, spec)
        def authorize(turn):
            signal.check()
            if (turn.tenant_id != (spec.origin.tenant_id if isinstance(spec, DelegatedHouseholdResearchSpec) else str(self.store.scope.tenant_id))
                    or turn.installation_id != (spec.origin.installation_id if isinstance(spec, DelegatedHouseholdResearchSpec) else str(self.store.scope.installation_id))
                    or turn.principal_id != (spec.origin.actor_id if isinstance(spec, DelegatedHouseholdResearchSpec) else spec.owner_principal_id)
                    or turn.audience.kind != ("household" if isinstance(spec, DelegatedHouseholdResearchSpec) else "private_person")):
                raise ValueError("research turn differs from admitted owner scope")
            if self.clock() >= parse_utc_timestamp(spec.deadline):
                raise ValueError("research deadline expired")
            self.store.authorize_tool_dispatch(call)
        async def watch():
            if cancellation_event is not None:
                await cancellation_event.wait()
                signal.cancel()
        watcher = asyncio.create_task(watch())
        try:
            if (not isinstance(spec, DelegatedResearchSpec)
                    or DelegatedWorkReleaseSelector.from_spec(spec) != self.profile.release
                    or spec.model_profile_id != self.profile.model_profile_id
                    or self.research.research_configuration_sha256() != self.profile.configuration_sha256):
                raise ValueError("research release or provider differs")
            if cancellation_event is not None and cancellation_event.is_set():
                signal.cancel()
            provider_timeout = getattr(self.research, "_research_timeout_seconds", self.profile.timeout_seconds)
            if provider_timeout > self.profile.timeout_seconds:
                raise ValueError("research provider timeout exceeds its grant")
            if (spec.grant.maximum_input_tokens != self.profile.maximum_input_tokens
                    or spec.grant.maximum_output_tokens != self.profile.maximum_output_tokens
                    or spec.grant.maximum_tool_calls != self.profile.maximum_tool_calls
                    or spec.grant.timeout_seconds != self.profile.timeout_seconds
                    or spec.grant.maximum_cost_usd_micros != self.profile.maximum_cost_usd_micros):
                raise ValueError("research grant differs from the configured bounds")
            validate_public_projection(spec.research.arguments())
            # The query alone enters a separate private journal; no recalled memory.
            if isinstance(spec, DelegatedHouseholdResearchSpec):
                from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
                origin = spec.origin
                conversation = "household:" + origin.household_id + ":conversation:research:" + canonical_sha256(spec.work_id)
                reference = HouseholdConsentReference(origin.actor_id, origin.household_id, origin.consent_operation_id)
                turn = ConversationTurn(origin.tenant_id, origin.installation_id,
                    origin.actor_id, conversation, conversation + ":" + call_id, spec.task, spec.created_at,
                    MemoryAudience.household(principal_id=origin.actor_id, household_id=origin.household_id, consent_ref=reference))
            else:
                turn = ConversationTurn.private(str(self.store.scope.tenant_id), str(self.store.scope.installation_id),
                    spec.owner_principal_id, "delegated-research:" + canonical_sha256(spec.work_id)[:32],
                    call_id, spec.task, spec.created_at)
            arguments = spec.research.arguments()
            # Host disclosure checks above do not confer initiative authority.
            arguments["public_review"] = "not_applicable"
            adapter = OwnerResearchContinuationTool(self.research, PostgresConversationActions._validated_research_arguments)
            tools = PostgresExternalConversationTools(self.store._engine, adapter, adapter, authorize_turn=authorize)
            def execute():
                with cancellation_scope(signal):
                    authorize(turn)
                    PostgresConversationJournal(self.store._engine, external_timeline_scope(
                        turn.tenant_id, turn.installation_id, "research journal scope")).admit(turn)
                    return tools.execute(turn, ConversationActionCall(call_id, "research_current_options", arguments))
            receipt = await asyncio.to_thread(execute)
            output = receipt.output
            artifact = output.get("result", {})
            raw = artifact.get("model_usage", {})
            usage = self._usage(raw)
            if output.get("status") != "verified" or not artifact.get("sources"):
                raise ValueError("research lacks a verified source artifact")
            if artifact.get("question") != spec.task or artifact.get("title") != spec.research.title:
                raise ValueError("research result differs from admitted task")
            content = artifact.get("content")
            if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != artifact.get("content_sha256"):
                raise ValueError("research content hash differs")
            if usage.measurement != "measured" or usage.completed_model_responses != 1 or not usage.tool_calls:
                raise ValueError("research requires measured actual tool use")
            for actual, maximum in ((usage.input_tokens,spec.grant.maximum_input_tokens),
                (usage.output_tokens,spec.grant.maximum_output_tokens), (usage.tool_calls,spec.grant.maximum_tool_calls),
                (usage.cost_usd_micros,spec.grant.maximum_cost_usd_micros)):
                if actual > maximum:
                    raise ValueError("research usage exceeds its grant")
            sources = "\n".join(f"- {source['title']}: {source['url']}" for source in artifact['sources']
                if source["url"] not in content)
            text = content + ("\n\nBronnen:\n" + sources if sources else "")
            authorize(turn)
            result = DelegatedWorkResult(contract="DelegatedWorkResult/v1", schema_version=1,
                work_id=spec.work_id, call_id=call_id, output=text,
                output_sha256=hashlib.sha256(text.encode()).hexdigest(), spec_sha256=spec.sha256,
                usage=usage.to_payload(), completed_at=self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return ToolFreeChildOutcome("succeeded", result, usage, False, None)
        except Exception as error:
            measured = getattr(error, "model_usage", None)
            if measured is not None:
                usage = self._usage(measured.to_wire())
            elif usage.measurement == "unmeasured" and tools is not None and turn is not None:
                # Recover measured failure usage from the same durable journal, never replay it.
                events = tools._timeline(turn).events(tools._thread_id(turn, "research_current_options"))
                for event in reversed(events):
                    data = event.payload.get("data", {})
                    raw = data.get("model_usage")
                    if not isinstance(raw, dict):
                        # Dispatch may have finished before policy revocation blocked
                        # observation. Retain its metering without releasing content.
                        dispatch = data.get("dispatch", {})
                        details = dispatch.get("details", {})
                        artifact = details.get("research_artifact")
                        if (isinstance(artifact, dict)
                                and artifact.get("question") == spec.task
                                and artifact.get("title") == spec.research.title
                                and details.get("artifact_sha256") == canonical_sha256(artifact)
                                and dispatch.get("provider_ref") == canonical_sha256(artifact)):
                            raw = artifact.get("model_usage")
                    if isinstance(raw, dict):
                        recovered = self._usage(raw)
                        if recovered.measurement != "unmeasured":
                            usage = recovered
                            break
            return ToolFreeChildOutcome("cancelled" if cancellation_event is not None and cancellation_event.is_set() else "internal_error", None, usage, False,
                hashlib.sha256((type(error).__name__ + ":" + str(error)).encode()).hexdigest())
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    @staticmethod
    def _usage(raw):
        if raw.get("measurement") not in {"measured", "partial"}:
            return DelegatedWorkUsage.unmeasured()
        inputs, outputs = raw["input_tokens"], raw["output_tokens"]
        cost = ((inputs * INPUT_PRICE_USD_MICROS_PER_MILLION + 999_999) // 1_000_000
            + (outputs * OUTPUT_PRICE_USD_MICROS_PER_MILLION + 999_999) // 1_000_000)
        return DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
            measurement=raw["measurement"], completed_model_responses=raw["model_attempts"],
            tool_calls=raw["tool_calls"], input_tokens=inputs, output_tokens=outputs,
            total_tokens=inputs+outputs, cost_usd_micros=cost)


class ResearchEnabledDelegatedRunner:
    """Composition only: the existing worker claims either sealed profile."""
    def __init__(self, tool_free_runner, research, profile, *, clock=None, household_authority=None):
        self.tool_free_runner, self.research, self.profile = tool_free_runner, research, profile
        self.clock = clock
        self.household_authority = household_authority
        self.household_authorizer = None if household_authority is None else household_authority.validate_spec

    def bind_store(self, store):
        research_runner = DelegatedResearchRunner(store, self.research, self.profile, clock=self.clock)
        household_runner = (None if self.household_authority is None else DelegatedResearchRunner(
            store, self.research, self.household_authority.profile, clock=self.clock))
        tool_free = self.tool_free_runner
        class BoundRunner:
            async def run(self, spec, *, call_id, cancellation_event=None):
                if isinstance(spec, DelegatedHouseholdResearchSpec):
                    if household_runner is None:
                        raise ValueError("household research is not composed")
                    selected = household_runner
                else:
                    selected = research_runner if isinstance(spec, DelegatedResearchSpec) else tool_free
                return await selected.run(spec, call_id=call_id, cancellation_event=cancellation_event)
        return BoundRunner()


async def serve_research_enabled_work(settings, research, profile, *, once=False, household_authority=None, digital_settings=None):
    """Opt-in composition inside the existing service loop; no separate scheduler."""
    from eva.bootstrap.delegated_work_service import (
        _open_provider_runner, _provider_release, serve_delegated_work_service)
    async with _open_provider_runner(settings.model_access_lease_file) as tool_free:
        return await serve_delegated_work_service(
            ResearchEnabledDelegatedRunner(tool_free, research, profile, household_authority=household_authority),
            release=(_provider_release(), profile.release) + (() if household_authority is None else (household_authority.profile.release,)), settings=settings.service, once=once, digital_settings=digital_settings)
