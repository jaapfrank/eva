"""HA lifecycle callback regression with the HA platform boundary stubbed."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def test_disabling_option_unloads_previously_loaded_phone(monkeypatch):
    async def run():
        for name in ("homeassistant", "homeassistant.const", "homeassistant.helpers", "homeassistant.helpers.aiohttp_client"):
            monkeypatch.setitem(sys.modules, name, ModuleType(name))
        sys.modules["homeassistant.const"].Platform = SimpleNamespace(CONVERSATION="conversation")
        sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: None
        source=Path(__file__).parents[3]/"deploy/home-assistant/custom_components/eva2_voice"
        spec=importlib.util.spec_from_file_location("ha_phone_entry_test",source/"__init__.py",submodule_search_locations=[str(source)])
        module=importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules,spec.name,module);spec.loader.exec_module(module)
        events=[]
        class Client:
            def __init__(self,*args,**kwargs): pass
            async def close(self):events.append("household-close")
        monkeypatch.setattr(module,"EvaVoiceClient",Client)
        phone=ModuleType(spec.name+".phone")
        async def setup(hass,entry):hass.data["loaded-phone"]=entry.entry_id;events.append("phone-setup")
        async def unload(hass,entry):
            if hass.data.get("loaded-phone")==entry.entry_id:
                hass.data.pop("loaded-phone");events.append("phone-close")
        phone.setup_phone=setup;phone.unload_phone=unload
        monkeypatch.setitem(sys.modules,phone.__name__,phone)
        class Entries:
            async def async_forward_entry_setups(self,*args):pass
            async def async_unload_platforms(self,*args):return True
            async def async_reload(self,*args):pass
        entry=SimpleNamespace(entry_id="entry",options={"owner_voice_panel":True},
            data={"url":"http://127.0.0.1:44714","token":"household","satellite_ids":["speaker"]},
            async_on_unload=lambda callback:None,add_update_listener=lambda callback:callback)
        hass=SimpleNamespace(data={},config_entries=Entries())
        assert await module.async_setup_entry(hass,entry)
        entry.options={"owner_voice_panel":False}
        assert await module.async_unload_entry(hass,entry)
        assert "phone-close" in events and "loaded-phone" not in hass.data
        assert await module.async_setup_entry(hass,entry)
        assert events.count("phone-setup")==1
        await module.async_unload_entry(hass,entry)
    asyncio.run(run())


import pytest
import probatio as vol


@pytest.fixture
def ask_service(monkeypatch):
    """Load real service/schema code with only the HA host boundary stubbed."""
    names = ("homeassistant", "homeassistant.const", "homeassistant.helpers",
             "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.entity_registry",
             "homeassistant.config_entries", "homeassistant.core", "homeassistant.exceptions")
    for name in names:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    loaded, unloaded = object(), object()
    sys.modules["homeassistant.const"].Platform = SimpleNamespace(CONVERSATION="conversation")
    sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: None
    sys.modules["homeassistant.helpers.entity_registry"].async_get = lambda hass: hass.registry
    sys.modules["homeassistant.config_entries"].ConfigEntryState = SimpleNamespace(
        LOADED=loaded, NOT_LOADED=unloaded)
    sys.modules["homeassistant.core"].callback = lambda method: method
    sys.modules["homeassistant.core"].SupportsResponse = SimpleNamespace(OPTIONAL="optional")
    class HomeAssistantError(Exception):
        pass
    class ServiceValidationError(HomeAssistantError):
        pass
    sys.modules["homeassistant.exceptions"].HomeAssistantError = HomeAssistantError
    sys.modules["homeassistant.exceptions"].ServiceValidationError = ServiceValidationError
    source = Path(__file__).parents[3] / "deploy/home-assistant/custom_components/eva2_voice"
    spec = importlib.util.spec_from_file_location(
        "ha_ask_entry_test", source / "__init__.py", submodule_search_locations=[str(source)])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    # Keep imports scoped to this fixture, including real client exception identity.
    for suffix in ("services", "phone"):
        monkeypatch.delitem(sys.modules, spec.name + "." + suffix, raising=False)
    services_spec = importlib.util.spec_from_file_location(spec.name + ".services", source / "services.py")
    services = importlib.util.module_from_spec(services_spec)
    monkeypatch.setitem(sys.modules, services_spec.name, services)
    services_spec.loader.exec_module(services)
    phone = ModuleType(spec.name + ".phone")
    async def unload_phone(hass, entry):
        pass
    phone.unload_phone = unload_phone
    monkeypatch.setitem(sys.modules, phone.__name__, phone)

    class Client:
        def __init__(self):
            self.calls = []
            self.closed = False
        async def respond(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(text="Het opgeslagen antwoord.", continue_conversation=True)
        async def close(self):
            self.closed = True

    room, garden = "assist_satellite.room", "assist_satellite.garden"
    entries = [
        SimpleNamespace(entry_id="room", state=loaded, data={"satellite_ids": [room]}),
        SimpleNamespace(entry_id="garden", state=loaded, data={"satellite_ids": [garden]})]
    clients = {entry.entry_id: Client() for entry in entries}
    registry = {room: SimpleNamespace(device_id="a"*32, disabled_by=None),
                garden: SimpleNamespace(device_id="b"*32, disabled_by=None)}

    class Registry:
        def __init__(self):
            self.registrations = []
        def has_service(self, domain, name):
            return bool(self.registrations)
        def async_register(self, domain, name, handler, **kwargs):
            self.registrations.append((domain, name, handler, kwargs))
        async def invoke(self, data, return_response=True):
            _, _, handler, kwargs = self.registrations[0]
            return await handler(SimpleNamespace(data=kwargs["schema"](data),
                                                  return_response=return_response))
    async def unload_platforms(*args):
        return True
    hass = SimpleNamespace(data={module.DOMAIN: clients}, config=SimpleNamespace(language="nl"),
        registry=SimpleNamespace(async_get=registry.get), services=Registry(),
        config_entries=SimpleNamespace(async_entries=lambda domain: entries,
                                       async_unload_platforms=unload_platforms))
    asyncio.run(module.async_setup(hass, {}))
    return SimpleNamespace(hass=hass, module=module, services=services, clients=clients,
        entries=entries, registry=registry, room=room, garden=garden, unloaded=unloaded,
        validation_error=ServiceValidationError, error=HomeAssistantError)


def test_ask_returns_text_with_registry_device_and_generated_conversation(ask_service):
    fixture = ask_service
    result = asyncio.run(fixture.hass.services.invoke(
        {"satellite_id": fixture.room, "text": "Bekijk de toegestane situatie."}))
    assert set(result) == {"text", "conversation_id", "continue_conversation"}
    assert result["text"] == "Het opgeslagen antwoord."
    assert result["conversation_id"].startswith("automation:")
    assert fixture.clients["room"].calls == [{
        "satellite_id": fixture.room, "device_id": "a"*32,
        "conversation_id": result["conversation_id"],
        "text": "Bekijk de toegestane situatie.", "language": "nl", "interrupt_existing": False, "delivery_mode": "automation"}]
    assert not fixture.clients["garden"].calls
    assert fixture.hass.services.registrations[0][3]["supports_response"] == "optional"


def test_ask_reuses_requested_conversation_and_optional_response(ask_service):
    fixture = ask_service
    result = asyncio.run(fixture.hass.services.invoke({
        "satellite_id": fixture.room, "text": "En daarna?", "conversation_id": "known",
        "language": "en"}, return_response=False))
    assert result is None
    assert fixture.clients["room"].calls[0]["conversation_id"] == "known"
    assert fixture.clients["room"].calls[0]["language"] == "en"


@pytest.mark.parametrize("case", [
    "missing_entity", "disabled_entity", "missing_device", "unconfigured_satellite",
    "unloaded_entry", "missing_client", "multiple_entries",
])
def test_ask_requires_one_current_registered_client_before_dispatch(ask_service, case):
    f = ask_service
    data = {"satellite_id": f.room, "text": "Vraag"}
    if case == "missing_entity":
        f.registry.pop(f.room)
    elif case == "disabled_entity":
        f.registry[f.room].disabled_by = "user"
    elif case == "missing_device":
        f.registry[f.room].device_id = None
    elif case == "unconfigured_satellite":
        f.entries[0].data["satellite_ids"] = []
    elif case == "unloaded_entry":
        f.entries[0].state = f.unloaded
    elif case == "missing_client":
        f.hass.data[f.module.DOMAIN] = {}
    else:
        f.entries[1].data["satellite_ids"].append(f.room)
    with pytest.raises(f.validation_error):
        asyncio.run(f.hass.services.invoke(data))
    assert all(not client.calls for client in f.clients.values())


@pytest.mark.parametrize("extra", [
    {"device_id": "b"*32}, {"token": "never"}, {"principal_id": "private-owner"},
    {"satellite_id": "media_player.room"}, {"satellite_id": "assist_satellite." + "a"*200}, {"text": ""},
    {"text": "x"*16385}, {"conversation_id": "x"*201}, {"language": ""},
])
def test_ask_schema_rejects_authority_fields_and_invalid_input(ask_service, extra):
    f = ask_service
    with pytest.raises(vol.Invalid):
        asyncio.run(f.hass.services.invoke({"satellite_id": f.room, "text": "Vraag", **extra}))
    assert not f.clients["room"].calls


def test_ask_whitespace_question_fails_before_dispatch(ask_service):
    f = ask_service
    with pytest.raises(f.validation_error):
        asyncio.run(f.hass.services.invoke({"satellite_id": f.room, "text": " \n "}))
    assert not f.clients["room"].calls


@pytest.mark.parametrize("change", ["device", "disabled", "client"])
def test_ask_does_not_return_after_binding_changes(ask_service, change):
    f = ask_service
    client = f.clients["room"]
    async def respond(**kwargs):
        if change == "device":
            f.registry[f.room].device_id = "c"*32
        elif change == "disabled":
            f.registry[f.room].disabled_by = "user"
        else:
            f.clients["room"] = object()
        return SimpleNamespace(text="Niet vrijgeven", continue_conversation=False)
    client.respond = respond
    with pytest.raises(f.validation_error):
        asyncio.run(f.hass.services.invoke({"satellite_id": f.room, "text": "Vraag"}))


def test_ask_preserves_cancellation_instead_of_returning_an_error_answer(ask_service):
    f = ask_service
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def respond(**kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        f.clients["room"].respond = respond
        task = asyncio.create_task(f.hass.services.invoke({"satellite_id": f.room, "text": "Vraag"}))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    asyncio.run(run())


def test_ask_sanitizes_transport_failure_without_retry(ask_service):
    f = ask_service
    attempts = []
    async def respond(**kwargs):
        attempts.append(kwargs)
        raise f.services.EvaVoiceUnavailable("private transport detail")
    f.clients["room"].respond = respond
    with pytest.raises(f.error, match="niet bevestigen") as raised:
        asyncio.run(f.hass.services.invoke({"satellite_id": f.room, "text": "Vraag"}))
    assert "private" not in str(raised.value) and len(attempts) == 1


def test_ask_registration_survives_entry_unload_and_fails_clearly_without_entries(ask_service):
    f = ask_service
    async def run():
        assert await f.module.async_setup(f.hass, {})
        assert len(f.hass.services.registrations) == 1
        room_client = f.clients["room"]
        assert await f.module.async_unload_entry(f.hass, f.entries[0])
        assert room_client.closed
        with pytest.raises(f.validation_error):
            await f.hass.services.invoke({"satellite_id": f.room, "text": "Vraag"})
        assert (await f.hass.services.invoke(
            {"satellite_id": f.garden, "text": "Vraag"}))["text"]
        assert await f.module.async_unload_entry(f.hass, f.entries[1])
        assert f.hass.services.has_service(f.module.DOMAIN, "ask")
        with pytest.raises(f.validation_error):
            await f.hass.services.invoke({"satellite_id": f.garden, "text": "Vraag"})
    asyncio.run(run())



@pytest.mark.parametrize("already_active", [True, False])
def test_ask_busy_never_cancels_voice_including_playback_wait_race(ask_service, already_active):
    f = ask_service
    client_module = sys.modules[f.module.__name__ + ".client"]

    async def run():
        client = client_module.EvaVoiceClient(None, url="http://127.0.0.1:1", token="synthetic",
                                              satellite_ids=frozenset({f.room}))
        f.clients["room"] = client
        dispatches = []
        async def forbidden(*args, **kwargs):
            dispatches.append(args)
            raise AssertionError("Busy automation must not dispatch")
        client._respond = forbidden
        report_ready, release_report = asyncio.Event(), asyncio.Event()
        async def report():
            report_ready.set()
            await release_report.wait()
        report_task = asyncio.create_task(report())
        client._reports["a"*32] = {report_task}
        await report_ready.wait()
        voice = asyncio.create_task(asyncio.Event().wait())
        if already_active:
            client._active[f.room] = voice
        invocation = asyncio.create_task(
            f.hass.services.invoke({"satellite_id": f.room, "text": "Geplande vraag"}))
        await asyncio.sleep(0)
        if not already_active:
            assert not invocation.done()
            client._active[f.room] = voice
        release_report.set()
        try:
            with pytest.raises(f.validation_error, match="al met een aanvraag bezig"):
                await invocation
            assert client._active[f.room] is voice
            assert not voice.done() and not voice.cancelling()
            assert dispatches == []
        finally:
            await client.close()
            await asyncio.gather(voice, report_task, return_exceptions=True)
    asyncio.run(run())
