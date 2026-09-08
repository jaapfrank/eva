from copy import deepcopy
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined
import pytest

from tools.plan_home_assistant_voice_bridge import configuration_sha256, plan_voice_bridge

DEVICES = {"a" * 32: "assist_satellite.living", "b" * 32: "assist_satellite.garden"}
ENV = Environment(undefined=StrictUndefined)


def original():
    return {"id": "bridge", "mode": "parallel", "max": 10, "actions": [
        {"event": "eva_voice_query", "event_data": {
            "id": "{{ context.id }}", "satellite": "old-fallback", "sender": "Jaap"}},
        {"wait_for_trigger": [{"trigger": "event", "event_type": "eva_voice_reply",
                               "event_data": {"id": "{{ context.id }}"}}],
         "timeout": "00:00:35", "continue_on_timeout": True},
        {"choose": [{"conditions": [{"condition": "template", "value_template": "{{ wait.trigger is not none }}"}],
                     "sequence": [{"set_conversation_response": "{{ wait.trigger.event.data.reply }}"},
                                  {"event": "eva_voice_reply_ack", "event_data": {"accepted": True}}]}],
         "default": [{"set_conversation_response": "timeout"}]},
    ]}


def offer():
    return dict(id="request-1", reply='Hoi "Jaap"!\nCuraçao', conversation_id="conversation-1",
                satellite=DEVICES["a" * 32], delivery_operation_ref="home-assistant-voice-reply:" + "c" * 64,
                payload_sha256="d" * 64, reply_sha256="e" * 64)


def render(template, *, device="a" * 32, data=None, timeout=False):
    return ENV.from_string(template).render(
        trigger={} if device is None else {"device_id": device}, context={"id": "request-1"},
        wait=SimpleNamespace(trigger=None if timeout else SimpleNamespace(
            event=SimpleNamespace(data=offer() if data is None else data))),
    )


def planned():
    return plan_voice_bridge(original(), device_satellites=DEVICES)["automation"]


def condition(config):
    return config["actions"][2]["choose"][0]["conditions"][0]["value_template"]


def test_only_three_declared_subtrees_change_and_input_is_untouched():
    before = original()
    plan = plan_voice_bridge(before, device_satellites=DEVICES)
    assert before == original()
    after = deepcopy(plan["automation"])
    after["actions"][0]["event_data"]["satellite"] = before["actions"][0]["event_data"]["satellite"]
    after["actions"][2]["choose"][0]["conditions"] = before["actions"][2]["choose"][0]["conditions"]
    after["actions"][2]["choose"][0]["sequence"][1]["event_data"] = before["actions"][2]["choose"][0]["sequence"][1]["event_data"]
    assert after == before
    assert plan["beforeSha256"] == configuration_sha256(before)
    assert plan["afterSha256"] == configuration_sha256(plan["automation"])


@pytest.mark.parametrize("device,expected", [*DEVICES.items(), ("unknown", ""), (None, "")])
def test_exact_device_destination_without_fallback(device, expected):
    config = planned()
    assert render(config["actions"][0]["event_data"]["satellite"], device=device) == expected
    reply = offer()
    reply["satellite"] = expected
    assert render(condition(config), device=device, data=reply) == ("True" if expected else "False")


def test_ack_preserves_exact_unicode_reply_and_all_offer_binding_fields():
    config = planned()
    assert render(condition(config)) == "True"
    ack = config["actions"][2]["choose"][0]["sequence"][1]["event_data"]
    assert {k: render(v) for k, v in ack.items() if k != "accepted"} == offer()
    assert ack["accepted"] is True


@pytest.mark.parametrize("field,value", [
    ("id", "other-request"), ("satellite", "assist_satellite.other"),
    ("payload_sha256", ""), ("reply_sha256", "X" * 64),
    ("delivery_operation_ref", "bad:" + "c" * 64), ("reply", None), ("conversation_id", ""),
])
def test_invalid_offer_never_reaches_spoken_response_or_ack(field, value):
    data = offer()
    data[field] = value
    assert render(condition(planned()), data=data) == "False"


def test_missing_hash_and_timeout_do_not_acknowledge():
    data = offer()
    del data["reply_sha256"]
    assert render(condition(planned()), data=data) == "False"
    assert render(condition(planned()), timeout=True) == "False"


@pytest.mark.parametrize("mapping", [{}, {"invalid": "assist_satellite.living"},
                                       {"a" * 32: "media_player.other"},
                                       {"a" * 32: "assist_satellite.same", "b" * 32: "assist_satellite.same"}])
def test_mapping_requires_explicit_unique_device_destinations(mapping):
    with pytest.raises(ValueError):
        plan_voice_bridge(original(), device_satellites=mapping)


def test_changed_or_ambiguous_automation_is_rejected():
    config = original()
    config["actions"].append(deepcopy(config["actions"][0]))
    with pytest.raises(ValueError):
        plan_voice_bridge(config, device_satellites=DEVICES)
    config = original()
    config["actions"][1]["wait_for_trigger"][0]["event_data"] = {}
    with pytest.raises(ValueError):
        plan_voice_bridge(config, device_satellites=DEVICES)


def test_registry_device_replacement_does_not_reuse_old_automation_id():
    from tools.plan_home_assistant_voice_bridge import device_satellite_mapping
    registry = [{"entity_id": "assist_satellite.living", "device_id": "f" * 32,
                 "platform": "esphome", "disabled_by": None}]
    mapping = device_satellite_mapping(registry, satellite_ids=("assist_satellite.living",))
    plan = plan_voice_bridge(original(), device_satellites=mapping)
    template = plan["automation"]["actions"][0]["event_data"]["satellite"]
    assert render(template, device="f" * 32) == "assist_satellite.living"
    assert render(template, device="a" * 32) == ""


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "disabled", "no-device", "wrong-platform"])
def test_registry_rejects_unusable_or_ambiguous_binding(mutation):
    from tools.plan_home_assistant_voice_bridge import device_satellite_mapping
    row = {"entity_id": "assist_satellite.living", "device_id": "f" * 32,
           "platform": "esphome", "disabled_by": None}
    registry = [row]
    if mutation == "missing":
        registry = []
    elif mutation == "duplicate":
        registry.append(dict(row))
    elif mutation == "disabled":
        row["disabled_by"] = "user"
    elif mutation == "no-device":
        row["device_id"] = None
    else:
        row["platform"] = "other"
    with pytest.raises(ValueError):
        device_satellite_mapping(registry, satellite_ids=("assist_satellite.living",))
