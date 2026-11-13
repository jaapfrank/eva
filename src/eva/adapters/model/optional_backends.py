"""Upstream PydanticAI providers with explicit credentials and no SDK retries."""
import asyncio
from contextlib import asynccontextmanager
from pydantic_ai.models.wrapper import WrapperModel

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.openai_codex_provider import CodexProviderError
from eva.application.cancellation import WorkInterrupted


_TIMEOUT_ERROR_NAMES = frozenset({
    "APITimeoutError",
    "ConnectTimeout",
    "PoolTimeout",
    "ReadTimeout",
    "TimeoutException",
    "WriteTimeout",
})

_TRANSPORT_ERROR_NAMES = frozenset({
    "APIConnectionError",
    "ConnectError",
    "NetworkError",
    "ProtocolError",
    "ProxyError",
    "ReadError",
    "RemoteProtocolError",
    "TransportError",
    "WriteError",
})


def _classify_upstream_provider_error(error: BaseException) -> CodexProviderError | None:
    """Map upstream SDK/HTTP status and transport errors to safe CodexProviderError categories.

    Raw upstream exception bodies and headers are never chained into the raised error so
    API keys, workspace headers, or private prompts cannot leak into logs or tracebacks.
    """
    if isinstance(
        error,
        (
            CodexProviderError,
            ConversationModelError,
            WorkInterrupted,
            asyncio.CancelledError,
            KeyboardInterrupt,
        ),
    ):
        return None
    if type(error) is RuntimeError and str(error) in {
        "model connection changed",
        "provider usage is unconfirmed",
    }:
        return None

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    saw_timeout = False
    saw_transport = False

    while pending and len(seen) < 10:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(
            current,
            (
                ConversationModelError,
                WorkInterrupted,
                asyncio.CancelledError,
                KeyboardInterrupt,
            ),
        ):
            return None

        status = getattr(current, "status_code", None)
        if type(status) is not int:
            response = getattr(current, "response", None)
            resp_status = getattr(response, "status_code", None)
            if type(resp_status) is int:
                status = resp_status

        if type(status) is int:
            if status == 429:
                return CodexProviderError("rate_limited", http_error=error)
            if status in (401, 402, 403):
                return CodexProviderError("credentials_rejected", http_error=error)
            if status == 413:
                return CodexProviderError("request_too_large", http_error=error)
            if status == 408 or 500 <= status <= 599:
                return CodexProviderError("unavailable", http_error=error)
            if 400 <= status <= 499:
                return CodexProviderError("configuration", http_error=error)

        cls_name = type(current).__name__
        if isinstance(current, TimeoutError) or cls_name in _TIMEOUT_ERROR_NAMES:
            saw_timeout = True
        elif isinstance(current, ConnectionError) or cls_name in _TRANSPORT_ERROR_NAMES:
            saw_transport = True

        cause = current.__cause__ or current.__context__
        if cause is not None:
            pending.append(cause)

    if saw_timeout:
        return CodexProviderError("deadline", http_error=error)
    if saw_transport:
        return CodexProviderError("unavailable", http_error=error)
    # Catch-all scrub for any remaining upstream SDK/protocol exception (e.g.
    # ModelRequestRejectedError, UnexpectedModelBehavior, APIError without status)
    # so raw upstream bodies/headers never escape via __cause__.
    if isinstance(error, Exception):
        return CodexProviderError("protocol", http_error=error)
    return None


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
        except BaseException as error:
            mapped = _classify_upstream_provider_error(error)
            if mapped is not None:
                raise mapped from None
            raise
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
        except BaseException as error:
            mapped = _classify_upstream_provider_error(error)
            if mapped is not None:
                raise mapped from None
            raise
        finally:
            await self.client.close()
