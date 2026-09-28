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
