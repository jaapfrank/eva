"""Bound the model-facing task overview without altering owner data."""
import copy
import json
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from eva.application.conversation import ConversationActionCall
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.postgres.delegated_work_actions import _model_work_overview

class CompactOwnerOverviewTests(unittest.TestCase):
    def test_all_records_statuses_and_authoritative_identifiers_survive(self):
        original={"contract":"EvaDelegatedWorkOwnerResult/v1","operation":"list","truncated":True,
          "jobs":[{"work_id":"old","state":"ambiguous","title":"Task","task_preview":"previous request",
                    "generation":1,"parent_work_id":None,"result_available":False,
                    "stop_requested":False,"scope":"private_digital_worker", "usage":{"input_tokens":100},
                    "error_fingerprint":"a"*64,"failure_description":"Outcome uncertain"},
                   {"work_id":"child","state":"failed","generation":2,"parent_work_id":"old"}],
          "planning":{"truncated":False,"schedules":[{"schedule_id":"schedule:1","state":"dispatched",
            "task":"x"*400,"work_id":"child","run_at":None,"dependency":{"state":"none"},"can_cancel":False}]}}
        saved=copy.deepcopy(original);out=_model_work_overview(original)
        self.assertEqual(original,saved)
        self.assertEqual([(j["work_id"],j["state"],j["generation"]) for j in out["jobs"]],[("old","ambiguous",1),("child","failed",2)])
        self.assertEqual(out["jobs"][1]["parent_work_id"],"old")
        self.assertEqual(out["jobs"][0]["failure_description"],"Outcome uncertain")
        self.assertTrue(out["truncated"]);self.assertTrue(out["summary_only"])
        self.assertEqual(out["planning"]["schedules"][0]["work_id"],"child")
        self.assertEqual(len(out["planning"]["schedules"][0]["task_preview"]),160)
        self.assertTrue(out["planning"]["schedules"][0]["task_truncated"])
        self.assertEqual(_model_work_overview(out),out)
        self.assertIn("get_delegated_work",out["details_guidance"])
        self.assertIn("list_scheduled_work",out["details_guidance"])
        self.assertNotIn("error_fingerprint",out["jobs"][0])
        self.assertNotIn("usage",out["jobs"][0])
        self.assertLess(len(json.dumps(out)),len(json.dumps(original)))

    def test_only_list_action_gets_summary_and_replay_is_stable(self):
        result={"contract":"EvaDelegatedWorkOwnerResult/v1","operation":"list","jobs":[
            {"work_id":"w1","state":"ambiguous","title":"task","usage":{"input_tokens":1000}}],
            "planning":{"schedules":[],"truncated":False},"truncated":False}
        service=Mock();service.execute.return_value=result
        actions=PostgresDelegatedWorkActions(object(),service)
        rows={}
        class Timeline:
            def events(self,key):return tuple(rows.get(key,()))
            def create_thread(self,*,thread_id,data,event_type,**kw):
                rows[thread_id]=[SimpleNamespace(event_type=event_type,payload={"data":data})]
            def transition(self,*,thread_id,data,event_type,**kw):
                rows[thread_id].append(SimpleNamespace(event_type=event_type,payload={"data":data}))
        actions._timeline=lambda turn:Timeline()
        turn=SimpleNamespace(tenant_id="tenant",installation_id="install",principal_id="owner",
                             conversation_id="chat",message_id="msg")
        call=ConversationActionCall("list-call","list_delegated_work",{})
        receipt=actions.execute(turn,call)
        self.assertTrue(receipt.output["operation_result"]["summary_only"])
        self.assertNotIn("usage",receipt.output["operation_result"]["jobs"][0])
        self.assertEqual(actions.execute(turn,call),receipt)
        self.assertEqual(service.execute.call_count,1)
        self.assertIn("usage",result["jobs"][0])
        full={"contract":"EvaDelegatedWorkOwnerResult/v1","operation":"get","work":{"work_id":"w1","usage":{"input_tokens":1000}}}
        service.execute.return_value=full
        get=actions.execute(turn,ConversationActionCall("get-call","get_delegated_work",{"work_id":"w1"}),step_index=1)
        self.assertEqual(get.output["operation_result"],full)

    def test_unknown_shape_falls_back_without_hiding_data(self):
        for v in [None,{}, {"jobs":[],"planning":None},
                  {"jobs":[None],"planning":{"schedules":[]}},
                  {"jobs":[],"planning":{"schedules":[None]}}]:
            with self.subTest(value=v):self.assertIs(_model_work_overview(v),v)

if __name__ == "__main__":
    unittest.main()
