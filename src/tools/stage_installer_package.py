"""Stage the existing installer and its exact interpreter from a local image.

This is package assembly, not target provisioning or release validation. It
does not pull/build an image, execute the image, import credentials, or change
an existing package. Failed output is retained for inspection.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
from uuid import uuid4


_SOURCE_DIRECTORIES = ("src", "deploy", "development/validation")
_SOURCE_FILES = ("start-installer.py", "pyproject.toml", "deploy/requirements.lock")
_EXCLUDED_PARTS = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
                   ".mypy_cache", ".playwright-mcp", "host-dependencies"}


def stage_package(*, source_root, target_root, runtime_image,
                  platform_lock="deploy/release/platform-lock-wsl-candidate.json", development_images=None,
                  browser_native_wheel=None):
    """Create one new package from trusted source and an already local image."""
    source, target = Path(source_root), Path(target_root)
    for path in (source, target):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("canonical absolute package paths required")
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("package and source trees must not overlap")
    if target.exists() or not source.is_dir():
        raise ValueError("existing source and new package directory required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_image):
        raise ValueError("immutable local runtime image required")
    native_wheel = None
    if browser_native_wheel is not None:
        from tools.installer_browser_module import native_browser_wheel
        _, native_wheel = native_browser_wheel(source, browser_native_wheel)
    def docker(*arguments):
        return subprocess.check_output(["docker", *arguments], text=True,
                                       stderr=subprocess.PIPE).strip()
    actual = json.loads(docker("image", "inspect", runtime_image))
    if (len(actual) != 1 or actual[0]["Id"] != runtime_image
            or actual[0].get("Architecture") != "amd64"
            or actual[0].get("Os") != "linux"):
        raise ValueError("exact local Linux amd64 runtime image required")
    # Package only Git-controlled project inputs. Installed dependencies,
    # experiment caches and private untracked files must never enter a bundle.
    repository = Path(subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"], text=True).strip())
    prefix = source.relative_to(repository)
    selected = [str(prefix / name) for name in (*_SOURCE_FILES, *_SOURCE_DIRECTORIES)]
    tracked = subprocess.check_output(
        ["git", "-C", str(repository), "ls-files", "-z", "--", *selected]).decode().split("\0")
    source_commit = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "-C", str(repository), "status", "--porcelain", "--", *selected]))
    if development_images is not None:
        if (not {"living-core", "postgres", "hindsight"} <= set(development_images)
                or set(development_images) - {"living-core", "postgres", "hindsight", "browser", "browser-mcp", "mcp-gateway", "voice-media", "livekit"}
                or development_images["living-core"] != runtime_image):
            raise ValueError("complete explicit development image inventory required")
        if ("voice-media" in development_images) != ("livekit" in development_images):
            raise ValueError("both optional voice images are required")
        for digest in development_images.values():
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("immutable local development image required")
            inspected = json.loads(docker("image", "inspect", digest))
            if (len(inspected) != 1 or inspected[0]["Id"] != digest
                    or inspected[0].get("Os") != "linux" or inspected[0].get("Architecture") != "amd64"):
                raise ValueError("local development image differs")
    # Inspect all intended source entries before creating the output tree.
    files = []
    for name in tracked:
        if not name:
            continue
        path = repository / name
        if (_EXCLUDED_PARTS.intersection(path.relative_to(source).parts)
                or path.suffix in {".pyc", ".pyo"}):
            continue
        metadata = path.lstat()
        if (not path.is_relative_to(source) or path.resolve() != path
                or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise ValueError("installer source contains a linked or unsupported entry")
        files.append(path)
    relative_files = {str(path.relative_to(source)) for path in files}
    if not {*_SOURCE_FILES, "src/tools/launch_packaged_installer.py",
            "src/tools/installer_restore_wizard.py",
            "src/eva/bootstrap/installer_wizard_cli.py"} <= relative_files:
        raise ValueError("required installer source must be Git-controlled")
    selected_lock = Path(platform_lock)
    if (selected_lock.is_absolute() or ".." in selected_lock.parts
            or selected_lock.parts[:2] != ("deploy", "release")
            or str(selected_lock) not in relative_files):
        raise ValueError("Git-controlled packaged platform lock required")
    target.mkdir(mode=0o700)
    receipt = {"contract": "EvaInstallerPackageStaging/v1", "status": "staging",
               "runtime_image": runtime_image, "source_files": {},
               "platform_lock": str(selected_lock),
               "base_commit": source_commit, "source_has_changes": dirty,
               "runtime_executed": False, "target_prepared": False,
               "platform_support_claim": False}
    if development_images is not None:
        receipt["development_candidate"] = {"contract": "EvaLocalInstallerCandidate/v1",
            "imageDigests": dict(development_images), "sourceCommit": source_commit,
            "public_release": False}
    def save():
        output = target / "package-staging.json"
        output.write_text(json.dumps(receipt, indent=2) + "\n")
        output.chmod(0o600)
    container = None
    export_verified = False
    export_nonce = uuid4().hex
    save()
    try:
        for path in sorted(files):
            relative = path.relative_to(source)
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
            receipt["source_files"][str(relative)] = hashlib.sha256(output.read_bytes()).hexdigest()
        if native_wheel is not None:
            relative = Path("deploy/owner-browser") / native_wheel.name
            output = target / relative
            shutil.copyfile(native_wheel, output)
            _, verified = native_browser_wheel(target, output)
            receipt["source_files"][str(relative)] = hashlib.sha256(verified.read_bytes()).hexdigest()
        save()
        container = docker("create", "--network", "none", "--label",
                           "io.eva2.installer-export=" + export_nonce,
                           "--entrypoint", "/bin/true", runtime_image)
        receipt["export_container"] = container
        save()
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise ValueError("export container identity is invalid")
        inspection = json.loads(docker("inspect", container))
        if (len(inspection) != 1 or inspection[0]["Id"] != container
                or inspection[0]["Image"] != runtime_image
                or inspection[0].get("Config", {}).get("Labels", {}).get(
                    "io.eva2.installer-export") != export_nonce
                or inspection[0]["State"]["Status"] != "created"):
            raise ValueError("owned unstarted export container differs")
        export_verified = True
        docker("cp", container + ":/usr/local", str(target / "python-runtime"))
        if not (target / "python-runtime/bin/python3.12").is_file():
            raise ValueError("runtime image lacks the packaged Python interpreter")
        receipt["status"] = "staged_not_validated"
        save()
        return receipt
    except Exception as error:
        receipt.update(status="needs_inspection", failure_type=type(error).__name__)
        save()
        raise
    finally:
        # Only this invocation's exact stopped export container is removed.
        # Package output and existing Eva services/data are never removed.
        if export_verified:
            docker("rm", container)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--platform-lock", default="deploy/release/platform-lock-wsl-candidate.json")
    parser.add_argument("--browser-native-wheel", type=Path)
    parser.add_argument("--postgres-image")
    parser.add_argument("--hindsight-image")
    parser.add_argument("--mcp-gateway-image")
    parser.add_argument("--voice-media-image")
    parser.add_argument("--livekit-image")
    args = parser.parse_args(argv)
    if bool(args.postgres_image) != bool(args.hindsight_image):
        parser.error("both local development dependency images are required")
    if bool(args.voice_media_image) != bool(args.livekit_image):
        parser.error("both optional voice images are required")
    if (args.voice_media_image or args.mcp_gateway_image) and not args.postgres_image:
        parser.error("optional voice images require the explicit development image inventory")
    images = ({"living-core": args.runtime_image, "postgres": args.postgres_image,
               "hindsight": args.hindsight_image} if args.postgres_image else None)
    if args.mcp_gateway_image:
        images["mcp-gateway"]=args.mcp_gateway_image
    if args.voice_media_image:
        images.update({"voice-media": args.voice_media_image, "livekit": args.livekit_image})
    stage_package(source_root=args.source_root, target_root=args.target_root,
                  runtime_image=args.runtime_image, platform_lock=args.platform_lock,
                  development_images=images, browser_native_wheel=args.browser_native_wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
