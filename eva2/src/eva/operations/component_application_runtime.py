"""Apply an independently reviewed update after its Eva task releases execution."""
from __future__ import annotations

import json
import inspect
import re
import subprocess

def _execution_completion_state(rows, task_id, scope_sha256, identity_sha256):
    """A typed same-execution resume may finish retained work; a stop still wins."""
    by_id = {row["work_id"]: row for row in rows}
    original = by_id.get(task_id)
    if original is None:
        raise ValueError("application task is unavailable")
    spec = original["spec_payload"]
    source = spec["source"]
    identity = {key: source[key] for key in ("tenant_id", "installation_id", "principal_id")}
    if identity_sha256(identity) != scope_sha256:
        raise ValueError("application source differs")
    current, visited = original, set()
    while True:
        if current["work_id"] in visited:
            raise ValueError("application continuation cycle")
        visited.add(current["work_id"])
        if current["stop_requested_at"] is not None or current["state"] == "stopped":
            return "cancelled"
        children = []
        for row in rows:
            child = row["spec_payload"]
            if child.get("parent_task_id") != current["work_id"]:
                continue
            if (child.get("generation") != current["spec_payload"]["generation"] + 1
                    or any(child.get(key) != spec.get(key) for key in (
                        "execution_id", "source", "source_ingress_sha256", "grant",
                        "owner_principal_id", "principal_binding_id", "audience", "episode_id"))):
                continue
            children.append(row)
        if len(children) > 1:
            raise ValueError("application continuation is ambiguous")
        if not children:
            return "ready" if current["state"] in {"succeeded", "verified"} else "waiting"
        current = children[0]


# Executed inside the existing core so database credentials stay there. Only
# status, stop and lock metadata are read; no conversation content is returned.
_READINESS = inspect.getsource(_execution_completion_state) + r'''
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
  rows=c.execute(sa.select(work.c.work_id,work.c.state,work.c.stop_requested_at,work.c.spec_payload).where(work.c.spec_payload['execution_id'].astext==original['execution_id'])).mappings().all()
  state=_execution_completion_state(rows,request['task_id'],request['scope_sha256'],canonical_sha256)
  if state=='ready':
   leased=c.scalar(sa.select(sa.func.count()).select_from(work).where(work.c.lease_owner.is_not(None),work.c.lease_expires_at>sa.func.now()))
   locks=c.scalar(sa.text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid()"))
   state='waiting' if leased or locks else 'ready'
 print(json.dumps({'state':state}))
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
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 192 or "\x00" in task_id:
            raise ValueError("invalid application task")
        result = self.run(["docker", "exec", "-i", self.config["core_container"], "python", "-",
                          json.dumps({"task_id": task_id, "scope_sha256": self.config["scope_sha256"]})],
                         input=_READINESS, text=True, capture_output=True, timeout=30)
        if result.returncode != 0 or len(result.stdout) > 1024:
            raise ValueError("application task readiness unavailable")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("state") not in {"ready", "waiting", "cancelled"}:
            raise ValueError("application task readiness invalid")
        return payload["state"]

    @staticmethod
    def apply_release(release):
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


def configured_component_applications(config):
    from eva.operations.reviewed_component_application import ReviewedComponentApplications
    if not isinstance(config, dict) or set(config) != {"runtime", "queue"}:
        raise ValueError("invalid reviewed application configuration")
    runtime = ComponentApplicationRuntime(config["runtime"])
    return ReviewedComponentApplications(config["queue"], ready_to_apply=runtime.ready_to_apply,
                                         apply_release=runtime.apply_release)
