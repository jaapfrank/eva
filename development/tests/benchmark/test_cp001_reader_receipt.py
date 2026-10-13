"""Authenticated isolated PG reader with separately pinned receipt trust."""

import copy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
import pytest
from starlette.requests import Request

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationTurn, ConversationModelUsage
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_people_memory_overview import people_memory_engine  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "development/benchmark/inspect_ai_v1"))
from cp001_reader_receipt import OwnerSnapshotReader, verify_bundle  # noqa: E402


def test_owner_reader_snapshot_receipt_uses_external_trust(request, tmp_path):
    engine = request.getfixturevalue("people_memory_engine")
    label = "reader-provenance"
    scope, _, binding = _seed_authority(engine, label)
    owner = _id(label, "principal")
    turn = ConversationTurn.private(
        "tenant:" + label,
        "installation:" + label,
        str(owner),
        "dashboard:" + str(binding),
        "message:private-reader",
        "PRIVATE FIXTURE TEXT",
        "2026-08-26T12:00:00Z",
    )
    journal = PostgresConversationJournal(engine, scope)
    attempt = journal.begin_model_request(turn)
    journal.complete_model_request(
        turn,
        attempt,
        ConversationModelUsage.measured(
            model_attempts=1, tool_calls=0, input_tokens=7, output_tokens=2
        ),
    )
    # Another person's row must not be included even under the same tenant.
    other = ConversationTurn.private(
        turn.tenant_id,
        turn.installation_id,
        "other-owner",
        "other-conversation",
        "message:other",
        "OTHER PRIVATE FIXTURE",
        "2026-08-26T12:00:00Z",
    )
    journal.begin_model_request(other)
    key = Ed25519PrivateKey.generate()
    token = "isolated-owner-token-" + "x" * 32
    options = dict(
        engine=engine,
        scope=scope,
        owner=owner,
        binding=binding,
        conversation_id=turn.conversation_id,
        message_id=turn.message_id,
        access=DashboardAccess(token),
        signing_key=key,
        root=ROOT,
        configuration={"mode": "isolated_reader", "version": 1},
        image_digest=None,
        clock=lambda: datetime(2026, 8, 26, 12, tzinfo=UTC),
    )
    reader = OwnerSnapshotReader(**options)

    def req(value=None):
        headers = (
            [] if value is None else [(b"authorization", ("Bearer " + value).encode())]
        )
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/isolated-snapshot",
                "headers": headers,
            }
        )

    challenge = "ab" * 32
    for value in (None, "wrong-token"):
        with pytest.raises(HTTPException) as denied:
            reader.read(req(value), challenge=challenge)
        assert denied.value.status_code == 401
    with pytest.raises(ValueError):
        OwnerSnapshotReader(**{**options, "access": DashboardAccess(None)})
    for override in (
        {"owner": _id(label, "other")},
        {"binding": _id(label, "missing")},
        {"clock": lambda: datetime(2026, 9, 4, tzinfo=UTC)},
    ):
        with pytest.raises(RuntimeError):
            OwnerSnapshotReader(**{**options, **override}).read(
                req(token), challenge=challenge
            )
    bundle = reader.read(req(token), challenge=challenge)
    scope_hash = canonical_sha256(
        {
            "tenant_id": str(scope.tenant_id),
            "installation_id": str(scope.installation_id),
            "principal_id": str(owner),
            "conversation_id": turn.conversation_id,
            "message_id": turn.message_id,
        }
    )
    # The public key/context/challenge are pinned by the test operator, not taken from bundle.
    trust = {
        "public_key": key.public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex(),
        "context": reader.context,
        "challenge": challenge,
        "scope_sha256": scope_hash,
    }
    export, verified = verify_bundle(bundle, trust)
    assert verified["status"] == "verified_against_pinned_reader_key"
    assert verified["image_binding"] == "unknown"
    assert bundle["receipt"]["transaction_read_only"] == "on"
    assert bundle["receipt"]["isolation"] == "repeatable read"
    assert len(bundle["rows"]) == 3 and len(export["direct_journal"]["events"]) == 2
    assert "OTHER PRIVATE FIXTURE" not in json.dumps(bundle)
    assert "PRIVATE FIXTURE TEXT" not in json.dumps(bundle["receipt"])
    # A later journal row cannot alter or be silently spliced into this signed snapshot.
    journal.begin_model_request(turn)
    assert verify_bundle(bundle, trust)[0] == export
    later = reader.read(req(token), challenge=challenge)
    assert later["receipt"]["row_count"] == 4
    for change in ("truncate", "challenge", "config", "embedded_key"):
        bad = copy.deepcopy(bundle)
        if change == "truncate":
            bad["rows"].pop()
        elif change == "challenge":
            bad["receipt"]["challenge"] = "cd" * 32
        elif change == "config":
            bad["receipt"]["context"]["configuration_sha256"] = "0" * 64
        else:
            attacker = Ed25519PrivateKey.generate()
            bad["public_key"] = (
                attacker.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            )
            bad["signature"] = attacker.sign(canonical_json_bytes(bad["receipt"])).hex()
        with pytest.raises(ValueError, match="provenance"):
            verify_bundle(bad, trust)
    out = Path(os.environ.get("CP001_READER_EVIDENCE_DIR", tmp_path))
    (out / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n")
    (out / "trust.json").write_text(json.dumps(trust, indent=2) + "\n")
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "development/benchmark/inspect_ai_v1/cp001_accounting.py"),
            "--reader-bundle",
            str(out / "bundle.json"),
            "--reader-trust",
            str(out / "trust.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 2 and not run.stderr
    result = json.loads(run.stdout)
    assert (
        result["reader_provenance"]["status"] == verified["status"]
        and not result["live_enabled"]
    )
    assert (
        result["participants"]["eva2"]["direct_journal_admission"]["status"]
        == "source_and_rows_bound"
    )
    (out / "cli.json").write_text(run.stdout)
