from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

import eva.adapters.model.conversation as conversation_module
from eva.adapters.model.conversation import (
    CodexConversationModel,
    ConversationModelError,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationObservationBinding,
    ConversationTurn,
    IdentityProfile,
    RecentConversationMessage,
    load_current_identity_pack,
)
from eva.application.memory import PresenceMemory
from eva.application.sensory import SensoryEvent
from eva.application.personal_interests import (
    CompletedPersonalExploration,
    CompletedPersonalPrototype,
    PersonalInterestClaim,
    PersonalInterestExploration,
    PersonalProductReleaseClaim,
    PersonalPrototypeClaim,
    PersonalPrototype,
)
from eva.application.research import ResearchSource
from eva.application.repository_improvements import (
    RepositoryCheck,
    RepositoryFile,
    RepositorySnapshot,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact
from eva.contracts.runtime_budget import RuntimeResourceBudget


NOW = "2026-08-23T14:00:00Z"


@pytest.fixture(autouse=True)
def local_model_lease_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit leases local while production uses the fixed container contract."""

    monkeypatch.setattr(conversation_module, "_MODEL_LEASE_OWNER_UID", os.geteuid())
    monkeypatch.setattr(conversation_module, "_MODEL_LEASE_READER_GID", os.getegid())
    monkeypatch.setattr(
        conversation_module,
        "read_shared_runtime_file_bytes",
        lambda path, **_kwargs: Path(path).read_bytes(),
    )


def test_bound_photo_correction_uses_closed_action_without_face_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "identity-correction-call-1",
        "record_observation_identity_correction",
        {"corrected_name": "Mark"},
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    binding = ConversationObservationBinding(
        "telegram-photo:mark-1",
        "a" * 64,
        "b" * 64,
        "image/jpeg",
        "telegram:file:mark-1",
    )
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-photo-correction",
            "Nee, dat is Mark.",
            NOW,
            observation_binding=binding,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("record_observation_identity_correction",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == (
        "record_observation_identity_correction"
    )
    assert binding.binding_sha256 in bodies[0]["input"][0]["content"]
    assert "Leid zelf geen gezicht of naam" in bodies[0]["instructions"]


def test_validated_file_edit_is_a_closed_model_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "validated-edit-call-1",
        "edit_text_file_with_focused_validation",
        {
            "new_text": "MODE=turbo",
            "old_text": "MODE=safe",
            "target_path": "project/settings.env",
            "test_manifest_path": "project/focused-test.json",
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-edit",
            (
                "Vervang MODE=safe door MODE=turbo in project/settings.env en "
                "draai project/focused-test.json."
            ),
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("edit_text_file_with_focused_validation",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == (
        "edit_text_file_with_focused_validation"
    )
    assert "rolt alleen de eigen edit terug" in bodies[0]["instructions"]


def test_repository_improvement_is_self_chosen_from_a_bounded_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    snapshot = RepositorySnapshot(
        project_directory="fixture-repo",
        repository_sha256="a" * 64,
        checks_manifest_sha256="b" * 64,
        files=(
            RepositoryFile(
                "settings.toml", "retry_limit = 0\n", "c" * 64
            ),
        ),
        checks=(
            RepositoryCheck(
                "bounded_retry",
                "settings.toml",
                "contains",
                "retry_limit = 2",
                False,
                "d" * 64,
            ),
        ),
        captured_at=datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", ConversationActionCall(
            "proposal-1",
            "submit_repository_improvement",
            {
                "check_id": "bounded_retry",
                "evidence": "settings.toml bevat retry_limit = 0.",
                "new_text": "retry_limit = 2",
                "old_text": "retry_limit = 0",
                "problem": "Een tijdelijke fout krijgt geen begrensde retry.",
                "target_path": "settings.toml",
            },
        )

    monkeypatch.setattr(model, "_request", request)
    proposal = model.choose_repository_improvement(snapshot)

    assert proposal.check_id == "bounded_retry"
    assert proposal.target_path == "settings.toml"
    assert bodies[0]["tool_choice"]["name"] == "submit_repository_improvement"
    assert "retry_limit = 0" in bodies[0]["input"][0]["content"]
    assert "Kies zelf precies één werkelijk falende check" in bodies[0]["instructions"]


def _write_access_lease(path: Path, access_token: str = "access") -> None:
    path.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "account_id": "account",
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "0" * 64,
            }
        )
    )
    path.chmod(0o640)


def _runtime_budget(
    *, max_input_tokens: int = 12_000, max_output_tokens: int = 1_200
) -> RuntimeResourceBudget:
    values = {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v1",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": max_output_tokens,
        "memory_bytes_per_person": 20_971_520,
        "memory_records_per_person": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id="tenant:test",
        installation_id="installation:test",
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(values)
        ).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=40,
        external_monthly_spend_cap_eur_cents=0,
    )


@contextmanager
def _budget_provider(
    frames: bytes,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(size)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/responses", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _budget_frames(
    *,
    input_tokens: int = 100,
    output_tokens: int = 20,
    status: str = "completed",
    error: object = None,
    include_usage: bool = True,
    trailing_data: bool = False,
) -> bytes:
    usage = (
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        if include_usage
        else None
    )
    completed = json.dumps(
        {
            "response": {"error": error, "status": status, "usage": usage},
            "type": "response.completed",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    suffix = (
        'data: {"type":"response.output_text.delta","delta":"te laat"}\n\n'
        if trailing_data
        else "data: [DONE]\n\n"
    )
    return (
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"Binnen grens."}\n\n'
        "event: response.completed\n"
        f"data: {completed}\n\n"
        f"{suffix}"
    ).encode()


@pytest.fixture
def model_server() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(size)))
            frames = (
                "event: response.output_text.delta\n"
                'data: {"type":"response.output_text.delta","delta":"Dat klinkt "}\n\n'
                "event: response.output_text.delta\n"
                'data: {"type":"response.output_text.delta","delta":"echt rot."}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/responses", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_runtime_budget_rejects_oversized_input_before_auth_or_network(
    tmp_path: Path,
) -> None:
    with _budget_provider(_budget_frames()) as (endpoint, requests):
        model = CodexConversationModel(
            auth_file=tmp_path / "missing-auth.json",
            endpoint=endpoint,
            resource_budget=_runtime_budget(max_input_tokens=4_000),
        )

        with pytest.raises(ConversationModelError, match="input budget"):
            model._request(  # noqa: SLF001 - this is the provider admission boundary
                {"input": [{"content": "x" * 4_000, "role": "user"}]}
            )

    assert requests == []


def test_runtime_budget_blocks_unknown_autonomous_spend_before_network(
    tmp_path: Path,
) -> None:
    with _budget_provider(_budget_frames()) as (endpoint, requests):
        model = CodexConversationModel(
            auth_file=tmp_path / "missing-auth.json",
            endpoint=endpoint,
            resource_budget=_runtime_budget(),
        )

        with pytest.raises(ConversationModelError, match="no verified price"):
            model._request_autonomous(  # noqa: SLF001
                {"input": [{"content": "kleine taak", "role": "user"}]},
            )

    assert requests == []


def test_runtime_budget_records_measured_terminal_provider_usage(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_access_lease(auth_file)
    with _budget_provider(_budget_frames()) as (endpoint, requests):
        model = CodexConversationModel(
            auth_file=auth_file,
            endpoint=endpoint,
            resource_budget=_runtime_budget(),
        )

        text, action, usage = model._request_conversation(  # noqa: SLF001
            {"input": [{"content": "hoi", "role": "user"}]}
        )

    assert text == "Binnen grens."
    assert action is None
    assert usage.measurement == "measured"
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.model_attempts == 1
    assert usage.tool_calls == 0
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("frames", "message"),
    (
        (_budget_frames(output_tokens=1_201), "output budget"),
        (_budget_frames(input_tokens=12_001), "input budget"),
        (
            _budget_frames(status="incomplete"),
            "completion status is invalid",
        ),
        (_budget_frames(trailing_data=True), "data after completion"),
        (
            (
                "event: response.output_text.delta\n"
                'data: {"type":"response.output_text.delta","delta":"geen usage"}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
            "usage is missing",
        ),
    ),
)
def test_runtime_budget_withholds_unverifiable_or_over_budget_replies(
    tmp_path: Path,
    frames: bytes,
    message: str,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_access_lease(auth_file)
    with _budget_provider(frames) as (endpoint, requests):
        model = CodexConversationModel(
            auth_file=auth_file,
            endpoint=endpoint,
            resource_budget=_runtime_budget(),
        )

        with pytest.raises(ConversationModelError, match=message):
            model._request_conversation(  # noqa: SLF001
                {"input": [{"content": "hoi", "role": "user"}]}
            )

    assert len(requests) == 1


def _memory() -> PresenceMemory:
    fact = ConfirmedFact(
        fact_id="hindsight:memory-1",
        subject="jaap",
        predicate="memory.observation",
        value={"text": "Jaap houdt niet van formele antwoorden."},
        source_ref="telegram:history",
        evidence_ref="hindsight:memory-1",
        observed_at=NOW,
        trust="host_policy",
    )
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="recall:1",
        query_purpose="conversation_reply",
        tenant_id="tenant",
        principal_id="jaap",
        conversation_id="conversation",
        project_id=None,
        requested_valid_at=NOW,
        admitted_fact_ids_sha256=canonical_sha256([fact.fact_id]),
        selected=[
            RecalledFact(
                rank=1,
                fact_id=fact.fact_id,
                source_event_ids=[fact.source_ref],
                trust="model_inference_unconfirmed",
                ranking_reason="test",
            ).model_dump(mode="json")
        ],
        excluded_reasons={},
        projection_version="hindsight-0.9.1-v1",
        scope_sha256=canonical_sha256({"kind": "person"}),
        created_at=NOW,
    )
    return PresenceMemory(
        recall,
        (fact,),
        "Jaap wil korte, directe en warme antwoorden zonder onnodige kopjes.",
    )


def test_direct_model_receives_recalled_memories_without_reflection(
    model_server: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    endpoint, requests = model_server
    auth_file = tmp_path / "auth.json"
    _write_access_lease(auth_file)
    model = CodexConversationModel(auth_file=auth_file, endpoint=endpoint)
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Ik heb een rotdag.",
        NOW,
    )
    identity = IdentityProfile(
        "Eva",
        "Je bent een warme digitale huisgenoot.",
        ("Wees eerlijk.",),
        80,
        load_current_identity_pack(),
    )

    result = model.generate(turn, identity, _memory(), ())

    assert result.text == "Dat klinkt echt rot."
    assert result.provider == "chatgpt-codex:gpt-5.4-mini"
    assert requests[0]["reasoning"]["effort"] == "medium"
    assert requests[0]["input"][0]["content"] == turn.text
    assert "Jaap houdt niet van formele antwoorden" in requests[0]["instructions"]
    assert "waargenomen 2026-08-23T14:00:00Z" in requests[0]["instructions"]
    assert "nieuwste betrouwbare bron" in requests[0]["instructions"]
    assert "Duurzame stijlspiegel" in requests[0]["instructions"]
    assert "Actief procedureel IdentityPack" in requests[0]["instructions"]
    assert (
        "warm, practical, curious, concise, slightly_stubborn"
        in (requests[0]["instructions"])
    )
    assert "claim alleen wat aantoonbaar is gelukt" in requests[0]["instructions"]
    assert "Jaap wil korte, directe en warme antwoorden" in requests[0]["instructions"]
    assert "kopieer geen zinnen, typefouten of boosheid" in requests[0]["instructions"]
    assert "één expliciete relevante herinnering" in requests[0]["instructions"]
    assert "Verzin dan geen voorbeeldzin" in requests[0]["instructions"]
    assert "geen ongevraagd aanbod of vraag" in requests[0]["instructions"]
    assert "nooit een generiek dienstenmenu" in requests[0]["instructions"]
    assert "eigen digitaal perspectief" in requests[0]["instructions"]
    assert "leg het werk niet terug" in requests[0]["instructions"]
    assert (
        "Vervang het doel niet door een makkelijker buurdoel"
        in (requests[0]["instructions"])
    )
    assert (
        "wat de laatste zin werkelijk van jou vraagt" in (requests[0]["instructions"])
    )
    assert (
        "Beantwoord die gevraagde eigen handeling eerst"
        in (requests[0]["instructions"])
    )
    assert "uitgesproken grens" in requests[0]["instructions"]
    assert "Verzin geen fysieke feiten" in requests[0]["instructions"]
    assert "kan geen toekomstig werk inplannen" in requests[0]["instructions"]
    assert "geen tijdstip of latere terugkoppeling" in requests[0]["instructions"]
    assert "/reflect" not in json.dumps(requests[0])
    assert (
        "antwoord als jezelf, niet vanuit een vaste antwoordvorm"
        in (requests[0]["instructions"])
    )


def test_recent_durable_conversation_is_not_denied_as_missing_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    captured: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, None]:
        captured.append(body)
        return "amberkleurige maan", None

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-after-restart",
            "Welke twee woorden moest je onthouden?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (
            RecentConversationMessage(
                "Jaap",
                "Onthoud voor deze herstartproef: amberkleurige maan.",
                "2026-08-23T13:59:00Z",
            ),
        ),
    )

    assert result.text == "amberkleurige maan"
    instructions = captured[0]["instructions"]
    assert "duurzame gesprekjournal" in instructions
    assert "een herstart van core of databasecontainer overleven" in instructions
    assert "niet kunt onthouden" in instructions
    assert "amberkleurige maan" in instructions


def test_document_work_action_is_model_chosen_but_episode_bound(
    model_server: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    endpoint, requests = model_server
    auth_file = tmp_path / "auth.json"
    _write_access_lease(auth_file)
    model = CodexConversationModel(auth_file=auth_file, endpoint=endpoint)
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Ga hiermee door.",
        NOW,
    )
    identity = IdentityProfile(
        "Eva",
        "Je bent een warme digitale huisgenoot.",
        ("Wees eerlijk.",),
        80,
        load_current_identity_pack(),
    )

    model.generate(
        turn,
        identity,
        _memory(),
        (),
        available_actions=("checkpoint_document_task",),
        self_context=(
            "Direct actieve documenttaak in dit gesprek: checklist.md.",
        ),
    )

    assert requests[0]["tools"][0]["name"] == "checkpoint_document_task"
    instructions = requests[0]["instructions"]
    assert "oudere globale taak" in instructions
    assert "Direct actieve documenttaak in dit gesprek" in instructions


def test_direct_model_accepts_an_explicit_bounded_reasoning_effort(
    model_server: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    endpoint, requests = model_server
    auth_file = tmp_path / "auth.json"
    _write_access_lease(auth_file)
    model = CodexConversationModel(
        auth_file=auth_file, endpoint=endpoint, reasoning_effort="high"
    )
    model.generate(
        ConversationTurn.private(
            "tenant", "installation", "jaap", "conversation", "message", "Hoi", NOW
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
    )
    assert requests[0]["reasoning"]["effort"] == "high"

    with pytest.raises(ValueError, match="reasoning effort"):
        CodexConversationModel(
            auth_file=auth_file,
            endpoint=endpoint,
            reasoning_effort="extreme",  # type: ignore[arg-type]
        )


def test_memory_query_planner_expands_meaning_without_answering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", ConversationActionCall(
            "memory-query-1",
            "submit_memory_search_plan",
            {
                "needs_semantic_memory": True,
                "query_text": (
                    "overkapping veranda buitenruimte temperatuur warm koel voorkeur"
                ),
            },
        )

    monkeypatch.setattr(model, "_request", request)
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-memory-query",
        "Welke kant moet de temperatuur onder de overkapping volgens mij op?",
        NOW,
    )

    plan = model.expand_memory_query(turn, ())

    assert plan.needs_semantic_memory is True
    assert plan.query_text is not None
    assert "veranda" in plan.query_text
    assert bodies[0]["reasoning"]["effort"] == "low"
    assert bodies[0]["tool_choice"]["name"] == "submit_memory_search_plan"
    assert "verzin nooit de gekozen waarde" in bodies[0]["instructions"]
    assert "natuurlijke vervolgzinnen zonder vraagteken" in bodies[0]["instructions"]


def test_model_reports_an_expired_access_lease_without_provider_call(
    tmp_path: Path,
) -> None:
    def encoded(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    token = f"{encoded({'alg': 'none'})}.{encoded({'exp': 1})}.signature"
    auth_file = tmp_path / "lease.json"
    auth_file.write_text(
        json.dumps(
            {
                "access_token": token,
                "account_id": "account",
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "0" * 64,
            }
        )
    )
    auth_file.chmod(0o640)

    model = CodexConversationModel(auth_file=auth_file)

    status = model.credential_status(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert status.ready is False
    assert status.state == "expired"


def test_model_rejects_a_full_refresh_authority_file(tmp_path: Path) -> None:
    auth_file = tmp_path / "full-auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "access",
                    "account_id": "account",
                    "refresh_token": "refresh-must-stay-with-owner",
                }
            }
        )
    )
    auth_file.chmod(0o640)

    status = CodexConversationModel(auth_file=auth_file).credential_status()

    assert status.ready is False
    assert status.state == "invalid"


def test_provider_rejection_latches_until_the_access_lease_rotates(
    tmp_path: Path,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth_file = tmp_path / "lease.json"
        auth_file.write_text(
            json.dumps(
                {
                    "access_token": "rejected-access",
                    "account_id": "account",
                    "contract": "EvaModelAccessLease/v1",
                    "source_sha256": "1" * 64,
                }
            )
        )
        auth_file.chmod(0o640)
        host, port = server.server_address
        model = CodexConversationModel(
            auth_file=auth_file, endpoint=f"http://{host}:{port}/responses"
        )

        with pytest.raises(ConversationModelError, match="credentials were rejected"):
            model._request({})
        assert model.credential_status().state == "rejected"

        replacement = auth_file.with_suffix(".new")
        replacement.write_text(
            json.dumps(
                {
                    "access_token": "rotated-access",
                    "account_id": "account",
                    "contract": "EvaModelAccessLease/v1",
                    "source_sha256": "2" * 64,
                }
            )
        )
        replacement.chmod(0o640)
        replacement.replace(auth_file)
        assert model.credential_status().state == "ready"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_stream_read_timeout_is_a_bounded_provider_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class TimedOutStream:
        def __iter__(self) -> "TimedOutStream":
            return self

        def __next__(self) -> bytes:
            raise TimeoutError("synthetic stream read timeout")

        def close(self) -> None:
            pass

    auth_file = tmp_path / "lease.json"
    _write_access_lease(auth_file)
    model = CodexConversationModel(auth_file=auth_file)
    monkeypatch.setattr(
        conversation_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: TimedOutStream(),
    )

    with pytest.raises(ConversationModelError, match="provider is unavailable"):
        model._request({})


def test_model_can_choose_a_verified_switch_without_treating_it_as_a_light(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    captured: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        captured.append(body)
        return "", ConversationActionCall(
            "switch-call-1",
            "set_home_assistant_switch",
            {"state": "on", "target": "verwarming in de overkapping"},
        )

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-switch",
            "Zet de verwarming in de overkapping aan.",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (
            RecentConversationMessage(
                "Jaap", "Het is warm in de overkapping.", "2026-08-23T13:59:00Z"
            ),
            RecentConversationMessage(
                "Eva",
                "Ik kan de verwarming in de overkapping uitzetten.",
                "2026-08-23T13:59:30Z",
            ),
        ),
        available_actions=("set_home_assistant_switch",),
    )

    assert result.action_call is not None
    assert result.action_call.name == "set_home_assistant_switch"
    assert captured[0]["tools"][0]["name"] == "set_home_assistant_switch"
    assert "sloten of alarmsystemen" in captured[0]["instructions"]
    assert (
        "neemt het concrete apparaat en de richting over"
        in (captured[0]["instructions"])
    )
    assert "geen nieuwe raadselvraag" in captured[0]["instructions"]


def test_model_can_choose_bounded_home_assistant_history_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    captured: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        captured.append(body)
        return "", ConversationActionCall(
            "history-call-1",
            "read_home_assistant_history",
            {
                "entity_kind": "light",
                "period": "yesterday",
                "target": "licht in de overkapping",
                "transition_to": "off",
            },
        )

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-history",
            "Hoe laat ging gisteren het licht in de overkapping uit?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("read_home_assistant_history",),
    )

    assert result.action_call is not None
    assert result.action_call.arguments["period"] == "yesterday"
    assert captured[0]["tools"][0]["name"] == "read_home_assistant_history"
    assert "Gok niet vanuit herinneringen" in captured[0]["instructions"]
    assert (
        "behoud bijvoorbeeld 'licht in de overkapping'"
        in (
            captured[0]["tools"][0]["parameters"]["properties"]["target"]["description"]
        )
    )


def test_model_can_choose_current_home_assistant_state_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    captured: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        captured.append(body)
        return "", ConversationActionCall(
            "state-call-1",
            "read_home_assistant_state",
            {"entity_kind": "switch", "target": "verwarming in de overkapping"},
        )

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-state",
            "Staat de verwarming in de overkapping nu aan?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("read_home_assistant_state",),
    )

    assert result.action_call is not None
    assert result.action_call.arguments["entity_kind"] == "switch"
    assert captured[0]["tools"][0]["name"] == "read_home_assistant_state"
    assert "niet op oude gesprekscontext" in captured[0]["instructions"]


def test_dutch_identity_retries_an_accidental_foreign_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    responses = iter(
        (
            ("Om 16:29 en nog ერთხელ om 16:35.", None),
            ("Om 16:29 en nogmaals om 16:35.", None),
        )
    )
    captured: list[dict[str, Any]] = []

    def request(body: dict[str, Any]):  # type: ignore[no-untyped-def]
        captured.append(dict(body))
        return next(responses)

    monkeypatch.setattr(model, "_request", request)
    pack = load_current_identity_pack()
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-language",
            "Hoe laat ging de lamp uit?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80, pack),
        _memory(),
        (),
    )

    assert result.text == "Om 16:29 en nogmaals om 16:35."
    assert len(captured) == 2
    assert "geen Markdown-opmaak" in captured[0]["instructions"]
    assert "volledig in natuurlijk Nederlands" in captured[1]["instructions"]


def test_model_action_roundtrip_uses_durable_receipt_before_visible_text(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(size)))
            if len(requests) == 1:
                frames = (
                    "event: response.output_item.done\n"
                    'data: {"type":"response.output_item.done","item":'
                    '{"type":"function_call","call_id":"call_interest_1",'
                    '"name":"adopt_personal_interest","arguments":'
                    '"{\\"motivation\\":\\"Ik hou van patronen.\\",'
                    '\\"next_exploration\\":\\"Welke restjes combineren goed?\\",'
                    '\\"title\\":\\"Slim koken\\"}"}}\n\n'
                    "data: [DONE]\n\n"
                ).encode()
            else:
                frames = (
                    "event: response.output_text.delta\n"
                    'data: {"type":"response.output_text.delta",'
                    '"delta":"Ik kies slim koken; dat staat nu echt vast."}\n\n'
                    "data: [DONE]\n\n"
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth_file = tmp_path / "auth.json"
        _write_access_lease(auth_file)
        host, port = server.server_address
        model = CodexConversationModel(
            auth_file=auth_file, endpoint=f"http://{host}:{port}/responses"
        )
        turn = ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-interest",
            "Kies zelf een hobby.",
            NOW,
        )
        identity = IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        )

        chosen = model.generate(
            turn,
            identity,
            _memory(),
            (),
            available_actions=("adopt_personal_interest",),
        )
        assert chosen.text is None
        assert chosen.action_call is not None
        assert chosen.action_call.arguments["title"] == "Slim koken"
        receipt = ConversationActionReceipt(
            chosen.action_call,
            {
                "contract": "EvaConversationActionReceipt/v1",
                "disposition": "adopted",
                "durable": True,
                "intent_id": "personal-interest:1",
                "motivation": chosen.action_call.arguments["motivation"],
                "next_exploration": chosen.action_call.arguments["next_exploration"],
                "state": "active",
                "title": chosen.action_call.arguments["title"],
            },
        )
        expressed = model.generate(
            turn, identity, _memory(), (), completed_action=receipt
        )

        assert expressed.text == "Ik kies slim koken; dat staat nu echt vast."
        assert expressed.action_call is None
        assert requests[0]["tools"][0]["name"] == "adopt_personal_interest"
        assert requests[0]["tool_choice"] == "auto"
        assert requests[1]["tool_choice"] == "none"
        assert requests[1]["input"][1]["type"] == "function_call"
        assert requests[1]["input"][2]["type"] == "function_call_output"
        assert '"durable":true' in requests[1]["input"][2]["output"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_passive_house_event_is_forced_through_bounded_shadow_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", ConversationActionCall(
            "will-call-1",
            "submit_sensory_decision",
            {
                "disposition": "silence",
                "expression": None,
                "reason": "Een gewone lampwisseling vraagt niet om aandacht.",
                "urgency": 0,
            },
        )

    monkeypatch.setattr(model, "_request", request)
    event = SensoryEvent(
        "home-assistant",
        "home-assistant:event-1",
        "state_changed",
        datetime(2026, 8, 23, 14, 0, tzinfo=UTC),
        {
            "entity_id": "light.kantoor_links",
            "friendly_name": "Kantoor links",
            "new_state": "on",
            "old_state": "off",
        },
    )
    identity = IdentityProfile(
        "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
    )

    decision = model.evaluate_sensory_event(event, identity, _memory())

    assert decision.disposition == "silence"
    assert decision.urgency == 0
    assert decision.provider == "chatgpt-codex:gpt-5.4-mini"
    assert bodies[0]["tool_choice"]["name"] == "submit_sensory_decision"
    assert bodies[0]["parallel_tool_calls"] is False
    assert "transportbewijs" in bodies[0]["instructions"]


def test_explicit_automation_request_uses_exact_bounded_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []
    call = ConversationActionCall(
        "automation-call-1",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "lamp in de hal",
            "action_service": "turn_on",
            "alias": "Hal aan bij thuiskomst",
            "trigger_entity_id": "Jaap",
            "trigger_to": "home",
        },
    )

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-automation",
        "Maak een automatisering: als Jaap thuiskomt, zet de lamp in de hal aan.",
        NOW,
    )

    result = model.generate(
        turn,
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("create_home_assistant_state_automation",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == ("create_home_assistant_state_automation")
    assert "natuurlijke namen" in bodies[0]["instructions"]
    assert "exacte Home Assistant-entiteiten" in bodies[0]["instructions"]


def test_natural_if_then_request_does_not_require_the_word_automation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []
    call = ConversationActionCall(
        "automation-call-natural-1",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "oefenventilator",
            "action_service": "turn_on",
            "alias": "Ventilator bij proefdeur",
            "trigger_entity_id": "proefdeur",
            "trigger_to": "on",
        },
    )

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-natural-automation",
            (
                "Wil je regelen dat mijn oefenventilator aangaat zodra de proefdeur "
                "opengaat?"
            ),
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("create_home_assistant_state_automation",),
    )

    assert result.action_call == call
    instructions = bodies[0]["instructions"]
    assert "hoeft het woord automatisering niet te noemen" in instructions
    assert "beweer dan niet dat de Home Assistant-koppeling ontbreekt" in instructions


def test_explicit_multistep_automation_uses_advanced_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "advanced-automation-call-1",
        "create_home_assistant_advanced_state_automation",
        {
            "actions": [
                {
                    "delay_after_seconds": 30,
                    "entity_id": "lamp in de hal",
                    "service": "turn_on",
                },
                {
                    "delay_after_seconds": 0,
                    "entity_id": "koffiehoek",
                    "service": "turn_on",
                },
            ],
            "alias": "Avondroute",
            "condition_entity_id": "nachtmodus",
            "condition_state": "on",
            "trigger_entity_id": "Jaap",
            "trigger_from": "not_home",
            "trigger_to": "home",
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-advanced-automation",
            (
                "Als Jaap thuiskomt terwijl nachtmodus aan is, zet de lamp in de hal "
                "aan, wacht 30 seconden en zet daarna de koffiehoek aan."
            ),
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("create_home_assistant_advanced_state_automation",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == (
        "create_home_assistant_advanced_state_automation"
    )
    properties = bodies[0]["tools"][0]["parameters"]["properties"]
    assert properties["trigger_kind"]["enum"] == [
        "state",
        "time",
        "numeric_state",
    ]
    assert properties["trigger_at"]["type"] == ["string", "null"]
    assert "geen templates" in bodies[0]["instructions"]
    assert "wachten na die actie" in bodies[0]["instructions"]
    assert "natuurlijke namen" in bodies[0]["instructions"]


def test_verified_automation_receipt_is_evidence_not_a_robotic_reply_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, None]:
        bodies.append(body)
        return "Staat klaar. Zodra er beweging is, gaat de testschakelaar aan.", None

    monkeypatch.setattr(model, "_request", request)
    call = ConversationActionCall(
        "automation-call-receipt-1",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "Eva2 testschakelaar",
            "action_service": "turn_on",
            "alias": "Testschakelaar bij beweging",
            "trigger_entity_id": "Eva2 testbeweging",
            "trigger_to": "on",
        },
    )
    receipt = ConversationActionReceipt(
        call,
        {
            "status": "verified",
            "automation_id": "eva2_internal_7f1a",
            "config_sha256": "a" * 64,
            "verified_config": {
                "alias": "Testschakelaar bij beweging",
                "action": [
                    {
                        "service": "switch.turn_on",
                        "target": {"entity_id": "switch.eva2_test_target"},
                    }
                ],
                "trigger": [
                    {
                        "entity_id": "binary_sensor.eva2_test_motion",
                        "platform": "state",
                        "to": "on",
                    }
                ],
            },
        },
    )

    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-automation-receipt",
            "Regel dat de testschakelaar aangaat zodra er beweging is.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=receipt,
    )

    assert result.text == (
        "Staat klaar. Zodra er beweging is, gaat de testschakelaar aan."
    )
    instructions = bodies[0]["instructions"]
    assert "receipt uitsluitend als bewijs, nooit als antwoordvorm" in instructions
    assert "geen entity-ID" in instructions
    assert "Trigger, Voorwaarden en Actie" in instructions
    assert "één of twee natuurlijke zinnen" in instructions
    assert bodies[0]["tool_choice"] == "none"


def test_full_tuya_request_selects_read_only_advanced_plan_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "tuya-plan-call-1",
        "plan_tuya_to_localtuya_migration",
        {"scope": "all_tuya_devices"},
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-tuya",
            "Zet alles van Tuya naar LocalTuya.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("plan_tuya_to_localtuya_migration",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == "plan_tuya_to_localtuya_migration"
    assert "verandert nog geen apparaat" in bodies[0]["instructions"]


def test_tuya_receipt_is_factual_input_not_a_robotic_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, None]:
        bodies.append(body)
        return "Ik heb de inventaris bekeken; er is nog niets veranderd.", None

    monkeypatch.setattr(model, "_request", request)
    call = ConversationActionCall(
        "tuya-plan-call-2",
        "plan_tuya_to_localtuya_migration",
        {"scope": "all_tuya_devices"},
    )
    receipt = ConversationActionReceipt(
        call,
        {
            "plan": {
                "devices": [
                    {"route": "localtuya_candidate"},
                    {"route": "matter_review"},
                    {"route": "rebuild_scene"},
                ],
                "blockers": ["localtuya_package_not_installed"],
            }
        },
    )

    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-tuya-receipt",
            "Zet alles van Tuya naar LocalTuya.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=receipt,
    )

    assert result.text == "Ik heb de inventaris bekeken; er is nog niets veranderd."
    instructions = bodies[0]["instructions"]
    assert "feiten en grenzen, geen voorgeschreven tekst" in instructions
    assert "som niet automatisch alles op" in instructions


def test_explicit_followup_can_select_exact_localtuya_package_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "localtuya-package-call-1",
        "install_approved_localtuya_package",
        {"version": "v5.2.5"},
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-localtuya-package",
            "Ik geef expliciet toestemming: installeer LocalTuya v5.2.5.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("install_approved_localtuya_package",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == "install_approved_localtuya_package"
    assert "expliciet zegt" in bodies[0]["instructions"]


def test_explicit_followup_can_register_only_exact_localtuya_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "localtuya-repository-call-1",
        "register_approved_localtuya_repository",
        {
            "expected_version": "2026.7.0",
            "repository_full_name": "xZetsubou/hass-localtuya",
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-localtuya-repository",
            (
                "Voeg xZetsubou/hass-localtuya 2026.7.0 als HACS-repository toe; "
                "installeer nog niets."
            ),
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("register_approved_localtuya_repository",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == (
        "register_approved_localtuya_repository"
    )
    assert "alleen de bron toe" in bodies[0]["instructions"]


def test_explicit_fanout_request_selects_one_multi_recipient_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "fanout-call-1",
        "deliver_test_message_fanout",
        {
            "message": "Controlebericht",
            "recipients": ["sink-a", "sink-b", "sink-c"],
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-fanout",
            "Stuur Controlebericht naar sink-a, sink-b en sink-c.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("deliver_test_message_fanout",),
    )

    assert result.action_call == call
    assert bodies[0]["tools"][0]["name"] == "deliver_test_message_fanout"
    assert "geen algemeen succes" in bodies[0]["instructions"]


def test_fanout_receipt_keeps_provider_acceptance_distinct_from_human_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, None]:
        bodies.append(body)
        return "sink-a en sink-b accepteerden het bericht.", None

    monkeypatch.setattr(model, "_request", request)
    call = ConversationActionCall(
        "fanout-call-2",
        "deliver_test_message_fanout",
        {"message": "Test", "recipients": ["sink-a", "sink-b"]},
    )
    model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-fanout-receipt",
            "Stuur Test naar sink-a en sink-b.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=ConversationActionReceipt(
            call,
            {
                "accepted_count": 2,
                "rejected_count": 0,
                "receipts": [],
            },
        ),
    )

    assert "nooit ‘ontvangen’" in bodies[0]["instructions"]


def test_future_condition_selects_bounded_durable_goal_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "goal-call-1",
        "create_bounded_durable_goal",
        {
            "delivery_recipient": "sink-a",
            "description": "Maak na vrijgave één checklist",
            "pivot": None,
            "primary_instruction": "Maak één checklist",
            "wake_deadline": "2026-08-24T08:00:00Z",
            "wake_expected_value": "yes",
            "wake_field": "ready",
            "wake_resource": "release-1",
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", call

    monkeypatch.setattr(model, "_request", request)
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-goal",
            "Zodra release-1 ready is, maak één checklist en bezorg die aan sink-a.",
            "2026-08-24T07:00:00Z",
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=(
            "create_bounded_durable_goal",
            "cancel_active_durable_goal",
        ),
    )

    assert result.action_call == call
    assert [tool["name"] for tool in bodies[0]["tools"]] == [
        "create_bounded_durable_goal",
        "cancel_active_durable_goal",
    ]
    assert "vraag de gebruiker niet om later opnieuw" not in bodies[0]["instructions"]
    assert "precies één deadline" in bodies[0]["instructions"]


def test_durable_goal_receipt_does_not_claim_work_already_finished(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, None]:
        bodies.append(body)
        return "Ik wacht op de vrijgave en ga daarna aan de slag.", None

    monkeypatch.setattr(model, "_request", request)
    call = ConversationActionCall(
        "goal-call-2",
        "create_bounded_durable_goal",
        {
            "delivery_recipient": "sink-a",
            "description": "Maak één checklist",
            "pivot": None,
            "primary_instruction": "Maak één checklist",
            "wake_deadline": "2026-08-24T08:00:00Z",
            "wake_expected_value": "yes",
            "wake_field": "ready",
            "wake_resource": "release-1",
        },
    )
    model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-goal-receipt",
            "Zodra release-1 ready is, maak één checklist.",
            "2026-08-24T07:00:00Z",
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=ConversationActionReceipt(
            call,
            {
                "contract": "EvaDurableGoalActionReceipt/v1",
                "disposition": "scheduled",
                "status": "waiting",
            },
        ),
    )

    assert "Beweer niet dat het werk al klaar" in bodies[0]["instructions"]
    assert "vraag de gebruiker niet om later opnieuw" in bodies[0]["instructions"]


def test_model_builds_complete_download_instead_of_another_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    interest = PersonalInterestClaim(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "personal-interest:menu",
        "intent:personal-interest:menu",
        "Slimme thuistemplates",
        "Ik wil zelf nuttige digitale producten maken.",
        "Welke eerste template is het meest bruikbaar?",
        NOW,
        1,
    )
    claim = PersonalPrototypeClaim(
        CompletedPersonalExploration(
            interest,
            PersonalInterestExploration(
                "Maak een weekmenuplanner met gekoppelde boodschappenlijst.",
                "test-model",
            ),
            3,
        ),
        4,
    )
    action = ConversationActionCall(
        "prototype-call",
        "submit_personal_prototype",
        {
            "content": "# Mijn weekmenu\n\n| Dag | Gerecht |\n|---|---|\n| Ma | ___ |",
            "filename": "eva-weekmenu.md",
            "media_type": "text/markdown",
            "title": "Eva's weekmenuplanner",
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", action

    monkeypatch.setattr(model, "_request", request)

    prototype = model.build_prototype(claim)

    assert prototype.filename == "eva-weekmenu.md"
    assert prototype.content.startswith("# Mijn weekmenu")
    assert prototype.content.endswith("\n")
    assert bodies[0]["tool_choice"] == {
        "name": "submit_personal_prototype",
        "type": "function",
    }
    assert "volledige inhoud, niet een plan" in bodies[0]["instructions"]


def test_model_listing_cannot_claim_unproven_sales_or_product_features(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    interest = PersonalInterestClaim(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "personal-interest:menu",
        "intent:personal-interest:menu",
        "Slimme thuistemplates",
        "Ik wil zelf nuttige digitale producten maken.",
        "Welke eerste template is bruikbaar?",
        NOW,
        1,
    )
    exploration = CompletedPersonalExploration(
        interest,
        PersonalInterestExploration("Maak een weekmenuplanner.", "test-model"),
        3,
    )
    prototype = PersonalPrototype(
        "Eva's weekmenuplanner",
        "eva-weekmenu.md",
        "text/markdown",
        "# Weekmenu\n\nMaandag: ___\n",
        "test-model",
    )
    claim = PersonalProductReleaseClaim(
        CompletedPersonalPrototype(
            PersonalPrototypeClaim(exploration, 4), prototype, 5
        ),
        6,
    )
    action = ConversationActionCall(
        "listing-call",
        "submit_personal_product_listing",
        {
            "description": "Een compacte invulbare weekmenuplanner.",
            "headline": "Weekmenu zonder gedoe",
            "price_eur_cents": 499,
            "tags": ["weekmenu", "planner", "boodschappen"],
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", action

    monkeypatch.setattr(model, "_request", request)

    listing = model.prepare_product_listing(claim)

    assert listing.price_eur_cents == 499
    assert listing.tags == ("weekmenu", "planner", "boodschappen")
    assert prototype.content in bodies[0]["input"][0]["content"]
    assert "Claim geen klanten, omzet" in bodies[0]["instructions"]


def test_web_research_uses_built_in_search_and_requires_cited_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = CodexConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]):  # type: ignore[no-untyped-def]
        bodies.append(body)
        return (
            "# Vergelijking\n\nBronfeit: drie officiële opties. Mijn gevolgtrekking: B.",
            (
                ResearchSource("Maker A", "https://a.example/docs"),
                ResearchSource("Maker B", "https://b.example/docs"),
                ResearchSource("Maker C", "https://c.example/docs"),
            ),
            (),
        )

    monkeypatch.setattr(model, "_request_research", request)
    artifact = model.research_current_options(
        question="Vergelijk drie actuele opties.",
        title="Drie actuele opties",
        options=("A", "B", "C"),
        researched_at=NOW,
    )

    assert artifact.content_sha256
    assert len(artifact.sources) == 3
    assert bodies[0]["tools"] == [
        {"search_context_size": "medium", "type": "web_search"}
    ]
    assert bodies[0]["include"] == ["web_search_call.action.sources"]
    assert "primaire bron" in bodies[0]["instructions"]
    assert "gevolgtrekking" in bodies[0]["instructions"]


def test_web_research_sse_parser_preserves_clickable_citations() -> None:
    annotations = [
        {"type": "url_citation", "title": "A", "url": "https://a.example/x"},
        {"type": "url_citation", "title": "B", "url": "https://b.example/x"},
        {"type": "url_citation", "title": "C", "url": "https://c.example/x"},
    ]
    item = {
        "type": "response.output_item.done",
        "item": {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "Onderzoek met drie bronnen.",
                    "annotations": annotations,
                }
            ],
        },
    }
    response = BytesIO(
        ("data: " + json.dumps(item) + "\n\ndata: [DONE]\n\n").encode()
    )

    text, sources, consulted = CodexConversationModel._parse_research_sse(response)

    assert text == "Onderzoek met drie bronnen."
    assert [source.url for source in sources] == [
        "https://a.example/x",
        "https://b.example/x",
        "https://c.example/x",
    ]
    assert consulted == ()
