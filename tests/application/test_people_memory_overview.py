from __future__ import annotations

import pytest

from eva.application.people_memory_overview import PeopleMemoryOverview
from eva.application.people_memory_overview import MemorySource, MemorySourcePage, memory_source_page_request


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


@pytest.mark.parametrize("limit,cursor", [(0,None),(51,None),(True,None),(20,"other-person"),(20,"2026-09-07T00:00:00Z|not-a-uuid")])
def test_memory_source_request_is_bounded(limit, cursor):
    with pytest.raises(ValueError):
        memory_source_page_request(limit, cursor)


def test_memory_source_page_is_explicit_about_sources_not_inferences():
    item = MemorySource("00000000-0000-0000-0000-000000000001", "2026-09-07T00:00:00Z", "Eigen bron", False, "retained")
    page = MemorySourcePage((item,)).to_payload()
    assert page["read_only"] is True
    assert page["sources"][0]["excerpt"] == "Eigen bron"
    assert "principal_id" not in page["sources"][0]
    with pytest.raises(ValueError):
        MemorySource(item.source_id, item.occurred_at, "x"*2049, False, "retained")


def test_owner_bridge_alias_requires_unique_private_current_file_binding():
    from uuid import UUID
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings

    binding = UUID("00000000-0000-0000-0000-000000000001")
    row = TelegramPrincipalBinding(101, "person:owner", ("101",), str(binding))
    resolver = TelegramPrincipalBindings((row,))
    assert resolver.private_bridge_principal(principal_id="person:owner", principal_binding_id=binding) == "telegram:101"
    assert resolver.private_bridge_principal(principal_id="person:other", principal_binding_id=binding) is None
    group_only = TelegramPrincipalBindings((TelegramPrincipalBinding(101,"person:owner",("-123",),str(binding)),))
    assert group_only.private_bridge_principal(principal_id="person:owner", principal_binding_id=binding) is None
    ambiguous = TelegramPrincipalBindings((row, TelegramPrincipalBinding(102,"person:owner",("102",),str(binding))))
    with pytest.raises(ValueError, match="ambiguous"):
        ambiguous.private_bridge_principal(principal_id="person:owner", principal_binding_id=binding)
