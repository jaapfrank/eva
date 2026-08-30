from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Iterator
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from eva.adapters.connectors.telegram import TelegramBotApiAdapter
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.delegated_work import (
    DelegatedWorkConflict,
    DelegatedWorkReleaseSelector,
    DelegatedWorkStoreError,
    PostgresDelegatedWorkStore,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    external_timeline_scope,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.adapters.postgres.telegram_ingress import PostgresTelegramIngressStore
from eva.adapters.subagents.tool_free_child import (
    OUTPUT_SCHEMA_SHA256,
    ClosedModelRegistry,
    PydanticToolFreeChildRunner,
    ToolFreeChildOutcome,
    ToolFreeChildProfile,
)
from eva.application.conversation import ConversationTurn
from eva.application.telegram_ingress import (
    TelegramInboundMessage,
    TelegramIngressJob,
    TelegramIngressWorker,
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands
from eva.bootstrap.delegated_work_provider import (
    build_unadmitted_delegated_work_spec,
)
from eva.contracts.delegated_work import (
    DelegatedWorkGrant,
    DelegatedWorkResult,
    DelegatedWorkSpec,
    DelegatedWorkUsage,
)
from eva.operations.delegated_work_worker import DelegatedWorkWorker
from eva.operations.install_schema import install_schema
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from sqlalchemy.engine import Engine, make_url

principals = metadata.tables["eva_core.principals"]
principal_bindings = metadata.tables["eva_core.principal_bindings"]
episode_generations = metadata.tables["eva_core.episode_generations"]
episodes = metadata.tables["eva_core.episodes"]
delegated_work_runs = metadata.tables["eva_core.delegated_work_runs"]
ROOT = Path(__file__).resolve().parents[2]
PROCESS_FIXTURE = ROOT / "tests/fixtures/subagents/delegated_work_service_fixture.py"


@pytest.fixture
def process_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="eva2-delegated-process-", dir="/tmp"))
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path)


@pytest.fixture(scope="module")
def delegated_engine() -> Engine:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if not url.database or not url.database.startswith("eva2_4b_disposable_"):
        pytest.fail("delegated work requires an explicit disposable database")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert int(connection.scalar(sa.text("SHOW server_version_num"))) == 160013
        assert not connection.scalar(
            sa.text("SELECT to_regclass('public.eva_alembic_version') IS NOT NULL")
        )
    receipt = install_schema(dsn, alembic_ini=ROOT / "alembic.ini")
    assert receipt.final_revision == "4e_0007_delegated_private"
    try:
        yield engine
    finally:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
        try:
            # The revision refuses to discard durable tasks. Test that invariant
            # before the explicit test-only cleanup used for base teardown.
            with pytest.raises(
                Exception,
                    match=(
                        "must be empty before downgrade|stop requests must be empty|"
                        "delegated private contexts cannot be represented"
                    ),
            ):
                command.downgrade(config, "4e_0002_delegated_work")
            with engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL ROLE eva_migrator")
                connection.exec_driver_sql(
                    "ALTER TABLE eva_core.delegated_work_runs "
                    "NO FORCE ROW LEVEL SECURITY"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE eva_core.attention_items "
                    "NO FORCE ROW LEVEL SECURITY"
                )
                connection.execute(sa.text("DELETE FROM eva_core.delegated_work_runs"))
                connection.execute(
                    sa.text(
                        "DELETE FROM eva_core.attention_items "
                        "WHERE kind='delegated_work'"
                    )
                )
            command.downgrade(config, "base")
        finally:
            engine.dispose()


def _id(label: str, part: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"eva2-delegated-store-{label}-{part}")


def _seed_authority(engine: Engine, label: str) -> tuple[TimelineScope, UUID, UUID]:
    scope = external_timeline_scope(
        f"tenant:{label}", f"installation:{label}", "test scope is required"
    )
    tenant = scope.tenant_id
    installation = scope.installation_id
    episode = _id(label, "episode")
    principal = _id(label, "principal")
    binding = _id(label, "binding")
    at = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    until = at + timedelta(days=7)
    tables = (principals, principal_bindings, episode_generations, episodes)
    with engine.begin() as connection:
        for table in tables:
            connection.exec_driver_sql(
                f"ALTER TABLE {table.schema}.{table.name} DISABLE TRIGGER ALL"
            )
        try:
            connection.execute(
                principals.insert().values(
                    tenant_id=tenant,
                    principal_id=principal,
                    principal_type="human",
                    status="active",
                    display_name=None,
                    privacy_class="restricted",
                    revision=1,
                    retention_policy_id="retention:test",
                    retention_until=until,
                    created_at=at,
                    updated_at=at,
                )
            )
            connection.execute(
                principal_bindings.insert().values(
                    tenant_id=tenant,
                    principal_binding_id=binding,
                    principal_id=principal,
                    provider_id="telegram",
                    channel_id="private",
                    external_subject_sha256=bytes.fromhex("11" * 32),
                    verification_evidence_id=_id(label, "verification"),
                    status="verified",
                    verified_at=at,
                    revision=1,
                    retention_policy_id="retention:test",
                    retention_until=until,
                    created_at=at,
                    updated_at=at,
                )
            )
            connection.execute(
                episode_generations.insert().values(
                    tenant_id=tenant,
                    installation_id=installation,
                    episode_id=episode,
                    generation=1,
                    predecessor_generation=None,
                    state="current",
                    correction_message_id=None,
                    workflow_contract="Eva2Noor4DWorkflow",
                    workflow_contract_version=1,
                    identity_id="eva",
                    identity_pack_version=1,
                    identity_pack_sha256=bytes.fromhex("22" * 32),
                    policy_id="policy:test",
                    policy_pack_version=1,
                    policy_pack_sha256=bytes.fromhex("33" * 32),
                    revision=1,
                    retention_policy_id="retention:test",
                    retention_until=until,
                    created_at=at,
                    updated_at=at,
                )
            )
            connection.execute(
                episodes.insert().values(
                    tenant_id=tenant,
                    installation_id=installation,
                    episode_id=episode,
                    principal_id=principal,
                    conversation_id=_id(label, "conversation"),
                    ingress_request_id=_id(label, "ingress"),
                    state="executing",
                    current_generation=1,
                    supersedes_generation=None,
                    revision=1,
                    last_event_id=_id(label, "event"),
                    state_contract="EpisodeState/v1",
                    state_schema_version=1,
                    state_payload_sha256=bytes.fromhex("44" * 32),
                    retention_policy_id="retention:test",
                    retention_until=until,
                    created_at=at,
                    updated_at=at,
                )
            )
        finally:
            for table in reversed(tables):
                connection.exec_driver_sql(
                    f"ALTER TABLE {table.schema}.{table.name} ENABLE TRIGGER ALL"
                )
    return scope, episode, binding


def _spec(
    label: str,
    scope: TimelineScope,
    episode: UUID,
    binding: UUID,
    *,
    work_suffix: str = "research",
    maximum_claims: int = 2,
) -> DelegatedWorkSpec:
    principal = _id(label, "principal")
    return DelegatedWorkSpec(
        contract="DelegatedWork/v1",
        schema_version=1,
        work_id=f"work:{label}:{work_suffix}",
        episode_id=str(episode),
        generation=1,
        parent_task_id=f"task:{label}:parent",
        owner_principal_id=str(principal),
        principal_binding_id=str(binding),
        audience="private",
        worker_name="onderzoeker",
        worker_profile_id="worker:research:v1",
        worker_profile_release_sha256="55" * 32,
        task="Vergelijk de onderhouden voice-dependencies en geef één advies.",
        model_profile_id="model:test:v1",
        model_profile_release_sha256="66" * 32,
        worker_program_sha256="77" * 32,
        engine_release_sha256="88" * 32,
        output_schema_sha256="99" * 32,
        grant=DelegatedWorkGrant(
            contract="DelegatedWorkGrant/v1",
            schema_version=1,
            context_refs=[],
            capability_ids=[],
            workspace_ref=None,
            child_network_access=False,
            maximum_model_runs=1,
            maximum_completed_model_responses=1,
            maximum_input_tokens=1_000,
            maximum_output_tokens=500,
            maximum_total_tokens=1_500,
            maximum_cost_usd_micros=10_000,
            timeout_seconds=30,
            maximum_claims_before_call_start=maximum_claims,
            delegation_depth=1,
        ).model_dump(mode="json"),
        created_at="2026-08-26T12:00:00Z",
        deadline="2026-08-26T12:10:00Z",
    )


def _release(spec: DelegatedWorkSpec) -> DelegatedWorkReleaseSelector:
    return DelegatedWorkReleaseSelector.from_spec(spec)


def _process_spec(
    label: str,
    scope: TimelineScope,
    episode: UUID,
    binding: UUID,
) -> DelegatedWorkSpec:
    base = _spec(label, scope, episode, binding)
    return DelegatedWorkSpec.model_validate(
        base.model_dump(mode="json")
        | {
            "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
            "worker_program_sha256": hashlib.sha256(
                PROCESS_FIXTURE.read_bytes()
            ).hexdigest(),
        }
    )


def _product_process_spec(
    label: str,
    scope: TimelineScope,
    episode: UUID,
    binding: UUID,
) -> DelegatedWorkSpec:
    del scope
    return build_unadmitted_delegated_work_spec(
        work_id=f"work:{label}:research",
        episode_id=str(episode),
        generation=1,
        parent_task_id=f"task:{label}:parent",
        owner_principal_id=str(_id(label, "principal")),
        principal_binding_id=str(binding),
        task="Vat dit afgeschermde providerantwoord compact samen.",
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 8, 26, 12, 10, tzinfo=UTC),
    )


@contextmanager
def _official_provider_server(
    *, body_delay: float = 0,
) -> Iterator[tuple[str, list[dict[str, Any]], threading.Event]]:
    captured: list[dict[str, Any]] = []
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size))
            captured.append(
                {
                    "path": self.path,
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            received.set()
            if body_delay:
                time.sleep(body_delay)
            created = {
                "id": "resp_vertical_1",
                "object": "response",
                "created_at": 1_777_111_200.0,
                "status": "in_progress",
                "model": "gpt-5.4-mini",
                "output": [],
                "parallel_tool_calls": False,
                "tool_choice": "none",
                "tools": [],
            }
            output = '{"summary":"officiele SDK-keten werkt","findings":["bewijs"]}'
            completed = created | {
                "status": "completed",
                "output": [
                    {
                        "id": "msg_vertical_1",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": output,
                                "annotations": [],
                                "logprobs": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 31,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 11,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 42,
                },
            }
            events = [
                {"type": "response.created", "response": created},
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_vertical_1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": output,
                    "logprobs": [],
                },
                {"type": "response.completed", "response": completed},
            ]
            encoded = (
                "".join(
                    f"event: {event['type']}\ndata: "
                    f"{json.dumps(event, separators=(',', ':'))}\n\n"
                    for event in events
                )
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/responses",
            captured,
            received,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _process_environment(
    *,
    dsn_file: Path,
    scope: TimelineScope,
    scope_label: str,
    count_file: Path,
    mode: str,
    now: str = "2026-08-26T12:02:00Z",
    after_claim_marker: Path | None = None,
) -> dict[str, str]:
    try:
        import pydantic_ai
    except ImportError:
        pytest.skip("the exact delegated worker environment is unavailable")
    worker_site_packages = str(Path(pydantic_ai.__file__).resolve().parents[1])
    expected_scope = external_timeline_scope(
        f"tenant:{scope_label}",
        f"installation:{scope_label}",
        "test scope is required",
    )
    assert scope == expected_scope
    environment = {
        name: os.environ[name]
        for name in ("LANG", "LC_ALL", "PATH", "PYTHONHASHSEED")
        if name in os.environ
    }
    environment.update(
        {
        "EVA_DATABASE_DSN_FILE": str(dsn_file),
        "EVA_RUNTIME_TENANT_ID": f"tenant:{scope_label}",
        "EVA_RUNTIME_INSTALLATION_ID": f"installation:{scope_label}",
        "EVA_DELEGATED_WORKER_ID": "subagent-worker:process-test",
        "EVA_DELEGATED_WORK_POLL_SECONDS": "0.05",
        "EVA_TEST_CALL_COUNTER": str(count_file),
        "EVA_TEST_SUBAGENT_MODE": mode,
        "EVA_TEST_NOW": now,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}:{worker_site_packages}",
        }
    )
    if after_claim_marker is not None:
        environment["EVA_TEST_AFTER_CLAIM_MARKER"] = str(after_claim_marker)
    return environment


def _official_process_environment(
    *,
    dsn_file: Path,
    scope: TimelineScope,
    scope_label: str,
    count_file: Path,
    endpoint: str,
    lease_file: Path,
) -> dict[str, str]:
    environment = _process_environment(
        dsn_file=dsn_file,
        scope=scope,
        scope_label=scope_label,
        count_file=count_file,
        mode="official_sdk",
    )
    try:
        import openai
    except ImportError:
        pytest.skip("the exact delegated provider environment is unavailable")
    sdk_site_packages = str(Path(openai.__file__).resolve().parents[1])
    if sdk_site_packages not in environment["PYTHONPATH"].split(":"):
        environment["PYTHONPATH"] = f"{environment['PYTHONPATH']}:{sdk_site_packages}"
    environment["EVA_TEST_PROVIDER_ENDPOINT"] = endpoint
    environment["EVA_TEST_PROVIDER_LEASE_FILE"] = str(lease_file)
    return environment


def _start_process(environment: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, str(PROCESS_FIXTURE)],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _wait_for_call(count_file: Path, *, timeout_seconds: float = 10) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if count_file.exists() and count_file.read_text().count("provider-call") >= 1:
            return
        time.sleep(0.02)
    pytest.fail("separate delegated worker did not start its model call")


def _wait_for_file(path: Path, *, timeout_seconds: float = 10) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    pytest.fail(f"separate delegated worker did not create {path.name}")


def _store(engine: Engine, scope: TimelineScope) -> PostgresDelegatedWorkStore:
    return PostgresDelegatedWorkStore(engine, scope)


def _admit(
    store: PostgresDelegatedWorkStore, spec: DelegatedWorkSpec
):
    return store.admit(
        spec=spec,
        title="Kies de beste voice-route",
        summary="Vergelijk de onderhouden opties en leg één advies klaar.",
        worker_role="Onderzoek",
        retention_policy_id="retention:test",
        retention_until=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        occurred_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )


def _usage() -> DelegatedWorkUsage:
    return DelegatedWorkUsage(
        contract="DelegatedWorkUsage/v1",
        schema_version=1,
        measurement="measured",
        completed_model_responses=1,
        tool_calls=0,
        input_tokens=20,
        output_tokens=8,
        total_tokens=28,
        cost_usd_micros=14,
    )


def test_migration_keeps_delete_private(delegated_engine: Engine) -> None:
    with delegated_engine.connect() as connection:
        grants = {
            (row.grantee, row.privilege_type)
            for row in connection.execute(
                sa.text(
                    "SELECT grantee, privilege_type "
                    "FROM information_schema.role_table_grants "
                    "WHERE table_schema='eva_core' "
                    "AND table_name='delegated_work_runs'"
                )
            )
        }
    for role in ("eva_runtime", "eva_dbos"):
        assert (role, "SELECT") in grants
        assert (role, "INSERT") in grants
        assert (role, "DELETE") not in grants


def test_durable_handoff_call_result_review_and_workboard(
    delegated_engine: Engine,
) -> None:
    label = "happy"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)

    admission = _admit(store, spec)
    assert admission.disposition == "inserted"
    assert _admit(store, spec).disposition == "already_present"
    with pytest.raises(DelegatedWorkConflict, match="different immutable"):
        store.admit(
            spec=spec,
            title="Een andere titel",
            summary="Vergelijk de onderhouden opties en leg één advies klaar.",
            worker_role="Onderzoek",
            retention_policy_id="retention:test",
            retention_until=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
            occurred_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        )

    claim = store.claim_next(
        worker_id="worker:test",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None
    assert store.claim_next(
        worker_id="worker:other",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
    ) is None
    call = store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC)
    )
    assert call is not None
    assert store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 3, tzinfo=UTC)
    ) is None

    # A fresh process sees call_started and never claims or calls it again.
    restarted = _store(delegated_engine, scope)
    assert restarted.claim_next(
        worker_id="worker:restart",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 1, tzinfo=UTC),
    ) is None
    before_result = restarted.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 1, tzinfo=UTC),
    )
    assert before_result.cards[0].column == "attention"
    assert before_result.cards[0].result_status == "ambiguous"

    output = "Pydantic AI Realtime eerst; bewijs daarna onderbreken en latency."
    result = DelegatedWorkResult(
        contract="DelegatedWorkResult/v1",
        schema_version=1,
        work_id=spec.work_id,
        call_id=call.call_id,
        output=output,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        spec_sha256=spec.sha256,
        usage=_usage().model_dump(mode="json"),
        completed_at="2026-08-26T12:01:10Z",
    )
    recorded = restarted.record_success(
        call, result, now=datetime(2026, 8, 26, 12, 1, 10, tzinfo=UTC)
    )
    assert recorded.state == "succeeded"
    assert restarted.record_success(
        call, result, now=datetime(2026, 8, 26, 12, 1, 10, tzinfo=UTC)
    ) == recorded
    board = restarted.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 1, 11, tzinfo=UTC),
    )
    assert board.cards[0].column == "review"
    assert board.cards[0].usage.total_tokens == 28
    assert board.cards[0].usage.cost_usd_micros == 14

    verified = restarted.verify_result(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 1, 12, tzinfo=UTC),
    )
    assert verified.state == "verified"
    done = restarted.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 1, 13, tzinfo=UTC),
    )
    assert done.cards[0].column == "done"
    assert done.cards[0].result_status == "approved"


def test_concurrent_admission_and_claim_choose_one_winner(
    delegated_engine: Engine,
) -> None:
    label = "concurrent"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    spec = _spec(label, scope, episode, binding)
    barrier = threading.Barrier(2)

    def admit() -> str:
        barrier.wait(timeout=5)
        return _admit(_store(delegated_engine, scope), spec).disposition

    with ThreadPoolExecutor(max_workers=2) as executor:
        admissions = [future.result() for future in [executor.submit(admit) for _ in range(2)]]
    assert sorted(admissions) == ["already_present", "inserted"]

    claim_barrier = threading.Barrier(2)
    def claim(worker_id: str) -> object:
        claim_barrier.wait(timeout=5)
        return _store(delegated_engine, scope).claim_next(
            worker_id=worker_id,
            release=_release(spec),
            now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = [
            future.result()
            for future in [
                executor.submit(claim, f"worker:concurrent:{ordinal}")
                for ordinal in range(2)
            ]
        ]
    assert sum(value is not None for value in claims) == 1


def test_new_worker_release_skips_old_work_until_old_worker_drains_it(
    delegated_engine: Engine,
) -> None:
    label = "release-drain"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    old_spec = _spec(label, scope, episode, binding, work_suffix="old-release")
    new_spec = DelegatedWorkSpec.model_validate(
        _spec(
            label, scope, episode, binding, work_suffix="new-release"
        ).model_dump(mode="json")
        | {
            "engine_release_sha256": "aa" * 32,
            "model_profile_release_sha256": "bb" * 32,
            "output_schema_sha256": "cc" * 32,
            "worker_profile_release_sha256": "dd" * 32,
            "worker_program_sha256": "ee" * 32,
        }
    )
    _admit(store, old_spec)
    _admit(store, new_spec)

    new_claim = store.claim_next(
        worker_id="worker:new-release",
        release=_release(new_spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert new_claim is not None
    assert new_claim.work_id == new_spec.work_id
    old_waiting = store.load(
        old_spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert old_waiting.state == "admitted"
    assert old_waiting.call_id is None
    assert old_waiting.claim_count == 0

    old_claim = store.claim_next(
        worker_id="worker:old-release-drain",
        release=_release(old_spec),
        now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
    )
    assert old_claim is not None
    assert old_claim.work_id == old_spec.work_id


def test_runtime_role_cannot_read_another_scope(delegated_engine: Engine) -> None:
    label = "rls"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    spec = _spec(label, scope, episode, binding)
    _admit(_store(delegated_engine, scope), spec)
    other_scope = TimelineScope(_id(label, "other-tenant"), scope.installation_id)
    with delegated_engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL ROLE eva_runtime")
        set_local_scope(
            connection,
            tenant_id=str(other_scope.tenant_id),
            installation_id=str(other_scope.installation_id),
        )
        count = connection.scalar(
            sa.select(sa.func.count()).select_from(delegated_work_runs)
        )
        assert count == 0


def test_result_usage_cannot_exceed_the_child_grant(
    delegated_engine: Engine,
) -> None:
    label = "budget"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    claim = store.claim_next(
        worker_id="worker:budget",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None
    call = store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC)
    )
    assert call is not None
    over_budget = DelegatedWorkUsage(
        contract="DelegatedWorkUsage/v1",
        schema_version=1,
        measurement="measured",
        completed_model_responses=1,
        tool_calls=0,
        input_tokens=1_001,
        output_tokens=0,
        total_tokens=1_001,
        cost_usd_micros=14,
    )
    output = "Dit resultaat overschreed het afgeleide budget."
    result = DelegatedWorkResult(
        contract="DelegatedWorkResult/v1",
        schema_version=1,
        work_id=spec.work_id,
        call_id=call.call_id,
        output=output,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        spec_sha256=spec.sha256,
        usage=over_budget.model_dump(mode="json"),
        completed_at="2026-08-26T12:00:03Z",
    )
    with pytest.raises(DelegatedWorkConflict, match="input token budget exceeded"):
        store.record_success(
            call,
            result,
            now=datetime(2026, 8, 26, 12, 0, 3, tzinfo=UTC),
        )


def test_revoked_binding_stops_before_call_start(delegated_engine: Engine) -> None:
    label = "revoked"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    claim = store.claim_next(
        worker_id="worker:revoked",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None
    with delegated_engine.begin() as connection:
        connection.execute(
            sa.update(principal_bindings)
            .where(
                principal_bindings.c.tenant_id == scope.tenant_id,
                principal_bindings.c.principal_binding_id == binding,
            )
            .values(status="revoked")
        )
    assert store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC)
    ) is None
    with delegated_engine.connect() as connection:
        state = connection.scalar(
            sa.select(delegated_work_runs.c.state).where(
                delegated_work_runs.c.tenant_id == scope.tenant_id,
                delegated_work_runs.c.installation_id == scope.installation_id,
                delegated_work_runs.c.work_id == spec.work_id,
            )
        )
    assert state == "needs_help"


def test_owner_stop_wins_before_call_and_never_claims_again(
    delegated_engine: Engine,
) -> None:
    label = "owner-stop"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    assert store.latest_stoppable_work_id(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    ) == spec.work_id

    stopped = store.request_stop(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert stopped.state == "stopped"
    assert stopped.call_id is None
    assert stopped.lease_owner is None
    assert store.request_stop(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
    ) == stopped
    assert store.claim_next(
        worker_id="worker:after-stop",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 3, tzinfo=UTC),
    ) is None
    card = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 4, tzinfo=UTC),
    ).cards[0]
    assert card.column == "done"
    assert card.result_status == "stopped"
    assert store.latest_stoppable_work_id(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    ) == spec.work_id


def test_owner_stop_after_claim_prevents_call_and_active_call_records_request(
    delegated_engine: Engine,
) -> None:
    label = "owner-stop-race"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    claimed_spec = _spec(label, scope, episode, binding, work_suffix="claimed")
    _admit(store, claimed_spec)
    claim = store.claim_next(
        worker_id="worker:claimed",
        release=_release(claimed_spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None
    assert store.request_stop(
        claimed_spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
    ).state == "stopped"
    assert store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 3, tzinfo=UTC)
    ) is None

    started_spec = _spec(label, scope, episode, binding, work_suffix="started")
    _admit(store, started_spec)
    started_claim = store.claim_next(
        worker_id="worker:started",
        release=_release(started_spec),
        now=datetime(2026, 8, 26, 12, 0, 4, tzinfo=UTC),
    )
    assert started_claim is not None
    call = store.start_call(
        started_claim,
        now=datetime(2026, 8, 26, 12, 0, 5, tzinfo=UTC),
    )
    assert call is not None
    stopping = store.request_stop(
        started_spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 6, tzinfo=UTC),
    )
    assert stopping.state == "call_started"
    assert stopping.stop_requested_at == datetime(2026, 8, 26, 12, 0, 6, tzinfo=UTC)
    assert store.request_stop(
        started_spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 7, tzinfo=UTC),
    ) == stopping
    loaded = store.load(
        started_spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert loaded.state == "call_started"
    assert loaded.stop_requested_at == stopping.stop_requested_at
    lost = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 1, tzinfo=UTC),
    ).cards[0]
    assert lost.column == "attention"
    assert lost.worker_health == "contact_lost"
    assert lost.result_status == "ambiguous"
    assert lost.reason == (
        "De stopopdracht is bewaard, maar het workercontact is verlopen. "
        "De beëindiging is niet bevestigd en Eva start de modelcall niet opnieuw."
    )


@pytest.mark.parametrize("terminal_kind", ["success", "failure"])
def test_owner_stop_and_terminal_commit_share_one_lock_order(
    delegated_engine: Engine,
    terminal_kind: str,
) -> None:
    label = f"owner-stop-terminal-race-{terminal_kind}"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    claim = store.claim_next(
        worker_id=f"worker:{terminal_kind}",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None
    call = store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC)
    )
    assert call is not None
    now = datetime(2026, 8, 26, 12, 0, 3, tzinfo=UTC)
    barrier = threading.Barrier(2)

    def stop() -> object:
        barrier.wait(timeout=5)
        try:
            return _store(delegated_engine, scope).request_stop(
                spec.work_id,
                owner_principal_id=_id(label, "principal"),
                principal_binding_id=binding,
                now=now,
            )
        except Exception as error:  # returned for exact race classification
            return error

    def finish() -> object:
        barrier.wait(timeout=5)
        competing_store = _store(delegated_engine, scope)
        try:
            if terminal_kind == "failure":
                return competing_store.record_failure(
                    call,
                    usage=DelegatedWorkUsage.unmeasured(),
                    error_code="provider_unavailable",
                    error_fingerprint="aa" * 32,
                    ambiguous=False,
                    now=now,
                )
            output = "De gelijktijdige modeluitkomst is duurzaam bewaard."
            result = DelegatedWorkResult(
                contract="DelegatedWorkResult/v1",
                schema_version=1,
                work_id=spec.work_id,
                call_id=call.call_id,
                output=output,
                output_sha256=hashlib.sha256(output.encode()).hexdigest(),
                spec_sha256=spec.sha256,
                usage=_usage().model_dump(mode="json"),
                completed_at="2026-08-26T12:00:03Z",
            )
            return competing_store.record_success(call, result, now=now)
        except Exception as error:  # returned for exact race classification
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        stop_future = executor.submit(stop)
        finish_future = executor.submit(finish)
        outcomes = (stop_future.result(timeout=10), finish_future.result(timeout=10))

    assert not any(isinstance(outcome, sa.exc.OperationalError) for outcome in outcomes)
    assert all(
        not isinstance(outcome, Exception)
        or isinstance(outcome, DelegatedWorkConflict)
        for outcome in outcomes
    )
    loaded = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    if loaded.state == "call_started":
        assert loaded.stop_requested_at == now
        loaded = store.record_stopped(
            call,
            usage=DelegatedWorkUsage.unmeasured(),
            now=now,
        )
    expected_terminal = "succeeded" if terminal_kind == "success" else "failed"
    assert loaded.state in {"stopped", expected_terminal}


def test_owner_chat_spawns_reports_and_stops_durable_postgres_work(
    delegated_engine: Engine,
) -> None:
    label = "owner-chat-control"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = _store(delegated_engine, scope)
    commands = DelegatedWorkConversationCommands(
        store,
        owner_principal_id=owner,
        principal_binding_id=binding,
        clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )

    def turn(message_id: str, text: str) -> ConversationTurn:
        return ConversationTurn.private(
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
            principal_id=str(owner),
            conversation_id="telegram:owner-chat-control",
            message_id=message_id,
            text=text,
            occurred_at="2026-08-26T12:00:00Z",
        )

    started = commands.handle(
        turn(
            "telegram:owner-chat-control:1",
            "Laat een onderzoeker de onderhoudsstatus van onze dependencies samenvatten",
        ),
        principal_binding_id=binding,
    )
    assert started is not None
    assert "ingepland" in started.text
    work_id = store.latest_stoppable_work_id(
        owner_principal_id=owner,
        principal_binding_id=binding,
    )
    assert work_id is not None
    stored = store.load(
        work_id,
        owner_principal_id=owner,
        principal_binding_id=binding,
    )
    assert stored.state == "admitted"
    assert stored.spec.task == (
        "de onderhoudsstatus van onze dependencies samenvatten"
    )

    status = commands.handle(
        turn("telegram:owner-chat-control:2", "Hoe staat mijn onderzoek?"),
        principal_binding_id=binding,
    )
    assert status is not None
    assert "1 wachtend" in status.text

    stopped = commands.handle(
        turn("telegram:owner-chat-control:3", "Stop mijn onderzoek"),
        principal_binding_id=binding,
    )
    assert stopped is not None
    assert "Gestopt voordat" in stopped.text
    stopped_replay = commands.handle(
        turn("telegram:owner-chat-control:3", "Stop mijn onderzoek"),
        principal_binding_id=binding,
    )
    assert stopped_replay == stopped
    assert store.load(
        work_id,
        owner_principal_id=owner,
        principal_binding_id=binding,
    ).state == "stopped"
    assert store.claim_next(
        worker_id="worker:after-owner-chat-stop",
        release=_release(stored.spec),
        now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
    ) is None


def test_private_telegram_owner_starts_follows_and_stops_postgres_work(
    delegated_engine: Engine,
) -> None:
    label = "telegram-owner-control"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = _store(delegated_engine, scope)
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    commands = DelegatedWorkConversationCommands(
        store,
        owner_principal_id=owner,
        principal_binding_id=binding,
        clock=lambda: now,
    )
    messages = (
        TelegramInboundMessage(
            1,
            "101",
            101,
            101,
            now,
            "Laat een onderzoeker de onderhoudsstatus samenvatten",
        ),
        TelegramInboundMessage(2, "101", 102, 101, now, "Hoe staat mijn onderzoek?"),
        TelegramInboundMessage(3, "101", 103, 101, now, "Stop mijn onderzoek"),
    )
    delivered: list[str] = []

    class Provider:
        def poll(self, *, offset: int):  # type: ignore[no-untyped-def]
            assert offset == 0
            return messages

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            delivered.append(text)
            return f"telegram:{message.chat_id}:receipt:{message.update_id}"

    class Core:
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
            raise AssertionError((message, principal_id, audience))

    class IngressStore:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, message, *, reason):  # type: ignore[no-untyped-def]
            raise AssertionError((message, reason))

        def admit(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            assert principal_id == str(owner)
            return TelegramIngressJob(message, principal_id, "ingress_admitted", 1)

        def prepare_reply(self, job, reply, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message,
                job.principal_id,
                "reply_prepared",
                2,
                reply,
            )

        def fail_processing(self, job, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError((job, error, now))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message,
                job.principal_id,
                "delivery_claimed",
                3,
                job.reply,
            )

        def acknowledge_delivery(self, job, receipt, *, now):  # type: ignore[no-untyped-def]
            assert receipt.endswith(str(job.message.update_id))

        def mark_delivery_ambiguous(self, job, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError((job, error, now))

    worker = TelegramIngressWorker(  # type: ignore[arg-type]
        Provider(),
        Core(),
        IngressStore(),
        TelegramPrincipalBindings(
            (
                TelegramPrincipalBinding(
                    101,
                    str(owner),
                    ("101",),
                    str(binding),
                ),
            )
        ),
        delegated_work_commands=commands,
        tenant_id=str(scope.tenant_id),
        installation_id=str(scope.installation_id),
    )

    assert worker.run_once() == "processed"
    assert len(delivered) == 3
    assert "ingepland" in delivered[0]
    assert "1 wachtend" in delivered[1]
    assert "Gestopt voordat" in delivered[2]
    work_id = store.latest_stoppable_work_id(
        owner_principal_id=owner,
        principal_binding_id=binding,
    )
    assert work_id is not None
    assert store.load(
        work_id,
        owner_principal_id=owner,
        principal_binding_id=binding,
    ).state == "stopped"
    board = store.workboard(
        owner_principal_id=owner,
        principal_binding_id=binding,
        now=now,
    )
    assert len(board.cards) == 1
    assert board.cards[0].column == "done"


def test_bot_api_and_ingress_ledger_replay_owner_delegation_exactly_once(
    delegated_engine: Engine,
) -> None:
    label = "telegram-http-owner-control"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    delegated_store = _store(delegated_engine, scope)
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    commands = DelegatedWorkConversationCommands(
        delegated_store,
        owner_principal_id=owner,
        principal_binding_id=binding,
        clock=lambda: now,
    )
    commands_text = (
        "Laat een onderzoeker de onderhoudsstatus samenvatten",
        "Hoe staat mijn onderzoek?",
        "Stop mijn onderzoek",
    )
    updates = [
        {
            "message": {
                "chat": {"id": 101},
                "date": int(now.timestamp()),
                "from": {"id": 101},
                "message_id": 100 + update_id,
                "text": text,
            },
            "update_id": update_id,
        }
        for update_id, text in enumerate(commands_text, start=1)
    ]
    received: list[tuple[str, dict[str, object]]] = []
    sent: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            received.append((self.path, payload))
            if self.path.endswith("/getUpdates"):
                offset = payload["offset"]
                response = {
                    "ok": True,
                    "result": [item for item in updates if item["update_id"] >= offset],
                }
            elif self.path.endswith("/sendMessage"):
                sent.append(payload)
                response = {
                    "ok": True,
                    "result": {"message_id": 9000 + len(sent)},
                }
            else:
                response = {"ok": False}
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    class Core:
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
            raise AssertionError((message, principal_id, audience))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ingress_store = PostgresTelegramIngressStore(
            delegated_engine,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        worker = TelegramIngressWorker(  # type: ignore[arg-type]
            TelegramBotApiAdapter(
                bot_token="123456:synthetic-owner-test",
                api_base_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=2,
            ),
            Core(),
            ingress_store,
            TelegramPrincipalBindings(
                (
                    TelegramPrincipalBinding(
                        101,
                        str(owner),
                        ("101",),
                        str(binding),
                    ),
                )
            ),
            delegated_work_commands=commands,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )

        assert worker.run_once() == "processed"
        assert ingress_store.next_offset() == 4
        assert len(sent) == 3
        assert "ingepland" in str(sent[0]["text"])
        assert "1 wachtend" in str(sent[1]["text"])
        assert "Gestopt voordat" in str(sent[2]["text"])
        assert worker.run_once() == "idle"
        assert len(sent) == 3
        assert [
            payload["offset"]
            for path, payload in received
            if path.endswith("/getUpdates")
        ] == [0, 4]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    work_id = delegated_store.latest_stoppable_work_id(
        owner_principal_id=owner,
        principal_binding_id=binding,
    )
    assert work_id is not None
    assert delegated_store.load(
        work_id,
        owner_principal_id=owner,
        principal_binding_id=binding,
    ).state == "stopped"


def test_revocation_waits_for_atomic_call_start(
    delegated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = "revocation-race"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    claim = store.claim_next(
        worker_id="worker:race",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None

    checked = threading.Event()
    release = threading.Event()
    original = store._validate_owner_binding

    def pause_after_locked_authority(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)  # type: ignore[arg-type]
        checked.set()
        if not release.wait(timeout=5):
            raise RuntimeError("revocation race test did not release")

    monkeypatch.setattr(store, "_validate_owner_binding", pause_after_locked_authority)
    outcomes: list[object] = []

    def start() -> None:
        outcomes.append(
            store.start_call(
                claim,
                now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
            )
        )

    thread = threading.Thread(target=start)
    thread.start()
    assert checked.wait(timeout=5)
    with delegated_engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql("SET LOCAL statement_timeout = '200ms'")
        with pytest.raises(sa.exc.OperationalError, match="statement timeout"):
            connection.execute(
                sa.update(principal_bindings)
                .where(
                    principal_bindings.c.tenant_id == scope.tenant_id,
                    principal_bindings.c.principal_binding_id == binding,
                )
                .values(status="revoked")
            )
        transaction.rollback()
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and outcomes[0] is not None

    # Once call-start committed, revocation can proceed and linearizes after it.
    with delegated_engine.begin() as connection:
        connection.execute(
            sa.update(principal_bindings)
            .where(
                principal_bindings.c.tenant_id == scope.tenant_id,
                principal_bindings.c.principal_binding_id == binding,
            )
            .values(status="revoked")
        )


def test_crash_before_call_is_reclaimed_but_after_call_is_not(
    delegated_engine: Engine,
) -> None:
    label = "crash"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    first = store.claim_next(
        worker_id="worker:first",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
        lease_seconds=5,
    )
    assert first is not None

    restarted = _store(delegated_engine, scope)
    second = restarted.claim_next(
        worker_id="worker:second",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 7, tzinfo=UTC),
        lease_seconds=5,
    )
    assert second is not None
    assert second.work_id == first.work_id
    call = restarted.start_call(
        second, now=datetime(2026, 8, 26, 12, 0, 8, tzinfo=UTC)
    )
    assert call is not None

    after_fence = _store(delegated_engine, scope)
    assert after_fence.claim_next(
        worker_id="worker:third",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 1, tzinfo=UTC),
    ) is None


def test_ambiguous_failure_and_scope_isolation(delegated_engine: Engine) -> None:
    label = "ambiguous"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    claim = store.claim_next(
        worker_id="worker:test",
        release=_release(spec),
        now=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
    )
    assert claim is not None
    call = store.start_call(
        claim, now=datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC)
    )
    assert call is not None
    usage = DelegatedWorkUsage.unmeasured()
    outcome = store.record_failure(
        call,
        usage=usage,
        error_code="provider_unavailable",
        error_fingerprint="aa" * 32,
        ambiguous=True,
        now=datetime(2026, 8, 26, 12, 0, 3, tzinfo=UTC),
    )
    assert outcome.state == "ambiguous"
    assert outcome.usage == usage

    other = PostgresDelegatedWorkStore(
        delegated_engine,
        TimelineScope(_id(label, "other-tenant"), scope.installation_id),
    )
    with pytest.raises(DelegatedWorkStoreError, match="binding is not current"):
        other.load(
            spec.work_id,
            owner_principal_id=_id(label, "principal"),
            principal_binding_id=binding,
        )
    with pytest.raises(DelegatedWorkStoreError, match="binding is not current"):
        other.workboard(
            owner_principal_id=_id(label, "principal"),
            principal_binding_id=binding,
            now=datetime(2026, 8, 26, 12, 0, 4, tzinfo=UTC),
        )


def test_corrupt_private_result_fails_closed(delegated_engine: Engine) -> None:
    label = "corrupt"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    with delegated_engine.begin() as connection:
        connection.execute(
            sa.update(delegated_work_runs)
            .where(
                delegated_work_runs.c.tenant_id == scope.tenant_id,
                delegated_work_runs.c.installation_id == scope.installation_id,
                delegated_work_runs.c.work_id == spec.work_id,
            )
            .values(spec_sha256=bytes.fromhex("ff" * 32))
        )
    with pytest.raises(DelegatedWorkStoreError, match="corrupt"):
        store.load(
            spec.work_id,
            owner_principal_id=_id(label, "principal"),
            principal_binding_id=binding,
        )
    with pytest.raises(DelegatedWorkStoreError, match="corrupt"):
        store.workboard(
            owner_principal_id=_id(label, "principal"),
            principal_binding_id=binding,
            now=datetime(2026, 8, 26, 12, 0, 4, tzinfo=UTC),
        )


def test_workboard_reports_when_more_than_64_cards_exist(
    delegated_engine: Engine,
) -> None:
    label = "board-limit"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    for ordinal in range(65):
        _admit(
            store,
            _spec(
                label,
                scope,
                episode,
                binding,
                work_suffix=f"research-{ordinal:03d}",
            ),
        )
    board = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 0, 4, tzinfo=UTC),
    )
    assert len(board.cards) == 64
    assert board.truncated is True
    assert "meer taken" in board.message


class _SuccessfulRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        spec: DelegatedWorkSpec,
        *,
        call_id: str,
        cancellation_event: object | None = None,
    ) -> ToolFreeChildOutcome:
        del cancellation_event
        self.calls += 1
        output = "Gebruik de onderhouden Pydantic-route en meet de echte voice-ervaring."
        usage = _usage()
        result = DelegatedWorkResult(
            contract="DelegatedWorkResult/v1",
            schema_version=1,
            work_id=spec.work_id,
            call_id=call_id,
            output=output,
            output_sha256=hashlib.sha256(output.encode()).hexdigest(),
            spec_sha256=spec.sha256,
            usage=usage.model_dump(mode="json"),
            completed_at="2026-08-26T12:02:00Z",
        )
        return ToolFreeChildOutcome(
            status="succeeded",
            result=result,
            usage=usage,
            retryable=False,
            error_fingerprint=None,
        )


class _OwnerCancellableRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    async def run(
        self,
        spec: DelegatedWorkSpec,
        *,
        call_id: str,
        cancellation_event: object | None = None,
    ) -> ToolFreeChildOutcome:
        del spec, call_id
        assert isinstance(cancellation_event, asyncio.Event)
        self.calls += 1
        self.started.set()
        await asyncio.wait_for(cancellation_event.wait(), timeout=2)
        return ToolFreeChildOutcome(
            status="cancelled",
            result=None,
            usage=DelegatedWorkUsage.unmeasured(),
            retryable=False,
            error_fingerprint=hashlib.sha256(b"owner cancellation").hexdigest(),
        )


class _CountedFunctionModel(FunctionModel):
    def __init__(self) -> None:
        self.requests = 0
        super().__init__(self._respond)

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        del messages, model_settings, model_request_parameters
        return RequestUsage(input_tokens=20)

    def _respond(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        del messages
        assert info.function_tools == []
        self.requests += 1
        return ModelResponse(
            parts=[
                TextPart(
                    '{"summary":"Pydantic AI blijft de eerste route",'
                    '"findings":["de duurzame callgrens is gekoppeld"]}'
                )
            ],
            usage=RequestUsage(input_tokens=20, output_tokens=8),
            model_name="fixture",
        )


def test_worker_couples_one_durable_call_and_never_replays_it(
    delegated_engine: Engine,
) -> None:
    label = "worker"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    runner = _SuccessfulRunner()

    def clock() -> datetime:
        return datetime(2026, 8, 26, 12, 2, tzinfo=UTC)

    worker = DelegatedWorkWorker(
        store,
        runner,
        worker_id="worker:one",
        release=_release(spec),
        clock=clock,
    )
    assert asyncio.run(worker.run_once()) == "succeeded"
    assert runner.calls == 1

    restarted = DelegatedWorkWorker(
        _store(delegated_engine, scope),
        runner,
        worker_id="worker:restarted",
        release=_release(spec),
        clock=clock,
    )
    assert asyncio.run(restarted.run_once()) == "idle"
    assert runner.calls == 1
    card = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 11, tzinfo=UTC),
    ).cards[0]
    assert card.column == "review"
    assert card.worker_health == "finished"
    assert card.overdue is False


def test_owner_stop_cancels_active_worker_and_never_replays_it(
    delegated_engine: Engine,
) -> None:
    label = "worker-owner-stop"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _spec(label, scope, episode, binding)
    _admit(store, spec)
    runner = _OwnerCancellableRunner()
    now = datetime(2026, 8, 26, 12, 2, tzinfo=UTC)
    worker = DelegatedWorkWorker(
        store,
        runner,
        worker_id="worker:owner-stop",
        release=_release(spec),
        clock=lambda: now,
    )

    async def run_and_stop() -> str:
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(runner.started.wait(), timeout=2)
        stopping = await asyncio.to_thread(
            store.request_stop,
            spec.work_id,
            owner_principal_id=_id(label, "principal"),
            principal_binding_id=binding,
            now=now,
        )
        assert stopping.state == "call_started"
        assert stopping.stop_requested_at == now
        return await asyncio.wait_for(running, timeout=2)

    assert asyncio.run(run_and_stop()) == "stopped"
    assert runner.calls == 1
    loaded = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert loaded.state == "stopped"
    assert loaded.call_id is not None
    assert loaded.stop_requested_at == now
    assert loaded.completed_at == now
    assert loaded.result is None
    assert loaded.usage == DelegatedWorkUsage.unmeasured()
    assert loaded.error_code is None
    assert loaded.error_fingerprint is None
    assert loaded.lease_owner is None
    with pytest.raises(sa.exc.IntegrityError):
        with delegated_engine.begin() as connection:
            connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == scope.tenant_id,
                    delegated_work_runs.c.installation_id == scope.installation_id,
                    delegated_work_runs.c.work_id == spec.work_id,
                )
                .values(completed_at=now - timedelta(seconds=1))
            )

    restarted = DelegatedWorkWorker(
        _store(delegated_engine, scope),
        runner,
        worker_id="worker:owner-stop:restarted",
        release=_release(spec),
        clock=lambda: now,
    )
    assert asyncio.run(restarted.run_once()) == "idle"
    assert runner.calls == 1
    card = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=now,
    ).cards[0]
    assert card.column == "done"
    assert card.worker_health == "stopped"
    assert card.result_status == "stopped"


def test_real_pydantic_runner_crosses_the_durable_fence_once(
    delegated_engine: Engine,
) -> None:
    label = "pydantic-worker"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    base = _spec(label, scope, episode, binding)
    spec = DelegatedWorkSpec.model_validate(
        base.model_dump(mode="json")
        | {
            "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
        }
    )
    store = _store(delegated_engine, scope)
    _admit(store, spec)
    model = _CountedFunctionModel()
    profile = ToolFreeChildProfile(
        model_profile_id=spec.model_profile_id,
        model_profile_release_sha256=spec.model_profile_release_sha256,
        worker_profile_id=spec.worker_profile_id,
        worker_profile_release_sha256=spec.worker_profile_release_sha256,
        instructions="Beantwoord uitsluitend de toegelaten onderzoekstaak.",
        model=model,
        input_cost_usd_micros_per_million_tokens=0,
        output_cost_usd_micros_per_million_tokens=0,
    )
    now = datetime(2026, 8, 26, 12, 2, tzinfo=UTC)
    runner = PydanticToolFreeChildRunner(
        ClosedModelRegistry({spec.model_profile_id: profile}),
        worker_program_sha256=spec.worker_program_sha256,
        engine_release_sha256=spec.engine_release_sha256,
        clock=lambda: now,
    )
    worker = DelegatedWorkWorker(
        store,
        runner,
        worker_id="worker:pydantic",
        release=_release(spec),
        clock=lambda: now,
    )

    assert asyncio.run(worker.run_once()) == "succeeded"
    assert model.requests == 1
    restarted = DelegatedWorkWorker(
        _store(delegated_engine, scope),
        runner,
        worker_id="worker:pydantic:restarted",
        release=_release(spec),
        clock=lambda: now,
    )
    assert asyncio.run(restarted.run_once()) == "idle"
    assert model.requests == 1
    loaded = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert loaded.result is not None
    assert "duurzame callgrens" in loaded.result.output
    assert loaded.usage is not None
    assert loaded.usage.total_tokens == 28


def _dsn_secret(engine: Engine, path: Path) -> Path:
    path.write_text(engine.url.render_as_string(hide_password=False))
    path.chmod(0o600)
    return path


def _closed_process(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert stdout == b""
    assert stderr == b""
    return stdout, stderr


def test_separate_process_executes_one_durable_pydantic_call(
    delegated_engine: Engine, process_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    label = "separate-process-success"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    count_file = process_tmp_path / "provider-calls"
    monkeypatch.setenv("EVA_TEST_PARENT_SECRET_CANARY", "must-not-reach-child")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-child")
    environment = _process_environment(
        dsn_file=dsn_file,
        scope=scope,
        scope_label=label,
        count_file=count_file,
        mode="success",
    )
    assert "EVA_TEST_PARENT_SECRET_CANARY" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "EVA2_TEST_POSTGRES_DSN" not in environment

    process = _start_process(environment)
    assert process.pid != os.getpid()
    assert all(spec.task not in argument for argument in process.args)
    assert all(str(delegated_engine.url) not in argument for argument in process.args)
    _closed_process(process)

    assert count_file.read_text() == "provider-call\n"
    loaded = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert loaded.state == "succeeded"
    assert loaded.result is not None
    assert "apart OS-proces" in loaded.result.output
    assert loaded.usage is not None and loaded.usage.total_tokens == 28
    card = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 2, 1, tzinfo=UTC),
    ).cards[0]
    assert card.column == "review"
    assert spec.task not in card.model_dump_json()
    assert spec.work_id not in card.model_dump_json()


def test_separate_process_executes_official_sdk_from_postgres_to_result(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "official-sdk-process-success"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _product_process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    lease_file = process_tmp_path / "provider-lease"
    token = "official-sdk-secret-canary"
    lease_file.write_text(
        json.dumps({"access_token": token, "account_id": "account-test"})
    )
    lease_file.chmod(0o600)

    with _official_provider_server() as (endpoint, captured, received):
        environment = _official_process_environment(
            dsn_file=dsn_file,
            scope=scope,
            scope_label=label,
            count_file=process_tmp_path / "unused-provider-counter",
            endpoint=endpoint,
            lease_file=lease_file,
        )
        process = _start_process(environment)
        assert process.pid != os.getpid()
        assert all(spec.task not in argument for argument in process.args)
        assert all(token not in argument for argument in process.args)
        _closed_process(process)
        assert received.is_set()

    assert len(captured) == 1
    assert captured[0]["path"] == "/responses"
    assert captured[0]["authorization"] == f"Bearer {token}"
    assert captured[0]["body"]["tool_choice"] == "none"
    assert captured[0]["body"]["tools"] == []
    loaded = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert loaded.state == "succeeded"
    assert loaded.result is not None
    assert "officiele SDK-keten" in loaded.result.output
    assert loaded.usage is not None
    assert loaded.usage.input_tokens == 31
    assert loaded.usage.output_tokens == 11
    assert token not in repr(loaded)


def test_sigterm_during_official_sdk_call_is_ambiguous_and_never_replayed(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "official-sdk-process-sigterm"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _product_process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    lease_file = process_tmp_path / "provider-lease"
    lease_file.write_text(
        json.dumps({"access_token": "test-token", "account_id": "account-test"})
    )
    lease_file.chmod(0o600)

    with _official_provider_server(body_delay=2) as (endpoint, captured, received):
        environment = _official_process_environment(
            dsn_file=dsn_file,
            scope=scope,
            scope_label=label,
            count_file=process_tmp_path / "unused-provider-counter",
            endpoint=endpoint,
            lease_file=lease_file,
        )
        process = _start_process(environment)
        assert received.wait(timeout=10)
        process.terminate()
        _closed_process(process)
        loaded = store.load(
            spec.work_id,
            owner_principal_id=_id(label, "principal"),
            principal_binding_id=binding,
        )
        assert loaded.state == "ambiguous"
        assert loaded.error_code == "cancelled"

        restarted = _start_process(environment)
        _closed_process(restarted)
    assert len(captured) == 1


def test_owner_chat_stop_cancels_official_sdk_call_and_never_replays(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "official-sdk-owner-stop"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = _store(delegated_engine, scope)
    spec = _product_process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    lease_file = process_tmp_path / "provider-lease"
    token = "owner-stop-token"
    lease_file.write_text(
        json.dumps({"access_token": token, "account_id": "account-owner-stop"})
    )
    lease_file.chmod(0o600)
    now = datetime(2026, 8, 26, 12, 2, tzinfo=UTC)
    commands = DelegatedWorkConversationCommands(
        store,
        owner_principal_id=owner,
        principal_binding_id=binding,
        clock=lambda: now,
    )

    with _official_provider_server(body_delay=2) as (endpoint, captured, received):
        environment = _official_process_environment(
            dsn_file=dsn_file,
            scope=scope,
            scope_label=label,
            count_file=process_tmp_path / "unused-provider-counter",
            endpoint=endpoint,
            lease_file=lease_file,
        )
        process = _start_process(environment)
        assert received.wait(timeout=10)
        reply = commands.handle(
            ConversationTurn.private(
                tenant_id=str(scope.tenant_id),
                installation_id=str(scope.installation_id),
                principal_id=str(owner),
                conversation_id="telegram:owner-stop",
                message_id="telegram:owner-stop:1",
                text="Stop mijn onderzoek",
                occurred_at="2026-08-26T12:02:00Z",
            ),
            principal_binding_id=binding,
        )
        assert reply is not None
        assert reply.text == (
            "De stopopdracht is bewaard. Het resultaat van deze actieve modelcall "
            "wordt niet aan Eva geleverd en Eva start hem niet opnieuw."
        )
        _closed_process(process)

        loaded = store.load(
            spec.work_id,
            owner_principal_id=owner,
            principal_binding_id=binding,
        )
        assert loaded.state == "stopped"
        assert loaded.call_id is not None
        assert loaded.stop_requested_at == now
        assert loaded.result is None
        assert loaded.usage is not None
        assert loaded.error_code is None
        assert token not in repr(loaded)

        replay = commands.handle(
            ConversationTurn.private(
                tenant_id=str(scope.tenant_id),
                installation_id=str(scope.installation_id),
                principal_id=str(owner),
                conversation_id="telegram:owner-stop",
                message_id="telegram:owner-stop:1",
                text="Stop mijn onderzoek",
                occurred_at="2026-08-26T12:02:01Z",
            ),
            principal_binding_id=binding,
        )
        assert replay is not None
        assert replay == reply

        restarted = _start_process(environment)
        _closed_process(restarted)
    assert len(captured) == 1


def test_two_separate_processes_share_one_call_start_fence(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "separate-process-race"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _process_spec(label, scope, episode, binding)
    _admit(store, spec)
    environment = _process_environment(
        dsn_file=_dsn_secret(
            delegated_engine, process_tmp_path / "database-dsn"
        ),
        scope=scope,
        scope_label=label,
        count_file=process_tmp_path / "provider-calls",
        mode="success",
    )

    first = _start_process(environment)
    second = _start_process(environment)
    _closed_process(first)
    _closed_process(second)

    assert (process_tmp_path / "provider-calls").read_text() == "provider-call\n"
    assert store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    ).state == "succeeded"


def test_process_death_before_call_start_is_reclaimable(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "separate-process-before-call"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    count_file = process_tmp_path / "provider-calls"
    claimed_marker = process_tmp_path / "claimed-before-call"
    paused = _process_environment(
        dsn_file=dsn_file,
        scope=scope,
        scope_label=label,
        count_file=count_file,
        mode="pause_after_claim",
        after_claim_marker=claimed_marker,
    )

    first = _start_process(paused)
    _wait_for_file(claimed_marker)
    claimed = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert claimed.state == "admitted"
    assert claimed.lease_owner is not None
    assert claimed.call_id is None
    first.kill()
    first.communicate(timeout=10)
    assert first.returncode == -9
    assert not count_file.exists()

    restarted = _start_process(
        _process_environment(
            dsn_file=dsn_file,
            scope=scope,
            scope_label=label,
            count_file=count_file,
            mode="success",
            now="2026-08-26T12:03:00Z",
        )
    )
    _closed_process(restarted)
    assert count_file.read_text() == "provider-call\n"
    assert store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    ).state == "succeeded"


def test_sigterm_during_model_call_is_recorded_ambiguous_and_never_replayed(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "separate-process-sigterm"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    count_file = process_tmp_path / "provider-calls"
    environment = _process_environment(
        dsn_file=dsn_file,
        scope=scope,
        scope_label=label,
        count_file=count_file,
        mode="slow",
    )

    process = _start_process(environment)
    _wait_for_call(count_file)
    process.terminate()
    _closed_process(process)
    loaded = store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    )
    assert loaded.state == "ambiguous"
    assert loaded.error_code == "cancelled"
    card = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 2, 1, tzinfo=UTC),
    ).cards[0]
    assert card.column == "attention"
    assert card.worker_health == "stopped"
    assert card.result_status == "ambiguous"
    assert card.reason == (
        "De worker is tijdens de modelrun netjes gestopt. Er is geen bevestigd "
        "resultaat; Eva start deze modelrun niet opnieuw."
    )

    restarted = _start_process(environment | {"EVA_TEST_SUBAGENT_MODE": "success"})
    _closed_process(restarted)
    assert count_file.read_text() == "provider-call\n"


def test_sigkill_after_call_start_is_visible_and_never_replayed(
    delegated_engine: Engine, process_tmp_path: Path
) -> None:
    label = "separate-process-sigkill"
    scope, episode, binding = _seed_authority(delegated_engine, label)
    store = _store(delegated_engine, scope)
    spec = _process_spec(label, scope, episode, binding)
    _admit(store, spec)
    dsn_file = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    count_file = process_tmp_path / "provider-calls"
    environment = _process_environment(
        dsn_file=dsn_file,
        scope=scope,
        scope_label=label,
        count_file=count_file,
        mode="slow",
    )

    process = _start_process(environment)
    _wait_for_call(count_file)
    process.kill()
    process.communicate(timeout=10)
    assert process.returncode == -9
    assert store.load(
        spec.work_id,
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
    ).state == "call_started"

    restarted = _start_process(environment | {"EVA_TEST_SUBAGENT_MODE": "success"})
    _closed_process(restarted)
    assert count_file.read_text() == "provider-call\n"
    card = store.workboard(
        owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding,
        now=datetime(2026, 8, 26, 12, 3, tzinfo=UTC),
    ).cards[0]
    assert card.worker_health == "contact_lost"
    assert card.result_status == "ambiguous"
