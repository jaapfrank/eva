"""Materialize confirmed runtime inputs in a fresh installer-owned directory."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import secrets

from sqlalchemy.engine import URL
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.installer_identity_storage import atomic_write_private, write_private_once
from eva.operations.installer_runtime_budget import materialize_runtime_resource_budget
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs
from eva.operations.model_credentials import build_access_lease, build_codex_consumer_auth


@dataclass(frozen=True, slots=True)
class NewDatabaseTarget:
    service_hostname: str
    database: str
    username: str
    password_filename: str
    dsn_filename: str
    drivername: str


class RuntimeMaterializationError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = deepcopy(receipt)
        super().__init__("runtime materialization incomplete; retain the target directory")


def materialize_installer_runtime_files(*, runtime_inputs, current_plan, budget_workflow,
                                       target_root, tenant_id, installation_id,
                                       database_targets, model_authority_file=None):
    """Write only new target files, never update source credentials or databases.

    The caller rebuilds the current trusted plan and binds its inputs immediately
    before this operation. Database descriptors name new resources to be created
    by the installer, not existing installations. Files remain owner-only; the
    trusted launch materializer must arrange explicit runtime reader ownership.
    """
    plan = deepcopy(current_plan)
    if not isinstance(runtime_inputs, InstallerRuntimeInputs):
        raise ValueError("bound runtime inputs required")
    expected = runtime_inputs.receipt.plan_sha256
    if (plan.get("contract") != "EvaInstallerInstallationPlan/v2"
            or plan.get("plan_sha256") != expected
            or canonical_sha256({k:v for k,v in plan.items() if k != "plan_sha256"}) != expected
            or plan.get("status") != "ready_for_release_preparation" or plan.get("blockers") != []):
        raise ValueError("current confirmed installation plan differs")
    bound = tuple((s["slot_id"],s["reference"],s["generation"]) for s in plan["inputs"]["secret_bindings"])
    slots = {item.slot_id:item.content for item in runtime_inputs.secrets}
    if (bound != runtime_inputs.receipt.secret_bindings
            or len(slots) != len(runtime_inputs.secrets)
            or set(slots) != {item[0] for item in bound}):
        raise ValueError("runtime secret bindings differ")
    status = budget_workflow.status()
    actual_budget = {"budget":status.budget.as_dict() if status.budget else None,
                     "budget_sha256":status.budget_sha256,"revision":status.revision}
    if status.phase != "ready" or actual_budget != plan["inputs"]["budgets"]:
        raise ValueError("confirmed runtime budget changed")
    targets = tuple(database_targets)
    filenames = {"materialization-receipt.json", "runtime-budget.json", "model-access-lease.json",
                 "hindsight-auth.json", *("provider-"+slot+".secret" for slot in slots)}
    if not targets:
        raise ValueError("explicit new database descriptors required")
    hosts = set()
    for target in targets:
        if not isinstance(target, NewDatabaseTarget):
            raise ValueError("trusted database descriptor required")
        if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", target.service_hostname)
                or target.service_hostname in hosts or not target.database or not target.username
                or target.drivername not in {"postgresql", "postgresql+psycopg"}):
            raise ValueError("new database target identity differs")
        hosts.add(target.service_hostname)
        for filename in (target.password_filename,target.dsn_filename):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", filename) or filename in filenames:
                raise ValueError("distinct private target filenames required")
            filenames.add(filename)
    model_files = None
    if model_authority_file is not None:
        lease = build_access_lease(Path(model_authority_file))
        try:
            matches = json.loads(slots.get("model_provider", b"null")) == json.loads(lease)
        except (ValueError, UnicodeError):
            matches = False
        if not matches:
            raise ValueError("explicit model authority differs from approved access lease")
        consumer = build_codex_consumer_auth(Path(model_authority_file))
        if build_access_lease(Path(model_authority_file)) != lease:
            raise ValueError("model authority changed during materialization")
        model_files = (lease, consumer)
    root = Path(target_root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("new materialization root must be absolute without links")
    root.mkdir(mode=0o700, exist_ok=False)
    receipt = {"contract":"EvaInstallerRuntimeMaterialization/v1", "status":"materializing",
               "plan_sha256":expected,"tenant_id":tenant_id,"installation_id":installation_id,
               "files":[],"databases":[],"model_authority_reused":model_files is not None,
               "pending":["runtime_reader_ownership", "complete_launch_configuration",
                          "restore_authority_and_source_fencing"]}
    if model_files is None:
        receipt["pending"].append("explicit_model_authority_for_refresh_lifecycle")
    def save():
        atomic_write_private(root/"materialization-receipt.json", canonical_json_bytes(receipt))
    def write(filename, data, role):
        path = root/filename
        write_private_once(path, data)
        receipt["files"].append({"role":role,"path":str(path),"sha256":hashlib.sha256(data).hexdigest()})
        save()
    save()
    try:
        policy = materialize_runtime_resource_budget(budget_workflow,target=root/"runtime-budget.json",
            tenant_id=tenant_id,installation_id=installation_id)
        if policy.source_budget_sha256 != actual_budget["budget_sha256"]:
            raise ValueError("budget changed during materialization")
        write("runtime-budget.json",policy.to_bytes(),"runtime_budget")
        for slot, content in slots.items():
            write("provider-"+slot+".secret",content,"provider:"+slot)
        for target in targets:
            password = secrets.token_urlsafe(48)
            dsn = URL.create(target.drivername,username=target.username,password=password,
                host=target.service_hostname,port=5432,database=target.database).render_as_string(hide_password=False)
            write(target.password_filename,password.encode(),"database_password:"+target.service_hostname)
            write(target.dsn_filename,dsn.encode(),"database_dsn:"+target.service_hostname)
            receipt["databases"].append({"service_hostname":target.service_hostname,
                "database":target.database,"username":target.username,
                "password_path":str(root/target.password_filename),"dsn_path":str(root/target.dsn_filename)})
            save()
        if model_files is not None:
            write("model-access-lease.json",model_files[0],"model_access_lease")
            write("hindsight-auth.json",model_files[1],"hindsight_consumer_auth")
        receipt["status"] = "materialized"
        save()
        return deepcopy(receipt)
    except Exception as error:
        receipt["status"] = "incomplete"
        receipt["failure_type"] = type(error).__name__
        save()
        raise RuntimeMaterializationError(receipt) from error
