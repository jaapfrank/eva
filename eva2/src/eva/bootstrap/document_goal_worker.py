"""Opt-in host composition for one exact document goal; no new worker loop."""

from eva.adapters.credentials.runtime_files import read_runtime_secret_file, required_environment
from eva.application.durable_goals import DurableGoalWorker
from eva.operations.durable_goal_worker import main as run_loop
from eva.providers.document_goal_plan import DocumentGoalPlan
from eva.providers.document_goals import build_document_goal_ports
from eva.providers.document_task_input import DocumentTaskPool
import os
import json


def main() -> int:
    pool_file = os.environ.get("EVA_DOCUMENT_TASK_POOL_FILE", "").strip()
    plan_file = os.environ.get("EVA_DOCUMENT_GOAL_PLAN_FILE", "").strip() if pool_file else required_environment("EVA_DOCUMENT_GOAL_PLAN_FILE")
    def pool_claims(store):
        pool=DocumentTaskPool.from_json(read_runtime_secret_file(pool_file, setting_name="EVA_DOCUMENT_TASK_POOL_FILE"))
        claims=[]
        for slot in pool.slots:
            claim=store.document_slot(slot.slot_id)
            if claim is not None:
                if claim['pool_sha256']!=pool.sha256:
                    raise RuntimeError("document pool binding changed")
                claims.append(claim)
        return claims


    def legacy_plan():
        if not plan_file:
            return None
        return DocumentGoalPlan.from_json(read_runtime_secret_file(
            plan_file, setting_name="EVA_DOCUMENT_GOAL_PLAN_FILE"))

    def select_goal(store, _observed_at):
        goals=[]
        identities=set()
        if pool_file:
            from eva.providers.document_goal_plan import PreparedDocumentGoal
            for claim in pool_claims(store):
                plan=DocumentGoalPlan.from_json(json.dumps(claim['plan']))
                definition=PreparedDocumentGoal.from_json(json.dumps(claim['definition']),plan)
                identity=(plan.goal_id,plan.owner_principal_id)
                if identity in identities:
                    raise RuntimeError("document goal has multiple plan bindings")
                identities.add(identity)
                state=store.state(plan.goal_id,plan.owner_principal_id)
                if state is None or (state.status=="waiting" and state.spec==definition.goal):
                    goals.append(definition.goal if state is None else state.spec)
        plan=legacy_plan()
        if plan is not None:
            if (plan.goal_id,plan.owner_principal_id) in identities:
                raise RuntimeError("document goal has multiple plan bindings")
            state=store.state(plan.goal_id,plan.owner_principal_id)
            if state is not None and state.status=="waiting" and state.spec.sha256==plan.goal_sha256:
                goals.append(state.spec)
        return tuple(goals)

    def worker_factory(engine, store, goal, tenant_id, installation_id):
        from eva.providers.mcp_owner_authority import McpOwnerAuthority
        from eva.providers.mcp_autonomy import _SourceTurnLoader
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        authority=McpOwnerAuthority(engine,required_environment("EVA_MCP_AUTONOMY_MANDATE_FILE"),
            os.environ.get("EVA_MCP_MANAGED_ENDPOINTS_FILE") or None)
        matches=[c for c in pool_claims(store) if c['plan']['goal_id']==goal.goal_id] if pool_file else []
        legacy=legacy_plan()
        is_legacy=legacy is not None and legacy.goal_id==goal.goal_id and legacy.owner_principal_id==goal.owner_id
        if len(matches)>1 or (matches and is_legacy) or (not matches and not is_legacy):
            raise RuntimeError("document goal has no unique host plan")
        use_pool=bool(matches)
        def read_bound_plan():
            matches=[c for c in pool_claims(store) if c['plan']['goal_id']==goal.goal_id]
            if len(matches)!=1:raise RuntimeError("document task plan is unavailable")
            claim=matches[0]
            turn=_SourceTurnLoader(engine,authority)(claim['source'])
            authority.authorize_turn(turn)
            PostgresConversationJournal(engine,authority.scope).ensure_running(turn)
            if turn.project_id!=claim['definition']['conversation_project_id']:
                raise RuntimeError("document source project changed")
            return json.dumps(claim['plan'])
        if use_pool and store.state(goal.goal_id,goal.owner_id) is None:
            plan=DocumentGoalPlan.from_json(read_bound_plan())
            authority.authorize_current(phase="execute",operation_kind="effect")
            store.admit(goal)
            store.bind_document_plan(goal,plan.sha256,authority.configuration_sha256)
            store.schedule_wake(goal)
        ports = build_document_goal_ports(
            engine=engine, store=store, goal=goal, plan_file=plan_file,
            tenant_id=tenant_id, installation_id=installation_id,
            read_bound_plan=read_bound_plan if use_pool else None,
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
