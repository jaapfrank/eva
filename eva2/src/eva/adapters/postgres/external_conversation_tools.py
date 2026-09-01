"""Provider-neutral durable execution for model-chosen external tools."""

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
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

    def __init__(
        self,
        engine: sa.Engine,
        planner: ConversationExternalToolPlanner,
        provider: AsyncToolPort,
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
        self._action_names = action_names

    @property
    def action_names(self) -> tuple[str, ...]:
        return self._action_names

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

    def pending(self, turn: ConversationTurn) -> ConversationActionReceipt | None:
        timeline = self._timeline(turn)
        for action_name in self.action_names:
            events = timeline.events(self._thread_id(turn, action_name))
            if not events:
                continue
            if events[-1].event_type not in _TERMINAL_EVENTS:
                self._resume(turn, action_name, events)
                events = timeline.events(self._thread_id(turn, action_name))
            return self._receipt(turn, events)
        return None

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if call.name not in self.action_names:
            raise ConversationConflict(f"external tool {call.name!r} is unavailable")
        preparation = asyncio.run(self._planner.prepare(turn, call))
        request = self._request(turn, call.name, preparation)
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
        asyncio.run(VerifiedToolOrchestrator(timeline, self._provider).run(request))
        return self._receipt(turn, timeline.events(request.thread_id))

    def _resume(
        self,
        turn: ConversationTurn,
        action_name: str,
        events: tuple[TimelineEvent, ...],
    ) -> None:
        if not events or events[0].event_type != "TOOL_REQUEST_ADMITTED":
            raise ConversationConflict("external tool admission is missing")
        admission = self._event_data(events[0])
        request = self._request_from_admission(turn, action_name, admission)
        asyncio.run(
            VerifiedToolOrchestrator(self._timeline(turn), self._provider).run(request)
        )

    def _receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
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
            thread_id=self._thread_id(turn, action_name),
            operation_id=self._operation_id(turn, action_name),
            checkpoint_prefix=self._checkpoint_prefix(turn, action_name),
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
    ) -> ToolRequest:
        return ToolRequest(
            thread_id=self._thread_id(turn, action_name),
            operation_id=self._operation_id(turn, action_name),
            checkpoint_prefix=self._checkpoint_prefix(turn, action_name),
            tool_name=preparation.tool_name,
            arguments=preparation.arguments,
            expected_proof=preparation.expected_proof,
            observation_tool_name=preparation.observation_tool_name,
            observation_arguments=preparation.observation_arguments,
            tool_configuration_sha256=preparation.tool_configuration_sha256,
            execution_timeout_seconds=preparation.execution_timeout_seconds,
            verification_timeout_seconds=preparation.verification_timeout_seconds,
        )

    @staticmethod
    def _identity(turn: ConversationTurn, action_name: str) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "action_name": action_name,
                    "installation_id": turn.installation_id,
                    "message_id": turn.message_id,
                    "tenant_id": turn.tenant_id,
                }
            )
        ).hexdigest()[:32]

    @classmethod
    def _operation_id(cls, turn: ConversationTurn, action_name: str) -> str:
        return f"external-tool:{cls._identity(turn, action_name)}"

    @classmethod
    def _thread_id(cls, turn: ConversationTurn, action_name: str) -> str:
        return f"tool:{cls._operation_id(turn, action_name)}"

    @classmethod
    def _checkpoint_prefix(cls, turn: ConversationTurn, action_name: str) -> str:
        return f"checkpoint:{cls._operation_id(turn, action_name)}"

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
