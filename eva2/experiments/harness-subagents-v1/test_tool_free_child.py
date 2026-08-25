from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib

import pytest
from pydantic_ai import Agent, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from eva.contracts.delegated_work import (
    DelegatedWorkGrant,
    DelegatedWorkResult,
    DelegatedWorkSpec,
    DelegatedWorkUsage,
)
from tool_free_child import (
    ClosedModelRegistry,
    OUTPUT_SCHEMA_SHA256,
    PydanticToolFreeChildRunner,
    ToolFreeChildProfile,
)


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
HASH_A = hashlib.sha256(b"worker-program").hexdigest()
HASH_B = hashlib.sha256(b"engine-release").hexdigest()
MODEL_RELEASE = hashlib.sha256(b"model-profile-release").hexdigest()
WORKER_RELEASE = hashlib.sha256(b"worker-profile-release").hexdigest()
SEALED_INSTRUCTIONS = "Controleer de aangeleverde bewering onafhankelijk."


class CountedFunctionModel(FunctionModel):
    def __init__(self, function, *, predicted_input_tokens: int = 12):  # type: ignore[no-untyped-def]
        super().__init__(function)
        self.predicted_input_tokens = predicted_input_tokens
        self.count_calls = 0

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        self.count_calls += 1
        return RequestUsage(input_tokens=self.predicted_input_tokens)


def grant(**overrides: object) -> DelegatedWorkGrant:
    values: dict[str, object] = {
        "context_refs": [],
        "capability_ids": [],
        "workspace_ref": None,
        "child_network_access": False,
        "maximum_model_runs": 1,
        "maximum_completed_model_responses": 1,
        "maximum_input_tokens": 2_000,
        "maximum_output_tokens": 2_000,
        "maximum_total_tokens": 4_000,
        "maximum_cost_usd_micros": 1_000_000,
        "timeout_seconds": 3,
        "maximum_claims_before_call_start": 2,
    }
    values.update(overrides)
    return DelegatedWorkGrant(**values)  # type: ignore[arg-type]


def spec(**overrides: object) -> DelegatedWorkSpec:
    values: dict[str, object] = {
        "work_id": "work:1",
        "episode_id": "episode:1",
        "generation": 1,
        "parent_task_id": "task:parent:1",
        "owner_principal_id": "person:jaap",
        "principal_binding_id": "binding:telegram:jaap",
        "audience": "private",
        "worker_name": "reviewer",
        "worker_profile_id": "worker:reviewer:v1",
        "worker_profile_release_sha256": WORKER_RELEASE,
        "task": "Noem precies één controleerbare bevinding.",
        "model_profile_id": "model:fixture",
        "model_profile_release_sha256": MODEL_RELEASE,
        "worker_program_sha256": HASH_A,
        "engine_release_sha256": HASH_B,
        "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
        "grant": grant().to_payload(),
        "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deadline": (NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    values.update(overrides)
    return DelegatedWorkSpec(**values)  # type: ignore[arg-type]


def success_model(
    *,
    offered_tools: list[str] | None = None,
    observed: dict[str, object] | None = None,
    output: str = '{"summary":"gecontroleerd","findings":["bewijs-1"]}',
    usage: RequestUsage | None = None,
) -> CountedFunctionModel:
    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if offered_tools is not None:
            offered_tools.extend(tool.name for tool in info.function_tools)
        if observed is not None:
            observed["instructions"] = info.instructions
            observed["settings"] = dict(info.model_settings or {})
            observed["messages"] = messages
        return ModelResponse(
            parts=[TextPart(output)],
            usage=usage or RequestUsage(input_tokens=31, output_tokens=11),
            model_name="fixture",
        )

    return CountedFunctionModel(run)


def profile(
    model: FunctionModel,
    *,
    input_rate: int = 2_000_000,
    output_rate: int = 4_000_000,
) -> ToolFreeChildProfile:
    return ToolFreeChildProfile(
        model_profile_id="model:fixture",
        model_profile_release_sha256=MODEL_RELEASE,
        worker_profile_id="worker:reviewer:v1",
        worker_profile_release_sha256=WORKER_RELEASE,
        instructions=SEALED_INSTRUCTIONS,
        model=model,
        input_cost_usd_micros_per_million_tokens=input_rate,
        output_cost_usd_micros_per_million_tokens=output_rate,
    )


def runner(
    model: FunctionModel,
    *,
    clock=None,  # type: ignore[no-untyped-def]
    input_rate: int = 2_000_000,
    output_rate: int = 4_000_000,
) -> PydanticToolFreeChildRunner:
    child_profile = profile(model, input_rate=input_rate, output_rate=output_rate)
    return PydanticToolFreeChildRunner(
        ClosedModelRegistry({"model:fixture": child_profile}),
        worker_program_sha256=HASH_A,
        engine_release_sha256=HASH_B,
        clock=clock or (lambda: NOW + timedelta(seconds=1)),
    )


def test_closed_contract_rejects_ambient_authority_and_false_usage() -> None:
    for override in (
        {"context_refs": ["memory:private"]},
        {"capability_ids": ["tool:web"]},
        {"workspace_ref": "workspace:1"},
        {"child_network_access": True},
        {"delegation_depth": 2},
        {"maximum_model_runs": 2},
    ):
        with pytest.raises(ValueError):
            grant(**override)
    with pytest.raises(ValueError, match="unmeasured"):
        DelegatedWorkUsage(measurement="unmeasured", input_tokens=0)
    with pytest.raises(ValueError, match="must carry every"):
        DelegatedWorkUsage(measurement="measured", input_tokens=1)


def test_contract_round_trip_hash_and_result_tampering_are_exact() -> None:
    work = spec()
    assert DelegatedWorkSpec.from_payload(work.to_payload()) == work
    assert DelegatedWorkSpec.from_payload(work.to_payload()).sha256 == work.sha256
    usage = DelegatedWorkUsage(
        measurement="measured",
        completed_model_responses=1,
        tool_calls=0,
        input_tokens=31,
        output_tokens=11,
        total_tokens=42,
        cost_usd_micros=106,
    )
    output = '{"findings":["bewijs-1"],"summary":"gecontroleerd"}'
    result = DelegatedWorkResult(
        work_id=work.work_id,
        call_id="call:1",
        output=output,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        spec_sha256=work.sha256,
        usage=usage.to_payload(),
        completed_at="2026-08-26T12:00:01Z",
    )
    raw = result.to_payload()
    raw["output"] = "gewijzigd"
    with pytest.raises(ValueError, match="output hash differs"):
        DelegatedWorkResult.from_payload(raw)


def test_public_agent_run_binds_profile_output_usage_cost_and_zero_tools() -> None:
    offered_tools: list[str] = []
    observed: dict[str, object] = {}
    model = success_model(offered_tools=offered_tools, observed=observed)

    outcome = asyncio.run(runner(model).run(spec(), call_id="call:1"))

    assert outcome.status == "succeeded"
    assert outcome.result is not None
    assert outcome.result.output == (
        '{"findings":["bewijs-1"],"summary":"gecontroleerd"}'
    )
    assert outcome.result.spec_sha256 == spec().sha256
    assert outcome.usage == DelegatedWorkUsage(
        measurement="measured",
        completed_model_responses=1,
        tool_calls=0,
        input_tokens=31,
        output_tokens=11,
        total_tokens=42,
        cost_usd_micros=106,
    )
    assert offered_tools == []
    assert str(observed["instructions"]).startswith(SEALED_INSTRUCTIONS)
    assert observed["settings"] == {"max_tokens": 2000, "parallel_tool_calls": False}
    assert model.count_calls == 1


def test_release_schema_and_closed_profiles_fail_before_model_request() -> None:
    model = success_model()
    use = runner(model)
    for work in (
        spec(output_schema_sha256="0" * 64),
        spec(worker_program_sha256="0" * 64),
        spec(engine_release_sha256="0" * 64),
        spec(model_profile_id="https://attacker.invalid/model"),
        spec(model_profile_release_sha256="0" * 64),
        spec(worker_profile_id="worker:attacker:v1"),
        spec(worker_profile_release_sha256="0" * 64),
    ):
        outcome = asyncio.run(use.run(work, call_id="call:closed"))
        assert outcome.status == "misconfigured"
        assert outcome.usage.measurement == "unmeasured"
    assert model.count_calls == 0


def test_fallback_and_instrumented_models_are_forbidden() -> None:
    model = success_model()
    with pytest.raises(ValueError, match="fallback and instrumented"):
        profile(FallbackModel(model, success_model()))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="fallback and instrumented"):
        profile(InstrumentedModel(model))  # type: ignore[arg-type]


def test_invalid_call_identity_fails_before_model_request() -> None:
    model = success_model()
    outcome = asyncio.run(runner(model).run(spec(), call_id="bad call"))
    assert outcome.status == "misconfigured"
    assert outcome.usage.measurement == "unmeasured"
    assert model.count_calls == 0


def test_precount_budget_stops_real_model_request() -> None:
    request_calls = 0

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal request_calls
        request_calls += 1
        return ModelResponse(parts=[TextPart("never")])

    model = CountedFunctionModel(run, predicted_input_tokens=2_001)
    outcome = asyncio.run(runner(model).run(spec(), call_id="call:budget"))

    assert outcome.status == "budget_exceeded"
    assert model.count_calls == 1
    assert request_calls == 0
    assert outcome.usage.measurement == "unmeasured"


def test_sealed_price_reserve_is_hard_and_zero_price_is_measured() -> None:
    paid_model = success_model()
    paid = spec(grant=grant(maximum_cost_usd_micros=0).to_payload())
    outcome = asyncio.run(runner(paid_model).run(paid, call_id="call:cost"))
    assert outcome.status == "budget_exceeded"
    assert paid_model.count_calls == 0

    free_model = success_model()
    free = spec(grant=grant(maximum_cost_usd_micros=0).to_payload())
    outcome = asyncio.run(
        runner(free_model, input_rate=0, output_rate=0).run(free, call_id="call:free")
    )
    assert outcome.status == "succeeded"
    assert outcome.usage.cost_usd_micros == 0


@pytest.mark.parametrize(
    ("reported_cost", "expected_micros"),
    (
        (Decimal("0.000001"), 106),
        (Decimal("0.000106"), 106),
        (Decimal("100"), 100_000_000),
    ),
)
def test_known_provider_cost_uses_the_higher_conservative_value(
    reported_cost: Decimal, expected_micros: int
) -> None:
    usage = RequestUsage(input_tokens=31, output_tokens=11, cost=reported_cost)
    work = spec(grant=grant(maximum_cost_usd_micros=200_000_000).to_payload())
    outcome = asyncio.run(
        runner(success_model(usage=usage)).run(work, call_id="call:known-cost")
    )
    assert outcome.status == "succeeded"
    assert outcome.usage.cost_usd_micros == expected_micros


def test_known_provider_cost_above_grant_is_not_hidden_by_lower_rate_card() -> None:
    usage = RequestUsage(input_tokens=31, output_tokens=11, cost=Decimal("100"))
    outcome = asyncio.run(
        runner(success_model(usage=usage)).run(spec(), call_id="call:cost-overrun")
    )
    assert outcome.status == "budget_exceeded"
    assert outcome.result is None
    assert outcome.usage.measurement == "partial"
    assert outcome.usage.cost_usd_micros == 100_000_000


@pytest.mark.parametrize("bad_cost", (Decimal("NaN"), Decimal("-1")))
def test_nonfinite_or_negative_provider_cost_is_invalid_usage(
    bad_cost: Decimal,
) -> None:
    usage = RequestUsage(input_tokens=31, output_tokens=11, cost=bad_cost)
    outcome = asyncio.run(
        runner(success_model(usage=usage)).run(spec(), call_id="call:bad-cost")
    )
    assert outcome.status == "invalid_usage"
    assert outcome.usage.measurement == "unmeasured"


def test_invalid_structured_or_durable_output_is_not_success() -> None:
    for output in (
        '{"summary":"ok","extra":"forbidden"}',
        '{"summary":"bad\ufffe","findings":[]}',
    ):
        outcome = asyncio.run(
            runner(success_model(output=output)).run(spec(), call_id="call:invalid")
        )
        assert outcome.status == "invalid_output"
        assert outcome.result is None


def test_provider_secret_body_is_never_reflected_and_usage_is_unknown() -> None:
    marker = "secret-provider-body-canary"

    def rejected(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(401, "fixture", {"private": marker})

    outcome = asyncio.run(
        runner(CountedFunctionModel(rejected)).run(spec(), call_id="call:rejected")
    )

    assert outcome.status == "provider_rejected"
    assert outcome.provider_status_code == 401
    assert outcome.usage.measurement == "unmeasured"
    assert marker not in repr(outcome)
    assert marker not in (outcome.error_fingerprint or "")


def test_malformed_provider_usage_becomes_closed_invalid_usage() -> None:
    bad_usage = RequestUsage(input_tokens=-1, output_tokens=3)
    outcome = asyncio.run(
        runner(success_model(usage=bad_usage)).run(spec(), call_id="call:bad-usage")
    )
    assert outcome.status == "invalid_usage"
    assert outcome.result is None
    assert outcome.usage.measurement == "unmeasured"


def test_absolute_deadline_caps_the_relative_timeout_and_usage_is_unknown() -> None:
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(1.2)
        return ModelResponse(
            parts=[TextPart('{"summary":"late","findings":[]}')],
            usage=RequestUsage(input_tokens=5, output_tokens=2),
        )

    work = spec(deadline=(NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    outcome = asyncio.run(
        runner(CountedFunctionModel(slow), clock=lambda: NOW).run(
            work, call_id="call:deadline"
        )
    )
    assert outcome.status == "timed_out"
    assert outcome.usage.measurement == "unmeasured"


def test_clock_jump_after_response_cannot_commit_late_success() -> None:
    values = iter((NOW, NOW, NOW + timedelta(minutes=6)))
    outcome = asyncio.run(
        runner(success_model(), clock=lambda: next(values)).run(
            spec(), call_id="call:clock-jump"
        )
    )
    assert outcome.status == "timed_out"
    assert outcome.usage.measurement == "partial"


def test_completion_time_is_captured_once_for_deadline_and_result() -> None:
    values = iter(
        (
            NOW,
            NOW,
            NOW + timedelta(minutes=4),
            NOW + timedelta(minutes=6),
        )
    )
    calls: list[datetime] = []

    def clock() -> datetime:
        value = next(values)
        calls.append(value)
        return value

    outcome = asyncio.run(
        runner(success_model(), clock=clock).run(spec(), call_id="call:one-clock")
    )
    assert outcome.status == "succeeded"
    assert outcome.result is not None
    assert outcome.result.completed_at == "2026-08-26T12:04:00Z"
    assert calls == [NOW, NOW, NOW + timedelta(minutes=4)]


def test_private_run_forces_instrumentation_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Agent.run

    async def checked(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        assert self.instrument is False
        return await original(self, *args, **kwargs)

    Agent.instrument_all(True)
    monkeypatch.setattr(Agent, "run", checked)
    try:
        outcome = asyncio.run(
            runner(success_model()).run(spec(), call_id="call:private")
        )
    finally:
        Agent.instrument_all(False)
    assert outcome.status == "succeeded"


def test_first_party_cancellation_is_structured_and_idempotent() -> None:
    started = asyncio.Event()

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        started.set()
        await asyncio.sleep(10)
        return ModelResponse(parts=[TextPart("never")])

    async def scenario():  # type: ignore[no-untyped-def]
        event = asyncio.Event()
        task = asyncio.create_task(
            runner(CountedFunctionModel(slow)).run(
                spec(), call_id="call:cancel", cancellation_event=event
            )
        )
        await started.wait()
        event.set()
        event.set()
        return await task

    outcome = asyncio.run(scenario())
    assert outcome.status == "cancelled"
    assert outcome.result is None
    assert outcome.usage.measurement == "unmeasured"


def test_external_task_cancellation_is_never_swallowed() -> None:
    started = asyncio.Event()

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        started.set()
        await asyncio.sleep(10)
        return ModelResponse(parts=[TextPart("never")])

    async def scenario() -> None:
        task = asyncio.create_task(
            runner(CountedFunctionModel(slow)).run(
                spec(), call_id="call:external-cancel"
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
