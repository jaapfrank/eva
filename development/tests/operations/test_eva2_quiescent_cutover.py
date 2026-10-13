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
    BACKGROUND_FLAG,
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
        SystemBackend, "_inspect", staticmethod(lambda _container: {"Image": image, "Config": {"Env": [BACKGROUND_FLAG + "=false"]}})
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
    monkeypatch.setattr(SystemBackend, "_is_running", staticmethod(lambda _: False))
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
        "Config": {"Env": [BACKGROUND_FLAG + "=false"]}, "Mounts": [],
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
    monkeypatch.setattr(SystemBackend, "_is_running", staticmethod(lambda _: False))
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
            "Config": {"Env": ([f"EVA_TELEGRAM_BOT_TOKEN_FILE={PROACTIVE_TOKEN_TARGET}"] if activated else []) + [BACKGROUND_FLAG + "=" + str(activated).lower()]},
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


def test_connector_free_activation_still_requires_selected_route(monkeypatch, tmp_path):
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {}))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(lambda *a, **k: pytest.fail("invalid route must not activate")))
    with pytest.raises((RuntimeError, OSError)):
        SystemBackend().activate_delivery(_slot(3), tmp_path / "missing-route")


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


@pytest.mark.parametrize("telegram", [False, True])
@pytest.mark.parametrize("failure", [None, "staging_leak", "writable", "network", "url"])
def test_connector_access_only_appears_at_activation(monkeypatch, tmp_path, telegram, failure):
    from tools.eva2_quiescent_cutover import CONNECTOR_MANIFEST, CONNECTOR_TARGET
    slot = _slot(3)
    image = "sha256:" + "a" * 64
    settings = {"EVA_MCP_GATEWAY_URL": "http://gateway:4444",
                "EVA_MCP_GATEWAY_ACCESS_DIR_HOST": "/private/connector",
                "EVA_MCP_GATEWAY_NETWORK_NAME": "eva2-connectors",
                "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true",
                "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST": "b" * 64}
    token = tmp_path / "telegram-token" if telegram else None
    commands = []
    active = False
    monkeypatch.setattr(SystemBackend, "_connector_settings", classmethod(lambda cls, env: settings))
    monkeypatch.setattr(SystemBackend, "_proactive_token", staticmethod(lambda env: token))
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {"EVA_LIVING_CORE_IMAGE": "eva2/core@" + image}))
    def inspect(_):
        row = {"Image": image, "Config": {"Env": []}, "Mounts": [], "NetworkSettings": {"Networks": {}}}
        if active or failure == "staging_leak":
            env = {k: settings[k] for k in ("EVA_MCP_GATEWAY_URL", "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP", "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST")}
            env.update(EVA_MCP_GATEWAY_TOKEN_FILE=CONNECTOR_TARGET + "/token", EVA_MCP_GATEWAY_CATALOG_FILE=CONNECTOR_TARGET + "/catalog.json")
            if failure == "url":
                env["EVA_MCP_GATEWAY_URL"] = "http://other:4444"
            row["Config"]["Env"] = [k + "=" + v for k, v in env.items()]
            row["Mounts"] = [{"Type": "bind", "Source": settings["EVA_MCP_GATEWAY_ACCESS_DIR_HOST"], "Destination": CONNECTOR_TARGET, "RW": failure == "writable"}]
            if failure != "network":
                row["NetworkSettings"]["Networks"]["eva2-connectors"] = {}
        if active and telegram:
            row["Config"]["Env"].append("EVA_TELEGRAM_BOT_TOKEN_FILE=" + PROACTIVE_TOKEN_TARGET)
            row["Mounts"].append({"Type": "bind", "Source": str(token), "Destination": PROACTIVE_TOKEN_TARGET, "RW": False})
        row["Config"]["Env"].append(BACKGROUND_FLAG + "=" + str(active).lower())
        return row
    def run(command, **kwargs):
        nonlocal active
        commands.append(command)
        if "--property=Environment" in command:
            return "EVA2_CANARY_URL=" + slot.url
        if "--property=ActiveState" in command:
            return "inactive"
        if "up" in command and command[-1] == "core":
            active = str(CONNECTOR_MANIFEST) in command
            assert (str(PROACTIVE_MANIFEST) in command) is telegram if active else True
        return ""
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(inspect))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    monkeypatch.setattr(SystemBackend, "_wait_container", staticmethod(lambda _: None))
    monkeypatch.setattr(SystemBackend, "_ready", staticmethod(lambda _: True))
    monkeypatch.setattr(SystemBackend, "verify_runtime", lambda *a: None)
    backend = SystemBackend(owner_uid=os.getuid())
    if failure == "staging_leak":
        with pytest.raises(QuiescentCutoverError, match="connector access"):
            backend.start_writers(slot)
        assert not active
        return
    backend.start_writers(slot)
    assert not active and not any(str(CONNECTOR_MANIFEST) in command for command in commands)
    dropin = tmp_path / "route"
    dropin.write_bytes(canonical_dropin(slot.url))
    dropin.chmod(0o644)
    if failure:
        with pytest.raises(QuiescentCutoverError, match="connector"):
            backend.activate_delivery(slot, dropin)
    else:
        # Preserve the existing return meaning: Telegram delivery, not MCP presence.
        assert backend.activate_delivery(slot, dropin) is telegram
    assert active


@pytest.mark.parametrize("failure", [None, "disabled_config", "flag", "overlay", "catalog", "token", "public_network", "extra_file", "external_host", "wrong_image", "wrong_network"])
def test_connector_configuration_is_sealed_before_activation(monkeypatch, tmp_path, failure):
    import hashlib
    import json
    from types import SimpleNamespace
    from tools.eva2_quiescent_cutover import CONNECTOR_MANIFEST, CONNECTOR_FLAG, MANIFEST
    directory = tmp_path / "access"
    directory.mkdir(mode=0o700)
    (directory / "token").write_text("synthetic-token")
    (directory / "catalog.json").write_text('{"contract":"synthetic"}')
    values = {CONNECTOR_FLAG: "true", "EVA_MCP_GATEWAY_URL": "http://eva2-mcp-gateway:4444",
              "EVA_MCP_GATEWAY_IMAGE_ID": "sha256:" + "b" * 64,
              "EVA_MCP_GATEWAY_ACCESS_DIR_HOST": str(directory), "EVA_MCP_GATEWAY_NETWORK_NAME": "eva2-connectors",
              "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true", "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST": "a" * 64,
              "EVA_MCP_GATEWAY_OVERLAY_SHA256": hashlib.sha256(CONNECTOR_MANIFEST.read_bytes()).hexdigest(),
              "EVA_MCP_GATEWAY_BASE_MANIFEST_SHA256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
              "EVA_MCP_GATEWAY_DELIVERY_OVERLAY_SHA256": hashlib.sha256(PROACTIVE_MANIFEST.read_bytes()).hexdigest(),
              "EVA_MCP_GATEWAY_TOKEN_SHA256": hashlib.sha256((directory / "token").read_bytes()).hexdigest(),
              "EVA_MCP_GATEWAY_CATALOG_SHA256": hashlib.sha256((directory / "catalog.json").read_bytes()).hexdigest()}
    real_stat = Path.stat
    def metadata(path, **kwargs):
        original = real_stat(path, **kwargs)
        return SimpleNamespace(st_mode=original.st_mode, st_uid=65532) if path == directory else original
    monkeypatch.setattr(Path, "stat", metadata)
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(lambda *args, **kwargs: None))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["network", "inspect"]:
            return json.dumps([{"Name": "eva2-connectors", "Internal": failure != "public_network", "Id": "network-id", "Containers": {"gateway-id": {}}}])
        assert command[1:] == ["inspect", "eva2-mcp-gateway"]
        return json.dumps([{"Id": "gateway-id", "Name": "/eva2-mcp-gateway", "State": {"Running": True},
                           "Image": "sha256:" + ("c" if failure == "wrong_image" else "b") * 64,
                           "NetworkSettings": {"Networks": {"eva2-connectors": {
                               "NetworkID": "wrong" if failure == "wrong_network" else "network-id", "Aliases": ["eva2-mcp-gateway"]}}}}])
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    if failure == "external_host":
        values["EVA_MCP_GATEWAY_URL"] = "https://external.example"
    if failure == "disabled_config":
        values[CONNECTOR_FLAG] = "false"
    if failure == "flag":
        values[CONNECTOR_FLAG] = "TRUE"
    if failure == "overlay":
        values["EVA_MCP_GATEWAY_OVERLAY_SHA256"] = "0" * 64
    if failure in {"catalog", "token"}:
        (directory / ("catalog.json" if failure == "catalog" else "token")).write_text("changed")
    if failure == "extra_file":
        (directory / "admin-token").write_text("must-not-be-mounted")
    if failure:
        with pytest.raises(QuiescentCutoverError, match="connector"):
            SystemBackend._connector_settings(values)
    else:
        assert SystemBackend._connector_settings(values) is not None
    assert all(command[1:3] == ["network", "inspect"] or command[1:] == ["inspect", "eva2-mcp-gateway"] for command in calls)
    assert bool(calls) is (failure in {None, "public_network", "wrong_image", "wrong_network"})


def test_first_connector_activation_rolls_back_to_disabled_source_without_telegram(tmp_path):
    class ConnectorBackend(FakeBackend):
        def __init__(self):
            super().__init__(tmp_path, fail_at="start-eva1:4")
            self.active_connectors = set()
            self.activations = []

        def stop_writers(self, slot):
            super().stop_writers(slot)
            self.active_connectors.discard(slot.number)

        def start_writers(self, slot):
            super().start_writers(slot)
            assert slot.number not in self.active_connectors

        def activate_delivery(self, slot, dropin):
            self._call(f"activate-delivery:{slot.number}")
            assert self.route == canonical_dropin(slot.url)
            enabled = slot.number == 4
            if enabled:
                self.active_connectors.add(slot.number)
            self.activations.append((slot.number, enabled))
            return False  # MCP-only activation must still trigger lossless rollback.

    backend = ConnectorBackend()
    with pytest.raises(QuiescentCutoverError, match="start-eva1:4"):
        quiescent_cutover(source_slot=3, target_slot=4, backend=backend,
                          dropin=tmp_path / "route", workspace=tmp_path)
    assert backend.activations == [(4, True), (3, False)]
    assert backend.active_connectors == set()
    assert backend.route == canonical_dropin(_slot(3).url)
    assert backend.calls.index("snapshot:4:automatic-rollback") < backend.calls.index("restore:3:reverse") < backend.calls.index("activate-delivery:3")


@pytest.mark.parametrize("household,mutation", [
    (household, mutation) for household in (False, True)
    for mutation in [None, "revoked", "changed", "overlay", "baseline", "extra_file",
                     "directory_mode", "file_mode", "symlink", "disabled_config"]
] + [(True, mutation) for mutation in ("token_changed", "token_missing", "token_mode")])
def test_voice_activation_pins_policy_and_private_directory(monkeypatch, tmp_path, mutation, household):
    import hashlib
    from types import SimpleNamespace
    from tools.eva2_quiescent_cutover import VOICE_MANIFEST, MANIFEST, CONNECTOR_MANIFEST
    directory = tmp_path / "voice"
    directory.mkdir(mode=0o700)
    policy = directory / "current.json"
    policy.write_text('{"synthetic":"policy"}')
    policy.chmod(0o600)
    from tools.eva2_quiescent_cutover import HOUSEHOLD_VOICE_MANIFEST
    prefix = "EVA_HOUSEHOLD_VOICE_" if household else "EVA_DASHBOARD_VOICE_"
    loader = SystemBackend._household_voice_settings if household else SystemBackend._voice_settings
    token = directory / "token"
    if household:
        token.write_text("synthetic-token")
        token.chmod(0o600)
    values = {prefix + "ENABLED": "true", prefix + "POLICY_DIR_HOST": str(directory),
              prefix + "POLICY_SHA256": hashlib.sha256(policy.read_bytes()).hexdigest()}
    if household:
        values[prefix + "TOKEN_SHA256"] = hashlib.sha256(token.read_bytes()).hexdigest()
    for path, key in ((HOUSEHOLD_VOICE_MANIFEST if household else VOICE_MANIFEST, "OVERLAY_SHA256"), (MANIFEST, "BASE_MANIFEST_SHA256"),
                      (PROACTIVE_MANIFEST, "DELIVERY_OVERLAY_SHA256"), (CONNECTOR_MANIFEST, "CONNECTOR_OVERLAY_SHA256")):
        values[prefix + key] = hashlib.sha256(path.read_bytes()).hexdigest()
    original_stat = Path.stat
    def metadata(path, **kwargs):
        actual = original_stat(path, **kwargs)
        return SimpleNamespace(st_mode=actual.st_mode, st_uid=65532) if path == directory else actual
    monkeypatch.setattr(Path, "stat", metadata)
    original_private = SystemBackend._private_file
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(lambda path, mode, uid: original_private(path, mode=mode, uid=os.getuid())))
    if mutation == "revoked":
        policy.unlink()
    if mutation == "changed":
        policy.write_text('{"synthetic":"expanded grant"}')
    if mutation == "overlay":
        values[prefix + "OVERLAY_SHA256"] = "0" * 64
    if mutation == "baseline":
        values[prefix + "BASE_MANIFEST_SHA256"] = "0" * 64
    if mutation == "extra_file":
        (directory / "secret").write_text("must not mount")
    if mutation == "directory_mode":
        directory.chmod(0o755)
    if mutation == "file_mode":
        policy.chmod(0o644)
    if mutation == "symlink":
        policy.unlink()
        policy.symlink_to(tmp_path / "missing")
    if mutation == "disabled_config":
        values[prefix + "ENABLED"] = "false"
    if mutation and mutation.startswith("token_"):
        if mutation == "token_changed":
            token.write_text("changed")
        elif mutation == "token_missing":
            token.unlink()
        else:
            token.chmod(0o644)
    if mutation not in {None, "revoked"}:
        with pytest.raises(QuiescentCutoverError):
            loader(values)
    else:
        assert loader(values) is not None
    assert loader({}) is None


@pytest.mark.parametrize("household", [False, True])
@pytest.mark.parametrize("others", [False, True])
@pytest.mark.parametrize("failure", [None, "staging_leak", "writable", "wrong_path", "policy_race", "environment_race"])
def test_voice_is_withheld_until_managed_activation(monkeypatch, tmp_path, others, failure, household):
    from tools.eva2_quiescent_cutover import VOICE_MANIFEST, CONNECTOR_MANIFEST
    slot = _slot(3)
    image = "sha256:" + "a" * 64
    from tools.eva2_quiescent_cutover import HOUSEHOLD_VOICE_MANIFEST
    manifest = HOUSEHOLD_VOICE_MANIFEST if household else VOICE_MANIFEST
    prefix = "EVA_HOUSEHOLD_VOICE_" if household else "EVA_DASHBOARD_VOICE_"
    target = "/run/eva/household-voice" if household else "/run/eva/voice"
    settings = {prefix + "POLICY_DIR_HOST": "/private/voice"}
    active = False
    commands = []
    def environment(_):
        result = {"EVA_LIVING_CORE_IMAGE": "eva/core@" + image}
        if active and failure == "environment_race":
            result["CHANGED_AFTER_ADMISSION"] = "true"
        return result
    def voice_settings(cls, env):
        if active and failure == "policy_race":
            raise QuiescentCutoverError("voice policy changed after admission")
        return settings
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(environment))
    monkeypatch.setattr(SystemBackend, "_household_voice_settings" if household else "_voice_settings", classmethod(voice_settings))
    monkeypatch.setattr(SystemBackend, "_connector_settings", classmethod(lambda cls, env: {} if others else None))
    monkeypatch.setattr(SystemBackend, "_proactive_token", staticmethod(lambda env: tmp_path / "token" if others else None))
    monkeypatch.setattr(SystemBackend, "_verify_connector", staticmethod(lambda *args: None))
    monkeypatch.setattr(SystemBackend, "_verify_proactive_mount", staticmethod(lambda *args: None))
    def inspect(_):
        result = {"Image": image, "Config": {"Env": []}, "Mounts": []}
        if active or failure == "staging_leak":
            result["Config"]["Env"] = [prefix + "POLICY_FILE=" + target + "/current.json"]
            if household:
                result["Config"]["Env"].append(prefix + "TOKEN_FILE=" + target + "/token")
            result["Mounts"] = [{"Type": "bind", "Source": "/wrong" if failure == "wrong_path" else "/private/voice", "Destination": target, "RW": failure == "writable"}]
        result["Config"]["Env"].append(BACKGROUND_FLAG + "=" + str(active).lower())
        return result
    def run(command, **kwargs):
        nonlocal active
        commands.append(command)
        if "--property=Environment" in command:
            return "EVA2_CANARY_URL=" + slot.url
        if "--property=ActiveState" in command:
            return "inactive"
        if "up" in command and command[-1] == "core":
            active = str(manifest) in command
            if active:
                assert (str(CONNECTOR_MANIFEST) in command) is others
                assert (str(PROACTIVE_MANIFEST) in command) is others
        return ""
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(inspect))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    monkeypatch.setattr(SystemBackend, "_wait_container", staticmethod(lambda _: None))
    monkeypatch.setattr(SystemBackend, "_ready", staticmethod(lambda _: True))
    monkeypatch.setattr(SystemBackend, "verify_runtime", lambda *args: None)
    backend = SystemBackend(owner_uid=os.getuid())
    if failure == "staging_leak":
        with pytest.raises(QuiescentCutoverError, match="voice access"):
            backend.start_writers(slot)
        assert not active
        return
    backend.start_writers(slot)
    assert not active
    dropin = tmp_path / "route"
    dropin.write_bytes(canonical_dropin(slot.url))
    dropin.chmod(0o644)
    if failure:
        with pytest.raises(QuiescentCutoverError, match="voice"):
            backend.activate_delivery(slot, dropin)
    else:
        assert backend.activate_delivery(slot, dropin) is others
    assert active


def test_voice_only_cutover_failure_preserves_new_data_before_disabling_voice(tmp_path):
    class VoiceBackend(FakeBackend):
        def __init__(self):
            super().__init__(tmp_path, fail_at="start-eva1:4")
            self.voice_slots = set()
            self.activations = []

        def stop_writers(self, slot):
            super().stop_writers(slot)
            self.voice_slots.discard(slot.number)

        def start_writers(self, slot):
            super().start_writers(slot)
            assert slot.number not in self.voice_slots

        def activate_delivery(self, slot, dropin):
            self._call(f"activate-delivery:{slot.number}")
            assert self.route == canonical_dropin(slot.url)
            if slot.number == 4:
                self.voice_slots.add(slot.number)
            self.activations.append(slot.number)
            return False  # Voice-only activation still needs reverse data sync.

    backend = VoiceBackend()
    with pytest.raises(QuiescentCutoverError, match="start-eva1:4"):
        quiescent_cutover(source_slot=3, target_slot=4, backend=backend,
                          dropin=tmp_path / "route", workspace=tmp_path)
    assert backend.activations == [4, 3]
    assert backend.voice_slots == set()
    assert backend.route == canonical_dropin(_slot(3).url)
    assert backend.calls.index("snapshot:4:automatic-rollback") < backend.calls.index("restore:3:reverse") < backend.calls.index("activate-delivery:3")


@pytest.mark.parametrize("configured", ["true", "false"])
def test_connector_free_staging_activation_and_reverse_path_force_background_phase(monkeypatch, tmp_path, configured):
    monkeypatch.setattr(SystemBackend, "_is_running", staticmethod(lambda _: False))
    image = "sha256:" + "a" * 64
    slot = _slot(4)
    dropin = tmp_path / "route"
    dropin.write_bytes(canonical_dropin(slot.url))
    dropin.chmod(0o644)
    phases = []
    actual = "false"
    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: {
        "EVA_LIVING_CORE_IMAGE": "eva2/core@" + image, BACKGROUND_FLAG: configured,
    }))
    def run(command, **kwargs):
        nonlocal actual
        if "--property=Environment" in command:
            return "EVA2_CANARY_URL=" + slot.url
        if "--property=ActiveState" in command:
            return "inactive"
        assert "up" in command
        phase = kwargs["extra_env"][BACKGROUND_FLAG]
        if command[-1] == "core":
            actual = phase
            phases.append((slot.number, phase))
        else:
            assert phase == "false"
        return ""
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(lambda _: {
        "Image": image, "Config": {"Env": [BACKGROUND_FLAG + "=" + actual]}, "Mounts": [],
    }))
    monkeypatch.setattr(SystemBackend, "_wait_container", staticmethod(lambda _: None))
    monkeypatch.setattr(SystemBackend, "_ready", staticmethod(lambda _: True))
    monkeypatch.setattr(SystemBackend, "verify_runtime", lambda *a: None)
    backend = SystemBackend(owner_uid=os.getuid())
    backend.start_writers(slot)
    assert backend.activate_delivery(slot, dropin) is False
    # A reverse route also stages first, even with no delivery credentials.
    slot = _slot(3)
    dropin.write_bytes(canonical_dropin(slot.url))
    backend.start_writers(slot)
    assert backend.activate_delivery(slot, dropin) is False
    assert phases == [(4, "false"), (4, "true"), (3, "false"), (3, "true")]


@pytest.mark.parametrize("enabled, values", [(False, []), (False, ["true"]), (True, ["false"]), (False, ["false", "false"])])
def test_background_phase_readback_refuses_missing_wrong_or_duplicate_switch(enabled, values):
    with pytest.raises(QuiescentCutoverError, match="background work setting"):
        SystemBackend._verify_background_setting({"Config": {"Env": [BACKGROUND_FLAG + "=" + v for v in values]}}, enabled=enabled)


@pytest.mark.parametrize("difference", ["disabled", "policy", "revoked"])
def test_preflight_refuses_household_voice_loss_before_any_cutover_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, difference: str,
) -> None:
    source_dir, target_dir = tmp_path / "source", tmp_path / "target"
    for directory in (source_dir, target_dir):
        directory.mkdir()
        (directory / "current.json").write_bytes(b"same-policy")
        (directory / "token").write_bytes(b"same-token")
    prefix = "EVA_HOUSEHOLD_VOICE_"
    source = {prefix + "ENABLED": "true", prefix + "POLICY_DIR_HOST": str(source_dir),
              prefix + "POLICY_SHA256": "a" * 64, prefix + "TOKEN_SHA256": "b" * 64}
    target = {**source, prefix + "POLICY_DIR_HOST": str(target_dir)}
    if difference == "disabled":
        target = {}
    elif difference == "policy":
        target[prefix + "POLICY_SHA256"] = "c" * 64
    else:
        (target_dir / "current.json").unlink()
    dropin = tmp_path / "route"
    dropin.write_bytes(canonical_dropin(_slot(4).url))
    dropin.chmod(0o644)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(SystemBackend, "_verify_slot", lambda self, slot, **kw:
                        source if slot.number == 4 else target)
    commands = []
    monkeypatch.setattr(SystemBackend, "_inspect_all", lambda self: commands.append("inspect") or [])
    monkeypatch.setattr(SystemBackend, "_run", lambda self, *a, **kw: commands.append("run") or "")
    with pytest.raises(QuiescentCutoverError, match="voice continuity"):
        SystemBackend(owner_uid=os.getuid()).preflight(_slot(4), _slot(3), dropin)
    assert commands == []


def test_voice_continuity_allows_distinct_slot_paths_with_same_authority(tmp_path: Path) -> None:
    prefix = "EVA_HOUSEHOLD_VOICE_"
    environments = []
    for name in ("source", "target"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "current.json").write_bytes(b"same-policy")
        (directory / "token").write_bytes(b"same-token")
        environments.append({prefix + "ENABLED": "true", prefix + "POLICY_DIR_HOST": str(directory),
                             prefix + "POLICY_SHA256": "a" * 64, prefix + "TOKEN_SHA256": "b" * 64})
    SystemBackend._verify_voice_continuity(*environments)


@pytest.mark.parametrize("initiative", [False, True])
def test_connector_autonomy_seals_the_two_additional_files_without_extra_mount(monkeypatch, tmp_path, initiative):
    import hashlib
    import json
    from types import SimpleNamespace
    from tools.eva2_quiescent_cutover import CONNECTOR_MANIFEST, CONNECTOR_FLAG, MANIFEST
    guard_path, guard, guard_gateway = _prepared_transport_guard(tmp_path)
    directory = tmp_path / "access-autonomy"
    directory.mkdir(mode=0o700)
    files = {"token": "TOKEN_SHA256", "catalog.json": "CATALOG_SHA256",
             "mandate.json": "AUTONOMY_MANDATE_SHA256", "registration-token": "REGISTRATION_TOKEN_SHA256"}
    if initiative:
        files["initiative-mandate.json"] = "INITIATIVE_MANDATE_SHA256"
    values = {CONNECTOR_FLAG: "true", "EVA_MCP_GATEWAY_URL": "http://eva2-mcp-gateway:4444",
              "EVA_MCP_GATEWAY_IMAGE_ID": guard["upstreamImage"],
              "EVA_MCP_GATEWAY_GUARD_RECEIPT_FILE": str(guard_path),
              "EVA_MCP_GATEWAY_GUARD_RECEIPT_SHA256": hashlib.sha256(guard_path.read_bytes()).hexdigest(),
              "EVA_MCP_GATEWAY_ACCESS_DIR_HOST": str(directory), "EVA_MCP_GATEWAY_NETWORK_NAME": "eva2-connectors",
              "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true", "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST": "a" * 64,
              "EVA_MCP_AUTONOMY_MANDATE_FILE": "/run/eva/connectors/mcp/mandate.json",
              "EVA_MCP_REGISTRATION_TOKEN_FILE": "/run/eva/connectors/mcp/registration-token",
              "EVA_MCP_REGISTRATION_VISIBILITY": "public",
              "EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "true"}
    for name, key in files.items():
        path = directory / name
        path.write_text("synthetic-" + name)
        path.chmod(0o600)
        values["EVA_MCP_GATEWAY_" + key] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path, key in ((CONNECTOR_MANIFEST, "OVERLAY_SHA256"), (MANIFEST, "BASE_MANIFEST_SHA256"),
                      (PROACTIVE_MANIFEST, "DELIVERY_OVERLAY_SHA256")):
        values["EVA_MCP_GATEWAY_" + key] = hashlib.sha256(path.read_bytes()).hexdigest()
    real_stat = Path.stat
    def metadata(path, **kwargs):
        original = real_stat(path, **kwargs)
        return SimpleNamespace(st_mode=original.st_mode, st_uid=65532) if path == directory else original
    monkeypatch.setattr(Path, "stat", metadata)
    original_private = SystemBackend._private_file
    verified_files = []
    def private_file(path, *, mode, uid):
        assert uid in {0, 65532} and mode == 0o600
        if uid == 65532:
            verified_files.append(path.name)
        original_private(path, mode=mode, uid=os.getuid())
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(private_file))
    inspections = []
    def run(command, **kwargs):
        inspections.append(command)
        if command[1:3] == ["network", "inspect"]:
            return json.dumps([{"Name": "eva2-connectors", "Internal": True, "Id": "network-id",
                                "Containers": {"gateway-id": {}}}])
        if command[1:3] == ["exec", "eva2-mcp-gateway"]:
            assert command[3:5] == ["/app/.venv/bin/python", "-c"]
            return json.dumps({row["target"]: row["sha256"] for row in guard["files"]})
        assert command[1:] == ["inspect", "eva2-mcp-gateway"]
        return json.dumps([{**guard_gateway, "Id": "gateway-id", "Name": "/eva2-mcp-gateway", "State": {"Running": True},
                            "Image": guard["upstreamImage"],
                            "NetworkSettings": {"Networks": {"eva2-connectors": {
                                "NetworkID": "network-id", "Aliases": ["eva2-mcp-gateway"]}}}}])
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    assert SystemBackend._connector_settings(values) == {k: v for k, v in values.items() if k != CONNECTOR_FLAG}
    assert set(verified_files) == set(files)
    assert len(inspections) == 3
    for changes in (
        {"EVA_MCP_GATEWAY_GUARD_RECEIPT_FILE": ""},
        {"EVA_MCP_GATEWAY_GUARD_RECEIPT_SHA256": ""},
        {"EVA_MCP_GATEWAY_AUTONOMY_MANDATE_SHA256": ""},
        {"EVA_MCP_GATEWAY_REGISTRATION_TOKEN_SHA256": ""},
        {"EVA_MCP_GATEWAY_AUTONOMY_MANDATE_SHA256": "z" * 64},
        {"EVA_MCP_GATEWAY_REGISTRATION_TOKEN_SHA256": "a" * 64},
        {"EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "false"},
        {"EVA_MCP_REGISTRATION_TOKEN_FILE": "/other/token"},
        {"EVA_MCP_AUTONOMY_MANDATE_FILE": ""},
        {"EVA_MCP_REGISTRATION_TOKEN": "synthetic-inline-secret"},
        {CONNECTOR_FLAG: "false"},
    ):
        with pytest.raises(QuiescentCutoverError, match="connector"):
            SystemBackend._connector_settings({**values, **changes})
    for name in ("mandate.json", "registration-token", *(("initiative-mandate.json",) if initiative else ())):
        path = directory / name
        content = path.read_bytes()
        path.write_text("changed-after-sealing")
        with pytest.raises(QuiescentCutoverError, match="digest differs"):
            SystemBackend._connector_settings(values)
        path.write_bytes(content)
        path.chmod(0o644)
        with pytest.raises(QuiescentCutoverError, match="metadata is unsafe"):
            SystemBackend._connector_settings(values)
        path.chmod(0o600)
    disabled = {**values, **dict.fromkeys(("EVA_MCP_AUTONOMY_MANDATE_FILE",
        "EVA_MCP_REGISTRATION_TOKEN_FILE", "EVA_MCP_REGISTRATION_VISIBILITY",
        "EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY"), "")}
    with pytest.raises(QuiescentCutoverError, match="digest pair"):
        SystemBackend._connector_settings(disabled)
    for key in ("EVA_MCP_GATEWAY_AUTONOMY_MANDATE_SHA256", "EVA_MCP_GATEWAY_REGISTRATION_TOKEN_SHA256",
                "EVA_MCP_GATEWAY_GUARD_RECEIPT_FILE", "EVA_MCP_GATEWAY_GUARD_RECEIPT_SHA256"):
        disabled.pop(key)
    disabled.pop("EVA_MCP_GATEWAY_INITIATIVE_MANDATE_SHA256", None)
    with pytest.raises(QuiescentCutoverError, match="unexpected files"):
        SystemBackend._connector_settings(disabled)
    assert len(inspections) == 3  # all altered configurations failed before Docker inspection
    for name in ("mandate.json", "registration-token", *(("initiative-mandate.json",) if initiative else ())):
        (directory / name).unlink()
    assert SystemBackend._connector_settings(disabled) is not None

    # The installed service and owner controls share the same private directory.
    # Every optional file and environment group is pinned before Docker inspection.
    values.update({
        "EVA_MCP_MANAGED_ENDPOINTS_FILE": "/run/eva/connectors/mcp/managed-endpoints.json",
        "EVA_BROWSER_CONTROL_URL": "http://172.30.0.1:45163",
        "EVA_BROWSER_CONTROL_TOKEN_FILE": "/run/eva/connectors/mcp/browser-control-token",
        "EVA_BROWSER_OWNER_ORIGIN": "http://localhost:44710",
    })
    for name, key in {**{name: "EVA_MCP_GATEWAY_" + key for name, key in files.items()},
                      "managed-endpoints.json": "EVA_MCP_GATEWAY_MANAGED_ENDPOINTS_SHA256",
                      "browser-control-token": "EVA_BROWSER_CONTROL_TOKEN_SHA256"}.items():
        path = directory / name
        path.write_text("synthetic-" + name)
        path.chmod(0o600)
        values[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    sealed = SystemBackend._connector_settings(values)
    assert sealed["EVA_BROWSER_CONTROL_URL"] == values["EVA_BROWSER_CONTROL_URL"]
    assert {"managed-endpoints.json", "browser-control-token"} <= set(verified_files)
    before_invalid = len(inspections)
    for changes in (
        {"EVA_MCP_MANAGED_ENDPOINTS_FILE": ""},
        {"EVA_MCP_GATEWAY_MANAGED_ENDPOINTS_SHA256": ""},
        {"EVA_MCP_GATEWAY_MANAGED_ENDPOINTS_SHA256": "a" * 64},
        {"EVA_BROWSER_CONTROL_TOKEN_SHA256": ""},
        {"EVA_BROWSER_CONTROL_TOKEN_SHA256": "a" * 64},
        {"EVA_BROWSER_CONTROL_TOKEN_FILE": "/other/token"},
        {"EVA_BROWSER_OWNER_ORIGIN": ""},
        {"EVA_BROWSER_CONTROL_URL": "http://host/control"},
    ):
        with pytest.raises(QuiescentCutoverError):
            SystemBackend._connector_settings({**values, **changes})
    assert len(inspections) == before_invalid


def _prepared_transport_guard(tmp_path):
    import hashlib
    import importlib.util
    import json
    source = Path(__file__).resolve().parents[3] / "deploy/mcp-autonomy/prepare.py"
    spec = importlib.util.spec_from_file_location("guard_preparation_test", source)
    preparation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preparation)
    directory = tmp_path / "transport-guard"
    directory.mkdir(mode=0o700)
    code = directory / "code"
    hashes, mounts = {}, []
    for relative in (*preparation.INPUTS, "mcpgateway/eva_mcp_pinning.py"):
        path = code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sealed fixture source " + relative)
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        mounts.append({"type": "bind", "source": str(path), "target": "/app/" + relative,
                       "read_only": True, "bind": {"create_host_path": False}})
    policy = json.dumps({"owner_networks": ["10.0.20.0/24"], "blocked_addresses": ["10.0.20.46"],
                         "filesystem_origin": "http://filesystem:9000", "filesystem_address": "172.30.0.3"})
    overlay = directory / "pinning.compose.json"
    overlay.write_text(json.dumps({"services": {"gateway": {
        "environment": {"EVA_MCP_PINNING_POLICY": policy}, "volumes": mounts}}}))
    base, egress, environment = (directory / name for name in ("base.yml", "egress.yml", "provider.env"))
    base.write_text("services: {gateway: {}}")
    egress.write_text("networks: {egress: {}}")
    environment.write_text("SYNTHETIC=fixture")
    receipt_path = directory / "guard-receipt.json"
    receipt = preparation.seal_guard(overlay, base_compose=base, egress_compose=egress,
        environment_file=environment, receipt_path=receipt_path, effective_file_sha256=hashes)
    assert receipt["recreateCommand"] == ["/usr/bin/docker", "compose", "--project-name", "eva2-workspace-provider",
        "--env-file", str(environment), "--file", str(base), "--file", str(egress),
        "--file", str(overlay), "up", "--detach", "--no-deps", "gateway"]
    assert receipt["rollback"]["requiresAutonomyDisabled"] is True
    assert str(overlay) not in receipt["rollback"]["command"]
    return receipt_path, receipt, {"Image": receipt["upstreamImage"],
        "Config": {"Env": ["EVA_MCP_PINNING_POLICY=" + policy]},
        "Mounts": [{"Type": "bind", "Source": item["source"], "Destination": item["target"], "RW": False}
                   for item in mounts]}


def test_connector_guard_refuses_recreated_gateway_or_changed_policy_and_source(monkeypatch, tmp_path):
    import copy
    import hashlib
    import json
    path, receipt, inspected = _prepared_transport_guard(tmp_path)
    settings = {"EVA_MCP_GATEWAY_IMAGE_ID": receipt["upstreamImage"],
                "EVA_MCP_GATEWAY_GUARD_RECEIPT_FILE": str(path),
                "EVA_MCP_GATEWAY_GUARD_RECEIPT_SHA256": hashlib.sha256(path.read_bytes()).hexdigest()}
    original_private = SystemBackend._private_file
    monkeypatch.setattr(SystemBackend, "_private_file", staticmethod(
        lambda path, *, mode, uid: original_private(path, mode=mode, uid=os.getuid())))
    calls, changed = [], False
    def run(command, **kwargs):
        calls.append(command)
        assert command[1:5] == ["exec", "eva2-mcp-gateway", "/app/.venv/bin/python", "-c"]
        assert kwargs["timeout"] == 15
        return json.dumps({row["target"]: ("a" * 64 if changed else row["sha256"]) for row in receipt["files"]})
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    SystemBackend._verify_connector_guard(inspected, settings)
    assert len(calls) == 1
    for mutation in ("missing_guard", "missing_policy", "different_policy", "writable", "different_image"):
        gateway = copy.deepcopy(inspected)
        if mutation == "missing_guard":
            gateway["Mounts"] = []
        elif mutation == "missing_policy":
            gateway["Config"]["Env"] = []
        elif mutation == "different_policy":
            gateway["Config"]["Env"] = ['EVA_MCP_PINNING_POLICY={"broader":true}']
        elif mutation == "writable":
            gateway["Mounts"][0]["RW"] = True
        else:
            gateway["Image"] = "sha256:" + "a" * 64
        with pytest.raises(QuiescentCutoverError, match="transport guard"):
            SystemBackend._verify_connector_guard(gateway, settings)
    assert len(calls) == 1  # denied by inspection before reading installed files
    changed = True
    with pytest.raises(QuiescentCutoverError, match="transport guard"):
        SystemBackend._verify_connector_guard(inspected, settings)
    changed = False
    original = Path(receipt["files"][0]["source"])
    content = original.read_bytes()
    original.write_text("unexpected source edit")
    with pytest.raises(QuiescentCutoverError, match="transport guard"):
        SystemBackend._verify_connector_guard(inspected, settings)
    original.write_bytes(content)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(QuiescentCutoverError, match="transport guard"):
        SystemBackend._verify_connector_guard(inspected, settings)


@pytest.mark.parametrize("image_prefix", ["", "eva2/core@"])
def test_delegated_worker_quiesces_stages_disabled_and_activates_with_health(monkeypatch, tmp_path, image_prefix):
    from tools.eva2_quiescent_cutover import DELEGATED_FLAG

    slot = _slot(3)
    image = "sha256:" + "a" * 64
    environment = {
        DELEGATED_FLAG: "true", "EVA_LIVING_CORE_IMAGE": image_prefix + image,
        "EVA_DELEGATED_WORK_IMAGE": image_prefix + image,
        "EVA_RUNTIME_PRINCIPAL_ID": "owner", "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": "binding",
        "EVA_DELEGATED_WORK_OWNER_PRINCIPAL_ID": "owner", "EVA_DELEGATED_WORK_OWNER_BINDING_ID": "binding",
        "EVA_DATABASE_DSN_FILE_HOST": "/private/database-dsn",
        "EVA_MODEL_ACCESS_LEASE_DIR": "/private/model-access-lease",
    }
    running = {slot.core, slot.delegated_worker, slot.hindsight}
    calls = []
    active = False
    healthy = True

    def inspect(container):
        if container == slot.delegated_worker:
            return {"Image": image, "Config": {
                "User": "65532:65532", "Entrypoint": ["/usr/local/bin/python"],
                "Cmd": ["-m", "eva.bootstrap.delegated_work_service"],
                "Labels": {"com.docker.compose.project": slot.project,
                           "com.docker.compose.service": "delegated-work-worker"}},
                "State": {"Running": container in running,
                          "Health": {"Status": "healthy" if healthy else "unhealthy"}},
                "Mounts": [{"Type": "bind", "Source": source, "Destination": target, "RW": False}
                           for source, target in [("/private/database-dsn", "/run/secrets/database_dsn"),
                                                  ("/private/model-access-lease", "/run/eva/model-lease")]]}
        return {"Image": image, "Config": {"Env": [
            BACKGROUND_FLAG + "=" + str(active).lower(),
            DELEGATED_FLAG + "=" + str(active).lower()]}}

    def run(command, **kwargs):
        nonlocal active
        calls.append(command)
        if "stop" in command:
            running.remove(command[-1])
        elif "--property=Environment" in command:
            return "EVA2_CANARY_URL=" + slot.url
        elif "--property=ActiveState" in command:
            return "inactive"
        elif "up" in command:
            enabled = kwargs["extra_env"][DELEGATED_FLAG]
            if command[-1] == "core":
                active = enabled == "true"
                running.add(slot.core)
            elif command[-1] == "delegated-work-worker":
                assert active and enabled == "true"
                assert command[command.index("--profile") + 1] == "delegated-work"
                running.add(slot.delegated_worker)
            else:
                assert enabled == "false"
                running.add(slot.hindsight)
        return ""

    monkeypatch.setattr(SystemBackend, "_environment", staticmethod(lambda _: environment))
    monkeypatch.setattr(SystemBackend, "_is_running", staticmethod(lambda name: name in running))
    monkeypatch.setattr(SystemBackend, "_inspect", staticmethod(inspect))
    monkeypatch.setattr(SystemBackend, "_run", staticmethod(run))
    monkeypatch.setattr(SystemBackend, "_wait_container", staticmethod(lambda _: None))
    monkeypatch.setattr(SystemBackend, "_ready", staticmethod(lambda _: True))
    monkeypatch.setattr(SystemBackend, "verify_runtime", lambda *_: None)
    backend = SystemBackend(owner_uid=os.getuid())
    backend.stop_writers(slot)
    assert running == set()
    assert [command[-1] for command in calls] == [slot.core, slot.delegated_worker, slot.hindsight]
    backend.start_writers(slot)
    assert not active and slot.delegated_worker not in running
    dropin = tmp_path / "route"
    dropin.write_bytes(canonical_dropin(slot.url))
    dropin.chmod(0o644)
    assert backend.activate_delivery(slot, dropin) is False
    assert active and slot.delegated_worker in running
    healthy = False
    with pytest.raises(QuiescentCutoverError, match="health"):
        backend._verify_delegated_worker(slot, environment)
    backend.stop_writers(slot)
    assert not running


@pytest.mark.parametrize("mutation", ["valid", "missing", "revoked", "expired", "future", "prior_source", "household", "disable_existing"])
def test_explicit_first_voice_activation_preserves_existing_authority(tmp_path, mutation):
    from datetime import UTC, datetime, timedelta
    from tests.bootstrap.test_dashboard_voice_bootstrap import setup
    import json
    path, policy, _, _ = setup(tmp_path)
    now = datetime.now(UTC)
    policy.update(effective_at=(now-timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  expires_at=(now+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    prefix = "EVA_DASHBOARD_VOICE_"
    source = {}
    target = {prefix+"ENABLED": "true", prefix+"POLICY_DIR_HOST": str(tmp_path)}
    if mutation == "revoked": policy["state"] = "revoked"
    if mutation == "expired": policy["expires_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if mutation == "future": policy["effective_at"] = (now+timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if mutation == "prior_source": source[prefix+"POLICY_SHA256"] = "a"*64
    if mutation == "household": target["EVA_HOUSEHOLD_VOICE_ENABLED"] = "true"
    if mutation == "disable_existing": source, target = target, source
    grant = tmp_path / "current.json"
    grant.write_text(json.dumps(policy))
    if mutation == "missing": grant.unlink()
    with pytest.raises(QuiescentCutoverError):
        SystemBackend._verify_voice_continuity(source, target)
    if mutation == "valid":
        SystemBackend._verify_voice_continuity(source, target, activate_dashboard_voice=True)
    else:
        with pytest.raises(QuiescentCutoverError):
            SystemBackend._verify_voice_continuity(source, target, activate_dashboard_voice=True)


def test_new_voice_readiness_failure_rolls_back_before_ingress(monkeypatch, tmp_path):
    from dataclasses import replace
    from io import BytesIO
    from urllib import error
    from tools import eva2_quiescent_cutover as module
    token = tmp_path / "owner-token"
    token.write_text("x" * 40)
    checks = []
    class Response(BytesIO):
        status = 200
    def open_request(req, **kwargs):
        checks.append(req.full_url)
        if req.full_url.endswith("/voice-session/ready"):
            raise error.HTTPError(req.full_url, 503, "not ready", {}, None)
        if req.get_header("Authorization") is None:
            raise error.HTTPError(req.full_url, 401, "owner required", {}, None)
        return Response(b"{}")
    monkeypatch.setattr(module.request, "urlopen", open_request)
    monkeypatch.setattr(SystemBackend, "_ready", staticmethod(lambda _: True))
    verifier = SystemBackend(activate_dashboard_voice=True)
    verifier._voice_activation_slot = 4
    class Backend(FakeBackend):
        def verify_runtime(self, slot):
            super().verify_runtime(slot)
            verifier.verify_runtime(replace(slot, dashboard_token=token))
        def activate_delivery(self, slot, dropin):
            result = super().activate_delivery(slot, dropin)
            verifier._verify_new_voice_readiness(replace(slot, dashboard_token=token))
            return result
    backend = Backend(tmp_path)
    with pytest.raises(QuiescentCutoverError, match="new voice channel is not ready"):
        quiescent_cutover(source_slot=3, target_slot=4, backend=backend,
                          dropin=tmp_path/"dropin", workspace=tmp_path)
    assert "start-eva1:4" not in backend.calls
    assert backend.calls[-8:] == ["stop:4", "snapshot:4:automatic-rollback", "restore:3:reverse",
                                 "start:3", "verify:3", "route:44710", "activate-delivery:3", "start-eva1:3"]
    assert sum(url.endswith("/voice-session/ready") for url in checks) == 1


@pytest.mark.parametrize("operation", ["snapshot", "restore"])
@pytest.mark.parametrize("active", ["core", "delegated", "memory", "document"])
def test_database_copy_rechecks_all_writers_before_io(monkeypatch, tmp_path, operation, active):
    slot = _slot(3)
    backend = SystemBackend()
    names = {"core": slot.core, "delegated": slot.delegated_worker,
             "memory": slot.hindsight, "document": "separate-document-worker"}
    monkeypatch.setattr(backend, "_is_running", lambda name: name == names[active])
    monkeypatch.setattr(backend, "_inspect_all", lambda: [
        _container(slot.core, source="/private/source-dsn", running=False),
        _container(names["document"], source="/private/source-dsn", running=active == "document"),
    ])
    monkeypatch.setattr(backend, "_attest", lambda *args: pytest.fail("snapshot read began with a writer running"))
    monkeypatch.setattr(backend, "_recreate_database", lambda *args: pytest.fail("target changed with a writer running"))
    with pytest.raises(QuiescentCutoverError, match="stopped writers|active shared database consumer"):
        if operation == "snapshot":
            backend.snapshot(slot, tmp_path, "test")
        else:
            backend.restore(slot, None)


def test_database_copy_guard_accepts_stopped_shared_workers(monkeypatch):
    slot = _slot(3)
    backend = SystemBackend()
    monkeypatch.setattr(backend, "_is_running", lambda name: False)
    monkeypatch.setattr(backend, "_inspect_all", lambda: [
        _container(slot.core, source="/private/source-dsn", running=False),
        _container("separate-document-worker", source="/private/source-dsn", running=False),
        _container("other-installation-worker", source="/private/other-dsn", running=True),
    ])
    backend._require_stopped_database_writers(slot)
