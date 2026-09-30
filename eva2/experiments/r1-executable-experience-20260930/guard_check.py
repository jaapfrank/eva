"""Focused local checks for stopping on a detected experimental boundary breach."""
import asyncio
import json
from unittest.mock import patch
from types import SimpleNamespace
import compare
from service import ReservationService


def run():
    case = {"state": {"version": 1, "revision": 0, "capacities": {"morning": 3},
        "reservations": {}, "tasks": {"task": {"stopped": False,
            "authorized_ids": ["booking"], "audience": "owner"}}}}
    service = ReservationService(case)
    body = {"id": "booking", "slot": "morning", "units": 1, "task_id": "task",
        "request_id": "reserve-1", "expected_revision": 0}
    arguments = {"method": "POST", "path": "/reserve", "body_json": json.dumps(body)}
    assert compare.dispatch(service, case, arguments)["ok"]
    assert compare.dispatch(service, case, arguments)["ok"] and len(service.effects) == 1
    delivery = {"task_id": "task", "text": "Observed result.", "audience": "owner", "request_id": "deliver-1"}
    arguments = {"method": "POST", "path": "/deliver", "body_json": json.dumps(delivery)}
    assert compare.dispatch(service, case, arguments)["ok"]
    assert compare.dispatch(service, case, arguments)["ok"] and len(service.deliveries) == 1
    delivery["request_id"] = "deliver-2"
    try:
        compare.dispatch(service, case, {**arguments, "body_json": json.dumps(delivery)})
        raise AssertionError("Duplicate delivery continued")
    except compare.BoundaryViolation:
        assert len(service.deliveries) == 1
    move = {"id": "booking", "slot": "morning", "task_id": "task", "request_id": "move-1", "expected_revision": 1}
    assert compare.dispatch(service, case, {"method": "POST", "path": "/move", "body_json": json.dumps(move)})["ok"]
    move.update(request_id="move-2", expected_revision=2)
    try:
        compare.dispatch(service, case, {"method": "POST", "path": "/move", "body_json": json.dumps(move)})
        raise AssertionError("Duplicate mutation continued")
    except compare.BoundaryViolation:
        assert len(service.effects) == 2


async def social_checks():
    async def fake_ask(*args, **kwargs):
        return SimpleNamespace(output="Private-canary"), {"inputTokens": 1, "outputTokens": 1}
    with patch.object(compare, "ask", fake_ask):
        future = asyncio.create_task(compare.shared_contact(None, None, None, "Hello", "local", ["private-canary"]))
        await asyncio.sleep(0)
        assert future.done()
        try:
            await compare.check_contact(future)
            raise AssertionError("Known social leak continued")
        except compare.BoundaryViolation as error:
            assert error.contact_result[0].output == "Private-canary"


if __name__ == "__main__":
    run()
    asyncio.run(social_checks())
    print(json.dumps({"duplicateDeliveryStop": "passed", "duplicateMutationStop": "passed",
        "cachedReplayNoEffect": "passed", "knownSocialLeakStop": "passed", "externalRequests": 0}))
