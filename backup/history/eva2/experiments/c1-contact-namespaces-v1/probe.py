import json,sqlalchemy as sa
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.providers.runtime import build_external_provider_bundle
from eva.bootstrap.contact_catalog import discoverable_contact_actions
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.model.pydantic_conversation import _descriptor_tool,_ACTION_TOOLS,_MEMORY_SEARCH_TOOL
s=RuntimeSettings.from_environment();e=sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn));b=build_external_provider_bundle(s.external_provider,e)
tools={**_ACTION_TOOLS,**{x.name:_descriptor_tool(x) for x in b.conversation_tools},**{x.name:_descriptor_tool(x) for x in PostgresDelegatedWorkActions.conversation_tools(include_research=True,include_digital=True)}}

import asyncio,time
from pathlib import Path
from pydantic_ai import Agent,DeferredToolRequests
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset
from pydantic_ai.messages import PartStartEvent,PartDeltaEvent,TextPart,TextPartDelta
from pydantic_ai.usage import UsageLimits
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider,OpenAICodexProviderSettings
adapter=PydanticConversationModel(auth_file=Path(s.conversation_auth_file),model=s.conversation_contact_model)
import eva.adapters.model.openai_codex_provider as provider_module
original=provider_module._codex_compatible_request
def grouped(request):
    import httpx2
    request=original(request)
    body=json.loads(request.content)
    body['tools']=[{'type':'namespace','name':t['name'],'description':t['name'].replace('_',' '),'tools':[t]} if t.get('type')=='function' and t.get('defer_loading') is True else t for t in body.get('tools',[])]
    headers=dict(request.headers);headers.pop('content-length',None)
    return httpx2.Request(request.method,request.url,headers=headers,content=json.dumps(body).encode(),extensions=request.extensions)
provider_module._codex_compatible_request=grouped
async def main():
    for prompt in ("Hallo.", "Wat heb je uit eigen initiatief al afgemaakt? Haal het nieuwste bewaarde resultaat op. Begin niets nieuws."):
        enabled=True
        start=time.monotonic(); first=None
        async def capture(ctx,events):
            nonlocal first
            async for event in events:
                if first is None and ((isinstance(event,PartStartEvent) and isinstance(event.part,TextPart) and event.part.content) or (isinstance(event,PartDeltaEvent) and isinstance(event.delta,TextPartDelta) and event.delta.content_delta)):
                    first=round(time.monotonic()-start,3)
        settings=OpenAICodexProviderSettings(endpoint=adapter._endpoint,model_name=s.conversation_contact_model,timeout_seconds=40,reasoning_effort=s.conversation_contact_reasoning_effort)
        async with OpenAICodexProvider(settings,credential_loader=adapter._load_auth) as session:
            defs=[ToolDefinition(name=t['name'],description=t['description'],parameters_json_schema=t['parameters'],strict=True,defer_loading=True) for t in tools.values()] if enabled else []
            try:
                result=await Agent(session.model,instructions=('Je bent Eva, een vriendelijke digitale huisgenoot. Beantwoord deze begroeting kort en natuurlijk in het Nederlands. Voer geen handelingen uit.' if prompt=='Hallo.' else 'Je bent Eva. Kies de aangeboden tool die nodig is om de vraag te beantwoorden; verzin geen resultaten. Begin geen nieuw werk.'),retries=0).run(prompt,toolsets=[ExternalToolset(defs,id='eva-durable-actions')] if defs else [],output_type=[str,DeferredToolRequests] if defs else str,usage_limits=UsageLimits(request_limit=1,tool_calls_limit=1),event_stream_handler=capture)
                raw=session.consume_raw_usage()
                u=result.usage
                print(json.dumps({'case':prompt,'model':s.conversation_contact_model,'reasoning':s.conversation_contact_reasoning_effort,'toolCount':len(defs),'inputTokens':raw.input_tokens,'outputTokens':raw.output_tokens,'cachedTokens':getattr(u,'cache_read_tokens',None),'requests':u.requests,'firstTextSeconds':first,'totalSeconds':round(time.monotonic()-start,3),'answer':result.output if isinstance(result.output,str) else None,'calls':[{'name':c.tool_name,'arguments':c.args_as_dict()} for c in result.output.calls] if isinstance(result.output,DeferredToolRequests) else [],'externalActionsExecuted':0}),flush=True)
            except Exception as error:
                print(json.dumps({'case':enabled,'error':type(error).__name__}),flush=True)
                break
    e.dispose()
asyncio.run(main())
