"""Independent continuation fail-closed regressions on disposable PostgreSQL."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import pytest
import sqlalchemy as sa

from tests.postgres.test_runtime_repair_host import database, postgres_engine, native_digital, costless_native, rpc
from tests.postgres.test_native_repair_rate_limit_recovery import rejected
from eva.bootstrap.native_initiative_work import resume_one_rate_limited_repair
from eva.adapters.postgres.delegated_work import delegated_work_runs, DelegatedWorkConflict
from eva.contracts.canonical_json import canonical_sha256


@pytest.mark.parametrize("change", ["parent_stop", "child_stop", "parent_expired", "child_expired", "child_source", "branch"])
def test_stage_never_borrows_success_from_invalid_execution(native_digital, change):
    f, parent = rejected(native_digital)
    assert resume_one_rate_limited_repair(f.service) == "repair_resumed"
    event = f.auth.timeline.events(f.claim.thread_id)[-1].payload["data"]
    child = f.store.load(event["work_id"], **f.service._owner)
    w = delegated_work_runs.c
    now = f.service.now()
    with f.store.engine.begin() as c:
        if change.endswith("_stop"):
            target = parent if change.startswith("parent") else child
            values = dict(state="stopped", completed_at=now, error_code=None, error_fingerprint=None)
            if target.call_id is not None:
                values["stop_requested_at"] = now
            c.execute(sa.update(delegated_work_runs).where(w.work_id == target.spec.work_id).values(**values))
        elif change.endswith("_expired"):
            target = parent if change.startswith("parent") else child
            created = c.scalar(sa.select(w.created_at).where(w.work_id == target.spec.work_id))
            c.execute(sa.update(delegated_work_runs).where(w.work_id == target.spec.work_id).values(retention_until=created))
            f.service.clock = lambda: now + timedelta(seconds=1)
        elif change == "child_source":
            payload = deepcopy(child.spec.model_dump(mode="json"))
            payload["source"]["message_id"] += "-forged"
            c.execute(sa.update(delegated_work_runs).where(w.work_id == child.spec.work_id).values(spec_payload=payload, spec_sha256=bytes.fromhex(canonical_sha256(payload))))
        else:
            row = dict(c.execute(sa.select(delegated_work_runs).where(w.work_id == child.spec.work_id)).mappings().one())
            row["work_id"] += "-branch"
            row["attention_id"] = uuid4()
            row["spec_payload"] = deepcopy(row["spec_payload"])
            row["spec_payload"]["work_id"] = row["work_id"]
            row["spec_sha256"] = bytes.fromhex(canonical_sha256(row["spec_payload"]))
            c.execute(sa.insert(delegated_work_runs).values(**row))
    with pytest.raises((ValueError, DelegatedWorkConflict)):
        rpc(f, event["rate_limit_evidence"]["incident_id"], "stage_metadata", task_id=parent.spec.work_id, require_running=False)
