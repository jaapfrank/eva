"""Explicit household-only context for initiative decisions.

Bindings identify an authorized device/audience, never a recognized person.
Current consent is checked by the host callback and the existing memory port.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Callable, Protocol

from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_SHARED_QUERY = (
    "Gebruik uitsluitend informatie die expliciet met dit huishouden is gedeeld "
    "voor een passende, nuttige bijdrage aan het gezamenlijke dagelijks leven. "
    "Privégesprekken, persoonlijke herinneringen en privétaken horen hier niet bij. "
    "Noem of suggereer ook niet dat zulke privé-informatie of privétaken bestaan."
)


@dataclass(frozen=True, slots=True)
class HouseholdInitiativeBinding:
    tenant_id: str
    installation_id: str
    household_id: str
    actor_id: str
    satellite_id: str
    device_id: str
    consent_operation_id: str
    policy_sha256: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.installation_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError("household initiative scope is invalid")
        if (not isinstance(self.device_id, str) or not re.fullmatch(r"[0-9a-f]{32}", self.device_id)
                or self.actor_id != "household-device:" + self.device_id
                or not isinstance(self.satellite_id, str)
                or not re.fullmatch(r"assist_satellite\.[a-z0-9_]{1,183}", self.satellite_id)
                or not isinstance(self.policy_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.policy_sha256)):
            raise ValueError("household initiative device or policy binding is invalid")
        self._consent_reference()

    def _consent_reference(self) -> HouseholdConsentReference:
        return HouseholdConsentReference(principal_id=self.actor_id,
            household_id=self.household_id, operation_id=self.consent_operation_id)

    def to_payload(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id, "installation_id": self.installation_id,
                "household_id": self.household_id, "actor_id": self.actor_id,
                "satellite_id": self.satellite_id, "device_id": self.device_id,
                "consent_operation_id": self.consent_operation_id,
                "policy_sha256": self.policy_sha256}

    def event(self, now: datetime) -> SensoryEvent:
        if (not isinstance(now, datetime) or now.tzinfo is None
                or now.utcoffset() != timedelta(0) or now.microsecond or now.timestamp() < 0):
            raise ValueError("household initiative event requires whole-second UTC")
        window = int(now.timestamp()) // 3600
        encoded = json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))
        identity = "household-initiative:" + hashlib.sha256(encoded.encode()).hexdigest() + ":" + str(window)
        return SensoryEvent(source="household-initiative", source_event_id=identity,
            event_type="household_opportunity_review", occurred_at=now,
            payload={"household_binding": self.to_payload(), "review_window": window})

    def attention_event(self, now: datetime, reference: dict) -> SensoryEvent:
        """A fresh review occasion carrying only its original shared provenance."""
        from eva.application.resident_attention import validate_attention_reference
        from eva.contracts.timestamps import require_utc_second, parse_utc_timestamp
        require_utc_second(now, "attention review")
        prefix = "sensory:" + self.event(now).source_event_id.rsplit(":", 1)[0] + ":"
        validate_attention_reference(reference, prefix=prefix)
        if parse_utc_timestamp(reference["effective_at"]) > now:
            raise ValueError("household attention is not due")
        suffix = str(int(canonical_sha256({"model_attention": reference}), 16))
        return SensoryEvent("household-initiative", prefix.removeprefix("sensory:") + suffix,
            "household_attention_review", now,
            {"household_binding": self.to_payload(), "attention_origin": reference})

    def observation_event(self, original: SensoryEvent) -> SensoryEvent:
        """Bind one normalized public household signal to this audience and output."""
        if (not isinstance(original, SensoryEvent)
                or original.source in {"initiative-review", "household-initiative", "telegram", "conversation"}
                or original.occurred_at.tzinfo is None or original.occurred_at.utcoffset() != timedelta(0)
                or original.occurred_at.microsecond):
            raise ValueError("household observation source differs")
        allowed = {"entity_id", "device_class", "friendly_name", "old_state", "new_state",
                   "unit_of_measurement", "time_fired", "current_state", "checked_at"}
        # Only normalized sensor metadata enters the shared audience. Raw bodies,
        # private memories, attributes, images and credentials are never copied.
        if original.event_type == "household_observation_batch":
            members = original.payload.get("observations")
            if (not isinstance(members, list) or not 1 <= len(members) <= 16
                    or len(canonical_json_bytes(members)) > 6144):
                raise ValueError("household observation batch is invalid")
            normalized = []
            for member in members:
                if not isinstance(member, dict) or set(member) != {"source", "source_event_id", "event_type", "occurred_at", "payload"}:
                    raise ValueError("household observation batch member is invalid")
                if member["event_type"] == "household_observation_batch":
                    raise ValueError("nested household observation batches are invalid")
                source = SensoryEvent(member["source"], member["source_event_id"], member["event_type"],
                    datetime.fromisoformat(member["occurred_at"]), member["payload"])
                public = self.observation_event(source).payload["observation"]
                if public != member:
                    raise ValueError("household observation batch contains unnormalized data")
                normalized.append(public)
            payload = {"observations": normalized, "selection_limit": 16,
                       "historical_observations_not_live_proof": True}
        else:
            payload = {key: value for key, value in original.payload.items() if key in allowed}
        if original.event_type != "household_observation_batch" and (not isinstance(payload.get("entity_id"), str) or not payload["entity_id"]
                or any(value is not None and not isinstance(value, (str, bool, int, float))
                       for value in payload.values())):
            raise ValueError("household observation metadata is invalid")
        if original.event_type != "household_observation_batch" and "camera_observation" in original.payload:
            from eva.contracts.camera_observation import normalize_camera_observation
            payload["camera_observation"] = normalize_camera_observation(original.payload["camera_observation"])
        observation = {"source": original.source, "source_event_id": original.source_event_id,
            "event_type": original.event_type, "occurred_at": original.occurred_at.isoformat(),
            "payload": payload}
        binding = self.to_payload()
        if len(canonical_json_bytes(observation)) > 8192:
            raise ValueError("household observation metadata exceeds its bound")
        prefix = self.event(original.occurred_at).source_event_id.rsplit(":", 1)[0]
        identity = prefix + ":observation:" + canonical_sha256(observation)
        return SensoryEvent("household-initiative", identity, "household_observation_review",
            original.occurred_at, {"household_binding": binding, "observation": observation})

    def matches_event(self, event: SensoryEvent) -> bool:
        """Validate either the original clock source or an immutable shared signal."""
        if not isinstance(event, SensoryEvent):
            return False
        try:
            if event.event_type == "household_attention_review":
                return (set(event.payload) == {"household_binding", "attention_origin"}
                    and event.payload["household_binding"] == self.to_payload()
                    and event == self.attention_event(event.occurred_at, event.payload["attention_origin"]))
            if event.event_type == "household_opportunity_review":
                return event == self.event(event.occurred_at)
            if (event.event_type != "household_observation_review"
                    or set(event.payload) != {"household_binding", "observation"}
                    or event.payload["household_binding"] != self.to_payload()):
                return False
            observation = event.payload["observation"]
            if (not isinstance(observation, dict) or set(observation) != {
                    "source", "source_event_id", "event_type", "occurred_at", "payload"}):
                return False
            original = SensoryEvent(observation["source"], observation["source_event_id"],
                observation["event_type"], datetime.fromisoformat(observation["occurred_at"]),
                observation["payload"])
            return event == self.observation_event(original)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def recall_request(self, *, recall_id: str, now: datetime) -> RecallCurrentFacts:
        if (not isinstance(now, datetime) or now.tzinfo is None
                or now.utcoffset() != timedelta(0) or now.microsecond
                or not isinstance(recall_id, str) or not recall_id.strip() or len(recall_id) > 256):
            raise ValueError("household initiative recall requires an identity and whole-second UTC")
        return RecallCurrentFacts(
            recall_id=recall_id, tenant_id=self.tenant_id, installation_id=self.installation_id,
            principal_id=self.actor_id,
            audience=MemoryAudience.household(principal_id=self.actor_id,
                household_id=self.household_id, consent_ref=self._consent_reference()),
            conversation_id=(f"household:{self.household_id}:conversation:initiative:"
                             f"{self.policy_sha256}:{self.device_id}"),
            project_id=None, query_purpose="household_initiative_review",
            requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            scope={"kind": "household", "household_id": self.household_id},
            subject_ids=(self.actor_id,), query_text=_SHARED_QUERY, maximum_facts=8,
        )


class HouseholdInitiativeMemoryPort(Protocol):
    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory: ...


class HouseholdInitiativeContext:
    def __init__(self, binding: HouseholdInitiativeBinding, authorize: Callable[[], None]):
        if not isinstance(binding, HouseholdInitiativeBinding) or not callable(authorize):
            raise ValueError("household initiative context requires a binding and current authority")
        self.binding = binding
        self._authorize = authorize

    def __call__(self, event: SensoryEvent, now: datetime,
                 memory: HouseholdInitiativeMemoryPort) -> tuple[PresenceMemory, dict[str, str]]:
        if not self.binding.matches_event(event):
            raise ValueError("household initiative event binding differs")
        recall_id = "household-initiative:" + hashlib.sha256(event.source_event_id.encode()).hexdigest()
        query = self.binding.recall_request(recall_id=recall_id, now=now)
        self._authorize()
        result = memory.recall_for_presence(query)
        self._authorize()
        if not isinstance(result, PresenceMemory):
            raise ValueError("household initiative memory response is invalid")
        recall = result.recall
        # Projection version identifies the actual memory adapter, not an echoed
        # request identity. Hindsight legitimately replaces the generic default.
        fields = ("recall_id", "tenant_id", "principal_id", "conversation_id", "project_id",
                  "query_purpose", "requested_valid_at")
        if (any(getattr(recall, name) != getattr(query, name) for name in fields)
                or recall.scope_sha256 != canonical_sha256(query.scope)
                or len(result.confirmed_facts) > query.maximum_facts
                or len(recall.selected) > query.maximum_facts
                or {fact.fact_id for fact in result.confirmed_facts}
                   != {fact.fact_id for fact in recall.selected}
                or result.style_mirror is not None):
            raise ValueError("household initiative memory response scope differs")
        return result, self.binding.to_payload()
