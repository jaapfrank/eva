"""Owner corrections retained with a digital execution across explicit resumptions."""
from contextlib import contextmanager
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope, CheckpointConflict
from eva.contracts.canonical_json import canonical_sha256


def _timeline(engine, spec):
    return PostgresOperationalTimeline(engine, external_timeline_scope(
        spec.source.tenant_id, spec.source.installation_id, "correction scope required"))


def _thread(spec):
    return "work-corrections:" + canonical_sha256({"execution_id": spec.execution_id,
        "source": spec.source.model_dump(mode="json")})


def work_corrections(engine, spec):
    return [{"instruction": event.payload["data"]["instruction"],
             "recorded_at": event.occurred_at.isoformat(),
             "work_id": event.payload["data"]["work_id"]}
            for event in _timeline(engine, spec).events(_thread(spec))]


def record_work_correction(engine, spec, *, instruction, request_key, now):
    if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 2000:
        raise ValueError("work correction requires 1..2000 characters")
    timeline, thread = _timeline(engine, spec), _thread(spec)
    for _ in range(3):
        events = timeline.events(thread)
        for event in events:
            if event.payload["data"]["request_key"] == request_key:
                if event.payload["data"]["instruction"] != instruction.strip():
                    raise ValueError("work correction changed during replay")
                return event.occurred_at
        if len(events) >= 16:
            raise ValueError("start a new task after sixteen corrections")
        values = dict(thread_id=thread, checkpoint_id=thread + ":" + str(len(events) + 1),
            occurred_at=now, phase="correction_recorded", status="completed",
            event_type="OWNER_WORK_CORRECTION_RECORDED",
            data={"request_key": request_key, "instruction": instruction.strip(),
                  "work_id": spec.work_id})
        try:
            if events:
                timeline.transition(expected_revision=events[-1].sequence, **values)
            else:
                timeline.create_thread(**values)
            return now
        except CheckpointConflict:
            continue
    raise ValueError("work corrections changed concurrently; retry the same request")


def correction_lock_key(spec):
    """Share the correction boundary with dependent schedule evaluation."""
    return "work-correction-lock:" + canonical_sha256({
        "execution_id": spec.execution_id, "source": spec.source.model_dump(mode="json")})


@contextmanager
def correction_boundary(engine, spec):
    # Keep stop/save/schedule indivisible to dependency readers without changing
    # the existing durable operations. Readers try the lock rather than waiting,
    # so they cannot deadlock against the scheduler's owner-level admission lock.
    with engine.begin() as connection:
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                           {"key": correction_lock_key(spec)})
        yield


def is_legacy_model_refusal(work):
    """Recognize the old typed-429 classification, never arbitrary failures."""
    import hashlib
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
    return (type(work.spec) is DelegatedDigitalWorkSpec and work.state == "ambiguous"
            and work.error_code == "internal_error" and work.error_fingerprint == hashlib.sha256(
                b"ConversationModelRateLimited:conversation provider rate limited").hexdigest())


def reconcile_legacy_model_refusal(engine, work, source_turn, *, now, close_session):
    """Explicit resume only: prove a closed model refusal and local-only dispatches.

    Preserve the old terminal record and all replay guards. Missing model records,
    live work, unknown tool results or any external dispatch remain a refusal.
    """
    from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.operational_timeline import operational_events as events
    if (not is_legacy_model_refusal(work) or work.stop_requested_at is not None
            or work.lease_owner is not None or work.lease_expires_at is not None
            or work.call_id is None or work.call_started_at is None or work.completed_at is None
            or work.result is not None or work.retention_until <= now
            or work.usage is None or type(work.usage.tool_calls) is not int):
        return None
    spec = work.spec
    if canonical_sha256(PostgresConversationJournal._ingress_data(source_turn)) != spec.source_ingress_sha256:
        return None
    timeline = _timeline(engine, spec)
    scope = timeline._scope_value
    accounting = BackgroundModelJournal(PostgresConversationJournal(engine, scope),
                                       source_turn, work_id=spec.work_id)._accounting
    with engine.connect() as connection:
        timeline._scope(connection)
        names = list(connection.scalars(sa.select(events.c.thread_id).where(
            events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
            events.c.event_type == "CONVERSATION_MODEL_ADMITTED",
            events.c.payload["data"]["message_id"].astext == accounting.message_id
        ).limit(spec.grant.maximum_model_runs + 1)))
    if not names or len(names) > spec.grant.maximum_model_runs:
        return None
    heads, failed = [], []
    for name in names:
        chain = timeline.events(name)  # The timeline validates retained hashes.
        if (len(chain) != 2 or chain[0].event_type != "CONVERSATION_MODEL_ADMITTED"
                or chain[1].event_type != "CONVERSATION_MODEL_RECORDED"):
            return None
        admitted, recorded = (e.payload["data"] for e in chain)
        expected = {"principal_id": source_turn.principal_id,
                    "conversation_id": accounting.conversation_id, "message_id": accounting.message_id}
        if (any(admitted.get(k) != v or recorded.get(k) != v for k, v in expected.items())
                or recorded.get("request_id") != admitted.get("request_id")
                or recorded.get("local_stream_closed") is not True
                or chain[0].occurred_at < work.call_started_at
                or chain[1].occurred_at > work.completed_at):
            return None
        outcome = recorded.get("outcome", "succeeded")
        if outcome == "failed": failed.append(chain[1].occurred_at)
        elif outcome != "succeeded": return None
        heads.append((chain[1].occurred_at, name, canonical_sha256(chain[1].payload)))
    if len(failed) != 1 or failed[0] != max(h[0] for h in heads):
        return None
    identity = {"execution_id": spec.execution_id, "source": spec.source.model_dump(mode="json"),
                "source_ingress_sha256": spec.source_ingress_sha256}
    workspace = canonical_sha256(identity)
    local = []
    for step in range(spec.grant.maximum_tool_calls + 1):
        thread = "programming-action:" + canonical_sha256({"workspace": workspace, "step": step})
        chain = timeline.events(thread)
        if not chain or chain[0].occurred_at < work.call_started_at:
            continue  # Previous generations keep their existing replay guards.
        if (len(chain) != 1 or chain[0].event_type != "PROGRAMMING_ACTION_RECORDED"
                or chain[0].occurred_at > work.completed_at):
            return None
        data = chain[0].payload["data"]
        if (data.get("name") not in {"list_programming_tools", "use_programming_tool",
                                     "write_programming_file", "start_programming_process"}
                or data.get("output", {}).get("status") != "verified"
                or not isinstance(data.get("output", {}).get("programming_result"), dict)):
            return None
        local.append({"thread_id": thread, "sha256": canonical_sha256(chain[0].payload)})
    # A dispatch without a saved programming receipt (including an external call)
    # consumes the same counter, so no incomplete or external effect is pardoned.
    if len(local) != work.usage.tool_calls:
        return None
    if local:
        closed = close_session(canonical_sha256({**identity, "work_id": spec.work_id}))
        # The /programming HTTP boundary acknowledges close as {"closed": true};
        # its host implementation retains the workspace and joins the old actor.
        if (not isinstance(closed, dict) or set(closed) != {"closed"}
                or closed["closed"] is not True):
            return None
    proof = {"contract": "EvaLegacyModelRefusalReconciliation/v1", "work_id": spec.work_id,
             "spec_sha256": spec.sha256, "call_id": work.call_id,
             "source_ingress_sha256": spec.source_ingress_sha256,
             "model_heads": [{"thread_id": h[1], "sha256": h[2]} for h in sorted(heads)],
             "local_heads": local, "workspace": workspace, "session_closed": bool(local),
             "external_effects_reconciled": False}
    thread = "work-model-refusal-reconciliation:" + canonical_sha256(proof)
    existing = timeline.events(thread)
    if existing:
        if len(existing) != 1 or existing[0].payload.get("data") != proof:
            return None
    else:
        timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":recorded",
            occurred_at=now, phase="model_refusal_reconciled", status="completed",
            event_type="LEGACY_MODEL_REFUSAL_RECONCILED", data=proof)
    return proof


def is_local_host_rejection(work):
    """A completed private attempt's HTTP rejection, never timeout/unknown I/O."""
    import hashlib
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
    return (type(work.spec) is DelegatedDigitalWorkSpec and work.state == 'ambiguous'
        and work.error_code == 'internal_error'
        and work.error_fingerprint == hashlib.sha256(b'HTTPError:HTTP Error 400: Bad Request').hexdigest())


def reconcile_local_host_rejection(engine, work, source_turn, *, now, close_session, read_host_proof, queue_scope):
    """Explicit resume: retain uncertainty unless *all* no-effect proofs agree.

    No connector/procedure/program dispatch is pardoned. The original terminal
    record and grant remain unchanged; the host must independently observe the
    exact candidate with no review or application admission.
    """
    from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.operational_timeline import operational_events as events
    from eva.application.background_execution import BackgroundExecutionScope
    if (not is_local_host_rejection(work) or work.stop_requested_at is not None
            or work.lease_owner is not None or work.lease_expires_at is not None
            or work.call_id is None or work.call_started_at is None or work.completed_at is None
            or work.result is not None or work.retention_until <= now or work.usage is None
            or type(work.usage.tool_calls) is not int or work.usage.tool_calls < 2):
        return None
    spec = work.spec
    if canonical_sha256(PostgresConversationJournal._ingress_data(source_turn)) != spec.source_ingress_sha256:
        return None
    timeline = _timeline(engine, spec);scope = timeline._scope_value
    accounting = BackgroundModelJournal(PostgresConversationJournal(engine, scope), source_turn, work_id=spec.work_id)._accounting
    identity = {'execution_id': spec.execution_id, 'source': spec.source.model_dump(mode='json'),
                'source_ingress_sha256': spec.source_ingress_sha256}
    workspace = canonical_sha256(identity);namespace = BackgroundExecutionScope(source_turn, spec.execution_id).namespace
    opaque = {}
    for step in range((spec.generation + 1) * spec.grant.maximum_tool_calls):
        for thread in ['programming-action:'+canonical_sha256({'workspace':workspace,'step':step}),
                       'component-draft:'+canonical_sha256({'execution':spec.execution_id,'source':identity['source'],'step':step}),
                       'tool-program:'+canonical_sha256({**identity,'step':step}),
                       'procedure-action:'+canonical_sha256({'principal_id':source_turn.principal_id,
                           'conversation_id':source_turn.conversation_id,'message_id':source_turn.message_id,
                           'step_index':step,'execution_namespace':namespace})]:
            opaque[thread] = step
    with engine.connect() as connection:
        timeline._scope(connection)
        names = list(connection.scalars(sa.select(events.c.thread_id).where(
            events.c.event_type=='CONVERSATION_MODEL_ADMITTED',
            events.c.payload['data']['message_id'].astext==accounting.message_id).limit(spec.grant.maximum_model_runs+1)))
        data = events.c.payload['data']
        rows = connection.execute(sa.select(events).where(
            events.c.occurred_at >= work.call_started_at, events.c.occurred_at <= work.completed_at,
            sa.or_(events.c.thread_id.in_(tuple(opaque)),data['execution_namespace'].astext==namespace))).mappings().all()
    if not names or len(names) != work.usage.completed_model_responses:
        return None
    model_heads=[]
    for name in names:
        chain=timeline.events(name)
        if (len(chain)!=2 or chain[0].event_type!='CONVERSATION_MODEL_ADMITTED'
                or chain[1].event_type!='CONVERSATION_MODEL_RECORDED'):
            return None
        admitted,recorded=(item.payload['data'] for item in chain)
        if (recorded.get('outcome')!='succeeded' or recorded.get('local_stream_closed') is not True
                or recorded.get('request_id')!=admitted.get('request_id')
                or any(recorded.get(k)!=getattr(accounting,k) or admitted.get(k)!=getattr(accounting,k)
                       for k in ('principal_id','conversation_id','message_id'))
                or chain[0].occurred_at < work.call_started_at or chain[1].occurred_at > work.completed_at):
            return None
        model_heads.append({'thread_id':name,'sha256':canonical_sha256(chain[1].payload)})
    heads=[];contract_read=False;seen_steps=set();seen_calls=set()
    for row in rows:
        if row['sequence']!=1 or canonical_sha256(row['payload'])!=bytes(row['payload_sha256']).hex():return None
        value=row['payload']['data'];output=value.get('output',{})
        if row['thread_id'] not in opaque or opaque[row['thread_id']] in seen_steps or value.get('call_id') in seen_calls:return None
        seen_steps.add(opaque[row['thread_id']]);seen_calls.add(value.get('call_id'))
        if output.get('status')!='verified':return None
        if row['event_type']=='PROGRAMMING_ACTION_RECORDED':
            if value.get('name') not in {'list_programming_tools','use_programming_tool','write_programming_file','start_programming_process'} or not isinstance(output.get('programming_result'),dict):return None
        elif row['event_type']=='COMPONENT_ACTION_RECORDED':
            result=output.get('operation_result',{})
            if (value.get('name')!='get_reviewed_component_application'
                    or result.get('contract') not in {'EvaOwnedPackageCandidate/v1','EvaOwnedPackageHandoff/v1'}
                    or result.get('state') not in {'package_contract','not_submitted'}
                    or result.get('installed') is not False):return None
            contract_read=True
        else:
            return None  # Includes every admitted external, procedure or tool-program dispatch.
        heads.append({'thread_id':row['thread_id'],'sha256':bytes(row['payload_sha256']).hex()})
    if not contract_read or len(heads)!=work.usage.tool_calls-1:return None
    proof=read_host_proof(task_id=spec.work_id,scope_sha256=canonical_sha256({k:getattr(spec.source,k) for k in ('tenant_id','installation_id','principal_id')}))
    from eva.bootstrap.owned_package_host import _owner_binding
    expected_origin={'task_id':spec.work_id,'execution_id':spec.execution_id,'source_ingress_sha256':spec.source_ingress_sha256,
        'spec_sha256':spec.sha256,'workspace_key':workspace,'owner_binding_sha256':_owner_binding(spec,queue_scope),
        'scope_sha256':canonical_sha256({k:getattr(spec.source,k) for k in ('tenant_id','installation_id','principal_id')})}
    if (not isinstance(proof,dict) or proof.get('contract')!='EvaOwnedPackageNoDispatch/v1'
            or proof.get('origin')!=expected_origin
            or any(proof.get(k) is not False for k in ('handoff_exists','application_requested','installed'))):return None
    from eva.operations.owned_package import key
    key(proof.get('archive_sha256'));key(proof.get('package_sha256'))
    closed=close_session(canonical_sha256({**identity,'work_id':spec.work_id}))
    if not isinstance(closed,dict) or set(closed)!={'closed'} or closed['closed'] is not True:return None
    model_heads.sort(key=lambda value:value['thread_id']);heads.sort(key=lambda value:value['thread_id'])
    receipt={'contract':'EvaLocalHostRejectionReconciliation/v1','work_id':spec.work_id,'spec_sha256':spec.sha256,
        'call_id':work.call_id,'model_heads':model_heads,'local_and_read_heads':heads,
        'host_no_dispatch':proof,'external_effects_reconciled':False,'original_terminal_state_preserved':True}
    thread='work-local-host-rejection:'+canonical_sha256(receipt);existing=timeline.events(thread)
    if existing:
        if len(existing)!=1 or existing[0].payload.get('data')!=receipt:return None
    else:
        timeline.create_thread(thread_id=thread,checkpoint_id=thread+':recorded',occurred_at=now,
            phase='local_host_rejection_reconciled',status='completed',
            event_type='LOCAL_HOST_REJECTION_RECONCILED',data=receipt)
    return receipt
