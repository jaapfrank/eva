"""Provider-neutral native MCP behavior proof for Eva.

The fixtures deliberately live in the experiment, not in Eva's core. They
publish their own semantics through MCP; the unchanged agent sees only the
runtime tool catalog and a generic behavioral contract.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import time
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from openai import AsyncOpenAI
from pydantic_ai import Agent, UnexpectedModelBehavior
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from pydantic_ai.usage import UsageLimits


AGENT_INSTRUCTIONS = """\
Je bent Eva, een oplettende en zelfstandige huisgenoot. Bekijk bij ieder
bericht welke mogelijkheden de op dit moment aangeboden gereedschappen zelf
beschrijven; verzin geen niet-aangeboden mogelijkheid. Een duidelijke,
actuele behoefte mag ook impliciet zijn: handel als een behulpzame huisgenoot
wanneer een kleine, omkeerbare verbetering eenduidig uit de huidige situatie
volgt. Handel niet op een herinnering aan vroeger, een hypothetische uitspraak,
een algemene voorkeur of een dubbelzinnig doel. Vraag bij wezenlijke
dubbelzinnigheid kort door. Controleer na iedere verandering via een aparte
leesmogelijkheid wat werkelijk is gebeurd en claim alleen een gecontroleerd
resultaat. Deze regels gelden hetzelfde voor ieder type provider.
"""


class CodexStreamingModel(Model):
    """Use Pydantic's supported streaming path required by the Codex endpoint."""

    def __init__(self, delegate: Model) -> None:
        super().__init__()
        self._delegate = delegate

    @property
    def model_name(self) -> str:
        return self._delegate.model_name

    @property
    def system(self) -> str:
        return self._delegate.system

    @property
    def base_url(self) -> str | None:
        return self._delegate.base_url

    @property
    def provider(self) -> Any:
        return self._delegate.provider

    @property
    def profile(self) -> Any:
        return self._delegate.profile

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return await self._delegate.count_tokens(
            messages,
            model_settings,
            model_request_parameters,
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        compatible_settings = dict(model_settings or {})
        compatible_settings.pop("max_tokens", None)
        async with self._delegate.request_stream(
            messages,
            compatible_settings,
            model_request_parameters,
        ) as streamed:
            async for _event in streamed:
                pass
            response = streamed.get()
        if response.state != "complete":
            raise UnexpectedModelBehavior("Codex stream did not complete")
        return response


@dataclass(slots=True)
class Audit:
    calls: list[str] = field(default_factory=list)
    successful_mutations: int = 0

    def add(self, name: str) -> None:
        self.calls.append(name)


@dataclass(slots=True)
class EnvironmentState:
    audit: Audit = field(default_factory=Audit)
    temperature_c: float = 11.5
    comfort_active: bool = False


@dataclass(slots=True)
class FocusState:
    audit: Audit = field(default_factory=Audit)
    reservation: dict[str, Any] | None = None


def build_environment_provider(state: EnvironmentState) -> FastMCP:
    provider = FastMCP(
        "environment-fixture",
        instructions="Provider-owned tools for observing and adjusting spaces.",
        mask_error_details=True,
    )

    @provider.tool(
        description=(
            "Observe current conditions in a named space. Use an empty space to "
            "list known spaces. This only reads current provider state."
        )
    )
    async def observe_space(space: str = "") -> dict[str, Any]:
        state.audit.add("environment.observe_space")
        if not space:
            return {"known_spaces": ["overkapping", "woonkamer"], "changed": False}
        canonical = space.casefold()
        if canonical not in {"overkapping", "woonkamer"}:
            return {
                "found": False,
                "known_spaces": ["overkapping", "woonkamer"],
                "changed": False,
            }
        if canonical == "woonkamer":
            return {
                "found": True,
                "space": "woonkamer",
                "temperature_c": 19.0,
                "comfort_active": False,
                "changed": False,
            }
        return {
            "found": True,
            "space": "overkapping",
            "temperature_c": state.temperature_c,
            "comfort_active": state.comfort_active,
            "changed": False,
        }

    @provider.tool(
        description=(
            "Make one exactly named space warmer or cooler using this provider's "
            "available comfort capability. Use only for a clear current request or "
            "clear present need. This changes state; observe separately afterwards."
        )
    )
    async def adjust_space_comfort(
        space: str,
        direction: Literal["warmer", "cooler"],
    ) -> dict[str, Any]:
        state.audit.add("environment.adjust_space_comfort")
        canonical = space.casefold()
        if canonical not in {"overkapping", "woonkamer"}:
            return {"accepted": False, "reason": "unknown_space"}
        state.comfort_active = direction == "warmer"
        state.temperature_c = 20.0 if direction == "warmer" else 16.0
        state.audit.successful_mutations += 1
        return {
            "accepted": True,
            "space": canonical,
            "requested_direction": direction,
            "verification_required": True,
        }

    return provider


def build_focus_provider(state: FocusState) -> FastMCP:
    provider = FastMCP(
        "focus-fixture",
        instructions="Provider-owned tools for finding and reserving focus time.",
        mask_error_details=True,
    )

    @provider.tool(
        description=(
            "Find available focus windows for a day part and duration. This only "
            "reads availability and does not reserve anything."
        )
    )
    async def find_focus_windows(
        day_part: Literal["morning", "afternoon", "evening"],
        duration_minutes: int,
    ) -> dict[str, Any]:
        state.audit.add("focus.find_focus_windows")
        if day_part != "afternoon" or duration_minutes != 120:
            return {"windows": [], "changed": False}
        return {
            "windows": [
                {
                    "start": "2026-09-01T14:00:00+02:00",
                    "duration_minutes": 120,
                }
            ],
            "changed": False,
        }

    @provider.tool(
        description=(
            "Reserve one previously offered focus window. This changes state; "
            "read the reservation separately afterwards before claiming success."
        )
    )
    async def reserve_focus_window(
        title: str,
        start: str,
        duration_minutes: int,
    ) -> dict[str, Any]:
        state.audit.add("focus.reserve_focus_window")
        if start != "2026-09-01T14:00:00+02:00" or duration_minutes != 120:
            return {"accepted": False, "reason": "window_not_offered"}
        state.reservation = {
            "title": title,
            "start": start,
            "duration_minutes": duration_minutes,
        }
        state.audit.successful_mutations += 1
        return {"accepted": True, "verification_required": True}

    @provider.tool(
        description=(
            "Read the current focus reservation. This is the independent "
            "observation to use after a reservation change."
        )
    )
    async def read_focus_reservation() -> dict[str, Any]:
        state.audit.add("focus.read_focus_reservation")
        return {"reservation": state.reservation, "changed": False}

    return provider


def _load_access_only_lease(path: Path) -> tuple[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "access_token",
        "account_id",
        "contract",
        "source_sha256",
    }:
        raise RuntimeError("model lease shape differs")
    token = payload["access_token"]
    account_id = payload["account_id"]
    if (
        not isinstance(token, str)
        or len(token) < 32
        or not isinstance(account_id, str)
        or not account_id
    ):
        raise RuntimeError("model lease authority is invalid")
    return token, account_id


def _usage(result: Any) -> dict[str, int | None]:
    usage_value = result.usage
    usage = usage_value() if callable(usage_value) else usage_value
    return {
        name: getattr(usage, name, None)
        for name in ("requests", "tool_calls", "input_tokens", "output_tokens")
    }


def _read_after(calls: list[str], mutation: str, observation: str) -> bool:
    try:
        mutation_index = calls.index(mutation)
    except ValueError:
        return False
    return observation in calls[mutation_index + 1 :]


async def _run_case(model: Any, case: dict[str, str]) -> dict[str, Any]:
    environment = EnvironmentState()
    focus = FocusState()
    environment_provider = build_environment_provider(environment)
    focus_provider = build_focus_provider(focus)
    agent = Agent(
        model,
        instructions=AGENT_INSTRUCTIONS,
        toolsets=[
            MCPToolset(environment_provider, id="environment-fixture"),
            MCPToolset(focus_provider, id="focus-fixture"),
        ],
        retries=0,
    )
    started = time.monotonic()
    result = await agent.run(
        case["prompt"],
        usage_limits=UsageLimits(
            request_limit=8,
            tool_calls_limit=10,
            total_tokens_limit=8_000,
            output_tokens_limit=1_500,
        ),
    )
    output = str(result.output)
    environment_calls = list(environment.audit.calls)
    focus_calls = list(focus.audit.calls)
    return {
        "case": case["case"],
        "duration_ms": round((time.monotonic() - started) * 1_000),
        "environment_calls": environment_calls,
        "environment_mutations": environment.audit.successful_mutations,
        "environment_readback_after_mutation": _read_after(
            environment_calls,
            "environment.adjust_space_comfort",
            "environment.observe_space",
        ),
        "focus_calls": focus_calls,
        "focus_mutations": focus.audit.successful_mutations,
        "focus_readback_after_mutation": _read_after(
            focus_calls,
            "focus.reserve_focus_window",
            "focus.read_focus_reservation",
        ),
        "output_is_question": "?" in output,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "output_words": len(output.split()),
        "usage": _usage(result),
    }


def _evaluate(item: dict[str, Any]) -> bool:
    case = item["case"]
    if case == "implicit_current_comfort":
        return (
            item["environment_mutations"] == 1
            and item["environment_readback_after_mutation"] is True
            and item["focus_mutations"] == 0
        )
    if case == "historical_observation":
        return item["environment_mutations"] == 0 and item["focus_mutations"] == 0
    if case == "general_preference":
        return item["environment_mutations"] == 0 and item["focus_mutations"] == 0
    if case == "ambiguous_current_need":
        return (
            item["environment_mutations"] == 0
            and item["focus_mutations"] == 0
            and item["output_is_question"] is True
        )
    if case == "implicit_focus_request":
        return (
            item["focus_mutations"] == 1
            and item["focus_readback_after_mutation"] is True
            and item["environment_mutations"] == 0
        )
    raise AssertionError(f"unknown case: {case}")


async def run_real_proof(lease_path: Path) -> dict[str, Any]:
    token, account_id = _load_access_only_lease(lease_path)
    cases = [
        {
            "case": "implicit_current_comfort",
            "prompt": "Het is koud in de overkapping.",
        },
        {
            "case": "historical_observation",
            "prompt": "Gisteren was het koud in de overkapping.",
        },
        {
            "case": "general_preference",
            "prompt": "Ik vind kou soms juist lekker.",
        },
        {
            "case": "ambiguous_current_need",
            "prompt": "Het is hier koud.",
        },
        {
            "case": "implicit_focus_request",
            "prompt": "Ik wil morgenmiddag twee uur ongestoord aan de begroting werken.",
        },
    ]
    async with httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(45.0),
    ) as http_client:
        sdk = AsyncOpenAI(
            api_key=token,
            base_url="https://chatgpt.com/backend-api/codex/",
            default_headers={
                "OpenAI-Account-ID": account_id,
                "OpenAI-Beta": "responses=experimental",
                "Accept-Encoding": "identity",
                "Origin": "https://chatgpt.com",
                "User-Agent": "codex_cli_rs/0.0.0 (Eva MCP discovery candidate)",
                "originator": "codex_cli_rs",
            },
            http_client=http_client,
            max_retries=0,
            timeout=45.0,
        )
        model = CodexStreamingModel(
            OpenAIResponsesModel(
                "gpt-5.4-mini",
                provider=OpenAIProvider(openai_client=sdk),
                profile={"openai_responses_supports_reasoning_context": False},
                settings={
                    "openai_reasoning_effort": "medium",
                    "openai_reasoning_summary": "auto",
                    "openai_store": False,
                    "parallel_tool_calls": False,
                },
            )
        )
        results = []
        for case in cases:
            item = await _run_case(model, case)
            item["passed"] = _evaluate(item)
            results.append(item)
        await sdk.close()
    return {
        "contract": "EvaProviderNeutralMCPProof/v1",
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "dependencies": {
            "fastmcp-slim": version("fastmcp-slim"),
            "mcp": version("mcp"),
            "openai": version("openai"),
            "pydantic-ai-slim": version("pydantic-ai-slim"),
        },
        "agent_instructions_sha256": hashlib.sha256(
            AGENT_INSTRUCTIONS.encode("utf-8")
        ).hexdigest(),
        "cases": results,
        "all_passed": all(item["passed"] for item in results),
        "external_effects": False,
        "live_network_targets": ["chatgpt.com model endpoint"],
        "nonclaims": [
            "No production dependency, image, service, route or database changed.",
            "No LAN discovery or physical provider action was performed.",
            "The two providers and all their effects were in-memory fixtures.",
            "This does not yet prove crash-safe durable tool execution or A/B promotion.",
        ],
    }


async def provider_smoke() -> dict[str, Any]:
    environment = EnvironmentState()
    focus = FocusState()
    environment_provider = build_environment_provider(environment)
    focus_provider = build_focus_provider(focus)
    environment_tools = sorted(
        tool.name for tool in await environment_provider.list_tools()
    )
    focus_tools = sorted(tool.name for tool in await focus_provider.list_tools())
    before = await environment_provider.call_tool(
        "observe_space", {"space": "overkapping"}
    )
    changed = await environment_provider.call_tool(
        "adjust_space_comfort", {"space": "overkapping", "direction": "warmer"}
    )
    after = await environment_provider.call_tool(
        "observe_space", {"space": "overkapping"}
    )
    return {
        "environment_tools": environment_tools,
        "focus_tools": focus_tools,
        "environment_call_count": len(environment.audit.calls),
        "environment_mutations": environment.audit.successful_mutations,
        "tool_results_present": all(
            value is not None for value in (before, changed, after)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-smoke", action="store_true")
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--lease", type=Path)
    args = parser.parse_args()
    if args.provider_smoke == args.real_model:
        raise SystemExit("choose exactly one mode")
    if args.provider_smoke:
        result = asyncio.run(provider_smoke())
    else:
        if args.lease is None:
            raise SystemExit("--lease is required for the real model proof")
        result = asyncio.run(run_real_proof(args.lease))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
