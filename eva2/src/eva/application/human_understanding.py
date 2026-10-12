"""Typed provisional learning records carried by existing conversation memory."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from eva.application.memory import ConversationMemoryDocument
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

CONTENT_PREFIX = "Eva human understanding v1\n"
EVIDENCE_PREFIX = "human-understanding:"
MAX_LEARNING_RECORDS = 8
_ARGUMENT_FIELDS = frozenset({
    "kind", "interpretation", "uncertainty_reason", "review_at", "outcome",
    "previous_source_ref", "previous_evidence_ref",
})
_RECORD_FIELDS = _ARGUMENT_FIELDS | {"version", "source_text_sha256", "recorded_at"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def is_human_understanding_reference(value: object) -> bool:
    return (isinstance(value, str) and value.startswith(EVIDENCE_PREFIX)
            and _SHA256.fullmatch(value[len(EVIDENCE_PREFIX):]) is not None)


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("learning timestamp must be canonical UTC")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("learning timestamp must be canonical UTC") from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("learning timestamp must be canonical UTC")
    return value


def _record(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.keys() != _RECORD_FIELDS:
        raise ValueError("learning record fields differ")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("learning record version differs")
    if payload["kind"] not in ("person_hypothesis", "expectation"):
        raise ValueError("learning kind differs")
    for key, maximum in (("interpretation", 1600), ("uncertainty_reason", 600)):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError("learning text is empty or too large")
    if payload["outcome"] not in ("open", "confirmed", "refuted", "unknown"):
        raise ValueError("learning outcome differs")
    _timestamp(payload["recorded_at"])
    if payload["review_at"] is not None:
        _timestamp(payload["review_at"])
    elif payload["kind"] == "expectation":
        raise ValueError("expectation review time is required")
    source, evidence = payload["previous_source_ref"], payload["previous_evidence_ref"]
    if (source is None) != (evidence is None):
        raise ValueError("learning predecessor references must be paired")
    if source is not None:
        if (not isinstance(source, str) or not source.strip() or len(source) > 1024
                or not is_human_understanding_reference(evidence)):
            raise ValueError("learning predecessor reference differs")
    elif (payload["outcome"] in ("confirmed", "refuted")
          or (payload["kind"] == "expectation" and payload["outcome"] != "open")):
        raise ValueError("learning outcome requires a predecessor")
    if (not isinstance(payload["source_text_sha256"], str)
            or _SHA256.fullmatch(payload["source_text_sha256"]) is None):
        raise ValueError("learning source hash differs")
    return dict(payload)


def parse_human_understanding_record(content: str, *, evidence_ref: str) -> dict[str, Any]:
    """Parse exact canonical source bytes; extraction text is never a record."""
    if (not isinstance(content, str) or not content.startswith(CONTENT_PREFIX)
            or len(content.encode("utf-8")) > 16384
            or not is_human_understanding_reference(evidence_ref)):
        raise ValueError("learning source envelope differs")
    try:
        payload = _record(json.loads(content[len(CONTENT_PREFIX):]))
    except (TypeError, json.JSONDecodeError):
        raise ValueError("learning source JSON differs") from None
    if (content != CONTENT_PREFIX + canonical_json_bytes(payload).decode("utf-8")
            or evidence_ref != EVIDENCE_PREFIX + canonical_sha256(payload)):
        raise ValueError("learning source bytes or evidence hash differ")
    return payload


def human_understanding_document_id(*, tenant_id: str, installation_id: str,
                                    principal_id: str, conversation_id: str,
                                    source_ref: str, evidence_ref: str) -> str:
    return EVIDENCE_PREFIX + canonical_sha256([
        tenant_id, installation_id, principal_id, conversation_id, source_ref, evidence_ref,
    ])


def build_human_understanding_document(turn, arguments: dict[str, Any], *,
                                      retention_until: str | None = None) -> ConversationMemoryDocument:
    """Bind model meaning to the current real private ingress and host-owned scope."""
    if (turn.audience.kind != "private_person"
            or turn.audience.principal_id != turn.principal_id
            or turn.observation_binding is not None
            or not isinstance(arguments, dict) or arguments.keys() != _ARGUMENT_FIELDS):
        raise ValueError("learning arguments or private source scope differ")
    payload = _record({
        **arguments, "version": 1, "recorded_at": _timestamp(turn.occurred_at),
        "source_text_sha256": hashlib.sha256(turn.text.encode("utf-8")).hexdigest(),
    })
    evidence_ref = EVIDENCE_PREFIX + canonical_sha256(payload)
    return ConversationMemoryDocument(
        tenant_id=turn.tenant_id, installation_id=turn.installation_id,
        principal_id=turn.principal_id, conversation_id=turn.conversation_id,
        document_id=human_understanding_document_id(
            tenant_id=turn.tenant_id, installation_id=turn.installation_id,
            principal_id=turn.principal_id, conversation_id=turn.conversation_id,
            source_ref=turn.message_id, evidence_ref=evidence_ref),
        content=CONTENT_PREFIX + canonical_json_bytes(payload).decode("utf-8"),
        occurred_at=turn.occurred_at, source_ref=turn.message_id, evidence_ref=evidence_ref,
        privacy_class="confidential", trust="model_inference_unconfirmed",
        audience=turn.audience, project_id=turn.project_id, retention_until=retention_until,
    )


def validate_human_understanding_context(context: object, *, source_ref: str,
                                         evidence_ref: str) -> dict[str, Any]:
    """Validate a host-resolved chain before it enters model context."""
    if (not isinstance(context, dict) or context.keys() != {"contract", "records"}
            or context["contract"] != "EvaHumanUnderstandingContext/v1"
            or not isinstance(context["records"], list)
            or not 1 <= len(context["records"]) <= MAX_LEARNING_RECORDS):
        raise ValueError("learning context differs")
    selected = (source_ref, evidence_ref)
    first = context["records"][0]
    if not isinstance(first, dict):
        raise ValueError("learning context reference differs")
    source_ref, evidence_ref = first.get("source_ref"), first.get("evidence_ref")
    seen, child = set(), None
    for item in context["records"]:
        if not isinstance(item, dict) or item.keys() != {"source_ref", "evidence_ref", "record"}:
            raise ValueError("learning context reference differs")
        record = _record(item["record"])
        ref = (item["source_ref"], item["evidence_ref"])
        if (ref != (source_ref, evidence_ref) or ref in seen
                or evidence_ref != EVIDENCE_PREFIX + canonical_sha256(record)
                or (child is not None and (record["kind"] != child["kind"]
                    or record["recorded_at"] >= child["recorded_at"]))):
            raise ValueError("learning context chain differs")
        seen.add(ref)
        child = record
        source_ref, evidence_ref = record["previous_source_ref"], record["previous_evidence_ref"]
    if source_ref is not None or evidence_ref is not None or selected not in seen:
        raise ValueError("learning context dependency is absent")
    return context
