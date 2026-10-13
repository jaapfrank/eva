"""Structural ports for already-claimed external provider calls.

The application boundary describes only the durable authority material that a
result store consumes.  Concrete dispatchers may carry additional launcher
details, but persistence must not import those orchestration implementations.
"""

from __future__ import annotations

from typing import Literal, Protocol

from eva.contracts.connector import ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
)
from eva.contracts.execution import EffectAttempt, ReadAttempt


class ActionChainEffectClaim(Protocol):
    """Authority exposed after exactly one durable effect call claim."""

    disposition: Literal["claimed", "already_started"]
    attempt: EffectAttempt
    process_spec: object | None
    connector_manifest: ConnectorManifest | None
    initialize: ConnectorInitializeRequest | None
    invoke: ConnectorInvokeRequest | None


class ActionChainReadClaim(Protocol):
    """Authority exposed after exactly one durable read or readback claim."""

    disposition: Literal["claimed", "already_started"]
    attempt: ReadAttempt
    process_spec: object | None
    connector_manifest: ConnectorManifest | None
    initialize: ConnectorInitializeRequest | None
    invoke: ConnectorInvokeRequest | None


__all__ = ["ActionChainEffectClaim", "ActionChainReadClaim"]
