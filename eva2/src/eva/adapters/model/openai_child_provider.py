"""Official-SDK provider lifecycle for Eva's sealed, tool-free child model.

Pydantic AI owns the Responses wire format and structured model execution.
The OpenAI SDK owns HTTP/API behavior. Eva's small boundary only supplies the
existing access-only lease, fixed Codex headers and fail-closed rotation rules.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import cast
from uuid import uuid4

import httpx2
from pydantic_ai import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from eva.adapters.model.openai_endpoint import responses_base_url
from eva.contracts.canonical_json import canonical_json_bytes

CredentialLoader = Callable[[], tuple[str, str]]


class ChildProviderConfigurationError(UserError):
    """The sealed provider profile or current access lease is unusable."""


class ChildProviderRequestTooLarge(UserError):
    """The sealed request exceeded its release-owned byte boundary."""


class ChildProviderResponseTooLarge(RuntimeError):
    """The provider stream exceeded its release-owned byte boundary."""


class ChildProviderProtocolError(RuntimeError):
    """The provider stream ended without one trustworthy terminal receipt."""


@dataclass(frozen=True, slots=True)
class OpenAIChildProviderSettings:
    """Release-owned settings; task text never constructs this object."""

    endpoint: str
    model_name: str
    timeout_seconds: float = 30.0
    reasoning_effort: str = "low"
    maximum_request_bytes: int = 256 * 1024
    maximum_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        responses_base_url(
            self.endpoint, invalid_error="child OpenAI endpoint must end in /responses"
        )
        if not self.model_name.strip() or len(self.model_name) > 120:
            raise ValueError("child model name is invalid")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("child model timeout must be 1..120 seconds")
        if self.reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("child reasoning effort is invalid")
        for value in (self.maximum_request_bytes, self.maximum_response_bytes):
            if not 4_096 <= value <= 8 * 1024 * 1024:
                raise ValueError("child model byte limit is invalid")


class _RotatingAccessToken:
    """Reload one access token per SDK request and pin its account authority."""

    def __init__(self, credential_loader: CredentialLoader, *, account_id: str) -> None:
        self._credential_loader = credential_loader
        self._account_id = account_id

    async def __call__(self) -> str:
        _forbid_sdk_logging()
        access_token, account_id = _load_credentials(self._credential_loader)
        if account_id != self._account_id:
            raise ChildProviderConfigurationError(
                "child model account changed during the provider lifecycle"
            )
        return access_token


class _BoundedAsyncResponseStream(httpx2.AsyncByteStream):
    """Bound and validate a complete provider SSE before the SDK sees it."""

    def __init__(
        self,
        stream: httpx2.AsyncByteStream,
        maximum_bytes: int,
        *,
        validate_terminal_sse: bool,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._validate_terminal_sse = validate_terminal_sse

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        total = 0
        buffered: list[bytes] = []
        async for chunk in self._stream:
            total += len(chunk)
            if total > self._maximum_bytes:
                await self._stream.aclose()
                raise ChildProviderResponseTooLarge(
                    "child model response exceeded its byte limit"
                )
            buffered.append(chunk)
        content = b"".join(buffered)
        if self._validate_terminal_sse:
            _validate_terminal_sse(content)
        if content:
            yield content

    async def aclose(self) -> None:
        await self._stream.aclose()


class _LocalInputCountTransport(httpx2.AsyncBaseTransport):
    """Bound wire bytes and emulate Codex's unsupported token-count route."""

    def __init__(
        self,
        *,
        maximum_request_bytes: int,
        maximum_response_bytes: int,
    ) -> None:
        self._network = httpx2.AsyncHTTPTransport(retries=0, trust_env=False)
        self._maximum_request_bytes = maximum_request_bytes
        self._maximum_response_bytes = maximum_response_bytes
        self.local_count_requests = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        content = await request.aread()
        if len(content) > self._maximum_request_bytes:
            raise ChildProviderRequestTooLarge(
                "child model request exceeded its byte limit"
            )
        if request.url.path.endswith("/responses/input_tokens"):
            self.local_count_requests += 1
            # This is a deliberately conservative admission estimate, not a claim to
            # reproduce the provider tokenizer. A token cannot encode less than one
            # request byte; the complete SDK JSON is counted and 256 extra tokens are
            # reserved for provider framing. Terminal usage remains authoritative.
            conservative_count = min(2_000_000, len(content) + 256)
            return httpx2.Response(
                200,
                json={
                    "object": "response.input_tokens",
                    "input_tokens": conservative_count,
                },
                request=request,
            )
        if request.url.path.endswith("/responses"):
            request = _codex_compatible_request(request)
            if len(request.content) > self._maximum_request_bytes:
                raise ChildProviderRequestTooLarge(
                    "child model request exceeded its byte limit"
                )
        response = await self._network.handle_async_request(request)
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as error:
                await response.aclose()
                raise ChildProviderResponseTooLarge(
                    "child model response length is invalid"
                ) from error
            if declared_length > self._maximum_response_bytes:
                await response.aclose()
                raise ChildProviderResponseTooLarge(
                    "child model response exceeded its byte limit"
                )
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=_BoundedAsyncResponseStream(
                cast(httpx2.AsyncByteStream, response.stream),
                self._maximum_response_bytes,
                validate_terminal_sse=(
                    200 <= response.status_code < 300
                    and response.headers.get("content-type", "")
                    .lower()
                    .startswith("text/event-stream")
                ),
            ),
            request=request,
            extensions=response.extensions,
            history=response.history,
        )

    async def aclose(self) -> None:
        await self._network.aclose()


class _CodexCompatibleResponsesModel(Model):
    """Keep Pydantic's adapter while omitting one unsupported Codex field."""

    def __init__(self, delegate: Model) -> None:
        super().__init__()
        self._delegate = delegate

    @property
    def model_name(self) -> str:
        return self._delegate.model_name

    @property
    def system(self) -> str:
        return self._delegate.system

    @property
    def base_url(self) -> str | None:
        return self._delegate.base_url

    @property
    def provider(self):  # type: ignore[no-untyped-def]
        return self._delegate.provider

    @property
    def profile(self):  # type: ignore[no-untyped-def]
        return self._delegate.profile

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return await self._delegate.count_tokens(
            messages,
            model_settings,
            model_request_parameters,
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        # ChatGPT's Codex endpoint currently rejects max_output_tokens. Eva still
        # validates terminal provider usage and rejects an over-budget result.
        compatible_settings = dict(model_settings or {})
        compatible_settings.pop("max_tokens", None)
        try:
            async with self._delegate.request_stream(
                messages,
                compatible_settings,
                model_request_parameters,
            ) as streamed:
                async for _event in streamed:
                    pass
                response = streamed.get()
                finish_reason = (response.provider_details or {}).get(
                    "finish_reason"
                )
                if response.state != "complete" or finish_reason != "completed":
                    raise UnexpectedModelBehavior(
                        "child model stream ended without a completed response"
                    )
                return response
        except Exception as error:
            configuration_error = _caused_by(
                error, ChildProviderConfigurationError
            )
            if configuration_error is not None:
                raise configuration_error from None
            raise


def _codex_compatible_request(request: httpx2.Request) -> httpx2.Request:
    """Project Pydantic's official request onto the narrower ChatGPT Codex API."""

    try:
        body = json.loads(request.content)
    except (httpx2.RequestNotRead, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChildProviderConfigurationError(
            "child model request is not valid JSON"
        ) from error
    allowed = {"include", "input", "model", "reasoning", "store", "stream", "text"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ChildProviderConfigurationError("child model request shape differs")
    if body.get("store") is not False or body.get("stream") is not True:
        raise ChildProviderConfigurationError("child model request is not a stream")
    raw_input = body.get("input")
    if not isinstance(raw_input, list) or not raw_input:
        raise ChildProviderConfigurationError("child model input is invalid")
    instructions: list[str] = []
    projected_input: list[dict[str, str]] = []
    for item in raw_input:
        if not isinstance(item, dict) or set(item) != {"content", "role"}:
            raise ChildProviderConfigurationError("child model input item differs")
        content = item.get("content")
        role = item.get("role")
        if not isinstance(content, str) or not content:
            raise ChildProviderConfigurationError("child model input text is invalid")
        if role == "system":
            instructions.append(content)
        elif role == "user":
            projected_input.append(
                {"content": content, "role": "user", "type": "message"}
            )
        else:
            raise ChildProviderConfigurationError("child model input role is forbidden")
    if len(projected_input) != 1 or not instructions:
        raise ChildProviderConfigurationError("child model input boundary differs")
    body["input"] = projected_input
    body["instructions"] = "\n\n".join(instructions)
    body.pop("text", None)
    body["parallel_tool_calls"] = False
    body["tool_choice"] = "none"
    body["tools"] = []
    encoded = canonical_json_bytes(body)
    headers = dict(request.headers)
    headers.pop("content-length", None)
    return httpx2.Request(
        request.method,
        request.url,
        headers=headers,
        content=encoded,
        extensions=request.extensions,
    )


def _validate_terminal_sse(content: bytes) -> None:
    """Require exactly one completed terminal event with coherent measured usage."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ChildProviderProtocolError(
            "child model response is not UTF-8 SSE"
        ) from error
    terminal: list[dict[str, object]] = []
    completed_seen = False
    for line in lines:
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            event = json.loads(line.removeprefix("data: "))
        except json.JSONDecodeError as error:
            raise ChildProviderProtocolError(
                "child model response contains invalid SSE JSON"
            ) from error
        if not isinstance(event, dict):
            raise ChildProviderProtocolError("child model SSE event is invalid")
        kind = event.get("type")
        if completed_seen:
            raise ChildProviderProtocolError(
                "child model returned an event after completion"
            )
        if kind == "error":
            raise ChildProviderProtocolError("child model returned an error event")
        if kind in {
            "response.cancelled",
            "response.completed",
            "response.failed",
            "response.incomplete",
        }:
            terminal.append(event)
            completed_seen = kind == "response.completed"
    if len(terminal) != 1 or terminal[0].get("type") != "response.completed":
        raise ChildProviderProtocolError(
            "child model stream has no unique completed terminal event"
        )
    response = terminal[0].get("response")
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise ChildProviderProtocolError("child model completion status is invalid")
    if response.get("error") is not None:
        raise ChildProviderProtocolError("child model completion contains an error")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ChildProviderProtocolError("child model completion usage is missing")
    values = tuple(usage.get(name) for name in ("input_tokens", "output_tokens", "total_tokens"))
    if (
        any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values)
        or values[2] != values[0] + values[1]
    ):
        raise ChildProviderProtocolError("child model completion usage is invalid")


class OpenAIChildProvider:
    """Async lifecycle for one official OpenAI Responses model instance."""

    def __init__(
        self,
        settings: OpenAIChildProviderSettings,
        *,
        credential_loader: CredentialLoader,
    ) -> None:
        self._settings = settings
        self._credential_loader = credential_loader
        self._http_client: httpx2.AsyncClient | None = None
        self._transport: _LocalInputCountTransport | None = None
        self._sdk_client: object | None = None
        self._model: Model | None = None

    @property
    def local_count_requests(self) -> int:
        """Return only the number of local preflight counts, never their content."""

        return 0 if self._transport is None else self._transport.local_count_requests

    async def __aenter__(self) -> Model:
        if self._model is not None:
            raise RuntimeError("child provider lifecycle is already open")
        _forbid_sdk_logging()
        _access_token, account_id = _load_credentials(self._credential_loader)
        try:
            from openai import AsyncOpenAI
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.providers.openai import OpenAIProvider
        except ImportError as error:
            raise ChildProviderConfigurationError(
                "official OpenAI SDK dependency is unavailable"
            ) from error

        async def bind_request(request: httpx2.Request) -> None:
            request.headers["session_id"] = str(uuid4())

        transport = _LocalInputCountTransport(
            maximum_request_bytes=self._settings.maximum_request_bytes,
            maximum_response_bytes=self._settings.maximum_response_bytes,
        )
        http_client = httpx2.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx2.Timeout(self._settings.timeout_seconds),
            event_hooks={"request": [bind_request]},
        )
        try:
            sdk_client = AsyncOpenAI(
                api_key=_RotatingAccessToken(
                    self._credential_loader,
                    account_id=account_id,
                ),
                base_url=responses_base_url(
                    self._settings.endpoint,
                    invalid_error="child OpenAI endpoint must end in /responses",
                ),
                default_headers={
                    "OpenAI-Account-ID": account_id,
                    "OpenAI-Beta": "responses=experimental",
                    "Accept-Encoding": "identity",
                    "Origin": "https://chatgpt.com",
                    "User-Agent": "codex_cli_rs/0.0.0 (Eva subagent)",
                    "originator": "codex_cli_rs",
                },
                http_client=http_client,
                max_retries=0,
                timeout=self._settings.timeout_seconds,
            )
            delegate = OpenAIResponsesModel(
                self._settings.model_name,
                provider=OpenAIProvider(openai_client=sdk_client),
                profile={"openai_responses_supports_reasoning_context": False},
                settings={
                    "openai_reasoning_effort": self._settings.reasoning_effort,
                    "openai_reasoning_summary": "auto",
                    "openai_store": False,
                    "parallel_tool_calls": False,
                    "timeout": self._settings.timeout_seconds,
                },
            )
            model = _CodexCompatibleResponsesModel(delegate)
        except BaseException:
            await http_client.aclose()
            raise
        self._http_client = http_client
        self._transport = transport
        self._sdk_client = sdk_client
        self._model = model
        return model

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        sdk_client = self._sdk_client
        http_client = self._http_client
        self._model = None
        self._sdk_client = None
        self._http_client = None
        self._transport = None
        if sdk_client is not None:
            await sdk_client.close()  # type: ignore[attr-defined]
        if http_client is not None and not http_client.is_closed:
            await http_client.aclose()


def _load_credentials(loader: CredentialLoader) -> tuple[str, str]:
    try:
        access_token, account_id = loader()
    except Exception as error:
        raise ChildProviderConfigurationError("child model credentials are unavailable") from error
    if not isinstance(access_token, str) or not access_token:
        raise ChildProviderConfigurationError("child model access token is missing")
    if not isinstance(account_id, str) or not account_id:
        raise ChildProviderConfigurationError("child model account ID is missing")
    return access_token, account_id


def _forbid_sdk_logging() -> None:
    if os.environ.get("OPENAI_LOG"):
        raise ChildProviderConfigurationError(
            "ambient OpenAI SDK logging is forbidden for delegated work"
        )


def _caused_by(
    error: BaseException, kind: type[BaseException]
) -> BaseException | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, kind):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


__all__ = [
    "ChildProviderConfigurationError",
    "ChildProviderRequestTooLarge",
    "ChildProviderResponseTooLarge",
    "OpenAIChildProvider",
    "OpenAIChildProviderSettings",
]
