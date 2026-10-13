from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.application.conversation import ConversationTurn
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference
from tests.postgres.test_delegated_work_store import delegated_engine, _seed_authority, _id, principal_bindings
from tests.postgres.test_delegated_delivery_store import NOW, succeed


def setup(engine, label, voice=True):
    scope, _, binding = _seed_authority(engine, label)
    owner = _id(label, 'principal')
    work = PostgresDelegatedWorkStore(engine, scope)
    at = [NOW]
    service = DelegatedWorkOwnerService(work, owner_principal_id=owner,
        principal_binding_id=binding, clock=lambda: at[0])
    turn = ConversationTurn.private('tenant:'+label, 'installation:'+label, str(owner),
        'voice:'+str(uuid4()) if voice else 'dashboard:'+str(binding),
        'message:1', 'Research with sources', NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    if voice:
        assert service.private_return(turn, 'open')['status'] == 'open'
    return service, turn, at


def start(service, turn, key='start'):
    item = service.execute('start', turn, task='Research with sources', request_key=key)['work']
    return service.store.load(item['work_id'], **service._owner).spec


@pytest.mark.parametrize('voice', [True, False])
def test_late_result_preserves_bytes_claim_and_ack_without_duplicate(delegated_engine, voice):
    service, turn, at = setup(delegated_engine, 'return-late-'+str(voice), voice)
    spec = start(service, turn)
    assert service.private_return(turn, 'poll')['result'] is None
    # The foreground admission has already returned; a second turn can read work.
    assert service.execute('get', replace(turn, message_id='message:2'),
        work_id=spec.work_id, request_key='next')['work']['state'] == 'admitted'
    saved = succeed(service.store, spec)
    at[0] += timedelta(seconds=4)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: service.private_return(turn, 'poll'), range(2)))
    results = [r['result'] for r in replies if r.get('result')]
    assert len(results) == 1
    result = results[0]
    assert result['title'] == service.store.load(spec.work_id, **service._owner).title
    assert result['output'] == saved.output
    assert result['output_sha256'] == saved.output_sha256
    identity = {k: result[k] for k in ('work_id', 'operation_id')}
    assert service.private_return(turn, 'authorize', **identity)['output_sha256'] == saved.output_sha256
    assert service.private_return(turn, 'ack', **identity)['status'] == 'acknowledged'
    assert service.private_return(turn, 'ack', **identity)['status'] == 'acknowledged'
    assert service.private_return(turn, 'authorize', **identity)['output_sha256'] == saved.output_sha256
    assert service.private_return(turn, 'poll')['result'] is None
    assert service.execute('result', turn, work_id=spec.work_id, request_key='read')['work']['result']['output'] == saved.output


@pytest.mark.parametrize('ending', ['close', 'expiry', 'crash'])
def test_session_loss_never_reopens_or_returns_to_another_session(delegated_engine, ending):
    service, turn, at = setup(delegated_engine, 'return-loss-'+ending)
    spec = start(service, turn)
    succeed(service.store, spec)
    at[0] += timedelta(seconds=4)
    if ending == 'crash':
        assert service.private_return(turn, 'poll')['result']
        # Lost response/claim is never made available again.
        assert service.private_return(turn, 'poll')['result'] is None
    elif ending == 'close':
        service.private_return(turn, 'close')
        with pytest.raises(ValueError): service.private_return(turn, 'poll')
    else:
        at[0] += timedelta(seconds=20)
        with pytest.raises(ValueError): service.private_return(turn, 'poll')
    with pytest.raises(ValueError): service.private_return(turn, 'open')
    replacement = replace(turn, conversation_id='voice:'+str(uuid4()))
    service.private_return(replacement, 'open')
    assert service.private_return(replacement, 'poll')['result'] is None
    assert service.execute('result', turn, work_id=spec.work_id, request_key='read')['work']['result_available']


def test_older_completion_outside_twenty_cards_is_not_starved(delegated_engine):
    service, turn, at = setup(delegated_engine, 'return-older', False)
    spec = start(service, turn)
    saved = succeed(service.store, spec)
    for i in range(22):
        later = replace(turn, message_id='later:'+str(i),
            occurred_at=(NOW+timedelta(seconds=i+1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        start(service, later, 'later:'+str(i))
    assert spec.work_id not in {w['work_id'] for w in service.execute('list', turn, request_key='list')['jobs']}
    at[0] += timedelta(seconds=24)
    result = service.private_return(turn, 'poll')['result']
    assert result['work_id'] == spec.work_id and result['output'] == saved.output


@pytest.mark.parametrize('change', ['binding', 'other_session', 'forged_claim', 'household', 'retention'])
def test_private_return_rechecks_authority_and_result(delegated_engine, change):
    service, turn, at = setup(delegated_engine, 'return-private-'+change)
    spec = start(service, turn); succeed(service.store, spec)
    at[0] += timedelta(seconds=4)
    result = service.private_return(turn, 'poll')['result']
    identity = {k: result[k] for k in ('work_id', 'operation_id')}
    if change == 'binding':
        with delegated_engine.begin() as c:
            c.execute(sa.update(principal_bindings).where(
                principal_bindings.c.tenant_id == service.store.scope.tenant_id,
                principal_bindings.c.principal_binding_id == service.principal_binding_id).values(status='revoked'))
    elif change == 'other_session':
        turn = replace(turn, conversation_id='voice:'+str(uuid4()))
        service.private_return(turn, 'open')
    elif change == 'forged_claim': identity['operation_id'] = '0'*64
    elif change == 'household':
        turn = replace(turn, conversation_id='household:home:conversation:test',
            message_id='household:home:conversation:test:request:1', audience=MemoryAudience.household(principal_id=turn.principal_id,
            household_id='home', consent_ref=HouseholdConsentReference(turn.principal_id, 'home', 'household-consent:test')))
    else:
        from eva.adapters.postgres.current_catalog import delegated_work_runs
        with delegated_engine.begin() as c:
            c.execute(sa.update(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id == service.store.scope.tenant_id,
                delegated_work_runs.c.work_id == spec.work_id).values(retention_until=at[0]))
    with pytest.raises((ValueError, RuntimeError)):
        service.private_return(turn, 'authorize', **identity)


def test_stopped_task_never_becomes_an_automatic_result(delegated_engine):
    service, turn, at = setup(delegated_engine, 'return-stop')
    spec = start(service, turn)
    service.execute('stop', turn, work_id=spec.work_id, request_key='stop')
    assert service.private_return(turn, 'poll')['result'] is None


def test_voice_origin_never_becomes_a_dashboard_or_telegram_return(delegated_engine):
    service, turn, at = setup(delegated_engine, 'return-channel')
    spec = start(service, turn); saved = succeed(service.store, spec)
    at[0] += timedelta(seconds=4)
    dashboard = replace(turn, conversation_id='dashboard:'+str(service.principal_binding_id))
    assert service.private_return(dashboard, 'poll')['result'] is None
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
    from eva.application.telegram_ingress import TelegramPrincipalBindings
    telegram = PostgresDelegatedWorkDeliveryStore(service.store,
        bindings_loader=lambda:TelegramPrincipalBindings(()), clock=lambda:at[0])
    assert telegram.authorize_delivery(spec.work_id, **service._owner) is None
    assert service.private_return(turn, 'poll')['result']['output'] == saved.output


@pytest.mark.parametrize("research", [False, True])
def test_admitted_task_title_reaches_private_result(delegated_engine, research):
    from eva.bootstrap.delegated_research import DelegatedResearchProfile
    from eva.bootstrap.delegated_work_provider import MODEL_PROFILE_ID

    service, turn, at = setup(delegated_engine, "return-title-" + str(research), False)
    service.research_profile = DelegatedResearchProfile(MODEL_PROFILE_ID, "a" * 64)
    task = "  Compare   the available options\n" + "carefully " * 20
    title = "Home Assistant speech options"
    args = ({"research": {
        "contract": "PublicResearchRequest/v1", "schema_version": 1,
        "question": "What does Home Assistant Cloud provide for speech?",
        "title": title, "options": ["Home Assistant Cloud"],
        "research_mode": "topic", "public_review": "public_unlinked",
    }} if research else {"task": task})
    operation = "research" if research else "start"
    admitted = service.execute(operation, turn, request_key="title", **args)
    expected = title if research else " ".join(task.split())[:120]
    assert admitted["work"]["title"] == expected
    spec = service.store.load(admitted["work"]["work_id"], **service._owner).spec
    saved = succeed(service.store, spec)
    at[0] += timedelta(seconds=4)
    result = service.private_return(turn, "poll")["result"]
    assert (result["work_id"], result["title"], result["output"]) == (
        spec.work_id, expected, saved.output)
    replay = service.execute(operation, turn, request_key="title", **args)
    assert replay["created"] is False and replay["work"]["title"] == expected


def test_legacy_title_replay_preserves_metadata_and_rejects_changed_task(delegated_engine, monkeypatch):
    from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
    service, turn, at = setup(delegated_engine, "return-legacy-title", False)
    admit = service.store.admit

    def legacy_admission(**kwargs):
        return admit(**{**kwargs, "title": "Onderzoek vanuit Eva-chat"})

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "admit", legacy_admission)
        original = service.execute("start", turn, task="Write a picnic plan", request_key="legacy")
    replay = service.execute("start", turn, task="Write a picnic plan", request_key="legacy")
    assert replay["created"] is False
    assert replay["work"]["work_id"] == original["work"]["work_id"]
    assert replay["work"]["title"] == "Onderzoek vanuit Eva-chat"
    with pytest.raises(DelegatedWorkConflict):
        service.execute("start", turn, task="Write another task", request_key="legacy")
    service.execute("stop", turn, work_id=original["work"]["work_id"], request_key="stop")
    resumed = service.execute("resume", turn, work_id=original["work"]["work_id"], request_key="resume")
    assert resumed["work"]["title"] == "Onderzoek vanuit Eva-chat"
