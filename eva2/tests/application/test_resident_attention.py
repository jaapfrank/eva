from __future__ import annotations

from datetime import UTC, datetime, time

from eva.application.resident_attention import (
    ResidentAttentionPolicy,
    ResidentAttentionState,
    ResidentTemperatureRule,
    evaluate_resident_attention,
)
from eva.application.sensory import SensoryEvent


POLICY = ResidentAttentionPolicy(
    timezone_name="Europe/Amsterdam",
    quiet_start=time(23, 0),
    quiet_end=time(7, 0),
    notification_budget_per_day=3,
    urgent_device_classes=("gas", "moisture", "smoke"),
    camera_incident_entities=(
        "binary_sensor.camera_tuin_beweging",
        "binary_sensor.camera_oprit_beweging",
        "binary_sensor.camera_deur_beweging",
    ),
    camera_coalesce_seconds=120,
    temperature_rule=ResidentTemperatureRule(
        "ventilation-opportunity",
        "sensor.buitentemperatuur",
        "sensor.binnentemperatuur",
        3.0,
        1.0,
    ),
)


def _event(
    entity_id: str,
    new_state: str,
    occurred_at: datetime,
    *,
    device_class: str | None = None,
    unit: str | None = None,
) -> SensoryEvent:
    return SensoryEvent(
        "home_assistant",
        f"home-assistant:{entity_id}:{occurred_at.timestamp()}",
        "state_changed",
        occurred_at,
        {
            "device_class": device_class,
            "entity_id": entity_id,
            "new_state": new_state,
            "old_state": "off",
            "unit_of_measurement": unit,
        },
    )


def test_temperature_notifies_once_holds_and_rearms_after_reset() -> None:
    state = ResidentAttentionState()
    inside = _event(
        "sensor.binnentemperatuur",
        "21.0",
        datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        unit="°C",
    )
    first = evaluate_resident_attention(
        inside, POLICY, state, notifications_delivered_today=0
    )
    assert first is not None and first.policy_rule == "temperature_pair_incomplete"
    crossed = evaluate_resident_attention(
        _event(
            "sensor.buitentemperatuur",
            "17.5",
            datetime(2026, 8, 24, 10, 1, tzinfo=UTC),
            unit="°C",
        ),
        POLICY,
        first.next_state,
        notifications_delivered_today=0,
    )
    assert crossed is not None and crossed.disposition == "model"
    assert crossed.policy_rule == "temperature_threshold_crossed"
    held = evaluate_resident_attention(
        _event(
            "sensor.buitentemperatuur",
            "18.0",
            datetime(2026, 8, 24, 10, 2, tzinfo=UTC),
            unit="°C",
        ),
        POLICY,
        crossed.next_state,
        notifications_delivered_today=1,
    )
    assert held is not None and held.policy_rule == "temperature_hysteresis_hold"
    reset = evaluate_resident_attention(
        _event(
            "sensor.buitentemperatuur",
            "19.5",
            datetime(2026, 8, 24, 10, 3, tzinfo=UTC),
            unit="°C",
        ),
        POLICY,
        held.next_state,
        notifications_delivered_today=1,
    )
    assert reset is not None and reset.policy_rule == "temperature_hysteresis_reset"
    assert reset.next_state.temperature_latched is False


def test_camera_sources_coalesce_across_three_authorized_views() -> None:
    first_time = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    first = evaluate_resident_attention(
        _event("binary_sensor.camera_tuin_beweging", "on", first_time),
        POLICY,
        ResidentAttentionState(),
        notifications_delivered_today=0,
    )
    assert first is not None and first.policy_rule == "camera_distinct_incident"
    duplicate = evaluate_resident_attention(
        _event(
            "binary_sensor.camera_oprit_beweging",
            "on",
            datetime(2026, 8, 24, 10, 1, tzinfo=UTC),
        ),
        POLICY,
        first.next_state,
        notifications_delivered_today=0,
    )
    assert duplicate is not None
    assert duplicate.policy_rule == "camera_duplicate_coalesced"
    distinct = evaluate_resident_attention(
        _event(
            "binary_sensor.camera_deur_beweging",
            "on",
            datetime(2026, 8, 24, 10, 3, tzinfo=UTC),
        ),
        POLICY,
        duplicate.next_state,
        notifications_delivered_today=1,
    )
    assert distinct is not None and distinct.policy_rule == "camera_distinct_incident"


def test_quiet_hours_suppress_nonurgent_but_not_predefined_urgent_event() -> None:
    quiet = datetime(2026, 8, 24, 22, 30, tzinfo=UTC)  # 00:30 CEST
    camera = evaluate_resident_attention(
        _event("binary_sensor.camera_tuin_beweging", "on", quiet),
        POLICY,
        ResidentAttentionState(),
        notifications_delivered_today=0,
    )
    assert camera is not None and camera.policy_rule == "quiet_hours"
    smoke = evaluate_resident_attention(
        _event(
            "binary_sensor.rook_keuken",
            "on",
            quiet,
            device_class="smoke",
        ),
        POLICY,
        ResidentAttentionState(),
        notifications_delivered_today=99,
    )
    assert smoke is not None and smoke.disposition == "model"
    assert smoke.urgency_floor == 3
    assert smoke.policy_rule == "urgent_exception"


def test_daily_budget_silences_nonurgent_candidate() -> None:
    decision = evaluate_resident_attention(
        _event(
            "binary_sensor.camera_tuin_beweging",
            "on",
            datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        ),
        POLICY,
        ResidentAttentionState(),
        notifications_delivered_today=3,
    )
    assert decision is not None and decision.disposition == "silence"
    assert decision.policy_rule == "daily_notification_budget"
