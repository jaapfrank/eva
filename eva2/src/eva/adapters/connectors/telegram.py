"""Bounded Telegram Bot API adapters for proactive and conversational delivery."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Callable
from urllib import error, request as urllib_request

from eva.application.free_will import ProactiveExpressionClaim
from eva.application.telegram_ingress import (
    TELEGRAM_MAXIMUM_TEXT_CHARACTERS,
    TelegramCoreReply,
    TelegramInboundMessage,
    TelegramProviderRateLimited,
)
from eva.application.memory_audience import (
    MemoryAudience,
    memory_audience_to_wire,
)


class TelegramProactiveDelivery:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout_seconds: int = 10,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if ":" not in bot_token or any(character.isspace() for character in bot_token):
            raise ValueError("Telegram bot token is invalid")
        if not chat_id or len(chat_id) > 32 or not chat_id.lstrip("-").isdigit():
            raise ValueError("Telegram proactive chat ID is invalid")
        if not 2 <= timeout_seconds <= 20:
            raise ValueError("Telegram proactive timeout is invalid")
        if not api_base_url.startswith(("http://", "https://")):
            raise ValueError("Telegram API URL is invalid")
        self._url = f"{api_base_url.rstrip('/')}/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout_seconds

    def deliver(self, claim: ProactiveExpressionClaim) -> str:
        if not claim.text.strip() or len(claim.text) > 4096:
            raise ValueError("Telegram proactive text is invalid")
        payload = json.dumps(
            {
                "chat_id": self._chat_id,
                "disable_notification": claim.urgency < 3,
                "text": claim.text,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        request = urllib_request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(65_537)
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError("Telegram proactive delivery failed") from exc
        if len(raw) > 65_536:
            raise RuntimeError("Telegram proactive receipt is too large")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Telegram proactive receipt is invalid") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("Telegram proactive receipt is invalid")
        result = decoded.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if decoded.get("ok") is not True or not isinstance(message_id, int):
            raise RuntimeError("Telegram did not accept the proactive expression")
        return f"telegram:{self._chat_id}:{message_id}"


class TelegramBotApiAdapter:
    """Long-poll text updates and return exact sendMessage acceptance receipts."""

    def __init__(
        self,
        *,
        bot_token: str,
        timeout_seconds: int = 10,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if ":" not in bot_token or any(character.isspace() for character in bot_token):
            raise ValueError("Telegram bot token is invalid")
        if not 2 <= timeout_seconds <= 20:
            raise ValueError("Telegram timeout is invalid")
        if not api_base_url.startswith(("http://", "https://")):
            raise ValueError("Telegram API URL is invalid")
        self._base_url = f"{api_base_url.rstrip('/')}/bot{bot_token}"
        self._timeout = timeout_seconds

    def _call(
        self,
        method: str,
        payload: dict[str, object],
        *,
        timeout_seconds: int | None = None,
    ) -> object:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        request = urllib_request.Request(
            f"{self._base_url}/{method}",
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                request,
                timeout=self._timeout + 2 if timeout_seconds is None else timeout_seconds,
            ) as response:
                raw = response.read(1_048_577)
        except error.HTTPError as exc:
            try:
                raw = exc.read(1_048_577)
            finally:
                exc.close()
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError(f"Telegram {method} failed") from exc
        if len(raw) > 1_048_576:
            raise RuntimeError(f"Telegram {method} receipt is too large")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Telegram {method} receipt is invalid") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError(f"Telegram {method} receipt is invalid")
        if decoded.get("ok") is not True:
            parameters = decoded.get("parameters")
            retry_after = (
                parameters.get("retry_after")
                if isinstance(parameters, dict)
                else None
            )
            if (
                decoded.get("error_code") == 429
                and type(retry_after) is int
                and 1 <= retry_after <= 86_400
            ):
                raise TelegramProviderRateLimited(retry_after)
            raise RuntimeError(f"Telegram {method} was not accepted")
        return decoded.get("result")

    def poll(self, *, offset: int) -> tuple[TelegramInboundMessage, ...]:
        if offset < 0:
            raise ValueError("Telegram polling offset is invalid")
        result = self._call(
            "getUpdates",
            {
                "allowed_updates": ["message"],
                "limit": 32,
                "offset": offset,
                "timeout": self._timeout,
            },
        )
        if not isinstance(result, list) or len(result) > 32:
            raise RuntimeError("Telegram getUpdates result is invalid")
        updates: list[TelegramInboundMessage] = []
        for item in result:
            if not isinstance(item, dict):
                raise RuntimeError("Telegram update is invalid")
            message = item.get("message")
            update_id = item.get("update_id")
            if not isinstance(update_id, int) or not isinstance(message, dict):
                raise RuntimeError("Telegram update identity is invalid")
            chat = message.get("chat")
            sender = message.get("from")
            message_id = message.get("message_id")
            occurred_at = message.get("date")
            if (
                not isinstance(chat, dict)
                or not isinstance(sender, dict)
                or type(chat.get("id")) is not int
                or type(sender.get("id")) is not int
                or type(message_id) is not int
                or type(occurred_at) is not int
                or occurred_at < 0
            ):
                raise RuntimeError("Telegram message envelope is invalid")
            text = message.get("text")
            updates.append(
                TelegramInboundMessage(
                    update_id,
                    str(chat["id"]),
                    message_id,
                    sender["id"],
                    datetime.fromtimestamp(occurred_at, tz=UTC),
                    text if isinstance(text, str) else None,
                )
            )
        return tuple(updates)

    def send_reply(self, message: TelegramInboundMessage, text: str) -> str:
        if (
            not text.strip()
            or len(text) > TELEGRAM_MAXIMUM_TEXT_CHARACTERS
        ):
            raise ValueError("Telegram reply text is invalid")
        result = self._call(
            "sendMessage",
            {
                "chat_id": message.chat_id,
                "reply_parameters": {"message_id": message.message_id},
                "text": text,
            },
        )
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int or message_id < 1:
            raise RuntimeError("Telegram sendMessage receipt is invalid")
        return f"telegram:{message.chat_id}:{message_id}"

    def send_reply_draft(self, message: TelegramInboundMessage, text: str) -> None:
        """Show Telegram's ephemeral AI draft; final delivery remains sendMessage."""

        if message.chat_id.startswith("-") or message.chat_id != str(message.sender_id):
            raise ValueError("Telegram reply drafts require a private user chat")
        if len(text) > TELEGRAM_MAXIMUM_TEXT_CHARACTERS:
            raise ValueError("Telegram reply draft text is invalid")
        digest = hashlib.sha256(
            f"{message.chat_id}:{message.message_id}".encode()
        ).digest()
        # Bot API Integer values are signed 32-bit; zero is not a valid draft ID.
        draft_id = int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF
        result = self._call(
            "sendMessageDraft",
            {
                "chat_id": int(message.chat_id),
                "draft_id": draft_id or 1,
                "text": text,
            },
            timeout_seconds=2,
        )
        if result is not True:
            raise RuntimeError("Telegram sendMessageDraft receipt is invalid")


class LivingCoreTurnHttpAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        installation_id: str,
        timeout_seconds: int = 20,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Living Core URL is invalid")
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("Living Core conversation scope is invalid")
        if not 2 <= timeout_seconds <= 60:
            raise ValueError("Living Core conversation timeout is invalid")
        self._url = f"{base_url.rstrip('/')}/internal/v1/turns/respond"
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._timeout = timeout_seconds

    def respond(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
    ) -> TelegramCoreReply:
        payload, conversation_id, message_id = self._turn_request(
            message, principal_id=principal_id, audience=audience
        )
        request = urllib_request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(65_537)
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError("Living Core conversation request failed") from exc
        if len(raw) > 65_536:
            raise RuntimeError("Living Core conversation reply is too large")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Living Core conversation reply is invalid") from exc
        return self._telegram_reply(
            self._decode_reply(decoded, conversation_id, message_id)
        )

    def respond_stream(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
        on_delta: Callable[[str], None],
    ) -> TelegramCoreReply:
        payload, conversation_id, message_id = self._turn_request(
            message, principal_id=principal_id, audience=audience
        )
        request = urllib_request.Request(
            self._url,
            data=payload,
            headers={
                "Accept": "application/x-ndjson",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        final: TelegramCoreReply | None = None
        raw_visible = ""
        visible = ""
        total_bytes = 0
        event_count = 0
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:
                if response.headers.get_content_type() != "application/x-ndjson":
                    raise RuntimeError("Living Core conversation stream type differs")
                while raw := response.readline(65_537):
                    total_bytes += len(raw)
                    event_count += 1
                    if len(raw) > 65_536 or total_bytes > 131_072 or event_count > 1_024:
                        raise RuntimeError("Living Core conversation stream is too large")
                    try:
                        decoded = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RuntimeError(
                            "Living Core conversation stream is invalid"
                        ) from exc
                    if not isinstance(decoded, dict):
                        raise RuntimeError("Living Core conversation stream is invalid")
                    contract = decoded.get("contract")
                    if contract == "EvaTextDelta/v1":
                        delta = decoded.get("text")
                        if (
                            set(decoded) != {"contract", "text"}
                            or not isinstance(delta, str)
                            or not delta
                            or final is not None
                        ):
                            raise RuntimeError(
                                "Living Core conversation delta is invalid"
                            )
                        raw_visible += delta
                        unbounded = raw_visible.strip()
                        telegram_text = (
                            unbounded[:-1]
                            if len(unbounded) == TELEGRAM_MAXIMUM_TEXT_CHARACTERS
                            else _fit_telegram_text(unbounded)
                        )
                        if not telegram_text.startswith(visible):
                            raise RuntimeError(
                                "Living Core conversation stream is not append-only"
                            )
                        safe_delta = telegram_text[len(visible) :]
                        if safe_delta:
                            on_delta(safe_delta)
                            visible = telegram_text
                    elif contract == "EvaInternalGroundedReply/v1":
                        if final is not None:
                            raise RuntimeError(
                                "Living Core conversation stream has two finals"
                            )
                        final = self._decode_reply(
                            decoded, conversation_id, message_id
                        )
                    elif contract == "EvaTextStreamError/v1":
                        raise RuntimeError("Living Core conversation stream failed")
                    else:
                        raise RuntimeError(
                            "Living Core conversation stream contract differs"
                        )
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError("Living Core conversation stream failed") from exc
        if final is None:
            raise RuntimeError("Living Core conversation stream has no final reply")
        telegram_final = self._telegram_reply(final)
        if raw_visible:
            raw_final = raw_visible.strip()
            expected = _fit_telegram_text(raw_final)
            if final.text != raw_final or not expected.startswith(visible):
                raise RuntimeError("Living Core conversation stream final differs")
            if telegram_final.text != expected:
                raise RuntimeError("Living Core Telegram rendering differs")
            final_delta = expected[len(visible) :]
            if final_delta:
                on_delta(final_delta)
        return telegram_final

    def _turn_request(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
    ) -> tuple[bytes, str, str]:
        if audience.principal_id != principal_id:
            raise ValueError("Telegram turn audience principal differs")
        if message.chat_id.startswith("-") != (audience.kind == "telegram_group"):
            raise ValueError("Telegram turn audience differs from chat")
        if audience.kind == "telegram_group" and (
            audience.group_chat_id != message.chat_id
        ):
            raise ValueError("Telegram group audience differs from chat")
        conversation_id = f"telegram:{message.chat_id}"
        message_id = f"telegram:{message.chat_id}:{message.message_id}"
        payload = json.dumps(
            {
                "audience": memory_audience_to_wire(audience),
                "conversation_id": conversation_id,
                "installation_id": self._installation_id,
                "message_id": message_id,
                "occurred_at": message.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "principal_id": principal_id,
                "tenant_id": self._tenant_id,
                "text": message.text,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return payload, conversation_id, message_id

    @staticmethod
    def _decode_reply(
        decoded: object, conversation_id: str, message_id: str
    ) -> TelegramCoreReply:
        if (
            not isinstance(decoded, dict)
            or decoded.get("contract") != "EvaInternalGroundedReply/v1"
            or decoded.get("conversation_id") != conversation_id
            or decoded.get("message_id") != message_id
            or not isinstance(decoded.get("text"), str)
            or not isinstance(decoded.get("text_sha256"), str)
        ):
            raise RuntimeError("Living Core conversation reply contract differs")
        text = decoded["text"]
        text_sha256 = decoded["text_sha256"]
        if (
            not text.strip()
            or text != text.strip()
            or hashlib.sha256(text.encode()).hexdigest() != text_sha256
        ):
            raise RuntimeError("Living Core conversation reply content differs")
        return TelegramCoreReply(
            conversation_id,
            message_id,
            text,
            text_sha256,
        )

    @staticmethod
    def _telegram_reply(reply: TelegramCoreReply) -> TelegramCoreReply:
        text = _fit_telegram_text(reply.text)
        return TelegramCoreReply(
            reply.conversation_id,
            reply.message_id,
            text,
            hashlib.sha256(text.encode()).hexdigest(),
        )


def _fit_telegram_text(text: str) -> str:
    """Render a complete core reply within Telegram's single-message limit."""

    canonical = text.strip()
    if len(canonical) <= TELEGRAM_MAXIMUM_TEXT_CHARACTERS:
        return canonical
    return canonical[: TELEGRAM_MAXIMUM_TEXT_CHARACTERS - 1] + "…"


__all__ = [
    "LivingCoreTurnHttpAdapter",
    "TelegramBotApiAdapter",
    "TelegramProactiveDelivery",
]
