from eva.bootstrap.component_observations import ComponentObserver, configured_component_observer


def test_existing_read_only_probe_is_cached_with_original_observation_time(monkeypatch):
    calls = []
    now = [1.0]
    monkeypatch.setattr("eva.bootstrap.component_observations.read_runtime_secret_file", lambda *a, **k: "credential")
    class Probe:
        def read_only_probe(self, *, secret, timeout_seconds):
            assert bytes(secret) == b"credential"
            assert timeout_seconds == 2.0
            calls.append(True)
    observer = ComponentObserver({"telegram": ("/token", Probe, "API only; no messages tested.")}, clock=lambda: now[0])
    first = observer()
    assert first["telegram"]["state"] == "ready"
    assert observer() == first and len(calls) == 1
    first["telegram"]["state"] = "changed externally"
    assert observer()["telegram"]["state"] == "ready"
    now[0] = 32.0
    assert observer()["telegram"]["state"] == "ready" and len(calls) == 2


def test_probe_failure_is_visible_without_exposing_credentials(monkeypatch):
    monkeypatch.setattr("eva.bootstrap.component_observations.read_runtime_secret_file", lambda *a, **k: "credential")
    class Probe:
        def read_only_probe(self, **kwargs):
            raise RuntimeError("SECRET private endpoint")
    result = ComponentObserver({"home_assistant": ("/token", Probe, "API")})()
    assert result["home_assistant"]["state"] == "attention"
    assert result["home_assistant"]["observed_at"] is not None
    assert "SECRET" not in str(result)


def test_missing_credentials_do_not_trigger_a_probe(monkeypatch):
    monkeypatch.delenv("EVA_TELEGRAM_BOT_TOKEN_FILE", raising=False)
    monkeypatch.delenv("EVA_HOME_ASSISTANT_TOKEN_FILE", raising=False)
    result = configured_component_observer()()
    assert all(row["state"] == "not_configured" and row["observed_at"] is None for row in result.values())


def test_worker_observation_requires_current_bounded_contract(monkeypatch):
    import json
    calls = []
    payload = [{"contract": "EvaWorkerLiveness/v1", "state": "ready", "active_lanes": 2}]
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("worker", 8081, 2.0)
        def request(self, method, path):
            assert (method, path) == ("GET", "/health")
            calls.append(True)
        def getresponse(self):
            if isinstance(payload[0], Exception):
                raise payload[0]
            class Response:
                status = 200
                def read(self, limit):
                    assert limit == 1025
                    return json.dumps(payload[0]).encode()[:limit]
            return Response()
        def close(self):
            pass
    monkeypatch.setattr("eva.bootstrap.component_observations.HTTPConnection", Connection)
    valid = ComponentObserver({}, worker_url="http://worker:8081/health")()
    assert valid["worker"]["state"] == "ready"
    assert "modeltoegang" in valid["worker"]["detail"]
    for invalid in [RuntimeError("SECRET endpoint"), {"contract": "other"},
                    {"contract": "EvaWorkerLiveness/v1", "state": "attention", "active_lanes": 0},
                    {"contract": "EvaWorkerLiveness/v1", "state": "ready", "active_lanes": True},
                    {"contract": "EvaWorkerLiveness/v1", "state": "ready", "active_lanes": 3},
                    "x" * 2048]:
        payload[0] = invalid
        result = ComponentObserver({}, worker_url="http://worker:8081/health")()
        assert result["worker"]["state"] == "attention"
        assert result["worker"]["observed_at"] is not None
        assert "SECRET" not in str(result)


def test_optional_worker_observation_preserves_cached_time(monkeypatch):
    monkeypatch.setenv("EVA_DELEGATED_WORK_HEALTH_URL", "http://worker:8081/health")
    configured = configured_component_observer()
    assert configured._worker_url == "http://worker:8081/health"
    calls = []
    now = [0.0]
    def observe(url):
        calls.append(url)
        return "worker", {"state": "ready", "observed_at": str(now[0]), "detail": "loop only"}
    monkeypatch.setattr(ComponentObserver, "_observe_worker", staticmethod(observe))
    observer = ComponentObserver({}, worker_url="http://worker:8081/health", clock=lambda: now[0])
    first = observer()
    now[0] = 10
    assert observer() == first and len(calls) == 1
    now[0] = 31
    assert observer()["worker"]["observed_at"] == "31" and len(calls) == 2
