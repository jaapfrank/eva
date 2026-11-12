"""A longer private-work profile must not invalidate household voice work."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from tests.contract.test_delegated_digital_work import household_spec
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.application.conversation import ConversationTurn
from eva.application.memory_audience import HouseholdConsentReference,MemoryAudience
from eva.bootstrap.household_digital_work import admit_household_digital
from eva.contracts.timestamps import parse_utc_timestamp

def test_household_admission_caps_long_profile_without_widening_household_contract():
    profile,example=household_spec()
    profile=replace(profile,timeout_seconds=1200)
    origin=example.origin
    turn=ConversationTurn(origin.tenant_id,origin.installation_id,origin.actor_id,
        origin.conversation_id,origin.message_id,'Check the weather',origin.received_at,
        MemoryAudience.household(principal_id=origin.actor_id,household_id=origin.household_id,
            consent_ref=HouseholdConsentReference(origin.actor_id,origin.household_id,origin.consent_operation_id)))
    class Admitted(Exception):pass
    class Store:
        def load(self,*a,**kw):raise DelegatedWorkNotFound('not yet admitted')
        def admit(self,*,spec,**kw):
            assert (parse_utc_timestamp(spec.deadline)-parse_utc_timestamp(spec.created_at)).total_seconds()==300
            assert spec.grant.timeout_seconds==1200
            assert spec.origin==origin
            raise Admitted()
    authority=SimpleNamespace(validate_actor=lambda _:None,
        policy=SimpleNamespace(expires_at='2026-09-24T12:00:00Z'),clock=lambda:parse_utc_timestamp(origin.received_at))
    service=SimpleNamespace(digital_profile=profile,validate_authority=lambda _:None,origin=lambda _:origin,
        server=SimpleNamespace(authority=authority),store=Store(),
        _owner={'owner_principal_id':example.owner_principal_id,'principal_binding_id':example.principal_binding_id})
    with pytest.raises(Admitted):admit_household_digital(service,turn,'weather-test','Check the weather')
