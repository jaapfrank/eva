"""SQL ordering keeps an older active task ahead of bounded terminal history."""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from tests.postgres.test_household_public_work import setup, PUBLIC, delegated_engine


@pytest.mark.parametrize("household", [False, True])
def test_active_first_prevents_newer_terminal_history_hiding_running_work(delegated_engine, household):
    x = setup(delegated_engine, "ordering-" + str(household))
    if household:
        service, turn = x.service, x.turn
        operation, payload = "research", {"research": PUBLIC}
    else:
        service = DelegatedWorkOwnerService(x.server.store, **x.service._owner,
            clock=lambda: x.clock[0], conversation_scope=x.server.conversation_scope)
        turn = ConversationTurn.private(x.turn.tenant_id, x.turn.installation_id,
            str(service.owner_principal_id), "private:ordering", "old", "Old task", x.turn.occurred_at)
        operation, payload = "start", {"task": "Old active task"}
    old = service.execute(operation, turn, request_key="old", **payload)["work"]["work_id"]
    x.clock[0] += timedelta(seconds=1)
    for index in range(21):
        newer = (x.service.turn(str(uuid4()), "New task") if household else
                 replace(turn, message_id=f"new:{index}", occurred_at=x.clock[0].strftime("%Y-%m-%dT%H:%M:%SZ")))
        work_id = service.execute(operation, newer, request_key=f"new:{index}", **payload)["work"]["work_id"]
        service.execute("stop", newer, request_key=f"stop:{index}", work_id=work_id)
    store = x.server.store
    if household:
        listing = lambda **kwargs: store.list_household(**x.service._owner,
            household_origin=x.service.origin(turn), now=x.clock[0], **kwargs)
    else:
        listing = lambda **kwargs: store.list_owned(**service._owner, **kwargs)
    assert old not in [work.spec.work_id for work in listing(limit=20)]
    selected = listing(limit=20, active_first=True)
    assert selected[0].spec.work_id == old
    assert len(selected) == 20
    assert [work.spec.work_id for work in listing(limit=1)] == [listing(limit=20)[0].spec.work_id]
    assert x.research.calls == 0


@pytest.mark.parametrize("household", [False, True])
def test_active_first_requires_explicit_boolean(delegated_engine, household):
    x = setup(delegated_engine, "ordering-flag-" + str(household))
    with pytest.raises(ValueError, match="boolean"):
        if household:
            x.server.store.list_household(**x.service._owner, household_origin=x.service.origin(x.turn),
                                         now=x.clock[0], active_first="yes")
        else:
            x.server.store.list_owned(**x.service._owner, active_first="yes")
