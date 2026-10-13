#!/usr/bin/env python3
"""Kill Eva after one real provider return, then resume by readback only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

import sqlalchemy as sa

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import ConversationTurn, IdentityProfile
from eva.application.orchestration import (
    AsyncToolPort,
    ToolDispatchResult,
    ToolProofObservation,
    ToolRequest,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.providers.runtime import (
    build_external_provider_bundle,
    load_external_provider_settings,
)

from prove_real_time_provider import (
    EVA_TOOL_NAME,
    FIXED_ARGUMENTS,
    _configure_provider,
    _database_url,
    _login,
    _memory,
    _migrate,
    _request_count,
    _tool_row,
)


BASE_URL = "http://contextforge:4444"
STATE_ROOT = Path("/run/proof-state")
EXPECTED_MIGRATION = "4e_0007_delegated_private"


def _write_once(name: str, value: dict[str, Any]) -> None:
    target = STATE_ROOT / name
    raw = canonical_json_bytes(value) + b"\n"
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(STATE_ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_state(name: str) -> dict[str, Any]:
    value = json.loads((STATE_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} is not a JSON object")
    return value


class _BlockAfterRealExecute(AsyncToolPort):
    """Test-only wrapper that exposes the post-provider/pre-commit kill window."""

    def __init__(
        self,
        delegate: AsyncToolPort,
        durable_state: Callable[[ToolRequest], dict[str, Any]],
    ) -> None:
        self._delegate = delegate
        self._durable_state = durable_state

    async def execute(self, request: ToolRequest) -> ToolDispatchResult:
        result = await self._delegate.execute(request)
        _write_once(
            "effect-returned.json",
            {
                "accepted": result.accepted,
                "contract": "EvaRealProviderReturnedBeforeSigkill/v1",
                "operationId": request.operation_id,
                "providerRefSha256": (
                    None
                    if result.provider_ref is None
                    else hashlib.sha256(result.provider_ref.encode("utf-8")).hexdigest()
                ),
                "toolNameSha256": hashlib.sha256(
                    request.tool_name.encode("utf-8")
                ).hexdigest(),
                **self._durable_state(request),
            },
        )
        print(
            json.dumps(
                {
                    "contract": "EvaRealProviderKillWindow/v1",
                    "phase": "effect_returned_waiting_for_sigkill",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        await asyncio.Event().wait()
        raise AssertionError("SIGKILL wait unexpectedly returned")

    async def observe(
        self,
        request: ToolRequest,
        dispatch: ToolDispatchResult | None,
    ) -> ToolProofObservation:
        return await self._delegate.observe(request, dispatch)


def _turn() -> ConversationTurn:
    return ConversationTurn.private(
        "real-time-proof-tenant",
        "real-time-proof-installation",
        "real-time-proof-person",
        "real-time-proof-conversation",
        "real-time-proof-message",
        (
            "Gebruik het beschikbare tijdgereedschap. Zet exact "
            "2026-08-30T12:34:56+02:00 om van Europe/Amsterdam naar "
            "Europe/Amsterdam en geef daarna alleen de gecontroleerde uitkomst."
        ),
        "2026-08-30T20:20:00Z",
    )


def _identity() -> IdentityProfile:
    return IdentityProfile(
        "Eva",
        "Een eerlijke digitale assistent die extern bewijs gebruikt.",
        ("Gebruik gereedschap alleen wanneer het resultaat echt nodig is.",),
        80,
    )


def _assert_existing_database(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) != 160013:
            raise RuntimeError("real provider crash proof requires PostgreSQL 16.13")
        versions = tuple(
            connection.scalars(
                sa.text(
                    "SELECT version_num FROM public.eva_alembic_version "
                    "ORDER BY version_num"
                )
            )
        )
        if versions != (EXPECTED_MIGRATION,):
            raise RuntimeError("real provider crash proof schema differs")


def _prepared_state(
    route: PostgresExternalConversationTools,
    turn: ConversationTurn,
    request: ToolRequest,
) -> dict[str, Any]:
    timeline = route._timeline(turn)  # noqa: SLF001
    state = timeline.load(request.thread_id)
    events = timeline.events(request.thread_id)
    event_types = [event.event_type for event in events]
    if (
        state.phase != "tool_prepared"
        or state.status != "waiting_for_proof"
        or state.operation_id != request.operation_id
        or event_types != ["TOOL_REQUEST_ADMITTED", "TOOL_PREPARED"]
    ):
        raise RuntimeError("provider returned outside the sealed SIGKILL window")
    return {
        "databaseEventTypes": event_types,
        "databasePhase": state.phase,
        "databaseRevision": state.revision,
    }


def _components(*, migrate: bool) -> tuple[sa.Engine, Any, Any, str, int]:
    STATE_ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    row = _tool_row(BASE_URL, token)
    catalog_sha256 = _configure_provider(BASE_URL, token, row)
    url = _database_url(Path("/run/secrets/postgres-password"))
    engine = sa.create_engine(url, pool_pre_ping=False)
    if migrate:
        _migrate(engine, url)
    else:
        _assert_existing_database(engine)
    bundle = build_external_provider_bundle(load_external_provider_settings(), engine)
    if bundle is None or [item.name for item in bundle.conversation_tools] != [
        EVA_TOOL_NAME
    ]:
        raise RuntimeError("MCP provider did not contribute the sealed tool")
    route = next(
        item for item in bundle.external_tools if EVA_TOOL_NAME in item.action_names
    )
    if not isinstance(route, PostgresExternalConversationTools):
        raise RuntimeError("real provider route uses another durable owner")
    return engine, bundle, route, catalog_sha256, _request_count(BASE_URL, token)


def _start() -> int:
    if any(STATE_ROOT.iterdir()):
        raise RuntimeError("real provider crash proof state is not empty")
    engine, bundle, route, catalog_sha256, provider_count = _components(migrate=True)
    model = PydanticConversationModel(
        auth_file=Path("/run/eva/model-lease/auth.json"),
        conversation_tools=bundle.conversation_tools,
    )
    turn = _turn()
    selected = model.generate(
        turn,
        _identity(),
        _memory(),
        (),
        available_actions=(EVA_TOOL_NAME,),
    )
    call = selected.action_call
    if call is None or call.name != EVA_TOOL_NAME or call.arguments != FIXED_ARGUMENTS:
        raise RuntimeError("model did not select the exact real time tool call")
    _write_once(
        "start.json",
        {
            "candidateImageId": os.environ["EVA_PROOF_IMAGE_ID"],
            "candidateSourceCommit": os.environ["EVA_PROOF_SOURCE_COMMIT"],
            "callSha256": canonical_sha256(
                {"arguments": call.arguments, "callId": call.call_id, "name": call.name}
            ),
            "catalogSha256": catalog_sha256,
            "contract": "EvaRealProviderSigkillStart/v1",
            "proofProgramSha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "providerRequestCountBefore": provider_count,
            "usage": selected.usage.to_wire(),
        },
    )
    route._provider = _BlockAfterRealExecute(  # noqa: SLF001
        route._provider,  # noqa: SLF001
        lambda request: _prepared_state(route, turn, request),
    )
    route.execute(turn, call)
    engine.dispose()
    raise RuntimeError("initial phase returned instead of being killed")


def _resume() -> int:
    start = _read_state("start.json")
    effect = _read_state("effect-returned.json")
    engine, bundle, route, catalog_sha256, before_readback = _components(migrate=False)
    if catalog_sha256 != start.get("catalogSha256"):
        raise RuntimeError("provider catalog changed across SIGKILL")
    turn = _turn()
    receipt = route.pending(turn)
    if receipt is None or receipt.output.get("status") != "verified":
        raise RuntimeError("real provider action did not resume to verified")
    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    after_readback = _request_count(BASE_URL, token)
    replay = route.execute(turn, receipt.call)
    after_replay = _request_count(BASE_URL, token)
    baseline = start.get("providerRequestCountBefore")
    if not isinstance(baseline, int) or isinstance(baseline, bool):
        raise RuntimeError("provider baseline is invalid")
    if (
        before_readback - baseline != 1
        or after_readback - before_readback != 1
        or after_replay - after_readback != 0
        or replay != receipt
    ):
        raise RuntimeError("provider execution, readback or replay count differs")
    timeline = route._timeline(turn)  # noqa: SLF001
    event_types = [
        event.event_type
        for event in timeline.events(route._thread_id(turn, EVA_TOOL_NAME))  # noqa: SLF001
    ]
    if event_types != ["TOOL_REQUEST_ADMITTED", "TOOL_PREPARED", "STATE_VERIFIED"]:
        raise RuntimeError("durable SIGKILL recovery timeline differs")

    completed = PydanticConversationModel(
        auth_file=Path("/run/eva/model-lease/auth.json"),
        conversation_tools=bundle.conversation_tools,
    ).generate(turn, _identity(), _memory(), (), completed_action=receipt)
    if completed.text is None or FIXED_ARGUMENTS["time"] not in completed.text:
        raise RuntimeError("model final answer omitted the verified time")
    result = {
        "candidateImageId": os.environ["EVA_PROOF_IMAGE_ID"],
        "candidateSourceCommit": os.environ["EVA_PROOF_SOURCE_COMMIT"],
        "catalogSha256": catalog_sha256,
        "contract": "EvaRealNonHomeMcpProviderSigkillProof/v1",
        "effectMarkerSha256": canonical_sha256(effect),
        "eventTypes": event_types,
        "finalAnswerSha256": hashlib.sha256(completed.text.encode("utf-8")).hexdigest(),
        "gatewayCallsForEffect": 1,
        "gatewayCallsForReadback": 1,
        "gatewayCallsForReplay": 0,
        "receiptSha256": canonical_sha256(receipt.output),
        "receiptStatus": receipt.output["status"],
        "usage": completed.usage.to_wire(),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    engine.dispose()
    return 0


def _replay() -> int:
    start = _read_state("start.json")
    _read_state("effect-returned.json")
    engine, _bundle, route, catalog_sha256, before = _components(migrate=False)
    baseline = start.get("providerRequestCountBefore")
    if not isinstance(baseline, int) or isinstance(baseline, bool):
        raise RuntimeError("provider baseline is invalid")
    if catalog_sha256 != start.get("catalogSha256") or before - baseline != 2:
        raise RuntimeError("fresh replay precondition differs")
    turn = _turn()
    receipt = route.pending(turn)
    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    after_pending = _request_count(BASE_URL, token)
    if receipt is None or receipt.output.get("status") != "verified":
        raise RuntimeError("fresh replay could not load the verified receipt")
    replay = route.execute(turn, receipt.call)
    after_replay = _request_count(BASE_URL, token)
    receipt_sha256 = canonical_sha256(receipt.output)
    expected_receipt = os.environ.get("EVA_PROOF_EXPECTED_RECEIPT_SHA256")
    if (
        expected_receipt != receipt_sha256
        or replay != receipt
        or after_pending != before
        or after_replay != before
    ):
        raise RuntimeError("fresh process replay changed receipt or provider count")
    event_types = [
        event.event_type
        for event in route._timeline(turn).events(  # noqa: SLF001
            route._thread_id(turn, EVA_TOOL_NAME)  # noqa: SLF001
        )
    ]
    if event_types != ["TOOL_REQUEST_ADMITTED", "TOOL_PREPARED", "STATE_VERIFIED"]:
        raise RuntimeError("fresh process replay changed the durable timeline")
    result = {
        "candidateImageId": os.environ["EVA_PROOF_IMAGE_ID"],
        "candidateSourceCommit": os.environ["EVA_PROOF_SOURCE_COMMIT"],
        "contract": "EvaRealNonHomeMcpProviderFreshReplayProof/v1",
        "eventTypes": event_types,
        "gatewayCallsForFreshProcessReplay": 0,
        "providerRequestCount": after_replay,
        "receiptSha256": receipt_sha256,
        "receiptStatus": receipt.output["status"],
    }
    _write_once("replay.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("start", "resume", "replay"))
    arguments = parser.parse_args()
    if arguments.phase == "start":
        return _start()
    if arguments.phase == "resume":
        return _resume()
    return _replay()


if __name__ == "__main__":
    raise SystemExit(main())
