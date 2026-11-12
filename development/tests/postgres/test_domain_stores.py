"""Unit tests for the consolidated 8-domain PostgreSQL store facade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from eva.adapters.postgres.core_indexes import CORE_COMPOSITE_INDEXES
from eva.adapters.postgres.domain_stores import (
    COMPACTABLE_NOISE_EVENT_TYPES,
    CoreDomainStores,
    DOMAIN_STORE_INVENTORY,
    SensoryDomainStore,
)
from eva.adapters.postgres.operational_timeline import TimelineScope


def test_core_domain_stores_exposes_all_eight_domains_and_indexes() -> None:
    scope = TimelineScope(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        installation_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    dummy_engine = SimpleNamespace(
        url=SimpleNamespace(get_backend_name=lambda: "postgresql")
    )
    stores = CoreDomainStores(dummy_engine, scope)

    inventory = stores.domain_inventory()
    assert len(inventory) == 8
    assert [item.domain for item in inventory] == [
        "timeline",
        "work",
        "memory",
        "initiative",
        "self_repair",
        "connector",
        "household_voice",
        "sensory",
    ]
    assert len(CORE_COMPOSITE_INDEXES) == 4
    assert COMPACTABLE_NOISE_EVENT_TYPES == frozenset(
        {"SENSORY_EVENT_OBSERVED", "WILL_REVALIDATION_REQUIRED"}
    )


def test_sensory_domain_store_compaction_guards_minimum_retention_and_bounds() -> None:
    scope = TimelineScope(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        installation_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    sensory = SensoryDomainStore(SimpleNamespace(), scope)
    with pytest.raises(ValueError, match="at least 24 hours"):
        sensory.inspect_or_compact_noise_events(older_than=timedelta(hours=12))
    with pytest.raises(ValueError, match="batch_limit"):
        sensory.inspect_or_compact_noise_events(batch_limit=0)
