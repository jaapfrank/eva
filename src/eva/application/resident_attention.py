"""Deterministic resident attention boundaries around Eva's free-will model.

The policy decides eligibility, repetition and quiet-hour boundaries. It never
writes resident-facing prose: eligible events still go through Eva's model so
her expression remains contextual and human.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
import math
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eva.application.sensory import SensoryEvent


@dataclass(frozen=True, slots=True)
class NextAttention:
    """A model-selected future review; never an action or delivery permission."""
    at: datetime
    reason: str
    source_event_id: str | None = None

    def __post_init__(self):
        from eva.contracts.timestamps import require_utc_second
        if not isinstance(self.at, datetime):
            raise ValueError("next attention requires a UTC datetime")
        require_utc_second(self.at, "next attention")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 400:
            raise ValueError("next attention reason is invalid")
        if self.source_event_id is not None and (not isinstance(self.source_event_id, str)
                or not self.source_event_id.strip() or len(self.source_event_id) > 240):
            raise ValueError("next attention source reference is invalid")

    def to_payload(self):
        from eva.contracts.timestamps import format_required_utc_timestamp
        return {"at": format_required_utc_timestamp(self.at), "reason": self.reason,
                **({"source_event_id": self.source_event_id} if self.source_event_id is not None else {})}

    @classmethod
    def from_payload(cls, value):
        from eva.contracts.timestamps import parse_utc_timestamp
        if not isinstance(value, dict) or not {"at", "reason"} <= set(value) <= {"at", "reason", "source_event_id"}:
            raise ValueError("next attention fields are invalid")
        if not isinstance(value["at"], str):
            raise ValueError("next attention time must be a UTC timestamp")
        at = parse_utc_timestamp(value["at"])
        if at.strftime("%Y-%m-%dT%H:%M:%SZ") != value["at"]:
            raise ValueError("next attention time must be canonical UTC")
        return cls(at, value["reason"], value.get("source_event_id"))


AttentionDisposition = Literal["silence", "model"]


@dataclass(frozen=True, slots=True)
class ResidentTemperatureRule:
    rule_id: str
    outside_entity_id: str
    inside_entity_id: str
    colder_by_celsius: float
    reset_margin_celsius: float

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or len(self.rule_id) > 120:
            raise ValueError("resident temperature rule id is invalid")
        for entity_id in (self.outside_entity_id, self.inside_entity_id):
            if not entity_id.startswith("sensor.") or len(entity_id) > 255:
                raise ValueError("resident temperature entity is invalid")
        if not 0.5 <= self.colder_by_celsius <= 30:
            raise ValueError("resident temperature threshold is invalid")
        if not 0.1 <= self.reset_margin_celsius < self.colder_by_celsius:
            raise ValueError("resident temperature reset margin is invalid")


@dataclass(frozen=True, slots=True)
class ResidentAttentionPolicy:
    timezone_name: str
    quiet_start: time
    quiet_end: time
    notification_budget_per_day: int
    urgent_device_classes: tuple[str, ...]
    camera_incident_entities: tuple[str, ...] = ()
    camera_coalesce_seconds: int = 120
    temperature_rule: ResidentTemperatureRule | None = None

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError("resident attention timezone is invalid") from error
        if self.quiet_start.tzinfo is not None or self.quiet_end.tzinfo is not None:
            raise ValueError("resident quiet-hour clock values must be local")
        if not 1 <= self.notification_budget_per_day <= 32:
            raise ValueError("resident notification budget is invalid")
        if not self.urgent_device_classes or any(
            not item.isidentifier() for item in self.urgent_device_classes
        ):
            raise ValueError("resident urgent device classes are invalid")
        if len(set(self.urgent_device_classes)) != len(self.urgent_device_classes):
            raise ValueError("resident urgent device classes contain duplicates")
        if len(self.camera_incident_entities) > 32 or any(
            not item.startswith("binary_sensor.") or len(item) > 255
            for item in self.camera_incident_entities
        ):
            raise ValueError("resident camera incident entities are invalid")
        if len(set(self.camera_incident_entities)) != len(
            self.camera_incident_entities
        ):
            raise ValueError("resident camera incident entities contain duplicates")
        if not 15 <= self.camera_coalesce_seconds <= 900:
            raise ValueError("resident camera coalesce window is invalid")


@dataclass(frozen=True, slots=True)
class ResidentAttentionState:
    temperature_values: tuple[tuple[str, float], ...] = ()
    temperature_latched: bool = False
    camera_cluster_started_at: datetime | None = None

    def __post_init__(self) -> None:
        if len(self.temperature_values) > 2:
            raise ValueError("resident temperature state is too large")
        if any(
            not key.startswith("sensor.")
            or not math.isfinite(value)
            or isinstance(value, bool)
            for key, value in self.temperature_values
        ):
            raise ValueError("resident temperature state is invalid")
        if self.camera_cluster_started_at is not None and (
            self.camera_cluster_started_at.tzinfo is None
        ):
            raise ValueError("resident camera cluster timestamp is naive")


@dataclass(frozen=True, slots=True)
class ResidentAttentionDecision:
    disposition: AttentionDisposition
    reason: str
    urgency_floor: int
    next_state: ResidentAttentionState
    policy_rule: str

    def __post_init__(self) -> None:
        if self.disposition not in {"silence", "model"}:
            raise ValueError("resident attention disposition is invalid")
        if not self.reason.strip() or len(self.reason) > 600:
            raise ValueError("resident attention reason is invalid")
        if not 0 <= self.urgency_floor <= 3:
            raise ValueError("resident attention urgency is invalid")
        if self.disposition == "silence" and self.urgency_floor != 0:
            raise ValueError("silent resident attention cannot require urgency")
        if not self.policy_rule.strip() or len(self.policy_rule) > 120:
            raise ValueError("resident attention rule is invalid")


def evaluate_resident_attention(
    event: SensoryEvent,
    policy: ResidentAttentionPolicy,
    state: ResidentAttentionState,
    *,
    notifications_delivered_today: int,
) -> ResidentAttentionDecision | None:
    """Return a host boundary or ``None`` when Eva may judge the event freely."""

    if notifications_delivered_today < 0:
        raise ValueError("resident delivered-notification count is invalid")
    entity_id = event.payload.get("entity_id")
    new_state = event.payload.get("new_state")
    device_class = event.payload.get("device_class")
    if not isinstance(entity_id, str) or not isinstance(new_state, str):
        return None

    if (
        isinstance(device_class, str)
        and device_class in policy.urgent_device_classes
        and new_state == "on"
    ):
        return ResidentAttentionDecision(
            "model",
            "Een vooraf toegestane urgente melder werd actief.",
            3,
            state,
            "urgent_exception",
        )

    temperature = _temperature_decision(event, policy, state)
    if temperature is not None:
        return _apply_delivery_boundaries(
            temperature,
            event=event,
            policy=policy,
            notifications_delivered_today=notifications_delivered_today,
        )

    if entity_id in policy.camera_incident_entities and new_state == "on":
        if (
            state.camera_cluster_started_at is not None
            and event.occurred_at - state.camera_cluster_started_at
            <= timedelta(seconds=policy.camera_coalesce_seconds)
        ):
            return ResidentAttentionDecision(
                "silence",
                "Deze cameratrigger valt binnen hetzelfde incidentcluster.",
                0,
                state,
                "camera_duplicate_coalesced",
            )
        camera = ResidentAttentionDecision(
            "model",
            "Een nieuwe trigger van een expliciet toegestane camera verdient beoordeling.",
            1,
            replace(state, camera_cluster_started_at=event.occurred_at),
            "camera_distinct_incident",
        )
        return _apply_delivery_boundaries(
            camera,
            event=event,
            policy=policy,
            notifications_delivered_today=notifications_delivered_today,
        )
    if _event_inside_quiet_hours(event, policy):
        return ResidentAttentionDecision(
            "silence",
            "Deze niet-urgente gebeurtenis valt binnen de afgesproken stille uren.",
            0,
            state,
            "quiet_hours",
        )
    if notifications_delivered_today >= policy.notification_budget_per_day:
        return ResidentAttentionDecision(
            "silence",
            "Het vooraf ingestelde dagelijkse aandachtbudget is bereikt.",
            0,
            state,
            "daily_notification_budget",
        )
    return None


def _temperature_decision(
    event: SensoryEvent,
    policy: ResidentAttentionPolicy,
    state: ResidentAttentionState,
) -> ResidentAttentionDecision | None:
    rule = policy.temperature_rule
    entity_id = event.payload.get("entity_id")
    if rule is None or entity_id not in {
        rule.outside_entity_id,
        rule.inside_entity_id,
    }:
        return None
    unit = event.payload.get("unit_of_measurement")
    if unit not in {"°C", "C", "celsius"}:
        return ResidentAttentionDecision(
            "silence",
            "Een temperatuurwaarde zonder toegestane Celsius-eenheid is onbruikbaar.",
            0,
            state,
            "temperature_unit_rejected",
        )
    raw_value = event.payload.get("new_state")
    try:
        value = float(str(raw_value))
    except (TypeError, ValueError):
        return ResidentAttentionDecision(
            "silence",
            "De temperatuurwaarde is niet numeriek en wordt niet geïnterpreteerd.",
            0,
            state,
            "temperature_value_rejected",
        )
    if not math.isfinite(value) or not -80 <= value <= 80:
        return ResidentAttentionDecision(
            "silence",
            "De temperatuurwaarde ligt buiten de toegestane meetgrens.",
            0,
            state,
            "temperature_value_rejected",
        )
    values = dict(state.temperature_values)
    values[str(entity_id)] = value
    next_state = replace(state, temperature_values=tuple(sorted(values.items())))
    if rule.outside_entity_id not in values or rule.inside_entity_id not in values:
        return ResidentAttentionDecision(
            "silence",
            "Beide actuele temperatuurbronnen zijn nodig vóór een beslissing.",
            0,
            next_state,
            "temperature_pair_incomplete",
        )
    delta = values[rule.inside_entity_id] - values[rule.outside_entity_id]
    if state.temperature_latched:
        reset_below = rule.colder_by_celsius - rule.reset_margin_celsius
        if delta <= reset_below:
            return ResidentAttentionDecision(
                "silence",
                "De temperatuurconditie is aantoonbaar gereset.",
                0,
                replace(next_state, temperature_latched=False),
                "temperature_hysteresis_reset",
            )
        return ResidentAttentionDecision(
            "silence",
            "De gemelde temperatuurconditie blijft binnen haar hysteresisvenster.",
            0,
            next_state,
            "temperature_hysteresis_hold",
        )
    if delta >= rule.colder_by_celsius:
        return ResidentAttentionDecision(
            "model",
            "Buiten werd voor het eerst aantoonbaar voldoende kouder dan binnen.",
            1,
            replace(next_state, temperature_latched=True),
            "temperature_threshold_crossed",
        )
    return ResidentAttentionDecision(
        "silence",
        "De bewonersdrempel voor het temperatuurverschil is niet bereikt.",
        0,
        next_state,
        "temperature_below_threshold",
    )


def _apply_delivery_boundaries(
    decision: ResidentAttentionDecision,
    *,
    event: SensoryEvent,
    policy: ResidentAttentionPolicy,
    notifications_delivered_today: int,
) -> ResidentAttentionDecision:
    if decision.urgency_floor >= 3:
        return decision
    if notifications_delivered_today >= policy.notification_budget_per_day:
        return ResidentAttentionDecision(
            "silence",
            "Het vooraf ingestelde dagelijkse aandachtbudget is bereikt.",
            0,
            decision.next_state,
            "daily_notification_budget",
        )
    if _event_inside_quiet_hours(event, policy):
        return ResidentAttentionDecision(
            "silence",
            "Deze niet-urgente gebeurtenis valt binnen de afgesproken stille uren.",
            0,
            decision.next_state,
            "quiet_hours",
        )
    return decision


def _event_inside_quiet_hours(
    event: SensoryEvent, policy: ResidentAttentionPolicy
) -> bool:
    local_time = event.occurred_at.astimezone(ZoneInfo(policy.timezone_name)).time()
    return _inside_quiet_hours(local_time, policy.quiet_start, policy.quiet_end)


def _inside_quiet_hours(value: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= value < end
    return value >= start or value < end


__all__ = [
    "ResidentAttentionDecision",
    "ResidentAttentionPolicy",
    "ResidentAttentionState",
    "ResidentTemperatureRule",
    "evaluate_resident_attention",
]


def validate_attention_reference(value, *, prefix):
    """Validate bounded host provenance without treating an old signal as current."""
    import re
    from eva.contracts.timestamps import parse_utc_timestamp
    keys = {"source_thread_id", "source_event_id", "source_decision_sequence",
            "source_decision_sha256", "source_occurred_at", "origin_thread_id",
            "origin_occurred_at", "requested_at", "effective_at", "reason"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("attention provenance fields differ")
    if (any(not isinstance(value[k], str) or not value[k].startswith(prefix)
            or len(value[k]) > 256 for k in ("source_thread_id", "origin_thread_id"))
            or value["source_event_id"] != value["source_thread_id"].removeprefix("sensory:")
            or type(value["source_decision_sequence"]) is not int
            or value["source_decision_sequence"] < 2
            or re.fullmatch(r"[0-9a-f]{64}", str(value["source_decision_sha256"])) is None):
        raise ValueError("attention provenance identity differs")
    choice = NextAttention.from_payload({"at": value["requested_at"], "reason": value["reason"]})
    times = {k: parse_utc_timestamp(value[k]) for k in
             ("source_occurred_at", "origin_occurred_at", "effective_at")}
    if times["effective_at"] < choice.at or times["effective_at"] <= times["source_occurred_at"]:
        raise ValueError("attention provenance time differs")
    return value
