"""Shared environment configuration without application or voice composition."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from eva.providers.runtime import ExternalProviderSettings, load_external_provider_settings
from eva.contracts.runtime_budget import RuntimeResourceBudget, RuntimeResourceBudgetError
from eva.adapters.credentials.runtime_files import environment_secret, read_private_runtime_file_bytes
from eva.contracts.dashboard_access_token import validate_dashboard_access_token


def _environment_value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _environment_optional(name: str) -> str | None:
    return _environment_value(name) or None


def _environment_items(
    name: str, default: str = "", *, unique_sorted: bool = False
) -> tuple[str, ...]:
    values = tuple(
        item.strip()
        for item in _environment_value(name, default).split(",")
        if item.strip()
    )
    return tuple(sorted(set(values))) if unique_sorted else values


def _environment_int(name: str, default: int, invalid: str) -> int:
    try:
        return int(_environment_value(name, str(default)))
    except ValueError as error:
        raise RuntimeError(invalid) from error


def _environment_float(name: str, default: float, invalid: str) -> float:
    try:
        return float(_environment_value(name, str(default)))
    except ValueError as error:
        raise RuntimeError(invalid) from error


def _environment_bool(
    name: str, default: bool = False, invalid: str | None = None
) -> bool:
    raw = _environment_value(name, str(default).lower()).casefold()
    if raw not in {"false", "true"}:
        raise RuntimeError(invalid or f"{name} must be true or false")
    return raw == "true"


def _file_only_environment_secret(value_name: str, file_name: str) -> str:
    if value_name in os.environ:
        raise RuntimeError(f"{value_name} is forbidden; use {file_name}")
    return environment_secret(value_name, file_name)


def _require_complete_configuration(invalid: str, *values: object) -> None:
    present = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise RuntimeError(invalid)


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    database_dsn: str
    hindsight_url: str
    hindsight_bank_namespace: str = "eva2"
    hindsight_recall_budget: str = "low"
    hindsight_recall_max_tokens: int = 2048
    hindsight_recall_node_budget: int = 40
    identity_name: str = "Eva"
    identity_description: str = (
        "Je bent een warme, nuchtere en capabele digitale huisgenoot."
    )
    identity_pack_file: str | None = None
    identity_pack_trust_file: str | None = None
    conversation_auth_file: str = "/run/eva/codex/auth.json"
    conversation_model: str = "gpt-6-astra"
    conversation_maximum_requests: int = 32
    conversation_reasoning_effort: str = "medium"
    conversation_contact_model: str | None = None
    conversation_contact_reasoning_effort: str = "low"
    runtime_resource_budget: RuntimeResourceBudget | None = None
    background_work_enabled: bool = True
    delegated_work_enabled: bool = False
    delegated_research_enabled: bool = False
    delegated_digital_enabled: bool = False
    delegated_result_delivery_enabled: bool = False
    background_tenant_id: str | None = None
    background_installation_id: str | None = None
    background_principal_id: str | None = None
    dashboard_tenant_id: str | None = None
    dashboard_installation_id: str | None = None
    dashboard_principal_binding_id: str | None = None
    dashboard_access_token: str | None = None
    dashboard_voice_policy_file: str | None = None
    household_voice_policy_file: str | None = None
    household_voice_token_file: str | None = None
    household_voice_session_enabled: bool = False
    household_work_config_file: str | None = None
    portable_restore_authority_file: str | None = None
    cold_path_idle_seconds: float = 300.0
    personal_interest_lease_seconds: int = 90
    external_provider: ExternalProviderSettings | None = None
    telegram_proactive_chat_id: str | None = None
    telegram_bot_token: str | None = None
    telegram_principal_bindings_file: str | None = None
    stripe_account_id: str | None = None
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    commerce_public_base_url: str | None = None
    stripe_live_mode: bool = False
    file_batch_root: str | None = None
    fanout_recipient_endpoints: dict[str, str] | None = None
    fanout_allow_insecure_http: bool = False
    durable_goal_provider: str | None = None
    durable_goal_recipients: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.conversation_contact_model is not None and not self.conversation_contact_model.strip():
            raise ValueError("conversation contact model must be nonempty")
        if self.conversation_contact_reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ValueError("conversation contact reasoning effort is invalid")
        if self.conversation_contact_model == "gpt-6-sol":
            if self.conversation_contact_reasoning_effort != "none":
                raise ValueError("Sol contact requires reasoning none")
            if self.conversation_model != "gpt-6-astra" or self.conversation_reasoning_effort not in {"medium", "high"}:
                raise ValueError("Sol contact requires Astra work at medium or higher")
        if type(self.background_work_enabled) is not bool:
            raise ValueError("background work switch must be a boolean")
        if type(self.delegated_work_enabled) is not bool:
            raise ValueError("delegated work switch must be a boolean")
        if type(self.delegated_research_enabled) is not bool:
            raise ValueError("delegated research switch must be a boolean")
        if type(self.delegated_digital_enabled) is not bool:
            raise ValueError("delegated digital work switch must be a boolean")
        if self.delegated_digital_enabled and (
            not self.delegated_work_enabled or self.runtime_resource_budget is None
        ):
            raise ValueError("delegated digital work requires delegated work and its runtime budget")
        if type(self.delegated_result_delivery_enabled) is not bool:
            raise ValueError("delegated result delivery switch must be a boolean")
        if self.delegated_result_delivery_enabled and (
            not self.delegated_work_enabled or self.telegram_bot_token is None
            or self.telegram_principal_bindings_file is None
        ):
            raise ValueError("delegated result delivery requires work and its private Telegram route")
        if self.delegated_research_enabled and (
            not self.delegated_work_enabled or self.runtime_resource_budget is None
        ):
            raise ValueError("delegated research requires delegated work and its runtime budget")
        if type(self.household_voice_session_enabled) is not bool:
            raise ValueError("household voice session switch must be a boolean")
        if self.delegated_work_enabled and self.dashboard_principal_binding_id is None:
            raise ValueError("delegated work requires a configured owner binding")
        if type(self.conversation_maximum_requests) is not int or self.conversation_maximum_requests < 1:
            raise ValueError("conversation request limit must be a positive integer")

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        database_dsn = _file_only_environment_secret(
            "EVA_DATABASE_DSN", "EVA_DATABASE_DSN_FILE"
        )
        hindsight_url = _environment_value("EVA_HINDSIGHT_URL")
        namespace = _environment_value("EVA_HINDSIGHT_BANK_NAMESPACE", "eva2")
        hindsight_recall_budget = _environment_value(
            "EVA_HINDSIGHT_RECALL_BUDGET", "low"
        ).casefold()
        hindsight_recall_max_tokens = _environment_int(
            "EVA_HINDSIGHT_RECALL_MAX_TOKENS",
            2048,
            "Eva Hindsight recall configuration is invalid",
        )
        hindsight_recall_node_budget = _environment_int(
            "EVA_HINDSIGHT_RECALL_NODE_BUDGET",
            40,
            "Eva Hindsight recall configuration is invalid",
        )
        if not 2_000 <= hindsight_recall_max_tokens <= 4_000:
            raise RuntimeError(
                "EVA_HINDSIGHT_RECALL_MAX_TOKENS must be within 2000..4000"
            )
        if not 20 <= hindsight_recall_node_budget <= 100:
            raise RuntimeError(
                "EVA_HINDSIGHT_RECALL_NODE_BUDGET must be within 20..100"
            )
        identity_name = _environment_value("EVA_IDENTITY_NAME", "Eva")
        identity_description = _environment_value(
            "EVA_IDENTITY_DESCRIPTION",
            "Je bent een warme, nuchtere en capabele digitale huisgenoot.",
        )
        identity_pack_file = _environment_optional("EVA_IDENTITY_PACK_FILE")
        identity_pack_trust_file = _environment_optional("EVA_IDENTITY_PACK_TRUST_FILE")
        conversation_auth_file = _environment_value(
            "EVA_CONVERSATION_AUTH_FILE", "/run/eva/codex/auth.json"
        )
        conversation_model = _environment_value(
            "EVA_CONVERSATION_MODEL", "gpt-6-astra"
        )
        conversation_reasoning_effort = _environment_value(
            "EVA_CONVERSATION_REASONING_EFFORT", "medium"
        )
        background_tenant_id = _environment_optional("EVA_RUNTIME_TENANT_ID")
        background_installation_id = _environment_optional(
            "EVA_RUNTIME_INSTALLATION_ID"
        )
        background_principal_id = _environment_optional("EVA_RUNTIME_PRINCIPAL_ID")
        runtime_resource_budget_file = _environment_value(
            "EVA_RUNTIME_RESOURCE_BUDGET_FILE"
        )
        if (
            not runtime_resource_budget_file
            or background_tenant_id is None
            or background_installation_id is None
        ):
            raise RuntimeError(
                "Eva runtime resource budget file and scope are required"
            )
        try:
            runtime_resource_budget = RuntimeResourceBudget.from_bytes(
                read_private_runtime_file_bytes(
                    runtime_resource_budget_file,
                    setting_name="EVA_RUNTIME_RESOURCE_BUDGET_FILE",
                ),
                expected_tenant_id=background_tenant_id,
                expected_installation_id=background_installation_id,
            )
        except (RuntimeError, RuntimeResourceBudgetError) as error:
            raise RuntimeError("Eva runtime resource budget is invalid") from error
        if "EVA_HINDSIGHT_RECALL_MAX_TOKENS" not in os.environ:
            hindsight_recall_max_tokens = runtime_resource_budget.recall_context_tokens
        if "EVA_HINDSIGHT_RECALL_NODE_BUDGET" not in os.environ:
            hindsight_recall_node_budget = runtime_resource_budget.retrieval_nodes
        if (
            hindsight_recall_max_tokens != runtime_resource_budget.recall_context_tokens
            or hindsight_recall_node_budget != runtime_resource_budget.retrieval_nodes
        ):
            raise RuntimeError(
                "Eva Hindsight recall configuration differs from runtime budget"
            )
        dashboard_principal_binding_id = _environment_optional(
            "EVA_DASHBOARD_PRINCIPAL_BINDING_ID"
        )
        dashboard_tenant_id = (
            _environment_optional("EVA_DASHBOARD_TENANT_ID") or background_tenant_id
        )
        dashboard_installation_id = (
            _environment_optional("EVA_DASHBOARD_INSTALLATION_ID")
            or background_installation_id
        )
        dashboard_access_token_raw = _file_only_environment_secret(
            "EVA_DASHBOARD_ACCESS_TOKEN", "EVA_DASHBOARD_ACCESS_TOKEN_FILE"
        )
        dashboard_access_token = None
        if dashboard_access_token_raw:
            try:
                dashboard_access_token = validate_dashboard_access_token(
                    dashboard_access_token_raw
                )
            except ValueError as error:
                raise RuntimeError(
                    "EVA_DASHBOARD_ACCESS_TOKEN_FILE has an invalid length or contents"
                ) from error
        portable_restore_authority_file = _environment_optional(
            "EVA_PORTABLE_RESTORE_AUTHORITY_FILE"
        )
        if (
            portable_restore_authority_file is not None
            and not Path(portable_restore_authority_file).is_absolute()
        ):
            raise RuntimeError("EVA_PORTABLE_RESTORE_AUTHORITY_FILE must be absolute")
        if dashboard_principal_binding_id is not None:
            if background_principal_id is None:
                raise RuntimeError(
                    "Eva dashboard owner binding requires a runtime principal"
                )
            try:
                UUID(dashboard_tenant_id or "")
                UUID(dashboard_installation_id or "")
                UUID(background_principal_id)
                UUID(dashboard_principal_binding_id)
            except ValueError as error:
                raise RuntimeError("Eva dashboard owner binding is invalid") from error
            if dashboard_access_token is None:
                raise RuntimeError(
                    "Eva dashboard owner binding requires a dashboard access token file"
                )
            if portable_restore_authority_file is None:
                raise RuntimeError(
                    "Eva dashboard owner binding requires a portable restore "
                    "authority file"
                )
        cold_path_idle_seconds = _environment_float(
            "EVA_COLD_PATH_IDLE_SECONDS", 300, "EVA_COLD_PATH_IDLE_SECONDS is invalid"
        )
        personal_interest_lease_seconds = _environment_int(
            "EVA_PERSONAL_INTEREST_LEASE_SECONDS",
            90,
            "EVA_PERSONAL_INTEREST_LEASE_SECONDS is invalid",
        )
        external_provider = load_external_provider_settings()
        telegram_proactive_chat_id = _environment_optional(
            "EVA_TELEGRAM_PROACTIVE_CHAT_ID"
        )
        telegram_bot_token = (
            _file_only_environment_secret(
                "EVA_TELEGRAM_BOT_TOKEN", "EVA_TELEGRAM_BOT_TOKEN_FILE"
            )
            or None
        )
        telegram_principal_bindings_file = _environment_optional(
            "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE"
        )
        stripe_account_id = _environment_optional("EVA_STRIPE_ACCOUNT_ID")
        stripe_secret_key = (
            _file_only_environment_secret(
                "EVA_STRIPE_SECRET_KEY", "EVA_STRIPE_SECRET_KEY_FILE"
            )
            or None
        )
        stripe_webhook_secret = (
            _file_only_environment_secret(
                "EVA_STRIPE_WEBHOOK_SECRET", "EVA_STRIPE_WEBHOOK_SECRET_FILE"
            )
            or None
        )
        commerce_public_base_url = _environment_optional("EVA_COMMERCE_PUBLIC_BASE_URL")
        stripe_live_mode = _environment_bool("EVA_STRIPE_LIVE_MODE")
        file_batch_root = _environment_optional("EVA_FILE_BATCH_ROOT")
        fanout_raw = _environment_value("EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON")
        fanout_recipient_endpoints: dict[str, str] | None = None
        if fanout_raw:
            try:
                decoded_fanout = json.loads(fanout_raw)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON is invalid"
                ) from error
            if (
                not isinstance(decoded_fanout, dict)
                or not decoded_fanout
                or len(decoded_fanout) > 8
                or not all(
                    isinstance(key, str)
                    and isinstance(value, str)
                    and key.strip()
                    and value.strip()
                    for key, value in decoded_fanout.items()
                )
            ):
                raise RuntimeError("Eva fan-out endpoint configuration is invalid")
            fanout_recipient_endpoints = {
                str(key).strip(): str(value).strip()
                for key, value in decoded_fanout.items()
            }
        fanout_allow_insecure_http = _environment_bool("EVA_FANOUT_ALLOW_INSECURE_HTTP")
        durable_goal_provider = _environment_optional("EVA_DURABLE_GOAL_PROVIDER")
        durable_goal_recipients = _environment_items("EVA_DURABLE_GOAL_RECIPIENTS")
        _require_complete_configuration(
            "Eva durable goal configuration is incomplete",
            durable_goal_provider,
            durable_goal_recipients or None,
        )
        if len(durable_goal_recipients) > 8 or len(set(durable_goal_recipients)) != len(
            durable_goal_recipients
        ):
            raise RuntimeError("Eva durable goal recipients are invalid")
        if not database_dsn:
            raise RuntimeError("EVA_DATABASE_DSN is required")
        if not hindsight_url:
            raise RuntimeError("EVA_HINDSIGHT_URL is required")
        if hindsight_recall_budget not in {"low", "mid", "high"}:
            raise RuntimeError("EVA_HINDSIGHT_RECALL_BUDGET is invalid")
        if (
            not identity_name
            or not identity_description
            or not conversation_auth_file
            or not conversation_model
        ):
            raise RuntimeError("Eva identity configuration is incomplete")
        _require_complete_configuration(
            "EVA_IDENTITY_PACK_FILE and EVA_IDENTITY_PACK_TRUST_FILE must be set together",
            identity_pack_file,
            identity_pack_trust_file,
        )
        if conversation_reasoning_effort not in {"low", "medium", "high"}:
            raise RuntimeError("EVA_CONVERSATION_REASONING_EFFORT is invalid")
        if background_tenant_id is None or background_installation_id is None:
            raise RuntimeError("Eva runtime tenant and installation scope are required")
        if telegram_proactive_chat_id is not None and telegram_bot_token is None:
            raise RuntimeError("Eva proactive Telegram configuration is incomplete")
        _require_complete_configuration(
            "Eva Stripe commerce configuration is incomplete",
            stripe_account_id,
            stripe_secret_key,
            stripe_webhook_secret,
            commerce_public_base_url,
        )
        if stripe_account_id is not None and (
            background_tenant_id is None or background_installation_id is None
        ):
            raise RuntimeError("Eva Stripe webhook scope is incomplete")
        if not 1 <= cold_path_idle_seconds <= 86_400:
            raise RuntimeError("Eva cold-path idle period is outside 1..86400 seconds")
        if not 10 <= personal_interest_lease_seconds <= 600:
            raise RuntimeError("Eva personal-interest lease is outside 10..600 seconds")
        return cls(
            database_dsn=database_dsn,
            hindsight_url=hindsight_url,
            hindsight_bank_namespace=namespace,
            hindsight_recall_budget=hindsight_recall_budget,
            hindsight_recall_max_tokens=hindsight_recall_max_tokens,
            hindsight_recall_node_budget=hindsight_recall_node_budget,
            identity_name=identity_name,
            identity_description=identity_description,
            identity_pack_file=identity_pack_file,
            identity_pack_trust_file=identity_pack_trust_file,
            conversation_auth_file=conversation_auth_file,
            conversation_model=conversation_model,
            conversation_reasoning_effort=conversation_reasoning_effort,
            conversation_contact_model=_environment_optional("EVA_CONVERSATION_CONTACT_MODEL"),
            conversation_contact_reasoning_effort=_environment_value(
                "EVA_CONVERSATION_CONTACT_REASONING_EFFORT", "low"
            ),
            conversation_maximum_requests=_environment_int(
                "EVA_CONVERSATION_MAXIMUM_REQUESTS", 32, "Eva conversation request limit is invalid",
            ),
            runtime_resource_budget=runtime_resource_budget,
            background_work_enabled=_environment_bool("EVA_BACKGROUND_WORK_ENABLED", True),
            delegated_work_enabled=_environment_bool("EVA_DELEGATED_WORK_ENABLED", False),
            delegated_research_enabled=_environment_bool("EVA_DELEGATED_RESEARCH_ENABLED", False),
            delegated_digital_enabled=_environment_bool("EVA_DELEGATED_DIGITAL_ENABLED", False),
            delegated_result_delivery_enabled=_environment_bool("EVA_DELEGATED_RESULT_DELIVERY_ENABLED", False),
            background_tenant_id=background_tenant_id,
            background_installation_id=background_installation_id,
            background_principal_id=background_principal_id,
            dashboard_tenant_id=dashboard_tenant_id,
            dashboard_installation_id=dashboard_installation_id,
            dashboard_principal_binding_id=dashboard_principal_binding_id,
            dashboard_access_token=dashboard_access_token,
            dashboard_voice_policy_file=_environment_optional("EVA_DASHBOARD_VOICE_POLICY_FILE"),
            household_voice_policy_file=_environment_optional("EVA_HOUSEHOLD_VOICE_POLICY_FILE"),
            household_voice_token_file=_environment_optional("EVA_HOUSEHOLD_VOICE_TOKEN_FILE"),
            household_voice_session_enabled=_environment_bool("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", False),
            household_work_config_file=_environment_optional("EVA_HOUSEHOLD_WORK_CONFIG_FILE"),
            portable_restore_authority_file=portable_restore_authority_file,
            cold_path_idle_seconds=cold_path_idle_seconds,
            personal_interest_lease_seconds=personal_interest_lease_seconds,
            external_provider=external_provider,
            telegram_proactive_chat_id=telegram_proactive_chat_id,
            telegram_bot_token=telegram_bot_token,
            telegram_principal_bindings_file=telegram_principal_bindings_file,
            stripe_account_id=stripe_account_id,
            stripe_secret_key=stripe_secret_key,
            stripe_webhook_secret=stripe_webhook_secret,
            commerce_public_base_url=commerce_public_base_url,
            stripe_live_mode=stripe_live_mode,
            file_batch_root=file_batch_root,
            fanout_recipient_endpoints=fanout_recipient_endpoints,
            fanout_allow_insecure_http=fanout_allow_insecure_http,
            durable_goal_provider=durable_goal_provider,
            durable_goal_recipients=durable_goal_recipients,
        )

