"""Closed release composition for Eva's first real tool-free subagent."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re

from eva.adapters.model.openai_child_provider import (
    OpenAIChildProvider,
    OpenAIChildProviderSettings,
)
from eva.adapters.subagents.tool_free_child import (
    OUTPUT_SCHEMA_SHA256,
    ClosedModelRegistry,
    PydanticToolFreeChildRunner,
    ToolFreeChildProfile,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.bootstrap.secrets import read_shared_runtime_file_bytes

_MODEL_LEASE_OWNER_UID = 1000
_MODEL_LEASE_READER_GID = 65532
_MODEL_LEASE_FILE_MODE = 0o640
_SHA256 = re.compile(r"[0-9a-f]{64}")

MODEL_PROFILE_ID = "model:codex:gpt-5.4-mini:v1"
WORKER_PROFILE_ID = "worker:onderzoeker:v1"
MODEL_NAME = "gpt-5.4-mini"
MODEL_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
MODEL_TIMEOUT_SECONDS = 30
MODEL_MAXIMUM_REQUEST_BYTES = 256 * 1024
MODEL_MAXIMUM_RESPONSE_BYTES = 256 * 1024
INPUT_PRICE_USD_MICROS_PER_MILLION = 750_000
OUTPUT_PRICE_USD_MICROS_PER_MILLION = 4_500_000
PYDANTIC_AI_VERSION = "2.32.1"
OPENAI_SDK_VERSION = "3.3.1"
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
WORKER_PROGRAM_SHA256 = canonical_sha256(
    {
        "contract": "EvaDelegatedWorkerProgram/v1",
        "provider_boundary": "openai_child_provider.py",
        "runner_boundary": "tool_free_child.py",
    }
)
ENGINE_RELEASE_SHA256 = canonical_sha256(
    {
        "contract": "EvaDelegatedEngineRelease/v1",
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


@asynccontextmanager
async def open_delegated_work_runner(
    *,
    model_access_lease_file: Path,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[PydanticToolFreeChildRunner]:
    """Open one closed official-SDK provider and return Eva's existing runner."""

    verify_delegated_provider_dependencies()
    provider = OpenAIChildProvider(
        OpenAIChildProviderSettings(
            endpoint=MODEL_ENDPOINT,
            model_name=MODEL_NAME,
            timeout_seconds=MODEL_TIMEOUT_SECONDS,
            reasoning_effort="low",
            maximum_request_bytes=MODEL_MAXIMUM_REQUEST_BYTES,
            maximum_response_bytes=MODEL_MAXIMUM_RESPONSE_BYTES,
        ),
        credential_loader=lambda: _read_access_lease(model_access_lease_file),
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
        yield PydanticToolFreeChildRunner(
            ClosedModelRegistry({MODEL_PROFILE_ID: profile}),
            worker_program_sha256=WORKER_PROGRAM_SHA256,
            engine_release_sha256=ENGINE_RELEASE_SHA256,
            clock=clock,
        )


__all__ = [
    "ENGINE_RELEASE_SHA256",
    "MODEL_PROFILE_ID",
    "MODEL_PROFILE_RELEASE_SHA256",
    "WORKER_PROFILE_ID",
    "WORKER_PROFILE_RELEASE_SHA256",
    "WORKER_PROGRAM_SHA256",
    "open_delegated_work_runner",
    "verify_delegated_provider_dependencies",
]
