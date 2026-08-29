"""Dependency-backed tool-free child runner on Pydantic AI's public API.

Pydantic AI owns model execution, structured output, cancellation and its
portable usage counters. Eva adds only the authority that a generic model
library cannot know: a sealed worker/model profile, absolute deadline, hard
work budget and durable wire result. The durable one-call fence still belongs
to PostgreSQL and is deliberately outside this API spike.
"""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from pydantic_ai import (
    Agent,
    CancellationToken,
    FallbackExceptionGroup,
    ModelAPIError,
    ModelHTTPError,
    PromptedOutput,
    RunCancelled,
    RunUsage,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.usage import UsageLimits

from eva.contracts.base import Identifier, NaturalText, Sha256
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.delegated_work import (
    DelegatedWorkResult,
    DelegatedWorkSpec,
    DelegatedWorkUsage,
)

Finding = Annotated[str, StringConstraints(min_length=1, max_length=1_000)]


class EvaToolFreeChildOutputV1(BaseModel):
    """Small durable output; no model-visible output tool is created."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    summary: str = Field(min_length=1, max_length=4_000)
    findings: list[Finding] = Field(default_factory=list, max_length=16)


OUTPUT_SCHEMA_SHA256 = canonical_sha256(EvaToolFreeChildOutputV1.model_json_schema())
_CALL_ID = TypeAdapter(Identifier)
_PROFILE_ID = TypeAdapter(Identifier)
_SHA256 = TypeAdapter(Sha256)
_INSTRUCTIONS = TypeAdapter(NaturalText)
_ONE_MILLION = Decimal(1_000_000)
_MAX_PORTABLE_COUNTER = 2_000_000

OutcomeStatus = Literal[
    "succeeded",
    "deadline_expired",
    "budget_exceeded",
    "cancelled",
    "timed_out",
    "provider_rejected",
    "provider_transient",
    "provider_unavailable",
    "invalid_output",
    "invalid_usage",
    "misconfigured",
    "internal_error",
]


@dataclass(frozen=True, slots=True)
class ToolFreeChildOutcome:
    status: OutcomeStatus
    result: DelegatedWorkResult | None
    usage: DelegatedWorkUsage
    retryable: bool
    error_fingerprint: str | None
    provider_status_code: int | None = None

    def __post_init__(self) -> None:
        if (self.status == "succeeded") != (self.result is not None):
            raise ValueError("child success and result presence differ")
        if (self.status == "succeeded") == (self.error_fingerprint is not None):
            raise ValueError("child error fingerprint presence differs")


@dataclass(frozen=True, slots=True)
class ToolFreeChildProfile:
    """Operator-built release binding; durable text never constructs a model."""

    model_profile_id: str
    model_profile_release_sha256: str
    worker_profile_id: str
    worker_profile_release_sha256: str
    instructions: str
    model: Model
    input_cost_usd_micros_per_million_tokens: int
    output_cost_usd_micros_per_million_tokens: int

    def __post_init__(self) -> None:
        _PROFILE_ID.validate_python(self.model_profile_id, strict=True)
        _PROFILE_ID.validate_python(self.worker_profile_id, strict=True)
        _SHA256.validate_python(self.model_profile_release_sha256, strict=True)
        _SHA256.validate_python(self.worker_profile_release_sha256, strict=True)
        _INSTRUCTIONS.validate_python(self.instructions, strict=True)
        if not isinstance(self.model, Model):
            raise TypeError("child profile model is invalid")
        if isinstance(self.model, (FallbackModel, InstrumentedModel)):
            raise TypeError("fallback and instrumented child models are forbidden")
        for rate in (
            self.input_cost_usd_micros_per_million_tokens,
            self.output_cost_usd_micros_per_million_tokens,
        ):
            if (
                isinstance(rate, bool)
                or not isinstance(rate, int)
                or not 0 <= rate <= 1_000_000_000
            ):
                raise ValueError("child profile price is invalid")

    def cost_usd_micros(self, *, input_tokens: int, output_tokens: int) -> int:
        return _rated_cost(
            input_tokens, self.input_cost_usd_micros_per_million_tokens
        ) + _rated_cost(output_tokens, self.output_cost_usd_micros_per_million_tokens)


class ClosedModelRegistry:
    """Resolve only sealed profiles; never infer provider data from durable text."""

    def __init__(self, profiles: Mapping[str, ToolFreeChildProfile]) -> None:
        if not profiles or any(
            key != profile.model_profile_id for key, profile in profiles.items()
        ):
            raise ValueError("closed child model registry is invalid")
        self._profiles = MappingProxyType(dict(profiles))

    def resolve(self, spec: DelegatedWorkSpec) -> ToolFreeChildProfile:
        try:
            profile = self._profiles[spec.model_profile_id]
        except KeyError as error:
            raise LookupError("child model profile is not allowed") from error
        if (
            spec.model_profile_release_sha256 != profile.model_profile_release_sha256
            or spec.worker_profile_id != profile.worker_profile_id
            or spec.worker_profile_release_sha256
            != profile.worker_profile_release_sha256
        ):
            raise LookupError("child model or worker profile release differs")
        return profile


class PydanticToolFreeChildRunner:
    """Run one already-authorized child with no tools or ambient context."""

    def __init__(
        self,
        registry: ClosedModelRegistry,
        *,
        worker_program_sha256: str,
        engine_release_sha256: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._worker_program_sha256 = worker_program_sha256
        self._engine_release_sha256 = engine_release_sha256
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    async def run(
        self,
        spec: DelegatedWorkSpec,
        *,
        call_id: str,
        cancellation_event: asyncio.Event | None = None,
    ) -> ToolFreeChildOutcome:
        try:
            _CALL_ID.validate_python(call_id, strict=True)
        except ValueError as error:
            return self._error("misconfigured", RunUsage(), error, retryable=False)
        try:
            profile = self._registry.resolve(spec)
        except LookupError as error:
            return self._error("misconfigured", RunUsage(), error, retryable=False)
        preflight = self._preflight(spec, profile)
        if preflight is not None:
            return preflight

        remaining_seconds = (
            _parse_timestamp(spec.deadline) - self._now()
        ).total_seconds()
        effective_timeout = min(float(spec.grant.timeout_seconds), remaining_seconds)
        if effective_timeout <= 0:
            return self._error(
                "deadline_expired",
                RunUsage(),
                RuntimeError("durable child deadline expired"),
                retryable=False,
            )

        usage = RunUsage()
        agent = Agent(
            None,
            name=spec.worker_name,
            instructions=profile.instructions,
            output_type=PromptedOutput(
                EvaToolFreeChildOutputV1,
                name="EvaToolFreeChildOutputV1",
                description="One bounded analysis result with a summary and findings.",
            ),
            toolsets=(),
            capabilities=(),
        )
        # A process-wide Agent.instrument_all() must never expose this private task.
        agent.instrument = False
        limits = UsageLimits(
            request_limit=spec.grant.maximum_completed_model_responses,
            tool_calls_limit=0,
            input_tokens_limit=spec.grant.maximum_input_tokens,
            output_tokens_limit=spec.grant.maximum_output_tokens,
            total_tokens_limit=spec.grant.maximum_total_tokens,
            per_request_input_tokens_limit=spec.grant.maximum_input_tokens,
            count_tokens_before_request=True,
        )
        cancellation_token = CancellationToken()
        cancellation_watcher: asyncio.Task[None] | None = None
        if cancellation_event is not None:
            cancellation_watcher = asyncio.create_task(
                _forward_cancellation(cancellation_event, cancellation_token)
            )
        try:
            async with asyncio.timeout(effective_timeout):
                run_result = await agent.run(
                    spec.task,
                    model=profile.model,
                    model_settings={
                        "max_tokens": spec.grant.maximum_output_tokens,
                        "parallel_tool_calls": False,
                    },
                    message_history=None,
                    deps=None,
                    usage=usage,
                    usage_limits=limits,
                    cancellation_token=cancellation_token,
                    retries={"tools": 0, "output": 0},
                    toolsets=(),
                    capabilities=(),
                    infer_name=False,
                )
        except UsageLimitExceeded as error:
            return self._error("budget_exceeded", usage, error, retryable=False)
        except RunCancelled as error:
            return self._error("cancelled", error.usage, error, retryable=False)
        except TimeoutError as error:
            partial = RunCancelled.from_cancellation(error)
            return self._error(
                "timed_out",
                partial.usage if partial is not None else usage,
                error,
                retryable=False,
            )
        except asyncio.CancelledError:
            raise
        except ModelHTTPError as error:
            transient = error.status_code in {408, 429} or error.status_code >= 500
            return self._error(
                "provider_transient" if transient else "provider_rejected",
                usage,
                error,
                retryable=transient,
                provider_status_code=error.status_code,
            )
        except (ModelAPIError, FallbackExceptionGroup) as error:
            return self._error("provider_unavailable", usage, error, retryable=True)
        except UnexpectedModelBehavior as error:
            return self._error("invalid_output", usage, error, retryable=False)
        except (UserError, NotImplementedError) as error:
            return self._error("misconfigured", usage, error, retryable=False)
        except Exception as error:  # noqa: BLE001 - redact every unknown provider failure
            return self._error("internal_error", usage, error, retryable=False)
        finally:
            if cancellation_watcher is not None:
                cancellation_watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await cancellation_watcher

        measured = _normalized_usage(run_result.usage, profile=profile, complete=True)
        if measured is None:
            return self._error(
                "invalid_usage",
                RunUsage(),
                RuntimeError("provider returned invalid child usage"),
                retryable=False,
            )
        assert measured.completed_model_responses is not None
        assert measured.tool_calls is not None
        assert measured.input_tokens is not None
        assert measured.output_tokens is not None
        assert measured.total_tokens is not None
        assert measured.cost_usd_micros is not None
        if measured.tool_calls != 0 or measured.completed_model_responses != 1:
            return self._error(
                "invalid_usage",
                run_result.usage,
                RuntimeError("tool-free child usage invariant failed"),
                retryable=False,
            )
        if (
            measured.input_tokens > spec.grant.maximum_input_tokens
            or measured.output_tokens > spec.grant.maximum_output_tokens
            or measured.total_tokens > spec.grant.maximum_total_tokens
            or measured.cost_usd_micros > spec.grant.maximum_cost_usd_micros
        ):
            return self._error(
                "budget_exceeded",
                run_result.usage,
                RuntimeError("child usage exceeded its durable grant"),
                retryable=False,
            )
        completed_at = self._now()
        if completed_at >= _parse_timestamp(spec.deadline):
            return self._error(
                "timed_out",
                run_result.usage,
                RuntimeError("child completed after its durable deadline"),
                retryable=False,
            )
        try:
            output = canonical_json_bytes(
                run_result.output.model_dump(mode="json")
            ).decode("utf-8")
            result = DelegatedWorkResult(
                contract="DelegatedWorkResult/v1",
                schema_version=1,
                work_id=spec.work_id,
                call_id=call_id,
                output=output,
                output_sha256=hashlib.sha256(output.encode()).hexdigest(),
                spec_sha256=spec.sha256,
                usage=measured.to_payload(),
                completed_at=_canonical_timestamp(completed_at),
            )
        except ValueError as error:
            return self._error(
                "invalid_output", run_result.usage, error, retryable=False
            )
        return ToolFreeChildOutcome(
            status="succeeded",
            result=result,
            usage=measured,
            retryable=False,
            error_fingerprint=None,
        )

    def _preflight(
        self, spec: DelegatedWorkSpec, profile: ToolFreeChildProfile
    ) -> ToolFreeChildOutcome | None:
        if self._now() >= _parse_timestamp(spec.deadline):
            return self._error(
                "deadline_expired",
                RunUsage(),
                RuntimeError("durable child deadline expired"),
                retryable=False,
            )
        expected = (
            spec.output_schema_sha256 == OUTPUT_SCHEMA_SHA256
            and spec.worker_program_sha256 == self._worker_program_sha256
            and spec.engine_release_sha256 == self._engine_release_sha256
        )
        if not expected:
            return self._error(
                "misconfigured",
                RunUsage(),
                RuntimeError("durable child release binding differs"),
                retryable=False,
            )
        worst_case_cost = profile.cost_usd_micros(
            input_tokens=spec.grant.maximum_input_tokens,
            output_tokens=spec.grant.maximum_output_tokens,
        )
        if worst_case_cost > spec.grant.maximum_cost_usd_micros:
            return self._error(
                "budget_exceeded",
                RunUsage(),
                RuntimeError("durable child cost reserve is insufficient"),
                retryable=False,
            )
        return None

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("child clock must return UTC")
        return now.astimezone(UTC)

    @staticmethod
    def _error(
        status: OutcomeStatus,
        usage: RunUsage,
        error: BaseException,
        *,
        retryable: bool,
        provider_status_code: int | None = None,
    ) -> ToolFreeChildOutcome:
        # Never hash or persist exception text: provider bodies and headers can hold secrets.
        fingerprint = canonical_sha256(
            {
                "category": status,
                "exception_type": type(error).__name__,
                "provider_status_code": provider_status_code,
            }
        )
        normalized = _normalized_usage(usage, profile=None, complete=False)
        if normalized is None:
            status = "invalid_usage"
            retryable = False
            fingerprint = canonical_sha256(
                {
                    "category": status,
                    "exception_type": "InvalidProviderUsage",
                    "provider_status_code": provider_status_code,
                }
            )
            normalized = DelegatedWorkUsage.unmeasured()
        return ToolFreeChildOutcome(
            status=status,
            result=None,
            usage=normalized,
            retryable=retryable,
            error_fingerprint=fingerprint,
            provider_status_code=provider_status_code,
        )


def _normalized_usage(
    usage: RunUsage,
    *,
    profile: ToolFreeChildProfile | None,
    complete: bool,
) -> DelegatedWorkUsage | None:
    """Validate provider counters; unknown and failed-attempt usage never become zero."""

    try:
        responses = _portable_counter(usage.requests)
        tool_calls = _portable_counter(usage.tool_calls)
        input_tokens = _portable_counter(usage.input_tokens)
        output_tokens = _portable_counter(usage.output_tokens)
        provider_cost = usage.cost
        if provider_cost is not None:
            provider_cost = Decimal(provider_cost)
            if not provider_cost.is_finite() or provider_cost < 0:
                return None
    except (AttributeError, TypeError, ValueError):
        return None

    if complete:
        if profile is None:
            return None
        rated_cost = profile.cost_usd_micros(
            input_tokens=input_tokens, output_tokens=output_tokens
        )
        reported_cost = 0
        if provider_cost is not None:
            reported_cost = int(
                (provider_cost * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING)
            )
        cost = max(rated_cost, reported_cost)
        try:
            return DelegatedWorkUsage(
                contract="DelegatedWorkUsage/v1",
                schema_version=1,
                measurement="measured",
                completed_model_responses=responses,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cost_usd_micros=cost,
            )
        except ValueError:
            return None

    if (
        not any((responses, tool_calls, input_tokens, output_tokens))
        and provider_cost is None
    ):
        return DelegatedWorkUsage.unmeasured()
    cost_micros = None
    if provider_cost is not None:
        cost_micros = int(
            (provider_cost * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING)
        )
    try:
        return DelegatedWorkUsage(
            contract="DelegatedWorkUsage/v1",
            schema_version=1,
            measurement="partial",
            completed_model_responses=responses,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd_micros=cost_micros,
        )
    except ValueError:
        return None


def _portable_counter(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("provider usage counter is not an integer")
    if not 0 <= value <= _MAX_PORTABLE_COUNTER:
        raise ValueError("provider usage counter is outside the portable range")
    return value


def _rated_cost(tokens: int, micros_per_million: int) -> int:
    amount = Decimal(tokens) * Decimal(micros_per_million) / _ONE_MILLION
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _forward_cancellation(event: asyncio.Event, token: CancellationToken) -> None:
    if not event.is_set():
        await event.wait()
    token.cancel()


__all__ = [
    "OUTPUT_SCHEMA_SHA256",
    "ClosedModelRegistry",
    "EvaToolFreeChildOutputV1",
    "PydanticToolFreeChildRunner",
    "ToolFreeChildOutcome",
    "ToolFreeChildProfile",
]
