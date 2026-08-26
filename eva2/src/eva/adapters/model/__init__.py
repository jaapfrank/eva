"""Purpose-scoped model adapters with sidecar-safe lazy public exports."""

from typing import Any

__all__ = [
    "CodexConversationModel",
    "ConversationModelError",
    "FixtureDispatchRejected",
    "FixtureInvocationResult",
    "FixtureModelAdapter",
    "FixtureModelError",
    "FixtureProgramError",
    "MODEL_ARTIFACT_SHA256",
    "MODEL_MANIFEST_SHA256",
    "MODEL_PROGRAM_SHA256",
    "SealedFixtureProgram",
]


def __getattr__(name: str) -> Any:
    if name in {"CodexConversationModel", "ConversationModelError"}:
        from .conversation import CodexConversationModel, ConversationModelError

        return {
            "CodexConversationModel": CodexConversationModel,
            "ConversationModelError": ConversationModelError,
        }[name]
    if name in set(__all__) - {"CodexConversationModel", "ConversationModelError"}:
        from .fixture import (
            MODEL_ARTIFACT_SHA256,
            MODEL_MANIFEST_SHA256,
            MODEL_PROGRAM_SHA256,
            FixtureDispatchRejected,
            FixtureInvocationResult,
            FixtureModelAdapter,
            FixtureModelError,
            FixtureProgramError,
            SealedFixtureProgram,
        )

        return {
            "FixtureDispatchRejected": FixtureDispatchRejected,
            "FixtureInvocationResult": FixtureInvocationResult,
            "FixtureModelAdapter": FixtureModelAdapter,
            "FixtureModelError": FixtureModelError,
            "FixtureProgramError": FixtureProgramError,
            "MODEL_ARTIFACT_SHA256": MODEL_ARTIFACT_SHA256,
            "MODEL_MANIFEST_SHA256": MODEL_MANIFEST_SHA256,
            "MODEL_PROGRAM_SHA256": MODEL_PROGRAM_SHA256,
            "SealedFixtureProgram": SealedFixtureProgram,
        }[name]
    raise AttributeError(name)
