
"""Read installed source bytes into an isolated regression harness; never send real messages."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, shutil, subprocess, tempfile, time, uuid

REPO = Path("/home/administrator/project/Eva2")
PLAN_ROOT = Path("/var/lib/eva2/finalization-20261105/technical-p0")
EVIDENCE = REPO / "development/evidence/reassessment/clean-staged-source-20261105"
EVIDENCE.mkdir(exist_ok=True)
PYTHON = "/home/administrator/project/.eva2-artifacts/pydantic-ai-2.37-gate-env.NWD66e/venv/bin/python"

def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=90, **kwargs)

def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
            and p.suffix not in (".pyc", ".pyo")}

def main():
    indexpath=Path(run(["git","rev-parse","--git-path","index"],cwd=REPO).stdout.strip())
    if not indexpath.is_absolute():indexpath=REPO/indexpath
    index_before=hashlib.sha256(indexpath.read_bytes()).hexdigest()
    root=Path(tempfile.mkdtemp(prefix="eva-p0-staged-"))
    paths=subprocess.check_output(["git","ls-files","-z","--","src","development/tests","deploy/alembic.ini","deploy/migrations","pyproject.toml"],cwd=REPO)
    stage_records=subprocess.check_output(["git","ls-files","--stage","-z","--","src","development/tests","deploy/alembic.ini","deploy/migrations","pyproject.toml"],cwd=REPO)
    result=subprocess.run(["git","checkout-index","--prefix="+str(root)+"/","-z","--stdin"],input=paths,cwd=REPO,capture_output=True)
    assert result.returncode==0,result.stderr.decode()
    index_after=hashlib.sha256(indexpath.read_bytes()).hexdigest()
    assert index_after==index_before,"Index changed during read-only snapshot"
    source=root/"src/eva"
    before=hashes(source)
    copied=hashes(root)
    env=dict(os.environ,PYTHONPATH=str(root/"src")+":"+str(root/"development"),
        PYTHONDONTWRITEBYTECODE="1",PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",PYTHONPYCACHEPREFIX=str(root/"fresh-pycache"))
    (EVIDENCE/"snapshot.json").write_text(json.dumps({"contract":"EvaReadOnlyStagedSnapshot/v1", "snapshot":str(root),"indexSha256Before":index_before,"indexSha256After":index_after,"indexUnchanged":True,"stageRecordsSha256":hashlib.sha256(stage_records).hexdigest(),"snapshotFileCount":len(copied),"snapshotSha256":copied,"workingTreeOverlay":False},indent=2)+"\n")
    socket = root / "pgsocket"
    socket.mkdir(mode=0o777)
    socket.chmod(0o777)
    name = "eva-staged-review-" + uuid.uuid4().hex[:10]
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
            database = "eva2_" + kind + "_disposable_staged_" + label
            run(["docker", "exec", name, "createdb", "-U", "postgres", database])
            dsn = "postgresql+psycopg://postgres@/" + database + "?host=" + str(socket)
            testenv = dict(env, EVA2_TEST_POSTGRES_DSN=dsn, EVA2_IN002_TEST_ADMIN_DSN=dsn)
            result = subprocess.run([PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                                    cwd=root, env=testenv, capture_output=True, text=True, timeout=180)
            log = result.stdout + result.stderr
            output = EVIDENCE / ("staged-" + label + "-tests.log")
            output.write_text(log)
            results.append({"suite": label, "tests": tests, "exitCode": result.returncode,
                            "log": str(output.relative_to(REPO)),
                            "logSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                            "summary": log[-2000:]})
            print(label, result.returncode, log[-2000:], flush=True)
        assert hashes(source) == before, "Frozen installed source mutated during tests"
        report = {"contract": "EvaCleanStagedSourceBehavior/v1",
                  "checkedAt": datetime.now(timezone.utc).isoformat(),
                  "operator": "/root/p001_repair", "snapshotOrigin":"git index only", "indexSha256AtSnapshot":index_before, "sourceSnapshot": str(source),
                  "sourceCount": len(before), "sourceHashes": before,
                  "sourceUnchanged": True, "results": results,
                  "allPassed": all(x["exitCode"] == 0 for x in results),
                  "limits": ["Executed clean staged source from the read-only Git index, without foreign working-tree overlays.",
                             "Only isolated PostgreSQL16.13 and fake transports; no real Telegram or external effects.",
                             "No full autonomous E4.16, later reuse or family acceptance claim."]}
        (EVIDENCE / "staged-source-behavior.json").write_text(json.dumps(report, indent=2) + "\n")
        print("ALL_PASSED", report["allPassed"], flush=True)
    finally:
        run(["docker", "stop", "--timeout", "10", name])
        print("DISPOSABLE_STOPPED", True, flush=True)

if __name__ == "__main__":
    main()
