"""Provider-neutral model adapter status and failure contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


class ConversationModelError(RuntimeError):
    """The configured conversation model did not return a usable result."""


@dataclass(frozen=True, slots=True)
class ConversationCredentialStatus:
    """Non-secret readiness state for the current model access lease."""

    ready: bool
    state: Literal["ready", "expired", "rejected", "invalid", "unavailable"]
    expires_at: datetime | None = None


__all__ = ["ConversationCredentialStatus", "ConversationModelError"]
