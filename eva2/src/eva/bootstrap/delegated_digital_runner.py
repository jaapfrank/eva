"""Execute admitted private digital work with Eva's existing conversation loop."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import hashlib

from eva.adapters.connectors.mcp_gateway import McpGatewayUnavailable
from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
from eva.adapters.postgres.background_work_progress import BackgroundWorkProgress, load_work_progress
from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions, DRAFT, EVALUATE, PREPARE
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


class _UnfinishedWorkPlan(ConversationConflict):
    """Preserved progress exists, but this bounded attempt cannot finish it."""


class _TaskModel:
    def __init__(self, model, task, browser_context=None, corrections=()):
        self.model, self.task = model, task
        self.owner_corrections = tuple(
            "Bewaarde bijsturing van de eigenaar, in volgorde; latere correcties vervangen eerdere alleen waar ze botsen: "
            + item["instruction"] for item in corrections)
        self.continuation_feedback = ()
        self.browser_context = browser_context
        self.journals_provider_requests = getattr(model, "journals_provider_requests", False)

    def generate(self, *args, self_context=(), **kwargs):
        availability = () if self.browser_context is None else self.browser_context()
        return self.model.generate(*args, self_context=(*self_context, *availability, *self.owner_corrections, *self.continuation_feedback,
            "Je voert nu deze aangenomen achtergrondopdracht uit: " + self.task,
            "Maak deze opdracht af met de aangeboden tools, controleer de uitkomst en geef "
            "het resultaat terug. Het oorspronkelijke bericht is de context, geen nieuwe "
            "opdracht om opnieuw werk te delegeren. Beweer alleen wat toolresultaten bewijzen. "
            "Als iets ontbreekt, vermeld concreet wat niet is gelukt.",
            "Bij grotere opdrachten: bewaar een beknopt uitvoerplan met save_work_progress, "
            "werk de stappen bij na betekenisvolle voortgang en noteer de eerstvolgende actie "
            "voor hervatten. Besteed geen plan-call aan een eenvoudige opdracht. Een opgeslagen "
            "plan is nog geen resultaat; voer het werk daadwerkelijk uit. Bij een blokkade "
            "bewaar je de stand en benoem je wat nodig is. Bestaande budgetten blijven gelden. ",
            "Bij een gevraagde verbetering van je eigen code: lees de echte component met "
            "read_own_component_source, onderzoek de oorzaak en relevante bestaande oplossingen, "
            "en maak alleen bij een concrete tekortkoming een draft_own_component_improvement. "
            "Bewaar de kandidaat, patch en meetplan met save_workspace_document als afzonderlijk "
            "voorstel. Gebruik evaluate_own_component_improvement indien aangeboden voor die component, "
            "met de draft_step uit het voorstel. Bewaar ook het echte meetrapport en beoordeel "
            "of dat de vooraf beschreven winst daadwerkelijk bewijst. Een gelijke uitkomst is geen winst. "
            "Gebruik prepare_own_component_release bij een geslaagde vergelijking om het exacte "
            "voorstel met zijn rapport als installatiekandidaat te bewaren. Gebruik evaluation_step "
            "uit het meetrapport. Dit pakket wacht nog op gemeten voordeel en beoordeling. "
            "Wijzig of installeer geen draaiende component via andere tools. Beschrijf "
            "verwachte winst als hypothese zolang er geen meting is; tekstcontroles bewijzen "
            "geen algemene functionele kwaliteit. Geef de echte vindplaats en beperkingen terug.",
            "Bij een opdracht om een vaardigheid te leren: raadpleeg de relevante documentatie "
            "met beschikbare tools en pas de aanpak toe binnen de opdracht en het bestaande "
            "mandaat. Bewaar een bruikbare werkwijze met save_workspace_document en registreer "
            "het zojuist opgeslagen document met designate_reusable_procedure als die tools "
            "beschikbaar zijn. Vermeld wanneer de aanpak nuttig is, vereisten, concrete stappen, "
            "controle van het resultaat en bekende beperkingen. Scheid daadwerkelijk uitgevoerde "
            "stappen van onbeproefde suggesties; bewaar geen credentials of overbodige persoonsgegevens. "
            "Kies een passende eindige bewaartermijn vanuit het bestaande mandaat. Een gewone "
            "notitie is niet automatisch een werkwijze. Zoek bij een nieuwe opdracht eerst naar "
            "een relevante bestaande aanpak met list_reusable_procedures(query=gerichte trefwoorden), "
            "als die tool beschikbaar is en een aangeleerde aanpak nuttig kan zijn. Kies zo nodig "
            "andere trefwoorden dan de vraag; beoordeel zelf of de gevonden aanpak werkelijk past. "
            "Geen resultaat bewijst niet dat er geen werkwijze bestaat: bladeren blijft beschikbaar. "
            "Bij hergebruik haal je de passende "
            "verwijzing eerst op met resolve_reusable_procedure en voer je de nieuwe opdracht "
            "uit met de aangeboden tools. Voor een zelfgemaakte Python-werkwijze kun je "
            "run_reusable_procedure gebruiken: geverifieerd .py-document registreren, JSON via stdin, "
            "resultaat via stdout. Geen netwerk of hostbestanden; controleer de uitvoer en bewaar "
            "het resultaat via de documenttool. Een exitcode is geen inhoudelijk bewijs. "
            "Meld alleen een bewaarde of toegepaste werkwijze "
            "wanneer de bijbehorende toolresultaten dat bevestigen."), **kwargs)


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
        if receipt.call.name in {"list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure", "run_reusable_procedure"}:
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
            # Recovered receipts do not consume this attempt's new-call budget.
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
    if any(receipt.call.name in {DRAFT, EVALUATE, PREPARE} for receipt in receipts) and not any(
            receipt.call.name == "save_workspace_document" and receipt.output.get("status") == "verified"
            for receipt in receipts):
        return False
    completed = False
    for receipt in receipts:
        output = receipt.output
        if receipt.call.name == "save_work_progress":
            # A planning checkpoint proves persistence, never completed work.
            continue
        if receipt.call.name in {"list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure", "run_reusable_procedure"}:
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
            elif receipt.call.name == "run_reusable_procedure":
                if status == "execution_completed":
                    completed = True
                elif status != "execution_failed":
                    return False
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
                 connector_tools, journal_factory=None, clock=None, browser_context=None):
        self.store, self.profile = store, profile
        self.source_loader, self.memory, self.model, self.identity = source_loader, memory, model, identity
        self.connector_tools = tuple(connector_tools)
        self.browser_context = browser_context
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
                    or not self.profile.accepts_grant(spec.grant)
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
            progress = BackgroundWorkProgress(self.store._engine, spec, source, authorize,
                                              dispatch_guard, self.clock)
            improvements = ComponentImprovementActions(self.store._engine, spec, source, authorize,
                                                        dispatch_guard, self.clock)
            actions = BackgroundConnectorActions((*self.connector_tools, progress, improvements), spec.grant.capability_ids,
                                                  authorize, spec.grant.maximum_tool_calls)
            from eva.adapters.postgres.work_corrections import work_corrections
            task_model = _TaskModel(self.model, spec.task, self.browser_context,
                                    corrections=work_corrections(self.store._engine, spec))
            conversation = LivingConversation(self.memory, task_model, self.identity, actions)

            def execute():
                # Continue the same admitted attempt, accounting identity and
                # budgets. Never admit another task or retry an uncertain effect.
                prompted_states = set()
                with cancellation_scope(signal), background_execution(BackgroundExecutionScope(source, spec.execution_id, dispatch_guard)):
                    while True:
                        reply = conversation.respond(source, before_step=checkpoint, model_journal=journal)
                        authorize(source)
                        progress_state = load_work_progress(self.store._engine, spec)
                        if not progress_state:
                            return reply
                        remaining = [step for step in progress_state["steps"]
                                     if step["state"] not in {"done", "skipped"}]
                        if not remaining:
                            return reply
                        if any(step["state"] == "blocked" for step in remaining):
                            raise _UnfinishedWorkPlan("task plan contains a recorded blocker")
                        actual = journal.model_usage(source)
                        checkpoint(source, actual)
                        state_key = (progress_state["revision"], actions.calls)
                        if state_key in prompted_states:
                            raise _UnfinishedWorkPlan("task made no progress after continuation")
                        if (actual.measurement != "measured"
                                or actual.model_attempts >= spec.grant.maximum_model_runs
                                or actual.input_tokens >= spec.grant.maximum_input_tokens
                                or actual.output_tokens >= spec.grant.maximum_output_tokens
                                or actions.calls >= spec.grant.maximum_tool_calls
                                or actions.dispatches >= spec.grant.maximum_tool_calls):
                            raise _UnfinishedWorkPlan("remaining attempt budget cannot continue the plan")
                        prompted_states.add(state_key)
                        task_model.continuation_feedback = (
                            "Je gaf een slotantwoord, maar je opgeslagen uitvoerplan heeft nog open "
                            "stappen. De opdracht is daarmee niet afgerond. Ga door met de eerstvolgende "
                            "uitvoerbare stap binnen dezelfde opdracht en werk daarna de voortgang bij. "
                            "Gebruik eerdere toolresultaten en herhaal geen al uitgevoerde actie. "
                            "Markeer stappen alleen als gedaan als werkelijk resultaat dat onderbouwt. "
                            "Bij een echte blokkade: bewaar die met state blocked, de reden en een "
                            "bruikbare hervatnotitie; verzin geen succes en vergroot het mandaat niet.",)
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
            saved_progress = load_work_progress(self.store._engine, spec)
            if saved_progress and any(step["state"] not in {"done", "skipped"}
                                      for step in saved_progress["steps"]):
                raise _UnfinishedWorkPlan("background task plan is unfinished")
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
                else "incomplete_work" if isinstance(error, _UnfinishedWorkPlan)
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
