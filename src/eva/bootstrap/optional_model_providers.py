"""Optional private-text model selection; reuse Eva's context and durable actions."""
from copy import copy
from dataclasses import replace
from pathlib import Path
import asyncio
import importlib.util
import os

from eva.operations.model_provider_settings import ModelProviderSettings
from eva.adapters.model.contracts import ConversationModelError


class OptionalConversationModel:
    def __init__(self, baseline, control):
        self.baseline, self.control = baseline, control

    def __getattr__(self, name):
        # Research, initiative, development and status retain the configured
        # subscription route. Only the explicitly offered private text scope is
        # selectable here; this is not a second agent or tool executor.
        return getattr(self.baseline,name)

    def with_context_archive(self, archive):
        return OptionalConversationModel(self.baseline.with_context_archive(archive),self.control)

    def generate(self, turn, *args, **kwargs):
        from eva.application.voice_context import current_voice_context
        if current_voice_context() is not None or turn.audience.kind!='private_person' or not turn.conversation_id.startswith(('dashboard:','telegram:','whatsapp:')):
            return self.baseline.generate(turn,*args,**kwargs)
        state=self.control.settings.read();identifier=state['selected_id']
        if identifier is None: return self.baseline.generate(turn,*args,**kwargs)
        from eva.adapters.model.optional_backends import OptionalBackend
        row,_=self.control.settings.connection(identifier,selected=True)
        selected=copy(self.baseline)
        selected._model=row['model']
        selected._model_backend=OptionalBackend(self.control.settings,identifier,selected=True,
            max_tokens=(selected._resource_budget.max_output_tokens if selected._resource_budget else 2048),
            transport=self.control.transport)
        try:
            result=selected.generate(turn,*args,**kwargs)
            return replace(result,provider=row['kind']+':'+row['model'])
        except Exception as error:
            # Keep original failure/usage for the existing request journal; never
            # resend family content to another provider as an implicit fallback.
            if isinstance(error,ConversationModelError): raise
            raise ConversationModelError('optional model request failed') from error


class OptionalModelProviders:
    def __init__(self, baseline, directory, *, transport=None):
        self.baseline=baseline
        self.settings=ModelProviderSettings(Path(directory))
        self.transport=transport

    def status(self):
        state=self.settings.read();default=self.baseline.credential_status()
        return {'contract':'EvaModelProviders/v1','scope':'private_text_conversations',
            'selected_id':state['selected_id'],'default':{'label':'Bestaande ChatGPT-verbinding','ready':default.ready,'state':default.state},
            'connections':[{k:v for k,v in row.items() if k!='workspace_id'} | {'selected':identifier==state['selected_id']}
                for identifier,row in state['connections'].items()],
            'adapters':[{'kind':kind,'label':label,'installed':importlib.util.find_spec(module) is not None}
                for kind,label,module in [('anthropic','Claude / Anthropic','anthropic'),('openai_compatible','OpenAI-compatibele API','openai')]],
            'meaning':'Optioneel voor privétekst via website en Telegram. Dezelfde Eva, geheugen en acties; achtergrondwerk/initiatief blijven Astra. Spraak blijft bij de bestaande uitvoerder. Aanmelding met API-sleutel; providerkosten apart. Uitschakelen stopt nieuwe aanvragen, geen reeds begonnen actie.'}

    def check(self, identifier, *, admission_check=None):
        from typing import Literal
        from pydantic import BaseModel
        from pydantic_ai import Agent, ToolOutput
        from eva.adapters.model.optional_backends import OptionalBackend
        class Confirmation(BaseModel):
            echoed: Literal['eva-provider-check']
        usage=None
        try:
            backend=OptionalBackend(self.settings,identifier,transport=self.transport,max_tokens=256,admission_check=admission_check)
            async def run():
                agent=Agent(backend,output_type=ToolOutput(Confirmation),retries=0)
                return await agent.run('Return echoed=eva-provider-check using the provided output tool. No other work.')
            result=asyncio.run(run());reported=result.usage
            usage={'model_attempts':reported.requests,'input_tokens':reported.input_tokens,'output_tokens':reported.output_tokens}
            ready=isinstance(result.output,Confirmation) and reported.requests==1
        except Exception:
            ready=False  # Provider exception bodies may contain credentials or prompts.
        if admission_check is not None: admission_check()
        self.settings.record_check(identifier,ready=ready,usage=usage)
        return self.status()


def attach_optional_models(runtime):
    existing=getattr(runtime,'_model_provider_control',None)
    if existing is not None: return existing
    conversation=getattr(runtime,'_conversation',None)
    if conversation is None or getattr(runtime,'_dashboard_owner_principal_id',None) is None: return None
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    baseline=getattr(conversation,'_model',None)
    if not isinstance(baseline,PydanticConversationModel): return None
    control=OptionalModelProviders(baseline,os.environ.get('EVA_MODEL_PROVIDER_DIRECTORY','/var/lib/eva/work/model-providers'))
    conversation._model=OptionalConversationModel(baseline,control)
    if getattr(conversation,'_contact_model',None) is not None:
        conversation._contact_model=OptionalConversationModel(conversation._contact_model,control)
    runtime._model_provider_control=control
    return control
