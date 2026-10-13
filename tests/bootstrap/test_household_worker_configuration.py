"""The worker cannot silently activate household public work."""
import asyncio
from pathlib import Path
import pytest
from eva.bootstrap import delegated_work_service as service
from eva.bootstrap import delegated_research, household_work_composition

ENV = {"EVA_DATABASE_DSN_FILE": "/run/eva/dsn", "EVA_MODEL_ACCESS_LEASE_FILE": "/run/eva/lease",
       "EVA_RUNTIME_TENANT_ID": "11111111-1111-1111-1111-111111111111",
       "EVA_RUNTIME_INSTALLATION_ID": "22222222-2222-2222-2222-222222222222",
       "EVA_DELEGATED_WORKER_ID": "worker:test"}


def test_household_configuration_is_explicit_and_requires_research():
    assert service.OpenAIDelegatedWorkSettings.from_environment(ENV).household_work_config_file is None
    with pytest.raises(ValueError, match="requires public research"):
        service.OpenAIDelegatedWorkSettings.from_environment(ENV | {"EVA_HOUSEHOLD_WORK_CONFIG_FILE": "/run/eva/household"})
    with pytest.raises(ValueError, match="absolute configuration"):
        service.OpenAIDelegatedWorkSettings.from_environment(ENV | {
            "EVA_DELEGATED_RESEARCH_ENABLED": "true", "EVA_RUNTIME_RESOURCE_BUDGET_FILE": "/run/eva/budget",
            "EVA_HOUSEHOLD_WORK_CONFIG_FILE": "relative"})


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_research_worker_receives_only_explicit_household_authority(monkeypatch, enabled):
    settings = service.OpenAIDelegatedWorkSettings.from_environment(ENV | {
        "EVA_DELEGATED_RESEARCH_ENABLED": "true", "EVA_RUNTIME_RESOURCE_BUDGET_FILE": "/run/eva/budget",
        **({"EVA_HOUSEHOLD_WORK_CONFIG_FILE": "/run/eva/household"} if enabled else {})})
    research, profile, authority = object(), object(), object()
    loaded = []
    monkeypatch.setattr(service, "_research_composition", lambda value: (research, profile))
    def load(path, **kwargs):
        loaded.append(path)
        assert kwargs == dict(scope=settings.service.scope, profile=profile, model_lease_file=settings.model_access_lease_file)
        return authority
    monkeypatch.setattr(household_work_composition, "load_household_work_authority", load)
    async def serve(selected, actual_research, actual_profile, *, once, household_authority, digital_settings=None):
        assert selected is settings and actual_research is research and actual_profile is profile and once
        assert household_authority is (authority if enabled else None)
        return "completed"
    monkeypatch.setattr(delegated_research, "serve_research_enabled_work", serve)
    assert asyncio.run(service.serve_openai_delegated_work(settings, once=True)) == "completed"
    assert loaded == ([Path("/run/eva/household")] if enabled else [])
