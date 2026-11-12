"""Unit tests for UnifiedTaskEngine and ModuleRegistry provider composition."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from eva.application.conversation import ConversationToolDescriptor
from eva.bootstrap.delegated_digital_composition import DigitalEnabledDelegatedRunner
from eva.bootstrap.unified_task_engine import UnifiedTaskEngine, classify_task_spec
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
from eva.contracts.delegated_work import DelegatedResearchSpec, DelegatedWorkSpec
from eva.providers.contracts import (
    ExternalProviderSettings,
    LoadedProvider,
    ProviderContribution,
)
from eva.providers.runtime import build_external_module_registry


class _DummyRunner:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.calls: list[tuple[object, str]] = []

    async def run(self, spec: object, *, call_id: str, cancellation_event: object = None):
        self.calls.append((spec, call_id))
        return f"{self.tag}:{call_id}"


def test_unified_task_engine_routes_digital_and_existing_specs() -> None:
    base = _DummyRunner("base")
    digital = _DummyRunner("digital")
    engine = UnifiedTaskEngine(existing_runner=base, digital_runner=digital)

    child_spec = DelegatedWorkSpec.__new__(DelegatedWorkSpec)
    object.__setattr__(child_spec, "contract", "DelegatedWork/v1")
    object.__setattr__(child_spec, "audience", "private")

    digital_spec = DelegatedDigitalWorkSpec.__new__(DelegatedDigitalWorkSpec)
    object.__setattr__(digital_spec, "contract", "DelegatedDigitalWork/v4")

    research_spec = DelegatedResearchSpec.__new__(DelegatedResearchSpec)
    object.__setattr__(research_spec, "contract", "DelegatedResearchWork/v2")

    assert classify_task_spec(child_spec).lane == "child"
    assert classify_task_spec(digital_spec).lane == "digital"
    assert classify_task_spec(research_spec).lane == "research"

    res1 = asyncio.run(engine.run(child_spec, call_id="c1"))
    res2 = asyncio.run(engine.run(digital_spec, call_id="c2"))
    assert res1 == "base:c1"
    assert res2 == "digital:c2"

    wrapper = DigitalEnabledDelegatedRunner(base, digital)
    assert asyncio.run(wrapper.run(digital_spec, call_id="c3")) == "digital:c3"


def test_build_external_module_registry_composes_providers() -> None:
    tool = ConversationToolDescriptor(
        name="custom_action",
        description="Custom action",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": [],
        },
    )
    port = SimpleNamespace(action_names=("custom_action",))
    factory = SimpleNamespace(
        provider_id="custom_provider",
        supports_digital_work=True,
        supports_household_digital_work=False,
        build=lambda s, e: ProviderContribution(
            provider_id="custom_provider",
            conversation_tools=(tool,),
            external_tools=(port,),
            event_source=None,
            digital_action_names=("custom_action",),
        ),
    )
    settings = ExternalProviderSettings(
        providers=(LoadedProvider("custom_provider", factory, {"enabled": True}),)
    )
    registry = build_external_module_registry(settings, engine=SimpleNamespace())
    assert registry is not None
    bundle = registry.build_provider_bundle()
    assert [t.name for t in bundle.conversation_tools] == ["custom_action"]
    assert bundle.digital_action_names == ("custom_action",)
