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


def _new_subnet(reserved=()):
    ids = _docker("network", "ls", "-q").split()
    existing = [ipaddress.ip_network(value) for value in reserved]
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



def prepare_telegram_owner_files(inputs, owner_user_id, identifiers):
    """Bind the explicit installer account to the same fresh private owner."""
    from uuid import UUID
    from eva.operations.installer_provider_configuration import telegram_owner_user_id
    owner = telegram_owner_user_id(str(owner_user_id))
    principal, binding = (str(UUID(identifiers[key])) for key in ("principal", "binding"))
    paths = {"bindings": inputs / "telegram-bindings.json", "pairing": inputs / "telegram-pairing-secret"}
    write_private_once(paths["bindings"], canonical_json_bytes({
        "contract": "EvaTelegramPrincipalBindings/v2", "bindings": [{
            "telegram_user_id": owner, "principal_id": principal,
            "principal_binding_id": binding, "allowed_chat_ids": [str(owner)]}]}))
    write_private_once(paths["pairing"], secrets.token_urlsafe(48).encode())
    return paths


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
        "from eva.providers.mcp_gateway import McpGatewayProviderFactory; "
        "from eva.providers.agentmail import AgentMailProviderFactory; "
        "from eva.providers.mcp_autonomy import McpAutonomyProviderFactory; "
        "from eva.adapters.api.browser_session import browser_session_from_environment; "
        "RuntimeSettings.from_environment(); HomeAssistantProviderFactory.load_settings(); "
        "McpGatewayProviderFactory.load_settings(); McpAutonomyProviderFactory.load_settings(); AgentMailProviderFactory.load_settings(); browser_session_from_environment(); "
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


def retained_initial_target_inputs(workspace, *, profile, identity_trust_file):
    """Explicit operator continuation for failure before any target resources.

    This is deliberately not automatic: only the exact early four-file state is
    reusable. A module plan or workflow receipt requires its own inspected repair.
    """
    workspace = Path(workspace)
    inputs = workspace / "inputs"
    if (workspace.resolve() != workspace or inputs.resolve() != inputs
            or {p.name for p in workspace.iterdir()} != {"inputs", "owner-access.json"}
            or {p.name for p in inputs.iterdir()} != {
                "dashboard-token", "identity-trust.json", "installation-profile.json"}):
        raise ValueError("only inspected initial target inputs can be retained")
    retained_profile = read_private_artifact(inputs / "installation-profile.json", label="retained profile")
    trust = read_private_artifact(inputs / "identity-trust.json", label="retained identity trust")
    if (canonical_sha256(json.loads(retained_profile)) != canonical_sha256(profile)
            or trust != Path(identity_trust_file).read_bytes()):
        raise ValueError("retained target choices differ from the current plan")
    access = json.loads(read_private_artifact(workspace / "owner-access.json", label="retained owner access"))
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", access.get("url", ""))
    if (not match or not 1024 <= int(match[1]) <= 65535
            or not re.fullmatch(r"eva-install-[0-9a-f]{12}", access.get("namespace", ""))
            or access.get("development_only") is not True
            or access.get("platform_support_claim") is not False):
        raise ValueError("retained owner access differs")
    token = read_private_artifact(inputs / "dashboard-token", label="retained owner token").decode()
    if not re.fullmatch(r"[A-Za-z0-9_-]{64}", token):
        raise ValueError("retained owner token differs")
    port = int(match[1])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
    return access["namespace"], port, token


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

    def voice_access(self):
        if self.status() != "runtime_ready": return None
        value = json.loads(read_private_artifact(self.workspace / "owner-access.json", label="fresh owner access"))
        return value.get("voice_url")

    def __call__(self, displayed_plan):
        if self.status() != "available" or self.plan_reader() != displayed_plan:
            raise ValueError("current unattempted installation plan required")
        self.verification.require_verified()
        settings = self._settings(displayed_plan)
        self.workflow = InstallerTargetWorkflow(workspace=self.workspace, settings=settings, plan_reader=self.plan_reader)
        return self.workflow(displayed_plan)

    def _settings(self, plan, *, retain_initial_inputs=False):
        if plan.get("status") != "ready_for_release_preparation":
            raise ValueError("confirmed complete wizard choices required")
        # Providers without installed consumers must remain explicit. Selected
        # HA uses the owner-entered endpoint and its existing verified secret.
        connectors_selected=any(c['state']=='ready' and c['step_id']=='adaptive_connector' for c in plan['capabilities'])
        connector_scope=None
        if connectors_selected:
            from eva.operations.installer_connector_selection import load_connector_scope
            connector_scope=load_connector_scope(plan,self.provider_verifier)
            from tools.provision_workspace_provider import GATEWAY_IMAGE
            if self.manifest['imageDigests'].get('mcp-gateway')!=GATEWAY_IMAGE.rsplit('@',1)[1]:
                raise ValueError('selected connectors require the exact packaged gateway image')
        voice_selected = any(item["step_id"] == "voice" and item["state"] == "ready"
                             for item in plan["capabilities"])
        voice_selection = None
        if voice_selected:
            from eva.operations.installer_voice_selection import load_voice_selection
            from tools.installer_voice_module import prepare_voice_module, start_voice_module
            voice_selection = load_voice_selection(plan, self.provider_verifier, self.provider_configuration_store)
            if not {"voice-media", "livekit"} <= self.manifest["imageDigests"].keys():
                raise ValueError("selected voice images are not packaged")
        email_selected = any(item["step_id"] == "email" and item["state"] == "ready"
                             for item in plan["capabilities"])
        email_inbox = (verified_provider_configuration(
            plan, "email", self.provider_configuration_store, self.provider_verifier)
            if email_selected else None)
        telegram_selected = any(item["step_id"] == "telegram" and item["state"] == "ready"
                                for item in plan["capabilities"])
        telegram_owner = None
        if telegram_selected:
            from eva.operations.installer_provider_configuration import telegram_owner_user_id
            telegram_owner = telegram_owner_user_id(verified_provider_configuration(
                plan, "telegram", self.provider_configuration_store, self.provider_verifier))
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
        browser_selected = any(item["step_id"] == "browser" and item["state"] == "selected"
                               for item in plan["capabilities"])
        if browser_selected:
            # Check the actual package imports before writing any target inputs.
            from tools.installer_browser_module import prepare_browser_module, start_browser_module
        if browser_selected or connectors_selected:
            from tools.installer_browser_gateway import prepare_browser_gateway_files, start_browser_gateway
        inputs = self.workspace / "inputs"
        identifiers = {key: str(uuid4()) for key in ("tenant", "installation", "principal", "binding")}
        # The package's verified default identity is neutral, not a family dump.
        identity_file = self.identity_file
        trust_file = inputs / "identity-trust.json"
        if retain_initial_inputs:
            if self.status() != "available" or self.plan_reader() != plan:
                raise ValueError("current unstarted target plan required")
            namespace, port, token = retained_initial_target_inputs(self.workspace,
                profile=profile, identity_trust_file=self.identity_trust_file)
        else:
            inputs.mkdir(mode=0o700, exist_ok=False)
            namespace = "eva-install-" + uuid4().hex[:12]
            token = secrets.token_urlsafe(48)
            write_private_once(inputs / "dashboard-token", token.encode())
            write_private_once(trust_file, self.identity_trust_file.read_bytes())
            write_private_once(inputs / "installation-profile.json", canonical_json_bytes(profile))
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            write_private_once(self.workspace / "owner-access.json", canonical_json_bytes({
                "url": f"http://127.0.0.1:{port}", "namespace": namespace,
                "development_only": True, "platform_support_claim": False}))
        telegram_files = {}
        if telegram_selected:
            telegram_files = prepare_telegram_owner_files(inputs, telegram_owner, identifiers)
        voice_module = None
        if voice_selected:
            ports = []
            while len(ports) < 3:
                with socket.socket() as candidate:
                    candidate.bind(("127.0.0.1", 0)); selected_port = candidate.getsockname()[1]
                    if selected_port in ports: continue
                    with socket.socket(type=socket.SOCK_DGRAM) as udp:
                        try: udp.bind(("127.0.0.1", selected_port))
                        except OSError: continue
                    ports.append(selected_port)
            voice_module = prepare_voice_module(workspace=inputs, namespace=namespace,
                images=self.manifest["imageDigests"], selection=voice_selection, identifiers=identifiers,
                identity=identity, identity_file=identity_file, core_url=f"http://127.0.0.1:{port}",
                media_port=ports[0], livekit_port=ports[1], rtc_udp_port=ports[2])
            from eva.operations.installer_identity_storage import atomic_write_private
            owner_access = json.loads(read_private_artifact(self.workspace / "owner-access.json", label="fresh owner access"))
            owner_access["voice_url"] = voice_module["voice_url"]
            atomic_write_private(self.workspace / "owner-access.json", canonical_json_bytes(owner_access))
        network_subnet = _new_subnet()
        browser_module = None
        gateway_plan = None
        selected_module_preparation = None
        if browser_selected:
            internal_subnet = _new_subnet((network_subnet,))
            egress_subnet = _new_subnet((network_subnet, internal_subnet))
            def available_port():
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    return listener.getsockname()[1]
            import sys
            browser_module = prepare_browser_module(package_root=self.root, workspace=inputs,
                namespace=namespace, owner_origin=f"http://127.0.0.1:{port}",
                internal_subnet=internal_subnet, egress_subnet=egress_subnet,
                control_port=available_port(), viewer_port=available_port(),
                python_executable=Path(sys.executable))
            gateway_plan = prepare_browser_gateway_files(package_root=self.root, workspace=inputs,
                namespace=namespace, browser_plan=browser_module, gateway_port=available_port(),
                images=self.manifest["imageDigests"],connector_scope=connector_scope,identifiers=identifiers)
            def selected_module_preparation():
                browser_runtime = start_browser_module(browser_module)
                gateway_runtime = start_browser_gateway(gateway_plan, browser_runtime=browser_runtime)
                return {"browser": browser_runtime, "gateway": gateway_runtime}
        elif connectors_selected:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));gateway_port=listener.getsockname()[1]
            gateway_plan=prepare_browser_gateway_files(package_root=self.root,workspace=inputs,
                namespace=namespace,browser_plan=None,gateway_port=gateway_port,images=self.manifest['imageDigests'],
                connector_scope=connector_scope,identifiers=identifiers,connector_subnet=_new_subnet((network_subnet,)))
            def selected_module_preparation():
                return {'gateway':start_browser_gateway(gateway_plan)}
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
        if telegram_selected:
            values["EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST"] = str(telegram_files["bindings"])
            values["EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST"] = str(telegram_files["pairing"])
        required = set(re.findall(r"\$\{(\w+):\?", template.read_text())) - {"EVA_B_CONFIGURATION_SHA256"}
        if required - values.keys():
            raise ValueError("maintained installer template has unresolved package settings")
        render, graph = render_managed_b_configuration(CoreBPlan(tuple(sorted(values.items())), canonical_sha256(values)))
        core = graph["services"]["core"]
        core["environment"].update(EVA_DASHBOARD_RELEASE_SHA256=values["EVA_DASHBOARD_RELEASE_SHA256"],
            TZ=profile["time_zone"],
            EVA_CONVERSATION_CONTACT_MODEL="gpt-6-sol", EVA_CONVERSATION_CONTACT_REASONING_EFFORT="none")
        if voice_selected:
            core["environment"]["EVA_DASHBOARD_VOICE_POLICY_FILE"] = "/run/eva/voice/policy.json"
            core.setdefault("volumes", []).append({"type":"bind", "source":voice_module["policy_directory"],
                "target":"/run/eva/voice", "read_only":True, "bind":{"create_host_path":False}})
        if email_selected:
            core["environment"].update(
                EVA_AGENTMAIL_INBOX_ID=email_inbox,
                EVA_AGENTMAIL_API_KEY_FILE="/run/secrets/agentmail_api_key")
            core.setdefault("volumes", []).append({"type": "bind",
                "source": str(inputs / "selected-email-secret"),
                "target": "/run/secrets/agentmail_api_key", "read_only": True,
                "bind": {"create_host_path": False}})
        if telegram_selected:
            # This image also runs the HTTP core; its inherited HTTP healthcheck
            # is not a health endpoint offered by the existing polling process.
            graph["services"]["telegram-ingress"]["healthcheck"] = {"disable": True}
            core["environment"]["EVA_TELEGRAM_BOT_TOKEN_FILE"] = "/run/secrets/telegram_bot_token"
            core.setdefault("volumes", []).append({"type": "bind",
                "source": values["EVA_TELEGRAM_BOT_TOKEN_FILE_HOST"],
                "target": "/run/secrets/telegram_bot_token", "read_only": True,
                "bind": {"create_host_path": False}})
        if home_assistant_selected:
            core["environment"].update(
                EVA_HOME_ASSISTANT_API_ENABLED="true",
                EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=
                    hashlib.sha256(identifiers["principal"].encode()).hexdigest())
        if connectors_selected:
            core['environment'].update(
                EVA_MCP_GATEWAY_URL=gateway_plan['gateway_url'],
                EVA_MCP_GATEWAY_TOKEN_FILE='/run/eva/connectors/autonomy/token',
                EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP='true',
                EVA_MCP_AUTONOMY_MANDATE_FILE='/run/eva/connectors/autonomy/mandate.json',
                EVA_MCP_REGISTRATION_TOKEN_FILE='/run/eva/connectors/autonomy/registration-token',
                EVA_MCP_REGISTRATION_VISIBILITY='public',EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY='true')
            core.setdefault('volumes',[]).append({'type':'bind','source':str(Path(gateway_plan['token_file']).parent),
                'target':'/run/eva/connectors/autonomy','read_only':True,'bind':{'create_host_path':False}})
            if browser_selected:
                core['environment'].update(EVA_MCP_MANAGED_ENDPOINTS_FILE='/run/eva/connectors/autonomy/managed-endpoints.json',
                    EVA_BROWSER_MCP_ENDPOINT=browser_module['mcp_url'])
        if browser_selected:
            core["environment"].update(
                EVA_MCP_GATEWAY_URL=gateway_plan["gateway_url"],
                EVA_MCP_GATEWAY_TOKEN_FILE="/run/eva/connectors/mcp/token",
                EVA_MCP_GATEWAY_CATALOG_FILE="/run/eva/connectors/mcp/catalog.json",
                EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP="true",
                EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST=hashlib.sha256(identifiers["principal"].encode()).hexdigest(),
                EVA_BROWSER_CONTROL_URL=browser_module["control_url"],
                EVA_BROWSER_CONTROL_TOKEN_FILE="/run/eva/connectors/mcp/browser-control-token",
                EVA_BROWSER_OWNER_ORIGIN=f"http://127.0.0.1:{port}")
            for source, destination in ((gateway_plan["token_file"], "token"),
                    (gateway_plan["catalog_file"], "catalog.json"),
                    (browser_module["control_token_file"], "browser-control-token")):
                core.setdefault("volumes", []).append({"type": "bind", "source": source,
                    "target": "/run/eva/connectors/mcp/" + destination, "read_only": True,
                    "bind": {"create_host_path": False}})
        if connectors_selected and browser_selected:
            core['environment']['EVA_MCP_GATEWAY_TOKEN_FILE']='/run/eva/connectors/autonomy/token'
            core['environment']['EVA_MCP_GATEWAY_CATALOG_FILE']='/run/eva/connectors/autonomy/catalog.json'
            core['volumes']=[m for m in core['volumes'] if m.get('target') not in {
                '/run/eva/connectors/mcp/token','/run/eva/connectors/mcp/catalog.json'}]
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
                elif voice_selected and mount.get("source") == voice_module["policy_directory"]:
                    extras[(name, mount["target"])] = mount["source"]
                elif telegram_selected and mount.get("source") in {str(p) for p in telegram_files.values()}:
                    extras[(name, mount["target"])] = mount["source"]
                elif name == "core" and ((browser_selected and mount["target"].startswith("/run/eva/connectors/mcp/"))
                        or (connectors_selected and mount["target"]=="/run/eva/connectors/autonomy")):
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
            model_authority_file=self.model_authority, network_subnet=network_subnet,
            fresh_start=InstallerFreshStartSettings("postgres", "core", ("model-access-lease", "hindsight-access-lease", "hindsight", "core") + (("telegram-ingress",) if telegram_selected else ()),
                authority, installation_profile=profile, support_tier="unsupported_fixture"),
            fresh_installation=True, local_port=port,
            source_authority_directories=((self.model_authority.parent, "auth.json"),),
            configuration_validator=validate_target_runtime_configuration,
            selected_module_preparation=selected_module_preparation,
            after_fresh_start=(lambda graph: start_voice_module(voice_module, graph)) if voice_selected else None)


def run_packaged_target_wizard(root, argv):
    from eva.bootstrap.installer_wizard_cli import run_installer_wizard
    manifest, verification = verify_installer_development_candidate(root, inspect_image=_inspect_image)
    def factory(**kwargs):
        return PackagedInstallerPreparation(root=root, manifest=manifest, verification=verification, **kwargs)
    return run_installer_wizard(argv, installation_preparation_factory=factory,
        preflight_transform=lambda receipt: development_preflight(receipt, verification),
        installed_runtime_access=True)
