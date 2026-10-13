"""Materialize a completed task's referenced documents from verified save receipts.

This is a host read of existing evidence, never a connector dispatch or a new
model turn. Paths mentioned in prose alone cannot grant access to a file.
"""
from dataclasses import dataclass
import hashlib
from types import SimpleNamespace

import sqlalchemy as sa

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, operational_events, external_timeline_scope
from eva.application.procedure_references import verified_save_arguments
from eva.contracts.canonical_json import canonical_sha256


@dataclass(frozen=True, slots=True)
class SavedWorkArtifacts:
    output: str
    sha256: str
    manifest: tuple[dict, ...]


def saved_work_artifacts(store, work) -> SavedWorkArtifacts | None:
    spec = work.spec
    if (spec.contract != 'DelegatedDigitalWork/v4' or work.result is None
            or work.state not in {'succeeded', 'verified'} or work.stop_requested_at is not None):
        return None
    namespace = 'background-work:' + hashlib.sha256(spec.execution_id.encode()).hexdigest()
    source_scope = external_timeline_scope(spec.source.tenant_id, spec.source.installation_id,
                                          'saved result source scope is absent')
    timeline = PostgresOperationalTimeline(store._engine, source_scope)
    with store._engine.begin() as connection:
        timeline._scope(connection)
        rows = connection.execute(sa.select(operational_events).where(
            operational_events.c.tenant_id == source_scope.tenant_id,
            operational_events.c.installation_id == source_scope.installation_id,
            operational_events.c.event_type == 'TOOL_REQUEST_ADMITTED',
            operational_events.c.payload['data']['execution_namespace'].astext == namespace,
            operational_events.c.occurred_at <= work.completed_at,
        ).order_by(operational_events.c.occurred_at, operational_events.c.thread_id)).mappings().all()
    # Consider only actual saves explicitly referenced by the completed answer.
    # Taking the last admitted save prevents an older successful draft from
    # substituting for a later incomplete revision of the same path.
    latest = {}
    for row in rows:
        admission = row['payload'].get('data', {})
        arguments = admission.get('request_arguments', {})
        path, content = arguments.get('path'), arguments.get('content')
        if (admission.get('action_name') in {'save_workspace_document', 'use_connector_tool'}
                and isinstance(path, str) and path and path in work.result.output
                and isinstance(content, str)):
            step = admission.get('step_index', 0)
            if type(step) is not int:
                raise ValueError('saved result step is invalid')
            if path not in latest or step > latest[path][0]:
                latest[path] = (step, row['thread_id'])
    if not latest:
        return None
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    def require_source(turn):
        if canonical_sha256(PostgresConversationJournal._ingress_data(turn)) != spec.source_ingress_sha256:
            raise ValueError('saved result original ingress differs')
    source = _SourceTurnLoader(store._engine, SimpleNamespace(
        scope=source_scope, authorize_turn=require_source))(spec.source.model_dump(mode='json'))
    manifest, pieces = [], ['Volledig opgeslagen resultaat']
    for path, (step, thread) in sorted(latest.items()):
        events = timeline.events(thread)
        checkpoint = timeline.load(thread)
        if (not events or events[0].event_type != 'TOOL_REQUEST_ADMITTED'
                or events[-1].event_type != 'STATE_VERIFIED'
                or events[-1].occurred_at > work.completed_at
                or tuple(event.sequence for event in events) != tuple(range(1, len(events) + 1))
                or checkpoint.revision != len(events)):
            raise ValueError('saved result verification history differs')
        admission = events[0].payload['data']
        name = admission.get('action_name')
        port = object.__new__(PostgresExternalConversationTools)
        port._action_names = (name,)
        if (admission.get('execution_namespace') != namespace
                or thread != 'tool:' + port._historical_operation_id(source, name, admission, step)):
            raise ValueError('saved result execution identity differs')
        receipt = port._receipt(source, events, step, project=False)
        args = verified_save_arguments(admission)
        proof, output = receipt.output.get('proof', {}), admission.get('receipt_output', {})
        content = args.get('content')
        configuration = admission.get('request_tool_configuration_sha256')
        expected = {'content': content}
        if (receipt.output.get('status') != 'verified'
                or output.get('transport') != 'mcp_gateway'
                or output.get('operation_kind') in {'read', 'invoke'}
                or output.get('external_state_verified') is False
                or not isinstance(configuration, str) or len(configuration) != 64
                or output.get('tool_configuration_sha256') != configuration
                or args.get('path') != path
                or admission.get('request_arguments', {}).get('path') != path
                or admission.get('request_arguments', {}).get('content') != content
                or not isinstance(content, str) or not content.strip()
                or admission.get('request_observation_arguments', {}).get('path') != path
                or admission.get('request_expected_proof') != expected
                or proof.get('expected') != expected or proof.get('observed') != expected
                or proof.get('evidence', {}).get('source') != 'mcp_gateway_readback'):
            raise ValueError('saved result content is not bound to its verified save')
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        manifest.append({'path': path, 'content_sha256': digest,
                         'receipt_sha256': canonical_sha256(receipt.output), 'thread_id': thread})
        pieces.extend((f'Bestand: {path}\nSHA-256: {digest}', content))
    output = '\n\n'.join(pieces)
    if len(output.encode('utf-8')) > 65_536:
        raise ValueError('saved result is outside its delivery contract')
    # A document has its own durable representation. Old text-part attempts,
    # including uncertain acceptance, remain closed and are never replayed.
    identity = {'representation': 'utf8_document/v1', 'documents': manifest}
    return SavedWorkArtifacts(output, canonical_sha256(identity), tuple(manifest))
