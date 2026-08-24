from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime

import pytest

from eva.bootstrap.runtime import RuntimeSettings
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from eva.adapters.model.conversation import ConversationCredentialStatus


def _minimum(monkeypatch: pytest.MonkeyPatch) -> None:
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
    ):
        monkeypatch.delenv(name, raising=False)


def test_runtime_reads_database_and_home_assistant_secrets_from_files(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch)
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


def test_runtime_reads_and_bounds_conversation_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
    monkeypatch.setenv("EVA_CONVERSATION_REASONING_EFFORT", "high")
    assert RuntimeSettings.from_environment().conversation_reasoning_effort == "high"

    monkeypatch.setenv("EVA_CONVERSATION_REASONING_EFFORT", "extreme")
    with pytest.raises(RuntimeError, match="REASONING_EFFORT"):
        RuntimeSettings.from_environment()


def test_runtime_reads_and_bounds_hindsight_recall_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
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
    _minimum(monkeypatch)
    token = private_tmp_path / "home-assistant-token"
    token.write_text("not-a-real-token\n")
    token.chmod(0o640)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://home-assistant:8123")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN_FILE", str(token))

    with pytest.raises(RuntimeError, match="group or other permissions"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("inline_value", ("", "not-a-real-token"))
def test_runtime_forbids_inline_home_assistant_token_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    inline_value: str,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://home-assistant:8123")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN", inline_value)

    with pytest.raises(RuntimeError, match="HOME_ASSISTANT_TOKEN is forbidden"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("inline_value", ("", "telegram-test-value"))
def test_runtime_forbids_inline_telegram_token_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    inline_value: str,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
    monkeypatch.setenv("EVA_TELEGRAM_PROACTIVE_CHAT_ID", "approved-chat")
    monkeypatch.setenv("EVA_TELEGRAM_BOT_TOKEN", inline_value)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is forbidden"):
        RuntimeSettings.from_environment()


def test_runtime_reads_telegram_token_from_file(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
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
    name: str,
    value: str,
    expected: str,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
    monkeypatch.setenv("EVA_STRIPE_ACCOUNT_ID", "acct_12345678")
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVA_COMMERCE_PUBLIC_BASE_URL", "https://eva.example")

    with pytest.raises(RuntimeError, match=expected):
        RuntimeSettings.from_environment()


def test_runtime_reads_bounded_fanout_sink_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
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
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
    monkeypatch.setenv("EVA_DURABLE_GOAL_PROVIDER", "goal-test")
    monkeypatch.setenv("EVA_DURABLE_GOAL_RECIPIENTS", "sink-a,sink-b")
    settings = RuntimeSettings.from_environment()
    assert settings.durable_goal_provider == "goal-test"
    assert settings.durable_goal_recipients == ("sink-a", "sink-b")

    monkeypatch.delenv("EVA_DURABLE_GOAL_RECIPIENTS")
    with pytest.raises(RuntimeError, match="durable goal configuration"):
        RuntimeSettings.from_environment()


def test_runtime_rejects_ambiguous_secret_sources(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch)
    database = private_tmp_path / "database-dsn"
    database.write_text("postgresql://from-file")
    database.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://from-environment")
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(database))
    with pytest.raises(RuntimeError, match="only one"):
        RuntimeSettings.from_environment()


def test_runtime_reads_stripe_secret_from_file_and_binds_mode(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
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
    _minimum(monkeypatch)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://runtime")
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
