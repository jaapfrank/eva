"""Purpose-scoped model adapters with sidecar-safe lazy public exports."""

from typing import Any

__all__ = ["CodexConversationModel", "ConversationModelError"]


def __getattr__(name: str) -> Any:
    if name == "ConversationModelError":
        from .contracts import ConversationModelError

        return ConversationModelError
    if name == "CodexConversationModel":
        from .conversation import CodexConversationModel

        return CodexConversationModel
    raise AttributeError(name)
