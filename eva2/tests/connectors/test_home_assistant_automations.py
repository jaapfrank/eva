from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from eva.adapters.connectors.home_assistant_automations import (
    HomeAssistantAutomationAdapter,
    HomeAssistantAutomationConfig,
    acceptance_automation,
    advanced_automation,
    advanced_state_automation,
    state_action_automation,
)
from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256


def test_state_action_builder_is_bounded() -> None:
    config = state_action_automation(
        automation_id="eva2_hal_aan",
        alias="Hal aan bij thuiskomst",
        trigger_entity_id="person.jaap",
        trigger_to="home",
        action_entity_id="light.hal",
        action_service="turn_on",
    )

    assert config["triggers"] == [
        {"entity_id": "person.jaap", "to": "home", "trigger": "state"}
    ]
    assert config["actions"] == [
        {"action": "homeassistant.turn_on", "target": {"entity_id": "light.hal"}}
    ]


def test_state_action_builder_rejects_a_sensitive_action_domain() -> None:
    try:
        state_action_automation(
            automation_id="eva2_onveilig",
            alias="Voordeur",
            trigger_entity_id="person.jaap",
            trigger_to="home",
            action_entity_id="lock.voordeur",
            action_service="turn_on",
        )
    except ValueError as error:
        assert str(error) == "Eva automation action domain is not allowed"
    else:
        raise AssertionError("sensitive action domain was accepted")


def test_advanced_builder_supports_condition_sequence_and_delay() -> None:
    config = advanced_state_automation(
        automation_id="eva2_avondroute",
        alias="Avondroute bij thuiskomst",
        trigger_entity_id="person.jaap",
        trigger_from="not_home",
        trigger_to="home",
        condition_entity_id="input_boolean.nachtmodus",
        condition_state="on",
        actions=[
            {
                "delay_after_seconds": 30,
                "entity_id": "light.hal",
                "service": "turn_on",
            },
            {
                "delay_after_seconds": 0,
                "entity_id": "switch.koffiehoek",
                "service": "turn_on",
            },
        ],
    )

    assert config["triggers"] == [
        {
            "entity_id": "person.jaap",
            "from": "not_home",
            "to": "home",
            "trigger": "state",
        }
    ]
    assert config["conditions"] == [
        {
            "condition": "state",
            "entity_id": "input_boolean.nachtmodus",
            "state": "on",
        }
    ]
    assert config["actions"] == [
        {"action": "homeassistant.turn_on", "target": {"entity_id": "light.hal"}},
        {"delay": {"seconds": 30}},
        {
            "action": "homeassistant.turn_on",
            "target": {"entity_id": "switch.koffiehoek"},
        },
    ]


def test_advanced_builder_rejects_sensitive_action_domains() -> None:
    try:
        advanced_state_automation(
            automation_id="eva2_onveilig",
            alias="Onveilig",
            trigger_entity_id="person.jaap",
            trigger_from=None,
            trigger_to="home",
            condition_entity_id=None,
            condition_state=None,
            actions=[
                {
                    "delay_after_seconds": 0,
                    "entity_id": "lock.voordeur",
                    "service": "turn_on",
                }
            ],
        )
    except ValueError as error:
        assert str(error) == "Eva automation action domain is not allowed"
    else:
        raise AssertionError("sensitive action domain was accepted")


def test_advanced_builder_supports_clock_and_numeric_triggers() -> None:
    actions = [
        {
            "delay_after_seconds": 0,
            "entity_id": "light.hal",
            "service": "turn_on",
        }
    ]
    clock = advanced_automation(
        automation_id="eva2_ochtend",
        alias="Ochtendlicht",
        trigger_kind="time",
        trigger_entity_id=None,
        trigger_from=None,
        trigger_to=None,
        trigger_at="07:15",
        trigger_above=None,
        trigger_below=None,
        condition_entity_id=None,
        condition_state=None,
        actions=actions,
    )
    numeric = advanced_automation(
        automation_id="eva2_koud",
        alias="Verwarming bij kou",
        trigger_kind="numeric_state",
        trigger_entity_id="sensor.overkapping_temperatuur",
        trigger_from=None,
        trigger_to=None,
        trigger_at=None,
        trigger_above=None,
        trigger_below=12,
        condition_entity_id="input_boolean.thuis",
        condition_state="on",
        actions=[
            {
                "delay_after_seconds": 0,
                "entity_id": "switch.overkapping_verwarming",
                "service": "turn_on",
            }
        ],
    )

    assert clock["triggers"] == [{"at": "07:15", "trigger": "time"}]
    assert numeric["triggers"] == [
        {
            "below": 12,
            "entity_id": "sensor.overkapping_temperatuur",
            "trigger": "numeric_state",
        }
    ]
    assert numeric["conditions"] == [
        {
            "condition": "state",
            "entity_id": "input_boolean.thuis",
            "state": "on",
        }
    ]


def test_advanced_builder_rejects_mixed_trigger_fields() -> None:
    try:
        advanced_automation(
            automation_id="eva2_mixed",
            alias="Onjuiste mix",
            trigger_kind="time",
            trigger_entity_id="sensor.temperatuur",
            trigger_from=None,
            trigger_to=None,
            trigger_at="07:15",
            trigger_above=None,
            trigger_below=None,
            condition_entity_id=None,
            condition_state=None,
            actions=[
                {
                    "delay_after_seconds": 0,
                    "entity_id": "light.hal",
                    "service": "turn_on",
                }
            ],
        )
    except ValueError as error:
        assert str(error) == "Eva time trigger is invalid"
    else:
        raise AssertionError("mixed time trigger was accepted")


def test_automation_create_readback_and_delete_are_exact() -> None:
    stored: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            if not stored:
                self.send_error(404)
                return
            self._send(stored)

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            stored.update(json.loads(self.rfile.read(size)))
            self._send({"result": "ok"})

        def do_DELETE(self) -> None:  # noqa: N802
            stored.clear()
            self._send({"result": "ok"})

        def _send(self, value: object) -> None:
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = acceptance_automation("eva2_b_acceptance")
        adapter = HomeAssistantAutomationAdapter(
            HomeAssistantAutomationConfig(
                f"http://127.0.0.1:{server.server_port}", "test-token"
            )
        )
        request = ToolRequest(
            "automation:test",
            "operation:test",
            "checkpoint:automation:test",
            "home_assistant.automation.create",
            {"automation_id": "eva2_b_acceptance", "config": config},
            {
                "automation_id": "eva2_b_acceptance",
                "config_sha256": canonical_sha256(config),
            },
        )

        dispatch = asyncio.run(adapter.execute(request))
        proof = asyncio.run(adapter.observe(request, dispatch))
        deleted = asyncio.run(
            adapter.delete_verified(
                "eva2_b_acceptance", expected_config_sha256=canonical_sha256(config)
            )
        )

        assert dispatch.accepted is True
        assert proof.disposition == "verified"
        assert proof.observed["config_sha256"] == canonical_sha256(config)
        assert deleted == "deleted"
        assert stored == {}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
