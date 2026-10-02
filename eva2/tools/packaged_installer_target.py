"""Connect a local development package to the existing fresh target workflow.

Only confirmed wizard state determines owner choices. Package inputs and Docker
paths are derived by this trusted bootstrap, never submitted as browser graph
data. Production release authorization remains a separate release gate.
"""
from dataclasses import replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import subprocess
from uuid import uuid4

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.installer_development_candidate import verify_installer_development_candidate, development_preflight
from eva.operations.installer_identity_storage import write_private_once, read_private_artifact
from eva.operations.model_credentials import build_access_lease
from tools.core_b_preflight import CoreBPlan
from tools.installer_runtime_materialization import NewDatabaseTarget
from tools.installer_target_workflow import InstallerTargetWorkflow, InstallerTargetSettings, InstallerFreshStartSettings
from tools.managed_b_render import render_managed_b_configuration


def _docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.PIPE)


def _inspect_image(digest):
    rows = json.loads(_docker("image", "inspect", digest))
    if len(rows) != 1:
        raise ValueError("one inspected local image required")
    return rows[0]


def existing_model_authority(owner_uid):
    """Prefer the registered Eva lease source, retaining its original publisher.

    The managed producer metadata identifies the existing provider route. No
    refresh credential is copied, and unrelated participant containers are not
    consulted. A fresh computer falls back to its invoking owner's native login.
    """
    ids = _docker("ps", "-q", "--filter", "label=io.eva2.managed-by=eva2-explicit-b-manifest-v1",
                  "--filter", "label=com.docker.compose.service=model-access-lease").split()
    sources = set()
    if ids:
        for row in json.loads(_docker("inspect", *ids)):
            command = row.get("Config", {}).get("Cmd", [])
            if (row.get("State", {}).get("Running") is not True
                    or "eva.operations.model_credentials" not in command
                    or command.count("--source") != 1):
                continue
            original = Path(command[command.index("--source") + 1])
            for mount in row.get("Mounts", []):
                if (mount.get("Type") == "bind" and mount.get("RW") is False
                        and original.parent == Path(mount.get("Destination", ""))):
                    path = Path(mount["Source"]) / original.name
                    if path.resolve() == path and path.is_file() and path.stat().st_uid == owner_uid:
                        sources.add(path)
    if len(sources) > 1:
        raise ValueError("multiple registered owner model authorities require explicit selection")
    return next(iter(sources)) if sources else Path(pwd.getpwuid(owner_uid).pw_dir) / ".codex/auth.json"


def _new_subnet():
    ids = _docker("network", "ls", "-q").split()
    existing = []
    if ids:
        for network in json.loads(_docker("network", "inspect", *ids)):
            for spec in network.get("IPAM", {}).get("Config", []) or []:
                if spec.get("Subnet"):
                    existing.append(ipaddress.ip_network(spec["Subnet"]))
    routes = json.loads(subprocess.check_output(["ip", "-json", "route"], text=True))
    existing.extend(ipaddress.ip_network(row["dst"]) for row in routes
                    if row.get("dst") not in {None, "default"})
    for subnet in ipaddress.ip_network("10.254.0.0/16").subnets(new_prefix=24):
        if not any(subnet.version == other.version and subnet.overlaps(other) for other in existing):
            return str(subnet)
    raise ValueError("no unused private installer subnet available")


def verified_provider_configuration(plan, slot_id, configuration_store, verifier):
    """Read only the configuration already bound by the existing v4 probe.

    Capability evidence binds the endpoint and credential generation. Reuse that
    receipt instead of inventing a second plan contract or probing again.
    """
    capability = next((item for item in plan["capabilities"]
                       if item["step_id"] == slot_id), None)
    if capability is None or capability["state"] != "ready":
        raise ValueError("selected verified provider required")
    if configuration_store is None or verifier is None:
        raise ValueError("selected provider configuration unavailable")
    configuration = configuration_store.read(slot_id=slot_id)
    digest = capability["evidence_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("selected provider evidence identity differs")
    raw = read_private_artifact(verifier.evidence_workspace / (digest + ".json"),
                                label="verified provider configuration")
    receipt = json.loads(raw)
    binding = next((item for item in plan["inputs"]["secret_bindings"]
                    if item["slot_id"] == slot_id), None)
    if (hashlib.sha256(raw).hexdigest() != digest
            or receipt.get("contract") != "EvaInstallerProviderVerificationReceipt/v4"
            or receipt.get("slot_id") != slot_id or receipt.get("outcome") != "ready"
            or receipt.get("reason_code") != "verified" or binding is None
            or receipt.get("configuration_sha256") != configuration.configuration_sha256
            or receipt.get("secret_reference") != binding["reference"]
            or receipt.get("secret_generation") != binding["generation"]):
        raise ValueError("selected provider configuration differs from confirmed verification")
    return configuration.value



def validate_target_runtime_configuration(graph):
    """Check the actual selected image and bound files before creating services."""
    core = graph["services"]["core"]
    command = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
               "--name", core["container_name"] + "-configuration-check",
               "--entrypoint", "/usr/local/bin/python"]
    for key, value in core["environment"].items():
        command.extend(("--env", key + "=" + str(value)))
    for mount in core.get("volumes", []):
        if mount["type"] == "bind":
            command.extend(("--mount", "type=bind,src=" + mount["source"] +
                            ",dst=" + mount["target"] +
                            (",readonly" if mount.get("read_only") else "")))
    command.extend((core["image"], "-c",
        "from eva.bootstrap.runtime import RuntimeSettings; "
        "from eva.providers.home_assistant import HomeAssistantProviderFactory; "
        "RuntimeSettings.from_environment(); HomeAssistantProviderFactory.load_settings(); "
        "print('runtime configuration accepted')"))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or result.stdout.strip() != "runtime configuration accepted":
        raise ValueError("selected image runtime configuration was not accepted")
    return {"image": core["image"], "configuration_accepted": True,
            "network": "none", "provider_requests": 0}


def omit_unconfigured_text_target_transports(core):
    """Leave unselected background delivery disabled rather than half configured.

    The maintained render template requires placeholder values for its fenced
    worker. Those are not provider selections for the ordinary text core.
    """
    core["environment"] = {
        key: value for key, value in core["environment"].items()
        if not key.startswith(("EVA_FANOUT_", "EVA_DURABLE_GOAL_"))
    }


class PackagedInstallerPreparation:
    def __init__(self, *, root, manifest, verification, workspace, secret_store, budget_workflow, plan_reader,
                 identity_file, identity_trust_file, secret_lifecycle,
                 provider_configuration_store=None, provider_verifier=None):
        if os.geteuid() != 0:
            raise ValueError("start the installer with sudo to create runtime-owned files")
        self.root, self.manifest, self.verification = Path(root), manifest, verification
        self.workspace = Path(workspace) / "target"
        self.workspace.mkdir(mode=0o700, exist_ok=True)
        self.secret_store, self.budget_workflow, self.plan_reader = secret_store, budget_workflow, plan_reader
        self.identity_file, self.identity_trust_file = Path(identity_file), Path(identity_trust_file)
        # Ordinary sudo preserves the invoking owner's UID. The auth cache stays
        # at its existing authority; no refresh token enters the package or UI.
        uid = int(os.environ.get("SUDO_UID", os.geteuid()))
        self.model_authority = existing_model_authority(uid)
        lease = build_access_lease(self.model_authority)
        slot = next(s for s in secret_store.status() if s.slot_id == "model_provider")
        if slot.state == "missing":
            secret_store.store(slot_id="model_provider", secret=lease.decode("utf-8"))
        elif json.loads(secret_store.read_secret(slot_id="model_provider")) != json.loads(lease):
            if secret_lifecycle is None:
                raise ValueError("actual credential lifecycle required to invalidate stale model verification")
            secret_lifecycle.rotate(slot_id="model_provider", secret=lease.decode("utf-8"))
        self.provider_configuration_store = provider_configuration_store
        self.provider_verifier = provider_verifier
        self.workflow = None

    def status(self):
        if self.workflow is not None:
            return self.workflow.status()
        # The existing durable workflow receipt remains the only task state.
        return InstallerTargetWorkflow(workspace=self.workspace, settings=None,
                                       plan_reader=self.plan_reader).status()

    def runtime_access(self):
        if self.status() != "runtime_ready":
            return None
        selected = json.loads(read_private_artifact(self.workspace / "owner-access.json", label="fresh owner access"))
        token = read_private_artifact(self.workspace / "inputs/dashboard-token", label="fresh owner token").decode()
        return selected["url"], token

    def __call__(self, displayed_plan):
        if self.status() != "available" or self.plan_reader() != displayed_plan:
            raise ValueError("current unattempted installation plan required")
        self.verification.require_verified()
        settings = self._settings(displayed_plan)
        self.workflow = InstallerTargetWorkflow(workspace=self.workspace, settings=settings, plan_reader=self.plan_reader)
        return self.workflow(displayed_plan)

    def _settings(self, plan):
        if plan.get("status") != "ready_for_release_preparation":
            raise ValueError("confirmed complete wizard choices required")
        # Providers without installed consumers must remain explicit. Selected
        # HA uses the owner-entered endpoint and its existing verified secret.
        if any(c["state"] == "ready" and c["step_id"] in {"telegram", "email", "adaptive_connector"}
               for c in plan["capabilities"]):
            raise ValueError("selected provider has no ordinary target consumer yet")
        home_assistant_selected = any(item["step_id"] == "home_assistant"
                                      and item["state"] == "ready"
                                      for item in plan["capabilities"])
        home_assistant_url = (verified_provider_configuration(
            plan, "home_assistant", self.provider_configuration_store, self.provider_verifier)
            if home_assistant_selected else "http://127.0.0.1:9")
        from eva.adapters.objects.identity_verifier import load_current_identity_pack
        from eva.application.identity_verifier import load_identity_pack_trust_binding
        identity = load_current_identity_pack(self.identity_file,
                    trust=load_identity_pack_trust_binding(self.identity_trust_file))
        profile = plan["inputs"]["profile"]["profile"]
        if canonical_sha256(identity) != plan["inputs"]["identity_pack_sha256"]:
            raise ValueError("mounted identity differs from confirmed wizard identity")
        if profile["language"].split("-")[0] != identity.language.split("-")[0]:
            raise ValueError("installation language requires a matching verified identity pack")
        inputs = self.workspace / "inputs"
        inputs.mkdir(mode=0o700, exist_ok=False)
        namespace = "eva-install-" + uuid4().hex[:12]
        identifiers = {key: str(uuid4()) for key in ("tenant", "installation", "principal", "binding")}
        token = secrets.token_urlsafe(48)
        write_private_once(inputs / "dashboard-token", token.encode())
        # The package's verified default identity is neutral, not a family dump.
        identity_file = self.identity_file
        trust_file = inputs / "identity-trust.json"
        write_private_once(trust_file, self.identity_trust_file.read_bytes())
        write_private_once(inputs / "installation-profile.json", canonical_json_bytes(profile))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        write_private_once(self.workspace / "owner-access.json", canonical_json_bytes({
            "url": f"http://127.0.0.1:{port}", "namespace": namespace,
            "development_only": True, "platform_support_claim": False}))
        docker_config = inputs / "docker-config"
        docker_config.mkdir(mode=0o700)
        images = self.manifest["imageDigests"]
        template = self.root / "deploy/living-core/managed-b.compose.yml"
        values = {
            "EVA_B_DOCKER_BINARY": shutil.which("docker"), "EVA_B_DOCKER_CONFIG_DIR": str(docker_config),
            "EVA_B_DOCKER_CONTEXT": "default", "EVA_B_COMPOSE_PROJECT": namespace,
            "EVA_B_MANAGED_MANIFEST_SHA256": hashlib.sha256(template.read_bytes()).hexdigest(),
            "EVA_LIVING_CORE_IMAGE": images["living-core"], "EVA_POSTGRES_IMAGE": images["postgres"],
            "EVA_HINDSIGHT_POSTGRES_IMAGE": images["postgres"], "EVA_HINDSIGHT_IMAGE": images["hindsight"],
            "EVA_CODEX_AUTH_DIR": str(self.model_authority.parent),
            "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST": str(inputs / "dashboard-token"),
            "EVA_IDENTITY_PACK_FILE_HOST": str(identity_file), "EVA_IDENTITY_PACK_TRUST_FILE_HOST": str(trust_file),
            "EVA_MODEL_ACCESS_LEASE_DIR": str(inputs / "model-lease"),
            "EVA_HINDSIGHT_ACCESS_DIR": str(inputs / "hindsight-access"),
            "EVA_DATABASE_DSN_FILE_HOST": str(inputs / "core-dsn"),
            "EVA_HINDSIGHT_DATABASE_DSN_FILE_HOST": str(inputs / "memory-dsn"),
            "EVA_POSTGRES_PASSWORD_FILE_HOST": str(inputs / "core-password"),
            "EVA_HINDSIGHT_POSTGRES_PASSWORD_FILE_HOST": str(inputs / "memory-password"),
            "EVA_RUNTIME_RESOURCE_BUDGET_FILE_HOST": str(inputs / "runtime-budget"),
            "EVA_PORTABLE_RESTORE_AUTHORITY_DIR_HOST": str(inputs / "restore-authority"),
            "EVA_STACK_CORE_CONTAINER": namespace + "-core",
            "EVA_STACK_POSTGRES_VOLUME": namespace + "-postgres-data",
            "EVA_STACK_HINDSIGHT_POSTGRES_VOLUME": namespace + "-hindsight-postgres-data",
            "EVA_STACK_WORK_VOLUME": namespace + "-work-data",
            "EVA_STACK_CORE_PORT": str(port), "EVA_STACK_BIND_ADDRESS": "127.0.0.1",
            "EVA_DASHBOARD_RELEASE_SHA256": images["living-core"].removeprefix("sha256:"),
            "EVA_HINDSIGHT_BANK_NAMESPACE": namespace,
            "EVA_FANOUT_ALLOW_INSECURE_HTTP": "false", "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON": "{}",
            # Required template fields for omitted/fenced consumers are removed
            # by existing capability projection before any container uses them.
            "EVA_HOME_ASSISTANT_URL": home_assistant_url,
            "EVA_DURABLE_GOAL_PROVIDER": "unconfigured", "EVA_DURABLE_GOAL_PROVIDER_URL": "http://127.0.0.1:9",
            "EVA_CONVERSATION_MODEL": "gpt-6-astra", "EVA_CONVERSATION_REASONING_EFFORT": "medium",
            "TZ": profile["time_zone"],
        }
        for prefix in ("EVA_DASHBOARD", "EVA_RUNTIME"):
            values[prefix + "_TENANT_ID"] = identifiers["tenant"]
            values[prefix + "_INSTALLATION_ID"] = identifiers["installation"]
        values["EVA_DASHBOARD_PRINCIPAL_BINDING_ID"] = identifiers["binding"]
        values["EVA_RUNTIME_PRINCIPAL_ID"] = identifiers["principal"]
        for kind in ("HOME_ASSISTANT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PRINCIPAL_BINDINGS", "TELEGRAM_PAIRING_SECRET"):
            values["EVA_" + kind + "_FILE_HOST"] = str(inputs / ("omitted-" + kind.lower()))
        required = set(re.findall(r"\$\{(\w+):\?", template.read_text())) - {"EVA_B_CONFIGURATION_SHA256"}
        if required - values.keys():
            raise ValueError("maintained installer template has unresolved package settings")
        render, graph = render_managed_b_configuration(CoreBPlan(tuple(sorted(values.items())), canonical_sha256(values)))
        core = graph["services"]["core"]
        core["environment"].update(EVA_DASHBOARD_RELEASE_SHA256=values["EVA_DASHBOARD_RELEASE_SHA256"],
            TZ=profile["time_zone"],
            EVA_CONVERSATION_CONTACT_MODEL="gpt-6-sol", EVA_CONVERSATION_CONTACT_REASONING_EFFORT="none")
        if home_assistant_selected:
            core["environment"].update(
                EVA_HOME_ASSISTANT_API_ENABLED="true",
                EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=
                    hashlib.sha256(identifiers["principal"].encode()).hexdigest())
        omit_unconfigured_text_target_transports(core)
        graph["services"]["hindsight"]["environment"].update(
            HINDSIGHT_API_LLM_MODEL="gpt-6-astra", HINDSIGHT_API_LLM_REASONING_EFFORT="medium")
        extras = {}
        for name, service in graph["services"].items():
            for mount in service.get("volumes", []):
                if mount.get("source") == str(self.model_authority.parent):
                    # Retain the original directory bind: atomic native auth
                    # replacement remains visible to the fixed lease producer.
                    extras[(name, mount["target"])] = str(self.model_authority.parent)
                elif mount.get("source") in {str(inputs / "dashboard-token"), str(identity_file), str(trust_file)}:
                    extras[(name, mount["target"])] = mount["source"]
        render = replace(render, rendered_sha256=hashlib.sha256(canonical_json_bytes(graph)).hexdigest())
        authority = {"tenant_id": identifiers["tenant"], "installation_id": identifiers["installation"],
                     "principal_id": identifiers["principal"], "binding_id": identifiers["binding"],
                     "release_sha256": values["EVA_DASHBOARD_RELEASE_SHA256"], "access_token": token}
        roles = {name: ("postgres" if name in {"postgres", "hindsight-postgres"} else
                       "hindsight" if name == "hindsight" else "living-core") for name in graph["services"]}
        return InstallerTargetSettings(self.manifest, self.verification, self.secret_store, self.budget_workflow,
            render, graph, (
                NewDatabaseTarget("postgres", "eva2", "postgres", "core-password", "core-dsn", "postgresql+psycopg"),
                NewDatabaseTarget("hindsight-postgres", "hindsight", "postgres", "memory-password", "memory-dsn", "postgresql")),
            namespace, roles, identifiers["tenant"], identifiers["installation"], extras,
            {images["living-core"]: "65532:65532", images["postgres"]: "70:70", images["hindsight"]: "1000:1000"},
            model_authority_file=self.model_authority, network_subnet=_new_subnet(),
            fresh_start=InstallerFreshStartSettings("postgres", "core", ("model-access-lease", "hindsight-access-lease", "hindsight", "core"),
                authority, installation_profile=profile, support_tier="unsupported_fixture"),
            fresh_installation=True, local_port=port,
            source_authority_directories=((self.model_authority.parent, "auth.json"),),
            configuration_validator=validate_target_runtime_configuration)


def run_packaged_target_wizard(root, argv):
    from eva.bootstrap.installer_wizard_cli import run_installer_wizard
    manifest, verification = verify_installer_development_candidate(root, inspect_image=_inspect_image)
    def factory(**kwargs):
        return PackagedInstallerPreparation(root=root, manifest=manifest, verification=verification, **kwargs)
    return run_installer_wizard(argv, installation_preparation_factory=factory,
        preflight_transform=lambda receipt: development_preflight(receipt, verification),
        installed_runtime_access=True)
