"""Reproduce the locked passive photo-library artifact from explicit source inputs."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess

RECIPE = Path(__file__).resolve().parent
NODE_SHA256 = "94efd062ed066c1a0b332e18249859c905e920eeada5df55d24c99b919629a90"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_recipe():
    manifest = json.loads((RECIPE / "artifact-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = RECIPE / name
        assert path.is_file() and not path.is_symlink()
        assert sha(path) == expected, f"Recipe input changed: {name}"


def source_files(snapshot, manifest):
    assert not snapshot.is_symlink()
    snapshot = snapshot.resolve(strict=True)
    assert len(manifest["files"]) == manifest["sourceFileCount"] == 11
    selected = {}
    for relative, expected in manifest["files"].items():
        name = Path(relative)
        assert not name.is_absolute() and ".." not in name.parts
        path = snapshot / name
        assert path.resolve(strict=True).is_relative_to(snapshot)
        for parent in [path, *path.parents]:
            assert not parent.is_symlink()
            if parent == snapshot:
                break
        info = path.stat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        assert sha(path) == expected, f"Passive source changed: {relative}"
        selected[relative] = path
    return selected


def copy_sources(selected, destination):
    for relative, source in selected.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)


def vendor_files(root):
    vendor = root / "node_modules"
    observed = {}
    for path in sorted(vendor.rglob("*")):
        relative = str(path.relative_to(root))
        info = path.lstat()
        if path.is_symlink():
            target = os.readlink(path)
            assert not os.path.isabs(target) and path.resolve().is_relative_to(root.resolve())
            observed[relative] = {"kind": "symlink", "target": target}
        elif path.is_dir():
            observed[relative] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
        else:
            assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            observed[relative] = {"kind": "file", "mode": stat.S_IMODE(info.st_mode),
                                  "sha256": sha(path)}
    observed["node_modules"] = {"kind": "directory", "mode": stat.S_IMODE(vendor.stat().st_mode)}
    return observed


def restore_recorded_npm_cache(artifact):
    recorded_path = RECIPE / "npm-installed-lock.json"
    recorded = json.loads(recorded_path.read_text())
    generated_path = artifact / "node_modules/.package-lock.json"
    generated = json.loads(generated_path.read_text())
    ci_variant = json.loads(json.dumps(recorded))
    ci_variant["packages"]["node_modules/better-sqlite3"]["hasInstallScript"] = True
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    assert canonical(generated) in {canonical(recorded), canonical(ci_variant)}, "Unknown npm cache metadata"
    shutil.copyfile(recorded_path, generated_path)


def run(arguments, *, cwd, environment):
    result = subprocess.run(arguments, cwd=cwd, env=environment, capture_output=True,
                            text=True, timeout=180)
    assert result.returncode == 0, "Public dependency reproduction command failed"
    return result.stdout.strip()


def prepare(snapshot, output, node, npm_cli):
    check_recipe()
    manifest = json.loads((RECIPE / "source-files.json").read_text())
    selected = source_files(snapshot, manifest)
    output = output.resolve()
    assert not output.exists()
    assert not output.is_relative_to(snapshot.resolve())
    assert sha(node) == NODE_SHA256
    output.mkdir(parents=True)
    artifact = output / "photo-library-candidate"
    artifact.mkdir()
    copy_sources(selected, artifact)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(RECIPE / name, artifact / name)
    user_config = output / "empty-user.npmrc"
    global_config = output / "empty-global.npmrc"
    user_config.write_text("")
    global_config.write_text("")
    environment = {
        "PATH": str(node.parent) + ":/usr/bin:/bin",
        "HOME": str(output),
        "NPM_CONFIG_USERCONFIG": str(user_config),
        "NPM_CONFIG_GLOBALCONFIG": str(global_config),
        "NPM_CONFIG_CACHE": str(output / "npm-cache"),
        "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org",
    }
    metadata = json.loads(run([str(node), "-p",
        "JSON.stringify({node:process.versions.node,platform:process.platform,arch:process.arch})"],
        cwd=artifact, environment=environment))
    assert metadata == {"node": "22.23.4", "platform": "linux", "arch": "x64"}
    assert run([str(node), str(npm_cli), "--version"], cwd=artifact,
               environment=environment) == "10.9.8"
    run([str(node), str(npm_cli), "ci", "--ignore-scripts", "--omit=dev"],
        cwd=artifact, environment=environment)
    restore_recorded_npm_cache(artifact)
    expected = json.loads((RECIPE / "vendor-files.json").read_text())["files"]
    assert vendor_files(artifact) == expected, "Vendor bytes, modes or link topology differ"
    signatures = json.loads(run([str(node), str(npm_cli), "audit", "signatures", "--json"],
                                cwd=artifact, environment=environment))
    assert signatures == {"invalid": [], "missing": []}
    assert all(sha(artifact / relative) == manifest["files"][relative] for relative in selected)
    result = {"contract": "EvaPassivePhotoLibraryReproduction/v1",
              "sourceFiles": 11, "vendorManifestExact": True,
              "npmSignaturesVerified": True, "node": metadata["node"],
              "dependencyLockSha256": sha(artifact / "package-lock.json"),
              "photoModelRequests": 0, "profileDataInputs": 0}
    (output / "reproduction.json").write_text(json.dumps(result, indent=2) + chr(10))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--npm-cli", type=Path, required=True)
    options = parser.parse_args()
    os.umask(0o022)
    print(json.dumps(prepare(options.source_snapshot, options.output,
                             options.node, options.npm_cli)))


if __name__ == "__main__":
    main()
