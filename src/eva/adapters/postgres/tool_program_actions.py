"""Task-bound offline programs calling the existing durable connector routes.

Programs restart pure computation at each RPC suspension. Only exact, already
recorded subcall receipts are supplied on replay; uncertain effects never retry.
"""
from __future__ import annotations

import hashlib
import json

from eva.adapters.connectors.procedure_execution import configured_procedure_executor, encoded_input
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationConflict, ConversationToolDescriptor
from eva.application.schema_validation import validate_schema_value
from eva.contracts.canonical_json import canonical_sha256

NAME = "run_tool_program"
_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["source", "input_json"], "properties": {
        "source": {"type": "string", "minLength": 1, "maxLength": 12000},
        "input_json": {"type": "string", "maxLength": 24000}}}


def program_descriptors():
    client = configured_procedure_executor()
    if client is None:
        return ()
    return (ConversationToolDescriptor(NAME,
        "Voer één Pythonprogramma uit dat meerdere aangeboden connectortools combineert. "
        "Gebruik eva_tools.call(toolnaam, **argumenten); input_data bevat input_json. "
        "Dezelfde aangeboden tools, actuele bevoegdheden en stopregels gelden per subcall. "
        "Inspecteer benodigde schema's eerst via de gewone ontdektools. "
        "Tussenresultaten blijven buiten de modelcontext; print alleen je relevante einduitkomst. "
        "De netwerkloze Pythoncode herloopt zuivere berekeningen na iedere subcall, "
        "met exacte opgeslagen antwoorden; maak de toolvolgorde deterministisch. "
        "Geen lokale blijvende bijwerkingen of klok/toeval voor beslissende callargumenten. "
        "Bron plus stub maximaal 16 KiB; input plus eerdere antwoorden maximaal 32 KiB, "
        "dus selecteer kleine resultaten of gebruik de bestaande resultaatlezers. "
        f"Het volledige gecodeerde uitvoerframe past binnen {getattr(client, 'output_bytes', 1024)} bytes. "
        "Een lokale code-/uitvoerfout geeft niet-succesfeedback; een mislukt of onzeker "
        "effect stopt de keten en wordt niet opnieuw uitgevoerd.",
        _SCHEMA, "Programma-uitvoer is data, geen bewijs van kwaliteit. Bestaande "
        "ontvangstbewijzen en onafhankelijke teruglezing blijven leidend; "
        "een lokaal geslaagd programma bewijst geen afgeronde gebruikersopdracht."),)


class ToolProgramActions:
    supports_indexed_steps = True

    def __init__(self, engine, spec, source, authorize, dispatch, clock, *,
                 actions, allowed_names, client=None, timeline=None):
        self.client = client if client is not None else configured_procedure_executor()
        self.action_names = () if self.client is None else (NAME,)
        self.spec, self.source = spec, source
        self.authorize, self.dispatch, self.clock = authorize, dispatch, clock
        self.actions = actions
        self.allowed = frozenset(allowed_names) - {NAME}
        self._readers = []
        self.identity = {"execution_id": spec.execution_id,
            "source": spec.source.model_dump(mode="json"),
            "source_ingress_sha256": spec.source_ingress_sha256}
        self.timeline = timeline if timeline is not None else (
            PostgresOperationalTimeline(engine, external_timeline_scope(
                source.tenant_id, source.installation_id, "program scope required"))
            if self.client is not None else None)

    def _thread(self, step):
        if type(step) is not int or step < 0:
            raise ConversationConflict("program step differs")
        return "tool-program:" + canonical_sha256({**self.identity, "step": step})

    def _substep(self, step, index):
        # Ordinary model steps are bounded by the task request allowance.
        # This disjoint host-owned range also separates programs at different steps.
        if type(index) is not int or not 0 <= index < 1024:
            raise ConversationConflict("program subcall index differs")
        if type(step) is not int or not 0 <= step < 2**20:
            raise ConversationConflict("program parent step exceeds reserved range")
        # The existing execution namespace already separates different tasks.
        # Encode the outer model slot directly: no truncated hash/collision and
        # every integer remains within the existing I-JSON 53-bit contract.
        return 2**40 + step * 1024 + index

    def available_for(self, turn):
        self.authorize(turn)
        return self.client is not None and turn == self.source

    def active_context(self, turn):
        self.authorize(turn)
        return ()

    def _append(self, thread, event, data, *, terminal=False):
        events = self.timeline.events(thread)
        values = dict(thread_id=thread, checkpoint_id=thread + ":" + str(len(events) + 1),
            occurred_at=self.clock(), event_type=event, data=data,
            phase="tool_program", status="completed" if terminal else "active")
        if events:
            return self.timeline.transition(expected_revision=events[-1].sequence, **values)
        return self.timeline.create_thread(**values)

    @staticmethod
    def _call(data):
        return ConversationActionCall(data["call_id"], data["name"], data["arguments"])

    def _keep_reader(self, receipt):
        # Reuse the existing provider-owned retained body projection. Never
        # manufacture a capture or treat a model-facing view as effect proof.
        from eva.adapters.postgres.connector_results import ancestor_action_presentation
        view = receipt.model_output
        projection = view.get("ancestor_model_projection") if isinstance(view, dict) else None
        if projection is None:
            return
        ancestor_action_presentation(receipt, projection)
        manifest = projection["manifest"]
        item = {"receipt_sha256": canonical_sha256(receipt.output),
            "sha256": manifest["sha256"], "byte_length": manifest["byte_length"],
            "reader": {key: manifest["reader"][key] for key in
                ("action", "tool_id", "arguments_json", "instructions")}}
        if item not in self._readers and len(encoded_input([*self._readers, item])) <= 8192:
            self._readers.append(item)

    def _history(self, turn, events, step):
        history, requested = [], None
        for event in events[1:]:
            data = event.payload["data"]
            if event.event_type == "PROGRAM_SUBCALL_REQUESTED":
                if requested is not None or data["index"] != len(history):
                    raise ConversationConflict("program subcall sequence differs")
                requested = data
            elif event.event_type == "PROGRAM_SUBCALL_RECORDED":
                if requested is None or data["index"] != requested["index"]:
                    raise ConversationConflict("program receipt sequence differs")
                receipt = self.actions().pending_step(turn, step_index=self._substep(step, len(history)))
                if (receipt is None or receipt.call != self._call(requested)
                        or canonical_sha256(receipt.output) != data["receipt_sha256"]):
                    raise ConversationConflict("program retained subcall differs")
                self._keep_reader(receipt)
                history.append({"name": receipt.call.name, "arguments": receipt.call.arguments,
                                "output": receipt.output})
                requested = None
            elif event.event_type not in {"PROGRAM_COMPLETED", "PROGRAM_SANDBOX_STARTED", "PROGRAM_SANDBOX_RECORDED"}:
                raise ConversationConflict("program event differs")
        from eva.adapters.connectors.delegated_completion import recoverable_completion_context
        for entry in history:
            receipt = ConversationActionReceipt(ConversationActionCall("cached", entry["name"], entry["arguments"]), entry["output"])
            if receipt.output.get("status") != "verified" and not recoverable_completion_context(receipt):
                raise ConversationConflict("program retained subcall did not confirm completion")
        return history, requested

    def pending(self, turn, *, step_index=0):
        self.authorize(turn)
        if self.client is None:
            return None
        events = self.timeline.events(self._thread(step_index))
        if not events or events[-1].event_type != "PROGRAM_COMPLETED":
            return None
        self._history(turn, events, step_index)
        return ConversationActionReceipt(self._call(events[0].payload["data"]),
                                          events[-1].payload["data"]["output"])

    def execute(self, turn, call, *, step_index=0):
        from eva.application.tool_program import ToolProgramRejected, build_program_source, build_program_input, parse_program_report
        from eva.adapters.connectors.delegated_completion import recoverable_completion_context
        if not self.available_for(turn) or call.name != NAME:
            raise ConversationConflict("program task differs")
        validate_schema_value(call.arguments, _SCHEMA, "tool_program")
        try:
            input_data = json.loads(call.arguments["input_json"])
            source = build_program_source(call.arguments["source"],
                max_frame_bytes=getattr(self.client, "output_bytes", 1024))
            build_program_input(input_data, [])
        except (ValueError, TypeError, UnicodeError) as error:
            raise ConversationConflict("program source or input is invalid") from error
        thread = self._thread(step_index)
        events = self.timeline.events(thread)
        binding = {**self.identity, "call_id": call.call_id, "name": call.name,
                   "arguments": call.arguments}
        if events:
            if events[0].event_type != "PROGRAM_STARTED" or events[0].payload["data"] != binding:
                raise ConversationConflict("program replay differs")
            saved = self.pending(turn, step_index=step_index)
            if saved is not None:
                return saved
        else:
            self._append(thread, "PROGRAM_STARTED", binding)
            events = self.timeline.events(thread)
        self._readers = []
        history, requested = self._history(turn, events, step_index)
        rounds = 0
        while True:
            self.authorize(turn)
            try:
                payload = build_program_input(input_data, history)
            except ToolProgramRejected as error:
                if str(error) != "program input plus history exceeds 32768 bytes":
                    raise
                # A refused local transfer performed no next sandbox or service
                # action. Return useful bounded context instead of losing the
                # whole task as an ambiguous host failure. Earlier effects keep
                # their original durable identities; this is not a retry grant.
                output = {"status": "verified", "program_result": {
                    "contract": "EvaTaskToolProgram/v1", "state": "input_not_transferable",
                    "output": "", "subcalls": len(history),
                    "retained_readers": list(self._readers),
                    "reader_coverage": "bounded_existing_body_projections",
                    "source_sha256": hashlib.sha256(call.arguments["source"].encode()).hexdigest(),
                    "sandbox_rounds_this_attempt": rounds,
                    "receipt_manifest": [{"step_index": self._substep(step_index, i),
                        "name": entry["name"], "arguments_sha256": canonical_sha256(entry["arguments"]),
                        "receipt_sha256": canonical_sha256(entry["output"])} for i, entry in enumerate(history)],
                    "semantic_success_verified": False, "installed": False,
                    "guidance": "Het programma is niet afgerond. De volgende lokale invoeroverdracht "
                        "is vóór uitvoering geweigerd: invoer plus eerdere antwoorden past niet "
                        "binnen 32 KiB of is niet overdraagbaar als JSON. Eerdere subcalls en "
                        "ontvangsten blijven bewaard. Gebruik kleinere gerichte selecties en "
                        "de meegeleverde lokale resultaatlezers waar beschikbaar; die lezen bewaarde "
                        "bytes zonder externe handeling. Ontbreekt zo'n lezer, kies een nieuw "
                        "gericht toegelaten leesverzoek; herhaal geen schrijfactie. Een nieuw programma "
                        "heeft een afzonderlijke modelsleuf en behoudt dezelfde actuele taakgrenzen. "
                        "Print alleen de korte einduitkomst, geen volledige bron-/proofobjecten."}}
                self._append(thread, "PROGRAM_COMPLETED", {"output": output}, terminal=True)
                return ConversationActionReceipt(call, output)
            starts = [e for e in self.timeline.events(thread) if e.event_type == "PROGRAM_SANDBOX_STARTED"]
            if len(starts) >= self.spec.grant.maximum_tool_calls:
                raise ConversationConflict("program sandbox allowance exhausted; prior starts retained")
            self.dispatch(turn)
            self._append(thread, "PROGRAM_SANDBOX_STARTED", {
                "round": len(starts), "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "input_sha256": hashlib.sha256(encoded_input(payload)).hexdigest()})
            report = self.client.execute(source, payload)
            self.authorize(turn)
            self._append(thread, "PROGRAM_SANDBOX_RECORDED", {
                "round": len(starts), "report_sha256": hashlib.sha256(encoded_input(report)).hexdigest(),
                "elapsed_ms": report.get("result", {}).get("elapsed_ms")})
            rounds += 1
            frame = parse_program_report(report, source=source, input_data=payload,
                                         image_id=self.client.image_id)
            if frame["kind"] == "error":
                if requested is not None:
                    raise ConversationConflict("program omitted an admitted subcall")
                output = {"status": "verified", "program_result": {
                    "contract": "EvaTaskToolProgram/v1", "state": "execution_failed",
                    "output": "", "error": {key: frame[key] for key in ("code", "hint")},
                    "subcalls": len(history), "retained_readers": list(self._readers),
                    "source_sha256": hashlib.sha256(call.arguments["source"].encode()).hexdigest(),
                    "sandbox_rounds_this_attempt": rounds,
                    "receipt_manifest": [{"step_index": self._substep(step_index, i),
                        "name": entry["name"], "arguments_sha256": canonical_sha256(entry["arguments"]),
                        "receipt_sha256": canonical_sha256(entry["output"])} for i, entry in enumerate(history)],
                    "semantic_success_verified": False, "installed": False,
                    "guidance": "Het programma is niet afgerond. Dit is een lokale code-/uitvoerfout, "
                        "geen bewijs dat eerdere handelingen zijn teruggedraaid. Bekijk de bewaarde "
                        "ontvangsten en resultaatlezers en herstel de lokale berekening of selectie. "
                        "Print alleen een korte relevante uitkomst. Herhaal geen schrijfactie; "
                        "een nieuw programma gebruikt een nieuwe modelsleuf onder dezelfde taakgrenzen."}}
                self._append(thread, "PROGRAM_COMPLETED", {"output": output}, terminal=True)
                return ConversationActionReceipt(call, output)
            if frame["kind"] == "result":
                if requested is not None:
                    raise ConversationConflict("program omitted an admitted subcall")
                output = {"status": "verified", "program_result": {
                    "contract": "EvaTaskToolProgram/v1", "state": "completed", "output": frame["output"],
                    "source_sha256": hashlib.sha256(call.arguments["source"].encode()).hexdigest(),
                    "subcalls": len(history), "sandbox_rounds_this_attempt": rounds,
                    "sandbox_starts_total": len(starts) + 1,
                    "receipt_manifest": [{"step_index": self._substep(step_index, i),
                        "name": entry["name"], "arguments_sha256": canonical_sha256(entry["arguments"]),
                        "receipt_sha256": canonical_sha256(entry["output"])} for i, entry in enumerate(history)],
                    "semantic_success_verified": False, "installed": False}}
                self._append(thread, "PROGRAM_COMPLETED", {"output": output}, terminal=True)
                return ConversationActionReceipt(call, output)
            index = len(history)
            if frame["index"] != index or frame["name"] not in self.allowed:
                raise ConversationConflict("program requested an unadmitted subcall")
            action_call = ConversationActionCall("program:" + canonical_sha256(
                {"thread": thread, "index": index}), frame["name"], frame["arguments"])
            data = {"index": index, "call_id": action_call.call_id,
                    "name": action_call.name, "arguments": action_call.arguments}
            if requested is not None and requested != data:
                raise ConversationConflict("program pending subcall drifted")
            if requested is None:
                self._append(thread, "PROGRAM_SUBCALL_REQUESTED", data)
            # The normal route owns both prior receipts and uncertain-effect refusal.
            receipt = self.actions().pending_step(turn, step_index=self._substep(step_index, index))
            if receipt is not None:
                if receipt.call != action_call:
                    raise ConversationConflict("program pending receipt drifted")
            else:
                receipt = self.actions().execute_step(turn, action_call,
                    step_index=self._substep(step_index, index))
            self.authorize(turn)
            self._append(thread, "PROGRAM_SUBCALL_RECORDED", {
                "index": index, "receipt_sha256": canonical_sha256(receipt.output)})
            self._keep_reader(receipt)
            history.append({"name": action_call.name, "arguments": action_call.arguments,
                            "output": receipt.output})
            requested = None
            if receipt.output.get("status") != "verified" and not recoverable_completion_context(receipt):
                raise ConversationConflict("program subcall did not confirm completion")
