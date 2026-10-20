from dataclasses import replace
from copy import deepcopy
import pytest

from eva.providers.home_assistant import HomeAssistantProviderSettings
from eva.providers.home_assistant_capabilities import HomeAssistantCapabilityProfile
from eva.adapters.connectors.home_assistant_mcp import HomeAssistantMcpError
from eva.adapters.connectors.home_assistant import HomeAssistantConfig, HomeAssistantToolAdapter
from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from development.tests.connectors.test_home_assistant_mcp_effect import SCHEMA


def settings(**kw):
    return HomeAssistantProviderSettings(endpoint_url="http://ha.test", credential="secret",
        effects_enabled=True, effect_allowlist=("light.test:turn_off",),
        effect_principal_sha256_allowlist=("a"*64,), event_domains=("light",), api_enabled=True, **kw)


def profile(monkeypatch, *, tools=None, version="test", ws_version=None):
    calls = []
    catalog_tools = tools if tools is not None else [{"name": n, "inputSchema": deepcopy(SCHEMA)}
        for n in ("HassTurnOn", "HassTurnOff")]
    class Client:
        def __init__(self, **kw): calls.append(("client", kw))
        def invalidate(self): calls.append(("invalidate",))
        def catalog(self, timeout):
            calls.append(("catalog",))
            return {"tools": catalog_tools, "catalog_sha256": "b"*64, "protocol_version": "2025-06-18"}
    monkeypatch.setattr("eva.providers.home_assistant_capabilities.HomeAssistantAssistClient", Client)
    def config(self, timeout):
        calls.append(("config",));return {"version": version, "components": ["automation", "recorder"]}
    monkeypatch.setattr(HomeAssistantCapabilityProfile, "_read_config", config)
    monkeypatch.setattr(HomeAssistantCapabilityProfile, "_read_websocket", lambda self, deadline:
        ({"version": ws_version or version}, {"id": "account", "is_admin": True}, {"light": {}}, {"exposed_entities": {}}))
    return HomeAssistantCapabilityProfile(settings()), calls


def test_profile_lazy_shared_and_bound_without_granting_access(monkeypatch):
    p, calls = profile(monkeypatch)
    assert not calls
    first=p.snapshot(); second=p.snapshot()
    assert first==second
    assert sum(c[0]=="catalog" for c in calls)==1
    assert first["routes"]["on_off"]["available"]
    assert first["routes"]["automation_management"]["available"]
    assert not first["automatic_transport_fallback"]
    assert "secret" not in str(first)
    first["routes"].clear()
    assert p.snapshot()["routes"]
    assert p.effect_binding(5)[1]=="b"*64
    assert len(calls)==3


def test_service_schema_reuses_profile_then_refreshes_without_mutable_alias(monkeypatch):
    p, calls = profile(monkeypatch)
    reads = []
    def websocket(deadline):
        reads.append(deadline)
        return ({"version": "test"}, {"id": "account"},
            {"sample": {"get_values": {"revision": len(reads)}}}, {})
    monkeypatch.setattr(p, "_read_websocket", websocket)
    first = p.service_definition("sample", "get_values")
    first["definition"]["revision"] = 999
    second = p.service_definition("sample", "get_values")
    assert second["definition"] == {"revision": 1} and len(reads) == 1
    assert first["observed_at"] == second["observed_at"]
    assert p.service_definition("missing", "service")["definition"] is None
    p.invalidate()
    assert p.service_definition("sample", "get_values")["definition"] == {"revision": 2}
    p._expires = 0
    assert p.service_definition("sample", "get_values")["definition"] == {"revision": 3}
    assert sum(c[0] == "catalog" for c in calls) == 3


@pytest.mark.parametrize("tools", [[], [{"name":"HassTurnOff", "inputSchema":{}}], [None]])
def test_missing_or_drifted_mcp_never_changes_api_authority(monkeypatch, tools):
    p,_=profile(monkeypatch,tools=tools)
    snap=p.snapshot()
    assert not snap["routes"]["on_off"]["available"]
    assert snap["routes"]["general_api"]["access"].startswith("existing authorized")
    assert p.require_transport("rest")["transport"]=="rest"
    if tools==[None]:
        with pytest.raises(HomeAssistantMcpError):p.effect_binding(5)


def test_expiry_and_explicit_invalidation_refresh_observations(monkeypatch):
    p,calls=profile(monkeypatch)
    p.snapshot();p.invalidate();p.snapshot()
    assert sum(c[0]=="catalog" for c in calls)==2
    p._expires=0;p.snapshot()
    assert sum(c[0]=="catalog" for c in calls)==3


def test_version_race_rejects_before_effect(monkeypatch):
    p,calls=profile(monkeypatch,ws_version="changed")
    with pytest.raises(HomeAssistantMcpError):p.effect_binding(5)
    assert not any(c[0]=="catalog" for c in calls)


def test_refresh_failure_does_not_return_old_observation(monkeypatch):
    p,_=profile(monkeypatch);p.snapshot();p.invalidate()
    def fail(*a,**kw):raise OSError("offline")
    monkeypatch.setattr(p,"_read_config",fail)
    with pytest.raises(HomeAssistantMcpError):p.effect_binding(5)
    assert p._snapshot is None and p._client is None


def test_effect_timeout_never_invokes_transport(monkeypatch):
    class SlowProfile:
        def effect_binding(self, timeout):return object(), "a"*64
        def invalidate(self):pass
    adapter=HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test","secret"),capability_profile=SlowProfile())
    clock=iter([0,6]);monkeypatch.setattr("time.monotonic",lambda:next(clock))
    monkeypatch.setattr("eva.adapters.connectors.home_assistant.call_assist_effect",lambda **kw:pytest.fail("late effect"))
    with pytest.raises(HomeAssistantMcpError):adapter._call_assist_effect(timeout=5)


def test_api_refuses_unavailable_selected_transport_without_fallback(monkeypatch):
    class Profile:
        def require_transport(self, transport, **kw):raise RuntimeError("unavailable")
    tool=HomeAssistantApiTool(base_url="http://ha.test",token="secret",capability_profile=Profile())
    with pytest.raises(RuntimeError):tool._request(dict(transport="rest",method="GET",path="/api/config",
        body_json="",response_pointer="",offset=0,limit=1))


def test_profile_lock_wait_cannot_restart_its_discovery_budget(monkeypatch):
    p, calls = profile(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr("eva.providers.home_assistant_capabilities.time.monotonic", lambda:clock[0])
    class Lock:
        def acquire(self, *, timeout):
            assert timeout == 5
            clock[0] += 6
            return True
        def release(self):pass
    p._lock = Lock()
    with pytest.raises(HomeAssistantMcpError, match="timed out"):
        p.snapshot(timeout=5)
    assert not calls


def test_api_rechecks_stop_after_profile_before_request(monkeypatch):
    from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
    signal = CancellationSignal()
    class Profile:
        def require_transport(self, *a, **kw):
            signal.cancel()
            return {"transport": "rest"}
    class Client:
        def __init__(self, **kwargs):pass
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def stream(self, *a, **kw):pytest.fail("effect after stop")
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_api.httpx.Client", Client)
    tool = HomeAssistantApiTool(base_url="http://unused.test", token="test", capability_profile=Profile())
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        tool._request(dict(transport="rest", method="POST", path="/api/services/light/turn_off",
            body_json='{"entity_id":"light.test"}', response_pointer="", offset=0, limit=1))


def test_api_does_not_dispatch_when_authorization_consumes_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("time.monotonic", lambda:clock[0])
    monkeypatch.setattr("eva.adapters.postgres.connector_results.check_capture_authority",
                        lambda:clock.__setitem__(0, 6.0))
    class Client:
        def __init__(self, **kwargs):pass
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def stream(self, *a, **kw):pytest.fail("late dispatch")
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_api.httpx.Client", Client)
    tool = HomeAssistantApiTool(base_url="http://unused.test", token="test")
    with pytest.raises(TimeoutError):
        tool._request(dict(transport="rest", method="POST", path="/api/services/light/turn_off",
            body_json='{"entity_id":"light.test"}', response_pointer="", offset=0, limit=1), deadline=5)
