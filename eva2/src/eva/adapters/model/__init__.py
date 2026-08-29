"""Purpose-scoped model adapters with sidecar-safe lazy public exports."""

from typing import Any

__all__ = [
    "CodexConversationModel",
    "ConversationModelError",
]


def __getattr__(name: str) -> Any:
    if name in {"CodexConversationModel", "ConversationModelError"}:
        from .conversation import CodexConversationModel, ConversationModelError

        return {
            "CodexConversationModel": CodexConversationModel,
            "ConversationModelError": ConversationModelError,
        }[name]
    raise AttributeError(name)
