"""Validated document completions for the existing private delivery routes."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from uuid import UUID

from eva.adapters.postgres.private_work_return import private_episode
from eva.application.delegated_work_delivery import PrivateWorkDestination
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.document_goal_actions import DocumentGoalActions, _source
from eva.providers.document_task_input import DocumentTaskPool


def return_binding(authority, turn):
    """Bind only a freshly verified private route at new document admission."""
    resolver = authority._private_owner_resolver
    if resolver is None:
        return None
    owner = authority.canonical_owner(turn)
    if resolver(turn) != owner:
        raise ValueError("document return owner is unavailable")
    binding = str(resolver._binding)
    destination = None
    if turn.conversation_id.startswith('telegram:'):
        chat = turn.conversation_id.removeprefix('telegram:')
        if not chat.isascii() or not chat.isdigit():
            raise ValueError("document return chat is invalid")
        destination = PrivateWorkDestination.from_telegram_turn(turn,
            owner_principal_id=UUID(owner), principal_binding_id=resolver._binding,
            telegram_user_id=int(chat), chat_id=chat, bindings=resolver._bindings_loader()).to_payload()
        channel = 'telegram_private'
    elif turn.conversation_id == 'dashboard:' + binding or turn.conversation_id.startswith('voice:'):
        channel = 'private_session'
    else:
        return None
    return {'contract': 'EvaDocumentReturnBinding/v1', 'owner_principal_id': owner,
            'principal_binding_id': binding, 'source': _source(turn),
            'conversation_id': turn.conversation_id, 'episode_id': private_episode(turn),
            'channel': channel, 'destination': destination}


@dataclass(frozen=True)
class DocumentCompletion:
    goal_id: str
    definition_sha256: str
    binding: dict
    artifact_payload: dict
    output: str
    created_at: datetime
    completed_at: str

    def to_payload(self):
        return self.artifact_payload

    @property
    def output_sha256(self):
        return hashlib.sha256(self.output.encode()).hexdigest()

    @property
    def result_sha256(self):
        return canonical_sha256(self.artifact_payload)


class DocumentCompletionSource:
    def __init__(self, *, store, authority, read_pool, load_source_turn):
        self.store, self.authority = store, authority
        self.read_pool, self.load_source_turn = read_pool, load_source_turn

    def candidates(self):
        pool = DocumentTaskPool.from_json(self.read_pool())
        for _, claim in pool.claims(self.store):
            # Historical claims have no opt-in and are never backfilled.
            if claim.get('return_binding') is not None:
                yield claim['plan']['goal_id']

    def load(self, goal_id):
        if not isinstance(goal_id, str) or not goal_id.startswith('document-task:'):
            return None
        pool = DocumentTaskPool.from_json(self.read_pool())
        claim = pool.find(self.store, goal_id.removeprefix('document-task:'))
        if claim is None or claim.get('return_binding') is None:
            return None
        turn = self.load_source_turn(claim['source'])
        self.authority.authorize_turn(turn)
        self.authority.authorize_current(phase='execute', operation_kind='effect')
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        PostgresConversationJournal(self.store._timeline._engine, self.authority.scope).ensure_running(turn)
        if _source(turn) != claim['source'] or return_binding(self.authority, turn) != claim['return_binding']:
            raise ValueError('document return source binding changed')
        adapter = DocumentGoalActions(store=self.store, authority=self.authority,
            read_plan=lambda: json.dumps(claim['plan']),
            read_definition=lambda: json.dumps(claim['definition']), load_source_turn=self.load_source_turn)
        adapter.authorize_turn(turn)
        state = adapter._state()
        if state is None or state.status != 'completed':
            return None
        first, final, plan = state.first_artifact, state.artifact, adapter.plan
        if (first is None or final is None or state.delivery_status != 'accepted'
                or self.store.document_plan_binding(state.spec) is None
                or first.artifact_id != 'document:' + plan.step(1).operation_id
                or first.content != plan.first_content
                or final.artifact_id != 'document:' + plan.step(2, first).operation_id
                or final.content != plan.final_content(first)):
            raise ValueError('document completion artifact binding differs')
        completed = [event for event in self.store._events(state.spec) if event.event_type == 'GOAL_COMPLETED']
        if len(completed) != 1:
            raise ValueError('document completion event differs')
        completed_at = completed[0].occurred_at.isoformat().replace('+00:00', 'Z')
        payload = {'contract': 'EvaDocumentCompletion/v1', 'goal_id': goal_id,
                   'definition_sha256': adapter.definition.sha256,
                   'first': {**first.to_payload(), 'path': plan.first_path},
                   'final': {**final.to_payload(), 'path': plan.final_path},
                   'local_availability': 'verified_stored_result', 'completed_at': completed_at}
        output = (state.spec.description + '\n\n' + plan.first_path + '\n' + first.content
                  + '\n\n' + plan.final_path + '\n' + final.content)
        return DocumentCompletion(goal_id, adapter.definition.sha256, claim['return_binding'],
            payload, output, state.spec.created_at, completed_at)
