"""Single Eva 2 composition root with sidecar-safe lazy public exports."""

from typing import Any

__all__ = [
    "PostgresHindsightMemoryRuntime",
    "RuntimeSettings",
    "create_runtime_app",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    if name == "RuntimeSettings":
        from .runtime_settings import RuntimeSettings
        return RuntimeSettings
    from .runtime import (
        PostgresHindsightMemoryRuntime,
        RuntimeSettings,
        create_runtime_app,
    )

    return {
        "PostgresHindsightMemoryRuntime": PostgresHindsightMemoryRuntime,
        "RuntimeSettings": RuntimeSettings,
        "create_runtime_app": create_runtime_app,
    }[name]
