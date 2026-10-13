"""One real PG lifecycle for direct budget/stop admission, no live provider."""

from contextlib import asynccontextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import (
    ConversationExecutionLimitReached,
    ConversationStopped,
    ConversationModelUsage,
    observe_model_requests,
)
from tests.model.test_pydantic_conversation_model import (
    _adapter,
    _generate,
    _turn,
    _ExactStreamUsageModel,
)
from tests.postgres.test_people_memory_overview import people_memory_engine  # noqa: F401
from tests.support.scripted_pydantic_conversation import (
    ScriptedPhase,
    ScriptedPydanticConversation,
)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "eva2/benchmark/inspect_ai_v1"))
import cp001_accounting as accounting  # noqa: E402
import cp001_journal_admission as admission  # noqa: E402


def test_direct_limit_before_provider_usage_stop_and_replay(request, tmp_path):
    engine = request.getfixturevalue("people_memory_engine")
    turn = _turn()
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "missing")
    journal = PostgresConversationJournal(engine, scope, maximum_model_requests=2)
    source_files = {
        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
        for p in admission.SOURCES
    }
    identity = {k: getattr(turn, k) for k in admission.IDENTITY}
    observations = {}
    reader = sa.create_engine(engine.url)

    def snapshot(label):
        with reader.connect() as connection:
            rows = (
                connection.execute(
                    sa.text(
                        "SELECT tenant_id::text, installation_id::text, event_type, payload "
                        "FROM eva_core.operational_events "
                        "WHERE tenant_id=:tenant AND installation_id=:installation "
                        "AND event_type IN ('CONVERSATION_MODEL_ADMITTED','CONVERSATION_MODEL_RECORDED','CONVERSATION_STOP_REQUESTED') "
                        "AND payload->'data'->>'principal_id'=:principal "
                        "AND payload->'data'->>'conversation_id'=:conversation "
                        "AND payload->'data'->>'message_id'=:message ORDER BY thread_id, sequence"
                    ),
                    dict(
                        tenant=scope.tenant_id,
                        installation=scope.installation_id,
                        principal=turn.principal_id,
                        conversation=turn.conversation_id,
                        message=turn.message_id,
                    ),
                )
                .mappings()
                .all()
            )
        export = {
            "request": identity,
            "direct_journal": {
                "scope": {
                    "tenant_id": str(scope.tenant_id),
                    "installation_id": str(scope.installation_id),
                },
                "source_files": source_files,
                "events": [dict(row) for row in rows],
            },
        }
        report = accounting.preflight({"eva2": export})
        assert report["live_enabled"] is False
        contract = report["participants"]["eva2"]["provider_attempt_contract"]
        assert contract["source_expressions"]["request_limit"] == "1"
        assert contract["runtime_application_verified"] is False
        value = report["participants"]["eva2"]["direct_journal_admission"]
        assert value["status"] == "source_and_rows_bound"
        assert value["server_quiescent"] is None
        assert set(value["provider_attempt_limits"].values()) == {None}
        observations[label] = {"export": export, "report": report}
        return value

    try:
        assert snapshot("before_admission")["maximum_requests"] is None
        scripted = ScriptedPydanticConversation((ScriptedPhase.text("Hallo Jaap."),))

        class Observed(_ExactStreamUsageModel):
            @asynccontextmanager
            async def request_stream(self, *args, **kwargs):
                before = snapshot("before_provider_output")
                assert (
                    before["maximum_requests"] == 2 and before["admitted_requests"] == 1
                )
                assert before["unresolved_requests"] == 1
                async with super().request_stream(*args, **kwargs) as response:
                    yield response

        model = _adapter(tmp_path, scripted)
        model._model_backend = Observed(scripted.model, input_tokens=7)
        with observe_model_requests(turn, journal):
            result = _generate(model)
        measured = snapshot("first_terminal")["known_direct_usage"]
        assert measured == {k: v for k, v in result.usage.to_wire().items()}
        second = journal.begin_model_request(
            turn
        )  # Admission alone is not proof of dispatch.
        pending = snapshot("second_unresolved")
        assert pending["maximum_requests"] == 2 and pending["admitted_requests"] == 2
        assert pending["unresolved_requests"] == 1
        assert "direct_attempts_unresolved" in pending["blockers"]
        restored = PostgresConversationJournal(reader, scope, maximum_model_requests=99)
        with pytest.raises(ConversationExecutionLimitReached):
            restored.begin_model_request(turn)
        assert snapshot("limit_refused") == pending
        restored.request_turn_stop(turn)
        stopped = snapshot("stop_with_unresolved_attempt")
        assert stopped["stop_recorded"] and stopped["unresolved_requests"] == 1
        with pytest.raises(ConversationStopped):
            restored.begin_model_request(turn)
        restored.complete_model_request(
            turn, second, ConversationModelUsage.unmeasured(), outcome="failed"
        )
        finished = snapshot("all_terminal")
        assert (
            finished["terminal_requests"] == 2 and finished["unresolved_requests"] == 0
        )
        assert finished["known_direct_usage"]["measurement"] == "partial"
        assert (
            finished["known_direct_usage"]["input_tokens"] == result.usage.input_tokens
        )
        # Terminal unknown usage plus stop is still no provider-quiescence evidence.
        assert finished["server_quiescent"] is None
        restored.request_turn_stop(turn)
        restored.complete_model_request(
            turn, second, ConversationModelUsage.unmeasured(), outcome="failed"
        )
        assert snapshot("replay") == finished
        assert len(scripted.captures) == 1
        valid = observations["replay"]["export"]
        for variant in ("scope", "source", "duplicate", "checkpoint"):
            bad = copy.deepcopy(valid)
            if variant == "scope":
                bad["direct_journal"]["events"][0]["tenant_id"] = "wrong"
            elif variant == "source":
                bad["direct_journal"]["source_files"][admission.SOURCES[0]] = "0" * 64
            elif variant == "checkpoint":
                bad["direct_journal"]["events"][0]["payload"]["checkpoint"]["phase"] = (
                    "wrong"
                )
            else:
                bad["direct_journal"]["events"].append(
                    bad["direct_journal"]["events"][0]
                )
            denied = accounting.preflight({"eva2": bad})["participants"]["eva2"][
                "direct_journal_admission"
            ]
            assert denied["status"] == "rejected" and denied["maximum_requests"] is None
        if os.environ.get("CP001_ADMISSION_EVIDENCE_DIR"):
            out = Path(os.environ["CP001_ADMISSION_EVIDENCE_DIR"])
            (out / "lifecycle.json").write_text(
                json.dumps(observations, indent=2) + "\n"
            )
            (out / "native-exports.json").write_text(
                json.dumps({"eva2": valid}, indent=2) + "\n"
            )
    finally:
        reader.dispose()


def test_actual_stream_events_drive_cli_local_settlement(
    request, tmp_path, monkeypatch
):
    import asyncio
    import subprocess
    import httpx2
    from eva.adapters.model import pydantic_conversation as module
    from eva.adapters.model.openai_codex_provider import OpenAICodexProvider
    from tests.model.test_openai_codex_provider_shared import _sse
    from tests.model.test_pydantic_conversation_model import _adapter_type

    engine = request.getfixturevalue("people_memory_engine")
    turn = _turn()
    scope = external_timeline_scope("settlement-cli", "isolated", "missing")
    journal = PostgresConversationJournal(engine, scope, maximum_model_requests=3)
    sources = {
        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
        for p in admission.SOURCES
    }
    mode = ["success"]
    calls = []
    snapshots = {}
    close_error = RuntimeError("fixture close failure")

    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for chunk in _sse("Hallo Jaap."):
                yield chunk

        async def aclose(self):
            await asyncio.sleep(0)

    class Network(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, wire):
            calls.append(mode[0])
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Body(),
                request=wire,
            )

        async def aclose(self):
            await asyncio.sleep(0)
            if mode[0] == "close_failed":
                raise close_error

    class Provider(OpenAICodexProvider):
        def __init__(self, settings, *, credential_loader):
            super().__init__(
                settings,
                credential_loader=lambda: ("offline", "offline"),
                network_transport=Network(),
            )

    monkeypatch.setattr(module, "OpenAICodexProvider", Provider)
    model = _adapter_type()(auth_file=tmp_path / "unused.json")
    reader = sa.create_engine(engine.url)

    def cli_snapshot(label, expected):
        with reader.connect() as connection:
            rows = (
                connection.execute(
                    sa.text(
                        "SELECT tenant_id::text, installation_id::text, event_type, payload "
                        "FROM eva_core.operational_events WHERE tenant_id=:tenant AND installation_id=:installation "
                        "AND event_type IN ('CONVERSATION_MODEL_ADMITTED','CONVERSATION_MODEL_RECORDED','CONVERSATION_STOP_REQUESTED') "
                        "ORDER BY thread_id, sequence"
                    ),
                    dict(tenant=scope.tenant_id, installation=scope.installation_id),
                )
                .mappings()
                .all()
            )
        export = {
            "eva2": {
                "request": {k: getattr(turn, k) for k in admission.IDENTITY},
                "direct_journal": {
                    "scope": {
                        "tenant_id": str(scope.tenant_id),
                        "installation_id": str(scope.installation_id),
                    },
                    "source_files": sources,
                    "events": [dict(row) for row in rows],
                },
            }
        }
        path = tmp_path / "export.json"
        path.write_text(json.dumps(export))
        run = subprocess.run(
            [
                sys.executable,
                str(ROOT / "eva2/benchmark/inspect_ai_v1/cp001_accounting.py"),
                "--exports",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        assert run.returncode == 2 and not run.stderr
        report = json.loads(run.stdout)
        value = report["participants"]["eva2"]["direct_journal_admission"]
        assert value["status"] == "source_and_rows_bound"
        assert value["observed_local_settlement"] == expected
        assert (
            value["remote_provider_quiescent"] is None
            and value["server_quiescent"] is None
        )
        assert "helper_memory_coverage_unavailable" in value["blockers"]
        assert report["live_enabled"] is False
        snapshots[label] = {"export": export, "report": report}
        return value

    try:
        cli_snapshot("empty", "unknown")
        with observe_model_requests(turn, journal):
            _generate(model)
        first = cli_snapshot("closed", "settled")
        assert first["local_stream_closed_requests"] == 1
        mode[0] = "close_failed"
        with (
            observe_model_requests(turn, journal),
            pytest.raises(RuntimeError) as caught,
        ):
            _generate(model)
        assert caught.value is close_error
        failed = cli_snapshot("failed_close", "unknown")
        assert (
            failed["terminal_requests"] == 2
            and failed["local_stream_closed_requests"] == 1
        )
        journal.begin_model_request(turn)
        unfinished = cli_snapshot("unfinished", "unfinished")
        assert unfinished["unresolved_requests"] == 1
        journal.request_turn_stop(turn)
        stopped = cli_snapshot("stopped", "unfinished")
        journal.request_turn_stop(turn)
        assert cli_snapshot("stop_replay", "unfinished") == stopped
        assert calls == ["success", "close_failed"]
        # A string/client-shaped claim is not a native boolean observation.
        invalid = copy.deepcopy(snapshots["closed"]["export"])
        record = next(
            v
            for v in invalid["eva2"]["direct_journal"]["events"]
            if v["event_type"] == "CONVERSATION_MODEL_RECORDED"
        )
        record["payload"]["data"]["local_stream_closed"] = "true"
        assert (
            accounting.preflight(invalid)["participants"]["eva2"][
                "direct_journal_admission"
            ]["status"]
            == "rejected"
        )
        if os.environ.get("CP001_ADMISSION_EVIDENCE_DIR"):
            out = Path(os.environ["CP001_ADMISSION_EVIDENCE_DIR"])
            (out / "settlement-lifecycle.json").write_text(
                json.dumps(snapshots, indent=2) + "\n"
            )
    finally:
        reader.dispose()
