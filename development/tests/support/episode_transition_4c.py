"""Test-only composition root for the historical sealed 4C vertical."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable

from dbos import (
    DBOS,
    DBOSPortableJSONSerializer,
    SetWorkflowID,
    WorkflowSerializationFormat,
)
from dbos._utils import GlobalParams
import dbos._dbos as dbos_runtime_module

from tests.support.dbos_datasource import DBOS_SCHEMA, EvaDBOSDatasource
from tests.support.episode_repository import PostgresEpisodeRepository
from eva.contracts.canonical_json import canonical_json_bytes, decode_json_bytes
from eva.domain.episode import EpisodeTransitionCommand


CANONICAL_TRANSACTION_NAME = "eva.episode.accepted_to_planning.transaction.v1"
CANONICAL_WORKFLOW_NAME = "eva.episode.accepted_to_planning.workflow.v1"
CANONICAL_APPLICATION_NAME = "eva2-living-core-4c"

CanonicalResult = dict[str, Any]
CommandPayload = dict[str, Any]


@dataclass(frozen=True, slots=True)
class BoundEpisodeTransition:
    workflow: Callable[[CommandPayload], CanonicalResult]


@dataclass(slots=True)
class EpisodeTransitionRuntime:
    """Owned, exact DBOS runtime for the sealed 4C workflow."""

    datasource: EvaDBOSDatasource
    bound: BoundEpisodeTransition
    _instance: DBOS
    _launched: bool = False
    _closed: bool = False

    def _require_owned(self) -> None:
        if self._closed:
            raise RuntimeError("4C DBOS runtime is closed")
        if dbos_runtime_module._dbos_global_instance is not self._instance:
            raise RuntimeError("4C no longer owns the process DBOS singleton")

    def launch(self) -> None:
        self._require_owned()
        if self._launched:
            raise RuntimeError("4C DBOS runtime is already launched")
        try:
            DBOS.launch()
            if not self._instance._launched:
                raise RuntimeError(
                    "4C DBOS launch did not initialize its owned runtime"
                )
            self._launched = True
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def start(self, workflow_id: str, payload: CommandPayload) -> CanonicalResult:
        self._require_owned()
        if not self._launched:
            raise RuntimeError("4C DBOS runtime is not launched")
        owned_payload = _owned_command_payload(payload)
        with SetWorkflowID(workflow_id):
            handle = DBOS.start_workflow(self.bound.workflow, owned_payload)
        return handle.get_result()

    def recover(self, workflow_id: str) -> CanonicalResult:
        self._require_owned()
        if not self._launched:
            raise RuntimeError("4C DBOS runtime is not launched")
        return DBOS.retrieve_workflow(workflow_id).get_result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._launched = False
        destroy_error: BaseException | None = None
        try:
            _destroy_owned_dbos(self._instance)
        except BaseException as error:
            destroy_error = error
        finally:
            self.datasource.close()
        if destroy_error is not None:
            raise destroy_error


def _reset_dbos_globals() -> None:
    """Restore DBOS's exact pre-construction singleton state after owned cleanup."""

    dbos_runtime_module._dbos_global_instance = None
    dbos_runtime_module._dbos_global_registry = None
    GlobalParams.app_version = os.environ.get("DBOS__APPVERSION", "")
    GlobalParams.executor_id = os.environ.get("DBOS__VMID", "local")
    GlobalParams.app_name = None


def _destroy_owned_dbos(expected: DBOS | None) -> None:
    current = dbos_runtime_module._dbos_global_instance
    if expected is not None and current is not expected:
        raise RuntimeError("refusing to destroy a DBOS singleton not owned by 4C")
    destroy_error: BaseException | None = None
    try:
        DBOS.destroy(destroy_registry=True, workflow_completion_timeout_sec=1)
    except BaseException as error:
        destroy_error = error
    finally:
        _reset_dbos_globals()
    if destroy_error is not None:
        raise destroy_error


def _command(payload: CommandPayload) -> EpisodeTransitionCommand:
    expected_fields = {
        "tenant_id",
        "installation_id",
        "episode_id",
        "command_id",
        "event_id",
        "expected_revision",
        "expected_state_sha256",
        "occurred_at",
    }
    if set(payload) != expected_fields:
        raise ValueError("transition command fields differ from the sealed 4C command")
    return EpisodeTransitionCommand(**payload)


def _owned_command_payload(payload: CommandPayload) -> CommandPayload:
    decoded = decode_json_bytes(canonical_json_bytes(payload))
    if not isinstance(decoded, dict):
        raise ValueError("transition command must be a JSON object")
    owned = dict(decoded)
    _command(owned)
    return owned


def bind_episode_transition(
    datasource: EvaDBOSDatasource,
) -> BoundEpisodeTransition:
    """Register one connectorless workflow and its one datasource transaction."""

    if not isinstance(datasource, EvaDBOSDatasource):
        raise TypeError("4C composition requires an attested EvaDBOSDatasource")

    @datasource.transaction(name=CANONICAL_TRANSACTION_NAME)
    def apply_transition(payload: CommandPayload) -> CanonicalResult:
        command = _command(payload)
        connection = datasource.connection()
        applied = PostgresEpisodeRepository(
            connection,
            tenant_id=command.tenant_id,
            installation_id=command.installation_id,
        ).apply_transition(command)
        result = {
            "revision": applied.revision,
            "state": applied.state.model_dump(mode="json"),
        }
        return decode_json_bytes(canonical_json_bytes(result))

    @DBOS.workflow(
        name=CANONICAL_WORKFLOW_NAME,
        max_recovery_attempts=3,
        serialization_type=WorkflowSerializationFormat.PORTABLE,
    )
    def workflow(payload: CommandPayload) -> CanonicalResult:
        return apply_transition(payload)

    return BoundEpisodeTransition(workflow=workflow)


def configure_episode_transition_runtime(
    datasource: EvaDBOSDatasource,
    *,
    system_database_url: str,
    application_version: str,
    executor_id: str,
) -> EpisodeTransitionRuntime:
    """Configure the only supported no-DDL, portable 4C DBOS runtime."""

    if not isinstance(datasource, EvaDBOSDatasource):
        raise TypeError("4C runtime requires an attested EvaDBOSDatasource")
    if not application_version.strip() or not executor_id.strip():
        datasource.close()
        raise ValueError("application_version and executor_id must be non-empty")
    if (
        dbos_runtime_module._dbos_global_instance is not None
        or dbos_runtime_module._dbos_global_registry is not None
    ):
        datasource.close()
        raise RuntimeError("4C requires a pristine process DBOS singleton and registry")

    registration_started = False
    instance: DBOS | None = None
    try:
        datasource.attest_system_database_url(system_database_url)
        registration_started = True
        bound = bind_episode_transition(datasource)
        instance = DBOS(
            config={
                "name": CANONICAL_APPLICATION_NAME,
                "system_database_url": system_database_url,
                "dbos_system_schema": DBOS_SCHEMA,
                "run_migrations": False,
                "serializer": DBOSPortableJSONSerializer(),
                "application_version": application_version,
                "executor_id": executor_id,
                "use_listen_notify": False,
                "log_level": "WARNING",
                "notification_listener_polling_interval_sec": 0.01,
            }
        )
        config = instance._config
        if (
            dbos_runtime_module._dbos_global_instance is not instance
            or config["name"] != CANONICAL_APPLICATION_NAME
            or config["system_database_url"] != system_database_url
            or config.get("database_url") is not None
            or config.get("dbos_system_schema") != DBOS_SCHEMA
            or config.get("run_migrations") is not False
            or config.get("use_listen_notify") is not False
            or instance._serializer.name() != "portable_json"
            or instance.enable_patching
            or instance.conductor_url is not None
            or instance.conductor_key is not None
            or GlobalParams.dbos_cloud
            or GlobalParams.app_version != application_version
            or GlobalParams.executor_id != executor_id
        ):
            raise RuntimeError(
                "constructed DBOS singleton differs from sealed 4C config"
            )
        return EpisodeTransitionRuntime(
            datasource=datasource,
            bound=bound,
            _instance=instance,
        )
    except BaseException:
        if registration_started:
            try:
                _destroy_owned_dbos(instance)
            except BaseException:
                pass
        datasource.close()
        raise
