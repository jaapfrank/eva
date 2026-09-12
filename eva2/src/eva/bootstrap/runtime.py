"""Executable Living Core composition with Hindsight-owned semantic memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from eva.adapters.api.app import RuntimeHealth, create_app
from eva.adapters.api.browser_session import browser_session_from_environment
from eva.adapters.connectors.telegram import TelegramProactiveDelivery
from eva.adapters.connectors.fanout_http import HttpFanoutDeliverySink
from eva.adapters.connectors.local_files import LocalMarkdownBatchAdapter
from eva.adapters.connectors.validated_edits import LocalValidatedTextEditAdapter
from eva.adapters.connectors.repository_improvements import (
    LocalRepositoryImprovementAdapter,
)
from eva.adapters.connectors.stripe import (
    StripePublicationAdapter,
    StripePublicationConfig,
)
from eva.adapters.connectors.stripe_webhook import (
    StripeWebhookConfig,
    StripeWebhookVerifier,
)
from eva.adapters.postgres.conversation_actions import (
    PostgresConversationActions,
    PostgresPersonalInterestStore,
    PostgresPersonalProductReleaseStore,
    PostgresPersonalPrototypeStore,
)
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.runtime_resource_usage import (
    PostgresDailyReflectionBudget,
    PostgresMemoryStorageBudget,
)
from eva.adapters.postgres.people_memory_overview import (
    PostgresPeopleMemoryOverview,
)
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.postgres.telegram_group_consent import (
    PostgresTelegramGroupConsentStore,
)
from eva.adapters.postgres.telegram_group_observation import (
    PostgresTelegramGroupObservationStore,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    external_timeline_scope,
)
from eva.bootstrap.dashboard_voice import build_dashboard_voice
from eva.bootstrap.action_chain_effect_controls import (
    DashboardActionChainEffectController,
    DashboardActionChainEffectStop,
)
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.adapters.postgres.commerce import (
    PostgresPaidOrderStore,
    PostgresProductPublicationStore,
)
from eva.adapters.projection.hindsight import (
    ActiveHouseholdConsentResolver,
    HindsightMemoryEngine,
    HindsightProjectionConfig,
)
from eva.adapters.objects.identity_verifier import load_current_identity_pack

if TYPE_CHECKING:
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.cancellation import WorkInterrupted, check_cancellation
from eva.application.conversation import (
    ConversationConflict,
    ConversationTurn,
    ConversationStopped,
    ConversationExecutionLimitReached,
    ConversationModelUsage,
    GroundedReply,
    IdentityProfile,
    LivingConversation,
    RecentConversationMessage,
)
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.providers.runtime import (
    ExternalProviderSettings,
    build_external_provider_bundle,
    load_external_provider_settings,
)
from eva.application.memory import (
    BudgetedConversationMemoryWriter,
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    PresenceMemory,
    RecallCurrentFacts,
    SemanticMemoryProviderError,
    SemanticMemoryProviderPort,
)
from eva.application.memory_audience import MemoryAudience
from eva.application.people_memory_overview import (
    PeopleMemoryOverview,
    PeopleMemoryOverviewPort,
)
from eva.contracts.workboard import WorkBoard
from eva.contracts.runtime_budget import (
    RuntimeResourceBudget,
    RuntimeResourceBudgetError,
)
from eva.application.telegram_group_consent import TelegramGroupConsentStorePort
from eva.application.telegram_canary import (
    TelegramCanaryAdmission,
    TelegramCanaryBridge,
)
from eva.application.telegram_ingress import TelegramPrincipalBindings
from eva.application.durable_goals import DurableGoalSpec, DurableGoalState
from eva.application.memory_background import HindsightColdPathWorker
from eva.application.personal_interests import (
    PersonalInterestWorker,
    PersonalProductReleaseArtifact,
    PersonalProductReleaseWorker,
    PersonalPrototypeArtifact,
    PersonalPrototypeWorker,
)
from eva.application.research import ResearchArtifact
from eva.application.commerce import (
    CommerceError,
    DigitalFulfillmentDownload,
    ProductPublicationApproval,
    ProductPublicationPlan,
    ProductPublicationReceipt,
    publication_plan,
)
from eva.application.free_will import (
    ProactiveExpressionClaim,
    ProactiveExpressionWorker,
    SensoryDecisionWorker,
)
from eva.application.sensory import SensoryIngestionWorker, SensoryEventRevalidatorPort
from eva.adapters.credentials.runtime_files import (
    environment_secret,
    read_private_runtime_file_bytes,
)
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands
from eva.bootstrap.telegram_ingress import load_principal_bindings
from eva.adapters.postgres.telegram_pairing import PostgresPrivateTelegramRecipient
from eva.adapters.api.dashboard_access import validate_dashboard_access_token
from eva.operations.portable_restore_authority import PrivateRestoreAuthorityStore


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
    conversation_model: str = "gpt-5.4-mini"
    conversation_maximum_requests: int = 32
    conversation_reasoning_effort: str = "medium"
    runtime_resource_budget: RuntimeResourceBudget | None = None
    background_work_enabled: bool = True
    delegated_work_enabled: bool = False
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
        if type(self.background_work_enabled) is not bool:
            raise ValueError("background work switch must be a boolean")
        if type(self.delegated_work_enabled) is not bool:
            raise ValueError("delegated work switch must be a boolean")
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
            "EVA_CONVERSATION_MODEL", "gpt-5.4-mini"
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
            conversation_maximum_requests=_environment_int(
                "EVA_CONVERSATION_MAXIMUM_REQUESTS", 32, "Eva conversation request limit is invalid",
            ),
            runtime_resource_budget=runtime_resource_budget,
            background_work_enabled=_environment_bool("EVA_BACKGROUND_WORK_ENABLED", True),
            delegated_work_enabled=_environment_bool("EVA_DELEGATED_WORK_ENABLED", False),
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


class PostgresHindsightMemoryRuntime:
    """Operational PostgreSQL readiness plus Hindsight as the only memory engine."""

    def __init__(
        self,
        engine: Engine,
        memory: SemanticMemoryProviderPort,
        conversation: LivingConversation,
        cold_path: HindsightColdPathWorker,
        prototype_store: PostgresPersonalPrototypeStore,
        release_store: PostgresPersonalProductReleaseStore,
        conversation_actions: PostgresConversationActions,
        sensory_worker: SensoryIngestionWorker | None = None,
        sensory_store: PostgresSensoryEventStore | None = None,
        conversation_model: PydanticConversationModel | None = None,
        stripe_publication: StripePublicationAdapter | None = None,
        stripe_webhook: StripeWebhookVerifier | None = None,
        commerce_tenant_id: str | None = None,
        commerce_installation_id: str | None = None,
        runtime_tenant_id: str | None = None,
        runtime_installation_id: str | None = None,
        group_consent: TelegramGroupConsentStorePort | None = None,
        household_consent: ActiveHouseholdConsentResolver | None = None,
        telegram_canary: TelegramCanaryBridge | None = None,
        people_memory_overview: PeopleMemoryOverviewPort | None = None,
        delegated_work_store: PostgresDelegatedWorkStore | None = None,
        telegram_principal_bindings: TelegramPrincipalBindings | None = None,
        delegated_work_commands: DelegatedWorkConversationCommands | None = None,
        dashboard_effect_controller: DashboardActionChainEffectController | None = None,
        dashboard_owner_principal_id: UUID | None = None,
        dashboard_principal_binding_id: UUID | None = None,
        dashboard_authority_scope: TimelineScope | None = None,
        telegram_principal_bindings_loader: Callable[[], TelegramPrincipalBindings] | None = None,
        memory_storage_budget: PostgresMemoryStorageBudget | None = None,
        conversation_maximum_requests: int = 32,
        background_work_enabled: bool = True,
    ) -> None:
        self._engine = engine
        self._memory = memory
        if type(conversation_maximum_requests) is not int or conversation_maximum_requests < 1:
            raise ValueError("conversation request limit must be a positive integer")
        self._conversation_maximum_requests = conversation_maximum_requests
        self._conversation = conversation
        self._cold_path = cold_path
        if type(background_work_enabled) is not bool:
            raise ValueError("background work switch must be a boolean")
        self._background_work_enabled = background_work_enabled
        self._prototype_store = prototype_store
        self._release_store = release_store
        self._conversation_actions = conversation_actions
        self._sensory_worker = sensory_worker
        self._sensory_store = sensory_store
        self._conversation_model = conversation_model
        self._stripe_publication = stripe_publication
        self._stripe_webhook = stripe_webhook
        self._commerce_tenant_id = commerce_tenant_id
        self._commerce_installation_id = commerce_installation_id
        if (runtime_tenant_id is None) != (runtime_installation_id is None):
            raise ValueError("conversation runtime scope must be complete")
        self._runtime_tenant_id = runtime_tenant_id
        self._runtime_installation_id = runtime_installation_id
        self._group_consent = group_consent
        self._household_consent = household_consent
        self._telegram_canary = telegram_canary
        self._people_memory_overview = people_memory_overview
        if (delegated_work_store is None) != (dashboard_owner_principal_id is None):
            raise ValueError("delegated work dashboard owner is incomplete")
        if (delegated_work_store is None) != (dashboard_principal_binding_id is None):
            raise ValueError("delegated work dashboard binding is incomplete")
        self._delegated_work_store = delegated_work_store
        self._telegram_principal_bindings = telegram_principal_bindings
        self._delegated_work_commands = delegated_work_commands
        self._dashboard_effect_controller = dashboard_effect_controller
        self._dashboard_owner_principal_id = dashboard_owner_principal_id
        self._dashboard_principal_binding_id = dashboard_principal_binding_id
        self._dashboard_authority_scope = dashboard_authority_scope
        self._telegram_principal_bindings_loader = telegram_principal_bindings_loader
        self._memory_storage_budget = memory_storage_budget
        self._memory_writer = BudgetedConversationMemoryWriter(
            memory, memory_storage_budget
        )

    def admit_telegram_canary(
        self,
        *,
        telegram_user_id: int,
        chat_id: str,
        text: str,
        operation_id: str,
        now: datetime,
    ) -> TelegramCanaryAdmission:
        if self._telegram_canary is None:
            raise RuntimeError("Telegram canary bridge is unavailable")
        return self._telegram_canary.admit(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            text=text,
            operation_id=operation_id,
            now=now,
        )

    def admit_durable_goal(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal: DurableGoalSpec,
    ) -> DurableGoalState:
        self._require_runtime_scope(tenant_id, installation_id)
        store = PostgresDurableGoalStore(
            self._engine, tenant_id=tenant_id, installation_id=installation_id
        )
        store.admit(goal)
        store.schedule_wake(goal)
        state = store.state(goal.goal_id, goal.owner_id)
        if state is None:
            raise RuntimeError("durable goal was not admitted")
        return state

    def cancel_durable_goal(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal_id: str,
        owner_id: str,
        reason: str,
        occurred_at: datetime,
    ) -> DurableGoalState:
        self._require_runtime_scope(tenant_id, installation_id)
        store = PostgresDurableGoalStore(
            self._engine, tenant_id=tenant_id, installation_id=installation_id
        )
        store.cancel(goal_id, owner_id, reason, occurred_at)
        state = store.state(goal_id, owner_id)
        if state is None:
            raise RuntimeError("durable goal disappeared after cancellation")
        return state

    def durable_goal_state(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal_id: str,
        owner_id: str,
    ) -> DurableGoalState | None:
        self._require_runtime_scope(tenant_id, installation_id)
        return PostgresDurableGoalStore(
            self._engine, tenant_id=tenant_id, installation_id=installation_id
        ).state(goal_id, owner_id)

    def _require_runtime_scope(self, tenant_id: str, installation_id: str) -> None:
        if self._runtime_tenant_id is not None and (
            tenant_id != self._runtime_tenant_id
            or installation_id != self._runtime_installation_id
        ):
            raise ValueError("request crossed its runtime scope")

    def start_background_work(self) -> None:
        # A staged reserve may serve readiness checks without observing the home
        # or consuming the copied outbox and autonomous work queues.
        if not self._background_work_enabled:
            return
        self._cold_path.start()
        if self._sensory_worker is not None:
            self._sensory_worker.start()

    def stop_background_work(self) -> None:
        if not self._background_work_enabled:
            return
        if self._sensory_worker is not None:
            self._sensory_worker.stop()
        self._cold_path.stop()

    def claim_proactive_expression(self) -> ProactiveExpressionClaim | None:
        if self._sensory_store is None:
            return None
        return self._sensory_store.claim_next_expression(
            now=datetime.now(tz=UTC).replace(microsecond=0)
        )

    def acknowledge_proactive_expression(
        self,
        *,
        thread_id: str,
        revision: int,
        channel: str,
        provider_message_id: str,
    ) -> None:
        if self._sensory_store is None:
            raise RuntimeError("proactive expression store is unavailable")
        self._sensory_store.acknowledge_expression_receipt(
            thread_id=thread_id,
            revision=revision,
            channel=channel,
            provider_message_id=provider_message_id,
            now=datetime.now(tz=UTC).replace(microsecond=0),
        )

    def health(self) -> RuntimeHealth:
        try:
            with self._engine.connect() as connection:
                if connection.scalar(sa.text("SELECT 1")) != 1:
                    raise RuntimeError("PostgreSQL readiness query differed")
        except Exception as error:
            return RuntimeHealth(False, "unavailable", "unknown", str(error), "unknown")
        try:
            self._memory.health()
        except SemanticMemoryProviderError as error:
            return RuntimeHealth(False, "ready", "unavailable", str(error), "unknown")
        if self._conversation_model is not None:
            model_status = self._conversation_model.credential_status()
            if not model_status.ready:
                return RuntimeHealth(
                    False,
                    "ready",
                    "ready",
                    f"conversation model credentials are {model_status.state}",
                    f"credentials_{model_status.state}",
                )
        return RuntimeHealth(True, "ready", "ready", None, "ready")

    def dashboard_people_memory(self) -> PeopleMemoryOverview:
        """Return the privacy-safe cockpit projection for this fixed runtime scope."""

        if self._people_memory_overview is None:
            raise RuntimeError("people and memory cockpit projection is unavailable")
        try:
            self._memory.health()
            hindsight_state = "ready"
        except Exception:
            hindsight_state = "attention"
        return self._people_memory_overview.snapshot(
            now=datetime.now(tz=UTC).replace(microsecond=0),
            hindsight_state=hindsight_state,
        )

    def dashboard_workboard(self) -> WorkBoard:
        """Return only the board bound to this configured local operator."""

        if (
            self._delegated_work_store is None
            or self._dashboard_owner_principal_id is None
            or self._dashboard_principal_binding_id is None
        ):
            raise RuntimeError("delegated workboard projection is unavailable")
        return self._delegated_work_store.workboard(
            owner_principal_id=self._dashboard_owner_principal_id,
            principal_binding_id=self._dashboard_principal_binding_id,
            now=datetime.now(tz=UTC).replace(microsecond=0),
        )

    def _owner_memory_sources(self):
        """Reload trusted bindings for every owner memory read or command."""
        from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources

        if (self._runtime_tenant_id is None or self._runtime_installation_id is None
                or self._dashboard_owner_principal_id is None
                or self._dashboard_principal_binding_id is None
                or self._dashboard_authority_scope is None):
            raise RuntimeError("owner memory sources unavailable")
        bindings = (self._telegram_principal_bindings if self._telegram_principal_bindings_loader is None
                    else self._telegram_principal_bindings_loader())
        return PostgresOwnerMemorySources(
            self._engine, tenant_id=self._runtime_tenant_id,
            installation_id=self._runtime_installation_id,
            owner=self._dashboard_owner_principal_id,
            binding=self._dashboard_principal_binding_id,
            authority_scope=self._dashboard_authority_scope,
            telegram_bindings=bindings,
        )

    def dashboard_memory_sources(self, *, limit: int = 20, before: str | None = None):
        return self._owner_memory_sources().page(
            now=datetime.now(UTC).replace(microsecond=0), limit=limit, before=before)

    def dashboard_memory_source(self, source_id: str):
        doc, state = self._owner_memory_sources().document(
            source_id, now=datetime.now(UTC).replace(microsecond=0))
        return {
            "contract": "EvaOwnerMemorySource/v1", "source_id": source_id,
            "content": doc.content, "occurred_at": doc.occurred_at,
            "retention_state": state,
            "origin": "Telegram" if doc.conversation_id.startswith("telegram:") else "Eigen gesprek",
        }

    def dashboard_forget_memory_source(self, source_id: str):
        now = datetime.now(UTC).replace(microsecond=0)
        doc, state = self._owner_memory_sources().document(source_id, now=now)
        if state != "retained":
            raise ConversationConflict("Deze bron is nog niet verwerkt. Probeer wissen na verwerking opnieuw.")
        # Accept only a proven raw exchange binding; an arbitrary document/source
        # reference must never authorize removal of a different conversation.
        if doc.document_id != f"{doc.conversation_id}:exchange:{doc.source_ref}":
            raise ConversationConflict("Deze bron heeft geen ondersteunde gesprekskoppeling.")
        erased = self._cold_path.erase_document(principal_id=doc.principal_id,
            document_id=doc.document_id, source_message_id=doc.source_ref, now=now)
        return {"contract": "EvaOwnerMemoryChange/v1", "source_id": source_id,
                "state": "erased" if erased else "erasure_requested"}

    def dashboard_correct_memory_source(self, source_id: str, text: str):
        from uuid import uuid4
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("memory correction is invalid")
        now = datetime.now(UTC).replace(microsecond=0)
        doc, _ = self._owner_memory_sources().document(source_id, now=now)
        # A correction is new direct owner testimony, preserving the old source
        # as history. Reuse the normal durable conversation and retention route,
        # including its erasure and recovery bindings; no second memory store.
        turn = ConversationTurn(tenant_id=doc.tenant_id, installation_id=doc.installation_id,
            principal_id=doc.principal_id, conversation_id=doc.conversation_id,
            message_id=f"dashboard-memory:{uuid4()}",
            occurred_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), audience=doc.audience,
            project_id=doc.project_id,
            text=f"Correctie op eerdere informatie. Voortaan geldt: {text.strip()}")
        journal = self._journal(turn)
        journal.admit(turn)
        reply = GroundedReply(conversation_id=turn.conversation_id, message_id=turn.message_id,
            text="Je correctie is ontvangen voor verwerking in het geheugen.",
            generated_at=turn.occurred_at, provider="owner-memory-controls",
            evidence_memory_ids=())
        reply = journal.commit_reply(turn, reply)
        self.after_response(turn, reply)
        if journal.load(turn).phase != "memory_queued":
            raise ConversationConflict("De correctie is niet in het geheugen opgenomen; verwerking is geblokkeerd.")
        return {"contract": "EvaOwnerMemoryChange/v1", "source_id": source_id,
                "state": "correction_queued"}

    def _owner_initiatives(self):
        from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
        if (self._runtime_tenant_id is None or self._runtime_installation_id is None
                or self._dashboard_owner_principal_id is None
                or self._dashboard_principal_binding_id is None
                or self._dashboard_authority_scope is None):
            raise RuntimeError("owner initiatives unavailable")
        return PostgresOwnerInitiatives(self._engine,
            tenant_id=self._runtime_tenant_id, installation_id=self._runtime_installation_id,
            owner=self._dashboard_owner_principal_id,
            binding=self._dashboard_principal_binding_id,
            authority_scope=self._dashboard_authority_scope)

    def _initiative_authority(self):
        from functools import partial
        from eva.adapters.credentials.runtime_files import read_runtime_secret_file
        from eva.adapters.postgres.initiative_execution import PostgresInitiativeAuthority
        path = os.environ.get("EVA_INITIATIVE_MANDATE_FILE")
        if not path:
            base = os.environ.get("EVA_MCP_AUTONOMY_MANDATE_FILE")
            if not base:
                raise RuntimeError("initiative mandate unavailable")
            path = str(Path(base).with_name("initiative-mandate.json"))
        return PostgresInitiativeAuthority(self._owner_initiatives(),
            partial(read_runtime_secret_file, path, setting_name="EVA_INITIATIVE_MANDATE_FILE"))

    def execute_native_initiative(self, claim):
        from eva.application.initiative_execution import initiative_execution
        guard, turn = self._initiative_authority().bind(claim)
        with initiative_execution(guard):
            # Same durable journal, actual effects and model limits as conversations.
            # Do not invoke after_response: this is not a direct user assertion.
            return self.respond(turn)

    def dashboard_revoke_initiative_mandate(self):
        return self._initiative_authority().revoke()

    def dashboard_revise_initiative_mandate(self, expected_sha256, candidate_sha256):
        return self._initiative_authority().admit_revision(expected_sha256, candidate_sha256)

    def dashboard_initiative_feedback(self, intent_id, feedback):
        return self._owner_initiatives().feedback(intent_id, feedback, now=datetime.now(UTC).replace(microsecond=0))

    def dashboard_initiatives(self):
        return self._owner_initiatives().page(now=datetime.now(tz=UTC).replace(microsecond=0))

    def dashboard_initiative_artifact(self, intent_id: str):
        return self._owner_initiatives().artifact(intent_id,
            now=datetime.now(tz=UTC).replace(microsecond=0))

    def dashboard_stop_initiative(self, intent_id: str):
        return self._owner_initiatives().stop(intent_id,
            now=datetime.now(tz=UTC).replace(microsecond=0))

    def dashboard_stop_effect(
        self, effect_intent_id: UUID
    ) -> DashboardActionChainEffectStop:
        """Submit one owner-bound stop command through the durable effect ledger."""

        if self._dashboard_effect_controller is None:
            raise RuntimeError("effect stop control is unavailable")
        return self._dashboard_effect_controller.stop(effect_intent_id)

    def dashboard_stop_conversation(self) -> int:
        """Stop unfinished turns in the server-bound owner's dashboard chat."""
        if (self._runtime_tenant_id is None or self._runtime_installation_id is None
                or self._dashboard_owner_principal_id is None
                or self._dashboard_principal_binding_id is None):
            raise RuntimeError("owner conversation stop is unavailable")
        journal = PostgresConversationJournal(self._engine, external_timeline_scope(
            self._runtime_tenant_id, self._runtime_installation_id,
            "conversation stop scope is required",
        ))
        return journal.request_conversation_stop(
            principal_id=str(self._dashboard_owner_principal_id),
            conversation_id=f"dashboard:{self._dashboard_principal_binding_id}",
        )

    def dashboard_turn(self, text: str) -> tuple[ConversationTurn, GroundedReply]:
        """Create one private owner turn without trusting browser-supplied scope.

        The dashboard is an owner interface, not a connector.  Its request never
        supplies a tenant, installation, person, conversation, or message ID;
        all of those are bound here to the configured runtime owner.
        """

        if (
            self._runtime_tenant_id is None
            or self._runtime_installation_id is None
            or self._dashboard_owner_principal_id is None
            or self._dashboard_principal_binding_id is None
        ):
            raise RuntimeError("owner conversation is unavailable")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > 4_096
        ):
            raise ValueError("owner conversation text is invalid")
        principal_id = str(self._dashboard_owner_principal_id)
        turn = ConversationTurn.private(
            self._runtime_tenant_id,
            self._runtime_installation_id,
            principal_id,
            f"dashboard:{self._dashboard_principal_binding_id}",
            f"dashboard:{uuid4()}",
            text.strip(),
            datetime.now(tz=UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return turn, self.respond(turn)

    def dashboard_erase_conversation_message(self, message_id: str) -> bool:
        """Erase one owner-dashboard exchange without accepting browser scope."""

        if (
            self._runtime_tenant_id is None
            or self._runtime_installation_id is None
            or self._dashboard_owner_principal_id is None
            or self._dashboard_principal_binding_id is None
        ):
            raise RuntimeError("owner conversation erasure is unavailable")
        prefix = "dashboard:"
        if not isinstance(message_id, str) or not message_id.startswith(prefix):
            raise ValueError("owner conversation message is invalid")
        try:
            UUID(message_id.removeprefix(prefix))
        except ValueError as error:
            raise ValueError("owner conversation message is invalid") from error
        conversation_id = f"dashboard:{self._dashboard_principal_binding_id}"
        return self._cold_path.erase_document(
            principal_id=str(self._dashboard_owner_principal_id),
            document_id=f"{conversation_id}:exchange:{message_id}",
            source_message_id=message_id,
            now=datetime.now(tz=UTC).replace(microsecond=0),
        )

    def recall(self, query: RecallCurrentFacts) -> PresenceMemory:
        return self._memory.recall_for_presence(query)

    def retain(self, document: ConversationMemoryDocument) -> MemoryRetentionReceipt:
        now = datetime.now(tz=UTC).replace(microsecond=0)
        return self._memory_writer.retain_bounded(document, now=now)

    def admit_turn_audience(self, turn: ConversationTurn) -> None:
        """Revalidate a connector's group grant at the core boundary."""

        self._require_runtime_scope(turn.tenant_id, turn.installation_id)
        audience: MemoryAudience = turn.audience
        if audience.kind == "private_person":
            return
        if audience.kind == "household":
            resolver = getattr(self, "_household_consent", None)
            if resolver is None:
                raise ValueError("household turn has no mandate verifier")
            try:
                active = resolver.active_reference(
                    principal_id=turn.principal_id, household_id=audience.household_id,
                    now=datetime.now(tz=UTC).replace(microsecond=0),
                )
            except Exception as error:
                raise ValueError("household turn mandate could not be verified") from error
            if active != audience.household_consent_ref:
                raise ValueError("household turn mandate is not active and exact")
            return
        if self._group_consent is None:
            raise ValueError("Telegram group turn has no consent verifier")
        assert audience.group_chat_id is not None
        assert audience.consent_ref is not None
        try:
            active = self._group_consent.active_reference(
                principal_id=turn.principal_id,
                group_chat_id=audience.group_chat_id,
                now=datetime.now(tz=UTC).replace(microsecond=0),
            )
        except Exception as error:
            raise ValueError(
                "Telegram group turn consent could not be verified"
            ) from error
        if active != audience.consent_ref:
            raise ValueError("Telegram group turn consent is not active and exact")

    def admit_voice_turn(self, turn: ConversationTurn) -> ConversationTurn:
        """Resolve the first durable receive time for an owner voice message."""
        check_cancellation()
        self.admit_turn_audience(turn)
        return self._journal(turn).admit_voice_ingress(turn)

    def respond(
        self,
        turn: ConversationTurn,
        recent_messages: tuple[RecentConversationMessage, ...] = (),
    ) -> GroundedReply:
        from eva.application.initiative_execution import require_initiative_context
        require_initiative_context(turn)
        check_cancellation()
        self.admit_turn_audience(turn)
        journal = self._journal(turn)
        with journal.execution(turn):
            check_cancellation()
            journal.admit(turn)
            committed = journal.committed_reply(turn)
            if committed is not None:
                return committed
            from eva.application.voice_context import current_voice_context
            voice = current_voice_context()
            if voice is None and turn.text.strip().casefold() in {"/stop", "stop deze opdracht", "stop deze opdracht."}:
                count = journal.request_conversation_stop(
                    principal_id=turn.principal_id, conversation_id=turn.conversation_id,
                    request_message_id=turn.message_id,
                )
                return journal.commit_reply(turn, GroundedReply(
                    conversation_id=turn.conversation_id, message_id=turn.message_id,
                    text=("Ik start geen verdere stappen voor de lopende opdracht. Een stap die al in behandeling is, kan nog afronden."
                          if count else "Er is in dit gesprek geen lopende opdracht om te stoppen."),
                    evidence_memory_ids=(), generated_at=turn.occurred_at,
                    provider="runtime:conversation-stop-command",
                ))
            durable_recent = journal.recent(turn, exclude_message_id=turn.message_id)
            recent = (voice.recent if voice is not None
                      else tuple((*recent_messages, *durable_recent)[-12:]))
            latest_usage = ConversationModelUsage.unmeasured()

            def checkpoint(current, usage):
                nonlocal latest_usage
                if isinstance(usage, ConversationModelUsage):
                    latest_usage = usage
                check_cancellation()
                require_initiative_context(current)
                self.admit_turn_audience(current)
                journal.ensure_running(current)

            try:
                candidate = self._conversation.respond(
                    turn, recent, before_step=checkpoint, model_journal=journal,
                )
                checkpoint(turn, candidate.model_usage)
            except ConversationExecutionLimitReached:
                candidate = GroundedReply(
                    conversation_id=turn.conversation_id, message_id=turn.message_id,
                    text="Deze opdracht is nog niet af. Ik heb de ingestelde uitvoergrens bereikt en start daarom geen nieuwe stappen.",
                    evidence_memory_ids=(), generated_at=turn.occurred_at,
                    provider="runtime:conversation-limit", model_usage=journal.model_usage(turn),
                )
            except (ConversationStopped, WorkInterrupted):
                durable_usage = journal.model_usage(turn)
                if durable_usage.measurement != "unmeasured":
                    latest_usage = durable_usage
                candidate = GroundedReply(
                    conversation_id=turn.conversation_id,
                    message_id=turn.message_id,
                    text="Ik start geen verdere stappen voor deze opdracht. Een stap die al in behandeling is, kan nog afronden.",
                    evidence_memory_ids=(), generated_at=turn.occurred_at,
                    provider="runtime:conversation-stop", model_usage=latest_usage,
                )
            return journal.commit_reply(turn, candidate)

    def request_turn_stop(self, turn: ConversationTurn, *, request_message_id: str | None = None) -> str:
        self.admit_turn_audience(turn)
        return self._journal(turn).request_turn_stop(turn, request_message_id=request_message_id)

    def respond_telegram_delegated_work(
        self,
        turn: ConversationTurn,
        *,
        telegram_user_id: int,
        chat_id: str,
    ) -> GroundedReply | None:
        """Handle owner work commands on Eva1's existing private bridge."""

        try:
            bindings = (self._telegram_principal_bindings_loader()
                        if self._telegram_principal_bindings_loader is not None
                        else self._telegram_principal_bindings)
        except (OSError, ValueError, RuntimeError):
            return None
        commands = self._delegated_work_commands
        if (
            bindings is None
            or commands is None
            or turn.audience.kind != "private_person"
        ):
            return None
        authority_principal = bindings.resolve(telegram_user_id, chat_id)
        authority_binding = bindings.resolve_binding_id(telegram_user_id, chat_id)
        if authority_principal is None or authority_binding is None:
            return None
        if not commands.matches(turn, principal_binding_id=authority_binding):
            return None
        commands.validate_authority(turn)
        journal = self._journal(turn)
        journal.admit(turn)
        committed = journal.committed_reply(turn)
        if committed is not None:
            return committed
        reply = commands.handle(turn, principal_binding_id=authority_binding)
        if reply is None:
            raise RuntimeError("delegated owner command disappeared after admission")
        return journal.commit_reply(turn, reply)

    def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None:
        """Queue semantic retention only after the reply is durably committed."""

        if turn.conversation_id.startswith("initiative-task:"):
            # Native proposals and model results are not direct owner testimony.
            return
        if turn.audience.kind == "household":
            self.admit_turn_audience(turn)

        # The durable journal already owns the complete user/assistant exchange.
        # Hindsight may learn semantic facts from direct user testimony only: an
        # assistant reply is model output and must never inherit direct_user trust.
        # Keeping that authority boundary here prevents a speculative reply from
        # becoming a self-confirming long-term memory on the following turn.
        _ = reply
        actions = getattr(self, "_conversation_actions", None)
        completed_action = None if actions is None else actions.pending(turn)
        grounded_correction = (
            completed_action
            if completed_action is not None
            and completed_action.call.name == "record_observation_identity_correction"
            else None
        )
        if grounded_correction is None:
            document_id = f"{turn.conversation_id}:exchange:{turn.message_id}"
            content = f"Gebruiker (directe bron): {turn.text}"
            evidence_ref = (
                f"{turn.conversation_id}:evidence:{turn.message_id}"
                if turn.audience.kind in {"telegram_group", "household"}
                else f"conversation-reply:{turn.message_id}"
            )
            privacy_class = "internal"
        else:
            binding = turn.observation_binding
            if binding is None:
                raise ConversationConflict(
                    "durable identity correction lacks its observation binding"
                )
            corrected_name = grounded_correction.output.get("corrected_name")
            if not isinstance(corrected_name, str) or not corrected_name:
                raise ConversationConflict(
                    "durable identity correction lacks its corrected name"
                )
            document_id = (
                f"{turn.conversation_id}:observation-identity:{turn.message_id}"
            )
            content = (
                "Expliciete, door de gebruiker gegeven identiteitscorrectie bij "
                f"fotowaarneming {binding.observation_id}, media_sha256 "
                f"{binding.media_sha256}, subject_region_sha256 "
                f"{binding.subject_region_sha256}: de persoon in exact deze gebonden "
                f"onderwerpregio is {corrected_name}. Generaliseer dit niet naar andere "
                "foto's of onderwerpregio's."
            )
            evidence_ref = f"observation-binding:{binding.binding_sha256}"
            privacy_class = "confidential"
        document = ConversationMemoryDocument(
            tenant_id=turn.tenant_id,
            installation_id=turn.installation_id,
            principal_id=turn.principal_id,
            conversation_id=turn.conversation_id,
            document_id=document_id,
            content=content,
            occurred_at=turn.occurred_at,
            source_ref=turn.message_id,
            evidence_ref=evidence_ref,
            privacy_class=privacy_class,
            trust="direct_user",
            audience=turn.audience,
            project_id=turn.project_id,
        )
        # ``submit`` commits the separate outbox thread before returning. The
        # queue wake-up and Hindsight call remain asynchronous, but a crash in
        # between cannot strand the source document anymore.
        admission = self._cold_path.submit(document)
        journal = self._journal(turn)
        admitted = None if admission is None else getattr(admission, "admitted", None)
        if admitted is False:
            reason = getattr(admission, "reason", None)
            if not isinstance(reason, str) or not reason:
                raise ConversationConflict(
                    "memory storage budget returned an invalid refusal"
                )
            journal.memory_skipped(
                turn,
                journal.load(turn),
                operation_document_id=document_id,
                reason=reason,
            )
            return
        if admitted is not None and admitted is not True:
            raise ConversationConflict("memory storage budget decision is invalid")
        journal.memory_queued(
            turn,
            journal.load(turn),
            operation_document_id=document_id,
        )

    def latest_personal_prototype(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> PersonalPrototypeArtifact | None:
        return self._prototype_store.latest(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            now=datetime.now(tz=UTC).replace(microsecond=0),
        )

    def latest_personal_product_release(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> PersonalProductReleaseArtifact | None:
        return self._release_store.latest(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            now=datetime.now(tz=UTC).replace(microsecond=0),
        )

    def latest_current_research(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> ResearchArtifact | None:
        return self._conversation_actions.latest_research(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
        )

    def _product_publication_store(
        self, tenant_id: str, installation_id: str
    ) -> PostgresProductPublicationStore:
        return PostgresProductPublicationStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )

    def plan_product_publication(
        self, *, tenant_id: str, installation_id: str, principal_id: str
    ) -> dict[str, object]:
        if self._stripe_publication is None:
            raise RuntimeError("Stripe publication connector is unavailable")
        now = datetime.now(tz=UTC).replace(microsecond=0)
        artifact = self._release_store.latest(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            now=now,
        )
        if artifact is None:
            raise CommerceError("no release candidate exists for publication")
        plan = publication_plan(
            artifact,
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            provider_account_id=self._stripe_publication.account_id,
            live_mode=self._stripe_publication.live_mode,
            created_at=now,
        )
        disposition = self._product_publication_store(
            tenant_id, installation_id
        ).retain_plan(plan)
        return {
            "disposition": disposition,
            "plan": plan.to_payload(),
            "plan_sha256": plan.sha256,
        }

    def approve_product_publication(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_sha256: str,
        approved_by: str,
    ) -> ProductPublicationApproval:
        store = self._product_publication_store(tenant_id, installation_id)
        plan = store.plan(plan_sha256)
        now = datetime.now(tz=UTC).replace(microsecond=0)
        approval = ProductPublicationApproval(
            approval_id=f"approval:publication:{plan.sha256[:48]}",
            plan_sha256=plan.sha256,
            release_sha256=plan.release_sha256,
            provider_account_id=plan.provider_account_id,
            live_mode=plan.live_mode,
            approved_by=approved_by,
            approved_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        store.approve(plan, approval, now=now)
        return approval

    async def publish_approved_product(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_sha256: str,
        approval_sha256: str,
    ) -> dict[str, object]:
        if self._stripe_publication is None:
            raise RuntimeError("Stripe publication connector is unavailable")
        store = self._product_publication_store(tenant_id, installation_id)
        plan = store.plan(plan_sha256)
        approval = store.approval(plan_sha256)
        if approval.sha256 != approval_sha256:
            raise CommerceError("publication approval hash differs")
        now = datetime.now(tz=UTC).replace(microsecond=0)
        opened = store.open_call(
            plan_sha256=plan_sha256,
            approval_sha256=approval_sha256,
            now=now,
        )
        if opened == "already_verified":
            existing = store.receipt(plan_sha256)
            if existing is None:
                raise CommerceError("verified publication receipt is missing")
            return {"disposition": opened, "receipt": existing.to_payload()}
        try:
            receipt = await self._stripe_publication.publish(plan, approval, now=now)
        except Exception as error:
            try:
                store.record_failure(plan_sha256=plan_sha256, error=error, now=now)
            except Exception:
                pass
            raise
        disposition = store.record_verified(
            plan, approval, receipt, now=receipt.observed_at
        )
        return {"disposition": disposition, "receipt": receipt.to_payload()}

    async def reconcile_product_publication(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_sha256: str,
        approval_sha256: str,
        provider_resource_id: str,
    ) -> dict[str, object]:
        if self._stripe_publication is None:
            raise RuntimeError("Stripe publication connector is unavailable")
        store = self._product_publication_store(tenant_id, installation_id)
        plan: ProductPublicationPlan = store.plan(plan_sha256)
        approval = store.approval(plan_sha256)
        if approval.sha256 != approval_sha256:
            raise CommerceError("publication approval hash differs")
        now = datetime.now(tz=UTC).replace(microsecond=0)
        receipt: ProductPublicationReceipt = await self._stripe_publication.readback(
            plan, resource_id=provider_resource_id, now=now
        )
        disposition = store.record_verified(plan, approval, receipt, now=now)
        return {"disposition": disposition, "receipt": receipt.to_payload()}

    def record_stripe_webhook(
        self, *, payload: bytes, signature_header: str
    ) -> dict[str, object]:
        if (
            self._stripe_webhook is None
            or self._commerce_tenant_id is None
            or self._commerce_installation_id is None
        ):
            raise RuntimeError("Stripe paid-order webhook is unavailable")
        receipt = self._stripe_webhook.verify(
            payload,
            signature_header,
            now=datetime.now(tz=UTC).replace(microsecond=0),
        )
        publication_store = self._product_publication_store(
            self._commerce_tenant_id, self._commerce_installation_id
        )
        publication = publication_store.receipt(receipt.plan_sha256)
        if publication is None:
            raise CommerceError("paid order has no verified publication")
        order_store = PostgresPaidOrderStore(
            self._engine,
            tenant_id=self._commerce_tenant_id,
            installation_id=self._commerce_installation_id,
        )
        disposition = order_store.retain_paid(receipt, publication)
        durable = order_store.load(receipt.checkout_session_id)
        return {
            "checkout_session_id": durable.checkout_session_id,
            "disposition": disposition,
            "receipt_sha256": durable.sha256,
        }

    def paid_product_download(
        self, *, checkout_session_id: str
    ) -> DigitalFulfillmentDownload:
        if self._commerce_tenant_id is None or self._commerce_installation_id is None:
            raise RuntimeError("paid product fulfillment is unavailable")
        now = datetime.now(tz=UTC).replace(microsecond=0)
        orders = PostgresPaidOrderStore(
            self._engine,
            tenant_id=self._commerce_tenant_id,
            installation_id=self._commerce_installation_id,
        )
        paid = orders.load(checkout_session_id)
        plan = self._product_publication_store(
            self._commerce_tenant_id, self._commerce_installation_id
        ).plan(paid.plan_sha256)
        try:
            artifact = self._release_store.exact(
                tenant_id=self._commerce_tenant_id,
                installation_id=self._commerce_installation_id,
                principal_id=plan.principal_id,
                intent_id=plan.release_intent_id,
                release_sha256=paid.release_sha256,
                now=now,
            )
        except (ConversationConflict, ValueError) as error:
            raise CommerceError("paid product artifact is unavailable") from error
        release = artifact.release
        _disposition, grant = orders.grant_fulfillment(
            checkout_session_id=checkout_session_id,
            release_sha256=release.content_sha256,
            filename=release.filename,
            byte_length=len(release.content),
            now=now,
        )
        return DigitalFulfillmentDownload(release.content, grant)

    def _journal(self, turn: ConversationTurn) -> PostgresConversationJournal:
        return PostgresConversationJournal(
            self._engine,
            external_timeline_scope(
                turn.tenant_id,
                turn.installation_id,
                "conversation journal scope is required",
            ),
            maximum_model_requests=getattr(self, "_conversation_maximum_requests", 32),
        )


def _build_proactive_delivery(settings: RuntimeSettings, engine: sa.Engine) -> TelegramProactiveDelivery | None:
    if settings.telegram_bot_token is None:
        if settings.telegram_proactive_chat_id is not None:
            raise RuntimeError("Eva proactive Telegram configuration is incomplete")
        return None
    if (settings.telegram_principal_bindings_file is None
            or settings.dashboard_tenant_id is None or settings.dashboard_installation_id is None
            or settings.dashboard_principal_binding_id is None or settings.background_principal_id is None):
        raise RuntimeError("proactive delivery requires the verified private owner binding")
    recipient = PostgresPrivateTelegramRecipient(
        engine,
        TimelineScope(UUID(settings.dashboard_tenant_id), UUID(settings.dashboard_installation_id)),
        owner=UUID(settings.background_principal_id), binding=UUID(settings.dashboard_principal_binding_id),
        bindings=lambda: load_principal_bindings(settings.telegram_principal_bindings_file),
    )
    def current_recipient() -> str:
        return recipient.current_chat_id(now=datetime.now(tz=UTC))
    expected = current_recipient()
    if settings.telegram_proactive_chat_id is not None and settings.telegram_proactive_chat_id != expected:
        raise RuntimeError("configured Telegram recipient differs from verified owner")
    return TelegramProactiveDelivery(
        bot_token=settings.telegram_bot_token, chat_id=settings.telegram_proactive_chat_id,
        recipient_resolver=current_recipient,
    )


def create_runtime_app() -> FastAPI:
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.bootstrap.household_voice import load_household_voice_authority

    settings = RuntimeSettings.from_environment()
    engine = sa.create_engine(
        sqlalchemy_postgres_dsn(settings.database_dsn), pool_pre_ping=True
    )
    if (
        settings.background_tenant_id is None
        or settings.background_installation_id is None
    ):
        raise RuntimeError("Eva runtime tenant and installation scope are required")
    group_consent = PostgresTelegramGroupConsentStore(
        engine,
        tenant_id=settings.background_tenant_id,
        installation_id=settings.background_installation_id,
    )
    telegram_principal_bindings = (
        None
        if settings.telegram_principal_bindings_file is None
        else load_principal_bindings(settings.telegram_principal_bindings_file)
    )
    telegram_canary = TelegramCanaryBridge(
        observations=PostgresTelegramGroupObservationStore(
            engine,
            tenant_id=settings.background_tenant_id,
            installation_id=settings.background_installation_id,
        ),
        group_consent=group_consent,
    )
    people_memory_overview = PostgresPeopleMemoryOverview(
        engine,
        tenant_id=settings.background_tenant_id,
        installation_id=settings.background_installation_id,
    )
    def current_identity_pack():
        return load_current_identity_pack(
            None if settings.identity_pack_file is None else Path(settings.identity_pack_file),
            trust=(None if settings.identity_pack_trust_file is None
                   else load_identity_pack_trust_binding(Path(settings.identity_pack_trust_file))),
        )

    identity_pack = current_identity_pack()
    household_voice = load_household_voice_authority(
        settings, runtime_identity=identity_pack, identity_loader=current_identity_pack,
    )
    memory = HindsightMemoryEngine(
        HindsightProjectionConfig(
            settings.hindsight_url,
            bank_namespace=settings.hindsight_bank_namespace,
            recall_budget=settings.hindsight_recall_budget,  # type: ignore[arg-type]
            recall_max_tokens=settings.hindsight_recall_max_tokens,
            recall_node_budget=settings.hindsight_recall_node_budget,
        ),
        active_group_consent_resolver=group_consent,
        active_household_consent_resolver=household_voice,
    )
    dashboard_voice = build_dashboard_voice(
        settings, runtime_identity=identity_pack, identity_loader=current_identity_pack,
    )
    identity = IdentityProfile(
        name=settings.identity_name,
        description=settings.identity_description,
        principles=(
            "Zeg eerlijk wat je wel en niet weet.",
            "Reageer als één persoon, niet als een verzameling scripts.",
            "Beweer een handeling pas na onafhankelijk bewijs.",
            "Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen.",
        ),
        maximum_reply_words=110,
        identity_pack=identity_pack,
    )
    delegated_work_store = None
    delegated_work_commands = None
    delegated_work_actions = None
    dashboard_effect_controller = None
    dashboard_owner_principal_id = None
    dashboard_principal_binding_id = None
    dashboard_authority_scope = None
    if settings.dashboard_principal_binding_id is not None:
        assert settings.dashboard_tenant_id is not None
        assert settings.dashboard_installation_id is not None
        assert settings.background_principal_id is not None
        dashboard_owner_principal_id = UUID(settings.background_principal_id)
        dashboard_principal_binding_id = UUID(settings.dashboard_principal_binding_id)
        dashboard_authority_scope = TimelineScope(
            UUID(settings.dashboard_tenant_id), UUID(settings.dashboard_installation_id),
        )
        delegated_work_store = PostgresDelegatedWorkStore(
            engine,
            TimelineScope(
                UUID(settings.dashboard_tenant_id),
                UUID(settings.dashboard_installation_id),
            ),
        )
        if settings.delegated_work_enabled:
            if telegram_principal_bindings is not None and not telegram_principal_bindings.contains_binding(
                principal_id=str(dashboard_owner_principal_id),
                principal_binding_id=dashboard_principal_binding_id,
            ):
                raise RuntimeError("delegated owner differs from trusted Telegram bindings")
            delegated_work_service = DelegatedWorkOwnerService(
                delegated_work_store,
                owner_principal_id=dashboard_owner_principal_id,
                principal_binding_id=dashboard_principal_binding_id,
                conversation_scope=external_timeline_scope(
                    settings.background_tenant_id or "",
                    settings.background_installation_id or "",
                    "delegated work requires the configured conversation scope",
                ),
                private_bridge_principal_loader=(
                    None if settings.telegram_principal_bindings_file is None
                    else lambda: load_principal_bindings(
                        settings.telegram_principal_bindings_file
                    ).private_bridge_principal(
                        principal_id=str(dashboard_owner_principal_id),
                        principal_binding_id=dashboard_principal_binding_id,
                    )
                ),
            )
            delegated_work_actions = PostgresDelegatedWorkActions(engine, delegated_work_service)
            if telegram_principal_bindings is not None:
                delegated_work_commands = DelegatedWorkConversationCommands(
                    service=delegated_work_service,
                )
        dashboard_effect_controller = DashboardActionChainEffectController(
            engine,
            TimelineScope(
                UUID(settings.dashboard_tenant_id),
                UUID(settings.dashboard_installation_id),
            ),
            owner_principal_id=dashboard_owner_principal_id,
            principal_binding_id=dashboard_principal_binding_id,
        )
    if settings.delegated_work_enabled and delegated_work_actions is None:
        raise RuntimeError("delegated work requires the configured dashboard owner")
    provider_bundle = build_external_provider_bundle(settings.external_provider, engine)
    model = PydanticConversationModel(
        auth_file=Path(settings.conversation_auth_file),
        model=settings.conversation_model,
        reasoning_effort=settings.conversation_reasoning_effort,  # type: ignore[arg-type]
        resource_budget=settings.runtime_resource_budget,
        conversation_tools=(
            (() if provider_bundle is None else provider_bundle.conversation_tools)
            + (() if delegated_work_actions is None else delegated_work_actions.conversation_tools())
        ),
    )
    stripe_publication = (
        None
        if settings.stripe_account_id is None or settings.stripe_secret_key is None
        else StripePublicationAdapter(
            StripePublicationConfig(
                account_id=settings.stripe_account_id,
                secret_key=settings.stripe_secret_key,
                fulfillment_base_url=settings.commerce_public_base_url or "",
                live_mode=settings.stripe_live_mode,
            )
        )
    )
    stripe_webhook = (
        None
        if settings.stripe_webhook_secret is None
        else StripeWebhookVerifier(
            StripeWebhookConfig(
                signing_secret=settings.stripe_webhook_secret,
                live_mode=settings.stripe_live_mode,
            )
        )
    )
    proactive_delivery = _build_proactive_delivery(settings, engine)
    file_batch = (
        None
        if settings.file_batch_root is None
        else LocalMarkdownBatchAdapter(Path(settings.file_batch_root))
    )
    validated_file_edit = (
        None
        if settings.file_batch_root is None
        else LocalValidatedTextEditAdapter(Path(settings.file_batch_root))
    )
    repository_improvement = (
        None
        if settings.file_batch_root is None
        else LocalRepositoryImprovementAdapter(Path(settings.file_batch_root))
    )
    fanout_delivery = (
        None
        if settings.fanout_recipient_endpoints is None
        else HttpFanoutDeliverySink(
            recipient_endpoints=settings.fanout_recipient_endpoints,
            allow_insecure_http=settings.fanout_allow_insecure_http,
        )
    )
    native_research = None
    if callable(getattr(model, "research_configuration_sha256", None)):
        from eva.adapters.model.research_tools import NativeResearchTool
        from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
        research_adapter = NativeResearchTool(model, PostgresConversationActions._validated_research_arguments)
        native_research = PostgresExternalConversationTools(engine, research_adapter, research_adapter)
    conversation_actions = PostgresConversationActions(
        engine,
        research=model,
        native_research=native_research,
        file_batch=file_batch,
        validated_file_edit=validated_file_edit,
        repository_improvement=repository_improvement,
        repository_improvement_model=(
            None if repository_improvement is None else model
        ),
        fanout_delivery=fanout_delivery,
        durable_goal_provider=settings.durable_goal_provider,
        durable_goal_recipients=settings.durable_goal_recipients,
        external_tools=(
            (() if provider_bundle is None else provider_bundle.external_tools)
            + (() if delegated_work_actions is None else (delegated_work_actions,))
        ),
    )
    conversation = LivingConversation(memory, model, identity, conversation_actions)
    sensory_store = (
        None
        if settings.background_tenant_id is None
        or settings.background_installation_id is None
        else PostgresSensoryEventStore(
            engine,
            tenant_id=settings.background_tenant_id,
            installation_id=settings.background_installation_id,
            recovery_started_at=datetime.now(tz=UTC).replace(microsecond=0),
            historical_group_size=16,
            initiative_principal_id=settings.background_principal_id,
            initiative_authorizer=lambda: runtime._initiative_authority().policy(),
        )
    )
    sensory_worker = (
        None
        if provider_bundle is None
        or provider_bundle.event_source is None
        or sensory_store is None
        else SensoryIngestionWorker(
            provider_bundle.event_source,
            sensory_store,
        )
    )
    idle_tasks = ()
    memory_outbox = PostgresMemoryOutbox(
        engine,
        tenant_id=settings.background_tenant_id or "",
        installation_id=settings.background_installation_id or "",
    )
    reflection_budget = (
        None
        if settings.runtime_resource_budget is None
        else PostgresDailyReflectionBudget(
            engine,
            tenant_id=settings.background_tenant_id or "",
            installation_id=settings.background_installation_id or "",
            resource_budget=settings.runtime_resource_budget,
        )
    )
    storage_budget = (
        None
        if settings.runtime_resource_budget is None
        else PostgresMemoryStorageBudget(
            engine,
            tenant_id=settings.background_tenant_id or "",
            installation_id=settings.background_installation_id or "",
            resource_budget=settings.runtime_resource_budget,
        )
    )
    conversation_erasure = (
        None
        if settings.background_tenant_id is None
        or settings.background_installation_id is None
        else PostgresConversationJournal(
            engine,
            external_timeline_scope(
                settings.background_tenant_id,
                settings.background_installation_id,
                "conversation erasure scope is required",
            ),
        )
    )
    restore_authority_erasure = (
        None
        if settings.portable_restore_authority_file is None
        or settings.dashboard_installation_id is None
        else PrivateRestoreAuthorityStore(
            Path(settings.portable_restore_authority_file),
            source_installation_id=settings.dashboard_installation_id,
        )
    )
    memory_writer = BudgetedConversationMemoryWriter(memory, storage_budget)
    prototype_store = PostgresPersonalPrototypeStore(
        engine, lease_seconds=settings.personal_interest_lease_seconds
    )
    release_store = PostgresPersonalProductReleaseStore(
        engine, lease_seconds=settings.personal_interest_lease_seconds
    )
    if (
        settings.background_tenant_id is not None
        and settings.background_installation_id is not None
    ):
        interest_worker = PersonalInterestWorker(
            PostgresPersonalInterestStore(
                engine, lease_seconds=settings.personal_interest_lease_seconds
            ),
            model,
            memory_writer,
            execute_native=lambda claim: runtime.execute_native_initiative(claim),
        )
        prototype_worker = PersonalPrototypeWorker(
            prototype_store,
            model,
            memory_writer,
        )
        release_worker = PersonalProductReleaseWorker(
            release_store, model, memory_writer
        )
        decision_worker = (
            None
            if settings.background_principal_id is None or sensory_store is None
            else SensoryDecisionWorker(
                sensory_store,
                model,
                memory,
                identity,
                tenant_id=settings.background_tenant_id,
                installation_id=settings.background_installation_id,
                principal_id=settings.background_principal_id,
                memory_principal_resolver=(
                    None
                    if settings.telegram_principal_bindings_file is None
                    or settings.dashboard_principal_binding_id is None
                    else lambda: (
                        load_principal_bindings(settings.telegram_principal_bindings_file)
                        .private_bridge_principal(
                            principal_id=settings.background_principal_id,
                            principal_binding_id=UUID(settings.dashboard_principal_binding_id),
                        ) or settings.background_principal_id
                    )
                ),
                admission_blocker=model.autonomous_admission_blocker,
                clock=lambda: datetime.now(tz=UTC).replace(microsecond=0),
                revalidator=(
                    provider_bundle.event_source
                    if provider_bundle is not None
                    and isinstance(provider_bundle.event_source, SensoryEventRevalidatorPort)
                    else None
                ),
            )
        )
        context_review_store = (
            None if settings.background_principal_id is None else PostgresSensoryEventStore(
                engine, tenant_id=settings.background_tenant_id,
                installation_id=settings.background_installation_id,
                initiative_principal_id=settings.background_principal_id,
                initiative_authorizer=lambda: runtime._initiative_authority().policy(),
                context_reviews_only=True, decision_lease_seconds=300,
            )
        )
        context_decision_worker = (
            None
            if settings.background_principal_id is None or context_review_store is None
            else SensoryDecisionWorker(
                context_review_store,
                model,
                memory,
                identity,
                tenant_id=settings.background_tenant_id,
                installation_id=settings.background_installation_id,
                principal_id=settings.background_principal_id,
                memory_principal_resolver=(
                    None
                    if settings.telegram_principal_bindings_file is None
                    or settings.dashboard_principal_binding_id is None
                    else lambda: (
                        load_principal_bindings(settings.telegram_principal_bindings_file)
                        .private_bridge_principal(
                            principal_id=settings.background_principal_id,
                            principal_binding_id=UUID(settings.dashboard_principal_binding_id),
                        ) or settings.background_principal_id
                    )
                ),
                admission_blocker=model.autonomous_admission_blocker,
                context_review_budget=reflection_budget,
                context_review_authorizer=lambda: runtime._initiative_authority().policy(),
                clock=lambda: datetime.now(tz=UTC).replace(microsecond=0),

            )
        )
        expression_worker = (
            None
            if sensory_store is None or proactive_delivery is None
            else ProactiveExpressionWorker(
                sensory_store,
                proactive_delivery,
                channel="telegram",
            )
        )

        def explore_one_interest() -> str:
            now = datetime.now(tz=UTC).replace(microsecond=0)
            return interest_worker.run_once(
                tenant_id=settings.background_tenant_id or "",
                installation_id=settings.background_installation_id or "",
                now=now,
            )

        def build_one_prototype() -> str:
            now = datetime.now(tz=UTC).replace(microsecond=0)
            return prototype_worker.run_once(
                tenant_id=settings.background_tenant_id or "",
                installation_id=settings.background_installation_id or "",
                now=now,
            )

        def prepare_one_release() -> str:
            now = datetime.now(tz=UTC).replace(microsecond=0)
            return release_worker.run_once(
                tenant_id=settings.background_tenant_id or "",
                installation_id=settings.background_installation_id or "",
                now=now,
            )

        def evaluate_one_sensory_event() -> str:
            if decision_worker is None:
                return "idle"
            return decision_worker.run_once(
                now=datetime.now(tz=UTC).replace(microsecond=0)
            )

        def deliver_one_proactive_expression() -> str:
            if expression_worker is None:
                return "idle"
            return expression_worker.run_once(
                now=datetime.now(tz=UTC).replace(microsecond=0)
            )

        def consider_one_private_opportunity() -> str:
            if context_review_store is None or context_decision_worker is None:
                return "idle"
            # Check current authority before private recall as well as at adoption/dispatch.
            try:
                runtime._initiative_authority().policy()
            except Exception:
                return "idle"
            if model.autonomous_admission_blocker() is not None:
                return "idle"
            now = datetime.now(tz=UTC).replace(microsecond=0)
            context_review_store.schedule_context_review(now=now)
            return context_decision_worker.run_once(now=now)

        idle_tasks = (
            explore_one_interest,
            build_one_prototype,
            prepare_one_release,
            evaluate_one_sensory_event,
            deliver_one_proactive_expression,
            consider_one_private_opportunity,
        )
    runtime = PostgresHindsightMemoryRuntime(
        engine,
        memory,
        conversation,
        HindsightColdPathWorker(
            memory,
            idle_seconds=settings.cold_path_idle_seconds,
            idle_tasks=idle_tasks,
            outbox=memory_outbox,
            reflection_budget=reflection_budget,
            storage_budget=storage_budget,
            conversation_erasure=conversation_erasure,
            restore_authority_erasure=restore_authority_erasure,
        ),
        prototype_store,
        release_store,
        conversation_actions,
        sensory_worker,
        sensory_store,
        model,
        conversation_maximum_requests=settings.conversation_maximum_requests,
        background_work_enabled=settings.background_work_enabled,
        stripe_publication=stripe_publication,
        stripe_webhook=stripe_webhook,
        commerce_tenant_id=settings.background_tenant_id,
        commerce_installation_id=settings.background_installation_id,
        runtime_tenant_id=settings.background_tenant_id,
        runtime_installation_id=settings.background_installation_id,
        group_consent=group_consent,
        household_consent=household_voice,
        telegram_canary=telegram_canary,
        people_memory_overview=people_memory_overview,
        delegated_work_store=delegated_work_store,
        telegram_principal_bindings=telegram_principal_bindings,
        delegated_work_commands=delegated_work_commands,
        dashboard_effect_controller=dashboard_effect_controller,
        dashboard_owner_principal_id=dashboard_owner_principal_id,
        dashboard_principal_binding_id=dashboard_principal_binding_id,
        dashboard_authority_scope=dashboard_authority_scope,
        telegram_principal_bindings_loader=(
            None if settings.telegram_principal_bindings_file is None
            else lambda: load_principal_bindings(settings.telegram_principal_bindings_file)
        ),
        memory_storage_budget=storage_budget,
    )
    native_voice = (None if household_voice is None else household_voice.build_service(
        runtime=runtime, engine=engine,
    ))
    app = create_app(
        runtime,
        native_voice=native_voice,
        dashboard_access_token=settings.dashboard_access_token,
        dashboard_identity_loader=current_identity_pack,
        dashboard_voice=dashboard_voice,
        browser_session=browser_session_from_environment(),
    )
    if household_voice is not None:
        from eva.adapters.api.household_voice_session import install_household_voice_session
        install_household_voice_session(app, runtime=runtime, authority=household_voice)
    return app


__all__ = [
    "PostgresHindsightMemoryRuntime",
    "RuntimeSettings",
    "create_runtime_app",
]
