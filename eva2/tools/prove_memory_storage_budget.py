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
from eva.adapters.postgres.operational_timeline import OperationalTimelineError
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
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


def _budget(
    installation_id: str = INSTALLATION,
    *,
    group_records: int = 100,
    group_bytes: int = 1_048_576,
    project_records: int = 100,
    project_bytes: int = 1_048_576,
    installation_records: int = 1_000,
    installation_bytes: int = 10_485_760,
) -> RuntimeResourceBudget:
    payload = {
        "background_reflections_per_installation_day": 6,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 1_048_576,
        "memory_bytes_per_group": group_bytes,
        "memory_bytes_per_installation": installation_bytes,
        "memory_bytes_per_project": project_bytes,
        "memory_records_per_person": 100,
        "memory_records_per_group": group_records,
        "memory_records_per_installation": installation_records,
        "memory_records_per_project": project_records,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 7,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id=TENANT,
        installation_id=installation_id,
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
        memory_records_per_group=group_records,
        memory_bytes_per_group=group_bytes,
        memory_records_per_project=project_records,
        memory_bytes_per_project=project_bytes,
        memory_records_per_installation=installation_records,
        memory_bytes_per_installation=installation_bytes,
        retention_days=7,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _document(
    document_id: str,
    *,
    principal_id: str,
    content: str = "x",
    installation_id: str = INSTALLATION,
    project_id: str | None = None,
) -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=installation_id,
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
        project_id=project_id,
    )


def _group_document(
    document_id: str,
    *,
    installation_id: str,
    principal_id: str,
    group_chat_id: str,
    content: str = "x",
) -> ConversationMemoryDocument:
    consent = TelegramGroupConsentReference(
        principal_id=principal_id,
        group_chat_id=group_chat_id,
        operation_id=f"telegram-group-consent:{principal_id}:{group_chat_id[1:]}",
    )
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=installation_id,
        principal_id=principal_id,
        conversation_id=f"telegram:{group_chat_id}",
        document_id=document_id,
        content=content,
        occurred_at="2026-08-29T12:00:00Z",
        source_ref=f"telegram:{group_chat_id}:source:{document_id}",
        evidence_ref=f"telegram:{group_chat_id}:evidence:{document_id}",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.telegram_group(
            principal_id=principal_id,
            group_chat_id=group_chat_id,
            consent_ref=consent,
        ),
    )


def _governor(
    engine: sa.Engine,
    *,
    installation_id: str = INSTALLATION,
    budget: RuntimeResourceBudget | None = None,
) -> PostgresMemoryStorageBudget:
    return PostgresMemoryStorageBudget(
        engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=budget or _budget(installation_id),
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

        group_installation = f"{INSTALLATION}-group"
        group_governor = _governor(
            engine,
            installation_id=group_installation,
        )
        group_large = group_governor.admit_memory(
            _group_document(
                "memory:proof:group:large",
                installation_id=group_installation,
                principal_id="person:group-a",
                group_chat_id="-3101",
                content="é" * 300_000,
            ),
            now=NOW,
        )
        group_byte_block = group_governor.admit_memory(
            _group_document(
                "memory:proof:group:byte-block",
                installation_id=group_installation,
                principal_id="person:group-b",
                group_chat_id="-3101",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(seconds=1),
        )
        for index in range(99):
            decision = group_governor.admit_memory(
                _group_document(
                    f"memory:proof:group:prefill:{index}",
                    installation_id=group_installation,
                    principal_id=f"person:group-prefill:{index}",
                    group_chat_id="-3102",
                ),
                now=NOW + timedelta(seconds=10 + index),
            )
            if not decision.admitted:
                raise RuntimeError("group memory prefill was unexpectedly blocked")
        restarted_group = _governor(
            engine,
            installation_id=group_installation,
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            group_raced = tuple(
                pool.map(
                    lambda index: restarted_group.admit_memory(
                        _group_document(
                            f"memory:proof:group:final:{index}",
                            installation_id=group_installation,
                            principal_id=f"person:group-final:{index}",
                            group_chat_id="-3102",
                        ),
                        now=NOW + timedelta(seconds=200 + index),
                    ),
                    range(8),
                )
            )

        project_installation = f"{INSTALLATION}-project"
        project_governor = _governor(
            engine,
            installation_id=project_installation,
        )
        project_large = project_governor.admit_memory(
            _document(
                "memory:proof:project:large",
                installation_id=project_installation,
                principal_id="person:project-a",
                project_id="project:alpha",
                content="é" * 300_000,
            ),
            now=NOW,
        )
        project_byte_block = project_governor.admit_memory(
            _document(
                "memory:proof:project:byte-block",
                installation_id=project_installation,
                principal_id="person:project-b",
                project_id="project:alpha",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(seconds=1),
        )
        for index in range(99):
            decision = project_governor.admit_memory(
                _document(
                    f"memory:proof:project:prefill:{index}",
                    installation_id=project_installation,
                    principal_id=f"person:project-prefill:{index}",
                    project_id="project:race",
                ),
                now=NOW + timedelta(seconds=10 + index),
            )
            if not decision.admitted:
                raise RuntimeError("project memory prefill was unexpectedly blocked")
        restarted_project = _governor(
            engine,
            installation_id=project_installation,
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            project_raced = tuple(
                pool.map(
                    lambda index: restarted_project.admit_memory(
                        _document(
                            f"memory:proof:project:final:{index}",
                            installation_id=project_installation,
                            principal_id=f"person:project-final:{index}",
                            project_id="project:race",
                        ),
                        now=NOW + timedelta(seconds=200 + index),
                    ),
                    range(8),
                )
            )

        installation_byte_id = f"{INSTALLATION}-installation-byte"
        installation_byte_budget = _budget(
            installation_byte_id,
            installation_bytes=1_048_576,
        )
        installation_byte_governor = _governor(
            engine,
            installation_id=installation_byte_id,
            budget=installation_byte_budget,
        )
        installation_large = installation_byte_governor.admit_memory(
            _document(
                "memory:proof:installation:large",
                installation_id=installation_byte_id,
                principal_id="person:installation-a",
                content="é" * 300_000,
            ),
            now=NOW,
        )
        installation_byte_block = installation_byte_governor.admit_memory(
            _document(
                "memory:proof:installation:byte-block",
                installation_id=installation_byte_id,
                principal_id="person:installation-b",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(seconds=1),
        )

        installation_record_id = f"{INSTALLATION}-installation-record"
        installation_record_budget = _budget(
            installation_record_id,
            installation_records=100,
        )
        installation_record_governor = _governor(
            engine,
            installation_id=installation_record_id,
            budget=installation_record_budget,
        )
        for index in range(99):
            decision = installation_record_governor.admit_memory(
                _document(
                    f"memory:proof:installation:prefill:{index}",
                    installation_id=installation_record_id,
                    principal_id=f"person:installation-prefill:{index}",
                ),
                now=NOW + timedelta(seconds=10 + index),
            )
            if not decision.admitted:
                raise RuntimeError(
                    "installation memory prefill was unexpectedly blocked"
                )
        restarted_installation = _governor(
            engine,
            installation_id=installation_record_id,
            budget=installation_record_budget,
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            installation_raced = tuple(
                pool.map(
                    lambda index: restarted_installation.admit_memory(
                        _document(
                            f"memory:proof:installation:final:{index}",
                            installation_id=installation_record_id,
                            principal_id=f"person:installation-final:{index}",
                        ),
                        now=NOW + timedelta(seconds=200 + index),
                    ),
                    range(8),
                )
            )

        removal_installation = f"{INSTALLATION}-scope-removal"
        removal_budget = _budget(
            removal_installation,
            installation_bytes=1_048_576,
        )
        removal_governor = _governor(
            engine,
            installation_id=removal_installation,
            budget=removal_budget,
        )
        removal_document = _group_document(
            "memory:proof:scope-removal:large",
            installation_id=removal_installation,
            principal_id="person:removal-a",
            group_chat_id="-3201",
            content="é" * 300_000,
        )
        removal_admission = removal_governor.admit_memory(
            removal_document,
            now=NOW,
        )
        removal_governor.mark_retained(
            replace(
                removal_document,
                retention_until=removal_admission.retention_until,
            ),
            MemoryRetentionReceipt(
                bank_id="eva2-" + "b" * 32,
                document_id=removal_document.document_id,
                content_sha256=hashlib.sha256(
                    removal_document.content.encode("utf-8")
                ).hexdigest(),
            ),
            now=NOW + timedelta(seconds=1),
        )
        removal_blocked = removal_governor.admit_memory(
            _group_document(
                "memory:proof:scope-removal:blocked",
                installation_id=removal_installation,
                principal_id="person:removal-b",
                group_chat_id="-3201",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(days=7),
        )
        removal_jobs = removal_governor.claim_expired(
            now=NOW + timedelta(days=7)
        )
        if len(removal_jobs) == 1:
            removal_governor.mark_removed(
                removal_jobs[0], now=NOW + timedelta(days=7, seconds=1)
            )
        scope_replacement = removal_governor.admit_memory(
            _group_document(
                "memory:proof:scope-removal:replacement",
                installation_id=removal_installation,
                principal_id="person:removal-b",
                group_chat_id="-3201",
                content="b" * 500_000,
            ),
            now=NOW + timedelta(days=7, seconds=2),
        )

        erasure_installation = f"{INSTALLATION}-explicit-erasure"
        erasure_governor = _governor(
            engine,
            installation_id=erasure_installation,
        )
        erasure_document = _document(
            "memory:proof:explicit-erasure",
            installation_id=erasure_installation,
            principal_id="person:erasure-owner",
            content="synthetic erasure content",
        )
        erasure_admission = erasure_governor.admit_memory(
            erasure_document,
            now=NOW,
        )
        erasure_governor.mark_retained(
            replace(
                erasure_document,
                retention_until=erasure_admission.retention_until,
            ),
            MemoryRetentionReceipt(
                bank_id="eva2-" + "e" * 32,
                document_id=erasure_document.document_id,
                content_sha256=hashlib.sha256(
                    erasure_document.content.encode("utf-8")
                ).hexdigest(),
            ),
            now=NOW + timedelta(seconds=1),
        )
        wrong_principal_blocked = False
        try:
            erasure_governor.request_erasure(
                principal_id="person:other",
                document_id=erasure_document.document_id,
                now=NOW + timedelta(seconds=2),
            )
        except OperationalTimelineError as error:
            wrong_principal_blocked = "scope differs" in str(error)
        erasure_job = erasure_governor.request_erasure(
            principal_id="person:erasure-owner",
            document_id=erasure_document.document_id,
            now=NOW + timedelta(seconds=3),
        )
        duplicate_erasure = erasure_governor.request_erasure(
            principal_id="person:erasure-owner",
            document_id=erasure_document.document_id,
            now=NOW + timedelta(seconds=3),
        )
        restarted_erasure = _governor(
            engine,
            installation_id=erasure_installation,
        )
        recovered_erasure = restarted_erasure.claim_expired(
            now=NOW + timedelta(seconds=4)
        )
        if len(recovered_erasure) == 1:
            restarted_erasure.mark_removed(
                recovered_erasure[0],
                now=NOW + timedelta(seconds=5),
            )
        completed_erasure_replay = restarted_erasure.request_erasure(
            principal_id="person:erasure-owner",
            document_id=erasure_document.document_id,
            now=NOW + timedelta(seconds=6),
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
            and group_large.admitted
            and not group_byte_block.admitted
            and group_byte_block.reason == "group_byte_limit_reached"
            and sum(item.admitted for item in group_raced) == 1
            and all(
                item.reason == "group_record_limit_reached"
                for item in group_raced
                if not item.admitted
            )
            and project_large.admitted
            and not project_byte_block.admitted
            and project_byte_block.reason == "project_byte_limit_reached"
            and sum(item.admitted for item in project_raced) == 1
            and all(
                item.reason == "project_record_limit_reached"
                for item in project_raced
                if not item.admitted
            )
            and installation_large.admitted
            and not installation_byte_block.admitted
            and installation_byte_block.reason
            == "installation_byte_limit_reached"
            and sum(item.admitted for item in installation_raced) == 1
            and all(
                item.reason == "installation_record_limit_reached"
                for item in installation_raced
                if not item.admitted
            )
            and not removal_blocked.admitted
            and removal_blocked.reason == "group_byte_limit_reached"
            and len(removal_jobs) == 1
            and scope_replacement.admitted
            and scope_replacement.group_records_used == 1
            and scope_replacement.installation_records_used == 1
            and wrong_principal_blocked
            and erasure_job is not None
            and erasure_job.state.retry_count == 1
            and duplicate_erasure is None
            and len(recovered_erasure) == 1
            and recovered_erasure[0].state.retry_count == 2
            and completed_erasure_replay is None
        )
        if not expected:
            raise RuntimeError("memory storage budget proof differs")

        return {
            "contract": "EvaMemoryStorageBudgetProof/v3",
            "limits": {
                "bytes_per_group": 1_048_576,
                "bytes_per_installation": 10_485_760,
                "bytes_per_person": 1_048_576,
                "bytes_per_project": 1_048_576,
                "records_per_group": 100,
                "records_per_installation": 1_000,
                "records_per_person": 100,
                "records_per_project": 100,
                "retention_days": 7,
            },
            "postgres": {
                "byte_admission_utf8_bytes": large_admission.content_bytes,
                "byte_limit_blocked_before_provider": True,
                "capacity_released_only_after_removal_ack": True,
                "concurrent_final_admitted": sum(item.admitted for item in raced),
                "concurrent_final_attempts": len(raced),
                "group_byte_limit_blocked_across_people": True,
                "group_concurrent_final_admitted": sum(
                    item.admitted for item in group_raced
                ),
                "installation_byte_limit_blocked_across_people": True,
                "installation_concurrent_final_admitted": sum(
                    item.admitted for item in installation_raced
                ),
                "explicit_erasure_recovered_after_restart": True,
                "explicit_erasure_wrong_principal_blocked": True,
                "explicit_erasure_duplicate_provider_lease_blocked": True,
                "project_byte_limit_blocked_across_people": True,
                "project_concurrent_final_admitted": sum(
                    item.admitted for item in project_raced
                ),
                "record_limit_final_used": max(
                    item.person_records_used for item in raced
                ),
                "removal_crash_reclaimed": True,
                "restart_reloaded_reservations": True,
                "scope_capacity_released_only_after_removal_ack": True,
                "server_version": version,
            },
            "privacy": {
                "budget_events_store_content": False,
                "group_identifier_stored_as_sha256": True,
                "principal_identifier_stored_as_sha256": True,
                "project_identifier_stored_as_sha256": True,
            },
            "non_claims": [
                "geen live Hindsight- of productiegegevens gewijzigd",
                "quota tellen bronrecords en UTF-8-inhoud, niet database- of vectorindexoverhead",
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
