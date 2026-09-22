"""One isolated registered-gateway relocation proof, without a private core.

Uses retained immutable images and the existing gateway acceptance helpers.
The provider is replaced behind its original DNS name; the gateway database,
registration and selected tool IDs remain in place throughout the relocation.
"""

import base64
import importlib.util
import inspect
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid

import yaml


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "eva2/src"))
from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
from eva.contracts.canonical_json import canonical_sha256


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_HELPER = REPO / "test/workspace-relocation-mcp-proof.py"
p = load_module("relocation_mcp_fixture", MCP_HELPER)
GATEWAY_HELPER = REPO / "eva2/experiments/tool01-replacement-acceptance-v2/run_physical_acceptance.py"
assert p.sha(GATEWAY_HELPER.read_bytes()) == "a8dc2a31ea327a54f70850669c0c2c3f3b40893a7bb80863df6bdeb09527d3be"
h = load_module("relocation_gateway_fixture", GATEWAY_HELPER)

SOURCE_IMAGE = "sha256:81188c414ce5b20eb59f545f9651da5501576dcfbdd9edc3abd125d455f1cb6d"
TARGET_IMAGE = "sha256:bf0cbf788561643bb25688cc0d1e6d99e13f6b7c7c8f4f4533f55b7969582179"
GATEWAY_IMAGE = "sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd"
TOOLS = ["workspace_create_bound", "workspace_observe_bound"]
KEY = "c" * 64


def catalog_snapshot(rows):
    return {
        name: {
            "tool_id": row["id"], "gateway_id": h._row_gateway_id(row), "runtime_name": row["name"],
            "binding": mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v4"),
            "binding_sha256": canonical_sha256(mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v4")),
        }
        for name, row in rows.items()
    }


def main():
    nonce = uuid.uuid4().hex[:10]
    prefix = "eva-relocation-gateway-" + nonce
    names = {name: prefix + "-" + name for name in ["provider", "gateway", "network"]}
    user = f"{os.getuid()}:{os.getgid()}"
    for image in [SOURCE_IMAGE, TARGET_IMAGE, GATEWAY_IMAGE]:
        assert p.docker("image", "inspect", image, "--format", "{{.Id}}") == image
    with tempfile.TemporaryDirectory(prefix=prefix + "-") as temporary:
        stage = Path(temporary)
        paths = {label: {name: stage / (label + "-" + name) for name in ["work", "journal", "conf"]}
                 for label in ["source", "target"]}
        for file in paths["source"].values():
            file.mkdir(mode=0o700)
        paths["target"]["conf"].mkdir(mode=0o700)
        database = stage / "gateway-database"
        database.mkdir(mode=0o700)
        password = secrets.token_urlsafe(40)
        for label in paths:
            for secret in ["jwt", "encryption", "admin"]:
                p.write_private(paths[label]["conf"] / secret,
                                (password if secret == "admin" else secrets.token_hex(32)).encode())
        environment = {"EVA_CONTEXTFORGE_JWT_SECRET_FILE": "/run/eva2/jwt",
                       "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE": "/run/eva2/encryption",
                       "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE": "/run/eva2/admin"}
        inspect_script = """
const fs=require('fs'),c=require('crypto');
const stat=p=>{const s=fs.statSync(p);return {dev:s.dev,ino:s.ino}};
const hash=p=>c.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
console.log(JSON.stringify({root:stat('/workspace'),journal:stat('/journal'),
binary_sha256:hash('/opt/eva2/atomic/workspace-writer-broker'),
client_sha256:hash('/opt/eva2/atomic/broker-client.mjs'),resolver_sha256:hash('/opt/eva2/atomic/project-journal.mjs')}));
"""
        original = json.loads(p.docker("run", "--rm", "--network", "none", "--read-only", "--user", user,
                                      *p.mounts(paths["source"], readonly=True), "--entrypoint", "node", SOURCE_IMAGE,
                                      "-e", inspect_script))
        original.update(version=2, journal_mode="per_path_prefix_v1", project_journal_identity=secrets.token_hex(32))
        original_bytes = p.canonical(original)
        p.write_private(paths["source"]["conf"] / "atomic-binding.json", original_bytes)
        base = ["run", "--detach", "--pull", "never", "--user", user, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "768m",
                "--network", names["network"], "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777"]

        def start_provider(label, image, readonly=False):
            args = base + ["--name", names["provider"], *p.mounts(paths[label], readonly)]
            for key, value in environment.items():
                args += ["--env", key + "=" + value]
            p.docker(*args, image)
            provider = p.Provider(names["provider"])
            provider.ready()
            return provider

        p.docker("network", "create", "--internal", names["network"])
        try:
            source = start_provider("source", SOURCE_IMAGE)
            compose = REPO / "eva2/experiments/contextforge-gateway-v1/compose.yml"
            start_script = REPO / "eva2/experiments/contextforge-gateway-v1/start-contextforge.sh"
            config = yaml.safe_load(compose.read_text())["services"]["contextforge"]["environment"]
            config.update(environment, DATABASE_URL="sqlite:////data/contextforge.db",
                          SSRF_ALLOW_PRIVATE_NETWORKS="true", CACHE_TYPE="none")
            args = base + ["--name", names["gateway"], "-v", f"{paths['source']['conf']}:/run/eva2:ro",
                           "-v", f"{database}:/data", "-v", f"{start_script}:/run/start.sh:ro"]
            for key, value in config.items():
                args += ["--env", key + "=" + str(value)]
            p.docker(*args, "--entrypoint", "/bin/sh", GATEWAY_IMAGE, "/run/start.sh")
            h._wait_contextforge(names["gateway"])
            token = h._login_contextforge(names["gateway"], password)
            provider_url = f"http://{names['provider']}:9000/mcp"
            gateway_id = h._register_gateway(names["gateway"], token, name="relocation_" + nonce, url=provider_url)
            selected = {name: h._make_tool_public(names["gateway"], token,
                        h._wait_gateway_tool(names["gateway"], token, gateway_id=gateway_id, original_name=name))
                        for name in TOOLS}
            saved_catalog = catalog_snapshot(selected)
            catalog_hash = canonical_sha256(saved_catalog)
            gateway_container_id = p.docker("inspect", names["gateway"], "--format", "{{.Id}}")
            first = {"operation_id": "1" * 64, "request_binding_sha256": "2" * 64,
                     "expected_generation": 0, "path": KEY + "-first.md", "content": "Synthetic gateway document first step café\n"}
            second = {**first, "operation_id": "3" * 64, "expected_generation": 1,
                      "path": KEY + "-second.md", "content": "Synthetic gateway missing second step\n"}
            sequence = 0

            def call(tool, request):
                nonlocal sequence
                sequence += 1
                response = h._gateway_request(names["gateway"], "POST", "/rpc", token=token,
                            payload={"jsonrpc": "2.0", "id": sequence, "method": "tools/call",
                                     "params": {"name": saved_catalog[tool]["runtime_name"], "arguments": request}})
                assert not response.get("error"), response
                return response["result"]

            created = call("workspace_create_bound", first)
            assert not created.get("isError"), created
            assert call("workspace_observe_bound", first) == created
            original_tools = source.rpc("tools/list", {})["tools"]
            p.docker("rm", "-f", names["provider"])
            source_before = {name: p.tree_state(paths["source"][name]) for name in ["work", "journal"]}
            for name in ["work", "journal"]:
                shutil.copytree(paths["source"][name], paths["target"][name], copy_function=shutil.copy2)
                assert paths["source"][name].stat().st_ino != paths["target"][name].stat().st_ino
            source_journal = paths["source"]["journal"] / KEY / "workspace-writer.journal"
            target_journal = paths["target"]["journal"] / KEY / "workspace-writer.journal"
            prefix_bytes = source_journal.read_bytes()
            assert target_journal.read_bytes() == prefix_bytes
            assert target_journal.stat().st_ino != source_journal.stat().st_ino
            assert (paths["target"]["work"] / first["path"]).read_bytes() == (paths["source"]["work"] / first["path"]).read_bytes()
            p.write_private(paths["target"]["conf"] / "original-binding.json", original_bytes)
            prepare_script = """
import fs from 'node:fs';
import {prepareRelocation} from '/opt/eva2/atomic/relocation.mjs';
const flags=fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW;
const rootFd=fs.openSync('/workspace',flags),journalFd=fs.openSync('/journal',flags);
try {
 const result=prepareRelocation({executable:'/opt/eva2/atomic/workspace-writer-broker',rootFd,journalFd,
 originalBindingBytes:fs.readFileSync('/run/eva2/original-binding.json'),logicalCatalogIdentity:process.argv[1],projectKeys:[process.argv[2]]});
 console.log(JSON.stringify({binding:result.bindingBytes.toString('base64'),snapshot:result.snapshots[process.argv[2]].toString('base64')}));
} finally {fs.closeSync(rootFd);fs.closeSync(journalFd);}
"""
            package = json.loads(p.docker("run", "--rm", "--network", "none", "--read-only", "--user", user,
                                         *p.mounts(paths["target"], readonly=True), "--entrypoint", "node", TARGET_IMAGE,
                                         "--input-type=module", "-e", prepare_script, catalog_hash, KEY))
            binding_bytes = base64.b64decode(package["binding"])
            p.write_private(paths["target"]["conf"] / "atomic-binding.json", binding_bytes)
            relocation_dir = paths["target"]["conf"] / "relocations"
            relocation_dir.mkdir(mode=0o700)
            snapshot_bytes = base64.b64decode(package["snapshot"])
            p.write_private(relocation_dir / (KEY + ".relocation"), snapshot_bytes)
            before_observe = {name: p.tree_state(paths["target"][name]) for name in ["work", "journal"]}
            target = start_provider("target", TARGET_IMAGE, readonly=True)
            assert target.rpc("tools/list", {})["tools"] == original_tools

            def existing_catalog():
                rows = h._list_tools(names["gateway"], token, include_metrics=False)
                found = {}
                for name in TOOLS:
                    matches = [row for row in rows if row["id"] == saved_catalog[name]["tool_id"]]
                    assert len(matches) == 1, "stored gateway tool ID no longer uniquely resolves"
                    found[name] = matches[0]
                return catalog_snapshot(found)

            assert existing_catalog() == saved_catalog
            observed = call("workspace_observe_bound", first)
            assert observed == created
            assert before_observe == {name: p.tree_state(paths["target"][name]) for name in ["work", "journal"]}
            # Remount only the relocated provider writable. The gateway process,
            # SQLite database, registration and stored tool names/IDs are kept.
            p.docker("rm", "-f", names["provider"])
            target = start_provider("target", TARGET_IMAGE)
            assert existing_catalog() == saved_catalog
            result = call("workspace_create_bound", second)
            assert not result.get("isError"), result
            after = {name: p.tree_state(paths["target"][name]) for name in ["work", "journal"]}
            assert target_journal.read_bytes().startswith(prefix_bytes)
            assert len(target_journal.read_bytes()) > len(prefix_bytes)
            assert call("workspace_create_bound", second) == result
            assert call("workspace_observe_bound", first) == created
            assert call("workspace_observe_bound", second) == result
            assert after == {name: p.tree_state(paths["target"][name]) for name in ["work", "journal"]}
            assert len(list(paths["target"]["work"].iterdir())) == 2
            assert existing_catalog() == saved_catalog
            assert p.docker("inspect", names["gateway"], "--format", "{{.Id}}") == gateway_container_id
            assert source_before == {name: p.tree_state(paths["source"][name]) for name in ["work", "journal"]}
            evidence = {
                "passed": True, "transport": "registered ContextForge gateway /rpc -> provider HTTP -> SDK -> native",
                "sourceImage": SOURCE_IMAGE, "targetImage": TARGET_IMAGE, "gatewayImage": GATEWAY_IMAGE,
                "gatewayRegistrationId": gateway_id, "providerUrlUnchanged": provider_url,
                "gatewayContainerUnchanged": True, "gatewayReregistered": False,
                "selectedCatalog": saved_catalog, "selectedCatalogSha256": catalog_hash,
                "originalBindingSha256": p.sha(original_bytes), "relocationBindingSha256": p.sha(binding_bytes),
                "relocationSnapshotSha256": p.sha(snapshot_bytes), "originalJournalPrefixSha256": p.sha(prefix_bytes),
                "firstReceiptResponseSha256": canonical_sha256(created),
                "secondReceiptResponseSha256": canonical_sha256(result),
                "gatewayHelperSha256": p.sha(GATEWAY_HELPER.read_bytes()), "mcpHelperSha256": p.sha(MCP_HELPER.read_bytes()),
                "catalogFunctionSha256": p.sha(inspect.getsource(mcp_gateway_catalog_binding).encode()),
                "testSha256": p.sha(Path(__file__).read_bytes()),
                "sourceCommit": p.command("git", "-C", str(REPO), "rev-parse", "HEAD"),
                "tests": ["registered source first step has durable receipt", "source provider fenced before exact-byte relocation",
                          "same provider address and untouched gateway registration/database", "stored tool IDs and v4 catalog bindings unchanged",
                          "gateway observes exact old receipt against readonly target mounts without writes",
                          "only missing second step executes once through stored gateway tool name",
                          "replay does not append or recreate material", "source data unchanged"],
                "limits": ["isolated synthetic gateway only", "fixture tool visibility is public on a unique internal network",
                           "no private core, person authority, model, owner mandate or restore-release proof", "no live installation"],
            }
        finally:
            for name in [names["provider"], names["gateway"]]:
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", names["network"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert not p.docker("ps", "-a", "--filter", "name=" + prefix, "--format", "{{.Names}}")
        assert not p.docker("network", "ls", "--filter", "name=" + prefix, "--format", "{{.Name}}")
        evidence["fixtureResourcesRemoved"] = True
        print(json.dumps(evidence))


if __name__ == "__main__":
    main()
