"""Disposable relocation proof through ContextForge HTTP -> SDK -> native.

Builds only a uniquely tagged local candidate. No models, gateway registration,
live data, or installed service changes. The source provider is stopped before
copying. All fixtures are synthetic and removed after the decisive proof.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
PROVIDER = REPO / "eva2/deploy/living-core/workspace-create-only"
SOURCE_IMAGE = os.environ.get(
    "EVA_RELOCATION_SOURCE_IMAGE",
    "sha256:81188c414ce5b20eb59f545f9651da5501576dcfbdd9edc3abd125d455f1cb6d",
)
BASE_IMAGE = "eva2.local/workspace-filesystem@sha256:bd31ea2dde76561fe662a551756f939e9007048956c300e54ad49a8c18ddfdc4"
KEYS = ["a" * 64, "b" * 64]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def docker(*args):
    return command("docker", *args)


def write_private(file, data):
    file.write_bytes(data)
    file.chmod(0o400)


def mounts(paths, readonly=False):
    suffix = ":ro" if readonly else ""
    return ["-v", f"{paths['work']}:/workspace{suffix}", "-v",
            f"{paths['journal']}:/journal{suffix}", "-v", f"{paths['conf']}:/run/eva2:ro"]


def tree_state(directory):
    """Exclude access time; reading evidence must not count as mutation."""
    result = {}
    for file in sorted(directory.rglob("*")):
        stat = file.lstat()
        result[str(file.relative_to(directory))] = {
            "dev": stat.st_dev, "ino": stat.st_ino, "mode": stat.st_mode,
            "uid": stat.st_uid, "gid": stat.st_gid, "links": stat.st_nlink,
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "sha256": sha(file.read_bytes()) if file.is_file() else None,
        }
    return result


class Provider:
    def __init__(self, name):
        self.name = name
        self.sequence = 0

    def rpc(self, method, params):
        self.sequence += 1
        data = json.dumps({"jsonrpc": "2.0", "id": self.sequence,
                           "method": method, "params": params})
        client = (
            "import urllib.request,sys;"
            "r=urllib.request.Request('http://127.0.0.1:9000/mcp',sys.argv[1].encode(),"
            "{'Content-Type':'application/json','Accept':'application/json, text/event-stream',"
            "'MCP-Protocol-Version':'2025-03-26'});"
            "print(urllib.request.urlopen(r,timeout=30).read().decode())"
        )
        response = json.loads(docker("exec", self.name, "python3", "-c", client, data))
        assert "error" not in response, response
        return response["result"]

    def ready(self):
        for _ in range(80):
            try:
                self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "isolated-relocation-proof", "version": "1"}})
                return
            except (subprocess.CalledProcessError, AssertionError, json.JSONDecodeError):
                time.sleep(0.25)
        raise RuntimeError("isolated provider startup failed: " + docker("logs", self.name))

    def call(self, tool, request):
        return self.rpc("tools/call", {"name": tool, "arguments": request})


def main():
    nonce = uuid.uuid4().hex[:10]
    prefix = "eva-relocation-proof-" + nonce
    image_tag = "eva2-relocation-proof:" + nonce
    network = prefix + "-net"
    user = f"{os.getuid()}:{os.getgid()}"
    containers = []
    source_image_id = docker("image", "inspect", SOURCE_IMAGE, "--format", "{{.Id}}")
    base_image_id = docker("image", "inspect", BASE_IMAGE, "--format", "{{.Id}}")
    source_files = ["Dockerfile", "broker-client.mjs", "project-journal.mjs", "relocation.mjs",
                    "mcp-server.mjs", "start-atomic-workspace.sh"]
    source_hashes = {name: sha((PROVIDER / name).read_bytes()) for name in source_files}
    source_hashes["native/workspace_writer_broker.c"] = sha((REPO / "native/workspace_writer_broker.c").read_bytes())
    with tempfile.TemporaryDirectory(prefix=prefix + "-") as temporary:
        stage = Path(temporary)
        context = stage / "build"
        context.mkdir(mode=0o700)
        for name in source_files:
            shutil.copy2(PROVIDER / name, context / name)
        # BuildKit resolves repository digests remotely even when inspect can
        # find their local image. Pin a disposable local tag to the exact image
        # ID and change only this temporary build context, never product source.
        local_base = "eva2-relocation-proof-base:" + nonce
        docker("tag", base_image_id, local_base)
        dockerfile = (context / "Dockerfile").read_text()
        assert "FROM " + BASE_IMAGE in dockerfile
        (context / "Dockerfile").write_text(dockerfile.replace("FROM " + BASE_IMAGE, "FROM " + local_base, 1))
        compiler = ["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-static",
                    "-o", str(context / "workspace-writer-broker"), str(REPO / "native/workspace_writer_broker.c")]
        subprocess.run(compiler, check=True, capture_output=True)
        binary_hash = sha((context / "workspace-writer-broker").read_bytes())
        (context / "workspace-writer-broker.sha256").write_text(binary_hash + "\n")
        # The base digest was checked locally; pull=false and network=none keep
        # the build independent of package/download services.
        build = subprocess.run(["docker", "build", "--pull=false", "--network=none", "-t", image_tag, str(context)],
                               capture_output=True)
        docker("image", "rm", local_base)
        if build.returncode:
            raise RuntimeError("isolated image build failed: " + (build.stdout + build.stderr).decode())
        build_hash = sha(build.stdout + build.stderr)
        image_id = docker("image", "inspect", image_tag, "--format", "{{.Id}}")
        paths = {}
        for label in ["source", "target"]:
            paths[label] = {name: stage / (label + "-" + name) for name in ["work", "journal", "conf"]}
        for file in paths["source"].values():
            file.mkdir(mode=0o700)
        paths["target"]["conf"].mkdir(mode=0o700)
        for label in paths:
            for secret in ["jwt", "encryption", "admin"]:
                write_private(paths[label]["conf"] / secret, secrets.token_hex(32).encode())
        inspect_script = """
const fs=require('fs'),c=require('crypto');
const stat=p=>{const s=fs.statSync(p);return {dev:s.dev,ino:s.ino}};
const hash=p=>c.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
console.log(JSON.stringify({root:stat('/workspace'),journal:stat('/journal'),
binary_sha256:hash('/opt/eva2/atomic/workspace-writer-broker'),
client_sha256:hash('/opt/eva2/atomic/broker-client.mjs'),
resolver_sha256:hash('/opt/eva2/atomic/project-journal.mjs')}));
"""
        original = json.loads(docker("run", "--rm", "--network", "none", "--read-only", "--user", user,
                                     *mounts(paths["source"], readonly=True), "--entrypoint", "node",
                                     source_image_id, "-e", inspect_script))
        original.update(version=2, journal_mode="per_path_prefix_v1", project_journal_identity=secrets.token_hex(32))
        original_bytes = canonical(original)
        write_private(paths["source"]["conf"] / "atomic-binding.json", original_bytes)

        def start(label, selected_image, readonly=False):
            name = prefix + "-" + label
            containers.append(name)
            docker("run", "-d", "--name", name, "--pull", "never", "--network", network, "--read-only",
                   "--user", user, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777", *mounts(paths["source" if label == "source" else "target"], readonly),
                   "-e", "EVA_CONTEXTFORGE_JWT_SECRET_FILE=/run/eva2/jwt",
                   "-e", "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE=/run/eva2/encryption",
                   "-e", "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE=/run/eva2/admin", selected_image)
            provider = Provider(name)
            provider.ready()
            return provider

        def stop(provider):
            docker("rm", "-f", provider.name)
            containers.remove(provider.name)

        def request(key, index):
            return {"operation_id": sha(f"{key}:operation:{index}".encode()),
                    "request_binding_sha256": sha(f"{key}:binding:{index}".encode()),
                    "expected_generation": index - 1, "path": f"{key}-step-{index}.md",
                    "content": f"Synthetic private project {key[0]} step {index}: café\n"}

        requests = {key: [request(key, 1), request(key, 2)] for key in KEYS}
        docker("network", "create", "--internal", network)
        try:
            source = start("source", source_image_id)
            tools = source.rpc("tools/list", {})["tools"]
            assert len(tools) == 2
            catalog_hash = sha(canonical(tools))
            receipts = {}
            for key, limit in [(KEYS[0], 2), (KEYS[1], 1)]:
                receipts[key] = []
                for req in requests[key][:limit]:
                    result = source.call("workspace_create_bound", req)
                    assert not result.get("isError"), result
                    receipts[key].append(result)
            stop(source)  # Fence this fixture before copying its journal/material.
            for name in ["work", "journal"]:
                shutil.copytree(paths["source"][name], paths["target"][name], copy_function=shutil.copy2)
                assert paths["source"][name].stat().st_ino != paths["target"][name].stat().st_ino
            for key in KEYS:
                for filename in ["workspace-writer.journal", ".project-identity.json"]:
                    assert (paths["source"]["journal"] / key / filename).read_bytes() == (paths["target"]["journal"] / key / filename).read_bytes()
                assert (paths["source"]["journal"] / key).stat().st_ino != (paths["target"]["journal"] / key).stat().st_ino
            for file in paths["source"]["work"].iterdir():
                copy = paths["target"]["work"] / file.name
                assert file.read_bytes() == copy.read_bytes() and file.stat().st_ino != copy.stat().st_ino
            write_private(paths["target"]["conf"] / "original-binding.json", original_bytes)
            prepare_script = """
import fs from 'node:fs';
import {prepareRelocation} from '/opt/eva2/atomic/relocation.mjs';
const flags=fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW;
const rootFd=fs.openSync('/workspace',flags),journalFd=fs.openSync('/journal',flags);
try {
 const result=prepareRelocation({executable:'/opt/eva2/atomic/workspace-writer-broker',rootFd,journalFd,
 originalBindingBytes:fs.readFileSync('/run/eva2/original-binding.json'),logicalCatalogIdentity:process.argv[1],projectKeys:JSON.parse(process.argv[2])});
 console.log(JSON.stringify({binding:result.bindingBytes.toString('base64'),snapshots:Object.fromEntries(Object.entries(result.snapshots).map(([key,value])=>[key,value.toString('base64')]))}));
} finally {fs.closeSync(rootFd);fs.closeSync(journalFd);}
"""
            import base64
            package = json.loads(docker("run", "--rm", "--network", "none", "--read-only", "--user", user,
                                        *mounts(paths["target"], readonly=True), "--entrypoint", "node", image_id,
                                        "--input-type=module", "-e", prepare_script, catalog_hash, json.dumps(KEYS)))
            binding_bytes = base64.b64decode(package["binding"])
            write_private(paths["target"]["conf"] / "atomic-binding.json", binding_bytes)
            relocation_dir = paths["target"]["conf"] / "relocations"
            relocation_dir.mkdir(mode=0o700)
            for key, encoded in package["snapshots"].items():
                write_private(relocation_dir / (key + ".relocation"), base64.b64decode(encoded))
            prefixes = {key: (paths["target"]["journal"] / key / "workspace-writer.journal").read_bytes() for key in KEYS}
            before = {name: tree_state(paths["target"][name]) for name in ["work", "journal"]}
            # Read-only bind mounts provide kernel enforcement for the entire
            # ordinary HTTP observe path, including resolver and native broker.
            observer = start("observe", image_id, readonly=True)
            assert observer.rpc("tools/list", {})["tools"] == tools
            for key in KEYS:
                for index, expected in enumerate(receipts[key]):
                    assert observer.call("workspace_observe_bound", requests[key][index]) == expected
            absent = observer.call("workspace_observe_bound", requests[KEYS[1]][1])
            assert absent["isError"] and absent["structuredContent"]["status"] == "blocked"
            assert before == {name: tree_state(paths["target"][name]) for name in ["work", "journal"]}
            stop(observer)
            target = start("resume", image_id)
            assert target.rpc("tools/list", {})["tools"] == tools
            missing = requests[KEYS[1]][1]
            completed = target.call("workspace_create_bound", missing)
            assert not completed.get("isError"), completed
            assert completed["structuredContent"]["effect"] == "created"
            after = {name: tree_state(paths["target"][name]) for name in ["work", "journal"]}
            for key in KEYS:
                journal = (paths["target"]["journal"] / key / "workspace-writer.journal").read_bytes()
                assert journal.startswith(prefixes[key])
                assert (journal == prefixes[key]) == (key == KEYS[0])
            assert target.call("workspace_create_bound", missing) == completed
            docker("restart", target.name)
            target.ready()
            assert target.call("workspace_create_bound", missing) == completed
            assert target.call("workspace_observe_bound", missing) == completed
            assert after == {name: tree_state(paths["target"][name]) for name in ["work", "journal"]}
            assert len(list(paths["target"]["work"].iterdir())) == 4
            stop(target)
            authority = relocation_dir / (KEYS[0] + ".relocation")
            damaged = bytearray(authority.read_bytes())
            damaged[48] ^= 1
            authority.chmod(0o600)
            write_private(authority, damaged)
            denied = start("wrong-authority", image_id, readonly=True)
            result = denied.call("workspace_observe_bound", requests[KEYS[0]][0])
            assert result["isError"] and result["structuredContent"]["status"] == "blocked"
            assert after == {name: tree_state(paths["target"][name]) for name in ["work", "journal"]}
            stop(denied)
            print(json.dumps({
                "passed": True, "transport": "ContextForge translate HTTP -> SDK stdio -> native",
                "sourceImage": source_image_id, "targetImage": image_id, "targetTag": image_tag,
                "baseImage": base_image_id, "buildOutputSha256": build_hash,
                "temporaryBuildBaseReference": "local tag pinned to the inspected base image ID; product Dockerfile unchanged",
                "sourceHashes": source_hashes, "nativeBinarySha256": binary_hash,
                "testSha256": sha(Path(__file__).read_bytes()),
                "sourceCommit": command("git", "-C", str(REPO), "rev-parse", "HEAD"),
                "logicalProviderBindingSha256": sha(original_bytes), "relocationBindingSha256": sha(binding_bytes),
                "directMcpCatalogSha256": catalog_hash,
                "journalPrefixSha256": {key: sha(value) for key, value in prefixes.items()},
                "tests": ["completed and waiting projects copied to different directory/file inodes",
                          "source provider stopped before exact-byte copy", "offline preparation on readonly mounts",
                          "full original direct MCP tool schemas and descriptions preserved",
                          "all old receipts identical through ordinary HTTP observe",
                          "observe succeeds on readonly workspace/journal with zero metadata/content changes",
                          "missing second step executes once with original request and generation",
                          "restart and replay leave all material/journals unchanged",
                          "old prefixes retained while only waiting journal appends", "wrong authority refuses without writes"],
                "limits": ["synthetic isolated fixtures only", "no gateway registration or private core route",
                           "no owner mandate, person binding or restore-release integration",
                           "no live installation", "OS owner and P-only crash negatives are in the native suite"],
                "fixtureContainersRemoved": True, "candidateImageRetained": True,
            }))
        finally:
            for name in containers:
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
