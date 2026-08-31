"""Test-only child process for the durable delegated-work service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
import eva.bootstrap  # noqa: F401  # Load root before its model submodule.
from eva.adapters.postgres.delegated_work import (
    DelegatedWorkReleaseSelector,
    PostgresDelegatedWorkStore,
)
from eva.adapters.subagents.tool_free_child import (
    OUTPUT_SCHEMA_SHA256,
    ClosedModelRegistry,
    PydanticToolFreeChildRunner,
    ToolFreeChildProfile,
)
from eva.bootstrap.delegated_work_service import (
    DelegatedWorkService,
    DelegatedWorkServiceSettings,
    run_delegated_work_service,
    serve_delegated_work_service,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.operations.delegated_work_worker import DelegatedWorkWorker
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage


class _ProcessFixtureModel(FunctionModel):
    def __init__(self, *, mode: str, call_counter_path: Path) -> None:
        self._mode = mode
        self._call_counter_path = call_counter_path
        super().__init__(self._respond)

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        del messages, model_settings, model_request_parameters
        return RequestUsage(input_tokens=20)

    async def _respond(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        del messages
        assert info.function_tools == []
        descriptor = os.open(
            self._call_counter_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
            0o600,
        )
        try:
            os.write(descriptor, b"provider-call\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if self._mode == "slow":
            await asyncio.sleep(300)
        return ModelResponse(
            parts=[
                TextPart(
                    '{"summary":"procesgrens bewezen",'
                    '"findings":["een apart OS-proces leverde dit resultaat"]}'
                )
            ],
            usage=RequestUsage(input_tokens=20, output_tokens=8),
            model_name="process-fixture",
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


class _PauseAfterClaimStore:
    """Test barrier proving the lease exists before the process is killed."""

    def __init__(self, store: PostgresDelegatedWorkStore, marker: Path) -> None:
        self._store = store
        self._marker = marker

    def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
        claim = self._store.claim_next(**kwargs)
        if claim is not None:
            self._marker.write_text("claimed\n")
            self._marker.chmod(0o600)
            while True:
                time.sleep(1)
        return claim

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._store, name)


def _clock() -> datetime:
    return datetime.strptime(_required("EVA_TEST_NOW"), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )


def _run_paused_after_claim(
    runner: PydanticToolFreeChildRunner,
    release: DelegatedWorkReleaseSelector,
) -> None:
    settings = DelegatedWorkServiceSettings.from_environment()
    database_dsn = read_runtime_secret_file(
        settings.database_dsn_file,
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    engine = sa.create_engine(database_dsn, pool_pre_ping=True)
    try:
        store = _PauseAfterClaimStore(
            PostgresDelegatedWorkStore(
                engine,
                settings.scope,
            ),
            Path(_required("EVA_TEST_AFTER_CLAIM_MARKER")),
        )
        worker = DelegatedWorkWorker(
            store,  # type: ignore[arg-type]
            runner,
            worker_id=settings.worker_id,
            release=release,
            clock=_clock,
        )
        asyncio.run(
            DelegatedWorkService(
                worker,
                poll_interval_seconds=settings.poll_interval_seconds,
            ).serve(once=True)
        )
    finally:
        engine.dispose()


def _test_provider_credentials() -> tuple[str, str]:
    value = json.loads(Path(_required("EVA_TEST_PROVIDER_LEASE_FILE")).read_text())
    if not isinstance(value, dict) or set(value) != {"access_token", "account_id"}:
        raise RuntimeError("test provider lease shape differs")
    token = value["access_token"]
    account_id = value["account_id"]
    if not isinstance(token, str) or not token:
        raise RuntimeError("test provider token is missing")
    if not isinstance(account_id, str) or not account_id:
        raise RuntimeError("test provider account is missing")
    return token, account_id


async def _run_official_sdk() -> None:
    from eva.adapters.model.openai_child_provider import (
        OpenAIChildProvider,
        OpenAIChildProviderSettings,
    )
    from eva.bootstrap.delegated_work_provider import (
        ENGINE_RELEASE_SHA256,
        INPUT_PRICE_USD_MICROS_PER_MILLION,
        MODEL_NAME,
        MODEL_PROFILE_ID,
        MODEL_PROFILE_RELEASE_SHA256,
        OUTPUT_PRICE_USD_MICROS_PER_MILLION,
        WORKER_INSTRUCTIONS,
        WORKER_PROFILE_ID,
        WORKER_PROFILE_RELEASE_SHA256,
        WORKER_PROGRAM_SHA256,
    )

    provider = OpenAIChildProvider(
        OpenAIChildProviderSettings(
            endpoint=_required("EVA_TEST_PROVIDER_ENDPOINT"),
            model_name=MODEL_NAME,
            timeout_seconds=30,
        ),
        credential_loader=_test_provider_credentials,
    )
    async with provider as model:
        profile = ToolFreeChildProfile(
            model_profile_id=MODEL_PROFILE_ID,
            model_profile_release_sha256=MODEL_PROFILE_RELEASE_SHA256,
            worker_profile_id=WORKER_PROFILE_ID,
            worker_profile_release_sha256=WORKER_PROFILE_RELEASE_SHA256,
            instructions=WORKER_INSTRUCTIONS,
            model=model,
            input_cost_usd_micros_per_million_tokens=(
                INPUT_PRICE_USD_MICROS_PER_MILLION
            ),
            output_cost_usd_micros_per_million_tokens=(
                OUTPUT_PRICE_USD_MICROS_PER_MILLION
            ),
        )
        runner = PydanticToolFreeChildRunner(
            ClosedModelRegistry({MODEL_PROFILE_ID: profile}),
            worker_program_sha256=WORKER_PROGRAM_SHA256,
            engine_release_sha256=ENGINE_RELEASE_SHA256,
            clock=_clock,
        )
        settings = DelegatedWorkServiceSettings.from_environment()
        await serve_delegated_work_service(
            runner,
            release=DelegatedWorkReleaseSelector(
                worker_profile_release_sha256=WORKER_PROFILE_RELEASE_SHA256,
                model_profile_release_sha256=MODEL_PROFILE_RELEASE_SHA256,
                worker_program_sha256=WORKER_PROGRAM_SHA256,
                engine_release_sha256=ENGINE_RELEASE_SHA256,
                output_schema_sha256=OUTPUT_SCHEMA_SHA256,
            ),
            settings=settings,
            once=True,
            clock=_clock,
        )


def main() -> int:
    forbidden = (
        "EVA_DATABASE_DSN",
        "EVA2_TEST_POSTGRES_DSN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "EVA_TEST_PARENT_SECRET_CANARY",
    )
    leaked = [name for name in forbidden if name in os.environ]
    if leaked:
        raise RuntimeError("parent process secrets reached delegated worker")
    mode = _required("EVA_TEST_SUBAGENT_MODE")
    if mode == "official_sdk":
        asyncio.run(_run_official_sdk())
        return 0
    model = _ProcessFixtureModel(
        mode=mode,
        call_counter_path=Path(_required("EVA_TEST_CALL_COUNTER")),
    )
    profile = ToolFreeChildProfile(
        model_profile_id="model:test:v1",
        model_profile_release_sha256="66" * 32,
        worker_profile_id="worker:research:v1",
        worker_profile_release_sha256="55" * 32,
        instructions="Beantwoord uitsluitend de toegelaten onderzoekstaak.",
        model=model,
        input_cost_usd_micros_per_million_tokens=0,
        output_cost_usd_micros_per_million_tokens=0,
    )
    program_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runner = PydanticToolFreeChildRunner(
        ClosedModelRegistry({profile.model_profile_id: profile}),
        worker_program_sha256=program_sha256,
        engine_release_sha256="88" * 32,
        clock=_clock,
    )
    release = DelegatedWorkReleaseSelector(
        worker_profile_release_sha256=profile.worker_profile_release_sha256,
        model_profile_release_sha256=profile.model_profile_release_sha256,
        worker_program_sha256=program_sha256,
        engine_release_sha256="88" * 32,
        output_schema_sha256=OUTPUT_SCHEMA_SHA256,
    )
    if mode == "pause_after_claim":
        _run_paused_after_claim(runner, release)
    else:
        run_delegated_work_service(
            runner, release=release, once=True, clock=_clock
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
