"""Provider-neutral model adapter status and failure contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from eva.application.conversation import ConversationModelUsage


class ConversationModelError(RuntimeError):
    """The configured conversation model did not return a usable result."""

    def __init__(self, *args: object, model_usage: ConversationModelUsage | None = None) -> None:
        if model_usage is not None and not isinstance(model_usage, ConversationModelUsage):
            raise ValueError("model failure usage is invalid")
        self.model_usage = model_usage
        super().__init__(*args)


class ConversationModelRateLimited(ConversationModelError):
    """The provider refused this request with HTTP 429; no immediate retry."""


class ConversationModelDeadlineExceeded(ConversationModelError):
    """The model phase reached its deadline without a usable result."""


@dataclass(frozen=True, slots=True)
class ConversationCredentialStatus:
    """Non-secret readiness state for the current model access lease."""

    ready: bool
    state: Literal["ready", "expired", "rejected", "invalid", "unavailable"]
    expires_at: datetime | None = None


__all__ = ["ConversationCredentialStatus", "ConversationModelError", "ConversationModelDeadlineExceeded"]
