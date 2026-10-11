"""Project a real task outcome into the existing person-scoped memory contract."""
from __future__ import annotations

import hashlib
from datetime import UTC

from eva.application.memory import ConversationMemoryDocument
from eva.contracts.delegated_digital_work import (
    DelegatedDigitalWorkSpec, DelegatedInitiativeDigitalWorkSpec,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.application.memory_audience import memory_audience_to_wire


def outcome_document(work, source, *, ingress_sha256: str) -> ConversationMemoryDocument:
    """Keep reported output distinct from confirmed lifecycle state and user facts."""
    spec = work.spec
    if (type(spec) not in (DelegatedDigitalWorkSpec, DelegatedInitiativeDigitalWorkSpec) or spec.audience != "private"
            or source.audience.kind != "private_person"
            or spec.source.model_dump(mode="json") != {
                "tenant_id": source.tenant_id, "installation_id": source.installation_id,
                "principal_id": source.principal_id, "conversation_id": source.conversation_id,
                "message_id": source.message_id,
                "audience": {"kind": "private_person", "principal_id": source.principal_id}}
            or ingress_sha256 != spec.source_ingress_sha256
            or work.completed_at is None):
        raise ValueError("work memory differs from its private source")
    succeeded = work.state in {"succeeded", "verified"}
    if succeeded:
        if (work.result is None or work.result.spec_sha256 != spec.sha256
                or work.result.work_id != spec.work_id
                or work.result.call_id != work.call_id
                or hashlib.sha256(work.result.output.encode()).hexdigest()
                    != work.result.output_sha256):
            raise ValueError("work memory lacks its bound result")
        output = work.result.output
        result_hash = work.result.output_sha256
        status = "De werker heeft een eindresultaat opgeslagen."
        detail = (
            "Dit is Eva's gerapporteerde uitkomst, geen onafhankelijk bevestigd extern feit. "
            "Gebruik de bron en taakgegevens voor verdere verificatie.\n"
            + output[:8192]
            + ("\n[Ingekort; volledig resultaat staat in de oorspronkelijke taak.]" if len(output) > 8192 else "")
        )
    elif work.state in {"failed", "stopped", "needs_help"} and work.result is None:
        result_hash = "geen"
        status = "De taak is niet afgerond." if work.state != "stopped" else "De taak is gestopt."
        detail = "Geen bevestigd eindresultaat. Niet als succes onthouden of automatisch herhalen."
        if work.error_code is not None:
            detail += "\nGeregistreerde foutcode: " + work.error_code
    else:
        raise ValueError("work memory requires a terminal outcome")
    digest = hashlib.sha256(spec.work_id.encode()).hexdigest()
    source_label = ("Aanleiding (eigen initiatief van Eva): "
                    if type(spec) is DelegatedInitiativeDigitalWorkSpec
                    else "Oorspronkelijke vraag (directe gebruiker): ")
    content = (
        "Taakervaring — een resultaatregistratie, geen instructie of nieuwe bevoegdheid.\n"
        + source_label + source.text[:4096]
        + (" [ingekort]" if len(source.text) > 4096 else "")
        + "\nTaak: " + spec.task[:4096]
        + "\nTaaknummer: " + spec.work_id
        + "\nPoging: " + str(spec.generation)
        + "\nUitkomst: " + status
        + "\n" + detail
        + "\nResultaat-SHA256: " + result_hash
        + "\nDeze ervaring is geen algemene vaardigheid of bewezen verbetering."
    )
    return ConversationMemoryDocument(
        tenant_id=source.tenant_id, installation_id=source.installation_id,
        principal_id=source.principal_id, conversation_id=source.conversation_id,
        document_id="work-outcome:" + digest,
        content=content,
        occurred_at=work.completed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source_ref=source.message_id, evidence_ref="delegated-work:" + digest + ":" + spec.sha256,
        privacy_class="confidential", trust="model_inference_unconfirmed",
        audience=source.audience, project_id=source.project_id,
    )


def document_binding_sha256(document: ConversationMemoryDocument) -> str:
    """Exact provenance, independent of the storage budget's retention deadline."""
    return canonical_sha256({
        "tenant_id": document.tenant_id, "installation_id": document.installation_id,
        "principal_id": document.principal_id, "conversation_id": document.conversation_id,
        "document_id": document.document_id, "source_ref": document.source_ref,
        "evidence_ref": document.evidence_ref, "occurred_at": document.occurred_at,
        "privacy_class": document.privacy_class, "trust": document.trust,
        "audience": memory_audience_to_wire(document.audience), "project_id": document.project_id})
