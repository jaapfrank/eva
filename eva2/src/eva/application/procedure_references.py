"""Host-owned reusable artifact references, separate from personal facts.

A search hit is only a candidate ID. No procedure text acquires execution or
memory-fact authority through this boundary.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Protocol

from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience
from eva.contracts.canonical_json import canonical_sha256


class ProcedureReferenceRejected(ValueError):
    pass


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProcedureReferenceRejected("unbound time")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ProcedureScope:
    tenant_id: str
    installation_id: str
    principal_id: str
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProcedureReference:
    document_id: str
    use_when: str
    path: str
    content_sha256: str
    source_call_id: str
    source_message_id: str
    tool_configuration_sha256: str
    receipt_sha256: str
    retention_until: str


MAX_RESOLVED_CONTENT_BYTES = 16384


@dataclass(frozen=True, slots=True)
class ResolvedProcedure:
    reference: ProcedureReference
    untrusted_content: str


@dataclass(frozen=True, slots=True)
class VerifiedSaveSource:
    """Loaded by the host receipt port, never accepted from model tool arguments."""
    scope: ProcedureScope
    admission: dict
    terminal_event_type: str
    proof: dict


class ProcedureSources(Protocol):
    def record_source(self, document: ConversationMemoryDocument): ...
    def resolve_source(self, document_id: str, *, principal_id: str,
                       project_id: str | None) -> ConversationMemoryDocument | None: ...


class ProcedureHost(Protocol):
    def load_save(self, scope: ProcedureScope, call_id: str) -> VerifiedSaveSource: ...
    def permits(self, scope: ProcedureScope, path: str) -> bool: ...
    def read_current(self, scope: ProcedureScope, path: str) -> bytes: ...
    def source_active(self, scope: ProcedureScope, source_message_id: str) -> bool: ...


class ProcedureReferences:
    def __init__(self, sources: ProcedureSources, host: ProcedureHost):
        self._sources, self._host = sources, host

    def designate(self, scope: ProcedureScope, *, source_call_id: str,
                  use_when: str, now: str, retention_until: str) -> ProcedureReference:
        """Explicit reusable designation of an already verified save, not every save."""
        if not isinstance(use_when, str) or not use_when.strip() or len(use_when) > 1000:
            raise ProcedureReferenceRejected("invalid applicability description")
        if _instant(retention_until) <= _instant(now):
            raise ProcedureReferenceRejected("expired reference")
        source = self._host.load_save(scope, source_call_id)
        a, proof = source.admission, source.proof
        output = a.get("receipt_output", {})
        args = a.get("action_arguments", {})
        if (source.scope != scope or a.get("principal_id") != scope.principal_id
                or a.get("action_call_id") != source_call_id
                or source.terminal_event_type != "STATE_VERIFIED"
                or output.get("transport") != "mcp_gateway"
                or output.get("operation_kind") in {"invoke", "read"}
                or output.get("external_state_verified") is False
                or proof.get("evidence", {}).get("source") != "mcp_gateway_readback"
                or proof.get("expected") != proof.get("observed")
                or proof.get("observed", {}).get("content") != args.get("content")
                or not isinstance(args.get("content"), str)
                or not isinstance(args.get("path"), str) or not args["path"]):
            raise ProcedureReferenceRejected("not a bound verified save")
        config = a.get("request_tool_configuration_sha256")
        if (not isinstance(config, str) or len(config) != 64
                or any(c not in "0123456789abcdef" for c in config)
                or output.get("tool_configuration_sha256") != config
                or a.get("request_expected_proof") != proof["expected"]
                or a.get("request_observation_arguments", {}).get("path") != args["path"]
                or a.get("request_arguments", {}).get("path") != args["path"]
                or a.get("request_arguments", {}).get("content") != args["content"]):
            raise ProcedureReferenceRejected("save proof binding differs")
        message = a.get("source_message_id")
        conversation = a.get("conversation_id")
        if not isinstance(message, str) or not message or not isinstance(conversation, str) or not conversation:
            raise ProcedureReferenceRejected("source identity missing")
        digest = hashlib.sha256(args["content"].encode("utf-8")).hexdigest()
        receipt_hash = canonical_sha256({"admission": a, "proof": proof})
        doc_id = "procedure-reference:" + canonical_sha256({"scope": asdict(scope), "receipt": receipt_hash})
        reference = ProcedureReference(doc_id, use_when.strip(), args["path"], digest,
            source_call_id, message, config, receipt_hash, retention_until)
        self._check_current(scope, reference, now)
        document = ConversationMemoryDocument(tenant_id=scope.tenant_id,
            installation_id=scope.installation_id, principal_id=scope.principal_id,
            project_id=scope.project_id, conversation_id=conversation,
            document_id=doc_id, content=json.dumps({"contract":"ProcedureReference/v1", **asdict(reference)}, sort_keys=True),
            occurred_at=now, source_ref=message, evidence_ref="verified-save:"+receipt_hash,
            privacy_class="confidential", trust="model_inference_unconfirmed",
            audience=MemoryAudience.private_person(principal_id=scope.principal_id),
            retention_until=retention_until)
        self._sources.record_source(document)
        return reference

    def _check_current(self, scope: ProcedureScope, reference: ProcedureReference, now: str) -> bytes:
        if (_instant(now) >= _instant(reference.retention_until)
                or not self._host.source_active(scope, reference.source_message_id)
                or not self._host.permits(scope, reference.path)):
            raise ProcedureReferenceRejected("source expired, revoked or not permitted")
        source = self._host.load_save(scope, reference.source_call_id)
        if (source.scope != scope or source.terminal_event_type != "STATE_VERIFIED"
                or canonical_sha256({"admission": source.admission, "proof": source.proof}) != reference.receipt_sha256):
            raise ProcedureReferenceRejected("durable save source changed")
        current = self._host.read_current(scope, reference.path)
        if not isinstance(current, bytes) or hashlib.sha256(current).hexdigest() != reference.content_sha256:
            raise ProcedureReferenceRejected("artifact changed")
        # A revocation arriving during read must not yield a usable reference.
        if not self._host.source_active(scope, reference.source_message_id) or not self._host.permits(scope, reference.path):
            raise ProcedureReferenceRejected("source revoked during read")
        return current

    def resolve_candidates(self, scope: ProcedureScope, candidate_ids: tuple[str, ...],
                           *, now: str) -> tuple[ResolvedProcedure, ...]:
        if len(candidate_ids) > 8 or len(set(candidate_ids)) != len(candidate_ids):
            raise ProcedureReferenceRejected("candidate bound exceeded")
        resolved = []
        total_bytes = 0
        for doc_id in candidate_ids:
            document = self._sources.resolve_source(doc_id, principal_id=scope.principal_id,
                                                    project_id=scope.project_id)
            if document is None:
                continue
            if (document.tenant_id != scope.tenant_id or document.installation_id != scope.installation_id
                    or document.principal_id != scope.principal_id or document.project_id != scope.project_id
                    or document.audience != MemoryAudience.private_person(principal_id=scope.principal_id)
                    or document.trust != "model_inference_unconfirmed"):
                raise ProcedureReferenceRejected("source scope differs")
            try:
                wire = json.loads(document.content)
                if wire.pop("contract") != "ProcedureReference/v1":
                    raise ValueError("contract")
                reference = ProcedureReference(**wire)
                if (reference.document_id != doc_id or document.document_id != doc_id
                        or reference.retention_until != document.retention_until
                        or document.source_ref != reference.source_message_id
                        or document.evidence_ref != "verified-save:"+reference.receipt_sha256):
                    raise ValueError("binding")
            except (ValueError, TypeError, KeyError) as error:
                raise ProcedureReferenceRejected("invalid source reference") from error
            current = self._check_current(scope, reference, now)
            total_bytes += len(current)
            if total_bytes > MAX_RESOLVED_CONTENT_BYTES:
                raise ProcedureReferenceRejected("resolved content bound exceeded")
            try:
                content = current.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise ProcedureReferenceRejected("procedure is not UTF-8 text") from error
            resolved.append(ResolvedProcedure(reference, content))
        return tuple(resolved)
