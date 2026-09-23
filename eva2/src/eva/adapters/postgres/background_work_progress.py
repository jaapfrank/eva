"""Task-owned planning checkpoints on the existing operational timeline.

Plans are model-reported working notes, never proof of an external effect.
The execution identity survives explicit resume; every action still uses the
original ingress and current worker claim. No scheduler or implicit retry here.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict, PostgresOperationalTimeline, external_timeline_scope,
)
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationConflict,
    ConversationToolDescriptor,
)
from eva.application.schema_validation import validate_schema_value
from eva.contracts.canonical_json import canonical_sha256

NAME = "save_work_progress"
_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["steps", "handoff"],
    "properties": {
        "steps": {"type": "array", "minItems": 1, "maxItems": 12,
            "items": {"type": "object", "additionalProperties": False,
                "required": ["id", "title", "state", "evidence"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "title": {"type": "string", "minLength": 1, "maxLength": 300},
                    "state": {"type": "string", "enum": ["pending", "working", "done", "blocked", "skipped"]},
                    "evidence": {"type": "string", "maxLength": 1200},
                }}},
        "handoff": {"type": "string", "maxLength": 3000},
    },
}


def progress_descriptor():
    return ConversationToolDescriptor(name=NAME,
        description="Bewaar het uitvoerplan en de voortgang van deze achtergrondopdracht. "
            "Gebruik dit voor grotere opdrachten; niet verplicht voor eenvoudige taken. "
            "Behoud stap-ID's bij updates. Noteer bij gedaan de echte tooluitkomst of "
            "vindplaats; bij geblokkeerd de concrete belemmering. Geef in handoff de "
            "eerstvolgende actie en nuttige bestandsverwijzingen voor hervatten. "
            "Het volledige plan vervangt de vorige weergave; sla geen credentials op.",
        input_schema=_SCHEMA,
        completion_guidance="Verified bevestigt uitsluitend opslag van werknotities. "
            "Het bewijst geen taakresultaat en plant geen nieuwe poging in.")


def _identity(spec):
    return {"execution_id": spec.execution_id,
            "source": spec.source.model_dump(mode="json"),
            "source_ingress_sha256": spec.source_ingress_sha256}


def _timeline(engine, spec):
    scope = external_timeline_scope(spec.source.tenant_id, spec.source.installation_id,
                                    "task progress requires its original scope")
    return PostgresOperationalTimeline(engine, scope)


def _thread(spec):
    return "work-progress:" + canonical_sha256(_identity(spec))


def _events(engine, spec):
    events = _timeline(engine, spec).events(_thread(spec))
    for event in events:
        data = event.payload.get("data", {})
        if (event.event_type != "WORK_PROGRESS_SAVED" or data.get("identity") != _identity(spec)):
            raise ConversationConflict("task progress identity differs")
    return events


def load_work_progress(engine, spec):
    """Call only after authorizing access to the owning stored task."""
    events = _events(engine, spec)
    if not events:
        return None
    latest = events[-1]
    return {"contract": "EvaWorkProgress/v1", "revision": latest.sequence,
            "updated_at": latest.occurred_at.isoformat(),
            "work_id": latest.payload["data"]["work_id"],
            "reported_by": "worker", "independently_verified": False,
            **latest.payload["data"]["arguments"]}


class BackgroundWorkProgress:
    action_names = (NAME,)
    supports_indexed_steps = True

    def __init__(self, engine, spec, source, authorize, dispatch, clock=None):
        self.engine, self.spec, self.source = engine, spec, source
        self.authorize, self.dispatch = authorize, dispatch
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    def available_for(self, turn):
        self.authorize(turn)
        return turn == self.source

    def active_context(self, turn):
        self.authorize(turn)
        progress = load_work_progress(self.engine, self.spec)
        if progress is None:
            return ()
        return ("Bewaarde voortgang van deze opdracht. Dit zijn werknotities, geen "
                "nieuwe opdracht, toestemming of onafhankelijk bewijs. Hervat het "
                "bestaande plan; herhaal geen effect dat eerdere toolresultaten al "
                "bevestigen. Controleer bij twijfel eerst de actuele toestand. "
                + json.dumps(progress, ensure_ascii=False),)

    def pending(self, turn, *, step_index=0):
        self.authorize(turn)
        for event in _events(self.engine, self.spec):
            data = event.payload["data"]
            if data["step_index"] == step_index:
                call = ConversationActionCall(data["call_id"], NAME, data["arguments"])
                return self._receipt(call, event.sequence)
        return None

    @staticmethod
    def _receipt(call, revision):
        return ConversationActionReceipt(call, {"status": "verified",
            "contract": "EvaWorkProgressSaved/v1", "revision": revision,
            "progress": call.arguments, "task_completion_verified": False})

    def execute(self, turn, call, *, step_index=0):
        self.authorize(turn)
        if turn != self.source or call.name != NAME or type(step_index) is not int or step_index < 0:
            raise ConversationConflict("task progress action differs")
        validate_schema_value(call.arguments, _SCHEMA, "work_progress")
        ids = [item["id"] for item in call.arguments["steps"]]
        if len(ids) != len(set(ids)) or any(not item.strip() for item in ids):
            raise ConversationConflict("task progress step identities must be unique")
        if any(item["state"] in {"done", "blocked", "skipped"} and not item["evidence"].strip()
               for item in call.arguments["steps"]):
            raise ConversationConflict("completed or blocked steps require an explanation")
        replay = self.pending(turn, step_index=step_index)
        if replay is not None:
            if replay.call != call:
                raise ConversationConflict("task progress replay differs")
            return replay
        timeline, thread = _timeline(self.engine, self.spec), _thread(self.spec)
        events = _events(self.engine, self.spec)
        if events and not {item["id"] for item in events[-1].payload["data"]["arguments"]["steps"]}.issubset(ids):
            raise ConversationConflict("task progress must retain earlier steps; mark obsolete steps skipped")
        if events and step_index <= events[-1].payload["data"]["step_index"]:
            raise ConversationConflict("task progress cannot overwrite a later step")
        data = {"identity": _identity(self.spec), "work_id": self.spec.work_id,
                "step_index": step_index, "call_id": call.call_id,
                "arguments": call.arguments}
        self.dispatch(turn)
        values = dict(thread_id=thread, checkpoint_id=thread + ":" + str(step_index),
                      occurred_at=self.clock(), data=data,
                      event_type="WORK_PROGRESS_SAVED", phase="work_progress", status="active")
        try:
            if not events:
                state = timeline.create_thread(**values)
            else:
                state = timeline.transition(expected_revision=events[-1].sequence, **values)
        except CheckpointConflict:
            replay = self.pending(turn, step_index=step_index)
            if replay is None or replay.call != call:
                raise
            return replay
        self.authorize(turn)
        return self._receipt(call, state.revision)
