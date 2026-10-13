#!/usr/bin/env python3
"""Prove one confirmed installer budget through the model and PostgreSQL journal."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Iterator
from unittest.mock import patch
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.conversation import (
    ConversationTurn,
    GroundedReply,
    IdentityProfile,
)
from eva.application.memory import PresenceMemory
from eva.application.personal_interests import PersonalInterestClaim
from eva.bootstrap.runtime import RuntimeSettings
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall
from eva.operations.installer_budget_workflow import InstallerBudgetWorkflow
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_runtime_budget import materialize_runtime_resource_budget


ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = "tenant:runtime-budget-proof"
INSTALLATION_ID = "installation:runtime-budget-proof"
PRINCIPAL_ID = "principal:runtime-budget-proof"
ACCESS_TOKEN = "synthetic-runtime-budget-access-token"
ACCOUNT_ID = "synthetic-runtime-budget-account"
NOW = "2026-08-29T05:30:00Z"
SOURCE_PATHS = (
    "src/eva/adapters/model/openai_codex_provider.py",
    "src/eva/adapters/model/pydantic_conversation.py",
    "src/eva/bootstrap/runtime.py",
    "src/eva/contracts/runtime_budget.py",
    "src/eva/operations/installer_runtime_budget.py",
    "development/tests/bootstrap/test_runtime_settings.py",
    "development/tests/model/test_conversation_model.py",
    "development/tests/operations/test_installer_runtime_budget.py",
    "src/tools/prove_runtime_resource_budget.py",
)
_SHA256 = re.compile(r"[0-9a-f]{40,64}")


def _stream(*, text: str, input_tokens: int, output_tokens: int) -> bytes:
    events = (
        {"delta": text, "type": "response.output_text.delta"},
        {
            "response": {
                "error": None,
                "status": "completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            },
            "type": "response.completed",
        },
    )
    return (
        b"".join(b"data: " + canonical_json_bytes(event) + b"\n\n" for event in events)
        + b"data: [DONE]\n\n"
    )


class _ProviderHandler(BaseHTTPRequestHandler):
    responses: list[bytes] = []
    requests: list[dict[str, object]] = []
    matching_requests = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            self.send_error(400)
            return
        type(self).requests.append(body)
        if (
            self.path == "/backend-api/codex/responses"
            and self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"
            and self.headers.get("OpenAI-Account-ID") == ACCOUNT_ID
            and body.get("store") is False
            and body.get("stream") is True
        ):
            type(self).matching_requests += 1
        index = len(type(self).requests) - 1
        if index >= len(type(self).responses):
            self.send_error(503)
            return
        raw = type(self).responses[index]
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _provider() -> Iterator[tuple[str, type[_ProviderHandler]]]:
    handler = type("BoundRuntimeBudgetProvider", (_ProviderHandler,), {})
    handler.responses = [
        _stream(text="Budget werkt.", input_tokens=100, output_tokens=20),
        _stream(text="Te groot.", input_tokens=100, output_tokens=1_201),
    ]
    handler.requests = []
    handler.matching_requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/backend-api/codex/responses",
            handler,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("runtime budget provider did not stop")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def _private_file(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.write_bytes(raw)
    path.chmod(mode)


def _budget_values() -> dict[str, int]:
    return {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_person": 2_000,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
    }


def _materialize(root: Path) -> tuple[Path, str]:
    capabilities = root / "capabilities"
    budgets = root / "budgets"
    _private_directory(capabilities)
    _private_directory(budgets)
    capability = InstallerCapabilityWorkflow(
        workspace=capabilities,
        clock=lambda: "2026-08-29T05:25:00Z",
    )
    capability.select(step_id="budgets", wanted=True)
    workflow = InstallerBudgetWorkflow(
        workspace=budgets,
        capability_workflow=capability,
        clock=lambda: "2026-08-29T05:26:00Z",
    )
    staged = workflow.stage(**_budget_values())
    workflow.confirm(expected_budget_sha256=staged.budget_sha256)
    target = root / "runtime-budget.json"
    policy = materialize_runtime_resource_budget(
        workflow,
        target=target,
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
    )
    return target, policy.source_budget_sha256


def _memory(message_id: str) -> PresenceMemory:
    return PresenceMemory(
        MemoryRecall(
            schema_version=1,
            contract="MemoryRecall/v1",
            recall_id=f"conversation:{message_id}",
            query_purpose="conversation_reply",
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            conversation_id="conversation:runtime-budget-proof",
            project_id=None,
            requested_valid_at=NOW,
            admitted_fact_ids_sha256=canonical_sha256([]),
            selected=[],
            excluded_reasons={},
            projection_version="runtime-budget-proof-v1",
            scope_sha256=canonical_sha256(
                {"kind": "person", "principal_id": PRINCIPAL_ID}
            ),
            created_at=NOW,
        ),
        (),
    )


def _turn(message_id: str, text: str) -> ConversationTurn:
    return ConversationTurn.private(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        principal_id=PRINCIPAL_ID,
        conversation_id="conversation:runtime-budget-proof",
        message_id=message_id,
        text=text,
        occurred_at=NOW,
    )


def _journal(engine: sa.Engine) -> PostgresConversationJournal:
    return PostgresConversationJournal(
        engine,
        TimelineScope(
            tenant_id=UUID("11111111-1111-4111-8111-111111111111"),
            installation_id=UUID("22222222-2222-4222-8222-222222222222"),
        ),
    )


def _source_files() -> list[dict[str, str]]:
    return [
        {
            "path": relative,
            "sha256": hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
        }
        for relative in SOURCE_PATHS
    ]


def prove(
    database_dsn: str,
    *,
    focused_tests_passed: int,
    model_auth_file: Path,
    source_commit: str,
) -> dict[str, object]:
    if focused_tests_passed < 1:
        raise ValueError("focused test count is invalid")
    if _SHA256.fullmatch(source_commit) is None:
        raise ValueError("source commit is invalid")
    with tempfile.TemporaryDirectory(
        prefix="eva2-runtime-budget-proof-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        budget_file, budget_sha256 = _materialize(root)
        database_file = root / "database-dsn"
        _private_file(database_file, f"{database_dsn}\n".encode())
        environment = {
            "EVA_CONVERSATION_AUTH_FILE": str(model_auth_file),
            "EVA_DATABASE_DSN_FILE": str(database_file),
            "EVA_HINDSIGHT_URL": "http://hindsight.invalid:8888",
            "EVA_RUNTIME_INSTALLATION_ID": INSTALLATION_ID,
            "EVA_RUNTIME_RESOURCE_BUDGET_FILE": str(budget_file),
            "EVA_RUNTIME_TENANT_ID": TENANT_ID,
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = RuntimeSettings.from_environment()
        policy = settings.runtime_resource_budget
        if (
            policy is None
            or settings.hindsight_recall_max_tokens != 3_000
            or settings.hindsight_recall_node_budget != 50
            or policy.source_budget_sha256 != budget_sha256
            or stat.S_IMODE(budget_file.stat().st_mode) != 0o600
        ):
            raise RuntimeError("materialized runtime budget differs")

        engine = sa.create_engine(database_dsn, pool_pre_ping=True)
        journal = _journal(engine)
        identity = IdentityProfile(
            "Eva",
            "Je bent een warme, nieuwsgierige en eerlijke digitale huisgenoot.",
            ("Zeg eerlijk wat je wel en niet weet.",),
            80,
        )
        try:
            with engine.connect() as connection:
                postgres_version = str(
                    connection.execute(sa.text("SELECT version()")).scalar_one()
                )
            with _provider() as (endpoint, handler):
                model = PydanticConversationModel(
                    auth_file=model_auth_file,
                    endpoint=endpoint,
                    resource_budget=policy,
                )

                accepted_turn = _turn("message:accepted", "Geef een kort antwoord.")
                journal.admit(accepted_turn)
                generated = model.generate(
                    accepted_turn,
                    identity,
                    _memory(accepted_turn.message_id),
                    (),
                )
                accepted = GroundedReply(
                    conversation_id=accepted_turn.conversation_id,
                    message_id=accepted_turn.message_id,
                    text=generated.text or "",
                    evidence_memory_ids=(),
                    generated_at=accepted_turn.occurred_at,
                    provider=generated.provider,
                    model_usage=generated.usage,
                )
                journal.commit_reply(accepted_turn, accepted)
                durable = journal.committed_reply(accepted_turn)
                if durable != accepted:
                    raise RuntimeError("measured reply did not round-trip")

                oversized_turn = _turn("message:oversized", "x" * 12_000)
                journal.admit(oversized_turn)
                before_oversized = len(handler.requests)
                try:
                    model.generate(
                        oversized_turn,
                        identity,
                        _memory(oversized_turn.message_id),
                        (),
                    )
                except ConversationModelError as error:
                    oversized_reason = str(error)
                else:
                    raise RuntimeError(
                        "oversized request unexpectedly reached provider"
                    )
                if (
                    len(handler.requests) != before_oversized
                    or journal.committed_reply(oversized_turn) is not None
                    or "input budget" not in oversized_reason
                ):
                    raise RuntimeError("oversized input was not closed before provider")

                output_turn = _turn(
                    "message:output-over", "Geef nog een kort antwoord."
                )
                journal.admit(output_turn)
                before_output = len(handler.requests)
                try:
                    model.generate(
                        output_turn,
                        identity,
                        _memory(output_turn.message_id),
                        (),
                    )
                except ConversationModelError as error:
                    output_reason = str(error)
                else:
                    raise RuntimeError("over-budget output unexpectedly became a reply")
                if (
                    len(handler.requests) != before_output + 1
                    or journal.committed_reply(output_turn) is not None
                    or "output budget" not in output_reason
                ):
                    raise RuntimeError("over-budget output was not withheld")

                claim = PersonalInterestClaim(
                    TENANT_ID,
                    INSTALLATION_ID,
                    PRINCIPAL_ID,
                    "conversation:runtime-budget-proof",
                    "message:autonomous",
                    "personal-interest:runtime-budget-proof",
                    "intent:runtime-budget-proof",
                    "Zelfstandig verbeteren",
                    "Eva wil nuttig blijven zonder ongecontroleerde kosten.",
                    "Welke kleine verbetering is aantoonbaar nuttig?",
                    NOW,
                    1,
                )
                before_autonomous = len(handler.requests)
                try:
                    model.explore(claim)
                except ConversationModelError as error:
                    autonomous_reason = str(error)
                else:
                    raise RuntimeError("unknown autonomous spend unexpectedly started")
                if (
                    len(handler.requests) != before_autonomous
                    or "no verified price" not in autonomous_reason
                ):
                    raise RuntimeError("unknown autonomous spend was not closed")

                provider_requests = len(handler.requests)
                matching_provider_requests = handler.matching_requests
                if provider_requests != 2 or matching_provider_requests != 2:
                    raise RuntimeError("runtime budget provider request set differs")
        finally:
            engine.dispose()

    receipt = {
        "accepted_reply": {
            "input_tokens": 100,
            "measurement": "measured",
            "output_tokens": 20,
            "postgres_round_trip": True,
            "text_sha256": accepted.text_sha256,
        },
        "blocked": {
            "autonomous_unknown_cost_provider_calls": 0,
            "output_over_budget_provider_calls": 1,
            "output_over_budget_reply_committed": False,
            "oversized_input_provider_calls": 0,
            "oversized_input_reply_committed": False,
        },
        "contract": "EvaRuntimeResourceBudgetProof/v1",
        "installer_to_runtime": {
            "budget_file_mode": "0600",
            "hindsight_recall_max_tokens": 3_000,
            "hindsight_retrieval_nodes": 50,
            "source_budget_sha256": budget_sha256,
        },
        "non_claims": [
            "geen live providerprijs of maandelijkse kostenafrekening",
            "geen productiepromotie of beheerde B-omschakeling",
            "geen bewijs dat iedere niet-modelresource al fysiek wordt afgekapt",
        ],
        "postgres": {
            "major_minor": ".".join(postgres_version.split()[1].split(".")[:2]),
            "real_journal": True,
        },
        "provider": {
            "contract_server": "loopback",
            "matching_request_count": matching_provider_requests,
            "request_count": provider_requests,
        },
        "source_commit": source_commit,
        "source_files": _source_files(),
        "tests": {"focused_passed": focused_tests_passed},
    }
    serialized = canonical_json_bytes(receipt)
    forbidden = (
        ACCESS_TOKEN.encode(),
        ACCOUNT_ID.encode(),
        database_dsn.encode(),
        b"Budget werkt.",
        b"Te groot.",
    )
    if any(value in serialized for value in forbidden):
        raise RuntimeError("runtime budget receipt retained private proof data")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-dsn-file", type=Path, required=True)
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--model-auth-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output already exists")
    database_dsn = args.database_dsn_file.read_text().strip()
    if not database_dsn.startswith("postgresql"):
        raise SystemExit("proof database DSN is invalid")
    receipt = prove(
        database_dsn,
        focused_tests_passed=args.focused_tests_passed,
        model_auth_file=args.model_auth_file,
        source_commit=args.source_commit,
    )
    args.output.write_bytes(canonical_json_bytes(receipt))
    print(hashlib.sha256(args.output.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
