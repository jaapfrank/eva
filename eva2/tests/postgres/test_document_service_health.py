"""Real isolated processes and PostgreSQL; metadata probes have no effects."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401

WORKER_IMAGE = "sha256:1d0a704d32255bc7630c8670d8137d6d6c2a7c76e4533f5186c8f79613205fa0"
PROVIDER_IMAGE = "sha256:ba9eda41299033a19e25c483e07e973b40545b226b062da6a29ccb70482f1b5e"
DEPLOY = Path(__file__).resolve().parents[2] / "deploy/living-core"


def docker(*args, check=True):
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=30)


def wait_probe(name, script):
    for _ in range(30):
        r = docker("exec", name, "python3", "/opt/eva2-health/" + script, check=False)
        if r.returncode == 0:
            return r
        time.sleep(0.25)
    raise AssertionError(r.stdout + r.stderr + docker("logs", name, check=False).stderr)


def private(path, content, uid):
    path.write_text(content)
    os.chown(path, uid, uid)
    path.chmod(0o400)


def test_real_worker_process_plan_and_database_health(timeline_engine, tmp_path):
    from eva.providers.document_goal_plan import DocumentGoalPlan
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    scope = external_timeline_scope("health-test", "isolated", "health")
    plan = DocumentGoalPlan("health-absent-goal", "a" * 64, "owner", str(scope.tenant_id), str(scope.installation_id),
        "b" * 64, "c" * 64, "/proof-state/draft.txt", "/proof-state/final.txt", "Draft", "Final", "\n")
    files = tmp_path / "worker"
    files.mkdir(); files.chmod(0o755)
    private(files / "plan", json.dumps(plan.to_payload()), 65532)
    private(files / "bad-plan", "{}", 65532)
    socket = timeline_engine.url.query["host"]
    private(files / "dsn", "postgresql://postgres@/" + timeline_engine.url.database + "?host=/test-socket", 65532)
    private(files / "bad-dsn", "postgresql://postgres@127.0.0.1:1/unreachable", 65532)
    name = "eva2-health-worker-proof-" + str(os.getpid())
    try:
        docker("run", "-d", "--name", name, "--network", "none", "--read-only", "--user", "65532:65532",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=384m",
            "--mount", f"type=bind,src={files},dst=/fixture,readonly",
            "--mount", f"type=bind,src={socket},dst=/test-socket,readonly",
            "--mount", f"type=bind,src={DEPLOY},dst=/health,readonly",
            "-e", "EVA_DATABASE_DSN_FILE=/fixture/dsn", "-e", "EVA_DOCUMENT_GOAL_PLAN_FILE=/fixture/plan",
            "-e", "EVA_RUNTIME_TENANT_ID=health-test", "-e", "EVA_RUNTIME_INSTALLATION_ID=isolated",
            WORKER_IMAGE, "-m", "eva.bootstrap.document_goal_worker")
        wait_probe(name, "document-worker-health.py")
        for key, value in [("EVA_DOCUMENT_GOAL_PLAN_FILE", "/fixture/bad-plan"), ("EVA_DATABASE_DSN_FILE", "/fixture/bad-dsn")]:
            r = docker("exec", "-e", key + "=" + value, name, "python3", "/opt/eva2-health/document-worker-health.py", check=False)
            assert r.returncode == 1 and r.stderr.strip() == "document worker not ready"
        docker("kill", "--signal=STOP", name)
        r = docker("exec", name, "python3", "/opt/eva2-health/document-worker-health.py", check=False)
        assert r.returncode == 1
        docker("kill", "--signal=CONT", name)
        wait_probe(name, "document-worker-health.py")
        docker("kill", name)
        assert not json.loads(docker("inspect", name).stdout)[0]["State"]["Running"]
        # Absent selected goal never becomes admitted by polling or probing.
        from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
        assert PostgresDurableGoalStore(timeline_engine, tenant_id="health-test", installation_id="isolated").state(plan.goal_id, "owner") is None
    finally:
        docker("rm", "-f", name, check=False)


def test_real_provider_catalog_binding_and_dead_stdio(tmp_path):
    root = tmp_path / "root"; journal = tmp_path / "journal"; secrets = tmp_path / "secrets"
    for p in [root, journal, secrets]:
        p.mkdir(); os.chown(p, 10001, 10001); p.chmod(0o700)
    # Read immutable image metadata, never production bindings or secrets.
    probe = docker("run", "--rm", "--network=none", "--entrypoint=python3", PROVIDER_IMAGE, "-c",
        "from pathlib import Path;import hashlib,json;print(json.dumps({k:hashlib.sha256(Path('/opt/eva2/atomic/'+v).read_bytes()).hexdigest() for k,v in [('binary','workspace-writer-broker'),('client','broker-client.mjs')]}))")
    hashes = json.loads(probe.stdout)
    binding = {"version": 1, "project_journal_identity": "d" * 64, "binary_sha256": hashes["binary"], "client_sha256": hashes["client"],
        "root": {"dev": root.stat().st_dev, "ino": root.stat().st_ino}, "journal": {"dev": journal.stat().st_dev, "ino": journal.stat().st_ino}}
    private(secrets / "atomic-binding.json", json.dumps(binding), 10001)
    for n in ["jwt", "encryption", "password"]:private(secrets / n, os.urandom(48).hex(), 10001)
    name = "eva2-health-provider-proof-" + str(os.getpid())
    try:
        docker("run", "-d", "--name", name, "--network=none", "--read-only", "--user=10001:10001", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777", "--pids-limit=128", "--memory=768m",
            "--mount", f"type=bind,src={root},dst=/workspace,readonly", "--mount", f"type=bind,src={journal},dst=/journal,readonly",
            "--mount", f"type=bind,src={secrets},dst=/run/eva2,readonly", "--mount", f"type=bind,src={DEPLOY},dst=/health,readonly",
            "-e", "EVA_CONTEXTFORGE_JWT_SECRET_FILE=/run/eva2/jwt", "-e", "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE=/run/eva2/encryption",
            "-e", "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE=/run/eva2/password", PROVIDER_IMAGE)
        wait_probe(name, "document-provider-health.py")
        original_binding = (secrets / "atomic-binding.json").read_bytes()
        (secrets / "atomic-binding.json").write_text(json.dumps({**binding, "project_journal_identity": "e" * 64}))
        assert docker("exec", name, "python3", "/opt/eva2-health/document-provider-health.py", check=False).returncode == 1
        (secrets / "atomic-binding.json").write_bytes(original_binding)
        wait_probe(name, "document-provider-health.py")
        docker("exec", name, "python3", "-c", "from pathlib import Path;import os,signal; p=[int(d.name) for d in Path('/proc').glob('[0-9]*') if (d/'cmdline').exists() and (d/'cmdline').read_bytes().split(bytes([0]))[:2]==[b'node',b'/opt/eva2/atomic/mcp-server.mjs']];assert len(p)==1;os.kill(p[0],signal.SIGSTOP)")
        assert docker("exec", name, "python3", "-c", "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9000/healthz',timeout=2).read().decode())").stdout.strip() == "ok"
        r = docker("exec", name, "python3", "/opt/eva2-health/document-provider-health.py", check=False)
        assert r.returncode == 1 and r.stderr.strip() == "document provider not ready"
        docker("kill", name)
        assert not json.loads(docker("inspect", name).stdout)[0]["State"]["Running"]
        assert not list(root.iterdir()) and not list(journal.iterdir())
    finally:
        docker("rm", "-f", name, check=False)
