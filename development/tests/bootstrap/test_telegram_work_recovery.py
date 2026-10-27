"""Regressions for owner search recovery and current native tool availability."""
from types import SimpleNamespace
from datetime import datetime, UTC
from uuid import UUID
import unittest
from unittest.mock import patch

from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationActionInputRejected, ConversationConflict
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.adapters.postgres import delegated_work_actions as actions_module
from eva.adapters.postgres import component_improvement_actions as component_module
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions

OWNER=UUID('33333333-3333-3333-8333-333333333333')
BINDING=UUID('44444444-4444-4444-8444-444444444444')

def turn():
    return ConversationTurn.private(tenant_id='tenant:test',installation_id='install:test',
        principal_id=str(OWNER),conversation_id='owner-chat',message_id='message-1',
        text='Find an earlier task',occurred_at='2026-10-27T10:00:00Z')

class SearchStore:
    scope=external_timeline_scope('tenant:test','install:test','test scope required')
    def __init__(self):
        self.reads=[];self.denied=False;self.unexpected=False
    def validate_admission_authority(self,**kwargs):
        if kwargs!={'owner_principal_id':OWNER,'principal_binding_id':BINDING}:
            raise AssertionError('owner binding changed')
        if self.denied:raise DelegatedWorkConflict('revoked')
    def list_owned(self,**kwargs):
        self.validate_admission_authority(**{k:kwargs[k] for k in ['owner_principal_id','principal_binding_id']})
        if kwargs.get('before_work_id')=='':
            # Execute the actual installed store's pre-connection validation.
            return PostgresDelegatedWorkStore.list_owned(object(),**kwargs)
        if kwargs.get('before_work_id')=='missing-cursor':
            raise ValueError('work search cursor is unavailable for this owner')
        if self.unexpected:raise ValueError('unexpected storage failure')
        self.reads.append(kwargs);return ()

class Timeline:
    def __init__(self,events):self.saved=events
    def events(self,key):return tuple(self.saved.get(key,()))
    def create_thread(self,*,thread_id,data,event_type,**kwargs):
        assert thread_id not in self.saved
        self.saved[thread_id]=[SimpleNamespace(event_type=event_type,payload={'data':data})]
    def transition(self,*,thread_id,data,event_type,**kwargs):
        self.saved[thread_id].append(SimpleNamespace(event_type=event_type,payload={'data':data}))

class OwnerSearchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store=SearchStore();self.events={}
        self.service=DelegatedWorkOwnerService(self.store,owner_principal_id=OWNER,
            principal_binding_id=BINDING,clock=lambda:datetime(2026,10,27,10,tzinfo=UTC))
        self.timeline=Timeline(self.events)
        self.patches=[patch.object(actions_module,'PostgresOperationalTimeline',lambda *a:self.timeline),
            patch('eva.adapters.postgres.scheduled_work.ScheduledOwnerWork',lambda service:SimpleNamespace(list=lambda:[]))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.actions=actions_module.PostgresDelegatedWorkActions(object(),self.service)
        self.source=turn()
    def call(self,cursor=None,query='example',identifier='call-1'):
        search={'query':query}
        if cursor is not None:search['before_work_id']=cursor
        return ConversationActionCall(identifier,'search_delegated_work',{'search':search})
    def test_actual_store_reproduces_empty_cursor_before_connection(self):
        with self.assertRaisesRegex(ValueError,'work search cursor is invalid'):
            PostgresDelegatedWorkStore.list_owned(object(),owner_principal_id=OWNER,
                principal_binding_id=BINDING,query='example',before_work_id='')
    def test_rejected_search_can_be_corrected_at_same_action_index(self):
        with self.assertRaises(ConversationActionInputRejected):self.actions.execute(self.source,self.call(''))
        self.assertEqual(self.events,{})
        fixed=self.call(None,identifier='corrected-call')
        receipt=self.actions.execute(self.source,fixed)
        self.assertEqual(receipt.output['operation_result']['jobs'],[])
        self.assertEqual(receipt.output['status'],'verified')
        self.assertEqual(self.actions.pending(self.source),receipt)
        self.assertEqual(self.actions.execute(self.source,fixed),receipt)
        self.assertEqual(len(self.store.reads),1)
        self.assertEqual(len(next(iter(self.events.values()))),2)
    def test_bad_query_is_recoverable_before_receipt_preparation(self):
        for query in ['', ' '*3, 17, 'x'*201]:
            with self.subTest(query_type=type(query).__name__):
                with self.assertRaises(ConversationActionInputRejected):self.actions.execute(self.source,self.call(query=query))
                self.assertEqual(self.events,{})
        self.assertEqual(self.store.reads,[])
    def test_unavailable_cursor_does_not_poison_followup_identity(self):
        with self.assertRaises(ConversationActionInputRejected):self.actions.execute(self.source,self.call('missing-cursor'))
        self.assertEqual(self.events,{})
        self.actions.execute(self.source,self.call(identifier='corrected'))
        self.assertEqual(len(self.store.reads),1)
    def test_unexpected_storage_error_is_not_masked(self):
        self.store.unexpected=True
        with self.assertRaisesRegex(ValueError,'unexpected storage failure'):
            self.actions.execute(self.source,self.call('valid-cursor'))
        self.assertEqual(self.events,{})
    def test_valid_cursor_and_owner_bounds_are_preserved(self):
        self.actions.execute(self.source,self.call('valid-cursor'))
        self.assertEqual(self.store.reads,[{'owner_principal_id':OWNER,'principal_binding_id':BINDING,
            'limit':21,'query':'example','before_work_id':'valid-cursor'}])
    def test_revoked_owner_never_reads_or_records_search(self):
        self.store.denied=True
        with self.assertRaises(DelegatedWorkConflict):self.actions.execute(self.source,self.call())
        self.assertEqual(self.events,{});self.assertEqual(self.store.reads,[])
    def test_effectful_task_keeps_prepare_before_dispatch_and_replay(self):
        starts=[]
        class Service:
            def available_for(self,_):return True
            def validate_authority(self,_):pass
            def execute(inner,operation,source,**kwargs):
                self.assertEqual(len(self.events),1)
                self.assertEqual(next(iter(self.events.values()))[0].event_type,'DELEGATED_ACTION_PREPARED')
                starts.append(operation);return {'operation':operation,'work':{'work_id':'work:one','state':'admitted'}}
        actions=actions_module.PostgresDelegatedWorkActions(object(),Service())
        call=ConversationActionCall('start','start_delegated_work',{'task':'A bounded task'})
        receipt=actions.execute(self.source,call)
        self.assertEqual(actions.pending(self.source),receipt)
        self.assertEqual(starts,['start'])

class CurrentComponentToolTests(unittest.TestCase):
    def setUp(self):
        self.evaluator=SimpleNamespace(reviewed_releases=(),final_assessment_scopes=(),
            runtime_repair_enabled=True,component_maintenance_enabled=True)
        p=patch.object(component_module,'configured_component_evaluator',lambda:self.evaluator)
        p.start();self.addCleanup(p.stop);self.source=turn();self.dispatched=[]
        timeline_patch=patch.object(component_module,'PostgresOperationalTimeline',lambda *a:Timeline({}))
        timeline_patch.start();self.addCleanup(timeline_patch.stop)
    def actions(self,guard=None):
        spec=SimpleNamespace(execution_id='exec-1',work_id='work:test')
        return component_module.ComponentImprovementActions(None,spec,self.source,lambda _:None,
            lambda _:self.dispatched.append(True),lambda:datetime(2026,10,27,10,tzinfo=UTC),repair_guard=guard)
    def catalog(self,actions):
        # Keep the full original grant, including the currently unavailable tools.
        return BackgroundConnectorActions((actions,),actions.action_names,lambda _:None,10)
    def test_private_worker_admits_full_catalog_but_cannot_offer_native_tools(self):
        a=self.actions();self.assertIn(component_module.REPAIR,a.action_names)
        names=self.catalog(a).available_action_names(self.source)
        self.assertNotIn(component_module.REPAIR,names)
        self.assertNotIn(component_module.MAINTENANCE,names)
        self.assertIn(component_module.READ,names)
    def test_direct_unbound_repair_is_rejected_before_dispatch(self):
        a=self.actions();call=ConversationActionCall('call','manage_own_runtime_repair',{'action':'describe'})
        with self.assertRaises(ConversationConflict):a.execute(self.source,call)
        self.assertEqual(self.dispatched,[])
    def test_real_native_repair_and_generic_native_maintenance_stay_available(self):
        bound=self.catalog(self.actions(SimpleNamespace(_repair_incident_id='incident',repair_role=None)))
        self.assertIn(component_module.REPAIR,bound.available_action_names(self.source))
        native=self.catalog(self.actions(SimpleNamespace(_repair_incident_id=None)))
        self.assertNotIn(component_module.REPAIR,native.available_action_names(self.source))
        self.assertIn(component_module.MAINTENANCE,native.available_action_names(self.source))
    def test_current_provider_filter_cannot_offer_another_providers_tool(self):
        class Provider:
            action_names=('own',)
            def available_for(self,_):return True
            def available_action_names(self,_):return ('foreign',)
        a=BackgroundConnectorActions((Provider(),),('own',),lambda _:None,10)
        self.assertEqual(a.available_action_names(self.source),())
    def test_ordinary_provider_without_dynamic_filter_is_unchanged(self):
        class Provider:
            action_names=('read','write')
            def available_for(self,_):return False
        a=BackgroundConnectorActions((Provider(),),('read','write'),lambda _:None,10)
        self.assertEqual(a.available_action_names(self.source),())
        a._tools[0].available_for=lambda _:True
        self.assertEqual(a.available_action_names(self.source),('read','write'))
