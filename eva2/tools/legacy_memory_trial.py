"""Safe, review-only conversion of a selected old-Eva memory slice.

This module deliberately does *not* open the old database and does not choose
an owner.  The caller must supply a row-level source reader and an explicit
mapping from one legacy person label to one new Eva principal.  That keeps an
old label from silently becoming a new identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from eva.application.memory import RememberFact
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import ExperienceSourceEvent, MemoryFact


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_MAX_TRIAL_ROWS = 50
_MAX_MEMORY_BYTES = 4_096


class LegacyMemoryTrialError(ValueError):
    """A legacy slice is unsuitable for a review-only Eva2 trial."""


@dataclass(frozen=True, slots=True)
class LegacyMemoryRecord:
    """One old-Eva row after a read-only source adapter has normalised it."""

    legacy_memory_id: int
    occurred_at: str
    person_label_sha256: str
    content: str
    category_sha256: str
    archived: bool


@dataclass(frozen=True, slots=True)
class LegacyMemoryTrialSpec:
    """Explicit, review-only destination for one selected legacy person label."""

    tenant_id: str
    installation_id: str
    target_principal_id: str
    provenance_evidence_id: str
    legacy_source_sha256: str
    selected_person_label_sha256: str
    imported_at: str
    retention_until: str
    retention_policy_id: str = "legacy-memory-trial-v1"

    def __post_init__(self) -> None:
        for value in (
            self.tenant_id,
            self.installation_id,
            self.target_principal_id,
            self.provenance_evidence_id,
        ):
            if not _UUID.fullmatch(value):
                raise LegacyMemoryTrialError("trial scope IDs must be canonical UUIDs")
        for value in (
            self.legacy_source_sha256,
            self.selected_person_label_sha256,
        ):
            if not _SHA256.fullmatch(value):
                raise LegacyMemoryTrialError("trial hashes must be SHA-256 values")
        _require_canonical_timestamp(self.imported_at)
        _require_canonical_timestamp(self.retention_until)
        if self.retention_until < self.imported_at:
            raise LegacyMemoryTrialError("trial retention cannot precede import")
        if not self.retention_policy_id:
            raise LegacyMemoryTrialError("trial retention policy is required")


def _canonical_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LegacyMemoryTrialError("legacy timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise LegacyMemoryTrialError("legacy timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_canonical_timestamp(value: str) -> None:
    if _canonical_timestamp(value) != value:
        raise LegacyMemoryTrialError("trial timestamp must be canonical UTC")


def _stable_id(spec: LegacyMemoryTrialSpec, record: LegacyMemoryRecord, kind: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            ":".join(
                (
                    "eva2",
                    "legacy-memory-trial-v1",
                    spec.legacy_source_sha256,
                    str(record.legacy_memory_id),
                    kind,
                )
            ),
        )
    )


def build_legacy_memory_trial(
    records: tuple[LegacyMemoryRecord, ...], spec: LegacyMemoryTrialSpec
) -> tuple[RememberFact, ...]:
    """Make review-only commands without making an old memory conversational.

    Records are accepted only for one explicitly selected old person label.  A
    restricted privacy class and a dedicated non-conversation scope ensure that
    even a successful database write cannot appear in ordinary recall.
    """

    if not records:
        raise LegacyMemoryTrialError("legacy memory trial needs at least one row")
    if len(records) > _MAX_TRIAL_ROWS:
        raise LegacyMemoryTrialError("legacy memory trial is limited to 50 rows")

    commands: list[RememberFact] = []
    seen_ids: set[int] = set()
    for record in records:
        if record.legacy_memory_id < 1 or record.legacy_memory_id in seen_ids:
            raise LegacyMemoryTrialError("legacy memory IDs must be unique and positive")
        seen_ids.add(record.legacy_memory_id)
        if record.archived:
            raise LegacyMemoryTrialError("archived legacy memories are not importable")
        if record.person_label_sha256 != spec.selected_person_label_sha256:
            raise LegacyMemoryTrialError("legacy row is outside the selected person label")
        if not _SHA256.fullmatch(record.category_sha256):
            raise LegacyMemoryTrialError("legacy category must be represented by a hash")
        content = record.content.strip()
        if not content or len(content.encode("utf-8")) > _MAX_MEMORY_BYTES:
            raise LegacyMemoryTrialError("legacy memory content is outside the trial budget")

        occurred_at = _canonical_timestamp(record.occurred_at)
        source_id = _stable_id(spec, record, "source")
        fact_id = _stable_id(spec, record, "fact")
        current_id = _stable_id(spec, record, "current")
        source_ref = (
            f"legacy-sqlite:sha256:{spec.legacy_source_sha256}:"
            f"memories:{record.legacy_memory_id}"
        )
        value = {
            "legacy_memory_id": record.legacy_memory_id,
            "content": content,
            "category_sha256": record.category_sha256,
            "person_label_sha256": record.person_label_sha256,
        }
        source = ExperienceSourceEvent.model_validate(
            {
                "schema_version": 1,
                "contract": "ExperienceSourceEvent/v1",
                "source_event_id": source_id,
                "tenant_id": spec.tenant_id,
                "principal_id": spec.target_principal_id,
                "subject_id": spec.target_principal_id,
                "event_type": "legacy_memory_candidate",
                "source_kind": "legacy_unverified",
                "source_ref": source_ref,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "provenance_refs": [spec.provenance_evidence_id],
                "observed_at": occurred_at,
                "valid_from": occurred_at,
                "valid_until": None,
                "ingested_at": spec.imported_at,
                "trust": "legacy_unverified",
                "privacy_class": "restricted",
                "purpose": "legacy_memory_review",
                "retention_until": spec.retention_until,
                "delete_sla_at": spec.retention_until,
                "corrects_source_event_id": None,
                "supersedes_source_event_id": None,
                "retracts_source_event_id": None,
                "erasure_request_id": None,
            }
        )
        fact = MemoryFact.model_validate(
            {
                "schema_version": 1,
                "contract": "MemoryFact/v1",
                "fact_id": fact_id,
                "tenant_id": spec.tenant_id,
                "subject_id": spec.target_principal_id,
                "predicate": f"legacy.memory.{record.legacy_memory_id}",
                "value": value,
                "source_event_ids": [source_id],
                "observed_at": occurred_at,
                "valid_from": occurred_at,
                "valid_until": None,
                "trust": "legacy_unverified",
                "content_sha256": canonical_sha256(value),
                "assertion_status": "active",
                "superseded_by_fact_id": None,
                "retraction_source_event_id": None,
            }
        )
        commands.append(
            RememberFact(
                installation_id=spec.installation_id,
                current_fact_id=current_id,
                source_event=source,
                fact=fact,
                scope={
                    "kind": "legacy_memory_trial",
                    "principal_id": spec.target_principal_id,
                    "person_label_sha256": record.person_label_sha256,
                },
                retention_policy_id=spec.retention_policy_id,
                committed_at=spec.imported_at,
            )
        )
    return tuple(commands)
