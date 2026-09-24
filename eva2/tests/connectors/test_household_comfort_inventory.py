"""Household device context remains useful without exposing private entity state."""
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter


def test_inventory_keeps_room_device_category_and_bounded_comfort_state():
    projected = HomeAssistantAdminAdapter._project_household(
        [{"area_id": "lounge", "name": "Woonkamer"}],
        [{"id": "speaker", "area_id": "lounge"}],
        [{"entity_id": "light.ring", "device_id": "speaker", "entity_category": "config"}],
        [{"entity_id": "light.ring", "state": "on", "attributes": {"friendly_name": "Voice LED"}},
         {"entity_id": "sensor.temperature", "state": "17.5", "attributes": {
             "device_class": "temperature", "unit_of_measurement": "°C", "private_note": "omit"}},
         {"entity_id": "climate.lounge", "state": "heat", "attributes": {
             "current_temperature": 17.5, "temperature": 20, "hvac_action": "heating", "token": "omit"}},
         {"entity_id": "person.owner", "state": "private-location", "attributes": {}}])
    rows = {row["entity_id"]: row for row in projected["entities"]}
    assert rows["light.ring"]["area_id"] == "lounge"
    assert rows["light.ring"]["entity_category"] == "config"
    assert rows["sensor.temperature"]["state"] == "17.5"
    assert rows["sensor.temperature"]["attributes"] == {"unit_of_measurement": "°C"}
    assert rows["climate.lounge"]["attributes"] == {
        "current_temperature": 17.5, "temperature": 20, "hvac_action": "heating"}
    assert "state" not in rows["person.owner"]
    assert "attributes" not in rows["person.owner"]


def test_registered_shared_speaker_can_discover_rooms_but_not_automation_details():
    import asyncio
    import hashlib
    import pytest
    import sqlalchemy as sa
    from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationConflict
    from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference
    from eva.providers.home_assistant import HomeAssistantProviderFactory, HomeAssistantProviderSettings
    from eva.adapters.connectors.home_assistant_admin import HomeAssistantHouseholdTool
    actor = "speaker:lounge"
    audience = MemoryAudience.household(principal_id=actor, household_id="home",
        consent_ref=HouseholdConsentReference(actor, "home", "household-consent:test"))
    turn = ConversationTurn("tenant", "install", actor, "household:home:conversation:test",
        "household:home:conversation:test:1", "Het is koud", "2026-09-24T12:00:00Z", audience)
    engine = sa.create_engine("postgresql+psycopg://unused@127.0.0.1/unused")  # No connection is opened.
    try:
        contribution = HomeAssistantProviderFactory.build(HomeAssistantProviderSettings(
            "http://ha.invalid", "fixture", False, (),
            (hashlib.sha256(actor.encode()).hexdigest(),), ("light",)), engine)
        tool = next(item for item in contribution.external_tools if "read_home_assistant_inventory" in item.action_names)
        assert tool.available_for(turn)
        with pytest.raises(ConversationConflict, match="rooms and devices"):
            asyncio.run(HomeAssistantHouseholdTool(None).prepare(turn,
                ConversationActionCall("one", "read_home_assistant_inventory", {"section": "automation_details"})))
    finally:
        engine.dispose()
