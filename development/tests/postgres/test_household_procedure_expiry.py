from datetime import timedelta
import pytest
from eva.application.free_will import FreeWillDecision
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_household_observation_initiative import _shared,_binding,_signal


def prepared(fixture, name):
    auth, old, interests, owner, originals, path, policy=fixture
    owner.stop(old.intent_id,now=NOW);auth.policy();binding=_binding(old.tenant_id)
    store=_shared(owner._engine,old.tenant_id,binding,auth.policy,old.principal_id)
    event=_signal(name);originals.retain(event);store.schedule_household_observation(event,now=NOW)
    claim=store.claim_next(now=NOW)
    store.complete(claim,FreeWillDecision('respond','Reviewed proposal',1,'model','Welcome',
        expression_expires_at_epoch=int(NOW.timestamp())+30,expression_guard_sha256='c'*64),now=NOW)
    return store,binding


def test_expiry_is_durable_before_claim_and_checked_after_claim(costless_native):
    store,binding=prepared(costless_native,'procedure-expiry')
    claim=store.claim_next_household_expression(binding=binding.to_payload(),now=NOW+timedelta(seconds=1))
    assert claim is not None
    assert store._prepared_expression(claim.thread_id)['expression_guard_sha256']=='c'*64
    store.authorize_household_expression(claim,binding=binding.to_payload(),now=NOW+timedelta(seconds=29))
    with pytest.raises(CheckpointConflict):
        store.authorize_household_expression(claim,binding=binding.to_payload(),now=NOW+timedelta(seconds=30))


def test_unclaimed_expired_proposal_is_not_offered(costless_native):
    store,binding=prepared(costless_native,'procedure-expiry-unclaimed')
    assert store.claim_next_household_expression(binding=binding.to_payload(),now=NOW+timedelta(seconds=30)) is None
