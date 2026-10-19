"""Typed metadata must not become synthetic memory text or renewed authority."""
from dataclasses import FrozenInstanceError
import hashlib

import pytest

from eva.application.memory_source_lifecycle import (
    CompactedPrivateMemorySource, compacted_private_memory_source_from_data,
    source_identity_sha256,
)
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_dream_memory_handoff import _dream


def _data():
    data=PostgresMemoryOutbox._document_data(_dream("installation","source"))
    content=data.pop("content")
    receipt=dict(bank_id="bank",document_id=data["document_id"],
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),operation_id="op",
        selected_fact_count=0,disposition="not_selected")
    data["content_compaction"]=dict(contract="EvaPrivateMemorySourceCompaction/v1",
        compacted_at="2026-08-30T00:00:00Z", queued_payload_sha256="1"*64,
        content_sha256=receipt["content_sha256"], source_payload_sha256="2"*64,
        ingress_payload_sha256="3"*64,ingress_text_sha256="4"*64,
        reply_payload_sha256="5"*64,reply_text_sha256="6"*64,
        cohort_manifest_sha256="7"*64,bank_id="bank",operation_id="op",
        selected_fact_count=0,disposition="not_selected",receipt_sha256=canonical_sha256(receipt),
        source_identity_sha256=source_identity_sha256(data))
    return data


def test_compacted_source_is_frozen_identity_without_content():
    source=compacted_private_memory_source_from_data(_data())
    assert isinstance(source,CompactedPrivateMemorySource)
    assert not hasattr(source,"content")
    with pytest.raises(FrozenInstanceError):
        source.principal_id="other"


@pytest.mark.parametrize("field,value",[("content","placeholder"),("project_id","project"),
    ("principal_id","other"),("retention_mode","immediate"),("trust","imported_legacy"),
    ("occurred_at","2026-08-30T00:00:00Z"),("content_erasure",{})])
def test_raw_changed_or_nonordinary_metadata_is_rejected(field,value):
    data=_data(); data[field]=value
    with pytest.raises(ValueError): compacted_private_memory_source_from_data(data)


@pytest.mark.parametrize("field,value",[("selected_fact_count",None),("selected_fact_count",True),
    ("selected_fact_count",-1),("operation_id",""),("content_sha256","bad"),
    ("receipt_sha256","f"*64),("source_identity_sha256","f"*64),("disposition","retained")])
def test_unknown_forged_or_mismatched_proof_is_not_selected_zero(field,value):
    data=_data(); data["content_compaction"][field]=value
    with pytest.raises(ValueError): compacted_private_memory_source_from_data(data)
