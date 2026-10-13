"""Lifecycle of installer-owned restore composition, independent of CLI files."""
from pathlib import Path
import pytest
from tools import installer_restore_wizard as module


def config(tmp_path):
    return {"dsn": "postgresql+psycopg://fixture/fixture", "authority": str(tmp_path / "authority"),
            "workspace": str(tmp_path / "work"), "objects": str(tmp_path / "objects"),
            "keyring": None, "traffic_closed": True, "runtime_guard": {}}


@pytest.mark.parametrize("failure", [None, "construct", "body", "close"])
def test_composition_always_releases_database_engine(monkeypatch, tmp_path, failure):
    events = []
    class Engine:
        def dispose(self): events.append("dispose")
    class Workflow:
        def __init__(self, **kwargs):
            events.append("construct")
            if failure == "construct": raise RuntimeError("construct")
        def close(self):
            events.append("close")
            if failure == "close": raise RuntimeError("close")
    monkeypatch.setattr(module, "create_engine", lambda dsn: Engine())
    monkeypatch.setattr(module, "InstallerPortableRestore", Workflow)
    def run():
        with module.configured_restore_workflow(config(tmp_path)) as workflow:
            assert isinstance(workflow, Workflow)
            events.append("body")
            if failure == "body": raise RuntimeError("body")
    if failure:
        with pytest.raises(RuntimeError, match=failure): run()
    else:
        run()
    assert events[-1] == "dispose"
    assert events.count("dispose") == 1
    assert events.count("close") == (0 if failure == "construct" else 1)


def test_cli_uses_same_composition_and_preserves_wizard_arguments(monkeypatch, tmp_path):
    import json
    from contextlib import contextmanager
    settings = config(tmp_path)
    monkeypatch.setattr(module, "read_private_runtime_file_bytes", lambda *a, **kw: json.dumps(settings).encode())
    workflow = object()
    events = []
    @contextmanager
    def composed(value):
        assert value == settings
        events.append("enter")
        try: yield workflow
        finally: events.append("exit")
    def run(argv, *, restore_workflow):
        assert argv == ["--port", "44999"]
        assert restore_workflow is workflow
        events.append("wizard")
        return 7
    monkeypatch.setattr(module, "configured_restore_workflow", composed)
    monkeypatch.setattr(module, "run_installer_wizard", run)
    assert module.main(["--restore-config", str(tmp_path / "target.json"), "--port", "44999"]) == 7
    assert events == ["enter", "wizard", "exit"]


@pytest.mark.parametrize("custom_workspace", [False, True])
def test_ordinary_launcher_needs_no_restore_configuration(monkeypatch, tmp_path, custom_workspace):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    calls = []
    def run(argv):
        calls.append(argv)
        return 0
    def unexpected(*args, **kwargs):
        raise AssertionError("ordinary onboarding must not read restore configuration or create a restore target")
    monkeypatch.setattr(module, "run_installer_wizard", run)
    monkeypatch.setattr(module, "read_private_runtime_file_bytes", unexpected)
    monkeypatch.setattr(module, "configured_restore_workflow", unexpected)
    arguments = ["--port", "44999"]
    workspace = tmp_path / "data/eva2/installer"
    if custom_workspace:
        workspace = tmp_path / "chosen"
        arguments.extend(["--workspace", str(workspace)])
    assert module.main(arguments) == 0
    assert calls == [["--port", "44999", "--workspace", str(workspace)]]
