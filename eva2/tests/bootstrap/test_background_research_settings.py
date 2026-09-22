"""Research admission cannot be enabled without its existing owner worker and budget."""
import pytest
from eva.bootstrap.runtime import RuntimeSettings
from tests.bootstrap.test_runtime_settings import _minimum


def test_research_is_disabled_by_default_and_requires_worker_budget():
    assert RuntimeSettings("unused", "http://memory").delegated_research_enabled is False
    with pytest.raises(ValueError, match="research requires"):
        RuntimeSettings("unused", "http://memory", delegated_research_enabled=True)
    with pytest.raises(ValueError, match="research switch"):
        RuntimeSettings("unused", "http://memory", delegated_research_enabled="true")


def test_research_environment_is_strict_and_requires_owner_worker(monkeypatch, private_tmp_path):
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_DELEGATED_RESEARCH_ENABLED", "invalid")
    with pytest.raises(RuntimeError):
        RuntimeSettings.from_environment()
    monkeypatch.setenv("EVA_DELEGATED_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("EVA_DELEGATED_WORK_ENABLED", "false")
    with pytest.raises(ValueError, match="research requires"):
        RuntimeSettings.from_environment()
