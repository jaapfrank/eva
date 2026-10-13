"""External semantic-memory adapters."""

from .hindsight import (
    HindsightMemoryEngine,
    HindsightMemoryProjection,
    HindsightMemoryScopeError,
    HindsightProjectionConfig,
    HindsightProjectionError,
    HindsightProjectionProtocolError,
    HindsightProjectionReceipt,
    HindsightProjectionUnavailable,
    ProjectedRecall,
)

__all__ = [
    "HindsightMemoryEngine",
    "HindsightMemoryProjection",
    "HindsightMemoryScopeError",
    "HindsightProjectionConfig",
    "HindsightProjectionError",
    "HindsightProjectionProtocolError",
    "HindsightProjectionReceipt",
    "HindsightProjectionUnavailable",
    "ProjectedRecall",
]
