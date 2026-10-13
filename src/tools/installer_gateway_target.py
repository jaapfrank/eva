"""Prepare one fresh stopped ContextForge target using its maintained launch spec."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import yaml
from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_identity_storage import atomic_write_private
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs
from tools.installation_document_connection import _command, _inspect_network
from tools.installation_gateway_start import _private_file, _target

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "deploy/gateway/compose.yml"
_SCRIPT = _ROOT / "deploy/gateway/start-contextforge.sh"
_LABEL = "io.eva2.installer-gateway-target"
_DESTINATIONS = {"jwt":"/run/secrets/contextforge-jwt", "encryption":"/run/secrets/contextforge-encryption",
                 "admin":"/run/secrets/contextforge-admin-password", "access_token":"/run/secrets/access-token"}


class GatewayTargetPreparationError(RuntimeError):
    def __init__(self, receipt):
        self.receipt=deepcopy(receipt)
        super().__init__("gateway target preparation incomplete; retain the recorded target")


def prepare_installer_gateway_target(*, runtime_inputs, image_role, network_id,
                                     container_name, workspace, package_root, credentials):
    """Create only; existing restore owns SQLite import, verification and start.

    Credential files are explicitly supplied current operator authority, not
    extracted from an untrusted package. Repeated attempts cannot adopt targets.
    """
    if not isinstance(runtime_inputs,InstallerRuntimeInputs):
        raise ValueError("bound installer release inputs required")
    image=dict(runtime_inputs.receipt.images).get(image_role)
    if not image:
        raise ValueError("gateway image role absent from approved release")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}",container_name):
        raise ValueError("new gateway container name is invalid")
    if set(credentials)!=set(_DESTINATIONS):
        raise ValueError("exact explicit gateway credential files required")
    package=Path(package_root)
    if not package.is_absolute() or package.resolve()!=package:
        raise ValueError("package root must be absolute without links")
    checked={}
    for key,value in credentials.items():
        path=Path(value)
        if not path.is_absolute() or path.resolve()!=path or path==package or package in path.parents:
            raise ValueError("gateway credential must be separately provisioned without links")
        _private_file(path,minimum=16 if key=="admin" else 32,maximum=32768 if key=="access_token" else 512)
        checked[key]=path
    _inspect_network(network_id)
    network=json.loads(_command(["docker","network","inspect",network_id]))[0]
    image_rows=json.loads(_command(["docker","image","inspect",image]))
    if len(image_rows)!=1:raise ValueError("approved gateway image unavailable")
    image_id=image_rows[0]["Id"]
    existing=_command(["docker","ps","-a","--format","{{.Names}}"]).splitlines()
    if container_name in existing:raise ValueError("gateway target name already exists")
    project=container_name
    if _command(["docker","ps","-aq","--filter","label=com.docker.compose.project="+project]).strip():
        raise ValueError("gateway preparation project already exists")
    root=Path(workspace)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValueError("gateway workspace must be absolute without links")
    service=deepcopy(yaml.safe_load(_TEMPLATE.read_text())["services"]["contextforge"])
    if service.get("command")!=["/bin/sh","/run/eva2/start-contextforge.sh"]:
        raise ValueError("maintained gateway startup definition changed")
    root.mkdir(mode=0o700,exist_ok=False)
    data=root/"data";data.mkdir(mode=0o700)
    database=data/"contextforge.sqlite"
    nonce=uuid4().hex
    receipt={"contract":"EvaInstallerGatewayTarget/v1","status":"preparing","container_name":container_name,
             "network_id":network_id,"target_path":str(database),"plan_sha256":runtime_inputs.receipt.plan_sha256,
             "manifest_sha256":runtime_inputs.receipt.manifest_sha256,"started":False,"container":None}
    def save():atomic_write_private(root/"gateway-target-receipt.json",canonical_json_bytes(receipt))
    save()
    try:
        service.update(image=image,container_name=container_name,user=f"{os.geteuid()}:{os.getegid()}",restart="no",
                       entrypoint=["/bin/sh"],command=["/run/eva2/start-contextforge.sh"],
                       networks={"target":{"aliases":[container_name]}},labels={_LABEL:nonce},
                       tmpfs=[f"/tmp:rw,noexec,nosuid,nodev,size=256m,uid={os.geteuid()},gid={os.getegid()},mode=0700"])
        service.pop("profiles",None)
        service["environment"].update(DATABASE_URL="sqlite:////data/contextforge.sqlite",
            SSRF_ALLOW_PRIVATE_NETWORKS="true",CACHE_TYPE="none",ENABLE_HEADER_PASSTHROUGH="false")
        mounts=[{"type":"bind","source":str(_SCRIPT),"target":"/run/eva2/start-contextforge.sh","read_only":True},
                {"type":"bind","source":str(data),"target":"/data","read_only":False}]
        mounts += [{"type":"bind","source":str(checked[key]),"target":destination,"read_only":True}
                   for key,destination in _DESTINATIONS.items()]
        for mount in mounts:mount["bind"]={"create_host_path":False}
        service["volumes"]=mounts
        graph={"name":project,"services":{"contextforge":service},
               "networks":{"target":{"external":True,"name":network["Name"]}}}
        manifest=root/"compose.json";atomic_write_private(manifest,canonical_json_bytes(graph))
        _command(["docker","compose","--project-name",project,"--file",str(manifest),"create",
                  "--no-build","--pull","never","--no-recreate","contextforge"],deadline=120)
        actual=json.loads(_command(["docker","inspect",container_name]))[0]
        if actual.get("Config",{}).get("Labels",{}).get(_LABEL)!=nonce:
            raise ValueError("gateway target was not created by this preparation")
        pin={"name":container_name,"id":actual["Id"],"image":actual["Image"]}
        receipt["container"]=pin;save()
        if pin["image"]!=image_id:raise ValueError("created gateway image differs from approved image")
        start={"container":pin,"network_id":network_id,"access_token_path":_DESTINATIONS["access_token"]}
        _target(start,database,package,running=False)
        receipt.update(status="prepared_stopped",gateway_start=start,
                       external_gateway={"target_path":str(database)},external_writer=pin)
        save();return deepcopy(receipt)
    except Exception as error:
        if receipt["container"] is None:
            try:
                actual=json.loads(_command(["docker","inspect",container_name]))[0]
                if actual.get("Config",{}).get("Labels",{}).get(_LABEL)==nonce:
                    receipt["container"]={"name":container_name,"id":actual["Id"],"image":actual["Image"]}
            except Exception:pass
        receipt.update(status="incomplete",failure_type=type(error).__name__)
        save();raise GatewayTargetPreparationError(receipt) from error
