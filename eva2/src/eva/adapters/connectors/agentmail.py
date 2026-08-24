"""Bounded read-only AgentMail connector using the documented HTTP API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from typing import Any
from urllib import error, parse, request as urllib_request

from eva.application.mail import MailInboxPage, MailMessage, MailMessageSummary


_IDENTIFIER = re.compile(r"^[A-Za-z0-9@._:+-]{3,240}$")


@dataclass(frozen=True, slots=True)
class AgentMailConfig:
    inbox_id: str
    api_key: str = field(repr=False)
    base_url: str = "https://api.agentmail.to/v0"
    request_timeout_seconds: int = 15

    def __post_init__(self) -> None:
        parsed = parse.urlsplit(self.base_url)
        if not parsed.netloc or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            )
        ):
            raise ValueError("AgentMail URL must be HTTPS")
        if _IDENTIFIER.fullmatch(self.inbox_id) is None:
            raise ValueError("AgentMail inbox ID is invalid")
        if not self.api_key or len(self.api_key) > 4096:
            raise ValueError("AgentMail API key is invalid")
        if not 2 <= self.request_timeout_seconds <= 30:
            raise ValueError("AgentMail timeout is invalid")


class AgentMailReadAdapter:
    def __init__(self, config: AgentMailConfig) -> None:
        self._config = config

    async def list_messages(
        self, *, limit: int = 10, page_token: str | None = None
    ) -> MailInboxPage:
        if isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("AgentMail list limit is outside 1..20")
        if page_token is not None and (
            not page_token or len(page_token) > 2048 or not page_token.isascii()
        ):
            raise ValueError("AgentMail page token is invalid")
        query = {"limit": str(limit), "include_spam": "false"}
        if page_token is not None:
            query["page_token"] = page_token
        raw = await asyncio.to_thread(
            self._request_json,
            "GET",
            "/inboxes/"
            + parse.quote(self._config.inbox_id, safe="@._+-")
            + "/messages?"
            + parse.urlencode(query),
        )
        messages_raw = raw.get("messages")
        if not isinstance(messages_raw, list) or len(messages_raw) > limit:
            raise RuntimeError("AgentMail message page is invalid")
        messages = tuple(self._summary(value) for value in messages_raw)
        next_page_token = raw.get("next_page_token")
        if next_page_token is not None and (
            not isinstance(next_page_token, str)
            or not next_page_token
            or len(next_page_token) > 2048
            or not next_page_token.isascii()
        ):
            raise RuntimeError("AgentMail next-page token is invalid")
        return MailInboxPage(self._config.inbox_id, messages, next_page_token)

    async def get_message(self, message_id: str) -> MailMessage:
        if _IDENTIFIER.fullmatch(message_id) is None:
            raise ValueError("AgentMail message ID is invalid")
        raw = await asyncio.to_thread(
            self._request_json,
            "GET",
            "/inboxes/"
            + parse.quote(self._config.inbox_id, safe="@._+-")
            + "/messages/"
            + parse.quote(message_id, safe="@._:+-"),
        )
        summary = self._summary(raw)
        if summary.message_id != message_id:
            raise RuntimeError("AgentMail returned another message")
        text = raw.get("text") or ""
        extracted = raw.get("extracted_text") or ""
        attachments = raw.get("attachments") or []
        if (
            not isinstance(text, str)
            or not isinstance(extracted, str)
            or len(text.encode()) > 100_000
            or len(extracted.encode()) > 100_000
            or not isinstance(attachments, list)
            or len(attachments) > 100
        ):
            raise RuntimeError("AgentMail message content is invalid")
        return MailMessage(summary, text, extracted, len(attachments))

    def _request_json(self, method: str, path: str) -> dict[str, Any]:
        request = urllib_request.Request(
            self._config.base_url.rstrip("/") + path,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
        )
        try:
            with urllib_request.urlopen(
                request, timeout=self._config.request_timeout_seconds
            ) as response:
                raw = response.read(1_048_577)
        except error.HTTPError as exc:
            raise RuntimeError(f"AgentMail read failed ({exc.code})") from exc
        if len(raw) > 1_048_576:
            raise RuntimeError("AgentMail response is too large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("AgentMail response is invalid")
        return value

    @staticmethod
    def _summary(raw: object) -> MailMessageSummary:
        if not isinstance(raw, dict):
            raise RuntimeError("AgentMail message summary is invalid")
        recipients = raw.get("to")
        labels = raw.get("labels")
        required = {
            "message_id": raw.get("message_id"),
            "thread_id": raw.get("thread_id"),
            "timestamp": raw.get("timestamp"),
            "from": raw.get("from"),
            "subject": raw.get("subject"),
            "preview": raw.get("preview"),
        }
        if (
            any(not isinstance(value, str) for value in required.values())
            or not isinstance(recipients, list)
            or not all(isinstance(value, str) for value in recipients)
            or not isinstance(labels, list)
            or not all(isinstance(value, str) for value in labels)
        ):
            raise RuntimeError("AgentMail message summary is invalid")
        values = {key: str(value) for key, value in required.items()}
        if any(len(value.encode()) > 20_000 for value in values.values()):
            raise RuntimeError("AgentMail message summary is too large")
        return MailMessageSummary(
            values["message_id"],
            values["thread_id"],
            values["timestamp"],
            values["from"],
            tuple(recipients),
            values["subject"],
            values["preview"],
            tuple(labels),
        )


__all__ = ["AgentMailConfig", "AgentMailReadAdapter"]
