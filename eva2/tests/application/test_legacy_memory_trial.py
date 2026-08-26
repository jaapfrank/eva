from __future__ import annotations

import hashlib

import pytest

from eva.application.legacy_memory_trial import (
    LegacyMemoryRecord,
    LegacyMemoryTrialError,
    LegacyMemoryTrialSpec,
    build_legacy_memory_trial,
)
from eva.application.memory import MemoryAuthority, MemoryWriteReceipt, RecallCurrentFacts


TENANT_ID = "66a4aad5-0550-5211-8b36-398eabe5b7be"
INSTALLATION_ID = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
PRINCIPAL_ID = "8d0ce6a5-da38-51a8-81a6-f2a99d1652d3"
EVIDENCE_ID = "52752881-f9e4-58e7-a398-8c4a982d6794"
SOURCE_SHA = "a" * 64
PERSON_SHA = hashlib.sha256(b"owner-label").hexdigest()
CATEGORY_SHA = hashlib.sha256(b"preference").hexdigest()


def _spec(**changes: object) -> LegacyMemoryTrialSpec:
    return LegacyMemoryTrialSpec(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        target_principal_id=PRINCIPAL_ID,
        provenance_evidence_id=EVIDENCE_ID,
        legacy_source_sha256=SOURCE_SHA,
        selected_person_label_sha256=PERSON_SHA,
        imported_at="2026-08-26T12:00:00Z",
        retention_until="2026-09-02T12:00:00Z",
        **changes,
    )


def _record(**changes: object) -> LegacyMemoryRecord:
    values = {
        "legacy_memory_id": 42,
        "occurred_at": "2026-06-12T13:12:22.222Z",
        "person_label_sha256": PERSON_SHA,
        "content": "De proef mag alleen door de eigenaar worden beoordeeld.",
        "category_sha256": CATEGORY_SHA,
        "archived": False,
        **changes,
    }
    return LegacyMemoryRecord(**values)  # type: ignore[arg-type]


class _Repository:
    def __init__(self) -> None:
        self.commands = []

    def remember(self, command):  # type: ignore[no-untyped-def]
        self.commands.append(command)
        return MemoryWriteReceipt(
            disposition="inserted",
            source_event_id=command.source_event.source_event_id,
            fact_id=command.fact.fact_id,
            current_fact_id=command.current_fact_id,
            current_revision=1,
        )

    def recall_current(self, _query: RecallCurrentFacts):
        return ()


def test_trial_converts_one_selected_active_legacy_row_to_review_only_memory() -> None:
    command = build_legacy_memory_trial((_record(),), _spec())[0]

    assert command.source_event.source_kind == "legacy_unverified"
    assert command.source_event.trust == "legacy_unverified"
    assert command.source_event.privacy_class == "restricted"
    assert command.source_event.source_ref.endswith(":memories:42")
    assert command.fact.trust == "legacy_unverified"
    assert command.fact.predicate == "legacy.memory.42"
    assert command.scope["kind"] == "legacy_memory_trial"
    assert command.source_event.observed_at == "2026-06-12T13:12:22Z"


def test_trial_command_is_accepted_by_memory_authority_without_becoming_confirmed() -> None:
    command = build_legacy_memory_trial((_record(),), _spec())[0]
    repository = _Repository()

    receipt = MemoryAuthority(repository).remember(command)

    assert receipt.disposition == "inserted"
    assert repository.commands == [command]


@pytest.mark.parametrize(
    "record",
    (
        _record(archived=True),
        _record(person_label_sha256=hashlib.sha256(b"other").hexdigest()),
        _record(content=" "),
        _record(content="x" * 4097),
    ),
)
def test_trial_rejects_archived_other_person_and_unbounded_content(
    record: LegacyMemoryRecord,
) -> None:
    with pytest.raises(LegacyMemoryTrialError):
        build_legacy_memory_trial((record,), _spec())


def test_trial_rejects_more_than_fifty_rows() -> None:
    records = tuple(
        _record(legacy_memory_id=index)
        for index in range(1, 52)
    )

    with pytest.raises(LegacyMemoryTrialError, match="50"):
        build_legacy_memory_trial(records, _spec())
