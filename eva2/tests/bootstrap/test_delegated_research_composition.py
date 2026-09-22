"""Provider-free checks for identical core/worker research composition."""
import asyncio
from dataclasses import replace
from pathlib import Path
import urllib.request

import pytest
from eva.bootstrap import delegated_work_service as service
from eva.bootstrap import delegated_research as research_module
from eva.bootstrap.delegated_work_service import OpenAIDelegatedWorkSettings
from eva.bootstrap.delegated_research import build_public_research_composition
from eva.contracts.runtime_budget import RuntimeResourceBudgetError
from tests.model.test_pydantic_conversation_model import _runtime_budget

ENV = {"EVA_DATABASE_DSN_FILE":"/run/eva/database-dsn",
    "EVA_MODEL_ACCESS_LEASE_FILE":"/run/eva/model-lease/auth.json",
    "EVA_RUNTIME_TENANT_ID":"tenant:test", "EVA_RUNTIME_INSTALLATION_ID":"installation:test",
    "EVA_DELEGATED_WORKER_ID":"worker:test", "EVA_RUNTIME_RESOURCE_BUDGET_FILE":"/run/eva/budget.json"}

@pytest.mark.parametrize('value',['TRUE','1','yes',' false','', 'False'])
def test_research_switch_rejects_non_boolean_strings(value):
    with pytest.raises(RuntimeError):
        OpenAIDelegatedWorkSettings.from_environment(ENV | {'EVA_DELEGATED_RESEARCH_ENABLED':value})


def test_default_worker_does_not_require_a_research_budget():
    settings=OpenAIDelegatedWorkSettings.from_environment({k:v for k,v in ENV.items() if k!='EVA_RUNTIME_RESOURCE_BUDGET_FILE'})
    assert settings.research_enabled is False and settings.research_budget_file is None
    with pytest.raises(RuntimeError):
        OpenAIDelegatedWorkSettings.from_environment({k:v for k,v in ENV.items() if k!='EVA_RUNTIME_RESOURCE_BUDGET_FILE'} | {'EVA_DELEGATED_RESEARCH_ENABLED':'true'})


def fixtures(monkeypatch):
    budget=_runtime_budget(max_input_tokens=65536)
    monkeypatch.setattr('eva.bootstrap.delegated_work_provider.validate_delegated_model_access_lease',lambda path:None)
    monkeypatch.setattr('eva.adapters.credentials.runtime_files.read_private_runtime_file_bytes',lambda path,**kw:budget.to_bytes())
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:pytest.fail('composition cannot contact provider'))
    return budget


def test_core_and_worker_factories_have_identical_release_and_budget(monkeypatch):
    budget=fixtures(monkeypatch)
    settings=OpenAIDelegatedWorkSettings.from_environment(ENV | {'EVA_DELEGATED_RESEARCH_ENABLED':'true'})
    core_model,core_profile=build_public_research_composition(model_access_lease_file=settings.model_access_lease_file,resource_budget=budget)
    worker_model,worker_profile=service._research_composition(settings)
    assert core_profile==worker_profile and core_profile.release==worker_profile.release
    assert core_model.research_configuration_sha256()==worker_model.research_configuration_sha256()
    assert worker_model._resource_budget==budget
    assert worker_model._model=='gpt-5.6-luna' and worker_model._reasoning_effort=='low'
    assert worker_model._research_timeout_seconds==60 and worker_profile.maximum_tool_calls==3
    assert worker_profile.maximum_input_tokens==65536 and worker_profile.maximum_output_tokens==1200
    for field in ['research_tenant_id','research_installation_id']:
        with pytest.raises(RuntimeResourceBudgetError):
            service._research_composition(replace(settings,**{field:'other'}))


def test_enabled_entrypoint_reuses_worker_service_composition(monkeypatch):
    fixtures(monkeypatch)
    settings=OpenAIDelegatedWorkSettings.from_environment(ENV | {'EVA_DELEGATED_RESEARCH_ENABLED':'true'})
    observed=[]
    async def composed(actual,model,profile,*,once):
        observed.append((actual,profile,once)); return 'completed'
    monkeypatch.setattr(research_module,'serve_research_enabled_work',composed)
    assert asyncio.run(service.serve_openai_delegated_work(settings,once=True))=='completed'
    assert len(observed)==1 and observed[0][0]==settings and observed[0][2] is True


def test_check_validates_research_config_without_provider_or_worker_run(monkeypatch):
    fixtures(monkeypatch)
    settings=OpenAIDelegatedWorkSettings.from_environment(ENV | {'EVA_DELEGATED_RESEARCH_ENABLED':'true'})
    checked=[]
    original=service._research_composition
    def capture(value): checked.append(original(value)[1].release)
    monkeypatch.setattr(service,'_research_composition',capture)
    monkeypatch.setattr('eva.bootstrap.delegated_work_provider.verify_delegated_provider_dependencies',lambda:None)
    monkeypatch.setattr(service,'read_runtime_secret_file',lambda *a,**kw:'postgresql://unused/test')
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def scalar(self,statement): return '4e_0007_delegated_private'
    class Engine:
        def connect(self): return Connection()
        def dispose(self): pass
    monkeypatch.setattr(service.sa,'create_engine',lambda *a,**kw:Engine())
    assert service.check_openai_delegated_work(settings) is None
    assert len(checked)==1
