"""Closed release composition for Eva's first real tool-free subagent."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re

from eva.adapters.model.openai_codex_provider import (
    CodexProviderError,
    OpenAICodexProvider,
    OpenAICodexProviderSettings,
)
from eva.adapters.subagents.tool_free_child import (
    OUTPUT_SCHEMA_SHA256,
    ClosedModelRegistry,
    PydanticToolFreeChildRunner,
    ToolFreeChildOutcome,
    ToolFreeChildProfile,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.credentials.runtime_files import read_shared_runtime_file_bytes
from eva.contracts.delegated_work import (
    DelegatedWorkGrant, DelegatedWorkSpec, DelegatedWorkUsage,
)

_MODEL_LEASE_OWNER_UID = 1000
_MODEL_LEASE_READER_GID = 65532
_MODEL_LEASE_FILE_MODE = 0o640
_SHA256 = re.compile(r"[0-9a-f]{64}")

MODEL_PROFILE_ID = "model:codex:gpt-5.4-mini:v1"
WORKER_PROFILE_ID = "worker:research:v1"
MODEL_NAME = "gpt-5.4-mini"
MODEL_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
MODEL_TIMEOUT_SECONDS = 30
MODEL_MAXIMUM_REQUEST_BYTES = 256 * 1024
MODEL_MAXIMUM_RESPONSE_BYTES = 256 * 1024
INPUT_PRICE_USD_MICROS_PER_MILLION = 750_000
OUTPUT_PRICE_USD_MICROS_PER_MILLION = 4_500_000
PYDANTIC_AI_VERSION = "2.37.0"
OPENAI_SDK_VERSION = "3.6.0"
WORKER_INSTRUCTIONS = (
    "Werk als een afgeschermde onderzoeker voor Eva. Gebruik geen tools of externe "
    "bronnen. Scheid controleerbare bevindingen van aannames, blijf bij de gegeven "
    "taak en geef compact geldig JSON volgens het gevraagde schema."
)

MODEL_PROFILE_RELEASE_SHA256 = canonical_sha256(
    {
        "contract": "EvaDelegatedModelProfile/v1",
        "endpoint": MODEL_ENDPOINT,
        "input_preflight": "conservative_wire_byte_preflight_v1",
        "provider_adapter": "OpenAICodexProvider",
        "usage_attestation": "raw_response_completed_v1",
        "maximum_request_bytes": MODEL_MAXIMUM_REQUEST_BYTES,
        "maximum_response_bytes": MODEL_MAXIMUM_RESPONSE_BYTES,
        "model": MODEL_NAME,
        "openai_sdk": OPENAI_SDK_VERSION,
        "output_budget_enforcement": "verified_after_provider_completion",
        "output_price_usd_micros_per_million": (
            OUTPUT_PRICE_USD_MICROS_PER_MILLION
        ),
        "input_price_usd_micros_per_million": INPUT_PRICE_USD_MICROS_PER_MILLION,
        "pydantic_ai": PYDANTIC_AI_VERSION,
        "reasoning_effort": "low",
        "timeout_seconds": MODEL_TIMEOUT_SECONDS,
    }
)
WORKER_PROFILE_RELEASE_SHA256 = canonical_sha256(
    {
        "contract": "EvaDelegatedWorkerProfile/v1",
        "instructions": WORKER_INSTRUCTIONS,
        "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
        "worker_profile_id": WORKER_PROFILE_ID,
    }
)
_EVA_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_LOCK_SHA256 = (
    "036e2463be9fea6b3adb8528e8e0288b0985f65f958d4abdbf371c21d828693e"
)


def _source_hashes() -> dict[str, str]:
    relative_paths = (
        "adapters/model/__init__.py",
        "adapters/model/openai_codex_provider.py",
        "adapters/model/openai_endpoint.py",
        "adapters/postgres/delegated_work.py",
        "adapters/subagents/tool_free_child.py",
        "adapters/credentials/runtime_files.py",
        "bootstrap/__init__.py",
        "bootstrap/delegated_work_provider.py",
        "bootstrap/delegated_work_service.py",
        "contracts/delegated_work.py",
        "operations/delegated_work_worker.py",
    )
    return {
        path: hashlib.sha256((_EVA_PACKAGE_ROOT / path).read_bytes()).hexdigest()
        for path in relative_paths
    }


WORKER_PROGRAM_SHA256 = canonical_sha256(
    {
        "contract": "EvaDelegatedWorkerProgram/v1",
        "source_sha256": _source_hashes(),
    }
)
ENGINE_RELEASE_SHA256 = canonical_sha256(
    {
        "contract": "EvaDelegatedEngineRelease/v1",
        "dependency_lock_sha256": DEPENDENCY_LOCK_SHA256,
        "openai_sdk": OPENAI_SDK_VERSION,
        "pydantic_ai": PYDANTIC_AI_VERSION,
    }
)


def verify_delegated_provider_dependencies() -> None:
    """Refuse an installed dependency version outside the sealed profile."""

    expected = {
        "openai": OPENAI_SDK_VERSION,
        "pydantic-ai-slim": PYDANTIC_AI_VERSION,
    }
    try:
        installed = {package: version(package) for package in expected}
    except PackageNotFoundError as error:
        raise RuntimeError("delegated provider dependency is unavailable") from error
    if installed != expected:
        raise RuntimeError("delegated provider dependency release differs")


def build_unadmitted_delegated_work_spec(
    *,
    work_id: str,
    episode_id: str,
    generation: int,
    parent_task_id: str,
    owner_principal_id: str,
    principal_binding_id: str,
    task: str,
    created_at: datetime,
    deadline: datetime,
) -> DelegatedWorkSpec:
    """Build a sealed candidate; PostgreSQL admission remains the authority check."""

    def timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
            raise ValueError("delegated work time must be aware whole seconds")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    grant = DelegatedWorkGrant(
        contract="DelegatedWorkGrant/v1",
        schema_version=1,
        context_refs=[],
        capability_ids=[],
        workspace_ref=None,
        child_network_access=False,
        maximum_model_runs=1,
        maximum_completed_model_responses=1,
        maximum_input_tokens=4_000,
        maximum_output_tokens=500,
        maximum_total_tokens=4_500,
        maximum_cost_usd_micros=10_000,
        timeout_seconds=MODEL_TIMEOUT_SECONDS,
        maximum_claims_before_call_start=2,
        delegation_depth=1,
    )
    return DelegatedWorkSpec(
        contract="DelegatedWork/v1",
        schema_version=1,
        work_id=work_id,
        episode_id=episode_id,
        generation=generation,
        parent_task_id=parent_task_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        audience="private",
        worker_name="onderzoeker",
        worker_profile_id=WORKER_PROFILE_ID,
        worker_profile_release_sha256=WORKER_PROFILE_RELEASE_SHA256,
        task=task,
        model_profile_id=MODEL_PROFILE_ID,
        model_profile_release_sha256=MODEL_PROFILE_RELEASE_SHA256,
        worker_program_sha256=WORKER_PROGRAM_SHA256,
        engine_release_sha256=ENGINE_RELEASE_SHA256,
        output_schema_sha256=OUTPUT_SCHEMA_SHA256,
        grant=grant.to_payload(),
        created_at=timestamp(created_at),
        deadline=timestamp(deadline),
    )


def _read_access_lease(auth_file: Path) -> tuple[str, str]:
    try:
        raw = read_shared_runtime_file_bytes(
            str(auth_file),
            setting_name="delegated child model access lease",
            owner_uid=_MODEL_LEASE_OWNER_UID,
            reader_gid=_MODEL_LEASE_READER_GID,
            required_mode=_MODEL_LEASE_FILE_MODE,
        )
        value = json.loads(raw.decode("utf-8"))
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("delegated child model access lease is unavailable") from error
    if not isinstance(value, dict) or set(value) != {
        "access_token",
        "account_id",
        "contract",
        "source_sha256",
    }:
        raise RuntimeError("delegated child model access lease shape differs")
    if value.get("contract") != "EvaModelAccessLease/v1":
        raise RuntimeError("delegated child model access lease contract differs")
    source_sha256 = value.get("source_sha256")
    access_token = value.get("access_token")
    account_id = value.get("account_id")
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise RuntimeError("delegated child model access lease hash is invalid")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("delegated child model access token is missing")
    if not isinstance(account_id, str) or not account_id:
        raise RuntimeError("delegated child model account ID is missing")
    return access_token, account_id


def validate_delegated_model_access_lease(auth_file: Path) -> None:
    """Validate the shared lease without exposing its sensitive values."""

    _read_access_lease(auth_file)


class _CodexDelegatedWorkRunner:
    """Give each durably admitted child a fresh shared-provider deadline."""

    def __init__(self, lease_file: Path, clock: Callable[[], datetime] | None):
        self._lease_file = lease_file
        self._clock = clock

    async def run(
        self, spec: DelegatedWorkSpec, *, call_id: str,
        cancellation_event: asyncio.Event | None = None,
    ) -> ToolFreeChildOutcome:
        provider = OpenAICodexProvider(
            OpenAICodexProviderSettings(
                endpoint=MODEL_ENDPOINT,
                model_name=MODEL_NAME,
                timeout_seconds=MODEL_TIMEOUT_SECONDS,
                reasoning_effort="low",
                maximum_request_bytes=MODEL_MAXIMUM_REQUEST_BYTES,
                maximum_response_bytes=MODEL_MAXIMUM_RESPONSE_BYTES,
            ),
            credential_loader=lambda: _read_access_lease(self._lease_file),
        )
        outcome = None
        try:
            async with provider as session:
                profile = ToolFreeChildProfile(
                    model_profile_id=MODEL_PROFILE_ID,
                    model_profile_release_sha256=MODEL_PROFILE_RELEASE_SHA256,
                    worker_profile_id=WORKER_PROFILE_ID,
                    worker_profile_release_sha256=WORKER_PROFILE_RELEASE_SHA256,
                    instructions=WORKER_INSTRUCTIONS,
                    model=session.model,
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
                    clock=self._clock,
                )
                outcome = await runner.run(
                    spec, call_id=call_id, cancellation_event=cancellation_event,
                )
                if outcome.result is not None:
                    raw = session.consume_raw_usage()
                    if (
                        outcome.usage.input_tokens != raw.input_tokens
                        or outcome.usage.output_tokens != raw.output_tokens
                        or outcome.usage.total_tokens != raw.total_tokens
                    ):
                        raise CodexProviderError("protocol")
                return outcome
        except CodexProviderError as error:
            # No retry after the durable call fence; preserve measured usage if
            # a completed response failed the independent raw-usage comparison.
            fingerprint = hashlib.sha256(
                ("delegated-shared-provider:" + error.category).encode()
            ).hexdigest()
            if outcome is not None:
                return replace(
                    outcome, status="invalid_usage", result=None,
                    retryable=False, error_fingerprint=fingerprint,
                )
            return ToolFreeChildOutcome(
                status="misconfigured" if error.category == "configuration"
                else "internal_error",
                result=None, usage=DelegatedWorkUsage.unmeasured(),
                retryable=False, error_fingerprint=fingerprint,
            )


@asynccontextmanager
async def open_delegated_work_runner(
    *,
    model_access_lease_file: Path,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[_CodexDelegatedWorkRunner]:
    """Keep the worker idle without opening or aging a provider session."""

    verify_delegated_provider_dependencies()
    yield _CodexDelegatedWorkRunner(model_access_lease_file, clock)


__all__ = [
    "ENGINE_RELEASE_SHA256",
    "DEPENDENCY_LOCK_SHA256",
    "MODEL_PROFILE_ID",
    "MODEL_PROFILE_RELEASE_SHA256",
    "WORKER_PROFILE_ID",
    "WORKER_PROFILE_RELEASE_SHA256",
    "WORKER_PROGRAM_SHA256",
    "build_unadmitted_delegated_work_spec",
    "open_delegated_work_runner",
    "verify_delegated_provider_dependencies",
    "validate_delegated_model_access_lease",
]
