"""Assemble existing restore components around one freshly prepared gateway."""
from copy import deepcopy
import json
from pathlib import Path

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_identity_storage import atomic_write_private, read_private_artifact
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs
from tools.installation_document_connection import _inspect_network
from tools.installation_external_restore import _pin
from tools.installation_recovery_materializer import _owners
from tools.installer_document_components import build_document_components
from tools.installer_gateway_target import prepare_installer_gateway_target
from tools.installer_prepared_restore_config import _actual
from tools.installation_gateway_start import _configured_network, _private_file


class PreparedComponentsError(RuntimeError):
    def __init__(self,receipt):
        self.receipt=deepcopy(receipt)
        super().__init__("component preparation incomplete; inspect retained gateway, do not repeat")


def _read(path):
    return json.loads(read_private_artifact(path,label="prepared component input"))


def prepare_installer_components(*, runtime_inputs, prepared_workspace, package_root,
                                layouts, gateway_image_role, document_image_role,
                                credentials, runtime_owners, workspace,
                                approved_external_writers=()):
    """Prepare a stopped gateway and return the existing callback configuration.

    Explicit additional writer pins come from trusted source approval, never the
    archive. No memory identity, activation approval or source fence is invented.
    The new workspace is an exclusive attempt; failures must be inspected.
    """
    if not isinstance(runtime_inputs,InstallerRuntimeInputs):
        raise ValueError("bound installer runtime inputs required")
    owners=deepcopy(runtime_owners);_owners(owners)
    if owners["config"] != owners["documents"]:
        raise ValueError("config and document material must share the document runtime owner")
    prepared_root=Path(prepared_workspace);target=Path(workspace)
    for path in (prepared_root,target):
        if not path.is_absolute() or path.resolve()!=path:
            raise ValueError("component workspace paths must be absolute without links")
    workflow=_read(prepared_root/"target-workflow.json")
    preparation_path=prepared_root/"resources/preparation-receipt.json"
    prepared=_read(preparation_path)
    if (workflow.get("contract")!="EvaInstallerTargetWorkflow/v1" or workflow.get("status")!="prepared"
            or workflow.get("preparation_receipt")!=str(preparation_path)
            or prepared.get("contract")!="EvaRestoreTargetPreparation/v1"
            or prepared.get("status")!="prepared" or prepared.get("schema_completed") is not True
            or prepared.get("namespace")!=workflow.get("namespace")
            or prepared.get("plan_sha256")!=workflow.get("plan_sha256")
            or prepared.get("plan_sha256")!=runtime_inputs.receipt.plan_sha256):
        raise ValueError("completed prepared target differs from bound plan")
    networks=prepared.get("networks",[])
    if len(networks)!=1 or set(networks[0])!={"id","name"}:
        raise ValueError("one pinned prepared target network required")
    network_id=networks[0]["id"];_inspect_network(network_id)
    selected=[p for p in prepared["containers"] if p.get("service")=="hindsight"]
    if len(selected)!=1:raise ValueError("prepared memory API pin required")
    memory_writer={k:selected[0][k] for k in ("name","id","image")};_pin(memory_writer)
    writers=[memory_writer,*deepcopy(tuple(approved_external_writers))]
    for writer in writers:_pin(writer)
    if len({p["id"] for p in writers})!=len(writers) or len({p["name"] for p in writers})!=len(writers):
        raise ValueError("external writer pins overlap")
    images=dict(runtime_inputs.receipt.images)
    if gateway_image_role not in images or document_image_role not in images:
        raise ValueError("component image roles absent from approved release")
    if set(credentials)!={"jwt","encryption","admin","access_token"}:
        raise ValueError("explicit current gateway and document credentials required")
    for key in ("jwt","encryption","admin"):
        path=Path(credentials[key])
        _private_file(path,minimum=16 if key=="admin" else 32,maximum=512)
        info=path.lstat()
        if (info.st_uid,info.st_gid)!=(owners["documents"]["uid"],owners["documents"]["gid"]):
            raise ValueError("shared gateway/document credentials differ from document runtime owner")
    for writer in writers:
        actual=_actual(writer)
        state=actual.get("State",{})
        if (state.get("Running") is not False or state.get("Pid")!=0
                or state.get("Paused") or state.get("Restarting")
                or state.get("Status") not in {"created","exited"}
                or actual.get("HostConfig",{}).get("RestartPolicy",{}).get("Name")!="no"):
            raise ValueError("external writer is not fenced for restore")
        if writer==memory_writer:_configured_network(actual,network_id)
    namespace=prepared["namespace"]
    gateway_workspace=target/"gateway"
    gateway_database=gateway_workspace/"data/contextforge.sqlite"
    document_credentials={name:str(credentials[key]) for name,key in (
        ("jwt_secret_file","jwt"),("encryption_secret_file","encryption"),("admin_password_file","admin"))}
    # Layout/coverage validation is read-only and precedes any gateway creation.
    document=build_document_components(package_root=package_root,layouts=layouts,
        runtime_image=images[document_image_role],runtime_uid=owners["documents"]["uid"],
        runtime_gid=owners["documents"]["gid"],credentials=document_credentials,network_id=network_id,
        namespace=namespace,workspace=target/"documents",gateway_database=gateway_database)
    target.mkdir(mode=0o700,exist_ok=False)
    receipt={"contract":"EvaInstallerPreparedComponents/v1","status":"preparing",
             "phase":"gateway_preparation","namespace":namespace,"network_id":network_id,
             "plan_sha256":runtime_inputs.receipt.plan_sha256,"gateway_receipt":str(gateway_workspace/"gateway-target-receipt.json"),
             "import_executed":False,"activation_configured":False}
    def save():atomic_write_private(target/"components-receipt.json",canonical_json_bytes(receipt))
    save()
    try:
        (target/"documents").mkdir(mode=0o700,exist_ok=False)
        gateway=prepare_installer_gateway_target(runtime_inputs=runtime_inputs,image_role=gateway_image_role,
            network_id=network_id,container_name=namespace+"-gateway",workspace=gateway_workspace,
            package_root=package_root,credentials=credentials)
        if gateway.get("status")!="prepared_stopped" or gateway.get("external_gateway")!={"target_path":str(gateway_database)}:
            raise ValueError("gateway preparation differs from component targets")
        new_writer=gateway["external_writer"]
        if any(new_writer["id"]==p["id"] or new_writer["name"]==p["name"] for p in writers):
            raise ValueError("new gateway overlaps approved external writers")
        components={"recovery_materialization":{"destination":str(target/"inactive"),"owners":owners},
                    "external_recovery":{"gateway":gateway["external_gateway"],"writers":[new_writer,*writers]},
                    "gateway_start":gateway["gateway_start"],**document}
        receipt.update(status="prepared",phase="component_configuration",component_configuration=components,
                       pending=["memory_target_identity_from_prepared_restore_config","activation_binding"])
        save();return deepcopy(receipt)
    except Exception as error:
        receipt.update(status="needs_inspection",failure_type=type(error).__name__)
        save();raise PreparedComponentsError(receipt) from error
