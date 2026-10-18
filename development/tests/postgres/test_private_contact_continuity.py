"""Delivered private self-contact reaches the existing conversation, not another audience."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.application.free_will import ProactiveExpressionWorker
from tests.postgres.test_private_initiative_contact import _setup, _prepare
from tests.postgres.test_owner_initiatives import costless_native, NOW


def test_only_verified_delivery_is_recalled_and_age_bounded(costless_native):
    _, _, owner, _, _, store = _setup(costless_native)
    _prepare(store)
    assert owner.recent_private_contacts(now=NOW) == []
    worker = ProactiveExpressionWorker(store, SimpleNamespace(deliver=lambda claim: "telegram:42:23"),
        channel="telegram", before_send=lambda claim: store.authorize_private_expression(claim, now=NOW))
    assert worker.run_once(now=NOW+timedelta(seconds=1)) == "delivered"
    contacts = owner.recent_private_contacts(now=NOW+timedelta(seconds=1))
    assert len(contacts) == 1 and contacts[0]["text"] == "May I ask a private question?"
    assert contacts[0]["provider_message_id"] == "telegram:42:23"
    assert owner.recent_private_contacts(now=NOW) == []
    # Keep this synthetic authority current while exercising the separate contact TTL.
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import metadata
    with owner._engine.begin() as conn:
        for name in ("eva_core.principal_bindings", "eva_core.principals"):
            table = metadata.tables[name]
            conn.execute(sa.update(table).where(table.c.tenant_id == owner._authority.tenant_id,
                table.c.principal_id == owner._owner).values(retention_until=NOW+timedelta(days=10)))
    assert owner.recent_private_contacts(now=NOW+timedelta(days=8)) == []
    assert worker.run_once(now=NOW) == "idle"


def test_ambiguous_private_send_never_becomes_delivered_context(costless_native):
    _, _, owner, _, _, store = _setup(costless_native)
    _prepare(store)
    def unknown(claim):
        raise TimeoutError("transport outcome unknown")
    worker = ProactiveExpressionWorker(store, SimpleNamespace(deliver=unknown), channel="telegram",
        before_send=lambda claim: store.authorize_private_expression(claim, now=NOW))
    assert worker.run_once(now=NOW) == "failed"
    assert owner.recent_private_contacts(now=NOW) == []
    assert worker.run_once(now=NOW) == "idle"


def test_owner_revocation_blocks_delivered_contact_read(costless_native):
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import metadata
    _, _, owner, _, _, store = _setup(costless_native)
    _prepare(store)
    worker = ProactiveExpressionWorker(store, SimpleNamespace(deliver=lambda claim: "telegram:42:23"),
        channel="telegram", before_send=lambda claim: store.authorize_private_expression(claim, now=NOW))
    assert worker.run_once(now=NOW) == "delivered"
    bindings = metadata.tables["eva_core.principal_bindings"]
    with owner._engine.begin() as conn:
        conn.execute(sa.update(bindings).where(bindings.c.tenant_id == owner._authority.tenant_id,
            bindings.c.principal_binding_id == owner._binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        owner.recent_private_contacts(now=NOW)
