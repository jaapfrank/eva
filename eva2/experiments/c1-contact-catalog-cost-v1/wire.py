import json,sqlalchemy as sa
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.providers.runtime import build_external_provider_bundle
from eva.bootstrap.contact_catalog import discoverable_contact_actions
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.model.pydantic_conversation import _descriptor_tool,_ACTION_TOOLS,_MEMORY_SEARCH_TOOL
s=RuntimeSettings.from_environment();e=sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn));b=build_external_provider_bundle(s.external_provider,e)
tools={**_ACTION_TOOLS,**{x.name:_descriptor_tool(x) for x in b.conversation_tools},**{x.name:_descriptor_tool(x) for x in PostgresDelegatedWorkActions.conversation_tools(include_research=True,include_digital=True)}}

import asyncio,httpx2
from pydantic_ai import Agent,DeferredToolRequests
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider,OpenAICodexProviderSettings
captured=[]
async def handler(request):
    body=json.loads(request.content)
    definitions=body.get('tools',[])
    captured.append({'model':body.get('model'),'bodyBytes':len(request.content),'toolBytes':len(json.dumps(definitions).encode()),'toolTypes':[x.get('type') for x in definitions],'deferredCount':sum(x.get('defer_loading',False) for x in definitions),'toolCount':len(definitions),'toolSample':definitions[:2]})
    return httpx2.Response(400,json={'error':{'message':'offline capture complete','type':'invalid_request_error'}})
async def main():
    async with OpenAICodexProvider(OpenAICodexProviderSettings(endpoint='https://example.invalid/responses',model_name=s.conversation_contact_model),credential_loader=lambda:('synthetic-token','synthetic-account'),network_transport=httpx2.MockTransport(handler)) as session:
        defs=[ToolDefinition(name=t['name'],description=t['description'],parameters_json_schema=t['parameters'],strict=True,defer_loading=True) for t in tools.values()]
        try:
            await Agent(session.model,instructions='Synthetic offline catalog capture',retries=0).run('Hallo',toolsets=[ExternalToolset(defs,id='eva-durable-actions')],output_type=[str,DeferredToolRequests])
        except Exception as error:
            print(json.dumps({'capture':captured,'termination':type(error).__name__,'network':'MockTransport only; no external request'},ensure_ascii=False))
    e.dispose()
asyncio.run(main())
