"""Bounded document data from a real conversation; host identity stays outside."""
from dataclasses import asdict, dataclass
from eva.application.conversation import ConversationConflict
from eva.contracts.canonical_json import canonical_sha256


@dataclass(frozen=True, slots=True)
class DocumentTaskInput:
    title: str
    first_document: str
    second_document: str

    @classmethod
    def from_arguments(cls, arguments):
        if not isinstance(arguments, dict) or set(arguments) != {
            "title", "first_document", "second_document"
        }:
            raise ConversationConflict("document task accepts only title and two texts")
        for key, maximum in (("title", 300), ("first_document", 12000), ("second_document", 12000)):
            value = arguments[key]
            if not isinstance(value, str) or not value.strip():
                raise ConversationConflict("document task text is empty")
            try:
                length = len(value.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as error:
                raise ConversationConflict("document task text is not UTF-8") from error
            if length > maximum:
                raise ConversationConflict("document task text exceeds byte limit")
        return cls(**arguments)

    @property
    def sha256(self):
        return canonical_sha256({"contract": "EvaDocumentTaskInput/v1", **asdict(self)})

    def bound_identity(self, turn, call_id):
        # Caller must load and authorize this exact ingress before persisting.
        if not isinstance(call_id, str) or not call_id.strip():
            raise ConversationConflict("document task call identity is absent")
        return canonical_sha256({"contract": "EvaDocumentTaskIngress/v1",
            "tenant_id": turn.tenant_id, "installation_id": turn.installation_id,
            "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
            "message_id": turn.message_id, "project_id": turn.project_id,
            "call_id": call_id})


@dataclass(frozen=True, slots=True)
class DocumentTaskSlot:
    slot_id: str
    tool_configuration_sha256: str

    def __post_init__(self):
        import re
        if not isinstance(self.slot_id,str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,64}",self.slot_id) is None:
            raise ConversationConflict("document slot identity invalid")
        if not isinstance(self.tool_configuration_sha256,str) or re.fullmatch(r"[0-9a-f]{64}",self.tool_configuration_sha256) is None:
            raise ConversationConflict("document slot tool binding invalid")


class DocumentTaskPool:
    """Host-pinned legacy slots and optional task-isolated shared provider."""
    def __init__(self, slots, dynamic_tool_configuration_sha256=None):
        self.slots=tuple(slots)
        self.dynamic_tool_configuration_sha256 = dynamic_tool_configuration_sha256
        if dynamic_tool_configuration_sha256 is not None:
            DocumentTaskSlot("dynamic", dynamic_tool_configuration_sha256)
            if dynamic_tool_configuration_sha256 in {slot.tool_configuration_sha256 for slot in self.slots}:
                raise ConversationConflict("dynamic provider must be separately pinned")
        if not 1 <= len(self.slots) <= 16 or len({s.slot_id for s in self.slots})!=len(self.slots) or len({s.tool_configuration_sha256 for s in self.slots})!=len(self.slots):
            raise ConversationConflict("document pool needs distinct bounded provider instances")

    @classmethod
    def from_json(cls, raw):
        import json
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ConversationConflict("document pool repeats field")
                result[key]=value
            return result
        try:
            if not isinstance(raw,str) or len(raw.encode())>16384:raise ValueError("size")
            payload=json.loads(raw,object_pairs_hook=unique)
            if not isinstance(payload, dict):
                raise ValueError("shape")
            if payload.get("contract") == "EvaDocumentTaskPool/v1" and set(payload) == {"contract", "slots"}:
                return cls(DocumentTaskSlot(**item) for item in payload["slots"])
            if (payload.get("contract") == "EvaDocumentTaskPool/v2"
                    and set(payload) == {"contract", "slots", "dynamic_tool_configuration_sha256"}
                    and isinstance(payload["dynamic_tool_configuration_sha256"], str)):
                return cls((DocumentTaskSlot(**item) for item in payload["slots"]),
                    payload["dynamic_tool_configuration_sha256"])
            raise ValueError("shape")
        except (ValueError,TypeError,KeyError) as error:
            raise ConversationConflict("document pool is invalid") from error

    @property
    def legacy_sha256(self):
        return canonical_sha256({"contract":"EvaDocumentTaskPool/v1","slots":[asdict(s) for s in self.slots]})

    @property
    def sha256(self):
        if self.dynamic_tool_configuration_sha256 is None:
            return self.legacy_sha256
        return canonical_sha256({"contract": "EvaDocumentTaskPool/v2",
            "slots": [asdict(s) for s in self.slots],
            "dynamic_tool_configuration_sha256": self.dynamic_tool_configuration_sha256})

    def validate_claim(self, claim):
        import re
        task_id = claim.get("task_id")
        if not isinstance(task_id, str) or re.fullmatch(r"[0-9a-f]{64}", task_id) is None:
            raise ConversationConflict("document claim task identity differs")
        legacy = next((s for s in self.slots if s.slot_id == claim.get("slot_id")), None)
        if legacy is not None:
            expected_pool, expected_tool = self.legacy_sha256, legacy.tool_configuration_sha256
        elif self.dynamic_tool_configuration_sha256 is not None and claim.get("slot_id") == "task:" + task_id:
            expected_pool, expected_tool = self.sha256, self.dynamic_tool_configuration_sha256
        else:
            raise ConversationConflict("document claim provider is unavailable")
        if (claim.get("pool_sha256") != expected_pool
                or claim["plan"]["tool_configuration_sha256"] != expected_tool
                or claim["plan"]["goal_id"] != "document-task:" + task_id):
            raise ConversationConflict("document claim host binding differs")
        return claim

    def claims(self, store, *, after=""):
        while True:
            page = store.document_claims(after=after)
            if not page:
                return
            for cursor, claim in page:
                if claim["slot_id"] in {s.slot_id for s in self.slots} or claim["slot_id"].startswith("task:"):
                    yield cursor, self.validate_claim(claim)
            after = page[-1][0]

    def find(self, store, task_id):
        for slot in self.slots:
            claim = store.document_slot(slot.slot_id)
            if claim is not None and claim.get("task_id") == task_id:
                return self.validate_claim(claim)
        if self.dynamic_tool_configuration_sha256 is not None:
            claim = store.document_slot("task:" + task_id)
            if claim is not None:
                return self.validate_claim(claim)
        return None

    def claim(self, store, *, task, turn, call_id, authority_sha256, tenant_id, installation_id, owner_principal_id=None, return_binding_factory=None):
        from datetime import datetime, timedelta
        from eva.application.durable_goals import DurableGoalSpec, GoalWakeCondition
        from eva.providers.document_goal_plan import DocumentGoalPlan, PreparedDocumentGoal
        owner = turn.principal_id if owner_principal_id is None else owner_principal_id
        task_id=task.bound_identity(turn,call_id)
        at=datetime.fromisoformat(turn.occurred_at.replace('Z','+00:00'))
        old = self.find(store, task_id)
        if old is not None:
            if old.get("input_sha256") != task.sha256:
                raise ConversationConflict("document task replay differs")
            return old
        if self.dynamic_tool_configuration_sha256 is None:
            candidates = [(slot.slot_id, slot.tool_configuration_sha256, self.legacy_sha256) for slot in self.slots]
        else:
            candidates = [("task:" + task_id, self.dynamic_tool_configuration_sha256, self.sha256)]
        for slot_id, tool_sha256, pool_sha256 in candidates:
            goal_id='document-task:'+task_id
            goal=DurableGoalSpec(goal_id,owner,task.title,
                'Save the first private document from the bound task input.',None,None,None,
                owner,GoalWakeCondition('document-task',goal_id,'ready','yes',at+timedelta(days=1)),at,
                'Save the second private document from the same bound task input.')
            plan=DocumentGoalPlan(goal.goal_id,goal.sha256,owner,tenant_id,installation_id,
                authority_sha256,tool_sha256,
                '/proof-state/'+task_id+'-first.txt','/proof-state/'+task_id+'-second.txt',
                task.first_document,'','',task.second_document)
            definition=PreparedDocumentGoal(goal,plan.sha256,turn.project_id)
            payload={'task_id':task_id,'input_sha256':task.sha256,'input':asdict(task),
                'pool_sha256':pool_sha256,'slot_id':slot_id,
                'source':{'tenant_id':turn.tenant_id,'installation_id':turn.installation_id,
                    'principal_id':turn.principal_id,'conversation_id':turn.conversation_id,
                    'message_id':turn.message_id,'audience':{'kind':'private_person','principal_id':turn.principal_id}},
                'plan':plan.to_payload(),'definition':definition.to_payload()}
            if return_binding_factory is not None:
                binding = return_binding_factory()
                if binding is not None:
                    payload['return_binding'] = binding
            actual=store.claim_document_slot(slot_id,payload,at=at)
            if actual==payload:return actual
            if actual.get('task_id')==task_id:
                raise ConversationConflict("document task replay binding differs")
        raise ConversationConflict("geen vrij projectslot")
