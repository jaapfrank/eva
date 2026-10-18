from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

import pytest

from eva.adapters.projection.hindsight import (
    _bounded_recall_query,
    _recall_tokenizer,
    HindsightMemoryEngine,
    HindsightMemoryScopeError,
    HindsightMemoryProjection,
    HindsightProjectionConfig,
    HindsightProjectionProtocolError,
)
from eva.application.memory import (
    ConversationMemoryDocument,
    RecallCurrentFacts,
    RememberFact,
)
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
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
        self.enforce_query_limit = False
        self.bad_recall = False
        self.anchor_recall = False
        self.bound_low_score_recall = False
        self.bound_bank_id: str | None = None
        self.source_fact_recall = False
        self.group_recall_response: dict[str, Any] | None = None
        self.ignore_source_query_update = False
        self.mental_model_queries: dict[str, str] = {}
        self.mental_models: set[str] = set()
        self.mental_model_triggers: dict[str, dict[str, Any]] = {}


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

        def do_GET(self) -> None:  # noqa: N802
            state.requests.append(("GET", self.path, None))
            if "/operations/" in self.path:
                operation_id = self.path.rsplit("/", 1)[-1]
                self._send(
                    200,
                    {"operation_id": operation_id, "status": "completed"},
                )
            elif "/mental-models/" in self.path:
                mental_model_id = self.path.split("/mental-models/", 1)[1].split(
                    "?", 1
                )[0]
                if mental_model_id not in state.mental_models:
                    self._send(404, {"detail": "not found"})
                elif self.path.endswith("?detail=metadata"):
                    # Hindsight 0.9.1 excludes configuration from metadata.
                    self._send(200, {"id": mental_model_id})
                else:
                    self._send(
                        200,
                        {
                            "content": (
                                "Jaap wil korte, directe en warme antwoorden zonder "
                                "onnodige kopjes; zijn latere correctie is leidend."
                            ),
                            "id": mental_model_id,
                            "trigger": state.mental_model_triggers[mental_model_id],
                            "source_query": state.mental_model_queries.get(mental_model_id, "legacy"),
                            "reflect_response": {
                                "based_on": {"world": [FACT], "observation": []}
                            },
                        },
                    )
            else:
                self._send(200, {"status": "healthy"})

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            state.requests.append(("POST", self.path, body))
            if self.path.endswith("/memories/recall"):
                if state.enforce_query_limit and len(_recall_tokenizer().encode(body["query"], disallowed_special=())) > 500:
                    self._send(400, {"detail": "Query too long"})
                    return
                if state.group_recall_response is not None:
                    self._send(200, state.group_recall_response)
                    return
                if state.bound_low_score_recall and body is not None:
                    if (
                        body["query"] == "Mark"
                        and state.bound_bank_id is not None
                        and f"/banks/{state.bound_bank_id}/" in self.path
                    ):
                        self._send(
                            200,
                            {
                                "results": [
                                    {
                                        "id": "bound-mark",
                                        "scores": {"final": 0.025},
                                        "text": "De persoon in deze regio is Mark.",
                                        "type": "world",
                                        "mentioned_at": NOW,
                                        "document_id": "bound-mark",
                                        "metadata": {
                                            "principal_id": PRINCIPAL,
                                            "privacy_class": "confidential",
                                            "source_ref": "telegram:photo-correction",
                                            "evidence_ref": "observation-binding:"
                                            + "a" * 64,
                                            "trust": "direct_user",
                                            "observation_binding_sha256": "a" * 64,
                                        },
                                    }
                                ],
                                "source_facts": {"anchor-source": {
                                    "mentioned_at": NOW,
                                    "metadata": {"principal_id": PRINCIPAL, "privacy_class": "internal",
                                                 "trust": "host_policy", "source_ref": "telegram:anchor",
                                                 "evidence_ref": "evidence:anchor"},
                                }},
                            },
                        )
                    else:
                        self._send(200, {"results": []})
                    return
                if state.source_fact_recall:
                    self._send(
                        200,
                        {
                            "results": [
                                {
                                    "id": "new-observation",
                                    "scores": {"final": 1.09},
                                    "text": "Het actuele codewoord is basalt.",
                                    "type": "observation",
                                    "mentioned_at": "2026-08-23T10:01:00Z",
                                    "metadata": {},
                                    "source_fact_ids": ["new-source-fact"],
                                },
                                {
                                    "id": "old-world",
                                    "scores": {"final": 1.08},
                                    "text": "Het actuele codewoord is graniet.",
                                    "type": "world",
                                    "mentioned_at": NOW,
                                    "document_id": "old-document",
                                    "metadata": {
                                        "principal_id": PRINCIPAL,
                                        "privacy_class": "internal",
                                        "source_ref": "telegram:old",
                                        "evidence_ref": "evidence:old",
                                        "trust": "direct_user",
                                    },
                                },
                            ],
                            "source_facts": {
                                "new-source-fact": {
                                    "id": "new-source-fact",
                                    "text": "Het actuele codewoord is basalt.",
                                    "type": "world",
                                    "mentioned_at": "2026-08-23T10:01:00Z",
                                    "document_id": "new-document",
                                    "metadata": {
                                        "principal_id": PRINCIPAL,
                                        "privacy_class": "internal",
                                        "source_ref": "telegram:new",
                                        "evidence_ref": "evidence:new",
                                        "trust": "direct_user",
                                    },
                                }
                            },
                            "source_facts_truncated": False,
                        },
                    )
                    return
                if state.anchor_recall and body is not None:
                    if body["query"] == "werkkamer":
                        self._send(
                            200,
                            {
                                "results": [
                                    {
                                        "id": "latest-workroom-preference",
                                        "scores": {"final": 1.08},
                                        "text": (
                                            "Jaap wil in de werkkamer alleen stille "
                                            "meldingen, zonder geluid."
                                        ),
                                        "type": "observation",
                                        "mentioned_at": NOW,
                                        "source_fact_ids": ["anchor-source"],
                                        "metadata": {},
                                    }
                                ],
                                "source_facts": {"anchor-source": {
                                    "mentioned_at": NOW,
                                    "metadata": {"principal_id": PRINCIPAL, "privacy_class": "internal",
                                                 "trust": "host_policy", "source_ref": "telegram:anchor",
                                                 "evidence_ref": "evidence:anchor"},
                                }},
                            },
                        )
                    else:
                        self._send(200, {"results": []})
                    return
                self._send(
                    200,
                    {"not_results": []}
                    if state.bad_recall
                    else {
                        "source_facts": {"style-source": {
                            "mentioned_at": NOW,
                            "metadata": {"principal_id": PRINCIPAL, "privacy_class": "internal",
                                         "trust": "host_policy", "source_ref": "telegram:conversation:jaap",
                                         "evidence_ref": "evidence:style:jaap"},
                        }},
                        "results": [
                            {
                                "id": FACT,
                                "scores": {"final": 0.91},
                                "text": "Jaap houdt van een warme overkapping.",
                                "type": "world",
                                "mentioned_at": NOW,
                                "document_id": FACT,
                                "metadata": {
                                    "fact_id": FACT,
                                    "principal_id": PRINCIPAL,
                                    "privacy_class": "internal",
                                    "source_ref": "telegram:message:5001",
                                    "evidence_ref": "evidence:message:5001",
                                    "trust": "direct_user",
                                },
                            },
                            {
                                "id": OLD_FACT,
                                "source_fact_ids": ["style-source"],
                                "scores": {"final": 0.88},
                                "text": "Jaap wil korte, directe antwoorden.",
                                "type": "observation",
                                "occurred_start": NOW,
                                "document_id": OLD_FACT,
                                "metadata": {
                                    "fact_id": OLD_FACT,
                                    "principal_id": PRINCIPAL,
                                    "privacy_class": "internal",
                                    "source_ref": "telegram:conversation:jaap",
                                    "evidence_ref": "evidence:style:jaap",
                                    "trust": "host_policy",
                                },
                            },
                            *(
                                [
                                    {
                                        "id": "irrelevant-low-score",
                                        "scores": {"final": 0.01},
                                        "text": "Een ongerelateerd project.",
                                        "type": "world",
                                        "mentioned_at": NOW,
                                        "document_id": "irrelevant-low-score",
                                        "metadata": {
                                            "principal_id": PRINCIPAL,
                                            "privacy_class": "internal",
                                            "trust": "direct_user",
                                        },
                                    }
                                ]
                                if body is not None and "types" in body
                                else []
                            ),
                        ]
                    },
                )
            elif self.path.endswith("/refresh") and "/mental-models/" in self.path:
                self._send(
                    200,
                    {
                        "operation_id": "763f8954-51f2-4d61-84b3-d11f858b7db8",
                        "status": "queued",
                    },
                )
            elif self.path.endswith("/mental-models"):
                assert body is not None
                state.mental_models.add(body["id"])
                state.mental_model_queries[body["id"]] = body["source_query"]
                state.mental_model_triggers[body["id"]] = body["trigger"]
                self._send(
                    200,
                    {
                        "mental_model_id": body["id"],
                        "operation_id": "763f8954-51f2-4d61-84b3-d11f858b7db8",
                    },
                )
            else:
                self._send(
                    200,
                    {
                        "success": True,
                        "items_count": len(body["items"]),
                        "async": True,
                        "operation_id": "60c962ab-e659-47a0-aa44-70bc60ff7f66",
                    },
                )

        def do_PATCH(self) -> None:  # noqa: N802
            body = self._body()
            state.requests.append(("PATCH", self.path, body))
            if "/mental-models/" in self.path:
                assert body is not None
                mental_model_id = self.path.rsplit("/", 1)[-1]
                state.mental_model_triggers[mental_model_id] = body["trigger"]
                if not state.ignore_source_query_update:
                    state.mental_model_queries[mental_model_id] = body["source_query"]
                self._send(
                    200,
                    {
                        "id": mental_model_id,
                        "trigger": body["trigger"],
                        "source_query": state.mental_model_queries.get(mental_model_id, "legacy"),
                    },
                )
            else:
                self._send(200, {"bank_id": self.path.split("/")[-2], "config": {}})

        def do_DELETE(self) -> None:  # noqa: N802
            state.requests.append(("DELETE", self.path, None))
            if "/mental-models/" in self.path:
                mental_model_id = self.path.rsplit("/", 1)[-1]
                if mental_model_id not in state.mental_models:
                    self._send(404, {"detail": "not found"})
                    return
                state.mental_models.remove(mental_model_id)
                self._send(200, {"status": "deleted"})
                return
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
        audience=MemoryAudience.private_person(principal_id=PRINCIPAL),
        conversation_id="conversation:telegram:jaap",
        project_id=None,
        query_purpose="reply",
        requested_valid_at=NOW,
        scope={"kind": "person", "principal_id": PRINCIPAL},
        subject_ids=(PRINCIPAL,),
        query_text="Hoe wil Jaap de temperatuur onder de overkapping?",
    )


def _conversation_document() -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        principal_id=PRINCIPAL,
        conversation_id="conversation:telegram:jaap",
        document_id="conversation:telegram:jaap:history",
        content="Jaap: Antwoord kort en direct.\nEva: Begrepen.",
        occurred_at=NOW,
        source_ref="telegram:conversation:jaap",
        evidence_ref="evidence:conversation:jaap",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id=PRINCIPAL),
    )


def _group_document(
    *, consent_ref: TelegramGroupConsentReference
) -> ConversationMemoryDocument:
    return replace(
        _conversation_document(),
        conversation_id="telegram:-100123",
        document_id="telegram:-100123:history:1",
        source_ref="telegram:-100123:message:1",
        evidence_ref="telegram:-100123:evidence:1",
        audience=MemoryAudience.telegram_group(
            principal_id=PRINCIPAL,
            group_chat_id="-100123",
            consent_ref=consent_ref,
        ),
    )


def test_historical_eva1_recall_preserves_unverified_trust_and_private_provenance(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    historical = replace(_conversation_document(), trust="legacy_unverified",
                         source_ref="eva1-memory:source:rows:0-49",
                         evidence_ref="eva1-snapshot:sha256:" + "a" * 64,
                         content="Historisch Eva1-geheugen, onbevestigd. De eerdere voorkeur was stilte.")
    engine.retain_conversation(historical)
    retained = next(body["items"][0] for method,path,body in state.requests
                    if method == "POST" and path.endswith("/memories"))
    assert retained["metadata"]["trust"] == "legacy_unverified"
    state.group_recall_response = {"results": [{
        "id": "eva1-source-memory", "text": "De eerdere voorkeur was stilte.",
        "type": "world", "scores": {"final": 1.0}, "mentioned_at": NOW,
        "metadata": retained["metadata"],
    }]}
    recalled = engine.recall_for_presence(_query())
    assert recalled.recall.selected[0].trust == "legacy_unverified"
    assert recalled.confirmed_facts[0].value["semantic_trust"] == "legacy_unverified"
    assert recalled.confirmed_facts[0].source_ref == historical.source_ref
    assert recalled.confirmed_facts[0].evidence_ref == historical.evidence_ref
    assert recalled.confirmed_facts[0].trust == "host_policy"
    state.group_recall_response["results"][0]["metadata"]["principal_id"] = "other-person"
    with pytest.raises(HindsightMemoryScopeError):
        engine.recall_for_presence(_query())


def test_native_memory_batch_keeps_each_source_and_refuses_mixed_private_people(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    first = replace(_conversation_document(), document_id="eva1:rows:0-249", trust="legacy_unverified")
    second = replace(first, document_id="eva1:rows:250-499", content="Another historical source")
    receipts = engine.retain_conversations((first, second))
    writes = [body for method,path,body in state.requests if method == "POST" and path.endswith("/memories")]
    assert len(writes) == 1 and len(writes[0]["items"]) == 2
    assert [r.document_id for r in receipts] == [first.document_id, second.document_id]
    assert all(item["metadata"]["trust"] == "legacy_unverified" for item in writes[0]["items"])
    other = replace(second, principal_id="other-person", audience=MemoryAudience.private_person(principal_id="other-person"))
    before = len(state.requests)
    with pytest.raises(HindsightMemoryScopeError):
        engine.retain_conversations((first, other))
    assert len(state.requests) == before
    with pytest.raises(HindsightMemoryScopeError):
        engine.retain_conversations((first, first))


def test_hindsight_owns_conversation_memory_and_style_observations(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    original = {"principal_id": PRINCIPAL, "source_ref": "telegram:conversation:jaap",
        "evidence_ref": "evidence:style:jaap", "project_id": None, "occurred_at": NOW,
        "text": "Keep answers brief and direct.", "truncated": False}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
        private_source_resolver=lambda _q, _r: (original,))

    receipt = engine.retain_conversation(_conversation_document())
    memory = engine.recall_for_presence(_query())

    assert receipt.document_id == "conversation:telegram:jaap:history"
    assert [fact.predicate for fact in memory.confirmed_facts] == [
        "memory.world",
        "memory.observation",
    ]
    assert memory.confirmed_facts[0].value["text"] == (
        "Jaap houdt van een warme overkapping."
    )
    assert memory.confirmed_facts[1].trust == "host_policy"
    assert memory.confirmed_facts[0].value["source_observed_at"] == NOW
    assert memory.confirmed_facts[1].value["source_observed_at"] is None
    assert memory.recall.projection_version == "hindsight-0.9.1-v1"

    bank = next(body for method, _path, body in state.requests if method == "PUT")
    assert bank is not None
    assert "brevity" in bank["observations_mission"]
    assert "question, hypothetical" in bank["retain_mission"]
    retained = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories")
    )
    assert retained is not None
    item = retained["items"][0]
    assert item["content"] == _conversation_document().content
    assert item["document_id"] == _conversation_document().document_id
    assert item["update_mode"] == "replace"
    assert item["observation_scopes"][0][0].startswith("eva-person-")
    assert retained["async"] is True


def test_private_recall_includes_memory_on_first_turn_and_reuses_same_question(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    query = _query()

    cold = engine.recall_for_presence_fast(query)
    semantic_requests = len(state.requests)
    warm = engine.recall_for_presence_fast(query)
    assert cold.confirmed_facts
    assert warm == cold  # Current source enrichment need not preserve object identity.
    assert len(state.requests) == semantic_requests
    changed = replace(query, query_text="Een andere vraag over persoonlijke voorkeuren")
    assert engine._fast_presence_key(changed) != engine._fast_presence_key(query)
    assert engine.recall_for_presence_fast(changed) is not cold


@pytest.mark.parametrize("changed_field", ["content", "occurred_at", "principal_id", "installation_id"])
def test_retain_operation_tracks_exact_source_revision_and_scope(
    hindsight_server: tuple[str, _State], changed_field: str,
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    document = _conversation_document()
    engine.retain_conversation(document)
    engine.retain_conversation(document)
    updates = {
        "content": "Jaap: Mijn voorkeur is veranderd; voortaan liever uitgebreid.",
        "occurred_at": "2026-08-24T10:00:00Z",
        "principal_id": SOURCE,
        "installation_id": SOURCE,
    }
    changes = {changed_field: updates[changed_field]}
    if changed_field == "principal_id":
        changes["audience"] = MemoryAudience.private_person(principal_id=SOURCE)
    changed = replace(document, **changes)
    engine.retain_conversation(changed)
    # A fresh adapter must derive the same retry identity without a process cache.
    HindsightMemoryEngine(HindsightProjectionConfig(base_url)).retain_conversation(changed)
    requests = [body for method, path, body in state.requests
                if method == "POST" and path.endswith("/memories")]
    ids = [body["operation_id"] for body in requests]
    assert ids[0] == ids[1]
    assert ids[0] != ids[2]
    assert ids[2] == ids[3]


def test_retained_correction_discards_stale_context_and_fences_inflight_recall(
    hindsight_server: tuple[str, _State], monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, _state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    query = _query()
    key = engine._fast_presence_key(query)
    memory = engine.recall_for_presence(query)
    engine._fast_presence_cache[key] = (time.monotonic(), memory)
    engine._fast_presence_cache["other-person"] = (time.monotonic(), memory)
    generation = engine._fast_presence_generation
    engine.retain_conversation(_conversation_document())
    assert key not in engine._fast_presence_cache
    assert "other-person" in engine._fast_presence_cache
    assert engine._fast_presence_generation > generation
    def recall_during_correction(_query):
        engine._fast_presence_generation += 1
        return memory
    monkeypatch.setattr(engine, "recall_for_presence", recall_during_correction)
    engine.recall_for_presence_fast(query)
    assert key not in engine._fast_presence_cache


def test_hindsight_retention_deadline_and_exact_document_removal(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    document = replace(_conversation_document(), retention_until="2027-08-23T14:00:00Z")

    receipt = engine.retain_conversation(document)
    principal_sha256 = hashlib.sha256(document.principal_id.encode("utf-8")).hexdigest()
    mental_model_id = f"eva-style-mirror-v1-{principal_sha256[:16]}"
    state.mental_models.add(mental_model_id)
    engine._fast_presence_cache["stale"] = (  # noqa: SLF001
        time.monotonic(),
        engine._empty_presence(_query()),  # noqa: SLF001
    )
    cache_generation = engine._fast_presence_generation  # noqa: SLF001
    engine.remove_conversation_document(
        bank_id=receipt.bank_id,
        document_id=document.document_id,
        principal_sha256=principal_sha256,
    )

    retained = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories")
    )
    assert retained is not None
    assert retained["items"][0]["metadata"]["retention_until"] == "2027-08-23T14:00:00Z"
    assert any(
        method == "DELETE"
        and path.endswith("/documents/conversation%3Atelegram%3Ajaap%3Ahistory")
        for method, path, _body in state.requests
    )
    assert mental_model_id not in state.mental_models
    assert any(
        method == "DELETE" and path.endswith(f"/mental-models/{mental_model_id}")
        for method, path, _body in state.requests
    )
    assert engine._fast_presence_cache == {}  # noqa: SLF001
    assert engine._fast_presence_generation == cache_generation + 1  # noqa: SLF001

    # A crash after either provider delete can replay both exact DELETEs safely.
    engine.remove_conversation_document(
        bank_id=receipt.bank_id,
        document_id=document.document_id,
        principal_sha256=principal_sha256,
    )


def test_hindsight_document_removal_rejects_an_unbound_derivative_scope(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))

    with pytest.raises(
        HindsightMemoryScopeError,
        match="derivative principal binding is invalid",
    ):
        engine.remove_conversation_document(
            bank_id="eva2-" + "a" * 32,
            document_id="conversation:1",
            principal_sha256="not-a-hash",
        )

    assert state.requests == []


def test_private_hindsight_memory_never_calls_the_group_consent_resolver(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, _state = hindsight_server
    calls = 0

    class Resolver:
        def active_reference(self, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            raise AssertionError("private memory must not resolve group consent")

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )

    engine.retain_conversation(_conversation_document())
    engine.recall_for_presence(_query())

    assert calls == 0


def test_hindsight_rejects_a_group_document_until_group_tagging_is_wired(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    group_document = _group_document(
        consent_ref=TelegramGroupConsentReference(
            principal_id=PRINCIPAL,
            group_chat_id="-100123",
            operation_id="telegram-group-consent:41",
        )
    )

    with pytest.raises(HindsightMemoryScopeError, match="no active consent resolver"):
        engine.retain_conversation(group_document)
    assert state.requests == []


def test_hindsight_retain_uses_only_current_group_tag_and_metadata(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    consent_ref = TelegramGroupConsentReference(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        operation_id="telegram-group-consent:42",
    )
    calls: list[tuple[str, str]] = []

    class Resolver:
        def active_reference(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
            calls.append((principal_id, group_chat_id))
            return consent_ref

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )
    group_document = _group_document(consent_ref=consent_ref)

    engine.retain_conversation(group_document)

    retained = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories")
    )
    assert retained is not None
    item = retained["items"][0]
    group_tag = HindsightMemoryEngine._group_tag("-100123")
    assert item["tags"] == ["eva-memory", group_tag]
    assert item["observation_scopes"] == [[group_tag]]
    assert HindsightMemoryEngine._person_tag(PRINCIPAL) not in item["tags"]
    assert item["metadata"] == {
        "audience_id": "telegram-group:-100123",
        "audience_kind": "telegram_group",
        "consent_operation_id": "telegram-group-consent:42",
        "conversation_id": "telegram:-100123",
        "evidence_ref": "telegram:-100123:evidence:1",
        "group_chat_id": "-100123",
        "installation_id": INSTALLATION,
        "principal_id": PRINCIPAL,
        "privacy_class": "internal",
        "source_ref": "telegram:-100123:message:1",
        "trust": "direct_user",
    }
    assert calls == [(PRINCIPAL, "-100123")]


@pytest.mark.parametrize("active", [None, "other-operation"])
def test_hindsight_retain_rejects_revoked_or_replaced_group_consent_before_http(
    hindsight_server: tuple[str, _State], active: str | None
) -> None:
    base_url, state = hindsight_server
    consent_ref = TelegramGroupConsentReference(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        operation_id="telegram-group-consent:43",
    )

    class Resolver:
        def active_reference(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
            if active is None:
                return None
            return TelegramGroupConsentReference(
                principal_id=principal_id,
                group_chat_id=group_chat_id,
                operation_id=f"telegram-group-consent:{active}",
            )

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )
    group_document = _group_document(consent_ref=consent_ref)

    with pytest.raises(HindsightMemoryScopeError, match="not active and exact"):
        engine.retain_conversation(group_document)
    assert state.requests == []


def test_hindsight_retain_rejects_a_group_document_with_private_evidence_binding(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    consent_ref = TelegramGroupConsentReference(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        operation_id="telegram-group-consent:44",
    )

    class Resolver:
        def active_reference(self, **_kwargs):  # type: ignore[no-untyped-def]
            return consent_ref

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )
    document = replace(
        _group_document(consent_ref=consent_ref),
        evidence_ref="observation-binding:" + "a" * 64,
    )

    with pytest.raises(HindsightMemoryScopeError, match="not bound to its group"):
        engine.retain_conversation(document)
    assert state.requests == []


def test_hindsight_rejects_group_recall_without_private_or_sql_fallback(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    audience = MemoryAudience.telegram_group(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        consent_ref=TelegramGroupConsentReference(
            principal_id=PRINCIPAL,
            group_chat_id="-100123",
            operation_id="telegram-group-consent:31",
        ),
    )
    query = replace(_query(), audience=audience, conversation_id="telegram:-100123")

    with pytest.raises(HindsightMemoryScopeError, match="no active consent resolver"):
        engine.recall_for_presence(query)
    with pytest.raises(HindsightMemoryScopeError, match="not wired"):
        engine.recall_or_sql_fallback(query)

    assert state.requests == []


def test_hindsight_group_recall_uses_only_group_tag_and_no_style_mirror(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    consent_ref = TelegramGroupConsentReference(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        operation_id="telegram-group-consent:51",
    )
    state.group_recall_response = {
        "results": [
            {
                "id": "group-memory-1",
                "scores": {"final": 0.91},
                "text": "De barbecue begint om zes uur.",
                "type": "world",
                "mentioned_at": NOW,
                "document_id": "telegram:-100123:history:1",
                "metadata": {
                    "audience_id": "telegram-group:-100123",
                    "audience_kind": "telegram_group",
                    "consent_operation_id": consent_ref.operation_id,
                    "conversation_id": "telegram:-100123",
                    "group_chat_id": "-100123",
                    "principal_id": PRINCIPAL,
                    "privacy_class": "internal",
                    "source_ref": "telegram:-100123:message:1",
                    "evidence_ref": "telegram:-100123:evidence:1",
                    "trust": "direct_user",
                },
            }
        ]
    }

    class Resolver:
        def active_reference(self, **_kwargs):  # type: ignore[no-untyped-def]
            return consent_ref

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )
    query = replace(
        _query(),
        audience=MemoryAudience.telegram_group(
            principal_id=PRINCIPAL,
            group_chat_id="-100123",
            consent_ref=consent_ref,
        ),
        conversation_id="telegram:-100123",
        query_text="bbq",
        scope={"kind": "telegram_group", "group_chat_id": "-100123"},
    )

    memory = engine.recall_for_presence(query)

    recall_request = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories/recall")
    )
    assert recall_request is not None
    assert recall_request["tags"] == [HindsightMemoryEngine._group_tag("-100123")]
    assert HindsightMemoryEngine._person_tag(PRINCIPAL) not in recall_request["tags"]
    assert [fact.value["text"] for fact in memory.confirmed_facts] == [
        "De barbecue begint om zes uur."
    ]
    assert memory.style_mirror is None
    assert not any("/mental-models/" in path for _method, path, _body in state.requests)


def test_hindsight_group_recall_excludes_result_after_consent_is_revoked(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    consent_ref = TelegramGroupConsentReference(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        operation_id="telegram-group-consent:52",
    )
    state.group_recall_response = {
        "results": [
            {
                "id": "revoked-group-memory",
                "scores": {"final": 0.91},
                "text": "Dit mag na intrekking niet verschijnen.",
                "type": "world",
                "mentioned_at": NOW,
                "metadata": {
                    "audience_id": "telegram-group:-100123",
                    "audience_kind": "telegram_group",
                    "consent_operation_id": consent_ref.operation_id,
                    "conversation_id": "telegram:-100123",
                    "evidence_ref": "telegram:-100123:evidence:2",
                    "group_chat_id": "-100123",
                    "principal_id": PRINCIPAL,
                    "privacy_class": "internal",
                    "source_ref": "telegram:-100123:message:2",
                    "trust": "direct_user",
                },
            }
        ]
    }
    calls = 0

    class Resolver:
        def active_reference(self, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return consent_ref if calls == 1 else None

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )
    query = replace(
        _query(),
        audience=MemoryAudience.telegram_group(
            principal_id=PRINCIPAL,
            group_chat_id="-100123",
            consent_ref=consent_ref,
        ),
        conversation_id="telegram:-100123",
        query_text="bbq",
        scope={"kind": "telegram_group", "group_chat_id": "-100123"},
    )

    memory = engine.recall_for_presence(query)

    assert memory.confirmed_facts == ()
    assert memory.style_mirror is None
    assert not any(method == "DELETE" for method, _path, _body in state.requests)


def test_hindsight_group_recall_stops_before_http_when_caller_consent_is_revoked(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    consent_ref = TelegramGroupConsentReference(
        principal_id=PRINCIPAL,
        group_chat_id="-100123",
        operation_id="telegram-group-consent:53",
    )

    class Resolver:
        def active_reference(self, **_kwargs):  # type: ignore[no-untyped-def]
            return None

    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(base_url), active_group_consent_resolver=Resolver()
    )
    query = replace(
        _query(),
        audience=MemoryAudience.telegram_group(
            principal_id=PRINCIPAL,
            group_chat_id="-100123",
            consent_ref=consent_ref,
        ),
        conversation_id="telegram:-100123",
        query_text="bbq",
        scope={"kind": "telegram_group", "group_chat_id": "-100123"},
    )

    memory = engine.recall_for_presence(query)

    assert memory.confirmed_facts == ()
    assert memory.style_mirror is None
    assert state.requests == []


def test_project_memory_uses_a_separate_bank_while_recall_keeps_person_context(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    global_document = _conversation_document()
    project_document = replace(
        global_document,
        document_id="conversation:project-red:history",
        project_id="project-red",
    )

    engine.retain_conversation(global_document)
    engine.retain_conversation(project_document)

    retain_paths = [
        path
        for method, path, _body in state.requests
        if method == "POST" and path.endswith("/memories")
    ]
    assert len(retain_paths) == 2
    assert len(set(retain_paths)) == 2
    project_body = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path == retain_paths[1]
    )
    assert project_body["items"][0]["metadata"]["project_id"] == "project-red"

    state.requests.clear()
    engine.recall_for_presence(
        replace(
            _query(),
            project_id="project-red",
            scope={
                "kind": "person_project",
                "principal_id": PRINCIPAL,
                "project_id": "project-red",
            },
        )
    )
    recall_banks = {
        path.split("/banks/", 1)[1].split("/memories/recall", 1)[0]
        for method, path, _body in state.requests
        if method == "POST" and path.endswith("/memories/recall")
    }
    assert len(recall_banks) == 2


def test_bound_photo_identity_uses_an_exact_observation_bank(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    binding_sha256 = "a" * 64
    state.bound_bank_id = engine._bank_id(  # noqa: SLF001
        TENANT,
        engine._observation_partition(INSTALLATION, binding_sha256),  # noqa: SLF001
    )
    document = replace(
        _conversation_document(),
        document_id="conversation:photo:identity-correction",
        evidence_ref=f"observation-binding:{binding_sha256}",
        privacy_class="confidential",
    )

    receipt = engine.retain_conversation(document)

    retained = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories")
    )
    assert (
        retained["items"][0]["metadata"]["observation_binding_sha256"] == binding_sha256
    )
    assert receipt.bank_id != engine._bank_id(TENANT, INSTALLATION)  # noqa: SLF001

    state.requests.clear()
    engine.recall_for_presence(
        replace(
            _query(),
            scope={
                "kind": "person",
                "observation_binding_sha256": binding_sha256,
                "principal_id": PRINCIPAL,
            },
        )
    )
    recall_banks = {
        path.split("/banks/", 1)[1].split("/memories/recall", 1)[0]
        for method, path, _body in state.requests
        if method == "POST" and path.endswith("/memories/recall")
    }
    assert receipt.bank_id in recall_banks
    assert len(recall_banks) == 2


def test_bound_photo_bank_admits_low_proper_name_score_without_widening_scope(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    state.bound_low_score_recall = True
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    binding_sha256 = "a" * 64
    state.bound_bank_id = engine._bank_id(  # noqa: SLF001
        TENANT,
        engine._observation_partition(INSTALLATION, binding_sha256),  # noqa: SLF001
    )

    recalled = engine.recall_for_presence(
        replace(
            _query(),
            query_text="Wie staat hier?\nGebonden retrieval-ankers: Mark",
            scope={
                "kind": "person",
                "observation_binding_sha256": binding_sha256,
                "principal_id": PRINCIPAL,
            },
        )
    )

    assert [fact.fact_id for fact in recalled.confirmed_facts] == [
        "hindsight:bound-mark"
    ]


def test_direct_recall_rejects_scope_widening(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, _state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    query = _query()
    query = replace(query, subject_ids=("someone-else",))

    with pytest.raises(HindsightMemoryScopeError, match="admitted principal"):
        engine.recall_for_presence(query)


def test_hot_path_recall_is_low_budget_and_never_calls_reflect(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))

    engine.recall_for_presence(_query())

    request = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories/recall")
    )
    assert request is not None
    assert request["budget"] == "low"
    assert request["max_tokens"] == 2048
    assert request["include"]["source_facts"] == {
        "max_tokens": 512,
        "max_tokens_per_observation": 256,
    }
    bank_budget = next(
        body
        for method, path, body in state.requests
        if method == "PATCH" and path.endswith("/config")
    )
    assert bank_budget == {
        "updates": {
            "recall_budget_fixed_low": 40,
            "recall_budget_fixed_mid": 40,
            "recall_budget_fixed_high": 40,
            "recall_budget_function": "fixed",
        }
    }
    assert not any(path.endswith("/reflect") for _method, path, _body in state.requests)


def test_hindsight_config_applies_bounded_recall_settings_to_both_paths(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(
        HindsightProjectionConfig(
            base_url,
            recall_budget="mid",
            recall_max_tokens=3000,
            recall_node_budget=25,
        )
    )

    engine.recall_for_presence(_query())
    engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)

    recall = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories/recall")
    )
    bank_budget = next(
        body
        for method, path, body in state.requests
        if method == "PATCH" and path.endswith("/config")
    )
    reflection = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/mental-models")
    )

    assert recall["budget"] == "mid"
    assert recall["max_tokens"] == 3000
    assert bank_budget == {
        "updates": {
            "recall_budget_fixed_low": 25,
            "recall_budget_fixed_mid": 25,
            "recall_budget_fixed_high": 25,
            "recall_budget_function": "fixed",
        }
    }
    assert reflection["trigger"]["recall_max_tokens"] == 3000


def test_hot_path_recovers_a_hidden_preference_through_a_neutral_word_anchor(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    state.anchor_recall = True
    original = {"principal_id": PRINCIPAL, "source_ref": "telegram:anchor", "evidence_ref": "evidence:anchor",
        "project_id": None, "occurred_at": NOW, "text": "Only silent notifications in my workroom.", "truncated": False}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
        private_source_resolver=lambda _q, _r: (original,))
    query = replace(
        _query(),
        query_text=(
            "Ik ben bezig en wil er niet uit gehaald worden. Hoe pak je dat vanuit "
            "de werkkamer aan?"
        ),
    )

    memory = engine.recall_for_presence(query)

    assert memory.confirmed_facts[0].value["text"] == (
        "Jaap wil in de werkkamer alleen stille meldingen, zonder geluid."
    )
    requests = [
        body["query"]
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/memories/recall") and body is not None
    ]
    assert query.query_text in requests
    assert "werkkamer" in requests


def test_observation_inherits_authority_from_complete_bounded_source_facts(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    state.source_fact_recall = True
    original = {"principal_id": PRINCIPAL, "source_ref": "telegram:new", "evidence_ref": "evidence:new",
        "project_id": None, "occurred_at": "2026-08-23T10:01:00Z", "text": "The new codeword is basalt.", "truncated": False}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
        private_source_resolver=lambda _q, _r: (original,))

    memory = engine.recall_for_presence(_query())

    newest, older = memory.confirmed_facts
    assert newest.value["text"] == "Het actuele codewoord is basalt."
    assert newest.observed_at == "2026-08-23T10:01:00Z"
    assert newest.trust == "host_policy"
    assert newest.value["semantic_trust"] == "model_inference_unconfirmed"
    assert newest.source_ref == "telegram:new"
    assert older.value["text"] == "Het actuele codewoord is graniet."
    assert older.observed_at == NOW


def test_model_planned_synonyms_become_independent_recall_queries() -> None:
    variants = HindsightMemoryEngine._recall_query_variants(
        "Welke temperatuur wil ik onder de overkapping?\n"
        "Semantische zoekconcepten: temperatuur, buitenterras, veranda, overkapping"
    )

    assert variants[0].startswith("Welke temperatuur")
    assert "buitenterras" in variants
    assert "veranda" in variants
    assert "overkapping" in variants

    bound = HindsightMemoryEngine._recall_query_variants(
        "Wie staat hier?\nGebonden retrieval-ankers: Mark"
    )
    assert bound == (
        "Wie staat hier?\nGebonden retrieval-ankers: Mark",
        "Mark",
    )


@pytest.mark.parametrize("text", [
    "Wat heb je vandaag gedaan?", "En wat heb je gisteren gedaan?",
    "Wat hadden jullie eigenlijk kunnen doen?",
    "Wacht even, ik bedoelde iets anders. Hoe gaat het met je?",
    "Vertel eens, wat herinner je je van gisteren?",
    "Ik begrijp het. Wat bedoelde je daarmee?",
])
def test_implicit_recall_keeps_grammatical_context_without_topicless_expansion(
    hindsight_server, text,
):
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    result = engine.recall_for_presence(replace(_query(), query_text=text))
    requests = [body for method, path, body in state.requests
                if method == "POST" and path.endswith("/memories/recall")]
    assert [body["query"] for body in requests] == [text]
    assert result.confirmed_facts  # Full scoped semantic recall still runs.


def test_implicit_recall_preserves_named_topics_and_explicit_time_concepts():
    text = "Wat heb je gisteren in Apeldoorn aan de buitenverlichting gedaan?"
    assert HindsightMemoryEngine._recall_query_variants(text) == (
        text, "buitenverlichting", "Apeldoorn",
    )
    for marker in ("Gebonden retrieval-ankers:", "Semantische zoekconcepten:"):
        explicit = f"Zoek dit op.\n{marker} gisteren, gedaan"
        assert HindsightMemoryEngine._recall_query_variants(explicit) == (
            explicit, "gisteren", "gedaan",
        )


def test_reflection_has_no_unmanaged_schedule_and_refreshes_only_on_request(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))

    receipt = engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
    repeated = engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)

    assert receipt.disposition == "created"
    assert repeated.disposition == "refreshed"
    request = next(
        body
        for method, path, body in state.requests
        if method == "POST" and path.endswith("/mental-models")
    )
    assert request is not None
    assert request["trigger"]["mode"] == "delta"
    assert request["trigger"]["refresh_cron"] is None
    assert request["trigger"]["recall_max_tokens"] == 2048
    assert request["trigger"]["refresh_after_consolidation"] is False
    assert request["max_tokens"] == 320
    assert "maximaal 100" in request["source_query"]
    assert (
        sum(
            method == "POST" and path.endswith("/refresh")
            for method, path, _body in state.requests
        )
        == 1
    )
    assert engine.recall_for_presence(_query()).style_mirror == (
        "Jaap wil korte, directe en warme antwoorden zonder onnodige kopjes; "
        "zijn latere correctie is leidend."
    )
    assert not any(path.endswith("/reflect") for _method, path, _body in state.requests)


def test_reflection_removes_an_existing_unmanaged_cron_before_refresh(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    mental_model_id = engine._style_mirror_id(PRINCIPAL)  # noqa: SLF001
    state.mental_models.add(mental_model_id)
    state.mental_model_triggers[mental_model_id] = {
        "exclude_mental_models": True,
        "fact_types": ["world", "experience", "observation"],
        "include_chunks": False,
        "keep_trace": True,
        "mode": "delta",
        "recall_chunks_max_tokens": 512,
        "recall_max_tokens": 2048,
        "refresh_after_consolidation": False,
        "refresh_cron": "0 3 * * *",
        "tags_match": "all_strict",
    }

    receipt = engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)

    assert receipt.disposition == "refreshed"
    patch_request = next(
        body
        for method, path, body in state.requests
        if method == "PATCH" and "/mental-models/" in path
    )
    assert patch_request is not None
    assert patch_request["trigger"]["refresh_cron"] is None
    patch_index = next(
        index
        for index, (method, path, _body) in enumerate(state.requests)
        if method == "PATCH" and "/mental-models/" in path
    )
    refresh_index = next(
        index
        for index, (method, path, _body) in enumerate(state.requests)
        if method == "POST" and path.endswith("/refresh")
    )
    assert patch_index < refresh_index


@pytest.mark.parametrize("accept_update", [True, False])
def test_reflection_upgrades_legacy_query_before_refresh_and_requires_acknowledgment(
    hindsight_server: tuple[str, _State], accept_update: bool,
) -> None:
    base_url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
    model_id = engine._style_mirror_id(PRINCIPAL)  # noqa: SLF001
    current_query = state.mental_model_queries[model_id]
    state.mental_model_queries[model_id] = "Begin altijd met De gebruiker wil"
    state.ignore_source_query_update = not accept_update
    state.requests.clear()

    if accept_update:
        assert engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL).disposition == "refreshed"
        assert state.mental_model_queries[model_id] == current_query
        changes = [method for method, path, _ in state.requests if "/mental-models/" in path]
        assert changes.index("PATCH") < changes.index("POST")
        state.requests.clear()
        engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
        assert not any(method == "PATCH" for method, _, _ in state.requests)
    else:
        with pytest.raises(HindsightProjectionProtocolError, match="reflection configuration"):
            engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
        assert not any(path.endswith("/refresh") for _, path, _ in state.requests)


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



def _household_memory_fixture(base_url, state, household_id="home"):
    from eva.application.memory_audience import HouseholdConsentReference
    actor = "household-device:" + "a" * 32
    other = "household-device:" + "c" * 32
    grants = {p: HouseholdConsentReference(p, household_id, "household-consent:" + str(i))
              for i, p in enumerate((actor, other))}
    audience = MemoryAudience.household(principal_id=actor, household_id=household_id, consent_ref=grants[actor])
    conversation = f"household:{household_id}:conversation:room"
    doc = replace(_conversation_document(), principal_id=actor, audience=audience,
                  conversation_id=conversation, document_id=conversation + ":document:1",
                  source_ref=conversation + ":message:1", evidence_ref=conversation + ":evidence:1")
    query = replace(_query(), principal_id=actor, subject_ids=(actor,), audience=audience,
                    conversation_id=conversation, scope={"kind": "household", "household_id": household_id})
    class Resolver:
        def active_reference(self, *, principal_id, household_id, now):
            assert household_id == audience.household_id
            return grants.get(principal_id)
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
                                  active_household_consent_resolver=Resolver())
    source_conversation = f"household:{household_id}:conversation:garden"
    metadata = {"audience_kind": "household", "audience_id": f"household:{household_id}",
                "household_id": household_id, "principal_id": other,
                "consent_operation_id": grants[other].operation_id,
                "conversation_id": source_conversation,
                "source_ref": source_conversation + ":message:2",
                "evidence_ref": source_conversation + ":evidence:2",
                "trust": "direct_user", "privacy_class": "internal"}
    state.group_recall_response = {"results": [{"id": "household-memory", "scores": {"final": 0.95},
        "text": "De barbecue begint om zes uur.", "type": "world", "mentioned_at": NOW,
        "document_id": source_conversation + ":document:2", "metadata": metadata}]}
    return engine, doc, query, grants, metadata


def test_household_memory_is_shared_between_admitted_devices_without_private_style(hindsight_server):
    base_url, state = hindsight_server
    engine, doc, query, grants, metadata = _household_memory_fixture(base_url, state)
    engine.retain_conversation(doc)
    result = engine.recall_for_presence(query)
    assert [fact.value["text"] for fact in result.confirmed_facts] == ["De barbecue begint om zes uur."]
    assert result.style_mirror is None
    assert not any("/mental-models/" in path for _, path, _ in state.requests)
    retained = next(body for method, path, body in state.requests if method == "POST" and path.endswith("/memories"))
    item = retained["items"][0]
    assert item["tags"] == ["eva-memory", engine._household_tag("home")]
    assert item["metadata"]["audience_kind"] == "household"
    for method, path, body in state.requests:
        if path.endswith("/memories/recall"):
            assert body["tags"] == [engine._household_tag("home")]
    grants.pop(metadata["principal_id"])
    assert engine.recall_for_presence(query).confirmed_facts == ()
    grants.clear()
    state.requests.clear()
    assert engine.recall_for_presence(query).confirmed_facts == ()
    with pytest.raises(HindsightMemoryScopeError, match="not active and exact"):
        engine.retain_conversation(doc)
    assert not state.requests


@pytest.mark.parametrize("field,value", [
    ("audience_kind", "private_person"), ("audience_id", "household:neighbours"),
    ("household_id", "neighbours"), ("source_ref", "telegram:101:private"),
    ("evidence_ref", "conversation-reply:private"), ("conversation_id", "private:1"),
    ("consent_operation_id", "telegram-group-consent:1"),
])
def test_household_recall_rejects_cross_scope_provider_results(hindsight_server, field, value):
    base_url, state = hindsight_server
    engine, doc, query, grants, metadata = _household_memory_fixture(base_url, state)
    metadata[field] = value
    with pytest.raises(HindsightMemoryScopeError):
        engine.recall_for_presence(query)


def test_household_memory_has_no_unconfigured_private_fallback(hindsight_server):
    base_url, state = hindsight_server
    _, doc, query, _, _ = _household_memory_fixture(base_url, state)
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    with pytest.raises(HindsightMemoryScopeError, match="no active mandate resolver"):
        engine.retain_conversation(doc)
    with pytest.raises(HindsightMemoryScopeError, match="no active mandate resolver"):
        engine.recall_for_presence(query)
    assert not state.requests
    for change in ({"scope": {"kind": "person", "principal_id": query.principal_id}},
                   {"conversation_id": "telegram:101"}, {"project_id": "private-project"}):
        with pytest.raises(ValueError):
            replace(query, **change)
    with pytest.raises(ValueError):
        replace(doc, source_ref="telegram:101:private")


@pytest.mark.parametrize("memory_type", ["world", "observation"])
@pytest.mark.parametrize("long_source", [False, True])
def test_existing_long_household_references_remain_traceable(hindsight_server, memory_type, long_source):
    base_url, state = hindsight_server
    engine, _, query, grants, metadata = _household_memory_fixture(base_url, state, "eva-family")
    conversation = "household:eva-family:conversation:" + "a" * 64
    message = conversation + ":request:" + "b" * 32
    metadata.update(conversation_id=conversation,
                    source_ref=conversation + ":exchange:" + message if long_source else message,
                    evidence_ref=conversation + ":evidence:" + message)
    original = dict(metadata)
    assert len(original["evidence_ref"]) == 247
    item = state.group_recall_response["results"][0]
    item["type"] = memory_type
    if memory_type == "observation":
        item["source_fact_ids"] = ["source-1"]
        state.group_recall_response["source_facts"] = {
            "source-1": {"metadata": metadata, "mentioned_at": NOW}}
    result = engine.recall_for_presence_fast(query)
    fact = result.confirmed_facts[0]
    assert fact.value["text"] == "De barbecue begint om zes uur."
    for key in ("source_ref", "evidence_ref"):
        assert len(getattr(fact, key)) <= 192
        if len(original[key]) > 192:
            assert fact.value["reference_provenance"][key] == original[key]
        else:
            assert getattr(fact, key) == original[key]
            assert key not in fact.value["reference_provenance"]
    assert result.recall.selected[0].source_event_ids == [fact.source_ref]
    again = engine.recall_for_presence_fast(query).confirmed_facts[0]
    assert (again.source_ref, again.evidence_ref) == (fact.source_ref, fact.evidence_ref)
    assert metadata == original  # No stored reference is rewritten.
    grants.pop(metadata["principal_id"])
    assert engine.recall_for_presence_fast(query).confirmed_facts == ()


def test_private_short_references_remain_unchanged(hindsight_server):
    base_url, _ = hindsight_server
    result = HindsightMemoryEngine(HindsightProjectionConfig(base_url)).recall_for_presence(_query())
    fact = result.confirmed_facts[0]
    assert fact.source_ref == "telegram:message:5001"
    assert fact.evidence_ref == "evidence:message:5001"
    assert "reference_provenance" not in fact.value



@pytest.mark.parametrize("corruption", ["private", "other_household", "old_mandate", "bad_provenance", "missing", "truncated_missing", "truncated_complete", "injected", None])
def test_household_observations_require_every_source_authority(hindsight_server, corruption):
    base_url, state = hindsight_server
    engine, doc, query, grants, metadata = _household_memory_fixture(base_url, state)
    good = dict(metadata)
    earlier = {**good, "source_ref": good["source_ref"] + ":earlier",
               "evidence_ref": good["evidence_ref"] + ":earlier"}
    if corruption == "private":
        earlier.update(audience_kind="private_person", audience_id="person:private")
    elif corruption == "other_household":
        earlier.update(household_id="neighbours", audience_id="household:neighbours")
    elif corruption == "old_mandate":
        earlier["consent_operation_id"] = "household-consent:expired"
    elif corruption == "bad_provenance":
        earlier["evidence_ref"] = "private:1"
    source_facts = {"one": {"metadata": earlier, "mentioned_at": "2026-08-22T10:00:00Z"},
                    "two": {"metadata": good, "mentioned_at": NOW}}
    if corruption in {"missing", "truncated_missing", "injected"}:
        source_facts.pop("one")
    state.group_recall_response = {
        "results": [{"id": "derived", "scores": {"final": 0.95}, "type": "observation",
                     "text": "A combined conclusion", "mentioned_at": NOW,
                     "source_fact_ids": ["one", "two"], "metadata": good,
                     "_eva_source_metadata": good}],
        "source_facts": source_facts,
        "source_facts_truncated": corruption in {"truncated_missing", "truncated_complete"},
    }
    result = engine.recall_for_presence(query)
    if corruption in {None, "truncated_complete"}:
        assert len(result.confirmed_facts) == 1
        # Shared authority remains admitted without a private PG resolver;
        # consolidation never promotes the derived claim to a direct-user fact.
        fact = result.confirmed_facts[0]
        assert fact.trust == "host_policy"
        assert fact.value["semantic_trust"] == "model_inference_unconfirmed"
        assert len(fact.value["observation_source_refs"]) == 2
        assert "observation_sources" not in fact.value
    else:
        assert result.confirmed_facts == ()



@pytest.mark.parametrize("sources_kind", ["mixed", "household_only", "missing"])
def test_private_recall_cannot_hide_shared_observation_sources(hindsight_server, sources_kind):
    base_url, state = hindsight_server
    _, _, _, _, household_metadata = _household_memory_fixture(base_url, state)
    shared = {**household_metadata, "principal_id": PRINCIPAL}
    private = {"principal_id": PRINCIPAL, "privacy_class": "internal", "trust": "direct_user",
               "audience_kind": "private_person", "source_ref": "telegram:private",
               "evidence_ref": "evidence:private"}
    sources = {"one": {"mentioned_at": NOW, "metadata": shared},
               "two": {"mentioned_at": NOW, "metadata": private if sources_kind == "mixed" else shared}}
    if sources_kind == "missing":
        sources = {}
    state.group_recall_response = {"results": [{"id": "mixed", "type": "observation",
        "scores": {"final": 0.9}, "text": "Shared content disguised as private",
        "mentioned_at": NOW, "metadata": private, "source_fact_ids": ["one", "two"],
        "_eva_source_metadata": private}], "source_facts": sources}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    if sources_kind == "household_only":
        with pytest.raises(HindsightMemoryScopeError, match="shared memory for a private audience"):
            engine.recall_for_presence(_query())
    else:
        assert engine.recall_for_presence(_query()).confirmed_facts == ()


@pytest.mark.parametrize(
    "statuses,retries,succeeds",
    [
        (["failed", "pending", "completed"], 1, True),
        (["failed", "failed"], 1, False),
        (["pending", "failed"], 0, False),
        (["cancelled"], 0, False),
    ],
)
def test_retain_retries_only_preexisting_failure_once(
    monkeypatch: pytest.MonkeyPatch, statuses: list[str], retries: int, succeeds: bool,
) -> None:
    from eva.adapters.projection.hindsight import HindsightProjectionError

    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://localhost:8888"))
    remaining = iter(statuses)
    calls = []

    def request(method, path, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((method, path))
        if method == "POST":
            assert path == "/v1/default/banks/bank/operations/op/retry"
            return 200, {"success": True, "operation_id": "op"}
        return 200, {"operation_id": "op", "status": next(remaining)}

    monkeypatch.setattr(engine, "_request", request)
    if succeeds:
        engine._wait_for_operation("bank", "op", retry_existing_failure=True)
    else:
        with pytest.raises(HindsightProjectionError, match="ended as"):
            engine._wait_for_operation("bank", "op", retry_existing_failure=True)
    assert sum(method == "POST" for method, _ in calls) == retries


@pytest.mark.parametrize("score", [0.0004, 0.024])
def test_conversational_recall_keeps_best_low_scoring_candidate(
    hindsight_server: tuple[str, _State], score: float,
) -> None:
    base_url, state = hindsight_server
    state.group_recall_response = {"results": [
        {"id": "best", "text": "De gebruiker wandelt graag langs het water.",
         "type": "world", "scores": {"final": score}, "mentioned_at": NOW,
         "document_id": "walking-preference", "metadata": {
             "principal_id": PRINCIPAL, "privacy_class": "internal",
             "trust": "direct_user"}},
        {"id": "unrelated", "text": "Een eerdere computerreparatie.",
         "type": "world", "scores": {"final": score / 10}, "mentioned_at": NOW,
         "document_id": "computer", "metadata": {
             "principal_id": PRINCIPAL, "privacy_class": "internal",
             "trust": "direct_user"}},
    ]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    memory = engine.recall_for_presence_fast(
        replace(_query(), query_text="Ik ga even naar buiten. Welke route is fijn?")
    )
    assert [fact.fact_id for fact in memory.confirmed_facts] == ["hindsight:best"]
    assert memory.confirmed_facts[0].source_ref == "walking-preference"


@pytest.mark.parametrize("boundary", ["zero", "other_person", "missing_source"])
def test_low_score_candidate_preserves_admission_boundaries(
    hindsight_server: tuple[str, _State], boundary: str,
) -> None:
    base_url, state = hindsight_server
    item = {"id": "best", "text": "Een herinnering.", "type": "world",
            "scores": {"final": 0 if boundary == "zero" else 0.001},
            "mentioned_at": NOW, "document_id": "source", "metadata": {
                "principal_id": "another-person" if boundary == "other_person" else PRINCIPAL,
                "privacy_class": "internal", "trust": "direct_user"}}
    if boundary == "missing_source":
        item["type"] = "observation"
        item["metadata"] = {}
    state.group_recall_response = {"results": [item]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    if boundary == "other_person":
        with pytest.raises(HindsightMemoryScopeError):
            engine.recall_for_presence_fast(_query())
    else:
        assert engine.recall_for_presence_fast(_query()).confirmed_facts == ()


@pytest.mark.parametrize("text", ["Hallo", "warme woonkamer " * 80, "<|endoftext|> hoi"])
def test_recall_queries_within_token_limit_are_unchanged(text: str) -> None:
    assert len(_recall_tokenizer().encode(text, disallowed_special=())) <= 500
    assert _bounded_recall_query(text) == text


@pytest.mark.parametrize("middle", ["documentatie " * 1500, "🔆漢字é " * 1000, "<|endoftext|> " * 500])
def test_long_recall_keeps_both_ends_without_invalid_unicode(middle: str) -> None:
    text = "BEGIN woonkamer " + middle + " EINDE overkapping"
    bounded = _bounded_recall_query(text)
    assert bounded.startswith("BEGIN woonkamer ")
    assert bounded.endswith(" EINDE overkapping")
    assert "\ufffd" not in bounded
    assert len(_recall_tokenizer().encode(bounded, disallowed_special=())) <= 500


def test_long_recall_succeeds_with_original_query_and_person_scope_preserved(
    hindsight_server: tuple[str, _State],
) -> None:
    base_url, state = hindsight_server
    state.enforce_query_limit = True
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    text = "BEGIN " + "lange formaatdocumentatie " * 600 + " EINDE"
    query = replace(_query(), query_text=text)
    variants = engine._recall_query_variants(text)
    result = engine.recall_for_presence(query)
    requests = [body for method, path, body in state.requests
                if method == "POST" and path.endswith("/memories/recall")]
    assert result.confirmed_facts
    assert query.query_text == text
    assert len(requests) == len(variants)
    assert {body["query"] for body in requests} == {_bounded_recall_query(v) for v in variants}
    expected_tag = "eva-person-" + hashlib.sha256(PRINCIPAL.encode()).hexdigest()[:32]
    assert all(body["tags"] == [expected_tag] and body["tags_match"] == "all_strict" for body in requests)


def test_low_score_fallback_ignores_unproven_observation(hindsight_server, caplog):
    base_url, state = hindsight_server
    state.group_recall_response = {"results": [
        {"id": "unproven-derived", "text": "PRIVATE-DERIVED-CONTENT",
         "type": "observation", "scores": {"final": 0.9},
         "mentioned_at": NOW, "source_fact_ids": ["missing-source"]},
        {"id": "permitted-source", "text": "PRIVATE-TRUSTED-CONTENT",
         "type": "world", "scores": {"final": 0.001}, "mentioned_at": NOW,
         "document_id": "trusted-document", "metadata": {
             "principal_id": PRINCIPAL, "privacy_class": "internal",
             "trust": "direct_user"}},
    ]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    with caplog.at_level("INFO", logger="eva.adapters.projection.hindsight"):
        memory = engine.recall_for_presence_fast(_query())
    assert [fact.fact_id for fact in memory.confirmed_facts] == [
        "hindsight:permitted-source"]
    assert memory.confirmed_facts[0].source_ref == "trusted-document"
    events = [json.loads(record.message.split(" ", 1)[1])
              for record in caplog.records
              if record.message.startswith("memory_recall_selection ")]
    assert len(events) == 1
    assert events[0]["selected"] == [canonical_sha256("hindsight:permitted-source")]
    assert len(events[0]["candidates"]) == 2
    serialized = json.dumps(events[0])
    for private_value in ["PRIVATE-DERIVED-CONTENT", "PRIVATE-TRUSTED-CONTENT",
                          "unproven-derived", "permitted-source",
                          "trusted-document", PRINCIPAL, _query().query_text]:
        assert private_value not in serialized


def test_recall_diagnostic_handles_trailing_invalid_type(hindsight_server, caplog):
    base_url, state = hindsight_server
    state.group_recall_response = {"results": [
        {"id": "permitted-source", "text": "Trusted memory", "type": "world",
         "scores": {"final": 0.9}, "mentioned_at": NOW,
         "document_id": "trusted-document", "metadata": {
             "principal_id": PRINCIPAL, "privacy_class": "internal",
             "trust": "direct_user"}},
        {"id": "trailing-invalid", "text": "Ignored memory", "type": [],
         "scores": {"final": 0.1}, "mentioned_at": NOW},
    ]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    query = replace(_query(), maximum_facts=1)
    with caplog.at_level("INFO", logger="eva.adapters.projection.hindsight"):
        memory = engine.recall_for_presence_fast(query)
    assert [fact.fact_id for fact in memory.confirmed_facts] == [
        "hindsight:permitted-source"]
    events = [json.loads(record.message.split(" ", 1)[1])
              for record in caplog.records
              if record.message.startswith("memory_recall_selection ")]
    assert events[0]["candidates"][1]["memory_type"] == "unknown"


def test_recall_diagnostic_failure_does_not_discard_memory(hindsight_server, monkeypatch, caplog):
    base_url, state = hindsight_server
    state.group_recall_response = {"results": [
        {"id": "permitted-source", "text": "Trusted memory", "type": "world",
         "scores": {"final": 0.9}, "mentioned_at": NOW,
         "document_id": "trusted-document", "metadata": {
             "principal_id": PRINCIPAL, "privacy_class": "internal",
             "trust": "direct_user"}},
    ]}
    def fail_diagnostic(*args, **kwargs):
        raise RuntimeError("PRIVATE-DIAGNOSTIC-ERROR")
    monkeypatch.setattr("eva.adapters.projection.hindsight._LOGGER.info", fail_diagnostic)
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url))
    memory = engine.recall_for_presence_fast(_query())
    assert [fact.fact_id for fact in memory.confirmed_facts] == [
        "hindsight:permitted-source"]
    assert "PRIVATE-DIAGNOSTIC-ERROR" not in caplog.text


@pytest.mark.parametrize("source_state", ["absent", "error"])
def test_original_private_source_is_deduplicated_and_revalidated_on_cache_hit(hindsight_server, source_state):
    base_url, state = hindsight_server
    doc = _conversation_document()
    metadata = {
        "principal_id": doc.principal_id, "source_ref": doc.source_ref,
        "evidence_ref": doc.evidence_ref, "privacy_class": doc.privacy_class,
        "trust": doc.trust, "audience_kind": "private_person",
        "audience_id": doc.audience.audience_id,
    }
    state.group_recall_response = {"results": [
        {"id": name, "text": text, "type": "world", "mentioned_at": NOW,
         "scores": {"final": score}, "metadata": metadata}
        for name, text, score in (
            ("feedback", "That day no new task may be started.", 1.0),
            ("norm", "The project requires a reserve of nine.", 0.9),
        )
    ]}
    original = {
        "principal_id": doc.principal_id, "source_ref": doc.source_ref,
        "evidence_ref": doc.evidence_ref, "project_id": None, "occurred_at": NOW,
        "text": "Use three short bullets with a next step. Start nothing new now.",
        "truncated": False,
    }
    calls = []
    available = [True]

    def sources(query, references):
        calls.append((query, references))
        if not available[0] and source_state == "error":
            raise RuntimeError("synthetic resolver outage")
        return (dict(original),) if available[0] else ()

    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
                                   private_source_resolver=sources)
    first = engine.recall_for_presence_fast(_query())
    assert len(calls) == 1
    assert calls[0][1] == ((doc.principal_id, doc.source_ref, doc.evidence_ref),)
    assert sum("original_source" in fact.value for fact in first.confirmed_facts) == 1
    assert first.confirmed_facts[0].value["original_source"]["text"] == original["text"]
    assert [fact.value["text"] for fact in first.confirmed_facts] == [
        "That day no new task may be started.", "The project requires a reserve of nine."]
    requests = len(state.requests)
    # The original source was erased or authority expired during the cache lease.
    available[0] = False
    second = engine.recall_for_presence_fast(_query())
    assert len(calls) == 2 and len(state.requests) == requests
    assert all("original_source" not in fact.value for fact in second.confirmed_facts)
    if source_state == "absent":
        assert second.confirmed_facts == ()
        assert second.recall.selected == []
        assert second.recall.admitted_fact_ids_sha256 == canonical_sha256([])
        assert second.recall.excluded_reasons == {
            fact.fact_id: "known_source_unavailable" for fact in first.confirmed_facts}
    else:
        # An unavailable resolver has not proved a known source erased/expired.
        assert second.recall == first.recall
        assert len(second.confirmed_facts) == 2
    available[0] = True
    restored = engine.recall_for_presence_fast(_query())
    assert restored.confirmed_facts == first.confirmed_facts
    assert len(state.requests) == requests


def test_original_private_source_never_crosses_project(hindsight_server):
    base_url, state = hindsight_server
    doc = _conversation_document()
    state.group_recall_response = {"results": [{
        "id": "project-feedback", "text": "An extracted preference.", "type": "world",
        "mentioned_at": NOW, "scores": {"final": 1.0},
        "metadata": {"principal_id": doc.principal_id, "source_ref": doc.source_ref,
                     "evidence_ref": doc.evidence_ref, "privacy_class": doc.privacy_class,
                     "trust": doc.trust},
    }]}
    wrong_source = {
        "principal_id": doc.principal_id, "source_ref": doc.source_ref,
        "evidence_ref": doc.evidence_ref, "project_id": "another-project",
        "occurred_at": NOW, "text": "Private source in another project.",
        "truncated": False,
    }
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
                                   private_source_resolver=lambda _q, _r: (wrong_source,))
    recalled = engine.recall_for_presence(_query())
    assert all("original_source" not in fact.value for fact in recalled.confirmed_facts)


def test_learning_recall_cache_requires_current_chain_and_ignores_consolidated_claim(hindsight_server):
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record
    base_url, state = hindsight_server
    original = _conversation_document()
    turn = ConversationTurn.private(original.tenant_id, original.installation_id, original.principal_id,
        original.conversation_id, original.source_ref, "Genuine synthetic user evidence", NOW)
    doc = build_human_understanding_document(turn, {"kind":"person_hypothesis", "interpretation":"Current provisional understanding.",
        "uncertainty_reason":"Limited direct evidence.","review_at":None,"outcome":"open",
        "previous_source_ref":None,"previous_evidence_ref":None})
    record = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
    context = {"contract":"EvaHumanUnderstandingContext/v1","records":[{
        "source_ref":doc.source_ref,"evidence_ref":doc.evidence_ref,"record":record}]}
    metadata = {"principal_id":doc.principal_id,"source_ref":doc.source_ref,"evidence_ref":doc.evidence_ref,
        "privacy_class":doc.privacy_class,"trust":doc.trust}
    state.group_recall_response = {"results":[{"id":"learning-fact","text":"Untrusted synthesized claim.",
        "type":"world","mentioned_at":NOW,"scores":{"final":1.0},"metadata":metadata}]}
    available = [True]
    source = {"principal_id":doc.principal_id,"source_ref":doc.source_ref,"evidence_ref":doc.evidence_ref,
        "project_id":None,"occurred_at":NOW,"text":turn.text,"truncated":False,"learning_context":context}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
        private_source_resolver=lambda _q,_r:(source,) if available[0] else ())
    first = engine.recall_for_presence_fast(_query())
    assert first.confirmed_facts[0].value["text"] == "Current provisional understanding."
    assert first.confirmed_facts[0].value["semantic_trust"] == "model_inference_unconfirmed"
    requests = len(state.requests)
    available[0] = False
    second = engine.recall_for_presence_fast(_query())
    assert len(state.requests) == requests and second.confirmed_facts == ()
    assert second.recall.selected == []
    assert second.recall.excluded_reasons == {first.confirmed_facts[0].fact_id:"learning_source_unavailable"}
    # Consolidation with unrelated direct-user facts has no typed learning authority.
    mixed = {"results":[{"id":"mixed","type":"observation","source_fact_ids":["learning","other"]}],
        "source_facts":{"learning":{"metadata":metadata,"mentioned_at":NOW},
            "other":{"metadata":{**metadata,"evidence_ref":"direct-user-source","trust":"direct_user"},"mentioned_at":NOW}}}
    engine._request = lambda *args, **kwargs: (200, mixed)
    assert engine._recall_response("test-bank", "person-tag", _query(), "query", None, None) == []


def test_current_typed_learning_without_semantic_match_is_bounded_and_rechecked(hindsight_server):
    from datetime import datetime, timezone, timedelta
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record
    base_url, state = hindsight_server
    ordinary = _conversation_document()
    state.group_recall_response = {"results":[{"id":"ordinary", "text":"Ordinary conversation fact.",
        "type":"world", "mentioned_at":NOW, "scores":{"final":1.0}, "metadata":{
            "principal_id":ordinary.principal_id,"source_ref":ordinary.source_ref,
            "evidence_ref":ordinary.evidence_ref,"privacy_class":ordinary.privacy_class,"trust":ordinary.trust}}]}
    sources=[]
    for index in range(8):
        stamp=(datetime.strptime(NOW,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)-timedelta(seconds=10-index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        turn=ConversationTurn.private(ordinary.tenant_id,ordinary.installation_id,ordinary.principal_id,
            ordinary.conversation_id,"typed-"+str(index),"Genuine synthetic user source.",stamp)
        doc=build_human_understanding_document(turn,{"kind":"person_hypothesis","interpretation":"Provisional "+str(index),
            "uncertainty_reason":"Needs verification.","review_at":None,"outcome":"open", "previous_source_ref":None,"previous_evidence_ref":None})
        context={"contract":"EvaHumanUnderstandingContext/v1","records":[{"source_ref":doc.source_ref,
            "evidence_ref":doc.evidence_ref,"record":parse_human_understanding_record(doc.content,evidence_ref=doc.evidence_ref)}]}
        sources.append({"principal_id":doc.principal_id,"source_ref":doc.source_ref,"evidence_ref":doc.evidence_ref,
            "project_id":None,"occurred_at":stamp,"text":turn.text,"truncated":False,"learning_context":context})
    current=[sources[:1]]
    engine=HindsightMemoryEngine(HindsightProjectionConfig(base_url),private_source_resolver=lambda q,r:tuple(current[0]))
    first=engine.recall_for_presence_fast(_query())
    assert len(first.confirmed_facts)==2 and first.confirmed_facts[-1].evidence_ref==sources[0]["evidence_ref"]
    requests=len(state.requests)
    current[0]=sources
    second=engine.recall_for_presence_fast(_query())
    assert len(state.requests)==requests and len(second.confirmed_facts)==9
    contexts=[fact.value["learning_context"] for fact in second.confirmed_facts if "learning_context" in fact.value]
    assert len({(record["source_ref"],record["evidence_ref"]) for context in contexts for record in context["records"]})==8
    assert all(fact.value["semantic_trust"]=="model_inference_unconfirmed" for fact in second.confirmed_facts[1:])
    current[0]=[]
    erased=engine.recall_for_presence_fast(_query())
    assert len(state.requests)==requests and [fact.fact_id for fact in erased.confirmed_facts]==[first.confirmed_facts[0].fact_id]
    # An unrelated project/actor cannot acquire the host-resolved learning records.
    current[0]=[{**sources[0],"project_id":"other"}]
    assert len(engine.recall_for_presence_fast(_query()).confirmed_facts)==1
    current[0]=[{**sources[0],"principal_id":"person:other"}]
    assert len(engine.recall_for_presence_fast(_query()).confirmed_facts)==1


def _multi_event_observation(state):
    # One provisional pattern includes its contrary event; all text is synthetic.
    sources = [{"principal_id": PRINCIPAL, "source_ref": "telegram:event:" + str(index),
        "evidence_ref": "evidence:event:" + str(index), "project_id": None,
        "occurred_at": "2026-08-2" + str(index) + "T10:00:00Z", "text": text, "truncated": False}
        for index, text in enumerate(("I preferred a quiet meeting.", "I chose another quiet meeting.",
                                      "At the celebration I preferred a lively group."), start=1)]
    state.group_recall_response = {"results": [{"id": "pattern", "type": "observation",
        "text": "A provisional preference for quiet meetings with a contextual exception.",
        "mentioned_at": NOW, "scores": {"final": 1.0}, "source_fact_ids": ["a", "b", "c"],
        "_eva_observation_source_refs": [{"principal_id": "another-person",
            "source_ref": "private:injected", "evidence_ref": "private:injected"}]}],
        "source_facts": {key: {"mentioned_at": source["occurred_at"], "metadata": {
            "principal_id": source["principal_id"], "source_ref": source["source_ref"],
            "evidence_ref": source["evidence_ref"], "privacy_class": "internal", "trust": "direct_user"}}
            for key, source in zip(("a", "b", "c"), sources, strict=True)}}
    return sources


def test_private_observation_keeps_all_events_and_rechecks_contrary_source_on_cache(hindsight_server):
    base_url, state = hindsight_server
    sources = _multi_event_observation(state)
    current = list(sources)
    requests = []
    def resolve(query, references):
        requests.append(references)
        return tuple(source for source in current if
            (source["principal_id"], source["source_ref"], source["evidence_ref"]) in references)
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url), private_source_resolver=resolve)
    first = engine.recall_for_presence_fast(_query())
    fact, = first.confirmed_facts
    assert requests == [tuple((s["principal_id"], s["source_ref"], s["evidence_ref"]) for s in sources)]
    assert fact.value["observation_sources"] == sources
    assert fact.source_ref == sources[-1]["source_ref"]  # Newest primary stays compatible.
    assert fact.value["original_source"] == sources[-1]
    assert first.recall.selected[0].source_event_ids == [s["source_ref"] for s in sources]
    assert first.recall.selected[0].trust == "model_inference_unconfirmed"
    assert fact.trust == "host_policy" and fact.value["semantic_trust"] == "model_inference_unconfirmed"
    semantic_requests = len(state.requests)
    current.pop()  # Current PG authority no longer admits the contrary event.
    second = engine.recall_for_presence_fast(_query())
    assert len(state.requests) == semantic_requests and len(requests) == 2
    assert second.confirmed_facts == () and second.recall.selected == []
    assert second.recall.excluded_reasons == {fact.fact_id: "observation_source_unavailable"}
    assert fact.value["observation_sources"] == sources  # Cached evidence wasn't rewritten.


@pytest.mark.parametrize("failure", ["no_resolver", "unavailable", "wrong_person", "wrong_project",
                                     "mixed_authority", "missing_support", "over_source_bound"])
def test_private_observation_never_trims_or_substitutes_unresolved_support(hindsight_server, failure):
    base_url, state = hindsight_server
    sources = _multi_event_observation(state)
    if failure == "unavailable":
        sources.pop()
    elif failure == "wrong_person":
        sources[-1] = {**sources[-1], "principal_id": "another-person"}
    elif failure == "wrong_project":
        sources[-1] = {**sources[-1], "project_id": "another-project"}
    elif failure == "mixed_authority":
        state.group_recall_response["source_facts"]["a"]["metadata"]["principal_id"] = "another-person"
    elif failure == "missing_support":
        state.group_recall_response["source_facts"].pop("a")
    elif failure == "over_source_bound":
        state.group_recall_response["results"][0]["source_fact_ids"].append("d")
        last = state.group_recall_response["source_facts"]["c"]
        state.group_recall_response["source_facts"]["d"] = {**last, "metadata": {
            **last["metadata"], "source_ref": "telegram:event:4", "evidence_ref": "evidence:event:4"}}
    resolver = None if failure == "no_resolver" else lambda _q, _r: tuple(sources)
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url), private_source_resolver=resolver)
    assert engine.recall_for_presence(_query()).confirmed_facts == ()


@pytest.mark.parametrize("before,after,grounded", [
    ("  Earlier style  ", "  Updated style\n", True),
    ("Same style", "  Same style  ", True),
    (None, "New style", True),
    ("Old ungrounded style", "Ungrounded style", False),
])
def test_reflection_receipt_binds_only_grounded_presence_content(monkeypatch, before, after, grounded):
    import hashlib
    from dataclasses import asdict
    from eva.adapters.projection.hindsight import BackgroundReflectionReceipt

    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://127.0.0.1:8888"))
    model_id = engine._style_mirror_id(PRINCIPAL)
    evidence = {"world": [{"id": "private-source-one"}], "observations": [{"id": "private-source-two"}]}
    trace = []
    finished = False

    def ensure_bank(tenant, installation):
        assert (tenant, installation) == (TENANT, INSTALLATION)
        return "scoped-bank"

    def response(content):
        return {"id": model_id, "trigger": {}, "content": content,
                "reflect_response": {"based_on": evidence if grounded else {"world": []}}}

    def request(method, path, payload=None, **kwargs):
        trace.append((method, path, payload))
        assert path.startswith("/v1/default/banks/scoped-bank/mental-models")
        if method == "GET":
            if not finished and before is None:
                return 404, {}
            return 200, response(after if finished else before)
        if method == "PATCH":
            return 200, {"id": model_id, **payload}
        if path.endswith("/mental-models"):
            assert payload["tags"] == [engine._person_tag(PRINCIPAL)]
            assert payload["trigger"]["tags_match"] == "all_strict"
        return 200, {"operation_id": "operation-one"}

    def wait(bank, operation):
        nonlocal finished
        assert (bank, operation) == ("scoped-bank", "operation-one")
        trace.append(("WAIT", bank, operation))
        finished = True

    monkeypatch.setattr(engine, "ensure_bank", ensure_bank)
    monkeypatch.setattr(engine, "_request", request)
    monkeypatch.setattr(engine, "_wait_for_operation", wait)
    receipt = engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
    assert [item[0] for item in trace][-2:] == ["WAIT", "GET"]
    digest = lambda content: hashlib.sha256(content.strip().encode()).hexdigest()
    assert receipt.before_content_sha256 == (digest(before) if grounded and before is not None else None)
    assert receipt.after_content_sha256 == (digest(after) if grounded else None)
    assert receipt.source_fact_count == (2 if grounded else 0)
    expected = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert receipt.source_evidence_sha256 == (expected if grounded else None)
    assert engine._recall_style_mirror("scoped-bank", PRINCIPAL) == (after.strip() if grounded else None)
    assert "private-source" not in json.dumps(asdict(receipt))
    assert after not in json.dumps(asdict(receipt))
    assert BackgroundReflectionReceipt("b", "m", "created", "o").source_fact_count == 0
    wrong = response(after)
    wrong["id"] = engine._style_mirror_id("another-person")
    with pytest.raises(HindsightProjectionProtocolError, match="identity differs"):
        engine._style_mirror_content(wrong, model_id)


@pytest.mark.parametrize("readback", ["unavailable", "malformed", "ungrounded"])
def test_reflection_receipt_preserves_settled_operation_on_readback_loss(monkeypatch, readback):
    import hashlib
    from dataclasses import asdict
    from eva.adapters.projection.hindsight import HindsightProjectionUnavailable

    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://127.0.0.1:8888"))
    model_id = engine._style_mirror_id(PRINCIPAL)
    trace = []
    settled = False

    def request(method, path, payload=None, **kwargs):
        trace.append(method)
        if method == "GET" and not settled:
            return 404, {}
        if method == "POST":
            assert not settled
            return 200, {"operation_id": "settled-operation"}
        assert method == "GET" and settled
        if readback == "unavailable":
            raise HindsightProjectionUnavailable("private diagnostic must not escape")
        return 200, {
            "id": model_id,
            "content": None if readback == "malformed" else "Ungrounded content",
            "reflect_response": {"based_on": {"world": ["private-source"] if readback == "malformed" else []}},
        }

    def wait(bank, operation):
        nonlocal settled
        assert operation == "settled-operation"
        settled = True
        trace.append("WAIT")

    monkeypatch.setattr(engine, "ensure_bank", lambda *args: "scoped-bank")
    monkeypatch.setattr(engine, "_request", request)
    monkeypatch.setattr(engine, "_wait_for_operation", wait)
    receipt = engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
    assert trace == ["GET", "POST", "WAIT", "GET"]
    assert receipt.operation_id == "settled-operation" and receipt.disposition == "created"
    assert receipt.after_content_sha256 is None
    assert receipt.source_evidence_sha256 is None and receipt.source_fact_count == 0
    if readback == "ungrounded":
        assert receipt.observation_error_type is None
    else:
        error_type = HindsightProjectionUnavailable if readback == "unavailable" else HindsightProjectionProtocolError
        assert receipt.observation_error_type == hashlib.sha256(
            f"{error_type.__module__}.{error_type.__qualname__}".encode()
        ).hexdigest()
    assert "private" not in json.dumps(asdict(receipt))


def test_missing_imported_legacy_source_does_not_invent_native_erasure(hindsight_server):
    base_url, state = hindsight_server
    state.group_recall_response = {"results": [{
        "id": "imported-legacy", "text": "Synthetic historical preference", "type": "world",
        "mentioned_at": NOW, "scores": {"final": 1.0}, "metadata": {
            "principal_id": PRINCIPAL, "source_ref": "eva1-memory:source:rows:0-49",
            "evidence_ref": "eva1:import:synthetic", "privacy_class": "internal",
            "trust": "legacy_unverified"},
    }]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base_url),
        private_source_resolver=lambda _query, _refs: ())
    first = engine.recall_for_presence_fast(_query())
    second = engine.recall_for_presence_fast(_query())
    assert len(first.confirmed_facts) == len(second.confirmed_facts) == 1
    assert second.confirmed_facts[0].value["semantic_trust"] == "legacy_unverified"
    assert second.recall.selected[0].trust == "legacy_unverified"
    assert "original_source" not in second.confirmed_facts[0].value
    assert second.recall.excluded_reasons == {}


@pytest.mark.parametrize("missing_metadata", [None, {}])
def test_shared_recall_skips_unproven_legacy_fact_without_losing_valid_fallback(
    hindsight_server, missing_metadata,
):
    base_url, state = hindsight_server
    engine, _doc, query, _grants, _metadata = _household_memory_fixture(base_url, state)
    valid = state.group_recall_response["results"][0]
    valid["scores"]["final"] = 0.009
    unproven = {**valid, "id": "legacy-without-authority", "text": "Unproven household content",
                "scores": {"final": 0.014}, "metadata": missing_metadata}
    state.group_recall_response["results"] = [unproven, valid]
    result = engine.recall_for_presence(query)
    assert [fact.value["text"] for fact in result.confirmed_facts] == [valid["text"]]
    assert result.recall.excluded_reasons == {
        "hindsight:legacy-without-authority": "missing_shared_source_authority"}
    state.group_recall_response["results"] = [unproven]
    result = engine.recall_for_presence(query)
    assert result.confirmed_facts == ()
    assert result.recall.excluded_reasons == {
        "hindsight:legacy-without-authority": "missing_shared_source_authority"}
