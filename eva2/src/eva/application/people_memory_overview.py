"""Closed, privacy-safe status contract for Eva's people and memory cockpit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


DynamicAccountState = Literal[
    "extra_account_linked",
    "no_extra_account",
    "unavailable",
]
GroupSharingState = Literal[
    "private_only",
    "group_active",
    "expires_soon",
    "unavailable",
]
MemoryFlowState = Literal[
    "no_signals",
    "up_to_date",
    "processing",
    "retrying",
    "needs_help",
    "unavailable",
]
HindsightState = Literal["ready", "attention", "unknown"]
ControlPlaneState = Literal["owner_viewer_candidate"]


class PeopleMemoryOverviewError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PeopleMemoryOverview:
    """Only coarse states; never identities, content, IDs, hashes or timestamps."""

    dynamic_account: DynamicAccountState
    group_sharing: GroupSharingState
    memory_flow: MemoryFlowState
    hindsight: HindsightState
    control_plane: ControlPlaneState = "owner_viewer_candidate"

    def __post_init__(self) -> None:
        allowed = (
            (
                self.dynamic_account,
                {"extra_account_linked", "no_extra_account", "unavailable"},
            ),
            (
                self.group_sharing,
                {"private_only", "group_active", "expires_soon", "unavailable"},
            ),
            (
                self.memory_flow,
                {
                    "no_signals",
                    "up_to_date",
                    "processing",
                    "retrying",
                    "needs_help",
                    "unavailable",
                },
            ),
            (self.hindsight, {"ready", "attention", "unknown"}),
            (self.control_plane, {"owner_viewer_candidate"}),
        )
        if any(value not in choices for value, choices in allowed):
            raise ValueError("people and memory overview state is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "contains_raw_content": False,
            "contract": "EvaPeopleMemoryOverview/v1",
            "control_plane": {"state": self.control_plane},
            "hindsight": {"state": self.hindsight},
            "memory": {"state": self.memory_flow},
            "people": {"dynamic_account": self.dynamic_account},
            "read_only": True,
            "sharing": {"state": self.group_sharing},
        }


class PeopleMemoryOverviewPort(Protocol):
    def snapshot(
        self, *, now: datetime, hindsight_state: HindsightState
    ) -> PeopleMemoryOverview: ...


__all__ = [
    "DynamicAccountState",
    "ControlPlaneState",
    "GroupSharingState",
    "HindsightState",
    "MemoryFlowState",
    "PeopleMemoryOverview",
    "PeopleMemoryOverviewError",
    "PeopleMemoryOverviewPort",
]
