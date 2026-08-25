"""Short-lived connector process adapter for tranche 4D."""

from .process import (
    ConnectorCommandTargetPin,
    ConnectorFixtureSelection,
    ConnectorProgramBinding,
    ConnectorProcessResult,
    ConnectorProcessSpec,
    run_connector_process,
)

__all__ = [
    "ConnectorCommandTargetPin",
    "ConnectorFixtureSelection",
    "ConnectorProgramBinding",
    "ConnectorProcessResult",
    "ConnectorProcessSpec",
    "run_connector_process",
]
