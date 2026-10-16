"""A real PostgreSQL return claim stays bound to its WhatsApp source episode."""
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from eva.bootstrap.whatsapp_context import original_result_message
from eva.adapters.postgres.scheduled_work_failure import schedule_return_binding
from tests.postgres.test_private_work_return import setup, start
from tests.postgres.test_delegated_delivery_store import succeed
from eva.operations.install_schema import install_schema


@pytest.fixture(scope='module')
def delegated_engine():
    dsn = os.environ['EVA2_TEST_POSTGRES_DSN']
    assert make_url(dsn).database.startswith('eva2_4b_disposable_e26_')
    # The real installation is PostgreSQL 18; the old shared fixture pins 16.
    # Apply the actual schema to the runner's disposable database, never live data.
    install_schema(dsn, alembic_ini=Path(__file__).resolve().parents[3] / 'deploy/alembic.ini')
    engine = sa.create_engine(dsn)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize('separate_conversation_scope', [False, True])
def test_whatsapp_result_claim_authority_original_quote_and_other_contact(delegated_engine, separate_conversation_scope):
    service, turn, at = setup(delegated_engine, 'whatsapp-return-' + str(separate_conversation_scope), voice=False)
    if separate_conversation_scope:
        from tests.postgres.test_delegated_work_store import _seed_authority
        scope, _, _ = _seed_authority(delegated_engine, 'whatsapp-other-namespace')
        service.conversation_scope = scope
        turn = replace(turn, tenant_id='tenant:whatsapp-other-namespace', installation_id='installation:whatsapp-other-namespace')
    turn = replace(turn, conversation_id='whatsapp:31612345678', message_id='whatsapp:31612345678:ORIGINAL')
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    PostgresConversationJournal(delegated_engine, service.conversation_scope).admit(turn)
    # Actual foreground command key is bound to this exact original ingress.
    spec = start(service, turn, key=service.command_key(turn))
    saved = succeed(service.store, spec)
    at[0] += timedelta(seconds=4)
    result = service.private_return(turn, 'poll')['result']
    assert result['output_sha256'] == saved.output_sha256
    assert original_result_message(SimpleNamespace(_delegated_work_owner_service=service), turn, spec.work_id) == 'ORIGINAL'
    key = {name: result[name] for name in ('work_id', 'operation_id')}
    assert service.private_return(turn, 'authorize', **key)['status'] == 'authorized'
    assert service.private_return(replace(turn, conversation_id='whatsapp:31611111111'), 'poll')['result'] is None
    with pytest.raises(ValueError):
        service.private_return(replace(turn, conversation_id='whatsapp:31611111111'), 'authorize', **key)
    service.private_return(turn, 'ack', **key)
    assert service.private_return(turn, 'poll')['result'] is None
    # The same existing work remains readable; no second executor was started.
    assert service.execute('result', turn, work_id=spec.work_id, request_key='read')['work']['result']['output'] == saved.output
    binding = schedule_return_binding(service, turn)
    assert binding['channel'] == 'private_session'
    assert binding['source']['message_id'] == turn.message_id


def test_malformed_whatsapp_route_is_not_a_private_return_channel(delegated_engine):
    service, turn, _ = setup(delegated_engine, 'whatsapp-malformed', voice=False)
    with pytest.raises(ValueError, match='route unavailable'):
        service.private_return(replace(turn, conversation_id='whatsapp:unknown'), 'poll')
