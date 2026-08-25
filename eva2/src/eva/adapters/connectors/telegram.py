"""Bounded Telegram Bot API adapters for proactive and conversational delivery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib import error, request as urllib_request

from eva.application.free_will import ProactiveExpressionClaim
from eva.application.telegram_ingress import (
    TelegramCoreReply,
    TelegramInboundMessage,
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

    def _call(self, method: str, payload: dict[str, object]) -> object:
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
            with urllib_request.urlopen(request, timeout=self._timeout + 2) as response:
                raw = response.read(1_048_577)
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError(f"Telegram {method} failed") from exc
        if len(raw) > 1_048_576:
            raise RuntimeError(f"Telegram {method} receipt is too large")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Telegram {method} receipt is invalid") from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
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
                or not isinstance(chat.get("id"), int)
                or not isinstance(sender.get("id"), int)
                or not isinstance(message_id, int)
                or not isinstance(occurred_at, int)
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
        if not text.strip() or len(text) > 4096:
            raise ValueError("Telegram reply text is invalid")
        result = self._call(
            "sendMessage",
            {
                "chat_id": message.chat_id,
                "reply_parameters": {"message_id": message.message_id},
                "text": text,
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise RuntimeError("Telegram sendMessage receipt is invalid")
        return f"telegram:{message.chat_id}:{result['message_id']}"


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
        if (
            not isinstance(decoded, dict)
            or decoded.get("contract") != "EvaInternalGroundedReply/v1"
            or decoded.get("conversation_id") != conversation_id
            or decoded.get("message_id") != message_id
            or not isinstance(decoded.get("text"), str)
            or not isinstance(decoded.get("text_sha256"), str)
        ):
            raise RuntimeError("Living Core conversation reply contract differs")
        return TelegramCoreReply(
            conversation_id,
            message_id,
            decoded["text"],
            decoded["text_sha256"],
        )


__all__ = [
    "LivingCoreTurnHttpAdapter",
    "TelegramBotApiAdapter",
    "TelegramProactiveDelivery",
]
