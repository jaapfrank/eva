"""Regressions for preserving unknown prior effects during owner resumption."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID
from datetime import datetime, UTC
from eva.application.conversation import ConversationTurn
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict, DelegatedWorkNotFound
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService

OWNER=UUID("33333333-3333-3333-8333-333333333333")
BINDING=UUID("44444444-4444-4444-8444-444444444444")

def turn():
    return ConversationTurn.private(tenant_id="tenant:test",installation_id="install:test",
        principal_id=str(OWNER),conversation_id="owner-chat",message_id="message-1",
        text="inspect the previous task",occurred_at="2026-10-28T12:00:00Z")

class FakeDigitalSpec:
    work_id="previous"
    generation=1

class OtherSpec(FakeDigitalSpec):
    pass

class UncertainResumeTests(unittest.TestCase):
    def setUp(self):
        self.previous=SimpleNamespace(state="ambiguous",call_id="earlier-call",usage=None,
            spec=FakeDigitalSpec())
        self.store=Mock()
        self.store.scope=external_timeline_scope("tenant:test","install:test","test scope")
        self.store.load.side_effect=lambda work_id,**kw: self.previous if work_id=="previous" else (_ for _ in ()).throw(DelegatedWorkNotFound("absent"))
        self.class_patch=patch("eva.bootstrap.delegated_work_owner_service.DelegatedDigitalWorkSpec",FakeDigitalSpec)
        self.class_patch.start();self.addCleanup(self.class_patch.stop)
        self.service=DelegatedWorkOwnerService(self.store,owner_principal_id=OWNER,
            principal_binding_id=BINDING,clock=lambda:datetime(2026,10,28,12,tzinfo=UTC))
        self.service._view=Mock(return_value={"work_id":"previous","state":"ambiguous"})

    def test_uncertain_effects_never_create_another_attempt(self):
        for state,call_id,usage in [
            ("ambiguous","call",None),("call_started","call",None),
            ("stopped","call",None),
            ("stopped","call",SimpleNamespace(measurement="partial",completed_model_responses=0)),
            ("stopped","call",SimpleNamespace(measurement="measured",completed_model_responses=1)),
        ]:
            with self.subTest(state=state,usage=usage):
                self.previous.state=state;self.previous.call_id=call_id;self.previous.usage=usage
                with patch("eva.bootstrap.scheduled_admission.scheduled_creation_time",
                           side_effect=AssertionError("unexpected resume planning")):
                    out=self.service.execute("resume",turn(),work_id="previous",request_key="test")
                self.assertEqual(out["status"],"not_resumable")
                self.assertFalse(out["created"]);self.assertTrue(out["prior_outcome_unknown"])
                self.assertIn("onzekere uitkomst",out["guidance"])
                self.store.admit.assert_not_called()
                self.assertEqual(self.store.load.call_args_list[-2].args,("previous",))
                self.assertEqual(self.store.load.call_args_list[-1].kwargs,dict(owner_principal_id=OWNER,principal_binding_id=BINDING))

    def test_existing_resume_is_returned_without_starting_another(self):
        child=SimpleNamespace(state="ambiguous",call_id="child-call",usage=None,spec=FakeDigitalSpec())
        self.store.load.side_effect=lambda work_id,**kw: self.previous if work_id=="previous" else child
        self.service._view.side_effect=lambda obj,**kw: {"work_id":"previous" if obj is self.previous else "existing"}
        out=self.service.execute("resume",turn(),work_id="previous",request_key="test")
        self.assertEqual(out["status"],"not_resumable");self.assertFalse(out["created"])
        self.assertEqual(out["work"]["work_id"],"existing");self.store.admit.assert_not_called()

    def test_denied_owner_cannot_inspect_or_resume(self):
        self.store.load.side_effect=DelegatedWorkConflict("revoked")
        with self.assertRaises(DelegatedWorkConflict):
            self.service.execute("resume",turn(),work_id="previous",request_key="test")
        self.store.admit.assert_not_called();self.service._view.assert_not_called()

    def test_known_failure_and_stopped_without_call_keep_existing_route(self):
        sentinel=RuntimeError("reached normal resume planning")
        for state,call_id,usage in [("failed",None,None),("stopped",None,None)]:
            with self.subTest(state=state,call_id=call_id):
                self.previous.state=state;self.previous.call_id=call_id;self.previous.usage=usage
                self.previous.spec=FakeDigitalSpec()
                with patch("eva.bootstrap.scheduled_admission.scheduled_creation_time",side_effect=sentinel):
                    with self.assertRaisesRegex(RuntimeError,"reached normal resume planning"):
                        self.service.execute("resume",turn(),work_id="previous",request_key="test")
                self.store.admit.assert_not_called()

    def test_other_spec_keeps_existing_route(self):
        self.previous.spec=OtherSpec()
        with patch("eva.bootstrap.scheduled_admission.scheduled_creation_time",side_effect=RuntimeError("normal route")):
            with self.assertRaisesRegex(RuntimeError,"normal route"):
                self.service.execute("resume",turn(),work_id="previous",request_key="test")
        self.store.admit.assert_not_called()

if __name__ == "__main__":
    unittest.main()
