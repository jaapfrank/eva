"""Shared, bounded Pydantic/OpenAI provider for Eva's Codex access lease.

This module owns transport compatibility only.  It deliberately knows nothing
about conversations, tools, durable actions, or provider execution.  Callers
give the returned public Pydantic ``Model`` to an ``Agent`` and retain all
authority over external effects themselves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
from time import monotonic
from types import TracebackType
from typing import Any, Literal, cast
from uuid import uuid4

import httpx2
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from eva.adapters.model.openai_endpoint import responses_base_url
from eva.contracts.canonical_json import canonical_json_bytes


CredentialLoader = Callable[[], tuple[str, str]]
FailureCategory = Literal[
    "configuration",
    "credentials_rejected",
    "deadline",
    "protocol",
    "request_too_large",
    "response_too_large",
    "unavailable",
]


class CodexProviderError(RuntimeError):
    """A provider failure whose message is safe to expose to product code."""

    def __init__(self, category: FailureCategory) -> None:
        self.category = category
        super().__init__(f"Codex provider {category.replace('_', ' ')}")


@dataclass(frozen=True, slots=True)
class RawUsageAttestation:
    """Usage read from the unique raw ``response.completed`` SSE event."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("raw provider usage is invalid")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("raw provider usage total is incoherent")


@dataclass(frozen=True, slots=True)
class OpenAICodexProviderSettings:
    """One release-owned model phase; prompts cannot alter these bounds."""

    endpoint: str
    model_name: str
    timeout_seconds: float = 30.0
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    maximum_request_bytes: int = 8 * 1024 * 1024
    maximum_response_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        responses_base_url(
            self.endpoint,
            invalid_error="Codex endpoint must end in /responses",
        )
        if not self.model_name.strip() or len(self.model_name) > 120:
            raise ValueError("Codex model name is invalid")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("Codex phase timeout must be 1..120 seconds")
        if self.reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("Codex reasoning effort is invalid")
        for value in (self.maximum_request_bytes, self.maximum_response_bytes):
            if not 4_096 <= value <= 8 * 1024 * 1024:
                raise ValueError("Codex byte limit is invalid")


class _RawSseUsageAttestor:
    """Incrementally retain only enough raw SSE to attest terminal usage."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._terminal: RawUsageAttestation | None = None
        self._completed = False

    def feed(self, chunk: bytes) -> None:
        self._pending.extend(chunk)
        while True:
            newline = self._pending.find(b"\n")
            if newline < 0:
                return
            raw = bytes(self._pending[:newline]).rstrip(b"\r")
            del self._pending[: newline + 1]
            self._line(raw)

    def finish(self) -> RawUsageAttestation:
        if self._pending:
            raw = bytes(self._pending).rstrip(b"\r")
            self._pending.clear()
            self._line(raw)
        if not self._completed or self._terminal is None:
            raise CodexProviderError("protocol")
        return self._terminal

    @property
    def terminal(self) -> RawUsageAttestation | None:
        """Return the completed receipt as soon as its whole SSE line was read."""

        return self._terminal

    def _line(self, raw: bytes) -> None:
        if not raw.startswith(b"data: "):
            return
        payload = raw[6:]
        if payload == b"[DONE]":
            return
        if self._completed:
            raise CodexProviderError("protocol")
        try:
            event = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CodexProviderError("protocol") from error
        if not isinstance(event, dict):
            raise CodexProviderError("protocol")
        kind = event.get("type")
        if kind == "error" or kind in {
            "response.cancelled",
            "response.failed",
            "response.incomplete",
        }:
            raise CodexProviderError("protocol")
        if kind != "response.completed":
            return
        if self._terminal is not None:
            raise CodexProviderError("protocol")
        response = event.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or response.get("status") != "completed"
            or response.get("error") is not None
            or not isinstance(usage, dict)
        ):
            raise CodexProviderError("protocol")
        try:
            self._terminal = RawUsageAttestation(
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        except (TypeError, ValueError) as error:
            raise CodexProviderError("protocol") from error
        self._completed = True


def _remaining(deadline: float) -> float:
    value = deadline - monotonic()
    if value <= 0:
        raise CodexProviderError("deadline")
    return value


class _AttestedStreamingBody(httpx2.AsyncByteStream):
    """Yield bytes immediately while bounding and attesting the copied stream."""

    def __init__(
        self,
        stream: httpx2.AsyncByteStream,
        *,
        deadline: float,
        maximum_bytes: int,
        attest: bool,
        retain: Callable[[RawUsageAttestation], None],
    ) -> None:
        self._stream = stream
        self._deadline = deadline
        self._maximum_bytes = maximum_bytes
        self._attestor = _RawSseUsageAttestor() if attest else None
        self._retain = retain
        self._retained = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        total = 0
        iterator = self._stream.__aiter__()
        while True:
            try:
                async with asyncio.timeout(_remaining(self._deadline)):
                    chunk = await anext(iterator)
            except StopAsyncIteration:
                break
            except TimeoutError as error:
                await self._stream.aclose()
                raise CodexProviderError("deadline") from error
            total += len(chunk)
            if total > self._maximum_bytes:
                await self._stream.aclose()
                raise CodexProviderError("response_too_large")
            if self._attestor is not None:
                self._attestor.feed(chunk)
                terminal = self._attestor.terminal
                if terminal is not None and not self._retained:
                    self._retain(terminal)
                    self._retained = True
            yield chunk
        if self._attestor is not None:
            terminal = self._attestor.finish()
            if not self._retained:
                self._retain(terminal)
                self._retained = True

    async def aclose(self) -> None:
        await self._stream.aclose()


class _CodexTransport(httpx2.AsyncBaseTransport):
    """Bound one SDK model phase and retain its raw terminal usage."""

    def __init__(
        self,
        *,
        deadline: float,
        maximum_request_bytes: int,
        maximum_response_bytes: int,
        network: httpx2.AsyncBaseTransport | None,
    ) -> None:
        self._deadline = deadline
        self._maximum_request_bytes = maximum_request_bytes
        self._maximum_response_bytes = maximum_response_bytes
        self._network = network or httpx2.AsyncHTTPTransport(retries=0)
        self._model_requests = 0
        self._local_count_requests = 0
        self._attestation: RawUsageAttestation | None = None
        self._consumed = False

    @property
    def model_requests(self) -> int:
        return self._model_requests

    @property
    def local_count_requests(self) -> int:
        return self._local_count_requests

    def consume_attestation(self) -> RawUsageAttestation:
        if self._consumed or self._model_requests != 1 or self._attestation is None:
            raise CodexProviderError("protocol")
        self._consumed = True
        return self._attestation

    def _retain(self, usage: RawUsageAttestation) -> None:
        if self._attestation is not None:
            raise CodexProviderError("protocol")
        self._attestation = usage

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        try:
            async with asyncio.timeout(_remaining(self._deadline)):
                content = await request.aread()
        except TimeoutError as error:
            raise CodexProviderError("deadline") from error
        if len(content) > self._maximum_request_bytes:
            raise CodexProviderError("request_too_large")
        if request.url.path.endswith("/responses/input_tokens"):
            self._local_count_requests += 1
            return httpx2.Response(
                200,
                json={
                    "object": "response.input_tokens",
                    "input_tokens": min(2_000_000, len(content) + 256),
                },
                request=request,
            )
        is_model_request = request.url.path.endswith("/responses")
        if is_model_request:
            self._model_requests += 1
            if self._model_requests != 1:
                raise CodexProviderError("protocol")
            request = _codex_compatible_request(request)
            if len(request.content) > self._maximum_request_bytes:
                raise CodexProviderError("request_too_large")
        try:
            async with asyncio.timeout(_remaining(self._deadline)):
                response = await self._network.handle_async_request(request)
        except TimeoutError as error:
            raise CodexProviderError("deadline") from error
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError as error:
                await response.aclose()
                raise CodexProviderError("protocol") from error
            if declared_bytes > self._maximum_response_bytes:
                await response.aclose()
                raise CodexProviderError("response_too_large")
        content_type = response.headers.get("content-type", "").lower()
        attest = (
            is_model_request
            and 200 <= response.status_code < 300
            and content_type.startswith("text/event-stream")
        )
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=_AttestedStreamingBody(
                cast(httpx2.AsyncByteStream, response.stream),
                deadline=self._deadline,
                maximum_bytes=self._maximum_response_bytes,
                attest=attest,
                retain=self._retain,
            ),
            request=request,
            extensions=response.extensions,
            history=response.history,
        )

    async def aclose(self) -> None:
        await self._network.aclose()


def _codex_compatible_request(request: httpx2.Request) -> httpx2.Request:
    """Project Pydantic's public Responses request onto the Codex endpoint."""

    try:
        body = json.loads(request.content)
    except (httpx2.RequestNotRead, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CodexProviderError("configuration") from error
    allowed = {
        "include",
        "input",
        "instructions",
        "model",
        "parallel_tool_calls",
        "prompt_cache_key",
        "reasoning",
        "store",
        "stream",
        "text",
        "tool_choice",
        "tools",
    }
    if not isinstance(body, dict) or set(body) - allowed:
        raise CodexProviderError("configuration")
    if body.get("store") is not False or body.get("stream") is not True:
        raise CodexProviderError("configuration")
    if body.get("parallel_tool_calls") not in {None, False}:
        raise CodexProviderError("configuration")
    raw_input = body.get("input")
    if not isinstance(raw_input, list) or not raw_input:
        raise CodexProviderError("configuration")
    instructions: list[str] = []
    existing_instructions = body.get("instructions")
    if existing_instructions is not None:
        if not isinstance(existing_instructions, str) or not existing_instructions:
            raise CodexProviderError("configuration")
        instructions.append(existing_instructions)
    projected_input: list[dict[str, Any]] = []
    for item in raw_input:
        if not isinstance(item, dict):
            raise CodexProviderError("configuration")
        if set(item) == {"content", "role"}:
            content, role = item.get("content"), item.get("role")
            if not isinstance(content, str) or not content:
                raise CodexProviderError("configuration")
            if role == "system":
                instructions.append(content)
            elif role == "user":
                projected_input.append(
                    {"content": content, "role": "user", "type": "message"}
                )
            else:
                raise CodexProviderError("configuration")
            continue
        if item.get("type") not in {"function_call", "function_call_output"}:
            raise CodexProviderError("configuration")
        projected_input.append(item)
    if not projected_input or not instructions:
        raise CodexProviderError("configuration")
    tools = body.get("tools", [])
    if not isinstance(tools, list):
        raise CodexProviderError("configuration")
    body["input"] = projected_input
    body["instructions"] = "\n\n".join(instructions)
    body.pop("text", None)
    body["parallel_tool_calls"] = False
    body.setdefault("tool_choice", "auto" if tools else "none")
    body.setdefault("tools", tools)
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


class _UsedAccessToken:
    """Retain only the digest of the credential used for the model request."""

    def __init__(self) -> None:
        self.sha256: str | None = None

    def retain(self, access_token: str) -> None:
        self.sha256 = hashlib.sha256(access_token.encode()).hexdigest()


class _RotatingAccessToken:
    """Reload the access-only lease per SDK request and pin its account."""

    def __init__(
        self,
        loader: CredentialLoader,
        *,
        account_id: str,
        used_access_token: _UsedAccessToken,
    ) -> None:
        self._loader = loader
        self._account_id = account_id
        self._used_access_token = used_access_token

    async def __call__(self) -> str:
        access_token, account_id = _load_credentials(self._loader)
        if account_id != self._account_id:
            raise CodexProviderError("configuration")
        self._used_access_token.retain(access_token)
        return access_token


class _CodexStreamingModel(Model):
    """Delegate to Pydantic's public streaming API required by Codex."""

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
            messages, model_settings, model_request_parameters
        )

    @staticmethod
    def _compatible_settings(value: ModelSettings | None) -> ModelSettings:
        compatible = dict(value or {})
        compatible.pop("max_tokens", None)
        return cast(ModelSettings, compatible)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        try:
            async with self.request_stream(
                messages,
                model_settings,
                model_request_parameters,
            ) as streamed:
                async for _event in streamed:
                    pass
                response = streamed.get()
        except Exception as error:
            raise _sanitized(error) from None
        if response.state != "complete":
            raise CodexProviderError("protocol")
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ):  # type: ignore[no-untyped-def]
        try:
            async with self._delegate.request_stream(
                messages,
                self._compatible_settings(model_settings),
                model_request_parameters,
                run_context,
            ) as streamed:
                yield streamed
        except Exception as error:
            raise _sanitized(error) from None


@dataclass(frozen=True, slots=True)
class OpenAICodexSession:
    """The one model plus wire attestation for a single bounded phase."""

    model: Model
    _transport: _CodexTransport
    _used_access_token: _UsedAccessToken

    @property
    def model_requests(self) -> int:
        return self._transport.model_requests

    @property
    def local_count_requests(self) -> int:
        return self._transport.local_count_requests

    @property
    def access_token_sha256(self) -> str | None:
        """Digest of the access token actually supplied to the SDK request."""

        return self._used_access_token.sha256

    def consume_raw_usage(self) -> RawUsageAttestation:
        return self._transport.consume_attestation()


class OpenAICodexProvider:
    """Async lifecycle for exactly one bounded Pydantic model phase."""

    def __init__(
        self,
        settings: OpenAICodexProviderSettings,
        *,
        credential_loader: CredentialLoader,
        network_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._credential_loader = credential_loader
        self._network_transport = network_transport
        self._http_client: httpx2.AsyncClient | None = None
        self._sdk_client: Any | None = None
        self._session: OpenAICodexSession | None = None

    async def __aenter__(self) -> OpenAICodexSession:
        if self._session is not None:
            raise RuntimeError("Codex provider lifecycle is already open")
        _access_token, account_id = _load_credentials(self._credential_loader)
        try:
            from openai import AsyncOpenAI
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.providers.openai import OpenAIProvider
        except ImportError as error:
            raise CodexProviderError("configuration") from error
        deadline = monotonic() + self._settings.timeout_seconds
        used_access_token = _UsedAccessToken()
        transport = _CodexTransport(
            deadline=deadline,
            maximum_request_bytes=self._settings.maximum_request_bytes,
            maximum_response_bytes=self._settings.maximum_response_bytes,
            network=self._network_transport,
        )

        async def bind_request(request: httpx2.Request) -> None:
            request.headers["session_id"] = str(uuid4())

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
                    used_access_token=used_access_token,
                ),
                base_url=responses_base_url(
                    self._settings.endpoint,
                    invalid_error="Codex endpoint must end in /responses",
                ),
                default_headers={
                    "OpenAI-Account-ID": account_id,
                    "OpenAI-Beta": "responses=experimental",
                    "Accept-Encoding": "identity",
                    "Origin": "https://chatgpt.com",
                    "User-Agent": "codex_cli_rs/0.0.0 (Eva)",
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
            session = OpenAICodexSession(
                _CodexStreamingModel(delegate), transport, used_access_token
            )
        except BaseException:
            await http_client.aclose()
            raise
        self._http_client = http_client
        self._sdk_client = sdk_client
        self._session = session
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        sdk_client = self._sdk_client
        http_client = self._http_client
        self._session = None
        self._sdk_client = None
        self._http_client = None
        if sdk_client is not None:
            await sdk_client.close()
        if http_client is not None and not http_client.is_closed:
            await http_client.aclose()


def _load_credentials(loader: CredentialLoader) -> tuple[str, str]:
    try:
        access_token, account_id = loader()
    except Exception as error:
        raise CodexProviderError("configuration") from error
    if not isinstance(access_token, str) or not access_token:
        raise CodexProviderError("configuration")
    if not isinstance(account_id, str) or not account_id:
        raise CodexProviderError("configuration")
    return access_token, account_id


def _sanitized(error: BaseException) -> CodexProviderError:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CodexProviderError):
            return current
        status_code = getattr(current, "status_code", None)
        if status_code in {401, 403}:
            return CodexProviderError("credentials_rejected")
        if status_code in {408, 409, 429} or (
            isinstance(status_code, int) and status_code >= 500
        ):
            return CodexProviderError("unavailable")
        current = current.__cause__ or current.__context__
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return CodexProviderError("unavailable")
    if isinstance(error, UnexpectedModelBehavior):
        return CodexProviderError("protocol")
    return CodexProviderError("unavailable")


__all__ = [
    "CodexProviderError",
    "FailureCategory",
    "OpenAICodexProvider",
    "OpenAICodexProviderSettings",
    "OpenAICodexSession",
    "RawUsageAttestation",
]
