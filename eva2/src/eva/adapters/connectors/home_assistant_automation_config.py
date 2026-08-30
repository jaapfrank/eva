"""Provider-owned construction of bounded Home Assistant automations."""

from __future__ import annotations

import re

from eva.contracts.base import JsonValue


_AUTOMATION_ID = re.compile(r"^eva2_[a-z0-9_]{1,58}$")
_ENTITY = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?$")
_SAFE_ACTION_DOMAINS = frozenset(
    {"automation", "fan", "input_boolean", "light", "scene", "switch"}
)


def state_action_automation(
    *,
    automation_id: str,
    alias: str,
    trigger_entity_id: str,
    trigger_to: str,
    action_entity_id: str,
    action_service: str,
) -> dict[str, JsonValue]:
    if _AUTOMATION_ID.fullmatch(automation_id) is None:
        raise ValueError("Eva automation ID is invalid")
    if not alias.strip() or len(alias) > 160:
        raise ValueError("Eva automation alias is invalid")
    if _ENTITY.fullmatch(trigger_entity_id) is None:
        raise ValueError("Eva automation trigger entity is invalid")
    if not trigger_to.strip() or len(trigger_to) > 80:
        raise ValueError("Eva automation target state is invalid")
    if _ENTITY.fullmatch(action_entity_id) is None:
        raise ValueError("Eva automation action entity is invalid")
    if action_entity_id.split(".", 1)[0] not in _SAFE_ACTION_DOMAINS:
        raise ValueError("Eva automation action domain is not allowed")
    if action_service not in {"turn_on", "turn_off", "toggle"}:
        raise ValueError("Eva automation service is invalid")
    return {
        "actions": [
            {
                "action": f"homeassistant.{action_service}",
                "target": {"entity_id": action_entity_id},
            }
        ],
        "alias": alias.strip(),
        "conditions": [],
        "description": "Beheerd door Eva2; wijziging vereist bewijs en een herstelpad.",
        "mode": "single",
        "triggers": [
            {
                "entity_id": trigger_entity_id,
                "to": trigger_to.strip(),
                "trigger": "state",
            }
        ],
    }


def advanced_state_automation(
    *,
    automation_id: str,
    alias: str,
    trigger_entity_id: str,
    trigger_from: str | None,
    trigger_to: str,
    condition_entity_id: str | None,
    condition_state: str | None,
    actions: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    """Backward-compatible state-trigger wrapper around the bounded builder."""

    return advanced_automation(
        automation_id=automation_id,
        alias=alias,
        trigger_kind="state",
        trigger_entity_id=trigger_entity_id,
        trigger_from=trigger_from,
        trigger_to=trigger_to,
        trigger_at=None,
        trigger_above=None,
        trigger_below=None,
        condition_entity_id=condition_entity_id,
        condition_state=condition_state,
        actions=actions,
    )


def advanced_automation(
    *,
    automation_id: str,
    alias: str,
    trigger_kind: str,
    trigger_entity_id: str | None,
    trigger_from: str | None,
    trigger_to: str | None,
    trigger_at: str | None,
    trigger_above: int | None,
    trigger_below: int | None,
    condition_entity_id: str | None,
    condition_state: str | None,
    actions: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    """Build state, clock or numeric automations without code or templates."""

    if _AUTOMATION_ID.fullmatch(automation_id) is None:
        raise ValueError("Eva automation ID is invalid")
    if not alias.strip() or len(alias) > 160:
        raise ValueError("Eva automation alias is invalid")
    if trigger_kind not in {"state", "time", "numeric_state"}:
        raise ValueError("Eva automation trigger kind is invalid")
    if (condition_entity_id is None) != (condition_state is None):
        raise ValueError("Eva automation condition must be complete")
    if condition_entity_id is not None and (
        _ENTITY.fullmatch(condition_entity_id) is None
        or condition_state is None
        or not condition_state.strip()
        or len(condition_state) > 80
    ):
        raise ValueError("Eva automation condition is invalid")
    if not 1 <= len(actions) <= 6:
        raise ValueError("Eva automation must contain 1..6 actions")

    rendered_actions: list[JsonValue] = []
    for raw in actions:
        if set(raw) != {"delay_after_seconds", "entity_id", "service"}:
            raise ValueError("Eva automation action fields differ")
        entity_id = raw["entity_id"]
        service = raw["service"]
        delay = raw["delay_after_seconds"]
        if not isinstance(entity_id, str) or _ENTITY.fullmatch(entity_id) is None:
            raise ValueError("Eva automation action entity is invalid")
        if entity_id.split(".", 1)[0] not in _SAFE_ACTION_DOMAINS:
            raise ValueError("Eva automation action domain is not allowed")
        if service not in {"turn_on", "turn_off", "toggle"}:
            raise ValueError("Eva automation service is invalid")
        if (
            isinstance(delay, bool)
            or not isinstance(delay, int)
            or not 0 <= delay <= 86400
        ):
            raise ValueError("Eva automation delay is invalid")
        rendered_actions.append(
            {
                "action": f"homeassistant.{service}",
                "target": {"entity_id": entity_id},
            }
        )
        if delay:
            rendered_actions.append({"delay": {"seconds": delay}})

    trigger: dict[str, JsonValue]
    if trigger_kind == "state":
        if trigger_entity_id is None or _ENTITY.fullmatch(trigger_entity_id) is None:
            raise ValueError("Eva automation trigger entity is invalid")
        if trigger_to is None or not trigger_to.strip() or len(trigger_to) > 80:
            raise ValueError("Eva automation target state is invalid")
        if trigger_from is not None and (
            not trigger_from.strip() or len(trigger_from) > 80
        ):
            raise ValueError("Eva automation source state is invalid")
        if any(
            value is not None for value in (trigger_at, trigger_above, trigger_below)
        ):
            raise ValueError("Eva state trigger contains other trigger fields")
        trigger = {
            "entity_id": trigger_entity_id,
            "to": trigger_to.strip(),
            "trigger": "state",
        }
        if trigger_from is not None:
            trigger["from"] = trigger_from.strip()
    elif trigger_kind == "time":
        if (
            trigger_at is None
            or _TIME.fullmatch(trigger_at) is None
            or any(
                value is not None
                for value in (
                    trigger_entity_id,
                    trigger_from,
                    trigger_to,
                    trigger_above,
                    trigger_below,
                )
            )
        ):
            raise ValueError("Eva time trigger is invalid")
        trigger = {"at": trigger_at, "trigger": "time"}
    else:
        if (
            trigger_entity_id is None
            or _ENTITY.fullmatch(trigger_entity_id) is None
            or any(
                value is not None for value in (trigger_from, trigger_to, trigger_at)
            )
            or (trigger_above is None and trigger_below is None)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not -100_000 <= value <= 100_000
                for value in (trigger_above, trigger_below)
                if value is not None
            )
            or (
                trigger_above is not None
                and trigger_below is not None
                and trigger_above >= trigger_below
            )
        ):
            raise ValueError("Eva numeric trigger is invalid")
        trigger = {"entity_id": trigger_entity_id, "trigger": "numeric_state"}
        if trigger_above is not None:
            trigger["above"] = trigger_above
        if trigger_below is not None:
            trigger["below"] = trigger_below
    conditions: list[JsonValue] = []
    if condition_entity_id is not None and condition_state is not None:
        conditions.append(
            {
                "condition": "state",
                "entity_id": condition_entity_id,
                "state": condition_state.strip(),
            }
        )
    return {
        "actions": rendered_actions,
        "alias": alias.strip(),
        "conditions": conditions,
        "description": (
            "Beheerd door Eva2; gesloten meerstapsregel met bewijs en herstelpad."
        ),
        "mode": "restart",
        "triggers": [trigger],
    }


__all__ = [
    "advanced_automation",
    "advanced_state_automation",
    "state_action_automation",
]
