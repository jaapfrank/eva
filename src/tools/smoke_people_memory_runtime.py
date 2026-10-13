"""Network-free smoke for the real runtime people/memory composition."""

from __future__ import annotations

from datetime import datetime

from eva.application.people_memory_overview import PeopleMemoryOverview
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime


class _Memory:
    available = True

    def health(self) -> None:
        if not self.available:
            raise RuntimeError("private Hindsight smoke detail")


class _Overview:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def snapshot(
        self, *, now: datetime, hindsight_state: str
    ) -> PeopleMemoryOverview:
        assert now.tzinfo is not None
        assert now.microsecond == 0
        self.calls.append(hindsight_state)
        return PeopleMemoryOverview(
            dynamic_account="no_extra_account",
            group_sharing="private_only",
            memory_flow="no_signals",
            hindsight=hindsight_state,  # type: ignore[arg-type]
        )


def main() -> None:
    memory = _Memory()
    overview = _Overview()
    runtime = PostgresHindsightMemoryRuntime(
        object(),  # type: ignore[arg-type]
        memory,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        runtime_tenant_id="tenant:people-memory-smoke",
        runtime_installation_id="installation:people-memory-smoke",
        people_memory_overview=overview,  # type: ignore[arg-type]
    )

    assert runtime.dashboard_people_memory().hindsight == "ready"
    memory.available = False
    assert runtime.dashboard_people_memory().hindsight == "attention"
    assert overview.calls == ["ready", "attention"]
    print("runtime-people-memory-ready-attention-ok")


if __name__ == "__main__":
    main()
