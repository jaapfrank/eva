"""Execute admitted private digital work with Eva's existing conversation loop."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import hashlib

from eva.adapters.connectors.mcp_gateway import McpGatewayUnavailable
from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import DelegatedWorkCall, DelegatedWorkReleaseSelector
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.adapters.subagents.tool_free_child import ToolFreeChildOutcome
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.cancellation import CancellationSignal, cancellation_scope
from eva.application.conversation import ConversationActionReceipt, ConversationConflict, LivingConversation
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import DelegatedWorkUsage
from eva.contracts.timestamps import parse_utc_timestamp


class _TaskModel:
    def __init__(self, model, task):
        self.model, self.task = model, task
        self.journals_provider_requests = getattr(model, "journals_provider_requests", False)

    def generate(self, *args, self_context=(), **kwargs):
        return self.model.generate(*args, self_context=(*self_context,
            "Je voert nu deze aangenomen achtergrondopdracht uit: " + self.task,
            "Maak deze opdracht af met de aangeboden tools, controleer de uitkomst en geef "
            "het resultaat terug. Het oorspronkelijke bericht is de context, geen nieuwe "
            "opdracht om opnieuw werk te delegeren. Beweer alleen wat toolresultaten bewijzen. "
            "Als iets ontbreekt, vermeld concreet wat niet is gelukt."), **kwargs)


class BackgroundConnectorActions:
    """Route existing connector tools, without exposing recursive delegation."""
    def __init__(self, tools, allowed_names, authorize, maximum_calls):
        self._tools = tuple(tools)
        self._allowed = frozenset(allowed_names)
        self._authorize = authorize
        self._maximum_calls = maximum_calls
        self.calls = 0
        self.dispatches = 0
        self.receipts = []
        names = [name for tool in self._tools for name in tool.action_names]
        if len(names) != len(set(names)) or not self._allowed.issubset(names):
            raise ValueError("background connector catalog differs from admitted capabilities")
        self._by_name = {name: tool for tool in self._tools for name in tool.action_names}

    @property
    def action_names(self):
        return tuple(name for name in self._by_name if name in self._allowed)

    def available_action_names(self, turn):
        self._authorize(turn)
        return tuple(name for name in self.action_names
                     if self._by_name[name].available_for(turn))

    continuation_action_names = available_action_names

    def active_context(self, turn):
        self._authorize(turn)
        return tuple(item for tool in self._tools if tool.available_for(turn)
                     for item in tool.active_context(turn))

    @staticmethod
    def _receipt(receipt):
        # The existing loop continues only after a verified host step. Keep the
        # procedure status intact inside the result; discovery is not task success.
        if receipt.call.name in {"list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure"}:
            return ConversationActionReceipt(receipt.call, {"status": "verified",
                "operation_result": receipt.output})
        return receipt

    def pending(self, turn):
        return self.pending_step(turn, step_index=0)

    def pending_step(self, turn, *, step_index):
        self._authorize(turn)
        found = None
        for tool in self._tools:
            if not self._allowed.intersection(tool.action_names):
                continue
            receipt = tool.pending(turn, step_index=step_index)
            if receipt is not None:
                if receipt.call.name not in self._allowed or found is not None:
                    raise ConversationConflict("background action admission differs")
                found = self._receipt(receipt)
        if found is not None:
            self.calls += 1
            self.receipts.append(found)
        return found

    def execute(self, turn, call):
        return self.execute_step(turn, call, step_index=0)

    def execute_step(self, turn, call, *, step_index):
        self._authorize(turn)
        if call.name not in self.available_action_names(turn):
            raise ConversationConflict("background action was not admitted")
        if self.calls >= self._maximum_calls:
            raise ConversationConflict("background connector call budget exhausted")
        self.calls += 1
        receipt = self._receipt(self._by_name[call.name].execute(turn, call, step_index=step_index))
        self.receipts.append(receipt)
        self._authorize(turn)
        return receipt


def _has_completed_tool_evidence(receipts):
    """Discovery is useful context, but cannot alone prove completed work."""
    completed = False
    for receipt in receipts:
        output = receipt.output
        if receipt.call.name in {"list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure"}:
            if output.get("status") != "verified":
                return False
            output = output.get("operation_result", {})
            if output.get("contract") != "ProcedureReferenceAction/v1":
                return False
            status = output.get("status")
            if receipt.call.name == "list_reusable_procedures":
                if status != "candidates":
                    return False
            elif receipt.call.name == "designate_reusable_procedure":
                if status != "designated" or not output.get("reference"):
                    return False
                completed = True
            elif status == "resolved":
                if not output.get("references"):
                    return False
                completed = True
            elif status != "unavailable":
                return False
        elif output.get("status") == "verified":
            completed = True
        else:
            return False
    return completed


class DelegatedDigitalRunner:
    def __init__(self, store, profile, *, source_loader, memory, model, identity,
                 connector_tools, journal_factory=None, clock=None):
        self.store, self.profile = store, profile
        self.source_loader, self.memory, self.model, self.identity = source_loader, memory, model, identity
        self.connector_tools = tuple(connector_tools)
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.journal_factory = journal_factory or self._journal

    def _journal(self, turn, spec):
        journal = PostgresConversationJournal(self.store._engine, external_timeline_scope(
            turn.tenant_id, turn.installation_id, "background source scope is required"),
            maximum_model_requests=spec.grant.maximum_model_runs)
        return BackgroundModelJournal(journal, turn, work_id=spec.work_id)

    async def run(self, spec, *, call_id, cancellation_event=None):
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec, DelegatedDigitalWorkResult
        signal = CancellationSignal()
        usage = DelegatedWorkUsage.unmeasured()
        journal = source = actions = None
        deadline = parse_utc_timestamp(spec.deadline)
        call = DelegatedWorkCall(spec.work_id, "digital:" + spec.work_id, call_id, spec)

        def authorize(turn):
            signal.check()
            if turn != source:
                raise ConversationConflict("background source differs")
            if self.clock() >= deadline:
                signal.cancel()
                signal.check()
            self.store.authorize_tool_dispatch(call)

        def dispatch_guard(turn):
            authorize(turn)
            if actions is None or actions.dispatches >= spec.grant.maximum_tool_calls:
                raise ConversationConflict("background dispatch budget exhausted")
            actions.dispatches += 1

        def checkpoint(turn, actual):
            authorize(turn)
            for value, maximum in ((actual.model_attempts, spec.grant.maximum_model_runs),
                    (actual.input_tokens, spec.grant.maximum_input_tokens),
                    (actual.output_tokens, spec.grant.maximum_output_tokens)):
                if value is not None and value > maximum:
                    raise ConversationConflict("background model budget exceeded")

        async def watch():
            while True:
                if cancellation_event is not None and cancellation_event.is_set():
                    signal.cancel()
                    return
                if self.clock() >= deadline:
                    signal.cancel()
                    return
                await asyncio.sleep(0.05)

        watcher = asyncio.create_task(watch())
        try:
            if (not isinstance(spec, DelegatedDigitalWorkSpec)
                    or DelegatedWorkReleaseSelector.from_spec(spec) != self.profile.release
                    or spec.grant != self.profile.grant()
                    or spec.model_profile_id != self.profile.model_profile_id):
                raise ConversationConflict("background release differs")
            if spec.grant.maximum_cost_usd_micros is not None:
                raise ConversationConflict("background subscription price is not measurable")
            deadline = min(deadline, self.clock() + timedelta(seconds=spec.grant.timeout_seconds))
            source = self.source_loader(spec.source.model_dump(mode="json"))
            if canonical_sha256(PostgresConversationJournal._ingress_data(source)) != spec.source_ingress_sha256:
                raise ConversationConflict("background original ingress differs")
            if cancellation_event is not None and cancellation_event.is_set():
                signal.cancel()
            authorize(source)
            journal = self.journal_factory(source, spec)
            actions = BackgroundConnectorActions(self.connector_tools, spec.grant.capability_ids,
                                                  authorize, spec.grant.maximum_tool_calls)
            conversation = LivingConversation(self.memory, _TaskModel(self.model, spec.task),
                                               self.identity, actions)
            def execute():
                with cancellation_scope(signal), background_execution(BackgroundExecutionScope(source, spec.execution_id, dispatch_guard)):
                    return conversation.respond(source, before_step=checkpoint, model_journal=journal)
            worker = asyncio.create_task(asyncio.to_thread(execute))
            try:
                reply = await asyncio.shield(worker)
            except asyncio.CancelledError:
                signal.cancel()
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    worker.exception()
                raise
            authorize(source)
            actual = journal.model_usage(source)
            checkpoint(source, actual)
            usage = self._usage(actual, actions.dispatches)
            if not _has_completed_tool_evidence(actions.receipts):
                raise ConversationConflict("background task lacks verified connector results")
            if actual.measurement != "measured":
                raise ConversationConflict("background model token usage is incomplete")
            result = DelegatedDigitalWorkResult(contract="DelegatedDigitalWorkResult/v4", schema_version=4,
                work_id=spec.work_id, call_id=call_id, output=reply.text,
                output_sha256=hashlib.sha256(reply.text.encode()).hexdigest(), spec_sha256=spec.sha256,
                usage=usage.to_payload(), completed_at=self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return ToolFreeChildOutcome("succeeded", result, usage, False, None)
        except Exception as error:
            if journal is not None and source is not None:
                with suppress(Exception):
                    usage = self._usage(journal.model_usage(source), 0 if actions is None else actions.dispatches)
            return ToolFreeChildOutcome("cancelled" if cancellation_event is not None and cancellation_event.is_set()
                else "timed_out" if self.clock() >= deadline
                else "provider_unavailable" if type(error) is McpGatewayUnavailable
                else "internal_error", None, usage, False,
                hashlib.sha256((type(error).__name__ + ":" + str(error)).encode()).hexdigest())
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    @staticmethod
    def _usage(actual, tool_calls):
        if actual.measurement == "unmeasured":
            return DelegatedWorkUsage.unmeasured()
        return DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
            measurement="partial", completed_model_responses=actual.model_attempts,
            tool_calls=tool_calls, input_tokens=actual.input_tokens, output_tokens=actual.output_tokens,
            total_tokens=actual.input_tokens + actual.output_tokens, cost_usd_micros=None)
