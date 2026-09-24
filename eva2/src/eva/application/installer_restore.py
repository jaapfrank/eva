"""Operator-provided restore boundary; the installer never chooses its target."""

from typing import Protocol


class InstallerRestorePort(Protocol):
    def prepare(self, archive: bytes, passphrase: str) -> dict[str, object]: ...
    def execute(self, confirmation: str) -> dict[str, object]: ...


class InactiveRecoveryMaterialRetained(ValueError):
    """An inactive copy may exist; the operator must reconcile before retry."""

    def __init__(self, destination: str, *, external_import_attempted: bool = False,
                 document_preparation_attempted: bool = False,
                 document_preparation: dict | None = None):
        self.destination = destination
        self.external_import_attempted = external_import_attempted
        self.document_preparation_attempted = document_preparation_attempted
        self.document_preparation = document_preparation
        super().__init__("Recovery was not confirmed; inactive material is retained for operator inspection")
