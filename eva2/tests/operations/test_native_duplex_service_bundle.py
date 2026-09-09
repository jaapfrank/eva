import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys

import pytest

EXPERIMENT = Path(__file__).resolve().parents[2] / "experiments/native-duplex-v1"
sys.path.insert(0, str(EXPERIMENT))
bundle = importlib.import_module("service_bundle")
launcher = importlib.import_module("service_launcher")


@pytest.mark.parametrize("environment", [
    "", "EVA2_CANARY_URL=http://127.0.0.1:44709",
    "EVA2_CANARY_URL=http://other:44711",
    "EVA2_CANARY_URL=http://127.0.0.1:44711/",
    "EVA2_CANARY_URL=http://127.0.0.1:44711 EVA2_CANARY_URL=http://127.0.0.1:44710",
])
def test_launcher_rejects_missing_ambiguous_or_unmanaged_route(environment):
    with pytest.raises(ValueError, match="retained slot"):
        launcher.selected_core(environment)


def test_launcher_uses_loaded_parent_route_and_checks_parent_state(monkeypatch):
    calls = []
    def output(command, **kwargs):
        calls.append(command)
        return "active\n" if "is-active" in command else "OTHER=value EVA2_CANARY_URL=http://127.0.0.1:44710\n"
    monkeypatch.setattr(launcher.subprocess, "check_output", output)
    assert launcher.active_core("probe-parent.service") == "http://127.0.0.1:44710"
    assert all("probe-parent.service" in cmd for cmd in calls)
    with pytest.raises(ValueError):
        launcher.active_core("evil.service;command")
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *a, **k: "inactive")
    with pytest.raises(ValueError, match="not active"):
        launcher.active_core()


def prepared(tmp_path):
    root = tmp_path / "bundle"; root.mkdir()
    (root / "source.py").write_text("print('candidate')\n")
    runtime = tmp_path / "host-library"; runtime.write_bytes(b"expected host bytes")
    data = {"contract": "EvaNativeSpeakerBundle/v1", "sourceCommit": "a" * 40,
        "files": bundle.file_map(root), "hostRuntime": {
            "python": str(Path(sys.executable).resolve()), "pythonVersion": platform.python_version(),
            "files": {str(runtime): bundle.digest(runtime)}}}
    (root / "bundle.json").write_text(json.dumps(data))
    return root, runtime


@pytest.mark.parametrize("change", ["source", "extra-file", "symlink", "host-library"])
def test_startup_rejects_changed_source_dependency_or_host_library(tmp_path, change):
    root, runtime = prepared(tmp_path)
    assert bundle.verify(root, require_root=False)["sourceCommit"] == "a" * 40
    if change == "source":
        (root / "source.py").write_text("changed")
    elif change == "extra-file":
        (root / "injected.py").write_text("extra")
    elif change == "symlink":
        (root / "injected.py").symlink_to(runtime)
    else:
        runtime.write_bytes(b"OS update requires revalidation")
    with pytest.raises(ValueError):
        bundle.verify(root, require_root=False)


def test_bundle_preparation_cannot_replace_existing_release(tmp_path):
    target = tmp_path / "existing"; target.mkdir()
    marker = target / "keep"; marker.write_bytes(b"previous release")
    with pytest.raises(ValueError, match="new absolute"):
        bundle.prepare(repository=tmp_path, source_commit="a" * 40, wheelhouse=tmp_path,
                       model=tmp_path, output=target, pip_python=Path(sys.executable))
    assert marker.read_bytes() == b"previous release"


def test_systemd_preserves_parent_lifecycle_and_owned_child_cleanup():
    text = (EXPERIMENT.parents[1] / "deploy/home-assistant/eva2-native-speaker.service").read_text()
    assert all(line in text.splitlines() for line in (
        "BindsTo=eva.service", "PartOf=eva.service", "DynamicUser=yes", "KillMode=mixed",
        "TimeoutStopSec=20", "NoNewPrivileges=yes", "ProtectSystem=strict"))
    assert " -I -S -B " in text
    assert "LoadCredential=config.json:" in text and "LoadCredential=tls.key:" in text
    assert "EnvironmentFile=" not in text
