"""Bounded Telegram Bot API adapters for proactive and conversational delivery."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4
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
        chat_id: str | None = None,
        recipient_resolver: Callable[[], str] | None = None,
        timeout_seconds: int = 10,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if ":" not in bot_token or any(character.isspace() for character in bot_token):
            raise ValueError("Telegram bot token is invalid")
        if chat_id is None and recipient_resolver is None:
            raise ValueError("Telegram proactive chat ID is invalid")
        if chat_id is not None:
            self._validate_private_chat(chat_id)
        if not 2 <= timeout_seconds <= 20:
            raise ValueError("Telegram proactive timeout is invalid")
        if not api_base_url.startswith(("http://", "https://")):
            raise ValueError("Telegram API URL is invalid")
        self._api = TelegramBotApiAdapter(
            bot_token=bot_token, timeout_seconds=timeout_seconds, api_base_url=api_base_url,
        )
        self._chat_id = chat_id
        self._recipient_resolver = recipient_resolver
        self._timeout = timeout_seconds

    @staticmethod
    def _validate_private_chat(chat_id: str) -> None:
        if (not isinstance(chat_id, str) or not chat_id.isascii() or not chat_id.isdigit()
                or len(chat_id) > 16 or int(chat_id) < 1 or str(int(chat_id)) != chat_id):
            raise ValueError("Telegram proactive recipient must be a private user")

    def deliver(self, claim: ProactiveExpressionClaim) -> str:
        return self._deliver_text(claim.text, silent=claim.urgency < 3)

    def deliver_text(self, text: str, *, expected_chat_id: str,
                     reply_to_message_id: int | None = None) -> str:
        """Deliver a saved result only to its still-current bound private recipient.

        The caller owns durable claiming and ambiguous-acceptance recovery. This
        transport makes one attempt and never substitutes a newly paired person.
        """
        self._validate_private_chat(expected_chat_id)
        return self._deliver_text(text, silent=True, expected_chat_id=expected_chat_id,
                                  reply_to_message_id=reply_to_message_id)

    def _deliver_text(self, text: str, *, silent: bool,
                      expected_chat_id: str | None = None,
                      reply_to_message_id: int | None = None) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError("Telegram proactive text is invalid")
        chat_id = self._chat_id if self._recipient_resolver is None else self._recipient_resolver()
        self._validate_private_chat(chat_id)
        if self._chat_id is not None and self._chat_id != chat_id:
            raise RuntimeError("configured Telegram recipient differs from verified owner")
        if expected_chat_id is not None and expected_chat_id != chat_id:
            raise RuntimeError("saved Telegram destination differs from verified owner")
        payload: dict[str, object] = {
            "chat_id": chat_id, "disable_notification": silent, "text": text,
        }
        if reply_to_message_id is not None:
            if type(reply_to_message_id) is not int or reply_to_message_id < 1:
                raise ValueError("Telegram reply message identity is invalid")
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": False,
            }
        result = self._api._call(
            "sendMessage",
            payload,
            timeout_seconds=self._timeout,
            maximum_response_bytes=65_536,
        )
        message_id = result.get("message_id") if isinstance(result, dict) else None
        chat = result.get("chat") if isinstance(result, dict) else None
        if (type(message_id) is not int or message_id < 1
                or not isinstance(chat, dict) or type(chat.get("id")) is not int
                or str(chat["id"]) != chat_id or chat.get("type") != "private"
                or result.get("text") != text):
            raise RuntimeError("Telegram proactive receipt does not prove the intended private delivery")
        if reply_to_message_id is not None:
            replied = result.get("reply_to_message")
            if (not isinstance(replied, dict)
                    or type(replied.get("message_id")) is not int
                    or replied["message_id"] != reply_to_message_id):
                raise RuntimeError("Telegram receipt does not prove the original question binding")
        return f"telegram:{chat_id}:{message_id}"

    def deliver_document(self, text: str, *, expected_chat_id: str,
                         reply_to_message_id: int) -> str:
        """Send one complete UTF-8 result and verify Telegram's stored bytes.

        Only the accepted file is read back. A failed or uncertain send is never
        retried here; the existing durable claim owns its disposition.
        """
        raw = text.encode('utf-8')
        if not text.strip() or len(raw) > 65_536:
            raise ValueError('Telegram saved document is invalid')
        self._validate_private_chat(expected_chat_id)
        chat_id = self._chat_id if self._recipient_resolver is None else self._recipient_resolver()
        self._validate_private_chat(chat_id)
        if chat_id != expected_chat_id or (self._chat_id is not None and self._chat_id != chat_id):
            raise RuntimeError('saved Telegram destination differs from verified owner')
        if type(reply_to_message_id) is not int or reply_to_message_id < 1:
            raise ValueError('Telegram reply message identity is invalid')
        filename = 'eva-volledig-resultaat.md'
        caption = 'Eva2: het volledige opgeslagen resultaat, inclusief bronnen.'
        result = self._api._call('sendDocument', {
            'chat_id': chat_id, 'disable_notification': True, 'caption': caption,
            'reply_parameters': {'message_id': reply_to_message_id, 'allow_sending_without_reply': False},
        }, timeout_seconds=self._timeout, maximum_response_bytes=65_536,
            document=(filename, raw))
        if not isinstance(result, dict):
            raise RuntimeError('Telegram document receipt is invalid')
        chat, document, replied = (result.get(key) for key in ('chat', 'document', 'reply_to_message'))
        message_id = result.get('message_id')
        if (type(message_id) is not int or message_id < 1
                or not isinstance(chat, dict) or type(chat.get('id')) is not int
                or str(chat['id']) != chat_id or chat.get('type') != 'private'
                or result.get('caption') != caption
                or not isinstance(replied, dict) or type(replied.get('message_id')) is not int
                or replied['message_id'] != reply_to_message_id
                or not isinstance(document, dict) or document.get('file_name') != filename
                or type(document.get('file_size')) is not int or document['file_size'] != len(raw)
                or not isinstance(document.get('file_id'), str) or not document['file_id']):
            raise RuntimeError('Telegram document receipt does not prove the original delivery')
        stored = self._api.download_document(document['file_id'], maximum_bytes=len(raw))
        if stored != raw:
            raise RuntimeError('Telegram document content differs from the saved result')
        return f"telegram:{chat_id}:{message_id}:sha256:{hashlib.sha256(stored).hexdigest()}"


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
        maximum_response_bytes: int = 1_048_576,
        document: tuple[str, bytes] | None = None,
    ) -> object:
        if type(maximum_response_bytes) is not int or not 1 <= maximum_response_bytes <= 1_048_576:
            raise ValueError("Telegram response bound is invalid")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        content_type = 'application/json'
        if document is not None:
            filename, content = document
            if method != 'sendDocument' or filename != 'eva-volledig-resultaat.md' or len(content) > 65_536:
                raise ValueError('Telegram document upload is invalid')
            boundary = 'eva-' + uuid4().hex
            fields = []
            for key, value in payload.items():
                wire = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
                fields.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{wire}\r\n'.encode())
            fields.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\nContent-Type: text/markdown; charset=utf-8\r\n\r\n'.encode(),
                           content, f'\r\n--{boundary}--\r\n'.encode()))
            encoded = b''.join(fields)
            content_type = f'multipart/form-data; boundary={boundary}'
        request = urllib_request.Request(
            f"{self._base_url}/{method}",
            data=encoded,
            headers={"Content-Type": content_type},
            method="POST",
        )
        failed_status = False
        try:
            with urllib_request.urlopen(
                request,
                timeout=self._timeout + 2 if timeout_seconds is None else timeout_seconds,
            ) as response:
                raw = response.read(maximum_response_bytes + 1)
        except error.HTTPError as exc:
            failed_status = True
            try:
                raw = exc.read(maximum_response_bytes + 1)
            finally:
                exc.close()
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError(f"Telegram {method} failed") from exc
        if len(raw) > maximum_response_bytes:
            raise RuntimeError(f"Telegram {method} receipt is too large")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Telegram {method} receipt is invalid") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError(f"Telegram {method} receipt is invalid")
        if failed_status and decoded.get("ok") is True:
            raise RuntimeError(f"Telegram {method} HTTP error contradicts its receipt")
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

    def download_document(self, file_id: str, *, maximum_bytes: int) -> bytes:
        """Read only the provider file bound by the sendDocument receipt."""
        if not 1 <= maximum_bytes <= 65_536:
            raise ValueError('Telegram document readback bound is invalid')
        metadata = self._call('getFile', {'file_id': file_id}, maximum_response_bytes=8192)
        path = metadata.get('file_path') if isinstance(metadata, dict) else None
        if (not isinstance(metadata, dict) or metadata.get('file_id') != file_id
                or type(metadata.get('file_size')) is not int or metadata['file_size'] != maximum_bytes
                or not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_/-]+\.[A-Za-z0-9]+', path)
                or path.startswith('/') or '..' in path):
            raise RuntimeError('Telegram stored document metadata is invalid')
        origin, token = self._base_url.rsplit('/bot', 1)
        try:
            with urllib_request.urlopen(f'{origin}/file/bot{token}/{path}', timeout=self._timeout) as response:
                raw = response.read(maximum_bytes + 1)
        except (TimeoutError, error.URLError) as exc:
            raise RuntimeError('Telegram document readback failed') from exc
        if len(raw) != maximum_bytes:
            raise RuntimeError('Telegram stored document size differs')
        return raw

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
                "reply_parameters": {"message_id": message.message_id, "allow_sending_without_reply": False},
                "text": text,
            },
        )
        message_id = result.get("message_id") if isinstance(result, dict) else None
        chat = result.get("chat") if isinstance(result, dict) else None
        original = result.get("reply_to_message") if isinstance(result, dict) else None
        if (type(message_id) is not int or message_id < 1
                or not isinstance(chat, dict) or type(chat.get("id")) is not int
                or str(chat["id"]) != message.chat_id
                or chat.get("type") not in (
                    {"group", "supergroup"} if message.chat_id.startswith("-") else {"private"})
                or not isinstance(original, dict)
                or type(original.get("message_id")) is not int
                or original["message_id"] != message.message_id
                or result.get("text") != text):
            raise RuntimeError("Telegram sendMessage receipt does not prove the exact reply")
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

    def stop_pending(self, message: TelegramInboundMessage, *, stop_message: TelegramInboundMessage, principal_id: str, audience: MemoryAudience) -> None:
        payload, conversation_id, message_id = self._turn_request(
            message, principal_id=principal_id, audience=audience,
        )
        if stop_message.chat_id != message.chat_id or stop_message.sender_id != message.sender_id:
            raise ValueError("stop sender differs")
        _, _, stop_id = self._turn_request(stop_message, principal_id=principal_id, audience=audience)
        decoded_payload = json.loads(payload)
        decoded_payload["stop_request_message_id"] = stop_id
        payload = json.dumps(decoded_payload).encode()
        request = urllib_request.Request(
            self._url.removesuffix("/respond") + "/stop", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(2049)
            decoded = json.loads(raw)
            if (len(raw) > 2048 or not isinstance(decoded, dict)
                    or set(decoded) != {"contract", "conversation_id", "message_id", "stop_request_message_id"}
                    or decoded["contract"] != "EvaTurnStop/v1" or decoded["conversation_id"] != conversation_id
                    or decoded["stop_request_message_id"] != stop_id
                    or not isinstance(decoded["message_id"], str)
                    or not decoded["message_id"].startswith(conversation_id + ":")
                    or len(decoded["message_id"]) > 240):
                raise ValueError("stop receipt differs")
        except (TimeoutError, error.URLError, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("Living Core stop was not confirmed") from exc

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
