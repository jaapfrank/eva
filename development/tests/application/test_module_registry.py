from __future__ import annotations

import pytest

from eva.adapters.postgres.core_indexes import CORE_COMPOSITE_INDEXES
from eva.application.conversation import ConversationToolDescriptor
from eva.application.module_registry import (
    AuthorityChannelModuleAdapter,
    ModuleRegistry,
    ProviderModuleAdapter,
)
from eva.contracts.module import (
    EvaModule,
    ModuleHealth,
    ModulePolicyStatus,
    ModuleSpecification,
)
from eva.providers.contracts import ProviderContribution


class _DummyRoute:
    action_names = ("custom_action",)


def _descriptor(name: str = "custom_action") -> ConversationToolDescriptor:
    return ConversationToolDescriptor(
        name=name,
        description="Test modular capability",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        completion_guidance="Done.",
    )


def test_module_registry_admits_active_modules_and_isolates_expired_optional_channels() -> None:
    registry = ModuleRegistry()
    active_adapter = ProviderModuleAdapter(
        "active_provider",
        lambda: ProviderContribution(
            provider_id="active_provider",
            conversation_tools=(_descriptor("custom_action"),),
            external_tools=(_DummyRoute(),),
        ),
        execution_sources=("providers/runtime.py",),
    )
    expired_adapter = ProviderModuleAdapter(
        "expired_optional_channel",
        lambda: (_ for _ in ()).throw(ValueError("Household voice authority is no longer current")),
        optional_channel=True,
    )
    expired_voice = AuthorityChannelModuleAdapter(
        "household_voice",
        None,
        capabilities=("voice_turn",),
    )

    assert isinstance(active_adapter, EvaModule)
    assert isinstance(expired_adapter, EvaModule)
    assert isinstance(expired_voice, EvaModule)

    registry.register(active_adapter)
    registry.register(expired_adapter)
    registry.register(expired_voice)

    snap = registry.snapshot(allow_inactive=True)
    by_id = {m.module_id: m for m in snap.modules}
    assert by_id["active_provider"] == ModuleHealth(
        module_id="active_provider",
        healthy=True,
        state="active",
        detail=None,
    )
    assert by_id["expired_optional_channel"].healthy is False
    assert by_id["expired_optional_channel"].state == "inactive_policy"
    assert by_id["household_voice"].healthy is False
    assert by_id["household_voice"].state == "inactive_policy"

    assert [t.name for t in snap.conversation_tools] == ["custom_action"]
    bundle = registry.build_provider_bundle(allow_inactive=True)
    assert bundle is not None
    assert [t.name for t in bundle.conversation_tools] == ["custom_action"]


def test_module_registry_rejects_duplicate_module_ids() -> None:
    registry = ModuleRegistry()
    mod = AuthorityChannelModuleAdapter("household_voice", None)
    registry.register(mod)
    with pytest.raises(ValueError, match="Duplicate EvaModule"):
        registry.register(mod)


def test_core_composite_indexes_cover_worker_claim_and_event_storm_queries() -> None:
    names = {name for name, _ in CORE_COMPOSITE_INDEXES}
    assert names == {
        "ix_delegated_work_runs__worker_claim",
        "ix_operational_events__type_seq_time",
        "ix_operational_events__thread_type_seq",
        "ix_operational_events__occurred_at",
    }
