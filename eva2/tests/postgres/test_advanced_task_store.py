from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.providers.home_assistant_advanced_store import (
    PostgresAdvancedTaskStore,
)
from eva.adapters.connectors.home_assistant_advanced_tasks import (
    AdvancedTaskCanaryProof,
    AdvancedTaskDevice,
    AdvancedTaskDevicePreflight,
    AdvancedTaskError,
    AdvancedTaskPlan,
    HomeAssistantInventory,
    HomeAssistantRepository,
)


def _plan(
    *,
    inventory_sha256: str,
    minute: int = 0,
    blockers: tuple[str, ...] = ("local_device_credentials_not_collected",),
) -> AdvancedTaskPlan:
    return AdvancedTaskPlan(
        "advanced:test",
        "operation:test",
        "migrate_tuya_to_localtuya",
        inventory_sha256,
        "blocked_preflight",
        blockers,
        (),
        (),
        "jaap",
        datetime(2026, 8, 23, 14, minute, tzinfo=UTC),
    )


def test_plan_refresh_is_append_only_and_loads_the_latest_inventory() -> None:
    first = _plan(inventory_sha256="a" * 64)
    second = _plan(
        inventory_sha256="b" * 64,
        minute=1,
        blockers=("localtuya_package_not_installed",),
    )
    events = [
        SimpleNamespace(
            event_type="ADVANCED_PLAN_CREATED", payload={"data": first.to_payload()}
        )
    ]

    class Timeline:
        def events(self, thread_id: str):  # type: ignore[no-untyped-def]
            assert thread_id == first.plan_id
            return tuple(events)

        def load(self, thread_id: str):  # type: ignore[no-untyped-def]
            assert thread_id == first.plan_id
            return SimpleNamespace(revision=len(events))

        def transition(self, **values):  # type: ignore[no-untyped-def]
            assert values["expected_revision"] == 1
            events.append(
                SimpleNamespace(
                    event_type=values["event_type"],
                    payload={"data": values["data"]},
                )
            )

    store = object.__new__(PostgresAdvancedTaskStore)
    store._timeline = Timeline()  # type: ignore[attr-defined]

    assert store.refresh(second) == "refreshed"
    assert store.load(first.plan_id) == second
    assert [event.event_type for event in events] == [
        "ADVANCED_PLAN_CREATED",
        "ADVANCED_PLAN_REFRESHED",
    ]
    assert store.refresh(second) == "already_current"
    assert len(events) == 2


def test_latest_scopes_before_parsing_another_principals_legacy_plan() -> None:
    current = _plan(inventory_sha256="c" * 64)
    legacy_other = {
        "contract": "EvaAdvancedTaskPlan/v1",
        "requested_by": "somebody-else",
    }
    by_thread = {
        "advanced:legacy-other": (
            SimpleNamespace(
                event_type="ADVANCED_PLAN_CREATED",
                payload={"data": legacy_other},
            ),
        ),
        current.plan_id: (
            SimpleNamespace(
                event_type="ADVANCED_PLAN_CREATED",
                payload={"data": current.to_payload()},
            ),
        ),
    }

    class Timeline:
        def list_checkpoints(self, **_values):  # type: ignore[no-untyped-def]
            return (
                SimpleNamespace(thread_id="advanced:legacy-other"),
                SimpleNamespace(thread_id=current.plan_id),
            )

        def events(self, thread_id: str):  # type: ignore[no-untyped-def]
            return by_thread[thread_id]

    store = object.__new__(PostgresAdvancedTaskStore)
    store._timeline = Timeline()  # type: ignore[attr-defined]

    assert store.latest(requested_by="jaap") == current


def test_plan_refresh_does_not_append_when_only_capture_metadata_changed() -> None:
    first = _plan(inventory_sha256="a" * 64)
    same_inventory = _plan(inventory_sha256="b" * 64, minute=1)
    events = [
        SimpleNamespace(
            event_type="ADVANCED_PLAN_CREATED", payload={"data": first.to_payload()}
        )
    ]

    class Timeline:
        def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
            return tuple(events)

    store = object.__new__(PostgresAdvancedTaskStore)
    store._timeline = Timeline()  # type: ignore[attr-defined]

    assert store.refresh(same_inventory) == "already_current"
    assert store.load(first.plan_id) == first
    assert len(events) == 1


def test_plan_refresh_cannot_change_task_identity() -> None:
    first = _plan(inventory_sha256="a" * 64)

    class Timeline:
        def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
            return (
                SimpleNamespace(
                    event_type="ADVANCED_PLAN_CREATED",
                    payload={"data": first.to_payload()},
                ),
            )

    store = object.__new__(PostgresAdvancedTaskStore)
    store._timeline = Timeline()  # type: ignore[attr-defined]
    wrong = AdvancedTaskPlan(
        first.plan_id,
        "operation:other",
        first.task,
        "b" * 64,
        first.state,
        first.blockers,
        first.devices,
        first.steps,
        first.requested_by,
        datetime(2026, 8, 23, 14, 1, tzinfo=UTC),
    )

    with pytest.raises(AdvancedTaskError, match="changed task identity"):
        store.refresh(wrong)


def test_secret_free_device_preflight_is_durable_and_unlocks_one_canary() -> None:
    plan = _plan(inventory_sha256="a" * 64)
    plan = AdvancedTaskPlan(
        plan.plan_id,
        plan.operation_id,
        plan.task,
        plan.inventory_sha256,
        plan.state,
        plan.blockers,
        (
            AdvancedTaskDevice(
                "device-light",
                "Schuur 1",
                "localtuya_candidate",
                ("light.schuur_1",),
            ),
        ),
        plan.steps,
        plan.requested_by,
        plan.created_at,
    )
    preflight = AdvancedTaskDevicePreflight(
        "preflight:test",
        plan.plan_id,
        plan.sha256,
        plan.inventory_sha256,
        "device-light",
        "01K3XJR7BTERG1Z20Q1J2HYP0B",
        "b" * 64,
        "c" * 64,
        "3.3",
        "d" * 64,
        datetime(2026, 8, 23, 14, 1, tzinfo=UTC),
    )
    events = [
        SimpleNamespace(
            event_type="ADVANCED_PLAN_CREATED", payload={"data": plan.to_payload()}
        )
    ]

    class Timeline:
        def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
            return tuple(events)

        def load(self, _thread_id: str):  # type: ignore[no-untyped-def]
            return SimpleNamespace(revision=len(events))

        def transition(self, **values):  # type: ignore[no-untyped-def]
            events.append(
                SimpleNamespace(
                    event_type=values["event_type"],
                    payload={"data": values["data"]},
                )
            )

    store = object.__new__(PostgresAdvancedTaskStore)
    store._timeline = Timeline()  # type: ignore[attr-defined]

    assert store.retain_device_preflight(preflight) == "verified"
    approval = store.approve_canary(
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        device_id="device-light",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 23, 14, 2, tzinfo=UTC),
    )

    assert approval.device_id == "device-light"
    assert [event.event_type for event in events] == [
        "ADVANCED_PLAN_CREATED",
        "ADVANCED_DEVICE_PREFLIGHT_VERIFIED",
        "ADVANCED_CANARY_APPROVED",
    ]
    assert "local_key" not in str(events[1].payload)

    durable_approval, durable_preflight = store.canary_authority(
        plan_id=plan.plan_id,
        approval_sha256=approval.sha256,
        preflight_sha256=preflight.sha256,
    )
    assert durable_approval == approval
    assert durable_preflight == preflight
    before_inventory = HomeAssistantInventory(
        (), (), (), datetime(2026, 8, 23, 14, 3, tzinfo=UTC)
    )
    assert (
        store.prepare_canary(
            approval,
            preflight,
            configuration_sha256="e" * 64,
            before_inventory=before_inventory,
            occurred_at=datetime(2026, 8, 23, 14, 3, tzinfo=UTC),
        )
        == "prepared"
    )
    assert (
        store.prepare_canary(
            approval,
            preflight,
            configuration_sha256="e" * 64,
            before_inventory=before_inventory,
            occurred_at=datetime(2026, 8, 23, 14, 3, tzinfo=UTC),
        )
        == "already_prepared"
    )
    assert store.prepared_canary_context(plan_id=plan.plan_id) == (
        approval,
        preflight,
        "e" * 64,
        before_inventory,
    )
    assert (
        store.open_canary_call(
            plan_id=plan.plan_id,
            configuration_sha256="e" * 64,
            occurred_at=datetime(2026, 8, 23, 14, 3, 10, tzinfo=UTC),
        )
        == "opened"
    )
    assert (
        store.open_canary_call(
            plan_id=plan.plan_id,
            configuration_sha256="e" * 64,
            occurred_at=datetime(2026, 8, 23, 14, 3, 11, tzinfo=UTC),
        )
        == "already_opened"
    )
    receipt = {
        "configuration_sha256": "e" * 64,
        "configured_entity_count": 1,
        "contract": "EvaLocalTuyaCreationReceipt/v1",
        "disposition": "created",
    }
    assert (
        store.record_canary_call_result(
            plan_id=plan.plan_id,
            configuration_sha256="e" * 64,
            receipt=receipt,
            occurred_at=datetime(2026, 8, 23, 14, 3, 12, tzinfo=UTC),
        )
        == "recorded"
    )
    proof = AdvancedTaskCanaryProof(
        "proof:test",
        plan.plan_id,
        plan.sha256,
        plan.inventory_sha256,
        approval.approval_id,
        approval.sha256,
        preflight.sha256,
        "device-light",
        "e" * 64,
        preflight.config_entry_id,
        "device-light-local",
        ("light.schuur_1_lokaal",),
        ("light.schuur_1",),
        True,
        datetime(2026, 8, 23, 14, 4, tzinfo=UTC),
    )
    assert store.record_canary_verified(proof) == "verified"
    assert store.record_canary_verified(proof) == "already_verified"
    assert [event.event_type for event in events][-4:] == [
        "ADVANCED_CANARY_PREPARED",
        "ADVANCED_CANARY_CALL_OPENED",
        "ADVANCED_CANARY_CALL_RETURNED",
        "ADVANCED_CANARY_VERIFIED",
    ]


def test_repository_registration_is_separately_approved_and_readback_bound() -> None:
    plan = _plan(
        inventory_sha256="f" * 64,
        blockers=("localtuya_maintained_repository_not_available",),
    )
    events = [
        SimpleNamespace(
            event_type="ADVANCED_PLAN_CREATED", payload={"data": plan.to_payload()}
        )
    ]

    class Timeline:
        def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
            return tuple(events)

        def load(self, _thread_id: str):  # type: ignore[no-untyped-def]
            return SimpleNamespace(revision=len(events))

        def transition(self, **values):  # type: ignore[no-untyped-def]
            events.append(
                SimpleNamespace(
                    event_type=values["event_type"],
                    payload={"data": values["data"]},
                )
            )

    store = object.__new__(PostgresAdvancedTaskStore)
    store._timeline = Timeline()  # type: ignore[attr-defined]
    now = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)

    approval = store.approve_repository_registration(
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        repository_full_name="xZetsubou/hass-localtuya",
        expected_version="2026.7.0",
        approved_by="jaap",
        approved_at=now,
    )
    assert (
        store.repository_approval(
            plan_id=plan.plan_id, approval_sha256=approval.sha256
        )
        == approval
    )
    assert store.prepare_repository_registration(approval, occurred_at=now) == "prepared"
    assert (
        store.prepare_repository_registration(approval, occurred_at=now)
        == "already_prepared"
    )
    repository = HomeAssistantRepository(
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
    assert (
        store.record_repository_registered(approval, repository, occurred_at=now)
        == "verified"
    )
    assert (
        store.record_repository_registered(approval, repository, occurred_at=now)
        == "already_verified"
    )
    assert [event.event_type for event in events] == [
        "ADVANCED_PLAN_CREATED",
        "ADVANCED_REPOSITORY_REGISTRATION_APPROVED",
        "ADVANCED_REPOSITORY_REGISTRATION_PREPARED",
        "ADVANCED_REPOSITORY_REGISTERED",
    ]
