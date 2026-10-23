from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from eva.application.household_procedure import HouseholdProcedureModel
from eva.application.free_will import FreeWillDecision


def test_same_household_model_decides_and_original_deadline_survives():
    now=datetime(2026,10,23,12,tzinfo=UTC); context={'expires_at_epoch':int(now.timestamp())+30,
        'binding_sha256':'a'*64,'is_execution_permission':False}
    calls=[]
    class Proposal:
        def __call__(self,event,**kwargs):calls.append('isolated');return context
        def validate_current(self,value):assert value is context;calls.append('revalidate')
    def decide(event,*args,**kw):
        assert kw['household_proposal_context']==context
        calls.append('existing-model');return FreeWillDecision('respond','Model chooses',1,'model','Welkom')
    wrapper=HouseholdProcedureModel(SimpleNamespace(evaluate_sensory_event=decide),Proposal(),
        authorize=lambda:calls.append('authorize'),clock=lambda:now)
    result=wrapper.evaluate_sensory_event(object())
    assert calls.count('isolated')==calls.count('existing-model')==1
    assert result.expression_expires_at_epoch==int(now.timestamp())+30
    assert result.expression_guard_sha256=='a'*64


def test_model_delay_discards_speech_instead_of_extending_deadline():
    now=[datetime(2026,10,23,12,tzinfo=UTC)];deadline=int(now[0].timestamp())+30
    class Proposal:
        def __call__(self,*a,**kw):return {'expires_at_epoch':deadline,'binding_sha256':'a'*64}
        def validate_current(self,value):pass
    def decide(*a,**kw):now[0]+=timedelta(seconds=31);return FreeWillDecision('respond','Choice',1,'model','Late')
    wrapper=HouseholdProcedureModel(SimpleNamespace(evaluate_sensory_event=decide),Proposal(),authorize=lambda:None,clock=lambda:now[0])
    assert wrapper.evaluate_sensory_event(object()).disposition=='silence'


def test_revoked_proposal_cannot_become_a_spoken_decision():
    now=datetime.now(UTC)
    class Proposal:
        def __call__(self,*a,**kw):return {'expires_at_epoch':int(now.timestamp())+30,'binding_sha256':'a'*64}
        def validate_current(self,value):raise ValueError('revoked')
    wrapper=HouseholdProcedureModel(SimpleNamespace(evaluate_sensory_event=lambda *a,**kw:FreeWillDecision('respond','Choice',1,'model','No')),
        Proposal(),authorize=lambda:None,clock=lambda:now)
    with pytest.raises(ValueError,match='revoked'):wrapper.evaluate_sensory_event(object())
