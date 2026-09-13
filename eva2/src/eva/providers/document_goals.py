"""Document goal ports over the existing verified MCP effect pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from eva.adapters.postgres.operational_timeline import CheckpointConflict, TimelineNotFound
from eva.application.durable_goals import (
    DurableGoalError, DurableGoalSpec, DurableGoalWorker, GoalArtifact, GoalEvidence,
)
from eva.application.orchestration import ToolRequest, VerifiedToolOrchestrator
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


class DocumentGoalPorts:
    """One host-bound project; no ordinary conversation identity or new executor."""

    def __init__(self, *, goal, store, guard, timeline, adapter, spec) -> None:
        self.goal = goal
        self.store = store
        self.guard = guard
        self.timeline = timeline
        self.adapter = adapter
        self.spec = spec
        proof_fields = {"operation_id", "request_binding_sha256", "path", "content"}
        if (spec.operation_kind != "effect"
                or {binding.observed_name for binding in spec.proof_bindings} != proof_fields
                or any(binding.argument_name != binding.observed_name for binding in spec.proof_bindings)):
            raise DurableGoalError("document provider lacks exact receipt proof bindings")

    def _same_goal(self, goal) -> None:
        if goal != self.goal:
            raise DurableGoalError("document provider crossed its host-bound goal")

    def _request(self, plan, number, previous=None) -> ToolRequest:
        step = plan.step(number, previous)
        arguments = {
            "operation_id": step.operation_id,
            "request_binding_sha256": step.request_binding_sha256,
            # The host owns one persistent journal for this exact two-step project.
            "expected_generation": number - 1,
            "path": step.path.removeprefix("/proof-state/"),
            "content": step.content,
        }
        proof = {name: arguments[name] for name in (
            "operation_id", "request_binding_sha256", "path", "content"
        )}
        return ToolRequest(
            thread_id="document-step:" + step.operation_id,
            operation_id=step.operation_id,
            checkpoint_prefix="document-step:" + step.operation_id,
            tool_name=self.spec.execute_tool_name,
            arguments=arguments,
            expected_proof=proof,
            observation_tool_name=self.spec.observe_tool_name,
            observation_arguments=dict(arguments),
            tool_configuration_sha256=self.spec.configuration_sha256,
        )

    def _admission(self, request, plan):
        return {
            "goal_sha256": self.goal.sha256,
            "plan_sha256": plan.sha256,
            "arguments": request.arguments,
            "expected_proof": request.expected_proof,
            "tool_name": request.tool_name,
            "observation_tool_name": request.observation_tool_name,
            "tool_configuration_sha256": request.tool_configuration_sha256,
        }

    def _existing(self, request, plan):
        try:
            state = self.timeline.load(request.thread_id)
        except TimelineNotFound:
            return None
        events = self.timeline.events(request.thread_id)
        if (not events or events[0].event_type != "DOCUMENT_STEP_ADMITTED"
                or events[0].payload.get("data") != self._admission(request, plan)):
            raise DurableGoalError("durable document request binding differs")
        return state

    def _seal(self, request, plan):
        if self._existing(request, plan) is None:
            try:
                self.timeline.create_thread(
                    thread_id=request.thread_id, checkpoint_id=request.checkpoint_prefix + ":admitted",
                    event_type="DOCUMENT_STEP_ADMITTED", phase="admitted", status="active",
                    occurred_at=self.goal.created_at, data=self._admission(request, plan),
                    operation_id=request.operation_id,
                )
            except CheckpointConflict:
                pass
        if self._existing(request, plan) is None:
            raise DurableGoalError("document request admission is absent")

    def _block(self):
        state = self.store.state(self.goal.goal_id, self.goal.owner_id)
        if state is not None and state.status == "waiting":
            self.store.mark_needs_help(
                self.goal, "Document effect or receipt is uncertain; operator review required",
                datetime.now(UTC).replace(microsecond=0),
            )

    async def _verified(self, request, plan, *, allow_first_dispatch):
        ports = self

        class CurrentProvider:
            async def execute(self, command):
                if not allow_first_dispatch:
                    raise DurableGoalError("document readback may not dispatch")
                await ports.adapter.verify_catalog()
                if ports.guard.authorize_effect(ports.goal) != plan:
                    raise DurableGoalError("document plan changed before dispatch")
                # Admission is not an atomic mandate. This is the last owner/stop
                # check before the existing adapter hands the request to transport.
                return await ports.adapter.execute(command)

            async def observe(self, command, dispatch):
                await ports.adapter.verify_catalog()
                if ports.guard.authorize_readback(ports.goal) != plan:
                    raise DurableGoalError("document plan changed before readback")
                return await ports.adapter.observe(command, dispatch)

        provider = CurrentProvider()
        try:
            state = self._existing(request, plan)
            if state is None:
                raise DurableGoalError("document request was not sealed")
            if state.status == "completed":
                # A local proof is historical. Recheck actual provider receipt and
                # current material before projecting newly available goal output.
                observation = await provider.observe(request, None)
                if observation.disposition != "verified" or observation.observed != request.expected_proof:
                    raise DurableGoalError("document receipt no longer verifies")
            result = await VerifiedToolOrchestrator(self.timeline, provider).run(request)
            if result.status != "verified":
                raise DurableGoalError("document effect was not independently verified")
            return GoalArtifact(
                "document:" + request.operation_id, request.arguments["content"], "text/plain",
                # Literal document content was fixed when the goal was created;
                # this is not the filesystem publication timestamp.
                self.goal.created_at,
            )
        except Exception:
            self._block()
            raise

    async def read(self, condition):
        if condition != self.goal.wake:
            raise DurableGoalError("document wake condition differs")
        plan = self.guard.authorize_effect(self.goal)
        return GoalEvidence(
            condition.provider, condition.resource, {condition.field: condition.expected_value},
            datetime.now(UTC).replace(microsecond=0), plan.sha256,
        )

    async def readback(self, *, goal=None, recipient=None, idempotency_key):
        if goal is not None and recipient is not None:
            raise DurableGoalError("document readback scope is ambiguous")
        plan = self.guard.authorize_readback(self.goal)
        state = self.store.state(self.goal.goal_id, self.goal.owner_id)
        if state is None:
            raise DurableGoalError("document goal is absent")
        if recipient is not None:
            if (recipient != self.goal.delivery_recipient or state.artifact is None
                    or idempotency_key != DurableGoalWorker.delivery_idempotency_key(self.goal, state.artifact)):
                raise DurableGoalError("document delivery binding differs")
            request = self._request(plan, 2, state.first_artifact)
            artifact = await self._verified(request, plan, allow_first_dispatch=False)
            if artifact != state.artifact:
                raise DurableGoalError("document final artifact differs")
            return "accepted", None
        self._same_goal(goal)
        if idempotency_key == DurableGoalWorker.effect_idempotency_key(self.goal):
            request = self._request(plan, 1)
        elif state.first_artifact is not None and idempotency_key == (
            DurableGoalWorker.followup_idempotency_key(self.goal, state.first_artifact)
        ):
            request = self._request(plan, 2, state.first_artifact)
        else:
            raise DurableGoalError("document effect key differs")
        existing = self._existing(request, plan)
        # Before TOOL_PREPARED the durable local protocol has not dispatched.
        # A provider failure or missing provider journal never reaches this branch.
        if existing is None or existing.phase == "admitted":
            return None
        return await self._verified(request, plan, allow_first_dispatch=False)

    async def execute(self, *, goal, instruction, evidence, idempotency_key, previous_artifact=None):
        self._same_goal(goal)
        plan = self.guard.authorize_effect(goal)
        number = 1 if previous_artifact is None else 2
        expected_key = (DurableGoalWorker.effect_idempotency_key(goal) if number == 1 else
                        DurableGoalWorker.followup_idempotency_key(goal, previous_artifact))
        expected_instruction = goal.primary_instruction if number == 1 else goal.followup_instruction
        if idempotency_key != expected_key or instruction != expected_instruction:
            raise DurableGoalError("document execution differs from the sealed step")
        request = self._request(plan, number, previous_artifact)
        self._seal(request, plan)
        return await self._verified(request, plan, allow_first_dispatch=True)

    async def deliver(self, **_kwargs):
        raise DurableGoalError("document delivery only verifies existing local availability")


def build_document_goal_ports(*, engine, store, goal, plan_file, tenant_id, installation_id, read_bound_plan=None):
    """Explicit worker composition; configured files are host inputs only."""
    import hashlib
    import os

    from eva.adapters.connectors.mcp_gateway import McpGatewayConfig, McpGatewayToolAdapter
    from eva.adapters.credentials.runtime_files import read_runtime_secret_file, required_environment
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
    from eva.providers.mcp_gateway import McpGatewayProviderFactory
    from eva.providers.mcp_owner_authority import McpOwnerAuthority

    read_plan = read_bound_plan or (lambda: read_runtime_secret_file(plan_file, setting_name="EVA_DOCUMENT_GOAL_PLAN_FILE"))
    plan = DocumentGoalPlan.from_json(read_plan())
    settings = McpGatewayProviderFactory.load_settings()
    if settings is None:
        raise DurableGoalError("document goal requires a configured MCP provider")
    authority = McpOwnerAuthority(
        engine, required_environment("EVA_MCP_AUTONOMY_MANDATE_FILE"),
        os.environ.get("EVA_MCP_MANAGED_ENDPOINTS_FILE") or None,
    )
    scope = external_timeline_scope(tenant_id, installation_id, "document worker scope is required")
    if authority.scope != scope:
        raise DurableGoalError("document worker and owner scopes differ")
    # Enforce the existing gateway principal allowlist against the trusted host
    # owner directly. No ConversationTurn or requester-selected principal exists.
    owner_hash = hashlib.sha256(authority.owner_principal_id.encode()).hexdigest()
    if owner_hash not in settings.principal_sha256_allowlist:
        raise DurableGoalError("document owner is outside the configured gateway allowlist")
    selected = [spec for spec in settings.specs if spec.configuration_sha256 == plan.tool_configuration_sha256]
    if len(selected) != 1:
        raise DurableGoalError("document plan tool configuration is unavailable")
    spec = selected[0]
    adapter = McpGatewayToolAdapter(
        McpGatewayConfig(settings.base_url, settings.bearer_token, allow_plain_http=settings.allow_plain_http),
        (spec,),
    )
    guard = DocumentGoalAuthority(
        store=store, authority=authority, read_plan=read_plan,
        tool_configuration_sha256=spec.configuration_sha256,
    )
    if store.document_plan_binding(goal) is None:
        guard.authorize_effect(goal)
    else:
        guard.authorize_readback(goal)
    return DocumentGoalPorts(
        goal=goal, store=store, guard=guard, timeline=PostgresOperationalTimeline(engine, scope),
        adapter=adapter, spec=spec,
    )
