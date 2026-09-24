"""Operator-provided restore boundary; the installer never chooses its target."""

from typing import Protocol


class InstallerRestorePort(Protocol):
    def prepare(self, archive: bytes, passphrase: str) -> dict[str, object]: ...
    def execute(self, confirmation: str) -> dict[str, object]: ...


class RestoreTargetNotReady(ValueError):
    """The installer has not completed target preparation for restore yet."""


class InactiveRecoveryMaterialRetained(ValueError):
    """An inactive copy may exist; the operator must reconcile before retry."""

    def __init__(self, destination: str, *, activation_attempted: bool = False,
                 runtime_start_attempted: bool = False, external_import_attempted: bool = False,
                 document_preparation_attempted: bool = False,
                 document_preparation: dict | None = None,
                 document_connection_attempted: bool = False,
                 document_connection: dict | None = None,
                 gateway_start_attempted: bool = False,
                 gateway_connection: dict | None = None,
                 document_promotion_attempted: bool = False,
                 document_promotion: dict | None = None):
        self.destination = destination
        self.activation_attempted = activation_attempted
        self.runtime_start_attempted = runtime_start_attempted
        self.external_import_attempted = external_import_attempted
        self.document_preparation_attempted = document_preparation_attempted
        self.document_preparation = document_preparation
        self.document_connection_attempted = document_connection_attempted
        self.document_connection = document_connection
        self.gateway_start_attempted = gateway_start_attempted
        self.gateway_connection = gateway_connection
        self.document_promotion_attempted = document_promotion_attempted
        self.document_promotion = document_promotion
        super().__init__("Recovery was not confirmed; inactive material is retained for operator inspection")
