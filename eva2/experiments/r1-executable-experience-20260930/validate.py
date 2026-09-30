"""Development-only differential check through the installed offline executor."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

from eva.adapters.connectors.procedure_execution import configured_procedure_executor
from service import transition


def main():
    artifact = json.loads(Path(sys.argv[1]).read_text())
    executor = configured_procedure_executor()
    if executor is None:
        raise ValueError("existing isolated executor unavailable")
    state = {"version": 1, "revision": 7, "request_ids": [], "capacities": {"one": 3, "two": 2},
        "reservations": {"old": {"slot": "one", "units": 2}},
        "tasks": {"task": {"audience": "owner", "authorized_ids": ["old", "new"], "stopped": False}}}
    def body(**kwargs):
        return {"id": "new", "slot": "one", "units": 1, "task_id": "task",
            "expected_revision": 7, "request_id": "r", **kwargs}
    def move_body(**kwargs):
        return {key: value for key, value in body(**kwargs).items() if key != "units"}
    def release_body(**kwargs):
        return {key: value for key, value in body(**kwargs).items() if key not in {"slot", "units"}}
    cases = [
        ("reserve", state, [{"path": "/reserve", "body": body()}]),
        ("capacity", state, [{"path": "/reserve", "body": body(units=2)}]),
        ("move_then_reserve", state, [
            {"path": "/move", "body": move_body(id="old", slot="two")},
            {"path": "/reserve", "body": body(units=3, expected_revision=8, request_id="r2")}]),
        ("release", state, [{"path": "/release", "body": release_body(id="old")}]),
        ("stale", state, [{"path": "/reserve", "body": body(expected_revision=6)}]),
        ("unauthorized", state, [{"path": "/reserve", "body": body(id="foreign")}]),
        ("stopped", {**state, "tasks": {"task": {**state["tasks"]["task"], "stopped": True}}},
            [{"path": "/reserve", "body": body()}]),
        ("version_change", {**state, "version": 2}, [{"path": "/reserve", "body": body()}]),
    ]
    records = []
    for name, original, operations in cases:
        started = time.monotonic()
        report = executor.execute(artifact["predictor_source"], {"state": original, "operations": operations})
        execution = report["result"]
        if execution["exit_code"] != 0 or execution["timed_out"] or execution["cancelled"] or execution["stdout_truncated"]:
            raise ValueError("predictor did not complete in the existing isolated executor")
        observed = json.loads(execution["stdout"])
        expected = copy.deepcopy(original)
        flags = []
        if original["version"] == 1:
            for operation in operations:
                expected, response = transition(expected, operation["path"], operation["body"])
                flags.append(response["ok"])
                if not response["ok"]:
                    break
        passed = (observed["unsupported"] == (original["version"] != 1)
            and observed["state"] == expected
            and [x["ok"] for x in observed["steps"]] == flags)
        records.append({"case": name, "passed": passed, "seconds": time.monotonic() - started,
            "sourceSha256": report["source_sha256"], "executorImage": report["image_id"],
            "inputSha256": report["input_sha256"]})
    output = {"kind": "development_validation_not_heldout", "cases": records,
        "allPassed": all(x["passed"] for x in records), "modelCalls": 0}
    Path(sys.argv[2]).write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    main()
