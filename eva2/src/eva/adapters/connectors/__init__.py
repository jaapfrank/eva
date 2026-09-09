"""Connector adapters; process-worker exports load only when requested."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .process import (
        ConnectorCommandTargetPin, ConnectorFixtureSelection, ConnectorProgramBinding,
        ConnectorProcessResult, ConnectorProcessSpec, run_connector_process,
    )

__all__ = [
    "ConnectorCommandTargetPin",
    "ConnectorFixtureSelection",
    "ConnectorProgramBinding",
    "ConnectorProcessResult",
    "ConnectorProcessSpec",
    "run_connector_process",
]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module(".process", __name__), name)
    globals()[name] = value
    return value
