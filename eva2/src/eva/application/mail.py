"""Connector-neutral mail read contracts for Eva's living core."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from eva.contracts.canonical_json import canonical_sha256


@dataclass(frozen=True, slots=True)
class MailMessageSummary:
    message_id: str
    thread_id: str
    timestamp: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    preview: str
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MailInboxPage:
    inbox_id: str
    messages: tuple[MailMessageSummary, ...]
    next_page_token: str | None

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaMailInboxPage/v1",
            "inbox_id": self.inbox_id,
            "messages": [
                {
                    **asdict(message),
                    "labels": list(message.labels),
                    "recipients": list(message.recipients),
                }
                for message in self.messages
            ],
            "next_page_token": self.next_page_token,
        }


@dataclass(frozen=True, slots=True)
class MailMessage:
    summary: MailMessageSummary
    text: str
    extracted_text: str
    attachment_count: int

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "attachment_count": self.attachment_count,
            "contract": "EvaMailMessage/v1",
            "extracted_text": self.extracted_text,
            "summary": {
                **asdict(self.summary),
                "labels": list(self.summary.labels),
                "recipients": list(self.summary.recipients),
            },
            "text": self.text,
        }


class MailReadPort(Protocol):
    async def list_messages(
        self, *, limit: int = 10, page_token: str | None = None
    ) -> MailInboxPage: ...

    async def get_message(self, message_id: str) -> MailMessage: ...


__all__ = ["MailInboxPage", "MailMessage", "MailMessageSummary", "MailReadPort"]
