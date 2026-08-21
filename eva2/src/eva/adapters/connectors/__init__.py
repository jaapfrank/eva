"""Short-lived connector process adapter for tranche 4D."""

from .process import (
    ConnectorFixtureSelection,
    ConnectorProcessResult,
    ConnectorProcessSpec,
    run_connector_process,
)

__all__ = [
    "ConnectorFixtureSelection",
    "ConnectorProcessResult",
    "ConnectorProcessSpec",
    "run_connector_process",
]
