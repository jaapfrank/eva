from __future__ import annotations

import pytest

from eva.application.people_memory_overview import PeopleMemoryOverview


def test_people_memory_overview_has_one_closed_non_identifying_wire_shape() -> None:
    overview = PeopleMemoryOverview(
        dynamic_account="no_extra_account",
        group_sharing="private_only",
        memory_flow="up_to_date",
        hindsight="ready",
    )

    assert overview.to_payload() == {
        "contains_raw_content": False,
        "contract": "EvaPeopleMemoryOverview/v1",
        "control_plane": {"state": "owner_viewer_candidate"},
        "hindsight": {"state": "ready"},
        "memory": {"state": "up_to_date"},
        "people": {"dynamic_account": "no_extra_account"},
        "read_only": True,
        "sharing": {"state": "private_only"},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("dynamic_account", "linked:123"),
        ("group_sharing", "person:jaap"),
        ("memory_flow", "secret memory text"),
        ("hindsight", "http://hindsight:8888"),
        ("control_plane", "direct_write_authority"),
    ),
)
def test_people_memory_overview_refuses_open_or_identifying_states(
    field: str, value: str
) -> None:
    values = {
        "dynamic_account": "no_extra_account",
        "group_sharing": "private_only",
        "memory_flow": "no_signals",
        "hindsight": "unknown",
        "control_plane": "owner_viewer_candidate",
    }
    values[field] = value

    with pytest.raises(ValueError, match="overview state is invalid"):
        PeopleMemoryOverview(**values)  # type: ignore[arg-type]
