import importlib
import json
from pathlib import Path
import platform
import sys

import pytest

EXPERIMENT = Path(__file__).resolve().parents[3] / "experiments/native-duplex-v1"
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


def prepared(tmp_path, monkeypatch):
    root = tmp_path / "bundle"; root.mkdir()
    (root / "source.py").write_text("print('candidate')\n")
    runtime = tmp_path / "host-library"; runtime.write_bytes(b"expected host bytes")
    data = {"contract": "EvaNativeSpeakerBundle/v1", "sourceCommit": "a" * 40,
        "files": bundle.file_map(root), "hostRuntime": {
            "python": str(Path(sys.executable).resolve()), "pythonVersion": platform.python_version(),
            "files": {str(runtime): bundle.digest(runtime)}}}
    (root / "bundle.json").write_text(json.dumps(data))
    def current(_root):
        return {**data["hostRuntime"], "files": {str(runtime): bundle.digest(runtime)}}
    monkeypatch.setattr(bundle, "host_runtime", current)
    return root, runtime


@pytest.mark.parametrize("change", ["source", "extra-file", "symlink", "host-library"])
def test_startup_rejects_changed_source_dependency_or_host_library(tmp_path, monkeypatch, change):
    root, runtime = prepared(tmp_path, monkeypatch)
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


def test_host_closure_detects_changed_soname_and_added_python_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    stdlib = tmp_path / "stdlib"; stdlib.mkdir()
    old = tmp_path / "lib-old.so"; old.write_bytes(b"old")
    new = tmp_path / "lib-new.so"; new.write_bytes(b"new")
    link = tmp_path / "lib.so.1"; link.symlink_to(old)
    monkeypatch.setattr(bundle.sysconfig, "get_path", lambda name: str(stdlib))
    monkeypatch.setattr(bundle.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=f"lib.so.1 => {link} (0x123)"))
    first = bundle.host_runtime()
    link.unlink(); link.symlink_to(new)
    changed = bundle.host_runtime()
    assert str(old) in first["files"] and str(new) in changed["files"]
    assert first != changed
    cache = stdlib / "module.pyc"; cache.write_bytes(b"new cache")
    assert str(cache) in bundle.host_runtime()["files"]


def test_systemd_credential_copy_is_private_bounded_and_removed(tmp_path):
    import stat
    credentials = tmp_path / "systemd"; credentials.mkdir()
    runtime = tmp_path / "runtime"; runtime.mkdir(mode=0o700)
    for name in ("config.json", "tls.crt", "tls.key"):
        path = credentials / name; path.write_bytes(b"synthetic")
        path.chmod(0o440)  # ACL mask on actual systemd credentials is 0440.
    with launcher.private_credentials(credentials, runtime) as copy:
        saved = copy
        assert stat.S_IMODE(copy.stat().st_mode) == 0o700
        for path in copy.iterdir():
            assert path.read_bytes() == b"synthetic"
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not saved.exists() and not list(runtime.iterdir())
    (credentials / "tls.key").chmod(0o600)
    (credentials / "tls.key").write_bytes(b"x" * 65537)
    with pytest.raises(ValueError):
        with launcher.private_credentials(credentials, runtime):
            pytest.fail("oversized credentials must not enter service")
    assert not list(runtime.iterdir())



def test_silero_disables_telemetry_before_constructing_inference_session(monkeypatch):
    from types import SimpleNamespace
    import importlib.util
    events = []
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(disable_telemetry_events=lambda: events.append("disabled")))
    spec = importlib.util.spec_from_file_location("speaker_telemetry_probe", EXPERIMENT / "silero_onnx.py")
    module = importlib.util.module_from_spec(spec)
    # np.ndarray appears in annotations in the retained wrapper.
    sys.modules["numpy"].ndarray = object
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SileroOnnxModel", lambda path: events.append("session"))
    detector = module.Silero10msDetector("synthetic-model")
    assert events == ["disabled", "session"]
    # The continuous microphone validates every 10ms result, including the
    # three blocks retained before the first 32ms inference window is ready.
    for _ in range(3):
        assert detector.Process10ms(bytes(320)) == 0.0
