"""Apply an independently reviewed package without replacing Eva's shared core.

Only trusted host configuration selects the image, network, credentials and
storage. Package code never runs on the host and never sees the Docker socket,
Eva's database, other workspaces or ambient core/provider credentials.
"""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.owned_package import archive_files, json_object, key, sha, validate_descriptor
from eva.operations.reviewed_component_application import _artifact, _path, _save


def _archive(binding):
    from eva.operations.component_native_work_producer import read_private
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError("exact package archive binding required")
    raw = read_private(_path(binding["path"]))
    if sha(raw) != key(binding["sha256"]):
        raise ValueError("reviewed archive changed")
    return raw


def validate_review(package, review, work, verification):
    if (set(review) != {"contract", "package_sha256", "decision", "reason", "checks", "rights_verified"}
            or review["contract"] != "EvaOwnedPackageReview/v1"
            or review["package_sha256"] != package["package_sha256"]
            or review["decision"] != "accept" or review["rights_verified"] is not True
            or not isinstance(review["reason"], str) or not 1 <= len(review["reason"]) <= 8000
            or not isinstance(review["checks"], list) or not 1 <= len(review["checks"]) <= 64):
        raise ValueError("package has no independent quality and rights acceptance")
    for check in review["checks"]:
        if (not isinstance(check, dict) or set(check) != {"name", "passed", "evidence"}
                or check["passed"] is not True
                or any(not isinstance(check[k], str) or not 1 <= len(check[k]) <= 4000
                       for k in ("name", "evidence"))):
            raise ValueError("package independent checks are incomplete")
    origin = package["origin"]
    if (work.get("state") not in {"succeeded", "verified"} or work.get("active_lease") is not False
            or work.get("task_id") == origin["task_id"] or work.get("execution_id") == origin["execution_id"]
            or work.get("workspace_key") == origin["workspace_key"]
            or work.get("owner_binding_sha256") != origin["owner_binding_sha256"]
            or work.get("review_origin") != origin
            or work.get("review_package_sha256") != package["package_sha256"]):
        raise ValueError("package reviewer is not separate authorized completed work")
    if (verification.get("contract") != "EvaOwnedPackageIndependentVerification/v1"
            or verification.get("package_sha256") != package["package_sha256"]
            or verification.get("review_spec_sha256") != work.get("spec_sha256")
            or verification.get("exit_code") != 0 or verification.get("timed_out") is not False
            or verification.get("truncated") is not False):
        raise ValueError("independent package tests did not complete")
    for field in ("image", "test_sha256", "stdout_sha256", "stderr_sha256"):
        value = verification.get(field)
        key(value.removeprefix("sha256:") if isinstance(value, str) and field == "image" else value)


def verify_release(review_id, release):
    if (not isinstance(release, dict) or set(release) != {"kind", "package", "archive", "review",
            "review_work", "verification", "policy", "network_authority", "update_receipt"} or release["kind"] != "owned_package"):
        raise ValueError("reviewed package release fields differ")
    _, package = _artifact(release["package"])
    if (package.get("contract") != "EvaOwnedPackageSnapshot/v1"
            or package.get("package_sha256") != canonical_sha256({
                k: v for k, v in package.items() if k != "package_sha256"})
            or package.get("archive_sha256") != key(review_id)):
        raise ValueError("reviewed package identity differs")
    raw = _archive(release["archive"])
    if sha(raw) != package["archive_sha256"]:
        raise ValueError("release archive does not match reviewed snapshot")
    validate_descriptor(package["descriptor"], archive_files(raw))
    _, review = _artifact(release["review"])
    _, work = _artifact(release["review_work"])
    _, verification = _artifact(release["verification"])
    validate_review(package, review, work, verification)
    _, policy = _artifact(release["policy"])
    validate_policy(policy, package)
    _, network = _artifact(release['network_authority'])
    if (network.get('contract') != 'EvaOwnedPackageNetworkAuthority/v1' or network.get('origin') != package['origin']
            or network.get('hosts') != package['descriptor']['permissions']['network_hosts']
            or network.get('current') is not True):
        raise ValueError('reviewed package network authorization differs')
    return {"kind": "owned_package", "package": package, "archive": release["archive"],
            "policy": policy, "accepted_review_sha256": canonical_sha256(review),
            "receipt": _path(release["update_receipt"], exists=False)}


def validate_policy(policy, package):
    required = {"image", "network", "root", "memory_mb", "cpus", "pids", "network_hosts",
                "environment", "secret_files"}
    if (not isinstance(policy, dict) or set(policy) != required
            or not isinstance(policy["image"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", policy["image"]) is None
            or not isinstance(policy["network"], str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", policy["network"]) is None
            or policy["network"] in {"host", "container"}
            or type(policy["memory_mb"]) is not int or not 128 <= policy["memory_mb"] <= 2048
            or type(policy["cpus"]) not in {int, float} or not 0.1 <= policy["cpus"] <= 2
            or type(policy["pids"]) is not int or not 16 <= policy["pids"] <= 128):
        raise ValueError("host package runtime policy differs")
    root = Path(policy["root"])
    if not root.is_absolute() or root == Path("/") or root.resolve() != root:
        raise ValueError("exact private package installation root required")
    permissions = package["descriptor"]["permissions"]
    if (not isinstance(policy["network_hosts"], list)
            or not set(permissions["network_hosts"]) <= set(policy["network_hosts"])
            or (permissions["network_hosts"] and policy["network"] == "none")
            or not isinstance(policy["environment"], dict) or not isinstance(policy["secret_files"], dict)
            or not set(policy["secret_files"]) <= set(permissions["secret_names"])):
        raise ValueError("package exceeds host-authorized configuration or network policy")
    for name, value in policy["environment"].items():
        if (not isinstance(name, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name) is None
                or not isinstance(value, str) or "\x00" in value
                or name in {"PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH"}
                or name in permissions["secret_names"]):
            raise ValueError("package environment may contain only declared non-secret settings")
    for path in policy["secret_files"].values():
        _path(path)
    return policy


def configuration_status(policy, package, files):
    from jsonschema import Draft202012Validator
    schema = json_object(files[package["descriptor"]["config_schema"]])
    # Configuration validation must never fetch a model-selected remote schema.
    def local(value):
        if isinstance(value, dict):
            if "$ref" in value and not str(value["$ref"]).startswith("#"):
                raise ValueError("remote package configuration schema is forbidden")
            for item in value.values():
                local(item)
        elif isinstance(value, list):
            for item in value:
                local(item)
    local(schema)
    Draft202012Validator.check_schema(schema)
    environment = policy["environment"]
    missing = sorted(set(schema.get("required", [])) - set(environment))
    bearer = package["descriptor"]["transport"]["bearer_env"]
    missing_secrets = sorted(set(package["descriptor"]["permissions"]["secret_names"])
                             - set(policy["secret_files"]) - {bearer})
    if missing or missing_secrets:
        return {"ready": False, "missing_configuration": missing, "missing_secrets": missing_secrets}
    if list(Draft202012Validator(schema).iter_errors(environment)):
        raise ValueError("host package configuration does not match its schema")
    return {"ready": True, "missing_configuration": [], "missing_secrets": []}


class OwnedPackageRuntime:
    def __init__(self, *, run=subprocess.run):
        self.run = run

    def command(self, args, *, timeout=60, input=None):
        result = self.run(args, text=True, input=input, capture_output=True, timeout=timeout)
        if result.returncode or len(result.stdout.encode()) > 262144:
            raise RuntimeError("managed package command did not complete")
        return result.stdout

    @staticmethod
    def _binding(actual):
        config, host = actual['Config'], actual['HostConfig']
        if (not host['ReadonlyRootfs'] or host['Privileged'] or host.get('CapAdd')
                or host['CapDrop'] != ['ALL'] or host.get('PortBindings')
                or config['User'] != '10001:10001'
                or not any(value in {'no-new-privileges', 'no-new-privileges:true'} for value in host.get('SecurityOpt', []))
                or any(mount['Destination'] not in {'/tmp', '/data'} for mount in actual['Mounts'])):
            raise ValueError('package runtime isolation differs')
        return canonical_sha256({'image': actual['Image'], 'config': {key: config.get(key) for key in
            ('User', 'WorkingDir', 'Entrypoint', 'Cmd', 'Env')},
            'host': {key: host.get(key) for key in ('NetworkMode', 'ReadonlyRootfs', 'Privileged',
                'CapAdd', 'CapDrop', 'SecurityOpt', 'PortBindings', 'Memory', 'MemorySwap', 'NanoCpus', 'PidsLimit', 'Tmpfs')},
            'mounts': actual['Mounts'], 'networks': sorted(actual['NetworkSettings']['Networks'])})

    def inspect(self, receipt, package):
        container = receipt.get('container_id')
        if (receipt.get('package_sha256') != package['package_sha256'] or not isinstance(container, str)
                or re.fullmatch(r'[0-9a-f]{64}', container) is None):
            raise ValueError('package container binding differs')
        actual = json.loads(self.command(['docker', 'inspect', container]))[0]
        if (actual['Image'] != receipt['image'] or actual['State']['Running'] is not True
                or self._binding(actual) != receipt.get('runtime_binding_sha256')):
            raise ValueError('package runtime changed or stopped')
        return actual

    def apply(self, release):
        package, policy = release["package"], release["policy"]
        raw = _archive(release["archive"])
        files = archive_files(raw)
        if not configuration_status(policy, package, files)["ready"]:
            raise ValueError("package requires its actual host configuration")
        receipt = release["receipt"]
        if receipt.exists():
            raise ValueError("an application receipt already owns this package effect")
        identity = canonical_sha256({"package": package["package_sha256"], "task_id": release["task_id"]})
        root = Path(policy["root"])
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
            raise ValueError("package installation root must be private")
        slot_key = canonical_sha256({'owner_binding': package['origin']['owner_binding_sha256'], 'name': package['descriptor']['name']})
        slot = root / ('active-' + slot_key + '.json')
        with (root / ('.slot-' + slot_key + '.lock')).open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if slot.exists():
                # A second version must not race the first process against its
                # data. Retain it until a separate reviewed upgrade is available.
                raise ValueError('an existing package activation requires inspection or a reviewed upgrade')
            _save(slot, {'package_sha256': package['package_sha256'], 'task_id': release['task_id'],
                         'state': 'reserved', 'receipt': str(receipt)}, initial=True)
        directory = root / identity
        directory.mkdir(mode=0o700)
        source = directory / "source"
        source.mkdir(mode=0o755)
        for path, content in files.items():
            destination = source / path
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            destination.write_bytes(content)
            destination.chmod(0o444)
        image_file = directory / "image.txt"
        (directory / "Dockerfile").write_text("FROM " + policy["image"] + "\nCOPY source/ /package/\n")
        # Fixed COPY only: no package Dockerfile, RUN instruction or build network.
        self.command(["docker", "build", "--network=none", "--pull=false", "--iidfile", str(image_file), str(directory)], timeout=180)
        image = image_file.read_text().strip()
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
            raise ValueError("package image identity differs")
        environment = dict(policy["environment"])
        for name, path in policy["secret_files"].items():
            from eva.operations.component_native_work_producer import read_private
            environment[name] = read_private(path, limit=16384).decode().strip()
        bearer_env = package["descriptor"]["transport"]["bearer_env"]
        environment[bearer_env] = secrets.token_urlsafe(48)
        if any(not value or any(c in value for c in "\x00\r\n") for value in environment.values()):
            raise ValueError("package configuration cannot be represented as a private environment")
        env_file = directory / "environment.private"
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(k + "=" + v for k, v in environment.items()) + "\n")
        data = root / ("data-" + canonical_sha256({"owner_binding": package["origin"]["owner_binding_sha256"],
                                                   "name": package["descriptor"]["name"]}))
        data.mkdir(mode=0o700, exist_ok=True)
        if data.is_symlink() or data.resolve() != data:
            raise ValueError("package data path differs")
        os.chown(data, 10001, 10001)
        name = "eva-owned-package-" + identity
        state = {"contract": "EvaOwnedPackageApplication/v1", "task_id": release["task_id"],
                 "package_sha256": package["package_sha256"], "archive_sha256": package["archive_sha256"],
                 "status": "dispatching", "container_name": name, "image": image,
                 "environment_file": str(env_file), "data_directory": str(data), "installed": False}
        _save(receipt, state, initial=True)  # reserve BEFORE docker create/start
        args = ["docker", "run", "--detach", "--name", name, "--pull=never", "--no-healthcheck",
                "--network", policy["network"], "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--user=10001:10001", "--memory=" + str(policy["memory_mb"]) + "m",
                "--memory-swap=" + str(policy["memory_mb"]) + "m", "--cpus=" + str(policy["cpus"]),
                "--pids-limit=" + str(policy["pids"]), "--log-driver=none", "--workdir=/package",
                "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1", "--env-file", str(env_file),
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777"]
        if package["descriptor"]["permissions"]["persistent_data"]:
            args += ["--mount", "type=bind,src=" + str(data) + ",dst=/data"]
        args += ["--entrypoint=python", image, "/package/" + package["descriptor"]["entrypoint"]]
        container = self.command(args).strip()
        if re.fullmatch(r"[0-9a-f]{64}", container) is None:
            raise ValueError("package container identity unconfirmed")
        state["container_id"] = container
        observed = json.loads(self.command(['docker', 'inspect', container]))[0]
        if observed['Image'] != image or observed['HostConfig']['NetworkMode'] != policy['network']:
            raise ValueError('created package runtime does not match the reviewed plan')
        data_mounts = [mount for mount in observed['Mounts'] if mount['Destination'] == '/data']
        if (bool(data_mounts) != package['descriptor']['permissions']['persistent_data']
                or any(mount['Type'] != 'bind' or mount['Source'] != str(data) or mount['RW'] is not True for mount in data_mounts)):
            raise ValueError('created package storage differs from its private data binding')
        state['runtime_binding_sha256'] = self._binding(observed)
        _save(receipt, state)
        # Discovery is read-only. A failed/unknown start is never redispatched.
        for _ in range(20):
            try:
                tools = self.rpc(state, package, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                actual = tools["result"]["tools"]
                if (not isinstance(actual, list) or len(actual) != len(package["descriptor"]["tool_names"])
                        or {tool["name"] for tool in actual} != set(package["descriptor"]["tool_names"])):
                    raise ValueError("installed package tool registration differs")
                state.update(status="catalog_verified", installed=True, tool_catalog_sha256=canonical_sha256(actual))
                _save(receipt, state)
                _save(slot, {'package_sha256': package['package_sha256'], 'task_id': release['task_id'],
                             'state': 'active', 'receipt': str(receipt)})
                return state
            except RuntimeError:
                time.sleep(0.1)
        raise RuntimeError("package registration was not confirmed")

    def rpc(self, receipt, package, message):
        if receipt.get("package_sha256") != package["package_sha256"]:
            raise ValueError("package proxy identity differs")
        container = receipt.get("container_id")
        if not isinstance(container, str) or re.fullmatch(r"[0-9a-f]{64}", container) is None:
            raise ValueError("package has no exact running container")
        self.inspect(receipt, package)
        script = ("import json,os,sys,urllib.request; q=json.load(sys.stdin); "
            "r=urllib.request.Request('http://127.0.0.1:'+str(q['port'])+q['path'],"
            "json.dumps(q['message']).encode(),headers={'Content-Type':'application/json',"
            "'Authorization':'Bearer '+os.environ[q['bearer_env']]}); "
            "o=urllib.request.build_opener(urllib.request.ProxyHandler({}),"
            "type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda *a,**k:None})()); "
            "b=o.open(r,timeout=15).read(262145); "
            "assert len(b)<=262144; sys.stdout.buffer.write(b)")
        transport = package["descriptor"]["transport"]
        raw = self.command(["docker", "exec", "-i", container, "python", "-c", script],
                           timeout=20, input=json.dumps({**transport, "message": message}))
        return json_object(raw)

    def activation(self, receipt, package, *, action, query=''):
        """One bounded request to a reviewed local activation path; no retries."""
        activation = package['descriptor'].get('activation')
        if not activation or action not in {'start', 'callback'} or len(query.encode()) > 8192:
            raise ValueError('package activation route is unavailable')
        # Reuse the same immutable container/read-only checks as MCP access.
        container = receipt.get('container_id')
        if not isinstance(container, str) or re.fullmatch(r'[0-9a-f]{64}', container) is None:
            raise ValueError('package activation container differs')
        self.inspect(receipt, package)
        path = activation['start_path' if action == 'start' else 'callback_path']
        script = ("import json,os,sys,urllib.request; q=json.load(sys.stdin); "
            "r=urllib.request.Request('http://127.0.0.1:'+str(q['port'])+q['path']+q['query'],"
            "data=b'{}' if q['start'] else None,headers={'Content-Type':'application/json',"
            "'Authorization':'Bearer '+os.environ[q['bearer_env']]}); "
            "o=urllib.request.build_opener(urllib.request.ProxyHandler({}),"
            "type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda *a,**k:None})()); "
            "b=o.open(r,timeout=15).read(65537);assert len(b)<=65536;sys.stdout.buffer.write(b)")
        raw = self.command(['docker', 'exec', '-i', container, 'python', '-c', script], timeout=20,
            input=json.dumps({**package['descriptor']['transport'], 'path': path,
                              'query': '?' + query if query else '', 'start': action == 'start'}))
        return json_object(raw)


def receipt_confirms(receipt, package, task_id):
    return (receipt.get("contract") == "EvaOwnedPackageApplication/v1" and receipt.get("task_id") == task_id
        and receipt.get("package_sha256") == package["package_sha256"]
        and receipt.get("archive_sha256") == package["archive_sha256"]
        and receipt.get("status") == "catalog_verified" and receipt.get("installed") is True
        and isinstance(receipt.get("container_id"), str) and re.fullmatch(r"[0-9a-f]{64}", receipt["container_id"]) is not None
        and isinstance(receipt.get("tool_catalog_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", receipt["tool_catalog_sha256"]) is not None)
