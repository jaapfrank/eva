from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile

import pytest


MODULE_PATH = Path(__file__).with_name("build_candidate.py")
SPEC = importlib.util.spec_from_file_location("filesystem_mcp_build_candidate", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def linux_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="eva2-filesystem-mcp-test-", dir="/var/tmp"
    ) as raw:
        yield Path(raw)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _artifact_root(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "artifacts"
    npm = root / "npm-cache"
    rpm = root / "rpm"
    npm.mkdir(parents=True, mode=0o700)
    rpm.mkdir(mode=0o700)
    package = npm / "package-byte"
    rpm_file = rpm / "node.rpm"
    package.write_bytes(b"package")
    rpm_file.write_bytes(b"rpm")
    package.chmod(0o400)
    rpm_file.chmod(0o400)
    rows = [
        f"{_sha256(package.read_bytes())}  npm-cache/package-byte",
        f"{_sha256(rpm_file.read_bytes())}  rpm/node.rpm",
    ]
    manifest = root / "manifest.sha256"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    manifest.chmod(0o400)
    authority: dict[str, object] = {
        "privateArtifactManifest": {
            "fileCount": 2,
            "sha256": MODULE._sha256(manifest),
        },
        "rpmPackages": ["node.rpm"],
    }
    return root, authority


def test_artifact_root_accepts_exact_closed_file_set(linux_tmp_path: Path) -> None:
    root, authority = _artifact_root(linux_tmp_path)

    observed = MODULE._validate_artifacts(root, authority)

    assert observed == authority["privateArtifactManifest"]["sha256"]


@pytest.mark.parametrize("mutation", ["bytes", "extra", "mode", "symlink"])
def test_artifact_root_rejects_unsealed_changes(
    linux_tmp_path: Path, mutation: str
) -> None:
    root, authority = _artifact_root(linux_tmp_path)
    if mutation == "bytes":
        target = root / "npm-cache" / "package-byte"
        target.chmod(0o600)
        target.write_bytes(b"changed")
        target.chmod(0o400)
    elif mutation == "extra":
        target = root / "rpm" / "extra.rpm"
        target.write_bytes(b"extra")
        target.chmod(0o400)
    elif mutation == "mode":
        (root / "rpm" / "node.rpm").chmod(0o600)
    else:
        target = root / "npm-cache" / "package-byte"
        target.chmod(0o600)
        target.unlink()
        target.symlink_to(root / "rpm" / "node.rpm")

    with pytest.raises(RuntimeError):
        MODULE._validate_artifacts(root, authority)
