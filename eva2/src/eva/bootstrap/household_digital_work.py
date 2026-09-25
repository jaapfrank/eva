"""Reuse durable digital work with the registered household actor and HA tools."""
from datetime import UTC, timedelta
import hashlib
from uuid import NAMESPACE_URL, uuid5

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound, DelegatedWorkReleaseSelector
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import parse_utc_timestamp


from eva.contracts.delegated_digital_work import HOUSEHOLD_DIGITAL_CAPABILITIES as HOUSEHOLD_CAPABILITIES


class HouseholdDigitalAuthority:
    """Add the existing HA authorization to the existing household lifecycle."""
    def __init__(self, parent, profile, settings_loader):
        self.parent, self.digital_profile = parent, profile
        self._settings_loader = settings_loader
        self.validate_current()

    def __getattr__(self, name):
        return getattr(self.parent, name)

    def validate_current(self):
        self.parent.validate_current()
        settings = self._settings_loader()
        if settings is None or not settings.api_enabled:
            raise ValueError("household digital work requires the configured HA API")

    def validate_actor(self, actor):
        self.validate_current()
        settings = self._settings_loader()
        if (actor not in {"household-device:" + value for value in self.policy.devices.values()}
                or hashlib.sha256(actor.encode()).hexdigest() not in settings.effect_principal_sha256_allowlist):
            raise ValueError("household digital actor lacks HA access")

    def validate_spec(self, spec):
        return self._validate_spec(spec, require_current_release=True)

    def validate_read_spec(self, spec):
        return self._validate_spec(spec, require_current_release=False)

    def _validate_spec(self, spec, *, require_current_release):
        from eva.contracts.delegated_digital_work import DelegatedHouseholdDigitalWorkSpec
        if not isinstance(spec, DelegatedHouseholdDigitalWorkSpec):
            validator = self.parent.validate_spec if require_current_release else self.parent.validate_read_spec
            return validator(spec)
        self.validate_actor(spec.origin.actor_id)
        self.validate_device(spec.origin.satellite_id, spec.origin.device_id)
        p, origin, profile = self.policy, spec.origin, self.digital_profile
        actual, current = DelegatedWorkReleaseSelector.from_spec(spec), profile.release
        release_fields = ("worker_profile_release_sha256", "model_profile_release_sha256",
            "engine_release_sha256", "output_schema_sha256")
        legacy_read = False
        if not require_current_release and profile.maximum_response_output_tokens is not None:
            from eva.bootstrap.delegated_digital_profile import DIGITAL_PROFILE_ID
            legacy_grant = profile.grant().model_copy(update={
                "maximum_output_tokens": profile.maximum_response_output_tokens,
                "maximum_total_tokens": profile.maximum_input_tokens + profile.maximum_response_output_tokens})
            legacy_hash = canonical_sha256({"profile": DIGITAL_PROFILE_ID, "grant": legacy_grant.to_payload()})
            legacy_read = (actual.worker_profile_release_sha256 == legacy_hash
                and spec.grant.maximum_output_tokens == legacy_grant.maximum_output_tokens
                and profile.accepts_grant(spec.grant))
        if (any(getattr(actual, key) != getattr(current, key) for key in release_fields
                if not (legacy_read and key == "worker_profile_release_sha256"))
                or (require_current_release and actual.worker_program_sha256 != current.worker_program_sha256)
                or not profile.accepts_grant(spec.grant)
                or spec.model_profile_id != profile.model_profile_id
                or spec.worker_profile_id != "worker:digital-work:v4"
                or not set(spec.grant.capability_ids).issubset(HOUSEHOLD_CAPABILITIES)
                or spec.owner_principal_id != str(p.owner_principal_id)
                or spec.principal_binding_id != str(p.principal_binding_id)
                or origin.research_policy_id != p.policy_id
                or origin.research_policy_sha256 != self.sha256
                or origin.household_policy_id != p.household_policy_id
                or origin.household_policy_sha256 != p.household_policy_sha256
                or any(getattr(origin, key) != getattr(p, key) for key in
                       ("tenant_id", "installation_id", "household_id", "consent_operation_id"))
                or spec.deadline > p.expires_at or spec.created_at < p.effective_at
                or parse_utc_timestamp(spec.created_at) > self.clock()):
            raise ValueError("household digital work differs from current authority")


def with_household_digital(parent, profile, settings_loader=None):
    from eva.providers.home_assistant import HomeAssistantProviderFactory
    loader = settings_loader or HomeAssistantProviderFactory.load_settings
    settings = loader()
    if profile is None or settings is None or not settings.api_enabled:
        return parent
    return HouseholdDigitalAuthority(parent, profile, loader)


def household_source(turn):
    return dict(tenant_id=turn.tenant_id, installation_id=turn.installation_id,
        principal_id=turn.principal_id, conversation_id=turn.conversation_id, message_id=turn.message_id,
        audience=dict(kind="household", principal_id=turn.principal_id,
            household_id=turn.audience.household_id,
            consent_operation_id=turn.audience.household_consent_ref.operation_id))


class HouseholdDigitalSourceLoader:
    def __init__(self, engine, scope, authority):
        self.timeline = PostgresOperationalTimeline(engine, scope)
        self.authority = authority

    def __call__(self, value):
        from eva.contracts.delegated_digital_work import HouseholdWorkSource
        source = HouseholdWorkSource.model_validate(value)
        authority, p = self.authority, self.authority.policy
        authority.validate_actor(source.principal_id)
        if ((source.tenant_id, source.installation_id, source.audience.household_id,
                source.audience.consent_operation_id) !=
                (p.tenant_id, p.installation_id, p.household_id, p.consent_operation_id)):
            raise ConversationConflict("household source scope differs")
        thread = "conversation:" + source.message_id
        self.timeline.load(thread)
        events = self.timeline.events(thread)
        if not events or events[0].event_type != "INGRESS_COMMITTED":
            raise ConversationConflict("household source ingress is absent")
        event = events[0]
        data = event.payload.get("data", {})
        turn = ConversationTurn(source.tenant_id, source.installation_id, source.principal_id,
            source.conversation_id, source.message_id, data["text"],
            event.occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            MemoryAudience.household(principal_id=source.principal_id, household_id=p.household_id,
                consent_ref=HouseholdConsentReference(source.principal_id, p.household_id, p.consent_operation_id)))
        if PostgresConversationJournal._ingress_data(turn) != data:
            raise ConversationConflict("household original ingress differs")
        return turn


def admit_household_digital(service, turn, request_key, task, *, previous=None, admission_time=None,
                            household_continuation_execution_id=None):
    """Use the same admission, resume receipts and delivery scope as research."""
    profile = service.digital_profile
    if profile is None:
        raise ValueError("household digital work is unavailable")
    service.validate_authority(turn)
    origin = service.origin(turn) if previous is None else previous.spec.origin
    service.server.authority.validate_actor(origin.actor_id)
    created = admission_time or parse_utc_timestamp(turn.occurred_at)
    expiry = parse_utc_timestamp(service.server.authority.policy.expires_at)
    key = canonical_sha256(dict(origin=origin.to_payload(), request_key=request_key))
    if previous is None:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("household digital task is required")
        grant = profile.grant().model_copy(update={"capability_ids": sorted(set(profile.capability_ids) & HOUSEHOLD_CAPABILITIES)})
        identity = dict(work_id="work:household-digital:"+str(uuid5(NAMESPACE_URL,key)), generation=1,
            parent_task_id="household:"+key, source=household_source(turn),
            source_ingress_sha256=canonical_sha256(PostgresConversationJournal._ingress_data(turn)), grant=grant)
    else:
        spec = previous.spec
        task = spec.task
        identity = dict(work_id="work:eva-resume:"+str(uuid5(service.store.scope.installation_id,
            f"{spec.work_id}:{spec.generation+1}")), generation=spec.generation+1,
            parent_task_id=spec.work_id, source=spec.source.model_dump(mode="json"),
            source_ingress_sha256=spec.source_ingress_sha256, execution_id=spec.execution_id,
            episode_id=spec.episode_id, grant=spec.grant)
    spec = profile.build_spec(**identity, origin=origin.to_payload(),
        owner_principal_id=str(service._owner["owner_principal_id"]),
        principal_binding_id=str(service._owner["principal_binding_id"]), task=task,
        created_at=created, deadline=min(created+timedelta(seconds=profile.timeout_seconds),expiry))
    title = previous.title if previous else " ".join(task.split())[:120]
    continuation = (service._request_access(previous, turn)
        if previous is not None and household_continuation_execution_id is None else
        {} if household_continuation_execution_id is None else
        {"household_continuation_execution_id": household_continuation_execution_id})
    try:
        saved = service.store.load(spec.work_id, **service._owner, household_origin=origin, **continuation)
        title = saved.title
    except DelegatedWorkNotFound:
        pass
    admitted = service.store.admit(spec=spec, title=title,
        summary="Huisopdracht via de bestaande Home Assistant-API.", worker_role="Huisopdracht",
        retention_policy_id="retention:household-digital-work:v1",
        retention_until=min(created+timedelta(days=7),expiry), occurred_at=created,
        **continuation, **({} if previous is None else {"resumes_work_id":previous.spec.work_id}))
    return dict(created=admitted.disposition=="inserted", work=service.view(admitted.work),
        prior_outcome_unknown=previous is not None and previous.call_id is not None)
