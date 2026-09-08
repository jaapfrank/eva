"""Plan a bounded repair of the existing HA voice transport; never apply it.

The caller must recheck beforeSha256 against current configuration before any
separately reviewed update. This grants neither personal identity nor core access.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

_DEVICE = re.compile(r"[0-9a-f]{32}")
_SATELLITE = re.compile(r"assist_satellite\.[a-z0-9_]+")
_OFFER_FIELDS = (
    "id", "reply", "conversation_id", "satellite", "delivery_operation_ref",
    "payload_sha256", "reply_sha256",
)


def configuration_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode()).hexdigest()


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("unexpected voice bridge structure")


def _mapping(devices: dict[str, str]) -> str:
    if (not isinstance(devices, dict) or not devices or any(not isinstance(k, str) or not _DEVICE.fullmatch(k)
                           or not isinstance(v, str) or not _SATELLITE.fullmatch(v)
                           for k, v in devices.items())
            or len(set(devices.values())) != len(devices)):
        raise ValueError("explicit unique device/satellite mapping required")
    return json.dumps(devices, sort_keys=True, separators=(",", ":"))


def plan_voice_bridge(
    automation: dict[str, Any], *, device_satellites: dict[str, str],
) -> dict[str, Any]:
    """Accept only the observed bridge shape, keeping unrelated config exact."""
    devices = _mapping(device_satellites)
    after = deepcopy(automation)
    try:
        actions = after["actions"]
        query, wait, response = actions
        _require(query["event"] == "eva_voice_query")
        _require(query["event_data"]["id"] == "{{ context.id }}")
        _require(wait["wait_for_trigger"] == [{
            "trigger": "event", "event_type": "eva_voice_reply",
            "event_data": {"id": "{{ context.id }}"},
        }])
        _require(wait["continue_on_timeout"] is True)
        choice, = response["choose"]
        _require(choice["conditions"] == [{
            "condition": "template", "value_template": "{{ wait.trigger is not none }}",
        }])
        speak, ack = choice["sequence"]
        _require(speak == {"set_conversation_response": "{{ wait.trigger.event.data.reply }}"})
        _require(ack["event"] == "eva_voice_reply_ack")
        _require(isinstance(query["event_data"]["satellite"], str))
        _require(isinstance(ack["event_data"], dict))
        _require(len(response["default"]) == 1)
        _require(set(response["default"][0]) == {"set_conversation_response"})
    except (KeyError, TypeError, ValueError, AssertionError) as error:
        raise ValueError("voice bridge configuration differs from expected shape") from error

    binding = "{% set devices = " + devices + " %}{% set satellite = devices.get(trigger.device_id | default(''), '') %}"
    query["event_data"]["satellite"] = binding + "{{ satellite }}"
    # Validate the offer before it becomes a spoken response or an ACK.
    # All tests are standard Jinja; no HA-specific regex extension is needed.
    checks = [
        "satellite != ''", "wait.trigger is not none",
        "offer is mapping", "offer.id == context.id", "offer.satellite == satellite",
        "offer.reply is string", "offer.reply | length > 0",
        "offer.conversation_id is string", "offer.conversation_id | length > 0",
        "offer.delivery_operation_ref is string",
        "offer.delivery_operation_ref[:27] == 'home-assistant-voice-reply:'",
        "offer.delivery_operation_ref | length == 91",
    ]
    # The prefix above is 27 characters; every remaining character must be hex.
    for value in ("offer.delivery_operation_ref[27:]", "offer.payload_sha256", "offer.reply_sha256"):
        checks += [f"{value} is string", f"{value} | length == 64",
                   f"{value} | reject('in', '0123456789abcdef') | list | length == 0"]
    choice["conditions"][0]["value_template"] = (
        binding + "{% set offer = wait.trigger.event.data if wait.trigger is not none else {} %}"
        + "{{ " + " and ".join(checks) + " }}"
    )
    ack["event_data"] = {
        field: "{{ wait.trigger.event.data." + field + " }}" for field in _OFFER_FIELDS
    }
    ack["event_data"]["accepted"] = True
    return {
        "status": "plan_only", "beforeSha256": configuration_sha256(automation),
        "afterSha256": configuration_sha256(after), "automation": after,
        "changedPaths": ["actions[0].event_data.satellite",
                         "actions[2].choose[0].conditions[0].value_template",
                         "actions[2].choose[0].sequence[1].event_data"],
        "nonClaims": ["No live write or speaker output.",
                      "No speaker identity grant or Eva2 core handoff.",
                      "Acknowledgement proves transport acceptance, not acoustic playback."],
    }
