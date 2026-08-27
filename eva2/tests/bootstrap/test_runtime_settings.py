from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from eva.adapters.model.conversation import ConversationCredentialStatus
from eva.application.people_memory_overview import PeopleMemoryOverview
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime, RuntimeSettings
from eva.contracts.workboard import WorkBoard


def _minimum(monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path) -> None:
    monkeypatch.setenv("EVA_HINDSIGHT_URL", "http://hindsight:8888")
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", "installation:test")
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "tenant:test")
    for name in (
        "EVA_DATABASE_DSN",
        "EVA_DATABASE_DSN_FILE",
        "EVA_HOME_ASSISTANT_URL",
        "EVA_HOME_ASSISTANT_TOKEN",
        "EVA_HOME_ASSISTANT_TOKEN_FILE",
        "EVA_TELEGRAM_PROACTIVE_CHAT_ID",
        "EVA_TELEGRAM_BOT_TOKEN",
        "EVA_TELEGRAM_BOT_TOKEN_FILE",
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
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID",
        "EVA_DASHBOARD_ACCESS_TOKEN",
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    database = private_tmp_path / "database-dsn"
    database.write_text("postgresql://runtime\n")
    database.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(database))


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
    assert settings.home_assistant_token == "test-token"
    assert settings.conversation_reasoning_effort == "medium"


def test_dashboard_owner_binding_is_explicit_and_uuid_bound(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv(
        "EVA_RUNTIME_INSTALLATION_ID", "22222222-2222-2222-2222-222222222222"
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

    settings = RuntimeSettings.from_environment()
    assert settings.dashboard_principal_binding_id == (
        "44444444-4444-4444-4444-444444444444"
    )

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
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_BUDGET", "mid")
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "3000")
    monkeypatch.setenv("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "25")

    settings = RuntimeSettings.from_environment()

    assert settings.hindsight_recall_budget == "mid"
    assert settings.hindsight_recall_max_tokens == 3000
    assert settings.hindsight_recall_node_budget == 25

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
