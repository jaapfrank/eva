from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

import pytest

from eva.adapters.projection.hindsight import (
    HindsightMemoryProjection,
    HindsightProjectionConfig,
    HindsightProjectionProtocolError,
)
from eva.application.memory import RecallCurrentFacts, RememberFact
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import ExperienceSourceEvent, MemoryFact


TENANT = "66a4aad5-0550-5211-8b36-398eabe5b7be"
INSTALLATION = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
PRINCIPAL = "95684f20-4ccb-5830-9a80-1d400f69730b"
SOURCE = "bd8b4729-f2b3-5c6e-bf8e-d4e0ebfcdd35"
FACT = "308563a5-7f06-5066-9fd8-825927cfed36"
OLD_FACT = "43ecfc54-af4b-5848-95ae-2fb38f1b0c42"
NOW = "2026-08-23T10:00:00Z"
RETENTION = "2027-08-23T10:00:00Z"


class _State:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.bad_recall = False


@pytest.fixture
def hindsight_server() -> Iterator[tuple[str, _State]]:
    state = _State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def _body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length)) if length else None

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self) -> None:  # noqa: N802
            body = self._body()
            state.requests.append(("PUT", self.path, body))
            self._send(200, {})

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            state.requests.append(("POST", self.path, body))
            if self.path.endswith("/memories/recall"):
                self._send(
                    200,
                    {"not_results": []}
                    if state.bad_recall
                    else {
                        "results": [
                            {
                                "document_id": FACT,
                                "metadata": {"fact_id": FACT},
                            },
                            {
                                "document_id": OLD_FACT,
                                "metadata": {"fact_id": OLD_FACT},
                            },
                        ]
                    },
                )
            else:
                self._send(200, {"success": True, "items_count": 1})

        def do_DELETE(self) -> None:  # noqa: N802
            state.requests.append(("DELETE", self.path, None))
            self._send(200, {"success": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _command() -> RememberFact:
    value = {"preference": "warm", "context": "overkapping"}
    source = ExperienceSourceEvent.model_validate(
        {
            "schema_version": 1,
            "contract": "ExperienceSourceEvent/v1",
            "source_event_id": SOURCE,
            "tenant_id": TENANT,
            "principal_id": PRINCIPAL,
            "subject_id": PRINCIPAL,
            "event_type": "preference_corrected",
            "source_kind": "direct_user_correction",
            "source_ref": "telegram:message:5001",
            "content_sha256": "1" * 64,
            "provenance_refs": ["f69ba2ff-fb79-5a35-bb56-8e91e271f651"],
            "observed_at": NOW,
            "valid_from": NOW,
            "valid_until": None,
            "ingested_at": NOW,
            "trust": "confirmed_direct_user",
            "privacy_class": "internal",
            "purpose": "conversation_continuity",
            "retention_until": RETENTION,
            "delete_sla_at": RETENTION,
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
            "fact_id": FACT,
            "tenant_id": TENANT,
            "subject_id": PRINCIPAL,
            "predicate": "preference.outdoor_temperature",
            "value": value,
            "source_event_ids": [SOURCE],
            "observed_at": NOW,
            "valid_from": NOW,
            "valid_until": None,
            "trust": "confirmed_direct_user",
            "content_sha256": canonical_sha256(value),
            "assertion_status": "active",
            "superseded_by_fact_id": None,
            "retraction_source_event_id": None,
        }
    )
    return RememberFact(
        installation_id=INSTALLATION,
        current_fact_id="877ea196-f816-54ba-9d4e-3c483607d58c",
        source_event=source,
        fact=fact,
        scope={"kind": "person", "principal_id": PRINCIPAL},
        retention_policy_id="resident-memory-v1",
        committed_at=NOW,
    )


def _query() -> RecallCurrentFacts:
    return RecallCurrentFacts(
        recall_id="recall:hindsight:5002",
        installation_id=INSTALLATION,
        tenant_id=TENANT,
        principal_id=PRINCIPAL,
        conversation_id="conversation:telegram:jaap",
        project_id=None,
        query_purpose="reply",
        requested_valid_at=NOW,
        scope={"kind": "person", "principal_id": PRINCIPAL},
        subject_ids=(PRINCIPAL,),
        query_text="Hoe wil Jaap de temperatuur onder de overkapping?",
    )


def test_projects_current_fact_and_uses_hindsight_to_rank_candidates(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    projection = HindsightMemoryProjection(HindsightProjectionConfig(base_url))

    receipt = projection.project_current(_command(), previous_fact_id=OLD_FACT)
    projected = projection.recall_candidates(_query())

    assert receipt.fact_id == FACT
    assert TENANT not in receipt.bank_id
    assert INSTALLATION not in receipt.bank_id
    assert projected.backend == "hindsight-0.9.1-v1"
    assert projected.query.candidate_fact_ids == (FACT, OLD_FACT)
    assert projected.query.projection_version == "hindsight-0.9.1-v1"

    retained = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories")
    )
    assert retained is not None
    assert retained["items"][0]["document_id"] == FACT
    assert '"preference":"warm"' in retained["items"][0]["content"]
    assert any(method == "DELETE" for method, _path, _body in state.requests)


def test_projection_outage_explicitly_falls_back_to_postgres() -> None:
    projection = HindsightMemoryProjection(
        HindsightProjectionConfig("http://127.0.0.1:1", timeout_seconds=0.1)
    )

    result = projection.recall_or_sql_fallback(_query())

    assert result.backend == "postgres-simple-v1"
    assert result.fallback_reason == "hindsight_unavailable"
    assert result.query.candidate_fact_ids is None


def test_invalid_hindsight_response_fails_closed_instead_of_falling_back(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    state.bad_recall = True
    projection = HindsightMemoryProjection(HindsightProjectionConfig(base_url))

    with pytest.raises(HindsightProjectionProtocolError, match="result list"):
        projection.recall_or_sql_fallback(_query())
