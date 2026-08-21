"""Purpose-scoped model adapter boundaries."""

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

__all__ = [
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
