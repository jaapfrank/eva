"""Read-only document worker liveness and scoped database readiness, not progress."""
from pathlib import Path
import json
import os
import sys


def check_process(pid=1):
    root = Path("/proc") / str(pid)
    args = (root / "cmdline").read_bytes().split(b"\0")
    if args[:3] != [b"/usr/local/bin/python", b"-m", b"eva.bootstrap.document_goal_worker"]:
        raise RuntimeError("unexpected worker process")
    status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines() if ":" in line)
    if status["State"].split()[0] not in {"R", "S", "D"}:
        raise RuntimeError("worker stopped or dead")


def check():
    check_process()
    import sqlalchemy as sa
    from eva.adapters.credentials.runtime_files import read_runtime_secret_file, required_environment
    from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
    from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
    from eva.providers.document_goal_plan import DocumentGoalPlan
    pool_file = os.environ.get("EVA_DOCUMENT_TASK_POOL_FILE", "").strip()
    if pool_file:
        from eva.providers.document_task_input import DocumentTaskPool
        pool = DocumentTaskPool.from_json(read_runtime_secret_file(pool_file, setting_name="EVA_DOCUMENT_TASK_POOL_FILE"))
    plan_file = os.environ.get("EVA_DOCUMENT_GOAL_PLAN_FILE", "").strip() if pool_file else required_environment("EVA_DOCUMENT_GOAL_PLAN_FILE")
    plan = (DocumentGoalPlan.from_json(read_runtime_secret_file(
        plan_file, setting_name="EVA_DOCUMENT_GOAL_PLAN_FILE")) if plan_file else None)
    dsn = read_runtime_secret_file(required_environment("EVA_DATABASE_DSN_FILE"), setting_name="EVA_DATABASE_DSN_FILE")
    engine = sa.create_engine(sqlalchemy_postgres_dsn(dsn), poolclass=sa.pool.NullPool,
        connect_args={"connect_timeout": 2, "options": "-c default_transaction_read_only=on -c statement_timeout=2000"})
    try:
        store = PostgresDurableGoalStore(engine,
            tenant_id=required_environment("EVA_RUNTIME_TENANT_ID"),
            installation_id=required_environment("EVA_RUNTIME_INSTALLATION_ID"))
        plans = []
        if pool_file:
            for slot in pool.slots:
                claim=store.document_slot(slot.slot_id)
                if claim is not None:
                    if claim['pool_sha256']!=pool.sha256:
                        raise RuntimeError("configured pool differs")
                    plans.append(DocumentGoalPlan.from_json(json.dumps(claim['plan'])))
        if plan is not None:
            plans.append(plan)
        for current in plans:
            state = store.state(current.goal_id, current.owner_principal_id)
            if state is not None and state.spec.sha256 != current.goal_sha256:
                raise RuntimeError("configured goal differs")
        # An absent goal is valid before first admission; terminal outcomes do not
        # determine worker health. The real scoped query must always succeed.
    finally:
        engine.dispose()
    check_process()


if __name__ == "__main__":
    try:
        check()
    except Exception:
        print("document worker not ready", file=sys.stderr)
        raise SystemExit(1)
    print("document worker live; plan and scoped database ready")
