"""Connect the existing restore UI to a completed installer target, lazily."""
from contextlib import ExitStack
from threading import Lock

from eva.application.installer_restore import RestoreTargetNotReady
from eva.bootstrap.installer_wizard_cli import run_installer_wizard
from tools.installer_restore_wizard import configured_restore_workflow


class PreparedInstallerRestore:
    """Keep one existing restore session between archive check and confirmation.

    Preparation and config_builder are trusted bootstrap objects. The builder
    reads the existing preparation result; it must not provision another target.
    A new process requires a fresh archive check, as the existing restore workflow
    owns one-use confirmation tokens. No passphrase or archive is persisted here.
    """
    def __init__(self, *, preparation, config_builder):
        self.preparation = preparation
        self.config_builder = config_builder
        self._stack = ExitStack()
        self._workflow = None
        self._closed = False
        self._lock = Lock()

    def _require_prepared(self):
        if self._closed or self.preparation.status() != "prepared":
            raise RestoreTargetNotReady("prepared installer target required")

    def prepare(self, archive, passphrase):
        with self._lock:
            self._require_prepared()
            if self._workflow is None:
                # Resolve only after preparation; never fabricate target paths.
                configuration = self.config_builder(self.preparation)
                self._workflow = self._stack.enter_context(configured_restore_workflow(configuration))
            return self._workflow.prepare(archive, passphrase)

    def execute(self, confirmation):
        with self._lock:
            self._require_prepared()
            if self._workflow is None:
                raise ValueError("check the archive in this installer session first")
            return self._workflow.execute(confirmation)

    def close(self):
        with self._lock:
            self._closed = True
            self._workflow = None
            self._stack.close()


def run_prepared_installer_wizard(*, preparation, config_builder, argv=None):
    """Use the current page for target preparation and the same restore session."""
    restore = PreparedInstallerRestore(preparation=preparation, config_builder=config_builder)
    try:
        return run_installer_wizard(argv, installation_preparer=preparation,
            installation_preparation_status=preparation.status, restore_workflow=restore)
    finally:
        restore.close()
