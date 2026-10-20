"""Optional provider lifecycle, ordinary chat and unchanged authority boundaries."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationTurn, IdentityProfile, LivingConversation
from eva.providers.contracts import ExternalProviderSettings, LoadedProvider, ProviderServices
from eva.providers.runtime import build_external_provider_bundle, build_provider_owner_media, build_provider_services, load_external_provider_settings
from eva.providers.home_assistant import HomeAssistantProviderFactory, HomeAssistantProviderSettings
from tests.application.test_conversation import _Memory, NOW
from tests.application.test_contact_model import Model


def clear_providers(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("EVA_HOME_ASSISTANT_", "EVA_AGENTMAIL_", "EVA_MCP_", "EVA_EXTERNAL_PROVIDER")):
            monkeypatch.delenv(key, raising=False)


def test_disabled_provider_does_not_read_credentials_or_expose_tools(monkeypatch):
    clear_providers(monkeypatch)
    monkeypatch.setenv("EVA_EXTERNAL_PROVIDERS_DISABLED", "home_assistant")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://ha.invalid")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN", "must-not-be-read")
    assert load_external_provider_settings() is None
    assert HomeAssistantProviderFactory.load_settings() is None
    assert build_external_provider_bundle(None, sa.create_engine("sqlite://")) is None
    assert build_provider_owner_media(None, object()) is None
    assert build_provider_services(None, object()) == ProviderServices()


@pytest.mark.parametrize("disabled", ["unknown_provider", "../module", "A"])
def test_invalid_disabled_provider_is_not_silently_accepted(monkeypatch, disabled):
    clear_providers(monkeypatch)
    monkeypatch.setenv("EVA_EXTERNAL_PROVIDERS_DISABLED", disabled)
    with pytest.raises(RuntimeError):
        load_external_provider_settings()


def test_optional_module_can_be_physically_absent_without_breaking_core_import_or_chat():
    program = r"""
import importlib.abc,sys
class Absent(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if 'home_assistant' in name:raise ModuleNotFoundError('optional absent',name=name)
sys.meta_path.insert(0,Absent())
from eva.bootstrap.runtime import RuntimeSettings
from eva.providers.runtime import load_external_provider_settings
assert load_external_provider_settings() is None
assert not any('home_assistant' in n for n in sys.modules)
from eva.application.conversation import IdentityProfile,LivingConversation,ConversationModelResult
from tests.application.test_conversation import _Memory,NOW
from eva.application.conversation import ConversationTurn
class Model:
 def generate(self,*args,**kwargs):
  assert not kwargs.get('available_actions')
  assert 'Home Assistant' not in repr(kwargs)
  return ConversationModelResult(text='Hoi! Fijn dat je er bent.',provider='fixture')
reply=LivingConversation(_Memory(),Model(),IdentityProfile('Eva','Huisgenoot.',('Wees eerlijk.',))).respond(ConversationTurn.private('tenant','installation','person','conversation','message','Hallo',NOW))
assert reply.text=='Hoi! Fijn dat je er bent.'
print('ordinary-chat-without-optional-module')
"""
    env = {k:v for k,v in os.environ.items() if not k.startswith("EVA_")}
    result = subprocess.run([sys.executable,"-c",program],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ordinary-chat-without-optional-module"


def test_enabled_provider_owns_guidance_only_for_current_authorized_actions():
    owner = hashlib.sha256(b"owner").hexdigest()
    settings = HomeAssistantProviderSettings("http://ha.invalid", "fixture", False, (), (owner,), ("light",), api_enabled=True)
    contribution = HomeAssistantProviderFactory.build(settings,sa.create_engine("sqlite://"))
    inventory = next(tool for tool in contribution.external_tools if "read_home_assistant_inventory" in tool.action_names)
    api = next(tool for tool in contribution.external_tools if "home_assistant_request" in tool.action_names)
    turn = ConversationTurn.private("tenant","installation","owner","conversation","message","Hoe warm is het?",NOW)
    assert not inventory.conversation_instructions(turn, ("unrelated_action",))
    assert "Home Assistant" in repr(inventory.conversation_instructions(turn, inventory.action_names))
    foreign = ConversationTurn.private("tenant","installation","other","conversation","message","Hoe warm?",NOW)
    assert inventory.conversation_instructions(foreign, inventory.action_names) == ()
    receipt = ConversationActionReceipt(ConversationActionCall("call","home_assistant_request",{}),{"status":"verified"})
    assert api.contact_reply_allowed(turn,receipt) is True
    with pytest.raises(Exception):
        api.contact_reply_allowed(foreign,receipt)
    assert set(contribution.digital_action_names) == {"read_home_assistant_inventory","home_assistant_request"}
    # Disabling/rebuilding changes composition, not durable tasks or old receipts.
    old = dict(receipt.output)
    assert build_external_provider_bundle(None,sa.create_engine("sqlite://")) is None
    rebuilt = HomeAssistantProviderFactory.build(settings,sa.create_engine("sqlite://"))
    assert tuple(t.name for t in rebuilt.conversation_tools) == tuple(t.name for t in contribution.conversation_tools)
    assert receipt.output == old


def test_other_provider_services_work_without_optional_house_module():
    class Factory:
        @staticmethod
        def build_services(settings, context):
            return ProviderServices((lambda: settings,), False)
        @staticmethod
        def build_owner_media(settings, runtime_settings, **identity):
            return settings
    settings = ExternalProviderSettings((LoadedProvider("calendar",Factory(),"calendar-settings"),))
    assert build_provider_services(settings,object()).tasks[0]() == "calendar-settings"
    assert build_provider_owner_media(settings,object()) == "calendar-settings"


def test_existing_delivery_adapter_is_composed_once_without_sending():
    settings = HomeAssistantProviderSettings("http://ha.invalid","fixture",False,(),(),("light",))
    context = SimpleNamespace(work_server=object(), shared_output=None,principal_id=None,budget=None)
    result = HomeAssistantProviderFactory.build_services(settings,context)
    assert len(result.tasks)==1 and not result.owns_sensory_decisions
    adapter=result.tasks[0].__self__
    assert adapter.server is context.work_server and adapter.provider is settings


def test_generic_core_and_model_do_not_contain_house_specific_rules():
    root=Path(__file__).resolve().parents[3]
    for relative in ["src/eva/application/conversation.py","src/eva/adapters/model/pydantic_conversation.py","src/eva/adapters/model/tool_namespaces.py","src/eva/providers/runtime.py","src/eva/bootstrap/runtime.py"]:
        assert "home_assistant" not in (root/relative).read_text().casefold(), relative


def test_disabled_module_is_not_imported_by_other_tool_presentation():
    program = r"""
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if 'home_assistant' in name:raise AssertionError('disabled module imported')
sys.meta_path.insert(0,Block())
from eva.adapters.model.tool_namespaces import function_defer_loading,deferred_namespaces
assert function_defer_loading('search_public_info') is False
result,mapping=deferred_namespaces([{'type':'function','name':'calendar_read','description':'Read calendar','parameters':{}}])
assert mapping=={'calendar_read':'calendar_read'}
print('other-tool-without-disabled-module')
"""
    env = {k:v for k,v in os.environ.items() if not k.startswith("EVA_")}
    env["EVA_EXTERNAL_PROVIDERS_DISABLED"]="home_assistant"
    result=subprocess.run([sys.executable,"-c",program],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()=="other-tool-without-disabled-module"
