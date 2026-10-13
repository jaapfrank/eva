"""Apply an independently reviewed update after its Eva task releases execution."""
from __future__ import annotations

import json
import inspect
import re
import subprocess

def _execution_chain(rows, task_id, scope_sha256, identity_sha256, *, strict=False):
    """Select the unique retained root-to-leaf chain, including ancestors."""
    by_id = {row["work_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("application continuation has duplicate work")
    original = by_id.get(task_id)
    if original is None:
        raise ValueError("application task is unavailable")
    spec = original["spec_payload"]
    source = spec["source"]
    identity = {key: source[key] for key in ("tenant_id", "installation_id", "principal_id")}
    if identity_sha256(identity) != scope_sha256:
        raise ValueError("application source differs")
    current, ancestors = original, set()
    fields = ("execution_id", "source", "source_ingress_sha256", "grant",
              "owner_principal_id", "principal_binding_id", "audience", "episode_id", "contract", "task")
    def follows(child, parent):
        return (type(child.get("generation")) is int
                and type(parent.get("generation")) is int
                and child["generation"] == parent["generation"] + 1
                and all(child.get(key) == parent.get(key) == spec.get(key) for key in fields))
    while current["spec_payload"].get("parent_task_id") in by_id:
        if current["work_id"] in ancestors:
            raise ValueError("application continuation cycle")
        ancestors.add(current["work_id"])
        parent = by_id[current["spec_payload"]["parent_task_id"]]
        if not follows(current["spec_payload"], parent["spec_payload"]):
            raise ValueError("application continuation authority differs")
        current = parent
    if type(current["spec_payload"].get("generation")) is not int or current["spec_payload"]["generation"] != 1:
        raise ValueError("application continuation root is missing")
    chain, visited = [], set()
    while True:
        if current["work_id"] in visited:
            raise ValueError("application continuation cycle")
        visited.add(current["work_id"])
        chain.append(current)
        if current["stop_requested_at"] is not None or current["state"] == "stopped":
            return chain
        children = []
        for row in rows:
            child = row["spec_payload"]
            if child.get("parent_task_id") != current["work_id"]:
                continue
            if not follows(child, current["spec_payload"]):
                continue
            children.append(row)
        if len(children) > 1:
            raise ValueError("application continuation is ambiguous")
        if not children:
            if task_id not in visited or (strict and len(visited) != len(rows)):
                raise ValueError("application continuation is not one validated chain")
            return chain
        current = children[0]


def _execution_completion_state(rows, task_id, scope_sha256, identity_sha256):
    """A typed same-execution resume may finish retained work; a stop still wins."""
    chain = _execution_chain(rows, task_id, scope_sha256, identity_sha256)
    if any(row["stop_requested_at"] is not None or row["state"] == "stopped" for row in chain):
        return "cancelled"
    return "ready" if chain[-1]["state"] in {"succeeded", "verified"} else "waiting"


def _execution_usage(chain, journal, source_turn, *, active=False):
    """Read each real work journal once; retain unknown and old terminal payloads."""
    from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
    from eva.application.conversation import ConversationModelUsage
    from eva.contracts.canonical_json import canonical_sha256
    from eva.contracts.delegated_work import DelegatedWorkUsage
    usages, terminal = [], []
    for index, row in enumerate(chain):
        usage = BackgroundModelJournal(journal, source_turn, work_id=row["work_id"]).model_usage(source_turn)
        if usage.measurement != "measured":
            raise ValueError("execution journal usage is incomplete")
        usages.append(usage)
        if active and index == len(chain) - 1:
            continue
        payload, digest = row["usage_payload"], row["usage_sha256"]
        if payload is None or digest is None or bytes(digest).hex() != canonical_sha256(payload):
            raise ValueError("execution terminal usage is not retained")
        retained = DelegatedWorkUsage.model_validate(payload)
        if (retained.measurement == "unmeasured"
                or any(getattr(retained, k) is None for k in
                       ("completed_model_responses", "tool_calls", "input_tokens", "output_tokens", "total_tokens"))
                or retained.input_tokens != usage.input_tokens or retained.output_tokens != usage.output_tokens):
            raise ValueError("execution terminal usage is incomplete or differs")
        terminal.append(retained)
    model = ConversationModelUsage.aggregate(tuple(usages))
    if active:
        return model, None
    fields = ("completed_model_responses", "tool_calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd_micros")
    totals = {key: (sum(getattr(value, key) for value in terminal)
                    if all(getattr(value, key) is not None for value in terminal) else None)
              for key in fields}
    retained = DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
        measurement="measured" if all(value.measurement == "measured" for value in terminal) else "partial", **totals)
    return model, retained.model_dump(mode="json")


def _registered_lifecycle_origin(origin, spec, turn, scope_sha256):
    """Recognize a pre-admission technical record, never a fictitious initiative."""
    import hashlib
    from eva.contracts.canonical_json import canonical_sha256
    if not isinstance(origin, dict):
        return False
    if (origin.get('kind') != 'registered_lifecycle_technical_probe'
            or origin.get('stage') != 'construction'
            or any(origin.get(k) is not False for k in (
                'ownerAuthoredText', 'selfChosenInitiative', 'foreground_contact_used'))):
        return False
    for key in ('registration_sha256', 'resources_sha256', 'runtime_admission_sha256',
                'input_sha256', 'scope_sha256'):
        value = origin.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            return False
    source = spec['source']
    actor = origin.get('actor', {})
    identity = origin.get('identity', {})
    return (origin['scope_sha256'] == scope_sha256
        and canonical_sha256({k: source[k] for k in ('tenant_id', 'installation_id', 'principal_id')}) == scope_sha256
        and all(actor.get(k) == source[k] for k in ('tenant_id', 'installation_id', 'principal_id'))
        and actor.get('principal_id') == spec['owner_principal_id']
        and actor.get('principal_binding_id') == spec['principal_binding_id']
        and identity.get('task_id') == spec['work_id']
        and all(identity.get(k) == source[k] for k in ('conversation_id', 'message_id'))
        and identity.get('source_ingress_sha256') == spec['source_ingress_sha256']
        and origin.get('spec_sha256') == canonical_sha256(spec)
        and origin.get('task_sha256') == hashlib.sha256(spec['task'].encode()).hexdigest()
        and origin.get('project_id') == turn.project_id
        and turn.project_id in (
            'component-work-evaluation:' + origin['registration_sha256'] + ':construction',
            'component-work-strategy:' + origin['registration_sha256']))


# Executed inside the existing core so database credentials stay there. Only
# status, stop and lock metadata are read; no conversation content is returned.
_READINESS = (inspect.getsource(_execution_chain) + inspect.getsource(_execution_completion_state)
              + inspect.getsource(_execution_usage) + inspect.getsource(_registered_lifecycle_origin)) + r'''
import json,sys,sqlalchemy as sa
from uuid import UUID
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline,TimelineScope
from eva.adapters.postgres.current_catalog import delegated_work_runs as work
from eva.contracts.canonical_json import canonical_sha256
request=json.loads(sys.argv[1]);s=RuntimeSettings.from_environment()
e=sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn))
try:
 scope=PostgresOperationalTimeline(e,TimelineScope(UUID(str(s.dashboard_tenant_id)),UUID(str(s.dashboard_installation_id))))
 with e.begin() as c:
  scope._scope(c)
  original=c.execute(sa.select(work.c.spec_payload).where(work.c.work_id==request['task_id'])).scalar_one_or_none()
  if original is None:raise ValueError('application task is unavailable')
  rows=c.execute(sa.select(work.c.work_id,work.c.state,work.c.stop_requested_at,work.c.spec_payload,work.c.retention_until,work.c.usage_payload,work.c.usage_sha256).where(work.c.spec_payload['execution_id'].astext==original['execution_id'])).mappings().all()
  state=_execution_completion_state(rows,request['task_id'],request['scope_sha256'],canonical_sha256)
  chain=_execution_chain(rows,request['task_id'],request['scope_sha256'],canonical_sha256,
   strict=bool(request.get('include_binding') or request.get('include_usage') or request.get('include_active_usage')))
  root,leaf=chain[0]['spec_payload'],chain[-1]
  if request.get('include_binding') or request.get('include_usage') or request.get('include_active_usage'):
   from datetime import UTC,datetime
   if any(row['retention_until'] is None or row['retention_until']<=datetime.now(UTC) for row in chain):state='cancelled'
  if request.get('include_active_usage'):
   if state=='cancelled' or leaf['work_id']!=request['task_id'] or leaf['state']!='call_started':
    raise ValueError('construction is not a current active call')
   state='ready'
  source_turn=None
  if state=='ready' and original.get('contract')=='DelegatedInitiativeDigitalWork/v1':
   from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
   from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
   from datetime import UTC,datetime
   try:_,source_turn=ProcedureReferenceProviderFactory.native_authority(e,s).reviewed_work_current(request['task_id'],now=datetime.now(UTC))
   except (InitiativeMandateDenied,RuntimeError):state='cancelled'
  if state=='ready' and request.get('require_idle',True):
   leased=c.scalar(sa.select(sa.func.count()).select_from(work).where(work.c.lease_owner.is_not(None),work.c.lease_expires_at>sa.func.now()))
   locks=c.scalar(sa.text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid()"))
   state='waiting' if leased or locks else 'ready'
 if state=='ready' and (request.get('include_binding') or request.get('include_usage') or request.get('include_active_usage')) and source_turn is None:
  from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
  from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
  from eva.providers.mcp_autonomy import _SourceTurnLoader
  from eva.adapters.postgres.operational_timeline import external_timeline_scope
  from types import SimpleNamespace
  store=PostgresDelegatedWorkStore(e,TimelineScope(UUID(str(s.dashboard_tenant_id)),UUID(str(s.dashboard_installation_id))))
  loaded_by_id={}
  for row in chain:
   loaded=store.load(row['work_id'],owner_principal_id=UUID(root['owner_principal_id']),principal_binding_id=UUID(root['principal_binding_id']))
   if loaded.spec.sha256!=canonical_sha256(row['spec_payload']):raise ValueError('reviewed retained spec differs')
   if loaded.state!=row['state'] or loaded.stop_requested_at is not None:raise ValueError('reviewed work changed or stopped')
   if loaded.retention_until<=datetime.now(UTC):raise ValueError('reviewed work retention expired')
   loaded_by_id[row['work_id']]=loaded
  loaded=loaded_by_id[leaf['work_id']]
  if request.get('include_active_usage'):
   from eva.adapters.postgres.delegated_work import DelegatedWorkCall
   store.authorize_tool_dispatch(DelegatedWorkCall(loaded.spec.work_id,loaded.storage_episode_id,loaded.call_id,loaded.spec))
  actual_scope=external_timeline_scope(root['source']['tenant_id'],root['source']['installation_id'],'reviewed source scope required')
  journal=PostgresConversationJournal(e,actual_scope)
  source_turn=_SourceTurnLoader(e,SimpleNamespace(scope=actual_scope,authorize_turn=journal.ensure_running))(root['source'])
  if canonical_sha256(PostgresConversationJournal._ingress_data(source_turn))!=root['source_ingress_sha256']:
   raise ValueError('reviewed source ingress differs')
  from eva.adapters.postgres.operational_timeline import external_timeline_scope
  PostgresConversationJournal(e,external_timeline_scope(source_turn.tenant_id,source_turn.installation_id,'reviewed source scope required')).ensure_running(source_turn)
 payload={'state':state}
 if request.get('include_binding'):
  from datetime import UTC,datetime
  payload.update(contract='EvaReviewedComponentWorkAuthority/v1',task_id=request['task_id'],
   scope_sha256=request['scope_sha256'],spec_sha256=canonical_sha256(original),
   source_ingress_sha256=original['source_ingress_sha256'])
 if request.get('include_usage') or request.get('include_active_usage'):
  if state!='ready':raise ValueError('original work is not terminal and current')
  from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
  from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
  from eva.adapters.postgres.operational_timeline import external_timeline_scope
  if source_turn is None:raise ValueError('original source authority unavailable')
  if canonical_sha256(PostgresConversationJournal._ingress_data(source_turn))!=root['source_ingress_sha256']:
   raise ValueError('original accounting ingress differs')
  journal=PostgresConversationJournal(e,external_timeline_scope(source_turn.tenant_id,source_turn.installation_id,'original accounting scope required'))
  model_usage,terminal_usage=_execution_usage(chain,journal,source_turn,active=bool(request.get('include_active_usage')))
  contact_usage=journal.model_usage(source_turn)
  from eva.adapters.postgres.operational_timeline import operational_events
  contact_identity=journal._stop_identity(source_turn.principal_id,source_turn.conversation_id,source_turn.message_id)
  with e.begin() as c:
   journal._scope(c)
   contact_data=operational_events.c.payload['data']
   contact_admissions=c.scalar(sa.select(sa.func.count()).select_from(operational_events).where(
    operational_events.c.tenant_id==journal._scope_value.tenant_id,
    operational_events.c.installation_id==journal._scope_value.installation_id,
    operational_events.c.event_type=='CONVERSATION_MODEL_ADMITTED',
    *(contact_data[key].astext==item for key,item in contact_identity.items())))
  native_without_contact=(root.get('contract')=='DelegatedInitiativeDigitalWork/v1'
   and contact_usage.measurement=='unmeasured' and contact_admissions==0)
  technical_without_contact=False
  if contact_usage.measurement=='unmeasured' and contact_admissions==0:
   import hashlib
   marker='work-memory:'+hashlib.sha256(chain[0]['work_id'].encode()).hexdigest()
   marker_events=scope.events(marker)
   if len(marker_events)==1 and marker_events[0].event_type=='HOST_EVALUATION_MEMORY_EXCLUDED':
    data=marker_events[0].payload.get('data',{})
    technical_without_contact=(data.get('disposition')=='host_evaluation_excluded'
      and _registered_lifecycle_origin(data.get('origin'),root,source_turn,request['scope_sha256']))
  disposition=('native_origin_without_foreground' if native_without_contact else
   'registered_technical_origin_without_foreground' if technical_without_contact else 'recorded_original_ingress')
  payload.update(contract='EvaReviewedComponentWorkUsage/v1',task_id=request['task_id'],scope_sha256=request['scope_sha256'],
    model_usage=model_usage.to_wire(),contact_usage=None if native_without_contact or technical_without_contact else contact_usage.to_wire(),
    contact_disposition=disposition)
  if request.get('include_active_usage'):
    payload.update(contract='EvaActiveComponentWorkUsage/v1',spec_sha256=canonical_sha256(leaf['spec_payload']),source_ingress_sha256=root['source_ingress_sha256'])
  else:
    payload.update(terminal_usage=terminal_usage,terminal_usage_sha256=canonical_sha256(terminal_usage))
 print(json.dumps(payload))
finally:e.dispose()
'''


class ComponentApplicationRuntime:
    """Trusted host callbacks; model input supplies no commands or resources."""

    def __init__(self, config, *, run=subprocess.run):
        if (not isinstance(config, dict) or set(config) != {"core_container", "scope_sha256"}
                or not isinstance(config["core_container"], str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", config["core_container"])
                or not isinstance(config["scope_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", config["scope_sha256"])):
            raise ValueError("invalid trusted application runtime")
        self.config, self.run = dict(config), run

    def ready_to_apply(self, task_id):
        return self._state(task_id, require_idle=True)

    def reviewed_work_current(self, task_id):
        """Check original completion/stop during isolated review work.

        An evaluation child holds its ordinary lease. Application retains the
        separate ready_to_apply global idle checks.
        """
        return self._state(task_id, require_idle=False)

    def reviewed_work_binding(self, task_id):
        return self._state(task_id, require_idle=False, include_binding=True)

    def reviewed_work_usage(self, task_id):
        return self._state(task_id, require_idle=False, include_usage=True)

    def active_work_usage(self, task_id):
        return self._state(task_id, require_idle=False, include_active_usage=True)

    def _state(self, task_id, *, require_idle, include_binding=False, include_usage=False, include_active_usage=False):
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 192 or "\x00" in task_id:
            raise ValueError("invalid application task")
        request = {"task_id": task_id, "scope_sha256": self.config["scope_sha256"]}
        if not require_idle:
            request["require_idle"] = False
        if include_binding:
            request["include_binding"] = True
        if include_usage:
            request["include_usage"] = True
        if include_active_usage:
            request["include_active_usage"] = True
        result = self.run(["docker", "exec", "-i", self.config["core_container"], "python", "-",
                          json.dumps(request)],
                         input=_READINESS, text=True, capture_output=True, timeout=30)
        if result.returncode != 0 or len(result.stdout) > 4096:
            raise ValueError("application task readiness unavailable")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("state") not in {"ready", "waiting", "cancelled"}:
            raise ValueError("application task readiness invalid")
        if include_usage or include_active_usage:
            from eva.application.conversation import ConversationModelUsage
            from eva.contracts.delegated_work import DelegatedWorkUsage
            from eva.contracts.canonical_json import canonical_sha256
            expected = {"state", "contract", "task_id", "scope_sha256", "model_usage",
                        "terminal_usage", "terminal_usage_sha256","contact_usage","contact_disposition"}
            if include_active_usage:
                expected -= {"terminal_usage", "terminal_usage_sha256"}
                expected |= {"spec_sha256", "source_ingress_sha256"}
            if (set(payload) != expected or payload["state"] != "ready"
                    or payload["contract"] != ("EvaActiveComponentWorkUsage/v1" if include_active_usage else "EvaReviewedComponentWorkUsage/v1")
                    or payload["task_id"] != task_id
                    or payload["scope_sha256"] != self.config["scope_sha256"]
                    or (not include_active_usage and canonical_sha256(payload["terminal_usage"]) != payload["terminal_usage_sha256"])):
                raise ValueError("reviewed original usage binding differs")
            if payload["contact_usage"] is None:
                if payload["contact_disposition"] not in {"native_origin_without_foreground", "registered_technical_origin_without_foreground"}:
                    raise ValueError("original contact accounting absent")
            elif (payload["contact_disposition"] != "recorded_original_ingress"
                    or ConversationModelUsage.from_wire(payload["contact_usage"]).measurement != "measured"):
                raise ValueError("original contact accounting incomplete")
            model = ConversationModelUsage.from_wire(payload["model_usage"])
            if include_active_usage:
                if (model.measurement != "measured" or model.model_attempts < 1
                        or any(not isinstance(payload[k],str) or not re.fullmatch(r"[0-9a-f]{64}",payload[k])
                               for k in ("spec_sha256","source_ingress_sha256"))):
                    raise ValueError("active construction usage is incomplete")
                return payload
            terminal = DelegatedWorkUsage.model_validate(payload["terminal_usage"])
            if (model.measurement != "measured" or terminal.measurement == "unmeasured"
                    or any(getattr(terminal,k) is None for k in
                           ("completed_model_responses","tool_calls","input_tokens","output_tokens","total_tokens"))
                    or any(getattr(model,k) != getattr(terminal,k) for k in ("input_tokens","output_tokens"))):
                raise ValueError("original work usage is incomplete or differs")
            return payload
        if include_binding:
            expected = {"state", "contract", "task_id", "scope_sha256",
                        "spec_sha256", "source_ingress_sha256"}
            if (set(payload) != expected
                    or payload["contract"] != "EvaReviewedComponentWorkAuthority/v1"
                    or payload["task_id"] != task_id
                    or payload["scope_sha256"] != self.config["scope_sha256"]
                    or any(not isinstance(payload[k], str)
                           or not re.fullmatch(r"[0-9a-f]{64}", payload[k])
                           for k in ("spec_sha256", "source_ingress_sha256"))):
                raise ValueError("reviewed original work binding differs")
            return payload
        return payload["state"]

    def apply_release(self, release):
        if release.get("kind") == "saved_artifact":
            return self._apply_saved_artifact(release)
        from tools.installation_runtime_update import update_runtime
        from eva.operations.component_evaluation import _capture
        def command(argv, *, deadline=15, limit=1024 * 1024):
            result = _capture(argv, deadline=deadline, limit=limit)
            if (result["exit_code"] != 0 or result["timed_out"]
                    or result["stdout_truncated"] or result["stderr_truncated"]):
                raise RuntimeError("reviewed update command did not complete")
            return result["stdout"]
        return update_runtime(project=release["project"], installed=release["installed"],
                              candidate=release["candidate"], receipt=release["receipt"], run=command)

    def _apply_saved_artifact(self, release):
        from eva.operations.reviewed_component_application import _save
        from eva.operations.saved_artifact_application import receipt_confirms_application
        # Reserve before the only effect-capable core call; never retry dispatch.
        receipt = release["receipt"]
        _save(receipt, {"contract": "EvaSavedArtifactApplication/v1",
            "status": "dispatch_result_unknown", "task_id": release["task_id"],
            "publication": release["publication"]}, initial=True)
        payload = {key: release[key] for key in ("publication", "candidate_content", "task_id")}
        payload["scope_sha256"] = self.config["scope_sha256"]
        command = "from eva.operations.saved_artifact_application import runtime_apply;import json,sys;print(json.dumps(runtime_apply(json.loads(sys.stdin.read()))))"
        result = self.run(["docker", "exec", "-i", self.config["core_container"], "python", "-c", command],
            input=json.dumps(payload), text=True, capture_output=True)
        if result.returncode != 0 or len(result.stdout) > 16384:
            raise RuntimeError("saved artifact application is unconfirmed; inspect retained receipt")
        observed = json.loads(result.stdout)
        if not receipt_confirms_application(observed, release["publication"], release["task_id"]):
            raise RuntimeError("saved artifact application proof differs")
        _save(receipt, observed)
        return observed


def configured_component_applications(config):
    from eva.operations.reviewed_component_application import ReviewedComponentApplications
    if not isinstance(config, dict) or set(config) != {"runtime", "queue"}:
        raise ValueError("invalid reviewed application configuration")
    runtime = ComponentApplicationRuntime(config["runtime"])
    return ReviewedComponentApplications(config["queue"], ready_to_apply=runtime.ready_to_apply,
                                         apply_release=runtime.apply_release)
