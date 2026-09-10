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
        source=Path(__file__).parents[2]/"deploy/home-assistant/custom_components/eva2_voice"
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
