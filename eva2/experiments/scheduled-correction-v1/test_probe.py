"""Bounded reproduction: correction must not strand a dependent project step."""
import hashlib
from pathlib import Path
from datetime import timedelta
from dataclasses import replace
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup


def test_observe_correction_dependency(database):
    service, turn, clock = setup(database, "l2-correction-dependency", False)
    root = Path(__file__).resolve().parents[2] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    service.digital_profile = DelegatedDigitalProfile("model:controlled", "a"*64, "b"*64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root/source).read_bytes()).hexdigest()),))
    first = service.execute("digital", turn, task="Prepare a document", request_key="first")["work"]["work_id"]
    second = ScheduledOwnerWork(service).schedule(turn, request_key="second", request={
        "task":"Summarize the completed document", "after_work_id":first})
    clock[0] += timedelta(seconds=1)
    correction = service.execute("revise_digital", replace(turn, message_id="correct"),
        request_key="correct", revision={"work_id":first, "instruction":"Use the corrected title"})
    assert correction["status"] == "correction_saved"
    scheduler = ScheduledOwnerWork(service)
    observations = [scheduler.run_once(), ScheduledOwnerWork(service).run_once()]
    rows = {row["schedule_id"]:row for row in ScheduledOwnerWork(service).list()["schedules"]}
    child = rows[second["schedule_id"]]
    continuation = rows[correction["schedule"]["schedule_id"]]
    # Assertions capture the defect, not a product acceptance or a desired behavior.
    assert child["state"] == "blocked", child
    assert continuation["state"] == "dispatched", continuation
    assert child["work_id"] is None
    assert continuation["work_id"] != first
    print("OBSERVED: corrected continuation dispatched; dependent step blocked; no child admitted", observations)
