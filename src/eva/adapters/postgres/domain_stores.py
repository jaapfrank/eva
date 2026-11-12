"""Consolidated 8-domain PostgreSQL store facade for Eva2 Core.

Unifies the 83 specialized PostgreSQL adapter modules behind 8 coherent domain
stores backed by the 3 canonical tables in ``eva_core``:
- ``eva_core.operational_events``
- ``eva_core.operational_checkpoints``
- ``eva_core.delegated_work_runs``

The 8 domain stores are:
1. ``TimelineDomainStore``: operational timeline, conversation journals & turn accounting
2. ``WorkDomainStore``: delegated work, scheduled work, progress & failure retention
3. ``MemoryDomainStore``: Hindsight memory outbox, candidates & reconciliation
4. ``InitiativeDomainStore``: owner initiatives, AIDE² adoption & durable goals
5. ``SelfRepairDomainStore``: runtime self-repair (E4.16), component improvements & E4.15 packages
6. ``ConnectorDomainStore``: MCP connectors, procedure references, mail & Telegram registries
7. ``HouseholdVoiceDomainStore``: household voice sessions, enrollment & check-in delivery
8. ``SensoryDomainStore``: sensory events, Home Assistant observations, will status & safe event compaction
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.core_indexes import ensure_core_composite_indexes
from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline,
    TimelineScope,
    operational_checkpoints,
    operational_events,
)


# Transient high-frequency event types eligible for age-gated compaction when a
# thread is terminal/superseded and the event is neither sequence 1 (origin) nor
# referenced by the current checkpoint revision.
COMPACTABLE_NOISE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "SENSORY_EVENT_OBSERVED",
        "WILL_REVALIDATION_REQUIRED",
    }
)


@dataclass(frozen=True, slots=True)
class DomainStoreInventory:
    """Mapping of a consolidated domain store to its constituent adapter modules."""

    domain: str
    primary_tables: tuple[str, ...]
    modules: tuple[str, ...]


DOMAIN_STORE_INVENTORY: tuple[DomainStoreInventory, ...] = (
    DomainStoreInventory(
        domain="timeline",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "operational_timeline",
            "conversation_journal",
            "conversation_actions",
            "conversation_threads",
            "turn_accounting",
            "action_journal",
        ),
    ),
    DomainStoreInventory(
        domain="work",
        primary_tables=("eva_core.delegated_work_runs", "eva_core.operational_events"),
        modules=(
            "delegated_work",
            "delegated_work_actions",
            "ScopedDelegatedWorkActions",
            "scheduled_work",
            "scheduled_work_failure",
            "background_work_progress",
            "work_source_retention",
            "work_corrections",
            "private_work_return",
        ),
    ),
    DomainStoreInventory(
        domain="memory",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "memory_outbox",
            "memory_candidate_store",
            "memory_reconciliation_store",
            "memory_retention",
        ),
    ),
    DomainStoreInventory(
        domain="initiative",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "owner_initiatives",
            "initiative_adoption",
            "initiative_execution",
            "completed_contribution",
            "durable_goals",
            "proactive_checkin_store",
        ),
    ),
    DomainStoreInventory(
        domain="self_repair",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "self_repair",
            "component_improvement_actions",
            "programming_actions",
            "tool_program_actions",
            "owned_package_context",
        ),
    ),
    DomainStoreInventory(
        domain="connector",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "mcp_connector_registry",
            "connector_results",
            "external_conversation_tools",
            "procedure_reference_host",
            "agentmail_ingress",
            "telegram_contact_registry",
            "telegram_group_consent",
        ),
    ),
    DomainStoreInventory(
        domain="household_voice",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "household_voice_sessions",
            "household_work_sessions",
            "household_work_destination",
            "household_checkin_delivery",
            "voice_enrollment",
            "voice_turn_staging",
            "speaker_enrollment",
        ),
    ),
    DomainStoreInventory(
        domain="sensory",
        primary_tables=("eva_core.operational_events", "eva_core.operational_checkpoints"),
        modules=(
            "sensory_events",
            "home_assistant_events",
            "face_observation",
            "will_status",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class EventCompactionReport:
    """Summary of historical sensory/will noise events eligible or compacted."""

    tenant_id: UUID
    installation_id: UUID
    cutoff: datetime
    dry_run: bool
    eligible_events: int
    compacted_events: int
    by_event_type: dict[str, int]


class TimelineDomainStore:
    """Domain 1: Operational timeline, conversation journal, and turn accounting."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope
        self.timeline = PostgresOperationalTimeline(engine, scope)

    def conversation_journal(self):
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal

        return PostgresConversationJournal(self.engine, self.scope)


class WorkDomainStore:
    """Domain 2: Delegated work runs, scheduled work, progress, and failure retention."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope

    def delegated_work_store(self, **kwargs: Any):
        from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore

        return PostgresDelegatedWorkStore(self.engine, self.scope, **kwargs)


class MemoryDomainStore:
    """Domain 3: Hindsight memory outbox, candidates, and reconciliation."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope

    def outbox_store(self):
        from eva.adapters.postgres.memory_outbox import PostgresMemoryOutboxStore

        return PostgresMemoryOutboxStore(
            self.engine,
            tenant_id=str(self.scope.tenant_id),
            installation_id=str(self.scope.installation_id),
        )


class InitiativeDomainStore:
    """Domain 4: Owner initiatives, AIDE² adoption, and durable goals."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope

    def owner_initiatives(self, *, owner: UUID, binding: UUID, authority_scope: TimelineScope | None = None):
        from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives

        return PostgresOwnerInitiatives(
            self.engine,
            tenant_id=str(self.scope.tenant_id),
            installation_id=str(self.scope.installation_id),
            owner=owner,
            binding=binding,
            authority_scope=authority_scope or self.scope,
        )


class SelfRepairDomainStore:
    """Domain 5: Runtime self-repair (E4.16), component improvements, and E4.15 packages."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope

    def current_binding(self, authority: Any, incident_id: str, *, now: datetime):
        from eva.adapters.postgres.self_repair import current_repair_binding

        return current_repair_binding(authority, incident_id, now=now)


class ConnectorDomainStore:
    """Domain 6: MCP connector registry, procedure references, AgentMail, and Telegram."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope

    def telegram_group_consent(self):
        from eva.adapters.postgres.telegram_group_consent import PostgresTelegramGroupConsentStore

        return PostgresTelegramGroupConsentStore(
            self.engine,
            tenant_id=str(self.scope.tenant_id),
            installation_id=str(self.scope.installation_id),
        )


class HouseholdVoiceDomainStore:
    """Domain 7: Household voice sessions, speaker enrollment, and check-in delivery."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope


class SensoryDomainStore:
    """Domain 8: Sensory events, Home Assistant observations, will status, and compaction."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope

    def inspect_or_compact_noise_events(
        self,
        *,
        older_than: timedelta = timedelta(days=7),
        now: datetime | None = None,
        dry_run: bool = True,
        batch_limit: int = 5000,
    ) -> EventCompactionReport:
        """Inspect or compact superseded historical sensory/will noise events.

        Safety invariants:
        - Only ``SENSORY_EVENT_OBSERVED`` and ``WILL_REVALIDATION_REQUIRED`` are eligible.
        - Never touches ``sequence = 1`` (thread origin event) or the latest sequence
          referenced by ``eva_core.operational_checkpoints.revision``.
        - Never touches threads without a checkpoint or threads in active repair/work.
        - Defaults to ``dry_run=True`` so callers can audit counts before deletion.
        """

        if older_than < timedelta(hours=24):
            raise ValueError("event compaction requires a retention window of at least 24 hours")
        if not 1 <= batch_limit <= 50_000:
            raise ValueError("event compaction batch_limit must be between 1 and 50,000")
        reference_now = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = reference_now - older_than

        e = operational_events.c
        c = operational_checkpoints.c
        eligible_query = (
            sa.select(e.event_id, e.event_type)
            .select_from(
                operational_events.join(
                    operational_checkpoints,
                    sa.and_(
                        e.tenant_id == c.tenant_id,
                        e.installation_id == c.installation_id,
                        e.thread_id == c.thread_id,
                    ),
                )
            )
            .where(
                e.tenant_id == self.scope.tenant_id,
                e.installation_id == self.scope.installation_id,
                e.event_type.in_(tuple(COMPACTABLE_NOISE_EVENT_TYPES)),
                e.sequence > 1,
                e.sequence < c.revision,
                e.occurred_at < cutoff,
            )
            .order_by(e.occurred_at.asc(), e.sequence.asc())
            .limit(batch_limit)
        )

        with self.engine.begin() as connection:
            PostgresOperationalTimeline(self.engine, self.scope)._scope(connection)
            rows = connection.execute(eligible_query).all()
            by_type: dict[str, int] = {}
            event_ids: list[str] = []
            for event_id, event_type in rows:
                by_type[event_type] = by_type.get(event_type, 0) + 1
                event_ids.append(event_id)
            compacted = len(event_ids) if not dry_run else 0

        if not dry_run:
            with self.engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.execute(sa.text("ANALYZE eva_core.operational_events"))
                connection.execute(sa.text("ANALYZE eva_core.operational_checkpoints"))

        return EventCompactionReport(
            tenant_id=self.scope.tenant_id,
            installation_id=self.scope.installation_id,
            cutoff=cutoff,
            dry_run=dry_run,
            eligible_events=len(event_ids),
            compacted_events=compacted,
            by_event_type=by_type,
        )


class CoreDomainStores:
    """Single entry point to Eva2's 8 PostgreSQL domain stores."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self.engine = engine
        self.scope = scope
        self.timeline = TimelineDomainStore(engine, scope)
        self.work = WorkDomainStore(engine, scope)
        self.memory = MemoryDomainStore(engine, scope)
        self.initiative = InitiativeDomainStore(engine, scope)
        self.self_repair = SelfRepairDomainStore(engine, scope)
        self.connector = ConnectorDomainStore(engine, scope)
        self.household_voice = HouseholdVoiceDomainStore(engine, scope)
        self.sensory = SensoryDomainStore(engine, scope)

    def ensure_indexes(self) -> tuple[str, ...]:
        """Ensure all composite B-tree indexes exist on the 3 core tables."""

        return ensure_core_composite_indexes(self.engine)

    @staticmethod
    def domain_inventory() -> tuple[DomainStoreInventory, ...]:
        return DOMAIN_STORE_INVENTORY


__all__ = [
    "COMPACTABLE_NOISE_EVENT_TYPES",
    "ConnectorDomainStore",
    "CoreDomainStores",
    "DOMAIN_STORE_INVENTORY",
    "DomainStoreInventory",
    "EventCompactionReport",
    "HouseholdVoiceDomainStore",
    "InitiativeDomainStore",
    "MemoryDomainStore",
    "SelfRepairDomainStore",
    "SensoryDomainStore",
    "TimelineDomainStore",
    "WorkDomainStore",
    "delegated_work_runs",
    "operational_checkpoints",
    "operational_events",
]
