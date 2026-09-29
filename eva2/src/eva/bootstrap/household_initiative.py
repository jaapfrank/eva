"""Compose shared household initiative with existing memory, budgets and HA output.

Recent native conversation supplies a destination, not personal recognition or
proof that someone remains present. Existing session stop/expiry silences it.
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
                 budget, principal_id, authorize_initiative):
        self.engine, self.voice, self.server, self.provider = engine, voice, server, provider
        self.model, self.memory, self.identity, self.budget = model, memory, identity, budget
        self.principal_id, self.authorize_initiative = principal_id, authorize_initiative
        self._components = {}
        self._timezone = ZoneInfo("Europe/Amsterdam")

    def _selected(self):
        # One recent live session, never a broadcast to every configured speaker.
        return self.server.sessions.latest_native()

    def _authorize(self):
        self.voice.validate_current()
        self.server.authority.validate_current()
        self.authorize_initiative()

    def _components_for(self, destination):
        satellite, device = destination["satellite_id"], destination["device_id"]
        if self.voice.policy.devices.get(satellite) != device:
            raise ValueError("household initiative destination differs from current policy")
        key = (satellite, device)
        if key in self._components:
            return self._components[key]
        policy = self.voice.policy
        binding = HouseholdInitiativeBinding(tenant_id=policy.tenant_id,
            installation_id=policy.installation_id, household_id=policy.household_id,
            actor_id="household-device:" + device, satellite_id=satellite, device_id=device,
            consent_operation_id=policy.consent_operation_id,
            policy_sha256=canonical_sha256(policy.model_dump(mode="json")))
        store = PostgresSensoryEventStore(self.engine, tenant_id=policy.tenant_id,
            installation_id=policy.installation_id, initiative_principal_id=self.principal_id,
            initiative_authorizer=self._authorize, context_reviews_only=True,
            household_binding=binding, decision_lease_seconds=300)

        def window(now):
            local = now.astimezone(self._timezone)
            if _inside_quiet_hours(local.time(), time(22), time(8)):
                return False
            current = self._selected()
            if current is None or (current["satellite_id"], current["device_id"]) != key:
                return False
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
            return store.household_notifications_since(midnight) < 2

        worker = SensoryDecisionWorker(store, self.model, self.memory, self.identity,
            tenant_id=policy.tenant_id, installation_id=policy.installation_id,
            principal_id=self.principal_id, admission_blocker=self.model.autonomous_admission_blocker,
            context_review_budget=self.budget, context_review_authorizer=self._authorize,
            household_context=HouseholdInitiativeContext(binding, self._authorize),
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
            if self.model.autonomous_admission_blocker() is not None:
                return "idle"
            store.schedule_context_review(now=now)
            return worker.run_once(now=now)
        except Exception:
            # No private content or credentials in routine availability output.
            return "unavailable"
