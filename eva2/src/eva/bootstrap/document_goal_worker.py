"""Opt-in host composition for one exact document goal; no new worker loop."""

from eva.adapters.credentials.runtime_files import read_runtime_secret_file, required_environment
from eva.application.durable_goals import DurableGoalWorker
from eva.operations.durable_goal_worker import main as run_loop
from eva.providers.document_goal_plan import DocumentGoalPlan
from eva.providers.document_goals import build_document_goal_ports


def main() -> int:
    plan_file = required_environment("EVA_DOCUMENT_GOAL_PLAN_FILE")

    def select_goal(store, _observed_at):
        plan = DocumentGoalPlan.from_json(read_runtime_secret_file(
            plan_file, setting_name="EVA_DOCUMENT_GOAL_PLAN_FILE"
        ))
        state = store.state(plan.goal_id, plan.owner_principal_id)
        if state is None or state.status != "waiting" or state.spec.sha256 != plan.goal_sha256:
            return ()
        return (state.spec,)

    def worker_factory(engine, store, goal, tenant_id, installation_id):
        ports = build_document_goal_ports(
            engine=engine, store=store, goal=goal, plan_file=plan_file,
            tenant_id=tenant_id, installation_id=installation_id,
        )
        return DurableGoalWorker(store, ports, ports, ports)

    def blocked(store, goal, error, at):
        state = store.state(goal.goal_id, goal.owner_id)
        if state is not None and state.status == "waiting":
            store.mark_needs_help(goal, "Document provider blocked: " + type(error).__name__, at)
            return "needs_help"
        return "missing" if state is None else state.status

    return run_loop(select_goals=select_goal, worker_factory=worker_factory, failure_handler=blocked)


if __name__ == "__main__":
    raise SystemExit(main())
