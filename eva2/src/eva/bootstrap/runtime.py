"""Executable Living Core composition with Hindsight-owned semantic memory."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from eva.adapters.api.app import RuntimeHealth, create_app
from eva.adapters.connectors.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantToolAdapter,
)
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.adapters.connectors.home_assistant_hacs import HomeAssistantHacsAdapter
from eva.adapters.connectors.home_assistant_localtuya import (
    HomeAssistantLocalTuyaAdapter,
    LocalTuyaProbeSecret,
)
from eva.adapters.connectors.telegram import TelegramProactiveDelivery
from eva.adapters.connectors.fanout_http import HttpFanoutDeliverySink
from eva.adapters.connectors.agentmail import AgentMailConfig, AgentMailReadAdapter
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
from eva.adapters.connectors.home_assistant_automations import (
    HomeAssistantAutomationAdapter,
    HomeAssistantAutomationConfig,
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
from eva.adapters.postgres.people_memory_overview import (
    PostgresPeopleMemoryOverview,
)
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.advanced_tasks import PostgresAdvancedTaskStore
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.postgres.telegram_group_consent import (
    PostgresTelegramGroupConsentStore,
)
from eva.adapters.postgres.operational_timeline import TimelineScope
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
    HindsightMemoryEngine,
    HindsightProjectionConfig,
)

if TYPE_CHECKING:
    from eva.adapters.model.conversation import CodexConversationModel
from eva.application.conversation import (
    ConversationConflict,
    ConversationTurn,
    GroundedReply,
    IdentityProfile,
    load_current_identity_pack,
    LivingConversation,
    RecentConversationMessage,
)
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.application.advanced_tasks import (
    AdvancedTaskApproval,
    AdvancedTaskBlocked,
    AdvancedTaskPackageApproval,
    AdvancedTaskPlan,
    AdvancedTaskRepositoryApproval,
    LocalTuyaCanaryConfiguration,
    LocalTuyaEntityConfiguration,
    TuyaToLocalTuyaPlanner,
    adopt_device_preflight,
    advanced_plan_id,
    require_current_package_preflight,
    require_current_repository_preflight,
    verify_localtuya_canary_readback,
)
from eva.application.memory import (
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    PresenceMemory,
    RecallCurrentFacts,
    SemanticMemoryProviderError, SemanticMemoryProviderPort,
)
from eva.application.memory_audience import MemoryAudience
from eva.application.people_memory_overview import (
    PeopleMemoryOverview,
    PeopleMemoryOverviewPort,
)
from eva.contracts.workboard import WorkBoard
from eva.application.telegram_group_consent import TelegramGroupConsentStorePort
from eva.application.durable_goals import DurableGoalSpec, DurableGoalState
from eva.application.memory_background import HindsightColdPathWorker
from eva.application.mail import MailInboxPage, MailMessage
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
from eva.application.orchestration import VerifiedToolResult
from eva.application.free_will import (
    ProactiveExpressionClaim,
    ProactiveExpressionWorker,
    SensoryDecisionWorker,
)
from eva.application.sensory import SensoryIngestionWorker
from eva.bootstrap.secrets import environment_secret, read_runtime_secret_file
from eva.adapters.api.dashboard_access import validate_dashboard_access_token


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
    conversation_reasoning_effort: str = "medium"
    background_tenant_id: str | None = None
    background_installation_id: str | None = None
    background_principal_id: str | None = None
    dashboard_principal_binding_id: str | None = None
    dashboard_access_token: str | None = None
    cold_path_idle_seconds: float = 300.0
    personal_interest_lease_seconds: int = 90
    home_assistant_url: str | None = None
    home_assistant_token: str | None = None
    home_assistant_event_domains: tuple[str, ...] = ("light",)
    agentmail_inbox_id: str | None = None
    agentmail_api_key_file: str | None = None
    telegram_proactive_chat_id: str | None = None
    telegram_bot_token: str | None = None
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

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        if "EVA_DATABASE_DSN" in os.environ:
            raise RuntimeError(
                "EVA_DATABASE_DSN is forbidden; use EVA_DATABASE_DSN_FILE"
            )
        database_dsn = environment_secret(
            "EVA_DATABASE_DSN", "EVA_DATABASE_DSN_FILE"
        )
        hindsight_url = os.environ.get("EVA_HINDSIGHT_URL", "").strip()
        namespace = os.environ.get("EVA_HINDSIGHT_BANK_NAMESPACE", "eva2").strip()
        hindsight_recall_budget = os.environ.get(
            "EVA_HINDSIGHT_RECALL_BUDGET", "low"
        ).strip().casefold()
        try:
            hindsight_recall_max_tokens = int(
                os.environ.get("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "2048")
            )
            hindsight_recall_node_budget = int(
                os.environ.get("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "40")
            )
        except ValueError as error:
            raise RuntimeError("Eva Hindsight recall configuration is invalid") from error
        identity_name = os.environ.get("EVA_IDENTITY_NAME", "Eva").strip()
        identity_description = os.environ.get(
            "EVA_IDENTITY_DESCRIPTION",
            "Je bent een warme, nuchtere en capabele digitale huisgenoot.",
        ).strip()
        identity_pack_file = os.environ.get("EVA_IDENTITY_PACK_FILE", "").strip() or None
        identity_pack_trust_file = (
            os.environ.get("EVA_IDENTITY_PACK_TRUST_FILE", "").strip() or None
        )
        conversation_auth_file = os.environ.get(
            "EVA_CONVERSATION_AUTH_FILE", "/run/eva/codex/auth.json"
        ).strip()
        conversation_model = os.environ.get(
            "EVA_CONVERSATION_MODEL", "gpt-5.4-mini"
        ).strip()
        conversation_reasoning_effort = os.environ.get(
            "EVA_CONVERSATION_REASONING_EFFORT", "medium"
        ).strip()
        background_tenant_id = (
            os.environ.get("EVA_RUNTIME_TENANT_ID", "").strip() or None
        )
        background_installation_id = (
            os.environ.get("EVA_RUNTIME_INSTALLATION_ID", "").strip() or None
        )
        background_principal_id = (
            os.environ.get("EVA_RUNTIME_PRINCIPAL_ID", "").strip() or None
        )
        dashboard_principal_binding_id = (
            os.environ.get("EVA_DASHBOARD_PRINCIPAL_BINDING_ID", "").strip() or None
        )
        if "EVA_DASHBOARD_ACCESS_TOKEN" in os.environ:
            raise RuntimeError(
                "EVA_DASHBOARD_ACCESS_TOKEN is forbidden; use "
                "EVA_DASHBOARD_ACCESS_TOKEN_FILE"
            )
        dashboard_access_token_file = os.environ.get(
            "EVA_DASHBOARD_ACCESS_TOKEN_FILE", ""
        ).strip()
        dashboard_access_token = None
        if dashboard_access_token_file:
            try:
                dashboard_access_token = validate_dashboard_access_token(
                    read_runtime_secret_file(
                        dashboard_access_token_file,
                        setting_name="EVA_DASHBOARD_ACCESS_TOKEN_FILE",
                    )
                )
            except ValueError as error:
                raise RuntimeError(
                    "EVA_DASHBOARD_ACCESS_TOKEN_FILE has an invalid length or contents"
                ) from error
        if dashboard_principal_binding_id is not None:
            if background_principal_id is None:
                raise RuntimeError(
                    "Eva dashboard owner binding requires a runtime principal"
                )
            try:
                UUID(background_tenant_id or "")
                UUID(background_installation_id or "")
                UUID(background_principal_id)
                UUID(dashboard_principal_binding_id)
            except ValueError as error:
                raise RuntimeError("Eva dashboard owner binding is invalid") from error
            if dashboard_access_token is None:
                raise RuntimeError(
                    "Eva dashboard owner binding requires a dashboard access token file"
                )
        try:
            cold_path_idle_seconds = float(
                os.environ.get("EVA_COLD_PATH_IDLE_SECONDS", "300")
            )
        except ValueError as error:
            raise RuntimeError("EVA_COLD_PATH_IDLE_SECONDS is invalid") from error
        try:
            personal_interest_lease_seconds = int(
                os.environ.get("EVA_PERSONAL_INTEREST_LEASE_SECONDS", "90")
            )
        except ValueError as error:
            raise RuntimeError(
                "EVA_PERSONAL_INTEREST_LEASE_SECONDS is invalid"
            ) from error
        home_assistant_url = (
            os.environ.get("EVA_HOME_ASSISTANT_URL", "").strip() or None
        )
        if "EVA_HOME_ASSISTANT_TOKEN" in os.environ:
            raise RuntimeError(
                "EVA_HOME_ASSISTANT_TOKEN is forbidden; use "
                "EVA_HOME_ASSISTANT_TOKEN_FILE"
            )
        home_assistant_token = (
            environment_secret(
                "EVA_HOME_ASSISTANT_TOKEN", "EVA_HOME_ASSISTANT_TOKEN_FILE"
            )
            or None
        )
        home_assistant_event_domains = tuple(
            sorted(
                {
                    value.strip()
                    for value in os.environ.get(
                        "EVA_HOME_ASSISTANT_EVENT_DOMAINS",
                        "binary_sensor,light,person",
                    ).split(",")
                    if value.strip()
                }
            )
        )
        agentmail_inbox_id = (
            os.environ.get("EVA_AGENTMAIL_INBOX_ID", "").strip() or None
        )
        agentmail_api_key_file = (
            os.environ.get("EVA_AGENTMAIL_API_KEY_FILE", "").strip() or None
        )
        telegram_proactive_chat_id = (
            os.environ.get("EVA_TELEGRAM_PROACTIVE_CHAT_ID", "").strip() or None
        )
        if "EVA_TELEGRAM_BOT_TOKEN" in os.environ:
            raise RuntimeError(
                "EVA_TELEGRAM_BOT_TOKEN is forbidden; use "
                "EVA_TELEGRAM_BOT_TOKEN_FILE"
            )
        telegram_bot_token = (
            environment_secret(
                "EVA_TELEGRAM_BOT_TOKEN", "EVA_TELEGRAM_BOT_TOKEN_FILE"
            )
            or None
        )
        stripe_account_id = (
            os.environ.get("EVA_STRIPE_ACCOUNT_ID", "").strip() or None
        )
        if "EVA_STRIPE_SECRET_KEY" in os.environ:
            raise RuntimeError(
                "EVA_STRIPE_SECRET_KEY is forbidden; use "
                "EVA_STRIPE_SECRET_KEY_FILE"
            )
        if "EVA_STRIPE_WEBHOOK_SECRET" in os.environ:
            raise RuntimeError(
                "EVA_STRIPE_WEBHOOK_SECRET is forbidden; use "
                "EVA_STRIPE_WEBHOOK_SECRET_FILE"
            )
        stripe_secret_key = (
            environment_secret(
                "EVA_STRIPE_SECRET_KEY", "EVA_STRIPE_SECRET_KEY_FILE"
            )
            or None
        )
        stripe_webhook_secret = (
            environment_secret(
                "EVA_STRIPE_WEBHOOK_SECRET", "EVA_STRIPE_WEBHOOK_SECRET_FILE"
            )
            or None
        )
        commerce_public_base_url = (
            os.environ.get("EVA_COMMERCE_PUBLIC_BASE_URL", "").strip() or None
        )
        stripe_live_raw = os.environ.get("EVA_STRIPE_LIVE_MODE", "false").strip().lower()
        if stripe_live_raw not in {"true", "false"}:
            raise RuntimeError("EVA_STRIPE_LIVE_MODE must be true or false")
        stripe_live_mode = stripe_live_raw == "true"
        file_batch_root = os.environ.get("EVA_FILE_BATCH_ROOT", "").strip() or None
        fanout_raw = os.environ.get("EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON", "").strip()
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
        fanout_insecure_raw = os.environ.get(
            "EVA_FANOUT_ALLOW_INSECURE_HTTP", "false"
        ).strip().casefold()
        if fanout_insecure_raw not in {"true", "false"}:
            raise RuntimeError("EVA_FANOUT_ALLOW_INSECURE_HTTP must be true or false")
        fanout_allow_insecure_http = fanout_insecure_raw == "true"
        durable_goal_provider = (
            os.environ.get("EVA_DURABLE_GOAL_PROVIDER", "").strip() or None
        )
        durable_goal_recipients = tuple(
            item.strip()
            for item in os.environ.get("EVA_DURABLE_GOAL_RECIPIENTS", "").split(",")
            if item.strip()
        )
        if (durable_goal_provider is None) != (not durable_goal_recipients):
            raise RuntimeError("Eva durable goal configuration is incomplete")
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
        if not 2_000 <= hindsight_recall_max_tokens <= 4_000:
            raise RuntimeError(
                "EVA_HINDSIGHT_RECALL_MAX_TOKENS must be within 2000..4000"
            )
        if not 20 <= hindsight_recall_node_budget <= 100:
            raise RuntimeError(
                "EVA_HINDSIGHT_RECALL_NODE_BUDGET must be within 20..100"
            )
        if (
            not identity_name
            or not identity_description
            or not conversation_auth_file
            or not conversation_model
        ):
            raise RuntimeError("Eva identity configuration is incomplete")
        if (identity_pack_file is None) != (identity_pack_trust_file is None):
            raise RuntimeError(
                "EVA_IDENTITY_PACK_FILE and EVA_IDENTITY_PACK_TRUST_FILE must be set together"
            )
        if conversation_reasoning_effort not in {"low", "medium", "high"}:
            raise RuntimeError("EVA_CONVERSATION_REASONING_EFFORT is invalid")
        if background_tenant_id is None or background_installation_id is None:
            raise RuntimeError("Eva runtime tenant and installation scope are required")
        if (home_assistant_url is None) != (home_assistant_token is None):
            raise RuntimeError("Eva Home Assistant configuration is incomplete")
        if (agentmail_inbox_id is None) != (agentmail_api_key_file is None):
            raise RuntimeError("Eva AgentMail configuration is incomplete")
        if (telegram_proactive_chat_id is None) != (telegram_bot_token is None):
            raise RuntimeError("Eva proactive Telegram configuration is incomplete")
        if len(
            {
                stripe_account_id is None,
                stripe_secret_key is None,
                stripe_webhook_secret is None,
                commerce_public_base_url is None,
            }
        ) != 1:
            raise RuntimeError("Eva Stripe commerce configuration is incomplete")
        if stripe_account_id is not None and (
            background_tenant_id is None or background_installation_id is None
        ):
            raise RuntimeError("Eva Stripe webhook scope is incomplete")
        if not home_assistant_event_domains:
            raise RuntimeError("Eva Home Assistant event domain set is empty")
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
            background_tenant_id=background_tenant_id,
            background_installation_id=background_installation_id,
            background_principal_id=background_principal_id,
            dashboard_principal_binding_id=dashboard_principal_binding_id,
            dashboard_access_token=dashboard_access_token,
            cold_path_idle_seconds=cold_path_idle_seconds,
            personal_interest_lease_seconds=personal_interest_lease_seconds,
            home_assistant_url=home_assistant_url,
            home_assistant_token=home_assistant_token,
            home_assistant_event_domains=home_assistant_event_domains,
            agentmail_inbox_id=agentmail_inbox_id,
            agentmail_api_key_file=agentmail_api_key_file,
            telegram_proactive_chat_id=telegram_proactive_chat_id,
            telegram_bot_token=telegram_bot_token,
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
        home_assistant_admin: HomeAssistantAdminAdapter | None = None,
        home_assistant_hacs: HomeAssistantHacsAdapter | None = None,
        home_assistant_localtuya: HomeAssistantLocalTuyaAdapter | None = None,
        agentmail: AgentMailReadAdapter | None = None,
        conversation_model: CodexConversationModel | None = None,
        stripe_publication: StripePublicationAdapter | None = None,
        stripe_webhook: StripeWebhookVerifier | None = None,
        commerce_tenant_id: str | None = None,
        commerce_installation_id: str | None = None,
        runtime_tenant_id: str | None = None,
        runtime_installation_id: str | None = None,
        group_consent: TelegramGroupConsentStorePort | None = None,
        people_memory_overview: PeopleMemoryOverviewPort | None = None,
        delegated_work_store: PostgresDelegatedWorkStore | None = None,
        dashboard_effect_controller: DashboardActionChainEffectController | None = None,
        dashboard_owner_principal_id: UUID | None = None,
        dashboard_principal_binding_id: UUID | None = None,
    ) -> None:
        self._engine = engine
        self._memory = memory
        self._conversation = conversation
        self._cold_path = cold_path
        self._prototype_store = prototype_store
        self._release_store = release_store
        self._conversation_actions = conversation_actions
        self._sensory_worker = sensory_worker
        self._sensory_store = sensory_store
        self._home_assistant_admin = home_assistant_admin
        self._home_assistant_hacs = home_assistant_hacs
        self._home_assistant_localtuya = home_assistant_localtuya
        self._agentmail = agentmail
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
        self._people_memory_overview = people_memory_overview
        if (delegated_work_store is None) != (dashboard_owner_principal_id is None):
            raise ValueError("delegated work dashboard owner is incomplete")
        if (delegated_work_store is None) != (dashboard_principal_binding_id is None):
            raise ValueError("delegated work dashboard binding is incomplete")
        self._delegated_work_store = delegated_work_store
        self._dashboard_effect_controller = dashboard_effect_controller
        self._dashboard_owner_principal_id = dashboard_owner_principal_id
        self._dashboard_principal_binding_id = dashboard_principal_binding_id

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
        self._cold_path.start()
        if self._sensory_worker is not None:
            self._sensory_worker.start()

    def stop_background_work(self) -> None:
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

    def dashboard_stop_effect(
        self, effect_intent_id: UUID
    ) -> DashboardActionChainEffectStop:
        """Submit one owner-bound stop command through the durable effect ledger."""

        if self._dashboard_effect_controller is None:
            raise RuntimeError("effect stop control is unavailable")
        return self._dashboard_effect_controller.stop(effect_intent_id)

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
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4_096:
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

    def recall(self, query: RecallCurrentFacts) -> PresenceMemory:
        return self._memory.recall_for_presence(query)

    def retain(self, document: ConversationMemoryDocument) -> MemoryRetentionReceipt:
        return self._memory.retain_conversation(document)

    def admit_turn_audience(self, turn: ConversationTurn) -> None:
        """Revalidate a connector's group grant at the core boundary."""

        self._require_runtime_scope(turn.tenant_id, turn.installation_id)
        audience: MemoryAudience = turn.audience
        if audience.kind == "private_person":
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
            raise ValueError("Telegram group turn consent could not be verified") from error
        if active != audience.consent_ref:
            raise ValueError("Telegram group turn consent is not active and exact")

    def respond(
        self,
        turn: ConversationTurn,
        recent_messages: tuple[RecentConversationMessage, ...] = (),
    ) -> GroundedReply:
        self.admit_turn_audience(turn)
        journal = self._journal(turn)
        journal.admit(turn)
        committed = journal.committed_reply(turn)
        if committed is not None:
            return committed
        durable_recent = journal.recent(turn, exclude_message_id=turn.message_id)
        recent = tuple((*recent_messages, *durable_recent)[-12:])
        candidate = self._conversation.respond(turn, recent)
        return journal.commit_reply(turn, candidate)

    def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None:
        """Queue semantic retention only after the reply is durably committed."""

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
            and completed_action.call.name
            == "record_observation_identity_correction"
            else None
        )
        if grounded_correction is None:
            document_id = f"{turn.conversation_id}:exchange:{turn.message_id}"
            content = f"Gebruiker (directe bron): {turn.text}"
            evidence_ref = (
                f"{turn.conversation_id}:evidence:{turn.message_id}"
                if turn.audience.kind == "telegram_group"
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
        self._cold_path.submit(document)
        journal = self._journal(turn)
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
        disposition = PostgresProductPublicationStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        ).retain_plan(plan)
        return {"disposition": disposition, "plan": plan.to_payload(), "plan_sha256": plan.sha256}

    def approve_product_publication(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_sha256: str,
        approved_by: str,
    ) -> ProductPublicationApproval:
        store = PostgresProductPublicationStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
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
        store = PostgresProductPublicationStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
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
            receipt = await self._stripe_publication.publish(
                plan, approval, now=now
            )
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
        store = PostgresProductPublicationStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
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
        publication_store = PostgresProductPublicationStore(
            self._engine,
            tenant_id=self._commerce_tenant_id,
            installation_id=self._commerce_installation_id,
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
        if (
            self._commerce_tenant_id is None
            or self._commerce_installation_id is None
        ):
            raise RuntimeError("paid product fulfillment is unavailable")
        now = datetime.now(tz=UTC).replace(microsecond=0)
        orders = PostgresPaidOrderStore(
            self._engine,
            tenant_id=self._commerce_tenant_id,
            installation_id=self._commerce_installation_id,
        )
        paid = orders.load(checkout_session_id)
        plan = PostgresProductPublicationStore(
            self._engine,
            tenant_id=self._commerce_tenant_id,
            installation_id=self._commerce_installation_id,
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

    async def execute_home_assistant_light(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        thread_id: str,
        operation_id: str,
        entity_id: str,
        service: str,
    ) -> VerifiedToolResult:
        return await self._conversation_actions.execute_home_assistant_light(
            tenant_id=tenant_id,
            installation_id=installation_id,
            thread_id=thread_id,
            operation_id=operation_id,
            entity_id=entity_id,
            service=service,
        )

    async def plan_tuya_to_localtuya(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        operation_id: str,
    ) -> AdvancedTaskPlan:
        if self._home_assistant_admin is None:
            raise RuntimeError("Home Assistant advanced planning is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        plan_id = advanced_plan_id(operation_id=operation_id, requested_by=principal_id)
        existing = store.find(plan_id)
        if existing is not None:
            return existing
        inventory = await self._home_assistant_admin.inventory()
        plan = TuyaToLocalTuyaPlanner().build(
            inventory,
            operation_id=operation_id,
            requested_by=principal_id,
        )
        store.retain(plan)
        return plan

    async def refresh_tuya_to_localtuya_plan(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
    ) -> dict[str, object]:
        """Re-read Home Assistant and append the next durable plan version."""

        if self._home_assistant_admin is None:
            raise RuntimeError("Home Assistant advanced planning is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        current = store.load(plan_id)
        inventory = await self._home_assistant_admin.inventory()
        refreshed = TuyaToLocalTuyaPlanner().build(
            inventory,
            operation_id=current.operation_id,
            requested_by=current.requested_by,
        )
        if refreshed.plan_id != current.plan_id:
            raise RuntimeError("advanced task refresh changed plan identity")
        disposition = store.refresh(refreshed)
        return {"disposition": disposition, "plan": store.load(plan_id).to_payload()}

    def approve_tuya_to_localtuya_canary(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        device_id: str,
        approved_by: str,
    ) -> AdvancedTaskApproval:
        return PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        ).approve_canary(
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            inventory_sha256=inventory_sha256,
            device_id=device_id,
            approved_by=approved_by,
            approved_at=datetime.now(tz=UTC).replace(microsecond=0),
        )

    async def probe_localtuya_canary(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        device_id: str,
        config_entry_id: str,
        friendly_name: str,
        host: str,
        local_device_id: str,
        local_key: str,
        protocol_version: str,
    ) -> dict[str, object]:
        """Prove one local connection; abort the HA flow before device creation."""

        if self._home_assistant_localtuya is None or self._home_assistant_admin is None:
            raise RuntimeError("Home Assistant LocalTuya preflight is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        plan = store.load(plan_id)
        if plan.sha256 != plan_sha256 or plan.inventory_sha256 != inventory_sha256:
            raise RuntimeError("LocalTuya preflight has stale plan authority")
        inventory = await self._home_assistant_admin.inventory()
        current = TuyaToLocalTuyaPlanner().build(
            inventory,
            operation_id=plan.operation_id,
            requested_by=plan.requested_by,
        )
        disposition = store.refresh(current)
        if disposition == "refreshed":
            raise RuntimeError("Home Assistant changed; refresh approval authority")
        local_entries = [
            entry
            for entry in inventory.integrations
            if entry.domain == "localtuya" and entry.entry_id == config_entry_id
        ]
        if len(local_entries) != 1 or local_entries[0].state != "loaded":
            raise RuntimeError("LocalTuya config entry is not loaded")
        secret = LocalTuyaProbeSecret(
            friendly_name=friendly_name,
            host=host,
            device_id=local_device_id,
            local_key=local_key,
            protocol_version=protocol_version,
        )
        proof = await self._home_assistant_localtuya.probe(
            config_entry_id=config_entry_id,
            secret=secret,
        )
        if not proof.flow_aborted or proof.next_step_id != "pick_entity_type":
            raise RuntimeError(
                "LocalTuya preflight did not stop before device creation"
            )
        now = datetime.now(tz=UTC).replace(microsecond=0)
        preflight = adopt_device_preflight(
            plan,
            device_id=device_id,
            config_entry_id=config_entry_id,
            local_device_id_sha256=proof.device_id_sha256,
            local_host_sha256=proof.host_sha256,
            protocol_version=proof.protocol_version,
            next_schema_sha256=proof.next_schema_sha256,
            probed_at=now,
        )
        retained = store.retain_device_preflight(preflight)
        return {"disposition": retained, "preflight": preflight.to_payload()}

    async def prepare_localtuya_canary_execution(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        approval_sha256: str,
        preflight_sha256: str,
        configuration_sha256: str,
    ) -> dict[str, object]:
        """Commit the exact canary fence before any provider mutation."""

        if self._home_assistant_admin is None:
            raise RuntimeError("Home Assistant LocalTuya execution is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        plan = store.load(plan_id)
        approval, preflight = store.canary_authority(
            plan_id=plan_id,
            approval_sha256=approval_sha256,
            preflight_sha256=preflight_sha256,
        )
        before = await self._home_assistant_admin.inventory()
        current = TuyaToLocalTuyaPlanner().build(
            before,
            operation_id=plan.operation_id,
            requested_by=plan.requested_by,
        )
        if store.refresh(current) == "refreshed":
            raise RuntimeError("Home Assistant changed; canary approval is stale")
        disposition = store.prepare_canary(
            approval,
            preflight,
            configuration_sha256=configuration_sha256,
            before_inventory=before,
            occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
        )
        return {
            "approval_sha256": approval.sha256,
            "configuration_sha256": configuration_sha256,
            "device_id": approval.device_id,
            "disposition": disposition,
            "preflight_sha256": preflight.sha256,
        }

    async def reconcile_localtuya_canary_execution(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
    ) -> dict[str, object]:
        """After call or crash, derive success only from the current HA registries."""

        if self._home_assistant_admin is None:
            raise RuntimeError("Home Assistant LocalTuya execution is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        approval, preflight, configuration_sha256, before = (
            store.prepared_canary_context(plan_id=plan_id)
        )
        after = await self._home_assistant_admin.inventory()
        proof = verify_localtuya_canary_readback(
            store.load(plan_id),
            approval,
            preflight,
            configuration_sha256=configuration_sha256,
            before=before,
            after=after,
            observed_at=datetime.now(tz=UTC).replace(microsecond=0),
        )
        disposition = store.record_canary_verified(proof)
        return {"disposition": disposition, "proof": proof.to_payload()}

    async def execute_prepared_localtuya_canary(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        friendly_name: str,
        host: str,
        local_device_id: str,
        local_key: str,
        protocol_version: str,
        entities: tuple[LocalTuyaEntityConfiguration, ...],
        readback_timeout_seconds: float = 10.0,
    ) -> dict[str, object]:
        """Run one prepared canary once, then poll independent HA readback."""

        if self._home_assistant_localtuya is None:
            raise RuntimeError("Home Assistant LocalTuya execution is unavailable")
        configuration = LocalTuyaCanaryConfiguration(
            friendly_name=friendly_name,
            host=host,
            device_id=local_device_id,
            local_key=local_key,
            protocol_version=protocol_version,
            entities=entities,
        )
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        _approval, preflight, expected_sha256, _before = store.prepared_canary_context(
            plan_id=plan_id
        )
        if configuration.sha256 != expected_sha256:
            raise RuntimeError(
                "LocalTuya canary configuration differs from preparation"
            )
        if (
            hashlib.sha256(local_device_id.encode()).hexdigest()
            != preflight.local_device_id_sha256
            or hashlib.sha256(host.encode()).hexdigest() != preflight.local_host_sha256
            or protocol_version != preflight.protocol_version
        ):
            raise RuntimeError("LocalTuya canary differs from verified preflight")
        now = datetime.now(tz=UTC).replace(microsecond=0)
        opened = store.open_canary_call(
            plan_id=plan_id,
            configuration_sha256=configuration.sha256,
            occurred_at=now,
        )
        if opened == "already_verified":
            return await self.reconcile_localtuya_canary_execution(
                tenant_id=tenant_id,
                installation_id=installation_id,
                plan_id=plan_id,
            )
        if opened != "opened":
            raise RuntimeError(
                "LocalTuya canary call was already opened; reconcile instead of replay"
            )
        try:
            receipt = await self._home_assistant_localtuya.create_canary(
                config_entry_id=preflight.config_entry_id,
                configuration=configuration,
            )
        except Exception as error:
            store.record_canary_call_failure(
                plan_id=plan_id,
                error_text=f"{type(error).__name__}:{error}",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
            raise
        store.record_canary_call_result(
            plan_id=plan_id,
            configuration_sha256=configuration.sha256,
            receipt=receipt.to_payload(),
            occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + readback_timeout_seconds
        latest_error = "Home Assistant has not exposed the canary yet"
        while True:
            try:
                result = await self.reconcile_localtuya_canary_execution(
                    tenant_id=tenant_id,
                    installation_id=installation_id,
                    plan_id=plan_id,
                )
                return {"provider_receipt": receipt.to_payload(), **result}
            except AdvancedTaskBlocked as error:
                latest_error = str(error)
                if not any(
                    marker in latest_error
                    for marker in (
                        "did not create exactly one",
                        "has no independently read",
                        "is not loaded after canary",
                    )
                ):
                    raise
                if loop.time() >= deadline:
                    return {
                        "detail": latest_error,
                        "disposition": "awaiting_readback",
                        "provider_receipt": receipt.to_payload(),
                    }
                await asyncio.sleep(0.5)

    async def list_mail_messages(
        self, *, limit: int, page_token: str | None
    ) -> MailInboxPage:
        if self._agentmail is None:
            raise RuntimeError("Eva mail connector is unavailable")
        return await self._agentmail.list_messages(limit=limit, page_token=page_token)

    async def get_mail_message(self, *, message_id: str) -> MailMessage:
        if self._agentmail is None:
            raise RuntimeError("Eva mail connector is unavailable")
        return await self._agentmail.get_message(message_id)

    def approve_localtuya_package(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        repository_id: str,
        version: str,
        approved_by: str,
    ) -> AdvancedTaskPackageApproval:
        return PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        ).approve_package_install(
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            inventory_sha256=inventory_sha256,
            repository_id=repository_id,
            version=version,
            approved_by=approved_by,
            approved_at=datetime.now(tz=UTC).replace(microsecond=0),
        )

    def approve_localtuya_repository(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        repository_full_name: str,
        expected_version: str,
        approved_by: str,
    ) -> AdvancedTaskRepositoryApproval:
        return PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        ).approve_repository_registration(
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            inventory_sha256=inventory_sha256,
            repository_full_name=repository_full_name,
            expected_version=expected_version,
            approved_by=approved_by,
            approved_at=datetime.now(tz=UTC).replace(microsecond=0),
        )

    async def register_approved_localtuya_repository(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        approval_sha256: str,
    ) -> dict[str, object]:
        if self._home_assistant_hacs is None:
            raise RuntimeError("Home Assistant HACS execution is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        approval = store.repository_approval(
            plan_id=plan_id, approval_sha256=approval_sha256
        )
        now = datetime.now(tz=UTC).replace(microsecond=0)
        if now >= approval.expires_at:
            raise RuntimeError("LocalTuya repository approval expired")
        if not store.repository_registration_started(plan_id):
            if self._home_assistant_admin is None:
                raise RuntimeError("Home Assistant preflight is unavailable")
            inventory = await self._home_assistant_admin.inventory()
            require_current_repository_preflight(store.load(plan_id), inventory)
        store.prepare_repository_registration(approval, occurred_at=now)
        try:
            repository = await self._home_assistant_hacs.register_exact(
                approval, now=now
            )
            disposition = store.record_repository_registered(
                approval,
                repository,
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
        except Exception as error:
            store.record_repository_failure(
                approval,
                error_text=f"{type(error).__name__}:{error}",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
            raise
        refreshed_plan = store.load(plan_id)
        refresh_disposition = "not_refreshed"
        if self._home_assistant_admin is not None:
            inventory = await self._home_assistant_admin.inventory()
            refreshed = TuyaToLocalTuyaPlanner().build(
                inventory,
                operation_id=refreshed_plan.operation_id,
                requested_by=refreshed_plan.requested_by,
            )
            refresh_disposition = store.refresh(refreshed)
            refreshed_plan = store.load(plan_id)
        return {
            "approval_sha256": approval.sha256,
            "disposition": disposition,
            "plan": refreshed_plan.to_payload(),
            "plan_refresh_disposition": refresh_disposition,
            "repository_full_name": repository.full_name,
            "repository_id": repository.repository_id,
            "repository_sha256": repository.sha256,
            "version": repository.available_version,
        }

    async def install_approved_localtuya_package(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        plan_id: str,
        approval_sha256: str,
    ) -> dict[str, object]:
        if self._home_assistant_hacs is None:
            raise RuntimeError("Home Assistant HACS execution is unavailable")
        store = PostgresAdvancedTaskStore(
            self._engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
        approval = store.package_approval(
            plan_id=plan_id, approval_sha256=approval_sha256
        )
        now = datetime.now(tz=UTC).replace(microsecond=0)
        if now >= approval.expires_at:
            raise RuntimeError("LocalTuya package approval expired")
        if not store.package_install_started(plan_id):
            if self._home_assistant_admin is None:
                raise RuntimeError("Home Assistant preflight is unavailable")
            inventory = await self._home_assistant_admin.inventory()
            require_current_package_preflight(store.load(plan_id), inventory)
        store.prepare_package_install(approval, occurred_at=now)
        try:
            repository = await self._home_assistant_hacs.install_exact(
                approval, now=now
            )
            disposition = store.record_package_installed(
                approval,
                repository,
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
        except Exception as error:
            store.record_package_failure(
                approval,
                error_text=f"{type(error).__name__}:{error}",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
            raise
        return {
            "approval_sha256": approval.sha256,
            "disposition": disposition,
            "installed_version": repository.installed_version,
            "plan_id": plan_id,
            "repository_id": repository.repository_id,
            "repository_sha256": repository.sha256,
        }

    def _journal(self, turn: ConversationTurn) -> PostgresConversationJournal:
        return PostgresConversationJournal(
            self._engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL,
                    f"eva2:{turn.tenant_id}:{turn.installation_id}",
                ),
            ),
        )


def create_runtime_app() -> FastAPI:
    from eva.adapters.model.conversation import CodexConversationModel

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
    people_memory_overview = PostgresPeopleMemoryOverview(
        engine,
        tenant_id=settings.background_tenant_id,
        installation_id=settings.background_installation_id,
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
    )
    identity_pack = load_current_identity_pack(
        None if settings.identity_pack_file is None else Path(settings.identity_pack_file),
        trust=(
            None
            if settings.identity_pack_trust_file is None
            else load_identity_pack_trust_binding(Path(settings.identity_pack_trust_file))
        ),
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
    model = CodexConversationModel(
        auth_file=Path(settings.conversation_auth_file),
        model=settings.conversation_model,
        reasoning_effort=settings.conversation_reasoning_effort,  # type: ignore[arg-type]
    )
    home_assistant = (
        None
        if settings.home_assistant_url is None
        else HomeAssistantToolAdapter(
            HomeAssistantConfig(
                settings.home_assistant_url,
                settings.home_assistant_token or "",
            )
        )
    )
    home_assistant_automations = (
        None
        if settings.home_assistant_url is None
        else HomeAssistantAutomationAdapter(
            HomeAssistantAutomationConfig(
                settings.home_assistant_url,
                settings.home_assistant_token or "",
            )
        )
    )
    home_assistant_admin = (
        None
        if settings.home_assistant_url is None
        else HomeAssistantAdminAdapter(
            base_url=settings.home_assistant_url,
            token=settings.home_assistant_token or "",
        )
    )
    home_assistant_hacs = (
        None
        if settings.home_assistant_url is None
        else HomeAssistantHacsAdapter(
            base_url=settings.home_assistant_url,
            token=settings.home_assistant_token or "",
        )
    )
    home_assistant_localtuya = (
        None
        if settings.home_assistant_url is None
        else HomeAssistantLocalTuyaAdapter(
            base_url=settings.home_assistant_url,
            token=settings.home_assistant_token or "",
        )
    )
    agentmail = None
    if (
        settings.agentmail_inbox_id is not None
        and settings.agentmail_api_key_file is not None
    ):
        api_key = read_runtime_secret_file(
            settings.agentmail_api_key_file,
            setting_name="EVA_AGENTMAIL_API_KEY_FILE",
        )
        agentmail = AgentMailReadAdapter(
            AgentMailConfig(
                inbox_id=settings.agentmail_inbox_id,
                api_key=api_key,
            )
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
    proactive_delivery = (
        None
        if settings.telegram_proactive_chat_id is None
        or settings.telegram_bot_token is None
        else TelegramProactiveDelivery(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_proactive_chat_id,
        )
    )
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
    conversation_actions = PostgresConversationActions(
        engine,
        home_assistant,
        home_assistant_automations,
        home_assistant_admin,
        home_assistant_hacs,
        research=model,
        file_batch=file_batch,
        validated_file_edit=validated_file_edit,
        repository_improvement=repository_improvement,
        repository_improvement_model=(
            None if repository_improvement is None else model
        ),
        fanout_delivery=fanout_delivery,
        durable_goal_provider=settings.durable_goal_provider,
        durable_goal_recipients=settings.durable_goal_recipients,
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
        )
    )
    sensory_worker = (
        None
        if settings.home_assistant_url is None or sensory_store is None
        else SensoryIngestionWorker(
            HomeAssistantEventStream(
                base_url=settings.home_assistant_url,
                token=settings.home_assistant_token or "",
                admitted_domains=settings.home_assistant_event_domains,
            ),
            sensory_store,
        )
    )
    idle_tasks = ()
    memory_outbox = PostgresMemoryOutbox(
        engine,
        tenant_id=settings.background_tenant_id or "",
        installation_id=settings.background_installation_id or "",
    )
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
            memory,
        )
        prototype_worker = PersonalPrototypeWorker(
            prototype_store,
            model,
            memory,
        )
        release_worker = PersonalProductReleaseWorker(release_store, model, memory)
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

        idle_tasks = (
            explore_one_interest,
            build_one_prototype,
            prepare_one_release,
            evaluate_one_sensory_event,
            deliver_one_proactive_expression,
        )
    delegated_work_store = None
    dashboard_effect_controller = None
    dashboard_owner_principal_id = None
    dashboard_principal_binding_id = None
    if settings.dashboard_principal_binding_id is not None:
        assert settings.background_tenant_id is not None
        assert settings.background_installation_id is not None
        assert settings.background_principal_id is not None
        dashboard_owner_principal_id = UUID(settings.background_principal_id)
        dashboard_principal_binding_id = UUID(
            settings.dashboard_principal_binding_id
        )
        delegated_work_store = PostgresDelegatedWorkStore(
            engine,
            TimelineScope(
                UUID(settings.background_tenant_id),
                UUID(settings.background_installation_id),
            ),
        )
        dashboard_effect_controller = DashboardActionChainEffectController(
            engine,
            TimelineScope(
                UUID(settings.background_tenant_id),
                UUID(settings.background_installation_id),
            ),
            owner_principal_id=dashboard_owner_principal_id,
            principal_binding_id=dashboard_principal_binding_id,
        )
    return create_app(
        PostgresHindsightMemoryRuntime(
            engine,
            memory,
            conversation,
            HindsightColdPathWorker(
                memory,
                idle_seconds=settings.cold_path_idle_seconds,
                idle_tasks=idle_tasks,
                outbox=memory_outbox,
            ),
            prototype_store,
            release_store,
            conversation_actions,
            sensory_worker,
            sensory_store,
            home_assistant_admin,
            home_assistant_hacs,
            home_assistant_localtuya,
            agentmail,
            model,
            stripe_publication=stripe_publication,
            stripe_webhook=stripe_webhook,
            commerce_tenant_id=settings.background_tenant_id,
            commerce_installation_id=settings.background_installation_id,
            runtime_tenant_id=settings.background_tenant_id,
            runtime_installation_id=settings.background_installation_id,
            group_consent=group_consent,
            people_memory_overview=people_memory_overview,
            delegated_work_store=delegated_work_store,
            dashboard_effect_controller=dashboard_effect_controller,
            dashboard_owner_principal_id=dashboard_owner_principal_id,
            dashboard_principal_binding_id=dashboard_principal_binding_id,
        ),
        dashboard_access_token=settings.dashboard_access_token,
    )


__all__ = [
    "PostgresHindsightMemoryRuntime",
    "RuntimeSettings",
    "create_runtime_app",
]
