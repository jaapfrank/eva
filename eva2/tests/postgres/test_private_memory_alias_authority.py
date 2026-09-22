from dataclasses import replace

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.private_memory_aliases import PostgresPrivateMemoryAliases
from eva.adapters.postgres.current_catalog import metadata
from eva.application.memory_audience import MemoryAudience, TelegramGroupConsentReference, HouseholdConsentReference
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from tests.postgres.test_people_memory_overview import people_memory_engine, NOW
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.projection.test_hindsight import _query


def test_private_alias_current_authority_scope_and_revocation(people_memory_engine):
    label = "private-alias-current"
    scope, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    bindings = [TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),))]
    resolver = PostgresPrivateMemoryAliases(people_memory_engine,
        tenant_id="conversation-tenant", installation_id="conversation-installation",
        owner=owner, binding=binding, authority_scope=scope,
        bindings_loader=lambda: bindings[0], clock=lambda: NOW)
    query = replace(_query(), tenant_id="conversation-tenant", installation_id="conversation-installation",
        principal_id=str(owner), subject_ids=(str(owner),),
        audience=MemoryAudience.private_person(principal_id=str(owner)))
    pair = (str(owner), "telegram:101")
    assert resolver(query) == pair
    bridge = replace(query, principal_id="telegram:101", subject_ids=("telegram:101",),
        audience=MemoryAudience.private_person(principal_id="telegram:101"), conversation_id="telegram:101")
    assert resolver(bridge) == pair
    assert resolver(replace(bridge, conversation_id="telegram:-101")) == ()
    assert resolver(replace(query, installation_id="other-installation")) == ()
    assert resolver(replace(query, tenant_id="other-tenant")) == ()
    stranger = replace(query, principal_id="person:other", subject_ids=("person:other",),
        audience=MemoryAudience.private_person(principal_id="person:other"))
    assert resolver(stranger) == ()
    for audience in (
        MemoryAudience.telegram_group(principal_id=str(owner), group_chat_id="-101",
            consent_ref=TelegramGroupConsentReference(str(owner), "-101", "telegram-group-consent:test")),
        MemoryAudience.household(principal_id=str(owner), household_id="home",
            consent_ref=HouseholdConsentReference(str(owner), "home", "household-consent:test")),
    ):
        assert resolver(replace(query, audience=audience,
            conversation_id=("household:home:conversation:test" if audience.kind == "household" else "telegram:-101"),
            scope=({"kind": "household", "household_id": "home"} if audience.kind == "household" else query.scope))) == ()
    bindings[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("-101",), str(binding)),))
    assert resolver(query) == ()
    bindings[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),))
    assert resolver(query) == pair
    table = metadata.tables["eva_core.principal_bindings"]
    with people_memory_engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == scope.tenant_id,
            table.c.principal_binding_id == binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="not current"):
        resolver(query)


def test_private_alias_unreadable_binding_file_and_provider_fail_closed(people_memory_engine):
    label = "private-alias-provider"
    scope, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),))
    def unavailable():
        raise OSError("unreadable trusted binding file")
    resolver = PostgresPrivateMemoryAliases(people_memory_engine,
        tenant_id="conversation-tenant", installation_id="conversation-installation",
        owner=owner, binding=binding, authority_scope=scope,
        bindings_loader=unavailable, clock=lambda: NOW)
    query = replace(_query(), tenant_id="conversation-tenant", installation_id="conversation-installation",
        principal_id=str(owner), subject_ids=(str(owner),),
        audience=MemoryAudience.private_person(principal_id=str(owner)))
    with pytest.raises(OSError):
        resolver(query)
    resolver._bindings_loader = lambda: bindings
    table = metadata.tables["eva_core.principal_bindings"]
    with people_memory_engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == scope.tenant_id,
            table.c.principal_binding_id == binding).values(channel_id="group"))
    assert resolver(query) == ()
