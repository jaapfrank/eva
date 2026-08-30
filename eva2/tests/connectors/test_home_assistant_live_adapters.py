from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from urllib import parse
from datetime import UTC, datetime

import pytest

from eva.adapters.connectors.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantConversationEffectPlanner,
    HomeAssistantToolAdapter,
)
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.adapters.connectors.home_assistant_hacs import HomeAssistantHacsAdapter
from eva.application.advanced_tasks import (
    AdvancedTaskPackageApproval,
    AdvancedTaskRepositoryApproval,
    HomeAssistantRepository,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationClarificationRequired,
    ConversationTurn,
)


def _private_turn(principal_id: str = "jaap") -> ConversationTurn:
    return ConversationTurn.private(
        "tenant",
        "installation",
        principal_id,
        "conversation",
        "message:provider-effect",
        "Zet de lamp uit",
        "2026-08-30T19:15:00Z",
    )


def test_effect_planner_owns_resolution_policy_and_provider_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    catalog_calls: list[str] = []

    def catalog() -> list[dict[str, object]]:
        catalog_calls.append("read")
        return [
            {
                "attributes": {"friendly_name": "Schuurlamp"},
                "entity_id": "light.schuur",
                "state": "on",
            }
        ]

    monkeypatch.setattr(
        adapter,
        "_light_catalog_sync",
        catalog,
    )
    planner = HomeAssistantConversationEffectPlanner(
        adapter,
        allowlist=frozenset({("light.schuur", "turn_off")}),
        principal_sha256_allowlist=frozenset({hashlib.sha256(b"jaap").hexdigest()}),
    )

    prepared = asyncio.run(
        planner.prepare(
            _private_turn(),
            ConversationActionCall(
                "call:provider-effect",
                "set_home_assistant_light",
                {"state": "off", "target": "Schuurlamp"},
            ),
        )
    )

    assert prepared.tool_name == "home_assistant.light.turn_off"
    assert prepared.arguments == {
        "domain": "light",
        "entity_id": "light.schuur",
        "service": "turn_off",
        "service_data": {},
    }
    assert prepared.expected_proof == {"entity_id": "light.schuur", "state": "off"}
    assert prepared.receipt_output["friendly_name"] == "Schuurlamp"
    assert catalog_calls == ["read"]

    with pytest.raises(
        ConversationClarificationRequired,
        match="alleen uitzetten, niet aanzetten",
    ):
        asyncio.run(
            planner.prepare(
                _private_turn(),
                ConversationActionCall(
                    "call:forbidden-direction",
                    "set_home_assistant_light",
                    {"state": "on", "target": "Schuurlamp"},
                ),
            )
        )

    with pytest.raises(ConversationClarificationRequired, match="dit profiel"):
        asyncio.run(
            planner.prepare(
                _private_turn("lola"),
                ConversationActionCall(
                    "call:unauthorized",
                    "set_home_assistant_light",
                    {"state": "off", "target": "Schuurlamp"},
                ),
            )
        )
    assert catalog_calls == ["read", "read"]


def test_light_resolution_ignores_unavailable_duplicate(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    monkeypatch.setattr(
        adapter,
        "_light_catalog_sync",
        lambda: [
            {
                "attributes": {"friendly_name": "kantoor links"},
                "entity_id": "light.kantoor_links",
                "state": "off",
            },
            {
                "attributes": {"friendly_name": "kantoor links"},
                "entity_id": "light.kantoor_links_2",
                "state": "unavailable",
            },
        ],
    )

    import asyncio

    assert asyncio.run(adapter.resolve_light("Kantoor links")) == {
        "entity_id": "light.kantoor_links",
        "friendly_name": "kantoor links",
        "state": "off",
    }


def test_switch_resolution_maps_a_natural_dutch_heater_name_fail_closed(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    monkeypatch.setattr(
        adapter,
        "_switch_catalog_sync",
        lambda: [
            {
                "attributes": {"friendly_name": "Heater overkapping Socket 1"},
                "entity_id": "switch.antela_heater_socket_1",
                "state": "on",
            },
            {
                "attributes": {"friendly_name": "Vijverpomp"},
                "entity_id": "switch.vijverpomp",
                "state": "off",
            },
            {
                "attributes": {"friendly_name": "Heater overkapping Socket 2"},
                "entity_id": "switch.unavailable_duplicate",
                "state": "unavailable",
            },
        ],
    )

    assert asyncio.run(adapter.resolve_switch("verwarming in de overkapping")) == {
        "entity_id": "switch.antela_heater_socket_1",
        "friendly_name": "Heater overkapping Socket 1",
        "state": "on",
    }


def test_switch_resolution_rejects_a_real_semantic_tie(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    monkeypatch.setattr(
        adapter,
        "_switch_catalog_sync",
        lambda: [
            {
                "attributes": {"friendly_name": f"Heater overkapping Socket {index}"},
                "entity_id": f"switch.heater_{index}",
                "state": "off",
            }
            for index in (1, 2)
        ],
    )

    with pytest.raises(ValueError, match="ambiguous"):
        asyncio.run(adapter.resolve_switch("verwarming in de overkapping"))


def test_automation_entities_resolve_natural_names_from_one_live_snapshot(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    calls = 0

    def catalog() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [
            {
                "attributes": {"friendly_name": "Jaap"},
                "entity_id": "person.jaap",
                "state": "home",
            },
            {
                "attributes": {"friendly_name": "Nachtmodus"},
                "entity_id": "input_boolean.nachtmodus",
                "state": "off",
            },
            {
                "attributes": {"friendly_name": "Lamp in de hal"},
                "entity_id": "light.hal",
                "state": "off",
            },
        ]

    monkeypatch.setattr(adapter, "_state_catalog_sync", catalog)

    resolved = asyncio.run(
        adapter.resolve_entities(
            (
                ("Jaap", ("person", "binary_sensor")),
                ("nachtmodus", ("input_boolean",)),
                ("lamp in de hal", ("light", "switch")),
            )
        )
    )

    assert calls == 1
    assert [item["entity_id"] for item in resolved] == [
        "person.jaap",
        "input_boolean.nachtmodus",
        "light.hal",
    ]


def test_automation_entity_resolution_rejects_ambiguous_natural_name(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    monkeypatch.setattr(
        adapter,
        "_state_catalog_sync",
        lambda: [
            {
                "attributes": {"friendly_name": "Lamp hal"},
                "entity_id": f"light.hal_{index}",
                "state": "off",
            }
            for index in (1, 2)
        ],
    )

    with pytest.raises(ValueError, match="ambiguous"):
        asyncio.run(adapter.resolve_entities((("lamp hal", ("light",)),)))


def test_history_uses_home_assistant_timezone_and_returns_only_real_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    requests: list[tuple[str, str]] = []

    async def request_json(method: str, path: str, payload=None):  # type: ignore[no-untyped-def]
        assert payload is None
        requests.append((method, path))
        if path == "/api/config":
            return {"time_zone": "Europe/Amsterdam"}
        return [
            [
                {
                    "entity_id": "light.overkapping",
                    "last_changed": "2026-08-22T21:00:00+00:00",
                    "state": "on",
                },
                {
                    "last_changed": "2026-08-22T22:34:00+00:00",
                    "state": "off",
                },
                {
                    "last_changed": "2026-08-23T18:00:00+00:00",
                    "state": "on",
                },
                {
                    "last_changed": "2026-08-23T20:15:00+00:00",
                    "state": "off",
                },
            ]
        ]

    monkeypatch.setattr(adapter, "_request_json", request_json)
    result = asyncio.run(
        adapter.history(
            entity_id="light.overkapping",
            period="yesterday",
            transition_to="off",
            observed_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        )
    )

    assert result["window_start"] == "2026-08-23T00:00:00+02:00"
    assert result["window_end"] == "2026-08-24T00:00:00+02:00"
    assert result["matches"] == [
        {
            "changed_at": "2026-08-23T00:34:00+02:00",
            "from": "on",
            "to": "off",
        },
        {
            "changed_at": "2026-08-23T22:15:00+02:00",
            "from": "on",
            "to": "off",
        },
    ]
    assert requests[0] == ("GET", "/api/config")
    assert requests[1][0] == "GET"
    assert requests[1][1].startswith("/api/history/period/2026-08-23T00:00:00%2B02:00?")
    assert "filter_entity_id=light.overkapping" in requests[1][1]


def test_current_state_returns_only_bounded_provider_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    requested: list[tuple[str, str, object]] = []

    async def request_json(method: str, path: str, payload=None):  # type: ignore[no-untyped-def]
        requested.append((method, path, payload))
        return {
            "attributes": {
                "friendly_name": "Heater overkapping",
                "vendor_secret": "must-not-leave-adapter",
            },
            "entity_id": "switch.heater_overkapping",
            "last_changed": "2026-08-24T01:55:00+00:00",
            "last_updated": "2026-08-24T01:55:01+00:00",
            "state": "on",
        }

    monkeypatch.setattr(adapter, "_request_json", request_json)

    result = asyncio.run(adapter.state(entity_id="switch.heater_overkapping"))

    assert result == {
        "contract": "EvaHomeAssistantCurrentState/v1",
        "entity_id": "switch.heater_overkapping",
        "last_changed": "2026-08-24T01:55:00+00:00",
        "last_updated": "2026-08-24T01:55:01+00:00",
        "source": "home_assistant_state",
        "state": "on",
    }
    assert requested == [("GET", "/api/states/switch.heater_overkapping", None)]


def test_history_target_resolves_a_natural_light_area_as_bounded_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    monkeypatch.setattr(
        adapter,
        "_state_catalog_sync",
        lambda: [
            {
                "attributes": {"friendly_name": "Overkapping wand"},
                "entity_id": "light.overkapping_wand",
                "state": "on",
            },
            {
                "attributes": {"friendly_name": "Spotjes overkapping"},
                "entity_id": "light.overkapping_spotjes",
                "state": "off",
            },
            {
                "attributes": {"friendly_name": "Overkapping wand"},
                "entity_id": "light.overkapping_wand_duplicate",
                "state": "unavailable",
            },
            {
                "attributes": {"friendly_name": "Keuken"},
                "entity_id": "light.keuken",
                "state": "on",
            },
        ],
    )

    resolved = asyncio.run(
        adapter.resolve_history_targets("licht in de overkapping", "light")
    )

    assert [item["entity_id"] for item in resolved] == [
        "light.overkapping_spotjes",
        "light.overkapping_wand",
    ]


def test_group_history_uses_all_off_and_discards_short_stagger_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "token"))
    rows = {
        "light.overkapping_a": [
            ("2026-08-22T21:00:00+00:00", "on"),
            ("2026-08-23T00:55:00+00:00", "off"),
            ("2026-08-23T00:55:03+00:00", "on"),
            ("2026-08-23T04:30:00+00:00", "off"),
        ],
        "light.overkapping_b": [
            ("2026-08-22T21:00:00+00:00", "on"),
            ("2026-08-23T00:55:02+00:00", "off"),
            ("2026-08-23T00:55:04+00:00", "on"),
            ("2026-08-23T04:31:00+00:00", "off"),
        ],
    }

    async def request_json(method: str, path: str, payload=None):  # type: ignore[no-untyped-def]
        assert method == "GET" and payload is None
        if path == "/api/config":
            return {"time_zone": "Europe/Amsterdam"}
        query = parse.parse_qs(parse.urlsplit(path).query)
        entity_id = query["filter_entity_id"][0]
        return [
            [
                {"entity_id": entity_id, "last_changed": changed, "state": state}
                for changed, state in rows[entity_id]
            ]
        ]

    monkeypatch.setattr(adapter, "_request_json", request_json)
    result = asyncio.run(
        adapter.history_group(
            entity_ids=("light.overkapping_a", "light.overkapping_b"),
            period="yesterday",
            transition_to="off",
            observed_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        )
    )

    assert result["aggregation"] == "any_member_on_all_members_off"
    assert result["matches"] == [
        {
            "changed_at": "2026-08-23T06:31:00+02:00",
            "from": "on",
            "stable_for_seconds": 62_940,
            "to": "off",
        }
    ]


def test_state_changed_frame_is_minimized_and_stably_identified() -> None:
    source = HomeAssistantEventStream(
        base_url="http://ha.test:8123",
        token="token",
        admitted_domains=("light",),
    )
    frame = json.dumps(
        {
            "event": {
                "data": {
                    "entity_id": "light.kantoor_links",
                    "new_state": {
                        "attributes": {
                            "brightness": 255,
                            "friendly_name": "kantoor links",
                            "secret_vendor_blob": "not retained",
                        },
                        "context": {"id": "context-1"},
                        "state": "on",
                    },
                    "old_state": {"state": "off"},
                },
                "time_fired": "2026-08-23T14:29:04.123456+00:00",
            },
            "id": 1,
            "type": "event",
        }
    )

    event = source._event_from_frame(frame)

    assert event is not None
    assert event.payload == {
        "context_id": "context-1",
        "device_class": None,
        "entity_id": "light.kantoor_links",
        "friendly_name": "kantoor links",
        "new_state": "on",
        "old_state": "off",
        "time_fired": "2026-08-23T14:29:04.123456+00:00",
        "unit_of_measurement": None,
    }
    assert event.source_event_id.startswith("home-assistant:")


def test_state_changed_frame_drops_creation_and_same_state_noise() -> None:
    source = HomeAssistantEventStream(
        base_url="http://ha.test:8123",
        token="token",
        admitted_domains=("binary_sensor", "light"),
    )

    def frame(old_state: object, new_value: str) -> str:
        return json.dumps(
            {
                "event": {
                    "data": {
                        "entity_id": "binary_sensor.achterdeur",
                        "new_state": {
                            "attributes": {"device_class": "door"},
                            "state": new_value,
                        },
                        "old_state": old_state,
                    },
                    "time_fired": "2026-08-23T14:29:04+00:00",
                },
                "id": 1,
                "type": "event",
            }
        )

    assert source._event_from_frame(frame(None, "off")) is None
    assert source._event_from_frame(frame({"state": "off"}, "off")) is None
    event = source._event_from_frame(frame({"state": "off"}, "on"))
    assert event is not None
    assert event.payload["device_class"] == "door"


def test_admin_inventory_keeps_only_read_only_localtuya_package_evidence() -> None:
    inventory = HomeAssistantAdminAdapter._select_inventory(
        [
            {
                "domain": "tuya",
                "entry_id": "tuya-entry",
                "state": "loaded",
                "title": "Tuya",
            },
            {
                "domain": "localtuya",
                "entry_id": "local-entry",
                "state": "not_loaded",
                "title": "LocalTuya",
            },
        ],
        [
            {
                "config_entries": ["tuya-entry"],
                "id": "device-1",
                "manufacturer": "Tuya",
                "model": "Lamp",
                "name": "Schuur",
            }
        ],
        [
            {
                "config_entry_id": "tuya-entry",
                "device_id": "device-1",
                "disabled_by": None,
                "entity_id": "light.schuur",
                "platform": "tuya",
            }
        ],
        [
            {
                "available_version": "v5.2.5",
                "can_download": True,
                "domain": "localtuya",
                "full_name": "rospogrigio/localtuya",
                "id": "249381778",
                "installed": False,
                "installed_version": "",
            },
            {
                "domain": "other",
                "full_name": "vendor/other",
                "id": "other",
                "installed": True,
            },
        ],
        domains=frozenset({"localtuya", "tuya"}),
        captured_at=datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
        repository_inventory_verified=True,
        home_assistant_version="2026.8.1",
    )

    assert inventory.repository_inventory_verified is True
    assert len(inventory.repositories) == 1
    assert inventory.repositories[0].full_name == "rospogrigio/localtuya"
    assert inventory.repositories[0].installed is False
    assert inventory.repositories[0].available_version == "v5.2.5"
    assert inventory.home_assistant_version == "2026.8.1"


def test_hacs_install_requires_exact_approval_and_independent_readback(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantHacsAdapter(base_url="http://ha.test", token="token")
    before = HomeAssistantRepository(
        "249381778",
        "rospogrigio/localtuya",
        "localtuya",
        False,
        None,
        "v5.2.5",
        True,
    )
    after = replace(before, installed=True, installed_version="v5.2.5")
    observations = iter((before, after))
    commands: list[tuple[str, dict[str, object]]] = []

    async def repository(_: str) -> HomeAssistantRepository:
        return next(observations)

    monkeypatch.setattr(adapter, "repository", repository)
    monkeypatch.setattr(
        adapter,
        "_command_sync",
        lambda command, values: commands.append((command, values)),
    )
    now = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
    approval = AdvancedTaskPackageApproval(
        "approval:package",
        "advanced:plan",
        "a" * 64,
        "b" * 64,
        before.repository_id,
        before.sha256,
        "v5.2.5",
        "jaap",
        now,
        now + timedelta(minutes=15),
    )

    observed = asyncio.run(
        adapter.install_exact(
            approval, now=now, poll_interval_seconds=0.001, timeout_seconds=1
        )
    )

    assert observed == after
    assert commands == [
        (
            "hacs/repository/download",
            {"repository": "249381778", "version": "v5.2.5"},
        )
    ]


def test_hacs_registration_adds_exact_source_then_verifies_readback(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantHacsAdapter(base_url="http://ha.test", token="token")
    maintained = HomeAssistantRepository(
        "maintained-1",
        "xZetsubou/hass-localtuya",
        "localtuya",
        False,
        None,
        "2026.7.0",
        True,
        "2025.1.0",
        True,
    )
    observations = iter((None, maintained))
    commands: list[tuple[str, dict[str, object]]] = []

    async def repository_by_full_name(_: str) -> HomeAssistantRepository | None:
        return next(observations)

    monkeypatch.setattr(adapter, "repository_by_full_name", repository_by_full_name)
    monkeypatch.setattr(
        adapter,
        "_command_sync",
        lambda command, values: commands.append((command, values)),
    )
    now = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    approval = AdvancedTaskRepositoryApproval(
        "approval:repository",
        "advanced:plan",
        "a" * 64,
        "b" * 64,
        "xZetsubou/hass-localtuya",
        "2026.7.0",
        "jaap",
        now,
        now + timedelta(minutes=15),
    )

    observed = asyncio.run(
        adapter.register_exact(
            approval, now=now, poll_interval_seconds=0.001, timeout_seconds=1
        )
    )

    assert observed == maintained
    assert commands == [
        (
            "hacs/repositories/add",
            {
                "category": "integration",
                "repository": "xZetsubou/hass-localtuya",
            },
        )
    ]


def test_hacs_registration_rejects_mismatched_readback(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapter = HomeAssistantHacsAdapter(base_url="http://ha.test", token="token")
    wrong = HomeAssistantRepository(
        "maintained-1",
        "xZetsubou/hass-localtuya",
        "localtuya",
        False,
        None,
        "2026.6.1",
        True,
        "2025.1.0",
        True,
    )

    async def repository_by_full_name(_: str) -> HomeAssistantRepository | None:
        return wrong

    monkeypatch.setattr(adapter, "repository_by_full_name", repository_by_full_name)
    now = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    approval = AdvancedTaskRepositoryApproval(
        "approval:repository",
        "advanced:plan",
        "a" * 64,
        "b" * 64,
        "xZetsubou/hass-localtuya",
        "2026.7.0",
        "jaap",
        now,
        now + timedelta(minutes=15),
    )

    with pytest.raises(Exception, match="registered LocalTuya repository differs"):
        asyncio.run(adapter.register_exact(approval, now=now))
