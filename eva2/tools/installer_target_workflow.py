"""Durable composition of existing installer target preparation operations."""
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from uuid import uuid4

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_identity_storage import atomic_write_private, read_private_artifact, write_private_once
from eva.operations.installer_safety import require_owner_only_directory
from eva.operations.installer_runtime_inputs import bind_installer_runtime_inputs
from tools.installer_runtime_materialization import materialize_installer_runtime_files
from tools.installer_target_bindings import bind_installer_target_graph
from tools.installation_target_provisioning import prepare_restore_target


@dataclass(frozen=True)
class InstallerTargetSettings:
    release_manifest: dict
    release_verification: object
    secret_store: object
    budget_workflow: object
    render_receipt: object
    rendered_graph: dict
    database_targets: tuple
    namespace: str
    image_roles: dict
    tenant_id: str
    installation_id: str
    additional_bindings: dict
    image_users: dict
    database_services: tuple = ("postgres", "hindsight-postgres")
    schema_service: str = "schema-installer"
    model_authority_file: Path | None = None
    network_subnet: str | None = None


class InstallerTargetWorkflow:
    """Trusted bootstrap callback and durable status reader for the existing UI.

    Settings are installer-owned, never request data. The plan reader rebuilds
    current confirmed choices. Effective image reader identities must include
    entrypoint privilege changes. This prepares a target; it does not import a
    backup, start Eva, move traffic or approve a source installation.
    """
    def __init__(self, *, workspace, settings, plan_reader):
        self.workspace = Path(workspace)
        if not self.workspace.is_absolute() or self.workspace.resolve() != self.workspace:
            raise ValueError("private absolute workflow workspace required")
        require_owner_only_directory(self.workspace, error=ValueError,
            unavailable_message="workflow workspace unavailable", invalid_message="workflow workspace is not private")
        self.settings = settings
        self.plan_reader = plan_reader
        self.receipt_path = self.workspace / "target-workflow.json"
        self.material_root = self.workspace / "material"
        self.access_root = self.workspace / "runtime-access"
        self.resources_root = self.workspace / "resources"
        self.running = False

    def status(self):
        if self.running:
            return "running"
        if not self.receipt_path.exists():
            return "needs_inspection" if any(p.exists() for p in (
                self.material_root, self.access_root, self.resources_root)) else "available"
        try:
            receipt = json.loads(read_private_artifact(self.receipt_path, label="target preparation receipt"))
            if receipt.get("contract") != "EvaInstallerTargetWorkflow/v1":
                return "needs_inspection"
            return "prepared" if receipt.get("status") == "prepared" else "needs_inspection"
        except Exception:
            return "needs_inspection"

    def __call__(self, displayed_plan):
        if self.status() != "available":
            raise ValueError("target preparation already attempted; inspect retained state")
        plan = deepcopy(self.plan_reader())
        if plan != displayed_plan:
            raise ValueError("installer choices changed before preparation")
        settings = self.settings
        def bind():
            current = self.plan_reader()
            if current != plan:
                raise ValueError("installer choices changed during preparation")
            return bind_installer_runtime_inputs(plan=current,
                expected_plan_sha256=plan["plan_sha256"],
                release_manifest=settings.release_manifest,
                release_verification=settings.release_verification,
                secret_store=settings.secret_store)
        inputs = bind()
        receipt = {"contract": "EvaInstallerTargetWorkflow/v1", "status": "running",
            "attempt_id": str(uuid4()), "plan_sha256": plan["plan_sha256"],
            "namespace": settings.namespace, "phase": "materialization", "eva_started": False}
        # Unique attempt bytes make this an exclusive claim across processes.
        write_private_once(self.receipt_path, canonical_json_bytes(receipt))
        self.running = True
        def save(phase):
            receipt["phase"] = phase
            atomic_write_private(self.receipt_path, canonical_json_bytes(receipt))
        try:
            material = materialize_installer_runtime_files(runtime_inputs=inputs,
                current_plan=plan, budget_workflow=settings.budget_workflow,
                target_root=self.material_root, tenant_id=settings.tenant_id,
                installation_id=settings.installation_id,
                database_targets=settings.database_targets,
                model_authority_file=settings.model_authority_file)
            save("service_binding")
            binding = bind_installer_target_graph(render_receipt=settings.render_receipt,
                rendered_graph=settings.rendered_graph, namespace=settings.namespace,
                materialization_receipt=material, expected_plan_sha256=plan["plan_sha256"],
                materialized_bind_root=self.material_root,
                additional_bindings=settings.additional_bindings,
                network_subnet=settings.network_subnet, current_plan=plan)
            if binding.graph is None or binding.pending:
                receipt["missing_bindings"] = list(binding.pending)
                raise ValueError("complete target service bindings required")
            save("runtime_access")
            from tools.installer_runtime_access import establish_installer_runtime_access
            access = establish_installer_runtime_access(binding=binding,
                materialized_bind_root=self.material_root, target_root=self.access_root,
                image_users=settings.image_users)
            # Rebind credentials/choices immediately before creating resources.
            if bind() != inputs:
                raise ValueError("approved runtime inputs changed before provisioning")
            save("resource_preparation")
            prepared = prepare_restore_target(runtime_inputs=inputs, launch_graph=access.graph,
                namespace=settings.namespace, image_roles={name: settings.image_roles[name]
                    for name in access.graph["services"]},
                database_services=settings.database_services, schema_service=settings.schema_service,
                workspace=self.resources_root, materialized_bind_root=self.access_root)
            if prepared.get("status") != "prepared" or prepared.get("schema_completed") is not True:
                raise ValueError("target preparation was not confirmed")
            receipt["preparation_receipt"] = str(self.resources_root / "preparation-receipt.json")
            receipt["status"] = "prepared"
            save("prepared")
        except Exception as error:
            receipt["status"] = "needs_inspection"
            receipt["failure_type"] = type(error).__name__
            atomic_write_private(self.receipt_path, canonical_json_bytes(receipt))
            raise RuntimeError("preparation incomplete; inspect retained target, do not repeat") from error
        finally:
            self.running = False
