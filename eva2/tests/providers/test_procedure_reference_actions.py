import pytest
from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory, ProcedureReferenceActions
from eva.providers.bundled import bundled_provider_factories


def test_opt_in_is_explicit_and_does_not_inspect_gateway_when_disabled(monkeypatch):
    monkeypatch.delenv('EVA_PROCEDURE_REFERENCES_ENABLED',raising=False)
    monkeypatch.setenv('EVA_MCP_GATEWAY_TOKEN','must-not-be-read')
    assert ProcedureReferenceProviderFactory.load_settings() is None
    assert sum(f.provider_id=='procedure_references' for f in bundled_provider_factories())==1


def test_invalid_opt_in_fails_closed(monkeypatch):
    monkeypatch.setenv('EVA_PROCEDURE_REFERENCES_ENABLED','maybe')
    with pytest.raises(RuntimeError):ProcedureReferenceProviderFactory.load_settings()


def test_tools_are_closed_without_owner_or_path_overrides():
    descriptors=ProcedureReferenceActions.conversation_tools()
    assert len(descriptors)==3
    for descriptor in descriptors:
        assert descriptor.input_schema['additionalProperties'] is False
        assert not {'owner','tenant','path','content','proof'} & set(descriptor.input_schema['properties'])
