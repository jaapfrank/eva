"""Verify interpreter selection without opening UI or starting target services."""
import pytest
import json
import hashlib

from tools.launch_packaged_installer import installer_command


@pytest.fixture
def package(tmp_path):
    for name in ("python-runtime/bin/python3.12", "tools/installer_restore_wizard.py",
                 "src/eva/bootstrap/installer_wizard_cli.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder")
    lock = tmp_path / "release/selected.json"
    lock.parent.mkdir()
    lock.write_text("packaged lock")
    (tmp_path / "package-staging.json").write_text(json.dumps({
        "platform_lock": "release/selected.json",
        "source_files": {"release/selected.json": hashlib.sha256(lock.read_bytes()).hexdigest()},
    }))
    return tmp_path


def test_host_python_configuration_cannot_override_packaged_dependencies(package):
    command, environment = installer_command(package, ["--workspace", "/private/setup"], {
        "PYTHONPATH": "/host/packages", "PYTHONHOME": "/host/python",
        "PYTHONUSERBASE": "/host/user", "PYTHONSTARTUP": "/host/start.py",
        "LD_LIBRARY_PATH": "/host/libraries", "HOME": "/owner", "PATH": "/usr/bin",
    })
    assert command == [str(package / "python-runtime/bin/python3.12"),
                       "-m", "tools.installer_restore_wizard", "--workspace", "/private/setup",
                       "--platform-lock", str(package / "release/selected.json")]
    assert environment["PYTHONHOME"] == str(package / "python-runtime")
    assert environment["PYTHONPATH"] == str(package / "src") + ":" + str(package)
    assert environment["LD_LIBRARY_PATH"] == str(package / "python-runtime/lib")
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONUSERBASE" not in environment and "PYTHONSTARTUP" not in environment
    assert environment["HOME"] == "/owner" and environment["PATH"] == "/usr/bin"


@pytest.mark.parametrize("missing", ["python-runtime/bin/python3.12",
                                    "tools/installer_restore_wizard.py",
                                    "src/eva/bootstrap/installer_wizard_cli.py"])
def test_incomplete_package_never_falls_back_to_host_python(package, missing):
    (package / missing).unlink()
    with pytest.raises(ValueError, match="complete packaged"):
        installer_command(package, [], {})


def test_external_interpreter_link_is_rejected(package, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other-python") / "python"
    outside.write_text("not packaged")
    interpreter = package / "python-runtime/bin/python3.12"
    interpreter.unlink()
    interpreter.symlink_to(outside)
    with pytest.raises(ValueError, match="complete packaged"):
        installer_command(package, [], {})


def test_changed_packaged_lock_is_rejected_before_wizard_start(package):
    (package / "release/selected.json").write_text("changed selection")
    with pytest.raises(ValueError, match="verified packaged platform"):
        installer_command(package, [], {})


def test_explicit_operator_selection_is_not_duplicated(package):
    arguments = ["--platform-lock", "/operator/validated-platform.json"]
    command, _ = installer_command(package, arguments, {})
    assert command[-2:] == arguments
    assert command.count("--platform-lock") == 1
