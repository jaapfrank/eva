"""Controlled transaction/start ordering and partial-activation HTTP reporting.

The transaction backend and component release body are controlled here. This
suite does not prove real PostgreSQL commit, external restore or Docker startup.
"""
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from time import monotonic

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eva.adapters.api.installer_restore_api import register_installer_restore_routes
from eva.application.installer_restore import InactiveRecoveryMaterialRetained
from tools.installer_portable_restore import InstallerPortableRestore


class Backend:
    def __init__(self, failure=None):
        self.events = []
        self.failure = failure
    @contextmanager
    def begin(self):
        self.events.append("begin")
        try:
            yield
        except Exception:
            self.events.append("rollback")
            raise
        else:
            if self.failure == "commit":
                self.events.append("commit_failed")
                raise RuntimeError("commit failed")
            self.events.append("committed")


class ConnectionContext:
    def __init__(self, backend): self.backend = backend
    def execution_options(self, **options):
        assert options == {"isolation_level": "SERIALIZABLE"}
        return self
    def __enter__(self):
        self.backend.events.append("connect"); return self.backend
    def __exit__(self, *args): self.backend.events.append("connection_closed")


def workflow(tmp_path, monkeypatch, failure=None):
    backend = Backend(failure)
    engine = type("Engine", (), {"connect": lambda self: ConnectionContext(backend)})()
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    wizard = InstallerPortableRestore(engine=engine, authority_reader=lambda: b"authority",
        workspace=private, object_store_root=tmp_path / "objects")
    # Bypass unrelated archive preparation to isolate the real execute boundary.
    staging = TemporaryDirectory(dir=private)
    wizard._pending = (staging, "confirmation", monotonic()+60, "archive")
    wizard.recovery_materialization = {"destination": str(tmp_path / "retained")}
    wizard.runtime_identity = {"tenant_id": "tenant", "installation_id": "installation"}
    monkeypatch.setattr(wizard, "_read", lambda path: ({}, b'{"contract":"EvaInstallationRecovery/v1"}', {"archive_sha256":"archive"}))
    release = {"contract": "EvaRestoreActivationRelease/v1", "status": "released_pending_commit"}
    def restore(connection, directory, manifest, summary, state):
        assert backend.events[-1] == "begin"
        state.update(published=True, activation_attempted=True)
        backend.events.append("release")
        if failure == "release": raise RuntimeError("release failed")
        return {"restore_id":"restore", "archive_sha256":"archive", "activation_release":release}
    monkeypatch.setattr(wizard, "_execute_installation", restore)
    def start(**kwargs):
        assert backend.events[-2:] == ["committed", "connection_closed"]
        assert kwargs == dict(restore_id="restore", archive_sha256="archive", release_receipt=release,
                             runtime_identity=wizard.runtime_identity)
        backend.events.append("start")
        if failure == "start": raise RuntimeError("partial start")
        if failure == "readiness": return {"runtime_started":False}
        return {"contract":"EvaRestoredRuntimeStart/v1", "status":"runtime_started_verified", "runtime_started":True}
    wizard.start_runtime = start
    return wizard, backend


def test_start_follows_successful_commit_and_connection_close(tmp_path, monkeypatch):
    wizard, backend = workflow(tmp_path, monkeypatch)
    result = wizard.execute("confirmation")
    assert backend.events == ["connect", "begin", "release", "committed", "connection_closed", "start"]
    assert result["target_activation"] == "runtime_started"
    assert "geen volledige gebruikersacceptatie" in result["notice"]
    assert wizard._pending is None


@pytest.mark.parametrize("failure", ["release", "commit"])
def test_release_or_commit_failure_never_starts_runtime(tmp_path, monkeypatch, failure):
    wizard, backend = workflow(tmp_path, monkeypatch, failure)
    with pytest.raises(InactiveRecoveryMaterialRetained) as error:
        wizard.execute("confirmation")
    assert "start" not in backend.events
    assert error.value.activation_attempted is True
    assert error.value.runtime_start_attempted is False
    assert wizard._pending is None


@pytest.mark.parametrize("failure", ["start", "readiness"])
def test_partial_start_is_reported_as_unconfirmed_without_retry(tmp_path, monkeypatch, failure):
    wizard, backend = workflow(tmp_path, monkeypatch, failure)
    app = FastAPI()
    register_installer_restore_routes(app, workflow=wizard, require_write=lambda request: None, security_headers={})
    with TestClient(app) as client:
        result = client.post("/internal/v1/onboarding/restore/execute", json={"confirmation":"confirmation"})
        assert result.status_code == 409
        assert result.json()["recovery_state"] == "activation_unconfirmed"
        assert "gedeeltelijk gestart" in result.json()["detail"]
        assert "herhaal" in result.json()["detail"]
        assert str(tmp_path) not in result.text
        assert "Eva blijft uit" not in result.text
        client.post("/internal/v1/onboarding/restore/execute", json={"confirmation":"confirmation"})
    assert backend.events.count("start") == 1
    assert wizard._pending is None


@pytest.mark.parametrize("failure", ["release", "commit"])
def test_activation_failure_http_does_not_claim_inactive_target(tmp_path, monkeypatch, failure):
    wizard, _ = workflow(tmp_path, monkeypatch, failure)
    app = FastAPI()
    register_installer_restore_routes(app, workflow=wizard, require_write=lambda request: None, security_headers={})
    result = TestClient(app).post("/internal/v1/onboarding/restore/execute", json={"confirmation":"confirmation"})
    assert result.status_code == 409
    assert result.json()["recovery_state"] == "activation_unconfirmed"
    assert "Eva blijft uit" not in result.text
