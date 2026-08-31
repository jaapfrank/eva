"""Purpose-scoped model adapters with sidecar-safe lazy public exports."""

from typing import Any

__all__ = ["PydanticConversationModel", "ConversationModelError"]


def __getattr__(name: str) -> Any:
    if name == "ConversationModelError":
        from .contracts import ConversationModelError

        return ConversationModelError
    if name == "PydanticConversationModel":
        from .pydantic_conversation import PydanticConversationModel

        return PydanticConversationModel
    raise AttributeError(name)
