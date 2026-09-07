from __future__ import annotations

from pathlib import Path
import os
from typing import Any

import pytest

from tools.eva2_canary_cutover import EVA1_PATHS, canonical_dropin
from tools.eva2_quiescent_cutover import (
    _dump_content_digest,
    _shared_database_consumers,
    DatabaseAttestation,
    DatabaseSnapshot,
    QuiescentCutoverError,
    PROACTIVE_FLAG,
    PROACTIVE_MANIFEST,
    PROACTIVE_TOKEN_TARGET,
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


@pytest.mark.parametrize("value", ["", "yes", "1", "TRUE", " false"])
def test_proactive_flag_rejects_ambiguous_admission(value: str) -> None:
    with pytest.raises(QuiescentCutoverError, match="flag"):
        SystemBackend._proactive_token({PROACTIVE_FLAG: value})


def test_token_file_presence_does_not_enable_proactive_delivery() -> None:
    assert SystemBackend._proactive_token({
        "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": "/not/read/merely/because/present",
    }) is None


@pytest.mark.parametrize("value", ["", "relative/token", "/tmp/../token"])
def test_proactive_token_requires_absolute_canonical_path(value: str) -> None:
    with pytest.raises(QuiescentCutoverError, match="path"):
        SystemBackend._proactive_token({
            PROACTIVE_FLAG: "true", "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": value,
        })


def test_proactive_token_requires_private_runtime_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed = []
    token_file = tmp_path / "telegram-token"
    token_file.write_text("123456:synthetic-test\n")
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(
        lambda path, **options: observed.append((path, options))
    ))
    token = SystemBackend._proactive_token({
        PROACTIVE_FLAG: "true", "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": str(token_file),
    })
    assert observed == [(token, {"mode": 0o600, "uid": 65532})]


@pytest.mark.parametrize("contents", [b"", b"probe-only", b"private-sentinel", b"123:with space", b"123:with\nnewline", b"123:\xff", b"123:" + b"x" * 4093], ids=["empty", "old-probe", "sentinel", "space", "newline", "encoding", "oversize"])
def test_invalid_token_rejected_before_any_cutover_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: bytes,
) -> None:
    token_file = tmp_path / "telegram-token"
    token_file.write_bytes(contents)
    dropin = tmp_path / "dropin"
    dropin.write_bytes(canonical_dropin(_slot(3).url))
    dropin.chmod(0o644)
    commands = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {
        PROACTIVE_FLAG: "true", "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": str(token_file),
    }))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(lambda cmd, **kw: commands.append(cmd)))
    with pytest.raises(QuiescentCutoverError, match="proactive token content") as error:
        SystemBackend(owner_uid=os.getuid()).preflight(_slot(3), _slot(4), dropin)
    assert "private-sentinel" not in str(error.value)
    assert commands == []


def test_start_writers_withholds_token_even_for_enabled_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    commands = []
    image = "sha256:" + "a" * 64
    token = tmp_path / "telegram-token"
    token.write_text("123456:synthetic-test")
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {
        "EVA_LIVING_CORE_IMAGE": f"eva2/core@{image}", PROACTIVE_FLAG: "true",
        "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": str(token),
    }))
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(
        lambda command, **kw: commands.append(command) or ""
    ))
    monkeypatch.setattr(SystemBackend, "_wait_container", staticmethod(lambda _: None))
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(lambda _: {
        "Image": image,
        "Config": {"Env": []}, "Mounts": [],
    }))
    SystemBackend().start_writers(_slot(4))
    assert [command[-1] for command in commands] == ["hindsight", "core"]
    assert str(PROACTIVE_MANIFEST) not in commands[0]
    assert str(PROACTIVE_MANIFEST) not in commands[1]
    assert all("telegram-ingress" not in command for command in commands)


@pytest.mark.parametrize("problem", ["missing", "writable", "other_source", "duplicate", "inline"])
def test_proactive_runtime_mount_fails_closed(problem: str) -> None:
    token = Path("/rt001-test-token")
    document = {
        "Config": {"Env": [f"EVA_TELEGRAM_BOT_TOKEN_FILE={PROACTIVE_TOKEN_TARGET}"]},
        "Mounts": [{"Type": "bind", "Source": str(token),
                    "Destination": PROACTIVE_TOKEN_TARGET, "RW": False}],
    }
    if problem == "missing":
        document["Mounts"] = []
    elif problem == "writable":
        document["Mounts"][0]["RW"] = True
    elif problem == "other_source":
        document["Mounts"][0]["Source"] = "/another-token"
    elif problem == "duplicate":
        document["Mounts"] *= 2
    else:
        document["Config"]["Env"] = ["EVA_TELEGRAM_BOT_TOKEN_FILE=not-a-file"]
    with pytest.raises(QuiescentCutoverError, match="mount differs"):
        SystemBackend._verify_proactive_mount(document, token)


@pytest.mark.parametrize("document", [
    {"Config": {"Env": [f"EVA_TELEGRAM_BOT_TOKEN_FILE={PROACTIVE_TOKEN_TARGET}"]}},
    {"Mounts": [{"Destination": PROACTIVE_TOKEN_TARGET}]},
])
def test_rollback_disabled_slot_cannot_keep_proactive_token(document: dict) -> None:
    with pytest.raises(QuiescentCutoverError, match="disabled proactive"):
        SystemBackend._verify_proactive_mount(document, None)


@pytest.mark.parametrize("failure", [None, "route_file", "loaded_route", "not_ready", "ingress_active", "stale_image", "mount_writable"])
def test_activation_requires_staged_image_route_and_quiescence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None,
) -> None:
    slot = _slot(4)
    token = tmp_path / "telegram-token"
    token.write_text("123456:synthetic-test")
    image = "sha256:" + "a" * 64
    dropin = tmp_path / "dropin"
    dropin.write_bytes(canonical_dropin(slot.url if failure != "route_file" else _slot(3).url))
    dropin.chmod(0o644)
    commands = []
    activated = False
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {
        PROACTIVE_FLAG: "true", "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": str(token),
        "EVA_LIVING_CORE_IMAGE": f"eva2/core@{image}",
    }))
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(lambda *a, **k: None))
    def run(command, **kwargs):
        nonlocal activated
        commands.append(command)
        if "--property=Environment" in command:
            return f"EVA2_CANARY_URL={_slot(3).url if failure == 'loaded_route' else slot.url}"
        if "--property=ActiveState" in command:
            return "active" if failure == "ingress_active" else "inactive"
        assert command[-1] == "core" and str(PROACTIVE_MANIFEST) in command
        activated = True
        return ""
    def inspect(_):
        return {
            "Image": "sha256:" + "b" * 64 if failure == "stale_image" else image,
            "Config": {"Env": [f"EVA_TELEGRAM_BOT_TOKEN_FILE={PROACTIVE_TOKEN_TARGET}"] if activated else []},
            "Mounts": [{"Type": "bind", "Source": str(token),
                        "Destination": PROACTIVE_TOKEN_TARGET, "RW": failure == "mount_writable"}] if activated else [],
        }
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(inspect))
    monkeypatch.setattr(SystemBackend, "_ready", staticmethod(lambda _: failure != "not_ready"))
    monkeypatch.setattr(SystemBackend, "_wait_container", staticmethod(lambda _: None))
    monkeypatch.setattr(SystemBackend, "verify_runtime", lambda *a: None)
    backend = SystemBackend(owner_uid=os.getuid())
    if failure is None:
        assert backend.activate_delivery(slot, dropin) is True
        assert activated
    else:
        with pytest.raises((QuiescentCutoverError, RuntimeError)):
            backend.activate_delivery(slot, dropin)
        assert activated is (failure == "mount_writable")
    assert not any("telegram-ingress" in command for command in commands)


def test_disabled_slot_activation_never_restarts_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {}))
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(lambda _: {"Config": {"Env": []}, "Mounts": []}))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(lambda *a, **k: pytest.fail("must not start or load credentials")))
    assert SystemBackend().activate_delivery(_slot(3), Path("/unused")) is False


def test_migrate_schema_uses_the_target_image_before_writers_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = _slot(3)
    commands: list[tuple[list[str], int | None]] = []
    monkeypatch.setattr(
        SystemBackend, "_is_running", staticmethod(lambda _container: False)
    )
    monkeypatch.setattr(
        SystemBackend,
        "_run",
        staticmethod(
            lambda command, **kwargs: commands.append(
                (command, kwargs.get("timeout"))
            )
            or ""
        ),
    )

    SystemBackend().migrate_schema(_slot(4), slot)

    assert len(commands) == 1
    command, timeout = commands[0]
    assert command[-5:] == [
        "--profile",
        "schema-install",
        "run",
        "--rm",
        "schema-installer",
    ]
    assert str(slot.environment) in command
    assert slot.project in command
    assert timeout == 300


def test_migrate_schema_refuses_running_target_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = _slot(3)
    monkeypatch.setattr(
        SystemBackend,
        "_is_running",
        staticmethod(lambda container: container == slot.hindsight),
    )

    with pytest.raises(QuiescentCutoverError, match="stopped target writers"):
        SystemBackend().migrate_schema(_slot(4), slot)


def test_migrate_schema_uses_source_migration_for_a_safe_release_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _slot(3)
    target = _slot(4)
    source_image = "eva2/new@sha256:" + "a" * 64
    target_image = "eva2/old@sha256:" + "b" * 64
    calls: list[tuple[list[str], dict[str, object]]] = []
    installer_attempts = 0

    monkeypatch.setattr(
        SystemBackend, "_is_running", staticmethod(lambda _container: False)
    )
    monkeypatch.setattr(
        SystemBackend,
        "_environment",
        staticmethod(
            lambda path: {
                "EVA_LIVING_CORE_IMAGE": (
                    source_image if path == source.environment else target_image
                )
            }
        ),
    )

    def fake_run(command: list[str], **kwargs: object) -> str:
        nonlocal installer_attempts
        calls.append((command, kwargs))
        if command[-1] == "schema-installer":
            installer_attempts += 1
            if installer_attempts == 1:
                raise QuiescentCutoverError(
                    "command failed closed: schema-installer: "
                    "database has an unknown Eva schema revision"
                )
            return ""
        if command[:2] == ["/usr/bin/docker", "run"]:
            return (
                "4e_0007_delegated_private\n"
                if source_image in command
                else "4e_0006_memory_outbox_erasure\n"
            )
        if "SELECT version_num FROM public.eva_alembic_version;" in command:
            return "4e_0007_delegated_private\n"
        if "--entrypoint" in command and "schema-installer" in command:
            assert kwargs["extra_env"] == {"EVA_LIVING_CORE_IMAGE": source_image}
            assert command[-1] == "4e_0006_memory_outbox_erasure"
            return ""
        raise AssertionError(command)

    monkeypatch.setattr(SystemBackend, "_run", staticmethod(fake_run))

    SystemBackend().migrate_schema(source, target)

    assert installer_attempts == 2
    assert len(calls) == 6


def test_migrate_schema_never_downgrades_an_unattributed_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _slot(3)
    target = _slot(4)
    source_image = "eva2/new@sha256:" + "a" * 64
    target_image = "eva2/old@sha256:" + "b" * 64

    monkeypatch.setattr(
        SystemBackend, "_is_running", staticmethod(lambda _container: False)
    )
    monkeypatch.setattr(
        SystemBackend,
        "_environment",
        staticmethod(
            lambda path: {
                "EVA_LIVING_CORE_IMAGE": (
                    source_image if path == source.environment else target_image
                )
            }
        ),
    )

    def fake_run(command: list[str], **_kwargs: object) -> str:
        if command[-1] == "schema-installer":
            raise QuiescentCutoverError(
                "command failed closed: schema-installer: "
                "database has an unknown Eva schema revision"
            )
        if command[:2] == ["/usr/bin/docker", "run"]:
            return (
                "4e_0007_delegated_private\n"
                if source_image in command
                else "4e_0006_memory_outbox_erasure\n"
            )
        if "SELECT version_num FROM public.eva_alembic_version;" in command:
            return "future_untrusted_revision\n"
        raise AssertionError(command)

    monkeypatch.setattr(SystemBackend, "_run", staticmethod(fake_run))

    with pytest.raises(QuiescentCutoverError, match="cannot be attributed"):
        SystemBackend().migrate_schema(source, target)


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

    def migrate_schema(self, source: SlotSpec, target: SlotSpec) -> None:
        self._call(f"migrate:{source.number}:{target.number}")

    def verify_runtime(self, slot: SlotSpec) -> None:
        self._call(f"verify:{slot.number}")

    def write_route(self, dropin: Path, content: bytes) -> None:
        port = 44711 if content == canonical_dropin("http://127.0.0.1:44711") else 44710
        self._call(f"route:{port}")
        self.route = content

    def start_eva1(self, slot: SlotSpec) -> dict[str, int]:
        self._call(f"start-eva1:{slot.number}")
        return {path: 200 for path in EVA1_PATHS}

    def activate_delivery(self, slot: SlotSpec, dropin: Path) -> bool:
        self._call(f"activate-delivery:{slot.number}")
        assert self.route == canonical_dropin(slot.url)
        return True


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
        "migrate:3:4",
        "start:4",
        "verify:4",
        "route:44711",
        "activate-delivery:4",
        "start-eva1:4",
    ]
    assert receipt["status"] == "switched"
    assert receipt["sourcePort"] == 44710
    assert receipt["targetPort"] == 44711
    assert receipt["sourceWritersStopped"] is True
    assert receipt["eva1Backup"]["quickCheck"] == "ok"  # type: ignore[index]


def test_failure_before_route_preserves_background_writes_before_source_restart(
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

    assert backend.calls[-7:] == [
        "stop:4", "snapshot:4:automatic-rollback", "restore:3:reverse",
        "start:3", "verify:3", "activate-delivery:3", "start-eva1:3",
    ]
    assert "route:44710" not in backend.calls


def test_schema_migration_failure_never_starts_or_routes_target(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(tmp_path, fail_at="migrate:3:4")

    with pytest.raises(QuiescentCutoverError, match="failed at migrate:3:4"):
        quiescent_cutover(
            source_slot=3,
            target_slot=4,
            backend=backend,
            dropin=tmp_path / "dropin",
            workspace=tmp_path,
        )

    assert "start:4" not in backend.calls
    assert "route:44711" not in backend.calls
    assert backend.calls[-4:] == ["start:3", "verify:3", "activate-delivery:3", "start-eva1:3"]


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

    assert backend.calls[-8:] == [
        "start:4",
        "stop:4",
        "snapshot:4:automatic-rollback",
        "restore:3:reverse",
        "start:3",
        "verify:3",
        "activate-delivery:3",
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

    assert backend.calls[-10:] == [
        "start-eva1:4",
        "stop-eva1",
        "stop:4",
        "snapshot:4:automatic-rollback",
        "restore:3:reverse",
        "start:3",
        "verify:3",
        "route:44710",
        "activate-delivery:3",
        "start-eva1:3",
    ]
    assert backend.route == canonical_dropin("http://127.0.0.1:44710")


def test_activation_failure_preserves_effect_state_before_restoring_source(tmp_path: Path) -> None:
    backend = FakeBackend(tmp_path, fail_at="activate-delivery:4")
    with pytest.raises(QuiescentCutoverError, match="activate-delivery:4"):
        quiescent_cutover(source_slot=3, target_slot=4, backend=backend,
                          dropin=tmp_path / "dropin", workspace=tmp_path)
    assert "start-eva1:4" not in backend.calls
    assert backend.calls[-9:] == [
        "activate-delivery:4", "stop:4", "snapshot:4:automatic-rollback",
        "restore:3:reverse", "start:3", "verify:3", "route:44710",
        "activate-delivery:3", "start-eva1:3",
    ]


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

    assert backend.calls[-5:] == ["start:3", "verify:3", "route:44710", "activate-delivery:3", "start-eva1:3"]


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
