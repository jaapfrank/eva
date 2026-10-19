"""Offline identity failures; no Docker daemon, providers or microphone needed."""
import copy
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from runtime_identity import capture_containers, capture_loaded_sources, compare, snapshot


@dataclass
class LoadedConfig:
    tts_engine: str = "tts.home_assistant_cloud"
    tts_language: str = "nl-NL"
    ha_token: str = "secret-runtime-token"
    grants: tuple = ("private-device-id",)


@pytest.fixture
def probe(tmp_path, monkeypatch):
    # Import a real temporary module once, then mutate disk/code independently.
    source = tmp_path / "private-person-name.py"
    source.write_text("def render():\n    return 'first'\n")
    spec = importlib.util.spec_from_file_location("identity_test_voice", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    config = tmp_path / "private-secret-config.json"
    config.write_text(json.dumps({"ha_token": "secret-disk-token", "tts_language": "nl-NL"}))
    fixture = tmp_path / "private-household-utterance.wav"
    fixture.write_bytes(b"private fixture contents")
    loaded = LoadedConfig()

    def take():
        return snapshot(module_names=(spec.name,), runtime_config=loaded,
            config_files={"service": config}, fixtures={"input": fixture},
            resolved_settings={"tts_voice": "FennaNeural", "wake_greeting": True})

    return SimpleNamespace(take=take, source=source, module=module, config=config,
                           fixture=fixture, loaded=loaded)


@pytest.fixture
def docker(monkeypatch):
    raw = {"Image": "sha256:" + "a" * 64, "Id": "b" * 64, "RestartCount": 0,
        "State": {"StartedAt": "2026-10-19T11:00:00Z", "Running": True},
        "Config": {"Env": ["TOKEN=secret-docker-token"], "Image": "private-tag"},
        "HostConfig": {"Binds": ["/private-owner/config:/config"]},
        "Mounts": [{"Source": "/private-owner/config"}]}
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps([raw]).encode())

    monkeypatch.setattr(subprocess, "run", run)
    return SimpleNamespace(raw=raw, take=lambda: capture_containers({"speaker": "speaker-test"}),
                           calls=calls)


def result(first, last, docker, container_after=None):
    before = docker.take()
    return compare(first, last, containers_before=before,
                   containers_after=container_after or docker.take())


def test_unchanged_real_loaded_source_binds_without_private_values(probe, docker):
    before, after = probe.take(), probe.take()
    containers = docker.take()
    assert before["valid"] and result(before, after, docker)["status"] == "bound"
    rendered = json.dumps([before, after, containers])
    for secret in ("secret-runtime-token", "secret-disk-token", "secret-docker-token",
                   "private-device-id", "private-person-name", "private-owner", "private-tag",
                   "private fixture contents", "private-household-utterance"):
        assert secret not in rendered
    assert before["identity"]["settings"] == {"tts_engine": "tts.home_assistant_cloud",
        "tts_language": "nl-NL", "tts_voice": "FennaNeural", "wake_greeting": True}
    source = before["identity"]["sources"]["identity_test_voice"]
    assert source["origin"] is None and len(source["loaded_code_sha256"]) == 64
    assert docker.calls[0][0] == ["docker", "inspect", "--type", "container", "speaker-test"]
    assert docker.calls[0][1]["timeout"] == 15


def test_disk_source_change_is_not_mistaken_for_loaded_code_change(probe, docker):
    before = probe.take()
    probe.source.write_text("def render():\n    return 'changed-on-disk-only'\n")
    after = probe.take()
    first = before["identity"]["sources"]["identity_test_voice"]
    last = after["identity"]["sources"]["identity_test_voice"]
    assert first["sha256"] != last["sha256"]
    assert first["loaded_code_sha256"] == last["loaded_code_sha256"]
    assert probe.module.render() == "first"
    assert not result(before, after, docker)["valid"]


def test_resident_method_patch_invalidates_even_with_unchanged_file(probe, docker):
    before = probe.take()
    probe.module.render = lambda: "changed-resident-code"
    after = probe.take()
    first = before["identity"]["sources"]["identity_test_voice"]
    last = after["identity"]["sources"]["identity_test_voice"]
    assert first["sha256"] == last["sha256"]
    assert first["loaded_code_sha256"] != last["loaded_code_sha256"]
    assert not result(before, after, docker)["valid"]


@pytest.mark.parametrize("change", ["disk_config", "loaded_config", "fixture"])
def test_inputs_and_loaded_config_are_independently_pinned(probe, docker, change):
    before = probe.take()
    if change == "disk_config":
        probe.config.write_text('{"ha_token":"changed-token"}')
    elif change == "loaded_config":
        probe.loaded.ha_token = "changed-in-memory-only"
    else:
        probe.fixture.write_bytes(b"different command")
    after = probe.take()
    assert not result(before, after, docker)["valid"]
    if change == "loaded_config":
        assert before["identity"]["config_files"] == after["identity"]["config_files"]
    if change == "disk_config":
        assert before["identity"]["runtime_config_sha256"] == after["identity"]["runtime_config_sha256"]


@pytest.mark.parametrize("change", ["restart", "replace", "image", "env", "stopped"])
def test_container_restart_replacement_and_config_change_invalidate(probe, docker, change):
    first = docker.take()
    if change == "restart":
        docker.raw["RestartCount"] = 1
        docker.raw["State"]["StartedAt"] = "2026-10-19T11:01:00Z"
    elif change == "replace":
        docker.raw["Id"] = "c" * 64
    elif change == "image":
        docker.raw["Image"] = "sha256:" + "d" * 64
    elif change == "env":
        docker.raw["Config"]["Env"] = ["TOKEN=rotated-secret"]
    else:
        docker.raw["State"]["Running"] = False
    outcome = compare(probe.take(), probe.take(), containers_before=first,
                      containers_after=docker.take())
    assert outcome["status"] == "invalid" and not outcome["valid"]
    assert "rotated-secret" not in json.dumps(outcome)


def test_fresh_process_in_same_container_cannot_pass(probe, docker):
    before = probe.take()
    after = copy.deepcopy(before)
    after["identity"]["process"]["start_ticks"] += 1
    assert not result(before, after, docker)["valid"]


@pytest.mark.parametrize("missing", ["sources", "config_files", "fixtures", "runtime_config_sha256"])
def test_missing_required_evidence_never_passes_even_if_both_sides_match(probe, docker, missing):
    manifest = probe.take()
    del manifest["identity"][missing]
    assert not result(manifest, manifest, docker)["valid"]


@pytest.mark.parametrize("section,label,key", [
    ("sources", "identity_test_voice", "loaded_code_sha256"),
    ("config_files", "service", "sha256"),
    ("fixtures", "input", "sha256"),
])
def test_matching_but_missing_nested_digest_is_invalid(probe, docker, section, label, key):
    manifest = probe.take()
    del manifest["identity"][section][label][key]
    assert not result(manifest, manifest, docker)["valid"]


def test_missing_container_or_malformed_matching_container_digest_fail(probe, docker):
    assert not compare(probe.take(), probe.take(), containers_before=None,
                       containers_after=docker.take())["valid"]
    containers = docker.take()
    containers["identity"]["speaker"]["configuration_sha256"] = "not-a-digest"
    assert not compare(probe.take(), probe.take(), containers_before=containers,
                       containers_after=containers)["valid"]


def test_unloaded_module_is_not_imported_to_make_evidence_pass(probe, monkeypatch):
    monkeypatch.delitem(sys.modules, "identity_test_voice")
    captured = capture_loaded_sources(("identity_test_voice",))
    assert captured["sources"] == {} and captured["errors"]
    assert "identity_test_voice" not in sys.modules and not probe.take()["valid"]


def test_private_values_disguised_as_safe_fields_and_failures_do_not_escape(probe, docker, monkeypatch):
    probe.loaded.tts_language = "private spoken phrase"
    assert "private spoken phrase" not in json.dumps(probe.take())
    probe.fixture.unlink()
    assert not probe.take()["valid"]
    assert "private-household-utterance" not in json.dumps(probe.take())

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["secret-command"], output=b"secret-output",
                                            stderr=b"secret-env")

    monkeypatch.setattr(subprocess, "run", fail)
    capture = docker.take()
    assert not capture["valid"]
    assert "secret" not in json.dumps(capture)
