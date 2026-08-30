"""Bounded OpenAI Responses SDK transport without agent or tool authority."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from queue import Empty, Full, Queue
import re
from threading import Event, Thread
from time import monotonic
from typing import Any, Callable, Iterator, cast
from uuid import uuid4

import httpx2

from eva.adapters.model.openai_endpoint import responses_base_url
from eva.application.conversation import (
    ConversationActionCall,
    ConversationModelTiming,
    ConversationModelUsage,
    publish_conversation_text,
)
from eva.contracts.canonical_json import canonical_json_bytes


_ALLOWED_BODY_FIELDS = frozenset({
    "include", "input", "instructions", "model", "parallel_tool_calls",
    "prompt_cache_key", "reasoning", "store", "tool_choice", "tools",
})
_SAFE_OUTPUT_ITEM_TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,79}")


class OpenAIResponsesTransportError(RuntimeError):
    """The candidate SDK transport could not return a trusted result."""


class OpenAIResponsesCredentialRejected(OpenAIResponsesTransportError):
    """The provider rejected one exact access-token/account pair."""


class OpenAIResponsesUnavailable(OpenAIResponsesTransportError):
    """The provider or optional SDK dependency is unavailable."""


class ConversationSseError(ValueError):
    """The ordinary conversation stream is malformed."""


def parse_conversation_sse(events: Iterator[tuple[str, dict[str, Any]]], *,
                           started_at: float | None = None, clock: Callable[[], float] = monotonic,
                           tools_disabled: bool = False) -> Any:
    deltas: list[str] = []
    completed_parts: list[str] = []
    action_call: ConversationActionCall | None = None
    first_content_ms: int | None = None
    for kind, data in events:
        if kind in {"response.output_text.delta", "response.text.delta"}:
            delta = data.get("delta")
            if not isinstance(delta, str):
                raise ConversationSseError("conversation text delta is invalid")
            if delta and started_at is not None and first_content_ms is None:
                first_content_ms = round((clock() - started_at) * 1000)
            publish_conversation_text(delta, tools_disabled=tools_disabled)
            deltas.append(delta)
        elif kind == "response.output_item.done":
            item = data.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                if action_call is not None:
                    raise ConversationSseError("conversation model returned multiple actions")
                call_id, name, encoded = item.get("call_id"), item.get("name"), item.get("arguments")
                if not all(isinstance(value, str) for value in (call_id, name, encoded)):
                    raise ConversationSseError("conversation action frame is invalid")
                try:
                    arguments = json.loads(encoded)
                except json.JSONDecodeError as error:
                    raise ConversationSseError("conversation action arguments are invalid JSON") from error
                if not isinstance(arguments, dict):
                    raise ConversationSseError("conversation action arguments are not an object")
                try:
                    action_call = ConversationActionCall(call_id, name, arguments)
                except ValueError as error:
                    raise ConversationSseError("conversation action arguments are invalid") from error
                continue
            content = item.get("content")
            if isinstance(content, list):
                completed_parts.extend(part["text"] for part in content
                                       if isinstance(part, dict) and isinstance(part.get("text"), str))
        elif kind == "response.failed":
            raise ConversationSseError("conversation provider reported failure")
    text = "".join(deltas) if deltas else "".join(completed_parts)
    if started_at is None:
        return text, action_call
    completed_ms = max(0, round((clock() - started_at) * 1000))
    first = completed_ms if first_content_ms is None and text else first_content_ms
    return text, action_call, ConversationModelTiming(first, completed_ms)


class _RequestTooLarge(RuntimeError):
    pass


class _ResponseTooLarge(RuntimeError):
    pass


class _DeadlineExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenAIResponsesResult:
    """One SDK response projected into Eva-owned contracts."""

    text: str
    action_call: ConversationActionCall | None
    usage: ConversationModelUsage
    first_text_delta_seconds: float | None = None
    completed_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.first_text_delta_seconds is not None and (
            self.first_text_delta_seconds < 0 or self.completed_seconds is None
        ):
            raise ValueError("first text timing is invalid")
        if self.completed_seconds is not None and self.completed_seconds < 0:
            raise ValueError("completion timing is invalid")
        if (
            self.first_text_delta_seconds is not None
            and self.completed_seconds is not None
            and self.first_text_delta_seconds > self.completed_seconds
        ):
            raise ValueError("first text timing exceeds completion timing")


class _BoundedResponseStream(httpx2.SyncByteStream):
    def __init__(
        self, stream: httpx2.SyncByteStream, maximum_bytes: int,
        deadline: float, expire: Callable[[], None],
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._deadline = deadline
        self._expire = expire
        self._expired = False

    def __iter__(self) -> Iterator[bytes]:
        messages: Queue[tuple[str, object]] = Queue(maxsize=1)
        stopped = Event()

        def pump() -> None:
            try:
                for chunk in self._stream:
                    while not stopped.is_set():
                        try:
                            messages.put(("chunk", chunk), timeout=0.05)
                            break
                        except Full:
                            continue
                    if stopped.is_set():
                        return
                messages.put(("done", None))
            except BaseException as error:
                while not stopped.is_set():
                    try:
                        messages.put(("error", error), timeout=0.05)
                        return
                    except Full:
                        continue
            finally:
                if self._expired:
                    self._stream.close()

        Thread(target=pump, name="eva-openai-response", daemon=True).start()
        total = 0
        while True:
            remaining = self._deadline - monotonic()
            if remaining <= 0:
                stopped.set()
                self._expired = True
                self._expire()
                raise _DeadlineExceeded
            try:
                kind, value = messages.get(timeout=remaining)
            except Empty as error:
                stopped.set()
                self._expired = True
                self._expire()
                raise _DeadlineExceeded from error
            if kind == "done":
                return
            if kind == "error":
                raise cast(BaseException, value)
            chunk = cast(bytes, value)
            total += len(chunk)
            if total > self._maximum_bytes:
                stopped.set()
                raise _ResponseTooLarge
            yield chunk

    def close(self) -> None:
        if not self._expired:
            self._stream.close()


class _BoundedTransport(httpx2.BaseTransport):
    """Apply byte limits before the SDK parses request or response content."""

    def __init__(
        self,
        *,
        maximum_request_bytes: int,
        maximum_response_bytes: int,
        deadline: float,
    ):
        self._maximum_request_bytes = maximum_request_bytes
        self._maximum_response_bytes = maximum_response_bytes
        self._deadline = deadline
        self._transport = httpx2.HTTPTransport(trust_env=False, retries=0)
        self._expired = Event()

    def _expire(self) -> None:
        if self._expired.is_set():
            return
        self._expired.set()
        Thread(
            target=self._transport.close, name="eva-openai-close", daemon=True
        ).start()

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        deadline = self._deadline
        try:
            content = request.content
        except httpx2.RequestNotRead:
            content = request.read()
        if len(content) > self._maximum_request_bytes:
            raise _RequestTooLarge
        results: Queue[tuple[str, object]] = Queue(maxsize=1)

        def send() -> None:
            try:
                results.put(("response", self._transport.handle_request(request)))
            except BaseException as error:
                results.put(("error", error))
            finally:
                if self._expired.is_set():
                    self._transport.close()

        Thread(target=send, name="eva-openai-request", daemon=True).start()
        remaining = deadline - monotonic()
        if remaining <= 0:
            self._expire()
            raise _DeadlineExceeded
        try:
            kind, value = results.get(timeout=remaining)
        except Empty as error:
            self._expire()
            raise _DeadlineExceeded from error
        if kind == "error":
            raise cast(BaseException, value)
        response = cast(httpx2.Response, value)
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=_BoundedResponseStream(
                cast(httpx2.SyncByteStream, response.stream),
                self._maximum_response_bytes,
                deadline,
                self._expire,
            ),
            request=request,
            extensions=response.extensions,
            history=response.history,
        )

    def close(self) -> None:
        if not self._expired.is_set():
            self._transport.close()


class OpenAIResponsesTransport:
    """One temporary, no-retry SDK client for one exact lease generation."""

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        maximum_request_bytes: int = 8 * 1024 * 1024,
        maximum_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._base_url = responses_base_url(
            endpoint, invalid_error="OpenAI SDK endpoint must end in /responses"
        )
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("OpenAI SDK timeout must be 1..120 seconds")
        if not 256 <= maximum_request_bytes <= 8 * 1024 * 1024:
            raise ValueError("OpenAI SDK request limit is invalid")
        if not 256 <= maximum_response_bytes <= 8 * 1024 * 1024:
            raise ValueError("OpenAI SDK response limit is invalid")
        self._timeout_seconds = timeout_seconds
        self._maximum_request_bytes = maximum_request_bytes
        self._maximum_response_bytes = maximum_response_bytes
        (
            self._api_connection_error,
            self._api_error,
            self._api_status_error,
            self._api_timeout_error,
            self._openai_client,
        ) = _load_sdk()

    def request(self, body: dict[str, Any], *, access_token: str,
                account_id: str) -> OpenAIResponsesResult:
        """Stream one response; never execute a returned tool call."""

        _validate_request(body, access_token=access_token, account_id=account_id)
        if os.environ.get("OPENAI_LOG"):
            raise OpenAIResponsesTransportError(
                "OpenAI SDK ambient logging is not allowed"
            )
        deadline = monotonic() + self._timeout_seconds
        payload = dict(body)
        payload.pop("stream")
        transport = _BoundedTransport(
            maximum_request_bytes=self._maximum_request_bytes,
            maximum_response_bytes=self._maximum_response_bytes,
            deadline=deadline,
        )
        try:
            remaining = _remaining(deadline)
            with httpx2.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx2.Timeout(remaining),
            ) as http_client:
                with self._openai_client(
                    api_key=access_token,
                    base_url=self._base_url,
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
                    timeout=remaining,
                ) as client:
                    started_at = monotonic()
                    stream = client.responses.create(
                        **payload,
                        stream=True,
                        extra_headers={"session_id": str(uuid4())},
                        timeout=_remaining(deadline),
                    )
                    with cast(Any, stream) as events:
                        return _project_events(
                            events,
                            started_at=started_at,
                            tools_disabled=payload.get("tool_choice") == "none",
                        )
        except Exception as error:
            failure = _sanitized_failure(
                error,
                api_connection_error=self._api_connection_error,
                api_error=self._api_error,
                api_status_error=self._api_status_error,
                api_timeout_error=self._api_timeout_error,
            )
        raise failure from None


def _load_sdk() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from openai import (
            APIConnectionError,
            APIError,
            APIStatusError,
            APITimeoutError,
            OpenAI,
        )
    except ImportError:
        imported: tuple[Any, Any, Any, Any, Any] | None = None
    else:
        imported = (
            APIConnectionError,
            APIError,
            APIStatusError,
            APITimeoutError,
            OpenAI,
        )
    if imported is None:
        raise OpenAIResponsesUnavailable(
            "OpenAI SDK candidate dependency is unavailable"
        )
    return imported


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _DeadlineExceeded
    return remaining


def _sanitized_failure(
    error: Exception,
    *,
    api_connection_error: type[Exception],
    api_error: type[Exception],
    api_status_error: type[Exception],
    api_timeout_error: type[Exception],
) -> OpenAIResponsesTransportError:
    if isinstance(error, OpenAIResponsesTransportError):
        return type(error)(str(error))
    if _caused_by(error, (_RequestTooLarge, _ResponseTooLarge)):
        return OpenAIResponsesTransportError(
            "OpenAI SDK request or response exceeded its byte limit")
    if _caused_by(error, (_DeadlineExceeded,)):
        return OpenAIResponsesUnavailable(
            "OpenAI SDK provider exceeded its total deadline")
    if isinstance(error, api_status_error):
        status_code = getattr(error, "status_code", None)
        if status_code in {401, 403}:
            return OpenAIResponsesCredentialRejected(
                "OpenAI SDK credentials were rejected")
        if isinstance(status_code, int):
            return OpenAIResponsesTransportError(
                f"OpenAI SDK provider returned HTTP {status_code}")
    if isinstance(error, (api_timeout_error, api_connection_error)):
        return OpenAIResponsesUnavailable("OpenAI SDK provider is unavailable")
    if isinstance(error, api_error):
        return OpenAIResponsesTransportError(
            "OpenAI SDK response protocol is invalid")
    return OpenAIResponsesTransportError("OpenAI SDK transport failed closed")


def _caused_by(error: BaseException, kinds: tuple[type[BaseException], ...]) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, kinds):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _validate_request(
    body: dict[str, Any], *, access_token: str, account_id: str
) -> None:
    if not access_token or not account_id:
        raise ValueError("OpenAI SDK lease pair is incomplete")
    if set(body) - (_ALLOWED_BODY_FIELDS | {"stream"}):
        raise OpenAIResponsesTransportError(
            "OpenAI SDK request contains an unsupported field"
        )
    if (
        body.get("stream") is not True
        or body.get("store") is not False
        or body.get("parallel_tool_calls") is not False
        or not isinstance(body.get("model"), str)
        or not body["model"].strip()
        or not isinstance(body.get("input"), list)
        or not isinstance(body.get("tools"), list)
        or any(
            not isinstance(tool, dict)
            or not (
                tool.get("type") == "function"
                and tool.get("defer_loading") in {None, True}
                or index == 0
                and tool == {"execution": "server", "type": "tool_search"}
            )
            for index, tool in enumerate(body.get("tools", []))
        )
    ):
        raise OpenAIResponsesTransportError("OpenAI SDK request contract is invalid")
    try:
        canonical_json_bytes(body)
    except (TypeError, ValueError) as error:
        raise OpenAIResponsesTransportError(
            "OpenAI SDK request is not canonical JSON"
        ) from error


def _project_events(
    events: Iterator[Any],
    *,
    started_at: float | None = None,
    clock: Callable[[], float] = monotonic,
    tools_disabled: bool = False,
) -> OpenAIResponsesResult:
    deltas: list[str] = []
    completed_parts: list[str] = []
    action_call: ConversationActionCall | None = None
    completed_usage: Any | None = None
    completed = False
    first_text_delta_seconds: float | None = None
    try:
        for event in events:
            kind = getattr(event, "type", None)
            if completed:
                raise OpenAIResponsesTransportError(
                    "OpenAI SDK returned an event after completion"
                )
            if kind == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if not isinstance(delta, str):
                    raise OpenAIResponsesTransportError(
                        "OpenAI SDK text delta is invalid"
                    )
                if delta and started_at is not None and first_text_delta_seconds is None:
                    first_text_delta_seconds = max(0.0, clock() - started_at)
                publish_conversation_text(delta, tools_disabled=tools_disabled)
                deltas.append(delta)
                continue
            if kind == "response.output_item.done":
                item = getattr(event, "item", None)
                item_type = getattr(item, "type", None)
                if item_type == "function_call":
                    if action_call is not None:
                        raise OpenAIResponsesTransportError(
                            "OpenAI SDK returned multiple tool calls"
                        )
                    action_call = _action_call(item)
                elif item_type == "message":
                    completed_parts.extend(_message_text_parts(item))
                elif item_type not in {"reasoning", "tool_search_call"}:
                    safe_item_type = (
                        item_type
                        if isinstance(item_type, str)
                        and _SAFE_OUTPUT_ITEM_TYPE.fullmatch(item_type)
                        else "unknown"
                    )
                    raise OpenAIResponsesTransportError(
                        "OpenAI SDK returned an unsupported output item "
                        f"({safe_item_type})"
                    )
                continue
            if kind == "response.completed":
                if completed:
                    raise OpenAIResponsesTransportError(
                        "OpenAI SDK returned multiple completion events"
                    )
                completed = True
                response = getattr(event, "response", None)
                if (
                    getattr(response, "status", None) != "completed"
                    or getattr(response, "error", None) is not None
                ):
                    raise OpenAIResponsesTransportError(
                        "OpenAI SDK completion status is invalid"
                    )
                completed_usage = getattr(response, "usage", None)
                continue
            if kind in {"error", "response.failed", "response.incomplete"}:
                raise OpenAIResponsesTransportError(
                    "OpenAI SDK provider reported failure"
                )
    except OpenAIResponsesTransportError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise OpenAIResponsesTransportError(
            "OpenAI SDK response projection is invalid"
        ) from error
    if not completed:
        raise OpenAIResponsesTransportError(
            "OpenAI SDK stream ended without completion"
        )
    text = "".join(deltas) if deltas else "".join(completed_parts)
    usage = _usage(completed_usage, tool_calls=int(action_call is not None))
    if not text.strip() and action_call is None:
        raise OpenAIResponsesTransportError(
            "OpenAI SDK returned neither text nor a tool call"
        )
    completed_seconds = (
        None if started_at is None else max(0.0, clock() - started_at)
    )
    return OpenAIResponsesResult(
        text,
        action_call,
        usage,
        first_text_delta_seconds,
        completed_seconds,
    )


def _action_call(item: Any) -> ConversationActionCall:
    call_id = getattr(item, "call_id", None)
    name = getattr(item, "name", None)
    encoded_arguments = getattr(item, "arguments", None)
    if not all(isinstance(value, str) for value in (call_id, name, encoded_arguments)):
        raise OpenAIResponsesTransportError("OpenAI SDK tool call is invalid")
    arguments = json.loads(cast(str, encoded_arguments))
    if not isinstance(arguments, dict):
        raise OpenAIResponsesTransportError(
            "OpenAI SDK tool arguments are not an object"
        )
    return ConversationActionCall(cast(str, call_id), cast(str, name), arguments)


def _message_text_parts(item: Any) -> list[str]:
    content = getattr(item, "content", None)
    if not isinstance(content, list):
        return []
    return [text for part in content
            if isinstance((text := getattr(part, "text", None)), str)]


def _usage(value: Any, *, tool_calls: int) -> ConversationModelUsage:
    if value is None:
        return ConversationModelUsage.unmeasured()
    input_tokens = getattr(value, "input_tokens", None)
    output_tokens = getattr(value, "output_tokens", None)
    total_tokens = getattr(value, "total_tokens", None)
    if (
        not isinstance(input_tokens, int)
        or isinstance(input_tokens, bool)
        or input_tokens < 0
        or not isinstance(output_tokens, int)
        or isinstance(output_tokens, bool)
        or output_tokens < 0
        or not isinstance(total_tokens, int)
        or isinstance(total_tokens, bool)
        or total_tokens != input_tokens + output_tokens
    ):
        raise OpenAIResponsesTransportError("OpenAI SDK usage is invalid")
    return ConversationModelUsage.measured(
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


__all__ = ["OpenAIResponsesCredentialRejected", "OpenAIResponsesResult",
           "OpenAIResponsesTransport", "OpenAIResponsesTransportError",
           "OpenAIResponsesUnavailable"]
