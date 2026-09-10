"""Provider-neutral durable execution for model-chosen external tools."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from datetime import datetime
import hashlib
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
    external_timeline_scope,
)
from eva.adapters.postgres.scope import try_execution_lock
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.cancellation import check_cancellation
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationExternalToolPlanner,
    ConversationToolPreparation,
    ConversationTurn,
)
from eva.application.orchestration import (
    AsyncToolPort,
    ToolRequest,
    ToolDispatchResult,
    ToolProofObservation,
    VerifiedToolOrchestrator,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes


_TERMINAL_EVENTS = {
    "RECOVERY_FAILED",
    "RECOVERY_NEEDS_HELP",
    "STATE_VERIFICATION_CONFLICT",
    "STATE_VERIFICATION_TIMED_OUT",
    "STATE_VERIFIED",
    "TOOL_REJECTED",
}


class PostgresExternalConversationTools:
    """Persist admission, execution and proof without knowing the provider."""

    supports_indexed_steps = True

    def __init__(
        self,
        engine: sa.Engine,
        planner: ConversationExternalToolPlanner,
        provider: AsyncToolPort,
        *,
        authorize_turn: Callable[[ConversationTurn], None] | None = None,
    ) -> None:
        action_names = planner.action_names
        if (
            not action_names
            or len(set(action_names)) != len(action_names)
            or any(not name or len(name) > 120 for name in action_names)
        ):
            raise ValueError("external tool planner action names are invalid")
        self._engine = engine
        self._planner = planner
        self._provider = provider
        self._authorize_turn = authorize_turn
        self._action_names = action_names

    @property
    def action_names(self) -> tuple[str, ...]:
        return self._action_names

    def available_for(self, turn: ConversationTurn) -> bool:
        """Filter model offerings; execution still rechecks authorization."""
        try:
            self._authorize(turn)
        except ConversationConflict:
            return False
        return True

    def active_context(self, turn: ConversationTurn) -> tuple[str, ...]:
        provider_context = getattr(self._planner, "active_context", None)
        if provider_context is None:
            return ()
        context = provider_context(turn)
        if not isinstance(context, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in context
        ):
            raise ConversationConflict("external tool context is malformed")
        return context

    def pending(
        self, turn: ConversationTurn, *, step_index: int = 0
    ) -> ConversationActionReceipt | None:
        self._validate_step_index(step_index)
        timeline = self._timeline(turn)
        for action_name in self.action_names:
            thread_id = self._thread_id(turn, action_name, step_index)
            if not timeline.events(thread_id):
                continue
            with self._execution_lock(thread_id):
                events = timeline.events(thread_id)
                admitted_name = self._event_data(events[0]).get("action_name")
                if admitted_name not in self.action_names:
                    return None
                self._authorize(turn)
                if events[-1].event_type not in _TERMINAL_EVENTS:
                    self._resume(turn, str(admitted_name), events, step_index)
                    events = timeline.events(thread_id)
                return self._receipt(turn, events, step_index)
        return None

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall,
        *, step_index: int = 0,
    ) -> ConversationActionReceipt:
        check_cancellation()
        from eva.application.initiative_execution import authorize_initiative_action
        authorize_initiative_action(turn, call.name)
        self._validate_step_index(step_index)
        if call.name not in self.action_names:
            raise ConversationConflict(f"external tool {call.name!r} is unavailable")
        self._authorize(turn)
        # Preserve clarification before storage access for the initial action.
        preparation = (
            asyncio.run(self._planner.prepare(turn, call)) if step_index == 0 else None
        )
        # Hold a database session lock across dispatch/readback. A concurrent
        # retry must not mistake an in-flight dispatch for a crashed worker.
        with self._execution_lock(self._thread_id(turn, call.name, step_index)):
            return self._execute(
                turn, call, step_index=step_index, preparation=preparation
            )

    def _execute(
        self, turn: ConversationTurn, call: ConversationActionCall,
        *, step_index: int, preparation: ConversationToolPreparation | None,
    ) -> ConversationActionReceipt:
        self._authorize(turn)
        if step_index:
            timeline = self._timeline(turn)
            events = timeline.events(self._thread_id(turn, call.name, step_index))
            if events:
                admission = self._event_data(events[0])
                if (
                    admission.get("action_call_id") != call.call_id
                    or admission.get("action_name") != call.name
                    or admission.get("action_arguments") != call.arguments
                ):
                    raise ConversationConflict("external tool replay differs")
                if events[-1].event_type not in _TERMINAL_EVENTS:
                    self._resume(turn, call.name, events, step_index)
                    events = timeline.events(
                        self._thread_id(turn, call.name, step_index)
                    )
                return self._receipt(turn, events, step_index)
        if preparation is None:
            preparation = asyncio.run(self._planner.prepare(turn, call))
        self._ensure_running(turn)
        request = self._request(turn, call.name, preparation, step_index)
        admission: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "action_arguments": call.arguments,
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "receipt_output": preparation.receipt_output,
            "request_arguments": request.arguments,
            "request_expected_proof": request.expected_proof,
            "request_execution_timeout_seconds": request.execution_timeout_seconds,
            "request_tool_name": request.tool_name,
            "request_verification_timeout_seconds": (
                request.verification_timeout_seconds
            ),
            "source_message_id": turn.message_id,
        }
        if step_index:
            admission["step_index"] = step_index
        if request.observation_tool_name is not None:
            admission["request_observation_tool_name"] = request.observation_tool_name
            admission["request_observation_arguments"] = (
                request.observation_arguments or {}
            )
        if request.tool_configuration_sha256 is not None:
            admission["request_tool_configuration_sha256"] = (
                request.tool_configuration_sha256
            )
        if preparation.observation_output_key is not None:
            admission["observation_output_key"] = preparation.observation_output_key
        timeline = self._timeline(turn)
        try:
            timeline.create_thread(
                thread_id=request.thread_id,
                checkpoint_id=f"{request.checkpoint_prefix}:admitted",
                occurred_at=self._timestamp(turn),
                data=admission,
                event_type="TOOL_REQUEST_ADMITTED",
                phase="admitted",
                status="active",
                operation_id=request.operation_id,
            )
        except CheckpointConflict:
            events = timeline.events(request.thread_id)
            state = timeline.load(request.thread_id)
            if (
                state.operation_id != request.operation_id
                or not events
                or self._event_data(events[0]) != admission
            ):
                raise ConversationConflict("external tool replay differs") from None
        self._ensure_running(turn)
        asyncio.run(self._orchestrator(turn, timeline).run(request))
        return self._receipt(turn, timeline.events(request.thread_id), step_index)

    def _resume(
        self,
        turn: ConversationTurn,
        action_name: str,
        events: tuple[TimelineEvent, ...],
        step_index: int = 0,
    ) -> None:
        if not events or events[0].event_type != "TOOL_REQUEST_ADMITTED":
            raise ConversationConflict("external tool admission is missing")
        admission = self._event_data(events[0])
        self._ensure_running(turn)
        request = self._request_from_admission(turn, action_name, admission, step_index)
        asyncio.run(
            self._orchestrator(turn, self._timeline(turn)).run(request)
        )

    def _receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...],
        step_index: int = 0,
    ) -> ConversationActionReceipt:
        if not events or events[0].event_type != "TOOL_REQUEST_ADMITTED":
            raise ConversationConflict("external tool admission is missing")
        admission = self._event_data(events[0])
        action_name = admission.get("action_name")
        call_id = admission.get("action_call_id")
        arguments = admission.get("action_arguments")
        receipt_output = admission.get("receipt_output")
        expected_scope = {
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
        }
        if (
            action_name not in self.action_names
            or admission.get("step_index", 0) != step_index
            or not isinstance(call_id, str)
            or not call_id
            or not isinstance(arguments, dict)
            or not isinstance(receipt_output, dict)
            or any(admission.get(key) != value for key, value in expected_scope.items())
        ):
            raise ConversationConflict("external tool receipt scope is malformed")
        status = {
            "STATE_VERIFIED": "verified",
            "STATE_VERIFICATION_TIMED_OUT": "timed_out",
            "STATE_VERIFICATION_CONFLICT": "failed",
            "TOOL_REJECTED": "rejected",
            "RECOVERY_FAILED": "failed",
            "RECOVERY_NEEDS_HELP": "needs_help",
        }.get(events[-1].event_type)
        if status is None:
            raise ConversationConflict("external tool has no terminal receipt")
        proof = self._event_data(events[-1]).get("proof")
        if proof is not None and not isinstance(proof, dict):
            raise ConversationConflict("external tool proof is malformed")
        observation_output: dict[str, JsonValue] = {}
        observation_output_key = admission.get("observation_output_key")
        if observation_output_key is not None:
            if (
                not isinstance(observation_output_key, str)
                or not observation_output_key.isidentifier()
                or len(observation_output_key) > 80
                or observation_output_key in receipt_output
                or observation_output_key in {"contract", "durable", "proof", "status"}
            ):
                raise ConversationConflict(
                    "external tool observation output key is malformed"
                )
            evidence = proof.get("evidence") if isinstance(proof, dict) else None
            observation = (
                evidence.get("observation") if isinstance(evidence, dict) else None
            )
            if status == "verified":
                if not isinstance(observation, dict):
                    raise ConversationConflict(
                        "external tool verified observation is missing"
                    )
                observation_output[observation_output_key] = observation
        return ConversationActionReceipt(
            ConversationActionCall(call_id, str(action_name), arguments),
            {
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                **receipt_output,
                **observation_output,
                "proof": proof,
                "status": status,
            },
        )

    def _request_from_admission(
        self,
        turn: ConversationTurn,
        action_name: str,
        admission: dict[str, JsonValue],
        step_index: int = 0,
    ) -> ToolRequest:
        tool_name = admission.get("request_tool_name")
        arguments = admission.get("request_arguments")
        expected_proof = admission.get("request_expected_proof")
        observation_tool_name = admission.get("request_observation_tool_name")
        observation_arguments = admission.get("request_observation_arguments")
        tool_configuration_sha256 = admission.get("request_tool_configuration_sha256")
        receipt_output = admission.get("receipt_output")
        legacy_configuration_sha256 = (
            receipt_output.get("tool_configuration_sha256")
            if isinstance(receipt_output, dict)
            else None
        )
        if (
            tool_configuration_sha256 is None
            and legacy_configuration_sha256 is not None
        ):
            tool_configuration_sha256 = legacy_configuration_sha256
        execution_timeout = admission.get("request_execution_timeout_seconds", 10)
        verification_timeout = admission.get("request_verification_timeout_seconds", 10)
        if (
            admission.get("action_name") != action_name
            or admission.get("step_index", 0) != step_index
            or not isinstance(tool_name, str)
            or not isinstance(arguments, dict)
            or not isinstance(expected_proof, dict)
            or (observation_tool_name is None) != (observation_arguments is None)
            or (
                observation_tool_name is not None
                and not isinstance(observation_tool_name, str)
            )
            or (
                observation_arguments is not None
                and not isinstance(observation_arguments, dict)
            )
            or (
                tool_configuration_sha256 is not None
                and not isinstance(tool_configuration_sha256, str)
            )
            or (
                admission.get("request_tool_configuration_sha256") is not None
                and legacy_configuration_sha256 is not None
                and admission.get("request_tool_configuration_sha256")
                != legacy_configuration_sha256
            )
            or not isinstance(execution_timeout, int)
            or isinstance(execution_timeout, bool)
            or not isinstance(verification_timeout, int)
            or isinstance(verification_timeout, bool)
            or admission.get("conversation_id") != turn.conversation_id
            or admission.get("principal_id") != turn.principal_id
            or admission.get("source_message_id") != turn.message_id
        ):
            raise ConversationConflict("external tool resume command is malformed")
        return ToolRequest(
            thread_id=self._thread_id(turn, action_name, step_index),
            operation_id=self._operation_id(turn, action_name, step_index),
            checkpoint_prefix=self._checkpoint_prefix(turn, action_name, step_index),
            tool_name=tool_name,
            arguments=arguments,
            expected_proof=expected_proof,
            observation_tool_name=observation_tool_name,
            observation_arguments=observation_arguments,
            tool_configuration_sha256=tool_configuration_sha256,
            execution_timeout_seconds=execution_timeout,
            verification_timeout_seconds=verification_timeout,
        )

    def _request(
        self,
        turn: ConversationTurn,
        action_name: str,
        preparation: ConversationToolPreparation,
        step_index: int = 0,
    ) -> ToolRequest:
        return ToolRequest(
            thread_id=self._thread_id(turn, action_name, step_index),
            operation_id=self._operation_id(turn, action_name, step_index),
            checkpoint_prefix=self._checkpoint_prefix(turn, action_name, step_index),
            tool_name=preparation.tool_name,
            arguments=preparation.arguments,
            expected_proof=preparation.expected_proof,
            observation_tool_name=preparation.observation_tool_name,
            observation_arguments=preparation.observation_arguments,
            tool_configuration_sha256=preparation.tool_configuration_sha256,
            execution_timeout_seconds=preparation.execution_timeout_seconds,
            verification_timeout_seconds=preparation.verification_timeout_seconds,
        )

    def _orchestrator(
        self, turn: ConversationTurn, timeline: PostgresOperationalTimeline
    ) -> VerifiedToolOrchestrator:
        provider = self._provider
        authorize = self._authorize
        journal = PostgresConversationJournal(self._engine, external_timeline_scope(
            turn.tenant_id, turn.installation_id, "conversation stop scope is required",
        ))

        class StopCheckedProvider:
            async def execute(self, request: ToolRequest) -> ToolDispatchResult:
                check_cancellation()
                # Also covers a recovery dispatch after an observation. Readback
                # itself remains possible for an effect that already started.
                await asyncio.to_thread(authorize, turn)
                await asyncio.to_thread(journal.admit_dispatch, turn, request.operation_id)
                check_cancellation()
                from eva.application.initiative_execution import require_initiative_context
                guard = require_initiative_context(turn)
                if guard is not None:
                    await asyncio.to_thread(guard.authorize_request, request, phase="execute")
                return await provider.execute(request)

            async def observe(
                self, request: ToolRequest, dispatch: ToolDispatchResult | None
            ) -> ToolProofObservation:
                await asyncio.to_thread(authorize, turn)
                from eva.application.initiative_execution import require_initiative_context
                guard = require_initiative_context(turn)
                if guard is not None:
                    await asyncio.to_thread(guard.authorize_request, request, phase="observe")
                return await provider.observe(request, dispatch)

        return VerifiedToolOrchestrator(timeline, StopCheckedProvider())

    def _authorize(self, turn: ConversationTurn) -> None:
        from eva.application.initiative_execution import require_initiative_context
        require_initiative_context(turn)
        if self._authorize_turn is not None:
            self._authorize_turn(turn)

    def _ensure_running(self, turn: ConversationTurn) -> None:
        check_cancellation()
        self._authorize(turn)
        PostgresConversationJournal(self._engine, external_timeline_scope(
            turn.tenant_id, turn.installation_id, "conversation stop scope is required",
        )).ensure_running(turn)

    @contextmanager
    def _execution_lock(self, thread_id: str) -> Iterator[None]:
        with try_execution_lock(
            self._engine, f"eva:external-dispatch:{thread_id}"
        ) as acquired:
            if not acquired:
                raise ConversationConflict("external tool step is already running")
            yield

    @staticmethod
    def _validate_step_index(step_index: int) -> None:
        if type(step_index) is not int or step_index < 0:
            raise ValueError("external tool step index must be a nonnegative integer")

    @classmethod
    def _identity(
        cls, turn: ConversationTurn, action_name: str, step_index: int = 0
    ) -> str:
        cls._validate_step_index(step_index)
        # Step zero retains the deployed single-action identity for old replays.
        # Later slots belong to the conversation, not to a tool: two competing
        # proposals for the same slot must conflict even across providers.
        identity: dict[str, JsonValue] = {
            "action_name": action_name,
            "installation_id": turn.installation_id,
            "message_id": turn.message_id,
            "tenant_id": turn.tenant_id,
        }
        if step_index:
            del identity["action_name"]
            identity.update({
                "conversation_id": turn.conversation_id,
                "principal_id": turn.principal_id,
                "step_index": step_index,
            })
        return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()[:32]

    @classmethod
    def _operation_id(
        cls, turn: ConversationTurn, action_name: str, step_index: int = 0
    ) -> str:
        return f"external-tool:{cls._identity(turn, action_name, step_index)}"

    @classmethod
    def _thread_id(
        cls, turn: ConversationTurn, action_name: str, step_index: int = 0
    ) -> str:
        return f"tool:{cls._operation_id(turn, action_name, step_index)}"

    @classmethod
    def _checkpoint_prefix(
        cls, turn: ConversationTurn, action_name: str, step_index: int = 0
    ) -> str:
        return f"checkpoint:{cls._operation_id(turn, action_name, step_index)}"

    @staticmethod
    def _timestamp(turn: ConversationTurn) -> datetime:
        return datetime.fromisoformat(turn.occurred_at.replace("Z", "+00:00"))

    @staticmethod
    def _event_data(event: TimelineEvent) -> dict[str, JsonValue]:
        data = event.payload.get("data")
        if not isinstance(data, dict):
            raise ConversationConflict("external tool event data is malformed")
        return data

    def _timeline(self, turn: ConversationTurn) -> PostgresOperationalTimeline:
        return PostgresOperationalTimeline(
            self._engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL,
                    f"eva2:{turn.tenant_id}:{turn.installation_id}",
                ),
            ),
        )


__all__ = ["PostgresExternalConversationTools"]
