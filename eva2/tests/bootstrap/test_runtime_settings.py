from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from eva.adapters.model.contracts import ConversationCredentialStatus
from eva.application.people_memory_overview import PeopleMemoryOverview
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime, RuntimeSettings
from eva.contracts.workboard import WorkBoard
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.runtime_budget import RuntimeResourceBudget


def test_contact_model_is_opt_in_and_validated():
    assert RuntimeSettings("unused", "http://memory").conversation_contact_model is None
    with pytest.raises(ValueError, match="contact model"):
        RuntimeSettings("unused", "http://memory", conversation_contact_model=" ")
    with pytest.raises(ValueError, match="contact reasoning"):
        RuntimeSettings("unused", "http://memory", conversation_contact_reasoning_effort="invalid")


def test_result_delivery_has_a_compatible_disabled_rollback():
    from dataclasses import replace
    settings = RuntimeSettings("unused", "http://memory", delegated_work_enabled=True,
        dashboard_principal_binding_id="44444444-4444-4444-4444-444444444444",
        telegram_bot_token="123456:fixture", telegram_principal_bindings_file="/private/bindings")
    assert settings.delegated_result_delivery_enabled is False
    enabled = replace(settings, delegated_result_delivery_enabled=True)
    assert enabled.delegated_work_enabled
    assert replace(enabled, delegated_result_delivery_enabled=False) == settings
    for changes in ({"delegated_work_enabled": False}, {"telegram_bot_token": None},
                    {"telegram_principal_bindings_file": None}):
        with pytest.raises(ValueError, match="result delivery requires"):
            replace(enabled, **changes)
    with pytest.raises(ValueError, match="switch must be a boolean"):
        replace(settings, delegated_result_delivery_enabled="false")


def test_result_delivery_switch_environment_is_strict(monkeypatch, private_tmp_path):
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.delenv("EVA_DELEGATED_RESULT_DELIVERY_ENABLED", raising=False)
    assert RuntimeSettings.from_environment().delegated_result_delivery_enabled is False
    monkeypatch.setenv("EVA_DELEGATED_RESULT_DELIVERY_ENABLED", "false")
    assert RuntimeSettings.from_environment().delegated_result_delivery_enabled is False
    monkeypatch.setenv("EVA_DELEGATED_RESULT_DELIVERY_ENABLED", "invalid")
    with pytest.raises(RuntimeError):
        RuntimeSettings.from_environment()


def test_contact_model_environment(monkeypatch, private_tmp_path):
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_CONVERSATION_CONTACT_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("EVA_CONVERSATION_CONTACT_REASONING_EFFORT", "low")
    settings = RuntimeSettings.from_environment()
    assert settings.conversation_contact_model == "gpt-5.6-luna"
    assert settings.conversation_contact_reasoning_effort == "low"


def test_unreleased_household_session_requires_explicit_configuration(monkeypatch, private_tmp_path):
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.delenv("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", raising=False)
    assert RuntimeSettings.from_environment().household_voice_session_enabled is False
    monkeypatch.setenv("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", "true")
    assert RuntimeSettings.from_environment().household_voice_session_enabled is True
    monkeypatch.setenv("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", "invalid")
    with pytest.raises(RuntimeError):
        RuntimeSettings.from_environment()


def _write_runtime_budget(
    path: Path,
    *,
    tenant_id: str = "tenant:test",
    installation_id: str = "installation:test",
) -> RuntimeResourceBudget:
    budget_values = {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_person": 2_000,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    budget = RuntimeResourceBudget(
        tenant_id=tenant_id,
        installation_id=installation_id,
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(budget_values)
        ).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        memory_records_per_group=2_000,
        memory_bytes_per_group=20_971_520,
        memory_records_per_project=2_000,
        memory_bytes_per_project=20_971_520,
        memory_records_per_installation=20_000,
        memory_bytes_per_installation=209_715_200,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=40,
        external_monthly_spend_cap_eur_cents=0,
    )
    path.write_bytes(budget.to_bytes())
    path.chmod(0o600)
    return budget


def _minimum(monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path) -> None:
    monkeypatch.setenv("EVA_HINDSIGHT_URL", "http://hindsight:8888")
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", "installation:test")
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "tenant:test")
    for name in (
        "EVA_BACKGROUND_WORK_ENABLED",
        "EVA_DATABASE_DSN",
        "EVA_DATABASE_DSN_FILE",
        "EVA_HOME_ASSISTANT_URL",
        "EVA_HOME_ASSISTANT_TOKEN",
        "EVA_HOME_ASSISTANT_TOKEN_FILE",
        "EVA_HOME_ASSISTANT_EFFECTS_ENABLED",
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST",
        "EVA_AGENTMAIL_INBOX_ID",
        "EVA_AGENTMAIL_API_KEY_FILE",
        "EVA_TELEGRAM_PROACTIVE_CHAT_ID",
        "EVA_TELEGRAM_BOT_TOKEN",
        "EVA_TELEGRAM_BOT_TOKEN_FILE",
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE",
        "EVA_STRIPE_ACCOUNT_ID",
        "EVA_STRIPE_SECRET_KEY",
        "EVA_STRIPE_SECRET_KEY_FILE",
        "EVA_STRIPE_WEBHOOK_SECRET",
        "EVA_STRIPE_WEBHOOK_SECRET_FILE",
        "EVA_STRIPE_LIVE_MODE",
        "EVA_COMMERCE_PUBLIC_BASE_URL",
        "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON",
        "EVA_FANOUT_ALLOW_INSECURE_HTTP",
        "EVA_DURABLE_GOAL_PROVIDER",
        "EVA_DURABLE_GOAL_RECIPIENTS",
        "EVA_DASHBOARD_TENANT_ID",
        "EVA_DASHBOARD_INSTALLATION_ID",
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID",
        "EVA_DASHBOARD_ACCESS_TOKEN",
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE",
        "EVA_PORTABLE_RESTORE_AUTHORITY_FILE",
        "EVA_IDENTITY_PACK_FILE",
        "EVA_IDENTITY_PACK_TRUST_FILE",
        "EVA_RUNTIME_RESOURCE_BUDGET_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    database = private_tmp_path / "database-dsn"
    database.write_text("postgresql://runtime\n")
    database.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(database))
    budget_file = private_tmp_path / "runtime-budget.json"
    _write_runtime_budget(budget_file)
    monkeypatch.setenv("EVA_RUNTIME_RESOURCE_BUDGET_FILE", str(budget_file))


def test_runtime_reads_database_and_home_assistant_secrets_from_files(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    database = private_tmp_path / "database-dsn"
    database.write_text("postgresql+psycopg://eva:test@postgres/eva\n")
    database.chmod(0o600)
    token = private_tmp_path / "home-assistant-token"
    token.write_text("test-token\n")
    token.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(database))
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://home-assistant:8123")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN_FILE", str(token))

    settings = RuntimeSettings.from_environment()
    assert settings.database_dsn.endswith("@postgres/eva")
    assert settings.external_provider is not None
    home = settings.external_provider.providers[0].settings
    assert home.credential == "test-token"
    assert home.effects_enabled is False
    assert settings.conversation_reasoning_effort == "medium"

    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "true")
    monkeypatch.setenv(
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST",
        "light.dimmable_light_1:turn_off",
    )
    principal_sha256 = hashlib.sha256(b"telegram:123").hexdigest()
    monkeypatch.setenv(
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST", principal_sha256
    )
    provider = RuntimeSettings.from_environment().external_provider
    assert provider is not None
    home = provider.providers[0].settings
    assert home.effects_enabled is True
    assert home.effect_allowlist == ("light.dimmable_light_1:turn_off",)
    assert home.effect_principal_sha256_allowlist == (principal_sha256,)

    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "yes")
    with pytest.raises(RuntimeError, match="EFFECTS_ENABLED"):
        RuntimeSettings.from_environment()


def test_runtime_requires_a_strict_effect_allowlist(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "true")
    with pytest.raises(RuntimeError, match="EFFECT_ALLOWLIST is required"):
        RuntimeSettings.from_environment()

    for invalid in (
        "light.dimmable_light_1:toggle",
        "cover.garage:turn_off",
        "light.Bad:turn_off",
        "light.dimmable_light_1",
    ):
        monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST", invalid)
        with pytest.raises(RuntimeError, match="EFFECT_ALLOWLIST is invalid"):
            RuntimeSettings.from_environment()

    monkeypatch.setenv(
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST", "light.dimmable_light_1:turn_off"
    )
    monkeypatch.delenv(
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST", raising=False
    )
    with pytest.raises(RuntimeError, match="PRINCIPAL_SHA256_ALLOWLIST is required"):
        RuntimeSettings.from_environment()
    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST", "z" * 64)
    with pytest.raises(RuntimeError, match="PRINCIPAL_SHA256_ALLOWLIST is invalid"):
        RuntimeSettings.from_environment()


def test_runtime_requires_custom_identity_pack_and_trust_together(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_IDENTITY_PACK_FILE", "/run/eva/identity/current.json")
    with pytest.raises(
        RuntimeError, match="IDENTITY_PACK_FILE and EVA_IDENTITY_PACK_TRUST_FILE"
    ):
        RuntimeSettings.from_environment()

    monkeypatch.setenv("EVA_IDENTITY_PACK_TRUST_FILE", "/run/eva/identity/trust.json")
    settings = RuntimeSettings.from_environment()
    assert settings.identity_pack_file == "/run/eva/identity/current.json"
    assert settings.identity_pack_trust_file == "/run/eva/identity/trust.json"


def test_dashboard_owner_binding_is_explicit_and_uuid_bound(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv(
        "EVA_RUNTIME_INSTALLATION_ID", "22222222-2222-2222-2222-222222222222"
    )
    _write_runtime_budget(
        private_tmp_path / "runtime-budget.json",
        tenant_id="11111111-1111-1111-1111-111111111111",
        installation_id="22222222-2222-2222-2222-222222222222",
    )
    monkeypatch.setenv(
        "EVA_RUNTIME_PRINCIPAL_ID", "33333333-3333-3333-3333-333333333333"
    )
    monkeypatch.setenv(
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID",
        "44444444-4444-4444-4444-444444444444",
    )
    dashboard_token = private_tmp_path / "dashboard-token"
    dashboard_token.write_text("t" * 32)
    dashboard_token.chmod(0o600)
    monkeypatch.setenv("EVA_DASHBOARD_ACCESS_TOKEN_FILE", str(dashboard_token))

    with pytest.raises(RuntimeError, match="requires a portable restore authority"):
        RuntimeSettings.from_environment()
    authority_file = private_tmp_path / "restore-authority" / "current.json"
    monkeypatch.setenv("EVA_PORTABLE_RESTORE_AUTHORITY_FILE", str(authority_file))

    settings = RuntimeSettings.from_environment()
    assert settings.dashboard_principal_binding_id == (
        "44444444-4444-4444-4444-444444444444"
    )
    assert settings.portable_restore_authority_file == str(authority_file)

    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "legacy-tenant")
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", "legacy-installation")
    _write_runtime_budget(
        private_tmp_path / "runtime-budget.json",
        tenant_id="legacy-tenant",
        installation_id="legacy-installation",
    )
    monkeypatch.setenv(
        "EVA_DASHBOARD_TENANT_ID", "11111111-1111-1111-1111-111111111111"
    )
    monkeypatch.setenv(
        "EVA_DASHBOARD_INSTALLATION_ID",
        "22222222-2222-2222-2222-222222222222",
    )
    settings = RuntimeSettings.from_environment()
    assert settings.background_tenant_id == "legacy-tenant"
    assert settings.dashboard_tenant_id == ("11111111-1111-1111-1111-111111111111")

    monkeypatch.setenv("EVA_DASHBOARD_PRINCIPAL_BINDING_ID", "not-a-uuid")
    with pytest.raises(RuntimeError, match="dashboard owner binding is invalid"):
        RuntimeSettings.from_environment()


def test_dashboard_access_token_is_file_only_and_required_for_owner_binding(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_DASHBOARD_ACCESS_TOKEN", "t" * 32)
    with pytest.raises(RuntimeError, match="DASHBOARD_ACCESS_TOKEN is forbidden"):
        RuntimeSettings.from_environment()

    monkeypatch.delenv("EVA_DASHBOARD_ACCESS_TOKEN")
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv(
        "EVA_RUNTIME_INSTALLATION_ID", "22222222-2222-2222-8222-222222222222"
    )
    _write_runtime_budget(
        private_tmp_path / "runtime-budget.json",
        tenant_id="11111111-1111-1111-1111-111111111111",
        installation_id="22222222-2222-2222-8222-222222222222",
    )
    monkeypatch.setenv(
        "EVA_RUNTIME_PRINCIPAL_ID", "33333333-3333-3333-8333-333333333333"
    )
    monkeypatch.setenv(
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID",
        "44444444-4444-4444-8444-444444444444",
    )
    with pytest.raises(RuntimeError, match="requires a dashboard access token file"):
        RuntimeSettings.from_environment()

    token = private_tmp_path / "dashboard-token"
    token.write_text("t" * 31)
    token.chmod(0o600)
    monkeypatch.setenv("EVA_DASHBOARD_ACCESS_TOKEN_FILE", str(token))
    with pytest.raises(RuntimeError, match="invalid length"):
        RuntimeSettings.from_environment()


def test_runtime_reads_and_bounds_conversation_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_CONVERSATION_REASONING_EFFORT", "high")
    assert RuntimeSettings.from_environment().conversation_reasoning_effort == "high"

    monkeypatch.setenv("EVA_CONVERSATION_REASONING_EFFORT", "extreme")
    with pytest.raises(RuntimeError, match="REASONING_EFFORT"):
        RuntimeSettings.from_environment()


def test_runtime_reads_and_bounds_hindsight_recall_limits(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    defaults = RuntimeSettings.from_environment()
    assert defaults.hindsight_recall_max_tokens == 3_000
    assert defaults.hindsight_recall_node_budget == 50

    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_BUDGET", "mid")
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "3000")
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "50")

    settings = RuntimeSettings.from_environment()

    assert settings.hindsight_recall_budget == "mid"
    assert settings.hindsight_recall_max_tokens == 3000
    assert settings.hindsight_recall_node_budget == 50

    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "25")
    with pytest.raises(RuntimeError, match="differs from runtime budget"):
        RuntimeSettings.from_environment()

    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "50")
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "4001")
    with pytest.raises(RuntimeError, match="RECALL_MAX_TOKENS"):
        RuntimeSettings.from_environment()

    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "not-an-int")
    with pytest.raises(RuntimeError, match="Hindsight recall configuration"):
        RuntimeSettings.from_environment()

    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "3000")
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "19")
    with pytest.raises(RuntimeError, match="RECALL_NODE_BUDGET"):
        RuntimeSettings.from_environment()


def test_runtime_requires_private_scoped_resource_budget(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    budget_file = private_tmp_path / "runtime-budget.json"

    monkeypatch.delenv("EVA_RUNTIME_RESOURCE_BUDGET_FILE")
    with pytest.raises(
        RuntimeError, match="resource budget file and scope are required"
    ):
        RuntimeSettings.from_environment()

    monkeypatch.setenv("EVA_RUNTIME_RESOURCE_BUDGET_FILE", str(budget_file))
    budget_file.chmod(0o644)
    with pytest.raises(RuntimeError, match="resource budget is invalid"):
        RuntimeSettings.from_environment()

    _write_runtime_budget(budget_file)
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", "installation:other")
    with pytest.raises(RuntimeError, match="resource budget is invalid"):
        RuntimeSettings.from_environment()


def test_runtime_rejects_tampered_resource_budget(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    budget_file = private_tmp_path / "runtime-budget.json"
    payload = json.loads(budget_file.read_bytes())
    payload["budget"]["max_input_tokens"] = 11_999
    budget_file.write_bytes(canonical_json_bytes(payload))
    budget_file.chmod(0o600)

    with pytest.raises(RuntimeError, match="resource budget is invalid"):
        RuntimeSettings.from_environment()


def test_runtime_rejects_legacy_resource_budget_contract(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    budget_file = private_tmp_path / "runtime-budget.json"
    payload = json.loads(budget_file.read_bytes())
    payload["contract"] = "EvaRuntimeResourceBudget/v1"
    payload["budget"]["contract"] = "EvaInstallerBudget/v1"
    budget_file.write_bytes(canonical_json_bytes(payload))
    budget_file.chmod(0o600)

    with pytest.raises(RuntimeError, match="resource budget is invalid"):
        RuntimeSettings.from_environment()


def test_runtime_rejects_insecure_home_assistant_file_before_connector_setup(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    token = private_tmp_path / "home-assistant-token"
    token.write_text("not-a-real-token\n")
    token.chmod(0o640)
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://home-assistant:8123")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN_FILE", str(token))

    with pytest.raises(RuntimeError, match="group or other permissions"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("inline_value", ("", "not-a-real-token"))
def test_runtime_forbids_inline_home_assistant_token_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
    inline_value: str,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://home-assistant:8123")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN", inline_value)

    with pytest.raises(RuntimeError, match="HOME_ASSISTANT_TOKEN is forbidden"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("inline_value", ("", "telegram-test-value"))
def test_runtime_forbids_inline_telegram_token_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
    inline_value: str,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_TELEGRAM_PROACTIVE_CHAT_ID", "approved-chat")
    monkeypatch.setenv("EVA_TELEGRAM_BOT_TOKEN", inline_value)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is forbidden"):
        RuntimeSettings.from_environment()


def test_runtime_reads_telegram_token_from_file(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_TELEGRAM_PROACTIVE_CHAT_ID", "approved-chat")
    telegram = private_tmp_path / "telegram-token"
    telegram.write_text("telegram-test-value\n")
    telegram.chmod(0o600)
    monkeypatch.setenv("EVA_TELEGRAM_BOT_TOKEN_FILE", str(telegram))
    settings = RuntimeSettings.from_environment()

    assert settings.database_dsn == "postgresql://runtime"
    assert settings.telegram_bot_token == "telegram-test-value"


def test_proactive_runtime_derives_and_rechecks_private_owner(monkeypatch):
    import eva.bootstrap.runtime as runtime
    from eva.application.free_will import ProactiveExpressionClaim

    calls = []
    allowed = [True]
    tenant = UUID("00000000-0000-0000-0000-000000000001")
    installation = UUID("00000000-0000-0000-0000-000000000002")
    owner = UUID("00000000-0000-0000-0000-000000000003")
    binding = UUID("00000000-0000-0000-0000-000000000004")
    def load(path):
        assert path == "/private/owner-bindings"
        calls.append("reload-bindings")
        return object()
    class Recipient:
        def __init__(self, engine, scope, **kwargs):
            assert scope.tenant_id == tenant and scope.installation_id == installation
            assert kwargs["owner"] == owner and kwargs["binding"] == binding
            self.bindings = kwargs["bindings"]
        def current_chat_id(self, *, now):
            self.bindings()
            assert now.tzinfo is not None
            if not allowed[0]:
                raise RuntimeError("binding revoked")
            return "42"
    monkeypatch.setattr(runtime, "PostgresPrivateTelegramRecipient", Recipient)
    monkeypatch.setattr(runtime, "load_principal_bindings", load)
    settings = RuntimeSettings(
        database_dsn="unused", hindsight_url="unused", telegram_bot_token="123456:test",
        telegram_principal_bindings_file="/private/owner-bindings",
        dashboard_tenant_id=str(tenant), dashboard_installation_id=str(installation),
        dashboard_principal_binding_id=str(binding), background_principal_id=str(owner),
    )
    delivery = runtime._build_proactive_delivery(settings, object())
    assert delivery is not None  # No manually supplied chat ID or polling worker.
    def send(method, body, **kwargs):
        calls.append(method)
        return {"message_id": 7, "chat": {"id": 42, "type": "private"}, "text": body["text"]}
    monkeypatch.setattr(delivery._api, "_call", send)
    claim = ProactiveExpressionClaim("thread", "op", 1, 0, "event", "Fixture", 1)
    assert delivery.deliver(claim) == "telegram:42:7"
    allowed[0] = False
    with pytest.raises(RuntimeError, match="revoked"):
        delivery.deliver(claim)
    assert calls == ["reload-bindings", "reload-bindings", "sendMessage", "reload-bindings"]


def test_proactive_runtime_without_token_stays_off_and_unbound_token_is_rejected():
    from eva.bootstrap.runtime import _build_proactive_delivery
    assert _build_proactive_delivery(RuntimeSettings(database_dsn="unused", hindsight_url="unused"), object()) is None
    with pytest.raises(RuntimeError, match="verified private owner"):
        _build_proactive_delivery(RuntimeSettings(
            database_dsn="unused", hindsight_url="unused", telegram_bot_token="123456:test",
        ), object())


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    (
        ("EVA_STRIPE_SECRET_KEY", "", "STRIPE_SECRET_KEY is forbidden"),
        (
            "EVA_STRIPE_SECRET_KEY",
            "sk_test_private",
            "STRIPE_SECRET_KEY is forbidden",
        ),
        ("EVA_STRIPE_WEBHOOK_SECRET", "", "STRIPE_WEBHOOK_SECRET is forbidden"),
        (
            "EVA_STRIPE_WEBHOOK_SECRET",
            "whsec_test_signing_secret",
            "STRIPE_WEBHOOK_SECRET is forbidden",
        ),
    ),
)
def test_runtime_forbids_inline_stripe_secrets_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
    name: str,
    value: str,
    expected: str,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_STRIPE_ACCOUNT_ID", "acct_12345678")
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVA_COMMERCE_PUBLIC_BASE_URL", "https://eva.example")

    with pytest.raises(RuntimeError, match=expected):
        RuntimeSettings.from_environment()


def test_runtime_reads_bounded_fanout_sink_configuration(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv(
        "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON",
        '{"sink-a":"http://fanout-sink:8090","sink-b":"http://fanout-sink:8090"}',
    )
    monkeypatch.setenv("EVA_FANOUT_ALLOW_INSECURE_HTTP", "true")

    settings = RuntimeSettings.from_environment()
    assert settings.fanout_recipient_endpoints == {
        "sink-a": "http://fanout-sink:8090",
        "sink-b": "http://fanout-sink:8090",
    }
    assert settings.fanout_allow_insecure_http is True


def test_runtime_binds_durable_goal_provider_and_recipients_together(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_DURABLE_GOAL_PROVIDER", "goal-test")
    monkeypatch.setenv("EVA_DURABLE_GOAL_RECIPIENTS", "sink-a,sink-b")
    settings = RuntimeSettings.from_environment()
    assert settings.durable_goal_provider == "goal-test"
    assert settings.durable_goal_recipients == ("sink-a", "sink-b")

    monkeypatch.delenv("EVA_DURABLE_GOAL_RECIPIENTS")
    with pytest.raises(RuntimeError, match="durable goal configuration"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("inline_value", ("", "postgresql://from-environment"))
def test_runtime_forbids_inline_database_dsn_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
    inline_value: str,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_DATABASE_DSN", inline_value)
    with pytest.raises(RuntimeError, match="EVA_DATABASE_DSN is forbidden"):
        RuntimeSettings.from_environment()


def test_runtime_reads_stripe_secret_from_file_and_binds_mode(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    secret = private_tmp_path / "stripe-secret"
    secret.write_text("sk_test_private\n")
    secret.chmod(0o600)
    webhook = private_tmp_path / "stripe-webhook-secret"
    webhook.write_text("whsec_test_signing_secret_12345678\n")
    webhook.chmod(0o600)
    monkeypatch.setenv("EVA_STRIPE_ACCOUNT_ID", "acct_12345678")
    monkeypatch.setenv("EVA_STRIPE_SECRET_KEY_FILE", str(secret))
    monkeypatch.setenv("EVA_STRIPE_WEBHOOK_SECRET_FILE", str(webhook))
    monkeypatch.setenv("EVA_STRIPE_LIVE_MODE", "false")
    monkeypatch.setenv("EVA_COMMERCE_PUBLIC_BASE_URL", "https://eva.example")
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "tenant:test")
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", "installation:test")

    settings = RuntimeSettings.from_environment()
    assert settings.stripe_account_id == "acct_12345678"
    assert settings.stripe_secret_key == "sk_test_private"
    assert settings.stripe_webhook_secret == "whsec_test_signing_secret_12345678"
    assert settings.commerce_public_base_url == "https://eva.example"
    assert settings.stripe_live_mode is False


def test_runtime_rejects_partial_or_invalid_stripe_configuration(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_STRIPE_ACCOUNT_ID", "acct_12345678")
    with pytest.raises(RuntimeError, match="Stripe commerce configuration"):
        RuntimeSettings.from_environment()

    secret = private_tmp_path / "stripe-secret"
    secret.write_text("sk_test_private\n")
    secret.chmod(0o600)
    webhook = private_tmp_path / "stripe-webhook-secret"
    webhook.write_text("whsec_test_signing_secret\n")
    webhook.chmod(0o600)
    monkeypatch.setenv("EVA_STRIPE_SECRET_KEY_FILE", str(secret))
    monkeypatch.setenv("EVA_STRIPE_WEBHOOK_SECRET_FILE", str(webhook))
    monkeypatch.setenv("EVA_COMMERCE_PUBLIC_BASE_URL", "https://eva.example")
    monkeypatch.setenv("EVA_STRIPE_LIVE_MODE", "sometimes")
    with pytest.raises(RuntimeError, match="STRIPE_LIVE_MODE"):
        RuntimeSettings.from_environment()


def test_runtime_readiness_reports_a_rejected_model_lease() -> None:
    class Connection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def scalar(self, _statement: object) -> int:
            return 1

    class Engine:
        def connect(self) -> Connection:
            return Connection()

    class Memory:
        def health(self) -> None:
            pass

    class Model:
        def credential_status(self) -> ConversationCredentialStatus:
            return ConversationCredentialStatus(
                False,
                "rejected",
                datetime(2026, 8, 23, tzinfo=UTC),
            )

    runtime = PostgresHindsightMemoryRuntime(
        Engine(),  # type: ignore[arg-type]
        Memory(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        conversation_model=Model(),  # type: ignore[arg-type]
    )

    result = runtime.health()
    assert result.ready is False
    assert result.database == "ready"
    assert result.memory_projection == "ready"
    assert result.model_provider == "credentials_rejected"


def test_runtime_composes_the_real_people_memory_projection_fail_closed() -> None:
    class Memory:
        available = True

        def health(self) -> None:
            if not self.available:
                raise RuntimeError("private Hindsight detail canary")

    class Overview:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def snapshot(
            self, *, now: datetime, hindsight_state: str
        ) -> PeopleMemoryOverview:
            assert now.tzinfo is not None
            assert now.microsecond == 0
            self.calls.append(hindsight_state)
            return PeopleMemoryOverview(
                dynamic_account="no_extra_account",
                group_sharing="private_only",
                memory_flow="no_signals",
                hindsight=hindsight_state,  # type: ignore[arg-type]
            )

    memory = Memory()
    overview = Overview()
    runtime = PostgresHindsightMemoryRuntime(
        object(),  # type: ignore[arg-type]
        memory,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        runtime_tenant_id="tenant:cockpit-runtime",
        runtime_installation_id="installation:cockpit-runtime",
        people_memory_overview=overview,  # type: ignore[arg-type]
    )

    assert runtime.dashboard_people_memory().hindsight == "ready"
    memory.available = False
    assert runtime.dashboard_people_memory().hindsight == "attention"
    assert overview.calls == ["ready", "attention"]


def test_runtime_workboard_uses_only_its_fixed_owner_and_binding() -> None:
    owner = UUID("33333333-3333-3333-3333-333333333333")
    binding = UUID("44444444-4444-4444-4444-444444444444")

    class Store:
        def __init__(self) -> None:
            self.calls: list[tuple[UUID, UUID]] = []

        def workboard(
            self,
            *,
            owner_principal_id: UUID,
            principal_binding_id: UUID,
            now: datetime,
        ) -> WorkBoard:
            assert now.tzinfo is not None and now.microsecond == 0
            self.calls.append((owner_principal_id, principal_binding_id))
            return WorkBoard(
                contract="EvaWorkBoard/v1",
                schema_version=1,
                read_only=True,
                status="ready",
                cards=[],
                truncated=False,
                message="Nog geen duurzame subagenttaken.",
            )

    store = Store()
    runtime = PostgresHindsightMemoryRuntime(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        delegated_work_store=store,  # type: ignore[arg-type]
        dashboard_owner_principal_id=owner,
        dashboard_principal_binding_id=binding,
    )

    assert runtime.dashboard_workboard().cards == []
    assert store.calls == [(owner, binding)]


def test_owner_memory_sources_use_only_server_scope_without_provider_calls(monkeypatch):
    from eva.adapters.postgres import people_memory_overview
    from eva.adapters.postgres.operational_timeline import TimelineScope
    from eva.application.people_memory_overview import MemorySourcePage

    owner = UUID("33333333-3333-3333-3333-333333333333")
    binding = UUID("44444444-4444-4444-8444-444444444444")
    seen = []
    authority_scope = TimelineScope(UUID(int=101), UUID(int=102))
    loaded = []

    def load_bindings():
        loaded.append(True)
        return None

    class Projection:
        def __init__(self, engine, **kwargs):
            seen.append(kwargs)

        def page(self, **kwargs):
            seen.append(kwargs)
            return MemorySourcePage(())

    monkeypatch.setattr(people_memory_overview, "PostgresOwnerMemorySources", Projection)
    runtime = PostgresHindsightMemoryRuntime(
        object(), object(), None, None, None, None, None,
        runtime_tenant_id="tenant:dashboard", runtime_installation_id="installation:dashboard",
        delegated_work_store=object(), dashboard_owner_principal_id=owner,
        dashboard_principal_binding_id=binding,
        dashboard_authority_scope=authority_scope,
        telegram_principal_bindings_loader=load_bindings,
    )
    assert runtime.dashboard_memory_sources(limit=2).sources == ()
    assert seen[0] == {"tenant_id":"tenant:dashboard", "installation_id":"installation:dashboard",
                       "owner":owner, "binding":binding, "telegram_bindings":None,
                       "authority_scope":authority_scope}
    assert seen[1]["limit"] == 2 and seen[1]["before"] is None
    assert loaded == [True]
    runtime.dashboard_memory_sources(limit=1)
    assert loaded == [True, True]
    runtime._dashboard_authority_scope = None
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime.dashboard_memory_sources()
    runtime._dashboard_authority_scope = authority_scope
    runtime._dashboard_owner_principal_id = None
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime.dashboard_memory_sources()
    assert len(seen) == 4


def test_dashboard_owner_turns_keep_one_server_bound_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = UUID("33333333-3333-3333-3333-333333333333")
    binding = UUID("44444444-4444-4444-8444-444444444444")
    seen = []

    def respond(runtime: PostgresHindsightMemoryRuntime, turn: object) -> object:
        _ = runtime
        seen.append(turn)
        return object()

    monkeypatch.setattr(PostgresHindsightMemoryRuntime, "respond", respond)
    runtime = PostgresHindsightMemoryRuntime(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        runtime_tenant_id="tenant:dashboard",
        runtime_installation_id="installation:dashboard",
        delegated_work_store=object(),  # type: ignore[arg-type]
        dashboard_owner_principal_id=owner,
        dashboard_principal_binding_id=binding,
    )

    first, _ = runtime.dashboard_turn("Eerste bericht")
    second, _ = runtime.dashboard_turn("Tweede bericht")

    assert first.conversation_id == second.conversation_id == f"dashboard:{binding}"
    assert first.message_id != second.message_id
    assert seen == [first, second]


def test_dashboard_erasure_uses_only_fixed_owner_and_conversation() -> None:
    owner = UUID("33333333-3333-3333-3333-333333333333")
    binding = UUID("44444444-4444-4444-8444-444444444444")
    message_uuid = UUID("55555555-5555-4555-8555-555555555555")

    class ColdPath:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def erase_document(self, **kwargs: object) -> bool:
            self.calls.append(kwargs)
            return True

    cold_path = ColdPath()
    runtime = PostgresHindsightMemoryRuntime(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        cold_path,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        runtime_tenant_id="tenant:dashboard",
        runtime_installation_id="installation:dashboard",
        delegated_work_store=object(),  # type: ignore[arg-type]
        dashboard_owner_principal_id=owner,
        dashboard_principal_binding_id=binding,
    )

    message_id = f"dashboard:{message_uuid}"
    assert runtime.dashboard_erase_conversation_message(message_id)
    assert len(cold_path.calls) == 1
    call = cold_path.calls[0]
    assert call["principal_id"] == str(owner)
    assert call["document_id"] == (f"dashboard:{binding}:exchange:{message_id}")
    assert call["source_message_id"] == message_id
    assert getattr(call["now"], "tzinfo", None) is not None

    with pytest.raises(ValueError, match="message is invalid"):
        runtime.dashboard_erase_conversation_message("telegram:other-person:1")
    assert len(cold_path.calls) == 1


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "32"])
def test_conversation_request_limit_requires_positive_integer(value):
    with pytest.raises(ValueError, match="request limit"):
        RuntimeSettings("unused", "http://memory", conversation_maximum_requests=value)


def test_conversation_request_limit_has_configurable_turn_local_default():
    assert RuntimeSettings("unused", "http://memory").conversation_maximum_requests == 32
    assert RuntimeSettings("unused", "http://memory", conversation_maximum_requests=96).conversation_maximum_requests == 96


@pytest.mark.parametrize("raw, expected", [(None, True), ("true", True), ("false", False)])
def test_runtime_background_work_switch(monkeypatch, private_tmp_path, raw, expected):
    _minimum(monkeypatch, private_tmp_path)
    if raw is not None:
        monkeypatch.setenv("EVA_BACKGROUND_WORK_ENABLED", raw)
    assert RuntimeSettings.from_environment().background_work_enabled is expected


@pytest.mark.parametrize("raw", ["yes", "0", "disabled"])
def test_runtime_rejects_ambiguous_background_work_switch(monkeypatch, private_tmp_path, raw):
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_BACKGROUND_WORK_ENABLED", raw)
    with pytest.raises(RuntimeError, match="EVA_BACKGROUND_WORK_ENABLED"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("enabled", [False, True])
def test_reserve_lifecycle_cannot_start_observation_or_autonomous_workers(enabled):
    from unittest.mock import Mock

    cold_path, sensory = Mock(), Mock()
    runtime = PostgresHindsightMemoryRuntime(
        None, None, None, cold_path, None, None, None,
        sensory_worker=sensory, background_work_enabled=enabled,
    )
    runtime.start_background_work()
    runtime.stop_background_work()
    if enabled:
        cold_path.start.assert_called_once_with()
        sensory.start.assert_called_once_with()
        sensory.stop.assert_called_once_with()
        cold_path.stop.assert_called_once_with()
    else:
        assert cold_path.mock_calls == []
        assert sensory.mock_calls == []


@pytest.mark.parametrize("invalid", ["false", 0, None])
def test_runtime_background_switch_rejects_non_boolean_constructor_values(invalid):
    with pytest.raises(ValueError, match="background work switch"):
        RuntimeSettings("postgresql://test", "http://memory", background_work_enabled=invalid)
    with pytest.raises(ValueError, match="background work switch"):
        PostgresHindsightMemoryRuntime(
            None, None, None, None, None, None, None, background_work_enabled=invalid,
        )
