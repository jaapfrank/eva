"""Launch the existing wizard with the interpreter shipped in its package.

The package assembler supplies python-runtime from the pinned runtime image.
This launcher neither downloads dependencies nor adopts host user packages.
Package/release verification and target preparation remain in the existing
wizard; reaching that wizard is not installation or platform-support evidence.
"""

from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys


def installer_command(package_root, argv, inherited_environment):
    """Build one exact local launch; never fall back to system Python."""
    root = Path(package_root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("canonical absolute installer package root required")
    runtime = root / "python-runtime"
    interpreter = runtime / "bin/python3.12"
    if (not interpreter.is_file() or not interpreter.resolve().is_relative_to(runtime)
            or not (root / "tools/installer_restore_wizard.py").is_file()
            or not (root / "src/eva/bootstrap/installer_wizard_cli.py").is_file()):
        raise ValueError("complete packaged interpreter and wizard required")
    # Assembly selects the first target; owners need no handwritten CLI path.
    # This checksum binds local package content, not public release authority.
    try:
        metadata = json.loads((root / "package-staging.json").read_bytes())
        selected = Path(metadata["platform_lock"])
        selected_path = root / selected
        if (selected.is_absolute() or ".." in selected.parts
                or selected.parts[:1] != ("release",)
                or selected_path.resolve() != selected_path
                or hashlib.sha256(selected_path.read_bytes()).hexdigest()
                != metadata["source_files"][str(selected)]):
            raise ValueError("packaged platform selection differs")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("verified packaged platform selection required") from error
    arguments = list(argv)
    if not any(arg == "--platform-lock" or arg.startswith("--platform-lock=")
               for arg in arguments):
        arguments.extend(("--platform-lock", str(selected_path)))
    environment = dict(inherited_environment)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP"):
        environment.pop(key, None)
    environment.update({
        "PYTHONHOME": str(runtime),
        "LD_LIBRARY_PATH": str(runtime / "lib"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
    })
    return [str(interpreter), "-m", "tools.installer_restore_wizard", *arguments], environment


def main(*, package_root, argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    command, environment = installer_command(package_root, arguments, os.environ)
    # Check the bundled runtime before opening the existing local UI.
    check = subprocess.run([command[0], "-c",
        "import platform; assert platform.python_version() == '3.12.14'"],
        env=environment, cwd=package_root, capture_output=True, check=False)
    if check.returncode != 0:
        raise RuntimeError("packaged Python3.12.14 could not start; no fallback attempted")
    os.chdir(package_root)
    os.execvpe(command[0], command, environment)
