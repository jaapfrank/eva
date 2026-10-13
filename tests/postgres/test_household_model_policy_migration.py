"""A model migration retains explicitly selected results, never old execution."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from eva.bootstrap.household_research_policy import HouseholdResearchAuthority
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from tests.postgres.test_household_public_work import setup, start
from tests.postgres.test_scheduled_failure_return import database


def test_registered_result_survives_model_policy_change_without_execution(database, monkeypatch):
    x = setup(database, "model-policy-retained")
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    store = x.server.store
    origin = x.service.origin(x.turn)
    work = store.load(work_id, **x.service._owner, household_origin=origin)
    old = x.server.authority
    profile = replace(old.profile, configuration_sha256="b" * 64)
    policy = old.policy.model_copy(update={
        "provider_configuration_sha256": profile.configuration_sha256,
        "retained_read_spec_sha256": [canonical_sha256(work.spec.to_payload())],
    })
    policy = type(policy).model_validate_json(policy.model_dump_json())
    current = [policy]
    authority = HouseholdResearchAuthority(
        voice_authority=old.voice, policy_loader=lambda: current[0],
        budget_loader=old._budget_loader, provider_validator=lambda: None,
        profile=profile, clock=old.clock,
    )
    store._household_authorizer = authority.validate_spec
    assert store.load(work_id, **x.service._owner, household_origin=origin).result == work.result
    with pytest.raises(ValueError):
        authority.validate_spec(work.spec)
    unlisted = work.spec.model_copy(update={"work_id": "work:unlisted"})
    with pytest.raises(ValueError):
        authority.validate_read_spec(unlisted)

    monkeypatch.setattr("eva.adapters.postgres.household_work_sessions.verify_origin",
                        lambda *a, **kw: {"native_conversation_key": "verified-native-session"})
    request = origin.model_copy(update={"research_policy_sha256": authority.sha256})
    with database.begin() as connection:
        store._authorize_household_request(work, connection, origin=origin, request_origin=request)
        for update in ({"actor_id": "household-device:another"},
                       {"device_id": "another-device"},
                       {"research_policy_sha256": "c" * 64}):
            with pytest.raises(DelegatedWorkNotFound):
                store._authorize_household_request(work, connection, origin=origin,
                    request_origin=request.model_copy(update=update))
    current[0] = policy.model_copy(update={"state": "revoked"})
    with pytest.raises(ValueError):
        authority.validate_read_spec(work.spec)
