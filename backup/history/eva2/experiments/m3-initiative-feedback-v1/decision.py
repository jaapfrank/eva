import json,dataclasses,time
from datetime import datetime,UTC
from pathlib import Path
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import IdentityProfile
from eva.application.memory import PresenceMemory
from eva.contracts.memory import MemoryRecall
from eva.contracts.canonical_json import canonical_sha256
from eva.application.sensory import SensoryEvent
s=RuntimeSettings.from_environment();stamp=datetime.now(UTC).replace(microsecond=0);at=stamp.isoformat().replace('+00:00','Z')
recall=MemoryRecall(schema_version=1,contract='MemoryRecall/v1',recall_id='m3-feedback:empty-recall',query_purpose='private_opportunity_review',tenant_id='m3-feedback',principal_id='synthetic',conversation_id='m3-feedback:new-review',project_id=None,requested_valid_at=at,admitted_fact_ids_sha256=canonical_sha256([]),selected=[],excluded_reasons={},projection_version='hindsight-0.9.1-v1',scope_sha256=canonical_sha256({'kind':'person'}),created_at=at)
memory=PresenceMemory(recall,(),None)
identity=IdentityProfile(s.identity_name,s.identity_description,('Zeg eerlijk wat je wel en niet weet.',),110)
model=PydanticConversationModel(auth_file=Path(s.conversation_auth_file),model=s.conversation_model,reasoning_effort=s.conversation_reasoning_effort,resource_budget=s.runtime_resource_budget)
event=SensoryEvent('initiative-review','m3-feedback:next-review','scheduled_review',stamp,{'reason':'ordinary quiet-time review'})
started=time.monotonic()
result=model.evaluate_sensory_event(event,identity,memory,initiative_history=tuple(HISTORY),initiative_capabilities=('save_workspace_document',))
print(json.dumps({'seconds':time.monotonic()-started,'decision':dataclasses.asdict(result),'executed':False},ensure_ascii=False,default=str),flush=True)
