"""New-browser preparation and bounded provisioning with no Docker mutations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import shutil
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("installer_browser_module", ROOT / "tools/installer_browser_module.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def prepare(tmp_path):
    package = tmp_path / "package"
    recipe = package / "deploy/owner-browser"
    if not recipe.exists():
        shutil.copytree(ROOT / "deploy/owner-browser", recipe)
        pin_file = recipe / "pixelflux-idle-source.json"
        pin = json.loads(pin_file.read_text())
        wheel = recipe / pin["wheel"]["filename"]
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(pin["installedTarget"]["extension"], b"native-updated")
            archive.writestr("pixelflux.libs/library.so", b"library")
            archive.writestr("pixelflux-2.1.0.dist-info/licenses/LICENSE", b"retained license")
        pin["wheel"]["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        pin["installedTarget"]["extensionSha256"] = hashlib.sha256(b"native-updated").hexdigest()
        pin["original"]["extensionSha256"] = hashlib.sha256(b"native-original").hexdigest()
        pin_file.write_text(json.dumps(pin))
        provenance_file = recipe / "provenance.json"
        provenance = json.loads(provenance_file.read_text())
        if "pixelflux-idle-source.json" in provenance["files"]:
            provenance["files"]["pixelflux-idle-source.json"]["sha256"] = hashlib.sha256(pin_file.read_bytes()).hexdigest()
            provenance_file.write_text(json.dumps(provenance))
    return module.prepare_browser_module(package_root=package, workspace=tmp_path,
        namespace="installer-browser-test", owner_origin="http://localhost:44811",
        internal_subnet="172.28.90.0/24", egress_subnet="172.28.91.0/24",
        control_port=45811, viewer_port=45812, python_executable=Path(sys.executable))


def test_prepare_preserves_recipe_pins_and_renders_only_new_isolated_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_run", lambda *a, **k: pytest.fail("prepare must not invoke Docker"))
    plan = prepare(tmp_path)
    root = Path(plan["deployment_dir"])
    assert plan["control_url"] == "http://172.28.90.1:45811"
    assert plan["mcp_url"] == "http://172.28.90.2:8931/mcp"
    assert all("@sha256:" in image for image in plan["images"])
    assert "172.28.91.2" in (root / "browser-firewall.py").read_text()
    assert "EVA2VB3F" not in (root / "browser-firewall.py").read_text()
    assert "172.28.91.0/24" in (root / "configure-egress.py").read_text()
    secrets = [p.read_bytes() for p in (root / "secrets").iterdir()]
    assert len(set(secrets)) == 4
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (root / "secrets").iterdir())
    assert (root / "autostart").stat().st_mode & 0o777 == 0o755
    with pytest.raises(FileExistsError):
        prepare(tmp_path)


def test_missing_pin_stops_before_runtime_mutation_and_keeps_state(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    plan["operator_uid"] = 0
    (Path(plan["deployment_dir"]) / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        raise RuntimeError("image pin unavailable")
    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(RuntimeError, match="pin unavailable"):
        module.start_browser_module(plan)
    state = json.loads((Path(plan["deployment_dir"]) / "phase.json").read_text())
    assert state["phase"] == "prepared" and state["error_type"] == "RuntimeError"
    assert len(calls) == 1 and calls[0][:3] == ["docker", "image", "inspect"]
    with pytest.raises(ValueError, match="already attempted"):
        module.start_browser_module(plan)


def test_existing_subnet_is_rejected_before_creating_any_resources(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    plan["operator_uid"] = 0
    (Path(plan["deployment_dir"]) / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "network", "ls"]:
            return "existing"
        if argv[:3] == ["docker", "network", "inspect"]:
            return json.dumps([{"IPAM": {"Config": [{"Subnet": "172.28.0.0/16"}]}}])
        return "[]"
    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(ValueError, match="already used"):
        module.start_browser_module(plan)
    assert not any("create" in call for call in calls)


def test_changed_recipe_material_is_rejected_before_start(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    plan["operator_uid"] = 0
    root = Path(plan["deployment_dir"])
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "autostart").chmod(0o600)
    (root / "autostart").write_text("changed")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "_run", lambda *a, **k: pytest.fail("must fail before runtime"))
    with pytest.raises(ValueError, match="material changed"):
        module.start_browser_module(plan)


def test_start_orders_protection_before_existing_controller_start(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import os
    plan = prepare(tmp_path)
    root = Path(plan["deployment_dir"])
    plan["operator_uid"] = 0
    plan["overlay"] = dict(plan["overlay"], upstream_sha256=hashlib.sha256(b"upstream").hexdigest(),
                           patched_sha256=hashlib.sha256(b"patched").hexdigest())
    (root / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *args: None)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "network", "ls"]:
            return "host\nnone\nbridge"
        if argv[:3] == ["docker", "network", "inspect"]:
            return json.dumps([
                {"Name": "host", "IPAM": {"Config": None}},
                {"Name": "none", "IPAM": {"Config": None}},
                {"Name": "bridge", "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}},
            ])
        if argv[:3] == ["docker", "network", "create"]:
            return "a" * 64
        if argv[:2] == ["docker", "create"]:
            return ("b" if argv[3].endswith("-browser") else "c") * 64
        if argv[:2] == ["docker", "cp"] and argv[2].startswith("b" * 64):
            destination = Path(argv[3])
            if destination.parent.name == "native-original":
                if destination.name.endswith(".so"):
                    destination.write_bytes(b"native-original")
                else:
                    destination.mkdir()
            else:
                destination.write_bytes(b"upstream")
        if argv[:2] == ["docker", "cp"] and argv[2] == str(root / "native-overlay") + "/.":
            assert (root / "native-overlay/pixelflux-2.1.0.dist-info/licenses/LICENSE").read_bytes() == b"retained license"
            assert (root / "native-overlay/pixelflux.libs/library.so").stat().st_mode & 0o777 == 0o444
        if argv[0] == "patch":
            Path(argv[2]).write_bytes(b"patched")
            Path(argv[2]).chmod(0o600)
        if argv[:2] == ["docker", "cp"] and argv[2] == str(root / "selkies-auth-patched.py"):
            assert Path(argv[2]).stat().st_mode & 0o777 == 0o444
        return "private-password-hash" if argv[0] == "openssl" else ""
    monkeypatch.setattr(module, "_run", run)
    def popen(argv, **kwargs):
        calls.append(["controller", *argv])
        assert kwargs["start_new_session"] and kwargs["env"]["PYTHONPATH"] == str(Path(plan["package_root"]) / "src")
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    def control(selected, path, payload=None):
        calls.append(["control", path, payload])
        if path == "status":
            return {"state": "stopped", "revision": 0}
        assert payload == {"action": "start", "revision": 0}
        return {"state": "eva", "revision": 1}
    monkeypatch.setattr(module, "_control", control)
    result = module.start_browser_module(plan)
    assert result["state"] == "eva" and result["network_id"] == "a" * 64
    assert result["browser_id"] == "b" * 64 and result["mcp_id"] == "c" * 64
    firewall = next(i for i, call in enumerate(calls) if str(root / "browser-firewall.py") in call)
    stopped = calls.index(["docker", "stop", "b" * 64])
    controller = next(i for i, call in enumerate(calls) if call[0] == "controller")
    native_copy = next(i for i, call in enumerate(calls) if call[:3] == ["docker", "cp", str(root / "native-overlay") + "/."])
    first_start = calls.index(["docker", "start", "b" * 64])
    assert native_copy < first_start < firewall < stopped < controller
    assert not any(call[:2] == ["docker", "start"] and "c" * 64 in call for call in calls)
    assert json.loads((root / "phase.json").read_text())["phase"] == "ready"
    config = json.loads((root / "host-config.json").read_text())
    assert config["listen_host"] == "172.28.90.1"
    assert config["viewer_origin"] == "http://172.28.90.2:3000"


def test_prepare_keeps_intended_file_modes_under_private_umask(tmp_path):
    import os
    previous = os.umask(0o077)
    try:
        plan = prepare(tmp_path)
    finally:
        os.umask(previous)
    root = Path(plan["deployment_dir"])
    for name in ("autostart", "selkies-run"):
        assert (root / name).stat().st_mode & 0o777 == 0o755
    for name in ("configure-egress.py", "browser-firewall.py", "host-config.template.json"):
        assert (root / name).stat().st_mode & 0o777 == 0o444
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (root / "secrets").iterdir())
    assert (root / "plan.json").stat().st_mode & 0o777 == 0o600


def test_native_wheel_requires_exact_offline_bytes(tmp_path):
    plan = prepare(tmp_path)
    wheel = Path(plan["native_wheel"])
    wheel.write_bytes(b"same-version-but-wrong-wheel")
    with pytest.raises(ValueError, match="exact pinned"):
        module.native_browser_wheel(plan["package_root"])
    wheel.unlink()
    with pytest.raises(FileNotFoundError):
        module.native_browser_wheel(plan["package_root"])


@pytest.mark.parametrize("include_native", [False, True])
@pytest.mark.parametrize("include_voice", [False, True])
def test_stage_optional_native_wheel_inventory_without_runtime(tmp_path, monkeypatch, include_native, include_voice):
    from types import ModuleType
    stage_spec = importlib.util.spec_from_file_location("stage_browser_test", ROOT / "tools/stage_installer_package.py")
    stage = importlib.util.module_from_spec(stage_spec)
    stage_spec.loader.exec_module(stage)
    prepared = prepare(tmp_path)
    source = Path(prepared["package_root"])
    required = [*stage._SOURCE_FILES, "tools/launch_packaged_installer.py",
        "tools/installer_restore_wizard.py", "src/eva/bootstrap/installer_wizard_cli.py",
        "release/platform-lock-wsl-candidate.json", "deploy/owner-browser/pixelflux-idle-source.json"]
    for name in required:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("fixture")
    target = tmp_path / "staged"
    image, container = "sha256:" + "d" * 64, "e" * 64
    nonce = None
    calls = []
    def output(argv, **kwargs):
        nonlocal nonce
        calls.append(argv)
        if argv[0] == "git":
            if "--show-toplevel" in argv:
                return str(source)
            if "ls-files" in argv:
                return "\0".join(required).encode()
            if "status" in argv:
                return b""
            return "f" * 40
        if argv[1:3] == ["image", "inspect"]:
            return json.dumps([{"Id": argv[3], "Architecture": "amd64", "Os": "linux"}])
        if argv[1] == "create":
            nonce = argv[argv.index("--label") + 1].split("=", 1)[1]
            return container
        if argv[1] == "inspect":
            return json.dumps([{"Id": container, "Image": image, "Config": {"Labels": {
                "io.eva2.installer-export": nonce}}, "State": {"Status": "created"}}])
        if argv[1] == "cp":
            runtime = target / "python-runtime/bin/python3.12"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("fixture")
        return ""
    monkeypatch.setattr(stage.subprocess, "check_output", output)
    tools_module = ModuleType("tools")
    tools_module.__path__ = []
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.installer_browser_module", module)
    images={"living-core":image,"postgres":"sha256:"+"1"*64,"hindsight":"sha256:"+"2"*64}
    if include_voice:images.update({"voice-media":"sha256:"+"3"*64,"livekit":"sha256:"+"4"*64})
    result = stage.stage_package(development_images=images, source_root=source, target_root=target, runtime_image=image,
        browser_native_wheel=Path(prepared["native_wheel"]) if include_native else None)
    assert result["development_candidate"]["imageDigests"]==images
    assert all(["docker","image","inspect",digest] in calls for digest in images.values())
    key = "deploy/owner-browser/" + Path(prepared["native_wheel"]).name
    assert (key in result["source_files"]) is include_native
    assert (target / key).exists() is include_native
    if include_native:
        assert result["source_files"][key] == prepared["native_overlay"]["wheel"]["sha256"]
    assert not any(call[0] == "docker" and call[1] in {"start", "run", "pull"} for call in calls)


@pytest.mark.parametrize('flags,valid', [([],True),(['--voice-media-image','sha256:a'],False),
    (['--livekit-image','sha256:b'],False),(['--voice-media-image','sha256:a','--livekit-image','sha256:b'],True)])
def test_stage_voice_cli_pair(monkeypatch,flags,valid):
    spec=importlib.util.spec_from_file_location('stage_voice_cli',ROOT/'tools/stage_installer_package.py')
    stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
    calls=[];monkeypatch.setattr(stage,'stage_package',lambda **kw:calls.append(kw))
    args=['--source-root','/source','--target-root','/target','--runtime-image','sha256:core',
          '--postgres-image','sha256:pg','--hindsight-image','sha256:memory',*flags]
    if not valid:
        with pytest.raises(SystemExit):stage.main(args)
        assert calls==[]
    else:
        assert stage.main(args)==0
        images=calls[0]['development_images']
        assert ('voice-media' in images)==bool(flags) and ('livekit' in images)==bool(flags)


def test_stage_connector_gateway_cli_uses_existing_inventory(monkeypatch):
    spec=importlib.util.spec_from_file_location('stage_connector_cli',ROOT/'tools/stage_installer_package.py')
    stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
    calls=[];monkeypatch.setattr(stage,'stage_package',lambda **kw:calls.append(kw))
    base=['--source-root','/source','--target-root','/target','--runtime-image','sha256:core','--mcp-gateway-image','sha256:gateway']
    with pytest.raises(SystemExit):stage.main(base)
    assert not calls
    assert stage.main([*base,'--postgres-image','sha256:pg','--hindsight-image','sha256:memory'])==0
    assert calls[0]['development_images']['mcp-gateway']=='sha256:gateway'
