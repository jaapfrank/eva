"""Wizard composition at the existing portable operator boundary.

The target engine and fresh private authority reader are supplied at startup,
never by HTTP. A preview token refers to authenticated local staged bytes only.
"""

from __future__ import annotations

import json
from copy import deepcopy
import sqlalchemy as sa
from graphlib import TopologicalSorter
from eva.adapters.postgres.catalog import metadata
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from collections.abc import Callable
from typing import Any

from eva.operations.portable_restore_authority import authorize_portable_restore, authorize_installation_recovery
from eva.application.installer_restore import InactiveRecoveryMaterialRetained
from tools.portable_transport import open_portable_transport
from tools.read_eva_portable_archive import validate_and_derive
from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS
from tools.portable_restore import (
    restore_column_preserving_records,
    verify_empty_restore_target,
    validate_portable_restore_records,
)
from tools.portable_object_verifier import verify_portable_object_candidates
from tools.portable_object_materializer import materialize_verified_portable_objects


def _restore_specs():
    """Respect immediate foreign keys; export ordinal is not insert order."""
    specs = {str(spec["table"]): spec for spec in CANONICAL_EXPORT_SPECS}
    dependencies = {
        name: sorted(
            {
                constraint.referred_table.fullname
                for constraint in metadata.tables[name].foreign_key_constraints
                if not constraint.deferrable
                and constraint.referred_table.fullname != name
                and constraint.referred_table.fullname in specs
            }
        )
        for name in specs
    }
    return tuple(specs[name] for name in TopologicalSorter(dependencies).static_order())


class InstallerPortableRestore:
    def __init__(
        self,
        *,
        engine: Any,
        authority_reader: Callable[[], bytes],
        workspace: Path,
        object_store_root: Path,
        key_resolver: Any = None,
        runtime_identity: dict[str, str] | None = None,
        runtime_guard: Callable[[Any], None] | None = None,
        recovery_materialization: dict | None = None,
        external_restore: Callable[..., dict] | None = None,
        document_prepare: Callable[..., dict] | None = None,
        document_connect: Callable[..., dict] | None = None,
        gateway_start: Callable[..., dict] | None = None,
        document_promote: Callable[..., dict] | None = None,
        verify_components: Callable[..., dict] | None = None,
        start_runtime: Callable[..., dict] | None = None,
    ):
        # The composition root must provide an isolated, traffic-closed target.
        if (
            not workspace.is_dir()
            or workspace.is_symlink()
            or workspace.stat().st_mode & 0o077
        ):
            raise ValueError("restore workspace must be a private directory")
        self.engine = engine
        self.authority_reader = authority_reader
        self.workspace = workspace
        self.object_store_root = object_store_root
        self.key_resolver = key_resolver
        self.runtime_identity = runtime_identity
        self.runtime_guard = runtime_guard
        if recovery_materialization is not None:
            from tools.installation_recovery_materializer import _owners
            if (not isinstance(recovery_materialization, dict)
                    or set(recovery_materialization) != {"destination", "owners"}
                    or not isinstance(recovery_materialization["destination"], str)
                    or not Path(recovery_materialization["destination"]).is_absolute()):
                raise ValueError("recovery materialization requires a trusted absolute destination and owners")
            _owners(recovery_materialization["owners"])
        if external_restore is not None and (not callable(external_restore) or recovery_materialization is None):
            raise ValueError("external restoration requires configured materialization")
        if document_prepare is not None and (not callable(document_prepare)
                or external_restore is None or recovery_materialization is None):
            raise ValueError("document preparation requires external restoration and materialization")
        self.document_prepare = document_prepare
        if document_connect is not None and (not callable(document_connect) or document_prepare is None):
            raise ValueError("document connection requires document preparation")
        self.document_connect = document_connect
        if gateway_start is not None and (not callable(gateway_start) or document_connect is None):
            raise ValueError("gateway start requires document connection")
        self.gateway_start = gateway_start
        if document_promote is not None and (not callable(document_promote) or gateway_start is None):
            raise ValueError("document write promotion requires verified gateway startup")
        self.document_promote = document_promote
        if (verify_components is None) != (start_runtime is None):
            raise ValueError("restore activation requires component verification and runtime startup together")
        if verify_components is not None and (
                not callable(verify_components) or not callable(start_runtime)
                or document_promote is None or runtime_guard is None):
            raise ValueError("restore activation requires complete trusted component and runtime composition")
        self.verify_components, self.start_runtime = verify_components, start_runtime
        self.external_restore = external_restore
        self.recovery_materialization = deepcopy(recovery_materialization)
        self._lock = Lock()
        self._pending: tuple[TemporaryDirectory, str, float, str] | None = None

    def close(self) -> None:
        with self._lock:
            self._discard()

    def _discard(self) -> None:
        if self._pending:
            self._pending[0].cleanup()
            self._pending = None

    @staticmethod
    def _validate_archive(directory: Path):
        from tools.read_eva_portable_archive import _root_descriptor, _read_regular, _load_canonical_object
        with _root_descriptor(directory) as descriptor:
            header = _load_canonical_object(_read_regular(descriptor, "archive.json"), label="archive manifest")
        if header.get("contract") == "EvaInstallationRecovery/v1":
            from tools.installation_recovery_package import read_installation_package
            return read_installation_package(directory)
        if header.get("contract") == "EvaPortableArchive/v2":
            from tools.portable_v2_reader import read_current_archive
            return read_current_archive(directory)
        return validate_and_derive(directory)

    @staticmethod
    def _object_source(directory: Path, manifest: bytes) -> Path:
        header = json.loads(manifest)
        if header.get("contract") == "EvaInstallationRecovery/v1":
            return directory / "core" / "canonical" / "objects"
        return directory / "canonical" / "objects" if header.get("schema_version") == 2 else directory / "objects"

    def _read(self, directory: Path) -> tuple[dict, bytes, dict]:
        validated = self._validate_archive(directory)
        if isinstance(validated, dict):
            from tools.portable_v2_profile import ALL_SPECS
            installation = validated["manifest"]["contract"] == "EvaInstallationRecovery/v1"
            package = validated if installation else None
            if installation:
                if self.recovery_materialization is None:
                    raise ValueError("installation package needs trusted recovery materialization configuration")
                validated = package["core_archive"]
            current = validated["manifest"]
            if self.runtime_identity is None or current["runtime_identity"] != self.runtime_identity:
                raise ValueError("current archive needs matching trusted runtime configuration")
            raw = validated["manifest_raw"]
            if installation:
                authorize_installation_recovery(package_manifest_raw=package["manifest_raw"],
                    core_manifest_raw=raw, restore_authority_raw=self.authority_reader())
            else:
                authorize_portable_restore(archive_manifest_raw=raw, restore_authority_raw=self.authority_reader())
            records = validated["records"]
            summary = {"archive_sha256": validated["archive_sha256"],
                "installation": current["source_installation_id"], "records": sum(map(len, records.values())),
                "reauthorization_required": [str(row["row"].get("provider_id", row["row"].get("connector_id", "connector")))
                    for row in records["eva_core.connector_bindings"]],
                "notice": "Huidig werk wordt behouden. Externe geheugenbanken, documentbestanden en providers moeten afzonderlijk worden hersteld voordat Eva wordt gestart.",
                "target_activation": "closed", "external_requirements": current["external_requirements"],
                "object_recovery_notice": "Bij een mislukte herstelpoging kunnen versleutelde bestanden in de afgeschermde doelopslag achterblijven. Deze blijven ongeactiveerd en worden niet automatisch verwijderd."}
            if records["eva_evidence.object_refs"]:
                if self.key_resolver is None:
                    raise ValueError("separately provisioned object keyring is required")
                verify_portable_object_candidates(object_root=self._object_source(directory, package["manifest_raw"] if installation else raw),
                    records_by_table=records, key_resolver=self.key_resolver)
            if installation:
                summary.update(
                    archive_sha256=package["archive_sha256"],
                    core_archive_sha256=validated["archive_sha256"],
                    notice=("Deze herstelactie importeert de kern, het geheugen en de gateway in de ingestelde aparte doelen. Eva blijft uit totdat documentproviders zijn gekoppeld en de installatie is vrijgegeven."
                        if self.external_restore is not None else
                        "Eva blijft uit. De kern wordt hersteld en pakketbestanden worden inactief klaargezet. Geheugen- en gatewaydatabases moeten daarna worden geïmporteerd; documentproviders moeten worden gekoppeld en gecontroleerd vóór afzonderlijke operatorvrijgave."),
                    external_components="not_materialized",
                    object_recovery_notice="Een mislukte poging kan een volledige inactieve bestandskopie achterlaten. Deze wordt niet verwijderd of overschreven; de operator moet de kopie beoordelen en een nieuw leeg hersteldoel configureren voor een nieuwe poging.",
                )
                if self.document_prepare is not None:
                    summary.update(
                        document_components="preparation_planned",
                        notice="Deze herstelactie importeert de kern, het geheugen en de gateway in aparte doelen en bereidt daarna documentproviders inactief voor. Voorbereiden sluit geen documentprovider aan en start Eva niet. Aansluiten, controleren en activeren volgen afzonderlijk.",
                    )
                if self.document_connect is not None:
                    summary.update(document_components="read_only_connection_planned",
                        notice="Deze herstelactie zet de gegevens terug, bereidt documentprojecten voor en start de ingestelde documentproviders alleen-lezen voor controle. Eva en haar taakuitvoerders blijven uit; schrijven en vrijgeven volgen afzonderlijk.")
                if self.gateway_start is not None:
                    summary.update(gateway_component="start_planned", notice="Na import en documentcontrole start de ingestelde herstelde gateway voor controle van bewaarde registraties. Eva en haar taakuitvoerders blijven uit. Schrijftoegang en volledige vrijgave volgen afzonderlijk.")
                if self.document_promote is not None:
                    summary.update(document_write_access="planned", notice="Na import, documentcontrole en gatewaystart worden nieuwe documentproviders met schrijftoegang voorbereid. Oude controlecontainers blijven bewaard. Eva en haar taakuitvoerders blijven uit tot afzonderlijke vrijgave.")
                if self.verify_components is not None:
                    summary.update(target_activation="release_and_start_planned",
                        notice="De gegevens worden hersteld en de gekoppelde onderdelen opnieuw gecontroleerd. Pas na bevestigde vrijgave worden de vooraf ingestelde Eva-kern en taakuitvoerders gestart. Bij gedeeltelijke uitkomst blijven alle doelen bewaard voor controle.")
                return records, package["manifest_raw"], summary
            return records, raw, summary
        outputs, proof = validated
        manifest = (directory / "archive.json").read_bytes()
        authorize_portable_restore(
            archive_manifest_raw=manifest, restore_authority_raw=self.authority_reader()
        )
        records = {
            str(spec["table"]): [
                json.loads(line)
                for line in (directory / str(spec["record_jsonl_relative_path"]))
                .read_text()
                .splitlines()
            ]
            for spec in CANONICAL_EXPORT_SPECS
        }
        identity = json.loads(outputs["readback/identity.json"])
        summary = {
            "archive_sha256": proof["archive_json_sha256"],
            "installation": identity["installation_record"]["row"]["installation_id"],
            "records": sum(map(len, records.values())),
            "reauthorization_required": [
                str(
                    row["row"].get(
                        "provider_id", row["row"].get("connector_id", "connector")
                    )
                )
                for row in records["eva_core.connector_bindings"]
            ],
            "notice": "Aanmeldgegevens en installatiesleutels worden niet uit de reservekopie hersteld.",
        }
        objects = records.get("eva_evidence.object_refs", [])
        if objects:
            if self.key_resolver is None:
                raise ValueError("separately provisioned object keyring is required")
            verify_portable_object_candidates(
                object_root=directory / "objects",
                records_by_table=records,
                key_resolver=self.key_resolver,
            )
        return records, manifest, summary

    def prepare(self, archive: bytes, passphrase: str) -> dict[str, object]:
        with self._lock:
            self._discard()
            staging = TemporaryDirectory(prefix="restore-", dir=self.workspace)
            try:
                root = Path(staging.name)
                source = root / "backup.eva"
                source.write_bytes(archive)
                source.chmod(0o600)
                directory = root / "archive"
                open_portable_transport(
                    source,
                    directory,
                    passphrase=passphrase,
                    validator=self._validate_archive,
                )
                records, manifest, summary = self._read(directory)
                current = json.loads(manifest).get("contract") in {"EvaPortableArchive/v2", "EvaInstallationRecovery/v1"}
                if current:
                    from tools.portable_v2_profile import ALL_SPECS
                    from tools.portable_v2_restore import verify_current_target
                specs = ALL_SPECS if current else CANONICAL_EXPORT_SPECS
                checked_objects = ()
                preview_objects = root / "preview-objects"
                if records.get("eva_evidence.object_refs"):
                    checked_objects = materialize_verified_portable_objects(
                        source_object_root=self._object_source(directory, manifest),
                        target_object_root=preview_objects,
                        records_by_table=records,
                        key_resolver=self.key_resolver,
                    )
                validate_portable_restore_records(
                    records,
                    table_specs=specs,
                    materialized_objects=checked_objects,
                    object_store_root=preview_objects,
                )
                with self.engine.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                ) as connection:
                    with connection.begin():
                        if self.runtime_guard is not None:
                            self.runtime_guard(connection)
                        if current:
                            verify_current_target(connection)
                        else:
                            verify_empty_restore_target(connection, table_specs=CANONICAL_EXPORT_SPECS)
                token = secrets.token_urlsafe(32)
                digest = str(summary["archive_sha256"])
                self._pending = (staging, token, monotonic() + 600, digest)
                return {"confirmation": token, "summary": summary}
            except BaseException:
                staging.cleanup()
                raise

    def _execute_installation(self, connection, directory, manifest, summary, materialization_state):
        from tools.installation_recovery_materializer import materialize_installation_package
        from tools.installation_recovery_package import read_installation_package
        from tools.portable_v2_restore import restore_current_archive, verify_current_target
        config = self.recovery_materialization
        if config is None:
            raise ValueError("installation materialization configuration is absent")
        destination = Path(config["destination"])
        verify_current_target(connection)
        package = read_installation_package(directory)
        if package["manifest_raw"] != manifest:
            raise ValueError("installation package changed after preview")
        authorize_installation_recovery(package_manifest_raw=manifest,
            core_manifest_raw=package["core_archive"]["manifest_raw"],
            restore_authority_raw=self.authority_reader())
        try:
            materialize_installation_package(directory, destination, owners=config["owners"])
            materialization_state["published"] = True
        except Exception:
            # A directory fsync failure can occur after exclusive publication.
            # Report only an exact matching package, never an unrelated path.
            if not materialization_state["preexisted"]:
                try:
                    retained = read_installation_package(destination)
                    materialization_state["published"] = retained["manifest_raw"] == manifest
                except Exception:
                    pass
            raise
        result = restore_current_archive(connection, directory / "core",
            authority_reader=self.authority_reader,
            expected_runtime_identity=self.runtime_identity,
            object_store_root=self.object_store_root, key_resolver=self.key_resolver)
        # The inner restore remains independently authorized. Only its newly
        # created held row is bound to the outer package, in this transaction.
        changed = connection.execute(sa.text("""
            UPDATE eva_core.restore_activation SET archive_sha256=:outer
            WHERE restore_id=:restore_id AND held=true AND archive_sha256=:inner
        """), {"outer": package["archive_sha256"],
               "inner": package["core_archive"]["archive_sha256"],
               "restore_id": result["restore_id"]})
        if changed.rowcount != 1:
            raise ValueError("installation restore hold binding differs")
        if self.runtime_guard is not None:
            self.runtime_guard(connection)
        authorize_installation_recovery(package_manifest_raw=manifest,
            core_manifest_raw=package["core_archive"]["manifest_raw"],
            restore_authority_raw=self.authority_reader())
        external = None
        documents = None
        document_connection = None
        gateway_connection = None
        document_promotion = None
        if self.external_restore is not None:
            def guard():
                if self.runtime_guard is not None:
                    self.runtime_guard(connection)
                authorize_installation_recovery(package_manifest_raw=manifest,
                    core_manifest_raw=package["core_archive"]["manifest_raw"],
                    restore_authority_raw=self.authority_reader())
            materialization_state["external_import_attempted"] = True
            external = self.external_restore(destination, guard=guard)
            guard()
            if self.document_prepare is not None:
                materialization_state["document_preparation_attempted"] = True
                try:
                    documents = self.document_prepare(destination, guard=guard)
                except Exception as error:
                    # The native preparer retains partial inactive output and
                    # supplies its sanitized receipt; never retry it here.
                    materialization_state["document_preparation"] = deepcopy(
                        getattr(error, "receipt", {"status": "inspection_required_no_retry"}))
                    raise
                materialization_state["document_preparation"] = deepcopy(documents)
                if (not isinstance(documents, dict)
                        or documents.get("contract") != "EvaDocumentRelocationPreparation/v1"
                        or documents.get("status") != "prepared_not_connected"
                        or any(documents.get(key) is not False for key in
                               ("connected", "provider_verified", "services_started"))):
                    raise ValueError("document preparation receipt differs")
                guard()
                if self.document_connect is not None:
                    materialization_state["document_connection_attempted"] = True
                    try:
                        document_connection = self.document_connect(destination,
                            preparation_receipt=documents, guard=guard)
                    except Exception as error:
                        materialization_state["document_connection"] = deepcopy(
                            getattr(error, "receipt", {"status": "inspection_required_no_retry"}))
                        raise
                    materialization_state["document_connection"] = deepcopy(document_connection)
                    if (not isinstance(document_connection, dict)
                            or document_connection.get("contract") != "EvaDocumentConnection/v1"
                            or document_connection.get("status") != "connected_read_only"
                            or document_connection.get("services_started") is not True
                            or document_connection.get("documents_verified") is not True
                            or document_connection.get("core_activated") is not False):
                        raise ValueError("document connection receipt differs")
                    guard()
        if self.gateway_start is not None:
            materialization_state["gateway_start_attempted"] = True
            try:
                gateway_connection = self.gateway_start(destination,
                    connection_receipt=document_connection, guard=guard)
            except Exception as error:
                materialization_state["gateway_connection"] = deepcopy(
                    getattr(error, "receipt", {"status": "inspection_required_no_retry"}))
                raise
            materialization_state["gateway_connection"] = deepcopy(gateway_connection)
            if (not isinstance(gateway_connection, dict)
                    or gateway_connection.get("contract") != "EvaRestoredGatewayStart/v1"
                    or gateway_connection.get("status") != "gateway_started_verified"
                    or gateway_connection.get("gateway_started") is not True
                    or gateway_connection.get("authenticated_inventory_verified") is not True
                    or gateway_connection.get("core_activated") is not False):
                raise ValueError("gateway startup receipt differs")
            guard()
        if self.document_promote is not None:
            materialization_state["document_promotion_attempted"] = True
            try:
                document_promotion = self.document_promote(destination,
                    preparation_receipt=documents, connection_receipt=document_connection,
                    gateway_receipt=gateway_connection, guard=guard)
            except Exception as error:
                materialization_state["document_promotion"] = deepcopy(
                    getattr(error, "receipt", {"status": "inspection_required_no_retry"}))
                raise
            materialization_state["document_promotion"] = deepcopy(document_promotion)
            if (not isinstance(document_promotion, dict)
                    or document_promotion.get("contract") != "EvaDocumentWritePromotion/v1"
                    or document_promotion.get("status") != "writable_verified"
                    or document_promotion.get("providers_verified") is not True
                    or document_promotion.get("core_activated") is not False):
                raise ValueError("document write promotion receipt differs")
            guard()
        activation = {}
        if self.verify_components is not None:
            from datetime import UTC, datetime
            from eva.operations.restore_activation import release_restored_database
            materialization_state["activation_attempted"] = True
            release = release_restored_database(connection, restore_id=result["restore_id"],
                package_manifest_raw=manifest, core_manifest_raw=package["core_archive"]["manifest_raw"],
                authority_reader=self.authority_reader, runtime_identity=self.runtime_identity,
                runtime_guard=self.runtime_guard,
                verify_components=lambda conn: self.verify_components(conn,
                    package_root=destination, restore_id=result["restore_id"],
                    preparation_receipt=deepcopy(documents),
                    connection_receipt=deepcopy(document_connection),
                    gateway_receipt=deepcopy(gateway_connection),
                    promotion_receipt=deepcopy(document_promotion), guard=guard),
                now=datetime.now(UTC).replace(microsecond=0))
            activation = {"activation_release": release, "target_activation": "released_pending_commit"}
        return {**summary, **result, **activation, "archive_sha256": package["archive_sha256"],
            "core_archive_sha256": package["core_archive"]["archive_sha256"],
            "external_components": ("memory_imported_gateway_started" if gateway_connection is not None
                                    else "imported_inactive" if external is not None else "materialized_inactive"),
            "external_restore": external,
            **({"document_preparation": documents, "document_components": "prepared_not_connected"}
               if documents is not None else {}),
            **({"document_connection": document_connection, "document_components": "connected_read_only"}
               if document_connection is not None else {}),
            **({"gateway_connection": gateway_connection, "gateway_component": "started_verified"}
               if gateway_connection is not None else {}),
            **({"document_write_promotion": document_promotion, "document_components": "writable_verified"}
               if document_promotion is not None else {}),
            "materialization_destination": str(destination),
            "notice": ("Nieuwe documentproviders met schrijftoegang zijn voorbereid en gecontroleerd. De oude alleen-lezen-containers zijn bewaard. Er is geen documentopdracht uitgevoerd; Eva en haar taakuitvoerders blijven uit tot componentcontrole en vrijgave."
                if document_promotion is not None else
                "De herstelde gateway is gestart en de bewaarde registraties zijn teruggelezen. Documentproviders blijven alleen-lezen; Eva en haar taakuitvoerders blijven uit. Een volledige documentaanroep, schrijftoegang en vrijgave volgen afzonderlijk."
                if gateway_connection is not None else
                "Documentproviders zijn alleen-lezen aangesloten. De native controle bevestigt dat bewaarde projectbestanden en uitvoerbewijzen overeenkomen met de voorbereiding. Eva en haar taakuitvoerders blijven uit; gatewaykoppeling, schrijftoegang en vrijgave volgen afzonderlijk."
                if document_connection is not None else
                "Kern, geheugen en gateway zijn geïmporteerd. Documentproviders zijn voorbereid maar niet aangesloten of gecontroleerd. Alles blijft inactief; aansluiten, controleren en vrijgeven zijn afzonderlijke stappen."
                if documents is not None else
                "Kern, geheugen en gateway zijn geïmporteerd maar blijven inactief. Koppel en controleer de documentproviders vóór vrijgave."
                if external is not None else
                "De kern is hersteld en de bestanden staan inactief klaar. Eva blijft uit. Importeer eerst geheugen- en gatewaydatabases, koppel en controleer documentproviders en laat de operator daarna afzonderlijk vrijgeven.")}

    def execute(self, confirmation: str) -> dict[str, object]:
        with self._lock:
            pending = self._pending
            if pending is None or not secrets.compare_digest(confirmation, pending[1]):
                raise ValueError("restore confirmation is invalid")
            try:
                if monotonic() > pending[2]:
                    raise ValueError("restore preview expired")
                directory = Path(pending[0].name) / "archive"
                records, manifest, summary = self._read(directory)
                if summary["archive_sha256"] != pending[3]:
                    raise ValueError("restore preview changed")
                materialization_state = {"preexisted": False, "published": False}
                if self.recovery_materialization is not None:
                    destination = Path(self.recovery_materialization["destination"])
                    materialization_state["preexisted"] = destination.exists() or destination.is_symlink()
                with self.engine.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                ) as connection:
                    with connection.begin():
                        if self.runtime_guard is not None:
                            self.runtime_guard(connection)
                        if json.loads(manifest).get("contract") == "EvaInstallationRecovery/v1":
                            installation_result = self._execute_installation(connection, directory, manifest, summary, materialization_state)
                        elif json.loads(manifest).get("schema_version") == 2:
                            from tools.portable_v2_restore import restore_current_archive
                            result = restore_current_archive(connection, directory,
                                authority_reader=self.authority_reader,
                                expected_runtime_identity=self.runtime_identity,
                                object_store_root=self.object_store_root, key_resolver=self.key_resolver)
                            if self.runtime_guard is not None:
                                self.runtime_guard(connection)
                            return {**result, **summary}
                        else:
                            verify_empty_restore_target(
                                connection, table_specs=CANONICAL_EXPORT_SPECS
                            )
                            # Cross-table references are checked together at commit.
                            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
                            materialized = ()
                            if records.get("eva_evidence.object_refs"):
                                materialized = materialize_verified_portable_objects(
                                    source_object_root=directory / "objects",
                                    target_object_root=self.object_store_root,
                                    records_by_table=records,
                                    key_resolver=self.key_resolver,
                                )
                            inserted = restore_column_preserving_records(
                                connection,
                                records,
                                archive_manifest_raw=manifest,
                                restore_authority_raw=self.authority_reader(),
                                table_specs=_restore_specs(),
                                materialized_objects=materialized,
                                object_store_root=self.object_store_root,
                            )
                            if self.runtime_guard is not None:
                                self.runtime_guard(connection)
                if "installation_result" in locals():
                    if self.start_runtime is not None:
                        materialization_state["runtime_start_attempted"] = True
                        startup = self.start_runtime(restore_id=installation_result["restore_id"],
                            archive_sha256=installation_result["archive_sha256"],
                            release_receipt=installation_result["activation_release"],
                            runtime_identity=self.runtime_identity)
                        if (not isinstance(startup, dict)
                                or startup.get("contract") != "EvaRestoredRuntimeStart/v1"
                                or startup.get("status") != "runtime_started_verified"
                                or startup.get("runtime_started") is not True):
                            raise ValueError("restored runtime readiness not confirmed")
                        installation_result.update(runtime_start=startup, target_activation="runtime_started",
                            notice="De herstelde Eva-kern en taakuitvoerders zijn gestart en hun gereedheid is bevestigd. Controleer nu de gesprekken, geheugen en aansluitingen; dit is nog geen volledige gebruikersacceptatie.")
                    return installation_result
                return {"status": "restored", "inserted_records": inserted, **summary}
            except Exception as error:
                if "materialization_state" in locals() and materialization_state["published"]:
                    raise InactiveRecoveryMaterialRetained(str(destination),
                        activation_attempted=materialization_state.get("activation_attempted", False),
                        runtime_start_attempted=materialization_state.get("runtime_start_attempted", False),
                        external_import_attempted=materialization_state.get("external_import_attempted", False),
                        document_preparation_attempted=materialization_state.get("document_preparation_attempted", False),
                        document_preparation=deepcopy(materialization_state.get("document_preparation")),
                        document_connection_attempted=materialization_state.get("document_connection_attempted", False),
                        document_connection=deepcopy(materialization_state.get("document_connection")),
                        gateway_start_attempted=materialization_state.get("gateway_start_attempted", False),
                        gateway_connection=deepcopy(materialization_state.get("gateway_connection")),
                        document_promotion_attempted=materialization_state.get("document_promotion_attempted", False),
                        document_promotion=deepcopy(materialization_state.get("document_promotion"))) from error
                raise
            finally:
                self._discard()
