"""Opt-in live model -> real action -> isolated DB -> reloaded attention probe.

The host exchanges request/response JSON with the installed model container.
No synthetic memory, feedback or delivery is written to the production database.
"""
import json
import os
import time
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pytest

from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import (ConversationActionCall, ConversationModelResult,
    ConversationModelUsage, ConversationTurn, IdentityProfile, LivingConversation)
from eva.application.free_will import PersonalInitiativeProposal
from eva.application.resident_attention import NextAttention
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from tests.application.test_conversation import _Memory
from tests.postgres.test_next_attention import finish
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import costless_native, NOW

pytestmark = pytest.mark.skipif(not os.environ.get('EVA_INSTALLED_MODEL_PROBE_DIR'),
    reason='Explicit installed-model bridge required; not an offline regression')


class InstalledModel:
    def __init__(self, directory, case):
        self.directory, self.case, self.calls = directory, case, 0

    def generate(self, turn, identity, memory, recent_messages, **kwargs):
        self.calls += 1
        assert self.calls <= 4, 'Probe model-call budget exceeded'
        key = self.case + "-" + str(self.calls)
        payload = {'turn':asdict(turn), 'self_context':kwargs.get('self_context',()),
            'available_actions':kwargs.get('available_actions',()),
            'completed_action':None if kwargs.get('completed_action') is None else asdict(kwargs['completed_action']),
            'prior_actions':[asdict(r) for r in kwargs.get('prior_actions',())]}
        temporary = self.directory / (key + '.tmp')
        temporary.write_text(json.dumps(payload))
        temporary.rename(self.directory / (key + '.request.json'))
        response = self.directory / (key + '.response.json')
        deadline = time.monotonic() + 90
        while not response.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert response.exists(), 'Installed model response missing; no retry'
        data = json.loads(response.read_text())
        assert 'error' not in data, data.get('error')
        call = data.get('action_call')
        return ConversationModelResult(data['text'], data['provider'],
            action_call=None if call is None else ConversationActionCall(**call),
            usage=ConversationModelUsage(**data['usage']), text_phase=data.get('text_phase'))


@pytest.mark.parametrize('correction', [True, False])
def test_real_model_correction_is_stored_once_and_cancels_attention(review_setup, correction):
    auth, old, control, budget, source, factory, *_ = review_setup
    origin = finish(source, choice=NextAttention(NOW + timedelta(minutes=5), 'Review pending container preparation'),
        initiative=PersonalInitiativeProposal('Containerophaling voorbereiden',
            'Voorkomen dat de ophaling wordt gemist', 'Onderzoek of de container voor vertrek klaarstaat'))
    intent = source._timeline.events(origin.thread_id)[-1].payload['data']['initiative']['intent_id']
    at = NOW + timedelta(seconds=1)
    service = DelegatedWorkOwnerService(PostgresDelegatedWorkStore(control._engine, control._authority),
        owner_principal_id=control._owner, principal_binding_id=control._binding,
        conversation_scope=control._scope, clock=lambda:at)
    class Actions(PostgresDelegatedWorkActions):
        def available_action_names(self, turn):
            return tuple(n for n in self.action_names if n in (
                'give_initiative_feedback','stop_own_initiative','list_own_initiatives')) if self.available_for(turn) else ()
        def continuation_action_names(self, turn):
            return self.available_action_names(turn)
        def execute_step(self, turn, call, *, step_index):
            return self.execute(turn, call, step_index=step_index)
    actions = Actions(control._engine, service)
    bridge = Path(os.environ['EVA_INSTALLED_MODEL_PROBE_DIR'])
    case = 'correction' if correction else 'bare-stop'
    model = InstalledModel(bridge, case)
    conversation = LivingConversation(_Memory(), model,
        IdentityProfile('Eva','Housemate',('Honest',),80), actions, contact_model=model)
    turn = ConversationTurn.private(control._tenant_id, control._installation_id,
        str(control._owner), 'telegram:feedback-probe', 'feedback-probe-1',
        ('Dat containeronderzoek is niet meer nodig. De buurman regelt het, dus geen herinnering of vervolgonderzoek meer.'
         if correction else 'Stop dat containeronderzoek.'),
        at.strftime('%Y-%m-%dT%H:%M:%SZ'))
    reply = conversation.respond(turn)
    receipt = actions.pending(turn)
    assert receipt.call.name in {'give_initiative_feedback', 'stop_own_initiative'}
    assert receipt.call.arguments['intent_id'] == intent
    assert (receipt.output['operation_result'].get('execution_stopped') is True
            or receipt.output['operation_result'].get('status') == 'stopped')
    assert actions.execute(turn, receipt.call) == receipt
    feedback = [e for e in source._timeline.events('intent:'+intent)
                if e.event_type in {'PERSONAL_INITIATIVE_FEEDBACK', 'PERSONAL_INITIATIVE_STOPPED'}]
    assert len(feedback) == 1
    retained = next(x for x in factory().recent_initiatives() if x['intent_id'] == intent)
    if correction:
        assert retained.get('owner_feedback'), 'The substantive correction must remain available'
        assert retained['owner_feedback'] == receipt.call.arguments['feedback']
        assert 'buurman' in retained['owner_feedback'].casefold()
    else:
        assert receipt.call.name == 'stop_own_initiative'
    assert retained['phase'] == 'personal_stopped'
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=5)) == 'idle'
    assert factory().claim_next(now=NOW+timedelta(minutes=5)) is None
    (bridge/('outcome-'+case+'.json')).write_text(json.dumps({'reply':reply.text,'modelCalls':model.calls,
        'action':receipt.call.name,'feedback':retained.get('owner_feedback'),
        'stopOrFeedbackEvents':len(feedback),'phase':retained['phase'],'dueAttention':'idle',
        'productionWrites':False,'delivered':False},ensure_ascii=False))
