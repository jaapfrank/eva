from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa

import eva.providers.home_assistant as home_assistant_provider
import eva.providers.runtime as provider_runtime
from eva.providers.agentmail import AgentMailProviderSettings, AgentMailProviderFactory
OWNER = {"tenant_id": "00000000-0000-0000-0000-000000000001",
         "installation_id": "00000000-0000-0000-0000-000000000002",
         "principal_id": "00000000-0000-0000-0000-000000000003"}
from eva.providers.home_assistant import HomeAssistantProviderSettings
from eva.providers.runtime import (
    ExternalProviderSettings,
    LoadedProvider,
    ProviderContribution,
    build_external_provider_bundle,
    load_external_provider_settings,
)
from eva.application.conversation import ConversationToolDescriptor


def test_generic_provider_compositor_contains_no_provider_specific_identifiers() -> None:
    source = Path(provider_runtime.__file__).read_text()
    forbidden = ("agentmail", "home_assistant", "homeassistant", "tuya")
    assert not {item for item in forbidden if item in source.casefold()}


def test_provider_settings_stay_outside_core_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(provider_runtime.os.environ):
        if name.startswith(
            ("EVA_HOME_ASSISTANT_", "EVA_AGENTMAIL_", "EVA_MCP_GATEWAY_")
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("EVA_EXTERNAL_PROVIDER_PLUGINS", raising=False)
    assert load_external_provider_settings() is None

    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://provider:8123")
    monkeypatch.setattr(
        home_assistant_provider, "environment_secret", lambda *_: "token"
    )
    settings = load_external_provider_settings()
    assert settings is not None
    assert len(settings.providers) == 1
    home = settings.providers[0].settings
    assert isinstance(home, HomeAssistantProviderSettings)
    assert home.endpoint_url == "http://provider:8123"
    assert home.effects_enabled is False

    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "true")
    with pytest.raises(RuntimeError, match="ALLOWLIST is required"):
        load_external_provider_settings()


def test_email_provider_settings_are_file_only_and_live_outside_core(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    for name in tuple(provider_runtime.os.environ):
        if name.startswith(
            ("EVA_HOME_ASSISTANT_", "EVA_AGENTMAIL_", "EVA_MCP_GATEWAY_")
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("EVA_EXTERNAL_PROVIDER_PLUGINS", raising=False)
    key = private_tmp_path / "agentmail-key"
    key.write_text("private-test-key\n")
    key.chmod(0o600)
    monkeypatch.setenv("EVA_AGENTMAIL_INBOX_ID", "eva@example.invalid")
    monkeypatch.setenv("EVA_AGENTMAIL_API_KEY_FILE", str(key))

    for key, value in OWNER.items():
        monkeypatch.setenv("EVA_RUNTIME_" + key.upper(), value)
    settings = load_external_provider_settings()

    assert settings is not None
    assert len(settings.providers) == 1
    assert settings.providers[0].provider_id == "agentmail"
    assert settings.providers[0].settings == AgentMailProviderSettings(
        inbox_id="eva@example.invalid",
        credential="private-test-key", **OWNER,
    )
    assert "private-test-key" not in repr(settings)
    monkeypatch.setenv("EVA_AGENTMAIL_API_KEY", "forbidden-inline-key")
    with pytest.raises(RuntimeError, match="API_KEY is forbidden"):
        load_external_provider_settings()


def test_provider_bundle_registers_tools_without_core_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = hashlib.sha256(b"person:owner").hexdigest()
    monkeypatch.delenv("EVA_EXTERNAL_PROVIDER_PLUGINS", raising=False)
    for name in tuple(provider_runtime.os.environ):
        if name.startswith("EVA_MCP_GATEWAY_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        home_assistant_provider.HomeAssistantProviderFactory,
        "load_settings",
        staticmethod(lambda: HomeAssistantProviderSettings(
            endpoint_url="http://provider:8123",
            credential="token",
            effects_enabled=True,
            effect_allowlist=("light.desk:turn_off",),
            effect_principal_sha256_allowlist=(principal,),
            event_domains=("light",),
        )),
    )
    from eva.providers.agentmail import AgentMailProviderFactory

    monkeypatch.setattr(
        AgentMailProviderFactory,
        "load_settings",
        staticmethod(lambda: AgentMailProviderSettings(
            inbox_id="eva@example.invalid",
            credential="secret", **OWNER,
        )),
    )
    settings = load_external_provider_settings()
    bundle = build_external_provider_bundle(settings, sa.create_engine("sqlite://"))

    assert bundle is not None
    assert "read_home_assistant_state" in {
        name for tools in bundle.external_tools for name in tools.action_names
    }
    assert "set_home_assistant_light" in {
        name for tools in bundle.external_tools for name in tools.action_names
    }
    assert "list_email_messages" in {
        descriptor.name for descriptor in bundle.conversation_tools
    }
    assert len(bundle.external_tools) == 7
    assert bundle.event_source is not None


def test_explicit_python_entry_point_adds_a_provider_without_compositor_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Port:
        action_names = ("read_calendar",)

    descriptor = ConversationToolDescriptor(
        name="read_calendar",
        description="Read one calendar.",
        input_schema={
            "additionalProperties": False,
            "properties": {},
            "required": [],
            "type": "object",
        },
        completion_guidance="Use only the observed calendar data.",
    )

    class Factory:
        provider_id = "calendar_test"

        @staticmethod
        def load_settings() -> object:
            return object()

        @staticmethod
        def build(settings: object, engine: sa.Engine) -> ProviderContribution:
            assert settings is not None
            assert engine is not None
            return ProviderContribution(
                "calendar_test",
                (descriptor,),
                (Port(),),  # type: ignore[arg-type]
            )

    class EntryPoint:
        name = "calendar_test"

        @staticmethod
        def load() -> type[Factory]:
            return Factory

    class EntryPoints(tuple[EntryPoint, ...]):
        def select(self, **values: str) -> EntryPoints:
            assert values == {"group": "eva2.external_providers"}
            return self

    monkeypatch.setenv("EVA_EXTERNAL_PROVIDER_PLUGINS", "calendar_test")
    monkeypatch.setattr(
        provider_runtime.metadata,
        "entry_points",
        lambda: EntryPoints((EntryPoint(),)),
    )

    settings = load_external_provider_settings()
    bundle = build_external_provider_bundle(settings, sa.create_engine("sqlite://"))

    assert settings is not None
    assert [provider.provider_id for provider in settings.providers] == [
        "calendar_test"
    ]
    assert bundle is not None
    assert [tool.name for tool in bundle.conversation_tools] == ["read_calendar"]


def test_provider_registry_rejects_missing_plugins_and_duplicate_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyEntryPoints(tuple[()]):
        def select(self, **values: str) -> EmptyEntryPoints:
            assert values == {"group": "eva2.external_providers"}
            return self

    monkeypatch.setenv("EVA_EXTERNAL_PROVIDER_PLUGINS", "missing_provider")
    monkeypatch.setattr(
        provider_runtime.metadata,
        "entry_points",
        lambda: EmptyEntryPoints(),
    )
    with pytest.raises(RuntimeError, match="not installed"):
        load_external_provider_settings()

    monkeypatch.setenv("EVA_EXTERNAL_PROVIDER_PLUGINS", "mcp_gateway")
    with pytest.raises(RuntimeError, match="built-in provider"):
        load_external_provider_settings()

    class Port:
        action_names = ("same_tool",)

    descriptor = ConversationToolDescriptor(
        name="same_tool",
        description="Duplicate fixture.",
        input_schema={
            "additionalProperties": False,
            "properties": {},
            "required": [],
            "type": "object",
        },
        completion_guidance="Fixture only.",
    )

    class Factory:
        def __init__(self, provider_id: str) -> None:
            self.provider_id = provider_id

        def load_settings(self) -> object:
            return object()

        def build(self, settings: object, engine: sa.Engine) -> ProviderContribution:
            return ProviderContribution(
                self.provider_id,
                (descriptor,),
                (Port(),),  # type: ignore[arg-type]
            )

    first = Factory("first")
    second = Factory("second")
    configured = ExternalProviderSettings(
        (
            LoadedProvider("first", first, object()),
            LoadedProvider("second", second, object()),
        )
    )
    with pytest.raises(RuntimeError, match="descriptor is duplicated"):
        build_external_provider_bundle(configured, sa.create_engine("sqlite://"))

    duplicated_identity = ExternalProviderSettings(
        (
            LoadedProvider("first", first, object()),
            LoadedProvider("first", first, object()),
        )
    )
    with pytest.raises(RuntimeError, match="ID is duplicated"):
        build_external_provider_bundle(
            duplicated_identity,
            sa.create_engine("sqlite://"),
        )

    class MismatchedPort:
        action_names = ("other_tool",)

    class MismatchedFactory(Factory):
        def build(
            self, settings: object, engine: sa.Engine
        ) -> ProviderContribution:
            return ProviderContribution(
                self.provider_id,
                (descriptor,),
                (MismatchedPort(),),  # type: ignore[arg-type]
            )

    mismatched = MismatchedFactory("mismatched")
    with pytest.raises(RuntimeError, match="catalog and action routes differ"):
        build_external_provider_bundle(
            ExternalProviderSettings(
                (LoadedProvider("mismatched", mismatched, object()),)
            ),
            sa.create_engine("sqlite://"),
        )


def test_household_inventory_is_private_owner_read_even_with_effects_disabled():
    from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationConflict
    from eva.application.memory_audience import MemoryAudience, TelegramGroupConsentReference

    engine = sa.create_engine("sqlite://")
    contribution = home_assistant_provider.HomeAssistantProviderFactory.build(
        HomeAssistantProviderSettings("http://ha.invalid", "fixture", False, (), (hashlib.sha256(b"owner").hexdigest(),), ("light",)), engine,
    )
    household = next(tool for tool in contribution.external_tools if "read_home_assistant_inventory" in tool.action_names)
    assert not any("set_home_assistant_light" in tool.action_names for tool in contribution.external_tools)
    owner = ConversationTurn.private("tenant", "install", "owner", "chat", "one", "Lees mijn huis", "2026-09-08T12:00:00Z")
    other = ConversationTurn.private("tenant", "install", "other", "chat2", "two", "Lees het huis", "2026-09-08T12:00:00Z")
    group = ConversationTurn("tenant", "install", "owner", "telegram:-100", "telegram:-100:three", "Lees het huis", "2026-09-08T12:00:00Z", MemoryAudience.telegram_group(principal_id="owner", group_chat_id="-100", consent_ref=TelegramGroupConsentReference("owner", "-100", "telegram-group-consent:proof")))
    assert household.available_for(owner) is True
    for denied in (other, group):
        assert household.available_for(denied) is False
        with pytest.raises(ConversationConflict, match="audience"):
            household.execute(denied, ConversationActionCall("call", "read_home_assistant_inventory", {}))
    engine.dispose()


@pytest.mark.parametrize("changed", ["tenant_id", "installation_id", "principal_id", "audience_owner", "household"])
def test_email_private_owner_gate_precedes_database_and_provider(changed):
    from types import SimpleNamespace
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    settings = AgentMailProviderSettings(inbox_id="private@example.invalid", credential="secret", **OWNER)
    tools = AgentMailProviderFactory.build(settings, None).external_tools[0]
    turn = SimpleNamespace(**OWNER, conversation_id="ordinary-chat", audience=SimpleNamespace(kind="private_person", principal_id=OWNER['principal_id']))
    assert tools.available_for(turn)
    if changed in OWNER: setattr(turn, changed, "00000000-0000-0000-0000-000000000009")
    elif changed == 'audience_owner': turn.audience.principal_id = 'someone-else'
    else: turn.audience.kind = 'household'
    assert not tools.available_for(turn)
    with pytest.raises(ConversationConflict, match="unavailable"):
        tools.execute(turn, ConversationActionCall("private-check", "list_email_messages", {"limit": 1}))


def test_email_enabled_without_runtime_owner_fails_before_secret_read(monkeypatch):
    import eva.providers.agentmail as module
    monkeypatch.setenv('EVA_AGENTMAIL_INBOX_ID', 'private@example.invalid')
    monkeypatch.setenv('EVA_AGENTMAIL_API_KEY_FILE', '/must/not/read')
    monkeypatch.delenv('EVA_AGENTMAIL_API_KEY', raising=False)
    for key in OWNER: monkeypatch.delenv('EVA_RUNTIME_' + key.upper(), raising=False)
    monkeypatch.setattr(module, 'read_runtime_secret_file', lambda *a, **k: pytest.fail('secret read before owner validation'))
    with pytest.raises(RuntimeError, match='private owner'):
        AgentMailProviderFactory.load_settings()
