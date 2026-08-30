from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from eva.adapters.connectors.home_assistant_advanced_tasks import (
    AdvancedTaskBlocked,
    AdvancedTaskCanaryProof,
    HomeAssistantDevice,
    HomeAssistantEntity,
    HomeAssistantIntegration,
    HomeAssistantInventory,
    HomeAssistantRepository,
    TuyaToLocalTuyaPlanner,
    advanced_plan_id,
    adopt_device_preflight,
    approve_canary,
    approve_localtuya_package_install,
    approve_localtuya_repository_registration,
    require_current_package_preflight,
    verify_localtuya_canary_readback,
)


def test_tuya_migration_is_per_device_and_blocked_before_any_mutation() -> None:
    inventory = HomeAssistantInventory(
        (
            HomeAssistantIntegration("localtuya", "local", "not_loaded", "localtuya"),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (
            HomeAssistantDevice(
                "device-light", "Schuur 1", "Tuya", "Light Strip", ("cloud",)
            ),
            HomeAssistantDevice(
                "device-matter", "Heater", "Tuya", "Smart Plug Matter", ("cloud",)
            ),
            HomeAssistantDevice(
                "device-scene", "Kleur", "Tuya", "Tuya Scene", ("cloud",)
            ),
        ),
        (
            HomeAssistantEntity(
                "light.schuur_1", "tuya", "cloud", "device-light", None
            ),
            HomeAssistantEntity(
                "switch.heater", "tuya", "cloud", "device-matter", None
            ),
            HomeAssistantEntity("scene.kleur", "tuya", "cloud", "device-scene", None),
        ),
        datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "249381778",
                "rospogrigio/localtuya",
                "localtuya",
                False,
                None,
                "v5.2.5",
                True,
            ),
        ),
        True,
        "2025.12.4",
    )

    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:tuya-plan", requested_by="jaap"
    )

    assert plan.state == "blocked_preflight"
    assert "localtuya_integration_not_loaded" in plan.blockers
    assert "local_device_credentials_not_collected" in plan.blockers
    assert "localtuya_package_not_installed" in plan.blockers
    assert [item.route for item in plan.devices] == [
        "localtuya_candidate",
        "matter_review",
        "rebuild_scene",
    ]
    assert plan.steps[0].risk == "read_only"
    assert (
        next(
            step for step in plan.steps if step.step_id == "install_localtuya_package"
        ).requires_approval
        is True
    )
    assert (
        next(
            step for step in plan.steps if step.step_id == "approve_canary"
        ).requires_approval
        is True
    )
    assert all(step.rollback for step in plan.steps if step.risk != "read_only")
    assert len(plan.sha256) == 64
    assert plan.to_payload()["plan_sha256"] == plan.sha256
    assert type(plan).from_payload(plan.to_payload()) == plan

    with pytest.raises(AdvancedTaskBlocked, match="preflight blockers"):
        approve_canary(
            plan,
            device_id="device-light",
            approved_by="jaap",
            approved_at=datetime(2026, 8, 23, 15, 1, tzinfo=UTC),
        )

    package_approval = approve_localtuya_package_install(
        plan,
        repository_id="249381778",
        version="v5.2.5",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 23, 15, 1, tzinfo=UTC),
    )
    assert package_approval.repository_sha256 == inventory.repositories[0].sha256
    assert package_approval.plan_sha256 == plan.sha256
    assert package_approval.to_payload()["step_id"] == "install_localtuya_package"


def test_known_localtuya_release_incompatibility_blocks_package_install() -> None:
    inventory = HomeAssistantInventory(
        (
            HomeAssistantIntegration("localtuya", "local", "not_loaded", "LocalTuya"),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (HomeAssistantDevice("device-light", "Schuur", "Tuya", "Lamp", ("cloud",)),),
        (HomeAssistantEntity("light.schuur", "tuya", "cloud", "device-light", None),),
        datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "249381778",
                "rospogrigio/localtuya",
                "localtuya",
                False,
                None,
                "v5.2.5",
                True,
            ),
        ),
        True,
        "2026.8.1",
    )

    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:incompatible", requested_by="jaap"
    )

    assert "localtuya_package_incompatible_with_home_assistant" in plan.blockers
    with pytest.raises(AdvancedTaskBlocked, match="incompatible"):
        approve_localtuya_package_install(
            plan,
            repository_id="249381778",
            version="v5.2.5",
            approved_by="jaap",
            approved_at=datetime(2026, 8, 23, 15, 1, tzinfo=UTC),
        )


def test_maintained_localtuya_release_is_source_and_ha_floor_bound() -> None:
    inventory = HomeAssistantInventory(
        (
            HomeAssistantIntegration("localtuya", "local", "not_loaded", "LocalTuya"),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (HomeAssistantDevice("device-light", "Schuur", "Tuya", "Lamp", ("cloud",)),),
        (HomeAssistantEntity("light.schuur", "tuya", "cloud", "device-light", None),),
        datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "old",
                "rospogrigio/localtuya",
                "localtuya",
                False,
                None,
                "v5.2.5",
                True,
                "2024.4.0",
            ),
            HomeAssistantRepository(
                "maintained",
                "xZetsubou/hass-localtuya",
                "localtuya",
                False,
                None,
                "2026.7.0",
                True,
                "2025.1.0",
                True,
            ),
        ),
        True,
        "2026.8.1",
    )

    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:maintained", requested_by="jaap"
    )

    assert "localtuya_package_incompatible_with_home_assistant" not in plan.blockers
    assert "localtuya_maintained_repository_not_available" not in plan.blockers
    assert "localtuya_package_not_installed" in plan.blockers
    with pytest.raises(AdvancedTaskBlocked, match="maintained"):
        approve_localtuya_package_install(
            plan,
            repository_id="old",
            version="v5.2.5",
            approved_by="jaap",
            approved_at=datetime(2026, 8, 24, 6, 1, tzinfo=UTC),
        )
    approval = approve_localtuya_package_install(
        plan,
        repository_id="maintained",
        version="2026.7.0",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 24, 6, 1, tzinfo=UTC),
    )
    assert approval.repository_id == "maintained"
    assert HomeAssistantInventory.from_payload(inventory.to_payload()) == inventory


def test_maintained_localtuya_release_below_declared_ha_floor_fails_closed() -> None:
    inventory = HomeAssistantInventory(
        (),
        (),
        (),
        datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "future",
                "xZetsubou/hass-localtuya",
                "localtuya",
                False,
                None,
                "2026.7.0",
                True,
                "2026.9.0",
                True,
            ),
        ),
        True,
        "2026.8.1",
    )

    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:future-floor", requested_by="jaap"
    )

    assert "localtuya_package_incompatible_with_home_assistant" in plan.blockers


def test_maintained_repository_registration_approval_is_exact_and_short_lived() -> None:
    inventory = HomeAssistantInventory(
        (),
        (),
        (),
        datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "old",
                "rospogrigio/localtuya",
                "localtuya",
                False,
                None,
                "v5.2.5",
                True,
                "2024.1.0",
            ),
        ),
        True,
        "2026.8.1",
    )
    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:register-maintained", requested_by="jaap"
    )

    approval = approve_localtuya_repository_registration(
        plan,
        repository_full_name="xZetsubou/hass-localtuya",
        expected_version="2026.7.0",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 24, 6, 1, tzinfo=UTC),
    )

    assert approval.plan_sha256 == plan.sha256
    assert approval.inventory_sha256 == plan.inventory_sha256
    assert approval.expires_at.isoformat() == "2026-08-24T06:16:00+00:00"
    assert type(approval).from_payload(approval.to_payload()) == approval
    with pytest.raises(AdvancedTaskBlocked, match="maintained source"):
        approve_localtuya_repository_registration(
            plan,
            repository_full_name="rospogrigio/localtuya",
            expected_version="2026.7.0",
            approved_by="jaap",
            approved_at=datetime(2026, 8, 24, 6, 1, tzinfo=UTC),
        )
    with pytest.raises(AdvancedTaskBlocked, match="approved release"):
        approve_localtuya_repository_registration(
            plan,
            repository_full_name="xZetsubou/hass-localtuya",
            expected_version="2026.8.0",
            approved_by="jaap",
            approved_at=datetime(2026, 8, 24, 6, 1, tzinfo=UTC),
        )


def test_ready_plan_approval_is_short_lived_and_bound_to_one_canary() -> None:
    inventory = HomeAssistantInventory(
        (
            HomeAssistantIntegration("localtuya", "local", "loaded", "localtuya"),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (
            HomeAssistantDevice(
                "device-light", "Schuur 1", "Tuya", "Light Strip", ("cloud",)
            ),
        ),
        (HomeAssistantEntity("light.schuur_1", "tuya", "cloud", "device-light", None),),
        datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
    )
    blocked = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:ready", requested_by="jaap"
    )
    plan = type(blocked)(
        blocked.plan_id,
        blocked.operation_id,
        blocked.task,
        blocked.inventory_sha256,
        "awaiting_approval",
        (),
        blocked.devices,
        blocked.steps,
        blocked.requested_by,
        blocked.created_at,
    )

    approval = approve_canary(
        plan,
        device_id="device-light",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 23, 15, 1, tzinfo=UTC),
    )

    assert approval.plan_sha256 == plan.sha256
    assert approval.inventory_sha256 == inventory.sha256
    assert approval.device_id == "device-light"
    assert approval.expires_at.isoformat() == "2026-08-23T15:16:00+00:00"
    assert len(approval.sha256) == 64


def test_plan_identity_is_retry_stable_and_inventory_is_not_silently_replaced() -> None:
    assert advanced_plan_id(
        operation_id="operation:retry", requested_by="jaap"
    ) == advanced_plan_id(operation_id="operation:retry", requested_by="jaap")
    assert advanced_plan_id(
        operation_id="operation:retry", requested_by="jaap"
    ) != advanced_plan_id(operation_id="operation:other", requested_by="jaap")


def test_package_preflight_rechecks_drift_and_hidden_localtuya_devices() -> None:
    inventory = HomeAssistantInventory(
        (
            HomeAssistantIntegration("localtuya", "local", "not_loaded", "localtuya"),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (HomeAssistantDevice("device-light", "Schuur", "Tuya", "Lamp", ("cloud",)),),
        (HomeAssistantEntity("light.schuur", "tuya", "cloud", "device-light", None),),
        datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "249381778",
                "rospogrigio/localtuya",
                "localtuya",
                False,
                None,
                "v5.2.5",
                True,
            ),
        ),
        True,
        "2025.12.4",
    )
    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:preflight", requested_by="jaap"
    )

    require_current_package_preflight(
        plan,
        replace(inventory, captured_at=datetime(2026, 8, 23, 15, 2, tzinfo=UTC)),
    )

    renamed = replace(
        inventory,
        devices=(replace(inventory.devices[0], name="Andere schuur"),),
    )
    with pytest.raises(AdvancedTaskBlocked, match="inventory changed"):
        require_current_package_preflight(plan, renamed)

    hidden_local = replace(
        inventory,
        devices=inventory.devices
        + (HomeAssistantDevice("old-local", "Oud lokaal", None, None, ("local",)),),
        entities=inventory.entities
        + (
            HomeAssistantEntity(
                "switch.oud_lokaal", "localtuya", "local", "old-local", None
            ),
        ),
    )
    with pytest.raises(AdvancedTaskBlocked, match="revive existing device"):
        require_current_package_preflight(plan, hidden_local)


def test_verified_device_preflight_unblocks_only_its_canary() -> None:
    inventory = HomeAssistantInventory(
        (
            HomeAssistantIntegration("localtuya", "local", "loaded", "localtuya"),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (
            HomeAssistantDevice(
                "device-light", "Schuur 1", "Tuya", "Light Strip", ("cloud",)
            ),
            HomeAssistantDevice(
                "device-matter", "Heater", "Tuya", "Smart Plug Matter", ("cloud",)
            ),
            HomeAssistantDevice(
                "device-scene", "Kleur", "Tuya", "Tuya Scene", ("cloud",)
            ),
        ),
        (
            HomeAssistantEntity(
                "light.schuur_1", "tuya", "cloud", "device-light", None
            ),
            HomeAssistantEntity(
                "switch.heater", "tuya", "cloud", "device-matter", None
            ),
            HomeAssistantEntity("scene.kleur", "tuya", "cloud", "device-scene", None),
        ),
        datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
        (
            HomeAssistantRepository(
                "249381778",
                "rospogrigio/localtuya",
                "localtuya",
                True,
                "v5.2.5",
                "v5.2.5",
                True,
            ),
        ),
        True,
        "2025.12.4",
    )
    plan = TuyaToLocalTuyaPlanner().build(
        inventory, operation_id="operation:canary", requested_by="jaap"
    )
    assert set(plan.blockers) == {
        "local_device_credentials_not_collected",
        "matter_devices_require_native_matter_review",
        "tuya_scenes_require_explicit_rebuild",
    }
    preflight = adopt_device_preflight(
        plan,
        device_id="device-light",
        config_entry_id="01K3XJR7BTERG1Z20Q1J2HYP0B",
        local_device_id_sha256="a" * 64,
        local_host_sha256="b" * 64,
        protocol_version="3.3",
        next_schema_sha256="c" * 64,
        probed_at=datetime(2026, 8, 23, 15, 1, tzinfo=UTC),
    )

    approval = approve_canary(
        plan,
        device_id="device-light",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 23, 15, 2, tzinfo=UTC),
        preflight=preflight,
    )

    assert approval.device_id == "device-light"
    assert type(preflight).from_payload(preflight.to_payload()) == preflight
    assert "local_key" not in preflight.to_payload()
    with pytest.raises(AdvancedTaskBlocked, match="preflight blockers"):
        approve_canary(
            plan,
            device_id="device-light",
            approved_by="jaap",
            approved_at=datetime(2026, 8, 23, 15, 17, tzinfo=UTC),
            preflight=preflight,
        )


def test_canary_succeeds_only_from_independent_registry_readback_with_cloud_intact() -> (
    None
):
    before = HomeAssistantInventory(
        (
            HomeAssistantIntegration(
                "localtuya", "01K3XJR7BTERG1Z20Q1J2HYP0B", "loaded", "LocalTuya"
            ),
            HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),
        ),
        (
            HomeAssistantDevice(
                "device-light", "Schuur 1", "Tuya", "Light Strip", ("cloud",)
            ),
        ),
        (HomeAssistantEntity("light.schuur_1", "tuya", "cloud", "device-light", None),),
        datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
    )
    plan = TuyaToLocalTuyaPlanner().build(
        before, operation_id="operation:canary-readback", requested_by="jaap"
    )
    assert HomeAssistantInventory.from_payload(before.to_payload()) == before
    preflight = adopt_device_preflight(
        plan,
        device_id="device-light",
        config_entry_id="01K3XJR7BTERG1Z20Q1J2HYP0B",
        local_device_id_sha256="a" * 64,
        local_host_sha256="b" * 64,
        protocol_version="3.3",
        next_schema_sha256="c" * 64,
        probed_at=datetime(2026, 8, 23, 15, 1, tzinfo=UTC),
    )
    approval = approve_canary(
        plan,
        device_id="device-light",
        approved_by="jaap",
        approved_at=datetime(2026, 8, 23, 15, 2, tzinfo=UTC),
        preflight=preflight,
    )
    local_device = HomeAssistantDevice(
        "device-light-local",
        "Schuur 1 lokaal",
        "Tuya",
        "Light Strip",
        (preflight.config_entry_id,),
    )
    local_entity = HomeAssistantEntity(
        "light.schuur_1_lokaal",
        "localtuya",
        preflight.config_entry_id,
        local_device.device_id,
        None,
    )
    after = replace(
        before,
        devices=before.devices + (local_device,),
        entities=before.entities + (local_entity,),
        captured_at=datetime(2026, 8, 23, 15, 3, tzinfo=UTC),
    )

    proof = verify_localtuya_canary_readback(
        plan,
        approval,
        preflight,
        configuration_sha256="d" * 64,
        before=before,
        after=after,
        observed_at=datetime(2026, 8, 23, 15, 3, tzinfo=UTC),
    )

    assert proof.source_cloud_still_present is True
    assert proof.source_tuya_entity_ids == ("light.schuur_1",)
    assert proof.localtuya_entity_ids == ("light.schuur_1_lokaal",)
    assert AdvancedTaskCanaryProof.from_payload(proof.to_payload()) == proof

    without_source = replace(
        after,
        entities=(local_entity,),
        captured_at=datetime(2026, 8, 23, 15, 4, tzinfo=UTC),
    )
    with pytest.raises(AdvancedTaskBlocked, match="source Tuya entities changed"):
        verify_localtuya_canary_readback(
            plan,
            approval,
            preflight,
            configuration_sha256="d" * 64,
            before=before,
            after=without_source,
            observed_at=datetime(2026, 8, 23, 15, 4, tzinfo=UTC),
        )

    second_local = HomeAssistantDevice(
        "device-light-local-2",
        "Onverwacht lokaal apparaat",
        "Tuya",
        "Light Strip",
        (preflight.config_entry_id,),
    )
    with pytest.raises(AdvancedTaskBlocked, match="exactly one"):
        verify_localtuya_canary_readback(
            plan,
            approval,
            preflight,
            configuration_sha256="d" * 64,
            before=before,
            after=replace(after, devices=after.devices + (second_local,)),
            observed_at=datetime(2026, 8, 23, 15, 4, tzinfo=UTC),
        )
