from __future__ import annotations

import re
from uuid import UUID

import pytest
import sqlalchemy as sa

from tests.support.postgres_action_chain_effect_advances import (
    PostgresActionChainEffectAdvancer,
)
from eva.adapters.postgres.action_chain_effect_cancellations import (
    PostgresActionChainEffectCanceller,
)
from eva.adapters.postgres.action_chain_effect_readback_results import (
    PostgresActionChainEffectReadbackResultStore,
)
from eva.adapters.postgres.action_chain_effect_readbacks import (
    PostgresActionChainEffectReadbackPreparer,
)
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
)
from eva.adapters.postgres.action_chain_effect_stop_requests import (
    PostgresActionChainEffectStopRequester,
)
from eva.adapters.postgres.action_chain_effects import PostgresActionChainEffectPreparer
from eva.adapters.postgres.action_chain_leases import PostgresActionChainLeases
from tests.support.postgres_action_chain_read_advances import (
    PostgresActionChainReadAdvancer,
)
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
)
from eva.adapters.postgres.action_chain_reads import PostgresActionChainReadPreparer
from eva.adapters.postgres.operational_timeline import TimelineScope


@pytest.mark.parametrize(
    ("store", "message"),
    (
        (
            PostgresActionChainEffectAdvancer,
            "action-chain effect advances require PostgreSQL",
        ),
        (
            PostgresActionChainEffectCanceller,
            "action-chain effect cancellation requires PostgreSQL",
        ),
        (
            PostgresActionChainEffectPreparer,
            "action-chain effect preparation requires PostgreSQL",
        ),
        (
            PostgresActionChainEffectReadbackPreparer,
            "effect readback preparation requires PostgreSQL",
        ),
        (
            PostgresActionChainEffectReadbackResultStore,
            "effect readback result storage requires PostgreSQL",
        ),
        (
            PostgresActionChainEffectResultStore,
            "action-chain effect results require PostgreSQL",
        ),
        (
            PostgresActionChainEffectStopRequester,
            "action-chain effect stop requests require PostgreSQL",
        ),
        (PostgresActionChainLeases, "action-chain leases require PostgreSQL"),
        (
            PostgresActionChainReadAdvancer,
            "action-chain read advances require PostgreSQL",
        ),
        (
            PostgresActionChainReadPreparer,
            "action-chain read preparation requires PostgreSQL",
        ),
        (
            PostgresActionChainReadResultStore,
            "action-chain read results require PostgreSQL",
        ),
    ),
)
def test_all_action_chain_stores_keep_their_exact_postgres_gate(store, message):
    scope = TimelineScope(UUID(int=1), UUID(int=2))
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        store(sa.create_engine("sqlite://"), scope)
