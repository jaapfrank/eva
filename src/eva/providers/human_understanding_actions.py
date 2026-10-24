"""Source-bound provisional learning through the ordinary memory outbox."""
from datetime import UTC, datetime
from dataclasses import replace
import sqlalchemy as sa

from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox, conversation_memory_document_from_outbox_data
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope, CheckpointConflict
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.background_execution import authorize_background_dispatch, background_execution_namespace
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationActionInputRejected, ConversationConflict, ConversationToolDescriptor
from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record, MAX_LEARNING_RECORDS
from eva.application.memory import RecallCurrentFacts
from eva.contracts.canonical_json import canonical_sha256

ACTION = "record_human_understanding"


class HumanUnderstandingActions:
    action_names = (ACTION,)
    supports_indexed_steps = True

    def __init__(self, engine, *, source_reader, submit, clock=None):
        self.engine, self.source_reader, self.submit = engine, source_reader, submit
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    @staticmethod
    def contact_reply_allowed(turn, receipt):
        """Acknowledge an existing storage receipt without a work-model handoff."""
        return (receipt.call.name == ACTION
            and receipt.output.get("contract") == "EvaHumanUnderstandingAction/v1"
            and receipt.output.get("source_ref") == turn.message_id
            and receipt.output.get("status") in {"queued", "storage_refused"})

    @staticmethod
    def conversation_tools():
        return (ConversationToolDescriptor(name=ACTION,
            description="Bewaar een voorlopige persoonsinterpretatie, verwachting of eigen omgangsbijstelling op basis van dit echte privébericht. "
                "Gebruik personality_delta uitsluitend voor een concrete bijstelling in Eva’s eigen manier van helpen of communiceren "
                "uit expliciete feedback, maximaal 400 tekens, met de toepassingscontext en onzekerheid. "
                "Dit is geen persoonsclassificatie, emotie, eigen belevenis of nieuwe bevoegdheid. "
                "Pas feedback alleen toe voor degene die haar gaf; geen gezinsbrede voorkeur afleiden. "
                "Een eenmalig verzoek is niet vanzelf een blijvende omgangsbijstelling. "
                "Herzie bij nieuwe tegeninformatie een eerdere leerrecord met haar exacte source_ref en evidence_ref. "
                "Gebruik uitsluitend echte gebruikerinformatie, geen eigen antwoord als uitkomst. Alle interpretaties blijven onbevestigd; "
                "confirmed/refuted benoemt de beoordeling van de eerdere verwachting of interpretatie, geen feitelijke zekerheid. "
                "Een open/onbesliste verwachting vereist een review_at. Een brongebonden bevestiging of weerlegging van "
                "een eerdere verwachting mag review_at=null hebben: die vastgestelde uitkomst vraagt niet vanzelf een nieuwe toekomstige review.",
            input_schema={"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["person_hypothesis", "expectation", "personality_delta"]},
                "interpretation": {"type": "string", "minLength": 1, "maxLength": 1600},
                "uncertainty_reason": {"type": "string", "minLength": 1, "maxLength": 600},
                "review_at": {"type": ["string", "null"],
                    "description": "Canonieke UTC voor open/unknown verwachtingen. Bij confirmed/refuted met exacte "
                        "paired predecessor refs mag dit null zijn; persoonsinterpretaties mogen ook null gebruiken."},
                "outcome": {"type": "string", "enum": ["open", "confirmed", "refuted", "unknown"]},
                "previous_source_ref": {"type": ["string", "null"]},
                "previous_evidence_ref": {"type": ["string", "null"]}},
                "required": ["kind", "interpretation", "uncertainty_reason", "review_at", "outcome",
                             "previous_source_ref", "previous_evidence_ref"], "additionalProperties": False},
            completion_guidance="queued betekent duurzaam aangeboden aan de bestaande geheugenverwerking, nog geen voltooide Hindsight-retentie. "
                "Verwijs later naar de exacte bron/evidence. Privégegevens en interpretaties geven geen handelingsbevoegdheid."),)

    def _query(self, turn, now):
        return RecallCurrentFacts(recall_id="human-understanding-validation", tenant_id=turn.tenant_id,
            installation_id=turn.installation_id, principal_id=turn.principal_id, audience=turn.audience,
            conversation_id=turn.conversation_id, project_id=turn.project_id,
            query_purpose="human_understanding", requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            scope={"kind": "private"}, subject_ids=(turn.principal_id,), query_text=turn.text)

    def _host(self, turn):
        if (turn.audience.kind != "private_person" or turn.audience.principal_id != turn.principal_id
                or turn.observation_binding is not None or turn.conversation_id.startswith("initiative-task:")):
            raise ConversationConflict("learning requires a real private user source")
        now = self.clock()
        reader, query = self.source_reader(), self._query(turn, now)
        reader.require_learning_query(query, now=now)
        scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "learning scope required")
        timeline = PostgresOperationalTimeline(self.engine, scope)
        events = timeline.events("conversation:" + turn.message_id)
        timeline.load("conversation:" + turn.message_id)
        if (not events or events[0].event_type != "INGRESS_COMMITTED"
                or events[0].payload.get("data") != PostgresConversationJournal._ingress_data(turn)):
            raise ConversationConflict("learning original ingress differs")
        PostgresConversationJournal(self.engine, scope).ensure_running(turn)
        authorize_background_dispatch(turn)
        return reader, query, now, timeline

    def _available_host(self, turn):
        try:
            return self._host(turn)
        except (RuntimeError, ValueError, LookupError):
            return None

    def available_for(self, turn):
        return self._available_host(turn) is not None

    @staticmethod
    def _slot(turn, step):
        if type(step) is not int or step < 0:
            raise ValueError("learning step differs")
        return "human-understanding-action:" + canonical_sha256({
            "source": PostgresConversationJournal._ingress_data(turn), "step_index": step,
            "execution_namespace": background_execution_namespace(turn)})

    def _validate(self, turn, call, reader, query, now):
        if call.name != ACTION:
            raise ConversationConflict("learning action differs")
        try:
            doc = build_human_understanding_document(turn, call.arguments)
            record = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
        except ValueError:
            raise ConversationActionInputRejected(("learning_record",),
                prompt="Gebruik geldige voorlopige leerrecordvelden en de echte bronverwijzingen.") from None
        if record["previous_source_ref"] is not None:
            context = reader.learning_source(query, record["previous_source_ref"], record["previous_evidence_ref"], now=now,
                                             follow_revisions=False)
            if (not isinstance(context, dict) or len(context.get("records", ())) >= MAX_LEARNING_RECORDS
                    or not context.get("records")
                    or context["records"][0]["record"]["kind"] != record["kind"]
                    or context["records"][0]["record"]["recorded_at"] >= record["recorded_at"]):
                raise ConversationActionInputRejected(("previous_source_ref", "previous_evidence_ref"),
                    prompt="De eerdere leerrecord is niet beschikbaar binnen dezelfde persoon, project en bewaartermijn. Bewaar geen ongefundeerde herziening.")
        return doc

    def execute(self, turn, call, *, step_index=0):
        reader, query, now, timeline = self._host(turn)
        doc = self._validate(turn, call, reader, query, now)
        slot = self._slot(turn, step_index)
        admission = {"call_id": call.call_id, "name": call.name,
            "arguments_sha256": canonical_sha256(call.arguments), "document_id": doc.document_id,
            "source_ref": doc.source_ref, "evidence_ref": doc.evidence_ref,
            "previous_source_ref": call.arguments["previous_source_ref"],
            "previous_evidence_ref": call.arguments["previous_evidence_ref"],
            "principal_id": turn.principal_id, "project_id": turn.project_id,
            "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(turn)),
            "step_index": step_index}
        events = timeline.events(slot)
        if not events:
            # Serialize only admission, not retention or a model call. Otherwise
            # two conversations can both revise A and silently bypass correction B.
            lane = "learning-admission:" + canonical_sha256([
                turn.tenant_id, turn.installation_id, turn.principal_id, turn.project_id])
            with self.engine.begin() as connection:
                connection.execute(sa.text(
                    "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lane})
                events = timeline.events(slot)
                if not events:
                    reader, query, now, _ = self._host(turn)
                    previous = call.arguments["previous_source_ref"]
                    if previous is not None:
                        evidence = call.arguments["previous_evidence_ref"]
                        current = reader.learning_source(query, previous, evidence,
                            now=now, follow_revisions=True)
                        head = None if not current or not current.get("records") else current["records"][0]
                        if head is None or (head["source_ref"], head["evidence_ref"]) != (previous, evidence):
                            raise ConversationActionInputRejected(
                                ("previous_source_ref", "previous_evidence_ref"),
                                prompt="Deze interpretatie is inmiddels herzien of wordt herzien. Lees de actuele "
                                    "persoonscontext en verbind tegeninformatie aan de nieuwste beschikbare versie; "
                                    "maak geen nieuwe losse kopie om de correctie te omzeilen.")
                    try:
                        timeline.create_thread(thread_id=slot, checkpoint_id=slot + ":admitted", occurred_at=now,
                            data=admission, event_type="HUMAN_UNDERSTANDING_ADMITTED", phase="learning_admitted")
                    except CheckpointConflict:
                        pass
                    events = timeline.events(slot)
        state = timeline.load(slot)
        if not events or events[0].payload.get("data") != admission:
            raise ConversationConflict("learning step replay differs")
        if state.phase == "learning_completed":
            output = events[-1].payload["data"]["output"]
            if canonical_sha256(output) != events[-1].payload["data"]["output_sha256"]:
                raise ConversationConflict("learning receipt hash differs")
            return ConversationActionReceipt(call, output)
        if state.phase != "learning_admitted":
            raise ConversationConflict("learning admission is uncertain")
        # Current authority and the retained predecessor are checked again at the write boundary.
        reader, query, now, _ = self._host(turn)
        self._validate(turn, call, reader, query, now)
        decision = self.submit(doc)
        admitted = None if decision is None else getattr(decision, "admitted", None)
        if admitted is not None and type(admitted) is not bool:
            raise ConversationConflict("learning storage decision differs")
        output = {"contract": "EvaHumanUnderstandingAction/v1",
            "status": "storage_refused" if admitted is False else "queued",
            "document_id": doc.document_id, "source_ref": doc.source_ref,
            "evidence_ref": doc.evidence_ref, "trust": doc.trust,
            "semantic_retention_completed": False}
        if admitted is False:
            output["reason"] = getattr(decision, "reason", "storage_unavailable")
        timeline.transition(thread_id=slot, expected_revision=state.revision,
            checkpoint_id=slot + ":completed", occurred_at=now,
            data={"output": output, "output_sha256": canonical_sha256(output)},
            event_type="HUMAN_UNDERSTANDING_COMPLETED", phase="learning_completed", status="completed")
        return ConversationActionReceipt(call, output)

    def pending(self, turn, *, step_index=0):
        admitted = self._available_host(turn)
        if admitted is None:
            return None
        _, _, _, timeline = admitted
        events = timeline.events(self._slot(turn, step_index))
        if not events:
            return None
        admission = events[0].payload["data"]
        sources = timeline.events(PostgresMemoryOutbox._thread_id(admission["document_id"]))
        if not sources:
            # An interrupted submission or a storage refusal does not invent a retained record.
            raise ConversationConflict("learning source was not queued")
        timeline.load(sources[0].thread_id)
        doc = conversation_memory_document_from_outbox_data(sources[0].payload["data"])
        record = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
        arguments = {key: record[key] for key in ("kind", "interpretation", "uncertainty_reason", "review_at",
                    "outcome", "previous_source_ref", "previous_evidence_ref")}
        expected = build_human_understanding_document(turn, arguments)
        if (replace(doc, retention_until=None) != expected
                or sources[0].event_type != "MEMORY_RETAIN_QUEUED"
                or (doc.retention_until is not None and doc.retention_until <= self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))):
            raise ConversationConflict("learning queued source differs")
        return self.execute(turn, ConversationActionCall(admission["call_id"], ACTION, arguments), step_index=step_index)

    def can_continue_after(self, turn, receipt):
        if receipt.call.name != ACTION or receipt.output.get("contract") != "EvaHumanUnderstandingAction/v1":
            return False
        self._host(turn)
        return receipt.output.get("status") in ("queued", "storage_refused")
