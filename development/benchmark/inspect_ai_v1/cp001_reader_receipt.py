"""Isolated owner snapshot reader/receipt seam; not installed as a live route."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from pathlib import Path

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.objects.identity_verifier import verify_ed25519_signature
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline,
    operational_events,
)
from eva.adapters.postgres.people_memory_overview import require_private_owner_binding
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
import sqlalchemy as sa

from cp001_journal_admission import EVENTS, SOURCES

READER_SOURCES = (
    *SOURCES,
    "src/eva/adapters/api/dashboard_access.py",
    "src/eva/adapters/postgres/people_memory_overview.py",
    "development/benchmark/inspect_ai_v1/cp001_reader_receipt.py",
)


class OwnerSnapshotReader:
    def __init__(
        self,
        *,
        engine,
        scope,
        owner,
        binding,
        conversation_id,
        message_id,
        access: DashboardAccess,
        signing_key,
        root: Path,
        configuration: dict,
        image_digest=None,
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ):
        if access.token is None:
            raise ValueError("snapshot reader requires configured owner authentication")
        self.engine, self.scope, self.owner, self.binding = (
            engine,
            scope,
            owner,
            binding,
        )
        self.identity = dict(
            principal_id=str(owner),
            conversation_id=conversation_id,
            message_id=message_id,
        )
        self.access, self.key, self.clock = access, signing_key, clock
        self.source_files = {
            p: hashlib.sha256((root / p).read_bytes()).hexdigest()
            for p in READER_SOURCES
        }
        self.context = {
            "source_files": self.source_files,
            "configuration_sha256": canonical_sha256(configuration),
            "image_digest": image_digest,
        }

    def read(self, request, *, challenge):
        self.access.require(request)
        if (
            not isinstance(challenge, str)
            or len(challenge) != 64
            or any(c not in "0123456789abcdef" for c in challenge)
        ):
            raise ValueError("invalid reader challenge")
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                PostgresOperationalTimeline(self.engine, self.scope)._scope(connection)
                authority = require_private_owner_binding(
                    connection,
                    scope=self.scope,
                    owner=self.owner,
                    binding=self.binding,
                    now=self.clock(),
                )
                if (authority.provider_id, authority.channel_id) not in {
                    ("telegram", "private"),
                    ("eva-dashboard", "owner-local"),
                }:
                    raise ValueError("reader requires a private owner binding")
                e = operational_events.c
                # No event-type/window/cursor supplied by the caller: all matching request rows.
                rows = (
                    connection.execute(
                        sa.select(e)
                        .where(
                            e.tenant_id == self.scope.tenant_id,
                            e.installation_id == self.scope.installation_id,
                            *(
                                e.payload["data"][k].astext == v
                                for k, v in self.identity.items()
                            ),
                        )
                        .order_by(e.thread_id, e.sequence)
                    )
                    .mappings()
                    .all()
                )
                snapshot = connection.exec_driver_sql(
                    "SELECT pg_current_snapshot()::text, current_setting('transaction_read_only'), current_setting('transaction_isolation')"
                ).one()
                all_rows = []
                for row in rows:
                    if (
                        canonical_sha256(row["payload"])
                        != bytes(row["payload_sha256"]).hex()
                    ):
                        raise ValueError("journal payload hash differs")
                    all_rows.append(
                        {
                            "tenant_id": str(row["tenant_id"]),
                            "installation_id": str(row["installation_id"]),
                            "thread_id": row["thread_id"],
                            "sequence": row["sequence"],
                            "event_id": str(row["event_id"]),
                            "event_type": row["event_type"],
                            "payload": row["payload"],
                            "payload_sha256": bytes(row["payload_sha256"]).hex(),
                        }
                    )
                ranges = {}
                for row in all_rows:
                    key = hashlib.sha256(row["thread_id"].encode()).hexdigest()
                    ranges.setdefault(key, []).append(row["sequence"])
                scope = {
                    "tenant_id": str(self.scope.tenant_id),
                    "installation_id": str(self.scope.installation_id),
                }
                export = {
                    "request": dict(self.identity),
                    "direct_journal": {
                        "scope": scope,
                        "source_files": {p: self.source_files[p] for p in SOURCES},
                        "events": [
                            {
                                k: row[k]
                                for k in (
                                    "tenant_id",
                                    "installation_id",
                                    "event_type",
                                    "payload",
                                )
                            }
                            for row in all_rows
                            if row["event_type"] in EVENTS
                        ],
                    },
                }
                receipt = {
                    "contract": "CP001OwnerSnapshot/v1",
                    "challenge": challenge,
                    "context": deepcopy(self.context),
                    "scope_sha256": canonical_sha256({**scope, **self.identity}),
                    "snapshot": snapshot[0],
                    "transaction_read_only": snapshot[1],
                    "isolation": snapshot[2],
                    "row_count": len(all_rows),
                    "thread_sequences": ranges,
                    "rows_sha256": canonical_sha256(all_rows),
                    "export_sha256": canonical_sha256(export),
                }
        # Signing occurs only after successful completion of the read-only transaction.
        return {
            "export": export,
            "rows": all_rows,
            "receipt": receipt,
            "signature": self.key.sign(canonical_json_bytes(receipt)).hex(),
        }


def verify_bundle(bundle, trust):
    """Trust must be pinned separately; an embedded key/boolean is not authority."""
    try:
        receipt = bundle["receipt"]
        verify_ed25519_signature(
            public_key_bytes=bytes.fromhex(trust["public_key"]),
            signature=bytes.fromhex(bundle["signature"]),
            message=canonical_json_bytes(receipt),
        )
        if (
            receipt["contract"] != "CP001OwnerSnapshot/v1"
            or receipt["challenge"] != trust["challenge"]
            or receipt["context"] != trust["context"]
            or receipt["scope_sha256"] != trust["scope_sha256"]
            or receipt["transaction_read_only"] != "on"
            or receipt["isolation"] != "repeatable read"
        ):
            raise ValueError("reader receipt context differs")
        rows, export = bundle["rows"], bundle["export"]
        if (
            receipt["rows_sha256"] != canonical_sha256(rows)
            or receipt["export_sha256"] != canonical_sha256(export)
            or receipt["row_count"] != len(rows)
        ):
            raise ValueError("reader receipt content differs")
        selected = [
            {
                k: row[k]
                for k in ("tenant_id", "installation_id", "event_type", "payload")
            }
            for row in rows
            if row["event_type"] in EVENTS
        ]
        if selected != export["direct_journal"]["events"]:
            raise ValueError("reader event selection differs")
        ranges = {}
        for row in rows:
            if canonical_sha256(row["payload"]) != row["payload_sha256"]:
                raise ValueError("reader row hash differs")
            key = hashlib.sha256(row["thread_id"].encode()).hexdigest()
            ranges.setdefault(key, []).append(row["sequence"])
        if ranges != receipt["thread_sequences"]:
            raise ValueError("reader range differs")
        return export, {
            "status": "verified_against_pinned_reader_key",
            "scope": "one authenticated reader snapshot of matching request rows",
            "snapshot": receipt["snapshot"],
            "row_count": len(rows),
            "image_binding": "unknown"
            if receipt["context"]["image_digest"] is None
            else "pinned_metadata",
            "provider_completeness": None,
            "live_enabled": False,
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("reader provenance verification failed") from error
