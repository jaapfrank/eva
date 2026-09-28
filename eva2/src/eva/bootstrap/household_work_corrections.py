"""Shared digital corrections on the existing durable scheduling lifecycle."""
from dataclasses import asdict
from uuid import uuid5

from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec, DelegatedHouseholdDigitalWorkSpec
from eva.contracts.delegated_work import HouseholdWorkOrigin


def revise_digital_work(store, previous, *, instruction, request_key, clock,
                        load_work, stop_work, schedule_resume):
    """Preserve the same stop, correction and uncertain-acknowledgement semantics."""
    if not isinstance(previous.spec, DelegatedDigitalWorkSpec):
        return {"status": "not_digital", "guidance": "Deze bijsturing geldt voor digitale opdrachten. Stop ander werk gericht en neem de gewijzigde opdracht opnieuw aan via de passende route."}
    from eva.adapters.postgres.work_corrections import correction_boundary
    with correction_boundary(store._engine, previous.spec):
        return _revise_digital_work(store, previous, instruction=instruction,
            request_key=request_key, clock=clock, load_work=load_work,
            stop_work=stop_work, schedule_resume=schedule_resume)


def _revise_digital_work(store, previous, *, instruction, request_key, clock,
                         load_work, stop_work, schedule_resume):
    successor_id = "work:eva-resume:" + str(uuid5(store.scope.installation_id,
        f"{previous.spec.work_id}:{previous.spec.generation + 1}"))
    try:
        successor = load_work(successor_id)
    except DelegatedWorkNotFound:
        pass
    else:
        return {"status": "newer_attempt_exists", "work_id": successor.spec.work_id,
                "guidance": "Gebruik de nieuwere poging voor deze correctie; er is hier niets gewijzigd."}
    previous = stop_work(previous.spec.work_id, clock())
    if previous.state in {"succeeded", "verified"}:
        return {"status": "already_finished", "work_id": previous.spec.work_id,
                "guidance": "Deze opdracht is al afgerond. Bespreek het bewaarde resultaat en neem zo nodig een nieuwe vervolgopdracht aan."}
    from eva.adapters.postgres.work_corrections import record_work_correction
    try:
        recorded = record_work_correction(store._engine, previous.spec,
            instruction=instruction, request_key=request_key, now=clock())
    except Exception:
        return {"status": "stop_recorded_correction_unconfirmed", "work_id": previous.spec.work_id,
                "in_flight_effects_may_complete": True,
                "guidance": "De stop is vastgelegd. Het bewaren van je correctie is niet bevestigd. Controleer de taak en planning voordat je opnieuw probeert; ik herhaal dit verzoek niet automatisch."}
    try:
        queued = schedule_resume(previous, recorded)
    except Exception:
        return {"status": "correction_saved_resume_unconfirmed", "work_id": previous.spec.work_id,
                "in_flight_effects_may_complete": True,
                "guidance": "De stop en je correctie zijn vastgelegd. Het inplannen van de hervatting is niet bevestigd. Controleer de taak en planning voordat je opnieuw probeert; ik herhaal dit verzoek niet automatisch."}
    return {"status": "correction_saved", "work_id": previous.spec.work_id,
            "schedule": queued, "in_flight_effects_may_complete": True,
            "guidance": "Correctie bewaard; de huidige poging stopt en één begrensde hervatting wacht daarop. Eerdere acties en resultaten blijven behouden."}


class _HouseholdScheduleAudience:
    """Persist household authority without retaining a foreground session lease."""

    def __init__(self, server):
        self.server, self.store = server, server.store
        self.digital_profile = getattr(server.authority, "digital_profile", None)
        self.owner_principal_id = server.authority.policy.owner_principal_id
        self.principal_binding_id = server.authority.policy.principal_binding_id
        self.prefix = "scheduled-household-work:" + str(self.owner_principal_id) + ":"

    @property
    def _owner(self):
        return dict(owner_principal_id=self.owner_principal_id,
                    principal_binding_id=self.principal_binding_id)

    def now(self):
        return self.server.authority.clock()

    def authorize(self):
        self.server.authority.validate_current()
        if self.digital_profile is None:
            raise ValueError("household digital scheduling is unavailable")
        self.store.validate_admission_authority(**self._owner)

    def validate_authority(self, turn):
        from eva.bootstrap.household_digital_work import HouseholdDigitalSourceLoader, household_source
        self.authorize()
        if turn.audience.kind != "household":
            raise ValueError("household scheduling requires its original audience")
        restored = HouseholdDigitalSourceLoader(self.store._engine,
            self.server.conversation_scope, self.server.authority)(household_source(turn))
        if restored != turn:
            raise ValueError("household scheduling source differs from admitted ingress")

    def request_context(self, turn, request, resume):
        if not resume or set(request) != {"work_id", "run_at"}:
            raise ValueError("household scheduling only resumes an explicitly corrected task")
        from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
        service = ScopedDelegatedWorkActions(None, self.server)._select(turn)._service
        previous = service._load(request["work_id"], service.origin(turn))
        if not isinstance(previous.spec, DelegatedHouseholdDigitalWorkSpec):
            raise ValueError("household correction requires household digital work")
        return {"origin": previous.spec.origin.to_payload(),
                "request_origin": service.origin(turn).to_payload(),
                "execution_id": previous.spec.execution_id}

    def restore_turn(self, data):
        from eva.adapters.postgres.household_work_sessions import verify_origin
        from eva.bootstrap.household_digital_work import HouseholdDigitalSourceLoader
        self.authorize()
        context = data.get("audience_context")
        if not isinstance(context, dict) or set(context) != {"origin", "request_origin", "execution_id"}:
            raise ValueError("household schedule context is absent or invalid")
        origin = HouseholdWorkOrigin.model_validate(context["origin"])
        request_origin = HouseholdWorkOrigin.model_validate(context["request_origin"])
        if any(getattr(origin, name) != getattr(request_origin, name) for name in (
                "actor_id", "device_id", "satellite_id", "tenant_id", "installation_id",
                "household_id", "consent_operation_id", "household_policy_sha256",
                "research_policy_sha256")):
            raise ValueError("household correction crossed its original device or audience")
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            verify_origin(connection, self.store.scope, request_origin, require_open=False, now=self.now())
        turn = HouseholdDigitalSourceLoader(self.store._engine,
            self.server.conversation_scope, self.server.authority)({
                "tenant_id": request_origin.tenant_id, "installation_id": request_origin.installation_id,
                "principal_id": request_origin.actor_id, "conversation_id": request_origin.conversation_id,
                "message_id": request_origin.message_id,
                "audience": {"kind": "household", "principal_id": request_origin.actor_id,
                    "household_id": request_origin.household_id,
                    "consent_operation_id": request_origin.consent_operation_id}})
        if asdict(turn) != data.get("source"):
            raise ValueError("household schedule source differs from its retained turn")
        return turn

    def load(self, work_id, data):
        self.restore_turn(data)
        context = data["audience_context"]
        origin = HouseholdWorkOrigin.model_validate(context["origin"])
        work = self.store.load(work_id, **self._owner, household_origin=origin,
            household_continuation_execution_id=context["execution_id"])
        if (not isinstance(work.spec, DelegatedHouseholdDigitalWorkSpec)
                or work.spec.execution_id != context["execution_id"]
                or work.spec.origin != origin or self.now() >= work.retention_until):
            raise DelegatedWorkNotFound("household scheduled work differs from its original execution")
        self.server.authority.validate_actor(origin.actor_id)
        return work

    def request_stop(self, work_id, data, now):
        work = self.load(work_id, data)
        return self.store.request_stop(work_id, **self._owner,
            household_origin=work.spec.origin, now=now,
            household_continuation_execution_id=data["audience_context"]["execution_id"])

    def return_binding(self, turn):
        self.validate_authority(turn)
        return None

    def dispatch(self, data, turn, task, request_key, admission_time):
        if data.get("operation") != "resume" or task is not None:
            raise ValueError("household schedules cannot admit unrelated work")
        self.validate_authority(turn)
        previous = self.load(data["work_id"], data)
        from eva.bootstrap.household_digital_work import admit_household_digital
        return admit_household_digital(self, turn, request_key, None,
            previous=previous, admission_time=admission_time,
            household_continuation_execution_id=previous.spec.execution_id)

    @staticmethod
    def view(work):
        # Only the generic scheduler consumes this admission response.
        return {"work_id": work.spec.work_id}


def HouseholdScheduledWork(server):
    """Use the existing scheduler transitions with a household audience adapter."""
    audience = _HouseholdScheduleAudience(server)
    return ScheduledOwnerWork(audience, audience_adapter=audience)


def cancel_pending_household_resumes(server, work):
    """An explicit later stop also cancels this task's waiting correction."""
    scheduler = HouseholdScheduledWork(server)
    related = scheduler.related((work.spec.work_id,))
    for item in related["schedules"]:
        if (item["resume_work_id"] == work.spec.work_id
                and item["state"] in {"scheduled", "launching"}):
            scheduler.cancel_chain(item["schedule_id"])


def revise_household_work(service, turn, request_key, revision):
    previous = service._load(revision["work_id"], service.origin(turn))
    if not isinstance(previous.spec, DelegatedHouseholdDigitalWorkSpec):
        return {"status": "not_digital",
                "guidance": "Deze bijsturing geldt voor digitale huisopdrachten."}
    scheduler = HouseholdScheduledWork(service.server)
    return revise_digital_work(service.store, previous,
        instruction=revision["instruction"], request_key=request_key,
        clock=service.server.authority.clock,
        load_work=lambda work_id: service._load(work_id, service.origin(turn)),
        stop_work=lambda work_id, now: service.store.request_stop(work_id, **service._owner,
            household_origin=previous.spec.origin, now=now,
            **service._request_access(previous, turn)),
        schedule_resume=lambda work, recorded: scheduler.schedule(turn, request_key=request_key,
            request={"work_id": work.spec.work_id, "run_at": recorded.isoformat().replace("+00:00", "Z")},
            resume=True, wait_for_stop=True))
