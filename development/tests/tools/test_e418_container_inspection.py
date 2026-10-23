"""Behavioral boundaries for the shared read-only operator inventory."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from tools import container_inspection as subject
from tools.eva2_quiescent_cutover import (
    QuiescentCutoverError, SystemBackend, _shared_database_consumers,
)


def row(name, *, running=False, source="/private/source-dsn", destination="/run/secrets/database_dsn"):
    return {"Name": "/" + name, "State": {"Running": running},
            "Mounts": [{"Source": source, "Destination": destination}]}


def test_exact_running_siblings_only_and_input_is_not_changed():
    documents = [row("core"), row("z-worker", running=True), row("a-worker", running=True),
                 row("stopped"), row("other-database", running=True, source="/other/dsn"),
                 row("other-destination", running=True, destination="/other/dsn")]
    before = json.dumps(documents, sort_keys=True)
    assert subject.shared_database_consumers(retained_core="core", inspected=iter(documents)) == ["a-worker", "z-worker"]
    assert _shared_database_consumers(retained_core="core", inspected=documents) == ["a-worker", "z-worker"]
    assert json.dumps(documents, sort_keys=True) == before


@pytest.mark.parametrize("documents", [[], [{"Name": "/core", "Mounts": []}],
    [{"Name": "/core", "Mounts": [*row("core")["Mounts"], *row("core", source="/different")["Mounts"]]}]])
def test_missing_or_ambiguous_core_mount_keeps_legacy_error(documents):
    with pytest.raises(subject.ContainerInspectionError):
        subject.shared_database_consumers(retained_core="core", inspected=documents)
    with pytest.raises(QuiescentCutoverError):
        _shared_database_consumers(retained_core="core", inspected=documents)


def test_only_ps_then_inspect_commands_and_legacy_runner_is_preserved(monkeypatch):
    calls = []
    def run(command):
        calls.append(command)
        return "core\nworker\n" if len(calls) % 2 else json.dumps([row("core"), row("worker")])
    monkeypatch.setattr(SystemBackend, "_run", run)
    assert SystemBackend._inspect_all() == [row("core"), row("worker")]
    assert calls == [[subject.DOCKER, "ps", "-a", "--format", "{{.Names}}"],
                     [subject.DOCKER, "inspect", "core", "worker"]]


def test_no_containers_needs_no_second_command():
    calls = []
    assert subject.inspect_all(run=lambda command: calls.append(command) or "") == []
    assert len(calls) == 1


@pytest.mark.parametrize("payload", ["{bad", "{}", "[null]", '"text"'])
def test_bad_inventory_is_not_an_empty_success(payload, monkeypatch):
    def run(command):
        return "core\n" if command[1] == "ps" else payload
    with pytest.raises(subject.ContainerInspectionError, match="Docker inspection was invalid"):
        subject.inspect_all(run=run)
    monkeypatch.setattr(SystemBackend, "_run", run)
    with pytest.raises(QuiescentCutoverError, match="Docker inspection was invalid"):
        SystemBackend._inspect_all()


def test_default_runner_uses_fixed_read_only_commands_and_current_environment(monkeypatch):
    calls = []
    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(stdout="core\n" if command[1] == "ps" else "[]")
    monkeypatch.setattr(subject.subprocess, "run", run)
    monkeypatch.setenv("E418_NOT_FORWARDED", "private")
    assert subject.inspect_all() == []
    assert [c[0][1] for c in calls] == ["ps", "inspect"]
    for _, options in calls:
        assert options["check"] is True and options["timeout"] == 180
        assert set(options["env"]) == {"HOME", "LC_ALL", "PATH"}
        assert options["env"]["LC_ALL"] == "C"


def test_failed_command_is_not_retried_and_credentials_are_redacted(monkeypatch):
    calls = []
    secrets = ["postgresql://person:private@host/db", "sk-abcdefghijklmnop", "123456789:abcdefghijklmnopqrstuvw"]
    def run(command, **options):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, output=" ".join(secrets), stderr="unavailable")
    monkeypatch.setattr(subject.subprocess, "run", run)
    with pytest.raises(subject.ContainerInspectionError) as captured:
        subject.inspect_all()
    assert len(calls) == 1
    assert all(secret not in str(captured.value) for secret in secrets)
    assert "unavailable" in str(captured.value)


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.TimeoutExpired("docker", 180)])
def test_unavailable_docker_fails_once(monkeypatch, error):
    calls = []
    def run(command, **options):
        calls.append(command)
        raise error
    monkeypatch.setattr(subject.subprocess, "run", run)
    with pytest.raises(subject.ContainerInspectionError):
        subject.inspect_all()
    assert len(calls) == 1
