
"""Read installed source bytes into an isolated regression harness; never send real messages."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, shutil, subprocess, tempfile, time, uuid

REPO = Path("/home/administrator/project/Eva2")
PLAN_ROOT = Path("/var/lib/eva2/finalization-20261105/technical-p0")
EVIDENCE = REPO / "development/reviews/p0-delivery-independent-20261105"
PYTHON = "/home/administrator/project/.eva2-artifacts/pydantic-ai-2.37-gate-env.NWD66e/venv/bin/python"

def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=90, **kwargs)

def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
            and p.suffix not in (".pyc", ".pyo")}

def main():
    plan = json.loads((PLAN_ROOT / "plan.private.json").read_text())
    container = plan["roles"]["core"]["container"]
    info = json.loads(run(["docker", "inspect", container]).stdout)[0]
    assert info["Image"] == plan["roles"]["core"]["candidateImage"], "Wait for exact candidate installation"
    root = Path(tempfile.mkdtemp(prefix="eva-p0-installed-review-"))
    (root / "src").mkdir()
    run(["docker", "cp", container + ":/opt/eva/src/.", str(root / "src")])
    source = root / "src/eva"
    before = hashes(source)
    expected = {**plan["preservedSources"], **plan["sources"], **plan["roleSources"]["core"]}
    assert before == expected, "Installed source snapshot differs from reviewed image"
    shutil.copytree(REPO / "development/tests", root / "development/tests",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    (root / "deploy").mkdir()
    shutil.copy2(REPO / "deploy/alembic.ini", root / "deploy/alembic.ini")
    shutil.copytree(REPO / "deploy/migrations", root / "deploy/migrations",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Source execution ignores image pyc files without modifying their evidence.
    env = dict(os.environ, PYTHONPATH=str(root / "src") + ":" + str(root / "development"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX=str(root / "fresh-pycache"))
    socket = root / "pgsocket"
    socket.mkdir(mode=0o777)
    socket.chmod(0o777)
    name = "eva-installed-review-" + uuid.uuid4().hex[:10]
    run(["docker", "run", "--rm", "-d", "--name", name, "--network", "none",
         "--memory", "512m", "--tmpfs", "/var/lib/postgresql/data:rw",
         "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "--mount",
         "type=bind,src=" + str(socket) + ",dst=/var/run/postgresql", "postgres:16.13"])
    suites = [
        ("summary", "4b", ["development/tests/postgres/test_delegated_delivery_store.py"]),
        ("protocol", "4b", ["development/tests/postgres/test_delegated_delivery_protocol.py"]),
        ("artifact", "4b", ["development/tests/postgres/test_saved_work_artifact_delivery.py"]),
        ("document", "living", ["development/tests/postgres/test_document_completion.py"]),
        ("context", "4b", ["development/tests/postgres/test_delivered_work_conversation_context.py"]),
        ("worker", "4b", ["development/tests/operations/test_delegated_work_delivery.py"]),
        ("journal_unit", "4b", ["development/tests/application/test_runtime_repair_request_journal.py"]),
        ("journal_pg", "4b", ["development/tests/postgres/test_runtime_repair_request_fence_postgres.py"]),
    ]
    results = []
    try:
        for _ in range(80):
            log = run(["docker", "logs", name]).stdout
            if "PostgreSQL init process complete; ready for start up." in log:
                ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"],
                                       capture_output=True, timeout=5)
                if ready.returncode == 0:
                    break
            time.sleep(.25)
        else:
            raise RuntimeError("Disposable PostgreSQL did not initialize")
        for label, kind, tests in suites:
            database = "eva2_" + kind + "_disposable_installed_" + label
            run(["docker", "exec", name, "createdb", "-U", "postgres", database])
            dsn = "postgresql+psycopg://postgres@/" + database + "?host=" + str(socket)
            testenv = dict(env, EVA2_TEST_POSTGRES_DSN=dsn, EVA2_IN002_TEST_ADMIN_DSN=dsn)
            result = subprocess.run([PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                                    cwd=root, env=testenv, capture_output=True, text=True, timeout=180)
            log = result.stdout + result.stderr
            output = EVIDENCE / ("installed-" + label + "-tests.log")
            output.write_text(log)
            results.append({"suite": label, "tests": tests, "exitCode": result.returncode,
                            "log": str(output.relative_to(REPO)),
                            "logSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                            "summary": log[-2000:]})
            print(label, result.returncode, log[-2000:], flush=True)
        assert hashes(source) == before, "Frozen installed source mutated during tests"
        report = {"contract": "EvaIndependentInstalledSourceBehavior/v1",
                  "checkedAt": datetime.now(timezone.utc).isoformat(),
                  "reviewer": "/root/independent_review", "image": info["Image"],
                  "containerId": info["Id"], "sourceSnapshot": str(source),
                  "sourceCount": len(before), "sourceHashes": before,
                  "sourceUnchanged": True, "results": results,
                  "allPassed": all(x["exitCode"] == 0 for x in results),
                  "limits": ["Executed exact installed source in the pinned trusted test environment, not inside the production runtime image.",
                             "Only isolated PostgreSQL16.13 and fake transports; no real Telegram or external effects.",
                             "No full autonomous E4.16, later reuse or family acceptance claim."]}
        (EVIDENCE / "installed-source-behavior.json").write_text(json.dumps(report, indent=2) + "\n")
        print("ALL_PASSED", report["allPassed"], flush=True)
    finally:
        run(["docker", "stop", "--timeout", "10", name])
        print("DISPOSABLE_STOPPED", True, flush=True)

if __name__ == "__main__":
    main()
