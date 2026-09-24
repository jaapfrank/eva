"""Connect the existing restore UI to a completed installer target, lazily."""
from contextlib import ExitStack
from copy import deepcopy
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


class PreparedRestoreConfigurationBuilder:
    """Join installed component preparation and database binding for the UI.

    Both settings dictionaries are supplied by trusted installer bootstrap, not
    HTTP. Existing component receipts retain failed resources; a failed build
    cannot silently create a second target in the same session.
    """
    def __init__(self, *, component_settings, restore_settings):
        self.component_settings = deepcopy(component_settings)
        self.restore_settings = deepcopy(restore_settings)
        self._attempted = False
        self._configuration = None

    def __call__(self, preparation):
        from sqlalchemy import create_engine
        from tools.installer_prepared_components import prepare_installer_components
        from tools.installer_prepared_restore_config import build_prepared_restore_config
        if preparation.status() != "prepared":
            raise RestoreTargetNotReady("prepared installer target required")
        if self._configuration is not None:
            return self._configuration
        if self._attempted:
            raise ValueError("component preparation failed; inspect retained target before retry")
        if "component_configuration" in self.restore_settings or "connection" in self.restore_settings:
            raise ValueError("component configuration is derived from the prepared target")
        if self.component_settings.get("prepared_workspace") != preparation.workspace:
            raise ValueError("component settings select a different prepared workspace")
        if self.restore_settings.get("preparation_workspace") != preparation.workspace:
            raise ValueError("restore settings select a different prepared workspace")
        if not {"runtime_identity", "activation_settings"} <= set(self.restore_settings):
            raise ValueError("complete restore requires identity and activation settings")
        self._attempted = True
        engine = create_engine(self.restore_settings["target_dsn"])
        try:
            with engine.connect() as connection:
                # Check actual source/target lineage before creating components.
                preflight = {key: value for key, value in self.restore_settings.items()
                             if key != "activation_settings"}
                build_prepared_restore_config(connection=connection, **preflight)
                components = prepare_installer_components(**self.component_settings)
                result = build_prepared_restore_config(connection=connection,
                    component_configuration=components["component_configuration"], **self.restore_settings)
                if result.pending:
                    raise ValueError("complete prepared restore configuration is still missing components")
                self._configuration = result.config
                return self._configuration
        finally:
            engine.dispose()
