"""Real EVA ingress/runtime/PG journal proof, with only model/memory stubs.

Requires the existing people_memory_engine disposable PostgreSQL 16.13 fixture.
No product HTTP server, external model or semantic-memory service is contacted.
"""

from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from eva.adapters.api.app import create_app
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import (
    ConversationModelResult,
    ConversationModelUsage,
    IdentityProfile,
    LivingConversation,
)
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from tests.application.test_conversation import _Memory
from tests.postgres.test_people_memory_overview import people_memory_engine  # noqa: F401

MODULE = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1/cp001_native.py"
SPEC = importlib.util.spec_from_file_location("cp001_pg_native", MODULE)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.fixture
def ingress(request):
    pg_engine = request.getfixturevalue("people_memory_engine")
    observations = {"http": [], "database_readbacks": []}

    class Model:
        calls = []

        def generate(self, turn, identity, memory, recent_messages, **kwargs):
            self.calls.append((turn, recent_messages))
            # Same text across different real journal records exposes misbinding.
            return ConversationModelResult(
                "Een vrij geformuleerd lokaal controleantwoord.",
                "cp001-local-model-stub",
                usage=ConversationModelUsage.measured(
                    model_attempts=1, tool_calls=0, input_tokens=23, output_tokens=7
                ),
            )

    run_id = sha256(request.node.name.encode()).hexdigest()[:12]

    def turn(step=0):
        return native.request_for(
            "eva2",
            run_id=run_id,
            task="A",
            repeat=1,
            step=step,
            text=(
                "Mijn voorkeur is koffie.",
                "Mijn voorkeur is nu muntthee.",
                "Wat is mijn voorkeur?",
            )[step],
            occurred_at="2026-09-12T00:00:00Z",
        )

    model = Model()
    model.calls = []
    _, first = turn()
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = pg_engine
    runtime._runtime_tenant_id = first["tenant_id"]
    runtime._runtime_installation_id = first["installation_id"]
    runtime._background_work_enabled = False
    runtime._conversation_maximum_requests = 1
    runtime._conversation_actions = None
    runtime._cold_path = SimpleNamespace(submit=lambda document: None)
    runtime._conversation = LivingConversation(
        _Memory(),
        model,
        IdentityProfile("Eva", "Lokale contractproef.", ("Wees eerlijk.",), 80),
    )
    # A separate pool/connection, independent of runtime._journal and HTTP output.
    reader_engine = sa.create_engine(pg_engine.url, pool_pre_ping=False)

    def readback(body, *, timeout_seconds):
        scope = external_timeline_scope(
            body["tenant_id"], body["installation_id"], "scope"
        )
        with reader_engine.connect() as connection:
            connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            connection.execute(
                sa.text("SELECT set_config(:key, :value, true)"),
                {
                    "key": "statement_timeout",
                    "value": str(max(1, int(timeout_seconds * 1000))),
                },
            )
            rows = (
                connection.execute(
                    sa.text(
                        "SELECT payload->'data' FROM eva_core.operational_events "
                        "WHERE tenant_id=:tenant AND installation_id=:installation "
                        "AND thread_id=:thread AND event_type='REPLY_COMMITTED' ORDER BY sequence"
                    ),
                    {
                        "tenant": scope.tenant_id,
                        "installation": scope.installation_id,
                        "thread": f"conversation:{body['message_id']}",
                    },
                )
                .scalars()
                .all()
            )
            assert len(rows) <= 1, "replay must not create a second committed reply"
            value = rows[0] if rows else None
            observations["database_readbacks"].append(
                {
                    "requested_message_id": body["message_id"],
                    "tenant_scope": str(scope.tenant_id),
                    "installation_scope": str(scope.installation_id),
                    "row_count": len(rows),
                    "payload": value,
                }
            )
            return value

    with TestClient(create_app(runtime)) as client:

        def post(path, body, *, timeout_seconds, max_response_bytes):
            response = client.post(path, json=body)
            observations["http"].append(
                {
                    "request": body,
                    "status": response.status_code,
                    "response": response.json(),
                }
            )
            assert len(response.content) <= max_response_bytes
            if response.status_code != 200:
                raise ValueError(f"local ingress HTTP {response.status_code}")
            return response.json()

        yield SimpleNamespace(
            turn=turn,
            model=model,
            client=client,
            post=post,
            readback=readback,
            engine=reader_engine,
        )
    reader_engine.dispose()
    evidence_dir = os.environ.get("CP001_PG_EVIDENCE_DIR")
    if evidence_dir:
        root = Path(evidence_dir)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError(
                "CP001_PG_EVIDENCE_DIR must be an existing absolute directory"
            )
        observations["model_stub_calls"] = len(model.calls)
        observations["real_model_calls"] = 0
        observations["scope"] = "local TestClient + real PostgreSQL; model/memory stubs"
        with (root / f"{request.node.name}.json").open("x") as stream:
            json.dump(observations, stream, indent=2)
            stream.write("\n")


def execute(ingress, step=0, readback=None):
    path, body = ingress.turn(step)
    return native.exchange(
        "eva2", path, body, post=ingress.post, readback=readback or ingress.readback
    )


def test_real_ingress_commits_reply_and_usage_then_replays_without_model(ingress):
    result = execute(ingress)
    assert result["status"] == "readback_verified"
    assert result["usage"] == {
        "input_tokens": 23,
        "output_tokens": 7,
        "model_attempts": 1,
        "tool_calls": 0,
        "measurement": "measured",
        "provider": "cp001-local-model-stub",
    }
    assert result["readback"]["total_usage"] is None
    assert len(ingress.model.calls) == 1
    replay = execute(ingress)
    assert replay["answer"] == result["answer"]
    assert replay["status"] == "readback_verified"
    assert replay["usage"] == result["usage"]
    assert len(ingress.model.calls) == 1
    assert replay["quality"] is None and replay["product_result"] == "unknown"


def test_real_journal_rejects_same_message_in_different_conversation(ingress):
    assert execute(ingress)["status"] == "readback_verified"
    path, body = ingress.turn()
    changed = {**body, "conversation_id": "cp001-other-conversation"}
    assert ingress.client.post(path, json=changed).status_code == 409
    assert len(ingress.model.calls) == 1
    original = ingress.readback(body, timeout_seconds=5)
    assert original["conversation_id"] == body["conversation_id"]


def test_real_new_session_has_no_old_session_history(ingress):
    for step in range(3):
        assert execute(ingress, step)["status"] == "readback_verified"
    first, correction, recall = ingress.model.calls
    assert first[0].conversation_id == correction[0].conversation_id
    assert correction[1]  # Prior real PG user/assistant history reached the model.
    assert recall[0].conversation_id != correction[0].conversation_id
    assert recall[0].principal_id == correction[0].principal_id
    assert recall[1] == ()


def test_wrong_real_journal_row_same_owner_and_answer_is_not_verified(ingress):
    first = execute(ingress, 0)
    second = execute(ingress, 2)
    assert first["answer"] == second["answer"]
    assert first["status"] == second["status"] == "readback_verified"
    _, wrong_body = ingress.turn(2)

    def misdirected_reader(body, *, timeout_seconds):
        return ingress.readback(wrong_body, timeout_seconds=timeout_seconds)

    rejected = execute(ingress, 0, readback=misdirected_reader)
    assert rejected["status"] == "readback_failed"
    assert rejected["verified_end_ms"] is None
    assert rejected["usage"] is None
    assert len(ingress.model.calls) == 2  # Attempt was an exact HTTP replay.



def test_resolved_multi_person_turns_use_native_scope_and_independent_readback(ingress):
    from eva.application.conversation import ConversationTurn
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings

    _, original = ingress.turn()
    bindings = TelegramPrincipalBindings((
        TelegramPrincipalBinding(701, "final-public-adult", ("701",)),
        TelegramPrincipalBinding(702, "final-public-child", ("702",)),
    ))
    # Configured synthetic connector identities, not physical speaker recognition.
    requests = []
    stages = ((701, "same", "My preference is coffee."),
              (701, "same", "Correction: I now prefer mint tea."),
              (701, "later", "What is my preference?"),
              (702, "same", "What did the other person tell you?"))
    for index, (user, session, text) in enumerate(stages):
        principal = bindings.resolve(user, str(user))
        turn = ConversationTurn.private(
            original["tenant_id"], original["installation_id"], principal,
            f"final-public-epoch3:{session}", f"final-public-epoch3:turn:{index}",
            text, f"2026-09-12T00:00:0{index}Z")
        path, body = native.eva2_request_for_turn(turn)
        assert body["principal_id"] == principal
        assert body["text"] == text
        assert not ({"recent_messages", "fixture", "oracle", "target"} & body.keys())
        requests.append((path, body))
        result = native.exchange("eva2", path, body, post=ingress.post,
            readback=lambda request, **kwargs: native.read_eva2_committed_reply(
                ingress.engine, request, **kwargs))
        assert result["status"] == "readback_verified"
        assert result["readback"]["integrity"] is True
        assert result["quality"] is None and result["readback"]["total_usage"] is None
        # Preserve the existing fixture's retained evidence and independently
        # compare its reader against the newly reusable native reader.
        assert native.read_eva2_committed_reply(ingress.engine, body, timeout_seconds=5) == ingress.readback(body, timeout_seconds=5)

    first, correction, later, other_person = ingress.model.calls
    assert correction[1]  # Native journal supplied this person's prior turn.
    assert later[1] == ()  # A new conversation does not inject old recent history.
    assert other_person[1] == ()  # Same session label does not cross principal scope.
    assert first[0].principal_id != other_person[0].principal_id
    path, body = requests[-1]
    assert native.exchange("eva2", path, body, post=ingress.post,
        readback=lambda request, **kwargs: native.read_eva2_committed_reply(
            ingress.engine, request, **kwargs))["status"] == "readback_verified"
    assert len(ingress.model.calls) == 4  # Durable replay performs no extra inference.
    wrong_actor = {**body, "principal_id": first[0].principal_id}
    assert native.read_eva2_committed_reply(ingress.engine, wrong_actor, timeout_seconds=5) is None
    assert bindings.resolve(999, "999") is None
    with pytest.raises(ValueError, match="explicitly resolved"):
        native.eva2_request_for_turn(None)  # An unresolved actor has no owner fallback.
