"""Upstream PydanticAI providers with explicit credentials and no SDK retries."""
from contextlib import asynccontextmanager
from pydantic_ai.models.wrapper import WrapperModel


class OptionalBackend(WrapperModel):
    supports_deferred_loading = False

    def __init__(self, settings, identifier, *, selected=False, transport=None, max_tokens=2048, admission_check=None):
        self.registry, self.identifier, self.selected = settings, identifier, selected
        self.row, _ = settings.connection(identifier, selected=selected)
        self.max_tokens = max_tokens
        self.transport = transport
        self.admission_check = admission_check
        self.used = False
        backend = self._make_backend()
        super().__init__(backend)

    def _make_backend(self):
        self.row, key = self.registry.connection(self.identifier, selected=self.selected)
        import httpx2
        http = httpx2.AsyncClient(transport=self.transport, timeout=60, follow_redirects=False)
        if self.row['kind']=='anthropic':
            from anthropic import AsyncAnthropic
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider
            headers = ({'anthropic-workspace-id':self.row['workspace_id']} if self.row['workspace_id'] else {})
            self.client=AsyncAnthropic(api_key=key,base_url=self.row['endpoint'],http_client=http,max_retries=0,default_headers=headers)
            backend=AnthropicModel(self.row['model'],provider=AnthropicProvider(anthropic_client=self.client))
        else:
            from openai import AsyncOpenAI
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider
            self.client=AsyncOpenAI(api_key=key,base_url=self.row['endpoint'],http_client=http,max_retries=0)
            backend=OpenAIChatModel(self.row['model'],provider=OpenAIProvider(openai_client=self.client))
        return backend

    def _next_backend(self):
        backend = self._make_backend() if self.used else self.wrapped
        self.used = True
        return backend

    def _admit(self, model_settings):
        # Configuration is immutable; disable/selection changes stop the next
        # admission. An already dispatched request may still finish.
        if self.admission_check is not None: self.admission_check()
        row,_=self.registry.connection(self.identifier,selected=self.selected)
        if row['model']!=self.row['model'] or row['endpoint']!=self.row['endpoint']:
            raise RuntimeError('model connection changed')
        values=dict(model_settings or {})
        if self.row['kind']=='anthropic':
            values={key:value for key,value in values.items() if not key.startswith('openai_')}
        values['max_tokens']=self.max_tokens
        return values

    async def request(self, messages, model_settings, model_request_parameters):
        backend = self._next_backend()
        try:
            values=self._admit(model_settings)
            async with self.client:
                result = await backend.request(messages,values,model_request_parameters)
                if result.usage.input_tokens <= 0:
                    raise RuntimeError('provider usage is unconfirmed')
                return result
        finally:
            await self.client.close()

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        backend = self._next_backend()
        try:
            values=self._admit(model_settings)
            async with self.client:
                async with backend.request_stream(messages,values,model_request_parameters,run_context) as stream:
                    yield stream
                    if stream.usage.input_tokens <= 0:
                        raise RuntimeError('provider usage is unconfirmed')
        finally:
            await self.client.close()
