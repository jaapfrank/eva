"""Docker created containers bind configuration before endpoint allocation."""
import copy
import json
import pytest
from tools import installation_gateway_start as gateway
from tools import installation_memory_start as memory

NETWORK = "e" * 64


def created():
    return {"State": {"Running": False, "Status": "created", "Pid": 0, "Paused": False, "Restarting": False},
            "HostConfig": {"NetworkMode": "isolated"},
            "NetworkSettings": {"Networks": {"isolated": {"NetworkID": "", "EndpointID": "", "Aliases": None}}}}


def test_created_network_is_resolved_without_start(monkeypatch):
    calls = []
    def command(argv):
        calls.append(argv)
        return json.dumps([{"Id": NETWORK, "Name": "isolated", "Internal": True}])
    monkeypatch.setattr(gateway, "_command", command)
    row = created()
    before = copy.deepcopy(row)
    assert gateway._configured_network(row, NETWORK) == row["NetworkSettings"]["Networks"]["isolated"]
    assert row == before
    assert calls == [["docker", "network", "inspect", "isolated"]]


@pytest.mark.parametrize("fault", ["running", "exited", "pid", "paused", "restarting", "mode", "endpoint", "wrong_id", "extra_network"])
def test_unresolved_endpoint_requires_exact_never_started_configuration(monkeypatch, fault):
    row = created()
    if fault == "running": row["State"]["Running"] = True
    elif fault == "exited": row["State"]["Status"] = "exited"
    elif fault == "pid": row["State"]["Pid"] = 123
    elif fault in {"paused", "restarting"}: row["State"][fault.title()] = True
    elif fault == "mode": row["HostConfig"]["NetworkMode"] = "other"
    elif fault == "endpoint": row["NetworkSettings"]["Networks"]["isolated"]["EndpointID"] = "a" * 64
    elif fault == "wrong_id": row["NetworkSettings"]["Networks"]["isolated"]["NetworkID"] = "a" * 64
    else: row["NetworkSettings"]["Networks"]["other"] = {}
    monkeypatch.setattr(gateway, "_command", lambda *args: pytest.fail("invalid network must not resolve"))
    with pytest.raises(ValueError): gateway._configured_network(row, NETWORK)


@pytest.mark.parametrize("change", [{"Id": "f"*64}, {"Name": "other"}, {"Internal": False}])
def test_replaced_or_public_network_is_refused(monkeypatch, change):
    monkeypatch.setattr(gateway, "_command", lambda *args: json.dumps([{"Id": NETWORK, "Name": "isolated", "Internal": True, **change}]))
    with pytest.raises(ValueError): gateway._configured_network(created(), NETWORK)


def test_running_requires_allocated_pinned_network(monkeypatch):
    row = created(); row["State"].update(Running=True, Status="running", Pid=123)
    monkeypatch.setattr(gateway, "_command", lambda *args: pytest.fail("running identity comes from endpoint"))
    with pytest.raises(ValueError): gateway._configured_network(row, NETWORK)
    row["NetworkSettings"]["Networks"]["isolated"]["NetworkID"] = NETWORK
    assert gateway._configured_network(row, NETWORK)["NetworkID"] == NETWORK


def test_stopped_core_routes_to_created_then_running_memory_api(monkeypatch):
    core_pin = {"name": "core"}; api_pin = {"name": "memory"}
    core = created(); core["Config"] = {"Env": ["EVA_HINDSIGHT_URL=http://memory:8888"]}
    api = created()
    monkeypatch.setattr(gateway, "_command", lambda *args: json.dumps([{"Id": NETWORK, "Name": "isolated", "Internal": True}]))
    monkeypatch.setattr(memory, "_inspect", lambda pin: core if pin == core_pin else api)
    configuration = {"container": api_pin, "network_id": NETWORK}
    runtime = {"target": {"core_name": "core", "workers": [core_pin]}}
    memory._core_route(configuration, runtime)
    api["State"].update(Running=True, Status="running", Pid=10)
    api["NetworkSettings"]["Networks"]["isolated"]["NetworkID"] = NETWORK
    memory._core_route(configuration, runtime)
    api["NetworkSettings"]["Networks"]["isolated"]["NetworkID"] = ""
    with pytest.raises(ValueError): memory._core_route(configuration, runtime)


EGRESS = "d" * 64


def gateway_with_egress():
    row = created()
    row["State"].update(Running=True, Status="running", Pid=123)
    row["NetworkSettings"]["Networks"] = {
        "isolated": {"NetworkID": NETWORK}, "existing-egress": {"NetworkID": EGRESS}}
    return row


def test_gateway_allows_explicit_existing_egress_without_widening_memory_contract(monkeypatch):
    row = gateway_with_egress()
    monkeypatch.setattr(gateway, "_command", lambda args: json.dumps([
        {"Id": EGRESS, "Name": "existing-egress", "Internal": False, "Driver": "bridge"}]))
    assert gateway._gateway_networks(row, {"network_id": NETWORK, "egress_network_id": EGRESS})["NetworkID"] == NETWORK
    with pytest.raises(ValueError, match="count"):
        gateway._configured_network(row, NETWORK)
    with pytest.raises(ValueError, match="count"):
        gateway._gateway_networks(row, {"network_id": NETWORK})


@pytest.mark.parametrize("fault", ["unapproved", "extra", "replaced", "internal", "driver", "missing_primary"])
def test_gateway_egress_rejects_unpinned_or_changed_networks(monkeypatch, fault):
    row = gateway_with_egress()
    observed = {"Id": EGRESS, "Name": "existing-egress", "Internal": False, "Driver": "bridge"}
    if fault == "unapproved": row["NetworkSettings"]["Networks"]["existing-egress"]["NetworkID"] = "b" * 64
    elif fault == "extra": row["NetworkSettings"]["Networks"]["third"] = {"NetworkID": "b" * 64}
    elif fault == "replaced": observed["Id"] = "b" * 64
    elif fault == "internal": observed["Internal"] = True
    elif fault == "driver": observed["Driver"] = "host"
    else: row["NetworkSettings"]["Networks"]["isolated"]["NetworkID"] = "b" * 64
    monkeypatch.setattr(gateway, "_command", lambda args: json.dumps([observed]))
    with pytest.raises(ValueError):
        gateway._gateway_networks(row, {"network_id": NETWORK, "egress_network_id": EGRESS})
