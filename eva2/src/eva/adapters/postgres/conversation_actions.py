"""Durable model-chosen conversation actions backed by the operational timeline."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
import hashlib
import re
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.delivery import PostgresFanoutDeliveryStore
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
from eva.application.conversation import (
    ConversationModelUsage,
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationExternalToolPort,
    ConversationTurn,
)
from eva.application.delivery import (
    FanoutDeliveryRequest,
    FanoutDeliveryResult,
    FanoutDeliverySinkPort,
    FanoutDeliveryWorker,
)
from eva.application.durable_goals import (
    DurableGoalSpec,
    DurableGoalState,
    GoalWakeCondition,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes
from eva.application.personal_interests import (
    CompletedPersonalExploration,
    CompletedPersonalProductRelease,
    CompletedPersonalPrototype,
    PersonalInterestClaim,
    PersonalInterestExploration,
    PersonalProductListing,
    PersonalProductRelease,
    PersonalProductReleaseArtifact,
    PersonalProductReleaseClaim,
    PersonalPrototype,
    PersonalPrototypeArtifact,
    PersonalPrototypeClaim,
)
from eva.application.research import (
    CurrentResearchPort,
    ResearchArtifact,
    ResearchSource,
)
from eva.application.file_batches import (
    BatchFilePlan,
    TextBatchPlan,
    TextBatchPort,
)
from eva.application.validated_edits import (
    FocusedAssertionResult,
    FocusedValidationResult,
    ValidatedTextEditPlan,
    ValidatedTextEditPort,
    ValidatedTextEditResult,
)
from eva.application.repository_improvements import (
    RepositoryCheck,
    RepositoryFile,
    RepositoryImprovementModelPort,
    RepositoryImprovementPort,
    RepositoryImprovementProposal,
    RepositoryImprovementResult,
    RepositorySnapshot,
)


_ACTION = "adopt_personal_interest"
_ARGUMENT_KEYS = {"motivation", "next_exploration", "title"}
_FANOUT_DELIVERY_ACTION = "deliver_test_message_fanout"
_FANOUT_DELIVERY_ARGUMENT_KEYS = {"message", "recipients"}
_DURABLE_GOAL_ACTION = "create_bounded_durable_goal"
_DURABLE_GOAL_ARGUMENT_KEYS = {
    "delivery_recipient",
    "description",
    "pivot",
    "primary_instruction",
    "wake_deadline",
    "wake_expected_value",
    "wake_field",
    "wake_resource",
}
_DURABLE_GOAL_CANCEL_ACTION = "cancel_active_durable_goal"
_DURABLE_GOAL_CANCEL_ARGUMENT_KEYS = {"reason"}
_CURRENT_RESEARCH_ACTION = "research_current_options"
_CURRENT_RESEARCH_ARGUMENT_KEYS = {"options", "question", "title"}
_CURRENT_RESEARCH_LEGACY_ARGUMENT_KEYS = {"question", "title"}
_DOCUMENT_TASK_ACTION = "checkpoint_document_task"
_DOCUMENT_TASK_ARGUMENT_KEYS = {"content", "filename", "mode", "status", "title"}
_DOCUMENT_TASK_EVENTS = {
    "DOCUMENT_TASK_STARTED",
    "DOCUMENT_TASK_CHECKPOINTED",
    "DOCUMENT_TASK_COMPLETED",
    "DOCUMENT_TASK_RESCOPED",
}
_FILE_BATCH_ACTION = "convert_text_batch_to_markdown"
_FILE_BATCH_ARGUMENT_KEYS = {"destination_directory", "source_directory"}
_VALIDATED_FILE_EDIT_ACTION = "edit_text_file_with_focused_validation"
_VALIDATED_FILE_EDIT_ARGUMENT_KEYS = {
    "new_text",
    "old_text",
    "target_path",
    "test_manifest_path",
}
_OBSERVATION_IDENTITY_CORRECTION_ACTION = "record_observation_identity_correction"
_OBSERVATION_IDENTITY_CORRECTION_ARGUMENT_KEYS = {"corrected_name"}
_FILE_BATCH_TERMINAL_EVENTS = {
    "FILE_BATCH_COMPLETED",
    "FILE_BATCH_FAILED",
    "FILE_BATCH_PARTIALLY_COMPLETED",
}
_FILE_BATCH_MAX_ATTEMPTS = 3
_FILE_BATCH_RETRY_SECONDS = 1.0
_VALIDATED_FILE_EDIT_TERMINAL_EVENTS = {
    "VALIDATED_FILE_EDIT_COMMITTED",
    "VALIDATED_FILE_EDIT_ROLLED_BACK",
}
_REPOSITORY_IMPROVEMENT_ACTION = "improve_isolated_repository"
_REPOSITORY_IMPROVEMENT_ARGUMENT_KEYS = {"project_directory"}
_REPOSITORY_IMPROVEMENT_TERMINAL_EVENTS = {
    "REPOSITORY_IMPROVEMENT_COMMITTED",
    "REPOSITORY_IMPROVEMENT_ROLLED_BACK",
}


_TURN_ID_FIELDS = {
    "document": ("conversation_id", "principal_id", "project_id"),
    "document_task": (
        "conversation_id",
        "message_id",
        "principal_id",
        "project_id",
    ),
    "message": ("message_id",),
    "person": ("message_id", "principal_id"),
    "project": ("message_id", "principal_id", "project_id"),
}


def _turn_id(
    turn: ConversationTurn, kind: str, prefix: str, scope: str = "message"
) -> str:
    identity: dict[str, JsonValue] = {
        "installation_id": turn.installation_id,
        "kind": kind,
        "tenant_id": turn.tenant_id,
        **{field: getattr(turn, field) for field in _TURN_ID_FIELDS[scope]},
    }
    from eva.application.background_execution import background_execution_namespace
    namespace = background_execution_namespace(turn)
    if namespace is not None:
        identity["execution_namespace"] = namespace
    return prefix + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()[:32]


class PostgresConversationActions:
    """Persist Eva's chosen interest before the final conversational claim."""

    def __init__(
        self,
        engine: sa.Engine,
        research: CurrentResearchPort | None = None,
        file_batch: TextBatchPort | None = None,
        validated_file_edit: ValidatedTextEditPort | None = None,
        repository_improvement: RepositoryImprovementPort | None = None,
        repository_improvement_model: RepositoryImprovementModelPort | None = None,
        fanout_delivery: FanoutDeliverySinkPort | None = None,
        durable_goal_provider: str | None = None,
        durable_goal_recipients: tuple[str, ...] = (),
        external_tools: tuple[ConversationExternalToolPort, ...] = (),
        native_research: ConversationExternalToolPort | None = None,
    ) -> None:
        self._engine = engine
        self._external_tools = external_tools
        self._native_research = native_research
        external_action_names = tuple(
            action_name
            for provider in external_tools
            for action_name in provider.action_names
        )
        if any(
            not action_name or len(action_name) > 120
            for action_name in external_action_names
        ) or len(set(external_action_names)) != len(external_action_names):
            raise ValueError(
                "external conversation tool names are invalid or duplicated"
            )
        self._external_tool_by_action = {
            action_name: provider
            for provider in external_tools
            for action_name in provider.action_names
        }
        self._research = research
        self._owner_research_steps = None
        if research is not None and all(callable(getattr(research, name, None)) for name in (
            "research_request_payload", "research_configuration_sha256",
        )):
            from eva.adapters.model.research_tools import OwnerResearchContinuationTool
            from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
            adapter = OwnerResearchContinuationTool(research, self._validated_research_arguments)
            self._owner_research_steps = PostgresExternalConversationTools(engine, adapter, adapter)
        self._file_batch = file_batch
        self._validated_file_edit = validated_file_edit
        if (repository_improvement is None) != (repository_improvement_model is None):
            raise ValueError("repository improvement configuration is incomplete")
        self._repository_improvement = repository_improvement
        self._repository_improvement_model = repository_improvement_model
        self._fanout_delivery = fanout_delivery
        if durable_goal_provider is not None and (
            not durable_goal_provider.strip() or not durable_goal_recipients
        ):
            raise ValueError("durable goal provider configuration is incomplete")
        if len(set(durable_goal_recipients)) != len(durable_goal_recipients) or any(
            not item.strip() or len(item) > 160 for item in durable_goal_recipients
        ):
            raise ValueError("durable goal delivery recipients are invalid")
        self._durable_goal_provider = durable_goal_provider
        self._durable_goal_recipients = durable_goal_recipients

    def _durable_goal_store(self, turn: ConversationTurn) -> PostgresDurableGoalStore:
        return PostgresDurableGoalStore(
            self._engine,
            tenant_id=turn.tenant_id,
            installation_id=turn.installation_id,
        )

    @property
    def action_names(self) -> tuple[str, ...]:
        actions = [_ACTION, _DOCUMENT_TASK_ACTION]
        actions.extend(self._external_tool_by_action)
        if self._research is not None:
            actions.append(_CURRENT_RESEARCH_ACTION)
        if self._file_batch is not None:
            actions.append(_FILE_BATCH_ACTION)
        if self._validated_file_edit is not None:
            actions.append(_VALIDATED_FILE_EDIT_ACTION)
        if self._repository_improvement is not None:
            actions.append(_REPOSITORY_IMPROVEMENT_ACTION)
        if self._fanout_delivery is not None:
            actions.append(_FANOUT_DELIVERY_ACTION)
        if self._durable_goal_provider is not None:
            actions.extend((_DURABLE_GOAL_ACTION, _DURABLE_GOAL_CANCEL_ACTION))
        return tuple(actions)

    def available_action_names(self, turn: ConversationTurn) -> tuple[str, ...]:
        actions = list(self.action_names)
        for provider in self._external_tools:
            selector = getattr(provider, "available_action_names", None)
            available = getattr(provider, "available_for", None)
            if callable(selector):
                blocked = set(provider.action_names) - set(selector(turn))
                actions = [name for name in actions if name not in blocked]
            elif callable(available) and not available(turn):
                blocked = set(provider.action_names)
                actions = [name for name in actions if name not in blocked]
        if turn.observation_binding is not None:
            actions.append(_OBSERVATION_IDENTITY_CORRECTION_ACTION)
        from eva.application.initiative_execution import require_initiative_context
        guard = require_initiative_context(turn)
        return tuple(name for name in actions if guard is None or guard.action_allowed(name))

    def _step_tools(self, turn: ConversationTurn):
        from eva.application.initiative_execution import require_initiative_context
        native = require_initiative_context(turn)
        research_steps = self._native_research if native is not None else self._owner_research_steps
        return self._external_tools + ((research_steps,) if research_steps is not None else ())

    def can_continue_after(self, turn, receipt):
        """Only the registered receipt owner can admit its native continuation."""
        provider = self._external_tool_by_action.get(receipt.call.name)
        eligible = None if provider is None else getattr(provider, "can_continue_after", None)
        return callable(eligible) and eligible(turn, receipt) is True

    def continuation_action_names(self, turn: ConversationTurn) -> tuple[str, ...]:
        allowed = set(self.available_action_names(turn))
        return tuple(
            name
            for provider in self._step_tools(turn)
            if getattr(provider, "supports_indexed_steps", False)
            for name in provider.action_names
            if name in allowed
        )

    def pending_step(
        self, turn: ConversationTurn, *, step_index: int
    ) -> ConversationActionReceipt | None:
        if type(step_index) is not int or step_index < 1:
            raise ValueError("continuation step index must be a positive integer")
        for provider in self._step_tools(turn):
            if getattr(provider, "supports_indexed_steps", False):
                receipt = provider.pending(turn, step_index=step_index)
                if receipt is not None:
                    return receipt
        return None

    def execute_step(
        self, turn: ConversationTurn, call: ConversationActionCall, *, step_index: int
    ) -> ConversationActionReceipt:
        if type(step_index) is not int or step_index < 1:
            raise ValueError("continuation step index must be a positive integer")
        if call.name not in self.continuation_action_names(turn):
            raise ConversationConflict("conversation continuation action is unavailable")
        provider = next(p for p in self._step_tools(turn) if call.name in p.action_names)
        return provider.execute(
            turn, call, step_index=step_index,
        )

    def reply_guard(self, turn, receipts):
        """Delegate final consistency checks only to tools used by this turn."""
        guards = []
        visited = set()
        for receipt in receipts:
            provider = self._external_tool_by_action.get(receipt.call.name)
            factory = getattr(provider, "reply_guard", None)
            if not callable(factory) or id(provider) in visited:
                continue
            visited.add(id(provider))
            guard = factory(turn, receipts)
            if guard is not None:
                guards.append(guard)
        if not guards:
            return None

        def reconcile(text):
            for guard in guards:
                text = guard(text)
            return text
        return reconcile

    def memory_query_hints(self, turn: ConversationTurn) -> tuple[str, ...]:
        binding = turn.observation_binding
        if binding is None:
            return ()
        timeline = self._timeline(turn)
        states = timeline.list_checkpoints(
            thread_prefix="memory:observation-identity:",
            phases=("identity_correction_recorded",),
            statuses=("completed",),
            updated_before=self._timestamp(turn),
            limit=128,
        )
        for state in states:
            events = timeline.events(state.thread_id)
            if len(events) != 1 or events[0].event_type != (
                "OBSERVATION_IDENTITY_CORRECTED"
            ):
                continue
            data = self._event_data(events[0])
            if (
                data.get("binding_sha256") != binding.binding_sha256
                or data.get("principal_id") != turn.principal_id
                or data.get("project_id") != turn.project_id
            ):
                continue
            corrected_name = data.get("corrected_name")
            if not isinstance(corrected_name, str) or not corrected_name:
                raise ConversationConflict(
                    "observation identity retrieval hint is malformed"
                )
            return (corrected_name,)
        return ()

    def pending(self, turn: ConversationTurn) -> ConversationActionReceipt | None:
        durable_goal_receipt = self._pending_durable_goal(turn)
        if durable_goal_receipt is not None:
            return durable_goal_receipt
        fanout_receipt = self._pending_fanout(turn)
        if fanout_receipt is not None:
            return fanout_receipt
        for provider in self._step_tools(turn):
            external_receipt = provider.pending(turn)
            if external_receipt is not None:
                return external_receipt
        from eva.application.initiative_execution import require_initiative_context
        if require_initiative_context(turn) is not None:
            return None
        timeline = self._timeline(turn)
        correction_events = timeline.events(
            self._observation_identity_correction_thread_id(turn)
        )
        if correction_events:
            return self._observation_identity_correction_receipt(
                turn, correction_events
            )
        improvement_events = timeline.events(
            self._repository_improvement_thread_id(turn)
        )
        if improvement_events:
            if improvement_events[-1].event_type not in (
                _REPOSITORY_IMPROVEMENT_TERMINAL_EVENTS
            ):
                self._resume_repository_improvement(turn, improvement_events)
                improvement_events = timeline.events(
                    self._repository_improvement_thread_id(turn)
                )
            return self._repository_improvement_receipt(turn, improvement_events)
        edit_events = timeline.events(self._validated_file_edit_thread_id(turn))
        if edit_events:
            if edit_events[-1].event_type not in _VALIDATED_FILE_EDIT_TERMINAL_EVENTS:
                self._resume_validated_file_edit(turn, edit_events)
                edit_events = timeline.events(self._validated_file_edit_thread_id(turn))
            return self._validated_file_edit_receipt(turn, edit_events)
        batch_events = timeline.events(self._file_batch_thread_id(turn))
        if batch_events:
            if batch_events[-1].event_type not in _FILE_BATCH_TERMINAL_EVENTS:
                self._resume_file_batch(turn, batch_events)
                batch_events = timeline.events(self._file_batch_thread_id(turn))
            return self._file_batch_receipt(turn, batch_events)
        document_events = timeline.events(self._document_episode_thread_id(turn))
        document_receipt = self._document_receipt_for_turn(turn, document_events)
        if document_receipt is not None:
            return document_receipt
        research_events = timeline.events(self._research_thread_id(turn))
        if research_events:
            state = timeline.load(self._research_thread_id(turn))
            if state.status not in {"completed", "needs_help"}:
                self._resume_research(turn, research_events)
                research_events = timeline.events(self._research_thread_id(turn))
            return self._research_receipt(turn, research_events)
        events = timeline.events(self._intent_thread_id(turn))
        if not events:
            return None
        return self._receipt_from_event(turn, events[0], disposition="already_adopted")

    def active_context(self, turn: ConversationTurn) -> tuple[str, ...]:
        from eva.application.initiative_execution import require_initiative_context
        if require_initiative_context(turn) is not None:
            # The durable native claim is already supplied by guard.instructions().
            # Never relabel completed/stopped owner work as an active new goal.
            return ()
        timeline = self._timeline(turn)
        document_events = timeline.events(self._document_episode_thread_id(turn))
        current_document = self._current_document_task(document_events)
        states = timeline.list_checkpoints(
            thread_prefix="intent:personal-interest:",
            statuses=("active", "waiting_for_proof", "completed", "failed"),
            updated_before=self._timestamp(turn),
            limit=8,
        )
        context: list[str] = []
        if current_document is not None:
            content = str(current_document["content"])
            if len(content) > 8_000:
                content = content[:8_000] + "\n[checkpointcontext begrensd]"
            context.append(
                "Direct actieve documenttaak in dit gesprek: "
                f"‘{current_document['title']}’, bestand {current_document['filename']}, "
                f"laatst geverifieerde SHA-256 {current_document['content_sha256']}. "
                "Ga bij een deiktisch vervolg uitsluitend hiermee door, niet met een "
                f"ouder project. Laatste volledige checkpoint:\n{content}"
            )
        for state in states:
            events = timeline.events(state.thread_id)
            adopted = next(
                (
                    event
                    for event in events
                    if event.event_type == "PERSONAL_INTENT_ADOPTED"
                ),
                None,
            )
            if adopted is None:
                continue
            data = self._event_data(adopted)
            if data.get("principal_id") != turn.principal_id:
                continue
            title = data.get("title")
            motivation = data.get("motivation")
            question = data.get("next_exploration")
            if not all(
                isinstance(value, str) and value
                for value in (title, motivation, question)
            ):
                raise ConversationConflict("active personal intent is malformed")
            summary = (
                f"Actief eigen doel ‘{title}’. Waarom: {motivation} "
                f"Eerste onderzoeksvraag: {question}"
            )
            completed = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == "PERSONAL_EXPLORATION_COMPLETED"
                ),
                None,
            )
            if completed is not None:
                result = self._event_data(completed).get("exploration_text")
                if not isinstance(result, str) or not result:
                    raise ConversationConflict("active exploration result is malformed")
                summary += f" Uitgevoerde eerste verkenning: {result}"
            prototype_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == "PERSONAL_PROTOTYPE_COMPLETED"
                ),
                None,
            )
            if prototype_event is not None:
                artifact = self._event_data(prototype_event)
                artifact_title = artifact.get("title")
                filename = artifact.get("filename")
                content_sha256 = artifact.get("content_sha256")
                if not all(
                    isinstance(value, str) and value
                    for value in (artifact_title, filename, content_sha256)
                ):
                    raise ConversationConflict("active prototype is malformed")
                summary += (
                    f" Werkelijk gemaakt prototype: {artifact_title}, bestand "
                    f"{filename}, SHA-256 {content_sha256}. Dit is nog niet gepubliceerd "
                    "of verkocht."
                )
            release_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == "PERSONAL_PRODUCT_RELEASE_COMPLETED"
                ),
                None,
            )
            if release_event is not None:
                release = self._event_data(release_event)
                release_filename = release.get("filename")
                release_sha256 = release.get("content_sha256")
                price_eur_cents = release.get("price_eur_cents")
                if (
                    not isinstance(release_filename, str)
                    or not release_filename
                    or not isinstance(release_sha256, str)
                    or not release_sha256
                    or not isinstance(price_eur_cents, int)
                ):
                    raise ConversationConflict("active product release is malformed")
                summary += (
                    f" Verkoopklare bundel: {release_filename}, SHA-256 "
                    f"{release_sha256}, voorlopige prijs "
                    f"€{price_eur_cents / 100:.2f}. Nog zonder publicatie, betaling "
                    "of omzet."
                )
            context.append(summary)
            if len(context) == 2:
                break
        for provider in self._external_tools:
            provider_context = getattr(provider, "active_context", None)
            if provider_context is not None:
                context.extend(provider_context(turn))
        if self._durable_goal_provider is not None:
            goals = self._durable_goal_store(turn).waiting(
                observed_before=self._timestamp(turn), limit=32
            )
            owned = [goal for goal in goals if goal.owner_id == turn.principal_id]
            for goal in owned[-3:]:
                context.append(
                    "Actieve, duurzame gebruikersafspraak die ook na dit gesprek blijft "
                    f"lopen: {goal.description}. Zij wacht uiterlijk tot "
                    f"{goal.wake.deadline.isoformat().replace('+00:00', 'Z')} op "
                    f"{goal.wake.provider}:{goal.wake.resource}, namelijk "
                    f"{goal.wake.field}={goal.wake.expected_value}. Beantwoord een losse "
                    "tussenvraag normaal, maar doe niet alsof deze afspraak daardoor is "
                    "afgerond of geannuleerd."
                )
        return tuple(context)

    def latest_research(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
    ) -> ResearchArtifact | None:
        """Reopen the newest completed research artifact in the exact scope."""

        if not tenant_id or not installation_id or not principal_id:
            raise ValueError("current research scope is incomplete")
        timeline = self._timeline_for(tenant_id, installation_id)
        states = timeline.list_checkpoints(
            thread_prefix="research:current-research:",
            phases=("research_completed",),
            statuses=("completed",),
            limit=32,
        )
        for state in states:
            events = timeline.events(state.thread_id)
            if not events:
                continue
            admission = self._event_data(events[0])
            if admission.get("principal_id") != principal_id:
                continue
            completed = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == "CURRENT_RESEARCH_COMPLETED"
                ),
                None,
            )
            if completed is not None:
                return self._research_artifact_from_event(completed)
        return None

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        from eva.application.initiative_execution import authorize_initiative_action
        authorize_initiative_action(turn, call.name)
        from eva.application.initiative_execution import require_initiative_context
        if (require_initiative_context(turn) is not None
                and call.name == _CURRENT_RESEARCH_ACTION):
            if self._native_research is None:
                raise ConversationConflict("native research route is unavailable")
            return self._native_research.execute(turn, call)
        external_tool = self._external_tool_by_action.get(call.name)
        if external_tool is not None:
            return external_tool.execute(turn, call)
        if call.name == _OBSERVATION_IDENTITY_CORRECTION_ACTION:
            return self._execute_observation_identity_correction(turn, call)
        if call.name == _VALIDATED_FILE_EDIT_ACTION:
            return self._execute_validated_file_edit(turn, call)
        if call.name == _REPOSITORY_IMPROVEMENT_ACTION:
            return self._execute_repository_improvement(turn, call)
        if call.name == _DOCUMENT_TASK_ACTION:
            return self._execute_document_task(turn, call)
        if call.name == _FILE_BATCH_ACTION:
            return self._execute_file_batch(turn, call)
        if call.name == _CURRENT_RESEARCH_ACTION:
            return self._execute_research(turn, call)
        if call.name == _DURABLE_GOAL_ACTION:
            return self._execute_durable_goal(turn, call)
        if call.name == _DURABLE_GOAL_CANCEL_ACTION:
            return self._execute_durable_goal_cancellation(turn, call)
        if call.name == _FANOUT_DELIVERY_ACTION:
            return self._execute_fanout(turn, call)
        if call.name != _ACTION:
            raise ConversationConflict(f"unsupported conversation action {call.name!r}")
        arguments = self._validated_arguments(call.arguments)
        timeline = self._timeline(turn)
        intent_id = self._intent_id(turn)
        thread_id = self._intent_thread_id(turn)
        data: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "conversation_id": turn.conversation_id,
            "intent_id": intent_id,
            "kind": "personal_interest",
            "motivation": arguments["motivation"],
            "next_exploration": arguments["next_exploration"],
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
            "title": arguments["title"],
        }
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:adopted",
                occurred_at=self._timestamp(turn),
                data=data,
                event_type="PERSONAL_INTENT_ADOPTED",
                phase="intent_adopted",
                status="active",
                operation_id=intent_id,
            )
            disposition = "adopted"
            event = timeline.events(thread_id)[0]
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events:
                raise ConversationConflict(
                    "personal intent collision has no durable event"
                ) from None
            event = events[0]
            self._require_same_intent_scope(turn, event)
            disposition = "already_adopted"
        receipt = self._receipt_from_event(turn, event, disposition=disposition)
        self._link_to_conversation(turn, receipt)
        return receipt

    def _execute_observation_identity_correction(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        binding = turn.observation_binding
        if binding is None:
            raise ConversationConflict(
                "identity correction requires a bound photo observation"
            )
        arguments = self._validated_observation_identity_correction_arguments(
            call.arguments
        )
        thread_id = self._observation_identity_correction_thread_id(turn)
        correction_id = (
            "observation-identity:"
            + hashlib.sha256(
                (
                    f"{turn.tenant_id}\0{turn.installation_id}\0{turn.principal_id}\0"
                    f"{binding.binding_sha256}\0{turn.message_id}"
                ).encode()
            ).hexdigest()
        )
        data: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "binding_sha256": binding.binding_sha256,
            "conversation_id": turn.conversation_id,
            "corrected_name": arguments["corrected_name"],
            "correction_id": correction_id,
            "kind": "observation_identity_correction",
            "media_sha256": binding.media_sha256,
            "media_type": binding.media_type,
            "observation_id": binding.observation_id,
            "principal_id": turn.principal_id,
            "project_id": turn.project_id,
            "source_message_id": turn.message_id,
            "source_ref": binding.source_ref,
            "subject_region_sha256": binding.subject_region_sha256,
        }
        timeline = self._timeline(turn)
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:recorded",
                occurred_at=self._timestamp(turn),
                data=data,
                event_type="OBSERVATION_IDENTITY_CORRECTED",
                phase="identity_correction_recorded",
                status="completed",
                operation_id=correction_id,
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events or self._event_data(events[0]) != data:
                raise ConversationConflict(
                    "observation identity correction replay differs"
                ) from None
        return self._observation_identity_correction_receipt(
            turn, timeline.events(thread_id)
        )

    def _observation_identity_correction_receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt:
        if len(events) != 1 or events[0].event_type != "OBSERVATION_IDENTITY_CORRECTED":
            raise ConversationConflict("observation identity correction is malformed")
        data = self._event_data(events[0])
        binding = turn.observation_binding
        expected = {
            "action_name": _OBSERVATION_IDENTITY_CORRECTION_ACTION,
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "project_id": turn.project_id,
            "source_message_id": turn.message_id,
        }
        if binding is None or any(
            data.get(key) != value for key, value in expected.items()
        ):
            raise ConversationConflict("observation identity correction scope differs")
        if data.get("binding_sha256") != binding.binding_sha256:
            raise ConversationConflict("observation identity binding differs")
        call_id = data.get("action_call_id")
        correction_id = data.get("correction_id")
        corrected_name = data.get("corrected_name")
        if not all(
            isinstance(value, str) and value
            for value in (call_id, correction_id, corrected_name)
        ):
            raise ConversationConflict("observation identity correction fields differ")
        arguments = self._validated_observation_identity_correction_arguments(
            {"corrected_name": corrected_name}
        )
        return ConversationActionReceipt(
            ConversationActionCall(
                str(call_id), _OBSERVATION_IDENTITY_CORRECTION_ACTION, arguments
            ),
            {
                "binding_sha256": binding.binding_sha256,
                "contract": "EvaConversationActionReceipt/v1",
                "corrected_name": arguments["corrected_name"],
                "correction_id": str(correction_id),
                "durable": True,
                "observation_id": binding.observation_id,
                "status": "recorded",
                "subject_region_sha256": binding.subject_region_sha256,
            },
        )

    def _execute_validated_file_edit(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if self._validated_file_edit is None:
            raise ConversationConflict("validated file edit is unavailable")
        arguments = self._validated_file_edit_arguments(call.arguments)
        try:
            plan = self._validated_file_edit.prepare(
                target_path=str(arguments["target_path"]),
                old_text=str(arguments["old_text"]),
                new_text=str(arguments["new_text"]),
                test_manifest_path=str(arguments["test_manifest_path"]),
            )
        except ValueError as error:
            raise ConversationConflict(str(error)) from error
        timeline = self._timeline(turn)
        thread_id = self._validated_file_edit_thread_id(turn)
        data: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "arguments": arguments,
            "baseline_validation": self._focused_validation_data(
                plan.baseline_validation
            ),
            "conversation_id": turn.conversation_id,
            "manifest_sha256": plan.manifest_sha256,
            "original_content": plan.original_content,
            "original_sha256": plan.original_sha256,
            "principal_id": turn.principal_id,
            "project_id": turn.project_id,
            "proposed_content": plan.proposed_content,
            "proposed_sha256": plan.proposed_sha256,
            "source_message_id": turn.message_id,
            "target_path": plan.target_path,
            "test_manifest_path": plan.test_manifest_path,
            "unrelated_before_sha256": plan.unrelated_before_sha256,
            "worktree_before_sha256": plan.worktree_before_sha256,
        }
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:prepared",
                occurred_at=self._timestamp(turn),
                data=data,
                event_type="VALIDATED_FILE_EDIT_PREPARED",
                phase="validated_file_edit_prepared",
                status="active",
                operation_id=self._validated_file_edit_operation_id(turn),
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events or self._event_data(events[0]) != data:
                raise ConversationConflict(
                    "validated file edit replay differs"
                ) from None
        events = timeline.events(thread_id)
        if len(events) == 1:
            self._resume_validated_file_edit(turn, events)
        return self._validated_file_edit_receipt(turn, timeline.events(thread_id))

    def _execute_repository_improvement(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if (
            self._repository_improvement is None
            or self._repository_improvement_model is None
        ):
            raise ConversationConflict("repository improvement is unavailable")
        if set(call.arguments) != _REPOSITORY_IMPROVEMENT_ARGUMENT_KEYS:
            raise ConversationConflict("repository improvement fields differ")
        project_directory = call.arguments.get("project_directory")
        if (
            not isinstance(project_directory, str)
            or not project_directory
            or len(project_directory) > 240
        ):
            raise ConversationConflict("repository improvement project is invalid")
        try:
            snapshot = self._repository_improvement.inspect(
                project_directory=project_directory,
                captured_at=self._timestamp(turn),
            )
        except ValueError as error:
            raise ConversationConflict(str(error)) from error
        if not snapshot.failed_checks:
            raise ConversationConflict("repository has no evidenced improvement")
        thread_id = self._repository_improvement_thread_id(turn)
        data: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "project_id": turn.project_id,
            "snapshot": self._repository_snapshot_data(snapshot),
            "source_message_id": turn.message_id,
        }
        timeline = self._timeline(turn)
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:snapshot",
                occurred_at=self._timestamp(turn),
                data=data,
                event_type="REPOSITORY_SNAPSHOT_RECORDED",
                phase="repository_snapshot_recorded",
                status="active",
                operation_id=self._repository_improvement_operation_id(turn),
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events or self._event_data(events[0]) != data:
                raise ConversationConflict(
                    "repository improvement replay differs"
                ) from None
        events = timeline.events(thread_id)
        if events[-1].event_type not in _REPOSITORY_IMPROVEMENT_TERMINAL_EVENTS:
            self._resume_repository_improvement(turn, events)
        return self._repository_improvement_receipt(turn, timeline.events(thread_id))

    def _resume_repository_improvement(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> None:
        if (
            self._repository_improvement is None
            or self._repository_improvement_model is None
            or not events
            or events[0].event_type != "REPOSITORY_SNAPSHOT_RECORDED"
        ):
            raise ConversationConflict("repository improvement cannot resume")
        admission = self._event_data(events[0])
        if any(
            admission.get(key) != value
            for key, value in {
                "conversation_id": turn.conversation_id,
                "principal_id": turn.principal_id,
                "project_id": turn.project_id,
                "source_message_id": turn.message_id,
            }.items()
        ):
            raise ConversationConflict("repository improvement scope differs")
        snapshot = self._repository_snapshot_from_data(admission.get("snapshot"))
        timeline = self._timeline(turn)
        thread_id = self._repository_improvement_thread_id(turn)
        if len(events) == 1:
            proposal = self._repository_improvement_model.choose_repository_improvement(
                snapshot
            )
            selected = next(
                (
                    item
                    for item in snapshot.failed_checks
                    if item.check_id == proposal.check_id
                ),
                None,
            )
            if selected is None or selected.target_path != proposal.target_path:
                raise ConversationConflict(
                    "repository improvement choice is ungrounded"
                )
            state = timeline.load(thread_id)
            timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:patch-prepared",
                operation_id=self._repository_improvement_operation_id(turn),
                phase="repository_patch_prepared",
                status="active",
                event_type="REPOSITORY_IMPROVEMENT_PATCH_PREPARED",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                data={
                    "problem_evidence_sha256": selected.evidence_sha256,
                    "proposal": self._repository_proposal_data(proposal),
                    "repository_sha256": snapshot.repository_sha256,
                    "source_message_id": turn.message_id,
                },
            )
            events = timeline.events(thread_id)
        if len(events) == 2:
            prepared = self._event_data(events[1])
            proposal = self._repository_proposal_from_data(prepared.get("proposal"))
            if prepared.get("repository_sha256") != snapshot.repository_sha256:
                raise ConversationConflict(
                    "repository improvement snapshot binding differs"
                )
            try:
                result = self._repository_improvement.apply(snapshot, proposal)
            except (RuntimeError, ValueError) as error:
                raise ConversationConflict(str(error)) from error
            state = timeline.load(thread_id)
            timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:{result.status}",
                operation_id=self._repository_improvement_operation_id(turn),
                phase=f"repository_improvement_{result.status}",
                status="completed",
                event_type=(
                    "REPOSITORY_IMPROVEMENT_COMMITTED"
                    if result.status == "committed"
                    else "REPOSITORY_IMPROVEMENT_ROLLED_BACK"
                ),
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                data={
                    "problem": proposal.problem,
                    "problem_evidence": proposal.evidence,
                    "result": self._repository_result_data(result),
                    "source_message_id": turn.message_id,
                },
            )

    def _repository_improvement_receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt:
        if (
            len(events) != 3
            or events[0].event_type != "REPOSITORY_SNAPSHOT_RECORDED"
            or events[1].event_type != "REPOSITORY_IMPROVEMENT_PATCH_PREPARED"
            or events[2].event_type not in _REPOSITORY_IMPROVEMENT_TERMINAL_EVENTS
        ):
            raise ConversationConflict("repository improvement events differ")
        admission = self._event_data(events[0])
        prepared = self._event_data(events[1])
        completed = self._event_data(events[2])
        snapshot = self._repository_snapshot_from_data(admission.get("snapshot"))
        self._repository_proposal_from_data(prepared.get("proposal"))
        result = self._repository_result_from_data(completed.get("result"))
        call_id = admission.get("action_call_id")
        if (
            not isinstance(call_id, str)
            or completed.get("source_message_id") != turn.message_id
        ):
            raise ConversationConflict("repository improvement receipt scope differs")
        return ConversationActionReceipt(
            ConversationActionCall(
                call_id,
                _REPOSITORY_IMPROVEMENT_ACTION,
                {"project_directory": snapshot.project_directory},
            ),
            {
                "checks_after": [
                    {"check_id": item.check_id, "passed": item.passed}
                    for item in result.checks_after
                ],
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                "patch_sha256": result.patch_sha256,
                "problem": completed.get("problem"),
                "problem_evidence": completed.get("problem_evidence"),
                "project_directory": snapshot.project_directory,
                "repository_after_sha256": result.repository_after_sha256,
                "repository_before_sha256": result.repository_before_sha256,
                "selected_check_id": result.selected_check_id,
                "status": result.status,
                "target_path": result.target_path,
                "unrelated_work_preserved": (
                    result.unrelated_before_sha256 == result.unrelated_after_sha256
                ),
            },
        )

    @staticmethod
    def _repository_check_data(item: RepositoryCheck) -> dict[str, JsonValue]:
        return {
            "check_id": item.check_id,
            "evidence_sha256": item.evidence_sha256,
            "kind": item.kind,
            "passed": item.passed,
            "target_path": item.target_path,
            "value": item.value,
        }

    @classmethod
    def _repository_snapshot_data(
        cls, snapshot: RepositorySnapshot
    ) -> dict[str, JsonValue]:
        return {
            "captured_at": snapshot.captured_at.isoformat(),
            "checks": [cls._repository_check_data(item) for item in snapshot.checks],
            "checks_manifest_sha256": snapshot.checks_manifest_sha256,
            "files": [
                {"content": item.content, "path": item.path, "sha256": item.sha256}
                for item in snapshot.files
            ],
            "project_directory": snapshot.project_directory,
            "repository_sha256": snapshot.repository_sha256,
        }

    @staticmethod
    def _repository_check_from_data(raw: JsonValue) -> RepositoryCheck:
        if not isinstance(raw, dict):
            raise ConversationConflict("repository check data is malformed")
        try:
            return RepositoryCheck(
                check_id=str(raw["check_id"]),
                evidence_sha256=str(raw["evidence_sha256"]),
                kind=str(raw["kind"]),
                passed=raw["passed"] if isinstance(raw.get("passed"), bool) else False,
                target_path=str(raw["target_path"]),
                value=str(raw["value"]),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict("repository check data is malformed") from error

    @classmethod
    def _repository_snapshot_from_data(
        cls, raw: JsonValue | None
    ) -> RepositorySnapshot:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("files"), list)
            or not isinstance(raw.get("checks"), list)
        ):
            raise ConversationConflict("repository snapshot data is malformed")
        try:
            return RepositorySnapshot(
                project_directory=str(raw["project_directory"]),
                repository_sha256=str(raw["repository_sha256"]),
                checks_manifest_sha256=str(raw["checks_manifest_sha256"]),
                files=tuple(
                    RepositoryFile(
                        path=str(item["path"]),
                        content=str(item["content"]),
                        sha256=str(item["sha256"]),
                    )
                    for item in raw["files"]
                    if isinstance(item, dict)
                ),
                checks=tuple(
                    cls._repository_check_from_data(item) for item in raw["checks"]
                ),
                captured_at=datetime.fromisoformat(str(raw["captured_at"])),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict(
                "repository snapshot data is malformed"
            ) from error

    @staticmethod
    def _repository_proposal_data(
        proposal: RepositoryImprovementProposal,
    ) -> dict[str, JsonValue]:
        return {
            "check_id": proposal.check_id,
            "evidence": proposal.evidence,
            "new_text": proposal.new_text,
            "old_text": proposal.old_text,
            "problem": proposal.problem,
            "target_path": proposal.target_path,
        }

    @staticmethod
    def _repository_proposal_from_data(
        raw: JsonValue | None,
    ) -> RepositoryImprovementProposal:
        if not isinstance(raw, dict):
            raise ConversationConflict("repository proposal data is malformed")
        try:
            return RepositoryImprovementProposal(
                check_id=str(raw["check_id"]),
                evidence=str(raw["evidence"]),
                new_text=str(raw["new_text"]),
                old_text=str(raw["old_text"]),
                problem=str(raw["problem"]),
                target_path=str(raw["target_path"]),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict(
                "repository proposal data is malformed"
            ) from error

    @classmethod
    def _repository_result_data(
        cls, result: RepositoryImprovementResult
    ) -> dict[str, JsonValue]:
        return {
            "checks_after": [
                cls._repository_check_data(item) for item in result.checks_after
            ],
            "checks_before": [
                cls._repository_check_data(item) for item in result.checks_before
            ],
            "final_sha256": result.final_sha256,
            "original_sha256": result.original_sha256,
            "patch_sha256": result.patch_sha256,
            "proposed_sha256": result.proposed_sha256,
            "repository_after_sha256": result.repository_after_sha256,
            "repository_before_sha256": result.repository_before_sha256,
            "selected_check_id": result.selected_check_id,
            "status": result.status,
            "target_path": result.target_path,
            "unrelated_after_sha256": result.unrelated_after_sha256,
            "unrelated_before_sha256": result.unrelated_before_sha256,
        }

    @classmethod
    def _repository_result_from_data(
        cls, raw: JsonValue | None
    ) -> RepositoryImprovementResult:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("checks_before"), list)
            or not isinstance(raw.get("checks_after"), list)
        ):
            raise ConversationConflict("repository result data is malformed")
        try:
            return RepositoryImprovementResult(
                status=str(raw["status"]),
                selected_check_id=str(raw["selected_check_id"]),
                target_path=str(raw["target_path"]),
                repository_before_sha256=str(raw["repository_before_sha256"]),
                repository_after_sha256=str(raw["repository_after_sha256"]),
                unrelated_before_sha256=str(raw["unrelated_before_sha256"]),
                unrelated_after_sha256=str(raw["unrelated_after_sha256"]),
                original_sha256=str(raw["original_sha256"]),
                proposed_sha256=str(raw["proposed_sha256"]),
                final_sha256=str(raw["final_sha256"]),
                patch_sha256=str(raw["patch_sha256"]),
                checks_before=tuple(
                    cls._repository_check_from_data(item)
                    for item in raw["checks_before"]
                ),
                checks_after=tuple(
                    cls._repository_check_from_data(item)
                    for item in raw["checks_after"]
                ),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict("repository result data is malformed") from error

    def _resume_validated_file_edit(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> None:
        if self._validated_file_edit is None or not events:
            raise ConversationConflict("validated file edit cannot resume")
        admission = self._event_data(events[0])
        if events[0].event_type != "VALIDATED_FILE_EDIT_PREPARED" or any(
            admission.get(key) != value
            for key, value in {
                "conversation_id": turn.conversation_id,
                "principal_id": turn.principal_id,
                "project_id": turn.project_id,
                "source_message_id": turn.message_id,
            }.items()
        ):
            raise ConversationConflict("validated file edit scope differs")
        baseline = self._focused_validation_from_data(
            admission.get("baseline_validation")
        )
        try:
            plan = ValidatedTextEditPlan(
                target_path=str(admission["target_path"]),
                test_manifest_path=str(admission["test_manifest_path"]),
                original_content=str(admission["original_content"]),
                proposed_content=str(admission["proposed_content"]),
                manifest_sha256=str(admission["manifest_sha256"]),
                worktree_before_sha256=str(admission["worktree_before_sha256"]),
                unrelated_before_sha256=str(admission["unrelated_before_sha256"]),
                baseline_validation=baseline,
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict(
                "validated file edit plan is malformed"
            ) from error
        if (
            admission.get("original_sha256") != plan.original_sha256
            or admission.get("proposed_sha256") != plan.proposed_sha256
        ):
            raise ConversationConflict("validated file edit plan hashes differ")
        try:
            result = self._validated_file_edit.execute(plan)
        except (RuntimeError, ValueError) as error:
            raise ConversationConflict(str(error)) from error
        state = self._timeline(turn).load(self._validated_file_edit_thread_id(turn))
        self._timeline(turn).transition(
            thread_id=self._validated_file_edit_thread_id(turn),
            expected_revision=state.revision,
            checkpoint_id=f"{self._validated_file_edit_thread_id(turn)}:{result.status}",
            operation_id=self._validated_file_edit_operation_id(turn),
            phase=f"validated_file_edit_{result.status}",
            status="completed",
            event_type=(
                "VALIDATED_FILE_EDIT_COMMITTED"
                if result.status == "committed"
                else "VALIDATED_FILE_EDIT_ROLLED_BACK"
            ),
            occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            data=self._validated_file_edit_result_data(turn, result),
        )

    @staticmethod
    def _validated_file_edit_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _VALIDATED_FILE_EDIT_ARGUMENT_KEYS:
            raise ConversationConflict("validated file edit fields differ")
        values: dict[str, JsonValue] = {}
        for key in sorted(_VALIDATED_FILE_EDIT_ARGUMENT_KEYS):
            value = raw.get(key)
            maximum = 8_192 if key in {"new_text", "old_text"} else 240
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > maximum
            ):
                raise ConversationConflict("validated file edit value is invalid")
            values[key] = value
        return values

    @staticmethod
    def _focused_validation_data(
        validation: FocusedValidationResult,
    ) -> dict[str, JsonValue]:
        return {
            "assertions": [
                {
                    "kind": item.kind,
                    "passed": item.passed,
                    "value_sha256": item.value_sha256,
                }
                for item in validation.assertions
            ],
            "manifest_path": validation.manifest_path,
            "manifest_sha256": validation.manifest_sha256,
            "passed": validation.passed,
        }

    @staticmethod
    def _focused_validation_from_data(raw: JsonValue | None) -> FocusedValidationResult:
        if not isinstance(raw, dict) or not isinstance(raw.get("assertions"), list):
            raise ConversationConflict("focused validation data is malformed")
        try:
            return FocusedValidationResult(
                manifest_path=str(raw["manifest_path"]),
                manifest_sha256=str(raw["manifest_sha256"]),
                passed=raw["passed"] if isinstance(raw["passed"], bool) else False,
                assertions=tuple(
                    FocusedAssertionResult(
                        kind=str(item["kind"]),
                        passed=(
                            item["passed"]
                            if isinstance(item.get("passed"), bool)
                            else False
                        ),
                        value_sha256=str(item["value_sha256"]),
                    )
                    for item in raw["assertions"]
                    if isinstance(item, dict)
                ),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict(
                "focused validation data is malformed"
            ) from error

    def _validated_file_edit_result_data(
        self, turn: ConversationTurn, result: ValidatedTextEditResult
    ) -> dict[str, JsonValue]:
        return {
            "final_sha256": result.final_sha256,
            "original_sha256": result.original_sha256,
            "patch_sha256": result.patch_sha256,
            "proposed_sha256": result.proposed_sha256,
            "source_message_id": turn.message_id,
            "status": result.status,
            "target_path": result.target_path,
            "unrelated_after_sha256": result.unrelated_after_sha256,
            "unrelated_before_sha256": result.unrelated_before_sha256,
            "validation_after_edit": self._focused_validation_data(
                result.validation_after_edit
            ),
            "validation_after_repair": (
                None
                if result.validation_after_repair is None
                else self._focused_validation_data(result.validation_after_repair)
            ),
            "worktree_after_sha256": result.worktree_after_sha256,
            "worktree_before_sha256": result.worktree_before_sha256,
        }

    def _validated_file_edit_receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt:
        if (
            len(events) != 2
            or events[0].event_type != "VALIDATED_FILE_EDIT_PREPARED"
            or events[-1].event_type not in _VALIDATED_FILE_EDIT_TERMINAL_EVENTS
        ):
            raise ConversationConflict("validated file edit events differ")
        admission = self._event_data(events[0])
        final = self._event_data(events[-1])
        raw_arguments = admission.get("arguments")
        if not isinstance(raw_arguments, dict):
            raise ConversationConflict("validated file edit arguments are missing")
        arguments = self._validated_file_edit_arguments(raw_arguments)
        call_id = admission.get("action_call_id")
        if (
            not isinstance(call_id, str)
            or final.get("source_message_id") != turn.message_id
            or final.get("target_path") != arguments["target_path"]
        ):
            raise ConversationConflict("validated file edit receipt scope differs")
        return ConversationActionReceipt(
            ConversationActionCall(call_id, _VALIDATED_FILE_EDIT_ACTION, arguments),
            {
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                "final_sha256": final.get("final_sha256"),
                "focused_test_after_edit": final.get("validation_after_edit"),
                "focused_test_after_repair": final.get("validation_after_repair"),
                "original_sha256": final.get("original_sha256"),
                "patch_sha256": final.get("patch_sha256"),
                "proposed_sha256": final.get("proposed_sha256"),
                "status": final.get("status"),
                "target_path": final.get("target_path"),
                "test_manifest_path": arguments["test_manifest_path"],
                "unrelated_work_preserved": (
                    final.get("unrelated_before_sha256")
                    == final.get("unrelated_after_sha256")
                ),
                "worktree_after_sha256": final.get("worktree_after_sha256"),
                "worktree_before_sha256": final.get("worktree_before_sha256"),
            },
        )

    def _execute_file_batch(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if self._file_batch is None:
            raise ConversationConflict("bestandsbatch is niet beschikbaar")
        arguments = self._validated_file_batch_arguments(call.arguments)
        plan = self._file_batch.prepare(
            source_directory=str(arguments["source_directory"]),
            destination_directory=str(arguments["destination_directory"]),
        )
        timeline = self._timeline(turn)
        thread_id = self._file_batch_thread_id(turn)
        data: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "conversation_id": turn.conversation_id,
            "destination_directory": plan.destination_directory,
            "files": [
                {
                    "destination_path": item.destination_path,
                    "output": item.output,
                    "output_sha256": item.output_sha256,
                    "source_path": item.source_path,
                    "source_sha256": item.source_sha256,
                }
                for item in plan.files
            ],
            "principal_id": turn.principal_id,
            "project_id": turn.project_id,
            "source_directory": plan.source_directory,
            "source_message_id": turn.message_id,
        }
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:admitted",
                occurred_at=self._timestamp(turn),
                data=data,
                event_type="FILE_BATCH_ADMITTED",
                phase="file_batch_admitted",
                status="active",
                operation_id=self._file_batch_operation_id(turn),
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events or self._event_data(events[0]) != data:
                raise ConversationConflict("bestandsbatchreplay verschilt") from None
        events = timeline.events(thread_id)
        if len(events) == 1:
            self._resume_file_batch(turn, events)
        return self._file_batch_receipt(turn, timeline.events(thread_id))

    def _resume_file_batch(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> None:
        if self._file_batch is None or not events:
            raise ConversationConflict("bestandsbatch kan niet hervatten")
        admission = self._event_data(events[0])
        if any(
            admission.get(key) != value
            for key, value in {
                "conversation_id": turn.conversation_id,
                "principal_id": turn.principal_id,
                "project_id": turn.project_id,
                "source_message_id": turn.message_id,
            }.items()
        ):
            raise ConversationConflict("bestandsbatchscope verschilt")
        raw_files = admission.get("files")
        if not isinstance(raw_files, list):
            raise ConversationConflict("bestandsbatchplan is ongeldig")
        try:
            plan = TextBatchPlan(
                str(admission["source_directory"]),
                str(admission["destination_directory"]),
                tuple(
                    BatchFilePlan(
                        str(item["source_path"]),
                        str(item["source_sha256"]),
                        str(item["destination_path"]),
                        str(item["output"]),
                    )
                    for item in raw_files
                    if isinstance(item, dict)
                ),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict("bestandsbatchplan is ongeldig") from error
        if len(plan.files) != len(raw_files):
            raise ConversationConflict("bestandsbatchplan bevat ongeldige files")
        attempt_events = [
            event
            for event in events
            if event.event_type == "FILE_BATCH_ATTEMPT_RECORDED"
        ]
        last_attempt_data = (
            None if not attempt_events else self._event_data(attempt_events[-1])
        )
        while len(attempt_events) < _FILE_BATCH_MAX_ATTEMPTS:
            if last_attempt_data is not None:
                failures = last_attempt_data.get("failures")
                if not isinstance(failures, list):
                    raise ConversationConflict("bestandsbatchpoging is ongeldig")
                if not failures or any(
                    not isinstance(item, dict) or item.get("retryable") is not True
                    for item in failures
                ):
                    break
                time.sleep(_FILE_BATCH_RETRY_SECONDS)
            result = self._file_batch.execute(plan)
            successes = [
                {
                    "destination_path": item.destination_path,
                    "output_sha256": item.output_sha256,
                    "source_path": item.source_path,
                    "status": item.status,
                }
                for item in result.successful
            ]
            retryable_ids = {
                (item.source_path, item.destination_path)
                for item in result.retryable_failed
            }
            failures = [
                {
                    "destination_path": item.destination_path,
                    "error_code": item.error_code,
                    "retryable": (item.source_path, item.destination_path)
                    in retryable_ids,
                    "source_path": item.source_path,
                }
                for item in result.failed
            ]
            state = self._timeline(turn).load(self._file_batch_thread_id(turn))
            attempt = len(attempt_events) + 1
            self._timeline(turn).transition(
                thread_id=self._file_batch_thread_id(turn),
                expected_revision=state.revision,
                checkpoint_id=(f"{self._file_batch_thread_id(turn)}:attempt:{attempt}"),
                operation_id=self._file_batch_operation_id(turn),
                phase="file_batch_attempted",
                status="active",
                event_type="FILE_BATCH_ATTEMPT_RECORDED",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                data={
                    "attempt": attempt,
                    "failures": failures,
                    "source_message_id": turn.message_id,
                    "successes": successes,
                },
            )
            attempt_events = [
                *attempt_events,
                self._timeline(turn).events(self._file_batch_thread_id(turn))[-1],
            ]
            last_attempt_data = self._event_data(attempt_events[-1])
            if not failures or any(item["retryable"] is not True for item in failures):
                break
        if last_attempt_data is None:
            raise ConversationConflict("bestandsbatch heeft geen uitvoeringspoging")
        successes = last_attempt_data.get("successes")
        failures = last_attempt_data.get("failures")
        if not isinstance(successes, list) or not isinstance(failures, list):
            raise ConversationConflict("bestandsbatchpoging is ongeldig")
        state = self._timeline(turn).load(self._file_batch_thread_id(turn))
        self._timeline(turn).transition(
            thread_id=self._file_batch_thread_id(turn),
            expected_revision=state.revision,
            checkpoint_id=f"{self._file_batch_thread_id(turn)}:completed",
            operation_id=self._file_batch_operation_id(turn),
            phase="file_batch_completed",
            status="completed",
            event_type=(
                "FILE_BATCH_PARTIALLY_COMPLETED"
                if successes and failures
                else "FILE_BATCH_COMPLETED"
                if successes
                else "FILE_BATCH_FAILED"
            ),
            occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            data={
                "attempt_count": len(attempt_events),
                "failures": failures,
                "source_message_id": turn.message_id,
                "successes": successes,
            },
        )

    @staticmethod
    def _validated_file_batch_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _FILE_BATCH_ARGUMENT_KEYS:
            raise ConversationConflict("bestandsbatchvelden verschillen")
        values: dict[str, JsonValue] = {}
        for key in sorted(_FILE_BATCH_ARGUMENT_KEYS):
            value = raw.get(key)
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ConversationConflict("bestandsbatchdirectory is ongeldig")
            values[key] = value
        return values

    def _file_batch_receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt:
        if (
            len(events) < 3
            or events[0].event_type != "FILE_BATCH_ADMITTED"
            or events[-1].event_type not in _FILE_BATCH_TERMINAL_EVENTS
        ):
            raise ConversationConflict("bestandsbatchevents verschillen")
        admission = self._event_data(events[0])
        completed = self._event_data(events[-1])
        arguments = self._validated_file_batch_arguments(
            {
                "destination_directory": admission.get("destination_directory"),
                "source_directory": admission.get("source_directory"),
            }
        )
        call_id = admission.get("action_call_id")
        successes = completed.get("successes")
        failures = completed.get("failures")
        if (
            not isinstance(call_id, str)
            or not isinstance(successes, list)
            or not isinstance(failures, list)
        ):
            raise ConversationConflict("bestandsbatchresultaat is ongeldig")
        return ConversationActionReceipt(
            ConversationActionCall(call_id, _FILE_BATCH_ACTION, arguments),
            {
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                "attempt_count": completed.get("attempt_count"),
                "failed": failures,
                "failed_count": len(failures),
                "status": (
                    "partial"
                    if successes and failures
                    else "completed"
                    if successes
                    else "failed"
                ),
                "successful": successes,
                "successful_count": len(successes),
            },
        )

    def _execute_document_task(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        arguments = self._validated_document_task_arguments(call.arguments)
        timeline = self._timeline(turn)
        thread_id = self._document_episode_thread_id(turn)
        events = timeline.events(thread_id)
        existing = self._document_receipt_for_turn(turn, events)
        if existing is not None:
            return existing
        current = self._current_document_task(events)
        mode = str(arguments["mode"])
        if mode in {"continue_current", "replace_current"} and current is None:
            raise ConversationConflict(
                "dit gesprek heeft geen direct actieve documenttaak"
            )
        if (
            mode == "continue_current"
            and current is not None
            and (
                arguments["title"] != current["title"]
                or arguments["filename"] != current["filename"]
            )
        ):
            raise ConversationConflict(
                "documentvervolg wijkt af van de direct actieve taak"
            )
        if mode == "continue_current" and current is not None:
            next_sha256 = hashlib.sha256(
                str(arguments["content"]).encode("utf-8")
            ).hexdigest()
            if next_sha256 == current["content_sha256"]:
                raise ConversationConflict(
                    "documentvervolg bevat geen aantoonbare voortgang"
                )
        task_id = (
            str(current["task_id"])
            if mode == "continue_current" and current is not None
            else self._document_task_id(turn)
        )
        content = str(arguments["content"])
        task_status = str(arguments["status"])
        if task_status == "completed":
            self._validate_completed_document(content)
        data: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "content": content,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "conversation_id": turn.conversation_id,
            "filename": arguments["filename"],
            "mode": mode,
            "principal_id": turn.principal_id,
            "project_id": turn.project_id,
            "source_message_id": turn.message_id,
            "status": task_status,
            "task_id": task_id,
            "title": arguments["title"],
            "utf8_byte_length": len(content.encode("utf-8")),
        }
        if mode == "replace_current" and current is not None:
            data["replaces_task_id"] = str(current["task_id"])
            event_type = "DOCUMENT_TASK_RESCOPED"
        elif mode == "continue_current":
            event_type = (
                "DOCUMENT_TASK_COMPLETED"
                if task_status == "completed"
                else "DOCUMENT_TASK_CHECKPOINTED"
            )
        else:
            event_type = (
                "DOCUMENT_TASK_COMPLETED"
                if task_status == "completed"
                else "DOCUMENT_TASK_STARTED"
            )
        if not events:
            if mode != "start_new":
                raise ConversationConflict(
                    "documenttaak kan niet zonder begin hervatten"
                )
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:1",
                occurred_at=self._timestamp(turn),
                data=data,
                event_type=event_type,
                phase="document_task_checkpointed",
                status="active",
                operation_id=self._document_episode_operation_id(turn),
            )
        else:
            state = timeline.load(thread_id)
            timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:{state.revision + 1}",
                operation_id=self._document_episode_operation_id(turn),
                phase="document_task_checkpointed",
                status="active",
                event_type=event_type,
                occurred_at=self._timestamp(turn),
                data=data,
            )
        receipt = self._document_receipt_for_turn(turn, timeline.events(thread_id))
        if receipt is None:
            raise ConversationConflict("documenttaakcheckpoint ontbreekt")
        return receipt

    @staticmethod
    def _validated_document_task_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _DOCUMENT_TASK_ARGUMENT_KEYS:
            raise ConversationConflict("documenttaakvelden verschillen")
        mode = raw.get("mode")
        status = raw.get("status")
        title = raw.get("title")
        filename = raw.get("filename")
        content = raw.get("content")
        if mode not in {"start_new", "continue_current", "replace_current"}:
            raise ConversationConflict("documenttaakmodus is ongeldig")
        if status not in {"active", "completed"}:
            raise ConversationConflict("documenttaakstatus is ongeldig")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ConversationConflict("documenttaaktitel is ongeldig")
        if (
            not isinstance(filename, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,115}\.md", filename) is None
        ):
            raise ConversationConflict("documenttaakbestandsnaam is ongeldig")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content.encode("utf-8")) > 131_072
        ):
            raise ConversationConflict("documenttaakinhoud is ongeldig")
        return {
            "content": content,
            "filename": filename,
            "mode": mode,
            "status": status,
            "title": title.strip(),
        }

    @staticmethod
    def _validate_completed_document(content: str) -> None:
        lines = content.splitlines()
        section_indexes = [
            index for index, line in enumerate(lines) if line.startswith("## ")
        ]
        if not section_indexes:
            raise ConversationConflict(
                "afgerond document heeft geen inhoudelijke secties"
            )
        for position, start in enumerate(section_indexes):
            stop = (
                section_indexes[position + 1]
                if position + 1 < len(section_indexes)
                else len(lines)
            )
            body = [
                line.strip()
                for line in lines[start + 1 : stop]
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if not body:
                raise ConversationConflict("afgerond document bevat een lege sectie")
        meaningful = " ".join(
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
        if len(meaningful) < 80:
            raise ConversationConflict(
                "afgerond document bevat te weinig bruikbare inhoud"
            )

    @classmethod
    def _document_receipt_for_turn(
        cls, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt | None:
        event = next(
            (
                item
                for item in reversed(events)
                if item.event_type in _DOCUMENT_TASK_EVENTS
                and cls._event_data(item).get("source_message_id") == turn.message_id
            ),
            None,
        )
        if event is None:
            return None
        data = cls._event_data(event)
        if (
            data.get("conversation_id") != turn.conversation_id
            or data.get("principal_id") != turn.principal_id
            or data.get("project_id") != turn.project_id
        ):
            raise ConversationConflict("documenttaakscope verschilt")
        call_id = data.get("action_call_id")
        if not isinstance(call_id, str):
            raise ConversationConflict("documenttaakcall ontbreekt")
        arguments = cls._validated_document_task_arguments(
            {key: data[key] for key in _DOCUMENT_TASK_ARGUMENT_KEYS if key in data}
        )
        content = str(arguments["content"])
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if data.get("content_sha256") != content_sha256 or data.get(
            "utf8_byte_length"
        ) != len(content.encode("utf-8")):
            raise ConversationConflict("documenttaakbytes verschillen")
        return ConversationActionReceipt(
            ConversationActionCall(call_id, _DOCUMENT_TASK_ACTION, arguments),
            {
                "artifact": {
                    "content": content,
                    "content_sha256": content_sha256,
                    "filename": arguments["filename"],
                    "media_type": "text/markdown",
                    "title": arguments["title"],
                    "utf8_byte_length": len(content.encode("utf-8")),
                },
                "contract": "EvaConversationActionReceipt/v1",
                "disposition": {
                    "DOCUMENT_TASK_STARTED": "started",
                    "DOCUMENT_TASK_CHECKPOINTED": "continued",
                    "DOCUMENT_TASK_COMPLETED": "completed",
                    "DOCUMENT_TASK_RESCOPED": "rescoped",
                }[event.event_type],
                "durable": True,
                "status": arguments["status"],
                "task_id": str(data["task_id"]),
            },
        )

    @classmethod
    def _current_document_task(
        cls, events: tuple[TimelineEvent, ...]
    ) -> dict[str, JsonValue] | None:
        focus: dict[str, JsonValue] | None = None
        for event in events:
            if event.event_type not in _DOCUMENT_TASK_EVENTS:
                continue
            data = cls._event_data(event)
            if event.event_type == "DOCUMENT_TASK_RESCOPED" and focus is not None:
                focus = None
            focus = data
        if focus is None or focus.get("status") != "active":
            return None
        required = {
            "content",
            "content_sha256",
            "filename",
            "status",
            "task_id",
            "title",
        }
        if not required.issubset(focus):
            raise ConversationConflict("actieve documenttaak is onvolledig")
        return focus

    def _execute_research(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if self._research is None:
            raise ConversationConflict("current research action is unavailable")
        arguments = self._validated_research_arguments(call.arguments)
        timeline = self._timeline(turn)
        thread_id = self._research_thread_id(turn)
        operation_id = self._research_operation_id(turn)
        admission: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "arguments": arguments,
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
        }
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:admitted",
                occurred_at=self._timestamp(turn),
                data=admission,
                event_type="CURRENT_RESEARCH_ADMITTED",
                phase="research_admitted",
                status="active",
                operation_id=operation_id,
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events or self._event_data(events[0]) != admission:
                raise ConversationConflict("current research replay differs") from None
        events = timeline.events(thread_id)
        self._resume_research(turn, events)
        return self._research_receipt(turn, timeline.events(thread_id))

    def _resume_research(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> None:
        if self._research is None or not events:
            raise ConversationConflict("current research cannot resume")
        admission = self._event_data(events[0])
        expected = {
            "action_name": _CURRENT_RESEARCH_ACTION,
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
        }
        if any(admission.get(key) != value for key, value in expected.items()):
            raise ConversationConflict("current research scope differs")
        raw_arguments = admission.get("arguments")
        if not isinstance(raw_arguments, dict):
            raise ConversationConflict("current research admission is malformed")
        arguments = self._validated_research_arguments(raw_arguments)
        timeline = self._timeline(turn)
        thread_id = self._research_thread_id(turn)
        state = timeline.load(thread_id)
        if state.status in {"completed", "needs_help"}:
            return
        try:
            artifact = self._research.research_current_options(
                question=arguments["question"],
                title=arguments["title"],
                options=tuple(arguments["options"]),  # type: ignore[arg-type]
                researched_at=turn.occurred_at,
                **({"research_mode": arguments["research_mode"]} if "research_mode" in arguments else {}),
            )
        except Exception as error:
            failed = timeline.record_recovery_failure(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:failed:{state.revision + 1}",
                operation_id=self._research_operation_id(turn),
                error_text=type(error).__name__,
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                **({"model_usage": error.model_usage}
                   if isinstance(getattr(error, "model_usage", None), ConversationModelUsage) else {}),
            )
            if failed.status != "needs_help":
                raise
            return
        data: dict[str, JsonValue] = {
            "content": artifact.content,
            "content_sha256": artifact.content_sha256,
            "media_type": "text/markdown",
            "provider": artifact.provider,
            "question": artifact.question,
            "researched_at": artifact.researched_at,
            "sources": [
                {"title": source.title, "url": source.url}
                for source in artifact.sources
            ],
            "title": artifact.title,
            "utf8_byte_length": len(artifact.content.encode("utf-8")),
            "model_usage": artifact.usage.to_wire(),
        }
        timeline.transition(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:completed",
            operation_id=self._research_operation_id(turn),
            phase="research_completed",
            status="completed",
            event_type="CURRENT_RESEARCH_COMPLETED",
            occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            data=data,
        )

    def _research_receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt:
        if not events or events[0].event_type != "CURRENT_RESEARCH_ADMITTED":
            raise ConversationConflict("current research event sequence differs")
        admission = self._event_data(events[0])
        raw_arguments = admission.get("arguments")
        call_id = admission.get("action_call_id")
        if not isinstance(raw_arguments, dict) or not isinstance(call_id, str):
            raise ConversationConflict("current research admission is malformed")
        arguments = self._validated_research_arguments(raw_arguments)
        if events[-1].event_type == "RECOVERY_NEEDS_HELP":
            return ConversationActionReceipt(
                ConversationActionCall(call_id, _CURRENT_RESEARCH_ACTION, arguments),
                {
                    "contract": "EvaConversationActionReceipt/v1",
                    "durable": True,
                    "status": "needs_help",
                },
            )
        if events[-1].event_type != "CURRENT_RESEARCH_COMPLETED":
            raise ConversationConflict("current research is not complete")
        artifact = self._research_artifact_from_event(events[-1])
        return ConversationActionReceipt(
            ConversationActionCall(call_id, _CURRENT_RESEARCH_ACTION, arguments),
            {
                "artifact": {
                    "content": artifact.content,
                    "content_sha256": artifact.content_sha256,
                    "media_type": "text/markdown",
                    "provider": artifact.provider,
                    "question": artifact.question,
                    "researched_at": artifact.researched_at,
                    "sources": [
                        {"title": source.title, "url": source.url}
                        for source in artifact.sources
                    ],
                    "title": artifact.title,
                    "model_usage": artifact.usage.to_wire(),
                },
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                "status": "verified",
                "verification_scope": "research_artifact_only",
            },
        )

    def _pending_durable_goal(
        self, turn: ConversationTurn
    ) -> ConversationActionReceipt | None:
        if self._durable_goal_provider is None:
            return None
        state = self._durable_goal_store(turn).state(
            self._durable_goal_id(turn), turn.principal_id
        )
        if state is None:
            return None
        call = ConversationActionCall(
            self._durable_goal_call_id(turn),
            _DURABLE_GOAL_ACTION,
            self._durable_goal_arguments(state.spec),
        )
        return self._durable_goal_receipt(call, state, "already_scheduled")

    def _execute_durable_goal(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if self._durable_goal_provider is None:
            raise ConversationConflict("durable goal execution is unavailable")
        arguments = self._validated_durable_goal_arguments(call.arguments)
        request_text = turn.text.casefold()
        if not any(
            word in request_text for word in ("zodra", "wanneer", "wacht", "als")
        ):
            raise ConversationConflict(
                "durable goal requires an explicit wake condition"
            )
        recipient = str(arguments["delivery_recipient"])
        if recipient not in self._durable_goal_recipients:
            raise ConversationConflict("durable goal recipient is not configured")
        deadline = datetime.fromisoformat(
            str(arguments["wake_deadline"]).replace("Z", "+00:00")
        )
        created_at = self._timestamp(turn)
        if deadline <= created_at or deadline > created_at + timedelta(days=7):
            raise ConversationConflict(
                "durable goal deadline is outside 1 second..7 days"
            )
        goal = DurableGoalSpec(
            goal_id=self._durable_goal_id(turn),
            owner_id=turn.principal_id,
            description=str(arguments["description"]),
            primary_instruction=str(arguments["primary_instruction"]),
            pivot_instruction=(
                None
                if arguments["pivot"] is None
                else str(arguments["pivot"]["instruction"])  # type: ignore[index]
            ),
            suitability_field=(
                None
                if arguments["pivot"] is None
                else str(arguments["pivot"]["suitability_field"])  # type: ignore[index]
            ),
            unsuitable_value=(
                None
                if arguments["pivot"] is None
                else str(arguments["pivot"]["unsuitable_value"])  # type: ignore[index]
            ),
            delivery_recipient=recipient,
            wake=GoalWakeCondition(
                provider=self._durable_goal_provider,
                resource=str(arguments["wake_resource"]),
                field=str(arguments["wake_field"]),
                expected_value=str(arguments["wake_expected_value"]),
                deadline=deadline,
            ),
            created_at=created_at,
        )
        store = self._durable_goal_store(turn)
        store.admit(goal)
        store.schedule_wake(goal)
        state = store.state(goal.goal_id, goal.owner_id)
        if state is None:
            raise ConversationConflict("durable goal was not persisted")
        return self._durable_goal_receipt(call, state, "scheduled")

    def _execute_durable_goal_cancellation(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if self._durable_goal_provider is None:
            raise ConversationConflict("durable goal cancellation is unavailable")
        if set(call.arguments) != _DURABLE_GOAL_CANCEL_ARGUMENT_KEYS:
            raise ConversationConflict("durable goal cancellation fields differ")
        reason = call.arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
            raise ConversationConflict("durable goal cancellation reason is invalid")
        if not any(
            word in turn.text.casefold() for word in ("annuleer", "stop", "hoeft niet")
        ):
            raise ConversationConflict("durable goal cancellation was not explicit")
        store = self._durable_goal_store(turn)
        owned = [
            goal
            for goal in store.waiting(observed_before=self._timestamp(turn), limit=32)
            if goal.owner_id == turn.principal_id
        ]
        if len(owned) != 1:
            raise ConversationConflict(
                "durable goal cancellation requires exactly one active goal"
            )
        goal = owned[0]
        store.cancel(
            goal.goal_id,
            goal.owner_id,
            reason.strip(),
            self._timestamp(turn),
        )
        state = store.state(goal.goal_id, goal.owner_id)
        if state is None:
            raise ConversationConflict("cancelled durable goal disappeared")
        return ConversationActionReceipt(
            call,
            {
                "contract": "EvaDurableGoalActionReceipt/v1",
                "description": goal.description,
                "disposition": "cancelled",
                "goal_id": goal.goal_id,
                "reason": reason.strip(),
                "status": state.status,
            },
        )

    @staticmethod
    def _validated_durable_goal_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _DURABLE_GOAL_ARGUMENT_KEYS:
            raise ConversationConflict("durable goal fields differ")
        required_strings = (
            "delivery_recipient",
            "description",
            "primary_instruction",
            "wake_deadline",
            "wake_expected_value",
            "wake_field",
            "wake_resource",
        )
        if any(
            not isinstance(raw.get(key), str)
            or not str(raw[key]).strip()
            or len(str(raw[key])) > 2000
            for key in required_strings
        ):
            raise ConversationConflict("durable goal text fields are invalid")
        pivot = raw.get("pivot")
        if pivot is not None and (
            not isinstance(pivot, dict)
            or set(pivot) != {"instruction", "suitability_field", "unsuitable_value"}
            or not all(
                isinstance(value, str) and value.strip() for value in pivot.values()
            )
        ):
            raise ConversationConflict("durable goal pivot is incomplete")
        try:
            datetime.fromisoformat(str(raw["wake_deadline"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ConversationConflict("durable goal deadline is invalid") from error
        return dict(raw)

    @staticmethod
    def _durable_goal_id(turn: ConversationTurn) -> str:
        return (
            "durable-goal:"
            + hashlib.sha256(
                canonical_json_bytes(
                    {
                        "conversation_id": turn.conversation_id,
                        "message_id": turn.message_id,
                        "principal_id": turn.principal_id,
                    }
                )
            ).hexdigest()[:40]
        )

    @classmethod
    def _durable_goal_call_id(cls, turn: ConversationTurn) -> str:
        return (
            "call:"
            + hashlib.sha256(cls._durable_goal_id(turn).encode()).hexdigest()[:24]
        )

    @staticmethod
    def _durable_goal_arguments(goal: DurableGoalSpec) -> dict[str, JsonValue]:
        return {
            "delivery_recipient": goal.delivery_recipient,
            "description": goal.description,
            "pivot": (
                None
                if goal.pivot_instruction is None
                else {
                    "instruction": goal.pivot_instruction,
                    "suitability_field": goal.suitability_field,
                    "unsuitable_value": goal.unsuitable_value,
                }
            ),
            "primary_instruction": goal.primary_instruction,
            "wake_deadline": goal.wake.deadline.isoformat().replace("+00:00", "Z"),
            "wake_expected_value": goal.wake.expected_value,
            "wake_field": goal.wake.field,
            "wake_resource": goal.wake.resource,
        }

    @staticmethod
    def _durable_goal_receipt(
        call: ConversationActionCall,
        state: DurableGoalState,
        disposition: str,
    ) -> ConversationActionReceipt:
        goal = state.spec
        return ConversationActionReceipt(
            call,
            {
                "contract": "EvaDurableGoalActionReceipt/v1",
                "delivery_recipient": goal.delivery_recipient,
                "description": goal.description,
                "disposition": disposition,
                "goal_id": goal.goal_id,
                "status": state.status,
                "wake": goal.wake.to_payload(),
            },
        )

    def _pending_fanout(
        self, turn: ConversationTurn
    ) -> ConversationActionReceipt | None:
        if self._fanout_delivery is None:
            return None
        store = PostgresFanoutDeliveryStore(
            self._engine,
            tenant_id=turn.tenant_id,
            installation_id=turn.installation_id,
        )
        request = store.find(
            operation_id=self._fanout_operation_id(turn),
            requested_by=turn.principal_id,
        )
        if request is None:
            return None
        result = asyncio.run(
            FanoutDeliveryWorker(store, self._fanout_delivery).run(request)
        )
        call = ConversationActionCall(
            self._fanout_call_id(turn),
            _FANOUT_DELIVERY_ACTION,
            {
                "message": request.message,
                "recipients": list(request.recipients),
            },
        )
        return self._fanout_receipt(call, result)

    def _execute_fanout(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        if self._fanout_delivery is None:
            raise ConversationConflict("fan-out delivery is unavailable")
        arguments = self._validated_fanout_arguments(call.arguments)
        recipients = tuple(arguments["recipients"])
        request_text = turn.text.casefold()
        if (
            len(recipients) < 2
            or not all(recipient.casefold() in request_text for recipient in recipients)
            or not any(word in request_text for word in ("stuur", "bezorg", "lever"))
        ):
            raise ConversationConflict(
                "fan-out delivery requires an explicit multi-recipient request"
            )
        request = FanoutDeliveryRequest(
            operation_id=self._fanout_operation_id(turn),
            requested_by=turn.principal_id,
            message=str(arguments["message"]),
            recipients=recipients,
            created_at=self._timestamp(turn),
        )
        store = PostgresFanoutDeliveryStore(
            self._engine,
            tenant_id=turn.tenant_id,
            installation_id=turn.installation_id,
        )
        result = asyncio.run(
            FanoutDeliveryWorker(store, self._fanout_delivery).run(request)
        )
        return self._fanout_receipt(call, result)

    @staticmethod
    def _fanout_receipt(
        call: ConversationActionCall, result: FanoutDeliveryResult
    ) -> ConversationActionReceipt:
        return ConversationActionReceipt(
            call,
            {
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                **result.to_payload(),
            },
        )

    def _link_to_conversation(
        self, turn: ConversationTurn, receipt: ConversationActionReceipt
    ) -> None:
        timeline = self._timeline(turn)
        thread_id = f"conversation:{turn.message_id}"
        data: dict[str, JsonValue] = {
            "action_name": receipt.call.name,
            "conversation_id": turn.conversation_id,
            "intent_id": str(receipt.output["intent_id"]),
            "kind": "conversation_action_link",
            "message_id": turn.message_id,
            "principal_id": turn.principal_id,
        }
        for event in timeline.events(thread_id):
            if event.event_type != "PERSONAL_INTENT_LINKED":
                continue
            durable = self._event_data(event)
            if durable != data:
                raise ConversationConflict("conversation action link differs")
            return
        state = timeline.load(thread_id)
        try:
            timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"conversation:{turn.message_id}:intent-linked",
                operation_id=str(receipt.output["intent_id"]),
                phase="intent_linked",
                status="active",
                event_type="PERSONAL_INTENT_LINKED",
                occurred_at=self._timestamp(turn),
                data=data,
            )
        except CheckpointConflict:
            for event in timeline.events(thread_id):
                if (
                    event.event_type == "PERSONAL_INTENT_LINKED"
                    and self._event_data(event) == data
                ):
                    return
            raise ConversationConflict(
                "conversation action link raced without a matching event"
            ) from None

    def _receipt_from_event(
        self,
        turn: ConversationTurn,
        event: TimelineEvent,
        *,
        disposition: str,
    ) -> ConversationActionReceipt:
        if event.event_type != "PERSONAL_INTENT_ADOPTED":
            raise ConversationConflict("personal intent event type differs")
        self._require_same_intent_scope(turn, event)
        data = self._event_data(event)
        arguments = self._validated_arguments(
            {
                "motivation": data.get("motivation"),
                "next_exploration": data.get("next_exploration"),
                "title": data.get("title"),
            }
        )
        call_id = data.get("action_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise ConversationConflict("personal intent action call is invalid")
        call = ConversationActionCall(call_id, _ACTION, arguments)
        return ConversationActionReceipt(
            call,
            {
                "contract": "EvaConversationActionReceipt/v1",
                "disposition": disposition,
                "durable": True,
                "intent_id": str(data["intent_id"]),
                "motivation": arguments["motivation"],
                "next_exploration": arguments["next_exploration"],
                "state": "active",
                "title": arguments["title"],
            },
        )

    def _require_same_intent_scope(
        self, turn: ConversationTurn, event: TimelineEvent
    ) -> None:
        data = self._event_data(event)
        expected = {
            "action_name": _ACTION,
            "conversation_id": turn.conversation_id,
            "intent_id": self._intent_id(turn),
            "kind": "personal_interest",
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
        }
        for key, value in expected.items():
            if data.get(key) != value:
                raise ConversationConflict("personal intent replay scope differs")

    @staticmethod
    def _event_data(event: TimelineEvent) -> dict[str, JsonValue]:
        payload = event.payload
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ConversationConflict("personal intent event data is malformed")
        return data

    @staticmethod
    def _validated_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _ARGUMENT_KEYS:
            raise ConversationConflict("personal interest has wrong fields")
        limits = {"title": 160, "motivation": 800, "next_exploration": 800}
        result: dict[str, JsonValue] = {}
        for key, maximum in limits.items():
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ConversationConflict(f"personal interest {key} is invalid")
            result[key] = value.strip()
        return result

    @staticmethod
    def _validated_observation_identity_correction_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _OBSERVATION_IDENTITY_CORRECTION_ARGUMENT_KEYS:
            raise ConversationConflict(
                "observation identity correction has wrong fields"
            )
        corrected_name = raw.get("corrected_name")
        if (
            not isinstance(corrected_name, str)
            or not corrected_name.strip()
            or len(corrected_name) > 120
            or any(ord(character) < 32 for character in corrected_name)
        ):
            raise ConversationConflict("observation identity name is invalid")
        return {"corrected_name": corrected_name.strip()}

    @staticmethod
    def _validated_fanout_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(raw) != _FANOUT_DELIVERY_ARGUMENT_KEYS:
            raise ConversationConflict("fan-out delivery has wrong fields")
        message = raw.get("message")
        recipients = raw.get("recipients")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > 4096
            or not isinstance(recipients, list)
            or not 1 <= len(recipients) <= 8
            or not all(
                isinstance(item, str) and item.strip() and len(item) <= 160
                for item in recipients
            )
        ):
            raise ConversationConflict("fan-out delivery arguments are invalid")
        normalized = [str(item).strip() for item in recipients]
        if len(set(normalized)) != len(normalized):
            raise ConversationConflict("fan-out delivery recipients are duplicated")
        return {"message": message.strip(), "recipients": normalized}

    @staticmethod
    def _validated_research_arguments(
        raw: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        research_mode = raw.get("research_mode")
        if "research_mode" in raw and research_mode not in ("topic", "comparison"):
            raise ConversationConflict("current research mode is invalid")
        public_review = raw.get("public_review")
        if "public_review" in raw and (not isinstance(public_review, str)
                or public_review not in {"public_unlinked", "uncertain", "not_applicable"}):
            raise ConversationConflict("current research public input review is invalid")
        keys = set(raw) - {"public_review", "research_mode"}
        if keys not in {
            frozenset(_CURRENT_RESEARCH_ARGUMENT_KEYS),
            frozenset(_CURRENT_RESEARCH_LEGACY_ARGUMENT_KEYS),
        }:
            raise ConversationConflict("current research has wrong fields")
        question = raw.get("question")
        title = raw.get("title")
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > 2_000
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 200
        ):
            raise ConversationConflict("current research arguments are invalid")
        result: dict[str, JsonValue] = {
            "question": question.strip(),
            "title": title.strip(),
        }
        if "research_mode" in raw:
            result["research_mode"] = research_mode
        if "public_review" in raw:
            result["public_review"] = public_review
        if keys == _CURRENT_RESEARCH_ARGUMENT_KEYS:
            options = raw.get("options")
            if (
                not isinstance(options, list)
                or not 1 <= len(options) <= 5
                or any(
                    not isinstance(option, str)
                    or not option.strip()
                    or len(option) > 120
                    for option in options
                )
            ):
                raise ConversationConflict("current research options are invalid")
            result["options"] = [str(option).strip() for option in options]
        return result

    @staticmethod
    def _research_artifact_from_event(event: TimelineEvent) -> ResearchArtifact:
        data = PostgresConversationActions._event_data(event)
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise ConversationConflict("current research sources are malformed")
        try:
            artifact = ResearchArtifact(
                title=str(data["title"]),
                question=str(data["question"]),
                content=str(data["content"]),
                sources=tuple(
                    ResearchSource(str(item["title"]), str(item["url"]))
                    for item in sources
                    if isinstance(item, dict)
                ),
                researched_at=str(data["researched_at"]),
                provider=str(data["provider"]),
                usage=ConversationModelUsage.from_wire(data["model_usage"])
                if "model_usage" in data else ConversationModelUsage.unmeasured(),
            )
        except (KeyError, ValueError) as error:
            raise ConversationConflict(
                "current research artifact is malformed"
            ) from error
        if (
            artifact.content_sha256 != data.get("content_sha256")
            or len(artifact.content.encode("utf-8")) != data.get("utf8_byte_length")
            or data.get("media_type") != "text/markdown"
            or len(artifact.sources) != len(sources)
        ):
            raise ConversationConflict("current research artifact binding differs")
        return artifact

    @staticmethod
    def _timestamp(turn: ConversationTurn) -> datetime:
        return datetime.fromisoformat(turn.occurred_at.replace("Z", "+00:00"))

    @staticmethod
    def _intent_id(turn: ConversationTurn) -> str:
        return _turn_id(turn, "personal_interest", "personal-interest:", "person")

    @classmethod
    def _intent_thread_id(cls, turn: ConversationTurn) -> str:
        return f"intent:{cls._intent_id(turn)}"

    @staticmethod
    def _fanout_operation_id(turn: ConversationTurn) -> str:
        return _turn_id(turn, "fanout_delivery", "fanout-delivery:", "person")

    @classmethod
    def _fanout_call_id(cls, turn: ConversationTurn) -> str:
        return (
            "fanout-call:"
            + hashlib.sha256(cls._fanout_operation_id(turn).encode()).hexdigest()[:32]
        )

    @staticmethod
    def _research_operation_id(turn: ConversationTurn) -> str:
        return _turn_id(turn, "current_research", "current-research:", "person")

    @classmethod
    def _research_thread_id(cls, turn: ConversationTurn) -> str:
        return f"research:{cls._research_operation_id(turn)}"

    @staticmethod
    def _document_episode_operation_id(turn: ConversationTurn) -> str:
        return _turn_id(turn, "document_work_episode", "document-work:", "document")

    @classmethod
    def _document_episode_thread_id(cls, turn: ConversationTurn) -> str:
        return f"work:{cls._document_episode_operation_id(turn)}"

    @staticmethod
    def _document_task_id(turn: ConversationTurn) -> str:
        return _turn_id(turn, "document_task", "document-task:", "document_task")

    @staticmethod
    def _file_batch_operation_id(turn: ConversationTurn) -> str:
        return _turn_id(turn, "text_to_markdown_batch", "file-batch:", "project")

    @classmethod
    def _file_batch_thread_id(cls, turn: ConversationTurn) -> str:
        return f"work:{cls._file_batch_operation_id(turn)}"

    @staticmethod
    def _validated_file_edit_operation_id(turn: ConversationTurn) -> str:
        return _turn_id(
            turn, "validated_text_file_edit", "validated-file-edit:", "project"
        )

    @classmethod
    def _validated_file_edit_thread_id(cls, turn: ConversationTurn) -> str:
        return f"work:{cls._validated_file_edit_operation_id(turn)}"

    @staticmethod
    def _repository_improvement_operation_id(turn: ConversationTurn) -> str:
        return _turn_id(
            turn,
            "self_chosen_repository_improvement",
            "repository-improvement:",
            "project",
        )

    @classmethod
    def _repository_improvement_thread_id(cls, turn: ConversationTurn) -> str:
        return f"work:{cls._repository_improvement_operation_id(turn)}"

    @staticmethod
    def _observation_identity_correction_thread_id(turn: ConversationTurn) -> str:
        return _turn_id(
            turn,
            "observation_identity_correction",
            "memory:observation-identity:",
            "project",
        )

    def _timeline(self, turn: ConversationTurn) -> PostgresOperationalTimeline:
        return self._timeline_for(turn.tenant_id, turn.installation_id)

    def _timeline_for(
        self, tenant_id: str, installation_id: str
    ) -> PostgresOperationalTimeline:
        return PostgresOperationalTimeline(
            self._engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL,
                    f"eva2:{tenant_id}:{installation_id}",
                ),
            ),
        )


class _PostgresPersonalClaimStore:
    """Claim one personal-work stage through the shared durable lease fence."""

    _PREFIX = "intent:personal-interest:"
    _stage: str

    def __init__(self, engine: sa.Engine, *, lease_seconds: int = 90) -> None:
        lease_name = "product release" if self._stage == "release" else self._stage
        if not 10 <= lease_seconds <= 600:
            raise ValueError(f"personal {lease_name} lease must be 10..600 seconds")
        self._actions = PostgresConversationActions(engine)
        self._lease_seconds = lease_seconds

    def claim_next(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> (
        PersonalInterestClaim
        | PersonalPrototypeClaim
        | PersonalProductReleaseClaim
        | None
    ):
        stage = self._stage
        phases, statuses, event_type = {
            "exploration": (
                ("intent_adopted", "exploration_claimed"),
                ("active", "failed", "waiting_for_proof"),
                "PERSONAL_EXPLORATION_CLAIMED",
            ),
            "prototype": (
                ("exploration_projected", "prototype_claimed"),
                ("completed", "failed", "waiting_for_proof"),
                "PERSONAL_PROTOTYPE_CLAIMED",
            ),
            "release": (
                ("prototype_projected", "release_claimed"),
                ("completed", "failed", "waiting_for_proof"),
                "PERSONAL_PRODUCT_RELEASE_CLAIMED",
            ),
        }[stage]
        timeline = self._actions._timeline_for(tenant_id, installation_id)
        candidates = timeline.list_checkpoints(
            thread_prefix=self._PREFIX,
            phases=phases,
            statuses=statuses,
            updated_before=now,
        )
        for state in candidates:
            if state.status == "waiting_for_proof":
                if state.deadline is None or state.deadline > now:
                    continue
                expired = (
                    "lease-expired"
                    if stage == "exploration"
                    else f"{stage}-lease-expired"
                )
                lease_name = "product release" if stage == "release" else stage
                failed = timeline.record_recovery_failure(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:{expired}:{state.revision + 1}",
                    operation_id=state.operation_id,
                    error_text=f"personal {lease_name} lease expired",
                    occurred_at=now,
                )
                if failed.status == "needs_help":
                    continue
                state = failed
            events = timeline.events(state.thread_id)
            if stage == "exploration":
                source = self._claim_from_events(
                    tenant_id, installation_id, events, state.revision + 1
                )
                operation_id = source.intent_id
                data = {
                    "intent_id": source.intent_id,
                    "question": source.question,
                    "title": source.title,
                }
            elif stage == "prototype":
                source = self._exploration_from_events(
                    tenant_id, installation_id, events, state.revision
                )
                operation_id = source.claim.intent_id
                data = {
                    "exploration_sha256": source.exploration.text_sha256,
                    "intent_id": operation_id,
                }
            else:
                source = self._completed_prototype(
                    tenant_id, installation_id, events, state.revision
                )
                operation_id = source.claim.completed_exploration.claim.intent_id
                data = {
                    "intent_id": operation_id,
                    "prototype_sha256": source.prototype.content_sha256,
                }
            try:
                claimed = timeline.transition(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:{stage}:{state.revision + 1}",
                    operation_id=operation_id,
                    phase=f"{stage}_claimed",
                    status="waiting_for_proof",
                    event_type=event_type,
                    occurred_at=now,
                    deadline=now + timedelta(seconds=self._lease_seconds),
                    retry_count=state.retry_count,
                    last_error_fingerprint=state.last_error_fingerprint,
                    data=data,
                )
            except CheckpointConflict:
                continue
            if stage == "exploration":
                return PersonalInterestClaim(
                    **{
                        field: getattr(source, field)
                        for field in source.__dataclass_fields__
                        if field != "revision"
                    },
                    revision=claimed.revision,
                )
            if stage == "prototype":
                return PersonalPrototypeClaim(source, claimed.revision)
            return PersonalProductReleaseClaim(source, claimed.revision)
        return None

    def _interest(self, value: Any) -> PersonalInterestClaim:
        if self._stage == "exploration":
            return value
        if self._stage == "prototype":
            return value.completed_exploration.claim
        return value.completed_prototype.claim.completed_exploration.claim

    def _transition(
        self,
        interest: PersonalInterestClaim,
        *,
        expected_revision: int,
        checkpoint: str,
        phase: str,
        status: str,
        event_type: str,
        now: datetime,
        data: dict[str, JsonValue],
    ):
        timeline = self._actions._timeline_for(
            interest.tenant_id, interest.installation_id
        )
        return timeline.transition(
            thread_id=interest.thread_id,
            expected_revision=expected_revision,
            checkpoint_id=f"{interest.thread_id}:{checkpoint}",
            operation_id=interest.intent_id,
            phase=phase,
            status=status,
            event_type=event_type,
            occurred_at=now,
            data=data,
        )

    def mark_projected(self, completed: Any, *, now: datetime) -> None:
        interest = self._interest(completed.claim)
        if self._stage == "exploration":
            checkpoint = "exploration-projected"
            phase = "exploration_projected"
            event_type = "PERSONAL_EXPLORATION_PROJECTED"
            data = {
                "document_id": f"{interest.intent_id}:exploration:v1",
                "exploration_sha256": completed.exploration.text_sha256,
                "intent_id": interest.intent_id,
            }
        elif self._stage == "prototype":
            checkpoint = "prototype-projected"
            phase = "prototype_projected"
            event_type = "PERSONAL_PROTOTYPE_PROJECTED"
            data = {
                "content_sha256": completed.prototype.content_sha256,
                "document_id": f"{interest.intent_id}:prototype:v1",
                "filename": completed.prototype.filename,
                "intent_id": interest.intent_id,
            }
        else:
            checkpoint = "release-ready"
            phase = "release_ready"
            event_type = "PERSONAL_PRODUCT_RELEASE_READY"
            data = {
                "content_sha256": completed.release.content_sha256,
                "filename": completed.release.filename,
                "intent_id": interest.intent_id,
                "publication_status": "unpublished_release_candidate",
            }
        self._transition(
            interest,
            expected_revision=completed.revision,
            checkpoint=checkpoint,
            phase=phase,
            status="completed",
            event_type=event_type,
            now=now,
            data=data,
        )

    def record_failure(self, claim: Any, error: Exception, *, now: datetime) -> None:
        interest = self._interest(claim)
        timeline = self._actions._timeline_for(
            interest.tenant_id, interest.installation_id
        )
        timeline.record_recovery_failure(
            thread_id=interest.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=(
                f"{interest.thread_id}:{self._stage}-failed:{claim.revision + 1}"
            ),
            operation_id=interest.intent_id,
            error_text=f"{type(error).__name__}: {error}",
            occurred_at=now,
        )

    @staticmethod
    def _latest_event(
        events: tuple[TimelineEvent, ...], event_type: str
    ) -> TimelineEvent | None:
        return next(
            (event for event in reversed(events) if event.event_type == event_type),
            None,
        )

    def _completed_events(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> tuple[CheckpointState, tuple[TimelineEvent, ...]] | None:
        timeline = self._actions._timeline_for(tenant_id, installation_id)
        states = timeline.list_checkpoints(
            thread_prefix=self._PREFIX,
            phases=(f"{self._stage}_completed",),
            statuses=("active",),
            updated_before=now,
        )
        if not states:
            return None
        return states[0], timeline.events(states[0].thread_id)

    def _latest_artifact_events(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        now: datetime,
        intent_id: str | None = None,
        limit: int = 32,
    ) -> tuple[str, tuple[TimelineEvent, ...]] | None:
        phases = (
            (
                "prototype_completed",
                "prototype_projected",
                "release_claimed",
                "release_completed",
                "release_ready",
            )
            if self._stage == "prototype"
            else ("release_completed", "release_ready")
        )
        statuses = (
            ("active", "waiting_for_proof", "completed", "failed", "needs_help")
            if self._stage == "prototype"
            else ("active", "completed")
        )
        timeline = self._actions._timeline_for(tenant_id, installation_id)
        states = timeline.list_checkpoints(
            thread_prefix=self._PREFIX,
            phases=phases,
            statuses=statuses,
            updated_before=now,
            limit=limit,
        )
        for state in states:
            events = timeline.events(state.thread_id)
            adopted = next(
                (
                    event
                    for event in events
                    if event.event_type == "PERSONAL_INTENT_ADOPTED"
                ),
                None,
            )
            if adopted is None:
                continue
            data = PostgresConversationActions._event_data(adopted)
            if data.get("principal_id") != principal_id:
                continue
            candidate_id = data.get("intent_id")
            if intent_id is not None and candidate_id != intent_id:
                continue
            if not isinstance(candidate_id, str) or not candidate_id:
                label = "product release" if self._stage == "release" else self._stage
                raise ConversationConflict(f"{label} intent ID is malformed")
            return candidate_id, events
        return None


class PostgresPersonalInterestStore(_PostgresPersonalClaimStore):
    """Lease and finish personal exploration through durable checkpoints."""

    _stage = "exploration"

    def complete_native(self, claim, reply, *, now):
        if reply.provider in {"runtime:conversation-limit", "runtime:conversation-stop"}:
            return self.block_native(claim, RuntimeError(reply.text), now=now)
        return self._transition(claim, expected_revision=claim.revision,
            checkpoint="initiative-completed", phase="initiative_completed", status="completed",
            event_type="PERSONAL_INITIATIVE_COMPLETED", now=now,
            data={"intent_id": claim.intent_id, "origin": "eva_initiative",
                  "content": reply.text, "content_sha256": reply.text_sha256,
                  "filename": "initiatief-resultaat.md", "provider": reply.provider,
                  "conversation_id": reply.conversation_id, "message_id": reply.message_id,
                  "model_usage": reply.model_usage.to_wire()})

    def block_native(self, claim, error, *, now):
        try:
            return self._transition(claim, expected_revision=claim.revision,
                checkpoint="initiative-blocked", phase="initiative_blocked", status="needs_help",
                event_type="PERSONAL_INITIATIVE_BLOCKED", now=now,
                data={"intent_id": claim.intent_id, "reason": str(error)[:800],
                      "origin": "eva_initiative"})
        except CheckpointConflict:
            # Owner stop or a newer lease won; never overwrite that decision.
            return None

    def completed_unprojected(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> CompletedPersonalExploration | None:
        candidate = self._completed_events(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if candidate is None:
            return None
        state, events = candidate
        claim = self._claim_from_events(
            tenant_id, installation_id, events, state.revision
        )
        completed = self._latest_event(events, "PERSONAL_EXPLORATION_COMPLETED")
        if completed is None:
            raise ConversationConflict("completed exploration has no result event")
        data = PostgresConversationActions._event_data(completed)
        text = data.get("exploration_text")
        provider = data.get("provider")
        expected_hash = data.get("exploration_sha256")
        if (
            not isinstance(text, str)
            or not isinstance(provider, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash
        ):
            raise ConversationConflict("completed exploration result differs")
        return CompletedPersonalExploration(
            claim,
            PersonalInterestExploration(text, provider),
            state.revision,
        )

    def complete(
        self,
        claim: PersonalInterestClaim,
        exploration: PersonalInterestExploration,
        *,
        now: datetime,
    ) -> CompletedPersonalExploration:
        completed = self._transition(
            claim,
            expected_revision=claim.revision,
            checkpoint="exploration-completed",
            phase="exploration_completed",
            status="active",
            event_type="PERSONAL_EXPLORATION_COMPLETED",
            now=now,
            data={
                "exploration_sha256": exploration.text_sha256,
                "exploration_text": exploration.text,
                "intent_id": claim.intent_id,
                "provider": exploration.provider,
            },
        )
        return CompletedPersonalExploration(claim, exploration, completed.revision)

    @staticmethod
    def _claim_from_events(
        tenant_id: str,
        installation_id: str,
        events: tuple[TimelineEvent, ...],
        revision: int,
    ) -> PersonalInterestClaim:
        adopted = next(
            (
                event
                for event in events
                if event.event_type == "PERSONAL_INTENT_ADOPTED"
            ),
            None,
        )
        if adopted is None:
            raise ConversationConflict("personal exploration lacks adopted intent")
        data = PostgresConversationActions._event_data(adopted)
        required = {
            key: data.get(key)
            for key in (
                "conversation_id",
                "intent_id",
                "motivation",
                "next_exploration",
                "principal_id",
                "source_message_id",
                "title",
            )
        }
        if any(not isinstance(value, str) or not value for value in required.values()):
            raise ConversationConflict("personal exploration intent is malformed")
        return PersonalInterestClaim(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=str(required["principal_id"]),
            conversation_id=str(required["conversation_id"]),
            source_message_id=str(required["source_message_id"]),
            intent_id=str(required["intent_id"]),
            thread_id=adopted.thread_id,
            title=str(required["title"]),
            motivation=str(required["motivation"]),
            question=str(required["next_exploration"]),
            occurred_at=adopted.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            revision=revision,
            execution_mode=str(data.get("execution_mode", "private_prototype")),
        )


class PostgresPersonalPrototypeStore(_PostgresPersonalClaimStore):
    """Persist one concrete prototype after a durable exploration."""

    _stage = "prototype"

    def completed_unprojected(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> CompletedPersonalPrototype | None:
        candidate = self._completed_events(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if candidate is None:
            return None
        state, events = candidate
        claim = PersonalPrototypeClaim(
            self._exploration_from_events(
                tenant_id, installation_id, events, state.revision
            ),
            state.revision,
        )
        prototype_event = self._latest_event(events, "PERSONAL_PROTOTYPE_COMPLETED")
        if prototype_event is None:
            raise ConversationConflict("completed prototype has no artifact event")
        data = PostgresConversationActions._event_data(prototype_event)
        prototype = self._prototype_from_data(data)
        return CompletedPersonalPrototype(claim, prototype, state.revision)

    def complete(
        self,
        claim: PersonalPrototypeClaim,
        prototype: PersonalPrototype,
        *,
        now: datetime,
    ) -> CompletedPersonalPrototype:
        interest = self._interest(claim)
        completed = self._transition(
            interest,
            expected_revision=claim.revision,
            checkpoint="prototype-completed",
            phase="prototype_completed",
            status="active",
            event_type="PERSONAL_PROTOTYPE_COMPLETED",
            now=now,
            data={
                "content": prototype.content,
                "content_sha256": prototype.content_sha256,
                "filename": prototype.filename,
                "intent_id": interest.intent_id,
                "media_type": prototype.media_type,
                "provider": prototype.provider,
                "title": prototype.title,
                "utf8_byte_length": len(prototype.content.encode("utf-8")),
            },
        )
        return CompletedPersonalPrototype(claim, prototype, completed.revision)

    def latest(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        now: datetime,
    ) -> PersonalPrototypeArtifact | None:
        candidate = self._latest_artifact_events(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            now=now,
        )
        if candidate is None:
            return None
        intent_id, events = candidate
        prototype_event = self._latest_event(events, "PERSONAL_PROTOTYPE_COMPLETED")
        if prototype_event is None:
            return None
        return PersonalPrototypeArtifact(
            intent_id,
            self._prototype_from_data(
                PostgresConversationActions._event_data(prototype_event)
            ),
        )

    @staticmethod
    def _exploration_from_events(
        tenant_id: str,
        installation_id: str,
        events: tuple[TimelineEvent, ...],
        revision: int,
    ) -> CompletedPersonalExploration:
        interest = PostgresPersonalInterestStore._claim_from_events(
            tenant_id, installation_id, events, revision
        )
        result_event = _PostgresPersonalClaimStore._latest_event(
            events, "PERSONAL_EXPLORATION_COMPLETED"
        )
        if result_event is None:
            raise ConversationConflict("prototype lacks completed exploration")
        data = PostgresConversationActions._event_data(result_event)
        text = data.get("exploration_text")
        provider = data.get("provider")
        expected_hash = data.get("exploration_sha256")
        if (
            not isinstance(text, str)
            or not isinstance(provider, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash
        ):
            raise ConversationConflict("prototype exploration differs")
        return CompletedPersonalExploration(
            interest, PersonalInterestExploration(text, provider), revision
        )

    @staticmethod
    def _prototype_from_data(data: dict[str, JsonValue]) -> PersonalPrototype:
        values = [
            data.get("title"),
            data.get("filename"),
            data.get("media_type"),
            data.get("content"),
            data.get("provider"),
        ]
        if any(not isinstance(value, str) for value in values):
            raise ConversationConflict("personal prototype event is malformed")
        prototype = PersonalPrototype(*values)  # type: ignore[arg-type]
        if prototype.content_sha256 != data.get("content_sha256"):
            raise ConversationConflict("personal prototype hash differs")
        if len(prototype.content.encode("utf-8")) != data.get("utf8_byte_length"):
            raise ConversationConflict("personal prototype length differs")
        return prototype


class PostgresPersonalProductReleaseStore(_PostgresPersonalClaimStore):
    """Persist a channel-neutral sales bundle without claiming publication."""

    _stage = "release"

    def completed_unprojected(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> CompletedPersonalProductRelease | None:
        candidate = self._completed_events(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if candidate is None:
            return None
        state, events = candidate
        claim = PersonalProductReleaseClaim(
            self._completed_prototype(
                tenant_id, installation_id, events, state.revision
            ),
            state.revision,
        )
        release_event = self._release_event(events)
        return CompletedPersonalProductRelease(
            claim,
            self._release_from_data(
                PostgresConversationActions._event_data(release_event)
            ),
            state.revision,
        )

    def complete(
        self,
        claim: PersonalProductReleaseClaim,
        release: PersonalProductRelease,
        *,
        now: datetime,
    ) -> CompletedPersonalProductRelease:
        interest = self._interest(claim)
        completed = self._transition(
            interest,
            expected_revision=claim.revision,
            checkpoint="release-completed",
            phase="release_completed",
            status="active",
            event_type="PERSONAL_PRODUCT_RELEASE_COMPLETED",
            now=now,
            data={
                "content_base64": release.content_base64,
                "content_sha256": release.content_sha256,
                "filename": release.filename,
                "headline": release.listing.headline,
                "description": release.listing.description,
                "intent_id": interest.intent_id,
                "price_eur_cents": release.listing.price_eur_cents,
                "provider": release.listing.provider,
                "publication_status": "unpublished_release_candidate",
                "tags": list(release.listing.tags),
                "byte_length": len(release.content),
            },
        )
        return CompletedPersonalProductRelease(claim, release, completed.revision)

    def latest(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        now: datetime,
    ) -> PersonalProductReleaseArtifact | None:
        candidate = self._latest_artifact_events(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            now=now,
        )
        if candidate is None:
            return None
        intent_id, events = candidate
        return PersonalProductReleaseArtifact(
            intent_id,
            self._release_from_data(
                PostgresConversationActions._event_data(self._release_event(events))
            ),
        )

    def exact(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        intent_id: str,
        release_sha256: str,
        now: datetime,
    ) -> PersonalProductReleaseArtifact:
        candidate = self._latest_artifact_events(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            now=now,
            intent_id=intent_id,
            limit=128,
        )
        if candidate is None:
            raise ConversationConflict("paid product release does not exist")
        _, events = candidate
        release = self._release_from_data(
            PostgresConversationActions._event_data(self._release_event(events))
        )
        if release.content_sha256 != release_sha256:
            raise ConversationConflict("paid release content hash differs")
        return PersonalProductReleaseArtifact(intent_id, release)

    @staticmethod
    def _completed_prototype(
        tenant_id: str,
        installation_id: str,
        events: tuple[TimelineEvent, ...],
        revision: int,
    ) -> CompletedPersonalPrototype:
        exploration = PostgresPersonalPrototypeStore._exploration_from_events(
            tenant_id, installation_id, events, revision
        )
        prototype_event = _PostgresPersonalClaimStore._latest_event(
            events, "PERSONAL_PROTOTYPE_COMPLETED"
        )
        if prototype_event is None:
            raise ConversationConflict("product release lacks completed prototype")
        return CompletedPersonalPrototype(
            PersonalPrototypeClaim(exploration, revision),
            PostgresPersonalPrototypeStore._prototype_from_data(
                PostgresConversationActions._event_data(prototype_event)
            ),
            revision,
        )

    @staticmethod
    def _release_event(events: tuple[TimelineEvent, ...]) -> TimelineEvent:
        event = _PostgresPersonalClaimStore._latest_event(
            events, "PERSONAL_PRODUCT_RELEASE_COMPLETED"
        )
        if event is None:
            raise ConversationConflict("completed product release has no bundle event")
        return event

    @staticmethod
    def _release_from_data(data: dict[str, JsonValue]) -> PersonalProductRelease:
        headline = data.get("headline")
        description = data.get("description")
        price = data.get("price_eur_cents")
        tags = data.get("tags")
        provider = data.get("provider")
        filename = data.get("filename")
        encoded = data.get("content_base64")
        if (
            not isinstance(headline, str)
            or not isinstance(description, str)
            or not isinstance(price, int)
            or not isinstance(tags, list)
            or any(not isinstance(tag, str) for tag in tags)
            or not isinstance(provider, str)
            or not isinstance(filename, str)
            or not isinstance(encoded, str)
        ):
            raise ConversationConflict("personal product release event is malformed")
        try:
            content = base64.b64decode(encoded, validate=True)
            release = PersonalProductRelease(
                PersonalProductListing(
                    headline, description, price, tuple(tags), provider
                ),
                filename,
                content,
            )
        except (ValueError, TypeError) as error:
            raise ConversationConflict(
                "personal product release bytes are invalid"
            ) from error
        if (
            release.content_sha256 != data.get("content_sha256")
            or len(release.content) != data.get("byte_length")
            or data.get("publication_status") != "unpublished_release_candidate"
        ):
            raise ConversationConflict("personal product release binding differs")
        return release


__all__ = [
    "PostgresConversationActions",
    "PostgresPersonalInterestStore",
    "PostgresPersonalProductReleaseStore",
    "PostgresPersonalPrototypeStore",
]
