"""Bounded restore start and fresh memory readiness contracts."""
import copy
from pathlib import Path
import pytest
from tools import installation_memory_start as m


@pytest.fixture
def setup(monkeypatch, tmp_path):
    api = {"id": "a"*64, "name": "memory-api", "image": "sha256:"+"b"*64}
    db = {"id": "c"*64, "name": "memory-db", "image": "sha256:"+"d"*64}
    source = {"id": "8"*64, "name": "source-memory-api", "image": "sha256:"+"b"*64}
    external = {"memory": {"container": db, "database": {"name": "memory", "oid": 42, "system_identifier": "123"}, "user": "memory"},
                "gateway": {"target_path": str(tmp_path / "gateway.sqlite")}, "writers": [api, source]}
    config = {"container": api, "network_id": "e"*64, "profile_sha256": "f"*64,
              "source_memory_writers": [source], "model_authority_file": "/private/current-auth.json", "model_auth_path": "/home/hindsight/.codex/auth.json"}
    calls = []
    monkeypatch.setattr(m, "_core_route", lambda *args: None)
    monkeypatch.setattr(m, "read_installation_package", lambda root: {"archive_sha256": "1"*64})
    monkeypatch.setattr(m, "_target", lambda *a, **k: calls.append(("target", k["running"])))
    monkeypatch.setattr(m, "_command", lambda argv, **k: calls.append(tuple(argv[:2])) or
                        ('{"healthy":true,"bank_count":3}' if argv[1] == "exec" else ""))
    return tmp_path, config, external, calls


def test_start_once_then_fresh_verification_never_restarts(setup):
    root, config, external, calls = setup
    args = dict(configuration=config, external_configuration=external, runtime_configuration={}, guard=lambda: calls.append("guard"))
    receipt = m.start_restored_memory_api(root, **args)
    assert receipt["ready"] and receipt["bank_count"] == 3
    assert receipt["upstream_model_verified"] is False
    assert calls.index(("target", False)) < calls.index(("docker", "start"))
    m.verify_restored_memory_api(root, **args, receipt=receipt)
    assert calls.count(("docker", "start")) == 1
    assert calls.count(("docker", "exec")) == 2


def test_failed_started_target_returns_partial_receipt_without_retry(setup, monkeypatch):
    root, config, external, calls = setup
    def fail(*args):
        raise ValueError("private raw server details")
    monkeypatch.setattr(m, "_verify", fail)
    with pytest.raises(m.RestoredMemoryStartError) as error:
        m.start_restored_memory_api(root, configuration=config, external_configuration=external, runtime_configuration={}, guard=lambda: None)
    assert error.value.receipt["start_attempted"] and not error.value.receipt["ready"]
    assert "private raw" not in str(error.value)
    assert calls.count(("docker", "start")) == 1


def test_wrong_archive_receipt_does_not_probe_or_start(setup):
    root, config, external, calls = setup
    with pytest.raises(ValueError, match="receipt binding"):
        m.verify_restored_memory_api(root, configuration=config, external_configuration=external, runtime_configuration={},
                                    guard=lambda: None, receipt={"ready": True})
    assert calls == []


def test_unpinned_writer_rejected_before_start(setup):
    root, config, external, calls = setup
    wrong = copy.deepcopy(config); wrong["container"]["id"] = "9"*64
    with pytest.raises(ValueError, match="external writer"):
        m.start_restored_memory_api(root, configuration=wrong, external_configuration=external, runtime_configuration={}, guard=lambda: None)
    assert calls == []


@pytest.mark.parametrize("fault", ["none", "source_running", "wrong_database", "wrong_user", "wrong_auth", "wrong_network", "wrong_profile"])
def test_target_binding_checks(monkeypatch, tmp_path, fault):
    api = {"id": "a"*64, "name": "target-api", "image": "sha256:"+"b"*64}
    source = {"id": "c"*64, "name": "source-api", "image": "sha256:"+"b"*64}
    db = {"id": "d"*64, "name": "target-db", "image": "sha256:"+"b"*64}
    external = {"memory": {"container": db, "database": {"name": "memory"}, "user": "memory"}, "writers": [source, api]}
    config = {"container": api, "source_memory_writers": [source], "network_id": "e"*64,
              "profile_sha256": "f"*64, "model_authority_file": "/private/owner.json", "model_auth_path": "/home/hindsight/.codex/auth.json"}
    row = {"State": {"Running": False, "Status": "created", "Pid": 0},
           "HostConfig": {"RestartPolicy": {"Name": "no"}, "ReadonlyRootfs": True},
           "Config": {"User": "1000:1000", "Env": ["HINDSIGHT_API_LLM_PROVIDER=openai-codex", "HINDSIGHT_API_DATABASE_URL_FILE=/run/dsn"]},
           "Mounts": [], "NetworkSettings": {"Networks": {"isolated": {"NetworkID": "e"*64}}}}
    database = {"State": {"Running": True}, "NetworkSettings": {"Networks": {"isolated": {"NetworkID": "e"*64, "Aliases": ["target-db"]}}}}
    checked = []
    def pins(value):
        checked.append(value["writers"])
        if fault == "source_running": raise ValueError("source still running")
    monkeypatch.setattr(m, "_pins_current", pins)
    monkeypatch.setattr(m, "_inspect", lambda pin: row if pin == api else database)
    monkeypatch.setattr(m, "_inspect_network", lambda value: None)
    monkeypatch.setattr(m, "runtime_profile_sha256", lambda value: "0"*64 if fault == "wrong_profile" else "f"*64)
    monkeypatch.setattr(m, "_host_file", lambda path, mounts, readonly: (Path("/private/dsn" if path == "/run/dsn" else "/private/auth"), {}))
    def read(path, **kwargs):
        if str(path).endswith("auth"): return b"wrong" if fault == "wrong_auth" else b"auth"
        return ("postgresql://"+("other" if fault == "wrong_user" else "memory")+"@target-db/"+("other" if fault == "wrong_database" else "memory")).encode()
    monkeypatch.setattr(m, "read_private_runtime_file_bytes", read)
    monkeypatch.setattr(m, "build_codex_consumer_auth", lambda path: b"auth")
    if fault == "wrong_network": database["NetworkSettings"]["Networks"]["isolated"]["NetworkID"] = "0"*64
    if fault == "none":
        assert m._target(tmp_path, config, external, running=False) == row
    else:
        with pytest.raises(ValueError): m._target(tmp_path, config, external, running=False)
    assert checked == [[source]]


@pytest.mark.parametrize("url,accepted", [("http://restored-memory:8888", True), ("http://old-memory:8888", False), ("http://restored-memory:8888/other", False)])
def test_core_route_targets_pinned_memory_api(monkeypatch, url, accepted):
    core_pin = {"name": "core", "id": "c"*64, "image": "sha256:"+"d"*64}
    api_pin = {"name": "restored-memory", "id": "a"*64, "image": "sha256:"+"b"*64}
    network = {"Networks": {"isolated": {"NetworkID": "e"*64, "Aliases": ["restored-memory"]}}}
    core = {"Config": {"Env": ["EVA_HINDSIGHT_URL="+url]}, "HostConfig": {}, "NetworkSettings": network}
    api = {"NetworkSettings": network}
    monkeypatch.setattr(m, "_inspect", lambda pin: core if pin == core_pin else api)
    config = {"container": api_pin, "network_id": "e"*64}
    runtime = {"target": {"core_name": "core", "workers": [core_pin]}}
    if accepted:
        m._core_route(config, runtime)
    else:
        with pytest.raises(ValueError): m._core_route(config, runtime)
