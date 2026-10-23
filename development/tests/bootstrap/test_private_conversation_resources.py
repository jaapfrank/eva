"""Shared resource composition retains current identity and memory boundaries."""
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import eva.bootstrap.private_conversation_resources as resources


def settings():
    return SimpleNamespace(
        dashboard_principal_binding_id="00000000-0000-0000-0000-000000000001",
        telegram_principal_bindings_file="/private/bindings.json",
        background_tenant_id="tenant", background_installation_id="installation",
        background_principal_id="00000000-0000-0000-0000-000000000002",
        dashboard_tenant_id="00000000-0000-0000-0000-000000000003",
        dashboard_installation_id="00000000-0000-0000-0000-000000000004",
        hindsight_url="http://127.0.0.1:8888", hindsight_bank_namespace="eva2",
        hindsight_recall_budget="low", hindsight_recall_max_tokens=2048,
        hindsight_recall_node_budget=40, identity_name="Eva", identity_description="Helpful",
        identity_pack_file="/private/identity.json", identity_pack_trust_file="/private/trust.json",
    )


def test_shared_resources_preserve_consent_and_reread_private_binding(monkeypatch):
    configured = settings()
    seen = {}
    current = [object()]
    monkeypatch.setattr(resources, "PostgresPrivateMemoryAliases",
        lambda engine, **kwargs: seen.update(engine=engine, alias=kwargs) or "aliases")
    monkeypatch.setattr(resources, "load_principal_bindings", lambda path: current[0])
    monkeypatch.setattr(resources, "HindsightMemoryEngine",
        lambda config, **kwargs: seen.update(config=config, memory=kwargs) or "memory")
    engine, group, household = object(), object(), object()
    memory, identity = resources.build_private_conversation_resources(configured, engine,
        group_consent=group, household_voice=household, identity_pack=None)
    assert memory == "memory" and seen["engine"] is engine
    assert seen["memory"]["active_group_consent_resolver"] is group
    assert seen["memory"]["active_household_consent_resolver"] is household
    assert seen["memory"]["private_alias_resolver"] == "aliases"
    source_lookup = seen["memory"]["private_source_resolver"]
    source_reads = []
    class SourceReader:
        def __init__(self, selected_engine, **kwargs):
            source_reads.append((selected_engine, kwargs))
        def selected_private_sources(self, query, refs, *, now):
            return (query, refs, now)
    monkeypatch.setattr(resources, "PostgresOwnerMemorySources", SourceReader)
    for _ in range(2):
        current[0] = object()
        returned = source_lookup("query", (("owner", "message", "evidence"),))
        assert returned[:2] == ("query", (("owner", "message", "evidence"),))
        assert source_reads[-1][1]["telegram_bindings"] is current[0]
        assert source_reads[-1][0] is engine
    assert seen["alias"]["owner"] == UUID(configured.background_principal_id)
    assert seen["alias"]["binding"] == UUID(configured.dashboard_principal_binding_id)
    assert seen["alias"]["authority_scope"].tenant_id == UUID(configured.dashboard_tenant_id)
    load = seen["alias"]["bindings_loader"]
    assert load() is current[0]
    current[0] = object()
    assert load() is current[0]
    assert identity.name == "Eva" and identity.maximum_reply_words == 110
    assert identity.principles[:4] == (
        "Zeg eerlijk wat je wel en niet weet.",
        "Reageer als één persoon, niet als een verzameling scripts.",
        "Beweer een handeling pas na onafhankelijk bewijs.",
        "Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen.",
    )


def test_identity_loader_reloads_pack_and_trust(monkeypatch):
    configured = settings()
    reads = []
    monkeypatch.setattr(resources, "load_identity_pack_trust_binding",
                        lambda path: reads.append(path) or "trust")
    monkeypatch.setattr(resources, "load_current_identity_pack",
                        lambda path, **kwargs: (path, kwargs))
    for _ in range(2):
        assert resources.load_conversation_identity_pack(configured) == (
            Path(configured.identity_pack_file), {"trust": "trust"})
    assert reads == [Path(configured.identity_pack_trust_file)] * 2


def test_digital_setting_defaults_off_and_requires_explicit_budget():
    from eva.bootstrap.runtime import RuntimeSettings
    base = dict(database_dsn="postgresql://unused", hindsight_url="http://127.0.0.1:8888")
    assert RuntimeSettings(**base).delegated_digital_enabled is False
    with pytest.raises(ValueError, match="boolean"):
        RuntimeSettings(**base, delegated_digital_enabled="true")
    with pytest.raises(ValueError, match="runtime budget"):
        RuntimeSettings(**base, delegated_digital_enabled=True)
