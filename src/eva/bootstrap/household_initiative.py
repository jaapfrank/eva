"""Route admitted household observations to Eva's shared decision and output.

The existing configured household output is available without a recent chat.
Private memory and native owner execution keep their existing audience boundary.
"""
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.free_will import SensoryDecisionWorker
from eva.application.household_initiative import HouseholdInitiativeBinding, HouseholdInitiativeContext
from eva.application.resident_attention import _inside_quiet_hours
from eva.bootstrap.household_initiative_delivery import HouseholdInitiativeDelivery
from eva.contracts.canonical_json import canonical_sha256


class HouseholdInitiativeLoop:
    def __init__(self, *, engine, voice, server, provider, model, memory, identity,
                 budget, principal_id, authorize_initiative, source_store=None, can_decide=None):
        self.engine, self.voice, self.server, self.provider = engine, voice, server, provider
        self.model, self.memory, self.identity, self.budget = model, memory, identity, budget
        self.principal_id, self.authorize_initiative = principal_id, authorize_initiative
        self.source_store = source_store
        self.can_decide = can_decide or (lambda: True)
        self._components = {}
        self._timezone = ZoneInfo("Europe/Amsterdam")

    def _selected(self):
        # A recent session can supply the preferred output, but is not permission
        # to speak and is not a prerequisite for household initiative.
        recent = self.server.sessions.latest_native()
        devices = self.voice.policy.devices
        if recent is not None and devices.get(recent.get("satellite_id")) == recent.get("device_id"):
            return recent
        return next(({"satellite_id": satellite, "device_id": device}
                     for satellite, device in devices.items()), None)

    def _authorize(self):
        self.voice.validate_current()
        self.server.authority.validate_current()
        return self.authorize_initiative()

    def _admit_review(self, store, claim, *, now):
        self._authorize()
        if not store.context_review_claim_is_current(claim, now=now):
            raise ValueError("household initiative dispatch claim is not current")
        policy = self.voice.policy
        available = getattr(self.model, "existing_subscription_autonomy", None)
        if callable(available) and available(tenant_id=policy.tenant_id,
                                            installation_id=policy.installation_id):
            from eva.adapters.postgres.runtime_resource_usage import BackgroundReflectionAdmission
            used, total = self.budget.read_usage_counts(principal_id=self.principal_id, now=now)
            return BackgroundReflectionAdmission(True, self.budget._day(now), used, total,
                                                 "existing_subscription_household_review")
        return self.budget.admit_background_reflection(tenant_id=policy.tenant_id,
            installation_id=policy.installation_id, principal_id=self.principal_id,
            now=now, reservation_id=claim.thread_id, spread_over_day=True)

    def _components_for(self, destination):
        satellite, device = destination["satellite_id"], destination["device_id"]
        policy = self.voice.policy
        if policy.devices.get(satellite) != device:
            raise ValueError("household initiative destination differs from current policy")
        policy_sha = canonical_sha256(policy.model_dump(mode="json"))
        key = (satellite, device, policy_sha)
        if key in self._components:
            return self._components[key]
        binding = HouseholdInitiativeBinding(tenant_id=policy.tenant_id,
            installation_id=policy.installation_id, household_id=policy.household_id,
            actor_id="household-device:" + device, satellite_id=satellite, device_id=device,
            consent_operation_id=policy.consent_operation_id, policy_sha256=policy_sha)
        store = PostgresSensoryEventStore(self.engine, tenant_id=policy.tenant_id,
            installation_id=policy.installation_id, initiative_principal_id=self.principal_id,
            initiative_authorizer=self._authorize, context_reviews_only=True,
            household_binding=binding, decision_lease_seconds=300)
        if (self.source_store is not None
                and self.source_store._timeline._scope_value != store._timeline._scope_value):
            raise ValueError("household observations differ from the admitted installation")

        def window(now):
            local = now.astimezone(self._timezone)
            if _inside_quiet_hours(local.time(), time(22), time(8)):
                return False
            self._authorize()
            return (self.voice.policy.devices.get(satellite) == device
                    and canonical_sha256(self.voice.policy.model_dump(mode="json")) == policy_sha
                    and self.can_decide())

        worker = SensoryDecisionWorker(store, self.model, self.memory, self.identity,
            tenant_id=policy.tenant_id, installation_id=policy.installation_id,
            principal_id=self.principal_id, admission_blocker=self.model.autonomous_admission_blocker,
            context_review_budget=self.budget, context_review_authorizer=self._authorize,
            context_review_admission=lambda claim, now: self._admit_review(store, claim, now=now),
            household_context=HouseholdInitiativeContext(binding, self._authorize),
            dispatch_ready=self.can_decide,
            clock=lambda: datetime.now(UTC).replace(microsecond=0))
        delivery = HouseholdInitiativeDelivery(store, binding, self._authorize, self.provider,
            delivery_window=window)
        result = (store, worker, delivery, window)
        self._components[key] = result
        return result

    def run_once(self):
        try:
            self._authorize()
            destination = self._selected()
            if destination is None:
                return "idle"
            store, worker, delivery, window = self._components_for(destination)
            now = datetime.now(UTC).replace(microsecond=0)
            if not window(now):
                return "idle"
            delivered = delivery.run_once()
            if delivered != "idle":
                return delivered
            if (not self.can_decide()
                    or self.model.autonomous_admission_blocker() is not None):
                return "idle"
            # All external observations already live in the original sensory
            # timeline. Project one fresh shared source; never scan/replay history.
            original = (None if self.source_store is None else
                        store.next_household_observation(now=now))
            if original is not None:
                store.schedule_household_observation(original, now=now)
            else:
                store.schedule_context_review(now=now)
            return worker.run_once(now=now)
        except Exception:
            # Availability output carries no private content or credentials.
            return "unavailable"
