#!/usr/bin/env python3
"""Prove durable semantic-memory bytes, records and expiry on disposable PostgreSQL."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

import sqlalchemy as sa

from eva.adapters.postgres.runtime_resource_usage import PostgresMemoryStorageBudget
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from eva.bootstrap.secrets import read_runtime_secret_file
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.runtime_budget import RuntimeResourceBudget


ROOT = Path(__file__).resolve().parents[1]
TENANT = "memory-storage-proof-tenant"
INSTALLATION = "memory-storage-proof-installation"
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
_COMMIT = re.compile(r"[0-9a-f]{40}")
SOURCE_PATHS = (
    "src/eva/adapters/postgres/conversation_journal.py",
    "src/eva/adapters/postgres/memory_outbox.py",
    "src/eva/adapters/postgres/runtime_resource_usage.py",
    "src/eva/adapters/projection/hindsight.py",
    "src/eva/application/memory.py",
    "src/eva/application/memory_background.py",
    "src/eva/bootstrap/runtime.py",
    "tests/application/test_memory_background.py",
    "tests/bootstrap/test_memory_provenance.py",
    "tests/postgres/test_operational_timeline.py",
    "tests/postgres/test_runtime_resource_usage.py",
    "tests/projection/test_hindsight.py",
    "tools/prove_memory_storage_budget.py",
)


def _budget() -> RuntimeResourceBudget:
    payload = {
        "background_reflections_per_installation_day": 6,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v1",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 1_048_576,
        "memory_records_per_person": 100,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 7,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=100,
        memory_bytes_per_person=1_048_576,
        retention_days=7,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _document(
    document_id: str, *, principal_id: str, content: str = "x"
) -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        principal_id=principal_id,
        conversation_id=f"conversation:{principal_id}",
        document_id=document_id,
        content=content,
        occurred_at="2026-08-29T12:00:00Z",
        source_ref=f"source:{document_id}",
        evidence_ref=f"evidence:{document_id}",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id=principal_id),
    )


def _governor(engine: sa.Engine) -> PostgresMemoryStorageBudget:
    return PostgresMemoryStorageBudget(
        engine,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        resource_budget=_budget(),
        removal_lease_seconds=1,
    )


def prove(
    database_dsn: str, *, source_commit: str, focused_tests_passed: int
) -> dict[str, object]:
    if _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source commit must be an exact lowercase Git commit")
    if focused_tests_passed < 1:
        raise ValueError("focused test count is invalid")
    engine = sa.create_engine(database_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            version = str(connection.scalar(sa.text("SHOW server_version")))
        governor = _governor(engine)

        large = _document(
            "memory:proof:large",
            principal_id="person:bytes",
            content="é" * 300_000,
        )
        large_admission = governor.admit_memory(large, now=NOW)
        replay = governor.admit_memory(large, now=NOW + timedelta(seconds=1))
        byte_block = governor.admit_memory(
            _document(
                "memory:proof:byte-block",
                principal_id="person:bytes",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(seconds=2),
        )
        separate_person = governor.admit_memory(
            _document(
                "memory:proof:separate",
                principal_id="person:separate",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(seconds=3),
        )

        for index in range(99):
            decision = governor.admit_memory(
                _document(
                    f"memory:proof:race:prefill:{index}",
                    principal_id="person:race",
                ),
                now=NOW + timedelta(seconds=10 + index),
            )
            if not decision.admitted:
                raise RuntimeError("memory record prefill was unexpectedly blocked")
        with ThreadPoolExecutor(max_workers=8) as pool:
            raced = tuple(
                pool.map(
                    lambda index: governor.admit_memory(
                        _document(
                            f"memory:proof:race:final:{index}",
                            principal_id="person:race",
                        ),
                        now=NOW + timedelta(seconds=200 + index),
                    ),
                    range(8),
                )
            )

        admitted_large = replace(
            large, retention_until=large_admission.retention_until
        )
        receipt = MemoryRetentionReceipt(
            bank_id="eva2-" + "a" * 32,
            document_id=large.document_id,
            content_sha256=hashlib.sha256(large.content.encode("utf-8")).hexdigest(),
        )
        governor.mark_retained(
            admitted_large, receipt, now=NOW + timedelta(seconds=300)
        )
        if governor.claim_expired(now=NOW + timedelta(days=7, seconds=-1)):
            raise RuntimeError("memory expired before its configured deadline")

        restarted = _governor(engine)
        first_claim = restarted.claim_expired(now=NOW + timedelta(days=7))
        while_lease = restarted.claim_expired(now=NOW + timedelta(days=7))
        second_claim = restarted.claim_expired(
            now=NOW + timedelta(days=7, seconds=1)
        )
        if len(second_claim) == 1:
            restarted.mark_removed(
                second_claim[0], now=NOW + timedelta(days=7, seconds=2)
            )
        replacement = restarted.admit_memory(
            _document(
                "memory:proof:replacement",
                principal_id="person:bytes",
                content="c" * 500_000,
            ),
            now=NOW + timedelta(days=7, seconds=3),
        )

        expected = (
            large_admission.admitted
            and large_admission.content_bytes == 600_000
            and large_admission.retention_until == "2026-09-05T12:00:00Z"
            and replay.replayed
            and not byte_block.admitted
            and byte_block.reason == "person_byte_limit_reached"
            and separate_person.admitted
            and sum(item.admitted for item in raced) == 1
            and all(
                item.reason == "person_record_limit_reached"
                for item in raced
                if not item.admitted
            )
            and len(first_claim) == 1
            and while_lease == ()
            and len(second_claim) == 1
            and second_claim[0].state.retry_count == 2
            and replacement.admitted
            and replacement.person_records_used == 1
            and replacement.person_bytes_used == 500_000
        )
        if not expected:
            raise RuntimeError("memory storage budget proof differs")

        return {
            "contract": "EvaMemoryStorageBudgetProof/v1",
            "limits": {
                "bytes_per_person": 1_048_576,
                "records_per_person": 100,
                "retention_days": 7,
            },
            "postgres": {
                "byte_admission_utf8_bytes": large_admission.content_bytes,
                "byte_limit_blocked_before_provider": True,
                "capacity_released_only_after_removal_ack": True,
                "concurrent_final_admitted": sum(item.admitted for item in raced),
                "concurrent_final_attempts": len(raced),
                "record_limit_final_used": max(
                    item.person_records_used for item in raced
                ),
                "removal_crash_reclaimed": True,
                "restart_reloaded_reservations": True,
                "server_version": version,
            },
            "privacy": {
                "budget_events_store_content": False,
                "principal_identifier_stored_as_sha256": True,
            },
            "non_claims": [
                "geen live Hindsight- of productiegegevens gewijzigd",
                "nog geen quota per groep, project of hele installatie",
                "nog geen volledige verwijdering van brongesprekken of back-ups",
            ],
            "source_commit": source_commit,
            "source_files": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256(
                        (ROOT / relative).read_bytes()
                    ).hexdigest(),
                }
                for relative in SOURCE_PATHS
            ],
            "tests": {"focused_passed": focused_tests_passed},
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-dsn-file", type=Path, required=True)
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--source-commit", required=True)
    arguments = parser.parse_args()
    dsn = read_runtime_secret_file(
        str(arguments.database_dsn_file.resolve()),
        setting_name="memory storage budget proof database DSN",
    )
    print(
        json.dumps(
            prove(
                dsn,
                source_commit=arguments.source_commit,
                focused_tests_passed=arguments.focused_tests_passed,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
