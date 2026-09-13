"""Host-plan admission for the document goal provider.

Effect ports are deliberately not composed until the atomic provider and its
durable, operation-bound receipt interface have been independently verified.
"""

from __future__ import annotations

from collections.abc import Callable

from eva.application.durable_goals import DurableGoalError, DurableGoalSpec
from eva.providers.document_goal_plan import DocumentGoalPlan


class DocumentGoalAuthority:
    """Reuse current MCP owner authority; never construct a request principal."""

    def __init__(self, *, store, authority, read_plan: Callable[[], str],
                 tool_configuration_sha256: str) -> None:
        self._store = store
        self._authority = authority
        self._read_plan = read_plan
        self._tool_configuration_sha256 = tool_configuration_sha256

    def _plan(self, goal: DurableGoalSpec) -> DocumentGoalPlan:
        plan = DocumentGoalPlan.from_json(self._read_plan())
        plan.require_host_binding(
            goal,
            owner_principal_id=self._authority.owner_principal_id,
            tenant_id=str(self._authority.scope.tenant_id),
            installation_id=str(self._authority.scope.installation_id),
            authority_configuration_sha256=self._authority.configuration_sha256,
            tool_configuration_sha256=self._tool_configuration_sha256,
        )
        existing = self._store.document_plan_binding(goal)
        if existing is not None and existing != (
            plan.sha256, plan.authority_configuration_sha256
        ):
            raise DurableGoalError("document plan changed after durable binding")
        return plan

    def authorize_effect(self, goal: DurableGoalSpec) -> DocumentGoalPlan:
        plan = self._plan(goal)
        state = self._store.state(goal.goal_id, goal.owner_id)
        if state is None or state.spec != goal or state.status != "waiting":
            raise DurableGoalError("document goal cannot start another effect")
        # Existing authority rereads the safe host mandate and its durable state.
        self._authority.authorize_current(phase="execute", operation_kind="effect")
        self._store.bind_document_plan(
            goal, plan.sha256, plan.authority_configuration_sha256
        )
        return plan

    def authorize_readback(self, goal: DurableGoalSpec) -> DocumentGoalPlan:
        plan = self._plan(goal)
        if self._store.document_plan_binding(goal) is None:
            raise DurableGoalError("document goal has no admitted execution binding")
        # The established owner policy permits evidence-only recovery of an
        # earlier effect after revocation. This method must never dispatch it.
        self._authority.authorize_current(phase="observe", operation_kind="effect")
        return plan
