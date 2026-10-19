"""Closed, privacy-safe status contract for Eva's people and memory cockpit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID
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
MemoryFlowDiagnostic = Literal["historical_timeline_attention", "historical_reconciled_attention", "historical_protocol_attention"]


class PeopleMemoryOverviewError(RuntimeError):
    pass


def memory_source_page_request(limit: int, before: str | None) -> tuple[datetime, UUID] | None:
    """A cursor selects a position only; it never grants access to a person."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("memory source page limit is invalid")
    if before is None:
        return None
    if not isinstance(before, str) or len(before) != 57:
        raise ValueError("memory source cursor is invalid")
    try:
        stamp, event_id = before.split("|", 1)
        at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        identity = UUID(event_id)
        if str(identity) != event_id:
            raise ValueError()
        return at, identity
    except ValueError as error:
        raise ValueError("memory source cursor is invalid") from error


@dataclass(frozen=True, slots=True)
class MemorySource:
    source_id: str
    occurred_at: str
    excerpt: str
    truncated: bool
    retention_state: Literal["retained", "pending", "needs_help", "day_context", "dream_processing", "not_selected"]

    raw_state: Literal["available", "compacted"] = "available"

    def __post_init__(self) -> None:
        memory_source_page_request(1, f"{self.occurred_at}|{self.source_id}")
        if (not isinstance(self.excerpt, str) or not self.excerpt
                or len(self.excerpt) > 2048 or type(self.truncated) is not bool
                or self.raw_state not in {"available", "compacted"}
                or (self.raw_state == "compacted" and self.truncated)
                or self.retention_state not in {"retained", "pending", "needs_help", "day_context", "dream_processing", "not_selected"}):
            raise ValueError("memory source is invalid")


@dataclass(frozen=True, slots=True)
class MemorySourcePage:
    sources: tuple[MemorySource, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        memory_source_page_request(50, self.next_cursor)
        if (not isinstance(self.sources, tuple) or len(self.sources) > 50
                or any(not isinstance(item, MemorySource) for item in self.sources)):
            raise ValueError("memory source page is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaOwnerMemorySources/v1",
            "meaning": "Eigen gespreksbronnen aangeboden aan het geheugen; geen overzicht van alle afgeleide herinneringen.",
            "read_only": True,
            "sources": [
                {"source_id": item.source_id, "occurred_at": item.occurred_at,
                 "excerpt": item.excerpt, "truncated": item.truncated,
                 "retention_state": item.retention_state, "raw_state": item.raw_state}
                for item in self.sources
            ],
            "next_cursor": self.next_cursor,
        }


@dataclass(frozen=True, slots=True)
class PeopleMemoryOverview:
    """Only coarse states; never identities, content, IDs, hashes or timestamps."""

    dynamic_account: DynamicAccountState
    group_sharing: GroupSharingState
    memory_flow: MemoryFlowState
    hindsight: HindsightState
    control_plane: ControlPlaneState = "owner_viewer_candidate"
    memory_diagnostic: MemoryFlowDiagnostic | None = None

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
        if (self.memory_diagnostic not in {None, "historical_timeline_attention", "historical_reconciled_attention", "historical_protocol_attention"}
                or (self.memory_diagnostic is not None and self.memory_flow != "unavailable")):
            raise ValueError("people and memory overview diagnostic is invalid")
        if any(value not in choices for value, choices in allowed):
            raise ValueError("people and memory overview state is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "contains_raw_content": False,
            "contract": "EvaPeopleMemoryOverview/v1",
            "control_plane": {"state": self.control_plane},
            "hindsight": {"state": self.hindsight},
            "memory": {"state": self.memory_flow, **({"diagnostic": self.memory_diagnostic,
                "explanation": ({
                    "historical_timeline_attention": "Het verwerkingsoverzicht kan niet volledig worden bevestigd door een tijdvolgordefout in de historie. Dit betekent niet dat geheugenzoeken onbereikbaar is.",
                    "historical_protocol_attention": "Het verwerkingsoverzicht kan niet volledig worden bevestigd doordat de oude proceshistorie stappen in een ongeldige volgorde bevat. Die fout blijft zichtbaar; dit betekent niet dat geheugenzoeken onbereikbaar is.",
                    "historical_reconciled_attention": "Een oud verwerkingsresultaat is later gecontroleerd bevestigd, maar de oorspronkelijke proceshistorie bevat nog een fout. De historie blijft bewaard; dit betekent niet dat geheugenzoeken onbereikbaar is.",
                }[self.memory_diagnostic])}
                if self.memory_diagnostic is not None else {})},
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
    "MemoryFlowDiagnostic",
    "PeopleMemoryOverview",
    "PeopleMemoryOverviewError",
    "PeopleMemoryOverviewPort",
]
