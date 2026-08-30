from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.eva2_canary_cutover import EVA1_PATHS, canonical_dropin
from tools.eva2_quiescent_cutover import (
    _dump_content_digest,
    _shared_database_consumers,
    DatabaseAttestation,
    DatabaseSnapshot,
    QuiescentCutoverError,
    SlotSpec,
    SystemBackend,
    _slot,
    quiescent_cutover,
)


def test_start_writers_recreates_services_and_checks_exact_core_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = _slot(3)
    commands: list[list[str]] = []
    image = "sha256:" + "a" * 64
    monkeypatch.setattr(
        SystemBackend,
        "_environment",
        staticmethod(lambda _path: {"EVA_LIVING_CORE_IMAGE": f"eva2/core@{image}"}),
    )
    monkeypatch.setattr(
        SystemBackend,
        "_run",
        staticmethod(lambda command, **_kwargs: commands.append(command) or ""),
    )
    monkeypatch.setattr(
        SystemBackend, "_wait_container", staticmethod(lambda _container: None)
    )
    monkeypatch.setattr(
        SystemBackend, "_inspect", staticmethod(lambda _container: {"Image": image})
    )

    SystemBackend().start_writers(slot)

    assert [command[-1] for command in commands] == ["hindsight", "core"]
    assert all("up" in command and "--no-deps" in command for command in commands)


def test_start_writers_refuses_a_stale_core_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = _slot(3)
    monkeypatch.setattr(
        SystemBackend,
        "_environment",
        staticmethod(
            lambda _path: {"EVA_LIVING_CORE_IMAGE": "eva2/core@sha256:" + "a" * 64}
        ),
    )
    monkeypatch.setattr(
        SystemBackend, "_run", staticmethod(lambda _command, **_kwargs: "")
    )
    monkeypatch.setattr(
        SystemBackend, "_wait_container", staticmethod(lambda _container: None)
    )
    monkeypatch.setattr(
        SystemBackend,
        "_inspect",
        staticmethod(lambda _container: {"Image": "sha256:" + "b" * 64}),
    )

    with pytest.raises(QuiescentCutoverError, match="core image differs"):
        SystemBackend().start_writers(slot)


def _container(
    name: str,
    *,
    source: str,
    running: bool = True,
    destination: str = "/run/secrets/database_dsn",
) -> dict[str, Any]:
    return {
        "Mounts": [{"Destination": destination, "Source": source}],
        "Name": f"/{name}",
        "State": {"Running": running},
    }


def test_shared_database_consumers_find_only_running_exact_mount_siblings() -> None:
    inspected = [
        _container("retained-core", source="/private/slot3-dsn"),
        _container("old-probe", source="/private/slot3-dsn"),
        _container("stopped-probe", source="/private/slot3-dsn", running=False),
        _container("other-slot", source="/private/slot4-dsn"),
        _container(
            "different-destination",
            source="/private/slot3-dsn",
            destination="/run/secrets/another_file",
        ),
    ]

    assert _shared_database_consumers(
        retained_core="retained-core",
        inspected=inspected,
    ) == ["old-probe"]


def test_shared_database_consumers_require_exact_retained_core_mount() -> None:
    with pytest.raises(QuiescentCutoverError, match="inspection is missing"):
        _shared_database_consumers(retained_core="missing", inspected=[])
    with pytest.raises(QuiescentCutoverError, match="mount is invalid"):
        _shared_database_consumers(
            retained_core="retained-core",
            inspected=[
                _container(
                    "retained-core",
                    source="/private/slot3-dsn",
                    destination="/wrong",
                )
            ],
        )


def _snapshot(tmp_path: Path, name: str, marker: str) -> DatabaseSnapshot:
    operational = tmp_path / f"{name}-operational.dump"
    memory = tmp_path / f"{name}-memory.dump"
    operational.write_bytes(marker.encode())
    memory.write_bytes((marker + "-memory").encode())
    return DatabaseSnapshot(
        operational_dump=operational,
        memory_dump=memory,
        operational_dump_sha256="a" * 64,
        memory_dump_sha256="b" * 64,
        operational_dump_bytes=operational.stat().st_size,
        memory_dump_bytes=memory.stat().st_size,
        operational=DatabaseAttestation(
            counts={"public.message": 4},
            count_digest="c" * 64,
            schema_digest="d" * 64,
            data_digest="e" * 64,
        ),
        memory=DatabaseAttestation(
            counts={"public.memory_units": 2},
            count_digest="f" * 64,
            schema_digest="1" * 64,
            data_digest="2" * 64,
        ),
    )


def test_database_digest_ignores_only_postgres_random_restrict_framing() -> None:
    first = _dump_content_digest(
        [
            b"-- header\n",
            b"\\restrict alpha\n",
            b"INSERT INTO x VALUES (1);\n",
            b"\\unrestrict alpha\n",
        ]
    )
    second = _dump_content_digest(
        [
            b"-- header\n",
            b"\\restrict beta\n",
            b"INSERT INTO x VALUES (1);\n",
            b"\\unrestrict beta\n",
        ]
    )
    changed = _dump_content_digest(
        [
            b"-- header\n",
            b"\\restrict beta\n",
            b"INSERT INTO x VALUES (2);\n",
            b"\\unrestrict beta\n",
        ]
    )

    assert first == second
    assert first != changed
    assert first == _dump_content_digest(
        [
            b"INSERT INTO x VALUES (1);\n",
            b"\\unrestrict another\n",
            b"-- header\n",
            b"\\restrict another\n",
        ]
    )


class FakeBackend:
    def __init__(self, tmp_path: Path, *, fail_at: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.forward = _snapshot(tmp_path, "forward", "forward")
        self.reverse = _snapshot(tmp_path, "reverse", "reverse")
        self.route = canonical_dropin("http://127.0.0.1:44710")

    def _call(self, value: str) -> None:
        self.calls.append(value)
        if self.fail_at == value:
            raise QuiescentCutoverError(f"failed at {value}")

    def preflight(self, source: SlotSpec, target: SlotSpec, dropin: Path) -> bytes:
        self._call(f"preflight:{source.number}:{target.number}")
        return self.route

    def stop_eva1(self) -> None:
        self._call("stop-eva1")

    def backup_eva1(self, source: SlotSpec, target: SlotSpec) -> dict[str, object]:
        self._call("backup-eva1")
        return {
            "bytes": 99,
            "filename": "eva1.db",
            "quickCheck": "ok",
            "sha256": "3" * 64,
        }

    def stop_writers(self, slot: SlotSpec) -> None:
        self._call(f"stop:{slot.number}")

    def start_writers(self, slot: SlotSpec) -> None:
        self._call(f"start:{slot.number}")

    def snapshot(self, slot: SlotSpec, workspace: Path, label: str) -> DatabaseSnapshot:
        self._call(f"snapshot:{slot.number}:{label}")
        return self.reverse if label == "automatic-rollback" else self.forward

    def restore(self, slot: SlotSpec, snapshot: DatabaseSnapshot) -> None:
        marker = "reverse" if snapshot is self.reverse else "forward"
        self._call(f"restore:{slot.number}:{marker}")

    def verify_runtime(self, slot: SlotSpec) -> None:
        self._call(f"verify:{slot.number}")

    def write_route(self, dropin: Path, content: bytes) -> None:
        port = 44711 if content == canonical_dropin("http://127.0.0.1:44711") else 44710
        self._call(f"route:{port}")
        self.route = content

    def start_eva1(self, slot: SlotSpec) -> dict[str, int]:
        self._call(f"start-eva1:{slot.number}")
        return {path: 200 for path in EVA1_PATHS}


def test_quiesces_copies_attests_and_only_then_switches(tmp_path: Path) -> None:
    backend = FakeBackend(tmp_path)

    receipt = quiescent_cutover(
        source_slot=3,
        target_slot=4,
        backend=backend,
        dropin=tmp_path / "dropin",
        workspace=tmp_path,
    )

    assert backend.calls == [
        "preflight:3:4",
        "stop-eva1",
        "backup-eva1",
        "stop:3",
        "stop:4",
        "snapshot:3:forward",
        "restore:4:forward",
        "start:4",
        "verify:4",
        "route:44711",
        "start-eva1:4",
    ]
    assert receipt["status"] == "switched"
    assert receipt["sourcePort"] == 44710
    assert receipt["targetPort"] == 44711
    assert receipt["sourceWritersStopped"] is True
    assert receipt["eva1Backup"]["quickCheck"] == "ok"  # type: ignore[index]


def test_failure_before_route_restores_source_without_reversing_data(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(tmp_path, fail_at="verify:4")

    with pytest.raises(QuiescentCutoverError, match="failed at verify:4"):
        quiescent_cutover(
            source_slot=3,
            target_slot=4,
            backend=backend,
            dropin=tmp_path / "dropin",
            workspace=tmp_path,
        )

    assert backend.calls[-4:] == ["stop:4", "start:3", "verify:3", "start-eva1:3"]
    assert not any("automatic-rollback" in call for call in backend.calls)
    assert "route:44710" not in backend.calls


def test_partial_target_start_is_stopped_before_source_recovery(tmp_path: Path) -> None:
    backend = FakeBackend(tmp_path, fail_at="start:4")

    with pytest.raises(QuiescentCutoverError, match="failed at start:4"):
        quiescent_cutover(
            source_slot=3,
            target_slot=4,
            backend=backend,
            dropin=tmp_path / "dropin",
            workspace=tmp_path,
        )

    assert backend.calls[-5:] == [
        "start:4",
        "stop:4",
        "start:3",
        "verify:3",
        "start-eva1:3",
    ]


def test_failure_after_eva1_start_attempt_reverses_new_writes_before_rollback(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(tmp_path, fail_at="start-eva1:4")

    with pytest.raises(QuiescentCutoverError, match="failed at start-eva1:4"):
        quiescent_cutover(
            source_slot=3,
            target_slot=4,
            backend=backend,
            dropin=tmp_path / "dropin",
            workspace=tmp_path,
        )

    assert backend.calls[-9:] == [
        "start-eva1:4",
        "stop-eva1",
        "stop:4",
        "snapshot:4:automatic-rollback",
        "restore:3:reverse",
        "start:3",
        "verify:3",
        "route:44710",
        "start-eva1:3",
    ]
    assert backend.route == canonical_dropin("http://127.0.0.1:44710")


def test_route_write_failure_still_rewrites_exact_previous_route(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(tmp_path, fail_at="route:44711")

    with pytest.raises(QuiescentCutoverError, match="failed at route:44711"):
        quiescent_cutover(
            source_slot=3,
            target_slot=4,
            backend=backend,
            dropin=tmp_path / "dropin",
            workspace=tmp_path,
        )

    assert backend.calls[-4:] == ["start:3", "verify:3", "route:44710", "start-eva1:3"]


def test_refuses_same_or_unknown_slots_before_preflight(tmp_path: Path) -> None:
    backend = FakeBackend(tmp_path)
    with pytest.raises(QuiescentCutoverError, match="must differ"):
        quiescent_cutover(
            source_slot=3,
            target_slot=3,
            backend=backend,
            workspace=tmp_path,
        )
    with pytest.raises(QuiescentCutoverError, match="only retained"):
        quiescent_cutover(
            source_slot=2,
            target_slot=4,
            backend=backend,
            workspace=tmp_path,
        )
    assert backend.calls == []
