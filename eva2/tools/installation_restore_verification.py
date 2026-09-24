"""Fresh installation component checks for the held restore transaction.

Historical preparation/promotion receipts locate exact targets; current mount,
identity, native retained-material and catalog checks establish their state.
"""
from pathlib import Path
import json

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_document_connection import (
    _command, _input_mounts, _inspect_network, _verify_running,
    _CATALOG, _VERIFY_MATERIAL, _validate_material_report,
)
from tools.installation_document_promotion import (
    _verify_stopped, validate_document_promotion_configuration,
)
from tools.installation_recovery_package import read_installation_package


def verify_promoted_documents(package_root, *, preparation_configuration,
                              connection_configuration, promotion_configuration,
                              preparation_receipt, connection_receipt,
                              gateway_receipt, promotion_receipt, guard):
    """Recheck all writable providers and retained old read-only targets."""
    validate_document_promotion_configuration(promotion_configuration,
        connection_configuration=connection_configuration,
        preparation_configuration=preparation_configuration)
    if not callable(guard):
        raise ValueError("fresh document restore guard required")
    guard()
    root = Path(package_root).resolve()
    package = read_installation_package(root)
    archive = package["archive_sha256"]
    checks = (
        (preparation_receipt, "EvaDocumentRelocationPreparation/v1", "prepared_not_connected"),
        (connection_receipt, "EvaDocumentConnection/v1", "connected_read_only"),
        (gateway_receipt, "EvaRestoredGatewayStart/v1", "gateway_started_verified"),
        (promotion_receipt, "EvaDocumentWritePromotion/v1", "writable_verified"),
    )
    for value, contract, status in checks:
        if (not isinstance(value, dict) or value.get("contract") != contract
                or value.get("status") != status or value.get("archive_sha256") != archive):
            raise ValueError("document preparation/promotion binding differs")
    preparations = preparation_configuration["providers"]
    prepared = preparation_receipt.get("providers", [])
    promoted = promotion_receipt.get("providers", [])
    connected = connection_receipt.get("providers", [])
    if any(len(values) != len(preparations) for values in (prepared, promoted, connected)):
        raise ValueError("restored document provider coverage differs")
    configs = {item["preparation_index"]: item for item in connection_configuration["providers"]}
    new_configs = {item["preparation_index"]: item for item in promotion_configuration["providers"]}
    actual = {}
    for item in promoted:
        index = item.get("preparation_index")
        if type(index) is not int or index not in configs or index in actual:
            raise ValueError("promoted document target index differs")
        actual[index] = item
    verified = []
    for index, preparation in enumerate(preparations):
        item = actual[index]
        config = configs[index]
        old = [value for value in connected if value.get("container_name") == config["container_name"]]
        if (len(old) != 1 or item.get("old_container_id") != old[0].get("container_id")
                or item.get("old_container_name") != config["container_name"]
                or item.get("container_name") != new_configs[index]["container_name"]
                or item.get("image_id") != preparation["image"]
                or item.get("gateway_alias") != old[0].get("gateway_alias")
                or not isinstance(item.get("container_id"), str)
                or item["container_id"] == item["old_container_id"]):
            raise ValueError("promoted document identity differs")
        mounts = _input_mounts(root, package, preparation, prepared[index], config)
        replacement = {**config, "container_name": item["container_name"]}
        def inspect():
            guard()
            _inspect_network(config["network_id"])
            _verify_running(item["container_id"], replacement, preparation, mounts,
                            alias=item["gateway_alias"], writable=True)
            _verify_stopped(item["old_container_id"], config, preparation, disconnected=True)
        inspect()
        catalog = json.loads(_command(["docker", "exec", item["container_id"], "python3", "-c", _CATALOG], deadline=40))
        if not isinstance(catalog, list) or canonical_sha256(catalog) != preparation["logical_catalog_sha256"]:
            raise ValueError("promoted provider catalog differs")
        material = json.loads(_command(["docker", "exec", item["container_id"], "node", "--input-type=module",
            "-e", _VERIFY_MATERIAL], deadline=20+10*len(preparation["project_keys"])))
        _validate_material_report(material, preparation, prepared[index])
        if _input_mounts(root, package, preparation, prepared[index], config) != mounts:
            raise ValueError("promoted document mounts changed during verification")
        inspect()
        verified.append({"preparation_index": index, "container_id": item["container_id"],
                         "catalog_sha256": canonical_sha256(catalog), "material_sha256": canonical_sha256(material)})
    if read_installation_package(root)["archive_sha256"] != archive:
        raise ValueError("document archive changed during verification")
    guard()
    return {"contract":"EvaRestoredDocumentVerification/v1", "archive_sha256":archive,
            "providers":verified, "all_verified":True, "tool_calls_executed":False}



def validate_restore_verification_configuration(configuration):
    """Validate private composition before any restore mutation."""
    from tools.restore_runtime_guard import _configuration
    from tools.installation_external_restore import validate_external_configuration
    from tools.installation_gateway_start import validate_gateway_start_configuration
    from tools.installation_runtime_authorization import validate_runtime_authorization_configuration
    from tools.installation_memory_start import validate_memory_start_configuration
    keys = {"runtime", "external", "gateway", "binding", "document_preparation",
            "document_connection", "document_promotion", "native_authorization", "memory_api"}
    if not isinstance(configuration, dict) or set(configuration) != keys:
        raise ValueError("restore component verification configuration differs")
    _configuration(configuration["runtime"])
    validate_external_configuration(configuration["external"])
    validate_gateway_start_configuration(configuration["gateway"],
        external_configuration=configuration["external"], binding_configuration=configuration["binding"])
    validate_document_promotion_configuration(configuration["document_promotion"],
        connection_configuration=configuration["document_connection"],
        preparation_configuration=configuration["document_preparation"])
    validate_runtime_authorization_configuration(configuration["native_authorization"])
    validate_memory_start_configuration(configuration["memory_api"],
        external_configuration=configuration["external"])

def verify_restored_installation(connection, *, package_root, restore_id,
                                preparation_receipt, connection_receipt, gateway_receipt,
                                promotion_receipt, guard, configuration):
    """Trusted transaction callback for release_restored_database.

    Configuration and guard originate in operator composition. Readiness
    observations are made here, not accepted as an uploaded approval receipt.
    The existing stopped-writer guard keeps target traffic closed before release.
    """
    import sqlalchemy as sa
    from eva.operations.restore_activation import _RELEASE_REQUIREMENTS
    from tools.installation_memory_verification import verify_memory_data
    from tools.installation_memory_start import start_restored_memory_api, verify_restored_memory_api
    from tools.installation_gateway_verification import verify_gateway_providers
    from tools.installation_runtime_authorization import verify_runtime_authorization
    from tools.restore_runtime_guard import verify_restore_runtime

    validate_restore_verification_configuration(configuration)
    if not connection.in_transaction() or connection.in_nested_transaction() or not callable(guard):
        raise ValueError("component verification requires the held root transaction and fresh guard")
    transaction = connection.get_transaction()
    root = Path(package_root).resolve()
    package = read_installation_package(root)
    archive = package["archive_sha256"]
    manifest = package["manifest"]
    def current():
        if (connection.get_transaction() is not transaction or not transaction.is_active
                or connection.in_nested_transaction()):
            raise ValueError("restore component verification transaction changed")
        guard()
        verify_restore_runtime(connection, expected=configuration["runtime"])
        hold = connection.execute(sa.text("""
            SELECT *, target_database_oid = (SELECT oid FROM pg_database
                WHERE datname=current_database()) AS same_database
            FROM eva_core.restore_activation
        """)).mappings().one()
        if (str(hold["restore_id"]) != str(restore_id) or hold["held"] is not True
                or hold["same_database"] is not True or hold["archive_sha256"] != archive
                or hold["target_database_oid"] != configuration["runtime"]["target_database"]["oid"]
                or str(hold["tenant_id"]) != manifest["source_tenant_id"]
                or str(hold["installation_id"]) != manifest["source_installation_id"]
                or any(str(hold["runtime_"+key]) != value for key,value in manifest["runtime_identity"].items())):
            raise ValueError("held restore component identity differs")
        if read_installation_package(root)["archive_sha256"] != archive:
            raise ValueError("restore component archive changed")
        return hold
    hold = current()
    native = verify_runtime_authorization(connection, package_root=root, restore_id=restore_id,
        configuration=configuration["native_authorization"], runtime_configuration=configuration["runtime"], guard=current)
    if (native.get("contract") != "EvaRestoredRuntimeAuthorization/v1"
            or native.get("scope") != "locally_authorized_target_bindings"
            or native.get("verified") is not True or native.get("archive_sha256") != archive
            or native.get("restore_id") != str(restore_id)
            or native.get("target_database_oid") != hold["target_database_oid"]):
        raise ValueError("native target authorization is not verified")
    memory = verify_memory_data(root, configuration=configuration["external"], guard=current)
    if (memory.get("contract") != "EvaRestoredMemoryDataVerification/v1"
            or memory.get("archive_sha256") != archive
            or memory.get("target_binding_sha256") != canonical_sha256(configuration["external"]["memory"])):
        raise ValueError("restored memory binding is not verified")
    memory_api = start_restored_memory_api(root, configuration=configuration["memory_api"],
        external_configuration=configuration["external"],
        runtime_configuration=configuration["runtime"], guard=current)
    documents = verify_promoted_documents(root,
        preparation_configuration=configuration["document_preparation"],
        connection_configuration=configuration["document_connection"],
        promotion_configuration=configuration["document_promotion"],
        preparation_receipt=preparation_receipt, connection_receipt=connection_receipt,
        gateway_receipt=gateway_receipt, promotion_receipt=promotion_receipt, guard=current)
    if documents.get("all_verified") is not True or documents.get("archive_sha256") != archive:
        raise ValueError("restored writable documents are not verified")
    gateway = verify_gateway_providers(root, configuration=configuration["gateway"],
        external_configuration=configuration["external"], binding_configuration=configuration["binding"], guard=current)
    if gateway.get("all_verified") is not True or gateway.get("archive_sha256") != archive:
        raise ValueError("restored upstream providers are not verified")
    current()
    latest_native = verify_runtime_authorization(connection, package_root=root, restore_id=restore_id,
        configuration=configuration["native_authorization"], runtime_configuration=configuration["runtime"], guard=current)
    if latest_native != native:
        raise ValueError("native target authorization changed during component verification")
    verify_restored_memory_api(root, configuration=configuration["memory_api"],
        external_configuration=configuration["external"],
        runtime_configuration=configuration["runtime"], guard=current, receipt=memory_api)
    current()
    return {"contract":"EvaRestoreActivationComponents/v1", "restore_id":str(restore_id),
            "archive_sha256":archive, "target_database_oid":int(hold["target_database_oid"]),
            "verified_requirements":list(_RELEASE_REQUIREMENTS),
            "verified_components":{name:canonical_sha256(value) for name,value in manifest["source_components"].items()}}
